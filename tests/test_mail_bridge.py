"""mail/bridge.py: requests and events over the host's connection, files in pieces, and a link that misbehaves.

The add-on's side is played by `Host`, which reads the requests the service writes and answers as the test says.
Nothing here starts a process or uses the network: the two ends are a socket pair.
"""

import asyncio
import base64
import json
import os
import socket

import pytest
import pytest_asyncio

from bombadil import paths
from bombadil.mail import bridge, protocol
from bombadil.mail.bridge import EngineError, EngineGone, EngineLink, EngineTimeout

pytestmark = pytest.mark.asyncio


class Host:
    """The other end of the link: what the native-messaging host would relay from the add-on."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.requests: list[dict] = []

    async def request(self, timeout=2.0) -> dict:
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        assert line, "the service closed the link"
        req = json.loads(line)
        self.requests.append(req)
        return req

    def say(self, frame: dict) -> None:
        self.writer.write((json.dumps(frame) + "\n").encode())

    def raw(self, data: bytes) -> None:
        self.writer.write(data)

    def answer(self, req: dict, result=None) -> None:
        self.say({"id": req["id"], "ok": True, "result": result})

    def fail(self, req: dict, code: str, sentence: str) -> None:
        self.say({"id": req["id"], "ok": False, "error": sentence, "code": code})

    def hang_up(self) -> None:
        self.writer.close()


async def pair(limit=1 << 20):
    a, b = socket.socketpair()
    reader, writer = await asyncio.open_unix_connection(sock=a, limit=limit)
    host = Host(*await asyncio.open_unix_connection(sock=b, limit=1 << 26))
    return reader, writer, host


@pytest.fixture
def files(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_FILES", str(home / "mailfiles"))
    return paths.mail_files()


@pytest_asyncio.fixture
async def link(files):
    link = EngineLink()
    link.events, link.states = [], []
    link.on_event = link.events.append
    link.on_state = link.states.append
    reader, writer, host = await pair()
    link.host = host
    link.task = link.attach(reader, writer)
    yield link
    await link.close()
    host.hang_up()


async def settle(seconds=0.1):
    """Let the loop and its helper threads (files are written off the loop) get through what was said."""
    await asyncio.sleep(seconds)


# -- requests --

async def test_a_request_is_written_with_its_arguments_and_gets_its_answer(link):
    task = asyncio.create_task(link.request("list", 2.0, account="e1", folder="inbox", limit=5))
    req = await link.host.request()
    assert {k: v for k, v in req.items() if k != "id"} == {"op": "list", "account": "e1", "folder": "inbox", "limit": 5}
    link.host.answer(req, {"messages": [], "more": False})
    assert await task == {"messages": [], "more": False}
    assert link._conn.pending == {}


async def test_requests_in_flight_together_are_each_answered_by_id_in_any_order(link):
    tasks = [asyncio.create_task(link.request("get", 2.0, key=f"k{i}")) for i in range(3)]
    reqs = [await link.host.request() for _ in range(3)]
    assert len({r["id"] for r in reqs}) == 3
    for req in reversed(reqs):
        link.host.answer(req, req["key"])
    assert sorted(await asyncio.gather(*tasks)) == ["k0", "k1", "k2"]


async def test_no_link_is_engine_gone_and_the_request_never_left(files):
    link = EngineLink()
    assert link.connected is False
    with pytest.raises(EngineGone) as e:
        await link.request("info")
    assert e.value.sent is False


async def test_an_error_answer_is_an_engine_error_with_its_code_and_a_sentence(link):
    task = asyncio.create_task(link.request("get", 2.0, key="k"))
    req = await link.host.request()
    link.host.fail(req, "not_found", "That  message\nis gone. " + "x" * 400)
    with pytest.raises(EngineError) as e:
        await task
    assert e.value.code == "not_found" and "\n" not in str(e.value) and len(str(e.value)) <= 300
    assert str(e.value).startswith("That message is gone.")


async def test_an_error_with_no_words_still_says_something(link):
    task = asyncio.create_task(link.request("get", 2.0))
    req = await link.host.request()
    link.host.say({"id": req["id"], "ok": False})
    with pytest.raises(EngineError) as e:
        await task
    assert e.value.code == protocol.ENGINE_ERROR and str(e.value)


async def test_a_request_not_answered_in_time_is_a_timeout_that_was_sent_and_leaves_nothing_waiting(link):
    with pytest.raises(EngineTimeout) as e:
        await link.request("send", 0.05)
    assert e.value.sent is True
    req = link.host.requests[0] if link.host.requests else await link.host.request()
    assert link._conn.pending == {}
    link.host.answer(req, "late")   # an answer that comes after is ignored, and the link is fine
    task = asyncio.create_task(link.request("info", 2.0))
    req = await link.host.request()
    link.host.answer(req, "now")
    assert await task == "now"


async def test_a_link_lost_while_a_request_waits_fails_it_as_possibly_acted_on_and_says_the_state_changed(link):
    task = asyncio.create_task(link.request("send", 5.0))
    await link.host.request()
    link.host.hang_up()
    with pytest.raises(EngineGone) as e:
        await task
    assert e.value.sent is True
    await link.task
    assert link.connected is False and link.states == [True, False]


async def test_a_second_host_replaces_the_first_and_what_waited_on_the_first_fails(link):
    old_host = link.host
    task = asyncio.create_task(link.request("send", 5.0))
    await old_host.request()
    reader, writer, new_host = await pair()
    link.attach(reader, writer)
    with pytest.raises(EngineGone) as e:
        await task
    assert e.value.sent is True and "replaced" in str(e.value)
    assert link.states == [True, True]   # no "down" in between: the link never stopped being there
    assert await asyncio.wait_for(old_host.reader.readline(), 2.0) == b""   # the old one was closed
    task = asyncio.create_task(link.request("info", 2.0))
    req = await new_host.request()
    new_host.answer(req, 1)
    assert await task == 1
    new_host.hang_up()


async def test_drop_hangs_up_on_the_host_and_fails_what_waits_on_it(link):
    task = asyncio.create_task(link.request("send", 5.0))
    await link.host.request()
    link.drop("Thunderbird stopped answering.")
    with pytest.raises(EngineGone) as e:
        await task
    assert e.value.sent is True and "stopped answering" in str(e.value)
    assert link.connected is False
    assert await asyncio.wait_for(link.host.reader.readline(), 2.0) == b""
    await link.task
    assert link.states == [True, False]
    link.drop()   # with no link there is nothing to do


async def test_a_host_replaced_during_a_fetch_fails_the_fetch_at_once_and_not_after_the_idle_wait(link, files):
    waiter = asyncio.create_task(link.receive_blob("t20", 1 << 20, 30.0))
    await settle()
    link.host.say(blob("t20", 0, b"abc"))
    await settle()
    reader, writer, new_host = await pair()
    link.attach(reader, writer)
    with pytest.raises(EngineGone):
        await asyncio.wait_for(waiter, 2.0)   # it used to wait out BLOB_IDLE_S
    assert link._incoming == {} and list((files / "inflight").glob("*")) == []
    new_host.hang_up()


async def test_what_a_replaced_host_says_after_that_is_not_believed(link):
    old = link._conn
    reader, writer, new_host = await pair()
    link.attach(reader, writer)
    await link._event(old, {"event": "hello", "version": 99})
    await link._event(old, {"event": "new_mail", "account": "e1", "messages": []})
    await link._event(old, blob("t21", 0, b"abc"))
    assert link.hello is None and link.events == [] and link._incoming == {}
    new_host.say({"event": "hello", "version": 1})
    await settle()
    assert link.hello == {"event": "hello", "version": 1}
    new_host.hang_up()


async def test_too_many_requests_at_once_is_said_not_queued_forever(link, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_PENDING", 3)
    tasks = [asyncio.create_task(link.request("x", 5.0)) for _ in range(3)]
    for _ in range(3):
        await link.host.request()
    with pytest.raises(EngineError, match="busy"):
        await link.request("x", 5.0)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert link._conn.pending == {}


async def test_a_host_that_stops_reading_is_a_link_that_is_gone_not_a_service_that_is_stuck(files, monkeypatch):
    monkeypatch.setattr(bridge, "WRITE_CAP", 1 << 20)
    a, b = socket.socketpair()   # b is never read from, and no event loop reads it for us
    b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    reader, writer = await asyncio.open_unix_connection(sock=a)
    link = EngineLink()
    states = []
    link.on_state = states.append
    link.attach(reader, writer)
    body = "x" * 900_000
    gone = None
    for _ in range(40):
        try:
            await link.request("send", 0.01, body=body)
        except EngineTimeout:
            continue
        except EngineGone as e:
            gone = e
            break
    assert gone is not None and "stopped reading" in str(gone)
    assert link.connected is False and states[-1] is False
    with pytest.raises(EngineGone):
        await link.request("info")
    b.close()


async def test_a_frame_that_one_frame_cannot_carry_is_refused_before_it_is_written(link):
    with pytest.raises(EngineError) as e:
        await link.request("send", 1.0, body="x" * (bridge.FRAME_MAX + 1))
    assert e.value.code == protocol.TOO_BIG
    assert link._conn.pending == {}
    await settle()
    assert link.host.requests == []


async def test_non_ascii_goes_out_as_itself_so_the_size_limit_counts_bytes_not_escapes(link):
    body = "é" * 400_000    # 800 KB as UTF-8, 2.4 MB as \\u escapes
    task = asyncio.create_task(link.request("send", 5.0, body=body))
    req = await link.host.request(5.0)
    assert req["body"] == body
    link.host.answer(req, {})
    await task


async def test_what_cannot_be_encoded_is_refused_and_leaves_nothing_waiting(link):
    with pytest.raises(EngineError) as e:
        await link.request("send", 1.0, body="a\ud800b")
    assert e.value.code == protocol.BAD_REQUEST
    with pytest.raises(EngineError):
        await link.request("send", 1.0, body=object())
    assert link._conn.pending == {}


# -- events --

async def test_events_arrive_in_order_and_hello_is_kept(link):
    link.host.say({"event": "hello", "version": 1, "app": "Thunderbird", "caps": ["send"]})
    link.host.say({"event": "new_mail", "account": "e1", "messages": []})
    link.host.say({"event": "counts_changed"})
    await settle()
    assert [e["event"] for e in link.events] == ["hello", "new_mail", "counts_changed"]
    assert link.hello["app"] == "Thunderbird"


async def test_a_handler_that_raises_does_not_break_the_link(link, capsys):
    def boom(event):
        raise RuntimeError("no")
    link.on_event = boom
    link.host.say({"event": "counts_changed"})
    await settle()
    task = asyncio.create_task(link.request("info", 2.0))
    req = await link.host.request()
    link.host.answer(req, 1)
    assert await task == 1
    assert "RuntimeError" in capsys.readouterr().err


async def test_a_state_handler_that_raises_does_not_break_attach(files, capsys):
    link = EngineLink()
    link.on_state = lambda up: 1 / 0
    reader, writer, host = await pair()
    link.attach(reader, writer)
    assert link.connected
    await link.close()
    host.hang_up()


async def test_frames_that_are_not_frames_are_ignored(link):
    link.host.raw(b"not json\n[1,2]\n5\n\n{}\n" + b'{"id": true, "ok": true}\n{"id": 99, "ok": true}\n'
                  b'{"id": "1", "ok": true}\n{"event": "counts_changed"}\n')
    await settle()
    assert [e["event"] for e in link.events] == ["counts_changed"]
    task = asyncio.create_task(link.request("info", 2.0))
    req = await link.host.request()
    link.host.answer(req, "fine")
    assert await task == "fine"


async def test_an_answer_with_the_id_of_a_request_nobody_made_answers_nothing(link):
    task = asyncio.create_task(link.request("info", 0.2))
    req = await link.host.request()
    link.host.say({"id": req["id"] + 1, "ok": True, "result": "wrong"})
    with pytest.raises(EngineTimeout):
        await task


async def test_a_frame_bigger_than_a_socket_line_is_fine_because_a_mail_arrives_in_one(link):
    big = "y" * (3 << 20)
    task = asyncio.create_task(link.request("get", 10.0))
    req = await link.host.request()
    link.host.answer(req, {"html": big})
    got = await task
    assert len(got["html"]) == 3 << 20


async def test_a_frame_over_the_native_messaging_limit_ends_the_link(link, monkeypatch, capsys):
    monkeypatch.setattr(protocol, "NM_MAX_READ", 1 << 20)
    task = asyncio.create_task(link.request("get", 10.0))
    req = await link.host.request()
    link.host.answer(req, {"html": "y" * (3 << 20)})
    with pytest.raises(EngineGone):
        await task
    assert link.connected is False and "limit" in capsys.readouterr().err


async def test_a_last_line_with_no_newline_is_not_a_frame(link):
    link.host.raw(b'{"event": "counts_changed"}')
    link.host.hang_up()
    await link.task
    assert link.events == []


async def test_close_fails_what_waits_and_ends_the_link(link):
    task = asyncio.create_task(link.request("send", 5.0))
    await link.host.request()
    await link.close()
    with pytest.raises(EngineGone):
        await task
    assert link.connected is False


# -- files out --

async def serve_blobs(host):
    """Answer each blob request as it comes, and check that it came alone: nothing more is written until
    this one is answered."""
    got = []
    while True:
        req = await host.request()
        assert req["op"] == "blob" and req["seq"] == len(got)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(host.reader.readline(), 0.03)   # a second request in flight would be here
        got.append(base64.b64decode(req["data"], validate=True))
        host.answer(req, {})
        if req["last"]:
            return got, req


async def test_a_file_goes_in_chunks_one_at_a_time_and_the_last_says_so(link):
    data = os.urandom(bridge.CHUNK * 2 + 5)
    sender = asyncio.create_task(link.send_blob("x1", data))
    got, last = await serve_blobs(link.host)
    await sender
    assert [len(c) for c in got] == [bridge.CHUNK, bridge.CHUNK, 5] and b"".join(got) == data
    assert last["xfer"] == "x1" and last["seq"] == 2 and last["last"] is True
    assert all(r["last"] is False for r in link.host.requests[:-1])


async def test_a_file_of_exactly_one_chunk_is_one_request_and_an_empty_file_is_one_empty_request(link):
    for n in (bridge.CHUNK, 0, 1):
        link.host.requests.clear()
        sender = asyncio.create_task(link.send_blob("x", b"z" * n))
        got, last = await serve_blobs(link.host)
        await sender
        assert len(got) == 1 and len(got[0]) == n and last["seq"] == 0 and last["last"] is True


async def test_a_file_on_disk_goes_the_same_way(link, files):
    path = files.parent / "src.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = os.urandom(bridge.CHUNK + 10)
    path.write_bytes(data)
    sender = asyncio.create_task(link.send_blob("x2", path))
    got, _ = await serve_blobs(link.host)
    await sender
    assert b"".join(got) == data


async def test_an_empty_file_on_disk_is_one_empty_chunk(link, files):
    path = files.parent / "empty.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    sender = asyncio.create_task(link.send_blob("x3", path))
    got, last = await serve_blobs(link.host)
    await sender
    assert got == [b""] and last["last"] is True


async def test_a_chunk_the_addon_refuses_ends_the_transfer_with_its_reason(link):
    sender = asyncio.create_task(link.send_blob("x4", b"a" * (bridge.CHUNK + 1)))
    req = await link.host.request()
    link.host.fail(req, "engine_error", "No room.")
    with pytest.raises(EngineError, match="No room"):
        await sender
    await settle()
    assert len(link.host.requests) == 1   # and no more chunks were sent


async def test_a_chunk_that_is_not_answered_in_time_is_a_timeout(link):
    with pytest.raises(EngineTimeout):
        await link.send_blob("x5", b"abc", timeout=0.05)


# -- files in --

def blob(xfer, seq, data: bytes, last=False):
    return {"event": "blob", "xfer": xfer, "seq": seq, "data": base64.b64encode(data).decode(), "last": last}


async def test_a_file_arrives_in_pieces_and_is_handed_over_on_disk(link, files):
    data = os.urandom(bridge.CHUNK + 1000)
    waiter = asyncio.create_task(link.receive_blob("t1", 10 << 20, 5.0))
    await settle()
    link.host.say(blob("t1", 0, data[:bridge.CHUNK]))
    link.host.say(blob("t1", 1, data[bridge.CHUNK:], last=True))
    path = await waiter
    assert path.read_bytes() == data
    assert path.parent == files / "inflight" and path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert link._incoming == {}
    # the events that were blobs did not go to the service's event handler
    assert link.events == []


async def test_pieces_that_come_before_anyone_asks_are_kept_for_whoever_does(link):
    link.host.say(blob("t2", 0, b"abc"))
    link.host.say(blob("t2", 1, b"def", last=True))
    await settle()
    assert (await link.receive_blob("t2", 1 << 20, 2.0)).read_bytes() == b"abcdef"


async def test_an_empty_file_arrives(link):
    link.host.say(blob("t3", 0, b"", last=True))
    await settle()
    assert (await link.receive_blob("t3", 1 << 20, 2.0)).read_bytes() == b""


async def test_the_name_on_disk_is_made_from_a_hash_because_the_addon_chose_the_transfers_name(link, files):
    names = ["../../etc/cron.d/x", "a/b", "..", "x" * 128, "été", "with space"]
    for n in names:
        link.host.say(blob(n, 0, b"data", last=True))
    await settle()
    paths_ = [await link.receive_blob(n, 1 << 20, 2.0) for n in names]
    for p in paths_:
        assert p.parent == files / "inflight" and p.name.endswith(".part") and len(p.stem) == 32
        assert p.read_bytes() == b"data"
    assert len({p.name for p in paths_}) == len(names)
    assert not (files.parent / "etc").exists()


@pytest.mark.parametrize("xfer", ["", "x" * 129, 5, None, ["a"]])
async def test_a_transfer_with_a_name_that_cannot_be_used_is_refused_or_ignored(link, xfer):
    link.host.say({"event": "blob", "xfer": xfer, "seq": 0, "data": "", "last": True})
    await settle()
    assert link._incoming == {}
    with pytest.raises(EngineError):
        await link.receive_blob(xfer, 1 << 20, 0.1)


async def test_pieces_out_of_order_end_the_transfer_and_take_the_partial_file_with_them(link, files):
    waiter = asyncio.create_task(link.receive_blob("t4", 1 << 20, 5.0))
    await settle()
    link.host.say(blob("t4", 0, b"abc"))
    link.host.say(blob("t4", 2, b"ghi", last=True))
    with pytest.raises(EngineError, match="order"):
        await waiter
    assert list((files / "inflight").glob("*")) == []


@pytest.mark.parametrize("piece", [{"data": "!!!not base64!!!"}, {"data": 5}, {"data": None}, {}])
async def test_a_piece_that_is_not_base64_ends_the_transfer(link, files, piece):
    waiter = asyncio.create_task(link.receive_blob("t5", 1 << 20, 5.0))
    await settle()
    link.host.say(blob("t5", 0, b"abc"))
    link.host.say({"event": "blob", "xfer": "t5", "seq": 1, "last": True, **piece})
    with pytest.raises(EngineError):
        await waiter
    assert list((files / "inflight").glob("*")) == []


async def test_a_piece_bigger_than_a_chunk_is_refused(link):
    waiter = asyncio.create_task(link.receive_blob("t6", 10 << 20, 5.0))
    await settle()
    link.host.say(blob("t6", 0, b"x" * (bridge.CHUNK + 1)))
    with pytest.raises(EngineError) as e:
        await waiter
    assert e.value.code == protocol.TOO_BIG


async def test_a_file_over_the_size_the_caller_allows_is_refused_and_removed(link, files):
    waiter = asyncio.create_task(link.receive_blob("t7", 1000, 5.0))
    await settle()
    link.host.say(blob("t7", 0, b"x" * 600))
    link.host.say(blob("t7", 1, b"x" * 600, last=True))
    with pytest.raises(EngineError) as e:
        await waiter
    assert e.value.code == protocol.TOO_BIG
    assert list((files / "inflight").glob("*")) == []


async def test_a_file_that_is_already_over_the_limit_when_asked_for_is_refused_at_once(link, files):
    link.host.say(blob("t8", 0, b"x" * 600))
    await settle()
    with pytest.raises(EngineError) as e:
        await link.receive_blob("t8", 100, 5.0)
    assert e.value.code == protocol.TOO_BIG and list((files / "inflight").glob("*")) == []


async def test_nobody_asking_caps_what_is_kept_for_them(link, files, monkeypatch):
    monkeypatch.setattr(bridge, "UNCLAIMED_MAX", 1000)
    link.host.say(blob("t9", 0, b"x" * 600))
    link.host.say(blob("t9", 1, b"x" * 600))
    await settle()
    assert "t9" not in link._incoming and list(files.glob("inflight/*")) == []


async def test_pieces_nobody_asked_for_are_dropped_after_a_while(link, files, monkeypatch):
    monkeypatch.setattr(bridge, "UNCLAIMED_S", 0.0)
    link.host.say(blob("old", 0, b"abc"))
    await settle()
    assert "old" in link._incoming
    await asyncio.sleep(0.01)
    link.host.say(blob("new", 0, b"abc"))
    await settle()
    assert "old" not in link._incoming
    assert len(list((files / "inflight").glob("*"))) == 1


async def test_only_so_many_files_arrive_at_once(link, monkeypatch, capsys):
    monkeypatch.setattr(bridge, "MAX_XFERS", 2)
    for n in ("a", "b", "c"):
        link.host.say(blob(n, 0, b"abc"))
    await settle()
    assert sorted(link._incoming) == ["a", "b"]
    with pytest.raises(EngineError):
        await link.receive_blob("c", 1000, 0.1)


async def test_a_file_that_goes_quiet_is_given_up_on_and_its_partial_is_removed(link, files, monkeypatch):
    monkeypatch.setattr(bridge, "BLOB_IDLE_S", 0.1)
    waiter = asyncio.create_task(link.receive_blob("t10", 1 << 20, 30.0))
    await settle()
    link.host.say(blob("t10", 0, b"abc"))
    with pytest.raises(EngineTimeout):
        await waiter
    assert list((files / "inflight").glob("*")) == [] and link._incoming == {}


async def test_a_file_that_takes_too_long_altogether_is_given_up_on_though_it_keeps_coming(link, files):
    waiter = asyncio.create_task(link.receive_blob("t11", 1 << 20, 0.3))
    await settle()

    async def drip():
        for i in range(100):
            link.host.say(blob("t11", i, b"x"))
            await asyncio.sleep(0.05)
    feeder = asyncio.create_task(drip())
    with pytest.raises(EngineTimeout):
        await waiter
    feeder.cancel()
    assert list((files / "inflight").glob("*")) == []


async def test_a_link_lost_during_a_fetch_fails_the_fetch_and_removes_the_partial(link, files):
    waiter = asyncio.create_task(link.receive_blob("t12", 1 << 20, 30.0))
    await settle()
    link.host.say(blob("t12", 0, b"abc"))
    await settle()
    link.host.hang_up()
    with pytest.raises(EngineGone):
        await waiter
    assert list((files / "inflight").glob("*")) == []


async def test_a_fetch_that_is_cancelled_cleans_up_after_itself(link, files):
    waiter = asyncio.create_task(link.receive_blob("t13", 1 << 20, 30.0))
    await settle()
    link.host.say(blob("t13", 0, b"abc"))
    await settle()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert list((files / "inflight").glob("*")) == [] and link._incoming == {}


async def test_closing_the_link_discards_what_was_arriving(link, files):
    link.host.say(blob("t14", 0, b"abc"))
    await settle()
    await link.close()
    assert link._incoming == {} and list((files / "inflight").glob("*")) == []


async def test_more_pieces_after_the_end_or_after_an_error_change_nothing(link):
    link.host.say(blob("t15", 0, b"abc", last=True))
    link.host.say(blob("t15", 1, b"def", last=True))
    await settle()
    assert (await link.receive_blob("t15", 1 << 20, 2.0)).read_bytes() == b"abc"


async def test_a_whole_file_over_what_the_caller_allows_is_an_error_and_not_a_path_to_nothing(link, files):
    link.host.say(blob("t23", 0, b"x" * 100, last=True))
    await settle()
    with pytest.raises(EngineError) as e:
        await link.receive_blob("t23", 10, 2.0)   # it had arrived whole before it was asked for
    assert e.value.code == protocol.TOO_BIG
    assert link._incoming == {} and list((files / "inflight").glob("*")) == []


async def test_a_fetch_with_no_link_is_gone_at_once(files):
    with pytest.raises(EngineGone):
        await EngineLink().receive_blob("t24", 1 << 20, 30.0)


async def test_fetching_a_file_nobody_is_sending_times_out_and_leaves_nothing(link):
    with pytest.raises(EngineTimeout):
        await link.receive_blob("nothing", 1 << 20, 0.1)
    assert link._incoming == {}


# -- serve_engine --

async def test_serve_engine_is_the_link_until_the_connection_ends(files):
    link = EngineLink()
    reader, writer, host = await pair()
    served = asyncio.create_task(bridge.serve_engine(reader, writer, link))
    await settle()
    assert link.connected
    host.hang_up()
    await asyncio.wait_for(served, 2.0)
    assert link.connected is False


async def test_serve_engine_cancelled_stops_reading(files):
    link = EngineLink()
    reader, writer, host = await pair()
    served = asyncio.create_task(bridge.serve_engine(reader, writer, link))
    await settle()
    task = link._conn.task
    served.cancel()
    with pytest.raises(asyncio.CancelledError):
        await served
    await settle()
    assert task.cancelled() or task.done()
    await link.close()
    host.hang_up()

"""connect/client.py: the blocking client, against the real service.

The service runs in a thread of its own with its own event loop (`Running`), on the fake engine in a temp dir, so
the tests call it from the plain threads the client is made for. A call that has to hang uses a driver whose press
never finishes. The fixed text of `ConnectUnavailable` and the code of `ConnectError` are what agentd, the launcher
and the CLI build their sentences on.
"""

import asyncio
import json
import socket
import threading
import time

import pytest

from bombadil.connect import client
from bombadil.connect.client import Connection, ConnectError, ConnectUnavailable
from bombadil.connect.driver import Driver, fingerprint
from bombadil.connect.service import Service

NOON = 1_790_000_000.0


class Hangs(Driver):
    """Says it is well at once and never finishes a press."""
    kind = "hangs"

    async def start(self):
        self.emit({"push": "state", "state": "ok", "note": ""})

    async def stop(self):
        pass

    def owns(self, kind, target):
        return target.startswith("hang:")

    def can_post(self):
        return True

    async def perform(self, kind, target, content):
        await asyncio.sleep(3600)


class Running:
    """A service in a thread of its own."""

    def __init__(self, home, **kw):
        self.service = Service(home / "state", home / "connect.sock", **kw)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error: BaseException | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            task = asyncio.ensure_future(self.service.serve())
            while not self.service.socket_path.exists() and not task.done():
                await asyncio.sleep(0.01)
            self.ready.set()
            await task
        try:
            asyncio.run(main())
        except BaseException as e:  # noqa: BLE001 - reported to the test that started it
            self.error = e
            self.ready.set()

    def start(self) -> "Running":
        self.thread.start()
        assert self.ready.wait(10), "the service did not start"
        assert self.error is None, self.error
        return self

    def stop(self):
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.service.stop)
            self.thread.join(10)

    def ask(self, coro_fn):
        """Run something on the service's loop and give what it returns (to look inside)."""
        async def run():
            return coro_fn()
        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(5)


@pytest.fixture(autouse=True)
def places(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_CONNECT_UIDS", "0-65535")
    monkeypatch.setenv("BOMBADIL_CONNECT_SOCKET", str(tmp_path / "connect.sock"))
    monkeypatch.setenv("BOMBADIL_CONNECT_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("BOMBADIL_CONNECT_ENGINE", raising=False)
    return tmp_path


@pytest.fixture
def running(tmp_path):
    made: list[Running] = []

    def make(**kw):
        kw.setdefault("engine", "fake")
        made.append(Running(tmp_path, **kw).start())
        return made[-1]
    yield make
    for r in made:
        r.stop()


def until(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.01)
    raise AssertionError(f"waited {timeout}s for {getattr(fn, '__name__', fn)}")


# -- when the service is not there --

def test_with_no_service_every_way_in_says_connections_are_not_running_yet():
    with pytest.raises(ConnectUnavailable) as e:
        Connection()
    assert str(e.value) == "Connections are not running yet." == client.DOWN
    assert e.value.detail and "FileNotFoundError" in e.value.detail
    with pytest.raises(ConnectUnavailable) as e:
        client.request("status")
    assert str(e.value) == client.DOWN
    with pytest.raises(ConnectUnavailable):
        client.request("messages", unread=True)


def test_a_path_that_cannot_be_a_socket_is_the_same_sentence(tmp_path):
    with pytest.raises(ConnectUnavailable) as e:
        Connection(tmp_path / ("x" * 300) / "c.sock")
    assert str(e.value) == client.DOWN
    (tmp_path / "plain").write_text("not a socket")
    with pytest.raises(ConnectUnavailable) as e:
        Connection(tmp_path / "plain")
    assert str(e.value) == client.DOWN


def test_notify_with_no_service_is_false_and_quick_and_never_raises():
    begun = time.monotonic()
    assert client.notify("seen", refs=[]) is False
    assert time.monotonic() - begun < client.NOTIFY_SECONDS


# -- calls --

def test_a_call_gets_its_result(running):
    running()
    assert [c["id"] for c in client.request("connections")["connections"]] == ["slack:w1", "mcp:linear"]
    with Connection() as conn:
        assert conn.request("status")["state"] == "ok"
        assert len(conn.request("messages", unread=True, limit=2)["messages"]) == 2
        assert [t["ref"] for t in conn.request("tasks")["items"]] == ["linear:LIN-41", "linear:LIN-40"]


def test_a_no_from_the_service_is_a_connect_error_with_its_sentence_and_code(running):
    running()
    with Connection() as conn:
        with pytest.raises(ConnectError) as e:
            conn.request("remove_connection", id="slack:w9")      # the call's own id is the id the service echoes
        assert e.value.code == "not_found" and str(e.value) == "There is no such connection."
        with pytest.raises(ConnectError) as e:
            conn.request("frobnicate")
        assert e.value.code == "bad_request"
        with pytest.raises(ConnectError) as e:
            conn.request("perform", kind="slack_reply", target="slack:TW1/D0/1.000000", content="hello",
                         fingerprint="0" * 64, proposal="p1")
        assert e.value.code == "changed" and str(e.value).endswith("Nothing was done.")
        assert conn.request("status")["state"] == "ok"           # and the connection is as it was


def test_a_press_goes_through_the_client_and_is_answered_once(running):
    running()
    ref = next(m["ref"] for m in client.request("messages")["messages"] if m["ref"].startswith("slack:TW1/C0LAUNCH/"))
    args = {"kind": "slack_reply", "target": ref, "content": "Yes.", "proposal": "p1",
            "fingerprint": fingerprint("slack_reply", ref, "Yes.")}
    assert client.request("perform", **args)["receipt"]["line"].startswith("Posted in #launch · ")
    with pytest.raises(ConnectError) as e:
        client.request("perform", **args)
    assert e.value.code == "already"


def test_a_request_that_cannot_be_written_as_json_is_refused_and_not_sent(running):
    running()
    with Connection() as conn:
        with pytest.raises(ConnectError) as e:
            conn.request("seen", refs=[float("nan")])
        assert e.value.code == "bad_request"
        assert conn.request("status")["state"] == "ok"


def test_text_goes_as_it_is_and_a_long_one_in_another_script_still_fits(running):
    running()
    with Connection() as conn:
        with pytest.raises(ConnectError) as e:
            conn.request("perform", kind="slack_reply", target="slack:TW1/D0/1.000000", content="こんにちは" * 5000,
                         fingerprint="f" * 64, proposal="p1")
        assert e.value.code == "changed"                          # it reached the fingerprint check: it was read whole


# -- notify --

def test_notify_is_true_when_the_service_says_yes_and_false_when_it_says_no(running):
    running()
    assert client.notify("seen", refs=[]) is True
    assert client.notify("seen", refs=["not a ref"]) is False
    assert client.notify("nonsense") is False


def test_notify_gives_up_in_a_third_of_a_second_on_a_service_that_does_not_answer(tmp_path):
    path = str(tmp_path / "connect.sock")
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.bind(path)
    silent.listen(4)
    try:
        begun = time.monotonic()
        assert client.notify("seen", refs=[]) is False
        assert time.monotonic() - begun < client.NOTIFY_SECONDS + 0.25
    finally:
        silent.close()


def test_notify_gives_up_on_a_service_that_is_slow_to_answer_a_real_press(running):
    r = running(engine="", registry={"slack": Hangs, "mcp": Hangs})
    client.request("add_connection", service="slack")
    until(lambda: r.service.lives["slack:w1"].record["state"] == "ok")
    begun = time.monotonic()
    assert client.notify("perform", kind="slack_reply", target="hang:1", content="hello", proposal="p1",
                         fingerprint=fingerprint("slack_reply", "hang:1", "hello")) is False
    assert time.monotonic() - begun < client.NOTIFY_SECONDS + 0.25


# -- pushes --

def test_a_subscriber_reads_the_pushes_with_push(running):
    running()
    with Connection() as watcher, Connection() as other:
        assert watcher.request("subscribe") == {}
        assert watcher.push(0.05) is None                                  # nothing yet, and the connection is as it was
        other.request("fake_inject", message={"text": "Are we still on for Thursday?"})
        got = watcher.push(3.0)
        assert got["push"] == "message" and got["message"]["text"] == "Are we still on for Thursday?"
        assert got["message"]["unread"] is True
        assert watcher.push(0.05) is None
        assert watcher.request("status")["state"] == "ok"


def test_pushes_that_arrive_while_a_call_waits_are_kept_for_push_in_order(running):
    running()
    with Connection() as watcher, Connection() as other:
        watcher.request("subscribe")
        for n in range(3):
            other.request("fake_inject", message={"text": f"message {n}"})
        watcher.request("status")                                          # reads what is waiting while it waits
        texts = []
        while len(texts) < 3:
            got = watcher.push(3.0)
            assert got is not None
            texts.append(got["message"]["text"])
        assert texts == ["message 0", "message 1", "message 2"]


def test_a_connection_push_comes_when_a_tool_is_added_and_allowed(running, monkeypatch):
    monkeypatch.setenv("BOMBADIL_CONNECT_FAKE_SIGNIN_S", "0.05")
    running()
    with Connection() as watcher:
        watcher.request("subscribe")
        client.request("add_connection", service="todoist")
        states = []
        end = time.monotonic() + 5
        while "ok" not in states and time.monotonic() < end:
            got = watcher.push(1.0)
            if got and got["push"] == "connection" and got["connection"]["id"] == "mcp:todoist":
                states.append(got["connection"]["state"])
        assert states[-1] == "ok" and "signin" in states


# -- a call that does not come back --

def test_a_call_that_times_out_closes_its_connection_and_the_next_one_says_unavailable(running):
    r = running(engine="", registry={"slack": Hangs, "mcp": Hangs})
    client.request("add_connection", service="slack")
    until(lambda: r.service.lives["slack:w1"].record["state"] == "ok")
    conn = Connection()
    begun = time.monotonic()
    with pytest.raises(ConnectUnavailable) as e:
        conn.request("perform", 0.3, kind="slack_reply", target="hang:1", content="hello", proposal="p1",
                     fingerprint=fingerprint("slack_reply", "hang:1", "hello"))
    assert 0.25 < time.monotonic() - begun < 2.0
    assert str(e.value) == client.DOWN and "no answer to perform" in e.value.detail
    with pytest.raises(ConnectUnavailable) as again:                       # it is closed: no answer can be mistaken for this one's
        conn.request("status")
    assert str(again.value) == client.DOWN and "closed" in again.value.detail
    with pytest.raises(ConnectUnavailable):
        conn.push(0.1)
    assert client.request("status")["state"] == "ok"                       # a connection of its own works
    until(lambda: len(r.ask(lambda: list(r.service.peers))) <= 1)          # and the service saw the closed one go


def test_a_service_that_goes_away_mid_connection_is_unavailable_with_its_detail(running):
    r = running()
    conn = Connection()
    assert conn.request("status")["state"] == "ok"
    r.stop()
    with pytest.raises(ConnectUnavailable) as e:
        conn.request("status")
    assert str(e.value) == client.DOWN
    with pytest.raises(ConnectUnavailable):
        conn.request("status")
    conn.close()
    conn.close()                                                           # closing again is nothing


def test_the_timeout_is_for_the_whole_call_not_for_each_read(tmp_path):
    path = str(tmp_path / "connect.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(2)

    def trickle():
        conn, _ = server.accept()
        conn.recv(65536)
        for _ in range(20):                                                # a push every 0.1 s, never the answer
            try:
                conn.sendall((json.dumps({"push": "message", "message": {}}) + "\n").encode())
            except OSError:
                return
            time.sleep(0.1)
        conn.close()
    threading.Thread(target=trickle, daemon=True).start()
    try:
        begun = time.monotonic()
        with Connection() as conn, pytest.raises(ConnectUnavailable):
            conn.request("status", 0.5)
        assert time.monotonic() - begun < 1.5
    finally:
        server.close()


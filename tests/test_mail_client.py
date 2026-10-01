"""mail/client.py: the blocking client, against a small socket server that does what each test says."""

import json
import math
import socket
import threading
import time

import pytest

from bombadil.mail import client
from bombadil.mail.client import Connection, MailError, MailUnavailable


class Server:
    """A mail.sock that answers by a script: `on(conn, request)` is called for every request line, in a thread
    of its own per connection, and does whatever the test wants with the socket."""

    def __init__(self, path, on):
        self.path = str(path)
        self.on = on
        self.requests: list[dict] = []
        self.connections = 0
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(8)
        self.stop = False
        self.conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buffer = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                req = json.loads(line)
                self.requests.append(req)
                try:
                    self.on(conn, req)
                except OSError:
                    return

    def close(self):
        self.stop = True
        self.sock.close()
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def say(conn, obj):
    conn.sendall((json.dumps(obj) + "\n").encode())


def ok(conn, req, result=None):
    say(conn, {"id": req["id"], "ok": True, "result": result})


@pytest.fixture
def serve(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(tmp_path / "mail.sock"))
    servers = []

    def make(on):
        server = Server(tmp_path / "mail.sock", on)
        servers.append(server)
        return server

    yield make
    for s in servers:
        s.close()


# -- calls --

def test_a_call_gets_its_result(serve):
    server = serve(lambda c, r: ok(c, r, {"echo": r["op"], "n": r.get("n")}))
    with Connection() as conn:
        assert conn.request("ping", 1.0, n=5) == {"echo": "ping", "n": 5}
    assert server.requests[0]["op"] == "ping" and server.requests[0]["n"] == 5


def test_a_call_with_no_id_of_its_own_gets_a_counter_and_calls_go_one_after_another(serve):
    server = serve(lambda c, r: ok(c, r, r["id"]))
    with Connection() as conn:
        assert [conn.request("ping", 1.0) for _ in range(3)] == [1, 2, 3]
    assert [r["id"] for r in server.requests] == [1, 2, 3]


def test_an_op_that_names_a_mail_or_draft_with_id_uses_it_as_the_id_the_service_echoes(serve):
    server = serve(lambda c, r: ok(c, r, r["id"]))
    with Connection() as conn:
        assert conn.request("read", 1.0, id="a1/k1@example.test") == "a1/k1@example.test"
        assert conn.request("send", 1.0, id="d7", fingerprint="f" * 64) == "d7"
    assert server.requests[1] == {"id": "d7", "op": "send", "fingerprint": "f" * 64}


def test_a_no_from_the_service_is_a_mail_error_with_its_sentence_and_code(serve):
    serve(lambda c, r: say(c, {"id": r["id"], "ok": False, "error": "That is not the draft.", "code": "changed"}))
    with Connection() as conn, pytest.raises(MailError) as e:
        conn.request("send", 1.0, id="d1")
    assert str(e.value) == "That is not the draft." and e.value.code == "changed"


def test_a_no_with_no_words_or_code_still_says_something(serve):
    serve(lambda c, r: say(c, {"id": r["id"], "ok": False}))
    with Connection() as conn, pytest.raises(MailError) as e:
        conn.request("x", 1.0)
    assert str(e.value) and e.value.code == "error"


def test_an_answer_without_an_id_is_the_answer_to_the_call_in_flight(serve):
    serve(lambda c, r: say(c, {"id": None, "ok": False, "error": "That request is too long.", "code": "bad_request"}))
    with Connection() as conn, pytest.raises(MailError) as e:
        conn.request("x", 1.0)
    assert e.value.code == "bad_request"


def test_answers_to_other_calls_are_skipped(serve):
    def on(c, r):
        say(c, {"id": 999, "ok": True, "result": "stale"})
        say(c, {"id": "other", "ok": True, "result": "stale"})
        ok(c, r, "mine")
    serve(on)
    with Connection() as conn:
        assert conn.request("x", 1.0) == "mine"


def test_a_request_that_cannot_be_written_as_json_is_refused_and_not_sent(serve):
    server = serve(lambda c, r: ok(c, r))
    with Connection() as conn:
        for bad in (math.nan, object(), {1, 2}):
            with pytest.raises(MailError) as e:
                conn.request("x", 1.0, value=bad)
            assert e.value.code == "bad_request"
        assert conn.request("x", 1.0) is None   # and the connection is still good
    assert len(server.requests) == 1


def test_text_goes_as_text_and_not_as_escapes_so_a_long_draft_in_any_script_fits_the_limit(serve):
    line = client._encode({"op": "draft", "body": "\u00e9" * 400_000})
    assert len(line) < 1 << 20 and b"\\u" not in line          # as escapes it would be six times the size
    odd = client._encode({"a": "x\ny" + chr(0x2028) + "z"})
    assert odd.count(b"\n") == 1 and odd.endswith(b"\n")       # one request is one line, whatever is in the text


def test_a_lone_surrogate_cannot_be_text_so_that_one_request_goes_escaped_and_the_call_carries_on(serve):
    lone = chr(0xD800)
    server = serve(lambda c, r: ok(c, r, r["body"]))
    with Connection() as conn:
        assert conn.request("draft", 1.0, body="a" + lone + "b") == "a" + lone + "b"
        assert conn.request("draft", 1.0, body="fine \u00e9") == "fine \u00e9"
    assert len(server.requests) == 2


def test_calls_from_two_threads_each_get_their_own_answer(serve):
    serve(lambda c, r: ok(c, r, r["tag"]))
    got = {}
    with Connection() as conn:
        def call(tag):
            got[tag] = conn.request("echo", 5.0, tag=tag)
        threads = [threading.Thread(target=call, args=(f"t{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert got == {f"t{i}": f"t{i}" for i in range(8)}


# -- the service is not there, or stops answering --

def test_no_socket_is_mail_not_running_with_the_reason_kept_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(tmp_path / "missing.sock"))
    with pytest.raises(MailUnavailable) as e:
        Connection()
    assert str(e.value) == "Mail is not running yet." and "FileNotFoundError" in e.value.detail


def test_a_socket_file_nobody_listens_on_is_the_same(tmp_path, monkeypatch):
    path = tmp_path / "dead.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path))
    s.close()   # the file stays, nothing listens
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(path))
    with pytest.raises(MailUnavailable):
        Connection()
    with pytest.raises(MailUnavailable):
        client.request("ping")


def test_a_path_too_long_for_a_socket_is_unavailable_and_not_a_crash(tmp_path):
    with pytest.raises(MailUnavailable):
        Connection(tmp_path / ("x" * 300))


def test_a_path_can_be_given(tmp_path):
    path = tmp_path / "other.sock"
    server = Server(path, lambda c, r: ok(c, r, "there"))
    try:
        with Connection(path) as conn:
            assert conn.request("ping", 1.0) == "there"
    finally:
        server.close()


def test_a_service_that_never_answers_is_given_up_on_in_the_time_given_and_the_connection_is_closed(serve):
    serve(lambda c, r: None)
    conn = Connection()
    started = time.monotonic()
    with pytest.raises(MailUnavailable) as e:
        conn.request("send", 0.3, id="d1")
    assert 0.25 < time.monotonic() - started < 1.5 and "no answer" in e.value.detail
    with pytest.raises(MailUnavailable):   # an answer that came late could be taken for another call's: it is closed
        conn.request("ping", 1.0)


def test_the_time_is_for_the_whole_call_not_for_each_read(serve):
    def on(c, r):
        for _ in range(100):
            say(c, {"push": "changed", "what": ["list"]})
            time.sleep(0.05)
    serve(on)
    started = time.monotonic()
    with Connection() as conn, pytest.raises(MailUnavailable):
        conn.request("x", 0.3)
    assert time.monotonic() - started < 1.5


def test_a_service_that_hangs_up_in_the_middle_of_a_call_is_unavailable(serve):
    serve(lambda c, r: c.shutdown(socket.SHUT_RDWR))
    with Connection() as conn, pytest.raises(MailUnavailable) as e:
        conn.request("send", 2.0, id="d1")
    assert "closed" in e.value.detail


def test_a_service_that_hangs_up_before_the_call_is_unavailable(serve):
    server = serve(lambda c, r: ok(c, r))
    conn = Connection()
    for _ in range(100):
        if server.conns:
            break
        time.sleep(0.01)
    for c in server.conns:
        c.shutdown(socket.SHUT_RDWR)
    time.sleep(0.05)
    with pytest.raises(MailUnavailable):
        conn.request("x", 1.0)


@pytest.mark.parametrize("line", [b"not json\n", b"[1, 2]\n", b'"text"\n', b"\x00\xff\n"])
def test_a_service_that_sends_something_else_is_not_believed(serve, line):
    serve(lambda c, r: c.sendall(line))
    with Connection() as conn, pytest.raises(MailUnavailable):
        conn.request("x", 1.0)


def test_a_line_that_never_ends_is_not_buffered_for_ever(serve, monkeypatch):
    monkeypatch.setattr(client, "MAX_LINE", 10_000)
    serve(lambda c, r: [c.sendall(b"x" * 4000) for _ in range(5)])
    with Connection() as conn, pytest.raises(MailUnavailable) as e:
        conn.request("x", 2.0)
    assert "too long" in e.value.detail


def test_a_big_answer_that_does_end_is_fine(serve):
    serve(lambda c, r: ok(c, r, "y" * 3_000_000))
    with Connection() as conn:
        assert len(conn.request("x", 5.0)) == 3_000_000


def test_a_closed_connection_says_so(serve):
    serve(lambda c, r: ok(c, r))
    conn = Connection()
    conn.close()
    conn.close()
    with pytest.raises(MailUnavailable):
        conn.request("x", 1.0)
    with pytest.raises(MailUnavailable):
        conn.push(0.1)


# -- pushes --

def test_pushes_that_arrive_while_a_call_waits_are_kept_in_order_for_push(serve):
    def on(c, r):
        say(c, {"push": "changed", "what": ["list"]})
        say(c, {"push": "status", "engine": "up"})
        ok(c, r, "done")
    serve(on)
    with Connection() as conn:
        assert conn.request("subscribe", 1.0) == "done"
        assert [conn.push(0.0)["push"] for _ in range(2)] == ["changed", "status"]
        assert conn.push(0.0) is None or conn.push(0.05) is None


def test_push_waits_for_one_and_gives_none_when_none_comes_and_the_connection_stays_good(serve):
    def on(c, r):
        if r["op"] == "subscribe":
            ok(c, r, {})
            threading.Timer(0.2, lambda: say(c, {"push": "show", "view": "drafts"})).start()
        else:
            ok(c, r, "pong")
    serve(on)
    with Connection() as conn:
        conn.request("subscribe", 1.0)
        assert conn.push(0.05) is None
        assert conn.request("ping", 1.0) == "pong"
        assert conn.push(2.0) == {"push": "show", "view": "drafts"}


def test_push_reads_only_pushes_and_leaves_answers_to_calls(serve):
    def on(c, r):
        ok(c, r, {})
        say(c, {"id": 77, "ok": True, "result": "not for anyone"})
        say(c, {"push": "changed", "what": ["list"]})
    serve(on)
    with Connection() as conn:
        conn.request("subscribe", 1.0)
        assert conn.push(1.0)["push"] == "changed"


def test_a_subscriber_whose_service_goes_away_is_told_so(serve):
    server = serve(lambda c, r: ok(c, r, {}))
    with Connection() as conn:
        conn.request("subscribe", 1.0)
        for c in server.conns:
            c.shutdown(socket.SHUT_RDWR)
        with pytest.raises(MailUnavailable):
            conn.push(1.0)


def test_only_the_most_recent_pushes_are_kept(serve):
    def on(c, r):
        for i in range(client.PUSHES_KEPT + 50):
            say(c, {"push": "changed", "n": i})
        ok(c, r, {})
    serve(on)
    with Connection() as conn:
        conn.request("subscribe", 5.0)
        assert len(conn.pushes) == client.PUSHES_KEPT
        assert conn.push(0.0)["n"] == 50


def test_a_push_with_half_a_line_waiting_is_not_lost_when_push_times_out(serve):
    def on(c, r):
        ok(c, r, {})
        c.sendall(b'{"push": "chan')
        threading.Timer(0.2, lambda: c.sendall(b'ged", "what": []}\n')).start()
    serve(on)
    with Connection() as conn:
        conn.request("subscribe", 1.0)
        assert conn.push(0.05) is None
        assert conn.push(2.0) == {"push": "changed", "what": []}


# -- one call on its own, and a poke --

def test_request_is_one_call_on_a_connection_of_its_own(serve):
    server = serve(lambda c, r: ok(c, r, "x"))
    assert client.request("ping") == "x" and client.request("ping", 1.0, view="all") == "x"
    assert server.connections == 2 and server.requests[1]["view"] == "all"


def test_request_says_mail_is_not_running_when_it_is_not(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(tmp_path / "missing.sock"))
    with pytest.raises(MailUnavailable):
        client.request("ping")


def test_notify_says_whether_it_was_heard_and_never_raises(serve, tmp_path, monkeypatch):
    serve(lambda c, r: ok(c, r) if r["op"] == "show" else say(c, {"id": r["id"], "ok": False, "error": "no"}))
    assert client.notify("show", view="all") is True
    assert client.notify("other") is False   # a no is not an error for a poke


def test_notify_with_no_service_is_false(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(tmp_path / "missing.sock"))
    assert client.notify("show") is False


def test_notify_gives_up_on_a_silent_service_in_a_third_of_a_second(serve):
    serve(lambda c, r: None)
    started = time.monotonic()
    assert client.notify("show", view="all") is False
    assert time.monotonic() - started < 1.0


def test_the_two_failures_are_not_the_same_exception():
    assert not issubclass(MailError, MailUnavailable) and not issubclass(MailUnavailable, MailError)
    assert str(MailUnavailable("x")) == "Mail is not running yet."

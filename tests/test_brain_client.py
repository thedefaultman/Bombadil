"""The brain's client: an answer, a plain no, or "not running", and a poke that never waits."""

import json
import os
import socket
import threading
import time

import pytest

from bombadil import paths
from bombadil.brain import client


class FakeBrain:
    """A brain.sock that answers each request line with what `reply(request)` returns
    (a list of messages to send, possibly none)."""

    def __init__(self, path, reply):
        self.path = str(path)
        self.reply = reply
        self.seen: list[dict] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen()
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            buf = b""
            while True:
                try:
                    data = conn.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    req = json.loads(line)
                    self.seen.append(req)
                    for msg in self.reply(req) or []:
                        try:
                            conn.sendall((msg if isinstance(msg, bytes) else json.dumps(msg).encode()) + b"\n")
                        except OSError:
                            return

    def close(self):
        self.srv.close()
        os.unlink(self.path)


@pytest.fixture
def sock(home):
    path = paths.brain_socket()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_paths_default_and_override(home, monkeypatch):
    assert paths.brain_socket() == home / "run" / "brain.sock"
    assert paths.brain_db() == home / "state" / "brain.db"
    monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(home / "elsewhere.sock"))
    monkeypatch.setenv("BOMBADIL_BRAIN_DB", str(home / "b.db"))
    assert paths.brain_socket() == home / "elsewhere.sock"
    assert paths.brain_db() == home / "b.db"


def test_not_running_is_one_sentence(sock):
    with pytest.raises(client.BrainUnavailable) as e:
        client.request("status")
    assert str(e.value) == "The brain is not running yet."
    # A socket file with nobody behind it (the brain died) is the same.
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock))
    s.close()
    with pytest.raises(client.BrainUnavailable):
        client.request("status")


def test_answer_skips_pushes_and_stale_answers(sock):
    def reply(req):
        return [{"push": "changed", "things": [1], "t": 1.0}, {"id": req["id"] + 100, "ok": True, "result": "old"},
                b"not json", b"[1, 2]", {"id": req["id"], "ok": True, "result": {"text": "Knows 3 things."}}]

    fake = FakeBrain(sock, reply)
    try:
        with client.Connection() as conn:
            assert conn.request("status") == {"text": "Knows 3 things."}
            assert conn.push(0.1) == {"push": "changed", "things": [1], "t": 1.0}
            assert conn.push(0.1) is None
            assert conn.request("why", ref="/home/user/a.txt") == {"text": "Knows 3 things."}
        assert fake.seen[1] == {"id": 2, "op": "why", "ref": "/home/user/a.txt"}
        assert client.request("search", q="lease", limit=3) == {"text": "Knows 3 things."}
        assert fake.seen[-1] == {"id": 1, "op": "search", "q": "lease", "limit": 3}
    finally:
        fake.close()


def test_pushes_nobody_listens_to_do_not_pile_up(sock):
    pushes = [{"push": "changed", "things": [i], "t": 1.0} for i in range(client.PUSH_KEEP * 3)]
    fake = FakeBrain(sock, lambda req: [*pushes, {"id": req["id"], "ok": True, "result": "done"}])
    try:
        with client.Connection() as conn:
            assert conn.request("status") == "done"
            assert len(conn.pushes) == client.PUSH_KEEP
            assert conn.push(0.1)["things"] == [client.PUSH_KEEP * 2]   # the newest are kept
    finally:
        fake.close()


def test_a_no_carries_the_brains_sentence(sock):
    fake = FakeBrain(sock, lambda req: [{"id": req["id"], "ok": False, "error": "The brain does not know this yet."}])
    try:
        with pytest.raises(client.BrainError) as e:
            client.request("focus", ref="/nope")
        assert str(e.value) == "The brain does not know this yet."
    finally:
        fake.close()
    fake = FakeBrain(sock, lambda req: [{"id": req["id"], "ok": False}])
    try:
        with pytest.raises(client.BrainError, match="could not answer"):
            client.request("focus", ref="/nope")
    finally:
        fake.close()


def test_silence_and_hangups_are_unavailable(sock):
    fake = FakeBrain(sock, lambda req: [])
    try:
        t0 = time.monotonic()
        with pytest.raises(client.BrainUnavailable):
            client.request("status", timeout=0.3)
        assert time.monotonic() - t0 < 1.5
    finally:
        fake.close()

    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sock))
    srv.listen()

    def hang_up():
        conn, _ = srv.accept()
        conn.recv(100)
        conn.close()
    threading.Thread(target=hang_up, daemon=True).start()
    try:
        with pytest.raises(client.BrainUnavailable) as e:
            client.request("status", timeout=2)
        assert e.value.detail == "the brain hung up"
    finally:
        srv.close()


def test_notify_hands_over_and_never_waits(sock):
    fake = FakeBrain(sock, lambda req: [])   # reads, never answers
    try:
        t0 = time.monotonic()
        assert client.notify("note", kind="turn_start", n=4, unit="bombadil-turn-1", prompt="hi", t=1.5)
        assert time.monotonic() - t0 < 0.5
        deadline = time.monotonic() + 2
        while not fake.seen and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fake.seen == [{"id": 0, "op": "note", "kind": "turn_start", "n": 4, "unit": "bombadil-turn-1",
                              "prompt": "hi", "t": 1.5}]
        # Whatever it is given, it does not raise: an object is sent as its string.
        assert client.notify("note", kind="turn_end", path=sock) is True
    finally:
        fake.close()


def test_notify_with_nobody_there_is_false_and_quick(sock):
    t0 = time.monotonic()
    assert client.notify("note", kind="turn_end", n=1) is False
    # A server that never accepts: the connect itself is what times out.
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sock))
    srv.listen(0)
    fillers = []
    try:
        for _ in range(8):   # fill the backlog so the next connect blocks
            f = socket.socket(socket.AF_UNIX)
            f.setblocking(False)
            try:
                f.connect(str(sock))
            except OSError:
                pass
            fillers.append(f)
        assert client.notify("note", kind="turn_end", n=1) is False
    finally:
        for f in fillers:
            f.close()
        srv.close()
    assert time.monotonic() - t0 < 1.5

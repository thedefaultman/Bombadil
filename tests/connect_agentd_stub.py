"""A stand-in for connect.sock: the client side of the wire protocol (docs/CONNECT.md) and nothing more.

Everything on agentd's side of connections (the watch, the notices and cards, the press, the agent's tools, the
launcher words) is tested against this, so those tests need neither the real service nor a Slack. It is
tests/mail_stub.py with the other service's housekeeping (`subscribe` and `status`, which is how agentd tests the
silence of a connection that stays open): it answers what it was told to answer, keeps every request it got, and
can push to subscribers, go silent and hang up.

It runs in a thread of its own, so a blocking client in the test's thread (the launcher) and agentd's event loop can
both use it at once.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from bombadil import paths


class StubConnect:
    def __init__(self, path):
        self.path = Path(path)
        self.requests: list[dict] = []     # the work asked of it, as sent, in order
        self.watching: list[dict] = []     # subscribe and status: agentd's own housekeeping, apart from the work
        self.results: dict[str, object] = {}     # op -> result, or a function of the request
        self.errors: dict[str, tuple[str, str]] = {}   # op -> (sentence, code)
        self.delays: dict[str, float] = {}       # op -> seconds before it answers; "hang" never does
        self.connections = 0
        self._subs: list[asyncio.StreamWriter] = []
        self._writers: list[asyncio.StreamWriter] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._ready = threading.Event()

    # -- what a test says --

    def answer(self, op: str, result) -> None:
        self.results[op] = result
        self.errors.pop(op, None)

    def fail(self, op: str, sentence: str, code: str = "internal") -> None:
        self.errors[op] = (sentence, code)

    def delay(self, op: str, seconds) -> None:
        self.delays[op] = seconds

    def asked(self, op: str) -> list[dict]:
        return [r for r in list(self.requests) + list(self.watching) if r.get("op") == op]

    def push(self, msg: dict) -> None:
        """Say something to every subscriber."""
        self._in_loop(self._push, msg)

    def hang_up(self) -> None:
        """Close every connection (the service restarted); new ones are still accepted."""
        self._in_loop(self._hang_up)

    def subscribers(self) -> int:
        return len(self._subs)

    # -- running --

    def start(self) -> "StubConnect":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "the stub connection service did not start"
        return self

    def stop(self) -> None:
        if self._loop is not None and self._thread is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(5)
        self._loop = None

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # A request line is up to a megabyte, as the service takes it (docs/CONNECT.md).
        self._server = self._loop.run_until_complete(
            asyncio.start_unix_server(self._serve, path=str(self.path), limit=1 << 20))
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._server.close()
            for w in list(self._writers):
                w.close()
            # A connection that was told to hang is still sleeping: end it, or the loop closes on it.
            pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
            for t in pending:
                t.cancel()
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def _in_loop(self, fn, *args) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(fn, *args)

    def _push(self, msg: dict) -> None:
        for w in list(self._subs):
            w.write((json.dumps(msg) + "\n").encode())

    def _hang_up(self) -> None:
        for w in list(self._writers):
            w.close()
        self._subs.clear()
        self._writers.clear()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        try:
            while line := await reader.readline():
                req = json.loads(line)
                op = req.get("op")
                (self.watching if op in ("subscribe", "status") else self.requests).append(req)
                if op == "subscribe":
                    self._subs.append(writer)
                delay = self.delays.get(op, 0)
                if delay == "hang":
                    await asyncio.sleep(3600)
                elif delay:
                    await asyncio.sleep(delay)
                if op in self.errors:
                    sentence, code = self.errors[op]
                    reply = {"id": req.get("id"), "ok": False, "error": sentence, "code": code}
                else:
                    result = self.results.get(op, {})
                    reply = {"id": req.get("id"), "ok": True, "result": result(req) if callable(result) else result}
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if writer in self._subs:
                self._subs.remove(writer)
            if writer in self._writers:
                self._writers.remove(writer)
            writer.close()


@pytest.fixture
def connect_paths(home, monkeypatch):
    """Where the connection service would be and keep its state, under the test's own directory: nothing here may
    reach for /run/bombadil-connect (whose default has no override but this one). Nothing is listening there."""
    monkeypatch.setenv("BOMBADIL_CONNECT_SOCKET", str(home / "run" / "connect.sock"))
    monkeypatch.setenv("BOMBADIL_CONNECT_STATE", str(home / "connect-state"))
    return paths.connect_socket()


@pytest.fixture(name="connect")
def connect_service(connect_paths):
    """A stub connection service on the socket the whole of Bombadil looks for."""
    stub = StubConnect(connect_paths).start()
    yield stub
    stub.stop()


# What the service says, as docs/CONNECT.md and connect/driver.py give the shapes. A message made now is a new one:
# agentd says nothing about a message older than two minutes.

def message(key="1727780000.000100", frm="Priya Shah", text="Legal just signed off. Can we lock the 14th for the "
            "launch?", channel="launch", kind="channel", team="T1", ts=None, cid=None, **more) -> dict:
    cid = cid or ("C1" if kind in ("channel", "private") else "D1")
    conv = {"id": cid, "name": channel if kind != "dm" else "", "kind": kind}
    return {"ref": f"slack:{team}/{conv['id']}/{key}", "source": "slack", "connection": "slack:w1",
            "conversation": conv, "thread": None, "from": {"id": "U2", "name": frm}, "text": text,
            "ts": time.time() if ts is None else ts, "mentions_me": False, "reason": f"in #{channel}",
            "unread": True, "web_url": f"https://acme.test/archives/{conv['id']}/p{key.replace('.', '')}", **more}


def task(ref="linear:LIN-42", title="Review the launch checklist", **more) -> dict:
    return {"ref": ref, "service": ref.split(":", 1)[0], "connection": "mcp:linear", "title": title,
            "why": "assigned to you", "status": "In progress", "due": None, "url": "https://linear.test/LIN-42",
            "fields": [], **more}


def connection(cid="slack:w1", service="slack", name="Acme", state="ok", note="", **more) -> dict:
    return {"id": cid, "kind": cid.split(":", 1)[0], "service": service, "name": name, "state": state, "note": note,
            "reads": "Direct messages, mentions and threads.", "can_post": state == "ok", "steps": [], **more}

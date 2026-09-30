"""A stand-in for mail.sock: the client side of the wire protocol (docs/MAIL.md) and nothing more.

Everything that talks to the mail service (agentd, the launcher, the CLI) is tested against this, so
those tests need neither the real service nor a Thunderbird. It answers what it was told to answer,
keeps every request it got, and can push to subscribers, go silent and hang up.

It runs in a thread of its own, so a blocking client in the test's thread (the launcher, the CLI) and
agentd's event loop can both use it at once.
"""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from bombadil import paths


class StubMail:
    def __init__(self, path):
        self.path = Path(path)
        self.requests: list[dict] = []     # the work asked of it, as sent, in order
        self.watching: list[dict] = []     # subscribe and ping: agentd's own housekeeping, apart from the work
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

    def fail(self, op: str, sentence: str, code: str = "engine_error") -> None:
        self.errors[op] = (sentence, code)

    def delay(self, op: str, seconds: float) -> None:
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

    def start(self) -> "StubMail":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "the stub mail service did not start"
        return self

    def stop(self) -> None:
        if self._loop is not None and self._thread is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(5)
        self._loop = None

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._server = self._loop.run_until_complete(asyncio.start_unix_server(self._serve, path=str(self.path)))
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
                (self.watching if op in ("subscribe", "ping") else self.requests).append(req)
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


@pytest.fixture(name="mail")
def mail_service(home):
    """A stub mail service on the socket the whole of Bombadil looks for (under the test's own runtime dir)."""
    stub = StubMail(paths.mail_socket()).start()
    yield stub
    stub.stop()


# A small mailbox of Msg-shaped rows (docs/MAIL.md), for tests that need mail to show.
def msg(key="k1", frm=("Priya Shah", "priya@example.test"), subject="Launch date", ts=1_790_000_000.0, **more) -> dict:
    return {"id": f"a1/{key}", "account": "a1", "key": key, "from": {"name": frm[0], "email": frm[1]},
            "to": [{"name": "Maya", "email": "maya@example.test"}], "cc": [], "subject": subject, "ts": ts,
            "unread": True, "flagged": False, "attachments": False, "folder": "inbox", "needs_reply": False,
            "why": None, "thread": None, **more}

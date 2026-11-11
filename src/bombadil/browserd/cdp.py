"""One lasting Chrome DevTools Protocol connection to one tab, over `bombadil.ws`.

The page's websocket address comes from the debugging port's `/json` list (the service reads it through
`browser.DevTools`; this module only speaks over it). A `Session` sends a command and waits for the answer with the
same `id`, passes the events it is told about to the service, and says what went wrong in three kinds of exception
the service turns into sentences: `Gone` (the tab or the browser closed the connection), `Timeout` (the page did not
answer: a dialog open, a hung script) and `Failed` (the page said no; `.transient` when it was only mid-navigation).

Nothing here says what a page's text is or what a command's answer held: errors carry a kind and a CDP code, never
the page's words, so they can be logged.
"""

import asyncio
import json

from .. import ws

EVALUATE_S = 10.0
# What Chromium says while a page is being replaced under a script: asking again a moment later works.
_TRANSIENT = ("context was destroyed", "cannot find context", "cannot find default execution context",
              "inspected target navigated or closed")


class CdpError(Exception):
    """Base of the three below; `str()` is a kind, not anything a page said."""


class Gone(CdpError):
    """The connection to the tab closed: the tab was closed or the browser went away."""


class Timeout(CdpError):
    """The page did not answer in time."""


class Failed(CdpError):
    def __init__(self, kind: str, transient: bool = False):
        super().__init__(kind)
        self.transient = transient


class Session:
    """Use `Session.open(...)`. `on_event(session, method, params)` is called (on the loop) for each event."""

    def __init__(self, tab_id: str, sock: ws.WebSocket, on_event):
        self.tab_id = tab_id
        self.sock = sock
        self.on_event = on_event
        self.closed = False
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, tab_id: str, url: str, on_event, timeout: float = 5.0) -> "Session":
        """Connect to the tab's websocket and ask for its navigation events. Raises ws.WebSocketError when it
        will not connect, `Gone` or `Timeout` when it connected and then did not answer."""
        sock = await ws.connect(url, timeout=timeout)
        session = cls(tab_id, sock, on_event)
        try:
            await session.call("Page.enable", timeout=timeout)
        except BaseException:
            await session.close()
            raise
        return session

    async def _read(self) -> None:
        try:
            while True:
                raw = await self.sock.recv()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if isinstance(msg.get("id"), int):
                    future = self._pending.pop(msg["id"], None)
                    if future is not None and not future.done():
                        future.set_result(msg)
                elif isinstance(msg.get("method"), str):
                    params = msg.get("params")
                    self.on_event(self, msg["method"], params if isinstance(params, dict) else {})
        except (ws.WebSocketError, OSError):
            pass
        finally:
            self.closed = True
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(Gone("closed"))
            self._pending.clear()

    async def call(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        """One command; its `result` object. Raises Gone, Timeout or Failed."""
        timeout = EVALUATE_S if timeout is None else timeout
        if self.closed:
            raise Gone("closed")
        self._next += 1
        rid = self._next
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        try:
            await self.sock.send(json.dumps({"id": rid, "method": method, "params": params or {}}))
            msg = await asyncio.wait_for(future, timeout)
        except ws.WebSocketError:
            raise Gone("closed") from None
        except TimeoutError:
            raise Timeout(method) from None
        finally:
            self._pending.pop(rid, None)
        error = msg.get("error")
        if isinstance(error, dict):
            text = str(error.get("message", "")).lower()
            raise Failed(f"cdp {error.get('code')}", transient=any(t in text for t in _TRANSIENT))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    async def evaluate(self, expression: str, timeout: float | None = None):
        """The value of an expression, by value. The expressions are the service's own (overlay.py)."""
        timeout = EVALUATE_S if timeout is None else timeout
        out = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "silent": True, "userGesture": False,
            "awaitPromise": False, "timeout": int(timeout * 1000)}, timeout + 1)
        if "exceptionDetails" in out:
            raise Failed("script")
        result = out.get("result")
        return result.get("value") if isinstance(result, dict) else None

    async def close(self) -> None:
        self.closed = True
        self._reader.cancel()
        try:
            await self.sock.close()
        except (ws.WebSocketError, OSError):
            pass
        await asyncio.gather(self._reader, return_exceptions=True)

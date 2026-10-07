"""A stand-in for Chromium's debugging port: the HTTP endpoints `browser.DevTools` reads (`/json/version`, `/json/list`,
`/json/new`, `/json/activate`, `/json/close`) and, per tab, a websocket (over `bombadil.ws.accept`) that answers the
only commands the service sends: `Page.enable` and `Runtime.evaluate`.

It cannot run JavaScript. What it does instead is the thing a test needs: it accepts an expression only when it is
exactly one of the fixed scripts in `bombadil.browserd.overlay` (anything else is refused, as an unknown script
cannot be run here), reads the script's name and data, and answers as the script would for the `FakePage` the tab
shows. `FakePage` is a small model of a page: its text, its fields, its buttons and the pointer drawn on it. The
model of "find" (exact words, then whole words, then part of the words) follows the script's own order.

Everything a test changes (a page, a closed tab, a navigation) goes through the stand-in's own loop.
"""

import asyncio
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass, field

from bombadil import ws
from bombadil.browserd import overlay


def norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


@dataclass
class FakeElement:
    label: str
    rect: tuple = (20.0, 20.0, 100.0, 30.0)         # x, y, w, h in the window
    selector: str | None = None
    visible: bool = True
    on_press: object = None                         # callable(tab_id): what pressing it does


@dataclass
class FakePage:
    url: str
    title: str = ""
    text: str = ""
    inputs: list = field(default_factory=list)       # values of fields (a token shown in a box)
    elements: list = field(default_factory=list)
    selectors: dict = field(default_factory=dict)    # selector -> text it reads as
    overlay: dict | None = None
    loop: object = None
    hold_timers: bool = False                        # a page whose own timers do not run (a hidden tab)
    _timer: object = None

    def find(self, needle: str) -> FakeElement | None:
        n = norm(needle)
        best, best_key = None, None
        for el in self.elements:
            label = norm(el.label)
            if not el.visible or not label:
                continue
            if label == n:
                score = 3
            elif re.search(r"(^|[^\w])" + re.escape(n) + r"($|[^\w])", label):
                score = 2
            elif n in label:
                score = 1
            else:
                continue
            key = (score, -len(label))
            if best_key is None or key > best_key:
                best, best_key = el, key
        return best

    def by_selector(self, selector: str) -> FakeElement | None:
        return next((e for e in self.elements if e.visible and e.selector == selector), None)

    def draw(self, element: FakeElement, label: str, ttl_ms: int) -> None:
        self.clear()
        token = object()
        self.overlay = {"label": label, "rect": element.rect, "element": element, "token": token}
        if self.loop is not None and not self.hold_timers:
            self._timer = self.loop.call_later(ttl_ms / 1000, self._expire, token)

    def _expire(self, token) -> None:
        if self.overlay is not None and self.overlay["token"] is token:
            self.overlay = None

    def clear(self) -> bool:
        had = self.overlay is not None
        self.overlay = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        return had

    # -- the fixed scripts, as this page would answer them --

    def script_read(self, args: dict) -> dict:
        selector = args.get("selector")
        if selector and selector.startswith("::bad"):
            return {"error": "selector"}
        if selector and selector not in self.selectors:
            return {"found": False, "url": self.url, "title": self.title, "text": ""}
        text = self.selectors[selector] if selector else self.text
        return {"found": True, "url": self.url, "title": self.title, "text": text[: args["max"]]}

    def script_find(self, args: dict) -> dict:
        el = self.find(args["text"])
        if el is None:
            return {"found": False}
        x, y, w, h = el.rect
        return {"found": True, "rect": {"x": x, "y": y, "w": w, "h": h}}

    def script_point(self, args: dict) -> dict:
        if (args.get("selector") or "").startswith("::bad"):
            return {"error": "selector"}
        el = self.by_selector(args["selector"]) if args.get("selector") else self.find(args["text"])
        if el is None:
            self.clear()
            return {"pointed": False}
        self.draw(el, args["label"], args["ttl"])
        return {"pointed": True}

    def script_unpoint(self, args: dict) -> dict:
        return {"removed": self.clear()}

    def script_probe(self, args: dict) -> dict:
        has = None if not args.get("text") else norm(args["text"]) in norm(self.text)
        return {"url": self.url, "title": self.title, "has": has, "ready": "complete"}

    def script_collect(self, args: dict) -> dict:
        return {"chunks": [self.text[: args["max"]], *self.inputs]}


@dataclass
class FakeTab:
    id: str
    page: FakePage
    server: object = None
    port: int = 0
    sockets: list = field(default_factory=list)
    stall: bool = False                              # does not answer Runtime.evaluate
    errors: list = field(default_factory=list)       # CDP error messages to answer the next evaluations with

    def record(self, host: str) -> dict:
        return {"id": self.id, "type": "page", "url": self.page.url, "title": self.page.title,
                "webSocketDebuggerUrl": f"ws://{host}:{self.port}/devtools/page/{self.id}"}


class FakeChrome:
    def __init__(self, page_factory=None):
        self.page_factory = page_factory or (lambda url: FakePage(url=url, title=url))
        self.tabs: dict[str, FakeTab] = {}          # newest first
        self.evaluated: list[tuple[str, str]] = []  # (tab id, expression) of every Runtime.evaluate
        self.commands: list[tuple[str, str]] = []   # (tab id, method) of every command
        self.opened: list[str] = []                 # the urls /json/new was asked for
        self.port = 0
        self.ws_host = "127.0.0.1"                  # what the list says a tab's websocket address is on
        self._count = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._http = None

    # -- running --

    def start(self) -> "FakeChrome":
        self._thread.start()
        self._http = self._sync(self._listen_http)
        self.port = self._http.sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        """The browser goes away: its port closes and every tab's websocket with it."""
        if self._http is None:
            return
        self._sync(self._shutdown)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._http = None

    def _sync(self, fn, *args):
        done = threading.Event()
        box = {}

        def run():
            task = asyncio.ensure_future(self._wrap(fn, *args))
            task.add_done_callback(lambda t: (box.update(task=t), done.set()))
        self._loop.call_soon_threadsafe(run)
        if not done.wait(10):
            raise TimeoutError("the stand-in's loop did not answer")
        return box["task"].result()

    @staticmethod
    async def _wrap(fn, *args):
        out = fn(*args)
        return await out if asyncio.iscoroutine(out) else out

    async def _listen_http(self):
        return await asyncio.start_server(self._http_conn, "127.0.0.1", 0)

    async def _shutdown(self) -> None:
        self._http.close()
        for tab in list(self.tabs.values()):
            await self._close_tab(tab)
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)

    # -- what a test changes --

    def add_tab(self, page: FakePage) -> str:
        return self._sync(self._add_tab, page)

    def navigate(self, tab_id: str, page: FakePage) -> None:
        self._sync(self.go, tab_id, page)

    def close_tab(self, tab_id: str) -> None:
        self._sync(self._drop_tab, tab_id)

    def press(self, tab_id: str, element: FakeElement) -> None:
        self._sync(element.on_press, tab_id)

    def page(self, tab_id: str) -> FakePage:
        return self.tabs[tab_id].page

    def overlay(self, tab_id: str) -> dict | None:
        tab = self.tabs.get(tab_id)
        return None if tab is None or tab.page.overlay is None else dict(tab.page.overlay)

    def tab(self, tab_id: str) -> FakeTab:
        return self.tabs[tab_id]

    async def _add_tab(self, page: FakePage) -> str:
        self._count += 1
        tab = FakeTab(id=f"T{self._count:03d}", page=page)
        page.loop = self._loop
        tab.server = await asyncio.start_server(lambda r, w: self._ws_conn(tab, r, w), "127.0.0.1", 0)
        tab.port = tab.server.sockets[0].getsockname()[1]
        self.tabs = {tab.id: tab, **self.tabs}
        return tab.id

    def go(self, tab_id: str, page: FakePage) -> None:
        """The tab shows another page (on the stand-in's loop: a press handler calls this)."""
        tab = self.tabs[tab_id]
        tab.page.clear()
        page.loop = self._loop
        tab.page = page
        event = json.dumps({"method": "Page.frameNavigated",
                            "params": {"frame": {"id": "main", "url": page.url}}})
        for sock in list(tab.sockets):
            asyncio.ensure_future(self._send(sock, event))

    @staticmethod
    async def _send(sock, event: str) -> None:
        try:
            await sock.send(event)
        except ws.WebSocketError:
            pass

    async def _drop_tab(self, tab_id: str) -> None:
        tab = self.tabs.get(tab_id)
        if tab is not None:
            await self._close_tab(tab)

    async def _close_tab(self, tab: FakeTab) -> None:
        self.tabs.pop(tab.id, None)
        tab.page.clear()
        tab.server.close()
        for sock in list(tab.sockets):
            await sock.close()

    # -- the HTTP side of the port --

    async def _http_conn(self, reader, writer) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            method, target, _ = head.split("\r\n")[0].split(" ", 2)
            path, _, query = target.partition("?")
            status, body = 200, ""
            if path == "/json/version":
                body = json.dumps({"Browser": "Fake/1"})
            elif path in ("/json", "/json/list"):
                body = json.dumps([t.record(self.ws_host) for t in self.tabs.values()])
            elif path == "/json/new" and method == "PUT":
                url = urllib.parse.unquote(query.split("&")[0])
                self.opened.append(url)
                tab_id = await self._add_tab(self.page_factory(url))
                body = json.dumps(self.tabs[tab_id].record(self.ws_host))
            elif path.startswith("/json/activate/"):
                body = "Target activated"
            elif path.startswith("/json/close/"):
                await self._drop_tab(path.rsplit("/", 1)[1])
                body = "Target is closing"
            else:
                status = 404
            data = body.encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                         + data)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    # -- a tab's websocket --

    async def _ws_conn(self, tab: FakeTab, reader, writer) -> None:
        try:
            sock = await ws.accept(reader, writer)
        except (ws.WebSocketError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        tab.sockets.append(sock)
        try:
            while True:
                msg = json.loads(await sock.recv())
                self.commands.append((tab.id, msg["method"]))
                reply = self._answer(tab, msg)
                if reply is not None:
                    await sock.send(json.dumps({"id": msg["id"], **reply}))
        except (ws.WebSocketError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if sock in tab.sockets:
                tab.sockets.remove(sock)
            writer.close()

    def _answer(self, tab: FakeTab, msg: dict) -> dict | None:
        if msg["method"] == "Page.enable":
            return {"result": {}}
        if msg["method"] != "Runtime.evaluate":
            return {"error": {"code": -32601, "message": f"'{msg['method']}' wasn't found"}}
        expression = msg["params"]["expression"]
        self.evaluated.append((tab.id, expression))
        if tab.stall:
            return None
        if tab.errors:
            return {"error": {"code": -32000, "message": tab.errors.pop(0)}}
        try:
            name, args, function = overlay.parse(expression)
            known = overlay.SCRIPTS.get(name) == function
        except (ValueError, TypeError, KeyError):
            known = False
        if not known:
            return {"result": {"exceptionDetails": {"text": "Uncaught"}, "result": {"type": "object"}}}
        value = getattr(tab.page, f"script_{name}")(args)
        return {"result": {"result": {"type": "object", "value": value}}}

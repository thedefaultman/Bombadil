"""A real headless Chromium for the browser service's tests, the pages it is given to show, and a second DevTools
client for the test to stand in for the person: it reads the page and presses with real mouse events, as a hand would,
so a press goes through whatever is drawn over the page.

`start()` raises `ChromiumUnavailable` with the reason when there is none or it will not start here; the tests skip
with that reason.
"""

import asyncio
import glob
import http.server
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from bombadil import browser, ws


class ChromiumUnavailable(Exception):
    pass


def chromium_path() -> str | None:
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome", "/opt/pw-browsers/chromium*/chrome-linux/chrome"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return shutil.which("chromium")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Chromium:
    """Headless Chromium on a temporary profile with its debugging port on `port`."""

    def __init__(self):
        self.port = free_port()
        self.devtools = browser.DevTools(port=self.port)
        self.profile = tempfile.mkdtemp(prefix="bombadil-chromium-")
        self.proc: subprocess.Popen | None = None

    def start(self) -> "Chromium":
        path = chromium_path()
        if path is None:
            raise ChromiumUnavailable("no Chromium is installed here")
        self.proc = subprocess.Popen(
            [path, f"--remote-debugging-port={self.port}", "--no-sandbox", "--headless=new", "--disable-gpu",
             "--no-first-run", "--no-proxy-server", "--window-size=1000,800", f"--user-data-dir={self.profile}",
             "about:blank"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise ChromiumUnavailable(f"Chromium exited at once (status {self.proc.returncode})")
            if self.devtools.up:
                return self
            time.sleep(0.1)
        self.stop()
        raise ChromiumUnavailable("Chromium did not open its debugging port in 30 seconds")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        shutil.rmtree(self.profile, ignore_errors=True)


class Pages:
    """A web server on localhost that serves `render(path, query)` -> (status, html)."""

    def __init__(self, render):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path, _, query = self.path.partition("?")
                outer.requests.append(self.path)
                status, html = outer.render(path, query)
                data = html.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.render = render
        self.requests: list[str] = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Cdp:
    """A blocking DevTools connection to one tab, for a test: evaluates in the page and moves the mouse."""

    def __init__(self, ws_url: str):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._sock = self._run(ws.connect(ws_url))
        self._next = 0

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(20)

    async def _call(self, method: str, params: dict) -> dict:
        self._next += 1
        await self._sock.send(json.dumps({"id": self._next, "method": method, "params": params}))
        while True:
            msg = json.loads(await self._sock.recv())
            if msg.get("id") == self._next:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def call(self, method: str, **params) -> dict:
        return self._run(self._call(method, params))

    def evaluate(self, js: str):
        out = self.call("Runtime.evaluate", expression=js, returnByValue=True)
        if "exceptionDetails" in out:
            raise RuntimeError(f"the page threw: {out['exceptionDetails'].get('text')}")
        return out["result"].get("value")

    def click(self, x: float, y: float) -> None:
        """A real click at a point of the window: the page gets it only if nothing drawn over it takes it."""
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def close(self) -> None:
        try:
            self._run(self._sock.close())
        except Exception:  # noqa: BLE001 - the browser may be gone already
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

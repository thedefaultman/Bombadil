"""bombadil-browserd: the read half of the browser service (docs/CONNECT.md, "The browser service").

One user service holds a connection to the panel's Chromium over its DevTools websocket and answers on
browserd.sock in JSON lines, the shape of connect.sock: {"id", "op", ...} gets {"id", "ok": true, "result"} or
{"id", "ok": false, "error": "a sentence", "code"}. It lets the Slack setup recipe (connect/slack_setup.py) open a
page, read it, point at a button the person is to press, wait for the next page, and take a token off a page into
the connection service. Each op is one `_op_<name>` here: status, open, tabs, read, find, point, unpoint, wait, take.

Why it is shaped this way:

- It has no hands. It never clicks, types or navigates a page by itself (`open` is the panel opening, through
  `browser.open_url`, so the panel slides in as it does for every link), and it never runs a script a caller
  supplied: callers pass data (a word, a selector, a regex), and the only code that reaches a page is the fixed
  set in overlay.py, with the data as a JSON literal. A request with an argument an op does not take is refused.
- Only the person's own processes may ask: a peer of another uid is closed at once (SO_PEERCRED), and the socket
  is 0600.
- A token goes one way. `take` runs the regex over the page's text and its fields' values and hands the first match
  straight to the connection service's `store_secret`. The value is never returned, logged, put in an exception
  text or kept after the call; the answer says only whether it was stored, and why not in a sentence. `read` hides
  anything shaped like a Slack token too, so no answer of this service carries one.
- A page's words are other people's words: `read` answers them with `untrusted: true`, made plain (no control or
  direction-changing characters), and nothing here acts on them.
- Chromium not running, the debugging port closed, a tab that is gone or a page that does not answer are a sentence
  and a code, never a crash. Codes: `bad_request`, `no_browser`, `no_tab`, `not_found`, `page`, `internal`.
- The HTTP side of the debugging port stays in browser.py (`DevTools`); this module takes the list of tabs from
  it and holds one websocket per tab it works on (cdp.py).
"""

import asyncio
import contextlib
import fcntl
import json
import math
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .. import browser, paths, ws
from ..connect import client as connect_client
from ..connect import protocol
from ..connect.protocol import Refusal, answer_error, answer_ok, encode, one_line, plain_text
from . import cdp, overlay

BAD_REQUEST = "bad_request"
NO_BROWSER = "no_browser"
NO_TAB = "no_tab"
NOT_FOUND = "not_found"
PAGE = "page"
INTERNAL = "internal"

NOT_RUNNING = "The browser is not running."
GONE = "That page is gone."
NO_PAGE = "There is no page open."
NOT_ANSWERING = "The page did not answer."
UNREADABLE = "The page could not be read just now."
NOTHING = "Nothing on the page looks like that."

OPS = ("status", "open", "tabs", "read", "find", "point", "unpoint", "wait", "take")
# Every argument an op takes. A request with another is refused: callers pass data the service knows what to do with.
ARGS = {"status": (), "open": ("url",), "tabs": (), "read": ("tab", "selector"), "find": ("tab", "text"),
        "point": ("tab", "text", "selector", "label", "ttl"), "unpoint": ("tab",),
        "wait": ("tab", "url", "text", "timeout"), "take": ("tab", "pattern", "into")}

LINE_LIMIT = 1 << 20
MAX_CLIENTS = 16
IDLE_S = 600.0
DRAIN_S = 10.0
POLL_S = 0.3               # `wait` looks at the page this often
WAIT_DEFAULT_S = 30.0
WAIT_MAX_S = 900.0
TTL_DEFAULT_S = 20.0
TTL_MIN_S, TTL_MAX_S = 0.5, 300.0
SESSIONS_MAX = 8
MAX_URL = 8000             # the Slack manifest address is about a thousand; a page's own can be long
MAX_NEEDLE = 200
MAX_SELECTOR = 200
MAX_LABEL = 60
MAX_PATTERN = 300
MAX_SECRET = 1000          # a match longer than this is not a token
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

_TOKEN_SHAPE = re.compile(r"\bx(?:app|ox[a-z])-[A-Za-z0-9-]{8,}")
_TAB_ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_NAME = re.compile(r"[a-z][a-z_]{0,39}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def log(text) -> None:
    print(f"bombadil-browserd: {' '.join(str(text).split())}"[:300], file=sys.stderr, flush=True)


def peer_uid(sock) -> int:
    """The uid of the process on the other end of a unix socket, -1 when the kernel cannot say."""
    try:
        _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                              struct.calcsize("3i")))
    except (OSError, AttributeError, struct.error):
        return -1
    return uid


class AlreadyRunning(Exception):
    pass


# -- what a caller may ask: data, checked --

def _tab_id(req) -> str | None:
    tab = req.get("tab")
    if tab is None:
        return None
    if not isinstance(tab, str) or not _TAB_ID.fullmatch(tab):
        raise Refusal(BAD_REQUEST, "That is not a page I know.")
    return tab


def _words(req, key: str, limit: int, what: str, required: bool = True) -> str | None:
    value = req.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
        raise Refusal(BAD_REQUEST, f"That needs {what}.")
    return value.strip()


def _selector(req) -> str | None:
    """Plain CSS, as `querySelector` takes it: short, one line, and nothing that is a script address."""
    value = req.get("selector")
    if value is None:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_SELECTOR or _CONTROL.search(value)
            or "javascript:" in re.sub(r"[\s\\]", "", value).lower()):
        raise Refusal(BAD_REQUEST, "That selector is not plain CSS.")
    return value.strip()


def _regex(value, what: str) -> re.Pattern:
    if not isinstance(value, str) or not value or len(value) > MAX_PATTERN:
        raise Refusal(BAD_REQUEST, f"That needs {what}.")
    try:
        return re.compile(value)
    except re.error:
        raise Refusal(BAD_REQUEST, f"That is not a pattern I can use for {what}.") from None


def _seconds(req, key: str, default: float, low: float, high: float) -> float:
    value = req.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise Refusal(BAD_REQUEST, f"{key} must be between {low:g} and {high:g} seconds.")
    return float(value)


def _web_url(value) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_URL or re.search(r"\s", value) \
            or _CONTROL.search(value):
        raise Refusal(BAD_REQUEST, "That is not a web address I can open.")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise Refusal(BAD_REQUEST, "That is not a web address I can open.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refusal(BAD_REQUEST, "That is not a web address I can open.")
    return value


def hide_tokens(text: str) -> str:
    return _TOKEN_SHAPE.sub("[token hidden]", text)


class Service:
    """`devtools`: browser.DevTools of the panel's debugging port. `connect`: the module that speaks to the connection
    service (`request`); `opener`: how a page is opened (`browser.open_url`). Tests give their own of each."""

    def __init__(self, devtools: browser.DevTools | None = None, connect=None, opener=None,
                 socket_path: Path | None = None):
        self.devtools = devtools or browser.DevTools()
        self.connect = connect or connect_client
        self.opener = opener or browser.open_url
        self.socket_path = Path(socket_path) if socket_path else paths.browserd_socket()
        self.sessions: dict[str, cdp.Session] = {}
        self.current: str | None = None       # the tab `open` last made: what a request with no `tab` means
        self.clients: set[asyncio.Task] = set()
        self.listening = None                 # a threading.Event a test can wait on, set when the socket answers
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task] = set()
        self._connecting = asyncio.Lock()
        self._stopping = asyncio.Event()
        self._lock_fd: int | None = None

    # -- running --

    async def serve(self) -> None:
        self._take_lock()
        server = None
        try:
            server = await self._listen()
            if self.listening is not None:
                self.listening.set()
            await self._stopping.wait()
        finally:
            await self._close(server)

    def stop(self) -> None:
        """Close cleanly (SIGTERM). Call on the loop, or through call_soon_threadsafe."""
        self._stopping.set()

    def _take_lock(self) -> None:
        """One service per socket: a second would take the socket from the first, which would go on holding the
        browser's connections with nobody able to reach it."""
        path = Path(f"{self.socket_path}.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AlreadyRunning(f"another bombadil-browserd is already answering on {self.socket_path}") from None
        self._lock_fd = fd

    async def _listen(self):
        path = self.socket_path
        if path.is_socket() or path.is_symlink():
            path.unlink()   # left by a service that died: the lock says none is running now
        old = os.umask(0o177)   # made private, not made and then made private
        try:
            server = await asyncio.start_unix_server(self._client, path=str(path), limit=LINE_LIMIT)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        return server

    async def _close(self, server) -> None:
        if server is not None:
            server.close()
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        for task in [*self.clients, *self._tasks]:
            task.cancel()
        await asyncio.gather(*self.clients, *self._tasks, return_exceptions=True)
        for session in list(self.sessions.values()):
            await session.close()
        self.sessions.clear()
        with contextlib.suppress(OSError):
            self.socket_path.unlink()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- clients --

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.clients) >= MAX_CLIENTS or peer_uid(writer.get_extra_info("socket")) != os.getuid():
            writer.close()
            return
        me = asyncio.current_task()
        self.clients.add(me)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), IDLE_S)
                except ValueError:
                    writer.write(encode(answer_error(None, "That request is too long.", BAD_REQUEST)))
                    break
                except (OSError, TimeoutError):
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                writer.write(encode(await self.answer(line)))
                try:
                    await asyncio.wait_for(writer.drain(), DRAIN_S)
                except (OSError, TimeoutError):
                    break
        finally:
            self.clients.discard(me)
            writer.close()

    async def answer(self, raw: bytes | str) -> dict:
        """One request line -> its answer. Whatever goes wrong is a sentence and a code, never a dead connection."""
        try:
            req = json.loads(raw)
        except ValueError:
            req = None
        if not isinstance(req, dict):
            return answer_error(None, "That was not a request the browser service understands.", BAD_REQUEST)
        rid, op = req.get("id"), req.get("op")
        if not isinstance(op, str) or op not in OPS:
            return answer_error(rid, "The browser service cannot do that.", BAD_REQUEST)
        if set(req) - {"id", "op", *ARGS[op]}:
            return answer_error(rid, "That request has something the browser service does not take.", BAD_REQUEST)
        try:
            return answer_ok(rid, await getattr(self, f"_op_{op}")(req))
        except Refusal as e:
            return answer_error(rid, str(e), e.code)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one bad request never costs the connection; the kind only, never the text
            log(f"{op}: {type(e).__name__}")
            return answer_error(rid, "The browser service could not do that just now.", INTERNAL)

    # -- the browser --

    async def _tabs(self) -> list[dict]:
        try:
            tabs = await asyncio.to_thread(self.devtools.tabs)
        except browser.DOWN:
            raise Refusal(NO_BROWSER, NOT_RUNNING) from None
        tabs = [t for t in tabs if isinstance(t.get("id"), str)]
        ids = {t["id"] for t in tabs}
        for tab_id in [k for k in self.sessions if k not in ids]:
            self._spawn(self._drop(tab_id))
        return tabs

    async def _target(self, req) -> dict:
        """The tab a request is about: the one it names, else the one `open` made last, else the first."""
        tabs = await self._tabs()
        wanted = _tab_id(req)
        if wanted is not None:
            tab = next((t for t in tabs if t["id"] == wanted), None)
            if tab is None:
                raise Refusal(NO_TAB, GONE)
            return tab
        tab = next((t for t in tabs if t["id"] == self.current), None) or (tabs[0] if tabs else None)
        if tab is None:
            raise Refusal(NO_TAB, NO_PAGE)
        return tab

    async def _session(self, tab: dict) -> cdp.Session:
        """The tab's lasting connection, made when first needed. Only a debugging address on this computer is used."""
        session = self.sessions.get(tab["id"])
        if session is not None and not session.closed:
            return session
        address = tab.get("webSocketDebuggerUrl")
        try:
            local = isinstance(address, str) and urlsplit(address).hostname in LOCAL_HOSTS
        except ValueError:
            local = False
        if not local:
            raise Refusal(NO_TAB, "That page cannot be reached.")
        async with self._connecting:
            session = self.sessions.get(tab["id"])
            if session is not None and not session.closed:
                return session
            if len(self.sessions) >= SESSIONS_MAX:
                await self._drop(next(iter(self.sessions)))
            try:
                session = await cdp.Session.open(tab["id"], address, self._event)
            except (cdp.CdpError, OSError, ValueError, ws.WebSocketError):
                raise Refusal(NO_TAB, "That page cannot be reached just now.") from None
            self.sessions[tab["id"]] = session
            return session

    async def _drop(self, tab_id: str) -> None:
        self._disarm(tab_id)
        session = self.sessions.pop(tab_id, None)
        if session is not None:
            await session.close()
        if self.current == tab_id:
            self.current = None

    def _event(self, session: cdp.Session, method: str, params: dict) -> None:
        # The page is replaced: what was drawn on it went with it, and its timer has nothing to take away.
        if method == "Page.frameNavigated" and not (params.get("frame") or {}).get("parentId"):
            self._disarm(session.tab_id)

    async def _run(self, session: cdp.Session, name: str, args: dict) -> dict:
        """One of the fixed scripts on one page, asked again if the page was being replaced under it."""
        expr = overlay.expression(name, args)
        for attempt in range(3):
            try:
                value = await session.evaluate(expr)
                break
            except cdp.Gone:
                self.sessions.pop(session.tab_id, None)
                raise Refusal(NO_TAB, GONE) from None
            except cdp.Timeout:
                raise Refusal(PAGE, NOT_ANSWERING) from None
            except cdp.Failed as e:
                if e.transient and attempt < 2:
                    await asyncio.sleep(0.15)
                    continue
                raise Refusal(PAGE, UNREADABLE) from None
        if not isinstance(value, dict):
            raise Refusal(PAGE, UNREADABLE)
        return value

    def _arm(self, tab_id: str, ttl: float) -> None:
        """The pointer takes itself away at its time (the page's own timer); this is for a page whose timers are
        held back, a hidden tab for one."""
        self._disarm(tab_id)
        self._timers[tab_id] = asyncio.get_running_loop().call_later(ttl + 1.0, self._spawn_clear, tab_id)

    def _disarm(self, tab_id: str) -> None:
        handle = self._timers.pop(tab_id, None)
        if handle is not None:
            handle.cancel()

    def _spawn_clear(self, tab_id: str) -> None:
        self._timers.pop(tab_id, None)
        self._spawn(self._clear(tab_id))

    async def _clear(self, tab_id: str) -> None:
        session = self.sessions.get(tab_id)
        if session is not None and not session.closed:
            with contextlib.suppress(cdp.CdpError):
                await session.evaluate(overlay.expression("unpoint", {"id": overlay.POINTER_ID}), timeout=3.0)

    # -- ops --

    async def _op_status(self, req) -> dict:
        up = await asyncio.to_thread(lambda: self.devtools.up)
        if not up:
            return {"up": False, "tabs": 0}
        try:
            return {"up": True, "tabs": len(await self._tabs())}
        except Refusal:
            return {"up": False, "tabs": 0}

    async def _op_open(self, req) -> dict:
        url = _web_url(req.get("url"))
        try:
            tab = await asyncio.to_thread(self.opener, url, devtools=self.devtools)
        except RuntimeError as e:
            sentence = "The browser is not installed on this computer." if "not installed" in str(e) \
                else "The browser would not open the page."
            raise Refusal(NO_BROWSER, sentence) from None
        except (OSError, subprocess.SubprocessError):
            raise Refusal(NO_BROWSER, "The browser would not open the page.") from None
        if not isinstance(tab, dict) or not isinstance(tab.get("id"), str):
            # the debugging port could not say which tab it is: look for it
            tabs = await self._tabs()
            tab = next((t for t in tabs if browser.same_url(t.get("url", ""), url)), None) or (tabs[0] if tabs else None)
            if tab is None:
                raise Refusal(NO_TAB, "The page did not open.")
        self.current = tab["id"]
        return {"tab": {"id": tab["id"], "url": one_line(tab.get("url") or url, protocol.MAX_URL)}}

    async def _op_tabs(self, req) -> dict:
        return {"tabs": [{"id": t["id"], "url": one_line(t.get("url"), protocol.MAX_URL),
                          "title": one_line(t.get("title"), 200)} for t in await self._tabs()]}

    async def _op_read(self, req) -> dict:
        selector = _selector(req)
        session = await self._session(await self._target(req))
        value = await self._run(session, "read", {"selector": selector, "max": overlay.MAX_TEXT})
        if value.get("error"):
            raise Refusal(BAD_REQUEST, "That selector is not valid CSS.")
        if not value.get("found"):
            raise Refusal(NOT_FOUND, "Nothing on the page matches that.")
        text = hide_tokens(str(value.get("text") or ""))
        return {"url": one_line(value.get("url"), protocol.MAX_URL), "title": one_line(value.get("title"), 200),
                "text": plain_text(text, overlay.MAX_TEXT), "untrusted": True}

    async def _op_find(self, req) -> dict:
        needle = _words(req, "text", MAX_NEEDLE, "some words to look for")
        session = await self._session(await self._target(req))
        value = await self._run(session, "find", {"text": needle})
        rect = value.get("rect")
        if value.get("found") is not True or not isinstance(rect, dict):
            return {"found": False, "rect": None}
        return {"found": True, "rect": {k: round(float(rect.get(k) or 0), 1) for k in ("x", "y", "w", "h")}}

    async def _op_point(self, req) -> dict:
        text = _words(req, "text", MAX_NEEDLE, "some words to point at", required=False)
        selector = _selector(req)
        if (text is None) == (selector is None):
            raise Refusal(BAD_REQUEST, "Say what to point at: words on the page, or a selector, not both.")
        label = one_line(_words(req, "label", MAX_LABEL, "a short label"), MAX_LABEL)
        ttl = _seconds(req, "ttl", TTL_DEFAULT_S, TTL_MIN_S, TTL_MAX_S)
        tab = await self._target(req)
        session = await self._session(tab)
        value = await self._run(session, "point", {"text": text, "selector": selector, "label": label,
                                                   "ttl": int(ttl * 1000), "id": overlay.POINTER_ID,
                                                   "ring": overlay.RING})
        if value.get("error"):
            raise Refusal(BAD_REQUEST, "That selector is not valid CSS.")
        if value.get("pointed") is True:
            self._arm(tab["id"], ttl)
            return {"pointed": True}
        self._disarm(tab["id"])
        return {"pointed": False}

    async def _op_unpoint(self, req) -> dict:
        tab = await self._target(req)
        session = await self._session(tab)
        self._disarm(tab["id"])
        await self._run(session, "unpoint", {"id": overlay.POINTER_ID})
        return {}

    async def _op_wait(self, req) -> dict:
        pattern = _regex(req["url"], "the address to wait for") if req.get("url") is not None else None
        text = _words(req, "text", MAX_NEEDLE, "some words to wait for", required=False)
        if pattern is None and text is None:
            raise Refusal(BAD_REQUEST, "Say what to wait for: an address or some words on the page.")
        timeout = _seconds(req, "timeout", WAIT_DEFAULT_S, 0.0, WAIT_MAX_S)
        deadline = time.monotonic() + timeout
        session = await self._session(await self._target(req))
        url = ""
        while True:
            try:
                value = await self._run(session, "probe", {"text": text})
            except Refusal as e:
                if e.code != PAGE:   # a page mid-navigation is not an answer yet; a page that is gone is
                    raise
                value = {}
            url = one_line(value.get("url"), protocol.MAX_URL) if value else url
            if value and (pattern is None or pattern.search(url)) and (text is None or value.get("has") is True):
                return {"matched": True, "url": url}
            left = deadline - time.monotonic()
            if left <= 0:
                return {"matched": False, "url": url}
            await asyncio.sleep(min(POLL_S, left))

    async def _op_take(self, req) -> dict:
        pattern = _regex(req.get("pattern"), "what to take")
        into = req.get("into")
        if (not isinstance(into, dict) or set(into) != {"connection", "name"}
                or not protocol.valid_connection_id(into["connection"])
                or not isinstance(into["name"], str) or not _NAME.fullmatch(into["name"])):
            raise Refusal(BAD_REQUEST, "That needs a connection and the name of the secret to put in it.")
        session = await self._session(await self._target(req))
        page = await self._run(session, "collect", {"max": overlay.MAX_TEXT, "fields": overlay.MAX_FIELDS,
                                                    "field_max": overlay.MAX_FIELD})
        value = None
        for chunk in page.get("chunks") or []:
            match = pattern.search(chunk) if isinstance(chunk, str) else None
            if match and match.group(0):
                value = match.group(0)
                break
        if value is None or len(value) > MAX_SECRET:
            return {"stored": False, "found": False, "why": NOTHING}
        try:
            await asyncio.to_thread(self.connect.request, "store_secret", 10.0, connection=into["connection"],
                                    name=into["name"], value=value)
        except connect_client.ConnectUnavailable as e:
            return {"stored": False, "found": True, "why": str(e)}
        except connect_client.ConnectError as e:
            sentence = str(e)
            return {"stored": False, "found": True,
                    "why": "Connections would not take it." if value in sentence else sentence}
        except Exception as e:  # noqa: BLE001 - the kind only: what was being stored must not reach a log
            log(f"take: {type(e).__name__}")
            return {"stored": False, "found": True, "why": "Connections could not take it just now."}
        return {"stored": True}


def _port(value: str | None) -> int:
    try:
        port = int(value or "")
    except ValueError:
        return browser.DEBUG_PORT
    return port if 0 < port < 65536 else browser.DEBUG_PORT


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(__doc__.split("\n\n", 1)[0])
        return 0 if argv[0] in ("-h", "--help") else 2
    service = Service(devtools=browser.DevTools(port=_port(os.environ.get("BOMBADIL_DEVTOOLS_PORT"))))

    async def run():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, service.stop)
        await service.serve()

    log(f"answering on {service.socket_path}, the browser's debugging port is {service.devtools.base}")
    try:
        asyncio.run(run())
    except AlreadyRunning as e:
        log(str(e))
        return 1
    except OSError as e:
        log(f"cannot start: {e}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

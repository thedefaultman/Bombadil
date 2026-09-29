"""Signing in to the provider, natively: the CLI's own login, run by agentd, with its sign-in
page in the browser panel.

Both CLIs sign in with OAuth in a browser. Each starts a small server on localhost for the
sign-in page to come back to, and hands the page's URL to $BROWSER. For the login it runs,
agentd makes $BROWSER bombadil-browser, which passes the URL back to agentd, which opens it
in the browser panel: the page returns to the CLI on this same machine and the CLI saves its
own credentials, so there is nothing to copy. A URL the CLI only prints is opened too, after
a moment's wait for $BROWSER. When the page it leads to ends on a code for the CLI's "paste
the code" prompt (Claude's manual flow), the code is in that tab's address: the browser's
debugging port lists every tab's URL, and the code is typed into the CLI's terminal.

`SignIn` is one run of a CLI's login in a pseudo-terminal, from start to signed in, failed,
timed out or cancelled. It says what is happening through `on_change`; agentd turns that into
the line above the pill. Everything that touches the browser goes through `browser.Panel`,
so tests drive it without one.
"""

import asyncio
import codecs
import fcntl
import os
import pty
import re
import secrets
import signal
import socket
import struct
import sys
import termios
import time
import urllib.parse
from collections.abc import Callable

from . import browser

TIMEOUT = float(os.environ.get("BOMBADIL_SIGNIN_TIMEOUT") or 600)   # seconds on the sign-in page
URL_GRACE = 2.0   # a printed URL waits this long for the CLI to hand one to $BROWSER instead
POLL = 1.0        # how often the tab and the panel are looked at
COLUMNS = 1000    # wide enough that no sign-in URL is wrapped by the CLI

_CSI = r"\x1b\[[0-?]*[ -/]*[@-~]"
_OSC = r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
ANSI = re.compile(f"{_CSI}|{_OSC}|\x1b[@-Z\\\\-_]|\x1b[()][0-9A-Za-z]")
OSC8 = re.compile(r"\x1b\]8;[^;\x07\x1b]*;([^\x07\x1b]*)(?:\x07|\x1b\\)")
# Box-drawing and block characters end a URL too: a TUI may draw one right after it.
URL = re.compile(r"https?://[^\s\"'<>\x00-\x1f\x7f─-▟]+")


def clean(raw: str) -> str:
    """Terminal output as plain text: escape sequences out, carriage returns as new lines."""
    return ANSI.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")


def links(raw: str) -> list[str]:
    """The targets of the terminal hyperlinks (OSC 8) in raw output: a TUI may print a short
    label and keep the URL there."""
    return [u for u in OSC8.findall(raw) if u.startswith(("http://", "https://"))]


def urls(text: str) -> list[str]:
    """The complete URLs in `text`, in order, once each. One still being written (at the very
    end, with nothing after it yet) is left for the next read."""
    out = []
    for m in URL.finditer(text):
        if m.end() == len(text):
            continue
        u = m.group(0).rstrip(".,;:)]}")
        if u not in out:
            out.append(u)
    return out


PROMPT = re.compile(r"[^>\n]*\b(?:paste|enter)\b[^>\n]*>\s?", re.I)


def last_words(text: str, n: int = 1) -> str:
    """What the CLI said last, for a failure line: no URLs, prompts or spinner debris."""
    lines = []
    for line in PROMPT.sub("", text).splitlines():
        s = " ".join(line.split())
        if not s or URL.search(s) or len(s) < 3 or not re.search(r"[A-Za-z]{3}", s):
            continue
        if lines and lines[-1] == s:
            continue
        lines.append(s)
    return " ".join(lines[-n:])[:200]


def reachable(host: str, port: int = 443, timeout: float = 4.0) -> bool:
    """Can this machine reach the sign-in server? A name that does not resolve counts as no."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


class SignIn:
    """One run of a provider CLI's login.

    phase: starting -> waiting (the page is open) -> finishing (the page came back, or a code
    was typed in) -> done | failed | timeout | cancelled. While waiting, `view` says whether the
    page is on screen: shown, hidden (the panel slid out) or closed (its tab or the browser).
    """

    def __init__(self, provider, on_change: Callable[["SignIn"], None] | None = None, *,
                 panel=None, env: dict | None = None, timeout: float | None = None,
                 grace: float = URL_GRACE, poll: float = POLL):
        self.provider = provider
        self.on_change = on_change or (lambda s: None)
        self.panel = panel or browser.Panel()
        self.id = secrets.token_hex(6)
        self.env = env or {}
        self.timeout = TIMEOUT if timeout is None else timeout
        self.grace = grace
        self.poll = poll
        self.phase = "starting"
        self.view = "shown"
        self.reason = ""           # why it failed, in the CLI's words
        self.hiccup = ""           # the last trouble with the browser (the reason, if the CLI said nothing)
        self.url: str | None = None   # the sign-in page, as opened
        self.tab: str | None = None   # its tab in the browser, when the debugging port said
        self.text = ""             # what the CLI printed, plain
        self.proc: asyncio.subprocess.Process | None = None
        self._raw = ""
        self._handed: list[str] = []            # URLs the CLI gave $BROWSER
        self._printed: dict[str, float] = {}    # URLs it printed, and when
        self._pasted: set[str] = set()
        self._master: int | None = None
        self._ended = asyncio.Event()
        self._wake = asyncio.Event()
        self._eof = asyncio.Event()
        self._cancelled = False
        self._state: str | None = None          # the page's OAuth state: a code must carry it
        self._opening: asyncio.Future | None = None
        self._lost = False                      # the page failed to open and no tab shows it

    # -- what agentd tells it --

    def browser_url(self, url: str) -> None:
        """The CLI handed `url` to $BROWSER (bombadil-browser passed it on)."""
        if url not in self._handed:
            self._handed.append(url)
        self._wake.set()

    def cancel(self) -> None:
        self._cancelled = True
        self._ended.set()

    async def show(self) -> None:
        """Put the sign-in page back on screen: slide the panel in, or open the page again in a
        new tab when its tab (or the whole browser) was closed. The CLI is still waiting for it."""
        if self.url is None or self.phase not in ("waiting", "finishing"):
            return
        if self.view == "closed":
            await self._open(self.url)
            self._lost = False
        else:
            await asyncio.to_thread(self.panel.show)
        self._set(view="shown")

    @property
    def running(self) -> bool:
        return self.phase in ("starting", "waiting", "finishing")

    # -- the run --

    async def run(self) -> str:
        """Run the login to its end; returns the final phase."""
        try:
            self.proc = await self._spawn()
        except OSError as e:
            self.reason = f"{self.provider.binary} did not start ({e.strerror or e})"
            self._set("failed")
            return self.phase
        loop = asyncio.get_running_loop()
        loop.add_reader(self._master, self._on_readable)
        watcher = asyncio.create_task(self._watch())
        exited = asyncio.create_task(self.proc.wait())
        ended = asyncio.create_task(self._ended.wait())
        phase = "failed"
        try:
            done, _ = await asyncio.wait({exited, ended}, timeout=self.timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if exited in done:
                try:   # what it said last, before it closed the terminal
                    await asyncio.wait_for(self._eof.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                if self._cancelled:
                    phase = "cancelled"
                elif self.proc.returncode == 0:
                    phase = await self._confirm()
                else:
                    phase = "failed"
                    self.reason = self.provider.signin_error(self.text) or self.hiccup or \
                        f"{self.provider.binary} exited with {self.proc.returncode}"
            elif ended in done:
                phase = "cancelled"
            else:
                phase = "timeout"
        finally:
            watcher.cancel()
            ended.cancel()
            await self._end_process()
            exited.cancel()
            self._close_terminal()
        await self._settle()
        if phase in ("done", "cancelled", "timeout"):
            # Finished or given up: the page slides back out, and its tab goes if others remain.
            await asyncio.to_thread(self._put_away)
        self._set(phase)
        return phase

    async def _confirm(self) -> str:
        """The CLI said it is done; make sure it really is signed in now."""
        try:
            ok = await asyncio.to_thread(self.provider.signed_in)
        except Exception:  # noqa: BLE001 - it said it worked; believe it
            ok = None
        if ok is False:
            # Codex says "Successfully logged in" for a stray visit to its success page too.
            self.reason = "it finished without signing in"
            return "failed"
        return "done"

    async def _spawn(self) -> asyncio.subprocess.Process:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, COLUMNS, 0, 0))
        env = {**os.environ, **self.env, "TERM": "xterm-256color", "COLUMNS": str(COLUMNS), "LINES": "50",
               "BOMBADIL_SIGNIN": self.id}

        def controlling_terminal():   # runs in the child, after setsid: the pty becomes its terminal
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.provider.signin_command(), stdin=slave, stdout=slave, stderr=slave, env=env,
                cwd=os.path.expanduser("~"), start_new_session=True, preexec_fn=controlling_terminal)
        except OSError:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._master = master
        return proc

    def _on_readable(self):
        try:
            data = os.read(self._master, 65536)
        except OSError:
            data = b""
        if not data:   # the CLI closed its terminal (it exited)
            asyncio.get_running_loop().remove_reader(self._master)
            self._eof.set()
            return
        if not hasattr(self, "_decoder"):
            self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._raw = (self._raw + self._decoder.decode(data))[-200_000:]
        self.text = clean(self._raw)
        now = time.monotonic()
        for u in urls(self.text) + links(self._raw):
            if u not in self._printed and self.provider.signin_url_kind(u):
                self._printed[u] = now
                self._wake.set()

    def type(self, text: str) -> None:
        """Type into the CLI's terminal, as if at its prompt."""
        if self._master is not None:
            os.write(self._master, text.encode())

    async def _watch(self):
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                if self.url is None:
                    await self._maybe_open()
                elif self.phase in ("waiting", "finishing"):
                    await self._look()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - a browser hiccup never ends the sign-in
                self._trouble("watching the page", e)

    def _pick(self) -> str | None:
        """The page to open: one handed to $BROWSER at once; a printed one once the CLI had its
        moment to hand one over, preferring a page that returns to the CLI by itself."""
        if self._handed:
            return self._handed[0]
        ripe = [u for u, t in self._printed.items() if time.monotonic() - t >= self.grace]
        if not ripe:
            return None
        ripe.sort(key=lambda u: self.provider.signin_url_kind(u) != "auto")
        return ripe[0]

    def _trouble(self, doing: str, e: Exception) -> None:
        self.hiccup = f"{type(e).__name__}: {e}"
        print(f"signin: {doing}: {self.hiccup}", file=sys.stderr, flush=True)

    async def _open(self, url: str):
        """Open `url` in the browser panel. The thread notes the tab itself, so a run that is
        called off while the browser is still opening it knows what to put away."""
        def work():
            tab = self.panel.open(url)
            if isinstance(tab, dict):
                self.tab = tab.get("id")
            return tab
        fut = self._opening = asyncio.ensure_future(asyncio.to_thread(work))
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())   # (seen: the awaiter raises it)
        return await asyncio.shield(fut)

    async def _settle(self):
        """Wait for a page that is still being opened (a slow browser), so it can be put away."""
        fut = self._opening
        if fut is not None and not fut.done():
            try:
                await asyncio.wait_for(asyncio.shield(fut), 30)
            except Exception:  # noqa: BLE001 - it is over either way
                pass

    async def _maybe_open(self):
        url = self._pick()
        if url is None:
            return
        self.url = url
        self._state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)).get("state")
        view = "shown"
        try:
            await self._open(url)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the panel would not slide in, or the browser was slow
            # The page may be open anyway. The sign-in waits for it in any case (the CLI does):
            # `_look` tells the pill whether it is shown, hidden or gone, and it can be opened again.
            self._trouble("opening the page", e)
            tabs = await asyncio.to_thread(self.panel.tabs) or []
            mine = next((t for t in tabs if browser.same_url(t.get("url", ""), url)), None)
            if mine is not None:
                self.tab = mine.get("id")
            else:
                view, self._lost = "closed", True
        self._set("waiting", view=view)

    async def _look(self):
        tabs = await asyncio.to_thread(self.panel.tabs)
        if tabs is not None and self.tab is None and self.url is not None:
            mine = next((t for t in tabs if browser.same_url(t.get("url", ""), self.url)), None)
            if mine is not None:   # (it opened after all, slowly)
                self.tab, self._lost = mine.get("id"), False
        if tabs is not None:
            for t in tabs:
                code = self.provider.code_from_url(t.get("url", ""))
                if code and code not in self._pasted and self._is_mine(code):
                    # The page ended on a code for the CLI's prompt: type it in for the user.
                    self._pasted.add(code)
                    self.type(code + "\r")
                    self._set("finishing")
            mine = next((t for t in tabs if t.get("id") == self.tab), None) if self.tab else None
            back = mine.get("url", "") if mine is not None else ""
            if local(back) and not self.provider.signin_url_kind(back) and self.phase == "waiting":
                self._set("finishing")   # the page came back to the CLI's own server
        if self.phase != "waiting":
            return
        if tabs is not None and (not tabs or self._lost
                                 or (self.tab and not any(t.get("id") == self.tab for t in tabs))):
            view = "closed"
        else:
            shown = await asyncio.to_thread(self.panel.shown)
            view = "hidden" if shown is False else "shown"
        self._set(view=view)

    def _is_mine(self, code: str) -> bool:
        """A code from this sign-in's page (its state), not a leftover tab's, and typeable."""
        return code.isprintable() and (not self._state or code.endswith("#" + self._state))

    def _set(self, phase: str | None = None, view: str | None = None):
        changed = False
        if phase is not None and phase != self.phase:
            self.phase, changed = phase, True
        if view is not None and view != self.view:
            self.view, changed = view, True
        if changed:
            try:
                self.on_change(self)
            except Exception:  # noqa: BLE001 - the listener's trouble is not the sign-in's
                pass

    async def _end_process(self):
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), wait)
                return
            except asyncio.TimeoutError:
                continue

    def _close_terminal(self):
        if self._master is None:
            return
        try:
            asyncio.get_running_loop().remove_reader(self._master)
        except (ValueError, RuntimeError):
            pass
        try:
            os.close(self._master)
        except OSError:
            pass
        self._master = None

    def _put_away(self):
        try:
            if self.tab:
                self.panel.close_tab(self.tab)
            if self.url is not None:
                self.panel.hide()
        except Exception:  # noqa: BLE001 - tidying up; the sign-in itself is decided
            pass

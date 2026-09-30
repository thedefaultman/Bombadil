"""Signing in: a provider CLI's login in a terminal, its page in a (stand-in) browser panel.

fake_signin.py plays the CLI: a localhost server as the provider's sign-in page, the URL for
$BROWSER, a printed URL with a paste prompt. FakePanel plays the browser: it loads a page,
follows its redirects as a browser would, and keeps each tab's address.
"""

import asyncio
import re
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from bombadil import providers, signin


class FakePanel:
    def __init__(self, follow=True):
        self.tabs_ = []
        self.visible = False
        self.follow = follow
        self.opened = []
        self.closed = []
        self.running = True
        self.fail_opens = 0   # the next opens raise, without opening anything
        self.delay = 0.0      # a slow browser
        self.unnamed = 0      # the next opens work but cannot say which tab they made
        self.close_raises = False
        self.abandoned = 0    # opens that stopped short because the sign-in was over
        self._n = 0

    def open(self, url, abandon=None):
        if self.delay:
            time.sleep(self.delay)
        if abandon is not None and abandon.is_set():
            self.abandoned += 1
            return None
        if self.fail_opens:
            self.fail_opens -= 1
            raise RuntimeError("hyprctl dispatch: didn't respond in time")
        self._n += 1
        tab = {"id": f"t{self._n}", "url": url, "type": "page"}
        self.tabs_.insert(0, tab)
        self.opened.append(url)
        self.visible = self.running = True
        if self.follow:
            threading.Thread(target=self._browse, args=(tab,), daemon=True).start()
        if self.unnamed:
            self.unnamed -= 1
            return None
        return {"id": tab["id"], "url": url}

    def _browse(self, tab):
        url = tab["url"]
        for _ in range(5):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as r:
                    tab["url"] = r.geturl()
                    html = r.read().decode(errors="replace")
            except OSError:
                return
            m = re.search(r"content='[\d.]+;url=([^']+)'", html)
            if not m:
                return
            url = m.group(1)
            tab["url"] = url

    def tabs(self):
        return [dict(t) for t in self.tabs_] if self.running else []

    def shown(self):
        return self.visible

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def close_tab(self, tab_id):
        if self.close_raises:
            raise OSError("the debugging port did not answer")
        self.closed.append(tab_id)
        if len(self.tabs_) > 1:
            self.tabs_ = [t for t in self.tabs_ if t["id"] != tab_id]


@pytest.fixture
def fake(home, monkeypatch):
    monkeypatch.setenv("FAKE_SIGNIN_DELAY", "0.2")

    def make(mode):
        monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", mode)
        return providers.Fake("x")
    return make


def _browser_script(tmp_path: Path) -> tuple[str, Path]:
    """A $BROWSER that writes down each URL it is given, as bombadil-browser passes it to agentd."""
    log = tmp_path / "browser.log"
    script = tmp_path / "browser.sh"
    script.write_text(f"#!/bin/sh\necho \"$1\" >> {log}\n")
    script.chmod(0o755)
    return str(script), log


async def _relay(s: signin.SignIn, log: Path):
    """What agentd does with bombadil-browser's message: hand the URL to the sign-in."""
    seen = 0
    while True:
        await asyncio.sleep(0.05)
        lines = log.read_text().split() if log.exists() else []
        for url in lines[seen:]:
            s.browser_url(url)
        seen = len(lines)


async def _run(s, log=None):
    relay = asyncio.create_task(_relay(s, log)) if log else None
    try:
        return await asyncio.wait_for(s.run(), 30)
    finally:
        if relay:
            relay.cancel()


def test_clean_and_urls():
    raw = ("\x1b[2K\x1b[1G\x1b[36mOpening browser to sign in…\x1b[39m\r\n"
           "\x1b]8;;https://claude.ai/oauth/authorize?a=1&b=2\x07Sign in\x1b]8;;\x07\r\n"
           "│ https://example.com/x?y=1 │\r\n"
           "https://half.example/still-writ")
    text = signin.clean(raw)
    assert "Opening browser to sign in…" in text and "\x1b" not in text
    assert signin.urls(text) == ["https://example.com/x?y=1"]
    assert signin.links(raw) == ["https://claude.ai/oauth/authorize?a=1&b=2"]


def test_last_words_skips_urls_and_debris():
    text = ("Opening browser…\nhttps://x.example/?a\n⠋\n\n"
            "Paste code here if prompted > Login failed: Invalid state parameter\n")
    assert signin.last_words(text) == "Login failed: Invalid state parameter"
    assert signin.last_words(text, 2) == "Opening browser… Login failed: Invalid state parameter"


@pytest.mark.asyncio
async def test_the_page_from_browser_comes_back_to_the_cli(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel()
    changes = []
    s = signin.SignIn(fake("auto"), lambda x: changes.append((x.phase, x.view)), panel=panel,
                      env={"BROWSER": cmd})
    assert await _run(s, log) == "done"
    # The page $BROWSER was given, not the printed one (that one ends on a code to paste).
    assert len(panel.opened) == 1 and "%2Fcallback" in panel.opened[0]
    assert (Path.home() / ".fake-signin").exists()
    assert changes[0] == ("waiting", "shown") and changes[-1][0] == "done"
    assert not panel.visible   # slid back out when done
    assert s.proc.returncode == 0


@pytest.mark.asyncio
async def test_a_printed_page_that_ends_on_a_code_gets_the_code_typed_in(fake):
    panel = FakePanel()
    s = signin.SignIn(fake("manual"), panel=panel, grace=0.3)
    assert await _run(s) == "done"
    assert len(panel.opened) == 1 and "%2Fcode" in panel.opened[0]
    assert "Login successful." in s.text
    assert len(s._pasted) == 1


@pytest.mark.asyncio
async def test_a_page_that_never_finishes_times_out_and_the_cli_is_ended(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel()
    s = signin.SignIn(fake("never"), panel=panel, env={"BROWSER": cmd}, timeout=2)
    t0 = time.monotonic()
    assert await _run(s, log) == "timeout"
    assert time.monotonic() - t0 < 8
    assert s.proc.returncode is not None
    assert not panel.visible


@pytest.mark.asyncio
async def test_a_refused_sign_in_fails_with_the_clis_reason(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel()
    s = signin.SignIn(fake("fail"), panel=panel, env={"BROWSER": cmd})
    assert await _run(s, log) == "failed"
    assert s.reason == "Login failed: access_denied"
    assert panel.visible   # a failed page stays, in case it says more


@pytest.mark.asyncio
async def test_the_panel_slid_out_or_closed_is_noticed_and_show_brings_it_back(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel()
    s = signin.SignIn(fake("never"), panel=panel, env={"BROWSER": cmd}, poll=0.1)
    run = asyncio.create_task(_run(s, log))

    async def until(pred):
        for _ in range(100):
            if pred():
                return True
            await asyncio.sleep(0.05)
        return False
    assert await until(lambda: s.phase == "waiting" and s.view == "shown")
    panel.hide()
    assert await until(lambda: s.view == "hidden")
    await s.show()
    assert panel.visible and s.view == "shown"
    panel.running = False   # the whole browser was closed
    assert await until(lambda: s.view == "closed")
    await s.show()
    assert len(panel.opened) == 2 and panel.opened[1] == panel.opened[0]   # the same page again
    assert await until(lambda: s.view == "shown")
    s.cancel()
    assert await run == "cancelled"
    assert s.proc.returncode is not None


@pytest.mark.asyncio
async def test_a_cli_that_is_missing_fails_at_once(home):
    class Missing(providers.Fake):
        def signin_command(self):
            return ["/nonexistent/claude", "auth", "login"]
    s = signin.SignIn(Missing("x"), panel=FakePanel())
    assert await _run(s) == "failed"
    assert "did not start" in s.reason


def test_reachable():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    assert signin.reachable("127.0.0.1", port, 1)
    srv.close()
    assert not signin.reachable("127.0.0.1", port, 1)
    assert not signin.reachable("no-such-host.invalid", 443, 1)


@pytest.mark.asyncio
async def test_a_page_that_would_not_open_is_offered_again_and_the_sign_in_still_finishes(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel()
    panel.fail_opens = 1   # a busy compositor: the first open raises
    changes = []
    s = signin.SignIn(fake("auto"), lambda x: changes.append((x.phase, x.view)), panel=panel,
                      env={"BROWSER": cmd}, poll=0.1)
    run = asyncio.create_task(_run(s, log))
    for _ in range(100):
        if s.phase == "waiting":
            break
        await asyncio.sleep(0.05)
    # Not stuck on "Opening the sign-in": the pill can say the page is not there and offer it again.
    assert (s.phase, s.view) == ("waiting", "closed") and "didn't respond" in s.hiccup
    await s.show()
    assert await run == "done"
    assert len(panel.opened) == 1


@pytest.mark.asyncio
async def test_a_browser_error_does_not_replace_the_reason_the_cli_gave(fake):
    class Wobbly(FakePanel):
        def tabs(self):
            raise RuntimeError("devtools went away")
    panel = Wobbly(follow=False)
    s = signin.SignIn(fake("manual"), panel=panel, grace=0.2, poll=0.1)
    run = asyncio.create_task(_run(s))
    for _ in range(100):
        if s.hiccup:
            break
        await asyncio.sleep(0.05)
    s.type("not a code\r")   # the CLI turns it down and ends
    assert await run == "failed"
    assert s.reason.startswith("Invalid code") and "devtools went away" in s.hiccup


@pytest.mark.asyncio
async def test_a_code_page_left_in_the_browser_is_not_typed_into_the_next_sign_in(fake):
    panel = FakePanel(follow=False)
    # An earlier manual sign-in ended on its code page, in the browser's only tab.
    panel.tabs_.append({"id": "old", "type": "page",
                        "url": "http://127.0.0.1:1/code?code=OLDCODE&state=OLDSTATE"})
    s = signin.SignIn(fake("manual"), panel=panel, grace=0.3, poll=0.1)
    run = asyncio.create_task(_run(s))
    for _ in range(100):
        if s.phase == "waiting":
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    assert not s._pasted   # the old code is not this page's
    s.cancel()
    assert await run == "cancelled"


@pytest.mark.asyncio
async def test_calling_it_off_while_the_browser_is_still_opening_puts_the_page_away(fake):
    panel = FakePanel(follow=False)
    panel.delay = 1.5   # a cold start of Chromium
    panel.tabs_.append({"id": "other", "type": "page", "url": "about:blank"})
    s = signin.SignIn(fake("never"), panel=panel, grace=0.2, poll=0.1)
    run = asyncio.create_task(_run(s))
    for _ in range(100):
        if s.url:
            break
        await asyncio.sleep(0.05)
    s.cancel()
    assert await run == "cancelled"
    assert not panel.visible                                   # not slid back in after the run
    assert [t["id"] for t in panel.tabs_] == ["other"]        # and its tab is closed


async def _until(pred, n=100):
    for _ in range(n):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_a_page_opened_again_that_the_browser_cannot_name_is_found_by_its_address(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel(follow=False)
    s = signin.SignIn(fake("never"), panel=panel, env={"BROWSER": cmd}, poll=0.1)
    run = asyncio.create_task(_run(s, log))
    assert await _until(lambda: s.phase == "waiting" and s.view == "shown")
    panel.running = False   # the whole browser was closed
    panel.tabs_.clear()
    assert await _until(lambda: s.view == "closed")
    panel.unnamed = 1       # it opens again, slowly, and cannot say which tab it made
    panel.delay = 0.4
    await asyncio.gather(s.show(), s.show())   # a double tap opens one page, not two
    assert len(panel.opened) == 2 and s.view == "shown"
    await asyncio.sleep(0.6)                   # a few looks later it is still shown, not "closed" again
    assert s.view == "shown" and s.tab == "t2"
    s.cancel()
    assert await run == "cancelled"
    assert panel.closed == ["t2"]              # the page that is really there is the one put away (not a stale id)


@pytest.mark.asyncio
async def test_calling_it_off_says_so_at_once_however_slow_the_browser_is(fake):
    phases = []
    panel = FakePanel(follow=False)
    panel.delay = 1.0
    s = signin.SignIn(fake("never"), lambda x: phases.append(x.phase), panel=panel, grace=0.2, poll=0.1)
    run = asyncio.create_task(_run(s))
    assert await _until(lambda: s.url)
    s.cancel()
    assert s.phase == "ending" and s.running   # the pill can say "Cancelling" this instant
    assert await run == "cancelled"
    assert "waiting" not in phases[phases.index("ending"):]   # the page opening late does not undo it
    assert panel.abandoned == 1 and not panel.visible and panel.opened == []


@pytest.mark.asyncio
async def test_a_tab_that_will_not_close_does_not_keep_the_panel_on_screen(fake, tmp_path):
    cmd, log = _browser_script(tmp_path)
    panel = FakePanel()
    panel.close_raises = True
    s = signin.SignIn(fake("auto"), panel=panel, env={"BROWSER": cmd}, poll=0.1)
    assert await _run(s, log) == "done"
    assert not panel.visible

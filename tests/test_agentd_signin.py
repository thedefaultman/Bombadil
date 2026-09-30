"""agentd and the provider: first boot's choice, signing in, prompts that wait for it, a turn
that finds the login gone, no network, and links from bombadil-browser.

The provider is the fake one with BOMBADIL_FAKE_SIGNIN set, so it needs signing in through
fake_signin.py; FakePanel (test_signin.py) is the browser.
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest
from test_signin import FakePanel

from bombadil import agentd, config, persona, providers, signin


@pytest.fixture
def signed_out(home, monkeypatch):
    monkeypatch.setenv("FAKE_SIGNIN_DELAY", "0.2")
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "auto")
    monkeypatch.setattr(agentd, "OFFLINE_POLL", 0.2)
    # The first sign-in asks for a name on a card that says "Signed in" itself (test_voice.py); with a
    # persona already saved, sign-in's own lines are what these tests read.
    persona.save()
    return home


async def _start(d):
    server = asyncio.create_task(d.serve())
    for _ in range(250):
        if d.socket_path.exists() and d.access != "checking":
            break
        await asyncio.sleep(0.02)
    r, w = await asyncio.open_unix_connection(str(d.socket_path), limit=1 << 24)
    greeting = [json.loads(await r.readline()) for _ in range(3)]
    assert [m["type"] for m in greeting] == ["status", "entries", "setup"]
    return server, r, w, greeting[2]


async def _send(w, msg):
    w.write((json.dumps(msg) + "\n").encode())
    await w.drain()


async def _until(r, pred, timeout=15):
    out = []
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), timeout))
        out.append(m)
        if pred(m):
            return out


def _setup(state):
    return lambda m: m.get("type") == "setup" and m.get("state") == state


class Browsing(providers.Fake):
    """The fake provider, with $BROWSER going nowhere: the tests hand the URL over as
    bombadil-browser would, through agentd's open_url."""


@pytest.mark.asyncio
async def test_first_boot_asks_which_ai_and_holds_prompts_until_signed_in(signed_out, monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, "claude", Browsing)   # "Claude" plays the fake
    panel = FakePanel()
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), chosen=False, panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "choose" and setup["line"] == "Which AI should run this computer?"
    assert [a["id"] for a in setup["actions"]] == ["provider:claude", "provider:codex"]
    assert {a["style"] for a in setup["actions"]} == {"big"}

    await _send(w, {"type": "prompt", "text": "tell me a joke"})
    msgs = await _until(r, lambda m: m.get("kind") == "queued")
    assert not any(m.get("kind") == "turn_start" for m in msgs)
    assert d.pending and d.turns == 0
    # A "!command" needs no AI: it runs while the joke waits.
    await _send(w, {"type": "prompt", "text": "!echo hi"})
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert any(m.get("kind") == "output" and m.get("text") == "hi" for m in msgs)

    await _send(w, {"type": "setup_action", "id": "provider:claude"})
    msgs = await _until(r, _setup("ready"))
    states = [m["state"] for m in msgs if m.get("type") == "setup"]
    assert "signing_in" in states
    lines = [m["line"] for m in msgs if m.get("type") == "setup"]
    assert "Opening the Fake sign-in" in lines and "Sign in to Fake in the browser" in lines
    assert msgs[-1]["line"] == "Signed in to Fake. Ask me for anything."
    assert config.load().provider == "claude" and d.chosen
    # The page went to the panel and slid back out when it was done.
    assert len(panel.opened) == 1 and not panel.visible
    # The joke that waited now runs.
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "turn_start")["prompt"] == "tell me a joke"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_signed_out_at_start_signs_in_by_itself(signed_out):
    panel = FakePanel()
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), auto_signin=True, panel=panel)
    server = asyncio.create_task(d.serve())
    for _ in range(300):
        if d.access == "ready":
            break
        await asyncio.sleep(0.05)
    assert d.access == "ready" and (Path.home() / ".fake-signin").exists()
    server.cancel()


@pytest.mark.asyncio
async def test_esc_calls_off_the_sign_in_and_the_prompts_that_waited(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    panel = FakePanel()
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "signed_out" and setup["line"] == "Sign in to Fake to start."
    assert setup["actions"][0] == {"id": "signin", "label": "Sign in", "style": "primary"}
    await _send(w, {"type": "prompt", "text": "hello"})   # asked while signed out: signs in
    await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "waiting")
    running = d.signin
    assert running is not None and running.url and panel.visible
    await _send(w, {"type": "stop"})
    msgs = await _until(r, _setup("signed_out"))
    assert msgs[-1]["line"] == "Sign-in cancelled."
    assert any(m.get("kind") == "unqueued" for m in msgs) and not d.pending
    assert running.proc.returncode is not None and not panel.visible
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_hidden_panel_and_show(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    panel = FakePanel()
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "setup_action", "id": "signin"})
    await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "waiting")
    panel.hide()
    msgs = await _until(r, lambda m: m.get("type") == "setup" and m.get("view") == "hidden")
    assert msgs[-1]["line"] == "The Fake sign-in is waiting in the browser"
    assert [a["id"] for a in msgs[-1]["actions"]] == ["show", "cancel"]
    await _send(w, {"type": "setup_action", "id": "show"})
    await _until(r, lambda m: m.get("type") == "setup" and m.get("view") == "shown")
    assert panel.visible
    panel.running = False
    msgs = await _until(r, lambda m: m.get("type") == "setup" and m.get("view") == "closed")
    assert msgs[-1]["line"] == "The Fake sign-in page was closed"
    assert msgs[-1]["actions"][0]["label"] == "Open it again"
    await _send(w, {"type": "setup_action", "id": "cancel"})
    await _until(r, _setup("signed_out"))
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_timed_out_sign_in_says_so(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    monkeypatch.setattr(signin, "TIMEOUT", 1.5)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "setup_action", "id": "signin"})
    msgs = await _until(r, _setup("signed_out"))
    assert msgs[-1]["line"] == "The Fake sign-in timed out." and msgs[-1]["tone"] == "error"
    assert msgs[-1]["actions"][0]["id"] == "signin"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_failed_sign_in_gives_the_reason(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "fail")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "setup_action", "id": "signin"})
    msgs = await _until(r, _setup("signed_out"))
    assert msgs[-1]["line"] == "Could not sign in to Fake: Login failed: access_denied."
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_no_network_waits_then_signs_in_when_it_comes_back(signed_out, monkeypatch):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    # Nothing listens there yet: "offline" (the VM smoke test does the same).
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN_HOST", f"127.0.0.1:{port}")
    p = providers.Fake("x")
    d = agentd.AgentD(p, agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "setup_action", "id": "signin"})
    msgs = await _until(r, _setup("offline"))
    assert msgs[-1]["line"] == "No internet. Connect to a network to sign in to Fake."
    assert [a["id"] for a in msgs[-1]["actions"]] == ["wifi", "signin"]
    probe.listen()   # the network is back
    await _until(r, _setup("ready"))
    probe.close()
    w.close()
    server.cancel()


class GoneLogin(providers.Fake):
    """A CLI whose first turn says the login is gone, like `claude -p` with a dead token."""
    SIGNED_OUT = (r"Please run /login",)

    def command(self, turn, workdir):
        flag = Path.home() / ".turned"
        if not flag.exists():
            flag.write_text("")
            return ["sh", "-c", "echo 'Invalid API key · Please run /login' >&2; exit 1"]
        return super().command(turn, workdir)

    def finish(self):
        if getattr(self, "returncode", 0) not in (0, None):
            return iter(())   # it said nothing on stdout; stderr has the reason
        return super().finish()


@pytest.mark.asyncio
async def test_a_turn_that_finds_the_login_gone_signs_in_and_runs_again(signed_out, monkeypatch):
    (Path.home() / ".fake-signin").write_text("stale")
    panel = FakePanel()
    d = agentd.AgentD(GoneLogin("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "ready"
    (Path.home() / ".fake-signin").unlink()   # the token died
    await _send(w, {"type": "prompt", "text": "hello"})
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "error")["text"] == "Fake signed you out."
    msgs = await _until(r, _setup("ready"))
    assert "Fake signed you out." in [m.get("line") for m in msgs]
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "result")["text"] == "echo: hello"
    assert d.turns == 2 and len(panel.opened) == 1
    w.close()
    server.cancel()


class RetryingLogin(providers.Fake):
    """Like `codex exec` logged out: a 401 at once, then retries for a while before it gives up."""
    ends_when_signed_out = True

    def command(self, turn, workdir):
        flag = Path.home() / ".turned"
        if not flag.exists():
            flag.write_text("")
            return ["sh", "-c", "echo 'unexpected status 401 Unauthorized'; sleep 60"]
        return super().command(turn, workdir)

    def parse(self, line):
        if "401" in line:
            yield {"kind": "signed_out"}
        else:
            yield from super().parse(line)

    def finish(self):
        if getattr(self, "returncode", 0) not in (0, None):
            return iter(())
        return super().finish()


@pytest.mark.asyncio
async def test_a_cli_that_would_retry_is_ended_at_the_first_sign_the_login_is_gone(signed_out):
    (Path.home() / ".fake-signin").write_text("stale")
    panel = FakePanel()
    d = agentd.AgentD(RetryingLogin("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, _ = await _start(d)
    (Path.home() / ".fake-signin").unlink()
    t0 = asyncio.get_running_loop().time()
    await _send(w, {"type": "prompt", "text": "hello"})
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert asyncio.get_running_loop().time() - t0 < 10
    assert [m["text"] for m in msgs if m.get("kind") == "error"] == ["Fake signed you out."]
    assert not msgs[-1]["stopped"]
    await _until(r, _setup("ready"))
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "result")["text"] == "echo: hello"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_links_go_to_the_panel_and_the_sign_in_takes_its_own(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    panel = FakePanel(follow=False)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "open_url", "url": "https://example.com/a"})
    for _ in range(100):
        if panel.opened:
            break
        await asyncio.sleep(0.02)
    assert panel.opened == ["https://example.com/a"]
    await _send(w, {"type": "open_url", "url": "javascript:alert(1)"})
    await _send(w, {"type": "setup_action", "id": "signin"})
    await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "waiting")
    # fake_signin.py handed its page to $BROWSER, bin/bombadil-browser, which told this agentd.
    assert len(panel.opened) == 2 and "/authorize" in panel.opened[1]
    d.signin.cancel()
    await _until(r, _setup("signed_out"))
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_login_run_in_a_terminal_is_watched_until_it_lands(signed_out):
    panel = FakePanel(follow=False)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "signed_out"
    # `claude auth login` typed in a terminal handed its page to bombadil-browser.
    await _send(w, {"type": "open_url", "url": "http://127.0.0.1:1/authorize?redirect_uri=http%3A%2F%2F127.0.0.1%3A1%2Fcallback"})
    msgs = await _until(r, _setup("signing_in"))
    assert msgs[-1]["line"] == "Sign in to Fake in the browser"
    (Path.home() / ".fake-signin").write_text("signed in")   # that CLI saved its login
    msgs = await _until(r, _setup("ready"))
    assert msgs[-1]["line"] == "Signed in to Fake. Ask me for anything."
    assert not panel.visible
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_typing_the_name_answers_which_ai(signed_out, monkeypatch):
    monkeypatch.delenv("BOMBADIL_FAKE_SIGNIN")
    monkeypatch.setitem(providers.PROVIDERS, "codex", Browsing)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), chosen=False, panel=FakePanel())
    server, r, w, setup = await _start(d)
    assert setup["state"] == "choose"
    await _send(w, {"type": "prompt", "text": "Codex"})
    msgs = await _until(r, _setup("ready"))
    assert msgs[-1]["line"] == "Fake is ready. Ask me for anything."
    assert config.load().provider == "codex" and d.provider.name == "fake"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_double_tap_on_sign_in_starts_one_login(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    probe.listen()
    # The sign-in server answers, so start_signin waits on the check before it starts the login.
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN_HOST", f"127.0.0.1:{probe.getsockname()[1]}")
    starts = []

    class Counting(signin.SignIn):
        def __init__(self, *a, **kw):
            starts.append(1)
            super().__init__(*a, **kw)
    monkeypatch.setattr(signin, "SignIn", Counting)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=FakePanel(follow=False))
    server, r, w, _ = await _start(d)
    for _ in range(2):
        await _send(w, {"type": "setup_action", "id": "signin"})
    await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "waiting")
    await asyncio.sleep(0.5)
    assert len(starts) == 1
    d.signin.cancel()
    await _until(r, _setup("signed_out"))
    probe.close()
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_terminal_login_is_watched_after_the_pill_sign_in_was_called_off(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    panel = FakePanel(follow=False)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), auto_signin=True, panel=panel)
    server, r, w, _ = await _start(d)
    await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "waiting")
    d.signin.cancel()
    await _until(r, _setup("signed_out"))
    assert d.signin is None   # nothing is signing in
    await _send(w, {"type": "open_url", "url": "http://127.0.0.1:1/authorize?redirect_uri=http%3A%2F%2F127.0.0.1%3A1%2Fcallback"})
    await _until(r, _setup("signing_in"))
    (Path.home() / ".fake-signin").write_text("signed in")
    await _until(r, _setup("ready"))
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_calling_off_a_sign_in_that_was_not_needed_keeps_the_login_and_the_queue(signed_out, monkeypatch):
    (Path.home() / ".fake-signin").write_text("signed in")
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    panel = FakePanel(follow=False)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "ready"
    await _send(w, {"type": "prompt", "text": "!sleep 1"})     # a turn is running...
    await _until(r, lambda m: m.get("kind") == "turn_start")
    await _send(w, {"type": "prompt", "text": "hello"})        # ...and this waits behind it
    await _send(w, {"type": "prompt", "text": "sign in"})      # sign in again although signed in
    await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "waiting")
    d.signin.cancel()
    await _until(r, _setup("ready"))                            # still signed in: ready, not signed out
    msgs = await _until(r, lambda m: m.get("kind") == "result" and "hello" in m.get("text", ""))
    assert not any(m.get("kind") == "unqueued" for m in msgs)  # "hello" was kept, and ran
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_picking_an_ai_that_needs_signing_in_never_shows_signed_out_first(signed_out, monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, "claude", Browsing)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), chosen=False, panel=FakePanel())
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "setup_action", "id": "provider:claude"})
    msgs = await _until(r, _setup("ready"))
    states = [m["state"] for m in msgs if m.get("type") == "setup"]
    assert "signed_out" not in states and "signing_in" in states   # `bombadil provider` follows this
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_launcher_words_about_signing_in_answer_like_the_rest(signed_out, monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, "codex", Browsing)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    await _send(w, {"type": "prompt", "text": "use codex"})
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["ok"] and msgs[-1]["action"] == "provider"   # `bombadil ask "use codex"` ends
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_turn_the_user_stopped_is_not_run_again_after_a_sign_in(signed_out):
    (Path.home() / ".fake-signin").write_text("signed in")
    d = agentd.AgentD(RetryingLogin("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    (Path.home() / ".fake-signin").unlink()
    await _send(w, {"type": "prompt", "text": "hello"})
    await _until(r, lambda m: m.get("kind") == "turn_start")
    await _send(w, {"type": "stop"})
    await _until(r, lambda m: m.get("kind") == "turn_end")
    await asyncio.sleep(1)
    assert d.turns == 1 and not d.pending   # the stopped ask is gone, not queued behind the sign-in
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_bar_that_connects_later_is_not_told_about_a_sign_in_from_long_ago(signed_out, monkeypatch):
    (Path.home() / ".fake-signin").write_text("signed in")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, setup = await _start(d)
    await d._set_access("ready", "Signed in to Fake. Ask me for anything.", "done")
    assert d._setup_msg()["line"] == "Signed in to Fake. Ask me for anything."
    monkeypatch.setattr(agentd, "READY_LINE_SECONDS", -1)
    assert d._setup_msg()["line"] == ""
    w.close()
    server.cancel()


def _interpreters():
    import shutil
    import sys
    found = {sys.executable}
    found.update(p for p in (shutil.which(f"python3.{v}") for v in (12, 13, 14)) if p)
    return sorted(found)


@pytest.mark.parametrize("python", _interpreters())
def test_agentd_exits_on_sigterm_with_the_bar_connected(home, monkeypatch, python):
    """Python 3.12+ waits for every client before a closing server lets go, and the bar never
    hangs up: agentd stayed (a login it started with it) and a fresh agentd ran beside it."""
    import os
    import signal
    import subprocess
    import time
    monkeypatch.setenv("BOMBADIL_PROVIDER", "fake")
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    monkeypatch.setenv("FAKE_SIGNIN_DELAY", "0.2")
    (home / "run").mkdir(exist_ok=True)
    agentd_bin = Path(__file__).resolve().parents[1] / "bin" / "agentd"
    proc = subprocess.Popen([python, str(agentd_bin)], env=os.environ, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        sock = home / "run" / "agentd.sock"
        for _ in range(200):
            if sock.exists():
                break
            time.sleep(0.1)
        assert sock.exists()
        bar = socket.socket(socket.AF_UNIX)
        bar.connect(str(sock))            # the bar: connected, and it stays
        time.sleep(1.5)                   # (the sign-in has started by now, waiting for a page)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
        bar.close()
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.asyncio
async def test_a_turn_that_found_the_old_ais_login_gone_leaves_the_new_ai_alone(signed_out, monkeypatch):
    """"use codex" comes while a Claude turn runs, then Claude reports it is signed out: Codex is
    signed in and must not be signed out (its login revokes the old one) or get Claude's prompt."""
    class Old(providers.Fake):
        title = "Old"

        def command(self, turn, workdir):
            return ["sh", "-c", "sleep 1; echo 'Invalid API key · Please run /login' >&2; exit 1"]
        SIGNED_OUT = (r"Please run /login",)

        def finish(self):
            return iter(())

    monkeypatch.setitem(providers.PROVIDERS, "codex", Browsing)
    (Path.home() / ".fake-signin").write_text("signed in")
    panel = FakePanel()
    d = agentd.AgentD(Old("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "ready"
    await _send(w, {"type": "prompt", "text": "hello"})
    await _until(r, lambda m: m.get("kind") == "turn_start")
    await _send(w, {"type": "setup_action", "id": "provider:codex"})
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "error")["text"] == "Old signed you out."
    await asyncio.sleep(0.5)
    assert d.access == "ready" and d.signin is None and not d._login_gone
    assert not d.pending and panel.opened == [] and d.provider.name == "fake"
    assert config.load().provider == "codex"
    w.close()
    server.cancel()


class AlwaysGone(providers.Fake):
    """A login that works but a CLI that keeps saying it is signed out (a 403, an API key in the environment)."""
    SIGNED_OUT = (r"Please run /login",)

    def command(self, turn, workdir):
        return ["sh", "-c", "echo 'Invalid API key · Please run /login' >&2; exit 1"]

    def finish(self):
        return iter(())


@pytest.mark.asyncio
async def test_a_rerun_that_fails_the_same_way_shows_the_cli_error_and_does_not_sign_in_again(signed_out):
    (Path.home() / ".fake-signin").write_text("signed in")
    panel = FakePanel()
    d = agentd.AgentD(AlwaysGone("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, _ = await _start(d)
    (Path.home() / ".fake-signin").unlink()   # the first failure is a real sign-out
    await _send(w, {"type": "prompt", "text": "hello"})
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "error")["text"] == "Fake signed you out."
    await _until(r, _setup("ready"))          # the sign-in worked...
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")   # ...and the prompt ran again
    assert "Please run /login" in next(m for m in msgs if m.get("kind") == "error")["text"]   # the CLI's words
    await asyncio.sleep(1)
    assert d.access == "ready" and d.signin is None and not d._login_gone
    assert len(panel.opened) == 1 and d.turns == 2 and not d.pending   # one sign-in, not a loop
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_what_the_user_did_without_the_model_reaches_the_rerun(signed_out):
    (Path.home() / ".fake-signin").write_text("signed in")
    d = agentd.AgentD(GoneLogin("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    (Path.home() / ".fake-signin").unlink()
    d.notes.append("'open notes': Opened Notes.")
    await _send(w, {"type": "prompt", "text": "hello"})
    await _until(r, lambda m: m.get("kind") == "turn_end")
    await _until(r, _setup("ready"))
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    said = next(m for m in msgs if m.get("kind") == "result")["text"]
    assert "Done by the user without you" in said and "Opened Notes" in said and said.endswith("hello")
    w.close()
    server.cancel()


class Replacing(Browsing):
    """Like Codex: signing in again signs the stored login out first."""
    login_replaces = True


@pytest.mark.asyncio
async def test_sign_in_typed_while_signed_in_leaves_a_login_that_a_new_one_would_replace(signed_out):
    (Path.home() / ".fake-signin").write_text("signed in")
    panel = FakePanel()
    d = agentd.AgentD(Replacing("x"), agentd._NoSnapshots(), panel=panel)
    server, r, w, setup = await _start(d)
    assert setup["state"] == "ready"
    await _send(w, {"type": "prompt", "text": "sign in"})
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["ok"] and msgs[-1]["text"] == "You are already signed in to Fake."
    await _send(w, {"type": "signin"})   # `bombadil signin` hears the same, as a setup line
    msgs = await _until(r, lambda m: m.get("type") == "setup" and "already signed in" in m.get("line", ""))
    assert msgs[-1]["state"] == "ready"
    await asyncio.sleep(0.3)
    assert d.signin is None and panel.opened == [] and (Path.home() / ".fake-signin").exists()
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_dead_login_still_signs_in_when_asked_even_if_a_new_login_replaces_it(signed_out):
    panel = FakePanel()
    d = agentd.AgentD(Replacing("x"), agentd._NoSnapshots(), panel=panel, auto_signin=True)
    server, r, w, _ = await _start(d)
    await _until(r, _setup("ready")) if d.access != "ready" else None   # signed out at start: signs in by itself
    (Path.home() / ".fake-signin").unlink()
    await _send(w, {"type": "prompt", "text": "sign in"})   # the login died: this is not "already"
    await _until(r, lambda m: m.get("type") == "setup" and m.get("state") == "signing_in")
    await _until(r, _setup("ready"))
    assert len(panel.opened) == 2
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_calling_off_a_sign_in_says_cancelling_at_once(signed_out, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "never")
    panel = FakePanel(follow=False)
    panel.delay = 1.5   # a cold browser
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), panel=panel, auto_signin=True)
    server, r, w, _ = await _start(d)
    for _ in range(100):   # (signed out at start: the sign-in opens by itself, slowly)
        if d.signin is not None and d.signin.running:
            break
        await asyncio.sleep(0.05)
    await _send(w, {"type": "stop"})
    msgs = await _until(r, lambda m: m.get("type") == "setup" and m.get("phase") == "ending")
    assert msgs[-1]["line"] == "Cancelling the Fake sign-in" and msgs[-1]["actions"] == []
    await _until(r, _setup("signed_out"))
    assert not panel.visible
    w.close()
    server.cancel()

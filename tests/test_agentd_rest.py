"""agentd while the AI rests: a refusal for the account's limit, a pause by hand, and what waits.

The provider is Claude with a Python one-liner for its CLI (see `_cli`): it refuses with a 429 shaped like the
real CLI's, partway through if the test says so, until the test flips its mode to "ok". Nothing here calls a
model or the network.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest
from test_agentd import RecordingSnaps, Scripted, _ask, _quiet_watch, _start, _step_texts
from test_signin import FakePanel

from bombadil import agentd, providers, rest


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch):
    monkeypatch.setattr(agentd.sysmap, "snapshot", lambda *a, **k: {})
    # The other AI is not on these machines, whatever this one has installed.
    monkeypatch.setattr(providers.Codex, "installed", property(lambda self: False))


@pytest.fixture
def needs_login(home, monkeypatch):
    """The fake provider is signed out and signs itself in (fake_signin.py) a moment after it is asked."""
    monkeypatch.setenv("FAKE_SIGNIN_DELAY", "0.2")
    monkeypatch.setenv("BOMBADIL_FAKE_SIGNIN", "auto")
    return home


CLI = r'''
import json, os, sys, time
prompt = sys.stdin.read()
open(@LOG@, "a").write(json.dumps(prompt) + "\n")
mode = open(@MODE@).read().strip()
def out(o): print(json.dumps(o), flush=True)
if mode == "ok":
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "answered"}]}})
    out({"type": "result", "result": "answered", "session_id": "s1"})
    sys.exit(0)
if mode == "partial":
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Write",
         "input": {"file_path": "/home/u/tracker/main.qml", "content": "x"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "w1", "content": "ok"}]}})
if mode == "waiting":
    # What the CLI does when a host switches on its unattended retry: it sleeps until the reset.
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": time.time() + 7200,
         "rateLimitType": "five_hour", "isUsingOverage": False}})
    out({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 0, "retry_delay_ms": 7200000,
         "error_status": 429, "error": "rate_limit"})
    time.sleep(60)
    sys.exit(0)
if mode == "retrying":
    out({"type": "system", "subtype": "api_retry", "error_status": 529, "attempt": 1})
    time.sleep(3.0)
    out({"type": "result", "result": "answered", "session_id": "s1"})
    sys.exit(0)

def refuse(text, error, info):
    out({"type": "rate_limit_event", "rate_limit_info": info})
    out({"type": "assistant", "error": error, "is_api_error_message": True,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
    out({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "result": text,
         "api_error_status": 429, "terminal_reason": "api_error", "session_id": "s-refused"})

if mode == "spend":
    refuse("You're out of extra usage · your organization's monthly spend limit was reached", "billing_error",
           {"status": "rejected", "rateLimitType": "overage", "isUsingOverage": False})
elif mode == "throttle":
    refuse("Server is temporarily limiting requests (not your usage limit) · Try again in a moment", "rate_limit",
           {"status": "allowed", "rateLimitType": "five_hour"})
else:
    refuse("You've hit your session limit", "rate_limit",
           {"status": "rejected", "resetsAt": time.time() + @WAIT@, "rateLimitType": "five_hour",
            "isUsingOverage": False})
'''


class Cli:
    """The scripted CLI and what it was asked: `mode` is ok, refuse, partial, spend, throttle or retrying."""

    def __init__(self, home, wait=3600.0, mode="refuse"):
        self.mode_file, self.log = home / "mode", home / "prompts.log"
        self.mode = mode
        self.script = (CLI.replace("@LOG@", repr(str(self.log))).replace("@MODE@", repr(str(self.mode_file)))
                       .replace("@WAIT@", repr(wait)))

    @property
    def mode(self):
        return self.mode_file.read_text().strip()

    @mode.setter
    def mode(self, value):
        self.mode_file.write_text(value)

    @property
    def prompts(self) -> list[str]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


async def _until(r, pred, timeout=10):
    out = []
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), timeout))
        out.append(m)
        if pred(m):
            return out


def _resting(m):
    return m.get("type") == "setup" and m["state"] == "resting"


def _ready(m):
    return m.get("type") == "setup" and m["state"] == "ready"


def _ended(m):
    return m.get("kind") == "turn_end"


def _send(w, msg):
    w.write((json.dumps(msg) + "\n").encode())


def _line(until, title="Claude", word="limit"):
    return f"{title} is at its {word} until {rest.when(until)}. Your apps and files still work."


async def _rest_with(home, text="make me a habit tracker", session=None, **kw):
    """A daemon whose first ask is refused, left resting. Returns everything a test needs."""
    cli = Cli(home, **kw)
    provider = Scripted(cli.script)
    d = agentd.AgentD(provider, agentd._NoSnapshots(), panel=FakePanel(follow=False))
    d.session_id = session                                 # the conversation so far
    server, r, w = await _start(d)
    await _ask(w, text)
    msgs = await _until(r, _resting)
    return cli, provider, d, server, r, w, msgs


async def _stop(server, w):
    w.close()
    server.cancel()


# -- a refusal --

@pytest.mark.asyncio
async def test_a_refused_ask_rests_the_machine_and_waits_as_a_chip(home):
    cli, _, d, server, r, w, msgs = await _rest_with(home)
    until = rest.limit("claude").until
    assert until and until > time.time() + 3000
    # The turn ended for the limit: no result, no error, and it says it will run again.
    assert not [m for m in msgs if m.get("kind") in ("error", "result")]
    end = next(m for m in msgs if _ended(m))
    assert end["requeued"] is True and end["changed"] is False and end["stopped"] is False
    assert end["line"] == _line(until)
    assert [m for m in msgs if m.get("kind") == "queued" and m["turn"] == 1 and m["prompt"] == "make me a habit tracker"]
    setup = msgs[-1]
    assert setup["line"] == _line(until) and setup["tone"] == "step" and setup["actions"] == []
    assert setup["rest"] == {"provider": "claude", "why": "limit", "kind": "five_hour", "until": until,
                             "when": rest.when(until), "hint": f"Open or find anything. Asks wait for {rest.when(until)}.",
                             "note": f"At {rest.when(until, short=True)}", "wait": rest.when(until, short=True)}
    status = (await _until(r, lambda m: m["type"] == "status" and m["setup"] == "resting"))[-1]
    assert status["queue"] == [{"turn": 1, "prompt": "make me a habit tracker", "wait": setup["rest"]["wait"]}]
    assert status["rest"] == setup["rest"] and status["busy"] is False
    # The file other processes read, and the turn's own log.
    assert rest.current("claude").why == "limit"
    kinds = [json.loads(line)["kind"] for line in d.turn_logs[1].read_text().splitlines()]
    assert "rest" in kinds and kinds[-1] == "turn_end"
    log = [json.loads(line) for line in agentd.paths.turns_log().read_text().splitlines()]
    assert log[-1]["requeued"] is True and log[-1]["summary"] == _line(until) and log[-1]["prompt"] == "make me a habit tracker"
    await _stop(server, w)


@pytest.mark.asyncio
async def test_while_it_rests_nothing_is_sent_and_every_ask_waits_in_order(home):
    cli, _, d, server, r, w, _ = await _rest_with(home)
    await _ask(w, "then add a chart")
    await _ask(w, "and make it blue")
    status = (await _until(r, lambda m: m["type"] == "status" and len(m["queue"]) == 3))[-1]
    assert [q["prompt"] for q in status["queue"]] == ["make me a habit tracker", "then add a chart", "and make it blue"]
    assert {q["wait"] for q in status["queue"]} == {status["rest"]["wait"]} and status["busy"] is False
    await asyncio.sleep(0.3)
    assert cli.prompts == ["make me a habit tracker"]     # the one that was refused; nothing since
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_typed_command_and_launcher_words_still_work_while_it_rests(home):
    cli, _, d, server, r, w, _ = await _rest_with(home)
    await _ask(w, "!echo hello there")
    msgs = await _until(r, _ended)
    assert any(m.get("kind") == "tool_result" and "hello there" in m.get("output", "") for m in msgs)
    assert not [m for m in msgs if m.get("kind") == "error"]
    await _ask(w, "undo")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["action"] == "undo"                   # answered here, at once
    assert d.access == "resting" and [i for i, _p in d.pending] == [1]
    assert cli.prompts == ["make me a habit tracker"]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_it_comes_back_by_itself_and_runs_the_waiting_asks_in_order_in_the_same_conversation(home, monkeypatch):
    monkeypatch.setattr(rest, "RESET_GRACE", 0.0)
    cli, provider, d, server, r, w, _ = await _rest_with(home, wait=1.5, session="s0")
    await _ask(w, "then add a chart")
    await _until(r, lambda m: m["type"] == "status" and len(m["queue"]) == 2)
    cli.mode = "ok"                                        # the account is back when the time comes
    back = (await _until(r, _ready, timeout=10))[-1]
    assert back["line"] == "Claude is back. Running your 2 waiting asks." and back["tone"] == "done"
    assert "rest" not in back and d.access == "ready"
    first = await _until(r, _ended)
    second = await _until(r, _ended)
    assert not [m for m in first + second if m.get("requeued")]
    assert [m["turn"] for m in first if m.get("kind") == "turn_start"] == [1]      # the same id it was asked under
    assert [m["turn"] for m in second if m.get("kind") == "turn_start"] == [2]
    assert cli.prompts == ["make me a habit tracker", "make me a habit tracker", "then add a chart"]
    assert provider.seen_sessions == ["s0", "s0", "s1"]    # the refused try did not cost the conversation
    assert rest.limit("claude") is None
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_turn_cut_off_partway_says_what_it_changed_and_its_rerun_is_told(home, monkeypatch):
    monkeypatch.setattr(rest, "RESET_GRACE", 0.0)
    cli = Cli(home, wait=1.5, mode="partial")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "make me a habit tracker")
    msgs = await _until(r, _resting)
    until = rest.limit("claude").until
    end = next(m for m in msgs if _ended(m))
    assert end["requeued"] and end["changed"] is True
    assert end["line"] == f"Claude hit its limit partway, after changing 1 file. It carries on at {rest.when(until)}."
    assert any(m.get("kind") == "tool" for m in msgs)       # what it did before is still in the turn
    cli.mode = "ok"
    await _until(r, _ready, timeout=10)
    await _until(r, _ended)
    again = cli.prompts[1]
    assert again.startswith("[The last try stopped at a usage limit after: ")
    assert "Check what is done before redoing it.]" in again and again.endswith("make me a habit tracker")
    assert cli.prompts[0] == "make me a habit tracker"
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_second_refusal_adds_to_what_the_rerun_is_told_without_repeating_itself(home, monkeypatch):
    monkeypatch.setattr(rest, "RESET_GRACE", 0.0)
    cli = Cli(home, wait=1.0, mode="partial")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "make me a habit tracker")
    await _until(r, _resting)
    await _until(r, _ready, timeout=10)                    # its time came; the next try is refused again
    await _until(r, _resting)
    cli.mode = "ok"
    await _until(r, _ready, timeout=10)
    await _until(r, _ended)
    assert cli.prompts[-1].count("The last try stopped at a usage limit after") == 1
    assert len(cli.prompts) == 3
    await _stop(server, w)


@pytest.mark.asyncio
async def test_an_ask_cut_off_with_nothing_changed_is_not_told_anything_on_its_rerun(home, monkeypatch):
    monkeypatch.setattr(rest, "RESET_GRACE", 0.0)
    cli, _, d, server, r, w, _ = await _rest_with(home, wait=1.5)
    cli.mode = "ok"
    await _until(r, _ready, timeout=10)
    await _until(r, _ended)
    assert cli.prompts[1] == "make me a habit tracker"
    await _stop(server, w)


@pytest.mark.asyncio
async def test_the_refused_turns_empty_restore_point_is_not_what_undo_takes_back(home):
    snaps = RecordingSnaps()
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), snaps)
    server, r, w = await _start(d)
    await _ask(w, "one")
    await _until(r, _ended)
    cli.mode = "refuse"
    await _ask(w, "two")
    await _until(r, _resting)
    assert [s.number for s in snaps.made] == [1, 2]
    await _ask(w, "undo")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["ok"] is True and "“one”" in msgs[-1]["text"]
    assert ("rollback", 1) in snaps.log and ("rollback", 2) not in snaps.log
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_cli_that_sleeps_until_the_reset_is_ended_and_the_ask_waits_like_any_other(home):
    cli = Cli(home, mode="waiting")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "make me a habit tracker")
    msgs = await _until(r, _ended, timeout=10)           # it did not hold the turn for two hours
    end = msgs[-1]
    until = rest.limit("claude").until
    assert end["requeued"] is True and end["line"] == _line(until)
    assert abs(until - (time.time() + 7200)) < 30 and rest.limit("claude").kind == "five_hour"
    assert not [m for m in msgs if m.get("kind") == "error"]
    await _until(r, _resting)
    assert d.proc is None or d.proc.returncode is not None
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_busy_moment_is_an_error_not_a_rest(home):
    cli = Cli(home, mode="throttle")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "hello")
    msgs = await _until(r, _ended)
    assert [m["text"] for m in msgs if m.get("kind") == "error"] == [agentd.RATE_TEXT]
    assert not msgs[-1].get("requeued") and d.access == "ready" and rest.limit("claude") is None
    await _stop(server, w)


@pytest.mark.asyncio
async def test_the_cli_retrying_a_busy_api_is_not_called_a_dead_connection(home, monkeypatch):
    _quiet_watch(monkeypatch)
    cli = Cli(home, mode="retrying")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "hello")
    texts = _step_texts(await _until(r, _ended))
    assert "Claude is busy, trying again" in texts
    assert not [t for t in texts if "not answering" in t]
    await _stop(server, w)


# -- the button under the line --

@pytest.mark.asyncio
async def test_a_spending_limit_offers_raise_the_limit_which_becomes_try_again_once_pressed(home):
    cli, _, d, server, r, w, msgs = await _rest_with(home, mode="spend")
    setup = msgs[-1]
    assert setup["line"] == "Claude is at its spending limit. Your apps and files still work."
    assert setup["actions"] == [{"id": "raise", "label": "Raise the limit", "style": "quiet"}]
    assert setup["rest"]["why"] == "spend" and setup["rest"]["until"] is None and setup["rest"]["wait"] == "limit"
    _send(w, {"type": "setup_action", "id": "raise"})
    pressed = (await _until(r, _resting))[-1]
    assert d.panel.opened == [rest.RAISE_PAGES["claude"]]          # the provider's page; nothing is bought
    assert pressed["actions"] == [{"id": "retry", "label": "Try again", "style": "primary"}]
    cli.mode = "ok"
    _send(w, {"type": "setup_action", "id": "retry"})
    back = (await _until(r, _ready))[-1]
    assert back["line"] == "Trying Claude again. Running your waiting ask."
    await _until(r, _ended)
    assert cli.prompts == ["make me a habit tracker", "make me a habit tracker"] and rest.limit("claude") is None
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_limit_with_a_time_offers_no_button_and_the_buttons_do_nothing_when_not_resting(home):
    cli, _, d, server, r, w, msgs = await _rest_with(home)
    assert msgs[-1]["actions"] == []
    _send(w, {"type": "setup_action", "id": "raise"})
    await asyncio.sleep(0.2)
    assert d.panel.opened == [] and d.access == "resting"
    await _stop(server, w)
    cli2 = Cli(home, mode="ok")
    d2 = agentd.AgentD(Scripted(cli2.script), agentd._NoSnapshots())
    d2.socket_path = home / "run" / "other.sock"
    rest.clear_limit("claude")
    server2, r2, w2 = await _start(d2)
    for action in ("resume", "raise", "retry"):
        _send(w2, {"type": "setup_action", "id": action})
    await asyncio.sleep(0.2)
    assert d2.access == "ready" and d2.panel is not None
    await _stop(server2, w2)


# -- by hand --

@pytest.mark.asyncio
async def test_pausing_by_hand_rests_the_machine_until_it_is_resumed_and_a_restart_keeps_it(home):
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "pause claude")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["text"] == "Claude is paused. Your apps and files still work." and msgs[-1]["action"] == "rest"
    setup = (await _until(r, _resting))[-1] if not any(_resting(m) for m in msgs) else next(m for m in msgs if _resting(m))
    assert setup["rest"]["why"] == "hand" and setup["rest"]["wait"] == "paused"
    assert setup["actions"] == [{"id": "resume", "label": "Resume Claude", "style": "primary"}]
    assert setup["rest"]["hint"] == "Open or find anything. Asks wait until you resume Claude."
    await _ask(w, "make me a habit tracker")
    status = (await _until(r, lambda m: m["type"] == "status" and m["queue"]))[-1]
    assert status["queue"][0]["wait"] == "paused" and status["busy"] is False
    await asyncio.sleep(0.2)
    assert cli.prompts == []
    await _stop(server, w)
    # A new agentd on the same machine: the pause is still on, and it says so at once.
    d2 = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    d2.socket_path = home / "run" / "again.sock"
    server2 = asyncio.create_task(d2.serve())
    for _ in range(250):
        if d2.socket_path.exists() and d2.access != "checking":
            break
        await asyncio.sleep(0.02)
    assert d2.access == "resting" and d2._setup_msg()["line"] == "Claude is paused. Your apps and files still work."
    r2, w2 = await asyncio.open_unix_connection(str(d2.socket_path), limit=1 << 24)
    for _ in range(3):
        await r2.readline()
    _send(w2, {"type": "setup_action", "id": "resume"})
    back = (await _until(r2, _ready))[-1]
    assert back["line"] == "Claude is back." and rest.hand("claude") is None
    await _stop(server2, w2)


@pytest.mark.asyncio
async def test_resume_claude_typed_ends_a_pause_and_runs_what_waited(home):
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "pause the ai")
    await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    await _ask(w, "make me a habit tracker")
    await _ask(w, "resume the ai")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["text"] == "Claude is back. Running your waiting ask."
    await _until(r, _ended)
    assert cli.prompts == ["make me a habit tracker"]
    await _ask(w, "resume claude")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["text"] == "Claude was not paused."
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_pause_while_a_limit_is_on_shows_what_is_left_when_it_is_resumed(home):
    cli, _, d, server, r, w, msgs = await _rest_with(home)
    until = rest.limit("claude").until
    await _ask(w, "pause claude")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    paused = next(m for m in msgs if _resting(m) and m["rest"]["why"] == "hand")
    assert paused["line"] == "Claude is paused. Your apps and files still work."
    await _ask(w, "resume claude")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["text"] == _line(until)
    assert d.access == "resting" and d._rest_cur.why == "limit"
    await _stop(server, w)


@pytest.mark.asyncio
async def test_pausing_the_other_ai_leaves_this_one_running(home):
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "pause codex")
    msgs = await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert msgs[-1]["text"] == "Codex is paused. Your apps and files still work."
    assert d.access == "ready" and rest.hand("codex") is not None and rest.hand("claude") is None
    await _ask(w, "hello")
    await _until(r, _ended)
    assert cli.prompts == ["hello"]
    await _stop(server, w)


# -- the AI card --

@pytest.mark.asyncio
async def test_the_ai_card_has_a_row_per_ai_and_its_switches_pause_and_resume(home):
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    _send(w, {"type": "ai", "op": "get"})
    rows = (await _until(r, lambda m: m["type"] == "ai"))[-1]["rows"]
    assert rows == [
        {"name": "claude", "title": "Claude", "state": "ready", "text": "ready", "on": True, "enabled": True,
         "current": True},
        {"name": "codex", "title": "Codex", "state": "missing", "text": "not installed", "on": False,
         "enabled": False, "current": False}]
    _send(w, {"type": "ai", "op": "pause", "provider": "claude"})
    paused = (await _until(r, lambda m: m["type"] == "ai"))[-1]["rows"][0]
    assert (paused["state"], paused["text"], paused["on"], paused["enabled"]) == ("paused", "paused", False, True)
    assert d.access == "resting" and rest.hand("claude") is not None
    _send(w, {"type": "ai", "op": "resume", "provider": "claude"})
    resumed = (await _until(r, lambda m: m["type"] == "ai" and m["rows"][0]["state"] == "ready"))[-1]["rows"][0]
    assert resumed["on"] is True and d.access == "ready"
    _send(w, {"type": "ai", "op": "pause", "provider": "nobody"})   # not an AI: nothing happens
    _send(w, {"type": "ai", "op": "dance", "provider": "claude"})
    await asyncio.sleep(0.2)
    assert d.access == "ready"
    log = [json.loads(line) for line in agentd.paths.turns_log().read_text().splitlines()]
    assert [(e["prompt"], e["action"]) for e in log if e.get("kind") == "local"] == [
        ("pause Claude", "rest"), ("resume Claude", "rest")]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_row_says_when_its_limit_lifts_and_every_client_hears_a_change(home):
    cli, _, d, server, r, w, _ = await _rest_with(home)
    until = rest.limit("claude").until
    card = (await _until(r, lambda m: m["type"] == "ai"))[-1]["rows"][0]
    assert card["state"] == "limit" and card["text"] == f"at its limit until {rest.when(until)}" and card["on"] is True
    await _stop(server, w)


# -- apps --

@pytest.mark.asyncio
async def test_an_apps_newer_ask_replaces_its_older_waiting_one_and_other_asks_stay(home):
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "pause claude")
    await _until(r, _resting)
    for text in ("[from app notes] summarise this", "make me a tracker", "[from app mail] triage",
                 "[from app notes] summarise that"):
        await _ask(w, text)
    msgs = await _until(r, lambda m: m["type"] == "status" and len(m["queue"]) == 3 and m["queue"][-1]["turn"] == 4)
    assert [m["turn"] for m in msgs if m.get("kind") == "unqueued" and m.get("replaced")] == [1]
    assert [p for _i, p in d.pending] == ["make me a tracker", "[from app mail] triage",
                                          "[from app notes] summarise that"]
    await _ask(w, "resume claude")
    await _until(r, _ended)
    await _until(r, _ended)
    await _until(r, _ended)
    assert cli.prompts == ["make me a tracker", "[from app mail] triage", "[from app notes] summarise that"]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_an_app_ask_that_is_not_resting_is_never_replaced(home):
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "[from app notes] one")
    await _ask(w, "[from app notes] two")
    await _until(r, _ended)
    await _until(r, _ended)
    assert cli.prompts == ["[from app notes] one", "[from app notes] two"]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_removing_a_waiting_chip_forgets_what_its_cut_off_try_had_done(home):
    cli = Cli(home, mode="partial")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "make me a habit tracker")
    await _until(r, _resting)
    assert d._resume_notes.get(1)
    _send(w, {"type": "unqueue", "turn": 1})
    await _until(r, lambda m: m.get("kind") == "unqueued")
    assert 1 not in d._resume_notes and d.pending == []
    await _stop(server, w)


# -- a restart --

@pytest.mark.asyncio
async def test_a_limit_that_is_still_on_when_agentd_starts_rests_it_and_one_that_lapsed_does_not(home):
    rest.set_limit("claude", rest.Limit("limit", "seven_day", time.time() + 3600))
    d = agentd.AgentD(Scripted(Cli(home, mode="ok").script), agentd._NoSnapshots())
    server, r, w = await _start(d, "resting")
    assert "weekly limit" in d._setup_msg()["line"]
    await _stop(server, w)
    rest.set_limit("claude", rest.Limit("limit", "seven_day", time.time() - 3600))   # a time gone by: tried again soon
    rest.clear_limit("claude")
    d2 = agentd.AgentD(Scripted(Cli(home, mode="ok").script), agentd._NoSnapshots())
    d2.socket_path = home / "run" / "later.sock"
    server2, r2, w2 = await _start(d2)
    assert d2.access == "ready"
    await _stop(server2, w2)


async def _signed_in(d, state):
    server = asyncio.create_task(d.serve())
    for _ in range(300):
        if d.access == state:
            break
        await asyncio.sleep(0.05)
    return server


@pytest.mark.asyncio
async def test_a_sign_in_that_completes_clears_a_limit(needs_login):
    rest.set_limit("fake", rest.Limit("limit", "five_hour", time.time() + 3600))
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), auto_signin=True, panel=FakePanel())
    server = await _signed_in(d, "ready")     # signed out at the start: it signs in by itself (it may be another account)
    assert d.access == "ready" and rest.limit("fake") is None
    server.cancel()


@pytest.mark.asyncio
async def test_a_sign_in_that_completes_does_not_end_a_pause_by_hand(needs_login):
    rest.set_limit("fake", rest.Limit("limit", "five_hour", time.time() + 3600))
    rest.set_hand("fake")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), auto_signin=True, panel=FakePanel())
    server = await _signed_in(d, "resting")
    assert d.access == "resting" and d._rest_cur.why == "hand" and rest.limit("fake") is None
    assert (Path.home() / ".fake-signin").exists()
    server.cancel()

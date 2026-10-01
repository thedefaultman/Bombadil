"""agentd's finder while the AI rests: what a waiting ask is offered, and what a press does.

Same scripted CLI as test_agentd_rest.py: it refuses its first ask, so the machine rests. Nothing here calls a
model, the network or a window.
"""

import asyncio
import json

import pytest
from test_agentd import _ask
from test_agentd_rest import (Cli, _line, _rest_with, _send, _stop, _until, needs_login,  # noqa: F401
                              quiet_machine)  # noqa: F401

from bombadil import agentd, apps, launcher, paths, rest


def _found(m):
    return m.get("type") == "found"


def _unqueued(m):
    return m.get("kind") == "unqueued"


def _local_done(m):
    return m.get("kind") == "local" and m.get("phase") == "done"


def _past_ask(prompt, name="turn-1.jsonl", where=None):
    """A finished ask of the user's, with its steps in a log of agentd's own (or `where`, which is not)."""
    log = (where or paths.state_dir() / "turns") / name
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("{}\n")
    row = {"t": 1_790_000_000.0, "prompt": prompt, "ok": True, "summary": "Done.", "details": str(log)}
    paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
    with paths.turns_log().open("a") as f:
        f.write(json.dumps(row) + "\n")
    return log


async def _opened(d, monkeypatch):
    """Record what a press runs instead of opening a window."""
    seen = []
    monkeypatch.setattr(d.launcher, "run", lambda action: (seen.append(action) or True, "Opened it."))
    monkeypatch.setattr(d.launcher, "details", lambda argv, follow=False: seen.append(argv))
    return seen


# -- what is offered --

@pytest.mark.asyncio
async def test_a_sentence_kept_while_it_rests_is_offered_the_app_it_nearly_names(home):
    apps.create("Passwords", "import QtQuick\nItem {}\n", description="Keeps your logins")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    until = rest.limit("claude").until
    await _ask(w, "where do I keep my logins")
    found = (await _until(r, _found))[-1]
    assert found["turn"] == 2 and found["prompt"] == "where do I keep my logins"
    assert found["line"] == f"Kept for {rest.when(until)}. Found on this computer:"
    assert found["matches"] == [{"id": "1", "kind": "app", "label": "Passwords", "hint": "App"}]
    await asyncio.sleep(0.2)
    assert cli.prompts == ["make me a habit tracker"]                # no model was asked
    assert [i for i, _p in d.pending] == [1, 2]                      # and the ask is still kept
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_sentence_that_names_nothing_here_says_so_and_is_still_kept(home):
    cli, _, d, server, r, w, _ = await _rest_with(home)
    await _ask(w, "what is the capital of france")
    found = (await _until(r, _found))[-1]
    assert found["matches"] == [] and found["line"].endswith("Nothing on this computer matches.")
    assert found["line"].startswith("Kept for ")
    assert [p for _i, p in d.pending][-1] == "what is the capital of france"
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_past_ask_is_offered_with_its_day_and_a_pause_by_hand_says_until_you_resume(home):
    _past_ask("file the march invoice")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    await _ask(w, "pause claude")
    await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    await _ask(w, "the march invoice")
    found = (await _until(r, _found))[-1]
    assert found["line"] == "Kept until you resume Claude. Found on this computer:"
    assert [(m["kind"], m["hint"]) for m in found["matches"]] == [("ask", "Its steps")]
    assert found["matches"][0]["label"].startswith("You asked: file the march invoice (")
    await _stop(server, w)


@pytest.mark.asyncio
async def test_only_what_the_user_typed_in_the_pill_is_looked_for(home):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    await _ask(w, "[from app notes] summarise my passwords")      # an app's ask
    await _ask(w, "!echo passwords")                              # a command
    _send(w, {"type": "prompt", "text": "my passwords please", "asked_by": "bombadil"})   # a terminal's ask
    await asyncio.sleep(0.5)
    await _ask(w, "my password app")
    found = (await _until(r, _found))[-1]
    assert found["prompt"] == "my password app"                   # the first one found is the pill's own
    await _stop(server, w)


@pytest.mark.asyncio
async def test_nothing_is_offered_when_the_ask_simply_runs(home):
    from test_agentd import Scripted, _start
    cli = Cli(home, mode="ok")
    d = agentd.AgentD(Scripted(cli.script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "where do I keep my logins")
    msgs = await _until(r, lambda m: m.get("kind") == "turn_end")
    assert not [m for m in msgs if _found(m)] and d._found == {}
    await _stop(server, w)


# -- a press --

@pytest.mark.asyncio
async def test_pressing_an_app_opens_it_like_its_typed_word_and_lets_go_of_the_kept_ask(home, monkeypatch):
    apps.create("Passwords", "import QtQuick\nItem {}\n", description="Keeps your logins")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "where do I keep my logins")
    found = (await _until(r, _found))[-1]
    _send(w, {"type": "found_open", "turn": found["turn"], "id": "1"})
    msgs = await _until(r, _local_done)
    assert [m["turn"] for m in msgs if _unqueued(m)] == [2]
    assert [(a.kind, a.target) for a in seen] == [("app", "passwords")]
    assert msgs[-1]["ok"] is True and [i for i, _p in d.pending] == [1]    # only the refused first ask is left
    assert d._found == {} and d.access == "resting"
    await _stop(server, w)


@pytest.mark.asyncio
async def test_pressing_a_launcher_word_runs_it(home, monkeypatch):
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "where is the browser please")
    found = (await _until(r, _found))[-1]
    assert [(m["kind"], m["label"]) for m in found["matches"]] == [("word", "Browser")]
    _send(w, {"type": "found_open", "turn": found["turn"], "id": "1"})
    await _until(r, _local_done)
    assert [a.kind for a in seen] == [a.kind for a in [launcher.match("browser", [])]]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_pressing_a_past_ask_shows_its_steps_and_lets_go_of_the_kept_ask(home, monkeypatch):
    log = _past_ask("file the march invoice")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "the march invoice")
    found = (await _until(r, _found))[-1]
    _send(w, {"type": "found_open", "turn": found["turn"], "id": "1"})
    msgs = await _until(r, _unqueued)
    await asyncio.sleep(0.2)
    assert [m["turn"] for m in msgs if _unqueued(m)] == [2]
    assert len(seen) == 1 and seen[0][-2:] == ["--file", str(log.resolve())]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_log_that_is_not_agentds_own_is_never_shown_and_the_ask_stays_kept(home, monkeypatch, tmp_path):
    _past_ask("file the march invoice", where=tmp_path)
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "the march invoice")
    found = (await _until(r, _found))[-1]
    _send(w, {"type": "found_open", "turn": found["turn"], "id": "1"})
    msgs = await _until(r, _local_done)
    assert msgs[-1]["ok"] is False and "not on this computer" in msgs[-1]["text"]
    assert seen == [] and [i for i, _p in d.pending] == [1, 2]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_press_on_something_that_was_not_offered_does_nothing(home, monkeypatch):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "my password app")
    found = (await _until(r, _found))[-1]
    for bad in ({"turn": found["turn"], "id": "2"}, {"turn": found["turn"], "id": "../1"},
                {"turn": 99, "id": "1"}, {"turn": True, "id": "1"}, {"turn": None, "id": "1"}, {"id": "1"}):
        _send(w, {"type": "found_open", **bad})
    await asyncio.sleep(0.5)
    assert seen == [] and [i for i, _p in d.pending] == [1, 2]
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_chip_whose_ask_is_gone_opens_nothing_and_dropping_the_ask_forgets_what_was_offered(home, monkeypatch):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "my password app")
    found = (await _until(r, _found))[-1]
    _send(w, {"type": "unqueue", "turn": found["turn"]})           # the x on the kept chip
    await _until(r, _unqueued)
    assert d._found == {}
    _send(w, {"type": "found_open", "turn": found["turn"], "id": "1"})
    await asyncio.sleep(0.4)
    assert seen == []
    await _stop(server, w)


@pytest.mark.asyncio
async def test_a_press_after_the_ask_has_started_opens_nothing(home, monkeypatch):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    cli, _, d, server, r, w, _ = await _rest_with(home)
    seen = await _opened(d, monkeypatch)
    await _ask(w, "my password app")
    found = (await _until(r, _found))[-1]
    d.pending = [(i, p) for i, p in d.pending if i != found["turn"]]    # it was taken to run
    _send(w, {"type": "found_open", "turn": found["turn"], "id": "1"})
    await asyncio.sleep(0.4)
    assert seen == []
    await _stop(server, w)

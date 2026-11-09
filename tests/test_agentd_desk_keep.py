"""agentd and the Widgets card: the shell's presses (set, keep, reset), the words ("keep machine", "widgets"),
the agent's `desk` tool, and a kept Machine that is sampled in its calm shape. The harness is test_agentd.py's."""

import asyncio
import json
import tomllib

import pytest
from test_agentd import (
    DESK_TURN,
    MACHINE_UP,
    FakeVitals,
    Scripted,
    _all_of,
    _another,
    _ask,
    _desk_result,
    _desk_state,
    _events_until,
    _in_a_turn,
    _is_done,
    _read_until,
    _say,
    _silent,
    _start,
    _stop,
)

from bombadil import agentd, desk, paths, procs, providers


def _agentd(**k):
    return agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), **k)


async def _line(r):
    """The one line the pill says for a press on the Widgets card."""
    msgs = await _all_of(r, _is_done, lambda m: m.get("type") == "desk")
    return next(m for m in msgs if _is_done(m)), next(m for m in msgs if m.get("type") == "desk")


@pytest.mark.asyncio
async def test_a_press_on_the_widgets_card_sets_the_state_tells_everyone_and_says_what_it_did(home):
    d = _agentd()
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "keep"})
    line, state = await _line(r)
    assert (line["turn"], line["action"], line["target"], line["ok"], line["text"]) == (
        None, "desk", "machine", True, "Machine stays on the desk.")
    assert state["kept"] == ["machine"] and state == d.desk.snapshot()
    assert (await _desk_state(other_r))["kept"] == ["machine"]
    assert tomllib.loads(paths.desk_file().read_text())["kept"] == ["machine"]
    # Off from kept is one press, and the desk says it.
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "off"})
    line, state = await _line(r)
    assert line["text"] == "Put Machine away." and state["kept"] == [] and "machine" in state["hidden"]
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "needed"})
    line, state = await _line(r)
    assert line["text"] == "Put Machine on the desk." and "machine" not in state["hidden"]
    for x in (w, other_w):
        x.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_press_that_changes_nothing_or_is_refused_still_says_so_and_tells_no_one_the_desk(home):
    d = _agentd()
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    for msg, ok, text in [
        ({"op": "set", "widget": "needs", "state": "off"}, False, "Needs you cannot be hidden."),
        ({"op": "set", "widget": "machine", "state": "needed"}, True, "Machine is already on the desk."),
        ({"op": "set", "widget": "now", "state": "keep"}, True, "Now shows only while a turn has steps to follow."),
        ({"op": "set", "widget": "machine", "state": "loud"}, False, "A widget is kept, shown when needed, or off."),
        ({"op": "set", "widget": "machine"}, False, "A widget is kept, shown when needed, or off."),
        ({"op": "set", "widget": "sofa", "state": "off"}, False, None),
        ({"op": "keep", "widget": "now"}, True, "Now shows only while a turn has steps to follow."),
        ({"op": "reset"}, True, "The desk is already as it came."),
    ]:
        await _say(w, {"type": "desk", **msg})
        got = (await _events_until(r, _is_done, 5))[-1]
        assert got["ok"] is ok and (text is None or got["text"] == text), (msg, got)
    assert await _silent(r)                                         # the desk did not change: it is not sent again
    heard = []                                                      # the other bar hears the lines, never the desk
    try:
        while True:
            heard.append(json.loads(await asyncio.wait_for(other_r.readline(), 0.3)))
    except TimeoutError:
        pass
    assert heard and not [m for m in heard if m.get("type") == "desk"]
    assert len([m for m in heard if _is_done(m)]) == 8
    assert d.desk.snapshot() == desk.Desk().snapshot()
    assert not paths.desk_file().exists()
    for x in (w, other_w):
        x.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_second_press_of_off_says_so_and_does_not_send_the_desk_again(home):
    d = _agentd()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "off"})
    line, state = await _line(r)
    assert line["text"] == "Put Machine away." and "machine" in state["hidden"]
    stamp = paths.desk_file().stat().st_mtime_ns
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "off"})
    got = (await _events_until(r, _is_done, 5))[-1]
    assert got["ok"] is True and got["text"] == "Machine is already put away."
    assert await _silent(r) and paths.desk_file().stat().st_mtime_ns == stamp
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_press_that_is_not_the_cards_is_still_silent_on_the_line(home):
    d = _agentd()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "hide", "widget": "machine"})        # a gesture, not a press
    await _desk_state(r)
    assert await _silent(r)                                                    # nothing on the line
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_reset_from_the_card_puts_the_whole_desk_back_and_leaves_the_screen(home):
    d = _agentd()
    d.desk.screen = "DP-1"
    d.desk.apply("keep", "machine")
    d.desk.apply("hide", "now")
    d.desk.apply("move", "watching", "right", 0)
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "reset"})
    line, state = await _line(r)
    assert line["text"] == "The desk is back as it came." and line["ok"] is True
    assert state == desk.Desk().snapshot() | {"screen": "DP-1"}
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_press_is_told_to_the_next_turn_like_any_change_at_the_desk(home):
    d = _agentd()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "keep"})
    await _line(r)
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    said = next(m["text"] for m in msgs if m.get("kind") == "result")
    assert said == "echo: [Done by the user without you since your last turn: at the desk: Machine stays on the desk.] hello"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_word_widgets_asks_every_bar_for_the_card_and_never_starts_a_turn(home):
    d = _agentd()
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    await _ask(w, "widgets")
    for rd in (r, other_r):
        msgs = await _events_until(rd, lambda m: m.get("type") == "widgets", 5)
        assert msgs[-1] == {"type": "widgets", "op": "open"}
    msgs = await _all_of(r, _is_done)
    done = next(m for m in msgs if _is_done(m))
    assert done["ok"] is True and done["text"] == "Here are the widgets."
    assert d.turns == 0 and d.current is None
    assert not [m for m in msgs if m.get("type") == "desk"]           # the desk is as it was: nothing resent
    for x in (w, other_w):
        x.close()
    server.cancel()


@pytest.mark.asyncio
async def test_widgets_asked_as_a_question_is_for_the_agent_and_opens_no_card(home):
    d = _agentd()
    server, r, w = await _start(d)
    await _ask(w, "widgets?")
    msgs = await _read_until(r, "turn_end")
    assert not [m for m in msgs if m.get("type") == "widgets"] and d.turns == 1
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_keep_machine_in_words_keeps_it_says_so_and_never_starts_a_turn(home):
    d = _agentd()
    server, r, w = await _start(d)
    await _ask(w, "keep machine on the desk")
    assert json.loads(await r.readline()) == {"type": "local", "action": "widget"}
    msgs = await _all_of(r, _is_done, lambda m: m.get("type") == "desk")
    local = [m for m in msgs if m.get("kind") == "local"]
    assert [(m["phase"], m["text"]) for m in local] == [("start", "Keeping Machine on the desk"),
                                                        ("done", "Machine stays on the desk.")]
    assert next(m for m in msgs if m.get("type") == "desk")["kept"] == ["machine"] and d.turns == 0
    # And "keep watching" answers and changes nothing: no desk message, no file.
    await _ask(w, "keep watching")
    msgs = await _all_of(r, _is_done)
    assert [m["text"] for m in msgs if _is_done(m)] == ["Watching shows only while something counts."]
    assert await _silent(r) and d.desk.snapshot()["kept"] == ["machine"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_kept_machine_is_sampled_calm_and_a_machine_no_longer_kept_is_not(home):
    fake = FakeVitals(dict(MACHINE_UP, calm=True), delay=0.05)
    d = _agentd(vitals=fake)
    server, r, w = await _start(d)
    assert fake.kept is False
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "keep"})
    await _desk_state(r)
    for _ in range(100):
        if fake.kept:
            break
        await asyncio.sleep(0.02)
    assert fake.kept is True
    await _say(w, {"type": "desk", "op": "set", "widget": "machine", "state": "needed"})
    await _desk_state(r)
    for _ in range(100):
        if not fake.kept:
            break
        await asyncio.sleep(0.02)
    assert fake.kept is False
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_desk_that_was_kept_before_a_restart_is_sampled_calm_from_the_first_bar(home):
    first = desk.Desk()
    first.apply("keep", "machine")
    fake = FakeVitals(dict(MACHINE_UP, calm=True), delay=0.05)
    d = _agentd(vitals=fake)
    assert d.desk.snapshot()["kept"] == ["machine"]
    server, r, w = await _start(d)
    msg = (await _events_until(r, lambda m: m.get("type") == "machine", 5))[-1]
    assert msg["calm"] is True and fake.kept is True           # the very first message is the calm card
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_machine_put_away_is_not_sampled_even_when_it_was_kept(home):
    fake = FakeVitals(delay=0.02)
    d = _agentd(vitals=fake)
    d.desk.apply("keep", "machine")
    d.desk.apply("hide", "machine")                  # no longer kept
    d.desk.apply("hide", "machine")                  # put away
    server, r, w = await _start(d)
    await asyncio.sleep(0.2)
    assert fake.ticks == 0 and fake.kept is False
    w.close()
    server.cancel()


# -- the agent's own tool --

@pytest.mark.asyncio
async def test_the_desk_tool_keeps_sets_resets_and_opens_the_card_in_a_turn_that_asked_for_the_desk(home):
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    opened = []
    d.desk.on_widgets = lambda: opened.append(1)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "please keep the machine on my desk")
    for i, (msg, ok, text) in enumerate([
        ({"op": "keep", "widget": "machine"}, True, "Machine stays on the desk."),
        ({"op": "keep", "widget": "machine"}, True, "Machine is already kept on the desk."),
        ({"op": "set", "widget": "machine", "state": "off"}, True, "Put Machine away."),
        ({"op": "set", "widget": "machine", "state": "needed"}, True, "Put Machine on the desk."),
        ({"op": "set", "widget": "machine", "state": "keep"}, True, "Machine stays on the desk."),
        ({"op": "hide", "widget": "machine"}, True,
         "Machine is no longer kept. It still comes up when something needs a look."),
        ({"op": "set", "widget": "needs", "state": "off"}, False, "Needs you cannot be hidden."),
        ({"op": "set", "widget": "machine", "state": "loud"}, False, "A widget is kept, shown when needed, or off."),
        ({"op": "widgets"}, True, "Here are the widgets."),
        ({"op": "set", "widget": "now", "state": "off"}, True, "Put Now away."),
        ({"op": "reset"}, True, "The desk is back as it came."),
        ({"op": "reset"}, True, "The desk is already as it came."),
    ]):
        await _say(tool_w, {"type": "desk-tool", "id": f"k{i}", "turn": 1, **msg})
        got = await _desk_result(tool_r)
        assert (got["id"], got["ok"], got["text"]) == (f"k{i}", ok, text)
    assert opened == [1]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_desk_tool_cannot_keep_in_a_turn_that_did_not_ask_for_the_desk(home):
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "how much memory is free?")
    for msg in ({"op": "keep", "widget": "machine"}, {"op": "set", "widget": "machine", "state": "keep"},
                {"op": "reset"}, {"op": "widgets"}):
        await _say(tool_w, {"type": "desk-tool", "id": "n", "turn": 1, **msg})
        got = await _desk_result(tool_r)
        assert got["ok"] is False and "did not ask for the desk" in got["text"]
    assert d.desk.snapshot() == desk.Desk().snapshot()
    await _stop(r, w, server, tool_w)

"""agentd's Away card: what it is built from, who is told, and what puts it away.

The card itself (rows, order, wording) is tested in test_away.py; this is about the daemon around it. Nothing
here calls a model, the network or a window: the turns are the fake provider's.
"""

import asyncio
import json
import time

import pytest
from test_agentd import (_another, _ask, _events_until, _finish, _heard, _in_a_turn, _jobs_daemon, _say, _silent,
                         _start, _start_job, _stop)

from bombadil import agentd, away, paths, providers


def _away(m):
    return m.get("type") == "away"


async def _card(r, timeout=5):
    return (await _events_until(r, _away, timeout))[-1]


def _seed(*rows):
    """Rows in the turn log, as agentd wrote them while the person was gone."""
    path = paths.turns_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _turn(ago, prompt, ok=True, **more):
    return {"t": time.time() - ago, "prompt": prompt, "provider": "fake", "ok": ok, "result": "", **more}


def _titles(card):
    return [row["title"] for row in card["rows"]]


def _daemon():
    return agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())


@pytest.mark.asyncio
async def test_show_away_builds_the_card_from_the_turn_log_and_everyone_is_told(home):
    _seed(_turn(300, "build the site", ok=False, result="the build broke\nmore"),
          _turn(200, "tidy the notes"))
    d = _daemon()
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    card = await _card(r)
    assert _titles(card) == ["build the site", "tidy the notes"]      # a failure first, then what finished
    assert card["rows"][0]["sub"] == "the build broke" and card["rows"][0]["tone"] == "red"
    assert card["count"] == 2 and card["since"] > 0
    assert await _card(other_r) == card                               # the other bar hears the same card
    assert d.away.up and d.turns == 2 and d.current is None and not d.pending   # nothing reached the model
    w.close()
    other_w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_asking_when_nothing_happened_is_still_a_card(home):
    d = _daemon()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    card = await _card(r)
    assert _titles(card) == ["Nothing happened"] and card["count"] == 0
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_new_bar_and_the_desks_reply_hear_a_card_that_is_up_and_not_one_that_is_not(home):
    _seed(_turn(100, "tidy the notes"))
    d = _daemon()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "get"})
    got = [json.loads(await asyncio.wait_for(r.readline(), 5)) for _ in range(2)]
    assert [m["type"] for m in got] == ["desk", "jobs"] and await _silent(r)    # no card: nothing sent
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    card = await _card(r)
    await _say(w, {"type": "desk", "op": "get"})
    got = [json.loads(await asyncio.wait_for(r.readline(), 5)) for _ in range(3)]
    assert [m["type"] for m in got] == ["desk", "jobs", "away"] and got[2] == card
    late_r, late_w = await _another(d)
    assert await _card(late_r) == card                                          # a bar that comes later
    late_w.close()
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_shell_saying_seen_puts_the_card_away_for_everyone_once(home):
    _seed(_turn(100, "tidy the notes"))
    d = _daemon()
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    await _card(r)
    await _card(other_r)
    await _say(w, {"type": "away", "op": "seen"})
    assert (await _card(r))["rows"] == [] and (await _card(other_r))["rows"] == []
    assert not d.away.up and d.away.seen_at is not None
    assert paths.state_dir().joinpath("away.json").exists()          # remembered across a restart
    await _say(w, {"type": "away", "op": "seen"})                    # a second click has nothing to put away
    assert await _silent(r) and await _silent(other_r)
    w.close()
    other_w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_what_was_seen_is_not_news_again_and_agentd_remembers_it_across_a_restart(home):
    _seed(_turn(500, "build the site", ok=False, result="broke"))
    d = _daemon()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    assert _titles(await _card(r)) == ["build the site"]
    await _say(w, {"type": "away", "op": "seen"})
    await _card(r)
    seen_at = d.away.seen_at
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})      # nothing has happened since
    card = await _card(r)
    assert _titles(card) == ["Nothing happened"] and card["since"] == pytest.approx(seen_at, abs=1e-6)
    assert _daemon().away.seen_at == pytest.approx(seen_at, abs=1e-6)             # a new agentd starts from there
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_shell_can_ask_for_the_card_again_and_only_the_asker_is_told(home):
    d = _daemon()
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    await _say(w, {"type": "away", "op": "get"})
    assert (await _card(r))["rows"] == []                           # no card is a card with no rows
    assert await _silent(other_r)
    await _say(w, {"type": "away", "op": "nonsense"})
    await _say(w, {"type": "away"})
    assert await _silent(r)
    w.close()
    other_w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_persons_next_ask_puts_the_card_away(home):
    _seed(_turn(100, "tidy the notes"))
    d = _daemon()
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    await _card(r)
    await _ask(w, "tell me a joke")
    assert (await _card(r))["rows"] == [] and not d.away.up
    assert d.away.seen_at is not None
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_ask_from_a_coding_session_or_an_agents_process_leaves_the_card_up(home):
    _seed(_turn(100, "tidy the notes"))
    d = _daemon()
    agent = {"now": False}
    d._was_agent = lambda pid: agent["now"]     # who connected: nobody in a turn, until the test says
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "show", "widget": "away"})
    await _card(r)
    # A coding session's ask comes with its name, from a client that is nobody's agent.
    await _say(w, {"type": "prompt", "text": "run the checks", "asked_by": "builder"})
    await _events_until(r, lambda m: m.get("kind") == "turn_end", 10)
    assert d.away.up and d._typed_last is None
    # And a process of an agent's turn says nothing of itself: it is told by who connected.
    agent["now"] = True
    await _ask(w, "and what else")
    await _events_until(r, lambda m: m.get("kind") == "turn_end", 10)
    assert d.away.up and d.away.seen_at is None
    assert not [m for m in await _heard(r) if _away(m)]
    # Their asks are not the person at the pill either: the time the person was last there is not theirs.
    assert d._typed_last is None and d._typed_before is None
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_card_reaches_back_to_the_ask_before_this_one(home):
    d = _daemon()
    server, r, w = await _start(d)
    await _ask(w, "tell me a joke")
    await _events_until(r, lambda m: m.get("kind") == "turn_end")
    first = d._typed_last
    assert first is not None and d._typed_before is None
    # One row from before that ask, one from after it.
    _seed({"t": first - 60, "prompt": "an old build", "ok": False, "result": "broke"},
          {"t": time.time() + 0.5, "prompt": "a newer build", "ok": False, "result": "broke"})
    await _ask(w, "show away")
    card = await _card(r)
    assert d._typed_before == first and d._typed_last > first
    assert card["since"] == pytest.approx(first, abs=1e-6)
    # The first ask's own turn ended after it, so it is in the card only when the card reaches back that far
    # (from the later ask it would not be).
    assert "tell me a joke" in _titles(card)
    assert "a newer build" in _titles(card) and "an old build" not in _titles(card)
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_job_that_ended_names_itself_in_the_log_and_a_stop_is_marked_as_the_persons_own(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    stopped, _ = await _start_job(tool_r, tool_w, "Mirror")
    ended, _ = await _start_job(tool_r, tool_w, "Build", "make")
    await _say(w, {"type": "jobs", "op": "stop", "id": stopped})
    _finish(ended, 1, "boom\n")
    for _ in range(200):
        rows = [json.loads(x) for x in paths.turns_log().read_text().splitlines()] if paths.turns_log().exists() else []
        if len([x for x in rows if x.get("action") == "job"]) >= 2:
            break
        await asyncio.sleep(0.02)
    rows = {x["target"]: x for x in rows if x.get("action") == "job"}
    assert rows["Build"]["job"] == ended and rows["Build"]["ok"] is False and "stopped" not in rows["Build"]
    assert rows["Mirror"]["stopped"] is True
    # So the card: the one that failed is news, the one they stopped is not.
    found = away.items(away.read_tail(), since=0, now=time.time() + 5)
    assert [(i["title"], i["kind"], i.get("job")) for i in found] == [("Build", "failed", ended)]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_away_sessions_is_empty_until_agentd_keeps_coding_sessions(home):
    assert _daemon()._away_sessions() == []

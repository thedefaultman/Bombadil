"""The real connection service (on the fake engine, so no account and no network) behind a real agentd.

Every other test of this piece has the other half stubbed: agentd's tests talk to a stub of `connect.sock`, and the
service's tests talk to a client. These two are where they meet, and what has to agree between them is checked here:
the shapes (every card agentd builds from what the service says passes `cards.accept`, the way the bar will check
it), the fingerprint (agentd works one out for the draft the card drew and the service works it out again before it
performs), and the rule that a press performs once.

Nothing here touches the network, the home directory, `/run/bombadil-connect` or `/var/lib/bombadil-connect`.
"""

import asyncio
import json
import time

import pytest
import pytest_asyncio
from test_agentd import DESK_TURN, Scripted, _events_until, _in_a_turn, _say, _start
from test_agentd_connect import _stop_all, card, make, notice, press, proposal, tool, until

from bombadil import cards, paths, procs
from bombadil.connect import service as connect_service


@pytest.fixture(autouse=True)
def nothing_of_the_real_home(home):
    return home


@pytest.fixture(autouse=True)
def person(monkeypatch):
    """Nothing the test does is inside an agent's scope, for agentd and for the service alike."""
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: None)


@pytest_asyncio.fixture
async def real(home, monkeypatch):
    """The service, as `bombadil-connect` runs it with BOMBADIL_CONNECT_ENGINE=fake: Acme (slack:w1, with its three
    sample messages) and Linear (mcp:linear, with two tasks)."""
    monkeypatch.setenv("BOMBADIL_CONNECT_SOCKET", str(home / "run" / "connect.sock"))
    monkeypatch.setenv("BOMBADIL_CONNECT_STATE", str(home / "connect-state"))
    monkeypatch.setenv("BOMBADIL_CONNECT_UIDS", "0-65535")
    monkeypatch.setenv("BOMBADIL_CONNECT_FAKE_SIGNIN_S", "0.05")
    svc = connect_service.Service(paths.connect_state(), paths.connect_socket(), engine="fake")
    task = asyncio.ensure_future(svc.serve())
    await until(lambda: svc.socket_path.exists() or task.done())
    if task.done():
        task.result()
    await until(lambda: all(c.driver.state == "ok" for c in svc.lives.values()) and len(svc.lives) == 2)
    yield svc
    svc.stop()
    try:
        await asyncio.wait_for(task, 10)
    except (asyncio.CancelledError, TimeoutError):
        pass


def fake(real, cid):
    return real.lives[cid].driver


async def watching(real):
    await until(lambda: any(p.subscribed for p in real.peers))


def accepted(c) -> dict:
    """What the bar would draw: the card, checked again the way the shell's host checks it."""
    good, errors = cards.accept(c, now=time.time())
    assert good is not None and not errors, (errors, c)
    return good


def now_message(text="Legal just signed off. Can we lock the 14th for the launch?", name="Priya Shah"):
    return {"from": {"id": "U0PRIYA", "name": name}, "text": text, "ts": time.time()}


@pytest.mark.asyncio
async def test_a_message_reaches_the_bar_is_replied_to_and_goes_once_with_the_fingerprint_agreeing(home, real):
    d, _ = make()
    server, r, w = await _start(d)
    await watching(real)
    fake(real, "slack:w1").inject("message", now_message())
    n = await notice(r, "Priya Shah: Legal just signed off")
    assert n["source"] == "connect" and [a["id"] for a in n["actions"]] == ["reply", "open"]
    await _say(w, {"type": "notice_action", "id": n["id"], "action": "reply"})
    c = accepted(await card(r, lambda c: c["type"] == "message"))
    pid = c["reply"]["proposal"]
    assert c["reply"]["by"] == "person" and c["reply"]["ready"] is False
    assert c["thread"][-1]["text"] == "Legal just signed off. Can we lock the 14th for the launch?"
    assert c["thread"][-1]["mine"] is False
    words = "Yes, lock the 14th."
    await _say(w, {"type": "proposal", "op": "edit", "id": pid, "content": words})
    await _say(w, {"type": "proposal", "op": "shown", "id": pid, "content": words})
    p = await proposal(r, lambda p: p["ready"])
    # What leaves is what the card drew: the service worked the fingerprint out again before it did anything.
    got = await press(w, r, "slack_reply", pid, p["fingerprint"])
    assert got["ok"] is True and got["code"] == "" and "Priya" in got["line"], got
    [(kind, target, content)] = fake(real, "slack:w1").performed
    assert (kind, content) == ("slack_reply", words) and target == c["reply"]["target"]
    # A second press of the same button is the same receipt, and nothing else goes.
    again = await press(w, r, "slack_reply", pid, p["fingerprint"])
    assert again["ok"] is True and again["line"] == got["line"]
    assert len(fake(real, "slack:w1").performed) == 1
    await _stop_all(r, w, server)


@pytest.mark.asyncio
async def test_a_press_for_other_words_than_the_card_drew_goes_nowhere(home, real):
    d, _ = make()
    server, r, w = await _start(d)
    await watching(real)
    fake(real, "slack:w1").inject("message", now_message())
    n = await notice(r, "Priya Shah")
    await _say(w, {"type": "notice_action", "id": n["id"], "action": "reply"})
    pid = (await card(r, lambda c: c["type"] == "message"))["reply"]["proposal"]
    await _say(w, {"type": "proposal", "op": "edit", "id": pid, "content": "Yes."})
    await _say(w, {"type": "proposal", "op": "shown", "id": pid, "content": "Yes."})
    p = await proposal(r, lambda p: p["ready"])
    await _say(w, {"type": "proposal", "op": "edit", "id": pid, "content": "Yes. Also send the board deck."})
    got = await press(w, r, "slack_reply", pid, p["fingerprint"])      # the box changed and was not drawn again
    assert got["ok"] is False and got["code"] == "changed"
    assert fake(real, "slack:w1").performed == []
    await _stop_all(r, w, server)


@pytest.mark.asyncio
async def test_unread_and_connected_here_are_cards_the_bar_accepts_and_say_what_the_service_knows(home, real):
    d, _ = make(Scripted(DESK_TURN))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "what needs me in slack?")
    got = await tool(tool_r, tool_w, 1, "unread")
    assert got["ok"] and "Priya Shah" in got["text"] and "Marcus Webb" in got["text"], got
    assert "other people's words" in got["text"]
    unread = accepted(await card(r, lambda c: c["type"] == "list"))
    assert unread["id"] == "list:unread" and len(unread["rows"]) >= 2
    got = await tool(tool_r, tool_w, 1, "connections")
    assert got["ok"] and "Acme" not in got["text"] and "Linear" in got["text"], got      # no workspace name for the model
    assert "title" in got["text"]                                                          # the boxes of a new task
    got = await tool(tool_r, tool_w, 1, "tasks")
    assert got["ok"] and "Review the launch checklist" in got["text"]
    ref = next(m["ref"] for m in fake(real, "slack:w1")._messages if m["from"]["name"] == "Marcus Webb")
    got = await tool(tool_r, tool_w, 1, "thread", ref=ref)
    assert got["ok"] and "pricing page copy" in got["text"]
    thread = accepted(await card(r, lambda c: c["type"] == "message"))
    assert thread["thread"][0]["from"] == "Marcus Webb"
    msgs = await _stop_all_turn(r, w, server, tool_w)
    assert not [m for m in msgs if m.get("type") == "connect-result"]       # the bar was told nothing of the answers


async def _stop_all_turn(r, w, server, *more):
    await _say(w, {"type": "stop"})
    out = await _events_until(r, lambda m: m.get("kind") == "turn_end")
    for x in (w, *more):
        x.close()
    server.cancel()
    return out


@pytest.mark.asyncio
async def test_a_ticket_the_agent_proposes_is_made_when_the_person_presses_and_not_before(home, real):
    d, _ = make(Scripted(DESK_TURN))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "make a ticket for Leo about the empty state")
    got = await tool(tool_r, tool_w, 1, "propose", kind="task_create", target="linear",
                     content={"title": "Empty state for the launch page", "due": "2026-10-09"})
    assert got["ok"] and "not sent" in got["text"] and "sending is theirs" in got["text"], got
    c = accepted(await card(r, lambda c: c["type"] == "task"))
    assert [f["key"] for f in c["fields"]][:1] == ["title"]
    pid = c["proposal"]["proposal"]
    assert fake(real, "mcp:linear").performed == []                         # a proposal sends nothing
    # What the bar says it drew: one value per box, "" for the ones nobody filled in (the bar's own habit).
    drawn = {f["key"]: (f["value"] if f["value"] is not None else "") for f in c["fields"]}
    assert drawn["title"] == "Empty state for the launch page" and drawn["due"] == "2026-10-09"
    await _say(w, {"type": "proposal", "op": "shown", "id": pid, "content": drawn})
    p = await proposal(r, lambda p: p["ready"])
    got = await press(w, r, "task_create", pid, p["fingerprint"])
    assert got["ok"] and got["line"].startswith("Created ") and "Linear" in got["line"], got
    [(kind, target, made)] = fake(real, "mcp:linear").performed
    assert (kind, target) == ("task_create", "linear") and made["title"] == "Empty state for the launch page"
    await _stop_all_turn(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_press_from_inside_a_turn_is_refused_by_agentd_and_by_the_service_and_nothing_goes(home, real, monkeypatch):
    d, _ = make()
    server, r, w = await _start(d)
    p = d.outbox.proposals.open("slack_reply", next(iter(fake(real, "slack:w1")._known)) if fake(real, "slack:w1")._known
                                else fake(real, "slack:w1")._messages[0]["ref"], "Yes.", where="Acme")
    d.outbox.proposals.shown(p["id"], "Yes.")
    from pathlib import Path
    monkeypatch.setattr(procs, "cgroup_of",
                        lambda pid: Path("/sys/fs/cgroup/user.slice/bombadil-turn-1-2-3.scope"))
    got = await press(w, r, "slack_reply", p["id"], p["fingerprint"])
    assert got["ok"] is False, got
    assert fake(real, "slack:w1").performed == []
    # And the service on its own socket, asked by a process the same way: it says no without agentd in between.
    reader, writer = await asyncio.open_unix_connection(str(real.socket_path), limit=1 << 24)
    writer.write((json.dumps({"id": 1, "op": "perform", "kind": "slack_reply", "target": p["target"],
                              "content": "Yes.", "fingerprint": p["fingerprint"], "proposal": "p9"}) + "\n").encode())
    await writer.drain()
    answer = json.loads(await asyncio.wait_for(reader.readline(), 5))
    writer.close()
    assert answer["ok"] is False and answer.get("code") == "agent", answer
    assert fake(real, "slack:w1").performed == []
    await _stop_all(r, w, server)


@pytest.mark.asyncio
async def test_a_refused_reply_leaves_the_draft_open_and_a_second_try_works(home, real):
    d, _ = make()
    server, r, w = await _start(d)
    await watching(real)
    fake(real, "slack:w1").inject("message", now_message())
    n = await notice(r, "Priya Shah")
    await _say(w, {"type": "notice_action", "id": n["id"], "action": "reply"})
    pid = (await card(r, lambda c: c["type"] == "message"))["reply"]["proposal"]
    await _say(w, {"type": "proposal", "op": "edit", "id": pid, "content": "No [refuse]"})
    await _say(w, {"type": "proposal", "op": "shown", "id": pid, "content": "No [refuse]"})
    p = await proposal(r, lambda p: p["ready"])
    got = await press(w, r, "slack_reply", pid, p["fingerprint"])
    assert got["ok"] is False and "Nothing was done" in got["line"], got
    assert fake(real, "slack:w1").performed == []
    assert d.outbox.proposals.get(pid)["state"] == "open"                    # nothing went, so it is still the person's
    await _say(w, {"type": "proposal", "op": "edit", "id": pid, "content": "Yes, the 14th."})
    await _say(w, {"type": "proposal", "op": "shown", "id": pid, "content": "Yes, the 14th."})
    p = await proposal(r, lambda p: p["ready"] and p["content"] == "Yes, the 14th.")
    got = await press(w, r, "slack_reply", pid, p["fingerprint"])
    assert got["ok"] is True, got
    assert [c for _, _, c in fake(real, "slack:w1").performed] == ["Yes, the 14th."]
    await _stop_all(r, w, server)

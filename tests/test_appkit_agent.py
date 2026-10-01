"""`Agent.handle` against the events agentd broadcasts, including the ones only a busy agentd sends
and the ones a resting agentd sends (a limit, a pause: `note`, `ready`, a turn that goes back to wait).

Nothing here needs an event loop: the messages go straight into `handle`, by hand and from a
real AgentD read over its socket.
"""

import asyncio
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_agentd import Scripted  # noqa: E402

from bombadil import agentd, rest  # noqa: E402
from bombadil.appkit.context import AppContext  # noqa: E402
from bombadil.appkit.native.agent import Agent  # noqa: E402


@pytest.fixture
def agent(tmp_path):
    a = Agent(AppContext("agent-test", "Agent Test", tmp_path, tmp_path / "main.qml"))
    a.replies = []
    a.replied.connect(a.replies.append)
    return a


def feed(a: Agent, *msgs: dict):
    for m in msgs:
        a.handle(m)


def event(kind: str, turn: int | None, **fields) -> dict:
    return {"type": "event", "kind": kind, "turn": turn, **fields}


def test_queued_event_is_not_the_start_of_a_turn(agent):
    """agentd broadcasts {kind: queued} when a prompt waits behind another turn, to the asker too."""
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 1}, event("turn_start", 1, prompt="one"),
         event("text", 1, text="Part one of the answer"))
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 2}, event("queued", 2, prompt="two"))
    assert agent.property("reply") == "Part one of the answer"
    feed(agent, event("text", 1, text="Part two."), event("result", 1, ok=True, text="done"),
         event("turn_end", 1))
    assert agent.replies == ["Part one of the answer\n\nPart two."]
    feed(agent, event("turn_start", 2, prompt="two"), event("text", 2, text="Second answer"),
         event("turn_end", 2))
    assert agent.replies[1:] == ["Second answer"] and agent.property("reply") == "Second answer"
    assert agent._turns == set()


def test_queued_event_of_someone_elses_prompt_is_ignored(agent):
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 5}, event("turn_start", 5), event("text", 5, text="mine"),
         event("queued", 6, prompt="from the bar"), event("unqueued", 6))
    assert agent.property("reply") == "mine" and agent.replies == [] and agent._turns == {5}


def test_unqueued_turn_of_ours_ends(agent):
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 4}, event("queued", 4, prompt="waits"), event("unqueued", 4))
    assert agent.replies == ["Error: dropped from the queue"]
    assert agent.property("reply") == "Error: dropped from the queue"
    assert agent._turns == set()
    agent._on_disconnected()                    # agentd restarts: nothing of ours is left to lose
    assert agent.replies == ["Error: dropped from the queue"]


def test_unqueued_turn_leaves_a_running_answer_alone(agent):
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 1}, event("turn_start", 1), event("text", 1, text="Part one"))
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 2}, event("queued", 2, prompt="two"), event("unqueued", 2))
    assert agent.replies == ["Error: dropped from the queue"]
    assert agent.property("reply") == "Part one" and agent._turns == {1}
    feed(agent, event("text", 1, text="Part two."), event("turn_end", 1))
    assert agent.replies[1:] == ["Part one\n\nPart two."] and agent._turns == set()


def test_unqueued_turn_of_someone_else_is_ignored(agent):
    feed(agent, event("unqueued", 9), event("unqueued", None))
    assert agent.replies == [] and agent.property("reply") == ""


# -- the AI rests: out of plan or spending, or paused by hand (rest.py, agentd.py "Resting") --

def resting(note="At 15:00", **fields) -> dict:
    return {"type": "status", "busy": False, "provider": "claude", "setup": "resting",
            "rest": {"provider": "claude", "why": "limit", "until": 1790000000, "when": "15:00",
                     "hint": "Open or find anything. Asks wait for 15:00.", "note": note, "wait": "15:00"}, **fields}


def test_status_while_resting_gives_the_note_and_is_not_ready(agent):
    seen = []
    agent.noteChanged.connect(lambda: seen.append(("note", agent.property("note"))))
    agent.readyChanged.connect(lambda: seen.append(("ready", agent.property("ready"))))
    assert agent.property("note") == "" and agent.property("ready") is True       # nothing known yet: not resting
    feed(agent, resting())
    assert agent.property("note") == "At 15:00" and agent.property("ready") is False
    feed(agent, resting(), resting(note="At 16:00"))                                # a repeat says nothing again
    assert agent.property("note") == "At 16:00"
    feed(agent, resting(note="Paused"), {"type": "status", "busy": False, "provider": "claude", "setup": "ready"})
    assert agent.property("note") == "" and agent.property("ready") is True
    assert seen == [("ready", False), ("note", "At 15:00"), ("note", "At 16:00"), ("note", "Paused"),
                    ("ready", True), ("note", "")]


def test_resting_without_a_note_is_still_not_ready(agent):
    """`ready` is whether the AI rests, whatever the words; the note is only the words."""
    feed(agent, {"type": "status", "busy": False, "provider": "claude", "setup": "resting"})
    assert agent.property("ready") is False and agent.property("note") == ""
    feed(agent, {"type": "status", "busy": False, "provider": "claude"})            # an agentd that says nothing
    assert agent.property("ready") is True and agent.property("note") == ""


def test_other_setup_states_are_not_resting(agent):
    """Signing in or being offline is not a rest: only the AI at its limit or paused is."""
    for state in ("checking", "signed_out", "signing_in", "offline", "ready"):
        feed(agent, {"type": "status", "busy": False, "provider": "claude", "setup": state})
        assert agent.property("ready") is True and agent.property("note") == ""


def test_the_note_is_gone_when_agentd_is(agent):
    feed(agent, resting())
    agent._on_disconnected()
    assert agent.property("note") == "" and agent.property("ready") is True and agent.replies == []


def test_note_and_ready_are_properties_qml_can_bind(agent):
    meta = agent.metaObject()
    for name in ("note", "ready"):
        prop = meta.property(meta.indexOfProperty(name))
        assert prop.isValid() and prop.hasNotifySignal() and prop.notifySignal().name() == f"{name}Changed".encode()


def test_a_turn_the_limit_stopped_is_not_over(agent):
    """turn_end with requeued: no replied, still the app's turn, the half answer cleared; the rerun answers."""
    agent._waiting = 1
    prompt = "[from app agent-test] sum it up"
    feed(agent, {"type": "queued", "turn": 3}, event("turn_start", 3, prompt=prompt),
         event("text", 3, text="Starting with the first part"),
         event("rest", 3, provider="claude", why="limit", window="five_hour", until=1790000000, text="..."),
         event("turn_end", 3, requeued=True, line="Claude hit its limit partway. It carries on at 15:00."))
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == {3}
    feed(agent, resting(), event("queued", 3, prompt=prompt))               # back at the front of the queue
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == {3}
    feed(agent, event("turn_start", 3), event("text", 3, text="The whole answer."),
         event("result", 3, ok=True, text="The whole answer."), event("turn_end", 3, summary="Done."))
    assert agent.replies == ["The whole answer."] and agent.property("reply") == "The whole answer."
    assert agent._turns == set()


def test_a_turn_refused_at_once_makes_no_error_for_the_app(agent):
    """A refused turn has no result and no error event: the app sees a wait, never the limit as words."""
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 1}, event("turn_start", 1), event("rest", 1, why="spend", until=None),
         event("turn_end", 1, requeued=True, line="Claude is at its spending limit. Your apps and files still work."))
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == {1}
    agent._on_disconnected()                        # agentd restarts while it waits: that still ends the ask
    assert agent.replies == ["Error: lost the connection to agentd"]


def test_a_turn_that_waits_leaves_a_later_running_answer_alone(agent):
    """The requeued turn is no longer the one that owns `reply`: dropping another ask of ours still says so."""
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 1}, event("turn_start", 1), event("text", 1, text="Half"),
         event("turn_end", 1, requeued=True))
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 2}, event("queued", 2, prompt="two"), event("unqueued", 2))
    assert agent.replies == ["Error: dropped from the queue"]
    assert agent.property("reply") == "Error: dropped from the queue" and agent._turns == {1}
    feed(agent, event("turn_start", 1), event("text", 1, text="All of it"), event("turn_end", 1))
    assert agent.replies[1:] == ["All of it"] and agent.property("reply") == "All of it" and agent._turns == set()


def test_an_ask_replaced_by_the_apps_newer_one_goes_silently(agent):
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 4}, event("turn_start", 4), event("text", 4, text="Half"),
         event("turn_end", 4, requeued=True))
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 5}, event("unqueued", 4, replaced=True), event("queued", 5, prompt="again"))
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == {5}
    feed(agent, event("turn_start", 5), event("text", 5, text="Answer"), event("turn_end", 5))
    assert agent.replies == ["Answer"] and agent.property("reply") == "Answer" and agent._turns == set()


def test_a_replaced_ask_leaves_a_running_answer_alone(agent):
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 1}, event("turn_start", 1), event("text", 1, text="Part one"))
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 2}, event("queued", 2, prompt="two"), event("unqueued", 2, replaced=True))
    assert agent.replies == [] and agent.property("reply") == "Part one" and agent._turns == {1}
    feed(agent, event("turn_end", 1))
    assert agent.replies == ["Part one"]


def test_a_replaced_ask_of_someone_else_is_ignored(agent):
    feed(agent, event("unqueued", 9, replaced=True))
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == set()


# -- the same sequences from a real agentd --

SLOW = (
    "import json, sys, time\n"
    "sys.stdin.read()\n"
    "def say(t): print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': t}]}}), flush=True)\n"
    "say('Part one of the answer')\n"
    "time.sleep(1.5)\n"
    "say('Part two.')\n"
    "print(json.dumps({'type': 'result', 'result': 'done', 'session_id': 's1'}), flush=True)\n"
)


class Session:
    """The Agent, fed by a real agentd over its socket the way its own reader does."""

    def __init__(self, agent, reader, writer):
        self.agent, self.reader, self.writer = agent, reader, writer
        self.seen: list[dict] = []

    async def send(self, msg: dict):
        if msg["type"] == "prompt":
            self.agent._waiting += 1             # what Agent._send counts
        self.writer.write((json.dumps(msg) + "\n").encode())
        await self.writer.drain()

    async def until(self, done):
        while not done():
            line = await asyncio.wait_for(self.reader.readline(), 10)
            msg = json.loads(line)
            self.seen.append(msg)
            self.agent.handle(msg)


async def session(agent, provider):
    d = agentd.AgentD(provider, agentd._NoSnapshots())
    server = asyncio.create_task(d.serve())
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    r, w = await asyncio.open_unix_connection(str(d.socket_path), limit=1 << 24)
    s = Session(agent, r, w)
    await s.until(lambda: any(m["type"] == "entries" for m in s.seen))
    return s, server


@pytest.mark.asyncio
async def test_second_ask_while_the_first_runs_keeps_both_answers(home, agent):
    s, server = await session(agent, Scripted(SLOW))
    await s.send({"type": "prompt", "text": "first"})
    await s.until(lambda: agent.property("reply") == "Part one of the answer")
    await s.send({"type": "prompt", "text": "second"})
    await s.until(lambda: any(m.get("kind") == "queued" for m in s.seen))
    assert agent.property("reply") == "Part one of the answer"
    await s.until(lambda: len(agent.replies) == 2)
    assert agent.replies == ["Part one of the answer\n\nPart two."] * 2
    assert agent._turns == set()
    s.writer.close()
    server.cancel()


@pytest.mark.asyncio
async def test_dropping_a_queued_ask_answers_it(home, agent):
    s, server = await session(agent, Scripted(SLOW))
    await s.send({"type": "prompt", "text": "first"})
    await s.until(lambda: agent.property("reply") == "Part one of the answer")
    await s.send({"type": "prompt", "text": "second"})
    await s.until(lambda: any(m.get("kind") == "queued" for m in s.seen))
    first = next(m["turn"] for m in s.seen if m["type"] == "queued")
    second = next(m["turn"] for m in s.seen if m.get("kind") == "queued")
    await s.send({"type": "unqueue", "turn": second})
    await s.until(lambda: any(m.get("kind") == "unqueued" for m in s.seen))
    assert agent.replies == ["Error: dropped from the queue"] and agent._turns == {first}
    await s.until(lambda: len(agent.replies) == 2)
    assert agent.replies[1] == "Part one of the answer\n\nPart two."
    assert agent._turns == set()
    agent._on_disconnected()                     # a later agentd restart has nothing of ours to lose
    assert len(agent.replies) == 2
    s.writer.close()
    server.cancel()


# The first run of this "CLI" is refused for the account's limit after saying a few words, the way Claude
# Code writes it (see test_limit.py), and the limit lifts two seconds later; the runs after it answer.
LIMITED = (
    "import json, os, sys, time\n"
    "sys.stdin.read()\n"
    "def say(**m): print(json.dumps(m), flush=True)\n"
    "if not os.path.exists({mark!r}):\n"
    "    open({mark!r}, 'w').close()\n"
    "    say(type='assistant', message={{'content': [{{'type': 'text', 'text': 'Starting with the first part'}}]}})\n"
    "    say(type='rate_limit_event', rate_limit_info={{'status': 'rejected', 'resetsAt': time.time() + 2,\n"
    "                                                 'rateLimitType': 'five_hour'}})\n"
    "    say(type='assistant', error='rate_limit', is_api_error_message=True,\n"
    "        message={{'role': 'assistant', 'content': [{{'type': 'text', 'text': \"You've hit your session limit\"}}]}})\n"
    "    say(type='result', subtype='success', is_error=True, num_turns=1, result=\"You've hit your session limit\",\n"
    "        api_error_status=429, terminal_reason='api_error', session_id='s1')\n"
    "else:\n"
    "    say(type='assistant', message={{'content': [{{'type': 'text', 'text': 'The whole answer.'}}]}})\n"
    "    say(type='result', result='The whole answer.', session_id='s1')\n"
)


def app_ask(agent, text: str) -> dict:
    return {"type": "prompt", "text": f"[from app {agent._ctx.name}] {text}"}


@pytest.mark.asyncio
async def test_an_ask_the_limit_stops_waits_and_answers_after_the_reset(home, agent, monkeypatch):
    monkeypatch.setattr(rest, "RESET_GRACE", 0.0)
    s, server = await session(agent, Scripted(LIMITED.format(mark=str(home / "ran"))))
    await s.send(app_ask(agent, "sum it up"))
    await s.until(lambda: agent.property("ready") is False)
    assert agent.property("note").startswith("At ")
    turn = next(m["turn"] for m in s.seen if m["type"] == "queued")
    assert any(m.get("kind") == "turn_end" and m.get("requeued") for m in s.seen)
    # The half-turn is not an answer, and the limit never reaches the app as words.
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == {turn}
    assert not any(m.get("kind") in ("error", "result") for m in s.seen)
    await s.until(lambda: agent.replies)                       # the limit lifts by itself
    assert agent.replies == ["The whole answer."] and agent._turns == set()
    await s.until(lambda: agent.property("ready") is True)
    assert agent.property("note") == "" and agent.replies == ["The whole answer."]
    s.writer.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_newer_ask_of_the_app_replaces_the_waiting_one_without_a_word(home, agent, monkeypatch):
    monkeypatch.setattr(rest, "RESET_GRACE", 0.0)
    s, server = await session(agent, Scripted(LIMITED.format(mark=str(home / "ran"))))
    await s.send(app_ask(agent, "first"))
    await s.until(lambda: agent.property("ready") is False)
    await s.send(app_ask(agent, "second"))
    await s.until(lambda: any(m.get("replaced") for m in s.seen))
    first, second = [m["turn"] for m in s.seen if m["type"] == "queued"]
    assert agent.replies == [] and agent.property("reply") == "" and agent._turns == {second} != {first}
    await s.until(lambda: agent.replies)
    assert agent.replies == ["The whole answer."] and agent._turns == set()
    s.writer.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_ask_while_paused_by_hand_waits_for_the_resume(home, agent):
    s, server = await session(agent, Scripted(SLOW))
    await s.send({"type": "ai", "op": "pause", "provider": "claude"})
    await s.until(lambda: agent.property("ready") is False)
    assert agent.property("note") == "Paused"
    await s.send(app_ask(agent, "hello"))
    await s.until(lambda: any(m.get("kind") == "queued" for m in s.seen))
    assert agent.replies == [] and agent.property("note") == "Paused" and len(agent._turns) == 1
    await s.send({"type": "ai", "op": "resume", "provider": "claude"})
    await s.until(lambda: agent.replies)
    assert agent.replies == ["Part one of the answer\n\nPart two."] and agent._turns == set()
    await s.until(lambda: agent.property("ready") is True)
    assert agent.property("note") == ""
    s.writer.close()
    server.cancel()


def test_what_the_finder_found_for_a_kept_ask_changes_nothing_for_an_app(agent):
    """The pill shows these chips; an app's own ask is never looked for, and it hears nothing of them."""
    agent._waiting = 1
    feed(agent, {"type": "queued", "turn": 3}, event("queued", 3, prompt="[from app notes] summarise this"))
    before = (agent.property("reply"), set(agent._turns), agent._current, agent.replies[:])
    feed(agent, {"type": "found", "turn": 3, "prompt": "x", "line": "Kept for 15:00. Found on this computer:",
                 "matches": [{"id": "1", "kind": "app", "label": "Notes", "hint": "App"}]},
         {"type": "found_open", "turn": 3, "id": "1"})
    assert (agent.property("reply"), set(agent._turns), agent._current, agent.replies) == before

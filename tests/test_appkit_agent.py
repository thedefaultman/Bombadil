"""`Agent.handle` against the events agentd broadcasts, including the ones only a busy agentd sends.

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

from bombadil import agentd  # noqa: E402
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

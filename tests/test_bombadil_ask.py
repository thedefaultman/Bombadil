"""`bombadil ask` against a fake agentd: it never hangs until a reset, and says why it stopped waiting.

The real script runs in a child process, the way a terminal runs it. The fake daemon greets like agentd,
answers the first prompt with canned messages and then keeps the connection open without a word: a client
that waited for more would hit the timeout, which is the failure being tested.
"""

import asyncio
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from test_agentd import Scripted, _events_until, _start

from bombadil import agentd, paths

BOMBADIL = Path(__file__).resolve().parents[1] / "bin" / "bombadil"
RESTING_LINE = "Claude is at its limit until 15:00. Your apps and files still work."
CUT_OFF = "Claude hit its limit partway, after changing 2 files. It carries on at 15:00."


def status(setup="ready", queue=(), **more) -> dict:
    return {"type": "status", "busy": False, "provider": "claude", "setup": setup, "turns": 0, "snapshots": False,
            "queued": len(queue), "turn": None, "queue": list(queue), **more}


def setup(state="ready", line="") -> dict:
    return {"type": "setup", "state": state, "provider": "claude", "title": "Claude", "line": line,
            "tone": "step", "actions": []}


READY = [status(), {"type": "entries", "entries": []}, setup()]
RESTING = [status("resting", rest={"note": "At 15:00", "wait": "15:00"}), {"type": "entries", "entries": []},
           setup("resting", RESTING_LINE)]


def event(kind: str, turn: int | None, **fields) -> dict:
    return {"type": "event", "kind": kind, "turn": turn, **fields}


def ask(home, greeting: list[dict], answers: list[dict], *words: str) -> subprocess.CompletedProcess:
    """Run `bombadil ask` against a daemon that sends `greeting` on connect and `answers` to its prompt."""
    path = paths.socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    heard = []

    def serve():
        conn, _ = srv.accept()
        with conn:
            for msg in greeting:
                conn.sendall((json.dumps(msg) + "\n").encode())
            heard.append(json.loads(conn.makefile("r").readline()))
            for msg in answers:
                conn.sendall((json.dumps(msg) + "\n").encode())
            threading.Event().wait(15)           # silence: nothing more comes, the connection stays open

    threading.Thread(target=serve, daemon=True).start()
    try:
        done = subprocess.run([sys.executable, str(BOMBADIL), "ask", *words], capture_output=True, text=True,
                              timeout=10)
    finally:
        srv.close()
        path.unlink(missing_ok=True)
    assert heard and heard[0]["type"] == "prompt" and heard[0]["text"] == " ".join(words)
    return done


def test_a_turn_the_limit_stops_ends_the_ask_with_its_line_and_75(home):
    done = ask(home, READY, [
        {"type": "queued", "turn": 7},
        event("turn_start", 7, prompt="tidy my notes"),
        event("text", 7, text="Starting with the first part"),
        event("tool", 6, name="Bash", input={"command": "someone else's turn"}),
        event("rest", 7, provider="claude", why="limit", window="five_hour", until=1790000000, text="..."),
        event("turn_end", 7, seconds=3, summary="", changed=True, requeued=True, line=CUT_OFF),
    ], "tidy", "my", "notes")
    assert done.returncode == 75
    assert done.stdout == "Starting with the first part\n"      # the answer's words only
    assert done.stderr == CUT_OFF + "\n"


def test_a_turn_the_limit_stops_before_it_starts_says_the_resting_line(home):
    done = ask(home, READY, [
        {"type": "queued", "turn": 2},
        event("turn_start", 2, prompt="tidy my notes"),
        event("turn_end", 2, seconds=0, summary="", changed=False, requeued=True, line=RESTING_LINE),
    ], "tidy my notes")
    assert (done.returncode, done.stdout, done.stderr) == (75, "", RESTING_LINE + "\n")


def test_an_ask_made_while_resting_does_not_wait_for_the_reset(home):
    """It waits as a chip in agentd (its status gives it a time); the terminal says so and goes."""
    done = ask(home, RESTING, [
        {"type": "queued", "turn": 8},
        event("queued", 8, prompt="tidy my notes"),
        status("resting", [{"turn": 8, "prompt": "tidy my notes", "wait": "15:00"}], rest={"note": "At 15:00"}),
    ], "tidy my notes")
    assert (done.returncode, done.stdout, done.stderr) == (75, "", RESTING_LINE + "\n")


def test_what_the_finder_offers_for_a_kept_ask_is_not_the_terminals_business(home):
    """agentd tells every client what it found for a waiting ask; a terminal just says it is kept and goes."""
    done = ask(home, RESTING, [
        {"type": "queued", "turn": 8},
        event("queued", 8, prompt="tidy my notes"),
        {"type": "found", "turn": 8, "prompt": "tidy my notes", "line": "Kept for 15:00. Found on this computer:",
         "matches": [{"id": "1", "kind": "app", "label": "Notes", "hint": "App"}]},
        status("resting", [{"turn": 8, "prompt": "tidy my notes", "wait": "15:00"}], rest={"note": "At 15:00"}),
    ], "tidy my notes")
    assert (done.returncode, done.stdout, done.stderr) == (75, "", RESTING_LINE + "\n")


def test_a_paused_ai_says_so_the_same_way(home):
    line = "Claude is paused. Your apps and files still work."
    done = ask(home, [status("resting"), {"type": "entries", "entries": []}, setup("resting", line)], [
        {"type": "queued", "turn": 1},
        event("queued", 1, prompt="hello"),
        status("resting", [{"turn": 1, "prompt": "hello", "wait": "paused"}]),
    ], "hello")
    assert (done.returncode, done.stdout, done.stderr) == (75, "", line + "\n")


def test_a_command_runs_and_ends_normally_while_resting(home):
    """"!" commands and launcher words never wait for the AI, so the terminal waits for them as always."""
    done = ask(home, RESTING, [
        {"type": "queued", "turn": 4},
        status("resting", [{"turn": 3, "prompt": "an older ask", "wait": "15:00"}]),   # someone else's chip
        event("turn_start", 4, prompt="!echo hi"),
        event("output", 4, text="hi"),
        event("turn_end", 4, seconds=0, summary="Ran it.", changed=False),
    ], "!echo hi")
    assert (done.returncode, done.stdout, done.stderr) == (0, "hi\n", "")
    done = ask(home, RESTING, [
        {"type": "local", "action": "browser"},
        event("local", None, action="browser", phase="done", ok=True, text="Opened the browser."),
    ], "browser")
    assert (done.returncode, done.stdout, done.stderr) == (0, "Opened the browser.\n", "")


def test_an_ask_to_a_ready_ai_streams_and_ends_normally(home):
    done = ask(home, READY, [
        {"type": "queued", "turn": 5},
        status(queue=[{"turn": 5, "prompt": "hello"}]),             # waiting behind a turn: no "wait", no rest
        event("turn_start", 5, prompt="hello"),
        event("text", 5, text="Hello."),
        event("turn_end", 5, seconds=1, summary="Said hello.", changed=False),
    ], "hello")
    assert (done.returncode, done.stdout, done.stderr) == (0, "Hello.\n", "")


def test_an_ask_that_waited_through_a_rest_that_ended_runs_on(home):
    """The AI was resting when the terminal connected, but is back by the time the ask is queued."""
    done = ask(home, RESTING, [
        setup("ready", "Claude is back."),
        {"type": "queued", "turn": 6},
        status(queue=[]),
        event("turn_start", 6, prompt="hello"),
        event("text", 6, text="Hello."),
        event("turn_end", 6, seconds=1, summary="Said hello.", changed=False),
    ], "hello")
    assert (done.returncode, done.stdout, done.stderr) == (0, "Hello.\n", "")


def test_help_names_the_status(home):
    done = subprocess.run([sys.executable, str(BOMBADIL)], capture_output=True, text=True, timeout=10)
    assert done.returncode == 2 and "exits 75" in done.stdout


# -- the same against a real agentd --

OVER = (
    "import json, sys, time\n"
    "sys.stdin.read()\n"
    "def say(**m): print(json.dumps(m), flush=True)\n"
    "say(type='assistant', message={'content': [{'type': 'text', 'text': 'Starting with the first part'}]})\n"
    "say(type='rate_limit_event', rate_limit_info={'status': 'rejected', 'resetsAt': time.time() + 3 * 3600,\n"
    "                                             'rateLimitType': 'five_hour'})\n"
    "say(type='assistant', error='rate_limit', is_api_error_message=True,\n"
    "    message={'role': 'assistant', 'content': [{'type': 'text', 'text': \"You've hit your session limit\"}]})\n"
    "say(type='result', subtype='success', is_error=True, num_turns=1, result=\"You've hit your session limit\",\n"
    "    api_error_status=429, terminal_reason='api_error', session_id='s1')\n"
)
FINE = (
    "import json, sys\n"
    "sys.stdin.read()\n"
    "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Hello.'}]}}), flush=True)\n"
    "print(json.dumps({'type': 'result', 'result': 'Hello.', 'session_id': 's1'}), flush=True)\n"
)


async def run_ask(*words: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(sys.executable, str(BOMBADIL), "ask", *words,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 10)
    return proc.returncode, out.decode(), err.decode()


@pytest.mark.asyncio
async def test_an_ask_to_a_paused_ai_returns_75_and_stays_queued_for_the_resume(home):
    d = agentd.AgentD(Scripted(FINE), agentd._NoSnapshots())
    server, r, w = await _start(d)
    w.write(b'{"type": "ai", "op": "pause", "provider": "claude"}\n')
    await w.drain()
    await _events_until(r, lambda m: m.get("type") == "setup" and m.get("state") == "resting")
    code, out, err = await run_ask("say", "hello")
    assert (code, out, err) == (75, "", "Claude is paused. Your apps and files still work.\n")
    assert [p for _, p in d.pending] == ["say hello"]            # held: it runs when Claude is resumed
    w.write(b'{"type": "ai", "op": "resume", "provider": "claude"}\n')
    await w.drain()
    msgs = await _events_until(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in msgs if m.get("kind") == "text")["text"] == "Hello." and d.pending == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_ask_the_limit_stops_returns_75_with_the_resting_line(home):
    d = agentd.AgentD(Scripted(OVER), agentd._NoSnapshots())
    server, r, w = await _start(d)
    code, out, err = await run_ask("sum", "it", "up")
    assert code == 75 and out == "Starting with the first part\n"
    assert err.startswith("Claude is at its limit until ") and err.endswith(" Your apps and files still work.\n")
    assert [p for _, p in d.pending] == ["sum it up"]            # back at the front of the queue
    code, out, err = await run_ask("and", "this")                # now it is resting when the ask is made
    assert code == 75 and out == "" and err.startswith("Claude is at its limit until ")
    w.close()
    server.cancel()

import asyncio
import json
import re

import pytest
from test_jobs import Systemd

from bombadil import agentd, desk, launcher, paths, providers
from bombadil.jobs import Jobs


async def _client(path):
    r, w = await asyncio.open_unix_connection(str(path), limit=1 << 24)
    return r, w


async def _read_until(r, kind):
    out = []
    while True:
        line = await asyncio.wait_for(r.readline(), 5)
        msg = json.loads(line)
        out.append(msg)
        if msg.get("kind") == kind:
            return out


@pytest.mark.asyncio
async def test_turn_round_trip(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server = asyncio.create_task(d.serve())
    await _ready(d)
    r, w = await _client(d.socket_path)
    first = json.loads(await r.readline())
    assert first == {"type": "status", "busy": False, "provider": "fake", "setup": "ready", "turns": 0,
                     "snapshots": False, "queued": 0, "turn": None, "queue": []}
    entries = json.loads(await r.readline())
    assert entries["type"] == "entries" and any(e["name"] == "browser" for e in entries["entries"])
    setup = json.loads(await r.readline())
    assert setup["type"] == "setup" and setup["state"] == "ready" and setup["actions"] == []
    w.write(b'{"type": "prompt", "text": "tell me a joke"}\n')
    await w.drain()
    msgs = await _read_until(r, "turn_end")
    kinds = [m.get("kind") for m in msgs if m["type"] == "event" and m["kind"] != "status"]
    assert kinds == ["turn_start", "text", "result", "turn_end"]
    assert next(m for m in msgs if m.get("kind") == "text")["text"] == "echo: tell me a joke"
    log = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    assert log[0]["prompt"] == "tell me a joke" and log[0]["provider"] == "fake"
    w.close()
    server.cancel()


class Scripted(providers.Claude):
    """A Claude adapter whose 'CLI' is a Python one-liner printing stream-json lines."""
    name = "claude"

    def __init__(self, script):
        super().__init__("x")
        self.script = script
        self.seen_sessions = []

    @property
    def installed(self):
        return True

    def command(self, turn, workdir):
        self.seen_sessions.append(turn.session_id)
        return ["python3", "-c", self.script]

    def signed_in(self):
        return True


async def _ready(d, state="ready"):
    """Wait for the socket, and for agentd to know whether its provider is signed in."""
    for _ in range(250):
        if d.socket_path.exists() and d.access != "checking":
            break
        await asyncio.sleep(0.02)
    assert d.access == state


async def _start(d, state="ready"):
    server = asyncio.create_task(d.serve())
    await _ready(d, state)
    r, w = await _client(d.socket_path)
    await r.readline()   # status
    await r.readline()   # entries
    await r.readline()   # setup
    return server, r, w


async def _ask(w, text):
    w.write((json.dumps({"type": "prompt", "text": text}) + "\n").encode())
    await w.drain()


@pytest.mark.asyncio
async def test_reply_streams_before_the_cli_exits_and_long_lines_survive(home):
    big = "x" * 200_000  # a stream-json line with a screenshot in it is this long
    script = (
        "import json, sys, time\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'first'}]}}), flush=True)\n"
        "time.sleep(1.5)\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'x' * 200_000}]}}), flush=True)\n"
        "print(json.dumps({'type': 'result', 'result': 'done', 'session_id': 's1'}), flush=True)\n"
    )
    d = agentd.AgentD(Scripted(script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "-rf is not an option")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), 5))
        if m.get("kind") == "text":
            break
    assert m["text"] == "first" and loop.time() - t0 < 1.2
    msgs = await _read_until(r, "turn_end")
    assert any(x.get("text") == big for x in msgs)
    assert d.session_id == "s1"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_dead_session_is_dropped(home):
    script = (
        "import json, sys\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'result', 'is_error': True, 'num_turns': 0, 'session_id': 'gone',"
        " 'errors': ['No conversation found with session ID: gone']}))\n"
    )
    p = Scripted(script)
    d = agentd.AgentD(p, agentd._NoSnapshots())
    d.session_id = "gone"
    server, r, w = await _start(d)
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    errors = [m["text"] for m in msgs if m.get("kind") == "error"]
    assert errors and "new one" in errors[0]
    assert d.session_id is None and p.seen_sessions == ["gone"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_prompts_get_turn_ids_and_empty_ones_are_refused(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "  ")
    assert json.loads(await r.readline())["text"] == "empty prompt"
    await _ask(w, "hi")
    assert json.loads(await r.readline()) == {"type": "queued", "turn": 1}
    msgs = await _read_until(r, "turn_end")
    assert all(m["turn"] == 1 for m in msgs if m["type"] == "event")
    w.close()
    server.cancel()


async def _events_until(r, pred, timeout=8):
    out = []
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), timeout))
        out.append(m)
        if pred(m):
            return out


@pytest.mark.asyncio
async def test_launcher_words_never_start_a_turn(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "Undo")
    assert json.loads(await r.readline()) == {"type": "local", "action": "undo"}
    msgs = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert [m["kind"] for m in msgs] == ["local", "local"]
    assert msgs[0]["text"] == "Undoing the last change" and msgs[1]["ok"] is False
    assert d.turns == 0
    log = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    assert log[-1]["kind"] == "local" and log[-1]["action"] == "undo" and log[-1]["prompt"] == "Undo"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_what_happened_without_the_model_is_told_to_the_next_turn(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    monkeypatch.setattr(d.launcher, "run", lambda action: (True, "Opened the browser."))
    server, r, w = await _start(d)
    await _ask(w, "chrome")
    await _events_until(r, lambda m: m.get("phase") == "done")
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    said = next(m["text"] for m in msgs if m.get("kind") == "text")
    assert said.startswith("echo: [Done by the user without you since your last turn: 'chrome': Opened the browser.]")
    assert next(m for m in msgs if m.get("kind") == "turn_start")["prompt"] == "hello"
    await _ask(w, "again")
    msgs = await _read_until(r, "turn_end")
    assert next(m["text"] for m in msgs if m.get("kind") == "text") == "echo: again"
    w.close()
    server.cancel()


SLOW_INSTALL = (
    "import json, subprocess, sys, time\n"
    "sys.stdin.read()\n"
    "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash',"
    " 'input': {'command': 'sudo pacman -S docker'}}]}}), flush=True)\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(json.dumps({'type': 'system', 'subtype': 'child', 'pid': child.pid}), flush=True)\n"
    "time.sleep(60)\n"
)


@pytest.mark.asyncio
async def test_the_line_says_what_runs_and_stop_ends_it_all(home):
    from bombadil import procs
    d = agentd.AgentD(Scripted(SLOW_INSTALL), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w = await _start(d)
    await _ask(w, "install docker")
    msgs = await _events_until(r, lambda m: m.get("kind") == "status" and m.get("source") == "step")
    step = msgs[-1]
    assert (step["text"], step["risk"], step["command"]) == ("Installing docker", "system", "sudo pacman -S docker")
    pgid = d.proc.pid
    await asyncio.sleep(0.5)
    kids = procs.descendants(pgid, procs.all_procs())
    assert kids
    w.write(b'{"type": "stop"}\n')
    await w.drain()
    msgs = await _read_until(r, "turn_end")
    end = msgs[-1]
    assert end["stopped"] is True and end["line"] == "Stopped while installing docker."
    assert not [m for m in msgs if m.get("kind") == "error"]   # a stop is not an error
    await asyncio.sleep(0.2)
    assert not [k for k in kids if procs.read(k) and not procs._zombie(k)]
    log = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    assert log[-1]["stopped"] is True
    w.close()
    server.cancel()


class SlowSnaps(agentd._NoSnapshots):
    available = True

    def create(self, description):
        import time
        time.sleep(1.0)
        from bombadil import snapshots
        return snapshots.Snapshot(7, description)


@pytest.mark.asyncio
async def test_turn_start_comes_before_the_restore_point(home):
    d = agentd.AgentD(providers.Fake("x"), SlowSnaps())
    server, r, w = await _start(d)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await _ask(w, "hello")
    await _events_until(r, lambda m: m.get("kind") == "turn_start")
    assert loop.time() - t0 < 0.3
    msgs = await _read_until(r, "turn_end")
    kinds = [m.get("kind") for m in msgs if m["type"] == "event"]
    assert kinds.index("snapshot") < kinds.index("text")
    assert next(m for m in msgs if m.get("kind") == "status")["text"] == "Saving a restore point"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_prompts_typed_during_a_turn_wait_as_chips(home):
    slow = "import json, sys, time\nsys.stdin.read()\ntime.sleep(1.5)\nprint(json.dumps({'type': 'result', 'result': 'ok'}))\n"
    d = agentd.AgentD(Scripted(slow), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "first")
    await _events_until(r, lambda m: m.get("kind") == "turn_start")
    await _ask(w, "second")
    await _ask(w, "third")
    msgs = await _events_until(r, lambda m: m.get("kind") == "queued" and m.get("prompt") == "third")
    queued = [m for m in msgs if m.get("kind") == "queued"]
    assert [q["prompt"] for q in queued] == ["second", "third"]
    w.write((json.dumps({"type": "unqueue", "turn": queued[0]["turn"]}) + "\n").encode())
    await w.drain()
    await _events_until(r, lambda m: m.get("kind") == "unqueued")
    starts = []
    for _ in range(2):
        msgs = await _read_until(r, "turn_end")
        starts += [m["prompt"] for m in msgs if m.get("kind") == "turn_start"]
    assert starts == ["third"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_bang_runs_a_shell_command(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "!echo one; sleep 0.2; echo two")
    msgs = await _read_until(r, "turn_end")
    assert [m["text"] for m in msgs if m.get("kind") == "output"] == ["one", "two"]
    assert next(m for m in msgs if m.get("kind") == "tool")["input"] == {"command": "echo one; sleep 0.2; echo two"}
    assert next(m for m in msgs if m.get("kind") == "result")["text"] == "one\ntwo"
    await _ask(w, "!echo broken >&2; exit 3")
    msgs = await _read_until(r, "turn_end")
    assert next(m for m in msgs if m.get("kind") == "error")["text"] == "broken\n(exit 3)"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_details_show_the_turns_own_log(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    opened = []
    closed = []
    monkeypatch.setattr(d.launcher, "details", lambda argv, toggle=False: opened.append((argv, toggle)))
    monkeypatch.setattr(d.launcher, "close_details", lambda: closed.append(True))
    server, r, w = await _start(d)
    await _ask(w, "!echo hi")
    await _read_until(r, "turn_end")
    w.write(b'{"type": "details", "turn": 1}\n')
    await w.drain()
    for _ in range(50):
        if opened:
            break
        await asyncio.sleep(0.02)
    argv, toggle = opened[0]
    assert argv[1:3] == ["watch", "--file"]
    assert toggle   # Details again closes the drawer it shows
    kinds = [json.loads(line)["kind"] for line in open(argv[3])]
    assert kinds[0] == "turn_start" and kinds[-1] == "turn_end" and "tool_result" in kinds
    # Esc in the pill puts the drawer away.
    w.write(b'{"type": "close_details"}\n')
    await w.drain()
    for _ in range(50):
        if closed:
            break
        await asyncio.sleep(0.02)
    assert closed == [True]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_key_binds_reach_the_bar(home):
    """`bombadil pill` and `bombadil stop` send one line and go: agentd must still act on it."""
    import sys
    from pathlib import Path
    bombadil = Path(__file__).resolve().parents[1] / "bin" / "bombadil"
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    for _ in range(5):
        proc = await asyncio.create_subprocess_exec(sys.executable, str(bombadil), "pill")
        assert await asyncio.wait_for(proc.wait(), 5) == 0
        assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "summon"}
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_stop_while_the_restore_point_is_saved_ends_the_turn_before_its_cli(home):
    d = agentd.AgentD(providers.Fake("x"), SlowSnaps())
    server, r, w = await _start(d)
    await _ask(w, "hello")
    msgs = await _events_until(r, lambda m: m.get("kind") == "status" and m.get("text") == "Saving a restore point")
    assert all(m["busy"] for m in msgs if m["type"] == "status")   # Esc and the dot still stop
    w.write(b'{"type": "stop"}\n')
    await w.drain()
    msgs = await _read_until(r, "turn_end")
    assert msgs[-1]["stopped"] is True
    assert not [m for m in msgs if m.get("kind") in ("text", "result")]   # the CLI never ran
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_broken_app_folder_never_costs_a_client(home):
    from pathlib import Path
    import sys
    (home / "Apps" / "passwords.bak").mkdir(parents=True)
    (home / "Apps" / "passwords.bak" / "main.qml").write_text("Item {}")
    (home / "Apps" / "notes").mkdir()
    (home / "Apps" / "notes" / "main.qml").write_text("Item {}")
    (home / "Apps" / "notes" / "app.toml").write_text('title = "\\ud83c"\n')
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "stop")
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "local", "action": "stop"}
    done = (await _events_until(r, lambda m: m.get("phase") == "done"))[-1]
    assert done["text"] == "Nothing is running."
    bombadil = Path(__file__).resolve().parents[1] / "bin" / "bombadil"
    proc = await asyncio.create_subprocess_exec(sys.executable, str(bombadil), "pill")
    assert await asyncio.wait_for(proc.wait(), 5) == 0
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "summon"}
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_job_left_in_the_background_does_not_hold_the_turn(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await _ask(w, "!sleep 7.25 &")
    await _read_until(r, "turn_end")
    assert loop.time() - t0 < 3
    await _ask(w, "!echo second")
    msgs = await _read_until(r, "turn_end")
    assert [m["text"] for m in msgs if m.get("kind") == "output"] == ["second"]
    assert loop.time() - t0 < 5
    w.close()
    server.cancel()
    from bombadil import procs
    for p in procs.all_procs().values():
        if "7.25" in p.cmdline:
            import os
            import signal
            os.kill(p.pid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_a_client_that_stops_reading_holds_up_nobody(home, monkeypatch):
    monkeypatch.setattr(agentd, "SEND_TIMEOUT", 1.0)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    stuck_r, stuck_w = await _client(d.socket_path)   # never reads
    await _ask(w, "!python3 -c \"import time\nwhile True: print('x' * 4000, flush=True); time.sleep(0.0005)\"")
    await _events_until(r, lambda m: m.get("kind") == "output")
    await asyncio.sleep(1.5)
    stop_r, stop_w = await _client(d.socket_path)
    stop_w.write(b'{"type": "stop"}\n')
    await stop_w.drain()
    msgs = await _read_until(r, "turn_end")
    assert msgs[-1]["stopped"] is True
    for x in (w, stuck_w, stop_w):
        x.close()
    server.cancel()


class RecordingSnaps(agentd._NoSnapshots):
    available = True

    def __init__(self):
        self.made, self.log = [], []

    def create(self, description):
        from bombadil import snapshots
        s = snapshots.Snapshot(len(self.made) + 1, description)
        self.made.append(s)
        self.log.append(("create", s.number))
        return s

    def list(self, limit=20):
        return list(self.made)

    def rollback(self, number):
        self.log.append(("rollback", number))
        return True


@pytest.mark.asyncio
async def test_undo_during_a_turn_takes_back_that_turn_even_with_one_queued(home):
    snaps = RecordingSnaps()
    d = agentd.AgentD(providers.Fake("x"), snaps)
    server, r, w = await _start(d)
    # A turn whose child ignores SIGINT, so Stop takes its whole grace.
    await _ask(w, "!python3 -c \"import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)\"")
    await _events_until(r, lambda m: m.get("kind") == "snapshot")
    await _ask(w, "!echo two")
    await _events_until(r, lambda m: m.get("kind") == "queued")
    await _ask(w, "undo")
    msgs = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done", timeout=15)
    assert msgs[-1]["ok"] is True and "“!python3" in msgs[-1]["text"]
    await _read_until(r, "turn_end")     # the queued turn runs after the undo, not before it
    assert snaps.log == [("create", 1), ("rollback", 1), ("create", 2)]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_ask_stop_answers_and_returns(home):
    import sys
    from pathlib import Path
    bombadil = Path(__file__).resolve().parents[1] / "bin" / "bombadil"
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "!sleep 30")
    await _events_until(r, lambda m: m.get("kind") == "tool")
    proc = await asyncio.create_subprocess_exec(sys.executable, str(bombadil), "ask", "stop",
                                                stdout=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(), 8)
    assert proc.returncode == 0 and out.decode().strip() == "Stopping."
    msgs = await _read_until(r, "turn_end")
    assert msgs[-1]["stopped"] is True
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_slow_launcher_word_does_not_hold_up_what_comes_after_it(home, monkeypatch):
    import time as _time
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    monkeypatch.setattr(d.launcher, "run", lambda action: (_time.sleep(2), (True, "Opened the browser."))[1])
    server, r, w = await _start(d)
    await _ask(w, "chrome")
    await _ask(w, "!echo hi")
    msgs = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    kinds = [m.get("kind") for m in msgs]
    assert kinds.index("turn_end") < len(kinds) - 1   # the turn ran while the browser was opening
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_narration_bug_costs_a_line_not_the_turn(home, monkeypatch):
    from bombadil import narrate

    def broken(self, ev):
        raise TypeError("unhashable type: 'list'")
    monkeypatch.setattr(narrate.Narrator, "on_event", broken)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    assert not [m for m in msgs if m.get("kind") == "error"]
    assert next(m for m in msgs if m.get("kind") == "result")["text"] == "echo: hello"
    w.close()
    server.cancel()


# -- the desk --

async def _say(w, msg):
    w.write((json.dumps(msg) + "\n").encode())
    await w.drain()


async def _another(d):
    """A second client, past its greeting."""
    r, w = await _client(d.socket_path)
    await r.readline()   # status
    await r.readline()   # entries
    await r.readline()   # setup
    return r, w


async def _silent(r, seconds=0.3):
    """Does nothing arrive for a while?"""
    try:
        await asyncio.wait_for(r.readline(), seconds)
    except TimeoutError:
        return True
    return False


async def _all_of(r, *preds, timeout=5):
    """Read until each predicate has matched a message (in any order); return every message."""
    out, left = [], list(preds)
    while left:
        out.append(json.loads(await asyncio.wait_for(r.readline(), timeout)))
        left = [p for p in left if not p(out[-1])]
    return out


def _is_done(m):
    return m.get("kind") == "local" and m.get("phase") == "done"


async def _desk_state(r):
    return (await _events_until(r, lambda m: m.get("type") == "desk", 5))[-1]


async def _desk_result(r):
    return (await _events_until(r, lambda m: m.get("type") == "desk-result", 5))[-1]


@pytest.mark.asyncio
async def test_the_shell_asks_for_the_desk_and_only_the_asker_is_told(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    assert await _silent(r) and await _silent(other_r)   # the greeting is still status and entries
    await _say(w, {"type": "desk", "op": "get"})
    state = json.loads(await asyncio.wait_for(r.readline(), 5))
    assert state == d.desk.snapshot() and state["type"] == "desk" and state["folded"] is False
    assert state["order"] == {"left": ["now", "watching", "alive"], "right": ["needs", "away", "machine"]}
    # The jobs table follows, empty or not, so a bar that restarted does not keep an old one.
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "jobs", "jobs": []}
    assert await _silent(r) and await _silent(other_r)
    w.close()
    other_w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_bar_that_restarts_mid_turn_gets_the_route_again(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    plan = {"type": "event", "kind": "plan", "turn": 3,
            "steps": [{"id": "1", "subject": "Install ffmpeg", "active": None, "status": "in_progress"}]}
    d.current, d.plan_msg = 3, plan
    await _say(w, {"type": "desk", "op": "get"})
    assert json.loads(await asyncio.wait_for(r.readline(), 5))["type"] == "desk"
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == plan
    assert json.loads(await asyncio.wait_for(r.readline(), 5))["type"] == "jobs"   # after the state and the plan
    d.current = None   # no turn runs: a plan left over is not the route of anything
    await _say(w, {"type": "desk", "op": "get"})
    assert json.loads(await asyncio.wait_for(r.readline(), 5))["type"] == "desk"
    assert json.loads(await asyncio.wait_for(r.readline(), 5))["type"] == "jobs"
    assert await _silent(r)
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_shell_changes_the_desk_and_everyone_is_told(home):
    import tomllib
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    await _say(w, {"type": "desk", "op": "hide", "widget": "machine"})
    for rd in (r, other_r):
        assert (await _desk_state(rd))["hidden"] == ["alive", "machine"]
    assert tomllib.loads(paths.desk_file().read_text())["hidden"] == ["alive", "machine"]
    await _say(w, {"type": "desk", "op": "fold"})
    assert (await _desk_state(r))["folded"] is True
    await _say(w, {"type": "desk", "op": "fold"})
    assert (await _desk_state(r))["folded"] is False
    await _say(w, {"type": "desk", "op": "move", "widget": "watching", "rail": "right", "rank": 0})
    moved = await _desk_state(r)
    assert moved["rails"]["watching"] == "right" and moved["order"]["right"][0] == "watching"
    await _say(w, {"type": "desk", "op": "show", "widget": "machine"})
    assert (await _desk_state(r))["hidden"] == ["alive"]
    # What changes nothing, what is refused and what is not the shell's to ask says nothing.
    for msg in ({"op": "hide", "widget": "needs"}, {"op": "show", "widget": "now"},
                {"op": "move", "widget": "watching", "rail": "right", "rank": 0},
                {"op": "unfold"}, {"op": "state"}, {"op": "make", "widget": "batch"}, {"op": "hide"}, {}):
        await _say(w, {"type": "desk", **msg})
    assert await _silent(r)
    for _ in range(4):   # the other client heard every change too, and nothing else
        await _desk_state(other_r)
    assert await _silent(other_r)
    assert d.desk.snapshot()["hidden"] == ["alive"]
    for x in (w, other_w):
        x.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_gesture_at_the_desk_is_told_to_the_agent_and_kept_in_the_history(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _say(w, {"type": "desk", "op": "hide", "widget": "machine"})
    await _desk_state(r)
    await _say(w, {"type": "desk", "op": "hide", "widget": "machine"})   # again: nothing changed, nothing noted
    await _say(w, {"type": "desk", "op": "hide", "widget": "needs"})     # refused
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    said = next(m["text"] for m in msgs if m.get("kind") == "result")   # the whole prompt, echoed
    assert said == ("echo: [Done by the user without you since your last turn: at the desk: Put Machine away.]"
                    " hello")
    assert not [m for m in msgs if m.get("kind") == "local"]   # a gesture is not said on the line
    log = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    assert [(x["kind"], x["prompt"], x["result"]) for x in log if x.get("kind") == "local"] == [
        ("local", "at the desk", "Put Machine away.")]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_changes_made_in_a_thread_reach_every_client_once_they_settle(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    other_r, other_w = await _another(d)
    # The launcher's worker thread is one of these; the shell's messages another.
    await asyncio.to_thread(d.desk.apply, "hide", "watching")
    await asyncio.to_thread(d.desk.apply, "hide", "away")
    await _say(w, {"type": "desk", "op": "fold"})
    seen = []
    while not seen or seen[-1]["hidden"] != ["watching", "alive", "away"] or not seen[-1]["folded"]:
        seen.append(await _desk_state(r))
    assert len(seen) <= 3
    assert (await _desk_state(other_r)) is not None
    await asyncio.sleep(0.2)
    d.desk.apply("hide", "needs")   # refused: no broadcast
    assert await _silent(r)
    for x in (w, other_w):
        x.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_desk_word_changes_the_desk_says_so_and_is_told_to_the_next_turn(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "hide machine")
    assert json.loads(await r.readline()) == {"type": "local", "action": "widget"}
    msgs = await _all_of(r, _is_done, lambda m: m.get("type") == "desk")
    local = [m for m in msgs if m.get("kind") == "local"]
    assert [(m["phase"], m["text"]) for m in local] == [("start", "Putting Machine away"),
                                                        ("done", "Put Machine away.")]
    assert local[1]["ok"] is True and local[0]["target"] == "machine"
    state = next(m for m in msgs if m.get("type") == "desk")
    assert state["hidden"] == ["alive", "machine"] and state == d.desk.snapshot()
    await _ask(w, "desk")
    msgs = await _all_of(r, _is_done, lambda m: m.get("type") == "desk")
    assert next(m for m in msgs if m.get("type") == "desk")["folded"] is True
    assert d.turns == 0
    # "hide needs you" is refused in the words the line shows, and nothing changes.
    await _ask(w, "hide needs you")
    done = (await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done"))[-1]
    assert (done["ok"], done["text"]) == (False, "Needs you cannot be hidden.")
    assert await _silent(r)
    # What happened without the model reaches its next prompt; the refused one does not.
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    said = next(m["text"] for m in msgs if m.get("kind") == "result")
    assert said == ("echo: [Done by the user without you since your last turn: 'hide machine': Put Machine away.; "
                    "'desk': Folded the desk.] hello")
    log = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    assert [(x["prompt"], x["action"], x["target"], x["ok"]) for x in log if x.get("kind") == "local"] == [
        ("hide machine", "widget", "machine", True), ("desk", "desk", "", True),
        ("hide needs you", "widget", "needs", False)]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_desk_button_and_a_sentence_about_the_desk(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _say(w, {"type": "local", "action": "desk"})
    msgs = await _all_of(r, _is_done, lambda m: m.get("type") == "desk")
    assert next(m for m in msgs if _is_done(m))["text"] == "Folded the desk."
    assert next(m for m in msgs if m.get("type") == "desk")["folded"] is True
    # A sentence with the word in it is for the agent, not the desk.
    await _ask(w, "what is on my desk?")
    assert json.loads(await r.readline()) == {"type": "queued", "turn": 1}
    msgs = await _read_until(r, "turn_end")
    assert next(m for m in msgs if m.get("kind") == "turn_start")["prompt"] == "what is on my desk?"
    # The agent had it.
    assert next(m for m in msgs if m.get("kind") == "result")["text"].endswith("what is on my desk?")
    assert d.desk.folded is True   # and the desk was not touched by it
    w.close()
    server.cancel()


def test_there_is_one_desk_for_the_launcher_the_shell_and_the_tool(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    assert d.launcher.desk_state is d.desk and d.desk.on_change == d._desk_changed
    mine = desk.Desk()
    assert agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), desk=mine).launcher.desk_state is mine
    lx = launcher.Launcher(snaps=agentd._NoSnapshots(), desk=desk.Desk())
    assert agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), launch=lx).desk is lx.desk_state


def test_agentd_starts_with_the_desk_that_was_saved(home):
    saved = desk.Desk()
    saved.apply("hide", "machine")
    saved.apply("toggle")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    assert d.desk.snapshot() == saved.snapshot()


# What the CLI of a turn is told about it, and what it asks the desk while it runs.
DESK_TURN = (
    "import json, os, sys, time\n"
    "prompt = sys.stdin.read()\n"
    "seen = os.environ['BOMBADIL_TURN'] + ' ' + os.environ['BOMBADIL_SOCKET'] + ' ' + prompt\n"
    "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': seen}]}}),"
    " flush=True)\n"
    "time.sleep(60)\n"
)


async def _in_a_turn(d, prompt, **extra):
    """A running turn whose CLI waits, and a second client standing in for its os-mcp server."""
    server, r, w = await _start(d)
    tool_r, tool_w = await _another(d)
    if extra:
        await _say(w, {"type": "prompt", "text": prompt, **extra})
    else:
        await _ask(w, prompt)
    msgs = await _events_until(r, lambda m: m.get("kind") == "text")
    return server, r, w, tool_r, tool_w, msgs[-1]["text"]


async def _stop(r, w, server, *more):
    await _say(w, {"type": "stop"})
    msgs = await _read_until(r, "turn_end")
    for x in (w, *more):
        x.close()
    server.cancel()
    return msgs


@pytest.mark.asyncio
async def test_the_desk_tool_works_in_the_turn_that_asked_for_the_desk(home):
    from bombadil import procs
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w, tool_r, tool_w, seen = await _in_a_turn(d, "please hide the machine widget")
    # The CLI (and so its os-mcp server) is told the turn and where agentd listens; agentd keeps
    # the words as they were typed.
    assert seen.startswith(f"1 {d.socket_path} ") and d.turn_prompt == "please hide the machine widget"
    tool = {"type": "desk-tool", "id": "a", "turn": 1, "op": "hide", "widget": "machine"}
    await _say(tool_w, tool)
    assert await _desk_result(tool_r) == {"type": "desk-result", "id": "a", "ok": True, "text": "Put Machine away."}
    assert (await _desk_state(r))["hidden"] == ["alive", "machine"]   # the shell sees it
    assert (await _desk_state(tool_r))["hidden"] == ["alive", "machine"]
    for i, (msg, ok, text) in enumerate([
        ({"op": "state"}, True, ("The desk is open. Left rail, nearest the pill first: Now, Watching, "
                                 "Alive (put away). Right rail: Needs you, Away, Machine (put away).")),
        ({"op": "fold"}, True, "Folded the desk."),
        ({"op": "fold"}, True, "The desk is already folded."),
        ({"op": "unfold"}, True, "Unfolded the desk."),
        ({"op": "move", "widget": "watching", "rail": "right", "rank": 0}, True,
         "Moved Watching to the right rail."),
        ({"op": "show", "widget": "Machine"}, True, "Put Machine on the desk."),
        ({"op": "hide", "widget": "needs"}, False, "Needs you cannot be hidden."),
        ({"op": "hide", "widget": "sofa"}, False, ("There is no widget called 'sofa'. The widgets are Now, "
                                                    "Watching, Alive, Needs you, Away and Machine.")),
        ({"op": "make", "widget": "batch"}, False, ("The desk cannot make. It can show, hide, move, fold, unfold "
                                                    "and say its state.")),
        ({"op": "remove", "widget": "batch"}, False, ("The desk cannot remove. It can show, hide, move, fold, "
                                                      "unfold and say its state.")),
        ({}, False, "The desk cannot do that. It can show, hide, move, fold, unfold and say its state."),
    ]):
        await _say(tool_w, {"type": "desk-tool", "id": f"q{i}", "turn": 1, **msg})
        got = await _desk_result(tool_r)
        assert (got["id"], got["ok"], got["text"]) == (f"q{i}", ok, text)
    msgs = await _stop(r, w, server, tool_w)
    # Only the one that asked got an answer; the shell did not.
    assert not [m for m in msgs if m.get("type") == "desk-result"]


@pytest.mark.asyncio
async def test_the_desk_tool_refuses_a_turn_that_is_not_the_running_one(home):
    from bombadil import procs
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "hide machine now please, in the desk")
    no = "That turn is over, so the desk stays as it is."
    for turn in (0, 2, None, "1", 99, True, 1.5):   # True == 1 in Python; a flag is no turn
        await _say(tool_w, {"type": "desk-tool", "id": "x", "turn": turn, "op": "hide", "widget": "machine"})
        assert await _desk_result(tool_r) == {"type": "desk-result", "id": "x", "ok": False, "text": no}
    await _say(tool_w, {"type": "desk-tool", "id": "y", "op": "hide", "widget": "machine"})   # no turn at all
    assert (await _desk_result(tool_r))["text"] == no
    assert d.desk.snapshot()["hidden"] == ["alive"]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_desk_tool_refuses_a_turn_that_did_not_ask_for_the_desk(home):
    from bombadil import procs
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "install htop")
    for op in ({"op": "hide", "widget": "machine"}, {"op": "state"}, {"op": "fold"}):
        await _say(tool_w, {"type": "desk-tool", "id": "n", "turn": 1, **op})
        res = await _desk_result(tool_r)
        assert res["ok"] is False and res["text"].startswith("The person did not ask for the desk in this turn")
    assert d.desk.snapshot() == desk.Desk().snapshot()
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_desk_tool_refuses_while_the_turn_is_being_stopped(home):
    from bombadil import procs
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "hide machine, it is on my desk")
    d.stopping = True
    await _say(tool_w, {"type": "desk-tool", "id": "s", "turn": 1, "op": "hide", "widget": "machine"})
    res = await _desk_result(tool_r)
    assert res["ok"] is False and res["text"] == "That turn is over, so the desk stays as it is."
    d.stopping = False
    assert d.desk.snapshot()["hidden"] == ["alive"]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_turn_a_coding_session_asked_for_cannot_use_the_desk_tool(home):
    """The words of a coding session are not the person's, even when they say "desk"."""
    from bombadil import procs
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "hide machine on the desk", asked_by="builder")
    assert d.turn_prompt is None
    await _say(tool_w, {"type": "desk-tool", "id": "b", "turn": 1, "op": "hide", "widget": "machine"})
    res = await _desk_result(tool_r)
    assert res["ok"] is False and "did not ask for the desk" in res["text"]
    assert d.desk.snapshot() == desk.Desk().snapshot()
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_earlier_desk_words_in_the_notes_do_not_open_the_gate(home):
    from bombadil import procs
    d = agentd.AgentD(Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0))
    server, r, w = await _start(d)
    tool_r, tool_w = await _another(d)
    await _ask(w, "hide machine")            # a launcher word: it reaches the next prompt as a note
    await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    await _ask(w, "tell me a joke")
    text = (await _events_until(r, lambda m: m.get("kind") == "text"))[-1]["text"]
    # The CLI has the notes in front of the prompt; the gate does not.
    assert "'hide machine': Put Machine away." in text and text.endswith("tell me a joke")
    assert d.turn_prompt == "tell me a joke"
    await _say(tool_w, {"type": "desk-tool", "id": "z", "turn": 1, "op": "show", "widget": "machine"})
    res = await _desk_result(tool_r)
    assert res["ok"] is False and "did not ask for the desk" in res["text"]
    assert "machine" in d.desk.snapshot()["hidden"]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_turn_that_has_ended_cannot_use_the_desk_tool(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "put the desk away")
    await _read_until(r, "turn_end")
    await _say(w, {"type": "desk-tool", "id": "late", "turn": 1, "op": "fold"})
    res = await _desk_result(r)
    assert res == {"type": "desk-result", "id": "late", "ok": False,
                   "text": "That turn is over, so the desk stays as it is."}
    assert d.desk.folded is False
    w.close()
    server.cancel()


def _mcp_turn(arguments, tool="desk"):
    """A CLI that starts the real os-mcp server as a child, as Claude Code does, and calls one of
    its tools (the `desk` tool, unless told otherwise) once."""
    from pathlib import Path
    mcp = Path(__file__).resolve().parents[1] / "bin" / "bombadil-os-mcp"
    return (
        "import json, subprocess, sys\n"
        "sys.stdin.read()\n"
        f"p = subprocess.Popen([sys.executable, {str(mcp)!r}], stdin=subprocess.PIPE, stdout=subprocess.PIPE,"
        " text=True)\n"
        "def rpc(m):\n"
        "    p.stdin.write(json.dumps(m) + '\\n')\n"
        "    p.stdin.flush()\n"
        "    return json.loads(p.stdout.readline())\n"
        "rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})\n"
        "r = rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',"
        f" 'params': {{'name': {tool!r}, 'arguments': {arguments!r}}}}})\n"
        "p.stdin.close()\n"
        "p.wait()\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text',"
        " 'text': json.dumps(r['result'])}]}}), flush=True)\n"
        "print(json.dumps({'type': 'result', 'result': 'done'}), flush=True)\n")


@pytest.mark.asyncio
async def test_the_real_os_mcp_server_reaches_the_desk_from_inside_a_turn(home):
    d = agentd.AgentD(Scripted(_mcp_turn({"op": "hide", "widget": "machine"})), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "please hide the machine widget")
    msgs = await _read_until(r, "turn_end")
    said = json.loads(next(m["text"] for m in msgs if m.get("kind") == "text"))
    assert said == {"content": [{"type": "text", "text": "Put Machine away."}]}
    assert d.desk.snapshot()["hidden"] == ["alive", "machine"]
    # The next turn's words did not ask for the desk: the same call is refused, in words.
    await _ask(w, "install htop")
    msgs = await _read_until(r, "turn_end")
    said = json.loads(next(m["text"] for m in msgs if m.get("kind") == "text"))
    assert said["isError"] is True and said["content"][0]["text"].startswith("The person did not ask for the desk")
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_missing_cli_still_starts_and_ends_its_turn(home):
    class Missing(providers.Claude):
        @property
        def installed(self):
            return False
    d = agentd.AgentD(Missing("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    kinds = [m.get("kind") for m in msgs if m["type"] == "event"]
    assert kinds[0] == "turn_start" and "error" in kinds and msgs[-1]["turn"] == 1
    w.close()
    server.cancel()


# What Claude prints for a turn that keeps a task list: two tasks made, the first done under a
# command that changes the system and a file it writes.
PLANNED = (
    "import json, sys, time\n"
    "sys.stdin.read()\n"
    "def call(id, name, **inp):\n"
    "    print(json.dumps({'type': 'assistant', 'message': {'content': [\n"
    "        {'type': 'tool_use', 'id': id, 'name': name, 'input': inp}]}}), flush=True)\n"
    "def result(id, text):\n"
    "    print(json.dumps({'type': 'user', 'message': {'content': [\n"
    "        {'type': 'tool_result', 'tool_use_id': id, 'content': text}]}}), flush=True)\n"
    "call('t1', 'TaskCreate', subject='Install ffmpeg', description='d', activeForm='Installing ffmpeg')\n"
    "call('t2', 'TaskCreate', subject='Open the browser', description='d')\n"
    "result('t1', 'Task #1 created successfully: Install ffmpeg')\n"
    "result('t2', 'Task #2 created successfully: Open the browser')\n"
    "call('u1', 'TaskUpdate', taskId='1', status='in_progress')\n"
    "call('b1', 'Bash', command='sudo pacman -S --noconfirm ffmpeg')\n"
    "result('b1', 'installed')\n"
    "call('w1', 'Write', file_path='/home/u/notes.md', content='hi')\n"
    "call('u2', 'TaskUpdate', taskId='1', status='completed')\n"
)


def _planned(then):
    """The planned turn, then what the CLI does next (a line of Python)."""
    return PLANNED + then + "\n"


WORKING_THEN_DONE = (
    "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Working.'}]}}),"
    " flush=True)\n"
    "time.sleep(%s)\n"
    "print(json.dumps({'type': 'result', 'result': 'done', 'session_id': 's1'}), flush=True)")


def _steps(msg):
    return [(s["id"], s["status"]) for s in msg["steps"]]


@pytest.mark.asyncio
async def test_the_plan_reaches_every_client_stamped_with_its_turn_and_whole_each_time(home):
    d = agentd.AgentD(Scripted(_planned(WORKING_THEN_DONE % 0)), agentd._NoSnapshots())
    server, r, w = await _start(d)
    other_r, other_w = await _client(d.socket_path)   # a second bar hears the same
    await other_r.readline()
    await other_r.readline()
    await _ask(w, "install ffmpeg and open the browser")
    msgs = await _read_until(r, "turn_end")
    plans = [m for m in msgs if m.get("kind") == "plan"]
    assert all(m["type"] == "event" and m["turn"] == 1 for m in plans)
    assert [_steps(m) for m in plans] == [
        [(None, "pending")],
        [(None, "pending"), (None, "pending")],
        [("1", "pending"), (None, "pending")],
        [("1", "pending"), ("2", "pending")],
        [("1", "in_progress"), ("2", "pending")],
        [("1", "completed"), ("2", "pending")]]
    assert plans[-1]["steps"] == [
        {"id": "1", "subject": "Install ffmpeg", "active": "Installing ffmpeg", "status": "completed"},
        {"id": "2", "subject": "Open the browser", "active": "Opening the browser", "status": "pending"}]
    heard = await _read_until(other_r, "turn_end")
    assert [m for m in heard if m.get("kind") == "plan"] == plans
    # It is in the turn's log too, where the details drawer reads from.
    log = [json.loads(line) for line in d.turn_logs[1].read_text().splitlines()]
    assert [e["kind"] for e in log].count("plan") == len(plans)
    w.close()
    other_w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_status_lines_of_a_step_say_what_the_turn_has_touched_so_far(home):
    d = agentd.AgentD(Scripted(_planned(WORKING_THEN_DONE % 0)), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "install ffmpeg")
    msgs = await _read_until(r, "turn_end")
    steps = [m for m in msgs if m.get("kind") == "status" and m.get("source") == "step"]
    assert [(m["text"], m["touched"], m["touched_text"]) for m in steps] == [
        ("Planning the steps", {}, ""),
        ("Installing ffmpeg", {}, ""),
        ("Installing ffmpeg", {"package": 1}, "1 package so far"),
        ("Writing notes.md", {"package": 1, "file": 1}, "1 package and 1 file so far")]
    # The agent's own words carry no counts: the desk keeps what it last had.
    said = [m for m in msgs if m.get("kind") == "status" and m.get("source") == "agent"]
    assert said and all("touched" not in m and "touched_text" not in m for m in said)
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_bar_that_joins_mid_turn_can_be_given_the_latest_plan(home):
    d = agentd.AgentD(Scripted(_planned(WORKING_THEN_DONE % 1.5)), agentd._NoSnapshots())
    server, r, w = await _start(d)
    assert d.plan_msg is None
    await _ask(w, "install ffmpeg")
    await _events_until(r, lambda m: m.get("kind") == "text" and m.get("text") == "Working.")
    # Held for the turn as the message the clients got, whole.
    assert d.plan_msg == {"type": "event", "kind": "plan", "turn": 1, "steps": [
        {"id": "1", "subject": "Install ffmpeg", "active": "Installing ffmpeg", "status": "completed"},
        {"id": "2", "subject": "Open the browser", "active": "Opening the browser", "status": "pending"}]}
    await _events_until(r, lambda m: m["type"] == "status" and not m["busy"])
    assert d.plan_msg is None   # a turn's plan is not the next turn's
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_stopped_turn_sends_no_more_plan(home):
    from bombadil import procs
    # Stop (SIGINT) finds it waiting; it writes one more step change on its way out.
    then = ("try:\n    time.sleep(60)\n"
            "except KeyboardInterrupt:\n    call('u3', 'TaskUpdate', taskId='2', status='in_progress')")
    d = agentd.AgentD(Scripted(_planned(then)), agentd._NoSnapshots(), stopper=procs.Stopper(grace=2.0))
    server, r, w = await _start(d)
    await _ask(w, "install ffmpeg")
    await _events_until(r, lambda m: m.get("kind") == "plan" and _steps(m) == [("1", "completed"), ("2", "pending")])
    w.write(b'{"type": "stop"}\n')
    await w.drain()
    msgs = await _read_until(r, "turn_end")
    assert msgs[-1]["stopped"] is True
    assert any(m.get("kind") == "tool" and m.get("id") == "u3" for m in msgs)   # it did write it
    assert not [m for m in msgs if m.get("kind") == "plan"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_turn_says_who_asked_for_it(home):
    slow = ("import json, sys, time\nsys.stdin.read()\ntime.sleep(1.0)\n"
            "print(json.dumps({'type': 'result', 'result': 'ok'}))\n")
    d = agentd.AgentD(Scripted(slow), agentd._NoSnapshots())
    server, r, w = await _start(d)

    async def ask(text, **extra):
        w.write((json.dumps({"type": "prompt", "text": text, **extra}) + "\n").encode())
        await w.drain()
    await ask("first", asked_by="builder")
    await ask("second", asked_by="builder")     # waits, and is taken back before it runs
    await ask("third")                          # typed by the person
    await ask("fourth", asked_by="somebody")    # a helper with another name is still not the person
    await ask("fifth", asked_by="Some Body; rm -rf")   # anything that is no name is taken as typed
    seen = await _events_until(r, lambda m: m.get("kind") == "queued" and m.get("prompt") == "fifth")
    second = next(m for m in seen if m.get("kind") == "queued" and m["prompt"] == "second")
    w.write((json.dumps({"type": "unqueue", "turn": second["turn"]}) + "\n").encode())
    await w.drain()
    for _ in range(4):
        seen += await _read_until(r, "turn_end")
    starts = {m["prompt"]: m["asked_by"] for m in seen if m.get("kind") == "turn_start"}
    assert starts == {"first": "builder", "third": None, "fourth": "somebody", "fifth": None}
    assert d.asked_by == {}   # nothing left over from the turn that was taken back
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_launcher_word_from_a_coding_session_is_still_a_launcher_word(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    w.write(b'{"type": "prompt", "text": "stop", "asked_by": "builder"}\n')
    await w.drain()
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "local", "action": "stop"}
    await _events_until(r, lambda m: m.get("phase") == "done")
    assert d.asked_by == {} and d.turns == 0
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_cli_is_started_with_the_plan_tools_switched_on(home, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_ENABLE_TODO_TOOLS", raising=False)
    script = (
        "import json, os, sys\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text',"
        " 'text': 'todo=' + os.environ.get('CLAUDE_CODE_ENABLE_TODO_TOOLS', 'unset')}]}}), flush=True)\n"
        "print(json.dumps({'type': 'result', 'result': 'ok'}))\n"
    )
    d = agentd.AgentD(Scripted(script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "hello")
    msgs = await _read_until(r, "turn_end")
    assert next(m["text"] for m in msgs if m.get("kind") == "text") == "todo=1"
    # A typed "!command" is not the agent's: it gets the daemon's environment as it is.
    await _ask(w, "!echo todo=${CLAUDE_CODE_ENABLE_TODO_TOOLS:-unset}")
    msgs = await _read_until(r, "turn_end")
    assert [m["text"] for m in msgs if m.get("kind") == "output"] == ["todo=unset"]
    w.close()
    server.cancel()


# -- jobs --

def _jobs_daemon(monkeypatch, provider=None, sd=None):
    """An agentd whose jobs run in a fake systemd, and which looks at them every 50 ms."""
    from bombadil import procs
    monkeypatch.setattr(agentd, "JOBS_POLL", 0.05)
    sd = sd or Systemd()
    return agentd.AgentD(provider or Scripted(DESK_TURN), agentd._NoSnapshots(), stopper=procs.Stopper(grace=1.0),
                         jobs=Jobs(runner=sd)), sd


async def _jobs_table(r):
    return (await _events_until(r, lambda m: m.get("type") == "jobs", 5))[-1]


async def _job_result(r):
    return (await _events_until(r, lambda m: m.get("type") == "job-result", 5))[-1]


async def _heard(r, seconds=0.4):
    """Everything that arrives in the next little while."""
    out = []
    try:
        async with asyncio.timeout(seconds):
            while True:
                out.append(json.loads(await r.readline()))
    except TimeoutError:
        pass
    return out


def _job_line(m):
    return m.get("type") == "event" and m.get("kind") == "local" and m.get("action") == "job"


def _table_with(state):
    return lambda m: m.get("type") == "jobs" and any(x["state"] == state for x in m["jobs"])


async def _start_job(tool_r, tool_w, title="Ubuntu 26.04 ISO", command="curl -O https://example.org/ubuntu.iso",
                     **more):
    """The os-mcp server asks for a job in turn 1. Returns its id and what the agent was told."""
    await _say(tool_w, {"type": "job-tool", "id": "s", "turn": 1, "op": "start", "title": title,
                        "command": command, **more})
    res = await _job_result(tool_r)
    assert res["ok"] is True, res
    return re.search(r"job ([0-9a-f]+)", res["text"]).group(1), res["text"]


def _finish(job_id, code=0, output=""):
    """What the job's own wrapper does when its command ends."""
    if output:
        (paths.jobs_dir() / f"{job_id}.log").write_text(output)
    (paths.jobs_dir() / f"{job_id}.exit").write_text(f"{code}\n")


@pytest.mark.asyncio
async def test_the_job_tool_starts_a_job_in_the_running_turn_and_the_shell_sees_the_table(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    # Starting a job is ordinary work: this turn's words say nothing about the desk.
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, said = await _start_job(tool_r, tool_w)
    assert said.startswith(f"Started Ubuntu 26.04 ISO as job {job_id}.") and str(paths.jobs_dir()) in said
    argv = sd.calls[0]      # and the loop that looks at it asks systemd whether it still runs
    assert argv[:4] == ["systemd-run", "--user", f"--unit=bombadil-job-{job_id}", "--description=Ubuntu 26.04 ISO"]
    for rd in (r, tool_r):   # everyone is told, the shell among them
        rows = (await _jobs_table(rd))["jobs"]
        assert [(x["id"], x["title"], x["kind"], x["state"], x["unit"], x["pct"], x["last"]) for x in rows] == [
            (job_id, "Ubuntu 26.04 ISO", "job", "running", f"bombadil-job-{job_id}", None, "")]
    msgs = await _stop(r, w, server, tool_w)
    assert not [m for m in msgs if m.get("type") == "job-result"]     # only the asker was answered


@pytest.mark.asyncio
async def test_progress_reaches_the_shell_once_per_change(home, monkeypatch):
    d, _ = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, _ = await _start_job(tool_r, tool_w)
    await _jobs_table(r)
    assert not [m for m in await _heard(r) if m.get("type") == "jobs"]   # polled many times, nothing new to say
    (paths.jobs_dir() / f"{job_id}.log").write_text("12 MB of 30\n 43%  12 MB/s\n")
    row = (await _jobs_table(r))["jobs"][0]
    assert (row["pct"], row["last"], row["state"]) == (43.0, "43%  12 MB/s", "running")
    assert not [m for m in await _heard(r) if m.get("type") == "jobs"]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_when_a_job_ends_the_pill_says_so_and_the_next_turn_is_told(home, monkeypatch):
    d, _ = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, _ = await _start_job(tool_r, tool_w)
    _finish(job_id, 0, "100%\n")
    msgs = await _all_of(r, _job_line, _table_with("done"))
    line = next(m for m in msgs if _job_line(m))
    # One line in the pill, as a launcher word's answer is: a local event with no turn.
    assert re.fullmatch(r"Ubuntu 26\.04 ISO is done in \d+ s\.", line["text"])
    assert (line["turn"], line["phase"], line["ok"], line["target"]) == (None, "done", True, job_id)
    done = next(x for m in msgs if _table_with("done")(m) for x in m["jobs"])
    assert (done["state"], done["pct"], done["last"]) == ("done", 100.0, "100%") and done["ended"] >= done["started"]
    assert not [m for m in await _heard(r) if _job_line(m)]          # said once
    # The agent, not the pill, is told in its next prompt.
    await _say(w, {"type": "stop"})
    await _read_until(r, "turn_end")
    await _ask(w, "hello")
    text = (await _events_until(r, lambda m: m.get("kind") == "text"))[-1]["text"]
    assert f"[Done by the user without you since your last turn: background job {job_id}: {line['text']}]\n\nhello" in text
    log = [json.loads(x) for x in paths.turns_log().read_text().splitlines()]
    assert [(x["prompt"], x["action"], x["target"], x["result"], x["ok"]) for x in log if x.get("kind") == "local"] == [
        ("background job", "job", "Ubuntu 26.04 ISO", line["text"], True)]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_failed_job_says_its_last_line_and_where_the_rest_is(home, monkeypatch):
    d, _ = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "update the system")
    job_id, _ = await _start_job(tool_r, tool_w, "Build", "make")
    _finish(job_id, 2, "cc -o a a.c\npacman: could not resolve host\n")
    msgs = await _all_of(r, _job_line, _table_with("failed"))
    line = next(m for m in msgs if _job_line(m))
    assert (line["text"], line["ok"]) == ("Build failed: pacman: could not resolve host", False)
    failed = next(x for m in msgs if _table_with("failed")(m) for x in m["jobs"])
    assert (failed["state"], failed["last"]) == ("failed", "pacman: could not resolve host")
    assert d.notes == [(f"background job {job_id}: Build failed: pacman: could not resolve host "
                       f"(its output is in {paths.jobs_dir()}/{job_id}.log)")]
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_timer_ends_with_its_line(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "tell me when ten minutes are up")
    job_id, said = await _start_job(tool_r, tool_w, "", "", seconds=600)
    assert said == f"Timer set for 10 min as job {job_id}. The desk counts it down and says so when it is up."
    assert sd.calls[0][2:5] == [f"--unit=bombadil-timer-{job_id}", "--on-active=600s",
                                "--timer-property=AccuracySec=1s"]
    row = (await _jobs_table(r))["jobs"][0]
    assert (row["kind"], row["title"]) == ("timer", "Timer, 10 min")
    assert row["deadline"] - row["started"] == pytest.approx(600)
    _finish(job_id)
    line = next(m for m in await _all_of(r, _job_line) if _job_line(m))
    assert (line["text"], line["ok"]) == ("Timer, 10 min is up.", True)
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_shell_stops_a_job_without_the_model(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, _ = await _start_job(tool_r, tool_w)
    await _jobs_table(r)
    await _say(w, {"type": "jobs", "op": "stop", "id": job_id})
    assert (await _jobs_table(r))["jobs"] == []                 # the row is gone at once
    assert sd.of("stop") == [["systemctl", "--user", "stop", f"bombadil-job-{job_id}"]]
    heard = await _heard(r)
    assert not [m for m in heard if m.get("type") == "jobs" or _job_line(m)]   # a × is its own answer: no line
    assert d.notes == [f"background job {job_id}: Stopped Ubuntu 26.04 ISO."]
    log = [json.loads(x) for x in paths.turns_log().read_text().splitlines()]
    assert [(x["prompt"], x["result"]) for x in log if x.get("kind") == "local"] == [
        ("background job", "Stopped Ubuntu 26.04 ISO.")]
    # An id that is no job, and one that is not an id, do nothing at all.
    calls = len(sd.calls)
    for bad in ("ffffff", "../../etc", None, 7, ""):
        await _say(w, {"type": "jobs", "op": "stop", "id": bad})
    await _say(w, {"type": "jobs", "op": "stop"})
    heard = await _heard(r)
    assert not [m for m in heard if m.get("type") == "jobs" or _job_line(m)]
    assert len(sd.calls) == calls and len(d.notes) == 1
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_unit_that_will_not_stop_is_said_on_the_line_and_keeps_its_row(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, _ = await _start_job(tool_r, tool_w)
    sd.stuck.add(f"bombadil-job-{job_id}.service")
    await _say(w, {"type": "jobs", "op": "stop", "id": job_id})
    line = next(m for m in await _all_of(r, _job_line) if _job_line(m))
    assert line["ok"] is False
    assert line["text"] == "Could not stop Ubuntu 26.04 ISO: Failed to stop unit: Access denied."
    assert d.jobs.running() and d.notes == []
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_shell_drops_a_finished_row_and_asks_why_a_job_failed(home, monkeypatch):
    d, _ = _jobs_daemon(monkeypatch)
    shown = []
    monkeypatch.setattr(d.launcher, "details", lambda argv, toggle=False: shown.append((argv, toggle)) or "shown")
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "update the system")
    job_id, _ = await _start_job(tool_r, tool_w, "Build", "make")
    _finish(job_id, 1, "boom\n")
    await _all_of(r, _table_with("failed"))
    # Why? shows the output in the drawer, the way history does; again closes it (the drawer's toggle).
    await _say(w, {"type": "jobs", "op": "why", "id": job_id})
    for _ in range(50):
        if shown:
            break
        await asyncio.sleep(0.02)
    assert shown == [(d.jobs.why(job_id), True)]
    assert shown[0][0][:2] == ["bash", "-c"] and shown[0][0][4] == str(paths.jobs_dir() / f"{job_id}.log")
    await _say(w, {"type": "jobs", "op": "why", "id": "ffffff"})
    await _say(w, {"type": "jobs", "op": "why", "id": "../x"})
    await _say(w, {"type": "jobs", "op": "dismiss", "id": job_id})
    assert (await _jobs_table(r))["jobs"] == []
    assert len(shown) == 1 and d.notes[-1].startswith(f"background job {job_id}: Build failed")
    assert len(d.notes) == 1        # dismissing changed nothing the agent needs to hear

    def broken(argv, toggle=False):
        raise RuntimeError("foot is not installed")
    monkeypatch.setattr(d.launcher, "details", broken)       # a drawer that cannot open says so on the line
    job2, _ = await _start_job(tool_r, tool_w, "Again", "make")
    await _say(w, {"type": "jobs", "op": "why", "id": job2})
    line = (await _events_until(r, lambda m: _job_line(m) and "show" in m["text"]))[-1]
    assert (line["text"], line["ok"]) == ("Could not show the output: foot is not installed", False)
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_shell_asks_for_the_table_and_a_running_one_is_in_the_desks_reply(home, monkeypatch):
    d, _ = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, _ = await _start_job(tool_r, tool_w)
    await _jobs_table(r)
    other_r, other_w = await _another(d)
    await _say(other_w, {"type": "jobs", "op": "get"})
    table = json.loads(await asyncio.wait_for(other_r.readline(), 5))
    assert table == d.jobs.snapshot() and [x["id"] for x in table["jobs"]] == [job_id]
    await _say(other_w, {"type": "desk", "op": "get"})
    got = [json.loads(await asyncio.wait_for(other_r.readline(), 5)) for _ in range(2)]
    assert [m["type"] for m in got] == ["desk", "jobs"] and got[1] == table
    assert not [m for m in await _heard(other_r, 0.2) if m.get("type") in ("desk", "jobs")]
    assert not [m for m in await _heard(r, 0.2) if m.get("type") in ("desk", "jobs")]   # asking is for the asker
    other_w.close()
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_job_tool_starts_only_from_the_turn_that_is_running(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    no = {"type": "job-result", "id": "x", "ok": False, "text": "That turn is over, so nothing was started."}
    for turn in (0, 2, None, "1", 99, True):
        await _say(tool_w, {"type": "job-tool", "id": "x", "turn": turn, "op": "start", "title": "T",
                            "command": "true"})
        assert await _job_result(tool_r) == no
    await _say(tool_w, {"type": "job-tool", "id": "x", "op": "start", "title": "T", "command": "true"})   # no turn
    assert await _job_result(tool_r) == no
    d.stopping = True      # Stop was pressed: no new job starts on the way out
    await _say(tool_w, {"type": "job-tool", "id": "x", "turn": 1, "op": "start", "title": "T", "command": "true"})
    assert (await _job_result(tool_r))["text"] == "That turn is being stopped, so nothing was started."
    d.stopping = False
    assert sd.calls == [] and d.jobs.snapshot()["jobs"] == []
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_job_tool_says_why_in_plain_words(home, monkeypatch):
    d, sd = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    sd.refuse = "Failed to connect to bus: No medium found\n"
    cases = [
        ({"op": "list"}, True, "Nothing is running in the background."),
        ({"op": "start", "title": "T", "command": ""}, False, "Say what to run: the command is empty."),
        ({"op": "start", "title": "", "command": "true"}, False,
         "Give the job a short name for the desk to show, like “Ubuntu 26.04 ISO”."),
        ({"op": "start", "title": "T", "command": "true", "kind": "daemon"}, False,
         "There is no kind of job called 'daemon'. Use job or watch, or give seconds for a timer."),
        ({"op": "start", "title": "T", "seconds": 0}, False, "A timer runs for between 1 second and 7 days."),
        ({"op": "start", "title": "T", "command": "true"}, False,
         "Could not start T: Failed to connect to bus: No medium found."),
        ({"op": "stop"}, False, "Which job? `list` says which are running."),
        ({"op": "stop", "job": "abcdef"}, False, "There is no job 'abcdef'. `list` says which are running."),
        ({"op": "stop", "job": "../../etc/passwd"}, False,
         "There is no job '../../etc/passwd'. `list` says which are running."),
        ({"op": "restart"}, False, "Jobs cannot restart. They can start, list and stop."),
        ({}, False, "Jobs cannot do that. They can start, list and stop."),
    ]
    for i, (msg, ok, text) in enumerate(cases):
        await _say(tool_w, {"type": "job-tool", "id": f"q{i}", "turn": 1, **msg})
        got = await _job_result(tool_r)
        assert (got["id"], got["ok"], got["text"]) == (f"q{i}", ok, text)
    assert d.jobs.snapshot()["jobs"] == [] and len([c for c in sd.calls if c[0] == "systemd-run"]) == 1
    sd.refuse = None
    job_id, _ = await _start_job(tool_r, tool_w, "the build", "tail --pid=1234 -f /dev/null", kind="watch")
    assert [c for c in sd.calls if c[0] == "systemd-run"][-1][2] == f"--unit=bombadil-job-{job_id}"
    assert (await _jobs_table(r))["jobs"][0]["kind"] == "watch"
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_the_agent_can_list_and_stop_jobs_without_a_turn(home, monkeypatch):
    sd = Systemd()
    rec = Jobs(runner=sd).start("Build", "make")         # one a turn started earlier
    d, _ = _jobs_daemon(monkeypatch, providers.Fake("x"), sd)
    server, r, w = await _start(d)
    await _say(w, {"type": "job-tool", "id": "a", "op": "list"})
    got = await _job_result(r)
    assert got["ok"] is True and got["text"].startswith(f"{rec['id']} · Build · running ")
    await _say(w, {"type": "job-tool", "id": "b", "op": "start", "title": "T", "command": "true"})
    assert (await _job_result(r))["text"] == "That turn is over, so nothing was started."
    await _say(w, {"type": "job-tool", "id": "c", "op": "stop", "job": rec["id"]})
    assert await _job_result(r) == {"type": "job-result", "id": "c", "ok": True, "text": "Stopped Build."}
    assert sd.of("stop") == [["systemctl", "--user", "stop", f"bombadil-job-{rec['id']}"]]
    assert (await _jobs_table(r))["jobs"] == [] and d.notes == []   # the agent did it itself: no note
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_loop_runs_only_while_the_table_has_something_in_it_and_stops_with_agentd(home, monkeypatch):
    d, _ = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    await asyncio.sleep(0.3)
    assert d._jobs_task is not None and d._jobs_task.done()         # an empty table is not looked at
    job_id, _ = await _start_job(tool_r, tool_w)
    await asyncio.sleep(0.3)
    assert not d._jobs_task.done()
    await _say(w, {"type": "jobs", "op": "stop", "id": job_id})
    await asyncio.sleep(0.4)
    assert d._jobs_task.done()
    await _start_job(tool_r, tool_w, "Again", "make")               # and it starts again with the next one
    await asyncio.sleep(0.3)
    assert not d._jobs_task.done()
    task = d._jobs_task
    await _stop(r, w, server, tool_w)
    await asyncio.sleep(0.2)
    assert task.done()                                                # agentd went away with a job still running


@pytest.mark.asyncio
async def test_one_bad_look_does_not_end_the_looking(home, monkeypatch, capsys):
    d, _ = _jobs_daemon(monkeypatch)
    server, r, w, tool_r, tool_w, _ = await _in_a_turn(d, "get me the ubuntu iso")
    job_id, _ = await _start_job(tool_r, tool_w)
    real = d.jobs.poll
    calls = []

    def poll():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the disk is full")
        return real()
    monkeypatch.setattr(d.jobs, "poll", poll)
    await asyncio.sleep(0.3)
    _finish(job_id)
    await _all_of(r, _job_line)
    assert "looking at jobs: RuntimeError: the disk is full" in capsys.readouterr().err
    await _stop(r, w, server, tool_w)


@pytest.mark.asyncio
async def test_a_restarted_agentd_counts_what_is_still_running_and_says_when_it_ends(home, monkeypatch):
    monkeypatch.setattr(agentd, "JOBS_POLL", 0.05)
    sd = Systemd()
    rec = Jobs(runner=sd).start("Ubuntu 26.04 ISO", "curl")      # started by the agentd that has since gone
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), jobs=Jobs(runner=sd))
    server, r, w = await _start(d)
    await _say(w, {"type": "jobs", "op": "get"})
    assert [x["id"] for x in (await _jobs_table(r))["jobs"]] == [rec["id"]]
    _finish(rec["id"])
    msgs = await _all_of(r, _job_line, _table_with("done"))
    assert next(m for m in msgs if _job_line(m))["text"].startswith("Ubuntu 26.04 ISO is done in ")
    assert len(d.notes) == 1
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_job_that_ends_while_nobody_is_connected_is_still_noted(home, monkeypatch):
    monkeypatch.setattr(agentd, "JOBS_POLL", 0.05)
    sd = Systemd()
    rec = Jobs(runner=sd).start("Build", "make")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), jobs=Jobs(runner=sd))
    server = asyncio.create_task(d.serve())
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    _finish(rec["id"], 1, "make: *** Error 2\n")
    for _ in range(100):
        if d.notes:
            break
        await asyncio.sleep(0.02)
    assert d.notes[0].startswith(f"background job {rec['id']}: Build failed: make: *** Error 2 (its output is in ")
    r, w = await _another(d)       # the bar that connects later asks, and is told what the table is
    await _say(w, {"type": "jobs", "op": "get"})
    assert (await _jobs_table(r))["jobs"][0]["state"] == "failed"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_real_os_mcp_job_tool_starts_lists_and_stops_a_job_from_inside_a_turn(home, monkeypatch):
    start = Scripted(_mcp_turn({"op": "start", "title": "Build", "command": "make"}, "job"))
    d, sd = _jobs_daemon(monkeypatch, start)
    server, r, w = await _start(d)

    async def call(provider, prompt):
        d.provider = provider
        await _ask(w, prompt)
        msgs = await _read_until(r, "turn_end")
        return json.loads(next(m["text"] for m in msgs if m.get("kind") == "text"))
    said = await call(start, "build it in the background")
    text = said["content"][0]["text"]
    job_id = re.search(r"job ([0-9a-f]+)", text).group(1)
    assert "isError" not in said and text.startswith(f"Started Build as job {job_id}.")
    assert [x["id"] for x in d.jobs.snapshot()["jobs"]] == [job_id]
    assert sd.calls[0][:3] == ["systemd-run", "--user", f"--unit=bombadil-job-{job_id}"]
    said = await call(Scripted(_mcp_turn({"op": "list"}, "job")), "what is running?")
    assert said["content"][0]["text"].startswith(f"{job_id} · Build · running ")
    said = await call(Scripted(_mcp_turn({"op": "stop", "id": job_id}, "job")), "stop the build")
    assert said["content"][0]["text"] == "Stopped Build." and "isError" not in said
    assert d.jobs.snapshot()["jobs"] == [] and sd.of("stop")
    said = await call(Scripted(_mcp_turn({"op": "stop", "id": job_id}, "job")), "stop it again")
    assert said["isError"] is True and said["content"][0]["text"].startswith(f"There is no job '{job_id}'")
    w.close()
    server.cancel()

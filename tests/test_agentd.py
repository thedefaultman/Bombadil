import asyncio
import json

import pytest

from bombadil import agentd, paths, providers


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
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    r, w = await _client(d.socket_path)
    first = json.loads(await r.readline())
    assert first == {"type": "status", "busy": False, "provider": "fake", "turns": 0,
                     "snapshots": False, "queued": 0, "turn": None, "queue": []}
    entries = json.loads(await r.readline())
    assert entries["type"] == "entries" and any(e["name"] == "browser" for e in entries["entries"])
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


async def _start(d):
    server = asyncio.create_task(d.serve())
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    r, w = await _client(d.socket_path)
    await r.readline()   # status
    await r.readline()   # entries
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

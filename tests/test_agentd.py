import asyncio
import json

import pytest

from bombadil import agentd, paths, providers


@pytest.fixture(autouse=True)
def no_machine_captures(monkeypatch):
    """A turn looks at the network, disks, sound and screens before it starts (for its receipt);
    these tests are not about the machine they run on."""
    monkeypatch.setattr(agentd.sysmap, "snapshot", lambda *a, **k: {})


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


WHY_SCRIPT = (
    "import json, sys, time\n"
    "sys.stdin.read()\n"
    "def out(o): print(json.dumps(o), flush=True)\n"
    "out({'type': 'stream_event', 'event': {'type': 'message_start'}})\n"
    "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'w1', 'name': 'WebFetch',"
    " 'input': {'url': 'https://user:pw@wireguard.com/quickstart?x=1'}}]}})\n"
    "out({'type': 'stream_event', 'event': {'type': 'message_start'}})\n"
    "out({'type': 'assistant', 'message': {'content': [{'type': 'text',"
    " 'text': 'Let me check the vendor page. The tunnel needs its tools, so installing them first.'}]}})\n"
    "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'b1', 'name': 'Bash',"
    " 'input': {'command': 'sudo pacman -S --noconfirm wireguard-tools'}}]}})\n"
    "time.sleep(2)\n"
    "out({'type': 'result', 'result': 'Installed.', 'session_id': 's1'})\n"
)


@pytest.mark.asyncio
async def test_a_step_carries_its_reason_and_what_it_followed_and_the_turn_says_what_it_read(home):
    d = agentd.AgentD(Scripted(WHY_SCRIPT.replace("time.sleep(2)", "pass")), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "set up the tunnel")
    msgs = await _read_until(r, "turn_end")
    step = next(m for m in msgs if m.get("kind") == "status" and m.get("risk") == "system")
    assert step["text"] == "Installing wireguard-tools"
    assert step["because"] == "The tunnel needs its tools, so installing them first."
    assert step["after"] == {"label": "wireguard.com/quickstart", "kind": "web",
                             "text": "after reading wireguard.com/quickstart"}
    # The step's own tool event carries them too, for Details.
    tool = next(m for m in msgs if m.get("kind") == "tool" and m["name"] == "Bash")
    assert tool["because"] == step["because"] and tool["after"] == step["after"]
    fetch = next(m for m in msgs if m.get("kind") == "tool" and m["name"] == "WebFetch")
    assert "because" not in fetch and "after" not in fetch
    end = msgs[-1]
    assert end["read"] == [{"label": "wireguard.com/quickstart", "kind": "web", "outside": True}]
    # Never a credential in what is shown: the raw tool event keeps its input, the line and the list do not.
    shown = [m for m in msgs if m.get("kind") == "status"] + [end["read"]]
    assert "user:pw" not in json.dumps(shown) and "x=1" not in json.dumps(shown)
    row = [json.loads(line) for line in paths.turns_log().read_text().splitlines()][-1]
    assert row["read"] == end["read"]


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
async def test_why_during_a_turn_is_answered_from_the_recorded_reason_without_the_model(home):
    d = agentd.AgentD(Scripted(WHY_SCRIPT), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "set up the tunnel")
    await _events_until(r, lambda m: m.get("kind") == "status" and m.get("because"))
    await _ask(w, "Why?")
    msgs = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert {"type": "local", "action": "why"} in msgs
    said = msgs[-1]
    assert said["action"] == "why" and said["ok"] is True
    assert said["text"] == ("The tunnel needs its tools, so installing them first. "
                            "(after reading wireguard.com/quickstart)")
    # No chip, no second turn: it never reached the queue.
    assert d.pending == [] and d.next_id == 1
    await _read_until(r, "turn_end")
    # Once the turn is over "why" is an ordinary question for the agent again.
    await _ask(w, "why")
    msgs = await _events_until(r, lambda m: m.get("type") == "queued")
    assert msgs[-1] == {"type": "queued", "turn": 2}
    w.write(b'{"type": "stop"}\n')
    await w.drain()
    await _read_until(r, "turn_end")


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


# -- pictures --

def _diagram(title="How a VPN works", **over):
    spec = {"shape": "chain", "title": title, "nodes": [{"label": "Laptop"}, {"label": "Tunnel"},
                                                        {"label": "Internet"}]}
    spec.update(over)
    return spec


async def _send_card(path, spec):
    """What os-mcp does: a client of its own sends the card and reads agentd's answer."""
    r, w = await _client(path)
    w.write((json.dumps({"type": "card", "card": spec}) + "\n").encode())
    await w.drain()
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), 5))
        if m.get("type") == "card_ack":
            w.close()
            return m


@pytest.mark.asyncio
async def test_a_card_from_os_mcp_is_checked_and_drawn_by_every_bar(home):
    from bombadil import cards
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    card, errors = cards.validate_diagram(_diagram())
    assert errors == []
    ack = await _send_card(d.socket_path, card)
    assert ack == {"type": "card_ack", "shown": True}
    m = await _events_until(r, lambda m: m.get("kind") == "card")
    ev = m[-1]
    assert ev["turn"] is None and ev["card"]["title"] == "How a VPN works" and ev["card"]["id"] == "card-1"
    assert ev["card"]["text"].startswith("How a VPN works: Laptop → Tunnel → Internet")
    # Not a card: nothing reaches the bar, and the sender is told what to fix.
    bad = await _send_card(d.socket_path, {"shape": "chain", "title": "", "nodes": [{"label": "x" * 40}]})
    assert bad["shown"] is False and any("title is required" in e for e in bad["errors"])


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
    await ask("fourth", asked_by="somebody")    # a name nobody knows
    seen = await _events_until(r, lambda m: m.get("kind") == "queued" and m.get("prompt") == "fourth")
    second = next(m for m in seen if m.get("kind") == "queued" and m["prompt"] == "second")
    w.write((json.dumps({"type": "unqueue", "turn": second["turn"]}) + "\n").encode())
    await w.drain()
    for _ in range(3):
        seen += await _read_until(r, "turn_end")
    starts = {m["prompt"]: m["asked_by"] for m in seen if m.get("kind") == "turn_start"}
    assert starts == {"first": "builder", "third": None, "fourth": None}
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
async def test_a_card_with_no_bar_to_draw_it_says_so(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server = asyncio.create_task(d.serve())
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    from bombadil import cards
    ack = await _send_card(d.socket_path, cards.validate_diagram(_diagram())[0])
    assert ack == {"type": "card_ack", "shown": False}
    server.cancel()


CARD_CHUNKS = ['{"shape": "chain", "title": "How a VP', 'N works", "nodes": [', '{"label": "Laptop"}, ',
               '{"label": "Tunnel", "state": "new"}, ', '{"label": "Internet"}]}']
CARD_SCRIPT = (
    "import json, sys, time\n"
    "sys.stdin.read()\n"
    "def out(o): print(json.dumps(o), flush=True)\n"
    "def ev(e): out({'type': 'stream_event', 'event': e})\n"
    "ev({'type': 'message_start'})\n"
    "ev({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'id': 'toolu_c1',"
    " 'name': 'mcp__bombadil-os__show_card'}})\n"
    f"for c in {CARD_CHUNKS!r}:\n"
    "    ev({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': c}})\n"
    "    time.sleep(0.05)\n"
    "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'toolu_c1',"
    " 'name': 'mcp__bombadil-os__show_card', 'input': {}}]}})\n"
    "time.sleep(2)\n"
    "out({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_c1',"
    " 'content': 'fine', 'is_error': %s}]}})\n"
    "out({'type': 'result', 'result': 'Drew it.', 'session_id': 's1'})\n"
)


@pytest.mark.asyncio
async def test_the_boxes_appear_while_the_card_is_written_and_the_finished_card_takes_their_place(home):
    from bombadil import cards
    d = agentd.AgentD(Scripted(CARD_SCRIPT % "False"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "how does a VPN work?")
    seen = []
    msgs = await _events_until(r, lambda m: (m.get("kind") == "card" and len(m["card"].get("nodes", [])) == 3
                                             and seen.append(m) is None))
    partials = [m["card"] for m in msgs if m.get("kind") == "card"]
    assert all(c["partial"] and c["id"] == "stream-toolu_c1" for c in partials)
    assert [len(c["nodes"]) for c in partials] == sorted(len(c["nodes"]) for c in partials)
    assert partials[0]["title"] == "How a VPN works" and partials[0]["nodes"] == []
    # The finished card arrives from os-mcp with the tool's result still to come.
    final, _ = cards.validate_diagram({**_diagram("How a VPN works")})
    ack = await _send_card(d.socket_path, final)
    assert ack["shown"] is True
    msgs = await _events_until(r, lambda m: m.get("kind") == "card" and not m["card"].get("partial"))
    done = msgs[-1]["card"]
    assert done["id"] == "stream-toolu_c1" and "partial" not in done and done["links"]
    assert done["id"] not in d._stream_ids
    await _read_until(r, "turn_end")
    # Only the finished card is in the turn's log (Details), not its drafts.
    log = paths.state_dir() / "turns"
    logged = [json.loads(line) for f in log.iterdir() for line in f.read_text().splitlines()]
    cards_logged = [e for e in logged if e.get("kind") == "card"]
    assert len(cards_logged) == 1 and cards_logged[0]["turn"] == 1 and not cards_logged[0]["card"].get("partial")
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_card_call_that_failed_takes_its_half_drawn_card_back(home):
    d = agentd.AgentD(Scripted(CARD_SCRIPT.replace("time.sleep(2)", "pass") % "True"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "how does a VPN work?")
    msgs = await _events_until(r, lambda m: m.get("kind") == "card" and m["card"].get("gone"))
    assert msgs[-1]["card"] == {"id": "stream-toolu_c1", "gone": True}
    await _read_until(r, "turn_end")
    assert d._stream_ids == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_half_drawn_card_is_taken_back_when_the_turn_ends(home):
    script = CARD_SCRIPT.replace("time.sleep(2)", "pass").split("out({'type': 'user'")[0] + (
        "out({'type': 'result', 'result': 'Done.', 'session_id': 's1'})\n")
    d = agentd.AgentD(Scripted(script), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "how does a VPN work?")
    msgs = await _read_until(r, "turn_end")
    gone = [m for m in msgs if m.get("kind") == "card" and m["card"].get("gone")]
    assert [g["card"]["id"] for g in gone] == ["stream-toolu_c1"]
    w.close()
    server.cancel()


def _machine(monkeypatch, **over):
    from bombadil import sysmap
    calls = []

    def capture(kind, target="", **kw):
        calls.append((kind, target))
        card, _ = __import__("bombadil.cards", fromlist=["x"]).validate_diagram(
            _diagram("How you're connected", nodes=[{"label": "This laptop"}, {"label": "Wi-Fi"},
                                                   {"label": "Router"}], say="All of it answers."))
        card["source"] = kind
        return {"card": card, "facts": []}
    monkeypatch.setattr(agentd.sysmap, "capture", over.get("capture", capture))
    return calls, sysmap


@pytest.mark.asyncio
async def test_a_picture_word_draws_the_machine_with_no_model_and_no_turn(home, monkeypatch):
    calls, _ = _machine(monkeypatch)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "How am I connected?")
    assert json.loads(await r.readline()) == {"type": "local", "action": "picture"}
    msgs = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    kinds = [(m["kind"], m.get("phase")) for m in msgs]
    assert kinds == [("local", "start"), ("card", None), ("local", "done")]
    assert msgs[0]["text"] == "Drawing how you're connected"
    assert msgs[1]["turn"] is None and msgs[1]["card"]["source"] == "network"
    assert msgs[2]["ok"] is True and msgs[2]["text"] == "All of it answers."
    assert calls == [("network", "")] and d.turns == 0
    # The agent is told at its next turn what the user was shown.
    assert d.notes and "showed a picture" in d.notes[0] and "How you're connected" in d.notes[0]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_picture_that_cannot_be_drawn_says_why_in_a_line(home, monkeypatch):
    from bombadil import sysmap

    def nothing(kind, target="", **kw):
        raise sysmap.Unavailable("Could not read the screens: Hyprland did not answer.")
    _machine(monkeypatch, capture=nothing)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "my screens")
    msgs = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert not any(m.get("kind") == "card" for m in msgs)
    assert msgs[-1]["ok"] is False and msgs[-1]["text"] == "Could not read the screens: Hyprland did not answer."
    w.close()
    server.cancel()


VPN_FACTS_BEFORE = [{"key": "gateway", "label": "Router", "value": "192.168.1.1"}]
VPN_FACTS_AFTER = VPN_FACTS_BEFORE + [{"key": "vpn", "label": "VPN tunnel", "value": "wg0"}]
VPN_SCRIPT = (
    "import json, sys\n"
    "sys.stdin.read()\n"
    "def out(o): print(json.dumps(o), flush=True)\n"
    "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'b1', 'name': 'Bash',"
    " 'input': {'command': %r}}]}})\n"
    "out({'type': 'result', 'result': 'Done.', 'session_id': 's1'})\n"
)


def _snapshots(monkeypatch, before, after):
    seen = []

    def snap(kinds=(), provider="claude", **kw):
        seen.append(tuple(kinds))
        return {"network": before if len(seen) == 1 else after}
    monkeypatch.setattr(agentd.sysmap, "snapshot", snap)
    return seen


@pytest.mark.asyncio
async def test_a_turn_that_changed_the_network_ends_with_a_before_and_after(home, monkeypatch):
    seen = _snapshots(monkeypatch, VPN_FACTS_BEFORE, VPN_FACTS_AFTER)
    d = agentd.AgentD(Scripted(VPN_SCRIPT % "wg-quick up wg0"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "start the vpn")
    msgs = await _events_until(r, lambda m: m.get("kind") == "card")
    end = next(m for m in msgs if m.get("kind") == "turn_end")   # the closing line comes first
    card = msgs[-1]
    assert msgs.index(end) < msgs.index(card)
    assert card["turn"] == 1 and card["card"]["receipt"] is True and card["card"]["shape"] == "compare"
    assert [n["label"] for n in card["card"]["nodes"]] == ["VPN tunnel"]
    assert card["card"]["nodes"][0]["side"] == "after" and card["card"]["nodes"][0]["state"] == "new"
    assert seen[0] == agentd.sysmap.BEFORE_KINDS and seen[1] == ("network",)   # only what it touched, after
    w.close()
    server.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("command,after", [("wg-quick up wg0", VPN_FACTS_BEFORE), ("ls ~", VPN_FACTS_AFTER)])
async def test_no_receipt_when_nothing_it_touched_changed_or_it_touched_nothing(home, monkeypatch, command, after):
    _snapshots(monkeypatch, VPN_FACTS_BEFORE, after)
    d = agentd.AgentD(Scripted(VPN_SCRIPT % command), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "go")
    msgs = await _read_until(r, "turn_end")
    await _quiet(d)
    w.write(b'{"type": "status"}\n')
    await w.drain()
    msgs += await _events_until(r, lambda m: m.get("type") == "status" and not m["busy"])
    assert not any(m.get("kind") == "card" for m in msgs)
    w.close()
    server.cancel()


DRAW_SCRIPT = (VPN_SCRIPT.replace("'name': 'Bash'", "'name': 'mcp__bombadil-os__system_map'")
               .replace("{'command': %r}", "{'kind': 'network'}"))


@pytest.mark.asyncio
@pytest.mark.parametrize("script,explain", [(DRAW_SCRIPT, "normal"), (VPN_SCRIPT % "wg-quick up wg0", "brief")])
async def test_no_receipt_when_the_agent_drew_a_picture_itself_or_explaining_is_off(home, monkeypatch, script,
                                                                                     explain):
    seen = _snapshots(monkeypatch, VPN_FACTS_BEFORE, VPN_FACTS_AFTER)
    d = agentd.AgentD(Scripted(script), agentd._NoSnapshots(), explain=explain)
    server, r, w = await _start(d)
    await _ask(w, "go")
    msgs = await _read_until(r, "turn_end")
    await _quiet(d)
    assert not any(m.get("kind") == "card" for m in msgs)
    if explain == "brief":
        assert seen == []   # not even looked at
    w.close()
    server.cancel()


async def _quiet(d, limit=3.0):
    """Until the receipt task, if any, has finished."""
    loop = asyncio.get_running_loop()
    end = loop.time() + limit
    await asyncio.sleep(0.05)
    while d._tasks and loop.time() < end:
        await asyncio.sleep(0.05)


def _service_facts(monkeypatch, before, after):
    states = {"before": before}
    monkeypatch.setattr(agentd.sysmap, "snapshot_service", lambda unit, *a, **k: states.pop("before", None) or after)


@pytest.mark.asyncio
async def test_a_service_receipt_shows_how_its_state_changed(home, monkeypatch):
    _service_facts(monkeypatch, [{"key": "state", "label": "wg-quick@wg0", "value": "failed (failed)"}],
                   [{"key": "state", "label": "wg-quick@wg0", "value": "active (exited)"}])
    d = agentd.AgentD(Scripted(VPN_SCRIPT % "sudo systemctl restart wg-quick@wg0"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "restart the vpn")
    msgs = await _events_until(r, lambda m: m.get("kind") == "card")
    card = msgs[-1]["card"]
    assert card["receipt"] and card["title"] == "wg-quick@wg0, before and after"
    assert [n["sub"] for n in card["nodes"]] == ["failed (failed)", "active (exited)"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_before_caught_while_the_service_was_already_restarting_is_no_before(home, monkeypatch):
    _service_facts(monkeypatch, [{"key": "state", "label": "wg-quick@wg0", "value": "activating (start)"}],
                   [{"key": "state", "label": "wg-quick@wg0", "value": "active (exited)"}])
    d = agentd.AgentD(Scripted(VPN_SCRIPT % "sudo systemctl restart wg-quick@wg0"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _ask(w, "restart the vpn")
    msgs = await _read_until(r, "turn_end")
    await _quiet(d)
    assert not any(m.get("kind") == "card" for m in msgs)


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


# -- a click on a box in a picture --

class _Opens:
    def __init__(self, result=(True, "Showing NetworkManager.service.")):
        self.calls, self.result = [], result

    def __call__(self, kind, value):
        self.calls.append((kind, value))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def _open(d, **msg):
    server, r, w = await _start(d)
    w.write((json.dumps({"type": "open", **msg}) + "\n").encode())
    await w.drain()
    got = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("action") == "open")
    w.close()
    server.cancel()
    return got[-1]


@pytest.mark.asyncio
async def test_a_box_that_names_a_service_opens_it_and_the_line_says_so(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    opens = _Opens()
    monkeypatch.setattr(d.launcher, "open_thing", opens)
    ev = await _open(d, kind="unit", value="NetworkManager.service")
    assert opens.calls == [("unit", "NetworkManager.service")]
    assert ev["ok"] is True and ev["text"] == "Showing NetworkManager.service." and ev["turn"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", [{"kind": "unit", "value": "x; rm -rf ~"}, {"kind": "path", "value": "relative/file"},
                                 {"kind": "url", "value": "file:///etc/passwd"}, {"kind": "shell", "value": "ls"},
                                 {"kind": "package", "value": "a b"}, {}])
async def test_what_a_box_names_is_checked_again_here(home, monkeypatch, msg):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    opens = _Opens()
    monkeypatch.setattr(d.launcher, "open_thing", opens)
    ev = await _open(d, **msg)
    assert opens.calls == [] and ev["ok"] is False and ev["text"].startswith("Cannot open that: ")


@pytest.mark.asyncio
async def test_something_that_breaks_while_opening_is_one_plain_line(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    monkeypatch.setattr(d.launcher, "open_thing", _Opens(RuntimeError("foot is not installed")))
    ev = await _open(d, kind="unit", value="sshd.service")
    assert ev["ok"] is False and "sshd.service" in ev["text"] and "foot is not installed" in ev["text"]


@pytest.mark.asyncio
async def test_a_box_that_names_a_turn_shows_its_details_when_they_are_kept(home, monkeypatch, tmp_path):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    shown = []
    monkeypatch.setattr(d.launcher, "details", lambda argv, toggle=False: shown.append(argv) or "shown")
    d.turn_logs[4] = tmp_path / "4.jsonl"
    server, r, w = await _start(d)
    w.write(b'{"type": "open", "kind": "turn", "value": "4"}\n')
    await w.drain()
    await _quiet(d)
    assert len(shown) == 1 and shown[0][-2:] == ["--file", str(tmp_path / "4.jsonl")]
    w.write(b'{"type": "open", "kind": "turn", "value": "9"}\n')
    await w.drain()
    got = await _events_until(r, lambda m: m.get("kind") == "local" and m.get("action") == "open")
    assert got[-1]["ok"] is False and got[-1]["text"] == "The details of turn 9 are not kept." and len(shown) == 1
    w.close()
    server.cancel()

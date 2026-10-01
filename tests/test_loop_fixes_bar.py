"""Fixes from the second review of the loop, in the part that sees where asks come from, what the
prober's collectors look at, and the bar: one test per defect, each of which failed before its fix."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_agentd_signin import (  # noqa: E402,F401  (the harness of a login that dies mid-turn)
    GoneLogin, _send, _setup, _start, _until, signed_out)
from test_loop_cli import machine as cli_machine  # noqa: E402,F401  (the stand-in machine the commands run on)
from test_loop_runner import NOW, program  # noqa: E402
from test_loop_service import machine, passwords_asks, rig_of  # noqa: E402,F401  (their machine, and a service on it)
from test_signin import FakePanel  # noqa: E402

import test_loop_signals as sig_tests  # noqa: E402

from bombadil import agentd, paths, providers  # noqa: E402
from bombadil.loop import probes, runner, signals, words  # noqa: E402
from bombadil.loop.probes import Observation, Result  # noqa: E402
from bombadil.loop.signals import Signals  # noqa: E402

T0 = 1_790_680_000.0


# -- where an ask comes from --

def test_bombadil_ask_says_it_came_from_the_command_line(cli_machine):
    server = cli_machine.agentd(lambda msg: [{"type": "queued", "turn": "t1"},
                                             {"type": "event", "turn": "t1", "kind": "turn_end"}]
                                if msg.get("type") == "prompt" else [])
    got = cli_machine.run("ask", "tidy", "my", "downloads")
    assert got.returncode == 0
    prompts = [m for m in server.got if m.get("type") == "prompt"]
    assert prompts == [{"type": "prompt", "text": "tidy my downloads", "origin": "cli"}]


def _rows() -> list[dict]:
    """The ledger's turns (the sign-in has its own row between them)."""
    rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    return [row for row in rows if "prompt" in row]


@pytest.mark.asyncio
@pytest.mark.parametrize("sent, origin", [({"origin": "loop"}, "loop"), ({"origin": "routine"}, "routine"),
                                          ({"origin": "cli"}, "cli"), ({"text": "[from app notes] hi"}, "app"),
                                          ({}, "typed")])
async def test_a_turn_run_again_after_a_sign_in_keeps_who_asked(signed_out, sent, origin):
    (Path.home() / ".fake-signin").write_text("stale")
    d = agentd.AgentD(GoneLogin("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    (Path.home() / ".fake-signin").unlink()            # the token died
    await _send(w, {"type": "prompt", "text": "hello", **sent})
    await _until(r, lambda m: m.get("kind") == "turn_end")
    await _until(r, _setup("ready"))
    await _until(r, lambda m: m.get("kind") == "turn_end")
    assert [row["origin"] for row in _rows()] == [origin, origin]
    assert not d._origins
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_coding_sessions_turn_run_again_after_a_sign_in_is_still_the_sessions(signed_out):
    (Path.home() / ".fake-signin").write_text("stale")
    d = agentd.AgentD(GoneLogin("x"), agentd._NoSnapshots(), panel=FakePanel())
    server, r, w, _ = await _start(d)
    (Path.home() / ".fake-signin").unlink()
    await _send(w, {"type": "prompt", "text": "hello", "asked_by": "builder"})
    first = await _until(r, lambda m: m.get("kind") == "turn_end")
    await _until(r, _setup("ready"))
    second = await _until(r, lambda m: m.get("kind") == "turn_end")
    starts = [m for m in first + second if m.get("kind") == "turn_start"]
    assert [m["asked_by"] for m in starts] == ["builder", "builder"]
    assert [row["origin"] for row in _rows()] == ["session", "session"]
    assert d.turn_prompt is None and not d.asked_by
    w.close()
    server.cancel()


# -- what the prober's collectors look at --

@pytest.fixture
def runtime(home, monkeypatch):
    """A runtime directory short enough for sockets, and a PATH of stand-ins only."""
    short = Path(tempfile.mkdtemp(prefix="rn"))
    (short / "bin").mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short))
    monkeypatch.setenv("PATH", str(short / "bin"))
    monkeypatch.setattr(runner, "_stuck", 0)
    runner._complained.clear()
    yield short
    shutil.rmtree(short, ignore_errors=True)


class Compositor:
    """A stand-in Hyprland on an instance's socket: every request gets `answer`."""

    def __init__(self, runtime: Path, sig: str, answer: str = "[]", pid: int | None = None):
        self.dir = runtime / "hypr" / sig
        self.dir.mkdir(parents=True)
        self.answer = answer
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.dir / ".socket.sock"))
        self.sock.listen(4)
        if pid is not None:
            (self.dir / "hyprland.lock").write_text(f"{pid}\nwayland-1\n")
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.recv(4096)
                conn.sendall(self.answer.encode())

    def crash(self):
        """It is gone and its files are not: what a compositor that crashed leaves behind."""
        self.sock.close()

    def quit(self):
        self.sock.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def _dead_pid() -> int:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    return gone.pid


def test_the_collectors_follow_a_hyprland_that_restarted(runtime, monkeypatch):
    first = Compositor(runtime, "old_1", '[{"title": "first"}]')
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "old_1")
    c = runner.Collectors()
    c.begin()
    assert c.clients() == [{"title": "first"}]
    first.quit()                                               # greetd starts a new one, under a new signature
    second = Compositor(runtime, "new_2", '[{"title": "second"}]')
    c.begin()
    assert c.clients() == [{"title": "second"}]
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "new_2"
    second.quit()


def test_a_crashed_hyprland_that_left_its_socket_behind_is_not_the_one_to_ask(runtime, monkeypatch):
    first = Compositor(runtime, "old_1", pid=_dead_pid())
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "old_1")
    first.crash()
    second = Compositor(runtime, "new_2", '[{"title": "second"}]', pid=os.getpid())
    assert (first.dir / ".socket.sock").exists()
    assert runner.find_instance() == "new_2"
    assert runner.socket2_path() == second.dir / ".socket2.sock"
    c = runner.Collectors()
    c.begin()
    assert c.clients() == [{"title": "second"}]
    second.quit()


def test_an_instance_that_names_a_live_process_or_none_is_running(runtime):
    Compositor(runtime, "named_1", pid=os.getpid())
    Compositor(runtime, "bare_2")                              # no lock to read: the socket is all there is
    assert runner._running(runtime / "hypr" / "named_1") and runner._running(runtime / "hypr" / "bare_2")
    for said in ("not a pid\n", "-5\n", "0\n", ""):
        (runtime / "hypr" / "bare_2" / "hyprland.lock").write_text(said)
        assert runner._running(runtime / "hypr" / "bare_2")
    assert not runner._running(runtime / "hypr" / "absent_3")


def _coredumps(bindir, rows, infos, log=None):
    other = "'PID: ' + pid + ' (python3)\\n  Command Line: /usr/bin/python3 /home/dev/script.py'"
    return program(bindir, "coredumpctl", f"""
if sys.argv[1] == 'list':
    print(json.dumps({rows!r}))
else:
    pid = sys.argv[-1]
    print({infos!r}.get(pid, {other}))
""", log)


def test_the_newest_python_dumps_are_the_ones_that_are_asked_about(runtime, tmp_path):
    def at(ago):
        return int((NOW - ago) * 1e6)
    other = [{"time": at(5 * 86400 - i * 60), "pid": 200 + i, "sig": 11, "exe": "/usr/bin/python3.12"}
             for i in range(12)]
    ours = {"time": at(60), "pid": 999, "sig": 11, "exe": "/usr/bin/python3.12"}
    infos = {"999": "PID: 999 (python3)\n  Command Line: /usr/bin/python3 /usr/local/bin/agentd"}
    runs = tmp_path / "runs"
    _coredumps(runtime / "bin", [*other, ours], infos, runs)       # `coredumpctl list` is oldest first
    got = runner.Collectors(hyprland=object(), clock=lambda: NOW)._list_coredumps()
    assert [r["pid"] for r in got] == [999] and got[0]["cmdline"].endswith("/usr/local/bin/agentd")
    assert len(runs.read_text()) <= 1 + runner.COREDUMP_LOOKS      # the list, and no more than ten looks


def test_the_dumps_asked_about_are_kept_in_the_lists_order(runtime):
    def at(ago):
        return int((NOW - ago) * 1e6)
    rows = [{"time": at(7200 - i), "pid": 300 + i, "sig": 6, "exe": "/usr/bin/python3.12"} for i in range(14)]
    infos = {str(pid): f"PID: {pid} (python3)\n  Command Line: /usr/bin/python3 /usr/local/bin/agentd"
             for pid in (300, 311, 313)}
    _coredumps(runtime / "bin", rows, infos)
    got = runner.Collectors(hyprland=object(), clock=lambda: NOW)._list_coredumps()
    assert [r["pid"] for r in got] == [311, 313]                    # 300 is older than the newest ten


# -- what a finding about agentd or the bar can read --

def test_agentd_and_the_bar_have_no_unit_so_no_journal_is_read_for_them(runtime, tmp_path):
    ran = tmp_path / "ran"
    program(runtime / "bin", "journalctl", "print('a\\nb')", ran)
    r = runner.Runner({}, None, None)
    for component, probe in (("agentd", "agentd-ping"), ("bar", "bar-alive")):
        assert r._log_for(Result(False, probe, component, probe, "x", "y"), Observation()) == []
    assert r._log_for(Result(False, "x", "unknown", "x", "x", "y"), Observation()) == []
    assert not ran.exists()


# -- the bar is not to blame for agentd being away --

def _bar_alive(**kw) -> Result:
    return probes.run_probe("bar-alive", Observation(bar={"alive_at": T0 - 40}, now=T0, **kw))[0]


def test_a_bar_that_cannot_reach_agentd_is_not_called_stopped():
    down = {"connected": False, "ponged": False, "error": "ConnectionRefusedError"}
    assert _bar_alive(agentd=down, agentd_started=T0 - 3600).ok is None
    assert _bar_alive(agentd={"connected": True, "ponged": False}, agentd_started=T0 - 3600).ok is None
    up = {"connected": True, "ponged": True, "latency": 0.002}
    assert _bar_alive(agentd=up, agentd_started=T0 - 3600).ok is False     # agentd is there and the bar is not
    assert _bar_alive(agentd_started=T0 - 3600).ok is None                 # nothing known of agentd: not checked


def test_asking_for_the_bar_alone_still_looks_at_agentd():
    assert "agentd" in runner.Runner._fields(["bar-alive"])


# -- a second tap on Super gives the pill back: that summon is not one that got no keyboard --

def _summon_rows(tmp_path, *, cancel: bool):
    clock = [T0]
    s = Signals(tmp_path / "loop", lambda: clock[0])
    s.hello({"type": "hello", "client": "bar", "pid": 7, "build": "b"})
    first = s.summon("Virtual-1")
    clock[0] += 0.1
    s.focus_ack({"id": first, "ms": 90})                      # the first tap gave the pill the keyboard
    clock[0] += 3
    second = s.summon("Virtual-1")                              # the second tap gives it back
    if cancel:
        clock[0] += 0.05
        s.focus_cancel({"type": "focus_cancel", "id": second})
    clock[0] += 5
    s.tick()
    return signals.read_events(tmp_path / "loop" / "signals.jsonl"), clock[0]


def test_a_summon_given_back_is_written_as_cancelled_and_never_times_out(tmp_path):
    rows, now = _summon_rows(tmp_path, cancel=True)
    assert [(r["kind"], r.get("id")) for r in rows if r["kind"] != "hello"] == [
        ("summon", 1), ("focus_ack", 1), ("summon", 2), ("focus_cancel", 2)]
    got = probes.run_probe("summon-focus", Observation(events=rows, now=now))
    assert [r.ok for r in got] == [True]


def test_a_summon_nobody_answered_is_still_red(tmp_path):
    rows, now = _summon_rows(tmp_path, cancel=False)
    assert [r["kind"] for r in rows][-1] == "focus_timeout"
    got = probes.run_probe("summon-focus", Observation(events=rows, now=now))
    assert [r.ok for r in got] == [False]


def test_a_cancel_that_comes_after_a_timeout_still_clears_that_summon(tmp_path):
    rows, now = _summon_rows(tmp_path, cancel=False)
    rows.append({"t": now + 1, "kind": "focus_cancel", "id": 2})
    assert [r.ok for r in probes.run_probe("summon-focus", Observation(events=rows, now=now + 2))] == [True]


def test_only_the_summon_that_was_given_back_is_cancelled(tmp_path):
    clock = [T0]
    s = Signals(tmp_path / "loop", lambda: clock[0])
    s.hello({"type": "hello", "client": "bar", "pid": 7, "build": "b"})
    stuck, back = s.summon("Virtual-1"), s.summon("Virtual-1")
    s.focus_cancel({"id": back})
    s.focus_cancel({"id": "x"})                                  # not an id: nothing is written
    s.focus_cancel({})
    clock[0] += 5
    s.tick()
    rows = signals.read_events(tmp_path / "loop" / "signals.jsonl")
    assert [(r["kind"], r["id"]) for r in rows if r["kind"] in ("focus_cancel", "focus_timeout")] == [
        ("focus_cancel", back), ("focus_timeout", stuck)]
    assert [r.ok for r in probes.run_probe("summon-focus", Observation(events=rows, now=clock[0]))] == [False]


@pytest.mark.asyncio
async def test_agentd_writes_down_a_summon_the_bar_gave_back(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await sig_tests._start(d)
    await sig_tests._say(w, type="hello", client="bar", pid=1, build="b")
    await sig_tests._say(w, type="summon")
    assert json.loads(await r.readline()) == {"type": "summon", "id": 1}
    await sig_tests._say(w, type="focus_cancel", id=1)
    await sig_tests._wait(lambda: any(e["kind"] == "focus_cancel" for e in signals.read_events()))
    d.signals.tick(T0 * 2)
    assert [(e["kind"], e["id"]) for e in signals.read_events() if e["kind"] != "hello"] == [
        ("summon", 1), ("focus_cancel", 1)]
    w.close()
    server.cancel()


# -- the status line's Undo that did not work says so --

@pytest.mark.asyncio
async def test_an_undo_that_did_not_work_is_said_on_the_line_that_had_the_button(rig_of, monkeypatch):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    [made] = rig.turns("improve")
    before = len(rig.bar.inbox)

    def full_disk(phrase):
        raise OSError("disk full")
    monkeypatch.setattr(words, "remove", full_disk)
    res = await rig.do("undo", made["id"])
    assert res["ok"] is False and res["text"] == "That did not work."
    lines = [m for m in rig.bar.inbox[before:] if m["type"] == "event"]
    assert [(m["kind"], m["ok"], m["text"], m.get("undo_msg")) for m in lines] == [
        ("local", False, "That did not work.", None)]
    gone = await rig.do("undo", "i0-0")
    assert gone["ok"] is False
    assert [m["text"] for m in rig.bar.inbox if m["type"] == "event"][-1] == gone["text"]


@pytest.mark.asyncio
async def test_a_tap_that_is_not_an_undo_adds_no_line_when_it_fails(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    before = len(rig.bar.inbox)
    res = await rig.do("accept", "no-such-row")
    assert res["ok"] is False
    assert [m for m in rig.bar.inbox[before:] if m["type"] == "event"] == []


# -- Esc at a card is named as a card --

def test_a_burst_of_esc_the_bar_sent_with_a_card_is_named_a_card_in_the_finding(tmp_path):
    s = Signals(tmp_path / "loop", lambda: T0)
    s.friction({"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": False, "card": True})
    s.friction({"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": True, "card": False})
    rows = signals.read_events(tmp_path / "loop" / "signals.jsonl")
    got = probes.run_probe("esc-friction", Observation(events=rows, now=T0 + 1))
    assert [r.observed for r in got] == ["Esc was pressed again and again with a card up",
                                         "Esc was pressed again and again with the drawer up"]

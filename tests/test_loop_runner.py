"""The runner: collectors that never raise or stall, a run that looks twice at what is red, events that
schedule the checks, and the read-only doctor.

Real sockets and files stand in for the machine: a unix socket for agentd and for Hyprland's event
stream, stand-in programs on a PATH of their own for coredumpctl, systemctl and journalctl. What needs a
real Hyprland (the exact shapes `hyprctl -j` prints, real event timing) is only in the fixtures' shapes
here; docs/loop/report.md lists it.
"""

import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from bombadil import paths
from bombadil.loop import findings, probes, report, runner
from bombadil.loop.findings import FindingsStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "loop" / "probes"
VERSIONS = {"build": "d9dde3b", "machine": "laptop VM", "Hyprland": "0.56.2", "Quickshell": "0.3.1"}
NOW = 1_790_680_000.0      # the fixtures' "now"


@pytest.fixture(autouse=True)
def machine(home, monkeypatch):
    """No real machine: the loop's files are in a temp dir, PATH holds only stand-ins, and the runtime
    directory is short enough for a unix socket. Yields that directory."""
    monkeypatch.delenv("BOMBADIL_LOOP", raising=False)
    monkeypatch.delenv("BOMBADIL_SOCKET", raising=False)
    monkeypatch.setattr(runner, "_stuck", 0)
    runner._complained.clear()
    short = Path(tempfile.mkdtemp(prefix="rn"))
    (short / "bin").mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short))
    monkeypatch.setenv("BOMBADIL_RUNTIME", str(short / "bombadil"))
    monkeypatch.setenv("PATH", str(short / "bin"))
    yield short
    shutil.rmtree(short, ignore_errors=True)


@pytest.fixture
def bindir(machine):
    return machine / "bin"


@pytest.fixture
def store(machine):
    s = FindingsStore()
    yield s
    s.close()


def program(bindir, name, body, log=None):
    """A stand-in program: Python `body`, and a line in `log` every time it is run."""
    path = bindir / name
    note = f"open({str(log)!r}, 'a').write('x')\n" if log else ""
    path.write_text(f"#!{sys.executable}\nimport json, sys, time\n{note}{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Hypr:
    """A stand-in for `hypr.Hyprland`: answers `j/<what>` from a dict (JSON text, or a value to dump)."""
    available = True

    def __init__(self, **answers):
        self.answers, self.asked = answers, []

    def request(self, command):
        self.asked.append(command)
        answer = self.answers.get(command.removeprefix("j/"), "[]")
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer()
        return answer if isinstance(answer, str) else json.dumps(answer)


class Hang(Hypr):
    """A compositor that does not answer until it is let go."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def call(self):
        self.release.wait(30)
        return "[]"

    def request(self, command):
        self.asked.append(command)
        return self.call()

    def let_go(self):
        self.release.set()
        end = time.monotonic() + 3
        while runner._stuck and time.monotonic() < end:
            time.sleep(0.01)


@pytest.fixture
def hang():
    h = Hang()
    yield h
    h.let_go()


def fixture(name) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def collectors_of(fx: dict) -> dict:
    return {k: (lambda v=v: v) for k, v in fx.items() if k in runner.COLLECTED}


def runner_for(name, store, **more):
    """A Runner that looks at a fixture's Observation, with the retry's wait recorded, not slept."""
    fx = fixture(name)
    sleeps: list = []
    kw = {"clock": lambda: fx["now"], "sleep": sleeps.append, "presence": runner.Presence(locked=lambda: False),
          **more}
    r = runner.Runner(collectors_of(fx), store, VERSIONS, **kw)
    r.details_at = fx.get("details_at")
    return r, sleeps


def write_lines(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return path


def stamp(path: Path, t: float) -> Path:
    os.utime(path, (t, t))
    return path


# -- a call that may hang --

def test_bounded_gives_what_the_call_returned():
    assert runner.bounded(lambda: 42, 1) == (True, 42)
    assert runner.bounded(lambda: None, 1) == (True, None)


def test_bounded_says_not_done_for_a_call_that_raises():
    def boom():
        raise RuntimeError("no socket")
    assert runner.bounded(boom, 1) == (False, None)


def test_bounded_gives_up_on_a_call_that_hangs(hang):
    t0 = time.monotonic()
    assert runner.bounded(hang.call, 0.2) == (False, None)
    assert time.monotonic() - t0 < 2


def test_no_more_calls_are_started_while_too_many_are_stuck(hang):
    for _ in range(runner.STUCK_MAX):
        assert runner.bounded(hang.call, 0.05) == (False, None)
    started = []
    assert runner.bounded(lambda: started.append(1), 1) == (False, None) and started == []
    hang.let_go()
    assert runner.bounded(lambda: 7, 1) == (True, 7)       # they came back, so calls start again


# -- where Hyprland is --

def make_instance(machine, sig, mtime=None):
    d = machine / "hypr" / sig
    d.mkdir(parents=True)
    (d / ".socket.sock").write_text("")
    if mtime:
        os.utime(d, (mtime, mtime))
    return d


def test_the_instance_in_the_environment_is_used_when_its_socket_is_there(machine, monkeypatch):
    make_instance(machine, "aaa_1", 100)
    make_instance(machine, "bbb_2", 200)
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "aaa_1")
    assert runner.find_instance() == "aaa_1"


def test_a_stale_signature_gives_way_to_the_newest_live_instance(machine, monkeypatch):
    make_instance(machine, "aaa_1", 100)
    make_instance(machine, "bbb_2", 200)
    (machine / "hypr" / "ccc_3").mkdir()                 # a folder with no socket is not running
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "gone_0")
    assert runner.find_instance() == "bbb_2"
    assert runner.adopt_instance() == "bbb_2"
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "bbb_2"
    assert runner.socket2_path() == machine / "hypr" / "bbb_2" / ".socket2.sock"


def test_no_hyprland_is_none_and_the_environment_is_left_alone(monkeypatch):
    assert runner.find_instance() is None and runner.socket2_path() is None
    assert runner.adopt_instance() is None
    assert "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ


# -- what the bar and agentd wrote --

def test_events_are_the_rows_of_the_last_days_and_what_is_not_a_row_is_skipped(tmp_path):
    now = 1_000_000.0
    f = write_lines(tmp_path / "signals.jsonl", [
        {"t": now - 10, "kind": "summon"}, "garbage{", [1, 2], {"t": now - 10 * 86400, "kind": "hello"},
        {"kind": "no-time"}, {"t": "soon"}, {"t": float("inf")}, {"t": now - 5, "kind": "restart"}])
    assert runner.read_events(f, now=now) == [{"t": now - 10, "kind": "summon"}, {"t": now - 5, "kind": "restart"}]
    assert runner.read_events(tmp_path / "none.jsonl", now=now) is None


def test_events_come_from_the_loop_folder_by_default():
    write_lines(paths.loop_dir() / "signals.jsonl", [{"t": time.time(), "kind": "hello"}])
    assert [r["kind"] for r in runner.read_events()] == ["hello"]


def test_the_end_of_a_long_file_is_read_without_its_cut_first_line(tmp_path):
    f = write_lines(tmp_path / "log", [f"line {i:04d}" for i in range(1000)])
    lines = runner._tail(f, 100)
    assert lines and lines[-1] == b"line 0999" and all(ln.startswith(b"line ") and len(ln) == 9 for ln in lines)
    assert runner._tail(tmp_path / "missing") is None
    assert runner.tail_lines(f, 3) == ["line 0997", "line 0998", "line 0999"]
    assert runner.tail_lines(tmp_path / "missing") == []


def test_bar_and_agentd_files_are_objects_or_nothing(tmp_path):
    assert runner.read_bar(tmp_path / "missing") is None
    (tmp_path / "bar.json").write_text('{"pid": 7, "alive_at": 1.0}')
    assert runner.read_bar(tmp_path / "bar.json") == {"pid": 7, "alive_at": 1.0}
    for text in ("[1]", "{torn", "", "null", "7"):
        (tmp_path / "bar.json").write_text(text)
        assert runner.read_bar(tmp_path / "bar.json") is None
    (tmp_path / "agentd.json").write_text('{"started": 12.5}')
    assert runner.read_agentd(tmp_path / "agentd.json") == {"started": 12.5}


# -- agentd, for real --

class Sockets:
    """Unix sockets that act as agentd (or Hyprland's event socket) does, one connection per handler."""

    def __init__(self, folder):
        self.folder, self.closers = folder, []

    def listen(self, name, *handlers):
        path = self.folder / name
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        srv.listen(4)
        stop = threading.Event()

        def run():
            for handler in handlers:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                with conn:
                    try:
                        handler(conn)
                    except OSError:
                        pass
            stop.wait(10)

        threading.Thread(target=run, daemon=True).start()
        self.closers.append((srv, stop))
        return path

    def close(self):
        for srv, stop in self.closers:
            stop.set()
            srv.close()


@pytest.fixture
def sockets(machine):
    s = Sockets(machine)
    yield s
    s.close()


def pong(conn):
    conn.sendall(b'{"type": "status", "ok": true}\n{"type": "names", "names": []}\n')
    data = b""
    while b"\n" not in data:
        data += conn.recv(4096)
    assert json.loads(data.split(b"\n")[0]) == {"type": "ping"}
    conn.sendall(b'{"type": "pong", "t": 1.0, "pid": 7}\n')


def test_a_running_agentd_is_connected_and_answers_the_ping(sockets):
    got = runner.agentd_liveness(sockets.listen("a.sock", pong), timeout=2)
    assert got["connected"] and got["ponged"] and got["error"] == "" and 0 <= got["latency"] < 2


def test_what_agentd_says_on_connecting_is_read_past_and_so_is_noise(sockets):
    def noisy(conn):
        conn.sendall(b"not json\n[1]\n" + b'{"type": "pong"')       # the pong arrives in two pieces
        time.sleep(0.05)
        conn.sendall(b', "t": 2}\n')
    assert runner.agentd_liveness(sockets.listen("a.sock", noisy), timeout=2)["ponged"]


def test_agentd_that_does_not_answer_is_connected_but_not_ponged_within_the_timeout(sockets):
    t0 = time.monotonic()
    got = runner.agentd_liveness(sockets.listen("a.sock", lambda conn: time.sleep(5)), timeout=0.3)
    assert time.monotonic() - t0 < 2
    assert got["connected"] and not got["ponged"] and "no" in got["error"] and got["latency"] is None


def test_agentd_that_chatters_without_a_pong_is_not_waited_on_forever(sockets):
    def chatter(conn):
        for _ in range(40):
            conn.sendall(b'{"type": "status"}\n')
            time.sleep(0.05)
    t0 = time.monotonic()
    got = runner.agentd_liveness(sockets.listen("a.sock", chatter), timeout=0.4)
    assert time.monotonic() - t0 < 2 and not got["ponged"]


def test_agentd_that_hangs_up_says_so(sockets):
    got = runner.agentd_liveness(sockets.listen("a.sock", lambda conn: None), timeout=1)
    assert got["connected"] and not got["ponged"] and got["error"] == "agentd hung up before it answered"


def test_no_socket_and_a_stale_socket_file_are_not_connections(sockets, machine):
    got = runner.agentd_liveness(machine / "missing.sock", timeout=1)
    assert not got["connected"] and got["error"]
    path = sockets.listen("old.sock")
    sockets.close()                        # nothing listens now, and the file is left behind
    assert path.exists()
    got = runner.agentd_liveness(path, timeout=1)
    assert not got["connected"] and not got["ponged"] and got["error"]


def test_the_collector_asks_the_socket_agentd_is_configured_with(sockets, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SOCKET", str(sockets.listen("agentd.sock", pong)))
    assert runner.Collectors(hyprland=Hypr(), agentd_timeout=2).agentd()["ponged"]
    monkeypatch.setenv("BOMBADIL_SOCKET", "/nowhere/agentd.sock")
    assert runner.Collectors(hyprland=Hypr()).agentd()["ponged"] is False


# -- the hyprctl collectors --

def test_each_hyprctl_collector_asks_for_its_own_json():
    h = Hypr(clients=[{"class": "foot"}], monitors=[{"name": "DP-1"}], layers={"DP-1": {}},
             activewindow={"class": "foot"}, configerrors=["one"])
    c = runner.Collectors(hyprland=h)
    assert (c.clients(), c.monitors(), c.layers(), c.activewindow(), c.configerrors()) == (
        [{"class": "foot"}], [{"name": "DP-1"}], {"DP-1": {}}, {"class": "foot"}, ["one"])
    assert h.asked == ["j/clients", "j/monitors", "j/layers", "j/activewindow", "j/configerrors"]


def test_prose_or_the_wrong_shape_is_no_data_not_a_crash():
    c = runner.Collectors(hyprland=Hypr(clients="Hyprland is busy, try again", monitors={"a": 1}, layers="[]",
                                        activewindow="", configerrors="null"))
    assert (c.clients(), c.monitors(), c.layers(), c.activewindow(), c.configerrors()) == (None,) * 5


def test_a_compositor_that_is_not_there_is_no_data_and_asks_nothing_more():
    h = Hypr(clients=RuntimeError("Hyprland is not running"))
    c = runner.Collectors(hyprland=h)
    assert c.clients() is None and c.monitors() is None and h.asked == ["j/clients"]
    c.begin()
    assert c.monitors() == []                        # the next look asks again
    h.available = False
    assert c.monitors() is None and h.asked == ["j/clients", "j/monitors"]


def test_a_hyprctl_that_hangs_costs_one_timeout_not_one_per_collector(hang):
    c = runner.Collectors(hyprland=hang, hypr_timeout=0.25)
    t0 = time.monotonic()
    got = (c.clients(), c.monitors(), c.layers(), c.activewindow(), c.configerrors())
    assert got == (None,) * 5 and time.monotonic() - t0 < 1.5 and len(hang.asked) == 1
    c.begin()
    assert c.clients() is None and len(hang.asked) == 2          # a new look tries again


# -- crashes and units --

def coredump_rows(now):
    at = lambda ago: int((now - ago) * 1e6)
    return [{"time": at(3600), "pid": 100, "sig": 11, "exe": "/usr/bin/quickshell"},
            {"time": at(3500), "pid": 101, "sig": 11, "exe": "/usr/bin/firefox"},
            {"time": at(3400), "pid": 102, "sig": 6, "exe": "/usr/bin/python3.11"},
            {"time": at(3300), "pid": 103, "sig": 6, "exe": "/usr/bin/python3.11"}]


def fake_coredumpctl(bindir, rows, log=None):
    info = {"102": "           PID: 102 (python3)\n  Command Line: /usr/bin/python3 /usr/local/bin/agentd",
            "103": "           PID: 103 (python3)\n  Command Line: /usr/bin/python3 /home/someone/script.py"}
    return program(bindir, "coredumpctl", f"""
if sys.argv[1] == 'list':
    print(json.dumps({rows!r}))
else:
    print({info!r}.get(sys.argv[-1], ''))
""", log)


def test_coredumps_are_bombadils_programs_and_python_is_told_apart_by_its_command_line(bindir):
    fake_coredumpctl(bindir, coredump_rows(NOW))
    got = runner.Collectors(hyprland=Hypr(), clock=lambda: NOW).coredumps()
    assert [r["pid"] for r in got] == [100, 102]              # not firefox, not someone else's python
    assert got[1]["cmdline"].endswith("/usr/local/bin/agentd")


def test_coredumps_none_is_an_answer_and_no_answer_is_none(bindir):
    c = runner.Collectors(hyprland=Hypr(), clock=lambda: NOW)
    assert c.coredumps() is None                               # no coredumpctl on this machine
    program(bindir, "coredumpctl", "print('No coredumps found.', file=sys.stderr); sys.exit(1)")
    c.refresh()
    assert c.coredumps() == []
    program(bindir, "coredumpctl", "print('Failed to open the journal'); sys.exit(1)")
    c.refresh()
    assert c.coredumps() is None
    for said in ("not json", "{}", '"x"'):
        program(bindir, "coredumpctl", f"print({said!r})")
        c.refresh()
        assert c.coredumps() is None
    program(bindir, "coredumpctl", "print('[null, 7, {\"exe\": \"/usr/bin/agentd\", \"time\": \"soon\"}]')")
    c.refresh()
    assert len(c.coredumps()) == 1


def test_coredumpctl_is_not_asked_more_than_every_few_minutes(bindir, tmp_path):
    log = tmp_path / "runs"
    fake_coredumpctl(bindir, coredump_rows(NOW), log)
    c = runner.Collectors(hyprland=Hypr(), clock=lambda: NOW)
    c.coredumps()
    runs = len(log.read_text())
    c.coredumps()
    assert len(log.read_text()) == runs
    c.refresh()
    c.coredumps()
    assert len(log.read_text()) > runs


def test_a_coredumpctl_that_hangs_is_given_up_on(bindir, monkeypatch):
    program(bindir, "coredumpctl", "time.sleep(60)")
    monkeypatch.setattr(runner, "COREDUMP_TIMEOUT", 0.4)
    t0 = time.monotonic()
    assert runner.Collectors(hyprland=Hypr()).coredumps() is None
    assert time.monotonic() - t0 < 5


def test_failed_units_are_bombadils_only(bindir):
    c = runner.Collectors(hyprland=Hypr())
    assert c.failed_units() is None                            # no systemctl
    program(bindir, "systemctl", "print('bombadil-shell.service loaded failed failed The bar\\n"
            "flatpak-x.service loaded failed failed Other\\nbombadil-agentd.service loaded failed failed Agent')")
    assert c.failed_units() == ["bombadil-shell.service", "bombadil-agentd.service"]
    program(bindir, "systemctl", "print('Failed to connect to bus'); sys.exit(1)")
    assert c.failed_units() is None                            # no user manager
    program(bindir, "systemctl", "pass")
    assert c.failed_units() == []


# -- the turns --

def seed_turns(now=NOW):
    """Two model turns, the second with a log: an os tool that failed, a shell command that failed,
    an error event; and a local row that is not a turn."""
    turns = paths.state_dir() / "turns"
    turns.mkdir(parents=True)
    rows = [
        {"t": now - 600, "prompt": "show me how full my disk is", "result": "212 GB free", "ok": True,
         "id": "100-1", "started": now - 610, "details": str(turns / "100-1.jsonl"), "v": 2},
        {"t": now - 300, "prompt": "put the notes app on the left", "result": "done", "ok": False,
         "id": "200-2", "started": now - 330, "details": str(turns / "200-2.jsonl"), "v": 2},
        {"t": now - 290, "kind": "local", "prompt": "hide noticed", "v": 2},
    ]
    write_lines(paths.turns_log(), rows)
    write_lines(turns / "200-2.jsonl", [
        {"t": now - 320, "type": "event", "kind": "tool", "id": "u1", "name": "mcp__bombadil-os__desk_add"},
        {"t": now - 319, "type": "event", "kind": "tool_result", "id": "u1", "output": "no such app", "error": True},
        {"t": now - 318, "type": "event", "kind": "tool", "id": "u2", "name": "Bash"},
        {"t": now - 317, "type": "event", "kind": "tool_result", "id": "u2", "output": "exit 1", "error": True},
        {"t": now - 316, "type": "event", "kind": "tool", "id": "u3", "name": "mcp__bombadil-os__screenshot"},
        {"t": now - 315, "type": "event", "kind": "tool_result", "id": "u3", "output": "ok"},
        {"t": now - 310, "type": "event", "kind": "error", "text": "provider said overloaded"},
        "not json at all", {"t": now - 309, "type": "event", "kind": "text", "text": "hello"}])


def test_the_turns_are_the_ledger_their_os_tool_results_and_their_errors():
    seed_turns()
    c = runner.Collectors(hyprland=Hypr(), clock=lambda: NOW)
    ledger = c.ledger()
    assert [r["id"] for r in ledger if r.get("kind") is None] == ["100-1", "200-2"]
    assert any(r.get("kind") == "local" for r in ledger)
    assert c.tool_results() == [
        {"turn": "200-2", "tool": "mcp__bombadil-os__desk_add", "ok": False, "t": NOW - 319, "text": "no such app"},
        {"turn": "200-2", "tool": "mcp__bombadil-os__screenshot", "ok": True, "t": NOW - 315, "text": "ok"}]
    assert c.turn_errors() == [{"turn": "200-2", "t": NOW - 310, "text": "provider said overloaded"}]


def test_no_turns_is_no_data_and_a_torn_ledger_is_read_as_far_as_it_goes():
    c = runner.Collectors(hyprland=Hypr(), clock=lambda: NOW)
    assert c.ledger() is None and c.tool_results() is None and c.turn_errors() is None
    write_lines(paths.turns_log(), [{"t": NOW - 5, "prompt": "hi", "id": "1-1", "v": 2}, "{torn"])
    c.refresh()
    assert [r["id"] for r in c.ledger()] == ["1-1"] and c.tool_results() == []


def test_a_turns_log_is_found_by_its_id_and_a_row_cannot_point_at_another_file(machine):
    turns = paths.state_dir() / "turns"
    turns.mkdir(parents=True)
    secret = machine / "secret.jsonl"
    secret.write_text('{"t": 1, "kind": "tool", "id": "a", "name": "mcp__bombadil-os__x"}\n')
    assert runner.Collectors._log_of({"id": "../../secret", "details": str(secret)}).name == "none"
    assert runner.Collectors._log_of({"id": "x", "details": str(secret)}).name == "none"
    (turns / "9-9.jsonl").write_text("")
    assert runner.Collectors._log_of({"id": "9-9", "details": ""}) == turns / "9-9.jsonl"
    assert runner.Collectors._log_of({"id": "1", "details": str(turns / "elsewhere.jsonl")}).name == "elsewhere.jsonl"


def test_the_turns_are_read_once_for_the_three_fields(monkeypatch):
    seed_turns()
    calls = []
    real = runner.ledger_mod.read_rows
    monkeypatch.setattr(runner.ledger_mod, "read_rows", lambda *a, **k: calls.append(1) or real(*a, **k))
    c = runner.Collectors(hyprland=Hypr(), clock=lambda: NOW)
    c.ledger(), c.tool_results(), c.turn_errors()
    assert len(calls) == 1


def test_groups_and_usual_lengths_come_from_the_counts_and_no_counts_is_no_data():
    c = runner.Collectors(hyprland=Hypr())
    assert c.groups() is None and c.medians() is None              # no loop.db yet
    paths.loop_dir().mkdir(parents=True)
    db = sqlite3.connect(paths.loop_db())
    db.execute("CREATE TABLE other(x)")
    db.commit()
    c.refresh()
    assert c.groups() is None                                      # a db with another shape
    db.execute("CREATE TABLE requests(id TEXT, t REAL, ok INTEGER, stopped INTEGER, seconds REAL, grp TEXT)")
    for i, seconds in enumerate((10, 20, 30, 40, 50)):
        db.execute("INSERT INTO requests VALUES(?, ?, 1, 0, ?, 'disk')", (f"r{i}", i, seconds))
    db.execute("INSERT INTO requests VALUES('few', 9, 1, 0, 5, 'rare')")
    db.execute("INSERT INTO requests VALUES('stopped', 9, 1, 1, 500, 'disk')")
    db.commit()
    db.close()
    c.refresh()
    assert c.groups()["r0"] == "disk" and c.medians() == {"disk": 30}      # 'rare' has too few for a usual length


# -- apps --

def test_apps_are_the_status_files_of_the_last_days_with_the_end_of_their_log():
    root = paths.state_dir() / "apps"
    root.mkdir(parents=True)
    write_lines(root / "notes.log", ["line 1", "QML error: bad"])
    (root / "notes.status.json").write_text(json.dumps({"ok": False, "pid": 2 ** 30}))
    (root / "clock.status.json").write_text(json.dumps({"ok": True}))
    (root / "torn.status.json").write_text("{torn")
    (root / "no-ok.status.json").write_text("{}")
    old = root / "old.status.json"
    old.write_text(json.dumps({"ok": True}))
    stamp(old, NOW - 10 * 86400)
    for f in root.glob("*.status.json"):
        if f != old:
            stamp(f, NOW - 60)
    got = {a["name"]: a for a in runner.Collectors(hyprland=Hypr(), clock=lambda: NOW).apps()}
    assert set(got) == {"notes", "clock", "no-ok"}
    assert got["notes"] == {"name": "notes", "ok": False, "running": False, "log": ["line 1", "QML error: bad"]}
    assert got["clock"]["ok"] is True and got["clock"]["log"] == [] and got["no-ok"]["ok"] is None


def test_no_apps_folder_is_no_apps():
    assert runner.Collectors(hyprland=Hypr()).apps() == []


def test_a_pid_counts_only_while_it_is_a_bombadil_app():
    assert runner._alive(None) is False and runner._alive(True) is False and runner._alive(-3) is False
    assert runner._alive(2 ** 30) is False
    if not Path("/proc/self/cmdline").exists():
        pytest.skip("no /proc to read a command line from")
    assert runner._alive(os.getpid()) is False                      # a live pid that is not an app
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "bombadil-app"])
    try:
        end = time.monotonic() + 5
        while b"bombadil-app" not in Path(f"/proc/{child.pid}/cmdline").read_bytes() and time.monotonic() < end:
            time.sleep(0.01)                   # for a moment after exec the kernel shows a partial command line
        assert runner._alive(child.pid) is True
    finally:
        child.kill()
        child.wait()


def test_a_coredumps_time_is_seconds_however_coredumpctl_wrote_it():
    assert runner._epoch(1_790_680_000_000_000) == 1_790_680_000
    assert runner._epoch(1_790_680_000_000) == 1_790_680_000
    assert runner._epoch(1_790_680_000) == 1_790_680_000
    assert runner._epoch("soon") is None and runner._epoch(0) is None and runner._epoch(float("nan")) is None


# -- is he away --

def test_he_is_away_after_ten_minutes_without_a_prompt(machine):
    p = runner.Presence(locked=lambda: False)
    assert p.away(NOW) is False                                     # nothing to go on: present
    stamp(write_lines(paths.turns_log(), [{"t": NOW - 1000, "prompt": "x", "id": "1"}]), NOW - 601)
    assert p.away(NOW) is True and p.away(NOW - 2) is False       # 599 s since the last prompt
    stamp(paths.turns_log(), NOW - 10)
    assert p.away(NOW) is False


def test_the_last_prompt_is_the_newest_of_the_ledger_the_turn_logs_a_summon_and_agentd_starting():
    p = runner.Presence(locked=lambda: False)
    turns = paths.state_dir() / "turns"
    turns.mkdir(parents=True)
    stamp(write_lines(turns / "1790679000000-1.jsonl", [{"kind": "text"}]), NOW - 4000)
    stamp(write_lines(turns / "1790679500000-2.jsonl", [{"kind": "text"}]), NOW - 2000)
    assert p.last_active(NOW) == NOW - 2000
    write_lines(paths.loop_dir() / "signals.jsonl", [{"t": NOW - 900, "kind": "summon"},
                                                     {"t": NOW - 100, "kind": "hello"}])
    assert p.last_active(NOW) == NOW - 900
    paths.loop_dir().joinpath("agentd.json").write_text(json.dumps({"started": NOW - 50}))
    assert p.last_active(NOW) == NOW - 50
    assert p.last_active(NOW - 60) == NOW - 900           # what has not happened yet is not counted


def test_a_locked_screen_is_away_at_once_and_a_lock_that_cannot_be_asked_is_not_away():
    stamp(write_lines(paths.turns_log(), [{"t": NOW - 2, "prompt": "x", "id": "1"}]), NOW - 2)
    assert runner.Presence(locked=lambda: True).away(NOW) is True

    def broken():
        raise OSError("no pgrep")
    assert runner.Presence(locked=broken).away(NOW) is False


def test_hyprlock_is_found_with_pgrep(bindir):
    p = runner.Presence()
    assert p.locked() is False                                     # no pgrep at all
    program(bindir, "pgrep", "sys.exit(0 if sys.argv[-1] == 'hyprlock' else 1)")
    assert p.locked() is True
    program(bindir, "pgrep", "sys.exit(1)")
    assert p.locked() is False


# -- Hyprland's events --

def poll_until(stream, count, seconds=3.0):
    got, end = [], time.monotonic() + seconds
    while len(got) < count and time.monotonic() < end:
        got += stream.poll(0.2)
    return got


def test_events_arrive_as_a_name_and_its_data(sockets):
    def events(conn):
        conn.sendall(b"openwindow>>55a1c0,1,bombadil-details,Details\nactivespecial>>special:details,Virtual-1\n"
                     b"a line with no separator\n>>nameless\nfocusedmon>>Virtual-1,1\n")
    stream = runner.EventStream(sockets.listen("ev.sock", events))
    assert poll_until(stream, 3) == [("openwindow", "55a1c0,1,bombadil-details,Details"),
                                     ("activespecial", "special:details,Virtual-1"), ("focusedmon", "Virtual-1,1")]
    assert stream.connected
    stream.close()
    assert not stream.connected


def test_an_event_split_over_two_reads_is_one_event(sockets):
    def slow(conn):
        conn.sendall(b"closewin")
        time.sleep(0.15)
        conn.sendall(b"dow>>55a1c0\nopenwin")
        time.sleep(0.15)
        conn.sendall(b"dow>>7,1,foot,a title, with a comma\n")
    stream = runner.EventStream(sockets.listen("ev.sock", slow))
    assert poll_until(stream, 2) == [("closewindow", "55a1c0"), ("openwindow", "7,1,foot,a title, with a comma")]


def test_a_compositor_that_goes_and_comes_back_is_reconnected(sockets):
    path = sockets.listen("ev.sock", lambda conn: conn.sendall(b"one>>1\n"), lambda conn: conn.sendall(b"two>>2\n"))
    stream = runner.EventStream(path, backoff=(0.05, 0.1))
    assert poll_until(stream, 2) == [("one", "1"), ("two", "2")]


def test_no_compositor_is_asked_again_after_a_growing_pause_and_nothing_raises(machine):
    now, sleeps = [0.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    stream = runner.EventStream(machine / "no.sock", backoff=(1, 2, 4), clock=lambda: now[0], sleep=sleep)
    for _ in range(8):
        assert stream.poll(5) == []
    assert sleeps == [1, 2, 4, 4] and not stream.connected


def test_a_stream_with_no_path_looks_for_hyprland_and_finds_none():
    stream = runner.EventStream()
    assert stream.poll(0.01) == [] and not stream.connected


def test_a_huge_unfinished_line_is_dropped_not_kept_forever(sockets):
    def flood(conn):
        for _ in range(24):
            conn.sendall(b"x" * 65536)
    stream = runner.EventStream(sockets.listen("ev.sock", flood))
    for _ in range(30):
        assert stream.poll(0.05) == []
    assert len(stream._buf) <= (1 << 20) + 65536


# -- a run --

@pytest.mark.parametrize("name, probe_ids", [
    ("drawer", {"drawer-focus"}), ("apps-stacked", {"apps-stacked"}), ("line-over-app", {"window-under-bar"}),
    ("monitor-640", {"window-oversize", "monitor-narrow"}), ("super-tap", {"summon-focus"})])
def test_each_bad_fixture_is_found_and_each_fixed_one_is_not(store, name, probe_ids):
    bad, sleeps = runner_for(f"{name}-bad", store)
    assert {f.probe for f in bad.run_once()} == probe_ids
    assert sleeps == ([0.5] if name != "super-tap" else [])
    fixed, sleeps = runner_for(f"{name}-fixed", store)
    assert fixed.run_once() == [] and sleeps == []


def test_a_red_invariant_is_looked_at_again_half_a_second_later_and_written_down(store):
    r, sleeps = runner_for("apps-stacked-bad", store)
    [found] = r.run_once()
    assert sleeps == [0.5]
    assert (found.component, found.rule, found.kind, found.n, found.days) == ("hypr", "apps-stacked", "invariant", 1, 1)
    bundle = json.loads((Path(found.evidence) / "evidence.json").read_text())
    assert bundle["versions"]["build"] == "d9dde3b" and len(bundle["windows"]) == 3
    assert "app x2" in report.build(found).picture
    assert store.open_findings() == [found]


def test_a_red_that_is_green_on_the_second_look_is_not_a_finding(store):
    bad, fixed = fixture("apps-stacked-bad")["clients"], fixture("apps-stacked-fixed")["clients"]
    looks = []

    def clients():
        looks.append(1)
        return bad if len(looks) % 2 else fixed
    r = runner.Runner({"clients": clients}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    assert r.run_once() == [] and len(looks) == 2 and store.open_findings() == []


def test_a_probe_that_keeps_changing_its_mind_is_set_aside_and_written_up(store):
    bad, fixed = fixture("apps-stacked-bad")["clients"], fixture("apps-stacked-fixed")["clients"]
    looks = []

    def clients():
        looks.append(1)
        return bad if len(looks) % 2 else fixed
    sleeps = []
    r = runner.Runner({"clients": clients}, store, VERSIONS, clock=lambda: NOW, sleep=sleeps.append)
    assert r.run_once(now=NOW) == [] and r.run_once(now=NOW + 60) == []
    [flaky] = r.run_once(now=NOW + 120)
    assert (flaky.component, flaky.rule) == ("loop", "flaky-probe") and "apps-stacked" in flaky.observed
    assert store.quarantined(NOW + 120) == {"apps-stacked"}
    looks.clear()
    looked = len(sleeps)
    assert r.run_once(now=NOW + 180) == [] and len(sleeps) == looked     # set aside: no look, no second look
    assert store.open_findings() == [flaky]


def test_the_same_state_is_one_sighting_until_the_episode_is_over(store):
    r, _ = runner_for("apps-stacked-bad", store)
    [first] = r.run_once(now=NOW)
    assert r.run_once(now=NOW + 60) == []
    [later] = r.run_once(now=NOW + findings.SAME_EPISODE + 61)
    assert (first.n, later.n, later.fp) == (1, 2, first.fp)


def test_only_the_probes_asked_for_run_and_only_what_they_need_is_collected():
    h = Hypr(clients=fixture("apps-stacked-bad")["clients"])
    s = FindingsStore()
    try:
        r = runner.Runner(runner.Collectors(hyprland=h, clock=lambda: NOW), s, VERSIONS, clock=lambda: NOW,
                          sleep=lambda x: None)
        assert {f.probe for f in r.run_once({"apps-stacked"})} == {"apps-stacked"}
        assert h.asked == ["j/clients", "j/clients"]                # the look, and the look again
        assert r.run_once({"no-such-probe"}) == []
    finally:
        s.close()


def test_the_real_collectors_with_a_compositor_that_serves_a_fixture(store):
    fx = fixture("line-over-app-bad")
    write_lines(paths.loop_dir() / "bar.json", [fx["bar"]])
    h = Hypr(clients=fx["clients"], monitors=fx["monitors"], layers=fx["layers"])
    r = runner.Runner(runner.Collectors(hyprland=h, clock=lambda: NOW), store, VERSIONS, clock=lambda: NOW,
                      sleep=lambda s: None)
    assert "window-under-bar" in {f.probe for f in r.run_once()}


def test_what_a_probe_asks_to_see_again_is_not_run_one_more_time_than_needed(store):
    r, sleeps = runner_for("monitor-640-bad", store)
    r.run_once()
    assert sleeps == [0.5]                     # two red invariants, one wait


def test_check_says_what_the_probes_say_now_and_keeps_nothing(store):
    r, sleeps = runner_for("apps-stacked-bad", store)
    got = r.check({"apps-stacked"})
    assert [(x.id, x.ok) for x in got] == [("apps-stacked", False)] and got[0].retry_after is None
    assert sleeps == [0.5] and store.open_findings() == [] and store.pending() == []
    assert r.check({"no-such-probe"}) == []
    assert {x.id for x in r.check()} >= {"apps-stacked", "drawer-focus", "agentd-ping"}


def test_a_collector_that_raises_costs_its_own_probes_only(store, capsys):
    fx = fixture("apps-stacked-bad")

    def boom():
        raise RuntimeError("the compositor fell over")
    c = {**collectors_of(fx), "monitors": boom, "layers": boom, "activewindow": boom}
    r = runner.Runner(c, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    assert {f.probe for f in r.run_once()} == {"apps-stacked"}     # it needs clients only
    r.run_once(now=NOW + 5000)
    assert capsys.readouterr().err.count("monitors: RuntimeError") == 1     # said once, not every minute


def test_a_compositor_that_hangs_does_not_stall_the_run(hang, store):
    c = runner.Collectors(hyprland=hang, hypr_timeout=0.25, clock=lambda: NOW)
    c.agentd = lambda: {"connected": True, "ponged": True, "latency": 0.001}      # this test is about hyprctl
    r = runner.Runner(c, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    t0 = time.monotonic()
    assert r.run_once() == []
    assert time.monotonic() - t0 < 4


def test_a_compositor_that_prints_garbage_is_no_finding_and_no_crash(store):
    h = Hypr(clients="\x00\x01 not json", monitors="<html>", layers="Hyprland 0.56.2", activewindow="{",
             configerrors="ok")
    c = runner.Collectors(hyprland=h, clock=lambda: NOW)
    c.agentd = lambda: {"connected": True, "ponged": True, "latency": 0.001}
    r = runner.Runner(c, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    assert r.run_once() == [] and r.check() is not None


def test_a_store_that_will_not_open_costs_a_line_and_the_run_is_empty(monkeypatch, capsys):
    def nope(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(runner, "FindingsStore", nope)
    r = runner.Runner({}, None, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    assert r.run_once() == [] and "loop.db will not open" in capsys.readouterr().err


def test_a_runner_opens_its_own_store_and_closes_it(machine):
    r = runner.Runner(collectors_of(fixture("apps-stacked-bad")), None, VERSIONS, clock=lambda: NOW,
                      sleep=lambda s: None)
    assert [f.probe for f in r.run_once()] == ["apps-stacked"]
    r.close()
    assert r._store_arg is None and paths.loop_db().exists()


def test_the_compositors_log_and_the_turn_go_into_the_evidence(machine, store, monkeypatch):
    sig = "abc_123_456"
    make_instance(machine, sig)
    write_lines(machine / "hypr" / sig / "hyprland.log", [f"line {i}" for i in range(100)])
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", sig)
    r, _ = runner_for("apps-stacked-bad", store)
    [found] = r.run_once()
    bundle = json.loads((Path(found.evidence) / "evidence.json").read_text())
    assert bundle["log"][-1] == "line 99" and len(bundle["log"]) <= runner.LOG_LINES


def test_the_log_of_a_unit_comes_from_the_journal(bindir):
    program(bindir, "journalctl", "print('a\\nb\\nc')")
    r = runner.Runner({}, None, None)
    obs = probes.Observation()
    assert r._log_for(probes.Result(False, "bar-alive", "bar", "bar-alive", "x", "y"), obs) == ["a", "b", "c"]
    program(bindir, "journalctl", "sys.exit(1)")
    assert r._log_for(probes.Result(False, "bar-alive", "bar", "bar-alive", "x", "y"), obs) == []
    assert r._log_for(probes.Result(False, "x", "unknown", "x", "x", "y"), obs) == []


def test_his_words_of_trouble_near_a_finding_are_kept_and_nothing_else_of_them(store):
    fx = fixture("apps-stacked-bad")
    fx["ledger"] = [{"t": NOW - 120, "prompt": "it is still stuck, show me the pictures of my family", "id": "1",
                     "started": NOW - 130, "seconds": 10.0, "origin": "typed", "details": ""},
                    {"t": NOW - 90000, "prompt": "won't work", "id": "0", "started": NOW - 90001, "seconds": 1.0,
                     "origin": "typed", "details": ""}]
    r = runner.Runner(collectors_of(fx), store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    [found] = r.run_once()
    text = (Path(found.evidence) / "evidence.json").read_text()
    assert json.loads(text)["words"] == ["still", "stuck"]
    assert "family" not in text and "pictures" not in text and "won't" not in text


# -- events --

DETAILS = "0x55a1c0,1,bombadil-details,Details"


def drawer_runner(store):
    fx = fixture("drawer-bad")
    sleeps: list = []
    c = collectors_of({k: v for k, v in fx.items() if k != "details_at"})
    r = runner.Runner(c, store, VERSIONS, clock=lambda: 0.0, sleep=sleeps.append,
                      presence=runner.Presence(locked=lambda: False))
    return r, sleeps


def test_the_drawer_is_judged_two_seconds_after_it_opens_and_not_before(store):
    r, sleeps = drawer_runner(store)
    assert r.run_event("openwindow", DETAILS, now=NOW - 6) == [] and r.details_at == NOW - 6
    assert r.tick(NOW - 6 + 1.5) == []                             # the layout checks: its grace is not over
    assert r.tick(NOW - 6 + 2.0) == []                             # not yet: a little after, for the grace to be over
    [found] = r.tick(NOW - 6 + 2.2)
    assert found.probe == "drawer-focus" and sleeps == [0.5]
    assert r.tick(NOW - 6 + 9) == []                               # and it was due once


def test_the_drawer_opening_as_a_special_workspace_counts_too_and_other_specials_do_not(store):
    r, _ = drawer_runner(store)
    r.run_event("activespecial", "special:browser,Virtual-1", now=NOW)
    r.run_event("activespecial", ",Virtual-1", now=NOW)
    assert r.details_at is None
    r.run_event("activespecial", "special:details,Virtual-1", now=NOW + 1)
    assert r.details_at == NOW + 1
    r.run_event("openwindow", "0x1,1,foot,a shell", now=NOW + 2)
    r.run_event("openwindow", "0x2,1", now=NOW + 3)               # a short line does not break it
    assert r.details_at == NOW + 1


def test_a_window_that_opens_or_closes_is_checked_once_it_is_placed(store):
    r, _ = runner_for("apps-stacked-bad", store)
    assert r.run_event("openwindow", "0x1,1,bombadil-app,notes", now=NOW) == []
    assert r.next_due() == NOW + runner.SETTLE
    assert r.tick(NOW + 0.5) == []
    assert {f.probe for f in r.tick(NOW + runner.SETTLE + 0.01)} == {"apps-stacked"}
    assert r.next_due() is None


def test_a_burst_of_window_events_is_one_check(store):
    r, _ = runner_for("apps-stacked-bad", store)
    for i in range(6):
        r.run_event("openwindow", f"0x{i},1,bombadil-app,notes", now=NOW + i * 0.05)
        r.run_event("closewindow", f"0x{i}", now=NOW + i * 0.05)
    assert len(r._due) == 1


def test_the_events_that_do_nothing_schedule_nothing(store):
    r, _ = runner_for("apps-stacked-bad", store)
    for name in ("focusedmon", "workspace", "windowtitle", "activewindow", "urgent", ""):
        assert r.run_event(name, "whatever", now=NOW) == []
    assert r.next_due() is None


def test_the_schedule_does_not_grow_without_bound(store):
    r, _ = runner_for("apps-stacked-bad", store)
    for i in range(500):
        r.run_event("openwindow", DETAILS, now=NOW + i * 10)
    assert len(r._due) <= 50


# -- time --

class Counting:
    """A look at the machine that counts itself, and presence that counts its questions."""

    def __init__(self, away=True):
        self.looks, self.asked, self.is_away = 0, 0, away

    def ledger(self):
        self.looks += 1

    def away(self, now=None):
        self.asked += 1
        return self.is_away


def test_while_he_is_away_the_probes_run_once_a_minute(store, monkeypatch):
    c, p = Counting(), Counting()
    monkeypatch.setattr(runner, "doctor_live", lambda collectors=None: [])
    r = runner.Runner({"ledger": c.ledger}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None, presence=p)
    r.tick(NOW)
    assert c.looks == 1                                          # the first look while away is at once
    for second in range(1, 60):
        r.tick(NOW + second)
    assert c.looks == 1
    r.tick(NOW + 61)
    r.tick(NOW + 62)
    assert c.looks == 2
    r.tick(NOW + 122)
    assert c.looks == 3


def test_while_he_is_there_nothing_runs_and_he_is_not_asked_every_second(store):
    c, p = Counting(), Counting(away=False)
    r = runner.Runner({"ledger": c.ledger}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None, presence=p)
    for second in range(30):
        r.tick(NOW + second)
    assert c.looks == 0 and 1 <= p.asked <= 3


def test_the_doctor_runs_when_he_is_away_then_once_a_day(store, monkeypatch):
    c, p, ran = Counting(), Counting(), []
    monkeypatch.setattr(runner, "doctor_live", lambda collectors=None: ran.append(1) or [])
    r = runner.Runner({"ledger": c.ledger}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None, presence=p)
    for minute in range(0, 24 * 60, 1):
        r.tick(NOW + minute * 60)
    assert len(ran) == 1
    r.tick(NOW + 86400 + 5)
    assert len(ran) == 2
    p.is_away = False
    r.tick(NOW + 3 * 86400)
    assert len(ran) == 2                                          # and never while he is there


def test_a_restart_does_not_run_the_doctor_again_at_once(store, monkeypatch):
    ran = []
    monkeypatch.setattr(runner, "doctor_live", lambda collectors=None: ran.append(1) or [])
    a = runner.Runner({}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None, presence=Counting())
    a.doctor(NOW)
    b = runner.Runner({}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None, presence=Counting())
    b.tick(NOW + 600)
    assert len(ran) == 1 and b._last_doctor == NOW


def test_serve_handles_events_ticks_and_survives_a_bad_one(store):
    r, _ = runner_for("apps-stacked-bad", store)

    class Stream:
        def __init__(self):
            self.script = [[("openwindow", DETAILS)], RuntimeError("the socket fell over"), [], []]
            self.closed = False

        def poll(self, timeout):
            if not self.script:
                stop.end = True
                return []
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        def close(self):
            self.closed = True

    class Stop:
        end = False

        def is_set(self):
            return self.end

        def wait(self, seconds):
            return None
    stop, stream = Stop(), Stream()
    r.serve(stop, stream)
    assert stream.closed and r.details_at is not None


def test_main_installs_its_handlers_and_serves_until_told_to_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.signal, "signal", lambda sig, handler: calls.append(sig))
    monkeypatch.setattr(runner.Runner, "serve", lambda self, stop=None, stream=None: calls.append("served"))
    assert runner.main() == 0
    assert "served" in calls and len(calls) == 3


# -- the doctor --

def good_collectors():
    fx = fixture("drawer-fixed")
    return {"agentd": lambda: {"connected": True, "ponged": True, "latency": 0.004}, "layers": lambda: fx["layers"],
            "configerrors": list, "failed_units": list}


def doctor(**more):
    c = {**good_collectors(), **more.pop("collectors", {})}
    return {x["name"]: x for x in runner.doctor_live(c, os_mcp=more.pop("os_mcp", lambda: (True, "13 tools")),
                                                      canary=more.pop("canary", lambda: (True, "1 loaded", False)))}


def test_a_healthy_machine_passes_every_check_in_order():
    got = runner.doctor_live(good_collectors(), os_mcp=lambda: (True, "13 tools"),
                             canary=lambda: (True, "1 loaded", False))
    assert [x["name"] for x in got] == ["agentd-ping", "bar-layer", "hypr-config", "units", "os-mcp", "canary-apps"]
    assert all(x["ok"] for x in got) and not any(x.get("skipped") for x in got)
    assert got[0]["detail"] == "answered in 4 ms"


def test_each_check_says_what_is_wrong():
    got = doctor(collectors={"agentd": lambda: {"connected": True, "ponged": False, "error": "no pong within 2 s"},
                             "layers": dict, "configerrors": lambda: ["line 3: bad key"],
                             "failed_units": lambda: ["bombadil-shell.service"]},
                 os_mcp=lambda: (False, "os-mcp lists no tools"), canary=lambda: (False, "notes: boom", False))
    assert [(n, x["ok"]) for n, x in got.items()] == [("agentd-ping", False), ("bar-layer", False),
                                                      ("hypr-config", False), ("units", False), ("os-mcp", False),
                                                      ("canary-apps", False)]
    assert got["agentd-ping"]["detail"] == "no pong within 2 s"
    assert got["units"]["detail"] == "failed: bombadil-shell.service"
    assert got["os-mcp"]["detail"] == "os-mcp lists no tools" and got["canary-apps"]["detail"] == "notes: boom"
    assert got["bar-layer"]["detail"] and got["hypr-config"]["detail"]


def test_a_check_with_no_answer_fails_and_one_that_does_not_apply_is_skipped():
    got = doctor(collectors={"agentd": lambda: None, "layers": lambda: None, "configerrors": lambda: None,
                             "failed_units": lambda: None}, canary=lambda: (True, "skipped: no canary", True))
    assert got["agentd-ping"]["ok"] is False and got["agentd-ping"]["detail"] == "agentd did not answer"
    assert got["bar-layer"] == {"name": "bar-layer", "ok": False, "detail": "hyprctl did not answer"}
    assert got["hypr-config"]["detail"] == "hyprctl did not answer"
    assert got["units"] == {"name": "units", "ok": True, "detail": "skipped: there is no user manager to ask",
                            "skipped": True}
    assert got["canary-apps"]["ok"] is True and got["canary-apps"]["skipped"] is True


def test_the_doctor_never_raises():
    def boom(*a):
        raise RuntimeError("x")
    got = doctor(collectors={"agentd": boom, "layers": boom, "configerrors": boom, "failed_units": boom},
                 os_mcp=boom, canary=boom)
    assert got["os-mcp"] == {"name": "os-mcp", "ok": False, "detail": "os-mcp check failed: RuntimeError"}
    assert got["canary-apps"]["detail"] == "canary check failed: RuntimeError" and not got["canary-apps"]["ok"]
    assert len(got) == 6


def test_the_real_os_mcp_lists_its_tools():
    ok, detail = runner._os_mcp_tools([sys.executable, str(ROOT / "bin" / "bombadil-os-mcp")], timeout=20)
    assert ok and detail.endswith(" tools")


def test_os_mcp_that_misbehaves_is_a_failed_check_and_never_a_stalled_one(machine):
    def mcp(body):
        path = machine / "mcp.py"
        path.write_text(f"import json, sys, time\n{body}\n")
        return [sys.executable, str(path)]
    answer = ("for line in sys.stdin:\n    m = json.loads(line)\n    if m['method'] == 'initialize':\n"
              "        print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}}), flush=True)\n"
              "    elif m['method'] == 'tools/list':\n"
              "        print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': {'tools': TOOLS}}), flush=True)\n")
    assert runner._os_mcp_tools(mcp("TOOLS = [{'name': 'a'}, {'name': 'b'}]\n" + answer), 5) == (True, "2 tools")
    assert runner._os_mcp_tools(mcp("TOOLS = []\n" + answer), 5) == (False, "os-mcp lists no tools")
    assert runner._os_mcp_tools(mcp("sys.exit(3)"), 5) == (False, "os-mcp exited (3) without listing its tools")
    assert runner._os_mcp_tools(mcp("print('hello there')\nprint('{')"), 5)[0] is False
    t0 = time.monotonic()
    assert runner._os_mcp_tools(mcp("time.sleep(60)"), 0.4) == (False, "os-mcp did not list its tools within 0.4 s")
    assert time.monotonic() - t0 < 5
    assert runner._os_mcp_tools(["/no/such/program"], 1)[0] is False


def canary_dirs(machine, monkeypatch, *names):
    """Canary apps under the installed share, and a `bombadil-app` that loads all but "bad-app"."""
    share = machine / "installed"
    for name in names:
        (share / "share" / "canary" / name).mkdir(parents=True, exist_ok=True)
        (share / "share" / "canary" / name / "main.qml").write_text("import QtQuick\nItem {}\n")
    monkeypatch.setenv("BOMBADIL_SHARE", str(share))


def fake_app_tool(bindir, check=True):
    help_code = 0 if check else 2
    return program(bindir, "bombadil-app", f"""
if sys.argv[2:3] == ['--help']:
    sys.exit({help_code})
if 'bad-app' in sys.argv[2]:
    print(json.dumps({{'ok': False, 'errors': ['QML: boom\\nmore of it']}}))
elif 'hangs' in sys.argv[2]:
    time.sleep(60)
else:
    print(json.dumps({{'ok': True}}))
""")


def test_canary_apps_are_loaded_with_the_apps_own_check(machine, bindir, monkeypatch):
    canary_dirs(machine, monkeypatch, "good-app")
    fake_app_tool(bindir)
    ok, detail, skipped = runner._canary_apps(timeout=10)
    assert (ok, skipped) == (True, False) and detail == "2 loaded"               # the template and the canary
    canary_dirs(machine, monkeypatch, "good-app", "bad-app")
    ok, detail, skipped = runner._canary_apps(timeout=10)
    assert not ok and not skipped and detail == "bad-app: QML: boom"


def test_a_canary_that_hangs_or_prints_nonsense_fails_without_stalling(machine, bindir, monkeypatch):
    canary_dirs(machine, monkeypatch, "hangs-app")
    fake_app_tool(bindir)
    t0 = time.monotonic()
    ok, detail, _ = runner._canary_apps(timeout=0.5)
    assert not ok and "hangs-app: it did not load" in detail and time.monotonic() - t0 < 10
    program(bindir, "bombadil-app", "sys.exit(0 if sys.argv[2:3] == ['--help'] else 0)")
    assert runner._canary_apps(timeout=5)[0] is False                             # no JSON: it did not load


def test_without_a_check_command_or_a_canary_the_check_is_skipped(machine, bindir, monkeypatch):
    assert runner._canary_apps()[2] is True                        # no bombadil-app at all
    fake_app_tool(bindir, check=False)
    ok, detail, skipped = runner._canary_apps()
    assert ok and skipped and "no `bombadil-app check`" in detail
    fake_app_tool(bindir)
    monkeypatch.setattr(runner, "_canaries", list)
    ok, detail, skipped = runner._canary_apps()
    assert ok and skipped and "no canary" in detail


def test_the_doctor_writes_what_it_found_and_keeps_the_checks_no_probe_covers(store, monkeypatch):
    checks = [{"name": "agentd-ping", "ok": False, "detail": "no pong"},
              {"name": "os-mcp", "ok": False, "detail": "os-mcp exited (1) without listing its tools"},
              {"name": "canary-apps", "ok": False, "detail": "good-app: QML: boom\nmore"},
              {"name": "units", "ok": True, "detail": "no failed unit"}]
    monkeypatch.setattr(runner, "doctor_live", lambda collectors=None: checks)
    r = runner.Runner({}, store, VERSIONS, clock=lambda: NOW, sleep=lambda s: None)
    found = r.doctor(NOW)
    assert sorted(f.rule for f in found) == ["canary-apps", "os-mcp"]      # agentd-ping has its own probe
    assert json.loads((paths.loop_dir() / "doctor.json").read_text()) == {"t": NOW, "checks": checks}
    assert r.last_doctor == checks
    assert all(f.kind == "event" and f.days == 1 for f in found)
    again = r.doctor(NOW + 86400)
    assert sorted(f.n for f in again) == [2, 2]
    monkeypatch.setattr(runner, "doctor_live", lambda collectors=None: [{**c, "ok": True} for c in checks])
    assert r.doctor(NOW + 2 * 86400) == []


def test_a_doctor_that_breaks_is_a_line_and_no_findings(store, monkeypatch, capsys):
    def boom(collectors=None):
        raise RuntimeError("the doctor fell over")
    monkeypatch.setattr(runner, "doctor_live", boom)
    assert runner.Runner({}, store, VERSIONS, clock=lambda: NOW).doctor(NOW) == []
    assert "doctor: RuntimeError" in capsys.readouterr().err


def test_a_finding_from_the_doctor_reports_without_anything_of_his(store, monkeypatch):
    detail = "good-app: QML error: /home/daniel/Apps/taxes/main.qml:3: Daniel is not defined"
    monkeypatch.setattr(runner, "doctor_live",
                        lambda collectors=None: [{"name": "canary-apps", "ok": False, "detail": detail}])
    monkeypatch.setenv("HOME", "/home/daniel")
    monkeypatch.setenv("USER", "daniel")
    [found] = runner.Runner({}, store, VERSIONS, clock=lambda: NOW).doctor(NOW)
    text = report.build(found).render().lower()
    assert "daniel" not in text and "taxes" not in text


# -- what a finding needs from the store, through the runner --

def test_a_finding_is_a_report_and_a_held_copy_the_store_clears(store):
    r, _ = runner_for("apps-stacked-bad", store)
    [found] = r.run_once()
    held = report.hold(report.build(found))
    assert held is not None and held.read_text().startswith("Title: ")
    store.mark(found.fp, "reported")
    assert store.clear_found() == 1 and not held.exists()
    [again] = r.run_once(now=NOW + 3 * 86400)
    assert again.fp == found.fp and again.n == 1                    # found again: the problem is still there

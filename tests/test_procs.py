import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from bombadil import procs

TREE = textwrap.dedent("""
    import os, signal, subprocess, sys, time
    sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
    kids = [
        subprocess.Popen(sleep + ["worker"]),
        # a daemon that left its session and process group, like sudo with a pty does
        subprocess.Popen(sleep + ["daemon"], start_new_session=True),
        # a window the turn opened: it must survive Stop
        subprocess.Popen(sleep + ["--class=bombadil-browser"]),
        # something that ignores SIGINT and SIGTERM
        subprocess.Popen([sys.executable, "-c",
                          "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)", "stubborn"]),
    ]
    print(" ".join(str(k.pid) for k in kids), flush=True)
    time.sleep(60)
""")


def _alive(pid):
    p = procs.read(pid)
    return p is not None and not procs._zombie(pid)


def test_stop_ends_the_whole_turn_but_not_the_windows_it_opened():
    root = subprocess.Popen([sys.executable, "-c", TREE], stdout=subprocess.PIPE, text=True)
    worker, daemon, window, stubborn = map(int, root.stdout.readline().split())
    time.sleep(0.3)
    try:
        members = procs.Stopper().members(root.pid)
        assert {root.pid, worker, daemon, stubborn} <= set(members)
        assert window not in members
        out = procs.Stopper(grace=1.0).stop(root.pid)
        root.wait(timeout=5)
        time.sleep(0.2)
        assert not any(_alive(p) for p in (worker, daemon, stubborn))
        assert _alive(window)
        assert out["killed"] >= 1   # the stubborn one needed SIGKILL
    finally:
        for pid in (worker, daemon, window, stubborn):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        root.kill()


def test_root_commands_under_sudo_are_left_to_sudo_on_sigint(monkeypatch):
    """sudo relays SIGINT to its command; signalling both would interrupt pacman twice."""
    table = {
        10: procs.Proc(10, 1, 1000, 5, "claude", "claude\0-p"),
        11: procs.Proc(11, 10, 1000, 6, "sudo", "sudo\0pacman\0-S\0docker"),
        12: procs.Proc(12, 11, 0, 7, "pacman", "pacman\0-S\0docker"),
        13: procs.Proc(13, 1, 0, 8, "pacman", "pacman\0-S\0orphan"),   # its sudo is gone
    }
    sent, sudo_calls = [], []
    monkeypatch.setattr(procs, "read", lambda pid: table.get(pid))

    def kill(pid, sig):
        if table[pid].uid == 0:
            raise PermissionError
        sent.append((pid, sig))

    monkeypatch.setattr(procs.os, "kill", kill)
    s = procs.Stopper(runner=lambda argv, **k: sudo_calls.append(argv))
    s._signal(table, signal.SIGINT, relayed=True)
    assert sent == [(10, signal.SIGINT), (11, signal.SIGINT)]
    assert sudo_calls == [["sudo", "-n", "kill", "-INT", "--", "13"]]
    sent.clear()
    sudo_calls.clear()
    s._signal(table, signal.SIGKILL)
    assert sudo_calls == [["sudo", "-n", "kill", "-KILL", "--", "12", "13"]]


def test_a_reused_pid_is_never_signalled(monkeypatch):
    old = procs.Proc(20, 1, 1000, 5, "sleep", "sleep 60")
    monkeypatch.setattr(procs, "read", lambda pid: procs.Proc(20, 1, 1000, 999, "bash", "bash"))
    monkeypatch.setattr(procs.os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("signalled")))
    procs.Stopper()._signal({20: old}, signal.SIGKILL)


def test_windows_are_recognised_by_their_arguments():
    assert procs._is_window("chromium\0--ozone-platform=wayland\0--class=bombadil-browser")
    assert procs._is_window("/usr/bin/python3\0/usr/share/bombadil/bin/bombadil-app\0run\0passwords")
    assert procs._is_window("bombadil-app\0run\0passwords")
    assert procs._is_window("foot\0--app-id=bombadil-terminal")
    assert not procs._is_window("sh\0-c\0chromium --class=bombadil-browser; sleep 1")
    assert not procs._is_window("bombadil-app\0check\0passwords")


def test_scope_command(monkeypatch):
    monkeypatch.setattr(procs, "_scope_ok", True)
    assert procs.in_scope(["claude", "-p"], "bombadil-turn-1") == [
        "systemd-run", "--user", "--scope", "--quiet", "--collect", "--expand-environment=no",
        "--unit=bombadil-turn-1", "--", "claude", "-p"]
    monkeypatch.setattr(procs, "_scope_ok", False)
    assert procs.in_scope(["claude"], "x") == ["claude"]


def test_pacman_lock_is_removed_only_when_nobody_holds_it(monkeypatch, tmp_path):
    lock = tmp_path / "db.lck"
    lock.write_text("")
    monkeypatch.setattr(procs, "PACMAN_LOCK", lock)
    calls = []
    s = procs.Stopper(runner=lambda argv, **k: calls.append(argv))
    monkeypatch.setattr(procs, "all_procs", lambda: {1: procs.Proc(1, 0, 0, 1, "pacman", "pacman -S x")})
    monkeypatch.setattr(procs.time, "sleep", lambda s: None)
    s._unlock_pacman()
    assert calls == []
    monkeypatch.setattr(procs, "all_procs", lambda: {})
    s._unlock_pacman()
    assert calls == [["sudo", "-n", "rm", "-f", str(lock)]]


def _pids_running(marker):
    return [p.pid for p in procs.all_procs().values() if marker in p.cmdline and not procs._zombie(p.pid)]


@pytest.mark.parametrize("new_session", [True, False])
def test_what_a_dead_root_left_behind_is_still_stopped(new_session):
    """A job the turn put in the background outlives its root; Stop must still reach what
    that job starts after the first signal (`sleep 31.4159` here), with or without a scope."""
    root = subprocess.Popen(["sh", "-c", "sh -c 'sleep 1; sleep 31.4159; true' >/dev/null 2>&1 & sleep 30"],
                            start_new_session=new_session)
    # Reaped at once, as asyncio's child watcher does: its orphans are reparented.
    threading.Thread(target=root.wait, daemon=True).start()
    time.sleep(0.3)
    try:
        procs.Stopper(grace=1.5).stop(root.pid)
        time.sleep(0.2)
        assert _pids_running("31.4159") == []
    finally:
        for pid in _pids_running("31.4159"):
            os.kill(pid, signal.SIGKILL)


FAKE_PACMAN = textwrap.dedent("""
    import signal, sys, time
    log = sys.argv[1]
    def note(s):
        open(log, "a").write(f"{s} {time.time()}\\n")
    def on_int(*_):
        note("pacman-int")
        time.sleep(1.2)       # finishing the package it was extracting
        note("pacman-done")
        sys.exit(130)
    signal.signal(signal.SIGINT, on_int)
    signal.signal(signal.SIGTERM, lambda *_: note("pacman-term"))
    time.sleep(60)
""")
FAKE_CLI = textwrap.dedent("""
    import signal, subprocess, sys, time
    log, pacman, script = sys.argv[1:4]
    def on_int(*_):
        open(log, "a").write(f"cli-int {time.time()}\\n")
        sys.exit(130)
    signal.signal(signal.SIGINT, on_int)
    subprocess.Popen([pacman, "-c", script, log])
    time.sleep(60)
""")


def test_stop_lets_pacman_finish_its_package_before_the_rest_stops(tmp_path):
    pacman = tmp_path / "pacman"     # a python named pacman, so its comm is "pacman"
    pacman.symlink_to(sys.executable)
    log = tmp_path / "log"
    root = subprocess.Popen([sys.executable, "-c", FAKE_CLI, str(log), str(pacman), FAKE_PACMAN])
    try:
        for _ in range(50):
            if any(p.comm == "pacman" for p in procs.Stopper().members(root.pid).values()):
                break
            time.sleep(0.05)
        said = []
        out = procs.Stopper(grace=0.5).stop(root.pid, notify=said.append)
        root.wait(timeout=5)
        events = dict(line.split() for line in log.read_text().splitlines())
        assert said == ["Stopping after this package"]
        assert "pacman-term" not in events
        # The CLI was frozen while pacman finished, and only interrupted after it.
        assert float(events["cli-int"]) >= float(events["pacman-done"])
        assert out["waited"] >= 1.0
    finally:
        root.kill()

"""placement.close() and open_url() on real processes: no Qt, no Hyprland."""

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from bombadil import hypr
from bombadil.appkit import placement

# Stands in for a stuck `bombadil-app run`: ignores SIGTERM, and its "Command" runs in a
# session of its own, as a Command's program does.
STUCK = textwrap.dedent("""\
    import signal, subprocess, sys, time
    subprocess.Popen(["sleep", sys.argv[1]], start_new_session=True, stdout=subprocess.DEVNULL)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
    while True:
        time.sleep(1)
    """)


def _sleeping(marker: str) -> bool:
    return subprocess.run(["pgrep", "-f", f"^sleep {marker}$"], capture_output=True).returncode == 0


def _sleeper(marker: str) -> int | None:
    pids = subprocess.run(["pgrep", "-f", f"^sleep {marker}$"], capture_output=True, text=True).stdout.split()
    return int(pids[0]) if pids else None


def test_close_kills_a_stuck_app_with_its_commands_programs(tmp_path):
    name, marker = f"stuck-{os.getpid()}", f"{os.getpid() % 1000}.77"
    script = tmp_path / "stuck.py"
    script.write_text(STUCK)
    app = subprocess.Popen([sys.executable, str(script), marker, "bombadil-app", "run", name], stdout=subprocess.PIPE)
    try:
        assert app.stdout.readline() == b"ready\n" and _sleeping(marker) and placement.is_running(name)
        assert "was killed" in placement.close(name, wait=0.5)
        app.wait(5)
        deadline = time.monotonic() + 5
        while _sleeping(marker):
            assert time.monotonic() < deadline, "the Command's program outlived its app"
            time.sleep(0.05)
    finally:
        app.kill()
        app.stdout.close()
        subprocess.run(["pkill", "-9", "-f", f"^sleep {marker}$"], check=False)


class FakeHypr(hypr.Hyprland):
    available = True

    def request(self, command, timeout=10):
        return "[]" if command.startswith("j/") else "ok"


def test_open_url_does_not_start_the_browser_as_our_child(tmp_path, monkeypatch):
    # An app that opens a link starts Chromium; closing that app when stuck kills its children.
    marker = f"{os.getpid() % 1000}.43"
    chromium = tmp_path / "chromium"
    chromium.write_text(f"#!/bin/sh\nexec sleep {marker}\n")
    chromium.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    try:
        assert placement.open_url("example.com", FakeHypr()) == "opened https://example.com in the browser panel"
        deadline = time.monotonic() + 5
        while (pid := _sleeper(marker)) is None:
            assert time.monotonic() < deadline, "the browser did not start"
            time.sleep(0.05)
        ppid = int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[1])
        assert ppid != os.getpid()
    finally:
        subprocess.run(["pkill", "-9", "-f", f"^sleep {marker}$"], check=False)


SHOWN = [{"name": "DP-1", "focused": True, "specialWorkspace": {"name": "special:app-notes"}}]


class ScriptedHypr(hypr.Hyprland):
    """Answers j/monitors with `monitors`; a dispatch (or `eval`) takes the next entry of `script`:
    an exception is raised, a string is the reply."""
    available = True

    def __init__(self, script=(), monitors=SHOWN):
        self.script, self.monitors, self.sent, self.timeouts = list(script), monitors, [], []

    def request(self, command, timeout=10):
        self.sent.append(command)
        self.timeouts.append(timeout)
        if command == "j/monitors" and not isinstance(self.monitors, Exception):
            return json.dumps(self.monitors)
        step = self.monitors if command == "j/monitors" else self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return step


def _toggles(h):
    return [c for c in h.sent if "toggle_special" in c]


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), BrokenPipeError(), ConnectionResetError(), ""])
def test_a_toggle_that_was_sent_is_never_sent_again(monkeypatch, failure):
    # Hyprland runs a request it has read even when the client gave up waiting for the answer.
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    h = ScriptedHypr([failure])
    with pytest.raises(RuntimeError, match="Hyprland did not take"):
        placement.hide("notes", h)
    assert len(_toggles(h)) == 1


def test_a_connect_that_fails_is_tried_again(monkeypatch):
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    h = ScriptedHypr([ConnectionRefusedError(), FileNotFoundError(), "ok"])
    assert placement.hide("notes", h) == "notes hidden"
    assert len(_toggles(h)) == 3   # the first two never got as far as Hyprland
    h = ScriptedHypr([ConnectionRefusedError()] * 5)
    with pytest.raises(RuntimeError, match="Hyprland did not take"):
        placement.hide("notes", h)
    assert len(_toggles(h)) == 3


def test_requests_wait_as_long_as_hypr_does(monkeypatch):
    # A busy compositor answers in seconds; placement must not give up sooner than hypr would.
    class Slow(ScriptedHypr):
        def request(self, command, timeout=10):
            if timeout < 3:
                raise TimeoutError(f"timed out after {timeout} s")
            return super().request(command, timeout)
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    h = Slow()
    assert placement.hide("notes", h) == "notes hidden"
    assert len(_toggles(h)) == 1


def test_hide_does_not_call_a_drawer_it_could_not_look_for_not_on_screen(monkeypatch):
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="j/monitors"):
        placement.hide("notes", ScriptedHypr(monitors=TimeoutError("timed out")))
    assert placement.shown(ScriptedHypr(monitors=TimeoutError("timed out"))) == set()


def test_a_compositor_that_answers_late_toggles_the_drawer_once(monkeypatch):
    """A fake serial Hyprland: the first dispatch takes 2.5 s, then it runs even if nobody listens."""
    runtime_dir = Path("/tmp") / f"bombadil-test-{os.getpid()}-p"   # short: socket paths are limited
    sock_dir = runtime_dir / "hypr" / "sig"
    sock_dir.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    state = {"shown": True, "toggles": 0}

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                request = conn.recv(65536).decode()
                if "toggle_special" in request:
                    state["toggles"] += 1
                    if state["toggles"] == 1:
                        time.sleep(2.5)
                    state["shown"] = not state["shown"]
                    reply = "ok"
                else:
                    ws = "special:app-notes" if state["shown"] else ""
                    reply = json.dumps([{"name": "DP-1", "focused": True, "specialWorkspace": {"name": ws}}])
                try:
                    conn.sendall(reply.encode())
                except OSError:
                    pass
    try:
        server.bind(str(sock_dir / ".socket.sock"))
        server.listen(5)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
        monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig")
        threading.Thread(target=serve, daemon=True).start()
        assert placement.hide("notes") == "notes hidden"
        assert state == {"shown": False, "toggles": 1}
    finally:
        server.close()
        (sock_dir / ".socket.sock").unlink(missing_ok=True)
        for p in (sock_dir, sock_dir.parent, runtime_dir):
            p.rmdir()

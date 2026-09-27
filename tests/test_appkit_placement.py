"""placement.close() and open_url() on real processes: no Qt, no Hyprland."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

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

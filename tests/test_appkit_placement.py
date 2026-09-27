"""placement.close() on a real process: no Qt, no Hyprland."""

import os
import subprocess
import sys
import textwrap
import time

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

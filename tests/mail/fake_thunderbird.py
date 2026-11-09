#!/usr/bin/env python3
"""A stand-in for Thunderbird, for the tests of mail/engine.py. It is not a mail program: it only behaves, where
the engine can see, the way the real one does.

    fake_thunderbird.py --profile DIR [--no-remote] [--headless]

- It records what it was started with in `DIR/fake-thunderbird-<pid>.json`: argv, the names of its environment
  variables and the values of a few, its pid, process group, session and parent, the time it started and the pid
  of a child it started (mode `child`). One file per run, so a test can read the history of starts.
- It holds the profile's lock the way Thunderbird does on Linux: an fcntl lock on `DIR/.parentlock` (the kernel
  drops it however the process dies) and the symlink `DIR/lock` -> "127.0.0.1:+<pid>", which a clean exit removes and
  a kill leaves behind. A profile whose lock is held makes it print what the real one prints and exit 0.
- It exits cleanly (lock symlink removed, status 0) on SIGTERM, leaving a child it started running, which is
  what a content process that outlives the main one looks like.

`BOMBADIL_FAKE_TB` is a comma-separated list of modes: `crash` (exit 3 soon after starting), `deaf` (ignore SIGTERM:
only a kill stops it), `slow` (take 1.5 s to quit), `child` (start a `sleep` in the same group), `noisy:N` (write N
bytes to stderr), `quick` (exit 0 at once, as a Thunderbird that found nothing to do), `die:N` (exit N after 0.3 s).
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SEEN = ("DISPLAY", "WAYLAND_DISPLAY", "MOZ_ENABLE_WAYLAND", "MOZ_CRASHREPORTER_DISABLE", "MOZ_CRASHREPORTER_NO_REPORT",
        "NO_AT_BRIDGE", "XDG_RUNTIME_DIR", "HOME")


def main(argv: list[str]) -> int:
    modes = [m for m in os.environ.get("BOMBADIL_FAKE_TB", "").split(",") if m]
    profile = None
    for flag, value in zip(argv, argv[1:] + [None]):
        if flag in ("--profile", "-profile"):
            profile = Path(value)
    if profile is None:
        print("fake thunderbird: no --profile", file=sys.stderr)
        return 2

    parentlock = os.open(profile / ".parentlock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(parentlock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Thunderbird is already running, but is not responding. To use Thunderbird, you must first close the "
              "existing Thunderbird process, restart your device, or use a different profile.")
        return 0
    lock = profile / "lock"
    try:
        os.symlink(f"127.0.0.1:+{os.getpid()}", lock)
    except FileExistsError:   # the fcntl lock is ours, so what was there is a crash's
        lock.unlink()
        os.symlink(f"127.0.0.1:+{os.getpid()}", lock)

    child = subprocess.Popen(["sleep", "300"]) if "child" in modes else None
    record = {"argv": argv, "env_keys": sorted(os.environ), "env": {k: os.environ[k] for k in SEEN if k in os.environ},
              "pid": os.getpid(), "pgid": os.getpgrp(), "sid": os.getsid(0), "ppid": os.getppid(),
              "child": child.pid if child else None, "modes": modes, "started": time.time_ns()}
    out = profile / f"fake-thunderbird-{os.getpid()}.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, out)

    for mode in modes:
        if mode.startswith("noisy:"):
            sys.stderr.write("x" * int(mode.partition(":")[2]))
            sys.stderr.flush()

    quitting = []

    def on_term(*_):
        quitting.append(time.monotonic())

    signal.signal(signal.SIGTERM, signal.SIG_IGN if "deaf" in modes else on_term)
    started = time.monotonic()
    while True:
        if "quick" in modes:
            break
        if "crash" in modes and time.monotonic() - started > 0.3:
            return 3
        for mode in modes:
            if mode.startswith("die:") and time.monotonic() - started > 0.3:
                return int(mode.partition(":")[2])
        if quitting and time.monotonic() - quitting[0] >= (1.5 if "slow" in modes else 0):
            break
        time.sleep(0.02)
    try:
        if os.readlink(lock) == f"127.0.0.1:+{os.getpid()}":
            lock.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

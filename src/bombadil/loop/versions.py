"""What Bombadil runs on: its own build, the compositor, the shell, the two AI tools and the kit.

A bug report is worth little without them, and a finding that only shows on one Hyprland or one
Claude Code is found by them. Each is asked once per process, with a short timeout, and remembered
(a program that was not there is asked again after a few minutes). Nothing here raises: a program
that is missing, hangs or prints something odd reads "unknown".

Nothing here belongs on a turn's path. The first `collect()` starts a few programs, so call it from
the loop's own process or thread.

  build()       the commit or VERSION this tree is ("" when nothing says)
  hyprland() quickshell() claude_code() codex() kit()     a version, or "unknown"
  machine()     "VM", "laptop", "desktop" or "" when not known
  collect()     all of it as one dict, for evidence bundles and reports
  capture()     run a program for its output, bounded (the runner uses it too)
"""

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

from .. import paths

UNKNOWN = "unknown"
TIMEOUT = 4.0           # seconds a program gets to say its version
RETRY_UNKNOWN = 300.0   # a program that was not there is asked again after this

_NUMBER = re.compile(r"\d+(?:\.\d+)+")
_CHASSIS = {"laptop": {8, 9, 10, 14, 30, 31, 32},      # portable, laptop, notebook, sub-notebook, tablet...
            "desktop": {3, 4, 5, 6, 7, 13, 15, 16, 24, 35}}


class Captured(NamedTuple):
    code: int
    out: str
    err: str


def capture(cmd: list[str], timeout: float | None = None, input: str | None = None) -> Captured | None:
    """Run a program and give what it printed, or None when it is not there or took longer than
    `timeout` (TIMEOUT when not given; then it and whatever it started are killed). Its own process
    group, so a program that forks and keeps our pipes open cannot hold us past the deadline."""
    timeout = TIMEOUT if timeout is None else timeout
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                                start_new_session=True)
    except (OSError, ValueError):
        return None
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.communicate(timeout=1)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        return None
    except (OSError, ValueError):
        return None
    return Captured(proc.returncode, out or "", err or "")


# -- asked once --

_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}


def reset() -> None:
    """Forget everything that was asked (tests; a process that installed something)."""
    with _lock:
        _cache.clear()


def _cached(name: str, ask: Callable[[], str], unknown: str = UNKNOWN) -> str:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(name)
    if hit is not None and (hit[0] != unknown or now - hit[1] < RETRY_UNKNOWN):
        return hit[0]
    try:
        value = ask() or unknown
    except Exception:  # noqa: BLE001 - a version we cannot read is "unknown", never a failure
        value = unknown
    with _lock:
        _cache[name] = (value, now)
    return value


def _version(text: str, after: str = "") -> str:
    """The first version number in `text` (after the word `after` when it is there)."""
    if after:
        m = re.search(rf"{re.escape(after)}\s+v?(\d+(?:\.\d+)+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
    m = _NUMBER.search(text)
    return m.group(0) if m else ""


def _says(cmd: list[str], after: str = "") -> str:
    """What `cmd` says its version is, "" when it does not say."""
    done = capture(cmd)
    return _version(done.out or done.err, after) if done is not None and done.code == 0 else ""


# -- what this tree is --

def _build() -> str:
    try:
        from .signals import build_id
        return build_id()
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[3]
    share = paths.share_dir()
    for stamp in (share / "VERSION", share.parent / "VERSION", root / "VERSION"):
        try:
            lines = stamp.read_text().strip().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if lines and lines[0].strip():
            return lines[0].strip()[:40]
    if (root / ".git").exists():
        done = capture(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], timeout=3)
        if done is not None and done.code == 0:
            return done.out.strip()[:40]
    return ""


def build() -> str:
    """The build this tree is: signals.build_id() when that exists, else the VERSION stamp beside the
    installed share directory, else the checkout's git short hash, else ""."""
    return _cached("build", _build, unknown="")


# -- what it runs on --

def hyprland() -> str:
    """The running compositor's version (what `hyprctl` says), else the installed one."""
    def ask() -> str:
        done = capture(["hyprctl", "-j", "version"])
        if done is not None and done.code == 0:
            try:
                said = json.loads(done.out)
            except ValueError:
                said = None
            text = (said.get("version") or said.get("tag")) if isinstance(said, dict) else None
            found = _NUMBER.search(text) if isinstance(text, str) else None
            if found:
                return found.group(0)
        return _says(["Hyprland", "--version"], "Hyprland")
    return _cached("Hyprland", ask)


def quickshell() -> str:
    return _cached("Quickshell", lambda: _says(["quickshell", "--version"], "quickshell"))


def claude_code() -> str:
    return _cached("Claude Code", lambda: _says(["claude", "--version"]))


def codex() -> str:
    return _cached("codex-cli", lambda: _says(["codex", "--version"], "codex-cli"))


def kit() -> str:
    """The app kit has no version of its own, so it is what it is made of: the newest version its
    qmldir names, how many parts it lists, and a short hash of the list ("1.0 (13 parts, 3f9a1c)")."""
    def ask() -> str:
        root = Path(__file__).resolve().parents[3]
        for base in (paths.share_dir() / "share", root / "share"):
            try:
                text = (base / "qml" / "Bombadil" / "qmldir").read_text()
            except (OSError, UnicodeDecodeError):
                continue
            parts = [ln.split() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
                     and not ln.startswith("module ")]
            versions = [p[-2] for p in parts if len(p) >= 3 and re.fullmatch(r"\d+(?:\.\d+)*", p[-2])]
            newest = max(versions, key=lambda v: [int(x) for x in v.split(".")], default="")
            digest = hashlib.sha1(text.encode()).hexdigest()[:6]
            return f"{newest or '?'} ({len(parts)} parts, {digest})"
        return ""
    return _cached("kit", ask)


def machine() -> str:
    """What kind of machine this is, only when something says: BOMBADIL_MACHINE (their own word for
    it, like "laptop VM"), else "VM" when systemd sees virtualisation, else the chassis type."""
    def ask() -> str:
        said = "".join(c for c in os.environ.get("BOMBADIL_MACHINE", "") if c.isalnum() or c in " -_")
        if said.strip():
            return said.strip()[:30]
        done = capture(["systemd-detect-virt"], timeout=2)
        if done is not None and done.code == 0 and done.out.strip() not in ("", "none"):
            virt = done.out.strip()
            return "container" if virt in ("docker", "podman", "lxc", "wsl", "systemd-nspawn") else "VM"
        try:
            chassis = int(Path("/sys/class/dmi/id/chassis_type").read_text().strip())
        except (OSError, ValueError):
            return ""
        return next((kind for kind, nums in _CHASSIS.items() if chassis in nums), "")
    return _cached("machine", ask, unknown="")


def collect(refresh: bool = False) -> dict[str, str]:
    """Everything above as one dict: {"build", "machine", "Hyprland", "Quickshell", "Claude Code",
    "codex-cli", "kit"}. The programs are asked side by side, so this takes one timeout at worst."""
    if refresh:
        reset()
    asks = {"build": build, "machine": machine, "Hyprland": hyprland, "Quickshell": quickshell,
            "Claude Code": claude_code, "codex-cli": codex, "kit": kit}
    with ThreadPoolExecutor(max_workers=len(asks)) as pool:
        futures = {name: pool.submit(fn) for name, fn in asks.items()}
    out = {}
    for name, future in futures.items():
        try:
            out[name] = future.result()
        except Exception:  # noqa: BLE001
            out[name] = "" if name in ("build", "machine") else UNKNOWN
    return out

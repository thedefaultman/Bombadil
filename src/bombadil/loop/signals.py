"""What the bar and agentd say about themselves, kept where the loop's probes can read it.

agentd hears the bar say hello, stay alive, report where its parts are, acknowledge a summon and
complain about friction. `Signals` writes that down and does nothing else with it:

  signals.jsonl   append only, one row a fact: hello, summon, focus_ack, focus_timeout, friction, restart
  bar.json        the bar's last report, rewritten whole and atomically, at most every 2 s
  agentd.json     who is serving: pid, start, build, socket; written once when agentd starts

All of it is best effort. A disk that will not take a write, a message with the wrong shapes in
it, costs one line on stderr (once) and never the connection: nothing here raises, and nothing
here is on a turn's path.
"""

import functools
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import paths

SUMMON_GRACE = 1.5   # seconds a summon may wait for its focus_ack before it is written up
BAR_EVERY = 2.0      # bar.json is rewritten at most this often
MAX_SCREENS = 16
MAX_RECTS = 200
MAX_WAITING = 100    # summons kept while they wait for an ack (a tick that never comes must not grow this)


def events_path() -> Path:
    return paths.loop_dir() / "signals.jsonl"


def bar_path() -> Path:
    return paths.loop_dir() / "bar.json"


def agentd_path() -> Path:
    return paths.loop_dir() / "agentd.json"


def read_events(path: Path | None = None, since: float = 0.0) -> list[dict]:
    """The rows of signals.jsonl written after `since` (epoch seconds, exclusive), oldest first.
    A line that is not a row is skipped; a missing file is no rows."""
    out = []
    try:
        for line in (path or events_path()).read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _num(row.get("t"), 0.0) > since:
                out.append(row)
    except (OSError, UnicodeDecodeError):
        pass
    return out


def read_bar(path: Path | None = None) -> dict | None:
    """The bar's last report, or None when it has never made one (or the file is unreadable)."""
    try:
        bar = json.loads((path or bar_path()).read_text())
    except (OSError, ValueError):
        return None
    return bar if isinstance(bar, dict) else None


def build_id() -> str:
    """Which build this is: the VERSION stamp shipped with the tree, else the checkout's git
    short hash, else "". May run `git` for a moment: call it off the event loop."""
    root = Path(__file__).resolve().parents[3]
    share = paths.share_dir()
    for stamp in (share / "VERSION", share.parent / "VERSION", root / "VERSION"):
        try:
            text = stamp.read_text().strip().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if text and text[0].strip():
            return text[0].strip()[:40]
    if (root / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                               text=True, timeout=3, check=False)
            return r.stdout.strip()[:40] if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def _num(v, default=None):
    """A finite number as it should be written (whole numbers as ints), else `default`."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return int(v) if float(v).is_integer() else round(float(v), 3)


def _scalars(msg: dict, skip=("type", "t", "kind")) -> dict:
    """The plain values of a message, so a report can grow a key without this code knowing it."""
    out = {}
    for k, v in msg.items():
        if k in skip or not isinstance(k, str) or len(k) > 40 or len(out) >= 10:
            continue
        if isinstance(v, bool) or v is None:
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = _num(v)
        elif isinstance(v, str):
            out[k] = v[:80]
    return out


def _quiet(fn):
    """Run under the lock, and let nothing out."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            with self._lock:
                return fn(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - what the loop writes down never costs a connection
            self._complain(f"{fn.__name__}: {type(e).__name__}: {e}")
    return wrapper


class Signals:
    def __init__(self, loop_dir: Path | str | None = None, clock=time.time):
        self._dir = Path(loop_dir) if loop_dir is not None else None
        self._clock = clock
        self._lock = threading.RLock()    # for a caller on another thread; agentd calls from its event loop
        self._next_summon = 0
        self._summons: dict[int, tuple[float, bool]] = {}   # waiting for an ack: id -> (when, bar was there)
        self._timed_out: dict[int, float] = {}               # written up already; an ack may still come
        self._pid: int | None = None
        self._build = ""
        self._connected_at: float | None = None
        self._alive_at: float | None = None
        self._screens: dict[str, dict] = {}
        self._connected = False   # a hello came and the bar has not gone since
        self._gone = False        # the bar went, and has not said hello again
        self._bar_written = -math.inf
        self._dirty = False
        self._complained: set[str] = set()

    # -- the bar --

    @_quiet
    def hello(self, msg: dict) -> None:
        """The bar connected. A different pid than last time, or any hello after the bar went,
        is a restart and is written up as one."""
        now = self._clock()
        pid = msg.get("pid")
        pid = pid if isinstance(pid, int) and not isinstance(pid, bool) else None
        build = str(msg.get("build") or "")[:80]
        was = self._pid
        restart = self._gone or (was is not None and pid != was)
        self._pid, self._build = pid, build
        self._connected, self._gone = True, False
        self._connected_at = self._alive_at = now
        self._screens = {}    # a new connection reports its parts again; the old ones may be wrong now
        self._append({"t": _t(now), "kind": "hello", "client": "bar", "pid": pid, "build": build})
        if restart:
            self._append({"t": _t(now), "kind": "restart", "pid": pid, "was": was})
        self._dirty = True
        self._flush(now, force=True)

    @_quiet
    def alive(self) -> None:
        now = self._clock()
        self._alive_at = now
        self._dirty = True
        self._flush(now)

    @_quiet
    def rects(self, msg: dict) -> None:
        """The bar's parts on one screen: {"screen", "w", "h", "rects": [{"name", "x", "y", "w", "h"}]}."""
        name = str(msg.get("screen") or "")[:60]
        if not name or (name not in self._screens and len(self._screens) >= MAX_SCREENS):
            return
        rects = []
        for r in msg.get("rects") if isinstance(msg.get("rects"), list) else []:
            if not isinstance(r, dict) or len(rects) >= MAX_RECTS:
                continue
            x, y, w, h = (_num(r.get(k)) for k in ("x", "y", "w", "h"))
            if None in (x, y, w, h):
                continue
            rects.append({"name": str(r.get("name") or "")[:60], "x": x, "y": y, "w": w, "h": h})
        self._screens[name] = {"w": _num(msg.get("w")), "h": _num(msg.get("h")), "rects": rects}
        self._dirty = True
        self._flush(self._clock())

    @_quiet
    def bar_gone(self) -> None:
        """The bar's connection ended. Its last report stays as it was; alive_at goes stale."""
        self._connected, self._gone = False, True
        self._dirty = True
        self._flush(self._clock(), force=True)

    @_quiet
    def friction(self, msg: dict) -> None:
        self._append({"t": _t(self._clock()), "kind": "friction", **_scalars(msg)})

    # -- summons --

    def summon(self, screen: str = "") -> int:
        """A summon is about to be broadcast: give it its id and start waiting for the ack."""
        with self._lock:
            self._next_summon += 1
            sid = self._next_summon
        self._summoned(sid, screen)
        return sid

    @_quiet
    def _summoned(self, sid: int, screen: str) -> None:
        now = self._clock()
        self._summons[sid] = (now, self._connected)
        while len(self._summons) > MAX_WAITING:
            self._summons.pop(next(iter(self._summons)))
        row = {"t": _t(now), "kind": "summon", "id": sid}
        if screen:
            row["screen"] = str(screen)[:60]
        self._append(row)

    @_quiet
    def focus_ack(self, msg: dict) -> None:
        """The bar's input has the keyboard: {"id", "ms"}. One that comes after its summon was
        written up as a timeout is marked late, so a reader can tell slow from stuck."""
        sid = msg.get("id") if isinstance(msg.get("id"), int) and not isinstance(msg.get("id"), bool) else None
        row = {"t": _t(self._clock()), "kind": "focus_ack", "id": sid, "ms": _num(msg.get("ms"))}
        if self._summons.pop(sid, None) is None and sid in self._timed_out:
            row["late"] = True
        self._append(row)

    # -- the clock --

    @_quiet
    def tick(self, now: float | None = None) -> None:
        """Write up every summon that has waited too long, and catch bar.json up. Safe to call
        every few seconds."""
        now = self._clock() if now is None else now
        for sid, (t0, bar) in list(self._summons.items()):
            if now - t0 > SUMMON_GRACE:
                del self._summons[sid]
                self._timed_out[sid] = t0
                while len(self._timed_out) > MAX_WAITING:
                    self._timed_out.pop(next(iter(self._timed_out)))
                self._append({"t": _t(now), "kind": "focus_timeout", "id": sid, "waited": round(now - t0, 2),
                              "bar": bar})
        self._flush(now)

    # -- agentd --

    def agentd_started(self, socket_path: Path | str) -> None:
        """agentd is serving. Looks up the build, which may run git: call it off the event loop."""
        try:
            build = build_id()
        except Exception as e:  # noqa: BLE001 - a build nobody could read is still a running agentd
            self._complain(f"build_id: {type(e).__name__}: {e}")
            build = ""
        self._agentd_info({"pid": os.getpid(), "started": _t(self._clock()), "build": build,
                           "socket": str(socket_path)})

    @_quiet
    def _agentd_info(self, info: dict) -> None:
        self._write_json(self._file("agentd.json"), info)

    # -- writing --

    def _file(self, name: str) -> Path:
        return (self._dir or paths.loop_dir()) / name

    def _append(self, row: dict) -> None:
        path = self._file("signals.jsonl")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            self._complain(f"{path.name}: {e}")

    def _flush(self, now: float, force: bool = False) -> None:
        """Write bar.json when something changed, but not more often than every BAR_EVERY seconds
        (a change that has to wait goes out on a later call or tick)."""
        if not self._dirty or (not force and now - self._bar_written < BAR_EVERY):
            return
        if self._connected_at is None and self._alive_at is None and not self._screens:
            self._dirty = False   # nothing to say about a bar that never spoke
            return
        self._bar_written = now
        if self._write_json(self._file("bar.json"), {
                "pid": self._pid, "connected_at": _t(self._connected_at), "alive_at": _t(self._alive_at),
                "build": self._build, "screens": self._screens}):
            self._dirty = False

    def _write_json(self, path: Path, obj: dict) -> bool:
        """Whole file or none: write beside it, then rename over it."""
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(obj))
            os.replace(tmp, path)
            return True
        except OSError as e:
            self._complain(f"{path.name}: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def _complain(self, text: str) -> None:
        """One line on stderr, once per distinct trouble: a full disk would otherwise say it every tick."""
        if text not in self._complained and len(self._complained) < 50:
            self._complained.add(text)
            print(f"signals: {text}", file=sys.stderr)


def _t(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds, 3)

"""The runner: collects what the probes look at, runs them, and keeps what they find.

The probes (probes.py) are pure; this is the part that touches the machine. Every collector asks one
thing (hyprctl, coredumpctl, agentd's socket, a file the bar or agentd wrote) and answers with data or
None, never raising and never waiting past its own deadline: a compositor that hangs or prints prose
costs the probes that needed it "not checked", not a stalled run. Nothing here is on a turn's path;
the runner lives in its own process (bin/bombadil-probe) and only reads.

  Collectors        the real collectors, one method per Observation field; `Runner` also takes a dict
                    {field: callable} (what the tests do), and a field left out is None
  Runner            run_once() looks, looks again 500 ms later at what is red, and records what counts;
                    run_event() and tick() decide when; serve() is the prober's loop
  EventStream       Hyprland's socket2 events, reconnecting with a backoff
  Presence          is he away (no prompt for 10 minutes, or the screen locked)?
  doctor_live()     the read-only part of bombadil-smoke, as the user: what `bombadil doctor --live` prints

One thread at a time: the findings store is an SQLite connection, which belongs to the thread that
opened it. The 500 ms retry sleeps in the caller's thread.
"""

import functools
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import statistics
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .. import hypr, paths
from . import ledger as ledger_mod
from . import versions as versions_mod
from .findings import SAME_EPISODE, Finding, FindingsStore, fingerprint_of
from .probes import (
    DRAWER_GRACE,
    PROBES,
    Observation,
    Result,
    _program,
    loads,
    run_all,
    run_probe,
    write_json_atomic,
)

HYPR_TIMEOUT = 3.0       # seconds a hyprctl answer may take
AGENTD_TIMEOUT = 2.0     # a pong within this, or agentd does not answer
COREDUMP_TIMEOUT = 8.0
COREDUMP_EVERY = 300.0   # coredumpctl reads the journal: not more often than this
COREDUMP_DAYS = 7
OS_MCP_TIMEOUT = 20.0    # the smoke test's own limit
CANARY_TIMEOUT = 30.0
STUCK_MAX = 4            # hyprctl calls left blocked before it is not asked at all
EVENT_DAYS = 3           # how far back signals.jsonl is read
LEDGER_DAYS = 3
LEDGER_ROWS = 400
TAIL_BYTES = 1 << 20     # how much of the end of a growing file is read
TURN_CACHE = 5.0         # seconds one read of the turns serves all three fields that come from them
LOG_TURNS = 30           # per-turn logs read each run: the newest this many
TOOL_TEXT = 4000         # characters of a tool's answer kept (app-check reads the first error in it)
MEDIAN_MIN = 4           # turns a group needs before it has a usual length
APP_DAYS = 3             # an app's status older than this is history
LOG_LINES = 40
OS_TOOLS = "mcp__bombadil-os__"

PERIOD = 60.0            # seconds between runs while he is away
PRESENCE_EVERY = 15.0    # and he is not asked about more often than this
DOCTOR_EVERY = 86400.0   # the idle doctor, once a day while he is away
AWAY_AFTER = 600.0       # no prompt for this long is away
SETTLE = 1.0             # a window has this long to be placed before the layout checks look at it
DRAWER_CHECK = DRAWER_GRACE + 0.1    # the drawer is judged when its two seconds of grace are over

WINDOW_PROBES = ("drawer-focus", "apps-stacked", "window-oversize", "window-under-bar")
WINDOW_EVENTS = ("openwindow", "closewindow", "activespecial")
COLLECTED = ("clients", "monitors", "layers", "activewindow", "configerrors", "bar", "events", "agentd",
             "coredumps", "ledger", "tool_results", "turn_errors", "groups", "medians", "apps")
ALWAYS = ("bar", "events")           # files a few KB long: every run may as well have them

_ROOT = Path(__file__).resolve().parents[3]


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


# -- a call that may hang --

_stuck = 0
_stuck_lock = threading.Lock()


def bounded(fn: Callable, timeout: float):
    """(done, value): `fn()` run on a thread that is given `timeout` seconds. A call that does not
    come back is left to finish on its own (it is a daemon, and only ever blocked on something of the
    machine's); once STUCK_MAX are waiting, nothing more is started until one comes back."""
    global _stuck
    with _stuck_lock:
        if _stuck >= STUCK_MAX:
            return False, None
    box: dict = {}
    finished = threading.Event()
    late = {"yes": False}

    def work():
        global _stuck
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - whatever it was, the caller reads it as no answer
            box["error"] = e
        finally:
            finished.set()
            with _stuck_lock:
                if late["yes"]:
                    _stuck -= 1

    threading.Thread(target=work, daemon=True, name="runner-call").start()
    if finished.wait(timeout):
        return "error" not in box, box.get("value")
    with _stuck_lock:
        if finished.is_set():
            return "error" not in box, box.get("value")
        late["yes"] = True
        _stuck += 1
    return False, None


# -- Hyprland's own paths --

def hypr_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "hypr"


def find_instance() -> str | None:
    """The signature of the running Hyprland: the one in the environment when its socket is there, else
    the newest instance whose socket is. A user unit does not always get the environment Hyprland had."""
    base = hypr_dir()
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig and (base / sig / ".socket.sock").exists():
        return sig
    try:
        live = [d for d in base.iterdir() if (d / ".socket.sock").exists()]
    except OSError:
        return None
    return max(live, key=lambda d: d.stat().st_mtime).name if live else None


def adopt_instance() -> str | None:
    """Point HYPRLAND_INSTANCE_SIGNATURE at the running Hyprland when it is not set (or stale), so the
    hyprctl calls of this process find it. For the prober's own process; returns the signature."""
    sig = find_instance()
    if sig and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") != sig:
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = sig
    return sig


# -- tolerant readers of what the bar and agentd write --

def _read_json(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _tail(path: Path, nbytes: int = TAIL_BYTES) -> list[bytes] | None:
    """The whole lines in the last `nbytes` of a file (the first, which may be cut, is dropped), or
    None when it cannot be read."""
    try:
        with path.open("rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - nbytes)
            f.seek(start)
            lines = f.read().splitlines()
    except OSError:
        return None
    return lines[1:] if start > 0 and lines else lines


def tail_lines(path: Path, lines: int = LOG_LINES, window: int = 64 * 1024) -> list[str]:
    raw = _tail(path, window)
    return [ln.decode(errors="replace") for ln in raw[-lines:]] if raw else []


def read_events(path: Path | None = None, now: float | None = None,
                days: float = EVENT_DAYS) -> list[dict] | None:
    """The rows of signals.jsonl from the last `days`: what the bar said and agentd wrote down. Lines that
    are not rows are skipped; None when the file is not there."""
    lines = _tail(path or paths.loop_dir() / "signals.jsonl")
    if lines is None:
        return None
    floor = (time.time() if now is None else now) - days * 86400
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (t := _num(row.get("t"))) is not None and t >= floor:
            rows.append(row)
    return rows


def read_bar(path: Path | None = None) -> dict | None:
    return _read_json(path or paths.loop_dir() / "bar.json")


def read_agentd(path: Path | None = None) -> dict | None:
    return _read_json(path or paths.loop_dir() / "agentd.json")


# -- agentd --

def agentd_liveness(path: Path | None = None, timeout: float = AGENTD_TIMEOUT) -> dict:
    """A real connection to agentd's socket and a ping it answers: {"connected", "ponged", "latency",
    "error"}. A socket file with nothing behind it (a stale one) is not a connection. agentd greets
    every client with its status and names first; those are read past."""
    path = path or paths.socket_path()
    out = {"connected": False, "ponged": False, "latency": None, "error": ""}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(path))
        out["connected"] = True
        deadline = time.monotonic() + timeout
        sock.sendall(b'{"type": "ping"}\n')
        sent, buf = time.monotonic(), b""
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            chunk = sock.recv(65536)
            if not chunk:
                out["error"] = "agentd hung up before it answered"
                return out
            *lines, buf = (buf + chunk).split(b"\n")
            buf = buf[-(1 << 20):]
            for line in lines:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "pong":
                    out.update(ponged=True, latency=round(time.monotonic() - sent, 3))
                    return out
        out["error"] = f"no pong within {timeout:g} s"
    except TimeoutError:
        out["error"] = f"no answer within {timeout:g} s"
    except (ConnectionResetError, BrokenPipeError):
        out["error"] = "agentd hung up before it answered"      # whether it closed before or after our ping
    except OSError as e:
        out["error"] = e.strerror or type(e).__name__
    finally:
        sock.close()
    return out


# -- the collectors --

def _safe(fn):
    """A collector answers with data or None; whatever goes wrong is None and one line on stderr."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - a collector that fails is a probe that is not checked
            _complain(f"{fn.__name__}: {type(e).__name__}: {e}")
            return None
    return wrapper


_complained: set[str] = set()


def _complain(text: str) -> None:
    """One line on stderr per distinct trouble: a prober that prints every minute is its own bug."""
    if text not in _complained and len(_complained) < 100:
        _complained.add(text)
        print(f"runner: {text}", file=sys.stderr)


class Collectors:
    """What the probes look at, collected from the machine. Every method is a collector: no arguments,
    data or None. Pass `hyprland` (an object with `request(command) -> str`) to fake the compositor."""

    def __init__(self, hyprland=None, hypr_timeout: float = HYPR_TIMEOUT,
                 clock: Callable[[], float] = time.time, agentd_timeout: float = AGENTD_TIMEOUT):
        self.hypr = hyprland if hyprland is not None else hypr.Hyprland()
        self.hypr_timeout = hypr_timeout
        self.agentd_timeout = agentd_timeout
        self._clock = clock
        self._hypr_down = False
        self._cores: tuple[float, list | None] | None = None
        self._turns: tuple[float, dict] | None = None
        self._requests: tuple[float, tuple | None] | None = None

    def begin(self) -> None:
        """A new look is starting: a hyprctl that hung is asked about again."""
        self._hypr_down = False

    def refresh(self) -> None:
        """Forget what is kept between looks (coredumps, the turns)."""
        self._cores = self._turns = self._requests = None

    # hyprctl

    def _hyprctl(self, what: str, kind: type):
        if self._hypr_down or getattr(self.hypr, "available", True) is False:
            return None
        done, text = bounded(lambda: self.hypr.request(f"j/{what}"), self.hypr_timeout)
        if not done:
            self._hypr_down = True      # the rest of this look would wait as long again
            return None
        data = loads(text)
        return data if isinstance(data, kind) else None

    @_safe
    def clients(self):
        return self._hyprctl("clients", list)

    @_safe
    def monitors(self):
        return self._hyprctl("monitors", list)

    @_safe
    def layers(self):
        return self._hyprctl("layers", dict)

    @_safe
    def activewindow(self):
        return self._hyprctl("activewindow", dict)

    @_safe
    def configerrors(self):
        return self._hyprctl("configerrors", list)

    # what the bar and agentd wrote

    @_safe
    def bar(self):
        return read_bar()

    @_safe
    def events(self):
        return read_events(now=self._clock())

    @_safe
    def agentd_started(self):
        return _num((read_agentd() or {}).get("started"))

    def agentd(self) -> dict:
        try:
            return agentd_liveness(timeout=self.agentd_timeout)
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "ponged": False, "latency": None, "error": type(e).__name__}

    # crashes and units

    @_safe
    def coredumps(self):
        """coredumpctl's list, only Bombadil's programs. A Python program shows as python: its command
        line (from `coredumpctl info`) says which. None when there is no coredumpctl or no journal."""
        now = time.monotonic()
        if self._cores is not None and now - self._cores[0] < COREDUMP_EVERY:
            return self._cores[1]
        rows = self._list_coredumps()
        self._cores = (now, rows)
        return rows

    def _list_coredumps(self) -> list | None:
        done = versions_mod.capture(["coredumpctl", "list", "--json=short", "--no-pager"], COREDUMP_TIMEOUT)
        if done is None:
            return None
        if done.code != 0:
            return [] if "No coredumps found" in done.out + done.err else None     # none is an answer
        try:
            rows = json.loads(done.out) if done.out.strip() else []
        except ValueError:
            return None
        if not isinstance(rows, list):
            return None
        floor, ours, looked = self._clock() - COREDUMP_DAYS * 86400, [], 0
        for row in (r for r in rows if isinstance(r, dict)):
            exe = os.path.basename(str(row.get("exe") or "")).lower()
            when = _epoch(row.get("time"))
            recent = when is None or when >= floor
            if recent and exe.startswith("python") and isinstance(row.get("pid"), int) and looked < 10:
                looked += 1
                row = {**row, **self._who(row["pid"])}
            if _program(row) is not None:
                ours.append(row)
        return ours

    def _who(self, pid: int) -> dict:
        """comm and cmdline of a dumped process, from `coredumpctl info`."""
        done = versions_mod.capture(["coredumpctl", "info", "--no-pager", str(pid)], COREDUMP_TIMEOUT)
        out: dict = {}
        for line in (done.out.splitlines() if done is not None else []):
            key, _, value = line.strip().partition(":")
            if key == "Command Line":
                out["cmdline"] = value.strip()
            elif key == "PID":
                m = re.search(r"\((.+)\)\s*$", value)
                if m:
                    out["comm"] = m.group(1)
        return out

    @_safe
    def failed_units(self):
        """The failed user units of Bombadil, from `systemctl --user --failed`; None without a user
        manager."""
        cmd = ["systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager"]
        done = versions_mod.capture(cmd, 5)
        if done is None or done.code != 0:
            return None
        names = [ln.split()[0] for ln in done.out.splitlines() if ln.split()]
        return [n for n in names if n.startswith("bombadil-")]

    # the turns

    def _turn_data(self) -> dict:
        now = time.monotonic()
        if self._turns is not None and now - self._turns[0] < TURN_CACHE:
            return self._turns[1]
        data = {"ledger": None, "tool_results": None, "turn_errors": None}
        try:
            data = self._read_turns()
        except Exception as e:  # noqa: BLE001
            _complain(f"turns: {type(e).__name__}: {e}")
        self._turns = (now, data)
        return data

    def _read_turns(self) -> dict:
        path = paths.turns_log()
        try:
            size = path.stat().st_size
        except OSError:
            return {"ledger": None, "tool_results": None, "turn_errors": None}
        floor = self._clock() - LEDGER_DAYS * 86400
        batch = ledger_mod.read_rows(path, max(0, size - TAIL_BYTES))
        rows = [r for r, _ in batch.rows if r["t"] >= floor][-LEDGER_ROWS:]
        results, errors = [], []
        models = [r for r in rows if r.get("kind") is None]
        for row in models[-LOG_TURNS:]:
            names: dict = {}
            for ev in ledger_mod.read_turn_log(self._log_of(row), kinds=("tool", "tool_result", "error")):
                t = _num(ev.get("t")) or row["t"]
                if ev["kind"] == "tool":
                    names[ev.get("id")] = ev.get("name")
                elif ev["kind"] == "tool_result":
                    name = names.get(ev.get("id"))
                    # his shell's failures are not ours
                    if isinstance(name, str) and name.startswith(OS_TOOLS):
                        results.append({"turn": row["id"], "tool": name, "ok": not ev.get("error"), "t": t,
                                        "text": str(ev.get("output") or "")[:TOOL_TEXT]})
                elif ev["kind"] == "error":
                    errors.append({"turn": row["id"], "t": t, "text": str(ev.get("text") or "")[:400]})
        return {"ledger": rows, "tool_results": results, "turn_errors": errors}

    @staticmethod
    def _log_of(row: dict) -> Path:
        """A turn's log: by its id under the state directory, else where the row says when that is also a
        turns folder (a row is text that was written once; it is not trusted to name any file)."""
        logs = paths.state_dir() / "turns"
        if re.fullmatch(r"[\w.-]+", str(row.get("id", ""))) and (logs / f"{row['id']}.jsonl").exists():
            return logs / f"{row['id']}.jsonl"
        said = Path(str(row.get("details") or ""))
        return said if said.parent.name == "turns" and said.suffix == ".jsonl" else logs / "none"

    @_safe
    def ledger(self):
        return self._turn_data()["ledger"]

    @_safe
    def tool_results(self):
        return self._turn_data()["tool_results"]

    @_safe
    def turn_errors(self):
        return self._turn_data()["turn_errors"]

    def _group_data(self) -> tuple | None:
        """(turn id -> group, group -> usual seconds), read from loop.db, which only the store writes."""
        now = time.monotonic()
        if self._requests is not None and now - self._requests[0] < TURN_CACHE:
            return self._requests[1]
        found = None
        path = paths.loop_db()
        if path.exists():
            conn = None
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
                rows = conn.execute("SELECT id, grp, seconds FROM requests WHERE grp IS NOT NULL AND ok = 1 "
                                    "AND stopped = 0 AND seconds > 0 ORDER BY t DESC LIMIT 3000").fetchall()
                groups = {i: g for i, g, _ in rows}
                seconds: dict[str, list] = {}
                for _, g, s in rows:
                    seconds.setdefault(g, []).append(s)
                found = groups, {g: statistics.median(v) for g, v in seconds.items() if len(v) >= MEDIAN_MIN}
            except sqlite3.Error:
                found = None     # no counts yet, or another version of the tables
            finally:
                if conn is not None:
                    conn.close()
        self._requests = (now, found)
        return found

    @_safe
    def groups(self):
        found = self._group_data()
        return found[0] if found else None

    @_safe
    def medians(self):
        found = self._group_data()
        return found[1] if found else None

    # apps

    @_safe
    def apps(self):
        """Each app that wrote a status file in the last few days: {"name", "ok", "running", "log"}."""
        root = paths.state_dir() / "apps"
        if not root.exists():
            return []
        floor, out = self._clock() - APP_DAYS * 86400, []
        for status in sorted(root.glob("*.status.json")):
            name = status.name.removesuffix(".status.json")
            try:
                if status.stat().st_mtime < floor:
                    continue
            except OSError:
                continue
            data = _read_json(status)
            if data is None:
                continue
            out.append({"name": name, "ok": data["ok"] if isinstance(data.get("ok"), bool) else None,
                        "running": _alive(data.get("pid")), "log": tail_lines(root / f"{name}.log")})
        return out


def _epoch(v) -> float | None:
    """A coredump's time as epoch seconds: coredumpctl gives microseconds, older versions fewer."""
    n = _num(v)
    if n is None or n <= 0:
        return None
    return n / 1e6 if n > 1e14 else n / 1e3 if n > 1e11 else n


def _alive(pid) -> bool:
    """Is that process still there, and still a Bombadil app (a pid can be given to another program)?"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return b"bombadil-app" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True      # no /proc to ask: the process is there


# -- is he away --

class Presence:
    """Away is no prompt for ten minutes, or the screen locked (hyprlock running). The last prompt is
    read from what the machine already writes: the turn ledger and the newest turn log, the bar's last
    summon, and failing those the moment agentd started. With nothing to go on he is not away."""

    def __init__(self, idle: float = AWAY_AFTER, locked: Callable[[], bool] | None = None):
        self.idle = idle
        self._locked = locked or self._hyprlock

    @staticmethod
    def _hyprlock() -> bool:
        done = versions_mod.capture(["pgrep", "-x", "hyprlock"], timeout=2)
        return done is not None and done.code == 0

    def locked(self) -> bool:
        try:
            return bool(self._locked())
        except Exception:  # noqa: BLE001
            return False

    def last_active(self, now: float | None = None) -> float | None:
        now = time.time() if now is None else now
        stamps: list[float] = []
        try:
            stamps.append(paths.turns_log().stat().st_mtime)
        except OSError:
            pass
        try:
            with os.scandir(paths.state_dir() / "turns") as it:
                newest = max(e.name for e in it if e.name.endswith(".jsonl"))   # named by the ms it began
            stamps.append((paths.state_dir() / "turns" / newest).stat().st_mtime)
        except (OSError, ValueError):
            pass
        summons = [_num(r.get("t")) for r in read_events(now=now) or [] if r.get("kind") == "summon"]
        stamps += [t for t in summons if t is not None]
        started = _num((read_agentd() or {}).get("started"))
        if started is not None:
            stamps.append(started)
        return max((t for t in stamps if t <= now), default=None)

    def away(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.locked():
            return True
        last = self.last_active(now)
        return last is not None and now - last >= self.idle


_PRESENCE = Presence()
presence = _PRESENCE       # runner.presence.away(now)


# -- Hyprland's events --

def socket2_path() -> Path | None:
    sig = find_instance()
    return hypr_dir() / sig / ".socket2.sock" if sig else None


class EventStream:
    """Hyprland's event socket: one line per event, `name>>data`. `poll()` reads what has come, waits
    at most `timeout` seconds, and answers a list of (name, data). A compositor that goes away, or
    is not there yet, is asked for again after a growing pause (`backoff`, in seconds); nothing raises."""

    def __init__(self, path: Path | str | None = None, backoff: tuple = (0.5, 1, 2, 5, 10, 30),
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.path = Path(path) if path is not None else None
        self.backoff = backoff
        self._clock, self._sleep = clock, sleep
        self._sock: socket.socket | None = None
        self._buf = b""
        self._failures = 0
        self._retry_at = 0.0

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock, self._buf = None, b""

    def _lost(self) -> None:
        self.close()
        self._retry_at = self._clock() + self.backoff[min(self._failures, len(self.backoff) - 1)]
        self._failures += 1

    def _connect(self) -> bool:
        path = self.path or socket2_path()
        if path is None:
            self._lost()
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(2.0)
            sock.connect(str(path))
        except OSError:
            sock.close()
            self._lost()
            return False
        self._sock, self._buf = sock, b""
        return True

    def poll(self, timeout: float = 1.0) -> list[tuple[str, str]]:
        if self._sock is None:
            left = self._retry_at - self._clock()
            if left > 0:
                self._sleep(min(timeout, left))
                return []
            if not self._connect():
                return []
        try:
            self._sock.settimeout(timeout)
            chunk = self._sock.recv(65536)
        except TimeoutError:
            return []
        except OSError:
            self._lost()
            return []
        if not chunk:       # Hyprland closed the socket
            self._lost()
            return []
        self._failures = 0
        *lines, self._buf = (self._buf + chunk).split(b"\n")
        if len(self._buf) > (1 << 20):
            self._buf = b""
        out = []
        for raw in lines:
            name, sep, data = raw.decode(errors="replace").partition(">>")
            if sep and name:
                out.append((name, data))
        return out


# -- the read-only smoke checks --

def _canaries() -> list[Path]:
    """Apps known to load: the app template, and any app folder in share/canary."""
    found: list[Path] = []
    for base in (paths.share_dir() / "share", _ROOT / "share"):
        for d in [base / "app-template", *sorted((base / "canary").glob("*"))]:
            if (d / "main.qml").exists() and d.name not in {f.name for f in found}:
                found.append(d)
    return found


def _os_mcp_tools(command: list[str] | None = None, timeout: float = OS_MCP_TIMEOUT) -> tuple[bool, str]:
    """Start os-mcp, say hello and ask for its tools over stdio JSON-RPC, as the agent does."""
    cmd = command or [shutil.which("bombadil-os-mcp") or str(_ROOT / "bin" / "bombadil-os-mcp")]
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    done = versions_mod.capture(cmd, timeout, input=json.dumps(hello) + "\n" + json.dumps(tools) + "\n")
    if done is None:
        return False, f"os-mcp did not list its tools within {timeout:g} s"
    for line in done.out.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        found = msg.get("result", {}).get("tools") if isinstance(msg, dict) and msg.get("id") == 2 else None
        if isinstance(found, list):
            return bool(found), f"{len(found)} tools" if found else "os-mcp lists no tools"
    return False, f"os-mcp exited ({done.code}) without listing its tools"


def _canary_apps(timeout: float = CANARY_TIMEOUT) -> tuple[bool, str, bool]:
    """(ok, detail, skipped): `bombadil-app check` on each canary. Skipped when the kit has no check yet."""
    exe = shutil.which("bombadil-app") or str(_ROOT / "bin" / "bombadil-app")
    has = versions_mod.capture([exe, "check", "--help"], timeout=15)
    if has is None or has.code != 0:
        return True, "skipped: this kit has no `bombadil-app check` yet", True
    apps = _canaries()
    if not apps:
        return True, "skipped: there is no canary app to load", True
    bad = []
    for app in apps:
        done = versions_mod.capture([exe, "check", str(app), "--wait", "800"], timeout)
        try:
            said = json.loads(done.out) if done is not None else None
        except ValueError:
            said = None
        if not (isinstance(said, dict) and said.get("ok") is True):
            errors = said.get("errors") if isinstance(said, dict) else None
            why = str(errors[0]).splitlines()[0][:120] if errors else "it did not load"
            bad.append(f"{app.name}: {why}")
    return (not bad), ("; ".join(bad) if bad else f"{len(apps)} loaded"), False


def doctor_live(collectors=None, *, os_mcp: Callable | None = None,
                canary: Callable | None = None) -> list[dict]:
    """bombadil-smoke's read-only checks, run as the user: [{"name", "ok", "detail"}]. A check that does
    not apply here is ok with "skipped: ..." in its detail and "skipped": True. Checks are the agentd
    ping (a real connection), the bar layer, hyprctl's config errors, failed Bombadil units, os-mcp
    listing its tools within 20 s, and the canary apps loading. Takes up to a minute; never raises."""
    collectors = collectors if collectors is not None else Collectors()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, skipped: bool = False):
        check = {"name": name, "ok": bool(ok), "detail": detail}
        checks.append({**check, "skipped": True} if skipped else check)

    def ask(field: str):
        fn = collectors.get(field) if isinstance(collectors, dict) else getattr(collectors, field, None)
        try:
            return fn() if callable(fn) else None
        except Exception:  # noqa: BLE001
            return None

    live = ask("agentd")
    if isinstance(live, dict) and live.get("ponged"):
        add("agentd-ping", True, f"answered in {round((live.get('latency') or 0) * 1000)} ms")
    else:
        add("agentd-ping", False, (live or {}).get("error") or "agentd did not answer")
    for name, field in (("bar-layer", "layers"), ("hypr-config", "configerrors")):
        seen = ask(field)
        if seen is None:
            add(name, False, "hyprctl did not answer")
            continue
        r = run_probe(name, Observation(**{field: seen}))[0]
        add(name, r.ok is True, r.observed if r.ok is not True else "ok")
    units = ask("failed_units")
    if units is None:
        add("units", True, "skipped: there is no user manager to ask", True)
    else:
        add("units", not units, "no failed unit" if not units else "failed: " + ", ".join(units))
    try:
        ok, detail = (os_mcp or _os_mcp_tools)()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"os-mcp check failed: {type(e).__name__}"
    add("os-mcp", ok, detail)
    try:
        ok, detail, skipped = (canary or _canary_apps)()
    except Exception as e:  # noqa: BLE001
        ok, detail, skipped = False, f"canary check failed: {type(e).__name__}", False
    add("canary-apps", ok, detail, skipped)
    return checks


# Doctor checks no probe covers become findings of their own (the others are the probes' own).
_DOCTOR_FINDINGS = {
    "os-mcp": ("os-mcp", "os-mcp lists its tools within 20 s", "The OS tools did not start when checked."),
    "canary-apps": ("apps", "the canary apps load", "A canary app did not load."),
}


# -- the runner --

def _unique(found: Iterable[Finding]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for f in found:
        seen[f.fp] = f
    return list(seen.values())


class Runner:
    """Looks, and keeps what counts. `collectors` is a `Collectors` or a dict {field: callable}; `store`
    a `FindingsStore` (opened on first use when none); `versions` a dict, or a callable giving one
    (`versions.collect` when none). `clock` and `sleep` are for tests."""

    def __init__(self, collectors=None, store: FindingsStore | None = None, versions=None, *,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 presence: Presence | None = None):
        self.collectors = collectors if collectors is not None else Collectors(clock=clock)
        self._store_arg, self._own_store = store, store is None
        self._versions_arg = versions
        self._clock, self._sleep = clock, sleep
        self.presence = presence if presence is not None else _PRESENCE
        self.details_at: float | None = None        # when the drawer last opened, from Hyprland's events
        self._due: list[tuple[float, tuple]] = []
        self._last_run = 0.0
        self._asked, self._away = float("-inf"), False    # when presence was last asked, and what it said
        self._last_doctor = _num((_read_json(paths.loop_dir() / "doctor.json") or {}).get("t")) or 0.0
        self._versions: dict | None = None
        self.last_doctor: list[dict] = []

    # plumbing

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def _store(self) -> FindingsStore | None:
        if self._store_arg is None:
            try:
                self._store_arg = FindingsStore()
            except Exception as e:  # noqa: BLE001 - loop.db will not open: nothing can be kept
                _complain(f"loop.db will not open: {type(e).__name__}: {e}")
                return None
        return self._store_arg

    def close(self) -> None:
        if self._own_store and self._store_arg is not None:
            self._store_arg.close()
            self._store_arg = None

    def _collector(self, field: str):
        c = self.collectors
        fn = c.get(field) if isinstance(c, dict) else getattr(c, field, None)
        return fn if callable(fn) else None

    def _collect(self, field: str):
        fn = self._collector(field)
        if fn is None:
            return None
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            _complain(f"{field}: {type(e).__name__}: {e}")
            return None

    def _versions_now(self) -> dict | None:
        if self._versions is None:
            try:
                v = self._versions_arg if self._versions_arg is not None else versions_mod.collect
                self._versions = v() if callable(v) else dict(v)
            except Exception as e:  # noqa: BLE001
                _complain(f"versions: {type(e).__name__}: {e}")
                self._versions = {}
        return self._versions or None

    # looking

    def observe(self, fields: Iterable[str] | None = None, now: float | None = None) -> Observation:
        """One look at the machine: the collectors of `fields` (all of them when None), each bounded."""
        now = self._now(now)
        begin = getattr(self.collectors, "begin", None)
        if callable(begin):
            begin()
        values = {f: self._collect(f) for f in (COLLECTED if fields is None else fields) if f in COLLECTED}
        return Observation(**values, now=now, details_at=self.details_at,
                           agentd_started=_num(self._collect("agentd_started")))

    @staticmethod
    def _fields(chosen: Iterable[str]) -> list[str]:
        need = set(ALWAYS)
        for pid in chosen:
            need |= set(PROBES[pid].needs)
        return [f for f in COLLECTED if f in need]

    def _chosen(self, ids, store) -> list[str]:
        skip = store.quarantined(self._clock()) if store is not None else set()
        return [p for p in PROBES if (ids is None or p in ids) and p not in skip]

    def check(self, ids: Iterable[str] | None = None, now: float | None = None) -> list[Result]:
        """What the probes say right now, each red invariant looked at again, nothing recorded: what
        `bombadil probe` prints. A red invariant is answered by what its second look said."""
        now = self._now(now)
        self._refresh()
        chosen = self._chosen(set(ids) if ids is not None else None, None)
        if not chosen:
            return []
        obs = self.observe(self._fields(chosen), now)
        results = run_all(obs, only=chosen)
        again, _ = self._second_look(results, now)
        out: list[Result] = []
        answered: set[str] = set()
        for r in results:
            if r.ok is False and r.retry_after is not None and r.id in again:
                if r.id not in answered:     # the second look speaks once for the probe
                    answered.add(r.id)
                    out += again[r.id]
            else:
                out.append(r)
        return out

    def _refresh(self) -> None:
        refresh = getattr(self.collectors, "refresh", None)
        if callable(refresh):
            refresh()

    def _second_look(self, results: list[Result],
                     now: float) -> tuple[dict[str, list[Result]], Observation | None]:
        """The invariants that are red look again, once, `retry_after` later: ({probe id: what it says
        now}, the look that was taken). One wait serves all of them."""
        first = {r.id: r for r in results if r.ok is False and r.retry_after is not None}
        if not first:
            return {}, None
        self._sleep(max(r.retry_after for r in first.values()))
        fresh = self.observe(self._fields(first), now)
        return {pid: run_probe(pid, fresh, retried=True) for pid in first}, fresh

    # keeping

    def run_once(self, ids: Iterable[str] | None = None, now: float | None = None, *,
                 fresh: bool | None = None) -> list[Finding]:
        """Run the probes (all, or `ids`), look again 500 ms later at each invariant that is red, and
        record what counts. Returns the findings that got a new sighting. Never raises. `fresh` forgets
        what the collectors keep between looks (the default when `ids` is given)."""
        wanted = None if ids is None else set(ids)
        try:
            return self._run_once(wanted, self._now(now), (wanted is not None) if fresh is None else fresh)
        except Exception as e:  # noqa: BLE001 - the loop costs a turn nothing, and itself one line
            _complain(f"run_once: {type(e).__name__}: {e}")
            return []

    def _run_once(self, ids: set | None, now: float, fresh: bool) -> list[Finding]:
        store = self._store()
        chosen = self._chosen(ids, store) if store is not None else []
        if not chosen:
            return []
        if fresh:
            self._refresh()
        obs = self.observe(self._fields(chosen), now)
        results = run_all(obs, only=chosen)
        changed: list[Finding] = []
        for r in results:
            if r.ok is False and r.retry_after is None:
                changed.append(store.record(r, now, **self._context(r, obs, now, store)))
        again, looked = self._second_look(results, now)
        for pid, second in again.items():
            first = next(r for r in results if r.id == pid and r.ok is False and r.retry_after is not None)
            changed.append(store.record_retry(first, second, now, **self._context(first, looked, now, store)))
        found = _unique(f for f in changed if f is not None)
        self._words(found, obs, store)
        return found

    def _context(self, r: Result, obs: Observation, now: float, store: FindingsStore) -> dict:
        """What goes into the evidence besides the result: the observation, versions, and (only when
        the store will write a new bundle) the log lines and the turn."""
        ctx: dict = {"obs": obs, "versions": self._versions_now()}
        if not self._new_sighting(r, now, store):
            return ctx
        ctx["log"] = self._log_for(r, obs)
        row = self._turn_of(r, obs)
        if row is not None:
            ctx["turn"] = row
            ctx["tools"] = [{"name": t["tool"], "ok": t["ok"], "error": "" if t["ok"] else t["text"]}
                            for t in obs.tool_results or [] if t.get("turn") == row.get("id")]
        return ctx

    @staticmethod
    def _new_sighting(r: Result, now: float, store: FindingsStore) -> bool:
        """Would the store count this as a new sighting? A state seen again within the episode, and a
        past fact already written down, are not worth collecting logs for."""
        times = store.sighting_times(fingerprint_of(r))
        if r.at is not None:
            return not any(abs(t - r.at) < 0.002 for t in times)
        return not times or now - max(times) >= SAME_EPISODE

    @staticmethod
    def _turn_of(r: Result, obs: Observation) -> dict | None:
        """The ledger row a past fact is about: the one written at that moment, else the turn that was
        running then."""
        if r.at is None or not isinstance(obs.ledger, list):
            return None
        rows = [x for x in obs.ledger if isinstance(x, dict) and x.get("kind") is None]
        return next((x for x in rows if abs(x["t"] - r.at) < 0.002), None) or next(
            (x for x in rows if (_num(x.get("started")) or x["t"]) <= r.at <= x["t"]), None)

    def _log_for(self, r: Result, obs: Observation) -> list[str]:
        """The last lines of the log of what the finding is about; [] when it has none."""
        try:
            if r.component == "hypr":
                sig = find_instance()
                return tail_lines(hypr_dir() / sig / "hyprland.log") if sig else []
            unit = {"agentd": "bombadil-agentd", "bar": "bombadil-shell"}.get(r.component)
            if unit:
                cmd = ["journalctl", "--user", "-u", f"{unit}.service", "-n", str(LOG_LINES), "--no-pager",
                       "-o", "cat"]
                done = versions_mod.capture(cmd, timeout=3)
                return done.out.splitlines()[-LOG_LINES:] if done is not None and done.code == 0 else []
            if r.component == "apps":
                apps = [a for a in obs.apps or [] if isinstance(a, dict) and a.get("log")]
                worst = [a for a in apps if a.get("ok") is False] or apps
                return [str(x) for x in worst[0]["log"]][-LOG_LINES:] if worst else []
        except Exception as e:  # noqa: BLE001
            _complain(f"log for {r.component}: {type(e).__name__}: {e}")
        return []

    def _words(self, changed: list[Finding], obs: Observation, store: FindingsStore) -> None:
        """His words of trouble ("still", "won't") from within five minutes of a finding: only those words."""
        if not changed:
            return
        rows = obs.ledger if isinstance(obs.ledger, list) else self._collect("ledger")
        prompts = [(r["t"], r["prompt"]) for r in rows or [] if isinstance(r, dict) and r.get("kind") is None
                   and isinstance(r.get("prompt"), str) and _num(r.get("t")) is not None]
        if prompts:
            for f in changed:
                store.add_words(f.fp, prompts)

    # events and time

    def run_event(self, name: str, data: str = "", now: float | None = None) -> list[Finding]:
        """A Hyprland event. A window that opens, closes or moves a panel schedules the layout checks
        a second on, when it has been placed; the drawer opening schedules its own check two seconds on,
        when its grace is over. Returns what `tick` finds that is due by now."""
        now = self._now(now)
        if name not in WINDOW_EVENTS:
            return []
        fields = data.split(",", 3)
        if (name == "openwindow" and len(fields) > 2 and fields[2] == "bombadil-details") or (
                name == "activespecial" and fields[0] == "special:details"):
            self.details_at = now
            self._later(now + DRAWER_CHECK, ("drawer-focus",))
        self._later(now + SETTLE, WINDOW_PROBES)
        return self.tick(now)

    def _later(self, at: float, ids: tuple) -> None:
        if not any(set(ids) <= set(i) and abs(a - at) < 0.3 for a, i in self._due) and len(self._due) < 50:
            self._due.append((at, ids))

    def tick(self, now: float | None = None) -> list[Finding]:
        """Whatever is due: checks that events scheduled, then, while he is away, the run every
        minute and the doctor every day."""
        now = self._now(now)
        due = [d for d in self._due if d[0] <= now]
        self._due = [d for d in self._due if d[0] > now]
        changed: list[Finding] = []
        if due:
            changed += self.run_once({i for _, ids in due for i in ids}, now, fresh=False)
        if self._is_away(now):
            if now - self._last_run >= PERIOD:
                self._last_run = now
                changed += self.run_once(None, now, fresh=False)
            if now - self._last_doctor >= DOCTOR_EVERY:
                changed += self.doctor(now)
        return _unique(changed)

    def _is_away(self, now: float) -> bool:
        """Presence costs a few reads and a process: asked when a run could be due, not every second."""
        if now - self._last_run < PERIOD and now - self._last_doctor < DOCTOR_EVERY:
            return False
        if now - self._asked >= PRESENCE_EVERY or now < self._asked:
            self._asked, self._away = now, self.presence.away(now)
        return self._away

    def next_due(self) -> float | None:
        return min((a for a, _ in self._due), default=None)

    def doctor(self, now: float | None = None) -> list[Finding]:
        """The idle doctor: doctor_live() on this machine, written to doctor.json, and the checks no
        probe covers (os-mcp, the canary apps) kept as findings when they fail."""
        now = self._now(now)
        self._last_doctor = now
        try:
            self.last_doctor = checks = doctor_live(self.collectors)
            write_json_atomic(paths.loop_dir() / "doctor.json", {"t": now, "checks": checks})
            store = self._store()
            found: list[Finding] = []
            for check in checks:
                if check["ok"] or check["name"] not in _DOCTOR_FINDINGS or store is None:
                    continue
                component, expected, title = _DOCTOR_FINDINGS[check["name"]]
                seen = (check["detail"].splitlines() or [""])[0][:160]
                r = Result(False, "doctor", component, check["name"], expected, seen, kind="event",
                           title=title)
                got = store.record(r, now, versions=self._versions_now())
                if got is not None:
                    found.append(got)
            return found
        except Exception as e:  # noqa: BLE001
            _complain(f"doctor: {type(e).__name__}: {e}")
            return []

    def serve(self, stop: threading.Event | None = None, stream: EventStream | None = None) -> None:
        """The prober's loop: Hyprland's events as they come, and `tick` every second or so. Returns
        when `stop` is set. One bad event or run costs a line, never the loop."""
        stop = stop or threading.Event()
        stream = stream or EventStream()
        while not stop.is_set():
            try:
                for name, data in stream.poll(1.0):
                    self.run_event(name, data)
                self.tick()
            except Exception as e:  # noqa: BLE001
                _complain(f"serve: {type(e).__name__}: {e}")
                stop.wait(1.0)
        stream.close()


def main(argv: list[str] | None = None) -> int:
    """`python -m bombadil.loop.runner`: the prober, until it is told to stop."""
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    adopt_instance()
    runner = Runner()
    try:
        runner.serve(stop)
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""`Processes`: a live, sorted, filtered process list read straight from /proc.

Each refresh reads one small file per process (`/proc/<pid>/stat`); the command line and
owner are read once per process and cached, so a few hundred processes every two seconds
cost a few milliseconds. The first read happens when the object is made, so `list` is
filled before any `Component.onCompleted` (and in `check`'s first frame).
"""

import os
import pwd
import signal as signals

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import qmlRegisterType

from ..context import AppContext
from . import MAJOR, MINOR, URI
from . import context as native_context
from .files import js_value
from .system import read

CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
STATES = {"R": "running", "S": "sleeping", "D": "waiting", "Z": "zombie", "T": "stopped", "t": "stopped",
          "I": "idle", "X": "dead", "P": "parked", "W": "paging"}
SIGNALS = {"TERM": signals.SIGTERM, "KILL": signals.SIGKILL, "STOP": signals.SIGSTOP,
           "CONT": signals.SIGCONT, "INT": signals.SIGINT, "HUP": signals.SIGHUP}

_users: dict[int, str] = {}


def user_name(uid: int) -> str:
    if uid not in _users:
        try:
            _users[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            _users[uid] = str(uid)
    return _users[uid]


def boot_time() -> float:
    for line in read("/proc/stat").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    return 0.0


def mem_total() -> int:
    for line in read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return 0


def uptime() -> float:
    up = read("/proc/uptime").split()
    return float(up[0]) if up else 0.0


def parse_stat(raw: bytes) -> dict:
    """The fields of /proc/<pid>/stat we use. The name sits in parentheses and may hold spaces."""
    left, right = raw.find(b"("), raw.rfind(b")")
    f = raw[right + 2:].split()
    return {"comm": raw[left + 1:right].decode(errors="replace"), "state": f[0].decode(),
            "ppid": int(f[1]), "ticks": int(f[11]) + int(f[12]), "threads": int(f[17]),
            "start": int(f[19]), "rss": int(f[21]) * PAGE_SIZE}


def identity(pid: int, comm: str) -> tuple[str, str, str]:
    """(name, command, user). Names longer than 15 characters come from argv[0] instead of comm."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        argv = []
    try:
        user = user_name(os.stat(f"/proc/{pid}").st_uid)
    except OSError:
        user = ""
    name = comm
    if argv and len(comm) >= 15:
        base = os.path.basename(argv[0].split(" ")[0])
        if base.startswith(comm):
            name = base
    return name, " ".join(argv) if argv else f"[{comm}]", user


def smaps_rollup(pid: int) -> dict[str, int] | None:
    text = read(f"/proc/{pid}/smaps_rollup")
    if not text:
        return None
    out = {}
    for line in text.splitlines()[1:]:
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[key] = int(parts[0]) * 1024
    return out


def details(pid: int) -> dict | None:
    status = read(f"/proc/{pid}/status")
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            st = parse_stat(f.read())
    except (OSError, IndexError, ValueError):
        return None
    if not status:
        return None
    fields = {}
    for line in status.splitlines():
        key, _, rest = line.partition(":")
        fields[key] = rest.strip()
    name, command, user = identity(pid, st["comm"])
    rollup = smaps_rollup(pid)

    def link(what):
        try:
            return os.readlink(f"/proc/{pid}/{what}")
        except OSError:
            return None

    try:
        fds = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        fds = None
    oom = read(f"/proc/{pid}/oom_score").strip()
    rss = int(fields["VmRSS"].split()[0]) * 1024 if "VmRSS" in fields else st["rss"]
    return {
        "pid": pid, "ppid": st["ppid"], "name": name, "command": command, "exe": link("exe"),
        "cwd": link("cwd"), "user": user, "state": STATES.get(st["state"], st["state"]), "rss": rss,
        "pss": rollup.get("Pss") if rollup else None,
        "uss": rollup.get("Private_Clean", 0) + rollup.get("Private_Dirty", 0) if rollup else None,
        "swap": rollup.get("Swap") if rollup else None,
        "shared": rollup.get("Shared_Clean", 0) + rollup.get("Shared_Dirty", 0) if rollup else None,
        "threads": st["threads"], "fds": fds, "started": round(boot_time() + st["start"] / CLK_TCK, 2),
        "oomScore": int(oom) if oom.lstrip("-").isdigit() else None,
    }


class Processes(QObject):
    intervalChanged = Signal()
    sortByChanged = Signal()
    descendingChanged = Signal()
    limitChanged = Signal()
    filterChanged = Signal()
    listChanged = Signal()
    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctx = native_context()
        self._sort_by = "memory"
        self._descending = True
        self._limit = 0
        self._filter = ""
        self._rows: list[dict] = []       # every process from the last scan
        self._list: list[dict] = []
        self._list_js = None
        self._count = 0
        self._prev: dict[tuple[int, int], int] = {}   # (pid, start) -> cpu ticks at the last scan
        self._prev_time = 0.0
        self._ids: dict[tuple[int, int], tuple[str, str, str]] = {}
        self._boot = boot_time()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        # Read now; QML then sets sortBy/filter/limit, and each re-sorts this reading. The timer
        # starts once those are set, so `interval: 0` never sees a tick.
        self.refresh()
        QTimer.singleShot(0, self, self._begin)

    @Slot()
    def _begin(self):
        if self._timer.interval() > 0:
            self._timer.start()

    @Slot()
    def refresh(self):
        now = uptime()
        total = mem_total() or 1
        elapsed = now - self._prev_time if self._prev_time else 0.0
        rows, ticks = [], {}
        try:
            pids = [int(n) for n in os.listdir("/proc") if n.isdigit()]
        except OSError:
            pids = []
        for pid in pids:
            try:
                with open(f"/proc/{pid}/stat", "rb") as f:
                    st = parse_stat(f.read())
            except (OSError, IndexError, ValueError):
                continue
            key = (pid, st["start"])
            ticks[key] = st["ticks"]
            if key in self._prev and elapsed > 0:
                cpu = (st["ticks"] - self._prev[key]) / CLK_TCK / elapsed
            else:  # new process: its average since it started
                age = now - st["start"] / CLK_TCK
                cpu = st["ticks"] / CLK_TCK / age if age > 0 else 0.0
            ident = self._ids.get(key) or identity(pid, st["comm"])
            self._ids[key] = ident
            rows.append({
                "pid": pid, "ppid": st["ppid"], "name": ident[0], "command": ident[1], "user": ident[2],
                "state": STATES.get(st["state"], st["state"]), "cpu": round(max(0.0, cpu), 4),
                "memory": st["rss"], "memoryPercent": round(st["rss"] * 100 / total, 2),
                "threads": st["threads"], "started": round(self._boot + st["start"] / CLK_TCK, 2),
            })
        self._prev, self._prev_time = ticks, now
        self._ids = {k: v for k, v in self._ids.items() if k in ticks}
        self._rows = rows
        self._publish()

    def _publish(self):
        rows = self._rows
        needle = self._filter.strip().lower()
        if needle:
            rows = [r for r in rows if needle in r["name"].lower() or needle in r["command"].lower()]
        key = self._sort_by if rows and self._sort_by in rows[0] else "memory"
        if key in ("name", "command", "user", "state"):
            rows = sorted(rows, key=lambda r: (r[key].lower(), r["pid"]), reverse=self._descending)
        else:
            rows = sorted(rows, key=lambda r: (r[key], r["pid"]), reverse=self._descending)
        count = len(rows)
        if self._limit > 0:
            rows = rows[:self._limit]
        if rows != self._list:
            self._list, self._list_js = rows, None
            self.listChanged.emit()
        if count != self._count:
            self._count = count
            self.countChanged.emit()

    @Slot(int, result="QVariant")
    def details(self, pid: int):
        return js_value(self, details(pid))

    def _get_list(self):
        if self._list_js is None:
            self._list_js = js_value(self, self._list)
        return self._list_js

    @Slot(int, result=bool)
    @Slot(int, str, result=bool)
    def kill(self, pid: int, signal: str = "TERM") -> bool:
        sig = SIGNALS.get(str(signal).upper().removeprefix("SIG"))
        if self._ctx.check or sig is None or pid <= 1:
            return False
        try:
            os.kill(pid, sig)
        except OSError:
            return False
        QTimer.singleShot(300, self, self.refresh)
        return True

    def _get_interval(self):
        return self._timer.interval()

    def _set_interval(self, ms: int):
        ms = max(0, int(ms))
        if 0 < ms < 100:
            ms = 100
        if ms == self._timer.interval():
            return
        self._timer.setInterval(ms)
        if ms == 0:
            self._timer.stop()
        elif self._prev_time:
            self._timer.start()
        self.intervalChanged.emit()

    interval = Property(int, _get_interval, _set_interval, notify=intervalChanged)

    def _setter(attr: str, signal_name: str, cast):
        def setter(self, value):
            value = cast(value)
            if getattr(self, attr) != value:
                setattr(self, attr, value)
                getattr(self, signal_name).emit()
                if self._prev_time:
                    self._publish()
        return setter

    sortBy = Property(str, lambda self: self._sort_by, _setter("_sort_by", "sortByChanged", str),
                      notify=sortByChanged)
    descending = Property(bool, lambda self: self._descending, _setter("_descending", "descendingChanged", bool),
                          notify=descendingChanged)
    limit = Property(int, lambda self: self._limit, _setter("_limit", "limitChanged", int), notify=limitChanged)
    filter = Property(str, lambda self: self._filter, _setter("_filter", "filterChanged", str),
                      notify=filterChanged)
    list = Property("QVariant", _get_list, notify=listChanged)
    count = Property(int, lambda self: self._count, notify=countChanged)
    del _setter


def register(ctx: AppContext) -> None:
    qmlRegisterType(Processes, URI, MAJOR, MINOR, "Processes")

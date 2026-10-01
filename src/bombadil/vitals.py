"""The Machine widget's numbers: how the machine is doing, who is using it, and when that is worth saying.

Everything here is a small text file the kernel keeps (/proc, /sys, the user's cgroup tree), and every
read, directory listing, statvfs and clock is handed in, so tests give it text and a fake tree and
nothing touches the machine. Any source may be missing (no cgroup v2, no memory controller, no thermal
zone, no /proc/net/dev interface, no systemd): that reading is then None and the card says less. No
function here raises on a file it cannot make sense of.

`Sampler` takes the readings. `Vitals` keeps four lines (the sessions' memory ceiling, memory, the disk,
heat), each of which takes a few samples over it to be crossed and more under a lower mark to be left, so
a reading that wobbles around its line does not make the card flicker, and builds the one `machine`
message the shell draws. agentd owns the loop: it calls `tick()` in a worker thread, waits
`next_delay()`, and sends what `tick()` returns; nothing runs while no shell is connected.
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass, replace

from .procs import CGROUP_ROOT

CALM_EVERY = 5.0         # seconds between samples while nothing is up and nothing is close
FAST_EVERY = 1.0         # ...while the card is up, asked for, or a reading is near its line
SLOW_EVERY = 30.0        # the disk, the list of sensors and the cgroup walk, when calm
ASK_FOR = 30.0           # how long "how's the machine" keeps the card up
ENTER_SAMPLES = 3        # samples in a row over a line to cross it
LEAVE_SAMPLES = 10       # ...and in a row under its lower mark to come back
MIN_UP = 10.0            # seconds a line stays up once it crossed
NEAR = 0.05              # "near" a line: within five points of it (five degrees for heat)
NEAR_DEGREES = 5.0
NET_STALE = 3.0          # a rate is not worked out between two reads this far apart
MAX_DEPTH = 4            # directory levels walked under the user's manager
MAX_SENSORS = 24
MAX_SANE_C = 150.0       # ACPI zones with no sensor report 255000 and the like
DISK_RED = 0.97          # the disk row turns red here (sysmap's number); amber is the line itself
DEV_SLICE = "bombadil-dev.slice"

# Fractions of the whole, and degrees for heat: where a line is crossed, and where it is left.
RULES = (
    ("sessions", 0.90, 0.80, NEAR),
    ("memory", 0.90, 0.85, NEAR),
    ("disk", 0.90, 0.88, NEAR),
    ("heat", 80.0, 72.0, NEAR_DEGREES),
)
WHY = {
    "sessions": "Coding sessions are near their memory limit",
    "memory": "Memory is nearly full",
    "disk": "The disk is nearly full",
    "heat": "The processor is hot",
}
HOLDS = {
    "machine": "the machine uses most",
    "sessions": "sessions use most",
    "you": "your apps use most",
}

# Filesystems that are not disks: kernel views, memory, images, containers.
PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "ramfs", "rootfs", "cgroup", "cgroup2", "pstore",
    "bpf", "tracefs", "debugfs", "securityfs", "configfs", "fusectl", "hugetlbfs", "mqueue", "autofs",
    "binfmt_misc", "efivarfs", "nsfs", "overlay", "squashfs", "iso9660", "selinuxfs", "rpc_pipefs",
    "nfsd", "zram",
}
# Never statted: statvfs on a mount whose server is gone blocks, and the loop would stand still.
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "ncpfs", "afs", "ceph", "glusterfs", "9p", "davfs",
              "fuse.sshfs", "fuse.rclone", "fuse.davfs2", "fuse.s3fs", "fuse.gvfsd-fuse"}

# Interfaces whose bytes a real one already counted: loopback, containers' and virtual machines' plumbing,
# and the bridges, bonds and VLANs built on top of a card.
_NOT_TRAFFIC = re.compile(r"lo$|veth|docker|br-|br\d|virbr|vnet|cni|podman|bond\d|team\d|[^.]+\.\d+$")
_MACHINE_UNIT = re.compile(r"bombadil-(?:turn|job|timer)-")
_SESSIONS_UNIT = re.compile(r"bombadil-dev(?:[-.]|$)")
_TEMP_FILE = re.compile(r"temp\d+_input")


# -- reading text --

def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, ValueError):
        return None


def list_names(path: str) -> list[str] | None:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return None


def stat_fs(path: str):
    try:
        return os.statvfs(path)
    except OSError:
        return None


def _int(text) -> int | None:
    """A whole number the way the kernel writes one. Not int(): that takes "1_0" and other people's digits,
    and raises on a digit string of any length."""
    s = (text or "").strip() if isinstance(text, str) else ""
    return int(s) if re.fullmatch(r"[0-9]{1,20}", s) else None


# -- parsers: text in, a number or None out --

def parse_cpu(text: str | None) -> tuple[int, int] | None:
    """(busy, total) jiffies of the whole machine, from the "cpu" line of /proc/stat (not "cpu0")."""
    for line in (text or "").splitlines():
        fields = line.split()
        if fields[:1] != ["cpu"]:
            continue
        v = [_int(x) for x in fields[1:9]]       # guest time is already in user and nice
        if len(v) < 4 or None in v:
            return None
        idle = v[3] + (v[4] if len(v) > 4 else 0)       # idle + iowait
        total = sum(v)
        return total - idle, total
    return None


def cpu_share(before: tuple[int, int] | None, after: tuple[int, int] | None) -> float | None:
    """The share of the time between two reads the processor was busy, 0..1."""
    if before is None or after is None or after[1] <= before[1]:
        return None
    return min(1.0, max(0.0, (after[0] - before[0]) / (after[1] - before[1])))


def parse_meminfo(text: str | None) -> dict[str, int]:
    """Every /proc/meminfo field in bytes (HugePages_* are counts); lines that are not one are skipped."""
    out = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        n = _int(parts[0]) if parts else None
        if n is not None and key.strip():
            out[key.strip()] = n * 1024 if parts[1:2] == ["kB"] else n
    return out


@dataclass(frozen=True)
class Memory:
    used: int
    total: int

    @property
    def fraction(self) -> float:
        return self.used / self.total


def memory_in_use(mi: dict[str, int]) -> Memory | None:
    """What the machine uses is what it could not give back: MemTotal minus MemAvailable. None when the file
    has no total, or says neither what is available nor what is free: that is a file we cannot read, not a
    full machine."""
    total = mi.get("MemTotal", 0)
    if total <= 0:
        return None
    if "MemAvailable" in mi:
        available = mi["MemAvailable"]
    elif "MemFree" in mi:
        available = mi["MemFree"] + mi.get("Buffers", 0) + mi.get("Cached", 0)     # before 3.14
    else:
        return None
    return Memory(min(total, max(0, total - available)), total)


def parse_net_dev(text: str | None) -> dict[str, tuple[int, int]]:
    """Interface -> (bytes received, bytes sent), for the ones that carry traffic of their own: not lo, and
    not the veth, docker, bridge, bond, VLAN and virtual-machine ports whose bytes a real interface counted
    too, which is why a sum over all of /proc/net/dev says twice what the network moved."""
    out = {}
    for line in (text or "").splitlines():
        name, sep, rest = line.partition(":")
        name, v = name.strip(), rest.split()
        if not sep or not name or _NOT_TRAFFIC.match(name) or len(v) < 9:
            continue
        rx, tx = _int(v[0]), _int(v[8])
        if rx is not None and tx is not None:
            out[name] = (rx, tx)
    return out


def parse_temp(text: str | None) -> float | None:
    """A thermal file's millidegrees as degrees. None for nothing, for zero or less (a sensor that has
    no reading says so that way) and for more than a chip can be."""
    s = (text or "").strip() if isinstance(text, str) else ""
    if not re.fullmatch(r"-?[0-9]{1,9}", s):
        return None
    c = int(s) / 1000
    return c if 0 < c <= MAX_SANE_C else None


def parse_limit(text: str | None) -> int | None:
    """memory.high and memory.max: a number of bytes, or "max" (no limit) which is None, as is 0."""
    return _int(text) or None


def parse_keyed(text: str | None) -> dict[str, int]:
    """memory.stat: "key number" per line."""
    out = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(" ")
        n = _int(rest)
        if key and n is not None:
            out[key] = n
    return out


def manager_cgroup(text: str | None, uid: int) -> str | None:
    """The user's systemd manager in the cgroup tree ("/user.slice/user-1000.slice/user@1000.service"),
    from /proc/self/cgroup: the path of this process up to that unit. A process outside it (a login
    session's own scope) gets the usual place. None without cgroup v2, which is the "0::" line."""
    for line in (text or "").splitlines():
        if line.startswith("0::"):
            m = re.match(rf"(.*?/user@{uid}\.service)(?:/|$)", line[3:].strip())
            return m.group(1) if m else f"/user.slice/user-{uid}.slice/user@{uid}.service"
    return None


def classify_unit(name: str) -> str | None:
    """"machine" for what a turn, a job or a timer runs in, "sessions" for the coding sessions' scopes and
    slices (bombadil-dev.slice, bombadil-dev-<project>.slice, bombadil-dev-<project>-<role>-<run>.scope),
    None for anything else."""
    if _MACHINE_UNIT.match(name):
        return "machine"
    if _SESSIONS_UNIT.match(name):
        return "sessions"
    return None


# -- the cgroup walk --

@dataclass(frozen=True)
class Ceiling:
    used: int
    limit: int

    @property
    def fraction(self) -> float:
        return self.used / self.limit


@dataclass(frozen=True)
class Cgroups:
    machine: int                      # bytes the turns, jobs and timers use
    sessions: int                     # bytes the coding sessions use
    ceiling: Ceiling | None = None    # the dev slice against its memory.high (else memory.max), if it has one
    dev: str | None = None            # where the dev slice is, so a calm sample can read just it


def unit_memory(read, path: str) -> int | None:
    """What a unit holds that is not just cache to give back: memory.current minus inactive_file, floored
    at 0. None when it has no memory.current (the controller is off there)."""
    current = _int(read(f"{path}/memory.current"))
    if current is None:
        return None
    return max(0, current - parse_keyed(read(f"{path}/memory.stat")).get("inactive_file", 0))


def ceiling_of(read, path: str) -> Ceiling | None:
    used = unit_memory(read, path)
    limit = parse_limit(read(f"{path}/memory.high")) or parse_limit(read(f"{path}/memory.max"))
    return Ceiling(used, limit) if used is not None and limit else None


def walk_units(listdir, manager: str, depth: int = MAX_DEPTH) -> list[tuple[str, str, str]]:
    """(kind, name, path) of the units of ours under `manager`, found at most `depth` levels down. Only a
    slice holds other units, so only slices are entered, and never one of ours: a sessions slice already
    holds its children."""
    out: list[tuple[str, str, str]] = []

    def visit(path: str, level: int):
        for name in listdir(path) or ():
            kind = classify_unit(name)
            if kind is not None:
                out.append((kind, name, f"{path}/{name}"))
            elif name.endswith(".slice") and level < depth:
                visit(f"{path}/{name}", level + 1)

    visit(manager, 1)
    return out


def read_cgroups(read, listdir, manager: str) -> Cgroups | None:
    """Who holds the memory, from the cgroup tree under `manager`. None when there is nothing to say: no
    such directory, or no memory controller in it."""
    if listdir(manager) is None or _int(read(f"{manager}/memory.current")) is None:
        return None
    machine = sessions = 0
    dev = None
    for kind, name, path in walk_units(listdir, manager):
        used = unit_memory(read, path)
        if used is None:
            continue
        if kind == "machine":
            machine += used
        else:
            sessions += used
        if name == DEV_SLICE:
            dev = path
    return Cgroups(machine, sessions, ceiling_of(read, dev) if dev else None, dev)


# -- disks --

@dataclass(frozen=True)
class Mount:
    device: str
    point: str
    fs: str


@dataclass(frozen=True)
class Disk:
    used: int
    total: int

    @property
    def fraction(self) -> float:
        return self.used / self.total


def _unescape(s: str) -> str:
    """/proc/mounts writes spaces and tabs in paths as octal escapes (\\040)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_mounts(text: str | None) -> list[Mount]:
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            out.append(Mount(_unescape(parts[0]), _unescape(parts[1]), parts[2]))
    return out


def _real(m: Mount) -> bool:
    if m.fs in PSEUDO_FS or m.fs in NETWORK_FS or m.device.startswith("//"):
        return False
    return not m.fs.startswith("fuse") or m.fs == "fuseblk"       # fuseblk: NTFS and exFAT disks


def fullest_disk(mounts: list[Mount], statvfs) -> Disk | None:
    """The fuller of / and /home. A path that is not a mount of its own is the other's, one on the same
    device (btrfs subvolumes) is counted once, and one that is not a real disk (memory, an image, a network
    share) is not looked at. Full is how much of what a person may write is written, as df says it: the
    blocks kept for root are not theirs."""
    last = {m.point: m for m in mounts}       # a later mount covers an earlier one
    seen, best = set(), None
    for point in ("/", "/home"):
        m = last.get(point)
        if m is None or not _real(m) or m.device in seen:
            continue
        seen.add(m.device)
        st = statvfs(point)
        if st is None:
            continue
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        total = used + st.f_bavail * st.f_frsize
        if total > 0 and used >= 0 and (best is None or used / total > best.fraction):
            best = Disk(used, total)
    return best


# -- the sampler --

@dataclass(frozen=True)
class Reading:
    """One sample. Whatever could not be read is None."""
    cpu: float | None = None                    # busy share since the sample before; None for the first
    memory: Memory | None = None
    cgroups: Cgroups | None = None
    disk: Disk | None = None
    heat: float | None = None                   # the hottest sensor, in degrees
    net: tuple[float, float] | None = None      # bytes a second (down, up); None until two samples agree


class Sampler:
    """Takes the readings. A calm sample is the processor, memory and the sensors, and the sessions' slice if
    the last walk found one; a fast one walks the cgroup tree and reads the network too. The disk, the sensor
    list and (when calm) the walk are redone every SLOW_EVERY seconds and carried between.
    `sample()` is called one at a time but may be from a different thread each time, so what is kept is
    on the object."""

    def __init__(self, read=read_text, listdir=list_names, statvfs=stat_fs, clock=time.monotonic,
                 uid=os.getuid, root=str(CGROUP_ROOT)):
        self._read, self._listdir, self._statvfs, self._clock = read, listdir, statvfs, clock
        self._uid, self._root = uid, root
        self.reset()

    def reset(self) -> None:
        """Forget everything, so the next sample does not stretch a rate over the time nobody was looking."""
        self._cpu_before: tuple[int, int] | None = None
        self._net_before: tuple[float, dict[str, tuple[int, int]]] | None = None
        self._disk: tuple[float, Disk | None] | None = None
        self._sensors: tuple[float, list[str]] | None = None
        self._walk: tuple[float, Cgroups | None] | None = None
        self._manager: str | None = None

    def sample(self, fast: bool = True) -> Reading:
        now = self._clock()
        return Reading(cpu=self._cpu(), memory=memory_in_use(parse_meminfo(self._read("/proc/meminfo"))),
                       cgroups=self._cgroups(now, fast), disk=self._disk_now(now), heat=self._heat(now),
                       net=self._net(now) if fast else None)

    def _cpu(self) -> float | None:
        now = parse_cpu(self._read("/proc/stat"))
        before, self._cpu_before = self._cpu_before, now or self._cpu_before
        return cpu_share(before, now)

    def _net(self, now: float) -> tuple[float, float] | None:
        counters = parse_net_dev(self._read("/proc/net/dev"))
        before, self._net_before = self._net_before, (now, counters)
        if before is None or not 0 < now - before[0] <= NET_STALE:
            return None
        # Per interface, so one that came or went (a cable, a phone) is not a burst, and a counter that
        # started over is not a negative rate.
        down = sum(max(0, c[0] - before[1][n][0]) for n, c in counters.items() if n in before[1])
        up = sum(max(0, c[1] - before[1][n][1]) for n, c in counters.items() if n in before[1])
        return down / (now - before[0]), up / (now - before[0])

    def _disk_now(self, now: float) -> Disk | None:
        if self._disk is None or now - self._disk[0] >= SLOW_EVERY:
            self._disk = (now, fullest_disk(parse_mounts(self._read("/proc/self/mounts")), self._statvfs))
        return self._disk[1]

    def _heat(self, now: float) -> float | None:
        if self._sensors is None or now - self._sensors[0] >= SLOW_EVERY:
            zones = [f"/sys/class/thermal/{n}/temp" for n in self._listdir("/sys/class/thermal") or ()
                     if n.startswith("thermal_zone")]
            # hwmon only when the zones have nothing to say
            self._sensors = (now, zones if self._hottest(zones) is not None else self._hwmon())
        return self._hottest(self._sensors[1])

    def _hwmon(self) -> list[str]:
        out: list[str] = []
        for chip in self._listdir("/sys/class/hwmon") or ():
            if chip.startswith("hwmon"):
                names = self._listdir(f"/sys/class/hwmon/{chip}") or ()
                out += [f"/sys/class/hwmon/{chip}/{n}" for n in names if _TEMP_FILE.fullmatch(n)]
        return out[:MAX_SENSORS]

    def _hottest(self, paths: list[str]) -> float | None:
        temps = [t for t in (parse_temp(self._read(p)) for p in paths[:MAX_SENSORS]) if t is not None]
        return max(temps) if temps else None

    def _cgroups(self, now: float, fast: bool) -> Cgroups | None:
        if fast or self._walk is None or now - self._walk[0] >= SLOW_EVERY:
            self._walk = (now, self._walked())
        elif self._walk[1] is not None and self._walk[1].dev:
            # Calm: not the walk, but the one slice that can say the sessions are near their limit.
            seen = self._walk[1]
            self._walk = (self._walk[0], replace(seen, ceiling=ceiling_of(self._read, seen.dev)))
        return self._walk[1]

    def _walked(self) -> Cgroups | None:
        if self._manager is None:
            self._manager = manager_cgroup(self._read("/proc/self/cgroup"), self._uid())
        if self._manager is None:
            return None
        return read_cgroups(self._read, self._listdir, self._root + self._manager)


# -- words and numbers for the card --

PARTS = ("machine", "sessions", "you")
UNITS = ("B", "kB", "MB", "GB", "TB")


def _pct(x: float) -> int:
    return int(x * 100 + 0.5)


def _deg(c: float) -> int:
    return int(c + 0.5)


def _frac(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 2)


def _unit(n: float) -> tuple[float, int]:
    """n in the largest unit it fills (thousands, as sysmap says them) and that unit's place in UNITS."""
    k = 0
    while n >= 999.5 and k < 4:      # 999.5 would be written 1000
        n /= 1000
        k += 1
    return n, k


def _digits(n: float, whole_from: float) -> str:
    """No decimals from `whole_from` up; below it one, without a trailing ".0"."""
    return f"{n:.0f}" if n >= whole_from else f"{n:.1f}".removesuffix(".0")


def size_text(used: float, total: float) -> str:
    """"14.5 of 16 GB": both in the unit the total suits."""
    if total <= 0:
        return "0 B"
    k = _unit(total)[1]
    return f"{_digits(used / 1000 ** k, 100)} of {_digits(total / 1000 ** k, 100)} {UNITS[k]}"


def rate_text(bytes_per_second: float) -> str:
    """"1.2 MB/s", "40 kB/s", "0 B/s"."""
    n, k = _unit(max(0.0, bytes_per_second))
    return f"{_digits(n, 10) if k else f'{n:.0f}'} {UNITS[k]}/s"


def holders(memory: Memory, cgroups: Cgroups) -> tuple[int, int, int]:
    """Bytes of the used memory that are the machine's, the sessions' and yours, in PARTS' order. A cgroup
    counts cache that is still warm and the kernel would give back; used does not, so when the two do not
    fit, the cgroups are cut to what is used, in proportion. What is left is yours: your apps and the
    system under them."""
    machine, sessions = cgroups.machine, cgroups.sessions
    if machine + sessions > memory.used:
        scale = memory.used / (machine + sessions)
        machine, sessions = int(machine * scale), int(sessions * scale)
    return machine, sessions, memory.used - machine - sessions


def memory_parts(memory: Memory, cgroups: Cgroups | None) -> list[dict]:
    """The stacked bar's parts, as fractions of the whole track. Without cgroups nobody can be told apart,
    so it is all "you". Each part ends where the running total, rounded, falls, so the rounded parts never
    add up to more than the track."""
    if cgroups is None:
        return [{"tone": "you", "fraction": _frac(memory.fraction)}]
    out, total, before = [], 0.0, 0.0
    for tone, held in zip(PARTS, holders(memory, cgroups), strict=True):
        total += held / memory.total
        cut = round(min(1.0, total), 2)
        out.append({"tone": tone, "fraction": round(cut - before, 2)})
        before = cut
    return out


# -- the lines --

class _Line:
    """One thing that can be wrong, and whether it is. It is crossed after ENTER_SAMPLES samples in a row
    at or over `enter`, and left after LEAVE_SAMPLES in a row at or under `leave` (a reading between the
    two starts the count again), but not before MIN_UP seconds after it was crossed. No reading counts
    as under: with nothing to measure, nothing is wrong."""

    def __init__(self, key: str, enter: float, leave: float, near: float):
        self.key, self.enter, self.leave, self.near = key, enter, leave, near
        self.up = False
        self.since = 0.0
        self.over = self.under = 0
        self.value = 0.0         # the last reading there was, for the words while it is being left

    def feed(self, value: float | None, now: float) -> None:
        self.value = self.value if value is None else value
        if not self.up:
            self.over = self.over + 1 if value is not None and value >= self.enter else 0
            if self.over >= ENTER_SAMPLES:
                self.up, self.since, self.under = True, now, 0
            return
        self.under = 0 if value is not None and value > self.leave else self.under + 1
        if self.under >= LEAVE_SAMPLES and now - self.since >= MIN_UP:
            self.up, self.over = False, 0

    def close(self, value: float | None) -> bool:
        return value is not None and value >= self.enter - self.near


def _strip_text(key: str, value: float) -> str:
    return f"hot · {_deg(value)}°" if key == "heat" else f"{key} {_pct(value)}%"


def _absent() -> dict:
    return {"type": "machine", "present": False, "asked": False, "why": "", "strip": {"text": "", "dot": ""},
            "rows": []}


class Vitals:
    """The lines and the card. One `tick()` is one sample; the caller waits `next_delay()` between them.
    The methods take a lock, so `ask()` and `message()` can come from the launcher's and the socket's
    threads while the loop's is sampling. `wall` is accepted for callers that hold both clocks: every
    interval here is on `clock`."""

    def __init__(self, sampler=None, clock=time.monotonic, wall=time.time):
        self._clock = clock
        self._sampler = sampler if sampler is not None else Sampler(clock=clock)
        self._lock = threading.RLock()
        self._lines = [_Line(*r) for r in RULES]
        self._reading: Reading | None = None
        self._asked_until = 0.0
        self._near = False
        self._sent: str | None = None

    def reset(self) -> None:
        """Forget everything: no shell is listening, so nothing that was true is any more."""
        with self._lock:
            self._lines = [_Line(*r) for r in RULES]
            self._reading, self._asked_until, self._near, self._sent = None, 0.0, False, None
            reset = getattr(self._sampler, "reset", None)
            if reset is not None:
                reset()

    def ask(self, seconds: float = ASK_FOR) -> None:
        """"How's the machine": the card is up for `seconds` even with no line crossed."""
        with self._lock:
            self._asked_until = max(self._asked_until, self._clock() + seconds)

    def next_delay(self) -> float:
        with self._lock:
            return FAST_EVERY if self._fast(self._clock()) else CALM_EVERY

    def message(self) -> dict:
        """The card as it is now, for a shell that has just connected."""
        with self._lock:
            return self._build(self._clock())

    def tick(self) -> dict | None:
        """Take one sample. The message when it is not the one the last tick returned (the first after a
        reset always is), else None: a calm machine says nothing."""
        with self._lock:
            now = self._clock()
            # The first sample reads everything, so what a client connecting sees is not half-known.
            self._reading = self._sampler.sample(self._reading is None or self._fast(now))
            self._advance(self._reading, now)
            message = self._build(now)
            sent = json.dumps(message, sort_keys=True)
            if sent == self._sent:
                return None
            self._sent = sent
            return message

    # -- the state --

    def _fast(self, now: float) -> bool:
        return self._near or now < self._asked_until or any(line.up for line in self._lines)

    @staticmethod
    def _values(r: Reading) -> dict[str, float | None]:
        ceiling = r.cgroups.ceiling if r.cgroups else None
        return {"sessions": ceiling.fraction if ceiling else None,
                "memory": r.memory.fraction if r.memory else None,
                "disk": r.disk.fraction if r.disk else None,
                "heat": r.heat}

    def _advance(self, r: Reading, now: float) -> None:
        values = self._values(r)
        self._near = False
        for line in self._lines:
            line.feed(values[line.key], now)
            self._near = self._near or line.close(values[line.key])

    # -- the message --

    def _build(self, now: float) -> dict:
        r = self._reading
        ups = [line for line in self._lines if line.up]
        asked = now < self._asked_until
        if r is None or not (ups or asked):
            return _absent()
        rows = [row for row in (self._memory_row(r, ups), self._disk_row(r, ups)) if row]
        rows += [self._cpu_row(r), self._net_row(r)]
        return {"type": "machine", "present": True, "asked": not ups, "why": self._why(ups, r),
                "strip": self._strip(ups, r), "rows": rows}

    def _strip(self, ups: list[_Line], r: Reading) -> dict:
        if ups:
            # The dot is the card's worst, whichever line the words are about.
            red = any(line.key == "disk" and line.value >= DISK_RED for line in ups)
            return {"text": _strip_text(ups[0].key, ups[0].value), "dot": "red" if red else "amber"}
        # Asked and nothing crossed: the reading nearest its line, and no dot, as nothing is wrong.
        values = self._values(r)
        read = [line for line in self._lines if values[line.key] is not None]
        near = max(read, key=lambda line: values[line.key] / line.enter, default=None)
        return {"text": _strip_text(near.key, values[near.key]) if near else "machine", "dot": ""}

    @staticmethod
    def _why(ups: list[_Line], r: Reading) -> str:
        """The worst line first (the lines are in that order), then the others, in lower case after it."""
        if not ups:
            return "The machine is fine"
        said: list[str] = []
        for line in ups:
            said.append(WHY[line.key] if not said else WHY[line.key][:1].lower() + WHY[line.key][1:])
            if line.key == "memory" and r.memory and r.cgroups:
                # who, when one holds half of what is used, or more
                held = dict(zip(PARTS, holders(r.memory, r.cgroups), strict=True))
                top = max(held, key=held.get)
                if held[top] * 2 >= r.memory.used > 0:
                    said.append(HOLDS[top])
        return " · ".join(said)

    @staticmethod
    def _memory_row(r: Reading, ups: list[_Line]) -> dict | None:
        if r.memory is None:
            return None
        return {"key": "memory", "kind": "stack", "title": "Memory", "meter": _frac(r.memory.fraction),
                "meterText": size_text(r.memory.used, r.memory.total),
                "tone": "amber" if any(line.key == "memory" for line in ups) else "you",
                "parts": memory_parts(r.memory, r.cgroups), "opens": ""}

    @staticmethod
    def _disk_row(r: Reading, ups: list[_Line]) -> dict | None:
        if r.disk is None:
            return None
        tone = ("red" if r.disk.fraction >= DISK_RED
                else "amber" if any(line.key == "disk" for line in ups) else "you")
        return {"key": "disk", "kind": "meter", "title": "Disk", "meter": _frac(r.disk.fraction),
                "meterText": size_text(r.disk.used, r.disk.total), "tone": tone, "opens": "disk"}

    @staticmethod
    def _cpu_row(r: Reading) -> dict:
        busy = f"{_deg(r.heat)}°" if r.heat is not None else ""     # the meter says how busy; the words say how hot
        return {"key": "cpu", "kind": "meter", "title": "Processor", "meter": _frac(r.cpu or 0.0),
                "meterText": busy, "tone": "you", "opens": ""}

    @staticmethod
    def _net_row(r: Reading) -> dict:
        down, up = r.net or (0.0, 0.0)
        return {"key": "net", "kind": "plain", "title": "Network", "tone": "you", "opens": "",
                "sub": f"↓ {rate_text(down)}   ↑ {rate_text(up)}"}

"""`System`: live machine state from /proc and /sys, as one bindable QML singleton.

No psutil: everything is a small text file the kernel already keeps. The singleton is made
(and starts polling) the first time an app's QML mentions `System`, so an app that never
looks at it costs nothing.
"""

import getpass
import glob
import os
import re
import socket
import time

from PySide6.QtCore import Property, QObject, QTimer, Signal
from PySide6.QtQml import qmlRegisterSingletonType

from ..context import AppContext
from . import MAJOR, MINOR, URI
from .files import js_value, to_js

# Filesystems that are not disks: kernel views, memory, images, containers.
PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "ramfs", "rootfs", "cgroup", "cgroup2", "pstore",
    "bpf", "tracefs", "debugfs", "securityfs", "configfs", "fusectl", "hugetlbfs", "mqueue", "autofs",
    "binfmt_misc", "efivarfs", "nsfs", "overlay", "squashfs", "iso9660", "selinuxfs", "rpc_pipefs",
    "nfsd", "zram",
}
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "fuse.sshfs"}


def read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def cpu_times() -> list[tuple[int, int]]:
    """(busy, total) jiffies for the whole machine, then each core."""
    out = []
    for line in read("/proc/stat").splitlines():
        if not line.startswith("cpu"):
            break
        v = [int(x) for x in line.split()[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)       # idle + iowait
        total = sum(v[:8])                                # guest time is already in user/nice
        out.append((total - idle, total))
    return out


def meminfo() -> dict[str, int]:
    """Every /proc/meminfo field; the kB ones in bytes (HugePages_* are counts)."""
    out = {}
    for line in read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        n = int(parts[0])
        out[key] = n * 1024 if len(parts) > 1 and parts[1] == "kB" else n
    return out


def memory(mi: dict[str, int]) -> dict:
    total = mi.get("MemTotal", 0)
    available = mi.get("MemAvailable", mi.get("MemFree", 0))
    return {
        "total": total,
        "used": total - available,
        "available": available,
        "free": mi.get("MemFree", 0),
        "cached": mi.get("Cached", 0) + mi.get("SReclaimable", 0),
        "buffers": mi.get("Buffers", 0),
        "shared": mi.get("Shmem", 0),
        "swapTotal": mi.get("SwapTotal", 0),
        "swapUsed": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0),
        "swapCached": mi.get("SwapCached", 0),
        "dirty": mi.get("Dirty", 0),
    }


def pressure() -> dict | None:
    """PSI "some avg10" (0..100) per resource; None when the kernel has no PSI."""
    if not os.path.isdir("/proc/pressure"):
        return None
    out = {}
    for res in ("cpu", "memory", "io"):
        m = re.search(r"^some avg10=([\d.]+)", read(f"/proc/pressure/{res}"), re.M)
        out[res] = float(m.group(1)) if m else None
    return out


def net_bytes() -> tuple[int, int]:
    rx = tx = 0
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() == "lo":
            continue
        v = rest.split()
        if len(v) >= 9:
            rx += int(v[0])
            tx += int(v[8])
    return rx, tx


def _unescape(s: str) -> str:
    """/proc/mounts writes spaces and tabs in paths as octal escapes (\\040)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def disks() -> list[dict]:
    seen: dict[str, dict] = {}
    for line in read("/proc/self/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fs = _unescape(parts[0]), _unescape(parts[1]), parts[2]
        if fs in PSEUDO_FS or (fs not in NETWORK_FS and (fs.startswith("fuse") or not device.startswith("/"))):
            continue
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        disk = {"mount": mount, "device": device, "fs": fs, "total": total,
                "used": total - st.f_bfree * st.f_frsize, "free": free}
        # btrfs subvolumes mount one device many times: keep its shortest mount point
        if device not in seen or len(mount) < len(seen[device]["mount"]):
            seen[device] = disk
    return sorted(seen.values(), key=lambda d: d["mount"])


def battery() -> dict:
    for supply in sorted(glob.glob("/sys/class/power_supply/*")):
        if read(f"{supply}/type").strip() != "Battery" or read(f"{supply}/present").strip() == "0":
            continue
        cap = read(f"{supply}/capacity").strip()
        status = read(f"{supply}/status").strip()
        return {"present": True, "percent": int(cap) if cap.isdigit() else None,
                "charging": status in ("Charging", "Full")}
    return {"present": False, "percent": None, "charging": False}


def temperature() -> float | None:
    """The hottest sensor in °C: thermal zones, or hwmon when there are none."""
    paths = glob.glob("/sys/class/thermal/thermal_zone*/temp") or glob.glob("/sys/class/hwmon/hwmon*/temp*_input")
    temps = []
    for p in paths:
        v = read(p).strip()
        if v.lstrip("-").isdigit() and int(v) > 0:
            temps.append(int(v) / 1000)
    return round(max(temps), 1) if temps else None


def process_count() -> int:
    try:
        return sum(1 for n in os.listdir("/proc") if n.isdigit())
    except OSError:
        return 0


def _value(name: str):
    """A read-only property backed by self._v[name]."""
    return lambda self: to_js(self._v[name])


def _data(name: str):
    """The same for arrays and objects: a real JS value, made once per change."""
    def get(self):
        if name not in self._js:
            self._js[name] = js_value(self, self._v[name], self._engine)
        return self._js[name]
    return get


class System(QObject):
    cpuChanged = Signal()
    cpusChanged = Signal()
    cpuCountChanged = Signal()
    memoryChanged = Signal()
    memoryUsageChanged = Signal()
    meminfoChanged = Signal()
    pressureChanged = Signal()
    loadChanged = Signal()
    uptimeChanged = Signal()
    processCountChanged = Signal()
    disksChanged = Signal()
    networkChanged = Signal()
    batteryChanged = Signal()
    temperatureChanged = Signal()
    hostnameChanged = Signal()
    kernelChanged = Signal()
    userChanged = Signal()
    intervalChanged = Signal()

    def __init__(self, engine=None):
        super().__init__()
        self._engine = engine
        self._js: dict = {}
        self._v = {"cpu": 0.0, "cpus": [], "cpuCount": os.cpu_count() or 1, "memory": memory({}),
                   "memoryUsage": 0.0, "meminfo": {}, "pressure": None, "load": [0.0, 0.0, 0.0],
                   "uptime": 0.0, "processCount": 0, "disks": [], "network": {"rx": 0.0, "tx": 0.0},
                   "battery": {"present": False, "percent": None, "charging": False},
                   "temperature": None, "hostname": "", "kernel": os.uname().release, "user": ""}
        self._cpu_prev: list[tuple[int, int]] = []
        self._net_prev: tuple[float, int, int] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._slow = QTimer(self)
        self._slow.setInterval(10_000)
        self._slow.timeout.connect(self.refresh_slow)
        self.refresh_slow()
        self.refresh()
        self._timer.start()
        self._slow.start()

    def _set(self, name: str, value):
        if self._v.get(name) != value:
            self._v[name] = value
            self._js.pop(name, None)
            getattr(self, name + "Changed").emit()

    def refresh(self):
        # First sample: busy share since boot, so the value is sensible right away.
        times = cpu_times()
        prev = self._cpu_prev if len(self._cpu_prev) == len(times) else [(0, 0)] * len(times)
        fracs = []
        for (b, t), (pb, pt) in zip(times, prev, strict=True):
            dt = t - pt
            fracs.append(round(min(1.0, max(0.0, (b - pb) / dt)), 4) if dt > 0 else 0.0)
        self._cpu_prev = times
        if fracs:
            self._set("cpu", fracs[0])
            self._set("cpus", fracs[1:])
            self._set("cpuCount", max(1, len(fracs) - 1))

        mi = meminfo()
        mem = memory(mi)
        self._set("meminfo", mi)
        self._set("memory", mem)
        self._set("memoryUsage", round(mem["used"] / mem["total"], 4) if mem["total"] else 0.0)
        self._set("pressure", pressure())

        load = read("/proc/loadavg").split()
        if len(load) >= 3:
            self._set("load", [float(x) for x in load[:3]])
        up = read("/proc/uptime").split()
        if up:
            self._set("uptime", float(up[0]))
        self._set("processCount", process_count())

        now = time.monotonic()
        rx, tx = net_bytes()
        if self._net_prev is not None and now > self._net_prev[0]:
            dt = now - self._net_prev[0]
            self._set("network", {"rx": round(max(0, rx - self._net_prev[1]) / dt, 1),
                                  "tx": round(max(0, tx - self._net_prev[2]) / dt, 1)})
        self._net_prev = (now, rx, tx)
        self._set("temperature", temperature())

    def refresh_slow(self):
        self._set("disks", disks())
        self._set("battery", battery())
        self._set("hostname", socket.gethostname())
        self._set("user", getpass.getuser())

    cpu = Property(float, _value("cpu"), notify=cpuChanged)
    cpus = Property("QVariant", _data("cpus"), notify=cpusChanged)
    cpuCount = Property(int, _value("cpuCount"), notify=cpuCountChanged)
    memory = Property("QVariant", _data("memory"), notify=memoryChanged)
    memoryUsage = Property(float, _value("memoryUsage"), notify=memoryUsageChanged)
    meminfo = Property("QVariant", _data("meminfo"), notify=meminfoChanged)
    pressure = Property("QVariant", _data("pressure"), notify=pressureChanged)
    load = Property("QVariant", _data("load"), notify=loadChanged)
    uptime = Property(float, _value("uptime"), notify=uptimeChanged)
    processCount = Property(int, _value("processCount"), notify=processCountChanged)
    disks = Property("QVariant", _data("disks"), notify=disksChanged)
    network = Property("QVariant", _data("network"), notify=networkChanged)
    battery = Property("QVariant", _data("battery"), notify=batteryChanged)
    temperature = Property("QVariant", _value("temperature"), notify=temperatureChanged)
    hostname = Property(str, _value("hostname"), notify=hostnameChanged)
    kernel = Property(str, _value("kernel"), notify=kernelChanged)
    user = Property(str, _value("user"), notify=userChanged)

    def _get_interval(self):
        return self._timer.interval()

    def _set_interval(self, ms: int):
        ms = max(100, int(ms))
        if ms != self._timer.interval():
            self._timer.setInterval(ms)
            self.intervalChanged.emit()

    interval = Property(int, _get_interval, _set_interval, notify=intervalChanged)


_instance: System | None = None     # the latest one made, for Python callers and tests


def register(ctx: AppContext) -> None:
    def make(engine):
        global _instance
        _instance = System(engine)
        return _instance

    qmlRegisterSingletonType(System, URI, MAJOR, MINOR, "System", make)

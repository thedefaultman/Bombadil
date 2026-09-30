"""bombadil-brain-watch: every save, create, delete and rename under /home, with who did it.

Who made a file is known only at the moment it is written, so this small root service holds
one fanotify mark on the whole btrfs filesystem and streams what it sees to each user's
brain as JSON lines on /run/bombadil-brain/watch.sock (the protocol is in the brain spec:
hello, then spooled lines, then live ones).

Why each piece is the way it is:

- A filesystem mark is the only fanotify mark that reports creates, deletes and renames with
  the writer's pid, and it needs root. On btrfs a filesystem mark with file-handle reporting
  placed on a subvolume (/ is @, /home is @home) fails with EXDEV, because btrfs mixes the
  subvolume id into the fsid. So the watcher mounts the top-level subvolume (subvolid=5,
  read-only) at /run/bombadil-brain/top in a mount namespace of its own and marks that: every
  subvolume shares the one superblock, so /home and nested subvolumes such as ~/Projects are
  covered. (Checked on Arch's kernel by tests/vm/samples/brain-watch.)
- Events carry a directory file handle and a name. The handle is opened through the private
  mount and its path mapped back out with /proc/self/mountinfo: /run/bombadil-brain/top/@home/
  user/a.txt is /home/user/a.txt because /home is the mount whose root is /@home. Resolved
  directories are cached, and the cache is dropped whenever a directory is renamed or deleted.
- The writer is named by its cgroup and its chain of parents, read from /proc. A pidfd with
  each event says whether that pid is still the writer. Short-lived writers (a turn's
  `echo x > f`) are gone by then, so a cn_proc fork tracker (forks.py) finds their nearest
  live ancestor, which is still in the turn's scope; those events say "gone": true.
- Each connected client gets only the events under its own home plus /etc and pacman.log
  (root gets everything). A user whose brain is not connected gets a spool file instead
  (/var/lib/bombadil-brain/spool/<uid>.jsonl, capped; an overflow line tells the brain to
  walk). A user is spooled for once their brain has connected once; root never is, so a
  debugging `socat` does not leave a spool of the whole machine behind.
- A slow client never blocks the reader: output is queued per client, and a client whose
  queue passes a bound has it moved to its spool on disk and reads on from there, without
  reconnecting (root, which has no spool, is dropped instead).
- After a stop or a reboot, `btrfs subvolume find-new` lists what was written meanwhile in
  each subvolume under /home, from the generation remembered in generations.json, as
  "offline" events, then "caught_up". A generation is remembered only once every event
  from before it has been read (the queue was seen empty after it), so a crash or a stop
  in the middle of a burst re-lists rather than loses. Deletions and renames made meanwhile
  are invisible to find-new; the brain walks for those.
- Anything else (the live ISO's overlay root, an ext4 dev machine): the socket still answers
  with hello and watching:false, so the brain knows not to wait, and nothing else happens.
  For development, --path DIR marks the filesystem holding DIR directly and treats DIR as
  the home root.

Nothing here reads file contents or follows a symlink.
"""

import argparse
import ctypes
import errno
import json
import os
import pwd
import re
import resource
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from . import fanotify as fan
from . import forks as forkmod

SOCKET = "/run/bombadil-brain/watch.sock"
TOP = "/run/bombadil-brain/top"
STATE = "/var/lib/bombadil-brain"
HOME_ROOT = "/home"
SYSTEM_DIRS = ("/etc",)
SYSTEM_FILES = ("/var/log/pacman.log",)
MASK = fan.FAN_CREATE | fan.FAN_DELETE | fan.FAN_RENAME | fan.FAN_CLOSE_WRITE | fan.FAN_ONDIR
INIT_FLAGS = (fan.FAN_CLASS_NOTIF | fan.FAN_CLOEXEC | fan.FAN_NONBLOCK | fan.FAN_UNLIMITED_QUEUE
              | fan.FAN_REPORT_DFID_NAME)
# Each event in a read carries a pidfd, so one read must stay well under the fd limit.
READ_SIZE = 64 << 10
SPOOL_CAP = 64 << 20
CLIENT_CAP = 8 << 20          # queued bytes before a client is moved to reading its spool
CLIENTS_PER_UID = 8           # so nobody can grow the watcher by opening connections it must fill
CLIENTS_MAX = 256
SPOOL_CHUNK = 256 << 10
GENERATIONS_EVERY = 60.0
CHAIN_MAX = 10
CMD_MAX = 200
# Consecutive events by one writer travel in one line, its who-members once: a chain of ten
# command lines is most of an event's bytes, and a build writes 20,000 files.
RUN_EVENTS = 1000
RUN_BYTES = 192 << 10
# Under a home, never sent for create/write/delete (renames always are, so moves in and out
# are seen): per home, and anywhere below one.
HOME_NOISE = (".cache/", ".local/share/Trash/expunged/")
ANY_NOISE = ("/.git/objects/", "/node_modules/", "/__pycache__/")

CLONE_NEWNS = 0x00020000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC, MS_SLAVE = 32, 4096, 16384, 1 << 19


def log(msg: str) -> None:
    print(f"bombadil-brain-watch: {msg}", file=sys.stderr, flush=True)


def under(path: str, prefix: str) -> bool:
    """Is `path` `prefix` itself or inside it?"""
    if prefix == "/":
        return path.startswith("/")
    return path == prefix or path.startswith(prefix + "/")


def _tail(path: str, prefix: str) -> str | None:
    """What follows `prefix` in `path` ("" or "/..."), or None when it is not under it."""
    if prefix == "/":
        if not path.startswith("/"):
            return None
        return "" if path == "/" else path
    if path == prefix:
        return ""
    if path.startswith(prefix + "/"):
        return path[len(prefix):]
    return None


def _join(base: str, tail: str) -> str:
    """base + tail, where tail is "" or starts with "/"."""
    if base == "/":
        return tail or "/"
    return base + tail


def dumps(obj: dict) -> bytes:
    # ensure_ascii escapes the lone surrogates os.fsdecode makes of undecodable bytes
    # (\udcff), and json.loads + os.fsencode turn them back into the same bytes.
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"


def _members(obj: dict) -> str:
    """The members of a JSON object without its braces, to splice into another."""
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))[1:-1]


def event_line(head: dict, who: str) -> bytes:
    """One event: `head` (op, t, path, old, dir, ino, size) then the who-members."""
    return ("{" + _members(head) + "," + who + "}\n").encode()


def batch_line(who: str, heads: list[str]) -> bytes:
    """Several events by one writer: {"op":"batch", <who-members>, "events":[{head}, ...]}. `heads`
    are the events' own members as JSON (see _members); the brain reads it as those events in
    order, each with the who-members (service._unbatch). A line stands alone, so a spool, a
    reader that joins late and a client that reconnects need nothing from the lines before it."""
    return ('{"op":"batch",' + who + ',"events":[' + ",".join("{" + h + "}" for h in heads) + "]}\n").encode()


# --- The mount table ----------------------------------------------------------------------

@dataclass
class Mount:
    id: int
    dev: str        # major:minor of the superblock
    root: str       # the directory of the filesystem this mount shows (/@home)
    point: str      # where it shows it (/home)
    fstype: str
    source: str


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_mountinfo(text: str) -> list[Mount]:
    out = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        f = left.split()
        r = right.split()
        if not sep or len(f) < 5 or len(r) < 2:
            continue
        try:
            out.append(Mount(int(f[0]), f[2], _unescape(f[3]), _unescape(f[4]), r[0], _unescape(r[1])))
        except ValueError:
            continue
    return out


def read_mountinfo(fd: int | None = None) -> list[Mount]:
    if fd is None:
        with open("/proc/self/mountinfo", "rb") as f:
            return parse_mountinfo(os.fsdecode(f.read()))
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        b = os.read(fd, 1 << 16)
        if not b:
            break
        chunks.append(b)
    return parse_mountinfo(os.fsdecode(b"".join(chunks)))


def mount_of(mounts: list[Mount], path: str) -> Mount | None:
    """The mount `path` is on: the last-mounted one with the longest mount point above it."""
    best = None
    for m in mounts:
        if under(path, m.point) and (best is None or len(m.point) >= len(best.point)):
            best = m
    return best


class PathMap:
    """Real paths for paths under one mount of a filesystem (the private top-level mount,
    or DIR's own mount with --path), through the other mounts of the same filesystem: the
    one whose root is the longest prefix wins, then the shortest mount point. When `prefer`
    is given, a place it likes wins over a longer root: a bind mount of ~/Projects at /srv
    must not move Projects' events out of /home, where the brain looks."""

    def __init__(self, mounts: list[Mount], own: Mount, exclude_own: bool,
                 prefer: Callable[[str], bool] | None = None):
        self.own = own
        self.prefer = prefer
        targets = [m for m in mounts if m.dev == own.dev and not (exclude_own and m.id == own.id)]
        targets.sort(key=lambda m: (-len(m.root), len(m.point)))
        self.targets = [(m.root, m.point) for m in targets]

    def real(self, path: str) -> str | None:
        tail = _tail(path, self.own.point)
        if tail is None:
            return None
        rel = _join(self.own.root, tail)
        first = None
        for root, point in self.targets:
            t = _tail(rel, root)
            if t is not None:
                got = _join(point, t)
                if self.prefer is None or self.prefer(got):
                    return got
                first = first or got
        return first


# --- What gets sent -----------------------------------------------------------------------

class Scope:
    """Which paths are sent at all, and which are noise under a home."""

    def __init__(self, home_root: str = HOME_ROOT, system_dirs=SYSTEM_DIRS, system_files=SYSTEM_FILES):
        self.home_root = home_root.rstrip("/") or "/"
        self._home = self.home_root + "/" if self.home_root != "/" else "/"
        self._sys_dirs = tuple(d.rstrip("/") + "/" for d in system_dirs)
        self._sys_files = frozenset(system_files)

    def system(self, path: str) -> bool:
        return path in self._sys_files or path.startswith(self._sys_dirs)

    def home(self, path: str) -> bool:
        return path.startswith(self._home) and path != self._home

    def keep(self, path: str) -> bool:
        return self.home(path) or self.system(path)

    def near(self, path: str) -> bool:
        """Is this directory where kept paths are (the home root, /etc, pacman.log's)?"""
        return (under(path, self.home_root) or any(under(path, d.rstrip("/")) for d in self._sys_dirs)
                or any(under(path, os.path.dirname(f)) for f in self._sys_files))

    def noise(self, path: str) -> bool:
        if not self.home(path):
            return False
        rel = path[len(self._home) - 1:]        # "/user/..."
        _, _, rest = rel[1:].partition("/")      # the path inside that user's home
        return rest.startswith(HOME_NOISE) or any(n in rel for n in ANY_NOISE)


# --- Who wrote it -------------------------------------------------------------------------

def _slurp(path: str, size: int = 8192) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        return os.read(fd, size)
    except OSError:
        return None
    finally:
        os.close(fd)


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    comm: str
    start: int          # clock ticks since boot
    uid: int | None
    cgroup: str
    cmd: str            # argv joined by spaces, cut to CMD_MAX
    t: float = 0.0      # when read (monotonic)
    chain: list | None = None
    frags: dict | None = None   # gone -> the who-members as JSON, encoded once per process


def parse_stat(raw: bytes) -> tuple[str, int, int] | None:
    """(comm, ppid, start ticks) from /proc/<pid>/stat."""
    try:
        comm = raw[raw.index(b"(") + 1: raw.rindex(b")")].decode(errors="replace")
        fields = raw[raw.rindex(b")") + 2:].split()
        return comm, int(fields[1]), int(fields[19])
    except (ValueError, IndexError):
        return None


def parse_cgroup(raw: bytes | None) -> str:
    for line in (raw or b"").splitlines():
        if line.startswith(b"0::"):
            return os.fsdecode(line[3:])
    return ""


def parse_uid(raw: bytes | None) -> int | None:
    for line in (raw or b"").splitlines():
        if line.startswith(b"Uid:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


class Procs:
    """Process facts from /proc, cached briefly per pid: a build writing 20,000 files is one
    process, and its /proc files need reading once, not 20,000 times."""

    def __init__(self, proc: str = "/proc", ttl: float = 1.0, size: int = 8192):
        self.proc = proc
        self.ttl = ttl
        self.size = size
        self.hz = os.sysconf("SC_CLK_TCK")
        self._cache: OrderedDict[int, ProcInfo] = OrderedDict()

    def read(self, pid: int, now: float) -> ProcInfo | None:
        base = f"{self.proc}/{pid}"
        st = _slurp(f"{base}/stat")
        parsed = parse_stat(st) if st else None
        if parsed is None:
            return None
        comm, ppid, start = parsed
        cmd = (_slurp(f"{base}/cmdline", 4096) or b"").rstrip(b"\0").replace(b"\0", b" ")
        return ProcInfo(pid, ppid, comm, start, parse_uid(_slurp(f"{base}/status")),
                        parse_cgroup(_slurp(f"{base}/cgroup")), cmd.decode(errors="replace")[:CMD_MAX], now)

    def info(self, pid: int, now: float | None = None) -> ProcInfo | None:
        now = time.monotonic() if now is None else now
        p = self._cache.get(pid)
        if p is not None and now - p.t < self.ttl:
            return p
        p = self.read(pid, now)
        if p is None:
            self._cache.pop(pid, None)
            return None
        self._cache[pid] = p
        self._cache.move_to_end(pid)
        while len(self._cache) > self.size:
            self._cache.popitem(last=False)
        return p

    def forget(self, pid: int) -> None:
        self._cache.pop(pid, None)

    def chain(self, p: ProcInfo, now: float) -> list:
        """The writer then its ancestors, up to CHAIN_MAX, ending after pid 1."""
        if p.chain is not None:
            return p.chain
        out, cur, seen = [], p, set()
        while cur is not None and len(out) < CHAIN_MAX:
            out.append([cur.pid, cur.comm, cur.cmd])
            seen.add(cur.pid)
            if cur.pid == 1 or cur.ppid <= 0 or cur.ppid in seen:
                break
            cur = self.info(cur.ppid, now)
        p.chain = out
        return out

    def started_by(self, pid: int, t_boot: float) -> bool:
        """Is `pid` running and did it start no later than `t_boot` (CLOCK_BOOTTIME)?"""
        p = self.info(pid)
        return p is not None and p.start / self.hz <= t_boot


def pidfd_alive(pidfd: int) -> bool:
    try:
        signal.pidfd_send_signal(pidfd, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class Attribution:
    """The who-fields of an event: pid, uid, comm, cgroup, chain, gone."""

    def __init__(self, procs: Procs, forks: forkmod.ForkMap | None):
        self.procs = procs
        self.forks = forks

    def _fields(self, p: ProcInfo, now: float, gone: bool) -> dict:
        return {"pid": p.pid, "uid": p.uid, "comm": p.comm, "cgroup": p.cgroup,
                "chain": self.procs.chain(p, now), "gone": gone}

    @staticmethod
    def unknown(pid: int) -> dict:
        return {"pid": pid, "uid": None, "comm": "", "cgroup": "", "chain": [], "gone": True}

    def find(self, pid: int, pidfd: int | None, now: float) -> tuple[ProcInfo | None, bool]:
        """(the writer, False), (its nearest live ancestor, True) or (None, True)."""
        if pid > 0 and pidfd != fan.FAN_NOPIDFD:
            p = self.procs.info(pid, now)
            # With a pidfd, what /proc said is the writer's only if it is still alive now:
            # a pid reaped and reused in between would name someone else.
            if p is not None and pidfd is not None and pidfd >= 0 and not pidfd_alive(pidfd):
                self.procs.forget(pid)
                p = None
            if p is not None:
                return p, False
        if pid > 0 and self.forks is not None:
            anc = self.forks.ancestor(pid, self.procs.started_by)
            p = self.procs.info(anc, now) if anc else None
            if p is not None:
                return p, True
        return None, True

    def who(self, pid: int, pidfd: int | None, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        p, gone = self.find(pid, pidfd, now)
        return self._fields(p, now, gone) if p is not None else self.unknown(pid)

    def who_json(self, pid: int, pidfd: int | None, now: float | None = None) -> str:
        """who() as JSON object members, encoded once per process rather than per event: a
        chain of ten command lines is most of every line."""
        now = time.monotonic() if now is None else now
        p, gone = self.find(pid, pidfd, now)
        if p is None:
            return _members(self.unknown(pid))
        if p.frags is None:
            p.frags = {}
        frag = p.frags.get(gone)
        if frag is None:
            frag = p.frags[gone] = _members(self._fields(p, now, gone))
        return frag


# --- Handles to paths ---------------------------------------------------------------------

class Resolver:
    """Directory file handle -> (path under the watched mount, real path or None), cached."""

    def __init__(self, mount_fd: int, pathmap: PathMap, size: int = 8192,
                 opener: Callable[[int, bytes], int] = fan.open_by_handle):
        self.mount_fd = mount_fd
        self.pathmap = pathmap
        self.size = size
        self.opener = opener
        self._cache: OrderedDict[bytes, tuple[str, str | None]] = OrderedDict()
        self.stale = 0

    def clear(self) -> None:
        self._cache.clear()

    def dir(self, handle: bytes) -> tuple[str, str | None] | None:
        hit = self._cache.get(handle)
        if hit is not None:
            self._cache.move_to_end(handle)
            return hit
        try:
            fd = self.opener(self.mount_fd, handle)
        except OSError:
            # ESTALE: the directory is gone. Anything else: nothing better to do either.
            self.stale += 1
            return None
        try:
            priv = os.fsdecode(os.readlink(f"/proc/self/fd/{fd}"))
            # A removed directory reads as "<path> (deleted)"; a real name ending that way
            # still exists under it.
            gone = priv.endswith(" (deleted)") and (os.fstat(fd).st_nlink == 0 or not os.path.lexists(priv))
        except OSError:
            return None
        finally:
            os.close(fd)
        if gone:
            self.stale += 1
            return None
        got = (priv, self.pathmap.real(priv))
        self._cache[handle] = got
        while len(self._cache) > self.size:
            self._cache.popitem(last=False)
        return got

    def path(self, handle: bytes | None, name: bytes | None) -> tuple[str, str] | None:
        if handle is None:
            return None
        d = self.dir(handle)
        if d is None or d[1] is None:
            return None
        if not name or name == b".":
            return d[0], d[1]
        n = os.fsdecode(name)
        if "/" in n:
            return None
        return _join(d[0], "/" + n), _join(d[1], "/" + n)


# --- Clients and spools -------------------------------------------------------------------

class Spool:
    """One user's lines while their brain is away."""

    def __init__(self, path: str, cap: int):
        self.path = path
        self.cap = cap
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        self.size = os.fstat(self.fd).st_size
        self.pending = bytearray()
        self.reader: "Client | None" = None

    def append(self, line: bytes) -> None:
        if self.size + len(self.pending) + len(line) > self.cap:
            self.overflow()
        self.pending += line

    def overflow(self) -> None:
        self.pending.clear()
        os.ftruncate(self.fd, 0)
        self.size = 0
        self.pending += dumps({"op": "overflow", "t": round(time.time(), 3)})
        if self.reader is not None and self.reader.rfd is not None:
            os.lseek(self.reader.rfd, 0, os.SEEK_SET)
            self.reader.carry = b""

    def flush(self) -> None:
        done = 0
        err = None
        with memoryview(self.pending) as view:
            try:
                while done < len(view):
                    done += os.write(self.fd, view[done:])
            except OSError as e:
                err = e
        self.size += done
        del self.pending[:done]
        if err is not None:
            # A full disk: what is waiting is lost, so the spool becomes one overflow line
            # (the brain walks), which truncating it also makes room for.
            log(f"could not write {self.path}: {err.strerror}")
            try:
                self.overflow()
                self.size += os.write(self.fd, self.pending)
            except OSError:
                pass
            self.pending.clear()

    def empty(self) -> None:
        self.pending.clear()
        os.ftruncate(self.fd, 0)
        self.size = 0

    def close(self) -> None:
        self.flush()
        os.close(self.fd)


class Client:
    def __init__(self, sock: socket.socket, uid: int, home: str | None):
        self.sock = sock
        self.fd = sock.fileno()
        self.uid = uid
        self.home = home
        self.out = bytearray()
        self.partial = b""      # the part of a line already sent, when a send stopped mid-line
        self.rfd: int | None = None   # reading the spool: live lines wait in the spool meanwhile
        self.carry = b""
        self.inbuf = bytearray()
        self.writing = False    # registered for EPOLLOUT
        self.eof = False        # the client shut its sending side; it may still read


class Hub:
    """Routes lines to clients by the peer's uid, and to spools for users who are away."""

    def __init__(self, spool_dir: str, scope: Scope, home_of: Callable[[int], str | None],
                 spool_cap: int = SPOOL_CAP, client_cap: int = CLIENT_CAP, hello: bytes = b""):
        self.spool_dir = spool_dir
        self.scope = scope
        self.home_of = home_of
        self.spool_cap = spool_cap
        self.client_cap = client_cap
        self.hello = hello
        self.clients: dict[int, Client] = {}
        self.by_uid: dict[int, list[Client]] = {}
        self.spools: dict[int, Spool] = {}
        self.homes: dict[int, str | None] = {}
        self.dirty: set[int] = set()           # client fds with output to push
        self._targets: list[int] | None = None
        self.on_drop: Callable[[Client], None] = lambda c: None
        os.makedirs(spool_dir, mode=0o700, exist_ok=True)
        for name in os.listdir(spool_dir):
            if name.endswith(".jsonl") and name[:-6].isdigit() and int(name[:-6]) != 0:
                uid = int(name[:-6])
                self._spool(uid)

    def _spool(self, uid: int) -> Spool | None:
        if uid == 0:
            return None
        s = self.spools.get(uid)
        if s is None:
            try:
                s = self.spools[uid] = Spool(os.path.join(self.spool_dir, f"{uid}.jsonl"), self.spool_cap)
                self._targets = None
            except OSError as e:
                log(f"no spool for uid {uid}: {e.strerror}")
                return None
            self.homes.setdefault(uid, self._home(uid))
        return s

    def _home(self, uid: int) -> str | None:
        """Where uid's events are, if anywhere: only a home under the home root counts, so a
        system user whose home is / (nobody on Arch) gets the system paths and nothing else."""
        home = self.home_of(uid)
        return home if home is not None and self.scope.home(home) else None

    def full(self, uid: int) -> bool:
        return len(self.clients) >= CLIENTS_MAX or len(self.by_uid.get(uid, ())) >= CLIENTS_PER_UID

    # Routing
    def wants(self, uid: int, paths: Iterable[str | None]) -> bool:
        if uid == 0:
            return True
        home = self.homes.get(uid)
        for p in paths:
            if p is None:
                continue
            if self.scope.system(p) or (home is not None and under(p, home)):
                return True
        return False

    def deliver(self, line: bytes, paths: tuple) -> None:
        for uid in self._uids():
            if self.wants(uid, paths):
                self._to(uid, line)

    def deliver_run(self, who: str, run: list[tuple[str, tuple]]) -> None:
        """Consecutive events by one writer, as (the event's members, its paths): each user
        gets the ones under their own home, in order, in one line with the who-members once (a
        single event is an ordinary line)."""
        for uid in self._uids():
            mine = [head for head, paths in run if self.wants(uid, paths)]
            if len(mine) == 1:
                self._to(uid, ("{" + mine[0] + "," + who + "}\n").encode())
            elif mine:
                self._to(uid, batch_line(who, mine))

    def broadcast(self, line: bytes) -> None:
        for uid in self._uids():
            self._to(uid, line)

    def _uids(self) -> list[int]:
        if self._targets is None:
            self._targets = sorted(set(self.spools) | set(self.by_uid))
        return self._targets

    def _to(self, uid: int, line: bytes) -> None:
        clients = self.by_uid.get(uid, ())
        draining = False
        stuck = []
        for c in clients:
            if c.rfd is not None:
                draining = True
                continue
            c.out += line
            self.dirty.add(c.fd)
            if len(c.out) > self.client_cap:
                stuck.append(c)
        spool = self.spools.get(uid)
        if spool is not None and (draining or not clients):
            spool.append(line)
        for c in stuck:
            self._behind(c)

    def _behind(self, c: Client) -> None:
        """A client that stopped keeping up: its queue moves to its spool on disk and it
        reads from there at its own pace, as if it had just connected, so a burst never
        grows the watcher and never costs the brain a reconnect. Root has no spool and a
        spool has one reader, so otherwise the client is dropped."""
        spool = self.spools.get(c.uid)
        if spool is None or spool.reader is not None:
            log(f"a client of uid {c.uid} is not reading; dropping it")
            self.drop(c)
            return
        # The line it is in the middle of stays in its queue; whole lines go to the spool.
        cut = c.out.find(b"\n") + 1 if c.partial else 0
        spool.append(bytes(c.out[cut:]))
        spool.flush()
        try:
            c.rfd = os.open(spool.path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            self.drop(c)
            return
        spool.reader = c
        del c.out[cut:]

    # Clients
    def add(self, sock: socket.socket, uid: int) -> Client:
        home = self._home(uid)
        self.homes[uid] = home
        c = Client(sock, uid, home)
        c.out += self.hello
        others = self.by_uid.get(uid, [])
        spool = self._spool(uid)
        if spool is not None and not others and spool.reader is None:
            spool.flush()
            try:
                c.rfd = os.open(spool.path, os.O_RDONLY | os.O_CLOEXEC)
                spool.reader = c
            except OSError:
                c.rfd = None
        self.clients[c.fd] = c
        self.by_uid.setdefault(uid, []).append(c)
        self._targets = None
        self.dirty.add(c.fd)
        return c

    def drop(self, c: Client) -> None:
        if self.clients.pop(c.fd, None) is None:
            return
        self.dirty.discard(c.fd)
        peers = self.by_uid.get(c.uid, [])
        if c in peers:
            peers.remove(c)
        if not peers:
            self.by_uid.pop(c.uid, None)
            self._targets = None
        spool = self.spools.get(c.uid)
        if c.rfd is not None:
            os.close(c.rfd)
            c.rfd = None
            if spool is not None:
                spool.reader = None   # the spool stays; the next connection reads it again
        elif spool is not None and (c.out or c.partial) and not any(p.rfd is None for p in peers):
            # What it never got goes back in line, whole lines only.
            spool.append(c.partial + bytes(c.out))
        self.on_drop(c)
        try:
            c.sock.close()
        except OSError:
            pass

    def _refill(self, c: Client) -> None:
        """Move the next whole lines of the spool into a reading client's output; at the end
        of the spool, empty it and make the client live."""
        spool = self.spools.get(c.uid)
        if c.rfd is None:
            return
        if spool is None:
            os.close(c.rfd)
            c.rfd = None
            return
        spool.flush()
        try:
            data = os.read(c.rfd, SPOOL_CHUNK)
        except OSError:
            data = b""
        if data:
            data = c.carry + data
            cut = data.rfind(b"\n") + 1
            c.out += data[:cut]
            c.carry = data[cut:]
            return
        # Everything written so far has been read: live from here on.
        c.carry = b""
        os.close(c.rfd)
        c.rfd = None
        spool.reader = None
        spool.empty()

    def pump(self, c: Client, budget: int = 4 << 20) -> bool:
        """Send what the client can take now. Returns whether output is still waiting."""
        sent = 0
        while sent < budget:
            if c.rfd is not None and len(c.out) < SPOOL_CHUNK:
                self._refill(c)
            if not c.out:
                if c.rfd is None:
                    return False
                continue
            try:
                with memoryview(c.out) as view:
                    n = c.sock.send(view[:1 << 20])
            except (BlockingIOError, InterruptedError):
                return True
            except OSError:
                self.drop(c)
                return False
            nl = c.out.rfind(b"\n", 0, n)
            c.partial = bytes(c.out[nl + 1:n]) if nl >= 0 else c.partial + bytes(c.out[:n])
            del c.out[:n]
            sent += n
        return bool(c.out) or c.rfd is not None

    def flush_spools(self) -> None:
        for s in self.spools.values():
            if s.pending:
                s.flush()

    def close(self) -> None:
        for c in list(self.clients.values()):
            self.pump(c, budget=1 << 20)
            self.drop(c)
        for s in self.spools.values():
            s.close()
        self.spools.clear()


# --- btrfs --------------------------------------------------------------------------------

FIND_NEW_LINE = re.compile(
    rb"^inode \d+ file offset \d+ len \d+ disk start \d+ offset \d+ gen \d+ flags \S+ (.*)$"
)
MARKER = re.compile(rb"^transid marker was (\d+)$")
SUBVOL_LINE = re.compile(rb"^ID (\d+) gen \d+ top level \d+ path (.*)$")


def parse_find_new(lines: Iterable[bytes]) -> Iterator[tuple[str, int | None]]:
    """(relative path, None) per extent line and ("", marker) for the last line."""
    for line in lines:
        line = line.rstrip(b"\n")
        m = FIND_NEW_LINE.match(line)
        if m:
            yield os.fsdecode(m.group(1)), None
            continue
        m = MARKER.match(line)
        if m:
            yield "", int(m.group(1))


def parse_subvolumes(out: bytes) -> list[tuple[int, str]]:
    got = []
    for line in out.splitlines():
        m = SUBVOL_LINE.match(line)
        if m:
            got.append((int(m.group(1)), os.fsdecode(m.group(2))))
    return got


class Btrfs:
    def __init__(self, binary: str = "btrfs"):
        self.bin = shutil.which(binary) or binary

    def _run(self, *args: str, timeout: float = 120) -> bytes:
        r = subprocess.run([self.bin, *args], capture_output=True, timeout=timeout, check=False,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise OSError(0, (r.stderr or b"").decode(errors="replace").strip() or f"btrfs {args[0]} failed")
        return r.stdout

    def subvolumes(self, top: str) -> list[tuple[int, str]]:
        """The writable subvolumes below the top-level one: (id, path from the top)."""
        every = parse_subvolumes(self._run("subvolume", "list", top))
        ro = {i for i, _ in parse_subvolumes(self._run("subvolume", "list", "-r", top))}
        return [(i, p) for i, p in every if i not in ro]

    def marker(self, path: str) -> int | None:
        for _, gen in parse_find_new(self._run("subvolume", "find-new", path, "9999999999").splitlines()):
            if gen is not None:
                return gen
        return None

    def find_new(self, path: str, gen: int) -> Iterator[str]:
        """Paths (relative to the subvolume) of files written at or after generation `gen`."""
        with subprocess.Popen([self.bin, "subvolume", "find-new", path, str(gen)], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL) as p:
            assert p.stdout is not None
            for rel, marker in parse_find_new(p.stdout):
                if marker is None:
                    yield rel


class Generations:
    """The last generation seen per subvolume, so a restart knows what it missed."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict[str, dict]:
        try:
            with open(self.path) as f:
                data = json.load(f)
            subs = data.get("subvolumes", {})
            return {k: v for k, v in subs.items() if isinstance(v, dict) and isinstance(v.get("gen"), int)}
        except (OSError, ValueError, AttributeError):
            return {}

    def save(self, subs: dict[str, dict]) -> None:
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"v": 1, "t": round(time.time(), 3), "subvolumes": subs}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            log(f"could not save {self.path}: {e.strerror}")


# --- The service --------------------------------------------------------------------------

def _libc():
    return fan.libc()


def private_mounts() -> None:
    """A mount namespace of our own, so the top-level mount is seen by nobody else. Under
    systemd (PrivateMounts=yes) this nests harmlessly; by hand it keeps the host clean."""
    lib = _libc()
    if lib.unshare(CLONE_NEWNS) != 0:
        e = ctypes.get_errno()
        raise OSError(e, f"unshare: {os.strerror(e)}")
    if lib.mount(b"none", b"/", None, MS_REC | MS_SLAVE, None) != 0:
        e = ctypes.get_errno()
        raise OSError(e, f"making / a slave mount: {os.strerror(e)}")


def mount_top(source: str, top: str) -> None:
    lib = _libc()
    os.makedirs(top, mode=0o700, exist_ok=True)
    flags = MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC
    src, dst = os.fsencode(source), os.fsencode(top)
    if lib.mount(src, dst, b"btrfs", flags, b"subvolid=5") == 0:
        return
    first = ctypes.get_errno()
    # Some kernels refuse a read-only mount of a filesystem mounted read-write elsewhere:
    # mount it read-write, then make this one mount read-only.
    if lib.mount(src, dst, b"btrfs", flags & ~MS_RDONLY, b"subvolid=5") == 0:
        if lib.mount(None, dst, None, MS_REMOUNT | MS_BIND | flags, None) != 0:
            log("the top-level mount stayed read-write")
        return
    raise OSError(first, f"mounting the top-level subvolume: {os.strerror(first)}")


def default_home_of(home_root: str, overrides: dict[int, str]) -> Callable[[int], str | None]:
    def home_of(uid: int) -> str | None:
        if uid in overrides:
            return overrides[uid]
        try:
            pw = pwd.getpwuid(uid).pw_dir
        except KeyError:
            return None
        if home_root != HOME_ROOT and not under(pw, home_root):
            return _join(home_root, "/" + os.path.basename(pw.rstrip("/")))
        return pw.rstrip("/") or "/"
    return home_of


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


class Watcher:
    def __init__(self, args):
        self.args = args
        self.pid = os.getpid()
        self.fan_fd: int | None = None
        self.mount_fd: int | None = None
        self.fs = ""
        self.top = ""
        self.reason = ""
        self.btrfs_mode = False
        self.stopping = False
        self.nl: socket.socket | None = None
        self.forks: forkmod.ForkMap | None = None
        self.pathmap: PathMap | None = None
        self.resolver: Resolver | None = None
        self.mountinfo_fd: int | None = None
        self.own: Mount | None = None
        self.scope = Scope(os.path.realpath(args.path) if args.path else HOME_ROOT)
        self.procs = Procs()
        self.attr = Attribution(self.procs, None)
        self.btrfs = Btrfs()
        self.generations = Generations(os.path.join(args.state, "generations.json"))
        self.saved: dict[str, dict] = {}
        self.pending: tuple[float, dict[str, dict], set[str]] | None = None   # (read at, markers, ids)
        self.empty_at = 0.0     # when the fanotify queue was last read to empty
        self.hub: Hub | None = None
        self.ep = select.epoll()
        self.listener: socket.socket | None = None
        self.stats = {"events": 0, "sent": 0}
        self._run: list[tuple[str, tuple]] = []     # events by one writer, waiting to go out as a line
        self._run_who: str | None = None
        self._run_bytes = 0

    # Setup
    def start_watching(self) -> None:
        mounts = read_mountinfo()
        if self.args.path:
            path = os.path.realpath(self.args.path)
            own = mount_of(mounts, path)
            if own is None:
                raise OSError(errno.ENOENT, f"no mount holds {path}")
            self.fs, self.top = own.fstype, path
            self.mount_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            mark_at = path
            exclude_own = False
        else:
            home = mount_of(mounts, HOME_ROOT)
            self.fs = home.fstype if home else ""
            if home is None or home.fstype != "btrfs":
                self.reason = f"{HOME_ROOT} is on {self.fs or 'nothing'}, not btrfs"
                raise LookupError(self.reason)
            private_mounts()
            self.top = self.args.top
            mount_top(home.source, self.top)
            mounts = read_mountinfo()
            own = next((m for m in mounts if m.point == self.top and m.dev == home.dev), None)
            if own is None:
                raise OSError(errno.ENOENT, "the top-level mount did not appear")
            self.mount_fd = os.open(self.top, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            mark_at = self.top
            exclude_own = True
            self.btrfs_mode = True
        self.own = own
        self._exclude_own = exclude_own
        self.pathmap = PathMap(mounts, own, exclude_own, self.scope.near)
        self.resolver = Resolver(self.mount_fd, self.pathmap)
        try:
            self.fan_fd = fan.init(INIT_FLAGS | fan.FAN_REPORT_PIDFD)
        except OSError:
            self.fan_fd = fan.init(INIT_FLAGS)   # before 5.15: no pidfd, /proc is trusted as is
            log("no FAN_REPORT_PIDFD: writers are read from /proc without a pidfd")
        fan.mark(self.fan_fd, fan.FAN_MARK_ADD | fan.FAN_MARK_FILESYSTEM, MASK, mark_at)
        self.ep.register(self.fan_fd, select.EPOLLIN)
        self.mountinfo_fd = os.open("/proc/self/mountinfo", os.O_RDONLY | os.O_CLOEXEC)
        self.ep.register(self.mountinfo_fd, select.EPOLLPRI | select.EPOLLERR)
        try:
            self.nl = forkmod.listen()
            self.forks = forkmod.ForkMap()
            self.attr.forks = self.forks
            self.ep.register(self.nl.fileno(), select.EPOLLIN)
        except OSError as e:
            log(f"no fork tracker ({e.strerror or e}); writers that exit first are not attributed")

    def hello(self) -> bytes:
        return dumps({"op": "hello", "v": 1, "watching": self.fan_fd is not None, "fs": self.fs,
                      "top": self.top if self.fan_fd is not None else "", "reason": self.reason})

    def listen(self) -> None:
        path = self.args.socket
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        s.bind(path)
        os.chmod(path, 0o666)
        s.listen(64)
        s.setblocking(False)
        self.listener = s
        self.ep.register(s.fileno(), select.EPOLLIN)

    def refresh_mounts(self) -> None:
        if self.mountinfo_fd is None or self.own is None:
            return
        mounts = read_mountinfo(self.mountinfo_fd)
        own = next((m for m in mounts if m.id == self.own.id), None)
        if own is None:
            return
        self.pathmap = PathMap(mounts, own, self._exclude_own, self.scope.near)
        if self.resolver is not None:
            self.resolver.pathmap = self.pathmap
            self.resolver.clear()

    # Events
    def read_events(self, rounds: int = 64) -> int:
        n = 0
        assert self.fan_fd is not None
        for _ in range(rounds):
            try:
                buf = os.read(self.fan_fd, READ_SIZE)
            except BlockingIOError:
                self.empty_at = time.monotonic()
                break
            except InterruptedError:
                break
            if not buf:
                break
            if self.nl is not None and self.forks is not None:
                # Every fork that happened before these events is queued already.
                forkmod.drain(self.nl, self.forks)
            events = fan.parse(buf)
            self.handle(events, round(time.time(), 3))
            self.push()
            n += len(events)
        return n

    def handle(self, events: list[fan.Event], now: float) -> None:
        mono = time.monotonic()
        for ev in events:
            try:
                self._one(ev, now, mono)
            except Exception as e:  # one odd event must never stop the watcher
                log(f"skipped an event: {e!r}")
            finally:
                for fd in ev.fds():
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        self._flush_run()
        self.stats["events"] += len(events)

    def _send(self, head: dict, who: str, paths: tuple) -> None:
        """Queue one event. Events from one writer in a row go out as one line (see
        _flush_run); a different writer, a full line or the end of a read ends the run."""
        if who != self._run_who or len(self._run) >= RUN_EVENTS or self._run_bytes >= RUN_BYTES:
            self._flush_run()
            self._run_who = who
        members = _members(head)
        self._run.append((members, paths))
        self._run_bytes += len(members)
        self.stats["sent"] += 1

    def _flush_run(self) -> None:
        run, who = self._run, self._run_who
        self._run, self._run_who, self._run_bytes = [], None, 0
        if run and who is not None:
            assert self.hub is not None
            try:
                self.hub.deliver_run(who, run)
            except Exception as e:  # never stops the watcher; what was lost is found again by walking
                log(f"could not deliver {len(run)} events: {e!r}")
                try:
                    self.hub.broadcast(dumps({"op": "overflow", "t": round(time.time(), 3)}))
                except Exception:  # noqa: BLE001
                    pass

    def _broadcast(self, line: bytes) -> None:
        """A line for everyone, after the events queued before it."""
        assert self.hub is not None
        self._flush_run()
        self.hub.broadcast(line)

    def _one(self, ev: fan.Event, now: float, mono: float) -> None:
        m = ev.mask
        if m & fan.FAN_Q_OVERFLOW:
            self._broadcast(dumps({"op": "overflow", "t": now}))
            return
        if ev.pid == self.pid or self.resolver is None:
            return
        isdir = bool(m & fan.FAN_ONDIR)
        scope = self.scope
        if m & fan.FAN_RENAME:
            old = self.resolver.path(ev.old_dir, ev.old_name)
            new = self.resolver.path(ev.new_dir, ev.new_name)
            if isdir:
                self.resolver.clear()
            o = old[1] if old else None
            n = new[1] if new else None
            keep_o = o is not None and scope.keep(o)
            keep_n = n is not None and scope.keep(n)
            if not (keep_o or keep_n):
                return
            who = self.attr.who_json(ev.pid, ev.pidfd, mono)
            if o is not None and n is not None:
                head = {"op": "rename", "t": now, "path": n, "old": o, "dir": isdir}
                self._send({**head, "ino": None, "size": None}, who, (n, o))
            elif n is not None and not scope.noise(n):
                # Moved in from somewhere we cannot name any more: it simply appeared.
                assert new is not None
                st = _lstat(new[0])
                head = {"op": "create", "t": now, "path": n, "dir": isdir}
                self._send({**head, **_idsize(st, isdir)}, who, (n,))
            elif o is not None and not scope.noise(o):
                head = {"op": "delete", "t": now, "path": o, "dir": isdir}
                self._send({**head, "ino": None, "size": None}, who, (o,))
            return
        p = self.resolver.path(ev.dir, ev.name)
        if isdir and m & fan.FAN_DELETE:
            self.resolver.clear()
        if p is None:
            return
        priv, real = p
        if not scope.keep(real) or scope.noise(real):
            return
        created, wrote, deleted = m & fan.FAN_CREATE, m & fan.FAN_CLOSE_WRITE, m & fan.FAN_DELETE
        st = _lstat(priv) if (created or wrote) else None
        ops = []
        if deleted and st is not None:
            ops.append("delete")        # merged delete + create of the same name: it is there now
        if created:
            ops.append("create")
        if wrote:
            ops.append("write")
        if deleted and st is None:
            ops.append("delete")
        if not ops:
            return
        who = self.attr.who_json(ev.pid, ev.pidfd, mono)
        for op in ops:
            ids = _idsize(st, isdir) if op != "delete" else {"ino": None, "size": None}
            self._send({"op": op, "t": now, "path": real, "dir": isdir, **ids}, who, (real,))

    # Catch-up
    def _home_subvolumes(self) -> list[tuple[int, str, str, str]]:
        """(id, path from the top, path under the private mount, real path) of each writable
        subvolume that shows up under the home root."""
        assert self.pathmap is not None
        out = []
        for sid, rel in self.btrfs.subvolumes(self.top):
            priv = _join(self.top, "/" + rel)
            real = self.pathmap.real(priv)
            if real is not None and under(real, self.scope.home_root):
                out.append((sid, rel, priv, real))
        return out

    def _markers(self, subs) -> dict[str, dict]:
        """Each subvolume's current generation. find-new commits the filesystem first, so
        anything written after this is in a later generation."""
        got = {}
        for sid, rel, priv, _ in subs:
            try:
                gen = self.btrfs.marker(priv)
            except (OSError, subprocess.TimeoutExpired) as e:
                log(f"no generation for {rel}: {e}")
                continue
            if gen is not None:
                got[str(sid)] = {"path": rel, "gen": gen}
        return got

    def catch_up(self) -> None:
        assert self.hub is not None
        if not self.btrfs_mode:
            self._broadcast(dumps({"op": "caught_up", "t": round(time.time(), 3), "offline": 0}))
            return
        t0 = time.monotonic()
        sent = 0
        try:
            subs = self._home_subvolumes()
            read_at = time.monotonic()
            fresh = self._markers(subs)
            self.pending = (read_at, fresh, {str(sid) for sid, *_ in subs})
            self.saved = old = self.generations.load()
            seen: set[str] = set()
            for sid, rel, priv, real in subs:
                last = old.get(str(sid))
                if last is None or last.get("path") != rel:
                    continue    # new since the last run: the brain's walk finds what is in it
                for name in self.btrfs.find_new(priv, last["gen"]):
                    path = _join(real, "/" + name)
                    if path in seen or not self.scope.keep(path) or self.scope.noise(path):
                        continue
                    seen.add(path)
                    st = _lstat(_join(priv, "/" + name))
                    if st is None or not stat.S_ISREG(st.st_mode):
                        continue
                    who = _members({"pid": 0, "uid": st.st_uid, "comm": "", "cgroup": "", "chain": [],
                                    "gone": True})
                    self._send({"op": "offline", "t": round(st.st_mtime, 3), "path": path, "dir": False,
                                "ino": st.st_ino, "size": st.st_size}, who, (path,))
                    sent += 1
                    if sent % 1000 == 0:
                        self._flush_run()
                        self.hub.flush_spools()
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"catch-up failed: {e}")
        self._broadcast(dumps({"op": "caught_up", "t": round(time.time(), 3), "offline": sent}))
        self.hub.flush_spools()
        log(f"caught up: {sent} files written while stopped ({time.monotonic() - t0:.1f}s)")

    def read_markers(self) -> None:
        """Every minute (unless the last ones are still waiting to be saved), and at a stop."""
        if not self.btrfs_mode or self.pending is not None:
            return
        try:
            subs = self._home_subvolumes()
            read_at = time.monotonic()
            self.pending = (read_at, self._markers(subs), {str(sid) for sid, *_ in subs})
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"could not read generations: {e}")

    def save_markers(self) -> bool:
        """Save the waiting markers once the queue has been read to empty after they were
        read: every write before them has then been seen, and every write after them is in
        a later generation, which is where the next start's find-new begins."""
        if self.pending is None or self.empty_at <= self.pending[0]:
            return False
        _, fresh, ids = self.pending
        keep = {k: v for k, v in self.saved.items() if k in ids and k not in fresh}
        self.saved = {**keep, **{k: {**v, "gen": v["gen"] + 1} for k, v in fresh.items()}}
        self.generations.save(self.saved)
        self.pending = None
        return True

    # Loop
    def on_signal(self, signum, frame) -> None:
        self.stopping = True

    def accept(self) -> None:
        assert self.listener is not None and self.hub is not None
        while True:
            try:
                sock, _ = self.listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            sock.setblocking(False)
            try:
                uid = peer_uid(sock)
            except OSError:
                sock.close()
                continue
            if self.hub.full(uid):
                sock.close()
                continue
            c = self.hub.add(sock, uid)
            self.ep.register(c.fd, select.EPOLLIN | select.EPOLLRDHUP)

    def client_input(self, c) -> None:
        assert self.hub is not None
        try:
            data = c.sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            # Shut for sending only: it can still read, so keep sending until a send fails.
            c.eof = True
            self._interest(c)
            return
        c.inbuf += data
        while b"\n" in c.inbuf:
            line, _, rest = bytes(c.inbuf).partition(b"\n")
            c.inbuf = bytearray(rest)
            try:
                req = json.loads(line)
            except ValueError:
                continue
            if isinstance(req, dict) and req.get("op") == "ping":
                c.out += dumps({"op": "pong"})
                self.hub.dirty.add(c.fd)
        if len(c.inbuf) > 65536 or len(c.out) > self.hub.client_cap:
            self.hub.drop(c)

    def forget_client(self, c) -> None:
        try:
            self.ep.unregister(c.fd)
        except (OSError, ValueError):
            pass

    def push(self) -> None:
        """Write spools, then send what each client with pending output can take."""
        assert self.hub is not None
        self.hub.flush_spools()
        for fd in list(self.hub.dirty):
            c = self.hub.clients.get(fd)
            self.hub.dirty.discard(fd)
            if c is None:
                continue
            more = self.hub.pump(c)
            if c.fd not in self.hub.clients:
                continue
            if more != c.writing:
                c.writing = more
                self._interest(c)

    def _interest(self, c) -> None:
        mask = (0 if c.eof else select.EPOLLIN | select.EPOLLRDHUP) | (select.EPOLLOUT if c.writing else 0)
        try:
            self.ep.modify(c.fd, mask)
        except OSError:
            pass

    def run(self) -> int:
        a = self.args
        os.makedirs(a.state, mode=0o700, exist_ok=True)
        try:
            self.start_watching()
        except LookupError:
            log(f"not watching: {self.reason}")
        except OSError as e:
            self.reason = self.reason or f"could not watch: {e.strerror or e}"
            log(f"not watching: {self.reason}")
            if self.fan_fd is not None:
                os.close(self.fan_fd)
                self.fan_fd = None
        scope = self.scope
        overrides = dict(a.home or [])
        self.hub = Hub(os.path.join(a.state, "spool"), scope, default_home_of(scope.home_root, overrides),
                       spool_cap=a.spool_cap, hello=self.hello())
        self.hub.on_drop = self.forget_client
        self.listen()
        rfd, wfd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self.ep.register(rfd, select.EPOLLIN)
        signal.set_wakeup_fd(wfd)
        signal.signal(signal.SIGTERM, self.on_signal)
        signal.signal(signal.SIGINT, self.on_signal)
        if self.fan_fd is not None:
            log(f"watching {self.top} ({self.fs}) for {scope.home_root}"
                + ("" if self.forks is not None else ", without the fork tracker"))
            self.catch_up()
        next_gen = time.monotonic() + GENERATIONS_EVERY
        next_prune = time.monotonic() + 10
        while not self.stopping:
            try:
                ready = self.ep.poll(1.0)
            except InterruptedError:
                ready = []
            for fd, ev in ready:
                if fd == self.fan_fd:
                    self.read_events()
                elif self.nl is not None and fd == self.nl.fileno():
                    assert self.forks is not None
                    forkmod.drain(self.nl, self.forks)
                elif self.listener is not None and fd == self.listener.fileno():
                    self.accept()
                elif fd == self.mountinfo_fd:
                    self.refresh_mounts()
                elif fd == rfd:
                    try:
                        os.read(rfd, 512)
                    except OSError:
                        pass
                else:
                    c = self.hub.clients.get(fd)
                    if c is None:
                        continue
                    if ev & (select.EPOLLHUP | select.EPOLLERR):
                        self.hub.drop(c)
                        continue
                    if ev & (select.EPOLLIN | select.EPOLLRDHUP):
                        self.client_input(c)
                    if ev & select.EPOLLOUT and fd in self.hub.clients:
                        self.hub.dirty.add(fd)
            self.push()
            now = time.monotonic()
            if now >= next_prune:
                next_prune = now + 10
                if self.forks is not None:
                    self.forks.prune(forkmod.boottime())
            if self.pending is not None and self.fan_fd is not None:
                self.read_events()      # an idle queue reads empty at once
                self.save_markers()
            if now >= next_gen:
                next_gen = now + GENERATIONS_EVERY
                self.read_markers()
                self.push()
        return self.shutdown()

    def shutdown(self) -> int:
        assert self.hub is not None
        if self.fan_fd is not None:
            # Where the filesystem is now, then every event queued so far. If the queue
            # does not empty in time (a build still running), nothing new is saved and the
            # next start re-lists from the older generations rather than miss anything.
            self.pending = None
            self.read_markers()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.read_events(rounds=16):
                pass
            if self.btrfs_mode and not self.save_markers():
                log("the queue did not empty before the stop; the next start re-lists more")
        self.push()
        self.hub.close()
        if self.listener is not None:
            self.listener.close()
            try:
                os.unlink(self.args.socket)
            except OSError:
                pass
        log(f"stopped after {self.stats['events']} events ({self.resolver.stale if self.resolver else 0} "
            "in directories already gone)")
        return 0


def _lstat(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _idsize(st: os.stat_result | None, isdir: bool) -> dict:
    if st is None:
        return {"ino": None, "size": None}
    return {"ino": st.st_ino, "size": None if isdir else st.st_size}


def _uid_dir(text: str) -> tuple[int, str]:
    uid, sep, path = text.partition(":")
    if not sep or not uid.isdigit() or not path.startswith("/"):
        raise argparse.ArgumentTypeError("expected UID:/path")
    return int(uid), path.rstrip("/") or "/"


def raise_fd_limit(want: int = 65536) -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = want if hard == resource.RLIM_INFINITY else min(want, hard)
    if soft < target:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bombadil-brain-watch", description=__doc__.split("\n")[0])
    ap.add_argument("--path", default=os.environ.get("BOMBADIL_WATCH_PATH") or None,
                    help="watch the filesystem holding DIR directly and treat DIR as the home root "
                         "(development; no btrfs needed)")
    ap.add_argument("--socket", default=os.environ.get("BOMBADIL_WATCH_SOCKET", SOCKET))
    ap.add_argument("--state", default=os.environ.get("BOMBADIL_WATCH_STATE", STATE))
    ap.add_argument("--top", default=TOP, help="where the top-level subvolume is mounted, privately")
    ap.add_argument("--spool-cap", type=int, default=SPOOL_CAP)
    ap.add_argument("--home", type=_uid_dir, action="append", metavar="UID:DIR",
                    help="route UID's events by DIR instead of its passwd home (tests)")
    args = ap.parse_args(argv)
    if os.geteuid() != 0:
        log("needs root (fanotify filesystem marks, the top-level mount, the fork tracker)")
        return 1
    raise_fd_limit()
    return Watcher(args).run()


if __name__ == "__main__":
    sys.exit(main())

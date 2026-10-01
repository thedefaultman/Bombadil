"""Who started whom, for writers that are gone by the time their save is read.

A turn's `echo x > notes.txt` is a shell that opens, writes and exits in a millisecond; its
close-write event is even emitted while it exits, so by the time the watcher reads the event
the writer is usually reaped and /proc has nothing to say about it. The kernel's process
connector (cn_proc, netlink NETLINK_CONNECTOR, needs CAP_NET_ADMIN) reports every fork and
exit as it happens, so the watcher keeps a short memory of child -> parent and walks up it
to the nearest ancestor that is still alive: the turn's shell, still in the turn's scope.

A fork is remembered until a couple of minutes after its process exits, and the whole map
is bounded, so a build that forks a million compilers cannot grow it without limit.

The netlink parsing is pure (`parse`), so it is tested with messages built by hand; `listen`
and `drain` are the thin socket part.
"""

import errno
import os
import socket
import struct
import time
from collections import OrderedDict
from collections.abc import Callable

NETLINK_CONNECTOR = 11
CN_IDX_PROC = 1
CN_VAL_PROC = 1
PROC_CN_MCAST_LISTEN = 1
NLMSG_DONE = 3
NLMSG_ERROR = 2
NLMSG_NOOP = 1

PROC_EVENT_FORK = 0x00000001
PROC_EVENT_EXEC = 0x00000002
PROC_EVENT_EXIT = 0x80000000

NLMSGHDR = struct.Struct("=IHHII")      # len, type, flags, seq, pid: 16 bytes
CN_MSG = struct.Struct("=IIIIHH")       # cb_id{idx, val}, seq, ack, len, flags: 20 bytes
PROC_EVENT = struct.Struct("=IIQ")      # what, cpu, timestamp_ns: 16 bytes, then the union
FORK = struct.Struct("=iiii")           # parent_pid, parent_tgid, child_pid, child_tgid
EXIT = struct.Struct("=ii")             # process_pid, process_tgid (then exit code, signal...)


def parse(buf: bytes) -> list[tuple]:
    """The process events in one netlink datagram: ("fork", parent_tgid, child_tgid) for a
    new process (thread creation is left out) and ("exit", tgid) when a process ends (a
    thread's exit is left out). Anything else, or anything short, is skipped."""
    out = []
    off = 0
    while off + NLMSGHDR.size <= len(buf):
        nlen, ntype, _, _, _ = NLMSGHDR.unpack_from(buf, off)
        if nlen < NLMSGHDR.size or off + nlen > len(buf):
            break
        body = off + NLMSGHDR.size
        end = off + nlen
        if ntype not in (NLMSG_ERROR, NLMSG_NOOP) and body + CN_MSG.size + PROC_EVENT.size <= end:
            idx, val, _, _, dlen, _ = CN_MSG.unpack_from(buf, body)
            ev = body + CN_MSG.size
            if idx == CN_IDX_PROC and val == CN_VAL_PROC and ev + min(dlen, PROC_EVENT.size) <= end:
                what, _, _ = PROC_EVENT.unpack_from(buf, ev)
                data = ev + PROC_EVENT.size
                if what == PROC_EVENT_FORK and data + FORK.size <= end:
                    _, ptgid, cpid, ctgid = FORK.unpack_from(buf, data)
                    if cpid == ctgid:
                        out.append(("fork", ptgid, ctgid))
                elif what == PROC_EVENT_EXIT and data + EXIT.size <= end:
                    pid, tgid = EXIT.unpack_from(buf, data)
                    if pid == tgid:
                        out.append(("exit", tgid))
        # Messages are aligned to 4 bytes (NLMSG_ALIGN).
        off += (nlen + 3) & ~3
    return out


class ForkMap:
    """child pid -> (parent pid, when it forked), for recent forks. Times are seconds on
    CLOCK_BOOTTIME, the clock /proc/<pid>/stat's start time counts on."""

    def __init__(self, keep: float = 120.0, limit: int = 200_000):
        self.keep = keep        # how long a record outlives its process
        self.limit = limit
        self._forks: OrderedDict[int, tuple[int, float]] = OrderedDict()
        self._exits: OrderedDict[int, float] = OrderedDict()

    def __len__(self) -> int:
        return len(self._forks)

    def fork(self, parent: int, child: int, t: float) -> None:
        self._forks.pop(child, None)   # a reused pid: the new process replaces the old record
        self._exits.pop(child, None)
        self._forks[child] = (parent, t)
        while len(self._forks) > self.limit:
            old, _ = self._forks.popitem(last=False)
            self._exits.pop(old, None)

    def exit(self, pid: int, t: float) -> None:
        if pid in self._forks:
            self._exits.pop(pid, None)
            self._exits[pid] = t

    def prune(self, now: float) -> None:
        while self._exits:
            pid, t = next(iter(self._exits.items()))
            if now - t < self.keep:
                break
            self._exits.popitem(last=False)
            self._forks.pop(pid, None)

    def parent(self, pid: int) -> tuple[int, float] | None:
        return self._forks.get(pid)

    def feed(self, events: list[tuple], t: float) -> None:
        for ev in events:
            if ev[0] == "fork":
                self.fork(ev[1], ev[2], t)
            else:
                self.exit(ev[1], t)

    def ancestor(self, pid: int, alive: Callable[[int, float], bool], depth: int = 64) -> int | None:
        """The nearest live ancestor of `pid` (which is gone) through the recorded forks.
        `alive(p, t)` says whether p is running and started no later than t, the moment it
        forked the child we came from; that rules out a pid reused since."""
        for _ in range(depth):
            rec = self._forks.get(pid)
            if rec is None:
                return None
            parent, t = rec
            if parent <= 0:
                return None
            if alive(parent, t):
                return parent
            pid = parent
        return None


def boottime() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _kernel_filters() -> bool:
    """Linux 6.6 lets a listener pick its event types (struct proc_input); older kernels
    ignore that message entirely, so they get the plain listen op."""
    try:
        major, minor = (int(x) for x in os.uname().release.split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= (6, 6)


def listen() -> socket.socket:
    """A non-blocking socket that receives fork and exit events. Raises OSError without
    CAP_NET_ADMIN or without cn_proc in the kernel."""
    s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC, NETLINK_CONNECTOR)
    try:
        s.bind((0, CN_IDX_PROC))
        try:
            # A burst of forks must not overflow the socket while the watcher is busy.
            s.setsockopt(socket.SOL_SOCKET, 33, 8 << 20)   # SO_RCVBUFFORCE
        except OSError:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        if _kernel_filters():
            op = struct.pack("=II", PROC_CN_MCAST_LISTEN, PROC_EVENT_FORK | PROC_EVENT_EXIT)
        else:
            op = struct.pack("=I", PROC_CN_MCAST_LISTEN)
        cn = CN_MSG.pack(CN_IDX_PROC, CN_VAL_PROC, 0, 0, len(op), 0) + op
        s.send(NLMSGHDR.pack(NLMSGHDR.size + len(cn), NLMSG_DONE, 0, 0, s.getsockname()[0]) + cn)
        s.setblocking(False)
    except OSError:
        s.close()
        raise
    return s


def drain(sock: socket.socket, forks: ForkMap, limit: int = 100_000) -> int:
    """Read every message waiting on `sock` into `forks`. Returns how many were read."""
    n = 0
    while n < limit:
        try:
            buf = sock.recv(65536)
        except (BlockingIOError, InterruptedError):
            break
        except OSError as e:
            if e.errno == errno.ENOBUFS:
                continue    # the kernel dropped some messages: those forks are unknown; go on
            break
        if not buf:
            break
        forks.feed(parse(buf), boottime())
        n += 1
    return n

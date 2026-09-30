import argparse
import errno
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from bombadil.brain import fanotify as fan
from bombadil.brain import forks
from bombadil.brain import service
from bombadil.brain import watch as W

TOP = "/run/bombadil-brain/top"
# The VM's mountinfo (tests/vm/btrfs-kernel.sh) as the watcher sees it in its own namespace.
BTRFS = r"""24 28 0:22 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw
37 36 0:25 /@ / rw,relatime - btrfs /dev/vda rw,compress=zstd:3,subvolid=256,subvol=/@
44 37 0:24 / /run rw,nosuid,nodev,relatime - tmpfs run rw,mode=755
46 37 0:25 /@home /home rw,relatime - btrfs /dev/vda rw,compress=zstd:3,subvolid=257,subvol=/@home
47 37 0:25 /@snapshots /.snapshots rw,relatime - btrfs /dev/vda rw,subvolid=259,subvol=/@snapshots
49 44 0:25 / /run/bombadil-brain/top ro,relatime - btrfs /dev/vda rw,subvolid=5,subvol=/
50 37 0:40 / /mnt/with\040space rw - ext4 /dev/vdb rw
"""
EXT4 = """28 1 254:0 / / rw,relatime - ext4 /dev/vda rw
50 28 254:0 /var/lib/docker /var/lib/docker rw,relatime shared:1 - ext4 /dev/vda rw
51 28 254:0 /srv/data /mnt/data rw - ext4 /dev/vda rw
"""


def btrfs_map() -> W.PathMap:
    mounts = W.parse_mountinfo(BTRFS)
    own = next(m for m in mounts if m.point == TOP)
    return W.PathMap(mounts, own, exclude_own=True)


# --- mounts and paths ---

def test_parse_mountinfo_and_mount_of():
    mounts = W.parse_mountinfo(BTRFS + "garbage line\n")
    assert len(mounts) == 7
    home = W.mount_of(mounts, "/home/user/x")
    assert (home.root, home.point, home.fstype, home.source, home.dev) == (
        "/@home", "/home", "btrfs", "/dev/vda", "0:25")
    assert W.mount_of(mounts, "/mnt/with space/f").fstype == "ext4"
    assert W.mount_of(mounts, "/homework").point == "/"


def test_private_top_paths_map_to_real_paths():
    pm = btrfs_map()
    assert pm.real(f"{TOP}/@home/user/a.txt") == "/home/user/a.txt"
    assert pm.real(f"{TOP}/@home/user/Projects/p/x.py") == "/home/user/Projects/p/x.py"   # nested subvolume
    assert pm.real(f"{TOP}/@home") == "/home"
    assert pm.real(f"{TOP}/@/etc/pacman.conf") == "/etc/pacman.conf"
    assert pm.real(f"{TOP}/@/var/log/pacman.log") == "/var/log/pacman.log"
    assert pm.real(f"{TOP}/@snapshots/3/snapshot") == "/.snapshots/3/snapshot"
    assert pm.real(f"{TOP}/@homework/x") is None      # not /@home: no prefix match on a partial name
    assert pm.real(f"{TOP}/@old/x") is None           # a subvolume nobody mounts
    assert pm.real(TOP) is None
    assert pm.real("/run/bombadil-brain/topx/@home/a") is None
    assert pm.real("/home/user/a") is None            # not under the private mount at all


def test_a_bind_mount_does_not_move_events_out_of_home():
    mounts = W.parse_mountinfo(BTRFS + "60 37 0:25 /@home/user/Projects /srv/proj rw - btrfs /dev/vda rw\n")
    own = next(m for m in mounts if m.point == TOP)
    assert W.PathMap(mounts, own, True).real(f"{TOP}/@home/user/Projects/x") == "/srv/proj/x"
    pm = W.PathMap(mounts, own, True, W.Scope().near)
    assert pm.real(f"{TOP}/@home/user/Projects/x") == "/home/user/Projects/x"
    assert pm.real(f"{TOP}/@/usr/bin/x") == "/usr/bin/x"            # nothing preferred: the longest root
    assert W.Scope().near("/var/log") and W.Scope().near("/etc") and not W.Scope().near("/usr")


def test_mount_changes_are_picked_up(tmp_path):
    w, _ = watcher(tmp_path, {})
    info = tmp_path / "mountinfo"
    before = ("49 44 0:25 / /run/bombadil-brain/top ro - btrfs /dev/vda rw\n"
              "37 1 0:25 /@ / rw - btrfs /dev/vda rw\n")
    info.write_text(before)
    w.mountinfo_fd = os.open(info, os.O_RDONLY)
    try:
        mounts = W.parse_mountinfo(before)
        w.own = mounts[0]
        w._exclude_own = True
        w.pathmap = W.PathMap(mounts, w.own, True, w.scope.near)
        # /home is not mounted yet (on @ it would be /home, empty): @home has nowhere to go.
        assert w.pathmap.real(f"{TOP}/@home/user/a") is None
        info.write_text(before + "46 37 0:25 /@home /home rw - btrfs /dev/vda rw\n")
        w.resolver.cleared = 0
        w.refresh_mounts()
        assert w.pathmap.real(f"{TOP}/@home/user/a") == "/home/user/a"
        assert w.resolver.pathmap is w.pathmap and w.resolver.cleared == 1
    finally:
        os.close(w.mountinfo_fd)


def test_path_mode_maps_through_the_longest_root():
    mounts = W.parse_mountinfo(EXT4)
    own = W.mount_of(mounts, "/tmp/w")
    pm = W.PathMap(mounts, own, exclude_own=False)
    assert pm.real("/tmp/w/user/a") == "/tmp/w/user/a"
    assert pm.real("/srv/data/x") == "/mnt/data/x"     # the bind mount's root is the longer prefix
    assert pm.real("/var/lib/docker/y") == "/var/lib/docker/y"
    assert pm.real("/") == "/"


def test_under_tail_join():
    assert W.under("/home/user", "/home") and W.under("/home", "/home") and not W.under("/homes", "/home")
    assert W.under("/x", "/")
    assert W._tail("/a/b", "/") == "/a/b" and W._tail("/", "/") == "" and W._tail("a", "/") is None
    assert W._join("/", "") == "/" and W._join("/", "/a") == "/a" and W._join("/h", "/a") == "/h/a"


# --- what is sent ---

def test_scope_keep_system_and_noise():
    s = W.Scope()
    assert s.keep("/home/user/a.txt") and s.keep("/etc/hosts") and s.keep("/var/log/pacman.log")
    assert not s.keep("/home") and not s.keep("/var/log/pacman.log.1") and not s.keep("/etc")
    assert not s.keep("/usr/bin/x") and not s.keep("/var/tmp/x") and not s.keep("/homer/x")
    noise = ["/home/user/.cache/x", "/home/user/.local/share/Trash/expunged/1/f",
             "/home/user/p/.git/objects/ab/cd", "/home/user/.git/objects/ab",
             "/home/user/web/node_modules/x/y.js", "/home/user/p/__pycache__/m.pyc",
             "/home/user/node_modules/z"]
    kept = ["/home/user/.cache", "/home/user/p/.git/index", "/home/user/p/.git/objects",
            "/home/user/a/.cache/x", "/home/user/node_modules", "/home/user/Trash/expunged/x",
            "/etc/node_modules/x"]
    for p in noise:
        assert s.noise(p), p
    for p in kept:
        assert not s.noise(p), p
    dev = W.Scope("/tmp/w")
    assert dev.keep("/tmp/w/user/f") and dev.noise("/tmp/w/user/.cache/f") and not dev.keep("/home/user/f")


def test_json_lines_round_trip_undecodable_bytes_and_newlines():
    raw = b"/home/user/bad\xff\xfe\nname \xe2\x9c\x93"
    line = W.dumps({"op": "write", "path": os.fsdecode(raw)})
    assert line.endswith(b"\n") and line.count(b"\n") == 1 and line.isascii()
    assert os.fsencode(json.loads(line)["path"]) == raw


def test_event_line_splices_the_who_members_in_order():
    who = W._members({"pid": 1, "uid": 0, "comm": "c", "cgroup": "/", "chain": [[1, "c", "c"]],
                      "gone": False})
    line = W.event_line({"op": "rename", "t": 1.5, "path": "/home/u/b", "old": "/home/u/a", "dir": False,
                         "ino": None, "size": None}, who)
    e = json.loads(line)
    assert list(e) == ["op", "t", "path", "old", "dir", "ino", "size",
                       "pid", "uid", "comm", "cgroup", "chain", "gone"]


# --- who wrote it ---

def test_parse_proc_files():
    stat = b"42 (a) (b c) S 7 42 42 0 -1 4194304 1 2 3 4 5 6 7 8 20 0 1 0 999 12 34"
    assert W.parse_stat(stat) == ("a) (b c", 7, 999)
    assert W.parse_stat(b"garbage") is None
    cg = (b"12:memory:/x\n1:name=systemd:/user.slice\n"
          b"0::/user.slice/user-1000.slice/app.slice/bombadil-turn-9-3-1.scope\n")
    assert W.parse_cgroup(cg) == "/user.slice/user-1000.slice/app.slice/bombadil-turn-9-3-1.scope"
    assert W.parse_cgroup(b"1:cpu:/\n") == "" and W.parse_cgroup(None) == ""
    assert W.parse_uid(b"Name:\tx\nUid:\t1000\t1000\t1000\t1000\n") == 1000
    assert W.parse_uid(b"Name:\tx\n") is None


def fake_proc(root: Path, pid: int, ppid: int, comm: str, start: int = 100, uid: int = 1000,
              cgroup: str = "/", argv: list[str] | None = None) -> None:
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start), "0"]
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in (argv or [comm])) + b"\0")
    (d / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    (d / "cgroup").write_text(f"0::{cgroup}\n")


def test_procs_chain_stops_after_pid_1_and_at_ten(tmp_path):
    fake_proc(tmp_path, 1, 0, "systemd", start=1)
    fake_proc(tmp_path, 50, 1, "bash", argv=["bash", "-c", "x" * 500])
    fake_proc(tmp_path, 60, 50, "cp", cgroup="/app.slice/bombadil-turn-1-2-3.scope", argv=["cp", "a b", "c"])
    procs = W.Procs(proc=str(tmp_path))
    p = procs.info(60)
    assert (p.pid, p.ppid, p.comm, p.uid) == (60, 50, "cp", 1000)
    assert p.cgroup == "/app.slice/bombadil-turn-1-2-3.scope"
    chain = procs.chain(p, time.monotonic())
    assert [c[0] for c in chain] == [60, 50, 1]
    assert chain[0][2] == "cp a b c" and len(chain[1][2]) == W.CMD_MAX
    for pid in range(100, 115):
        fake_proc(tmp_path, pid, pid - 1 if pid > 100 else 1, f"p{pid}")
    assert len(procs.chain(procs.info(114), time.monotonic())) == W.CHAIN_MAX
    assert procs.info(12345) is None
    hz = procs.hz
    assert procs.started_by(50, 100 / hz) and not procs.started_by(50, 99 / hz)
    assert not procs.started_by(7, 1e9)


def test_attribution_live_gone_and_unknown(tmp_path):
    fake_proc(tmp_path, 1, 0, "init", start=1, uid=0)
    fake_proc(tmp_path, 50, 1, "bash", start=100, cgroup="/turn.scope")
    procs = W.Procs(proc=str(tmp_path))
    fm = forks.ForkMap()
    attr = W.Attribution(procs, fm)
    # No pidfd to check against (older kernels): /proc is taken as it is.
    who = attr.who(50, None)
    assert who["pid"] == 50 and who["cgroup"] == "/turn.scope" and who["gone"] is False and who["uid"] == 1000
    # Reaped before the event was read: the nearest live ancestor through the fork map.
    fm.fork(50, 77, t=1e6)
    who = attr.who(77, fan.FAN_NOPIDFD)
    assert who["pid"] == 50 and who["gone"] is True and who["cgroup"] == "/turn.scope"
    assert json.loads("{" + attr.who_json(77, fan.FAN_NOPIDFD) + "}") == who
    # Even if /proc/77 exists now, NOPIDFD means it is someone else.
    fake_proc(tmp_path, 77, 1, "impostor", cgroup="/other")
    assert attr.who(77, fan.FAN_NOPIDFD)["pid"] == 50
    # Nothing known.
    assert attr.who(88, fan.FAN_NOPIDFD) == {"pid": 88, "uid": None, "comm": "", "cgroup": "", "chain": [],
                                             "gone": True}
    assert W.Attribution(procs, None).who(88, fan.FAN_NOPIDFD)["gone"] is True


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="no pidfd_open")
def test_a_dead_pidfd_means_proc_is_not_trusted(tmp_path):
    p = subprocess.Popen(["true"])
    fd = os.pidfd_open(p.pid)
    p.wait()
    assert not W.pidfd_alive(fd)
    fake_proc(tmp_path, p.pid, 1, "reused")
    who = W.Attribution(W.Procs(proc=str(tmp_path)), forks.ForkMap()).who(p.pid, fd)
    os.close(fd)
    assert who["gone"] is True and who["comm"] == ""
    me = os.pidfd_open(os.getpid())
    assert W.pidfd_alive(me)
    os.close(me)


# --- handles to paths ---

def test_resolver_caches_and_drops_gone_directories(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "gone").mkdir()
    dirs = {b"H1": tmp_path / "d", b"H2": tmp_path / "gone"}
    opened = []

    def opener(mount_fd, handle):
        opened.append(handle)
        if handle not in dirs:
            raise OSError(errno.ESTALE, "stale")
        return os.open(dirs[handle], os.O_PATH)

    mounts = W.parse_mountinfo("1 0 254:0 / / rw - ext4 /dev/vda rw\n")
    pm = W.PathMap(mounts, mounts[0], exclude_own=False)
    r = W.Resolver(-1, pm, opener=opener)
    assert r.path(b"H1", b"a.txt") == (f"{tmp_path}/d/a.txt", f"{tmp_path}/d/a.txt")
    assert r.path(b"H1", b"b.txt")[1] == f"{tmp_path}/d/b.txt"
    assert opened == [b"H1"]                        # cached
    assert r.path(b"H1", b".")[1] == f"{tmp_path}/d"
    assert r.path(b"H1", b"x\xff")[1] == f"{tmp_path}/d/x\udcff"
    assert r.path(b"NOPE", b"a") is None and r.stale == 1
    r.clear()
    r.path(b"H1", b"a")
    assert opened == [b"H1", b"NOPE", b"H1"]
    fd = os.open(tmp_path / "gone", os.O_PATH)       # keep it open so the handle still opens
    os.rmdir(tmp_path / "gone")
    dirs[b"H2"] = f"/proc/self/fd/{fd}"
    assert r.path(b"H2", b"f") is None
    os.close(fd)
    assert r.path(None, b"x") is None


# --- clients and spools ---

class Peer:
    """The brain's end of a socketpair."""

    def __init__(self):
        self.mine, self.theirs = socket.socketpair()
        self.mine.setblocking(False)
        self.buf = b""

    def read(self) -> list[dict]:
        self.theirs.settimeout(0.2)
        try:
            while True:
                b = self.theirs.recv(1 << 20)
                if not b:
                    break
                self.buf += b
        except (socket.timeout, BlockingIOError):
            pass
        lines, _, self.buf = self.buf.rpartition(b"\n")
        return [json.loads(x) for x in lines.split(b"\n") if x] if lines else []


HOMES = {1000: "/home/user", 1001: "/home/other"}


def hub(tmp_path, **kw) -> W.Hub:
    return W.Hub(str(tmp_path / "spool"), W.Scope(), HOMES.get, hello=W.dumps({"op": "hello"}), **kw)


def ev(path: str, n: int = 0, old: str | None = None) -> tuple[bytes, tuple]:
    obj = {"op": "rename" if old else "write", "n": n, "path": path}
    if old:
        obj["old"] = old
    return W.dumps(obj), (path, old) if old else (path,)


def pump_all(h: W.Hub) -> None:
    """Send what each client's socket takes now (pump says True while more is waiting)."""
    h.flush_spools()
    for c in list(h.clients.values()):
        for _ in range(50):
            if not h.pump(c):
                break


def test_routing_by_uid(tmp_path):
    h = hub(tmp_path)
    u, o, r = Peer(), Peer(), Peer()
    h.add(u.mine, 1000)
    h.add(o.mine, 1001)
    h.add(r.mine, 0)
    for line, paths in (ev("/home/user/a"), ev("/home/other/b"), ev("/etc/hosts"), ev("/home/userx/c"),
                        ev("/home/other/in", old="/home/user/out")):
        h.deliver(line, paths)
    h.broadcast(W.dumps({"op": "caught_up"}))
    pump_all(h)
    def paths(p):
        return [(e["op"], e.get("path")) for e in p.read()]
    assert paths(u) == [("hello", None), ("write", "/home/user/a"), ("write", "/etc/hosts"),
                        ("rename", "/home/other/in"), ("caught_up", None)]
    assert paths(o) == [("hello", None), ("write", "/home/other/b"), ("write", "/etc/hosts"),
                        ("rename", "/home/other/in"), ("caught_up", None)]
    assert len(paths(r)) == 7


def test_spool_while_away_then_spooled_lines_before_live_ones(tmp_path):
    h = hub(tmp_path)
    first = Peer()
    c = h.add(first.mine, 1000)            # connecting once makes the user known
    pump_all(h)
    h.drop(c)
    for i in range(5):
        h.deliver(*ev("/home/user/f", i))
    h.deliver(*ev("/home/other/g", 99))    # not theirs: not spooled
    h.flush_spools()
    spool = tmp_path / "spool" / "1000.jsonl"
    assert [json.loads(x)["n"] for x in spool.read_text().splitlines()] == [0, 1, 2, 3, 4]
    p = Peer()
    h.add(p.mine, 1000)
    h.deliver(*ev("/home/user/f", 5))      # arrives while the spool is being read: queued behind it
    pump_all(h)
    h.deliver(*ev("/home/user/f", 6))      # after the spool: live
    pump_all(h)
    got = p.read()
    assert got[0]["op"] == "hello" and [e["n"] for e in got[1:]] == [0, 1, 2, 3, 4, 5, 6]
    assert spool.read_bytes() == b""
    # Root is never spooled.
    h.deliver(*ev("/etc/x", 7))
    h.flush_spools()
    assert not (tmp_path / "spool" / "0.jsonl").exists()


def test_known_users_come_from_spool_files(tmp_path):
    (tmp_path / "spool").mkdir()
    (tmp_path / "spool" / "1000.jsonl").write_text("")
    (tmp_path / "spool" / "0.jsonl").write_text("")
    h = hub(tmp_path)
    assert set(h.spools) == {1000}


def test_spool_cap_truncates_and_says_overflow(tmp_path):
    h = hub(tmp_path, spool_cap=400)
    c = h.add(Peer().mine, 1000)
    h.drop(c)
    for i in range(20):
        h.deliver(*ev("/home/user/f", i))
        h.flush_spools()
    lines = [json.loads(x) for x in (tmp_path / "spool" / "1000.jsonl").read_text().splitlines()]
    assert (tmp_path / "spool" / "1000.jsonl").stat().st_size <= 400
    assert lines[0]["op"] == "overflow" and lines[-1]["n"] == 19
    assert [e["n"] for e in lines[1:]] == list(range(20 - len(lines) + 1, 20))


def test_a_client_that_stops_reading_moves_to_its_spool_and_loses_nothing(tmp_path):
    h = hub(tmp_path, client_cap=2000)
    p = Peer()
    p.mine.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    c = h.add(p.mine, 1000)
    for i in range(400):
        h.deliver(*ev("/home/user/f", i))
        h.flush_spools()
        if i % 7 == 0:
            h.pump(c)                       # the socket fills and stays full
    # Reading from disk now: the queue holds at most what two reads of the spool bring in.
    assert c.rfd is not None and len(c.out) <= 2 * W.SPOOL_CHUNK
    got = []
    for _ in range(200):
        pump_all(h)
        got += p.read()
        if c.rfd is None and len(got) >= 401:
            break
    assert [e.get("n") for e in got[1:]] == list(range(400))
    h.deliver(*ev("/home/user/f", 400))    # and live again afterwards
    pump_all(h)
    assert [e["n"] for e in p.read()] == [400]


def test_root_that_stops_reading_is_dropped(tmp_path):
    h = hub(tmp_path, client_cap=2000)
    p = Peer()
    p.mine.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    dropped = []
    h.on_drop = dropped.append
    c = h.add(p.mine, 0)
    for i in range(400):
        h.deliver(*ev("/home/user/f", i))
        if i % 7 == 0 and c.fd in h.clients:
            h.pump(c)
    assert dropped == [c] and not h.clients


def test_what_a_leaving_client_never_got_goes_back_to_its_spool(tmp_path):
    h = hub(tmp_path)
    p = Peer()
    c = h.add(p.mine, 1000)
    pump_all(h)
    p.read()
    h.deliver(*ev("/home/user/f", 1))
    h.deliver(*ev("/home/user/f", 2))
    c.partial = b'{"op":"write","n":0,'        # a line it got half of
    c.out[:0] = b'"path":"/home/user/f"}\n'
    h.drop(c)
    h.flush_spools()
    lines = (tmp_path / "spool" / "1000.jsonl").read_text().splitlines()
    assert [json.loads(x)["n"] for x in lines] == [0, 1, 2]


# --- btrfs output ---

def test_parse_find_new_and_subvolume_list():
    out = [b"inode 257 file offset 0 len 3 disk start 0 offset 0 gen 12 flags INLINE user/a.txt\n",
           b"inode 258 file offset 0 len 4096 disk start 13631488 offset 0 gen 12 flags COMPRESS|PREALLOC "
           b"user/with space/b \xff.txt\n",
           b"inode 258 file offset 4096 len 4096 disk start 1 offset 0 gen 13 flags NONE "
           b"user/with space/b \xff.txt\n",
           b"transid marker was 15\n"]
    got = list(W.parse_find_new(out))
    assert got[0] == ("user/a.txt", None) and got[1][0] == os.fsdecode(b"user/with space/b \xff.txt")
    assert got[-1] == ("", 15) and len(got) == 4
    subs = W.parse_subvolumes(b"ID 256 gen 6 top level 5 path @\n"
                              b"ID 258 gen 6 top level 257 path @home/user/Projects\n"
                              b"ID 260 gen 9 top level 5 path @home/with space\n")
    assert subs == [(256, "@"), (258, "@home/user/Projects"), (260, "@home/with space")]


def test_generations_save_and_load(tmp_path):
    g = W.Generations(str(tmp_path / "generations.json"))
    assert g.load() == {}
    g.save({"257": {"path": "@home", "gen": 15}})
    assert g.load() == {"257": {"path": "@home", "gen": 15}}
    (tmp_path / "generations.json").write_text("{not json")
    assert g.load() == {}


# --- one event through the watcher ---

class FakeResolver:
    def __init__(self, dirs):
        self.dirs = dirs
        self.cleared = 0

    def clear(self):
        self.cleared += 1

    def path(self, handle, name):
        if handle not in self.dirs:
            return None
        priv, real = self.dirs[handle]
        n = os.fsdecode(name) if name else ""
        return (f"{priv}/{n}", f"{real}/{n}") if n else (priv, real)


class Capture:
    """The hub, for a watcher under test: `sent` is the events the brain would read (batches
    unpacked the way it unpacks them), `lines` the lines as they went out."""

    def __init__(self):
        self.sent = []
        self.lines = []

    def deliver(self, line, paths):
        self.sent.append(json.loads(line))

    def deliver_run(self, who, run):
        heads = [head for head, _paths in run]
        line = W.batch_line(who, heads) if len(heads) > 1 else ("{" + heads[0] + "," + who + "}\n").encode()
        self.lines.append(line)
        self.sent.extend(service._unbatch(json.loads(line)))

    def broadcast(self, line):
        self.sent.append(json.loads(line))

    def flush_spools(self):
        pass


def watcher(tmp_path, dirs) -> tuple[W.Watcher, Capture]:
    args = argparse.Namespace(path=None, socket=str(tmp_path / "s"), state=str(tmp_path), top=TOP,
                              spool_cap=W.SPOOL_CAP, home=None)
    w = W.Watcher(args)
    w.resolver = FakeResolver(dirs)
    w.hub = cap = Capture()
    w.attr = W.Attribution(W.Procs(proc=str(tmp_path / "noproc")), None)
    return w, cap


def fev(mask, name=b"f", handle=b"H", pid=4321, **kw) -> fan.Event:
    return fan.Event(mask=mask, pid=pid, dir=handle, name=name, pidfd=fan.FAN_NOPIDFD, **kw)


def test_one_event_to_lines(tmp_path):
    home = tmp_path / "priv"
    (home / "user").mkdir(parents=True)
    (home / "user" / "exists").write_text("12345")
    dirs = {b"H": (str(home / "user"), "/home/user"), b"C": (str(home / "user"), "/home/user/.cache"),
            b"V": ("/x", "/var/tmp")}
    w, cap = watcher(tmp_path, dirs)
    now = 1.0
    w.handle([fev(fan.FAN_CREATE | fan.FAN_CLOSE_WRITE, b"exists")], now)
    assert [(e["op"], e["path"], e["size"]) for e in cap.sent] == [("create", "/home/user/exists", 5),
                                                                   ("write", "/home/user/exists", 5)]
    assert cap.sent[0]["gone"] is True and cap.sent[0]["pid"] == 4321
    cap.sent.clear()
    # A merged create + delete: the order follows whether it is there now.
    w.handle([fev(fan.FAN_CREATE | fan.FAN_DELETE, b"tmp")], now)
    w.handle([fev(fan.FAN_CREATE | fan.FAN_DELETE, b"exists")], now)
    assert [e["op"] for e in cap.sent] == ["create", "delete", "delete", "create"]
    assert cap.sent[1]["ino"] is None
    cap.sent.clear()
    # Noise, outside, and the watcher's own writes are never sent; renames into noise are.
    w.handle([fev(fan.FAN_CLOSE_WRITE, handle=b"C"), fev(fan.FAN_CLOSE_WRITE, handle=b"V"),
              fev(fan.FAN_CLOSE_WRITE, pid=os.getpid())], now)
    assert cap.sent == []
    w.handle([fan.Event(mask=fan.FAN_RENAME, pid=1, old_dir=b"H", old_name=b"a",
                        new_dir=b"C", new_name=b"b")], now)
    assert cap.sent[0]["op"] == "rename" and cap.sent[0]["old"] == "/home/user/a"
    cap.sent.clear()

    def rename(old_dir, old_name, new_dir, new_name):
        return fan.Event(mask=fan.FAN_RENAME, pid=1, old_dir=old_dir, old_name=old_name,
                         new_dir=new_dir, new_name=new_name)

    # A rename whose one side cannot be named any more: create or delete.
    w.handle([rename(b"GONE", b"a", b"H", b"exists"),
              rename(b"H", b"a", b"GONE", b"b"),
              rename(b"V", b"a", b"V", b"b")], now)
    # (and a rename that stays outside is not sent at all)
    assert [(e["op"], e["path"]) for e in cap.sent] == [("create", "/home/user/exists"),
                                                        ("delete", "/home/user/a")]
    assert cap.sent[0]["size"] == 5 and cap.sent[1]["ino"] is None
    cap.sent.clear()
    # Directory deletes and renames drop the handle cache; overflow goes to everyone.
    before = w.resolver.cleared
    w.handle([fev(fan.FAN_DELETE | fan.FAN_ONDIR, b"d"),
              fan.Event(mask=fan.FAN_RENAME | fan.FAN_ONDIR, pid=1, old_dir=b"H", old_name=b"a", new_dir=b"H",
                        new_name=b"b"),
              fan.Event(mask=fan.FAN_Q_OVERFLOW, pid=0)], now)
    assert w.resolver.cleared == before + 2
    assert [e["op"] for e in cap.sent] == ["delete", "rename", "overflow"] and cap.sent[0]["dir"] is True


def test_event_fds_are_closed_even_when_dropped(tmp_path):
    w, cap = watcher(tmp_path, {b"V": ("/x", "/var/tmp")})
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    os.close(w1)
    os.close(w2)
    w.handle([fan.Event(mask=fan.FAN_CLOSE_WRITE, pid=1, dir=b"V", name=b"f", pidfd=r1),
              fan.Event(mask=fan.FAN_CLOSE_WRITE, pid=1, dir=b"BAD", name=b"f", pidfd=r2)], 1.0)
    for fd in (r1, r2):
        with pytest.raises(OSError):
            os.fstat(fd)


# --- writers in a row share one line ---

CHAIN = [[100 + i, "sh", "sh -c " + "x" * 90] for i in range(W.CHAIN_MAX)]


def _long_who(w) -> str:
    """A writer with ten ancestors, each a command line: the who-block that used to repeat on every line."""
    who = W._members({"pid": 4321, "uid": 1000, "comm": "make", "cgroup": "/user.slice/x.scope", "chain": CHAIN,
                      "gone": False})
    w.attr.who_json = lambda pid, pidfd, now=None: who
    return who


def test_events_by_one_writer_in_a_row_go_out_as_one_line(tmp_path):
    w, cap = watcher(tmp_path, {b"H": ("/x", "/home/user")})
    _long_who(w)
    w.handle([fev(fan.FAN_CLOSE_WRITE, b"a"), fev(fan.FAN_CLOSE_WRITE, b"b"), fev(fan.FAN_CLOSE_WRITE, b"c")], 1.0)
    assert len(cap.lines) == 1
    line = json.loads(cap.lines[0])
    assert line["op"] == "batch" and line["chain"] == CHAIN and line["uid"] == 1000
    assert [e["path"] for e in line["events"]] == ["/home/user/a", "/home/user/b", "/home/user/c"]
    assert all("chain" not in e and "pid" not in e for e in line["events"])
    # What the brain reads is each event with the writer, as before.
    assert [(e["op"], e["path"], e["pid"], e["chain"]) for e in cap.sent] == [
        ("write", f"/home/user/{n}", 4321, CHAIN) for n in "abc"]


def test_one_event_is_an_ordinary_line_and_a_new_writer_starts_a_new_line_in_order(tmp_path):
    w, cap = watcher(tmp_path, {b"H": ("/x", "/home/user")})
    w.attr.who_json = lambda pid, pidfd, now=None: W._members({"pid": pid, "uid": 1000, "comm": "c", "cgroup": "",
                                                                   "chain": [], "gone": False})
    w.handle([fev(fan.FAN_CLOSE_WRITE, b"1", pid=10), fev(fan.FAN_CLOSE_WRITE, b"2", pid=10),
              fev(fan.FAN_CLOSE_WRITE, b"3", pid=20), fev(fan.FAN_CLOSE_WRITE, b"4", pid=10)], 1.0)
    assert [json.loads(x)["op"] for x in cap.lines] == ["batch", "write", "write"]   # 1+2, 3, 4
    # The order of events across writers is the order they happened in.
    assert [(e["path"], e["pid"]) for e in cap.sent] == [("/home/user/1", 10), ("/home/user/2", 10),
                                                         ("/home/user/3", 20), ("/home/user/4", 10)]


def test_a_run_is_cut_at_the_event_limit_and_at_the_byte_limit(tmp_path, monkeypatch):
    w, cap = watcher(tmp_path, {b"H": ("/x", "/home/user")})
    _long_who(w)
    monkeypatch.setattr(W, "RUN_EVENTS", 4)
    w.handle([fev(fan.FAN_CLOSE_WRITE, f"f{i}".encode()) for i in range(10)], 1.0)
    assert [len(json.loads(x)["events"]) for x in cap.lines] == [4, 4, 2]
    assert [e["path"] for e in cap.sent] == [f"/home/user/f{i}" for i in range(10)]
    cap.lines.clear()
    monkeypatch.setattr(W, "RUN_EVENTS", 1000)
    monkeypatch.setattr(W, "RUN_BYTES", 300)
    w.handle([fev(fan.FAN_CLOSE_WRITE, f"g{i}".encode()) for i in range(10)], 1.0)
    assert len(cap.lines) > 1 and all(len(x) < 1000 + 600 for x in cap.lines[:-1])


def test_a_20000_file_write_fits_the_clients_buffer(tmp_path):
    """One process writing 20,000 files was 56 MB of lines (its ten ancestors' command lines on
    each), more than a client's buffer and most of the spool; now the ancestors go once per read."""
    w, cap = watcher(tmp_path, {b"H": ("/x", "/home/user")})
    who = _long_who(w)
    flat = 0
    for start in range(0, 20000, 700):       # about what one read of the kernel's queue holds
        w.handle([fev(fan.FAN_CREATE | fan.FAN_CLOSE_WRITE, f"file-{i}.o".encode())
                  for i in range(start, min(start + 700, 20000))], 1.0)
    for e in cap.sent:
        flat += len(W.event_line({k: v for k, v in e.items() if k in ("op", "t", "path", "dir", "ino", "size")}, who))
    sent = sum(len(x) for x in cap.lines)
    assert len(cap.sent) == 40000 and flat > 40 << 20
    assert sent < W.CLIENT_CAP and sent * 8 < flat


def test_overflow_and_caught_up_come_after_the_events_before_them(tmp_path):
    w, cap = watcher(tmp_path, {b"H": ("/x", "/home/user")})
    w.handle([fev(fan.FAN_CLOSE_WRITE, b"a"), fev(fan.FAN_CLOSE_WRITE, b"b"),
              fan.Event(mask=fan.FAN_Q_OVERFLOW, pid=0), fev(fan.FAN_CLOSE_WRITE, b"c")], 1.0)
    assert [e["op"] for e in cap.sent] == ["write", "write", "overflow", "write"]
    assert [e.get("path") for e in cap.sent] == ["/home/user/a", "/home/user/b", None, "/home/user/c"]


def test_a_run_that_cannot_be_delivered_tells_the_brain_to_walk_and_does_not_stop_the_watcher(tmp_path, capsys):
    w, cap = watcher(tmp_path, {b"H": ("/x", "/home/user")})
    cap.deliver_run = lambda who, run: (_ for _ in ()).throw(OSError("disk full"))
    w.handle([fev(fan.FAN_CLOSE_WRITE, b"a"), fev(fan.FAN_CLOSE_WRITE, b"b")], 1.0)
    assert [e["op"] for e in cap.sent] == ["overflow"]
    assert "could not deliver 2 events" in capsys.readouterr().err
    cap.deliver_run = lambda who, run: None
    w.handle([fev(fan.FAN_CLOSE_WRITE, b"c")], 2.0)      # and on it goes
    assert w._run == [] and w.stats["sent"] == 3


def test_the_hub_gives_each_user_their_own_events_of_a_run_in_one_line(tmp_path):
    h = hub(tmp_path)
    u, o = Peer(), Peer()
    h.add(u.mine, 1000)
    h.add(o.mine, 1001)
    who = W._members({"pid": 7, "uid": 0, "comm": "cp", "cgroup": "", "chain": [[7, "cp", "cp a b"]], "gone": False})
    run = [(W._members({"op": "write", "t": 1.0, "path": p}), (p,))
           for p in ("/home/user/a", "/home/other/b", "/home/user/c", "/etc/hosts", "/home/userx/d")]
    h.deliver_run(who, run)
    pump_all(h)
    mine, theirs = u.read(), o.read()
    assert [e["op"] for e in mine] == ["hello", "batch"] and [e["op"] for e in theirs] == ["hello", "batch"]
    assert [e["path"] for e in mine[1]["events"]] == ["/home/user/a", "/home/user/c", "/etc/hosts"]
    assert [e["path"] for e in theirs[1]["events"]] == ["/home/other/b", "/etc/hosts"]
    assert mine[1]["chain"] == [[7, "cp", "cp a b"]] and "path" not in mine[1]
    # One event for a user is an ordinary line.
    h.deliver_run(who, run[:2])
    pump_all(h)
    assert [(e["op"], e["path"], e["pid"]) for e in u.read()] == [("write", "/home/user/a", 7)]


def test_unbatch_reads_batches_and_leaves_other_lines_alone():
    flat = {"op": "write", "path": "/home/u/a", "pid": 1}
    assert service._unbatch(flat) == [flat]
    batch = {"op": "batch", "pid": 9, "uid": 1000, "comm": "make", "cgroup": "/c", "chain": [[9, "make", "make"]],
             "gone": True, "events": [{"op": "create", "t": 1.0, "path": "/home/u/a"},
                                      {"op": "write", "t": 2.0, "path": "/home/u/a", "size": 3}, "junk"]}
    out = service._unbatch(batch)
    assert [(e["op"], e["t"], e["pid"], e["comm"], e["gone"], e["chain"]) for e in out] == [
        ("create", 1.0, 9, "make", True, [[9, "make", "make"]]), ("write", 2.0, 9, "make", True, [[9, "make", "make"]])]
    assert out[1]["size"] == 3
    assert service._unbatch({"op": "batch", "pid": 1, "events": "no"}) == []
    # A batch names its writer the way an event does, so the brain names it the same.
    from bombadil.brain import actors
    assert actors.from_event(out[0]).kind == "you"


# --- the whole service, on this machine's filesystem ---

def _cgroup2() -> str | None:
    for m in W.read_mountinfo():
        if m.fstype == "cgroup2":
            return m.point
    return None


CLIENT = ("import socket, sys\ns = socket.socket(socket.AF_UNIX)\ns.connect(sys.argv[1])\n"
          "while True:\n    b = s.recv(65536)\n    if not b: break\n    sys.stdout.buffer.write(b)\n"
          "    sys.stdout.buffer.flush()\n")


class Client:
    def __init__(self, sock: str, uid: int | None = None):
        cmd = [sys.executable, "-c", CLIENT, sock]
        if uid is not None:
            cmd = ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", *cmd]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        os.set_blocking(self.p.stdout.fileno(), False)
        self.buf = b""
        self.lines: list[bytes] = []
        self.events: list[dict] = []

    def poll(self) -> list[dict]:
        try:
            while True:
                b = os.read(self.p.stdout.fileno(), 1 << 20)
                if not b:
                    break
                self.buf += b
        except BlockingIOError:
            pass
        lines, _, self.buf = self.buf.rpartition(b"\n")
        if lines:
            for x in lines.split(b"\n"):
                if x:
                    self.lines.append(x)
                    self.events += service._unbatch(json.loads(x))
        return self.events

    def wait(self, pred, timeout=10.0) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for e in self.poll():
                if pred(e):
                    return e
            time.sleep(0.05)
        return None

    def close(self):
        self.p.terminate()
        self.p.wait(5)


def _usable(tmp_path) -> str | None:
    if os.geteuid() != 0:
        return "needs root"
    if not Path("/usr/bin/setpriv").exists() and not Path("/bin/setpriv").exists():
        return "needs setpriv"
    try:
        fd = fan.init(W.INIT_FLAGS | fan.FAN_REPORT_PIDFD)
        try:
            fan.mark(fd, fan.FAN_MARK_ADD | fan.FAN_MARK_FILESYSTEM, W.MASK, str(tmp_path))
        finally:
            os.close(fd)
    except OSError as e:
        return f"fanotify filesystem marks do not work here: {e}"
    return None


def test_the_service_end_to_end(tmp_path):
    why = _usable(tmp_path)
    if why:
        pytest.skip(why)
    home = tmp_path / "w" / "nobody"
    home.mkdir(parents=True)
    run = tempfile.mkdtemp(prefix="bombadil-watch-", dir="/dev/shm")   # reachable by the unprivileged client
    os.chmod(run, 0o755)
    sock = f"{run}/watch.sock"
    state = tmp_path / "state"
    env = {**os.environ, "PYTHONPATH": str(Path(W.__file__).resolve().parents[2])}
    w = subprocess.Popen([sys.executable, "-m", "bombadil.brain.watch", "--path", str(tmp_path / "w"),
                          "--socket", sock, "--state", str(state), "--home", f"65534:{home}"],
                         env=env, stderr=subprocess.PIPE)
    cg_dir = None
    clients = []
    try:
        end = time.monotonic() + 10
        while not os.path.exists(sock) and time.monotonic() < end:
            time.sleep(0.05)
        root = Client(sock)
        user = Client(sock, 65534)
        clients += [root, user]
        hello = user.wait(lambda e: e["op"] == "hello")
        assert hello and hello["watching"] is True and hello["top"] == str(tmp_path / "w")

        def saved(path, op="write"):
            return lambda e: e.get("path") == str(path) and e["op"] == op

        # A live writer: the right path, pid, uid; routed to its user.
        code = ("import os, sys, time\nopen(sys.argv[1], 'w').write('hi')\n"
                "print(os.getpid(), flush=True)\ntime.sleep(3)")
        wr = subprocess.Popen([sys.executable, "-c", code, str(home / "a.txt")],
                              stdout=subprocess.PIPE, text=True)
        pid = int(wr.stdout.readline())
        e = user.wait(saved(home / "a.txt"))
        assert e and e["pid"] == pid and e["uid"] == 0 and e["gone"] is False and e["size"] == 2
        assert user.wait(saved(home / "a.txt", "create"))
        wr.kill()
        wr.wait()
        # Rename, delete, a directory, noise, an undecodable name.
        subprocess.run(["sh", "-c", "mv a.txt b.txt && mkdir d && echo x > d/c && rm b.txt && "
                        "mkdir -p .cache && echo n > .cache/n && printf z > \"$(printf 'bad\\377\\nname')\""],
                       cwd=home, check=True)
        e = user.wait(lambda e: e["op"] == "rename")
        assert e and e["old"] == str(home / "a.txt") and e["path"] == str(home / "b.txt")
        assert user.wait(saved(home / "b.txt", "delete"))
        d = user.wait(saved(home / "d", "create"))
        assert d and d["dir"] is True and d["size"] is None
        bad = user.wait(lambda e: e.get("path", "").startswith(str(home / "bad")) and e["op"] == "write")
        assert bad and os.fsencode(bad["path"]) == os.fsencode(str(home)) + b"/bad\xff\nname"
        assert not [e for e in user.poll() if "/.cache/" in e.get("path", "")]
        # Outside the home root: root gets nothing either (not /etc, not elsewhere).
        (tmp_path / "outside").write_text("x")
        time.sleep(0.3)
        assert not [e for e in root.poll() if e.get("path") == str(tmp_path / "outside")]

        # A short-lived writer in a turn's scope, reaped before its event is read.
        cg2 = _cgroup2()
        if cg2 and os.access(cg2, os.W_OK):
            cg_dir = Path(cg2) / "bombadil-turn-test.scope"
            cg_dir.mkdir(exist_ok=True)

            def into_scope():
                (cg_dir / "cgroup.procs").write_text(str(os.getpid()))

            w.send_signal(signal.SIGSTOP)
            outer = subprocess.Popen(["sh", "-c", f"sh -c 'echo x > {home}/short'; sleep 2"],
                                     preexec_fn=into_scope)
            time.sleep(0.5)
            w.send_signal(signal.SIGCONT)
            e = user.wait(saved(home / "short"))
            outer.wait()
            assert e and e["gone"] is True and e["pid"] == outer.pid
            assert e["cgroup"] == "/bombadil-turn-test.scope"

        # Away: spooled, then delivered first on reconnect.
        user.close()
        clients.remove(user)
        time.sleep(0.3)
        (home / "while-away").write_text("x")
        assert root.wait(saved(home / "while-away"))
        time.sleep(0.3)
        assert (state / "spool" / "65534.jsonl").stat().st_size > 0
        again = Client(sock, 65534)
        clients.append(again)
        assert again.wait(saved(home / "while-away"))
        assert again.events[0]["op"] == "hello"
    finally:
        for c in clients:
            c.close()
        w.send_signal(signal.SIGTERM)
        try:
            _, err = w.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            w.kill()
            _, err = w.communicate()
        if cg_dir is not None:
            for _ in range(50):
                try:
                    cg_dir.rmdir()
                    break
                except OSError:
                    time.sleep(0.1)
        os.rmdir(run) if os.path.isdir(run) and not os.listdir(run) else None
    assert w.returncode == 0, err.decode()
    assert not os.path.exists(sock)


def test_not_btrfs_answers_hello_and_nothing_else(tmp_path, monkeypatch):
    mounts = W.parse_mountinfo("1 0 0:30 / / rw - overlay airootfs rw\n")
    monkeypatch.setattr(W, "read_mountinfo", lambda fd=None: mounts)
    args = argparse.Namespace(path=None, socket=str(tmp_path / "s"), state=str(tmp_path), top=TOP,
                              spool_cap=W.SPOOL_CAP, home=None)
    w = W.Watcher(args)
    with pytest.raises(LookupError):
        w.start_watching()
    h = json.loads(w.hello())
    assert h == {"op": "hello", "v": 1, "watching": False, "fs": "overlay", "top": "",
                 "reason": "/home is on overlay, not btrfs"}


def test_main_needs_root(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert W.main([]) == 1
    assert "needs root" in capsys.readouterr().err


def test_peer_uid():
    a, b = socket.socketpair()
    try:
        assert W.peer_uid(a) == os.getuid()
    finally:
        a.close()
        b.close()
    assert struct.calcsize("3i") == 12


# --- generations and the offline pass ---

def test_generations_are_saved_only_after_the_queue_read_empty(tmp_path):
    w, _ = watcher(tmp_path, {})
    w.btrfs_mode = True
    w.saved = {"257": {"path": "@home", "gen": 5}, "300": {"path": "@home/x", "gen": 7},
               "999": {"path": "@home/deleted", "gen": 1}}
    w.pending = (10.0, {"257": {"path": "@home", "gen": 20}}, {"257", "300"})
    w.empty_at = 9.5              # events from before the markers may still be unread
    assert not w.save_markers() and not (tmp_path / "generations.json").exists()
    w.empty_at = 11.0
    assert w.save_markers() and w.pending is None
    # The next start lists from the generation after the marker; a subvolume whose marker
    # could not be read keeps its old one; one that no longer exists is forgotten.
    assert W.Generations(str(tmp_path / "generations.json")).load() == {
        "257": {"path": "@home", "gen": 21}, "300": {"path": "@home/x", "gen": 7}}


class FakeBtrfs:
    def __init__(self, subs, markers, new):
        self.subs, self.markers, self.new, self.asked = subs, markers, new, []

    def subvolumes(self, top):
        return self.subs

    def marker(self, path):
        return self.markers[path]

    def find_new(self, path, gen):
        self.asked.append((path, gen))
        return iter(self.new.get(path, []))


def test_offline_pass_lists_what_changed_while_stopped(tmp_path):
    top = tmp_path / "top"
    (top / "@home" / "user" / "Projects").mkdir(parents=True)
    (top / "@home" / "user" / ".cache").mkdir()
    (top / "@home" / "user" / "a.txt").write_text("abc")
    (top / "@home" / "user" / ".cache" / "c").write_text("x")
    (top / "@home" / "user" / "Projects" / "p.txt").write_text("p")
    (top / "@home" / "user" / "link").symlink_to("/etc/passwd")
    os.utime(top / "@home" / "user" / "a.txt", (1000, 1234.5))
    w, cap = watcher(tmp_path, {})
    w.top = str(top)
    mounts = W.parse_mountinfo(f"49 44 0:25 / {top} ro - btrfs /dev/vda rw\n"
                               "46 37 0:25 /@home /home rw - btrfs /dev/vda rw\n"
                               "37 1 0:25 /@ / rw - btrfs /dev/vda rw\n")
    w.pathmap = W.PathMap(mounts, mounts[0], exclude_own=True)
    w.btrfs_mode = True
    home, projects = str(top / "@home"), str(top / "@home" / "user" / "Projects")
    w.btrfs = FakeBtrfs(
        [(256, "@"), (257, "@home"), (258, "@home/user/Projects"), (259, "@snapshots"), (260, "@home/new")],
        {home: 30, projects: 12, str(top / "@home" / "new"): 3},
        {home: ["user/a.txt", "user/a.txt", "user/.cache/c", "user/missing", "user/link"],
         projects: ["p.txt"]})
    W.Generations(str(tmp_path / "generations.json")).save(
        {"257": {"path": "@home", "gen": 10}, "258": {"path": "@home/user/Projects", "gen": 4}})
    w.catch_up()
    assert w.btrfs.asked == [(home, 10), (projects, 4)]    # @ and @snapshots are not under /home; 260 is new
    offline = [e for e in cap.sent if e["op"] == "offline"]
    assert [e["path"] for e in offline] == ["/home/user/a.txt", "/home/user/Projects/p.txt"]
    a = offline[0]
    assert (a["t"], a["size"], a["uid"], a["pid"]) == (1234.5, 3, os.getuid(), 0)
    assert a["gone"] is True and a["chain"] == []
    assert cap.sent[-1]["op"] == "caught_up" and cap.sent[-1]["offline"] == 2
    assert w.pending is not None and w.pending[1] == {"257": {"path": "@home", "gen": 30},
                                                      "258": {"path": "@home/user/Projects", "gen": 12},
                                                      "260": {"path": "@home/new", "gen": 3}}


def test_a_spool_on_a_full_disk_says_overflow_instead_of_growing(tmp_path):
    s = W.Spool(str(tmp_path / "1000.jsonl"), 1 << 20)
    os.close(s.fd)
    s.fd = os.open("/dev/full", os.O_WRONLY)
    s.append(b'{"op":"write"}\n')
    s.flush()                      # ENOSPC: logged, dropped, no exception
    assert not s.pending
    os.close(s.fd)
    s.fd = os.open(tmp_path / "1000.jsonl", os.O_WRONLY | os.O_APPEND)


def test_a_home_outside_the_home_root_routes_nothing_but_system_paths(tmp_path):
    h = W.Hub(str(tmp_path / "spool"), W.Scope(), {65534: "/", 1000: "/home/user", 5: "/home"}.get)
    peers = {uid: Peer() for uid in (65534, 1000, 5)}
    for uid, p in peers.items():
        h.add(p.mine, uid)
    h.deliver(*ev("/home/user/a"))
    h.deliver(*ev("/etc/hosts"))
    pump_all(h)
    assert [e["path"] for e in peers[65534].read()] == ["/etc/hosts"]
    assert [e["path"] for e in peers[5].read()] == ["/etc/hosts"]
    assert [e["path"] for e in peers[1000].read()] == ["/home/user/a", "/etc/hosts"]


def test_connections_per_user_are_bounded(tmp_path):
    h = hub(tmp_path)
    for _ in range(W.CLIENTS_PER_UID):
        assert not h.full(1000)
        h.add(Peer().mine, 1000)
    assert h.full(1000) and not h.full(1001)

import json
import os
import pathlib
import random
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from bombadil import vitals
from bombadil.vitals import (CALM_EVERY, FAST_EVERY, Ceiling, Cgroups, Disk, Memory, Reading, Sampler, Vitals,
                             classify_unit)

GB = 10**9
ROOT = "/sys/fs/cgroup"
MANAGER = "/user.slice/user-1000.slice/user@1000.service"
BASE = ROOT + MANAGER


# -- what the kernel prints --

def stat_text(user=4705, idle=84001):
    return (f"cpu  {user} 150 3120 {idle} 301 0 61 0 0 0\n"
            f"cpu0 {user // 2} 40 790 {idle // 2} 80 0 30 0 0 0\n"
            f"cpu1 {user - user // 2} 110 2330 {idle - idle // 2} 221 0 31 0 0 0\n"
            "intr 4182937 31 0 0 0 0 0 0 0 1 0 0 0\nctxt 9021833\nbtime 1790000000\nprocesses 18211\n"
            "procs_running 2\nprocs_blocked 0\n")


def meminfo_text(total_kb=16_000_000, available_kb=9_000_000):
    return (f"MemTotal:       {total_kb} kB\nMemFree:         1203948 kB\nMemAvailable:   {available_kb} kB\n"
            "Buffers:          123456 kB\nCached:          6123456 kB\nSwapCached:            0 kB\n"
            "Active:          5013904 kB\nShmem:            345678 kB\nSReclaimable:     412345 kB\n"
            "HugePages_Total:       0\nHugePages_Free:        0\nHugepagesize:       2048 kB\n"
            "DirectMap4k:      301056 kB\n")


def net_dev_text(**ifaces):
    head = ("Inter-|   Receive                                                |  Transmit\n"
            " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls "
            "carrier compressed\n")
    rows = "".join(f"{name:>8}: {rx} 9000 0 0 0 0 0 0 {tx} 5000 0 0 0 0 0 0\n" for name, (rx, tx) in ifaces.items())
    return head + rows


MOUNTS = ("sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\nproc /proc proc rw,nosuid,nodev,noexec 0 0\n"
          "/dev/nvme0n1p2 / ext4 rw,relatime 0 0\ntmpfs /tmp tmpfs rw,nosuid,nodev 0 0\n"
          "/dev/nvme0n1p3 /home ext4 rw,relatime 0 0\nserver:/export /mnt/nas nfs4 rw,relatime 0 0\n")


def vfs(used_gb, avail_gb, frsize=1000, reserved_gb=0):
    """What statvfs says for a filesystem with this much used, writable, and kept back for root."""
    blocks = (used_gb + avail_gb + reserved_gb) * GB // frsize
    return SimpleNamespace(f_frsize=frsize, f_blocks=blocks, f_bfree=(avail_gb + reserved_gb) * GB // frsize,
                           f_bavail=avail_gb * GB // frsize)


class Fs:
    """A machine as text: path -> what is in it. A directory is whatever the paths run through."""

    def __init__(self, files=None, disks=None):
        self.files = dict(files or {})
        self.disks = dict(disks or {})
        self.reads, self.listed, self.statted = [], [], []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)

    def listdir(self, path):
        self.listed.append(path)
        prefix = path.rstrip("/") + "/"
        return sorted({p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)}) or None

    def statvfs(self, path):
        self.statted.append(path)
        return self.disks.get(path)

    def unit(self, path, current, inactive=None, high=None, limit=None):
        """A cgroup directory: its memory.current, memory.stat and, when it has them, memory.high and memory.max."""
        self.files[f"{path}/memory.current"] = f"{current}\n"
        if inactive is not None:
            self.files[f"{path}/memory.stat"] = f"anon {current}\nfile 0\ninactive_file {inactive}\nactive_file 7\n"
        if high is not None:
            self.files[f"{path}/memory.high"] = f"{high}\n"
        if limit is not None:
            self.files[f"{path}/memory.max"] = f"{limit}\n"


def machine(**more):
    """An idle laptop: the files an agentd under the user's manager reads, in the shapes the kernel writes them."""
    fs = Fs(disks={"/": vfs(40, 160), "/home": vfs(100, 100)})
    fs.files.update({
        "/proc/stat": stat_text(),
        "/proc/meminfo": meminfo_text(),
        "/proc/self/mounts": MOUNTS,
        "/proc/net/dev": net_dev_text(lo=(905623, 905623), eth0=(12_345_678, 2_345_678)),
        "/proc/self/cgroup": f"0::{MANAGER}/app.slice/bombadil-agentd.service\n",
        "/sys/class/thermal/thermal_zone0/temp": "47000\n",
        "/sys/class/thermal/thermal_zone1/temp": "52000\n",
        "/sys/class/thermal/cooling_device0/cur_state": "0\n",
    })
    fs.unit(BASE, 9 * GB)
    fs.unit(f"{BASE}/app.slice", 3 * GB)
    fs.unit(f"{BASE}/app.slice/bombadil-turn-4242-1-1790000000.scope", 1_200_000_000, inactive=200_000_000)
    fs.unit(f"{BASE}/app.slice/bombadil-job-a1b2c3.service", 500_000_000, inactive=0)
    fs.unit(f"{BASE}/app.slice/bombadil-timer-d4e5f6.service", 10_000_000)
    fs.unit(f"{BASE}/app.slice/app-firefox-9921.scope", 2 * GB)          # yours, not ours to count
    fs.unit(f"{BASE}/bombadil.slice", 6 * GB)
    fs.unit(f"{BASE}/bombadil.slice/bombadil-dev.slice", 6 * GB, inactive=1 * GB, high=8 * GB, limit="max")
    fs.unit(f"{BASE}/bombadil.slice/bombadil-dev.slice/bombadil-dev-proj.slice", 6 * GB, inactive=1 * GB)
    fs.unit(f"{BASE}/bombadil.slice/bombadil-dev.slice/bombadil-dev-proj.slice/bombadil-dev-proj-builder-1.scope",
            4 * GB, inactive=0)
    fs.files.update(more)
    return fs


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def sampler(fs, clock=None):
    return Sampler(read=fs.read, listdir=fs.listdir, statvfs=fs.statvfs, clock=clock or Clock(), uid=lambda: 1000,
                   root=ROOT)


# -- a sampler that says what the test tells it to --

class Fake:
    def __init__(self):
        self.reading = reading()
        self.fast = []
        self.resets = 0

    def sample(self, fast):
        self.fast.append(fast)
        return self.reading

    def reset(self):
        self.resets += 1


def reading(memory=0.40, disk=0.50, heat=None, sessions=None, cpu=0.10, net=(0.0, 0.0), machine_=0, held=0):
    """A sample in percentages: memory of 16 GB, the disk, the sessions against an 8 GB ceiling."""
    ceiling = Ceiling(int(sessions * 8 * GB), 8 * GB) if sessions is not None else None
    cgroups = Cgroups(machine_, held, ceiling) if (ceiling or machine_ or held) else None
    return Reading(cpu=cpu, memory=Memory(int(memory * 16 * GB), 16 * GB), cgroups=cgroups,
                   disk=Disk(int(disk * 230 * GB), 230 * GB), heat=heat, net=net)


def rig():
    clock, fake = Clock(0.0), Fake()
    return Vitals(sampler=fake, clock=clock), fake, clock


def run(v, fake, clock, n, step=1.0, **now):
    """n ticks, a step apart, on what `reading(**now)` says; what each returned."""
    fake.reading = reading(**now)
    out = []
    for _ in range(n):
        out.append(v.tick())
        clock.t += step
    return out


def rise(v, fake, clock, **now):
    """Cross a line: three samples over it. Returns the message that said so."""
    return run(v, fake, clock, 3, **now)[-1]


# -- parsers --

def test_cpu_line_is_busy_and_total_jiffies_of_the_whole_machine():
    # idle and iowait are not busy; guest time is already in user, so it is not added again
    busy = 4705 + 150 + 3120 + 0 + 61
    assert vitals.parse_cpu(stat_text(4705, 84001)) == (busy, busy + 84001 + 301)
    assert vitals.parse_cpu("cpu  10 0 10 80 0 0 0 0 5 5\ncpu0 1 1 1 1") == (20, 100)


def test_cpu_line_that_is_not_one_gives_nothing():
    for text in (None, "", "\n", "cpu", "cpu  1 2 3", "cpu0 1 2 3 4 5\n", "intr 1 2 3 4 5", "cpu  a b c d e",
                 "cpu  1 2 3 4 -5", "cpu  1 2 3 4 5 6 7 1_0", "cpu  1 2 3 4 " + "9" * 5000):
        assert vitals.parse_cpu(text) is None, text


def test_older_kernels_with_fewer_cpu_columns_still_read():
    assert vitals.parse_cpu("cpu  10 0 10 80\n") == (20, 100)
    assert vitals.parse_cpu("cpu  10 0 10 70 10 0 0\n") == (20, 100)


def test_cpu_share_is_the_busy_part_of_the_time_between_two_reads():
    assert vitals.cpu_share((100, 1000), (150, 1200)) == pytest.approx(0.25)
    assert vitals.cpu_share((100, 1000), (100, 1100)) == 0.0
    assert vitals.cpu_share((100, 1000), (1300, 1100)) == 1.0         # never past 1
    assert vitals.cpu_share((100, 1000), (90, 1100)) == 0.0           # never under 0
    for before, after in (((1, 5), (1, 5)), ((1, 9), (1, 5)), (None, (1, 5)), ((1, 5), None)):
        assert vitals.cpu_share(before, after) is None


def test_meminfo_is_in_bytes_and_skips_what_is_not_a_field():
    mi = vitals.parse_meminfo(meminfo_text() + "garbage\n: 4 kB\nBad: x kB\nEmpty:\n\x00\x01\n")
    assert mi["MemTotal"] == 16_000_000 * 1024
    assert mi["MemAvailable"] == 9_000_000 * 1024
    assert mi["HugePages_Total"] == 0 and mi["Hugepagesize"] == 2048 * 1024
    assert "Bad" not in mi and "Empty" not in mi and "" not in mi
    assert vitals.parse_meminfo(None) == {} == vitals.parse_meminfo("")


def test_memory_in_use_is_what_could_not_be_given_back():
    mem = vitals.memory_in_use(vitals.parse_meminfo(meminfo_text(16_000_000, 9_000_000)))
    assert mem == Memory(7_000_000 * 1024, 16_000_000 * 1024)
    assert mem.fraction == pytest.approx(0.4375)


def test_memory_without_memavailable_is_worked_out_from_free_and_cache():
    text = "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 250 kB\n"
    assert vitals.memory_in_use(vitals.parse_meminfo(text)) == Memory(600 * 1024, 1000 * 1024)


def test_a_meminfo_that_says_too_little_is_not_a_full_machine():
    for text in ("", "MemTotal: 1000 kB\n", "MemTotal: 0 kB\nMemAvailable: 0 kB\n", "MemAvailable: 5 kB\n"):
        assert vitals.memory_in_use(vitals.parse_meminfo(text)) is None
    # more available than there is: nothing used, not a negative
    assert vitals.memory_in_use({"MemTotal": 100, "MemAvailable": 400}) == Memory(0, 100)


def test_network_counts_real_interfaces_and_not_the_ones_that_count_them_twice():
    text = net_dev_text(lo=(1, 2), eth0=(100, 10), wlan0=(5, 1), docker0=(900, 900), veth3f2a=(700, 700),
                        **{"br-1a2b3c": (800, 800), "virbr0": (600, 600), "vnet4": (50, 50), "tun0": (7, 7),
                           "br0": (40, 40), "bond0": (30, 30), "enp3s0.100": (20, 20), "wg0": (3, 3),
                           "lowpan0": (2, 2)})
    assert vitals.parse_net_dev(text) == {"eth0": (100, 10), "wlan0": (5, 1), "tun0": (7, 7), "wg0": (3, 3),
                                          "lowpan0": (2, 2)}


def test_network_text_that_is_garbage_gives_what_can_be_read():
    text = "no colon here\nshort: 1 2 3\nbad: x 1 1 1 1 1 1 1 y 1 1 1 1 1 1 1\n: 1 1 1 1 1 1 1 1 1\n" + \
        net_dev_text(eth0=(100, 10))
    assert vitals.parse_net_dev(text) == {"eth0": (100, 10)}
    assert vitals.parse_net_dev(None) == {} == vitals.parse_net_dev("")


def test_temperature_is_degrees_and_a_sensor_with_no_reading_is_none():
    assert vitals.parse_temp("47000\n") == 47.0
    assert vitals.parse_temp("82500") == 82.5
    for text in (None, "", "0\n", "-5000", "-1", "hot", "47.5", "255000", "9" * 20, "1_000", "٤٧٠٠٠"):
        assert vitals.parse_temp(text) is None, text


def test_limits_are_a_number_of_bytes_and_max_means_none():
    assert vitals.parse_limit("8589934592\n") == 8 * 1024**3
    for text in (None, "", "max\n", "0", "-1", "lots"):
        assert vitals.parse_limit(text) is None
    assert vitals.parse_keyed("anon 5\ninactive_file 123\nbad x\nlonely\n\n") == {"anon": 5, "inactive_file": 123}
    assert vitals.parse_keyed(None) == {}


def test_the_users_manager_is_found_in_the_process_own_cgroup():
    own = f"0::{MANAGER}/app.slice/bombadil-agentd.service\n"
    assert vitals.manager_cgroup(own, 1000) == MANAGER
    assert vitals.manager_cgroup(f"0::{MANAGER}\n", 1000) == MANAGER
    # a hybrid host: only the unified hierarchy's "0::" line counts
    assert vitals.manager_cgroup("12:memory:/x\n1:name=systemd:/y\n" + own, 1000) == MANAGER
    # somebody else's manager is not ours: the usual place for this user
    assert vitals.manager_cgroup("0::/user.slice/user-0.slice/user@0.service/app.slice/x.scope\n", 1000) == MANAGER
    # started from a login session, not from the manager
    assert vitals.manager_cgroup("0::/user.slice/user-1000.slice/session-3.scope\n", 1000) == MANAGER
    assert vitals.manager_cgroup("0::/user.slice/user-42.slice/user@42.service/a.scope\n", 42) == \
        "/user.slice/user-42.slice/user@42.service"
    for text in (None, "", "1:name=systemd:/x\n", "garbage"):
        assert vitals.manager_cgroup(text, 1000) is None


def test_units_are_classified_by_what_started_them():
    for name in ("bombadil-turn-4242-1-1790000000.scope", "bombadil-job-a1b2c3.service",
                 "bombadil-timer-d4e5f6.service", "bombadil-job-a1b2c3", "bombadil-turn-1"):
        assert classify_unit(name) == "machine", name
    for name in ("bombadil-dev.slice", "bombadil-dev-bombadil.slice", "bombadil-dev-bombadil-builder-7.scope",
                 "bombadil-dev"):
        assert classify_unit(name) == "sessions", name
    for name in ("", "app.slice", "bombadil.slice", "bombadil-agentd.service", "bombadil-shell.service",
                 "bombadil-developer.service", "bombadil-turn", "xbombadil-job-a1b2c3.service", "bombadil-job",
                 "app-bombadil-job-1.scope", "memory.current", "cgroup.procs"):
        assert classify_unit(name) is None, name


def test_mounts_are_read_with_their_escapes():
    mounts = vitals.parse_mounts("/dev/sdb1 /media/Backup\\040Disk exfat rw 0 0\nshort line\n\n"
                                 "/dev/sda1 / ext4 rw 0 0")
    assert mounts == [vitals.Mount("/dev/sdb1", "/media/Backup Disk", "exfat"),
                      vitals.Mount("/dev/sda1", "/", "ext4")]
    assert vitals.parse_mounts(None) == []


# -- the disk --

def disks_of(text, table):
    return vitals.fullest_disk(vitals.parse_mounts(text), lambda p: table.get(p))


def test_the_disk_is_the_fuller_of_root_and_home():
    assert disks_of(MOUNTS, {"/": vfs(40, 160), "/home": vfs(180, 20)}) == Disk(180 * GB, 200 * GB)
    assert disks_of(MOUNTS, {"/": vfs(190, 10), "/home": vfs(100, 100)}) == Disk(190 * GB, 200 * GB)


def test_the_disk_is_full_as_df_says_it_not_counting_what_is_kept_for_root():
    # 5% kept for root: a person with 95 of 100 GB used cannot write another byte
    disk = disks_of(MOUNTS, {"/": vfs(95, 0, reserved_gb=5), "/home": None})
    assert disk == Disk(95 * GB, 95 * GB) and disk.fraction == 1.0


def test_the_same_device_is_counted_once():
    text = "/dev/nvme0n1p2 / btrfs rw,subvol=/@ 0 0\n/dev/nvme0n1p2 /home btrfs rw,subvol=/@home 0 0\n"
    seen = []

    def statvfs(path):
        seen.append(path)
        return vfs(10, 90)

    assert vitals.fullest_disk(vitals.parse_mounts(text), statvfs) == Disk(10 * GB, 100 * GB)
    assert seen == ["/"]


def test_a_home_that_is_not_a_mount_of_its_own_is_the_roots():
    seen = []
    mounts = vitals.parse_mounts("/dev/sda1 / ext4 rw 0 0\n")
    assert vitals.fullest_disk(mounts, lambda p: seen.append(p) or vfs(1, 9)) == Disk(1 * GB, 10 * GB)
    assert seen == ["/"]


def test_memory_images_and_network_shares_are_never_statted():
    seen = []
    for text in ("tmpfs / tmpfs rw 0 0\n", "overlay / overlay rw 0 0\nserver:/h /home nfs4 rw 0 0\n",
                 "//nas/x /home cifs rw 0 0\n/dev/loop0 / squashfs ro 0 0\n",
                 "sshfs#host:/export /home fuse.sshfs rw 0 0\n", "", "garbage"):
        assert vitals.fullest_disk(vitals.parse_mounts(text), lambda p: seen.append(p)) is None
    assert seen == []
    # a disk behind FUSE is still a disk
    assert vitals.fullest_disk(vitals.parse_mounts("/dev/sdb1 /home fuseblk rw 0 0\n"), lambda p: vfs(1, 9)) == \
        Disk(1 * GB, 10 * GB)


def test_what_covers_a_mount_point_is_what_is_there():
    # a tmpfs mounted over /home later hides the disk under it
    text = "/dev/sda1 / ext4 rw 0 0\n/dev/sda2 /home ext4 rw 0 0\ntmpfs /home tmpfs rw 0 0\n"
    assert vitals.fullest_disk(vitals.parse_mounts(text), lambda p: vfs(1, 9) if p == "/" else vfs(9, 1)) == \
        Disk(1 * GB, 10 * GB)


def test_a_disk_that_cannot_be_statted_or_has_no_blocks_is_skipped():
    assert disks_of(MOUNTS, {}) is None
    assert disks_of(MOUNTS, {"/": vfs(0, 0), "/home": None}) is None
    assert disks_of(MOUNTS, {"/": None, "/home": vfs(5, 5)}) == Disk(5 * GB, 10 * GB)


# -- the cgroup walk --

def test_a_unit_is_its_memory_current_less_the_cache_it_can_give_back():
    fs = Fs()
    fs.unit("/a", 1_200_000_000, inactive=200_000_000)
    fs.unit("/b", 100, inactive=500)                     # more inactive than current: never negative
    fs.unit("/c", 777)                                   # no memory.stat: current as it is
    fs.files["/d/memory.current"] = "garbage\n"
    for path, used in (("/a", 1_000_000_000), ("/b", 0), ("/c", 777), ("/d", None), ("/nowhere", None)):
        assert vitals.unit_memory(fs.read, path) == used, path


def test_the_walk_adds_up_the_machines_units_and_the_sessions():
    fs = machine()
    got = vitals.read_cgroups(fs.read, fs.listdir, BASE)
    assert got.machine == 1_000_000_000 + 500_000_000 + 10_000_000
    assert got.sessions == 5 * GB            # the dev slice, once: its children are inside it
    assert got.ceiling == Ceiling(5 * GB, 8 * GB)
    assert got.dev == f"{BASE}/bombadil.slice/bombadil-dev.slice"


def test_the_walk_does_not_go_below_a_unit_of_ours():
    fs = machine()
    seen = vitals.walk_units(fs.listdir, BASE)
    assert [(kind, name) for kind, name, _ in seen] == [
        ("machine", "bombadil-job-a1b2c3.service"), ("machine", "bombadil-timer-d4e5f6.service"),
        ("machine", "bombadil-turn-4242-1-1790000000.scope"), ("sessions", "bombadil-dev.slice")]
    assert f"{BASE}/bombadil.slice/bombadil-dev.slice" not in fs.listed        # entered nothing below the dev slice
    assert not any("bombadil-dev-proj" in p for p in fs.listed)


def test_sessions_scopes_with_no_slice_around_them_are_counted_each():
    fs = Fs()
    fs.unit(BASE, 5 * GB)
    fs.unit(f"{BASE}/app.slice/bombadil-dev-bombadil-builder-1.scope", 2 * GB)
    fs.unit(f"{BASE}/app.slice/bombadil-dev-bombadil-reviewer-2.scope", 1 * GB, inactive=300_000_000)
    got = vitals.read_cgroups(fs.read, fs.listdir, BASE)
    assert (got.machine, got.sessions, got.ceiling, got.dev) == (0, 2 * GB + 700_000_000, None, None)


def test_the_walk_goes_four_levels_down_and_no_further():
    fs = Fs()
    fs.unit(BASE, 1)
    fs.unit(f"{BASE}/a.slice/b.slice/c.slice/bombadil-job-aaaaaa.service", 4)             # level 4
    fs.unit(f"{BASE}/a.slice/b.slice/c.slice/d.slice/bombadil-job-bbbbbb.service", 8)     # level 5
    assert vitals.read_cgroups(fs.read, fs.listdir, BASE).machine == 4
    assert [n for _, n, _ in vitals.walk_units(fs.listdir, BASE)] == ["bombadil-job-aaaaaa.service"]


def test_a_unit_without_memory_accounting_counts_nothing_but_the_rest_still_do():
    fs = machine()
    del fs.files[f"{BASE}/app.slice/bombadil-job-a1b2c3.service/memory.current"]
    assert vitals.read_cgroups(fs.read, fs.listdir, BASE).machine == 1_000_000_000 + 10_000_000


def test_without_a_memory_controller_or_a_cgroup_tree_there_is_nothing_to_say():
    fs = machine()
    del fs.files[f"{BASE}/memory.current"]
    assert vitals.read_cgroups(fs.read, fs.listdir, BASE) is None
    assert vitals.read_cgroups(machine().read, machine().listdir, "/user.slice/nobody") is None
    assert vitals.read_cgroups(lambda p: None, lambda p: None, BASE) is None


def test_the_sessions_ceiling_is_the_dev_slices_high_else_its_max():
    dev = f"{BASE}/bombadil.slice/bombadil-dev.slice"
    fs = machine()
    assert vitals.ceiling_of(fs.read, dev) == Ceiling(5 * GB, 8 * GB)
    fs.files[f"{dev}/memory.max"] = f"{12 * GB}\n"
    assert vitals.ceiling_of(fs.read, dev) == Ceiling(5 * GB, 8 * GB)           # high wins over max
    fs.files[f"{dev}/memory.high"] = "max\n"
    assert vitals.ceiling_of(fs.read, dev) == Ceiling(5 * GB, 12 * GB)
    fs.files[f"{dev}/memory.max"] = "max\n"
    assert vitals.ceiling_of(fs.read, dev) is None                            # no limit, no line
    del fs.files[f"{dev}/memory.high"], fs.files[f"{dev}/memory.max"]
    assert vitals.ceiling_of(fs.read, dev) is None


# -- numbers and words --

def test_sizes_are_said_in_the_unit_the_total_suits():
    for used, total, said in ((14.5 * GB, 16 * GB, "14.5 of 16 GB"), (214 * GB, 230 * GB, "214 of 230 GB"),
                              (0.8 * GB, 16.9 * GB, "0.8 of 16.9 GB"), (1.2e12, 2e12, "1.2 of 2 TB"),
                              (800e6, 940e6, "800 of 940 MB"), (0.5 * GB, 1 * GB, "0.5 of 1 GB"),
                              (3e6, 16 * GB, "0 of 16 GB"), (0, 0, "0 B"), (5, 900, "5 of 900 B"),
                              (99.95 * GB, 120 * GB, "100 of 120 GB"), (999.9e6, 999.96e6, "1 of 1 GB")):
        assert vitals.size_text(used, total) == said


def test_rates_are_said_the_way_a_person_says_them():
    for rate, said in ((1.2e6, "1.2 MB/s"), (40e3, "40 kB/s"), (0, "0 B/s"), (812, "812 B/s"), (999_700, "1 MB/s"),
                       (15_400, "15 kB/s"), (9_500, "9.5 kB/s"), (3.2e9, "3.2 GB/s"), (-5, "0 B/s"), (0.4, "0 B/s")):
        assert vitals.rate_text(rate) == said


def test_the_memory_bar_is_cut_where_the_rounded_running_total_falls_and_never_runs_over():
    mem = Memory(int(14.5 * GB), 16 * GB)
    parts = vitals.memory_parts(mem, Cgroups(int(1.92 * GB), 8 * GB))
    assert parts == [{"tone": "machine", "fraction": 0.12}, {"tone": "sessions", "fraction": 0.5},
                     {"tone": "you", "fraction": 0.29}]
    assert vitals.memory_parts(mem, None) == [{"tone": "you", "fraction": 0.91}]
    rng = random.Random(5)
    for _ in range(500):
        total = rng.randint(1, 10**12)
        used = rng.randint(0, total)
        parts = vitals.memory_parts(Memory(used, total), Cgroups(rng.randint(0, total), rng.randint(0, total)))
        assert [p["tone"] for p in parts] == ["machine", "sessions", "you"]
        assert all(0 <= p["fraction"] <= 1 for p in parts)
        assert sum(p["fraction"] for p in parts) <= 1 + 1e-9
        assert sum(p["fraction"] for p in parts) == pytest.approx(round(used / total, 2), abs=1e-9)


def test_cgroups_that_hold_more_than_is_used_are_cut_to_what_is_used():
    # they count cache that is still warm and MemAvailable does not count as used
    mem = Memory(10 * GB, 16 * GB)
    machine_, sessions, you = vitals.holders(mem, Cgroups(8 * GB, 8 * GB))
    assert (machine_, sessions, you) == (5 * GB, 5 * GB, 0)
    assert vitals.holders(mem, Cgroups(2 * GB, 3 * GB)) == (2 * GB, 3 * GB, 5 * GB)
    assert vitals.holders(Memory(0, 16 * GB), Cgroups(1, 1)) == (0, 0, 0)


def test_no_parser_raises_on_garbage():
    rng = random.Random(11)
    alphabet = "cpu 0123456789:-_.kBmax\n\t\x00\x01\\é٤ "
    texts = [None, "", "\n" * 50, "\x00" * 100, "9" * 10_000, ":" * 100, " ".join(["cpu"] * 50)]
    texts += ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200))) for _ in range(300)]
    for text in texts:
        vitals.parse_cpu(text)
        vitals.parse_meminfo(text)
        vitals.memory_in_use(vitals.parse_meminfo(text))
        vitals.parse_net_dev(text)
        vitals.parse_temp(text)
        vitals.parse_limit(text)
        vitals.parse_keyed(text)
        vitals.manager_cgroup(text, 1000)
        vitals.fullest_disk(vitals.parse_mounts(text), lambda p: None)
        vitals.classify_unit(text or "")


# -- the sampler --

def test_the_first_sample_has_no_processor_share_and_the_next_has():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    assert s.sample().cpu is None
    fs.files["/proc/stat"] = stat_text(user=4705 + 250, idle=84001 + 750)
    clock.t += 1
    assert s.sample().cpu == pytest.approx(0.25)
    fs.files["/proc/stat"] = "garbage"
    assert s.sample().cpu is None
    fs.files["/proc/stat"] = stat_text(user=4705 + 250 + 100, idle=84001 + 750 + 100)       # the baseline survived
    assert s.sample().cpu == pytest.approx(0.5)


def test_a_sample_of_an_idle_machine_reads_what_is_there():
    fs, clock = machine(), Clock()
    r = sampler(fs, clock).sample()
    assert r.memory == Memory(7_000_000 * 1024, 16_000_000 * 1024)
    assert r.heat == 52.0                                          # the hottest zone
    assert r.disk == Disk(100 * GB, 200 * GB)                      # /home is the fuller
    assert r.cgroups.machine == 1_510_000_000 and r.cgroups.sessions == 5 * GB
    assert r.cgroups.ceiling == Ceiling(5 * GB, 8 * GB)
    assert r.net is None                                           # one read of the counters is no rate


def test_network_rates_are_bytes_a_second_between_two_reads_of_each_interface():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    assert s.sample().net is None
    clock.t += 2
    fs.files["/proc/net/dev"] = net_dev_text(lo=(9, 9), eth0=(12_345_678 + 2_400_000, 2_345_678 + 80_000),
                                             docker0=(10**9, 10**9))
    assert s.sample().net == (1_200_000.0, 40_000.0)


def test_an_interface_that_comes_or_goes_or_starts_over_is_not_a_burst_or_a_negative_rate():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample()
    clock.t += 1
    # eth0's counters started over; a phone's tether (usb0) is new, with years of bytes on it
    fs.files["/proc/net/dev"] = net_dev_text(eth0=(100, 100), usb0=(10**12, 10**12))
    assert s.sample().net == (0.0, 0.0)
    clock.t += 1
    fs.files["/proc/net/dev"] = net_dev_text(eth0=(1100, 600), usb0=(10**12 + 50, 10**12 + 70))
    assert s.sample().net == (1050.0, 570.0)
    clock.t += 1
    fs.files["/proc/net/dev"] = net_dev_text(eth0=(1100, 600))                    # usb0 went away
    assert s.sample().net == (0.0, 0.0)


def test_a_rate_is_not_worked_out_across_a_gap():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample()
    clock.t += vitals.NET_STALE + 1
    fs.files["/proc/net/dev"] = net_dev_text(eth0=(10**10, 10**10))
    assert s.sample().net is None
    clock.t += 1
    assert s.sample().net == (0.0, 0.0)


def test_a_calm_sample_does_not_walk_the_cgroups_or_read_the_network():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample(fast=True)
    fs.reads.clear(), fs.listed.clear(), fs.statted.clear()
    clock.t += CALM_EVERY
    calm = s.sample(fast=False)
    dev = f"{BASE}/bombadil.slice/bombadil-dev.slice"
    # the processor, memory, the two thermal zones and the dev slice: no listing, no mounts, no network
    zones = ["/sys/class/thermal/thermal_zone0/temp", "/sys/class/thermal/thermal_zone1/temp"]
    assert sorted(fs.reads) == sorted(["/proc/stat", "/proc/meminfo", *zones, f"{dev}/memory.current",
                                       f"{dev}/memory.stat", f"{dev}/memory.high"])
    assert fs.listed == [] and fs.statted == []
    assert calm.net is None and calm.cgroups.sessions == 5 * GB            # carried from the walk
    assert calm.cgroups.ceiling == Ceiling(5 * GB, 8 * GB)


def test_a_calm_sample_still_sees_the_sessions_come_near_their_ceiling():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample(fast=False)
    dev = f"{BASE}/bombadil.slice/bombadil-dev.slice"
    fs.unit(dev, 8 * GB, inactive=1 * GB, high=8 * GB, limit="max")
    clock.t += CALM_EVERY
    assert s.sample(fast=False).cgroups.ceiling == Ceiling(7 * GB, 8 * GB)


def test_the_walk_is_redone_when_calm_every_thirty_seconds_and_the_disk_with_it():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample(fast=False)
    assert fs.statted == ["/", "/home"]
    fs.disks["/home"] = vfs(190, 10)
    fs.unit(f"{BASE}/app.slice/bombadil-job-ffffff.service", 700_000_000)
    clock.t += vitals.SLOW_EVERY - 1
    r = s.sample(fast=False)
    assert r.disk == Disk(100 * GB, 200 * GB) and r.cgroups.machine == 1_510_000_000     # not yet
    clock.t += 1
    r = s.sample(fast=False)
    assert r.disk == Disk(190 * GB, 200 * GB) and r.cgroups.machine == 2_210_000_000


def test_a_fast_sample_walks_every_time():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample(fast=True)
    fs.unit(f"{BASE}/app.slice/bombadil-job-ffffff.service", 700_000_000)
    clock.t += FAST_EVERY
    assert s.sample(fast=True).cgroups.machine == 2_210_000_000


def test_heat_is_the_hottest_zone_and_ignores_readings_that_say_nothing():
    fs = machine()
    fs.files["/sys/class/thermal/thermal_zone2/temp"] = "0\n"
    fs.files["/sys/class/thermal/thermal_zone3/temp"] = "-274000\n"
    fs.files["/sys/class/thermal/thermal_zone4/temp"] = "255000\n"
    assert sampler(fs).sample().heat == 52.0


def test_heat_falls_back_to_hwmon_and_is_none_without_any_sensor():
    fs = machine()
    for p in [p for p in fs.files if "thermal" in p]:
        del fs.files[p]
    assert sampler(fs).sample().heat is None
    fs.files["/sys/class/hwmon/hwmon0/name"] = "nvme\n"
    fs.files["/sys/class/hwmon/hwmon0/temp1_input"] = "38000\n"
    fs.files["/sys/class/hwmon/hwmon3/temp1_input"] = "61000\n"
    fs.files["/sys/class/hwmon/hwmon3/temp2_input"] = "66000\n"
    fs.files["/sys/class/hwmon/hwmon3/temp2_label"] = "Core 0\n"
    assert sampler(fs).sample().heat == 66.0
    fs.files["/sys/class/thermal/thermal_zone0/temp"] = "70000\n"                # the zones win when they read
    assert sampler(fs).sample().heat == 70.0


def test_a_sample_of_a_machine_with_nothing_readable_is_empty_and_does_not_raise():
    s = Sampler(read=lambda p: None, listdir=lambda p: None, statvfs=lambda p: None, clock=Clock(), uid=lambda: 1000,
                root=ROOT)
    for fast in (True, False, True, False):
        assert s.sample(fast) == Reading()


def test_the_sampler_without_a_cgroup_tree_says_so_and_the_rest_still_reads():
    fs = machine(**{"/proc/self/cgroup": "1:name=systemd:/\n"})
    r = sampler(fs).sample()
    assert r.cgroups is None and r.memory is not None and r.disk is not None


def test_reset_makes_the_next_sample_a_first_one():
    fs, clock = machine(), Clock()
    s = sampler(fs, clock)
    s.sample()
    fs.files["/proc/stat"] = stat_text(user=5000, idle=85000)
    fs.files["/proc/net/dev"] = net_dev_text(eth0=(12_345_678 + 1000, 2_345_678 + 1000))
    clock.t += 1
    r = s.sample()
    assert r.cpu is not None and r.net is not None
    s.reset()
    fs.files["/proc/stat"] = stat_text(user=5100, idle=85100)
    clock.t += 1
    r = s.sample()
    assert r.cpu is None and r.net is None


def test_the_real_sampler_reads_this_machine_without_raising():
    s = Sampler()
    first, second = s.sample(), s.sample(fast=False)
    assert isinstance(first, Reading) and isinstance(second, Reading)
    assert first.cpu is None


# -- the lines --

def test_a_line_is_crossed_by_three_samples_in_a_row_not_by_one_or_two():
    v, fake, clock = rig()
    out = run(v, fake, clock, 2, memory=0.91)
    assert out[0]["present"] is False and out[1] is None          # the first tick says so, the second has nothing new
    third = v.tick()
    assert third["present"] is True and third["asked"] is False
    assert third["strip"] == {"text": "memory 91%", "dot": "amber"}


def test_a_sample_under_the_line_starts_the_count_again():
    v, fake, clock = rig()
    for value in (0.91, 0.91, 0.89, 0.91, 0.91):
        assert (run(v, fake, clock, 1, memory=value)[0] or {"present": False})["present"] is False
    assert run(v, fake, clock, 1, memory=0.91)[0]["present"] is True


def test_exactly_at_the_line_is_over_it():
    v, fake, clock = rig()
    assert rise(v, fake, clock, disk=0.90)["present"] is True


def test_a_line_is_left_after_ten_samples_at_or_under_its_lower_mark():
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.91)                                 # crossed at t=2
    for i in range(9):
        run(v, fake, clock, 1, memory=0.85)                           # exactly at the lower mark is under it
        assert v.message()["present"] is True, i
    gone = run(v, fake, clock, 1, memory=0.85)[0]                     # the tenth, at t=12
    assert gone["present"] is False and gone["rows"] == [] and gone["why"] == ""


def test_a_sample_between_the_two_marks_neither_crosses_nor_leaves():
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.91)
    for _ in range(5):
        run(v, fake, clock, 1, memory=0.80)
    run(v, fake, clock, 1, memory=0.87)                               # over the lower mark: the count starts again
    for i in range(9):
        run(v, fake, clock, 1, memory=0.80)
        assert v.message()["present"] is True, i
    assert run(v, fake, clock, 1, memory=0.80)[0]["present"] is False
    # and in the band it never leaves
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.91)
    run(v, fake, clock, 40, memory=0.87)
    assert v.message()["present"] is True


def test_a_card_that_rose_stays_at_least_ten_seconds():
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.91)
    clock.t = 2.0                                                      # it rose now; the samples come fast
    fake.reading = reading(memory=0.50)
    for _ in range(30):
        v.tick()
    assert v.message()["present"] is True                              # thirty samples under, but no time has passed
    clock.t = 11.9
    v.tick()
    assert v.message()["present"] is True
    clock.t = 12.0
    assert v.tick()["present"] is False


def test_a_card_that_flickers_around_the_line_does_not_flicker():
    v, fake, clock = rig()
    said = []
    for i in range(60):                                                # 0.91 and 0.89 about the crossing mark
        fake.reading = reading(memory=0.91 if i % 2 == 0 else 0.89)
        said.append(v.tick())
        clock.t += 1
    assert not any(m and m["present"] for m in said[1:])               # it never gets three in a row
    said.clear()
    rise(v, fake, clock, memory=0.91)
    for i in range(60):                                                # once up, it never gets ten under in a row
        fake.reading = reading(memory=0.84 if i % 2 == 0 else 0.91)
        said.append(v.tick())
        clock.t += 1
    assert all(m is None or m["present"] for m in said)
    assert v.message()["present"] is True


def test_the_card_rises_once_and_leaves_once_for_a_reading_that_comes_and_goes():
    v, fake, clock = rig()
    out = [m for m in run(v, fake, clock, 5, memory=0.40) + run(v, fake, clock, 20, memory=0.93) +
           run(v, fake, clock, 30, memory=0.40) if m]
    presence = [m["present"] for m in out]
    changes = [a for a, b in zip(presence, presence[1:], strict=False) if a != b]
    assert changes == [False, True]                                    # up once, then down once


def test_the_sessions_line_is_crossed_at_ninety_percent_of_their_ceiling_and_says_so():
    v, fake, clock = rig()
    run(v, fake, clock, 5, sessions=0.89)
    assert v.message()["present"] is False
    m = rise(v, fake, clock, sessions=0.93)
    assert m["why"] == "Coding sessions are near their memory limit"
    assert m["strip"] == {"text": "sessions 93%", "dot": "amber"}
    assert next(r for r in m["rows"] if r["key"] == "memory")["tone"] == "you"      # memory itself is under its line
    run(v, fake, clock, 12, sessions=0.80)
    assert v.message()["present"] is False                                           # and it leaves at 80


def test_a_line_whose_reading_goes_away_is_left_like_one_that_fell_and_says_its_last_words_meanwhile():
    v, fake, clock = rig()
    rise(v, fake, clock, sessions=0.93)                                # crossed at t=2
    fake.reading = reading(sessions=None)                              # the sessions' slice is gone
    for i in range(9):
        clock.t += 1
        v.tick()
        m = v.message()
        assert m["present"] is True and m["strip"]["text"] == "sessions 93%", i
    clock.t += 1
    assert v.tick()["present"] is False


def test_the_sessions_line_has_nothing_to_cross_without_a_ceiling():
    v, fake, clock = rig()
    run(v, fake, clock, 10, sessions=None, held=20 * GB)
    assert v.message()["present"] is False


def test_memory_is_crossed_at_ninety_percent_used_and_left_at_eighty_five():
    v, fake, clock = rig()
    m = rise(v, fake, clock, memory=0.91)
    assert m["why"] == "Memory is nearly full"                        # no cgroups, so no one to name
    assert m["strip"] == {"text": "memory 91%", "dot": "amber"}
    row = m["rows"][0]
    assert (row["key"], row["tone"], row["meter"]) == ("memory", "amber", 0.91)
    run(v, fake, clock, 12, memory=0.86)
    assert v.message()["present"] is True
    run(v, fake, clock, 10, memory=0.85)
    assert v.message()["present"] is False


def test_the_disk_is_amber_from_ninety_and_red_from_ninety_seven():
    v, fake, clock = rig()
    m = rise(v, fake, clock, disk=0.94)
    assert m["why"] == "The disk is nearly full"
    assert m["strip"] == {"text": "disk 94%", "dot": "amber"}
    assert next(r for r in m["rows"] if r["key"] == "disk")["tone"] == "amber"
    m = run(v, fake, clock, 1, disk=0.965)[0]
    assert m["strip"]["dot"] == "amber" and next(r for r in m["rows"] if r["key"] == "disk")["tone"] == "amber"
    m = run(v, fake, clock, 1, disk=0.97)[0]
    assert m["strip"] == {"text": "disk 97%", "dot": "red"}
    assert next(r for r in m["rows"] if r["key"] == "disk")["tone"] == "red"
    run(v, fake, clock, 12, disk=0.89)
    assert v.message()["present"] is True                                  # the disk leaves at 88, not 90
    run(v, fake, clock, 10, disk=0.88)
    assert v.message()["present"] is False


def test_a_disk_past_the_red_line_makes_the_dot_red_whichever_line_the_words_are_about():
    v, fake, clock = rig()
    m = rise(v, fake, clock, sessions=0.95, disk=0.98)
    assert m["strip"] == {"text": "sessions 95%", "dot": "red"}
    assert next(r for r in m["rows"] if r["key"] == "disk")["tone"] == "red"
    m = rise(*rig(), sessions=0.95, disk=0.95)
    assert m["strip"] == {"text": "sessions 95%", "dot": "amber"}


def test_heat_is_crossed_at_eighty_degrees_and_left_at_seventy_two():
    v, fake, clock = rig()
    run(v, fake, clock, 5, heat=79.0)
    assert v.message()["present"] is False
    m = rise(v, fake, clock, heat=82.0)
    assert m["why"] == "The processor is hot"
    assert m["strip"] == {"text": "hot · 82°", "dot": "amber"}
    cpu = next(r for r in m["rows"] if r["key"] == "cpu")
    assert cpu["meterText"] == "82°" and cpu["tone"] == "you"
    run(v, fake, clock, 15, heat=73.0)
    assert v.message()["present"] is True
    run(v, fake, clock, 10, heat=72.0)
    assert v.message()["present"] is False


def test_the_lines_are_named_worst_first_in_one_line():
    v, fake, clock = rig()
    m = rise(v, fake, clock, memory=0.95, disk=0.92, heat=85.0, sessions=0.95)
    assert m["why"] == ("Coding sessions are near their memory limit · memory is nearly full · "
                        "your apps use most · the disk is nearly full · the processor is hot")
    assert m["strip"]["text"] == "sessions 95%"
    m = rise(*rig(), memory=0.40, disk=0.92, heat=85.0)
    assert m["why"] == "The disk is nearly full · the processor is hot"
    assert m["strip"]["text"] == "disk 92%"


def test_memory_says_who_holds_half_of_it_or_more():
    full = "Memory is nearly full"
    for held, said in (
            ({"held": 8 * GB}, f"{full} · sessions use most"),
            ({"machine_": 8 * GB}, f"{full} · the machine uses most"),
            ({"held": 2 * GB}, f"{full} · your apps use most"),             # 12.5 GB of the 14.5 are yours
            ({"held": 5 * GB, "machine_": 2 * GB}, f"{full} · your apps use most"),
            ({"held": 4 * GB, "machine_": 4 * GB}, full),                         # 4, 4 and 6.5 of 14.5: none has half
            ({}, full)):                                                          # no cgroups, no one to name
        v, fake, clock = rig()
        assert rise(v, fake, clock, memory=14.5 / 16, **held)["why"] == said, held


def test_half_exactly_is_enough():
    v, fake, clock = rig()
    # used 14.4 of 16 GB: 7.2 is half
    assert rise(v, fake, clock, memory=0.9, held=7_200_000_000)["why"].endswith("sessions use most")


# -- the message --

def test_the_message_has_the_shape_the_shell_reads():
    v, fake, clock = rig()
    fake.reading = reading(memory=14.5 / 16, disk=0.6, heat=62.4, cpu=0.37, net=(1.2e6, 40e3),
                           machine_=int(1.92 * GB), held=8 * GB)
    for _ in range(3):
        m = v.tick()
        clock.t += 1
    assert m == {
        "type": "machine", "present": True, "asked": False,
        "why": "Memory is nearly full · sessions use most",
        "strip": {"text": "memory 91%", "dot": "amber"},
        "rows": [
            {"key": "memory", "kind": "stack", "title": "Memory", "meterText": "14.5 of 16 GB", "meter": 0.91,
             "tone": "amber", "opens": "",
             "parts": [{"tone": "machine", "fraction": 0.12}, {"tone": "sessions", "fraction": 0.5},
                       {"tone": "you", "fraction": 0.29}]},
            {"key": "disk", "kind": "meter", "title": "Disk", "meterText": "138 of 230 GB", "meter": 0.6,
             "tone": "you", "opens": "disk"},
            {"key": "cpu", "kind": "meter", "title": "Processor", "meterText": "62°", "meter": 0.37,
             "tone": "you", "opens": ""},
            {"key": "net", "kind": "plain", "title": "Network", "sub": "↓ 1.2 MB/s   ↑ 40 kB/s", "tone": "you",
             "opens": ""}]}
    json.dumps(m)                                                      # it goes down a socket as it is


def test_the_disk_row_says_the_picture_it_opens_and_the_figures():
    v, fake, clock = rig()
    fake.reading = reading(disk=214 / 230)
    m = rise(v, fake, clock, disk=214 / 230)
    row = next(r for r in m["rows"] if r["key"] == "disk")
    assert row == {"key": "disk", "kind": "meter", "title": "Disk", "meterText": "214 of 230 GB", "meter": 0.93,
                   "tone": "amber", "opens": "disk"}
    assert m["strip"] == {"text": "disk 93%", "dot": "amber"}


def test_without_cgroups_the_memory_row_has_only_the_you_part():
    v, fake, clock = rig()
    m = rise(v, fake, clock, memory=0.91)
    assert m["rows"][0]["parts"] == [{"tone": "you", "fraction": 0.91}]


def test_rows_for_what_could_not_be_read_are_left_out_but_the_processor_and_network_stay():
    v, fake, clock = rig()
    fake.reading = Reading(cpu=0.5, heat=85.0)
    for _ in range(3):
        m = v.tick()
        clock.t += 1
    assert m["present"] is True and m["why"] == "The processor is hot"
    assert [r["key"] for r in m["rows"]] == ["cpu", "net"]
    assert m["rows"][0]["meterText"] == "85°"
    assert m["rows"][1]["sub"] == "↓ 0 B/s   ↑ 0 B/s"                  # no rate yet is none going


def test_the_processor_row_has_no_heat_clause_without_a_reading():
    v, fake, clock = rig()
    m = rise(v, fake, clock, memory=0.95, heat=None, cpu=None)
    cpu = next(r for r in m["rows"] if r["key"] == "cpu")
    assert cpu["meterText"] == "" and cpu["meter"] == 0.0


def test_the_network_row_is_always_there_and_says_the_last_rates():
    v, fake, clock = rig()
    m = rise(v, fake, clock, memory=0.95, net=(2.5e6, 130e3))
    assert m["rows"][-1] == {"key": "net", "kind": "plain", "title": "Network", "tone": "you", "opens": "",
                             "sub": "↓ 2.5 MB/s   ↑ 130 kB/s"}


def test_the_message_for_a_machine_with_nothing_to_say_takes_the_card_away():
    v, fake, clock = rig()
    assert v.message() == {"type": "machine", "present": False, "asked": False, "why": "",
                           "strip": {"text": "", "dot": ""}, "rows": []}
    assert v.tick() == v.message()
    json.dumps(v.message())


def test_message_is_the_current_card_for_a_client_that_connects_and_costs_no_sample():
    v, fake, clock = rig()
    assert v.message()["present"] is False                              # nothing is known before the first sample
    rise(v, fake, clock, memory=0.95)
    taken = len(fake.fast)
    m = v.message()
    assert m["present"] is True and m["rows"][0]["key"] == "memory"
    assert len(fake.fast) == taken
    m["rows"].clear()
    assert v.message()["rows"]                                          # a caller cannot spoil the card by editing it
    fake.reading = reading(memory=0.95)
    assert v.tick() is None                                             # and asking did not use up the message


def test_a_calm_machine_says_the_same_thing_every_sample_and_tick_says_nothing():
    fs, clock = machine(), Clock()
    v = Vitals(sampler=sampler(fs, clock), clock=clock)
    first = v.tick()
    assert first["present"] is False
    for i in range(30):
        clock.t += CALM_EVERY
        fs.files["/proc/stat"] = stat_text(user=4705 + 37 * (i + 1) * (i % 3 + 1), idle=84001 + 100 * (i + 1))
        fs.files["/proc/meminfo"] = meminfo_text(available_kb=9_000_000 + (i % 5) * 1000)
        fs.files["/sys/class/thermal/thermal_zone0/temp"] = f"{47000 + (i % 7) * 100}\n"
        assert v.tick() is None, i
        assert v.message() == first


def test_while_the_card_is_up_a_changing_reading_is_a_new_message_and_a_steady_one_is_not():
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.95, cpu=0.20)
    assert run(v, fake, clock, 1, memory=0.95, cpu=0.21)[0] is not None
    assert run(v, fake, clock, 1, memory=0.95, cpu=0.21)[0] is None
    assert run(v, fake, clock, 1, memory=0.95, cpu=0.2149)[0] is None          # the same to two places and a percent


def test_the_first_tick_after_a_reset_is_always_a_message_even_for_nothing_to_say():
    v, fake, clock = rig()
    assert v.tick()["present"] is False
    assert v.tick() is None
    v.reset()
    assert v.tick()["present"] is False
    assert fake.resets == 1


def test_reset_forgets_the_lines_the_asking_and_the_samplers_baselines():
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.95)
    v.ask()
    v.reset()
    assert v.message()["present"] is False and v.next_delay() == CALM_EVERY
    out = run(v, fake, clock, 2, memory=0.95)
    assert out[0]["present"] is False and out[1] is None               # two samples over it again, not three
    assert run(v, fake, clock, 1, memory=0.95)[0]["present"] is True


# -- asking --

def test_asking_raises_the_card_for_that_long_without_a_line_crossed():
    v, fake, clock = rig()
    v.tick()
    v.ask(20)
    m = v.tick()
    assert m["present"] is True and m["asked"] is True
    assert m["why"] == "The machine is fine"
    assert [r["key"] for r in m["rows"]] == ["memory", "disk", "cpu", "net"]
    assert m["rows"][0]["tone"] == "you"
    clock.t += 19
    assert v.tick() is None and v.message()["present"] is True
    clock.t += 2
    gone = v.tick()
    assert gone["present"] is False and gone["asked"] is False


def test_asking_is_a_default_of_thirty_seconds():
    v, fake, clock = rig()
    v.tick()
    v.ask()
    clock.t += vitals.ASK_FOR - 1
    assert v.message()["present"] is True
    clock.t += 2
    assert v.message()["present"] is False


def test_asked_says_the_reading_nearest_its_line_with_no_dot():
    v, fake, clock = rig()
    fake.reading = reading(memory=0.42, disk=0.50, heat=40.0)
    v.tick()
    v.ask()
    assert v.tick()["strip"] == {"text": "disk 50%", "dot": ""}
    fake.reading = reading(memory=0.70, disk=0.50, heat=40.0)
    assert v.tick()["strip"] == {"text": "memory 70%", "dot": ""}
    fake.reading = Reading()
    assert v.tick()["strip"] == {"text": "machine", "dot": ""}


def test_asking_while_a_line_is_crossed_is_not_asked_and_when_the_line_leaves_the_ask_still_holds():
    v, fake, clock = rig()
    rise(v, fake, clock, memory=0.95)
    v.ask(60)
    m = run(v, fake, clock, 1, memory=0.95)[0]
    assert m is None or m["asked"] is False
    assert v.message()["asked"] is False
    run(v, fake, clock, 14, memory=0.40)                                # the line leaves
    m = v.message()
    assert m["present"] is True and m["asked"] is True
    clock.t += 60
    assert v.tick()["present"] is False


def test_asking_again_cannot_shorten_the_ask():
    v, fake, clock = rig()
    v.tick()
    v.ask(30)
    v.ask(5)
    clock.t += 20
    assert v.message()["present"] is True


def test_asking_before_the_first_sample_is_not_a_card_of_nothing():
    v, fake, clock = rig()
    v.ask()
    assert v.message()["present"] is False
    m = v.tick()
    assert m["present"] is True and m["asked"] is True


# -- how often --

def test_the_next_sample_is_five_seconds_off_when_calm_and_one_when_the_card_is_up_or_asked():
    v, fake, clock = rig()
    assert v.next_delay() == CALM_EVERY == 5.0
    run(v, fake, clock, 1, memory=0.40)
    assert v.next_delay() == CALM_EVERY
    v.ask(10)
    assert v.next_delay() == FAST_EVERY == 1.0
    clock.t += 11
    assert v.next_delay() == CALM_EVERY
    rise(v, fake, clock, memory=0.95)
    assert v.next_delay() == FAST_EVERY
    run(v, fake, clock, 30, memory=0.40)
    assert v.next_delay() == CALM_EVERY


def test_a_reading_within_five_points_of_its_line_is_sampled_fast():
    for kw, calm, near in (("memory", 0.83, 0.86), ("disk", 0.83, 0.86), ("sessions", 0.83, 0.86),
                           ("heat", 73.0, 76.0)):
        v, fake, clock = rig()
        run(v, fake, clock, 1, **{kw: calm})
        assert v.next_delay() == CALM_EVERY, kw
        run(v, fake, clock, 1, **{kw: near})
        assert v.next_delay() == FAST_EVERY, kw
        run(v, fake, clock, 1, **{kw: calm})
        assert v.next_delay() == CALM_EVERY, kw


def test_the_processor_has_no_line_so_a_busy_one_is_not_a_reason_to_hurry():
    v, fake, clock = rig()
    run(v, fake, clock, 3, cpu=1.0)
    assert v.next_delay() == CALM_EVERY


def test_the_first_sample_reads_everything_and_calm_ones_after_it_read_less():
    v, fake, clock = rig()
    for _ in range(3):
        v.tick()
        clock.t += CALM_EVERY
    assert fake.fast == [True, False, False]
    v.ask()
    v.tick()
    assert fake.fast[-1] is True
    fake.reading = reading(disk=0.86)
    clock.t += 100
    v.tick()
    v.tick()
    # the sample that finds a reading near its line was taken calm; the one after it walks and reads the net
    assert fake.fast[-2:] == [False, True]


# -- when nothing works --

def test_nothing_readable_gives_a_card_that_is_not_there_and_never_raises():
    s = Sampler(read=lambda p: None, listdir=lambda p: None, statvfs=lambda p: None, clock=Clock(), uid=lambda: 1000,
                root=ROOT)
    v = Vitals(sampler=s, clock=Clock())
    assert v.tick()["present"] is False
    for _ in range(20):
        assert v.tick() is None
        assert v.next_delay() == CALM_EVERY
    v.ask()
    m = v.tick()
    assert m["present"] is True and [r["key"] for r in m["rows"]] == ["cpu", "net"]
    assert m["strip"] == {"text": "machine", "dot": ""}


def test_a_machine_with_every_file_full_of_garbage_does_not_raise():
    rng = random.Random(3)
    junk = ["", "\n", "max", "-1", "0", "garbage\x00\x01", "1_0", "9" * 40, "cpu", "\xe9\xe9", ":::",
            "a b c d e f g h i"]
    fs = machine()
    for p in list(fs.files):
        fs.files[p] = rng.choice(junk)
    clock = Clock()
    v = Vitals(sampler=sampler(fs, clock), clock=clock)
    v.ask()
    for _ in range(12):
        v.tick()
        v.message()
        v.next_delay()
        clock.t += 1


def test_without_systemd_or_cgroup_v2_the_card_still_works_on_memory_and_disk():
    fs = machine(**{"/proc/self/cgroup": "", "/proc/meminfo": meminfo_text(16_000_000, 1_000_000)})
    clock = Clock()
    v = Vitals(sampler=sampler(fs, clock), clock=clock)
    for _ in range(3):
        m = v.tick()
        clock.t += 1
    assert m["present"] is True and m["why"] == "Memory is nearly full"
    assert m["rows"][0]["parts"][0]["tone"] == "you" and len(m["rows"][0]["parts"]) == 1


def test_a_crowded_machine_end_to_end_through_the_real_sampler_on_a_fake_tree():
    """Memory 91% with the sessions holding most of it and nearly at their own ceiling."""
    fs = machine(**{"/proc/meminfo": meminfo_text(16_000_000, 1_450_000)})
    fs.unit(f"{BASE}/bombadil.slice/bombadil-dev.slice", 12 * GB, inactive=1 * GB, high=12 * GB, limit="max")
    clock = Clock()
    v = Vitals(sampler=sampler(fs, clock), clock=clock)
    for _ in range(3):
        m = v.tick()
        clock.t += 1
    assert m["why"] == ("Coding sessions are near their memory limit · memory is nearly full · "
                        "sessions use most")
    assert m["strip"] == {"text": "sessions 92%", "dot": "amber"}
    assert [r["key"] for r in m["rows"]] == ["memory", "disk", "cpu", "net"]
    memory = m["rows"][0]
    assert memory["meterText"] == "14.9 of 16.4 GB" and memory["tone"] == "amber"
    assert [p["tone"] for p in memory["parts"]] == ["machine", "sessions", "you"]
    assert sum(p["fraction"] for p in memory["parts"]) <= 1 + 1e-9


def test_vitals_can_be_driven_from_two_threads():
    fs, clock = machine(), Clock()
    v = Vitals(sampler=sampler(fs, clock), clock=clock)
    stop, errors = threading.Event(), []

    def poke():
        try:
            while not stop.is_set():
                v.ask(1)
                v.message()
                v.next_delay()
        except Exception as e:                                          # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=poke)
    t.start()
    try:
        for _ in range(200):
            v.tick()
            clock.t += 1
    finally:
        stop.set()
        t.join()
    assert errors == []


def test_a_client_that_connects_during_a_sample_gets_the_card_that_sample_made():
    entered, release = threading.Event(), threading.Event()
    got = []

    class Slow(Fake):
        def sample(self, fast):
            entered.set()
            release.wait(5)
            return super().sample(fast)

    v = Vitals(sampler=Slow(), clock=Clock(0.0))
    ticking = threading.Thread(target=v.tick)
    ticking.start()
    assert entered.wait(5)
    asking = threading.Thread(target=lambda: got.append(v.message()))
    asking.start()
    asking.join(0.3)
    assert asking.is_alive()                                           # it waits for the sample, not reads half of it
    release.set()
    ticking.join(5)
    asking.join(5)
    assert got == [v.message()]


def test_vitals_with_no_sampler_given_reads_this_machine_without_raising():
    v = Vitals()
    first = v.tick()
    assert first["type"] == "machine" and v.next_delay() in (CALM_EVERY, FAST_EVERY)
    v.ask()
    asked = v.tick()
    assert asked["present"] is True and asked["asked"] is True and asked["rows"][-1]["key"] == "net"
    json.dumps(asked)


def test_the_module_needs_nothing_but_the_standard_library():
    """agentd imports it and the package has no dependencies: it must not pull in the app kit or Qt."""
    code = ("import sys; sys.modules['PySide6'] = None; import bombadil.vitals; "
            "print(' '.join(sorted(m for m in sys.modules if m.startswith('bombadil'))))")
    src = str(pathlib.Path(vitals.__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": src})
    assert out.stdout.split() == ["bombadil", "bombadil.procs", "bombadil.vitals"]

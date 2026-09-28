import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bombadil.brain import fanotify as fan

HANDLE = struct.pack("=Ii", 8, 1) + b"\x11\x22\x33\x44\x55\x66\x77\x88"   # struct file_handle
HANDLE2 = struct.pack("=Ii", 12, 0x4d) + bytes(range(12))


def fid(kind: int, handle: bytes = HANDLE, name: bytes | None = None, fsid=(0x1234, 0x5678)) -> bytes:
    body = struct.pack("=ii", *fsid) + handle
    if name is not None:
        body += name + b"\0"
    size = 4 + len(body)
    padded = (size + 3) & ~3
    return struct.pack("=BBH", kind, 0, padded) + body + b"\0" * (padded - size)


def pidfd(fd: int) -> bytes:
    return struct.pack("=BBHi", fan.INFO_PIDFD, 0, 8, fd)


def event(mask: int, pid: int, *records: bytes, fd: int = -1, meta_len: int = 24, vers: int = 3) -> bytes:
    body = b"".join(records)
    extra = b"\0" * (meta_len - 24)
    return struct.pack("=IBBHQii", meta_len + len(body), vers, 0, meta_len, mask, fd, pid) + extra + body


def test_layouts_match_the_uapi_header():
    assert fan.META.size == 24
    assert fan.INFO_HEADER.size == 4
    assert fan.FSID.size == 8
    assert fan.HANDLE_HEADER.size == 8


@pytest.mark.skipif(not Path("/usr/include/linux/fanotify.h").exists(), reason="no kernel headers")
def test_constants_match_the_uapi_header():
    text = Path("/usr/include/linux/fanotify.h").read_text()
    defs = dict(re.findall(r"#define\s+(\w+)\s+(0x[0-9a-fA-F]+|-?\d+)\b", text))
    names = {
        "FAN_CLOSE_WRITE": fan.FAN_CLOSE_WRITE, "FAN_CREATE": fan.FAN_CREATE, "FAN_DELETE": fan.FAN_DELETE,
        "FAN_RENAME": fan.FAN_RENAME, "FAN_ONDIR": fan.FAN_ONDIR, "FAN_Q_OVERFLOW": fan.FAN_Q_OVERFLOW,
        "FAN_CLOEXEC": fan.FAN_CLOEXEC, "FAN_NONBLOCK": fan.FAN_NONBLOCK,
        "FAN_UNLIMITED_QUEUE": fan.FAN_UNLIMITED_QUEUE, "FAN_REPORT_PIDFD": fan.FAN_REPORT_PIDFD,
        "FAN_REPORT_FID": fan.FAN_REPORT_FID, "FAN_REPORT_DIR_FID": fan.FAN_REPORT_DIR_FID,
        "FAN_REPORT_NAME": fan.FAN_REPORT_NAME, "FAN_MARK_ADD": fan.FAN_MARK_ADD,
        "FAN_MARK_FILESYSTEM": fan.FAN_MARK_FILESYSTEM, "FAN_EVENT_INFO_TYPE_FID": fan.INFO_FID,
        "FAN_EVENT_INFO_TYPE_DFID_NAME": fan.INFO_DFID_NAME, "FAN_EVENT_INFO_TYPE_DFID": fan.INFO_DFID,
        "FAN_EVENT_INFO_TYPE_PIDFD": fan.INFO_PIDFD, "FAN_EVENT_INFO_TYPE_OLD_DFID_NAME": fan.INFO_OLD_DFID_NAME,
        "FAN_EVENT_INFO_TYPE_NEW_DFID_NAME": fan.INFO_NEW_DFID_NAME, "FAN_EPIDFD": fan.FAN_EPIDFD,
        "FANOTIFY_METADATA_VERSION": fan.METADATA_VERSION,
    }
    for name, value in names.items():
        if name in defs:
            assert int(defs[name], 0) == value, name


def test_create_with_dir_handle_name_and_pidfd():
    buf = event(fan.FAN_CREATE, 4242, fid(fan.INFO_DFID_NAME, name=b"a.txt"), pidfd(7))
    [e] = fan.parse(buf)
    assert e.mask == fan.FAN_CREATE and e.pid == 4242 and e.fd == -1
    assert e.dir == HANDLE and e.name == b"a.txt" and e.pidfd == 7
    assert e.fsid == struct.pack("=ii", 0x1234, 0x5678)
    assert not e.bad
    assert e.fds() == [7]


def test_rename_carries_old_and_new():
    buf = event(fan.FAN_RENAME | fan.FAN_ONDIR, 9,
                fid(fan.INFO_OLD_DFID_NAME, HANDLE, b"old"), fid(fan.INFO_NEW_DFID_NAME, HANDLE2, b"new-name"),
                pidfd(fan.FAN_NOPIDFD))
    [e] = fan.parse(buf)
    assert (e.old_dir, e.old_name, e.new_dir, e.new_name) == (HANDLE, b"old", HANDLE2, b"new-name")
    assert e.dir is None and e.pidfd == -1
    assert e.fds() == []   # FAN_NOPIDFD is not an fd


def test_several_events_merged_mask_and_names_that_are_not_utf8():
    buf = (event(fan.FAN_CREATE | fan.FAN_CLOSE_WRITE, 1, fid(fan.INFO_DFID_NAME, name=b"bad\xff\nname"), pidfd(3))
           + event(fan.FAN_DELETE, 2, fid(fan.INFO_DFID_NAME, name=b"x"), pidfd(fan.FAN_EPIDFD))
           + event(fan.FAN_Q_OVERFLOW, 0))
    a, b, c = fan.parse(buf)
    assert a.mask == fan.FAN_CREATE | fan.FAN_CLOSE_WRITE and a.name == b"bad\xff\nname"
    assert b.pidfd == fan.FAN_EPIDFD and b.fds() == []
    assert c.mask == fan.FAN_Q_OVERFLOW and c.dir is None and c.pidfd is None


def test_padding_is_walked_by_record_length():
    # A 5-byte name makes a record that needs padding; the pidfd record after it must parse.
    for name in (b"a", b"ab", b"abc", b"abcd", b"abcde"):
        [e] = fan.parse(event(fan.FAN_CLOSE_WRITE, 5, fid(fan.INFO_DFID_NAME, name=name), pidfd(11)))
        assert e.name == name and e.pidfd == 11


def test_child_fid_and_dfid_records():
    buf = event(fan.FAN_CREATE, 3, fid(fan.INFO_DFID_NAME, HANDLE, b"n"), fid(fan.INFO_FID, HANDLE2))
    [e] = fan.parse(buf)
    assert e.fid == HANDLE2 and e.dir == HANDLE
    [e] = fan.parse(event(fan.FAN_CREATE | fan.FAN_ONDIR, 3, fid(fan.INFO_DFID, HANDLE)))
    assert e.dir == HANDLE and e.name is None


def test_longer_metadata_and_unknown_records_are_skipped():
    unknown = struct.pack("=BBH", 99, 0, 12) + b"\xee" * 8
    buf = event(fan.FAN_CLOSE_WRITE, 8, unknown, fid(fan.INFO_DFID_NAME, name=b"f"), pidfd(4), meta_len=32)
    [e] = fan.parse(buf)
    assert e.name == b"f" and e.pidfd == 4 and not e.bad


def test_short_and_malformed_input_never_raises():
    good = event(fan.FAN_CREATE, 1, fid(fan.INFO_DFID_NAME, name=b"a"), pidfd(5))
    # A truncated tail is dropped, the whole events before it are kept.
    assert len(fan.parse(good + good[:30])) == 1
    for cut in range(len(good)):
        fan.parse(good[:cut])
    # A record whose length runs past its event marks the event bad but keeps what parsed.
    broken = bytearray(event(fan.FAN_CREATE, 1, pidfd(5), fid(fan.INFO_DFID_NAME, name=b"a")))
    struct.pack_into("=H", broken, 24 + 8 + 2, 200)
    [e] = fan.parse(bytes(broken))
    assert e.bad and e.pidfd == 5
    # A handle that claims more bytes than its record holds.
    lying = struct.pack("=Ii", 500, 1) + b"\0" * 8
    [e] = fan.parse(event(fan.FAN_CREATE, 1, fid(fan.INFO_DFID_NAME, lying, b"a")))
    assert e.bad and e.dir is None
    # Garbage metadata (wrong version, zero length) stops the walk.
    assert fan.parse(event(fan.FAN_CREATE, 1, vers=2)) == []
    assert fan.parse(b"\0" * 64) == []


@pytest.mark.skipif(os.geteuid() != 0, reason="fanotify filesystem marks need root")
def test_real_kernel_events_parse(tmp_path):
    flags = (fan.FAN_CLASS_NOTIF | fan.FAN_CLOEXEC | fan.FAN_NONBLOCK | fan.FAN_REPORT_DFID_NAME
             | fan.FAN_REPORT_PIDFD)
    try:
        fd = fan.init(flags)
        fan.mark(fd, fan.FAN_MARK_ADD | fan.FAN_MARK_FILESYSTEM,
                 fan.FAN_CREATE | fan.FAN_DELETE | fan.FAN_RENAME | fan.FAN_CLOSE_WRITE | fan.FAN_ONDIR, tmp_path)
    except OSError as e:
        pytest.skip(f"fanotify not usable here: {e}")
    mount_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        subprocess.run(["sh", "-c", "echo x > a && mv a b && rm b"], cwd=tmp_path, check=True)
        events = []
        end = time.monotonic() + 5
        while len(events) < 3 and time.monotonic() < end:
            try:
                buf = os.read(fd, 65536)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            events += [e for e in fan.parse(buf) if (e.name or e.new_name) in (b"a", b"b")]
        for e in events:
            for f in e.fds():
                os.close(f)
        kinds = [e.mask & (fan.FAN_CREATE | fan.FAN_RENAME | fan.FAN_DELETE) for e in events]
        assert kinds == [fan.FAN_CREATE, fan.FAN_RENAME, fan.FAN_DELETE], [hex(e.mask) for e in events]
        d = fan.open_by_handle(mount_fd, events[0].dir)
        try:
            assert os.readlink(f"/proc/self/fd/{d}") == str(tmp_path)
        finally:
            os.close(d)
        assert events[1].old_name == b"a" and events[1].new_name == b"b"
    finally:
        os.close(mount_fd)
        os.close(fd)
    assert sys.platform.startswith("linux")

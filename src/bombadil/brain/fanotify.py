"""fanotify for the brain's file watcher: the constants, three libc calls, and a pure parser.

The watcher asks for directory-entry events (create, delete, rename) and close-write on a
whole filesystem, reported by file handle and name (FAN_REPORT_DFID_NAME) with a pidfd for the
writer (FAN_REPORT_PIDFD). Nothing here opens files or follows paths: `parse` turns the bytes
read from the fanotify fd into events, so it can be tested with buffers built by hand, and
the caller turns handles into paths and closes every fd an event carries.

Layouts, from include/uapi/linux/fanotify.h:
  struct fanotify_event_metadata   u32 event_len, u8 vers, u8 reserved, u16 metadata_len,
                                   u64 mask, s32 fd, s32 pid                     (24 bytes)
  struct fanotify_event_info_header  u8 info_type, u8 pad, u16 len   (len covers the record,
                                   padding included, so records are walked by it)
  fid records (FID, DFID, DFID_NAME, OLD_/NEW_DFID_NAME)
                                   header, __kernel_fsid_t fsid (8 bytes), struct file_handle
                                   {u32 handle_bytes; s32 handle_type; f_handle[handle_bytes]},
                                   then for the *_NAME types a NUL-terminated name
  pidfd record                     header, s32 pidfd (FAN_NOPIDFD -1: the writer had already
                                   been reaped; FAN_EPIDFD -2: the kernel could not make one)
"""

import ctypes
import ctypes.util
import os
import struct
from dataclasses import dataclass

# Events (the ones the watcher uses, and what can come back).
FAN_ACCESS = 0x00000001
FAN_MODIFY = 0x00000002
FAN_ATTRIB = 0x00000004
FAN_CLOSE_WRITE = 0x00000008
FAN_MOVED_FROM = 0x00000040
FAN_MOVED_TO = 0x00000080
FAN_CREATE = 0x00000100
FAN_DELETE = 0x00000200
FAN_DELETE_SELF = 0x00000400
FAN_MOVE_SELF = 0x00000800
FAN_Q_OVERFLOW = 0x00004000
FAN_FS_ERROR = 0x00008000
FAN_RENAME = 0x10000000          # 5.17: one event with the old and the new dir + name
FAN_ONDIR = 0x40000000

# fanotify_init flags.
FAN_CLOEXEC = 0x00000001
FAN_NONBLOCK = 0x00000002
FAN_CLASS_NOTIF = 0x00000000
FAN_UNLIMITED_QUEUE = 0x00000010
FAN_REPORT_PIDFD = 0x00000080    # 5.15
FAN_REPORT_TID = 0x00000100
FAN_REPORT_FID = 0x00000200
FAN_REPORT_DIR_FID = 0x00000400
FAN_REPORT_NAME = 0x00000800
FAN_REPORT_TARGET_FID = 0x00001000
FAN_REPORT_DFID_NAME = FAN_REPORT_DIR_FID | FAN_REPORT_NAME

# fanotify_mark flags.
FAN_MARK_ADD = 0x00000001
FAN_MARK_REMOVE = 0x00000002
FAN_MARK_DONT_FOLLOW = 0x00000004
FAN_MARK_ONLYDIR = 0x00000008
FAN_MARK_INODE = 0x00000000
FAN_MARK_MOUNT = 0x00000010
FAN_MARK_FILESYSTEM = 0x00000100

# Info record types.
INFO_FID = 1
INFO_DFID_NAME = 2
INFO_DFID = 3
INFO_PIDFD = 4
INFO_ERROR = 5
INFO_OLD_DFID_NAME = 10
INFO_NEW_DFID_NAME = 12

FAN_NOFD = -1
FAN_NOPIDFD = -1
FAN_EPIDFD = -2
METADATA_VERSION = 3

META = struct.Struct("=IBBHQii")      # fanotify_event_metadata, 24 bytes
INFO_HEADER = struct.Struct("=BBH")   # fanotify_event_info_header, 4 bytes
FSID = struct.Struct("=ii")           # __kernel_fsid_t, 8 bytes
HANDLE_HEADER = struct.Struct("=Ii")  # struct file_handle before f_handle, 8 bytes
PIDFD = struct.Struct("=i")
ERROR = struct.Struct("=iI")

AT_FDCWD = -100


@dataclass
class Event:
    mask: int
    pid: int
    fd: int = FAN_NOFD           # never set in FID mode, but closed by the caller if it is
    pidfd: int | None = None     # None: the group has no FAN_REPORT_PIDFD (or no record came)
    fsid: bytes = b""
    dir: bytes | None = None     # struct file_handle bytes, ready for open_by_handle_at
    name: bytes | None = None
    old_dir: bytes | None = None     # FAN_RENAME: where it was
    old_name: bytes | None = None
    new_dir: bytes | None = None     # FAN_RENAME: where it is now
    new_name: bytes | None = None
    fid: bytes | None = None     # the object's own handle (FAN_REPORT_FID / TARGET_FID)
    error: int = 0               # FAN_FS_ERROR
    bad: bool = False            # a record did not parse; the rest of the event is best effort

    def fds(self) -> list[int]:
        """Every fd this event handed us, to close once it has been used."""
        return [fd for fd in (self.fd, self.pidfd) if fd is not None and fd >= 0]


def _fid(buf: bytes, start: int, end: int, named: bool):
    """(fsid, handle bytes, name or None) of a fid record's body, or None if it is short."""
    if start + FSID.size + HANDLE_HEADER.size > end:
        return None
    fsid = bytes(buf[start:start + FSID.size])
    h = start + FSID.size
    nbytes, _ = HANDLE_HEADER.unpack_from(buf, h)
    hend = h + HANDLE_HEADER.size + nbytes
    if hend > end:
        return None
    handle = bytes(buf[h:hend])
    name = None
    if named:
        nul = buf.find(b"\0", hend, end)
        name = bytes(buf[hend:nul if nul >= 0 else end])
    return fsid, handle, name


def parse(buf: bytes) -> list[Event]:
    """The events in one read() of a fanotify fd. Never raises on odd input: a short or
    malformed tail is dropped, and a record that does not parse marks its event `bad`."""
    out = []
    off, total = 0, len(buf)
    while off + META.size <= total:
        event_len, vers, _, meta_len, mask, fd, pid = META.unpack_from(buf, off)
        if event_len < META.size or off + event_len > total or vers != METADATA_VERSION \
                or meta_len < META.size or meta_len > event_len:
            break
        ev = Event(mask=mask, pid=pid, fd=fd)
        end = off + event_len
        rec = off + meta_len
        while rec + INFO_HEADER.size <= end:
            kind, _, rlen = INFO_HEADER.unpack_from(buf, rec)
            if rlen < INFO_HEADER.size or rec + rlen > end:
                ev.bad = True
                break
            body = rec + INFO_HEADER.size
            rend = rec + rlen
            if kind == INFO_PIDFD:
                if body + PIDFD.size <= rend:
                    ev.pidfd = PIDFD.unpack_from(buf, body)[0]
                else:
                    ev.bad = True
            elif kind in (INFO_FID, INFO_DFID, INFO_DFID_NAME, INFO_OLD_DFID_NAME, INFO_NEW_DFID_NAME):
                named = kind in (INFO_DFID_NAME, INFO_OLD_DFID_NAME, INFO_NEW_DFID_NAME)
                got = _fid(buf, body, rend, named)
                if got is None:
                    ev.bad = True
                else:
                    ev.fsid, handle, name = got
                    if kind == INFO_FID:
                        ev.fid = handle
                    elif kind == INFO_OLD_DFID_NAME:
                        ev.old_dir, ev.old_name = handle, name
                    elif kind == INFO_NEW_DFID_NAME:
                        ev.new_dir, ev.new_name = handle, name
                    else:
                        ev.dir, ev.name = handle, name
            elif kind == INFO_ERROR:
                if body + ERROR.size <= rend:
                    ev.error = ERROR.unpack_from(buf, body)[0]
            # Unknown record types (newer kernels) are skipped by their length.
            rec = rend
        out.append(ev)
        off = end
    return out


# --- libc -------------------------------------------------------------------------------

_libc = None


def libc():
    global _libc
    if _libc is None:
        lib = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        lib.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
        lib.fanotify_init.restype = ctypes.c_int
        lib.fanotify_mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int,
                                      ctypes.c_char_p]
        lib.fanotify_mark.restype = ctypes.c_int
        lib.open_by_handle_at.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        lib.open_by_handle_at.restype = ctypes.c_int
        _libc = lib
    return _libc


def _check(ret: int, what: str) -> int:
    if ret < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")
    return ret


def init(flags: int, event_f_flags: int = os.O_RDONLY | os.O_CLOEXEC) -> int:
    return _check(libc().fanotify_init(flags, event_f_flags), "fanotify_init")


def mark(fd: int, flags: int, mask: int, path: str | bytes, dirfd: int = AT_FDCWD) -> None:
    p = os.fsencode(path)
    _check(libc().fanotify_mark(fd, flags, mask, dirfd, p), "fanotify_mark")


def open_by_handle(mount_fd: int, handle: bytes, flags: int = os.O_PATH | os.O_CLOEXEC) -> int:
    """An fd for the object a struct file_handle names (needs CAP_DAC_READ_SEARCH). ESTALE
    means it no longer exists."""
    # The kernel copies handle_bytes + 8 bytes from the pointer; a bytes object is enough.
    return _check(libc().open_by_handle_at(mount_fd, handle, flags), "open_by_handle_at")

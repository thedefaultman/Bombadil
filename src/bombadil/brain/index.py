"""The first index of a home, and the walk that catches up when nobody was watching.

The watcher sees every save from the moment it starts, but a home already holds years of
files, and while the watcher or the brain was stopped (or the watcher's queue overflowed)
files were made, moved and deleted unseen. The first index walks the home once and records
everything as there before the brain; a file's user.bombadil.made_by xattr still says which
turn made it. Reconcile walks again after a gap and settles it: a thing whose path is gone
either moved (a new path with the same inode) or was deleted, and a path nobody knows is
found. Nothing is ever rescanned on a timer.

Both walks yield to everything else: they run in their own thread at nice 10 and idle I/O
priority, commit ~500 entries at a time so the live stream never waits long for the write
lock, stop between batches when asked, and prune what a person would not call a thing
(caches, most dot-folders, git-ignored files, node_modules) before reading it. A folder of
more than MAX_ENTRIES entries is generated data: one thing that says how many files it has.
"""

import ctypes
import os
import platform
import threading
import time
from collections import deque

from . import rules
from .ingest import UNKNOWN, Ingest
from .store import Store

# A folder with more direct entries than this is indexed as the folder alone ("data,
# 1.2 M files") rather than walked into.
MAX_ENTRIES = 5000
BATCH = 500
# A save the watcher did not see is only believed when the file is newer than the brain's
# last word on it by more than this (clocks and the watcher's own delay).
CHANGED_SLACK_S = 2.0
# ioprio_set(2): which=IOPRIO_WHO_PROCESS with who=0 is the calling thread; class 3 is idle.
IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "riscv64": 30}
IOPRIO_WHO_PROCESS = 1
IOPRIO_CLASS_IDLE = 3
IOPRIO_CLASS_SHIFT = 13

THING, PASS = "thing", "pass"   # index it; walk through it without indexing it (~/.config)
_lowered = threading.local()


def lower_priority() -> None:
    """nice 10 and idle I/O for the calling thread, once. Linux keeps both per thread, so
    the service's own loop is untouched."""
    if getattr(_lowered, "done", False):
        return
    _lowered.done = True
    try:
        os.nice(10)
    except OSError:
        pass
    nr = IOPRIO_SET.get(platform.machine())
    if nr is None:
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall(nr, IOPRIO_WHO_PROCESS, 0, IOPRIO_CLASS_IDLE << IOPRIO_CLASS_SHIFT)
    except (OSError, AttributeError):
        pass


def _storable(path: str) -> bool:
    """A name that is not valid UTF-8 cannot be stored or shown; it stays out."""
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _exists(path: str) -> bool:
    """Whether a path is still there. A folder that became unreadable hides its contents
    but did not delete them, so only "no such file" counts as gone."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


class Walker:
    """Walks one home into brain.db. It opens its own Store and Ingest because it runs in
    its own thread, beside the service's."""

    def __init__(self, db_path, home: str, xattrs: bool = True):
        self.db_path = str(db_path)
        self.home = str(home).rstrip("/") or "/"
        self.store = Store(self.db_path)
        self.ingest = Ingest(self.store, self.home, xattrs=xattrs)
        # brain.db itself, wherever it is kept (it lives in a skipped folder on a real system).
        self._own = {self.db_path + s for s in ("", "-wal", "-shm", "-journal")}
        self._caps: list[tuple[str, int | None]] = []

    def close(self) -> None:
        self.store.close()

    # -- the two walks --

    def first_index(self, progress=None, stop=None) -> dict:
        """Everything in home, as there before the brain (or made by whoever its xattr
        names). Sets meta "indexed" when it finished; a stopped walk leaves it unset, and
        running it again goes over what it already found at no cost."""
        lower_priority()
        found = 0

        def visit(path, st, is_dir, verdict):
            nonlocal found
            if verdict == PASS:
                return True
            tid = self.ingest.found(path, st, is_dir)
            if tid is None:
                return False
            found += 1
            if is_dir:
                self._note_ino(tid, st)
            return True

        if not self._walk(visit, progress, stop):
            return {"found": found, "stopped": True}
        self.store.set_meta("indexed", time.time())
        return {"found": found, "stopped": False}

    def reconcile(self, progress=None, stop=None) -> dict:
        """Settle what happened while nobody was watching: things whose path is gone moved
        (a new path has their inode) or were deleted, saves nobody saw are changes by
        someone unknown, and paths the brain does not know are found."""
        lower_priority()
        started = time.time()
        indexed = float(self.store.get_meta("indexed") or 0)
        counts = {"found": 0, "moved": 0, "gone": 0, "changed": 0, "stopped": False}
        gone = self._gone(stop)
        if gone is None:
            return {**counts, "stopped": True}
        missing_inos = {row["ino"] for row in gone if row["ino"]}
        candidates: dict[int, list[tuple[str, os.stat_result, bool]]] = {}
        deferred: list[tuple[str, os.stat_result, bool]] = []
        deferred_dirs: set[str] = set()

        def visit(path, st, is_dir, verdict):
            if verdict == PASS:
                return True
            thing = self.store.by_path(path)
            if thing is None and (st.st_ino in missing_inos or os.path.dirname(path) in deferred_dirs):
                # Maybe where a missing thing went: settled after the walk, with what is inside it.
                deferred.append((path, st, is_dir))
                if st.st_ino in missing_inos:
                    candidates.setdefault(st.st_ino, []).append((path, st, is_dir))
                if is_dir:
                    deferred_dirs.add(path)
                return True
            if thing is not None and not is_dir and thing["kind"] == "file" and self._unseen_save(thing, st, started):
                self.ingest.saw(path, "offline", st.st_mtime, UNKNOWN, size=st.st_size, ino=st.st_ino)
                counts["changed"] += 1
            tid = self._found(path, st, is_dir, thing is None, indexed)
            if tid is not None and thing is None:
                counts["found"] += 1
            return tid is not None

        if not self._walk(visit, progress, stop):
            return {**counts, "stopped": True}
        now = time.time()
        # Moves first, shortest paths first: a folder that moved takes what is inside it
        # along, and a file moved out of a folder is safe before that folder is deleted.
        gone.sort(key=lambda r: (r["path"].count("/"), r["path"]))
        used: set[str] = set()
        for moves in (True, False):
            for i in range(0, len(gone), BATCH):
                if stop is not None and stop():
                    return {**counts, "stopped": True}
                with self.store.tx():
                    for row in gone[i:i + BATCH]:
                        thing = self.store.get(row["id"])
                        if thing is None or thing["deleted"] is not None or not thing["path"] \
                                or _exists(thing["path"]):
                            continue   # moved or went with its folder, or came back
                        if not moves:
                            self.ingest.deleted(thing["path"], self._went_at(thing, now), UNKNOWN)
                            counts["gone"] += 1
                            continue
                        to = self._moved_to(thing, candidates.get(thing["ino"] or -1, []), used)
                        if to is not None:
                            path, st, is_dir = to
                            used.add(path)
                            self.ingest.renamed(thing["path"], path, min(st.st_ctime, now), UNKNOWN, is_dir)
                            counts["moved"] += 1
        for i in range(0, len(deferred), BATCH):
            with self.store.tx():
                for path, st, is_dir in deferred[i:i + BATCH]:
                    known = self.store.by_path(path) is not None
                    tid = self._found(path, st, is_dir, not known, indexed)
                    if tid is not None and not known:
                        counts["found"] += 1
                self._apply_caps()
        self._caps = []
        self.store.set_meta("reconciled", time.time())
        return counts

    # -- helpers for reconcile --

    def _gone(self, stop) -> list[dict] | None:
        """Live things under home whose path is no longer on disk, read a page at a time."""
        out, last = [], 0
        prefix = self.home + "/"
        while True:
            if stop is not None and stop():
                return None
            rows = self.store.q("SELECT id, path, kind, ino FROM things WHERE id > ? AND deleted IS NULL "
                                "AND path IS NOT NULL AND kind IN ('file', 'folder', 'project', 'app') "
                                "AND substr(path, 1, ?) = ? ORDER BY id LIMIT 5000",
                                (last, len(prefix), prefix))
            if not rows:
                return out
            last = rows[-1]["id"]
            out += [r for r in rows if not _exists(r["path"])]

    def _found(self, path, st, is_dir, new: bool, indexed: float) -> int | None:
        tid = self.ingest.found(path, st, is_dir)
        if tid is None:
            return None
        if is_dir:
            self._note_ino(tid, st)
        if new and indexed and st.st_mtime > indexed:
            # Made after the first index while nobody watched: not "there before the brain".
            thing = self.store.get(tid)
            if thing is not None and thing["made_by"] == "before":
                self.store.update(tid, made_by="unknown")
        return tid

    def _moved_to(self, thing: dict, cands: list, used: set) -> tuple | None:
        """The new path with the missing thing's inode, when it is the same thing. Inode
        numbers come back at once on ext4 (and repeat across btrfs subvolumes), so the inode
        alone is not enough: a file keeps its name (a move) or its size and age (a rename
        nobody edited since); a folder keeps its name or something the brain knew was in it.
        Anything less certain is a deletion and a new thing, which only splits a history
        where a wrong match would join two."""
        is_dir = thing["kind"] != "file"
        name = os.path.basename(thing["path"])
        cands = [c for c in cands if c[2] == is_dir and c[0] not in used]
        same_name = [c for c in cands if os.path.basename(c[0]) == name]
        if len(same_name) == 1:
            return same_name[0]
        if same_name:
            return None
        if is_dir:
            known = {os.path.basename(r["path"] or "") for r in self.store.children(thing["id"], live=False, limit=200)}
            fits = []
            for c in cands:
                try:
                    inside = set(os.listdir(c[0]))
                except OSError:
                    continue
                if (known & inside) or (not known and not inside):
                    fits.append(c)
        else:
            seen = max(thing.get("changed") or 0, thing.get("touched") or 0, thing.get("created") or 0)
            fits = [c for c in cands if c[1].st_size == thing.get("size") and c[1].st_mtime <= seen + CHANGED_SLACK_S]
        return fits[0] if len(fits) == 1 else None

    def _unseen_save(self, thing: dict, st: os.stat_result, started: float) -> bool:
        """A save nobody saw: the file is newer than anything the brain witnessed on it (for
        a turn, newer than the turn's end), and older than this walk, which the watcher, when
        there is one, reports itself."""
        if not (thing["changed"] or 0) + CHANGED_SLACK_S < st.st_mtime < started:
            return False
        last = self.store.one("SELECT MAX(COALESCE(e.t_end, e.t), COALESCE(t.ended, 0)) AS t FROM events e "
                              "LEFT JOIN turns t ON t.thing = e.actor_thing WHERE e.thing = ? "
                              "ORDER BY e.t DESC, e.id DESC LIMIT 1", (thing["id"],))
        return last is None or last["t"] is None or st.st_mtime > last["t"] + CHANGED_SLACK_S

    def _went_at(self, thing: dict, now: float) -> float:
        """When it went, as near as the disk says: its folder last changed then or later."""
        try:
            t = os.lstat(os.path.dirname(thing["path"])).st_mtime
        except OSError:
            return now
        floor = max(thing.get("changed") or 0, thing.get("touched") or 0, thing.get("created") or 0)
        return min(max(t, floor), now)

    def _note_ino(self, tid: int, st: os.stat_result) -> None:
        """Folders made from a watcher event have no inode yet; reconcile needs it to see a move."""
        thing = self.store.get(tid)
        if thing is not None and thing["ino"] != st.st_ino:
            self.store.update(tid, ino=st.st_ino)

    # -- the walk itself --

    def _verdict(self, path: str, is_dir: bool) -> str | None:
        v = rules.classify(path, self.home)
        if v.kind == "thing":
            return THING
        if is_dir and v.kind == "skip" and path.startswith(self.home + "/"):
            # ~/.config is not a thing, but ~/.config/hypr inside it is.
            rel = path[len(self.home) + 1:] + "/"
            if not any(p in rules.NOISE_DIRS for p in rel.split("/")) and \
                    any(a.startswith(rel) for a in rules.DOT_ALLOWED):
                return PASS
        return None

    def _judge(self, entry: os.DirEntry) -> tuple[str, os.stat_result, bool, str] | None:
        path = entry.path
        if not _storable(path) or path in self._own:
            return None
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            if not is_dir and not entry.is_file(follow_symlinks=False):
                return None   # symlinks are not followed or indexed; sockets and pipes are not things
            verdict = self._verdict(path, is_dir)
            if verdict is None:
                return None
            return path, entry.stat(follow_symlinks=False), is_dir, verdict
        except OSError:
            return None   # gone since it was listed, or not ours to look at

    def _list(self, d: str) -> tuple[list[os.DirEntry], int]:
        """A folder's entries and how many there are; past MAX_ENTRIES only counted."""
        entries, n = [], 0
        try:
            with os.scandir(d) as it:
                for e in it:
                    n += 1
                    if n <= MAX_ENTRIES:
                        entries.append(e)
        except OSError:
            return [], 0
        return entries, n

    def _count(self, root: str, stop=None) -> int | None:
        """How many folders the walk will go through, for progress: a quick pass that reads
        folder names only (no git, no stat of files)."""
        n = 0
        queue = deque([root])
        while queue:
            if stop is not None and n % 200 == 0 and stop():
                return None
            d = queue.popleft()
            n += 1
            entries, size = self._list(d)
            if size > MAX_ENTRIES:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False) and _storable(e.path) and self._verdict(e.path, True):
                        queue.append(e.path)
                except OSError:
                    continue
        return n

    def _walk(self, visit, progress=None, stop=None) -> bool:
        """Breadth first from home, so a folder is always found before what is inside it.
        visit(path, stat, is_dir, verdict) -> whether to go into a folder. False if stopped."""
        try:
            st = os.stat(self.home)
        except OSError:
            return True   # no home to walk
        total = self._count(self.home, stop)
        if total is None:
            return False
        done = 0
        self._caps = []
        with self.store.tx():
            go = visit(self.home, st, True, THING)
        queue = deque([(self.home, THING)] if go else [])
        while queue:
            pending: list[tuple[str, os.stat_result, bool, str]] = []
            while queue and len(pending) < BATCH:
                d, verdict = queue.popleft()
                done += 1
                entries, n = self._list(d)
                if verdict == THING:
                    self._caps.append((d, n if n > MAX_ENTRIES else None))
                if n > MAX_ENTRIES:
                    continue
                pending += [j for j in map(self._judge, entries) if j is not None]
            if stop is not None and stop():
                return False
            ignored = self.ingest.git.ignored([p for p, _, _, v in pending if v == THING], stop=self.home)
            for path, _, is_dir, _ in pending:
                if is_dir and path in ignored:
                    skipped = self._count(path, stop)   # its folders will not be walked
                    if skipped is None:
                        return False
                    done += skipped
            with self.store.tx():
                for path, st, is_dir, verdict in pending:
                    if path in ignored:
                        continue
                    if visit(path, st, is_dir, verdict) and is_dir:
                        queue.append((path, verdict))
                self._apply_caps()
            if progress is not None:
                progress(min(done, total), total)
        if progress is not None:
            progress(total, total)
        return True

    def _apply_caps(self) -> None:
        """Say how many files a too-big folder holds, or that it no longer is one. A folder
        not in the brain yet (reconcile settles it later) keeps its turn for the next call."""
        left = []
        for d, n in self._caps:
            thing = self.store.by_path(d)
            if thing is None:
                left.append((d, n))
                continue
            meta = dict(thing["meta"] or {})
            if n is not None and meta.get("files") != n:
                meta["files"] = n
                self.store.update(thing["id"], meta=meta)
            elif n is None and "files" in meta:
                del meta["files"]
                self.store.update(thing["id"], meta=meta)
        self._caps = left

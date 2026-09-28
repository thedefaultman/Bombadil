"""Witnesses: the logs other programs already keep, read as they grow.

The watcher sees every save, but some of what happened only other programs know: which
turn was which and what it touched (agentd's turns.jsonl), which pages you read and where
a download came from (Chromium's History), what was installed (pacman.log), and what the
agent learned (memory.md). Each witness reads only what was added since last time and
keeps its place in brain.db's meta table, so a restart of the brain picks up where it left
off and a line read twice still counts once.

They are guests in other programs' files. None of them raises (a log it cannot read costs
one line on stderr and a return of 0), none waits for long, none reads a private file, and
the only place any of them writes is brain-tmp: a private folder on the runtime tmpfs,
where Chromium's History is copied because Chromium keeps the original locked.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .. import paths
from . import rules
from .ingest import UNKNOWN, Ingest, Who, fingerprint_url

# Lines applied per transaction, with the witness's place saved in the same one: short
# enough that the walker and the live stream never wait long for the write lock.
CHUNK = 500
# A malformed row is skipped whole. Anything else (the disk is full, the database is
# locked) stops the read, and the same rows are tried again next time.
ROW_ERRORS = (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError,
              sqlite3.IntegrityError)

# A turn number past this is a corrupt row, not a turn.
MAX_TURN = 10**9

# Chromium keeps time as microseconds since 1601-01-01 UTC.
CHROME_EPOCH_S = 11644473600
HISTORY_GAP_S = 60.0
# The first import of a big History starts a month back: older visits cannot be "open while
# this changed" for anything the brain saw, and they would only slow the first minute down.
FIRST_DAYS = 30
FIRST_BIG = 2000
MAX_URL = 4096
# Page transitions (Chromium's ui/base/page_transition_types.h). Subframes are ads and
# embeds, and the first hops of a redirect chain are pages nobody saw.
CORE_MASK = 0xFF
SUBFRAMES = (3, 4)
CHAIN_START, CHAIN_END = 0x10000000, 0x20000000
REDIRECTS = 0x40000000 | 0x80000000
# Download states in History: 0 in progress, 1 complete, 2 cancelled, 4 interrupted (which
# may still resume, so it is looked at again for a week).
DOWNLOAD_COMPLETE = 1
DOWNLOAD_WAITING = (0, 4)
WAITING_KEEP_S = 7 * 86400
# Room to leave on the runtime tmpfs beyond the copy itself.
TMP_SPARE = 16 * 1024 * 1024

PACMAN_LINE = re.compile(r"^\[(?P<ts>[^\]]+)\] \[ALPM\] (?P<verb>installed|upgraded|downgraded|reinstalled|removed) "
                         r"(?P<name>[^\s()]+) \((?P<ver>[^()]*)\)$")
PACMAN_OPS = {"installed": "install", "upgraded": "upgrade", "downgraded": "upgrade", "reinstalled": "upgrade",
              "removed": "remove"}
# pacman keeps its log open for a whole transaction, so the write the watcher saw can carry
# lines from well before it; older than this, a line is attributed by the turns' times.
LIVE_WINDOW_S = 3600.0

MEMORY_MAX = 256 * 1024


def _log(what: str, e: BaseException) -> None:
    text = " ".join(f"{type(e).__name__}: {e}".split())
    print(f"bombadil-brain: {what}: {text}"[:300], file=sys.stderr)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json(line: bytes) -> dict | None:
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return row if isinstance(row, dict) else None


def _each(store, fn):
    """Apply one row atomically: a malformed row is skipped whole, never half-applied.
    Returns what fn returned, or None when the row was skipped."""
    store.x("SAVEPOINT witness_row")
    try:
        out = fn()
    except ROW_ERRORS:
        store.x("ROLLBACK TO witness_row")
        store.x("RELEASE witness_row")
        return None
    store.x("RELEASE witness_row")
    return out


def _turn_at(store, t: float, via: str) -> Who | None:
    """The turn that was running at time t, when nobody saw who did it."""
    row = store.one("SELECT thing FROM turns WHERE thing IS NOT NULL AND started IS NOT NULL AND started - 1 <= ? "
                    "AND COALESCE(ended, started + 3600) + 1 >= ? ORDER BY started DESC LIMIT 1", (t, t))
    return Who("turn", row["thing"], via) if row else None


class _Tail:
    """The complete lines a log gained since last time. The byte offset to go on from and a
    hash of the first line live in store meta, so a log that was replaced (it shrank, or it
    starts differently) is read again from the top. A last line still being written is left
    for next time."""

    def __init__(self, store, path: Path, name: str):
        self.store = store
        self.path = path
        self.name = name
        self.offset = 0
        self._next = 0
        self.head = ""
        self.restarted = False

    def chunks(self):
        """Lists of up to CHUNK lines (bytes). After applying one, call save() in the same
        transaction."""
        try:
            f = self.path.open("rb")
        except FileNotFoundError:
            return
        with f:
            size = os.fstat(f.fileno()).st_size
            first = f.readline()
            self.head = hashlib.sha1(first).hexdigest() if first.endswith(b"\n") else ""
            self.offset = _int(self.store.get_meta(f"{self.name}.offset"))
            self.restarted = self.offset > size or (
                self.offset > 0 and self.head != self.store.get_meta(f"{self.name}.head"))
            if self.restarted:
                self.offset = 0
            f.seek(self.offset)
            while True:
                chunk = []
                for _ in range(CHUNK):
                    line = f.readline()
                    if not line.endswith(b"\n"):
                        break
                    chunk.append(line)
                if not chunk:
                    return
                self._next = self.offset + sum(len(x) for x in chunk)
                yield chunk
                self.offset = self._next

    def save(self) -> None:
        self.store.set_meta(f"{self.name}.offset", self._next)
        self.store.set_meta(f"{self.name}.head", self.head)


class TurnsLog:
    """agentd's turns.jsonl: one row per finished turn, with the files it wrote and read."""

    def __init__(self, ingest: Ingest, path: Path | str | None = None):
        self.ingest = ingest
        self.path = Path(path) if path is not None else paths.turns_log()

    def read_new(self) -> int:
        """Apply the rows added since last time. Returns how many turns were applied."""
        store = self.ingest.store
        applied = 0
        try:
            tail = _Tail(store, self.path, "turns")
            count = None
            for chunk in tail.chunks():
                if count is None:
                    count = 0 if tail.restarted else _int(store.get_meta("turns.legacy"))
                with store.tx():
                    for line in chunk:
                        row = _json(line)
                        if row is None or row.get("kind") == "local":
                            continue   # not a row (a torn write), or a launcher action rather than a turn
                        if row.get("n") is None:
                            # Written before rows had numbers: the turns are counted in order.
                            count += 1
                            n = count
                        else:
                            n = _turn_number(row["n"])
                            count = max(count + 1, n or 0)
                            if n is None:
                                continue
                        if _each(store, lambda: self.ingest.turn_row(row, n)) is not None:
                            applied += 1
                    tail.save()
                    store.set_meta("turns.legacy", count)
        except Exception as e:  # noqa: BLE001 - a witness never takes the brain down
            _log(f"reading {self.path}", e)
            return 0
        return applied


def _turn_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and 0 < value < MAX_TURN else None


class History:
    """Chromium's History: the pages you read, how long you stayed, and where each download
    came from. Chromium holds the database locked, so it is copied, at most once a minute
    per profile, into brain-tmp and read there."""

    def __init__(self, ingest: Ingest, profiles: list | None = None, workdir: Path | str | None = None):
        self.ingest = ingest
        # Each one a Chromium user-data folder, a profile folder, or a History file itself.
        self.profiles = [Path(p) for p in profiles] if profiles is not None else None
        self.workdir = Path(workdir) if workdir is not None else paths.runtime_dir() / "brain-tmp"
        self._last: dict[str, float] = {}      # History path -> when it was last imported
        self._seen: dict[str, tuple] = {}      # History path -> its files as they were then

    def _roots(self) -> list[Path]:
        if self.profiles is not None:
            return self.profiles
        env = os.environ.get("BOMBADIL_CHROMIUM_DIRS")
        if env:
            return [Path(p).expanduser() for p in env.split(":") if p]
        config = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.join(self.ingest.home, ".config"))
        return [config / "chromium", paths.config_dir() / "chromium"]

    def histories(self) -> list[str]:
        """Every profile's History file, found afresh each time (a profile can appear later)."""
        out: list[str] = []
        for root in self._roots():
            try:
                if root.name == "History" and root.is_file():
                    found = [root]
                elif (root / "History").is_file():
                    found = [root / "History"]
                else:
                    found = sorted(root.glob("*/History"))
                for p in found:
                    if str(p) not in out and p.is_file():
                        out.append(str(p))
            except OSError:
                continue
        return out

    def files(self) -> list[str]:
        """The files whose writes mean History changed, for ingest.specials."""
        return [h + suffix for h in self.histories() for suffix in ("", "-journal", "-wal")]

    def due(self, now: float | None = None) -> float:
        """When import_new will next read a profile: a time.time() value, at or before now
        when it would read one right away."""
        now = time.time() if now is None else now
        times = []
        for hist in self.histories():
            last = self._last.get(hist)
            times.append(last + HISTORY_GAP_S if last is not None and 0 <= now - last < HISTORY_GAP_S else now)
        return min(times, default=now)

    def import_new(self, now: float | None = None) -> int:
        """Import the visits and downloads added since last time. Returns how many; 0 when
        it is too soon since the last import (see due) or nothing changed."""
        now = time.time() if now is None else now
        total = 0
        for hist in self.histories():
            last = self._last.get(hist)
            if last is not None and 0 <= now - last < HISTORY_GAP_S:
                continue
            seen = _signature(hist)
            if seen == self._seen.get(hist):
                continue
            self._last[hist] = now
            try:
                n = self._import(hist, now)
            except Exception as e:  # noqa: BLE001 - a witness never takes the brain down
                _log(f"reading {hist}", e)
                continue
            if n is not None:
                self._seen[hist] = seen
                total += n
        return total

    def _import(self, hist: str, now: float) -> int | None:
        self.workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = tempfile.mkdtemp(prefix="history-", dir=self.workdir)
        try:
            copy = self._copy(hist, tmp)
            if copy is None:
                print(f"bombadil-brain: {hist} kept changing while it was copied; trying again later",
                      file=sys.stderr)
                return None
            # Opened read-write on purpose: a copy taken mid-transaction carries a hot journal,
            # and a read-only connection refuses to roll it back (SQLITE_READONLY_ROLLBACK).
            # The copy is ours alone, and query_only keeps everything else from writing.
            db = sqlite3.connect(copy, isolation_level=None)
            db.text_factory = lambda b: b.decode("utf-8", errors="replace")   # one bad title is not a stuck import
            try:
                db.execute("PRAGMA query_only=1")
                profile = os.path.dirname(hist)
                return self._visits(db, profile, now) + self._downloads(db, profile, now)
            finally:
                db.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _copy(self, hist: str, into: str) -> str | None:
        """History and its journal or WAL, copied as one consistent moment. The database goes
        first: every page Chromium changed in it is already in the journal copied after it.
        If Chromium committed meanwhile, the copy may be torn, so it is taken again."""
        dst = os.path.join(into, "History")
        for _ in range(3):
            before = _signature(hist)
            need = sum(s[0] for s in before if s is not None)
            if shutil.disk_usage(into).free < need + TMP_SPARE:
                raise OSError(f"no room for a {need // 1048576} MB copy in {self.workdir}")
            shutil.copyfile(hist, dst)
            for suffix in ("-journal", "-wal"):
                try:
                    shutil.copyfile(hist + suffix, dst + suffix)
                except FileNotFoundError:
                    try:
                        os.unlink(dst + suffix)
                    except FileNotFoundError:
                        pass
            if _signature(hist) == before:
                return dst
        return None

    def _visits(self, db: sqlite3.Connection, profile: str, now: float) -> int:
        vcols, ucols = _columns(db, "visits"), _columns(db, "urls")
        if not {"id", "url", "visit_time"} <= vcols or not {"id", "url"} <= ucols:
            return 0
        store = self.ingest.store
        key = f"history.{profile}"
        last_id = store.get_meta(f"{key}.visit")
        floor = 0
        if last_id is None and db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] > FIRST_BIG:
            floor = _chrome(now - FIRST_DAYS * 86400)
        last_id = _int(last_id)
        # Both the last id and the last time: ids are reused after "clear browsing data".
        last_t = _int(store.get_meta(f"{key}.visit_t"))
        cols = ", ".join((
            "v.id", "v.visit_time", "v.visit_duration" if "visit_duration" in vcols else "0",
            "v.transition" if "transition" in vcols else "0", "u.hidden" if "hidden" in ucols else "0",
            "u.url", "u.title" if "title" in ucols else "''"))
        cur = db.execute(f"SELECT {cols} FROM visits v JOIN urls u ON u.id = v.url "
                         "WHERE (v.id > ? OR v.visit_time > ?) AND v.visit_time >= ? ORDER BY v.id",
                         (last_id, last_t, floor))
        n = 0
        while True:
            rows = cur.fetchmany(CHUNK)
            if not rows:
                return n
            with store.tx():
                for vid, vt, duration, transition, hidden, url, title in rows:
                    last_id, last_t = max(last_id, _int(vid)), max(last_t, _int(vt))
                    t = _unix(vt)
                    if not t or not _read_by_you(url, transition, hidden):
                        continue
                    title = title if isinstance(title, str) else ""
                    if _each(store, lambda: self.ingest.visit(url, title, t, max(_int(duration), 0) / 1e6)) is not None:
                        n += 1
                store.set_meta(f"{key}.visit", last_id)
                store.set_meta(f"{key}.visit_t", last_t)

    def _downloads(self, db: sqlite3.Connection, profile: str, now: float) -> int:
        cols = _columns(db, "downloads")
        if not {"id", "target_path", "state"} <= cols:
            return 0
        store = self.ingest.store
        key = f"history.{profile}"
        last = _int(store.get_meta(f"{key}.download"))
        try:
            waiting = [int(i) for i in json.loads(store.get_meta(f"{key}.download_waiting") or "[]")]
        except (ValueError, TypeError):
            waiting = []

        def col(name: str) -> str:
            return f"d.{name}" if name in cols else "NULL"

        chain = ("(SELECT c.url FROM downloads_url_chains c WHERE c.id = d.id ORDER BY c.chain_index DESC LIMIT 1)"
                 if {"id", "chain_index", "url"} <= _columns(db, "downloads_url_chains") else "NULL")
        where = "d.id > ?" + (f" OR d.id IN ({', '.join('?' for _ in waiting)})" if waiting else "")
        rows = db.execute(f"SELECT d.id, d.target_path, d.state, {col('end_time')}, {col('start_time')}, "
                          f"{col('tab_url')}, {col('referrer')}, {chain}, {col('url')} FROM downloads d "
                          f"WHERE {where} ORDER BY d.id", (last, *waiting)).fetchall()
        n = 0
        still = []
        with store.tx():
            for did, target, state, end, start, tab, referrer, chain_url, url in rows:
                last = max(last, _int(did))
                if state in DOWNLOAD_WAITING:
                    if _unix(start or end) > now - WAITING_KEEP_S:
                        still.append(_int(did))
                    continue
                if state != DOWNLOAD_COMPLETE or not isinstance(target, str) or not target.startswith("/"):
                    continue
                t = _unix(end) or _unix(start) or now
                src, page = str(chain_url or url or ""), str(tab or referrer or "")
                if _each(store, lambda: self.ingest.download(target, src, page, t)) is not None:
                    n += 1
            store.set_meta(f"{key}.download", last)
            store.set_meta(f"{key}.download_waiting", json.dumps(still[-100:]))
        return n


def _signature(hist: str) -> tuple:
    """History and its journal and WAL as they are now: (size, mtime, inode) or None each."""
    out = []
    for suffix in ("", "-journal", "-wal"):
        try:
            st = os.stat(hist + suffix)
            out.append((st.st_size, st.st_mtime_ns, st.st_ino))
        except OSError:
            out.append(None)
    return tuple(out)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _unix(chrome) -> float:
    c = _int(chrome)
    return c / 1e6 - CHROME_EPOCH_S if c > 0 else 0.0


def _chrome(unix: float) -> int:
    return int((unix + CHROME_EPOCH_S) * 1e6)


def _read_by_you(url, transition, hidden) -> bool:
    """A page you actually had in front of you, not an embed or a redirect's first hop."""
    if hidden or not isinstance(url, str) or not url or len(url) > MAX_URL:
        return False
    tr = _int(transition) & 0xFFFFFFFF
    if tr & CORE_MASK in SUBFRAMES:
        return False
    if tr & (CHAIN_START | REDIRECTS) and not tr & CHAIN_END:
        return False
    return fingerprint_url(url) is not None


class PacmanLog:
    """/var/log/pacman.log: what was installed, upgraded and removed, and by whom."""

    def __init__(self, ingest: Ingest, path: Path | str = Path("/var/log/pacman.log")):
        self.ingest = ingest
        self.path = Path(path)

    def read_new(self, who: Who | None = None) -> int:
        """Apply the package lines added since last time. `who` is the writer the watcher saw;
        without one (catching up at start), a line belongs to the turn that was running at
        its time, else to the system. Returns how many package changes were applied."""
        store = self.ingest.store
        live = who if who is not None and who.kind not in ("unknown", "before") else None
        applied = 0
        try:
            tail = _Tail(store, self.path, "pacman")
            for chunk in tail.chunks():
                now = time.time()
                with store.tx():
                    for raw in chunk:
                        parsed = parse_pacman(raw.decode("utf-8", errors="replace"))
                        if parsed is None:
                            continue
                        name, version, op, t = parsed
                        by = live if live is not None and t >= now - LIVE_WINDOW_S else None
                        by = by or _turn_at(store, t, "pacman") or Who("system", None, "pacman")
                        if _each(store, lambda: self.ingest.package(name, version, op, t, by)) is not None:
                            applied += 1
                    tail.save()
        except Exception as e:  # noqa: BLE001 - a witness never takes the brain down
            _log(f"reading {self.path}", e)
            return 0
        return applied


def parse_pacman(line: str) -> tuple[str, str, str, float] | None:
    """(name, version, op, t) for an [ALPM] package line; None for everything else (pacman's
    own [PACMAN] lines, scriptlet output, transaction markers)."""
    m = PACMAN_LINE.match(line.rstrip("\r\n"))
    if not m:
        return None
    t = pacman_time(m["ts"])
    if t is None:
        return None
    version = m["ver"].split(" -> ")[-1].strip()
    return m["name"], version, PACMAN_OPS[m["verb"]], t


def pacman_time(ts: str) -> float | None:
    """'2026-09-27T10:00:00+0200' (pacman 5.2 and later), or '2019-01-01 10:00' in local time
    as older logs have it."""
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        pass
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return None


class Memory:
    """memory.md, which both CLIs read as their instructions file: each line a fact."""

    def __init__(self, ingest: Ingest, files: list | None = None):
        self.ingest = ingest
        self._files = [Path(f) for f in files] if files is not None else None

    def candidates(self) -> list[Path]:
        """Where memory.md may be, first match wins: the others are links to the same file."""
        if self._files is not None:
            return self._files
        home = Path(self.ingest.home)
        return [home / ".bombadil" / "memory.md", home / ".claude" / "CLAUDE.md", home / ".codex" / "AGENTS.md"]

    def files(self) -> list[str]:
        """Every name a write to memory.md can arrive under, for ingest.specials: the watcher
        reports the real file, not the link."""
        out: list[str] = []
        for p in self.candidates():
            for name in (str(p), os.path.realpath(p)):
                if name not in out:
                    out.append(name)
        return out

    def read(self, who: Who | None = None) -> int:
        """Read memory.md again: new lines are facts `who` taught the machine, lines that went
        are facts forgotten. Returns how many facts it holds now. A missing file changes
        nothing (it is likely mid-save); an empty one forgets everything."""
        try:
            path = next((p for p in self.candidates() if p.is_file()), None)
            if path is None:
                return 0
            real = os.path.realpath(path)
            if rules.private(str(path), self.ingest.home) or rules.private(real, self.ingest.home):
                return 0
            with open(real, "rb") as f:
                st = os.fstat(f.fileno())
                data = f.read(MEMORY_MAX + 1)
            if len(data) > MEMORY_MAX:
                data = data[:data.rfind(b"\n", 0, MEMORY_MAX) + 1]   # never half a line
            lines = data.decode("utf-8", errors="replace").splitlines()
            if who is None or who.kind in ("unknown", "before"):
                who = _turn_at(self.ingest.store, st.st_mtime, "memory") or UNKNOWN
            self.ingest.facts(lines, st.st_mtime, who, str(path))
            return self.ingest.store.one("SELECT COUNT(*) AS n FROM things WHERE kind = 'fact' AND deleted IS NULL")["n"]
        except Exception as e:  # noqa: BLE001 - a witness never takes the brain down
            _log("reading memory.md", e)
            return 0

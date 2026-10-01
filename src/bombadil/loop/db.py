"""loop.db: one SQLite file (WAL) under the loop directory, shared by agentd, the probes and the
Noticed window. Each module owns its own tables and registers them with `schema()`; a table is
made once, and changing one means adding a new versioned step, never editing an old one.

The file is derived: counts come from turns.jsonl and the per-turn logs, findings from probes.
Only a few things in it cannot be made again (what he said no to, what was offered, what was
sent, and the mark that keeps what he forgot from being read in again). A damaged file is not
repaired or replaced on its own, because that would lose those; it is for him to delete, with its
-wal and -shm, and the next try counts turns.jsonl again from the top.

The file holds his own words, so the directory it is in is made 0700 and the files 0600.
"""

import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

from .. import paths

BUSY_MS = 5000


def connect(path: Path | None = None) -> sqlite3.Connection:
    """A connection with the loop's settings. Several processes write (agentd, the prober), so
    WAL and a busy timeout; rows come back as sqlite3.Row."""
    path = path or paths.loop_db()
    private = str(path) != ":memory:"
    if private:
        if path.parent != Path("."):   # (a bare file name is in the working directory: not ours to close)
            private_dir(path.parent)
        if not path.exists():
            os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))   # SQLite gives -wal and -shm its mode
    conn = sqlite3.connect(str(path), timeout=BUSY_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass   # a read-only mount or a network file system: the default journal still works
    conn.execute("PRAGMA synchronous=NORMAL")
    if private:
        for name in ("", "-wal", "-shm"):
            _owner_only(path.with_name(path.name + name))
    return conn


def private_dir(path: Path) -> None:
    """Make `path` a directory nobody but its owner can enter, so what is in it is closed to every
    other user whatever the files' own modes are: 0700 when it is made, and an older one that is
    ours loses its group and world bits. Only its own mode is touched, never its parents' or its
    contents', and a shared directory (sticky bit) or one that is not ours is left as it is."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        st = path.stat()
        if st.st_uid == os.getuid() and not st.st_mode & stat.S_ISVTX and st.st_mode & 0o077:
            path.chmod(stat.S_IMODE(st.st_mode) & ~0o077)
    except OSError:
        pass   # a file system that keeps no modes: the files' own modes still hold


def _owner_only(path: Path) -> None:
    try:
        st = path.stat()
        if st.st_uid == os.getuid() and st.st_mode & 0o077:
            path.chmod(0o600)
    except OSError:
        pass


def schema(conn: sqlite3.Connection, name: str, steps: list[str]) -> None:
    """Apply a module's schema steps in order, each once. `steps[i]` is the SQL for version i+1
    of `name`; a step already applied is skipped, so this is safe to call on every start."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_versions(name TEXT PRIMARY KEY, version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_versions WHERE name=?", (name,)).fetchone()
    have = row["version"] if row else 0
    for version, sql in enumerate(steps, start=1):
        if version <= have:
            continue
        with transaction(conn):
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_versions(name, version) VALUES(?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET version=excluded.version", (name, version))


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";\n") if s.strip()]


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT (ROLLBACK on an exception). The connection is in autocommit
    mode, so nothing else opens a transaction for you."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

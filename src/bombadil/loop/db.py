"""loop.db: one SQLite file (WAL) under the loop directory, shared by agentd, the probes and the
Noticed window. Each module owns its own tables and registers them with `schema()`; a table is
made once, and changing one means adding a new versioned step, never editing an old one.

The file is derived: counts come from turns.jsonl and the per-turn logs, findings from probes.
Only a few things in it cannot be made again (what he said no to, what was offered, what was
sent), so everything else may be dropped and rebuilt by `bombadil loop rebuild`.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .. import paths

BUSY_MS = 5000


def connect(path: Path | None = None) -> sqlite3.Connection:
    """A connection with the loop's settings. Several processes write (agentd, the prober), so
    WAL and a busy timeout; rows come back as sqlite3.Row."""
    path = path or paths.loop_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass   # a read-only mount or a network file system: the default journal still works
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


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

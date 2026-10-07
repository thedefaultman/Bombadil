"""connect.db: what bombadil-connect writes down, and nothing of anyone else's.

It holds the connections the person made (id, kind, service, name, state, note), the secrets each of them
needs (Slack's two tokens, an MCP service's sign-in), one time per conversation (when the person last looked),
and the ids of the presses already performed. A message's words, a task's title and a notification's text are
never written here: they live in the service's memory and go with it.

The secrets are stored as they are. The protection is the directory, which belongs to the service's own system
user (created 0700, the database 0600; systemd's StateDirectory makes it the same on the installed system), and
the fact that no other part of the system is ever given a value back: `Store.secrets(connection)` hands a
driver a `Secrets` for that connection alone, and its `get` is the one way a value leaves this module. A value is
never in a log line or in the text of an exception raised here.

One SQLite file in WAL mode, used from the service's event loop (the calls are small), with a lock for the few
callers that are not on it (tests, the start-up). A database that cannot be opened, is damaged or is newer than
this code is an error the service reports and stops on: it holds the only copy of the tokens and of which presses
went, so it is never quietly replaced.
"""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .driver import Secrets

SCHEMA_VERSION = 1
BUSY_MS = 5000
SECRET_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE connections(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  service TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
) WITHOUT ROWID;
CREATE TABLE secrets(
  connection TEXT NOT NULL,
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(connection, name)
) WITHOUT ROWID;
CREATE TABLE seen(
  key TEXT PRIMARY KEY,
  connection TEXT NOT NULL,
  ts REAL NOT NULL
) WITHOUT ROWID;
CREATE INDEX seen_connection ON seen(connection);
CREATE TABLE performed(
  proposal TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  outcome TEXT NOT NULL,
  at REAL NOT NULL
) WITHOUT ROWID;
"""

OUTCOMES = ("done", "unknown")


def private_directory(folder: Path) -> None:
    """Make `folder` 0700 when it is made here. One that exists already is not touched: it may be a place that is not
    ours to change (systemd made the installed one 0700 already), and the database in it is 0600 whatever it is."""
    if folder.is_dir():
        return
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)   # mkdir's mode is cut by the umask


class NewerSchema(sqlite3.DatabaseError):
    """connect.db was made by a newer Bombadil than this one."""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._depth = 0
        private_directory(self.path.parent)
        try:   # the file is made the service's alone before SQLite makes it, and gives its -wal and -shm its mode
            os.close(os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600))
        except OSError:
            pass   # what cannot be made is reported by the connect that follows
        self.db = sqlite3.connect(self.path, timeout=BUSY_MS / 1000, isolation_level=None, check_same_thread=False)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
            self.db.execute("PRAGMA journal_mode=WAL")
            # A press is written down before the service asks the driver, and that has to survive a power cut.
            self.db.execute("PRAGMA synchronous=FULL")
            self._migrate()
        except BaseException:
            self.db.close()
            raise

    def _migrate(self) -> None:
        (version,) = self.db.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise NewerSchema(f"{self.path.name} is version {version}; this Bombadil knows up to {SCHEMA_VERSION}")
        if version == SCHEMA_VERSION:
            return
        with self.tx():
            for statement in SCHEMA.split(";\n"):
                if statement.strip():
                    self.db.execute(statement)
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @contextmanager
    def tx(self):
        """One write transaction; nested calls join the outer one."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self
                finally:
                    self._depth -= 1
                return
            self.db.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self
            except BaseException:
                self._depth = 0
                self.db.execute("ROLLBACK")
                raise
            self._depth = 0
            try:
                self.db.execute("COMMIT")
            except BaseException:
                try:
                    self.db.execute("ROLLBACK")   # a full disk fails the commit and leaves the transaction open
                except sqlite3.Error:
                    pass
                raise

    def _all(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args=()) -> dict | None:
        with self._lock:
            row = self.db.execute(sql, args).fetchone()
            return dict(row) if row is not None else None

    def _run(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, args)

    # -- connections --

    def connections(self) -> list[dict]:
        """Every connection's record, in the order they were made."""
        return self._all("SELECT id, kind, service, name, state, note, created FROM connections "
                         "ORDER BY created, id")

    def connection(self, cid: str) -> dict | None:
        return self._one("SELECT id, kind, service, name, state, note, created FROM connections WHERE id = ?",
                         (cid,))

    def add_connection(self, cid: str, kind: str, service: str, name: str, state: str, note: str,
                       created: float) -> dict:
        self._run("INSERT INTO connections(id, kind, service, name, state, note, created) VALUES(?, ?, ?, ?, ?, ?, ?)",
                  (cid, kind, service, name, state, note, created))
        return self.connection(cid)

    def update_connection(self, cid: str, *, state: str | None = None, note: str | None = None,
                          name: str | None = None) -> None:
        sets = {"state": state, "note": note, "name": name}
        sets = {k: v for k, v in sets.items() if v is not None}
        if sets:
            self._run(f"UPDATE connections SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                      (*sets.values(), cid))

    def remove_connection(self, cid: str) -> None:
        """The connection, its secrets and the times it had kept, together or not at all."""
        with self.tx():
            for table, column in (("secrets", "connection"), ("seen", "connection"), ("connections", "id")):
                self._run(f"DELETE FROM {table} WHERE {column} = ?", (cid,))

    def next_number(self, counter: str) -> int:
        """The next number of a counter that never goes back: `slack:w3` is not used twice, even after it is gone."""
        with self.tx():
            row = self._one("SELECT value FROM meta WHERE key = ?", (f"counter:{counter}",))
            n = int(row["value"]) + 1 if row else 1
            self._run("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (f"counter:{counter}", str(n)))
            return n

    def get_meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._run("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, str(value)))

    # -- secrets --

    def secrets(self, connection: str) -> "ConnectionSecrets":
        return ConnectionSecrets(self, connection)

    # -- seen: when the person last looked, per conversation --

    def seen_times(self) -> dict[str, float]:
        return {r["key"]: r["ts"] for r in self._all("SELECT key, ts FROM seen")}

    def mark_seen(self, connection: str, key: str, ts: float) -> None:
        """The person has seen `key` up to `ts`. A time never goes back."""
        self._run("INSERT INTO seen(key, connection, ts) VALUES(?, ?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET ts = MAX(ts, excluded.ts)", (key, connection, ts))

    # -- performed: presses that went, so a second press of one does nothing --

    def performed(self, proposal: str) -> dict | None:
        return self._one("SELECT proposal, kind, outcome, at FROM performed WHERE proposal = ?", (proposal,))

    def record_performed(self, proposal: str, kind: str, outcome: str, at: float) -> None:
        if outcome not in OUTCOMES:
            raise ValueError("a press ends as done or unknown")
        self._run("INSERT INTO performed(proposal, kind, outcome, at) VALUES(?, ?, ?, ?) "
                  "ON CONFLICT(proposal) DO UPDATE SET outcome = excluded.outcome, at = excluded.at",
                  (proposal, kind, outcome, at))

    def forget_performed(self, proposal: str) -> None:
        self._run("DELETE FROM performed WHERE proposal = ?", (proposal,))

    def prune_performed(self, before: float) -> int:
        return self._run("DELETE FROM performed WHERE at < ?", (before,)).rowcount


class ConnectionSecrets(Secrets):
    """One connection's secrets, by name. This is what a driver is given, and `get` is the one place in the service
    a value comes out of the database. Nothing here says a value in a log line or an exception."""

    def __init__(self, store: Store, connection: str):
        self._store, self._connection = store, connection

    @staticmethod
    def _checked(name) -> str:
        if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
            raise ValueError("a secret's name is a short lowercase word")
        return name

    def get(self, name: str) -> str | None:
        row = self._store._one("SELECT value FROM secrets WHERE connection = ? AND name = ?",
                               (self._connection, self._checked(name)))
        return row["value"] if row else None

    def set(self, name: str, value: str) -> None:
        """Written for a connection that exists: a driver that is being stopped cannot bring back the secrets
        of one that was just removed."""
        if not isinstance(value, str) or not value:
            raise ValueError("a secret is a non-empty string")
        self._store._run("INSERT INTO secrets(connection, name, value) "
                         "SELECT ?1, ?2, ?3 WHERE EXISTS (SELECT 1 FROM connections WHERE id = ?1) "
                         "ON CONFLICT(connection, name) DO UPDATE SET value = excluded.value",
                         (self._connection, self._checked(name), value))

    def delete(self, name: str | None = None) -> None:
        """One secret, or every one of this connection's."""
        if name is None:
            self._store._run("DELETE FROM secrets WHERE connection = ?", (self._connection,))
        else:
            self._store._run("DELETE FROM secrets WHERE connection = ? AND name = ?",
                             (self._connection, self._checked(name)))

    def names(self) -> list[str]:
        return [r["name"] for r in self._store._all("SELECT name FROM secrets WHERE connection = ? ORDER BY name",
                                                    (self._connection,))]

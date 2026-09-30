"""mail.db: Bombadil's own notes about mail, and nothing of anyone else's.

It holds the accounts the person added, their "needs a reply" marks (who, what about, when, and one
line of why: never the mail's text), Bombadil's drafts with what the view last showed of each, the
receipts of what was sent, the addresses the person has sent to, and the account ids and draft
numbers already used. Thunderbird holds the mail.

One SQLite file in WAL mode. The service reaches it from one worker thread; the lock is for the
few callers that are not that thread (tests, the start-up). It is a cache of what Bombadil knows:
one that is damaged, or made by a newer Bombadil, is set aside as mail.db.broken and made again,
and the service finds the accounts again in Thunderbird's own list.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
BUSY_MS = 5000
RETENTION_S = 30 * 86400   # how long a draft that is sent or discarded is kept, for its receipt

SCHEMA = """
CREATE TABLE schema(version INTEGER NOT NULL);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE accounts(
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  sender TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL,
  state TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  engine_id TEXT,
  web_name TEXT,
  web_url TEXT,
  created REAL NOT NULL
);
CREATE TABLE forgotten(email TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE marks(
  account TEXT NOT NULL,
  key TEXT NOT NULL,
  why TEXT NOT NULL,
  sender_name TEXT NOT NULL,
  sender_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  ts REAL NOT NULL,
  marked REAL NOT NULL,
  PRIMARY KEY(account, key)
) WITHOUT ROWID;
CREATE INDEX marks_ts ON marks(ts);
CREATE TABLE drafts(
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  kind TEXT NOT NULL,
  reply_to TEXT,
  to_addrs TEXT NOT NULL,
  cc_addrs TEXT NOT NULL,
  bcc_addrs TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  attachments TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL,
  tainted INTEGER NOT NULL,
  typed TEXT NOT NULL,
  origin TEXT NOT NULL,
  warnings TEXT NOT NULL,
  adds TEXT NOT NULL,
  state TEXT NOT NULL,
  shown_fp TEXT,
  shown_at REAL,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX drafts_state ON drafts(state, updated);
CREATE TABLE receipts(draft TEXT PRIMARY KEY, body TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE sent_to(email TEXT PRIMARY KEY, last REAL NOT NULL, n INTEGER NOT NULL);
"""

# Steps from an older schema to the next one: MIGRATIONS[2] takes version 1 to 2, in the open transaction.
MIGRATIONS: dict = {}

ACCOUNT_FIELDS = ("email", "name", "sender", "provider", "state", "note", "engine_id", "web_name", "web_url")
MARK_FIELDS = ("why", "sender_name", "sender_email", "subject", "ts")
JSON_FIELDS = {"to_addrs": "to", "cc_addrs": "cc", "bcc_addrs": "bcc", "attachments": "attachments",
               "typed": "typed", "origin": "origin", "warnings": "warnings"}
OPEN = ("open", "unknown")


def corrupt(e: BaseException) -> bool:
    """Is the database itself damaged (not busy, locked or out of room, which are not that)?"""
    if not isinstance(e, sqlite3.DatabaseError) or isinstance(e, sqlite3.OperationalError):
        return False
    name = getattr(e, "sqlite_errorname", None)
    if name is not None:
        return name == "SQLITE_NOTADB" or name.startswith("SQLITE_CORRUPT")
    return "malformed" in str(e) or "not a database" in str(e)


class NewerSchema(sqlite3.DatabaseError):
    """mail.db was made by a newer Bombadil than this one."""


def _draft(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for column, name in JSON_FIELDS.items():
        d[name] = json.loads(d.pop(column))
    d["tainted"] = bool(d["tainted"])
    return d


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.recovered: str | None = None    # why the last database was set aside, for the service's log
        self._lock = threading.RLock()
        self._depth = 0
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._open()
        except sqlite3.DatabaseError as e:
            if not (corrupt(e) or isinstance(e, NewerSchema)):
                raise
            self.start_over(e)

    def _open(self) -> None:
        self.db = sqlite3.connect(self.path, timeout=BUSY_MS / 1000, isolation_level=None, check_same_thread=False)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
            self.db.execute("PRAGMA journal_mode=WAL")
            # A press is written down before the engine is asked, and that has to survive a power cut.
            self.db.execute("PRAGMA synchronous=FULL")
            if self.db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("database disk image is malformed")
            self._migrate()
        except BaseException:
            self.db.close()
            raise

    def _migrate(self) -> None:
        has = self.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema'").fetchone()
        if not has:
            with self.tx():
                for statement in SCHEMA.split(";\n"):
                    if statement.strip():
                        self.db.execute(statement)
                self.db.execute("INSERT INTO schema(version) VALUES(?)", (SCHEMA_VERSION,))
            return
        (version,) = self.db.execute("SELECT version FROM schema").fetchone()
        if version > SCHEMA_VERSION:
            raise NewerSchema(f"mail.db is version {version}; this Bombadil knows up to {SCHEMA_VERSION}")
        if version < SCHEMA_VERSION:
            with self.tx():
                for n in range(version + 1, SCHEMA_VERSION + 1):
                    MIGRATIONS[n](self.db)
                self.db.execute("UPDATE schema SET version = ?", (SCHEMA_VERSION,))

    def close(self) -> None:
        self.db.close()

    def start_over(self, why: BaseException) -> None:
        """Set the database aside as mail.db.broken and make a new one."""
        self.recovered = f"{type(why).__name__}: {why}"
        with self._lock:
            try:
                self.db.close()
            except (AttributeError, sqlite3.Error):
                pass
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.replace(f"{self.path}{suffix}", f"{self.path}.broken{suffix}")
                except OSError:
                    pass
            self._open()

    # -- plumbing --

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

    def q(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        with self._lock:
            row = self.db.execute(sql, args).fetchone()
            return dict(row) if row is not None else None

    def x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, args)

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value) -> None:
        self.x("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))

    def _next(self, counter: str) -> int:
        """The next number of a counter that never goes back: an id is not used twice, even after its row is gone."""
        with self.tx():
            n = int(self.get_meta(counter, "0")) + 1
            self.set_meta(counter, n)
            return n

    # -- accounts --

    def add_account(self, email: str, provider: str, state: str, name: str = "", note: str = "", now: float = 0.0,
                    **fields) -> dict:
        with self.tx():
            aid = f"a{self._next('accounts')}"
            self.x("DELETE FROM forgotten WHERE email = ?", (email,))
            self.x("INSERT INTO accounts(id, email, name, provider, state, note, created) VALUES(?,?,?,?,?,?,?)",
                   (aid, email, name, provider, state, note, now))
            if fields:
                self.update_account(aid, **fields)
        return self.account(aid)

    def account(self, aid: str) -> dict | None:
        return self.one("SELECT * FROM accounts WHERE id = ?", (aid,))

    def account_by_email(self, email: str) -> dict | None:
        return self.one("SELECT * FROM accounts WHERE email = ?", (email,))

    def accounts(self) -> list[dict]:
        return self.q("SELECT * FROM accounts ORDER BY CAST(substr(id, 2) AS INTEGER)")

    def update_account(self, aid: str, **fields) -> None:
        bad = set(fields) - set(ACCOUNT_FIELDS)
        if bad:
            raise ValueError(f"unknown account fields {sorted(bad)}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.x(f"UPDATE accounts SET {sets} WHERE id = ?", (*fields.values(), aid))

    def delete_account(self, aid: str, now: float = 0.0) -> list[str]:
        """Forget an account and everything Bombadil kept for it. Returns the ids of the drafts that went, whose
        attachment copies are the caller's to remove. The address is remembered as forgotten, so the engine's
        list (which still has it until Thunderbird restarts) does not bring it back."""
        with self.tx():
            acct = self.account(aid)
            gone = [r["id"] for r in self.q("SELECT id FROM drafts WHERE account = ?", (aid,))]
            for d in gone:
                self.x("DELETE FROM receipts WHERE draft = ?", (d,))
            self.x("DELETE FROM drafts WHERE account = ?", (aid,))
            self.x("DELETE FROM marks WHERE account = ?", (aid,))
            self.x("DELETE FROM accounts WHERE id = ?", (aid,))
            if acct is not None:
                self.x("INSERT OR REPLACE INTO forgotten(email, ts) VALUES(?, ?)", (acct["email"], now))
        return gone

    def is_forgotten(self, email: str) -> bool:
        return self.one("SELECT 1 AS x FROM forgotten WHERE email = ?", (email,)) is not None

    # -- marks --

    def set_mark(self, account: str, key: str, why: str, sender_name: str, sender_email: str, subject: str,
                 ts: float, now: float) -> None:
        self.x("INSERT OR REPLACE INTO marks(account, key, why, sender_name, sender_email, subject, ts, marked) "
               "VALUES(?,?,?,?,?,?,?,?)", (account, key, why, sender_name, sender_email, subject, ts, now))

    def clear_mark(self, account: str, key: str) -> bool:
        return self.x("DELETE FROM marks WHERE account = ? AND key = ?", (account, key)).rowcount > 0

    def mark(self, account: str, key: str) -> dict | None:
        return self.one("SELECT * FROM marks WHERE account = ? AND key = ?", (account, key))

    def marks_for(self, account: str, keys: list[str]) -> dict[str, dict]:
        """The marks among these keys of one account, by key. SQLite takes at most 999 variables."""
        out: dict[str, dict] = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            rows = self.q(f"SELECT * FROM marks WHERE account = ? AND key IN ({','.join('?' * len(part))})",
                          (account, *part))
            out.update({r["key"]: r for r in rows})
        return out

    def marks(self, limit: int = 200, offset: int = 0, account: str | None = None) -> list[dict]:
        where, args = ("WHERE account = ? ", (account,)) if account else ("", ())
        return self.q(f"SELECT * FROM marks {where}ORDER BY ts DESC, key LIMIT ? OFFSET ?", (*args, limit, offset))

    def marks_between(self, account: str, newest: float, oldest: float | None) -> list[dict]:
        """An account's marks whose mail is dated inside a page of its inbox: those the page should have shown."""
        if oldest is None:
            return self.q("SELECT * FROM marks WHERE account = ? AND ts <= ?", (account, newest))
        return self.q("SELECT * FROM marks WHERE account = ? AND ts <= ? AND ts > ?", (account, newest, oldest))

    def count_marks(self) -> int:
        return self.one("SELECT COUNT(*) AS n FROM marks")["n"]

    # -- drafts --

    def new_draft_id(self) -> str:
        return f"d{self._next('drafts')}"

    def put_draft(self, d: dict) -> None:
        """Write a whole draft (inserting it or replacing what was there)."""
        row = {"id": d["id"], "account": d["account"], "kind": d["kind"], "reply_to": d["reply_to"],
               "subject": d["subject"], "body": d["body"], "fingerprint": d["fingerprint"],
               "created_by": d["created_by"], "tainted": int(d["tainted"]), "adds": d["adds"], "state": d["state"],
               "shown_fp": d.get("shown_fp"), "shown_at": d.get("shown_at"), "created": d["created"],
               "updated": d["updated"]}
        for column, name in JSON_FIELDS.items():
            row[column] = json.dumps(d[name], separators=(",", ":"))
        cols = ", ".join(row)
        self.x(f"INSERT OR REPLACE INTO drafts({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))

    def draft(self, draft_id: str) -> dict | None:
        with self._lock:
            return _draft(self.db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone())

    def drafts(self, states=("open", "sending", "unknown"), account: str | None = None) -> list[dict]:
        marks = ",".join("?" * len(states))
        where, args = f"state IN ({marks})", list(states)
        if account:
            where += " AND account = ?"
            args.append(account)
        with self._lock:
            rows = self.db.execute(f"SELECT * FROM drafts WHERE {where} ORDER BY updated DESC, id", args).fetchall()
            return [_draft(r) for r in rows]

    def count_drafts(self) -> int:
        return self.one("SELECT COUNT(*) AS n FROM drafts WHERE state IN ('open', 'sending', 'unknown')")["n"]

    def set_state(self, draft_id: str, state: str, now: float, *, was: tuple[str, ...] | None = None) -> bool:
        """Move a draft to a state, only from one of `was` when that is given. False when it was not there."""
        sql, args = "UPDATE drafts SET state = ?, updated = ? WHERE id = ?", [state, now, draft_id]
        if was:
            sql += f" AND state IN ({','.join('?' * len(was))})"
            args += list(was)
        return self.x(sql, args).rowcount > 0

    def set_shown(self, draft_id: str, fingerprint: str, now: float) -> bool:
        """Record that the view drew this draft with this fingerprint. False when the draft is not open with
        exactly that fingerprint any more, so a view that drew an old version cannot vouch for the new one."""
        return self.x("UPDATE drafts SET shown_fp = ?, shown_at = ? WHERE id = ? AND fingerprint = ? "
                      "AND state IN ('open', 'unknown')", (fingerprint, now, draft_id, fingerprint)).rowcount > 0

    def begin_send(self, draft_id: str, fingerprint: str, now: float) -> bool:
        """Write "sending" for a draft that is still exactly what was checked: open (or unknown, when the
        person chooses to try again), with this fingerprint, and shown with it. False when any of that changed."""
        return self.x("UPDATE drafts SET state = 'sending', updated = ? WHERE id = ? AND state IN ('open', 'unknown') "
                      "AND fingerprint = ? AND shown_fp = ?", (now, draft_id, fingerprint, fingerprint)).rowcount > 0

    def lose_sends(self, now: float) -> list[str]:
        """After a stop or a crash: a draft found "sending" may or may not have gone. It becomes "unknown"."""
        with self.tx():
            ids = [r["id"] for r in self.q("SELECT id FROM drafts WHERE state = 'sending'")]
            self.x("UPDATE drafts SET state = 'unknown', updated = ? WHERE state = 'sending'", (now,))
        return ids

    def finish_send(self, draft_id: str, receipt: dict, recipients: list[str], reply_to: tuple[str, str] | None,
                    now: float) -> None:
        """Everything that is true once a message has gone, at once: the draft is sent and has its receipt, the
        addresses are ones the person has written to, and the mail it answered no longer needs an answer."""
        with self.tx():
            self.set_state(draft_id, "sent", now)
            self.x("INSERT OR REPLACE INTO receipts(draft, body, ts) VALUES(?,?,?)",
                   (draft_id, json.dumps(receipt), receipt["ts"]))
            for email in recipients:
                self.x("INSERT INTO sent_to(email, last, n) VALUES(?, ?, 1) ON CONFLICT(email) DO UPDATE SET "
                       "last = excluded.last, n = n + 1", (email, now))
            if reply_to is not None:
                self.clear_mark(*reply_to)

    def receipt(self, draft_id: str) -> dict | None:
        row = self.one("SELECT body FROM receipts WHERE draft = ?", (draft_id,))
        return json.loads(row["body"]) if row else None

    def sent_to(self, emails: list[str]) -> set[str]:
        """Which of these addresses the person has sent to."""
        if not emails:
            return set()
        rows = self.q(f"SELECT email FROM sent_to WHERE email IN ({','.join('?' * len(emails))})", list(emails))
        return {r["email"] for r in rows}

    def prune(self, now: float, keep: float = RETENTION_S) -> list[str]:
        """Drop drafts that were sent or discarded long ago, with their receipts. Returns their ids."""
        with self.tx():
            old = [r["id"] for r in self.q("SELECT id FROM drafts WHERE state IN ('sent', 'discarded') AND updated < ?",
                                           (now - keep,))]
            for d in old:
                self.x("DELETE FROM receipts WHERE draft = ?", (d,))
                self.x("DELETE FROM drafts WHERE id = ?", (d,))
        return old

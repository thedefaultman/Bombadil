"""brain.db: things, the events behind them, and the links between them.

One SQLite file in WAL mode under ~/.local/state/bombadil, which home snapshots already
leave out, so an undo never erases the record of what was undone. It is a cache: every
fact in it comes from plain files, xattrs, turns.jsonl, git, Chromium's History or
pacman.log, and `bombadil brain rebuild` makes it again from them.

A thing is anything a person would name: a file, a folder, a project, an app, a page, a
site, one of the machine's turns, a coding session, a package, a fact from memory.md.
Things with a place on disk are found by `path` (unique among the living); the rest by
`key` ("turn:41", "url:https://…", "app:passwords"). A thing is never removed for being
old or gone: `deleted` says when it went, and its history stays.

Events are what was witnessed, each with who did it: `actor` is you, turn, session, app,
system, unknown (written while nobody was watching) or before (there before the brain),
and `actor_thing` is the turn, session or app thing when there is one. Links are the
strong edges (made_by, changed_by, came_from, asked_about, mentions) with when they first
and last happened, how often, and the latest event behind them, so "why" is one query.
The weak ones (used with, read with) are worked out from events when Focus asks.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
BUSY_MS = 5000
# A burst of saves by the same writer is one change: events on the same thing by the same
# actor closer together than this are folded into one row (t_end and n grow).
COALESCE_S = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS things(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  key TEXT UNIQUE,
  path TEXT,
  url TEXT,
  title TEXT NOT NULL DEFAULT '',
  area INTEGER,
  parent INTEGER,
  created REAL,
  changed REAL,
  touched REAL,
  touches INTEGER NOT NULL DEFAULT 0,
  made_by TEXT,
  made_by_thing INTEGER,
  made_via TEXT,
  deleted REAL,
  size INTEGER,
  ino INTEGER,
  private INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  forgotten INTEGER NOT NULL DEFAULT 0,
  meta TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS things_live_path ON things(path) WHERE deleted IS NULL AND path IS NOT NULL;
CREATE INDEX IF NOT EXISTS things_any_path ON things(path);
CREATE INDEX IF NOT EXISTS things_parent ON things(parent);
CREATE INDEX IF NOT EXISTS things_area ON things(area);
CREATE INDEX IF NOT EXISTS things_touched ON things(touched);
CREATE INDEX IF NOT EXISTS things_made_by_thing ON things(made_by_thing);
CREATE INDEX IF NOT EXISTS things_ino ON things(ino);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  t REAL NOT NULL,
  t_end REAL,
  n INTEGER NOT NULL DEFAULT 1,
  kind TEXT NOT NULL,
  thing INTEGER NOT NULL,
  actor TEXT,
  actor_thing INTEGER,
  via TEXT,
  other INTEGER,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS events_thing_t ON events(thing, t);
CREATE INDEX IF NOT EXISTS events_t ON events(t);
CREATE INDEX IF NOT EXISTS events_actor_thing ON events(actor_thing, t);
CREATE INDEX IF NOT EXISTS events_kind_t ON events(kind, t);
CREATE TABLE IF NOT EXISTS links(
  src INTEGER NOT NULL,
  dst INTEGER NOT NULL,
  kind TEXT NOT NULL,
  first REAL,
  last REAL,
  n INTEGER NOT NULL DEFAULT 1,
  event INTEGER,
  PRIMARY KEY(src, dst, kind)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS links_dst ON links(dst, kind);
CREATE TABLE IF NOT EXISTS turns(
  n INTEGER PRIMARY KEY,
  unit TEXT UNIQUE,
  thing INTEGER,
  started REAL,
  ended REAL,
  prompt TEXT
);
CREATE TABLE IF NOT EXISTS descriptions(
  thing INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  t REAL NOT NULL,
  model TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(title, name, path, tokenize='trigram');
"""

THING_FIELDS = ("kind", "key", "path", "url", "title", "area", "parent", "created", "changed", "touched",
                "touches", "made_by", "made_by_thing", "made_via", "deleted", "size", "ino", "private",
                "pinned", "forgotten", "meta")


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    if "meta" in d:
        try:
            d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
        except ValueError:
            d["meta"] = {}
    if "detail" in d:
        try:
            d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
        except ValueError:
            d["detail"] = {}
    return d


def _name(path: str | None) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] if path else ""


class Store:
    """All of brain.db's reads and writes. One Store per thread: the service keeps one for
    its loop, and the first index opens its own; WAL lets them read while one writes."""

    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=BUSY_MS / 1000, isolation_level=None,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self.db.execute(f"PRAGMA busy_timeout={BUSY_MS}")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        if self.get_meta("schema") is None:
            self.set_meta("schema", str(SCHEMA_VERSION))

    def close(self) -> None:
        self.db.close()

    # -- transactions --

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
            self.db.execute("COMMIT")

    def q(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        with self._lock:
            return _row(self.db.execute(sql, args).fetchone())

    def x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, args)

    # -- meta --

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return r["value"] if r else default

    def set_meta(self, key: str, value) -> None:
        self.x("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))

    # -- things --

    def get(self, thing_id: int | None) -> dict | None:
        if thing_id is None:
            return None
        return self.one("SELECT * FROM things WHERE id = ?", (thing_id,))

    def by_path(self, path: str, deleted: bool = False) -> dict | None:
        """The living thing at `path`; with deleted=True, the one that went last if none lives."""
        live = self.one("SELECT * FROM things WHERE path = ? AND deleted IS NULL", (path,))
        if live is not None or not deleted:
            return live
        return self.one("SELECT * FROM things WHERE path = ? ORDER BY deleted DESC LIMIT 1", (path,))

    def by_key(self, key: str) -> dict | None:
        return self.one("SELECT * FROM things WHERE key = ?", (key,))

    def by_ino(self, ino: int) -> list[dict]:
        return self.q("SELECT * FROM things WHERE ino = ?", (ino,))

    def add(self, kind: str, title: str, **fields) -> int:
        """A new thing. Keys and live paths are unique: callers look them up first."""
        fields = {k: v for k, v in fields.items() if v is not None}
        bad = set(fields) - set(THING_FIELDS)
        if bad:
            raise ValueError(f"unknown thing fields {sorted(bad)}")
        if isinstance(fields.get("meta"), dict):
            fields["meta"] = json.dumps(fields["meta"])
        fields["kind"] = kind
        fields["title"] = title or _name(fields.get("path")) or kind
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.tx():
            cur = self.x(f"INSERT INTO things({cols}) VALUES({marks})", tuple(fields.values()))
            tid = cur.lastrowid
            self._index(tid)
        return tid

    def upsert_key(self, kind: str, key: str, title: str = "", **fields) -> int:
        """The thing with this key, made if new; given fields overwrite (None leaves as is)."""
        with self.tx():
            have = self.by_key(key)
            if have is None:
                return self.add(kind, title, key=key, **fields)
            changes = {k: v for k, v in fields.items() if v is not None}
            if title:
                changes["title"] = title
            if changes:
                self.update(have["id"], **changes)
            return have["id"]

    def update(self, thing_id: int, **fields) -> None:
        if not fields:
            return
        bad = set(fields) - set(THING_FIELDS)
        if bad:
            raise ValueError(f"unknown thing fields {sorted(bad)}")
        if isinstance(fields.get("meta"), dict):
            fields["meta"] = json.dumps(fields["meta"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.tx():
            self.x(f"UPDATE things SET {sets} WHERE id = ?", (*fields.values(), thing_id))
            if {"title", "path", "url"} & set(fields):
                self._index(thing_id)

    def merge_meta(self, thing_id: int, **values) -> None:
        t = self.get(thing_id)
        if t is None:
            return
        meta = dict(t["meta"] or {})
        meta.update(values)
        self.update(thing_id, meta=meta)

    def touch(self, thing_id: int, t: float, changed: bool = False) -> None:
        """Someone did something to it: it gets brighter."""
        extra = ", changed = MAX(COALESCE(changed, 0), ?)" if changed else ""
        args = (t, t) if changed else (t,)
        self.x(f"UPDATE things SET touched = MAX(COALESCE(touched, 0), ?), touches = touches + 1{extra} "
               f"WHERE id = ?", (*args, thing_id))

    def move(self, thing_id: int, new_path: str, parent: int | None, area: int | None) -> None:
        """A rename or a move: the thing keeps its identity, and so does everything inside it."""
        with self.tx():
            old = self.get(thing_id)
            if old is None:
                return
            old_path = old["path"]
            self.update(thing_id, path=new_path, title=_name(new_path) if old["kind"] in ("file", "folder")
                        else old["title"], parent=parent, area=area)
            if old_path and old["kind"] in ("folder", "project", "app"):
                prefix = old_path.rstrip("/") + "/"
                inside = self.q("SELECT id, path FROM things WHERE deleted IS NULL AND path LIKE ? ESCAPE '\\'",
                                (_like_prefix(prefix),))
                for row in inside:
                    self.x("UPDATE things SET path = ? WHERE id = ?",
                           (new_path.rstrip("/") + "/" + row["path"][len(prefix):], row["id"]))
                    self._index(row["id"])

    def mark_deleted(self, thing_id: int, t: float) -> list[int]:
        """It went (and, for a folder, everything inside it). Returns the ids marked."""
        with self.tx():
            thing = self.get(thing_id)
            if thing is None or thing["deleted"] is not None:
                return []
            ids = [thing_id]
            if thing["path"] and thing["kind"] in ("folder", "project", "app"):
                prefix = thing["path"].rstrip("/") + "/"
                ids += [r["id"] for r in self.q(
                    "SELECT id FROM things WHERE deleted IS NULL AND path LIKE ? ESCAPE '\\'", (_like_prefix(prefix),))]
            for i in ids:
                self.x("UPDATE things SET deleted = ? WHERE id = ?", (t, i))
            return ids

    def revive(self, thing_id: int) -> bool:
        """An editor that deletes and writes again keeps the same thing. False when another
        thing already lives at that path."""
        thing = self.get(thing_id)
        if thing is None or thing["deleted"] is None:
            return False
        if thing["path"] and self.by_path(thing["path"]) is not None:
            return False
        self.x("UPDATE things SET deleted = NULL WHERE id = ?", (thing_id,))
        return True

    def children(self, parent: int, live: bool = True, limit: int = 500, offset: int = 0) -> list[dict]:
        extra = "AND deleted IS NULL" if live else ""
        return self.q(f"SELECT * FROM things WHERE parent = ? {extra} AND forgotten = 0 "
                      "ORDER BY COALESCE(touched, changed, created, 0) DESC LIMIT ? OFFSET ?",
                      (parent, limit, offset))

    def merge_things(self, keep: int, drop: int) -> None:
        """Two things turned out to be one (a turn first seen by its scope, then by its number)."""
        if keep == drop:
            return
        with self.tx():
            for col in ("thing", "actor_thing", "other"):
                self.x(f"UPDATE events SET {col} = ? WHERE {col} = ?", (keep, drop))
            for col in ("made_by_thing", "area", "parent"):
                self.x(f"UPDATE things SET {col} = ? WHERE {col} = ?", (keep, drop))
            for row in self.q("SELECT * FROM links WHERE src = ? OR dst = ?", (drop, drop)):
                src = keep if row["src"] == drop else row["src"]
                dst = keep if row["dst"] == drop else row["dst"]
                self.x("DELETE FROM links WHERE src = ? AND dst = ? AND kind = ?", (row["src"], row["dst"], row["kind"]))
                if src != dst:
                    self._link_row(src, dst, row["kind"], row["first"], row["last"], row["n"], row["event"])
            self.x("UPDATE turns SET thing = ? WHERE thing = ?", (keep, drop))
            self.x("DELETE FROM descriptions WHERE thing = ?", (drop,))
            self.x("DELETE FROM search WHERE rowid = ?", (drop,))
            self.x("DELETE FROM things WHERE id = ?", (drop,))

    # -- search index --

    def _index(self, thing_id: int) -> None:
        t = self.get(thing_id)
        self.x("DELETE FROM search WHERE rowid = ?", (thing_id,))
        if t is None or t["forgotten"]:
            return
        name = _name(t["path"]) if t["path"] else (t["url"] or "")
        self.x("INSERT INTO search(rowid, title, name, path) VALUES(?, ?, ?, ?)",
               (thing_id, t["title"] or "", name, t["path"] or t["url"] or ""))

    def search(self, query: str, kind: str | None = None, limit: int = 20, live: bool = True) -> list[dict]:
        """Things whose title, name or path contain the words, most alive first. Trigrams need
        three letters; shorter words match the start of a name instead."""
        words = [w for w in query.split() if w]
        if not words:
            return []
        filters, args = [], []
        if kind:
            filters.append("things.kind = ?")
            args.append(kind)
        if live:
            filters.append("things.deleted IS NULL")
        filters.append("things.forgotten = 0")
        where = " AND ".join(filters)
        if all(len(w) >= 3 for w in words):
            match = " AND ".join('"' + w.replace('"', '""') + '"' for w in words)
            sql = (f"SELECT things.* FROM search JOIN things ON things.id = search.rowid "
                   f"WHERE search MATCH ? AND {where} "
                   f"ORDER BY things.pinned DESC, COALESCE(things.touched, things.changed, things.created, 0) DESC "
                   f"LIMIT ?")
            return self.q(sql, (match, *args, limit))
        conds = " AND ".join("lower(things.title) LIKE ? ESCAPE '\\'" for _ in words)
        likes = [_like_prefix(words[0].lower())] + [f"%{_like_escape(w.lower())}%" for w in words[1:]]
        sql = (f"SELECT things.* FROM things WHERE {conds} AND {where} "
               f"ORDER BY things.pinned DESC, COALESCE(things.touched, things.changed, things.created, 0) DESC "
               f"LIMIT ?")
        return self.q(sql, (*likes, *args, limit))

    # -- events --

    def event(self, t: float, kind: str, thing: int, actor: str | None = None, actor_thing: int | None = None,
              via: str | None = None, other: int | None = None, detail: dict | None = None,
              coalesce: float = COALESCE_S) -> int:
        """Record what happened. Saves by the same writer close together fold into one."""
        with self.tx():
            if coalesce and kind == "change":
                last = self.one("SELECT * FROM events WHERE thing = ? ORDER BY t DESC, id DESC LIMIT 1", (thing,))
                # A new file is a create and then the save that fills it: one event, not two.
                if (last and last["kind"] in ("change", "create") and last["actor"] == actor
                        and last["actor_thing"] == actor_thing and last["via"] == via
                        and 0 <= t - (last["t_end"] or last["t"]) <= coalesce):
                    self.x("UPDATE events SET t_end = ?, n = n + 1 WHERE id = ?", (t, last["id"]))
                    return last["id"]
            cur = self.x("INSERT INTO events(t, kind, thing, actor, actor_thing, via, other, detail) "
                         "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                         (t, kind, thing, actor, actor_thing, via, other, json.dumps(detail) if detail else None))
            return cur.lastrowid

    def events(self, thing: int, limit: int = 50, kinds: tuple[str, ...] | None = None,
               since: float | None = None) -> list[dict]:
        filters, args = ["thing = ?"], [thing]
        if kinds:
            filters.append(f"kind IN ({', '.join('?' for _ in kinds)})")
            args += list(kinds)
        if since is not None:
            filters.append("COALESCE(t_end, t) >= ?")
            args.append(since)
        return self.q(f"SELECT * FROM events WHERE {' AND '.join(filters)} ORDER BY t DESC, id DESC LIMIT ?",
                      (*args, limit))

    def events_by(self, actor_thing: int, limit: int = 200) -> list[dict]:
        return self.q("SELECT * FROM events WHERE actor_thing = ? ORDER BY t DESC LIMIT ?", (actor_thing, limit))

    # -- links --

    def _link_row(self, src, dst, kind, first, last, n, event):
        self.x("INSERT INTO links(src, dst, kind, first, last, n, event) VALUES(?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(src, dst, kind) DO UPDATE SET first = MIN(COALESCE(first, excluded.first), "
               "excluded.first), last = MAX(COALESCE(last, excluded.last), excluded.last), n = n + excluded.n, "
               "event = COALESCE(excluded.event, event)",
               (src, dst, kind, first, last, n, event))

    def link(self, src: int, dst: int, kind: str, t: float, event: int | None = None) -> None:
        if src == dst:
            return
        self._link_row(src, dst, kind, t, t, 1, event)

    def links_from(self, src: int, kinds: tuple[str, ...] | None = None) -> list[dict]:
        extra = f"AND kind IN ({', '.join('?' for _ in kinds)})" if kinds else ""
        return self.q(f"SELECT * FROM links WHERE src = ? {extra} ORDER BY last DESC", (src, *(kinds or ())))

    def links_to(self, dst: int, kinds: tuple[str, ...] | None = None) -> list[dict]:
        extra = f"AND kind IN ({', '.join('?' for _ in kinds)})" if kinds else ""
        return self.q(f"SELECT * FROM links WHERE dst = ? {extra} ORDER BY last DESC", (dst, *(kinds or ())))

    # -- turns --

    def turn(self, n: int) -> dict | None:
        return self.one("SELECT * FROM turns WHERE n = ?", (n,))

    def turn_by_unit(self, unit: str) -> dict | None:
        return self.one("SELECT * FROM turns WHERE unit = ?", (unit,))

    def turn_of_thing(self, thing: int) -> dict | None:
        return self.one("SELECT * FROM turns WHERE thing = ?", (thing,))

    def set_turn(self, n: int, thing: int, unit: str | None = None, started: float | None = None,
                 ended: float | None = None, prompt: str | None = None) -> None:
        self.x("INSERT INTO turns(n, unit, thing, started, ended, prompt) VALUES(?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(n) DO UPDATE SET unit = COALESCE(excluded.unit, unit), thing = excluded.thing, "
               "started = COALESCE(excluded.started, started), ended = COALESCE(excluded.ended, ended), "
               "prompt = COALESCE(excluded.prompt, prompt)",
               (n, unit, thing, started, ended, prompt))

    def last_turn(self) -> int:
        r = self.one("SELECT MAX(n) AS n FROM turns")
        return int(r["n"]) if r and r["n"] is not None else 0

    # -- descriptions --

    def description(self, thing: int) -> dict | None:
        return self.one("SELECT * FROM descriptions WHERE thing = ?", (thing,))

    def set_description(self, thing: int, text: str, fingerprint: str, model: str | None = None,
                        t: float | None = None) -> None:
        self.x("INSERT INTO descriptions(thing, text, fingerprint, t, model) VALUES(?, ?, ?, ?, ?) "
               "ON CONFLICT(thing) DO UPDATE SET text = excluded.text, fingerprint = excluded.fingerprint, "
               "t = excluded.t, model = excluded.model",
               (thing, text, fingerprint, t or time.time(), model))

    # -- counts --

    def counts(self) -> dict:
        r = self.one("SELECT COUNT(*) AS things, SUM(deleted IS NULL) AS live FROM things")
        e = self.one("SELECT COUNT(*) AS events FROM events")
        return {"things": r["things"] or 0, "live": r["live"] or 0, "events": e["events"] or 0}


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_prefix(s: str) -> str:
    return _like_escape(s) + "%"

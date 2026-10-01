"""loop.db's counting half: requests, the groups of them (`asks`), what was offered, what they said no to.

`LoopStore.ingest()` reads turns.jsonl from a byte offset, turns each new model turn into a request
(reading its per-turn log once for its route), decides whether it counts, and puts it in a group,
`CHUNK` rows to a transaction and the byte offset in the same one, so a crash costs at most
that chunk and reading the same file twice changes nothing.
Group ids come from the first member's id and members never move, so a group that was offered stays
the group it was offered as. Nothing here ever runs on a turn's path; agentd calls it from a thread.

What cannot be made again is kept apart from what can: `requests` and `asks` are derived (and
"forget what I ask" erases them), `offers`, `nevers` and `words_used` are their answers and stay,
and the byte offset never goes back, so what they forgot is not read in again.
"""

import dataclasses
import json
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from .. import apps as apps_mod
from .. import launcher, paths
from . import db, forms, habits, offers, route
from . import ledger as ledger_mod
from .habits import Group, Grouper, Profile
from .ledger import Request

SCHEMA = ["""
CREATE TABLE requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    t REAL NOT NULL,
    ended REAL NOT NULL,
    day TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'typed',
    ok INTEGER,
    stopped INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT '',
    session TEXT,
    snapshot INTEGER,
    details TEXT NOT NULL DEFAULT '',
    model TEXT,
    cost REAL,
    changed INTEGER NOT NULL DEFAULT 0,
    signin INTEGER NOT NULL DEFAULT 0,
    undone_at REAL,
    stopped_at REAL,
    route TEXT NOT NULL DEFAULT '[]',
    verb TEXT NOT NULL DEFAULT '',
    named TEXT NOT NULL DEFAULT '[]',
    near_miss INTEGER NOT NULL DEFAULT 0,
    style INTEGER NOT NULL DEFAULT 0,
    opens TEXT NOT NULL DEFAULT '',
    counted INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    grp TEXT
);
CREATE INDEX requests_grp ON requests(grp);
CREATE INDEX requests_t ON requests(t);
CREATE INDEX requests_snapshot ON requests(snapshot);
CREATE TABLE asks (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    first REAL NOT NULL DEFAULT 0,
    last REAL NOT NULL DEFAULT 0,
    days TEXT NOT NULL DEFAULT '[]',
    n INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 0,
    members TEXT NOT NULL DEFAULT '[]',
    form TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'counting',
    snooze_n INTEGER NOT NULL DEFAULT 0,
    snooze_t REAL NOT NULL DEFAULT 0,
    text_dropped INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX asks_state ON asks(state);
CREATE TABLE offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grp TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    n INTEGER NOT NULL DEFAULT 0,
    days INTEGER NOT NULL DEFAULT 0,
    existing INTEGER NOT NULL DEFAULT 0,
    shown_t REAL NOT NULL,
    answered_t REAL,
    outcome TEXT NOT NULL DEFAULT '',
    chosen TEXT NOT NULL DEFAULT ''
);
CREATE INDEX offers_grp ON offers(grp);
CREATE TABLE words_used (
    phrase TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    last_used REAL NOT NULL DEFAULT 0,
    made_t REAL NOT NULL DEFAULT 0
);
CREATE TABLE nevers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grp TEXT NOT NULL,
    signature TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT '',
    t REAL NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    sentence TEXT NOT NULL DEFAULT ''
);
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
)"""]

# Why a request was not counted, for the report; "" is counted.
REASONS = ("", "origin", "empty", "shell", "sign-in", "private", "long", *habits.FRICTION, "never", "error")
_UNGROUPED = ("never", *habits.FRICTION)        # asks that may belong to no group: its aging cannot reach them
_TEXT_KEPT = ("", *_UNGROUPED)                  # the words of anything else are not stored at all

WITHDRAWN = "withdrawn"     # an offer taken back because its group changed under it: nobody answered it
STATES = ("counting", "offered", "not_now", "said_no", "made", "got_it")
CHUNK = 150     # requests counted in one transaction: the write lock is held for well under a second
MEMORY = Path(":memory:")
_OPEN_LOCK = threading.Lock()


@dataclass
class IngestResult:
    new: int = 0                 # requests read for the first time
    counted: int = 0             # of them, how many count as asks
    friction: int = 0            # retries, stops, undos: not counted, kept as signals
    changed: list[str] = field(default_factory=list)   # ids of the groups that changed
    reset: bool = False          # the file was replaced or shrank and was read again from the start
    offset: int = 0
    more: bool = False           # a `limit` stopped the read: there may be more rows to read

    def __bool__(self) -> bool:
        return bool(self.new or self.changed)

    def add(self, other: "IngestResult") -> None:
        """Fold in the result of the chunk read after this one."""
        self.new += other.new
        self.counted += other.counted
        self.friction += other.friction
        self.changed = sorted({*self.changed, *other.changed})
        self.reset = self.reset or other.reset
        self.offset, self.more = other.offset, other.more


class LoopStore:
    """The counts, over loop.db. One instance per process is fine; it opens a connection for each
    thread that uses it. Pass `app_list` (the apps the launcher knows) to keep a test independent of
    the machine, `built` to say which forms can be made, and `config` to fix the offer thresholds."""

    def __init__(self, path: Path | None = None, *, app_list: list | None = None,
                 built: Iterable[str] | None = None, config: offers.Config | None = None,
                 logs_dir: Path | None = None):
        self.path = Path(path) if path is not None else paths.loop_db()
        self._app_list = app_list
        self.built = forms.BUILT if built is None else frozenset(built)
        self._config = config
        self.logs_dir = logs_dir       # look for a turn's log here instead of where its row says
        self._local = threading.local()
        self._lock = threading.RLock()
        self._shared: object | None = None
        self._grouper: Grouper | None = None
        self._grouper_version = -1
        self._apps_cache: tuple[float, list] | None = None

    # -- plumbing --

    @property
    def conn(self):
        if self.path == MEMORY:
            if self._shared is None:
                self._shared = self._open()
            return self._shared
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = self._open()
        return c

    def _open(self):
        c = db.connect(self.path)
        # db.schema checks the version and then creates, so two first opens of one file can both try to
        # create: one at a time inside this process, and a short retry for another process.
        for attempt in range(4):
            try:
                with _OPEN_LOCK:
                    db.schema(c, "store", SCHEMA)
                return c
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e) or attempt == 3:
                    c.close()
                    raise
                time.sleep(0.05 * (attempt + 1))
        return c

    def close(self) -> None:
        for c in (getattr(self._local, "conn", None), self._shared):
            if c is not None:
                c.close()
        self._local.conn = None
        self._shared = None

    @property
    def cfg(self) -> offers.Config:
        return self._config or offers.load_config()

    def _meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def _set_meta(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def _bump(self, keep: Grouper | None = None) -> None:
        """Say the requests or their groups changed, so another process reloads what it kept. `keep` is
        the grouper this process just brought up to date, which stays good for the next call."""
        version = int(self._meta("version", 0)) + 1
        self._set_meta("version", version)
        self._grouper, self._grouper_version = keep, version if keep is not None else -1

    def _apps(self) -> list:
        if self._app_list is not None:
            return self._app_list
        now = time.monotonic()
        if self._apps_cache is None or now - self._apps_cache[0] > 5:
            try:
                found = launcher.known_apps()
            except Exception:  # noqa: BLE001 - a broken apps folder must not stop the counting
                found = []
            self._apps_cache = (now, found)
        return self._apps_cache[1]

    def _titles(self) -> dict[str, str]:
        return {f"app:{a.name}": str(a.title) for a in self._apps()}

    # -- reading what was written --

    def ingest(self, log: Path | str | None = None, now: float | None = None,
               limit: int | None = None) -> IngestResult:
        """Read what turns.jsonl gained since last time and count it. Safe to call as often as you
        like, and again on the same file: a line is read once (by byte offset) and a request once (by
        id). A file that was replaced or shrank is read again from the top. Rows are counted `CHUNK`
        to a transaction, so a long backlog never holds the write lock for long and a crash resumes at
        the last chunk; with `limit` only that many rows are read, and `more` says to call again."""
        log = Path(log) if log else paths.turns_log()
        now = time.time() if now is None else now
        if limit is not None:
            return self._ingest_chunk(log, now, limit)
        total = IngestResult()
        while True:
            chunk = self._ingest_chunk(log, now, CHUNK)
            total.add(chunk)
            if not chunk.more:
                return total

    def _ingest_chunk(self, log: Path, now: float, limit: int) -> IngestResult:
        with self._lock:
            offset = int(self._meta("offset", 0))
            inode = self._meta("inode")
            batch = ledger_mod.read_rows(log, offset, int(inode) if inode not in (None, "") else None, limit)
            result = self._apply([row for row, _ in batch.rows], now, batch.end, batch.inode)
            result.reset, result.more = batch.reset, batch.more
            return result

    def _apply(self, rows: list[dict], now: float, end: int | None = None, inode: int | None = None) -> IngestResult:
        app_list = self._apps()
        things = habits.things_from_launcher(app_list)
        result = IngestResult()
        # What needs no write lock is done before it is taken: a restart rebuilds the groups here, and a
        # turn's route comes from its log, which depends on nothing stored.
        with self._lock:
            self._load_grouper(things, app_list)
        routes: dict[str, tuple[list[str], int]] = {}
        for row in rows:
            req = ledger_mod.request_from_row(row) if row.get("kind") is None else None
            if req is not None and _wants_route(req):
                routes[req.id] = self._route_of(req)
        with self._lock, _abandon_on_error(self), db.transaction(self.conn):
            forgot = float(self._meta("forgot_t", 0))
            grouper = self._load_grouper(things, app_list)
            changed: set[str] = set()
            new: list[Request] = []
            effects = []
            for row in rows:
                kind = row.get("kind")
                if kind == "local":
                    word = row.get("word")
                    if row.get("via") == "word" and isinstance(word, str) and word:
                        self._word_used(word, float(row["t"]))
                    eff = ledger_mod.local_effect(row)
                    if eff:
                        effects.append(eff)
                elif kind is None:
                    req = ledger_mod.request_from_row(row)
                    if req is not None and req.ended > forgot:
                        new.append(req)
            for eff in effects:
                target = ledger_mod.find_target(new, eff)
                if target is not None:
                    _mark(target, eff)
                else:
                    self._effect_on_stored(eff, grouper, things, app_list, changed)
            nevers = self._never_profiles()
            prev = self._last_request()
            for req in new:
                if self.conn.execute("SELECT 1 FROM requests WHERE id=?", (req.id,)).fetchone():
                    prev = req
                    continue
                try:
                    self._count(req, prev, grouper, things, app_list, nevers, changed, result, routes)
                except sqlite3.Error:
                    raise
                except Exception:  # noqa: BLE001 - one turn the counting cannot read must not stop the rest
                    self._grouper = None
                    grouper = self._load_grouper(things, app_list)
                    self._insert(req, None, [], False, "error", None)
                    result.new += 1
                prev = req
            self._save_groups(grouper, changed, now)
            if end is not None:
                self._set_meta("offset", end)
                self._set_meta("inode", inode if inode is not None else "")
            self._set_meta("config_hash", offers.config_hash(self.cfg))
            if result.new or changed:
                self._bump(grouper)
            self._maintain(now)
        result.changed = sorted(changed)
        result.offset = end or 0
        return result

    def _log_path(self, req: Request) -> Path | None:
        if not req.details:
            return None
        if self.logs_dir is not None:
            return Path(self.logs_dir) / Path(req.details).name
        return Path(req.details)

    def _route_of(self, req: Request) -> tuple[list[str], int]:
        """What a turn did, from its log: its topics and how many tools it ran."""
        events = ledger_mod.read_turn_log(self._log_path(req), kinds=("tool", "file_change"))
        return route.route(events), sum(1 for e in events if e.get("kind") == "tool")

    def _count(self, req: Request, prev: Request | None, grouper: Grouper, things: dict, app_list: list,
               nevers: list[Profile], changed: set[str], result: IngestResult,
               routes: dict[str, tuple[list[str], int]] | None = None) -> None:
        """Decide one new turn, group it if it counts, and store it. `routes` holds the ones already read."""
        topics: list[str] = []
        if _wants_route(req):
            topics, tools = (routes or {}).get(req.id) or self._route_of(req)
            if not req.steps:
                req.steps = tools
        ok, why = habits.counted(req, prev, topics, nevers, things)
        p = None
        if why in _TEXT_KEPT:
            p = habits.make_profile(req, topics, things, app_list)
        gid = None
        if ok:
            gid = grouper.add(p)
            changed.add(gid)
            result.counted += 1
        elif why in habits.FRICTION:
            result.friction += 1
            gid = grouper.attach_friction(p)
            if gid:
                changed.add(gid)
        result.new += 1
        self._insert(req, p, topics, ok, why, gid)

    def _insert(self, req: Request, p: Profile | None, topics: list[str], ok: bool, why: str,
                gid: str | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO requests(id, t, ended, day, text, origin, ok, stopped, seconds, steps, provider,"
            " session, snapshot, details, model, cost, changed, signin, undone_at, stopped_at, route, verb, named,"
            " near_miss, style, opens, counted, reason, grp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (req.id, req.t, req.ended, req.day, habits.clean_text(req.text) if why in _TEXT_KEPT else "", req.origin,
             None if req.ok is None else int(req.ok), int(req.stopped), req.seconds, req.steps, req.provider,
             req.session, req.snapshot, req.details, req.model, req.cost, int(req.changed), int(req.signin),
             req.undone_at, req.stopped_at, json.dumps(topics), p.verb if p else "",
             json.dumps(sorted(p.named)) if p else "[]", int(p.near_miss) if p else 0, int(p.style) if p else 0,
             p.open_target if p else "", int(ok), why, gid))

    def _last_request(self) -> Request | None:
        row = self.conn.execute("SELECT * FROM requests ORDER BY seq DESC LIMIT 1").fetchone()
        return _request_of(row) if row else None

    def _effect_on_stored(self, eff: tuple, grouper: Grouper, things: dict, app_list: list,
                          changed: set[str]) -> None:
        """An undo or a stop about a turn read in an earlier batch: the turn learns of it, and one that
        counted as an ask and was undone within a minute stops counting (it stays as friction)."""
        action, of, snap, t = eff
        row = None
        if of:
            row = self.conn.execute("SELECT * FROM requests WHERE id=?", (of,)).fetchone()
        if row is None and snap is not None:
            row = self.conn.execute("SELECT * FROM requests WHERE snapshot=? ORDER BY seq DESC LIMIT 1",
                                    (snap,)).fetchone()
        if row is None and of is None and snap is None:
            if action == "stop":
                row = self.conn.execute("SELECT * FROM requests WHERE t<=? AND max(ended, t)+1>=? "
                                        "ORDER BY t DESC LIMIT 1", (t, t)).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM requests WHERE t<=? ORDER BY t DESC LIMIT 1",
                                        (t,)).fetchone()
        if row is None:
            return
        req = _request_of(row)
        before = (req.undone_at, req.stopped_at)
        _mark(req, eff)
        if (req.undone_at, req.stopped_at) == before:
            return
        self.conn.execute("UPDATE requests SET undone_at=?, stopped_at=? WHERE id=?",
                          (req.undone_at, req.stopped_at, req.id))
        why = habits.friction_of(req)
        if row["counted"] and why:
            p = _profile_of(row, things, app_list)
            gid = grouper.remove(req.id)
            if gid is not None:
                grouper.place_friction(p, gid)
                changed.add(gid)
            self.conn.execute("UPDATE requests SET counted=0, reason=? WHERE id=?", (why, req.id))

    # -- the groups --

    def _load_grouper(self, things: dict, app_list: list) -> Grouper:
        """The groups as they stand, rebuilt from what is stored (placed, never re-decided, so nothing
        moves), unless this instance already holds them and nobody else has written since."""
        version = int(self._meta("version", 0))
        if self._grouper is not None and self._grouper_version == version:
            return self._grouper
        g = Grouper()
        live = {r["id"] for r in self.conn.execute("SELECT id FROM asks WHERE text_dropped=0")}
        rows = self.conn.execute("SELECT * FROM requests WHERE grp IS NOT NULL ORDER BY seq")
        for row in rows:
            if row["grp"] not in live or not row["text"]:
                continue
            p = _profile_of(row, things, app_list)
            if row["counted"]:
                g.place(p, row["grp"])
            elif row["grp"] in g.acc:
                g.place_friction(p, row["grp"])
        self._grouper, self._grouper_version = g, version
        return g

    def _save_groups(self, grouper: Grouper, ids: Iterable[str], now: float) -> None:
        for gid in ids:
            g = grouper.group(gid)
            if g is None:
                continue
            g.weigh(now)
            data = g.to_dict()
            for k in ("state", "form"):
                data.pop(k, None)
            self.conn.execute(
                "INSERT INTO asks(id, label, first, last, days, n, weight, members, data) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET label=excluded.label, first=excluded.first, last=excluded.last, "
                "days=excluded.days, n=excluded.n, weight=excluded.weight, members=excluded.members, "
                "data=excluded.data",
                (g.id, g.label, g.first, g.last, json.dumps(g.days), g.n, g.weight, json.dumps(g.members),
                 json.dumps(data)))

    def _group_of(self, row, now: float | None, apps: list | None = None) -> Group:
        g = Group.from_dict(json.loads(row["data"]))
        g.id, g.state, g.form, g.text_dropped = row["id"], row["state"], row["form"], bool(row["text_dropped"])
        if now is not None:
            g.weigh(now)
        if g.opens and g.word and not g.text_dropped:
            g.existing = habits.existing_word(g.word, self._apps() if apps is None else apps)
        return g

    def groups(self, states: Iterable[str] | None = None, now: float | None = None,
               listed_only: bool = False, *, min_n: int = 0, since: float | None = None) -> list[Group]:
        """The groups, heaviest first (each ask weighs 1 and halves every 14 days). `states` picks some
        of counting, offered, not_now, said_no, made, got_it; `listed_only` leaves out what has had no
        ask for 30 days. `min_n` and `since` leave out, in the query, the groups with fewer asks or
        whose last ask is older, so those are never loaded."""
        now = time.time() if now is None else now
        apps = self._apps()
        where: list[str] = []
        args: list = []
        if states is not None:
            wanted = list(states)
            where.append(f"state IN ({','.join('?' * len(wanted))})")
            args += wanted
        if min_n:
            where.append("n>=?")
            args.append(min_n)
        if since is not None:
            where.append("last>=?")
            args.append(since)
        rows = self.conn.execute("SELECT * FROM asks" + (f" WHERE {' AND '.join(where)}" if where else ""), args)
        out = [self._group_of(r, now, apps) for r in rows]
        if listed_only:
            out = [g for g in out if habits.listed(g, now)]
        return sorted(out, key=lambda g: (-g.weight, g.first, g.id))

    def group(self, group_id: str, now: float | None = None) -> Group | None:
        row = self.conn.execute("SELECT * FROM asks WHERE id=?", (group_id,)).fetchone()
        return self._group_of(row, time.time() if now is None else now) if row else None

    def members(self, group_id: str) -> list[dict]:
        """The asks of one group as stored: id, when, their words, what the turn touched. Oldest first."""
        rows = self.conn.execute("SELECT id, t, day, text, route, seconds, steps, counted, reason FROM requests "
                                 "WHERE grp=? ORDER BY seq", (group_id,))
        return [{"id": r["id"], "t": r["t"], "day": r["day"], "text": r["text"], "route": json.loads(r["route"]),
                 "seconds": r["seconds"], "steps": r["steps"], "counted": bool(r["counted"]),
                 "reason": r["reason"]} for r in rows]

    def regroup(self, now: float | None = None) -> IngestResult:
        """Decide every stored ask again from its stored words, route and friction, in the order they were
        asked: what `bring_back` needs after a Never is lifted, and what a change to the rules needs. A
        group that was offered, answered or hidden keeps its members; everything else may regroup. Group
        ids do not change."""
        now = time.time() if now is None else now
        app_list = self._apps()
        things = habits.things_from_launcher(app_list)
        result = IngestResult()
        with self._lock, _abandon_on_error(self), db.transaction(self.conn):
            pinned = {r["id"] for r in self.conn.execute("SELECT id FROM asks WHERE state != 'counting'")}
            nevers = self._never_profiles()
            grouper = Grouper()
            prev = None
            changed: set[str] = set()
            for row in list(self.conn.execute("SELECT * FROM requests ORDER BY seq")):
                req = _request_of(row)
                if row["text"] and row["origin"] == "typed":
                    topics = json.loads(row["route"])
                    ok, why = habits.counted(req, prev, topics, nevers, things)
                    p = _profile_of(row, things, app_list) if why in _TEXT_KEPT else None
                    if ok:
                        gid = grouper.place(p, row["grp"]) if row["grp"] in pinned else grouper.add(p)
                    else:
                        gid = grouper.attach_friction(p) if why in habits.FRICTION else None
                    if (int(ok), why, gid) != (row["counted"], row["reason"], row["grp"]):
                        self.conn.execute("UPDATE requests SET counted=?, reason=?, grp=? WHERE id=?",
                                          (int(ok), why, gid, row["id"]))
                    if gid:
                        changed.add(gid)
                    result.counted += int(ok)
                prev = req
                result.new += 1
            stale = {r["id"] for r in self.conn.execute("SELECT id FROM asks")} - set(grouper.acc) - pinned
            for gid in stale:
                self.conn.execute("DELETE FROM asks WHERE id=?", (gid,))
            self._save_groups(grouper, [gid for gid in grouper.acc if gid in changed or gid in pinned], now)
            self._bump(grouper)
            self._maintain(now)
        result.changed = sorted(changed)
        return result

    def forget_asks(self, now: float | None = None) -> dict:
        """"Forget what I ask": erases the requests and the groups made of them, the words in the offers
        already made, and the sentences they said no to (the Said no list falls back to the label). What they
        said no to (as a signature), the words used, and how many offers were taken stay, and so does the
        place in turns.jsonl, with a mark so that what they forgot is not read in again."""
        now = time.time() if now is None else now
        with self._lock, db.transaction(self.conn):
            n = self.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            m = self.conn.execute("SELECT COUNT(*) FROM asks").fetchone()[0]
            newest = self.conn.execute("SELECT MAX(ended) FROM requests").fetchone()[0] or 0
            self.conn.execute("DELETE FROM requests")
            self.conn.execute("DELETE FROM asks")
            self.conn.execute("UPDATE offers SET label='' WHERE 1")
            self.conn.execute("UPDATE nevers SET sentence='' WHERE 1")
            self.conn.execute("UPDATE offers SET outcome=?, answered_t=? WHERE outcome=''", (WITHDRAWN, now))
            self._set_meta("forgot_t", max(float(self._meta("forgot_t", 0)), newest, now))
            self._bump()
        return {"requests": n, "asks": m}

    def _maintain(self, now: float) -> None:
        """Let time pass: offers nobody answered expire into Not now, a Not now that is over lets its
        group count again, and a group gone quiet for 90 days loses its words (and the sentence they said
        no to it with), and so does what no group holds: an ask held back by a Never or kept as friction."""
        cfg = self.cfg
        for r in list(self.conn.execute("SELECT id, grp, shown_t FROM offers WHERE outcome=''")):
            if offers.expired(r["shown_t"], now, cfg):
                self._close(r["grp"], offers.EXPIRED, now, offer_id=r["id"])
        for r in list(self.conn.execute("SELECT id, n, snooze_n, snooze_t FROM asks WHERE state='not_now'")):
            if offers.released(r["snooze_n"], r["snooze_t"], r["n"], now, cfg):
                self.conn.execute("UPDATE asks SET state='counting' WHERE id=?", (r["id"],))
        cutoff = now - habits.TEXT_DAYS * offers.DAY
        for r in list(self.conn.execute("SELECT * FROM asks WHERE last<? AND text_dropped=0 AND n>0", (cutoff,))):
            g = habits.apply_decay(self._group_of(r, None), now)
            data = g.to_dict()
            for k in ("state", "form"):
                data.pop(k, None)
            self.conn.execute("UPDATE asks SET label='', text_dropped=1, data=? WHERE id=?",
                              (json.dumps(data), r["id"]))
            self.conn.execute("UPDATE requests SET text='' WHERE grp=?", (r["id"],))
        # A group's own aging does not reach these: no group holds them (a Never kept them from
        # counting, or a retry that joined nothing), so their words go on the same 90 days.
        self.conn.execute("UPDATE requests SET text='' WHERE text!='' AND grp IS NULL AND ended<? "
                          f"AND reason IN ({','.join('?' * len(_UNGROUPED))})", (cutoff, *_UNGROUPED))
        self.conn.execute("UPDATE nevers SET sentence='' WHERE sentence!='' "
                          "AND grp NOT IN (SELECT id FROM asks WHERE text_dropped=0)")

    # -- offers --

    def _history(self) -> list[offers.Past]:
        return [offers.Past(r["shown_t"], r["outcome"], r["answered_t"], r["form"])
                for r in self.conn.execute("SELECT * FROM offers ORDER BY shown_t, id")
                if r["outcome"] != WITHDRAWN]

    def _stopped(self) -> set[str]:
        return offers.stopped_forms([r["form"] for r in self.conn.execute("SELECT form FROM nevers")], self.cfg)

    def resting(self, now: float | None = None) -> str:
        """"Resting offers until 3 Nov" while offers rest, else "": two silent expiries or two Nevers in a
        row rest them for 30 days."""
        now = time.time() if now is None else now
        with self._lock:
            until = offers.resting_until(self._history(), now, self.cfg)
        return offers.rest_line(until) if until else ""

    def ripe_offer(self, now: float | None = None) -> offers.Offer | None:
        """The one offer to show now, or None. An offer already showing stays until it is answered or
        expires; a new one is made only when nothing waits, offers are not resting, the day's and the
        week's allowance is not used, and a counting group is ripe. Showing it records it."""
        now = time.time() if now is None else now
        with self._lock, db.transaction(self.conn):
            self._maintain(now)
            titles = self._titles()
            stopped = self._stopped()
            waiting = self.conn.execute("SELECT * FROM offers WHERE outcome='' ORDER BY shown_t, id").fetchall()
            for r in waiting:
                g = self.group(r["grp"], now)
                rec = forms.recommend(g, self.built, stopped, titles) if g is not None else None
                if rec is not None:
                    return offers.Offer(r["id"], g, rec, r["shown_t"])
                self._close(r["grp"], WITHDRAWN, now, offer_id=r["id"], state="counting")
            if waiting:
                return None
            history = self._history()
            pick = offers.next_offer(self._ripe_groups(history, now), history, now, self.cfg, built=self.built,
                                     stopped=stopped, titles=titles)
            if pick is None:
                return None
            g, rec = pick
            cur = self.conn.execute(
                "INSERT INTO offers(grp, form, label, n, days, existing, shown_t) VALUES(?,?,?,?,?,?,?)",
                (g.id, rec.form.letter, g.label, g.n, len(g.days), int(rec.already), now))
            self.conn.execute("UPDATE asks SET state='offered', form=? WHERE id=?", (rec.form.letter, g.id))
            g.state, g.form = "offered", rec.form.letter
            return offers.Offer(cur.lastrowid, g, rec, now)

    def peek_offer(self, now: float | None = None) -> tuple[Group, forms.Recommendation] | None:
        """The group and form `ripe_offer` would make a new offer of right now, recording nothing: for
        the one optional model call that may look at a group before it is shown. None while an offer
        waits, or when nothing is ripe."""
        now = time.time() if now is None else now
        with self._lock:
            if self.waiting():
                return None
            history = self._history()
            return offers.next_offer(self._ripe_groups(history, now), history, now, self.cfg,
                                     built=self.built, stopped=self._stopped(), titles=self._titles())

    def _ripe_groups(self, history: list[offers.Past], now: float) -> list[Group]:
        """The counting groups `offers.next_offer` could pick, and only when it could pick one: none are
        loaded while offers rest or the allowance is spent (a few rows of offers say so), and a group
        with fewer asks than the bar, or none since it left the list, can never be ripe, so the query
        leaves it out. What an idle poll costs does not grow with every ask they ever made."""
        cfg = self.cfg
        if offers.resting_until(history, now, cfg) or not offers.cadence_ok(history, now, cfg)[0]:
            return []
        return self.groups(("counting",), now, min_n=offers.asks_needed(history, cfg),
                           since=now - habits.LIST_DAYS * offers.DAY - 1)

    def waiting(self) -> int:
        """How many offers are showing and unanswered (at most one)."""
        return self.conn.execute("SELECT COUNT(*) FROM offers WHERE outcome=''").fetchone()[0]

    def answer(self, group_id: str, op: str, form: str | None = None, now: float | None = None) -> dict:
        """Their answer to an offer: `accept` (with the `form` they chose, else the recommended one), `not_now`,
        `never`, `got_it`, or `expired` (what silence becomes). Returns {"ok", "state", "form", "text"}."""
        now = time.time() if now is None else now
        states = {offers.ACCEPT: "made", offers.GOT_IT: "got_it", offers.NOT_NOW: "not_now",
                  offers.EXPIRED: "not_now", offers.NEVER: "said_no"}
        with self._lock, db.transaction(self.conn):
            g = self.group(group_id, now)
            if g is None:
                return {"ok": False, "state": "", "form": "", "text": "That is not on the list any more."}
            if op not in states:
                return {"ok": False, "state": g.state, "form": "", "text": f"Nothing to do for {op!r}."}
            waiting = self.conn.execute("SELECT * FROM offers WHERE grp=? AND outcome='' ORDER BY id DESC LIMIT 1",
                                        (group_id,)).fetchone()
            try:
                letter = forms.get(form).letter if form else (waiting["form"] if waiting else g.form)
            except KeyError:
                return {"ok": False, "state": g.state, "form": "", "text": f"No such way to do it: {form!r}."}
            if op == offers.NEVER:
                self._never(g, letter, now)
            self._close(group_id, op, now, offer_id=waiting["id"] if waiting else None, state=states[op],
                        form=letter if op in (offers.ACCEPT, offers.GOT_IT) else None)
            return {"ok": True, "state": states[op], "form": letter, "text": ""}

    def _close(self, group_id: str, outcome: str, now: float, offer_id: int | None = None,
               state: str | None = None, form: str | None = None) -> None:
        """Record how an offer ended and what state its group goes to: Not now for silence and for a
        "not now", said_no for Never, made for a taken offer."""
        if offer_id is not None:
            self.conn.execute("UPDATE offers SET outcome=?, answered_t=?, chosen=? WHERE id=?",
                              (outcome, now, form or "", offer_id))
        if state is None:
            state = "not_now"
        n = self.conn.execute("SELECT n FROM asks WHERE id=?", (group_id,)).fetchone()
        self.conn.execute("UPDATE asks SET state=?, snooze_n=?, snooze_t=?, form=CASE WHEN ?='' THEN form ELSE ? END "
                          "WHERE id=?", (state, n["n"] if n else 0, now, form or "", form or "", group_id))

    def rename_group(self, group_id: str, label: str) -> bool:
        """Give a group another label (the model's answer when it names one, `refine.py`). Only the
        label changes: members, counts and state stay. False when there is no such group."""
        with self._lock, db.transaction(self.conn):
            row = self.conn.execute("SELECT data FROM asks WHERE id=?", (group_id,)).fetchone()
            if row is None:
                return False
            data = json.loads(row["data"])
            data["label"] = label
            self.conn.execute("UPDATE asks SET label=?, data=? WHERE id=?",
                              (label, json.dumps(data), group_id))
            self._bump()
        return True

    def _never(self, g: Group, letter: str, now: float) -> None:
        """Remember a group they said Never to: what it looked like, not a sentence of their, so it survives
        forgetting and an undo, and later asks like it do not count."""
        things = habits.things_from_launcher(self._apps())
        rows = self.conn.execute("SELECT * FROM requests WHERE grp=? AND counted=1 ORDER BY seq LIMIT 5",
                                 (g.id,)).fetchall()
        sig = habits.signature([_profile_of(r, things, self._apps()) for r in rows if r["text"]])
        self.conn.execute("INSERT INTO nevers(grp, signature, form, t, label, sentence) VALUES(?,?,?,?,?,?)",
                          (g.id, json.dumps(sig), letter, now, g.label, g.sentences[0] if g.sentences else g.label))

    def _never_profiles(self) -> list[Profile]:
        out: list[Profile] = []
        for r in self.conn.execute("SELECT signature FROM nevers"):
            try:
                out += habits.from_signature(json.loads(r["signature"]))
            except ValueError:
                continue
        return out

    def said_no(self) -> list[dict]:
        """What they said Never to, newest first, for the "Said no" list: {"id", "label", "sentence", "form",
        "t"}."""
        return [{"id": r["grp"], "label": r["label"], "sentence": r["sentence"], "form": r["form"], "t": r["t"]}
                for r in self.conn.execute("SELECT * FROM nevers ORDER BY t DESC, id DESC")]

    def bring_back(self, group_id: str, now: float | None = None) -> dict:
        """Put a group back among what is counted: lift its Never, end its Not now, or undo what was
        made. Asks that were held back by the Never count again."""
        now = time.time() if now is None else now
        with self._lock:
            lifted = self.conn.execute("DELETE FROM nevers WHERE grp=?", (group_id,)).rowcount
            row = self.conn.execute("SELECT id FROM asks WHERE id=?", (group_id,)).fetchone()
            if row is not None:
                self.conn.execute("UPDATE asks SET state='counting', snooze_n=0, snooze_t=0 WHERE id=?", (group_id,))
            if lifted:
                self.regroup(now)
        return {"ok": bool(lifted or row), "state": "counting"}

    # -- words --

    def note_word_made(self, phrase: str, now: float | None = None) -> None:
        """A word was made (words.py wrote its row): it counts from now, so one never used can be found."""
        now = time.time() if now is None else now
        with self._lock:
            self.conn.execute("INSERT INTO words_used(phrase, count, last_used, made_t) VALUES(?,0,?,?) "
                              "ON CONFLICT(phrase) DO UPDATE SET made_t=excluded.made_t", (phrase, now, now))

    def note_word_used(self, phrase: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._word_used(phrase, now)

    def _word_used(self, phrase: str, t: float) -> None:
        # Only a use newer than the last counts, so a file read again from the top counts nothing twice.
        self.conn.execute(
            "INSERT INTO words_used(phrase, count, last_used, made_t) VALUES(?,1,?,?) "
            "ON CONFLICT(phrase) DO UPDATE SET count=count+1, last_used=excluded.last_used WHERE excluded.last_used>last_used",
            (phrase, t, t))

    def words_last_used(self) -> dict[str, float]:
        """When each word last opened something (a word never used shows when it was first made): what
        `words.words_unused` takes."""
        rows = self.conn.execute("SELECT phrase, last_used FROM words_used")
        return {r["phrase"]: r["last_used"] for r in rows}

    def words_unused(self, days: float = 28, now: float | None = None) -> list[str]:
        """The words that have not been used for `days` (28): time to put them away."""
        now = time.time() if now is None else now
        rows = self.conn.execute("SELECT phrase FROM words_used WHERE last_used<? ORDER BY last_used, phrase",
                                 (now - days * offers.DAY,))
        return [r["phrase"] for r in rows]

    # -- saying what was counted --

    def asks_report(self, limit: int = 20, now: float | None = None, singles: bool = False) -> list[dict]:
        """What they ask most, for `bombadil loop asks` and the Noticed window: each group asked more than
        once and still on the list (asked in the last 30 days), heaviest first, with its label, how many
        times on how many days, the last time, three of their own sentences and what it became."""
        now = time.time() if now is None else now
        out = []
        for g in self.groups(now=now, listed_only=True):
            if g.n < 2 and not singles:
                continue
            out.append({"id": g.id, "label": g.label, "n": g.n, "days": len(g.days), "first": g.first,
                        "last": g.last, "sentences": g.sentences[:3], "became": _became(g), "state": g.state,
                        "weight": round(g.weight, 3), "verb": g.verb, "friction": g.friction})
            if len(out) >= limit:
                break
        return out

    def asks_text(self, limit: int = 20, now: float | None = None) -> list[str]:
        """The same, as the lines `bombadil loop asks` prints."""
        now = time.time() if now is None else now
        lines = []
        for r in self.asks_report(limit, now):
            when = time.strftime("%a %H:%M", time.localtime(r["last"]))
            lines.append(f"{r['n']} times on {r['days']} day{'s' if r['days'] != 1 else ''}  ·  last {when}  ·  "
                         f"{r['label']}" + (f"  ·  {r['became']}" if r["became"] else ""))
            lines += [f"    “{s}”" for s in r["sentences"]]
        return lines or ["Nothing asked more than once yet."]

    def status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        q = self.conn.execute
        return {"requests": q("SELECT COUNT(*) FROM requests").fetchone()[0],
                "counted": q("SELECT COUNT(*) FROM requests WHERE counted=1").fetchone()[0],
                "groups": q("SELECT COUNT(*) FROM asks").fetchone()[0],
                "waiting": self.waiting(), "resting": self.resting(now),
                "offset": int(self._meta("offset", 0))}

    # -- replaying a real ledger --

    @staticmethod
    def replay(turns_jsonl: Path | str, now: float | None = None, corpus_dir: Path | str | None = None,
               app_list: list | None = None, app_names: Iterable[str] = (), config: offers.Config | None = None,
               pairs: int = 100) -> dict:
        """Run the whole pipeline over a copy of a real turns.jsonl, in memory, day by day, and report what
        it made of it: how many rows counted and why the rest did not, the groups, the offers it would have
        shown if they never answered, and pairs for hand-labelling (the brief wants 90% precision on "same
        request" before anyone trusts it). Touches nothing on disk. `corpus_dir` is where the per-turn
        logs are if they were copied elsewhere; `app_names` names the apps they have, when this is not their
        machine."""
        if app_list is None and app_names:
            app_list = [apps_mod.App(_slug(n), Path("."), str(n)) for n in app_names]
        store = LoopStore(MEMORY, app_list=app_list, config=config or offers.Config(),
                          logs_dir=Path(corpus_dir) if corpus_dir else None)
        try:
            return store._replay(Path(turns_jsonl), now, pairs)
        finally:
            store.close()

    def _replay(self, path: Path, now: float | None, pairs: int) -> dict:
        rows = [row for row, _ in ledger_mod.read_rows(path, 0).rows]
        model = [r for r in rows if r.get("kind") is None]
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            by_day.setdefault(ledger_mod.day_of(r.get("started", r["t"]) if r.get("kind") is None else r["t"]), []).append(r)
        shown = []
        last = 0.0
        for day in sorted(by_day):
            batch = by_day[day]
            last = max(last, *(r["t"] for r in batch))
            self._apply(batch, last)
            offer = self.ripe_offer(last)
            if offer is not None and not any(s["id"] == offer.group.id for s in shown):
                shown.append({"day": day, "id": offer.group.id, "label": offer.group.label, "n": offer.group.n,
                              "form": offer.rec.form.letter, "what": offer.rec.sentence,
                              "already": offer.rec.already})
        end = last if now is None else now
        reasons = Counter(r["reason"] for r in self.conn.execute("SELECT reason FROM requests"))
        groups = [{"id": g.id, "label": g.label, "n": g.n, "days": len(g.days), "first": g.first, "last": g.last,
                   "verb": g.verb, "routes": g.routes[:4], "near_miss": g.near_miss, "existing": g.existing,
                   "friction": g.friction, "sentences": g.sentences, "weight": round(g.weight, 3)}
                  for g in self.groups(now=end) if g.n >= 2]
        return {"rows": len(rows), "turns": len(model), "counted": reasons.get("", 0),
                "reasons": {(k or "counted"): v for k, v in sorted(reasons.items())},
                "groups": groups, "singles": self.conn.execute("SELECT COUNT(*) FROM asks WHERE n=1").fetchone()[0],
                "offers": shown, "resting": self.resting(end), "pairs": self._label_sheet(pairs)}

    def _label_sheet(self, limit: int) -> list[dict]:
        """Pairs of their own asks for hand-labelling: neighbours inside groups the loop made (were they the
        same request?) and the closest pairs it kept apart (should they have been?)."""
        app_list = self._apps()
        things = habits.things_from_launcher(app_list)
        rows = self.conn.execute("SELECT * FROM requests WHERE counted=1 AND text!='' ORDER BY seq").fetchall()
        profiles = {r["id"]: _profile_of(r, things, app_list) for r in rows}
        grp = {r["id"]: r["grp"] for r in rows}
        inside: list[dict] = []
        by_group: dict[str, list[str]] = {}
        for rid, gid in grp.items():
            by_group.setdefault(gid, []).append(rid)
        for gid, ids in by_group.items():
            for a, b in pairwise(ids):
                inside.append(_pair(profiles[a], profiles[b], True))
        apart: list[dict] = []
        ids = list(profiles)
        for i, a in enumerate(ids):
            for b in ids[i + 1:i + 60]:
                if grp[a] != grp[b]:
                    s = habits.score(profiles[a], profiles[b])
                    if s >= 0.25:
                        apart.append(_pair(profiles[a], profiles[b], False, s))
        apart.sort(key=lambda p: -p["score"])
        half = max(limit // 2, 1)
        return inside[:limit - min(len(apart), half)] + apart[:half]


class _abandon_on_error:
    """If a write fails and rolls back, what the store holds in memory is ahead of the file: drop it."""

    def __init__(self, store: "LoopStore"):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        if kind is not None:
            self.store._grouper = None
        return False


def _slug(name: str) -> str:
    try:
        return apps_mod.slug(str(name))
    except ValueError:
        return str(name).lower().replace(" ", "-")


def _pair(a: Profile, b: Profile, same: bool, score: float | None = None) -> dict:
    return {"a": a.text, "b": b.text, "same": same, "score": round(habits.score(a, b) if score is None else score, 3)}


def _became(g: Group) -> str:
    if g.state in ("made", "offered") and g.form:
        f = forms.get(g.form)
        return ("made " if g.state == "made" else "offered: ") + f.name.lower()
    return {"got_it": "already had a word", "said_no": "said no", "not_now": "not now"}.get(g.state, "")


def _wants_route(req: Request) -> bool:
    return (req.origin == "typed" and bool(req.text.strip()) and not req.text.lstrip().startswith("!")
            and not req.signin)


def _mark(req: Request, eff: tuple) -> None:
    action, _of, _snap, t = eff
    if action == "undo" and req.undone_at is None:
        req.undone_at = t
    elif action == "stop" and req.stopped_at is None:
        req.stopped_at = t


def _request_of(row) -> Request:
    return Request(
        id=row["id"], t=row["t"], day=row["day"], text=row["text"], origin=row["origin"],
        ok=None if row["ok"] is None else bool(row["ok"]), stopped=bool(row["stopped"]), seconds=row["seconds"],
        steps=row["steps"], provider=row["provider"], session=row["session"], snapshot=row["snapshot"],
        details=row["details"], model=row["model"], cost=row["cost"], ended=row["ended"],
        changed=bool(row["changed"]), signin=bool(row["signin"]), undone_at=row["undone_at"],
        stopped_at=row["stopped_at"])


def _profile_of(row, things: dict, app_list: list) -> Profile:
    """A stored request as the grouping sees it. Its verb and named things are the ones decided when it
    was asked, so an app made since does not change what it was."""
    p = habits.make_profile(_request_of(row), json.loads(row["route"]), things, app_list)
    return dataclasses.replace(p, named=frozenset(json.loads(row["named"])), verb=row["verb"] or p.verb,
                               near_miss=bool(row["near_miss"]), style=bool(row["style"]), open_target=row["opens"])


replay = LoopStore.replay

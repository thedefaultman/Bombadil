"""LoopService: what agentd runs beside its turns to turn the counts and the findings into "noticed".

It keeps the `noticed` state the bar's chip reads, answers his taps (`noticed_do`), makes the words,
starts the turn that makes an app, and keeps the trail (`improve` rows in turns.jsonl). It is a guest
in agentd: nothing it does is on a turn's path, and one broken step costs one line on stderr, never a
turn, the pill or a client.

One worker thread owns the `LoopStore` and `FindingsStore` connections (an SQLite connection belongs to
the thread that made it), and every database touch goes through it with `run_in_executor`: the event
loop never waits on SQLite. The hooks agentd calls (`on_row`, `on_client`, `on_message`) only schedule
work. Slow things that are not the database (the network, opening a page, moving an app's folder) run
in other threads, so a tap is never stuck behind them.

The trail is written only through agentd's own ledger writer (`_log_line`), so turns.jsonl keeps one
writer and stays append only. Nothing is sent anywhere from here until he presses Send and then Submit
on a page that shows what goes.
"""

import asyncio
import contextlib
import functools
import json
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .. import apps as apps_mod
from .. import launcher, paths
from . import db, forms, offers, refine, report, words
from . import findings as findings_mod
from .findings import FindingsStore
from .habits import sentence_of
from .store import LoopStore

DEBOUNCE = 2.0           # after a finished row: a burst of rows is one look at turns.jsonl
INGEST_EVERY = 600.0     # and a look at least this often
FINDINGS_EVERY = 20.0    # the prober is another process: read its findings this often while a client is here
SWEEP_EVERY = 86400.0    # a word unused for 28 days is put away once a day
TICK = 5.0
WORD_DAYS = 28
REFINE_TIMEOUT = 30.0    # the optional model call: it holds the worker, so it is kept short
MAX_ROWS = 3             # rows on the card
TRAIL_KEPT = 500
LATELY_DAYS = 7
MAX_PENDING_APPS = 5

# The service's own little table in loop.db. Only `hidden` and when words were last swept.
SCHEMA = ["CREATE TABLE service_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"]

# What the primary button says for the forms that can be made today; the rest keep offers.BUTTONS.
BUTTONS = {"A": "Make the word", "D": "Make an app"}
UNDOABLE = ("remove_word", "trash_app", "bring_back_word")

NOT_SAVED = "Noticed cannot look at its notes right now, so nothing was changed."


@dataclass
class Result:
    """What a tap answers: `ok`, one plain sentence, and a preview (text, or {"text", "goes", "stays"})."""
    ok: bool
    text: str
    preview: object = None


@dataclass
class _Snap:
    """What the worker last read: the parts of `noticed` that come from loop.db."""
    hidden: bool = False
    resting: str = ""
    offer: dict | None = None
    found: list[dict] = field(default_factory=list)       # findings nobody has reported yet
    reports: list[dict] = field(default_factory=list)     # held, ready to send


def _retry(fn: Callable):
    """The prober makes its tables at the same moment in another process: a table that "already exists"
    is that race, so look again."""
    for attempt in range(4):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e) or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def _clip(text, n: int = 30) -> str:
    return "".join(c for c in str(text) if c.isprintable())[:n]


def _times(n: int, days: int) -> str:
    return f"{n} time{'' if n == 1 else 's'} on {days} day{'' if days == 1 else 's'}"


def _t(row: dict) -> float:
    t = row.get("t")
    return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else 0.0


def _local_line(text: str, ok: bool = True, undo_msg: dict | None = None, target: str = "") -> dict:
    """A line above the pill, as agentd says one, for the client that tapped. `undo_msg` is what the
    line's Undo sends in place of the machine's undo."""
    msg = {"type": "event", "kind": "local", "turn": None, "action": "noticed", "target": target,
           "phase": "done", "ok": ok, "text": text}
    if undo_msg is not None:
        msg["undo_msg"] = undo_msg
    return msg


def _app_prompt(ask: dict) -> str:
    """The ordinary request an "app" offer sends: his own words, how often he asked, and what to make.
    It is his turn to read and undo like any other (origin "loop", so it is never counted as an ask)."""
    said = ", ".join(f"“{sentence_of(s, 120)}”" for s in ask["sentences"][:3])
    return (f"I keep asking for this: {said}. I have asked {_times(ask['n'], ask['days'])}. "
            "Please make me a small Bombadil app for it, using the app kit, so I can do it in one place "
            "from now on. When you are done, say in one sentence what you made.")


class LoopService:
    def __init__(self, agentd, *, loop_dir: Path | str | None = None, clock: Callable[[], float] = time.time,
                 opener: Callable[[str], str] | None = None, fetcher: Callable | None = None):
        """`agentd` is the daemon this runs beside. `clock` gives epoch seconds for every rule here (the
        cadence is tested with an injected one). `opener(url)` opens the issue page (what
        `report.open_issue_page` does by default) and `fetcher(url, timeout)` searches the project's
        issues (`report.already_reported`); tests pass their own, so nothing touches the network."""
        self.agentd = agentd
        self.clock = clock
        self.opener = opener
        self.fetcher = fetcher
        self.debounce = DEBOUNCE
        self._dir = Path(loop_dir) if loop_dir is not None else None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._store: LoopStore | None = None             # worker thread only
        self._findings: FindingsStore | None = None      # worker thread only
        self._snap = _Snap()
        self._trail: list[dict] = []                     # improve rows, event loop only
        self._trail_ids: set[str] = set()
        self._trail_read = False
        self._app_turns: list[dict] = []                 # app turns started by a tap, waiting for their row
        self._tasks: set[asyncio.Task] = set()
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._ingested = self._polled = self._swept = 0.0
        self._failed_at: float | None = None
        self._sending: set[str] = set()
        self._accepting: set[str] = set()
        self._said: set[str] = set()
        self._seq = 0
        self._stopped = False
        self._ops = {
            "open": self._op_open, "accept": self._op_accept, "other_ways": self._op_other_ways,
            "preview": self._op_preview, "report": self._op_report, "send": self._op_send,
            "undo": self._op_undo, "bring_back": self._op_bring_back, "forget_asks": self._op_forget_asks,
            "clear_found": self._op_clear_found, "hide": self._op_hide, "show": self._op_show,
            "not_now": functools.partial(self._op_answer, "not_now"),
            "never": functools.partial(self._op_answer, "never"),
            "got_it": functools.partial(self._op_answer, "got_it"),
        }
        self._last_json = json.dumps(self._state(), sort_keys=True)

    # -- the plumbing --

    @property
    def dir(self) -> Path:
        return self._dir if self._dir is not None else paths.loop_dir()

    def start(self) -> None:
        """agentd is listening. Open the stores, catch up with turns.jsonl and start the clock; all of
        it beside the event loop. Nothing here raises."""
        if self._pool is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bombadil-loop")
        self._spawn(self._boot())
        self._spawn(self._ticker())

    def stop(self) -> None:
        self._stopped = True
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(RuntimeError):   # the loop is closed already: the task went with it
                task.cancel()
        pool, self._pool = self._pool, None
        if pool is not None:
            with contextlib.suppress(RuntimeError):   # (a pool that was shut down already)
                pool.submit(self._w_close)
            pool.shutdown(wait=False)

    def _say(self, where: str, exc: Exception) -> None:
        """One line on stderr per distinct trouble. Never his words: only what kind of thing broke."""
        detail = ""
        if isinstance(exc, sqlite3.Error):
            detail = f": {exc}"
        elif isinstance(exc, OSError) and exc.strerror:
            detail = f": {exc.strerror}"
        self._note(f"{where}: {type(exc).__name__}{detail}")

    def _note(self, text: str) -> None:
        line = f"loop: {text}"
        if line not in self._said and len(self._said) < 50:
            self._said.add(line)
            print(line, file=sys.stderr)

    def _spawn(self, coro) -> asyncio.Task:
        task = (self._loop or asyncio.get_running_loop()).create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._say("task", task.exception())

    def _later(self, fn: Callable, *args) -> None:
        """Run `fn` on the event loop soon, from whatever thread this is called on."""
        loop = self._loop
        if loop is None or self._stopped:
            return

        def run():
            try:
                out = fn(*args)
                if asyncio.iscoroutine(out):
                    self._spawn(out)
            except Exception as e:  # noqa: BLE001 - a hook never costs agentd anything
                self._say("hook", e)
        try:
            loop.call_soon_threadsafe(run)
        except RuntimeError:
            pass   # the loop is closed: agentd is going away

    async def _work(self, fn: Callable, *args, default=None, where: str = ""):
        """Run `fn` on the worker thread, the only place the databases are touched. A failing step costs
        its one line and gives `default`."""
        pool = self._pool
        if pool is None or self._stopped:
            return default
        if self._store is None and fn != self._w_open:
            return default   # the loop has no notes to read (that was said once, when it started)
        try:
            return await asyncio.get_running_loop().run_in_executor(pool, functools.partial(fn, *args))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._say(where or getattr(fn, "__name__", "step").removeprefix("_w_"), e)
            return default

    # -- worker thread: everything below `_w_` runs there and nowhere else --

    def _w_open(self) -> bool:
        if self._store is not None and self._findings is not None:
            return True
        path = self.dir / "loop.db"
        store = LoopStore(path)
        try:
            _retry(lambda: db.schema(store.conn, "service", SCHEMA))
            found = _retry(lambda: FindingsStore(db.connect(path)))
        except BaseException:
            store.close()
            raise
        self._store, self._findings = store, found
        return True

    def _w_close(self) -> None:
        for closer in (self._findings, self._store):
            if closer is not None:
                with contextlib.suppress(Exception):   # going away anyway
                    closer.close()
        self._store = self._findings = None

    def _flag(self, key: str, default: str = "") -> str:
        row = self._store.conn.execute("SELECT value FROM service_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def _set_flag(self, key: str, value) -> None:
        self._store.conn.execute("INSERT INTO service_state(key, value) VALUES(?, ?) "
                                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def _w_hidden(self) -> tuple[bool, float]:
        return self._flag("hidden") == "1", float(self._flag("swept", "0") or 0)

    def _w_ingest(self, now: float) -> None:
        self._store.ingest(now=now)

    def _w_note_word(self, phrase: str, t: float) -> None:
        self._store.note_word_used(phrase, now=t)

    def _w_collect(self, now: float, may_offer: Callable[[], bool]) -> _Snap:
        """The state, from loop.db. A new offer is fetched (and so recorded as shown) only when
        `may_offer()` is true at this very moment; one already showing is read again whatever he does."""
        store, finds = self._store, self._findings
        snap = _Snap(hidden=self._flag("hidden") == "1")
        if snap.hidden:
            return snap   # counting and checking go on; nothing is offered while hidden
        snap.resting = store.resting(now)
        waiting = store.waiting() > 0
        if waiting or may_offer():
            if not waiting:
                # The optional model call belongs to the moment an offer could be made, so it never
                # runs while a turn does (it would take that turn's share of the account).
                try:
                    self._w_refine(now)
                except Exception as e:  # noqa: BLE001 - the optional step never costs the state
                    self._say("refine", e)
            offer = store.ripe_offer(now)
            if offer is not None:
                snap.offer = self._offer_row(offer)
        for f in finds.open_findings():
            row = self._found_row(f)
            (snap.reports if f.state == "reported" else snap.found).append(row)
        return snap

    def _offer_row(self, offer: offers.Offer) -> dict:
        row = offer.to_row(self._store._titles())
        rec = offer.rec
        label = "Got it" if rec.op == "got_it" else BUTTONS.get(rec.form.letter, row["primary"]["label"])
        row["primary"] = {**row["primary"], "label": label}
        # Not now and Never are the bar's own quiet buttons: "others" are the other ways to do it.
        row["others"] = [{"label": BUTTONS.get(f.letter, offers.BUTTONS.get(f.letter, "Make it")),
                          "op": "accept", "form": f.id} for f in rec.others[:2]]
        return row

    @staticmethod
    def _found_row(f: findings_mod.Finding) -> dict:
        held = f.state == "reported"
        return {"id": f.fp, "kind": "report" if held else "found", "title": f.title,
                "meta": _times(f.n, f.days),
                "what": ("It is held on this computer. Nothing is sent until you press Submit." if held
                         else "Bombadil cannot fix this here. You can send it to the project."),
                "fp": f.fp,
                "primary": {"label": "See the report" if held else "Send to the project", "op": "report"},
                "others": [], "forms": []}

    def _w_refine(self, now: float) -> None:
        """The one optional model call, for the group that is about to become an offer. Off unless his
        config turns it on; it only renames a group or holds one back, never adds to it."""
        if not refine.enabled():
            return
        store = self._store
        pick = store.peek_offer(now)
        if pick is None:
            return
        group, _ = pick
        members = [(m["id"], m["text"]) for m in reversed(store.members(group.id))
                   if m["counted"] and m["text"]]
        allowed = [f.id for f in forms.FORMS if f.offered and forms.available(f.letter, store.built)[0]]
        provider = getattr(getattr(self.agentd, "provider", None), "name", None)
        answer = refine.ask(members, allowed_forms=allowed, away=self._away(now), provider=provider, now=now,
                            timeout=REFINE_TIMEOUT)
        if answer is None:
            return
        if len(answer.same_ids) < store.cfg.asks:
            store.answer(group.id, offers.NOT_NOW, now=now)   # not really one request: nothing to offer
        elif answer.label and answer.label != group.label:
            store.rename_group(group.id, answer.label)

    @staticmethod
    def _away(now: float) -> bool:
        try:
            from . import runner
            return runner.presence.away(now)
        except Exception:  # noqa: BLE001 - not knowing means he is here
            return False

    def _w_full(self, now: float) -> dict:
        """What the Noticed window lists, from loop.db (the changes come from the trail)."""
        store, finds = self._store, self._findings
        titles = store._titles()
        asks = [{"id": r["id"], "title": sentence_of(r["sentences"][0]) if r["sentences"] else r["label"],
                 "n": r["n"], "days": r["days"], "last": r["last"], "state": r["state"],
                 "sentences": r["sentences"], "became": r["became"], **self._w_ask_buttons(r, now)}
                for r in store.asks_report(20, now)]
        said_no = []
        for r in store.said_no():
            try:
                form = forms.get(r["form"]).name
            except KeyError:
                form = ""
            said_no.append({"id": r["id"], "title": r["sentence"] or r["label"], "t": r["t"], "form": form})
        listed = [{"phrase": w.phrase, "away": w.away,
                   "opens": forms.thing_title(f"{w.kind}:{w.name}", titles)} for w in words.load()]
        found = []
        for f in finds.all(("open", "reported", "sent")):
            entry = {"id": f.fp, "title": f.title, "meta": _times(f.n, f.days), "fp": f.fp, "state": f.state,
                     "can_send": f.state in ("open", "reported"),   # the window's button says report first
                     "why": [t for t in (f"Expected: {f.expected}" if f.expected else "",
                                         f"Seen: {f.observed}" if f.observed else "") if t]}
            if f.state == "reported":
                entry["preview"] = report.preview(report.build(f)).to_dict()
            found.append(entry)
        return {"hidden": self._flag("hidden") == "1", "resting": store.resting(now), "asks": asks,
                "found": found, "said_no": said_no, "words": listed}

    def _w_ask_buttons(self, r: dict, now: float) -> dict:
        """For a group that is waiting on him: what the button says, what it would do, the other ways."""
        if r["state"] != "offered":
            return {}
        store = self._store
        g = store.group(r["id"], now)
        rec = forms.recommend(g, store.built, titles=store._titles()) if g is not None else None
        if rec is None:
            return {}
        label = "Got it" if rec.op == "got_it" else BUTTONS.get(rec.form.letter, "Make it")
        return {"what": rec.sentence, "primary": {"label": label, "op": rec.op, "form": rec.form.id},
                "others": [{"label": BUTTONS.get(f.letter, offers.BUTTONS.get(f.letter, "Make it")),
                            "op": "accept", "form": f.id} for f in rec.others[:2]]}

    def _w_sweep(self, now: float) -> list[str]:
        """Put away the words nobody said for 28 days. The phrases that went."""
        store = self._store
        self._set_flag("swept", now)
        away = []
        for phrase in words.words_unused(store.words_last_used(), WORD_DAYS, now):
            if words.put_away(phrase) is not None:
                away.append(phrase)
        return away

    def _w_group(self, rid: str, now: float):
        return self._store.group(rid, now)

    def _w_answer(self, rid: str, op: str, form: str | None, now: float) -> dict:
        return self._store.answer(rid, op, form, now=now)

    # -- the state --

    def _bar_connected(self) -> bool:
        signals = getattr(self.agentd, "signals", None)
        said = getattr(signals, "connected", None)   # a later Signals may say so itself
        return bool(getattr(signals, "_connected", False) if said is None else said)

    def _idle(self) -> bool:
        """No turn runs, nothing is queued, and a bar is there to show it: the only time an offer is
        fetched, and so recorded as shown."""
        a = self.agentd
        running = getattr(a, "current", None) is not None or getattr(a, "pending", None)
        return not running and self._bar_connected()

    def _state(self) -> dict:
        s = self._snap
        if s.hidden:
            return {"type": "noticed", "count": 0, "hidden": True, "resting": "", "lately": "", "rows": []}
        rows = ([s.offer] if s.offer else []) + s.found + s.reports
        return {"type": "noticed", "count": len(rows), "hidden": False, "resting": s.resting,
                "lately": self._lately(self.clock()), "rows": rows[:MAX_ROWS]}

    def _lately(self, now: float) -> str:
        """"2 changes this week", from the trail: what stands, not what was undone."""
        week = now - LATELY_DAYS * 86400
        n = sum(1 for c in self._changes() if not c["undone"] and c["t"] >= week)
        return f"{n} change{'' if n == 1 else 's'} this week" if n else ""

    async def _publish(self, to=None) -> None:
        """Tell every client when `noticed` is not what they last heard; tell `to` whatever it was."""
        try:
            msg = self._state()
            blob = json.dumps(msg, sort_keys=True)
        except Exception as e:  # noqa: BLE001
            self._say("state", e)
            return
        changed = blob != self._last_json
        self._last_json = blob
        if changed:
            await self.agentd.broadcast(msg)
        elif to is not None:
            await self.agentd._send(to, msg)

    async def _refresh(self, to=None) -> None:
        """Read the state again from loop.db and publish it."""
        now = self.clock()
        self._polled = now
        snap = await self._work(self._w_collect, now, self._idle, where="state")
        if snap is not None:
            self._snap = snap
        await self._publish(to)

    async def _ingest_now(self) -> None:
        now = self.clock()
        self._ingested = now
        await self._work(self._w_ingest, now, where="count")
        await self._refresh()

    async def _boot(self) -> None:
        """Open the stores, read the trail and catch up: what turns.jsonl gained while agentd was not here."""
        opened = await self._work(self._w_open, default=False,
                                  where="loop.db cannot be used, so nothing is counted until it can be")
        self._spawn(self._read_trail())   # the trail is a file: it needs no database
        if not opened:
            self._failed_at = self.clock()
            return
        self._failed_at = None
        _, self._swept = await self._work(self._w_hidden, default=(False, 0.0), where="settings")
        await self._ingest_now()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._say("tick", e)

    @staticmethod
    def _due(last: float, every: float, now: float) -> bool:
        return now - last >= every or last - now > every   # the clock may have been set back

    async def tick(self) -> None:
        """What is due at `clock()`: a look at turns.jsonl every 10 minutes, the findings every 20 seconds
        while a client is connected, the words once a day. The service's own timer calls this every few
        seconds; tests call it with their clock moved."""
        now = self.clock()
        if self._store is None:
            if self._failed_at is not None and self._due(self._failed_at, INGEST_EVERY, now):
                await self._boot()
            return
        if self._due(self._ingested, INGEST_EVERY, now):
            await self._ingest_now()
        elif getattr(self.agentd, "clients", None) and self._due(self._polled, FINDINGS_EVERY, now):
            await self._refresh()
        if self._due(self._swept, SWEEP_EVERY, now):
            await self._sweep()

    async def _sweep(self) -> None:
        now = self.clock()
        self._swept = now
        away = await self._work(self._w_sweep, now, default=[], where="words")
        for phrase in away:
            self._improve("word", f"Put the word “{phrase}” away: it was not used for {WORD_DAYS} days.",
                          undo={"op": "bring_back_word", "phrase": phrase})
        if away:
            await self._refresh()

    # -- what agentd tells us --

    def on_row(self, entry: dict) -> None:
        """A row was written to turns.jsonl. Read it, after a moment; note a word's use at once."""
        if not isinstance(entry, dict):
            return
        kind = entry.get("kind")
        if kind == "improve":
            # Our own trail row, on its way back through agentd's writer. Whoever wrote it publishes the
            # new state once it is done (a tap ends with a fresh `noticed`), so nothing is said here: a
            # state sent between the row and the rest of the change would show a half-made one.
            self._trail_add(entry)
            return
        if kind == "local":
            word = entry.get("word")
            if entry.get("via") == "word" and isinstance(word, str) and word:
                t = entry.get("t")
                self._later(self._note_word, word, float(t) if isinstance(t, (int, float)) else self.clock())
        elif kind is None:
            self._later(self._app_row, entry)
        self._later(self._arm)

    async def on_client(self, writer) -> None:
        await self.agentd._send(writer, self._state())

    async def on_message(self, msg: dict, writer) -> None:
        kind = msg.get("type")
        if kind == "noticed_do":
            await self._do(msg, writer)
        elif kind == "noticed_state":
            await self._refresh(to=writer)
        elif kind == "noticed_list":
            await self._list(writer)

    def _arm(self) -> None:
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        self._debounce_handle = self._loop.call_later(self.debounce, self._fire)

    def _fire(self) -> None:
        self._debounce_handle = None
        self._spawn(self._ingest_now())

    async def _note_word(self, phrase: str, t: float) -> None:
        await self._work(self._w_note_word, phrase, t, where="word")

    # -- the trail: improve rows --

    async def _read_trail(self) -> None:
        """The improve rows already in turns.jsonl (from before agentd started). Reading a file is not
        the database, but it is slow for a big one: off the event loop."""
        if self._trail_read:
            return
        self._trail_read = True
        for row in await asyncio.to_thread(_scan_trail, paths.turns_log()):
            self._trail_add(row)
        await self._publish()

    def _trail_add(self, row: dict) -> bool:
        rid = row.get("id") if isinstance(row, dict) else None
        if row.get("kind") != "improve" or not isinstance(rid, str) or not rid or rid in self._trail_ids:
            return False
        self._trail.append(row)
        self._trail_ids.add(rid)
        if len(self._trail) > TRAIL_KEPT:
            gone = self._trail.pop(0)
            self._trail_ids.discard(gone.get("id"))
        return True

    def _improve(self, what: str, title: str, *, group: str | None = None, undo: dict | None = None,
                 of: str | None = None, undone: bool = False, **more) -> dict:
        """Write one trail row, through agentd's own writer: the one thing that appends to turns.jsonl."""
        now = self.clock()
        self._seq += 1
        row = {"t": round(now, 3), "kind": "improve", "id": f"i{int(now * 1000)}-{self._seq}", "what": what,
               "title": title, "group": group, "undo": undo, "undone": undone, "v": 2}
        if of:
            row["of"] = of
        row.update(more)
        try:
            self.agentd._log_line(row)
        except Exception as e:  # noqa: BLE001 - the change stands; the trail keeps it for this run
            self._say("trail", e)
        self._trail_add(row)
        return row

    def _changes(self) -> list[dict]:
        """The window's "Changed itself" list, newest first: each change once, with whether it is undone
        (the latest row that answers it says) and whether Undo can still take it back."""
        latest: dict[str, dict] = {}
        newest_first = self._newest_first()
        for row in newest_first:
            if row.get("of"):
                latest.setdefault(row["of"], row)
        out = []
        for row in newest_first:
            if row.get("of"):
                continue
            answer = latest.get(row["id"])
            undone = bool(answer and answer.get("undone"))
            undo = row.get("undo")
            out.append({"id": row["id"], "title": str(row.get("title") or ""), "t": _t(row),
                        "what": str(row.get("what") or ""), "undone": undone,
                        "can_undo": not undone and isinstance(undo, dict) and undo.get("op") in UNDOABLE})
        return out

    def _newest_first(self) -> list[dict]:
        """The trail, latest row first. Rows written in the same instant (a clock that does not move
        between an undo and a bring back) keep the order they were written in, latest first: the place in
        the trail breaks a tie, so it is not left to chance."""
        ordered = sorted(enumerate(self._trail), key=lambda pair: (_t(pair[1]), pair[0]), reverse=True)
        return [row for _, row in ordered]

    def _row_of(self, rid: str) -> dict | None:
        return next((r for r in self._trail if r["id"] == rid and not r.get("of")), None)

    def _undone(self, rid: str) -> bool:
        return any(c["id"] == rid and c["undone"] for c in self._changes())

    # -- the window's list --

    async def _list(self, writer) -> None:
        now = self.clock()
        data = await self._work(self._w_full, now, default=None, where="list") or {
            "hidden": False, "resting": "", "asks": [], "found": [], "said_no": [], "words": []}
        msg = {"type": "noticed_full", "hidden": data["hidden"],
               "held": bool(data["hidden"] or data["resting"]), "resting": data["resting"],
               "asks": data["asks"], "changes": self._changes(), "found": data["found"],
               "said_no": data["said_no"], "words": data["words"]}
        await self.agentd._send(writer, msg)

    # -- the word "noticed" --

    async def noticed_word(self, verb: str) -> tuple[bool, str]:
        """"noticed", "open noticed", "show noticed" and "hide noticed", from the launcher: agentd hands
        the action here. Returns the line for the pill."""
        if verb == "hide":
            if not await self._set_hidden(True):
                return False, NOT_SAVED
            return True, "Noticed is hidden. Say “show noticed” to bring it back."
        await self._set_hidden(False)
        await self.agentd.broadcast({"type": "noticed_open"})
        await self._open_window()
        return True, "Opened Noticed."

    async def _set_hidden(self, hidden: bool) -> bool:
        ok = await self._work(self._set_hidden_w, hidden, default=False, where="hide")
        await self._refresh()
        return bool(ok)

    def _set_hidden_w(self, hidden: bool) -> bool:
        self._set_flag("hidden", "1" if hidden else "0")
        return True

    async def _open_window(self) -> bool:
        """Open the Noticed window, when that app is on this machine; quietly not when it is not."""
        lx = getattr(self.agentd, "launcher", None)
        if lx is None:
            return False
        try:
            found = await asyncio.to_thread(launcher.known_apps)
            app = next((a for a in found if a.name == launcher.NOTICED), None)
            if app is None:
                return False
            ok, _ = await asyncio.to_thread(lx.run, launcher.Action("app", app.name, "open", str(app.title)))
            return bool(ok)
        except Exception as e:  # noqa: BLE001 - a window that will not open is not a failed tap
            self._say("window", e)
            return False

    # -- his taps --

    async def _do(self, msg: dict, writer) -> None:
        op = msg.get("op") if isinstance(msg.get("op"), str) else ""
        rid = "" if msg.get("id") is None else str(msg.get("id"))
        form = msg.get("form") if isinstance(msg.get("form"), str) else None
        handler = self._ops.get(op)
        try:
            if handler is None:
                res = Result(False, f"Noticed cannot do “{_clip(op)}”." if op
                             else "Noticed did not hear what to do.")
            elif self._store is None and op != "open":
                res = Result(False, NOT_SAVED)
            else:
                res = await handler(rid, form, writer)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one bad tap costs that tap
            self._say(op or "tap", e)
            res = Result(False, "That did not work.")
        answer = {"type": "noticed_result", "op": op, "id": rid, "ok": res.ok, "text": res.text}
        if res.preview is not None:
            answer["preview"] = res.preview
        await self.agentd._send(writer, answer)
        await self._refresh(to=writer)

    async def _op_open(self, rid, form, writer) -> Result:
        opened = await self._open_window()
        return Result(True, "Opened the Noticed window." if opened else "")

    async def _op_other_ways(self, rid, form, writer) -> Result:
        return Result(True, "Those are the other ways it could be done.")

    async def _op_answer(self, op: str, rid: str, form, writer) -> Result:
        """Not now, Never, Got it: for an offer, or for something it found."""
        said = {"not_now": "Okay. That will not come up again for a while.",
                "never": "Okay. That will not be offered again.", "got_it": "Okay, nothing to make."}[op]
        if await self._work(self._w_finding, rid, default=None, where=op) is not None:
            await self._work(self._w_mark, rid, "never" if op == "never" else "dismissed", where=op)
            return Result(True, said)
        res = await self._work(self._w_answer, rid, op, None, self.clock(), default=None, where=op)
        if res is None:
            return Result(False, "That did not work.")
        return Result(bool(res["ok"]), said if res["ok"] else res["text"])

    def _w_finding(self, rid: str):
        return self._findings.get(rid) if ":" in rid else None

    def _w_mark(self, fp: str, state: str):
        return self._findings.mark(fp, state)

    # accept: a word, or an app

    def _w_plan(self, rid: str, form: str | None, now: float) -> tuple[dict | None, str, str]:
        """The group a tap is about and the form to make, or why not. (ask, letter, problem)"""
        store = self._store
        g = store.group(rid, now)
        if g is None:
            return None, "", "That is not on the list any more."
        try:
            letter = forms.get(form).letter if form else ""
        except KeyError:
            return None, "", "Noticed does not know that way to do it."
        if not letter:
            rec = forms.recommend(g, store.built, titles=store._titles())
            letter = g.form or (rec.form.letter if rec else "")
        if letter not in ("A", "D") or not forms.available(letter, store.built)[0]:
            return None, letter, "That way of doing it cannot be made here yet."
        ask = {"id": g.id, "state": g.state, "n": g.n, "days": len(g.days), "sentences": list(g.sentences),
               "opens": g.opens, "word": g.word, "label": g.label}
        return ask, letter, ""

    def _w_make_word(self, ask: dict, now: float) -> tuple[Result, dict | None]:
        """Form A: a row in words.toml. A phrase that already means something makes nothing and answers
        the offer Got it."""
        store = self._store
        kind, _, name = ask["opens"].partition(":")
        phrase = ask["word"]
        if not (phrase and kind in ("app", "panel") and name):
            why = "Noticed could not tell what that word should open, so nothing was made."
            return Result(False, why), None
        if kind == "app" and name not in {a.name for a in launcher.known_apps()}:
            return Result(False, "That app is not here any more, so nothing was made."), None
        try:
            word = words.add(phrase, {"kind": kind, "name": name}, group=ask["id"], now=now)
        except words.WordRefused as e:
            if e.why == "means":
                store.answer(ask["id"], offers.GOT_IT, now=now)
                said = f"“{phrase}” already means something on this computer, so nothing was made."
                return Result(False, said), None
            return Result(False, str(e)), None
        except words.WordError as e:
            return Result(False, str(e)), None
        store.note_word_made(word.phrase, now)
        store.answer(ask["id"], offers.ACCEPT, form="A", now=now)
        title = f"Made “{word.phrase}” open {forms.thing_title(ask['opens'], store._titles())}."
        return Result(True, title), {"phrase": word.phrase, "title": title, "group": ask["id"],
                                     "opens": {"kind": kind, "name": name}}

    async def _op_accept(self, rid: str, form: str | None, writer) -> Result:
        if rid in self._accepting:
            return Result(True, "That is already being made.")
        self._accepting.add(rid)
        try:
            return await self._accept(rid, form, writer)
        finally:
            self._accepting.discard(rid)

    async def _accept(self, rid: str, form: str | None, writer) -> Result:
        now = self.clock()
        plan = await self._work(self._w_plan, rid, form, now, default=None, where="accept")
        if plan is None:
            return Result(False, "That did not work.")
        ask, letter, problem = plan
        if ask is None:
            return Result(False, problem)
        if ask["state"] == "made":
            return Result(True, "That is already made.")
        if letter == "D":
            return await self._make_app(ask)
        failed = (Result(False, "That did not work."), None)
        res, made = await self._work(self._w_make_word, ask, now, default=failed, where="word")
        if made is not None:
            row = self._improve("word", made["title"], group=made["group"],
                                undo={"op": "remove_word", "phrase": made["phrase"], "opens": made["opens"]})
            await self.agentd._send(writer, _local_line(
                made["title"], target=made["phrase"],
                undo_msg={"type": "noticed_do", "op": "undo", "id": row["id"]}))
        return res

    def _app_names(self) -> dict[str, str]:
        return {a.name: str(a.title) for a in launcher.known_apps()}

    async def _make_app(self, ask: dict) -> Result:
        """Form D is an ordinary turn, asked on his tap: a request in his own words, origin "loop"."""
        if getattr(self.agentd, "access", "ready") != "ready":
            return Result(False, "Bombadil is not signed in to its AI right now, so it cannot make an app "
                                 "yet.")
        before = set(await asyncio.to_thread(self._app_names))
        pending = {"text": _app_prompt(ask), "before": before, "group": ask["id"],
                   "said": sentence_of(ask["sentences"][0]) if ask["sentences"] else ask["label"]}
        self._app_turns = [*self._app_turns[-(MAX_PENDING_APPS - 1):], pending]
        try:
            await self.agentd.handle({"type": "prompt", "text": pending["text"], "origin": "loop"}, None)
        except BaseException:
            self._app_turns.remove(pending)
            raise
        await self._work(self._w_answer, ask["id"], offers.ACCEPT, "D", self.clock(), where="accept")
        return Result(True, "Making a small app for it now. It will say what it made when it is done.")

    async def _app_row(self, entry: dict) -> None:
        """A model turn's row: if it is the one an "app" tap started, and it went well, find the app it
        made (what the apps folder gained) and put it in the trail."""
        if entry.get("origin") != "loop":
            return
        pending = next((p for p in self._app_turns if p["text"] == entry.get("prompt")), None)
        if pending is None:
            return
        self._app_turns.remove(pending)
        if entry.get("ok") is not True or entry.get("stopped"):
            # It did not get made: the ask goes back to "not now" instead of staying "made".
            await self._work(self._w_not_now, pending["group"], self.clock(), where="app")
            await self._refresh()
            return
        after = await asyncio.to_thread(self._app_names)
        for name in sorted(set(after) - pending["before"])[:3]:
            title = f"Made the app {after[name]} from “{pending['said']}”."
            self._improve("app", title, group=pending["group"], undo={"op": "trash_app", "name": name})
        await self._publish()

    # preview

    async def _op_preview(self, rid: str, form: str | None, writer) -> Result:
        preview = await self._work(self._w_preview, rid, form, self.clock(), default=None, where="preview")
        if preview is None:
            return Result(False, "There is nothing to show for that.")
        return Result(True, "This is what it would do.", preview)

    def _w_preview(self, rid: str, form: str | None, now: float):
        if ":" in rid:
            f = self._findings.get(rid)
            return report.preview(report.build(f)).to_dict() if f is not None else None
        g = self._store.group(rid, now)
        if g is None:
            return None
        try:
            letter = forms.get(form).letter if form else (g.form or "A")
        except KeyError:
            return None
        return forms.preview(g, letter, self._store._titles())

    # a report, and sending it

    async def _op_report(self, rid: str, form, writer) -> Result:
        made = await self._work(self._w_report, rid, default=None, where="report")
        if made is None:
            return Result(False, "That is not in the list of things it found any more.")
        await self._open_window()
        return Result(True, "The report is ready. Nothing is sent until you press Submit on the page.", made)

    def _w_report(self, fp: str):
        f = self._findings.get(fp)
        if f is None or f.state not in ("open", "reported"):
            return None
        rep = report.build(f)
        report.hold(rep)
        self._findings.mark(fp, "reported")
        return report.preview(rep).to_dict()

    def _w_reported(self, fp: str):
        f = self._findings.get(fp)
        return report.build(f) if f is not None and f.state == "reported" else None

    async def _op_send(self, rid: str, form, writer) -> Result:
        if rid in self._sending:
            return Result(False, "That is already being sent.")
        rep = await self._work(self._w_reported, rid, default=None, where="send")
        if rep is None:
            return Result(False, "Look at the report first, then send it.")
        self._sending.add(rid)
        try:
            return await self._send_report(rid, rep)
        finally:
            self._sending.discard(rid)

    async def _send_report(self, fp: str, rep: report.Report) -> Result:
        """Open the project's new-issue page, filled in; he presses Submit. The search for an issue with
        this fingerprint and the page itself are slow and run off the worker, so nothing waits on them."""
        number = await asyncio.to_thread(report.already_reported, fp, fetch=self.fetcher)
        if number is not None:
            await self._work(self._w_mark, fp, "sent", where="send")
            return Result(True, f"Already reported (#{number}). Nothing more was sent.")
        link = await asyncio.to_thread(report.issue_url, rep, copy=report.copy_text)
        shown = await asyncio.to_thread(self.opener or report.open_issue_page, link.url)
        if not shown:
            return Result(False, "The issue page would not open. The report is still held on this computer.")
        await self._work(self._w_mark, fp, "sent", where="send")
        if link.paste:
            tail = ("It was too long for a link, so it is on the clipboard: paste it into the page."
                    if link.copied else "It was too long for a link, so the page is empty. "
                    "The report is held on this computer.")
            return Result(True, f"The issue page is open. {tail}")
        return Result(True, "The issue page is open with the report filled in. "
                            "Press Submit there if it looks right.")

    # undo and bring back

    async def _op_undo(self, rid: str, form, writer) -> Result:
        row = self._row_of(rid)
        if row is None:
            return Result(False, "That change cannot be found any more.")
        if self._undone(rid):
            return Result(True, "That is already undone.")
        undo = row.get("undo") if isinstance(row.get("undo"), dict) else {}
        op = undo.get("op")
        now = self.clock()
        if op == "remove_word":
            phrase = str(undo.get("phrase") or "")
            took = await self._work(self._w_take_word, phrase, row.get("group"), now, default=False,
                                    where="undo")
            if not took:
                return Result(False, "That did not work.")
            text = f"Took out the word “{phrase}”."
            self._improve(row.get("what") or "word", text, group=row.get("group"), of=rid, undone=True)
        elif op == "trash_app":
            name = str(undo.get("name") or "")
            trash, title = await asyncio.to_thread(self._trash_app, name, now)
            if trash is None:
                return Result(False, "That app is not where it was made, so there is nothing to put away.")
            await self._work(self._w_not_now, row.get("group"), now, where="undo")
            text = f"Put the app {title} away."
            self._improve(row.get("what") or "app", text, group=row.get("group"), of=rid, undone=True,
                          trash=str(trash), name=name)
        elif op == "bring_back_word":
            phrase = str(undo.get("phrase") or "")
            ok, text = await self._work(self._w_word_back, phrase, now, default=(False, "That did not work."),
                                        where="undo")
            if not ok:
                return Result(False, text)
            self._improve("word", text, of=rid, undone=True)
        else:
            return Result(False, "That change cannot be taken back from here.")
        if writer is not None:
            await self.agentd._send(writer, _local_line(text))
        return Result(True, text)

    def _w_take_word(self, phrase: str, group: str | None, now: float) -> bool:
        words.remove(phrase)
        self._w_not_now(group, now)
        return True

    def _w_not_now(self, group: str | None, now: float) -> None:
        if group:
            self._store.answer(group, offers.NOT_NOW, now=now)   # undoing what he was offered: not now

    def _w_word_back(self, phrase: str, now: float) -> tuple[bool, str]:
        try:
            word = words.bring_back(phrase, now)
        except words.WordError as e:
            return False, str(e)
        if word is None:
            return False, f"“{phrase}” is not a put-away word any more."
        self._store.note_word_made(word.phrase, now)
        return True, f"Brought back the word “{phrase}”."

    def _trash_app(self, name: str, now: float) -> tuple[Path | None, str]:
        """Close an app's window and move its folder into the loop's trash. (where it went, its title)."""
        if not apps_mod.NAME_RE.match(name):
            return None, name
        src = apps_mod.app_dir(name)
        if not (src / "main.qml").exists():
            return None, name
        try:
            title = str(apps_mod.load(name).title)
        except Exception:  # noqa: BLE001 - a broken app.toml still has a folder to move
            title = name
        lx = getattr(self.agentd, "launcher", None)
        try:
            if lx is not None:
                lx.run(launcher.Action("app", name, "close", title))
        except Exception as e:  # noqa: BLE001 - an app that will not close is moved anyway
            self._say("close app", e)
        trash = self.dir / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / f"{name}-{int(now)}"
        n = 1
        while dest.exists():
            n += 1
            dest = trash / f"{name}-{int(now)}-{n}"
        shutil.move(str(src), str(dest))
        return dest, title

    async def _op_bring_back(self, rid: str, form, writer) -> Result:
        """A put-away word, a group he said no to, or an app that went to the trash."""
        row = self._row_of(rid)
        if row is not None:
            return await self._bring_back_app(row)
        now = self.clock()
        outcome = await self._work(self._w_bring_back, rid, now, default=None, where="bring back")
        if outcome is None:
            return Result(False, "That did not work.")
        return Result(*outcome)

    def _w_bring_back(self, rid: str, now: float) -> tuple[bool, str]:
        word = words.get(rid)
        if word is not None and word.away:
            try:
                words.bring_back(rid, now)
            except words.WordError as e:
                return False, str(e)
            self._store.note_word_made(word.phrase, now)
            return True, f"Brought back the word “{word.phrase}”."
        if any(r["id"] == rid for r in self._store.said_no()) or self._store.group(rid, now) is not None:
            out = self._store.bring_back(rid, now)
            if out["ok"]:
                return True, "Okay. That can come up again."
            return False, "That is not on the list any more."
        return False, "That is not on the list any more."

    async def _bring_back_made_word(self, row: dict) -> Result:
        """A word he took out: make it again, if what it opened is still here and the phrase is free."""
        rid = row["id"]
        undo = row.get("undo") if isinstance(row.get("undo"), dict) else {}
        opens = undo.get("opens") if isinstance(undo.get("opens"), dict) else {}
        phrase = str(undo.get("phrase") or "")
        if not (self._undone(rid) and phrase and opens.get("kind") in ("app", "panel") and opens.get("name")):
            return Result(False, "That word was not taken out, so there is nothing to bring back.")
        ok, text = await self._work(self._w_remake_word, phrase, opens, row.get("group"), self.clock(),
                                    default=(False, "That did not work."), where="bring back")
        if ok:
            self._improve("word", text, group=row.get("group"), of=rid, undone=False)
        return Result(ok, text)

    def _w_remake_word(self, phrase: str, opens: dict, group: str | None, now: float) -> tuple[bool, str]:
        try:
            word = words.add(phrase, {"kind": opens["kind"], "name": opens["name"]}, group=group, now=now)
        except words.WordError as e:
            return False, str(e)
        self._store.note_word_made(word.phrase, now)
        thing = forms.thing_title(f"{opens['kind']}:{opens['name']}", self._store._titles())
        return True, f"Made “{word.phrase}” open {thing} again."

    async def _bring_back_app(self, row: dict) -> Result:
        """An undone change whose app is in the trash: move it back, if its name is free."""
        rid = row["id"]
        if row.get("what") == "word":
            return await self._bring_back_made_word(row)
        answer = next((r for r in self._newest_first() if r.get("of") == rid), None)
        if answer is None or not answer.get("undone") or not answer.get("trash"):
            return Result(False, "That change was not put away, so there is nothing to bring back.")
        back, title = await asyncio.to_thread(self._untrash_app, str(answer.get("name") or ""),
                                              str(answer["trash"]))
        if back is None:
            return Result(False, title)
        text = f"Brought the app {title} back."
        self._improve(row.get("what") or "app", text, group=row.get("group"), of=rid, undone=False)
        return Result(True, text)

    def _untrash_app(self, name: str, trash: str) -> tuple[Path | None, str]:
        src = Path(trash)
        if not apps_mod.NAME_RE.match(name) or src.parent != (self.dir / "trash") or not src.is_dir():
            return None, "That app is no longer in the trash."
        dest = apps_mod.app_dir(name)
        if dest.exists():
            return None, "There is an app with that name already, so the old one stays in the trash."
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        try:
            title = str(apps_mod.load(name).title)
        except Exception:  # noqa: BLE001
            title = name
        return dest, title

    # forgetting, clearing, hiding

    async def _op_forget_asks(self, rid, form, writer) -> Result:
        await self._work(self._w_forget, self.clock(), where="forget")
        return Result(True, "Forgot what you asked. The words made from it stay.")

    def _w_forget(self, now: float) -> dict:
        out = self._store.forget_asks(now)
        refine.forget()
        return out

    async def _op_clear_found(self, rid, form, writer) -> Result:
        await self._work(self._findings_clear, where="clear")
        return Result(True, "Cleared what it found. A problem that is still there will be found again.")

    def _findings_clear(self) -> int:
        return self._findings.clear_found()

    async def _op_hide(self, rid, form, writer) -> Result:
        if not await self._set_hidden(True):
            return Result(False, NOT_SAVED)
        return Result(True, "Noticed is hidden. Say “show noticed” to bring it back.")

    async def _op_show(self, rid, form, writer) -> Result:
        if not await self._set_hidden(False):
            return Result(False, NOT_SAVED)
        await self.agentd.broadcast({"type": "noticed_open"})
        return Result(True, "Noticed is back.")


def _scan_trail(path: Path) -> list[dict]:
    """The improve rows of turns.jsonl. Only a line that says so is parsed; a line that is not JSON, or
    a file that is not there, costs that line."""
    rows: list[dict] = []
    try:
        with path.open("rb") as f:
            for raw in f:
                if b'"improve"' not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("kind") == "improve" and isinstance(row.get("id"), str):
                    rows.append(row)
    except OSError:
        pass
    return rows[-TRAIL_KEPT:]

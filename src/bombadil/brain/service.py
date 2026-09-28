"""bombadil-brain: the user service that keeps brain.db and answers on brain.sock.

Who made a file is known only at the moment it is written, so the brain listens to the root
watcher (bombadil-brain-watch) for every save with its writer, reads the logs other programs
keep (turns.jsonl, Chromium's History, pacman.log, memory.md) when the watcher says they
grew, and answers the pill, the Brain app, agentd and the CLI in JSON lines:
{"id", "op", ...} gets {"id", "ok", "result"} or {"id", "ok": false, "error": "a sentence"},
and after "subscribe" it pushes "changed", "show" and "status".

Why it is shaped this way:

- One writer. The ingest looks a thing up and then writes it, so all store work runs on ONE
  worker thread and the event loop only moves lines. The walks (the first index, and the
  catch-up after a gap) are the exception: they run on their own thread with their own
  connection, ~500 entries per transaction at idle priority, so the live stream never waits
  long and nothing waits for them.
- Nothing is scanned on a timer. The watcher says when a log grew; after a stop it replays
  its spool, and when it lost events it says "overflow" and the brain walks. Only without a
  watcher (the live ISO, a dev machine) does the brain look at the logs once a minute.
- Nothing waits for the brain. It answers from the index in milliseconds, a client that
  stops reading is dropped rather than slowing the others, and while the first index runs
  every question is answered from what is known so far.
- It never has to be right about what it did not see. What a walk finds without a witness
  is "there before the brain" or "while the brain was not watching", never a guess.
"""

import asyncio
import concurrent.futures
import fcntl
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .. import paths
from . import actors
from .ingest import Ingest, _app_title
from .store import Store

WATCH_SOCKET = "/run/bombadil-brain/watch.sock"
PACMAN_LOG = Path("/var/log/pacman.log")
BATCH_LINES = 500
BATCH_S = 0.25
PUSH_S = 0.25            # "changed" goes out at most four times a second
RECONNECT_S = 2.0
HELLO_S = 2.0            # a watcher that has not said hello by then does not hold the start up
LOOK_S = 60.0            # without a watcher: how often History and the logs are looked at
STATUS_S = 1.0           # progress pushes, at most once a second
REQUESTED_S = 120.0      # how long a thing the launcher asked for waits for the Brain app to start
SEND_CAP = 4 << 20       # bytes queued for a client that stopped reading before it is dropped
LINE_LIMIT = 1 << 20
READ_SIZE = 1 << 16
WALK_RETRY_S = 30.0
PROMPT_MAX = 4000
HISTORY_NAMES = ("History", "History-journal", "History-wal")
UNKNOWN = "The brain does not know this yet."
OPS = ("status", "thing", "focus", "children", "why", "search", "recent", "show", "requested", "describe",
       "note", "subscribe", "rebuild")


class Refusal(Exception):
    """A request the brain answers with no: str() is the sentence the client shows."""


class AlreadyRunning(Exception):
    pass


def log(text: str) -> None:
    print(f"bombadil-brain: {' '.join(str(text).split())}"[:400], file=sys.stderr, flush=True)


def watch_socket() -> str:
    return os.environ.get("BOMBADIL_WATCH_SOCKET") or WATCH_SOCKET


def _wire(msg: dict) -> bytes:
    return (json.dumps(msg, default=str) + "\n").encode()


def _int(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return max(low, min(high, int(value)))
    except (ValueError, OverflowError):
        return default


def _ref(req: dict, home: str):
    """The thing a request names: an id, a path (~ is home), a URL or a key."""
    ref = req.get("ref")
    if isinstance(ref, int) and not isinstance(ref, bool):
        return ref
    if isinstance(ref, str) and ref.strip() and "\0" not in ref:
        ref = ref.strip()
        if ref == "~" or ref.startswith("~/"):
            ref = home + ref[1:]
        return ref
    raise Refusal("Say which thing: a path, a link, or a name like turn:41.")


class Conn:
    """One client of brain.sock."""

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.subscribed = False
        self.closed = False

    def send(self, data: bytes) -> bool:
        if self.closed or self.writer.is_closing():
            self.close()
            return False
        if self.writer.transport.get_write_buffer_size() > SEND_CAP:
            log("dropping a client that stopped reading")
            self.close()
            return False
        try:
            self.writer.write(data)
        except (OSError, RuntimeError):
            self.close()
            return False
        return True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except (OSError, RuntimeError):
                pass


class Brain:
    """The service. `serve()` runs until `stop()`; everything else is its parts."""

    def __init__(self, db_path=None, socket_path=None, watch_path=None, home=None, *, xattrs: bool = True,
                 turns_log=None, pacman_log=PACMAN_LOG, memory_files=None, history_profiles=None,
                 history_workdir=None, describe_run=None, walk: bool = True):
        self.db_path = Path(db_path or paths.brain_db())
        self.socket_path = Path(socket_path or paths.brain_socket())
        self.watch_path = str(watch_path or watch_socket())
        self.home = str(home or paths.home()).rstrip("/") or "/"
        self.xattrs = xattrs
        self.walk = walk
        self._opts = {"turns_log": turns_log, "pacman_log": pacman_log, "memory_files": memory_files,
                      "history_profiles": history_profiles, "history_workdir": history_workdir,
                      "describe_run": describe_run}
        self.pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="brain-store")
        # Touched only on the worker thread.
        self.store: Store | None = None
        self.ingest: Ingest | None = None
        self.turns = self.history = self.pacman = self.memory = None
        self.describer = None
        self._describe_store: Store | None = None
        self._dirty: dict = {}               # witnesses whose files the last batch saw written
        self._stamps: dict[str, tuple] = {}  # log files as they were at the last look (no watcher)
        # Touched only on the loop.
        self.loop: asyncio.AbstractEventLoop | None = None
        self.conns: set[Conn] = set()
        self.watcher = "away"                # "here" while connected to the watcher
        self.watching: bool | None = None    # what its hello said
        self.watch_reason = ""
        self.walking: str | None = None      # "first" or "reconcile" while a walk runs
        self.progress: float | None = None
        self.requested: dict | None = None
        self._changed: set[int] = set()
        self._known = {"things": 0, "live": 0, "events": 0, "indexed": None, "turn": 0}
        self._walk_thread: threading.Thread | None = None
        self._walk_stop = threading.Event()
        self._walk_next: str | None = None
        self._walk_retry = WALK_RETRY_S
        self._rebuilding = False
        self._show_seq = 0
        self._history_task: asyncio.Task | None = None
        self._history_busy = False
        self._history_again = False
        self._status_at = 0.0
        self._status_pct: int | None = None
        self._stopping: asyncio.Event | None = None
        self._watch_ready: asyncio.Event | None = None
        self._tasks: set[asyncio.Task] = set()
        self._lock_fd: int | None = None
        self._sock_ino: int | None = None

    # -- running --

    async def serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stopping = asyncio.Event()
        self._watch_ready = asyncio.Event()
        self._take_lock()
        server = None
        try:
            await self.job(self._open)
            server = await self._listen()
            self._spawn(self._pusher())
            self._spawn(self._watch())
            self._spawn(self._look_around())
            await self.job(self._witnesses)
            try:
                await asyncio.wait_for(self._watch_ready.wait(), HELLO_S + 1)
            except TimeoutError:
                pass
            if self.walk and not self._stopping.is_set():
                indexed = await self.job(self.store.get_meta, "indexed")
                if not indexed:
                    self.want_walk("first")
                elif not self.watched:
                    self.want_walk("reconcile")   # nobody saw what happened while we were away
            await self._refresh_known()
            await self._stopping.wait()
        finally:
            await self._close(server)

    def stop(self) -> None:
        """Close cleanly (SIGTERM). Call on the loop, or through call_soon_threadsafe."""
        if self._stopping is not None:
            self._stopping.set()

    @property
    def watched(self) -> bool:
        return self.watcher == "here" and bool(self.watching)

    async def job(self, fn, *args):
        """Store work, on the one worker thread."""
        return await self.loop.run_in_executor(self.pool, self._run, fn, args)

    def _run(self, fn, args):
        try:
            return fn(*args)
        finally:
            ing = self.ingest
            if ing is not None and ing.changed:
                ids, ing.changed = ing.changed, set()
                self._to_loop(self._mark_changed, ids)

    def _mark_changed(self, ids) -> None:
        # Looked up here, on the loop: the pusher swaps the set out from under other threads.
        self._changed.update(ids)

    def _to_loop(self, fn, *args) -> None:
        try:
            self.loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass   # the loop is gone: we are shutting down

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                e = t.exception()
                log(f"{type(e).__name__}: {e}")
        task.add_done_callback(done)
        return task

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), seconds)
        except TimeoutError:
            pass

    def _take_lock(self) -> None:
        """One brain per brain.db: two writers would each think they know what the other did."""
        lock = Path(f"{self.db_path}.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AlreadyRunning(f"another brain is already keeping {self.db_path}") from None
        self._lock_fd = fd

    async def _listen(self):
        path = self.socket_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_socket() or path.is_symlink():
            path.unlink()   # left by a brain that died: the lock says none is running now
        server = await asyncio.start_unix_server(self._client, path=str(path), limit=LINE_LIMIT)
        os.chmod(path, 0o600)
        self._sock_ino = os.stat(path).st_ino
        return server

    async def _close(self, server) -> None:
        self._stopping.set()
        for task in list(self._tasks):
            task.cancel()
        if server is not None:
            server.close()
        for conn in list(self.conns):
            conn.close()
        self._walk_stop.set()
        walker = self._walk_thread
        if walker is not None:
            await asyncio.to_thread(walker.join, 5)
        try:
            await self.job(self._shut)
        except Exception as e:  # noqa: BLE001 - closing never fails the exit
            log(f"closing: {type(e).__name__}: {e}")
        self.pool.shutdown(wait=False)
        try:
            if self._sock_ino is not None and os.stat(self.socket_path).st_ino == self._sock_ino:
                self.socket_path.unlink()
        except OSError:
            pass
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    # -- the worker's parts --

    def _open(self) -> None:
        try:
            self.store = Store(self.db_path)
        except sqlite3.DatabaseError as e:
            # brain.db is a cache: one that is not a database any more is set aside and made
            # again. Locked, unreadable or a full disk is not that: it stays, and we fail.
            if isinstance(e, sqlite3.OperationalError) or \
                    getattr(e, "sqlite_errorname", "SQLITE_NOTADB") not in ("SQLITE_NOTADB", "SQLITE_CORRUPT"):
                raise
            log(f"{self.db_path} cannot be read ({e}); setting it aside and starting over")
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.replace(f"{self.db_path}{suffix}", f"{self.db_path}{suffix}.broken")
                except OSError:
                    pass
            self.store = Store(self.db_path)
        sessions = actors.Sessions(paths.state_dir() / "dev" / "sessions.json")
        self.ingest = Ingest(self.store, self.home, sessions, xattrs=self.xattrs)
        self._make_witnesses()
        self._make_describer()

    def _shut(self) -> None:
        if self.describer is not None:
            self.describer.close()
        for store in (self._describe_store, self.store):
            if store is not None:
                store.close()

    def _make_witnesses(self) -> None:
        try:
            from . import witnesses
        except ImportError as e:
            log(f"no witnesses ({e}); the brain knows only what the watcher and the walks see")
            return
        o = self._opts
        self.turns = witnesses.TurnsLog(self.ingest, o["turns_log"])
        self.history = witnesses.History(self.ingest, o["history_profiles"], o["history_workdir"])
        self.pacman = witnesses.PacmanLog(self.ingest, o["pacman_log"])
        self.memory = witnesses.Memory(self.ingest, o["memory_files"])
        self._specials()

    def _make_describer(self) -> None:
        if self.describer is not None:
            self.describer.close()
            self.describer = None
        try:
            from .describe import Describer
        except ImportError as e:
            log(f"no descriptions ({e})")
            return
        # Its thread writes descriptions on its own connection.
        if self._describe_store is None:
            self._describe_store = Store(self.db_path)
        self.describer = Describer(self._describe_store, self.home, run=self._opts["describe_run"],
                                   on_done=lambda tid: self._to_loop(self._mark_changed, (tid,)))

    def _safely(self, what: str, fn, *args):
        """A witness never takes the brain down."""
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            log(f"{what}: {type(e).__name__}: {e}")
            return None

    def _witnesses(self) -> None:
        """Read what the logs gained while the brain was away."""
        if self.turns is not None:
            self._safely("turns.jsonl", self.turns.read_new)
        if self.pacman is not None:
            self._safely("pacman.log", self.pacman.read_new, None)
        if self.history is not None:
            self._safely("History", self.history.import_new)
        if self.memory is not None:
            self._safely("memory.md", self.memory.read)
        self._specials()
        self._changed_logs()

    def _specials(self) -> None:
        """The witnesses' own files: a write to one means read that witness after the batch."""
        sp = self.ingest.specials
        if self.turns is not None:
            sp[str(self.turns.path)] = self._saw_turns
        if self.pacman is not None:
            sp[str(self.pacman.path)] = self._saw_pacman
        if self.memory is not None:
            for f in self._safely("memory.md", self.memory.files) or []:
                sp[f] = self._saw_memory
        if self.history is not None:
            for f in self._safely("History", self.history.files) or []:
                sp[f] = self._saw_history

    def _saw_turns(self, ev, who) -> None:
        self._dirty["turns"] = True

    def _saw_pacman(self, ev, who) -> None:
        self._dirty["pacman"] = who

    def _saw_memory(self, ev, who) -> None:
        self._dirty["memory"] = who

    def _saw_history(self, ev, who) -> None:
        self._dirty["history"] = True

    def _apply(self, events: list[dict]) -> tuple[list[str], bool]:
        """One batch from the watcher, then the witnesses it woke. Returns the control ops
        it carried and whether Chromium's History changed."""
        self._prescan(events)
        control: list[str] = []
        for attempt in range(3):
            try:
                control = self.ingest.apply(events)
                break
            except sqlite3.OperationalError as e:
                # The walk held the write lock past the busy timeout: try again, then walk.
                if attempt == 2:
                    log(f"lost {len(events)} events to a busy database ({e}); walking to catch up")
                    control = ["overflow"]
                else:
                    time.sleep(0.5)
            except Exception as e:  # noqa: BLE001 - one bad event must not cost the batch
                log(f"a batch failed ({type(e).__name__}: {e}); applying it one event at a time")
                control = []
                for ev in events:
                    try:
                        control += self.ingest.apply([ev])
                    except Exception as e2:  # noqa: BLE001
                        log(f"skipped an event for {str(ev.get('path'))[:200]}: {type(e2).__name__}: {e2}")
                break
        return control, self._after()

    def _prescan(self, events: list[dict]) -> None:
        """What specials cannot name ahead: a new Chromium profile, any app's app.toml."""
        apps = self.home + "/Apps/"
        for ev in events:
            for key in ("path", "old"):
                p = ev.get(key)
                if not isinstance(p, str):
                    continue
                name = p.rsplit("/", 1)[-1]
                if name in HISTORY_NAMES:
                    self._dirty["history"] = True
                elif name == "app.toml" and p.startswith(apps):
                    parts = p[len(apps):].split("/")
                    if len(parts) == 2 and parts[0]:
                        self._dirty.setdefault("apps", set()).add(parts[0])

    def _after(self) -> bool:
        dirty, self._dirty = self._dirty, {}
        if dirty.get("turns") and self.turns is not None:
            self._safely("turns.jsonl", self.turns.read_new)
        if "pacman" in dirty and self.pacman is not None:
            self._safely("pacman.log", self.pacman.read_new, dirty["pacman"])
        if "memory" in dirty and self.memory is not None:
            self._safely("memory.md", self.memory.read, dirty["memory"])
        for name in sorted(dirty.get("apps", ())):
            self._safely("app.toml", self._app_title, name)
        return bool(dirty.get("history")) and self.history is not None

    def _app_title(self, name: str) -> None:
        """An app's title lives in its app.toml: a save there renames the app."""
        path = f"{self.home}/Apps/{name}"
        thing = self.store.by_key(f"app:{name}")
        if thing is None:
            if not os.path.isdir(path):
                return
            thing = self.store.get(self.ingest.app_thing(name))
        title = _app_title(path, name)
        if thing is not None and title != thing["title"]:
            self.store.update(thing["id"], title=title)
            self.ingest.changed.add(thing["id"])

    def _history_due(self) -> float:
        return self.history.due() if self.history is not None else time.time()

    def _history_import(self) -> None:
        if self.history is not None:
            self._safely("History", self.history.import_new)
            self._specials()   # a profile made since the last import

    def _changed_logs(self) -> list[str]:
        """Which logs changed since the last look, by size and mtime (only without a watcher)."""
        named = []
        if self.turns is not None:
            named.append(("turns", [str(self.turns.path)]))
        if self.pacman is not None:
            named.append(("pacman", [str(self.pacman.path)]))
        if self.memory is not None:
            named.append(("memory", self._safely("memory.md", self.memory.files) or []))
        out = []
        for name, files in named:
            stamp = []
            for f in files:
                try:
                    st = os.stat(f)
                    stamp.append((f, st.st_mtime_ns, st.st_size, st.st_ino))
                except OSError:
                    stamp.append((f, None))
            stamp = tuple(stamp)
            if self._stamps.get(name) != stamp:
                self._stamps[name] = stamp
                out.append(name)
        return out

    def _look(self) -> None:
        """Without a watcher: what changed in the last minute, by mtime."""
        self._history_import()
        changed = self._changed_logs()
        if "turns" in changed:
            self._safely("turns.jsonl", self.turns.read_new)
        if "pacman" in changed:
            self._safely("pacman.log", self.pacman.read_new, None)
        if "memory" in changed:
            self._safely("memory.md", self.memory.read)

    def _wipe(self) -> None:
        """`bombadil brain rebuild`: forget everything; the walk and the logs make it again."""
        if self.describer is not None:
            self.describer.close()
        with self.store.tx():
            for table in ("things", "events", "links", "turns", "descriptions", "search"):
                self.store.x(f"DELETE FROM {table}")
            self.store.x("DELETE FROM meta WHERE key != 'schema'")
        self.ingest.changed.clear()
        self.ingest.specials.clear()
        self._dirty, self._stamps = {}, {}
        # Fresh witnesses: History remembers in memory when it last read each profile.
        self._make_witnesses()
        self._make_describer()

    def _counts(self) -> dict:
        c = self.store.counts()
        c["indexed"] = self.store.get_meta("indexed")
        c["turn"] = self.store.last_turn()
        return c

    # -- the watcher --

    async def _watch(self) -> None:
        """Follow the watcher; while it is away, try again every two seconds."""
        said = False
        while not self._stopping.is_set():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.watch_path, limit=LINE_LIMIT), HELLO_S)
            except (OSError, TimeoutError, ValueError) as e:
                if not said:
                    log(f"no watcher at {self.watch_path} ({e}); trying every {RECONNECT_S:.0f} s")
                    said = True
                self._watch_ready.set()
                await self._sleep(RECONNECT_S)
                continue
            said = False
            self.watcher = "here"
            try:
                await self._follow(reader)
            except Exception as e:  # noqa: BLE001 - the brain always goes back to the watcher
                log(f"the watcher's stream broke: {type(e).__name__}: {e}")
            finally:
                writer.close()
                was = self.watched
                self.watcher, self.watching = "away", None
                self._watch_ready.set()
                if not self._stopping.is_set():
                    if was:
                        log("the watcher went away")
                    self._status_soon(force=True)
            await self._sleep(RECONNECT_S)

    async def _follow(self, reader: asyncio.StreamReader) -> None:
        """Batches of up to 500 lines or 250 ms, applied in one transaction each."""
        buf = b""
        batch: list[dict] = []
        began = 0.0
        hello_by = self.loop.time() + HELLO_S
        while not self._stopping.is_set():
            if batch and (len(batch) >= BATCH_LINES or self.loop.time() - began >= BATCH_S):
                now, batch = batch[:BATCH_LINES], batch[BATCH_LINES:]
                await self._flush(now)
                began = self.loop.time()
                continue
            if batch:
                wait = began + BATCH_S - self.loop.time()
            elif self.watching is None and not self._watch_ready.is_set():
                wait = max(0.0, hello_by - self.loop.time())
            else:
                wait = None
            try:
                data = await asyncio.wait_for(reader.read(READ_SIZE), wait)
            except TimeoutError:
                if self.watching is None:
                    self._watch_ready.set()   # a watcher that says nothing does not hold the start up
                continue
            if not data:
                break
            *lines, buf = (buf + data).split(b"\n")
            for raw in lines:
                ev = _event(raw)
                if ev is None:
                    continue
                op = ev.get("op")
                if op == "hello":
                    self._hello(ev)
                elif op != "pong":
                    if not batch:
                        began = self.loop.time()
                    batch.append(ev)
            if len(buf) > LINE_LIMIT:
                log("the watcher sent a line too long to be one; skipping it")
                buf = b""
        while batch and not self._stopping.is_set():
            now, batch = batch[:BATCH_LINES], batch[BATCH_LINES:]
            await self._flush(now)

    def _hello(self, ev: dict) -> None:
        self.watching = bool(ev.get("watching"))
        self.watch_reason = str(ev.get("reason") or "")
        if not self.watching:
            log(f"the watcher is not watching here{': ' + self.watch_reason if self.watch_reason else ''}")
        self._watch_ready.set()
        self._status_soon(force=True)

    async def _flush(self, events: list[dict]) -> None:
        try:
            control, history = await self.job(self._apply, events)
        except Exception as e:  # noqa: BLE001 - lost events are found again by walking
            log(f"could not apply {len(events)} events ({type(e).__name__}: {e}); walking to catch up")
            control, history = ["overflow"], False
        if history:
            self._history_soon()
        if self.walk and ("overflow" in control or "caught_up" in control):
            self.want_walk("reconcile")

    # -- History, when it changed and it is time --

    def _history_soon(self) -> None:
        if self._history_task is None:
            self._history_task = self._spawn(self._history_later())
        elif self._history_busy:
            self._history_again = True   # changed while being read: read again when due

    async def _history_later(self) -> None:
        try:
            while not self._stopping.is_set():
                self._history_again = False
                due = await self.job(self._history_due)
                if due > time.time():
                    await self._sleep(due - time.time())
                if self._stopping.is_set():
                    return
                self._history_busy = True
                try:
                    await self.job(self._history_import)
                finally:
                    self._history_busy = False
                if not self._history_again:
                    return
        finally:
            self._history_task = None

    async def _look_around(self) -> None:
        """The one timer: only while no watcher is watching."""
        while not self._stopping.is_set():
            await self._sleep(LOOK_S)
            if not self.watched and not self._stopping.is_set() and not self._rebuilding:
                try:
                    await self.job(self._look)
                except Exception as e:  # noqa: BLE001 - the next minute tries again
                    log(f"looking around: {type(e).__name__}: {e}")

    # -- walks --

    def want_walk(self, kind: str) -> None:
        """Start a walk ("first" or "reconcile"), or run it after the one running now."""
        if self._stopping is not None and self._stopping.is_set():
            return
        if self._walk_thread is not None:
            if self._walk_next != "first":
                self._walk_next = kind
            return
        self._walk_stop.clear()
        self.walking, self.progress = kind, 0.0
        self._walk_thread = threading.Thread(target=self._walk_main, args=(kind,), name=f"brain-{kind}",
                                             daemon=True)
        self._walk_thread.start()
        self._status_soon(force=True)

    def _walk_main(self, kind: str) -> None:
        """On the walk's own thread, with its own Store."""
        result, failed, t0 = None, False, time.monotonic()
        try:
            from .index import Walker
            walker = Walker(self.db_path, self.home, xattrs=self.xattrs)
            try:
                go = walker.first_index if kind == "first" else walker.reconcile
                result = go(progress=lambda done, total: self._walked(walker, done, total),
                            stop=self._walk_stop.is_set)
                self._walked(walker, None, None)
            finally:
                walker.close()
        except Exception as e:  # noqa: BLE001 - a walk that fails is tried again later
            failed = True
            log(f"{'the first index' if kind == 'first' else 'catching up'} failed: {type(e).__name__}: {e}")
        if not failed:
            what = "the first index" if kind == "first" else "catching up"
            took = f"{time.monotonic() - t0:.1f} s"
            log(f"{what} stopped after {took}" if (result or {}).get("stopped") else f"{what} took {took}: {result}")
        self._to_loop(self._walk_done, kind, result, failed)

    def _walked(self, walker, done, total) -> None:
        ids, walker.ingest.changed = walker.ingest.changed, set()
        self._to_loop(self._on_progress, ids, done, total)

    def _on_progress(self, ids: set, done, total) -> None:
        self._mark_changed(ids)
        if done is not None and total:
            self.progress = min(1.0, done / total)
        self._status_soon()

    def _walk_done(self, kind: str, result, failed: bool) -> None:
        self._walk_thread = None
        self.walking = self.progress = None
        nxt, self._walk_next = self._walk_next, None
        if self._stopping.is_set():
            return
        if failed:
            delay, self._walk_retry = self._walk_retry, min(self._walk_retry * 2, 3600.0)
            self.loop.call_later(delay, self._retry_walk, kind)
        elif not (result or {}).get("stopped"):
            self._walk_retry = WALK_RETRY_S
        if nxt:
            self.want_walk(nxt)
        self._spawn(self._refresh_known(push=True))

    def _retry_walk(self, kind: str) -> None:
        if not self._stopping.is_set() and not self._rebuilding:
            self.want_walk(kind)

    async def _stop_walk(self, timeout: float = 60.0) -> bool:
        self._walk_next = None
        walker = self._walk_thread
        if walker is None:
            return True
        self._walk_stop.set()
        await asyncio.to_thread(walker.join, timeout)
        await asyncio.sleep(0)   # let _walk_done run
        return not walker.is_alive()

    # -- status --

    def _pct(self) -> int:
        return max(0, min(99, int((self.progress or 0.0) * 100)))

    def status(self) -> dict:
        """What `status` answers and "status" pushes, from what the brain last counted."""
        live = int(self._known.get("live") or 0)
        if self.walking == "first":
            text = f"Getting to know your files, {self._pct()}%"
        elif self.walking == "reconcile":
            text = f"Catching up on your files, {self._pct()}%"
        else:
            text = f"Knows {live:,} {'thing' if live == 1 else 'things'}"
            if self.watched:
                text += "; watching every save."
            elif self.watcher == "here" and self.watching is False:
                text += "; saves are not watched on this system."
            else:
                text += "; not watching saves right now."
        return {"text": text, "things": live, "events": int(self._known.get("events") or 0),
                "walking": self.walking, "progress": self.progress if self.walking else None,
                "watching": self.watched, "watcher": self.watcher, "reason": self.watch_reason,
                "indexed": _float(self._known.get("indexed")), "turn": self._known.get("turn") or 0}

    async def _refresh_known(self, push: bool = False) -> None:
        try:
            self._known = await self.job(self._counts)
        except Exception as e:  # noqa: BLE001 - a count that failed keeps the last one
            log(f"counting: {type(e).__name__}: {e}")
        if push:
            self._status_soon(force=True)

    def _status_soon(self, force: bool = False) -> None:
        """Push status to subscribers: at once for a change of state, at most once a second
        while a walk counts up."""
        now = time.monotonic()
        pct = self._pct() if self.walking else None
        if not force and (pct == self._status_pct or now - self._status_at < STATUS_S):
            return
        self._status_at, self._status_pct = now, pct
        self._broadcast({"push": "status", **self.status()})

    # -- pushes --

    def _broadcast(self, msg: dict) -> None:
        subs = [c for c in self.conns if c.subscribed]
        if not subs:
            return
        data = _wire(msg)
        for conn in subs:
            conn.send(data)

    async def _pusher(self) -> None:
        while not self._stopping.is_set():
            await self._sleep(PUSH_S)
            if self._changed:
                ids, self._changed = self._changed, set()
                self._broadcast({"push": "changed", "things": sorted(ids), "t": round(time.time(), 3)})

    # -- brain.sock --

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = Conn(writer)
        self.conns.add(conn)
        try:
            while not conn.closed:
                try:
                    line = await reader.readline()
                except ValueError:
                    conn.send(_wire({"id": None, "ok": False, "error": "That request is too long."}))
                    break
                except OSError:
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                reply = await self.answer(line, conn)
                if conn.send(_wire(reply)):
                    try:
                        await writer.drain()
                    except (OSError, RuntimeError):
                        break
        finally:
            self.conns.discard(conn)
            conn.close()

    async def answer(self, raw: bytes | str, conn: Conn | None = None) -> dict:
        """One request line -> its answer."""
        try:
            req = json.loads(raw)
        except (ValueError, RecursionError):
            req = None
        if not isinstance(req, dict):
            return {"id": None, "ok": False, "error": "That was not a request the brain understands."}
        rid = req.get("id")
        op = req.get("op")
        if not isinstance(op, str) or op not in OPS:
            return {"id": rid, "ok": False, "error": f"The brain cannot answer “{str(op)[:40]}”."}
        try:
            result = await getattr(self, f"_op_{op}")(req, conn)
        except Refusal as e:
            return {"id": rid, "ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - one bad question never costs the connection
            log(f"{op}: {type(e).__name__}: {e}")
            return {"id": rid, "ok": False, "error": "The brain could not answer that just now."}
        return {"id": rid, "ok": True, "result": result}

    async def _op_status(self, req, conn):
        await self._refresh_known()
        return self.status()

    async def _op_thing(self, req, conn):
        return await self.job(self._thing, _ref(req, self.home))

    async def _op_focus(self, req, conn):
        looking = req.get("looking")
        looking = looking if isinstance(looking, int) and not isinstance(looking, bool) else None
        return await self.job(self._focus, _ref(req, self.home), _int(req.get("limit"), 5, 0, 100), looking)

    async def _op_children(self, req, conn):
        return await self.job(self._children, _ref(req, self.home), _int(req.get("offset"), 0, 0, 10**9),
                              _int(req.get("limit"), 200, 1, 1000))

    async def _op_why(self, req, conn):
        return await self.job(self._why, _ref(req, self.home))

    async def _op_search(self, req, conn):
        q = req.get("q")
        kind = req.get("kind")
        return await self.job(self._search, q if isinstance(q, str) else "",
                              kind if isinstance(kind, str) and kind else None, _int(req.get("limit"), 20, 1, 200))

    async def _op_recent(self, req, conn):
        return await self.job(self._recent, _int(req.get("limit"), 20, 1, 200))

    async def _op_show(self, req, conn):
        ref = _ref(req, self.home)
        title, tid = await self.job(self._title_for, ref)
        self._show_seq += 1
        self.requested = {"ref": str(ref), "seq": self._show_seq, "title": title, "id": tid, "t": time.time()}
        self._broadcast({"push": "show", "ref": str(ref), "seq": self._show_seq, "title": title, "id": tid})
        return dict(self.requested)

    async def _op_requested(self, req, conn):
        r = self.requested
        return dict(r) if r is not None and time.time() - r["t"] <= REQUESTED_S else None

    async def _op_describe(self, req, conn):
        return await self.job(self._describe_ref, _ref(req, self.home))

    async def _op_note(self, req, conn):
        """agentd's pokes: a turn started (its scope's writes are that turn's), a turn ended
        (its row is in turns.jsonl)."""
        kind = req.get("kind")
        if kind == "turn_start":
            n = req.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or not 0 < n < 10**9:
                raise Refusal("A turn needs its number.")
            unit = req.get("unit")
            unit = unit.removesuffix(".scope") if isinstance(unit, str) and unit.strip() else None
            prompt = req.get("prompt")
            prompt = prompt[:PROMPT_MAX] if isinstance(prompt, str) and prompt.strip() else None
            t = _float(req.get("t")) or time.time()
            await self.job(self.ingest.turn, n, unit, prompt, t)
            return {"noted": kind, "n": n}
        if kind == "turn_end":
            if self.turns is not None:
                await self.job(self._safely, "turns.jsonl", self.turns.read_new)
            return {"noted": kind}
        raise Refusal(f"The brain takes no note of “{str(kind)[:40]}”.")

    async def _op_subscribe(self, req, conn):
        if conn is not None:
            conn.subscribed = True
        return {"subscribed": True}

    async def _op_rebuild(self, req, conn):
        if self._rebuilding:
            raise Refusal("The brain is already starting over.")
        self._rebuilding = True
        try:
            if not await self._stop_walk():
                raise Refusal("The brain is still putting a walk away; try again in a minute.")
            await self.job(self._wipe)
            await self.job(self._witnesses)
            if self.walk:
                self.want_walk("first")
        finally:
            self._rebuilding = False
        await self._refresh_known(push=True)
        return {"text": "Starting over: getting to know your files again."}

    # -- answers, on the worker --

    def _resolve(self, ref) -> dict:
        from . import focus
        thing = focus.resolve(self.store, ref)
        if thing is None:
            raise Refusal(UNKNOWN)
        return thing

    def _thing(self, ref) -> dict:
        from . import focus
        thing = self._resolve(ref)
        return {**thing, "ref": focus.ref_of(thing), "title": focus.title_of(self.store, thing)}

    def _focus(self, ref, limit: int, looking: int | None) -> dict:
        from . import focus
        out = focus.focus(self.store, ref, self.home, limit=limit, looking=looking)
        if out is None:
            raise Refusal(UNKNOWN)
        if not out["thing"].get("private"):
            out["description"] = self._description(out["thing"]["id"])
        return out

    def _children(self, ref, offset: int, limit: int) -> dict:
        from . import focus
        out = focus.children(self.store, ref, self.home, offset=offset, limit=limit)
        if out is None:
            raise Refusal(UNKNOWN)
        return out

    def _why(self, ref) -> str:
        from . import focus
        return focus.why(self.store, ref, self.home)

    def _description(self, tid: int) -> dict | None:
        if self.describer is None:
            return None
        try:
            return self.describer.get(tid)
        except Exception as e:  # noqa: BLE001 - Focus shows without a description
            log(f"describe {tid}: {type(e).__name__}: {e}")
            return None

    def _describe_ref(self, ref) -> dict | None:
        return self._description(self._resolve(ref)["id"])

    def _search(self, q: str, kind: str | None, limit: int) -> dict:
        try:
            rows = self.store.search(q, kind, limit)
        except sqlite3.OperationalError as e:
            log(f"search {q[:80]!r}: {e}")
            raise Refusal("The brain could not search for that.") from None
        return {"items": [self._item(t) for t in rows]}

    def _recent(self, limit: int) -> dict:
        rows = self.store.q("SELECT * FROM things WHERE deleted IS NULL AND forgotten = 0 AND touched IS NOT NULL "
                            "AND kind NOT IN ('system', 'site') ORDER BY touched DESC LIMIT ?", (limit,))
        return {"items": [self._item(t) for t in rows]}

    def _item(self, t: dict) -> dict:
        """A thing as one line in a list: 'lease-2026.pdf · Downloads · Tue'."""
        from . import focus, words
        at = t["touched"] or t["changed"] or t["created"]
        where = self._where(t)
        return {"id": t["id"], "ref": focus.ref_of(t), "title": focus.title_of(self.store, t), "kind": t["kind"],
                "path": t["path"], "url": t["url"], "where": where, "why": where, "t": at,
                "when": words.when(at), "private": bool(t["private"]), "deleted": t["deleted"]}

    def _where(self, t: dict) -> str:
        from . import focus
        kind = t["kind"]
        if kind == "page" and t["url"]:
            return focus.site_name(t["url"])
        if kind == "turn":
            return "the machine"
        if kind == "fact":
            return "memory"
        if kind == "session":
            return str((t["meta"] or {}).get("project") or "")
        for col in ("parent", "area"):
            if t[col] and t[col] != t["id"]:
                other = self.store.get(t[col])
                if other is not None:
                    return focus.title_of(self.store, other)
        return ""

    def _title_for(self, ref) -> tuple[str, int | None]:
        from . import focus
        thing = focus.resolve(self.store, ref)
        if thing is not None:
            return focus.title_of(self.store, thing), thing["id"]
        text = str(ref)
        return (os.path.basename(text.rstrip("/")) or text) if text.startswith("/") else text, None


def _event(raw: bytes) -> dict | None:
    try:
        ev = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return ev if isinstance(ev, dict) else None


def _float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(__doc__.split("\n\n", 1)[0])
        return 0 if argv[0] in ("-h", "--help") else 2
    brain = Brain()

    async def run():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, brain.stop)
        await brain.serve()

    log(f"keeping {brain.db_path}, answering on {brain.socket_path}")
    try:
        asyncio.run(run())
    except AlreadyRunning as e:
        log(str(e))
        return 1
    except OSError as e:   # no room for brain.db, or nowhere to answer
        log(f"cannot start: {e}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

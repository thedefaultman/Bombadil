"""bombadil-connect: the system service that owns connect.sock, the token store, the message ring and the press check.

Slack, Linear and the other services are reached through drivers (driver.py); this module knows nothing about any of
them. It answers agentd, the window and the launcher in JSON lines, as mail's service does: {"id", "op", ...} gets
{"id", "ok": true, "result"} or {"id", "ok": false, "error": "a sentence", "code"}, and after "subscribe" it pushes
"message", "task" and "connection". docs/CONNECT.md has the ops; each is one `_op_<name>` here.

Why it is shaped this way:

- It runs as its own system user and keeps every token in a directory only that user can read (store.py). The
  agent runs as the person, so nothing it can start reads a token, and no op returns one: `store_secret` takes a
  value, hands it to the driver's own `Secrets`, and says only that it did.
- Nothing of anyone else's is kept. Messages live in a ring in memory (at most 500, none older than 14 days) and go
  with the process; what is written down is the connections, their tokens, a time per conversation and the ids
  of the presses already performed. Tasks are not kept at all: `tasks` asks the drivers.
- The press is the only way out. `perform` does one thing for one press, and only when every check holds (see
  `_op_perform`): who pressed, that it was not the agent, that it is for exactly what was shown, that a connection
  can do it and which one, and that it was not done already or is not being done. It calls the driver once; what the
  driver returns or raises is final, and a press that may have gone is written down as that before the driver is
  asked and never tried again.
- A driver is one connection's whole life: it is built from the record, started, and started again with a growing
  pause (2 s to a minute) when it did not start or says it died. A driver that is not there only makes its own
  connection an error. Nothing a driver does can stall the loop: every call to one has a time limit, and every
  subscriber has a writer of its own with a queue that is cut when it falls behind.
- What is said about a connection, in an answer, a push or the log, is its own words and codes, never a driver's
  exception text or a secret: a driver's exceptions are logged by type alone, since what they hold is not known.
"""

import asyncio
import contextlib
import fcntl
import importlib
import json
import math
import os
import re
import select
import signal
import socket
import sqlite3
import struct
import sys
import time
from pathlib import Path

from .. import paths, procs
from . import driver as drv
from . import protocol
from .protocol import (
    AGENT,
    ALREADY,
    BAD_REQUEST,
    BLOCKED,
    BUSY,
    CHANGED,
    CODES,
    INTERNAL,
    NO_CONNECTION,
    NOT_FOUND,
    REFUSED,
    SERVICE_DOWN,
    UNKNOWN_OUTCOME,
    Refusal,
    log,
    one_line,
)
from .store import Store, private_directory

RING_MAX = 500
RING_AGE_S = 14 * 86400.0
PERFORMED_KEEP_S = 90 * 86400.0   # a press older than this is never pressed again: agentd forgets its proposals first
TIDY_S = 3600.0
DRIVER_S = 10.0           # a driver is asked to read for no longer than this
START_S = 60.0            # a driver's start() has this long to say what state it is in
START_WAIT_S = 20.0       # add_connection waits this long for it, and answers with whatever is known then
SECRETS_S = 30.0          # a driver's look at a new secret
STOP_S = 5.0
PERFORM_S = 60.0          # past this a press is an unknown outcome (Slack gives up on its own at 20 s)
BACKOFF_MIN, BACKOFF_MAX = 2.0, 60.0
STABLE_S = 60.0           # a driver up this long is well, and the next stop starts the pauses over
MAX_CONNECTIONS = 20
MAX_CLIENTS = 64
MAX_SCOPED_CLIENTS = 8    # connections an agent's turn may hold: it shares the socket with the person
QUEUE_MAX = 1000          # pushes waiting for one subscriber before it is dropped
DRAIN_S = 10.0            # a client that does not take what it was sent in this long is dropped
IDLE_S = 600.0            # a connection that is not waiting for pushes and says nothing this long is closed
MAX_NOTE = 200
MAX_SECRET = 2000
MAX_CONTENT = 100_000
UID_RANGE = (1000, 59999)

OPS = ("status", "connections", "add_connection", "remove_connection", "store_secret", "messages", "thread", "seen",
       "tasks", "perform", "subscribe", "fake_inject")
# What a process inside an agent's turn may not ask for even on the socket, where agentd's tools would never: a
# connection is the person's to make, change and put away, and what they have read is theirs to say.
PERSONS_ONLY = {"add_connection": "Connecting something", "remove_connection": "Disconnecting something",
                "store_secret": "Giving a token", "seen": "Saying what has been read"}
SERVICE_NAMES = {"slack": "Slack", "linear": "Linear", "notion": "Notion", "jira": "Jira", "todoist": "Todoist",
                 "clickup": "ClickUp"}
# Drivers are imported by name when they are first needed, so one that is not written yet or whose package is not
# installed costs its own connection and nothing else.
REGISTRY = {"slack": "bombadil.connect.slack:SlackDriver", "mcp": "bombadil.connect.mcpconn:McpDriver"}
FAKE_REGISTRY = {"slack": "bombadil.connect.fake:FakeDriver", "mcp": "bombadil.connect.fake:FakeDriver"}
NOT_DONE = "Nothing was done."
UNKNOWN_SENTENCE = str(drv.UnknownOutcome())

SO_PEERPIDFD = getattr(socket, "SO_PEERPIDFD", 77)   # Linux 6.5: a handle on the process, which is not its number


class AlreadyRunning(Exception):
    pass


def _bad(sentence: str) -> Refusal:
    return Refusal(BAD_REQUEST, sentence)


def _int(req: dict, name: str, default: int, low: int, high: int) -> int:
    value = req.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _bad(f"“{name}” is a number.")
    return max(low, min(high, int(value)))


def _epoch(req: dict, name: str) -> float:
    value = req.get(name)
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _bad(f"“{name}” is a time in seconds since 1970.")
    return float(value)


def _flag(req: dict, name: str) -> bool:
    value = req.get(name)
    if value is None or isinstance(value, bool):
        return bool(value)
    raise _bad(f"“{name}” is true or false.")


def _connection_id(req: dict, name: str) -> str:
    value = req.get(name)
    if not protocol.valid_connection_id(value):
        raise _bad("Say which connection.")
    return value


def _backoff(fails: int) -> float:
    """How long before a driver is started again after this many failures in a row: 2 s, doubling, to a minute."""
    return min(BACKOFF_MAX, BACKOFF_MIN * 2 ** max(0, fails - 1))


def _uid_range(value) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", value or "")
    if match and int(match[1]) <= int(match[2]):
        return int(match[1]), int(match[2])
    if value:
        log("BOMBADIL_CONNECT_UIDS is not like 1000-59999; using the default range")
    return UID_RANGE


def _parse(raw) -> dict | None:
    try:
        req = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return req if isinstance(req, dict) else None


def _line(reply: dict) -> bytes:
    try:
        return protocol.encode(reply)
    except (TypeError, ValueError):   # a number that is not one, or something that is not JSON: say so, not nothing
        return protocol.encode(protocol.answer_error(reply.get("id"), "Connections could not say that just now.",
                                                     INTERNAL))


def _peer_pidfd(sock) -> int | None:
    try:
        return struct.unpack("i", sock.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD, struct.calcsize("i")))[0]
    except (OSError, AttributeError, struct.error):
        return None   # an older kernel, or a process that was gone by the time it was asked


def turn_scope(pid: int) -> bool:
    """Is this process inside an agent turn's scope (procs.cgroup_of, as outbox.py asks it)?"""
    return procs.cgroup_of(pid) is not None


def _task_fields(raw) -> dict:
    """What a task card asks for, as a driver gave it, cut to the shape the card draws."""
    out: dict = {}
    for which in ("create", "comment"):
        rows = []
        for f in (raw.get(which) if isinstance(raw, dict) and isinstance(raw.get(which), list) else [])[:12]:
            if isinstance(f, dict) and one_line(f.get("key"), 40) and one_line(f.get("label"), 40):
                row = {"key": one_line(f["key"], 40), "label": one_line(f["label"], 40),
                       "edit": f.get("edit") if f.get("edit") in ("line", "text", "date") else "line"}
                if f.get("required") is True:
                    row["required"] = True
                rows.append(row)
        if rows:
            out[which] = rows
    return out


def _step(raw) -> dict | None:
    if not isinstance(raw, dict) or not one_line(raw.get("say"), 300):
        return None
    step = {"id": one_line(raw.get("id"), 40), "say": one_line(raw.get("say"), 300)}
    if protocol.web_url(raw.get("open")):
        step["open"] = protocol.web_url(raw["open"])
    point = raw.get("point")
    if isinstance(point, dict) and one_line(point.get("text"), 60):
        step["point"] = {"text": one_line(point["text"], 60), "label": one_line(point.get("label"), 60)}
    return step


def _receipt(result) -> dict:
    """What a driver said it did, as the one line and the page the person can open."""
    result = result if isinstance(result, dict) else {}
    out = {"line": one_line(result.get("line"), 200) or "Done."}
    web = result.get("web")
    if isinstance(web, dict) and protocol.web_url(web.get("url")) and one_line(web.get("name"), 40):
        out["web"] = {"name": one_line(web["name"], 40), "url": protocol.web_url(web["url"])}
    return out


class Peer:
    """One client of connect.sock, and what is known of the process at the other end."""

    def __init__(self, writer: asyncio.StreamWriter, pid: int, uid: int, pidfd: int | None = None):
        self.writer, self.pid, self.uid, self.pidfd = writer, pid, uid, pidfd
        self.scoped = False       # was the process inside an agent's turn when it connected
        self.subscribed = False
        self.closed = False
        self._queue: asyncio.Queue | None = None
        self._pusher: asyncio.Task | None = None

    def alive(self) -> bool:
        """Is the process that connected still there? The kernel names the process that called connect(), and a
        socket outlives it: a process inside a turn can connect, give the socket to a child and exit, and then
        nobody is at the number the kernel recorded, or somebody else is. Only a live process is asked which cgroup
        it is in, so a peer that is gone is not one that could be told apart from the person."""
        if self.pid <= 0:
            return False
        if self.pidfd is not None:
            try:   # a pidfd is readable once its process has ended
                poll = select.poll()
                poll.register(self.pidfd, select.POLLIN)
                return not poll.poll(0)
            except (OSError, ValueError):
                pass
        return os.path.exists(f"/proc/{self.pid}")

    def write(self, data: bytes) -> bool:
        if self.closed or self.writer.is_closing():
            self.close()
            return False
        try:
            self.writer.write(data)
        except (OSError, RuntimeError):
            self.close()
            return False
        return True

    def subscribe(self) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue(QUEUE_MAX)
            self._pusher = asyncio.ensure_future(self._pump())
        self.subscribed = True

    def push(self, data: bytes) -> None:
        """Queue a push. A subscriber that is a thousand behind is dropped, so one that stops reading costs a queue
        and then nothing, and the loop never waits for it."""
        if self._queue is None or self.closed:
            return
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            log("dropping a client that fell behind")
            self.close(abort=True)

    async def _pump(self) -> None:
        try:
            while True:
                data = await self._queue.get()
                self.writer.write(data)
                await asyncio.wait_for(self.writer.drain(), DRAIN_S)
        except (OSError, RuntimeError, TimeoutError):
            self.close(abort=True)

    def close(self, abort: bool = False) -> None:
        """Closing a transport waits for what is buffered to be read, which a client that does not read never does:
        so what is waiting is given DRAIN_S to be read, and a client that has stopped reading is cut at once."""
        if self.closed:
            return
        self.closed = True
        fd, self.pidfd = self.pidfd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if self._pusher is not None and self._pusher is not asyncio.current_task():
            self._pusher.cancel()
        try:
            transport = self.writer.transport
            if abort:
                transport.abort()
                return
            self.writer.close()
            if transport.get_write_buffer_size():
                asyncio.get_running_loop().call_later(DRAIN_S, transport.abort)
        except (OSError, RuntimeError):
            pass

    async def stopped(self) -> None:
        if self._pusher is not None:
            await asyncio.gather(self._pusher, return_exceptions=True)


class Live:
    """One connection while the service runs: its record, the driver that is up for it (if one is), and the task
    that keeps it up."""

    def __init__(self, record: dict):
        self.record = record
        self.cls = None                  # the driver's class, once it could be loaded
        self.driver: drv.Driver | None = None
        self.generation = 0              # which driver may speak: a stopped one is not heard
        self.task: asyncio.Task | None = None
        self.ready = asyncio.Event()     # a first start has been tried
        self.died = asyncio.Event()      # the driver said it died
        self.removed = False

    @property
    def id(self) -> str:
        return self.record["id"]


class Service:
    """The service. `serve()` runs until `stop()`. `engine` is "fake" for the sample driver (default: the
    BOMBADIL_CONNECT_ENGINE variable); `registry` maps a kind to a driver class or its "module:Class" name; `sleep`
    is what the pause before a driver is started again waits with."""

    def __init__(self, state_dir=None, socket_path=None, *, engine: str | None = None, registry: dict | None = None,
                 clock=time.time, sleep=asyncio.sleep, uids=None, in_turn=None):
        self.state_dir = Path(state_dir or paths.connect_state())
        self.db_path = self.state_dir / "connect.db"
        self.socket_path = Path(socket_path or paths.connect_socket())
        self.fake = (os.environ.get("BOMBADIL_CONNECT_ENGINE", "") if engine is None else engine) == "fake"
        self.registry = dict(registry if registry is not None else FAKE_REGISTRY if self.fake else REGISTRY)
        self.clock = clock
        self._sleep = sleep
        self.uids = _uid_range(os.environ.get("BOMBADIL_CONNECT_UIDS")) if uids is None else uids
        self.in_turn = in_turn or turn_scope
        self.store: Store | None = None
        self.lives: dict[str, Live] = {}
        self.ring: dict[str, dict] = {}            # ref -> message, as cleaned (its `unread` is worked out when it is read)
        self.seen: dict[str, float] = {}           # conversation key -> the time of the last message the person saw
        self.peers: set[Peer] = set()
        self._running: set[str] = set()            # proposals being performed now
        self._tasks: set[asyncio.Task] = set()
        self._clients: set[asyncio.Task] = set()
        self._stopping: asyncio.Event | None = None
        self._lock_fds: list[int] = []
        self._sock_ino: int | None = None
        self._agent_logged = float("-inf")

    # -- running --

    async def serve(self) -> None:
        self._stopping = asyncio.Event()
        self._take_locks()
        server = None
        try:
            self.store = Store(self.db_path)
            if self.fake:
                self._seed_fake()
            self.seen = self.store.seen_times()
            self.store.prune_performed(self.clock() - PERFORMED_KEEP_S)
            self.lives = {r["id"]: Live(r) for r in self.store.connections()}
            server = await self._listen()
            log(f"keeping {self.db_path}, answering on {self.socket_path}")
            for live in self.lives.values():
                self._start_live(live)
            self._spawn(self._tidy())
            await self._stopping.wait()
        finally:
            await self._close(server)

    def stop(self) -> None:
        """Close cleanly (SIGTERM). Call on the loop, or through call_soon_threadsafe."""
        if self._stopping is not None:
            self._stopping.set()

    def _seed_fake(self) -> None:
        """A store that is new starts with a workspace and a tool already connected, so nothing needs an account.
        Once only: what the person disconnects stays disconnected."""
        if self.store.get_meta("fake_seeded"):
            return
        from . import fake
        now = self.clock()
        self.store.add_connection(f"slack:w{self.store.next_number('slack')}", "slack", "slack", fake.WORKSPACE, "ok",
                                  "", now)
        self.store.add_connection("mcp:linear", "mcp", "linear", SERVICE_NAMES["linear"], "ok", "", now + 0.001)
        self.store.set_meta("fake_seeded", "1")

    def _take_locks(self) -> None:
        """One service per database and one per socket: two on the same tokens and press list would each believe
        they are the only one performing, and a second on the same socket would take it from the first."""
        try:
            private_directory(self.state_dir)   # before anything is put in it
            for what, path in ((self.db_path, Path(f"{self.db_path}.lock")),
                               (self.socket_path, Path(f"{self.socket_path}.lock"))):
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    raise AlreadyRunning(f"another bombadil-connect is already keeping {what}") from None
                self._lock_fds.append(fd)
        except BaseException:
            self._unlock()
            raise

    def _unlock(self) -> None:
        for fd in self._lock_fds:
            os.close(fd)
        self._lock_fds = []

    async def _listen(self):
        path = self.socket_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_socket() or path.is_symlink():
            path.unlink()   # left by a service that died: the lock says none is running now
        old = os.umask(0o111)
        try:
            server = await asyncio.start_unix_server(self._client, path=str(path), limit=protocol.MAX_LINE)
        finally:
            os.umask(old)
        os.chmod(path, 0o666)   # who may talk to it is decided by the peer's uid, per connection
        self._sock_ino = os.stat(path).st_ino
        return server

    async def _close(self, server) -> None:
        self._stopping.set()
        if server is not None:
            server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        # A request in flight ends here, before the store it is using is closed under it: a press that was
        # cancelled has written "unknown", which is what it may be.
        peers = list(self.peers)
        for task in list(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        for peer in peers:
            peer.close()
        await asyncio.gather(*(peer.stopped() for peer in peers), return_exceptions=True)
        await asyncio.gather(*(self._stop_driver(live) for live in self.lives.values()), return_exceptions=True)
        if self.store is not None:
            self.store.close()
        try:
            if self._sock_ino is not None and os.stat(self.socket_path).st_ino == self._sock_ino:
                self.socket_path.unlink()
        except OSError:
            pass
        self._unlock()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log(f"a task ended: {type(t.exception()).__name__}")
        task.add_done_callback(done)
        return task

    async def _tidy(self) -> None:
        while True:
            await asyncio.sleep(TIDY_S)
            self._expire()
            try:
                self.store.prune_performed(self.clock() - PERFORMED_KEEP_S)
            except sqlite3.Error as e:
                log(f"tidying: {type(e).__name__}")

    # -- drivers --

    def _start_live(self, live: Live) -> None:
        live.task = self._spawn(self._keep(live))

    def _load(self, record: dict):
        target = self.registry.get(record["kind"])
        if target is None:
            raise LookupError("no driver for that kind")
        if not isinstance(target, str):
            return target
        module, _, name = target.partition(":")
        return getattr(importlib.import_module(module), name)

    def _emitter(self, live: Live):
        generation = live.generation

        def emit(push) -> None:
            if live.removed or live.generation != generation:
                return   # a driver that was stopped, or replaced, says nothing
            try:
                self._take(live, push)
            except Exception as e:  # noqa: BLE001 - what a driver says is never worth the loop
                log(f"{live.id}: could not take a push ({type(e).__name__})")
        return emit

    async def _keep(self, live: Live) -> None:
        """Keep a driver up: start it, wait for it to die, start it again after a pause that grows."""
        fails = 0
        while not live.removed:
            live.died.clear()
            began = time.monotonic()
            try:
                up = await self._start(live)
            finally:
                live.ready.set()
            if up is None:
                return   # there is no driver to start: only a new Bombadil changes that
            if up:
                await live.died.wait()
                await self._stop_driver(live)
                if time.monotonic() - began >= STABLE_S:
                    fails = 0
            self._stopped(live)
            fails += 1
            await self._sleep(_backoff(fails))

    async def _start(self, live: Live) -> bool | None:
        """True when the driver is up, False when it did not start, None when there is no such driver."""
        name = SERVICE_NAMES.get(live.record["service"], "That service")
        try:
            live.cls = self._load(live.record)
        except Exception as e:  # noqa: BLE001 - a driver that is missing or broken at import is only its connection
            log(f"{live.id}: no driver ({type(e).__name__})")
            self._set_state(live, "error", f"{name} is not available here.")
            return None
        live.generation += 1
        try:
            driver = live.cls(dict(live.record), self.store.secrets(live.id), self._emitter(live), self.clock)
            live.driver = driver
            await asyncio.wait_for(driver.start(), START_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - what the driver raised is not known to be free of secrets
            log(f"{live.id}: the driver did not start ({type(e).__name__})")
            await self._stop_driver(live)
            return False
        return True

    def _stopped(self, live: Live) -> None:
        """A driver is down and will be started again: the person is told, unless it already said why."""
        if live.record["state"] not in ("error", "blocked") and not live.removed:
            name = SERVICE_NAMES.get(live.record["service"], "That service")
            self._set_state(live, "error", f"{name} stopped. I will try again in a moment.")

    async def _stop_driver(self, live: Live) -> None:
        driver, live.driver = live.driver, None
        live.generation += 1
        if driver is None:
            return
        try:
            await asyncio.wait_for(driver.stop(), STOP_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"{live.id}: stopping the driver ({type(e).__name__})")

    def _safe(self, live: Live, method: str, default, *args):
        """What a driver says about itself, or `default` when it has no driver or the driver fails."""
        if live.driver is None:
            return default
        try:
            return getattr(live.driver, method)(*args)
        except Exception as e:  # noqa: BLE001
            log(f"{live.id}: {method} failed ({type(e).__name__})")
            return default

    async def _ask(self, live: Live, method: str, *args):
        """What a driver answers to a question, or None: one that raises or does not answer in DRIVER_S is skipped."""
        if live.driver is None:
            return None
        try:
            return await asyncio.wait_for(getattr(live.driver, method)(*args), DRIVER_S)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            log(f"{live.id}: {method} took too long")
        except Exception as e:  # noqa: BLE001
            log(f"{live.id}: {method} failed ({type(e).__name__})")
        return None

    def _ordered(self) -> list[Live]:
        return sorted(self.lives.values(), key=lambda live: (live.record["created"], live.id))

    # -- what drivers say --

    def _take(self, live: Live, push) -> None:
        kind = push.get("push") if isinstance(push, dict) else None
        if kind == "state":
            # the workspace's name, which a driver learns once it is in, comes in the push or in its own record
            own = getattr(live.driver, "conn", None)
            name = push.get("name") or (own.get("name") if isinstance(own, dict) else None)
            self._set_state(live, push.get("state"), push.get("note") or "", name)
        elif kind == "message":
            message = protocol.clean_message(push.get("message"), live.id)
            if message is not None:
                self._add_message(message)
        elif kind == "task":
            item = protocol.clean_task({"service": live.record["service"], **(push.get("item") or {})}, live.id)
            if item is not None:
                self._broadcast({"push": "task", "item": item})
        elif kind == "died":
            live.died.set()

    def _set_state(self, live: Live, state, note: str = "", name=None) -> None:
        if state not in drv.STATES:
            log(f"{live.id}: a driver gave a state that is not one")
            return
        record = live.record
        note = one_line(note, MAX_NOTE)
        name = one_line(name, protocol.MAX_NAME) if name else None
        if (state, note) == (record["state"], record["note"]) and name in (None, record["name"]):
            return
        record["state"], record["note"] = state, note
        if name:
            record["name"] = name
        try:
            self.store.update_connection(live.id, state=state, note=note, name=name)
        except sqlite3.Error as e:
            log(f"{live.id}: could not write the state ({type(e).__name__})")
        self._push_connection(live)

    def _out(self, live: Live) -> dict:
        """The Connection, as docs/CONNECT.md has it."""
        record = live.record
        steps = [s for s in map(_step, self._safe(live, "steps", []) or []) if s] \
            if record["state"] in ("setup", "signin") else []
        out = {"id": record["id"], "kind": record["kind"], "service": record["service"], "name": record["name"],
               "state": record["state"], "note": record["note"],
               "reads": one_line(self._safe(live, "reads", ""), 160),
               "can_post": record["state"] == "ok" and bool(self._safe(live, "can_post", False)),
               "steps": steps}
        fields = _task_fields(self._safe(live, "task_fields", {}))
        if fields:
            out["task_fields"] = fields
        if steps and steps[0].get("open"):
            out["open"] = steps[0]["open"]
        return out

    def _push_connection(self, live: Live) -> None:
        self._broadcast({"push": "connection", "connection": self._out(live)})

    def _broadcast(self, msg: dict) -> None:
        subscribers = [p for p in self.peers if p.subscribed]
        if subscribers:
            data = protocol.encode(msg)
            for peer in subscribers:
                peer.push(data)

    # -- the ring --

    def _expire(self) -> None:
        cutoff = self.clock() - RING_AGE_S
        for ref in [r for r, m in self.ring.items() if m["ts"] < cutoff]:
            del self.ring[ref]

    def _add_message(self, message: dict) -> None:
        message["unread"] = False   # whether it is unread is the service's note, worked out when it is read
        if message["ts"] < self.clock() - RING_AGE_S or self.ring.get(message["ref"]) == message:
            return   # too old to keep, or the same again
        self.ring[message["ref"]] = message
        self._expire()
        while len(self.ring) > RING_MAX:
            del self.ring[min(self.ring.values(), key=lambda m: (m["ts"], m["ref"]))["ref"]]
        if message["ref"] in self.ring:
            self._broadcast({"push": "message", "message": self._flagged(message)})

    def _unread(self, message: dict) -> bool:
        return message["ts"] > self.seen.get(protocol.conversation_key(message["ref"]), 0.0)

    def _flagged(self, message: dict) -> dict:
        return {**message, "unread": self._unread(message)}

    # -- clients --

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                 struct.calcsize("3i")))
        except (OSError, AttributeError, struct.error):
            pid, uid = 0, -1
        peer = Peer(writer, pid, uid, _peer_pidfd(sock))
        if not self.uids[0] <= uid <= self.uids[1]:
            peer.write(protocol.encode(protocol.answer_error(None, "Connections answer the people who use this "
                                                                    "computer.", REFUSED)))
            peer.close()
            return
        if len(self.peers) >= MAX_CLIENTS:
            peer.close()
            return
        peer.scoped = self._scoped(peer)
        if peer.scoped and sum(p.scoped for p in self.peers) >= MAX_SCOPED_CLIENTS:
            peer.close()
            return
        self.peers.add(peer)
        me = asyncio.current_task()
        self._clients.add(me)
        try:
            while not peer.closed:
                try:   # a window waiting for pushes is quiet for as long as it likes; anything else that is quiet is a leak
                    line = await asyncio.wait_for(reader.readline(), None if peer.subscribed else IDLE_S)
                except ValueError:
                    peer.write(protocol.encode(protocol.answer_error(None, "That request is too long.", BAD_REQUEST)))
                    break
                except (OSError, TimeoutError):
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                reply = await self._answer(_parse(line), peer)
                if peer.write(_line(reply)):
                    try:
                        await asyncio.wait_for(writer.drain(), DRAIN_S)
                    except (OSError, RuntimeError, TimeoutError):
                        break
        finally:
            self.peers.discard(peer)
            self._clients.discard(me)
            peer.close()

    def _scoped(self, peer: Peer) -> bool:
        """Is whoever is on the other end to be treated as inside an agent's turn? Asked again for every press, so a
        process that joined a turn after it connected is caught, and it fails closed: a peer the kernel could not
        name, that has gone since it connected (and may have left the socket to a child that is in the turn), or
        that cannot be looked at, is the agent's."""
        if peer.scoped or peer.pid <= 0 or not peer.alive():
            return True
        try:
            told = bool(self.in_turn(peer.pid))
        except Exception as e:  # noqa: BLE001 - what cannot be told is refused
            log(f"cannot tell which cgroup a client is in ({type(e).__name__})")
            return True
        return told or not peer.alive()   # gone while it was being asked: too late

    def _yours(self, peer: Peer, what: str) -> None:
        if self._scoped(peer):
            raise Refusal(REFUSED, f"{what} is yours to do. It cannot be done from an agent's turn.")

    async def _answer(self, req, peer: Peer) -> dict:
        """One request -> its answer. Whatever goes wrong is a sentence and a code, never a dead connection."""
        if not isinstance(req, dict):
            return protocol.answer_error(None, "That was not a request I understand.", BAD_REQUEST)
        rid, op = req.get("id"), req.get("op")
        rid = rid if isinstance(rid, (str, int)) and not isinstance(rid, bool) else None
        extra = {"rid": req["rid"]} if isinstance(req.get("rid"), (str, int)) and not isinstance(req.get("rid"), bool) \
            else {}   # a client with calls in flight together matches answers on this
        if not isinstance(op, str) or op not in OPS:
            return {**protocol.answer_error(rid, f"Connections cannot “{str(op)[:40]}”.", BAD_REQUEST), **extra}
        try:
            if op in PERSONS_ONLY:
                self._yours(peer, PERSONS_ONLY[op])
            result = await getattr(self, f"_op_{op}")(req, peer)
        except Refusal as e:
            return {**protocol.answer_error(rid, str(e), e.code), **extra}
        except Exception as e:  # noqa: BLE001 - one bad request never costs the connection
            # store_secret's request holds a secret, and what an exception says may repeat what it was given
            log(f"{op}: {type(e).__name__}" + ("" if op == "store_secret" else f": {e}"))
            return {**protocol.answer_error(rid, "Connections could not do that just now.", INTERNAL), **extra}
        return {**protocol.answer_ok(rid, result), **extra}

    # -- ops: connections --

    async def _op_status(self, req, peer):
        return {"state": "ok", "connections": [self._out(live) for live in self._ordered()]}

    async def _op_connections(self, req, peer):
        return {"connections": [self._out(live) for live in self._ordered()]}

    async def _op_add_connection(self, req, peer):
        service = req.get("service")
        if service not in protocol.SERVICES:
            raise _bad("I can connect Slack, Linear, Notion, Jira, Todoist and ClickUp.")
        if service != "slack" and f"mcp:{service}" in self.lives:
            return self._out(self.lives[f"mcp:{service}"])
        if len(self.lives) >= MAX_CONNECTIONS:
            raise Refusal(REFUSED, "That is as many connections as I keep. Disconnect one first.")
        if service == "slack":
            cid, kind = f"slack:w{self.store.next_number('slack')}", "slack"
            while cid in self.lives:   # a number a store of an older kind has used
                cid = f"slack:w{self.store.next_number('slack')}"
        else:
            cid, kind = f"mcp:{service}", "mcp"
        created = max([self.clock(), *(live.record["created"] + 1e-6 for live in self.lives.values())])   # in order
        record = self.store.add_connection(cid, kind, service, SERVICE_NAMES[service], "setup", "", created)
        live = self.lives[cid] = Live(record)
        self._start_live(live)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(live.ready.wait(), START_WAIT_S)
        self._push_connection(live)
        return self._out(live)

    async def _op_remove_connection(self, req, peer):
        live = self.lives.get(_connection_id(req, "id"))
        if live is None:
            raise Refusal(NOT_FOUND, "There is no such connection.")
        live.removed = True
        del self.lives[live.id]
        if live.task is not None:
            live.task.cancel()
            await asyncio.gather(live.task, return_exceptions=True)
        await self._stop_driver(live)   # first: a driver that is stopping must not write its secrets back
        self.store.remove_connection(live.id)
        for ref in [r for r, m in self.ring.items() if m["connection"] == live.id]:
            del self.ring[ref]
        self.seen = self.store.seen_times()
        live.record["state"], live.record["note"] = "off", "Disconnected."
        self._push_connection(live)
        return {}

    async def _op_store_secret(self, req, peer):
        live = self.lives.get(_connection_id(req, "connection"))
        if live is None:
            raise Refusal(NOT_FOUND, "There is no such connection.")
        name, value = req.get("name"), req.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise _bad("Say which secret it is, and its value.")
        rules = getattr(live.driver or live.cls, "secret_rules", None) or {}
        if name not in rules:
            raise Refusal(REFUSED, "That connection does not take that.")
        if not (value.startswith(rules[name]) and len(value) <= MAX_SECRET and value.isprintable()
                and value == value.strip()):
            raise _bad("That is not the kind of token I need. Copy it again.")
        self.store.secrets(live.id).set(name, value)
        if live.driver is not None:
            try:
                await asyncio.wait_for(live.driver.secrets_changed(), SECRETS_S)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                log(f"{live.id}: the driver took too long over a new secret")
            except Exception as e:  # noqa: BLE001 - the driver was just handed a secret: its words are not repeated
                log(f"{live.id}: the driver failed over a new secret ({type(e).__name__})")
                live.died.set()   # it is built again, from what is stored
        return {"stored": True}

    # -- ops: reading --

    async def _op_messages(self, req, peer):
        unread, since = _flag(req, "unread"), _epoch(req, "since")
        limit = _int(req, "limit", 50, 1, 100)
        lives = self._ordered()
        if req.get("connection") is not None:
            wanted = _connection_id(req, "connection")
            if wanted not in self.lives:
                raise Refusal(NOT_FOUND, "There is no such connection.")
            lives = [live for live in lives if live.id == wanted]
        self._expire()
        rows: list[dict] = []
        skipped: list[dict] = []
        asked: list[Live] = []
        for live in lives:
            mine = [m for m in self.ring.values() if m["connection"] == live.id]
            if live.record["state"] != "ok":
                skipped.append(self._skipped(live))
            if mine:
                rows += mine
            elif live.record["state"] == "ok":
                asked.append(live)   # nothing pushed yet: what the driver already knows is all there is
        answers = await asyncio.gather(*(self._ask(live, "messages", since, limit) for live in asked))
        for live, got in zip(asked, answers, strict=True):
            if not isinstance(got, list):
                skipped.append(self._skipped(live, "It did not answer in time."))
                continue
            rows += [m for m in (protocol.clean_message(raw, live.id) for raw in got) if m]
        flagged = [self._flagged(m) for m in {m["ref"]: m for m in rows}.values() if m["ts"] > since]
        flagged = [m for m in flagged if m["unread"] or not unread]
        flagged.sort(key=lambda m: (-m["ts"], m["ref"]))
        return {"messages": flagged[:limit], "skipped": skipped}

    def _skipped(self, live: Live, why: str = "It is not ready yet.") -> dict:
        return {"connection": live.id, "state": live.record["state"], "note": live.record["note"] or why}

    async def _op_thread(self, req, peer):
        ref = req.get("ref")
        if not protocol.valid_ref(ref):
            raise _bad("Say which message.")
        limit = _int(req, "limit", 20, 1, 100)
        live = self._live_for_ref(ref)
        if live is None or live.driver is None:
            raise Refusal(NOT_FOUND, "That is not a message I know.")
        name = SERVICE_NAMES.get(live.record["service"], "That service")
        try:
            got = await asyncio.wait_for(live.driver.thread(ref, limit), DRIVER_S)
        except asyncio.CancelledError:
            raise
        except drv.DriverError as e:
            raise Refusal(e.code if e.code in CODES else REFUSED, str(e)) from None
        except Exception as e:  # noqa: BLE001
            log(f"{live.id}: thread failed ({type(e).__name__})")
            raise Refusal(SERVICE_DOWN, f"{name} did not answer.") from None
        rows = [m for m in (protocol.clean_message(raw, live.id) for raw in (got if isinstance(got, list) else []))
                if m]
        rows.sort(key=lambda m: (m["ts"], m["ref"]))
        return {"messages": [self._flagged(m) for m in rows[-limit:]]}

    def _live_for_ref(self, ref: str) -> Live | None:
        message = self.ring.get(ref)
        if message is not None:
            return self.lives.get(message["connection"])
        return next((live for live in self._ordered() if self._owns(live, "slack_reply", ref)), None)

    def _owns(self, live: Live, kind: str, target: str) -> bool:
        return live.driver is not None and bool(self._safe(live, "owns", False, kind, target))

    async def _op_seen(self, req, peer):
        refs = req.get("refs")
        if not isinstance(refs, list) or len(refs) > 100 or not all(protocol.valid_ref(r) for r in refs):
            raise _bad("Say which messages you have seen.")
        newest: dict[str, tuple[str, float]] = {}
        for ref in refs:
            found = self._time_of(ref)
            key = protocol.conversation_key(ref)
            if found is not None and found[1] > newest.get(key, ("", 0.0))[1]:
                newest[key] = found
        for key, (connection, ts) in newest.items():
            if ts > self.seen.get(key, 0.0):
                self.store.mark_seen(connection, key, ts)
                self.seen[key] = ts
        return {}

    def _time_of(self, ref: str) -> tuple[str, float] | None:
        """Which connection a message is of and when it was written: from the ring, or (after a restart, when the ring
        is empty) from a Slack ref, which carries its own time."""
        message = self.ring.get(ref)
        if message is not None:
            return message["connection"], message["ts"]
        live = self._live_for_ref(ref)
        try:
            return (live.id, float(protocol.split_slack_ref(ref)[2])) if live is not None else None
        except (Refusal, ValueError):
            return None

    async def _op_tasks(self, req, peer):
        service = req.get("service")
        if service is not None and service not in protocol.SERVICES:
            raise _bad("Say which service.")
        limit = _int(req, "limit", 30, 1, 100)
        lives = [live for live in self._ordered() if live.record["kind"] != "slack"
                 and service in (None, live.record["service"])]
        asked = [live for live in lives if live.record["state"] == "ok"]
        answers = await asyncio.gather(*(self._ask(live, "tasks", limit) for live in asked))
        got = dict(zip((live.id for live in asked), answers, strict=True))
        items: list[dict] = []
        skipped: list[dict] = []
        for live in lives:
            if live.id not in got:
                skipped.append(self._skipped(live))
            elif not isinstance(got[live.id], list):
                skipped.append(self._skipped(live, "It did not answer in time."))
            else:
                items += [t for t in (protocol.clean_task({"service": live.record["service"], **raw}, live.id)
                                      for raw in got[live.id] if isinstance(raw, dict)) if t]
        return {"items": items[:limit], "skipped": skipped}

    async def _op_subscribe(self, req, peer):
        peer.subscribe()
        return {}

    # -- the press --

    async def _op_perform(self, req, peer):
        """The only way anything leaves, and only when every one of these holds, in this order: the peer's uid is
        allowed (it was, to get here); the peer is not inside an agent's turn; the fingerprint is the one worked out
        again here; the kind is one a driver can do; exactly one connection owns the target and it is ok and can post;
        the proposal has not been performed and is not being performed. Then the driver is called once."""
        if self._scoped(peer):
            self._note_agent(peer)
            raise Refusal(AGENT, "That press came from inside an agent's turn, and pressing is yours. "
                                 "If this is your own window, close it and open it again from the pill. " + NOT_DONE)
        kind, target, content = req.get("kind"), req.get("target"), req.get("content")
        fingerprint, proposal = req.get("fingerprint"), req.get("proposal")
        if not (isinstance(kind, str) and isinstance(target, str) and 0 < len(target) <= protocol.MAX_REF
                and isinstance(content, (str, dict)) and isinstance(fingerprint, str)
                and len(json.dumps(content, ensure_ascii=False)) <= MAX_CONTENT):
            raise _bad("That press did not say what it was for. " + NOT_DONE)
        if fingerprint != drv.fingerprint(kind, target, content):
            raise Refusal(CHANGED, "That is not what was pressed for. Look at it again, then press again. " + NOT_DONE)
        if kind not in drv.KINDS:
            raise Refusal(REFUSED, "That is not something I do. " + NOT_DONE)
        live = self._doer(kind, target)
        if not protocol.valid_proposal_id(proposal):
            raise _bad("That press did not say which proposal it was. " + NOT_DONE)
        if proposal in self._running:   # (it has a row already, written as unknown: asked about first)
            raise Refusal(BUSY, "That is already being done.")
        before = self.store.performed(proposal)
        if before is not None:
            raise Refusal(ALREADY, "That press already went through." if before["outcome"] == "done" else
                          "That press may already have gone. Look where it would have gone before you press again.")
        self._running.add(proposal)   # before the first await: two presses race into busy, never into two calls
        try:
            return await self._do(live, kind, target, content, proposal)
        finally:
            self._running.discard(proposal)

    def _doer(self, kind: str, target: str) -> Live:
        """The one connection that is to do this press, or the refusal that says why there is not one."""
        owners = [live for live in self._ordered() if self._owns(live, kind, target)]
        if not owners:
            raise Refusal(NO_CONNECTION, "I have no connection that can do that. " + NOT_DONE)
        ready = [live for live in owners if live.record["state"] == "ok" and self._safe(live, "can_post", False)]
        if len(ready) > 1:
            raise Refusal(REFUSED, "More than one connection could do that, so I did not guess. " + NOT_DONE)
        if ready:
            return ready[0]
        record = owners[0].record
        if record["state"] == "blocked":
            raise Refusal(BLOCKED, f"{record['note'] or 'That connection is blocked.'} " + NOT_DONE)
        if record["state"] == "error":
            raise Refusal(SERVICE_DOWN, f"{record['note'] or 'That connection is not working.'} " + NOT_DONE)
        if record["state"] == "ok":
            raise Refusal(REFUSED, "That connection only reads, so it cannot do that. " + NOT_DONE)
        raise Refusal(NO_CONNECTION, "That connection is not ready yet. " + NOT_DONE)

    async def _do(self, live: Live, kind: str, target: str, content, proposal: str) -> dict:
        now = self.clock()
        try:   # written before the driver is asked: a service that stops mid-press leaves a press that may have gone
            self.store.record_performed(proposal, kind, "unknown", now)
        except sqlite3.Error as e:
            log(f"could not write a press down ({type(e).__name__})")
            raise Refusal(INTERNAL, "I could not write that down, so I did not do it. " + NOT_DONE) from None
        outcome, receipt, trouble = "done", None, None
        try:
            receipt = await asyncio.wait_for(live.driver.perform(kind, target, content), PERFORM_S)
        except asyncio.CancelledError:
            raise   # stopped mid-press: "unknown" stays
        except drv.UnknownOutcome as e:
            outcome, trouble = "unknown", Refusal(UNKNOWN_OUTCOME, str(e))
        except drv.DriverError as e:
            try:
                self.store.forget_performed(proposal)   # nothing went: the person may press it again
            except sqlite3.Error as err:
                log(f"could not take a press back ({type(err).__name__}); it stays as one that may have gone")
            raise Refusal(e.code if e.code in CODES else REFUSED, str(e)) from None
        except Exception as e:  # noqa: BLE001 - after the driver was asked, nothing is known (this includes a timeout)
            log(f"{kind}: the driver failed ({type(e).__name__})")
            outcome, trouble = "unknown", Refusal(UNKNOWN_OUTCOME, UNKNOWN_SENTENCE)
        # From here nothing may fail the press: an answer that says it did not go would be believed.
        try:
            self.store.record_performed(proposal, kind, outcome, self.clock())
        except sqlite3.Error as e:
            log(f"could not write down how a press ended ({type(e).__name__})")
        log(f"performed {kind} for {proposal}: {outcome}")
        if trouble is not None:
            raise trouble
        return {"receipt": _receipt(receipt)}

    def _note_agent(self, peer: Peer) -> None:
        """One line now and then, not one for each: a process that tries over and over would fill the log."""
        if time.monotonic() - self._agent_logged >= 10.0:
            self._agent_logged = time.monotonic()
            log(f"a press from inside an agent's turn was refused (uid {peer.uid})")

    # -- the fake engine --

    async def _op_fake_inject(self, req, peer):
        """A message or a task, said as if its driver had (tests and the desktop test). Only on the fake engine."""
        if not self.fake:
            raise Refusal(REFUSED, "That is only for the fake engine.")
        message, item = req.get("message"), req.get("item")
        if isinstance(message, dict) == isinstance(item, dict):
            raise _bad("Give a message or an item.")
        what, raw = ("message", message) if isinstance(message, dict) else ("item", item)
        kind = "slack" if what == "message" else "mcp"   # a message is a workspace's, a task a tool's
        wanted = req.get("connection")
        live = self.lives.get(wanted) if wanted is not None else next(
            (c for c in self._ordered() if c.record["kind"] == kind
             and (what == "message" or raw.get("service") in (None, c.record["service"]))), None)
        if live is None or live.record["kind"] != kind or not hasattr(live.driver, "inject"):
            raise Refusal(NO_CONNECTION, "There is no connection to say that.")
        live.driver.inject(what, raw)
        return {"injected": what}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(__doc__.split("\n\n", 1)[0])
        return 0 if argv[0] in ("-h", "--help") else 2
    service = Service()

    async def run():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, service.stop)
        await service.serve()

    try:
        asyncio.run(run())
    except AlreadyRunning as e:
        log(str(e))
        return 1
    except (OSError, sqlite3.Error) as e:   # no room for the database, it is damaged, or nowhere to answer
        log(f"cannot start: {e}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

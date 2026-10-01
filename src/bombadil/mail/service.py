"""bombadil-mail: the user service that owns mail.db and answers on mail.sock.

Thunderbird runs unseen as the engine and keeps the mail; this service supervises it, asks it things
through the add-on (bridge.py), and answers the Mail window, agentd and the CLI in JSON lines:
{"id", "op", ...} gets {"id", "ok": true, "result"} or {"id", "ok": false, "error": "a sentence", "code"},
and after "subscribe" it pushes "changed", "new_mail", "show", "sent" and "status". docs/MAIL.md has the
ops; each is one `_op_<name>` here.

Why it is shaped this way:

- Nothing of anyone else's is kept. A mail's text is fetched when a window asks, held for the answer,
  and forgotten. The notes that are kept (marks, drafts, receipts) hold the person's own words and
  one line of why, never a mail's body.
- The press is the only way out. A draft goes when the person presses Send for exactly what the view
  showed: `send` refuses unless the stored fingerprint, the pressed one, the one the view reported as
  drawn and one worked out again from the draft and its attachment files all agree, and it refuses a
  peer inside an agent's turn. "sending" is written (and survives a power cut) before the engine is
  asked, a send that may have happened is "unknown" and is never retried, and pressing twice sends once.
- One writer. All of mail.db is used from ONE worker thread, and the event loop only moves lines.
  Copying and hashing attachments run on their own threads, and so do the calls that start and stop
  Thunderbird, so none of them can hold up an answer.
- Nothing waits on Thunderbird for long. Each engine request has a timeout, a client that stops reading
  is dropped rather than slowing the others, and the engine's supervision never raises: a Thunderbird
  that dies is started again with a growing pause, and every answer that needs it says so in a sentence.
- mail.db is a cache. One that is damaged is set aside as mail.db.broken and made again, and the
  accounts are found again from Thunderbird's own list.
"""

import asyncio
import base64
import binascii
import concurrent.futures
import contextlib
import fcntl
import json
import math
import mimetypes
import os
import re
import shutil
import signal
import socket
import sqlite3
import struct
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .. import paths
from . import accounts as accts
from . import bridge, drafts, protocol, text
from .protocol import (
    BAD_REQUEST,
    CHANGED,
    ENGINE_DOWN,
    ENGINE_ERROR,
    FOLDERS,
    INTERNAL,
    NO_ACCOUNT,
    NOT_FOUND,
    REFUSED,
    TOO_BIG,
    UNKNOWN_OUTCOME,
    Addr,
    Refusal,
    log,
    msg_id,
    split_id,
)
from .store import Store, corrupt

READ_S = 10.0              # an engine request that reads
SEND_S = 60.0              # the engine's send: past this the outcome is unknown
ATTACHMENT_S = 120.0
KNOWN_S = 5.0
COUNTS_S = 10.0            # how long the engine's counts are believed
SUPERVISE_S = 3.0
BACKOFF_MIN, BACKOFF_MAX = 2.0, 60.0
STABLE_S = 60.0            # Thunderbird up this long is well, and the next stop starts the pauses over
LINK_WAIT_S = 120.0        # running with no add-on connected for this long is a Thunderbird to start again
DETECT_S = 8.0
PUSH_S = 0.25              # "changed" goes out at most four times a second
REQUESTED_S = 120.0
SEND_CAP = 4 << 20         # bytes queued for a client that stopped reading before it is dropped
DRAIN_S = 10.0             # a client that does not take an answer in this long is dropped
LINE_LIMIT = 1 << 20       # a request is at most this
CURSOR_MAX = 100_000       # a cursor names the newest time not yet shown and the mail at that time already shown
SEEN_MAX = 100
PRUNE_S = 3600.0
READ_SIZE = 1 << 16
MAX_CLIENTS = 64
MAX_DRAFTS = 200
MAX_RECIPIENTS = 100
MAX_BODY = 500_000          # characters of a draft's body ...
MAX_BODY_BYTES = 600_000    # ... and bytes, so that a send fits in one frame to Thunderbird
MAX_SUBJECT = 998
WHY_MAX = 140
NEW_MAIL_MAX = 20          # messages of one new_mail event that are told to the window ...
NEW_MAIL_SCAN = 500        # ... out of this many looked at
NEW_MAIL_AGE_S = 86400.0   # mail older than this is not "new", whatever the engine says
EVENTS_MAX = 200
PRESS_LOG_ROTATE = 1 << 20
DOWNLOAD_SLOTS = 2
UNKNOWN_SENTENCE = "Thunderbird did not say whether this went; look in Sent before pressing again."

OPS = ("ping", "status", "accounts", "add_account", "remove_account", "views", "list", "search", "read",
       "set_flags", "archive", "trash", "mark_reply", "save_attachment", "draft", "draft_edit", "draft_get",
       "draft_discard", "draft_shown", "send", "known", "subscribe", "show", "requested", "recent",
       "engine_window")
USABLE = ("ok", "syncing")
# What a process inside an agent's turn may not ask for even on the socket, where agentd's tools would never: the
# person's mail is not moved, saved to disk or put away, and their window is not taken from them, by a mail that
# talked an agent into it. (Sending, looking at a draft and accounts say so in their own ops.)
PERSONS_ONLY = {"set_flags": "Marking mail read or flagged", "archive": "Archiving mail", "trash": "Deleting mail",
                "save_attachment": "Saving an attachment", "draft_discard": "Putting a draft away",
                "engine_window": "Showing Thunderbird", "requested": "Taking what the window was asked to show"}
ENGINE_STATES = {"ok": "ok", "syncing": "syncing", "signin": "signin", "error": "error", "blocked": "blocked",
                 "idle": "ok"}


_DRAFT_ID = re.compile(r"d[0-9]{1,12}")


class AlreadyRunning(Exception):
    pass


def _bad(sentence: str) -> Refusal:
    return Refusal(BAD_REQUEST, sentence)


def _wire(msg: dict) -> bytes:
    return (json.dumps(msg, default=str) + "\n").encode()


def _int(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return max(low, min(high, int(value)))
    except (ValueError, OverflowError):
        return default


def _float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _draft_id(req: dict) -> str:
    """A draft id is what the store made ("d12"), and nothing else is looked up: an id a client makes up may hold
    anything, and the notes file would have to be asked about it."""
    did = req.get("id")
    if not isinstance(did, str) or not _DRAFT_ID.fullmatch(did):
        raise _bad("Say which draft.")
    return did


def _flag(req: dict, name: str) -> bool | None:
    value = req.get(name)
    if value is None or isinstance(value, bool):
        return value
    raise _bad(f"“{name}” is true or false.")


def _string(req: dict, name: str, limit: int, *, line: bool = False) -> str | None:
    """A text argument: a string that can be stored, at most `limit` characters, on one line when it is to be."""
    value = req.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad(f"“{name}” is text.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _bad(f"“{name}” cannot be stored.") from None
    value = value.replace("\x00", "")
    if line:
        value = " ".join(value.split())
    if len(value) > limit:
        raise _bad(f"“{name}” is too long.")
    return value


def _body(req: dict) -> str | None:
    value = _string(req, "body", MAX_BODY)
    if value is not None and len(value.encode()) > MAX_BODY_BYTES:
        raise _bad("“body” is too long.")
    return value


def _addresses(req: dict, name: str) -> list[Addr] | None:
    if req.get(name) is None:
        return None
    try:
        out = protocol.dedupe(protocol.parse_addrs(req[name]))
    except ValueError as e:
        raise _bad(str(e)) from None
    if len(out) > MAX_RECIPIENTS:
        raise _bad("That is too many addresses.")
    return out


def _paths(req: dict, name: str) -> list[tuple[str, str | None]]:
    """Files to attach: paths, or {"path", "name"}."""
    items = req.get(name)
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > drafts.ATTACHMENTS_MAX:
        raise _bad(f"“{name}” is a list of at most {drafts.ATTACHMENTS_MAX} files.")
    out = []
    for item in items:
        path, label = (item.get("path"), item.get("name")) if isinstance(item, dict) else (item, None)
        if not isinstance(path, str) or not path or "\x00" in path or not (label is None or isinstance(label, str)):
            raise _bad("Each file is a path.")
        out.append((path, label))
    return out


def _clean(value, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _local_time(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _who(addrs: list[dict]) -> str:
    """"Priya" for Priya Shah <priya@acme.test>, else the address; and how many more there are."""
    first = addrs[0]
    words = first["name"].split()
    name = words[0].strip(",") if words and words[0].strip(",") else first["email"]
    return name + (f" and {len(addrs) - 1} more" if len(addrs) > 1 else "")


class Conn:
    """One client of mail.sock, and what is known of the process at the other end."""

    def __init__(self, writer: asyncio.StreamWriter, pid: int, uid: int):
        self.writer = writer
        self.pid = pid
        self.uid = uid
        self.scoped = False      # is the process inside an agent's turn, as it was when it connected
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


def turn_scope(pid: int) -> bool:
    """Is this process inside an agent turn's scope? What cannot be told is not (the check that matters
    is agentd's; this one is the second)."""
    try:
        from .. import procs
        return procs.cgroup_of(pid) is not None
    except Exception:  # noqa: BLE001 - unknown means allowed
        return False


class Service:
    """The service. `serve()` runs until `stop()`."""

    def __init__(self, db_path=None, socket_path=None, *, engine=None, process=None, resolver=None, in_turn=None,
                 clock=time.time, press_log=None, initial_accounts=None):
        self.db_path = Path(db_path or paths.mail_db())
        self.socket_path = Path(socket_path or paths.mail_socket())
        self.press_path = Path(press_log or paths.press_log())
        self.engine = engine if engine is not None else bridge.EngineLink()
        self.process = process
        self.resolver = resolver
        self.in_turn = in_turn or turn_scope
        self.clock = clock
        self.initial_accounts = list(initial_accounts or [])
        self.pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="mail-store")
        self.files = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix="mail-files")
        self.procs = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="mail-engine")
        self.store: Store | None = None        # touched only on the worker thread
        self.loop: asyncio.AbstractEventLoop | None = None
        self.conns: set[Conn] = set()
        self.requested: dict | None = None
        self.engine_state, self.engine_detail = "off", "Mail is off until an account is added."
        self._stopping: asyncio.Event | None = None
        self._poke: asyncio.Event | None = None
        self._tasks: set[asyncio.Task] = set()
        self._events: deque = deque(maxlen=EVENTS_MAX)
        self._event_ready: asyncio.Event | None = None
        self._eacc: list[dict] | None = None   # the engine's accounts result and when it came
        self._eacc_at = 0.0
        self._what: set[str] = set()
        self._flights: dict[str, tuple] = {}   # draft id -> (fingerprint pressed, future of the answer)
        self._dlocks: dict[str, list] = {}   # draft id -> [lock, how many hold or wait for it]
        self._downloads = asyncio.Semaphore(DOWNLOAD_SLOTS)   # no loop is bound until first use
        self._show_seq = int(time.time() * 1000)
        self._lock_fd: int | None = None
        self._sock_ino: int | None = None
        self._running_since: float | None = None
        self._fails = 0
        self._next_try = 0.0
        self._cut = 0.0                        # when the add-on's link was last seen to be gone
        self._status_sent: tuple | None = None
        self._restarting = False

    # -- running --

    async def serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stopping, self._poke, self._event_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self._take_lock()
        server = None
        try:
            await self.job(self._open)
            self.engine.on_event = self._on_event
            self.engine.on_state = self._on_state
            await self.engine.start()
            server = await self._listen()
            for task in (self._pusher(), self._event_loop(), self._supervise()):
                self._spawn(task)
            if self.engine.connected:   # a link that was there before the service listened (the fake, a test)
                self._spawn(self._engine_up())
            await self._stopping.wait()
        finally:
            await self._close(server)

    def stop(self) -> None:
        """Close cleanly (SIGTERM). Call on the loop, or through call_soon_threadsafe."""
        if self._stopping is not None:
            self._stopping.set()
            self._poke.set()

    def _take_lock(self) -> None:
        """One service per mail.db: two would each believe they are the only one sending."""
        lock = Path(f"{self.db_path}.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AlreadyRunning(f"another bombadil-mail is already keeping {self.db_path}") from None
        self._lock_fd = fd

    async def _listen(self):
        path = self.socket_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_socket() or path.is_symlink():
            path.unlink()   # left by a service that died: the lock says none is running now
        old = os.umask(0o177)   # the socket is made private, not made and then made private
        try:
            server = await asyncio.start_unix_server(self._client, path=str(path), limit=LINE_LIMIT)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        self._sock_ino = os.stat(path).st_ino
        return server

    async def _close(self, server) -> None:
        self._stopping.set()
        if server is not None:
            server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for conn in list(self.conns):
            conn.close()
        try:
            await self.engine.close()
        except Exception as e:  # noqa: BLE001 - closing never fails the exit
            log(f"closing the engine link: {type(e).__name__}: {e}")
        if self.process is not None:
            try:
                await asyncio.wait_for(self._process(self.process.stop), 15)
            except Exception as e:  # noqa: BLE001
                log(f"stopping Thunderbird: {type(e).__name__}: {e}")
        try:
            await self.job(self._shut)
        except Exception as e:  # noqa: BLE001
            log(f"closing: {type(e).__name__}: {e}")
        for pool in (self.pool, self.files, self.procs):
            pool.shutdown(wait=False)
        try:
            if self._sock_ino is not None and os.stat(self.socket_path).st_ino == self._sock_ino:
                self.socket_path.unlink()
        except OSError:
            pass
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

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

    # -- threads --

    async def job(self, fn, *args):
        """Store work, on the one worker thread. A database found damaged is made again, and the caller is told."""
        return await self.loop.run_in_executor(self.pool, self._run, fn, args)

    def _run(self, fn, args):
        try:
            return fn(*args)
        except sqlite3.DatabaseError as e:
            if not corrupt(e):
                raise
            log(f"{self.db_path} is damaged ({e}); setting it aside and starting over")
            self.store.start_over(e)
            self.loop.call_soon_threadsafe(self._recovered)
            raise Refusal(INTERNAL, "Mail's own notes were damaged and had to be started again. "
                                    "Try that once more.") from e

    def _recovered(self) -> None:
        """On the loop, after the notes were made again: the accounts come back from Thunderbird's own list."""
        self._eacc = None
        if self._stopping is not None and not self._stopping.is_set():
            self._spawn(self._refresh(fresh=True, push=True))

    async def _process(self, fn, *args):
        """A call on the engine's process, which may block (it starts and stops a program)."""
        return await self.loop.run_in_executor(self.procs, fn, *args)

    async def _file(self, fn, *args):
        return await self.loop.run_in_executor(self.files, fn, *args)

    # -- on the worker --

    def _open(self) -> None:
        self.store = Store(self.db_path)
        if self.store.recovered:
            log(f"{self.db_path} could not be used ({self.store.recovered}); set aside as mail.db.broken")
        now = self.clock()
        for did in self.store.lose_sends(now):
            log(f"{did} was being sent when the service stopped; it may or may not have gone")
            self._press_row({"kind": "mail", "id": did, "fingerprint": "", "ok": False, "code": UNKNOWN_OUTCOME,
                             "pid": 0, "src": "mail"})
        self._prune()
        for row in self.initial_accounts:
            self._adopt(row)
        self._sweep_files()

    def _prune(self) -> None:
        """Drafts sent or discarded long ago go, with their receipts and any file left of them."""
        for did in self.store.prune(self.clock()):
            drafts.remove_copies(did)

    def _shut(self) -> None:
        if self.store is not None:
            self.store.close()

    def _sweep_files(self) -> None:
        """Files that belong to nothing: what was half fetched when the last run stopped, and the copies of
        drafts that are gone or no longer need them."""
        shutil.rmtree(paths.mail_files() / "inflight", ignore_errors=True)
        root = paths.mail_files() / "drafts"
        if not root.is_dir():
            return
        live = {d["id"] for d in self.store.drafts()}
        for child in root.iterdir():
            if child.name not in live:
                shutil.rmtree(child, ignore_errors=True)

    def _adopt(self, row: dict) -> dict | None:
        """An account the engine has that we do not know, unless it was removed on purpose."""
        email = protocol.parse_addr(row.get("email"))
        if email is None or self.store.is_forgotten(email.email):
            return None
        found = self.store.account_by_email(email.email)
        if found is not None:
            return found
        hint = row.get("provider")
        key = hint if hint in accts.PROVIDERS else accts.detect(email.email, resolver=lambda _d: []).key
        state = ENGINE_STATES.get(row.get("state"), "syncing")
        return self.store.add_account(email.email, key, state, name=_clean(row.get("name") or email.email, 200),
                                      note=_clean(row.get("note") or row.get("detail"), 300), now=self.clock())

    def _press_row(self, row: dict) -> None:
        """One row per press: when, what, which fingerprint, who, how it ended. Never a word of the mail."""
        row = {"t": round(self.clock(), 3), **row}
        try:
            self.press_path.parent.mkdir(parents=True, exist_ok=True)
            if self.press_path.exists() and self.press_path.stat().st_size > PRESS_LOG_ROTATE:
                self.press_path.replace(self.press_path.with_name(self.press_path.name + ".1"))
            with self.press_path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            log(f"press log: {e}")   # a log that cannot be written never stops a press

    # -- accounts: what we know, and what the engine says --

    def _account_out(self, a: dict, unread: int = 0) -> dict:
        prov = accts.PROVIDERS.get(a["provider"], accts.IMAP)
        web = {"name": a["web_name"] or prov.web_name, "url": a["web_url"] or accts.web_link(prov, a["email"]) or ""}
        return {"id": a["id"], "email": a["email"], "name": a["name"] or a["email"], "provider": a["provider"],
                "state": a["state"], "note": a["note"], "web": web, "unread": unread}

    def _unread(self, a: dict) -> int:
        for e in self._eacc or []:
            if e.get("engine_id") == a["engine_id"] and isinstance(e.get("unread"), int):
                return max(0, e["unread"])
        return 0

    async def _accounts_out(self, fresh: bool = False) -> list[dict]:
        await self._refresh(fresh)
        rows = await self.job(lambda: self.store.accounts())
        return [self._account_out(a, self._unread(a)) for a in rows]

    async def _refresh(self, fresh: bool = False, push: bool = False) -> bool:
        """The engine's own list of accounts (believed for COUNTS_S): it says which are ready, signing in or
        blocked, and how much is unread. False when the engine could not be asked."""
        if not fresh and self._eacc is not None and time.monotonic() - self._eacc_at < COUNTS_S:
            return True
        if not self.engine.connected:
            return False
        try:
            rows = await self._ask("accounts")
        except Refusal:
            return False
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        changed = await self.job(self._apply_accounts, rows)
        self._eacc, self._eacc_at = rows, time.monotonic()
        if changed or push:
            self._changed("accounts", "list")
        return True

    def _apply_accounts(self, rows: list[dict]) -> bool:
        changed = False
        for row in rows:
            emails = [e.lower() for e in row.get("emails", []) if isinstance(e, str)] \
                if isinstance(row.get("emails"), list) else []
            a = next((found for found in (self.store.account_by_email(e) for e in emails) if found), None)
            if a is None:
                for e in emails:
                    a = self._adopt({**row, "email": e})
                    if a is not None:
                        changed = True
                        break
                if a is None:
                    continue
            web = row.get("web") if isinstance(row.get("web"), dict) else {}
            identities = row.get("identities") if isinstance(row.get("identities"), list) else []
            sender = next((_clean(i.get("name"), 200) for i in identities
                           if isinstance(i, dict) and str(i.get("email", "")).lower() == a["email"]), a["sender"])
            state = ENGINE_STATES.get(row.get("state"), a["state"])
            fresh = {"engine_id": str(row.get("engine_id") or "") or None, "state": state,
                     "note": _clean(row.get("detail"), 300) if state != "ok" else "", "sender": sender,
                     "web_name": _clean(web.get("name"), 40) or None, "web_url": _clean(web.get("url"), 500) or None}
            if any(a[k] != v for k, v in fresh.items()):
                self.store.update_account(a["id"], **fresh)
                changed = True
        return changed

    async def _resolve(self, ref) -> dict:
        """An account by id, address or name."""
        if not isinstance(ref, str) or not ref.strip():
            raise _bad("Say which account.")
        ref = ref.strip().lower()
        rows = await self.job(lambda: self.store.accounts())
        for a in rows:
            if ref in (a["id"], a["email"], a["name"].lower()):
                return a
        raise Refusal(NO_ACCOUNT, "There is no mail account like that.")

    async def _engine_account(self, a: dict) -> str:
        """The engine's id for an account that can be read, or a refusal that says why not."""
        if a["state"] == "blocked":
            raise Refusal(REFUSED, a["note"] or f"{a['email']} cannot be used here.")
        if a["state"] == "signin":
            raise Refusal(ENGINE_ERROR, f"Finish signing in to {a['email']} first.")
        if a["state"] == "error":
            raise Refusal(ENGINE_ERROR, a["note"] or f"Something is wrong with {a['email']}.")
        if not a["engine_id"] and self.engine.connected:
            await self._refresh(fresh=True)
            a = await self.job(lambda: self.store.account(a["id"])) or a
        if not a["engine_id"]:
            raise Refusal(ENGINE_ERROR, f"Thunderbird does not have {a['email']} yet.")
        return a["engine_id"]

    def _down(self) -> Refusal:
        code = NO_ACCOUNT if self.engine_state == "off" else ENGINE_DOWN
        return Refusal(code, self.engine_detail)

    async def _ask(self, op: str, timeout: float = READ_S, **args):
        """One engine request, with its failures as refusals: a sentence and a code."""
        try:
            return await self.engine.request(op, timeout, **args)
        except bridge.EngineGone:
            raise self._down() from None
        except bridge.EngineTimeout:
            raise Refusal(ENGINE_ERROR, "Thunderbird did not answer in time.") from None
        except bridge.EngineError as e:
            raise Refusal(e.code if e.code in (NOT_FOUND, TOO_BIG) else ENGINE_ERROR, str(e)) from None

    # -- messages --

    def _msg(self, aid: str, e, mark: dict | None) -> dict | None:
        """A message as the window sees it. The engine's message is checked here: a shape that cannot be shown
        is left out, not passed on."""
        if not isinstance(e, dict):
            return None
        key = e.get("key")
        if not isinstance(key, str) or not key or len(key) > protocol.MAX_KEY:
            return None
        ts = _float(e.get("ts")) or 0.0
        folder = e.get("folder") if e.get("folder") in FOLDERS else "other"
        return {"id": msg_id(aid, key), "account": aid, "key": key,
                "from": protocol.addr_or_raw(e.get("from")).as_dict(),
                "to": [protocol.addr_or_raw(x).as_dict() for x in e.get("to") or [] if x][:50],
                "cc": [protocol.addr_or_raw(x).as_dict() for x in e.get("cc") or [] if x][:50],
                "subject": _clean(e.get("subject")), "ts": ts, "unread": bool(e.get("unread")),
                "flagged": bool(e.get("flagged")), "attachments": bool(e.get("attachments")), "folder": folder,
                "needs_reply": mark is not None, "why": mark["why"] if mark else None,
                "thread": str(e["thread"])[:200] if e.get("thread") else None}

    async def _msgs(self, engine_rows: list, by_engine: dict[str, dict]) -> list[dict]:
        """Engine messages as ours, with the marks that belong to them."""
        wanted: dict[str, list] = {}
        for e in engine_rows:
            a = by_engine.get(e.get("account")) if isinstance(e, dict) else None
            if a is not None and isinstance(e.get("key"), str):
                wanted.setdefault(a["id"], []).append(e)
        marks = await self.job(self._marks_for, {aid: [e["key"] for e in rows] for aid, rows in wanted.items()})
        out = []
        for e in engine_rows:
            a = by_engine.get(e.get("account")) if isinstance(e, dict) else None
            if a is not None:
                m = self._msg(a["id"], e, marks.get((a["id"], e.get("key"))))
                if m is not None:
                    out.append(m)
        return out

    def _marks_for(self, keys: dict[str, list[str]]) -> dict:
        return {(aid, k): m for aid, ks in keys.items() for k, m in self.store.marks_for(aid, ks).items()}

    async def _by_engine(self) -> dict[str, dict]:
        rows = await self.job(lambda: self.store.accounts())
        return {a["engine_id"]: a for a in rows if a["engine_id"]}

    async def _target(self, req: dict) -> tuple[dict, str, str]:
        """The account (ready to be asked), engine account id and key of the mail an id names."""
        try:
            aid, key = split_id(req.get("id"))
        except protocol.BadId as e:
            raise _bad(str(e)) from None
        a = await self.job(lambda: self.store.account(aid))
        if a is None:
            raise Refusal(NO_ACCOUNT, "There is no mail account for that mail.")
        if not self.engine.connected:
            raise self._down()
        return a, await self._engine_account(a), key

    # -- pushes --

    def _broadcast(self, msg: dict) -> None:
        subs = [c for c in self.conns if c.subscribed]
        if subs:
            data = _wire(msg)
            for conn in subs:
                conn.send(data)

    def _changed(self, *what: str) -> None:
        self._what.update(what)

    async def _pusher(self) -> None:
        tidied = time.monotonic()
        while not self._stopping.is_set():
            await self._sleep(PUSH_S)
            if time.monotonic() - tidied > PRUNE_S:
                tidied = time.monotonic()
                try:
                    await self.job(self._prune)
                except Exception as e:  # noqa: BLE001 - tidying is never worth the pusher
                    log(f"tidying: {type(e).__name__}: {e}")
            if self._what:
                what, self._what = sorted(self._what), set()
                self._broadcast({"push": "changed", "what": what})
            self._push_status()

    def _push_status(self, force: bool = False) -> None:
        mark = (self.engine_state, self.engine_detail)
        if force or mark != self._status_sent:
            self._status_sent = mark
            self._broadcast({"push": "status", "engine": self.engine_state, "detail": self.engine_detail,
                             "text": self._status_text()})

    def _status_text(self) -> str:
        return {"up": "Mail is running."}.get(self.engine_state, self.engine_detail)

    # -- clients --

    def _peer(self, writer: asyncio.StreamWriter) -> tuple[int, int]:
        sock = writer.get_extra_info("socket")
        try:
            pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                 struct.calcsize("3i")))
        except (OSError, AttributeError, struct.error):
            return 0, -1
        return pid, uid

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pid, uid = self._peer(writer)
        if len(self.conns) >= MAX_CLIENTS or uid not in (os.getuid(), 0):
            writer.close()
            return
        conn = Conn(writer, pid, uid)
        conn.scoped = bool(pid) and bool(self.in_turn(pid))
        self.conns.add(conn)
        try:
            while not conn.closed:
                try:
                    line = await reader.readline()
                except ValueError:
                    conn.send(_wire({"id": None, "ok": False, "error": "That request is too long.",
                                     "code": BAD_REQUEST}))
                    break
                except OSError:
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                req = _parse(line)
                if req is not None and req.get("op") == "engine_hello":
                    await self._engine_connected(reader, writer, conn)
                    break
                reply = await self.answer_request(req, conn)
                if conn.send(_wire(reply)):
                    try:
                        await asyncio.wait_for(writer.drain(), DRAIN_S)
                    except (OSError, RuntimeError, TimeoutError):
                        break
        finally:
            self.conns.discard(conn)
            conn.close()

    async def _engine_connected(self, reader, writer, conn: Conn) -> None:
        """The host says it is the engine: the rest of this connection is engine frames."""
        if conn.scoped or self.in_turn(conn.pid) or not hasattr(self.engine, "attach"):
            return   # an agent's process is not Thunderbird, and the fake engine has no host
        self.conns.discard(conn)
        await bridge.serve_engine(reader, writer, self.engine)

    async def answer(self, raw: bytes | str, conn: Conn | None = None) -> dict:
        return await self.answer_request(_parse(raw), conn)

    async def answer_request(self, req, conn: Conn | None = None) -> dict:
        """One request -> its answer. Whatever goes wrong is a sentence and a code, never a dead connection."""
        if not isinstance(req, dict):
            return {"id": None, "ok": False, "error": "That was not a request mail understands.", "code": BAD_REQUEST}
        rid, op = req.get("id"), req.get("op")
        head = {"id": rid}
        if isinstance(req.get("rid"), (str, int)) and not isinstance(req.get("rid"), bool):
            head["rid"] = req["rid"]   # a client with calls in flight together matches answers on this
        if not isinstance(op, str) or op not in OPS:
            return {**head, "ok": False, "error": f"Mail cannot “{str(op)[:40]}”.", "code": BAD_REQUEST}
        try:
            if op in PERSONS_ONLY:
                self._yours(conn, PERSONS_ONLY[op])
            result = await getattr(self, f"_op_{op}")(req, conn)
        except Refusal as e:
            return {**head, "ok": False, "error": str(e), "code": e.code}
        except Exception as e:  # noqa: BLE001 - one bad request never costs the connection
            log(f"{op}: {type(e).__name__}: {e}")
            return {**head, "ok": False, "error": "Mail could not do that just now.", "code": INTERNAL}
        return {**head, "ok": True, "result": result}

    def _scoped(self, conn: Conn | None) -> bool:
        return conn is not None and (conn.scoped or (bool(conn.pid) and bool(self.in_turn(conn.pid))))

    def _yours(self, conn: Conn | None, what: str) -> None:
        """Some things are the person's alone: a process inside an agent's turn is refused."""
        if self._scoped(conn):
            raise Refusal(REFUSED, f"{what} is yours to do, in the Mail window. It cannot be done from an agent's turn.")

    # -- ops: status and accounts --

    async def _op_ping(self, req, conn):
        return {"pong": True, "t": round(self.clock(), 3)}

    async def _op_status(self, req, conn):
        accounts = await self._accounts_out()
        counts = await self.job(lambda: (self.store.count_marks(), self.store.count_drafts()))
        return {"engine": self.engine_state, "detail": self.engine_detail, "text": self._status_text(),
                "accounts": accounts, "unread": sum(a["unread"] for a in accounts if a["state"] in USABLE),
                "needs_reply": counts[0], "drafts": counts[1], "fake": type(self.engine).__name__ == "FakeEngine"}

    async def _op_accounts(self, req, conn):
        return {"accounts": await self._accounts_out(fresh=bool(req.get("fresh")))}

    async def _op_add_account(self, req, conn):
        self._yours(conn, "Adding an account")
        given = req.get("email")
        email = protocol.parse_addr(given.strip()) if isinstance(given, str) else None
        if email is None or email.name or not accts.valid_email(email.email):
            raise _bad("That is not an email address.")
        known = await self.job(lambda: self.store.account_by_email(email.email))
        if known is not None and known["state"] in USABLE:
            return self._account_out(known, self._unread(known))
        try:
            provider = await asyncio.wait_for(asyncio.to_thread(accts.detect, email.email, self.resolver), DETECT_S)
        except TimeoutError:
            provider = accts.detect(email.email, resolver=lambda _d: [])
        state = "signin" if provider.auth == "oauth2" else "syncing"
        row = known or await self.job(lambda: self.store.add_account(email.email, provider.key, state,
                                                                      note=provider.note, now=self.clock()))
        if known is not None:   # asked again for one that is not working yet: the same steps, once more
            await self.job(lambda: self.store.update_account(known["id"], state=state, note=provider.note))
            row = await self.job(lambda: self.store.account(known["id"]))
        if self.process is not None:
            await self._seed(row, provider)
        self._changed("accounts", "list")
        return self._account_out(row)

    async def _seed(self, row: dict, provider: accts.Provider) -> None:
        """Write the account into Thunderbird's settings (effective at its next start) and start or restart it."""
        try:
            await self._process(self.process.seed_account, dict(row), provider)
            if await self._process(self.process.running):
                await self._restart("a new account was added")
            else:
                self._next_try = 0.0
                self._poke.set()
        except Exception as e:  # the account is ours either way; the engine is the supervisor's
            log(f"seeding {row['email']}: {type(e).__name__}: {e}")
            raise Refusal(ENGINE_ERROR, "The account was added, but Thunderbird could not be set up for it yet.") from e

    async def _op_remove_account(self, req, conn):
        self._yours(conn, "Removing an account")
        a = await self._resolve(req.get("id"))
        if await self.job(lambda: any(d["state"] == "sending" for d in self.store.drafts(account=a["id"]))):
            raise Refusal(REFUSED, "A message is being sent from that account. Wait for it to finish.")
        gone = await self.job(lambda: self.store.delete_account(a["id"], self.clock()))
        for did in gone:
            await self._file(drafts.remove_copies, did)
        self._eacc = None
        if self.process is not None:
            try:
                await self._process(self.process.forget_account, dict(a))
                left = await self.job(lambda: len(self.store.accounts()))
                if not left:
                    await self._process(self.process.stop)
                elif await self._process(self.process.running):
                    await self._restart("an account was removed")
            except Exception as e:  # noqa: BLE001 - the account is gone from here; Thunderbird's settings follow
                log(f"forgetting {a['email']}: {type(e).__name__}: {e}")
        self._poke.set()
        self._changed("accounts", "list", "drafts")
        return {}

    async def _op_views(self, req, conn):
        accounts = await self._accounts_out()
        marks, open_drafts = await self.job(lambda: (self.store.count_marks(), self.store.count_drafts()))
        views = [{"id": "all", "name": "All inboxes", "count": sum(a["unread"] for a in accounts
                                                                      if a["state"] in USABLE), "state": "ok"}]
        views += [{"id": f"acct:{a['id']}", "name": a["email"], "count": a["unread"], "state": a["state"]}
                  for a in accounts]
        views += [{"id": "needs_reply", "name": "Needs a reply", "count": marks, "state": "ok"},
                  {"id": "drafts", "name": "Drafts", "count": open_drafts, "state": "ok"}]
        return {"views": views}

    # -- ops: lists --

    async def _op_list(self, req, conn):
        view = req.get("view", "all")
        limit = _int(req.get("limit"), 50, 1, 200)
        cursor = req.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > CURSOR_MAX):
            raise _bad("That is not a place in the list.")
        if view == "needs_reply":
            return await self._list_marks(limit, cursor)
        if view == "drafts":
            return await self._list_drafts(limit)
        if view == "all":
            rows = await self.job(lambda: self.store.accounts())
            return await self._list_inbox("all", rows, limit, cursor)
        if isinstance(view, str) and view.startswith("acct:"):
            a = await self._resolve(view[5:])
            return await self._list_inbox(view, [a], limit, cursor)
        raise _bad("The views are all, needs_reply, drafts and acct:<id>.")

    async def _list_marks(self, limit: int, cursor: str | None) -> dict:
        offset = _int(cursor, 0, 0, 10**9)
        rows = await self.job(lambda: self.store.marks(limit + 1, offset))
        more = len(rows) > limit
        msgs = [{"id": msg_id(m["account"], m["key"]), "account": m["account"], "key": m["key"],
                 "from": Addr(m["sender_name"], m["sender_email"]).as_dict(), "to": [], "cc": [],
                 "subject": m["subject"], "ts": m["ts"], "unread": False, "flagged": False, "attachments": False,
                 "folder": "inbox", "needs_reply": True, "why": m["why"], "thread": None} for m in rows[:limit]]
        return {"view": "needs_reply", "messages": msgs, "cursor": str(offset + limit) if more else None,
                "more": more, "skipped": []}

    async def _list_drafts(self, limit: int) -> list[dict]:
        """The open drafts, newest first, as a list: there is no paging through a few drafts."""
        return await self.job(lambda: [self._draft_out(d) for d in self.store.drafts()[:limit]])

    async def _list_inbox(self, view: str, accounts: list[dict], limit: int, cursor: str | None) -> dict:
        """The inboxes of these accounts, newest first. Accounts that cannot be read are said, not fatal."""
        before, seen = _unwrap_cursor(cursor)
        ready, skipped = [], []
        for a in accounts:
            if a["state"] in USABLE and a["engine_id"]:
                ready.append(a)
            else:
                skipped.append(self._skipped(a))
        if ready and not self.engine.connected:
            raise self._down()
        results = await asyncio.gather(*(self._ask("list", account=a["engine_id"], folder="inbox",
                                                   limit=limit + len(seen), **({"before": before} if before else {}))
                                         for a in ready), return_exceptions=True)
        pages, rows = [], []
        for a, res in zip(ready, results, strict=True):
            if isinstance(res, BaseException):
                if not isinstance(res, Refusal):
                    raise res
                skipped.append({**self._skipped(a), "note": str(res)})
                continue
            page = [m for m in (res.get("messages") or []) if isinstance(m, dict)] if isinstance(res, dict) else []
            pages.append((a, page, bool(isinstance(res, dict) and res.get("more"))))
            rows += page
        if ready and not pages:
            raise next(r for r in results if isinstance(r, Refusal))
        by_engine = {a["engine_id"]: a for a in ready}
        msgs = await self._msgs(rows, by_engine)
        msgs = [m for m in msgs if m["id"] not in seen]
        msgs.sort(key=lambda m: (-m["ts"], m["id"]))
        more = len(msgs) > limit or any(flag for _, _, flag in pages)
        page = msgs[:limit]
        await self.job(self._drop_gone, [(a["id"], [m["key"] for m in rows_ if isinstance(m.get("key"), str)],
                                          before, more_) for a, rows_, more_ in pages if a["state"] == "ok"])
        nxt = None
        if more and page:
            edge = page[-1]["ts"]
            held = [m["id"] for m in page if m["ts"] == edge] + (list(seen) if edge == before else [])
            nxt = _wrap_cursor(edge, held[:SEEN_MAX])
        return {"view": view, "messages": page, "cursor": nxt, "more": more, "skipped": skipped}

    def _skipped(self, a: dict) -> dict:
        return {"account": a["id"], "state": a["state"], "note": a["note"],
                "web": self._account_out(a)["web"]}

    def _drop_gone(self, pages: list) -> None:
        """A mark whose mail is no longer in the inbox page that should hold it is dropped: it was archived
        or moved somewhere Bombadil did not see."""
        for aid, keys, newest, more in pages:
            dated = self.store.marks(200, 0, aid)
            if not dated:
                continue
            here = set(keys)
            top = newest if newest else float("inf")
            low = None
            if more:
                low = min((m["ts"] for m in dated if m["key"] in here), default=None)
                if low is None:
                    continue   # no mark is in the page: nothing tells how far it reached
            for m in dated:
                if m["key"] not in here and m["ts"] <= top and (low is None or m["ts"] > low):
                    self.store.clear_mark(aid, m["key"])

    async def _op_search(self, req, conn):
        limit = _int(req.get("limit"), 20, 1, 100)
        find: dict = {"folders": ["inbox", "archive", "sent", "other"], "limit": limit}
        for name in ("text", "from"):
            value = _string(req, name, 300, line=True)
            if value:
                find[name] = value
        unread = _flag(req, "unread")
        if unread is not None:
            find["unread"] = unread
        if req.get("since") is not None:
            find["since"] = _since(req["since"])
        rows = await self.job(lambda: self.store.accounts())
        if req.get("account") is not None:
            rows = [await self._resolve(req["account"])]
        ready = [a for a in rows if a["state"] in USABLE and a["engine_id"]]
        if not ready:
            return {"messages": []}
        if not self.engine.connected:
            raise self._down()
        found = await self._ask("find", accounts=[a["engine_id"] for a in ready], **find)
        messages = found.get("messages") if isinstance(found, dict) else None
        return {"messages": await self._msgs(messages or [], {a["engine_id"]: a for a in ready})}

    # -- ops: one mail --

    async def _op_read(self, req, conn):
        """A mail's text, for the person's window or the agent's reading. Nothing is marked read, and the text
        is passed on and not kept."""
        a, eid, key = await self._target(req)
        try:
            got = await self._ask("get", account=eid, key=key)
        except Refusal as e:
            if e.code == NOT_FOUND:
                await self.job(lambda: self.store.clear_mark(a["id"], key))   # it is gone: nothing needs answering
            raise
        if not isinstance(got, dict):
            raise Refusal(ENGINE_ERROR, "Thunderbird gave no answer for that mail.")
        mark = await self.job(lambda: self.store.mark(a["id"], key))
        msg = self._msg(a["id"], got.get("message"), mark)
        if msg is None:
            raise Refusal(ENGINE_ERROR, "Thunderbird gave that mail in a shape mail cannot use.")
        plain = got.get("text") if isinstance(got.get("text"), str) else ""
        body, cut = text.body_text(plain, got.get("html") if isinstance(got.get("html"), str) else None)
        headers = got.get("headers") if isinstance(got.get("headers"), dict) else {}
        attachments = [{"part": str(p.get("part"))[:64], "name": text.sanitize_filename(str(p.get("name") or "")),
                        "content_type": _clean(p.get("content_type"), 100), "size": _int(p.get("size"), 0, 0, 2**40),
                        "inline": bool(p.get("inline"))}
                       for p in got.get("attachments") or [] if isinstance(p, dict) and p.get("part") is not None]
        prov = accts.PROVIDERS.get(a["provider"], accts.IMAP)
        return {"message": msg, "text": body, "truncated": cut, "html_only": not plain.strip(),
                "attachments": attachments, "reply_to": [x.as_dict() for x in _reply_to(headers)],
                "web_url": a["web_url"] or accts.web_link(prov, a["email"], None if key.startswith("fp:") else key)}

    async def _op_set_flags(self, req, conn):
        read, flagged = _flag(req, "read"), _flag(req, "flagged")
        if read is None and flagged is None:
            raise _bad("Say read or flagged.")
        _, eid, key = await self._target(req)
        await self._ask("mark", account=eid, key=key, **{k: v for k, v in (("read", read), ("flagged", flagged))
                                                          if v is not None})
        self._eacc_at = 0.0
        self._changed("list", "accounts")
        return {}

    async def _move(self, req, to: str) -> dict:
        a, eid, key = await self._target(req)
        await self._ask("move", account=eid, key=key, to=to)
        await self.job(lambda: self.store.clear_mark(a["id"], key))   # dealt with: it no longer needs a reply
        self._eacc_at = 0.0
        self._changed("list", "accounts")
        return {}

    async def _op_archive(self, req, conn):
        return await self._move(req, "archive")

    async def _op_trash(self, req, conn):
        return await self._move(req, "trash")

    async def _op_mark_reply(self, req, conn):
        """Needs a reply, with one line of why: the sender, subject and time are kept, never the text."""
        needs = _flag(req, "needs")
        if needs is None:
            raise _bad("Say whether it needs a reply.")
        a, eid, key = await self._target(req)
        if not needs:
            await self.job(lambda: self.store.clear_mark(a["id"], key))
            self._changed("list")
            return {"id": req["id"], "needs_reply": False, "why": None}
        why = _clean(_string(req, "why", 4000) or "", WHY_MAX)
        got = await self._ask("get", account=eid, key=key)
        found = got.get("message") if isinstance(got, dict) else None
        msg = self._msg(a["id"], found, None)
        if msg is None:
            raise Refusal(ENGINE_ERROR, "Thunderbird gave that mail in a shape mail cannot use.")
        sender = msg["from"]
        await self.job(lambda: self.store.set_mark(a["id"], key, why, sender["name"], sender["email"],
                                                   msg["subject"], msg["ts"], self.clock()))
        self._changed("list")
        return {"id": req["id"], "needs_reply": True, "why": why}

    async def _op_save_attachment(self, req, conn):
        a, eid, key = await self._target(req)
        part = _string(req, "part", 64, line=True)
        if not part:
            raise _bad("Say which attachment.")
        folder = await self._downloads_dir(_string(req, "dir", 4096))
        async with self._downloads:
            info = await self._ask("attachment", ATTACHMENT_S, account=eid, key=key, part=part)
            if not isinstance(info, dict) or not isinstance(info.get("xfer"), str):
                raise Refusal(ENGINE_ERROR, "Thunderbird did not start sending that file.")
            try:
                size = int(info.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size > bridge.BLOB_MAX:
                raise Refusal(TOO_BIG, "That file is too big to save here.")
            try:
                got = await self.engine.receive_blob(info["xfer"], bridge.BLOB_MAX, ATTACHMENT_S)
            except bridge.EngineGone:
                raise self._down() from None
            except bridge.EngineTimeout:
                raise Refusal(ENGINE_ERROR, "Thunderbird did not finish sending that file.") from None
            except bridge.EngineError as e:
                raise Refusal(e.code if e.code == TOO_BIG else ENGINE_ERROR, str(e)) from None
        try:
            saved = await self._file(_place, got, folder, str(info.get("name") or "attachment"))
        finally:
            await self._file(_remove, got)
        meta = await self._ask("get", account=eid, key=key)
        msg = self._msg(a["id"], meta.get("message") if isinstance(meta, dict) else None, None) or {}
        return {"path": str(saved), "name": saved.name, "size": saved.stat().st_size, "from": msg.get("from"),
                "subject": msg.get("subject"), "ts": msg.get("ts")}

    async def _downloads_dir(self, given: str | None) -> Path:
        """Where a saved file goes: Downloads, or a folder the person named, which must already exist and must
        not be one that holds keys or Bombadil's own state."""
        if given is None:
            folder = paths.home() / "Downloads"
            await self._file(lambda: folder.mkdir(parents=True, exist_ok=True))
            return folder
        folder = Path(given).expanduser()
        if not folder.is_absolute():
            raise _bad("Give the whole path of the folder.")
        folder = Path(os.path.realpath(folder))
        if not folder.is_dir():
            raise Refusal(NOT_FOUND, "That folder does not exist.")
        if text.is_sensitive_path(folder / "x"):
            raise Refusal(REFUSED, "Files are not saved there.")
        return folder

    # -- ops: drafts --

    def _draft_out(self, d: dict) -> dict:
        """On the worker: a draft as the window sees it."""
        a = self.store.account(d["account"]) or {"email": "", "sender": ""}
        return {"id": d["id"], "account": d["account"], "kind": d["kind"], "reply_to": d["reply_to"],
                "from": Addr(a["sender"], a["email"]).as_dict(), "to": d["to"], "cc": d["cc"], "bcc": d["bcc"],
                "subject": d["subject"], "body": d["body"],
                "attachments": [{"name": x["name"], "size": x["size"], "sha256": x["sha256"]}
                                for x in d["attachments"]],
                "fingerprint": d["fingerprint"], "created_by": d["created_by"], "warnings": d["warnings"],
                "adds": d["adds"], "state": d["state"], "receipt": self.store.receipt(d["id"]),
                "updated": d["updated"]}

    def _created_by(self, req: dict, conn: Conn | None) -> str:
        """An agent's own process cannot say it is the person: what it makes is the agent's."""
        if self._scoped(conn) or req.get("created_by") == "agent":
            return "agent"
        return "person"

    def _typed(self, req: dict, conn: Conn | None) -> str:
        """What the person typed this turn, as agentd reports it with a draft. A process inside an agent's turn
        is not agentd, and its word for what the person typed would launder any address: it is not taken."""
        typed = _string(req, "typed", 20_000) or ""
        return "" if self._scoped(conn) else typed

    async def _known(self, emails: list[str]) -> set[str]:
        """Which of these the engine knows (address books and Sent). What cannot be asked is not known."""
        if not emails or not self.engine.connected:
            return set()
        try:
            got = await self._ask("known", KNOWN_S, emails=emails)
        except Refusal:
            return set()
        return {e for e, v in got.items() if v is True} if isinstance(got, dict) else set()

    async def _trust(self, d: dict) -> tuple[set[str], set[str]]:
        """The addresses that nothing needs to be said about, and those the engine knows, for a draft."""
        thread = set(d["origin"].get("thread", []))
        recipients = [x["email"] for x in (*d["to"], *d["cc"], *d["bcc"])]
        sent = await self.job(lambda: self.store.sent_to(recipients))
        trusted = thread | set(d["typed"]) | sent
        return trusted, await self._known([e for e in dict.fromkeys(recipients) if e not in trusted])

    def _refresh_draft(self, d: dict, provider: accts.Provider, trusted: set[str], known: set[str]) -> None:
        d["warnings"] = drafts.warnings(d, trusted=trusted, known=known, provider=provider, origin=d["origin"])
        d["adds"] = drafts.adds(provider.key, d["kind"])
        d["fingerprint"] = drafts.fingerprint(d)
        d["shown_fp"], d["shown_at"] = None, None
        d["updated"] = self.clock()

    async def _op_draft(self, req, conn):
        by = self._created_by(req, conn)
        tainted = self._scoped(conn) or req.get("tainted") is True
        if await self.job(lambda: self.store.count_drafts()) >= MAX_DRAFTS:
            raise Refusal(REFUSED, "There are too many drafts open. Discard some first.")
        to, cc, bcc = (_addresses(req, n) for n in ("to", "cc", "bcc"))
        subject = _string(req, "subject", MAX_SUBJECT, line=True)
        body = _body(req) or ""
        typed = self._typed(req, conn)
        files = _paths(req, "attachments")
        kind, reply = req.get("kind"), req.get("reply_to")
        origin: dict = {}
        if reply is not None:
            try:
                aid, key = split_id(reply)
            except protocol.BadId as e:
                raise _bad(str(e)) from None
            kind = kind or "reply"
            if kind not in ("reply", "reply_all", "forward"):
                raise _bad("A mail is answered with a reply, a reply to all or a forward.")
            a = await self.job(lambda: self.store.account(aid))
            if a is None:
                raise Refusal(NO_ACCOUNT, "There is no mail account for that mail.")
        else:
            kind = kind or "new"
            if kind != "new":
                raise _bad("Say which mail to answer.")
            key = None
            a = await self._resolve(req["account"]) if req.get("account") is not None else await self._first_account()
        if a["state"] not in USABLE:
            raise Refusal(REFUSED, a["note"] or f"{a['email']} cannot send mail right now.")
        derived_to: list[Addr] = []
        derived_cc: list[Addr] = []
        derived_subject = ""
        if reply is not None:
            if not self.engine.connected:
                raise self._down()
            got = await self._ask("get", account=await self._engine_account(a), key=key)
            message = self._msg(a["id"], got.get("message") if isinstance(got, dict) else None, None)
            if message is None:
                raise Refusal(ENGINE_ERROR, "Thunderbird gave that mail in a shape mail cannot use.")
            headers = got.get("headers") if isinstance(got.get("headers"), dict) else {}
            derived_to, derived_cc, origin = _derive(kind, a["email"], message, _reply_to(headers))
            derived_subject = drafts.subject_for(kind, message["subject"])
        did = await self.job(lambda: self.store.new_draft_id())
        d = {"id": did, "account": a["id"], "kind": kind, "reply_to": msg_id(a["id"], key) if key else None,
             "to": [x.as_dict() for x in (to if to is not None else derived_to)],
             "cc": [x.as_dict() for x in (cc if cc is not None else derived_cc)],
             "bcc": [x.as_dict() for x in bcc or []],
             "subject": subject if subject is not None else derived_subject, "body": body, "attachments": [],
             "created_by": by, "tainted": tainted, "origin": origin, "state": "open", "created": self.clock()}
        said = set(text.addresses_in(typed))
        if by == "person":
            said |= {x.email for x in (*(to or []), *(cc or []), *(bcc or []))}
        d["typed"] = sorted(said)
        try:
            for path, label in files:
                c = await self._file(drafts.copy_attachment, did, path, label, by)
                d["attachments"].append(c)
        except BaseException:
            await self._file(drafts.remove_copies, did)
            raise
        provider = accts.PROVIDERS.get(a["provider"], accts.IMAP)
        trusted, known = await self._trust(d)
        self._refresh_draft(d, provider, trusted, known)
        try:
            await self.job(lambda: self.store.put_draft(d))
        except BaseException:
            await self._file(drafts.remove_copies, did)
            raise
        self._changed("drafts")
        return await self.job(self._draft_out, d)

    async def _first_account(self) -> dict:
        rows = await self.job(lambda: self.store.accounts())
        for a in rows:
            if a["state"] in USABLE:
                return a
        raise Refusal(NO_ACCOUNT, "There is no mail account ready to write from.")

    async def _draft(self, req: dict, states: tuple[str, ...] = ("open", "unknown")) -> dict:
        did = _draft_id(req)
        d = await self.job(lambda: self.store.draft(did))
        if d is None:
            raise Refusal(NOT_FOUND, "There is no such draft.")
        if d["state"] not in states:
            raise Refusal(REFUSED, _STATE_SENTENCE.get(d["state"], "That draft cannot be changed now."))
        return d

    @contextlib.asynccontextmanager
    async def _locked(self, did: str):
        """One change to a draft at a time. The lock is forgotten when nobody holds or waits for it, so ids a
        client makes up do not pile up."""
        entry = self._dlocks.setdefault(did, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0 and self._dlocks.get(did) is entry:
                del self._dlocks[did]

    async def _op_draft_get(self, req, conn):
        did = _draft_id(req)
        d = await self.job(lambda: self.store.draft(did))
        if d is None:
            raise Refusal(NOT_FOUND, "There is no such draft.")
        return await self.job(self._draft_out, d)

    async def _op_draft_edit(self, req, conn):
        """Change a draft. Whatever changes, what the view showed is no longer what there is."""
        did = _draft_id(req)
        by = self._created_by(req, conn)
        async with self._locked(did):
            d = await self._draft(req)
            to, cc, bcc = (_addresses(req, n) for n in ("to", "cc", "bcc"))
            subject, body = _string(req, "subject", MAX_SUBJECT, line=True), _body(req)
            typed = self._typed(req, conn)
            before = {x["email"] for x in (*d["to"], *d["cc"], *d["bcc"])}
            said = set(d["typed"]) | set(text.addresses_in(typed))
            for name, value in (("to", to), ("cc", cc), ("bcc", bcc)):
                if value is not None:
                    d[name] = [x.as_dict() for x in value]
                    if by == "person":
                        said |= {x.email for x in value} - before   # what the person adds is their own words
            d["typed"] = sorted(said)
            if subject is not None:
                d["subject"] = subject
            if body is not None:
                d["body"] = body
            d["tainted"] = d["tainted"] or self._scoped(conn) or req.get("tainted") is True
            gone, added = await self._edit_attachments(d, req, by)
            try:
                a = await self.job(lambda: self.store.account(d["account"]))
                provider = accts.PROVIDERS.get(a["provider"] if a else "imap", accts.IMAP)
                trusted, known = await self._trust(d)
                self._refresh_draft(d, provider, trusted, known)
                if not await self.job(lambda: self._save_edit(d)):
                    raise Refusal(REFUSED, "That draft went while it was being changed.")
            except BaseException:
                for name in added:   # the edit did not happen: the copies it made go, the ones it meant to drop stay
                    await self._file(drafts.remove_copy, did, name)
                raise
            for name in gone:
                await self._file(drafts.remove_copy, did, name)
        self._changed("drafts")
        return await self.job(self._draft_out, d)

    def _save_edit(self, d: dict) -> bool:
        """Write an edited draft unless it stopped being editable meanwhile (a press may have begun)."""
        with self.store.tx():
            now = self.store.draft(d["id"])
            if now is None or now["state"] not in ("open", "unknown"):
                return False
            self.store.put_draft({**d, "state": now["state"]})
            return True

    async def _edit_attachments(self, d: dict, req: dict, by: str) -> tuple[list[str], list[str]]:
        """Change the draft's list of attachments as asked. Returns the names of the copies to delete once the
        edit is saved, and of the copies this made, which are to go if it is not: what is saved is what stays."""
        remove = req.get("remove_attachments")
        if remove is not None and (not isinstance(remove, list) or not all(isinstance(n, str) for n in remove)):
            raise _bad("“remove_attachments” is a list of names.")
        remove = remove or []
        files = _paths(req, "add_attachments")
        kept = [x for x in d["attachments"] if x["name"] not in remove]
        if len(kept) + len(files) > drafts.ATTACHMENTS_MAX:
            raise _bad(f"A draft has at most {drafts.ATTACHMENTS_MAX} attachments.")
        added: list[dict] = []
        try:
            for path, label in files:
                added.append(await self._file(drafts.copy_attachment, d["id"], path, label, by))
        except BaseException:
            for a in added:
                await self._file(drafts.remove_copy, d["id"], a["name"])
            raise
        gone = [x["name"] for x in d["attachments"] if x["name"] in remove]
        d["attachments"] = kept + added
        return gone, [a["name"] for a in added]

    async def _op_draft_discard(self, req, conn):
        did = _draft_id(req)
        async with self._locked(did):
            d = await self._draft(req, ("open", "unknown", "discarded"))
            if d["state"] != "discarded":
                moved = await self.job(lambda: self.store.set_state(did, "discarded", self.clock(),
                                                                    was=("open", "unknown")))
                if not moved:   # a press began in the same moment: it is being sent, and is not discarded
                    raise Refusal(REFUSED, _STATE_SENTENCE["sending"])
            await self._file(drafts.remove_copies, did)
        self._changed("drafts")
        return {}

    async def _op_draft_shown(self, req, conn):
        """The window drew this draft, as it is now: the fingerprint it drew is what a press may carry."""
        self._yours(conn, "Looking at a draft")
        fp = _string(req, "fingerprint", 128)
        did = _draft_id(req)
        async with self._locked(did):
            d = await self._draft(req)
            if fp != d["fingerprint"]:
                raise Refusal(CHANGED, "That is not the draft as it is now.")
            now = self.clock()
            if not await self.job(lambda: self.store.set_shown(did, fp, now)):
                raise Refusal(CHANGED, "That is not the draft as it is now.")
        return {"id": did, "shown": True}

    # -- the press --

    async def _op_send(self, req, conn):
        """The press. A second press while the first is still going gets the first's answer: sent once."""
        self._yours_press(conn, req)
        did = _draft_id(req)
        fp = req.get("fingerprint")
        running = self._flights.get(did)
        if running is not None:
            if running[0] != fp:
                raise Refusal(CHANGED, "That draft is being sent, and that is not the one that was pressed.")
            return await asyncio.shield(running[1])
        flight = self.loop.create_future()
        self._flights[did] = (fp, flight)
        try:
            result = await self._press(did, fp, req.get("again") is True)
        except asyncio.CancelledError:
            flight.cancel()
            raise
        except Exception as e:
            flight.set_exception(e)
            flight.exception()   # said to be retrieved: nobody else may be waiting
            raise
        else:
            flight.set_result(result)
            return result
        finally:
            self._flights.pop(did, None)

    def _yours_press(self, conn: Conn | None, req: dict) -> None:
        try:
            self._yours(conn, "Sending")
        except Refusal as e:
            self._press_row({"kind": "mail", "id": _clean(req.get("id"), 32),
                             "fingerprint": _clean(req.get("fingerprint"), 64), "ok": False, "code": "agent",
                             "pid": conn.pid if conn else 0, "src": "mail"})
            raise Refusal(REFUSED, "Sending is yours: press Send in the Mail window. It cannot be done from an "
                                   "agent's turn.") from e

    async def _press(self, did: str, fp, again: bool) -> dict:
        d = await self.job(lambda: self.store.draft(did))
        if d is None:
            raise Refusal(NOT_FOUND, "There is no such draft.")
        if d["state"] == "sent":
            receipt = await self.job(lambda: self.store.receipt(did))
            return {"receipt": receipt, "already": True}
        if d["state"] == "discarded":
            raise Refusal(REFUSED, _STATE_SENTENCE["discarded"])
        if d["state"] == "sending":
            raise Refusal(REFUSED, _STATE_SENTENCE["sending"])
        if d["state"] == "unknown" and not again:
            raise Refusal(UNKNOWN_OUTCOME, UNKNOWN_SENTENCE)
        if not isinstance(fp, str) or fp != d["fingerprint"]:
            raise Refusal(CHANGED, "That is not the draft as it is now. Look at it again, then press Send.")
        if d["shown_fp"] != d["fingerprint"]:
            raise Refusal(CHANGED, "That draft has not been shown in the Mail window since it last changed. "
                                   "Open it there, then press Send.")
        if drafts.fingerprint(d) != d["fingerprint"]:
            raise Refusal(CHANGED, "That draft does not match what was recorded for it. Look at it again.")
        recipients = [*d["to"], *d["cc"], *d["bcc"]]
        if not recipients:
            raise Refusal(REFUSED, "Add who it is going to first.")
        a = await self.job(lambda: self.store.account(d["account"]))
        if a is None:
            raise Refusal(NO_ACCOUNT, "The account this draft is from is gone.")
        provider = accts.PROVIDERS.get(a["provider"], accts.IMAP)
        size = drafts.encoded_size(d)
        if size > provider.size_limit:
            raise Refusal(TOO_BIG, f"This is about {size // 1_000_000} MB once it is encoded, over the "
                                   f"{provider.size_limit // 1_000_000} MB that {provider.web_name or 'your mail provider'} "
                                   "allows.")
        if not self.engine.connected:
            raise self._down()
        eid = await self._engine_account(a)
        data = await self._file(drafts.read_verified, did, d["attachments"])
        xfers = []
        for att, blob in zip(d["attachments"], data, strict=True):
            xfer = bridge.new_xfer()
            try:
                await self.engine.send_blob(xfer, blob)
            except bridge.EngineGone:
                raise self._down() from None
            except bridge.EngineTimeout:
                raise Refusal(ENGINE_ERROR, "Thunderbird did not take the attachments in time. Nothing was sent.") \
                    from None
            except bridge.EngineError as e:
                raise Refusal(ENGINE_ERROR, f"{e} Nothing was sent.") from None
            xfers.append({"name": att["name"], "content_type": mimetypes.guess_type(att["name"])[0]
                          or "application/octet-stream", "xfer": xfer})
        payload = {"account": eid, "kind": d["kind"], "reply_to": split_id(d["reply_to"])[1] if d["reply_to"] else None,
                   "to": d["to"], "cc": d["cc"], "bcc": d["bcc"], "subject": d["subject"], "body": d["body"],
                   "attachments": xfers}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > bridge.FRAME_MAX - 1000:
            raise Refusal(TOO_BIG, "The words of this mail are too many to send in one go. Shorten it.")
        prior, now = d["state"], self.clock()
        if not await self.job(lambda: self.store.begin_send(did, fp, now)):
            raise Refusal(CHANGED, "That draft changed just before it was sent. Look at it again.")
        self._changed("drafts")
        try:
            answer = await self.engine.request("send", SEND_S, **payload)
        except asyncio.CancelledError:
            raise   # stopped mid-send: "sending" stays, and the next start calls it unknown
        except bridge.EngineError as e:
            if e.code == UNKNOWN_OUTCOME:
                await self._unknown(did)
            await self._reopen(did, prior)
            raise Refusal(ENGINE_ERROR, f"{e} Nothing was sent.") from None
        except bridge.EngineGone as e:
            if e.sent:
                await self._unknown(did)
            await self._reopen(did, prior)
            raise self._down() from None
        except bridge.EngineTimeout:
            await self._unknown(did)
        except Exception as e:  # noqa: BLE001 - after the request was written, nothing is known
            log(f"send {did}: {type(e).__name__}: {e}")
            await self._unknown(did)
        receipt = self._receipt(d, a, provider, answer)
        try:
            await self.job(lambda: self.store.finish_send(
                did, receipt, [x["email"] for x in recipients],
                split_id(d["reply_to"]) if d["reply_to"] else None, self.clock()))
        except Exception as e:  # noqa: BLE001 - it went; the record of it failed, and "sending" is the honest state
            log(f"send {did}: sent, but could not write it down: {type(e).__name__}: {e}")
        else:
            await self._file(drafts.remove_copies, did)
        self._changed("drafts", "list")
        self._broadcast({"push": "sent", "receipt": receipt})
        return {"receipt": receipt, "already": False}

    async def _unknown(self, did: str) -> None:
        """A send that may or may not have gone: said so, and never tried again by anyone but the person."""
        try:
            await self.job(lambda: self.store.set_state(did, "unknown", self.clock(), was=("sending",)))
        except Exception as e:  # noqa: BLE001 - "sending" becomes unknown at the next start anyway
            log(f"{did}: could not be marked unknown: {type(e).__name__}: {e}")
        self._changed("drafts")
        raise Refusal(UNKNOWN_OUTCOME, UNKNOWN_SENTENCE)

    async def _reopen(self, did: str, prior: str) -> None:
        await self.job(lambda: self.store.set_state(did, prior, self.clock(), was=("sending",)))
        self._changed("drafts")

    def _receipt(self, d: dict, a: dict, provider: accts.Provider, answer) -> dict:
        mid = _clean(answer.get("message_id"), 500) if isinstance(answer, dict) else ""
        ts = self.clock()
        who = _who([*d["to"], *d["cc"], *d["bcc"]])
        return {"draft": d["id"], "to": d["to"], "from": Addr(a["sender"], a["email"]).as_dict(), "ts": ts,
                "message_id": mid,
                "web": {"name": provider.web_name, "url": accts.web_link(provider, a["email"], mid or None) or ""},
                "line": f"Sent to {who} from {a['email']} · {_local_time(ts)}"}

    # -- ops: the rest --

    async def _op_known(self, req, conn):
        emails = req.get("emails")
        if not isinstance(emails, list) or len(emails) > 200:
            raise _bad("“emails” is a list of addresses.")
        wanted = []
        for e in emails:
            one = protocol.parse_addr(e) if isinstance(e, str) else None
            if one is None:
                raise _bad(f"“{str(e)[:60]}” is not an email address.")
            wanted.append(one.email)
        sent = await self.job(lambda: self.store.sent_to(wanted))
        engine = await self._known([e for e in wanted if e not in sent])
        return {e: (e in sent or e in engine) for e in wanted}

    async def _op_subscribe(self, req, conn):
        if conn is not None:
            conn.subscribed = True
        return {"subscribed": True}

    async def _op_show(self, req, conn):
        """Somebody wants the window to show something: pushed to whoever listens and kept for a window that
        is not running yet."""
        view, ident, reply = req.get("view"), req.get("id"), req.get("reply")
        if view is not None and not (isinstance(view, str) and (view in ("all", "needs_reply", "drafts")
                                                                 or view.startswith("acct:") and len(view) < 40)):
            raise _bad("The views are all, needs_reply, drafts and acct:<id>.")
        for name, value in (("id", ident), ("reply", reply)):
            if value is not None and not (isinstance(value, str) and 0 < len(value) <= protocol.MAX_KEY + 8):
                raise _bad(f"“{name}” is a mail or a draft.")
        self._show_seq += 1
        self.requested = {"view": view, "id": ident, "reply": reply, "seq": self._show_seq, "t": time.time()}
        self._broadcast({"push": "show", "view": view, "id": ident, "reply": reply, "seq": self._show_seq})
        return dict(self.requested)

    async def _op_requested(self, req, conn):
        """What was last asked to be shown, once: a window that starts after a "show" finds it here."""
        asked, self.requested = self.requested, None
        return asked if asked is not None and time.time() - asked["t"] <= REQUESTED_S else None

    async def _op_recent(self, req, conn):
        """For the Brain: sender, subject, time and where to open it, never the text."""
        limit = _int(req.get("limit"), 20, 1, 100)
        find: dict = {"folders": ["inbox"], "limit": limit}
        if req.get("since") is not None:
            find["since"] = _since(req["since"])
        rows = await self.job(lambda: self.store.accounts())
        ready = [a for a in rows if a["state"] in USABLE and a["engine_id"]]
        if not ready:
            return {"items": []}
        if not self.engine.connected:
            raise self._down()
        found = await self._ask("find", accounts=[a["engine_id"] for a in ready], **find)
        by_engine = {a["engine_id"]: a for a in ready}
        items = []
        for m in await self._msgs(found.get("messages") or [] if isinstance(found, dict) else [], by_engine):
            a = next(x for x in ready if x["id"] == m["account"])
            prov = accts.PROVIDERS.get(a["provider"], accts.IMAP)
            items.append({"id": m["id"], "account": a["email"], "from": m["from"], "subject": m["subject"],
                          "ts": m["ts"], "web_url": accts.web_link(prov, a["email"], None if m["key"].startswith("fp:")
                                                                   else m["key"])})
        return {"items": items}

    async def _op_engine_window(self, req, conn):
        action = req.get("action")
        if action not in ("stage", "hide"):
            raise _bad("The window is staged or hidden.")
        if self.process is None:
            raise self._down()
        try:
            return {"staged": bool(await self._process(self.process.stage, action == "stage"))}
        except Exception as e:  # noqa: BLE001
            log(f"engine window: {type(e).__name__}: {e}")
            raise Refusal(ENGINE_ERROR, "Thunderbird's window could not be moved.") from None

    # -- the engine: events, link, supervision --

    def _on_event(self, event: dict) -> None:
        """From the link, on the loop: queued and handled in order, the oldest dropped if they pile up."""
        self._events.append(event)
        self._event_ready.set()

    def _on_state(self, up: bool) -> None:
        if not up:
            self._cut = time.monotonic()
        self._poke.set()
        if up:
            self._spawn(self._engine_up())

    async def _engine_up(self) -> None:
        try:
            info = await self._ask("info")
            log(f"Thunderbird {info.get('app_version', '?') if isinstance(info, dict) else '?'} connected")
            await self._refresh(fresh=True, push=True)
        except Refusal as e:
            log(f"after connecting: {e}")
        if self.engine.connected:
            self._set_engine("up", "Mail is running.")
        self._changed("accounts", "list")

    async def _event_loop(self) -> None:
        while not self._stopping.is_set():
            while self._events:
                event = self._events.popleft()
                try:
                    await self._handle(event)
                except Exception as e:  # noqa: BLE001 - one event never stops the rest
                    log(f"event {str(event.get('event'))[:40]!r}: {type(e).__name__}: {e}")
            self._event_ready.clear()
            if not self._events:
                await self._event_ready.wait()

    async def _handle(self, event: dict) -> None:
        name = event.get("event")
        if name == "new_mail":
            await self._new_mail(event)
        elif name == "accounts_changed":
            await self._refresh(fresh=True, push=True)
        elif name == "sync":
            await self._synced(event)
        elif name == "counts_changed":
            self._eacc_at = 0.0
            self._changed("accounts", "list")

    async def _synced(self, event: dict) -> None:
        eid, state = event.get("account"), ENGINE_STATES.get(event.get("state"))
        if not isinstance(eid, str) or state is None:
            return
        detail = _clean(event.get("detail"), 300) if state != "ok" else ""
        changed = await self.job(self._set_sync, eid, state, detail)
        if changed:
            self._eacc_at = 0.0
            self._changed("accounts", "list")

    def _set_sync(self, eid: str, state: str, detail: str) -> bool:
        a = next((x for x in self.store.accounts() if x["engine_id"] == eid), None)
        if a is None or (a["state"], a["note"]) == (state, detail):
            return False
        self.store.update_account(a["id"], state=state, note=detail)
        return True

    async def _new_mail(self, event: dict) -> None:
        eid, messages = event.get("account"), event.get("messages")
        if not isinstance(messages, list):
            return
        by_engine = await self._by_engine()
        if eid not in by_engine:
            await self._refresh(fresh=True, push=True)
            by_engine = await self._by_engine()
        if eid not in by_engine:
            return
        fresh = []
        for m in messages[:NEW_MAIL_SCAN]:   # the first few that are new, not the first few that there are
            if isinstance(m, dict) and m.get("folder", "inbox") == "inbox" \
                    and (_float(m.get("ts")) or 0) > self.clock() - NEW_MAIL_AGE_S:
                fresh.append(m)
                if len(fresh) == NEW_MAIL_MAX:
                    break
        msgs = await self._msgs([{**m, "account": eid} for m in fresh], by_engine)
        known = await self._known(list(dict.fromkeys(m["from"]["email"] for m in msgs if m["from"]["email"])))
        for msg in msgs:
            self._broadcast({"push": "new_mail", "message": msg, "known": msg["from"]["email"] in known})
        self._eacc_at = 0.0
        self._changed("list", "accounts")

    def _set_engine(self, state: str, detail: str) -> None:
        if (state, detail) != (self.engine_state, self.engine_detail):
            self.engine_state, self.engine_detail = state, detail
            self._changed("status")
            self._push_status()

    async def _supervise(self) -> None:
        """Keep Thunderbird running while there is an account. It never raises: what goes wrong is a state."""
        while not self._stopping.is_set():
            try:
                await self._tick()
            except Exception as e:  # noqa: BLE001
                log(f"supervising Thunderbird: {type(e).__name__}: {e}")
            self._poke.clear()
            try:
                await asyncio.wait_for(self._poke.wait(), SUPERVISE_S)
            except TimeoutError:
                pass

    async def _tick(self) -> None:
        if self.process is None:   # nothing here starts Thunderbird: whichever one is connected is the engine
            if not self.engine.connected:
                self._set_engine("blocked", "Thunderbird support is not installed, so mail cannot be fetched.")
            return
        if self._restarting:
            return
        accounts = await self.job(lambda: self.store.accounts())
        running = await self._process(self.process.running)
        if not accounts:
            if running:
                await self._process(self.process.stop)
            self._running_since = None
            self._set_engine("off", "Mail is off until an account is added.")
            return
        ok, why = await self._process(self.process.available)
        if not ok:
            self._set_engine("blocked", _clean(why, 300) or "Thunderbird is not available here.")
            return
        now = time.monotonic()
        if running:
            if self._running_since is None:
                self._running_since = now
            if self.engine.connected:
                if now - self._running_since > STABLE_S:
                    self._fails = 0
                return   # _engine_up says "up" once the add-on has answered
            if self.engine_state == "up":
                self._set_engine("starting", "Thunderbird's connection dropped. Waiting for it to come back.")
            if now - max(self._running_since, self._cut) > LINK_WAIT_S:
                log("Thunderbird is running but its add-on is not connected; starting it again")
                await self._restart("its add-on did not connect")
            return
        if self._running_since is not None:   # it was running and is not: it stopped by itself
            self._fails = self._fails + 1 if now - self._running_since < STABLE_S else 0
            self._next_try = now + min(BACKOFF_MAX, BACKOFF_MIN * 2 ** max(0, self._fails - 1))
            self._running_since = None
            log(f"Thunderbird stopped; starting it again in {self._next_try - now:.0f} s")
        if now < self._next_try:
            self._set_engine("down" if self._fails >= 6 else "restarting", self._waiting(self._next_try - now))
            return
        await self._start(accounts)

    def _waiting(self, left: float) -> str:
        if self._fails >= 6:
            return "Thunderbird keeps stopping. Trying again every minute."
        return f"Thunderbird stopped. Starting it again in {max(1, round(left))} s."

    async def _start(self, accounts: list[dict]) -> None:
        self._set_engine("starting", "Starting Thunderbird.")
        try:
            await self._process(self.process.prepare)
            for a in accounts:
                await self._process(self.process.seed_account, dict(a), accts.PROVIDERS.get(a["provider"], accts.IMAP))
            await self._process(self.process.start)
        except Exception as e:  # noqa: BLE001 - a failed start is a pause and another try
            self._fails += 1
            self._next_try = time.monotonic() + min(BACKOFF_MAX, BACKOFF_MIN * 2 ** (self._fails - 1))
            detail = _clean(e, 200)
            log(f"starting Thunderbird failed: {type(e).__name__}: {detail}")
            self._set_engine("down" if self._fails >= 6 else "restarting",
                             f"Thunderbird could not start{': ' + detail if detail else ''}. "
                             + self._waiting(self._next_try - time.monotonic()))
            return
        self._running_since = time.monotonic()

    async def _restart(self, why: str) -> None:
        log(f"restarting Thunderbird: {why}")
        self._restarting = True
        self._set_engine("restarting", "Restarting Thunderbird.")
        try:
            await self._process(self.process.restart)
            self._running_since = time.monotonic()
        except Exception as e:  # noqa: BLE001
            self._fails += 1
            self._next_try = time.monotonic() + min(BACKOFF_MAX, BACKOFF_MIN * 2 ** (self._fails - 1))
            self._running_since = None
            log(f"restarting Thunderbird failed: {type(e).__name__}: {e}")
        finally:
            self._restarting = False
            self._poke.set()


_STATE_SENTENCE = {"sent": "That draft was already sent.", "discarded": "That draft was discarded.",
                   "sending": "That draft is being sent.", "unknown": UNKNOWN_SENTENCE,
                   "open": "That draft cannot be changed now."}


def _parse(raw) -> dict | None:
    try:
        req = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return req if isinstance(req, dict) else None


def _since(value) -> float:
    """A time given as epoch seconds or as a date ("2026-09-28" is that day's midnight, here)."""
    if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).timestamp()
        except ValueError:
            pass
    raise _bad("“since” is a date like 2026-09-28.")


def _wrap_cursor(ts: float, seen: list[str]) -> str:
    return base64.urlsafe_b64encode(json.dumps({"ts": ts, "seen": seen}).encode()).decode()


def _unwrap_cursor(cursor: str | None) -> tuple[float | None, set[str]]:
    if not cursor:
        return None, set()
    try:
        got = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        ts, seen = _float(got["ts"]), got["seen"]
        if ts is None or not isinstance(seen, list) or not all(isinstance(s, str) for s in seen):
            raise ValueError
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise _bad("That is not a place in the list.") from None
    return ts, set(seen)


def _reply_to(headers: dict) -> list[Addr]:
    try:
        return protocol.dedupe(protocol.parse_addrs(str(headers.get("reply-to") or "")))
    except ValueError:
        return []


def _derive(kind: str, own: str, message: dict, reply_to: list[Addr]) -> tuple[list[Addr], list[Addr], dict]:
    """Who a reply goes to, from the mail it answers: its Reply-To (else its sender), and for a reply to all the
    others it went to. And what the mail says about itself, which the draft's warnings need.

    `thread` is whom this kind of answer goes to of itself, and nobody else the mail names: a mail is written by
    whoever sent it, and an address it lists in its own To or Cc is no more the person's than one in its body."""
    sender = protocol.addr_or_raw(message["from"])
    tos = [protocol.addr_or_raw(x) for x in message["to"]]
    ccs = [protocol.addr_or_raw(x) for x in message["cc"]]
    origin = {"from": sender.email, "reply_to": [a.email for a in reply_to], "thread": []}
    if kind == "forward":   # a forward is how a mail is passed on: nobody is expected, whoever the mail names
        return [], [], origin
    main = tos if sender.email == own else (reply_to or [sender])   # answering one's own mail is writing to its recipients
    to = [a for a in main if a.email != own]
    cc: list[Addr] = []
    if kind == "reply_all":
        to = protocol.dedupe(to + [a for a in tos if a.email != own])
        cc = [a for a in ccs if a.email != own and a.email not in {t.email for t in to}]
    origin["thread"] = list(dict.fromkeys([sender.email, *(a.email for a in (*to, *cc))]))
    return to, cc, origin


def _place(source: Path, folder: Path, name: str) -> Path:
    """Move a fetched file into a folder under its real name (made safe), never over another file."""
    name = text.sanitize_filename(name)
    for _ in range(100):
        dest = text.unique_path(folder, name)
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
            shutil.copyfileobj(src, out, 1 << 20)
        return dest
    raise FileExistsError(name)


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(__doc__.split("\n\n", 1)[0])
        return 0 if argv[0] in ("-h", "--help") else 2
    engine, process, initial = None, None, []
    if os.environ.get("BOMBADIL_MAIL_ENGINE") == "fake":
        from . import fake
        try:
            drip = float(os.environ.get("BOMBADIL_MAIL_FAKE_DRIP") or 0) or None
        except ValueError:
            drip = None
        engine = fake.FakeEngine(drip=drip, signin_after=2.0)
        process = fake.FakeProcess(engine)
        initial = engine.account_rows()
    else:
        engine = bridge.EngineLink()
        try:
            from .engine import ThunderbirdProcess
            process = ThunderbirdProcess()
        except ImportError as e:
            log(f"no Thunderbird support ({e}); drafts and notes still work")
    service = Service(engine=engine, process=process, initial_accounts=initial)

    async def run():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, service.stop)
        await service.serve()

    log(f"keeping {service.db_path}, answering on {service.socket_path}")
    try:
        asyncio.run(run())
    except AlreadyRunning as e:
        log(str(e))
        return 1
    except (OSError, sqlite3.Error) as e:   # no room for mail.db, it is locked, or nowhere to answer
        log(f"cannot start: {e}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

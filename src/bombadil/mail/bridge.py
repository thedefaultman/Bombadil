"""The link to Thunderbird's add-on: requests that get answers, events that arrive unasked, and files that
travel in pieces.

The add-on reaches the service through the native-messaging host, which keeps one connection to mail.sock
and says `engine_hello` on it; the service hands that connection to `serve_engine`, and from then on it
carries engine frames (docs/MAIL.md, "Engine protocol"), one JSON object per line, both ways.

Why it is shaped the way it is:

- The service must never wait on Thunderbird. `request` waits for its own answer and no longer than its
  timeout, nothing else waits on it, and writes go to a buffer that is capped: a host that stops reading
  is a link that is gone, not a loop that is stuck.
- A request that may have been acted on is told apart from one that never left. A send that the link lost
  after it was written is an `EngineGone` with `sent` true, and the service calls that an unknown outcome,
  where one that never left is simply not done.
- A host that reconnects replaces the old link, and whatever was waiting on the old one fails at once.
- Files move in chunks of 384 KiB, one at a time, because a native-messaging frame is limited and one
  huge line would hold everything else up. A file coming in is written to disk as it arrives (never
  held whole), under a size cap and an idle timeout, and its name on disk is made from a hash, because
  the add-on chose the transfer's name.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from .. import paths
from . import protocol
from .protocol import ENGINE_ERROR, log

CHUNK = 384 << 10          # bytes of a file in one frame
MAX_PENDING = 256          # requests waiting for an answer on one link
WRITE_CAP = 8 << 20        # bytes queued for a host that is not reading before the link is dropped
BLOB_MAX = 100 << 20       # the most one fetched file may be
BLOB_IDLE_S = 30.0         # a fetch that goes this long with no piece is over
BLOB_TOTAL_S = 120.0
UNCLAIMED_S = 60.0         # pieces that arrive for a transfer nobody asked about are dropped after this
UNCLAIMED_MAX = 4 << 20    # ... and so is a transfer that nobody has asked about by the time it is this big
MAX_XFERS = 16
FRAME_MAX = 1_000_000      # bytes of one frame to the add-on: Thunderbird refuses more than 1 MiB from a host


class EngineError(Exception):
    """Thunderbird's add-on answered, and the answer is no: str() is its sentence, `code` its reason."""

    def __init__(self, code: str, sentence: str):
        super().__init__(sentence)
        self.code = code


class EngineGone(Exception):
    """There is no link, or it went away. `sent` says whether the request had been written to it: a request
    that was may have been acted on."""

    def __init__(self, detail: str = "", sent: bool = False):
        super().__init__(detail or "Thunderbird is not connected.")
        self.sent = sent


class EngineTimeout(Exception):
    """The add-on did not answer in time. The request was sent and may have been acted on."""

    sent = True

    def __init__(self, detail: str = ""):
        super().__init__(detail or "Thunderbird did not answer in time.")


def new_xfer() -> str:
    return secrets.token_hex(8)


class _Conn:
    """One connection from a host: its streams and the requests still waiting on it."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.pending: dict[int, asyncio.Future] = {}
        self.task: asyncio.Task | None = None
        self.buffer = bytearray()    # what was read and is not yet a whole line
        self.scanned = 0             # how much of it is known to hold no newline

    async def line(self) -> bytes:
        """The next line, whatever its length up to the native-messaging limit (a mail's text comes in one
        frame, far over the 1 MiB that an ordinary client of mail.sock may send). b"" at the end of the
        stream; ValueError for a line that is over the limit."""
        while True:
            at = self.buffer.find(b"\n", self.scanned)
            if at >= 0:
                line = bytes(self.buffer[:at + 1])
                del self.buffer[:at + 1]
                self.scanned = 0
                return line
            self.scanned = len(self.buffer)
            if self.scanned > protocol.NM_MAX_READ:
                raise ValueError("a frame over the limit")
            chunk = await self.reader.read(1 << 18)
            if not chunk:
                return b""   # the end; a last line with no newline was cut short and is not one
            self.buffer += chunk


class _Incoming:
    """A file arriving as `blob` events."""

    def __init__(self, xfer: str):
        self.xfer = xfer
        digest = hashlib.sha256(xfer.encode("utf-8", "replace")).hexdigest()[:32]
        self.path = paths.mail_files() / "inflight" / f"{digest}.part"
        self.file = None
        self.size = 0
        self.seq = 0
        self.limit = UNCLAIMED_MAX
        self.claimed = False
        self.done = asyncio.Event()
        self.error: Exception | None = None
        self.at = time.monotonic()


class EngineLink:
    """The service's side of the engine connection. Public surface, which `fake.FakeEngine` shares:
    `connected`, `hello`, `on_event`, `on_state`, `request`, `send_blob`, `receive_blob`, `start`, `close`."""

    def __init__(self):
        self.on_event = None        # called with each engine event, on the loop: it must not block
        self.on_state = None        # called with True when a host connects and False when the link is gone
        self.hello: dict | None = None
        self.heard = 0.0            # monotonic time of the last frame of any kind
        self._conn: _Conn | None = None
        self._next = 0
        self._incoming: dict[str, _Incoming] = {}

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.writer.is_closing()

    async def start(self) -> None:
        """Nothing to start: the host connects when Thunderbird runs."""

    async def close(self) -> None:
        if self._conn is not None:
            self._drop(self._conn, "Mail is stopping.")
        for inc in list(self._incoming.values()):
            self._discard(inc, EngineGone("Mail is stopping."))

    # -- connections --

    def attach(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> asyncio.Task:
        """A host connected: this is the link now. The one before it is dropped, and its requests fail. The
        task returned ends when this connection does."""
        old, conn = self._conn, _Conn(reader, writer)
        self._conn = conn
        self.hello = None
        self.heard = time.monotonic()
        if old is not None:
            self._drop(old, "A new Thunderbird connection replaced this one.", replaced=True)
        conn.task = asyncio.ensure_future(self._read(conn))
        self._state(True)
        return conn.task

    def _drop(self, conn: _Conn, why: str, replaced: bool = False) -> None:
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(EngineGone(why, sent=True))
        conn.pending.clear()
        try:
            conn.writer.close()
        except (OSError, RuntimeError):
            pass
        if self._conn is conn:
            self._conn = None
            for inc in list(self._incoming.values()):
                self._discard(inc, EngineGone(why, sent=True))
            if not replaced:
                self._state(False)

    def _state(self, up: bool) -> None:
        if self.on_state is not None:
            try:
                self.on_state(up)
            except Exception as e:  # noqa: BLE001 - what listens never breaks the link
                log(f"engine state handler: {type(e).__name__}: {e}")

    async def _read(self, conn: _Conn) -> None:
        try:
            while True:
                line = await conn.line()
                if not line:
                    break
                try:
                    frame = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(frame, dict):
                    continue
                self.heard = time.monotonic()
                if "event" in frame:
                    await self._event(conn, frame)
                elif isinstance(frame.get("id"), int) and not isinstance(frame["id"], bool):
                    fut = conn.pending.pop(frame["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(frame)
        except (ValueError, OSError, asyncio.LimitOverrunError) as e:
            log(f"the engine link broke: {type(e).__name__}: {e}")   # a line over the limit, or a reset
        finally:
            self._drop(conn, "Thunderbird went away.")

    async def _event(self, conn: _Conn, frame: dict) -> None:
        if frame["event"] == "blob":
            await self._blob(frame)
            return
        if frame["event"] == "hello":
            self.hello = frame
        if self.on_event is not None:
            try:
                self.on_event(frame)
            except Exception as e:  # noqa: BLE001 - what listens never breaks the link
                log(f"engine event {str(frame['event'])[:40]!r}: {type(e).__name__}: {e}")

    # -- requests --

    async def request(self, op: str, timeout: float = 10.0, **args):
        """Ask the add-on one thing and return its result. Raises EngineError (it said no), EngineGone (no
        link, or it dropped) or EngineTimeout. Never waits longer than `timeout`."""
        conn = self._conn
        if conn is None or conn.writer.is_closing():
            raise EngineGone("Thunderbird is not connected.")
        if len(conn.pending) >= MAX_PENDING:
            raise EngineError(ENGINE_ERROR, "Thunderbird is busy. Try again in a moment.")
        if conn.writer.transport.get_write_buffer_size() > WRITE_CAP:
            self._drop(conn, "Thunderbird stopped reading.")
            raise EngineGone("Thunderbird stopped reading.")
        self._next += 1
        rid = self._next
        fut = asyncio.get_running_loop().create_future()
        conn.pending[rid] = fut
        try:
            try:
                frame = (json.dumps({**args, "id": rid, "op": op}, ensure_ascii=False) + "\n").encode()
            except (UnicodeEncodeError, TypeError, ValueError):
                raise EngineError(protocol.BAD_REQUEST, "That cannot be given to Thunderbird.") from None
            if len(frame) > FRAME_MAX:
                raise EngineError(protocol.TOO_BIG, "That is too much to give Thunderbird at once.")
            try:
                conn.writer.write(frame)
            except (OSError, RuntimeError) as e:
                raise EngineGone(str(e) or "Thunderbird went away.") from e
            try:
                answer = await asyncio.wait_for(fut, timeout)
            except TimeoutError:
                raise EngineTimeout(f"Thunderbird did not answer {op} in time.") from None
        finally:
            conn.pending.pop(rid, None)
        if answer.get("ok"):
            return answer.get("result")
        raise EngineError(str(answer.get("code") or ENGINE_ERROR),
                          " ".join(str(answer.get("error") or "Thunderbird could not do that.").split())[:300])

    # -- files out --

    async def send_blob(self, xfer: str, source: bytes | str | Path, timeout: float = 30.0) -> None:
        """Give the add-on a file in chunks, one request at a time, before a `send` that names `xfer`."""
        if isinstance(source, bytes):
            await self._send_chunks(xfer, _as_async(source[i:i + CHUNK] for i in range(0, len(source), CHUNK)),
                                    timeout)
            return
        f = await asyncio.to_thread(open, source, "rb")
        try:
            async def pieces():
                while piece := await asyncio.to_thread(f.read, CHUNK):
                    yield piece
            await self._send_chunks(xfer, pieces(), timeout)
        finally:
            f.close()

    async def _send_chunks(self, xfer: str, pieces, timeout: float) -> None:
        """Each chunk goes when the next is known to exist, so the last one can say so; an empty file is one
        empty chunk."""
        seq, held = 0, b""
        async for piece in pieces:
            if held:
                await self.request("blob", timeout=timeout, xfer=xfer, seq=seq, last=False,
                                   data=base64.b64encode(held).decode("ascii"))
                seq += 1
            held = piece
        await self.request("blob", timeout=timeout, xfer=xfer, seq=seq, last=True,
                           data=base64.b64encode(held).decode("ascii"))

    # -- files in --

    async def _blob(self, frame: dict) -> None:
        xfer = frame.get("xfer")
        if not isinstance(xfer, str) or not 0 < len(xfer) <= 128:
            return
        self._gc()
        inc = self._incoming.get(xfer)
        if inc is None:
            if len(self._incoming) >= MAX_XFERS:
                log("too many files arriving at once; dropping a piece")
                return
            inc = self._incoming[xfer] = _Incoming(xfer)
        if inc.done.is_set():
            return
        try:
            data = frame.get("data")
            if frame.get("seq") != inc.seq or not isinstance(data, str):
                raise EngineError(ENGINE_ERROR, "A file arrived out of order.")
            piece = base64.b64decode(data, validate=True)
            if len(piece) > CHUNK or inc.size + len(piece) > inc.limit:
                raise EngineError(protocol.TOO_BIG, "That file is too big to fetch.")
            if inc.file is None:
                inc.file = await asyncio.to_thread(_create, inc.path)
            await asyncio.to_thread(inc.file.write, piece)
            inc.size += len(piece)
            inc.seq += 1
            inc.at = time.monotonic()
            if frame.get("last") is True:
                await asyncio.to_thread(inc.file.close)
                inc.file = None
                inc.done.set()
        except (binascii.Error, ValueError):
            self._discard(inc, EngineError(ENGINE_ERROR, "A file arrived damaged."))
        except EngineError as e:
            self._discard(inc, e)
        except OSError as e:
            self._discard(inc, EngineError(ENGINE_ERROR, f"The file could not be kept: {e.strerror or e}."))

    async def receive_blob(self, xfer: str, max_bytes: int = BLOB_MAX, timeout: float = BLOB_TOTAL_S) -> Path:
        """Wait for the file the add-on is sending under this transfer name and return where it is (under
        mail_files()/inflight; it is the caller's now). Raises EngineError, EngineGone or EngineTimeout."""
        if not isinstance(xfer, str) or not 0 < len(xfer) <= 128:
            raise EngineError(ENGINE_ERROR, "Thunderbird named that file in a way that cannot be used.")
        self._gc()
        inc = self._incoming.get(xfer)
        if inc is None:
            if len(self._incoming) >= MAX_XFERS:
                raise EngineError(ENGINE_ERROR, "Too many files are being fetched at once.")
            inc = self._incoming[xfer] = _Incoming(xfer)
        inc.claimed = True
        inc.limit = max_bytes
        if inc.size > inc.limit:
            self._discard(inc, EngineError(protocol.TOO_BIG, "That file is too big to fetch."))
        deadline = time.monotonic() + timeout
        try:
            while not inc.done.is_set():
                now = time.monotonic()
                left = min(deadline - now, inc.at + BLOB_IDLE_S - now)
                if left <= 0:
                    self._discard(inc, EngineTimeout("Thunderbird stopped sending the file."))
                    break
                try:
                    await asyncio.wait_for(inc.done.wait(), left)
                except TimeoutError:
                    continue
        except BaseException:
            self._discard(inc, EngineGone("The fetch was given up."))
            self._incoming.pop(xfer, None)   # nobody is left to remove it
            raise
        self._incoming.pop(xfer, None)
        if inc.error is not None:
            raise inc.error
        return inc.path

    def _discard(self, inc: _Incoming, error: Exception) -> None:
        """End a transfer that will not finish: the partial file goes, and whoever waits is told."""
        if inc.error is None and not inc.done.is_set():
            inc.error = error
        if inc.file is not None:
            try:
                inc.file.close()
            except OSError:
                pass
            inc.file = None
        try:
            inc.path.unlink(missing_ok=True)
        except OSError:
            pass
        if inc.claimed:
            inc.done.set()   # the waiter finds the error, and removes the entry
        else:
            self._incoming.pop(inc.xfer, None)

    def _gc(self) -> None:
        now = time.monotonic()
        for inc in list(self._incoming.values()):
            if not inc.claimed and now - inc.at > UNCLAIMED_S:
                self._discard(inc, EngineTimeout("Nobody asked for that file."))


def _create(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600), "wb")


async def _as_async(items):
    for item in items:
        yield item


async def serve_engine(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, link: EngineLink) -> None:
    """The connection after `engine_hello`: it is the link until it ends, or until another host replaces it."""
    task = link.attach(reader, writer)
    try:
        await asyncio.wait([task])
    except asyncio.CancelledError:
        task.cancel()
        raise

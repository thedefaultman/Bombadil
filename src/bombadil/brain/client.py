"""Asking bombadil-brain from anywhere: agentd, the launcher, the CLI, the Brain app's backend.

The brain answers on brain.sock in JSON lines: {"id", "op", ...} gets {"id", "ok", "result"}
or {"id", "ok": false, "error": "one plain sentence"}. Plain blocking sockets and the
standard library only, so every caller can use it without an event loop (callers on one use
asyncio.to_thread).

The brain is never required. A caller that needs an answer gets BrainUnavailable when the
brain is not there or too slow, and says so in its own line; agentd's pokes go through
notify(), which gives up after 0.3 s and never raises, so a turn never waits on the brain.
"""

import json
import socket
import time
from collections import deque

from .. import paths

NOTIFY_S = 0.3
READ_SIZE = 65536
# A focus of a big folder is a few hundred KB; anything past this is not an answer.
MAX_LINE = 64 << 20


class BrainUnavailable(Exception):
    """The brain is not running, not answering, or went away mid-answer."""

    def __init__(self, detail: str = ""):
        super().__init__("The brain is not running yet.")
        self.detail = detail


class BrainError(Exception):
    """The brain answered, and the answer is no; str() is its sentence."""


class Connection:
    """One connection to the brain, for callers that ask several things or want pushes
    (after request("subscribe")). Pushes that arrive while waiting for an answer are kept
    for push()."""

    def __init__(self, path=None, timeout: float = 2.0):
        self.path = str(path or paths.brain_socket())
        self.timeout = timeout
        self.pushes: deque[dict] = deque()
        self._buf = b""
        self._next = 0
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError as e:
            raise BrainUnavailable(str(e)) from e
        try:
            self.sock.settimeout(timeout)
            self.sock.connect(self.path)
        except (OSError, ValueError) as e:
            self.sock.close()
            raise BrainUnavailable(str(e)) from e

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def request(self, op: str, timeout: float | None = None, **args):
        """Ask one thing and return the result. Raises BrainUnavailable or BrainError."""
        self._next += 1
        rid = self._next
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        data = (json.dumps({**args, "id": rid, "op": op}, default=str) + "\n").encode()
        try:
            self._wait(deadline)
            self.sock.sendall(data)
        except OSError as e:
            raise BrainUnavailable(str(e)) from e
        while True:
            msg = self._line(deadline)
            if "push" in msg:
                self.pushes.append(msg)
                continue
            if msg.get("id") != rid:
                continue   # an answer to an earlier request that timed out here
            if msg.get("ok"):
                return msg.get("result")
            raise BrainError(str(msg.get("error") or "The brain could not answer that."))

    def push(self, timeout: float | None = None) -> dict | None:
        """The next push, or None when none came in time."""
        if self.pushes:
            return self.pushes.popleft()
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        try:
            while True:
                msg = self._line(deadline)
                if "push" in msg:
                    return msg
        except BrainUnavailable:
            return None

    def _wait(self, deadline: float) -> None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise BrainUnavailable("timed out")
        self.sock.settimeout(left)

    def _line(self, deadline: float) -> dict:
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                raw, self._buf = self._buf[:nl], self._buf[nl + 1:]
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    return msg
                continue
            if len(self._buf) > MAX_LINE:
                raise BrainUnavailable("an answer too long to be one")
            try:
                self._wait(deadline)
                chunk = self.sock.recv(READ_SIZE)
            except OSError as e:
                raise BrainUnavailable(str(e) or "timed out") from e
            if not chunk:
                raise BrainUnavailable("the brain hung up")
            self._buf += chunk


def request(op: str, timeout: float = 2.0, **args):
    """Ask the brain one thing: request("why", ref="/home/user/setup-wg.sh"). Raises
    BrainUnavailable (not running, refused, too slow) or BrainError (it said no)."""
    with Connection(timeout=timeout) as conn:
        return conn.request(op, **args)


def notify(op: str, **args) -> bool:
    """Tell the brain something and move on: at most 0.3 s, never raises. True when the
    line was handed over (not that the brain did anything with it)."""
    deadline = time.monotonic() + NOTIFY_S
    try:
        data = (json.dumps({**args, "id": 0, "op": op}, default=str) + "\n").encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(NOTIFY_S)
            s.connect(str(paths.brain_socket()))
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            s.settimeout(left)
            s.sendall(data)
            # The brain reads what is buffered even after we hang up; its answer is not needed.
            s.shutdown(socket.SHUT_WR)
        return True
    except Exception:  # noqa: BLE001 - a poke must never cost the caller anything
        return False

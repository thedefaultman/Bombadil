"""The blocking client of mail.sock, for callers that are not in an event loop: agentd's worker threads,
the launcher and the CLI.

Two ways in. `request(op, timeout, **args)` is one call on a connection of its own, and `notify(op, **args)`
is the same for a poke that must not cost anything (it gives up in 0.3 s and never raises). `Connection`
keeps one socket for several calls, and for a subscriber, which reads the pushes with `push(timeout)`.

Why it is shaped this way:

- Two exceptions and no others. `MailUnavailable` says the service was not there or stopped answering: its
  text is always "Mail is not running yet." and the technical reason is in `.detail`. `MailError` says the
  service answered and said no: its text is the service's own sentence, and `.code` is one of the codes in
  docs/MAIL.md. A caller that needs to tell "never asked" from "asked, and then silence" (a send) relies on
  the first being raised when connecting and the second when waiting: connecting is `Connection()`.
- A call that times out closes its connection. The answer may still come, and on a connection that had gone
  on to something else it would be taken for the answer to that; a closed one cannot be mistaken.
- One call at a time on a connection, so an answer is matched by its `id` alone. That is the call's own
  `id` argument when the op has one (`read`, `send`: the mail or draft), else a counter; the service echoes
  it. A caller with calls in flight together uses a connection for each.
- Pushes that arrive while a call waits are kept (the most recent PUSHES_KEPT) for `push()` to give, in order.
- Nothing here retries, and nothing here blocks past the time it was given: the timeout is for the whole
  call, not for each read.
"""

import json
import socket
import threading
import time
from collections import deque

from .. import paths

DOWN = "Mail is not running yet."
CONNECT_SECONDS = 2.0
NOTIFY_SECONDS = 0.3
MAX_LINE = 32 << 20          # an answer is at most this: a service that sends more is not one to listen to
PUSHES_KEPT = 200


class MailUnavailable(Exception):
    """The service was not there, or went quiet or away: what it would have said is not known."""

    def __init__(self, detail: str = ""):
        super().__init__(DOWN)
        self.detail = detail


class MailError(Exception):
    """The service answered, and the answer was no: str() is its sentence, `.code` says why."""

    def __init__(self, sentence: str, code: str = "error"):
        super().__init__(sentence)
        self.code = code


def _encode(msg: dict) -> bytes:
    """One request line. Text goes as it is, not as \\u escapes: the service takes a request of a megabyte at most
    and a long draft in a language that is not English would be six times as long as it is. A lone surrogate cannot
    be written as text, so that one request is written escaped and the service says what is wrong with it."""
    try:
        return (json.dumps(msg, allow_nan=False, ensure_ascii=False) + "\n").encode()
    except UnicodeEncodeError:
        return (json.dumps(msg, allow_nan=False) + "\n").encode()


class Connection:
    """One connection to mail.sock. Calls on it are one at a time (from any thread); after a failure it is
    closed and every later call says `MailUnavailable`."""

    def __init__(self, path=None, timeout: float = CONNECT_SECONDS):
        self.timeout = timeout
        self.pushes: deque[dict] = deque(maxlen=PUSHES_KEPT)
        self._buffer = b""
        self._next_id = 0
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        target = str(path or paths.mail_socket())
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(target)
        except (OSError, ValueError) as e:   # ValueError: a path too long for a socket
            sock.close()
            raise MailUnavailable(f"{type(e).__name__}: {e}") from None
        self._sock = sock

    # -- calls --

    def request(self, op: str, timeout: float | None = None, **args):
        """One call: the result, `MailError` for a no, `MailUnavailable` for no answer within `timeout`
        seconds (the connection's own timeout when not given)."""
        with self._lock:
            sock = self._sock
            if sock is None:
                raise MailUnavailable("the connection is closed")
            self._next_id += 1
            # "id" is the call's own when the op has one (a mail, a draft): the service echoes it back
            rid = args.get("id", self._next_id)
            try:
                line = _encode({**args, "id": rid, "op": op})
            except (TypeError, ValueError):
                raise MailError("That request cannot be sent.", "bad_request") from None
            deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
            try:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                sock.sendall(line)
                while True:
                    msg = self._read(sock, deadline)
                    if "push" in msg:
                        self.pushes.append(msg)
                    elif msg.get("id") == rid or (msg.get("id") is None and "ok" in msg):
                        break   # an answer with no id is the service refusing a line it could not read
            except MailUnavailable:
                self._drop()
                raise
            except TimeoutError:
                self._drop()
                raise MailUnavailable(f"no answer to {op} in time") from None
            except OSError as e:
                self._drop()
                raise MailUnavailable(f"{type(e).__name__}: {e}") from None
        if msg.get("ok") is True:
            return msg.get("result")
        raise MailError(str(msg.get("error") or "Mail could not do that."), str(msg.get("code") or "error"))

    def push(self, timeout: float | None = None) -> dict | None:
        """The next push, waiting up to `timeout` seconds for one (None: no wait). None when there is none."""
        with self._lock:
            if self.pushes:
                return self.pushes.popleft()
            sock = self._sock
            if sock is None:
                raise MailUnavailable("the connection is closed")
            deadline = time.monotonic() + (timeout or 0.0)
            try:
                while True:
                    msg = self._read(sock, deadline)
                    if "push" in msg:
                        return msg
            except TimeoutError:
                return None   # nothing came: the connection is as it was
            except MailUnavailable:
                self._drop()
                raise
            except OSError as e:
                self._drop()
                raise MailUnavailable(f"{type(e).__name__}: {e}") from None

    def close(self) -> None:
        with self._lock:
            self._drop()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- reading --

    def _drop(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._buffer = b""

    def _read(self, sock: socket.socket, deadline: float) -> dict:
        """One line of JSON as an object, waiting until `deadline` (TimeoutError past it). A line that is not
        JSON, or an object, is a service that cannot be believed."""
        while b"\n" not in self._buffer:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            sock.settimeout(left)
            chunk = sock.recv(1 << 16)
            if not chunk:
                raise MailUnavailable("the service closed the connection")
            self._buffer += chunk
            if len(self._buffer) > MAX_LINE and b"\n" not in self._buffer:
                raise MailUnavailable("the service sent a line that is too long")
        line, _, self._buffer = self._buffer.partition(b"\n")
        try:
            msg = json.loads(line)
        except ValueError:
            raise MailUnavailable("the service sent something that is not JSON") from None
        if not isinstance(msg, dict):
            raise MailUnavailable("the service sent something that is not an object")
        return msg


def request(op: str, timeout: float = CONNECT_SECONDS, **args):
    """One call on a connection of its own. `timeout` is for connecting and for the whole call together: what
    connecting used is not given again to the wait for the answer."""
    deadline = time.monotonic() + timeout
    with Connection(timeout=timeout) as conn:
        return conn.request(op, max(0.001, deadline - time.monotonic()), **args)


def notify(op: str, **args) -> bool:
    """Tell the service something and do not care to hear back: gives up in NOTIFY_SECONDS and never raises.
    True when the service answered yes."""
    try:
        request(op, NOTIFY_SECONDS, **args)
    except (MailUnavailable, MailError):
        return False
    return True

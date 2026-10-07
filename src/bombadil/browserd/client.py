"""The blocking client of browserd.sock, for callers that are not in an event loop: agentd's worker threads (the
Slack setup recipe) and the CLI. It is `connect/client.py` with this service's socket and words.

Two ways in. `request(op, timeout, **args)` is one call on a connection of its own, and `notify(op, **args)` is the
same for a poke that must not cost anything (it gives up in 0.3 s and never raises). `Connection` keeps one socket
for several calls.

Why it is shaped this way:

- Two exceptions and no others. `BrowserdUnavailable` says the service was not there or stopped answering: its text
  is always "The browser service is not running." and the technical reason is in `.detail`. `BrowserdError` says the
  service answered and said no: its text is the service's own sentence, and `.code` is one of the codes in
  browserd/service.py.
- A call that times out closes its connection, so a late answer can never be taken for the answer to a later call.
- One call at a time on a connection, so an answer is matched by its `id` alone (a counter).
- Nothing here retries, and nothing here blocks past the time it was given: the timeout is for the whole call, not
  for each read. A call to the browser can be slow (opening a page may start the browser), so the default is longer
  than the connection service's.
- `wait` is the one op with an argument that is a length of time, and the wire calls it `timeout`, which here is the
  client's own limit. A caller says how long the service waits as `seconds=`; the client sends it as `timeout` and,
  unless the caller gave one, allows the call that much and a margin.
"""

import json
import socket
import threading
import time

from .. import paths

DOWN = "The browser service is not running."
CONNECT_SECONDS = 2.0
CALL_SECONDS = 30.0
NOTIFY_SECONDS = 0.3
WAIT_MARGIN_SECONDS = 5.0
MAX_LINE = 32 << 20          # an answer is at most this: a service that sends more is not one to listen to


class BrowserdUnavailable(Exception):
    """The service was not there, or went quiet or away: what it would have said is not known."""

    def __init__(self, detail: str = ""):
        super().__init__(DOWN)
        self.detail = detail


class BrowserdError(Exception):
    """The service answered, and the answer was no: str() is its sentence, `.code` says why."""

    def __init__(self, sentence: str, code: str = "error"):
        super().__init__(sentence)
        self.code = code


def _encode(msg: dict) -> bytes:
    """One request line, as text rather than \\u escapes; a lone surrogate cannot be written as text, so that one
    request is written escaped and the service says what is wrong with it."""
    try:
        return (json.dumps(msg, allow_nan=False, ensure_ascii=False) + "\n").encode()
    except UnicodeEncodeError:
        return (json.dumps(msg, allow_nan=False) + "\n").encode()


def _wire(op: str, timeout: float | None, args: dict) -> tuple[dict, float | None]:
    """The request's arguments as the service takes them, and the client's own limit for the call."""
    if op == "wait" and "seconds" in args:
        args = dict(args)
        seconds = args.pop("seconds")
        args["timeout"] = seconds
        if timeout is None and isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            timeout = seconds + WAIT_MARGIN_SECONDS
    return args, timeout


class Connection:
    """One connection to browserd.sock. Calls on it are one at a time (from any thread); after a failure it is
    closed and every later call says `BrowserdUnavailable`."""

    def __init__(self, path=None, timeout: float = CALL_SECONDS):
        self.timeout = timeout
        self._buffer = b""
        self._next_id = 0
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        target = str(path or paths.browserd_socket())
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(min(timeout, CONNECT_SECONDS))
            sock.connect(target)
        except (OSError, ValueError) as e:   # ValueError: a path too long for a socket
            sock.close()
            raise BrowserdUnavailable(f"{type(e).__name__}: {e}") from None
        self._sock = sock

    def request(self, op: str, timeout: float | None = None, **args):
        """One call: the result, `BrowserdError` for a no, `BrowserdUnavailable` for no answer within `timeout`
        seconds (the connection's own timeout when not given)."""
        args, timeout = _wire(op, timeout, args)
        with self._lock:
            sock = self._sock
            if sock is None:
                raise BrowserdUnavailable("the connection is closed")
            self._next_id += 1
            rid = self._next_id
            try:
                line = _encode({**args, "id": rid, "op": op})
            except (TypeError, ValueError):
                raise BrowserdError("That request cannot be sent.", "bad_request") from None
            deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
            try:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                sock.sendall(line)
                while True:
                    msg = self._read(sock, deadline)
                    if msg.get("id") == rid or (msg.get("id") is None and "ok" in msg):
                        break   # an answer with no id is the service refusing a line it could not read
            except BrowserdUnavailable:
                self._drop()
                raise
            except TimeoutError:
                self._drop()
                raise BrowserdUnavailable(f"no answer to {op} in time") from None
            except OSError as e:
                self._drop()
                raise BrowserdUnavailable(f"{type(e).__name__}: {e}") from None
        if msg.get("ok") is True:
            return msg.get("result")
        raise BrowserdError(str(msg.get("error") or "The browser service could not do that."),
                            str(msg.get("code") or "error"))

    def close(self) -> None:
        with self._lock:
            self._drop()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

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
                raise BrowserdUnavailable("the service closed the connection")
            self._buffer += chunk
            if len(self._buffer) > MAX_LINE and b"\n" not in self._buffer:
                raise BrowserdUnavailable("the service sent a line that is too long")
        line, _, self._buffer = self._buffer.partition(b"\n")
        try:
            msg = json.loads(line)
        except ValueError:
            raise BrowserdUnavailable("the service sent something that is not JSON") from None
        if not isinstance(msg, dict):
            raise BrowserdUnavailable("the service sent something that is not an object")
        return msg


def request(op: str, timeout: float | None = None, **args):
    """One call on a connection of its own. `timeout` is for connecting and for the whole call together: what
    connecting used is not given again to the wait for the answer."""
    _, limit = _wire(op, timeout, args)
    limit = CALL_SECONDS if limit is None else limit
    deadline = time.monotonic() + limit
    with Connection(timeout=limit) as conn:
        return conn.request(op, max(0.001, deadline - time.monotonic()), **args)


def notify(op: str, **args) -> bool:
    """Tell the service something and do not care to hear back: gives up in NOTIFY_SECONDS and never raises.
    True when the service answered yes."""
    try:
        request(op, NOTIFY_SECONDS, **args)
    except (BrowserdUnavailable, BrowserdError):
        return False
    return True

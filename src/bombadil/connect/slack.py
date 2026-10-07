"""The Slack driver: one connection to one workspace, through the person's own private app (docs/CONNECT.md,
"Slack, through a private internal app").

Two tokens come from the app the person made: an app-level token (`xapp-`) that opens a Socket Mode connection,
and the person's own User OAuth token (`xoxp-`) that reads and, when pressed for, posts as them. With neither
or one of them the driver is in `setup` and `steps()` says what is missing. With both it asks `auth.test` who
and where it is, opens the socket, and turns Slack's `message` events into the Messages that are for the person.

The rules it keeps:

- Reading is quiet. It calls `auth.test`, `users.info`, `conversations.{list,info,history,replies}`,
  `chat.getPermalink` and `apps.connections.open`; nothing is marked read, joined or reacted to. The one write,
  `chat.postMessage`, is made by `perform` alone, which the service calls only for a press.
- Nothing is kept on disk. Messages live in a ring of 200 in this process, names in a bounded dict for a day, and
  the threads the person is in in a bounded set of 500. Tokens live in `self.secrets` and nowhere else: they are
  sent in the Authorization header (never in an address), and no sentence, log line or exception text made here
  holds one.
- Slack's envelopes are acknowledged by `envelope_id` before anything else is done with them; what they carry
  goes to one worker, in order, so a slow name lookup never holds up an acknowledgement.
- A message is for the person when it is in a direct or group direct conversation, when it mentions them by name
  (`<@their id>`; `@here` and `@channel` are not a mention), or when it replies in a thread they posted in. Their
  own words and Slack's housekeeping (joins, edits, pins) are not.
- A press that may or may not have gone (the connection broke after the request was written, no answer in 20 s)
  is an `UnknownOutcome`; one that certainly did not (429, a refusal, no connection made) is a `DriverError`.
  Nothing retries a write.
- Problems with the connection itself become states: a token Slack no longer accepts or a missing permission is
  `error`, an administrator's approval wall is `blocked`, and Slack not being reachable is `error` too, until the
  next successful connection says `ok` again.

What it does not do: keep a message past the ring, mark anything read, look at threads or channels it was not
told about (mentions and threads come from events only; only direct conversations are caught up after a start
or a long gap), or retry a post.

`conversation.name` is `#launch` for a channel (the hash included), the other person's name for a direct message,
and the members' handles for a group direct message. A message of the person's own, which only `thread()` and the
ring can hold, has the reason "you wrote this" so a card can tell it is theirs.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .. import ws
from . import manifest
from .driver import Driver, DriverError, Secrets, UnknownOutcome
from .protocol import (
    AUTH,
    BAD_REQUEST,
    BLOCKED,
    MAX_NAME,
    NOT_FOUND,
    RATE_LIMITED,
    REFUSED,
    SERVICE_DOWN,
    Refusal,
    log,
    one_line,
    plain_text,
    slack_ref,
    split_slack_ref,
    web_url,
)

TIMEOUT = 20.0                  # every call to the Web API
CONCURRENT = 5                  # Web API requests in flight at once
RETRY_AFTER_MAX = 30.0          # the longest a 429's Retry-After is waited out
MIN_BACKOFF, MAX_BACKOFF = 1.0, 60.0
STABLE = 10.0                   # a socket that lived this long (by the injected clock) was a good one
QUICK_DISCONNECTS = 3           # Slack's own `disconnect`s answered at once, before the back-off applies
UNREACHABLE_AFTER = 5           # failed connections in a row before the person is told
PING_EVERY, PING_TIMEOUT = 30.0, 30.0   # a ping, then this long for any byte back before the socket is dropped
START_WAIT = 45.0               # the longest start() waits for the first connection to succeed or fail
EVENT_QUEUE = 500

RING = 200                      # messages kept for messages()
SEEN = 1000                     # refs already delivered, so a backfill and an event never both go out
THREADS = 500                   # threads the person is known to be in
LOOKED = 1000                   # threads already looked at once
NAMES = 500
CONVERSATIONS = 500
NAME_TTL = 86400.0
MISS_TTL = 600.0                # a lookup that failed is not tried again for this long
MAX_MENTION_LOOKUPS = 10        # names looked up for one message's mentions

BACKFILL_CONVERSATIONS = 100
BACKFILL_LIMIT = 20
BACKFILL_DAYS = 14
CATCH_UP_GAP = 60.0             # a reconnect after this long a gap goes through the direct messages again
MAX_REPLY = 3000

AUTH_SENTENCE = "Slack no longer accepts this connection. Set it up again."
APPROVAL_SENTENCE = "An administrator of your Slack workspace has to approve the app before it can connect."
LIMITED_SENTENCE = "An administrator of your Slack workspace has limited what this connection may do."
UNREACHABLE_SENTENCE = "I can't reach Slack. I'll keep trying."

# Slack's error words, by what they mean for a connection. Anything else is a refusal of that one call.
_AUTH = {"invalid_auth", "token_revoked", "account_inactive", "not_authed", "token_expired"}
_BLOCKED = {"app_approval", "not_allowed_token_type", "restricted_action"}
_UNSURE = {"fatal_error", "internal_error"}          # Slack says some of the work may have been done
_UNAVAILABLE = {"service_unavailable", "request_timeout"}
_GONE = {"channel_not_found", "thread_not_found", "message_not_found", "not_in_channel"}
_REFUSALS = {
    "channel_not_found": "Slack can't find that conversation any more.",
    "thread_not_found": "The message you are replying to is gone.",
    "message_not_found": "The message you are replying to is gone.",
    "not_in_channel": "You are not in that channel, so Slack won't post there.",
    "is_archived": "That channel is archived, so nothing can be posted in it.",
    "msg_too_long": "Slack says that message is too long.",
    "no_text": "Slack says there is nothing to post.",
    "cannot_reply_to_message": "Slack does not allow a reply to that message.",
    "restricted_action": "Your workspace does not let you post there.",
}

# Message subtypes that are somebody saying something; every other subtype is Slack's own housekeeping.
_SUBTYPES = {None, "thread_broadcast", "file_share", "me_message", "bot_message"}
_KINDS = {"im": "dm", "mpim": "group", "channel": "channel", "group": "private"}
_PREFIX_KINDS = {"D": "dm", "C": "channel"}

_ID = re.compile(r"[A-Z0-9]{2,30}")
_TS = re.compile(r"\d{9,11}\.\d{1,9}")
_SLACK_ERROR = re.compile(r"[a-z0-9_]{1,64}")
_SCOPES = re.compile(r"[^a-z0-9:,._-]")
_TOKEN = re.compile(r"xox[a-z]-[\w-]+|xapp-[\w-]+", re.IGNORECASE)
_TAG = re.compile(r"<([^<>\n]{1,2000})>")
_USER_TAG = re.compile(r"<@([A-Z0-9]{2,30})(?:\|[^<>\n]*)?>")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_ENTITY = re.compile(r"&(amp|lt|gt);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">"}
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")
_SPECIAL = {"here": "@here", "channel": "@channel", "everyone": "@everyone"}

# A request that failed on our side before a byte of it could have reached Slack.
_UNSENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError,
           httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.InvalidURL)


class _Failure(Exception):
    """A Web API call that gave no answer to use. `kind` is auth, scope, blocked (the connection itself is bad),
    rate, down, unsure (Slack says some of it may have happened) or refused (this call only). The text of the
    exception is the kind and nothing foreign, so it can be logged or printed whole."""

    def __init__(self, kind: str, error: str = "", *, needed: str = "", retry_after: float = 0.0,
                 sent: bool = True):
        super().__init__(kind)
        self.kind, self.error, self.needed, self.retry_after, self.sent = kind, error, needed, retry_after, sent

    @property
    def fatal(self) -> bool:
        return self.kind in ("auth", "scope", "blocked")


class _Lru:
    """A dict that forgets its least recently used key once it holds more than `size`."""

    def __init__(self, size: int):
        self.size = size
        self._d: OrderedDict = OrderedDict()

    def get(self, key, default=None):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return default

    def put(self, key, value=True) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def __contains__(self, key) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)


def _slack_error(value) -> str:
    return value if isinstance(value, str) and _SLACK_ERROR.fullmatch(value) else "unknown_error"


def _kind_of(error: str) -> str:
    if error in _AUTH:
        return "auth"
    if error == "missing_scope":
        return "scope"
    if error in _BLOCKED:
        return "blocked"
    if error in ("ratelimited", "rate_limited"):
        return "rate"
    if error in _UNAVAILABLE:
        return "down"
    if error in _UNSURE:
        return "unsure"
    return "refused"


def _state_for(f: _Failure) -> tuple[str, str]:
    """The state a failure of the connection itself puts the driver in, and the sentence that says why."""
    if f.kind == "scope":
        scope = _SCOPES.sub("", f.needed)[:100]
        what = f"the {scope} permission" if scope else "a permission it needs"
        return "error", f"The Slack app lacks {what}. Set it up again from the manifest."
    if f.kind == "blocked":
        return "blocked", LIMITED_SENTENCE if f.error == "restricted_action" else APPROVAL_SENTENCE
    return "error", AUTH_SENTENCE


def _is_id(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _person_name(user) -> str:
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    for value in (profile.get("real_name"), user.get("real_name"), profile.get("display_name"), user.get("name")):
        name = one_line(value, MAX_NAME)
        if name:
            return name
    return ""


def _group_name(raw, me: str) -> str:
    """`mpdm-priya--marcus--alex-1` as the handles of the others in it ("priya, marcus", for alex)."""
    if not isinstance(raw, str) or not raw.startswith("mpdm-"):
        return "a group message"
    names = re.sub(r"-\d+$", "", raw[len("mpdm-"):]).split("--")
    return one_line(", ".join(n for n in names if n and n != me), MAX_NAME) or "a group message"


def _entities(text: str) -> str:
    return _ENTITY.sub(lambda m: _ENTITIES[m.group(1)], text)


def _escaped(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackDriver(Driver):
    kind = "slack"
    secret_rules = {"app_token": "xapp-", "user_token": "xoxp-"}

    def __init__(self, conn: dict, secrets: Secrets, emit: Callable[[dict], None], clock: Callable[[], float],
                 *, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        super().__init__(conn, secrets, emit, clock)
        self._sleep = sleep
        self.api = os.environ.get("BOMBADIL_SLACK_API", "https://slack.com/api").rstrip("/")
        self.team_id: str | None = None
        self.user_id: str | None = None
        self._nick = ""                                       # the person's own handle, as Slack spells it
        self._code = ""                                       # what a press is told while the state is not ok
        self._name = one_line(conn.get("name"), MAX_NAME)
        self._told = (self.state, self.note, self._name)     # what the service was last sent
        self._app: str | None = None                          # the tokens the running connection uses
        self._user: str | None = None
        self._closed = False
        self._identified = False
        self._down = False                                    # the "can't reach Slack" error is showing
        self._alive: float | None = None                      # when the last socket ended
        self._http: httpx.AsyncClient | None = None
        self._gate = asyncio.Semaphore(CONCURRENT)
        self._lifecycle = asyncio.Lock()
        self._tasks: set[asyncio.Future] = set()
        self._run_task: asyncio.Future | None = None
        self._backfill_task: asyncio.Future | None = None
        self._sock: ws.WebSocket | None = None
        self._queue: asyncio.Queue = asyncio.Queue(EVENT_QUEUE)
        self._first = asyncio.Event()
        self._ring: dict[str, dict] = {}
        self._seen = _Lru(SEEN)
        self._threads = _Lru(THREADS)
        self._looked = _Lru(LOOKED)
        self._names = _Lru(NAMES)
        self._convs = _Lru(CONVERSATIONS)
        self._links = _Lru(RING)

    # -- life --

    async def start(self) -> None:
        self._closed = False
        async with self._lifecycle:
            app, user = self._tokens()
            if app and user:
                await self._begin(app, user)
            else:
                self._wait_for_tokens(app, user)

    async def stop(self) -> None:
        self._closed = True
        await self._halt()
        http, self._http = self._http, None
        if http is not None:
            with contextlib.suppress(Exception):
                await http.aclose()

    async def secrets_changed(self) -> None:
        if self._closed:
            return
        async with self._lifecycle:
            app, user = self._tokens()
            if not (app and user):
                await self._halt()
                self._app = self._user = None
                self._wait_for_tokens(app, user)
                return
            task = self._run_task
            running = task is not None and not (task.done() or task.cancelling())
            if running and (app, user) == (self._app, self._user):
                return
            await self._halt()
            await self._begin(app, user)

    def _tokens(self) -> tuple[str | None, str | None]:
        found = []
        for name, prefix in self.secret_rules.items():
            try:
                value = self.secrets.get(name)
            except Exception:
                value = None
            found.append(value if isinstance(value, str) and len(value) > len(prefix)
                         and value.startswith(prefix) else None)
        return found[0], found[1]

    def _wait_for_tokens(self, app, user) -> None:
        self._identified = False
        if not app and not user:
            note = "Slack is not connected yet. It needs two tokens from an app of your own."
        elif not app:
            note = "Slack is missing the app-level token."
        else:
            note = "Slack is missing the User OAuth token."
        self._set_state("setup", note, code=REFUSED)

    async def _begin(self, app: str, user: str) -> None:
        self._app, self._user = app, user
        self._identified = False
        self._first = asyncio.Event()
        self._queue = asyncio.Queue(EVENT_QUEUE)
        self._spawn(self._work())
        self._run_task = self._spawn(self._run())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._first.wait(), START_WAIT)

    async def _halt(self) -> None:
        me = asyncio.current_task()
        tasks = [t for t in self._tasks if t is not me]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
        self._run_task = self._backfill_task = None

    def _abort(self) -> None:
        """Cancel everything from inside one of its own tasks (a failure of the connection itself)."""
        for task in list(self._tasks):
            task.cancel()

    def _spawn(self, coro) -> asyncio.Future:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def steps(self) -> list[dict]:
        if self.state != "setup":
            return []
        app, user = self._tokens()
        steps = []
        if not app and not user:
            steps.append({"id": "create", "open": manifest.creation_url(),
                          "say": "Create the app in your Slack workspace from the manifest."})
        if not app:
            steps.append({"id": "app_token",
                          "say": "Make an app-level token for it with the connections:write permission."})
        if not user:
            steps.append({"id": "install", "say": "Install the app to your workspace."})
            steps.append({"id": "user_token", "say": "Copy its User OAuth Token."})
        return steps

    def owns(self, kind: str, target: str) -> bool:
        if kind != "slack_reply" or not self.team_id:
            return False
        try:
            return split_slack_ref(target)[0] == self.team_id
        except Refusal:
            return False

    def can_post(self) -> bool:
        return self.state == "ok"

    def reads(self) -> str:
        # Until `auth.test` has answered, the record's name is the service's placeholder, "Slack".
        where = f"in {self._name}" if self._name and self._name.lower() != "slack" else "in your workspace"
        return f"Your direct messages, the messages that mention you and the threads you are in, {where}."

    # -- state --

    def _emit(self, push: dict) -> None:
        if self._closed:
            return
        try:
            self.emit(push)
        except Exception as e:
            self._log(f"the service could not take a push ({type(e).__name__})")

    def _set_state(self, state: str, note: str, name: str | None = None, code: str = "") -> None:
        if name and name != self._name:
            self._name = name
            self.conn["name"] = name
        self.state, self.note, self._code = state, note, code
        told = (state, note, self._name)
        if told == self._told:
            return
        push = {"push": "state", "state": state, "note": note}
        if self._name != self._told[2]:
            push["name"] = self._name
        self._told = told
        self._emit(push)

    def _enter(self, f: _Failure) -> None:
        """The connection itself is bad (a token, a permission, an approval): say so and stop trying."""
        self._set_state(*_state_for(f), code={"auth": AUTH, "scope": REFUSED, "blocked": BLOCKED}[f.kind])
        self._log(f"the connection is {self.state}: {f.error or f.kind}")

    def _unreachable(self) -> None:
        self._down = True
        self._set_state("error", UNREACHABLE_SENTENCE, code=SERVICE_DOWN)

    def _log(self, text: str) -> None:
        text = str(text)
        for secret in (self._app, self._user):
            if secret:
                text = text.replace(secret, "…")
        log("slack: " + _TOKEN.sub("…", text))

    # -- the Web API --

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT))
        return self._http

    async def _call(self, method: str, token: str, params: dict | None = None,
                    client: httpx.AsyncClient | None = None) -> dict:
        """One call. The answer's JSON when Slack says ok, otherwise a _Failure. Tokens go in the header only."""
        try:
            r = await (client or self._client()).post(
                f"{self.api}/{method}", data=params or {}, headers={"Authorization": f"Bearer {token}"})
        except _UNSENT:
            raise _Failure("down", sent=False) from None
        except (httpx.HTTPError, RuntimeError):
            raise _Failure("down") from None
        if r.status_code == 429:
            try:
                wait = min(max(float(r.headers.get("retry-after", "")), 0.0), 3600.0)
            except ValueError:
                wait = RETRY_AFTER_MAX
            raise _Failure("rate", "ratelimited", retry_after=wait, sent=False)
        if r.status_code == 503:
            raise _Failure("down", sent=False)
        if r.status_code >= 500:
            raise _Failure("down")
        try:
            body = r.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise _Failure("down" if r.status_code < 400 else "refused", "unreadable")
        if body.get("ok") is not True:
            error = _slack_error(body.get("error"))
            kind = _kind_of(error)
            needed = body.get("needed") if isinstance(body.get("needed"), str) else ""
            raise _Failure(kind, error, needed=needed, sent=error not in _UNAVAILABLE)
        return body

    async def _once(self, method: str, token: str, params: dict | None) -> dict:
        async with self._gate:
            return await self._call(method, token, params)

    async def _read(self, method: str, params: dict | None = None, token: str | None = None) -> dict:
        """A read: at most CONCURRENT at once. A 429 is waited out (at most RETRY_AFTER_MAX) and tried once more."""
        token = token or self._user
        try:
            return await self._once(method, token, params)
        except _Failure as f:
            if f.kind != "rate":
                raise
            wait = min(f.retry_after, RETRY_AFTER_MAX)
        await self._sleep(wait)
        return await self._once(method, token, params)

    async def _try(self, method: str, params: dict | None = None) -> dict | None:
        """A read whose failure matters to nobody but that call: None, unless the connection itself is bad."""
        try:
            return await self._read(method, params)
        except _Failure as f:
            if f.fatal:
                raise
            return None

    # -- keeping connected --

    async def _run(self) -> None:
        fails = quick = 0
        try:
            while not self._closed:
                lived, outcome = 0.0, "failed"
                try:
                    if not self._identified:
                        await self._identify()
                    sock = await self._open()
                    lived, outcome = await self._session(sock)
                except _Failure as f:
                    if f.fatal:
                        self._enter(f)
                        self._abort()
                        return
                    self._log(f"could not connect ({f.error or f.kind})")
                except ws.WebSocketError:
                    self._log("could not open the socket")
                except Exception as e:
                    self._log(f"the connection failed ({type(e).__name__})")
                if lived >= STABLE:
                    fails = quick = 0
                if outcome == "disconnect" and quick < QUICK_DISCONNECTS:
                    quick += 1
                    delay = 0.0
                else:
                    delay = min(MAX_BACKOFF, MIN_BACKOFF * 2 ** fails)
                    fails += 1
                if (fails >= UNREACHABLE_AFTER or not self._identified) and not self._down:
                    self._unreachable()
                self._first.set()
                if delay:
                    self._log(f"reconnecting in {delay:g} s")
                    await self._sleep(delay)
        finally:
            self._first.set()

    async def _identify(self) -> None:
        body = await self._read("auth.test")
        team, user = body.get("team_id"), body.get("user_id")
        if not (_is_id(team) and _is_id(user)):
            raise _Failure("refused", "unreadable")
        self.team_id, self.user_id = team, user
        self._nick = one_line(body.get("user"), MAX_NAME)
        self._identified = True
        self._down = False
        self._set_state("ok", "", one_line(body.get("team"), MAX_NAME) or self._name)

    async def _open(self) -> ws.WebSocket:
        body = await self._read("apps.connections.open", token=self._app)
        url = body.get("url")
        if not (isinstance(url, str) and url.startswith(("ws://", "wss://"))):
            raise _Failure("refused", "unreadable")
        return await ws.connect(url)

    async def _session(self, sock: ws.WebSocket) -> tuple[float, str]:
        """Read one socket until it ends: (how long it was up, why it ended)."""
        heard = [0]
        original = sock.reader.feed_data

        def feed(data):
            heard[0] += 1
            original(data)

        # ws.py hands back only whole messages, and answers pings and drops pongs itself; counting the bytes that
        # arrive is how a socket that has gone quiet (a lid closed, a router that forgot us) is told from an idle one.
        sock.reader.feed_data = feed
        self._sock = sock
        keep = asyncio.ensure_future(self._keepalive(sock, heard))
        began = None
        outcome = "dropped"
        try:
            while True:
                message = self._parse(await sock.recv())
                if message is None:
                    continue
                envelope = message.get("envelope_id")
                if isinstance(envelope, str) and envelope:
                    await sock.send(json.dumps({"envelope_id": envelope}))
                kind = message.get("type")
                if kind == "hello":
                    began = self.clock()
                    self._hello()
                elif kind == "disconnect":
                    outcome = "disconnect"
                    break
                elif kind == "events_api":
                    self._enqueue(message)
        except ws.WebSocketError:
            pass
        finally:
            keep.cancel()
            self._sock = None
            self._alive = self.clock()
            with contextlib.suppress(Exception):
                await sock.close()
            await asyncio.gather(keep, return_exceptions=True)
        return (self.clock() - began if began is not None else 0.0), outcome

    @staticmethod
    def _parse(raw) -> dict | None:
        if not isinstance(raw, str):
            return None
        try:
            message = json.loads(raw)
        except ValueError:
            return None
        return message if isinstance(message, dict) else None

    async def _keepalive(self, sock: ws.WebSocket, heard: list[int]) -> None:
        try:
            while True:
                await asyncio.sleep(PING_EVERY)
                mark = heard[0]
                await sock.ping()
                await asyncio.sleep(PING_TIMEOUT)
                if heard[0] == mark:
                    self._log("the socket went quiet")
                    await sock.close()
                    return
        except ws.WebSocketError:
            return

    def _hello(self) -> None:
        if self._down:
            self._down = False
            self._set_state("ok", "")
        self._first.set()
        gap = None if self._alive is None else self.clock() - self._alive
        if gap is None or gap > CATCH_UP_GAP:
            if self._backfill_task is None or self._backfill_task.done():
                self._backfill_task = self._spawn(self._guard(self._backfill()))

    def _enqueue(self, envelope: dict) -> None:
        payload = envelope.get("payload")
        event = payload.get("event") if isinstance(payload, dict) else None
        if isinstance(event, dict) and event.get("type") == "message":
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                self._log("too many events at once; one was dropped")

    async def _work(self) -> None:
        while True:
            event = await self._queue.get()
            await self._guard(self._handle(event))

    async def _guard(self, coro) -> None:
        """Run work that belongs to no caller: a failure of the connection itself ends it, any other is logged."""
        try:
            await coro
        except _Failure as f:
            if f.fatal:
                self._enter(f)
                self._abort()
            else:
                self._log(f"a call failed ({f.error or f.kind})")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._log(f"something unexpected happened ({type(e).__name__})")

    # -- what is for the person --

    async def _handle(self, event: dict) -> None:
        channel = event.get("channel")
        message = await self._for_me(event, channel, _KINDS.get(event.get("channel_type")))
        if message:
            self._deliver(message)

    async def _for_me(self, ev: dict, channel, kind: str | None, other: str | None = None) -> dict | None:
        """The Message for an event or a history entry, or None when it is not for the person."""
        ts = ev.get("ts")
        if ev.get("subtype") not in _SUBTYPES or not _is_id(channel) or not (isinstance(ts, str)
                                                                              and _TS.fullmatch(ts)):
            return None
        sender = ev.get("user") if _is_id(ev.get("user")) else None
        thread_ts = ev.get("thread_ts") if isinstance(ev.get("thread_ts"), str) else None
        reply = bool(thread_ts and _TS.fullmatch(thread_ts) and thread_ts != ts)
        if sender and sender == self.user_id:
            self._threads.put((channel, thread_ts if reply else ts))
            return None
        text = ev.get("text")
        if not isinstance(text, str) or not text.strip() or slack_ref(self.team_id, channel, ts) in self._seen:
            return None
        kind = kind or _PREFIX_KINDS.get(channel[:1])
        if kind is None:
            kind = (await self._conversation(channel))["kind"]
        mention = re.search(rf"<@{re.escape(self.user_id)}(?:\|[^<>\n]*)?>", text) is not None
        if kind in ("dm", "group"):
            reason = "direct message"
        elif mention:
            reason = "mentioned you"
        elif reply and await self._in_thread(channel, thread_ts):
            reason = "a thread you are in"
        else:
            return None
        return await self._build(ev, channel, kind, sender, other or sender, text, reason, mention)

    async def _in_thread(self, channel: str, root: str) -> bool:
        """Whether the person has posted in this thread: known from their own events, or looked at once."""
        key = (channel, root)
        if key in self._threads:
            return True
        if key in self._looked:
            return False
        self._looked.put(key)
        body = await self._try("conversations.replies", {"channel": channel, "ts": root, "limit": 200})
        for m in (body or {}).get("messages") or []:
            if isinstance(m, dict) and m.get("user") == self.user_id:
                self._threads.put(key)
                return True
        return False

    async def _build(self, ev: dict, channel: str, kind: str | None, sender: str | None, other: str | None,
                     text: str, reason: str, mention: bool) -> dict | None:
        conv = await self._conversation(channel, kind, other if kind == "dm" else None)
        if sender:
            name = await self._user_name(sender)
        else:
            profile = ev.get("bot_profile") if isinstance(ev.get("bot_profile"), dict) else {}
            name = one_line(ev.get("username") or profile.get("name"), MAX_NAME) or "An app"
        plain = plain_text(await self._plain(text))
        if not plain:
            return None
        ts = ev["ts"]
        thread_ts = ev.get("thread_ts")
        reply = isinstance(thread_ts, str) and thread_ts != ts and _TS.fullmatch(thread_ts) is not None
        who = sender or (ev.get("bot_id") if _is_id(ev.get("bot_id")) else "")
        return {
            "ref": slack_ref(self.team_id, channel, ts),
            "source": "slack",
            "connection": self.conn.get("id", ""),
            "conversation": {"id": channel, "name": conv["name"], "kind": conv["kind"]},
            "thread": slack_ref(self.team_id, channel, thread_ts) if reply else None,
            "from": {"id": who, "name": name},
            "text": plain,
            "ts": float(ts),
            "mentions_me": mention,
            "reason": reason,
            "unread": True,
            "web_url": None,
        }

    def _deliver(self, message: dict) -> None:
        ref = message["ref"]
        if ref in self._seen:
            return
        self._seen.put(ref)
        self._ring[ref] = message
        while len(self._ring) > RING:
            del self._ring[min(self._ring, key=lambda r: self._ring[r]["ts"])]
        self._emit({"push": "message", "message": copy.deepcopy(message)})

    # -- names and conversations --

    async def _user_name(self, uid) -> str:
        if not _is_id(uid):
            return "Someone"
        hit = self._names.get(uid)
        now = self.clock()
        if hit and hit[1] > now:
            return hit[0]
        body = await self._try("users.info", {"user": uid})
        user = body.get("user") if body else None
        name = _person_name(user) if isinstance(user, dict) else ""
        self._names.put(uid, (name or "Someone", now + (NAME_TTL if name else MISS_TTL)))
        return name or "Someone"

    async def _conversation(self, channel: str, kind: str | None = None, other: str | None = None) -> dict:
        """{"id", "name", "kind"} of a conversation, from what is known or one `conversations.info`."""
        hit = self._convs.get(channel)
        now = self.clock()
        if hit and hit[1] > now:
            return hit[0]
        ttl = NAME_TTL
        if kind == "dm" and other:
            conv = {"id": channel, "kind": "dm", "name": await self._user_name(other)}
        else:
            body = await self._try("conversations.info", {"channel": channel})
            info = body.get("channel") if body else None
            if isinstance(info, dict):
                conv = await self._describe(channel, info)
            else:
                ttl = MISS_TTL
                kind = kind or _PREFIX_KINDS.get(channel[:1]) or "private"
                conv = {"id": channel, "kind": kind, "name": {
                    "dm": "a direct message", "group": "a group message", "channel": "a channel"}.get(
                        kind, "a private channel")}
        self._convs.put(channel, (conv, now + ttl))
        return conv

    async def _describe(self, channel: str, info: dict) -> dict:
        if info.get("is_im"):
            return {"id": channel, "kind": "dm", "name": await self._user_name(info.get("user"))}
        if info.get("is_mpim"):
            return {"id": channel, "kind": "group", "name": _group_name(info.get("name"), self._nick)}
        kind = "private" if info.get("is_private") or info.get("is_group") else "channel"
        name = one_line(info.get("name"), MAX_NAME)
        return {"id": channel, "kind": kind, "name": f"#{name}" if name else "a channel"}

    async def _plain(self, text: str) -> str:
        """Slack's markup as words: mentions, channel links and links; `&amp; &lt; &gt;` undone."""
        ids = list(dict.fromkeys(m.group(1) for m in _USER_TAG.finditer(text)))[:MAX_MENTION_LOOKUPS]
        names = dict(zip(ids, await asyncio.gather(*(self._user_name(i) for i in ids))))

        def one(m: re.Match) -> str:
            target, _, label = m.group(1).partition("|")
            if target.startswith("@"):
                return "@" + names.get(target[1:], label or "someone")
            if target.startswith("#"):
                cached = self._convs.get(target[1:])
                return "#" + (label or (cached[0]["name"].lstrip("#") if cached else "channel"))
            if target.startswith("!"):
                word = target[1:]
                if word in _SPECIAL:
                    return _SPECIAL[word]
                if word.startswith("subteam^"):
                    return label if label.startswith("@") else "@" + (label or "group")
                return label
            if target.startswith("mailto:"):
                address = target[len("mailto:"):]
                return address if not label or label == address else f"{label} ({address})"
            if _SCHEME.match(target):
                return target if not label or label == target else f"{label} ({target})"
            return label or target

        return _entities(_TAG.sub(one, text))

    # -- catching up on direct messages --

    async def _backfill(self) -> None:
        """Direct and group direct conversations: the last few messages of each, from other people and recent.
        What is unread is the service's to say; this only makes sure a restart does not lose a message."""
        conversations: list[dict] = []
        cursor = ""
        while len(conversations) < BACKFILL_CONVERSATIONS:
            params = {"types": "im,mpim", "limit": 100, "exclude_archived": "true"}
            if cursor:
                params["cursor"] = cursor
            page = await self._read("conversations.list", params)
            conversations += [c for c in page.get("channels") or [] if isinstance(c, dict) and _is_id(c.get("id"))
                              and not c.get("is_user_deleted")]
            meta = page.get("response_metadata")
            cursor = meta.get("next_cursor") if isinstance(meta, dict) and meta.get("next_cursor") else ""
            if not cursor:
                break
        oldest = self.clock() - BACKFILL_DAYS * 86400

        async def history(conv: dict):
            body = await self._try("conversations.history", {
                "channel": conv["id"], "limit": BACKFILL_LIMIT, "oldest": f"{oldest:.6f}"})
            return conv, (body or {}).get("messages") or []

        found = []
        for conv, entries in await asyncio.gather(*(history(c) for c in conversations[:BACKFILL_CONVERSATIONS])):
            kind = "group" if conv.get("is_mpim") else "dm"
            for entry in entries:
                try:
                    if (isinstance(entry, dict) and entry.get("user") != self.user_id
                            and float(entry.get("ts")) >= oldest):
                        found.append((float(entry["ts"]), entry, conv["id"], kind, conv.get("user")))
                except (TypeError, ValueError):
                    continue
        users = list(dict.fromkeys(f[1].get("user") for f in found if _is_id(f[1].get("user"))))
        await asyncio.gather(*(self._user_name(u) for u in users))
        for _, entry, channel, kind, other in sorted(found, key=lambda f: f[0]):
            message = await self._for_me(entry, channel, kind, other)
            if message:
                self._deliver(message)

    # -- asked for --

    async def messages(self, since: float = 0.0, limit: int = 50) -> list[dict]:
        rows = sorted((m for m in self._ring.values() if m["ts"] > since), key=lambda m: m["ts"], reverse=True)
        return [copy.deepcopy(m) for m in rows[:max(0, int(limit))]]

    async def thread(self, ref: str, limit: int = 20) -> list[dict]:
        channel, ts = self._parse_ref(ref, NOT_FOUND)
        if (problem := self._unavailable()) is not None:
            raise problem
        limit = max(1, min(int(limit), 100))
        known = self._ring.get(ref)
        root = split_slack_ref(known["thread"])[2] if known and known["thread"] else ts
        try:
            body = await self._read("conversations.replies", {"channel": channel, "ts": root, "limit": 200})
            entries = [m for m in body.get("messages") or [] if isinstance(m, dict)]
            if len(entries) < 2:
                body = await self._read("conversations.history", {
                    "channel": channel, "latest": ts, "inclusive": "true", "limit": limit})
                entries = [m for m in body.get("messages") or [] if isinstance(m, dict)][::-1]
        except _Failure as f:
            raise self._driver_error(f, not_found=True) from None
        try:
            out = await self._thread_messages(channel, entries[-limit:])
        except _Failure as f:
            raise self._driver_error(f) from None
        for message in out:
            if message["ref"] == ref:
                message["web_url"] = await self._permalink(channel, ts)
        return out

    async def _thread_messages(self, channel: str, entries: list[dict]) -> list[dict]:
        conv = await self._conversation(channel)
        out = []
        for entry in entries:
            if entry.get("subtype") not in _SUBTYPES or not isinstance(entry.get("ts"), str):
                continue
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            mention = re.search(rf"<@{re.escape(self.user_id)}(?:\|[^<>\n]*)?>", text) is not None
            if entry.get("user") == self.user_id:
                reason = "you wrote this"
            elif conv["kind"] in ("dm", "group"):
                reason = "direct message"
            else:
                reason = "mentioned you" if mention else f"in {conv['name']}"
            sender = entry.get("user") if _is_id(entry.get("user")) else None
            message = await self._build(entry, channel, conv["kind"], sender, None, text, reason, mention)
            if message:
                out.append(message)
        return out

    async def _permalink(self, channel: str, ts: str) -> str | None:
        key = (channel, ts)
        if key in self._links:
            return self._links.get(key)
        try:
            body = await self._read("chat.getPermalink", {"channel": channel, "message_ts": ts})
        except _Failure:
            return None
        link = web_url(body.get("permalink"))
        if link:
            self._links.put(key, link)
        return link

    # -- writing: only from a press --

    async def perform(self, kind: str, target: str, content: Any) -> dict:
        if kind != "slack_reply":
            raise DriverError("That is not something this connection does.", REFUSED)
        if (problem := self._unavailable()) is not None:
            raise problem
        channel, ts = self._parse_ref(target, BAD_REQUEST)
        text = self._reply_text(content)
        try:
            root = await self._thread_root(target, channel, ts)
        except _Failure as f:
            raise self._driver_error(f, not_found=True) from None
        params = {"channel": channel, "text": _escaped(text), "thread_ts": root, "unfurl_links": "false"}
        try:
            # A connection of its own, so that a failure to connect is certainly "nothing was written" and
            # every failure after it is "it may have been".
            async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT)) as client:
                posted = await self._call("chat.postMessage", self._user, params, client)
        except _Failure as f:
            raise self._driver_error(f, posting=True) from None
        posted_ts = posted.get("ts") if isinstance(posted.get("ts"), str) else None
        receipt = {"line": f"{await self._said(target, channel)} · "
                           f"{time.strftime('%H:%M', time.localtime(self.clock()))}"}
        link = await self._permalink(channel, posted_ts) if posted_ts and _TS.fullmatch(posted_ts) else None
        if link:
            receipt["web"] = {"name": "Slack", "url": link}
        return receipt

    def _parse_ref(self, ref, code: str) -> tuple[str, str]:
        try:
            team, channel, ts = split_slack_ref(ref)
        except Refusal:
            raise DriverError("That is not a message I know.", code) from None
        if not (_is_id(channel) and _TS.fullmatch(ts)):
            raise DriverError("That is not a message I know.", code)
        if team != self.team_id:
            raise DriverError("That message is not in this Slack workspace.", NOT_FOUND)
        return channel, ts

    @staticmethod
    def _reply_text(content) -> str:
        if not isinstance(content, str) or not content.strip():
            raise DriverError("There is nothing to post.", BAD_REQUEST)
        if len(content) > MAX_REPLY:
            raise DriverError(f"That reply is over {MAX_REPLY} characters, which is more than I will post.",
                              BAD_REQUEST)
        if _CONTROL.search(content):
            raise DriverError("That reply has characters in it that I will not post.", BAD_REQUEST)
        return content

    def _unavailable(self) -> DriverError | None:
        if self.state != "ok":
            return DriverError(self.note, self._code or SERVICE_DOWN)
        if not self._user or self.user_id is None:
            return DriverError("Slack is not connected yet.", REFUSED)
        return None

    async def _thread_root(self, target: str, channel: str, ts: str) -> str:
        """The message a reply to `target` is posted under: its thread's first message, or itself."""
        known = self._ring.get(target)
        if known is not None:
            return split_slack_ref(known["thread"])[2] if known["thread"] else ts
        body = await self._read("conversations.replies", {"channel": channel, "ts": ts, "limit": 1})
        first = (body.get("messages") or [None])[0]
        root = first.get("ts") if isinstance(first, dict) else None
        return root if isinstance(root, str) and _TS.fullmatch(root) else ts

    async def _said(self, target: str, channel: str) -> str:
        """"Posted in #launch" or "Replied to Priya Shah in a direct message": best effort, never an error."""
        known = self._ring.get(target)
        conv = known["conversation"] if known else None
        if conv is None:
            try:
                conv = await self._conversation(channel)
            except _Failure:
                return "Posted in Slack"
        if conv["kind"] == "dm":
            return f"Replied to {conv['name']} in a direct message"
        if conv["kind"] == "group":
            return "Replied in a group message"
        return f"Posted in {conv['name']}"

    def _driver_error(self, f: _Failure, *, posting: bool = False, not_found: bool = False) -> DriverError:
        """What a failed call is to the caller; a failure of the connection itself also changes the state."""
        if f.fatal and not (posting and f.error == "restricted_action"):
            self._enter(f)
            self._abort()
        if f.kind == "auth":
            return DriverError(AUTH_SENTENCE, AUTH)
        if f.kind == "scope":
            return DriverError(_state_for(f)[1], REFUSED)
        if f.kind == "blocked":
            return DriverError(_REFUSALS.get(f.error) or _state_for(f)[1], BLOCKED)
        if f.kind == "rate":
            return DriverError("Slack is limiting how fast I can go. Try again in a minute.", RATE_LIMITED)
        if f.kind == "unsure" and posting:
            return UnknownOutcome()
        if f.kind in ("down", "unsure"):
            if posting and f.sent:
                return UnknownOutcome()
            return DriverError("I can't reach Slack right now." + (" Nothing was sent." if posting else ""),
                               SERVICE_DOWN)
        if not_found and f.error in _GONE:
            return DriverError("That message is gone, or I can't see it.", NOT_FOUND)
        return DriverError(_REFUSALS.get(f.error, "Slack would not post that." if posting
                                         else "Slack would not do that."), REFUSED)


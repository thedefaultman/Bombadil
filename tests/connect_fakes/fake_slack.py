"""A Slack for tests: its Web API as JSON over HTTP/1.1 and its Socket Mode, on one localhost port.

It runs inside the test's event loop (no thread, no dependency beyond what `bombadil.ws` already is) and speaks
what the Slack driver speaks: `POST /api/<method>` with `Authorization: Bearer <token>` and a form or JSON body,
and `GET /link` as a WebSocket that says `hello`, carries `events_api` envelopes (the shape Slack documents), and
records every acknowledgement it gets. `apps.connections.open` answers with this server's own `/link` address.

The workspace is invented: Acme, with Alex Chen (the person), Priya Shah and Marcus Webb, a public channel
#launch, a private channel #roadmap, a direct conversation with each of the other two and a group direct
conversation of the three. A test says what happens with `say` (store a message and deliver it), `deliver` (an
event as it is, for the shapes `say` does not make), and breaks things on purpose: `drop_socket`,
`send_disconnect`, `go_silent`, `revoke_tokens`, `rate_limit`, `fail_after_write`, `fail`, `missing_scope`,
`hold`. Every request is logged (`requests`) with the token it came with, so a test can say what was and was
not called: `writes()` is every request that is not one of the read-only methods.

Tokens are made at run time from pieces. Nothing here is secret-shaped as written.
"""

import asyncio
import json
import secrets as _secrets
import urllib.parse
from dataclasses import dataclass, field

from bombadil import ws

NOW = 1_789_988_640.0            # 2026-09-21 11:04:00 UTC

READ_ONLY = {"auth.test", "users.info", "conversations.list", "conversations.info", "conversations.history",
             "conversations.replies", "chat.getPermalink", "apps.connections.open"}


def make_token(kind: str) -> str:
    """A token shaped like Slack's, put together here so that no source line holds one: kind is "app" or "user"."""
    tail = _secrets.token_hex(16)
    if kind == "app":
        return "xa" + "pp-" + "-".join(["1", "A0" + _secrets.token_hex(4).upper(),
                                        str(10 ** 12 + _secrets.randbelow(10 ** 12)), tail])
    return "xo" + "xp-" + "-".join([str(10 ** 11 + _secrets.randbelow(10 ** 11)),
                                    str(10 ** 11 + _secrets.randbelow(10 ** 11)),
                                    str(10 ** 12 + _secrets.randbelow(10 ** 12)), tail])


@dataclass
class Request:
    method: str
    token: str | None
    params: dict
    seq: int = 0


class Gate:
    """Holds a method's requests until `release()`; `arrived` counts how many are waiting."""

    def __init__(self):
        self.arrived = 0
        self._open = asyncio.Event()

    async def pause(self) -> None:
        self.arrived += 1
        await self._open.wait()

    def release(self) -> None:
        self._open.set()


@dataclass
class _Socket:
    sock: ws.WebSocket
    reading: asyncio.Task | None = None
    silent: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


class _Replay:
    """The reader of a connection whose request head was already read to see where it was going: `ws.accept`
    reads its own head, so it is handed this one again, and everything after it comes from the real reader."""

    def __init__(self, head: bytes, reader: asyncio.StreamReader):
        self._head, self._reader = head, reader

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        head, self._head = self._head, b""
        return head

    def __getattr__(self, name):
        return getattr(self._reader, name)


def _key(ts: str) -> tuple[int, int]:
    seconds, _, micro = str(ts).partition(".")
    return int(seconds), int((micro + "000000")[:6])


def _error(error: str, **more) -> dict:
    return {"ok": False, "error": error, **more}


class FakeSlack:
    def __init__(self, now: float = NOW):
        self.now = now
        self.team = {"id": "T0ACME", "name": "Acme", "url": "https://acme.slack.test/"}
        self.me = "U0ALEX"
        self.users = {"U0ALEX": {"name": "alex", "real_name": "Alex Chen"},
                      "U0PRIYA": {"name": "priya", "real_name": "Priya Shah"},
                      "U0MARCUS": {"name": "marcus", "real_name": "Marcus Webb"}}
        self.channels = {"C0LAUNCH": {"kind": "channel", "name": "launch"},
                         "G0ROADMAP": {"kind": "private", "name": "roadmap"},
                         "D0PRIYA": {"kind": "dm", "user": "U0PRIYA"},
                         "D0MARCUS": {"kind": "dm", "user": "U0MARCUS"},
                         "G0TRIO": {"kind": "group", "name": "mpdm-priya--marcus--alex-1"}}
        self.messages: dict[str, list[dict]] = {c: [] for c in self.channels}
        self.app_token = make_token("app")
        self.user_token = make_token("user")
        self.ticket = _secrets.token_hex(12)
        self.page_size = 200
        self.echo = True                 # a message posted through the API also arrives as an event, as in Slack
        self.revoked = False
        self.no_hello = False
        self.requests: list[Request] = []
        self.acks: list[str] = []
        self.client_messages: list[dict] = []
        self.delivered: list[str] = []
        self.posted: list[dict] = []
        self.opened = 0                  # sockets accepted
        self.max_inflight: dict[str, int] = {}
        self.sockets: list[_Socket] = []
        self.port = 0
        self._seq = 0
        self._inflight: dict[str, int] = {}
        self._drops: dict[str, int] = {}
        self._limits: dict[str, list[tuple[float, int]]] = {}
        self._failures: dict[str, tuple[dict, int | None]] = {}
        self._statuses: dict[str, tuple[int, int | None]] = {}
        self._gates: dict[str, Gate] = {}
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()
        self._server: asyncio.AbstractServer | None = None

    # -- running --

    async def start(self) -> "FakeSlack":
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", self.port or 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for conn in list(self.sockets):
            conn.sock.writer.transport.abort()
        for writer in list(self._writers):
            writer.transport.abort()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.port}/api"

    # -- the workspace --

    def add_user(self, uid: str, real_name: str) -> str:
        self.users[uid] = {"name": real_name.split()[0].lower(), "real_name": real_name}
        return uid

    def add_dm(self, uid: str, channel: str | None = None) -> str:
        channel = channel or "D" + uid[1:]
        self.channels[channel] = {"kind": "dm", "user": uid}
        self.messages.setdefault(channel, [])
        return channel

    def add_channel(self, channel: str, name: str, kind: str = "channel") -> str:
        self.channels[channel] = {"kind": kind, "name": name}
        self.messages.setdefault(channel, [])
        return channel

    def ts(self, days_ago: float = 0.0, seconds_ago: float = 0.0) -> str:
        """A message time, unique and in the order made within a second."""
        self._seq += 1
        return f"{int(self.now - days_ago * 86400 - seconds_ago)}.{self._seq:06d}"

    def event(self, channel: str, message: dict) -> dict:
        kind = self.channels[channel]["kind"]
        return {"type": "message", "channel": channel,
                "channel_type": {"dm": "im", "group": "mpim", "channel": "channel", "private": "group"}[kind],
                "team": self.team["id"], "event_ts": message["ts"], **message}

    async def say(self, channel: str, user: str, text: str, *, thread_ts: str | None = None,
                  subtype: str | None = None, ts: str | None = None, deliver: bool = True, **more) -> str:
        """Store a message in the conversation and, unless `deliver` is false, send it down every socket."""
        message = {"type": "message", "user": user, "text": text, "ts": ts or self.ts(), **more}
        if thread_ts:
            message["thread_ts"] = thread_ts
        if subtype:
            message["subtype"] = subtype
        self.messages[channel].append(message)
        if deliver:
            await self.deliver(self.event(channel, message))
        return message["ts"]

    # -- the socket --

    def live(self) -> list[_Socket]:
        return [c for c in self.sockets if not c.silent]

    async def wait_socket(self, count: int = 1, timeout: float = 5.0) -> None:
        await self._until(lambda: len(self.sockets) >= count, timeout, "a socket")

    async def wait_ack(self, envelope_id: str, timeout: float = 5.0) -> None:
        await self._until(lambda: envelope_id in self.acks, timeout, "an acknowledgement")

    async def deliver(self, event: dict, *, team_id: str | None = None) -> str:
        """An `events_api` envelope with this event, to every socket that is listening. Returns its envelope id."""
        envelope_id = _secrets.token_hex(8)
        envelope = {"envelope_id": envelope_id, "type": "events_api", "accepts_response_payload": False,
                    "payload": {"team_id": team_id or self.team["id"], "api_app_id": "A0BOMBADIL",
                                "type": "event_callback", "event": event, "event_id": "Ev" + envelope_id,
                                "event_time": int(self.now)}}
        self.delivered.append(envelope_id)
        for conn in self.live():
            await conn.sock.send(json.dumps(envelope))
        return envelope_id

    async def send_disconnect(self, reason: str = "refresh_requested") -> None:
        for conn in self.live():
            await conn.sock.send(json.dumps({"type": "disconnect", "reason": reason,
                                             "debug_info": {"host": "applink-fake"}}))

    async def drop_socket(self) -> None:
        """Every socket goes away without a word, as when a network does."""
        for conn in list(self.sockets):
            conn.sock.writer.transport.abort()
        await self._until(lambda: not self.sockets, 5.0, "the sockets to end")

    def go_silent(self) -> None:
        """Every socket now open stays open but is never read from, so a ping is never answered."""
        for conn in self.sockets:
            conn.silent = True
            if conn.reading is not None:
                conn.reading.cancel()
            conn.reading = asyncio.ensure_future(self._read_silently(conn))

    # -- what a test breaks --

    def revoke_tokens(self) -> None:
        self.revoked = True

    def rate_limit(self, method: str, seconds: float, times: int = 1) -> None:
        self._limits.setdefault(method, []).append((seconds, times))

    def fail_after_write(self, method: str, times: int = 1) -> None:
        """The request is read in full, then the connection is closed with no answer."""
        self._drops[method] = times

    def fail_status(self, method: str, status: int, times: int | None = None) -> None:
        """Answer with an HTTP error status and no JSON (for `times` calls, or until `clear`)."""
        self._statuses[method] = (status, times)

    def fail(self, method: str, error: str, times: int | None = None, **more) -> None:
        """Answer `{"ok": false, "error": error}` (for `times` calls, or until `clear`)."""
        self._failures[method] = (_error(error, **more), times)

    def missing_scope(self, method: str, scope: str, times: int | None = None) -> None:
        self.fail(method, "missing_scope", times, needed=scope, provided="users:read")

    def hold(self, method: str) -> Gate:
        self._gates[method] = gate = Gate()
        return gate

    def clear(self, method: str | None = None) -> None:
        for table in (self._drops, self._limits, self._failures, self._statuses, self._gates):
            if method is None:
                table.clear()
            else:
                table.pop(method, None)

    # -- what a test asks --

    def calls(self, method: str) -> list[Request]:
        return [r for r in self.requests if r.method == method]

    def writes(self) -> list[Request]:
        return [r for r in self.requests if r.method not in READ_ONLY]

    @staticmethod
    async def _until(predicate, timeout: float, what: str) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not predicate():
            if loop.time() > end:
                raise AssertionError(f"timed out waiting for {what}")
            await asyncio.sleep(0.005)

    # -- the wire --

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                    return
                lines = head.decode("latin-1").split("\r\n")
                path = lines[0].split(" ")[1]
                headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:] if ":" in h)}
                if headers.get("upgrade", "").lower() == "websocket":
                    await self._socket(_Replay(head, reader), writer)
                    return
                try:
                    body = await reader.readexactly(int(headers.get("content-length", "0")))
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                reply = await self._api(path, headers, body)
                if reply is None:
                    return
                writer.write(reply)
                await writer.drain()
        except (ConnectionError, ws.WebSocketError):
            pass
        finally:
            writer.close()
            self._writers.discard(writer)
            self._tasks.discard(task)

    async def _socket(self, reader, writer) -> None:
        conn = _Socket(await ws.accept(reader, writer))
        self.sockets.append(conn)
        self.opened += 1
        try:
            if not self.no_hello:
                await conn.sock.send(json.dumps({
                    "type": "hello", "num_connections": 1, "connection_info": {"app_id": "A0BOMBADIL"},
                    "debug_info": {"host": "applink-fake", "approximate_connection_time": 18060}}))
            conn.reading = asyncio.ensure_future(self._read_socket(conn))
            await conn.done.wait()
        finally:
            if conn in self.sockets:
                self.sockets.remove(conn)
            if conn.reading is not None:
                conn.reading.cancel()

    async def _read_socket(self, conn: _Socket) -> None:
        while True:
            try:
                message = json.loads(await conn.sock.recv())
            except ws.WebSocketError:
                conn.done.set()
                return
            if isinstance(message, dict) and "envelope_id" in message:
                self.acks.append(message["envelope_id"])
            else:
                self.client_messages.append(message)

    async def _read_silently(self, conn: _Socket) -> None:
        """Frames are taken off the wire and thrown away: no pong, no acknowledgement recorded. A close ends it."""
        while True:
            try:
                _, opcode, _ = await ws.read_frame(conn.sock.reader)
            except ws.WebSocketError:
                conn.done.set()
                return
            if opcode == ws.OP_CLOSE:
                conn.done.set()
                return

    def _take(self, table: dict, method: str):
        """One use of an instruction that was given for a number of calls."""
        if method not in table:
            return None
        value = table[method]
        if isinstance(value, int):
            if value <= 0:
                return None
            table[method] = value - 1
            return True
        return None

    async def _api(self, path: str, headers: dict, body: bytes) -> bytes | None:
        method = path.removeprefix("/api/")
        token = headers.get("authorization", "").removeprefix("Bearer ") or None
        ctype = headers.get("content-type", "")
        if "json" in ctype and body:
            params = json.loads(body)
        else:
            params = dict(urllib.parse.parse_qsl(body.decode(), keep_blank_values=True))
        self.requests.append(Request(method, token, params, len(self.requests)))
        self._inflight[method] = self._inflight.get(method, 0) + 1
        self.max_inflight[method] = max(self.max_inflight.get(method, 0), self._inflight[method])
        try:
            if self._take(self._drops, method):
                return None
            for i, (seconds, times) in enumerate(self._limits.get(method, [])):
                if times > 0:
                    self._limits[method][i] = (seconds, times - 1)
                    return self._http(429, _error("ratelimited"), {"Retry-After": f"{seconds:g}"})
            if method in self._gates:
                await self._gates[method].pause()
            if method in self._statuses:
                status, times = self._statuses[method]
                if times is None or times > 0:
                    if times is not None:
                        self._statuses[method] = (status, times - 1)
                    return self._http(status, {})
            return self._http(200, self._answer(method, token, params))
        finally:
            self._inflight[method] -= 1

    @staticmethod
    def _http(status: int, body: dict, headers: dict | None = None) -> bytes:
        data = json.dumps(body).encode()
        head = [f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not OK'}",
                "Content-Type: application/json; charset=utf-8", f"Content-Length: {len(data)}"]
        head += [f"{k}: {v}" for k, v in (headers or {}).items()]
        return ("\r\n".join(head) + "\r\n\r\n").encode() + data

    def _answer(self, method: str, token: str | None, params: dict):
        if self.revoked:
            return _error("invalid_auth")
        wanted = self.app_token if method == "apps.connections.open" else self.user_token
        if token != wanted:
            return _error("not_allowed_token_type" if token in (self.app_token, self.user_token)
                          else "invalid_auth")
        if method in self._failures:
            answer, times = self._failures[method]
            if times is None:
                return answer
            if times > 0:
                self._failures[method] = (answer, times - 1)
                return answer
        handler = getattr(self, "_m_" + method.replace(".", "_"), None)
        return handler(params) if handler else _error("unknown_method")

    # -- the methods --

    def _m_auth_test(self, p: dict) -> dict:
        return {"ok": True, "url": self.team["url"], "team": self.team["name"],
                "user": self.users[self.me]["name"], "team_id": self.team["id"], "user_id": self.me}

    def _m_apps_connections_open(self, p: dict) -> dict:
        return {"ok": True, "url": f"ws://127.0.0.1:{self.port}/link?ticket={self.ticket}&app_id=A0BOMBADIL"}

    def _m_users_info(self, p: dict) -> dict:
        user = self.users.get(p.get("user"))
        if user is None:
            return _error("user_not_found")
        return {"ok": True, "user": {"id": p["user"], "name": user["name"], "real_name": user["real_name"],
                                     "profile": {"real_name": user["real_name"], "display_name": user["name"]}}}

    def _describe(self, channel: str) -> dict:
        c = self.channels[channel]
        if c["kind"] == "dm":
            return {"id": channel, "is_im": True, "user": c["user"], "is_user_deleted": False}
        if c["kind"] == "group":
            return {"id": channel, "name": c["name"], "is_mpim": True, "is_group": True, "is_private": True}
        if c["kind"] == "private":
            return {"id": channel, "name": c["name"], "is_channel": False, "is_group": True, "is_private": True}
        return {"id": channel, "name": c["name"], "is_channel": True, "is_private": False}

    def _m_conversations_info(self, p: dict) -> dict:
        if p.get("channel") not in self.channels:
            return _error("channel_not_found")
        return {"ok": True, "channel": self._describe(p["channel"])}

    def _m_conversations_list(self, p: dict) -> dict:
        kinds = {"im": "dm", "mpim": "group", "public_channel": "channel", "private_channel": "private"}
        wanted = {kinds[t] for t in str(p.get("types") or "public_channel").split(",") if t in kinds}
        ids = [i for i, c in self.channels.items() if c["kind"] in wanted]
        start = int(p.get("cursor") or 0)
        size = min(int(p.get("limit") or 100), self.page_size)
        page = ids[start:start + size]
        more = str(start + size) if start + size < len(ids) else ""
        return {"ok": True, "channels": [self._describe(i) for i in page],
                "response_metadata": {"next_cursor": more}}

    def _m_conversations_history(self, p: dict) -> dict:
        channel = p.get("channel")
        if channel not in self.channels:
            return _error("channel_not_found")
        rows = [m for m in self.messages[channel] if not m.get("thread_ts") or m["thread_ts"] == m["ts"]
                or m.get("subtype") == "thread_broadcast"]
        if p.get("oldest"):
            rows = [m for m in rows if _key(m["ts"]) > _key(p["oldest"])]
        if p.get("latest"):
            inclusive = str(p.get("inclusive")).lower() == "true"
            rows = [m for m in rows if _key(m["ts"]) < _key(p["latest"])
                    or (inclusive and _key(m["ts"]) == _key(p["latest"]))]
        rows.sort(key=lambda m: _key(m["ts"]), reverse=True)
        limit = int(p.get("limit") or 100)
        return {"ok": True, "messages": rows[:limit], "has_more": len(rows) > limit}

    def _m_conversations_replies(self, p: dict) -> dict:
        channel = p.get("channel")
        if channel not in self.channels:
            return _error("channel_not_found")
        rows = self.messages[channel]
        found = next((m for m in rows if m["ts"] == p.get("ts")), None)
        if found is None:
            return _error("thread_not_found")
        root_ts = found.get("thread_ts") or found["ts"]
        root = next((m for m in rows if m["ts"] == root_ts), found)
        replies = sorted((m for m in rows if m.get("thread_ts") == root_ts and m["ts"] != root_ts),
                         key=lambda m: _key(m["ts"]))
        return {"ok": True, "messages": ([root] + replies)[:int(p.get("limit") or 1000)], "has_more": False}

    def _m_chat_getPermalink(self, p: dict) -> dict:
        found = any(m["ts"] == p.get("message_ts") for m in self.messages.get(p.get("channel"), []))
        if not found:
            return _error("message_not_found")
        return {"ok": True, "channel": p["channel"],
                "permalink": f"{self.team['url']}archives/{p['channel']}/p{p['message_ts'].replace('.', '')}"}

    def _m_chat_postMessage(self, p: dict):
        channel = p.get("channel")
        if channel not in self.channels:
            return _error("channel_not_found")
        if not p.get("text"):
            return _error("no_text")
        thread_ts = p.get("thread_ts")
        if thread_ts and not any(m["ts"] == thread_ts for m in self.messages[channel]):
            return _error("thread_not_found")
        message = {"type": "message", "user": self.me, "text": p["text"], "ts": self.ts()}
        if thread_ts:
            message["thread_ts"] = thread_ts
        self.messages[channel].append(message)
        self.posted.append({"channel": channel, **message})
        if self.echo:
            task = asyncio.ensure_future(self.deliver(self.event(channel, message)))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return {"ok": True, "channel": channel, "ts": message["ts"], "message": message}

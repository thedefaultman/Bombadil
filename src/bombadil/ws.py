"""A WebSocket client (RFC 6455) on asyncio streams, and the framing a test server needs.

Bombadil has two callers: bombadil-browserd talks to Chromium's DevTools over one, and bombadil-connect's
Slack connection is Socket Mode, which is one. Neither needs more than text messages, ping/pong and close, so
this is about two hundred lines and no dependency, rather than a package the image would have to carry.

What it does and does not do:

- Client side only: the handshake (checked: status 101, the accept key, no extension and no sub-protocol that was
  not asked for), masked frames out, unmasked frames in, fragmented messages put back together, a ping answered
  with a pong, a close answered with a close.
- A message is at most `limit` bytes (default 16 MiB): a peer that sends more is closed with 1009, not buffered.
- Text and binary messages come back as `str` and `bytes`. No compression (no permessage-deflate is offered).
- `wss://` uses the system's certificate store; `ws://` is for localhost. A proxy is not used: both callers
  talk to localhost or to a host the caller has already decided to reach.
"""

import asyncio
import base64
import hashlib
import os
import ssl
import struct
from urllib.parse import urlsplit

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 16 << 20
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    """The handshake was refused, the peer broke the protocol, or the connection went away."""


class Closed(WebSocketError):
    """The connection is closed (by either side); `.code` is the close code the peer sent, or 1006."""

    def __init__(self, code: int = 1006, reason: str = ""):
        super().__init__(f"closed ({code}) {reason}".strip())
        self.code = code
        self.reason = reason


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()


def encode_frame(opcode: int, payload: bytes, mask: bool, fin: bool = True) -> bytes:
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    flag = 0x80 if mask else 0
    if n < 126:
        head.append(flag | n)
    elif n < 1 << 16:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        head += key
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload)) if n < 4096 else _xor(payload, key)
    return bytes(head) + payload


def _xor(payload: bytes, key: bytes) -> bytes:
    """The mask applied to a long payload: one big-integer xor, not a Python loop over each byte."""
    n = len(payload)
    pad = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(pad, "big")).to_bytes(n, "big")


async def read_frame(reader: asyncio.StreamReader, limit: int = MAX_MESSAGE) -> tuple[bool, int, bytes]:
    """One frame: (fin, opcode, unmasked payload). Raises Closed when the stream ends."""
    try:
        b0, b1 = await reader.readexactly(2)
        n = b1 & 0x7F
        if n == 126:
            (n,) = struct.unpack(">H", await reader.readexactly(2))
        elif n == 127:
            (n,) = struct.unpack(">Q", await reader.readexactly(8))
        if n > limit:
            raise WebSocketError(f"a frame of {n} bytes is over the limit")
        key = await reader.readexactly(4) if b1 & 0x80 else None
        data = await reader.readexactly(n) if n else b""
    except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
        raise Closed(1006, type(e).__name__) from None
    if key:
        data = _xor(data, key) if data else data
    if b0 & 0x70:
        raise WebSocketError("a frame used a reserved bit (no extension was asked for)")
    return bool(b0 & 0x80), b0 & 0x0F, data


class WebSocket:
    """One open connection. `recv()` gives the next message; `send()` sends one; `close()` ends it politely."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limit: int = MAX_MESSAGE,
                 mask: bool = True):
        self.reader, self.writer, self.limit, self.mask = reader, writer, limit, mask
        self._send_lock = asyncio.Lock()
        self.closed = False
        self.close_code: int | None = None

    async def _write(self, opcode: int, payload: bytes) -> None:
        async with self._send_lock:
            if self.closed and opcode != OP_CLOSE:
                raise Closed(self.close_code or 1006)
            try:
                self.writer.write(encode_frame(opcode, payload, self.mask))
                await self.writer.drain()
            except (ConnectionError, OSError):
                self.closed = True
                raise Closed(1006, "write failed") from None

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, str):
            await self._write(OP_TEXT, message.encode())
        else:
            await self._write(OP_BINARY, bytes(message))

    async def ping(self, data: bytes = b"") -> None:
        await self._write(OP_PING, data[:125])

    async def recv(self) -> str | bytes:
        """The next message. Pings are answered and pongs dropped here; a close raises Closed."""
        parts: list[bytes] = []
        kind = None
        while True:
            try:
                fin, op, data = await read_frame(self.reader, self.limit)
            except WebSocketError as e:
                if not isinstance(e, Closed):
                    await self._fail(1002 if "reserved" in str(e) else 1009)
                    raise
                self.closed = True
                raise
            if op == OP_PING:
                await self._write(OP_PONG, data)
            elif op == OP_PONG:
                continue
            elif op == OP_CLOSE:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 1005
                self.close_code = code
                if not self.closed:
                    self.closed = True
                    try:
                        self.writer.write(encode_frame(OP_CLOSE, data[:2], self.mask))
                        await self.writer.drain()
                    except (ConnectionError, OSError):
                        pass
                self._shut()
                raise Closed(code, data[2:].decode(errors="replace"))
            elif op in (OP_TEXT, OP_BINARY, OP_CONT):
                if op != OP_CONT:
                    if parts:
                        await self._fail(1002)
                        raise WebSocketError("a new message began inside another")
                    kind = op
                elif kind is None:
                    await self._fail(1002)
                    raise WebSocketError("a continuation with nothing to continue")
                parts.append(data)
                if sum(map(len, parts)) > self.limit:
                    await self._fail(1009)
                    raise WebSocketError("a message is over the limit")
                if fin:
                    body = b"".join(parts)
                    if kind == OP_TEXT:
                        try:
                            return body.decode()
                        except UnicodeDecodeError:
                            await self._fail(1007)
                            raise WebSocketError("a text message was not UTF-8") from None
                    return body
            else:
                await self._fail(1002)
                raise WebSocketError(f"unknown opcode {op}")

    async def _fail(self, code: int) -> None:
        try:
            await self.close(code)
        except WebSocketError:
            pass

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            self.closed = True
            self.close_code = code
            try:
                self.writer.write(encode_frame(OP_CLOSE, struct.pack(">H", code), self.mask))
                await asyncio.wait_for(self.writer.drain(), 2)
            except (ConnectionError, OSError, TimeoutError):
                pass
        self._shut()

    def _shut(self) -> None:
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


async def connect(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0,
                  limit: int = MAX_MESSAGE, ssl_context: ssl.SSLContext | None = None) -> WebSocket:
    """Open `ws://` or `wss://`. Raises WebSocketError when the server does not agree."""
    u = urlsplit(url)
    if u.scheme not in ("ws", "wss") or not u.hostname:
        raise WebSocketError(f"not a websocket address: {url!r}")
    port = u.port or (443 if u.scheme == "wss" else 80)
    ctx = (ssl_context or ssl.create_default_context()) if u.scheme == "wss" else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(u.hostname, port, ssl=ctx, limit=1 << 20), timeout)
    except (OSError, TimeoutError) as e:
        raise WebSocketError(f"could not connect to {u.hostname}:{port}: {type(e).__name__}") from None
    key = base64.b64encode(os.urandom(16)).decode()
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    host = u.hostname if not u.port else f"{u.hostname}:{u.port}"
    lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items() if "\n" not in k + v and "\r" not in k + v]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    try:
        await writer.drain()
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
        writer.close()
        raise WebSocketError(f"the handshake did not finish: {type(e).__name__}") from None
    head = raw.decode("latin-1").split("\r\n")
    status = head[0].split(" ", 2)
    got = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in head[1:] if ":" in h)}
    if len(status) < 2 or status[1] != "101":
        writer.close()
        raise WebSocketError(f"the server answered {head[0][:80]!r} instead of switching protocols")
    if got.get("sec-websocket-accept") != accept_key(key) or got.get("upgrade", "").lower() != "websocket":
        writer.close()
        raise WebSocketError("the server's answer was not a websocket handshake")
    if "sec-websocket-extensions" in got or "sec-websocket-protocol" in got:
        writer.close()
        raise WebSocketError("the server used an extension or protocol nobody asked for")
    return WebSocket(reader, writer, limit)


async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, limit: int = MAX_MESSAGE) -> WebSocket:
    """Server side of the handshake, for the fake servers the tests run: reads the request, answers 101."""
    raw = await reader.readuntil(b"\r\n\r\n")
    got = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in raw.decode("latin-1").split("\r\n")[1:]
                                                       if ":" in h)}
    key = got.get("sec-websocket-key")
    if not key:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        raise WebSocketError("no Sec-WebSocket-Key")
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode())
    await writer.drain()
    return WebSocket(reader, writer, limit, mask=False)

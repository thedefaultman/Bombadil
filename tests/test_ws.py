"""The WebSocket client against the framing it ships for tests (src/bombadil/ws.py)."""

import asyncio
import struct

import pytest

from bombadil import ws

pytestmark = pytest.mark.asyncio


async def serve(handler, raw: bool = False):
    """A server on localhost whose `handler(websocket)` runs after the handshake (`raw`: the handler gets the
    streams and does the handshake, or breaks it, itself)."""
    async def on_client(reader, writer):
        try:
            if raw:
                await handler(reader, writer)
            else:
                sock = await ws.accept(reader, writer)
                await handler(sock)
        except (ws.WebSocketError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
    server = await asyncio.start_server(on_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_text_and_binary_messages_round_trip_whatever_their_size():
    async def echo(sock):
        while True:
            await sock.send(await sock.recv())
    server, port = await serve(echo)
    async with server, await ws.connect(f"ws://127.0.0.1:{port}/x?y=1") as c:
        for message in ("hi", "", "é" * 1000, "x" * 70_000, b"\x00\x01\xff", b"b" * 200_000):
            await c.send(message)
            assert await c.recv() == message


async def test_a_ping_is_answered_and_a_pong_is_not_a_message():
    async def server_side(sock):
        await sock.ping(b"are you there")
        await sock.send("after the ping")
        await sock.recv()
    server, port = await serve(server_side)
    async with server, await ws.connect(f"ws://127.0.0.1:{port}") as c:
        assert await c.recv() == "after the ping"
        await c.send("done")


async def test_a_fragmented_message_is_put_back_together():
    async def fragments(reader, writer):
        sock = await ws.accept(reader, writer)
        for frame in (ws.encode_frame(ws.OP_TEXT, b"one ", False, fin=False),
                      ws.encode_frame(ws.OP_PING, b"in between", False),
                      ws.encode_frame(ws.OP_CONT, b"two ", False, fin=False),
                      ws.encode_frame(ws.OP_CONT, b"three", False)):
            sock.writer.write(frame)
        await sock.writer.drain()
        await sock.recv()
    server, port = await serve(fragments, raw=True)
    async with server, await ws.connect(f"ws://127.0.0.1:{port}") as c:
        assert await c.recv() == "one two three"


async def test_the_peers_close_is_answered_and_raises_with_its_code():
    async def closer(sock):
        await sock.send("last")
        await sock.close(4000)
    server, port = await serve(closer)
    async with server:
        c = await ws.connect(f"ws://127.0.0.1:{port}")
        assert await c.recv() == "last"
        with pytest.raises(ws.Closed) as e:
            await c.recv()
        assert e.value.code == 4000 and c.closed
        with pytest.raises(ws.Closed):
            await c.send("too late")


async def test_a_connection_that_drops_is_closed_not_a_hang():
    async def drop(reader, writer):
        await ws.accept(reader, writer)
        writer.close()
    server, port = await serve(drop, raw=True)
    async with server:
        c = await ws.connect(f"ws://127.0.0.1:{port}")
        with pytest.raises(ws.Closed) as e:
            await asyncio.wait_for(c.recv(), 5)
        assert e.value.code == 1006


async def test_a_message_over_the_limit_is_refused_and_the_connection_closed():
    async def big(sock):
        await sock.send("y" * 5000)
        await asyncio.sleep(0.5)
    server, port = await serve(big)
    async with server:
        c = await ws.connect(f"ws://127.0.0.1:{port}", limit=1000)
        with pytest.raises(ws.WebSocketError):
            await c.recv()
        assert c.closed


async def test_a_handshake_the_server_refuses_or_gets_wrong_is_an_error():
    async def not_found(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()

    async def wrong_key(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: nope\r\n\r\n")
        await writer.drain()

    async def extension(reader, writer):
        raw = await reader.readuntil(b"\r\n\r\n")
        key = [ln.split(": ")[1] for ln in raw.decode().split("\r\n") if ln.lower().startswith("sec-websocket-key")][0]
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {ws.accept_key(key)}\r\nSec-WebSocket-Extensions: permessage-deflate\r\n\r\n"
                      ).encode())
        await writer.drain()

    for handler in (not_found, wrong_key, extension):
        server, port = await serve(handler, raw=True)
        async with server:
            with pytest.raises(ws.WebSocketError):
                await ws.connect(f"ws://127.0.0.1:{port}", timeout=3)
    with pytest.raises(ws.WebSocketError):
        await ws.connect("http://127.0.0.1:1/")
    with pytest.raises(ws.WebSocketError):
        await ws.connect("ws://127.0.0.1:1/", timeout=2)      # nothing listens there


async def test_a_reserved_bit_or_an_invalid_text_message_closes_the_connection():
    async def rude(reader, writer):
        sock = await ws.accept(reader, writer)
        sock.writer.write(bytes([0x80 | 0x40 | ws.OP_TEXT, 1]) + b"x")      # RSV1 set, as if compressed
        await sock.writer.drain()
        await asyncio.sleep(0.5)

    async def not_utf8(sock):
        sock.writer.write(ws.encode_frame(ws.OP_TEXT, b"\xff\xfe", False))
        await sock.writer.drain()
        await asyncio.sleep(0.5)
    for handler, raw in ((rude, True), (not_utf8, False)):
        server, port = await serve(handler, raw=raw)
        async with server:
            c = await ws.connect(f"ws://127.0.0.1:{port}")
            with pytest.raises(ws.WebSocketError):
                await c.recv()
            assert c.closed


async def test_the_masking_of_a_client_frame_is_undone_by_the_reader():
    reader = asyncio.StreamReader()
    reader.feed_data(ws.encode_frame(ws.OP_TEXT, "héllo".encode(), True))
    reader.feed_data(ws.encode_frame(ws.OP_BINARY, bytes(range(256)) * 40, True))
    assert await ws.read_frame(reader) == (True, ws.OP_TEXT, "héllo".encode())
    assert await ws.read_frame(reader) == (True, ws.OP_BINARY, bytes(range(256)) * 40)
    assert struct.unpack(">H", ws.encode_frame(ws.OP_BINARY, b"z" * 300, False)[2:4]) == (300,)

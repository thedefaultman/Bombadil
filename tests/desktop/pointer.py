"""A pointer for the headless session: a wlroots virtual pointer that stays connected and does what it is told.

The test compositor has no pointer, so its seat offers none and no client gets a wl_pointer. This
makes one with the zwlr_virtual_pointer_v1 protocol, which sway serves, and speaks the Wayland wire
protocol itself so the test image needs nothing more than Python. The protocol's own words, one per line on
stdin, each answered with "ok" on stdout once the compositor has taken it:

    move X Y        put the pointer at X, Y (the output's pixels; 1280x800 unless EXTENT says otherwise)
    press           the left button goes down
    release         and up
    quit

It stays up between commands: when the last virtual pointer goes, the seat loses its pointer too.
"""

import os
import socket
import struct
import sys
import time

BTN_LEFT = 0x110
WIDTH, HEIGHT = (int(v) for v in os.environ.get("EXTENT", "1280x800").split("x"))


def string(text):
    raw = text.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


class Wire:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(os.path.join(os.environ["XDG_RUNTIME_DIR"], os.environ["WAYLAND_DISPLAY"]))
        self.buf = b""
        self.last_id = 1

    def new_id(self):
        self.last_id += 1
        return self.last_id

    def send(self, obj, opcode, payload=b""):
        self.sock.sendall(struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload)

    def read(self):
        """The next event: (object, opcode, payload)."""
        while True:
            if len(self.buf) >= 8:
                obj, head = struct.unpack_from("<II", self.buf)
                size = head >> 16
                if len(self.buf) >= size:
                    payload, self.buf = self.buf[8:size], self.buf[size:]
                    if obj == 1 and head & 0xFFFF == 0:   # wl_display.error
                        sys.exit(f"pointer: the compositor refused: {payload[8:]!r}")
                    return obj, head & 0xFFFF, payload
            chunk = self.sock.recv(65536)
            if not chunk:
                sys.exit("pointer: the compositor hung up")
            self.buf += chunk

    def sync(self):
        """Returns once the compositor has handled everything sent so far; the globals it announced come back."""
        done, globals_ = self.new_id(), []
        self.send(1, 0, struct.pack("<I", done))
        while True:
            obj, opcode, payload = self.read()
            if obj == done:
                return globals_
            if obj == self.registry and opcode == 0:
                name, length = struct.unpack_from("<II", payload)
                (version,) = struct.unpack_from("<I", payload, 8 + ((length + 3) & ~3))
                globals_.append((name, payload[8:8 + length - 1].decode(), version))


def main():
    wire = Wire()
    wire.registry = wire.new_id()
    wire.send(1, 1, struct.pack("<I", wire.registry))
    known = {iface: (name, version) for name, iface, version in wire.sync()}
    bound = {}
    for iface, version in (("wl_seat", 1), ("zwlr_virtual_pointer_manager_v1", 1)):
        if iface not in known:
            sys.exit(f"pointer: the compositor has no {iface}")
        bound[iface] = wire.new_id()
        wire.send(wire.registry, 0, struct.pack("<I", known[iface][0]) + string(iface)
                  + struct.pack("<II", min(version, known[iface][1]), bound[iface]))
    pointer = wire.new_id()
    wire.send(bound["zwlr_virtual_pointer_manager_v1"], 0, struct.pack("<II", bound["wl_seat"], pointer))
    wire.sync()

    def now():
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    for line in sys.stdin:
        word = line.split()
        if not word:
            continue
        if word[0] == "move" and len(word) == 3:
            x, y = (min(max(int(v), 0), limit - 1) for v, limit in ((word[1], WIDTH), (word[2], HEIGHT)))
            wire.send(pointer, 1, struct.pack("<IIIII", now(), x, y, WIDTH, HEIGHT))
            wire.send(pointer, 4)
        elif word[0] in ("press", "release"):
            wire.send(pointer, 2, struct.pack("<III", now(), BTN_LEFT, 1 if word[0] == "press" else 0))
            wire.send(pointer, 4)
        elif word[0] == "quit":
            break
        else:
            print(f"pointer: no such word: {line.strip()!r}", flush=True)
            continue
        wire.sync()
        print("ok", flush=True)


if __name__ == "__main__":
    main()

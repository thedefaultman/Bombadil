#!/usr/bin/env python3
"""Tiny QMP client for test-vm.sh: `qmp.py SOCK send-keys down ret` or `qmp.py SOCK screenshot out.png`."""
import json
import socket
import struct
import sys
import zlib


def qmp(path):
    s = socket.socket(socket.AF_UNIX)
    s.connect(path)
    f = s.makefile("rw")
    f.readline()  # greeting
    def cmd(execute, **arguments):
        f.write(json.dumps({"execute": execute, "arguments": arguments}) + "\n"); f.flush()
        while True:
            r = json.loads(f.readline())
            if "return" in r or "error" in r:
                return r
    cmd("qmp_capabilities")
    return cmd


def ppm_to_png(ppm, png):
    data = open(ppm, "rb").read()
    parts = data.split(maxsplit=4)
    w, h = int(parts[1]), int(parts[2]); pix = parts[4][-(w * h * 3):]
    raw = b"".join(b"\x00" + pix[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    open(png, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                          + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main():
    sock, what, *rest = sys.argv[1:]
    cmd = qmp(sock)
    if what == "send-keys":
        for k in rest:
            cmd("send-key", keys=[{"type": "qcode", "data": k}])
    elif what == "screenshot":
        ppm = rest[0] + ".ppm"
        cmd("screendump", filename=ppm)
        ppm_to_png(ppm, rest[0])
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()

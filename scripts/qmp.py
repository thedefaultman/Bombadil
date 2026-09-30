#!/usr/bin/env python3
"""Tiny QMP client for test-vm.sh: `qmp.py SOCK send-keys down ret`, `qmp.py SOCK screenshot out.png`
or `qmp.py SOCK powerdown` (the ACPI power button: the guest shuts itself down).

A key joined with + is a chord: `meta_l+esc` holds both together."""
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
        f.write(json.dumps({"execute": execute, "arguments": arguments}) + "\n")
        f.flush()
        while True:
            r = json.loads(f.readline())
            if "return" in r or "error" in r:
                return r
    cmd("qmp_capabilities")
    return cmd


def ppm_to_png(ppm, png):
    with open(ppm, "rb") as f:
        data = f.read()
    # Header: "P6" w h maxval, each followed by one whitespace byte; pixels start right after,
    # and may themselves begin with whitespace bytes, so don't split the whole buffer.
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[pos:end])
        pos = end + 1
    w, h = int(fields[1]), int(fields[2])
    pix = data[pos:pos + w * h * 3]
    raw = b"".join(b"\x00" + pix[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main():
    sock, what, *rest = sys.argv[1:]
    cmd = qmp(sock)
    if what == "send-keys":
        for k in rest:
            cmd("send-key", keys=[{"type": "qcode", "data": part} for part in k.split("+")])
    elif what == "screenshot":
        ppm = rest[0] + ".ppm"
        cmd("screendump", filename=ppm)
        ppm_to_png(ppm, rest[0])
    elif what == "powerdown":
        cmd("system_powerdown")
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()

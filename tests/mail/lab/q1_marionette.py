#!/usr/bin/env python3
"""Q1 fallback: install the unsigned add-on over Marionette (--marionette, TCP 2828) in a release Thunderbird.

    python3 q1_marionette.py [--temporary | --permanent]

Starts the lab with NO add-on (LAB_LOAD=none) and --marionette, speaks the Marionette wire protocol directly
(length-prefixed JSON, protocol 3), sends Addon:Install (no chrome context needed) for the unsigned XPI,
then waits for the add-on's native-messaging hello. Needs no extra Python packages.
Uses the second lab directory (/tmp/bombadil-lab-mn, ports 3143/3025) so it can run next to another lab.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LAB_IMAP_PORT", "3143")
os.environ.setdefault("LAB_SMTP_PORT", "3025")
from labtest import Lab  # noqa: E402
import build_xpi  # noqa: E402


class Marionette:
    def __init__(self, port=2828, timeout=60):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = b""
        self.n = 0
        self.hello = self._read()

    def _read(self):
        while b":" not in self.buf:
            self.buf += self.s.recv(65536)
        ln, rest = self.buf.split(b":", 1)
        ln = int(ln)
        while len(rest) < ln:
            rest += self.s.recv(65536)
        self.buf = rest[ln:]
        return json.loads(rest[:ln].decode())

    def cmd(self, name, params=None):
        self.n += 1
        body = json.dumps([0, self.n, name, params or {}]).encode()
        self.s.sendall(b"%d:%s" % (len(body), body))
        while True:
            m = self._read()
            if isinstance(m, list) and m[0] == 1 and m[1] == self.n:
                if m[2]:
                    raise RuntimeError("%s: %s" % (name, json.dumps(m[2])[:300]))
                return m[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--permanent", action="store_true", help="temporary=false (installs into the profile, signature check applies)")
    a = ap.parse_args()
    xpi = build_xpi.build(os.path.expanduser("~/.cache/bombadil-lab/build/bombadil-mail-lab-mn.xpi")) if hasattr(build_xpi, "build") else None
    lab = Lab("/tmp/bombadil-lab-mn")
    lab.start(LAB_LOAD="none", LAB_MARIONETTE=1)
    try:
        for _ in range(60):
            try:
                m = Marionette()
                break
            except OSError:
                time.sleep(1)
        else:
            print("marionette: port 2828 never opened")
            return 1
        print("marionette handshake:", m.hello)
        print("NewSession:", json.dumps(m.cmd("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}}))[:300])
        # NB: Marionette:SetContext "chrome" is refused on a release build without -remote-allow-system-access
        # ("System access is required"); Addon:Install does not need the chrome context.
        t = time.time()
        r = m.cmd("Addon:Install", {"path": xpi, "temporary": not a.permanent})
        print("Addon:Install(temporary=%s) ->" % (not a.permanent), r, "in %.2fs" % (time.time() - t))
        ok = False
        for _ in range(40):
            if os.path.exists(os.path.join(lab.dir, "hello.json")):
                ok = True
                break
            time.sleep(0.5)
        print("add-on started and said hello over native messaging: %s" % ok)
        if ok:
            print("hello.json:", open(os.path.join(lab.dir, "hello.json")).read()[:200])
        return 0 if ok else 2
    finally:
        lab.stop()


if __name__ == "__main__":
    sys.exit(main())

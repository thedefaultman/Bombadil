#!/usr/bin/env python3
"""Python side of the lab's native-messaging bridge.

Listens on the Unix socket that bombadil_mail_host.py connects to and gives tests a synchronous API onto
the real messenger.* APIs of the running Thunderbird (through extension/background.js):

    from lab_bridge import Bridge
    b = Bridge("/tmp/bombadil-lab/host.sock")      # or Bridge.from_env()
    b.wait_connected(30)
    accounts = b.call("accounts.list")
    folders  = b.call("folders.query", {"specialUse": ["inbox"]})
    b.listen("messages.onNewMailReceived")
    ev = b.wait_event("messages.onNewMailReceived", timeout=60)

Wire format: newline-delimited JSON (see host/bombadil_mail_host.py, extension/background.js).
"""
import json
import os
import socket
import threading
import time


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, sock_path):
        self.path = sock_path
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(sock_path)
        self.srv.listen(4)
        self.conn = None
        self.cv = threading.Condition()
        self.next_id = 1
        self.pending = {}
        self.events = []
        self.frames = []  # everything received, for debugging
        self.connected_at = None
        threading.Thread(target=self._accept, daemon=True).start()

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("LAB_HOST_SOCK") or os.path.join(os.environ.get("BOMBADIL_LAB_DIR", "/tmp/bombadil-lab"), "host.sock"))

    # -- plumbing
    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            with self.cv:
                self.conn = c
                self.connected_at = time.time()
                self.cv.notify_all()
            threading.Thread(target=self._read, args=(c,), daemon=True).start()

    def _read(self, c):
        f = c.makefile("rb", buffering=1 << 20)
        for line in f:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self.cv:
                self.frames.append(msg)
                if "id" in msg and msg["id"] in self.pending:
                    self.pending[msg["id"]] = msg
                elif "event" in msg:
                    self.events.append(msg)
                self.cv.notify_all()
        with self.cv:
            if self.conn is c:
                self.conn = None
            self.cv.notify_all()

    def wait_connected(self, timeout=60):
        end = time.time() + timeout
        with self.cv:
            while self.conn is None:
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError("native host did not connect to %s" % self.path)
                self.cv.wait(left)
        return self

    def request(self, timeout=60, **msg):
        with self.cv:
            rid = self.next_id
            self.next_id += 1
            self.pending[rid] = None
            conn = self.conn
        msg["id"] = rid
        if conn is None:
            raise BridgeError("no native host connected")
        conn.sendall(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
        end = time.time() + timeout
        with self.cv:
            while self.pending[rid] is None:
                left = end - time.time()
                if left <= 0:
                    del self.pending[rid]
                    raise TimeoutError("no reply to %r" % (msg.get("path") or msg.get("op")))
                self.cv.wait(left)
            r = self.pending.pop(rid)
        if not r.get("ok"):
            raise BridgeError(r.get("error"))
        return r.get("result")

    # -- API
    def call(self, path, *args, timeout=60):
        """messenger.<path>(*args) in the add-on; returns the JSON-ised result."""
        return self.request(timeout=timeout, op="call", path=path, args=list(args))

    def listen(self, event):
        return self.request(op="listen", event=event)

    def unlisten(self, event):
        return self.request(op="unlisten", event=event)

    def eval(self, code, *args, timeout=60):
        """Run `code` (body of an async function(messenger, args)) inside the add-on."""
        return self.request(timeout=timeout, op="eval", code=code, args=list(args))

    def ping(self):
        return self.request(op="ping")

    def hello(self):
        return self.request(op="hello")

    def wait_event(self, name, timeout=60, where=None, since=0):
        end = time.time() + timeout
        with self.cv:
            while True:
                for ev in self.events:
                    if ev["event"] == name and ev["t"] / 1000 >= since and (where is None or where(ev)):
                        return ev
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError("event %s not seen within %ss" % (name, timeout))
                self.cv.wait(left)

    def close(self):
        try:
            self.srv.close()
        finally:
            if os.path.exists(self.path):
                os.unlink(self.path)


def blob_bytes(result):
    """Decode a {__blob: true, b64: ...} result into bytes."""
    import base64
    return base64.b64decode(result["b64"])

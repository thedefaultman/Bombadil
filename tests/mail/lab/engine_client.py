"""The mail service's end of the engine protocol, small enough to drive a real add-on from a test.

The add-on talks to the native-messaging host and the host talks to a Unix socket; this listens there
and speaks docs/MAIL.md's "Engine protocol" as the service does: it sends requests with ids, takes
answers and events, uploads attachments in 384 KiB `blob` requests and collects the `blob` events of a
fetched one. It accepts the host both ways: the lab's own host relays frames as they come, and
`bin/bombadil-mail-host` opens with `{"op": "engine_hello"}`, which is dropped here.

    client = EngineClient(path)
    client.wait_connected(30)
    accounts = client.request("accounts")
    client.wait_event("new_mail", timeout=30)
    xfer = client.upload(b"bytes")
    data = client.fetch(client.request("attachment", account=a, key=k, part="1.2"))
"""

import base64
import json
import os
import socket
import threading
import time

CHUNK = 384 << 10
FRAME_MAX = 1_000_000


class EngineFailed(Exception):
    """The add-on answered no: str() is its sentence, `code` its reason."""

    def __init__(self, code, sentence):
        super().__init__(sentence)
        self.code = code


class EngineClient:
    def __init__(self, path):
        self.path = str(path)
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(4)
        self.cv = threading.Condition()
        self.conn = None
        self.connections = 0
        self.next_id = 0
        self.pending = {}
        self.events = []        # every event but `blob`, in order of arrival
        self.frames = []        # every frame the add-on sent, for a failing test to print
        self.blobs = {}         # xfer -> {"chunks": [bytes], "last": bool, "next": int}
        self.hello_frames = 0
        threading.Thread(target=self._accept, daemon=True).start()

    # -- plumbing

    def _accept(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with self.cv:
                self.conn = conn
                self.connections += 1
                self.cv.notify_all()
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        for line in conn.makefile("rb", buffering=1 << 20):
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if not isinstance(frame, dict) or frame.get("op") == "engine_hello":
                continue
            with self.cv:
                self.frames.append(frame)
                if len(self.frames) > 2000:
                    del self.frames[:1000]
                self._take(frame)
                self.cv.notify_all()
        with self.cv:
            if self.conn is conn:
                self.conn = None
            self.cv.notify_all()

    def _take(self, frame):
        if "event" in frame:
            if frame["event"] == "blob":
                entry = self.blobs.setdefault(frame["xfer"], {"chunks": [], "last": False, "next": 0})
                if frame.get("seq") == entry["next"] and not entry["last"]:
                    entry["chunks"].append(base64.b64decode(frame["data"]))
                    entry["next"] += 1
                    entry["last"] = frame.get("last") is True
                else:
                    entry["error"] = f"out of order: {frame.get('seq')!r}"
            else:
                if frame["event"] == "hello":
                    self.hello_frames += 1
                self.events.append(frame)
        elif frame.get("id") in self.pending:
            self.pending[frame["id"]] = frame

    def wait_connected(self, timeout=60):
        end = time.time() + timeout
        with self.cv:
            while self.conn is None:
                if time.time() >= end:
                    raise TimeoutError(f"the add-on's host did not connect to {self.path}")
                self.cv.wait(end - time.time())
        return self

    def close(self):
        try:
            self.server.close()
        finally:
            if self.conn is not None:
                try:
                    self.conn.close()
                except OSError:
                    pass
            if os.path.exists(self.path):
                os.unlink(self.path)

    # -- requests

    def request(self, op, timeout=20, **args):
        """One request and its answer's result; EngineFailed when the add-on said no."""
        rid = self._send(op, args)
        end = time.time() + timeout
        with self.cv:
            while self.pending[rid] is None:
                left = end - time.time()
                if left <= 0:
                    del self.pending[rid]
                    raise TimeoutError(f"no answer to {op} in {timeout}s")
                self.cv.wait(left)
            answer = self.pending.pop(rid)
        if not answer.get("ok"):
            raise EngineFailed(answer.get("code"), answer.get("error"))
        return answer.get("result")

    def start(self, op, **args):
        """Send a request without waiting; `finish(token)` takes its answer."""
        return self._send(op, args)

    def finish(self, rid, timeout=20):
        end = time.time() + timeout
        with self.cv:
            while self.pending[rid] is None:
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(f"no answer to request {rid} in {timeout}s")
                self.cv.wait(left)
            return self.pending.pop(rid)

    def _send(self, op, args):
        with self.cv:
            self.next_id += 1
            rid = self.next_id
            self.pending[rid] = None
            conn = self.conn
        if conn is None:
            raise ConnectionError("the add-on is not connected")
        data = (json.dumps({**args, "id": rid, "op": op}, ensure_ascii=False) + "\n").encode()
        if len(data) > FRAME_MAX:
            raise ValueError(f"a frame over the {FRAME_MAX} bytes Thunderbird takes from a host")
        conn.sendall(data)
        return rid

    # -- events

    def wait_event(self, name, timeout=30, where=None, after=0):
        """The first event called `name` at or after index `after` of `events` that `where` accepts."""
        end = time.time() + timeout
        with self.cv:
            while True:
                for event in self.events[after:]:
                    if event["event"] == name and (where is None or where(event)):
                        return event
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(f"no {name} event within {timeout}s")
                self.cv.wait(left)

    def mark(self):
        """The index to give `wait_event(after=)` so that only later events count."""
        with self.cv:
            return len(self.events)

    # -- files

    def upload(self, data, xfer=None, chunk=CHUNK):
        """Give the add-on a file as `blob` requests, one at a time, and return its transfer name."""
        xfer = xfer or f"t-{os.getpid()}-{int(time.time() * 1000) % 10**9}"
        pieces = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [b""]
        for seq, piece in enumerate(pieces):
            self.request("blob", xfer=xfer, seq=seq, last=seq == len(pieces) - 1,
                         data=base64.b64encode(piece).decode("ascii"))
        return xfer

    def fetch(self, info, timeout=60):
        """The bytes of a fetched attachment: `info` is what the `attachment` request answered."""
        xfer = info["xfer"]
        end = time.time() + timeout
        with self.cv:
            while True:
                entry = self.blobs.get(xfer)
                if entry is not None and entry["last"]:
                    del self.blobs[xfer]
                    return b"".join(entry["chunks"])
                if entry is not None and "error" in entry:
                    raise ValueError(f"blob {xfer}: {entry['error']}")
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(f"the file {xfer} did not arrive")
                self.cv.wait(left)

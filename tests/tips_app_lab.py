"""What the Tips app's tests stand on: a stand-in for agentd's socket as the app sees it, the tips it lists, and
the state file agentd writes.

`FakeAgentd` greets every connection the way agentd does (a status, the entries, the Tips card, the coding
sessions: all things the app must ignore), answers {"type": "tips", "op": "list"} with the tips it was given, and
keeps every line it is sent and how many connections are open at any moment, so a test can say that nothing the
app does leaves a socket open. `listing()` is what agentd's list says on a machine that can back every tip: the
shipped table through the real Tips object. Every shipped row has its own beats, so the window plays the real
scenes; the fixture scenes (tests/tip_fixtures.py) are only for a row that arrives without beats (a row being
added), and a test that needs a tip with no scene adds a made-up row with `beats = []` to the listing.
"""

import copy
import importlib.util
import itertools
import json
import os
import socket
import threading
import time
from datetime import date
from pathlib import Path

import tip_fixtures as fx
from PySide6.QtCore import QEventLoop, QTimer

from bombadil import tips as tips_mod

APP_DIR = Path(__file__).resolve().parents[1] / "share" / "apps" / "tips"


def load_app(name: str = "tips_app_under_test"):
    """The app's app.py as a module of its own, so a test can set its clocks before a Backend reads them."""
    spec = importlib.util.spec_from_file_location(name, APP_DIR / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout_ms: int = 4000) -> bool:
    """Qt's loop keeps running while a test waits, which the backend and the window need."""
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        if cond():
            return True
        spin(10)
    return cond()


# The fixture scenes, for a tip of the table that arrives without beats of its own (none does today: every
# shipped row has its scene, and `listing()` leaves a row that has beats alone).
SCENES = {"make-an-app": fx.BUILD, "picture": fx.PICTURE, "esc": fx.ESC, "timer": fx.TIMER, "machine": fx.MACHINE,
          "bang": fx.BANG, "change-app": fx.CHANGE, "open-app": fx.OPEN,
          "passwords": fx.LOCK,
          "toolchain": fx.ROUTE, "disk": fx.DISK}
MACHINE = tips_mod.Facts(ai="Claude", installed=True, app="Tickets", project="myapp", desk_seen=True, keep=True)


_LISTINGS = itertools.count()      # each listing starts from a state of its own: what an earlier one tried is not carried


def listing(tmp: Path, facts=MACHINE, tried=(), new=()) -> list[dict]:
    """agentd's list: `tips.listing()` on the shipped table for a machine with these facts. `tried` are marked, `new`
    are tips an update brought (not known). A row without beats (none today) gets its fixture scene."""
    t = tips_mod.Tips(path=tmp / f"lab-tips-{next(_LISTINGS)}.json", clock=lambda: date(2026, 11, 9))
    t.facts = facts
    t.ready()
    if new:
        t.state.known = [i for i in t.state.known if i not in new]
    t.tried(list(tried))
    rows = t.listing()
    for row in rows:
        if not row["beats"] and row["id"] in SCENES:
            row["beats"] = copy.deepcopy(SCENES[row["id"]]["beats"])
    return rows


def write_state(path: Path, **fields) -> None:
    """tips.json as agentd writes it: all or nothing, by renaming a temporary file over it."""
    state = {"first_ready": "2026-11-09", "days": ["2026-11-09"], "tried": [], "known": [], "open_on": "",
             "desk_seen": False}
    if path.exists():
        try:
            state.update(json.loads(path.read_text()))
        except ValueError:
            pass
    state.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, path)


class FakeAgentd:
    """`mode`: "answer" (the list is sent), "silent" (it connects and says nothing more), "hangup" (it closes at
    once), "down" (nothing listens: `stop()` first)."""

    def __init__(self, path: Path, tips=None):
        self.path = str(path)
        self.tips = list(tips or [])
        self.mode = "answer"
        self.lines: list[dict] = []          # (every line any connection sent)
        self.by_connection: list[list[dict]] = []
        self.connections = 0
        self.open = 0
        self.max_open = 0
        self.clients: list[socket.socket] = []
        self.hooks: dict[tuple, object] = {}
        self.status = {"type": "status", "busy": False, "provider": "claude", "setup": "ready"}
        self._lock = threading.Lock()
        self.sock = None

    def start(self):
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(16)
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            sock.close()
        for c in list(self.clients):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()
        self.clients.clear()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except (OSError, AttributeError):
                return
            with self._lock:
                self.connections += 1
                self.open += 1
                self.max_open = max(self.max_open, self.open)
                mine: list[dict] = []
                self.by_connection.append(mine)
            self.clients.append(conn)
            threading.Thread(target=self._serve, args=(conn, mine), daemon=True).start()

    def say(self, conn, msg: dict):
        try:
            conn.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            pass

    def _serve(self, conn, mine):
        try:
            if self.mode == "hangup":
                return
            # What agentd tells every client that connects, before it answers a line.
            self.say(conn, self.status)
            self.say(conn, {"type": "entries", "entries": [{"name": f"app-{i}", "title": f"App {i}", "kind": "app",
                                                            "words": [f"app {i}"]} for i in range(30)]})
            self.say(conn, {"type": "tips", "present": True, "card": None})
            self.say(conn, {"type": "dev", "sessions": []})
            buf = b""
            while True:
                try:
                    data = conn.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    self.lines.append(msg)
                    mine.append(msg)
                    hook = self.hooks.get((msg.get("type"), msg.get("op")))
                    if hook is not None:
                        hook(msg)
                    if msg.get("type") == "tips" and msg.get("op") == "list" and self.mode == "answer":
                        self.say(conn, {"type": "tips", "list": copy.deepcopy(self.tips)})
        finally:
            with self._lock:
                self.open -= 1
            if conn in self.clients:
                self.clients.remove(conn)
            conn.close()

    # -- what a test asks of it --

    def of(self, kind: str, op: str | None = None) -> list[dict]:
        return [m for m in self.lines if m.get("type") == kind and (op is None or m.get("op") == op)]

    def wait_for(self, cond, seconds: float = 5.0, what: str = "") -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.01)
        return cond()

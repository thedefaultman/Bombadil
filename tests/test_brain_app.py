"""The Brain app's backend (share/apps/brain/app.py) against a fake bombadil-brain.

The backend is what turns the brain's socket into what the window shows: the trail, the
thing in the middle, pushes from the launcher. A small threaded server plays the brain.
"""

import importlib.util
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

APP_PY = Path(__file__).resolve().parents[1] / "share" / "apps" / "brain" / "app.py"


@pytest.fixture(scope="module")
def qt():
    return QGuiApplication.instance() or QGuiApplication([])


def spin(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout_ms: int = 4000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if cond():
            return True
        spin(20)
    return cond()


def load_backend():
    spec = importlib.util.spec_from_file_location("brain_app_under_test", APP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBrain:
    """Answers requests with `answer(msg) -> result` (or raises to answer ok: false), and can
    push lines to every connected client."""

    def __init__(self, path: Path, answer):
        self.path = str(path)
        self.answer = answer
        self.requests: list[dict] = []
        self.clients: list[socket.socket] = []
        self.delay: dict[str, float] = {}
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.clients.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
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
                msg = json.loads(line)
                self.requests.append(msg)
                threading.Thread(target=self._reply, args=(conn, msg), daemon=True).start()

    def _reply(self, conn, msg):
        time.sleep(self.delay.get(str(msg.get("ref")), 0))
        try:
            out = {"id": msg["id"], "ok": True, "result": self.answer(msg)}
        except LookupError as e:
            out = {"id": msg["id"], "ok": False, "error": str(e.args[0])}
        try:
            conn.sendall((json.dumps(out) + "\n").encode())
        except OSError:
            pass

    def push(self, msg: dict):
        for c in self.clients:
            try:
                c.sendall((json.dumps(msg) + "\n").encode())
            except OSError:
                pass

    def ops(self, op: str) -> list[dict]:
        return [r for r in self.requests if r.get("op") == op]

    def close(self):
        for c in self.clients:
            c.close()
        self.sock.close()


def thing(ref, title=None, area=None, id=1):
    return {"thing": {"id": id, "ref": ref, "title": title or ref.rsplit("/", 1)[-1], "kind": "file", "path": ref,
                      "area": area},
            "preview": {"type": "none"}, "slots": {}, "children": None, "description": None}


PROJECT = {"id": 9, "ref": "/p/Bombadil", "title": "Bombadil", "kind": "project"}


def answers(requested=None):
    ids = {"/p/Bombadil/a.py": 1, "/p/Bombadil/b.py": 2, "/p/Bombadil/c.py": 3, "/p/Bombadil": 9}

    def answer(msg):
        op = msg["op"]
        if op == "requested":
            return requested
        if op == "status":
            return {"text": "Getting to know your files, 40%"}
        if op == "focus":
            ref = msg["ref"]
            if ref not in ids:
                raise LookupError("The brain does not know this yet.")
            return thing(ref, area=PROJECT, id=ids[ref])
        if op == "search":
            return [{"id": 1, "ref": "/p/Bombadil/a.py", "title": "a.py", "kind": "file"}]
        return None
    return answer


@pytest.fixture
def brain(tmp_path, monkeypatch, qt):
    made = []

    def start(requested=None):
        path = tmp_path / "brain.sock"
        monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(path))
        fake = FakeBrain(path, answers(requested))
        made.append(fake)
        return fake
    yield start
    for f in made:
        f.close()


def titles(b):
    return [s["title"] for s in b.trail]


def test_opens_what_the_launcher_asked_for_with_its_area_first(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.focus is not None)
    assert b.focus["thing"]["ref"] == "/p/Bombadil/a.py"
    assert titles(b) == ["Bombadil", "a.py"]
    assert b.status == "Getting to know your files, 40%"
    assert fake.ops("subscribe")


def test_opens_home_when_nothing_was_asked_for(brain, qt, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    fake = brain(None)
    b = load_backend().Backend()
    assert wait_until(lambda: fake.ops("focus"))
    assert fake.ops("focus")[0]["ref"] == str(tmp_path)
    assert b.connected and b.trail[0]["ref"] == str(tmp_path)


def test_the_trail_goes_forward_back_and_jumps(brain, qt):
    brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: titles(b) == ["Bombadil", "a.py"])
    b.go("/p/Bombadil/b.py")
    b.go("/p/Bombadil/c.py")
    assert wait_until(lambda: titles(b) == ["Bombadil", "a.py", "b.py", "c.py"])
    assert b.canBack
    b.back()
    assert wait_until(lambda: b.focus["thing"]["ref"] == "/p/Bombadil/b.py")
    assert titles(b) == ["Bombadil", "a.py", "b.py"]
    b.jump(0)
    assert wait_until(lambda: b.focus["thing"]["ref"] == "/p/Bombadil")
    assert titles(b) == ["Bombadil"]
    assert not b.canBack


def test_a_slow_answer_for_an_old_thing_never_replaces_the_new_one(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.focus is not None)
    fake.delay["/p/Bombadil/b.py"] = 0.5
    b.go("/p/Bombadil/b.py")
    b.go("/p/Bombadil/c.py")
    assert wait_until(lambda: b.focus["thing"]["ref"] == "/p/Bombadil/c.py")
    spin(700)
    assert b.focus["thing"]["ref"] == "/p/Bombadil/c.py"


def test_show_pushes_start_a_new_trail_once_each(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    shown = []
    b.showRequested.connect(lambda: shown.append(1))
    assert wait_until(lambda: b.focus is not None)
    b.go("/p/Bombadil/b.py")
    assert wait_until(lambda: b.focus["thing"]["ref"] == "/p/Bombadil/b.py")
    fake.push({"push": "show", "ref": "/p/Bombadil/a.py", "seq": 1})   # the one already handled
    spin(200)
    assert shown == [] and b.focus["thing"]["ref"] == "/p/Bombadil/b.py"
    fake.push({"push": "show", "ref": "/p/Bombadil/c.py", "seq": 2})
    assert wait_until(lambda: b.focus["thing"]["ref"] == "/p/Bombadil/c.py")
    assert shown == [1]
    assert titles(b) == ["Bombadil", "c.py"]


def test_changes_to_things_on_screen_refresh_it_and_others_do_not(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.focus is not None)
    before = len(fake.ops("focus"))
    fake.push({"push": "changed", "things": [555], "t": 0})
    spin(500)
    assert len(fake.ops("focus")) == before
    fake.push({"push": "changed", "things": [1], "t": 0})
    fake.push({"push": "changed", "things": [1], "t": 0})
    assert wait_until(lambda: len(fake.ops("focus")) == before + 1)
    spin(500)
    assert len(fake.ops("focus")) == before + 1    # a burst is one refresh


def test_an_unknown_thing_says_so_and_keeps_the_trail(brain, qt):
    brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.focus is not None)
    b.go("/nowhere")
    assert wait_until(lambda: b.error == "The brain does not know this yet.")
    assert b.focus is None and titles(b) == ["Bombadil", "a.py", "nowhere"]
    b.back()
    assert wait_until(lambda: b.error == "" and b.focus["thing"]["ref"] == "/p/Bombadil/a.py")


def test_it_waits_for_the_brain_and_connects_when_it_comes(brain, qt, tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(tmp_path / "brain.sock"))
    b = load_backend().Backend()
    spin(300)
    assert not b.connected and b.focus is None
    brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    assert wait_until(lambda: b.connected and b.focus is not None, 5000)


def test_search_asks_once_after_typing_stops(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.connected)
    for q in ("s", "sn", "sna", "snap"):
        b.search(q)
    assert wait_until(lambda: b.results)
    assert [r["q"] for r in fake.ops("search")] == ["snap"]
    b.search("")
    assert b.results == []


def test_paths_read_with_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    mod = load_backend()
    assert mod.tilde(str(tmp_path / "Documents/lease.pdf")) == "~/Documents/lease.pdf"
    assert mod.tilde(str(tmp_path)) == "~"
    assert mod.tilde("/etc/hosts") == "/etc/hosts"

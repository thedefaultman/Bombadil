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
            try:
                c.shutdown(socket.SHUT_RDWR)   # a thread blocked in recv keeps a plain close() open
            except OSError:
                pass
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
            return {"text": "Getting to know your files, 40%", "walking": "first", "watching": True}
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


def test_the_subtitle_speaks_only_while_it_matters(qt, monkeypatch, tmp_path):
    monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(tmp_path / "none.sock"))
    b = load_backend().Backend()
    b._set_status({"text": "Knows 12 things; watching every save.", "walking": None, "watching": True})
    assert b.status == ""
    b._set_status({"text": "Knows 12 things; saves are not watched on this system.", "walking": None,
                   "watching": False})
    assert b.status.startswith("Knows 12 things; saves are not watched")
    b._set_status({"text": "Catching up on your files, 10%", "walking": "reconcile", "watching": True})
    assert b.status == "Catching up on your files, 10%"


# -- review: what the first pass got wrong --

def _with(base, extra):
    """The default answers, with `extra(msg)` first (None means "not mine")."""
    def answer(msg):
        got = extra(msg)
        return got if got is not None else base(msg)
    return answer


def test_a_change_to_an_event_id_does_not_refresh_what_is_on_screen(brain, qt):
    def extra(msg):
        if msg["op"] == "focus" and msg["ref"] == "/p/Bombadil/a.py":
            f = thing("/p/Bombadil/a.py", id=1)
            item = {"id": 5, "ref": "/p/Bombadil/b.py", "title": "b.py", "kind": "file"}
            f["slots"] = {"changes": {"items": [{"id": 777, "title": "made it", "t": 1}], "total": 1},
                          "read_with": {"items": [item], "total": 1}}
            return f

    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    fake.answer = _with(answers({"ref": "/p/Bombadil/a.py", "seq": 1}), extra)
    b = load_backend().Backend()
    assert wait_until(lambda: b.focus is not None)
    before = len(fake.ops("focus"))
    fake.push({"push": "changed", "things": [777], "t": 0})   # an event's id, not a thing's
    spin(500)
    assert len(fake.ops("focus")) == before
    fake.push({"push": "changed", "things": [5], "t": 0})
    assert wait_until(lambda: len(fake.ops("focus")) == before + 1)


def folder_answers(total):
    names = [f"f{i:04d}" for i in range(total)]

    def item(n):
        return {"id": 100 + int(n[1:]), "ref": f"/p/Docs/{n}", "title": n, "kind": "file",
                "path": f"/p/Docs/{n}"}

    def extra(msg):
        if msg["op"] == "focus" and msg["ref"] == "/p/Docs":
            f = thing("/p/Docs", id=50)
            f["thing"]["kind"] = "folder"
            f["children"] = {"items": [item(n) for n in names[:200]], "total": total}
            return f
        if msg["op"] == "children":
            o, n = msg["offset"], msg["limit"]
            return {"items": [item(x) for x in names[o:o + n]], "total": total}
    return extra


def test_show_in_folder_pages_on_until_the_entry_is_there_and_then_lets_go(brain, qt):
    fake = brain({"ref": "/p/Docs", "seq": 1})
    fake.answer = _with(answers({"ref": "/p/Docs", "seq": 1}), folder_answers(450))
    b = load_backend().Backend()
    assert wait_until(lambda: b.focus is not None)
    assert len(b.focus["children"]["items"]) == 200
    b.showInFolder("/p/Docs/f0430", "/p/Docs")
    assert wait_until(lambda: any(i["ref"] == "/p/Docs/f0430" for i in b.focus["children"]["items"]))
    assert [(r["offset"], r["limit"]) for r in fake.ops("children")] == [(200, 200), (400, 200)]
    assert len(b.focus["children"]["items"]) == 450
    assert b.select == "/p/Docs/f0430"
    b.settled()
    assert b.select == ""
    b.moreChildren()   # nothing is left
    spin(200)
    assert len(fake.ops("children")) == 2


def test_a_folder_that_never_shows_the_entry_is_not_paged_through_forever(brain, qt):
    fake = brain({"ref": "/p/Docs", "seq": 1})
    fake.answer = _with(answers({"ref": "/p/Docs", "seq": 1}), folder_answers(5000))
    mod = load_backend()
    b = mod.Backend()
    assert wait_until(lambda: b.focus is not None)
    b.showInFolder("/p/Docs/gone", "/p/Docs")
    assert wait_until(lambda: len(b.focus["children"]["items"]) >= mod.SEEK_MAX)
    spin(300)
    assert len(b.focus["children"]["items"]) == mod.SEEK_MAX


def test_enter_gets_results_for_what_was_typed_now(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.connected)
    b.search("snap")
    b.searchNow()   # Enter before the pause was over
    assert wait_until(lambda: b.resultsFor == "snap" and b.results)
    assert len(fake.ops("search")) == 1
    b.search("snapshot")
    assert b.resultsFor == "snap"   # the old results are for the old text
    b.search("")
    assert b.resultsFor == "" and b.results == []


def test_an_app_opens_by_its_name_only(qt, monkeypatch, tmp_path):
    monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(tmp_path / "none.sock"))
    mod = load_backend()
    b = mod.Backend()
    started = []
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/bin/bombadil-app")
    monkeypatch.setattr(mod.QProcess, "startDetached",
                        staticmethod(lambda p, a: started.append((p, a)) or (True, 1)))
    assert b.openApp("app:brain") and b.openApp("brain")
    assert started == [("/bin/bombadil-app", ["run", "brain"])] * 2
    for bad in ("", "app:", "-x", "../x", "a/b", "app:--help"):
        assert not b.openApp(bad)
    assert len(started) == 2


def test_a_pdf_page_is_drawn_once_kept_by_size_and_mtime_and_a_failure_is_said(qt, monkeypatch, tmp_path):
    monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(tmp_path / "none.sock"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "pdftoppm"   # a stand-in that "draws" a picture named by its last argument
    fake.write_text('#!/bin/sh\nfor a; do last="$a"; done\ncase "$*" in *broken*) exit 1;; esac\n'
                    'echo picture > "$last.png"\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    mod = load_backend()
    b = mod.Backend()
    ready, failed = [], []
    b.previewReady.connect(ready.append)
    b.previewFailed.connect(failed.append)
    pdf = tmp_path / "lease #1 100%.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert b.pdfPage(str(pdf)) == ""
    assert b.pdfPage(str(pdf)) == ""   # still drawing: not started twice
    assert wait_until(lambda: ready == [str(pdf)])
    url = b.pdfPage(str(pdf))
    assert url.startswith("file:///") and url.endswith(".png")
    assert not list((tmp_path / "cache" / "bombadil" / "brain").glob("*.part*"))
    pdf.write_bytes(b"%PDF-1.4 changed")   # a new file under the same name is drawn again
    assert b.pdfPage(str(pdf)) == ""
    assert wait_until(lambda: len(ready) == 2)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"x")
    assert b.pdfPage(str(broken)) == ""
    assert wait_until(lambda: failed == [str(broken)])
    assert b.pdfPage(str(broken)) == ""   # remembered: it says so again without another try
    assert wait_until(lambda: len(failed) == 2)
    assert b.pdfPage(str(tmp_path / "missing.pdf")) == ""
    assert wait_until(lambda: len(failed) == 3)
    assert b.fileBytes(str(pdf)) == len(b"%PDF-1.4 changed")
    assert b.fileBytes(str(tmp_path / "missing.pdf")) == 0


def test_the_cache_of_pdf_pages_keeps_only_the_newest(qt, monkeypatch, tmp_path):
    monkeypatch.setenv("BOMBADIL_BRAIN_SOCKET", str(tmp_path / "none.sock"))
    mod = load_backend()
    monkeypatch.setattr(mod, "PREVIEWS_KEPT", 3)
    for i in range(6):
        f = tmp_path / f"{i}.png"
        f.write_text("x")
        os.utime(f, (100 + i, 100 + i))
    mod.Backend._prune(tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["3.png", "4.png", "5.png"]


def test_layouts_that_rebuild_their_children_say_they_take_no_free_height():
    """Qt segfaulted on most moves to another thing (8 of 8 offscreen runs) when the kit's Panel
    and AppWindow `_grows` asked a layout for its size limits while its Repeater was replacing
    its children. `_grows` asks only layouts with Layout.fillHeight, so these say false."""
    for name, opener in (("Trail.qml", "RowLayout {"), ("Slot.qml", "GridLayout {"),
                         ("Changes.qml", "RowLayout {")):
        src = (APP_PY.parent / name).read_text()
        head = src[src.index(opener):src.index("Repeater {")]
        assert "Layout.fillHeight: false" in head, name


def test_what_the_brain_said_about_itself_goes_when_it_goes(brain, qt):
    fake = brain({"ref": "/p/Bombadil/a.py", "seq": 1})
    b = load_backend().Backend()
    assert wait_until(lambda: b.connected and b.status != "")
    fake.close()
    assert wait_until(lambda: not b.connected)
    assert b.status == "" and b.focus is not None   # the last thing stays for when it comes back

"""The Noticed window (share/apps/noticed), loaded the way bombadil-app loads it and driven by a fake agentd.

Each scenario runs in a child process with its own offscreen app: a QGuiApplication and the kit's `App`
singleton exist once per process. The child starts a small socket server that speaks the window's side of
docs/LOOP.md (noticed_list -> noticed_full, noticed_do -> noticed_result), loads the real main.qml through
the runtime's Host, presses real buttons with the mouse and reads what the window draws. Nothing opens a
browser or touches the network: the window has no such path, and the fake agentd is the only peer.

Set NOTICED_SHOTS to a directory to have the scenarios save a picture of each state there.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "share" / "apps" / "noticed"

if not (ROOT / "share" / "qml" / "Bombadil" / "qmldir").exists():
    pytest.skip("the app kit is not in this tree", allow_module_level=True)
pytest.importorskip("PySide6")

# Runs in the child before each scenario's body: the fake agentd, the runtime's Host on the real app, and
# a few helpers to look at the window and press its buttons.
PRELUDE = r'''
import json, os, socket, sys, tempfile, threading, time
from pathlib import Path
from PySide6.QtCore import QEventLoop, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtQml import QQmlEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from bombadil.appkit import context, engine, runtime

NOW = time.time()
OUT = {}
SHOTS = os.environ.get("NOTICED_SHOTS", "")

GOES = ["What happened: the details drawer took no keyboard, 3 times on 2 days",
        "The build: d9dde3b",
        "Versions: Hyprland 0.56.2, Quickshell 0.3.1, Claude Code 2.1.283",
        "The failing check and its output: bombadil-details is the active window within 2 s",
        "A picture of the windows as boxes, without titles",
        "2 lines of the log, with your folders removed"]
STAYS = ["What you typed or said", "Your files and their paths", "The titles of your windows and pages",
         "Screenshots", "Your name"]
REPORT = ("Title: Details drawer takes no keyboard (Hyprland 0.56.2) [fp 3c91a0]\n"
          "Seen: 3 times on 2 days, build d9dde3b, laptop VM\n"
          "Expected: bombadil-details is the active window within 2 s\n"
          "Observed: the active window was bombadil-bar (probe drawer-focus, 2 retries)\n"
          "Picture: the windows as boxes, drawn from hyprctl rectangles\n"
          "Versions: Quickshell 0.3.1, Claude Code 2.1.283, codex-cli 0.157.1\n"
          "Tried: nothing has fixed it on this machine yet")
PREVIEW = {"text": REPORT, "goes": GOES, "stays": STAYS}


def sample():
    """What a service with a week of their asks would say."""
    return {
        "hidden": False, "held": False, "resting": "",
        "asks": [
            {"id": "g-a1", "title": "show me my passwords", "n": 4, "days": 3, "last": NOW - 86400,
             "state": "offered", "became": "offered: a word",
             "sentences": ["show me my passwords", "open my passwords", "passwords app"],
             "what": "Say “my passwords” and Passwords opens. No model, under a tenth of a second.",
             "primary": {"label": "Make the word", "op": "accept", "form": "word"},
             "forms": [{"form": "word", "label": "A word", "recommended": True},
                       {"form": "app", "label": "An app"}]},
            {"id": "g-b2", "title": "what is eating my disk", "n": 5, "days": 4, "last": NOW - 3 * 3600,
             "state": "made", "became": "made a word",
             "sentences": ["what is eating my disk", "disk hog?"]},
            {"id": "g-c3", "title": "tidy my downloads", "n": 3, "days": 2, "last": NOW - 2 * 86400,
             "state": "counting", "became": "", "sentences": ["tidy my downloads"]},
        ],
        "changes": [
            {"id": "c-1", "title": "Made “my passwords” open Passwords.", "t": NOW - 7200, "what": "word",
             "undone": False, "can_undo": True},
            {"id": "c-2", "title": "Made “disk” open Disk.", "t": NOW - 5 * 86400, "what": "word",
             "undone": True, "can_undo": False},
        ],
        "found": [
            {"id": "f-1", "title": "The details drawer took no keyboard.", "meta": "3 times on 2 days",
             "fp": "hypr:drawer-focus:3c91a0", "state": "open", "can_send": True,
             "why": ["After you opened Details, the bar still had the keyboard.",
                     "It was the same two seconds later."]},
            {"id": "f-2", "title": "Two apps opened on top of each other.", "meta": "2 times on 1 day",
             "fp": "hypr:apps-stacked:77b2e1", "state": "open", "can_send": True},
        ],
        "said_no": [{"id": "g-x9", "title": "log a 5 km run", "t": NOW - 9 * 86400, "form": "app"}],
        "words": [{"phrase": "my passwords", "opens": {"kind": "app", "name": "passwords"}, "away": False},
                  {"phrase": "disk", "opens": {"kind": "app", "name": "disk-usage"}, "away": True}],
    }


class FakeAgentd:
    """The window's side of docs/LOOP.md, and what a real agentd says around it."""

    def __init__(self, path, full=None):
        self.path, self.full = path, full if full is not None else sample()
        self.received, self.conns, self.sock, self.alive = [], [], None, False
        self.lists = 0              # noticed_list requests heard
        self.answer_lists = True
        self.answer_dos = True
        self.broadcast = True       # a `noticed` to everyone after each change, as the service does
        self.fail = {}              # op -> text: answer not ok
        self.texts = {}             # op -> text of a good answer
        self.previews = {"f-1": PREVIEW}
        self.lock = threading.Lock()

    def start(self):
        Path(self.path).unlink(missing_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.path)
        s.listen(8)
        self.sock, self.alive = s, True
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        self.alive = False
        for c in [self.sock, *self.conns]:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
            try:
                c.close()
            except (OSError, AttributeError):
                pass
        self.conns = []
        Path(self.path).unlink(missing_ok=True)

    def _accept(self):
        while self.alive:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def send(self, c, msg):
        data = msg if isinstance(msg, bytes) else (json.dumps(msg) + "\n").encode()
        try:
            with self.lock:
                c.sendall(data)
        except OSError:
            pass

    def push(self, msg):
        for c in list(self.conns):
            self.send(c, msg)

    def noticed(self):
        found = [f for f in self.full.get("found", []) if f.get("state") in ("open", "reported")]
        return {"type": "noticed", "count": len(found), "hidden": self.full.get("hidden", False),
                "resting": "", "lately": "", "rows": []}

    def _serve(self, c):
        # What agentd says to anyone who connects: its status, then the chip's state.
        self.send(c, {"type": "status", "busy": False, "provider": "claude", "turns": 0, "queue": []})
        self.send(c, self.noticed())
        buf = b""
        while self.alive:
            try:
                data = c.recv(65536)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self.received.append(msg)
                self.handle(c, msg)

    def handle(self, c, msg):
        kind = msg.get("type")
        if kind == "noticed_list":
            self.lists += 1
            if self.answer_lists:
                self.send(c, {"type": "noticed_full", **self.full})
        elif kind == "noticed_do" and self.answer_dos:
            op, id_ = msg.get("op"), msg.get("id", "")
            if op in self.fail:
                self.send(c, {"type": "noticed_result", "op": op, "id": id_, "ok": False, "text": self.fail[op]})
                return
            extra = self.apply(op, id_)
            res = {"type": "noticed_result", "op": op, "id": id_, "ok": True,
                   "text": self.texts.get(op, "Done."), **extra}
            self.send(c, res)
            if self.broadcast:
                self.push(self.noticed())

    def apply(self, op, id_):
        """What the service does to its lists for each op; returns extra keys for the result."""
        f = self.full
        rows = lambda key: f.get(key, [])
        by = lambda key: next((r for r in rows(key) if r.get("id") == id_), None)
        if op == "undo" and by("changes"):
            by("changes").update(undone=True, can_undo=False)
        elif op == "bring_back":
            if by("changes"):
                by("changes").update(undone=False, can_undo=True)
            if by("said_no"):
                f["said_no"] = [r for r in rows("said_no") if r["id"] != id_]
            for w in rows("words"):
                if w["phrase"] == id_:
                    w["away"] = False
        elif op == "forget_asks":
            f["asks"] = []
        elif op == "clear_found":
            f["found"] = []
        elif op in ("hide", "show"):
            f["hidden"] = op == "hide"
        elif op == "report" and by("found"):
            by("found").update(state="reported", preview=self.previews.get(id_, PREVIEW))
            return {"preview": self.previews.get(id_, PREVIEW)}
        elif op == "send" and by("found"):
            by("found").update(state="sent")
        elif op in ("never", "not_now") and by("found"):
            f["found"] = [r for r in rows("found") if r["id"] != id_]
        elif op == "accept" and by("asks"):
            by("asks").update(state="made", became="made a word")
        elif op == "preview":
            return {"preview": "Say “my passwords” and Passwords opens. Example: “show me my passwords”."}
        return {}

    def did(self, op=None, id_=None):
        """The noticed_do messages heard, optionally only one op (and row)."""
        return [m for m in list(self.received) if m.get("type") == "noticed_do"
                and (op is None or m.get("op") == op) and (id_ is None or m.get("id") == id_)]


def pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, ms=4000):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        pump(25)
    try:
        return bool(cond())
    except Exception:
        return False


short = tempfile.mkdtemp(prefix="nt")
sockpath = os.path.join(short, "s")
os.environ["BOMBADIL_SOCKET"] = sockpath
fake = FakeAgentd(sockpath)
ctx = context.for_app("noticed")
qt = engine.make_app(ctx)
host = runtime.Host(ctx)
ok = host.start()
assert ok, host.collector.summary()
root, win = host.root, host.window
backend = host.backend
# The retry is a few seconds in life; here it only has to be quick.
backend.tick_ms = 200
backend._tick.start(200)


def items(name=None):
    """Every item on screen, in tree order, found by walking the visual tree: the delegates a Repeater makes
    are not children of anything in the object tree."""
    out = []

    def walk(item):
        for c in item.childItems():
            out.append(c)
            walk(c)

    walk(win.contentItem())
    return [i for i in out if not name or i.objectName() == name]


def obj(name):
    """A popup or other object that is not an item (a ConfirmDialog)."""
    return root.findChild(QObject, name)


def find(name):
    found =[i for i in items(name) if i.isVisible()]
    return found[0] if found else None


def prop(name, key):
    it = find(name)
    return None if it is None else it.property(key)


def enum(item, key):
    """A property whose type PySide cannot convert (an enum): read through the JS engine, as an int."""
    QQmlEngine.setObjectOwnership(item, QQmlEngine.ObjectOwnership.CppOwnership)
    return host.engine.newQObject(item).property(key).toInt()


def texts():
    """Everything the window shows as text, in tree order."""
    out = []
    for i in items():
        if not any(i.inherits(k) for k in ("QQuickText", "QQuickTextEdit")):
            continue      # the text itself, not the Button or Badge that holds it
        t = i.property("text")
        if isinstance(t, str) and t.strip() and i.isVisible():
            out.append(t)
    return out


def shows(text):
    return any(text == t for t in texts())


def has(part):
    return any(part in t for t in texts())


def reveal(item):
    """Scroll whatever holds the item so its middle is on screen."""
    p = item.parentItem()
    while p is not None:
        if p.property("contentY") is not None and p.property("contentHeight") is not None:
            pos = item.mapToItem(p.property("contentItem"), QPointF(0, item.height() / 2))
            want = max(0, min(pos.y() - p.height() / 2, p.property("contentHeight") - p.height()))
            p.setProperty("contentY", want)
            pump(40)
        p = p.parentItem()


def click(name):
    it = find(name)
    assert it is not None, f"nothing visible named {name}"
    assert it.isEnabled(), f"{name} is disabled"
    reveal(it)
    c = it.mapToScene(QPointF(it.width() / 2, it.height() / 2))
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(c.x()), int(c.y())))
    pump(40)


def shot(name):
    if SHOTS:
        pump(200)
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        host.current_window().grabWindow().save(str(Path(SHOTS) / f"{name}.png"))


def settled():
    return backend.property("ready") and backend.property("full")["asks"] is not None
'''

# Runs the scenario's body where the prelude left off, and says which of its lines failed.
RUNNER = r'''
import traceback
try:
    exec(compile(BODY, "<body>", "exec"), globals())
except BaseException as e:
    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "<body>"]
    if frames:
        print(f"FAILED at body line {frames[-1].lineno}: {BODY.splitlines()[frames[-1].lineno - 1].strip()}",
              file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)
'''

EPILOGUE = r'''
host.close()
fake.stop()
print("RESULT " + json.dumps(OUT, default=str))
'''


def drive(home: Path, body: str, timeout: int = 90) -> dict:
    """Runs `body` after the prelude in a child process and returns what it put in OUT."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
           "PYTHONPATH": str(ROOT / "src")}
    script = PRELUDE + f"BODY = {textwrap.dedent(body)!r}\n" + RUNNER + EPILOGUE
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=timeout, check=False)
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"driver failed (exit {r.returncode}):\n{r.stdout}\n{r.stderr[-4000:]}"
    return json.loads(lines[-1].removeprefix("RESULT "))


# -- what app.py does with what agentd sends (no Qt event loop needed) --

@pytest.fixture(scope="module")
def noticed():
    spec = importlib.util.spec_from_file_location("noticed_app", APP / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_full_list_is_cleaned_to_what_the_window_reads(noticed):
    full = noticed.clean_full({
        "hidden": True, "held": True, "resting": "2026-11-03",
        "asks": [{"id": "g1", "title": "show me my passwords", "n": 4, "days": 3, "last": 1790000000,
                  "state": "offered", "sentences": ["show me my passwords", "open my passwords", "x", "y"]}],
        "changes": [{"id": "c1", "title": "Made a word.", "t": 1790000000.5, "undone": False}],
        "found": [{"id": "f1", "title": "A bug.", "state": "reported", "can_send": True,
                   "preview": {"text": "T", "goes": ["a"], "stays": ["b"]}}],
        "said_no": [{"id": "g2", "label": "log a run", "form": "D"}],
        "words": [{"phrase": "my passwords", "opens": {"kind": "app", "name": "password-manager"}, "away": True}],
    })
    assert full["hidden"] and full["held"] and full["resting"] == "3 Nov"
    ask = full["asks"][0]
    assert ask["offered"] and ask["primary"] == {"label": "Make it", "op": "accept", "form": ""}
    assert ask["sentences"] == ["open my passwords", "x"]      # their other words, not the title again
    assert full["changes"][0]["can_undo"] is True              # not said either way: Undo is offered
    assert full["found"][0]["preview"]["text"] == "T" and full["found"][0]["why"] == []
    assert full["said_no"][0]["title"] == "log a run" and full["said_no"][0]["form"] == "an app"
    assert full["words"][0] == {"id": "my passwords", "phrase": "my passwords", "opens": "Password manager",
                                "away": True}


def test_rows_that_cannot_be_told_apart_or_acted_on_are_left_out(noticed):
    full = noticed.clean_full({
        "asks": [None, 3, "x", {}, {"id": "g1"}, {"title": "no id"}, {"id": "g2", "title": "kept"}],
        "changes": "not a list", "found": [{"fp": "hypr:x:1", "title": "Has only its fingerprint."}],
        "said_no": [{"id": 7, "title": "a number id"}], "words": [{"opens": "app"}, {"phrase": ""}],
    })
    assert [a["id"] for a in full["asks"]] == ["g2"]
    assert full["changes"] == [] and full["words"] == []
    assert full["found"][0]["id"] == "hypr:x:1" and full["found"][0]["can_send"] is False
    assert full["said_no"][0]["id"] == "7"


def test_wrong_types_are_read_as_nothing_rather_than_failing(noticed):
    full = noticed.clean_full({
        "hidden": "yes", "resting": {"x": 1}, "asks": [{"id": ["g"], "title": "t"},
        {"id": "g1", "title": {"a": 1}, "sentences": [1, None, "fine"], "n": "four", "days": -2, "last": "soon"}],
    })
    assert full["hidden"] is True and full["resting"] == ""
    ask = full["asks"][0]          # the title came from their first sentence
    assert ask["title"] == "1" or ask["title"] == "fine"
    assert (ask["n"], ask["days"], ask["last"]) == (0, 0, 0.0)
    assert noticed.clean_full({"asks": None, "found": 4})["asks"] == []
    assert noticed.clean_result({"op": "undo", "id": 3, "ok": "no"}) == ("undo", "3", True, "", {})
    assert noticed.clean_result({"ok": False, "text": "  broke \n here "})[2:4] == (False, "broke here")


def test_the_words_of_a_preview_are_kept_whole(noticed):
    text = "line one\n\n  line two with   spaces\n" + "x" * 5000
    p = noticed.clean_result({"op": "report", "id": "f", "preview": {"text": text, "goes": ["a"]}})[4]
    assert p["text"] == text and p["goes"] == ["a"] and p["stays"] == []
    assert noticed.clean_result({"preview": "just a string"})[4]["text"] == "just a string"
    assert noticed.clean_result({"preview": 5})[4] == {}


def test_other_ways_come_from_the_forms_or_the_others(noticed):
    row = {"id": "g", "title": "t", "state": "offered", "primary": {"label": "Make the word", "op": "accept",
                                                                     "form": "word"},
           "forms": [{"form": "word", "label": "A word", "recommended": True}, {"form": "app", "label": "An app"},
                     {"form": "card", "label": "A card"}, {"form": "widget", "label": "A widget"}]}
    assert [w["form"] for w in noticed.clean_full({"asks": [row]})["asks"][0]["ways"]] == ["app", "card"]
    row = {"id": "g", "title": "t", "state": "offered", "others": [
        {"label": "Other ways", "op": "other_ways"}, {"label": "Not now", "op": "not_now"},
        {"label": "An app", "op": "accept", "form": "app"}]}
    ask = noticed.clean_full({"asks": [row]})["asks"][0]
    assert ask["ways"] == [{"label": "An app", "op": "accept", "form": "app"}]


def test_a_day_for_resting_is_said_however_it_comes(noticed):
    assert noticed._resting("3 Nov") == "3 Nov"
    assert noticed._resting("Resting offers until 3 Nov") == "3 Nov"
    assert noticed._resting("2026-11-03T10:00:00") == "3 Nov"
    assert noticed._resting(1793000000) == noticed._resting("1793000000") != ""
    assert noticed._resting(None) == "" and noticed._resting(True) == ""


# -- the window, driven --

def test_the_lists_fill_and_the_header_says_what_waits(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: backend.property("ready")), "never ready"
        assert wait_until(lambda: shows("What you ask most"))
        OUT["texts"] = texts()
        OUT["subtitle"] = root.property("subtitle")
        OUT["sections"] = [n for n in ("asks", "changes", "found", "words", "said_no") if find("section:" + n)]
        OUT["rows"] = {n: len(items("row:" + i)) for n, i in
                       (("a", "g-a1"), ("b", "g-b2"), ("c", "g-c3"), ("c1", "c-1"), ("f1", "f-1"), ("x", "g-x9"))}
        OUT["last"] = prop("row:g-a1", "implicitHeight")
        shot("lists")
    """)
    assert out["subtitle"] == "1 idea · 2 things it found"
    assert out["sections"] == ["asks", "changes", "found", "words", "said_no"]
    for want in ("What you ask most", "Changed itself", "Found", "Words", "You said no to",
                 "show me my passwords", "4 times on 3 days", "“open my passwords”", "Make the word",
                 "Other ways", "Made a word", "Made “my passwords” open Passwords.", "Undo", "Undone",
                 "Put it back", "The details drawer took no keyboard.", "3 times on 2 days", "Why?",
                 "Send to the project", "log a 5 km run", "Bring back", "“my passwords”", "Opens Passwords",
                 "Opens Disk usage", "Put away", "Forget what I ask", "Clear what it found", "Hide noticed"):
        assert want in out["texts"] or any(want in t for t in out["texts"]), want
    assert any("last asked yesterday" in t for t in out["texts"])
    assert any(t.startswith("it was going to be an app") or "it was going to be an app" in t
               for t in out["texts"])
    assert all(n == 1 for n in out["rows"].values()), out["rows"]


def test_every_list_says_so_when_there_is_nothing_and_hides_when_another_has_something(home):
    out = drive(home, """
        fake.full = {"asks": [], "changes": [], "found": [], "said_no": [], "words": []}
        fake.start()
        assert wait_until(lambda: backend.property("ready"))
        assert wait_until(lambda: root.property("subtitle") == "Nothing waiting")
        OUT["empty"] = [t for t in texts() if t.startswith(("Nothing you", "Bombadil has not", "You have not"))]
        OUT["sections"] = [n for n in ("asks", "changes", "found", "words", "said_no") if find("section:" + n)]
        shot("empty")
        # One list has something: the others go.
        fake.full = {"asks": [], "changes": [], "said_no": [], "words": [],
                     "found": [{"id": "f-9", "title": "It stopped.", "meta": "2 times on 1 day", "can_send": True}]}
        backend.refresh()
        assert wait_until(lambda: find("section:found") is not None and find("section:asks") is None)
        OUT["one"] = [n for n in ("asks", "changes", "found", "words", "said_no") if find("section:" + n)]
        OUT["subtitle"] = root.property("subtitle")
        shot("one_list")
    """)
    assert out["empty"] == ["Nothing you ask for more than once yet.", "Bombadil has not changed anything by itself.",
                            "Bombadil has not found anything wrong.", "You have not said no to anything."]
    assert out["sections"] == ["asks", "changes", "found", "said_no"]      # Words has no empty sentence
    assert out["one"] == ["found"] and out["subtitle"] == "1 thing it found"


def test_each_button_sends_its_message_with_the_rows_id(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: shows("Make the word"))
        click("btn:primary:g-a1")
        assert wait_until(lambda: fake.did("accept", "g-a1"))
        OUT["accept"] = fake.did("accept", "g-a1")
        assert wait_until(lambda: shows("Made a word") and not shows("Make the word"))
        click("btn:undo:c-1")
        assert wait_until(lambda: fake.did("undo", "c-1"))
        OUT["undo"] = fake.did("undo", "c-1")
        # The change is undone, and says so: Put it back, for both of them now.
        assert wait_until(lambda: len(items("btn:putback:c-1")) == 1 and find("btn:undo:c-1") is None)
        click("btn:putback:c-1")
        assert wait_until(lambda: fake.did("bring_back", "c-1"))
        OUT["putback"] = fake.did("bring_back", "c-1")
        click("btn:bringback:g-x9")
        assert wait_until(lambda: fake.did("bring_back", "g-x9"))
        OUT["said_no"] = fake.did("bring_back", "g-x9")
        assert wait_until(lambda: find("section:said_no") is None)
        click("btn:bringback:disk")
        assert wait_until(lambda: fake.did("bring_back", "disk"))
        OUT["word"] = fake.did("bring_back", "disk")
        OUT["all"] = [(m["op"], m.get("id")) for m in fake.did()]
    """)
    assert out["accept"] == [{"type": "noticed_do", "op": "accept", "id": "g-a1", "form": "word"}]
    assert out["undo"] == [{"type": "noticed_do", "op": "undo", "id": "c-1"}]
    assert out["putback"] == [{"type": "noticed_do", "op": "bring_back", "id": "c-1"}]
    assert out["said_no"] == [{"type": "noticed_do", "op": "bring_back", "id": "g-x9"}]
    assert out["word"] == [{"type": "noticed_do", "op": "bring_back", "id": "disk"}]
    assert len(out["all"]) == 5


def test_other_ways_opens_the_other_forms_and_the_quiet_answers(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: shows("Other ways"))
        OUT["closed"] = find("btn:way:g-a1:app") is None and find("btn:never:g-a1") is None
        click("btn:ways:g-a1")
        assert wait_until(lambda: find("btn:way:g-a1:app") is not None)
        shot("other_ways")
        click("btn:preview:g-a1")
        assert wait_until(lambda: fake.did("preview", "g-a1"))
        OUT["preview"] = fake.did("preview", "g-a1")
        assert wait_until(lambda: has("Example: "))
        OUT["shown"] = [t for t in texts() if "Example: " in t]
        shot("preview_answer")
        click("btn:way:g-a1:app")
        assert wait_until(lambda: fake.did("accept", "g-a1"))
        OUT["app"] = fake.did("accept", "g-a1")
        # Not now and Never go with the row's id, on an offer that waits (put the row back first).
        fake.full["asks"][0].update(state="offered", became="offered: a word")
        backend.refresh()
        assert wait_until(lambda: find("btn:ways:g-a1") is not None)
        click("btn:ways:g-a1")
        assert wait_until(lambda: find("btn:not_now:g-a1") is not None)
        click("btn:not_now:g-a1")
        assert wait_until(lambda: fake.did("not_now", "g-a1"))
        OUT["shut"] = find("btn:never:g-a1") is None       # choosing puts the choices away
        click("btn:ways:g-a1")
        assert wait_until(lambda: find("btn:never:g-a1") is not None)
        click("btn:never:g-a1")
        assert wait_until(lambda: fake.did("never", "g-a1"))
        OUT["quiet"] = [fake.did("not_now", "g-a1"), fake.did("never", "g-a1")]
    """)
    assert out["closed"] is True and out["shut"] is True
    assert out["preview"] == [{"type": "noticed_do", "op": "preview", "id": "g-a1", "form": "word"}]
    assert out["shown"] and "Passwords opens" in out["shown"][0]
    assert out["app"] == [{"type": "noticed_do", "op": "accept", "id": "g-a1", "form": "app"}]
    assert out["quiet"] == [[{"type": "noticed_do", "op": "not_now", "id": "g-a1"}],
                            [{"type": "noticed_do", "op": "never", "id": "g-a1"}]]


def test_why_shows_what_it_saw_and_a_row_without_reasons_has_no_why(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: shows("Why?"))
        OUT["buttons"] = [n for n in ("f-1", "f-2") if find("btn:why:" + n)]
        OUT["closed"] = not has("After you opened Details")
        click("btn:why:f-1")
        assert wait_until(lambda: has("After you opened Details"))
        OUT["open"] = [t for t in texts() if "After you opened Details" in t]
        shot("why")
        click("btn:why:f-1")
        assert wait_until(lambda: not has("After you opened Details"))
        OUT["sent"] = len(fake.did())
    """)
    assert out["buttons"] == ["f-1"] and out["closed"] is True
    assert out["open"] == ["After you opened Details, the bar still had the keyboard.\nIt was the same two seconds later."]
    assert out["sent"] == 0


def test_the_send_card_shows_what_goes_what_stays_and_the_exact_text(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: find("btn:send:f-1") is not None)
        click("btn:send:f-1")
        assert wait_until(lambda: fake.did("report", "f-1"))
        OUT["report"] = fake.did("report", "f-1")
        assert wait_until(lambda: find("sendBody") is not None and find("sendText") is not None)
        assert wait_until(lambda: has("What happened: the details"))
        OUT["goes"] = [prop("sendGoes", "title")] + [t for t in texts() if t in GOES]
        OUT["stays"] = [prop("sendStays", "title")] + [t for t in texts() if t in STAYS]
        OUT["text"] = prop("sendText", "text")
        OUT["buttons"] = [t for t in texts() if t in ("Open the issue page", "Not now", "Never for this")]
        OUT["lists_hidden"] = find("section:found") is None and not shows("Forget what I ask")
        OUT["heading"] = [t for t in texts() if t.startswith("Send this")]
        shot("send_card")
        click("btn:open")
        assert wait_until(lambda: fake.did("send", "f-1"))
        OUT["send"] = fake.did("send", "f-1")
        assert wait_until(lambda: shows("Sent"))
        OUT["after"] = [t for t in texts() if t in ("Sent", "Send to the project")]
        shot("after_send")
    """)
    assert out["report"] == [{"type": "noticed_do", "op": "report", "id": "f-1"}]
    assert out["goes"][0] == "What goes" and len(out["goes"]) == 7
    assert out["stays"][0] == "What stays here" and len(out["stays"]) == 6
    assert out["text"].startswith("Title: Details drawer takes no keyboard") and out["text"].endswith("yet")
    assert "Observed: the active window was bombadil-bar" in out["text"]
    assert out["buttons"] == ["Open the issue page", "Not now", "Never for this"]
    assert out["lists_hidden"] is True and out["heading"] == ["Send this to the project?"]
    assert out["send"] == [{"type": "noticed_do", "op": "send", "id": "f-1"}]
    assert out["after"] == ["Sent", "Send to the project"]        # f-1 is sent, f-2 still can be


def test_not_now_and_never_for_this_answer_and_close_the_card(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: find("btn:send:f-1") is not None)
        click("btn:send:f-1")
        assert wait_until(lambda: find("btn:notnow") is not None and find("sendText") is not None)
        click("btn:notnow")
        assert wait_until(lambda: fake.did("not_now", "f-1"))
        OUT["not_now"] = fake.did("not_now", "f-1")
        assert wait_until(lambda: find("section:found") is not None and find("row:f-1") is None)
        OUT["gone"] = find("btn:send:f-2") is not None
        click("btn:send:f-2")
        assert wait_until(lambda: fake.did("report", "f-2"))
        assert wait_until(lambda: find("btn:never") is not None and find("sendText") is not None)
        click("btn:never")
        assert wait_until(lambda: fake.did("never", "f-2"))
        OUT["never"] = fake.did("never", "f-2")
        assert wait_until(lambda: find("sendCard") is None or not find("sendCard").isVisible())
    """)
    assert out["not_now"] == [{"type": "noticed_do", "op": "not_now", "id": "f-1"}]
    assert out["never"] == [{"type": "noticed_do", "op": "never", "id": "f-2"}]
    assert out["gone"] is True


def test_a_report_held_before_opens_its_card_and_back_does_not_send_anything(home):
    out = drive(home, """
        fake.full["found"][0].update(state="reported", preview=PREVIEW)
        fake.start()
        assert wait_until(lambda: find("sendText") is not None), "the held report did not open by itself"
        OUT["text"] = prop("sendText", "text")[:10]
        OUT["sent"] = len(fake.did())
        click("btn:back")
        assert wait_until(lambda: find("section:found") is not None)
        OUT["badge"] = [t for t in texts() if t == "Ready to send"]
        # A list that comes again does not open it again; pressing Send to the project does, after a fresh report.
        backend.refresh()
        pump(800)
        OUT["stayed_closed"] = find("sendText") is None
        click("btn:send:f-1")
        assert wait_until(lambda: fake.did("report", "f-1"))
        assert wait_until(lambda: find("sendText") is not None)
        OUT["reopened"] = True
        OUT["sent_after"] = len(fake.did())
    """)
    assert out["text"] == "Title: Det" and out["sent"] == 0
    assert out["badge"] == ["Ready to send"] and out["stayed_closed"] is True and out["reopened"] is True
    assert out["sent_after"] == 1


def test_a_report_that_cannot_be_made_says_so_under_its_row(home):
    out = drive(home, """
        fake.fail = {"report": "There is nothing to write up yet."}
        fake.start()
        assert wait_until(lambda: find("btn:send:f-1") is not None)
        click("btn:send:f-1")
        assert wait_until(lambda: find("answer:f-1") is not None and prop("answer:f-1", "text") != "")
        OUT["text"] = prop("answer:f-1", "text")
        OUT["card"] = find("sendText") is None
        OUT["lists"] = find("section:found") is not None
        shot("report_failed")
    """)
    assert out["text"] == "There is nothing to write up yet."
    assert out["card"] is True and out["lists"] is True


def test_forgetting_and_clearing_ask_once_and_then_do_it(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: find("foot:forget") is not None)
        click("foot:forget")
        forget = obj("confirm:forget")
        assert wait_until(lambda: forget.property("opened"))
        OUT["asked"] = [forget.property("title"), forget.property("text"), forget.property("confirmText")]
        OUT["before"] = len(fake.did())
        shot("forget_dialog")
        forget.reject()
        pump(300)
        OUT["cancelled"] = len(fake.did())
        click("foot:forget")
        assert wait_until(lambda: forget.property("opened"))
        forget.accept()
        assert wait_until(lambda: fake.did("forget_asks"))
        OUT["forget"] = fake.did("forget_asks")
        assert wait_until(lambda: find("section:asks") is None)
        OUT["asks_gone"] = True
        click("foot:clear")
        clear = obj("confirm:clear")
        assert wait_until(lambda: clear.property("opened"))
        OUT["clear_asked"] = [clear.property("title"), clear.property("text"), clear.property("confirmText")]
        shot("clear_dialog")
        clear.accept()
        assert wait_until(lambda: fake.did("clear_found"))
        OUT["clear"] = fake.did("clear_found")
        assert wait_until(lambda: find("section:found") is None)
        OUT["words_left"] = find("section:words") is not None and find("section:said_no") is not None
        OUT["all"] = [m["op"] for m in fake.did()]
    """)
    assert out["asked"][0] == "Forget what you ask?" and out["asked"][2] == "Forget"
    assert "What you said no to stays said" in out["asked"][1]
    assert out["before"] == 0 and out["cancelled"] == 0          # asking sends nothing, and cancel neither
    assert out["forget"] == [{"type": "noticed_do", "op": "forget_asks"}]
    assert out["clear_asked"][0] == "Clear what it found?" and "Nothing was sent" in out["clear_asked"][1]
    assert out["clear"] == [{"type": "noticed_do", "op": "clear_found"}]
    assert out["words_left"] is True and out["all"] == ["forget_asks", "clear_found"]


def test_hide_and_show_is_one_line_that_turns_over(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: shows("Hide noticed"))
        click("foot:hide")
        assert wait_until(lambda: fake.did("hide"))
        OUT["hide"] = fake.did("hide")
        assert wait_until(lambda: shows("Show noticed"))
        OUT["note"] = [t for t in texts() if t.startswith("Noticed is hidden")]
        shot("hidden")
        click("foot:hide")
        assert wait_until(lambda: fake.did("show"))
        OUT["show"] = fake.did("show")
        assert wait_until(lambda: shows("Hide noticed"))
        OUT["note_after"] = [t for t in texts() if t.startswith("Noticed is hidden")]
        fake.full["resting"] = "3 Nov"
        backend.refresh()
        assert wait_until(lambda: has("Resting offers until 3 Nov."))
        OUT["resting"] = [t for t in texts() if t.startswith("Resting")]
    """)
    assert out["hide"] == [{"type": "noticed_do", "op": "hide"}]
    assert out["show"] == [{"type": "noticed_do", "op": "show"}]
    assert len(out["note"]) == 1 and out["note_after"] == []
    assert out["resting"] == ["Resting offers until 3 Nov."]


def test_a_failed_answer_shows_under_its_row_and_the_button_works_again(home):
    out = drive(home, """
        fake.fail = {"undo": "That change cannot be undone any more."}
        fake.start()
        assert wait_until(lambda: find("btn:undo:c-1") is not None)
        click("btn:undo:c-1")
        assert wait_until(lambda: fake.did("undo", "c-1"))
        assert wait_until(lambda: has("cannot be undone any more"))
        OUT["shown"] = [t for t in texts() if "cannot be undone" in t]
        assert wait_until(lambda: find("btn:undo:c-1").isEnabled())
        OUT["enabled"] = True
        shot("undo_failed")
        fake.fail = {}
        click("btn:undo:c-1")
        assert wait_until(lambda: len(fake.did("undo", "c-1")) == 2)
        assert wait_until(lambda: find("btn:undo:c-1") is None)        # it took this time
        OUT["cleared"] = not has("cannot be undone any more")
    """)
    assert out["shown"] == ["That change cannot be undone any more."] and out["enabled"] is True
    assert out["cleared"] is True


def test_a_tap_that_waits_for_its_answer_cannot_be_sent_twice(home):
    out = drive(home, """
        fake.answer_dos = False
        fake.start()
        assert wait_until(lambda: find("btn:undo:c-1") is not None)
        click("btn:undo:c-1")
        assert wait_until(lambda: fake.did("undo", "c-1"))
        assert wait_until(lambda: not find("btn:undo:c-1").isEnabled())
        OUT["disabled"] = True
        backend.actForm("undo", "c-1", "")       # a second tap by any other road
        pump(300)
        OUT["heard"] = len(fake.did("undo", "c-1"))
        # No answer ever comes: it stops waiting, says so, and asks again.
        backend.answer_s = 0.4
        assert wait_until(lambda: find("btn:undo:c-1").isEnabled(), 3000)
        assert wait_until(lambda: has("Bombadil did not answer."))
        OUT["said"] = [t for t in texts() if t == "Bombadil did not answer."]
    """)
    assert out["disabled"] is True and out["heard"] == 1
    assert out["said"] == ["Bombadil did not answer."]


def test_agentd_absent_then_there_then_gone_and_back(home):
    out = drive(home, """
        # Nothing listens yet: the window says so.
        assert wait_until(lambda: shows("Bombadil is not listening right now."), 3000)
        OUT["away"] = [t for t in texts() if t.startswith(("Bombadil is not", "Noticed keeps"))]
        OUT["lists_while_away"] = find("section:asks") is None and find("foot:forget") is None
        OUT["subtitle_away"] = root.property("subtitle")
        shot("away")
        # It starts: the window finds it by itself.
        fake.start()
        assert wait_until(lambda: shows("What you ask most") and not shows("Bombadil is not listening right now."), 5000)
        OUT["filled"] = root.property("subtitle")
        # It goes again (every client dropped, the socket gone), and comes back with something else.
        fake.stop()
        assert wait_until(lambda: shows("Bombadil is not listening right now."), 3000)
        OUT["gone"] = find("section:asks") is None
        fake.full = {"asks": [], "changes": [], "found": [], "said_no": [], "words": []}
        fake.start()
        assert wait_until(lambda: root.property("subtitle") == "Nothing waiting", 5000)
        OUT["back"] = shows("Nothing you ask for more than once yet.")
        OUT["connections"] = backend.property("connected")
    """)
    assert out["away"] == ["Bombadil is not listening right now.", "Noticed keeps trying and fills in when Bombadil is back."]
    assert out["lists_while_away"] is True and out["subtitle_away"] == ""
    assert out["filled"] == "1 idea · 2 things it found"
    assert out["gone"] is True and out["back"] is True and out["connections"] is True


def test_a_tap_with_no_agentd_says_so_instead_of_waiting(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: find("btn:undo:c-1") is not None)
        got = []
        backend.answered.connect(lambda *a: got.append(a))
        fake.stop()
        pump(300)
        backend.actForm("undo", "c-1", "")
        OUT["got"] = [list(a[:4]) for a in got]
        OUT["pending"] = list(backend.property("pending"))
    """)
    assert out["got"] == [["undo", "c-1", False, "Bombadil is not listening right now."]]
    assert out["pending"] == []


def test_garbage_and_missing_fields_cost_a_row_not_the_window(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: shows("What you ask most"))
        c = fake.conns[0]
        fake.answer_lists = False       # a result makes the window ask again: hear only the garbage first
        for raw in (b"this is not json\\n", b"[1, 2, 3]\\n", b"\\"a string\\"\\n", b"null\\n", b"{}\\n",
                    b'{"type": 7}\\n', b'{"type": "noticed_result"}\\n', b'{"type": "noticed_result", "id": {"x": 1}}\\n',
                    b'{"type": "noticed_full"}\\n',
                    b'{"type": "noticed_full", "asks": 5, "changes": [null, 4, {"id": "c-1"}], "found": {"a": 1}}\\n',
                    b'{"type": "event", "kind": "text", "turn": 1, "text": "not for this window"}\\n',
                    b'\\xff\\xfe not utf-8 either\\n'):
            fake.send(c, raw)
        pump(500)
        OUT["alive"] = backend.property("connected") and backend.property("ready")
        OUT["subtitle"] = root.property("subtitle")
        OUT["sections"] = [n for n in ("asks", "changes", "found", "words", "said_no") if find("section:" + n)]
        # Then a good one, and it draws again.
        fake.answer_lists = True
        fake.send(c, {"type": "noticed_full", **sample()})
        assert wait_until(lambda: root.property("subtitle") == "1 idea · 2 things it found")
        OUT["recovered"] = root.property("subtitle")
        # A full whose rows lack things: what can be drawn is drawn.
        fake.send(c, {"type": "noticed_full", "asks": [{"id": "g-1", "title": "only a title"}],
                      "changes": [{"id": "c-9", "title": "no time, no flags"}],
                      "found": [{"id": "f-9", "title": "no meta, no state"}],
                      "said_no": [{"id": "g-8", "title": "no date"}], "words": [{"phrase": "lonely"}]})
        assert wait_until(lambda: shows("only a title"))
        OUT["thin"] = [t for t in texts() if t in ("only a title", "no time, no flags", "no meta, no state",
                                                   "no date", "\\u201clonely\\u201d")]
        OUT["buttons"] = [n for n in ("btn:undo:c-9", "btn:send:f-9", "btn:bringback:g-8") if find(n)]
        shot("thin_rows")
    """)
    assert out["alive"] is True
    assert out["subtitle"] == "Nothing waiting" and out["sections"] == ["asks", "changes", "found", "said_no"]
    assert out["recovered"] == "1 idea · 2 things it found"
    assert out["thin"] == ["only a title", "no time, no flags", "no meta, no state", "“lonely”", "no date"]
    assert out["buttons"] == ["btn:undo:c-9", "btn:bringback:g-8"]      # nothing said it could be sent


def test_his_words_are_shown_as_text_never_as_markup(home):
    out = drive(home, """
        fake.full["asks"][0]["title"] = "<b>bold</b> & <img src=x> show me"
        fake.full["asks"][0]["sentences"] = ["<i>tilted</i>", "a &amp; b"]
        fake.start()
        assert wait_until(lambda: has("<b>bold</b>"))
        OUT["title"] = [t for t in texts() if "bold" in t]
        OUT["note"] = prop("row:g-a1", "implicitHeight") > 0
        OUT["format"] = [enum(i, "textFormat") for i in items("title")]
        OUT["sent"] = [enum(i, "textFormat") for i in items("note") if i.isVisible()]
    """)
    assert out["title"] == ["<b>bold</b> & <img src=x> show me"]
    assert set(out["format"]) == {0}          # Text.PlainText
    assert set(out["sent"]) <= {0}


def test_a_burst_of_noticed_messages_asks_once_and_an_echo_asks_never(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: backend.property("ready"))
        pump(900)
        base = fake.lists
        c = fake.conns[0]
        for n in range(60):
            fake.send(c, {"type": "noticed", "count": n, "hidden": False, "resting": "", "lately": "", "rows": []})
        pump(1500)
        OUT["burst"] = fake.lists - base
        # The service answers a list with the same `noticed` every time: that echo must not start a loop.
        fake.broadcast = True
        def answer(c2, msg, real=fake.handle):
            real(c2, msg)
            if msg.get("type") == "noticed_list":
                fake.send(c2, fake.noticed())
        fake.handle = answer
        base = fake.lists
        backend.refresh()
        pump(3000)
        OUT["echo"] = fake.lists - base
        # An old agentd that never answers a list is asked again every few seconds, not in a storm.
        fake.answer_lists = False
        backend._lost()   # no-op while connected
        OUT["still"] = backend.property("ready")
    """)
    assert out["burst"] <= 2, out["burst"]
    assert out["echo"] <= 3, out["echo"]
    assert out["still"] is True


def test_noticed_open_asks_for_the_lists_and_a_line_that_never_ends_is_dropped(home):
    out = drive(home, """
        fake.start()
        assert wait_until(lambda: backend.property("ready"))
        pump(900)
        base = fake.lists
        fake.send(fake.conns[0], {"type": "noticed_open"})
        OUT["open_asked"] = wait_until(lambda: fake.lists > base)
        # A line with no end, bigger than anything agentd would say: dropped, and the window carries on.
        c = fake.conns[0]
        junk = threading.Thread(target=fake.send, args=(c, b"x" * (18 << 20)), daemon=True)
        junk.start()
        wait_until(lambda: not junk.is_alive(), 20000)
        pump(500)
        OUT["buffer"] = len(backend._buffer)
        OUT["connected"] = backend.property("connected")
        fake.send(c, b"\\n")
        fake.full["asks"][0]["title"] = "still listening"
        fake.send(c, {"type": "noticed_full", **fake.full})
        OUT["heard"] = wait_until(lambda: shows("still listening"))
    """)
    assert out["open_asked"] is True
    assert out["buffer"] < (16 << 20) and out["connected"] is True
    assert out["heard"] is True


def test_a_window_that_hears_nothing_about_noticed_keeps_asking_and_says_so(home):
    out = drive(home, """
        fake.answer_lists = False
        fake.start()
        assert wait_until(lambda: backend.property("connected"))
        OUT["not_ready"] = not backend.property("ready")
        assert wait_until(lambda: shows("Bombadil has not answered yet."), 5000)
        OUT["said"] = [t for t in texts() if t in ("Bombadil has not answered yet.", "Noticed asks again every few seconds.")]
        shot("no_answer")
        assert wait_until(lambda: fake.lists >= 3, 4000)
        OUT["asked"] = fake.lists >= 3
        fake.answer_lists = True
        assert wait_until(lambda: shows("What you ask most"), 4000)
        OUT["filled"] = not shows("Bombadil has not answered yet.")
    """)
    assert out["not_ready"] is True and out["asked"] is True and out["filled"] is True
    assert out["said"] == ["Bombadil has not answered yet.", "Noticed asks again every few seconds."]


def test_a_long_list_scrolls_and_the_foot_stays(home):
    out = drive(home, """
        full = sample()
        full["asks"] = [{"id": f"g-{n}", "title": f"ask number {n}", "n": 3, "days": 2, "last": NOW - n * 3600,
                         "state": "counting", "sentences": [f"ask number {n}"]} for n in range(40)]
        full["changes"] = [{"id": f"c-{n}", "title": f"Made word {n}.", "t": NOW - n * 86400, "undone": False,
                            "can_undo": True} for n in range(60)]
        fake.full = full
        fake.start()
        assert wait_until(lambda: shows("ask number 0"))
        lists = find("lists")
        OUT["scrolls"] = lists.property("contentHeight") > lists.height() * 3
        OUT["rows"] = [len(items("row:g-" + str(n))) for n in (0, 39)]
        OUT["caps"] = len([i for i in items() if i.objectName().startswith("row:c-")])
        foot = find("foot:forget")
        y0 = foot.mapToScene(QPointF(0, 0)).y()
        lists.setProperty("contentY", lists.property("contentHeight"))
        pump(100)
        OUT["foot_still"] = abs(foot.mapToScene(QPointF(0, 0)).y() - y0) < 1
        OUT["foot_inside"] = y0 + foot.height() <= win.height()
        shot("long")
    """)
    assert out["scrolls"] is True and out["rows"] == [1, 1]
    assert out["caps"] == 50          # a list draws at most 50 rows
    assert out["foot_still"] is True and out["foot_inside"] is True


def test_the_window_loads_clean_in_a_check_and_does_not_connect(home):
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), "check", "noticed",
                        "--screenshot", str(home / "check.png")],
                       capture_output=True, text=True, timeout=90, check=False,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
                            "BOMBADIL_SOCKET": str(home / "nothing-listens-here")})
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    result = json.loads(r.stdout)
    assert result["ok"] is True and result["errors"] == [] and result["warnings"] == [], result
    assert (home / "check.png").exists() and result["size"] == [620, 700]
    # Hot reload keeps nothing in the window that matters, so the folder holds only what the app needs.
    assert sorted(p.name for p in APP.iterdir() if p.name != "__pycache__") == sorted([
        "AsksSection.qml", "ChangesSection.qml", "FootLink.qml", "FoundSection.qml", "Line.qml",
        "SaidNoSection.qml", "Section.qml", "SendCard.qml", "Well.qml", "WordsSection.qml",
        "app.py", "app.toml", "main.qml", "text.js"])

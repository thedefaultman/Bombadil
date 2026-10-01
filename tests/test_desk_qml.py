"""The desk's state, rails and strips, driven by agentd's messages in an offscreen window.

DeskState, DeskRail and DeskStrips are plain Qt Quick (Quickshell only wraps them in shell.qml),
so they load here without a compositor, with the real PillState beside them as in the shell. The
card faces are the real ones from shell/. Set BOMBADIL_SCREENS=<dir> to save a picture of each state.
"""

import itertools
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from qml_theme import THEME

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
CARDS = SHELL
CARD_FILES = ["DeskCard.qml", "NowCard.qml", "RowsCard.qml", "DeskStrip.qml"]
DESK_FILES = ["DeskState.qml", "DeskRail.qml", "DeskStrips.qml", "PillState.qml", "DeskTheme.js"]

HARNESS = """
import QtQuick
import "%(dir)s"

Window {
    id: w
    width: %(width)d; height: %(height)d; visible: true
    color: "#101214"
    property var sent: []          // what the desk sends agentd
    property var pillSent: []
    property var actions: []
    property var opened: []
    property int machineChanges: 0
    // What shell.qml does with a line from agentd: parse it, and hand it to the pill and the desk.
    function feed(line) {
        const ev = JSON.parse(line)
        pillState.handle(ev)
        deskState.handle(ev)
    }
    // JSON has no NaN or infinity, and a message that reached the shell cannot have them. A test that
    // wants one (a number a handler built, not one that was parsed) spells it "NaN!" or "Infinity!".
    function feedNumbers(line) {
        const ev = JSON.parse(line, (k, v) => v === "NaN!" ? NaN : v === "Infinity!" ? Infinity : v)
        pillState.handle(ev)
        deskState.handle(ev)
    }
    // Lists and maps the shell builds in JS arrive as real arrays; a Python dict handed to a method
    // does not, so what a test sets goes through JSON the same way.
    function setWindowsJson(json) { deskState.setWindows(JSON.parse(json)) }
    function setDeskJson(name, json) { deskState[name] = JSON.parse(json) }
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => w.pillSent = w.pillSent.concat([msg])
    }
    DeskState {
        id: deskState
        objectName: "desk"
        pill: pillState
        pillAnimMs: 0
        tickMs: %(tick)d
        screenWidth: w.width; screenHeight: w.height
        onOutgoing: msg => w.sent = w.sent.concat([msg])
        onRowAction: (widget, key, action) => w.actions = w.actions.concat([[widget, key, action]])
        onRowOpen: (widget, key, opens) => w.opened = w.opened.concat([[widget, key, opens]])
        onMachineModelChanged: w.machineChanges += 1
    }
    // What shell.qml puts in its windows: a rail each side, the pill, and the strips beside it.
    DeskRail { objectName: "railLeft"; parent: w.contentItem; desk: deskState; side: "left"
               x: 16; y: 40; width: 300; height: w.height - 104 }
    DeskRail { objectName: "railRight"; parent: w.contentItem; desk: deskState; side: "right"
               x: w.width - 316; y: 40; width: 300; height: w.height - 104 }
    Rectangle {
        id: pillBox
        objectName: "pillBox"
        width: deskState.pillWidth; height: 52; radius: 26
        x: (w.width - width) / 2; y: w.height - 64
        color: "#f01a1d21"
    }
    DeskStrips { objectName: "stripsLeft"; parent: w.contentItem; desk: deskState; side: "left"
                 pillEdge: pillBox.x; pillCentreY: pillBox.y + pillBox.height / 2 }
    DeskStrips { objectName: "stripsRight"; parent: w.contentItem; desk: deskState; side: "right"
                 pillEdge: pillBox.x + pillBox.width; pillCentreY: pillBox.y + pillBox.height / 2 }
    // The same on a screen the desk does not live on: it must draw nothing.
    DeskStrips { objectName: "stripsOther"; parent: w.contentItem; desk: deskState; side: "left"; active: false
                 pillEdge: pillBox.x; pillCentreY: pillBox.y + pillBox.height / 2 }
}
"""


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


def plain(v):
    """A QML value as Python: var properties and returns arrive as QJSValue."""
    return v.toVariant() if hasattr(v, "toVariant") else v


class Desk:
    def __init__(self, app, tmp_path, width=1920, height=1080, calm=True):
        self.app = app
        home = tmp_path / "shell"
        home.mkdir()
        for name in DESK_FILES:
            shutil.copy(SHELL / name, home / name)
        for name in CARD_FILES:
            shutil.copy(CARDS / name, home / name)
        qml = tmp_path / "harness.qml"
        # A calm desk's clock ticks once an hour, so a test sets `now` and nothing moves it under the test.
        qml.write_text(HARNESS % {"dir": home.as_uri(), "width": width, "height": height,
                                  "tick": 3600000 if calm else 1000})
        self.engine = QtQml.QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        QtCore.qInstallMessageHandler(lambda mode, ctx, msg: self.warnings.append(msg)
                                      if ".qml" in (ctx.file or "") or "TypeError" in msg else None)
        self.desk = self.win.findChild(QtCore.QObject, "desk")
        self.pill = self.win.findChild(QtCore.QObject, "pill")
        self.pump()

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    # -- driving --

    def send(self, **ev):
        """A message from agentd, delivered as shell.qml does: the pill first, then the desk."""
        ev.setdefault("type", "event")
        QtCore.QMetaObject.invokeMethod(self.win, "feed", QtCore.Q_ARG("QVariant", json.dumps(ev)))
        self.pump()

    def send_numbers(self, **ev):
        """A message that carries numbers JSON cannot (see feedNumbers in the harness)."""
        QtCore.QMetaObject.invokeMethod(self.win, "feedNumbers", QtCore.Q_ARG("QVariant", json.dumps(ev)))
        self.pump()

    def call(self, name, *args, target=None):
        ret = QtCore.QMetaObject.invokeMethod(
            target or self.desk, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return plain(ret)

    def pill_call(self, name, *args):
        return self.call(name, *args, target=self.pill)

    def set(self, name, value):
        if isinstance(value, (list, dict)):
            QtCore.QMetaObject.invokeMethod(self.win, "setDeskJson", QtCore.Q_ARG("QVariant", name),
                                            QtCore.Q_ARG("QVariant", json.dumps(value)))
        else:
            self.desk.setProperty(name, value)
        self.pump()

    def prop(self, name):
        return plain(self.desk.property(name))

    def cover(self, *rects):
        """Windows on the stage, as (x, y, w, h) or (x, y, w, h, fullscreen)."""
        wins = [{"x": r[0], "y": r[1], "w": r[2], "h": r[3], "kind": "window",
                 "fullscreen": len(r) > 4 and r[4]} for r in rects]
        QtCore.QMetaObject.invokeMethod(self.win, "setWindowsJson", QtCore.Q_ARG("QVariant", json.dumps(wins)))
        self.pump()

    def jobs(self, *table):
        """agentd's jobs table, as the shell receives it."""
        self.send(type="jobs", jobs=list(table))

    def clock(self, seconds):
        """The desk's clock at `seconds` after T0. Set after the table that starts it, which reads the real one."""
        self.set("now", (T0 + seconds) * 1000)

    def vitals(self, *table, **kw):
        """agentd's machine message, as the shell receives it: the memory line crossed, unless told otherwise."""
        self.send(**machine_msg(*table, **kw))

    def dev(self, sessions, attention, **kw):
        """The dev message PR #4 defines: the sessions and the keys that want you, in Tab's order."""
        self.send(type="dev", sessions=list(sessions), attention=list(attention), front=kw.get("front", ""),
                  line=kw.get("line", ""))

    def resize(self, width, height):
        self.win.setProperty("width", width)
        self.win.setProperty("height", height)
        self.pump()

    def turn(self, n=1, prompt="install ffmpeg", steps=None, **start):
        self.send(kind="turn_start", turn=n, prompt=prompt, **start)
        if steps is not None:
            self.plan(n, steps)

    def plan(self, n, steps):
        self.send(kind="plan", turn=n, steps=[
            {"id": str(i + 1), "subject": s[0], "active": s[1], "status": s[2]} for i, s in enumerate(steps)])

    def end(self, n=1, **kw):
        kw.setdefault("seconds", 58)
        kw.setdefault("changed", True)
        kw.setdefault("stopped", False)
        self.send(kind="turn_end", turn=n, **kw)

    # -- looking --

    @property
    def sent(self):
        return [dict(m) for m in plain(self.win.property("sent"))]

    @property
    def faces(self):
        return self.prop("faces")

    @property
    def slots(self):
        return self.prop("slots")

    @property
    def now(self):
        return self.prop("nowModel")

    @property
    def watch(self):
        return self.prop("watchModel")

    @property
    def needs(self):
        return self.prop("needsModel")

    @property
    def machine(self):
        return self.prop("machineModel")

    def parts(self, row):
        """The pieces of a stack row as drawn, left to right, as (x, width, colour) on its track."""
        track = self.inside(row, "rowsMeter")
        found = [c for c in track.childItems() if c.objectName() == "rowsPart"]
        return [(p.x(), p.width(), p.property("color").name()) for p in sorted(found, key=lambda p: p.x())]

    def card_row(self, key):
        """The rows card's row for a key, wherever the desk drew it."""
        found = [r for r in self.items("rowsRow") if r.property("key") == key]
        return found[0] if found else None

    def inside(self, item, name):
        """The first visible item called `name` under `item`."""
        todo = list(item.childItems())
        while todo:
            it = todo.pop(0)
            if it.objectName() == name and it.isVisible():
                return it
            todo.extend(it.childItems())
        return None

    def items(self, name, visible_only=True, other=False):
        """Items called `name` in the desk's own windows; `other` looks only under the strips of a
        screen the desk is not on."""
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == "stripsOther" and not other:
                continue
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        if other:
            found = [it for it in found if self._under(it, "stripsOther")]
        return found

    @staticmethod
    def _under(item, name):
        while item is not None:
            if item.objectName() == name:
                return True
            item = item.parentItem()
        return False

    def item(self, name, visible_only=False):
        found = self.items(name, visible_only)
        return found[0] if found else None

    def shown(self, name):
        it = self.item(name)
        return it is not None and it.isVisible()

    def at(self, item):
        p = item.mapToScene(QtCore.QPointF(0, 0))
        return (round(p.x()), round(p.y()))

    def click(self, item, x=None, y=None):
        p = item.mapToScene(QtCore.QPointF(item.width() / 2 if x is None else x,
                                           item.height() / 2 if y is None else y)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p)
        self.pump()

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.3)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"desk-{name}.png"))


@pytest.fixture
def desk(app, tmp_path):
    d = Desk(app, tmp_path)
    d.send(type="status", busy=False, provider="claude", queue=[])
    yield d
    d.win.close()
    d.engine.deleteLater()
    d.pump()


@pytest.fixture
def live(app, tmp_path):
    """A desk whose clock ticks once a second, as in the shell."""
    d = Desk(app, tmp_path, calm=False)
    d.send(type="status", busy=False, provider="claude", queue=[])
    yield d
    d.win.close()
    d.engine.deleteLater()
    d.pump()


@pytest.fixture
def laptop(app, tmp_path):
    d = Desk(app, tmp_path, 1280, 720)
    d.send(type="status", busy=False, provider="claude", queue=[])
    yield d
    d.win.close()
    d.engine.deleteLater()
    d.pump()


T0 = 1_790_000_000.0        # an epoch second: agentd stamps jobs with the same machine's clock as the shell


def job(id="a1b2", title="Ubuntu 26.04 ISO", kind="job", state="running", **kw):
    """One row of agentd's jobs table."""
    j = {"id": id, "title": title, "kind": kind, "state": state, "started": T0, "deadline": None, "ended": None,
         "pct": None, "last": "", "unit": f"bombadil-job-{id}"}
    j.update(kw)
    return j


ISO = job("a1b2", "Ubuntu 26.04 ISO", pct=43.0, last="12 MB/s")
BUILD = job("c3d4", "Tell me when the build finishes", kind="watch", started=T0 - 120)
TIMER = job("e5f6", "Timer, 10 min", kind="timer", deadline=T0 + 600)
UPDATE = job("f7a8", "pacman -Syu", state="failed", ended=T0 + 30, last="pacman: could not resolve host")


def session(key, role="reviewer", project="bombadil", title="Bombadil", state="asked",
            last="apply the migration to the local database?", **kw):
    """One session of PR #4's dev message."""
    s = {"key": key, "project": project, "projectTitle": title, "role": role, "tool": "claude",
         "toolTitle": "Claude Code", "title": "", "state": state, "alive": True, "unseen": True, "yours": True,
         "copy": "", "since": T0, "last": last, "lines": []}
    s.update(kw)
    return s


def part(tone, fraction):
    return {"tone": tone, "fraction": fraction}


# The rows of agentd's machine message when memory has crossed its line (the contract in the Machine
# widget's design): who uses the memory, the disk, the processor, the network.
MEMORY = {"key": "memory", "kind": "stack", "title": "Memory", "meterText": "14.5 of 16 GB", "meter": 0.91,
          "tone": "amber", "parts": [part("machine", 0.12), part("sessions", 0.40), part("you", 0.39)], "opens": ""}
DISK = {"key": "disk", "kind": "meter", "title": "Disk", "meterText": "214 of 230 GB", "meter": 0.93,
        "tone": "amber", "opens": "disk"}
CPU = {"key": "cpu", "kind": "meter", "title": "Processor", "meterText": "37% busy · 62°", "meter": 0.37,
       "tone": "you", "opens": ""}
NET = {"key": "net", "kind": "plain", "title": "Network", "sub": "↓ 1.2 MB/s   ↑ 40 kB/s", "tone": "you", "opens": ""}
WHY = "Memory is nearly full · coding sessions use most of it"


def machine_msg(*table, **kw):
    msg = {"type": "machine", "present": True, "asked": False, "why": WHY,
           "strip": {"text": "memory 91%", "dot": "amber"}, "rows": list(table) or [MEMORY, DISK, CPU, NET]}
    msg.update(kw)
    return msg


def shown(row):
    """A machine row as the desk keeps it: every field the rows card reads, the ones it was not sent as nothing."""
    out = {"key": "", "kind": "plain", "title": "", "sub": "", "meter": None, "meterText": "", "tone": "you",
           "pulse": False, "button": "", "remove": False, "opens": ""}
    out.update(row)
    return out


TWO =[("Install ffmpeg", "Installing ffmpeg", "in_progress"), ("Open the browser", None, "pending")]
FOUR = [("Save a restore point", None, "completed"), ("Install docker", "Installing docker", "in_progress"),
        ("Add you to the docker group", None, "pending"), ("Start the service", None, "pending")]


def rows(n, kind="dot", first=None):
    """A rows model with n rows, for the cards that later pieces fill."""
    out = [{"key": f"r{i}", "kind": kind, "title": f"row {i}", "sub": "", "meter": None, "meterText": "",
            "tone": "machine", "button": "Open", "remove": False} for i in range(n)]
    if first:
        out[0].update(first)
    return {"title": "Card", "why": "", "rows": out}


def statuses(model):
    return [s["status"] for s in model["steps"]]


# -- Now: the model through a turn --

def test_now_follows_a_whole_turn(desk):
    desk.turn(1, "install ffmpeg")
    assert not desk.prop("nowVisible")                     # a turn with no plan and no system step is the line's
    desk.plan(1, TWO)
    assert desk.prop("nowVisible") and desk.prop("present")["now"]
    m = desk.now
    assert m["title"] == "install ffmpeg" and m["edge"] == "machine" and m["running"] and not m["done"]
    assert m["steps"] == [{"id": "1", "label": "Installing ffmpeg", "status": "in_progress"},
                          {"id": "2", "label": "Open the browser", "status": "pending"}]
    assert m["why"] == "Step 1 of 2 · Esc stops" and m["command"] == "" and m["caption"] == ""

    desk.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system",
              command="sudo pacman -S --noconfirm ffmpeg", touched={"package": 1}, touched_text="1 package so far")
    m = desk.now
    assert m["edge"] == "amber" and m["command"] == "sudo pacman -S --noconfirm ffmpeg"
    assert m["why"] == "Step 1 of 2 · 1 package so far · Esc stops"
    desk.snap("now-amber")

    # What the agent says itself always has risk null; it must not put the amber out.
    desk.send(kind="status", turn=1, text="and it is ready to use.", source="agent", risk=None, command=None)
    assert desk.now["edge"] == "amber" and desk.now["command"] != ""

    desk.send(kind="tool_result", turn=1, text="ok")
    m = desk.now
    assert m["edge"] == "machine" and m["command"] == "" and m["why"] == "Step 1 of 2 · 1 package so far · Esc stops"

    desk.plan(1, [("Install ffmpeg", "Installing ffmpeg", "completed"), ("Open the browser", "Opening the browser", "in_progress")])
    assert desk.now["why"].startswith("Step 2 of 2") and statuses(desk.now) == ["completed", "in_progress"]
    assert desk.now["steps"][1]["label"] == "Opening the browser"      # the current step speaks in its own words
    assert desk.now["steps"][0]["label"] == "Install ffmpeg"           # the others by their subject

    desk.send(kind="status", turn=1, text="Formatting /dev/sdb1", source="step", risk="irreversible",
              command="sudo mkfs.ext4 /dev/sdb1", touched_text="1 package and 1 file so far")
    m = desk.now
    assert m["edge"] == "red" and m["command"] == "sudo mkfs.ext4 /dev/sdb1"
    assert m["caption"] == "can't be undone · Esc stops it"
    desk.snap("now-red")

    # A plan from another turn is stale.
    desk.send(kind="plan", turn=0, steps=[{"id": "9", "subject": "Old", "active": None, "status": "pending"}])
    assert [s["id"] for s in desk.now["steps"]] == ["1", "2"]

    desk.send(kind="result", turn=1, ok=True, text="Installed ffmpeg.")
    desk.end(1, seconds=58, changed=True, summary="Installed ffmpeg.")
    m = desk.now
    assert statuses(m) == ["completed", "completed"] and m["edge"] == "ok" and m["done"] and not m["running"]
    assert m["why"] == "done in 58 s · Undo is above the pill"
    assert m["command"] == "" and m["caption"] == ""
    assert desk.pill.property("mode") == "closing" and desk.prop("nowVisible")     # it stays while the line does
    desk.snap("now-done")

    desk.pill_call("dismiss")                                                      # the closing line went
    assert desk.pill.property("mode") == "idle" and not desk.prop("nowVisible")
    assert desk.faces["now"] == "hidden" and "now" not in desk.slots


def test_a_done_card_leaves_when_another_line_takes_the_closings_place(desk):
    desk.turn(1, steps=TWO)
    desk.end(1)
    assert desk.prop("nowVisible")
    desk.send(type="local", action="panel")                 # nothing to do: the pill was not waiting for one
    desk.send(kind="local", turn=None, action="panel", phase="done", ok=True, text="Opened the browser.")
    assert desk.pill.property("mode") == "local" and not desk.prop("nowVisible")


def test_the_done_line_only_promises_undo_when_there_is_one(desk):
    desk.turn(1, "what is on my disk", steps=TWO)
    desk.end(1, seconds=3.4, changed=False)
    assert desk.now["why"] == "done in 3 s"


def test_presence_follows_the_design(desk):
    desk.turn(1, steps=[("Only step", None, "in_progress")])
    assert not desk.prop("nowVisible")                       # one step and nothing touched: the line says it
    desk.send(kind="status", turn=1, text="Reading the docs", source="step", risk=None, command=None)
    assert not desk.prop("nowVisible")
    desk.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system", command="sudo pacman -S ffmpeg")
    assert desk.prop("nowVisible")                           # touching the system is enough
    desk.end(1)
    desk.pill_call("dismiss")
    desk.turn(2, steps=TWO)
    assert desk.prop("nowVisible")                           # two steps are a route
    desk.end(2, stopped=True)
    assert not desk.prop("nowVisible")


def test_a_turn_with_no_plan_that_touches_the_system_is_one_step(desk):
    desk.turn(1, "install ffmpeg")
    desk.send(kind="status", turn=1, text="Saving a restore point", source="step", risk=None, command=None)
    assert not desk.prop("nowVisible")
    desk.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system", command="sudo pacman -S ffmpeg")
    m = desk.now
    assert desk.prop("nowVisible") and m["steps"] == [{"id": "step", "label": "Installing ffmpeg", "status": "in_progress"}]
    assert m["edge"] == "amber" and m["command"] == "sudo pacman -S ffmpeg"
    assert m["why"] == "Esc stops"                           # no plan, no step counter
    assert desk.prop("nowStripText") == "working"
    desk.end(1)
    assert statuses(desk.now) == ["completed"] and desk.now["edge"] == "ok"


def test_the_title_is_the_ask_on_one_line(desk):
    desk.turn(1, "install qemu-full", steps=TWO, asked_by="builder")
    assert desk.now["title"] == "install qemu-full · asked by builder"
    desk.end(1, stopped=True)
    long = "install docker and set it up for my projects,\n  then   open   the browser " + "and wait " * 12
    desk.turn(2, long, steps=TWO)
    title = desk.now["title"]
    assert "\n" not in title and "  " not in title and title.endswith("…") and len(title) <= 60
    assert title.startswith("install docker and set it up for my projects, then open")
    desk.end(2, stopped=True)
    desk.turn(3, "make me a timer app", steps=TWO, asked_by=None)      # the key is null for turns you typed
    assert desk.now["title"] == "make me a timer app"


def test_plans_and_statuses_of_other_turns_change_nothing(desk):
    desk.send(kind="plan", turn=1, steps=[{"id": "1", "subject": "a", "active": None, "status": "pending"},
                                         {"id": "2", "subject": "b", "active": None, "status": "pending"}])
    assert not desk.prop("nowVisible")                       # no turn yet: a plan has nothing to belong to
    desk.turn(2, steps=TWO)
    desk.send(kind="status", turn=1, text="Installing", source="step", risk="system", command="sudo x")
    assert desk.now["edge"] == "machine"
    desk.send(kind="turn_end", turn=1, seconds=1, changed=False, stopped=False)
    assert desk.prop("nowVisible")                           # the wrong turn's end does not end this one


def test_the_step_counter_and_the_strip_follow_the_plan(desk):
    desk.turn(1, steps=FOUR)
    assert desk.now["why"] == "Step 2 of 4 · Esc stops" and desk.prop("nowStripText") == "step 2 of 4"
    # Several rows in progress: one is current, the others wait.
    desk.plan(1, [("a", None, "in_progress"), ("b", None, "in_progress"), ("c", None, "pending")])
    assert statuses(desk.now) == ["in_progress", "pending", "pending"]
    # Nothing in progress yet: the count is the next one to do.
    desk.plan(1, [("a", None, "completed"), ("b", None, "pending"), ("c", None, "pending")])
    assert desk.now["why"] == "Step 2 of 3 · Esc stops"
    desk.plan(1, [("a", None, "completed"), ("b", None, "completed"), ("c", None, "completed")])
    assert desk.now["why"] == "Step 3 of 3 · Esc stops"


def test_the_latch_clears_when_the_current_step_changes(desk):
    desk.turn(1, steps=FOUR)
    desk.send(kind="status", turn=1, text="Installing docker", source="step", risk="system", command="sudo pacman -S docker")
    assert desk.now["edge"] == "amber"
    desk.plan(1, FOUR)                                       # the same step: still running its command
    assert desk.now["edge"] == "amber"
    desk.plan(1, [FOUR[0], ("Install docker", "Installing docker", "completed"),
                  ("Add you to the docker group", "Adding you", "in_progress"), FOUR[3]])
    assert desk.now["edge"] == "machine" and desk.now["command"] == ""


def test_a_stopped_or_failed_turn_takes_its_card_away_at_once(desk):
    desk.turn(1, steps=TWO)
    desk.end(1, stopped=True, changed=True, line="Stopped while installing ffmpeg.")
    assert not desk.prop("nowVisible")
    desk.turn(2, steps=TWO)
    desk.send(kind="error", turn=2, text="claude is not logged in.")
    desk.send(kind="result", turn=2, ok=False, text="")
    desk.end(2, changed=False)
    assert not desk.prop("nowVisible")                       # not "done in 58 s": it was not
    desk.turn(3, steps=TWO)
    desk.send(kind="error", turn=3, text="a hook complained")
    desk.send(kind="result", turn=3, ok=True, text="Installed it anyway.")
    desk.end(3)
    assert desk.prop("nowVisible") and desk.now["done"]      # an error the turn got past does not undo it


def test_a_bar_that_starts_mid_turn_picks_the_route_up(desk):
    desk.send(type="status", busy=True, provider="claude", turn=5, queue=[])
    assert not desk.prop("nowVisible")
    desk.plan(5, FOUR)                                       # agentd repeats the latest plan on `get`
    assert desk.prop("nowVisible") and desk.now["why"] == "Step 2 of 4 · Esc stops"
    assert desk.now["title"] == "Working on it"              # the ask is not known to a bar that missed it
    desk.call("lost")
    assert not desk.prop("nowVisible") and not desk.prop("connected")
    desk.send(type="status", busy=False, provider="claude", queue=[])
    desk.send(type="status", busy=True, provider="claude", turn=5, queue=[])
    desk.plan(5, FOUR)
    assert desk.prop("nowVisible")
    desk.send(type="status", busy=False, provider="claude", queue=[])   # agentd is idle: the turn ended unheard
    assert not desk.prop("nowVisible")


def test_the_next_turn_starts_clean(desk):
    desk.turn(1, "one", steps=TWO)
    desk.send(kind="status", turn=1, text="Installing", source="step", risk="system", command="sudo x", touched_text="1 file so far")
    desk.end(1)
    desk.turn(2, "two")
    assert not desk.prop("nowVisible")
    desk.plan(2, TWO)
    m = desk.now
    assert m["title"] == "two" and m["edge"] == "machine" and m["command"] == "" and m["why"] == "Step 1 of 2 · Esc stops"


# -- capacity --

def test_everything_fits_on_a_full_hd_screen(desk):
    desk.turn(1, steps=FOUR)
    desk.set("watchModel", rows(3, first={"kind": "meter"}))
    desk.set("aliveModel", rows(2))
    f = desk.faces
    assert (f["now"], f["watching"], f["alive"]) == ("full", "full", "full")
    s = desk.slots
    # From the bottom up, nearest the pill first, 12 apart, the bottom 16 above the pill's row.
    assert (s["now"]["y"], s["now"]["h"]) == (1000 - 172, 172)
    assert (s["watching"]["y"], s["watching"]["h"]) == (1000 - 172 - 12 - 190, 190)      # 50 + 34 + 2 * 44 + 18
    assert (s["alive"]["h"], s["alive"]["y"]) == (168, 1000 - 172 - 12 - 190 - 12 - 168)
    assert s["now"]["x"] == 16 and s["now"]["w"] == 300


def test_a_laptop_folds_what_does_not_fit_by_the_height_rule(laptop):
    assert laptop.prop("railHeight") == 600 and laptop.prop("railBottomY") == 640
    laptop.turn(1, steps=[(f"step {i}", None, "in_progress" if i == 0 else "pending") for i in range(8)])
    laptop.send(kind="status", turn=1, text="Installing", source="step", risk="system", command="sudo pacman -S x")
    laptop.set("watchModel", rows(3, first={"kind": "meter"}))
    laptop.set("aliveModel", rows(2))
    assert laptop.slots["now"]["h"] == 68 + 26 * 8 + 20                    # the command row adds 20
    f = laptop.faces
    assert (f["now"], f["watching"]) == ("full", "full")                   # 296 + 12 + 190 = 498 of 600
    assert f["alive"] == "strip"                                           # + 12 + 168 does not fit
    # The caption makes Now taller still; a taller rows card then no longer fits, and everything above it folds too.
    laptop.send(kind="status", turn=1, text="Formatting", source="step", risk="irreversible", command="sudo mkfs /dev/sdb1")
    assert laptop.slots["now"]["h"] == 68 + 26 * 8 + 20 + 18
    laptop.set("watchModel", rows(6))                                      # 50 + 6 * 44 + 18 = 332
    f = laptop.faces
    assert f["now"] == "full" and f["watching"] == "strip"
    assert f["alive"] == "strip"                                           # would fit alone, but it is above a folded one


def test_the_right_rail_holds_what_its_height_allows(laptop):
    laptop.set("needsModel", rows(3))                                      # 200
    laptop.set("awayModel", rows(3))                                       # 200
    laptop.vitals()                                                        # 214: a stack, two meters and a plain row
    f = laptop.faces
    assert (f["needs"], f["away"], f["machine"]) == ("full", "full", "strip")   # 200 + 12 + 200 + 12 + 214 = 638 > 600
    laptop.set("needsModel", rows(2))                                      # 156
    assert laptop.faces["machine"] == "full"                               # 156 + 12 + 200 + 12 + 214 = 594


def test_slots_are_the_size_of_the_cards_the_rail_draws(desk):
    # The card files are drawn by someone else's code: its heights must be the ones the desk stacks by.
    desk.turn(1, steps=FOUR)
    desk.send(kind="status", turn=1, text="Formatting", source="step", risk="irreversible", command="sudo mkfs /dev/sdb1")
    desk.set("watchModel", rows(3, first={"kind": "meter"}))
    desk.set("needsModel", rows(2))
    desk.pump(0.3)
    for name, widget in (("nowCard", "now"),):
        card = desk.item(name)
        assert card is not None and card.property("implicitHeight") == desk.slots[widget]["h"], widget
    rows_cards = desk.items("rowsCard")
    assert sorted(c.property("implicitHeight") for c in rows_cards) == sorted(
        [desk.slots["watching"]["h"], desk.slots["needs"]["h"]])
    assert desk.slots["needs"]["h"] == 50 + 2 * 44 + 18


def test_a_rails_window_is_up_while_a_card_is_full_and_while_it_folds_away(desk):
    # The window follows what the desk says, not the animation: one that is not up runs none.
    left, right = desk.item("railLeft"), desk.item("railRight")
    assert not left.property("shown") and not right.property("shown")
    desk.set("foldMs", 60)
    desk.turn(1, steps=TWO)
    assert left.property("shown") and not right.property("shown")     # up at once, before the card is visible
    desk.cover((0, 0, 1920, 1080))                                       # a window over it: the card folds
    assert desk.faces["now"] == "strip" and left.property("shown")      # still up while it folds
    desk.pump(0.4)
    assert not left.property("shown")
    desk.cover()
    desk.set("unfoldDelayMs", 60)
    desk.pump(0.3)                                                       # the card unfolds and the window is back
    assert desk.faces["now"] == "full" and left.property("shown")
    desk.pump(0.3)
    assert desk.shown("deskCard-now")


def test_cards_stand_at_their_slots(desk):
    desk.turn(1, steps=FOUR)
    desk.set("needsModel", rows(2))
    desk.pump(0.4)
    now, needs = desk.item("deskCard-now"), desk.item("deskCard-needs")
    s = desk.slots
    assert desk.at(now) == (16, 40 + (s["now"]["y"] - 40)) == (16, s["now"]["y"])
    assert desk.at(needs) == (1920 - 316, s["needs"]["y"])
    assert now.property("opacity") == 1 and needs.property("opacity") == 1
    # The rail takes clicks only where a card is.
    assert plain(desk.item("railLeft").property("hitRects")) == [
        {"x": 0, "y": s["now"]["y"] - 40, "w": 300, "h": s["now"]["h"]}]
    desk.snap("rails")


def test_the_stack_compacts_when_a_widget_goes_but_not_when_it_only_folds(laptop):
    laptop.turn(1, steps=FOUR)
    laptop.set("watchModel", rows(2))
    laptop.set("aliveModel", rows(2))
    before = laptop.slots
    laptop.cover((100, 310, 300, 140))                    # a window over Watching (and not over Now or Alive)
    assert laptop.faces["watching"] == "strip" and laptop.faces["now"] == "full" and laptop.faces["alive"] == "full"
    assert laptop.slots == before                          # the slots stay where they were
    laptop.cover()
    laptop.set("unfoldDelayMs", 60)
    laptop.pump(0.3)
    laptop.set("watchModel", rows(0))                      # nothing counting any more: the widget is absent
    assert "watching" not in laptop.slots
    assert laptop.slots["alive"]["y"] == before["alive"]["y"] + before["watching"]["h"] + 12   # alive slid down


# -- right of way --

def test_a_window_over_a_card_folds_it_at_once_and_it_comes_back_after_the_delay(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("unfoldDelayMs", 250)
    laptop.set("foldMs", 60)
    slot = laptop.slots["now"]
    assert laptop.faces["now"] == "full"
    laptop.cover((slot["x"] + 40, slot["y"] + 10, 400, 300))
    assert laptop.faces["now"] == "strip" and laptop.prop("mode") == "shared"
    laptop.pump(0.15)
    card = laptop.item("deskCard-now")
    assert card.property("opacity") == 0 and not card.isVisible()          # folded away
    assert laptop.shown("deskStrip-now")
    laptop.cover()
    laptop.pump(0.1)
    assert laptop.faces["now"] == "strip"                                  # not back until the window has been gone a while
    laptop.pump(0.4)
    assert laptop.faces["now"] == "full"
    laptop.pump(0.15)
    assert card.property("opacity") == 1 and card.isVisible() and not laptop.shown("deskStrip-now")


def test_a_window_back_in_the_delay_keeps_the_card_folded(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("unfoldDelayMs", 200)
    slot = laptop.slots["now"]
    win = (slot["x"], slot["y"], 300, 100)
    laptop.cover(win)
    laptop.cover()
    laptop.pump(0.12)
    laptop.cover(win)                                      # dragged back over it
    laptop.pump(0.4)
    assert laptop.faces["now"] == "strip"                  # the timer did not run on
    laptop.cover()
    laptop.pump(0.1)
    assert laptop.faces["now"] == "strip"                  # and it starts again from here, not from the first time
    laptop.pump(0.3)
    assert laptop.faces["now"] == "full"


def test_a_window_that_only_touches_the_slot_does_not_cover_it(laptop):
    laptop.turn(1, steps=TWO)
    slot = laptop.slots["now"]
    laptop.cover((slot["x"] + slot["w"], slot["y"], 400, slot["h"]))       # starts where the card ends
    assert laptop.faces["now"] == "full" and laptop.prop("mode") == "shared"
    laptop.cover((slot["x"], slot["y"] - 300, 300, 300))                   # ends where it starts
    assert laptop.faces["now"] == "full"
    laptop.cover((slot["x"] + slot["w"] - 1, slot["y"] + slot["h"] - 1, 50, 50))   # one pixel of the corner
    assert laptop.faces["now"] == "strip"                                  # a partial cover counts


def test_a_window_over_the_top_of_a_rail_leaves_the_card_by_the_pill(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("watchModel", rows(2))
    laptop.set("aliveModel", rows(2))
    s = laptop.slots
    assert s["alive"]["y"] > 40
    laptop.cover((0, 0, 500, s["alive"]["y"] + 20))                         # touches the top card only
    f = laptop.faces
    assert (f["now"], f["watching"], f["alive"]) == ("full", "full", "strip")


def test_cards_fold_widget_by_widget_with_their_own_timers(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("watchModel", rows(2))
    laptop.set("unfoldDelayMs", 150)
    s = laptop.slots
    laptop.cover((0, s["watching"]["y"], 400, s["now"]["y"] + s["now"]["h"] - s["watching"]["y"]))   # both
    assert (laptop.faces["now"], laptop.faces["watching"]) == ("strip", "strip")
    laptop.cover((0, s["watching"]["y"], 400, 20))                          # now only Watching's top
    laptop.pump(0.05)
    laptop.cover((0, s["watching"]["y"], 400, 20))
    laptop.pump(0.3)
    assert (laptop.faces["now"], laptop.faces["watching"]) == ("full", "strip")


def test_a_card_that_grows_into_a_window_folds(laptop):
    laptop.turn(1, steps=TWO)
    slot = laptop.slots["now"]
    laptop.cover((0, slot["y"] - 60, 400, 50))              # just above the card
    assert laptop.faces["now"] == "full"
    laptop.plan(1, TWO + [("third", None, "pending"), ("fourth", None, "pending"), ("fifth", None, "pending")])
    assert laptop.faces["now"] == "strip"                   # five steps reach the window


def test_the_same_windows_again_change_nothing(laptop):
    laptop.turn(1, steps=TWO)
    slot = laptop.slots["now"]
    win = (slot["x"], slot["y"], 100, 100)
    laptop.cover(win)
    laptop.set("unfoldDelayMs", 100)
    for _ in range(3):                                      # the 500 ms refresh hands over the same list each time
        laptop.cover(win)
        laptop.pump(0.05)
    assert laptop.faces["now"] == "strip"


# -- modes --

def test_a_window_on_the_stage_shares_the_desk_and_a_fullscreen_one_takes_it(desk):
    desk.turn(1, steps=TWO)
    assert desk.prop("mode") == "open" and desk.prop("pillWidth") == 900 and not desk.prop("capsule")
    desk.cover((700, 200, 500, 400))                       # an app in the middle: no rail touched
    assert desk.prop("mode") == "shared" and desk.prop("pillWidth") == 360
    assert desk.faces["now"] == "full"
    desk.pump(0.2)
    assert desk.item("pillBox").property("width") == 360
    desk.snap("shared")
    desk.cover((0, 0, 1920, 1080, True))
    assert desk.prop("mode") == "immersive" and desk.prop("capsule") and desk.prop("pillWidth") == 100
    assert set(desk.faces.values()) == {"hidden"}
    desk.pump(0.3)
    assert not desk.shown("deskCard-now") and desk.items("deskStrip-now") == []
    assert plain(desk.item("railLeft").property("hitRects")) == []
    desk.snap("immersive")
    desk.cover()
    assert desk.prop("mode") == "open"
    desk.pump(0.6)                                          # the card comes back 400 ms after the window went
    assert desk.faces["now"] == "full"
    assert desk.shown("deskCard-now")


def test_the_pill_takes_the_stage_less_the_strips(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("watchModel", rows(2))
    laptop.set("aliveModel", rows(2))
    assert laptop.prop("pillWidth") == 900 - 0 or laptop.prop("pillWidth") <= 900
    laptop.call("fold")
    laptop.send(type="desk", folded=True, hidden=[], rails={}, order={"left": ["now", "watching", "alive"],
                                                                     "right": ["needs", "away", "machine"]}, screen="")
    laptop.pump(0.2)
    widths = [it.property("width") for it in (laptop.item("deskStrip-now"), laptop.item("deskStrip-watching"),
                                              laptop.item("deskStrip-alive"))]
    side = sum(widths) + 2 * 12
    assert laptop.prop("leftStripsWidth") == side            # what the strips measure is what the pill leaves
    assert laptop.prop("pillWidth") == max(360, min(900, 1280 - 2 * (side + 12 + 16)))
    assert 360 <= laptop.prop("pillWidth") < 900
    laptop.send(type="desk", folded=False)
    laptop.pump(0.2)
    assert laptop.prop("leftStripsWidth") == 0 or laptop.prop("pillWidth") == 900


def test_a_card_in_full_narrows_the_pill_on_a_small_screen(laptop):
    # A rail is 300 wide and the line above the pill must never cover a card: 1280 px leaves 624.
    laptop.turn(1, steps=TWO)
    assert laptop.faces["now"] == "full"
    assert laptop.prop("pillWidth") == 1280 - 2 * (300 + 12 + 16)
    laptop.end(1, stopped=True)
    laptop.pump(0.3)
    assert laptop.prop("pillWidth") == 900                 # nothing showing: the whole stage


def test_a_card_in_full_leaves_the_pill_its_900_on_a_big_screen(desk):
    desk.turn(1, steps=TWO)
    assert desk.faces["now"] == "full" and desk.prop("pillWidth") == 900


def test_a_lot_of_strips_cannot_take_the_pill_below_its_minimum(laptop):
    laptop.send(type="desk", folded=True, rails={"now": "left", "watching": "left", "alive": "left", "needs": "left",
                                                  "away": "left", "machine": "left"},
                order={"left": ["now", "watching", "alive", "needs", "away", "machine"], "right": []})
    laptop.turn(1, steps=TWO)
    for name in ("watchModel", "needsModel", "awayModel", "machineModel", "aliveModel"):
        laptop.set(name, rows(2))
    laptop.resize(900, 720)
    laptop.pump(0.3)
    assert laptop.prop("pillWidth") == 360


# -- the desk state agentd keeps --

def desk_msg(**kw):
    msg = {"type": "desk", "folded": False, "hidden": [],
           "rails": {"now": "left", "watching": "left", "needs": "right", "machine": "right", "away": "right", "alive": "left"},
           "order": {"left": ["now", "watching", "alive"], "right": ["needs", "away", "machine"]}, "screen": ""}
    msg.update(kw)
    return msg


def test_the_desk_word_folds_every_card_to_its_strip_and_back(desk):
    desk.turn(1, steps=FOUR)
    desk.set("needsModel", rows(2))
    desk.pump(0.3)
    assert (desk.faces["now"], desk.faces["needs"]) == ("full", "full")
    desk.send(**desk_msg(folded=True))
    assert (desk.faces["now"], desk.faces["needs"]) == ("strip", "strip") and desk.prop("folded")
    desk.pump(0.4)
    assert not desk.shown("deskCard-now") and desk.shown("deskStrip-now") and desk.shown("deskStrip-needs")
    desk.snap("folded")
    desk.send(**desk_msg(folded=False))
    desk.pump(0.4)
    assert (desk.faces["now"], desk.faces["needs"]) == ("full", "full")
    assert desk.shown("deskCard-now") and not desk.shown("deskStrip-now")


def test_hidden_widgets_have_no_face_and_needs_cannot_be_hidden(desk):
    desk.turn(1, steps=TWO)
    desk.set("needsModel", rows(2))
    desk.send(**desk_msg(hidden=["now", "needs", "machine", "nonsense"]))
    assert desk.prop("hidden") == ["now", "machine"]         # nothing in agentd's file can hide Needs you
    assert desk.faces["now"] == "hidden" and "now" not in desk.slots
    assert desk.faces["needs"] == "full"
    assert not desk.shown("deskStrip-now")
    desk.send(**desk_msg(hidden=[]))
    assert desk.faces["now"] == "full"


def test_moving_a_widget_moves_its_card_and_its_strip(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("watchModel", rows(2))
    laptop.set("needsModel", rows(2))
    laptop.send(**desk_msg(rails={"now": "left", "watching": "right", "needs": "right", "machine": "right",
                                  "away": "right", "alive": "left"},
                           order={"left": ["now", "alive"], "right": ["watching", "needs", "away", "machine"]}))
    laptop.pump(0.3)
    s = laptop.slots
    assert s["watching"]["x"] == 1280 - 316 and s["needs"]["x"] == 1280 - 316
    assert s["watching"]["y"] > s["needs"]["y"]              # rank 0 is the bottom of the rail
    assert laptop.at(laptop.item("deskCard-watching")) == (1280 - 316, s["watching"]["y"])
    laptop.call("fold")
    laptop.send(**desk_msg(folded=True, rails={"now": "left", "watching": "right", "needs": "right", "machine": "right",
                                               "away": "right", "alive": "left"},
                           order={"left": ["now", "alive"], "right": ["watching", "needs", "away", "machine"]}))
    laptop.pump(0.3)
    right_strips = laptop.item("stripsRight")
    x_watching = laptop.at(laptop.item("deskStrip-watching"))[0]
    x_needs = laptop.at(laptop.item("deskStrip-needs"))[0]
    # (at() rounds to a whole pixel; the strips' x need not be one)
    assert round(right_strips.property("x")) <= x_watching < x_needs


def test_a_desk_message_that_is_partial_or_wrong_costs_nothing(desk):
    desk.send(**desk_msg(folded=True, screen="DP-2"))
    assert desk.prop("screen") == "DP-2"
    desk.send(type="desk", hidden="everything")              # a shape it does not know
    desk.send(type="desk", rails={"now": "up", "nonsense": "left"}, order={"left": "now", "right": [3, None]})
    desk.send(type="desk")
    assert desk.prop("folded") and desk.prop("screen") == "DP-2"
    assert desk.prop("rails")["now"] == "left"
    order = desk.prop("order")
    assert sorted(order["left"] + order["right"]) == ["alive", "away", "machine", "needs", "now", "watching"]
    desk.send(type="desk", screen="")
    assert desk.prop("screen") == ""


def test_messages_the_desk_does_not_know_are_ignored(desk):
    desk.turn(1, steps=TWO)
    before = desk.now
    for msg in ({"type": "entries", "entries": []}, {"type": "job-result", "id": "x", "ok": True, "text": "hi"},
                {"type": "summon"}, {"type": "event", "kind": "text", "turn": 1, "text": "hi"},
                {"type": "event", "kind": "queued", "turn": 2, "prompt": "later"},
                {"type": "event", "kind": "tool", "turn": 1}, {"type": "event", "kind": "who-knows", "turn": 1},
                {"type": "nonsense"}, {}):
        desk.send(**msg)
    assert desk.now == before and desk.prop("nowVisible")
    for junk in (None, 5, "text", []):
        QtCore.QMetaObject.invokeMethod(desk.desk, "handle", QtCore.Q_ARG("QVariant", junk))
    desk.pump()
    assert desk.warnings == []


# -- strips --

def test_strips_run_outward_from_the_pill_nearest_first(desk):
    desk.turn(1, steps=FOUR)
    desk.set("watchModel", rows(2))
    desk.set("aliveModel", rows(2))
    desk.set("needsModel", rows(2))
    desk.set("awayModel", rows(2))
    desk.set("machineModel", rows(1))
    desk.send(**desk_msg(folded=True))
    desk.pump(0.3)
    pill_x, pill_w = desk.item("pillBox").property("x"), desk.item("pillBox").property("width")
    left = [desk.item(f"deskStrip-{w}") for w in ("alive", "watching", "now")]
    right = [desk.item(f"deskStrip-{w}") for w in ("needs", "away", "machine")]
    assert all(it is not None for it in left + right)
    lx = [desk.at(it)[0] for it in left]
    rx = [desk.at(it)[0] for it in right]
    # Left: the first of the rail's order is the outermost; each strip is 12 from the next, the nearest 12 from the pill.
    assert abs(lx[0] + left[0].property("width") - (pill_x - 12)) <= 1      # positions are whole pixels, widths are not
    for near, far in itertools.pairwise(left):
        assert abs(desk.at(far)[0] + far.property("width") - (desk.at(near)[0] - 12)) <= 1
    assert rx[0] == pill_x + pill_w + 12
    for near, far in itertools.pairwise(right):
        assert abs(desk.at(far)[0] - (desk.at(near)[0] + near.property("width") + 12)) <= 1
    # All on the pill's row.
    assert len({desk.at(it)[1] for it in left + right}) == 1
    assert desk.at(left[0])[1] == 1080 - 64 + 26 - 14
    desk.snap("strips")


def test_the_strip_texts(desk):
    desk.turn(1, steps=FOUR)
    desk.set("needsModel", rows(2))
    desk.set("watchModel", rows(2))
    desk.send(**desk_msg(folded=True))
    assert desk.prop("leftStrips")[-1]["text"] == "step 2 of 4"
    assert desk.prop("leftStrips")[-1]["dot"] == THEME["accent"] and desk.prop("leftStrips")[-1]["ring"]
    assert desk.prop("rightStrips")[0]["text"] == "2 need you" and desk.prop("rightStrips")[0]["outlined"]
    desk.end(1)
    assert desk.prop("leftStrips")[-1]["text"] == "done" and desk.prop("leftStrips")[-1]["dot"] == THEME["good"]
    desk.pill_call("dismiss")
    desk.turn(2)                                            # no plan: Now is here for the system step only
    desk.send(kind="status", turn=2, text="Installing", source="step", risk="system", command="sudo x")
    assert desk.prop("leftStrips")[-1]["text"] == "working"
    desk.end(2, stopped=True)
    desk.turn(3, steps=[("only", None, "in_progress")])   # a plan of one is still "step 1 of 1"
    desk.send(kind="status", turn=3, text="Installing", source="step", risk="system", command="sudo x")
    assert desk.prop("leftStrips")[-1]["text"] == "step 1 of 1"


def test_strips_past_three_a_side_merge_into_a_counting_one(laptop):
    everything = {"now": "left", "watching": "left", "alive": "left", "needs": "left", "away": "left", "machine": "left"}
    laptop.send(**desk_msg(folded=True, rails=everything,
                           order={"left": ["now", "watching", "needs", "away", "machine", "alive"], "right": []}))
    laptop.turn(1, steps=TWO)
    for name in ("watchModel", "needsModel", "awayModel", "machineModel", "aliveModel"):
        laptop.set(name, rows(2))
    laptop.pump(0.3)
    shown = [n for n in ("alive", "machine", "away", "needs", "watching", "now") if laptop.item(f"deskStrip-{n}")]
    assert shown == ["alive", "machine", "away"]              # the three nearest the pill keep their own chip
    more = laptop.item("deskStripMore")
    assert more is not None and more.property("text") == "+3"
    assert laptop.at(more)[0] < laptop.at(laptop.item("deskStrip-away"))[0]   # outermost
    laptop.snap("strips-merged")
    assert [s["text"] for s in laptop.prop("leftStrips")][-1] == "+3"
    # The pure merge, at its edges.
    four = [{"id": str(i), "text": f"s{i}"} for i in range(4)]
    assert [s["text"] for s in laptop.call("mergeStrips", four)] == ["s0", "s1", "s2", "+1"]
    assert laptop.call("mergeStrips", four[:3]) == four[:3]
    assert laptop.call("mergeStrips", []) == []


def test_a_strip_fades_in_as_its_card_folds(desk):
    desk.turn(1, steps=TWO)
    desk.set("foldMs", 200)
    desk.send(**desk_msg(folded=True))
    strip = desk.item("deskStrip-now")
    assert strip.property("opacity") < 1
    desk.pump(0.5)
    assert strip.property("opacity") == 1
    desk.send(**desk_msg(folded=False))
    desk.pump(0.1)
    assert desk.items("deskStrip-now") == []


def test_no_strips_and_no_cards_at_rest(desk):
    assert set(desk.faces.values()) == {"hidden"}
    assert desk.slots == {} and desk.prop("leftStrips") == [] and desk.prop("rightStrips") == []
    assert desk.item("stripsLeft").property("width") == 0 and desk.item("stripsRight").property("width") == 0
    assert all(not desk.shown(f"deskCard-{w}") for w in ("now", "watching", "needs"))


# -- what leaves --

def test_the_desk_asks_for_its_state_when_it_connects(desk):
    assert desk.sent == []
    desk.set("connected", True)
    assert desk.sent == [{"type": "desk", "op": "get"}]
    desk.call("lost")
    desk.set("connected", True)
    assert desk.sent == [{"type": "desk", "op": "get"}] * 2


def test_what_the_desk_asks_agentd_to_do(desk):
    desk.call("fold")
    desk.call("hide", "machine")
    desk.call("show", "machine")
    desk.call("move", "now", "right", 0)
    desk.call("move", "watching", "", 2)
    assert desk.sent == [
        {"type": "desk", "op": "fold"},
        {"type": "desk", "op": "hide", "widget": "machine"},
        {"type": "desk", "op": "show", "widget": "machine"},
        {"type": "desk", "op": "move", "widget": "now", "rail": "right", "rank": 0},
        {"type": "desk", "op": "move", "widget": "watching", "rank": 2},
    ]
    # Nothing is applied here: the state comes back from agentd.
    assert not desk.prop("folded") and desk.prop("rails")["now"] == "left"


def test_needs_you_is_never_asked_to_hide(desk):
    assert desk.call("hide", "needs") is False
    assert desk.sent == []


def test_a_turn_sends_agentd_nothing(desk):
    desk.set("connected", True)
    sent = len(desk.sent)
    desk.turn(1, steps=FOUR)
    desk.send(kind="status", turn=1, text="Installing", source="step", risk="system", command="sudo x")
    desk.end(1)
    assert len(desk.sent) == sent


def test_a_click_on_nows_title_opens_the_turns_details(desk):
    desk.turn(1, steps=TWO)
    desk.pump(0.4)
    card = desk.item("nowCard")
    desk.click(card, 60, 18)
    assert [dict(m) for m in plain(desk.win.property("pillSent"))][-1] == {"type": "details", "turn": 1}


def test_a_row_button_reaches_the_desk_with_its_widget(desk):
    desk.set("watchModel", rows(2, first={"key": "iso", "button": "Open"}))
    desk.pump(0.4)
    row = next(r for r in desk.items("rowsRow") if r.property("key") == "iso")
    todo, button = list(row.childItems()), None
    while todo and button is None:
        it = todo.pop()
        if it.objectName() == "rowsButton":
            button = it
        todo.extend(it.childItems())
    desk.click(row)                                        # the row itself does nothing
    assert plain(desk.win.property("actions")) == []
    desk.click(button)
    assert plain(desk.win.property("actions")) == [["watching", "iso", "Open"]]


# -- Watching: the jobs table --

def test_a_jobs_table_becomes_the_watching_rows_in_agentds_order(desk):
    desk.jobs(ISO, BUILD, TIMER)
    desk.clock(240)
    m = desk.watch
    assert m["title"] == "Watching" and m["why"] == "3 counting · each ends with one line in the pill"
    assert m["rows"] == [
        {"key": "a1b2", "kind": "meter", "title": "Ubuntu 26.04 ISO", "sub": "", "meter": 0.43,
         "meterText": "4 min", "tone": "machine", "pulse": False, "button": "", "remove": True},
        {"key": "c3d4", "kind": "dot", "title": "Tell me when the build finishes", "sub": "6 min so far",
         "meter": None, "meterText": "", "tone": "machine", "pulse": True, "button": "", "remove": True},
        {"key": "e5f6", "kind": "dot", "title": "Timer, 10 min", "sub": "6:00 left", "meter": None,
         "meterText": "", "tone": "sessions", "pulse": True, "button": "", "remove": True},
    ]
    assert desk.prop("present")["watching"] and desk.faces["watching"] == "full"
    desk.jobs(TIMER, BUILD, ISO)                                     # the order is agentd's
    assert [r["key"] for r in desk.watch["rows"]] == ["e5f6", "c3d4", "a1b2"]


def test_a_job_with_no_progress_is_a_dot_row_and_one_with_progress_a_meter(desk):
    desk.jobs(job("b1", "Backing up ~/Documents"))
    desk.clock(40)
    (r,) = desk.watch["rows"]
    assert (r["kind"], r["sub"], r["pulse"], r["tone"], r["remove"]) == ("dot", "40 s so far", True, "machine", True)
    desk.jobs(job("b1", "Backing up ~/Documents", pct=0.0))
    desk.clock(40)
    (r,) = desk.watch["rows"]
    assert (r["kind"], r["meter"], r["meterText"]) == ("meter", 0.0, "40 s")      # 0% is progress, not none


def test_the_clock_drives_the_times_the_rows_say(desk):
    desk.jobs(ISO, BUILD, TIMER)                # the build began 2 min before T0, the timer has 10 min from T0
    table = ((0, "0 s", "2 min so far", "10:00 left"),
             (40, "40 s", "2 min so far", "9:20 left"),
             (200, "3 min", "5 min so far", "6:40 left"),
             (3900, "1 h 5 min", "1 h 7 min so far", "0:00 left"),
             (7260, "2 h 1 min", "2 h 3 min so far", "0:00 left"))
    for seconds, meter_text, so_far, left in table:
        desk.clock(seconds)
        meter, watch, timer = desk.watch["rows"]
        assert (meter["meterText"], watch["sub"], timer["sub"]) == (meter_text, so_far, left), seconds
    # A whole hour is shown as one, and the time left is never negative.
    desk.jobs(job("e5f6", "Timer, 1 h", kind="timer", deadline=T0 + 3900))
    desk.clock(0)
    assert desk.watch["rows"][0]["sub"] == "1:05:00 left"
    desk.clock(3899.4)
    assert desk.watch["rows"][0]["sub"] == "0:01 left"
    desk.clock(3900)
    assert desk.watch["rows"][0]["sub"] == "0:00 left"
    desk.clock(4000)
    assert desk.watch["rows"][0]["sub"] == "0:00 left"


def test_a_row_is_redrawn_in_place_as_the_clock_moves(desk):
    desk.jobs(ISO, TIMER)
    desk.clock(0)
    desk.pump(0.4)
    desk.card_row("a1b2").setProperty("marker", 7)
    desk.clock(61)
    assert desk.card_row("a1b2").property("marker") == 7             # the same row item, not built again
    assert desk.inside(desk.card_row("a1b2"), "rowsMeta").property("text") == "1 min"
    assert desk.inside(desk.card_row("e5f6"), "rowsRowSub").property("text") == "8:59 left"


def test_a_done_job_turns_green_says_how_long_it_took_and_leaves_12_s_after_it_ended(desk):
    desk.jobs(ISO, BUILD)
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", state="done", pct=100.0, ended=T0 + 180), BUILD)
    for seconds in (180, 185, 191.9):
        desk.clock(seconds)
        assert [r["key"] for r in desk.watch["rows"]] == ["a1b2", "c3d4"], seconds
    assert desk.watch["rows"][0] == {
        "key": "a1b2", "kind": "dot", "title": "Ubuntu 26.04 ISO", "sub": "done in 3 min", "meter": None,
        "meterText": "", "tone": "ok", "pulse": False, "button": "", "remove": True}
    assert desk.watch["why"] == "1 counting · each ends with one line in the pill"
    desk.snap("watching-done")
    desk.clock(192)
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4"]          # 12 s after it ended, by the clock
    # Nothing counting and nothing left to show: the card goes.
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", state="done", ended=T0 + 180))
    desk.clock(185)
    assert [r["key"] for r in desk.watch["rows"]] == ["a1b2"] and desk.watch["why"] == "finished"
    assert desk.prop("present")["watching"]
    desk.clock(193)
    assert desk.watch["rows"] == [] and not desk.prop("present")["watching"]
    assert desk.faces["watching"] == "hidden" and "watching" not in desk.slots


def test_a_done_job_with_no_times_just_says_done(desk):
    desk.jobs(job("a1b2", "Timer, 10 min", kind="timer", state="done", started=None, ended=None), ISO)
    desk.clock(1000)
    assert desk.watch["rows"][0]["sub"] == "done" and desk.watch["rows"][0]["tone"] == "ok"


def test_a_failed_job_is_red_with_its_last_line_and_why_and_stays_until_dismissed(desk):
    desk.jobs(ISO, UPDATE)
    desk.clock(40)
    assert desk.watch["rows"][1] == {
        "key": "f7a8", "kind": "dot", "title": "pacman -Syu", "sub": "pacman: could not resolve host",
        "meter": None, "meterText": "", "tone": "red", "pulse": False, "button": "Why?", "remove": True}
    desk.clock(3600 * 5)                                               # hours later it is still there
    assert [r["key"] for r in desk.watch["rows"]] == ["a1b2", "f7a8"]
    desk.jobs(UPDATE)
    assert [r["key"] for r in desk.watch["rows"]] == ["f7a8"]
    assert desk.watch["why"] == "finished" and desk.prop("present")["watching"]
    desk.snap("watching-failed")
    desk.jobs()
    assert desk.watch["rows"] == [] and not desk.prop("present")["watching"]


def test_watching_says_how_many_are_counting_while_any_are(desk):
    copy = job("d1", "Copy", state="done", ended=T0 + 2)
    desk.jobs(ISO, BUILD, UPDATE, copy)
    desk.clock(5)
    assert desk.watch["why"] == "2 counting · each ends with one line in the pill"
    desk.jobs(BUILD, UPDATE, copy)
    desk.clock(5)
    assert desk.watch["why"] == "1 counting · each ends with one line in the pill"
    desk.jobs(UPDATE, copy)
    desk.clock(5)
    assert desk.watch["why"] == "finished"


def test_the_watching_strip_is_the_first_meter_and_its_percent_else_the_count(desk):
    copy = job("d1", "Copy", state="done", ended=T0 + 2)
    desk.jobs(BUILD, ISO, job("a2", "Copying the photos", pct=7.0))
    desk.clock(10)
    strip = desk.watch["strip"]
    assert strip["text"] == "Ubuntu 43%" and strip["dot"] == THEME["accent"] and strip["ring"]
    desk.jobs(BUILD, TIMER, UPDATE, copy)
    desk.clock(5)
    strip = desk.watch["strip"]
    assert strip["text"] == "2 counting" and strip["dot"] == THEME["accent"] and strip["ring"]
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", pct=43.4))
    desk.clock(5)
    assert desk.watch["strip"]["text"] == "Ubuntu 43%"                # rounded, as the card's percent is
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", pct=43.6))
    desk.clock(5)
    assert desk.watch["strip"]["text"] == "Ubuntu 44%"
    # With nothing counting, the strip says what is left.
    desk.jobs(UPDATE)
    desk.clock(5)
    assert desk.watch["strip"] == {"text": "finished", "dot": THEME["bad"], "ring": False}
    desk.jobs(copy)
    desk.clock(5)
    assert desk.watch["strip"] == {"text": "finished", "dot": THEME["good"], "ring": False}


def test_the_strip_beside_the_pill_reads_the_jobs_when_the_card_folds(desk):
    desk.jobs(ISO, BUILD)
    desk.clock(10)
    desk.send(**desk_msg(folded=True))
    desk.pump(0.3)
    assert desk.prop("leftStrips")[0]["text"] == "Ubuntu 43%"
    strip = desk.item("deskStrip-watching")
    assert strip is not None and strip.property("text") == "Ubuntu 43%" and strip.property("ring")
    desk.jobs(BUILD, TIMER)
    desk.clock(10)
    desk.pump(0.3)
    assert desk.item("deskStrip-watching").property("text") == "2 counting"
    desk.snap("watching-strip")


def test_the_x_and_why_go_out_as_messages_and_the_row_leaves_at_once(desk):
    copy = job("d1e2", "Copy photos", state="done", ended=T0 + 100)
    desk.jobs(ISO, BUILD, UPDATE, copy)
    desk.clock(105)
    desk.pump(0.4)
    assert desk.sent == []
    assert [r["key"] for r in desk.watch["rows"]] == ["a1b2", "c3d4", "f7a8", "d1e2"]
    # A running row's x stops it, and the row is gone before agentd says so.
    desk.click(desk.inside(desk.card_row("a1b2"), "rowsRemove"))
    assert desk.sent == [{"type": "jobs", "op": "stop", "id": "a1b2"}]
    assert desk.card_row("a1b2") is None and [r["key"] for r in desk.watch["rows"]] == ["c3d4", "f7a8", "d1e2"]
    # A table already on its way still lists it: it stays gone.
    desk.jobs(ISO, BUILD, UPDATE, copy)
    desk.clock(106)
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4", "f7a8", "d1e2"]
    # A done row's x drops it, and a failed one's too.
    desk.click(desk.inside(desk.card_row("d1e2"), "rowsRemove"))
    desk.click(desk.inside(desk.card_row("f7a8"), "rowsRemove"))
    assert desk.sent[1:] == [{"type": "jobs", "op": "dismiss", "id": "d1e2"},
                             {"type": "jobs", "op": "dismiss", "id": "f7a8"}]
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4"]
    # A table that still has the dismissed one (agentd has yet to drop it) does not bring it back.
    desk.jobs(BUILD, UPDATE)
    desk.clock(107)
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4"]
    # One without it is agentd's word: a failure of the same id after that is a new row.
    desk.jobs(BUILD)
    desk.jobs(BUILD, UPDATE)
    desk.clock(108)
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4", "f7a8"]
    # Why? is its own message and leaves the row where it is.
    desk.pump(0.4)
    desk.click(desk.inside(desk.card_row("f7a8"), "rowsButton"))
    assert desk.sent[-1] == {"type": "jobs", "op": "why", "id": "f7a8"} and len(desk.sent) == 4
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4", "f7a8"]


def test_a_second_press_or_an_unknown_row_sends_nothing(desk):
    desk.jobs(ISO, BUILD)
    desk.clock(10)
    desk.call("_removeJob", "a1b2")
    desk.call("_removeJob", "a1b2")
    desk.call("_removeJob", "nope")
    desk.call("_removeJob", "")
    assert desk.sent == [{"type": "jobs", "op": "stop", "id": "a1b2"}]


def test_watching_never_reaches_the_model(desk):
    # Stop, dismiss and Why? are the shell's words to agentd's registry, never a turn.
    desk.jobs(ISO, UPDATE)
    desk.clock(10)
    desk.pump(0.4)
    desk.click(desk.inside(desk.card_row("f7a8"), "rowsButton"))
    desk.click(desk.inside(desk.card_row("a1b2"), "rowsRemove"))
    assert {m["type"] for m in desk.sent} == {"jobs"} and len(desk.sent) == 2
    assert plain(desk.win.property("pillSent")) == []


def test_a_row_action_for_another_widget_or_word_is_not_watchings(desk):
    desk.jobs(ISO, UPDATE)
    desk.clock(10)
    for widget, key, action in (("watching", "f7a8", "Open"), ("needs", "f7a8", "Why?"), ("watching", "f7a8", ""),
                                ("away", "f7a8", "Undo"), ("needs", "f7a8", "Do it")):
        QtCore.QMetaObject.invokeMethod(desk.desk, "rowAction", QtCore.Qt.DirectConnection,
                                        QtCore.Q_ARG("QString", widget), QtCore.Q_ARG("QString", key),
                                        QtCore.Q_ARG("QString", action))
    QtCore.QMetaObject.invokeMethod(desk.desk, "rowRemove", QtCore.Qt.DirectConnection,
                                    QtCore.Q_ARG("QString", "needs"), QtCore.Q_ARG("QString", "a1b2"))
    desk.pump()
    assert desk.sent == [] and len(desk.watch["rows"]) == 2


def test_a_jobs_table_that_is_odd_costs_nothing(desk):
    desk.jobs(ISO)
    desk.clock(10)
    before = desk.watch
    for msg in ({"type": "jobs"}, {"type": "jobs", "jobs": "everything"}, {"type": "jobs", "jobs": None},
                {"type": "jobs", "jobs": {"a": 1}}):
        desk.send(**msg)
    assert desk.watch == before
    # Rows that are not jobs, or that say a state it does not know, or that repeat an id, are dropped.
    desk.jobs(None, 5, "text", [], {"title": "no id"}, job("x1", "Odd", state="paused"),
              job("x2", "Odd", state="stopped"), ISO, job("a1b2", "Twice", state="running"))
    desk.clock(10)
    assert [(r["key"], r["title"]) for r in desk.watch["rows"]] == [("a1b2", "Ubuntu 26.04 ISO")]
    # pct is held to 0..100 and times to numbers; a job that says nothing of itself still has a row.
    desk.jobs(job("p1", "Over", pct=150), job("p2", "Under", pct=-5), job("p3", "Words", pct="lots"),
              job("p4", "", kind="timer", deadline="soon", started="yesterday"),
              job("p5", "", kind="weird"), job("p6", "Numbers", pct="50", started=str(T0)))
    desk.clock(30)
    by = {r["key"]: r for r in desk.watch["rows"]}
    assert (by["p1"]["kind"], by["p1"]["meter"]) == ("meter", 1.0)
    assert (by["p2"]["kind"], by["p2"]["meter"]) == ("meter", 0.0)
    assert by["p3"]["kind"] == "dot" and by["p3"]["sub"] == "30 s so far"
    assert by["p4"]["title"] == "Timer" and by["p4"]["sub"] == "0 s so far"     # no deadline, no countdown
    assert by["p5"]["title"] == "Job" and by["p5"]["kind"] == "dot"
    assert (by["p6"]["meter"], by["p6"]["meterText"]) == (0.5, "30 s")
    desk.jobs(job("w1", "", kind="watch"))
    assert desk.watch["rows"][0]["title"] == "Watching"


def test_watching_leaves_when_the_table_is_empty_and_comes_back_with_the_next_job(desk):
    desk.jobs(ISO)
    desk.clock(10)
    assert desk.prop("present")["watching"]
    desk.jobs()
    assert not desk.prop("present")["watching"] and desk.watch["rows"] == [] and desk.watch["why"] == ""
    assert desk.prop("leftStrips") == [] and desk.faces["watching"] == "hidden"
    desk.jobs(BUILD)
    desk.clock(10)
    assert desk.prop("present")["watching"]


def test_a_model_set_by_hand_is_left_alone_until_a_table_changes_it(desk):
    desk.set("watchModel", rows(2))
    desk.set("now", T0 * 1000)
    assert desk.watch == rows(2)
    desk.call("lost")
    assert desk.watch == rows(2)                                      # no table, nothing to clear


def test_hiding_watching_folds_no_job_away(desk):
    desk.jobs(ISO)
    desk.clock(10)
    desk.send(**desk_msg(hidden=["watching"]))
    assert desk.faces["watching"] == "hidden" and desk.prop("leftStrips") == []
    assert desk.watch["rows"][0]["key"] == "a1b2"                     # still counting: it only has no face
    desk.send(**desk_msg(hidden=[]))
    assert desk.faces["watching"] == "full"


def test_the_jobs_card_is_drawn_in_the_left_rail_with_its_x_on_the_meter(desk):
    desk.jobs(ISO, BUILD, TIMER, UPDATE)
    desk.clock(240)
    desk.pump(0.4)
    card = desk.item("deskCard-watching")
    assert card is not None and desk.at(card)[0] == 16
    assert desk.inside(card, "rowsTitle").property("text") == "Watching"
    assert desk.inside(card, "rowsWhy").property("text") == "3 counting · each ends with one line in the pill"
    meter = desk.card_row("a1b2")
    assert desk.inside(meter, "rowsMeter") is not None and desk.inside(meter, "rowsRemove") is not None
    assert desk.inside(desk.card_row("f7a8"), "rowsButton").property("text") == "Why?"
    desk.snap("watching")


# -- the clock --

def test_nothing_ticks_until_a_job_is_counting(live):
    assert not live.prop("clockRunning") and not live.prop("ticking")
    live.jobs(UPDATE)                                                # a failure alone has no clock to move
    assert not live.prop("clockRunning")
    live.jobs()
    live.jobs(ISO)
    assert live.prop("clockRunning") and live.prop("ticking")
    live.jobs(job("a1b2", "Ubuntu 26.04 ISO", state="done", ended=T0 + 1))
    assert live.prop("clockRunning")                                 # a done row has to leave on time
    live.jobs(UPDATE)
    assert not live.prop("clockRunning")
    live.jobs(ISO)
    assert live.prop("clockRunning")
    live.jobs()
    assert not live.prop("clockRunning")


def test_the_clock_reads_the_real_time_and_moves_once_a_second_while_a_job_counts(live):
    live.jobs(job("a1b2", "Ubuntu 26.04 ISO", pct=43.0, started=time.time() - 250))
    first = live.prop("now")
    assert abs(first - time.time() * 1000) < 1500
    assert live.watch["rows"][0]["meterText"] == "4 min"
    live.pump(2.3)
    assert live.prop("now") - first >= 1800                          # it kept time while the job counted
    live.jobs()
    live.pump(0.1)
    frozen = live.prop("now")
    live.pump(1.3)
    assert live.prop("now") == frozen                                # and stopped when there was nothing to count


def test_a_done_row_leaves_by_the_real_clock_even_if_agentd_says_nothing_more(live):
    ended = time.time() - 10.5
    live.jobs(job("a1b2", "Copy photos", state="done", ended=ended, started=ended - 30))
    assert [r["key"] for r in live.watch["rows"]] == ["a1b2"] and live.watch["rows"][0]["sub"] == "done in 30 s"
    live.pump(2.4)
    assert live.watch["rows"] == [] and not live.prop("present")["watching"]


def test_a_calm_desk_keeps_the_time_a_test_gives_it(desk):
    desk.jobs(ISO)
    desk.clock(99)
    desk.pump(1.3)
    assert desk.prop("now") == (T0 + 99) * 1000 and desk.watch["rows"][0]["meterText"] == "1 min"


# -- Needs you: the coding sessions --

def test_two_sessions_waiting_show_the_card_and_one_does_not(desk):
    desk.dev([session("k1")], ["k1"])
    assert len(desk.needs["rows"]) == 1 and not desk.prop("present")["needs"]
    assert desk.faces["needs"] == "hidden" and not desk.shown("deskCard-needs")
    both = [session("k1"), session("k2", role="builder", title="Tracker", last="install qemu-full?")]
    desk.dev(both, ["k1", "k2"])
    assert desk.prop("present")["needs"] and desk.faces["needs"] == "full"
    desk.pump(0.4)
    assert desk.at(desk.item("deskCard-needs"))[0] == 1920 - 316      # the right rail, the passenger's
    by_place = sorted(desk.items("rowsRow"), key=lambda r: desk.at(r)[1])
    assert [r.property("key") for r in by_place] == ["k1", "k2"]
    desk.snap("needs")
    desk.dev(both, ["k2"])                                            # one of them is handled: the card leaves
    assert not desk.prop("present")["needs"] and desk.faces["needs"] == "hidden"
    desk.dev([], [])
    assert desk.needs["rows"] == [] and desk.needs["why"] == ""


def face(desk):
    """What the pill's stone shows (shell/Stone.qml), from the real PillState beside the desk."""
    return plain(desk.pill.property("face"))


def test_the_stone_knocks_for_one_session_waiting_and_for_two(desk):
    assert face(desk) == "rest" and desk.pill.property("needsYou") is False
    desk.dev([session("k1")], ["k1"])
    assert desk.prop("needsYou") and desk.pill.property("needsYou") is True and face(desk) == "needs"
    assert not desk.prop("present")["needs"]                          # one session is the line, not the card
    both = [session("k1"), session("k2", role="builder", title="Tracker")]
    desk.dev(both, ["k1", "k2"])
    assert face(desk) == "needs" and desk.prop("present")["needs"]
    desk.dev(both, ["k2"])                                            # one is handled, one still waits
    assert face(desk) == "needs"
    desk.dev(both, [])                                                # nothing waits: the stone is still
    assert not desk.prop("needsYou") and face(desk) == "rest"
    assert desk.pill.property("needsYou") is False


def test_the_stone_knocks_only_for_a_row_the_desk_could_draw(desk):
    desk.dev([session("k1")], ["gone"])                               # a key for a session the table lacks
    assert desk.needs["rows"] == [] and face(desk) == "rest"
    desk.dev([session("k1")], ["gone", "k1", "k1"])                   # a key twice is one row
    assert len(desk.needs["rows"]) == 1 and face(desk) == "needs"


def test_the_mark_outlives_everything_that_puts_a_card_away(laptop):
    laptop.dev([session("k1"), session("k2")], ["k1", "k2"])
    assert laptop.faces["needs"] == "full" and face(laptop) == "needs"
    laptop.call("fold")                                               # "desk" folds every card to its strip...
    laptop.send(**desk_msg(folded=True))
    assert laptop.faces["needs"] == "strip" and face(laptop) == "needs"
    laptop.send(**desk_msg(folded=False))
    laptop.cover((0, 0, 1280, 656))                                   # ...a window covers the rail...
    laptop.pump(0.3)
    assert laptop.faces["needs"] == "strip" and face(laptop) == "needs"
    laptop.cover((0, 0, 1280, 720, True))                             # ...a full-screen window hides the desk
    assert laptop.prop("capsule") and laptop.faces["needs"] == "hidden" and face(laptop) == "needs"
    laptop.cover()
    laptop.send(**desk_msg(hidden=["needs"]))                         # (Needs you cannot be put away at all)
    assert laptop.faces["needs"] != "hidden" and face(laptop) == "needs"


def test_the_stone_knocks_over_a_running_turn_and_the_turn_goes_on_after(desk):
    desk.turn(1, "install ffmpeg", steps=TWO)
    assert face(desk) == "working"
    desk.dev([session("k1")], ["k1"])
    assert face(desk) == "needs"                                      # the person comes first
    desk.dev([session("k1")], [])
    assert face(desk) == "working"
    desk.end(1)
    assert face(desk) in ("done", "rest")


def test_a_dropped_socket_clears_the_mark_the_desk_set(desk):
    desk.dev([session("k1")], ["k1"])
    assert face(desk) == "needs"
    desk.call("lost")
    assert not desk.prop("needsYou") and desk.pill.property("needsYou") is False
    desk.dev([session("k1")], ["k1"])                                 # agentd sends the table again on reconnect
    assert desk.pill.property("needsYou") is True


def test_the_desk_clearing_its_mark_leaves_setups_own_ask_alone(desk):
    desk.send(type="setup", state="signed_out", line="Claude signed you out.", tone="step",
              actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    assert face(desk) == "needs"
    desk.dev([session("k1")], ["k1"])
    desk.dev([session("k1")], [])
    assert face(desk) == "needs"                                      # signed out still asks
    desk.send(type="setup", state="ready", line="", tone="done", actions=[])
    assert face(desk) == "rest"


def test_the_rows_are_the_attention_keys_in_their_order(desk):
    sessions = [session("k1", role="reviewer", title="Bombadil"), session("k2", role="builder", title="Tracker"),
                session("k3", role="designer", title="Bombadil"), session("k4", role="idle", title="Tracker")]
    desk.dev(sessions, ["k3", "k1", "k2"])                            # k4 is not waiting: no row
    assert [r["key"] for r in desk.needs["rows"]] == ["k3", "k1", "k2"]
    desk.dev(sessions, ["k2", "gone", "k1", "k2"])                    # no session for "gone", k2 once
    assert [r["key"] for r in desk.needs["rows"]] == ["k2", "k1"]
    desk.dev(sessions, [])
    assert desk.needs["rows"] == [] and not desk.prop("present")["needs"]


def test_a_needs_row_says_who_where_and_what_they_asked(desk):
    desk.dev([session("k1", state="asked"),
              session("k2", role="builder", title="Tracker", state="done", last="the tests pass."),
              session("k3", role="", title="Notes", state="idle", last="")], ["k1", "k2", "k3"])
    assert desk.needs["title"] == "Needs you"
    assert desk.needs["why"] == "Tab walks these · one alone is just the line"
    assert desk.needs["rows"] == [
        {"key": "k1", "kind": "dot", "title": "reviewer on Bombadil",
         "sub": "apply the migration to the local database?", "meter": None, "meterText": "",
         "tone": "sessions", "pulse": True, "button": "Open", "remove": False},
        {"key": "k2", "kind": "dot", "title": "builder on Tracker", "sub": "the tests pass.", "meter": None,
         "meterText": "", "tone": "sessions", "pulse": False, "button": "Open", "remove": False},
        {"key": "k3", "kind": "dot", "title": "Notes", "sub": "", "meter": None, "meterText": "",
         "tone": "sessions", "pulse": False, "button": "Open", "remove": False},
    ]


def test_the_title_uses_the_project_title_else_the_project_else_just_the_role(desk):
    desk.dev([session("k1", title="Bombadil", project="bombadil"), session("k2", title="", project="tracker"),
              session("k3", role="", title="", project="notes"), session("k4", role="ops", title="", project=""),
              session("k5", role="", title="", project="")], ["k1", "k2", "k3", "k4", "k5"])
    assert [r["title"] for r in desk.needs["rows"]] == [
        "reviewer on Bombadil", "reviewer on tracker", "notes", "ops", "k5"]


def test_the_sub_is_the_first_line_of_what_the_session_said_on_one_line_of_at_most_70(desk):
    long = "apply the migration to the local database and then restart the service so that it is picked up"
    lines = [("k1", long), ("k2", "x" * 70), ("k3", "x" * 71), ("k4", "first line\nthe second line"),
             ("k5", "\n\n  \nafter blanks\nmore"), ("k6", "a\tb   c"), ("k7", "")]
    desk.dev([session(k, last=last) for k, last in lines], [k for k, _ in lines])
    sub = {r["key"]: r["sub"] for r in desk.needs["rows"]}
    assert len(sub["k1"]) <= 70 and sub["k1"].endswith("…") and long.startswith(sub["k1"][:-1])
    assert len(sub["k1"]) >= 68
    assert sub["k2"] == "x" * 70
    assert sub["k3"] == "x" * 69 + "…" and len(sub["k3"]) == 70
    assert sub["k4"] == "first line" and sub["k5"] == "after blanks" and sub["k6"] == "a b c" and sub["k7"] == ""


def test_only_a_session_that_asked_pulses(desk):
    desk.dev([session(k, state=st) for k, st in (("a", "asked"), ("b", "done"), ("c", "failed"), ("d", "idle"),
                                                 ("e", "working"))], list("abcde"))
    assert [r["pulse"] for r in desk.needs["rows"]] == [True, False, False, False, False]
    assert {r["tone"] for r in desk.needs["rows"]} == {"sessions"}


def test_the_needs_strip_is_how_many_need_you(desk):
    everyone = [session("k1"), session("k2"), session("k3")]
    desk.dev(everyone, ["k1", "k2", "k3"])
    desk.send(**desk_msg(folded=True))
    assert desk.prop("rightStrips")[0]["text"] == "3 need you" and desk.prop("rightStrips")[0]["outlined"]
    desk.pump(0.3)
    assert desk.item("deskStrip-needs").property("text") == "3 need you"
    desk.dev(everyone, ["k1", "k2"])
    assert desk.prop("rightStrips")[0]["text"] == "2 need you"
    desk.dev(everyone, ["k1"])
    assert desk.prop("rightStrips") == []                             # one is the line in the pill, not a chip


def test_open_sends_the_session_to_agentd_and_nothing_else(desk):
    desk.dev([session("k1"), session("k2", role="builder", title="Tracker")], ["k1", "k2"])
    desk.pump(0.4)
    desk.click(desk.inside(desk.card_row("k2"), "rowsButton"))
    desk.click(desk.inside(desk.card_row("k1"), "rowsButton"))
    assert desk.sent == [{"type": "dev", "action": "open", "key": "k2"},
                         {"type": "dev", "action": "open", "key": "k1"}]
    assert [r["key"] for r in desk.needs["rows"]] == ["k1", "k2"]      # agentd's next message says what changed
    assert plain(desk.win.property("pillSent")) == []
    assert plain(desk.win.property("actions")) == [["needs", "k2", "Open"], ["needs", "k1", "Open"]]


def test_a_needs_row_has_no_x_and_a_meter_row_no_button(desk):
    desk.dev([session("k1"), session("k2")], ["k1", "k2"])
    desk.jobs(ISO, UPDATE)
    desk.clock(10)
    desk.pump(0.4)
    assert desk.inside(desk.card_row("k1"), "rowsRemove") is None
    assert desk.inside(desk.card_row("a1b2"), "rowsButton") is None


def test_a_dev_message_keeps_what_it_leaves_out_and_drops_what_it_gets_wrong(desk):
    desk.dev([session("k1"), session("k2")], ["k2", "k1"])
    desk.send(type="dev", attention=["k1"])                           # no sessions: the last ones stay
    assert [r["key"] for r in desk.needs["rows"]] == ["k1"]
    desk.send(type="dev", sessions=[session("k1", last="new words"), session("k2")])   # no attention: it stays
    assert [r["key"] for r in desk.needs["rows"]] == ["k1"] and desk.needs["rows"][0]["sub"] == "new words"
    desk.send(type="dev", sessions="everything", attention="everyone")
    assert [r["key"] for r in desk.needs["rows"]] == ["k1"]
    desk.send(type="dev", sessions=[None, 5, "text", {"role": "no key"}, session("k9")],
              attention=[None, "k9", 3, "k1"])
    assert [r["key"] for r in desk.needs["rows"]] == ["k9"]           # k1 has no session now
    desk.send(type="dev")
    assert [r["key"] for r in desk.needs["rows"]] == ["k9"]
    assert desk.warnings == []


def test_the_rows_follow_the_sessions_as_they_change(desk):
    desk.dev([session("k1"), session("k2", state="working", last="thinking")], ["k1", "k2"])
    desk.pump(0.4)
    desk.card_row("k2").setProperty("marker", 5)
    desk.dev([session("k1"), session("k2", state="asked", last="may I delete the cache?")], ["k1", "k2"])
    assert desk.card_row("k2").property("marker") == 5                # the row is kept, its words change
    assert desk.inside(desk.card_row("k2"), "rowsRowSub").property("text") == "may I delete the cache?"
    assert desk.needs["rows"][1]["pulse"]


def test_the_socket_going_takes_both_tables_away_and_a_reconnect_brings_them_back(desk):
    desk.set("connected", True)
    desk.jobs(ISO, UPDATE)
    desk.clock(10)
    desk.dev([session("k1"), session("k2")], ["k1", "k2"])
    desk.call("_removeJob", "a1b2")
    assert desk.prop("present")["watching"] and desk.prop("present")["needs"]
    desk.call("lost")
    assert desk.watch["rows"] == [] and desk.needs["rows"] == [] and desk.watch["why"] == ""
    assert not desk.prop("present")["watching"] and not desk.prop("present")["needs"]
    assert desk.prop("jobs") == [] and desk.prop("sessions") == [] and desk.prop("attention") == []
    assert not desk.prop("ticking")
    desk.call("lost")                                                 # twice is the same
    assert desk.watch["rows"] == []
    # What the person had removed is forgotten too: the new table is agentd's word.
    desk.set("connected", True)
    desk.jobs(ISO)
    desk.clock(10)
    assert [r["key"] for r in desk.watch["rows"]] == ["a1b2"]
    desk.dev([session("k1"), session("k2")], ["k1", "k2"])
    assert desk.prop("present")["needs"]


def test_the_cards_heights_are_what_the_rail_draws_for_jobs_and_sessions(desk):
    copy = job("d1", "Copy", state="done", ended=T0 + 200)
    desk.jobs(ISO, BUILD, TIMER, UPDATE, job("m2", "Copying the photos", pct=7.0), copy)
    desk.clock(205)
    desk.dev([session("k1"), session("k2"), session("k3")], ["k1", "k2", "k3"])
    desk.pump(0.4)
    assert sorted(c.property("implicitHeight") for c in desk.items("rowsCard")) == sorted(
        [desk.slots["watching"]["h"], desk.slots["needs"]["h"]])
    assert desk.slots["watching"]["h"] == 50 + 2 * 34 + 4 * 44 + 18   # two removable meters, four dot rows
    assert desk.slots["needs"]["h"] == 50 + 3 * 44 + 18


def test_a_laptop_folds_the_jobs_card_by_the_height_rule_and_its_strip_reads_the_meter(laptop):
    laptop.turn(1, steps=FOUR)
    failures = [job(f"x{i}", "Copy", state="failed", last="no space left") for i in range(8)]
    laptop.jobs(ISO, *failures)
    laptop.clock(30)
    laptop.pump(0.4)
    assert laptop.faces["now"] == "full" and laptop.faces["watching"] == "strip"     # 172 + 12 + 454 > 600
    assert laptop.prop("leftStrips")[0]["text"] == "Ubuntu 43%"
    laptop.snap("laptop-watching")


# -- Machine: agentd's vitals --

def test_a_machine_message_puts_the_card_on_the_right_rail_with_its_rows(desk):
    assert desk.machine is None and not desk.prop("present")["machine"] and desk.faces["machine"] == "hidden"
    desk.vitals()
    assert desk.machine == {
        "title": "Machine", "why": WHY, "rows": [shown(r) for r in (MEMORY, DISK, CPU, NET)],
        "strip": {"text": "memory 91%", "dot": THEME["warn"], "ring": False, "outlined": False}}
    assert desk.prop("present")["machine"] and desk.faces["machine"] == "full"
    # The right rail's, the passenger's: bottom up it is the first, nearest the pill.
    assert desk.slots["machine"] == {"side": "right", "x": 1920 - 316, "y": 1000 - 214, "w": 300, "h": 214}
    desk.pump(0.4)
    card = desk.item("deskCard-machine")
    assert desk.at(card) == (1920 - 316, 1000 - 214) and desk.shown("deskCard-machine")
    assert desk.inside(card, "rowsTitle").property("text") == "Machine"
    assert desk.inside(card, "rowsWhy").property("text") == WHY
    by_place = sorted(desk.items("rowsRow"), key=lambda r: desk.at(r)[1])
    assert [r.property("key") for r in by_place] == ["memory", "disk", "cpu", "net"]
    assert [r.property("kind") for r in by_place] == ["stack", "meter", "meter", "plain"]
    assert [desk.inside(r, "rowsRowTitle").property("text") for r in by_place] == ["Memory", "Disk", "Processor", "Network"]
    assert [desk.inside(r, "rowsMeta").property("text") for r in by_place[:3]] == [
        "14.5 of 16 GB", "214 of 230 GB", "37% busy · 62°"]
    assert [desk.inside(r, "rowsPercent").property("text") for r in by_place[:3]] == ["91%", "93%", "37%"]
    assert desk.inside(by_place[3], "rowsRowSub").property("text") == "↓ 1.2 MB/s   ↑ 40 kB/s"
    assert desk.inside(by_place[3], "rowsMeter") is None and desk.inside(by_place[3], "rowsDot") is None
    # Nothing here ticks: the card draws what agentd last said until it says something else.
    assert not desk.prop("ticking") and not desk.prop("clockRunning")
    desk.snap("machine")


def test_a_calm_message_takes_the_card_away_and_the_next_crossing_brings_it_back(desk):
    desk.vitals()
    desk.pump(0.4)
    assert desk.shown("deskCard-machine")
    desk.send(type="machine", present=False, asked=False, why="", strip={"text": "", "dot": ""}, rows=[])
    assert desk.machine is None and not desk.prop("present")["machine"]
    assert desk.faces["machine"] == "hidden" and "machine" not in desk.slots
    desk.pump(0.4)
    assert not desk.shown("deskCard-machine") and desk.prop("rightStrips") == []
    assert plain(desk.item("railRight").property("hitRects")) == []
    desk.vitals()
    assert desk.prop("present")["machine"] and desk.faces["machine"] == "full"
    # Not present wins over the rows it still lists, and present with nothing to draw is no card.
    desk.vitals(present=False)
    assert desk.machine is None
    desk.vitals()
    desk.vitals(rows=[])
    assert desk.machine is None and not desk.prop("present")["machine"]
    desk.vitals()
    desk.send(type="machine")                                  # a message that says nothing says no card
    assert desk.machine is None


def test_the_card_folds_away_with_what_it_last_said_and_comes_back_with_what_it_says_then(desk):
    desk.vitals()
    desk.pump(0.4)
    desk.set("foldMs", 400)
    desk.send(type="machine", present=False, rows=[])
    desk.pump(0.1)
    card = desk.item("deskCard-machine")
    assert 0 < card.property("opacity") < 1 and card.isVisible()          # on its way out
    assert desk.inside(card, "rowsTitle").property("text") == "Machine"
    assert sorted(r.property("key") for r in desk.items("rowsRow")) == ["cpu", "disk", "memory", "net"]
    desk.pump(0.6)
    assert not card.isVisible()
    desk.vitals(MEMORY, DISK)
    desk.pump(0.6)
    assert card.isVisible() and sorted(r.property("key") for r in desk.items("rowsRow")) == ["disk", "memory"]


def test_asked_for_is_drawn_the_same_as_crossed(desk):
    desk.vitals(asked=True)
    asked = desk.machine
    desk.vitals(asked=False)
    assert desk.machine == asked and desk.prop("present")["machine"]


def test_the_memory_stack_is_cut_by_who_and_a_line_crossed_says_so_in_amber(desk):
    desk.vitals()
    desk.pump(0.4)
    memory, disk, cpu = (desk.card_row(k) for k in ("memory", "disk", "cpu"))
    track = desk.inside(memory, "rowsMeter")
    cut = desk.parts(memory)
    # Orange the machine's turn, blue the coding sessions, white you, from the track's left edge.
    assert [c for _, _, c in cut] == [THEME["accent"], THEME["info"], THEME["fg"]]
    assert [w for _, w, _ in cut] == pytest.approx([0.12 * 272, 0.40 * 272, 0.39 * 272], abs=0.01)
    assert [x for x, _, _ in cut] == pytest.approx([0, 0.12 * 272, 0.52 * 272], abs=0.01)
    assert all(x + w <= track.width() for x, w, _ in cut) and track.width() == 272
    assert desk.inside(memory, "rowsMeter").property("fillWidth") == 0
    # The disk is a plain meter in the tone it says; the processor is white, as it said.
    assert desk.inside(disk, "rowsMeter").property("fillColor").name() == THEME["warn"]
    assert desk.inside(disk, "rowsMeter").property("fillWidth") == pytest.approx(0.93 * 272, abs=0.01)
    assert desk.inside(cpu, "rowsMeter").property("fillColor").name() == THEME["fg"]
    # Amber is a line crossed: its percent says so; the one under its line is quiet.
    assert [desk.inside(r, "rowsPercent").property("color").name() for r in (memory, disk, cpu)] == [
        THEME["warn"], THEME["warn"], THEME["muted"]]
    desk.vitals(dict(DISK, meter=0.98, tone="red"), CPU)
    assert desk.inside(desk.card_row("disk"), "rowsMeter").property("fillColor").name() == THEME["bad"]
    assert desk.inside(desk.card_row("disk"), "rowsPercent").property("color").name() == THEME["bad"]
    desk.snap("machine-stack")


def test_parts_that_add_up_to_more_than_the_track_never_overflow_it(desk):
    over = dict(MEMORY, parts=[part("machine", 0.7), part("sessions", 0.6), part("you", 0.5)])
    desk.vitals(over, DISK)
    cleaned = desk.machine["rows"][0]["parts"]
    assert [p["tone"] for p in cleaned] == ["machine", "sessions"]       # the third has no room at all
    assert [p["fraction"] for p in cleaned] == pytest.approx([0.7, 0.3], abs=1e-9)
    assert sum(p["fraction"] for p in cleaned) <= 1 + 1e-9
    desk.pump(0.4)
    cut = desk.parts(desk.card_row("memory"))
    assert [c for _, _, c in cut] == [THEME["accent"], THEME["info"]]
    assert cut[-1][0] + cut[-1][1] == pytest.approx(272, abs=0.01)
    # A piece under a pixel is not drawn; the next starts where it would have.
    desk.vitals(dict(MEMORY, parts=[part("machine", 0.5), part("sessions", 0.003), part("you", 0.2)]), DISK)
    cut = desk.parts(desk.card_row("memory"))
    assert [c for _, _, c in cut] == [THEME["accent"], THEME["fg"]]
    assert cut[1][0] == pytest.approx(0.503 * 272, abs=0.01)


def test_a_tap_on_the_disk_row_asks_agentd_to_open_it_and_nothing_else(desk):
    desk.set("connected", True)
    hello = list(desk.sent)
    assert hello == [{"type": "desk", "op": "get"}]
    desk.vitals()
    desk.pump(0.4)
    desk.click(desk.card_row("disk"))
    assert desk.sent == hello + [{"type": "vitals", "op": "open", "row": "disk"}]
    assert plain(desk.win.property("opened")) == [["machine", "disk", "disk"]]
    # A row that opens nothing is just a row: memory, the processor and the network send nothing.
    for key in ("memory", "cpu", "net"):
        desk.click(desk.card_row(key))
        desk.click(desk.inside(desk.card_row(key), "rowsRowTitle"))
    assert desk.sent == hello + [{"type": "vitals", "op": "open", "row": "disk"}]
    assert plain(desk.win.property("opened")) == [["machine", "disk", "disk"]]
    # It never reaches the model: the pill heard nothing, and no turn began.
    assert plain(desk.win.property("pillSent")) == [] and desk.prop("_phase") == "idle"
    desk.click(desk.inside(desk.card_row("disk"), "rowsMeta"))
    desk.click(desk.inside(desk.card_row("disk"), "rowsMeter"))
    assert [m for m in desk.sent[1:]] == [{"type": "vitals", "op": "open", "row": "disk"}] * 3
    assert plain(desk.win.property("pillSent")) == []


def test_only_machines_opens_are_a_message(desk):
    def opens(widget, key, what):
        QtCore.QMetaObject.invokeMethod(desk.desk, "rowOpen", QtCore.Qt.DirectConnection,
                                        QtCore.Q_ARG("QString", widget), QtCore.Q_ARG("QString", key),
                                        QtCore.Q_ARG("QString", what))
        desk.pump()
    for widget, key, what in (("watching", "disk", "disk"), ("needs", "disk", "disk"), ("away", "x", "y"),
                              ("alive", "x", "y"), ("machine", "disk", ""), ("", "disk", "disk")):
        opens(widget, key, what)
    assert desk.sent == []                                  # not the machine's, or nothing to open
    opens("machine", "disk", "disk")
    assert desk.sent == [{"type": "vitals", "op": "open", "row": "disk"}]
    opens("machine", "x", "elsewhere")                      # what a row says it opens is agentd's to judge
    assert desk.sent[1] == {"type": "vitals", "op": "open", "row": "elsewhere"} and len(desk.sent) == 2


def test_the_other_cards_rows_open_nothing(desk):
    desk.vitals()
    desk.jobs(ISO, UPDATE)
    desk.clock(10)
    desk.dev([session("k1"), session("k2")], ["k1", "k2"])
    desk.pump(0.4)
    for key in ("a1b2", "f7a8", "k1", "k2"):
        desk.click(desk.card_row(key))
        desk.click(desk.inside(desk.card_row(key), "rowsRowTitle"))
    desk.click(desk.inside(desk.card_row("f7a8"), "rowsButton"))          # Why? on Watching
    assert desk.sent == [{"type": "jobs", "op": "why", "id": "f7a8"}]
    assert plain(desk.win.property("opened")) == []


def test_the_strip_says_the_line_crossed_when_the_card_has_no_room(laptop):
    laptop.set("needsModel", rows(3))
    laptop.set("awayModel", rows(3))
    laptop.vitals()
    assert laptop.faces["machine"] == "strip" and "machine" in laptop.slots
    laptop.pump(0.3)
    (strip,) = laptop.prop("rightStrips")
    assert (strip["id"], strip["text"], strip["dot"], strip["ring"], strip["outlined"]) == (
        "machine", "memory 91%", THEME["warn"], False, False)
    chip = laptop.item("deskStrip-machine")
    assert chip.property("text") == "memory 91%" and chip.property("dot") == THEME["warn"]
    assert not chip.property("ring") and not laptop.shown("deskCard-machine")
    laptop.snap("laptop-machine-strip")
    # The words change in place: the chip is the same one, and its neighbours do not blink.
    laptop.vitals(strip={"text": "memory 93%", "dot": "red"})
    laptop.pump(0.05)
    assert laptop.item("deskStrip-machine") is chip and chip.property("opacity") == 1
    assert chip.property("text") == "memory 93%" and chip.property("dot") == THEME["bad"]
    laptop.vitals(strip={"text": "hot · 82°", "dot": ""})
    assert chip.property("text") == "hot · 82°" and chip.property("dot") == ""
    laptop.vitals(strip={"text": "disk 94%", "dot": "mauve"})                 # a colour it does not know is none
    assert chip.property("dot") == "" and laptop.machine["strip"]["dot"] == ""
    laptop.vitals(strip={"text": "memory 91%", "dot": "amber"})
    assert chip.property("dot") == THEME["warn"]
    # With room again the card is the face and the chip goes.
    laptop.set("needsModel", rows(2))
    laptop.pump(0.3)
    assert laptop.faces["machine"] == "full" and laptop.prop("rightStrips") == []


def test_the_strip_when_the_desk_folds_or_the_screen_is_narrow(desk):
    desk.vitals()
    desk.send(**desk_msg(folded=True))
    assert [s["text"] for s in desk.prop("rightStrips")] == ["memory 91%"]
    desk.pump(0.3)
    assert desk.item("deskStrip-machine").property("dot") == THEME["warn"]
    desk.snap("machine-strip")
    desk.send(**desk_msg(folded=False))
    assert desk.faces["machine"] == "full"
    desk.resize(1000, 720)
    assert desk.prop("narrow") and desk.faces["machine"] == "strip"
    assert [s["text"] for s in desk.prop("rightStrips")] == ["memory 91%"]


def test_what_vitals_says_is_what_the_desk_draws(desk):
    """Both ends of the machine message joined: the real Vitals on one side, the real shell on the other."""
    from test_vitals import GB, rig, rise, run
    v, fake, clock = rig()
    msg = rise(v, fake, clock, memory=0.91, disk=0.93, heat=82, held=8 * GB, machine_=2 * GB, net=(1_200_000, 40_000))
    assert msg["present"] and msg["strip"] == {"text": "memory 91%", "dot": "amber"}
    desk.send(**json.loads(json.dumps(msg)))
    desk.pump(0.3)
    assert desk.faces["machine"] == "full" and desk.slots["machine"]["side"] == "right"
    model = desk.machine
    assert model["why"] == msg["why"] and model["strip"]["text"] == msg["strip"]["text"]
    assert [r["key"] for r in model["rows"]] == [r["key"] for r in msg["rows"]] == ["memory", "disk", "cpu", "net"]
    assert [r["kind"] for r in model["rows"]] == ["stack", "meter", "meter", "plain"]
    memory = model["rows"][0]
    assert [p["tone"] for p in memory["parts"]] == [p["tone"] for p in msg["rows"][0]["parts"]]
    assert memory["tone"] == "amber" and memory["meterText"] == msg["rows"][0]["meterText"]
    assert model["rows"][1]["opens"] == "disk" and model["rows"][3]["sub"] == msg["rows"][3]["sub"]
    assert desk.machine["rows"][1]["meter"] == msg["rows"][1]["meter"]
    # And when the machine is calm again, the card goes.
    gone = [m for m in run(v, fake, clock, 12, memory=0.30, disk=0.40, heat=50) if m][-1]
    assert not gone["present"]
    desk.send(**json.loads(json.dumps(gone)))
    assert desk.machine is None and desk.faces["machine"] == "hidden"


def test_a_message_with_no_strip_leaves_the_chip_to_say_machine(desk):
    desk.send(**machine_msg(strip=None))
    assert "strip" not in desk.machine
    desk.send(**machine_msg(strip={"text": 5, "dot": "amber"}))
    assert "strip" not in desk.machine
    desk.send(**machine_msg(strip={"dot": "amber"}))
    assert "strip" not in desk.machine
    desk.send(**desk_msg(folded=True))
    assert [(s["text"], s["dot"]) for s in desk.prop("rightStrips")] == [("machine", "")]
    desk.send(**machine_msg(strip={"text": "disk 94%"}))
    assert desk.machine["strip"] == {"text": "disk 94%", "dot": "", "ring": False, "outlined": False}
    assert [(s["text"], s["dot"]) for s in desk.prop("rightStrips")] == [("disk 94%", "")]


def test_hide_machine_puts_the_card_away_even_while_the_message_says_present(desk):
    desk.vitals()
    desk.pump(0.3)
    assert desk.faces["machine"] == "full"
    desk.send(**desk_msg(hidden=["machine"]))
    assert desk.faces["machine"] == "hidden" and "machine" not in desk.slots
    assert not desk.prop("present")["machine"] and desk.prop("rightStrips") == []
    assert desk.machine is not None                           # still reading: it only has no face
    desk.pump(0.4)
    assert not desk.shown("deskCard-machine") and not desk.shown("deskStrip-machine")
    desk.vitals(why="Memory is nearly full")                  # a message every second changes nothing of that
    assert desk.faces["machine"] == "hidden" and desk.machine["why"] == "Memory is nearly full"
    desk.send(**desk_msg(hidden=[]))
    assert desk.faces["machine"] == "full" and desk.slots["machine"]["h"] == 214
    desk.pump(0.4)
    assert desk.shown("deskCard-machine")


def test_a_window_over_the_machine_card_folds_it_to_its_strip(laptop):
    laptop.vitals()
    slot = laptop.slots["machine"]
    laptop.cover((slot["x"] + 20, slot["y"] + 20, 100, 100))
    assert laptop.faces["machine"] == "strip"
    assert [s["text"] for s in laptop.prop("rightStrips")] == ["memory 91%"]


def test_the_socket_going_takes_the_machine_card_away_and_a_reconnect_brings_it_back(desk):
    desk.set("connected", True)
    desk.vitals()
    desk.pump(0.4)
    assert desk.prop("present")["machine"] and desk.shown("deskCard-machine")
    desk.call("lost")
    assert desk.machine is None and not desk.prop("present")["machine"]
    assert desk.faces["machine"] == "hidden" and "machine" not in desk.slots and desk.prop("rightStrips") == []
    desk.pump(0.4)
    assert not desk.shown("deskCard-machine")
    desk.call("lost")                                         # twice is the same
    assert desk.machine is None
    desk.set("connected", True)                               # agentd sends the desk and the card again
    desk.vitals()
    assert desk.prop("present")["machine"]
    desk.call("lost")
    desk.set("machineModel", rows(1))                         # a model set by hand goes too: nothing can answer it
    desk.call("lost")
    assert desk.machine is None


def test_a_machine_message_that_is_odd_is_cleaned(desk):
    odd = [None, 5, "text", [], {"kind": "meter", "title": "no key"}, {"key": "", "kind": "meter"},
           {"key": 7, "kind": "meter"}, {"key": "dot", "kind": "dot", "title": "a kind the card does not have"},
           {"key": "gauge", "kind": "gauge"}, {"key": "none", "kind": None}, {"key": "bare"},
           {"key": "a", "kind": "meter", "title": 5, "meterText": ["x"], "meter": 1.7, "tone": "purple", "opens": 5},
           {"key": "b", "kind": "meter", "title": "b", "meter": -3, "tone": 4, "pulse": True, "button": "Do it",
            "remove": True},
           {"key": "c", "kind": "meter", "meter": "lots"}, {"key": "d", "kind": "meter"},
           {"key": "a", "kind": "meter", "title": "the same key twice"},
           {"key": "e", "kind": "stack", "meter": 0.5, "parts": "most of it"},
           {"key": "f", "kind": "stack", "meter": 0.5,
            "parts": [None, 5, {"tone": "you"}, {"tone": "machine", "fraction": "lots"}, {"tone": "sessions", "fraction": -1},
                      {"tone": "nope", "fraction": 0.6}, {"tone": "machine", "fraction": 0.6},
                      {"tone": 3, "fraction": 2}]},
           {"key": "constructor", "kind": "plain", "title": "g", "sub": 3},
           {"key": "h", "kind": "plain", "title": "past the sixth"}]
    desk.send(**machine_msg(*odd, why=5, strip="chip"))
    m = desk.machine
    assert m["title"] == "Machine" and m["why"] == "" and "strip" not in m
    assert [r["key"] for r in m["rows"]] == ["a", "b", "c", "d", "e", "f"]               # six at most
    a, b, c, d, e, f = m["rows"]
    assert a == shown({"key": "a", "kind": "meter", "meter": 1.0})                       # held to 0..1, words only as words
    assert b == shown({"key": "b", "kind": "meter", "title": "b", "meter": 0.0})        # nobody's button, no x, no pulse
    assert c["meter"] is None and d["meter"] is None
    assert e["kind"] == "stack" and e["parts"] == [] and e["meter"] == 0.5
    # No fraction, no number, nothing in it: not a piece. The rest are held to the track: 0.6, then 0.4.
    assert [(p["tone"], p["fraction"]) for p in f["parts"]] == [
        ("you", pytest.approx(0.6)), ("machine", pytest.approx(0.4))]
    assert all("parts" not in r for r in (a, b, c, d))
    assert desk.prop("present")["machine"] and desk.slots["machine"]["h"] == 50 + 6 * 34 + 18
    desk.send(**machine_msg({"key": "constructor", "kind": "plain", "title": "g", "sub": 3}, {"key": "toString", "kind": "plain"}))
    assert [r["key"] for r in desk.machine["rows"]] == ["constructor", "toString"]     # keys are only keys
    assert desk.machine["rows"][0]["sub"] == ""
    desk.pump(0.3)
    assert desk.warnings == []


def test_present_means_true_and_nothing_else(desk):
    for present in ("yes", 1, "true", None, [], {}):
        desk.vitals(present=present)
        assert desk.machine is None, present
    desk.vitals(present=True)
    assert desk.machine is not None
    for rows_ in ("all of them", 5, None, {"memory": MEMORY}, [None, 5]):
        desk.vitals(rows=rows_)
        assert desk.machine is None, rows_
    desk.vitals()
    desk.send(type="machine", present=True)
    assert desk.machine is None                               # no rows to draw is no card


def test_a_number_that_is_not_a_number_is_no_reading(desk):
    desk.send_numbers(**machine_msg(
        {"key": "a", "kind": "meter", "meter": "NaN!", "title": "nan"},
        {"key": "b", "kind": "meter", "meter": "Infinity!", "title": "infinity"},
        {"key": "c", "kind": "stack", "meter": 0.5, "parts": [part("machine", "NaN!"), part("you", "Infinity!"),
                                                                 part("sessions", 0.25)]}))
    a, b, c = desk.machine["rows"]
    assert a["meter"] is None                                 # no reading is no percent, not an empty meter
    assert b["meter"] is None
    assert [(p["tone"], p["fraction"]) for p in c["parts"]] == [("sessions", 0.25)]
    desk.pump(0.4)
    assert desk.inside(desk.card_row("a"), "rowsPercent") is None and desk.warnings == []
    assert desk.inside(desk.card_row("a"), "rowsMeter").property("fillWidth") == 0


def test_the_machines_slot_is_the_height_of_the_card_the_rail_draws(desk):
    plain_rows = [{"key": f"p{i}", "kind": "plain", "title": f"row {i}", "sub": "x"} for i in range(3)]
    for table, height in (((MEMORY, DISK, CPU, NET), 50 + 3 * 34 + 44 + 18),
                          ((MEMORY,), 50 + 34 + 18), ((NET,), 50 + 44 + 18), ((MEMORY, DISK, CPU), 50 + 3 * 34 + 18),
                          (tuple(plain_rows), 50 + 3 * 44 + 18),
                          ((MEMORY, DISK, CPU, *plain_rows), 50 + 3 * 34 + 3 * 44 + 18)):
        desk.vitals(*table)
        desk.pump(0.3)
        assert desk.slots["machine"]["h"] == height, table
        (card,) = desk.items("rowsCard")
        assert card.property("implicitHeight") == height == card.height(), table
    desk.vitals(MEMORY, DISK)
    assert desk.call("heightOf", "machine") == 50 + 2 * 34 + 18


def test_the_card_keeps_its_rows_by_place_so_a_message_a_second_builds_nothing_again(desk):
    desk.vitals()
    desk.pump(0.4)
    memory = desk.card_row("memory")
    memory.setProperty("marker", 1)
    track = desk.inside(memory, "rowsMeter")
    first = [c for c in track.childItems() if c.objectName() == "rowsPart"]
    for i, p in enumerate(sorted(first, key=lambda p: p.x())):
        p.setProperty("marker", i)
    card = desk.item("deskCard-machine")

    def table(second):
        return (dict(MEMORY, meter=0.91 + 0.01 * second, meterText=f"{14.5 + 0.1 * second:.1f} of 16 GB",
                     parts=[part("machine", 0.12 + 0.01 * second), part("sessions", 0.40), part("you", 0.39)]),
                dict(DISK, meter=0.93 + 0.001 * second), CPU, NET)

    for second in range(1, 5):
        desk.vitals(*table(second))
    assert desk.card_row("memory") is memory and memory.property("marker") == 1
    same = sorted([c for c in track.childItems() if c.objectName() == "rowsPart"], key=lambda p: p.x())
    assert [p.property("marker") for p in same] == [0, 1, 2]
    assert [w for _, w, _ in desk.parts(memory)] == pytest.approx([0.16 * 272, 0.40 * 272, 0.39 * 272], abs=0.01)
    assert desk.inside(memory, "rowsPercent").property("text") == "95%"
    assert desk.inside(memory, "rowsMeta").property("text") == "14.9 of 16 GB"
    assert desk.item("deskCard-machine") is card and desk.items("rowsCard")[0].property("washLevel") == 0
    # The same message again is no change at all; a different one is one.
    before = desk.win.property("machineChanges")
    desk.vitals(*table(4))
    assert desk.win.property("machineChanges") == before
    desk.vitals(*table(5))
    assert desk.win.property("machineChanges") == before + 1


def test_the_snapshot_says_what_the_machine_card_holds(desk):
    assert desk.call("snapshot")["machine"] is None
    desk.vitals()
    snap = desk.call("snapshot")
    assert snap["machine"]["title"] == "Machine" and [r["key"] for r in snap["machine"]["rows"]] == [
        "memory", "disk", "cpu", "net"]
    assert snap["present"]["machine"] and snap["faces"]["machine"] == "full"


def test_the_machine_loads_without_qml_warnings(desk):
    desk.set("connected", True)
    desk.vitals()
    desk.pump(0.4)
    desk.click(desk.card_row("disk"))
    desk.click(desk.card_row("memory"))
    desk.vitals(dict(MEMORY, meter=0.7, tone="you"), dict(DISK, meter=0.5, tone="you"), CPU, NET,
                strip={"text": "", "dot": ""}, why="")
    desk.vitals(MEMORY)
    desk.cover((1200, 400, 700, 600))
    desk.send(**desk_msg(folded=True))
    desk.cover((0, 0, 1920, 1080, True))
    desk.cover()
    desk.send(**desk_msg(folded=False, hidden=["machine"]))
    desk.send(**desk_msg(hidden=[]))
    desk.vitals(present=False, rows=[])
    desk.vitals()
    desk.call("lost")
    desk.pump(0.5)
    assert desk.warnings == []


# -- what the shell review found --

def test_a_screen_too_narrow_for_two_rails_and_a_pill_keeps_its_cards_as_strips(desk):
    assert desk.prop("narrowLimit") == 2 * (300 + 16 + 12) + 360 == 1016
    desk.resize(1000, 720)
    desk.turn(1, steps=TWO)
    assert desk.prop("narrow") is True and desk.faces["now"] == "strip"
    assert desk.prop("anyFull") is False and desk.prop("pillWidth") > 360 - 1
    assert [s["text"] for s in desk.prop("leftStrips")] == ["step 1 of 2"]
    desk.resize(1016, 720)
    assert desk.prop("narrow") is False and desk.faces["now"] == "full"


def test_the_pill_does_not_widen_for_the_moments_the_cards_take_to_come_back(laptop):
    laptop.turn(1, steps=TWO)
    laptop.cover((0, 400, 1280, 300))
    laptop.pump(0.3)
    assert laptop.faces["now"] == "strip" and laptop.prop("pillTarget") == 360
    laptop.cover()                                          # the window goes; the card waits its delay
    assert laptop.prop("mode") == "open" and laptop.faces["now"] == "strip"
    assert laptop.prop("pillTarget") == 1280 - 2 * (300 + 12 + 16)   # already the width it will keep
    laptop.pump(0.7)
    assert laptop.faces["now"] == "full" and laptop.prop("pillTarget") == 1280 - 2 * (300 + 12 + 16)


def test_a_strip_whose_text_changes_is_redrawn_in_place_and_its_neighbours_do_not_blink(desk):
    desk.turn(1, steps=TWO)
    desk.jobs(ISO)
    desk.clock(10)
    desk.send(**desk_msg(folded=True))
    desk.pump(0.5)
    watching, now = desk.item("deskStrip-watching"), desk.item("deskStrip-now")
    assert watching.property("text") == "Ubuntu 43%" and watching.property("opacity") == 1
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", pct=44.0, last="12 MB/s"))
    desk.clock(12)
    desk.pump(0.05)
    assert desk.item("deskStrip-watching") is watching and watching.property("text") == "Ubuntu 44%"
    assert desk.item("deskStrip-now") is now and now.property("opacity") == 1
    assert watching.property("opacity") == 1


def test_a_screen_the_desk_is_not_on_draws_no_strips(desk):
    desk.turn(1, steps=TWO)
    desk.send(**desk_msg(folded=True))
    desk.pump(0.4)
    assert desk.items("deskStrip-now") != [] and desk.items("deskStrip-now", other=True, visible_only=False) != []
    assert desk.items("deskStrip-now", other=True) == []       # its strips exist but are not drawn


def test_an_irreversible_step_is_red_with_its_words_even_when_no_step_is_marked_current(desk):
    desk.turn(1, steps=[("Format the disk", None, "pending"), ("Mount it", None, "pending")])
    desk.send(kind="status", turn=1, text="Formatting", source="step", risk="irreversible",
              command="sudo mkfs.ext4 /dev/sdb1")
    m = desk.now
    assert m["edge"] == "red" and m["command"] == "sudo mkfs.ext4 /dev/sdb1"
    assert m["caption"] == "can't be undone · Esc stops it"
    desk.end(1, stopped=True)
    desk.turn(2, steps=[("a", None, "completed"), ("b", None, "completed")])
    desk.send(kind="status", turn=2, text="Installing", source="step", risk="system", command="sudo pacman -S x")
    assert desk.now["edge"] == "amber" and desk.now["command"] == "sudo pacman -S x"


def test_a_long_plan_shows_the_steps_around_the_current_one_and_counts_all_of_them(desk):
    steps = [(f"step {i}", None, "completed" if i < 15 else "in_progress" if i == 15 else "pending")
             for i in range(24)]
    desk.turn(1, steps=steps)
    m = desk.now
    assert len(m["steps"]) == 12 and m["why"].startswith("Step 16 of 24")
    assert m["steps"][0]["label"] == "step 11" and m["steps"][4]["status"] == "in_progress"
    assert desk.slots["now"]["h"] == 68 + 26 * 12
    desk.plan(1, [(f"step {i}", None, "in_progress" if i == 0 else "pending") for i in range(24)])
    assert [s["label"] for s in desk.now["steps"]][:2] == ["step 0", "step 1"]      # the start, not before it
    desk.plan(1, [(f"step {i}", None, "completed") for i in range(24)])
    assert [s["label"] for s in desk.now["steps"]][-1] == "step 23"                   # the end
    desk.plan(1, [(f"step {i}", None, "completed" if i < 5 else "pending") for i in range(9)])
    assert len(desk.now["steps"]) == 9                                                # short enough: all of it


def test_the_ask_gives_way_to_asked_by_and_the_counts_to_esc_stops(desk):
    ask = "Install qemu-full so the VM tests can run on the laptop"
    desk.turn(1, ask, steps=TWO, asked_by="builder")
    title = desk.now["title"]
    assert title.endswith(" · asked by builder") and len(title) <= 40 and title.startswith("Install qemu")
    desk.end(1, stopped=True)
    desk.turn(2, "fix it", steps=FOUR)
    desk.send(kind="status", turn=2, text="Installing", source="step", risk=None, command=None,
              touched_text="3 packages and 12 files so far")
    assert desk.now["why"] == "Step 2 of 4 · Esc stops · 3 packages and 12 files so far"
    desk.send(kind="status", turn=2, text="Installing", source="step", risk=None, command=None,
              touched_text="1 package so far")
    assert desk.now["why"] == "Step 2 of 4 · 1 package so far · Esc stops"               # short: the brief's order


def test_a_row_the_person_removed_comes_back_if_the_job_is_still_listed_when_the_wait_ends(desk):
    desk.jobs(ISO, BUILD)
    desk.clock(10)
    desk.pump(0.4)
    desk.set("goneMs", 200)
    desk.click(desk.inside(desk.card_row("a1b2"), "rowsRemove"))
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4"]
    desk.jobs(ISO, BUILD)                                   # the stop did not work: still listed
    desk.clock(11)
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4"]
    desk.pump(0.4)
    assert [r["key"] for r in desk.watch["rows"]] == ["a1b2", "c3d4"]


def test_a_row_that_really_stopped_stays_gone(desk):
    desk.jobs(ISO, BUILD)
    desk.clock(10)
    desk.pump(0.4)
    desk.set("goneMs", 200)
    desk.click(desk.inside(desk.card_row("a1b2"), "rowsRemove"))
    desk.jobs(BUILD)
    desk.clock(11)
    desk.pump(0.5)
    assert [r["key"] for r in desk.watch["rows"]] == ["c3d4"]


def test_the_desk_loads_without_qml_warnings(desk):
    desk.set("connected", True)
    desk.turn(1, "install docker", steps=FOUR, asked_by="builder")
    desk.send(kind="status", turn=1, text="Installing docker", source="step", risk="irreversible", command="sudo x",
              touched_text="1 package so far")
    desk.set("needsModel", rows(2))
    desk.set("watchModel", rows(3, first={"kind": "meter"}))
    desk.cover((300, 300, 400, 400))
    desk.cover((0, 0, 700, 1000))
    desk.send(**desk_msg(folded=True))
    desk.cover((0, 0, 1920, 1080, True))
    desk.cover()
    desk.send(**desk_msg(folded=False, hidden=["now"]))
    desk.end(1)
    desk.pill_call("dismiss")
    desk.call("lost")
    desk.pump(0.5)
    assert desk.warnings == []


def test_the_jobs_and_sessions_load_without_qml_warnings(desk):
    desk.set("connected", True)
    desk.turn(1, "install docker", steps=FOUR)
    desk.jobs(ISO, BUILD, TIMER, UPDATE, job("d1", "Copy", state="done", ended=T0 + 4))
    desk.clock(5)
    desk.dev([session("k1"), session("k2", last="x" * 200), session("k3", role="", title="")], ["k1", "k2", "k3"])
    desk.pump(0.4)
    desk.click(desk.inside(desk.card_row("a1b2"), "rowsRemove"))
    desk.click(desk.inside(desk.card_row("f7a8"), "rowsButton"))
    desk.click(desk.inside(desk.card_row("k1"), "rowsButton"))
    desk.cover((0, 0, 700, 1000))
    desk.send(**desk_msg(folded=True))
    desk.clock(9)
    desk.jobs(BUILD, UPDATE, job("d1", "Copy", state="done", ended=T0 + 4))
    desk.clock(17)
    desk.send(**desk_msg(folded=False))
    desk.cover()
    desk.dev([session("k1")], ["k1"])
    desk.jobs()
    desk.end(1)
    desk.pill_call("dismiss")
    desk.call("lost")
    desk.pump(0.5)
    assert desk.warnings == []


# -- dragging: what DeskState decides (the pointer events are in test_desk_drag_qml.py) --

def sent_ops(d):
    """What the desk asked agentd to do, without the ask for its state."""
    return [m for m in d.sent if m["op"] != "get"]


def a_full_desk(d):
    """Cards on both rails of the 1920x1080 screen, and the desk connected. From the pill up, with each card's
    slot top: left now (828), watching (660), alive (480); right needs (844), away (676), machine (450)."""
    d.set("connected", True)
    d.turn(1, steps=FOUR)
    d.set("watchModel", rows(2))
    d.set("aliveModel", rows(2))
    d.set("needsModel", rows(2))
    d.set("awayModel", rows(2))
    d.vitals()
    assert set(d.faces.values()) == {"full"}
    return d.slots


def centre(slot):
    return slot["y"] + slot["h"] / 2


def below(slot):
    """The boundary under a card: the middle of the gap to the next one."""
    return slot["y"] + slot["h"] + 6


def target(d):
    return d.prop("dropTarget")


def take(d, widget, x, y, from_="rail"):
    """The person takes a widget at (x, y); what a drop there would do."""
    assert d.call("dragStart", widget, from_, x, y) is True
    return target(d)


def rank(side, n, mark_y):
    return {"kind": "rank", "side": side, "rank": n, "markY": mark_y}


def test_a_stripped_widget_is_a_strip_with_no_slot_and_the_cards_above_it_slide_down(desk):
    s = a_full_desk(desk)
    desk.send(**desk_msg(stripped=["watching"]))
    assert desk.prop("stripped") == ["watching"] and desk.call("isStripped", "watching") is True
    assert desk.faces["watching"] == "strip" and "watching" not in desk.slots
    assert desk.prop("present")["watching"] is True
    assert desk.slots["now"] == s["now"]                                             # the card under it stays
    assert desk.slots["alive"]["y"] == s["alive"]["y"] + s["watching"]["h"] + 12     # the one above slides down
    assert desk.faces["alive"] == "full"
    # Its chip stands in the rail's order, with its own words.
    assert [c["text"] for c in desk.prop("leftStrips")] == ["2 counting"]
    desk.pump(0.3)
    assert desk.shown("deskStrip-watching") and not desk.shown("deskCard-watching")
    desk.send(**desk_msg(stripped=[]))
    assert desk.faces["watching"] == "full" and desk.slots == s


def test_a_card_folded_by_hand_gives_its_room_to_the_one_that_did_not_fit(laptop):
    laptop.turn(1, steps=[(f"step {i}", None, "in_progress" if i == 0 else "pending") for i in range(8)])
    laptop.send(kind="status", turn=1, text="Installing", source="step", risk="system", command="sudo pacman -S x")
    laptop.set("watchModel", rows(3, first={"kind": "meter"}))
    laptop.set("aliveModel", rows(2))
    assert (laptop.faces["watching"], laptop.faces["alive"]) == ("full", "strip")
    laptop.send(**desk_msg(stripped=["watching"]))
    assert (laptop.faces["watching"], laptop.faces["alive"]) == ("strip", "full")


def test_stripped_is_filtered_to_known_widgets_and_left_alone_when_a_message_leaves_it_out(desk):
    desk.send(**desk_msg(stripped=["now", "now", "nonsense", 3, None, "needs"]))
    assert desk.prop("stripped") == ["now", "needs"]             # unknown ones are dropped; needs may be stripped
    desk.send(**desk_msg())                                      # says nothing about it
    assert desk.prop("stripped") == ["now", "needs"]
    desk.send(type="desk", stripped="everything")                # a shape it does not know
    assert desk.prop("stripped") == ["now", "needs"]
    desk.send(type="desk", stripped=["away"])
    assert desk.prop("stripped") == ["away"]


def test_needs_you_can_be_folded_by_hand_and_the_stone_still_knocks(desk):
    desk.set("needsModel", rows(2))
    desk.send(**desk_msg(stripped=["needs"]))
    assert desk.faces["needs"] == "strip" and "needs" not in desk.slots
    assert desk.prop("needsYou") is True and desk.pill.property("needsYou") is True
    assert [c["text"] for c in desk.prop("rightStrips")] == ["2 need you"]


def test_a_stripped_widget_with_nothing_to_say_is_not_there_and_comes_back_a_strip(desk):
    desk.send(**desk_msg(stripped=["watching"]))
    assert desk.faces["watching"] == "hidden"
    desk.set("watchModel", rows(2))
    assert desk.faces["watching"] == "strip"


def test_a_strip_by_hand_stays_one_when_the_desk_unfolds_and_when_a_window_leaves(laptop):
    laptop.turn(1, steps=TWO)
    laptop.send(**desk_msg(stripped=["now"], folded=True))
    laptop.send(**desk_msg(folded=False))
    laptop.cover((100, 300, 400, 300))
    laptop.cover()
    laptop.set("unfoldDelayMs", 60)
    laptop.pump(0.3)
    assert laptop.faces["now"] == "strip"


def test_a_drag_starts_only_where_it_can_be_carried_out(desk):
    a_full_desk(desk)
    assert desk.call("dragStart", "now", "rail", 100, 900) is True
    assert desk.prop("dragging") is True
    assert desk.prop("drag") == {"id": "now", "from": "rail", "side": "left", "x": 100, "y": 900}
    assert desk.call("dragStart", "needs", "rail", 1700, 900) is False       # one at a time
    assert desk.prop("drag")["id"] == "now"
    desk.call("dragCancel")
    assert desk.prop("drag") is None and desk.prop("dragging") is False
    assert desk.call("dragStart", "needs", "strip", 1700, 900) is False      # a card is no strip
    assert desk.call("dragStart", "needs", "chip", 1700, 900) is False       # nor anything else
    assert desk.call("dragStart", "nonsense", "rail", 0, 0) is False
    desk.set("watchModel", rows(0))
    assert desk.call("dragStart", "watching", "rail", 100, 700) is False     # nothing to say: not there
    assert desk.prop("drag") is None


def test_a_strip_is_taken_only_as_a_strip(desk):
    a_full_desk(desk)
    desk.send(**desk_msg(stripped=["alive"]))
    assert desk.call("dragStart", "alive", "rail", 100, 500) is False
    assert desk.call("dragStart", "alive", "strip", 700, 1040) is True
    assert desk.prop("drag")["from"] == "strip"


def test_nothing_starts_a_drag_while_agentd_is_away_or_a_window_has_the_screen(desk):
    desk.turn(1, steps=TWO)
    assert desk.call("dragStart", "now", "rail", 100, 900) is False          # not connected
    desk.set("connected", True)
    desk.cover((0, 0, 1920, 1080, True))
    assert desk.prop("mode") == "immersive"
    assert desk.call("dragStart", "now", "rail", 100, 900) is False
    desk.cover()
    desk.set("unfoldDelayMs", 60)
    desk.pump(0.3)
    assert desk.call("dragStart", "now", "rail", 100, 900) is True


def test_a_move_an_end_or_a_cancel_with_nothing_in_hand_does_nothing(desk):
    a_full_desk(desk)
    desk.call("dragMove", 100, 100)
    desk.call("dragEnd", 100, 100)
    desk.call("dragCancel")
    assert desk.prop("drag") is None and sent_ops(desk) == [] and desk.prop("settling") == ""


def test_a_card_dragged_up_and_down_its_own_rail_takes_the_boundary_nearest_the_pointer(desk):
    s = a_full_desk(desk)
    take(desk, "watching", 100, centre(s["watching"]))
    # Left alone, or between the middles of its neighbours: where it is, so there is nothing to do.
    assert target(desk) is None
    desk.call("dragMove", 100, centre(s["now"]) - 1)
    assert target(desk) is None
    desk.call("dragMove", 100, centre(s["alive"]) + 1)
    assert target(desk) is None
    # Past the middle of the card by the pill: in front of it.
    desk.call("dragMove", 100, centre(s["now"]) + 1)
    assert target(desk) == rank("left", 0, below(s["now"]))
    # Above the middle of the card above it: after it, the last place.
    desk.call("dragMove", 100, centre(s["alive"]) - 1)
    assert target(desk) == rank("left", 2, s["alive"]["y"] - 6)
    desk.call("dragMove", 100, 45)                                           # the top of the rail
    assert target(desk)["rank"] == 2


def test_the_cards_own_slot_is_not_a_place_to_drop_between(desk):
    s = a_full_desk(desk)
    assert take(desk, "now", 100, 900) is None                               # now is the first already
    desk.call("dragMove", 100, centre(s["watching"]) - 1)                    # between watching and alive
    assert target(desk) == rank("left", 1, below(s["alive"]))
    desk.call("dragEnd", 100, centre(s["watching"]) - 1)
    assert sent_ops(desk) == [{"type": "desk", "op": "move", "widget": "now", "rail": "left", "rank": 1}]


def test_a_card_dragged_to_the_other_rail_gets_the_rank_the_pointer_is_at(desk):
    s = a_full_desk(desk)
    assert take(desk, "now", 1700, centre(s["needs"]) + 10) == rank("right", 0, below(s["needs"]))
    desk.call("dragMove", 1700, centre(s["away"]) + 10)
    assert target(desk) == rank("right", 1, below(s["away"]))
    desk.call("dragMove", 1700, centre(s["machine"]) + 10)
    assert target(desk)["rank"] == 2
    desk.call("dragMove", 1700, centre(s["machine"]) - 10)                   # above all of them: the last place
    assert target(desk) == rank("right", 3, s["machine"]["y"] - 6)
    desk.call("dragEnd", 1700, centre(s["machine"]) - 10)
    assert sent_ops(desk) == [{"type": "desk", "op": "move", "widget": "now", "rail": "right", "rank": 3}]


def test_a_rail_with_no_card_takes_a_drop_at_the_pill_end(desk):
    a_full_desk(desk)
    desk.set("needsModel", rows(0))
    desk.set("awayModel", rows(0))
    desk.set("machineModel", rows(0))
    assert set(desk.slots) == {"now", "watching", "alive"}
    assert take(desk, "now", 1700, 300) == rank("right", 0, desk.prop("railBottomY"))
    desk.call("dragEnd", 1700, 300)
    assert sent_ops(desk) == [{"type": "desk", "op": "move", "widget": "now", "rail": "right", "rank": 0}]


def test_a_rank_counts_the_widgets_that_are_away_because_agentd_does(desk):
    a_full_desk(desk)
    desk.set("watchModel", rows(0))                      # watching has nothing to say: no slot, but still second
    s = desk.slots
    assert "watching" not in s and s["alive"]["y"] == s["now"]["y"] - 12 - 168
    # A card from the other rail, dropped between now and alive: after alive it is the fourth, and before
    # alive it is the third, because watching (the second) counts.
    assert take(desk, "needs", 100, centre(s["now"]) + 5) == rank("left", 0, below(s["now"]))
    desk.call("dragMove", 100, centre(s["now"]) - 5)
    assert target(desk) == rank("left", 2, below(s["alive"]))
    desk.call("dragMove", 100, centre(s["alive"]) - 5)
    assert target(desk) == rank("left", 3, s["alive"]["y"] - 6)
    # alive itself: watching is no place to drop at, so the nearest it comes to staying is the place before it.
    desk.call("dragCancel")
    take(desk, "alive", 100, centre(s["now"]) - 5)
    assert target(desk)["rank"] == 1
    desk.call("dragMove", 100, centre(s["now"]) + 5)
    assert target(desk)["rank"] == 0


def test_a_widget_folded_by_hand_is_not_a_place_to_drop_between_either(desk):
    a_full_desk(desk)
    desk.send(**desk_msg(stripped=["watching"]))
    s = desk.slots
    assert take(desk, "needs", 100, centre(s["alive"]) + 5) == rank("left", 2, below(s["alive"]))
    desk.call("dragMove", 100, centre(s["alive"]) - 5)
    assert target(desk) == rank("left", 3, s["alive"]["y"] - 6)
    desk.call("dragMove", 100, centre(s["now"]) + 5)
    assert target(desk)["rank"] == 0


def test_the_row_takes_a_card_to_fold_and_gives_a_strip_nothing(desk):
    a_full_desk(desk)
    h = desk.prop("screenHeight")
    assert take(desk, "now", 960, h - 64) == {"kind": "fold"}                # the bar's zone is the row
    desk.call("dragMove", 960, h - 65)
    assert target(desk) is None                                              # just above it, mid-screen: the stage
    desk.call("dragMove", 5, h - 1)
    assert target(desk) == {"kind": "fold"}                                  # whichever side
    desk.call("dragCancel")
    desk.send(**desk_msg(stripped=["now"]))
    assert take(desk, "now", 960, h - 30, "strip") is None                   # a strip is not folded again
    desk.call("dragMove", 100, 300)
    assert target(desk)["kind"] == "rank"


def test_only_the_rails_columns_are_places_and_the_middle_is_the_stage(desk):
    a_full_desk(desk)
    assert take(desk, "needs", 16 + 300 + 40, 700)["side"] == "left"         # the column and 40 px more
    desk.call("dragMove", 16 + 300 + 41, 700)
    assert target(desk) is None
    desk.call("dragMove", 1920 - 16 - 300 - 41, 700)
    assert target(desk) is None
    desk.call("dragMove", 1920 - 16 - 300 - 40, 700)
    assert target(desk)["side"] == "right"
    desk.call("dragMove", -30, 700)                                          # past the screen's edge is still the column
    assert target(desk)["side"] == "left"
    desk.call("dragMove", 1950, 700)
    assert target(desk)["side"] == "right"


def test_a_window_under_the_pointer_is_no_place(desk):
    a_full_desk(desk)
    desk.cover((1400, 60, 520, 300))                                         # above every card on the right
    assert take(desk, "now", 1700, 200) is None
    desk.call("dragMove", 1700, 359)
    assert target(desk) is None
    desk.call("dragMove", 1700, 360)                                         # the window's edge is not in it
    assert target(desk)["kind"] == "rank"
    desk.cover((0, 1016, 1920, 64))                                          # a window over the row
    desk.call("dragMove", 960, 1050)
    assert target(desk) is None


def test_a_rail_that_shifts_under_a_drag_moves_the_mark(desk):
    s = a_full_desk(desk)
    assert take(desk, "needs", 100, centre(s["alive"]) + 5) == rank("left", 2, below(s["alive"]))
    desk.set("watchModel", rows(4))                                          # watching grows: alive moves up
    moved = desk.slots["alive"]
    assert moved["y"] < s["alive"]["y"]
    assert target(desk) == rank("left", 2, below(moved))


def test_a_drop_with_no_target_sends_nothing_and_dims_nothing(desk):
    a_full_desk(desk)
    take(desk, "now", 960, 500)                                              # the stage
    desk.call("dragEnd", 960, 500)
    assert sent_ops(desk) == [] and desk.prop("drag") is None and desk.prop("settling") == ""
    take(desk, "now", 100, 900)                                              # where it is
    desk.call("dragEnd", 100, 900)
    assert sent_ops(desk) == [] and desk.prop("settling") == ""


def test_a_card_dropped_in_the_row_folds_and_a_strip_dropped_on_a_rail_is_moved(desk):
    a_full_desk(desk)
    take(desk, "machine", 960, 1050)
    desk.call("dragEnd", 960, 1050)
    assert sent_ops(desk) == [{"type": "desk", "op": "fold", "widget": "machine"}]
    desk.send(**desk_msg(stripped=["machine"]))                              # agentd's answer
    take(desk, "machine", 1700, 1050, "strip")
    desk.call("dragEnd", 100, 300)                                           # up and over to the left rail
    assert sent_ops(desk)[-1] == {"type": "desk", "op": "move", "widget": "machine", "rail": "left", "rank": 3}
    # Dropped on its own rail it still asks: agentd says whether it was already there.
    take(desk, "machine", 1700, 1050, "strip")
    desk.call("dragEnd", 1700, 300)
    assert sent_ops(desk)[-1] == {"type": "desk", "op": "move", "widget": "machine", "rail": "right", "rank": 2}


def test_a_strip_that_is_one_for_the_room_is_never_in_its_place(desk):
    a_full_desk(desk)
    desk.send(**desk_msg(folded=True))                                       # the word "desk": every card is a strip
    assert set(desk.faces.values()) == {"strip"}
    assert take(desk, "watching", 100, 800, "strip") == rank("left", 0, desk.prop("railBottomY"))
    desk.call("dragEnd", 100, 800)
    assert sent_ops(desk) == [{"type": "desk", "op": "move", "widget": "watching", "rail": "left", "rank": 0}]


def test_the_chip_a_card_would_fold_into_is_the_ghost_for_the_row(desk):
    a_full_desk(desk)
    assert desk.prop("foldChip") is None
    take(desk, "watching", 960, 300)
    assert desk.prop("foldChip") is None
    desk.call("dragMove", 960, 1050)
    chip = desk.prop("foldChip")
    assert chip["id"] == "watching" and chip["text"] == "2 counting"
    desk.call("dragMove", 960, 500)
    assert desk.prop("foldChip") is None


def test_desk_messages_wait_for_the_drop_and_apply_in_the_order_they_came(desk):
    s = a_full_desk(desk)
    take(desk, "needs", 100, 900)
    desk.send(**desk_msg(stripped=["watching"]))
    desk.send(**desk_msg(stripped=["alive"],
                         order={"left": ["alive", "now", "watching"], "right": ["needs", "away", "machine"]}))
    assert desk.prop("stripped") == [] and desk.prop("order")["left"] == ["now", "watching", "alive"]
    assert desk.slots == s                                                   # the rail stays as the person sees it
    # What is not the desk's own state goes on as usual.
    desk.set("watchModel", rows(3))
    assert desk.slots["watching"]["h"] == 50 + 3 * 44 + 18
    desk.call("dragCancel")
    assert desk.prop("stripped") == ["alive"] and desk.prop("order")["left"] == ["alive", "now", "watching"]
    assert sent_ops(desk) == []


def test_a_drop_applies_what_waited_and_then_sends_its_own_message(desk):
    a_full_desk(desk)
    take(desk, "now", 960, 1050)
    desk.send(**desk_msg(folded=True))
    assert desk.prop("folded") is False
    desk.call("dragEnd", 960, 1050)
    assert desk.prop("folded") is True and desk.prop("dragging") is False
    assert sent_ops(desk) == [{"type": "desk", "op": "fold", "widget": "now"}]


def test_a_drop_dims_the_origin_until_agentd_answers_or_the_wait_runs_out(desk):
    a_full_desk(desk)
    desk.set("settleMs", 150)
    take(desk, "now", 960, 1050)
    assert desk.prop("settling") == ""                                       # in hand is not settling
    desk.call("dragEnd", 960, 1050)
    assert desk.prop("settling") == "now"
    desk.send(**desk_msg(stripped=["now"]))                                  # the answer
    assert desk.prop("settling") == ""
    desk.pump(0.3)
    assert desk.prop("settling") == ""
    take(desk, "needs", 960, 1050)
    desk.call("dragEnd", 960, 1050)
    assert desk.prop("settling") == "needs"
    desk.send(**desk_msg())                                                  # an answer that changes nothing counts
    assert desk.prop("settling") == ""
    take(desk, "alive", 960, 1050)
    desk.call("dragEnd", 960, 1050)
    assert desk.prop("settling") == "alive"
    desk.pump(0.4)
    assert desk.prop("settling") == ""                                       # no answer came


def test_nothing_is_dimmed_after_a_drag_that_was_cancelled(desk):
    a_full_desk(desk)
    take(desk, "now", 960, 1050)
    desk.call("dragCancel")
    assert desk.prop("settling") == "" and sent_ops(desk) == []


def test_losing_agentd_mid_drag_ends_it_and_a_drop_after_it_sends_nothing(desk):
    a_full_desk(desk)
    take(desk, "needs", 960, 1050)
    desk.send(**desk_msg(stripped=["watching"]))
    desk.call("lost")
    assert desk.prop("dragging") is False and desk.prop("stripped") == ["watching"]      # what waited applied
    desk.call("dragEnd", 960, 1050)
    assert sent_ops(desk) == [] and desk.prop("settling") == ""
    desk.set("connected", True)
    take(desk, "needs", 960, 1050)
    desk.set("connected", False)                                             # the property alone does the same
    assert desk.prop("dragging") is False


def test_a_card_that_stops_being_a_card_ends_its_drag(laptop):
    laptop.turn(1, steps=FOUR)
    laptop.set("watchModel", rows(2))
    laptop.set("connected", True)
    y = laptop.slots["watching"]["y"]
    take(laptop, "watching", 100, 400)                                       # a window over it folds it to its strip
    laptop.cover((16, y, 300, 50))
    assert laptop.prop("dragging") is False and sent_ops(laptop) == []
    laptop.cover()
    laptop.set("unfoldDelayMs", 60)
    laptop.pump(0.3)
    assert laptop.faces["watching"] == "full"
    take(laptop, "watching", 100, 400)                                       # it has nothing left to say
    laptop.set("watchModel", rows(0))
    assert laptop.prop("dragging") is False
    laptop.set("watchModel", rows(2))
    take(laptop, "watching", 100, 400)                                       # a full-screen window takes the desk
    laptop.cover((0, 0, 1280, 720, True))
    assert laptop.prop("dragging") is False and sent_ops(laptop) == []


def test_a_strip_that_becomes_a_card_ends_its_drag(laptop):
    laptop.turn(1, steps=TWO)
    laptop.set("connected", True)
    laptop.set("unfoldDelayMs", 60)
    laptop.cover((0, 100, 1280, 500))
    laptop.pump(0.1)
    assert laptop.faces["now"] == "strip"
    take(laptop, "now", 600, 690, "strip")
    laptop.cover()
    laptop.pump(0.3)
    assert laptop.faces["now"] == "full" and laptop.prop("dragging") is False


def test_what_waited_is_applied_when_a_drag_ends_by_itself(laptop):
    laptop.turn(1, steps=FOUR)
    laptop.set("connected", True)
    take(laptop, "now", 100, 600)
    laptop.send(**desk_msg(hidden=["now"]))
    assert laptop.prop("hidden") == []
    laptop.cover((0, 0, 1280, 720, True))
    assert laptop.prop("dragging") is False and laptop.prop("hidden") == ["now"]


def test_the_snapshot_carries_what_is_in_hand(desk):
    s = a_full_desk(desk)
    snap = desk.call("snapshot")
    assert snap["stripped"] == [] and snap["drag"] is None and snap["dropTarget"] is None and snap["settling"] == ""
    desk.send(**desk_msg(stripped=["alive"]))
    take(desk, "now", 100, centre(s["watching"]) - 1)
    snap = desk.call("snapshot")
    assert snap["stripped"] == ["alive"] and snap["drag"]["id"] == "now"
    assert snap["dropTarget"]["kind"] == "rank" and snap["dropTarget"]["rank"] == 1


def test_a_desk_message_as_the_python_half_sends_it_is_what_the_desk_reads(desk):
    # The shape Desk.snapshot() has once it carries the widgets folded by hand. When it does, build this
    # message from the real Desk instead of by hand.
    msg = {"type": "desk", "folded": False, "hidden": ["machine"], "stripped": ["watching", "needs"],
           "rails": {"now": "left", "watching": "left", "alive": "left", "needs": "right", "away": "right",
                     "machine": "right"},
           "order": {"left": ["now", "watching", "alive"], "right": ["needs", "away", "machine"]}, "screen": ""}
    desk.turn(1, steps=TWO)
    desk.set("watchModel", rows(2))
    desk.set("needsModel", rows(2))
    desk.send(**msg)
    assert desk.prop("stripped") == ["watching", "needs"] and desk.prop("hidden") == ["machine"]
    assert (desk.faces["now"], desk.faces["watching"], desk.faces["needs"]) == ("full", "strip", "strip")
    assert [c["text"] for c in desk.prop("leftStrips")] == ["2 counting"]
    assert [c["text"] for c in desk.prop("rightStrips")] == ["2 need you"]

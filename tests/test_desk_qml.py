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
    // What shell.qml does with a line from agentd: parse it, and hand it to the pill and the desk.
    function feed(line) {
        const ev = JSON.parse(line)
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

    def items(self, name, visible_only=True):
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return found

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


TWO = [("Install ffmpeg", "Installing ffmpeg", "in_progress"), ("Open the browser", None, "pending")]
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
    laptop.set("machineModel", rows(1))                                    # 210
    f = laptop.faces
    assert (f["needs"], f["away"], f["machine"]) == ("full", "full", "strip")   # 200 + 12 + 200 + 12 + 210 = 634 > 600
    laptop.set("needsModel", rows(2))                                      # 156
    assert laptop.faces["machine"] == "full"                               # 156 + 12 + 200 + 12 + 210 = 590


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
    assert right_strips.property("x") <= x_watching < x_needs


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
    assert desk.prop("leftStrips")[-1]["dot"] == "#d97757" and desk.prop("leftStrips")[-1]["ring"]
    assert desk.prop("rightStrips")[0]["text"] == "2 need you" and desk.prop("rightStrips")[0]["outlined"]
    desk.end(1)
    assert desk.prop("leftStrips")[-1]["text"] == "done" and desk.prop("leftStrips")[-1]["dot"] == "#5fb36b"
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
    assert strip["text"] == "Ubuntu 43%" and strip["dot"] == "#d97757" and strip["ring"]
    desk.jobs(BUILD, TIMER, UPDATE, copy)
    desk.clock(5)
    strip = desk.watch["strip"]
    assert strip["text"] == "2 counting" and strip["dot"] == "#d97757" and strip["ring"]
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", pct=43.4))
    desk.clock(5)
    assert desk.watch["strip"]["text"] == "Ubuntu 43%"                # rounded, as the card's percent is
    desk.jobs(job("a1b2", "Ubuntu 26.04 ISO", pct=43.6))
    desk.clock(5)
    assert desk.watch["strip"]["text"] == "Ubuntu 44%"
    # With nothing counting, the strip says what is left.
    desk.jobs(UPDATE)
    desk.clock(5)
    assert desk.watch["strip"] == {"text": "finished", "dot": "#e05252", "ring": False}
    desk.jobs(copy)
    desk.clock(5)
    assert desk.watch["strip"] == {"text": "finished", "dot": "#5fb36b", "ring": False}


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

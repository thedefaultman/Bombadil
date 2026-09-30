"""The desk's state, rails and strips, driven by agentd's messages in an offscreen window.

DeskState, DeskRail and DeskStrips are plain Qt Quick (Quickshell only wraps them in shell.qml),
so they load here without a compositor, with the real PillState beside them as in the shell. The
card faces are the real ones from shell/. Set BOMBADIL_SCREENS=<dir> to save a picture of each state.
"""

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
    def __init__(self, app, tmp_path, width=1920, height=1080):
        self.app = app
        home = tmp_path / "shell"
        home.mkdir()
        for name in DESK_FILES:
            shutil.copy(SHELL / name, home / name)
        for name in CARD_FILES:
            shutil.copy(CARDS / name, home / name)
        qml = tmp_path / "harness.qml"
        qml.write_text(HARNESS % {"dir": home.as_uri(), "width": width, "height": height})
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
def laptop(app, tmp_path):
    d = Desk(app, tmp_path, 1280, 720)
    d.send(type="status", busy=False, provider="claude", queue=[])
    yield d
    d.win.close()
    d.engine.deleteLater()
    d.pump()


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
    for msg in ({"type": "jobs", "jobs": []}, {"type": "dev", "sessions": []}, {"type": "entries", "entries": []},
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
    for near, far in zip(left, left[1:]):
        assert abs(desk.at(far)[0] + far.property("width") - (desk.at(near)[0] - 12)) <= 1
    assert rx[0] == pill_x + pill_w + 12
    for near, far in zip(right, right[1:]):
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

"""Dragging on the desk, with real pointer events across the three windows the shell gives it.

The rails are a window each and the pill with its strips is a third, every one at its own place on the
screen (DeskRails.qml and shell.qml say where). A press goes on delivering to the window it began in
however far the pointer travels, in that window's own coordinates, so a drag from a rail to the bar
arrives in the rail's window as a position far outside it. These tests press, move and release in the
window the gesture began in, with each screen position converted to its coordinates (outside it, when the
pointer is), the way a compositor's grab delivers them. What only a real compositor shows is listed in
docs/DESK.md, under "Dragging cards".

The desk's own rules (where a drop lands, what waits, what ends a drag) are in test_desk_qml.py; these
tests are about the handles, the grab and what is drawn. Set BOMBADIL_SCREENS=<dir> to save a picture
of the three windows in the middle of a drag.
"""

import os
from pathlib import Path

import pytest
from test_desk_qml import FOUR, TWO, Desk, below, centre, desk_msg, plain, rank, rows, sent_ops
from test_desk_qml import a_full_desk as full_desk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)

BAR_HEIGHT = 76         # the pill's 52, and 12 above and below it (the harness says the same)
GAP = 12                # a strip's gap, and a card's

# The bar's window is the root and the rails' are its children, each where the shell puts it: the bar
# across the bottom of the screen; a rail T.railMargin from its edge and T.railTop from the top, down to
# where the bar's zone starts (64 high, with no chips of apps above it). The pill and the strips are laid
# out as in shell.qml.
HARNESS = """
import QtQuick
import "%(dir)s"
import "%(dir)s/DeskTheme.js" as T

Window {
    id: bar
    flags: Qt.FramelessWindowHint
    x: 0; y: %(height)d - height; width: %(width)d; height: 76
    color: "transparent"
    visible: true
    property var sent: []
    property var pillSent: []
    property var clicks: []
    function feed(line) {
        const ev = JSON.parse(line)
        pillState.handle(ev)
        deskState.handle(ev)
    }
    function setWindowsJson(json) { deskState.setWindows(JSON.parse(json)) }
    function setDeskJson(name, json) { deskState[name] = JSON.parse(json) }
    // Which strips were clicked: the desk does nothing with a click on a strip yet.
    function watch(strip) { strip.clicked.connect(() => bar.clicks = bar.clicks.concat([strip.objectName])) }
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => bar.pillSent = bar.pillSent.concat([msg])
    }
    DeskState {
        id: deskState
        objectName: "desk"
        pill: pillState
        pillAnimMs: 0
        tickMs: %(tick)d
        screenWidth: bar.width; screenHeight: %(height)d
        onOutgoing: msg => bar.sent = bar.sent.concat([msg])
    }
    Rectangle {
        id: pillBox
        objectName: "pillBox"
        width: deskState.pillWidth; height: 52; radius: 26
        x: (bar.width - width) / 2; y: bar.height - 12 - height
        color: "#f01a1d21"
    }
    DeskStrips { objectName: "stripsLeft"; desk: deskState; side: "left"
                 origin: Qt.point(0, deskState.screenHeight - bar.height)
                 pillEdge: pillBox.x; pillCentreY: pillBox.y + pillBox.height / 2 }
    DeskStrips { objectName: "stripsRight"; desk: deskState; side: "right"
                 origin: Qt.point(0, deskState.screenHeight - bar.height)
                 pillEdge: pillBox.x + pillBox.width; pillCentreY: pillBox.y + pillBox.height / 2 }

    // Up as the rail says, as DeskRails.qml has it. A layer-shell window with no keyboard focus is never
    // made the active window when it appears, and the offscreen platform makes every new window one
    // except a tooltip, so this is one.
    component RailWindow: Window {
        id: win
        required property var desk
        required property string side
        flags: Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        x: desk.railX(side); y: T.railTop; width: T.cardWidth; height: desk.screenHeight - T.railTop - 64
        color: "transparent"
        visible: !desk.capsule && rail.shown
        DeskRail { id: rail; objectName: "rail-" + win.side; desk: win.desk; side: win.side
                   width: T.cardWidth; height: win.height }
    }
    RailWindow { objectName: "leftWindow"; desk: deskState; side: "left" }
    RailWindow { objectName: "rightWindow"; desk: deskState; side: "right" }
}
"""


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Screen(Desk):
    """The desk in its three windows, driven by pointer events a window at a time."""

    harness = HARNESS

    def __init__(self, app, tmp_path):
        super().__init__(app, tmp_path)
        self.bar = self.win
        self.left = self.win.findChild(QtGui.QWindow, "leftWindow")
        self.right = self.win.findChild(QtGui.QWindow, "rightWindow")

    def items(self, name, visible_only=True):
        found = []
        for win in (self.left, self.right, self.bar):
            todo = [win.contentItem()]
            while todo:
                it = todo.pop()
                if it.objectName() == name and (it.isVisible() or not visible_only):
                    found.append(it)
                todo.extend(it.childItems())
        return found

    # -- where things are on the screen --

    def window_of(self, item):
        """The window an item is drawn in. (Not item.window(): PySide ties what it returns to the item's
        wrapper and deletes the window when the wrapper goes.)"""
        while item.parentItem() is not None:
            item = item.parentItem()
        return next(w for w in (self.left, self.right, self.bar) if w.contentItem() == item)

    def screen_at(self, item, x=None, y=None):
        """A point of an item (its middle, or x and y from its top left) in screen coordinates."""
        win = self.window_of(item)
        p = item.mapToScene(QtCore.QPointF(item.width() / 2 if x is None else x,
                                           item.height() / 2 if y is None else y))
        return win.x() + p.x(), win.y() + p.y()

    def title(self, widget):
        """The middle of a card's title, and of the line under it."""
        s = self.slots[widget]
        return s["x"] + 60, s["y"] + 22

    def why(self, widget):
        s = self.slots[widget]
        return s["x"] + 60, s["y"] + 44

    def mark(self, side):
        """The drop mark of a rail while it is drawn."""
        win = self.left if side == "left" else self.right
        found = [m for m in self.items("dropMark") if self.window_of(m) is win]
        return found[0] if found else None

    def marks(self):
        return [self.mark("left"), self.mark("right")]

    def dim_of(self, item):
        """The dimming over a strip, drawn or not."""
        return next(d for d in self.items("deskStripDim", visible_only=False) if d.parentItem() == item)

    def running_timers(self):
        """The timers that are running anywhere in the desk, as (what they belong to, interval)."""
        return sorted((str(t.parent().objectName() or type(t.parent()).__name__), t.property("interval"))
                      for t in self.win.findChildren(QtCore.QObject)
                      if t.metaObject().className().startswith("QQmlTimer") and t.property("running"))

    def reap(self):
        """What a running event loop does by itself: delete what was let go of (an item the Repeater
        dropped), which processing events alone does not."""
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.pump()

    def end_grab(self, item, transition):
        """Have the DeskDrag on an item see its grab end the way `transition` says, with a pointer that
        has not been released (a point made here is in no state at all). The handler is not kept:
        PySide drops what it holds of an item's children along with the item's own wrapper, so the
        item has to outlive the call."""
        found = [o for o in item.findChildren(QtCore.QObject) if o.property("held") is not None]
        assert len(found) == 1
        QtCore.QMetaObject.invokeMethod(found[0], "grabChanged", QtCore.Qt.DirectConnection,
                                        QtCore.Q_ARG("QPointingDevice::GrabTransition", transition),
                                        QtCore.Q_ARG("QEventPoint", QtGui.QEventPoint()))
        self.pump()

    def brightest(self, win, x, y, w, h):
        """How bright the brightest pixel of a window's rectangle is, from 0 to 1."""
        img = win.grabWindow().copy(QtCore.QRect(round(x), round(y), w, h))
        return max(QtGui.QColor(img.pixel(i, j)).valueF() for i in range(w) for j in range(h))

    # -- the pointer, in screen coordinates, in the window the press began in --

    def local(self, win, x, y):
        return QtCore.QPoint(round(x - win.x()), round(y - win.y()))

    def press(self, win, x, y, button=QtCore.Qt.LeftButton):
        QtTest.QTest.mousePress(win, button, QtCore.Qt.NoModifier, self.local(win, x, y))
        self.pump()

    def move(self, win, x, y):
        QtTest.QTest.mouseMove(win, self.local(win, x, y))
        self.pump()

    def release(self, win, x, y, button=QtCore.Qt.LeftButton):
        QtTest.QTest.mouseRelease(win, button, QtCore.Qt.NoModifier, self.local(win, x, y))
        self.pump()

    def drag(self, win, start, *path):
        """Press at start, move through the path, and let go at its end."""
        self.press(win, *start)
        for point in path:
            self.move(win, *point)
        self.release(win, *path[-1])

    def click(self, item):
        x, y = self.screen_at(item)
        self.press(self.window_of(item), x, y)
        self.release(self.window_of(item), x, y)

    def watch(self, strip):
        QtCore.QMetaObject.invokeMethod(self.win, "watch", QtCore.Q_ARG("QVariant", strip))

    @property
    def clicks(self):
        return list(plain(self.win.property("clicks")))

    @property
    def pill_sent(self):
        return [dict(m) for m in plain(self.win.property("pillSent"))]

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.3)
            Path(out).mkdir(parents=True, exist_ok=True)
            for label, win in (("left", self.left), ("right", self.right), ("bar", self.bar)):
                win.grabWindow().save(str(Path(out) / f"desk-drag-{name}-{label}.png"))


@pytest.fixture
def screen(app, tmp_path):
    s = Screen(app, tmp_path)
    s.send(type="status", busy=False, provider="claude", queue=[])
    # Each window is where the shell puts it: the harness is worth nothing if they are not.
    assert s.left.geometry().getRect() == (16, 40, 300, 976)
    assert s.right.geometry().getRect() == (1604, 40, 300, 976)
    assert s.bar.geometry().getRect() == (0, 1080 - BAR_HEIGHT, 1920, BAR_HEIGHT)
    yield s
    s.win.close()
    s.engine.deleteLater()
    s.pump()


def a_full_desk(screen):
    """The full desk of test_desk_qml.py once its cards have come to rest: one still unfolding is
    smaller than its slot, and a press where its title will be misses."""
    slots = full_desk(screen)
    screen.pump(0.3)
    return slots


# -- the grab: where the pointer is, whichever window the press began in --

def test_a_press_is_reported_in_screen_coordinates_whichever_window_it_began_in(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["alive"]))
    screen.pump(0.3)
    chip = screen.item("deskStrip-alive")
    # Each to a place outside its own window and on the stage, so nothing is dropped anywhere.
    for win, start, goal in ((screen.left, screen.title("now"), (1234, 321)),
                             (screen.right, screen.title("needs"), (700, 789)),
                             (screen.bar, screen.screen_at(chip), (1500, 20))):
        screen.press(win, *start)
        screen.move(win, *goal)
        assert (screen.prop("drag")["x"], screen.prop("drag")["y"]) == goal, win.objectName()
        screen.release(win, *goal)
        assert screen.prop("dragging") is False
    assert sent_ops(screen) == []


def test_a_card_taken_by_its_title_to_the_other_rail_is_moved_there(screen):
    s = a_full_desk(screen)
    goal = (1700, centre(s["away"]) + 10)                               # in the right rail's column
    screen.press(screen.left, *screen.title("watching"))
    assert screen.prop("dragging") is False                              # not until it has moved
    screen.move(screen.left, 100, 700)
    assert screen.prop("drag")["id"] == "watching" and screen.prop("drag")["from"] == "rail"
    screen.move(screen.left, *goal)                                      # far outside the left rail's window
    assert screen.prop("dropTarget") == rank("right", 1, below(s["away"]))
    screen.release(screen.left, *goal)
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "watching", "rail": "right", "rank": 1}]
    assert screen.prop("dragging") is False and screen.prop("settling") == "watching"


def test_a_card_taken_by_the_line_under_its_title_is_a_handle_too(screen):
    s = a_full_desk(screen)
    screen.drag(screen.left, screen.why("alive"), (100, 600), (100, centre(s["now"]) + 5))
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "alive", "rail": "left", "rank": 0}]


def test_a_card_taken_to_another_rank_of_its_own_rail(screen):
    s = a_full_desk(screen)
    screen.drag(screen.left, screen.title("alive"), (200, 700), (200, centre(s["watching"]) + 5))
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "alive", "rail": "left", "rank": 1}]


def test_a_card_of_the_right_rail_is_taken_to_the_left_one(screen):
    s = a_full_desk(screen)
    screen.drag(screen.right, screen.title("machine"), (1700, 700), (200, centre(s["now"]) + 5))
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "machine", "rail": "left", "rank": 0}]


def test_a_card_dropped_in_the_row_is_folded(screen):
    a_full_desk(screen)
    screen.drag(screen.left, screen.title("watching"), (400, 800), (960, 1050))     # over the bar's window
    assert sent_ops(screen) == [{"type": "desk", "op": "fold", "widget": "watching"}]
    assert screen.prop("settling") == "watching"


def test_a_strip_dropped_on_a_rail_is_moved_there(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["watching"]))
    screen.pump(0.3)
    s = screen.slots
    chip = screen.item("deskStrip-watching")
    screen.drag(screen.bar, screen.screen_at(chip), (700, 900), (100, centre(s["now"]) + 5))
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "watching", "rail": "left", "rank": 0}]


def test_a_strip_dropped_on_the_other_rail(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["needs"]))
    screen.pump(0.3)
    chip = screen.item("deskStrip-needs")
    screen.drag(screen.bar, screen.screen_at(chip), (1000, 800), (200, 300))
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "needs", "rail": "left", "rank": 3}]


def test_the_plus_chip_is_not_a_handle_but_the_chips_beside_it_are(screen):
    a_full_desk(screen)
    order = {"left": ["now", "watching", "alive", "needs"], "right": ["away", "machine"]}
    rails = {"now": "left", "watching": "left", "alive": "left", "needs": "left", "away": "right", "machine": "right"}
    screen.send(**desk_msg(folded=True, rails=rails, order=order))
    screen.pump(0.3)
    more = screen.item("deskStripMore")
    assert more is not None and more.property("text") == "+1"
    screen.drag(screen.bar, screen.screen_at(more), (700, 700), (100, 300))
    assert sent_ops(screen) == [] and screen.prop("dragging") is False
    screen.drag(screen.bar, screen.screen_at(screen.item("deskStrip-watching")), (700, 700), (100, 300))
    assert [m["widget"] for m in sent_ops(screen)] == ["watching"]


def test_a_drag_dropped_nowhere_sends_nothing(screen):
    a_full_desk(screen)
    screen.drag(screen.left, screen.title("now"), (500, 700), (960, 500))           # the stage
    assert sent_ops(screen) == [] and screen.prop("dragging") is False and screen.prop("settling") == ""
    assert not screen.item("deskDim-now").isVisible()
    screen.drag(screen.left, screen.title("now"), (500, 700), (960, 1030), (960, 600))   # over the row and away again
    assert sent_ops(screen) == []
    screen.cover((800, 100, 400, 400))
    screen.drag(screen.left, screen.title("now"), (500, 700), (900, 200))           # over a window
    assert sent_ops(screen) == [] and screen.prop("dragging") is False


def test_a_card_dropped_where_it_was_sends_nothing(screen):
    s = a_full_desk(screen)
    screen.drag(screen.left, screen.title("watching"), (200, 600), (100, centre(s["watching"]) + 15))
    assert sent_ops(screen) == [] and screen.prop("settling") == ""


# -- taps are still taps --

def test_a_tap_on_a_cards_title_still_opens_the_details(screen):
    a_full_desk(screen)
    x, y = screen.title("now")
    before = len(screen.pill_sent)
    screen.press(screen.left, x, y)
    screen.release(screen.left, x, y)
    assert screen.pill_sent[before:] == [{"type": "details", "turn": 1}]
    screen.press(screen.left, x, y)                                      # a slide short of the drag threshold
    screen.move(screen.left, x + 4, y + 3)
    assert screen.prop("dragging") is False
    screen.release(screen.left, x + 4, y + 3)
    assert len(screen.pill_sent) == before + 2


def test_a_drag_that_ends_over_the_title_does_not_open_it(screen):
    a_full_desk(screen)
    x, y = screen.title("now")
    before = len(screen.pill_sent)
    screen.press(screen.left, x, y)
    screen.move(screen.left, x + 20, y - 15)
    assert screen.prop("dragging") is True
    screen.move(screen.left, x + 200, y + 300)
    screen.move(screen.left, x, y)                                       # back where it began
    screen.release(screen.left, x, y)
    assert len(screen.pill_sent) == before and sent_ops(screen) == []


def test_a_tap_on_a_strip_still_clicks_and_a_drag_from_it_does_not(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["now", "alive"]))
    screen.pump(0.3)
    now, alive = screen.item("deskStrip-now"), screen.item("deskStrip-alive")
    screen.watch(now)
    screen.watch(alive)
    screen.click(now)
    assert screen.clicks == ["deskStrip-now"]
    x, y = screen.screen_at(alive)
    screen.press(screen.bar, x, y)
    screen.move(screen.bar, x + 3, y - 2)                                # short of the drag threshold
    screen.release(screen.bar, x + 3, y - 2)
    assert screen.clicks == ["deskStrip-now", "deskStrip-alive"]
    screen.drag(screen.bar, (x, y), (700, 500), (x, y))                  # taken away and brought back
    assert screen.clicks == ["deskStrip-now", "deskStrip-alive"] and sent_ops(screen) == []


def test_a_rows_button_and_x_are_not_handles(screen):
    a_full_desk(screen)
    screen.set("watchModel", rows(2, first={"key": "iso", "button": "Open", "remove": True}))
    screen.pump(0.3)
    row = screen.card_row("iso")
    for item in (screen.inside(row, "rowsButton"), screen.inside(row, "rowsRemove")):
        x, y = screen.screen_at(item)
        screen.press(screen.left, x, y)
        screen.move(screen.left, x + 60, y + 120)
        assert screen.prop("dragging") is False
        screen.release(screen.left, x + 60, y + 120)
    # The body of a card starts under the title and the line: nothing there takes hold of it.
    x, y = screen.title("watching")
    screen.press(screen.left, x, y + 50)
    screen.move(screen.left, x + 60, y + 120)
    assert screen.prop("dragging") is False
    screen.release(screen.left, x + 60, y + 120)
    assert sent_ops(screen) == []


def test_a_press_on_a_card_that_has_folded_for_a_window_is_not_a_drag(screen):
    s = a_full_desk(screen)
    screen.cover((16, s["now"]["y"], 300, 100))
    assert screen.faces["now"] == "strip"
    x, y = screen.title("now")
    screen.press(screen.left, x, y)
    screen.move(screen.left, x + 50, y - 50)
    assert screen.prop("dragging") is False
    screen.release(screen.left, x + 50, y - 50)
    assert sent_ops(screen) == []


# -- what the person sees while holding a card --

def test_the_card_in_hand_is_dimmed_where_it_stands_until_agentd_answers(screen):
    s = a_full_desk(screen)
    screen.pump(0.3)
    now, watching = screen.item("deskDim-now"), screen.item("deskDim-watching")
    assert not now.isVisible() and not watching.isVisible()

    def title(widget):      # the title's rectangle in the rail's window
        return screen.left, 14, s[widget]["y"] - 40 + 14, 220, 24
    plain_now, plain_watching = screen.brightest(*title("now")), screen.brightest(*title("watching"))
    screen.press(screen.left, *screen.title("now"))
    screen.move(screen.left, 600, 600)
    assert now.isVisible() and not watching.isVisible()
    assert now.property("opacity") == 0.55
    # It stays where it stands: the grab moves nothing.
    assert screen.screen_at(screen.item("deskGrip-now"), 0, 0) == (s["now"]["x"], s["now"]["y"])
    dimmed = screen.brightest(*title("now"))
    assert 0.4 * plain_now < dimmed < 0.6 * plain_now                    # about 0.45 of what it was
    assert screen.brightest(*title("watching")) == plain_watching
    screen.snap("dimmed")
    screen.move(screen.left, 960, 1050)
    screen.release(screen.left, 960, 1050)
    assert screen.prop("settling") == "now" and now.isVisible()          # until agentd answers
    screen.send(**desk_msg(stripped=["now"]))
    assert not now.isVisible()


def test_a_dropped_card_stops_being_dimmed_when_no_answer_comes(screen):
    a_full_desk(screen)
    screen.set("settleMs", 120)
    screen.drag(screen.left, screen.title("now"), (500, 700), (960, 1050))
    assert screen.item("deskDim-now").isVisible()
    screen.pump(0.4)
    assert not screen.item("deskDim-now").isVisible()


def test_the_strip_in_hand_is_dimmed_too(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["watching"]))
    screen.pump(0.3)
    chip = screen.item("deskStrip-watching")
    dim = screen.dim_of(chip)
    assert not dim.isVisible()
    x, y = screen.screen_at(chip)
    screen.press(screen.bar, x, y)
    screen.move(screen.bar, x + 5, y - 30)
    assert dim.isVisible() and not screen.item("deskDim-now").isVisible()
    screen.move(screen.bar, 960, 300)                                    # the stage
    screen.release(screen.bar, 960, 300)
    assert not dim.isVisible()


def test_the_mark_is_drawn_in_the_rail_the_card_would_land_in_at_the_boundary_it_would_take(screen):
    s = a_full_desk(screen)
    screen.press(screen.left, *screen.title("watching"))
    screen.move(screen.left, 1700, centre(s["away"]) + 10)
    assert screen.mark("left") is None
    mark = screen.mark("right")
    assert (mark.width(), mark.height()) == (300, 3) and mark.property("color").name() == "#e6e8eb"
    assert screen.screen_at(mark) == (1604 + 150, below(s["away"]))      # across the column, in the gap
    screen.snap("mark")
    screen.move(screen.left, 1700, centre(s["machine"]) - 10)            # past the last card up
    assert screen.screen_at(screen.mark("right")) == (1754, s["machine"]["y"] - 6)
    screen.move(screen.left, 100, centre(s["now"]) + 5)                  # its own rail, in front of the card by the pill
    assert screen.mark("right") is None and screen.screen_at(screen.mark("left")) == (166, below(s["now"]))
    screen.move(screen.left, 100, centre(s["watching"]))                 # where it is: no mark
    assert screen.marks() == [None, None]
    screen.move(screen.left, 960, 500)                                   # the stage
    assert screen.marks() == [None, None]
    screen.release(screen.left, 960, 500)


def test_the_mark_follows_a_rail_that_shifts_while_the_card_is_held(screen):
    s = a_full_desk(screen)
    screen.press(screen.left, *screen.title("now"))
    screen.move(screen.left, 1700, centre(s["away"]) + 10)
    assert screen.screen_at(screen.mark("right"))[1] == below(s["away"])
    screen.set("needsModel", rows(3))                                    # the card by the pill grows: away moves up
    moved = screen.slots["away"]
    assert moved["y"] != s["away"]["y"]
    assert screen.screen_at(screen.mark("right"))[1] == below(moved)
    screen.release(screen.left, 960, 500)


def test_a_card_over_the_row_leaves_a_ghost_of_its_chip_beside_the_pill(screen):
    a_full_desk(screen)
    screen.pump(0.3)
    pill, width = screen.item("pillBox"), screen.prop("pillWidth")
    screen.press(screen.left, *screen.title("now"))
    screen.move(screen.left, 600, 900)
    assert screen.items("deskStripGhost") == []
    screen.move(screen.left, 960, 1050)
    (ghost,) = screen.items("deskStripGhost")
    assert ghost.property("text") == screen.prop("nowStripText") and ghost.property("outlined") is True
    assert ghost.property("opacity") == 0.6
    x, y = screen.screen_at(ghost, 0, 0)
    # No strip on that side: a gap from the pill's edge, on the pill's row.
    assert round(x + ghost.width()) == round(pill.x() - GAP)
    assert round(y) == 1080 - BAR_HEIGHT + 12 + 26 - 14
    assert screen.prop("pillWidth") == width                              # it takes no room from the pill
    screen.snap("ghost")
    screen.move(screen.left, 300, 1040)                                  # still over the row
    assert len(screen.items("deskStripGhost")) == 1
    screen.move(screen.left, 960, 500)
    assert screen.items("deskStripGhost") == []
    screen.release(screen.left, 960, 500)


def test_the_ghost_stands_past_the_strips_that_side_already_has(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["watching"]))
    screen.pump(0.3)
    chip = screen.item("deskStrip-watching")
    strips = screen.prop("leftStripsWidth")
    screen.press(screen.left, *screen.title("alive"))
    screen.move(screen.left, 500, 900)
    screen.move(screen.left, 960, 1050)
    (ghost,) = screen.items("deskStripGhost")
    assert round(screen.screen_at(ghost, 0, 0)[0] + ghost.width()) == round(screen.screen_at(chip, 0, 0)[0] - GAP)
    assert screen.prop("leftStripsWidth") == strips                      # and the pill does not move for it
    screen.release(screen.left, 960, 1050)
    assert screen.items("deskStripGhost") == []
    assert sent_ops(screen) == [{"type": "desk", "op": "fold", "widget": "alive"}]


def test_a_card_of_the_right_rail_leaves_its_ghost_on_the_right(screen):
    a_full_desk(screen)
    screen.press(screen.right, *screen.title("needs"))
    screen.move(screen.right, 1500, 900)
    screen.move(screen.right, 960, 1050)
    (ghost,) = screen.items("deskStripGhost")
    pill = screen.item("pillBox")
    assert round(screen.screen_at(ghost, 0, 0)[0]) == round(pill.x() + pill.width() + GAP)
    assert ghost.property("text") == "2 need you"
    screen.release(screen.right, 960, 500)


def test_a_strip_dragged_over_the_row_leaves_no_ghost(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["watching"]))
    screen.pump(0.3)
    x, y = screen.screen_at(screen.item("deskStrip-watching"))
    screen.press(screen.bar, x, y)
    screen.move(screen.bar, x - 100, y - 100)
    screen.move(screen.bar, 960, 1050)
    assert screen.items("deskStripGhost") == []
    screen.release(screen.bar, 960, 1050)
    assert sent_ops(screen) == []


def test_both_rail_windows_are_up_for_a_drag_even_with_no_cards_on_that_side(screen):
    screen.set("connected", True)
    screen.turn(1, steps=FOUR)
    screen.pump(0.3)
    assert screen.left.isVisible() and not screen.right.isVisible()
    screen.press(screen.left, *screen.title("now"))
    assert not screen.right.isVisible()                                  # a press is not a drag
    screen.move(screen.left, 600, 600)
    assert screen.right.isVisible() and screen.left.isVisible()
    screen.move(screen.left, 1700, 600)
    assert screen.mark("right") is not None                              # the empty rail draws the mark
    screen.release(screen.left, 1700, 600)
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "now", "rail": "right", "rank": 0}]
    screen.pump(0.5)
    assert not screen.right.isVisible()                                  # nothing came: it goes again


def test_the_rail_windows_come_up_for_a_strip_taken_from_the_bar(screen):
    screen.set("connected", True)
    screen.turn(1, steps=TWO)
    screen.send(**desk_msg(folded=True))
    screen.pump(0.4)
    assert not screen.left.isVisible() and not screen.right.isVisible()
    x, y = screen.screen_at(screen.item("deskStrip-now"))
    screen.press(screen.bar, x, y)
    screen.move(screen.bar, x - 60, y - 80)
    assert screen.left.isVisible() and screen.right.isVisible()
    screen.move(screen.bar, 100, 600)
    assert screen.mark("left") is not None
    screen.release(screen.bar, 100, 600)
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "now", "rail": "left", "rank": 0}]
    screen.pump(0.5)
    assert not screen.left.isVisible() and not screen.right.isVisible()


def test_a_desk_message_during_a_drag_does_not_destroy_the_card_in_hand(screen):
    s = a_full_desk(screen)
    screen.item("deskGrip-now").setProperty("held_in_hand", True)        # a mark only this very grip has
    screen.press(screen.left, *screen.title("now"))
    screen.move(screen.left, 600, 600)
    # Another bar moved a card in the left rail: the rail's cards are not rebuilt under the person's hand.
    screen.send(**desk_msg(order={"left": ["alive", "watching", "now"], "right": ["needs", "away", "machine"]}))
    screen.reap()
    assert screen.item("deskGrip-now").property("held_in_hand") is True
    assert screen.prop("dragging") is True and screen.prop("order")["left"] == ["now", "watching", "alive"]
    goal = (1700, centre(s["away"]) + 10)
    screen.move(screen.left, *goal)
    screen.release(screen.left, *goal)
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "now", "rail": "right", "rank": 1}]
    assert screen.prop("order")["left"] == ["alive", "watching", "now"]  # what waited has been applied


def test_a_card_that_folds_under_a_window_while_held_ends_its_drag_and_nothing_is_sent(screen):
    s = a_full_desk(screen)
    screen.press(screen.left, *screen.title("watching"))
    screen.move(screen.left, 600, 600)
    assert screen.prop("dragging") is True
    screen.cover((16, s["watching"]["y"], 300, 60))
    assert screen.prop("dragging") is False
    screen.move(screen.left, 1700, 700)                                  # the button is still down: it does nothing
    assert screen.marks() == [None, None]
    screen.release(screen.left, 1700, 700)
    assert sent_ops(screen) == []


def test_a_handle_that_goes_away_mid_drag_ends_the_drag(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["now", "watching"]))                # two strips on the left
    screen.pump(0.3)
    assert [c["id"] for c in screen.prop("leftStrips")] == ["watching", "now"]
    x, y = screen.screen_at(screen.item("deskStrip-now"))                # the outer one: the last place
    screen.press(screen.bar, x, y)
    screen.move(screen.bar, x - 20, y - 60)
    assert screen.prop("drag")["id"] == "now"
    screen.set("watchModel", rows(0))                                    # one strip left: the last place goes
    screen.reap()
    assert screen.prop("dragging") is False
    screen.release(screen.bar, 100, 600)
    assert sent_ops(screen) == []


def test_losing_agentd_mid_drag_ends_it_and_the_release_after_it_sends_nothing(screen):
    a_full_desk(screen)
    screen.press(screen.left, *screen.title("now"))
    screen.move(screen.left, 1700, 600)
    assert screen.mark("right") is not None
    screen.call("lost")
    assert screen.prop("dragging") is False and screen.marks() == [None, None]
    screen.move(screen.left, 1700, 700)
    screen.release(screen.left, 1700, 700)
    assert sent_ops(screen) == []


def test_a_grab_taken_away_is_no_drop(screen):
    s = a_full_desk(screen)
    goal = (1700, centre(s["away"]) + 10)
    for transition in (QtGui.QPointingDevice.GrabTransition.CancelGrabExclusive,
                       QtGui.QPointingDevice.GrabTransition.UngrabExclusive):    # taken, or let go without a release
        screen.press(screen.left, *screen.title("watching"))
        screen.move(screen.left, *goal)
        assert screen.prop("dropTarget") is not None
        screen.end_grab(screen.item("deskGrip-watching"), transition)
        assert screen.prop("dragging") is False and screen.marks() == [None, None]
        screen.release(screen.left, *goal)
        assert sent_ops(screen) == [] and screen.prop("settling") == ""


def test_a_window_that_loses_focus_mid_drag_ends_the_drag_with_no_drop(screen):
    # Another window coming up as the active one takes the grab from the window the drag began in (the
    # bar, the only one of the three that is ever the active one): the same end as a release, but with
    # the pointer still down.
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["watching"]))
    screen.pump(0.3)
    x, y = screen.screen_at(screen.item("deskStrip-watching"))
    screen.press(screen.bar, x, y)
    screen.move(screen.bar, 100, 600)
    assert screen.prop("dragging") is True and screen.bar.isActive()
    other = QtQuick.QQuickWindow()
    other.setGeometry(400, 300, 100, 100)
    other.show()
    screen.pump(0.1)
    assert screen.prop("dragging") is False and screen.marks() == [None, None]
    screen.release(screen.bar, 100, 600)
    assert sent_ops(screen) == []
    other.close()


def test_only_the_left_button_takes_a_card(screen):
    a_full_desk(screen)
    x, y = screen.title("now")
    screen.press(screen.left, x, y, button=QtCore.Qt.RightButton)
    screen.move(screen.left, 600, 600)
    assert screen.prop("dragging") is False
    screen.release(screen.left, 600, 600, button=QtCore.Qt.RightButton)
    assert sent_ops(screen) == []


def test_a_second_button_pressed_mid_drag_changes_nothing(screen):
    s = a_full_desk(screen)
    goal = (1700, centre(s["away"]) + 10)
    screen.press(screen.left, *screen.title("watching"))
    screen.move(screen.left, *goal)
    screen.press(screen.left, *goal, button=QtCore.Qt.RightButton)
    assert screen.prop("dragging") is True and sent_ops(screen) == []
    screen.release(screen.left, *goal)                                   # the left one, which holds the card
    assert sent_ops(screen) == [{"type": "desk", "op": "move", "widget": "watching", "rail": "right", "rank": 1}]
    screen.release(screen.left, *goal, button=QtCore.Qt.RightButton)
    assert len(sent_ops(screen)) == 1


# -- cost --

def test_a_drag_starts_no_timer_and_a_drop_starts_only_the_wait_for_agentd(screen):
    a_full_desk(screen)
    screen.pump(0.4)
    idle = screen.running_timers()
    screen.press(screen.left, *screen.title("now"))
    screen.move(screen.left, 600, 600)
    for goal in ((1700, 700), (960, 1050), (100, 900), (960, 500)):      # a rail, the row, its own rail, the stage
        screen.move(screen.left, *goal)
        screen.pump(0.1)
        assert screen.running_timers() == idle, goal
    screen.pump(0.5)
    assert screen.running_timers() == idle
    screen.move(screen.left, 960, 1050)
    screen.release(screen.left, 960, 1050)
    # The wait for agentd, and each rail's usual short while before it takes its window down.
    assert screen.running_timers() == [("desk", 500), ("rail-left", 230), ("rail-right", 230)]
    screen.send(**desk_msg(stripped=["now"]))
    assert ("desk", 500) not in screen.running_timers()                  # an answer ends the wait
    screen.pump(0.4)
    assert screen.running_timers() == idle


def test_dragging_loads_without_qml_warnings(screen):
    a_full_desk(screen)
    screen.send(**desk_msg(stripped=["alive"]))
    screen.pump(0.3)
    screen.drag(screen.left, screen.title("now"), (500, 700), (1700, 600), (960, 1050), (960, 500))
    screen.drag(screen.left, screen.title("watching"), (500, 700), (1700, 600))
    screen.send(**desk_msg(stripped=["alive", "watching"]))
    x, y = screen.screen_at(screen.item("deskStrip-alive"))
    screen.drag(screen.bar, (x, y), (500, 700), (100, 200))
    screen.send(**desk_msg(stripped=[]))
    screen.pump(0.6)
    assert screen.warnings == []

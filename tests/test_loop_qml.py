"""The "noticed" chip, its card and what the bar reports about itself, in an offscreen window.

LoopState, NoticedChip and NoticedCard are plain Qt Quick (Quickshell only wraps them in
shell.qml), so they load here without a compositor. The chip is built inside a delegate, as the
bar's PanelWindow is inside Variants, because names resolve differently there. Set
BOMBADIL_SCREENS=<dir> to save a picture of each state.
"""

import json
import os
import time
from collections import namedtuple
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)

SHELL = Path(__file__).resolve().parents[1] / "shell"
Box = namedtuple("Box", "x0 y0 x1 y1")   # an item's corners in the window

HARNESS = """
import QtQuick
import QtQuick.Layouts
import "%s"

Window {
    id: w
    width: 820; height: 760; visible: true
    color: "#3b4a5a"
    property var sent: []
    property int screens: 1
    property bool linked: true
    property bool typing: false
    property bool drawer: false
    property real pillMax: 520
    PillState {
        id: pillState
        objectName: "pill"
        connected: true
        onOutgoing: msg => w.sent = w.sent.concat([msg])
    }
    LoopState {
        id: loopState
        objectName: "loop"
        connected: w.linked
        busy: pillState.stoppable
        typing: w.typing
        drawer: w.drawer
        pid: 4242
        build: "9f3c2a1"
        peekDelay: 30; leaveDelay: 40; aliveMs: 600000
        onOutgoing: msg => w.sent = w.sent.concat([msg])
    }
    // A delegate standing in for the bar's window: the same parts, placed as shell.qml places them.
    Repeater {
        model: w.screens
        Item {
            id: bar
            objectName: "bar"
            required property int index
            parent: w.contentItem
            anchors.fill: parent
            function report() {
                loopState.reportRects("Virtual-" + (index + 1), 1920, 1080, 1080 - height,
                    [["statusLine", line], ["pillBox", pillBox], ["noticedChip", chip], ["noticedCard", card]])
            }
            ColumnLayout {
                id: column
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
                spacing: 8
                StatusLine { id: line; objectName: "statusLine"; pill: pillState; Layout.fillWidth: true; Layout.maximumWidth: w.pillMax; Layout.alignment: Qt.AlignHCenter }
                Rectangle {
                    id: pillBox
                    objectName: "pillBox"
                    Layout.fillWidth: true; Layout.maximumWidth: w.pillMax; Layout.alignment: Qt.AlignHCenter
                    implicitHeight: 52; radius: 26; color: "#f01a1d21"; border.color: "#2a2f36"; border.width: 1.5
                }
            }
            NoticedChip {
                id: chip
                loop: loopState
                screenName: "Virtual-" + (bar.index + 1)
                pillRight: column.x + pillBox.x + pillBox.width
                pillHeight: pillBox.height
                columnHeight: column.height
                windowWidth: bar.width
            }
            NoticedCard { id: card; loop: loopState; chip: chip; screenName: "Virtual-" + (bar.index + 1) }
        }
    }
}
"""

OFFER = {
    "id": "a1", "kind": "offer", "title": "show me my passwords", "meta": "4 times on 3 days",
    "what": "Say “my passwords” and Passwords opens. No model, under a tenth of a second.",
    "primary": {"label": "Make the word", "op": "accept", "form": "word"},
    "others": [{"label": "Make it an app", "op": "accept", "form": "app"}],
    "forms": [{"form": "word", "label": "A word", "recommended": True},
              {"form": "app", "label": "A new app"}],
}
CHANGE = {
    "id": "c1", "kind": "change", "title": "New apps no longer open on top of each other",
    "meta": "Seen 5 times on 3 days", "what": "Apps now open clear of the line above the pill.",
    "primary": {"label": "Use it", "op": "accept"}, "others": [], "forms": [],
}
FOUND = {
    "id": "f1", "kind": "found", "title": "Can’t fix here: the details drawer takes no keyboard",
    "meta": "3 times on 2 days", "what": "Held on this machine. Nothing is sent until you press Submit.",
    "primary": {"label": "Send to the project", "op": "report"}, "others": [], "forms": [],
}
MORE = {
    "id": "a2", "kind": "offer", "title": "what is eating my memory", "meta": "5 times on 4 days",
    "what": "Say “memory” and Memory opens.", "primary": {"label": "Make the word", "op": "accept", "form": "word"},
    "others": [], "forms": [],
}


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Bar:
    def __init__(self, app, tmp_path):
        self.app = app
        qml = tmp_path / "harness.qml"
        qml.write_text(HARNESS % SHELL.as_uri())
        self.engine = QtQml.QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        # Binding errors (a TypeError on an undefined loop) arrive as messages, not as engine warnings.
        QtCore.qInstallMessageHandler(lambda mode, ctx, msg: self.warnings.append(msg)
                                      if ".qml" in (ctx.file or "") or "TypeError" in msg else None)
        self.pill = self.win.findChild(QtCore.QObject, "pill")
        self.loop = self.win.findChild(QtCore.QObject, "loop")
        self.pump()

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    def noticed(self, *rows, **extra):
        ev = {"type": "noticed", "count": len(rows), "hidden": False, "resting": "", "lately": "",
              "rows": list(rows)}
        ev.update(extra)
        self.loop_send(**ev)

    def loop_send(self, **ev):
        QtCore.QMetaObject.invokeMethod(self.loop, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()

    def pill_send(self, **ev):
        ev.setdefault("type", "event")
        QtCore.QMetaObject.invokeMethod(self.pill, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()

    def call(self, name, *args, target=None):
        ret = QtCore.QMetaObject.invokeMethod(
            target or self.loop, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return ret.toVariant() if hasattr(ret, "toVariant") else ret

    def set(self, **props):
        for k, v in props.items():
            target = self.win if self.win.property(k) is not None else self.loop
            target.setProperty(k, v)
        self.pump()

    def prop(self, name):
        return self.loop.property(name)

    def items(self, name, visible_only=True):
        """Items by objectName through the visual tree (delegates included), top to bottom."""
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return sorted(found, key=lambda i: (i.mapToScene(QtCore.QPointF(0, 0)).y(),
                                            i.mapToScene(QtCore.QPointF(0, 0)).x()))

    def item(self, name):
        found = self.items(name, visible_only=False)
        return found[0] if found else None

    def labels(self, name):
        """The labels of the buttons called `name`, top to bottom."""
        return [i.property("label") for i in self.items(name)]

    def shown(self, name):
        it = self.item(name)
        return it is not None and it.isVisible() and it.width() > 0

    def text(self, name):
        return self.item(name).property("text")

    def texts(self, name):
        return [i.property("text") for i in self.items(name)]

    def centre(self, it):
        return it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()

    def box(self, it):
        p = it.mapToScene(QtCore.QPointF(0, 0))
        return Box(p.x(), p.y(), p.x() + it.width(), p.y() + it.height())

    def hover(self, it, fresh=True):
        if fresh:
            # The pointer may already be on that spot from an earlier test: a move to it would enter nothing.
            QtTest.QTest.mouseMove(self.win, QtCore.QPoint(3, 3))
            self.pump(0.1)
        QtTest.QTest.mouseMove(self.win, self.centre(it))
        self.pump(0.15)

    def leave(self):
        QtTest.QTest.mouseMove(self.win, QtCore.QPoint(5, 5))
        self.pump(0.2)

    def click(self, it):
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, self.centre(it))
        self.pump()

    def press(self, name, nth=0):
        self.click(self.items(name)[nth])

    @property
    def sent(self):
        value = self.win.property("sent")
        return [dict(m) for m in (value.toVariant() if hasattr(value, "toVariant") else value)]

    def drain(self):
        """What was sent so far, then forgotten."""
        out = self.sent
        self.win.setProperty("sent", [])
        return out

    def peek(self):
        self.hover(self.item("noticedChip"))
        assert self.prop("peeked") is True

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.3)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"{name}.png"))


@pytest.fixture
def bar(app, tmp_path):
    b = Bar(app, tmp_path)
    b.pill_send(type="status", busy=False, provider="claude", queue=[])
    b.drain()
    yield b
    b.win.close()
    b.engine.deleteLater()
    b.pump()


# -- the chip --

def test_the_chip_shows_only_while_something_waits(bar):
    assert not bar.shown("noticedChip")
    bar.noticed(OFFER)
    assert bar.shown("noticedChip") and bar.text("noticedChipText") == "noticed 1"
    bar.snap("chip-alone")
    bar.noticed(OFFER, FOUND)
    assert bar.text("noticedChipText") == "noticed 2"
    bar.noticed(OFFER, hidden=True)        # "hide noticed"
    assert not bar.shown("noticedChip")
    bar.noticed(OFFER)
    assert bar.shown("noticedChip")
    bar.noticed(count=0, rows=[])
    assert not bar.shown("noticedChip")


def test_the_chip_never_shows_during_a_turn(bar):
    bar.noticed(OFFER)
    assert bar.shown("noticedChip")
    bar.pill_send(kind="turn_start", turn=1, prompt="install ffmpeg")
    assert not bar.shown("noticedChip")
    bar.noticed(OFFER, FOUND)              # news during the turn does not show it either
    assert not bar.shown("noticedChip")
    bar.pill_send(kind="turn_end", turn=1, seconds=3, changed=False)
    assert bar.shown("noticedChip")


def test_the_chip_goes_while_agentd_is_away(bar):
    bar.noticed(OFFER)
    bar.set(linked=False)                  # agentd gone: nothing to answer a tap
    assert not bar.shown("noticedChip")
    bar.set(linked=True)
    assert bar.shown("noticedChip")


def test_the_chip_does_not_turn_up_while_the_pill_has_the_keyboard(bar):
    bar.set(typing=True)
    bar.noticed(OFFER)
    assert not bar.shown("noticedChip")    # he is typing: not now
    bar.set(typing=False)
    assert bar.shown("noticedChip")        # he is done: it comes
    bar.set(typing=True)
    assert bar.shown("noticedChip")        # one that is up stays up


def test_the_chip_sits_right_of_the_pill_and_centred_on_it(bar):
    bar.noticed(OFFER)
    chip, pill = bar.box(bar.item("noticedChip")), bar.box(bar.item("pillBox"))
    assert chip.x0 - pill.x1 == 12
    assert abs((chip.y0 + chip.y1) / 2 - (pill.y0 + pill.y1) / 2) < 1
    assert chip.y1 - chip.y0 == 28


def test_the_chip_sits_above_the_pill_when_there_is_no_room_beside_it(bar):
    bar.set(pillMax=800)
    bar.noticed(OFFER)
    chip_item = bar.item("noticedChip")
    chip, pill = bar.box(chip_item), bar.box(bar.item("pillBox"))
    assert chip.y1 <= pill.y0 and abs(chip.x1 - pill.x1) < 1.5      # above its right end
    assert chip_item.property("reach") > 0                          # the layer must grow to hold it
    bar.snap("chip-narrow")
    # the card rises from it, above the chip, with its right edge on the chip's
    bar.peek()
    card_item = bar.item("noticedCard")
    card = bar.box(card_item)
    assert card.y1 <= chip.y0 and abs(card.x1 - chip.x1) < 1.5 and card.x0 >= 0
    assert card_item.property("reach") > chip_item.property("reach")
    bar.snap("peek-narrow")


def test_the_chip_says_nothing_to_the_mask_while_hidden(bar):
    chip = bar.item("noticedChip")
    card = bar.item("noticedCard")
    assert chip.width() == 0 and chip.height() == 0 and card.width() == 0 and card.height() == 0
    assert chip.property("reach") == 0 and card.property("reach") == 0


# -- the card --

def test_hovering_the_chip_opens_the_card_after_a_short_delay(bar):
    bar.set(peekDelay=200)
    bar.noticed(OFFER)
    QtTest.QTest.mouseMove(bar.win, bar.centre(bar.item("noticedChip")))
    bar.pump(0.05)
    assert bar.prop("peeked") is False and not bar.shown("noticedCard")   # crossing it does not flash it
    bar.pump(0.3)
    assert bar.prop("peeked") is True and bar.shown("noticedCard")
    bar.leave()
    assert bar.prop("peeked") is False and not bar.shown("noticedCard")


def test_a_pointer_passing_over_the_chip_opens_nothing(bar):
    bar.set(peekDelay=200)
    bar.noticed(OFFER)
    QtTest.QTest.mouseMove(bar.win, bar.centre(bar.item("noticedChip")))
    bar.pump(0.05)
    bar.leave()
    bar.pump(0.4)
    assert bar.prop("peeked") is False


def test_the_card_stays_while_the_pointer_is_on_it(bar):
    bar.noticed(OFFER)
    bar.peek()
    bar.hover(bar.item("noticedRowTitle"), fresh=False)   # the gap between chip and card was crossed
    bar.pump(0.2)
    assert bar.prop("peeked") is True
    bar.leave()
    assert bar.prop("peeked") is False


def test_an_offer_row_shows_his_words_the_count_and_one_button(bar):
    bar.noticed(OFFER, lately="2 changes this week")
    bar.peek()
    assert bar.text("noticedTitle") == "Noticed"
    assert bar.text("noticedWhy") == "1 idea"
    assert bar.item("noticedCard").width() == 300
    assert bar.texts("noticedRowTitle") == ["show me my passwords"]
    assert bar.texts("noticedRowMeta") == ["4 times on 3 days"]
    assert bar.texts("noticedRowWhat") == ["Say “my passwords” and Passwords opens. No model, under a tenth of a second."]
    assert bar.labels("noticedPrimary") == ["Make the word"]
    assert bar.shown("noticedOtherWays") and bar.shown("noticedNotNow") and bar.shown("noticedNever")
    assert not bar.shown("noticedWay")                     # the other forms wait behind "Other ways"
    assert bar.text("noticedLately") == "Lately: 2 changes this week" and bar.shown("noticedSeeAll")
    assert not bar.shown("noticedResting")
    bar.snap("peek-with-an-offer")


def test_a_found_row_offers_to_send_it_to_the_project(bar):
    bar.noticed(FOUND, CHANGE)
    bar.peek()
    assert bar.text("noticedWhy") == "1 change to look at · 1 thing it found"
    assert sorted(bar.labels("noticedPrimary")) == ["Send to the project", "Use it"]
    # a change can only be put off, and a found thing has no other forms
    assert len(bar.items("noticedNotNow")) == 2 and len(bar.items("noticedNever")) == 1
    assert not bar.shown("noticedOtherWays")
    bar.snap("peek-with-a-found-row")


def test_the_card_shows_three_rows_at_most(bar):
    rows = [dict(OFFER, id=f"a{i}", title=f"open the thing {i}") for i in range(1, 5)]
    bar.noticed(*rows)
    assert bar.prop("count") == 4
    bar.peek()
    assert bar.texts("noticedRowTitle") == ["open the thing 1", "open the thing 2", "open the thing 3"]
    assert bar.text("noticedWhy") == "3 ideas"
    bar.snap("peek-with-three-rows")


def test_the_why_line_counts_the_kinds(bar):
    why = bar.call("why")
    assert why == "Nothing waiting"
    bar.noticed(OFFER, MORE, CHANGE)
    assert bar.call("why") == "2 ideas · 1 change to look at"
    bar.noticed(FOUND, dict(FOUND, id="f2", kind="report"))
    assert bar.call("why") == "1 thing it found · 1 report to send"


def test_resting_and_lately_are_the_last_quiet_lines(bar):
    bar.noticed(CHANGE, resting="2026-11-03", lately="Lately: 2 changes this week")
    bar.peek()
    assert bar.text("noticedResting") == "Resting offers until 3 Nov"
    assert bar.text("noticedLately") == "Lately: 2 changes this week"
    assert bar.box(bar.item("noticedResting")).y0 < bar.box(bar.item("noticedLately")).y0
    bar.snap("peek-resting")
    assert bar.call("restingLine") == "Resting offers until 3 Nov"
    bar.noticed(CHANGE, resting="3 Nov")
    assert bar.call("restingLine") == "Resting offers until 3 Nov"
    bar.noticed(CHANGE, resting="Resting offers until 3 Nov")
    assert bar.call("restingLine") == "Resting offers until 3 Nov"
    bar.noticed(CHANGE, resting="")
    assert bar.call("restingLine") == "" and not bar.shown("noticedResting")


def test_the_card_rises_above_the_chip_with_its_right_edge_on_the_chips(bar):
    bar.noticed(OFFER)
    bar.peek()
    card_item = bar.item("noticedCard")
    card, chip, pill = bar.box(card_item), bar.box(bar.item("noticedChip")), bar.box(bar.item("pillBox"))
    assert abs(card.x1 - chip.x1) < 1.5 and card.y1 < chip.y0 and card.y1 <= pill.y0
    assert card_item.property("reach") > card_item.height()    # the layer is asked to grow for it


def test_the_card_shows_on_the_screen_of_its_chip_only(bar):
    bar.win.setProperty("screens", 2)
    bar.pump(0.2)
    bar.noticed(OFFER)
    chips = {c.property("screenName"): c for c in bar.items("noticedChip")}
    assert set(chips) == {"Virtual-1", "Virtual-2"}
    bar.hover(chips["Virtual-2"])
    cards = {c.property("screenName"): c for c in bar.items("noticedCard", visible_only=False)}
    assert cards["Virtual-2"].width() == 300 and cards["Virtual-1"].width() == 0


# -- what a press sends --

def test_the_primary_button_is_the_ask(bar):
    bar.noticed(OFFER)
    bar.peek()
    bar.press("noticedPrimary")
    assert bar.drain() == [{"type": "noticed_do", "op": "accept", "id": "a1", "form": "word"}]
    bar.press("noticedPrimary")                     # the answer has not come: a second tap does nothing
    assert bar.drain() == []
    bar.noticed(count=0, rows=[])                   # the word was made: nothing waits, the card goes
    assert not bar.shown("noticedChip") and not bar.shown("noticedCard")


def test_other_ways_opens_the_forms_and_a_tap_on_one_accepts_it(bar):
    bar.noticed(OFFER)
    bar.peek()
    bar.press("noticedOtherWays")
    assert bar.drain() == []                        # opening it asks nothing
    assert bar.labels("noticedWay") == ["Make it an app"]
    bar.snap("peek-other-ways")
    bar.press("noticedWay")
    assert bar.drain() == [{"type": "noticed_do", "op": "accept", "id": "a1", "form": "app"}]


def test_other_ways_fall_back_to_the_rows_forms(bar):
    row = dict(OFFER, others=[])
    assert bar.call("others", row) == [{"label": "A new app", "op": "accept", "form": "app"}]
    assert bar.call("others", dict(row, forms=[])) == []
    assert bar.call("others", dict(OFFER, others=[{"label": "Not now", "op": "not_now"}], forms=[])) == []


def test_not_now_and_never_say_so_for_that_row(bar):
    bar.noticed(OFFER, MORE)
    bar.peek()
    bar.press("noticedNotNow", 1)
    assert bar.drain() == [{"type": "noticed_do", "op": "not_now", "id": "a2"}]
    bar.press("noticedNever", 0)
    assert bar.drain() == [{"type": "noticed_do", "op": "never", "id": "a1"}]


def test_a_found_row_hands_over_to_the_window_and_the_card_goes(bar):
    bar.noticed(FOUND)
    bar.peek()
    bar.press("noticedPrimary")
    assert bar.drain() == [{"type": "noticed_do", "op": "report", "id": "f1"}]
    assert bar.prop("cardOpen") is False


def test_got_it_has_no_quiet_buttons_and_a_change_has_only_not_now(bar):
    row = {"id": "g1", "kind": "offer", "title": "memory", "primary": {"label": "Got it", "op": "got_it"}}
    assert bar.call("quiet", row) == []
    assert bar.call("quiet", CHANGE) == [{"label": "Not now", "op": "not_now"}]
    assert [q["op"] for q in bar.call("quiet", OFFER)] == ["not_now", "never"]


def test_a_row_without_a_primary_says_what_its_kind_would(bar):
    assert bar.call("primary", {"id": "x", "kind": "offer"}) == {"label": "Make it", "op": "accept"}
    assert bar.call("primary", {"id": "x", "kind": "change"}) == {"label": "Use it", "op": "accept"}
    assert bar.call("primary", {"id": "x", "kind": "found"}) == {"label": "Send to the project", "op": "report"}
    assert bar.call("primary", OFFER) == {"label": "Make the word", "op": "accept", "form": "word"}


def test_a_preview_or_a_failure_shows_under_its_row(bar):
    bar.noticed(OFFER)
    bar.peek()
    bar.loop_send(type="noticed_result", op="preview", id="a1", ok=True, text="",
                  preview="“my passwords” opens Passwords.\nSeen 4 times, last on Tuesday.")
    assert bar.shown("noticedResult") and "opens Passwords" in bar.text("noticedResultText")
    bar.snap("peek-preview")
    bar.press("noticedPrimary")                     # the answer is forgotten when he asks again
    assert not bar.shown("noticedResult")
    bar.loop_send(type="noticed_result", op="accept", id="a1", ok=False, text="Could not make the word.")
    assert bar.text("noticedResultText") == "Could not make the word."
    bar.snap("peek-failed")
    bar.press("noticedPrimary")                     # and a failure lets him try once more
    assert bar.drain()[-1]["op"] == "accept"


def test_see_all_opens_the_window(bar):
    bar.noticed(OFFER, lately="2 changes this week")
    bar.peek()
    bar.press("noticedSeeAll")
    assert bar.drain() == [{"type": "noticed_do", "op": "open"}]


# -- keeping the card up --

def test_a_click_keeps_the_card_and_opens_the_window_and_another_closes_it(bar):
    bar.noticed(OFFER)
    chip = bar.item("noticedChip")
    bar.click(chip)
    assert bar.prop("kept") is True and bar.shown("noticedCard")
    assert bar.drain() == [{"type": "noticed_do", "op": "open"}]
    bar.leave()
    assert bar.shown("noticedCard")                 # kept: the pointer may go
    bar.click(chip)
    assert bar.prop("cardOpen") is False and not bar.shown("noticedCard")
    assert bar.drain() == []
    # the pointer is still on the chip after the click: it does not peek again until it leaves
    bar.pump(0.2)
    assert bar.prop("peeked") is False
    bar.leave()
    bar.hover(chip)
    assert bar.prop("peeked") is True


def test_saying_noticed_keeps_the_card_up_even_with_nothing_waiting(bar):
    opened = []
    bar.loop.opened.connect(lambda: opened.append(1))
    bar.loop_send(type="noticed_open")
    assert opened == [1] and bar.prop("kept") is True
    bar.loop.setProperty("cardScreen", "Virtual-1")   # shell.qml picks the screen
    bar.pump()
    assert bar.shown("noticedCard") and bar.text("noticedWhy") == "Nothing waiting"
    assert not bar.shown("noticedChip")


def test_esc_puts_a_kept_card_away_and_a_click_elsewhere_too(bar):
    bar.noticed(OFFER)
    bar.click(bar.item("noticedChip"))
    assert bar.call("esc") is True and not bar.shown("noticedCard")
    assert bar.call("esc") is False                   # nothing kept: the pill does what it does
    bar.click(bar.item("noticedChip"))
    bar.call("keyboardLost", "Virtual-2")
    assert bar.prop("kept") is True                   # another screen's pill
    bar.call("keyboardLost", "Virtual-1")
    assert bar.prop("kept") is False


def test_hiding_noticed_and_finishing_the_last_row_put_the_card_away(bar):
    bar.noticed(OFFER)
    bar.click(bar.item("noticedChip"))
    bar.noticed(OFFER, hidden=True)                   # "hide noticed"
    assert bar.prop("cardOpen") is False and not bar.shown("noticedChip")
    bar.noticed(OFFER)
    bar.click(bar.item("noticedChip"))
    assert bar.prop("kept") is True
    bar.noticed(count=0, rows=[])                     # the last thing that waited is done
    assert bar.prop("cardOpen") is False


def test_a_turn_starting_takes_the_card_away(bar):
    bar.noticed(OFFER)
    bar.click(bar.item("noticedChip"))
    bar.pill_send(kind="turn_start", turn=1, prompt="install docker")
    assert bar.prop("kept") is False and not bar.shown("noticedCard") and not bar.shown("noticedChip")


# -- what the bar says about itself --

def test_hello_and_then_alive_every_so_often(bar):
    bar.set(linked=False, aliveMs=90)
    bar.drain()
    bar.set(linked=True)
    bar.pump(0.6)
    sent = bar.drain()
    assert sent[0] == {"type": "hello", "client": "bar", "pid": 4242, "build": "9f3c2a1"}
    assert {"type": "noticed_state"} in sent
    alive = [m for m in sent if m["type"] == "alive"]
    assert len(alive) >= 2 and abs(alive[0]["t"] - time.time()) < 5
    bar.set(linked=False)
    bar.drain()
    bar.pump(0.3)
    assert bar.drain() == []                          # nothing is written to a dead socket


def test_rects_are_the_screens_pixels_and_sent_only_when_they_change(bar):
    bar.noticed(OFFER)
    bar.drain()
    barbox = bar.item("bar")
    bar.call("report", target=barbox)
    msgs = bar.drain()
    assert len(msgs) == 1 and msgs[0]["type"] == "rects" and msgs[0]["screen"] == "Virtual-1"
    assert (msgs[0]["w"], msgs[0]["h"]) == (1920, 1080)
    rects = {r["name"]: r for r in msgs[0]["rects"]}
    assert set(rects) == {"pillBox", "noticedChip"}   # the idle line and the closed card are not showing
    pill = bar.item("pillBox")
    win_h = bar.win.height()
    assert rects["pillBox"]["h"] == 52 and rects["pillBox"]["y"] == round(pill.mapToScene(QtCore.QPointF(0, 0)).y() + 1080 - win_h)
    bar.call("report", target=barbox)
    assert bar.drain() == []                          # nothing moved: nothing said
    bar.peek()
    bar.call("report", target=barbox)
    msgs = bar.drain()
    assert {r["name"] for r in msgs[0]["rects"]} == {"pillBox", "noticedChip", "noticedCard"}
    # a reconnect forgets what agentd was told
    bar.set(linked=False)
    bar.set(linked=True)
    bar.pump(0.1)
    bar.drain()
    bar.call("report", target=barbox)
    assert len(bar.drain()) == 1


def test_focus_ack_says_how_long_the_keyboard_took(bar):
    bar.set(fixedNow=10000.0)
    bar.loop_send(type="summon", id=7)
    bar.set(fixedNow=10120.0)
    bar.call("inputFocused")
    assert bar.drain() == [{"type": "focus_ack", "id": 7, "ms": 120}]
    bar.call("inputFocused")                          # the same summon is acknowledged once
    assert bar.drain() == []


def test_a_summon_that_never_got_the_keyboard_sends_nothing(bar):
    bar.set(fixedNow=10000.0)
    bar.loop_send(type="summon", id=8)
    bar.set(fixedNow=10000.0 + 60000)                 # he clicked the pill a minute later: not the summon's
    bar.call("inputFocused")
    assert bar.drain() == []
    bar.call("inputFocused")                          # and with no summon at all, nothing either
    assert bar.drain() == []
    bar.loop_send(type="summon", id=9)                # a newer summon replaces an older one
    bar.loop_send(type="summon", id=10)
    bar.call("inputFocused")
    assert [m["id"] for m in bar.drain()] == [10]


def test_three_escapes_in_ten_seconds_with_a_drawer_up_say_so_once(bar):
    bar.set(fixedNow=50000.0, drawer=True)
    for step in range(3):
        bar.set(fixedNow=50000.0 + step * 2000)
        bar.call("esc")
    assert bar.drain() == [{"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": True}]
    for step in range(3, 6):                          # the same burst goes on: still one
        bar.set(fixedNow=50000.0 + step * 2000)
        bar.call("esc")
    assert bar.drain() == []
    bar.set(fixedNow=90000.0)                         # a quiet spell, then another burst
    for step in range(3):
        bar.set(fixedNow=90000.0 + step * 500)
        bar.call("esc")
    assert len(bar.drain()) == 1


def test_escapes_with_nothing_up_or_too_slow_say_nothing(bar):
    bar.set(fixedNow=1000.0)
    for step in range(5):
        bar.set(fixedNow=1000.0 + step * 1000)
        bar.call("esc")
    assert bar.drain() == []                          # no drawer, no card: just Esc
    bar.set(drawer=True)
    for step in range(3):                             # three, but not within ten seconds of each other
        bar.set(fixedNow=100000.0 + step * 11000)
        bar.call("esc")
    assert bar.drain() == []


def test_escapes_at_a_peeked_card_count_with_the_card_not_the_drawer(bar):
    bar.noticed(OFFER)
    bar.peek()                                        # a peek ends with the pointer: Esc cannot close it
    bar.set(fixedNow=70000.0)
    for step in range(3):
        bar.set(fixedNow=70000.0 + step * 1000)
        bar.call("esc")
    assert bar.drain() == [{"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": False}]


def test_the_drawer_guess_follows_details_and_close(bar):
    bar.pill_send(kind="turn_start", turn=1, prompt="x")
    bar.pill_send(kind="turn_end", turn=1, seconds=1, changed=True, summary="Did it.")
    pill = bar.pill
    assert pill.property("drawerUp") is False
    bar.call("details", target=pill)
    assert pill.property("drawerUp") is True
    bar.call("details", target=pill)                  # a second Details on the same turn closes it
    assert pill.property("drawerUp") is False
    bar.call("details", target=pill)
    bar.call("closeDetails", target=pill)
    assert pill.property("drawerUp") is False


# -- the line's own Undo --

def test_a_receipt_with_its_own_undo_sends_that_message(bar):
    undo = {"type": "noticed_do", "op": "undo", "id": "w1"}
    bar.pill_send(kind="local", action="word", phase="done", ok=True,
                  text="Made “my passwords” open Passwords.", undo_msg=undo)
    line = bar.item("line")
    bar.pump(0.3)                                      # the line grows to its height first
    assert line.property("text") == "Made “my passwords” open Passwords."
    assert bar.shown("undoButton") and not bar.shown("detailsButton")
    assert bar.pill.property("sticky") is True        # it stays until the next prompt, like any change
    bar.snap("receipt-with-undo")
    bar.click(bar.item("undoButton"))
    assert bar.drain() == [undo]                      # not {"type": "local", "action": "undo"}
    assert bar.pill.property("sticky") is False


def test_a_local_line_without_undo_msg_has_no_undo(bar):
    bar.pill_send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert not bar.shown("undoButton") and not bar.shown("detailsButton")
    # and a turn's Undo is still the machine's
    bar.pill_send(kind="turn_start", turn=2, prompt="install ffmpeg")
    bar.pill_send(kind="turn_end", turn=2, seconds=3, changed=True, summary="Installed ffmpeg.")
    bar.drain()
    bar.click(bar.item("undoButton"))
    assert bar.drain() == [{"type": "local", "action": "undo"}]


def test_the_next_prompt_clears_a_receipts_undo(bar):
    undo = {"type": "noticed_do", "op": "undo", "id": "w1"}
    bar.pill_send(kind="local", action="word", phase="done", ok=True, text="Made a word.", undo_msg=undo)
    bar.call("submit", "hello", target=bar.pill)
    assert bar.pill.property("undoMsg") is None
    bar.pill_send(kind="turn_start", turn=3, prompt="hello")
    assert not bar.shown("undoButton")


def test_the_chip_and_card_load_without_qml_warnings(bar):
    bar.noticed(OFFER, FOUND, CHANGE, resting="3 Nov", lately="2 changes this week")
    bar.peek()
    bar.press("noticedOtherWays")
    bar.loop_send(type="noticed_result", op="preview", id="a1", ok=True, preview="x")
    bar.leave()
    bar.noticed(count=0, rows=[])
    assert bar.warnings == []


def test_a_rows_words_are_drawn_as_plain_text(bar):
    """The rows come from agentd as JSON: whatever they hold, the card draws it as plain text."""
    row = dict(OFFER, title="<b>show</b> me & my “passwords”")
    bar.noticed(json.loads(json.dumps(row)))
    bar.peek()
    assert bar.texts("noticedRowTitle") == ["<b>show</b> me & my “passwords”"]

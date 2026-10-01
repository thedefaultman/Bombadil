"""Notices on the line above the pill, driven by agentd's messages in an offscreen window.

PillState, StatusLine and NoticeChips are plain Qt Quick (Quickshell only wraps them in shell.qml), so
they load here without a compositor. Messages go in as JSON text through a QML function that parses
them, as shell.qml does: a Python dict handed to a QML function would not carry its lists the way a
parsed line does. Set BOMBADIL_SCREENS=<dir> to save a picture of each state.
"""

import json
import os
import re
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

SHELL = Path(__file__).resolve().parents[1] / "shell"

HARNESS = """
import QtQuick
import QtQuick.Layouts
import "%s"

Window {
    id: w
    width: 820; height: 260; visible: true
    color: "#3b4a5a"
    property var sent: []
    property int handOffs: 0
    // What shell.qml does with a line from agentd.
    function feed(line) { pillState.handle(JSON.parse(line)) }
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
        onHandOff: w.handOffs += 1
    }
    // A delegate, like the bar's PanelWindow in Variants: names resolve as they do in shell.qml.
    Repeater {
        model: 1
        ColumnLayout {
            parent: w.contentItem
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
            spacing: 8
            StatusLine { objectName: "statusLine"; pill: pillState; Layout.fillWidth: true }
            SetupChips { objectName: "setupChips"; pill: pillState; Layout.alignment: Qt.AlignHCenter }
            QueueChips { objectName: "chips"; pill: pillState; Layout.alignment: Qt.AlignHCenter }
            Rectangle { Layout.fillWidth: true; implicitHeight: 52; radius: 26; color: "#f01a1d21" }
        }
    }
}
"""


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
        QtCore.qInstallMessageHandler(lambda mode, ctx, msg: self.warnings.append(msg)
                                      if ".qml" in (ctx.file or "") or "TypeError" in msg else None)
        self.pill = self.win.findChild(QtCore.QObject, "pill")
        self.pump()

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    def feed(self, **msg):
        QtCore.QMetaObject.invokeMethod(self.win, "feed", QtCore.Q_ARG("QVariant", json.dumps(msg)))
        self.pump()

    def notice(self, id, line, tone="ask", actions=(), ttl=0, at=None, source="mail"):
        self.feed(type="notice", id=id, source=source, line=line, tone=tone, actions=list(actions), ttl=ttl,
                  at=at if at is not None else 1000.0 + id)

    def call(self, name, *args):
        ret = QtCore.QMetaObject.invokeMethod(
            self.pill, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return ret

    def send(self, **ev):
        """An event of the turn, as PillState.handle takes it (no lists in these)."""
        ev.setdefault("type", "event")
        QtCore.QMetaObject.invokeMethod(self.pill, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()

    def items(self, name, visible_only=True):
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return found

    def item(self, name):
        """The one on screen if any (the narrow line has a second, chips-only row), else any."""
        found = self.items(name) or self.items(name, visible_only=False)
        return found[0] if found else None

    def text(self, name="line"):
        return self.item(name).property("text")

    def chips(self):
        """The chips on screen, left to right, as (label, item)."""
        found = self.items("noticeChip")
        found.sort(key=lambda it: it.mapToScene(QtCore.QPointF(0, 0)).x())
        return [(next(c.property("text") for c in it.childItems() if c.property("text") is not None), it)
                for it in found]

    def labels(self):
        return [label for label, _ in self.chips()]

    def js(self, obj, name):
        value = obj.property(name)
        return value.toVariant() if hasattr(value, "toVariant") else value

    @property
    def sent(self):
        return [dict(m) for m in self.js(self.win, "sent")]

    @property
    def notices(self):
        return [dict(n) for n in self.js(self.pill, "notices")]

    def lines(self):
        return [n["line"] for n in self.notices]

    def centre(self, it):
        return it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()

    def click_item(self, it):
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, self.centre(it))
        self.pump()

    def hover(self, it):
        QtTest.QTest.mouseMove(self.win, self.centre(it))
        self.pump(0.2)

    def away(self):
        QtTest.QTest.mouseMove(self.win, QtCore.QPoint(5, 5))
        self.pump(0.2)

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.3)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"{name}.png"))


@pytest.fixture
def bar(app, tmp_path):
    b = Bar(app, tmp_path)
    b.send(type="status", busy=False, provider="claude", queue=[])
    yield b
    b.win.close()
    b.engine.deleteLater()
    b.pump()


REPLY = [{"id": "reply", "label": "Reply", "style": "primary"}, {"id": "open", "label": "Open", "style": "quiet"}]


def shown(bar):
    return bar.pill.property("noticeShown")


def test_a_notice_takes_the_idle_line_with_its_chips(bar):
    assert not shown(bar)
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY, ttl=300)
    assert shown(bar) and bar.text() == "Priya Shah: Launch date"
    assert bar.labels() == ["Reply", "Open"]
    assert bar.items("noticeMore") == []
    assert bar.item("statusLine").property("markEdge").name() == "#000000"   # news has no edge
    bar.snap("notice-1-new-mail")


def test_a_chip_sends_its_notice_and_its_own_id_and_lets_the_keyboard_go(bar):
    bar.notice(4, "Priya Shah: Launch date", actions=REPLY)
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "notice_action", "id": 4, "action": "reply"}
    assert bar.win.property("handOffs") == 1   # the window Reply opens must be able to take the keyboard
    bar.click_item(bar.chips()[1][1])
    assert bar.sent[-1] == {"type": "notice_action", "id": 4, "action": "open"}
    assert shown(bar)   # only agentd ends it: the answer to a chip may be a failure said on the notice itself


def test_the_primary_chip_is_orange_and_the_quiet_one_plain(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    bar.pump(0.4)   # the line fades in, and the border colour settles
    image = bar.win.grabWindow()

    def warmth(it):
        # The left edge of a chip is its border: orange is far redder than blue, grey is not.
        at = it.mapToScene(QtCore.QPointF(0, it.height() / 2)).toPoint()
        colours = [QtGui.QColor(image.pixel(at.x() + dx, at.y())) for dx in range(3)]
        return max(c.red() - c.blue() for c in colours)

    (_, primary), (_, quiet) = bar.chips()
    assert warmth(primary) > 100 and warmth(quiet) < 40


def test_the_cross_puts_it_away_and_tells_agentd(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    bar.click_item(bar.item("noticeDismiss"))
    assert bar.sent[-1] == {"type": "notice_dismiss", "id": 1}
    assert bar.notices == [] and not shown(bar)
    bar.feed(type="notice_end", id=1)   # agentd's own word on it changes nothing more
    assert bar.notices == []


def test_the_newest_notice_is_first_and_the_others_wait_behind_a_count(bar):
    bar.notice(1, "Leo Park: Quick question")
    bar.notice(2, "Sam Cole: Pricing copy")
    bar.notice(3, "Priya Shah: Launch date")
    assert bar.text() == "Priya Shah: Launch date"
    assert bar.text("noticeMore") == "+2"
    bar.snap("notice-2-more-waiting")
    bar.feed(type="notice_end", id=3)
    assert bar.text() == "Sam Cole: Pricing copy" and bar.text("noticeMore") == "+1"
    bar.feed(type="notice_end", id=2)
    assert bar.text() == "Leo Park: Quick question" and bar.items("noticeMore") == []
    bar.feed(type="notice_end", id=1)
    bar.pump(0.4)   # it fades out
    assert not shown(bar) and not bar.item("statusLine").isVisible()


def test_a_warning_stays_ahead_of_news(bar):
    bar.notice(1, "I can't tell whether that went. Look in Sent before you press Send again.", tone="error")
    bar.notice(2, "Priya Shah: Launch date", actions=REPLY)
    bar.notice(3, "Leo Park: Quick question", actions=REPLY)
    assert bar.text().startswith("I can't tell") and bar.text("noticeMore") == "+2"
    assert bar.item("statusLine").property("markEdge").name() == THEME["bad"]
    assert bar.item("statusLine").property("errored") is True
    bar.snap("notice-3-warning")
    bar.notice(4, "That was not your press on Send.", tone="error")
    assert bar.text() == "That was not your press on Send."   # newest first among warnings
    assert bar.lines()[1].startswith("I can't tell")


def test_a_changed_notice_keeps_its_id_and_moves_to_the_front(bar):
    bar.notice(1, "Reply to Priya is ready. Sending is yours.", actions=[{"id": "open", "label": "Open"}])
    bar.notice(2, "Leo Park: Quick question", actions=REPLY)
    assert bar.text() == "Leo Park: Quick question"
    bar.notice(1, "I could not make that draft.", tone="error", actions=[], at=2000.0)
    assert len(bar.notices) == 2
    assert bar.text() == "I could not make that draft." and bar.labels() == []
    assert bar.item("statusLine").property("errored") is True


def test_a_turn_line_wins_and_the_notice_comes_back_when_it_fades(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    assert bar.call("submit", "what time is it") is True
    bar.send(kind="turn_start", turn=1, prompt="what time is it")
    assert bar.text() == "On it" and not shown(bar) and bar.chips() == []
    bar.send(kind="result", turn=1, ok=True, text="It is 21:40 in Tokyo.")
    bar.send(kind="turn_end", turn=1, seconds=2, changed=False)
    assert bar.text() == "It is 21:40 in Tokyo." and not shown(bar) and bar.chips() == []
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.pill.property("mode") == "idle" and shown(bar)
    assert bar.text() == "Priya Shah: Launch date" and bar.labels() == ["Reply", "Open"]


def test_a_notice_that_comes_while_a_turn_runs_waits_for_it(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system", command="sudo pacman -S ffmpeg")
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    assert bar.text() == "Installing ffmpeg" and bar.chips() == []
    assert bar.item("statusLine").property("edge").name() == THEME["warn"]
    bar.send(kind="turn_end", turn=1, seconds=3, changed=False, summary="Installed ffmpeg.")
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.text() == "Priya Shah: Launch date"


def test_an_answer_to_something_typed_covers_a_notice_for_a_moment(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    bar.call("submit", "browser")
    bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert bar.text() == "Opened the browser." and bar.chips() == []
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.text() == "Priya Shah: Launch date"


def test_the_setup_line_has_the_line_until_the_machine_can_talk(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    bar.feed(type="setup", state="choose", line="Which AI should run this computer?", tone="ask",
             actions=[{"id": "provider:claude", "label": "Claude", "style": "big"}])
    assert bar.text() == "Which AI should run this computer?" and bar.chips() == []
    bar.feed(type="setup", state="ready", line="", tone="done", actions=[])
    assert bar.text() == "Priya Shah: Launch date" and bar.labels() == ["Reply", "Open"]


def test_a_notice_does_not_wear_the_last_turns_error_and_its_edge_is_its_own(bar):
    bar.call("submit", "hello")
    bar.send(kind="turn_start", turn=9, prompt="hello")
    bar.send(kind="error", turn=9, text="claude is not logged in.")
    bar.send(kind="result", turn=9, ok=False, text="")
    bar.send(kind="turn_end", turn=9, seconds=1, changed=False)
    assert bar.item("statusLine").property("markEdge").name() == THEME["bad"]
    bar.call("dismiss")
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    assert bar.pill.property("source") == "error"   # stale, and none of the notice's business
    assert bar.item("statusLine").property("errored") is False
    assert bar.item("statusLine").property("markEdge").name() == "#000000"


def test_a_line_that_ends_while_you_read_it_stays_without_its_chips_until_you_leave(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY, ttl=5)
    bar.hover(bar.item("statusLine"))
    assert bar.pill.property("hovers") == 1
    bar.feed(type="notice_end", id=1)
    assert bar.text() == "Priya Shah: Launch date" and bar.chips() == []   # agentd has let go of it
    assert [n["ended"] for n in bar.notices] == [True]
    bar.pump(0.6)
    assert shown(bar)
    bar.away()
    assert bar.notices == [] and not shown(bar)


def test_a_line_read_but_not_ended_is_left_alone_and_one_not_in_front_goes_at_once(bar):
    bar.notice(1, "Leo Park: Quick question", actions=REPLY)
    bar.notice(2, "Priya Shah: Launch date", actions=REPLY)
    bar.hover(bar.item("statusLine"))
    bar.feed(type="notice_end", id=1)   # behind the one on the line: nothing to keep
    assert bar.lines() == ["Priya Shah: Launch date"]
    bar.feed(type="notice_end", id=2)
    bar.notice(3, "Sam Cole: Pricing copy", actions=REPLY)   # news while the old line is kept: it takes over
    assert bar.lines() == ["Sam Cole: Pricing copy"] and bar.labels() == ["Reply", "Open"]
    bar.away()
    assert bar.lines() == ["Sam Cole: Pricing copy"]


def test_losing_agentd_drops_its_notices_and_the_new_one_sends_what_is_live(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    bar.notice(2, "Leo Park: Quick question", actions=REPLY)
    QtCore.QMetaObject.invokeMethod(bar.pill, "lost")
    bar.pump()
    assert bar.notices == [] and not shown(bar)
    before = len(bar.sent)
    bar.call("noticeAction", 2, "reply")
    bar.call("dismissNotice", 2)
    assert len(bar.sent) == before   # nothing reaches agentd while the socket is down
    bar.send(type="status", busy=False, provider="claude", queue=[])
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    assert bar.lines() == ["Priya Shah: Launch date"]


def test_a_press_goes_to_the_notice_it_was_drawn_for(bar):
    bar.notice(1, "Leo Park: Quick question", actions=REPLY)
    bar.notice(2, "Priya Shah: Launch date", actions=REPLY)
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "notice_action", "id": 2, "action": "reply"}
    # Each chip carries its own notice's id, so a stale one could not be pressed for the newer.
    assert [a["notice"] for a in bar.notices[0]["actions"]] == [2, 2]
    assert [a["notice"] for a in bar.notices[1]["actions"]] == [1, 1]


def test_a_cross_pressed_as_a_newer_notice_arrives_puts_nothing_away(bar):
    bar.notice(1, "Leo Park: Quick question", actions=REPLY)
    cross = bar.item("noticeDismiss")
    where = bar.centre(cross)
    QtTest.QTest.mousePress(bar.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, where)
    bar.pump()
    bar.notice(2, "Priya Shah: Launch date", actions=REPLY)
    QtTest.QTest.mouseRelease(bar.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, where)
    bar.pump()
    assert not any(m["type"] == "notice_dismiss" for m in bar.sent)
    assert bar.lines() == ["Priya Shah: Launch date", "Leo Park: Quick question"]


def test_a_notice_is_plain_text_on_one_line_and_cut_at_a_sane_length(bar):
    bar.notice(1, "<b>Priya</b>\u202e\ngpj.exe\x00 <img src=x onerror=alert(1)>", actions=[
        {"id": "reply", "label": "Re\u202eply\n", "style": "weird"}])
    line = bar.notices[0]["line"]
    assert line == "<b>Priya</b> gpj.exe <img src=x onerror=alert(1)>"
    assert bar.text() == line   # markup stays what it is: the Text is PlainText, so tags are drawn, not obeyed
    assert bar.labels() == ["Re ply"] and bar.notices[0]["actions"][0]["style"] == "quiet"
    bar.notice(2, "x" * 1000)
    assert len(bar.notices[0]["line"]) == 400 and bar.notices[0]["line"].endswith("…")


def test_what_a_notice_may_carry_is_checked(bar):
    bar.feed(type="notice", id="x", line="not an id")
    bar.feed(type="notice", id=3)   # no words
    bar.feed(type="notice", id=4, line="Four chips", tone="shout", actions=[
        {"id": "a", "label": "A"}, {"label": "no id"}, {"id": "", "label": "empty id"}, {"id": "b", "label": ""},
        {"id": "c", "label": "C", "style": "primary"}, {"id": "d", "label": "D"}, {"id": "e", "label": "E"}])
    assert [n["id"] for n in bar.notices] == [4]
    assert bar.notices[0]["tone"] == "step"
    assert [a["id"] for a in bar.notices[0]["actions"]] == ["a", "c", "d"]   # three at most
    assert bar.labels() == ["A", "C", "D"]


def test_a_runaway_cannot_grow_the_stack_and_never_costs_a_warning(bar):
    bar.notice(1, "Look in Sent before you press Send again.", tone="error")
    for i in range(2, 40):
        bar.notice(i, f"Mail {i}")
    assert len(bar.notices) == 8
    assert bar.notices[0]["id"] == 1 and bar.text().startswith("Look in Sent")
    assert [n["id"] for n in bar.notices[1:]] == list(range(39, 32, -1))


def test_the_pill_says_what_the_line_shows(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    ret = QtCore.QMetaObject.invokeMethod(bar.pill, "snapshot", QtCore.Qt.DirectConnection,
                                          QtCore.Q_RETURN_ARG("QVariant"))
    state = ret.toVariant() if hasattr(ret, "toVariant") else ret
    assert state["noticeShown"] is True and state["mode"] == "idle"
    assert [n["line"] for n in state["notices"]] == ["Priya Shah: Launch date"]


def test_on_a_narrow_line_the_chips_go_under_the_words_and_still_answer(bar):
    bar.win.setWidth(400)   # a window shares the stage: the bar is as wide as the pill may be
    bar.pump()
    bar.notice(7, "Priya Shah: Launch date, and the plan for the week after", actions=REPLY)
    line = bar.item("statusLine")
    assert line.property("narrow") is True
    assert bar.items("noticeRow") and bar.labels() == ["Reply", "Open"]
    words, cross = bar.item("line"), bar.item("noticeDismiss")
    where = [it.mapToScene(QtCore.QPointF(0, 0)) for it in (words, cross)]
    for _, chip in bar.chips():
        at = chip.mapToScene(QtCore.QPointF(0, 0))
        assert at.y() >= where[0].y() + words.height() - 1   # under the words, not beside them
        assert at.x() + chip.width() <= line.mapToScene(QtCore.QPointF(line.width(), 0)).x()
    assert cross.isVisible() and where[1].y() < bar.chips()[0][1].mapToScene(QtCore.QPointF(0, 0)).y()
    bar.snap("notice-4-narrow")
    bar.pump(0.3)   # the line grows to take the second row; a chip outside it is clipped, not pressable
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "notice_action", "id": 7, "action": "reply"}
    bar.notice(8, "Just so you know")   # no chips: no second row
    assert bar.chips() == [] and bar.items("noticeRow") == []
    bar.win.setWidth(820)
    bar.pump()
    bar.notice(9, "Priya Shah: Launch date", actions=REPLY)
    assert line.property("narrow") is False and bar.items("noticeRow") == []
    assert bar.labels() == ["Reply", "Open"]


UNSURE = "I can't tell whether that went. Look in Sent before you press Send again."
RECEIPT = "Sent to Priya from maya@acme.example · 09:08"


def changed_turn(bar):
    """A turn that changed something: its closing line stays, with Undo, until the next prompt."""
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    assert bar.pill.property("sticky") and bar.text() == "Installed ffmpeg."


def test_a_warning_is_read_over_a_closing_line_that_would_stay_for_good(bar):
    changed_turn(bar)
    bar.pill.setProperty("fadeAfter", 100)
    bar.notice(1, UNSURE, tone="error", ttl=0)
    bar.notice(2, RECEIPT, tone="done", ttl=120)
    # Not after the line's 12 s: at once, since a sticky line never fades by itself.
    assert shown(bar) and bar.text() == UNSURE and bar.text("noticeMore") == "+1"
    assert bar.items("undoButton") == [] and bar.items("detailsButton") == []   # the notice has the line
    bar.click_item(bar.item("line"))
    assert not any(m["type"] == "details" for m in bar.sent)   # a tap on a warning is not a tap on the turn
    bar.away()   # a line under the pointer is kept; once it is gone, the finished line under the warning ages
    bar.pump(0.6)
    assert bar.pill.property("mode") == "idle" and shown(bar) and bar.text() == UNSURE
    bar.click_item(bar.item("noticeDismiss"))
    assert bar.text() == RECEIPT   # the receipt is next, not the Installed line that already went


def test_news_waits_behind_a_sticky_line_and_comes_when_it_gives_way(bar):
    changed_turn(bar)
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    assert not shown(bar) and bar.text() == "Installed ffmpeg." and bar.chips() == []
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)   # a sticky line with nothing waiting would stay; this one has something waiting
    assert bar.pill.property("mode") == "idle" and not bar.pill.property("sticky")
    assert shown(bar) and bar.text() == "Priya Shah: Launch date" and bar.labels() == ["Reply", "Open"]


def test_a_sticky_line_with_no_notice_waiting_stays_with_its_undo(bar):
    changed_turn(bar)
    bar.pill.setProperty("fadeAfter", 100)
    bar.pump(0.8)
    assert bar.pill.property("mode") == "closing" and bar.pill.property("sticky")
    assert len(bar.items("undoButton")) == 1


def test_a_warning_waits_for_a_running_turn_and_is_there_when_it_ends(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system", command="sudo pacman -S ffmpeg")
    bar.notice(1, "That was not your press on Send.", tone="error")
    assert bar.text() == "Installing ffmpeg" and not shown(bar)   # the step with its command is not covered
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    assert shown(bar) and bar.text() == "That was not your press on Send."   # no wait for the line to fade


def test_a_warning_is_read_over_an_answer_and_over_the_setup_line_and_news_is_not(bar):
    bar.feed(type="setup", state="signed_out", line="Claude signed you out.", tone="error",
             actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    assert not shown(bar) and bar.text() == "Claude signed you out."   # news waits for the machine to be ready
    bar.notice(2, UNSURE, tone="error")
    assert bar.text() == UNSURE and bar.pill.property("mode") == "setup"
    bar.click_item(bar.item("noticeDismiss"))
    assert bar.text() == "Claude signed you out." and not shown(bar)
    bar.feed(type="setup", state="ready", line="", tone="done", actions=[])
    assert bar.text() == "Priya Shah: Launch date"
    bar.call("submit", "browser")
    bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert bar.pill.property("mode") == "local" and not shown(bar)
    bar.notice(3, UNSURE, tone="error")
    assert bar.text() == UNSURE and shown(bar)


def test_a_chip_the_person_pressed_is_gone_when_agentd_ends_it_though_the_pointer_is_on_it(bar):
    bar.notice(1, "Leo Park: Quick question", actions=REPLY)
    bar.notice(2, "Priya Shah: Launch date", actions=REPLY)
    reply = bar.chips()[0][1]
    bar.hover(reply)
    assert bar.pill.property("hovers") == 1
    bar.click_item(reply)
    assert bar.sent[-1] == {"type": "notice_action", "id": 2, "action": "reply"}
    bar.feed(type="notice_end", id=2)   # agentd answered the press and ended it
    assert bar.lines() == ["Leo Park: Quick question"] and bar.labels() == ["Reply", "Open"]
    assert bar.pill.property("hovers") == 1   # still over the line, now the next one's
    # Only a press counts: the one nobody pressed is still kept while it is read.
    bar.feed(type="notice_end", id=1)
    assert [n["ended"] for n in bar.notices] == [True] and bar.labels() == []
    bar.away()
    assert bar.notices == []


def test_a_chip_pressed_before_a_change_is_not_remembered_for_the_changed_notice(bar):
    bar.notice(1, "Reply to Priya is ready. Sending is yours.", actions=REPLY)
    bar.hover(bar.chips()[0][1])
    bar.click_item(bar.chips()[0][1])
    bar.notice(1, "I could not make that draft.", tone="error", actions=[], at=2000.0)   # the press failed
    bar.feed(type="notice_end", id=1)   # later, by itself
    assert [n["ended"] for n in bar.notices] == [True]   # read until the pointer leaves, as any other
    bar.away()
    assert bar.notices == []


def test_the_pointer_over_anything_else_on_the_stage_keeps_an_ended_notice_too(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY)
    bar.pill.setProperty("hovers", 1)   # what a picture above the line adds: the count is the stage's, not the line's
    bar.feed(type="notice_end", id=1)
    assert bar.lines() == ["Priya Shah: Launch date"] and bar.labels() == []
    bar.pill.setProperty("hovers", 0)
    bar.pump()
    assert bar.notices == []


def test_a_line_with_nothing_to_say_is_no_notice(bar):
    bar.feed(type="notice", id=1, line="  \u202e \n\t\u2066 ", tone="ask", actions=REPLY)
    bar.feed(type="notice", id=2, line="", tone="ask", actions=REPLY)
    bar.feed(type="notice", id=3, line=None, tone="ask", actions=REPLY)
    assert bar.notices == [] and not shown(bar)
    bar.notice(4, "Priya Shah: Launch date", actions=REPLY)
    bar.feed(type="notice", id=4, line="\u202e", tone="error", actions=[])   # a change that says nothing
    assert bar.lines() == ["Priya Shah: Launch date"]   # leaves what was said


def test_an_id_is_a_number_whichever_way_it_comes(bar):
    bar.feed(type="notice", id="5", line="Priya Shah: Launch date", tone="ask", actions=REPLY)
    assert [n["id"] for n in bar.notices] == [5]
    bar.feed(type="notice_end", id="5")
    assert bar.notices == []
    bar.notice(6, "Leo Park: Quick question")
    bar.feed(type="notice_end", id="not a number")
    bar.feed(type="notice_end")
    assert [n["id"] for n in bar.notices] == [6]


def test_the_shell_serves_the_line_to_the_smoke_and_takes_a_notice_by_ipc_only_for_a_test():
    shell = (SHELL / "shell.qml").read_text()
    # The VM smoke and the desktop test read the line through this; the offscreen harness has no Quickshell.
    assert re.search(r'target: "line"[^}]*function state\(\): string \{ return JSON.stringify\(pillState.snapshot\(\)\) \}', shell)
    # Anything of the person's, an agent's shell included, can call `desk inject`: it may not forge or end a notice.
    inject = shell[shell.index("function inject("):]
    inject = inject[:inject.index("\n        }\n") + 1]
    assert 'startsWith("notice")' in inject and 'Quickshell.env("BOMBADIL_BAR_INJECT") !== "1"' in inject
    assert inject.index("return") < inject.index("root.handle(message)")
    assert 'BOMBADIL_BAR_INJECT="1"' in (Path(__file__).parent / "desktop/driver.py").read_text()
    # And nothing on the image sets it.
    iso = SHELL.parent / "iso"
    assert [str(f) for f in iso.rglob("*") if f.is_file() and not f.is_symlink() and f.stat().st_size < 1 << 20
            and "BOMBADIL_BAR_INJECT" in f.read_text(errors="ignore")] == []


def test_the_bar_loads_without_qml_warnings(bar):
    bar.notice(1, "Priya Shah: Launch date", actions=REPLY, ttl=300)
    bar.notice(2, "Sorry", tone="error")
    bar.click_item(bar.item("noticeDismiss"))
    bar.feed(type="notice_end", id=1)
    bar.call("submit", "hello")
    bar.send(kind="turn_start", turn=1, prompt="hello")
    bar.send(kind="turn_end", turn=1, seconds=1, changed=True, summary="Did it.")
    assert bar.warnings == []

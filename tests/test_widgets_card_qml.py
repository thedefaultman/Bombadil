"""The Widgets card, drawn against a real DeskState in an offscreen window.

WidgetsCard and DeskState are plain Qt Quick (Quickshell only wraps them in shell.qml), so they load here
without a compositor, laid out the way the bar's column lays them out: a card of its own width, centred
above a pill. Set BOMBADIL_SCREENS=<dir> to save a picture of the card in each of Machine's three states.
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
import QtQuick.Controls
import QtQuick.Layouts
import "%s"

Window {
    id: w
    width: 420; height: 560; visible: true
    color: "#3b4a5a"
    property var sent: []
    property bool active: true
    // What shell.qml does with a line from agentd.
    function feed(line) { deskState.handle(JSON.parse(line)) }
    // What a test cannot read from outside (grouped and enum properties), walked here: every colour on the card, and
    // what each of its texts is and does.
    function paint() {
        const out = []
        const walk = it => {
            if (it.color !== undefined) out.push(String(it.color))
            if (it.border !== undefined && it.border.width > 0) out.push(String(it.border.color))
            for (let i = 0; i < it.children.length; i++) walk(it.children[i])
        }
        walk(card)
        return out
    }
    function texts() {
        const out = []
        const walk = it => {
            if (it.text !== undefined && it.font !== undefined)
                out.push({ text: it.text, family: it.font.family, plain: it.textFormat === Text.PlainText,
                           truncated: it.truncated, painted: it.paintedWidth, width: it.width, color: String(it.color),
                           name: it.objectName, parent: it.parent ? it.parent.objectName : "",
                           size: it.font.pixelSize, weight: it.font.weight })
            for (let i = 0; i < it.children.length; i++) walk(it.children[i])
        }
        walk(card)
        return out
    }
    function border() { return { width: card.border.width, color: String(card.border.color) } }
    // The controls (the pill-shaped group of three) and their parts: what a restyle would move.
    function controls() {
        const out = []
        const walk = it => {
            const n = it.objectName || ""
            if (n.startsWith("widgetsControl-"))
                out.push({ name: n, border: it.border.width, color: String(it.border.color), height: it.height })
            if (n.startsWith("widgetsPart-"))
                out.push({ name: n, height: it.height, width: it.width, label: it.children[0].implicitWidth })
            for (let i = 0; i < it.children.length; i++) walk(it.children[i])
        }
        walk(card)
        return out
    }
    DeskState {
        id: deskState
        objectName: "desk"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
    }
    // The bar's column: the card as wide as it is (not the bar), centred above the pill.
    ColumnLayout {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
        spacing: 8
        WidgetsCard {
            id: card
            desk: deskState
            active: w.active
            Layout.fillWidth: false
            Layout.alignment: Qt.AlignHCenter
        }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: 52; radius: 26; color: "#f01a1d21"
            // The pill's field: it holds the keyboard while the card is up, and a press on the card must not take it.
            TextField { objectName: "input"; anchors.fill: parent; focus: true; background: null }
        }
    }
}
"""

QUESTIONS = {"now": "What is it doing, and why?", "watching": "What is it keeping an eye on?",
             "needs": "What is waiting for me?", "machine": "Is the machine in trouble?"}
ROWS = [{"id": "now", "title": "Now", "question": QUESTIONS["now"], "can": ["needed", "off"]},
        {"id": "watching", "title": "Watching", "question": QUESTIONS["watching"], "can": ["needed", "off"]},
        {"id": "needs", "title": "Needs you", "question": QUESTIONS["needs"], "can": ["needed"]},
        {"id": "machine", "title": "Machine", "question": QUESTIONS["machine"], "can": ["keep", "needed", "off"]}]
STATES = ("keep", "needed", "off")


def plain(v):
    return v.toVariant() if hasattr(v, "toVariant") else v


def hexa(colour):
    """A QML colour as #AARRGGBB, the way Theme.qml writes it."""
    return QtGui.QColor(colour).name(QtGui.QColor.HexArgb)


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Card:
    def __init__(self, app, tmp_path):
        self.app = app
        self.warnings = []
        qml = tmp_path / "harness.qml"
        qml.write_text(HARNESS % SHELL.as_uri())
        self.engine = QtQml.QQmlApplicationEngine()
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.previous = QtCore.qInstallMessageHandler(
            lambda mode, ctx, msg: self.warnings.append(msg) if ".qml" in (ctx.file or "") or "TypeError" in msg else None)
        self.win = self.engine.rootObjects()[0]
        self.desk = self.win.findChild(QtCore.QObject, "desk")
        self.pump()

    def close(self):
        self.win.close()
        self.engine.deleteLater()
        self.pump()
        QtCore.qInstallMessageHandler(self.previous)

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    # -- driving --

    def send(self, **ev):
        """A line from agentd, as shell.qml hands it to the desk."""
        QtCore.QMetaObject.invokeMethod(self.win, "feed", QtCore.Q_ARG("QVariant", json.dumps(ev)))
        self.pump()

    def state(self, kept=(), hidden=(), widgets=None):
        """agentd's desk message, with what the card reads in it."""
        self.send(type="desk", kept=list(kept), hidden=list(hidden), widgets=ROWS if widgets is None else widgets)

    def open(self, **kw):
        """The card as the person finds it: agentd there, the desk said, the card asked for, the fade over."""
        self.desk.setProperty("connected", True)
        self.state(**kw)
        self.send(type="widgets", op="open")
        self.pump(0.4)
        self.win.setProperty("sent", [])        # the desk's hello is not what a press said

    def call(self, name, *args, wait=True):
        """A call on the desk; `wait=False` is for what the next thing that happens must find before any frame has."""
        ret = QtCore.QMetaObject.invokeMethod(self.desk, name, QtCore.Qt.DirectConnection,
                                              QtCore.Q_RETURN_ARG("QVariant"), *[QtCore.Q_ARG("QVariant", a) for a in args])
        if wait:
            self.pump()
        return plain(ret)

    def prop(self, name):
        return plain(self.desk.property(name))

    def ask(self, name):
        """What a harness function reads out of the card's tree (see paint, texts and border in the harness)."""
        ret = QtCore.QMetaObject.invokeMethod(self.win, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"))
        return plain(ret)

    @property
    def sent(self):
        return [dict(m) for m in plain(self.win.property("sent"))]

    # -- looking --

    def items(self, name, visible_only=False, root=None):
        found, todo = [], [root or self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return found

    def item(self, name, root=None):
        found = self.items(name, root=root)
        assert len(found) == 1, f"{name}: {len(found)} found"
        return found[0]

    @property
    def card(self):
        return self.item("widgetsCard")

    def row(self, wid):
        return self.item(f"widgetsRow-{wid}")

    def part(self, wid, state):
        return self.item(f"widgetsPart-{wid}-{state}")

    def text_of(self, item):
        """The Text a part, the close or the reset holds."""
        todo = list(item.childItems())
        while todo:
            it = todo.pop(0)
            if it.property("text") is not None:
                return it
            todo.extend(it.childItems())
        raise AssertionError(item.objectName())

    def rect(self, item):
        """x, y, w, h of an item in the card's own coordinates."""
        p = item.mapToItem(self.card, QtCore.QPointF(0, 0))
        return p.x(), p.y(), item.width(), item.height()

    def centre(self, item):
        return item.mapToScene(QtCore.QPointF(item.width() / 2, item.height() / 2)).toPoint()

    def click(self, item):
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, self.centre(item))
        self.pump()

    def click_at(self, item, x, y):
        p = item.mapToScene(QtCore.QPointF(x, y)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p)
        self.pump()

    def press(self, item):
        QtTest.QTest.mousePress(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, self.centre(item))
        self.pump()

    def release(self, point):
        QtTest.QTest.mouseRelease(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.pump()

    def hover(self, item):
        QtTest.QTest.mouseMove(self.win, self.centre(item))
        self.pump(0.1)

    def away(self):
        QtTest.QTest.mouseMove(self.win, QtCore.QPoint(2, 2))
        self.pump(0.1)

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.4)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"widgets-{name}.png"))


@pytest.fixture
def card(app, tmp_path):
    c = Card(app, tmp_path)
    yield c
    c.close()


def message(wid, state):
    return {"type": "desk", "op": "set", "widget": wid, "state": state}


def test_the_card_in_machines_three_states(card):
    for name, kw in (("machine-kept", {"kept": ["machine"]}), ("machine-needed", {}), ("machine-off", {"hidden": ["machine"]})):
        card.open(**kw)
        card.snap(name)
        assert card.part("machine", {"machine-kept": "keep", "machine-needed": "needed", "machine-off": "off"}[name]) \
            .property("color").name(QtGui.QColor.HexArgb) == hexa(THEME["raised"])


# -- what is on it --

def test_every_item_the_tests_and_the_driver_look_for_is_there(card):
    card.open()
    assert card.item("widgetsTitle").property("text") == "Widgets"
    assert card.text_of(card.item("widgetsClose")).property("text") == "×"
    assert card.text_of(card.item("widgetsReset")).property("text") == "Reset the desk"
    assert [r["id"] for r in ROWS] == ["now", "watching", "needs", "machine"]
    for r in ROWS:
        row = card.row(r["id"])
        assert card.item("widgetsName", root=row).property("text") == r["title"]
        assert card.item("widgetsQuestion", root=row).property("text") == r["question"]
        labels = [card.text_of(card.part(r["id"], s)).property("text") for s in STATES]
        assert labels == ["Keep", "When needed", "Off"]
    # One row for each widget and no more, in the order agentd listed them, top down.
    rows = sorted((i for i in card.items("widgetsName")), key=lambda i: card.rect(i)[1])
    assert [i.property("text") for i in rows] == ["Now", "Watching", "Needs you", "Machine"]
    assert len(card.items("widgetsCard")) == len(card.items("widgetsTitle")) == len(card.items("widgetsReset")) == 1


def test_the_rows_follow_the_list_agentd_sends(card):
    card.open()
    card.state(widgets=list(reversed(ROWS)))
    by_place = sorted(card.items("widgetsName"), key=lambda i: card.rect(i)[1])
    assert [i.property("text") for i in by_place] == ["Machine", "Needs you", "Watching", "Now"]
    card.state(widgets=ROWS[:2])
    assert [i.property("text") for i in sorted(card.items("widgetsName"), key=lambda i: card.rect(i)[1])] == ["Now", "Watching"]
    assert not card.items("widgetsRow-machine") and not card.items("widgetsPart-machine-off")
    card.state(widgets=[dict(ROWS[3], title="Machine room", question="Is it hot?")] + ROWS[:1])
    assert card.item("widgetsName", root=card.row("machine")).property("text") == "Machine room"
    assert card.item("widgetsQuestion", root=card.row("machine")).property("text") == "Is it hot?"
    assert card.warnings == []


def test_it_is_the_ai_cards_shape(card):
    card.open()
    c = card.card
    assert c.width() == 360 and c.property("radius") == THEME["radiusLine"] == 14
    assert hexa(c.property("color")) == hexa(THEME["glassLine"])
    border = card.ask("border")
    assert border["width"] == 1 and QtGui.QColor(border["color"]).name() == QtGui.QColor(THEME["border"]).name()
    assert c.property("clip") is True and c.property("opacity") == 1 and c.isVisible()
    # As wide as it is, not as wide as the bar, and in the middle of it.
    assert c.width() < card.win.width() - 24
    assert c.mapToScene(QtCore.QPointF(c.width() / 2, 0)).x() == pytest.approx(card.win.width() / 2, abs=1)
    # Padding as the AI card's: 18 on the left, 12 on the others, and it ends 12 under the last thing.
    assert card.rect(card.item("widgetsTitle"))[0] == 18
    assert card.rect(card.item("widgetsClose"))[0] + 24 == 360 - 12
    reset = card.rect(card.item("widgetsReset"))
    assert reset[0] == 18 and reset[1] + reset[3] + 12 == c.height()
    assert card.rect(card.item("widgetsTitle"))[1] >= 12
    desk_texts = card.ask("texts")
    assert desk_texts and all(t["family"] == THEME["fontFamily"] and t["plain"] for t in desk_texts)


def test_its_text_has_the_sizes_weights_and_colours_the_brief_gives_it(card):
    # Title 14 px semibold in the primary colour, each name 13 px in it, each question 12 px muted, the parts 12 px,
    # and the controls a 1 px hairline with parts 22 px high and 10 px either side of their words.
    card.open()
    same = lambda a, b: QtGui.QColor(a).name() == QtGui.QColor(b).name()
    texts = card.ask("texts")
    title = [t for t in texts if t["name"] == "widgetsTitle"]
    assert len(title) == 1 and title[0]["size"] == THEME["textSize"] == 14 and same(title[0]["color"], THEME["fg"])
    assert title[0]["weight"] == int(QtGui.QFont.DemiBold) == 600
    names = [t for t in texts if t["name"] == "widgetsName"]
    assert len(names) == len(ROWS)
    assert all(t["size"] == THEME["smallSize"] == 13 and same(t["color"], THEME["fg"]) for t in names)
    assert all(t["weight"] == int(QtGui.QFont.Normal) for t in names)
    questions = [t for t in texts if t["name"] == "widgetsQuestion"]
    assert len(questions) == len(ROWS)
    assert all(t["size"] == THEME["captionSize"] == 12 and same(t["color"], THEME["muted"]) for t in questions)
    labels = [t for t in texts if t["parent"].startswith("widgetsPart-")]
    assert len(labels) == 3 * len(ROWS) and all(t["size"] == 12 for t in labels)
    assert {t["text"] for t in labels} == {"Keep", "When needed", "Off"}
    allowed = {QtGui.QColor(THEME[k]).name() for k in ("fg", "muted", "faint")}
    assert {QtGui.QColor(t["color"]).name() for t in labels} <= allowed
    controls = card.ask("controls")
    wrappers = [c for c in controls if c["name"].startswith("widgetsControl-")]
    parts = [c for c in controls if c["name"].startswith("widgetsPart-")]
    assert len(wrappers) == len(ROWS) and len(parts) == 3 * len(ROWS)
    assert all(c["border"] == 1 and same(c["color"], THEME["border"]) and c["height"] == 24 for c in wrappers)
    assert all(c["height"] == 22 and c["width"] == pytest.approx(c["label"] + 20) for c in parts)


def test_it_is_there_only_with_the_card_asked_for_rows_to_draw_and_the_keyboard_on_this_screen(card):
    c = card.card
    card.desk.setProperty("connected", True)
    card.state()
    assert not c.isVisible() and c.property("implicitHeight") == 0 and c.property("shown") is False
    card.send(type="widgets", op="open")
    card.pump(0.4)
    assert c.isVisible() and c.property("opacity") == 1 and c.property("height") > 250
    # The screen that does not hold the keyboard draws nothing, whatever the desk says.
    card.win.setProperty("active", False)
    card.pump(0.4)
    assert not c.isVisible() and c.property("implicitHeight") == 0 and card.prop("widgetsOpen") is True
    card.win.setProperty("active", True)
    card.pump(0.4)
    assert c.isVisible()
    # No rows, no card: there is nothing to say on it.
    card.state(widgets=[])
    card.pump(0.4)
    assert not c.isVisible() and c.property("implicitHeight") == 0
    card.state()
    card.pump(0.4)
    assert c.isVisible()
    card.call("closeWidgets")
    card.pump(0.4)
    assert not c.isVisible() and c.property("implicitHeight") == 0
    assert card.warnings == []


def test_it_fades_and_does_not_snap(card):
    card.desk.setProperty("connected", True)
    card.state()
    c = card.card
    card.send(type="widgets", op="open")
    assert 0 < c.property("opacity") < 1 and 0 < c.property("height") < 320       # on its way in
    card.pump(0.4)
    assert c.property("opacity") == 1 and c.property("height") == 320
    card.call("closeWidgets")
    assert 0 < c.property("opacity") < 1                                          # and on its way out
    card.pump(0.4)
    assert not c.isVisible()


# -- the three states --

def test_the_part_that_is_so_is_raised_with_fg_text_and_the_others_are_not(card):
    raised, fg, muted, faint = (hexa(THEME[k]) for k in ("raised", "fg", "muted", "faint"))
    for kw, want in (({}, {"now": "needed", "watching": "needed", "needs": "needed", "machine": "needed"}),
                     ({"kept": ["machine"], "hidden": ["now"]},
                      {"now": "off", "watching": "needed", "needs": "needed", "machine": "keep"}),
                     ({"hidden": ["machine", "watching"]},
                      {"now": "needed", "watching": "off", "needs": "needed", "machine": "off"})):
        card.open(**kw)
        for r in ROWS:
            for state in STATES:
                part = card.part(r["id"], state)
                text = hexa(card.text_of(part).property("color"))
                if state == want[r["id"]]:
                    assert hexa(part.property("color")) == raised and text == fg, (r["id"], state)
                else:
                    assert part.property("color").alpha() == 0, (r["id"], state)          # nothing is filled
                    assert text == (muted if state in r["can"] else faint), (r["id"], state)


def test_a_part_the_widget_cannot_take_is_faint_and_does_nothing(card):
    card.open(kept=["machine"])
    faint = hexa(THEME["faint"])
    hello = list(card.sent)
    for wid, state in (("now", "keep"), ("watching", "keep"), ("needs", "keep"), ("needs", "off")):
        part = card.part(wid, state)
        assert hexa(card.text_of(part).property("color")) == faint
        card.hover(part)
        assert hexa(card.text_of(part).property("color")) == faint                 # no hover
        card.click(part)
        assert part.property("color").alpha() == 0
    assert card.sent == hello
    assert card.prop("kept") == ["machine"] and card.prop("hidden") == []


def test_a_press_on_a_part_that_can_be_taken_asks_once_and_the_card_follows_the_answer(card):
    card.open()
    card.click(card.part("machine", "keep"))
    assert card.sent == [message("machine", "keep")]
    # The press changed nothing it draws: the raised part is the one agentd last said.
    assert card.part("machine", "needed").property("color").alpha() == 255 and card.part("machine", "keep").property("color").alpha() == 0
    assert card.call("stateOf", "machine") == "needed"
    card.state(kept=["machine"])                                                      # agentd's answer
    assert card.part("machine", "keep").property("color").alpha() == 255 and card.part("machine", "needed").property("color").alpha() == 0
    card.click(card.part("machine", "off"))
    card.click(card.part("now", "off"))
    assert card.sent[1:] == [message("machine", "off"), message("now", "off")]


def test_every_part_that_can_be_pressed_sends_its_own_message_and_no_other(card):
    card.open()
    for r in ROWS:
        for state in r["can"]:
            if state == card.call("stateOf", r["id"]):
                continue
            before = len(card.sent)
            card.click(card.part(r["id"], state))
            assert card.sent[before:] == [message(r["id"], state)], (r["id"], state)


def test_a_press_on_the_part_that_is_already_so_sends_nothing(card):
    card.open(kept=["machine"], hidden=["now"])
    for wid, state in (("machine", "keep"), ("now", "off"), ("watching", "needed"), ("needs", "needed")):
        card.click(card.part(wid, state))
    assert card.sent == []


def test_a_click_beside_the_parts_sends_nothing(card):
    card.open()
    for r in ROWS:
        card.click(card.item("widgetsName", root=card.row(r["id"])))
        card.click(card.item("widgetsQuestion", root=card.row(r["id"])))
        card.click(card.row(r["id"]))
    card.click(card.item("widgetsTitle"))
    assert card.sent == [] and card.prop("widgetsOpen") is True


def test_reset_the_desk_asks_agentd_and_the_x_puts_the_card_away(card):
    card.open(kept=["machine"], hidden=["now"])
    card.click(card.item("widgetsReset"))
    assert card.sent == [{"type": "desk", "op": "reset"}]
    assert card.prop("kept") == ["machine"] and card.prop("hidden") == ["now"]      # nothing moves until agentd says
    assert card.prop("widgetsOpen") is True
    card.click(card.item("widgetsClose"))
    assert card.prop("widgetsOpen") is False and card.sent == [{"type": "desk", "op": "reset"}]
    card.pump(0.4)
    assert not card.card.isVisible()


def test_the_card_goes_with_the_socket_and_a_press_on_it_while_it_fades_reaches_nobody(card):
    card.open()
    card.call("lost", wait=False)
    assert card.prop("widgetsOpen") is False and card.card.property("shown") is False
    assert card.card.isVisible()                                                    # the fade is not over
    for item in (card.part("machine", "keep"), card.part("now", "off"), card.item("widgetsReset")):
        card.click(item)
    assert card.sent == []
    card.pump(0.4)
    assert not card.card.isVisible()


def test_a_card_that_is_fading_out_takes_no_press_even_with_agentd_there(card):
    # Put away, and the very next click lands on a card that is only still there to the eye (its fade has not
    # begun: a press is not a frame). The socket is up, so only the card itself can refuse it.
    card.open()
    card.call("closeWidgets", wait=False)
    assert card.prop("connected") is True and card.card.isVisible() and card.card.property("height") > 300
    for item in (card.part("machine", "keep"), card.part("now", "off"), card.item("widgetsReset")):
        card.click(item)
    assert card.sent == []
    assert card.prop("widgetsOpen") is False


def test_a_press_is_for_the_widget_and_the_state_under_the_finger_when_it_went_down(card):
    card.open()
    off = card.part("machine", "off")
    where = card.centre(off)
    card.press(off)
    # agentd's answer comes between the press and the release and moves the machine's row: the part under
    # the finger is now another widget's.
    card.state(widgets=list(reversed(ROWS)))
    assert card.row("machine").mapToScene(QtCore.QPointF(0, 0)).y() < off.mapToScene(QtCore.QPointF(0, 0)).y()
    card.release(where)
    assert card.sent == [message("machine", "off")]


def test_a_press_for_what_agentd_made_so_in_the_meantime_sends_nothing(card):
    card.open()
    off = card.part("machine", "off")
    where = card.centre(off)
    card.press(off)
    card.state(hidden=["machine"])                                                  # someone said "hide machine"
    card.release(where)
    assert card.sent == []


def test_the_pill_keeps_the_keyboard_whatever_is_pressed(card):
    card.open()
    field = card.item("input")
    card.pump()
    assert field.property("activeFocus") is True
    for item in (card.part("machine", "keep"), card.part("now", "keep"), card.part("machine", "keep"),
                 card.item("widgetsTitle"), card.row("now"), card.item("widgetsReset")):
        card.click(item)
        assert field.property("activeFocus") is True, item.objectName()


# -- colour --

def test_nothing_on_the_card_is_orange_in_any_state(card):
    orange = {hexa(v) for k, v in THEME.items() if k.startswith("accent") and isinstance(v, str)}
    assert hexa(THEME["accent"]) in orange and len(orange) >= 5

    def colours():
        return {hexa(c) for c in card.ask("paint")}

    for kw in ({}, {"kept": ["machine"]}, {"hidden": ["machine", "now"]}):
        card.open(**kw)
        assert not colours() & orange
        # Hovered, one thing at a time (the colours that change when the pointer is over something).
        for item in (card.part("machine", "keep"), card.part("now", "off"), card.part("needs", "off"), card.item("widgetsClose"),
                     card.item("widgetsReset"), card.part("machine", "needed")):
            card.hover(item)
            assert not colours() & orange, item.objectName()
        card.away()
    # Every colour on it is the theme's own, not one of its own.
    own = {hexa(THEME[k]) for k in ("glassLine", "border", "raised", "fg", "muted", "faint")} | {"#00000000", "#ff000000"}
    assert colours() <= own, colours() - own


def test_a_part_that_can_be_pressed_brightens_under_the_pointer_and_dims_after(card):
    card.open(kept=["machine"])
    fg, muted = hexa(THEME["fg"]), hexa(THEME["muted"])
    part = card.part("machine", "off")
    assert hexa(card.text_of(part).property("color")) == muted
    card.hover(part)
    assert hexa(card.text_of(part).property("color")) == fg
    card.away()
    assert hexa(card.text_of(part).property("color")) == muted
    # Reset and the x do the same.
    for name in ("widgetsReset", "widgetsClose"):
        item = card.item(name)
        card.hover(item)
        assert hexa(card.text_of(item).property("color")) == fg, name
        card.away()
        assert hexa(card.text_of(item).property("color")) == muted, name


# -- motion --

def test_a_part_changes_the_instant_agentd_says_so_and_nothing_eases(card):
    card.open()
    raised = card.part("machine", "needed")
    card.send(type="desk", kept=["machine"])
    # Read at once: a colour that eased toward the answer would still be the old one.
    assert card.part("machine", "keep").property("color").alpha() == 255 and raised.property("color").alpha() == 0
    assert hexa(card.text_of(card.part("machine", "keep")).property("color")) == hexa(THEME["fg"])
    assert hexa(card.text_of(raised).property("color")) == hexa(THEME["muted"])
    card.send(type="desk", kept=[], hidden=["machine"])
    assert card.part("machine", "off").property("color").alpha() == 255 and card.part("machine", "keep").property("color").alpha() == 0
    assert card.card.property("height") == 320 and card.card.property("opacity") == 1


def test_a_desk_message_that_changes_nothing_moves_nothing(card):
    card.open(kept=["machine"])
    row, part = card.row("machine"), card.part("machine", "keep")
    row.setProperty("marker", 1)
    before = (card.rect(row), card.rect(part), card.card.property("height"))
    for _ in range(5):
        card.state(kept=["machine"])
    assert (card.rect(row), card.rect(part), card.card.property("height")) == before
    assert card.row("machine") is row and row.property("marker") == 1 and card.card.property("opacity") == 1


# -- fit --

def test_what_agentd_says_fits_the_card_at_360_wide(card):
    card.open()
    assert all(not t["truncated"] and t["painted"] <= t["width"] + 0.01 for t in card.ask("texts"))
    inner_right = 360 - 12
    for r in ROWS:
        row, control = card.row(r["id"]), card.item(f"widgetsControl-{r['id']}")
        name, question = card.item("widgetsName", root=row), card.item("widgetsQuestion", root=row)
        # The name and the control share the first line; the question has the second to itself, at the row's width.
        assert card.rect(name)[1] + card.rect(name)[3] / 2 == pytest.approx(card.rect(control)[1] + 12, abs=1)
        assert card.rect(question)[1] >= card.rect(control)[1] + 24 and card.rect(question)[1] + card.rect(question)[3] <= card.rect(row)[1] + 56
        assert card.rect(question)[0] == card.rect(name)[0] == 18 and card.rect(question)[2] == inner_right - 18
        assert card.rect(name)[0] + card.rect(name)[2] <= card.rect(control)[0]
        assert card.rect(control)[0] + card.rect(control)[2] == inner_right
        assert card.rect(row)[3] == 56 and card.rect(control)[3] == 24 and card.rect(control)[1] == card.rect(row)[1] + 4
    # The control is the same size on every row, so the three parts stand in three columns.
    assert len({tuple(card.rect(card.part(r["id"], "needed"))[::2]) for r in ROWS}) == 1


def test_what_is_too_long_is_cut_short_not_spilled(card):
    question = "Is the machine in trouble? " * 14
    title = "A widget whose name goes on and on " * 3
    card.open(widgets=[dict(ROWS[3], title=title, question=question)] + ROWS[:3])
    row, control = card.row("machine"), card.item("widgetsControl-machine")
    name, asked = card.item("widgetsName", root=row), card.item("widgetsQuestion", root=row)
    for t in (name, asked):
        assert t.property("truncated") is True and t.property("paintedWidth") <= t.property("width") + 0.01
    assert asked.property("width") == 360 - 18 - 12 and asked.property("height") < 20          # one line, whatever it says
    assert card.rect(name)[0] + card.rect(name)[2] <= card.rect(control)[0]                  # the name stops short of the control
    assert card.rect(control)[0] + card.rect(control)[2] == 360 - 12 and card.rect(control)[2] < 200
    assert card.rect(row)[3] == 56 and card.card.property("height") == 320                   # and the card is no taller for it
    assert all(t["painted"] <= t["width"] + 0.01 for t in card.ask("texts"))
    card.snap("long-text")


def test_the_parts_stand_side_by_side_inside_their_control(card):
    card.open()
    control = card.rect(card.item("widgetsControl-machine"))
    parts = [card.rect(card.part("machine", s)) for s in STATES]
    assert parts[0][0] >= control[0] and parts[2][0] + parts[2][2] <= control[0] + control[2]
    assert parts[0][0] + parts[0][2] == pytest.approx(parts[1][0]) and parts[1][0] + parts[1][2] == pytest.approx(parts[2][0])
    assert all(control[1] <= p[1] and p[1] + p[3] <= control[1] + control[3] for p in parts)


# -- where a press lands --

def test_everything_that_can_be_pressed_is_inside_the_card_the_window_lets_clicks_through_to(card):
    # shell.qml's input mask for this card is the card's own rectangle: a press anywhere else goes to the windows behind.
    card.open()
    width, height = card.card.width(), card.card.height()
    items = [card.item("widgetsClose"), card.item("widgetsReset")]
    items += [card.part(r["id"], s) for r in ROWS for s in STATES]
    for it in items:
        x, y, w, h = card.rect(it)
        assert 0 <= x and 0 <= y and x + w <= width and y + h <= height, it.objectName()
        assert w >= 20 and h >= 20, it.objectName()                                  # something a pointer can hit
    # A press on each of them, at each of its corners, is its own.
    part = card.part("machine", "off")
    x, y, w, h = card.rect(part)
    for dx, dy in ((1, 1), (w - 1, 1), (1, h - 1), (w - 1, h - 1)):
        card.click_at(part, dx, dy)
    assert card.sent == [message("machine", "off")] * 4


def test_the_close_and_reset_have_a_pointer_sized_hit_area_on_their_own_words(card):
    card.open()
    close, reset = card.rect(card.item("widgetsClose")), card.rect(card.item("widgetsReset"))
    assert close[2] >= 24 and close[3] >= 24
    assert reset[3] >= 28 and reset[2] == pytest.approx(card.text_of(card.item("widgetsReset")).property("implicitWidth"), abs=1)


# -- how the shell wires it (shell.qml needs Quickshell, so it cannot load here: its lines are read) --

def shell_code():
    return re.sub(r"//[^\n]*", "", (SHELL / "shell.qml").read_text())


def test_shell_qml_puts_the_card_in_the_bars_column_and_its_mask():
    code = shell_code()
    assert code.index("AiCard {") < code.index("WidgetsCard {") < code.index("id: pillBox")
    block = code.split("WidgetsCard {", 1)[1].split("}", 1)[0]
    for line in ("id: widgetsCard", "desk: deskState", "active: win.summoned", "Layout.fillWidth: false",
                 "Layout.alignment: Qt.AlignHCenter"):
        assert line in block, line
    # Clicks on it reach it, and the ones beside it do not.
    assert "Region { item: widgetsCard.visible ? widgetsCard : null }" in code
    assert code.index("Region { item: aiCard.visible ? aiCard : null }") < code.index("Region { item: widgetsCard.visible")


def test_shell_qml_takes_the_keyboard_for_the_card_without_toggling_it_away():
    code = shell_code()
    asked = code.split("onWidgetsRequested:", 1)[1].split("\n        }", 1)[0]
    assert "root.takeKeyboard()" in asked
    take = code.split("function takeKeyboard()", 1)[1].split("\n", 1)[0]
    assert "root.summonedOn = root.focusedScreenName()" in take and "===" not in take and "?" not in take
    assert "root.cardKeys = false" in take            # the keys are the pill's, not a persona card's
    # The screen the keys go to is the one typed into (a pill already summoned), else the focused monitor's: the
    # shell's own helper for the card with a box, shared here. A tap on Super still reads the focused monitor itself,
    # which is how it moves the keys to another screen.
    pick = code.split("function focusedScreenName()", 1)[1].split("\n    }", 1)[0]
    assert pick.index("root.summonedOn") < pick.index("Hyprland.focusedMonitor")
    assert code.count("function focusedScreenName()") == 1
    assert "const m = Hyprland.focusedMonitor" in code.split("function summon(text, tip)", 1)[1].split("}", 1)[0]


def test_shell_qml_has_the_card_open_only_while_the_pill_holds_the_keyboard():
    code = shell_code()
    assert re.search(r'onSummonedOnChanged:\s*if \(summonedOn === ""\) deskState\.closeWidgets\(\)', code)
    # Esc: this card first (it is on top, and it can be up while a turn runs, since a launcher word does not wait for
    # one: the first Esc closes it and the keyboard goes back, a second stops the turn), then a turn, then the AI card.
    esc = code.split("Keys.onEscapePressed:", 1)[1].split("\n                                }", 1)[0]
    order = [esc.index(x) for x in ("widgetsCard.shown", "pillState.stoppable", "pillState.aiOpen", 'text !== ""')]
    assert order == sorted(order) and len(set(order)) == 4
    assert esc.lstrip().split("\n", 1)[1].lstrip().startswith(
        "if (widgetsCard.shown) { deskState.closeWidgets(); root.release() }")
    # ...and that is the card drawn on this screen, so one open in state but not drawn here cannot swallow the Esc.
    assert "deskState.widgetsOpen" not in esc
    assert 'if (text !== "") deskState.closeWidgets()' in code.split("onTextChanged:", 1)[1].split("}", 1)[0]
    # Not while the card is up: the idle timer would give the keyboard away from under it.
    idle = code.split("id: idle", 1)[1].split("onTriggered", 1)[0]
    assert "running: win.summoned && !widgetsCard.shown" in idle
    # One card over the pill at a time.
    assert "onAiOpenChanged: if (aiOpen) deskState.closeWidgets()" in code
    assert "pillState.closeAi()" in code.split("onWidgetsRequested:", 1)[1].split("\n        }", 1)[0]


def test_shell_qml_puts_the_picture_away_while_the_card_is_up():
    # The picture may take 60% of the screen, so with the card under it a small screen loses its top.
    code = shell_code()
    binding = code.split('property: "suppressed"', 1)[1].split("\n", 1)[0]
    assert "value: win.capsule || widgetsCard.shown" in binding and "when: cardHost.item !== null" in binding

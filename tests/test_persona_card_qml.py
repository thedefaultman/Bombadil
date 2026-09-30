"""The persona card above the pill, driven by agentd's messages in an offscreen window.

PersonaCard and PillState are plain Qt Quick (shell.qml only wraps them in Quickshell types), so
they load here without a compositor. The keyboard handoff itself lives in shell.qml and cannot run
here: what is checked is the card's side of it (takeKeys, wantKeys, closed). Set
BOMBADIL_SCREENS=<dir> to save a picture of each state.
"""

import os
import re
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
try:
    from PySide6 import QtCore, QtGui, QtQml, QtQuick, QtTest  # QtQuick gives the windows their type
except ImportError as e:   # no PySide6, or no libEGL: the offscreen tests skip, the reading tests still run
    QtCore = QtGui = QtQml = QtQuick = QtTest = None
    NO_QT = str(e)
else:
    NO_QT = ""

SHELL = Path(__file__).resolve().parents[1] / "shell"

HARNESS = """
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "%s"

Window {
    id: w
    width: 820; height: 520; visible: true
    color: "#3b4a5a"
    property var sent: []
    property bool here: true
    property int asks: 0
    property int wants: 0
    property int closes: 0
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
        onAsked: w.asks += 1
    }
    ColumnLayout {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
        spacing: 8
        StatusLine { objectName: "statusLine"; pill: pillState; Layout.fillWidth: true }
        PersonaCard {
            objectName: "personaCard"
            pill: pillState
            here: w.here
            Layout.fillWidth: true
            Layout.maximumWidth: 620
            Layout.alignment: Qt.AlignHCenter
            onWantKeys: w.wants += 1
            onClosed: w.closes += 1
        }
        // The pill's own field: it has the focus until the card takes the keyboard.
        TextField { objectName: "pillInput"; Layout.fillWidth: true; focus: true }
    }
}
"""

VOICES = [
    {"id": "merry", "name": "Merry", "card": "Welcome{n}. Let's go for a walk!"},
    {"id": "plain", "name": "Plain", "card": "Welcome{n}."},
    {"id": "quiet", "name": "Quiet", "card": "Nothing on an ordinary morning, only news."},
]
ASK = {"type": "persona_ask", "line": "Signed in to Claude. What should I call you?", "name": "",
       "voice": "merry", "current": False, "voices": VOICES}


@pytest.fixture(scope="module")
def app():
    if NO_QT:
        pytest.skip(NO_QT)
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
        # Binding errors (a TypeError on an undefined pill) arrive as messages, not as engine warnings.
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

    def send(self, **ev):
        ev.setdefault("type", "event")
        QtCore.QMetaObject.invokeMethod(self.pill, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()

    def call(self, name, *args, on=None):
        ret = QtCore.QMetaObject.invokeMethod(
            on or self.pill, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return ret

    def items(self, name, visible_only=True):
        """Items by objectName, found through the visual tree (delegates included)."""
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return sorted(found, key=lambda it: it.mapToScene(QtCore.QPointF(0, 0)).y())

    def focus(self, it):
        QtCore.QMetaObject.invokeMethod(it, "forceActiveFocus")
        self.pump()

    def item(self, name):
        found = self.items(name, visible_only=False)
        return found[0] if found else None

    @property
    def card(self):
        return self.item("personaCard")

    @property
    def field(self):
        return self.item("nameField")

    def prop(self, it, name):
        return it.property(name)

    @property
    def sent(self):
        return [dict(m) for m in self.win.property("sent").toVariant()] if hasattr(
            self.win.property("sent"), "toVariant") else [dict(m) for m in self.win.property("sent")]

    @property
    def shown(self):
        return bool(self.card.property("shown"))

    @property
    def rows(self):
        return self.items("voiceRow")

    def samples(self):
        return [it.property("text") for it in self.items("voiceSample")]

    def chosen(self):
        return [r.property("voiceId") for r in self.rows if r.property("on")]

    def hint(self):
        return self.item("cardHint").property("text")

    def key(self, k):
        QtTest.QTest.keyClick(self.win, k)
        self.pump(0.02)

    def type(self, text):
        for ch in text:
            QtTest.QTest.keyClick(self.win, ch)
        self.pump(0.02)

    def type_text(self, text):
        """Keys that keyClick cannot name (accents): a key event that carries the text."""
        for ch in text:
            for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                ev = QtGui.QKeyEvent(kind, 0, QtCore.Qt.NoModifier, ch)
                QtGui.QGuiApplication.sendEvent(self.win, ev)
        self.pump(0.02)

    def ask(self, **over):
        self.send(**{**ASK, **over})

    def take_keys(self):
        self.call("takeKeys", on=self.card)

    def click(self, it):
        centre = it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, centre)
        self.pump()

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.4)   # let the fade and height animations settle
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


@pytest.fixture
def card(bar):
    """The card up and holding the keyboard, as the shell leaves it after persona_ask."""
    bar.ask()
    bar.take_keys()
    return bar


def test_the_card_shows_its_own_line_an_empty_field_and_three_voices(bar):
    assert not bar.shown and not bar.card.isVisible()
    bar.ask()
    assert bar.shown and bar.card.isVisible() and bar.win.property("asks") == 1
    assert bar.item("cardLine").property("text") == "Signed in to Claude. What should I call you?"
    assert bar.field.property("text") == "" and bar.field.property("placeholderText") == "Your name"
    assert bar.field.property("maximumLength") == 24
    assert [it.property("text") for it in bar.items("voiceName")] == ["Merry", "Plain", "Quiet"]
    assert bar.chosen() == ["merry"]
    assert bar.samples() == ["Welcome. Let's go for a walk!", "Welcome.", "Nothing on an ordinary morning, only news."]
    assert bar.hint() == "Enter keeps Merry · Up and Down to change · Esc skips"
    bar.snap("card-1-asking")


def test_the_card_takes_the_keyboard_from_the_pill_and_a_name_never_reaches_it(bar):
    pill_input = bar.item("pillInput")
    bar.focus(pill_input)
    assert pill_input.property("activeFocus") and not bar.field.property("activeFocus")
    bar.ask()
    bar.take_keys()
    assert bar.field.property("activeFocus") and not pill_input.property("activeFocus")
    bar.type("Daniel")
    assert bar.field.property("text") == "Daniel" and pill_input.property("text") == ""


def test_the_samples_rewrite_as_the_name_is_typed(card):
    card.type("Dan")
    assert card.samples() == ["Welcome, Dan. Let's go for a walk!", "Welcome, Dan.",
                              "Nothing on an ordinary morning, only news."]
    card.type("iel")
    assert card.samples()[1] == "Welcome, Daniel."
    card.snap("card-2-typed")
    for _ in range(6):
        card.key(QtCore.Qt.Key_Backspace)
    assert card.samples()[:2] == ["Welcome. Let's go for a walk!", "Welcome."]
    # Two spaces and a trailing one read as the clean name agentd will get.
    card.type("Mary  Jane ")
    assert card.samples()[1] == "Welcome, Mary Jane."


def test_up_and_down_move_the_highlight_from_the_field_and_the_hint_follows(card):
    card.key(QtCore.Qt.Key_Down)
    assert card.chosen() == ["plain"] and card.hint() == "Enter keeps Plain · Up and Down to change · Esc skips"
    card.key(QtCore.Qt.Key_Down)
    assert card.chosen() == ["quiet"] and card.hint().startswith("Enter keeps Quiet")
    card.key(QtCore.Qt.Key_Down)
    assert card.chosen() == ["quiet"]   # the list ends: no wrap
    card.key(QtCore.Qt.Key_Up)
    card.key(QtCore.Qt.Key_Up)
    card.key(QtCore.Qt.Key_Up)
    assert card.chosen() == ["merry"]
    card.type("Da")
    card.key(QtCore.Qt.Key_Down)
    assert card.chosen() == ["plain"] and card.field.property("text") == "Da"   # Up and Down leave the text alone
    card.snap("card-3-plain")


def test_tab_and_shift_tab_move_the_highlight_and_the_field_keeps_the_keys(card):
    # Left alone, Tab takes the focus to the pill behind the card and the card stops answering.
    card.type("Al")
    card.key(QtCore.Qt.Key_Tab)
    assert card.chosen() == ["plain"] and card.field.property("activeFocus") and card.field.property("text") == "Al"
    card.key(QtCore.Qt.Key_Backtab)
    assert card.chosen() == ["merry"] and card.field.property("activeFocus")
    card.key(QtCore.Qt.Key_Tab)
    card.type("ex")
    assert card.field.property("text") == "Alex"
    before = len(card.sent)
    card.key(QtCore.Qt.Key_Return)
    assert card.sent[before:] == [{"type": "persona", "name": "Alex", "voice": "plain"}]


def test_a_name_that_ends_in_a_dot_does_not_double_the_full_stop_in_the_samples(card):
    card.type("Daniel Z.")
    assert card.samples()[:2] == ["Welcome, Daniel Z. Let's go for a walk!", "Welcome, Daniel Z."]


def test_each_row_reads_back_how_its_voice_ends_a_reply(card):
    card.ask(voices=[
        {"id": "merry", "name": "Merry", "card": "Welcome{n}.", "reply": "Two tabs use most of it. That's a lot of reading."},
        {"id": "plain", "name": "Plain", "card": "Welcome{n}.", "reply": "Two tabs use most of it."},
        {"id": "quiet", "name": "Quiet", "card": "Nothing on an ordinary morning, only news."}])
    replies = [it.property("text") for it in card.items("voiceReply")]
    assert replies == ["Two tabs use most of it. That's a lot of reading.", "Two tabs use most of it."]   # none for a voice without one


def test_every_text_in_the_card_is_plain_text_because_names_and_samples_come_from_outside():
    src = (SHELL / "PersonaCard.qml").read_text()
    texts = re.findall(r"^\s*Text \{", src, re.MULTILINE)
    assert len(texts) >= 5 and len(re.findall(r"textFormat: Text\.PlainText", src)) == len(texts)


def test_enter_answers_with_the_name_and_the_highlighted_voice(card):
    card.type("Dan")
    card.key(QtCore.Qt.Key_Down)
    before = len(card.sent)
    card.key(QtCore.Qt.Key_Return)
    assert card.sent[before:] == [{"type": "persona", "name": "Dan", "voice": "plain"}]
    assert not card.shown and card.pill.property("personaAsk") is None
    assert card.win.property("closes") == 1
    card.key(QtCore.Qt.Key_Return)   # the card is gone: a second Enter says nothing
    assert len(card.sent) == before + 1 and card.win.property("closes") == 1


def test_the_keypad_enter_answers_too(card):
    card.type("Dan")
    card.key(QtCore.Qt.Key_Enter)
    assert card.sent[-1] == {"type": "persona", "name": "Dan", "voice": "merry"}


def test_enter_with_nothing_typed_keeps_merry_and_no_name(card):
    card.key(QtCore.Qt.Key_Return)
    assert card.sent[-1] == {"type": "persona", "name": "", "voice": "merry"}
    assert not card.shown


def test_esc_skips_and_folds_the_card(card):
    card.type("Dan")
    before = len(card.sent)
    card.key(QtCore.Qt.Key_Escape)
    assert card.sent[before:] == [{"type": "persona_skip"}]
    assert not card.shown and card.win.property("closes") == 1
    card.key(QtCore.Qt.Key_Escape)
    assert len(card.sent) == before + 1


def test_a_click_picks_a_row_and_asks_for_the_keyboard(card):
    before = card.win.property("wants")
    card.click(card.rows[2])
    assert card.chosen() == ["quiet"] and card.win.property("wants") == before + 1
    assert card.field.property("activeFocus")   # the row never holds the keys
    card.click(card.rows[1])
    assert card.chosen() == ["plain"]
    card.type("Dan")
    card.key(QtCore.Qt.Key_Return)
    assert card.sent[-1] == {"type": "persona", "name": "Dan", "voice": "plain"}


def test_a_click_on_the_field_asks_for_the_keyboard_and_focuses_it(bar):
    bar.ask()
    pill_input = bar.item("pillInput")
    bar.focus(pill_input)
    before = bar.win.property("wants")
    bar.click(bar.field)
    assert bar.win.property("wants") == before + 1
    assert bar.field.property("activeFocus") and not pill_input.property("activeFocus")


def test_the_field_takes_only_what_a_name_may_hold(card):
    card.type("1Dan@!/")
    assert card.field.property("text") == "Dan"
    card.type("iel O'Neil-Smith.")
    assert card.field.property("text") == "Daniel O'Neil-Smith."
    card.type("x" * 10)
    assert len(card.field.property("text")) == 24   # maximumLength
    for _ in range(24):
        card.key(QtCore.Qt.Key_Backspace)
    card.type(" Dan")
    assert card.field.property("text") == "Dan"   # never starts with a space
    card.key(QtCore.Qt.Key_Backspace)
    card.key(QtCore.Qt.Key_Backspace)
    card.key(QtCore.Qt.Key_Backspace)
    card.type_text("Åsa")
    assert card.field.property("text") == "Åsa"


def test_more_than_three_words_is_refused_with_a_plain_hint_until_it_is_edited(card):
    card.type("Anna Maria Lou Smith")
    before = len(card.sent)
    card.key(QtCore.Qt.Key_Return)
    assert card.sent[before:] == [] and card.shown
    assert card.hint() == "A name is up to three words"
    assert card.card.property("problem") is True
    card.snap("card-4-too-long")
    card.key(QtCore.Qt.Key_Backspace)
    assert card.card.property("problem") is False and card.hint().startswith("Enter keeps")
    for _ in range(9):   # " Lou Smit"
        card.key(QtCore.Qt.Key_Backspace)
    card.key(QtCore.Qt.Key_Return)
    assert card.sent[-1] == {"type": "persona", "name": "Anna Maria", "voice": "merry"}


def test_a_persona_message_from_another_bar_folds_the_card(card):
    card.send(type="persona", name="Dan", voice="plain", greet=True)
    assert not card.shown and card.pill.property("personaAsk") is None
    assert [m for m in card.sent if m["type"].startswith("persona")] == []   # we answered nothing


def test_a_lost_socket_drops_the_card_and_the_reask_starts_fresh(card):
    card.type("Dan")
    card.key(QtCore.Qt.Key_Down)
    card.call("lost")
    assert not card.shown
    card.send(type="status", busy=False, provider="claude", queue=[])
    card.ask()
    card.take_keys()
    assert card.shown and card.field.property("text") == "" and card.chosen() == ["merry"]
    assert card.win.property("asks") == 2


def test_asked_again_with_the_current_name_and_voice_filled_in(bar):
    bar.ask(line="Pick a voice, or press Esc.", name="Dan", voice="plain", current=True)
    assert bar.item("cardLine").property("text") == "Pick a voice, or press Esc."
    assert bar.field.property("text") == "Dan" and bar.chosen() == ["plain"]
    assert bar.samples()[0] == "Welcome, Dan. Let's go for a walk!"
    assert bar.hint().startswith("Enter keeps Plain")
    bar.take_keys()
    bar.key(QtCore.Qt.Key_Return)
    assert bar.sent[-1] == {"type": "persona", "name": "Dan", "voice": "plain"}


def test_a_prompt_from_the_pill_folds_the_card_and_runs(card):
    before = len(card.sent)
    assert card.call("submit", "install ffmpeg") is True
    assert card.sent[before:] == [{"type": "persona_skip"}, {"type": "prompt", "text": "install ffmpeg"}]
    assert not card.shown and card.pill.property("mode") == "working"


def test_a_prompt_that_goes_nowhere_leaves_the_card(bar):
    bar.ask()
    bar.call("lost")
    bar.ask()   # as if agentd asked again while the socket is still down
    before = len(bar.sent)
    assert bar.call("submit", "hello") is False
    assert bar.sent[before:] == [] and bar.shown


def test_a_welcome_yields_to_the_card_and_the_card_clears_a_welcome(bar):
    bar.ask()
    bar.send(type="welcome", id=1, text="Welcome, Daniel.", first=True)
    assert bar.pill.property("mode") == "idle" and [m for m in bar.sent if m["type"] == "welcomed"] == []
    bar.call("personaSkip")
    bar.send(type="welcome", id=2, text="Welcome, Daniel. Let's go for a walk!", first=True)
    assert bar.pill.property("mode") == "welcome"
    bar.ask()
    assert bar.pill.property("mode") == "idle" and bar.shown   # the card has its own line


def test_the_card_shows_on_its_own_screen_only(bar):
    bar.win.setProperty("here", False)
    bar.ask()
    assert bar.pill.property("personaAsk") is not None and not bar.shown and not bar.card.isVisible()
    bar.call("personaSkip")   # the other screen's card ended it
    bar.win.setProperty("here", True)
    assert not bar.shown


def test_the_card_still_answers_when_agentd_sent_no_voices(bar):
    bar.ask(voices=[], voice="")
    assert bar.items("voiceRow") == [] and bar.hint() == "Enter saves · Esc skips"
    bar.take_keys()
    bar.type("Dan")
    bar.key(QtCore.Qt.Key_Down)   # nothing to move
    bar.key(QtCore.Qt.Key_Return)
    assert bar.sent[-1] == {"type": "persona", "name": "Dan", "voice": "merry"}
    bar.ask(voices=None, line="")
    assert bar.item("cardLine").property("text") == "What should I call you?"


def test_the_shipped_voice_samples_fill_in_the_typed_name(bar):
    persona = pytest.importorskip("bombadil.persona")
    voices = persona.templates()
    assert [v["id"] for v in voices] == list(persona.VOICES)
    bar.ask(voices=voices)
    bar.take_keys()
    assert bar.chosen() == [persona.DEFAULT_VOICE]
    bar.type("Daniel")
    shown = bar.samples()
    assert len(shown) == len(voices)
    for v, text in zip(voices, shown, strict=True):
        assert text == v["card"].replace("{n}", ", Daniel") and "{" not in text and len(text) <= 100
    assert [it.property("text") for it in bar.items("voiceName")] == [v["name"] for v in voices]


def test_every_string_on_the_card_keeps_the_briefs_rules(bar):
    seen = set()

    def collect():
        seen.update([bar.field.property("placeholderText"), bar.item("cardLine").property("text"), bar.hint()])

    bar.ask()
    bar.take_keys()
    collect()
    bar.ask(line="", voices=[])
    collect()
    bar.ask()
    bar.take_keys()
    bar.type("Anna Maria Lou Smith")
    bar.key(QtCore.Qt.Key_Return)
    collect()
    banned = re.compile(r"oops|sorry|hmm|let me|nothing here yet", re.IGNORECASE)
    for text in seen:
        assert text and "\n" not in text and len(text) <= 100, text
        assert "—" not in text and "–" not in text and "!" not in text, text
        assert not banned.search(text), text


def test_the_card_loads_and_runs_without_qml_warnings(bar):
    bar.ask()
    bar.take_keys()
    bar.type("Daniel")
    bar.key(QtCore.Qt.Key_Down)
    bar.click(bar.rows[0])
    bar.key(QtCore.Qt.Key_Return)
    bar.ask(name="Dan", voice="quiet", current=True)
    bar.key(QtCore.Qt.Key_Escape)
    bar.ask(voices=[])
    bar.send(type="persona", name="Dan", voice="quiet", greet=True)
    bar.ask()
    bar.call("lost")
    bar.win.setProperty("here", False)
    bar.pump(0.3)
    assert bar.warnings == []


# -- shell.qml cannot run without Quickshell: these read it instead --

def _members(path):
    """Every function, property and signal a QML file declares."""
    src = (SHELL / path).read_text()
    return (set(re.findall(r"\bfunction\s+(\w+)\s*\(", src)) | set(re.findall(r"\bsignal\s+(\w+)", src))
            | set(re.findall(r"\bproperty\s+(?:\w+\s+)+?(\w+)\s*:", src)))


def test_shell_qml_reaches_the_card_only_through_what_it_declares():
    shell = (SHELL / "shell.qml").read_text()
    card = _members("PersonaCard.qml")
    used = set(re.findall(r"\bpersonaCard\.(\w+)", shell))
    assert {"shown", "visible", "takeKeys"} <= used and used - {"visible"} <= card, used - card   # visible is Item's
    block = shell[shell.index("PersonaCard {"):]
    block = block[:block.index("QueueChips {")]
    assert "onWantKeys:" in block and "root.cardKeys = true" in block and "win.takeCard()" in block
    assert "here: win.cardHere" in block
    assert "root.release()" in block and "input.forceActiveFocus()" in block   # after Enter or Esc
    assert "Layout.fillWidth: true" in block and "pill: pillState" in block
    assert re.search(r"PersonaCard \{.*\n(?:.*\n)*?.*\}\n\n                QueueChips", shell)   # the card sits before the chips


def test_shell_qml_hands_the_keyboard_to_the_card_without_toggling_it():
    shell = (SHELL / "shell.qml").read_text()
    keep = shell[shell.index("function summonKeep()"):]
    keep = keep[:keep.index("\n    }")]
    assert "root.summonedOn = root.cardOn" in keep and "?" not in keep   # never the toggle of summon()
    assert "onAsked: root.summonKeep()" in shell
    # A window that was already summoned gets no summonedChanged: the Connections takes the card's keys.
    assert re.search(r"function onAsked\(\) \{ if \(win\.summoned\) Qt\.callLater\(win\.takeCard\) \}", shell)
    # Only a card that asked (or was clicked) gets the keys when a screen turns summoned: Super and a click
    # on the pill always mean the pill, even with the card up.
    assert "if (root.cardKeys && personaCard.shown) personaCard.takeKeys()" in shell
    assert "root.cardKeys = true" in keep
    summon = shell[shell.index("function summon()"):]
    summon = summon[:summon.index("\n    }")]
    assert "root.cardKeys = false" in summon and "pillState.focusPill()" in summon
    assert "function onFocusPill() { if (win.summoned) input.forceActiveFocus() }" in shell
    assert shell.count("TapHandler { onTapped: win.focusPill() }") == 2   # the pill and its field
    # The keyboard timer waits while the card does, and the input mask follows the card.
    assert "running: win.summoned && !personaCard.shown" in shell
    assert "Region { item: personaCard.visible ? personaCard : null }" in shell
    # Any fold puts the pill's field back in focus, and gives back a keyboard the card was holding.
    fold = shell[shell.index("function onPersonaAskChanged()"):]
    fold = fold[:fold.index("\n                }")]
    assert "input.forceActiveFocus()" in fold and "win.summoned && root.cardKeys" in fold and "root.release()" in fold

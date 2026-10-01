"""The line above the pill, and the picture above that, driven by agentd's events in an offscreen window.

PillState, StatusLine, QueueChips and CardHost are plain Qt Quick (Quickshell only wraps them in
shell.qml), so they load here without a compositor. Set BOMBADIL_SCREENS=<dir> to save a
picture of each state.
"""

import os
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
    width: 820; height: 760 + (w.screens - 1) * 200; visible: true
    color: "#3b4a5a"
    property var sent: []
    property int screens: 1
    property var summons: []
    property int handOffs: 0
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
        onSummoned: text => w.summons = w.summons.concat([text])
        onHandOff: w.handOffs += 1
    }
    // A delegate, like the bar's PanelWindow in Variants: names resolve as they do in shell.qml.
    Repeater {
        model: w.screens
        ColumnLayout {
            required property int index
            parent: w.contentItem
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
            anchors.bottomMargin: 12 + index * 200
            spacing: 8
            // As in shell.qml: loaded on its own, so a kit that will not load costs the pictures only.
            Loader {
                id: cardHost
                visible: status === Loader.Ready && item !== null && item.opacity > 0
                Layout.fillWidth: true
                Component.onCompleted: setSource("%s/CardHost.qml", { pill: pillState, maxHeight: 520 })
            }
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
        qml.write_text(HARNESS % (SHELL.as_uri(), SHELL.as_uri()))
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

    def call(self, name, *args):
        ret = QtCore.QMetaObject.invokeMethod(
            self.pill, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return ret

    def item(self, name):
        found = self.items(name, visible_only=False)
        return found[0] if found else None

    def text(self, name="line"):
        return self.item(name).property("text")

    def items(self, name, visible_only=True):
        """Items by objectName, found through the visual tree (delegates included)."""
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return found

    def shown(self, name):
        it = self.item(name)
        return it is not None and it.isVisible()

    @property
    def sent(self):
        return [dict(m) for m in self.win.property("sent").toVariant()] if hasattr(
            self.win.property("sent"), "toVariant") else [dict(m) for m in self.win.property("sent")]

    def click(self, name):
        it = self.item(name)
        centre = it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, centre)
        self.pump()

    def click_item(self, it):
        centre = it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, centre)
        self.pump()

    def chips(self):
        """The setup chips on screen, left to right, as (label, item)."""
        found = self.items("setupChip")
        found.sort(key=lambda it: it.mapToScene(QtCore.QPointF(0, 0)).x())
        return [(next(c.property("text") for c in it.childItems() if c.property("text") is not None), it)
                for it in found]

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.3)   # let the fade and height animations settle
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"{name}.png"))


@pytest.fixture
def bar(app, tmp_path):
    b = Bar(app, tmp_path)
    b.send(type="status", busy=False, provider="claude", queue=[])
    b.send(type="entries", entries=[{"name": "passwords", "title": "Passwords", "kind": "app",
                                     "words": ["passwords"]},
                                    {"name": "browser", "title": "Browser", "kind": "panel",
                                     "words": ["browser", "chrome"]},
                                    {"name": "machine", "title": "Machine", "kind": "widget",
                                     "words": ["machine"]},
                                    {"name": "now", "title": "Now", "kind": "widget", "words": ["now", "route"]},
                                    {"name": "desk", "title": "Desk", "kind": "command", "words": ["desk"]},
                                    {"name": "undo", "title": "Undo", "kind": "command", "words": ["undo"]}])
    yield b
    b.win.close()
    b.engine.deleteLater()
    b.pump()


def test_on_it_shows_before_agentd_answers(bar):
    assert not bar.item("statusLine").isVisible() or bar.item("statusLine").property("opacity") == 0
    assert bar.call("submit", "install ffmpeg") is True
    assert bar.text() == "On it"
    assert bar.sent[-1] == {"type": "prompt", "text": "install ffmpeg"}
    bar.pump(1.3)
    assert bar.shown("counter") and bar.text("counter") in ("1s", "2s")
    bar.snap("1-on-it")


def test_a_turn_streams_its_steps_and_closes_with_undo(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="status", turn=1, text="Saving a restore point", source="step")
    assert bar.text() == "Saving a restore point"
    bar.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system",
             command="sudo pacman -S --noconfirm ffmpeg")
    assert bar.text() == "Installing ffmpeg"
    assert bar.shown("command") and bar.text("command") == "sudo pacman -S --noconfirm ffmpeg"
    assert bar.item("statusLine").property("edge").name() == THEME["warn"]
    bar.snap("2-system-step")
    bar.send(kind="status", turn=1, text="and it is ready to use.", source="agent")
    assert not bar.shown("command")
    assert bar.item("line").property("maximumLineCount") == 1
    bar.send(kind="result", turn=1, ok=True, text="Installed ffmpeg 7.1.\nIt is ready to use.")
    bar.send(kind="turn_end", turn=1, seconds=9, changed=True, summary="Installed ffmpeg.", stopped=False)
    assert bar.text() == "Installed ffmpeg 7.1.\nIt is ready to use."
    assert bar.item("line").property("maximumLineCount") == 4
    assert bar.shown("undoButton") and bar.shown("detailsButton") and not bar.shown("counter")
    bar.snap("3-closing-with-undo")


def test_the_bars_text_is_set_in_the_themes_family(bar):
    # Every Text the bar draws, in every state that shows one: the theme's family, not whatever
    # fontconfig calls "sans". (The source check is tests/test_theme.py.)
    def families():
        found, todo = set(), [bar.win.contentItem()]
        while todo:
            it = todo.pop()
            todo.extend(it.childItems())
            if it.metaObject().className().startswith("QQuickText") and it.isVisible():
                found.add((it.property("text"), it.property("font").family()))
        return found

    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="status", turn=1, text="Installing ffmpeg", source="step", risk="system",
             command="sudo pacman -S --noconfirm ffmpeg")
    bar.pump(1.3)    # the seconds counter shows from the first second
    working = families()
    bar.send(kind="turn_end", turn=1, seconds=9, changed=True, summary="Installed ffmpeg.")
    closing = families()
    seen = working | closing
    assert {"Installing ffmpeg", "1s", "Undo", "Details"} <= {t for t, _ in seen}
    mono = {t for t, f in seen if f == THEME["monoFamily"]}
    assert mono == {"sudo pacman -S --noconfirm ffmpeg"}
    assert {f for t, f in seen if t not in mono} == {THEME["fontFamily"]}


def face(bar):
    return bar.pill.property("face")


def test_the_stone_starts_then_goes_offline_if_agentd_never_comes(app, tmp_path):
    b = Bar(app, tmp_path)           # no status yet: agentd has not answered
    try:
        assert face(b) == "starting"
        b.pill.setProperty("booting", False)       # the shell's 15 s are up
        b.pump()
        assert face(b) == "offline"
        b.send(type="status", busy=False, provider="claude", queue=[])
        assert face(b) == "rest"
    finally:
        b.win.close()
        b.engine.deleteLater()


def test_the_stone_goes_offline_when_agentd_goes_even_during_boot(bar):
    assert face(bar) == "rest" and bar.pill.property("seen")
    bar.call("lost")
    assert face(bar) == "offline"
    bar.pill.setProperty("booting", True)
    bar.pump()
    assert face(bar) == "offline"      # it was there, so this is a loss, not a start
    bar.send(type="status", busy=False, provider="claude", queue=[])
    assert face(bar) == "rest"


def test_the_stone_follows_a_turn(bar):
    bar.call("submit", "install ffmpeg")
    assert face(bar) == "working"                  # "On it", before agentd confirms
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    assert face(bar) == "working"
    bar.send(kind="turn_end", turn=1, seconds=9, changed=True, summary="Installed ffmpeg.")
    assert face(bar) == "done"
    bar.call("dismiss")
    assert face(bar) == "rest"
    bar.call("submit", "and stop it")
    bar.send(kind="turn_start", turn=2, prompt="and stop it")
    bar.send(kind="turn_end", turn=2, seconds=2, changed=False, stopped=True)
    assert face(bar) == "stopped"
    bar.call("dismiss")
    assert face(bar) == "rest"
    bar.call("submit", "break")
    bar.send(kind="turn_start", turn=3, prompt="break")
    bar.send(kind="error", turn=3, text="The CLI exited with 1.")
    bar.send(kind="turn_end", turn=3, seconds=1, changed=False)
    assert bar.pill.property("source") == "error" and face(bar) == "rest"


def test_the_stone_asks_for_you_while_setup_does(bar):
    bar.send(type="setup", state="choose", line="Which AI should run this computer?", tone="ask",
             actions=[{"id": "provider:claude", "label": "Claude", "style": "big"}])
    assert face(bar) == "needs"
    bar.send(type="setup", state="signing_in", line="Sign in to Claude in the browser", tone="step", actions=[])
    assert face(bar) == "working"
    bar.send(type="setup", state="signed_out", line="Claude signed you out.", tone="step",
             actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    assert face(bar) == "needs"
    bar.send(type="setup", state="ready", line="", tone="done", actions=[])
    assert face(bar) == "rest"


def test_needs_you_outranks_a_running_turn(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    assert face(bar) == "working"
    bar.pill.setProperty("needsYou", True)        # a session asks; the desk sets this
    bar.pump()
    assert face(bar) == "needs"
    bar.pill.setProperty("needsYou", False)
    bar.pump()
    assert face(bar) == "working"


def test_undo_on_the_line_does_not_also_open_details(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    before = len(bar.sent)
    bar.click("undoButton")
    assert bar.sent[before:] == [{"type": "local", "action": "undo"}]
    bar.click("detailsButton")
    assert bar.sent[-1] == {"type": "details", "turn": 1}


def test_details_hands_the_keyboard_to_the_drawer_and_esc_closes_it(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    # A summoned pill holds the keyboard exclusively, and Hyprland then refuses to focus the
    # drawer: the bar lets go before it asks for the drawer.
    bar.click("detailsButton")
    assert bar.win.property("handOffs") == 1 and bar.sent[-1] == {"type": "details", "turn": 1}
    bar.click("line")   # a click on the finished line itself opens (or closes) them too
    assert bar.win.property("handOffs") == 2 and bar.sent[-1] == {"type": "details", "turn": 1}
    bar.call("closeDetails")
    assert bar.sent[-1] == {"type": "close_details"}
    before = len(bar.sent)
    bar.send(type="status", busy=False, provider="claude", queue=[])
    QtCore.QMetaObject.invokeMethod(bar.pill, "lost")
    bar.pump()
    bar.call("closeDetails")
    bar.call("details")
    assert bar.sent[before:] == []   # nothing reaches agentd while the socket is down


def test_an_irreversible_step_is_red_and_says_so_after(bar):
    bar.call("submit", "wipe the usb stick")
    bar.send(kind="turn_start", turn=2, prompt="wipe the usb stick")
    bar.send(kind="status", turn=2, text="Formatting /dev/sdb1", source="step", risk="irreversible",
             command="sudo mkfs.ext4 /dev/sdb1")
    assert bar.item("statusLine").property("edge").name() == THEME["bad"]
    bar.snap("4-irreversible-step")
    bar.send(kind="turn_end", turn=2, seconds=4, changed=True, irreversible=True, summary="Formatted /dev/sdb1.")
    assert bar.text() == "Formatted /dev/sdb1."
    bar.snap("5-cannot-be-undone")


def test_stop_says_what_it_stopped_and_offers_no_undo(bar):
    bar.call("submit", "install docker")
    bar.send(kind="turn_start", turn=3, prompt="install docker")
    bar.send(kind="status", turn=3, text="Installing docker", source="step", risk="system", command="sudo pacman -S docker")
    bar.call("stop")
    assert bar.sent[-1] == {"type": "stop"} and bar.text() == "Stopping"
    bar.send(kind="turn_end", turn=3, seconds=5, changed=True, stopped=True, line="Stopped while installing docker.")
    assert bar.text() == "Stopped while installing docker."
    assert not bar.shown("undoButton") and bar.shown("detailsButton")
    bar.snap("6-stopped")


def test_a_queued_prompt_starting_after_stop_still_shows_what_stopped(bar):
    bar.call("submit", "install docker")
    bar.send(kind="turn_start", turn=3, prompt="install docker")
    bar.send(kind="queued", turn=4, prompt="tell me a joke")
    bar.send(kind="turn_end", turn=3, seconds=5, stopped=True, line="Stopped while installing docker.")
    bar.send(kind="turn_start", turn=4, prompt="tell me a joke")
    assert bar.pill.property("mode") == "working"
    assert bar.text() == "Stopped while installing docker."


def test_prompts_typed_while_working_wait_as_chips(bar):
    bar.call("submit", "install docker")
    bar.send(kind="turn_start", turn=4, prompt="install docker")
    bar.call("submit", "then open the browser")
    bar.send(kind="queued", turn=5, prompt="then open the browser")
    bar.send(kind="queued", turn=6, prompt="and make me a timer app")
    assert bar.text() == "On it"   # the running turn keeps its line
    assert len(bar.items("queuedChip")) == 2
    bar.snap("7-queued")
    bar.call("unqueue", 5)
    assert bar.sent[-1] == {"type": "unqueue", "turn": 5}
    bar.pump()
    assert len(bar.items("queuedChip")) == 1


def test_launcher_answers_replace_on_it_and_fade(bar):
    bar.pill.setProperty("fadeAfter", 5000)
    bar.call("submit", "browser")
    bar.send(type="local", action="panel")
    bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert bar.text() == "Opened the browser."
    assert not bar.shown("counter")
    bar.snap("8-launcher")
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.pill.property("mode") == "idle"


def test_a_launcher_answer_during_a_turn_flashes_over_it(bar):
    bar.call("submit", "install docker")
    bar.send(kind="turn_start", turn=7, prompt="install docker")
    bar.send(kind="status", turn=7, text="Installing docker", source="step")
    bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert bar.text() == "Opened the browser."
    assert bar.pill.property("mode") == "working" and bar.pill.property("line") == "Installing docker"


def test_a_turn_that_changed_nothing_fades_but_not_while_hovered(bar):
    bar.call("submit", "what time is it in Tokyo")
    bar.send(kind="turn_start", turn=8, prompt="what time is it in Tokyo")
    bar.send(kind="result", turn=8, ok=True, text="It is 21:40 in Tokyo.")
    bar.send(kind="turn_end", turn=8, seconds=2, changed=False)
    assert bar.text() == "It is 21:40 in Tokyo." and not bar.shown("undoButton")
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.pill.property("mode") == "idle"


def test_errors_and_lost_connection_read_plainly(bar):
    bar.call("submit", "hello")
    bar.send(kind="turn_start", turn=9, prompt="hello")
    bar.send(kind="error", turn=9, text="claude is not logged in.\nRun claude auth login.\nmore")
    bar.send(kind="result", turn=9, ok=False, text="")
    bar.send(kind="turn_end", turn=9, seconds=1, changed=False)
    assert bar.text() == "claude is not logged in.\nRun claude auth login."
    assert bar.item("statusLine").property("edge").name() == THEME["bad"]
    bar.snap("9-error")
    bar.call("submit", "again")
    bar.call("lost")
    assert bar.text() == "Lost touch with the agent. Reconnecting."
    assert bar.call("submit", "hello") is False
    assert bar.text() == "Not connected to the agent yet."


def test_tab_completes_names_and_exact_words_show_where_they_go(bar):
    assert bar.call("completion", "pass") == "words"
    assert bar.call("completion", "open chr") == "ome"
    assert bar.call("completion", "open un") == ""      # commands take no verb
    assert bar.call("exact", "Passwords.") == "Passwords"
    assert bar.call("exact", "open the browser and search") == ""


def test_a_widgets_name_is_a_launcher_word_only_after_a_verb_that_puts_a_thing_on_screen(bar):
    assert bar.call("exact", "show machine") == "Machine"
    assert bar.call("exact", "hide route") == "Now" and bar.call("exact", "close now.") == "Now"
    assert bar.call("exact", "machine") == "" and bar.call("exact", "now") == ""   # bare, it is a sentence
    assert bar.call("exact", "start now") == "" and bar.call("exact", "launch machine") == ""
    assert bar.call("completion", "show mach") == "ine" and bar.call("completion", "mach") == ""
    assert bar.call("completion", "start mach") == ""
    assert bar.call("exact", "desk") == "Desk" and bar.call("exact", "desk.") == "Desk"
    assert bar.call("exact", "desk?") == "" and bar.call("exact", "show machine?") == ""


def test_the_bar_loads_without_qml_warnings(bar):
    bar.call("submit", "hello")
    bar.send(kind="turn_start", turn=1, prompt="hello")
    bar.send(kind="turn_end", turn=1, seconds=1, changed=True, summary="Did it.")
    assert bar.warnings == []


def test_a_turn_that_ends_before_it_starts_still_ends_on_screen(bar):
    """agentd answers the prompt with its turn id; an error and turn_end for that id close
    "On it" even when no turn_start came (a CLI that is not installed yet)."""
    bar.call("submit", "hello")
    bar.send(type="queued", turn=1)
    bar.send(kind="error", turn=1, text="claude is not installed yet: press Super+Return and run bombadil-setup")
    bar.send(kind="turn_end", turn=1, seconds=0)
    assert bar.pill.property("mode") == "closing"
    assert bar.text().startswith("claude is not installed yet")
    assert not bar.pill.property("stoppable")


def test_esc_and_the_dot_can_stop_from_the_first_moment(bar):
    bar.call("submit", "install docker")
    bar.send(type="status", busy=False, provider="claude", queue=[])   # agentd has not started it yet
    assert bar.pill.property("stoppable")
    bar.send(kind="turn_start", turn=1, prompt="install docker")
    bar.send(kind="status", turn=1, text="Saving a restore point", source="step")
    assert bar.pill.property("stoppable")


def test_a_new_prompt_after_a_stop_shows_on_it_at_once(bar):
    bar.call("submit", "install docker")
    bar.send(kind="turn_start", turn=3, prompt="install docker")
    bar.send(kind="turn_end", turn=3, seconds=5, stopped=True, line="Stopped while installing docker.")
    bar.call("submit", "undo")
    assert bar.text() == "On it"


def test_not_connected_fades_even_after_a_line_that_stayed(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    assert bar.pill.property("sticky")
    bar.call("lost")
    bar.call("submit", "hello")
    assert bar.text() == "Not connected to the agent yet." and not bar.pill.property("sticky")


def test_undo_while_disconnected_keeps_the_line_and_says_why(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    bar.call("lost")
    before = len(bar.sent)
    bar.click("undoButton")
    assert len(bar.sent) == before and bar.pill.property("sticky")
    assert bar.text() == "Not connected to the agent yet."
    bar.send(kind="queued", turn=5, prompt="later")
    bar.call("unqueue", 5)
    assert len(bar.items("queuedChip")) == 1   # the chip stays: nothing was taken back


def test_exact_words_are_the_launchers_own(bar):
    assert bar.call("exact", "почему browser") == ""
    assert bar.call("exact", "!browser") == ""
    assert bar.call("exact", "Pass-words") == "Passwords"
    assert bar.call("exact", "browser, please") == ""


def test_hovering_the_line_on_one_screen_keeps_it_on_all(bar):
    bar.win.setProperty("screens", 2)
    bar.pump(0.2)
    lines = bar.items("statusLine", visible_only=False)
    assert len(lines) == 2
    bar.call("submit", "what time is it")
    bar.send(kind="turn_start", turn=8, prompt="what time is it")
    bar.send(kind="turn_end", turn=8, seconds=2, changed=False, summary="")
    bar.pump(0.3)
    first = min(lines, key=lambda it: it.mapToScene(QtCore.QPointF(0, 0)).y())
    centre = first.mapToScene(QtCore.QPointF(first.width() / 2, first.height() / 2)).toPoint()
    QtTest.QTest.mouseMove(bar.win, centre)
    bar.pump(0.2)
    assert bar.pill.property("hovers") == 1
    bar.pill.setProperty("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.pill.property("mode") == "closing"   # the other screen's line did not dismiss it
    QtTest.QTest.mouseMove(bar.win, QtCore.QPoint(5, 5))
    bar.pump(0.8)
    assert bar.pill.property("mode") == "idle"


def test_a_summon_can_carry_words_for_the_pill(bar):
    bar.send(type="summon")
    bar.send(type="summon", text="About ~/Documents/lease.pdf: ")
    summons = bar.win.property("summons")
    summons = summons.toVariant() if hasattr(summons, "toVariant") else summons
    assert list(summons) == ["", "About ~/Documents/lease.pdf: "]


def _hover_line(bar):
    line = bar.item("statusLine")
    centre = line.mapToScene(QtCore.QPointF(line.width() / 2, 6)).toPoint()
    QtTest.QTest.mouseMove(bar.win, centre)
    bar.pump(0.3)


def test_resting_on_the_line_shows_why_and_a_marked_step_always_does(bar):
    bar.call("submit", "make the tunnel")
    bar.send(kind="turn_start", turn=3, prompt="make the tunnel")
    bar.send(kind="status", turn=3, text="Reading wireguard.com", source="step",
             because="The vendor's page says which port the tunnel needs.")
    # Quiet until you rest the mouse on it.
    assert not bar.shown("because")
    _hover_line(bar)
    assert bar.shown("because") and bar.text("because") == "The vendor's page says which port the tunnel needs."
    QtTest.QTest.mouseMove(bar.win, QtCore.QPoint(5, 5))
    bar.pump(0.3)
    assert not bar.shown("because")
    # A step that changes the system shows its reason under the command, and what it followed.
    bar.send(kind="status", turn=3, text="Writing wg0.conf", source="step", risk="system",
             command="write /etc/wireguard/wg0.conf", because="Your router hands out 192.168.1.x, so the tunnel uses 10.8.0.x.",
             after={"label": "wireguard.com/quickstart", "kind": "web", "text": "after reading wireguard.com/quickstart"})
    assert bar.shown("command") and bar.shown("because") and bar.shown("after")
    assert bar.text("after") == "after reading wireguard.com/quickstart"
    bar.snap("4-because-and-after")
    # The agent's own words replace them, and so does the next step that has no reason.
    bar.send(kind="status", turn=3, text="Restarting wg-quick", source="step", risk="system", command="systemctl restart wg-quick@wg0")
    assert not bar.shown("because") and not bar.shown("after")
    bar.send(kind="turn_end", turn=3, seconds=4, changed=True, summary="Made the tunnel.")
    assert not bar.shown("because") and bar.pill.property("because") == "" and bar.pill.property("after") == ""


def test_why_flashes_the_reason_over_the_running_line_long_enough_to_read_it(bar):
    bar.call("submit", "install docker")
    bar.send(kind="turn_start", turn=7, prompt="install docker")
    bar.send(kind="status", turn=7, text="Installing docker", source="step", risk="system", command="sudo pacman -S docker",
             because="Docker is not installed yet.")
    bar.send(kind="local", action="why", phase="done", ok=True, text="Docker is not installed yet.")
    assert bar.text() == "Docker is not installed yet." and bar.pill.property("flashFor") == 8000
    assert bar.pill.property("line") == "Installing docker"
    bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert bar.pill.property("flashFor") == 3500


# -- the picture above the line --

def _diagram(**over):
    from bombadil import cards
    spec = {"shape": "chain", "title": "How a VPN works", "nodes": [{"label": "Laptop"}, {"label": "Tunnel", "state": "new"},
                                                                  {"label": "Internet"}], "say": "Everything goes through the tunnel."}
    spec.update(over)
    card, errors = cards.validate_diagram(spec)
    assert card is not None, errors
    return {**card, "id": over.get("id", "card-1")}


def _card_event(bar, card, turn=None):
    bar.send(kind="card", turn=turn, card=card)


def test_a_card_event_draws_the_picture_above_the_line(bar):
    _card_event(bar, _diagram())
    assert bar.shown("cardHost") and bar.text("cardTitle") == "How a VPN works"
    assert bar.text("cardSource") == "drawn by the agent"
    assert bar.text("cardSay") == "Everything goes through the tunnel."
    assert bar.items("box-n1") and bar.items("box-n3")
    # It sits above the status line, over the pill (once it has finished rising into place).
    bar.pump(0.3)
    assert bar.item("cardHost").mapToScene(QtCore.QPointF(0, 0)).y() < bar.item("statusLine").mapToScene(QtCore.QPointF(0, 0)).y() + 1
    bar.snap("card-agent")
    assert bar.warnings == []


def test_the_card_makes_room_at_once_so_the_bars_window_is_resized_once_not_every_frame(bar):
    # The bar's window is as tall as its contents. A height that grew over a few frames resized it
    # every frame, and on the compositor the pill jumped about while it caught up.
    _card_event(bar, _diagram())
    host = bar.item("cardHost")
    first = host.property("implicitHeight")
    assert first > 100
    bar.pump(0.5)
    assert host.property("implicitHeight") == first
    bar.call("dismissCard")
    bar.pump(0.06)
    assert host.property("implicitHeight") == first and host.property("opacity") < 1     # still there, fading out
    bar.pump(0.4)
    assert host.property("implicitHeight") == 0 and not bar.shown("cardHost")


def test_the_agents_words_on_the_line_start_at_a_word_not_in_the_middle_of_one(bar):
    said = ("A VPN builds an encrypted tunnel to a server, so your network cannot read your traffic and websites see "
            "the server's address instead of yours. The catch is that you are trusting the VPN company, which can "
            "see the traffic that comes out of its server.")
    bar.call("submit", "how does a vpn work")
    bar.send(kind="turn_start", turn=1, prompt="how does a vpn work")
    bar.send(kind="status", turn=1, text="Drawing a picture", source="step")
    bar.send(kind="status", turn=1, text=said, source="agent")
    shown = bar.text()
    line = bar.item("line")
    assert shown.startswith("…") and said.endswith(shown[1:])
    assert said[len(said) - len(shown) + 1 - 1] == " " and shown[1] != " "      # a whole word follows the "…"
    assert line.property("contentWidth") <= line.property("width") and shown.endswith("its server.")
    # More room shows more words; a line that fits shows all of it.
    bar.send(kind="status", turn=1, text="and it is ready to use.", source="agent")
    assert bar.text() == "and it is ready to use."
    # When the turn is over the whole answer is there, wrapped.
    bar.send(kind="result", turn=1, ok=True, text=said)
    bar.send(kind="turn_end", turn=1, seconds=4, changed=False, summary=said)
    assert bar.text() == said
    assert bar.warnings == []


def test_a_picture_the_machine_drew_says_so(bar):
    import test_sysmap as fixtures
    from bombadil import sysmap
    card = sysmap.capture_boot(fixtures.fake({"systemd-analyze critical-chain": fixtures.CHAIN,
                                              "systemd-analyze time": fixtures.TIME}))["card"]
    _card_event(bar, {**card, "id": "card-2"})
    assert bar.text("cardSource") == "from this machine"
    assert bar.items("step-u7")
    bar.snap("card-machine")
    assert bar.warnings == []


def test_a_newer_card_replaces_the_older_and_the_next_turn_clears_it(bar):
    _card_event(bar, _diagram())
    _card_event(bar, _diagram(title="What changed", id="card-2"))
    assert bar.text("cardTitle") == "What changed" and bar.pill.property("card")["id"] == "card-2"
    bar.send(kind="turn_start", turn=2, prompt="go on")
    assert bar.pill.property("card") is None
    bar.pump(0.4)
    assert not bar.shown("cardHost")


def test_a_half_drawn_card_grows_in_place_and_the_finished_one_takes_its_id(bar):
    from bombadil import cards
    half = cards.partial_diagram('{"shape": "chain", "title": "How a VPN works", "nodes": [{"label": "Laptop"}, {"label": "Tun')
    _card_event(bar, {**half, "id": "stream-t1"}, turn=1)
    assert bar.text("cardSource") == "drawing…" and bar.items("box-n1") and not bar.items("box-n2")
    _card_event(bar, {**_diagram(), "id": "stream-t1"}, turn=1)
    assert bar.text("cardSource") == "drawn by the agent" and bar.items("box-n3")
    # A call that failed takes its half back; one that is not showing stays.
    _card_event(bar, {**half, "id": "stream-t2"}, turn=1)
    bar.send(kind="card", turn=1, card={"id": "stream-t1", "gone": True})
    assert bar.pill.property("card")["id"] == "stream-t2"
    bar.send(kind="card", turn=1, card={"id": "stream-t2", "gone": True})
    assert bar.pill.property("card") is None


def test_a_connection_lost_mid_drawing_takes_the_half_picture_away(bar):
    from bombadil import cards
    half = cards.partial_diagram('{"shape": "chain", "title": "How a VPN works", "nodes": [{"label": "Laptop"}, {"label": "Tun')
    _card_event(bar, {**half, "id": "stream-t1"}, turn=1)
    bar.call("lost")
    assert bar.pill.property("card") is None
    _card_event(bar, _diagram())
    bar.call("lost")
    assert bar.pill.property("card") is not None     # a finished picture is still true


def test_the_cross_puts_it_away_and_so_does_esc(bar):
    _card_event(bar, _diagram())
    bar.click("cardClose")
    assert bar.pill.property("card") is None
    _card_event(bar, _diagram(id="card-2"))
    bar.call("dismiss")
    assert bar.pill.property("card") is None


def test_a_receipt_fades_with_the_closing_line_but_a_picture_you_asked_for_stays(bar):
    bar.send(kind="turn_start", turn=1, prompt="start the vpn")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=False, summary="Started the VPN.")
    _card_event(bar, {**_diagram(title="Network, before and after"), "receipt": True}, turn=1)
    assert bar.pill.property("fadeAfter") >= 15000          # read the two together
    bar.call("fade")
    assert bar.pill.property("card") is None and bar.pill.property("mode") == "idle"
    _card_event(bar, _diagram(id="card-2"))
    bar.send(kind="local", turn=None, action="picture", phase="done", ok=True, text="Here is how you are connected.")
    assert bar.pill.property("fadeAfter") == 8000
    bar.call("fade")
    assert bar.pill.property("card") is not None and bar.pill.property("mode") == "idle"


def test_a_picture_that_fails_puts_the_old_one_away_but_a_failed_click_does_not(bar):
    _card_event(bar, _diagram())
    bar.send(kind="local", turn=None, action="picture", target="boot", phase="start", text="Drawing your boot")
    assert bar.pill.property("card") is not None                # still there while the new one is read
    bar.send(kind="local", turn=None, action="picture", target="boot", phase="done", ok=False,
             text="Could not read the boot: systemd-analyze took longer than expected.")
    assert bar.pill.property("card") is None
    assert bar.pill.property("source") == "error"
    _card_event(bar, _diagram(id="card-2"))
    bar.send(kind="local", turn=None, action="open", target="nginx.service", phase="done", ok=False, text="Could not open nginx.service.")
    assert bar.pill.property("card") is not None                # the picture is still true; only the click failed


def test_a_window_the_launcher_opens_puts_the_picture_away_but_hiding_one_does_not(bar):
    # The picture sits over the middle of the screen, where a window opens: the Brain's list was under it.
    def opened(action, verb, ok=True, phase="done"):
        _card_event(bar, _diagram())
        bar.send(kind="local", turn=None, action=action, target="x", verb=verb, phase=phase, ok=ok, text="Opened.")
        return bar.pill.property("card") is None
    for action in ("brain", "app", "panel"):
        assert opened(action, "open"), action
    assert not opened("brain", "open", ok=False)                 # nothing opened: the picture is still true
    assert not opened("brain", "open", phase="start")           # it goes once the window is up
    assert not opened("panel", "hide") and not opened("app", "close")
    assert not opened("open", "open")                           # a click on a box in the picture keeps it
    assert not opened("undo", "open")


def test_hovering_the_picture_keeps_the_line_from_fading(bar):
    _card_event(bar, _diagram())
    it = bar.item("cardHost")
    centre = it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()
    QtTest.QTest.mouseMove(bar.win, centre)
    bar.pump(0.2)
    assert bar.pill.property("hovers") >= 1
    QtTest.QTest.mouseMove(bar.win, QtCore.QPoint(2, 2))
    bar.pump(0.2)
    assert bar.pill.property("hovers") == 0


def test_a_box_that_names_a_thing_opens_it_through_agentd(bar):
    _card_event(bar, _diagram(nodes=[{"label": "NetworkManager", "opens": {"kind": "unit", "value": "NetworkManager.service"}},
                                     {"label": "Turn 3", "opens": {"kind": "turn", "value": "3"}},
                                     {"label": "Nothing to open"}]))
    bar.pump(0.5)       # the card grows and fades in; a click lands on the box only once it has stopped moving
    before = len(bar.sent)
    bar.click("box-n1")
    assert bar.sent[before:] == [{"type": "open", "kind": "unit", "value": "NetworkManager.service"}]
    bar.click("box-n2")
    assert bar.sent[-1] == {"type": "details", "turn": 3}
    n = len(bar.sent)
    bar.click("box-n3")
    assert len(bar.sent) == n


def test_nothing_is_sent_to_agentd_while_it_is_away(bar):
    _card_event(bar, _diagram(nodes=[{"label": "NetworkManager", "opens": {"kind": "unit", "value": "NetworkManager.service"}}]))
    bar.call("lost")
    n = len(bar.sent)
    bar.click("box-n1")
    assert len(bar.sent) == n and bar.pill.property("flash") == "Not connected to the agent yet."


@pytest.mark.parametrize("card", [None, 3, "a string", {"type": "list", "id": "x"}, {"gone": True}])
def test_odd_cards_change_nothing(bar, card):
    _card_event(bar, _diagram())
    bar.send(kind="card", turn=None, card=card)
    assert bar.pill.property("card")["id"] == "card-1" and bar.warnings == []


def test_a_tall_picture_scrolls_inside_the_bar_instead_of_running_off_the_screen(bar):
    nodes = [{"id": f"s{i}", "label": f"Step {i}"} for i in range(1, 13)]
    links = [{"from": f"s{i}", "to": f"s{i + 1}"} for i in range(1, 12)]
    _card_event(bar, _diagram(shape="layers", nodes=nodes, links=links))
    bar.pump(0.4)
    scroll = bar.item("cardScroll")
    assert scroll.property("contentHeight") > scroll.property("height") >= 100
    assert bar.item("cardHost").property("height") <= 520 + 40
    bar.snap("card-tall")


CHOOSE = dict(type="setup", state="choose", line="Which AI should run this computer?", tone="ask",
              actions=[{"id": "provider:claude", "label": "Claude", "style": "big"},
                       {"id": "provider:codex", "label": "Codex", "style": "big"}])


def test_first_boot_asks_which_ai_with_two_big_chips(bar):
    bar.send(**CHOOSE)
    assert bar.text() == "Which AI should run this computer?"
    assert [label for label, _ in bar.chips()] == ["Claude", "Codex"]
    assert bar.chips()[0][1].height() > 40
    bar.snap("setup-1-choose")
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "provider:claude"}


def test_a_prompt_before_the_ai_is_ready_waits_without_on_it(bar):
    bar.send(**CHOOSE)
    assert bar.call("submit", "make me a password manager") is True
    assert bar.sent[-1] == {"type": "prompt", "text": "make me a password manager"}
    assert bar.text() == "Which AI should run this computer?" and bar.pill.property("mode") == "setup"
    bar.send(kind="queued", turn=1, prompt="make me a password manager")
    assert bar.shown("queuedChip")
    bar.snap("setup-2-waiting-prompt")


def test_signing_in_says_where_and_esc_calls_it_off(bar):
    bar.send(type="setup", state="signing_in", line="Sign in to Claude in the browser", tone="step",
             actions=[{"id": "cancel", "label": "Cancel", "style": "quiet"}], phase="waiting", view="shown")
    assert bar.text() == "Sign in to Claude in the browser"
    assert [label for label, _ in bar.chips()] == ["Cancel"]
    assert bar.pill.property("stoppable") is True
    bar.snap("setup-3-signing-in")
    bar.call("stop")
    assert bar.sent[-1] == {"type": "stop"}
    bar.send(type="setup", state="signing_in", line="The Claude sign-in is waiting in the browser", tone="step",
             actions=[{"id": "show", "label": "Show sign-in", "style": "primary"},
                      {"id": "cancel", "label": "Cancel", "style": "quiet"}], phase="waiting", view="hidden")
    assert [label for label, _ in bar.chips()] == ["Show sign-in", "Cancel"]
    bar.snap("setup-4-hidden")
    before = bar.win.property("handOffs")
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "show"}
    # The page that slides in takes the keyboard: typed passwords must not land in the pill.
    assert bar.win.property("handOffs") == before + 1
    bar.click_item(bar.chips()[1][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "cancel"}
    assert bar.win.property("handOffs") == before + 1   # cancelling opens nothing


def test_offline_and_failures_read_as_errors(bar):
    bar.send(type="setup", state="offline", line="No internet. Connect to a network to sign in to Claude.",
             tone="error", actions=[{"id": "wifi", "label": "Wi-Fi", "style": "primary"},
                                    {"id": "signin", "label": "Try again", "style": "quiet"}])
    assert bar.item("statusLine").property("edge").name() == THEME["bad"]
    assert [label for label, _ in bar.chips()] == ["Wi-Fi", "Try again"]
    bar.snap("setup-5-offline")
    bar.send(type="setup", state="signed_out", line="The Claude sign-in timed out.", tone="error",
             actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    assert bar.text() == "The Claude sign-in timed out." and bar.pill.property("stoppable") is False
    bar.pump(0.3)
    assert bar.pill.property("mode") == "setup"   # it stays until signed in; nothing fades it


def test_signed_in_is_said_once_and_fades(bar):
    bar.send(type="setup", state="signing_in", line="Finishing the Claude sign-in", tone="step", actions=[])
    bar.send(type="setup", state="ready", line="Signed in to Claude. Ask me for anything.", tone="done", actions=[])
    assert bar.text() == "Signed in to Claude. Ask me for anything."
    assert bar.pill.property("mode") == "local" and not bar.chips()
    bar.snap("setup-6-signed-in")
    bar.pill.setProperty("lineAt", bar.pill.property("lineAt") - 9000)
    bar.pump(0.6)
    assert bar.pill.property("mode") == "idle"


def test_a_turn_keeps_its_line_and_the_setup_comes_back_after(bar):
    signed_out = dict(type="setup", state="signed_out", line="Sign in to Claude to start.", tone="step",
                      actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    bar.send(**signed_out)
    bar.call("submit", "!ls")
    bar.send(kind="turn_start", turn=1, prompt="!ls")
    bar.send(**signed_out)
    assert bar.text() == "On it" and not bar.chips()
    bar.send(kind="result", turn=1, ok=True, text="Apps  Documents")
    bar.send(kind="turn_end", turn=1, seconds=0.1, changed=False, summary="")
    assert bar.text() == "Apps  Documents"
    bar.call("dismiss")
    assert bar.text() == "Sign in to Claude to start." and [label for label, _ in bar.chips()] == ["Sign in"]


def test_a_line_that_fades_before_sign_in_brings_the_setup_back_and_leaves_a_picture_you_asked_for(bar):
    bar.send(type="setup", state="signed_out", line="Sign in to Claude to start.", tone="step",
             actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    bar.send(kind="local", turn=None, action="picture", phase="done", ok=True, text="Showing how you're connected.")
    _card_event(bar, _diagram())
    bar.call("fade")                 # the line's own timer, not Esc
    assert bar.text() == "Sign in to Claude to start." and bar.pill.property("card") is not None
    bar.call("dismiss")
    assert bar.pill.property("card") is None


READY = dict(type="setup", state="ready", tone="done", actions=[])


def test_switching_to_another_ai_says_so(bar):
    bar.send(**READY, line="")
    bar.send(**READY, line="Codex is ready. Ask me for anything.")
    assert bar.text() == "Codex is ready. Ask me for anything." and bar.pill.property("mode") == "local"


def test_a_bar_that_starts_later_does_not_announce_an_old_sign_in(bar):
    bar.send(**READY, line="Signed in to Claude. Ask me for anything.")   # the greeting of a restarted bar
    assert bar.pill.property("mode") == "idle"


def test_a_prompt_before_the_ai_is_ready_brings_the_setup_line_back_over_a_finished_one(bar):
    bar.send(**CHOOSE)
    bar.call("submit", "!echo hi")
    bar.send(kind="turn_start", turn=1, prompt="!echo hi")
    bar.send(kind="result", turn=1, ok=True, text="hi")
    bar.send(kind="turn_end", turn=1, seconds=0.1, changed=True, summary="Ran it")
    assert bar.pill.property("mode") == "closing"
    bar.call("submit", "make me an app")
    assert bar.pill.property("mode") == "setup" and [label for label, _ in bar.chips()] == ["Claude", "Codex"]

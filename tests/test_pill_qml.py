"""The line above the pill, driven by agentd's events in an offscreen window.

PillState, StatusLine and QueueChips are plain Qt Quick (Quickshell only wraps them in
shell.qml), so they load here without a compositor. Set BOMBADIL_SCREENS=<dir> to save a
picture of each state.
"""

import os
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

SHELL = Path(__file__).resolve().parents[1] / "shell"

HARNESS = """
import QtQuick
import QtQuick.Layouts
import "%s"

Window {
    id: w
    width: 820; height: 260 + (w.screens - 1) * 200; visible: true
    color: "#3b4a5a"
    property var sent: []
    property int screens: 1
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
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
    assert bar.item("statusLine").property("edge").name() == "#e8a33d"
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


def test_undo_on_the_line_does_not_also_open_details(bar):
    bar.call("submit", "install ffmpeg")
    bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    bar.send(kind="turn_end", turn=1, seconds=3, changed=True, summary="Installed ffmpeg.")
    before = len(bar.sent)
    bar.click("undoButton")
    assert bar.sent[before:] == [{"type": "local", "action": "undo"}]
    bar.click("detailsButton")
    assert bar.sent[-1] == {"type": "details", "turn": 1}


def test_an_irreversible_step_is_red_and_says_so_after(bar):
    bar.call("submit", "wipe the usb stick")
    bar.send(kind="turn_start", turn=2, prompt="wipe the usb stick")
    bar.send(kind="status", turn=2, text="Formatting /dev/sdb1", source="step", risk="irreversible",
             command="sudo mkfs.ext4 /dev/sdb1")
    assert bar.item("statusLine").property("edge").name() == "#e05252"
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
    assert bar.item("statusLine").property("edge").name() == "#c04a4a"
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
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "show"}


def test_offline_and_failures_read_as_errors(bar):
    bar.send(type="setup", state="offline", line="No internet. Connect to a network to sign in to Claude.",
             tone="error", actions=[{"id": "wifi", "label": "Wi-Fi", "style": "primary"},
                                    {"id": "signin", "label": "Try again", "style": "quiet"}])
    assert bar.item("statusLine").property("edge").name() == "#c04a4a"
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

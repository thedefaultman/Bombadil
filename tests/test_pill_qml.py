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
    width: 820; height: 260; visible: true
    color: "#3b4a5a"
    property var sent: []
    PillState {
        id: pill
        objectName: "pill"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
    }
    ColumnLayout {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
        spacing: 8
        StatusLine { objectName: "statusLine"; pill: pill; Layout.fillWidth: true }
        QueueChips { objectName: "chips"; pill: pill; Layout.alignment: Qt.AlignHCenter }
        Rectangle { Layout.fillWidth: true; implicitHeight: 52; radius: 26; color: "#f01a1d21" }
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
        return self.win.findChild(QtQuick.QQuickItem, name)

    def text(self, name="line"):
        return self.item(name).property("text")

    def items(self, name):
        """Visible items by objectName, found through the visual tree (Repeater items included)."""
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and it.isVisible():
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


def test_the_harness_loads_without_qml_warnings(bar):
    assert [w for w in bar.warnings if "StatusLine" in w or "PillState" in w or "QueueChips" in w] == []

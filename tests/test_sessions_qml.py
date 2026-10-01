"""The dots beside the pill and the peek, driven by agentd's "dev" messages offscreen.

DevState, SessionChips and SessionPeek are plain Qt Quick, like the rest of the bar's parts.
Set BOMBADIL_SCREENS=<dir> to save a picture of each state.
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
    width: 900; height: 260; visible: true
    color: "#3b4a5a"
    property var sent: []
    DevState {
        id: devState
        objectName: "dev"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
    }
    // A delegate, like the bar's PanelWindow in Variants.
    Repeater {
        model: 1
        ColumnLayout {
            parent: w.contentItem
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
            spacing: 8
            SessionPeek { dev: devState; Layout.alignment: Qt.AlignLeft }
            RowLayout {
                spacing: 10
                SessionChips { objectName: "chips"; dev: devState }
                Rectangle { Layout.fillWidth: true; implicitHeight: 52; radius: 26; color: "#f01a1d21" }
            }
        }
    }
}
"""


def _s(key, state="idle", **kw):
    project, role = key.split("/")
    title = kw.pop("projectTitle", project.capitalize())
    base = {"key": key, "project": project, "projectTitle": title, "role": role, "tool": "claude",
            "toolTitle": "Claude", "title": f"{role} on {title}", "state": state, "alive": True, "unseen": False,
            "yours": False, "copy": False, "since": 0, "last": "", "lines": []}
    base.update(kw)
    return base


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
        self.dev = self.win.findChild(QtCore.QObject, "dev")
        self.pump()

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    def send(self, sessions, attention=(), line="", front=""):
        ev = {"type": "dev", "sessions": sessions, "attention": list(attention), "line": line, "front": front}
        QtCore.QMetaObject.invokeMethod(self.dev, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()

    def items(self, name, visible_only=True):
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return found

    def dots(self):
        return sorted((str(d.property("key")), str(d.property("look"))) for d in self.items("sessionDot"))

    def hover(self, item):
        centre = item.mapToScene(QtCore.QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QtTest.QTest.mouseMove(self.win, centre)
        self.pump()

    def click(self, item):
        centre = item.mapToScene(QtCore.QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, centre)
        self.pump()

    @property
    def sent(self):
        v = self.win.property("sent")
        return [dict(m) for m in (v.toVariant() if hasattr(v, "toVariant") else v)]

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.3)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"{name}.png"))


@pytest.fixture
def bar(app, tmp_path):
    b = Bar(app, tmp_path)
    yield b
    b.engine.deleteLater()


def test_no_sessions_no_chips(bar):
    bar.send([])
    assert not bar.items("projectChip") and not bar.items("sessionDot")
    assert not bar.warnings


def test_a_chip_per_project_and_a_dot_per_session(bar):
    bar.send([_s("bombadil/builder", "working"), _s("bombadil/reviewer", "asked", unseen=True, last="Run it?"),
              _s("bombadil/kit", "failed", alive=False, unseen=True), _s("myapp/api", "done", unseen=True),
              _s("myapp/claude", "asleep", alive=False)],
             attention=["bombadil/reviewer", "myapp/api", "bombadil/kit"], line="reviewer, api and kit are waiting")
    assert len(bar.items("projectChip")) == 2
    assert bar.dots() == [("bombadil/builder", "working"), ("bombadil/kit", "failed"), ("bombadil/reviewer", "turn"),
                          ("myapp/api", "turn"), ("myapp/claude", "asleep")]
    bar.snap("dots-two-projects")
    assert not bar.warnings


def test_hovering_a_dot_peeks_and_clicking_brings_it_back(bar):
    bar.send([_s("bombadil/reviewer", "asked", unseen=True, last="Run the migration?",
                 lines=["› fix the flaky test", "? Run the migration?"])])
    dot = bar.items("sessionDot")[0]
    bar.hover(dot)
    assert bar.items("sessionPeek")
    title = bar.items("peekTitle")[0].property("text")
    state = bar.items("peekState")[0].property("text")
    lines = bar.items("peekLines")[0].property("text")
    assert (title, state) == ("reviewer on Bombadil", "Claude · waiting for you")
    assert lines == "› fix the flaky test\n? Run the migration?"
    bar.snap("peek")
    bar.click(dot)
    assert bar.sent[-1] == {"type": "dev", "action": "open", "key": "bombadil/reviewer"}
    QtTest.QTest.mouseMove(bar.win, QtCore.QPoint(850, 20))
    bar.pump()
    assert not bar.items("sessionPeek")


def test_a_red_dot_that_stopped_shows_why_first(bar):
    bar.send([_s("bombadil/kit", "failed", alive=False, unseen=True, last="Claude stopped unexpectedly (exit 1)")])
    bar.click(bar.items("sessionDot")[0])
    assert bar.sent[-1] == {"type": "dev", "action": "why", "key": "bombadil/kit"}


def test_yours_are_rings_and_more_than_three_projects_fold(bar):
    many = [_s(f"p{i}/claude", "idle") for i in range(4)] + [_s("p1/mine", "working", yours=True)]
    bar.send(many, attention=["p0/claude"])
    assert len(bar.items("projectChip")) == 0
    fold = bar.items("foldChip")
    assert fold and "4 projects · 1 waiting" in [t.property("text") for t in fold[0].childItems()]
    bar.snap("folded")
    bar.click(fold[0])
    assert len(bar.items("projectChip")) == 4
    assert ("p1/mine", "working") in bar.dots()
    bar.snap("unfolded")
    bar.send(many[:2])
    assert not bar.dev.property("expanded")   # back under the fold limit: the next fold starts closed


def test_the_line_and_next(bar):
    bar.send([_s("bombadil/reviewer", "asked", unseen=True)], attention=["bombadil/reviewer"],
             line="reviewer on Bombadil: run the migration?")
    assert bar.dev.property("line") == "reviewer on Bombadil: run the migration?"
    QtCore.QMetaObject.invokeMethod(bar.dev, "next")
    bar.pump()
    assert bar.sent[-1] == {"type": "dev", "action": "next"}
    QtCore.QMetaObject.invokeMethod(bar.dev, "lost")
    bar.pump()
    assert bar.dev.property("line") == "" and bar.items("sessionDot")   # the dots stay

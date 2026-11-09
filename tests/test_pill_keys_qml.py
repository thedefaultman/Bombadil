"""PillKeys: the global shortcuts that give the pill the keyboard, with Quickshell's GlobalShortcut stood in for.

Quickshell is not here (and Hyprland would be the one to fire a shortcut), so Quickshell.Hyprland is a
directory with the one type PillKeys uses, declared as Quickshell does: appid and name, and two signals
that carry nothing. The real PillKeys and PillState load as shell.qml loads them.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)

SHELL = Path(__file__).resolve().parents[1] / "shell"

GLOBAL_SHORTCUT = """
import QtQuick
QtObject {
    property string appid: "quickshell"
    property string name
    property string description
    signal pressed()
    signal released()
}
"""

HARNESS = """
import QtQuick
import "%s"

Window {
    id: w
    property bool hyprland: true
    property var summons: []
    // What shell.qml does: summoned goes on to the screen's pill (root.summon).
    // (Not "pill": inside PillKeys { pill: ... } that name is its own property.)
    PillState { id: pillState; onSummoned: text => w.summons = w.summons.concat([text]) }
    Loader { id: keys; active: w.hyprland; sourceComponent: PillKeys { pill: pillState } }
    // The shortcuts the bar registered, as [appid:name].
    function shortcuts() { return keys.item ? [keys.item.tap, keys.item.key] : [] }
    function registered() { return shortcuts().map(s => s.appid + ":" + s.name) }
    // What Hyprland does when a bind that names one fires: the edge it sends is the bind's.
    function fire(shortcut, edge) {
        for (const s of shortcuts()) if (s.appid + ":" + s.name === shortcut) s[edge]()
    }
}
"""


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Keys:
    def __init__(self, tmp_path, hyprland=True):
        stub = tmp_path / "qml" / "Quickshell" / "Hyprland"
        stub.mkdir(parents=True)
        (stub / "qmldir").write_text("module Quickshell.Hyprland\nGlobalShortcut 1.0 GlobalShortcut.qml\n")
        (stub / "GlobalShortcut.qml").write_text(GLOBAL_SHORTCUT)
        (tmp_path / "harness.qml").write_text(HARNESS % SHELL.as_uri())
        self.engine = QtQml.QQmlApplicationEngine()
        self.engine.addImportPath(str(tmp_path / "qml"))
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.setInitialProperties({"hyprland": hyprland})
        self.engine.load(QtCore.QUrl.fromLocalFile(str(tmp_path / "harness.qml")))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]

    def call(self, name, *args):
        got = QtCore.QMetaObject.invokeMethod(
            self.win, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        return got.toVariant() if hasattr(got, "toVariant") else got

    def fire(self, shortcut, edge):
        self.call("fire", shortcut, edge)

    @property
    def registered(self):
        return list(self.call("registered"))

    @property
    def summons(self):
        got = self.win.property("summons")
        return list(got.toVariant() if hasattr(got, "toVariant") else got)


@pytest.fixture
def keys(app, tmp_path):
    k = Keys(tmp_path)
    yield k
    assert k.warnings == []
    k.win.close()
    k.engine.deleteLater()


def test_the_bar_registers_the_two_shortcuts_the_binds_name(keys):
    assert sorted(keys.registered) == ["bombadil:pill-press", "bombadil:pill-release"]


def test_a_tap_summons_the_pill_when_super_is_let_go_and_not_before(keys):
    keys.fire("bombadil:pill-release", "pressed")
    assert keys.summons == []
    keys.fire("bombadil:pill-release", "released")
    assert keys.summons == [""]


def test_alt_space_summons_the_pill_when_it_goes_down_and_not_a_second_time_when_it_comes_up(keys):
    # Hyprland sends the press, and the release of the same key after it.
    keys.fire("bombadil:pill-press", "pressed")
    keys.fire("bombadil:pill-press", "released")
    assert keys.summons == [""]


def test_each_tap_is_one_summon_the_bar_toggles(keys):
    for _ in range(3):
        keys.fire("bombadil:pill-release", "released")
    assert keys.summons == ["", "", ""]


def test_there_are_no_shortcuts_where_there_is_no_hyprland(app, tmp_path):
    k = Keys(tmp_path, hyprland=False)
    try:
        assert k.registered == [] and k.warnings == []
    finally:
        k.win.close()
        k.engine.deleteLater()

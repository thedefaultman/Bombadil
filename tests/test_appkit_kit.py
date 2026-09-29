"""The kit's QML components (share/qml/Bombadil), driven from small QML snippets.

Each test runs in a child process with its own offscreen app and engine: the `App`
singleton belongs to the first engine that reads it, and test_appkit_native has its own.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

# Runs in the child: creates the snippet as `root`, runs the test's body, prints `out` as JSON.
PRELUDE = """\
import json, sys, time
from pathlib import Path
from PySide6.QtCore import QEventLoop, QMetaObject, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlComponent
from PySide6.QtTest import QTest
from bombadil.appkit import engine
from bombadil.appkit.context import AppContext
from bombadil.appkit.native.files import from_js as js

d = Path(sys.argv[1])
ctx = AppContext("kit-test", "Kit Test", d, d / "main.qml")
qt = engine.make_app(ctx)
qml = engine.make_engine(ctx)
comp = QQmlComponent(qml)
comp.setData(("import QtQuick\\nimport Bombadil\\n" + sys.argv[2]).encode(), QUrl.fromLocalFile(str(ctx.main)))
root = comp.create()
assert root is not None, [e.toString() for e in comp.errors()]
out = {}

def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

def wait_until(cond, ms=2000):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end and not cond():
        spin(20)
    return cond()

def hexa(color):
    return color.name(QColor.NameFormat.HexArgb)

def click(x, y):
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))

def press(x, y):
    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))

def release(x, y):
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))

def double_click(x, y):
    QTest.mouseDClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))

# One finger down at (x, y), dragged dy pixels and lifted (dy 0 is a tap).
def swipe(x, y, dy):
    dev = QTest.createTouchDevice()
    def step(kind, at):
        seq = QTest.touchEvent(root, dev)
        getattr(seq, kind)(0, QPoint(x, at), root)
        seq.commit()
        spin(16)
    step("press", y)
    for i in range(1, 21):
        step("move", y + dy * i // 20)
    step("release", y + dy)
"""


def run(home: Path, qml: str, body: str) -> dict:
    """Creates `qml` (after `import QtQuick` and `import Bombadil`) and runs `body` on it."""
    d = home / "kit-app"
    d.mkdir(exist_ok=True)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
           "PYTHONPATH": str(ROOT / "src")}
    script = PRELUDE + textwrap.dedent(body) + "\nprint('RESULT ' + json.dumps(out))\n"
    r = subprocess.run([sys.executable, "-c", script, str(d), qml], capture_output=True, text=True,
                       env=env, timeout=60)
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"child failed (exit {r.returncode}):\n{r.stdout}\n{r.stderr}"
    return json.loads(lines[-1].removeprefix("RESULT "))


# -- AppWindow --

def test_appwindow_takes_the_first_milestone_window_properties(home):
    """AppWindow was an ApplicationWindow: apps that set its window properties still load."""
    out = run(home, """
AppWindow {
    width: 400; height: 300
    title: "Old"
    color: "#123456"
    font.pixelSize: 15
    minimumWidth: 300; minimumHeight: 200; maximumWidth: 900; maximumHeight: 800
    menuBar: Rectangle { height: 10 }
    header: Rectangle { height: 30 }
    footer: Rectangle { height: 20 }
    onClosing: close => close.accepted = true
    function dropHeader() { header = null }
    Text { text: "content" }
}""", """
out["color"] = hexa(root.property("color"))
for k in ("menuBar", "header", "footer"):
    bar = root.property(k)
    out[k] = [bar.parentItem() is not None, bar.width(), root.mapFromItem(bar, QPointF(0, 0)).y()]
old = root.property("header")
QMetaObject.invokeMethod(root, "dropHeader")
out["dropped"] = old.parentItem() is None
""")
    assert out == {"color": "#ff123456", "menuBar": [True, 400, 0], "header": [True, 400, 10],
                   "footer": [True, 400, 280], "dropped": True}


# -- ItemList --

def test_item_list_rows_that_are_buttons_select_on_click(home):
    """An ItemDelegate takes the press itself; a click on it still selects and activates."""
    out = run(home, """
import QtQuick.Controls
Window {
    width: 300; height: 300; visible: true
    property Item list: il
    property var log: []
    property int ownClicks: 0
    ItemList {
        id: il
        anchors.fill: parent
        model: [{ id: 1, title: "one" }, { id: 2, title: "two" }, { id: 3, title: "three" }]
        delegate: ItemDelegate {
            required property var modelData
            width: ListView.view.width
            height: 40
            text: modelData.title
            highlighted: ListView.isCurrentItem
            onClicked: ownClicks++
        }
        onActivated: (i, item) => log = log.concat([i, item.title])
    }
}""", """
QTest.qWaitForWindowExposed(root)
il = root.property("list")
click(100, 60)
out["first"] = wait_until(lambda: il.property("currentIndex") == 1)
for _ in range(3):   # new rows every time, each hooked once
    il.setProperty("model", [{"id": i, "title": t} for i, t in enumerate(["one", "two", "three"], 1)])
    spin(20)
click(100, 15)
out["second"] = wait_until(lambda: il.property("currentIndex") == 0)
out["log"] = js(root.property("log"))
out["ownClicks"] = root.property("ownClicks")
""")
    assert out == {"first": True, "second": True, "log": [1, "two", 0, "one"], "ownClicks": 2}


MOVING_ROWS = {
    "buttons": ("ItemDelegate", "CheckDelegate", "text: modelData.title"),
    "list_rows": ("ListRow", "ListRow", "title: modelData.title; selected: ListView.isCurrentItem"),
}


@pytest.mark.parametrize("rows", MOVING_ROWS.values(), ids=MOVING_ROWS.keys())
def test_item_list_rows_that_move_their_item_keep_it_selected(home, rows):
    """The row's own onClicked runs first: the click is about its item, wherever it went."""
    mail_row, todo_row, label = rows
    out = run(home, """
import QtQuick.Controls
Window {
    width: 600; height: 300; visible: true
    property Item mailList: mailList
    property Item todoList: todoList
    property var log: []
    // Unread first: opening "a" marks it read and sorts it below "b".
    property var mail: [{ id: 1, title: "a", read: false }, { id: 2, title: "b", read: false },
                        { id: 3, title: "c", read: true }]
    // Done items leave the list: ticking "b" removes it.
    property var todos: [{ id: 1, title: "a", done: false }, { id: 2, title: "b", done: false },
                         { id: 3, title: "c", done: false }]
    ItemList {
        id: mailList
        x: 0; width: 300; height: 300
        model: mail.slice().sort((p, q) => (p.read - q.read) || (p.id - q.id))
        delegate: MAIL_ROW {
            required property var modelData
            width: ListView.view.width
            height: 40
            LABEL
            onClicked: mail = mail.map(m => m.id === modelData.id ? Object.assign({}, m, { read: true }) : m)
        }
        onActivated: (i, item) => log = log.concat(["mail", i, item.title])
    }
    ItemList {
        id: todoList
        x: 300; width: 300; height: 300
        model: todos.filter(t => !t.done)
        delegate: TODO_ROW {
            required property var modelData
            width: ListView.view.width
            height: 40
            LABEL
            onClicked: todos = todos.map(t => t.id === modelData.id ? Object.assign({}, t, { done: true }) : t)
        }
        onActivated: (i, item) => log = log.concat(["todo", i, item.title])
    }
}""".replace("MAIL_ROW", mail_row).replace("TODO_ROW", todo_row).replace("LABEL", label), """
QTest.qWaitForWindowExposed(root)
mail, todo = root.property("mailList"), root.property("todoList")
click(100, 15)
spin(50)
out["mail"] = [mail.property("currentIndex"), js(mail.property("current"))]
click(400, 60)
spin(50)
out["todo"] = [todo.property("currentIndex"), js(todo.property("current"))]
out["log"] = js(root.property("log"))
""")
    assert out == {"mail": [1, {"id": 1, "title": "a", "read": True}], "todo": [-1, None],
                   "log": ["mail", 1, "a"]}


def test_item_list_rows_of_the_default_delegate_activate_once_per_click(home):
    out = run(home, """
Window {
    width: 300; height: 300; visible: true
    property Item list: il
    property var log: []
    ItemList {
        id: il
        anchors.fill: parent
        model: [{ id: 1, title: "one" }, { id: 2, title: "two" }]
        onActivated: (i, item) => log = log.concat([i])
    }
}""", """
QTest.qWaitForWindowExposed(root)
il = root.property("list")
click(100, 60)
out["selected"] = wait_until(lambda: il.property("currentIndex") == 1)
spin(50)
out["log"] = js(root.property("log"))
""")
    assert out == {"selected": True, "log": [1]}


def test_item_list_a_tap_is_about_the_item_pressed_when_the_model_changes_under_it(home):
    """A poller replaces the array between press and release: not whatever moved under the pointer."""
    out = run(home, """
Window {
    width: 300; height: 300; visible: true
    property Item list: il
    property var log: []
    ItemList {
        id: il
        anchors.fill: parent
        model: [{ id: 1, title: "one" }, { id: 2, title: "two" }, { id: 3, title: "three" },
                { id: 4, title: "four" }]
        onActivated: (i, item) => log = log.concat([[i, item.title]])
    }
}""", """
QTest.qWaitForWindowExposed(root)
il = root.property("list")
items = {t: {"id": i, "title": t} for i, t in enumerate(["one", "two", "three", "four"], 1)}
def pick(*names):
    return [items[n] for n in names]
state = lambda: [il.property("currentIndex"), js(il.property("current")), js(root.property("log"))]
press(100, 60)   # "two", which then moves to the end
il.setProperty("model", pick("one", "three", "four", "two"))
release(100, 60)
spin(50)
out["moved"] = state()
root.setProperty("log", [])
press(100, 15)   # "one", which then goes away
il.setProperty("model", pick("three", "four", "two"))
release(100, 15)
spin(50)
out["removed"] = state()
""")
    assert out == {"moved": [3, {"id": 2, "title": "two"}, [[3, "two"]]], "removed": [-1, None, []]}


ACTIVATING_ROWS = {"default": "", "list_rows": "delegate: ListRow { required property var modelData; "
                   "width: ListView.view.width; title: modelData.title; selected: ListView.isCurrentItem }"}


@pytest.mark.parametrize("delegate", ACTIVATING_ROWS.values(), ids=ACTIVATING_ROWS.keys())
def test_item_list_a_double_click_activates_once(home, delegate):
    out = run(home, """
import QtQuick.Controls
Window {
    width: 300; height: 300; visible: true
    property Item list: il
    property var log: []
    ItemList {
        id: il
        anchors.fill: parent
        model: [{ id: 1, title: "one" }, { id: 2, title: "two" }]
        DELEGATE
        onActivated: (i, item) => log = log.concat([i])
    }
}""".replace("DELEGATE", delegate), """
QTest.qWaitForWindowExposed(root)
double_click(100, 60)
spin(300)
out["log"] = js(root.property("log"))
out["index"] = root.property("list").property("currentIndex")
""")
    assert out == {"log": [1], "index": 1}


def _scrolling_list(delegate):
    return """
import QtQuick.Controls
Window {
    width: 300; height: 300; visible: true
    property Item list: il
    property var log: []
    ItemList {
        id: il
        anchors.fill: parent
        model: Array.from({ length: 50 }, (_, i) => ({ id: i, title: "row " + i }))
        DELEGATE
        onActivated: (i, item) => log = log.concat([i])
    }
}""".replace("DELEGATE", delegate)


TOUCH_ROWS = {"default": "", "buttons": "delegate: ItemDelegate { required property var modelData; "
              "width: ListView.view.width; height: 40; text: modelData.title }"}


@pytest.mark.parametrize("delegate", TOUCH_ROWS.values(), ids=TOUCH_ROWS.keys())
def test_item_list_a_touch_scroll_selects_nothing_but_a_tap_does(home, delegate):
    out = run(home, _scrolling_list(delegate), """
QTest.qWaitForWindowExposed(root)
il = root.property("list")
view = next(c for c in il.childItems() if c.metaObject().className().startswith("QQuickListView"))
swipe(100, 230, -190)
spin(300)
out["scrolled"] = view.property("contentY") > 100
out["after"] = [il.property("currentIndex"), js(root.property("log"))]
view.setProperty("contentY", 0)
swipe(100, 15, 0)
spin(300)
out["tap"] = [il.property("currentIndex"), js(root.property("log"))]
""")
    assert out == {"scrolled": True, "after": [-1, []], "tap": [0, [0]]}


def test_item_list_drops_the_selection_when_its_item_is_gone(home):
    """Same count, other items: an item with an id is not swapped for its neighbour."""
    out = run(home, """
ItemList {
    width: 300; height: 300
    model: [{ id: 1, title: "apple" }, { id: 2, title: "banana" }]
}""", """
root.setProperty("currentIndex", 1)
root.setProperty("model", [{"id": 1, "title": "apple"}, {"id": 4, "title": "avocado"}])
out["gone"] = [root.property("currentIndex"), js(root.property("current"))]
# Found again under its id: still selected, wherever it is now.
root.setProperty("currentIndex", 1)
root.setProperty("model", [{"id": 4, "title": "avocado 2"}, {"id": 1, "title": "apple"}])
out["moved"] = root.property("currentIndex")
# Items without an id were probably edited in place: the selection keeps its row.
root.setProperty("model", [{"title": "a"}, {"title": "b"}])
root.setProperty("currentIndex", 1)
root.setProperty("model", [{"title": "a"}, {"title": "b, edited"}])
out["edited"] = root.property("currentIndex")
""")
    assert out == {"gone": [-1, None], "moved": 0, "edited": 1}


# -- DataTable --

def test_data_table_drops_the_selection_when_its_row_is_gone(home):
    """A process exits and another starts between two refreshes: End must not move to it."""
    out = run(home, """
DataTable {
    width: 400; height: 300
    columns: [{ key: "pid" }, { key: "name" }]
    rows: [{ pid: 10, name: "A" }, { pid: 20, name: "B" }, { pid: 30, name: "C" }]
}""", """
root.setProperty("currentIndex", 1)
out["before"] = js(root.property("current"))
root.setProperty("rows", [{"pid": 10, "name": "A"}, {"pid": 30, "name": "C"}, {"pid": 40, "name": "D"}])
out["gone"] = [root.property("currentIndex"), js(root.property("current"))]
# Rows without an id keep their place when their values change.
root.setProperty("rows", [{"name": "A", "cpu": 1}, {"name": "B", "cpu": 2}])
root.setProperty("currentIndex", 1)
root.setProperty("rows", [{"name": "A", "cpu": 1}, {"name": "B", "cpu": 5}])
out["edited"] = js(root.property("current"))
""")
    assert out == {"before": {"pid": 20, "name": "B"}, "gone": [-1, None], "edited": {"name": "B", "cpu": 5}}


def test_data_table_a_tap_is_about_the_row_pressed_when_a_refresh_reorders_it(home):
    """The memory table re-sorts on every refresh: a click that straddles one keeps its row."""
    out = run(home, """
Window {
    width: 400; height: 300; visible: true
    property Item table: dt
    property real rowY: Theme.controlHeight + 4 + Theme.rowHeight * 1.5
    DataTable {
        id: dt
        anchors.fill: parent
        columns: [{ key: "pid" }, { key: "name" }, { key: "mem" }]
        sortKey: "mem"; sortDescending: true
        rows: [{ pid: 10, name: "A", mem: 300 }, { pid: 20, name: "B", mem: 200 }, { pid: 30, name: "C", mem: 100 }]
    }
}""", """
QTest.qWaitForWindowExposed(root)
dt = root.property("table")
y = int(root.property("rowY"))   # "B"
press(100, y)
dt.setProperty("rows", [{"pid": 10, "name": "A", "mem": 300}, {"pid": 20, "name": "B", "mem": 50},
                        {"pid": 30, "name": "C", "mem": 100}])
release(100, y)
spin(50)
out["current"] = [dt.property("currentIndex"), js(dt.property("current"))["name"]]
""")
    assert out == {"current": [2, "B"]}


def test_data_table_a_double_click_activates_once_and_a_touch_scroll_selects_nothing(home):
    out = run(home, """
Window {
    width: 400; height: 300; visible: true
    property Item table: dt
    property var log: []
    property real rowY: Theme.controlHeight + 4 + Theme.rowHeight * 1.5
    DataTable {
        id: dt
        anchors.fill: parent
        columns: [{ key: "pid" }, { key: "name" }]
        rows: Array.from({ length: 60 }, (_, i) => ({ pid: i, name: "p" + i }))
        onActivated: row => log = log.concat([row.pid])
    }
}""", """
QTest.qWaitForWindowExposed(root)
dt = root.property("table")
y = int(root.property("rowY"))
double_click(100, y)
spin(300)
out["double"] = [dt.property("currentIndex"), js(root.property("log"))]
dt.setProperty("currentIndex", -1)
root.setProperty("log", [])
swipe(100, y + 100, -90)
spin(300)
out["scroll"] = [dt.property("currentIndex"), js(root.property("log"))]
""")
    assert out == {"double": [1, [1]], "scroll": [-1, []]}


def test_data_table_drops_the_selection_when_its_row_leaves_a_list_model(home):
    """The removed element's live object reads as the one that took its place."""
    out = run(home, """
Item {
    width: 400; height: 300
    property Item table: dt
    readonly property string who: dt.current ? dt.current.name : "none"
    function removeFirst() { lm.remove(0) }
    function removeSelected() { lm.remove(dt.currentIndex) }
    ListModel {
        id: lm
        ListElement { pid: 10; name: "A" }
        ListElement { pid: 20; name: "B" }
        ListElement { pid: 30; name: "C" }
        ListElement { pid: 40; name: "D" }
    }
    DataTable {
        id: dt
        anchors.fill: parent
        columns: [{ key: "pid" }, { key: "name" }]
        rows: lm
    }
}""", """
dt = root.property("table")
state = lambda: [dt.property("currentIndex"), root.property("who")]
dt.setProperty("currentIndex", 2)
QMetaObject.invokeMethod(root, "removeFirst")   # C moves up and stays selected
out["before"] = state()
QMetaObject.invokeMethod(root, "removeSelected")
out["removed"] = state()
""")
    assert out == {"before": [1, "C"], "removed": [-1, "none"]}


# -- Theme --

def test_theme_alpha_takes_colors_given_as_strings(home):
    out = run(home, """
QtObject {
    property color series: Theme.alpha(Theme.series[0], 0.5)
    property color token: Theme.alpha(Theme.accent, 0.5)
    property color plain: Theme.alpha("#ff0000", 0)
}""", """
for k in ("series", "token", "plain"):
    out[k] = hexa(root.property(k))
""")
    assert out == {"series": "#803987e5", "token": "#80d97757", "plain": "#00ff0000"}


# -- Fmt --

def test_fmt_dates_that_are_not_numbers_are_a_dash(home):
    out = run(home, """
QtObject {
    property var out: [NaN, Infinity, Date.parse("garbage"), Number(undefined), "99999999999999999"]
        .map(v => [Fmt.time(v), Fmt.date(v), Fmt.dateTime(v), Fmt.relative(v)])
}""", """
out["formatted"] = js(root.property("out"))
""")
    assert out["formatted"] == [["—"] * 4] * 5


# -- Store --

def test_store_saves_a_value_that_keeps_changing(home):
    """Every change within 300 ms of the last used to push the save back, forever."""
    out = run(home, """
Item {
    Store { id: st; name: "busy"; property var samples: [] }
    Timer {
        interval: 100; repeat: true; running: true
        onTriggered: st.samples = st.samples.concat([st.samples.length]).slice(-100)
    }
}""", """
path = ctx.data_dir / "busy.json"
saved = lambda: len(json.loads(path.read_text())["samples"]) if path.exists() else 0
out["saved"] = wait_until(lambda: saved() > 0, 1500)
first = saved()
out["again"] = wait_until(lambda: saved() > first, 1500)
""")
    assert out == {"saved": True, "again": True}

"""The welcome line above the pill, driven by agentd's messages in an offscreen window.

PillState and StatusLine are plain Qt Quick (shell.qml only wraps them in Quickshell types), so they
load here without a compositor. What shell.qml adds (the IdleMonitor that calls touched(), the
fullscreen binding, the typing hooks) cannot run here; the functions it calls are tested directly.
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
import QtQuick.Layouts
import "%s"

Window {
    id: w
    width: 820; height: 260 + (w.screens - 1) * 200; visible: true
    color: "#3b4a5a"
    property var sent: []
    property int screens: 1
    property int hereIndex: 0
    property int shownCount: 0
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => w.sent = w.sent.concat([msg])
        onWelcomeShown: w.shownCount += 1
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
            StatusLine { objectName: "statusLine"; pill: pillState; here: index === w.hereIndex; Layout.fillWidth: true }
            Rectangle { Layout.fillWidth: true; implicitHeight: 52; radius: 26; color: "#f01a1d21" }
        }
    }
}
"""

GREETING = "Good morning, Daniel. Where to today?"


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

    def call(self, name, *args):
        ret = QtCore.QMetaObject.invokeMethod(
            self.pill, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return ret

    def prop(self, name):
        return self.pill.property(name)

    def set(self, name, value):
        self.pill.setProperty(name, value)
        self.pump()

    def items(self, name, visible_only=True):
        """Items by objectName, found through the visual tree (delegates included)."""
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return sorted(found, key=lambda it: it.mapToScene(QtCore.QPointF(0, 0)).y())

    def item(self, name):
        found = self.items(name, visible_only=False)
        return found[0] if found else None

    def text(self, name="line"):
        return self.item(name).property("text")

    def shown(self, name):
        it = self.item(name)
        return it is not None and it.isVisible()

    @property
    def mode(self):
        return self.pill.property("mode")

    @property
    def sent(self):
        return [dict(m) for m in self.win.property("sent").toVariant()] if hasattr(
            self.win.property("sent"), "toVariant") else [dict(m) for m in self.win.property("sent")]

    def welcomed(self):
        return [m for m in self.sent if m["type"] == "welcomed"]

    def welcome(self, text=GREETING, **over):
        self.send(type="welcome", id=over.pop("id", 1), text=text, **over)

    def hover(self, it):
        centre = it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()
        QtTest.QTest.mouseMove(self.win, centre)
        self.pump(0.2)

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
    yield b
    b.win.close()
    b.engine.deleteLater()
    b.pump()


def test_a_welcome_takes_the_line_and_tells_agentd_it_was_shown(bar):
    bar.welcome(id=7)
    assert bar.mode == "welcome" and bar.text() == GREETING and bar.shown("line")
    assert bar.prop("welcomeId") == 7 and bar.prop("touchedAt") == 0
    assert bar.welcomed() == [{"type": "welcomed", "id": 7}]
    assert bar.win.property("shownCount") == 1
    # A plain line: no counter, no command, no edge, no buttons, nothing that holds it.
    assert not bar.shown("counter") and not bar.shown("command")
    assert not bar.shown("undoButton") and not bar.shown("detailsButton")
    assert not bar.prop("sticky") and bar.item("statusLine").property("edge").name() == "#000000"
    bar.snap("welcome-1")


def test_the_first_hello_stays_longer_than_a_later_one(bar):
    bar.welcome(first=True)
    assert bar.prop("fadeAfter") == 15000
    bar.call("dismiss")
    bar.welcome()
    assert bar.prop("fadeAfter") == 8000


def test_welcome_waits_for_a_touch_then_fades_and_a_prompt_replaces_it(bar):
    bar.welcome()
    bar.set("fadeAfter", 200)
    bar.pump(0.6)
    assert bar.mode == "welcome"      # nobody touched anything yet
    bar.call("touched")
    bar.pump(0.8)
    assert bar.mode == "idle" and bar.text() == ""
    bar.welcome(text="Welcome, Daniel.", id=2)
    bar.call("submit", "hello")
    assert bar.text() == "On it"


def test_welcome_done_follows_the_rule_for_any_clock(bar):
    bar.welcome()
    t0 = bar.prop("lineAt")      # the clock is passed in, so nothing here waits
    bar.set("fadeAfter", 8000)
    bar.set("welcomeMax", 30000)
    done = lambda now: bar.call("welcomeDone", now)
    assert done(t0 + 29999) is False and done(t0 + 30001) is True           # untouched: the cap
    bar.set("touchedAt", t0 + 5000)
    assert done(t0 + 5000 + 8000) is False and done(t0 + 5000 + 8001) is True   # touched: fadeAfter later
    assert done(t0 + 30001) is True                                         # and never past the cap
    bar.set("hovers", 1)
    assert done(t0 + 90000) is False                                        # hover holds both limits
    bar.set("hovers", 0)
    bar.call("dismiss")
    assert done(t0 + 90000) is False                                        # only a welcome is ever done


def test_touched_counts_once_and_only_for_a_welcome(bar):
    bar.call("touched")
    assert bar.prop("touchedAt") == 0          # no welcome: nothing to touch
    bar.welcome()
    bar.call("touched")
    first = bar.prop("touchedAt")
    assert first > 0
    bar.pump(0.1)
    bar.call("touched")
    assert bar.prop("touchedAt") == first      # the first touch is the one that counts


def test_an_untouched_welcome_is_gone_after_the_cap(bar):
    bar.welcome()
    bar.set("welcomeMax", 300)
    bar.pump(0.9)
    assert bar.mode == "idle"


def test_hover_holds_a_welcome_and_counts_as_the_first_touch(bar):
    bar.welcome()
    bar.set("welcomeMax", 300)
    bar.set("fadeAfter", 200)
    bar.hover(bar.item("statusLine"))
    assert bar.prop("hovers") == 1 and bar.prop("touchedAt") > 0
    bar.pump(0.9)
    assert bar.mode == "welcome"               # past both limits, but the pointer is on it
    QtTest.QTest.mouseMove(bar.win, QtCore.QPoint(5, 5))
    bar.pump(0.8)
    assert bar.mode == "idle"


@pytest.mark.parametrize("why", ["busy", "working", "optimistic", "closing", "local", "flash", "card", "fullscreen"])
def test_a_welcome_is_dropped_not_queued_when_anything_holds_the_line(bar, why):
    if why == "busy":
        bar.send(type="status", busy=True, provider="claude", queue=[])
    elif why == "working":
        bar.call("submit", "install ffmpeg")
        bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
    elif why == "optimistic":
        bar.call("submit", "install ffmpeg")
    elif why == "closing":
        bar.call("submit", "install ffmpeg")
        bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
        bar.send(kind="turn_end", turn=1, seconds=2, changed=True, summary="Installed ffmpeg.")
    elif why == "local":
        bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    elif why == "flash":
        bar.call("submit", "install ffmpeg")
        bar.send(kind="turn_start", turn=1, prompt="install ffmpeg")
        bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
        assert bar.prop("flash") != ""
    elif why == "card":
        bar.send(type="persona_ask", line="What should I call you?", name="", voice="merry", current=False,
                 voices=[{"id": "merry", "name": "Merry", "card": "Welcome{n}."}])
    elif why == "fullscreen":
        bar.set("fullscreen", True)
    mode, line = bar.mode, bar.prop("line")
    bar.welcome()
    assert bar.mode == mode and bar.prop("line") == line and bar.welcomed() == []
    assert bar.win.property("shownCount") == 0
    # Dropped, not delayed: when the line frees up nothing appears by itself.
    bar.call("dismiss")
    bar.set("fullscreen", False)
    bar.call("personaSkip")
    bar.send(type="status", busy=False, provider="claude", queue=[])
    bar.pump(0.3)
    assert bar.mode in ("idle", "working") and bar.welcomed() == []
    assert bar.mode != "welcome"


def test_a_welcome_with_no_words_is_dropped(bar):
    bar.welcome(text="   ")
    assert bar.mode == "idle" and bar.welcomed() == []


@pytest.mark.parametrize("how", ["submit", "turn_start", "error", "local", "dismiss", "lost", "typed", "offline_submit"])
def test_a_welcome_gives_way_to_what_follows(bar, how):
    bar.welcome()
    assert bar.mode == "welcome"
    if how == "submit":
        assert bar.call("submit", "hello") is True
        assert bar.mode == "working" and bar.text() == "On it"
    elif how == "turn_start":
        bar.send(kind="turn_start", turn=1, prompt="hello")
        assert bar.mode == "working" and bar.text() == "On it"
    elif how == "error":
        bar.send(kind="error", turn=None, text="claude is not installed yet")
        assert bar.mode == "local" and bar.text() == "claude is not installed yet"
    elif how == "local":
        bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
        assert bar.mode == "local" and bar.text() == "Opened the browser."
    elif how == "dismiss":
        bar.call("dismiss")
        assert bar.mode == "idle"
    elif how == "lost":
        bar.call("lost")
        assert bar.mode == "idle" and bar.text() == ""
    elif how == "typed":
        bar.call("dismissWelcome")   # the shell calls it on typing, and on Enter with nothing typed
        assert bar.mode == "idle"
    elif how == "offline_submit":
        bar.call("lost")
        bar.welcome(id=2)            # agentd is gone: it cannot have sent this
        assert bar.call("submit", "hello") is False
        assert bar.text() == "Not connected to the agent yet."


def test_a_welcome_is_replaced_by_a_prompt_even_while_a_turn_is_queued(bar):
    bar.welcome()
    bar.send(type="status", busy=True, provider="claude", queue=[])   # a turn began elsewhere
    assert bar.mode == "welcome"    # status alone does not clear it
    assert bar.call("submit", "then open the browser") is True
    assert bar.mode == "idle"       # the queued prompt waits as a chip; the greeting is gone


def test_dismiss_welcome_ends_a_welcome_and_nothing_else(bar):
    bar.call("submit", "what time is it")
    bar.send(kind="turn_start", turn=1, prompt="what time is it")
    bar.send(kind="turn_end", turn=1, seconds=2, changed=False, summary="It is noon.")
    assert bar.mode == "closing"
    bar.call("dismissWelcome")
    assert bar.mode == "closing" and bar.text() == "It is noon."
    bar.pill.setProperty("flashAt", time.time() * 1000)   # first: a flash with no time is cleared at once
    bar.set("flash", "Not connected to the agent yet.")
    bar.call("dismissWelcome")
    assert bar.prop("flash") != ""


def test_a_line_that_follows_a_welcome_has_its_own_clock(bar):
    bar.welcome(first=True)
    bar.send(kind="local", action="panel", phase="done", ok=True, text="Opened the browser.")
    assert bar.prop("fadeAfter") == 5000 and bar.prop("touchedAt") == 0
    bar.call("touched")                      # no welcome any more: nothing to touch
    assert bar.prop("touchedAt") == 0
    bar.set("fadeAfter", 200)
    bar.pump(0.8)
    assert bar.mode == "idle"


def test_the_bar_says_who_it_is_once_per_connection(bar):
    assert bar.sent == [{"type": "bar", "idle": False}]       # the fixture's first status
    bar.send(type="status", busy=False, provider="claude", queue=[])
    bar.send(type="status", busy=True, provider="claude", queue=[])
    assert [m["type"] for m in bar.sent] == ["bar"]           # later statuses are broadcasts
    bar.call("lost")
    assert [m["type"] for m in bar.sent] == ["bar"]           # nothing while it is down
    bar.send(type="status", busy=False, provider="claude", queue=[])
    assert [m["type"] for m in bar.sent] == ["bar", "bar"]    # the new socket says it again
    bar.send(type="entries", entries=[])
    assert [m["type"] for m in bar.sent] == ["bar", "bar"]


def test_presence_goes_to_agentd_and_rides_the_hello_after_a_reconnect(bar):
    bar.call("presence", True)
    assert bar.sent[-1] == {"type": "presence", "idle": True} and bar.prop("idle") is True
    bar.call("presence", False)
    assert bar.sent[-1] == {"type": "presence", "idle": False}
    bar.call("presence", True)
    bar.call("lost")
    bar.send(type="status", busy=False, provider="claude", queue=[])
    assert bar.sent[-1] == {"type": "bar", "idle": True}      # agentd learns we are away, not only that we exist


def test_the_hello_does_not_disturb_the_line(bar):
    bar.call("lost")
    bar.send(type="status", busy=True, provider="claude", queue=[], turn=4)   # back in the middle of a turn
    assert bar.mode == "working" and bar.sent[-1] == {"type": "bar", "idle": False}


def test_a_welcome_shows_on_its_own_screen_only_and_fades_from_there(bar):
    bar.win.setProperty("screens", 2)
    bar.pump(0.2)
    lines = bar.items("statusLine", visible_only=False)
    assert len(lines) == 2
    bar.welcome()
    bar.pump(0.3)
    visible = [ln for ln in lines if ln.isVisible()]
    assert len(visible) == 1 and visible[0].property("here") is True
    assert bar.item("statusLine") is not None and bar.win.property("shownCount") == 1
    # Another screen's line is a plain row of the same state: it only stays out of sight.
    bar.set("fadeAfter", 200)
    bar.call("touched")
    bar.pump(0.8)
    assert bar.mode == "idle"


def test_a_line_that_is_not_a_welcome_shows_on_every_screen(bar):
    bar.win.setProperty("screens", 2)
    bar.win.setProperty("hereIndex", 1)
    bar.pump(0.2)
    bar.call("submit", "install ffmpeg")
    bar.pump(0.3)
    assert len([ln for ln in bar.items("statusLine", visible_only=False) if ln.isVisible()]) == 2


def test_a_welcome_loads_and_fades_without_qml_warnings(bar):
    bar.welcome(first=True)
    bar.call("touched")
    bar.set("fadeAfter", 100)
    bar.pump(0.6)
    bar.welcome(id=2)
    bar.call("submit", "hello")
    bar.send(kind="turn_start", turn=1, prompt="hello")
    bar.send(kind="turn_end", turn=1, seconds=1, changed=False, summary="Did it.")
    bar.call("lost")
    bar.welcome(id=3)
    assert bar.warnings == []


# -- shell.qml cannot run without Quickshell: these read it instead --

def _members(path):
    """Every function, property and signal a QML file declares."""
    src = (SHELL / path).read_text()
    return (set(re.findall(r"\bfunction\s+(\w+)\s*\(", src)) | set(re.findall(r"\bsignal\s+(\w+)", src))
            | set(re.findall(r"\bproperty\s+(?:\w+\s+)+?(\w+)\s*:", src)) | set(re.findall(r"\bproperty\s+\w+\s+(\w+)\s*$", src, re.MULTILINE)))


def _block(src, head):
    """The body of the `head {` block, by indentation."""
    start = src.index(head)
    indent = re.search(r"^( *)\S", src[src.rindex("\n", 0, start) + 1:], re.MULTILINE).group(1)
    end = re.search(rf"^{indent}\}}", src[start:], re.MULTILINE)
    return src[start:start + end.end()]


def test_the_bar_and_its_parts_import_only_plain_qtquick():
    for name in ("PillState.qml", "StatusLine.qml", "PersonaCard.qml"):
        imports = re.findall(r"^import\s+(\S+)", (SHELL / name).read_text(), re.MULTILINE)
        assert imports and all(i.startswith("QtQuick") for i in imports), (name, imports)


def test_shell_qml_only_reaches_for_what_pillstate_and_the_line_declare():
    shell = (SHELL / "shell.qml").read_text()
    pill = _members("PillState.qml")
    used = set(re.findall(r"\bpillState\.(\w+)", shell))
    assert {"presence", "touched", "dismissWelcome", "personaAsk", "mode", "touchedAt"} <= used
    assert used <= pill, used - pill
    # Handlers on the PillState block and on Connections to it name real signals and properties.
    signals = set(re.findall(r"\bsignal\s+(\w+)", (SHELL / "PillState.qml").read_text()))
    handlers = set(re.findall(r"\bon([A-Z]\w*)\s*:", _block(shell, "PillState {")))
    handlers |= set(re.findall(r"function\s+on([A-Z]\w*)\s*\(", shell))
    for h in handlers:
        name = h[0].lower() + h[1:]
        assert name in signals or (name.endswith("Changed") and name[:-7] in pill), h
    assert {"Asked", "WelcomeShown"} <= handlers
    assert _members("StatusLine.qml") >= {"here"}


def test_shell_qml_feeds_presence_and_touch_from_idle_monitors_and_binds_fullscreen():
    shell = (SHELL / "shell.qml").read_text()
    away, touch = re.findall(r"IdleMonitor \{(.*?)\n    \}", shell, re.DOTALL)
    assert 'Quickshell.env("BOMBADIL_IDLE_SECONDS")' in away and "|| 300" in away
    assert "pillState.presence(isIdle)" in away
    assert "timeout: 1" in touch and "respectInhibitors: false" in touch
    assert 'pillState.mode === "welcome" && pillState.touchedAt === 0' in touch
    assert "pillState.touched()" in touch
    assert re.search(r"fullscreen:.*activeWorkspace.*hasFullscreen", _block(shell, "PillState {"))
    # A welcome is dismissed by typing and by Enter on nothing; Esc already calls dismiss().
    assert len(re.findall(r"pillState\.dismissWelcome\(\)", shell)) == 2
    assert "here: win.welcomeHere" in shell and "root.welcomeOn = root.focusedScreen()" in shell

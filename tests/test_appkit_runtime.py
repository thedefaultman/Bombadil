"""The app runtime: hot reload, error handling, status, saved size, and window placement.

Qt runs in a child process per test (offscreen): a QGuiApplication and the `App`
singleton exist once per process, and a test must not leave either behind.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from bombadil import apps, hypr
from bombadil.appkit import placement, runtime
from bombadil.appkit.context import AppContext

ROOT = Path(__file__).resolve().parents[1]

ITEM_QML = """\
import QtQuick
import Bombadil

Item {
    width: 420; height: 300
    property string title: "Demo " + App.reloads
    Text {
        objectName: "label"
        color: Theme.fg
        text: "hello v1 " + (typeof backend !== "undefined" && backend ? backend.greeting : "")
    }
    Component.onCompleted: console.log("loaded")
}
"""

BACKEND = """\
from PySide6.QtCore import Property, QObject


class Backend(QObject):
    @Property(str, constant=True)
    def greeting(self):
        return "from py1"
"""

# Runs in the child: drives a runtime Host with a local event loop, prints results as JSON.
PRELUDE = """\
import json, os, sys, time
from pathlib import Path
from PySide6.QtCore import QEventLoop, QMetaObject, QTimer
from PySide6.QtQuick import QQuickItem
from bombadil.appkit import context, engine, runtime
from bombadil.appkit.native.files import from_js

name = sys.argv[1]
d = Path(os.environ["BOMBADIL_APPS"]) / name
ctx = context.for_app(name)
qt = engine.make_app(ctx)
host = runtime.Host(ctx)
out = {}

def pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

def wait_for(pred, ms=4000):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        if pred():
            return True
        pump(20)
    return False

def write(rel, text):
    p = d / rel
    tmp = p.with_name("." + p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)   # the way create_app writes: a new inode every time

def label():
    item = host.root.findChild(QQuickItem, "label")
    return item.property("text") if item is not None else None

def status():
    return json.loads(ctx.status_path.read_text())

reloads = lambda: host.app.property("reloads")
"""


def drive(name: str, body: str) -> dict:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
           "PYTHONPATH": str(ROOT / "src")}
    script = PRELUDE + textwrap.dedent(body) + "\nhost.close()\nprint('RESULT ' + json.dumps(out))\n"
    r = subprocess.run([sys.executable, "-c", script, name], capture_output=True, text=True, env=env, timeout=90)
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"driver failed (exit {r.returncode}):\n{r.stdout}\n{r.stderr}"
    return json.loads(lines[-1].removeprefix("RESULT "))


def make_app(name: str, files: dict[str, str]) -> Path:
    d = apps.app_dir(name)
    d.mkdir(parents=True)
    for rel, text in files.items():
        (d / rel).write_text(text)
    return d


def test_hot_reload_keeps_the_window_and_the_last_good_ui(home):
    pytest.importorskip("PySide6")
    make_app("demo", {"main.qml": ITEM_QML, "app.py": BACKEND, "app.toml": 'title = "Demo"\n'})
    out = drive("demo", """
        out["start"] = [host.start(), label(), reloads(), host.window.title(), host.window.width(), host.window.height()]
        win = host.window
        src = (d / "main.qml").read_text()

        write("main.qml", src.replace("hello v1", "hello v2"))
        out["v2"] = [wait_for(lambda: reloads() == 1), label(), host.window is win, host.window.title()]

        write("main.qml", src.replace("hello v1", "hello v3").replace('objectName: "label"', 'objectName: "label"; bogus: 1'))
        out["broken"] = [wait_for(lambda: host.app.property("lastError") != ""), label(), host.app.property("lastError")]
        out["broken_status"] = status()

        write("app.py", (d / "app.py").read_text().replace("from py1", "from py2"))
        write("main.qml", src.replace("hello v1", "hello v4"))
        out["fixed"] = [wait_for(lambda: reloads() == 2 and "py2" in (label() or "")), label(),
                        host.app.property("lastError")]
        out["fixed_status"] = status()

        (d / "data").mkdir()
        (d / "data" / "state.qml").write_text("not QML, and not watched")
        pump(500)
        out["after_data_write"] = reloads()

        write("main.qml", src.replace("width: 420; height: 300", "width: 500; height: 320"))
        out["resized"] = [wait_for(lambda: host.window.width() == 500), host.window.width(), host.window.height()]
        pump(800)
        out["window_json"] = json.loads((ctx.state_dir / "demo.window.json").read_text())
    """)
    assert out["start"] == [True, "hello v1 from py1", 0, "Demo 0", 420, 300]
    assert out["v2"] == [True, "hello v2 from py1", True, "Demo 1"]
    ok, text, error = out["broken"]
    assert ok and text == "hello v2 from py1"        # the old UI stays up
    assert error.startswith("main.qml:") and "bogus" in error
    st = out["broken_status"]
    assert st["ok"] is False and st["loaded"] is False and st["showing"] == "previous"
    assert st["errors"][0] == error and st["reloads"] == 1
    assert out["fixed"] == [True, "hello v4 from py2", ""]
    st = out["fixed_status"]
    assert st["ok"] and st["loaded"] and st["showing"] == "current" and st["errors"] == []
    assert st["console"] == ["loaded"] and st["reloads"] == 2 and st["size"] == [420, 300]
    assert set(st) >= {"ok", "loaded", "errors", "warnings", "console", "reloads", "at"}
    assert out["after_data_write"] == 2
    assert out["resized"] == [True, 500, 320]
    assert out["window_json"] == {"width": 500, "height": 320, "declared": [500, 320]}


def test_an_app_that_never_loaded_shows_its_errors_until_fixed(home):
    pytest.importorskip("PySide6")
    make_app("broken", {"main.qml": ITEM_QML.replace("width: 420", "nonsense: 1; width: 420"),
                        "app.py": "def broken(:\n"})
    out = drive("broken", """
        out["start"] = [host.start(), host.error_view is not None, host.window.isVisible(), host.collector.errors]
        out["start_status"] = status()
        write("app.py", "from PySide6.QtCore import QObject\\nclass Backend(QObject):\\n"
                        "    def __init__(self):\\n        super().__init__()\\n        raise ValueError('nope')\\n")
        out["init_error"] = [wait_for(lambda: host.attempts == 2), host.collector.errors]
        (d / "app.py").unlink()
        write("main.qml", (d / "main.qml").read_text().replace("nonsense: 1; ", "required property int count; "))
        out["required"] = [wait_for(lambda: "Required property" in "".join(host.collector.errors)),
                           host.loaded, host.error_view is not None]
        write("main.qml", (d / "main.qml").read_text().replace("required property int count; ", ""))
        out["fixed"] = [wait_for(lambda: host.loaded), host.error_view is None, label(), reloads(),
                        host.window.width(), host.window.height()]
    """)
    loaded, view, visible, errors = out["start"]
    assert not loaded and view and visible
    assert errors == ["app.py:1: SyntaxError: invalid syntax"]
    assert out["start_status"]["showing"] == "errors"
    assert out["init_error"] == [True, ["app.py:5: ValueError: nope"]]
    assert out["required"] == [True, False, True]      # created, then refused: still the error list
    # The first good load takes the size the app asks for.
    assert out["fixed"] == [True, True, "hello v1 ", 1, 420, 300]


def test_a_window_root_is_recreated_on_reload(home):
    pytest.importorskip("PySide6")
    make_app("legacy", {"main.qml": textwrap.dedent("""\
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            visible: true
            width: 300; height: 200
            title: "Legacy v1"
        }
        """)})
    out = drive("legacy", """
        out["start"] = host.start()
        first = host.current_window()
        out["first"] = [first.title(), first.width(), first.isVisible(), host.window is None]
        write("main.qml", (d / "main.qml").read_text().replace("v1", "v2"))
        out["reloaded"] = wait_for(lambda: reloads() == 1)
        w = host.current_window()
        out["second"] = [w.title(), w.width(), w.isVisible(), w is not first]
    """)
    assert out["start"] and out["reloaded"]
    assert out["first"] == ["Legacy v1", 300, True, True]
    assert out["second"] == ["Legacy v2", 300, True, True]


def test_check_renders_through_the_runtime(home, tmp_path):
    pytest.importorskip("PySide6")
    make_app("demo", {"main.qml": ITEM_QML, "app.py": BACKEND})
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    png = tmp_path / "shot.png"
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), "check", "demo", "--screenshot", str(png)],
                       capture_output=True, text=True, env=env, timeout=90)
    result = json.loads(r.stdout)
    assert r.returncode == 0 and result["ok"] and result["size"] == [420, 300]
    assert result["console"] == ["loaded"] and png.stat().st_size > 0
    assert list(result) == ["app", "ok", "loaded", "errors", "warnings", "console", "screenshot", "size", "seconds"]
    # Read-only: a check leaves no status, size or data behind.
    assert not (home / "state" / "apps" / "demo.status.json").exists()
    assert not (home / "state" / "apps" / "demo.window.json").exists()

    (apps.app_dir("demo") / "main.qml").write_text(ITEM_QML.replace("Text {", "Text { x: nope;"))
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), "check", "demo"],
                       capture_output=True, text=True, env=env, timeout=90)
    result = json.loads(r.stdout)
    assert r.returncode == 1 and not result["ok"] and result["loaded"]
    assert any(e.startswith("main.qml:7") and "nope" in e for e in result["errors"]), result


def test_a_broken_app_py_is_retried_on_every_reload_until_fixed(home):
    pytest.importorskip("PySide6")
    make_app("pyapp", {"main.qml": ITEM_QML, "app.py": "def broken(:\n"})
    out = drive("pyapp", """
        out["start"] = [host.start(), host.collector.errors]
        src = (d / "main.qml").read_text()
        # Only QML changes, but app.py is still broken: it is tried again, its error stays.
        write("main.qml", src.replace("hello v1", "hello v2"))
        out["qml_only"] = [wait_for(lambda: host.attempts == 2), host.loaded, host.collector.errors, status()["errors"]]
        write("app.py", BACKEND_SRC)
        out["fixed"] = [wait_for(lambda: host.loaded), label()]
        write("app.py", "raise ValueError('bad edit')\\n")
        out["broken_again"] = [wait_for(lambda: host.attempts == 4), host.loaded, host.collector.errors]
        write("main.qml", src.replace("hello v1", "hello v3"))
        out["then_qml"] = [wait_for(lambda: host.attempts == 5), host.loaded, host.collector.errors, label(),
                           status()["ok"]]
    """.replace("BACKEND_SRC", repr(BACKEND)))
    syntax = ["app.py:1: SyntaxError: invalid syntax"]
    assert out["start"] == [False, syntax]
    assert out["qml_only"] == [True, False, syntax, syntax]
    assert out["fixed"] == [True, "hello v2 from py1"]
    assert out["broken_again"] == [True, False, ["app.py:1: ValueError: bad edit"]]
    # The old UI and its old backend stay up, and the status does not claim otherwise.
    assert out["then_qml"] == [True, False, ["app.py:1: ValueError: bad edit"], "hello v2 from py1", False]


def test_the_first_size_is_read_after_bindings_run(home):
    pytest.importorskip("PySide6")
    make_app("sized", {"main.qml": "import QtQuick\nimport Bombadil\n"
                                   "AppWindow { width: 400 + 20; height: Theme.pad * 20 }\n"})
    out = drive("sized", """
        out["start"] = [host.start(), host.window.width(), host.window.height()]
        write("main.qml", (d / "main.qml").read_text().replace("400 + 20", "400 + 60"))
        out["edited"] = [wait_for(lambda: host.window.width() == 460), host.window.width(), host.window.height()]
    """)
    assert out["start"] == [True, 420, 320]
    assert out["edited"] == [True, 460, 320]


def test_a_window_too_big_for_the_screen_is_shrunk_and_that_size_is_not_saved(home):
    pytest.importorskip("PySide6")
    make_app("big", {"main.qml": "import QtQuick\nimport Bombadil\nAppWindow { width: 1120; height: 880 }\n"})
    out = drive("big", """
        runtime.placement.usable_area = lambda h=None: (1232, 716)   # a 1280x800 laptop with a bar
        out["start"] = [host.start(), host.window.width(), host.window.height()]
        pump(900)
        out["saved"] = (ctx.state_dir / "big.window.json").exists()
        write("main.qml", (d / "main.qml").read_text().replace("width: 1120; height: 880", "width: 1000; height: 900"))
        out["edited"] = [wait_for(lambda: host.window.width() == 1000), host.window.width(), host.window.height()]
        write("main.qml", (d / "main.qml").read_text().replace("width: 1000; height: 900", "width: 600; height: 500"))
        out["small"] = [wait_for(lambda: host.window.width() == 600), host.window.width(), host.window.height()]
    """)
    assert out["start"] == [True, 1120, 716] and out["saved"] is False
    assert out["edited"] == [True, 1000, 716]
    assert out["small"] == [True, 600, 500]      # a size that fits is kept as declared


def test_store_keeps_saved_values_across_a_rename_and_a_type_change(home):
    pytest.importorskip("PySide6")
    make_app("stored", {"main.qml": textwrap.dedent("""\
        import QtQuick
        import Bombadil
        AppWindow {
            property var store: st
            Store { id: st; property var entries: []; property string filter: ""; property var tags: [] }
        }
        """)})
    out = drive("stored", """
        st = lambda: host.root.property("store")
        saved = lambda: json.loads((d / "data" / "state.json").read_text())
        out["start"] = host.start()
        st().setProperty("entries", [{"site": "github"}])
        st().setProperty("tags", ["a", "b"])
        src = (d / "main.qml").read_text()
        # One edit renames entries to items and makes tags a string.
        write("main.qml", src.replace("property var entries: []", "property var items: []")
                             .replace("property var tags: []", 'property string tags: ""'))
        out["edited"] = [wait_for(lambda: reloads() == 1), host.collector.warnings]
        st().setProperty("filter", "git")
        pump(500)
        out["file"] = saved()
        write("main.qml", src)
        out["undone"] = [wait_for(lambda: reloads() == 2), from_js(st().property("entries")),
                         from_js(st().property("tags")), st().property("filter"), host.collector.warnings]
        QMetaObject.invokeMethod(st(), "reset")
        out["reset"] = [(d / "data" / "state.json").exists(), from_js(st().property("entries"))]
        st().setProperty("filter", "x")
        pump(500)
        out["after_reset"] = saved()
    """)
    assert out["start"]
    ok, warnings = out["edited"]
    assert ok and warnings == ["Store: the saved tags does not fit this property; it stays in state.json "
                               "until the app sets tags"]
    assert out["file"] == {"entries": [{"site": "github"}], "filter": "git", "tags": ["a", "b"], "items": []}
    assert out["undone"] == [True, [{"site": "github"}], ["a", "b"], "git", []]
    assert out["reset"] == [False, []]
    assert out["after_reset"] == {"entries": [], "filter": "x", "tags": []}   # reset dropped `items`


def test_store_sets_a_broken_file_aside_and_takes_a_nan_default(home):
    pytest.importorskip("PySide6")
    d = make_app("nan", {"main.qml": textwrap.dedent("""\
        import QtQuick
        import Bombadil
        AppWindow {
            property var store: st
            Store { id: st; property real avg: NaN; property var entries: [] }
        }
        """)})
    (d / "data").mkdir()
    (d / "data" / "state.json").write_text('{"entries": [1, 2')
    out = drive("nan", """
        import math
        st = lambda: host.root.property("store")
        out["start"] = [host.start(), st().property("loaded"), math.isnan(st().property("avg")), host.collector.warnings]
        out["bad"] = (d / "data" / "state.json.bad").read_text()
        st().setProperty("entries", [3])
        pump(500)
        out["file"] = json.loads((d / "data" / "state.json").read_text())
        write("main.qml", (d / "main.qml").read_text() + "\\n")
        out["reloaded"] = [wait_for(lambda: reloads() == 1), math.isnan(st().property("avg")),
                           from_js(st().property("entries")), host.collector.warnings]
    """)
    assert out["start"] == [True, True, True, ["Store: state.json is not valid JSON; it was renamed to "
                                               "state.json.bad, so the app starts from the defaults"]]
    assert out["bad"] == '{"entries": [1, 2'
    assert out["file"] == {"avg": None, "entries": [3]}
    assert out["reloaded"] == [True, True, [3], []]


def test_check_is_stopped_when_the_app_never_settles_and_its_commands_die(home):
    pytest.importorskip("PySide6")
    make_app("busy", {"main.qml": textwrap.dedent("""\
        import QtQuick
        import Bombadil
        AppWindow {
            Command { command: "sleep 97.31 | cat"; running: true }
            Timer { interval: 100; running: true; onTriggered: { console.log("spinning"); while (true) {} } }
        }
        """)})
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    script = "import sys\nfrom bombadil.appkit import check\ncheck.main(sys.argv[1], None, 500, None, settle=2)\n"
    r = subprocess.run([sys.executable, "-c", script, str(apps.app_dir("busy"))], capture_output=True, text=True,
                       env=env, timeout=60)
    result = json.loads(r.stdout)
    assert r.returncode == -signal.SIGKILL and result["ok"] is False and result["loaded"] is True
    assert "did not settle within 2.5 s" in result["errors"][-1] and result["console"] == ["spinning"]
    deadline = time.monotonic() + 5
    while subprocess.run(["pgrep", "-f", "sleep 97.31"], capture_output=True).returncode == 0:
        assert time.monotonic() < deadline, "the Command's program outlived the check"
        time.sleep(0.1)


def test_check_keeps_app_py_commands_and_qt_storage_off_the_users_files(home):
    pytest.importorskip("PySide6")
    make_app("storage", {
        "main.qml": textwrap.dedent("""\
            import QtQuick
            import QtCore
            import Bombadil
            AppWindow {
                Settings { id: settings; property int n: 5 }
                Command { command: "echo command BOMBADIL_CHECK=$BOMBADIL_CHECK"; running: true
                          onFinished: console.log(output.trim()) }
                Component.onCompleted: {
                    settings.n = 7
                    settings.sync()
                    console.log("app.py " + backend.check)
                    console.log("data in " + App.dataDir)
                }
            }
            """),
        "app.py": textwrap.dedent("""\
            import os
            from PySide6.QtCore import Property, QObject


            class Backend(QObject):
                @Property(str, constant=True)
                def check(self):
                    return "BOMBADIL_CHECK=" + os.environ.get("BOMBADIL_CHECK", "")
            """)})
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "XDG_CONFIG_HOME": str(home / "config-home")}
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), "check", "storage"],
                       capture_output=True, text=True, env=env, timeout=90)
    result = json.loads(r.stdout)
    assert result["ok"], result
    assert sorted(result["console"]) == ["app.py BOMBADIL_CHECK=1", "command BOMBADIL_CHECK=1",
                                         f"data in {home}/Apps/storage/data"]   # console text keeps its paths
    assert not (home / "config-home").exists() and not (home / ".config").exists()


def test_a_launched_app_logs_to_its_log_and_a_stuck_one_is_killed_on_close(home):
    """Started by a launcher (stderr not a terminal), not through apps.run."""
    pytest.importorskip("PySide6")
    make_app("stuck", {"main.qml": ITEM_QML.replace(
        'Component.onCompleted: console.log("loaded")',
        'Timer { id: spin; interval: 300; onTriggered: { while (true) {} } }\n'
        '    Component.onCompleted: { console.log("loaded"); spin.start() }')})
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
           "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "bombadil-app"), "run", "stuck"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        status_file = home / "state" / "apps" / "stuck.status.json"
        deadline = time.monotonic() + 30
        while not status_file.exists():
            assert proc.poll() is None and time.monotonic() < deadline, proc.stderr.read()
            time.sleep(0.1)
        time.sleep(1.0)   # the Timer has fired: the app spins and never runs its SIGTERM handler
        log = apps.log_path("stuck").read_text()
        assert "bombadil-app run stuck" in log and "console: loaded" in log
        assert "window: Hyprland is not running; stuck opens as a plain window" in log
        assert "was killed" in placement.close("stuck", wait=1.0)
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == -signal.SIGKILL and out == b"" and err == b""
    finally:
        proc.kill()


def test_the_collector_shortens_where_a_message_points_but_not_what_the_app_logs(home):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QtMsgType

    from bombadil.appkit.check import Collector

    d = home / "Apps" / "demo"
    c = Collector(AppContext("demo", "Demo", d, d / "main.qml"))
    c(QtMsgType.QtDebugMsg, None, f"{d}/data")
    c(QtMsgType.QtDebugMsg, None, f"file://{d}/main.qml")
    c(QtMsgType.QtWarningMsg, None, f"file://{d}/main.qml:3:5: Unable to assign [undefined] to QString")
    c(QtMsgType.QtCriticalMsg, None, f"{d}/lib/Row.qml:7: TypeError: Cannot read property 'x' of null")
    c(QtMsgType.QtWarningMsg, None, f"saving to {d}/data/x.json")
    assert c.console == [f"{d}/data", f"file://{d}/main.qml"]
    assert c.errors == ["main.qml:3:5: Unable to assign [undefined] to QString",
                        "lib/Row.qml:7: TypeError: Cannot read property 'x' of null"]
    assert c.warnings == [f"saving to {d}/data/x.json"]


def test_saved_size_wins_unless_the_app_asks_for_a_new_one(home):
    ctx = AppContext("demo", "Demo", home / "Apps" / "demo", home / "Apps" / "demo" / "main.qml")
    host = runtime.Host.__new__(runtime.Host)   # the size rules need no Qt
    host.ctx, host.declared, host._room, host._fitted = ctx, None, None, None
    assert host._target_size((560, 680)) == (560, 680)
    runtime._write_json(ctx.state_dir / "demo.window.json", {"width": 900, "height": 700, "declared": [560, 680]})
    assert host._target_size((560, 680)) == (900, 700)
    assert host._target_size((800, 600)) == (800, 600)      # edited to ask for a new size
    assert host._target_size((10, 99999)) == (240, 2160)    # clamped


def test_status_merges_the_status_file_and_the_log(home, monkeypatch):
    monkeypatch.setattr(placement, "running", lambda: {"demo": [123]})
    make_app("demo", {"main.qml": ITEM_QML})
    st = runtime.status("demo")
    assert st["running"] and st["log"] == [] and "note" in st
    runtime._write_json(home / "state/apps/demo.status.json", {"ok": False, "errors": ["main.qml:3: x"]})
    apps.log_path("demo").write_text("".join(f"line {i}\n" for i in range(100)))
    st = runtime.status("demo")
    assert st["errors"] == ["main.qml:3: x"] and len(st["log"]) == 40 and st["log"][-1] == "line 99"
    assert "note" not in st
    with pytest.raises(ValueError):
        runtime.status("../etc")


# Placement: the Lua it sends and what it does without Hyprland.

class FakeHypr(hypr.Hyprland):
    def __init__(self, monitors=None, replies=None, available=True):
        self.sent = []
        self.monitors = monitors or [{"name": "DP-1", "focused": True, "specialWorkspace": {"name": ""}}]
        self.replies = list(replies or [])
        self._available = available

    @property
    def available(self):
        return self._available

    def request(self, command):
        self.sent.append(command)
        if command == "j/monitors":
            return json.dumps(self.monitors)
        return self.replies.pop(0) if self.replies else "ok"

    def clients(self):
        return []


def test_lua_strings_are_exact_and_escaped():
    assert placement.rule_lua("notes", 540, 660) == (
        'hl.window_rule({ name = "bombadil-app-notes", match = { class = "^(bombadil-app-notes)$" }, '
        'float = true, center = true, size = { 540, 660 }, workspace = "special:app-notes" })')
    assert placement.show_lua("notes") == 'hl.dsp.focus({ workspace = "special:app-notes" })'
    assert placement.toggle_lua("notes") == 'hl.dsp.workspace.toggle_special("app-notes")'
    assert placement.lua_str('a"b\\c\nd\x01') == '"a\\"b\\\\c\\nd\\001"'
    for bad in ("Notes", "../x", 'x") os.exit(', ""):
        with pytest.raises(ValueError):
            placement.rule_lua(bad, 1, 1)


def test_without_hyprland_everything_is_a_clear_no_op(home, monkeypatch):
    started = []
    monkeypatch.setattr(placement.apps, "run", lambda name: started.append(name))
    monkeypatch.setattr(placement, "running", lambda: {"notes": [1]})
    none = FakeHypr(available=False)
    assert "not running" in placement.prepare("notes", 540, 660, none)
    assert "not running" in placement.show("notes", none)
    assert "not running" in placement.hide("notes", none)
    assert placement.shown(none) == set()
    assert "not running" in placement.open_url("example.com", none)
    assert none.sent == [] and started == []
    # Real Hyprland module, no HYPRLAND_INSTANCE_SIGNATURE (the fixture removes it).
    assert "not running" in placement.prepare("notes", 540, 660)
    monkeypatch.setattr(placement, "running", lambda: {})
    assert placement.show("notes", none).startswith("started notes") and started == ["notes"]


def test_show_hide_and_prepare_send_the_right_requests(monkeypatch):
    monkeypatch.setattr(placement, "running", lambda: {"notes": [42]})
    h = FakeHypr()
    assert placement.prepare("notes", 540, 660, h) == "notes opens in its drawer at 540x660"
    assert h.sent == ["eval " + placement.rule_lua("notes", 540, 660)]

    h = FakeHypr()
    assert placement.show("notes", h) == "notes shown"
    assert h.sent == ['dispatch hl.dsp.focus({ workspace = "special:app-notes" })']

    h = FakeHypr()
    assert placement.hide("notes", h) == "notes is not on screen" and h.sent == ["j/monitors"]

    h = FakeHypr([{"name": "DP-1", "focused": True, "specialWorkspace": {"name": "special:app-notes"}}])
    assert placement.shown(h) == {"notes"}
    assert placement.hide("notes", h) == "notes hidden"
    assert h.sent[-1] == 'dispatch hl.dsp.workspace.toggle_special("app-notes")'

    # Shown on a monitor without focus: focus that monitor first, or the toggle would move it.
    h = FakeHypr([{"name": "DP-1", "focused": True, "specialWorkspace": {"name": ""}},
                  {"name": "HDMI-A-1", "focused": False, "specialWorkspace": {"name": "special:app-notes"}}])
    placement.hide("notes", h)
    assert h.sent[-2:] == ['dispatch hl.dsp.focus({ monitor = "HDMI-A-1" })',
                           'dispatch hl.dsp.workspace.toggle_special("app-notes")']

    h = FakeHypr([{"name": "DP-1", "focused": True, "specialWorkspace": {"name": "special:app-notes"}}])
    assert placement.toggle("notes", h) == "notes hidden"
    h = FakeHypr()
    assert placement.toggle("notes", h) == "notes shown"


def test_requests_retry_and_errors_are_reported(monkeypatch):
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    h = FakeHypr(replies=["", "ok"])
    assert placement.prepare("notes", 1, 2, h).startswith("notes opens")
    assert len(h.sent) == 2
    h = FakeHypr(replies=["error: bad rule"])
    assert "bad rule" in placement.prepare("notes", 1, 2, h)
    monkeypatch.setattr(placement, "running", lambda: {"notes": [42]})
    with pytest.raises(RuntimeError, match="window not found"):
        placement.show("notes", FakeHypr(replies=["hl.focus: window not found"]))


def test_prepare_says_what_went_wrong_instead_of_raising(monkeypatch):
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)

    class Hung(FakeHypr):
        def request(self, command):
            raise TimeoutError("timed out")
    said = placement.prepare("notes", 1, 2, Hung())
    assert said.startswith("could not add the window rule for notes, it opens as a plain window: Hyprland did not")
    assert said.endswith("timed out")
    assert "opens as a plain window" in placement.prepare("notes", 1, 2, FakeHypr(replies=["", "", ""]))


def test_a_hyprland_request_times_out(home, monkeypatch):
    runtime_dir = Path("/tmp") / f"bombadil-test-{os.getpid()}"   # short: socket paths are limited
    sock_dir = runtime_dir / "hypr" / "sig"
    sock_dir.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(sock_dir / ".socket.sock"))
        server.listen(1)     # accepts, never answers: a hung compositor
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
        monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig")
        raised = []

        def ask():
            try:
                hypr.Hyprland().request("j/monitors")
            except OSError as e:
                raised.append(e)
        asking = threading.Thread(target=ask, daemon=True)
        asking.start()
        asking.join(4)
        assert not asking.is_alive() and raised, "a request to a hung Hyprland never returned"
    finally:
        server.close()
        (sock_dir / ".socket.sock").unlink(missing_ok=True)
        for p in (sock_dir, sock_dir.parent, runtime_dir):
            p.rmdir()


def test_usable_area_is_the_focused_monitor_without_the_bar():
    laptop = {"name": "eDP-1", "focused": True, "width": 1280, "height": 800, "scale": 1.0,
              "transform": 0, "reserved": [0, 36, 0, 0]}
    other = {**laptop, "name": "DP-1", "focused": False, "width": 3840, "height": 2160}
    assert placement.usable_area(FakeHypr([other, laptop])) == (1280 - 48, 800 - 36 - 48)
    hidpi = {**laptop, "width": 2560, "height": 1600, "scale": 2.0}
    assert placement.usable_area(FakeHypr([hidpi])) == (1232, 716)
    assert placement.usable_area(FakeHypr([{**laptop, "transform": 1}])) == (800 - 48, 1280 - 36 - 48)
    assert placement.usable_area(FakeHypr([{"name": "?"}])) is None
    assert placement.usable_area(FakeHypr(available=False)) is None
    assert placement.fit(1120, 880, (1232, 716)) == (1120, 716)
    assert placement.fit(560, 680, (1232, 716)) == (560, 680)
    assert placement.fit(1120, 880, None) == (1120, 880)
    assert placement.fit(900, 900, (100, 100)) == placement.MIN_SIZE


def test_the_runtime_logs_where_its_window_goes(home, monkeypatch, capsys):
    ctx = AppContext("demo", "Demo", home / "Apps" / "demo", home / "Apps" / "demo" / "main.qml")
    host = runtime.Host.__new__(runtime.Host)
    host.ctx, host._room, host._fitted = ctx, None, None
    monkeypatch.setattr(placement, "usable_area", lambda h=None: (1232, 716))
    monkeypatch.setattr(placement, "prepare", lambda name, w, h: f"could not add the window rule for {name}: no")
    assert host._place((1120, 880)) == (1120, 716) and host._fitted == (1120, 716)
    assert "window: could not add the window rule for demo: no" in capsys.readouterr().err


def test_close_kills_an_app_that_does_not_quit(monkeypatch):
    sent = []
    monkeypatch.setattr(placement, "running", lambda: {} if (101, signal.SIGKILL) in sent else {"notes": [101]})
    monkeypatch.setattr(placement.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    assert placement.close("notes", wait=0.05) == ("notes did not quit within 0.05 s (stuck?) and was killed; "
                                                   "unsaved changes are lost")
    assert sent == [(101, signal.SIGTERM), (101, signal.SIGKILL)]


def test_running_parses_pgrep_and_close_sends_sigterm(monkeypatch):
    out = ("101 /usr/bin/python3 /usr/share/bombadil/bin/bombadil-app run notes\n"
           "102 python3 bin/bombadil-app run password-manager\n"
           f"{os.getpid()} python3 bin/bombadil-app run myself\n"
           "103 vim bombadil-app run notes.txt\n")
    monkeypatch.setattr(placement.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))
    assert placement.running() == {"notes": [101], "password-manager": [102]}
    assert placement.is_running("notes") and not placement.is_running("memory")

    killed = []
    states = iter([{"notes": [101]}, {"notes": [101]}, {}])
    monkeypatch.setattr(placement, "running", lambda: next(states))
    monkeypatch.setattr(placement.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(placement.time, "sleep", lambda s: None)
    assert placement.close("notes") == "notes closed"
    assert killed == [(101, placement.signal.SIGTERM)]
    monkeypatch.setattr(placement, "running", lambda: {})
    assert placement.close("notes") == "notes is not running"


def test_open_url_validates_and_opens_in_the_browser_panel(monkeypatch):
    launched, panels = [], []
    monkeypatch.setattr(placement.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(placement.subprocess, "Popen", lambda cmd, **k: launched.append(cmd))
    h = FakeHypr()
    h.panel = lambda name, show=True: panels.append(name)
    for bad in ("--renderer-cmd-prefix=sh", "javascript:alert(1)", ""):
        with pytest.raises(ValueError):
            placement.open_url(bad, h)
    assert placement.open_url("example.com/a b", h) == "opened https://example.com/a b in the browser panel"
    assert launched[-1] == [*hypr.PANELS["browser"], "https://example.com/a b"]
    # Chromium was not running: the new window lands in special:browser; show it.
    assert h.sent[-1] == 'dispatch hl.dsp.focus({ workspace = "special:browser" })' and panels == []
    h.clients = lambda: [{"class": "bombadil-browser", "workspace": {"name": "special:browser"}}]
    placement.open_url("https://example.org", h)
    assert panels == ["browser"]

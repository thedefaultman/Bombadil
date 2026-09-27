"""The app runtime: hot reload, error handling, status, saved size, and window placement.

Qt runs in a child process per test (offscreen): a QGuiApplication and the `App`
singleton exist once per process, and a test must not leave either behind.
"""

import json
import os
import subprocess
import sys
import textwrap
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
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtQuick import QQuickItem
from bombadil.appkit import context, engine, runtime

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


def test_saved_size_wins_unless_the_app_asks_for_a_new_one(home):
    ctx = AppContext("demo", "Demo", home / "Apps" / "demo", home / "Apps" / "demo" / "main.qml")
    host = runtime.Host.__new__(runtime.Host)   # the size rules need no Qt
    host.ctx, host.declared = ctx, None
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

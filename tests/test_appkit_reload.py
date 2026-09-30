"""The runtime across edits that swap app.py, remove the app folder, fill the disk or
flood the log. Qt runs in a child process per test, like in test_appkit_runtime."""

import contextlib
import errno
import os
import subprocess
import sys
import textwrap
import time

import pytest

from bombadil import apps
from bombadil.appkit import placement, runtime
from test_appkit_runtime import ROOT, drive, make_app

COUNT_QML = ('import QtQuick\nimport Bombadil\n'
             'AppWindow { Text { objectName: "label"; text: "count " + backend.count } }\n')
COUNT_PY = """\
from PySide6.QtCore import Property, QObject


class Backend(QObject):
    @Property(int, constant=True)
    def count(self):
        return 7
"""

NOSUPER_PY = COUNT_PY + "\n    def __init__(self):\n        self.n = 1\n"   # never calls super().__init__()


def test_a_new_backend_does_not_make_the_old_ui_report_errors(home):
    pytest.importorskip("PySide6")
    make_app("counter", {"main.qml": COUNT_QML, "app.py": COUNT_PY})
    out = drive("counter", """
        qml, py = (d / "main.qml").read_text(), (d / "app.py").read_text()
        def result():
            st = status()
            return [host.loaded, label(), host.app.property("lastError"), st["ok"], st["errors"]]
        out["start"] = [host.start(), label()]

        write("app.py", py.replace("return 7", "return 8"))
        wait_for(lambda: host.attempts == 2)
        out["py_only"] = result()

        # Renamed in both files, and the new main.qml does not compile.
        write("app.py", py.replace("count", "total").replace("return 7", "return 9"))
        write("main.qml", qml.replace('"count " + backend.count', '"total " + backend.total.toFixed(0); bogus: 1'))
        wait_for(lambda: host.attempts == 3)
        out["broken_qml"] = result()

        write("main.qml", qml.replace('"count " + backend.count', '"total " + backend.total.toFixed(0)'))
        wait_for(lambda: host.attempts == 4)
        out["qml_fixed"] = result()

        # The old UI throws on the new backend on its way out; the new UI is fine.
        write("app.py", py.replace("count", "size").replace("return 7", "return 5"))
        write("main.qml", qml.replace('"count " + backend.count', '"size " + backend.size'))
        wait_for(lambda: host.attempts == 5)
        out["renamed"] = result()

        (d / "app.py").unlink()
        write("main.qml", qml.replace('"count " + backend.count', '"no backend"'))
        wait_for(lambda: host.attempts == 6)
        out["removed"] = result()
    """)
    assert out["start"] == [True, "count 7"]
    assert out["py_only"] == [True, "count 8", "", True, []]
    loaded, text, error, ok, errors = out["broken_qml"]
    assert not loaded and not ok and text == "count 8"   # the old UI keeps its own backend
    assert error.startswith("main.qml:") and "bogus" in error and errors == [error]
    assert out["qml_fixed"] == [True, "total 9", "", True, []]
    assert out["renamed"] == [True, "size 5", "", True, []]
    assert out["removed"] == [True, "no backend", "", True, []]


def test_an_old_backend_the_old_ui_kept_is_not_counted(home):
    pytest.importorskip("PySide6")
    make_app("keeper", {"main.qml": 'import QtQuick\nimport Bombadil\nAppWindow {\n'
                                    '    property var keep: ({count: 0})\n'
                                    '    Component.onCompleted: keep = backend\n'
                                    '    Text { objectName: "label"; text: "count " + keep.count }\n}\n',
                        "app.py": COUNT_PY})
    out = drive("keeper", """
        out["start"] = [host.start(), label()]
        write("app.py", (d / "app.py").read_text().replace("return 7", "return 8"))
        wait_for(lambda: host.attempts == 2)
        st = status()
        out["edited"] = [host.loaded, label(), st["ok"], st["errors"]]
    """)
    assert out["start"] == [True, "count 7"]
    assert out["edited"] == [True, "count 8", True, []]


@pytest.mark.parametrize("how", ["rm", "mv"])
def test_hot_reload_survives_the_app_folder_being_removed_and_written_again(home, how):
    pytest.importorskip("PySide6")
    make_app("notes", {"main.qml": 'import QtQuick\nimport Bombadil\nAppWindow { Text { objectName: "label"; '
                                   'text: "v1" } }\n', "app.toml": 'title = "Notes"\n'})
    out = drive("notes", """
        import shutil
        from bombadil import apps
        qml = (d / "main.qml").read_text()
        out["start"] = [host.start(), label()]
        if HOW == "rm":
            shutil.rmtree(d)
        else:
            d.rename(d.with_name("notes-old"))
        wait_for(lambda: not host.loaded, 2000)
        out["gone"] = [host.loaded, host.app.property("lastError")]
        pump(300)
        apps.create("Notes", qml.replace('"v1"', '"v2"'))
        out["again"] = [wait_for(lambda: host.loaded and label() == "v2"), label(), host.app.property("lastError")]
        write("main.qml", qml.replace('"v1"', '"v3"'))
        out["edited"] = [wait_for(lambda: label() == "v3"), label()]
    """.replace("HOW", repr(how)))
    assert out["start"] == [True, "v1"]
    loaded, error = out["gone"]
    assert not loaded and "main.qml" in error
    assert out["again"] == [True, "v2", ""]
    assert out["edited"] == [True, "v3"]


def test_a_full_disk_does_not_stop_an_app(home):
    pytest.importorskip("PySide6")
    make_app("disks", {"main.qml": 'import QtQuick\nimport Bombadil\nAppWindow { width: 400; height: 300 }\n'})
    out = drive("disks", """
        import errno
        real = runtime.Path.write_text
        def full(self, *a, **k):
            if self.parent == ctx.state_dir:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(self, *a, **k)
        runtime.Path.write_text = full
        out["start"] = [host.start(), host.loaded]
        host.window.resize(500, 350)
        pump(900)                              # the size is saved (or not) after 600 ms
        out["left"] = sorted(p.name for p in ctx.state_dir.iterdir()) if ctx.state_dir.exists() else []
        host.close()
        out["closed"] = [host.root is None, host.window.isVisible()]
    """)
    assert out["start"] == [True, True]
    assert out["left"] == []                   # no status, no size, no temp files
    assert out["closed"] == [True, True]


def test_write_json_removes_its_temp_file_when_it_fails(home):
    target = home / "state" / "apps" / "demo.status.json"
    target.mkdir(parents=True)                 # os.replace onto a directory fails
    with pytest.raises(OSError):
        runtime._write_json(target, {"ok": True})
    assert sorted(p.name for p in target.parent.iterdir()) == ["demo.status.json"]


def test_log_lines_are_dropped_when_stderr_cannot_take_them(monkeypatch):
    class Full:
        def write(self, _text):
            raise OSError(errno.ENOSPC, "No space left on device")

        def flush(self):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(sys, "stderr", Full())
    runtime._log("window: somewhere")


def test_status_reads_only_the_end_of_a_big_log(home, monkeypatch):
    monkeypatch.setattr(placement, "running", lambda: {})
    make_app("chatty", {"main.qml": "import QtQuick\nItem {}\n"})
    log = apps.log_path("chatty")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        f.write("x" * 5_000_000 + "\n")
        f.writelines(f"line {i}\n" for i in range(100))
    read_text = runtime.Path.read_text

    def read_all(path, *a, **k):
        assert path != log, "status() read the whole log"
        return read_text(path, *a, **k)
    monkeypatch.setattr(runtime.Path, "read_text", read_all)
    assert runtime.status("chatty")["log"] == [f"line {i}" for i in range(60, 100)]
    # Fewer than 40 whole lines at the end: a line cut off by the window is not shown.
    with log.open("a") as f:
        f.write("y" * 70_000 + "\n" + "last\n")
    assert runtime.status("chatty")["log"] == ["last"]


def test_a_running_app_rotates_its_log(home):
    pytest.importorskip("PySide6")
    make_app("flood", {"main.qml": textwrap.dedent("""\
        import QtQuick
        import Bombadil
        AppWindow {
            Timer { interval: 50; repeat: true; running: true; onTriggered: console.log("x".repeat(20000)) }
        }
        """)})
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
           "PYTHONPATH": str(ROOT / "src")}
    log = apps.log_path("flood")
    proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "bombadil-app"), "run", "flood"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not log.with_suffix(".log.1").exists():
            assert proc.poll() is None and time.monotonic() < deadline, proc.stderr.read()
            time.sleep(0.1)
        time.sleep(0.3)
        assert log.with_suffix(".log.1").stat().st_size > 1_000_000
        assert 0 < log.stat().st_size < 1_000_000 and "console: " in log.read_text()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_an_app_whose_log_is_on_a_full_disk_still_runs(home):
    """Launched from the bar, stdout and stderr are the log: every echo, traceback and
    console.log fails with ENOSPC. The log is a link to /dev/full, which fails every write."""
    pytest.importorskip("PySide6")
    if not os.path.exists("/dev/full"):
        pytest.skip("no /dev/full")
    says = ('import QtQuick\nimport Bombadil\nAppWindow {\n'
            '    Component.onCompleted: console.log("hello V")\n'
            '    Text { objectName: "label"; text: "V" }\n}\n')
    make_app("says", {"main.qml": says.replace("V", "v1")})
    make_app("broken", {"main.qml": 'import QtQuick\nimport Bombadil\nAppWindow { bogus: 1 }\n'})
    make_app("pyerr", {"main.qml": says.replace("V", "v1"), "app.py": "raise RuntimeError('no')\n"})
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
           "PYTHONPATH": str(ROOT / "src")}
    names = ["says", "broken", "pyerr"]
    for name in names:
        apps.log_path(name).parent.mkdir(parents=True, exist_ok=True)
        apps.log_path(name).symlink_to("/dev/full")
    status_path = lambda name: apps.log_path(name).with_name(f"{name}.status.json")   # noqa: E731
    procs = {name: subprocess.Popen([sys.executable, str(ROOT / "bin" / "bombadil-app"), "run", name], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL) for name in names}

    def wait_status(name, pred):
        deadline = time.monotonic() + 30
        while True:
            st = runtime._read_json(status_path(name))
            if st and pred(st):
                return st
            assert procs[name].poll() is None, f"{name} exited with {procs[name].returncode}"
            assert time.monotonic() < deadline, f"{name}: {st}"
            time.sleep(0.1)
    try:
        st = wait_status("says", lambda st: st["loaded"])
        assert st["ok"] and st["console"] == ["hello v1"]
        st = wait_status("broken", lambda st: st["errors"])
        assert not st["loaded"] and "bogus" in st["errors"][0]
        st = wait_status("pyerr", lambda st: st["errors"])
        assert not st["loaded"] and st["errors"] == ["app.py:1: RuntimeError: no"]
        # A reload whose new UI says something on completion replaces the old UI.
        (apps.app_dir("says") / "main.qml").write_text(says.replace("V", "v2"))
        st = wait_status("says", lambda st: st["reloads"] == 1)
        assert st["ok"] and st["showing"] == "current" and st["console"] == ["hello v2"]
        time.sleep(0.5)
        assert all(p.poll() is None for p in procs.values())
    finally:
        for p in procs.values():
            p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=10)
    assert {name: p.returncode for name, p in procs.items()} == dict.fromkeys(names, 0)


def test_a_backend_that_skips_qobject_init_is_an_app_error(home):
    pytest.importorskip("PySide6")
    make_app("counter", {"main.qml": COUNT_QML, "app.py": COUNT_PY})
    out = drive("counter", """
        qml, py = (d / "main.qml").read_text(), (d / "app.py").read_text()
        def result():
            st = status()
            return [host.loaded, label(), host.app.property("lastError"), st["ok"], st["showing"], st["errors"], reloads()]
        out["start"] = [host.start(), label()]

        write("app.py", NOSUPER)
        wait_for(lambda: host.attempts == 2)
        out["nosuper"] = result()

        # The error stays while app.py is broken, also across an edit of QML only.
        write("main.qml", qml.replace('"count "', '"n "'))
        wait_for(lambda: host.attempts == 3)
        out["qml_only"] = result()

        write("app.py", py.replace("return 7", "return 8"))
        wait_for(lambda: host.attempts == 4)
        out["fixed"] = result()
    """.replace("NOSUPER", repr(NOSUPER_PY)))
    assert out["start"] == [True, "count 7"]
    loaded, text, error, ok, showing, errors, reloads = out["nosuper"]
    assert (loaded, text, ok, showing, reloads) == (False, "count 7", False, "previous", 0)
    assert "super().__init__()" in error and errors == [error]
    assert out["qml_only"] == [False, "count 7", error, False, "previous", [error], 0]
    assert out["fixed"] == [True, "n 8", "", True, "current", [], 1]


def test_a_backend_that_skips_qobject_init_at_startup_shows_the_error(home):
    pytest.importorskip("PySide6")
    make_app("counter", {"main.qml": COUNT_QML, "app.py": NOSUPER_PY})
    out = drive("counter", """
        out["start"] = [host.start(), host.loaded, host.error_view is not None]
        st = status()
        out["status"] = [st["loaded"], st["showing"], st["errors"]]
    """)
    assert out["start"] == [False, False, True]
    loaded, showing, errors = out["status"]
    assert (loaded, showing) == (False, "errors")
    assert len(errors) == 1 and "super().__init__()" in errors[0]


def test_a_backend_that_qml_refuses_is_an_app_error_too(home):
    pytest.importorskip("PySide6")
    make_app("counter", {"main.qml": COUNT_QML, "app.py": COUNT_PY})
    out = drive("counter", """
        class Refusing:   # a context whose next setContextProperty raises
            def __init__(self, real):
                self.real = real
            def setContextProperty(self, *args):
                raise RuntimeError("refused")
            def __getattr__(self, name):
                return getattr(self.real, name)
        real, refuse = host.engine.rootContext, []
        host.engine.rootContext = lambda: Refusing(real()) if refuse and refuse.pop() else real()
        py = (d / "app.py").read_text()
        out["start"] = [host.start(), label()]
        refuse.append(True)
        write("app.py", py.replace("return 7", "return 8"))
        wait_for(lambda: host.attempts == 2)
        st = status()
        out["refused"] = [host.loaded, label(), st["ok"], st["errors"]]
        write("app.py", py.replace("return 7", "return 9"))
        wait_for(lambda: host.attempts == 3)
        out["fixed"] = [host.loaded, label(), status()["errors"]]
    """)
    assert out["start"] == [True, "count 7"]
    assert out["refused"] == [False, "count 7", False, ["RuntimeError: refused"]]
    assert out["fixed"] == [True, "count 9", []]


SAVE_PY = """\
from PySide6.QtCore import Property, QObject, Slot


class Backend(QObject):
    @Property(int, constant=True)
    def count(self):
        return 7

    @Slot(str)
    def save(self, text):
        pass
"""
FOCUS_QML = """\
import QtQuick
import Bombadil
AppWindow {
    Text { objectName: "label"; text: "count " + backend.count }
    TextInput { objectName: "field"; y: 40; width: 200; text: "draft"
        onActiveFocusChanged: if (!activeFocus) backend.save(text) }
}
"""
RENAME_SAVE = [("save", "store"), ("return 7", "return 8"), ("count 7", "count 8")]
LEAVING = {
    # The old UI's focus loss calls a slot the new Backend no longer has.
    "focus": (SAVE_PY, FOCUS_QML, RENAME_SAVE),
    # The same, caused by the new UI taking focus as it is created.
    "taken": (SAVE_PY, FOCUS_QML.replace("TextInput {", "TextInput { id: field;"),
              RENAME_SAVE + [("    Text {", "    Component.onCompleted: field.forceActiveFocus()\n    Text {")]),
    # The old UI's binding on App.reloads runs once the reload is counted.
    "reloads": (COUNT_PY, """\
        import QtQuick
        import Bombadil
        AppWindow {
            Text { objectName: "label"; text: "r" + App.reloads + " count " + backend.count.toFixed(0) }
        }
        """, [("count", "total"), ("return 7", "return 8")]),
}


@pytest.mark.parametrize("how", sorted(LEAVING))
def test_what_the_old_ui_does_as_it_leaves_is_not_counted(home, how):
    pytest.importorskip("PySide6")
    py, qml, renames = LEAVING[how]
    make_app("leaver", {"main.qml": textwrap.dedent(qml), "app.py": py})
    out = drive("leaver", """
        py, qml = (d / "app.py").read_text(), (d / "main.qml").read_text()
        out["start"] = [host.start(), label()]
        field = host.root.findChild(QQuickItem, "field")
        if field is not None:
            field.forceActiveFocus()
            pump(100)
            out["focused"] = field.hasActiveFocus()
        for old, new in RENAMES:
            py, qml = py.replace(old, new), qml.replace(old, new)
        write("app.py", py)
        write("main.qml", qml)
        wait_for(lambda: host.attempts == 2)
        pump(500)
        st = status()
        out["edited"] = [host.loaded, st["ok"], st["errors"], host.app.property("lastError"), reloads()]
    """.replace("RENAMES", repr(renames)))
    assert out["start"][0]
    assert out.get("focused", True)
    assert out["edited"] == [True, True, [], "", 1]


def test_a_reload_that_fails_late_leaves_the_old_ui_its_focus(home):
    pytest.importorskip("PySide6")
    make_app("focused", {"main.qml": FOCUS_QML, "app.py": SAVE_PY})
    out = drive("focused", """
        qml = (d / "main.qml").read_text()
        out["start"] = [host.start(), label()]
        field = host.root.findChild(QQuickItem, "field")
        field.forceActiveFocus()
        pump(100)
        # Compiles, but the root is created without the property it requires.
        write("main.qml", qml.replace("AppWindow {", "AppWindow { required property int need;"))
        wait_for(lambda: host.attempts == 2)
        pump(200)
        st = status()
        out["failed"] = [host.loaded, st["ok"], st["showing"], "need" in st["errors"][0], field.hasActiveFocus()]
    """)
    assert out["start"][0]
    assert out["failed"] == [False, False, "previous", True, True]


def test_reloading_app_py_does_not_abort_on_a_backend_thread(home):
    pytest.importorskip("PySide6")
    py = """\
import sys
from PySide6.QtCore import Property, QObject, QThread


class Worker(QThread):
    def run(self):
        self.exec()
        sys.__dict__.setdefault("stopped", []).append(1)


class Backend(QObject):
    def __init__(self):
        super().__init__()
        self.worker = Worker(self) if PARENTED else Worker()
        self.worker.start()

    @Property(int, constant=True)
    def count(self):
        return 7
"""
    for parented in (True, False):
        name = f"threads{int(parented)}"
        make_app(name, {"main.qml": COUNT_QML, "app.py": py.replace("PARENTED", repr(parented))})
        out = drive(name, """
            import sys
            out["start"] = [host.start(), label()]
            write("app.py", (d / "app.py").read_text().replace("return 7", "return 8"))
            wait_for(lambda: host.attempts == 2)
            out["edited"] = [host.loaded, label(), status()["ok"], len(getattr(sys, "stopped", []))]
            host.backend.worker.quit()
            host.backend.worker.wait()
        """)
        assert out["start"] == [True, "count 7"]
        assert out["edited"] == [True, "count 8", True, 1]   # the old thread was stopped, not leaked


def test_a_backend_thread_that_will_not_stop_is_kept_not_destroyed(home):
    pytest.importorskip("PySide6")
    make_app("stuck", {"main.qml": COUNT_QML, "app.py": """\
from PySide6.QtCore import Property, QObject, QThread


class Slow(QThread):
    def run(self):
        self.msleep(700)   # does not look at quit()


class Backend(QObject):
    def __init__(self):
        super().__init__()
        self.worker = Slow(self)
        self.worker.start()

    @Property(int, constant=True)
    def count(self):
        return 7
"""})
    out = drive("stuck", """
        runtime.THREAD_WAIT_MS = 50
        out["start"] = [host.start(), label()]
        write("app.py", (d / "app.py").read_text().replace("return 7", "return 8"))
        wait_for(lambda: host.attempts == 2)
        out["edited"] = [host.loaded, label(), len(host._retired)]
        for b in [host.backend, *host._retired]:
            b.worker.wait()
    """)
    assert out["start"] == [True, "count 7"]
    assert out["edited"] == [True, "count 8", 1]


def test_a_backend_that_is_not_a_qobject_still_reloads(home):
    pytest.importorskip("PySide6")
    make_app("plain", {"main.qml": 'import QtQuick\nimport Bombadil\nAppWindow {\n'
                                   '    Text { objectName: "label"; text: "backend " + typeof backend }\n}\n',
                       "app.py": "class Backend:\n    count = 7\n"})
    out = drive("plain", """
        py = (d / "app.py").read_text()
        out["start"] = [host.start(), label()]
        for n in (2, 3):
            write("app.py", py.replace("7", str(n)))
            wait_for(lambda: host.attempts == n)
            out[str(n)] = [host.loaded, status()["ok"], status()["errors"], reloads()]
    """)
    assert out["start"][0]
    assert out["2"] == [True, True, [], 1]
    assert out["3"] == [True, True, [], 2]


@pytest.mark.skipif(not os.path.exists("/dev/full"), reason="no /dev/full")
@pytest.mark.parametrize("how", ["writelines", "reconfigure"])
def test_a_lossy_stream_drops_every_write_the_disk_cannot_take(how):
    stream = open("/dev/full", "w", buffering=1 if how == "writelines" else -1)
    try:
        lossy = runtime._Lossy(stream)
        if how == "writelines":
            lossy.writelines(["a\n", "b\n"])     # line buffered: every newline flushes
        else:
            stream.write("x")                    # held until something flushes it
            lossy.reconfigure(line_buffering=True)
    finally:
        with contextlib.suppress(OSError):
            stream.close()


def test_a_launched_app_prints_to_its_log_as_it_runs(home):
    """stdout is the log, and a pipe or file makes Python block-buffer it: an app's print()
    would only show once the app quits."""
    pytest.importorskip("PySide6")
    make_app("printer", {"main.qml": COUNT_QML, "app.py": COUNT_PY.replace(
        "    @Property", "    def __init__(self):\n        super().__init__()\n"
                         "        print('hello from app.py')\n\n    @Property")})
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    env.update({"QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software", "PYTHONPATH": str(ROOT / "src")})
    log = apps.log_path("printer")
    proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "bombadil-app"), "run", "printer"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while not (log.exists() and "window: " in log.read_text()):   # it is up and has logged
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        time.sleep(0.5)
        assert "hello from app.py" in log.read_text()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

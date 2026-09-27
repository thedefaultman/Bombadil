"""`bombadil-app run`: the app's window, hot reload, saved size, and a status file for the agent.

The runtime owns one native window per app and puts main.qml's root (an AppWindow, which
is an Item) inside it, so a hot reload swaps the content in the same window: no flicker,
same size, same place. New QML is compiled before the old UI is touched; when it fails
the old UI stays up, App.lastError drives the red banner, and status.json says why.
Apps whose root is itself a Window (first-milestone apps) keep that window instead, and
it is recreated on each reload.

`check` loads apps through the same Host, so its screenshot is what `run` shows.
"""

import itertools
import json
import os
import signal
import sys
import threading
import time
import traceback
import types
from pathlib import Path

from .. import apps, paths
from . import engine as kit_engine
from . import placement
from .check import Collector
from .context import AppContext, for_app

WATCHED_SUFFIXES = {".qml", ".js", ".mjs", ".py", ".toml"}
SKIP_DIRS = {"__pycache__", "node_modules"}
DEBOUNCE_MS = 150
DEFAULT_SIZE = (560, 680)
_modules = itertools.count(1)

# Shown in place of an app that has never loaded, until an edit makes it load.
ERROR_VIEW = b"""
import QtQuick
import Bombadil

Rectangle {
    id: view
    property string title: ""
    property var errors: []
    color: Theme.bg
    Flickable {
        anchors.fill: parent
        anchors.margins: Theme.pad
        contentHeight: column.height
        clip: true
        Column {
            id: column
            width: parent.width
            spacing: Theme.gap
            Column {
                width: parent.width
                spacing: 4
                Text {
                    width: parent.width
                    text: view.title + " could not load"
                    color: Theme.bad
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.headingSize
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
                Text {
                    width: parent.width
                    text: "Fix the file and it reloads by itself."
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                    wrapMode: Text.Wrap
                }
            }
            Repeater {
                model: view.errors
                Rectangle {
                    required property var modelData
                    width: column.width
                    height: line.implicitHeight + 2 * Theme.gap
                    radius: Theme.radiusSmall
                    color: Theme.sunken
                    border.color: Theme.border
                    Text {
                        id: line
                        anchors.fill: parent
                        anchors.margins: Theme.gap
                        text: parent.modelData
                        color: Theme.fg
                        font: Theme.monoFont
                        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    }
                }
            }
        }
    }
}
"""
THEME_PROBE = b"import QtQuick\nimport Bombadil\nQtObject { readonly property color bg: Theme.bg }\n"


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _log(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {text}", file=sys.stderr, flush=True)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _watched(name: str) -> bool:
    return not name.startswith(".") and (Path(name).suffix in WATCHED_SUFFIXES or name == "qmldir")


def python_error(ctx: AppContext, e: BaseException) -> str:
    """`app.py:12: NameError: ...`, pointing at the app's own code when it can."""
    if isinstance(e, SyntaxError) and e.filename and Path(e.filename).parent == ctx.dir:
        return f"{Path(e.filename).name}:{e.lineno}: SyntaxError: {e.msg}"
    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename.startswith(f"{ctx.dir}/")]
    where = f"{Path(frames[-1].filename).relative_to(ctx.dir)}:{frames[-1].lineno}: " if frames else ""
    return f"{where}{type(e).__name__}: {e}"


class Host:
    """Loads an app into its window and keeps it there.

    `run()` drives it with QGuiApplication.exec(); `check` and tests call start() and
    load() and pump events themselves.
    """

    def __init__(self, ctx: AppContext):
        from PySide6.QtCore import QTimer, qInstallMessageHandler

        from .native import app as native_app

        self.ctx = ctx
        self.collector = Collector(ctx, echo=not ctx.check, on_change=self._messages_changed)
        qInstallMessageHandler(self.collector)
        self._excepthook = sys.excepthook
        sys.excepthook = self._python_error
        self.engine = kit_engine.make_engine(ctx)
        # Engine warnings come through the signal (with file:line) instead of stderr.
        self.engine.setOutputWarningsToStandardError(False)
        self.engine.warnings.connect(self.collector.add_qml_errors)
        self.app = native_app.instance()
        self.window = None          # the runtime's own window, for Item roots
        self.root = None            # the object from main.qml on screen
        self.error_view = None      # shown while the app has never loaded
        # PySide deletes what a QQmlComponent created when the component goes away.
        self._components: dict[int, object] = {}
        self.declared: tuple[int, int] | None = None  # the size the current root asks for
        self._room: tuple[int, int] | None = None     # the screen's room for the window (Hyprland)
        self._fitted: tuple[int, int] | None = None   # a size the runtime shrank to fit, not the user's
        self.attempts = 0
        self.loaded = False         # the latest load succeeded
        self.backend = None
        self._backend_failed = False  # app.py did not load: try it again on every reload
        self._module_name = ""
        self._sig: dict = {}
        self._dirty = False
        self._closed = False
        self._bg_color = None
        self.watcher = None
        self._debounce = QTimer(singleShot=True, interval=DEBOUNCE_MS)
        self._debounce.timeout.connect(self._maybe_reload)
        self._size_timer = QTimer(singleShot=True, interval=600)
        self._size_timer.timeout.connect(self._save_size)
        # Messages can arrive on any thread; a poll picks them up for status.json.
        self._status_timer = QTimer(interval=500)
        self._status_timer.timeout.connect(self._flush_status)
        if not ctx.check:
            self._status_timer.start()
            self.app.on_show = lambda: self._background(placement.show, ctx.name, start=False)
            self.app.on_hide = lambda: self._background(placement.hide, ctx.name)
            self.app.on_toggle = lambda: self._background(placement.toggle, ctx.name, start=False)
            self.app.on_close = self.quit

    # Loading

    def start(self) -> bool:
        ok = self.load()
        if not self.ctx.check:
            self._watch()
        return ok

    def load(self, changed: set[str] | None = None) -> bool:
        """Load main.qml (again). On failure whatever is on screen stays."""
        self.attempts += 1
        self.collector.reset()
        if self.root is not None:
            self.app.aboutToReload.emit()   # Stores save now and restore in the new UI
        self._refresh_meta()
        self.engine.clearComponentCache()
        obj = self._create() if self._load_backend(changed) else None
        if obj is None:
            if not self.collector.errors:
                self.collector.add("error", f"{self.ctx.main.name}: failed to load")
            self.loaded = False
            self.app.set_last_error(self.collector.errors[0])
            if self.root is None and not self.ctx.check:
                self._show_errors(self.collector.errors)
            self.write_status()
            return False
        self.loaded = True
        if self.attempts > 1:
            self.app.count_reload()
        self.app.set_last_error("")
        self.write_status()
        return True

    def _create(self):
        """Compile and create main.qml's root and put it on screen; None on failure."""
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        from PySide6.QtQuick import QQuickItem, QQuickWindow

        comp = QQmlComponent(self.engine, QUrl.fromLocalFile(str(self.ctx.main)))
        if comp.isLoading():
            loop = QEventLoop()
            comp.statusChanged.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
        if not comp.isReady():
            self.collector.add_qml_errors(comp.errors(), fatal=True)
            return None
        obj = comp.beginCreate(self.engine.rootContext())
        if obj is None:
            self.collector.add_qml_errors(comp.errors(), fatal=True)
            return None
        QQmlEngine.setObjectOwnership(obj, QQmlEngine.ObjectOwnership.CppOwnership)
        self._components[id(obj)] = comp
        if isinstance(obj, QQuickWindow):
            declared = (obj.width(), obj.height())
            size = self._target_size(declared)
            if not self.ctx.check:
                size = self._place(size)
            obj.resize(*size)
            if not self._complete(comp, obj):
                return None
            if not obj.isVisible():
                obj.show()
        elif isinstance(obj, QQuickItem):
            win = self._runtime_window()
            # Parented before completion, so Component.onCompleted already sees its window.
            obj.setParentItem(win.contentItem())
            if not self._complete(comp, obj):
                return None
            # Only now: `width: Theme.pad * 30` is a binding, and bindings run on completion.
            declared = self._item_size(obj)
            size = self._target_size(declared)
            if (win.width(), win.height()) != size:
                win.resize(*size)
        else:
            comp.completeCreate()
            self._discard(obj)
            self.collector.add("error", f"{self.ctx.main.name}: the root object must be AppWindow")
            return None
        self.declared = declared
        self._install(obj)
        return obj

    def _complete(self, comp, obj) -> bool:
        comp.completeCreate()
        if not comp.isError():   # e.g. "Required property x was not initialized"
            return True
        self.collector.add_qml_errors(comp.errors(), fatal=True)
        self._discard(obj)
        return False

    def _discard(self, obj) -> None:
        from PySide6.QtQuick import QQuickItem, QQuickWindow

        if isinstance(obj, QQuickItem):
            obj.setParentItem(None)
        elif isinstance(obj, QQuickWindow):
            obj.hide()
        obj.deleteLater()
        self._components.pop(id(obj), None)

    def _install(self, obj) -> None:
        from PySide6.QtQuick import QQuickItem

        old = [o for o in (self.root, self.error_view) if o is not None]
        self.root, self.error_view = obj, None
        if isinstance(obj, QQuickItem):
            self._fit()
            self._follow_title(obj)
            self._show(self.window)
            obj.forceActiveFocus()
        else:
            obj.widthChanged.connect(self._resized)
            obj.heightChanged.connect(self._resized)
            if self.window is not None and self.window.isVisible():
                self.window.hide()
        for o in old:
            self._discard(o)

    def _show_errors(self, errors: list[str]) -> None:
        """The built-in error list, for an app that has never loaded."""
        from PySide6.QtCore import QUrl
        from PySide6.QtQml import QQmlComponent, QQmlEngine

        if self.error_view is None:
            comp = QQmlComponent(self.engine)
            comp.setData(ERROR_VIEW, QUrl.fromLocalFile("/bombadil/error-view.qml"))
            view = comp.create()
            if view is None:
                return
            QQmlEngine.setObjectOwnership(view, QQmlEngine.ObjectOwnership.CppOwnership)
            self._components[id(view)] = comp
            win = self._runtime_window()
            view.setParentItem(win.contentItem())
            if not win.isVisible():
                win.resize(*(self._saved_size(None) or DEFAULT_SIZE))
            self.error_view = view
        self.error_view.setProperty("title", self.ctx.title)
        self.error_view.setProperty("errors", list(errors))
        self._fit()
        self.window.setTitle(self.ctx.title)
        self._show(self.window)

    def _load_backend(self, changed: set[str] | None) -> bool:
        """Import app.py afresh (a new module each time) and expose its Backend as `backend`.
        Skipped when only QML changed, unless app.py is still broken: its error stays until fixed."""
        if changed is not None and not self._backend_failed and not any(c.endswith(".py") for c in changed):
            return True
        py = self.ctx.dir / "app.py"
        self._backend_failed = False
        if not py.exists():
            if self.backend is not None:
                self.engine.rootContext().setContextProperty("backend", None)
                self.backend = None
            return True
        name = f"bombadil_app_{self.ctx.name.replace('-', '_')}_{next(_modules)}"
        module = types.ModuleType(name)
        module.__file__ = str(py)
        sys.modules[name] = module
        try:
            # compile() rather than import: no stale __pycache__, nothing written into the app.
            exec(compile(py.read_text(), str(py), "exec"), module.__dict__)
            backend = module.Backend() if hasattr(module, "Backend") else None
        except Exception as e:  # noqa: BLE001 - any error in the app's Python is the app's error
            sys.modules.pop(name, None)
            if self.collector.echo:
                traceback.print_exc()
            self.collector.add("error", python_error(self.ctx, e))
            self._backend_failed = True
            return False
        sys.modules.pop(self._module_name, None)
        self._module_name = name
        self.backend = backend
        self.engine.rootContext().setContextProperty("backend", backend)
        return True

    def _refresh_meta(self) -> None:
        title = apps.read_meta(self.ctx.dir).get("title")
        if isinstance(title, str) and title and title != self.ctx.title:
            self.ctx.title = title
            self.app.set_title(title)

    # The window

    def _runtime_window(self):
        from PySide6.QtQuick import QQuickWindow

        if self.window is None:
            self.window = QQuickWindow()
            self.window.setTitle(self.ctx.title)
            color = self._theme_bg()
            if color is not None:
                self.window.setColor(color)
            self.window.resize(*DEFAULT_SIZE)
            self.window.widthChanged.connect(self._resized)
            self.window.heightChanged.connect(self._resized)
        return self.window

    def current_window(self):
        from PySide6.QtQuick import QQuickWindow

        return self.root if isinstance(self.root, QQuickWindow) else self.window

    def _show(self, win) -> None:
        if win is None or win.isVisible():
            return
        if not self.ctx.check:
            size = self._place((win.width(), win.height()))
            if size != (win.width(), win.height()):
                win.resize(*size)
        win.show()

    def _place(self, size: tuple[int, int]) -> tuple[int, int]:
        """Before a window maps: shrink it to the screen if needed, and have Hyprland map it
        straight into the app's drawer (floating, centered, this size), so it slides in."""
        self._room = placement.usable_area()
        fitted = placement.fit(*size, self._room)
        if fitted != size:
            self._fitted = fitted
        _log(f"window: {placement.prepare(self.ctx.name, *fitted)}")
        return fitted

    def _theme_bg(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtQml import QQmlComponent

        if self._bg_color is None:
            comp = QQmlComponent(self.engine)
            comp.setData(THEME_PROBE, QUrl.fromLocalFile("/bombadil/theme-probe.qml"))
            probe = comp.create()
            if probe is not None:
                self._bg_color = probe.property("bg")
                probe.deleteLater()
        return self._bg_color

    def _fit(self) -> None:
        """Item roots fill the window."""
        from PySide6.QtQuick import QQuickItem

        if self.window is None:
            return
        for item in (self.root, self.error_view):
            if isinstance(item, QQuickItem):
                item.setSize(self.window.size().toSizeF())

    def _resized(self) -> None:
        self._fit()
        if not self.ctx.check:
            self._size_timer.start()

    def _follow_title(self, item) -> None:
        def update():
            try:
                title = item.property("title")
            except RuntimeError:  # the old root, on its way out
                return
            self.window.setTitle(title if isinstance(title, str) and title else self.ctx.title)

        update()
        try:
            item.titleChanged.connect(update)
        except (AttributeError, RuntimeError):
            pass

    @staticmethod
    def _item_size(item) -> tuple[int, int]:
        w = item.width() or item.implicitWidth() or DEFAULT_SIZE[0]
        h = item.height() or item.implicitHeight() or DEFAULT_SIZE[1]
        return int(w), int(h)

    def _target_size(self, declared: tuple[int, int]) -> tuple[int, int]:
        """First load: the size the user left it at, unless the app now asks for a new one.
        Reload: keep the window as it is, unless the app asks for a different size, which is
        shrunk to the screen like the first one."""
        declared = (min(max(declared[0], placement.MIN_SIZE[0]), 3840),
                    min(max(declared[1], placement.MIN_SIZE[1]), 2160))
        if self.declared is None:
            return self._saved_size(declared) or declared
        if declared != self.declared:
            fitted = placement.fit(*declared, self._room)
            if fitted != declared:
                self._fitted = fitted
            return fitted
        win = self.current_window()
        return (win.width(), win.height()) if win is not None else declared

    @property
    def _size_path(self) -> Path:
        return self.ctx.state_dir / f"{self.ctx.name}.window.json"

    def _saved_size(self, declared: tuple[int, int] | None) -> tuple[int, int] | None:
        saved = _read_json(self._size_path)
        try:
            size = (int(saved["width"]), int(saved["height"]))
        except (KeyError, TypeError, ValueError):
            return None
        if declared is not None and saved.get("declared") not in (None, list(declared)):
            return None  # the app was edited to ask for a new size since
        return size

    def _save_size(self) -> None:
        win = self.current_window()
        if self.ctx.check or self._closed or win is None or not win.isVisible() or self.declared is None:
            return
        if (win.width(), win.height()) == self._fitted:
            return  # shrunk to this screen by the runtime: not a size the user chose
        data = {"width": win.width(), "height": win.height(), "declared": list(self.declared)}
        if _read_json(self._size_path) != data:
            _write_json(self._size_path, data)

    # Hot reload

    def _scan(self) -> tuple[list[Path], list[Path]]:
        dirs: list[Path] = []
        files: list[Path] = []
        top = self.ctx.dir
        for d, subdirs, names in os.walk(top):
            here = Path(d)
            subdirs[:] = sorted(s for s in subdirs if not s.startswith(".") and s not in SKIP_DIRS
                                and not (here == top and s == "data"))
            dirs.append(here)
            files += [here / n for n in names if _watched(n)]
            if len(dirs) >= 64:
                break
        return dirs, files

    def _signature(self, files: list[Path]) -> dict:
        sig = {}
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            sig[str(f.relative_to(self.ctx.dir))] = (st.st_mtime_ns, st.st_size, st.st_ino)
        return sig

    def _watch(self) -> None:
        from PySide6.QtCore import QFileSystemWatcher

        self.watcher = QFileSystemWatcher()
        self.watcher.fileChanged.connect(self._fs_event)
        self.watcher.directoryChanged.connect(self._fs_event)
        self._sig = self._sync_watch()

    def _sync_watch(self) -> dict:
        """Watch every relevant file and folder; an atomic rename replaces a file's inode,
        so watches are re-added after every change. Returns the files' signature."""
        dirs, files = self._scan()
        have = set(self.watcher.files()) | set(self.watcher.directories())
        missing = [str(p) for p in dirs + files if str(p) not in have]
        if missing:
            self.watcher.addPaths(missing)
        return self._signature(files)

    def _fs_event(self, _path: str) -> None:
        self._debounce.start()

    def _maybe_reload(self) -> None:
        if self._closed:
            return
        sig = self._sync_watch()
        if sig == self._sig:
            return
        changed = {k for k in sig.keys() | self._sig.keys() if sig.get(k) != self._sig.get(k)}
        self._sig = sig
        _log(f"reload: {', '.join(sorted(changed))}")
        self.load(changed)

    # Status and errors

    def status(self) -> dict:
        win = self.current_window()
        showing = "current" if self.loaded else ("previous" if self.root is not None else "errors")
        return {
            "app": self.ctx.name,
            "ok": self.loaded and not self.collector.errors,
            "loaded": self.loaded,
            **self.collector.summary(),
            "reloads": self.app.property("reloads"),
            "showing": showing,
            "size": [win.width(), win.height()] if win is not None else None,
            "pid": os.getpid(),
            "at": _stamp(),
        }

    def write_status(self) -> None:
        self._dirty = False
        if not self.ctx.check and not self._closed:
            _write_json(self.ctx.status_path, self.status())

    def _messages_changed(self) -> None:
        self._dirty = True   # may run on any thread: only set a flag

    def _flush_status(self) -> None:
        if self._dirty and self.attempts:
            self.write_status()

    def _python_error(self, etype, value, tb) -> None:
        """Exceptions in the app's Python (Backend slots) count as app errors."""
        if self.collector.echo:
            traceback.print_exception(etype, value, tb)
        self.collector.add("error", python_error(self.ctx, value.with_traceback(tb)))

    def _background(self, fn, *args, **kwargs) -> None:
        """Hyprland calls can take a moment; never block the UI on them."""
        def call():
            try:
                fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - log it; the app keeps running
                print(f"{fn.__name__}: {e}", file=sys.stderr, flush=True)
        threading.Thread(target=call, daemon=True).start()

    # Leaving

    def quit(self) -> None:
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.quit()

    def close(self) -> None:
        """Save what can be saved and take the UI down; called on quit and after a check."""
        from PySide6.QtCore import QCoreApplication, QEvent, qInstallMessageHandler

        if self._closed:
            return
        if self.root is not None:
            self.app.aboutToReload.emit()
        self._save_size()
        self._closed = True
        for t in (self._debounce, self._size_timer, self._status_timer):
            t.stop()
        self.watcher = None
        # Destroy the UI now: Stores save and Commands end their programs, even though
        # `bombadil-app` then leaves with os._exit.
        for o in (self.root, self.error_view):
            if o is not None:
                self._discard(o)
        self.root = self.error_view = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        sys.excepthook = self._excepthook
        qInstallMessageHandler(None)


def _single_instance(ctx: AppContext):
    """Hold a lock for this app's lifetime; None if another `run` already holds it."""
    import fcntl

    path = ctx.state_dir / f"{ctx.name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _log_to_file(ctx: AppContext) -> None:
    """Started by a launcher (a .desktop file), not from a terminal: send stdout and stderr
    to the app's log, where app_status reads them, rotated like apps.run rotates it."""
    if os.isatty(2):
        return
    log = ctx.log_path
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(log.with_suffix(".log.1"))
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError as e:
        print(f"cannot log to {log}: {e}", file=sys.stderr, flush=True)
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def run(name: str) -> int:
    from PySide6.QtCore import QTimer

    ctx = for_app(name)
    lock = _single_instance(ctx)
    if lock is None:
        print(f"{name} is already running: {placement.show(name, start=False)}", file=sys.stderr)
        return 0
    _log_to_file(ctx)
    app = kit_engine.make_app(ctx)
    print(f"--- {_stamp()} bombadil-app run {name} (pid {os.getpid()})", file=sys.stderr, flush=True)
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Queued, so a signal during startup quits as soon as the loop runs.
        signal.signal(sig, lambda *_: QTimer.singleShot(0, app.quit))
    # Python signal handlers only run when Python gets control: tick now and then.
    tick = QTimer(interval=200)
    tick.timeout.connect(lambda: None)
    tick.start()
    host = Host(ctx)
    app.aboutToQuit.connect(host.close)
    host.start()
    code = app.exec()
    host.close()
    lock.close()
    return code


def status(name: str) -> dict:
    """The last load result a running app wrote, plus its log tail. No Qt."""
    apps.app_dir(name)
    state = _read_json(paths.state_dir() / "apps" / f"{name}.status.json")
    log = apps.log_path(name)
    try:
        tail = log.read_text(errors="replace").splitlines()[-40:]
    except OSError:
        tail = []
    out = {"app": name, **state, "running": placement.is_running(name), "log": tail}
    if not state:
        out["note"] = "no status yet: the app has not been opened since it was created"
    return out

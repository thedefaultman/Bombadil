"""Runs a generated app as a native Qt Quick window and hot reloads it on edit.

Kept separate from apps.py so the rest of Bombadil never imports PySide6.
"""

import importlib.util
import os
import sys
from pathlib import Path

from . import apps, paths


def run(name: str) -> int:
    from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    app = apps.load(name)
    qt_app = QGuiApplication(sys.argv[:1])
    qt_app.setApplicationName(app.title)
    qt_app.setDesktopFileName(f"bombadil-app-{app.name}")

    backend: QObject | None = None
    py = app.path / "app.py"
    py_mtime = py.stat().st_mtime if py.exists() else None
    if py.exists():
        spec = importlib.util.spec_from_file_location(f"bombadil_app_{app.name}", py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "Backend"):
            backend = module.Backend()

    engine = QQmlEngine()
    engine.addImportPath(str(paths.share_dir() / "share" / "qml"))
    engine.addImportPath(str(Path(__file__).resolve().parents[2] / "share" / "qml"))
    if backend is not None:
        engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("appDir", str(app.path))

    main_qml = app.path / "main.qml"
    roots: list[QObject] = []

    def load() -> bool:
        # Compile the new QML before touching the open window: on an error the old window
        # stays up and the errors go to the app's log, where the agent reads them.
        engine.clearComponentCache()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(main_qml)))
        obj = component.create() if component.isReady() else None
        if obj is None:
            for e in component.errors():
                print(f"QML error: {e.toString()}", file=sys.stderr, flush=True)
            return False
        QQmlEngine.setObjectOwnership(obj, QQmlEngine.ObjectOwnership.CppOwnership)
        for old in roots:
            try:
                old.deleteLater()
            except RuntimeError:  # already gone
                pass
        roots[:] = [obj]
        print(f"loaded {main_qml}", file=sys.stderr, flush=True)
        return True

    watcher = QFileSystemWatcher([str(main_qml), str(app.path)])
    debounce = QTimer(singleShot=True, interval=150)
    debounce.timeout.connect(load)

    def changed(_path: str):
        mtime = py.stat().st_mtime if py.exists() else None
        if mtime != py_mtime:
            # The Python behind the app changed: start over so the new Backend is used.
            os.execv(sys.executable, [sys.executable, *sys.argv])
        if str(main_qml) not in watcher.files() and main_qml.exists():
            watcher.addPath(str(main_qml))
        debounce.start()

    watcher.fileChanged.connect(changed)
    watcher.directoryChanged.connect(changed)

    if not load():
        print(f"{main_qml}: failed to load", file=sys.stderr)
        return 1
    return qt_app.exec()

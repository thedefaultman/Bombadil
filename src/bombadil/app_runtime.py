"""Runs a generated app as a native Qt Quick window and hot reloads it on edit.

Kept separate from apps.py so the rest of Bombadil never imports PySide6.
"""

import importlib.util
import sys
from pathlib import Path

from . import apps


def run(name: str) -> int:
    from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    app = apps.load(name)
    qt_app = QGuiApplication(sys.argv[:1])
    qt_app.setApplicationName(app.title)
    qt_app.setDesktopFileName(f"bombadil-{app.name}")

    backend: QObject | None = None
    py = app.path / "app.py"
    if py.exists():
        spec = importlib.util.spec_from_file_location(f"bombadil_app_{app.name}", py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "Backend"):
            backend = module.Backend()

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(Path(__file__).resolve().parents[2] / "share" / "qml"))
    engine.addImportPath("/usr/share/bombadil/qml")
    if backend is not None:
        engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("appDir", str(app.path))

    main_qml = app.path / "main.qml"

    def load():
        for root in engine.rootObjects():
            root.deleteLater()
        engine.clearComponentCache()
        engine.load(QUrl.fromLocalFile(str(main_qml)))

    watcher = QFileSystemWatcher([str(main_qml), str(app.path)])
    debounce = QTimer(singleShot=True, interval=150)
    debounce.timeout.connect(load)

    def changed(_path: str):
        if str(main_qml) not in watcher.files() and main_qml.exists():
            watcher.addPath(str(main_qml))
        debounce.start()

    watcher.fileChanged.connect(changed)
    watcher.directoryChanged.connect(changed)

    load()
    if not engine.rootObjects():
        print(f"{main_qml}: failed to load", file=sys.stderr)
        return 1
    return qt_app.exec()

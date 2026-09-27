"""A QML engine set up the way every Bombadil app expects.

`import Bombadil` gives the kit (share/qml/Bombadil) plus the native types registered
here; the stock QtQuick.Controls are drawn by the Bombadil.Style style, so a plain
`Button` already has the OS look. Used by both `run` and `check`, so what `check`
reports is what the real window does.
"""

import os
from pathlib import Path

from .. import paths
from .context import AppContext

STYLE = "Bombadil.Style"


def qml_dirs() -> list[Path]:
    """Where `import Bombadil` is found: this checkout first, then the installed copy."""
    local = Path(__file__).resolve().parents[3] / "share" / "qml"
    return [d for d in (local, paths.share_dir() / "qml") if d.exists()]


def configure(check: bool) -> None:
    """Environment that must be set before QGuiApplication exists."""
    if check:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        # The software renderer is what offscreen gets anyway; asking for it avoids GL probing.
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", STYLE)


def make_app(ctx: AppContext):
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    configure(ctx.check)
    app = QGuiApplication.instance() or QGuiApplication([f"bombadil-app-{ctx.name}"])
    app.setApplicationName(ctx.title)
    app.setOrganizationName("Bombadil")
    # On Wayland this is the window's app_id: Hyprland rules and the bar match on it.
    app.setDesktopFileName(f"bombadil-app-{ctx.name}")
    font = QFont("Inter")
    font.setPixelSize(14)
    app.setFont(font)
    QQuickStyle.setStyle(STYLE)
    return app


def make_engine(ctx: AppContext):
    """Engine with import paths and native types. Call after make_app()."""
    from PySide6.QtQml import QQmlApplicationEngine

    from . import native

    native.register(ctx)
    engine = QQmlApplicationEngine()
    for d in reversed(qml_dirs()):
        engine.addImportPath(str(d))
    # Other .qml files next to main.qml are usable by name (EntryRow.qml -> EntryRow {}).
    engine.rootContext().setContextProperty("appDir", str(ctx.dir))
    return engine

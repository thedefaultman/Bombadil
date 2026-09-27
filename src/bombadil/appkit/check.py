"""`bombadil-app check`: load an app offscreen, report what went wrong, screenshot it.

The agent gets this back from `create_app`, so it sees QML errors, runtime JS errors
and a picture of the window in the same tool call, without a display. Check mode is
read-only: Store, Vault and TextFile never write, window and agent calls are no-ops.
Commands do run, so the screenshot shows real data.
"""

import json
import re
import sys
import time
from pathlib import Path

from . import engine as kit_engine
from .context import AppContext

# Lines Qt prints that say nothing about the app.
NOISE = re.compile(
    r"This plugin does not support|propagateSizeHints|QFont::|Populating font family aliases|"
    r"qt\.qpa\.|QStandardPaths|XDG_RUNTIME_DIR|Could not find the Qt platform plugin|"
    r"^QQmlApplicationEngine failed to load component|QThreadStorage")
# Runtime problems worth failing a check for.
FATAL = re.compile(
    r"Error:|is not a type|is not defined|Cannot assign|Cannot read property|Invalid property|"
    r"Non-existent attached object|Binding loop|Unable to assign|Cannot call method|"
    r"is not a function|Expected token|Syntax error|failed to load|module .* is not installed|"
    r"Type .* unavailable|Cannot anchor|No such file")


class Collector:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.console: list[str] = []
        kit = kit_engine.qml_dirs()
        self._prefixes = [f"file://{ctx.dir}/", f"{ctx.dir}/"] + [f"file://{d}/" for d in kit] + [f"{d}/" for d in kit]

    def clean(self, text: str) -> str:
        for p in self._prefixes:
            text = text.replace(p, "")
        return text.strip()

    def __call__(self, mode, _context, message: str):
        from PySide6.QtCore import QtMsgType

        text = self.clean(message)
        if not text or NOISE.search(text):
            return
        if mode in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
            self.console.append(text.removeprefix("qml: "))
        elif text in self.errors or text in self.warnings:
            return
        elif FATAL.search(text):
            self.errors.append(text)
        else:
            self.warnings.append(text)

    def add_errors(self, qml_errors) -> None:
        for e in qml_errors:
            text = self.clean(e.toString())
            if text not in self.errors:
                self.errors.append(text)


def _window_for(root):
    """The QQuickWindow to screenshot. A bare Item root (a kit gallery) gets a window made for it."""
    from PySide6.QtGui import QColor
    from PySide6.QtQuick import QQuickItem, QQuickWindow

    if isinstance(root, QQuickWindow):
        return root
    if isinstance(root, QQuickItem):
        win = QQuickWindow()
        win.setColor(QColor("#101214"))
        root.setParentItem(win.contentItem())
        w, h = int(root.width() or root.implicitWidth() or 640), int(root.height() or root.implicitHeight() or 480)
        win.resize(w, h)
        win._kit_root = root  # keep the item alive with its window
        return win
    return None


def check(ctx: AppContext, screenshot: Path | None = None, wait_ms: int = 1200,
          size: tuple[int, int] | None = None) -> dict:
    from PySide6.QtCore import QEventLoop, QTimer, QUrl, qInstallMessageHandler

    started = time.monotonic()
    ctx.check = True
    app = kit_engine.make_app(ctx)
    collector = Collector(ctx)
    qInstallMessageHandler(collector)
    engine = kit_engine.make_engine(ctx)
    engine.warnings.connect(collector.add_errors)
    engine.load(QUrl.fromLocalFile(str(ctx.main)))
    roots = engine.rootObjects()
    result: dict = {"app": ctx.name, "loaded": bool(roots)}

    shot = None
    if roots:
        win = _window_for(roots[0])
        if win is None:
            collector.errors.append("main.qml: the root object must be AppWindow (or a Window/Item)")
        else:
            if size:
                win.resize(*size)
            win.show()
            loop = QEventLoop()
            QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()
            result["size"] = [win.width(), win.height()]
            if screenshot is not None:
                img = win.grabWindow()
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                if img.save(str(screenshot)):
                    shot = str(screenshot)
    qInstallMessageHandler(None)
    result.update({
        "ok": bool(roots) and not collector.errors,
        "errors": collector.errors[:20],
        "warnings": collector.warnings[:20],
        "console": collector.console[-20:],
        "screenshot": shot,
        "seconds": round(time.monotonic() - started, 2),
    })
    del engine
    app.processEvents()
    return result


def main(target: str, screenshot: str | None, wait_ms: int, size: str | None) -> int:
    from .context import for_target

    ctx = for_target(target, check=True)
    wh = tuple(int(x) for x in size.lower().split("x")) if size else None
    result = check(ctx, Path(screenshot) if screenshot else None, wait_ms, wh)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if result["ok"] else 1

"""`bombadil-app check`: load an app offscreen, report what went wrong, screenshot it.

The agent gets this back from `create_app`, so it sees QML errors, runtime JS errors
and a picture of the window in the same tool call, without a display. It loads through
the same Host as `run` (same window, same size rules, same backend), so the picture is
what the user gets. Check mode is read-only: Store, Vault and TextFile never write,
window and agent calls are no-ops. Commands do run, so the screenshot shows real data.
"""

import json
import re
import sys
import time
from collections.abc import Callable
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
    """Sorts what Qt and the QML engine say into errors, warnings and console output.

    Installed as the Qt message handler (console.log, Qt warnings) and connected to the
    engine's `warnings` signal (QML load and runtime errors, with file:line).
    """

    def __init__(self, ctx: AppContext, echo: bool = False, on_change: Callable[[], None] | None = None):
        self.ctx = ctx
        self.echo = echo            # also write every message to stderr (the app's log)
        self.on_change = on_change
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.console: list[str] = []
        kit = kit_engine.qml_dirs()
        self._prefixes = [f"file://{ctx.dir}/", f"{ctx.dir}/"] + [f"file://{d}/" for d in kit] + [f"{d}/" for d in kit]

    def reset(self) -> None:
        self.errors, self.warnings, self.console = [], [], []

    def clean(self, text: str) -> str:
        for p in self._prefixes:
            text = text.replace(p, "")
        return text.strip()

    def add(self, kind: str, text: str) -> None:
        """kind is "error", "warning" or "console"."""
        if kind == "console":
            self.console = (self.console + [text])[-200:]
        else:
            target = self.errors if kind == "error" else self.warnings
            if text in self.errors or text in self.warnings:
                return
            target.append(text)
        if self.echo:
            sys.stderr.write(f"{time.strftime('%H:%M:%S')} {kind}: {text}\n")
            sys.stderr.flush()
        if self.on_change is not None:
            self.on_change()

    def __call__(self, mode, _context, message: str):
        from PySide6.QtCore import QtMsgType

        text = self.clean(message)
        if not text or NOISE.search(text):
            return
        if mode in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
            self.add("console", text.removeprefix("qml: "))
        else:
            self.add("error" if FATAL.search(text) else "warning", text)

    def add_qml_errors(self, qml_errors, fatal: bool = False) -> None:
        """From QQmlComponent.errors() (fatal: the file did not load) or engine.warnings."""
        for e in qml_errors:
            text = self.clean(e.toString())
            if text:
                self.add("error" if fatal or FATAL.search(text) else "warning", text)

    def summary(self) -> dict:
        return {"errors": self.errors[:20], "warnings": self.warnings[:20], "console": self.console[-20:]}


def check(ctx: AppContext, screenshot: Path | None = None, wait_ms: int = 1200,
          size: tuple[int, int] | None = None) -> dict:
    from PySide6.QtCore import QEventLoop, QTimer

    from .runtime import Host

    started = time.monotonic()
    ctx.check = True
    app = kit_engine.make_app(ctx)
    host = Host(ctx)
    loaded = host.start()

    shot = shown_size = None
    win = host.current_window()
    if loaded and win is not None:
        if size:
            win.resize(*size)
        loop = QEventLoop()
        QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()
        shown_size = [win.width(), win.height()]
        if screenshot is not None:
            img = win.grabWindow()
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            if img.save(str(screenshot)):
                shot = str(screenshot)
    summary = host.collector.summary()
    host.close()
    app.processEvents()
    return {
        "app": ctx.name,
        "ok": loaded and not summary["errors"],
        "loaded": loaded,
        **summary,
        "screenshot": shot,
        "size": shown_size,
        "seconds": round(time.monotonic() - started, 2),
    }


def main(target: str, screenshot: str | None, wait_ms: int, size: str | None) -> int:
    from .context import for_target

    ctx = for_target(target, check=True)
    wh = tuple(int(x) for x in size.lower().split("x")) if size else None
    result = check(ctx, Path(screenshot) if screenshot else None, wait_ms, wh)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if result["ok"] else 1

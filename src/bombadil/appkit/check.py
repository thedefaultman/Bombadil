"""`bombadil-app check`: load an app offscreen, report what went wrong, screenshot it.

The agent gets this back from `create_app`, so it sees QML errors, runtime JS errors
and a picture of the window in the same tool call, without a display. It loads through
the same Host as `run` (same window, same size rules, same backend), so the picture is
what the user gets. Check mode is read-only: Store, Vault and TextFile never write,
window and agent calls are no-ops, Qt's own storage (Settings, LocalStorage) goes to a
throwaway test location, and BOMBADIL_CHECK=1 tells app.py and Commands. Commands do run,
so the screenshot shows real data.

An app stuck in a busy loop blocks the whole check process, so a forked watchdog ends it:
it stops the programs the app's Commands started, prints the result the check could not,
and kills the check.
"""

import json
import os
import re
import select
import signal
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
WAIT_MS = 1200  # how long a check lets the app run before judging it (`--wait`)
SETTLE_S = 25   # seconds a check may run past that before the watchdog ends it (tools gives up at 40)


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
        dirs = "|".join(re.escape(str(d)) for d in sorted([ctx.dir, *kit_engine.qml_dirs()], key=lambda d: -len(str(d))))
        # Where a message points (a file URL, or a `path:line`) is shown relative to the app;
        # any other path in the text is left alone.
        self._where = re.compile(rf"file://(?:{dirs})/|(?<![\w/])(?:{dirs})/(?=[^\s:]+:\d)")

    def reset(self) -> None:
        self.errors, self.warnings, self.console = [], [], []

    def clean(self, text: str) -> str:
        return self._where.sub("", text).strip()

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

        console = mode in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg)
        # console.log text is the app's own words: `console.log(App.dataDir)` shows the real path.
        text = message.strip() if console else self.clean(message)
        if not text or NOISE.search(text):
            return
        if console:
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


def check(ctx: AppContext, screenshot: Path | None = None, wait_ms: int = WAIT_MS,
          size: tuple[int, int] | None = None, watchdog: int | None = None) -> dict:
    """`watchdog`: the pipe from start_watchdog(), kept up to date with what the check saw so far."""
    from PySide6.QtCore import QEventLoop, QTimer

    from .runtime import Host

    started = time.monotonic()
    ctx.check = True
    app = kit_engine.make_app(ctx)
    host = Host(ctx)
    if watchdog is not None:
        on_change = host.collector.on_change

        def tell_watchdog():
            try:
                os.write(watchdog, json.dumps({"loaded": host.loaded, **host.collector.summary()}).encode() + b"\n")
            except OSError:
                pass

        def changed():
            on_change()
            tell_watchdog()
        host.collector.on_change = changed
    loaded = host.start()
    if watchdog is not None:
        tell_watchdog()

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


def _kill_children(parent: int) -> None:
    """SIGKILL every process `parent` started, each with its process group: a Command's program
    runs in a session of its own (with whatever `sh -c "a | b"` forked), which outlives the check."""
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            fields = Path(entry.path, "stat").read_text().rpartition(")")[2].split()
            pid, ppid, pgrp = int(entry.name), int(fields[1]), int(fields[2])
        except (OSError, IndexError, ValueError):
            continue
        if ppid != parent:
            continue
        try:
            if pgrp == pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _watch(pipe: int, parent: int, name: str, seconds: float) -> None:
    seen, buf = {}, b""
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        if not select.select([pipe], [], [], left)[0]:
            continue
        chunk = os.read(pipe, 65536)
        if not chunk:
            return   # the check finished (or crashed)
        *lines, buf = (buf + chunk).split(b"\n")
        if lines:   # each line is the whole summary so far: the last one is enough
            try:
                seen = json.loads(lines[-1])
            except ValueError:
                pass
    os.kill(parent, signal.SIGSTOP)   # nothing new starts while its programs are killed
    try:
        _kill_children(parent)
        result = {
            "app": name, "ok": False, "loaded": bool(seen.get("loaded")),
            "errors": [*seen.get("errors", []),
                       f"the app did not settle within {seconds:g} s, so the check was stopped: probably a busy "
                       "loop (a loop that never ends in JS, a binding or app.py). Its Commands were killed."],
            "warnings": seen.get("warnings", []), "console": seen.get("console", []),
            "screenshot": None, "size": None, "seconds": round(seconds, 2),
        }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        sys.stdout.flush()
    finally:
        os.kill(parent, signal.SIGKILL)


def start_watchdog(name: str, seconds: float) -> int:
    """Fork the watchdog, before Qt starts. Returns the pipe check() feeds; when the check
    exits, the pipe closes and the watchdog leaves quietly."""
    r, w = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    if os.fork():
        os.close(r)
        return w
    try:   # never return into the caller's code from the child
        os.close(w)
        _watch(r, os.getppid(), name, seconds)
    finally:
        os._exit(0)


def main(target: str, screenshot: str | None, wait_ms: int, size: str | None, settle: float = SETTLE_S) -> int:
    from .context import for_target

    ctx = for_target(target, check=True)
    wh = tuple(int(x) for x in size.lower().split("x")) if size else None
    dog = start_watchdog(ctx.name, wait_ms / 1000 + settle)
    result = check(ctx, Path(screenshot) if screenshot else None, wait_ms, wh, dog)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if result["ok"] else 1

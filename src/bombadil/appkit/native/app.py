"""`App`: the running app's identity and its window controls, as a QML singleton."""

import shutil
import subprocess
import threading
from collections.abc import Callable

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import qmlRegisterSingletonInstance

from ..context import AppContext
from . import MAJOR, MINOR, URI


class App(QObject):
    reloadsChanged = Signal()
    lastErrorChanged = Signal()
    titleChanged = Signal()
    # Emitted just before the UI is torn down for a hot reload or quit, so Store can save.
    aboutToReload = Signal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx
        self._reloads = 0
        self._last_error = ""
        # The runtime installs these; in `check` they stay no-ops.
        self.on_show: Callable[[], None] = lambda: None
        self.on_hide: Callable[[], None] = lambda: None
        self.on_toggle: Callable[[], None] = lambda: None
        self.on_close: Callable[[], None] = lambda: None

    @Property(str, constant=True)
    def name(self):
        return self._ctx.name

    @Property(str, notify=titleChanged)
    def title(self):
        return self._ctx.title

    def set_title(self, title: str):
        """app.toml changed (the runtime re-reads it on reload)."""
        self._ctx.title = title
        self.titleChanged.emit()

    @Property(str, constant=True)
    def dir(self):
        return str(self._ctx.dir)

    @Property(str, constant=True)
    def dataDir(self):
        d = self._ctx.data_dir
        if not self._ctx.check:
            d.mkdir(parents=True, exist_ok=True)
        return str(d)

    @Property(bool, constant=True)
    def checking(self):
        """True while `check` renders the app offscreen (nothing is saved then)."""
        return self._ctx.check

    @Property(int, notify=reloadsChanged)
    def reloads(self):
        return self._reloads

    def count_reload(self):
        self._reloads += 1
        self.reloadsChanged.emit()

    @Property(str, notify=lastErrorChanged)
    def lastError(self):
        """The first error of the last failed hot reload; "" when the UI loaded cleanly."""
        return self._last_error

    def set_last_error(self, text: str):
        if text != self._last_error:
            self._last_error = text
            self.lastErrorChanged.emit()

    @Slot()
    def show(self):
        self.on_show()

    @Slot()
    def hide(self):
        self.on_hide()

    @Slot()
    def toggle(self):
        self.on_toggle()

    @Slot()
    def close(self):
        self.on_close()

    @Slot(str)
    @Slot(str, str)
    def notify(self, title: str, body: str = ""):
        if self._ctx.check or shutil.which("notify-send") is None:
            return
        subprocess.Popen(["notify-send", "-a", self._ctx.title, title, body],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @Slot(str)
    def openUrl(self, url: str):
        if self._ctx.check:
            return
        from .. import placement

        def open_it():
            try:
                placement.open_url(url)
            except Exception as e:  # noqa: BLE001 - a bad link must not take the app down
                print(f"openUrl({url!r}): {e}", flush=True)
        # Starting the browser can take seconds; never block the UI on it.
        threading.Thread(target=open_it, daemon=True).start()


_instance: App | None = None


def instance() -> App:
    assert _instance is not None
    return _instance


def register(ctx: AppContext) -> None:
    global _instance
    _instance = App(ctx)
    qmlRegisterSingletonInstance(App, URI, MAJOR, MINOR, "App", _instance)

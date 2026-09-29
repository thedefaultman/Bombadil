"""`Clipboard`: copy text from QML, optionally clearing it again later (for secrets)."""

import time

from PySide6.QtCore import Property, QMimeData, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import qmlRegisterSingletonType

from ..context import AppContext
from . import MAJOR, MINOR, URI


def _now() -> float:
    """Seconds on a clock that keeps counting while the machine sleeps (monotonic and Qt timers stop)."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class Clipboard(QObject):
    textChanged = Signal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self.textChanged)
        self._secret = ("", 0.0)           # the last secret copied, and when to clear it (a _now() time)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    @Property(str, notify=textChanged)
    def text(self) -> str:
        return self._clipboard.text()

    @Slot(str)
    @Slot(str, float)
    def copy(self, text: str, clearAfterSeconds: float = 0):
        """Nothing happens during `check`."""
        if self._ctx.check:
            return
        if clearAfterSeconds > 0:
            # Clipboard managers that honour this KDE hint keep secrets out of their history.
            mime = QMimeData()
            mime.setText(text)
            mime.setData("x-kde-passwordManagerHint", b"secret")
            self._clipboard.setMimeData(mime)
            # Checked every second rather than timed once, so time asleep counts too.
            self._secret = (text, _now() + clearAfterSeconds)
            self._timer.start()
        else:
            self._clipboard.setText(text)

    def _tick(self):
        text, clear_at = self._secret
        if self._clipboard.text() != text:
            self._timer.stop()             # something else was copied since
        elif _now() >= clear_at:
            self._clipboard.clear()
            self._timer.stop()


def register(ctx: AppContext) -> None:
    qmlRegisterSingletonType(Clipboard, URI, MAJOR, MINOR, "Clipboard", lambda engine: Clipboard(ctx))

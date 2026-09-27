"""`Clipboard`: copy text from QML, optionally clearing it again later (for secrets)."""

from PySide6.QtCore import Property, QMimeData, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import qmlRegisterSingletonType

from ..context import AppContext
from . import MAJOR, MINOR, URI


class Clipboard(QObject):
    textChanged = Signal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self.textChanged)

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
            QTimer.singleShot(int(clearAfterSeconds * 1000), self, lambda: self._clear_if(text))
        else:
            self._clipboard.setText(text)

    def _clear_if(self, text: str):
        if self._clipboard.text() == text:
            self._clipboard.clear()


def register(ctx: AppContext) -> None:
    qmlRegisterSingletonType(Clipboard, URI, MAJOR, MINOR, "Clipboard", lambda engine: Clipboard(ctx))

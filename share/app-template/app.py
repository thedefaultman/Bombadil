from PySide6.QtCore import QObject, Property, Signal, Slot


class Backend(QObject):
    """Exposed to QML as `backend`. Add @Slot methods for QML to call."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self._items: list[str] = []

    @Property(list, notify=changed)
    def items(self):
        return self._items

    @Slot(str)
    def add(self, text: str):
        self._items.append(text)
        self.changed.emit()

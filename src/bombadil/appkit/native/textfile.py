"""`TextFile`: read, write and watch one text file from QML.

Editors save by writing a new file and renaming it over the old one, which drops the old
inode from QFileSystemWatcher; watching the folder too catches that and re-arms the watch.
"""

from pathlib import Path

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, Signal, Slot

from ..context import AppContext
from . import MAJOR, MINOR, URI
from . import context as native_context
from .files import atomic_write


class TextFile(QObject):
    pathChanged = Signal()
    textChanged = Signal()
    existsChanged = Signal()
    errorChanged = Signal()
    watchChanged = Signal()
    changedOnDisk = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctx = native_context()
        self._path = ""
        self._file: Path | None = None
        self._text = ""
        self._disk: str | None = None     # what the file held when last read or written
        self._stamp = None                # its (inode, size, mtime) then, to skip unrelated folder events
        self._exists = False
        self._error = ""
        self._watch = True
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_disk)
        self._watcher.directoryChanged.connect(self._on_disk)

    def _set(self, attr: str, value, signal):
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _rewatch(self):
        old = self._watcher.files() + self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if not self._watch or self._file is None:
            return
        if self._file.exists():
            self._watcher.addPath(str(self._file))
        if self._file.parent.is_dir():
            self._watcher.addPath(str(self._file.parent))

    def _stat(self):
        try:
            st = self._file.stat()
            return st.st_ino, st.st_size, st.st_mtime_ns
        except (OSError, AttributeError):
            return None

    def _read(self):
        f = self._file
        self._stamp = self._stat()
        if f is None:
            text, exists, error = "", False, ""
        else:
            try:
                text, exists, error = f.read_text(errors="replace"), True, ""
            except FileNotFoundError:
                text, exists, error = "", False, ""
            except OSError as e:
                text, exists, error = "", f.exists(), e.strerror or str(e)
        self._disk = text if exists else None
        self._set("_text", text, self.textChanged)
        self._set("_exists", exists, self.existsChanged)
        self._set("_error", error, self.errorChanged)

    @Slot(str)
    def _on_disk(self, _path: str):
        if self._file is None:
            return
        if self._file.exists() and str(self._file) not in self._watcher.files():
            self._rewatch()
        if self._stat() == self._stamp:
            return
        before = self._disk
        self._read()
        if self._disk != before:
            self.changedOnDisk.emit()

    @Slot()
    def reload(self):
        self._read()
        self._rewatch()

    @Slot(result=bool)
    @Slot(str, result=bool)
    def save(self, text: str | None = None) -> bool:
        """Write `text` (or the current text) atomically, creating folders. Nothing is written in check."""
        if text is None:
            text = self._text
        self._set("_text", text, self.textChanged)
        if self._file is None:
            self._set("_error", "no path", self.errorChanged)
            return False
        if self._ctx.check:
            return True
        try:
            atomic_write(self._file, text)
        except OSError as e:
            self._set("_error", e.strerror or str(e), self.errorChanged)
            return False
        self._disk, self._stamp = text, self._stat()
        self._set("_exists", True, self.existsChanged)
        self._set("_error", "", self.errorChanged)
        self._rewatch()
        return True

    @Slot(result=bool)
    def remove(self) -> bool:
        if self._file is None or self._ctx.check:
            return False
        try:
            self._file.unlink(missing_ok=True)
        except OSError as e:
            self._set("_error", e.strerror or str(e), self.errorChanged)
            return False
        self._disk, self._stamp = None, None
        self._set("_text", "", self.textChanged)
        self._set("_exists", False, self.existsChanged)
        return True

    def _set_path(self, path: str):
        if path == self._path:
            return
        self._path = path
        self._file = self._ctx.resolve(path) if path else None
        self.pathChanged.emit()
        self.reload()

    def _set_watch(self, value: bool):
        if value != self._watch:
            self._watch = value
            self._rewatch()
            self.watchChanged.emit()

    def _set_text(self, text: str):
        """Assigning `text` changes it in memory only; `save()` writes it."""
        self._set("_text", text, self.textChanged)

    path = Property(str, lambda self: self._path, _set_path, notify=pathChanged)
    text = Property(str, lambda self: self._text, _set_text, notify=textChanged)
    exists = Property(bool, lambda self: self._exists, notify=existsChanged)
    error = Property(str, lambda self: self._error, notify=errorChanged)
    watch = Property(bool, lambda self: self._watch, _set_watch, notify=watchChanged)


def register(ctx: AppContext) -> None:
    from PySide6.QtQml import qmlRegisterType

    qmlRegisterType(TextFile, URI, MAJOR, MINOR, "TextFile")

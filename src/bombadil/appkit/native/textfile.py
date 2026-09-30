"""`TextFile`: read, write and watch one text file from QML.

Editors save by writing a new file and renaming it over the old one, which drops the old
inode from QFileSystemWatcher; watching the folder too catches that and re-arms the watch.

Every TextFile shares one QFileSystemWatcher: each watcher is an inotify instance, and a
user may only have 128 of them (for every program they run, not just this app).
"""

import itertools
import weakref
from pathlib import Path

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, Signal, Slot

from ..context import AppContext
from . import MAJOR, MINOR, URI
from . import context as native_context
from .files import atomic_write


class _Watcher:
    """The shared watcher: which TextFile wants which paths, and who to tell when one changes."""

    def __init__(self):
        self._qt: QFileSystemWatcher | None = None
        self._paths: dict[int, set[str]] = {}         # TextFile token -> the paths it watches
        self._handlers: dict[int, weakref.WeakMethod] = {}

    def _watcher(self) -> QFileSystemWatcher:
        if self._qt is None:
            self._qt = QFileSystemWatcher()
            self._qt.fileChanged.connect(self._changed)
            self._qt.directoryChanged.connect(self._changed)
        return self._qt

    def watching(self, path: str) -> bool:
        return self._qt is not None and path in self._qt.files()

    def set(self, token: int, paths: set[str], handler=None):
        """Watch `paths` for this token instead of what it watched before (nothing: stop)."""
        before = self._paths.pop(token, set())
        self._handlers.pop(token, None)
        if paths:
            self._paths[token] = set(paths)
            self._handlers[token] = weakref.WeakMethod(handler)
        wanted = set().union(*self._paths.values())
        try:
            w = self._watcher()
            have = set(w.files()) | set(w.directories())
            gone = [p for p in before if p in have and p not in wanted]
            if gone:
                w.removePaths(gone)
            new = [p for p in paths if p not in have]
            if new:
                w.addPaths(new)
        except RuntimeError:        # quitting: Qt already deleted the watcher
            pass

    def _changed(self, path: str):
        for token, paths in list(self._paths.items()):
            if path not in paths:
                continue
            handler = self._handlers.get(token)
            method = handler() if handler is not None else None
            if method is None:
                self.set(token, set())
            else:
                method(path)


_watcher = _Watcher()
_tokens = itertools.count(1)


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
        self._token = next(_tokens)
        self.destroyed.connect(lambda *_, t=self._token: _watcher.set(t, set()))

    def _set(self, attr: str, value, signal):
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _rewatch(self):
        paths = set()
        if self._watch and self._file is not None:
            if self._file.exists():
                paths.add(str(self._file))
            if self._file.parent.is_dir():
                paths.add(str(self._file.parent))
        _watcher.set(self._token, paths, self._on_disk)

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
        if self._file.exists() and not _watcher.watching(str(self._file)):
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

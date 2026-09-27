"""`KitFiles`: what the kit's own QML (Store) needs from Python, plus helpers the other types share.

Store is written in QML so an app can declare its state as plain properties; it reads and
writes its JSON through this singleton, which applies the app's path rules and never writes
during `check`. Store also reads its property values through here: a read from Python is not
recorded as a binding dependency, so Store can restore values from inside a binding, before
any `Component.onCompleted` in the app runs.
"""

import json
import math
import os
import stat
import tempfile
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QObject, Qt, QTime, QUrl, Slot
from PySide6.QtGui import QColor
from PySide6.QtQml import QJSEngine, QJSValue, qmlEngine, qmlRegisterSingletonType

from ..context import AppContext
from . import MAJOR, MINOR, URI

NULL = QJSValue(QJSValue.SpecialValue.NullValue)

_umask = os.umask(0)
os.umask(_umask)


def js_value(owner: QObject | None, value, engine: QJSEngine | None = None):
    """Python data -> a JS value in `owner`'s engine, with real arrays and objects.

    Qt hands a QVariantList to QML as a sequence wrapper, which fails `Array.isArray` (the kit's
    lists and tables check that) and copies on every read. JSON.parse in the engine builds
    real JS values in one native call. Callers cache the result until the data changes.
    """
    engine = engine or (qmlEngine(owner) if owner is not None else None)
    if engine is None:        # made from Python (tests): plain Qt conversion
        return to_js(value)
    try:
        text = json.dumps(value, allow_nan=False, default=str)
    except ValueError:
        return to_js(value)
    result = engine.globalObject().property("JSON").property("parse").call([text])
    return to_js(value) if result.isError() else result


def to_js(value):
    """Python -> QML value. A bare None would reach QML as `undefined`; apps test for `null`."""
    if value is None:
        return NULL
    if isinstance(value, dict):
        return {k: to_js(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_js(v) for v in value]
    return value


def from_js(value):
    """QML value -> plain Python (lists, dicts, str, numbers, bools, None)."""
    if isinstance(value, QJSValue):
        value = value.toVariant()
    if isinstance(value, dict):
        return {k: from_js(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [from_js(v) for v in value]
    return value


def to_json(value):
    """A property value as JSON data; colors, urls and dates become strings. Raises TypeError.

    NaN and Infinity become null: JSON has no word for them, and a file holding `NaN` would
    fail JSON.parse on the next start.
    """
    if isinstance(value, QJSValue):
        value = value.toVariant()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if isinstance(value, QColor):
        return value.name(QColor.NameFormat.HexArgb if value.alpha() < 255 else QColor.NameFormat.HexRgb)
    if isinstance(value, QUrl):
        return value.toString()
    if isinstance(value, (QDateTime, QDate, QTime)):
        return value.toString(Qt.DateFormat.ISODateWithMs) if value.isValid() else None
    raise TypeError(f"{type(value).__name__} is not JSON")


def atomic_write(path: Path, data: bytes | str, mode: int | None = None) -> None:
    """Write through a temp file and rename, so a crash never leaves half a file behind."""
    if path.is_symlink():
        path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o666 & ~_umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode() if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class KitFiles(QObject):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx

    @Slot(str, result=str)
    def resolve(self, path: str) -> str:
        return str(self._ctx.resolve(path))

    @Slot(str, result=bool)
    def exists(self, path: str) -> bool:
        return self._ctx.resolve(path).exists()

    @Slot(str, result=str)
    def readText(self, path: str) -> str:
        """The file's text, or "" when it is missing or unreadable."""
        try:
            return self._ctx.resolve(path).read_text(errors="replace")
        except OSError:
            return ""

    @Slot(str, str, result=bool)
    def writeText(self, path: str, text: str) -> bool:
        if self._ctx.check:
            return False
        try:
            atomic_write(self._ctx.resolve(path), text)
            return True
        except OSError as e:
            print(f"KitFiles: cannot write {path}: {e}")
            return False

    @Slot(QObject, result="QVariantList")
    def storeKeys(self, obj: QObject) -> list[str]:
        """The values an app declared on a Store: not `name`, `loaded`, `_private`, read-only or object ones."""
        mo = obj.metaObject()
        keys = []
        for i in range(QObject.staticMetaObject.propertyCount(), mo.propertyCount()):
            prop = mo.property(i)
            name = prop.name()
            if name in ("name", "loaded") or name.startswith("_") or not prop.isWritable():
                continue
            if prop.typeName().endswith("*"):
                continue
            keys.append(name)
        return keys

    @Slot(QObject, "QVariant", result=str)
    @Slot(QObject, "QVariant", "QVariant", result=str)
    def snapshot(self, obj: QObject, keys, keep=None) -> str:
        """Those properties' values as a JSON object, read without creating binding dependencies.

        `keep` is what the file held before: its keys that are not properties any more are
        written back, so a property that one version of the app renames is not lost.
        """
        out = {}
        kept = from_js(keep)
        if isinstance(kept, dict):
            for k, v in kept.items():
                try:
                    out[str(k)] = to_json(v)
                except TypeError:
                    pass
        for k in from_js(keys) or []:
            try:
                out[k] = to_json(obj.property(k))
            except TypeError as e:
                print(f"Store: {k} is not saved: {e}")
        return json.dumps(out, indent=1, ensure_ascii=False, allow_nan=False)

    @Slot(str, result=str)
    def setAside(self, path: str) -> str:
        """Rename a file that cannot be read (`x.json` -> `x.json.bad`, then `.bad.2`, ...) so it is
        not overwritten; the new path, or "" when nothing was moved (missing file, `check`)."""
        if self._ctx.check:
            return ""
        src = self._ctx.resolve(path)
        if not src.is_file():
            return ""
        dest = src.with_name(src.name + ".bad")
        n = 1
        while dest.exists():
            n += 1
            dest = src.with_name(f"{src.name}.bad.{n}")
        try:
            os.replace(src, dest)
        except OSError as e:
            print(f"KitFiles: cannot set {path} aside: {e}")
            return ""
        return str(dest)

    @Slot(str, result=bool)
    def remove(self, path: str) -> bool:
        if self._ctx.check:
            return False
        try:
            self._ctx.resolve(path).unlink(missing_ok=True)
            return True
        except OSError:
            return False


def register(ctx: AppContext) -> None:
    qmlRegisterSingletonType(KitFiles, URI, MAJOR, MINOR, "KitFiles", lambda engine: KitFiles(ctx))

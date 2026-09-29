"""`Vault`: an encrypted JSON value on disk (scrypt + AES-256-GCM).

The file holds only the KDF parameters, salt, nonce and ciphertext. The derived key is kept
in memory for the life of the process (keyed by file), so a Vault that hot reload creates
again comes back unlocked, until it auto-locks or `lock()` is called. Every Vault object on
one file shows the same state: saving, unlocking or locking through one shows in the others.

During `check` nothing is written: what would go to the file is kept in memory instead, so
create, lock, unlock and changePassword behave as they will for the user.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import string
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..context import AppContext
from . import MAJOR, MINOR, URI
from . import context as native_context
from .files import NULL, atomic_write, js_value, to_json

VERSION = 1
# New vaults and password changes (OWASP's scrypt minimum: 128 MiB, about half a second).
KDF = {"name": "scrypt", "n": 2 ** 17, "r": 8, "p": 1}
# The most a file may ask for (512 MiB): the parameters are read before the password is
# checked, so a damaged or hostile file must not be able to demand gigabytes.
MAX_N, MAX_R, MAX_P = 2 ** 18, 16, 4
AAD = b"bombadil-vault-1"
SYMBOLS = "!#$%&()*+,-./:;<=>?@[]^_{|}~"
# The most used passwords, which score 0 whatever their length (a deny list, not credentials).
# The same list as PasswordField.qml's `_common`, kept as one string there and here.
COMMON = ("123456 12345678 qwerty azerty letmein welcome admin iloveyou monkey "
          "dragon football baseball master login abc123 111111 000000 sunshine princess trustno1 secret "
          "shadow summer winter hello freedom password whatever starwars changeme default root test "
          "hunter batman superman pokemon passw0rd").split(" ")
RUNS = re.compile(r"(0123|1234|2345|3456|4567|5678|6789|abcd|bcde|cdef|qwer|wert|asdf|sdfg|zxcv)")

# file path -> {"key", "salt", "expires", "touched"}; survives hot reloads, not restarts. Every
# Vault object on the file shares them (_now() times), so using one keeps them all open, and the
# shortest autoLock among them wins.
_keys: dict[str, dict] = {}
# file path -> what the file would hold, during `check` (which writes nothing).
_dry: dict[str, bytes] = {}


def _now() -> float:
    """Seconds on a clock that keeps counting while the machine sleeps (monotonic and Qt timers stop)."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class _Hub(QObject):
    # file path, and the Vault object whose state every other one on that file should show
    changed = Signal(str, QObject)


_hub: _Hub | None = None


def _crypto():
    """(AESGCM, InvalidTag) from the `cryptography` package, imported on first use."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except BaseException as e:  # noqa: BLE001 - a broken install panics in Rust instead of ImportError
        raise RuntimeError(f"the cryptography package is not usable ({e})") from None
    return AESGCM, InvalidTag


def derive(password: str, salt: bytes, kdf: dict) -> bytes:
    n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * r * n + (1 << 20), dklen=32)


def seal(key: bytes, salt: bytes, kdf: dict, value) -> bytes:
    AESGCM, _ = _crypto()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(value, ensure_ascii=False).encode(), AAD)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return json.dumps({"version": VERSION, "kdf": kdf, "salt": b64(salt), "nonce": b64(nonce),
                       "ciphertext": b64(ct)}, indent=1).encode()


def parse(raw: bytes | str) -> dict:
    """A vault file's fields; raises ValueError (or KeyError, TypeError) for a damaged one."""
    doc = json.loads(raw)
    kdf = doc["kdf"]
    n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    if kdf.get("name") != "scrypt" or not (2 <= n <= MAX_N and n & (n - 1) == 0 and 1 <= r <= MAX_R
                                           and 1 <= p <= MAX_P):
        raise ValueError(f"unsupported key derivation {kdf}")
    return {"kdf": kdf, "salt": base64.b64decode(doc["salt"]), "nonce": base64.b64decode(doc["nonce"]),
            "ciphertext": base64.b64decode(doc["ciphertext"])}


def unseal(key: bytes, doc: dict):
    """The decrypted value; raises cryptography's InvalidTag for a wrong key."""
    AESGCM, _ = _crypto()
    return json.loads(AESGCM(key).decrypt(doc["nonce"], doc["ciphertext"], AAD))


def generate_password(length: int = 20, symbols: bool = True) -> str:
    classes = [string.ascii_lowercase, string.ascii_uppercase, string.digits] + ([SYMBOLS] if symbols else [])
    length = max(length, len(classes), 4)
    chars = [secrets.choice(c) for c in classes]
    pool = "".join(classes)
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def strength(pw: str) -> int:
    """0..4, the same score as PasswordField's meter: a line-for-line port of its `_estimate`.

    JavaScript counts a string's length in UTF-16 units and its `.` matches one unit, so the
    checks run on the password spelled that way.
    """
    if not pw:
        return 0
    units = "".join(c if ord(c) < 0x10000 else chr(0xD800 + ((ord(c) - 0x10000) >> 10))
                    + chr(0xDC00 + ((ord(c) - 0x10000) & 0x3FF)) for c in pw)
    lower = units.lower()
    pool = 0
    if re.search(r"[a-z]", units):
        pool += 26
    if re.search(r"[A-Z]", units):
        pool += 26
    if re.search(r"[0-9]", units):
        pool += 10
    if re.search(r"[^a-zA-Z0-9]", units):
        pool += 33
    bits = len(units) * math.log(max(pool, 2)) / math.log(2)
    # Few distinct characters ("aaaa1111") carry less than their length suggests.
    if len(set(pw)) < len(units) / 2:
        bits *= 0.6
    if re.search(r"([^\n\r\u2028\u2029])\1\1", units):
        bits -= 8
    if RUNS.search(lower):
        bits -= 12
    if re.search(r"(19|20)[0-9][0-9]", units):
        bits -= 6
    stripped = re.sub(r"[^a-z]", "", lower)
    if any(w in lower or stripped == w for w in COMMON):
        bits -= 20
        if lower in COMMON or stripped in COMMON:
            return 0
    if len(units) < 6 or bits < 28:
        return 0 if len(units) < 4 else 1
    return 1 if bits < 40 else 2 if bits < 60 else 3 if bits < 80 else 4


class Vault(QObject):
    nameChanged = Signal()
    existsChanged = Signal()
    unlockedChanged = Signal()
    dataChanged = Signal()
    errorChanged = Signal()
    autoLockChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctx = native_context()
        self._name = "vault"
        self._auto_lock = 300
        self._exists = False
        self._unlocked = False
        self._data = None
        self._data_js = None
        self._error = ""
        self._key: bytes | None = None
        self._salt = b""
        self._kdf = dict(KDF)
        self._nonce = b""                  # the file's nonce as this object last read or wrote it
        self._timer = QTimer(self)
        self._timer.setInterval(1000)      # polls the deadline in _keys while unlocked
        self._timer.timeout.connect(self._tick)
        global _hub
        if _hub is None:
            _hub = _Hub()
        _hub.changed.connect(self._follow)
        self._load()

    @property
    def _file(self) -> Path:
        return self._ctx.resolve(f"{self._name}.vault")

    def _set(self, attr: str, value, signal):
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _data_changed(self):
        self._data_js = None
        self.dataChanged.emit()

    def _fail(self, message: str) -> bool:
        self._error = message
        self.errorChanged.emit()
        return False

    def _read(self) -> dict:
        """The file, parsed; during `check`, what this run would have written, if anything."""
        dry = _dry.get(str(self._file)) if self._ctx.check else None
        return parse(dry if dry is not None else self._file.read_bytes())

    def _load(self):
        """Look at the file, and unlock with a key an earlier Vault (before a hot reload) left behind."""
        self._key, self._data = None, None
        self._check_exists()
        cached = _keys.get(str(self._file))
        if self._exists and cached and (cached["expires"] is None or cached["expires"] > _now()):
            try:
                doc = self._read()
                if doc["salt"] == cached["salt"]:
                    self._nonce = doc["nonce"]
                    self._open(cached["key"], doc["salt"], doc["kdf"], unseal(cached["key"], doc))
                    return
            except Exception:  # noqa: BLE001 - a changed or damaged file just means locked
                pass
        self._set("_unlocked", False, self.unlockedChanged)
        self._data_changed()

    def _open(self, key: bytes, salt: bytes, kdf: dict, value):
        self._key, self._salt, self._kdf, self._data = key, salt, kdf, value
        self._set("_error", "", self.errorChanged)
        self._set("_unlocked", True, self.unlockedChanged)
        self._timer.start()
        self._touch()
        self._data_changed()

    def _close(self):
        self._timer.stop()
        self._key, self._data = None, None
        self._set("_unlocked", False, self.unlockedChanged)
        self._data_changed()

    def _share(self):
        """Make every other Vault object on this file show what this one does."""
        _hub.changed.emit(str(self._file), self)

    @Slot(str, QObject)
    def _follow(self, path: str, other):
        """Another Vault object on this file saved, unlocked or locked it: show the same."""
        if other is self or path != str(self._file):
            return
        self._set("_exists", other._exists, self.existsChanged)
        if other._unlocked:
            self._nonce = other._nonce
            self._open(other._key, other._salt, other._kdf, other._data)
        elif self._unlocked:
            self._close()

    def _touch(self):
        """Push the auto-lock deadline back; the key cache expires with it."""
        if self._unlocked:
            expires = _now() + self._auto_lock if self._auto_lock > 0 else None
            _keys[str(self._file)] = {"key": self._key, "salt": self._salt, "expires": expires,
                                      "touched": _now()}

    def _tick(self):
        """Lock once the deadline has passed, time asleep included (a Qt timer does not count it)."""
        cached, now = _keys.get(str(self._file)), _now()
        if (cached is None or (cached["expires"] is not None and now >= cached["expires"])
                or (self._auto_lock > 0 and now >= cached["touched"] + self._auto_lock)):
            self.lock()

    def _write(self, key: bytes, salt: bytes, kdf: dict, value) -> bool:
        """Save `value` under that key, unless something else changed the file since this object read it."""
        if self._unlocked and self._changed_on_disk():
            self.lock()
            return self._fail("the vault changed on disk; unlock it again")
        try:
            sealed = seal(key, salt, kdf, value)
            if self._ctx.check:
                _dry[str(self._file)] = sealed
            else:
                atomic_write(self._file, sealed, mode=0o600)
        except (OSError, TypeError, ValueError, RuntimeError) as e:
            return self._fail(f"cannot save: {e}")
        self._nonce = parse(sealed)["nonce"]
        self._set("_exists", True, self.existsChanged)
        return True

    def _changed_on_disk(self) -> bool:
        """Another process (or an edit by hand) saved the file after this object read or wrote it."""
        try:
            return self._read()["nonce"] != self._nonce
        except (OSError, KeyError, TypeError, ValueError):
            return False                   # gone or damaged: saving puts it right

    def _check_exists(self) -> bool:
        """Look at the disk: another Vault object (or process) may have made or removed the file."""
        there = self._file.exists() or (self._ctx.check and str(self._file) in _dry)
        self._set("_exists", there, self.existsChanged)
        return self._exists

    @Slot(str, result=bool)
    def create(self, password: str) -> bool:
        if self._check_exists():
            return self._fail("a vault already exists")
        if not password:
            return self._fail("empty password")
        salt, kdf = os.urandom(16), dict(KDF)
        key = derive(password, salt, kdf)
        if not self._write(key, salt, kdf, []):
            return False
        self._open(key, salt, kdf, [])
        self._share()
        return True

    @Slot(str, result=bool)
    def unlock(self, password: str) -> bool:
        if not self._check_exists():
            return self._fail("no vault yet")
        try:
            _, InvalidTag = _crypto()
        except RuntimeError as e:
            return self._fail(str(e))
        try:
            doc = self._read()
            key = derive(password, doc["salt"], doc["kdf"])
            value = unseal(key, doc)
        except InvalidTag:
            return self._fail("wrong password")
        except (OSError, KeyError, TypeError, ValueError) as e:
            return self._fail(f"the vault file is damaged: {e}")
        self._nonce = doc["nonce"]
        self._open(key, doc["salt"], doc["kdf"], value)
        self._share()
        return True

    @Slot()
    def lock(self):
        _keys.pop(str(self._file), None)
        self._close()
        self._share()

    @Slot(str, str, result=bool)
    def changePassword(self, old: str, new: str) -> bool:
        """Re-encrypt under `new` with a fresh salt; the vault is unlocked afterwards."""
        if not new:
            return self._fail("empty password")
        if self._unlocked and self._key is not None:
            if not hmac.compare_digest(derive(old, self._salt, self._kdf), self._key):
                return self._fail("wrong password")
            value = self._data
        elif not self.unlock(old):
            return False
        else:
            value = self._data
        salt, kdf = os.urandom(16), dict(KDF)
        key = derive(new, salt, kdf)
        if not self._write(key, salt, kdf, value):     # the old key stays until the new one is on disk
            return False
        self._open(key, salt, kdf, value)
        self._share()
        return True

    @Slot(result=str)
    @Slot(int, result=str)
    @Slot(int, bool, result=str)
    def generatePassword(self, length: int = 20, symbols: bool = True) -> str:
        return generate_password(length, symbols)

    @Slot(str, result=int)
    def strength(self, password: str) -> int:
        return strength(password)

    def _get_data(self):
        if not self._unlocked:
            return NULL
        self._touch()
        if self._data_js is None:
            self._data_js = js_value(self, self._data)
        return self._data_js

    def _set_data(self, value):
        if not self._unlocked:
            self._fail("the vault is locked")
            return
        try:
            value = to_json(value)
        except TypeError as e:
            self._fail(f"not JSON: {e}")
            return
        self._touch()
        if self._write(self._key, self._salt, self._kdf, value):   # a value that was not saved is not shown
            self._data = value
            self._set("_error", "", self.errorChanged)
            self._data_changed()
            self._share()

    def _set_name(self, name: str):
        if name and name != self._name:
            self._timer.stop()
            self._name = name
            self.nameChanged.emit()
            self._load()

    def _set_auto_lock(self, seconds: int):
        seconds = max(0, int(seconds))
        if seconds != self._auto_lock:
            self._auto_lock = seconds
            self.autoLockChanged.emit()
            self._touch()

    name = Property(str, lambda self: self._name, _set_name, notify=nameChanged)
    exists = Property(bool, lambda self: self._exists, notify=existsChanged)
    unlocked = Property(bool, lambda self: self._unlocked, notify=unlockedChanged)
    data = Property("QVariant", _get_data, _set_data, notify=dataChanged)
    error = Property(str, lambda self: self._error, notify=errorChanged)
    autoLock = Property(int, lambda self: self._auto_lock, _set_auto_lock, notify=autoLockChanged)


def register(ctx: AppContext) -> None:
    from PySide6.QtQml import qmlRegisterType

    qmlRegisterType(Vault, URI, MAJOR, MINOR, "Vault")

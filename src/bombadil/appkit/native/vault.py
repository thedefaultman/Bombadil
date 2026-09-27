"""`Vault`: an encrypted JSON value on disk (scrypt + AES-256-GCM).

The file holds only the KDF parameters, salt, nonce and ciphertext. The derived key is kept
in memory for the life of the process (keyed by file), so a Vault that hot reload creates
again comes back unlocked, until it auto-locks or `lock()` is called.
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
from .files import NULL, atomic_write, from_js, js_value

VERSION = 1
KDF = {"name": "scrypt", "n": 2 ** 15, "r": 8, "p": 1}
AAD = b"bombadil-vault-1"
SYMBOLS = "!#$%&()*+,-./:;<=>?@[]^_{|}~"
COMMON = {"password", "123456", "12345678", "123456789", "qwerty", "letmein", "iloveyou", "admin",
          "welcome", "monkey", "dragon", "football", "abc123", "111111", "passw0rd", "trustno1"}

# file path -> {"key", "salt", "expires"}; survives hot reloads, not restarts.
_keys: dict[str, dict] = {}


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


def open_file(path: Path) -> dict:
    doc = json.loads(path.read_text())
    kdf = doc["kdf"]
    if kdf.get("name") != "scrypt" or not (2 <= int(kdf["n"]) <= 2 ** 20 and int(kdf["r"]) <= 32 and int(kdf["p"]) <= 16):
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


def strength(password: str) -> int:
    """0..4 like zxcvbn's score, from character variety and length with repeats and runs discounted."""
    if not password or password.lower() in COMMON or len(password) < 6:
        return 0
    pool = sum(size for test, size in ((str.islower, 26), (str.isupper, 26), (str.isdigit, 10))
               if any(test(c) for c in password))
    if any(not c.isalnum() for c in password):
        pool += 33
    # Characters that repeat or continue a run (aaa, abc, 123) add little.
    effective = 1.0
    for a, b in zip(password, password[1:], strict=False):
        effective += 0.25 if a == b or abs(ord(b) - ord(a)) == 1 else 1.0
    if re.fullmatch(r"(.+?)\1+", password):
        effective = min(effective, len(password) / 2)
    bits = effective * math.log2(max(pool, 2))
    return 0 if bits < 28 else 1 if bits < 40 else 2 if bits < 60 else 3 if bits < 80 else 4


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
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.lock)
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

    def _load(self):
        """Look at the file, and unlock with a key an earlier Vault (before a hot reload) left behind."""
        self._key, self._data = None, None
        self._set("_exists", self._file.exists(), self.existsChanged)
        cached = _keys.get(str(self._file))
        if self._exists and cached and (cached["expires"] is None or cached["expires"] > time.monotonic()):
            try:
                doc = open_file(self._file)
                if doc["salt"] == cached["salt"]:
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
        self._touch()
        self._data_changed()

    def _touch(self):
        """Restart the auto-lock countdown; the key cache expires with it."""
        if not self._unlocked:
            return
        expires = None
        if self._auto_lock > 0:
            self._timer.start(self._auto_lock * 1000)
            expires = time.monotonic() + self._auto_lock
        else:
            self._timer.stop()
        if not self._ctx.check:
            _keys[str(self._file)] = {"key": self._key, "salt": self._salt, "expires": expires}

    def _write(self) -> bool:
        if self._ctx.check:
            return True
        try:
            atomic_write(self._file, seal(self._key, self._salt, self._kdf, self._data), mode=0o600)
        except (OSError, TypeError, ValueError, RuntimeError) as e:
            return self._fail(f"cannot save: {e}")
        self._set("_exists", True, self.existsChanged)
        return True

    @Slot(str, result=bool)
    def create(self, password: str) -> bool:
        if self._exists:
            return self._fail("a vault already exists")
        if not password:
            return self._fail("empty password")
        salt = os.urandom(16)
        self._key, self._salt, self._kdf, self._data = derive(password, salt, KDF), salt, dict(KDF), []
        if not self._write():
            return False
        self._open(self._key, salt, self._kdf, [])
        return True

    @Slot(str, result=bool)
    def unlock(self, password: str) -> bool:
        if not self._file.exists():
            return self._fail("no vault yet")
        try:
            _, InvalidTag = _crypto()
        except RuntimeError as e:
            return self._fail(str(e))
        try:
            doc = open_file(self._file)
            key = derive(password, doc["salt"], doc["kdf"])
            value = unseal(key, doc)
        except InvalidTag:
            return self._fail("wrong password")
        except (OSError, KeyError, TypeError, ValueError) as e:
            return self._fail(f"the vault file is damaged: {e}")
        self._open(key, doc["salt"], doc["kdf"], value)
        return True

    @Slot()
    def lock(self):
        self._timer.stop()
        _keys.pop(str(self._file), None)
        self._key, self._data = None, None
        self._set("_unlocked", False, self.unlockedChanged)
        self._data_changed()

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
        salt = os.urandom(16)
        self._key, self._salt, self._kdf, self._data = derive(new, salt, KDF), salt, dict(KDF), value
        if not self._write():
            return False
        self._open(self._key, salt, self._kdf, value)
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
        value = from_js(value)
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            self._fail(f"not JSON: {e}")
            return
        self._data = value
        self._touch()
        if self._write():
            self._set("_error", "", self.errorChanged)
        self._data_changed()

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

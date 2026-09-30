"""What every part of mail agrees on: error codes, ids, addresses, and the two framings.

Other people write the addresses, names and subjects that pass through here, so nothing in this
module trusts its input: an address is one mailbox with no control characters or line breaks
(a header is built from it later), an id is checked before a key is taken out of it, and a frame
has a size limit before it is read.

Two framings carry the same JSON. Sockets take one object per line. Native messaging (what
Thunderbird's add-on and the host speak on stdio) puts a 4-byte native-endian length in front of each.
"""

import json
import re
import struct
import sys
import unicodedata
from dataclasses import dataclass
from email.utils import formataddr, getaddresses

# What a request that failed says in `code`, next to the sentence in `error`.
ENGINE_DOWN = "engine_down"
NO_ACCOUNT = "no_account"
NOT_FOUND = "not_found"
BAD_REQUEST = "bad_request"
CHANGED = "changed"                    # the draft is not what the view showed
REFUSED = "refused"
TOO_BIG = "too_big"
ENGINE_ERROR = "engine_error"
UNKNOWN_OUTCOME = "unknown_outcome"    # a send that may or may not have happened
INTERNAL = "internal"                  # a bug here: nothing is known to have happened
CODES = (ENGINE_DOWN, NO_ACCOUNT, NOT_FOUND, BAD_REQUEST, CHANGED, REFUSED, TOO_BIG, ENGINE_ERROR,
         UNKNOWN_OUTCOME, INTERNAL)

FOLDERS = ("inbox", "sent", "drafts", "archive", "trash", "other")

NM_MAX_READ = 64 << 20     # the biggest frame the add-on may send; Thunderbird's own limit is 4 GiB
MAX_EMAIL = 254            # RFC 5321
MAX_NAME = 200
MAX_KEY = 512

_ACCOUNT = re.compile(r"a[0-9]{1,9}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")   # the last are lone surrogates
# One mailbox: no quotes, brackets, commas or spaces, and a dot in the domain. Not RFC 5322 (quoted
# local parts and domain literals are refused on purpose): whatever this accepts is safe to put in a header.
_EMAIL = re.compile(r"[^\s@<>(),;:\\\"\[\]]{1,64}@[^\s@<>(),;:\\\"\[\]]{1,255}\.[^\s@<>(),;:\\\"\[\]]{2,63}")


def log(text) -> None:
    print(f"bombadil-mail: {' '.join(str(text).split())}"[:400], file=sys.stderr, flush=True)


class Refusal(Exception):
    """A request that cannot be done, said as a sentence for the person and a code for the program."""

    def __init__(self, code: str, sentence: str):
        super().__init__(sentence)
        self.code = code


class BadId(ValueError):
    """An id that is not `<account>/<key>`."""


class FrameError(ValueError):
    """A native-messaging frame that is cut short, too big, or not a JSON object."""


# -- ids --

def msg_id(account: str, key: str) -> str:
    return f"{account}/{key}"


def split_id(value) -> tuple[str, str]:
    """`a1/<key>` -> ("a1", key). The key is a Message-ID and may itself contain slashes."""
    if not isinstance(value, str):
        raise BadId("That is not a mail id.")
    account, sep, key = value.partition("/")
    if not sep or not _ACCOUNT.fullmatch(account) or not key or len(key) > MAX_KEY or _CONTROL.search(key):
        raise BadId("That is not a mail id.")
    return account, key


# -- addresses --

@dataclass(frozen=True)
class Addr:
    name: str
    email: str

    def as_dict(self) -> dict:
        return {"name": self.name, "email": self.email}

    def __str__(self) -> str:
        return format_addr(self)


def valid_email(text) -> bool:
    if not isinstance(text, str) or len(text) > MAX_EMAIL or _EMAIL.fullmatch(text) is None:
        return False
    # Invisible and look-alike-by-layout characters (zero-width, bidi controls, odd spaces) make an address
    # that reads as another one: refused, so nothing carries one into a header or a recipient list.
    if any(unicodedata.category(c)[0] in "CZ" for c in text):
        return False
    return all(label and label[0] != "-" and label[-1] != "-" for label in text.rpartition("@")[2].split("."))


def _name(text: str) -> str:
    return " ".join(_CONTROL.sub(" ", text).split())[:MAX_NAME]


def parse_addr(text) -> Addr | None:
    """One mailbox ("Priya Shah <priya@acme.test>", "priya@acme.test", or an Addr-shaped dict), or None."""
    if isinstance(text, dict):
        email, name = text.get("email"), text.get("name")
        if not isinstance(email, str) or not isinstance(name, (str, type(None))):
            return None
        email = email.strip().lower()
        return Addr(_name(name or ""), email) if valid_email(email) else None
    if not isinstance(text, str) or len(text) > 2 * MAX_EMAIL + MAX_NAME or _CONTROL.search(text):
        return None
    found = _parse(text)
    return found[0] if len(found) == 1 else None


def _parse(text: str) -> list[Addr]:
    out = []
    for name, email in getaddresses([text]):
        email = email.strip().lower()
        if not valid_email(email):
            return []   # one bad mailbox spoils the list: half a recipient list is worse than none
        out.append(Addr(_name(name), email))
    return out


def parse_addrs(value) -> list[Addr]:
    """A list of mailboxes from a header-style string (commas inside quoted names are fine), or a list of
    strings or dicts. Raises ValueError naming the first one that is not a mailbox."""
    if value is None or value == "":
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("That is not a list of addresses.")
    out: list[Addr] = []
    for item in value:
        if isinstance(item, str):
            if len(item) > 50 * MAX_EMAIL or _CONTROL.search(item):
                raise ValueError(f"“{item[:60]}” is not an email address.")
            found = _parse(item)
            if not found:
                raise ValueError(f"“{item[:60]}” is not an email address.")
            out.extend(found)
        else:
            one = parse_addr(item)
            if one is None:
                raise ValueError(f"“{str(item)[:60]}” is not an email address.")
            out.append(one)
    return out


def addr_or_raw(value) -> Addr:
    """An address as the engine reported it: whatever it was, it becomes something a list can show."""
    one = parse_addr(value)
    if one is not None:
        return one
    raw = value.get("email") if isinstance(value, dict) else value
    return Addr("", _name(str(raw or ""))[:MAX_EMAIL])


def format_addr(addr: Addr) -> str:
    """`Name <email>` with the name quoted when it needs it; just the email when there is no name."""
    return formataddr((addr.name, addr.email)) if addr.name else addr.email


def dedupe(addrs: list[Addr]) -> list[Addr]:
    """The same mailbox once, the first spelling kept."""
    seen, out = set(), []
    for a in addrs:
        if a.email not in seen:
            seen.add(a.email)
            out.append(a)
    return out


# -- JSON --

def canonical(obj) -> str:
    """The one spelling of a value that a fingerprint may be taken of: sorted keys, no spaces, no escapes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def nm_read(stream) -> dict | None:
    """The next native-messaging frame from a binary stream, or None at a clean end of input."""
    head = _read_exact(stream, 4)
    if head is None:
        return None
    (size,) = struct.unpack("=I", head)
    if size > NM_MAX_READ:
        raise FrameError(f"a frame of {size} bytes is over the {NM_MAX_READ} limit")
    body = _read_exact(stream, size) if size else b""
    if body is None:
        raise FrameError("the input ended inside a frame")
    try:
        obj = json.loads(body)
    except (ValueError, RecursionError) as e:
        raise FrameError("a frame that is not JSON") from e
    if not isinstance(obj, dict):
        raise FrameError("a frame that is not an object")
    return obj


def _read_exact(stream, n: int) -> bytes | None:
    """n bytes; None when the stream ended before the first of them; FrameError when it ended after."""
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            if buf:
                raise FrameError("the input ended inside a frame")
            return None
        buf += chunk
    return buf


def nm_write(stream, obj: dict) -> None:
    data = json.dumps(obj, separators=(",", ":")).encode()
    if len(data) >= 1 << 32:
        raise FrameError("a frame too big to send")
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()

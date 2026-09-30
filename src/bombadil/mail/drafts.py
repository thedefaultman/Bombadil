"""What makes a draft safe to press Send on: its fingerprint, its attachment copies, and its warnings.

A draft's fingerprint is the SHA-256 of everything a person would want to have seen before sending:
the account, the kind, what it answers, who it goes to, the subject, the body, and each attachment's
name, size and hash. The view reports the fingerprint it drew, and the press carries it, so what is
sent is what was shown.

For that to hold for attachments, a file is copied into the draft's own folder when it is added
(a copy nobody else writes to) and hashed as it is copied. At send time the copy is read and hashed
again, and those bytes, read once, are what goes to the engine: a file swapped after the copy is
refused, and one swapped after the check is not the one sent, because the checked bytes are.

Warnings are the things a person should look at twice: an address that nothing they did explains
(the defence against a mail that tells the agent to send to someone else), a file that looks like
a key or a password, and a message the provider would refuse for its size. They are advice on the
draft and never a block, except that an agent may not attach a sensitive file at all.
"""

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from .. import paths
from . import accounts, protocol, text
from .protocol import Refusal

ATTACHMENT_MAX = 25 << 20        # bytes of one attached file
ATTACHMENTS_MAX = 20             # files on one draft
BASE64 = 1.37                    # what an attachment grows to once it is encoded for mail
CHUNK = 1 << 20
PRIVATE_KEY = b"PRIVATE KEY-----"

_DRAFT_ID = re.compile(r"d[0-9]{1,12}")
_RE = re.compile(r"\s*(?:re|aw|sv|antw|odp)\s*(?:[\[(]\d+[\])])?\s*:\s*", re.IGNORECASE)
_FWD = re.compile(r"\s*(?:fwd?|wg|vs|tr|rv)\s*:\s*", re.IGNORECASE)


# -- the fingerprint --

def content(draft: dict) -> dict:
    """The parts of a draft that a fingerprint is taken of."""
    return {"account": draft["account"], "kind": draft["kind"], "reply_to": draft["reply_to"],
            "to": draft["to"], "cc": draft["cc"], "bcc": draft["bcc"], "subject": draft["subject"],
            "body": draft["body"],
            "attachments": [{"name": a["name"], "size": a["size"], "sha256": a["sha256"]}
                            for a in draft["attachments"]]}


def fingerprint(draft: dict) -> str:
    return hashlib.sha256(protocol.canonical(content(draft)).encode("utf-8", "surrogatepass")).hexdigest()


# -- words --

def subject_for(kind: str, subject: str) -> str:
    """"Re: Launch date" for a reply and "Fwd: Launch date" for a forward, never "Re: Re: Launch date"."""
    if kind in ("reply", "reply_all"):
        return subject if _RE.match(subject) else f"Re: {subject}"
    if kind == "forward":
        return subject if _FWD.match(subject) else f"Fwd: {subject}"
    return subject


def adds(provider: str, kind: str) -> str:
    """What the provider puts on the message that the person's words do not include."""
    quoted = kind != "new"
    whose = {"google": "Your Gmail signature", "microsoft": "Your Outlook signature"}.get(
        provider, "Your signature, if you have one,")
    return f"{whose} and the quoted message are added when it sends." if quoted else \
        f"{whose} is added when it sends."


def _list(emails: list[str]) -> str:
    shown = emails[:3]
    tail = f" and {len(emails) - 3} more" if len(emails) > 3 else ""
    if len(shown) == 1:
        return shown[0] + tail
    return ", ".join(shown[:-1]) + (" and " if not tail else ", ") + shown[-1] + tail


def encoded_size(draft: dict) -> int:
    return int(sum(a["size"] for a in draft["attachments"]) * BASE64) + len(draft["body"].encode("utf-8", "replace"))


def warnings(draft: dict, *, trusted: set[str], known: set[str], provider: accounts.Provider,
             origin: dict | None = None) -> list[dict]:
    """The warnings for a draft as it is. `trusted` holds the addresses the thread, the person's own words and
    their Sent mail account for; `known` those the engine knows (address books and Sent folders); `origin` is
    what the mail being answered said about itself: {"from": email, "reply_to": [emails]}."""
    out: list[dict] = []
    recipients = [a["email"] for a in (*draft["to"], *draft["cc"], *draft["bcc"])]
    new = [e for e in dict.fromkeys(recipients) if e not in trusted and e not in known]
    if new:
        many = len(new) > 1
        it = "them" if many else "it"
        said = f"{'New addresses' if many else 'New address'}: {_list(new)}. "
        if draft["tainted"]:
            said += (f"You did not type {it}, this thread does not have {it}, and mail that was read for you may "
                     f"have asked for {it}. Check who {'they are' if many else 'it is'} before you press Send.")
        else:
            said += f"Not in this thread, your words, your contacts or anything you have sent. Check {it} before you press Send."
        out.append({"kind": "new_address", "text": said, "addresses": new})
    if origin and draft["kind"] in ("reply", "reply_all") and origin.get("reply_to"):
        sender = origin.get("from") or ""
        redirected = [e for e in origin["reply_to"] if e != sender and e in recipients]
        if redirected:
            out.append({"kind": "other", "addresses": redirected,
                        "text": f"The mail asks for replies to go to {_list(redirected)}, not to {sender}. "
                                "Check that address before you press Send."})
    for a in draft["attachments"]:
        if a.get("sensitive"):
            out.append({"kind": "sensitive_file", "addresses": [],
                        "text": f"“{a['name']}” looks like a key, a password or a sign-in file. "
                                "Check that you mean to send it."})
    size = encoded_size(draft)
    if size > provider.size_limit:
        out.append({"kind": "big", "addresses": [],
                    "text": f"This comes to about {size / 1e6:.0f} MB once it is encoded, and "
                            f"{provider.web_name or 'your mail provider'} allows {provider.size_limit // 1_000_000} MB, "
                            "so it will not send. Take something out."})
    return out


# -- attachment copies --

def folder(draft_id: str) -> Path:
    if not _DRAFT_ID.fullmatch(draft_id):
        raise ValueError("not a draft id")
    return paths.mail_files() / "drafts" / draft_id


def _open_regular(path, what: str) -> tuple[int, os.stat_result]:
    """A file opened for reading that is a regular file and not a link: a pipe or a device would block or never end."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        raise Refusal(protocol.NOT_FOUND, f"{what} cannot be opened.") from e
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise Refusal(protocol.REFUSED, f"{what} is not a file.")
    return fd, st


def copy_attachment(draft_id: str, source, name: str | None, created_by: str) -> dict:
    """Copy a file into the draft's folder, hashing it as it goes. Returns {name, size, sha256, sensitive}.
    An agent is refused a file that looks like a secret; a person gets it marked so the draft warns."""
    path = Path(os.fspath(source)).expanduser()
    if not path.is_absolute():
        raise Refusal(protocol.BAD_REQUEST, "Give the whole path of the file to attach.")
    target = Path(os.path.realpath(path))
    sensitive = text.is_sensitive_path(target)
    if sensitive and created_by != "person":
        raise Refusal(protocol.REFUSED, f"“{target.name}” looks like a key, a password or a sign-in file, "
                                        "so it is not attached.")
    shown = target.name or "That"
    src, st = _open_regular(target, f"“{shown}”")
    try:
        if st.st_size > ATTACHMENT_MAX:
            raise Refusal(protocol.TOO_BIG, f"“{shown}” is over {ATTACHMENT_MAX >> 20} MB, the most one "
                                            "attachment can be.")
        directory = folder(draft_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest, out = _create(directory, name or target.name)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(out, "wb") as f:
                while chunk := os.read(src, CHUNK):
                    if not size and PRIVATE_KEY in chunk[:8192]:
                        if created_by != "person":
                            raise Refusal(protocol.REFUSED, f"“{shown}” holds a private key, so it is not attached.")
                        sensitive = True   # a key in a file with an innocent name: the person is told
                    size += len(chunk)
                    if size > ATTACHMENT_MAX:
                        raise Refusal(protocol.TOO_BIG, f"“{shown}” is over {ATTACHMENT_MAX >> 20} MB, the most "
                                                        "one attachment can be.")
                    digest.update(chunk)
                    f.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
    finally:
        os.close(src)
    return {"name": dest.name, "size": size, "sha256": digest.hexdigest(), "sensitive": sensitive}


def _create(directory: Path, name: str) -> tuple[Path, int]:
    """A new file in the folder under a name that was not there, created so that it cannot be somebody else's."""
    for _ in range(100):
        dest = text.unique_path(directory, name)
        try:
            return dest, os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            continue
    raise Refusal(protocol.REFUSED, "There are too many files of that name.")


def read_verified(draft_id: str, attachments: list[dict]) -> list[bytes]:
    """The bytes of each attachment's copy, checked against the size and hash the draft recorded. Raises
    Refusal("changed") for one that is not what was attached."""
    directory = folder(draft_id)
    out = []
    for a in attachments:
        what = f"“{a['name']}”"
        changed = Refusal(protocol.CHANGED, f"{what} is not the file that was attached. Attach it again.")
        try:
            fd, st = _open_regular(directory / a["name"], what)
        except Refusal as e:
            raise changed from e
        with os.fdopen(fd, "rb") as f:
            if st.st_size != a["size"] or st.st_size > ATTACHMENT_MAX:
                raise changed
            data = f.read(a["size"] + 1)
        if len(data) != a["size"] or hashlib.sha256(data).hexdigest() != a["sha256"]:
            raise changed
        out.append(data)
    return out


def remove_copy(draft_id: str, name: str) -> None:
    try:
        (folder(draft_id) / name).unlink(missing_ok=True)
    except OSError:
        pass


def remove_copies(draft_id: str) -> None:
    """A draft that is sent or discarded no longer needs its files, which are copies of the person's own."""
    try:
        shutil.rmtree(folder(draft_id), ignore_errors=True)
    except ValueError:
        pass

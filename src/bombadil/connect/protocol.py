"""What every part of connections agrees on: error codes, refs, and how somebody else's words are made plain.

Other people write the messages, names and titles that pass through here (a Slack message, a ticket's title, a
notification's text), so nothing in this module trusts its input. The service runs every Message and Task a driver
hands over through `clean_message` and `clean_task`, so what reaches agentd, the model and the bar has no control,
invisible or direction-changing characters, is cut to a known size, and has only the keys of the shapes in
`driver.py`. A ref is checked before a key is taken out of it.
"""

import json
import math
import re
import sys
from urllib.parse import urlsplit

from .driver import KINDS, MESSAGE_KEYS, STATES, TASK_KEYS  # noqa: F401  (re-exported for callers)

# What a request that failed says in `code`, next to the sentence in `error`.
NO_CONNECTION = "no_connection"
NOT_FOUND = "not_found"
BAD_REQUEST = "bad_request"
REFUSED = "refused"
AGENT = "agent"                 # a press from inside an agent's turn
BLOCKED = "blocked"
AUTH = "auth"
RATE_LIMITED = "rate_limited"
SERVICE_DOWN = "service_down"
ALREADY = "already"             # that press was performed
BUSY = "busy"
CHANGED = "changed"             # the fingerprint is not what was pressed for
UNKNOWN_OUTCOME = "unknown_outcome"
INTERNAL = "internal"
CODES = (NO_CONNECTION, NOT_FOUND, BAD_REQUEST, REFUSED, AGENT, BLOCKED, AUTH, RATE_LIMITED, SERVICE_DOWN, ALREADY,
         BUSY, CHANGED, UNKNOWN_OUTCOME, INTERNAL)

SERVICES = ("slack", "linear", "notion", "jira", "todoist", "clickup")
CONVERSATION_KINDS = ("dm", "group", "channel", "private")
SOURCES = ("slack", "notification")

MAX_LINE = 1 << 20           # a request or a push is at most this long
MAX_TEXT = 4000              # a message's text
MAX_TITLE = 200
MAX_NAME = 120
MAX_REASON = 80
MAX_FIELDS = 6
MAX_REF = 200
MAX_URL = 2000

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  \ud800-\udfff]")   # not \t or \n
_CONTROL_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f  \ud800-\udfff]")
# Invisible padding, and the marks that reorder text on screen: a name that has them can read as another name.
INVISIBLE = re.compile("[­͏؜᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_REF = re.compile(r"[a-z]{2,12}:[A-Za-z0-9_.:@/+=-]{1,%d}" % (MAX_REF - 13))
_ID = re.compile(r"[A-Za-z0-9_.:@/+=-]{1,80}")
_CONNECTION_ID = re.compile(r"(slack|mcp):[A-Za-z0-9_.-]{1,60}")
_PROPOSAL_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def log(text) -> None:
    print(f"bombadil-connect: {' '.join(str(text).split())}"[:400], file=sys.stderr, flush=True)


class Refusal(Exception):
    """A request that cannot be done, said as a sentence for the person and a code for the program."""

    def __init__(self, code: str, sentence: str):
        super().__init__(sentence)
        self.code = code


# -- plain words --

def one_line(value, limit: int = MAX_NAME) -> str:
    """Somebody else's words as one plain line: no control, invisible or direction-changing characters and no
    lone surrogates (which cannot be stored or written), white space collapsed, at most `limit` characters."""
    return " ".join(INVISIBLE.sub("", _CONTROL_LINE.sub(" ", str(value if value is not None else ""))).split())[:limit]


def plain_text(value, limit: int = MAX_TEXT) -> str:
    """A message body: line breaks kept (one blank line at most), everything else as `one_line`, at most `limit`
    characters. Text that was cut ends with an ellipsis."""
    text = INVISIBLE.sub("", _CONTROL.sub(" ", str(value if value is not None else "")).replace("\r\n", "\n")
                         .replace("\r", "\n"))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    text = "\n".join(out).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def web_url(value) -> str | None:
    """An address to open in the person's browser: http or https with a host, no spaces, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_URL or re.search(r"\s", value) or _CONTROL_LINE.search(value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if parts.scheme in ("http", "https") and parts.hostname else None


# -- ids and refs --

def valid_ref(value) -> bool:
    return isinstance(value, str) and len(value) <= MAX_REF and _REF.fullmatch(value) is not None


def valid_connection_id(value) -> bool:
    return isinstance(value, str) and _CONNECTION_ID.fullmatch(value) is not None


def valid_proposal_id(value) -> bool:
    return isinstance(value, str) and _PROPOSAL_ID.fullmatch(value) is not None


def slack_ref(team: str, channel: str, ts: str) -> str:
    """`slack:<team>/<channel>/<ts>`: a Slack message is named by its workspace, its conversation and its time."""
    return f"slack:{team}/{channel}/{ts}"


def split_slack_ref(ref) -> tuple[str, str, str]:
    """`slack:T1/C2/1727780000.000100` -> ("T1", "C2", "1727780000.000100"). Raises Refusal when it is not one."""
    if isinstance(ref, str) and ref.startswith("slack:") and valid_ref(ref):
        parts = ref[len("slack:"):].split("/")
        if len(parts) == 3 and all(parts):
            return parts[0], parts[1], parts[2]
    raise Refusal(NOT_FOUND, "That is not a message I know.")


def conversation_key(ref: str) -> str:
    """The conversation a message ref belongs to, which is what 'seen' is kept per: `slack:T1/C2`. Other refs are
    their own conversation."""
    if isinstance(ref, str) and ref.startswith("slack:"):
        parts = ref.split("/")
        if len(parts) == 3:
            return "/".join(parts[:2])
    return str(ref)


# -- shapes --

def _number(value, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _person(value) -> dict:
    value = value if isinstance(value, dict) else {}
    ident = str(value.get("id") or "")
    return {"id": ident if _ID.fullmatch(ident) else "", "name": one_line(value.get("name"), MAX_NAME)}


def clean_message(raw, connection: str) -> dict | None:
    """The Message a driver gave, as the service passes it on: only the keys of `MESSAGE_KEYS`, every text cut and
    plain, `connection` the service's own id for the driver's connection. None when it has no usable ref or
    no words at all (a message that is only a file, a join or a pin is not for the person)."""
    if not isinstance(raw, dict) or not valid_ref(raw.get("ref")):
        return None
    text = plain_text(raw.get("text"))
    if not text:
        return None
    conv = raw.get("conversation") if isinstance(raw.get("conversation"), dict) else {}
    kind = conv.get("kind") if conv.get("kind") in CONVERSATION_KINDS else "channel"
    thread = raw.get("thread")
    source = raw.get("source") if raw.get("source") in SOURCES else "slack"
    return {
        "ref": raw["ref"],
        "source": source,
        "connection": connection,
        "conversation": {"id": _person(conv)["id"], "name": one_line(conv.get("name"), MAX_NAME), "kind": kind},
        "thread": thread if valid_ref(thread) else None,
        "from": _person(raw.get("from")),
        "text": text,
        "ts": _number(raw.get("ts")),
        "mentions_me": bool(raw.get("mentions_me")),
        "reason": one_line(raw.get("reason"), MAX_REASON),
        "unread": bool(raw.get("unread")),
        "web_url": web_url(raw.get("web_url")),
    }


def clean_task(raw, connection: str) -> dict | None:
    """The Task a driver gave, as the service passes it on (see `clean_message`). None when it has no ref or
    no title."""
    if not isinstance(raw, dict) or not valid_ref(raw.get("ref")):
        return None
    title = one_line(raw.get("title"), MAX_TITLE)
    if not title:
        return None
    service = raw.get("service") if raw.get("service") in SERVICES else ""
    if not service:
        return None
    fields = []
    for f in (raw.get("fields") if isinstance(raw.get("fields"), list) else [])[:MAX_FIELDS]:
        if isinstance(f, dict) and one_line(f.get("label"), 40) and one_line(f.get("value"), MAX_NAME):
            fields.append({"key": one_line(f.get("key") or f.get("label"), 40),
                           "label": one_line(f.get("label"), 40), "value": one_line(f.get("value"), MAX_NAME)})
    due = one_line(raw.get("due"), 40)
    return {
        "ref": raw["ref"],
        "service": service,
        "connection": connection,
        "title": title,
        "why": one_line(raw.get("why"), MAX_REASON),
        "status": one_line(raw.get("status"), 60),
        "due": due or None,
        "url": web_url(raw.get("url")),
        "fields": fields,
    }


# -- framing --

def encode(obj: dict) -> bytes:
    """One line for a socket. Text goes as it is, not as \\u escapes, so a long message in a language that is not
    English is not six times as long; a lone surrogate cannot be written as text, so that line is escaped."""
    try:
        return (json.dumps(obj, allow_nan=False, ensure_ascii=False) + "\n").encode()
    except UnicodeEncodeError:
        return (json.dumps(obj, allow_nan=False) + "\n").encode()


def answer_ok(rid, result) -> dict:
    return {"id": rid, "ok": True, "result": result}


def answer_error(rid, sentence: str, code: str = INTERNAL) -> dict:
    return {"id": rid, "ok": False, "error": one_line(sentence, 300) or "That did not work.", "code": code}


__all__ = ["KINDS", "MESSAGE_KEYS", "STATES", "TASK_KEYS"]

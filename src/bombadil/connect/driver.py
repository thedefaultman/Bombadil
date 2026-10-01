"""What a connection driver is, and the shapes it speaks in (docs/CONNECT.md, "Drivers").

bombadil-connect keeps connections to other people's services. The service itself (service.py) knows nothing
about Slack or Linear: it owns the socket, the token store, the press check and the in-memory message ring,
and asks a `Driver` for everything that is particular to a service. A driver for Slack is slack.py, the one
for every service that speaks MCP is mcpconn.py, and tests/ has fakes of both servers and a `FakeDriver`.

The rules a driver keeps:

- Reading is quiet. A driver never changes what it reads (no "mark as read" on the service).
- A driver never writes a token anywhere but `self.secrets`, never logs one, and never returns one: the only
  things that leave a driver are `Message`, `Task`, receipts and sentences made of its own words.
- A driver never does `perform` on its own. The service calls it, once per press, and what it returns or
  raises is final: no retry here and none above it.
- Nothing here blocks the loop: every method is a coroutine, and every network call has a timeout.
"""

from __future__ import annotations

import abc
import hashlib
import json
from collections.abc import Callable
from typing import Any

# A connection's state, as the person sees it: ok (working), signin (waiting for the person to allow it on the
# service's own page), setup (waiting for a step the person does: the Slack recipe), blocked (the service or an
# administrator says no; `note` says why), error (broken; `note` says what to try), off (put away).
STATES = ("ok", "signin", "setup", "blocked", "error", "off")

# What a press can ask a driver to do. A kind belongs to the driver of the service its target names.
KINDS = ("slack_reply", "task_create", "task_comment")


class DriverError(Exception):
    """The service said no, or could not be reached before anything was sent: str() is one plain sentence for the
    person, `.code` is one of the codes in docs/CONNECT.md. Nothing went."""

    def __init__(self, sentence: str, code: str = "error"):
        super().__init__(sentence)
        self.code = code


class UnknownOutcome(DriverError):
    """A `perform` that may or may not have happened (the connection dropped after the request was written, or
    the answer never came). The service tells the person to look where it would have gone, and nothing retries."""

    def __init__(self, sentence: str = ""):
        super().__init__(sentence or "I can't tell whether that went. Look where it would have gone before you "
                                     "press again.", "unknown_outcome")


class Secrets:
    """The view of the token store a driver is given: its own connection's secrets, by name. Names are short
    lowercase words (`app_token`, `user_token`, `access_token`, `refresh_token`, ...)."""

    def get(self, name: str) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, name: str, value: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, name: str | None = None) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def names(self) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError


def fingerprint(kind: str, target: str, content: Any) -> str:
    """SHA-256 of what a press would do: the kind, the target and the content, in canonical JSON. agentd's
    proposals and the service compute it the same way, so a press that names it is for exactly that."""
    body = json.dumps({"kind": kind, "target": target, "content": content}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


class Driver(abc.ABC):
    """One connection. The service builds it from the connection's record and calls `start()` once; `stop()` ends
    it. State changes and pushes go through `emit`."""

    kind = ""        # "slack" or "mcp"
    # The secrets the person's setup may hand over by name (`store_secret`), each with the prefix its value must
    # start with: {"app_token": "xapp-", "user_token": "xoxp-"} for Slack. A driver that gets its tokens some other
    # way (MCP: through its own sign-in) lists none, and the service refuses `store_secret` for it.
    secret_rules: dict[str, str] = {}

    def __init__(self, conn: dict, secrets: Secrets, emit: Callable[[dict], None],
                 clock: Callable[[], float]):
        """`conn` is the record: {"id", "kind", "service", "name", "state", "note", ...}. `emit(push)` takes one of
        {"push": "message", "message": Message}, {"push": "task", "item": Task} or {"push": "state", "state",
        "note"} (a change of the connection's own state, which the service writes down and passes on)."""
        self.conn, self.secrets, self.emit, self.clock = conn, secrets, emit, clock
        self.state = conn.get("state", "setup")
        self.note = conn.get("note", "")

    # -- life --

    @abc.abstractmethod
    async def start(self) -> None:
        """Connect and keep connected (reconnecting by itself, with a back-off). Returns when started, not when
        stopped. A driver with no secrets yet stays in `setup` or `signin` and says what is missing in `note`."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Close everything. After it nothing is emitted."""

    async def secrets_changed(self) -> None:
        """The service stored a secret for this connection (`store_secret`): look again at what is there and, if
        it is now enough, connect. Called after every store, so it must be cheap when nothing new arrived."""

    def owns(self, kind: str, target: str) -> bool:
        """Whether a `perform(kind, target, ...)` is for this connection: Slack owns the refs of its workspace, an
        MCP connection owns its service's tasks (`task_create` with its service as target, `task_comment` with a
        task ref that starts with it). The service picks the one connection that owns a press."""
        return False

    def reads(self) -> str:
        """One sentence saying what this connection reads, for "Connected here"."""
        return ""

    def can_post(self) -> bool:
        return False

    def task_fields(self) -> dict:
        """What a task card for this connection asks the person for, so the card can draw the boxes:
        {"create": [{"key", "label", "edit": "line"|"text"|"date", "required"?: bool}], "comment": [...]}. The
        service passes it on in the connection as `task_fields`; a connection that creates nothing returns {}."""
        return {}

    # -- reading --

    async def messages(self, since: float = 0.0, limit: int = 50) -> list[dict]:
        """Recent messages to the person (direct messages, mentions, threads they are in), newest first, as
        Message dicts, from what the driver has already been told or can fetch with a small request. Slack keeps
        none of them on disk; the driver's own memory is the ring."""
        return []

    async def thread(self, ref: str, limit: int = 20) -> list[dict]:
        """The messages around `ref`, oldest first, ending with `ref`'s own thread or the last few of its
        conversation. Raises DriverError("...", "not_found") when it is not a message of this connection."""
        raise DriverError("That is not a message I know.", "not_found")

    async def tasks(self, limit: int = 30) -> list[dict]:
        """The items that wait on the person ("assigned to you", "mentioned you"), as Task dicts."""
        return []

    # -- writing: only from a press --

    async def perform(self, kind: str, target: str, content: Any) -> dict:
        """Do exactly one thing the person pressed for. Returns a receipt {"line": "Posted in #launch · 11:04",
        "web": {"name": "Slack", "url": ...}} or raises DriverError (nothing went) or UnknownOutcome (may have)."""
        raise DriverError("That is not something this connection does.", "refused")

    # -- for the person's own setup steps --

    def steps(self) -> list[dict]:
        """What the person has to do next, while the state is `signin` or `setup`: [{"id", "say", "open"?: url,
        "point"?: {"text": "Create", "label": "Yours: Create"}}]."""
        return []


# The shapes, for the tests and for the people who read them. Nothing checks these at run time except the
# service's own `clean_message` and `clean_task`, which cut and strip what a driver hands over.

MESSAGE_KEYS = {
    "ref": "str, unique and stable: 'slack:<team>/<channel>/<ts>' (a thread reply carries its own ts)",
    "source": "str, 'slack' or 'notification'",
    "connection": "str, the connection's id",
    "conversation": "{'id', 'name', 'kind': 'dm'|'group'|'channel'|'private'}",
    "thread": "str|None, the ref of the thread's first message when this is a reply in a thread",
    "from": "{'id', 'name'}",
    "text": "str, other people's words, at most 4000 characters",
    "ts": "float, seconds since the epoch",
    "mentions_me": "bool",
    "reason": "str, why it is for the person: 'direct message', 'mentioned you', 'a thread you are in', 'in #launch'",
    "unread": "bool, newer than the last the person saw in that conversation (kept by the service, not by Slack)",
    "web_url": "str|None, opens it in the service's own page",
}

TASK_KEYS = {
    "ref": "str, unique and stable: '<service>:<id>'",
    "service": "str, 'linear', 'notion', 'jira', 'todoist', 'clickup'",
    "connection": "str",
    "title": "str, at most 200 characters (other people's words)",
    "why": "str, 'assigned to you' or 'mentioned you'",
    "status": "str, what the service calls its state",
    "due": "str|None, a date as the service gives it",
    "url": "str|None",
    "fields": "[{'key', 'label', 'value'}] a few more things worth a row on the card",
}

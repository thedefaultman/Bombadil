"""Notices: the lines a service says above the pill, and what can be done about each.

The pill's own line is the machine talking about its turn. A notice is another service talking
to the person (new mail, a draft that is ready, a receipt), so it has its own small stack beside
that line: one line of words, a tone (as in the setup line: step, ask, done or error), up to three
chips and, when it should not wait for ever, a time to live. agentd keeps the one stack; the
shell draws it, newest first, and answers with the chip pressed or the notice put away.

Why it is shaped this way:

- Pure. The stack never touches a socket or a clock of its own: every change is handed to
  `emit` as the message the shell gets, so agentd decides who hears it, and the tests read the
  messages. A client that connects later is given `live()`, the notices nobody has ended.
- At most MAX_NOTICES, and the oldest goes first. A burst of mail must not bury the screen, and
  nothing here is worth a scroll bar.
- A chip runs its notice's handler once. A second tap while it still runs does nothing (a double
  click on Reply must not make two drafts), and when the handler is done the notice ends, unless
  the handler says otherwise or has already replaced it with something new.
- Expiry is checked, not scheduled: agentd asks `next_expiry()` how long it may sleep and calls
  `expire()`, so a notice with no ttl costs nothing while it waits.
"""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

MAX_NOTICES = 4
MAX_ACTIONS = 3
MAX_LINE = 200
MAX_LABEL = 40
TONES = ("step", "ask", "done", "error")
STYLES = ("primary", "quiet")
_ACTION_ID = re.compile(r"[a-z][a-z0-9_:-]{0,39}")
# Control characters, and the ones that flip the direction text is drawn in (a subject line can
# use those to make "gpj.exe" read as "exe.jpg").
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]+")

# Runs when a chip is pressed, with the chip's id. Returns False to leave the notice up.
Handler = Callable[[str], Awaitable[bool | None]]


def one_line(text, limit: int = MAX_LINE) -> str:
    """Words as a line: whitespace and control characters folded to single spaces, then cut.
    What a notice says is often someone else's subject line, so nothing in it can start another
    line or turn the text around."""
    s = " ".join(_CONTROL.sub(" ", str(text)).split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _actions(actions) -> list[dict]:
    out = []
    for a in actions or ():
        if not isinstance(a, dict) or not _ACTION_ID.fullmatch(str(a.get("id", ""))):
            continue
        label = one_line(a.get("label", ""), MAX_LABEL)
        if label:
            out.append({"id": a["id"], "label": label, "style": a.get("style") if a.get("style") in STYLES
                        else "quiet"})
        if len(out) == MAX_ACTIONS:
            break
    return out


@dataclass
class Notice:
    id: int
    source: str
    line: str
    tone: str
    actions: list[dict]
    ttl: float
    at: float
    until: float | None              # on the monotonic clock; None waits for ever
    handler: Handler | None = None
    version: int = 0                 # counts replacements, so a handler can tell it was replaced
    busy: bool = field(default=False, repr=False)

    def wire(self) -> dict:
        return {"type": "notice", "id": self.id, "source": self.source, "line": self.line, "tone": self.tone,
                "actions": [dict(a) for a in self.actions], "ttl": self.ttl, "at": self.at}


class Notices:
    def __init__(self, emit: Callable[[dict], None] | None = None, limit: int = MAX_NOTICES,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic):
        self.emit = emit or (lambda msg: None)
        self.limit = limit
        self.clock = clock
        self.mono = mono
        self._live: dict[int, Notice] = {}   # in the order they were posted
        self._next = 0

    def post(self, source: str, line: str, tone: str = "step", actions=(), ttl: float = 0,
             handler: Handler | None = None) -> int:
        """Say something. The oldest notice that is not an error makes room when the stack is full."""
        self._next += 1
        ttl = max(0.0, float(ttl or 0))
        notice = Notice(self._next, str(source), one_line(line), tone if tone in TONES else "step",
                        _actions(actions), ttl, self.clock(), self.mono() + ttl if ttl else None, handler)
        while len(self._live) >= self.limit:
            # An error is something the person must not miss (a send nobody can be sure of, one that was not
            # theirs): a run of new mail does not push it off. Only when every one is an error does one go.
            self.dismiss(next((i for i, n in self._live.items() if n.tone != "error"), next(iter(self._live))))
        self._live[notice.id] = notice
        self.emit(notice.wire())
        return notice.id

    def replace(self, id: int, line: str | None = None, tone: str | None = None, actions=None,
                ttl: float | None = None, handler: Handler | None = None) -> bool:
        """Change a notice where it stands (what was asked becomes what happened). What is not
        given stays; a new ttl starts counting from now. False when it is gone."""
        notice = self._live.get(id)
        if notice is None:
            return False
        if line is not None:
            notice.line = one_line(line)
        if tone in TONES:
            notice.tone = tone
        if actions is not None:
            notice.actions = _actions(actions)
        if ttl is not None:
            notice.ttl = max(0.0, float(ttl))
            notice.until = self.mono() + notice.ttl if notice.ttl else None
        if handler is not None:
            notice.handler = handler
        notice.at = self.clock()
        notice.version += 1
        self.emit(notice.wire())
        return True

    async def action(self, id: int, action_id: str) -> bool:
        """A chip was pressed. True when its handler ran. A chip the notice does not have (the
        shell was slow, the notice has changed since) and a chip that is already running do nothing."""
        notice = self._live.get(id)
        if (notice is None or notice.handler is None or notice.busy
                or action_id not in {a["id"] for a in notice.actions}):
            return False
        notice.busy = True
        version = notice.version
        try:
            keep = await notice.handler(action_id)
        finally:
            notice.busy = False
        if keep is not False and self._live.get(id) is notice and notice.version == version:
            self.dismiss(id)
        return True

    def dismiss(self, id: int) -> bool:
        if self._live.pop(id, None) is None:
            return False
        self.emit({"type": "notice_end", "id": id})
        return True

    def expire(self) -> list[int]:
        """End the notices whose time is up."""
        now = self.mono()
        gone = [n.id for n in self._live.values() if n.until is not None and n.until <= now]
        for id in gone:
            self.dismiss(id)
        return gone

    def next_expiry(self) -> float | None:
        """Seconds until the next notice runs out, or None when none will."""
        times = [n.until for n in self._live.values() if n.until is not None]
        return max(0.0, min(times) - self.mono()) if times else None

    def live(self) -> list[dict]:
        """What a client that just connected has not seen, oldest first."""
        return [n.wire() for n in self._live.values()]

    def get(self, id: int) -> Notice | None:
        return self._live.get(id)

    def __len__(self) -> int:
        return len(self._live)

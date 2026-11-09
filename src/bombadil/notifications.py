"""Notifications: what the bar's notification server hands agentd, and the notice that becomes of each.

Quickshell's `NotificationServer` (shell/Notifications.qml) owns the org.freedesktop.Notifications name, in place of
mako, and sends every notification to agentd as {"type": "notification", "id", "app", "summary", "body", "urgency",
"icon", "actions"} (docs/CONNECT.md, "Notifications"). It keeps each one tracked until agentd says what became of it:
{"type": "notification_invoke", "id", "action"?} (the notification's own action, which for a browser tab brings the tab
forward) or {"type": "notification_close", "id"}. `Notifier` decides what the person sees and answers both.

Why it is shaped this way:

- A notification is another program's words, and often another person's. The sender and the preview are made plain
  (no control, invisible or direction-changing characters) and cut, and they are always the form "Priya Shah: Legal
  just signed off", the sender first and a colon, never a bare sentence the person could take for Bombadil's own.
  When the notification names nobody the app does.
- One notice per app per two seconds. A burst from one app (a chat that sent five messages at once) is one line, the
  last, with "+N" for the ones before it, and the earlier ones are closed in the bar: nobody can reach them any more.
  A notification with neither a summary nor a body says nothing and is closed.
- Reply is a card, not a send. It shows the notification as a message card whose reply is a copy ("Copy and open"):
  Bombadil can hand the person's words to the page that sent it and cannot answer for them. Open invokes the
  notification's own action, so the page it came from comes forward.
- The bar's ids are the bar's: integers that start again when it restarts. agentd numbers what it holds itself (a
  card is `msg:n<number>`), keeps at most TRACKED, closes the oldest when it needs room, and only ever invokes or
  closes an id that came from a `notification` message and has not been answered yet.
- A notice that ends (its time ran out, the person put it away) without a Reply closes its notification in the bar,
  so the bar does not hold what nobody will ask about. One with a card open stays until the card is put away.

What it does not do: keep any notification's text beyond the notice and the card that show it, or send anything.
"""

import re
import shutil
import subprocess
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .connect import cardmake
from .notices import Notices

COPY_CHARS = 4000        # the longest reply "Copy and open" puts on the clipboard
COPY_SECONDS = 3.0       # wl-copy takes the text and goes to the background; it should not take longer


def copy_text(text: str) -> bool:
    """Put the person's own words on the clipboard (wl-copy, from wl-clipboard) so they can paste them into the page
    that sent the notification. False when there is nothing to copy or it did not work: the caller then does not
    bring the page forward, because the person would paste nothing. Blocking, so agentd calls it in a thread."""
    exe = shutil.which("wl-copy")
    if exe is None or not isinstance(text, str) or not text.strip():
        return False
    try:
        done = subprocess.run([exe], input=text[:COPY_CHARS].encode(), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=COPY_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


RATE_SECONDS = 2.0       # at most one notice per app in this long
TTL = 10.0
CRITICAL_TTL = 30.0
TRACKED = 64             # notifications held in the bar for us
BURSTS = 64              # apps whose last notice is remembered, for the rate limit
APP, SUMMARY, BODY = 40, 60, 100
ACTION_ID = 64
MORE_SHOWN = 99          # "+99" is as far as a count goes
_CARD = re.compile(r"msg:n(\d{1,9})")
_ACTION = re.compile(rf"[^\x00-\x1f\x7f]{{1,{ACTION_ID}}}")


@dataclass
class Held:
    """A notification the bar is holding for us."""
    seq: int
    id: int                   # the bar's
    app: str
    summary: str
    body: str
    action: str | None        # what Open invokes: the notification's default action, if it has one
    notice: int = 0           # the notice that says it
    carded: bool = False      # Reply showed it as a card, which may still be open


@dataclass
class Burst:
    """The last notice said for an app: where a notification that follows it within RATE_SECONDS goes."""
    notice: int
    at: float
    count: int = 0            # how many were folded into it
    seq: int = 0              # the notification it now stands for


def _action_of(actions) -> str | None:
    """The action Open invokes: "default" when the notification has one (what a click on it does), else none. An
    app's other actions are its own words ("Delete", "Archive"), not something a press on Open means, so they are
    never invoked from here; with no default, Open puts the notification away."""
    ids = [a["id"] for a in actions if isinstance(a, dict) and isinstance(a.get("id"), str)
           and _ACTION.fullmatch(a["id"])] if isinstance(actions, list) else []
    return "default" if "default" in ids else None


def _words(value, limit: int) -> str:
    """What the bar sent as text, made plain and cut; anything that is not text is nothing."""
    return cardmake.preview(value, limit) if isinstance(value, str) else ""


def _line(app: str, summary: str, body: str) -> str:
    """"Priya Shah: Legal just signed off"; with only one of the two, the app comes first ("Teams: ...")."""
    if summary and body:
        return f"{summary}: {body}"
    return f"{app or 'A notification'}: {summary or body}"


class Notifier:
    """`send(msg)` tells the bar (notification_invoke, notification_close); `card(card)` shows a card and answers
    whether it was shown. `take` is for a notification message, `copy_open` and `dismissed` for the card buttons of
    a card made here, `notice_ended` for every notice that ends (agentd tells it)."""

    def __init__(self, notices: Notices, send: Callable[[dict], None], card: Callable[[dict], Awaitable[bool]],
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.notices = notices
        self.send = send
        self.card = card
        self.clock = clock
        self.wall = wall
        self._next = 0
        self._held: OrderedDict[int, Held] = OrderedDict()     # agentd's number -> the notification
        self._bursts: OrderedDict[str, Burst] = OrderedDict()  # an app (lowercased) -> its last notice

    # -- the bar's notifications --

    def take(self, msg) -> None:
        """One notification from the bar: a notice, or nothing when it has nothing to say."""
        if not isinstance(msg, dict):
            return
        nid = msg.get("id")
        if not isinstance(nid, int) or isinstance(nid, bool) or not 0 <= nid < 1 << 32:
            return
        app = _words(msg.get("app"), APP)
        summary, body = _words(msg.get("summary"), SUMMARY), _words(msg.get("body"), BODY)
        # An app that sends the same notification again with new words (a player, a download) is updating it: the
        # bar already replaced it, so the old one is let go without a close.
        prior = next((h for h in self._held.values() if h.id == nid), None)
        if prior is not None:
            del self._held[prior.seq]
        if not summary and not body:
            self.send({"type": "notification_close", "id": nid})   # nothing to say, nothing to hold on to
            return
        self._next += 1
        held = Held(self._next, nid, app, summary, body, _action_of(msg.get("actions")))
        self._held[held.seq] = held
        while len(self._held) > TRACKED:
            self._close(next(iter(self._held)))
        ttl = CRITICAL_TTL if msg.get("urgency") == "critical" else TTL
        line, key, now = _line(app, summary, body), app.casefold(), self.clock()
        burst = self._bursts.get(key)
        live = burst is not None and self.notices.get(burst.notice) is not None
        if prior is not None and self.notices.get(prior.notice) is not None:
            held.notice = prior.notice
            self.notices.replace(prior.notice, line=line, ttl=ttl, handler=self._handler(held))
            if burst is not None and burst.notice == prior.notice:
                burst.seq = held.seq
        elif burst is not None and live and now - burst.at < RATE_SECONDS:
            burst.count += 1
            self._close(burst.seq)
            burst.seq, held.notice = held.seq, burst.notice
            self.notices.replace(burst.notice, line=f"{line} +{min(burst.count, MORE_SHOWN)}", ttl=ttl,
                                 handler=self._handler(held))
        else:
            held.notice = self.notices.post("notify", line, "step",
                                            [{"id": "reply", "label": "Reply", "style": "primary"},
                                             {"id": "open", "label": "Open", "style": "quiet"}],
                                            ttl, self._handler(held))
            self._bursts[key] = Burst(held.notice, now, 0, held.seq)
            self._bursts.move_to_end(key)
            while len(self._bursts) > BURSTS:
                self._bursts.popitem(last=False)

    def _handler(self, held: Held):
        async def handler(action_id: str) -> bool:
            if action_id == "open":
                self._invoke(held)
            else:
                held.carded = True
                await self.card(cardmake.notification_card(held.seq, held.app, held.summary, held.body,
                                                           self.wall()))
            return True
        return handler

    # -- what became of one --

    def _invoke(self, held: Held) -> bool:
        """Open: the notification's own action, so the page that sent it comes forward."""
        if self._held.pop(held.seq, None) is None:
            return False
        self.send({"type": "notification_invoke", "id": held.id, **({"action": held.action} if held.action else {})})
        return True

    def _close(self, seq: int) -> None:
        held = self._held.pop(seq, None)
        if held is not None:
            self.send({"type": "notification_close", "id": held.id})

    def notice_ended(self, notice_id: int) -> None:
        """A notice is over. The notification it said is closed in the bar unless Reply turned it into a card."""
        for held in [h for h in self._held.values() if h.notice == notice_id and not h.carded]:
            self._close(held.seq)

    # -- the buttons of its card --

    def copy_open(self, card_id) -> bool:
        """"Copy and open": the shell has copied the person's words; the page they are for comes forward. False when
        the bar no longer holds the notification (it was closed, or the bar restarted)."""
        held = self._of(card_id)
        return held is not None and self._invoke(held)

    def dismissed(self, card_id) -> None:
        """The card was put away: the notification is let go."""
        held = self._of(card_id)
        if held is not None:
            self._close(held.seq)

    def _of(self, card_id) -> Held | None:
        found = _CARD.fullmatch(card_id) if isinstance(card_id, str) else None
        return self._held.get(int(found.group(1))) if found else None

"""How the brain says who and when, in the few words a person would use.

"the machine, turn 41, “install the VPN”", "builder on Bombadil", "you, in the terminal";
"13:02" for today, "Tue 14:02" this week, "3 Sep" this year. Every line in Focus and every
answer to "why is this here?" is built from these, so they read the same everywhere.
"""

import time
from dataclasses import dataclass

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class Who:
    kind: str                 # you, turn, session, app, system, unknown, before
    n: int | None = None      # turn number
    prompt: str = ""          # the turn's words
    name: str = ""            # session role or app title
    project: str = ""         # session's project
    via: str = ""             # program or window app


def _local(t: float) -> time.struct_time:
    return time.localtime(t)


def _days_apart(t: float, now: float) -> int:
    a, b = _local(t), _local(now)
    # round, not floor: the day the clocks go forward is 23 hours long.
    return round((time.mktime((b.tm_year, b.tm_mon, b.tm_mday, 0, 0, 0, 0, 0, -1))
                  - time.mktime((a.tm_year, a.tm_mon, a.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400)


def when(t: float | None, now: float | None = None) -> str:
    """'13:02' today, 'yesterday 13:02', 'Tue 14:02' this week, '3 Sep' this year, else '3 Sep 2025'."""
    if not t:
        return ""
    now = time.time() if now is None else now
    lt = _local(t)
    hm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
    days = _days_apart(t, now)
    if days <= 0:
        return hm
    if days == 1:
        return f"yesterday {hm}"
    if days < 7:
        return f"{DAYS[lt.tm_wday]} {hm}"
    if lt.tm_year == _local(now).tm_year:
        return f"{lt.tm_mday} {MONTHS[lt.tm_mon - 1]}"
    return f"{lt.tm_mday} {MONTHS[lt.tm_mon - 1]} {lt.tm_year}"


def on_day(t: float | None, now: float | None = None) -> str:
    """'today at 13:02', 'yesterday at 13:02', 'on Monday', 'on 3 Sep'."""
    if not t:
        return ""
    now = time.time() if now is None else now
    lt = _local(t)
    hm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
    days = _days_apart(t, now)
    if days <= 0:
        return f"today at {hm}"
    if days == 1:
        return f"yesterday at {hm}"
    if days < 7:
        return f"on {DAY_NAMES[lt.tm_wday]}"
    return "on " + when(t, now)


def quoted(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return f"“{text}”"


def window(via: str) -> str:
    from .actors import window_title
    return window_title(via) if via else ""


def who(w: Who) -> str:
    """The actor as the subject of a line: 'the machine, turn 41, “install the VPN”'."""
    if w.kind == "turn":
        bits = ["the machine"]
        if w.n is not None:
            bits.append(f"turn {w.n}")
        if w.prompt:
            bits.append(quoted(w.prompt))
        return ", ".join(bits)
    if w.kind == "session":
        if w.name and w.project:
            return f"{w.name} on {w.project}"
        return f"a coding session on {w.project}" if w.project else "a coding session"
    if w.kind == "app":
        return w.name or "an app"
    if w.kind == "you":
        place = window(w.via)
        return f"you, in {place}" if place else "you"
    if w.kind == "system":
        return f"the system ({w.via})" if w.via else "the system"
    if w.kind == "before":
        return "before the brain started"
    return "while the brain was not watching"


def by(w: Who) -> str:
    """The actor after a verb: 'by the machine in turn 41, “install the VPN”'."""
    if w.kind == "turn":
        s = "by the machine"
        if w.n is not None:
            s += f" in turn {w.n}"
        return s + (f", {quoted(w.prompt)}" if w.prompt else "")
    if w.kind == "session":
        return "by " + who(w)
    if w.kind == "app":
        return f"by {w.name}" if w.name else "by an app"
    if w.kind == "you":
        place = window(w.via)
        return f"by you in {place}" if place else "by you"
    if w.kind == "system":
        return "by the system" + (f" ({w.via})" if w.via else "")
    return ""


def _then(w: Who, day: str) -> str:
    """The day after an actor: "in turn 44, today at 10:02", "by you in the terminal today"."""
    if not day:
        return ""
    return (", " if w.kind == "turn" else " ") + day


def made_sentence(w: Who, t: float | None, now: float | None = None) -> str:
    """'Made by the machine in turn 41, “install the VPN”, on Monday.'"""
    day = on_day(t, now)
    if w.kind == "you":
        place = window(w.via)
        return f"You made it{' in ' + place if place else ''}{' ' + day if day else ''}."
    if w.kind == "before":
        return "It was here before the brain started."
    if w.kind == "unknown":
        return f"It appeared while the brain was not watching{', ' + day if day else ''}."
    return f"Made {by(w)}{_then(w, day)}."


def changed_sentence(w: Who, t: float | None, now: float | None = None) -> str:
    """'Last changed by you in the terminal today at 10:02.'"""
    day = on_day(t, now)
    if w.kind in ("unknown", "before"):
        return f"Last changed while the brain was not watching{', ' + day if day else ''}."
    return f"Last changed {by(w)}{_then(w, day)}."


def count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"

"""The AI at rest: out of plan or spending, or paused by hand.

Running out is a state of the machine, not an error of one ask. agentd (the only writer) holds
every ask while it lasts and says so once, with the time the provider gave; this module is what
both sides share: the state file other processes read, the reading of a provider's reset time,
and the words (the lines in one place, so the voice file can take them over).

The state file is `paths.state_dir()/rest.json`, in the state directory and not the runtime one
so that a weekly limit and a pause made by hand survive a restart:

    {"hand":      {"claude": {"since": 1759300000.0}},
     "providers": {"claude": {"why": "limit", "kind": "five_hour", "until": 1759329600.0,
                              "since": 1759300000.0}}}

`why` is limit (a plan window), spend (a spending cap or credits used up) or hand; `kind` is the
provider's own name for the window (five_hour, seven_day, seven_day_opus, seven_day_sonnet, overage)
or null; `until` is wall-clock seconds, null when the provider gave no time. A limit lapses on its
own RESET_GRACE after `until`, and a reader never sees a lapsed one. Nothing here calls a model or
the network, and nothing here asks a provider whether its limit is over: the first ask after the
time is the check.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from . import paths

# The provider's time is when the window resets; trust it only a minute later, so the first ask
# is never sent at the very edge of it.
RESET_GRACE = 60.0
# A refusal that names a time already past (a clock off, a window that rolled): try again in this long.
RETRY_AFTER_REFUSAL = 300.0

# How each provider names the page where a limit is raised, when its own message does not.
RAISE_PAGES = {"claude": "https://claude.ai/settings/usage", "codex": "https://chatgpt.com/codex/settings/usage"}
_RAISE_HOSTS = ("claude.ai", "claude.com", "anthropic.com", "chatgpt.com", "openai.com")


def now() -> float:
    return time.time()


@dataclass(frozen=True)
class Rest:
    provider: str
    why: str                    # limit | spend | hand
    kind: str | None = None
    until: float | None = None
    since: float = 0.0


@dataclass(frozen=True)
class Limit:
    """What a refused turn found: which limit, and when it lifts. Made by a provider adapter."""
    why: str                    # limit | spend
    kind: str | None = None
    until: float | None = None
    text: str = ""              # the provider's own words
    url: str | None = None      # the page its message names for raising the limit


# -- the state file --

def path() -> Path:
    return paths.state_dir() / "rest.json"


def _load() -> dict:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    hand = data.get("hand") if isinstance(data.get("hand"), dict) else {}
    providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    return {"hand": hand, "providers": providers}


def _lapsed(entry, at: float) -> bool:
    until = entry.get("until") if isinstance(entry, dict) else None
    return isinstance(until, (int, float)) and until + RESET_GRACE <= at


def read(at: float | None = None) -> dict:
    """The file's contents without the limits that have lapsed."""
    at = now() if at is None else at
    data = _load()
    data["providers"] = {k: v for k, v in data["providers"].items() if isinstance(v, dict) and not _lapsed(v, at)}
    data["hand"] = {k: v for k, v in data["hand"].items() if isinstance(v, dict)}
    return data


def _save(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o644)   # the Brain, the loop and the dots read it as the same user, but also as others
    os.replace(tmp, target)


def limit(provider: str, at: float | None = None) -> Rest | None:
    """The provider's limit if it is on (not lapsed), else None."""
    e = read(at)["providers"].get(provider)
    if not e:
        return None
    until = e.get("until")
    return Rest(provider, "spend" if e.get("why") == "spend" else "limit", e.get("kind"),
                float(until) if isinstance(until, (int, float)) else None, float(e.get("since") or 0.0))


def hand(provider: str) -> Rest | None:
    e = read().get("hand", {}).get(provider)
    return Rest(provider, "hand", since=float(e.get("since") or 0.0)) if e else None


def current(provider: str, at: float | None = None) -> Rest | None:
    """Why the provider rests, if it does: a pause by hand outranks a limit, since pressing the
    switch back on should show what is left."""
    return hand(provider) or limit(provider, at)


def resolve(provider: str, found: Limit, at: float | None = None) -> Rest:
    """What a refusal means for the provider's rest. A time that is already past (a clock that is
    off, a window that rolled over meanwhile) is not kept: the first ask after RETRY_AFTER_REFUSAL
    is the next try."""
    at = now() if at is None else at
    until = found.until
    if until is not None and until + RESET_GRACE <= at:
        until = at + RETRY_AFTER_REFUSAL
    return Rest(provider, found.why, found.kind, until, at)


def set_limit(provider: str, found: Limit, at: float | None = None) -> Rest:
    """Record a refusal."""
    r = resolve(provider, found, at)
    data = _load()
    data["providers"][provider] = {"why": r.why, "kind": r.kind, "until": r.until, "since": r.since}
    _save(data)
    return r


def clear_limit(provider: str) -> None:
    data = _load()
    if data["providers"].pop(provider, None) is not None:
        _save(data)


def set_hand(provider: str, at: float | None = None) -> None:
    data = _load()
    data["hand"][provider] = {"since": now() if at is None else at}
    _save(data)


def clear_hand(provider: str) -> bool:
    """True when there was a pause to clear."""
    data = _load()
    had = data["hand"].pop(provider, None) is not None
    if had:
        _save(data)
    return had


def due(provider: str, at: float | None = None) -> float | None:
    """When the provider's limit should be looked at again (its time plus the grace), or None."""
    e = limit(provider, at)
    return e.until + RESET_GRACE if e is not None and e.until is not None else None


# -- times --

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _local(tz: tzinfo | None) -> tzinfo:
    return tz or datetime.now().astimezone().tzinfo


def when(until: float, at: float | None = None, tz: tzinfo | None = None, short: bool = False) -> str:
    """"15:00" for today, "Thursday 09:00" within six days ("Thu 09:00" short), "1 Nov" beyond.
    Never "tomorrow": a word that promises more than a clock does."""
    tz = _local(tz)
    a = datetime.fromtimestamp(now() if at is None else at, tz)
    u = datetime.fromtimestamp(until, tz)
    days = (u.date() - a.date()).days
    clock = f"{u:%H:%M}"
    if days <= 0:
        return clock
    if days <= 6:
        day = _WEEKDAYS[u.weekday()]
        return f"{day[:3] if short else day} {clock}"
    return f"{u.day} {_MONTHS[u.month - 1]}" + (f" {u.year}" if u.year != a.year else "")


_CLOCK = r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s?(?P<ap>[AaPp][Mm])"
_DAY = (r"(?:(?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,?\s+)?"
        r"(?:(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<year>\d{4}))?(?:,|\s+at)?\s+)?")
_ZONE = r"(?:\s*\((?P<tz>[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)\))?"
_TIME_RE = re.compile(r"(?:resets|reset at|try again at|retry at|available again at)\s+" + _DAY + _CLOCK + _ZONE, re.IGNORECASE)


def fold(text: str) -> str:
    """The provider's words with curly quotes and no-break spaces made plain, so patterns match."""
    return str(text or "").replace("’", "'").replace("‘", "'").replace(" ", " ").replace(" ", " ")


def parse_time(text: str, at: float | None = None, tz: tzinfo | None = None) -> float | None:
    """The time in a provider's sentence: "resets 3:45pm (America/Vancouver)", "resets Oct 3, 3pm",
    "resets Mon 12:00am", "try again at 3:45 PM", "try again at Oct 2nd, 2026 3:45 PM". A clock with
    no day is the next one to come. Local time unless the sentence names a zone. None when there is none."""
    m = _TIME_RE.search(fold(text))
    if m is None:
        return None
    zone = _local(tz)
    if m["tz"]:
        try:
            zone = ZoneInfo(m["tz"])
        except (KeyError, ValueError, OSError):
            pass
    minute = int(m["m"] or 0)
    if not (1 <= int(m["h"]) <= 12 and minute < 60):
        return None
    start = datetime.fromtimestamp(now() if at is None else at, zone)
    hour = int(m["h"]) % 12 + (12 if m["ap"].lower() == "pm" else 0)
    try:
        if m["mon"]:
            month = _MONTHS.index(m["mon"][:3].title()) + 1
            year = int(m["year"]) if m["year"] else start.year
            found = datetime(year, month, int(m["day"]), hour, minute, tzinfo=zone)
            if not m["year"] and found < start - timedelta(days=1):
                found = found.replace(year=year + 1)
        elif m["wd"]:
            ahead = (["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(m["wd"][:3].lower()) - start.weekday()) % 7
            day = start + timedelta(days=ahead)
            found = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if found <= start:
                found += timedelta(days=7)
        else:
            found = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if found <= start:
                found += timedelta(days=1)
    except ValueError:
        return None
    return found.timestamp()


def page_in(text: str, provider: str) -> str:
    """Where to raise the limit: the page the provider's message names, if it is one of its own,
    else its usage page."""
    for m in re.finditer(r"https?://[^\s)\]>\"']+", fold(text)):
        url = m.group(0).rstrip(".,;:!?")
        host = (re.match(r"https?://([^/:?#]+)", url) or [None, ""])[1].lower()
        if any(host == h or host.endswith("." + h) for h in _RAISE_HOSTS):
            return url
    return RAISE_PAGES.get(provider, "")


# -- the words --
#
# Every line the machine says about resting is here, in the same words in every voice (under 100
# characters, no em-dash). They are proposals the setup voice thread may move into its own file.

KIND_WORDS = {"five_hour": "limit", "seven_day": "weekly limit", "seven_day_opus": "Opus limit",
              "seven_day_sonnet": "Sonnet limit", "seven_day_overage_included": "Fable limit",
              "overage": "spending limit"}
STILL_WORK = "Your apps and files still work."


def _limit_word(why: str, kind: str | None) -> str:
    return "spending limit" if why == "spend" else KIND_WORDS.get(kind or "", "limit")


def words(rest: Rest, title: str, at: float | None = None, tz: tzinfo | None = None) -> dict:
    """What the pill says while the provider rests: the line above it, the empty field's text, the
    label on a waiting chip (short), the note an app's button shows and the AI card's row."""
    if rest.why == "hand":
        return {"line": f"{title} is paused. {STILL_WORK}",
                "hint": f"Open or find anything. Asks wait until you resume {title}.",
                "wait": "paused", "note": "Paused", "row": "paused", "when": None}
    word = _limit_word(rest.why, rest.kind)
    if rest.until is None:
        return {"line": f"{title} is at its {word}. {STILL_WORK}",
                "hint": f"Open or find anything. Asks wait for {title}.", "wait": "limit",
                "note": "At its spending limit" if rest.why == "spend" else "At its limit",
                "row": f"at its {word}", "when": None}
    long, short = when(rest.until, at, tz), when(rest.until, at, tz, short=True)
    return {"line": f"{title} is at its {word} until {long}. {STILL_WORK}",
            "hint": f"Open or find anything. Asks wait for {long}.", "wait": short, "note": f"At {short}",
            "row": f"at its {word} until {long}", "when": long}


def cut_off(title: str, found: Rest, touched: str = "", at: float | None = None, tz: tzinfo | None = None) -> str:
    """The closing line of a turn the limit stopped halfway. `touched` is "2 files" or "1 package and
    2 files" (what the turn changed before it was stopped), "" when it changed nothing."""
    word = "spending limit" if found.why == "spend" else "limit"
    after = f", after changing {touched}" if touched else ""
    if found.until is not None:
        return f"{title} hit its {word} partway{after}. It carries on at {when(found.until, at, tz)}."
    return f"{title} hit its {word} partway{after}. It carries on once the limit is {'raised' if found.why == 'spend' else 'lifted'}."


def kept(rest: Rest, title: str, found: bool, at: float | None = None, tz: tzinfo | None = None) -> str:
    """The line for an ask that has to wait while the AI rests, once the finder has looked: when it runs,
    then what on this computer the sentence nearly names (`found`: there is something to offer)."""
    if rest.why == "hand":
        said = f"Kept until you resume {title}."
    elif rest.until is None:
        said = f"Kept until {title} is back."
    else:
        said = f"Kept for {when(rest.until, at, tz)}."
    return said + (" Found on this computer:" if found else " Nothing on this computer matches.")


def _running(waiting: int) -> str:
    return "" if waiting <= 0 else " Running your " + ("waiting ask." if waiting == 1 else f"{waiting} waiting asks.")


def back(title: str, waiting: int = 0) -> str:
    return f"{title} is back.{_running(waiting)}"


def trying(title: str, waiting: int = 0) -> str:
    """After Try again: nothing is checked, the first waiting ask is the check."""
    return f"Trying {title} again.{_running(waiting)}"

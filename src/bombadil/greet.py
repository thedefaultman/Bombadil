"""The welcome line: when Bombadil speaks, what it says and what it leaves out.

build() is pure: no clock, no I/O, no randomness, and it never changes the ledger, so the same inputs
give the same line. It holds the grammar (opener, name, body), the ranking of facts, the ledger rule
and the 100 character budget. Every word lives in share/voice/lines.toml, so a voice, an invitation or
an empty place is a table edit. A line with nothing true to say is None, and None means silence.
"""

import contextlib
import json
import math
import os
import re
import string
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import paths
from .persona import DEFAULT_VOICE, VOICES, Persona, clean_name

LIMIT = 100
SHORT_AWAY = 30 * 60        # a return greeting needs at least this long a break
LONG_AWAY = 4 * 3600
NIGHT_AWAY = 2 * 3600       # a break that crosses midnight is "overnight" from this long
WEEK_AWAY = 3 * 86400
STANDING_DAYS = 7
KEEP_DAYS = 120
# Ranking order: what failed or was put back, what waits on you, what finished, what was stopped,
# what you made, updates, a promise that came true.
KINDS = ("putback", "failed", "waiting", "finished", "stopped", "made", "updates", "promise")
ALONE = ("putback", "failed")            # these lead the line with no hello and no name
AWAY = ("waiting", "finished", "stopped")  # these read as "While you were away: ..."
NIGHT_END = 5                            # 00:00 to 04:59 is night
BYE_NIGHT = 20                           # and a goodbye from 20:00 is a good night
UTC_LIKE = frozenset({
    "UTC", "Etc/UTC", "UCT", "Etc/UCT", "Universal", "Etc/Universal", "Zulu", "Etc/Zulu", "GMT", "Etc/GMT",
    "GMT0", "GMT+0", "GMT-0", "Etc/GMT0", "Etc/GMT+0", "Etc/GMT-0", "Greenwich", "Etc/Greenwich"})


@dataclass(frozen=True)
class Fact:
    key: str            # what it is about, e.g. "updates"; a fact is said once per key and value
    kind: str           # one of KINDS
    text: str           # a clause without the final period
    value: str = ""     # said again when this changes
    standing: bool = False  # said again after seven days even when unchanged


def _text(v) -> str:
    return v if isinstance(v, str) else ""


def num(x) -> float | None:
    """x as a float when it is a finite number (never a bool), else None. A whole number too big for a
    float is not one: it would raise OverflowError in isfinite and in float()."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return None
    try:
        x = float(x)
    except OverflowError:
        return None
    return x if math.isfinite(x) else None


@dataclass
class Ledger:
    said: dict[str, dict] = field(default_factory=dict)  # key -> {"value", "day"}
    setup_day: str = ""
    last_boot_day: str = ""
    last_return_at: float = 0.0

    @classmethod
    def from_dict(cls, d) -> "Ledger":
        if not isinstance(d, dict):
            return cls()
        said, at = d.get("said"), num(d.get("last_return_at"))
        said = {k: v for k, v in said.items() if isinstance(v, dict)} if isinstance(said, dict) else {}
        return cls(said, _text(d.get("setup_day")), _text(d.get("last_boot_day")), at or 0.0)

    def to_dict(self) -> dict:
        return {"said": self.said, "setup_day": self.setup_day, "last_boot_day": self.last_boot_day,
                "last_return_at": self.last_return_at}


@dataclass(frozen=True)
class Greeting:
    text: str
    facts: tuple[Fact, ...] = ()   # the facts the line says, which commit() records
    first: bool = False            # the hello right after the card, which stays longer
    moment: str = ""


class _Say(string.Formatter):
    """str.format plus "{n:one/many}": the form for one, else the other, with "#" for the number."""

    def format_field(self, value, format_spec):
        if "/" in format_spec:
            one, many = format_spec.split("/", 1)
            return (one if value == 1 else many).replace("#", str(value))
        return super().format_field(value, format_spec)


def say(template: str, **slots) -> str:
    return _Say().vformat(template, (), slots)


_loaded: dict[str, dict] = {}


def _table(path: str) -> dict:
    """The table at `path`, read once. A read that failed is not kept, so a file that was missing or half
    written when agentd started is read again at the next line and not missed until agentd restarts."""
    table = _loaded.get(path)
    if table is None:
        try:
            with open(path, "rb") as f:
                table = tomllib.load(f)
        except (OSError, ValueError, RecursionError):
            return {}
        if table:
            _loaded[path] = table
    return table


def lines() -> dict:
    """The parsed share/voice/lines.toml, cached once it has been read. Empty when the file is missing or
    broken, and every function here then says nothing."""
    return _table(str(paths.voice_lines()))


def phrase(key: str, **slots) -> str:
    """A clause of the [fact] table, for the readers in greet_sources. "" when it cannot be said."""
    try:
        return say(lines()["fact"][key], **slots)
    except (KeyError, IndexError, ValueError):
        return ""


def join_names(items) -> str:
    """"Passwords and Tracker", "A, B and C"."""
    items = [i for i in items if i]
    join = lines().get("join", {})
    if len(items) < 2 or "list" not in join or "names" not in join:
        return "".join(items[:1])
    return join["list"].join(items[:-1]) + join["names"] + items[-1]


def empty(key: str, **slots) -> str | None:
    """The line of an empty place, or None when the table has none or a slot is missing. Where the table
    has a "<key>_live" line, it is the one on a live USB."""
    table = lines().get("empty", {})
    template = table.get(f"{key}_live") if is_live() else None
    template = template or table.get(key)
    try:
        return say(template, **slots) if template else None
    except (KeyError, IndexError, ValueError):
        return None


def empty_chip(key: str, **slots) -> str | None:
    """The label of the chip that is the one doorway out of an empty place, if it has one."""
    template = lines().get("empty_chip", {}).get(key)
    try:
        return say(template, **slots) if template else None
    except (KeyError, IndexError, ValueError):
        return None


def invitation(day: int) -> str:
    """Merry's invitation for the nth day since setup: the walk on day 0 and every fourth day, the other
    seven in table order on the days between, so none of them comes back within nine days."""
    table = lines().get("invite", {})
    pool = table.get("pool") or [""]
    day = max(int(day), 0)
    if day % 4 == 0:
        return table.get("walk", "")
    return pool[(day - day // 4 - 1) % len(pool)]


def _dt(now) -> datetime:
    return now if isinstance(now, datetime) else datetime.fromtimestamp(now, UTC).astimezone()


def _day(d: datetime) -> str:
    return d.date().isoformat()


def days_since(ledger: Ledger, now) -> int:
    """Whole days since setup, 0 when the day is unknown."""
    try:
        return max((_dt(now).date() - date.fromisoformat(ledger.setup_day)).days, 0)
    except ValueError:
        return 0


def tz_set() -> bool:
    """Whether a time zone was chosen: /etc/localtime names a zone that is not UTC or GMT and opens, and
    the environment has no TZ of its own. Until then no line says "morning" or "night", because a wrong
    hour is worse than none: the C library runs on UTC when it cannot open the zone, and follows TZ and
    not /etc/localtime when there is one. Reading the zone again with tzset means the clock that is read
    next follows a zone set while agentd was running."""
    if hasattr(time, "tzset"):
        time.tzset()
    if "TZ" in os.environ:
        return False
    link = paths.localtime_link()
    try:
        target = os.readlink(link)
    except OSError:
        return False
    zone = target.rpartition("zoneinfo/")[2] if "zoneinfo/" in target else ""
    zone = zone.removeprefix("posix/").removeprefix("right/")  # the same zones, in other folders
    return zone != "" and zone not in UTC_LIKE and os.path.isfile(link)


def is_live() -> bool:
    env = os.environ.get("BOMBADIL_LIVE")
    if env in ("0", "1"):
        return env == "1"
    return Path("/run/archiso").exists()


def _fresh(ledger: Ledger, fact: Fact, today: date) -> bool:
    said = ledger.said.get(fact.key)
    if not isinstance(said, dict) or said.get("value") != fact.value:
        return True
    if fact.standing:
        try:
            return (today - date.fromisoformat(said.get("day", ""))).days >= STANDING_DAYS
        except (ValueError, TypeError):
            return True
    return False


def _sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!", "…")) else text + "."


def _tidy(text: str) -> str:
    """One full stop where a name that ends in one meets the template's; an ellipsis stays as it is."""
    return re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)


def _more_tail() -> re.Pattern | None:
    """The table's "and N more" at the end of a sentence, as a pattern."""
    table = lines()
    names, more = table.get("join", {}).get("names"), table.get("fact", {}).get("more")
    if not names or not more or "{n}" not in more:
        return None
    return re.compile(re.escape(names) + re.escape(more).replace(re.escape("{n}"), r"\d+") + r"(?=[.?!]?$)")


def shorten(text: str, limit: int = LIMIT) -> str:
    """`text` as a sentence of at most `limit` characters. A trailing "and N more" goes first; what is
    still too long is cut after a whole word and ends with an ellipsis in place of the full stop."""
    text = text.strip()
    if len(text) <= limit:
        return text
    tail = _more_tail()
    if tail:
        text = tail.sub("", text)
        if len(text) <= limit:
            return text
    text = text.rstrip(".?!")
    head = text[:limit - 1]
    if len(text) >= limit and text[limit - 1] != " ":
        at = head.rfind(" ")
        if at >= limit // 2:  # a cut that leaves half the line or less is not worth a word
            head = head[:at]
    return head.rstrip(" ,;:.-") + "…"


def _clause(fact: Fact, first: bool, clause: dict) -> str:
    text = fact.text.strip().rstrip(".")
    if fact.kind != "made" or "made" not in clause:
        return text
    text = say(clause["made"], list=text)
    return text if first else text[:1].lower() + text[1:]


def _body(facts: list[Fact], moment: str, table: dict) -> str:
    """The facts as one sentence. Things that happened while you were away share one prefix."""
    clause, join = table.get("clause", {}), table.get("join", {})
    parts, rest = [], facts
    if moment == "return" and facts[0].kind in AWAY and "away" in clause:
        n = 2 if len(facts) > 1 and facts[1].kind in AWAY else 1
        things = join.get("list", "").join(f.text.strip().rstrip(".") for f in facts[:n])
        parts.append(say(clause["away"], list=things))
        rest = facts[n:]
    for f in rest:
        parts.append(_clause(f, not parts, clause))
    return _sentence(join.get("pair", "").join(parts))


def _opener(ctx: str, section: dict, dt: datetime, zone: bool, new_day: bool) -> str:
    if section.get("opener"):
        return section["opener"]
    if ctx == "boot" and zone and new_day:
        return "morning" if dt.hour < 12 else "afternoon" if dt.hour < 18 else "evening"
    return {"first": "welcome", "boot": "welcome", "long": "back", "back": "back"}.get(ctx, "")


def build(moment: str, persona: Persona, now, *, facts=(), ledger: Ledger | None = None, tz_set: bool = True,
          away: float | None = None, day: int = 0) -> Greeting | None:
    """The line for a moment, or None for silence.

    moment: "first" (right after the setup card), "boot", "return" (away is the seconds since the user was
    last active) or "installed". At boot the time away comes from the ledger's last boot day when `away` is
    not given. `day` counts days since setup and picks the invitation. With greeting off, only what failed
    or was put back is still said."""
    table = lines()
    if moment not in ("first", "boot", "return", "installed"):
        return None
    voice = persona.voice if persona.voice in VOICES else DEFAULT_VOICE
    voice_table = table.get(voice)
    if not voice_table:
        return None
    dt = _dt(now)
    today = dt.date()
    if ledger is None:
        ledger = Ledger()
    name = clean_name(persona.name)
    n = f", {name}" if name else ""

    if moment == "installed":
        if not persona.greet or "installed" not in voice_table:
            return None
        return Greeting(_tidy(say(voice_table["installed"], n=n)), moment=moment)

    seen: set[str] = set()
    fresh = []
    for f in facts:
        if f.kind in KINDS and f.text.strip() and f.key not in seen and _fresh(ledger, f, today):
            seen.add(f.key)
            fresh.append(f)
    fresh.sort(key=lambda f: KINDS.index(f.kind))

    if moment == "boot" and away is None and ledger.last_boot_day:
        try:
            away = float((today - date.fromisoformat(ledger.last_boot_day)).days * 86400)
        except ValueError:
            pass
    if moment == "return" and (away is None or away < SHORT_AWAY):
        return None
    long = away is not None and away >= WEEK_AWAY
    if not long:  # what you made is news only after three days or more
        fresh = [f for f in fresh if f.kind != "made"]

    if fresh and fresh[0].kind in ALONE:
        line = shorten(_sentence(fresh[0].text.strip()))
        return Greeting(line, (fresh[0],), first=moment == "first", moment=moment)
    if not persona.greet:
        return None

    if moment == "first":
        ctx = "first"
    elif moment == "boot":
        ctx = "long" if long else "night" if tz_set and dt.hour < NIGHT_END else "boot"
    else:
        if not fresh:
            return None
        # overnight is a break that crosses midnight and is long enough to be a night: 35 minutes at 00:25 is
        # a coffee break and is said like one
        crossed = away >= NIGHT_AWAY and (dt - timedelta(seconds=away)).date() != today
        ctx = "long" if long else "back" if away >= LONG_AWAY or crossed else "away"
    section = voice_table.get(ctx)
    if not section:
        return None
    template = section.get("facts" if fresh else "none")
    if not template:
        return None
    opener = table.get("opener", {}).get(
        _opener(ctx, section, dt, tz_set, ledger.last_boot_day != today.isoformat()), "")
    invite = invitation(day)

    chosen = fresh[:2]
    tries = [(chosen, True), (chosen[:1], True), (chosen[:1], False)] if fresh else [([], True), ([], False)]
    candidates = []
    for used, named in tries:
        text = say(template, opener=opener, n=n if named else "", invite=invite,
                   body=_body(used, moment, table) if used else "")
        candidates.append((_tidy(text), used))
    if fresh:  # last resort: the bare fact, which is cut if it is still too long
        candidates.append((_body(chosen[:1], moment, table), chosen[:1]))
    text, used = next((c for c in candidates if len(c[0]) <= LIMIT), candidates[-1])
    return Greeting(shorten(text), tuple(used), first=moment == "first", moment=moment) if text else None


def goodbye(persona: Persona, now, *, stopping=(), tz_set: bool = True) -> str:
    """The start text of a shutdown. `stopping` are clauses like "batch stops at 14 of 20". "" means there is
    nothing to say and the caller keeps its own text. With greeting off the words are Plain's. Two sessions
    are both named, more are counted ("a, b and 2 more"), and the tail of a voice that has a plural one
    ("stops") says it in the plural."""
    table = lines()
    voice = persona.voice if persona.voice in VOICES else DEFAULT_VOICE
    if not persona.greet:
        voice = "plain"
    bye = table.get(voice, {}).get("bye")
    if not bye:
        return ""
    dt = _dt(now)
    word = bye.get("night" if tz_set and (dt.hour >= BYE_NIGHT or dt.hour < NIGHT_END) else "day", "")
    name = clean_name(persona.name)
    clauses = [s.strip().rstrip(".") for s in stopping if isinstance(s, str) and s.strip()]

    def said(n: int) -> str:  # the first n clauses, then a count of the rest
        return join_names([*clauses[:n], *([phrase("more", n=len(clauses) - n)] if len(clauses) > n else [])])

    tries = [(said(2), True), (said(1), True), (said(1), False), ("", True), ("", False)]
    text = ""
    for stop, named in tries:
        key = ("stops" if len(clauses) > 1 and "stops" in bye else "stop") if stop else "none"
        if key not in bye:
            continue
        text = _tidy(say(bye[key], bye=word, n=f", {name}" if name and named else "", stop=stop))
        if len(text) <= LIMIT:
            break
    return shorten(text) if text else text


def commit(ledger: Ledger, greeting: Greeting, now) -> None:
    """Record that a greeting was shown (or, with empty text, that a silent boot happened)."""
    dt = _dt(now)
    today = _day(dt)
    for f in greeting.facts:
        ledger.said[f.key] = {"value": f.value, "day": today}
    if greeting.moment in ("boot", "first"):
        ledger.last_boot_day = today
        if greeting.moment == "first" and not ledger.setup_day:
            ledger.setup_day = today
    elif greeting.moment == "return":
        ledger.last_return_at = dt.timestamp()
    elif greeting.moment == "installed":
        ledger.said["installed"] = {"value": "", "day": today}
    cutoff = (dt.date() - timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, v in ledger.said.items() if k != "installed" and str(v.get("day", "")) < cutoff]:
        del ledger.said[k]


def load_ledger() -> Ledger:
    try:
        return Ledger.from_dict(json.loads(paths.ledger_file().read_text(encoding="utf-8")))
    except (OSError, ValueError, OverflowError, RecursionError):
        return Ledger()


def save_ledger(ledger: Ledger) -> bool:
    path = paths.ledger_file()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(ledger.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True

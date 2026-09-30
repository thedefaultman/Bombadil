"""When a group of repeated asks is worth an offer, and how often he is allowed to be asked.

Pure functions over a group, the history of past offers and `now`; nothing here reads a clock or a
file but the optional config, so every rule can be tested with an injected time. The store
(`store.py`) keeps the history and calls these.

The rules, from the brief:
- ripe: 3 asks on 2 different days within 21 days, and worth building (together at least 45 s of
  turn time, or each ran 3 or more steps, or each was a near miss of a word that exists);
- the bar rises to 5 asks when fewer than 3 of the last 10 offers were taken;
- at most one new offer a day and three a week;
- Not now hides a group until its count doubles or 30 days pass; Never remembers it for good;
  three Nevers on one form stop offering that form;
- an offer unanswered for 14 days expires into Not now; two silent expiries or two Nevers in a
  row rest all offers for 30 days.

The numbers are guesses until four weeks of real offers have been counted. They are read from
`config.toml` under `[offers]` (a missing or broken file, or a value that makes no sense, leaves
the default), never from a model.
"""

import hashlib
import time
import tomllib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .. import paths
from . import forms
from .habits import Group, decayed, listed, sentence_of
from .ledger import day_of

DAY = 86400

# Outcomes of an offer. "" is an offer still waiting.
ACCEPT, NOT_NOW, NEVER, GOT_IT, EXPIRED = "accept", "not_now", "never", "got_it", "expired"
TAKEN = (ACCEPT, GOT_IT)


@dataclass(frozen=True)
class Config:
    asks: int = 3                  # asks needed in the window
    days: int = 2                  # on at least this many different days
    window_days: int = 21
    seconds: float = 45            # worth building: together this much turn time ...
    steps: int = 3                 # ... or each ran this many steps ...
    raised_asks: int = 5           # the bar when offers are mostly not taken
    take_window: int = 10          # how many past offers say whether they are taken
    take_min: int = 3              # fewer than this many of them taken raises the bar
    per_day: int = 1
    per_week: int = 3
    not_now_days: int = 30
    expire_days: int = 14
    rest_days: int = 30
    rest_after: int = 2            # silent expiries, or Nevers, in a row
    never_forms: int = 3           # Nevers on one form that stop offering it


def load_config(path: Path | None = None) -> Config:
    """The numbers in `[offers]` of the loop's config.toml. A missing or broken file, and any value
    that is not a positive number of the right kind, leave the default."""
    path = path or paths.loop_dir() / "config.toml"
    try:
        raw = tomllib.loads(path.read_text()).get("offers", {})
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for f in fields(Config):
        v = raw.get(f.name)
        want = float if f.type is float else int
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 or (want is int and v != int(v)):
            continue
        out[f.name] = want(v)
    return Config(**out)


def config_hash(cfg: Config) -> str:
    return hashlib.sha1(repr(sorted(asdict(cfg).items())).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Past:
    """One offer that was shown: when, what it was, how it ended."""
    shown_t: float
    outcome: str = ""              # "" while it waits
    answered_t: float | None = None
    form: str = ""                 # the letter of the form offered


# -- ripeness --

def asks_needed(history: Sequence[Past], cfg: Config) -> int:
    """3 asks, or 5 when fewer than 3 of the last 10 offers were taken. With fewer than ten answered
    offers there is nothing to judge by, and the bar stays."""
    closed = sorted((h for h in history if h.outcome), key=lambda h: h.shown_t)[-cfg.take_window:]
    if len(closed) >= cfg.take_window and sum(1 for h in closed if h.outcome in TAKEN) < cfg.take_min:
        return cfg.raised_asks
    return cfg.asks


def ripe(g: Group, now: float, cfg: Config, need: int | None = None) -> tuple[bool, str]:
    """(is this group worth an offer now, why not). `need` is the number of asks the bar asks for."""
    need = cfg.asks if need is None else need
    cutoff = now - cfg.window_days * DAY
    inside = [i for i, t in enumerate(g.times) if cutoff <= t <= now]
    if len(inside) < need:
        return False, "too few asks"
    if len({day_of(g.times[i]) for i in inside}) < cfg.days:
        return False, "all on one day"
    seconds = sum(g.seconds[i] for i in inside if i < len(g.seconds))
    each_steps = all(g.steps[i] >= cfg.steps for i in inside if i < len(g.steps)) and bool(g.steps)
    if not (seconds >= cfg.seconds or each_steps or g.near_miss):
        return False, "not worth building"
    return True, ""


def cadence_ok(history: Sequence[Past], now: float, cfg: Config) -> tuple[bool, str]:
    """At most one new offer in a day and three in a week, counted back from `now`."""
    if sum(1 for h in history if 0 <= now - h.shown_t < DAY) >= cfg.per_day:
        return False, "one a day"
    if sum(1 for h in history if 0 <= now - h.shown_t < 7 * DAY) >= cfg.per_week:
        return False, "three a week"
    return True, ""


def resting_until(history: Sequence[Past], now: float, cfg: Config) -> float:
    """When all offers are resting until, or 0 when they are not: two silent expiries, or two Nevers,
    in a row, rest them for 30 days. Anything else between them breaks the row."""
    events = sorted((h.answered_t if h.answered_t is not None else h.shown_t, h.outcome)
                    for h in history if h.outcome)
    until, kind, count = 0.0, "", 0
    for t, outcome in events:
        if outcome in (EXPIRED, NEVER):
            kind, count = (outcome, count + 1) if outcome == kind else (outcome, 1)
            if count >= cfg.rest_after:
                until, kind, count = t + cfg.rest_days * DAY, "", 0
        else:
            kind, count = "", 0
    return until if until > now else 0.0


def rest_line(until: float) -> str:
    """"Resting offers until 3 Nov": the one line the card says while offers rest."""
    lt = time.localtime(until)
    return f"Resting offers until {lt.tm_mday} {time.strftime('%b', lt)}"


def released(n_then: int, t_then: float, n_now: int, now: float, cfg: Config) -> bool:
    """Is a Not now over: the count doubled since, or 30 days passed."""
    return n_now >= 2 * max(n_then, 1) or now - t_then >= cfg.not_now_days * DAY


def expired(shown_t: float, now: float, cfg: Config) -> bool:
    """An offer unanswered this long (14 days) expires into Not now."""
    return now - shown_t >= cfg.expire_days * DAY


def stopped_forms(never_forms: Iterable[str], cfg: Config) -> set[str]:
    """The letters of forms he said Never to at least three times: no longer offered."""
    counts = Counter(forms.get(f).letter for f in never_forms if f)
    return {letter for letter, n in counts.items() if n >= cfg.never_forms}


def next_offer(groups: Iterable[Group], history: Sequence[Past], now: float, cfg: Config, *,
               built: Iterable[str] | None = None, stopped: Iterable[str] = (),
               titles: dict[str, str] | None = None) -> tuple[Group, forms.Recommendation] | None:
    """The one new offer to show at `now`, or None: resting, over the cadence, or nothing ripe. Of the
    ripe groups the one with the heaviest (most recent, most repeated) asks comes first. Groups should
    be those counting, not hidden by Not now or said no to."""
    if resting_until(history, now, cfg) or not cadence_ok(history, now, cfg)[0]:
        return None
    need = asks_needed(history, cfg)
    ranked = sorted((g for g in groups if listed(g, now) and ripe(g, now, cfg, need)[0]),
                    key=lambda g: (-decayed(g.times, now), g.first))
    for g in ranked:
        rec = forms.recommend(g, built, stopped, titles)
        if rec is not None:
            return g, rec
    return None


# -- the row --

BUTTONS = {"A": "Make the word", "B": "Make the card", "C": "Make the widget", "D": "Make the app",
           "E": "Start the routine", "F": "Watch for it", "G": "Remember it"}


@dataclass
class Offer:
    """An offer showing now: the group, what is offered for it, and when it was first shown."""
    id: int
    group: Group
    rec: forms.Recommendation
    shown_t: float

    def to_row(self, titles: dict[str, str] | None = None) -> dict:
        """The Noticed row for it, as docs/LOOP.md describes: his own words, the count and span, what
        would happen, one button of at most three words, and the quiet ways around it."""
        g, rec = self.group, self.rec
        days = len(g.days)
        said = g.sentences[0] if g.sentences else g.label
        label = "Got it" if rec.op == "got_it" else BUTTONS.get(rec.form.letter, "Make it")
        others = [{"label": "Not now", "op": NOT_NOW}, {"label": "Never", "op": NEVER}]
        if rec.others:
            others.insert(0, {"label": "Other ways", "op": "other_ways"})
        return {
            "id": g.id, "offer": self.id, "kind": "offer", "title": sentence_of(said),
            "meta": f"{g.n} times on {days} day{'s' if days != 1 else ''}", "what": rec.sentence,
            "primary": {"label": label, "op": rec.op, "form": rec.form.id},
            "others": others,
            "forms": [{"form": rec.form.id, "label": rec.form.name, "recommended": True},
                      *({"form": f.id, "label": f.name} for f in rec.others)],
        }

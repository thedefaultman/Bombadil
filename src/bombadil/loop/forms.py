"""What a repeated request can become: nine forms, the rule that picks one, and what to say about it.

The forms are data (the brief's table, A to I). Only the ones whose builder exists on this machine
are offered: today a word (a row in words.toml, no model) and a new app (an ordinary turn). The rest
say which piece they wait for. `recommend()` runs the brief's seven tests in order, the first that
matches wins; if what it picks cannot be built here, the next form that fits and can be built is
taken, ties going to the smallest new surface and the cheapest undo: A, G, B, F, E, C, D. Two
forms, the kit part (H) and the fix in Bombadil (I), are never offered: they are done quietly.

Everything is a function of the group and needs no model.
"""

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .. import launcher
from . import route as routes
from .habits import Group

# What exists on this machine to build with. Others add to it as their pieces land.
BUILT = frozenset({"words", "create_app"})

TIE_ORDER = ("A", "G", "B", "F", "E", "C", "D")

_SHARE = 0.6        # "the asks were ..." means at least this share of them
_CLOCK_WINDOW = 45  # minutes either side of one time of day
_WEEKDAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


@dataclass(frozen=True)
class Form:
    letter: str
    id: str
    name: str                # "A word"
    fits: str                # what it fits, in plain words
    template: str            # the sentence for a group; {slots} are filled by `describe`
    needs: str = ""          # the piece that builds it
    waits_for: str = ""      # that piece, as a person would name it
    offered: bool = True     # H and I are done quietly and never offered


FORMS: tuple[Form, ...] = (
    Form("A", "word", "A word", "every ask ended opening or showing the same thing that exists",
         "Say “{word}” and {thing} opens. No model, under a tenth of a second.", needs="words"),
    Form("B", "card", "A card on a word", "a read-only answer from the machine, slow, with no state",
         "Say “{word}” and a card answers, read fresh from the machine each time. No model.",
         needs="answer_cards", waits_for="answer cards"),
    Form("C", "widget", "A widget", "an answer that changes within the hour, asked again and again",
         "Keep “{label}” on the desk while it matters, updating by itself.",
         needs="agent_widgets", waits_for="widgets the agent makes"),
    Form("D", "app", "An app", "asks that add to or change one set of data",
         "Make a small app for “{label}”: one place to add to it and look it up, in one turn you can undo.",
         needs="create_app"),
    Form("E", "routine", "A routine", "the same ask near the same time on 3 days, and it changes something",
         "Do “{sentence}” for you {when}. You can stop it from Autopilot.",
         needs="autopilot", waits_for="Autopilot"),
    Form("F", "watcher", "A watcher", "asks that poll for an event",
         "Tell you through the pill when “{label}” is done, so you can stop asking.",
         needs="autopilot", waits_for="Autopilot"),
    Form("G", "preference", "A standing preference", "the same correction of style after answers or builds",
         "Remember “{sentence}” so answers follow it without being told.",
         needs="remember", waits_for="remember"),
    Form("H", "kit_part", "A kit part", "the machine drew the same piece in 3 of his apps",
         "Keep the piece you keep drawing by hand as a part every app can use.",
         needs="kit_overlay", waits_for="the kit overlay", offered=False),
    Form("I", "fix", "A fix in Bombadil", "the repeats are workarounds of a bug",
         "Look into why “{label}” keeps coming back.", needs="findings", waits_for="findings", offered=False),
)
_BY_KEY = {k: f for f in FORMS for k in (f.letter, f.id)}


def get(form: str) -> Form:
    """A form by its letter ("A") or its id ("word")."""
    return _BY_KEY[str(form)]


def available(form: str, built: Iterable[str] | None = None) -> tuple[bool, str]:
    """(can it be built on this machine, why not). The reason names the piece it waits for."""
    f = get(form)
    if not f.offered:
        return False, "it is done quietly, never offered"
    if f.needs in (BUILT if built is None else built):
        return True, ""
    return False, f"waits for {f.waits_for or f.needs}"


# -- what a group is like --

def _share(count: int, n: int) -> float:
    return count / n if n else 0.0


def _verb_share(g: Group, *verbs: str) -> float:
    return _share(sum(g.verbs.get(v, 0) for v in verbs), g.n)


def holder(g: Group) -> str:
    """The app that already holds what the asks work on, from what their turns touched."""
    return next((t for t in g.routes if t.startswith("app:")), "")


def _data_topics(g: Group) -> bool:
    return any(t.startswith(("files:", "app:")) for t in g.routes)


def _reads(g: Group) -> bool:
    return _share(g.changed, g.n) < 0.5 and _verb_share(g, "ask", "find") >= 0.5 and bool(g.routes)


def clock(g: Group) -> tuple[int, str] | None:
    """(minutes after midnight, "Sundays" or "") when the asks came near one time of day on at least
    3 different days; None otherwise."""
    by_day: dict[str, tuple[int, int]] = {}
    for t in sorted(g.times):
        lt = time.localtime(t)
        by_day.setdefault(time.strftime("%Y-%m-%d", lt), (lt.tm_hour * 60 + lt.tm_min, lt.tm_wday))
    if len(by_day) < 3:
        return None
    minutes = sorted(m for m, _ in by_day.values())
    for centre in minutes:
        near = [(m, wd) for m, wd in by_day.values() if min(abs(m - centre), 1440 - abs(m - centre)) <= _CLOCK_WINDOW]
        if len(near) >= 3:
            at = sorted(m for m, _ in near)[len(near) // 2]
            days = {wd for _, wd in near}
            return at, _WEEKDAYS[days.pop()] if len(days) == 1 else ""
    return None


def _fits(letter: str, g: Group) -> bool:
    n = g.n
    if letter == "A":
        return bool(g.opens)
    if letter == "G":
        return _share(g.style, n) >= _SHARE
    if letter == "B":
        return _reads(g)
    if letter == "F":
        return _verb_share(g, "tell-me-when") >= _SHARE
    if letter == "E":
        return clock(g) is not None and _share(g.changed, n) >= _SHARE
    if letter == "C":
        return _reads(g) and routes.is_live(g.routes[:2])
    if letter == "D":
        return _verb_share(g, "log", "make", "change") >= 0.5 and _share(g.changed, n) >= 0.5 and _data_topics(g)
    return False


def fits(g: Group) -> list[str]:
    """The letters of every form that fits this group, smallest new surface first."""
    return [letter for letter in TIE_ORDER if _fits(letter, g)]


def ideal(g: Group) -> str:
    """The form the brief's rule picks, whether or not it can be built here: the first of seven
    tests that matches. "I" means the asks were retries: not offered, it becomes a finding. ""
    when no test matches."""
    n = max(g.n, 1)
    if g.friction >= 2 and g.friction * 2 >= n:
        return "I"
    for letter in ("G", "A", "F", "E"):
        if _fits(letter, g):
            return letter
    if _reads(g):
        return "C" if routes.is_live(g.routes[:2]) else "B"
    return "D" if _fits("D", g) else ""


# -- choosing --

def _usable(letter: str, g: Group, built: Iterable[str] | None, stopped: set[str]) -> bool:
    if letter in stopped:
        return False
    if letter == "A" and not g.word:
        return False    # asks too long or too loose to say as a phrase: there is no word to make
    if letter == "D":
        # Extending the app that holds the data needs per-app git; a second app beside it would not do.
        needs = "per_app_git" if holder(g) else "create_app"
        return needs in (BUILT if built is None else built)
    return available(letter, built)[0]


def _letters(forms: Iterable[str]) -> set[str]:
    return {get(f).letter for f in forms or ()}


@dataclass
class Recommendation:
    form: Form
    ideal: str                   # the letter the rule picked, which may not be buildable here
    others: list[Form] = field(default_factory=list)   # at most two more that fit and can be built
    already: bool = False        # the thing already has a word: the offer only says so
    op: str = "accept"           # what its button does: "accept", or "got_it" when it only says so
    sentence: str = ""           # what would happen, in plain words


def recommend(g: Group, built: Iterable[str] | None = None, stopped: Iterable[str] = (),
              titles: dict[str, str] | None = None) -> Recommendation | None:
    """The one form to offer for a group, with up to two others, or None: nothing fits, what fits
    cannot be built here, or the asks were retries (that is a finding, not an offer). `stopped` are
    the forms he said Never to three times."""
    stop = _letters(stopped)
    letter = ideal(g)
    if letter == "I":
        return None
    fit = fits(g)
    pick = letter if letter and _usable(letter, g, built, stop) else next(
        (x for x in fit if _usable(x, g, built, stop)), "")
    if not pick:
        return None
    form = get(pick)
    others = other_ways(g, pick, built, stop)
    if pick == "A" and g.existing:
        said = f"{thing_title(g.opens or (g.named[0] if g.named else ''), titles)} already opens with the word {g.existing}."
        return Recommendation(form, letter, others, True, "got_it", said[:1].upper() + said[1:])
    return Recommendation(form, letter, others, False, "accept", describe(g, form.letter, titles))


def other_ways(g: Group, pick: str, built: Iterable[str] | None = None,
               stopped: Iterable[str] = ()) -> list[Form]:
    """Up to two more forms that fit this group and can be built here, besides `pick`."""
    stop = _letters(stopped)
    chosen = get(pick).letter if pick else ""
    return [get(x) for x in fits(g) if x != chosen and _usable(x, g, built, stop)][:2]


# -- saying it --

def thing_title(thing: str, titles: dict[str, str] | None = None) -> str:
    """What the line calls a thing: "Passwords" for app:passwords, "the browser" for panel:browser."""
    if titles and thing in titles:
        return titles[thing]
    kind, _, name = str(thing).partition(":")
    if kind == "panel" and name in launcher.PANEL_TITLES:
        return launcher.PANEL_TITLES[name]
    return name.replace("-", " ").title() if name else "it"


def _when(g: Group) -> str:
    c = clock(g)
    if c is None:
        return "when you would ask"
    at = f"{c[0] // 60:02d}:{c[0] % 60:02d}"
    return f"every {c[1][:-1]} at {at}" if c[1] else f"around {at}"


def describe(g: Group, letter: str, titles: dict[str, str] | None = None) -> str:
    """The plain sentence for what this form would do for this group, no model: for a word,
    Say “my passwords” and Passwords opens. No model, under a tenth of a second."""
    f = get(letter)
    label = g.label or (g.sentences[0] if g.sentences else "this")
    return f.template.format(
        word=g.word or label, label=label, sentence=g.sentences[0] if g.sentences else label,
        thing=thing_title(g.opens or (g.named[0] if g.named else ""), titles), when=_when(g))


def preview(g: Group, letter: str, titles: dict[str, str] | None = None) -> str:
    """"Show me": a typed preview filled from his own counts and a real sentence of his, no model."""
    f = get(letter)
    said = g.sentences[0] if g.sentences else g.label
    days = len(g.days)
    lines = [f"You said: “{said}”", f"{g.n} times on {days} day{'s' if days != 1 else ''}.",
             f"{f.name}: {describe(g, letter, titles)}", "Nothing else changes, and Undo puts it back."]
    return "\n".join(lines)

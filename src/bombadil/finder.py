"""The finder: things on the computer that match a sentence the pill cannot answer right now.

While the AI rests (rest.py) a sentence that is not a launcher word waits as a chip. This is what makes
that more than a dead end: it looks, on this computer only, for the app, the launcher word or the past
ask the sentence nearly names, and offers up to three of them as chips. It is pure: no model, no network,
nothing opens or runs by itself. The machine shows, the user presses.

Matching is by words: a word of the sentence matches a word of a thing when they are the same, or one starts
the other (three letters or more, a trailing "s" ignored). A thing's score is how much of the sentence it
covers, with its title counting double. The words that carry no meaning ("the", "my", "app", ...) are left
out of the sentence, and a sentence with none left finds nothing.

    find("my password app")      -> [Match(kind="app", label="Passwords", say="passwords", ...)]
    find("the march invoice")    -> [Match(kind="ask", label="You asked: file the March invoice (3 Sep)", ...)]

Where things come from today: the apps (title and the description the agent wrote), the launcher's panels,
widgets and the commands that only show something, and the successful asks in turns.jsonl. Later sources (the user's own words, the
Brain's files and pages) join through `things()`.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from . import launcher, paths

LIMIT = 3           # chips shown
MIN_SCORE = 0.25    # what a thing must cover of the sentence, at least
PAST_ASKS = 400     # how far back in turns.jsonl it looks
LABEL = 60          # a chip's label is cut to this many characters

# Words that carry no meaning of their own in a request. "app" and its kin are here because every
# app is one, and "open" because every request to open something starts with it.
STOP = frozenset("""a an the my me i we you your our to of for and or in on at it its is are was be do does did
this that these those with from by as can could would should will please just some any all how what where when
which who why about into up out open show get make give want need find look there here then so if not no""".split()
                 + ["app", "apps", "application", "program", "thing", "stuff", "one"])
_WORD = re.compile(r"[a-z0-9]+")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Thing:
    """Something that can be found: what it says, the words it is found by and how to open it."""
    kind: str                 # app | word | ask
    label: str                # the chip
    hint: str                 # a few words about it
    title: str                # the words that count double
    words: str                # all the words it is found by
    say: str = ""             # the launcher word that opens it (app, word)
    path: str = ""            # the details file of a past ask
    t: float = 0.0            # when it was asked (ask)


@dataclass(frozen=True)
class Match:
    kind: str
    label: str
    hint: str
    say: str
    path: str
    score: float


def tokens(text: str) -> list[str]:
    """The words of a sentence that mean something, lower case, in order, without repeats."""
    seen: list[str] = []
    for w in _WORD.findall(str(text).lower()):
        if w not in STOP and w not in seen:
            seen.append(w)
    return seen


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _one_off(a: str, b: str) -> bool:
    """One letter wrong, missing or extra ("passwrd", "pasword"), or two letters swapped ("pasword" for "passowrd")."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long = sorted((a, b), key=len)
    return any(short == long[:i] + long[i + 1:] for i in range(len(long)))


def _hit(q: str, t: str) -> float:
    """How well one word of the sentence matches one word of a thing: 1 the same, 0.6 one starts the
    other, 0.5 one slip of the fingers away (words of five letters or more)."""
    q, t = _stem(q), _stem(t)
    if q == t:
        return 1.0
    short, long = sorted((q, t), key=len)
    if len(short) >= 3 and long.startswith(short):
        return 0.6
    return 0.5 if len(short) >= 5 and _one_off(q, t) else 0.0


def score(query: list[str], thing: Thing) -> float:
    """How much of the sentence the thing covers: each word of it counts by its best match, one in
    the title twice as much, and the total is shared over the words of the sentence."""
    if not query:
        return 0.0
    title, rest_ = tokens(thing.title), tokens(thing.words)
    total = 0.0
    for q in query:
        best = max((_hit(q, t) for t in title), default=0.0)
        best = max(best, 0.5 * max((_hit(q, t) for t in rest_), default=0.0)) if best < 1.0 else best
        total += min(1.0, best)
    # A thing found by one word of a long sentence is a weak find: the share is of the whole sentence.
    return total / len(query)


def rank(text: str, things: list[Thing], limit: int = LIMIT) -> list[Match]:
    """The things that match the sentence best, best first (apps before words before asks on a tie, and
    newer asks first), at most `limit`, each once."""
    query = tokens(text)
    order = {"app": 0, "word": 1, "ask": 2}
    scored = []
    for thing in things:
        s = score(query, thing)
        if s >= MIN_SCORE:
            scored.append((-s, order.get(thing.kind, 3), -thing.t, thing))
    scored.sort(key=lambda row: row[:3])
    out, seen = [], set()
    for neg, _o, _t, thing in scored:
        key = (thing.kind, thing.say or thing.title.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(Match(thing.kind, thing.label, thing.hint, thing.say, thing.path, round(-neg, 2)))
        if len(out) == limit:
            break
    return out


# -- where things come from --

def _app_things(app_list: list) -> list[Thing]:
    return [Thing("app", str(a.title), "App", str(a.title), f"{a.title} {a.name} {a.description}", say=a.name)
            for a in app_list]


# Commands that only show something: a chip may open these. The others (undo, hide, sign in, ...) change
# something, so a sentence that nearly names one is not offered it.
SHOWS = frozenset({"history", "desk", "brain", "wifi", "sound", "brightness", "battery"})


def _opener(words: list, kinds) -> str:
    """The phrase that opens a panel, widget or shows a command's answer: its word as it stands ("browser")
    or with "open" in front ("open machine", for a widget, whose bare word is a question's answer). ""
    when the launcher knows none."""
    for word in map(str, words):
        for phrase in (word, f"open {word}"):
            action = launcher.match(phrase, [])
            if action is not None and action.kind in kinds:
                return phrase
    return ""


def _word_things(entries: list[dict]) -> list[Thing]:
    """Panels, widgets and the commands that only show something, found by the names the launcher knows them
    by. A chip opens things; it never runs a command that changes anything (undo, restart, sign in)."""
    out = []
    for e in entries:
        kind = e.get("kind")
        if not e.get("words") or kind not in ("panel", "widget", "command"):
            continue
        if kind == "command" and e.get("name") not in SHOWS:
            continue
        say = _opener(e["words"], {"panel", "widget"} if kind != "command" else {e["name"]})
        if say:
            out.append(Thing("word", str(e["title"]), "Opens it", str(e["title"]), " ".join(map(str, e["words"])),
                             say=say))
    return out


def day(t: float, at: float | None = None, tz: tzinfo | None = None) -> str:
    """When a past ask was made: "today", "yesterday", "3 Sep", "3 Sep 2025" (never a clock: it is not news)."""
    zone = tz or datetime.now().astimezone().tzinfo
    then = datetime.fromtimestamp(t, zone)
    now = datetime.fromtimestamp(time.time() if at is None else at, zone)
    days = (now.date() - then.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{then.day} {_MONTHS[then.month - 1]}" + (f" {then.year}" if then.year != now.year else "")


def _cut(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def read_asks(path: Path | None = None, limit: int = PAST_ASKS) -> list[dict]:
    """The latest successful asks of the user, newest last, from turns.jsonl. Not what an app or a coding
    session asked, not a "!" command, not a launcher word, not a turn that was stopped, failed or is waiting
    for the limit to lift."""
    path = path or paths.turns_log()
    try:
        lines = path.read_text().splitlines()[-limit * 3:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        prompt = str(e.get("prompt") or "").strip() if isinstance(e, dict) else ""
        if (not prompt or e.get("kind") or e.get("stopped") or e.get("requeued") or e.get("ok") is not True
                or prompt.startswith(("!", "[from app"))):
            continue
        out.append(e)
    return out[-limit:]


def _ask_things(asks: list[dict], at: float | None = None, tz: tzinfo | None = None) -> list[Thing]:
    out = []
    for e in asks:
        prompt = " ".join(str(e["prompt"]).split())
        when = float(e.get("t") or 0.0)
        tail = f" ({day(when, at, tz)})" if when else ""
        label = f"You asked: {_cut(prompt, LABEL - len('You asked: ') - len(tail))}{tail}"
        out.append(Thing("ask", label, "Its steps", prompt, f"{prompt} {e.get('summary') or ''}",
                         path=str(e.get("details") or ""), t=when))
    return out


def things(app_list: list | None = None, entries: list[dict] | None = None, asks: list[dict] | None = None,
           at: float | None = None, tz: tzinfo | None = None) -> list[Thing]:
    """Everything on this computer a sentence can be found against. Arguments stand in for what is read
    from disk (the tests give their own)."""
    app_list = launcher.known_apps() if app_list is None else app_list
    entries = launcher.entries(app_list) if entries is None else entries
    asks = read_asks() if asks is None else asks
    return _app_things(app_list) + _word_things(entries) + _ask_things(asks, at, tz)


def find(text: str, limit: int = LIMIT, **sources) -> list[Match]:
    """Up to `limit` things on this computer that the sentence nearly names. Never asks a model."""
    return rank(text, things(**sources), limit)

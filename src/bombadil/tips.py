"""Tips: what a new person can ask, one line each, and which of it this machine can back right now.

The tips are rows in share/tips/tips.toml. This module loads and checks them, decides which the machine can
back (`Facts`, `backed`), fills {app}, {project} and {ai} from the machine, keeps what the person has been
taught in ~/.local/state/bombadil/tips.json (`State`), and builds the two things agentd says about them: the
card's `tips` message and the list the Tips app asks for. docs/TIPS.md is the whole design.

Nothing here talks to a model, a socket or the shell. Every file it reads and every clock is a parameter, so
the tests hand it text and a date, as they do vitals.py and desk.py. agentd is the only writer of tips.json
and the only caller of the methods that change it; none of them raises on a file it cannot make sense of.

Tried. A tip is tried when agentd sees, with no model, that the person did it: the words its button put in the
pill are sent as they were, or what agentd saw matches one of the tip's `tried_when` entries. Showing a tip
never counts. The entries are:

    "words"                      the words sent are the tip's own words (any way they got into the pill).
                                 Words ending in "…" stand for the start of an ask: more after them still counts.
    "local:<kind>[:<target>]"    a launcher action of that kind ran well ("local:picture:disks", "local:undo").
                                 Closing or hiding an app, hiding a widget and resuming the AI do not count.
    "tool:<name>"                the turn's tool call of that name worked ("tool:create_app" is the os-mcp
                                 tool whatever its prefix); `local_keys`, `tool_keys` and `job_keys` build them.
    "job:start[:<kind>]"         a background job of that kind (job, watch, timer) was started
    "bang"                       a "!" command was typed
"""

import json
import os
import re
import sys
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from importlib.util import find_spec
from pathlib import Path

from . import paths
from .apps import NAME_RE as APP_NAME
from .apps import read_meta

# The sections, in the order the app lists them and the card turns through them.
SECTIONS = {"start": "Start here", "work": "Work", "code": "Code", "machine": "This machine"}
# What a tip may need. Each is one thing the machine either can back or cannot, see `Facts`.
GATES = ("model", "installed", "app", "project", "sessions", "mail", "noticed", "keep", "desk-seen")
PLACEHOLDERS = ("ai", "app", "project")
JOB_KINDS = ("job", "watch", "timer")
WEEK_DAYS = 7              # days on which the person typed in the pill; Tips steps aside after this many
START = "start"            # the section a new person is taught from, in the card's first week
MAX_APP = 18               # an app's name is put in a button: a longer one is left out, not cut
MAX_PROJECT = 24
MAX_TRIED = 400            # what tips.json keeps (retired ids stay, so a reused id could never look tried)
MAX_DAYS = 30
MAX_SUMMONED = 24          # button presses remembered while waiting for Enter

ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EVIDENCE = re.compile(r"words|bang|local:[a-z][a-z0-9_-]*(?::[A-Za-z0-9_.:@+-]+)?|tool:[A-Za-z0-9_-]+"
                       r"|job:start(?::(?:" + "|".join(JOB_KINDS) + "))?")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
# What goes into a button's words from the machine: it starts with a letter or a digit (never "!" or "~",
# which would turn a name into a shell command or a path) and holds nothing a shell or a pill reads as more.
_TITLE = re.compile(r"[^\W_][\w .'’-]*")
_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


# -- the rows --

@dataclass(frozen=True)
class Tip:
    id: str
    section: str
    order: int
    line: str
    words: str = ""
    marks: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    card: bool = True
    tried_when: tuple[str, ...] = ()
    captions: tuple[str, ...] = ()
    beats: tuple[dict, ...] = field(default=(), compare=False)

    def as_dict(self) -> dict:
        """The whole row, as the card and the app are sent it."""
        return {"id": self.id, "section": self.section, "order": self.order, "line": self.line,
                "words": self.words, "marks": list(self.marks), "needs": list(self.needs), "card": self.card,
                "tried_when": list(self.tried_when), "captions": list(self.captions),
                "beats": [dict(b) for b in self.beats]}


def _strings(value) -> list[str] | None:
    return list(value) if isinstance(value, list) and all(isinstance(v, str) for v in value) else None


def parse_row(raw) -> tuple[Tip | None, list[str]]:
    """One [[tip]] table as a row, or None and what is wrong with it. A row that is not one is left out of the
    table, never half-used."""
    if not isinstance(raw, dict):
        return None, ["a tip is not a table"]
    name = raw.get("id") if isinstance(raw.get("id"), str) else "?"
    bad: list[str] = []

    def need(ok: bool, what: str):
        if not ok:
            bad.append(f"{name}: {what}")
    need(isinstance(raw.get("id"), str) and bool(ID_RE.fullmatch(raw["id"])) and len(raw["id"]) <= 40,
         "id is not kebab-case")
    need(raw.get("section") in SECTIONS, f"section is not one of {', '.join(SECTIONS)}")
    need(isinstance(raw.get("order"), int) and not isinstance(raw.get("order"), bool), "order is not a whole number")
    need(isinstance(raw.get("line"), str) and bool(raw["line"].strip()), "line is empty")
    need(isinstance(raw.get("words", ""), str), "words is not text")
    marks, needs, when = _strings(raw.get("marks", [])), _strings(raw.get("needs", [])), \
        _strings(raw.get("tried_when", []))
    caps, beats = _strings(raw.get("captions", [])), raw.get("beats", [])
    need(marks is not None, "marks is not a list of words")
    need(needs is not None and all(n in GATES for n in needs), f"needs holds a gate other than {', '.join(GATES)}")
    need(isinstance(raw.get("card", True), bool), "card is not true or false")
    need(when is not None and all(_EVIDENCE.fullmatch(w) for w in when), "tried_when holds evidence nothing sees")
    need(caps is not None and len(caps) == 3, "captions are not three")
    need(isinstance(beats, list) and all(isinstance(b, dict) for b in beats), "beats is not a list of tables")
    for text in (raw.get("line"), raw.get("words", "")):
        if isinstance(text, str):
            need(all(p in PLACEHOLDERS for p in _PLACEHOLDER.findall(text)), f"{text!r} holds a placeholder "
                 f"other than {', '.join(PLACEHOLDERS)}")
    if bad:
        return None, bad
    return Tip(raw["id"], raw["section"], raw["order"], raw["line"].strip(), raw.get("words", ""), tuple(marks),
               tuple(needs), raw.get("card", True), tuple(when), tuple(caps), tuple(beats)), []


def load_rows(text: str) -> tuple[list[Tip], list[str]]:
    """The tips in this text and what was wrong with the ones left out. A second row with an id is left out
    (ids are never reused); two rows with one place in a section are both kept and the order is reported."""
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as e:
        return [], [f"not TOML: {e}"]
    raws = data.get("tip") if isinstance(data, dict) else None
    if not isinstance(raws, list):
        return [], ["no [[tip]] rows"]
    rows, problems, seen = [], [], set()
    for raw in raws:
        row, bad = parse_row(raw)
        problems += bad
        if row is None:
            continue
        if row.id in seen:
            problems.append(f"{row.id}: id is used twice")
            continue
        seen.add(row.id)
        rows.append(row)
    places = [(r.section, r.order) for r in rows]
    twice = sorted({p for p in places if places.count(p) > 1})
    problems += [f"{s} {o}: two tips have this place" for s, o in twice]
    return rows, problems


def read_table(path: Path | None = None) -> list[Tip]:
    """The shipped tips, in section order. A table that cannot be read gives none, and agentd goes on."""
    path = path or paths.tips_table()
    try:
        rows, problems = load_rows(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        print(f"tips: {path} cannot be read ({e}); there are no tips", file=sys.stderr)
        return []
    for p in problems:
        print(f"tips: {p}", file=sys.stderr)
    return ordered(rows)


def ordered(rows) -> list[Tip]:
    """Start here, then the sections in order, each in its own fixed order. Never random."""
    rank = {s: i for i, s in enumerate(SECTIONS)}
    return sorted(rows, key=lambda r: (rank[r.section], r.order, r.id))


# -- what this machine can back --

@dataclass(frozen=True)
class Facts:
    """What this machine can do right now. A tip is shown only where its gates and placeholders are met."""
    ai: str = ""             # the AI's name ("Claude") when one is chosen and not signed out; "" otherwise
    installed: bool = False  # an installed disk (undo works), not the live USB
    app: str = ""            # the newest app in ~/Apps, as its title
    project: str = ""        # the newest folder in ~/Projects
    sessions: bool = False   # the coding-sessions code is on this machine
    mail: bool = False       # the mail tips' code is on this machine
    noticed: bool = False    # the self-improvement code is on this machine
    keep: bool = False       # the desk can keep a widget
    desk_seen: bool = False  # a desk card other than Tips has been on the screen

    @property
    def model(self) -> bool:
        return bool(self.ai)

    def has(self, gate: str) -> bool:
        return {"model": self.model, "installed": self.installed, "app": bool(self.app),
                "project": bool(self.project), "sessions": self.sessions, "mail": self.mail,
                "noticed": self.noticed, "keep": self.keep, "desk-seen": self.desk_seen}.get(gate, False)


def fill(text: str, facts: Facts) -> str | None:
    """The text with {ai}, {app} and {project} filled from the machine, or None when one cannot be."""
    values = {"ai": facts.ai, "app": facts.app, "project": facts.project}
    out = text
    for name in _PLACEHOLDER.findall(text):
        if not values.get(name):
            return None
        out = out.replace("{" + name + "}", values[name])
    return out


def backed(row: Tip, facts: Facts) -> bool:
    """Can this machine do what the tip says, as it says it? The brief's rule 2: a tip it cannot back is not
    shown anywhere, not even greyed."""
    return (all(facts.has(g) for g in row.needs) and fill(row.line, facts) is not None
            and fill(row.words, facts) is not None)


def resolve(row: Tip, facts: Facts) -> Tip | None:
    """The row with its placeholders filled, or None when the machine cannot back it."""
    if not backed(row, facts):
        return None
    return replace(row, line=fill(row.line, facts), words=fill(row.words, facts))


def listed(rows, facts: Facts) -> list[Tip]:
    """The tips this machine can back now, filled, in the fixed order."""
    return [t for t in (resolve(r, facts) for r in ordered(rows)) if t is not None]


def fit(text: str, pattern: re.Pattern, limit: int) -> str:
    """A name from the machine as it may go into a tip's words, or "" when it may not."""
    s = " ".join(str(text).split())
    return s if s and len(s) <= limit and pattern.fullmatch(s) else ""


def newest_app(apps_dir: Path) -> str:
    """The title of the newest app in the folder: the one whose main.qml was written last. An app whose name
    does not fit a button is left out, and the next newest is used."""
    best, name = -1.0, ""
    try:
        entries = sorted(os.scandir(apps_dir), key=lambda e: e.name)
    except OSError:
        return ""
    for e in entries:
        try:
            if not APP_NAME.match(e.name) or not e.is_dir():
                continue
            written = os.stat(os.path.join(e.path, "main.qml")).st_mtime
        except OSError:
            continue
        title = fit(read_meta(Path(e.path)).get("title") or "", _TITLE, MAX_APP) or fit(e.name, _TITLE, MAX_APP)
        if title and written > best:
            best, name = written, title
    return name


def newest_project(projects_dir: Path) -> str:
    """The name of the newest folder in the projects folder (a dot folder is not one)."""
    best, name = -1.0, ""
    try:
        entries = sorted(os.scandir(projects_dir), key=lambda e: e.name)
    except OSError:
        return ""
    for e in entries:
        try:
            if e.name.startswith(".") or not e.is_dir() or not fit(e.name, _FOLDER, MAX_PROJECT):
                continue
            written = e.stat().st_mtime
        except OSError:
            continue
        if written > best:
            best, name = written, e.name
    return name


def read_facts(*, ai: str = "", installed: bool = False, desk_seen: bool = False, apps_dir: Path | None = None,
               projects_dir: Path | None = None, has_module: Callable[[str], bool] | None = None,
               mail: bool = False, noticed: bool = False, keep: bool = False) -> Facts:
    """What this machine can back, from the folders and the code it has. The AI, the disk and the desk are
    agentd's to say (it knows the setup, the restore points and what the desk showed); the folders are read
    here. `mail`, `noticed` and `keep` stay false until the code each tip needs is on main: the PR that
    ships it flips its fact (docs/TIPS.md)."""
    has_module = has_module or (lambda name: find_spec(name) is not None)
    try:
        sessions = has_module("bombadil.dev")
    except (ImportError, ValueError):
        sessions = False
    return Facts(ai=ai, installed=installed, app=newest_app(apps_dir or paths.apps_dir()),
                 project=newest_project(projects_dir or paths.home() / "Projects"), sessions=sessions,
                 mail=mail, noticed=noticed, keep=keep, desk_seen=desk_seen)


# -- what the person has been taught --

@dataclass
class State:
    """tips.json. agentd is its only writer."""
    first_ready: str = ""                                       # the day setup was first ready, YYYY-MM-DD
    days: list[str] = field(default_factory=list)               # days on which the person typed in the pill
    tried: list[str] = field(default_factory=list)              # tip ids
    known: list[str] = field(default_factory=list)              # tip ids the person has had in front of them
    open_on: str = ""                                           # the tip the Tips app opens on
    desk_seen: bool = False                                     # a card other than Tips has been on the screen

    def dump(self) -> dict:
        return {"first_ready": self.first_ready, "days": self.days, "tried": self.tried, "known": self.known,
                "open_on": self.open_on, "desk_seen": self.desk_seen}


def _is_day(value) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _ids(value) -> list[str]:
    seen: dict[str, None] = {}
    for v in value if isinstance(value, list) else []:
        if isinstance(v, str) and ID_RE.fullmatch(v) and len(v) <= 40:
            seen[v] = None
    return list(seen)[-MAX_TRIED:]


def parse_state(text: str | None, all_ids=()) -> State:
    """tips.json as it is read. Whatever is missing, damaged or edited by hand falls back for that part; a
    file that does not parse gives a new person's state. `known` missing from a file that has begun means
    the person has had every tip there is in front of them, so none of them is new."""
    if not text or not text.strip():
        return State()
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        print("tips: tips.json is not usable; starting as if nothing was taught", file=sys.stderr)
        return State()
    if not isinstance(data, dict):
        return State()
    s = State(first_ready=data["first_ready"] if _is_day(data.get("first_ready")) else "")
    s.days = sorted({d for d in data["days"] if _is_day(d)})[-MAX_DAYS:] if isinstance(data.get("days"), list) else []
    s.tried = _ids(data.get("tried"))
    s.known = _ids(data["known"]) if isinstance(data.get("known"), list) else (list(all_ids) if s.first_ready else [])
    s.open_on = data["open_on"] if isinstance(data.get("open_on"), str) and ID_RE.fullmatch(data["open_on"]) else ""
    s.desk_seen = data.get("desk_seen") is True
    return s


# -- evidence --

def local_keys(kind: str, target: str = "", verb: str = "open") -> tuple[str, ...]:
    """What a launcher action that ran well leaves for `tried_when`. Only what the person asked to see counts:
    closing an app, hiding a widget or resuming the AI teaches nothing that a tip shows."""
    if (kind in ("app", "panel", "widget", "mail") and verb != "open") or (kind == "rest" and verb != "pause"):
        return ()
    return (f"local:{kind}", f"local:{kind}:{target}") if target else (f"local:{kind}",)


def tool_keys(name) -> tuple[str, ...]:
    """A tool call's name as `tried_when` writes it: whole, and without the prefix an MCP tool carries."""
    name = str(name or "")
    short = name.rsplit("__", 1)[-1]
    return tuple(dict.fromkeys(f"tool:{n}" for n in (name, short) if re.fullmatch(r"[A-Za-z0-9_-]+", n)))


def job_keys(kind: str) -> tuple[str, ...]:
    return ("job:start", f"job:start:{kind}") if kind in JOB_KINDS else ("job:start",)


BANG = ("bang",)


def _words(text) -> str:
    return " ".join(str(text or "").split())


def _same_words(typed: str, words: str) -> bool:
    """Are the typed words the tip's own, the way the launcher compares (case and the ends' marks aside)?
    Words that end in "…" are the start of an ask: more after them still is the tip."""
    typed, words = _words(typed).casefold(), _words(words).casefold()
    if not typed or not words:
        return False
    if words.endswith("…"):
        return typed.startswith(words[:-1].rstrip())
    return typed.strip(" .!?,;:") == words.strip(" .!?,;:")


# -- Tips --

class Tips:
    """The tips, what this person has been taught, and the card. One of these lives in agentd.

    Hooks, all optional and all plain functions: `kept()` (piece 2: the person keeps Tips on the desk, so the
    first week and the six tips no longer end it), `hidden()` (the person put Tips away) and `on_open(id,
    play)` (the Tips app is asked to open on a tip; the app's piece wires it). `clock()` is today's date."""

    def __init__(self, rows=None, path: Path | None = None, clock: Callable[[], date] | None = None,
                 kept: Callable[[], bool] | None = None, hidden: Callable[[], bool] | None = None,
                 on_open: Callable[[str, bool], None] | None = None):
        self.rows: list[Tip] = ordered(rows if rows is not None else read_table())
        self._path = path
        self._clock = clock or date.today
        self.kept = kept or (lambda: False)
        self.hidden = hidden or (lambda: False)
        self.on_open = on_open
        self.facts = Facts()
        self._lock = threading.RLock()
        self._shown = ""                          # the tip the card is on; it moves when asked, not by itself
        self._summoned: dict[str, str] = {}       # the words a button put in the pill, by the tip that did
        self.state = self._read()

    @property
    def path(self) -> Path:
        return self._path or paths.tips_file()

    def _ids(self) -> list[str]:
        return [r.id for r in self.rows]

    def _read(self) -> State:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return State()
        except (OSError, UnicodeDecodeError) as e:
            print(f"tips: {self.path} cannot be read ({e}); starting as if nothing was taught", file=sys.stderr)
            return State()
        return parse_state(text, self._ids())

    def _save(self) -> bool:
        """Write tips.json, all or nothing. A disk that will not take it never costs the change: it holds until
        agentd stops."""
        path = self.path
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.state.dump(), indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            print(f"tips: could not save {path}: {e}", file=sys.stderr)
            return False
        return True

    def _today(self) -> str:
        return self._clock().isoformat()

    # -- what the machine can back --

    def _facts(self) -> Facts:
        return replace(self.facts, desk_seen=self.facts.desk_seen or self.state.desk_seen)

    def listed(self) -> list[Tip]:
        with self._lock:
            return listed(self.rows, self._facts())

    def _sequence(self, tips: list[Tip]) -> list[Tip]:
        """The tips the card turns through: Start here in the first week, and every tip whose button fits the
        card once the person keeps Tips on the desk. A tip that came with an update waits in the app until the
        person has had it in front of them: nothing new is put on the desk unasked."""
        known = set(self.state.known)
        cards = [t for t in tips if t.card and t.id in known]
        return cards if self.kept() else [t for t in cards if t.section == START]

    def _present(self, facts: Facts, seq: list[Tip]) -> bool:
        """Has Tips something to say to a new person? Setup is ready, the person has not put it away, and
        either they keep it, or the first week is not over and a Start-here tip is left untried."""
        if not facts.model or self.hidden() or not seq:
            return False
        if self.kept():
            return True
        return (bool(self.state.first_ready) and len(self.state.days) < WEEK_DAYS
                and any(t.id not in self.state.tried for t in seq))

    def _first_untried(self, seq: list[Tip]) -> Tip:
        return next((t for t in seq if t.id not in self.state.tried), seq[0])

    def message(self) -> dict:
        """The one message agentd sends the shell: {"type": "tips", "present", "card"}. The card is the tip
        the card is on, which stays until it is turned, or until it is no longer one the machine can back
        (then the first tip not tried). `present` false takes the card away."""
        with self._lock:
            facts = self._facts()
            tips = listed(self.rows, facts)
            seq = self._sequence(tips)
            if not self._present(facts, seq):
                return {"type": "tips", "present": False, "card": None}
            tip = next((t for t in seq if t.id == self._shown), None) or self._first_untried(seq)
            self._shown = tip.id
            pool = [t for t in seq if t.section == tip.section]
            index = pool.index(tip) + 1
            return {"type": "tips", "present": True,
                    "card": {"id": tip.id, "line": tip.line, "words": tip.words, "marks": list(tip.marks),
                             "why": f"{SECTIONS[tip.section]} · {index} of {len(pool)}", "index": index,
                             "count": len(pool), "tip": tip.as_dict()}}

    def listing(self) -> list[dict]:
        """What the Tips app lists: only the tips this machine can back, each with whether it is tried and
        whether it is new (its id was not known). Asking is looking, so the ids it answers with are known after."""
        with self._lock:
            tips = listed(self.rows, self._facts())
            began = bool(self.state.first_ready)
            out = [{"id": t.id, "section": t.section, "order": t.order, "line": t.line, "words": t.words,
                    "marks": list(t.marks), "card": t.card, "tried": t.id in self.state.tried,
                    "new": began and t.id not in self.state.known, "captions": list(t.captions),
                    "beats": [dict(b) for b in t.beats]} for t in tips]
            if began:
                self._know([t.id for t in tips])
            return out

    def _know(self, ids) -> None:
        fresh = [i for i in ids if i not in self.state.known]
        if fresh:
            self.state.known = [*self.state.known, *fresh][-MAX_TRIED:]
            self._save()

    # -- the card --

    def turn(self, direction) -> bool:
        """The arrows and the wheel: one tip on, or back, round the card's tips. True when the card moved."""
        if direction not in (1, -1) or isinstance(direction, bool):
            return False
        with self._lock:
            msg = self.message()
            if not msg["present"]:
                return False
            seq = self._sequence(listed(self.rows, self._facts()))
            i = next((n for n, t in enumerate(seq) if t.id == self._shown), 0)
            now = seq[(i + direction) % len(seq)].id
            moved = now != self._shown
            self._shown = now
            return moved

    def advance(self) -> bool:
        """After a turn that tried a tip: the card goes to the first tip not tried. True when it moved."""
        with self._lock:
            msg = self.message()
            if not msg["present"]:
                return False
            seq = self._sequence(listed(self.rows, self._facts()))
            now = self._first_untried(seq).id
            moved = now != self._shown
            self._shown = now
            return moved

    def open(self, tip_id, play: bool = False) -> str:
        """The Tips app is asked to open on a tip ("" is its first). The id is kept for the app to read and the
        app's hook is called; no window opens here. Returns the id kept."""
        with self._lock:
            tip_id = tip_id if isinstance(tip_id, str) and any(t.id == tip_id for t in self.listed()) else ""
            if self.state.open_on != tip_id:
                self.state.open_on = tip_id
                self._save()
        if self.on_open is not None:
            try:
                self.on_open(tip_id, bool(play))
            except Exception as e:  # noqa: BLE001 - a broken hook never costs the request
                print(f"tips: on_open: {type(e).__name__}: {e}", file=sys.stderr)
        return tip_id

    # -- what has happened --

    def ready(self) -> bool:
        """Setup is ready: the first time, this is the day the first week begins. A person who has nothing yet
        to be told about has every tip there is in front of them, so none is new. True when it began."""
        with self._lock:
            if self.state.first_ready:
                return False
            self.state.first_ready = self._today()
            self.state.known = self._ids()
            self._save()
            return True

    def use(self) -> bool:
        """The person typed in the pill: today is a day of use. True the first time on a day."""
        with self._lock:
            today = self._today()
            if today in self.state.days:
                return False
            self.state.days = sorted({*self.state.days, today})[-MAX_DAYS:]
            self._save()
            return True

    def card_seen(self) -> bool:
        """A desk card other than Tips was on the screen: the tip that folds cards can be taught now."""
        with self._lock:
            if self.state.desk_seen:
                return False
            self.state.desk_seen = True
            self._save()
            return True

    def summoned(self, text: str, tip_id) -> bool:
        """A button put these words in the pill for this tip. Remembered until Enter sends them or agentd stops."""
        text = _words(text)
        with self._lock:
            if not text or not isinstance(tip_id, str) or tip_id not in self._ids():
                return False
            self._summoned.pop(text, None)
            self._summoned[text] = tip_id
            while len(self._summoned) > MAX_SUMMONED:
                self._summoned.pop(next(iter(self._summoned)))
            return True

    def sent(self, text: str) -> list[str]:
        """The person sent these words. A tip is tried when they are the words its button put in the pill (as
        they were, or with more after a "…"), or its own words with "words" among its evidence. Returns the ids
        newly tried."""
        text = _words(text)
        if not text:
            return []
        with self._lock:
            facts, found = self._facts(), []
            for words, tip_id in self._summoned.items():
                row = next((r for r in self.rows if r.id == tip_id), None)
                more = words.endswith("…") or (row is not None and row.words.endswith("…"))
                start = words.rstrip("…").rstrip()
                if text == words or (more and start and text.startswith(start)):
                    found.append(tip_id)
            for row in self.rows:
                filled = fill(row.words, facts) if row.words and "words" in row.tried_when else None
                if filled and _same_words(text, filled):
                    found.append(row.id)
            return self.tried(found)

    def saw(self, *keys: str) -> list[str]:
        """agentd saw this (`local_keys`, `tool_keys`, `job_keys`, BANG). Returns the ids newly tried."""
        with self._lock:
            return self.tried([r.id for r in self.rows if any(k in keys for k in r.tried_when)])

    def tried(self, ids) -> list[str]:
        """Mark tips tried. Returns the ids that were not tried before."""
        with self._lock:
            known = set(self._ids())
            fresh = [i for i in dict.fromkeys(ids) if i in known and i not in self.state.tried]
            if fresh:
                self.state.tried = [*self.state.tried, *fresh][-MAX_TRIED:]
                self._save()
            return fresh

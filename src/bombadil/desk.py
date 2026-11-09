"""The desk: where the widgets sit around the pill, and which of them are put away.

The shell draws the desk; this module is the one place that decides and remembers. It holds
whether the desk is folded to strips, which widgets are put away, kept or folded by hand, and each
widget's rail and place in it, and keeps them in ~/.local/state/bombadil/desk.toml so a restart
keeps your desk.
That directory is outside the restore points, so an undo never moves the desk.

A widget is in one of three states: kept (on the desk at rest, after every boot), needed (the default:
there only while it has something to say) or off (put away). Only a widget with a calm face, one that has
something true to show while nothing is wrong, can be kept. The shipped desk keeps nothing.

Three things change it: the launcher's own words ("desk", "hide machine", "keep machine"), the shell (a
click on a strip, a card dragged to another place or into the row, a press on the Widgets card) and the
agent's `desk` tool, which agentd lets through only in a turn whose words asked for the desk
(`asked_for_desk`). All three go through `Desk.apply`, which answers with one plain sentence for the line
above the pill.

Launcher words run in worker threads, so every method takes the lock. `on_change` is called
with no arguments, after the lock is released, whenever the state really changed.
"""

import json
import os
import re
import sys
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import paths

RAILS = ("left", "right")


@dataclass(frozen=True)
class Widget:
    id: str
    title: str                 # what the line calls it: "Needs you"
    words: tuple[str, ...]     # what the launcher answers to, next to the id
    rail: str                  # where it sits until moved
    question: str = ""         # the one question it answers (widgets brief, rule 1): the Widgets card shows it
    calm: bool = False         # has a face for a desk at rest, so it can be kept
    listed: bool = True        # has a row on the Widgets card (Away and Alive wait for their feeds)
    quiet: str = ""            # what "keep" says of a widget that has nothing to show at rest
    then: str = ""             # what it does once it is no longer kept ("It still comes up when ...")


# The order here is each rail's default order, bottom up: the widget nearest the pill first.
# Left is the machine's rail, right is yours.
WIDGETS: dict[str, Widget] = {w.id: w for w in (
    Widget("now", "Now", ("now", "route"), "left", "What is it doing, and why?",
           quiet="Now shows only while a turn has steps to follow."),
    Widget("watching", "Watching", ("watching",), "left", "What is it keeping an eye on?",
           quiet="Watching shows only while something counts."),
    Widget("alive", "Alive", ("alive",), "left", "What is being touched right now?", listed=False,
           quiet="Alive shows only while something is being touched."),
    Widget("needs", "Needs you", ("needs you", "needs"), "right", "What is waiting for me?",
           quiet="Needs you shows only while two or more sessions wait."),
    Widget("away", "Away", ("away", "while you were away"), "right", "What happened while I was gone?",
           listed=False, quiet="Away shows only after you have been away."),
    Widget("machine", "Machine", ("machine",), "right", "Is the machine in trouble?", calm=True,
           then="It still comes up when something needs a look."),
)}
STATES = ("keep", "needed", "off")
# The only place two sessions' requests can be answered together, so it cannot be put away.
ALWAYS = ("needs",)
# Opt in: it is on the desk only after "show alive".
OPT_IN = ("alive",)
# Widgets that are present only with something to say, and that "show" is also a question to: the word
# brings the card up for a while even when nothing is wrong ("how's the machine"). The sentence is the answer.
ASKABLE = {"machine": "Here is the machine."}

# "desk" as a place (not a standing desk or a help desk), or a widget anywhere in the words.
_DESK_WORD = re.compile(r"\bwidgets?\b|(?<!standing )(?<!front )(?<!help )(?<!writing )\bdesk\b"
                        r"(?! (?:job|lamp|chair|work))")
# A verb for moving things about, then a widget's name right after it, and then the end of the
# words or a word that goes on about where it should be: "hide machine", "put watching on the
# right", "bring the machine card back". Not "show me the machine's logs" or "keep watching the build".
_DESK_ASK = re.compile(
    r"\b(?:show|hide|put|keep|pin|move|bring|fold|unfold)\s+(?:(?:up|back|the|my)\s+)*"
    r"(?:now|watching|needs you|machine|alive|while you were away)\b(?!')"
    r"(?=\s*$|\s*,"
    r"|\s+(?:on|to|in|into|at|above|below|up|back|first|last|again|always|please|rails?|card|where)\b)")


def asked_for_desk(prompt: str) -> bool:
    """Do the person's own words ask for the desk? The `desk` tool works only when they do:
    a widget that appears unasked would be a popup by another name. Give it the raw typed
    prompt, never the one with notes in front, which quote earlier desk words. This is a
    courtesy gate, not a security boundary: anyone who can type to the agent can say the words."""
    t = str(prompt or "").lower()
    if _DESK_WORD.search(t):
        return True
    return any(_DESK_ASK.search(sentence) for sentence in re.split(r"[.!?;\n]+", t))


def _key(s) -> str:
    """As the launcher: only spaces, hyphens and underscores fold away."""
    k = re.sub(r"[\s_-]+", "", str(s).lower())
    return k if re.fullmatch(r"[a-z0-9]+", k) else ""


def find(word) -> str | None:
    """The widget id a word names ("needs you", "Needs", "while you were away"), or None."""
    k = _key(word)
    if not k:
        return None
    for w in WIDGETS.values():
        if k in {_key(x) for x in (w.id, w.title, *w.words)}:
            return w.id
    return None


def title(widget) -> str:
    """How the line names a widget; what was asked for when it is none of ours."""
    wid = find(widget)
    return WIDGETS[wid].title if wid else str(widget or "a widget")


def can(widget) -> tuple[str, ...]:
    """The states a widget can take, in the Widgets card's order: only one with a calm face can be kept,
    and Needs you cannot be put away."""
    wid = find(widget)
    if wid is None:
        return ()
    if wid in ALWAYS:
        return ("needed",)
    return STATES if WIDGETS[wid].calm else ("needed", "off")


def _names() -> str:
    names = [w.title for w in WIDGETS.values()]
    return ", ".join(names[:-1]) + " and " + names[-1]


class Desk:
    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.RLock()
        self.on_change = None
        self.on_ask = None      # called with the widget's id when "show" asks for an askable widget
        self.on_widgets = None  # called with no arguments when the Widgets card is asked for
        self._reset()

    @property
    def path(self) -> Path:
        return self._path or paths.desk_file()

    def _reset(self):
        self.folded = False
        self.hidden = set(OPT_IN)
        self.kept: set[str] = set()       # on the desk at rest, after every boot: only widgets with a calm face
        self.stripped: set[str] = set()   # folded by hand: a strip whatever the room, and no slot in its rail
        self.rails = {w.id: w.rail for w in WIDGETS.values()}
        self.order = {r: [w.id for w in WIDGETS.values() if w.rail == r] for r in RAILS}
        self.screen = ""    # "" is the shell's first screen; else an output name

    # -- what the shell is told --

    def snapshot(self) -> dict:
        with self._lock:
            return {"type": "desk", "folded": self.folded,
                    "hidden": [w for w in WIDGETS if w in self.hidden],
                    "stripped": [w for w in WIDGETS if w in self.stripped],
                    "kept": [w for w in WIDGETS if w in self.kept],
                    # The rows of the Widgets card: what each widget answers and which states it can take.
                    "widgets": [{"id": w.id, "title": w.title, "question": w.question,
                                 "can": list(can(w.id))} for w in WIDGETS.values() if w.listed],
                    "rails": dict(self.rails),
                    "order": {r: list(self.order[r]) for r in RAILS},
                    "screen": self.screen}

    def describe(self) -> str:
        """The desk in words, for the agent's `state`."""
        with self._lock:
            def rail(r):
                return ", ".join(WIDGETS[w].title + (" (put away)" if w in self.hidden else "")
                                 + (" (kept on the desk)" if w in self.kept else "")
                                 + (" (folded to a strip)" if w in self.stripped else "")
                                 for w in self.order[r])
            return (f"The desk is {'folded to strips' if self.folded else 'open'}. "
                    f"Left rail, nearest the pill first: {rail('left') or 'nothing'}. "
                    f"Right rail: {rail('right') or 'nothing'}.")

    # -- changing it --

    def apply(self, op: str, widget=None, rail=None, rank=None, state=None) -> tuple[bool, str]:
        """Do one thing to the desk. Ops: toggle (the word "desk"), fold and unfold (every card
        to its strip and back; with a widget, only that one: a card dragged into the row), hide
        (a kept widget is first no longer kept, and only the next hide puts it away), show (which
        also gives a hand-folded widget its card back), keep (on the desk at rest; only a widget with a
        calm face), set (a widget's `state` at once: "keep", "needed" or "off", which is what the Widgets
        card's three parts say), move (rank counts from the pill within the rail's whole order, put-away
        widgets included; none puts it last, and a move gives a hand-folded widget its card back), reset
        (every widget, rail and place as the desk ships; the screen stays), widgets (open the Widgets card,
        through `on_widgets`), state. Returns (ok, one plain sentence)."""
        with self._lock:
            ok, text, changed = self._apply(str(op), widget, rail, rank, state)
            if changed:
                self.save()
        if changed and self.on_change is not None:
            try:
                self.on_change()
            except Exception as e:  # noqa: BLE001 - a broken listener never costs the change
                print(f"desk: on_change: {type(e).__name__}: {e}", file=sys.stderr)
        wid = find(widget) if ok and op == "show" else None
        if wid in ASKABLE and self.on_ask is not None:
            try:
                self.on_ask(wid)
            except Exception as e:  # noqa: BLE001 - the answer is the sentence, whatever the card does
                print(f"desk: on_ask: {type(e).__name__}: {e}", file=sys.stderr)
        if ok and op == "widgets" and self.on_widgets is not None:
            try:
                self.on_widgets()
            except Exception as e:  # noqa: BLE001 - the answer is the sentence, whatever the card does
                print(f"desk: on_widgets: {type(e).__name__}: {e}", file=sys.stderr)
        return ok, text

    def _apply(self, op: str, widget, rail, rank, state=None) -> tuple[bool, str, bool]:
        if op == "toggle" or (op in ("fold", "unfold") and not widget):
            want = (not self.folded) if op == "toggle" else op == "fold"
            if want == self.folded:
                return True, f"The desk is already {'folded' if want else 'unfolded'}.", False
            self.folded = want
            return True, f"{'Folded' if want else 'Unfolded'} the desk.", True
        if op == "state":
            return True, self.describe(), False
        if op == "widgets":
            return True, "Here are the widgets.", False      # the card itself opens through on_widgets
        if op == "reset":
            return self._back_to_default()
        if op not in ("hide", "show", "move", "fold", "unfold", "keep", "set"):
            return False, f"The desk cannot {op}.", False
        wid = find(widget)
        if wid is None:
            what = f"There is no widget called {str(widget)!r}." if widget else "Which widget?"
            return False, f"{what} The widgets are {_names()}.", False
        name = WIDGETS[wid].title
        if op == "keep":
            return self._keep(wid)
        if op == "set":
            return self._set(wid, state)
        if op == "hide":
            if wid in ALWAYS:
                return False, f"{name} cannot be hidden.", False
            # A kept widget is first no longer kept: it goes back to showing when it has something to say,
            # and only a second "hide" puts it away (which also stops its warnings).
            if wid in self.kept:
                self.kept.discard(wid)
                return True, self._unkept(wid), True
            if wid in self.hidden:
                return True, f"{name} is already put away.", False
            self.hidden.add(wid)
            return True, f"Put {name} away.", True
        if op in ("fold", "unfold"):
            want = op == "fold"
            if want == (wid in self.stripped):
                return True, f"{name} is already {'folded' if want else 'unfolded'}.", False
            (self.stripped.add if want else self.stripped.discard)(wid)
            return True, f"Folded {name} to its strip." if want else f"Unfolded {name}.", True
        if op == "show":
            # Asking for a widget wants its card, so a hand-folded one is unfolded too.
            was_folded = wid in self.stripped
            self.stripped.discard(wid)
            if wid in ASKABLE:
                was_away = wid in self.hidden
                self.hidden.discard(wid)
                # Asking for what was put away puts it back: the answer says so, as the card would not say it.
                return True, ASKABLE[wid] + (" It is back on the desk." if was_away else ""), was_away or was_folded
            if wid not in self.hidden:
                return True, (f"Unfolded {name}." if was_folded else f"{name} is already on the desk."), was_folded
            self.hidden.discard(wid)
            return True, f"Put {name} on the desk.", True
        return self._move(wid, rail, rank)

    def _unkept(self, wid: str) -> str:
        w = WIDGETS[wid]
        return f"{w.title} is no longer kept." + (f" {w.then}" if w.then else "")

    def _keep(self, wid: str) -> tuple[bool, str, bool]:
        """On the desk at rest, after every boot. Asking for a widget wants its card, so a hand-folded one is
        unfolded too, and a put-away one is back. A widget with no calm face has nothing true to show while
        nothing is happening, so the answer says when it does show, and nothing changes. One that is put away
        does not show at all, so the answer says how it comes back instead (Alive ships that way)."""
        w = WIDGETS[wid]
        if not w.calm:
            if wid in self.hidden:
                return True, f"{w.title} is put away. Say show {w.words[0]} to bring it back.", False
            return True, w.quiet or f"{w.title} shows only while it has something to say.", False
        was_folded = wid in self.stripped
        self.stripped.discard(wid)
        self.hidden.discard(wid)
        if wid in self.kept:
            return (True, f"Unfolded {w.title}." if was_folded else f"{w.title} is already kept on the desk.",
                    was_folded)
        self.kept.add(wid)
        return True, f"{w.title} stays on the desk.", True

    def _set(self, wid: str, state) -> tuple[bool, str, bool]:
        """A widget's state at once, whatever it was: what each part of the Widgets card says. Unlike "hide",
        "off" does not stop at "no longer kept"."""
        name = WIDGETS[wid].title
        if state not in STATES:
            return False, "A widget is kept, shown when needed, or off.", False
        if state == "keep":
            return self._keep(wid)
        if state == "off":
            if wid in ALWAYS:
                return False, f"{name} cannot be hidden.", False
            if wid in self.hidden:
                return True, f"{name} is already put away.", False
            self.hidden.add(wid)
            self.kept.discard(wid)
            return True, f"Put {name} away.", True
        if wid in self.kept:
            self.kept.discard(wid)
            return True, self._unkept(wid), True
        if wid in self.hidden:
            self.hidden.discard(wid)
            return True, f"Put {name} on the desk.", True
        return True, f"{name} is already on the desk.", False

    def _shape(self) -> tuple:
        return (self.folded, frozenset(self.hidden), frozenset(self.kept), frozenset(self.stripped),
                tuple(sorted(self.rails.items())), tuple((r, tuple(self.order[r])) for r in RAILS))

    def _back_to_default(self) -> tuple[bool, str, bool]:
        """Every widget, rail and place as the desk ships. Which screen it lives on is another choice, and stays."""
        before, screen = self._shape(), self.screen
        self._reset()
        self.screen = screen
        if self._shape() == before:
            return True, "The desk is already as it came.", False
        return True, "The desk is back as it came.", True

    def state_of(self, widget) -> str | None:
        """"keep", "needed" or "off" for a widget's id or word, None for none of ours."""
        wid = find(widget)
        if wid is None:
            return None
        with self._lock:
            return "off" if wid in self.hidden else "keep" if wid in self.kept else "needed"

    def _unfolded(self, wid: str, otherwise: str) -> tuple[bool, str, bool]:
        """A move that changes no place still gives a hand-folded widget its card back."""
        if wid not in self.stripped:
            return True, otherwise, False
        self.stripped.discard(wid)
        return True, f"Unfolded {WIDGETS[wid].title}.", True

    def _move(self, wid: str, rail, rank) -> tuple[bool, str, bool]:
        name, here = WIDGETS[wid].title, self.rails[wid]
        if rail is None and rank is None:
            return False, "Say which rail, or which place in it, to move it to.", False
        rail = here if rail is None else rail
        if rail not in RAILS:
            return False, "The rails are left and right.", False
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int)):
            return False, "The place is a whole number: 0 is nearest the pill.", False
        if rank is None and rail == here:
            return self._unfolded(wid, f"{name} is already in the {rail} rail.")
        was = self.order[here].index(wid)
        there = [w for w in self.order[rail] if w != wid]
        at = len(there) if rank is None else max(0, min(rank, len(there)))
        there.insert(at, wid)
        if rail == here and there == self.order[here]:
            return self._unfolded(wid, f"{name} is already there.")
        self.stripped.discard(wid)
        if rail != here:
            self.order[here] = [w for w in self.order[here] if w != wid]
            self.rails[wid] = rail
        self.order[rail] = there
        if rail != here:
            return True, f"Moved {name} to the {rail} rail.", True
        return True, f"Moved {name} {'nearer' if at < was else 'further from'} the pill.", True

    # -- keeping it --

    def load(self) -> "Desk":
        """Read desk.toml. Whatever is missing, wrong or hand-edited falls back to the default
        for that part; a file that does not parse at all gives the default desk."""
        with self._lock:
            self._reset()
            try:
                data = tomllib.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                data = {}
            except Exception as e:   # noqa: BLE001 - a file that cannot be read never stops agentd
                print(f"desk: {self.path} is not usable ({e}); starting with the default desk",
                      file=sys.stderr)
                data = {}
            self._adopt(data if isinstance(data, dict) else {})
        return self

    def _adopt(self, data: dict):
        if isinstance(data.get("folded"), bool):
            self.folded = data["folded"]
        if isinstance(data.get("hidden"), list):
            self.hidden = {w for w in data["hidden"]
                           if isinstance(w, str) and w in WIDGETS and w not in ALWAYS}
        # Only a widget with a calm face can be kept, and one that is put away is not kept: of the two a
        # hand-edited file can say at once, off wins.
        if isinstance(data.get("kept"), list):
            self.kept = {w for w in data["kept"] if isinstance(w, str) and w in WIDGETS and WIDGETS[w].calm}
            self.kept -= self.hidden
        if isinstance(data.get("stripped"), list):
            self.stripped = {w for w in data["stripped"] if isinstance(w, str) and w in WIDGETS}
        # An output name ("DP-1", "HDMI-A-1", "Virtual-1"), nothing the shell could trip on.
        if isinstance(data.get("screen"), str) and re.fullmatch(r"[A-Za-z0-9 ._:/-]{1,40}", data["screen"]):
            self.screen = data["screen"]
        rails = data.get("rails") if isinstance(data.get("rails"), dict) else {}
        for w in WIDGETS:
            if rails.get(w) in RAILS:
                self.rails[w] = rails[w]
        # The rails say where a widget sits; the order only says where in it. A widget the
        # order lacks, or lists under the other rail, goes last in the rail it belongs to.
        order = data.get("order") if isinstance(data.get("order"), dict) else {}
        seen: set[str] = set()
        for r in RAILS:
            listed = order.get(r) if isinstance(order.get(r), list) else []
            self.order[r] = []
            for w in listed:
                if isinstance(w, str) and w in WIDGETS and w not in seen and self.rails[w] == r:
                    seen.add(w)
                    self.order[r].append(w)
        for w in WIDGETS:
            if w not in seen:
                self.order[self.rails[w]].append(w)

    def save(self) -> bool:
        """Write desk.toml, all or nothing. A disk that will not take it never costs the change:
        it holds until agentd stops."""
        with self._lock:
            lines = ["# Bombadil's desk: which widget sits in which rail, which are put away, which are kept on the",
                     "# desk at rest and which are folded by hand.",
                     "# agentd writes this when the desk changes; edit by hand only while agentd is stopped.",
                     f"folded = {'true' if self.folded else 'false'}",
                     f"hidden = {json.dumps([w for w in WIDGETS if w in self.hidden])}",
                     f"kept = {json.dumps([w for w in WIDGETS if w in self.kept])}",
                     f"stripped = {json.dumps([w for w in WIDGETS if w in self.stripped])}",
                     f"screen = {json.dumps(self.screen)}", "", "[rails]"]
            lines += [f"{w} = {json.dumps(self.rails[w])}" for w in WIDGETS]
            lines += ["", "[order]"] + [f"{r} = {json.dumps(self.order[r])}" for r in RAILS]
            path = self.path
            tmp = path.with_name(path.name + ".tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(tmp, path)
            except OSError as e:
                print(f"desk: could not save {path}: {e}", file=sys.stderr)
                return False
        return True

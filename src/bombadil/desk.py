"""The desk: where the widgets sit around the pill, and which of them are put away.

The shell draws the desk; this module is the one place that decides and remembers. It holds
whether the desk is folded to strips, which widgets are put away, and each widget's rail and
place in it, and keeps them in ~/.local/state/bombadil/desk.toml so a restart keeps your desk.
That directory is outside the restore points, so an undo never moves the desk.

Three things change it: the launcher's own words ("desk", "hide machine"), the shell (a click
on a strip, a drag later) and the agent's `desk` tool, which agentd lets through only in a turn
whose words asked for the desk (`asked_for_desk`). All three go through `Desk.apply`, which
answers with one plain sentence for the line above the pill.

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


# The order here is each rail's default order, bottom up: the widget nearest the pill first.
# Left is the machine's rail, right is yours.
WIDGETS: dict[str, Widget] = {w.id: w for w in (
    Widget("now", "Now", ("now", "route"), "left"),
    Widget("watching", "Watching", ("watching",), "left"),
    Widget("alive", "Alive", ("alive",), "left"),
    Widget("needs", "Needs you", ("needs you", "needs"), "right"),
    Widget("away", "Away", ("away", "while you were away"), "right"),
    Widget("machine", "Machine", ("machine",), "right"),
)}
# The only place two sessions' requests can be answered together, so it cannot be put away.
ALWAYS = ("needs",)
# Opt in: it is on the desk only after "show alive".
OPT_IN = ("alive",)

_DESK_WORD = re.compile(r"\b(?:desk|widgets?)\b")
_DESK_VERB = re.compile(r"\b(?:show|hide|put|keep|pin|move|bring|fold|make)\b")
_DESK_NOUN = re.compile(r"\b(?:now|watching|needs you|machine|away|alive)\b")


def asked_for_desk(prompt: str) -> bool:
    """Do the person's own words ask for the desk? The `desk` tool works only when they do:
    a widget that appears unasked would be a popup by another name. Give it the raw typed
    prompt, never the one with notes in front, which quote earlier desk words."""
    t = str(prompt or "").lower()
    if _DESK_WORD.search(t):
        return True
    return any(_DESK_VERB.search(s) and _DESK_NOUN.search(s) for s in re.split(r"[.!?;\n]+", t))


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


def _names() -> str:
    names = [w.title for w in WIDGETS.values()]
    return ", ".join(names[:-1]) + " and " + names[-1]


class Desk:
    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.RLock()
        self.on_change = None
        self._reset()

    @property
    def path(self) -> Path:
        return self._path or paths.desk_file()

    def _reset(self):
        self.folded = False
        self.hidden = set(OPT_IN)
        self.rails = {w.id: w.rail for w in WIDGETS.values()}
        self.order = {r: [w.id for w in WIDGETS.values() if w.rail == r] for r in RAILS}
        self.screen = ""    # "" is the shell's first screen; else an output name

    # -- what the shell is told --

    def snapshot(self) -> dict:
        with self._lock:
            return {"type": "desk", "folded": self.folded,
                    "hidden": [w for w in WIDGETS if w in self.hidden],
                    "rails": dict(self.rails),
                    "order": {r: list(self.order[r]) for r in RAILS},
                    "screen": self.screen}

    def describe(self) -> str:
        """The desk in words, for the agent's `state`."""
        with self._lock:
            def rail(r):
                return ", ".join(WIDGETS[w].title + (" (put away)" if w in self.hidden else "")
                                 for w in self.order[r])
            return (f"The desk is {'folded to strips' if self.folded else 'open'}. "
                    f"Left rail, nearest the pill first: {rail('left') or 'nothing'}. "
                    f"Right rail: {rail('right') or 'nothing'}.")

    # -- changing it --

    def apply(self, op: str, widget=None, rail=None, rank=None) -> tuple[bool, str]:
        """Do one thing to the desk. Ops: toggle (the word "desk"), fold, unfold, hide, show,
        move (rank counts from the pill within the rail's whole order, put-away widgets
        included; none puts it last), state. Returns (ok, one plain sentence)."""
        with self._lock:
            ok, text, changed = self._apply(str(op), widget, rail, rank)
            if changed:
                self.save()
        if changed and self.on_change is not None:
            try:
                self.on_change()
            except Exception as e:  # noqa: BLE001 - a broken listener never costs the change
                print(f"desk: on_change: {type(e).__name__}: {e}", file=sys.stderr)
        return ok, text

    def _apply(self, op: str, widget, rail, rank) -> tuple[bool, str, bool]:
        if op in ("toggle", "fold", "unfold"):
            want = (not self.folded) if op == "toggle" else op == "fold"
            if want == self.folded:
                return True, f"The desk is already {'folded' if want else 'unfolded'}.", False
            self.folded = want
            return True, f"{'Folded' if want else 'Unfolded'} the desk.", True
        if op == "state":
            return True, self.describe(), False
        if op not in ("hide", "show", "move"):
            return False, f"The desk cannot {op}.", False
        wid = find(widget)
        if wid is None:
            what = f"There is no widget called {str(widget)!r}." if widget else "Which widget?"
            return False, f"{what} The widgets are {_names()}.", False
        name = WIDGETS[wid].title
        if op == "hide":
            if wid in ALWAYS:
                return False, f"{name} cannot be hidden.", False
            if wid in self.hidden:
                return True, f"{name} is already put away.", False
            self.hidden.add(wid)
            return True, f"Put {name} away.", True
        if op == "show":
            if wid not in self.hidden:
                return True, f"{name} is already on the desk.", False
            self.hidden.discard(wid)
            return True, f"Put {name} on the desk.", True
        return self._move(wid, rail, rank)

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
            return True, f"{name} is already in the {rail} rail.", False
        was = self.order[here].index(wid)
        there = [w for w in self.order[rail] if w != wid]
        at = len(there) if rank is None else max(0, min(rank, len(there)))
        there.insert(at, wid)
        if rail == here and there == self.order[here]:
            return True, f"{name} is already there.", False
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
            except (OSError, ValueError) as e:   # TOMLDecodeError and UnicodeDecodeError are ValueErrors
                print(f"desk: {self.path} is not usable ({e}); starting with the default desk",
                      file=sys.stderr)
                data = {}
            self._adopt(data)
        return self

    def _adopt(self, data: dict):
        if isinstance(data.get("folded"), bool):
            self.folded = data["folded"]
        if isinstance(data.get("hidden"), list):
            self.hidden = {w for w in data["hidden"]
                           if isinstance(w, str) and w in WIDGETS and w not in ALWAYS}
        if isinstance(data.get("screen"), str):
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
            lines = ["# Bombadil's desk: which widget sits in which rail, and which are put away.",
                     "# agentd writes this when the desk changes; it is safe to edit by hand.",
                     f"folded = {'true' if self.folded else 'false'}",
                     f"hidden = {json.dumps([w for w in WIDGETS if w in self.hidden])}",
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

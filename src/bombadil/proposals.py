"""Proposals: what the agent or the person wants to go out and has not (docs/CONNECT.md, "agentd: proposals").

A reply to a Slack message, a new task, a comment on one: each waits here as a proposal until the person's press on
the card's Send lets the outbox do it. The outbox holds this table (outbox.py); the shell edits it through agentd and
draws it as the `reply` of a card.

Why it is shaped this way:

- The send button is lit by what was drawn, not by what was asked for. `shown` is the fingerprint of the content the
  shell last said it drew (`op: shown`: the shell sends the content and agentd works the hash out, so no hash is ever
  made in the shell), and a proposal is `ready` only while it equals the proposal's own `fingerprint`. A change of
  content clears `shown`, so a press cannot be for words the person has not seen.
- Nothing here talks to a service. `new_place` (a conversation the person has not heard from) is a fact the caller
  fetched and hands in; a caller that could not ask simply does not hand one in, and the proposal says nothing it
  cannot know.
- A press that may have gone is never repeated by itself. The connection service remembers every proposal id it
  performed, so a person's second try on an `unknown` one is a new attempt with its own id (`perform_id`: `p3`, then
  `p3-2`), and the first stays on record.
- The file is the person's alone (0600), written whole and renamed into place. A file that cannot be read, or that is
  of a version this code does not know, is set aside beside itself and the table starts empty, with the counter kept
  when it can be read: ids are never reused, because the service would answer a reused one "already".
- Closed proposals (sent, put away, unsure) are forgotten after 30 days, and the table keeps at most 100, the oldest
  closed ones going first.
"""

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import paths
from .connect.driver import KINDS, fingerprint
from .connect.protocol import one_line

VERSION = 1
STATES = ("open", "sending", "sent", "unknown", "discarded")
CLOSED = ("sent", "unknown", "discarded")
AGENT, PERSON = "agent", "person"
FORGET_AFTER = 30 * 86400.0
MAX_KEPT = 100
MAX_CONTENT = 20_000          # characters of one reply; past 3 000 it is a warning, and the service refuses it
MAX_FIELDS = 12
MAX_FIELD = 5_000
LONG = 3_000
TYPED_KEPT = 4_000
NOTE_MAX = 300
MAX_TARGET = 200
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,39}")
_BROADCAST = re.compile(r"@(channel|here|everyone)\b|<!(channel|here|everyone)\b", re.IGNORECASE)
_NEXT = re.compile(r'"next"\s*:\s*(\d+)')


class ProposalError(ValueError):
    """The words that are not a proposal, as a sentence for whoever made it."""


def clean_content(kind: str, content):
    """The content as a proposal holds it: text for a reply, an object of text fields for a task."""
    if kind == "slack_reply":
        if not isinstance(content, str):
            raise ProposalError("A reply is text.")
        return content[:MAX_CONTENT]
    if kind in ("task_create", "task_comment"):
        if not isinstance(content, dict):
            raise ProposalError("A task takes its fields, each as text.")
        out = {}
        for key, value in list(content.items())[:MAX_FIELDS]:
            if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(value, str):
                raise ProposalError("A task takes its fields, each as text.")
            out[key] = value[:MAX_FIELD]
        return out
    raise ProposalError("That is not something I send.")


def empty_of(kind: str) -> object:
    return "" if kind == "slack_reply" else {}


def is_empty(content) -> bool:
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, dict):
        return not any(isinstance(v, str) and v.strip() for v in content.values())
    return True


def _words(content) -> list[str]:
    """Every piece of text a proposal holds."""
    if isinstance(content, str):
        return [content]
    return [v for v in content.values() if isinstance(v, str)] if isinstance(content, dict) else []


def _squash(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def persons_words(content, typed: str) -> bool:
    """Is all of the text something the person typed? Words the agent put together from what it read are not."""
    said = _squash(typed)
    words = [_squash(w) for w in _words(content) if w.strip()]
    return bool(said) and bool(words) and all(w in said for w in words)


def warnings_for(kind: str, content, created_by: str, tainted: bool, typed: str, new_place: bool) -> list[dict]:
    """What the card says before the person presses Send, in the order they matter."""
    out: list[dict] = []
    if new_place:
        out.append({"kind": "new_place", "text": "You have not heard from or posted in this conversation lately. "
                                                 "Check it is the right place before you press Send."})
    if kind == "slack_reply" and isinstance(content, str):
        found = _BROADCAST.search(content)
        if found:
            word = "@" + (found.group(1) or found.group(2)).lower()
            out.append({"kind": "broadcast", "text": f"This notifies everyone in the channel ({word}). "
                                                     "Check you mean to before you press Send."})
        if len(content) > LONG:
            out.append({"kind": "long", "text": f"This is over {LONG:,} characters, more than Slack takes in one "
                                                "message. Shorten it before you press Send."})
    if tainted and created_by == AGENT and not persons_words(content, typed):
        out.append({"kind": "tainted", "text": "I wrote this after reading messages other people sent, and these are "
                                               "not words you typed. Read it before you press Send."})
    return out


def is_ready(p: dict) -> bool:
    """Lit for a press: open, something to send, and the content the shell drew is the content that would go."""
    return p.get("state") == "open" and not is_empty(p.get("content")) and bool(p.get("shown")) \
        and p.get("shown") == p.get("fingerprint")


def perform_id(p: dict) -> str:
    """The id the connection service is given: the proposal's own, then `-2`, `-3` for each try after it."""
    attempts = int(p.get("attempts") or 1)
    return p["id"] if attempts <= 1 else f"{p['id']}-{attempts}"


def wire(p: dict) -> dict:
    """What the shell is told: the proposal, lit or not, without the person's typed words."""
    out = {k: v for k, v in p.items() if k not in ("typed", "attempts")}
    out["ready"] = is_ready(p)
    return out


class Proposals:
    """The table. Every method is safe from any thread; `on_change(wire)` is called, after the change and in the
    thread that made it, whenever what the shell would see of a proposal is not what it last saw."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time,
                 on_change: Callable[[dict], None] | None = None):
        self._path = path
        self.clock = clock
        self.on_change = on_change
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        self._next = 1
        self._loaded = False
        self._told: dict[str, dict] = {}     # what the shell was last told, by id

    @property
    def path(self) -> Path:
        return self._path or paths.proposals_file()

    # -- reading --

    def get(self, id: str) -> dict | None:
        """The proposal as the shell sees it, or None."""
        with self._lock:
            self._load()
            p = self._items.get(id) if isinstance(id, str) else None
            return wire(p) if p else None

    def record(self, id: str) -> dict | None:
        """The whole record, with what is not for the shell. A copy."""
        with self._lock:
            self._load()
            p = self._items.get(id) if isinstance(id, str) else None
            return json.loads(json.dumps(p)) if p else None

    def all(self) -> list[dict]:
        with self._lock:
            self._load()
            return [wire(p) for p in self._items.values()]

    # -- making and changing --

    def open(self, kind: str, target: str, content=None, where: str = "", created_by: str = PERSON,
             tainted: bool = False, typed: str = "", new_place: bool = False) -> dict:
        """A new proposal. A person's reply starts with no words; the agent's has them. Raises ProposalError."""
        if kind not in KINDS:
            raise ProposalError("That is not something I send.")
        if not isinstance(target, str) or not target.strip() or len(target) > MAX_TARGET:
            raise ProposalError("I need to know where this goes.")
        if created_by not in (AGENT, PERSON):
            raise ProposalError("That is not something I send.")
        content = clean_content(kind, empty_of(kind) if content is None else content)
        with self._lock:
            self._load()
            id = f"p{self._next}"
            self._next += 1
            p = {"id": id, "kind": kind, "target": target.strip(), "content": content,
                 "where": one_line(where, 80), "created_by": created_by, "tainted": bool(tainted),
                 "typed": str(typed or "")[:TYPED_KEPT], "new_place": bool(new_place), "warnings": [],
                 "fingerprint": "", "shown": "", "state": "open", "receipt": None, "note": "", "attempts": 0,
                 "updated": self.clock()}
            self._items[id] = p
            self._refresh(p)
            self._trim()
            self._save()
            return self._told_out(p)

    def edit(self, id: str, content) -> dict | None:
        """The box changed. Only an open proposal takes it; what is not valid content changes nothing."""
        with self._lock:
            p = self._open_one(id)
            if p is None:
                return None
            try:
                p["content"] = clean_content(p["kind"], content)
            except ProposalError:
                return None
            p.update(shown="", note="", updated=self.clock())
            self._refresh(p)
            self._save()
            return self._told_out(p)

    def shown(self, id: str, content) -> dict | None:
        """The card drew exactly this content. The fingerprint is worked out here from it, so that what is lit is
        what would go. A content that is not valid for the kind lights nothing."""
        with self._lock:
            self._load()
            p = self._items.get(id) if isinstance(id, str) else None
            if p is None:
                return None
            try:
                p["shown"] = fingerprint(p["kind"], p["target"], clean_content(p["kind"], content))
            except ProposalError:
                p["shown"] = ""
            return self._told_out(p)

    def discard(self, id: str) -> dict | None:
        """Put away. An open one that has nothing in it was only ever a box, and goes at once."""
        with self._lock:
            self._load()
            p = self._items.get(id) if isinstance(id, str) else None
            if p is None or p["state"] not in ("open", "unknown"):
                return None
            p.update(state="discarded", shown="", updated=self.clock())
            self._refresh(p)
            out = self._told_out(p)
            if is_empty(p["content"]):
                self._items.pop(id, None)
                self._told.pop(id, None)
            self._save()
            return out

    # -- the press --

    def begin_send(self, id: str, again: bool = False) -> dict | None:
        """The press goes ahead: open (or, on the person's second choice, unsure) becomes `sending`. None when it
        is not in a state that can be sent."""
        with self._lock:
            self._load()
            p = self._items.get(id) if isinstance(id, str) else None
            if p is None or p["state"] != ("unknown" if again else "open"):
                return None
            p.update(state="sending", note="", attempts=int(p.get("attempts") or 0) + 1, updated=self.clock())
            self._save()
            return self._told_out(p)

    def sent(self, id: str, receipt: dict | None) -> dict | None:
        return self._end(id, "sent", receipt=receipt if isinstance(receipt, dict) else None, note="")

    def unknown(self, id: str, note: str = "") -> dict | None:
        """It may or may not have gone: the person looks, and may press again."""
        return self._end(id, "unknown", note=note)

    def reopen(self, id: str, note: str = "") -> dict | None:
        """The service said no and nothing went: open again, with its own sentence."""
        return self._end(id, "open", note=note)

    def _end(self, id: str, state: str, receipt=None, note: str = "") -> dict | None:
        with self._lock:
            self._load()
            p = self._items.get(id) if isinstance(id, str) else None
            if p is None or p["state"] != "sending":
                return None
            p.update(state=state, receipt=receipt, note=one_line(note, NOTE_MAX), updated=self.clock())
            self._save()
            return self._told_out(p)

    # -- inside --

    def _open_one(self, id) -> dict | None:
        self._load()
        p = self._items.get(id) if isinstance(id, str) else None
        return p if p is not None and p["state"] == "open" else None

    def _refresh(self, p: dict) -> None:
        """Fingerprint and warnings follow the content."""
        p["fingerprint"] = fingerprint(p["kind"], p["target"], p["content"])
        p["warnings"] = warnings_for(p["kind"], p["content"], p["created_by"], p["tainted"], p["typed"],
                                     p["new_place"])

    def _told_out(self, p: dict) -> dict:
        """The proposal for the shell, and the shell is told (when it is not what it already knows)."""
        out = wire(p)
        if self._told.get(p["id"]) != out:
            self._told[p["id"]] = out
            if self.on_change is not None:
                try:
                    self.on_change(out)
                except Exception as e:  # noqa: BLE001 - a listener that breaks never costs a change
                    print(f"agentd: proposal listener: {type(e).__name__}: {e}", file=sys.stderr)
        return out

    def _trim(self) -> None:
        """At most MAX_KEPT: the oldest closed go first, then the oldest of the rest."""
        while len(self._items) > MAX_KEPT:
            ranked = sorted(self._items.values(), key=lambda q: (q["state"] not in CLOSED, q["updated"]))
            gone = ranked[0]["id"]
            self._items.pop(gone, None)
            self._told.pop(gone, None)

    # -- the file --

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.path
        try:
            text = path.read_text()
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError):
            self._set_aside(path, "")
            return
        try:
            doc = json.loads(text)
            if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("proposals"), list):
                raise ValueError("not a table of this version")
            items = [self._record(raw) for raw in doc["proposals"]]
        except (ValueError, TypeError, KeyError, RecursionError):
            self._set_aside(path, text)
            return
        self._next = max(1, int(doc["next"])) if isinstance(doc.get("next"), int) else 1
        now = self.clock()
        for p in items:
            if p is None or (p["state"] in CLOSED and now - p["updated"] > FORGET_AFTER):
                continue
            if p["state"] == "sending":
                # agentd stopped between asking and hearing back: it may have gone
                p["state"] = "unknown"
            self._items[p["id"]] = p
            self._next = max(self._next, _number(p["id"]) + 1)

    def _record(self, raw) -> dict | None:
        """A proposal as it was saved, checked. One that is not valid is left out, not believed."""
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS or raw.get("state") not in STATES:
            return None
        if not isinstance(raw.get("id"), str) or _number(raw["id"]) < 1 or not isinstance(raw.get("target"), str):
            return None
        try:
            content = clean_content(raw["kind"], raw.get("content"))
        except ProposalError:
            return None
        p = {"id": raw["id"], "kind": raw["kind"], "target": raw["target"], "content": content,
             "where": one_line(raw.get("where"), 80),
             "created_by": raw["created_by"] if raw.get("created_by") in (AGENT, PERSON) else PERSON,
             "tainted": raw.get("tainted") is True, "typed": str(raw.get("typed") or "")[:TYPED_KEPT],
             "new_place": raw.get("new_place") is True, "warnings": [], "fingerprint": "", "shown": "",
             "state": raw["state"], "receipt": raw["receipt"] if isinstance(raw.get("receipt"), dict) else None,
             "note": one_line(raw.get("note"), NOTE_MAX),
             "attempts": raw["attempts"] if isinstance(raw.get("attempts"), int) else 0,
             "updated": float(raw["updated"]) if isinstance(raw.get("updated"), (int, float)) else self.clock()}
        self._refresh(p)
        return p

    def _set_aside(self, path: Path, text: str) -> None:
        """A table that cannot be believed is kept beside itself, not read and not thrown away; the counter is
        kept if it can be found, so that no id is used twice."""
        found = _NEXT.search(text or "")
        if found:
            self._next = max(1, int(found.group(1)))
        try:
            path.replace(path.with_name(f"{path.name}.bad-{int(self.clock())}"))
        except OSError as e:
            print(f"agentd: proposals file: {e}", file=sys.stderr)

    def _save(self) -> None:
        now = self.clock()
        for gone in [i for i, p in self._items.items() if p["state"] in CLOSED and now - p["updated"] > FORGET_AFTER]:
            self._items.pop(gone, None)
            self._told.pop(gone, None)
        doc = {"version": VERSION, "next": self._next, "proposals": list(self._items.values())}
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as f:
                    fd = -1
                    f.write(json.dumps(doc, ensure_ascii=False))
            finally:
                if fd >= 0:
                    os.close(fd)
            os.replace(tmp, path)
        except OSError as e:
            print(f"agentd: proposals file: {e}", file=sys.stderr)   # a table that cannot be kept still works


def _number(id: str) -> int:
    """`p12` -> 12; 0 for an id that is not one of ours."""
    match = re.fullmatch(r"p(\d{1,9})", id)
    return int(match.group(1)) if match else 0

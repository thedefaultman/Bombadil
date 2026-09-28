"""What Focus shows around one thing, and the one-line answer to "why is this here?".

Focus puts one thing in the middle and its links in fixed places around it: where it came
from on the left, what it was read with on the right, what it is used with below, and its
changes on top. Every line says why and when (rule 2), worked out here from what was
witnessed, so the Brain window, `bombadil brain`, the launcher and the agents' tools say
the same thing about the same file.

The strong links (made by, changed by, came from) are stored; the weak ones are worked out
on the spot from events, so they cannot go stale: "read with" is a page that was open in
the 20 minutes before a change, "used with" is something changed on the same occasion (the
same turn, or the same stretch of your or a session's saves) at least twice.

A folder in the middle is listed from disk, not from the brain, so Focus is a file manager
that is never behind; the brain only adds who and when. Private things show their names
and history, never their contents.

Nothing here calls a model: `why()` answers in the pill's line in milliseconds.
"""

import os
import time
from dataclasses import replace
from pathlib import Path

from . import rules, words
from .ingest import fingerprint_url, site_of
from .store import COALESCE_S, Store

# A page opened this long before a change was open while it was made...
READ_WINDOW_S = 20 * 60
# ...unless it was closed more than a minute before the change started.
STILL_OPEN_S = 60
# Your (or a session's) saves this close together are one occasion for "used with".
TOGETHER_S = 20 * 60
# Changed this recently by another turn or session: someone is working on it now (amber).
BUSY_S = 15 * 60
CHILDREN_LIMIT = 200
# A folder of a million generated files is listed by its first entries, not stat'ed whole.
SCAN_LIMIT = 20000
# How far back a thing's history is read for its lines; older events only count.
HISTORY_CAP = 2000
OCCASIONS = 60
UNKNOWN = "The brain does not know this yet."
NOT_OPENED = "Nothing has opened it since."
MOUNTS = Path("/proc/self/mounts")

ACTORS = ("turn", "session", "app", "you", "system")
# What happened, as (verb, what follows "it"): "moved" + "to the Trash".
VERBS = {"create": ("made", ""), "change": ("changed", ""), "move": ("moved", ""), "delete": ("deleted", ""),
         "trash": ("moved", "to the Trash"), "download": ("downloaded", ""), "visit": ("read", ""),
         "install": ("installed", ""), "upgrade": ("upgraded", ""), "remove": ("removed", "")}
GONE = ("delete", "trash", "move", "remove")
# Second-level names under a country's domain: bbc.co.uk is "bbc", not "co".
SLDS = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"}


# -- refs --

def resolve(store: Store, ref) -> dict | None:
    """The thing a ref names: an id (int or digits), an absolute path (the living thing
    there, else the one that went last), an http(s) URL, or a key ("turn:41", "system")."""
    if ref is None or isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return _visible(store.get(ref))
    ref = str(ref).strip()
    if not ref:
        return None
    if ref.isascii() and ref.isdigit():
        return _visible(store.get(int(ref)))
    if ref.startswith("/"):
        return _visible(store.by_path(os.path.normpath(ref), deleted=True))
    if ref.lower().startswith(("http://", "https://")):
        key = fingerprint_url(ref)
        return _visible(store.by_key(f"url:{key}")) if key else None
    return _visible(store.by_key(ref))


def _visible(thing: dict | None) -> dict | None:
    return None if thing is None or thing["forgotten"] else thing


def ref_of(thing: dict) -> str:
    """What the app passes back to focus to come here again. A thing that went is named by
    its id, since something new may live at its path now."""
    if thing["path"] and thing["deleted"] is None:
        return thing["path"]
    if thing["kind"] == "page" and thing["url"]:
        return thing["url"]
    if thing["key"] and not thing["key"].startswith("unit:"):
        return thing["key"]   # a running turn's key changes when it ends; its id does not
    return str(thing["id"])


def site_name(url: str) -> str:
    """'https://www.rent-portal.com/x' -> 'rent-portal': the site as people say it."""
    host = site_of(url) if "//" in url else url
    if not host or ":" in host or host.replace(".", "").isdigit():
        return host
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in SLDS and len(labels[-1]) == 2:
        labels = labels[:-2]
    elif len(labels) >= 2:
        labels = labels[:-1]
    return labels[-1] or host


# -- who --

def who_for(store: Store, actor: str | None, actor_thing: int | None = None, via: str | None = "") -> words.Who:
    """An event's actor as words.Who: the turn's number and words, the session's name and
    project, the app's title."""
    kind = actor or "unknown"
    via = via or ""
    if kind == "turn":
        n, prompt = _turn_words(store, actor_thing)
        return words.Who("turn", n=n, prompt=prompt, via=via)
    if kind == "session":
        thing = store.get(actor_thing)
        meta = (thing or {}).get("meta") or {}
        project = str(meta.get("project") or "")
        return words.Who("session", name=str(meta.get("name") or ""), project=_project_title(store, project), via=via)
    if kind == "app":
        thing = store.get(actor_thing)
        return words.Who("app", name=thing["title"] if thing else "", via=via)
    return words.Who(kind, via=via)


def _turn_words(store: Store, tid: int | None) -> tuple[int | None, str]:
    row = store.turn_of_thing(tid) if tid is not None else None
    if row is not None:
        return row["n"], row["prompt"] or ""
    thing = store.get(tid)
    key = (thing or {}).get("key") or ""
    if key.startswith("turn:") and key[5:].isdigit():
        n = int(key[5:])
        title = thing["title"] or ""
        return n, "" if title == f"turn {n}" else title
    return None, ""   # still running: its number comes with its row


def _project_title(store: Store, project: str) -> str:
    """Sessions name their project as the slice does ("bombadil"); the folder says "Bombadil"."""
    if not project:
        return ""
    row = store.one("SELECT title FROM things WHERE kind = 'project' AND deleted IS NULL AND title = ? "
                    "COLLATE NOCASE LIMIT 1", (project,))
    return row["title"] if row else project


def short(w: words.Who) -> str:
    """The actor in as few words as a list column holds: 'the machine, turn 41', 'kit', 'you'."""
    if w.kind == "turn":
        return words.who(replace(w, prompt=""))
    if w.kind == "session":
        return w.name or words.who(w)
    if w.kind == "app":
        return w.name or "an app"
    if w.kind == "you":
        return "you"
    if w.kind == "system":
        return "the system"
    return ""


def _phrase(kind: str, thing_kind: str = "") -> tuple[str, str]:
    if kind == "create" and thing_kind == "fact":
        return "added", ""
    return VERBS.get(kind, ("changed", ""))


def _did(kind: str, thing_kind: str = "") -> str:
    """'made this', 'moved this to the Trash'."""
    verb, tail = _phrase(kind, thing_kind)
    return f"{verb} this{' ' + tail if tail else ''}"


def sentence(verb: str, tail: str, w: words.Who, t: float | None, now: float | None = None) -> str:
    """Like words.made_sentence for any verb: 'Deleted by the machine in turn 44 today at
    10:02.', 'You moved it to the Trash in Files on Monday.'"""
    day = words.on_day(t, now)
    if w.kind == "you":
        place = words.window(w.via)
        bits = [f"You {verb} it", tail, f"in {place}" if place else "", day]
        return " ".join(b for b in bits if b) + "."
    head = verb[0].upper() + verb[1:] + (" " + tail if tail else "")
    if w.kind in ("unknown", "before"):
        return f"{head} while the brain was not watching{', ' + day if day else ''}."
    after = (", " if w.kind == "turn" and w.prompt else " ") + day if day else ""
    return f"{head} {words.by(w)}{after}."


def line_for_thing(store: Store, thing: dict, why: str, t: float | None = None, now: float | None = None,
                   tone: str = "normal", title: str | None = None) -> dict:
    """One line in a slot: a thing the app can focus next, and why it is there."""
    return {"id": thing["id"], "ref": ref_of(thing), "title": title or title_of(store, thing),
            "kind": thing["kind"], "why": why, "t": t, "when": words.when(t, now), "tone": tone}


def title_of(store: Store, thing: dict) -> str:
    if thing["kind"] == "session":
        return words.who(who_for(store, "session", thing["id"]))
    return thing["title"] or os.path.basename((thing["path"] or "").rstrip("/")) or thing["key"] or thing["kind"]


# -- the disk --

_mounts: tuple[float, list[tuple[str, bool]]] = (0.0, [])


def _atime_kept(path: str) -> bool:
    """Whether the filesystem records reads at all. On a noatime mount every file would look
    unopened, so the brain says nothing about opening rather than something false."""
    global _mounts
    now = time.monotonic()
    if now - _mounts[0] > 60:
        rows = []
        try:
            for line in MOUNTS.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    point = parts[1].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
                    rows.append((point, "noatime" not in parts[3].split(",")))
        except OSError:
            pass
        rows.sort(key=lambda r: len(r[0]), reverse=True)
        _mounts = (now, rows)
    for point, kept in _mounts[1]:
        if path == point or path.startswith(point.rstrip("/") + "/"):
            return kept
    return True


def opened_since_change(path: str | None) -> bool | None:
    """True when the file was read after its last change (relatime keeps that much), None
    when that cannot be known."""
    if not path or not _atime_kept(path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_atime_ns > st.st_mtime_ns


def _preview_type(path: str) -> str:
    from .describe import file_type
    return file_type(path)


# -- focus --

def focus(store: Store, ref, home: str, now: float | None = None, limit: int = 5,
          looking: int | None = None) -> dict | None:
    """Everything the Brain window shows around one thing, or None when the brain does not
    know the ref. `looking` is the turn or session asking (its thing id), whose own work is
    not "someone else changing it now".

    Every list item in a slot is a LINE:
        {"id", "ref", "title", "kind", "why", "t", "when", "tone": "normal" | "amber"}
    where ref is the path, URL, key or id to pass back to focus, why is a short clause
    without the title ("open while builder edited this"; the came_from lines end with their
    time: "“drop the old cleanup”, 13:02"), and when is words.when(t).

        {"thing": {"id", "ref", "kind", "title", "path", "url",
                   "area": {"id", "ref", "title", "kind"} | None,
                   "size", "created", "changed", "deleted", "private",
                   "made": "Made by the machine in turn 41, “install the VPN”, on Monday.",
                   "last": "Last changed by you in the terminal today at 10:02." | "",
                   "opened": bool | None,      # read since its last change (files only)
                   "exists": bool},
         "preview": {"type": "text" | "image" | "pdf" | "folder" | "page" | "turn" | "app" | "package"
                             | "fact" | "session" | "site" | "none",
                     "path"?, "url"?, "text"?, "private": bool},
         "slots": {"came_from": {"items": [LINE], "more": n},   # who made and changed it, the page
                                                                # it was downloaded from, what a turn
                                                                # read before writing it
                   "read_with": {"items": [LINE], "more": n},   # pages open while it changed
                   "used_with": {"items": [LINE], "more": n},   # changed with it, made from it
                   "changes": {"count_week": n, "days": [7 ints, oldest first, today last],
                               "items": [{"id", "kind", "t", "when", "why"}], "more": n}},
         "children": {"items": [{"id" | None, "ref", "name", "kind", "t", "when", "who", "private"}],
                      "total": n} | None,
         "description": None}                                   # the service fills it in

    Each slot holds at most `limit` lines and "more" counts the rest.
    """
    thing = resolve(store, ref)
    if thing is None:
        return None
    return _Focus(store, home, now, looking).focus(thing, max(0, min(int(limit), 100)))


def children(store: Store, ref, home: str, offset: int = 0, limit: int = CHILDREN_LIMIT,
             now: float | None = None) -> dict | None:
    """A page of what is inside a folder, project, app, site, turn or session (see focus)."""
    thing = resolve(store, ref)
    if thing is None:
        return None
    return _Focus(store, home, now).children(thing, max(0, int(offset)), max(0, min(int(limit), 1000)))


def why(store: Store, ref, home: str, now: float | None = None) -> str:
    """One or two sentences for the pill's line, with no model: "Made by the machine in turn
    41, “install the VPN”, on Monday. Nothing has opened it since." """
    thing = resolve(store, ref)
    if thing is None:
        return UNKNOWN
    return _Focus(store, home, now).why(thing)


class _Focus:
    """One question to the brain, with the lookups it repeats remembered."""

    def __init__(self, store: Store, home: str, now: float | None = None, looking: int | None = None):
        self.store = store
        self.home = home.rstrip("/") or "/"
        self.now = time.time() if now is None else now
        self.looking = looking
        self._who: dict = {}
        self._things: dict = {}

    def get(self, tid: int | None) -> dict | None:
        if tid is None:
            return None
        if tid not in self._things:
            self._things[tid] = self.store.get(tid)
        return self._things[tid]

    def many(self, ids) -> dict[int, dict]:
        ids = [i for i in dict.fromkeys(ids) if i is not None]
        out = {i: self._things[i] for i in ids if i in self._things}
        todo = [i for i in ids if i not in out]
        for n in range(0, len(todo), 500):
            chunk = todo[n:n + 500]
            for row in self.store.q(f"SELECT * FROM things WHERE id IN ({', '.join('?' for _ in chunk)})", chunk):
                self._things[row["id"]] = out[row["id"]] = row
        return out

    def who(self, actor: str | None, actor_thing: int | None = None, via: str | None = "") -> words.Who:
        key = (actor, actor_thing, via)
        if key not in self._who:
            self._who[key] = who_for(self.store, actor, actor_thing, via)
        return self._who[key]

    def when(self, t: float | None) -> str:
        return words.when(t, self.now)

    def line(self, thing: dict, why: str, t: float | None, tone: str = "normal", title: str | None = None) -> dict:
        return line_for_thing(self.store, thing, why, t, self.now, tone, title)

    # -- history --

    def history(self, tid: int) -> tuple[list[dict], int]:
        """The thing's events, newest first, with a save right after the create folded into
        making it (a new file is a create and then a close-write). Returns (events, total)."""
        rows = self.store.q("SELECT * FROM events WHERE thing = ? ORDER BY t DESC, id DESC LIMIT ?",
                            (tid, HISTORY_CAP))
        extra = 0
        if len(rows) == HISTORY_CAP:
            extra = self.store.one("SELECT COUNT(*) AS n FROM events WHERE thing = ?", (tid,))["n"] - HISTORY_CAP
        out: list[dict] = []
        for e in reversed(rows):
            e["te"] = e["t_end"] or e["t"]
            prev = out[-1] if out else None
            if (prev is not None and e["kind"] == "change" and prev["kind"] == "create"
                    and (e["actor"], e["actor_thing"]) == (prev["actor"], prev["actor_thing"])
                    and 0 <= e["t"] - prev["te"] <= COALESCE_S):
                prev["te"] = max(prev["te"], e["te"])
                continue
            out.append(e)
        out.reverse()
        return out, len(out) + extra

    def maker(self, thing: dict, hist: list[dict]) -> tuple[str | None, int | None, str, float | None]:
        """(actor, actor thing, via, when) of whoever made it, from the thing or its first event."""
        if thing["made_by"]:
            return thing["made_by"], thing["made_by_thing"], thing["made_via"] or "", thing["created"]
        first = next((e for e in reversed(hist) if e["kind"] in ("create", "install")), None)
        if first is not None:
            return first["actor"], first["actor_thing"], first["via"] or "", first["t"]
        if thing["kind"] in ("file", "folder", "project", "app", "package"):
            return "before", None, "", thing["created"]
        return None, None, "", thing["created"]

    # -- the thing itself --

    def focus(self, thing: dict, limit: int) -> dict:
        hist, total = self.history(thing["id"])
        return {"thing": self.thing(thing, hist),
                "preview": self.preview(thing),
                "slots": {"came_from": _slot(self.came_from(thing, hist), limit),
                          "read_with": _slot(self.read_with(thing, hist), limit),
                          "used_with": _slot(self.used_with(thing, hist), limit),
                          "changes": self.changes(thing, hist, total, limit)},
                "children": self.children(thing, 0, CHILDREN_LIMIT),
                "description": None}

    def thing(self, thing: dict, hist: list[dict]) -> dict:
        path = thing["path"]
        exists = thing["deleted"] is None and (os.path.lexists(path) if path else True)
        area = self.get(thing["area"]) if thing["area"] != thing["id"] else None
        return {"id": thing["id"], "ref": ref_of(thing), "kind": thing["kind"], "title": title_of(self.store, thing),
                "path": path, "url": thing["url"],
                "area": {"id": area["id"], "ref": ref_of(area), "title": title_of(self.store, area),
                         "kind": area["kind"]} if area else None,
                "size": thing["size"], "created": thing["created"], "changed": thing["changed"],
                "deleted": thing["deleted"], "private": bool(thing["private"]),
                "made": self.made(thing, hist), "last": self.last(thing, hist),
                "opened": opened_since_change(path) if thing["kind"] == "file" and exists else None,
                "exists": exists}

    def preview(self, thing: dict) -> dict:
        kind, path = thing["kind"], thing["path"]
        if thing["private"] or (path and rules.private(path, self.home)):
            return {"type": "none", "private": True}
        gone = thing["deleted"] is not None
        if kind == "file":
            if gone or not path or not os.path.isfile(path):
                return {"type": "none", "private": False}
            return {"type": _preview_type(path), "path": path, "private": False}
        if kind in ("folder", "project"):
            return {"type": "folder", "path": path, "private": False} if path and not gone and os.path.isdir(path) \
                else {"type": "none", "private": False}
        if kind == "app":
            return {"type": "app", "path": path, "private": False}
        if kind == "page":
            return {"type": "page", "url": thing["url"], "private": False}
        if kind == "turn":
            return {"type": "turn", "text": str((thing["meta"] or {}).get("summary") or ""), "private": False}
        if kind == "fact":
            return {"type": "fact", "text": thing["title"], "private": False}
        if kind == "package":
            version = (thing["meta"] or {}).get("version")
            return {"type": "package", "text": f"version {version}" if version else "", "private": False}
        if kind in ("session", "site"):
            return {"type": kind, "private": False}
        return {"type": "none", "private": False}

    # -- sentences --

    def made(self, thing: dict, hist: list[dict]) -> str:
        """How it came to be, in one sentence."""
        kind = thing["kind"]
        if kind == "turn":
            n, prompt = _turn_words(self.store, thing["id"])
            day = words.on_day(thing["created"], self.now)
            if n is None:
                return "The machine is working on it now."
            asked = f"the machine {words.quoted(prompt)}" if prompt else f"for turn {n}"
            return f"You asked {asked}{' ' + day if day else ''}."
        if kind == "session":
            first = self.store.one("SELECT MIN(t) AS t FROM events WHERE actor_thing = ?", (thing["id"],))["t"]
            return f"First seen {words.on_day(first, self.now)}." if first else ""
        if kind == "page":
            first = next((e for e in reversed(hist) if e["kind"] == "visit"), None)
            if first is not None:
                return f"You first read it {words.on_day(first['t'], self.now)}."
            dl = self.store.one("SELECT MIN(t) AS t FROM events WHERE kind = 'download' AND other = ?", (thing["id"],))
            return f"A download came from it {words.on_day(dl['t'], self.now)}." if dl["t"] else ""
        if kind == "site":
            r = self.store.one("SELECT COUNT(DISTINCT p.id) AS n, MAX(e.t) AS t FROM things p JOIN events e "
                               "ON e.thing = p.id AND e.kind = 'visit' WHERE p.area = ?", (thing["id"],))
            if not r["n"]:
                return ""
            return f"You have read {words.count(r['n'], 'page')} there, most recently {words.on_day(r['t'], self.now)}."
        if kind == "system":
            return "Packages, and the settings under /etc."
        download = self.download_sentence(thing, hist)
        if download:
            return download
        actor, actor_thing, via, t = self.maker(thing, hist)
        if actor is None:
            return ""
        w = self.who(actor, actor_thing, via)
        if actor == "before":
            return words.made_sentence(w, t, self.now)
        if kind == "package":
            return sentence("installed", "", w, t, self.now)
        if kind == "fact":
            return sentence("added", "", w, t, self.now)
        return words.made_sentence(w, t, self.now)

    def download_sentence(self, thing: dict, hist: list[dict]) -> str:
        """'Downloaded from rent-portal on Tuesday while you read “Lease renewal 2026”.'"""
        dl = next((e for e in hist if e["kind"] == "download"), None)
        if dl is None:
            return ""
        page = self.get(dl["other"])
        url = (page or {}).get("url") or (dl["detail"] or {}).get("url") or ""
        site = site_name(url) if url else ""
        day = words.on_day(dl["t"], self.now)
        s = "Downloaded" + (f" from {site}" if site else "") + (f" {day}" if day else "")
        if page is not None and page["title"] and page["title"] != page["url"]:
            s += f" while you read {words.quoted(page['title'])}"
        return s + "."

    def last(self, thing: dict, hist: list[dict]) -> str:
        """Who changed it last, when that was someone other than whoever made it."""
        actor, actor_thing, _, _ = self.maker(thing, hist)
        for e in hist:
            if e["kind"] not in ("change", "upgrade"):
                continue
            if (e["actor"], e["actor_thing"]) == (actor, actor_thing):
                return ""   # the maker's own later saves are not news
            w = self.who(e["actor"], e["actor_thing"], e["via"])
            if e["kind"] == "upgrade":
                return sentence("last upgraded", "", w, e["te"], self.now)
            return words.changed_sentence(w, e["te"], self.now)
        return ""

    def gone_sentence(self, thing: dict, hist: list[dict]) -> str:
        e = next((e for e in hist if e["kind"] in GONE and (e["kind"] != "move" or (e["detail"] or {}).get("hidden"))),
                 None)
        if e is None:
            return f"It was deleted {words.on_day(thing['deleted'], self.now)}."
        w = self.who(e["actor"], e["actor_thing"], e["via"])
        verb, tail = _phrase(e["kind"])
        if e["kind"] == "move":
            tail = f"to {os.path.basename(str((e['detail'] or {}).get('to') or '').rstrip('/'))}"
        return sentence(verb, tail, w, e["t"], self.now)

    def why(self, thing: dict) -> str:
        hist, _ = self.history(thing["id"])
        kind = thing["kind"]
        first = self.made(thing, hist)
        if kind in ("turn", "session"):
            second = self.work_sentence(thing)
        elif kind == "page":
            visits = [e for e in hist if e["kind"] == "visit"]
            downloads = self.store.one("SELECT COUNT(*) AS n FROM events WHERE kind = 'download' AND other = ?",
                                       (thing["id"],))["n"]
            if len(visits) > 1:
                second = f"Last read {words.on_day(visits[0]['t'], self.now)}."
            elif downloads and visits:
                second = f"{words.count(downloads, 'download')} came from it."
            else:
                second = ""
        elif thing["deleted"] is not None:
            second = self.gone_sentence(thing, hist)
        else:
            second = self.last(thing, hist)
            if not second and kind == "file" and opened_since_change(thing["path"]) is False:
                second = NOT_OPENED
            if not second and kind == "fact" and (thing["meta"] or {}).get("source"):
                second = f"It is a line in {os.path.basename(str(thing['meta']['source']))}."
        return " ".join(s for s in (first, second) if s) or "The brain knows it, but not where it came from."

    def work_sentence(self, thing: dict) -> str:
        """'It made 2 things and changed 3 others.' for a turn or a session."""
        r = self.store.one("SELECT COUNT(DISTINCT CASE WHEN kind IN ('create', 'install') THEN thing END) AS made, "
                           "COUNT(DISTINCT thing) AS total FROM events WHERE actor_thing = ?", (thing["id"],))
        made, other = r["made"] or 0, (r["total"] or 0) - (r["made"] or 0)
        if made and other:
            return f"It made {words.count(made, 'thing')} and changed {words.count(other, 'other')}."
        if made:
            return f"It made {words.count(made, 'thing')}."
        if other:
            return f"It changed {words.count(other, 'thing')}."
        return ""

    # -- left: where it came from --

    def came_from(self, thing: dict, hist: list[dict]) -> list[dict]:
        lines: list[tuple[float, int, dict]] = []
        downloaded = any(e["kind"] == "download" and e["other"] for e in hist)
        actors: dict[tuple, dict] = {}
        for e in hist:
            actor = e["actor"] or "unknown"
            if actor not in ACTORS or e["kind"] not in VERBS or e["kind"] in ("visit", "download"):
                continue
            if downloaded and actor == "you" and e["kind"] == "create":
                continue   # the browser writing a download: the page's line says it better
            key = (actor, e["actor_thing"] if actor in ("turn", "session", "app") else None)
            a = actors.setdefault(key, {"t": e["te"], "kind": e["kind"], "via": e["via"], "made": False, "n": 0})
            a["made"] = a["made"] or e["kind"] in ("create", "install")
            a["n"] += e["kind"] == "change"
        made_by = thing["made_by"]
        if made_by in ACTORS:
            key = (made_by, thing["made_by_thing"] if made_by in ("turn", "session", "app") else None)
            if key not in actors and not (downloaded and made_by == "you"):
                # Known from its xattr after a rebuild: no event, but still who made it.
                actors[key] = {"t": thing["created"], "kind": "create", "via": thing["made_via"], "made": True, "n": 0}
        for (actor, actor_thing), a in actors.items():
            w = self.who(actor, actor_thing, a["via"])
            if a["made"]:
                what = _did("create", thing["kind"]) if thing["kind"] != "package" else "installed this"
            elif a["n"] > 1:
                what = f"changed this {a['n']} times"
            else:
                what = _did(a["kind"], thing["kind"])
            if actor == "turn" and w.prompt:
                what = words.quoted(w.prompt)
            when = self.when(a["t"])
            why = f"{what}, {when}" if when else what
            title = words.who(replace(w, prompt="")) if actor == "turn" else words.who(w)
            actor_row = self.get(actor_thing)
            if actor_row is not None:
                line = self.line(actor_row, why, a["t"], title=title)
            else:
                line = {"id": None, "ref": None, "title": title, "kind": actor, "why": why, "t": a["t"],
                        "when": when, "tone": "normal"}
            lines.append((a["t"] or 0, 0, line))
        for link in self.store.links_from(thing["id"], ("came_from",)):
            src = self.get(link["dst"])
            if src is None or src["forgotten"]:
                continue
            if src["kind"] == "page":
                dl = next((e for e in hist if e["kind"] == "download" and e["other"] == src["id"]), None)
                t = dl["t"] if dl else link["last"]
                what = f"downloaded from {site_name(src['url'] or '')}"
                if dl and src["title"] and src["title"] != src["url"]:
                    what += " while you read it"
            else:
                t = link["last"]
                n = self.turn_ended_at(link["last"])
                what = f"read by turn {n} before writing this" if n else "this was made from it"
            when = self.when(t)
            lines.append((t or 0, 1, self.line(src, f"{what}, {when}" if when else what, t)))
        lines.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [x[2] for x in lines]

    def turn_ended_at(self, t: float | None) -> int | None:
        """The turn whose row made a came_from link: turn_row links at the turn's end."""
        if t is None:
            return None
        row = self.store.one("SELECT n FROM turns WHERE ended = ? ORDER BY n DESC LIMIT 1", (t,))
        return row["n"] if row else None

    # -- right: read with --

    def read_with(self, thing: dict, hist: list[dict]) -> list[dict]:
        if thing["kind"] == "page":
            return self.changed_while_open(thing, hist)
        changes = [e for e in hist if e["kind"] in ("create", "change")][:200]
        pages: dict[int, dict] = {}
        for e in changes:
            seen = set()
            for v in self.store.q("SELECT thing, t, detail FROM events WHERE kind = 'visit' AND t BETWEEN ? AND ?",
                                  (e["t"] - READ_WINDOW_S, e["te"])):
                duration = _duration(v)
                if (duration and v["t"] + duration < e["t"] - STILL_OPEN_S) or v["thing"] in seen:
                    continue
                seen.add(v["thing"])
                p = pages.setdefault(v["thing"], {"n": 0, "t": 0.0, "e": e})   # changes run newest first
                p["n"] += 1
                p["t"] = max(p["t"], v["t"])
        rows = self.many(pages)
        out = []
        for pid, p in sorted(pages.items(), key=lambda kv: (kv[1]["n"], kv[1]["t"]), reverse=True):
            page = rows.get(pid)
            if page is None or page["forgotten"]:
                continue
            e = p["e"]
            out.append(self.line(page, f"open while {self.editing(e)} this", p["t"]))
        return out

    def editing(self, e: dict) -> str:
        """'builder edited', 'you changed': who was at work, for the read-with lines."""
        w = self.who(e["actor"], e["actor_thing"], e["via"])
        if w.kind == "session":
            return f"{short(w)} edited"
        name = short(w) or "someone"
        return f"{'the machine' if w.kind == 'turn' else name} changed"

    def changed_while_open(self, page: dict, hist: list[dict]) -> list[dict]:
        """A page in the middle: the things that changed while it was open."""
        found: dict[int, dict] = {}
        for v in [e for e in hist if e["kind"] == "visit"][:200]:
            duration = _duration(v)
            end = v["t"] + READ_WINDOW_S
            if duration:
                end = min(end, v["t"] + duration + STILL_OPEN_S)
            seen = set()
            for e in self.store.q("SELECT * FROM events WHERE kind IN ('create', 'change') AND t <= ? "
                                  "AND COALESCE(t_end, t) >= ? ORDER BY t DESC", (end, v["t"])):
                if e["thing"] in seen:
                    continue
                seen.add(e["thing"])
                f = found.setdefault(e["thing"], {"n": 0, "t": 0.0, "e": e})
                f["n"] += 1
                f["t"] = max(f["t"], e["t_end"] or e["t"])
        rows = self.many(found)
        out = []
        for tid, f in sorted(found.items(), key=lambda kv: (kv[1]["n"], kv[1]["t"]), reverse=True):
            thing = rows.get(tid)
            if thing is None or thing["forgotten"] or thing["kind"] in ("turn", "session", "page", "site"):
                continue
            out.append(self.line(thing, f"{self.editing(f['e'])} it while this was open", f["t"]))
        return out

    # -- below: used with --

    def occasions(self, hist: list[dict]) -> list[dict]:
        """The times this thing changed, as occasions: a turn is one, and your or a session's
        saves no more than 20 minutes apart are one. Newest first."""
        out: list[dict] = []
        turns: set[int] = set()
        open_: dict[tuple, dict] = {}
        for e in reversed(hist):
            if e["kind"] not in ("create", "change") or e["actor"] not in ("turn", "session", "app", "you"):
                continue
            if e["actor"] == "turn":
                if e["actor_thing"] is not None and e["actor_thing"] not in turns:
                    turns.add(e["actor_thing"])
                    out.append({"actor": "turn", "thing": e["actor_thing"], "t0": e["t"], "t1": e["te"]})
                continue
            key = (e["actor"], e["actor_thing"])
            occ = open_.get(key)
            if occ is not None and e["t"] - occ["t1"] <= TOGETHER_S:
                occ["t1"] = max(occ["t1"], e["te"])
                continue
            occ = open_[key] = {"actor": e["actor"], "thing": e["actor_thing"], "t0": e["t"], "t1": e["te"]}
            out.append(occ)
        out.sort(key=lambda o: o["t1"], reverse=True)
        return out[:OCCASIONS]

    def used_with(self, thing: dict, hist: list[dict]) -> list[dict]:
        tid = thing["id"]
        shared: dict[int, dict] = {}
        for occ in self.occasions(hist):
            if occ["actor"] == "turn":
                rows = self.store.q("SELECT thing, MAX(COALESCE(t_end, t)) AS te FROM events WHERE actor_thing = ? "
                                    "AND kind IN ('create', 'change') AND thing != ? GROUP BY thing",
                                    (occ["thing"], tid))
            else:
                rows = self.store.q("SELECT thing, MAX(COALESCE(t_end, t)) AS te FROM events WHERE actor = ? "
                                    "AND actor_thing IS ? AND kind IN ('create', 'change') AND t <= ? "
                                    "AND COALESCE(t_end, t) >= ? AND thing != ? GROUP BY thing",
                                    (occ["actor"], occ["thing"], occ["t1"] + TOGETHER_S, occ["t0"] - TOGETHER_S, tid))
            for r in rows:
                s = shared.setdefault(r["thing"], {"n": 0, "turns": 0, "t": 0.0})
                s["n"] += 1
                s["turns"] += occ["actor"] == "turn"
                s["t"] = max(s["t"], r["te"] or 0)
        lines: list[tuple[tuple, dict]] = []
        made_from = self.store.links_to(tid, ("came_from",))
        rows = self.many([r["src"] for r in made_from] + [k for k, s in shared.items() if s["n"] >= 2])
        done = set()
        for link in made_from:
            src = rows.get(link["src"])
            if src is None or src["forgotten"] or src["id"] in done:
                continue
            done.add(src["id"])
            if thing["kind"] == "page":
                what = "downloaded from this"
            else:
                n = self.turn_ended_at(link["last"])
                if n is None:
                    maker = self.get(src["made_by_thing"]) if src["made_by"] == "turn" else None
                    n = _turn_words(self.store, maker["id"])[0] if maker else None
                what = f"made from this in turn {n}" if n else "made from this"
            lines.append(((1, link["last"] or 0, 0), self.busy(src, what, link["last"])))
        for co, s in shared.items():
            other = rows.get(co)
            if s["n"] < 2 or other is None or other["forgotten"] or co in done:
                continue
            if other["kind"] in ("turn", "session"):
                continue
            what = (f"changed in the same {words.count(s['n'], 'turn')}" if s["turns"] == s["n"]
                    else f"changed with this {s['n']} times")
            lines.append(((0, s["n"], s["t"]), self.busy(other, what, s["t"])))
        # Someone at work on it right now first, then what was made from this, then the
        # most shared.
        lines.sort(key=lambda x: (x[1]["tone"] == "amber", *x[0]), reverse=True)
        return [x[1] for x in lines]

    def busy(self, other: dict, why: str, t: float | None) -> dict:
        """The line, in amber when another turn or session changed it in the last 15 minutes."""
        row = self.store.one(
            "SELECT actor, actor_thing, via, MAX(COALESCE(t_end, t)) AS te FROM events WHERE thing = ? "
            "AND actor IN ('turn', 'session') AND actor_thing IS NOT NULL AND actor_thing != ? "
            "AND COALESCE(t_end, t) >= ?", (other["id"], self.looking or -1, self.now - BUSY_S))
        if row is None or row["te"] is None:
            return self.line(other, why, t)
        w = self.who(row["actor"], row["actor_thing"], row["via"])
        return self.line(other, f"also being changed by {short(w)}", row["te"], tone="amber")

    # -- top: changes --

    def changes(self, thing: dict, hist: list[dict], total: int, limit: int) -> dict:
        lt = time.localtime(self.now)
        starts = [time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - k, 0, 0, 0, 0, 0, -1)) for k in range(7)]
        days = [0] * 7
        for e in hist:
            for k, start in enumerate(starts):
                if e["te"] >= start:
                    days[6 - k] += 1
                    break
            else:
                break   # newest first: everything after this is older than the week
        items = []
        for e in hist[:limit]:
            w = self.who(e["actor"], e["actor_thing"], e["via"])
            verb, tail = _phrase(e["kind"], thing["kind"])
            if e["kind"] == "move" and (e["detail"] or {}).get("hidden"):
                tail = f"to {os.path.basename(str(e['detail'].get('to') or '').rstrip('/'))}"
            by = words.by(w) or "while the brain was not watching"
            items.append({"id": e["id"], "kind": e["kind"], "t": e["te"], "when": self.when(e["te"]),
                          "why": " ".join(b for b in (verb, tail, by) if b)})
        return {"count_week": sum(days), "days": days, "items": items, "more": max(0, total - len(items))}

    # -- the middle, for things that hold others --

    def children(self, thing: dict, offset: int, limit: int) -> dict | None:
        kind, path = thing["kind"], thing["path"]
        if kind in ("folder", "project", "app") and path:
            if thing["deleted"] is None and os.path.isdir(path):
                return self.disk(thing, offset, limit)
            return self.remembered(thing, offset, limit)
        if kind in ("site", "system"):
            return self.area_things(thing, offset, limit)
        if kind in ("turn", "session", "app"):
            return self.done_by(thing, offset, limit)
        return None

    def disk(self, thing: dict, offset: int, limit: int) -> dict:
        """The folder as it is on disk now, folders first then newest first, with who made or
        last changed each entry when the brain saw it."""
        path = thing["path"].rstrip("/") or "/"
        private = bool(thing["private"]) or rules.private(path, self.home)
        entries = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if len(entries) >= SCAN_LIMIT:
                        break
                    full = os.path.join(path, entry.name)
                    verdict = rules.classify(full, self.home)
                    if entry.name.startswith(".") or verdict.kind == "skip":
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        mtime = entry.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue   # gone between the listing and the look
                    entries.append((entry.name, full, is_dir, mtime, verdict.private))
        except OSError:
            return {"items": [], "total": 0}
        entries.sort(key=lambda x: (not x[2], -x[3], x[0].casefold()))
        page = entries[offset:offset + limit]
        known = {}
        for n in range(0, len(page), 500):
            chunk = [p[1] for p in page[n:n + 500]]
            for row in self.store.q(f"SELECT * FROM things WHERE deleted IS NULL AND path IN "
                                    f"({', '.join('?' for _ in chunk)})", chunk):
                known[row["path"]] = row
        writers = self.last_writers([t["id"] for t in known.values()])
        items = []
        for name, full, is_dir, mtime, named_private in page:
            t = known.get(full)
            item_private = private or named_private or bool(t and t["private"])
            kind = t["kind"] if t else ("folder" if is_dir else "file")
            if private:
                items.append({"id": t["id"] if t else None, "ref": full, "name": name, "kind": kind, "t": None,
                              "when": "", "who": "", "private": True})
                continue
            who = ""
            if t is not None and is_dir:
                if t["made_by"] in ACTORS:
                    who = short(self.who(t["made_by"], t["made_by_thing"], t["made_via"]))
            elif t is not None:
                last = writers.get(t["id"])
                # Only when the brain saw the write that left the file as it is now.
                if last is not None and last["te"] >= mtime - 5:
                    who = short(self.who(last["actor"], last["actor_thing"], last["via"]))
            items.append({"id": t["id"] if t else None, "ref": full, "name": name, "kind": kind, "t": mtime,
                          "when": self.when(mtime), "who": who, "private": item_private})
        return {"items": items, "total": len(entries)}

    def last_writers(self, ids: list[int]) -> dict[int, dict]:
        out = {}
        for n in range(0, len(ids), 500):
            chunk = ids[n:n + 500]
            for row in self.store.q(
                    f"SELECT thing, actor, actor_thing, via, MAX(COALESCE(t_end, t)) AS te FROM events "
                    f"WHERE thing IN ({', '.join('?' for _ in chunk)}) AND kind IN ('create', 'change', 'download') "
                    f"GROUP BY thing", chunk):
                out[row["thing"]] = row
        return out

    def remembered(self, thing: dict, offset: int, limit: int) -> dict:
        """A folder that is gone: what the brain remembers was in it."""
        rows = self.store.children(thing["id"], live=thing["deleted"] is None, limit=limit, offset=offset)
        live = "AND deleted IS NULL" if thing["deleted"] is None else ""
        total = self.store.one(f"SELECT COUNT(*) AS n FROM things WHERE parent = ? AND forgotten = 0 {live}",
                               (thing["id"],))["n"]
        private = bool(thing["private"])
        items = []
        for r in rows:
            t = r["changed"] or r["created"]
            who = short(self.who(r["made_by"], r["made_by_thing"], r["made_via"])) if r["made_by"] in ACTORS else ""
            items.append({"id": r["id"], "ref": ref_of(r), "name": title_of(self.store, r), "kind": r["kind"],
                          "t": None if private else t, "when": "" if private else self.when(t),
                          "who": "" if private else who, "private": private or bool(r["private"])})
        return {"items": items, "total": total}

    def area_things(self, thing: dict, offset: int, limit: int) -> dict:
        """A site's pages, or the System's packages and /etc files, most recently touched first."""
        extra = "AND kind = 'page'" if thing["kind"] == "site" else ""
        rows = self.store.q(f"SELECT * FROM things WHERE area = ? AND id != ? AND forgotten = 0 {extra} "
                            f"ORDER BY COALESCE(touched, changed, created, 0) DESC LIMIT ? OFFSET ?",
                            (thing["id"], thing["id"], limit, offset))
        total = self.store.one(f"SELECT COUNT(*) AS n FROM things WHERE area = ? AND id != ? AND forgotten = 0 "
                               f"{extra}", (thing["id"], thing["id"]))["n"]
        items = []
        for r in rows:
            t = r["touched"] or r["changed"] or r["created"]
            items.append({"id": r["id"], "ref": ref_of(r), "name": title_of(self.store, r), "kind": r["kind"],
                          "t": t, "when": self.when(t), "who": "", "private": bool(r["private"])})
        return {"items": items, "total": total}

    def done_by(self, actor: dict, offset: int, limit: int) -> dict:
        """What a turn, session or app made or changed, newest first; who says what it did."""
        rows = self.store.q("SELECT thing, kind, MAX(COALESCE(t_end, t)) AS te, "
                            "SUM(kind IN ('create', 'install')) AS made FROM events WHERE actor_thing = ? "
                            "GROUP BY thing ORDER BY te DESC", (actor["id"],))
        things = self.many([r["thing"] for r in rows])
        # An app changing its own data is recorded on the app itself: not something it made.
        rows = [r for r in rows if r["thing"] in things and r["thing"] != actor["id"]
                and not things[r["thing"]]["forgotten"]]
        items = []
        for r in rows[offset:offset + limit]:
            t = things[r["thing"]]
            verb = "made" if r["made"] else _phrase(r["kind"], t["kind"])[0]
            if t["kind"] == "package" and r["made"]:
                verb = "installed"
            items.append({"id": t["id"], "ref": ref_of(t), "name": title_of(self.store, t), "kind": t["kind"],
                          "t": r["te"], "when": self.when(r["te"]), "who": verb, "private": bool(t["private"])})
        return {"items": items, "total": len(rows)}


def _slot(lines: list[dict], limit: int) -> dict:
    return {"items": lines[:limit], "more": max(0, len(lines) - limit)}


def _duration(visit: dict) -> float:
    try:
        return float((visit["detail"] or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0

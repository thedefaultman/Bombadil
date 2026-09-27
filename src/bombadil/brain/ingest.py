"""From what was witnessed to things and links.

The watcher reports saves, creates, renames and deletes with the writer; agentd's turn
rows say which files a turn wrote and read; Chromium's History says which page a
download came from; pacman.log says what was installed. Each becomes events on things,
and the strong links follow from the events: made_by and changed_by from who wrote,
came_from from a download's page or a turn that read one file and wrote another.

Identity follows the file, not its name: a rename or a move keeps the thing (and
everything inside a folder), and an editor's save-by-rename (write a temp file, rename it
over the old one) keeps the old thing, because the rename lands on a path that already
has one. An editor that deletes and writes again within a few seconds keeps it too.

Who made a file is also written on the file, as user.bombadil.made_by (and, for a
download, user.bombadil.origin), so it survives a move, a copy that keeps xattrs and a
rebuild of brain.db.
"""

import hashlib
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from . import actors, rules
from .store import Store

# An editor that deletes and writes the file again keeps the same thing within this window.
REVIVE_S = 10.0
# A file that lived this briefly with one writer was scaffolding (a build's temp file with an
# ordinary name): when it goes, it goes from the brain too.
EPHEMERAL_S = 30.0
XATTR_MADE_BY = "user.bombadil.made_by"
XATTR_ORIGIN = "user.bombadil.origin"
# Special files that feed witnesses rather than being things themselves.
SKIP_URL_SCHEMES = ("chrome", "chrome-extension", "about", "data", "blob", "javascript", "devtools", "file",
                    "view-source", "chrome-search", "chrome-untrusted", "edge")


@dataclass(frozen=True)
class Who:
    """An actor as stored: kind, the thing that stands for it (turn, session, app), program."""
    kind: str
    thing: int | None = None
    via: str = ""


BEFORE = Who("before")
UNKNOWN = Who("unknown")


def fingerprint_url(url: str) -> str | None:
    """The page's key: the URL without its fragment. None for what is not a web page."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def site_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


class Ingest:
    def __init__(self, store: Store, home: str, sessions: actors.Sessions | None = None,
                 git: rules.GitIgnore | None = None, xattrs: bool = True):
        self.store = store
        self.home = home.rstrip("/")
        self.sessions = sessions
        self.git = git or rules.GitIgnore()
        self.xattrs = xattrs
        self.changed: set[int] = set()           # things touched since the service last pushed
        self.specials: dict[str, object] = {}    # exact path -> callback(ev, who): witnesses' files
        self._ignored: set[str] = set()

    # -- batches from the watcher --

    def apply(self, events: list[dict]) -> list[str]:
        """Apply a batch of watcher events in one transaction. Returns the control ops seen
        (hello, overflow, caught_up) for the service to act on."""
        control = []
        candidates = []
        for ev in events:
            for key in ("path", "old"):
                p = ev.get(key)
                if isinstance(p, str) and rules.classify(p, self.home).kind == "thing":
                    candidates.append(p)
        self._ignored = self.git.ignored(candidates, stop=self.home) if candidates else set()
        with self.store.tx():
            for ev in events:
                op = ev.get("op")
                if op in ("hello", "overflow", "caught_up", "pong"):
                    control.append(op)
                    continue
                try:
                    self.watch_event(ev)
                except (KeyError, TypeError, ValueError):
                    continue   # one malformed line never costs the batch
        return control

    def watch_event(self, ev: dict) -> None:
        op = ev.get("op")
        path = ev.get("path")
        if not isinstance(path, str):
            return
        t = float(ev.get("t") or time.time())
        who = self.who(actors.from_event(ev, self.sessions))
        special = self.specials.get(path)
        if special is not None:
            special(ev, who)
        if path.endswith("/.gitignore"):
            repo = os.path.dirname(path)
            self.git.forget(repo)
        if op == "rename":
            old = ev.get("old")
            if isinstance(old, str):
                special = self.specials.get(old)
                if special is not None:
                    special(ev, who)
                self.renamed(old, path, t, who, bool(ev.get("dir")), ev)
        elif op == "delete":
            self.deleted(path, t, who)
        elif op in ("create", "write", "offline"):
            self.saw(path, op, t, who, bool(ev.get("dir")), size=ev.get("size"), ino=ev.get("ino"))

    # -- who --

    def who(self, actor: actors.Actor) -> Who:
        if actor.kind == "turn":
            return Who("turn", self.turn_thing_for_unit(actor.key.removeprefix("unit:")), actor.via)
        if actor.kind == "session":
            rest = actor.key.removeprefix("session:")
            project, _, name = rest.partition("/")
            title = f"{name} on {project}" if name else f"a coding session on {project}"
            tid = self.store.upsert_key("session", actor.key, title, meta={"project": project, "name": name})
            return Who("session", tid, actor.via)
        if actor.kind == "app":
            return Who("app", self.app_thing(actor.key.removeprefix("app:")), actor.via)
        if actor.kind == "you":
            return Who("you", None, actor.via)
        return Who(actor.kind, None, actor.via)

    def turn_thing_for_unit(self, unit: str) -> int:
        row = self.store.turn_by_unit(unit)
        if row is not None and row["thing"]:
            return row["thing"]
        # Its row in turns.jsonl comes when the turn ends; until then it is "a turn".
        return self.store.upsert_key("turn", f"unit:{unit}", "a turn now running")

    def app_thing(self, name: str) -> int:
        path = f"{self.home}/Apps/{name}"
        have = self.store.by_key(f"app:{name}")
        if have is not None:
            return have["id"]
        live = self.store.by_path(path)
        if live is not None:
            self.store.update(live["id"], key=f"app:{name}", kind="app")
            return live["id"]
        return self.store.add("app", _app_title(path, name), key=f"app:{name}",
                              path=path if os.path.isdir(path) else None,
                              parent=self.folder(f"{self.home}/Apps") if os.path.isdir(path) else None)

    # -- folders and areas --

    def folder(self, path: str) -> int | None:
        """The thing for a folder, made (with the folders above it) if new."""
        path = path.rstrip("/") or "/"
        have = self.store.by_path(path)
        if have is not None:
            return have["id"]
        in_home = path == self.home or path.startswith(self.home + "/")
        in_etc = path == "/etc" or path.startswith("/etc/")
        if not in_home and not in_etc:
            return None
        kind = rules.kind_of_folder(path, self.home) if in_home else "folder"
        parent = None if path in (self.home, "/etc") else self.folder(os.path.dirname(path))
        if kind == "app":
            name = os.path.basename(path)
            have = self.store.by_key(f"app:{name}")
            if have is not None:
                self.store.update(have["id"], path=path, parent=parent)
                tid = have["id"]
            else:
                tid = self.store.add("app", _app_title(path, name), key=f"app:{name}", path=path, parent=parent)
        else:
            title = "Home" if path == self.home else os.path.basename(path)
            tid = self.store.add(kind, title, path=path, parent=parent,
                                 private=int(rules.private(path, self.home)))
        area = self.area(path, is_dir=True, self_id=tid)
        if area is not None:
            self.store.update(tid, area=area)
        return tid

    def area(self, path: str, is_dir: bool = False, self_id: int | None = None) -> int | None:
        a = rules.area_of(path, self.home, is_dir, is_project=lambda d: os.path.isdir(os.path.join(d, ".git")))
        if a is None:
            return None
        if a.kind == "system":
            return self.store.upsert_key("system", "system", "System")
        if a.path == path.rstrip("/"):
            return self_id
        return self.folder(a.path)

    # -- files --

    def saw(self, path: str, op: str, t: float, who: Who, is_dir: bool = False, size=None, ino=None) -> int | None:
        """A create, a save, or (offline) a write found after the fact."""
        v = rules.classify(path, self.home)
        if v.kind == "rollup":
            app = self.app_thing(v.app)
            ev = self.store.event(t, "change", app, who.kind, who.thing, who.via, detail={"data": True})
            self.store.touch(app, t, changed=True)
            self._changed_by(app, who, t, ev)
            self.changed.add(app)
            return app
        if v.kind != "thing" or path in self._ignored:
            return None
        thing = self.store.by_path(path)
        if thing is None:
            dead = self.store.by_path(path, deleted=True)
            if dead is not None and dead["deleted"] and 0 <= t - dead["deleted"] <= REVIVE_S \
                    and self.store.revive(dead["id"]):
                thing = self.store.get(dead["id"])
        if thing is None:
            if op == "offline":
                who = UNKNOWN
            # A save of something the brain never saw made was there before (the first index
            # has not reached it yet), so who made it is not known; the save is still theirs.
            made = BEFORE if op == "write" else who
            if is_dir:
                tid = self.folder(path)
                self.store.update(tid, created=t, made_by=made.kind, made_by_thing=made.thing, made_via=made.via)
            else:
                tid = self.store.add("file", os.path.basename(path), path=path,
                                     parent=self.folder(os.path.dirname(path)), area=self.area(path),
                                     created=t, changed=t, touched=t, touches=1, made_by=made.kind,
                                     made_by_thing=made.thing, made_via=made.via, private=int(v.private),
                                     size=size, ino=ino)
            ev = self.store.event(t, "create" if op != "write" else "change", tid, who.kind, who.thing, who.via)
            if who.thing is not None and op != "write":
                self.store.link(tid, who.thing, "made_by", t, ev)
                self._write_made_by(path, tid)
            elif op == "write":
                self._changed_by(tid, who, t, ev)
            self.changed.add(tid)
            return tid
        tid = thing["id"]
        fields = {}
        if size is not None:
            fields["size"] = size
        if ino is not None:
            fields["ino"] = ino
        if fields:
            self.store.update(tid, **fields)
        self.store.touch(tid, t, changed=True)
        if op == "create" and thing["kind"] in ("folder", "project", "app"):
            return tid   # mkdir -p on a folder that exists
        ev = self.store.event(t, "change", tid, who.kind, who.thing, who.via)
        self._changed_by(tid, who, t, ev, thing)
        self.changed.add(tid)
        return tid

    def _changed_by(self, tid: int, who: Who, t: float, ev: int, thing: dict | None = None) -> None:
        if who.thing is None:
            return
        thing = thing or self.store.get(tid)
        if thing is not None and thing["made_by_thing"] == who.thing:
            return
        self.store.link(tid, who.thing, "changed_by", t, ev)

    def renamed(self, old: str, new: str, t: float, who: Who, is_dir: bool, ev: dict | None = None) -> None:
        vo, vn = rules.classify(old, self.home), rules.classify(new, self.home)
        thing = self.store.by_path(old)
        if vn.kind == "trash":
            if thing is not None:
                self.store.mark_deleted(thing["id"], t)
                self.store.event(t, "trash", thing["id"], who.kind, who.thing, who.via, detail={"to": new})
                self.changed.add(thing["id"])
            return
        new_ok = vn.kind in ("thing", "rollup") and new not in self._ignored
        if thing is None or vo.kind != "thing":
            if new_ok:
                # A temp file renamed into place, a download finishing, a move in from out of
                # sight: to the brain it is the file appearing (or being saved) at `new`.
                self.saw(new, "write" if self.store.by_path(new) else "create", t, who, is_dir,
                         size=(ev or {}).get("size"), ino=(ev or {}).get("ino"))
            return
        if not new_ok:
            # Moved out of sight (a backup name like file~, into node_modules): gone for now.
            # An editor writing the file again right after revives it.
            self.store.mark_deleted(thing["id"], t)
            self.store.event(t, "move", thing["id"], who.kind, who.thing, who.via, detail={"to": new, "hidden": True})
            self.changed.add(thing["id"])
            return
        target = self.store.by_path(new)
        if target is not None and target["id"] != thing["id"]:
            # Save-by-rename: the file already at `new` keeps its identity, with a new version.
            self._gone(thing, t, who)
            self.saw(new, "write", t, who, is_dir, size=(ev or {}).get("size"), ino=(ev or {}).get("ino"))
            return
        parent = self.folder(os.path.dirname(new))
        self.store.move(thing["id"], new, parent, self.area(new, is_dir))
        if thing["kind"] in ("folder", "project", "app"):
            kind = rules.kind_of_folder(new, self.home)
            if kind != thing["kind"] and kind != "app" and thing["kind"] != "app":
                self.store.update(thing["id"], kind=kind)
        e = self.store.event(t, "move", thing["id"], who.kind, who.thing, who.via, detail={"from": old})
        self.store.touch(thing["id"], t)
        self._changed_by(thing["id"], who, t, e)
        self.changed.add(thing["id"])

    def deleted(self, path: str, t: float, who: Who) -> None:
        thing = self.store.by_path(path)
        if thing is None:
            return
        self._gone(thing, t, who)

    def _gone(self, thing: dict, t: float, who: Who) -> None:
        if self._ephemeral(thing, t):
            self.purge(thing["id"])
            return
        self.store.mark_deleted(thing["id"], t)
        self.store.event(t, "delete", thing["id"], who.kind, who.thing, who.via)
        self.changed.add(thing["id"])

    def _ephemeral(self, thing: dict, t: float) -> bool:
        if thing["kind"] != "file" or thing["created"] is None or t - thing["created"] > EPHEMERAL_S:
            return False
        evs = self.store.events(thing["id"], limit=20)
        return (bool(evs) and all(e["kind"] in ("create", "change") for e in evs)
                and len({(e["actor"], e["actor_thing"]) for e in evs}) == 1
                and not self.store.links_to(thing["id"]))

    def purge(self, thing_id: int) -> None:
        """Remove a thing and its history entirely (scaffolding, or "forget this")."""
        with self.store.tx():
            self.store.x("DELETE FROM events WHERE thing = ?", (thing_id,))
            self.store.x("DELETE FROM links WHERE src = ? OR dst = ?", (thing_id, thing_id))
            self.store.x("DELETE FROM descriptions WHERE thing = ?", (thing_id,))
            self.store.x("DELETE FROM search WHERE rowid = ?", (thing_id,))
            self.store.x("DELETE FROM things WHERE id = ?", (thing_id,))
        self.changed.discard(thing_id)

    # -- the first index and catch-up walks --

    def found(self, path: str, st: os.stat_result, is_dir: bool) -> int | None:
        """A thing found on disk by a walk (the first index, a rebuild, catch-up). Who made it
        comes from its xattr when it has one, else it was there before the brain."""
        v = rules.classify(path, self.home)
        if v.kind != "thing":
            return None
        have = self.store.by_path(path)
        if have is not None:
            if not is_dir and (have["size"] != st.st_size or have["ino"] != st.st_ino):
                self.store.update(have["id"], size=st.st_size, ino=st.st_ino)
            return have["id"]
        if is_dir:
            tid = self.folder(path)
            self.store.update(tid, created=st.st_mtime, changed=st.st_mtime, ino=st.st_ino,
                              made_by="before")
            return tid
        made, origin = self._read_xattrs(path)
        tid = self.store.add("file", os.path.basename(path), path=path, parent=self.folder(os.path.dirname(path)),
                             area=self.area(path), created=st.st_mtime, changed=st.st_mtime,
                             touched=st.st_mtime, made_by=made.kind, made_by_thing=made.thing, made_via=made.via,
                             private=int(v.private), size=st.st_size, ino=st.st_ino)
        if made.thing is not None:
            self.store.link(tid, made.thing, "made_by", st.st_mtime)
        if origin:
            page = self.page(origin, "", st.st_mtime)
            if page is not None:
                self.store.link(tid, page, "came_from", st.st_mtime)
        return tid

    def _read_xattrs(self, path: str) -> tuple[Who, str]:
        try:
            made = os.getxattr(path, XATTR_MADE_BY, follow_symlinks=False).decode(errors="replace")
        except OSError:
            made = ""
        try:
            origin = os.getxattr(path, XATTR_ORIGIN, follow_symlinks=False).decode(errors="replace")
        except OSError:
            origin = ""
        return self._who_from_label(made), origin

    def _who_from_label(self, label: str) -> Who:
        """The inverse of _label: 'turn 41', 'session bombadil/builder', 'app passwords', 'you'."""
        kind, _, rest = label.partition(" ")
        if kind == "turn" and rest.isdigit():
            n = int(rest)
            row = self.store.turn(n)
            tid = row["thing"] if row else self.store.upsert_key("turn", f"turn:{n}", f"turn {n}")
            if row is None:
                self.store.set_turn(n, tid)
            return Who("turn", tid)
        if kind == "session" and rest:
            project, _, name = rest.partition("/")
            return Who("session", self.store.upsert_key("session", f"session:{rest}",
                                                         f"{name} on {project}" if name else project))
        if kind == "app" and rest:
            return Who("app", self.app_thing(rest))
        if kind == "you":
            return Who("you")
        return BEFORE

    def _label(self, tid: int) -> str:
        thing = self.store.get(tid)
        if thing is None:
            return ""
        key = thing["key"] or ""
        if key.startswith("turn:"):
            return "turn " + key[5:]
        if key.startswith("session:"):
            return "session " + key[8:]
        if key.startswith("app:"):
            return "app " + key[4:]
        return ""

    def _write_made_by(self, path: str, tid: int) -> None:
        thing = self.store.get(tid)
        if not self.xattrs or thing is None or thing["kind"] != "file" or thing["made_by_thing"] is None:
            return
        label = self._label(thing["made_by_thing"])
        if label:
            _setxattr(path, XATTR_MADE_BY, label)

    # -- turns --

    def turn(self, n: int, unit: str | None = None, prompt: str | None = None, started: float | None = None,
             ended: float | None = None, meta: dict | None = None) -> int:
        """The thing for turn n, merged with the one its scope made while it ran."""
        key = f"turn:{n}"
        with self.store.tx():
            by_n = self.store.by_key(key)
            by_unit = self.store.by_key(f"unit:{unit}") if unit else None
            if by_unit is not None and by_n is not None and by_unit["id"] != by_n["id"]:
                self.store.merge_things(by_n["id"], by_unit["id"])
            elif by_unit is not None and by_n is None:
                self.store.update(by_unit["id"], key=key)
            title = " ".join(str(prompt or "").split())[:120] or f"turn {n}"
            tid = self.store.upsert_key("turn", key, title if prompt else "", created=started,
                                        changed=ended, touched=ended or started, meta=meta)
            if not prompt and self.store.get(tid)["title"] in ("a turn now running", ""):
                self.store.update(tid, title=f"turn {n}")
            self.store.set_turn(n, tid, unit, started, ended, prompt)
            if self.xattrs:
                for row in self.store.q("SELECT id, path FROM things WHERE made_by_thing = ? AND kind = 'file' "
                                        "AND deleted IS NULL", (tid,)):
                    if row["path"]:
                        _setxattr(row["path"], XATTR_MADE_BY, f"turn {n}")
        self.changed.add(tid)
        return tid

    def turn_row(self, row: dict, n: int) -> int:
        """One finished turn from turns.jsonl: its thing, and the files it wrote and read."""
        ended = float(row.get("t") or time.time())
        started = float(row.get("started") or ended)
        meta = {k: row[k] for k in ("summary", "provider", "ok", "stopped", "snapshot") if k in row}
        tid = self.turn(n, row.get("unit"), row.get("prompt"), started, ended, meta)
        files = row.get("files") if isinstance(row.get("files"), dict) else {}
        who = Who("turn", tid, str(row.get("provider") or ""))
        wrote_ids = []
        with self.store.tx():
            for p in _paths(files.get("wrote"))[:200]:
                tid_f = self._turn_wrote(p, tid, who, started, ended)
                if tid_f is not None:
                    wrote_ids.append(tid_f)
            read_ids = []
            for p in _paths(files.get("read"))[:50]:
                thing = self.store.by_path(p)
                if thing is not None:
                    read_ids.append(thing["id"])
            # A turn that read X and wrote Y: Y came from X. Only for small turns, where it
            # means something; a turn that read fifty files and wrote thirty says nothing.
            if len(read_ids) <= 10 and len(wrote_ids) <= 10:
                for w in wrote_ids:
                    for r in read_ids:
                        if r != w:
                            self.store.link(w, r, "came_from", ended)
            this = row.get("this")
            if isinstance(this, str):
                target = self.store.by_path(this) if this.startswith("/") else self.store.by_key(
                    f"url:{fingerprint_url(this)}")
                if target is not None:
                    self.store.link(tid, target["id"], "asked_about", started)
        return tid

    def _turn_wrote(self, path: str, tid: int, who: Who, started: float, ended: float) -> int | None:
        if rules.classify(path, self.home).kind != "thing":
            return None
        thing = self.store.by_path(path)
        if thing is None:
            if not os.path.exists(path):
                return None
            # The watcher did not see it (not running, or not on btrfs): the turn's own record
            # is the witness.
            return self.saw(path, "create", started, who, os.path.isdir(path))
        seen = self.store.one("SELECT id FROM events WHERE thing = ? AND actor_thing = ? LIMIT 1", (thing["id"], tid))
        if seen is None:
            ev = self.store.event(ended, "change", thing["id"], who.kind, tid, who.via)
            self._changed_by(thing["id"], who, ended, ev, thing)
            self.store.touch(thing["id"], ended, changed=True)
            self.changed.add(thing["id"])
        return thing["id"]

    # -- the web --

    def site(self, url: str) -> int | None:
        host = site_of(url)
        if not host:
            return None
        return self.store.upsert_key("site", f"site:{host}", host)

    def page(self, url: str, title: str, t: float) -> int | None:
        key = fingerprint_url(url)
        if key is None:
            return None
        have = self.store.by_key(f"url:{key}")
        if have is not None:
            if title and title != have["title"]:
                self.store.update(have["id"], title=title)
            return have["id"]
        return self.store.add("page", title or key, key=f"url:{key}", url=key, area=self.site(key), created=t)

    def visit(self, url: str, title: str, t: float, duration: float = 0.0, via: str = "chromium") -> int | None:
        pid = self.page(url, title, t)
        if pid is None:
            return None
        self.store.event(t, "visit", pid, "you", None, via, detail={"duration": round(duration, 1)} if duration else None,
                         coalesce=0)
        self.store.touch(pid, t)
        thing = self.store.get(pid)
        if thing and thing["area"]:
            self.store.touch(thing["area"], t)
        self.changed.add(pid)
        return pid

    def download(self, path: str, url: str, page_url: str, t: float, via: str = "chromium") -> int | None:
        """A finished download: the file came from the page you were on."""
        if rules.classify(path, self.home).kind != "thing":
            return None
        thing = self.store.by_path(path)
        tid = thing["id"] if thing else (self.saw(path, "create", t, Who("you", None, via)) if os.path.exists(path)
                                         else None)
        if tid is None:
            return None
        origin = page_url or url
        page = self.page(origin, "", t)
        if self.store.one("SELECT id FROM events WHERE thing = ? AND kind = 'download'", (tid,)) is None:
            ev = self.store.event(t, "download", tid, "you", None, via, other=page, detail={"url": url}, coalesce=0)
            if page is not None:
                self.store.link(tid, page, "came_from", t, ev)
            if self.xattrs and fingerprint_url(origin):
                _setxattr(path, XATTR_ORIGIN, fingerprint_url(origin))
        self.changed.add(tid)
        return tid

    # -- the system --

    def package(self, name: str, version: str, op: str, t: float, who: Who) -> int:
        system = self.store.upsert_key("system", "system", "System")
        pid = self.store.upsert_key("package", f"package:{name}", name, area=system, meta={"version": version})
        self.store.update(pid, deleted=t if op == "remove" else None)
        ev = self.store.event(t, op, pid, who.kind, who.thing, who.via, detail={"version": version}, coalesce=0)
        if who.thing is not None:
            self.store.link(pid, who.thing, "made_by" if op == "install" else "changed_by", t, ev)
        self.store.touch(pid, t, changed=True)
        self.changed.add(pid)
        return pid

    def facts(self, lines: list[str], t: float, who: Who, source: str) -> None:
        """memory.md, one fact per line: new lines are facts the writer taught the machine,
        lines that went are facts forgotten."""
        keep = {}
        for line in lines:
            text = line.strip().lstrip("-*").strip()
            if len(text) < 3 or text.startswith("#"):
                continue
            keep["fact:" + hashlib.sha1(text.encode()).hexdigest()[:16]] = text
        have = {r["key"]: r for r in self.store.q("SELECT * FROM things WHERE kind = 'fact' AND deleted IS NULL")}
        with self.store.tx():
            for key, row in have.items():
                if key not in keep:
                    self.store.mark_deleted(row["id"], t)
                    self.store.event(t, "delete", row["id"], who.kind, who.thing, who.via)
            for key, text in keep.items():
                if key in have:
                    continue
                dead = self.store.by_key(key)
                if dead is not None:
                    self.store.update(dead["id"], deleted=None)
                    continue
                fid = self.store.add("fact", text, key=key, created=t, made_by=who.kind, made_by_thing=who.thing,
                                     made_via=who.via, meta={"source": source})
                ev = self.store.event(t, "create", fid, who.kind, who.thing, who.via)
                if who.thing is not None:
                    self.store.link(fid, who.thing, "made_by", t, ev)
                self.changed.add(fid)


def _paths(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [os.path.normpath(p) for p in value if isinstance(p, str) and p.startswith("/")]


def _app_title(path: str, name: str) -> str:
    try:
        import tomllib
        with open(os.path.join(path, "app.toml"), "rb") as f:
            title = tomllib.load(f).get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    except (OSError, ValueError):
        pass
    return name.replace("-", " ").capitalize()


def _setxattr(path: str, name: str, value: str) -> None:
    try:
        os.setxattr(path, name, value.encode()[:1024], follow_symlinks=False)
    except OSError:
        pass   # not ours, a filesystem without user xattrs, or gone already

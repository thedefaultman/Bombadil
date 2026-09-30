"""The pill is also the launcher: a small exact list of words that never wait for the model.

Typing an app's name, a panel's name ("browser", or an alias people already use: "chrome"),
or one of a few commands ("undo", "stop", "history", "wifi", "desk") is handled here, by
agentd, in a fraction of a second and offline. So are the desk's widgets, but only with a
verb ("show machine", "hide now"): a bare "now" or "away" is an ordinary word for the agent.
Everything else goes to the agent. The list is deliberately exact (after lowercasing and
trimming, with an optional "open"/"close" in front): a parser that guesses would give the
machine two brains that sometimes disagree.

`match()` decides; `Launcher` does the work by calling Hyprland, the apps and snapper
directly. Every action returns one plain sentence for the line above the pill.
"""

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import apps, dev, hypr, paths, snapshots
from .desk import WIDGETS, Desk

PANEL_WORDS = {
    "browser": ["browser", "web browser", "web", "chrome", "chromium", "google", "internet"],
    "terminal": ["terminal", "console", "shell"],
    "files": ["files", "file manager", "folders", "my files"],
}
PANEL_TITLES = {"browser": "the browser", "terminal": "the terminal", "files": "Files"}
# The desk's widgets (desk.py has the table), each answering to its id and its words.
WIDGET_WORDS = {w.id: list(dict.fromkeys((w.id, *w.words))) for w in WIDGETS.values()}
WIDGET_TITLES = {w.id: w.title for w in WIDGETS.values()}

# Checked before app names: these must always mean the same thing.
CORE_COMMANDS = {
    "stop": ["stop", "cancel", "stop it", "stop that"],
    "undo": ["undo", "undo that", "undo it", "undo the last change"],
    "history": ["history", "rewind"],
    "hide": ["hide", "hide it", "hide that", "hide everything", "put it away", "put that away"],
    "desk": ["desk"],
    "lock": ["lock", "lock screen", "lock the screen"],
    "restart": ["restart", "reboot", "restart the computer"],
    "shutdown": ["shut down", "shutdown", "power off", "poweroff"],
}
# Signing in to the AI, and switching which AI runs the machine; agentd does these itself.
SIGNIN_WORDS = ["sign in", "log in", "login", "signin", "sign in again", "log in again", "sign me in",
                "log me in"]
PROVIDER_WORDS = {"claude": ["claude", "claude code", "anthropic"], "codex": ["codex", "openai codex", "chatgpt"]}
PROVIDER_VERBS = ("use", "switch to", "change to", "sign in to", "log in to", "sign into", "log into",
                  "sign in with", "log in with")
# Checked after app and panel names, so an app you made called "Sound" wins.
UTILITY_COMMANDS = {
    "wifi": ["wifi", "wi-fi", "wi fi", "network", "networks"],
    "sound": ["sound", "volume", "audio"],
    "brightness": ["brightness"],
    "battery": ["battery"],
}
OPEN_VERBS = ("open", "show", "launch", "start", "run", "bring up", "go to", "switch to")
CLOSE_VERBS = ("close", "quit", "exit", "kill")
HIDE_VERBS = ("hide", "put away")
# A widget takes only the verbs that mean a thing on the screen: "start now" and "run now" are
# sentences for the agent, not the desk.
WIDGET_VERBS = ((("open", "show", "bring up"), "open"), (("close",), "close"), (("hide", "put away"), "hide"))
# Not offered as completions: Tab should never land on these by accident.
NO_COMPLETE = {"restart", "shutdown", "lock", "stop"}

DETAILS_CLASS = "bombadil-details"


@dataclass
class Action:
    kind: str          # "panel", "app", "widget", "session", "project", or a command name ("undo", "stop", ...)
    target: str = ""   # panel, app or widget name; a session's key; a project's folder name
    verb: str = "open"  # open, close, hide; "start" for `claude <project> [role]`
    title: str = ""    # what the line calls it: "the browser", "Passwords", "reviewer on Bombadil"
    tool: str = ""     # coding sessions: claude, codex or shell
    role: str = ""


# Coding sessions: `claude|codex|shell <project> [role]`, a project's or a session's name,
# "end <name>" and "what's running?". Checked before app names: project, then session, then app.
DEV_RE = re.compile(r"(claude|codex|shell) ([^\s]+)(?: ([^\s]+))?")
SESSIONS_WORDS = {"whats running", "what is running", "sessions", "coding sessions", "list sessions"}
END_VERBS = ("end",)


def normalize(text: str) -> str:
    t = " ".join(str(text).lower().split())
    return t.strip(" .!?,;:")


def _key(s: str) -> str:
    """'Wi-Fi' -> 'wifi'. Only spaces, hyphens and underscores fold away: any other letter or
    sign ('почему wifi', '¿restart') makes the text no launcher word at all."""
    k = re.sub(r"[\s_-]+", "", str(s).lower())
    return k if re.fullmatch(r"[a-z0-9]+", k) else ""


def _title_key(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def _strip_verb(t: str, verbs) -> str | None:
    for v in verbs:
        if t.startswith(v + " "):
            rest = t[len(v) + 1:]
            return rest[4:] if rest.startswith("the ") else rest
    return None


def _lookup(word: str, table: dict[str, list[str]]) -> str | None:
    k = _key(word)
    if not k:
        return None
    for name, words in table.items():
        if any(_key(w) == k for w in words):
            return name
    return None


def _find_app(word: str, app_list: list) -> "apps.App | None":
    k, title = _key(word), _title_key(word)
    if not k and not title:
        return None
    for a in app_list:
        if (k and k in (_key(a.name), _key(a.title))) or title == _title_key(a.title):
            return a
    return None


def known_apps() -> list:
    """The apps, skipping what is not one: a copy named passwords.bak, a folder with no
    main.qml. An app.toml that does not parse still lists the app under its folder name."""
    root = paths.apps_dir()
    try:
        dirs = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        return []
    out = []
    for d in dirs:
        try:
            if not apps.NAME_RE.match(d.name) or not (d / "main.qml").exists():
                continue
            out.append(apps.load(d.name))
        except Exception:  # noqa: BLE001 - one broken app never hides the others
            out.append(apps.App(d.name, d, d.name))
    return out


def _dev_match(t: str, d) -> Action | None:
    """Coding sessions by name. `t` is normalized ASCII text."""
    m = DEV_RE.fullmatch(t)
    if m:
        folder = d.project(m.group(2))
        if folder is None:
            return None   # "claude what is this" is for the agent
        role = m.group(3) or m.group(1)
        return Action("session", folder.name, "start", f"{role} on {dev.project_title(folder.name)}",
                      tool=m.group(1), role=role)
    if t.replace("'", "") in SESSIONS_WORDS:
        return Action("sessions")
    ending = _strip_verb(t, END_VERBS)
    word = ending if ending is not None else (_strip_verb(t, OPEN_VERBS) or t)
    if ending is not None:
        found = _dev_end(ending, d)
        if found is not None:
            return found
    else:
        folder = d.project(word)
        if folder is not None:
            return Action("project", folder.name, "open", dev.project_title(folder.name))
    # "reviewer", "reviewer bombadil", "reviewer on bombadil"; "end reviewer"
    parts = word.split()
    if len(parts) == 3 and parts[1] == "on":
        parts = [parts[0], parts[2]]
    if not parts or len(parts) > 2:
        return None
    found = d.named(parts[0])
    if len(parts) == 2:
        folder = d.project(parts[1])
        found = [s for s in found if folder is not None and dev.key(s.project) == dev.key(folder.name)]
    if len(found) > 1:
        where = " and ".join(dev.project_title(s.project) for s in found)
        return Action("choose", parts[0], "end" if ending is not None else "open", f"{parts[0]} is on {where}",
                      role=found[0].project.lower())
    if found:
        s = found[0]
        if ending is not None:
            return Action("end", s.key, "end", s.title, tool=s.tool, role=s.role)
        return Action("session", s.key, "open", s.title, tool=s.tool, role=s.role)
    return None


def _dev_end(word: str, d) -> Action | None:
    """"end claude latchkey" and "end latchkey": the sessions a role cannot name. A project
    with one session ends it; with several, say which."""
    m = DEV_RE.fullmatch(word)
    folder = d.project(m.group(2)) if m else d.project(word)
    if folder is None:
        return None
    title = dev.project_title(folder.name)
    if m:
        role = m.group(3) or m.group(1)
        k = f"{dev.slug(folder.name)}/{role}"
        s = d.sessions.get(k)
        if s is None or s.state == "ended":
            return Action("end", "", "end", f"{role} on {title}", role=role)
        return Action("end", s.key, "end", s.title, tool=s.tool, role=s.role)
    found = [s for s in d.on_project(folder) if not s.yours]
    if len(found) == 1:
        s = found[0]
        return Action("end", s.key, "end", s.title, tool=s.tool, role=s.role)
    if not found:
        return Action("end", "", "end", title)
    roles = [s.role for s in found]
    listed = ", ".join(roles[:-1]) + " and " + roles[-1]
    return Action("choose", roles[0], "end", f"{title} runs {listed}", role=folder.name.lower())


def match(text: str, app_list: list | None = None, dev_names=None) -> Action | None:
    """The local action for exactly this text, or None to send it to the agent. `dev_names`
    (agentd's dev.Dev) adds the coding sessions' words."""
    raw = str(text).strip()
    if not raw or raw.startswith("!"):
        return None   # "!cmd" is a shell command, whatever follows the "!"
    t = normalize(raw)
    if not t:
        return None
    app_list = known_apps() if app_list is None else app_list
    # A sentence in another script or with signs in it is for the agent, even when one
    # launcher word is in it; only an app's own title (Café, Recipes 🍲) opens here.
    plain = t.isascii()
    if plain and _key(t) in {_key(w) for w in SIGNIN_WORDS}:
        return Action("signin")
    if plain:
        for verbs in (PROVIDER_VERBS,):
            word = _strip_verb(t, verbs)
            name = _lookup(word, PROVIDER_WORDS) if word else None
            if name:
                return Action("provider", name, title=name.capitalize())
    cmd = _lookup(t, CORE_COMMANDS) if plain else None
    if cmd:
        if cmd in ("restart", "shutdown", "desk") and raw.endswith("?"):
            return None   # "restart?" asks, it does not tell
        return Action(cmd)
    if dev_names is not None and plain:
        found = _dev_match(t, dev_names)
        if found is not None:
            return found
    for verbs, verb in ((OPEN_VERBS, "open"), (CLOSE_VERBS, "close"), (HIDE_VERBS, "hide"), ((), "open")):
        word = _strip_verb(t, verbs) if verbs else (t[4:] if t.startswith("the ") else t)
        if word is None:
            continue
        app = _find_app(word, app_list)
        if app is not None:
            return Action("app", app.name, verb, app.title)
        if not plain:
            continue
        panel = _lookup(word, PANEL_WORDS)
        if panel is not None:
            return Action("panel", panel, verb, PANEL_TITLES[panel])
        if verb == "open":
            util = _lookup(word, UTILITY_COMMANDS)
            if util and (verbs or word == t):
                return Action(util)
    return _widget_action(t, app_list) if plain and not raw.endswith("?") else None


def _widget_action(t: str, app_list: list) -> Action | None:
    """"show machine", "hide the now", "put watching away". Apps and panels have been tried by
    now, so an app you named Now still opens with its own name."""
    for verbs, verb in WIDGET_VERBS:
        word = _strip_verb(t, verbs)
        widget = _lookup(word, WIDGET_WORDS) if word is not None else None
        if widget is not None:
            return Action("widget", widget, verb, WIDGET_TITLES[widget])
    if t.startswith("put ") and t.endswith(" away"):
        word = t[4:-5].removeprefix("the ")
        widget = _lookup(word, WIDGET_WORDS)
        if widget is not None and _find_app(word, app_list) is None:
            return Action("widget", widget, "hide", WIDGET_TITLES[widget])
    return None


def entries(app_list: list | None = None, dev_names=None) -> list[dict]:
    """What the pill can complete with Tab: apps first, then projects and sessions, then panels,
    widgets, then commands."""
    app_list = known_apps() if app_list is None else app_list
    out = [{"name": a.name, "title": str(a.title), "kind": "app", "words": [str(a.title).lower(), a.name]}
           for a in app_list]
    if dev_names is not None:
        out += dev_names.names()
    out += [{"name": p, "title": PANEL_TITLES[p].removeprefix("the ").capitalize() if p != "files" else "Files",
             "kind": "panel", "words": words} for p, words in PANEL_WORDS.items()]
    out += [{"name": w, "title": WIDGET_TITLES[w], "kind": "widget", "words": words}
            for w, words in WIDGET_WORDS.items()]
    for table in (CORE_COMMANDS, UTILITY_COMMANDS):
        out += [{"name": c, "title": words[0].capitalize(), "kind": "command", "words": words[:1]}
                for c, words in table.items() if c not in NO_COMPLETE]
    out.append({"name": "signin", "title": "Sign in", "kind": "command", "words": ["sign in"]})
    return out


def _snapshot_prompt(desc: str) -> str:
    """'turn:12: install docker' -> 'install docker'."""
    if desc.startswith("turn:") and ": " in desc:
        return desc.split(": ", 1)[1]
    return desc


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


class Launcher:
    """Does what `match` found, without the model. Blocking; agentd calls it in a thread."""

    def __init__(self, hyprland: hypr.Hyprland | None = None, snaps: snapshots.Snapshots | None = None,
                 runner=subprocess.run, spawn=subprocess.Popen, desk: Desk | None = None,
                 sessions: "dev.Dev | None" = None):
        self.hypr = hyprland or hypr.Hyprland()
        self.snaps = snaps or snapshots.Snapshots()
        self.desk_state = desk if desk is not None else Desk().load()
        self.dev = sessions
        self._run = runner
        self._spawn = spawn
        self._drawer: list[str] | None = None   # what the details drawer shows, as its argv

    # -- words for the line while it works --

    @staticmethod
    def doing(action: Action) -> str:
        if action.kind == "session":
            return f"Starting {action.title}" if action.verb == "start" else f"Opening {action.title}"
        if action.kind == "project":
            return f"Opening {action.title}"
        if action.kind == "end":
            return f"Ending {action.title}"
        if action.kind in ("panel", "app"):
            verb = {"open": "Opening", "close": "Closing", "hide": "Putting"}[action.verb]
            return f"{verb} {action.title}" + (" away" if action.verb == "hide" else "")
        if action.kind == "widget":
            return f"Putting {action.title} " + ("on the desk" if action.verb == "open" else "away")
        return {"undo": "Undoing the last change", "history": "Opening the history", "hide": "Putting things away",
                "lock": "Locking the screen", "restart": "Restarting", "shutdown": "Shutting down",
                "wifi": "Opening Wi-Fi", "sound": "Checking the sound", "brightness": "Checking the brightness",
                "battery": "Checking the battery", "stop": "Stopping", "signin": "Signing in",
                "provider": f"Switching to {action.title or action.target}",
                "desk": "Changing the desk"}.get(action.kind, "On it")

    @staticmethod
    def failed(action: Action) -> str:
        if action.kind in ("session", "project"):
            return f"Could not open {action.title}"
        if action.kind == "end":
            return f"Could not end {action.title}"
        if action.kind in ("panel", "app"):
            verb = {"open": "open", "close": "close", "hide": "put away"}.get(action.verb, action.verb)
            return f"Could not {verb} {action.title or action.target}"
        if action.kind == "widget":
            return (f"Could not put {action.title or action.target} "
                    + ("on the desk" if action.verb == "open" else "away"))
        return {"undo": "Could not undo", "history": "Could not open the history", "hide": "Could not put things away",
                "lock": "Could not lock the screen", "restart": "Could not restart", "shutdown": "Could not shut down",
                "wifi": "Could not open Wi-Fi", "sound": "Could not check the sound",
                "brightness": "Could not check the brightness", "battery": "Could not check the battery",
                "desk": "Could not change the desk"}.get(action.kind, "That did not work")

    def run(self, action: Action) -> tuple[bool, str]:
        fn = getattr(self, f"_{action.kind}", None)
        if fn is None:
            return False, f"Nothing here can {action.kind} yet."
        try:
            return fn(action)
        except Exception as e:  # noqa: BLE001 - one plain line, whatever broke
            return False, f"{self.failed(action)}: {_reason(e)}"

    # -- panels and apps --

    def _panel(self, a: Action) -> tuple[bool, str]:
        if a.verb == "close":
            a.verb = "hide"
        out = self.hypr.panel(a.target, show=a.verb == "open")
        if a.verb == "open":
            return True, f"Opened {a.title}" + (", it is still starting." if "starting" in out else ".")
        return True, f"Put {a.title} away."

    def _app(self, a: Action) -> tuple[bool, str]:
        placement = _placement()
        if placement is not None:
            # The app kit's drawers: each app slides in from its own special workspace.
            fn = {"open": placement.show, "hide": placement.hide, "close": placement.close}[a.verb]
            fn(a.target)
        elif a.verb == "open":
            if _app_running(a.target):
                self._focus_class(f"bombadil-app-{a.target}")
            else:
                self.hypr.place_app(a.target)   # its own spot, before the window maps
                apps.run(a.target)
        elif a.verb == "close":
            self._run(["pkill", "-f", f"bombadil-app run {a.target}$"], capture_output=True, check=False)
        else:
            return True, f"{a.title} has no drawer to put away yet."
        past = {"open": "Opened", "close": "Closed", "hide": "Put"}[a.verb]
        return True, f"{past} {a.title}" + (" away." if a.verb == "hide" else ".")

    def _focus_class(self, cls: str) -> None:
        if self.hypr.available:
            self.hypr.dispatch(f'hl.dsp.focus({{ window = "class:^({cls})$" }})')

    # -- the desk --

    def _desk(self, _a: Action) -> tuple[bool, str]:
        return self.desk_state.apply("toggle")

    def _widget(self, a: Action) -> tuple[bool, str]:
        return self.desk_state.apply("show" if a.verb == "open" else "hide", a.target)

    def _hide(self, _a: Action) -> tuple[bool, str]:
        if not self.hypr.available:
            return True, "Nothing to put away."
        hidden = []
        for m in json.loads(self.hypr.request("j/monitors")):
            name = (m.get("specialWorkspace") or {}).get("name", "")
            if name.startswith("special:"):
                if not m.get("focused", True):
                    self.hypr.dispatch(f'hl.dsp.focus({{ monitor = "{m.get("name", "")}" }})')
                self.hypr.dispatch(f'hl.dsp.workspace.toggle_special("{name.removeprefix("special:")}")')
                hidden.append(name)
        return True, "Put everything away." if hidden else "Nothing to put away."

    # -- coding sessions --

    def _sessions_or_raise(self) -> "dev.Dev":
        if self.dev is None:
            raise RuntimeError("coding sessions are not running here")
        return self.dev

    def _session(self, a: Action) -> tuple[bool, str]:
        d = self._sessions_or_raise()
        if a.verb == "start":
            return d.open(a.tool, a.target, a.role)
        s = d.sessions.get(a.target)
        if s is None:
            return False, f"{a.title} is gone."
        return d.bring(s)

    def _project(self, a: Action) -> tuple[bool, str]:
        d = self._sessions_or_raise()
        folder = d.project(a.target)
        if folder is None:
            return False, f"{a.title} is not in your Projects folder any more."
        return d.open_project(folder)

    def _end(self, a: Action) -> tuple[bool, str]:
        d = self._sessions_or_raise()
        s = d.sessions.get(a.target) if a.target else None
        if s is None or s.state == "ended":
            return False, (f"{a.title[:1].upper()}{a.title[1:]} is not running." if a.role
                           else f"Nothing runs on {a.title}.")
        return d.end(s)

    def _choose(self, a: Action) -> tuple[bool, str]:
        verb = "end " if a.verb == "end" else ""
        return False, f"{a.title[:1].upper()}{a.title[1:]}: say which, as in “{verb}{a.target} {a.role}”."

    def _sessions(self, _a: Action) -> tuple[bool, str]:
        self.details([_bombadil(), "dev", "list"])
        return True, "Opened the list of coding sessions."

    # -- undo --

    # A rollback swaps the root at the next boot. Until then the running system still has
    # what was undone, and a restore point taken now would bring it back, so the marker of
    # the last undo stays until a restart has applied it.

    def _undo_marker(self) -> Path:
        return paths.state_dir() / "undo.json"

    def _marker(self) -> dict | None:
        try:
            m = json.loads(self._undo_marker().read_text())
            int(m["snapshot"])
            return m
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_marker(self, m: dict) -> None:
        path = self._undo_marker()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(m))

    def last_undo(self) -> int | None:
        m = self._marker()
        return int(m["snapshot"]) if m else None

    @staticmethod
    def _pending(m: dict | None) -> bool:
        """Is this undo still waiting for a restart?"""
        return bool(m and m.get("boot") and m.get("boot") == _boot_id())

    def clear_undo(self) -> None:
        """A new turn starts a new history: the next undo takes back that turn. Not while an
        undo still waits for its restart, which takes back this turn's system changes too."""
        if self._pending(self._marker()):
            return
        try:
            self._undo_marker().unlink()
        except OSError:
            pass

    def _undo(self, _a: Action) -> tuple[bool, str]:
        if not self.snaps.available:
            if Path("/run/archiso").exists():
                return False, "Undo starts once Bombadil is installed. The live system keeps no restore points."
            return False, "Undo is off here: this system keeps no restore points."
        m = self._marker()
        before = int(m["snapshot"]) if m else None
        snaps = self.snaps.list(limit=200)
        if m is not None and self._pending(m):
            # Turns since that undo run on the old root, which the restart replaces: they are
            # already covered. Say so once; the undo after that goes one turn further back.
            newest = max((s.number for s in snaps if s.number > before and s.description.startswith("turn:")),
                         default=0)
            if newest > int(m.get("covered") or 0):
                m["covered"] = newest
                self._write_marker(m)
                return True, (f"Already undone: when you restart, system files go back to before “{m.get('what', '')}”, "
                              "and that takes back everything since too.")
        # Each undo goes one turn further back until a new turn runs.
        target = None
        for snap in reversed(snaps):
            if before is not None and snap.number >= before:
                continue
            if snap.description.startswith("turn:"):
                target = snap
                break
        if target is None:
            return False, "Nothing to undo yet."
        self.snaps.rollback(target.number)
        what = _snapshot_prompt(target.description)
        self._write_marker({"snapshot": target.number, "what": what, "boot": _boot_id(), "t": time.time()})
        return True, (f"Undone. System files go back to before “{what}” when you restart. "
                      "Your home folder and apps stay as they are.")

    # -- things that open in the details drawer --

    def details(self, argv: list[str], toggle: bool = False) -> str:
        """Run a terminal program in the details drawer (a foot window in special:details).
        With toggle, the same drawer already open closes instead: a second click on Details."""
        foot = shutil.which("foot")
        if foot is None:
            raise RuntimeError("foot is not installed")
        shows = [a for a in argv if a != "--follow"]   # following or not, it is the same turn
        if toggle and shows == self._drawer and self._drawer_open():
            self.close_details()
            return "hidden"
        self.close_details()
        proc = self._spawn([foot, f"--app-id={DETAILS_CLASS}", "--title=Details", *argv], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self._drawer = shows
        if self.hypr.available:
            self._focus_drawer(proc)
        return "shown"

    def _focus_drawer(self, proc, wait: float = 5.0) -> None:
        """Slide the drawer in with the keyboard, so Esc (any key) closes it. Its window rule is
        silent, so the window never takes the keyboard by itself, and showing the workspace before
        the window maps opens it empty and leaves the keyboard where it was (the bar). So wait
        for the window, then focus it: that shows the drawer and moves keys and pointer into it."""
        pid = getattr(proc, "pid", None)

        def gone() -> bool:   # closed meanwhile (Esc, a second click), or foot failed
            poll = getattr(proc, "poll", None)
            return poll is not None and poll() is not None

        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if gone():
                return
            try:
                mapped = any(c.get("class") == DETAILS_CLASS and (pid is None or c.get("pid") == pid)
                             for c in self.hypr.clients())
            except (OSError, ValueError, RuntimeError):   # a busy compositor: ask again
                mapped = False
            if mapped:
                sel = f"pid:{pid}" if pid is not None else f"class:^({DETAILS_CLASS})$"
                self.hypr.dispatch(f'hl.dsp.focus({{ window = "{sel}" }})')
                return
            time.sleep(0.05)
        if not gone():
            # Still starting: show the drawer anyway; a click in it gives it the keyboard.
            self.hypr.dispatch('hl.dsp.focus({ workspace = "special:details" })')

    def _drawer_open(self) -> bool:
        r = self._run(["pgrep", "-f", "--", f"--app-id={DETAILS_CLASS}"], capture_output=True, check=False)
        return getattr(r, "returncode", 1) == 0

    def close_details(self) -> bool:
        """Put the drawer away (Esc in the pill, a second click on Details). Hyprland hides a
        special workspace when its last window closes."""
        self._drawer = None
        # "--" first: the pattern itself starts with dashes.
        r = self._run(["pkill", "-f", "--", f"--app-id={DETAILS_CLASS}"], capture_output=True, check=False)
        return getattr(r, "returncode", 1) == 0

    def _history(self, _a: Action) -> tuple[bool, str]:
        self.details([_bombadil(), "history"])
        return True, "Opened the history."

    def _wifi(self, _a: Action) -> tuple[bool, str]:
        if shutil.which("nmtui") is None:
            return False, "Wi-Fi settings need NetworkManager, which is not installed."
        self.details(["nmtui", "connect"])
        return True, "Opened Wi-Fi."

    # -- one-line readouts (cards with controls come later) --

    def _battery(self, _a: Action) -> tuple[bool, str]:
        bats = sorted(Path("/sys/class/power_supply").glob("BAT*"))
        if not bats:
            return True, "No battery: this computer runs on mains power."
        cap = _read(bats[0] / "capacity")
        status = _read(bats[0] / "status").lower()
        extra = {"charging": ", charging", "full": ", full", "discharging": ""}.get(status, "")
        return True, f"Battery {cap}%{extra}."

    def _brightness(self, _a: Action) -> tuple[bool, str]:
        lights = sorted(Path("/sys/class/backlight").glob("*"))
        if not lights:
            return True, "This screen has no brightness control here."
        cur, top = _read(lights[0] / "brightness"), _read(lights[0] / "max_brightness")
        if cur.isdigit() and top.isdigit() and int(top):
            return True, f"Brightness {round(100 * int(cur) / int(top))}%."
        return True, "This screen has no brightness control here."

    def _sound(self, _a: Action) -> tuple[bool, str]:
        if shutil.which("wpctl") is None:
            return False, "Sound settings need PipeWire, which is not running."
        r = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                      timeout=3, check=False)
        m = re.search(r"Volume:\s*([\d.]+)", r.stdout or "")
        if not m:
            return False, "No sound output found."
        muted = ", muted" if "MUTED" in r.stdout else ""
        return True, f"Volume {round(float(m.group(1)) * 100)}%{muted}."

    # -- the session --

    def _lock(self, _a: Action) -> tuple[bool, str]:
        if shutil.which("hyprlock"):
            self._spawn(["hyprlock"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True)
            return True, "Locked."
        return False, "Nothing can lock the screen yet: hyprlock is not installed."

    def _restart(self, _a: Action) -> tuple[bool, str]:
        self._run(["systemctl", "reboot"], capture_output=True, check=True, timeout=10)
        return True, "Restarting."

    def _shutdown(self, _a: Action) -> tuple[bool, str]:
        self._run(["systemctl", "poweroff"], capture_output=True, check=True, timeout=10)
        return True, "Shutting down."


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _reason(e: Exception) -> str:
    """Why an action failed, in one line: what the command said, not how Python saw it."""
    if isinstance(e, subprocess.CalledProcessError):
        said = e.stderr or e.output or ""
        if isinstance(said, bytes):
            said = said.decode(errors="replace")
        lines = [line.strip() for line in str(said).splitlines() if line.strip()]
        return lines[-1][:200] if lines else f"{PurePosixPath(str(e.cmd[0] if e.cmd else '')).name or 'it'} failed"
    if isinstance(e, subprocess.TimeoutExpired):
        return "it did not answer in time"
    return " ".join(str(e).split())[:200] or type(e).__name__


def _bombadil() -> str:
    local = Path(__file__).resolve().parents[2] / "bin" / "bombadil"
    return str(local) if local.exists() else (shutil.which("bombadil") or "bombadil")


def _placement():
    """The app kit's placement module (drawers per app) once it is on this branch; else None."""
    try:
        from .appkit import placement  # type: ignore[attr-defined]
    except ImportError:
        return None
    return placement


def _app_running(name: str) -> bool:
    r = subprocess.run(["pgrep", "-f", f"bombadil-app run {name}$"], capture_output=True, check=False)
    return r.returncode == 0

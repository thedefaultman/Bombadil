"""Who did it: a writer's cgroup and process chain, named the way a person would.

Every turn of the machine's agent runs in its own systemd scope (bombadil-turn-*.scope,
procs.py), every coding session in a scope under bombadil-dev-<project>.slice (the second
brief), and generated apps run as `bombadil-app run <name>`. So the cgroup says which turn
or session wrote a file, even for a process that has already exited (the watcher then
reports its nearest live ancestor). Anything else of yours is "you, in <the window's app>";
root outside a turn is the system.

An actor is (kind, key, via): kind is you, turn, session, app, system or unknown; key
names the turn ("unit:<scope>" until agentd's row gives its number), session or app; via
is the program, and for you the app whose window it was.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

TURN_SCOPE = re.compile(r"^bombadil-turn-(.+)\.scope$")
DEV_SLICE = re.compile(r"^bombadil-dev-(.+)\.slice$")
APP_UNIT = re.compile(r"^(?:app-)?bombadil(?:-|\\x2d)app(?:-|\\x2d)([a-z0-9][a-z0-9-]*?)(?:@[^.]*)?\.(?:scope|service)$")
# Processes that hold a session rather than being an app you use: the chain stops there.
SESSION_ROOTS = {"Hyprland", "hyprland", "Hyprland-real", "sway", "greetd", "systemd", "(sd-pam)", "login",
                 "sshd", "agentd", "quickshell", "uwsm", "start-hyprland", "init"}
# The window app a person would name, from a process name.
WINDOW_NAMES = {"foot": "the terminal", "footclient": "the terminal", "chromium": "the browser",
                "chrome": "the browser", "nautilus": "Files", "code": "VS Code", "codium": "VSCodium",
                "zed": "Zed", "kitty": "the terminal", "alacritty": "the terminal", "wezterm": "the terminal"}


@dataclass(frozen=True)
class Actor:
    kind: str           # you, turn, session, app, system, unknown
    key: str = ""       # "unit:<scope>", "session:<project>/<name>", "app:<name>"
    via: str = ""       # the program ("nvim", "pacman") or, for you, the window's app ("foot")
    prog: str = ""      # the program that wrote, when via is the window

    @property
    def thing_key(self) -> str | None:
        """The key of the thing that stands for this actor, if it has one."""
        return self.key if self.kind in ("turn", "session", "app") and self.key else None


YOU = Actor("you")
UNKNOWN = Actor("unknown")
SYSTEM = Actor("system")


def _unescape(s: str) -> str:
    """systemd unit name escaping: \\x2d is "-"."""
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)


def _argv(entry) -> list[str]:
    cmd = entry[2] if len(entry) > 2 else ""
    return str(cmd).split()


def _app_from_chain(chain: list) -> str | None:
    """`bombadil-app run <name>`, started directly or as `python3 …/bombadil-app run <name>`."""
    for entry in chain:
        argv = _argv(entry)
        for i, a in enumerate(argv[:3]):
            if a.rsplit("/", 1)[-1] == "bombadil-app" and argv[i + 1:i + 2] == ["run"] and len(argv) > i + 2:
                return argv[i + 2]
    return None


def _window(chain: list) -> str:
    """The app whose window a write came from: the last process before the session's root."""
    names = [str(e[1]) for e in chain if len(e) > 1]
    for i, n in enumerate(names):
        if n in SESSION_ROOTS:
            return names[i - 1] if i > 0 else ""
    return names[-1] if names else ""


class Sessions:
    """The coding sessions' registry (~/.local/state/bombadil/dev/sessions.json, written by
    agentd's dev layer): which scope is which session. Read lazily, re-read when it changes."""

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime = None
        self._by_unit: dict[str, dict] = {}

    def lookup(self, unit: str) -> dict | None:
        if self.path is None:
            return None
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return None
        if mtime != self._mtime:
            self._mtime = mtime
            self._by_unit = {}
            try:
                data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                data = []
            rows = list(data.values()) if isinstance(data, dict) else data if isinstance(data, list) else []
            for r in rows:
                if isinstance(r, dict) and r.get("unit"):
                    self._by_unit[str(r["unit"]).removesuffix(".scope")] = r
        return self._by_unit.get(unit.removesuffix(".scope"))


def from_event(ev: dict, sessions: Sessions | None = None) -> Actor:
    """The actor of one watcher event (see the protocol in watch.py)."""
    if ev.get("op") == "offline":
        return UNKNOWN
    cgroup = str(ev.get("cgroup") or "")
    chain = ev.get("chain") or []
    comm = str(ev.get("comm") or "")
    parts = [p for p in cgroup.split("/") if p]
    for p in reversed(parts):
        m = TURN_SCOPE.match(p)
        if m:
            return Actor("turn", f"unit:{p.removesuffix('.scope')}", comm)
    # Slices are named by their whole path (bombadil-dev-latchkey.slice lives inside
    # bombadil-dev.slice), so the outermost bombadil-dev-* slice names the project.
    slice_part = next((p for p in parts if DEV_SLICE.match(p)), None)
    if slice_part is not None:
        project = _unescape(DEV_SLICE.match(slice_part).group(1))
        scope = next((p for p in reversed(parts) if p.endswith(".scope")), "")
        entry = sessions.lookup(scope) if (sessions and scope) else None
        name = str((entry or {}).get("name") or (entry or {}).get("role") or "")
        key = f"session:{project}/{name}" if name else f"session:{project}"
        return Actor("session", key, comm)
    for p in reversed(parts):
        m = APP_UNIT.match(p)
        if m:
            return Actor("app", f"app:{m.group(1)}", comm)
    app = _app_from_chain(chain)
    if app:
        return Actor("app", f"app:{app}", comm)
    if ev.get("uid") == 0:
        return Actor("system", "", comm)
    if not cgroup and not chain:
        return UNKNOWN
    return Actor("you", "", _window(chain) or comm, comm)


def window_title(prog: str) -> str:
    return WINDOW_NAMES.get(prog, prog)

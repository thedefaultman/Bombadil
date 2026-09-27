"""The pill is also the launcher: a small exact list of words that never wait for the model.

Typing an app's name, a panel's name ("browser", or an alias people already use: "chrome"),
or one of a few commands ("undo", "stop", "history", "wifi") is handled here, by agentd,
in a fraction of a second and offline. Everything else goes to the agent. The list is
deliberately exact (after lowercasing and trimming, with an optional "open"/"close" in
front): a parser that guesses would give the machine two brains that sometimes disagree.

`match()` decides; `Launcher` does the work by calling Hyprland, the apps and snapper
directly. Every action returns one plain sentence for the line above the pill.
"""

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import apps, hypr, paths, snapshots

PANEL_WORDS = {
    "browser": ["browser", "web browser", "web", "chrome", "chromium", "google", "internet"],
    "terminal": ["terminal", "console", "shell"],
    "files": ["files", "file manager", "folders", "my files"],
}
PANEL_TITLES = {"browser": "the browser", "terminal": "the terminal", "files": "Files"}

# Checked before app names: these must always mean the same thing.
CORE_COMMANDS = {
    "stop": ["stop", "cancel", "stop it", "stop that"],
    "undo": ["undo", "undo that", "undo it", "undo the last change"],
    "history": ["history", "rewind"],
    "hide": ["hide", "hide it", "hide that", "hide everything", "put it away", "put that away"],
    "lock": ["lock", "lock screen", "lock the screen"],
    "restart": ["restart", "reboot", "restart the computer"],
    "shutdown": ["shut down", "shutdown", "power off", "poweroff", "turn off"],
}
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
# Not offered as completions: Tab should never land on these by accident.
NO_COMPLETE = {"restart", "shutdown", "lock", "stop"}

DETAILS_CLASS = "bombadil-details"


@dataclass
class Action:
    kind: str          # "panel", "app", or a command name ("undo", "stop", ...)
    target: str = ""   # panel or app name
    verb: str = "open"  # open, close, hide
    title: str = ""    # what the line calls it: "the browser", "Passwords"


def normalize(text: str) -> str:
    t = " ".join(str(text).lower().split())
    return t.strip(" .!?,;:")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


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
    k = _key(word)
    if not k:
        return None
    for a in app_list:
        if k in (_key(a.name), _key(a.title)):
            return a
    return None


def match(text: str, app_list: list | None = None) -> Action | None:
    """The local action for exactly this text, or None to send it to the agent."""
    t = normalize(text)
    if not t or t.startswith("!"):
        return None
    app_list = apps.list_apps() if app_list is None else app_list
    cmd = _lookup(t, CORE_COMMANDS)
    if cmd:
        return Action(cmd)
    for verbs, verb in ((OPEN_VERBS, "open"), (CLOSE_VERBS, "close"), (HIDE_VERBS, "hide"), ((), "open")):
        word = _strip_verb(t, verbs) if verbs else (t[4:] if t.startswith("the ") else t)
        if word is None:
            continue
        app = _find_app(word, app_list)
        if app is not None:
            return Action("app", app.name, verb, app.title)
        panel = _lookup(word, PANEL_WORDS)
        if panel is not None:
            return Action("panel", panel, verb, PANEL_TITLES[panel])
        if verb == "open":
            util = _lookup(word, UTILITY_COMMANDS)
            if util and (verbs or word == t):
                return Action(util)
    return None


def entries(app_list: list | None = None) -> list[dict]:
    """What the pill can complete with Tab: apps first, then panels, then commands."""
    app_list = apps.list_apps() if app_list is None else app_list
    out = [{"name": a.name, "title": a.title, "kind": "app", "words": [a.title.lower(), a.name]} for a in app_list]
    out += [{"name": p, "title": PANEL_TITLES[p].removeprefix("the ").capitalize() if p != "files" else "Files",
             "kind": "panel", "words": words} for p, words in PANEL_WORDS.items()]
    for table in (CORE_COMMANDS, UTILITY_COMMANDS):
        out += [{"name": c, "title": words[0].capitalize(), "kind": "command", "words": words[:1]}
                for c, words in table.items() if c not in NO_COMPLETE]
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
                 runner=subprocess.run, spawn=subprocess.Popen):
        self.hypr = hyprland or hypr.Hyprland()
        self.snaps = snaps or snapshots.Snapshots()
        self._run = runner
        self._spawn = spawn

    # -- words for the line while it works --

    @staticmethod
    def doing(action: Action) -> str:
        if action.kind in ("panel", "app"):
            verb = {"open": "Opening", "close": "Closing", "hide": "Putting"}[action.verb]
            return f"{verb} {action.title}" + (" away" if action.verb == "hide" else "")
        return {"undo": "Undoing the last change", "history": "Opening the history", "hide": "Putting things away",
                "lock": "Locking the screen", "restart": "Restarting", "shutdown": "Shutting down",
                "wifi": "Opening Wi-Fi", "sound": "Checking the sound", "brightness": "Checking the brightness",
                "battery": "Checking the battery", "stop": "Stopping"}.get(action.kind, "On it")

    def run(self, action: Action) -> tuple[bool, str]:
        fn = getattr(self, f"_{action.kind}", None)
        if fn is None:
            return False, f"Nothing here can {action.kind} yet."
        try:
            return fn(action)
        except Exception as e:  # noqa: BLE001 - one plain line, whatever broke
            return False, f"Could not {action.verb if action.kind in ('panel', 'app') else action.kind}: {e}"

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

    # -- undo --

    def _undo_marker(self) -> Path:
        return paths.state_dir() / "undo.json"

    def last_undo(self) -> int | None:
        try:
            return int(json.loads(self._undo_marker().read_text())["snapshot"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def clear_undo(self) -> None:
        """A new turn starts a new history: the next undo takes back that turn."""
        try:
            self._undo_marker().unlink()
        except OSError:
            pass

    def _undo(self, _a: Action) -> tuple[bool, str]:
        if not self.snaps.available:
            if Path("/run/archiso").exists():
                return False, "Undo starts once Bombadil is installed. The live system keeps no restore points."
            return False, "Undo is off here: this system keeps no restore points."
        # Each undo goes one turn further back until a new turn runs.
        before = self.last_undo()
        target = None
        for snap in reversed(self.snaps.list(limit=200)):
            if before is not None and snap.number >= before:
                continue
            if snap.description.startswith("turn:"):
                target = snap
                break
        if target is None:
            return False, "Nothing to undo yet."
        self.snaps.rollback(target.number)
        m = self._undo_marker()
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"snapshot": target.number, "t": time.time()}))
        what = _snapshot_prompt(target.description)
        return True, (f"Undone. System files go back to before “{what}” when you restart. "
                      "Your home folder and apps stay as they are.")

    # -- things that open in the details drawer --

    def details(self, argv: list[str]) -> str:
        """Run a terminal program in the details drawer (a foot window in special:details)."""
        foot = shutil.which("foot")
        if foot is None:
            raise RuntimeError("foot is not installed")
        self._run(["pkill", "-f", f"--app-id={DETAILS_CLASS}"], capture_output=True, check=False)
        self._spawn([foot, f"--app-id={DETAILS_CLASS}", "--title=Details", *argv], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if self.hypr.available:
            # The window rule puts it in special:details; focus shows that drawer (never toggles it off).
            self.hypr.dispatch('hl.dsp.focus({ workspace = "special:details" })')
        return "shown"

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

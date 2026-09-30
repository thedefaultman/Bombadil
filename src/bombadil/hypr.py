"""Talk to Hyprland over its IPC socket.

Panels are Hyprland "special workspaces": a scratch workspace that slides in over the
current one and slides back out. `panel("browser")` shows the browser panel, launching
Chromium into it if it is not running yet.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

PANELS: dict[str, list[str]] = {
    "browser": ["chromium", "--ozone-platform=wayland", "--remote-debugging-port=9222",
                "--class=bombadil-browser", "--no-first-run", "--no-default-browser-check"],
    "terminal": ["foot", "--app-id=bombadil-terminal"],
    "files": ["nautilus"],
}


# A generated app opens as a card this big (hyprland.lua's bombadil-apps rule), centered, and the next
# one a step down and to the right, so a second never hides the first.
APP_CARD = (540, 660)
APP_STEP = 32
APP_SLOTS = 3
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def app_slots(monitor: dict, card: tuple[int, int] = APP_CARD) -> list[tuple[int, int]]:
    """Where an app card can open on this monitor, in its coordinates: centered in the room the
    bar leaves, then down and right by a step each. The steps shrink to what the room allows, so
    the last slot's bottom edge is still above the bar; a card as tall as the room has one slot."""
    scale = float(monitor.get("scale") or 1)
    w, h = float(monitor["width"]) / scale, float(monitor["height"]) / scale
    if int(monitor.get("transform") or 0) % 2:
        w, h = h, w
    left, top, right, bottom = ([float(x) for x in monitor.get("reserved") or []] + [0.0] * 4)[:4]
    x0 = left + max(0.0, (w - left - right - card[0]) / 2)
    y0 = top + max(0.0, (h - top - bottom - card[1]) / 2)
    room = max(0.0, (h - top - bottom - card[1]) / 2)   # what is left below the centered card
    step = min(APP_STEP, room / (APP_SLOTS - 1))
    return [(round(x0 + step * k), round(y0 + step * k)) for k in range(APP_SLOTS)]


def _socket_path() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(base) / "hypr" / sig / ".socket.sock"


def _launching(cmd: list[str]) -> bool:
    """Is this panel's app already running (e.g. Chromium still starting, no window yet)?"""
    marker = next((a for a in cmd if a.startswith(("--class=", "--app-id="))), None)
    if marker is None:
        return False
    return subprocess.run(["pgrep", "-f", "--", marker], capture_output=True, check=False).returncode == 0


class Hyprland:
    @property
    def available(self) -> bool:
        p = _socket_path()
        return p is not None and p.exists()

    def request(self, command: str) -> str:
        path = _socket_path()
        if path is None or not path.exists():
            raise RuntimeError("Hyprland is not running")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(path))
            s.sendall(command.encode())
            chunks = []
            while chunk := s.recv(65536):
                chunks.append(chunk)
        return b"".join(chunks).decode()

    def dispatch(self, lua: str) -> str:
        """Run a dispatcher. Since 0.55 Hyprland dispatchers are Lua (`hl.dsp.*`)."""
        for attempt in range(3):  # a busy compositor can miss hyprctl's IPC deadline
            out = subprocess.run(["hyprctl", "dispatch", lua], capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip() in ("", "ok"):
                return out.stdout
            if "didn't respond in time" not in out.stdout + out.stderr:
                break
            time.sleep(1 + attempt)
        raise RuntimeError(f"hyprctl dispatch {lua!r}: {(out.stdout + out.stderr).strip()}")

    def clients(self) -> list[dict]:
        return json.loads(self.request("j/clients"))

    def place_app(self, name: str) -> str:
        """Before an app starts: a window rule that opens it on the first free slot of app_slots,
        so a second app does not open exactly on top of the first. Never raises; without Hyprland
        (or if it does not answer) the app opens where hyprland.lua's rule puts it."""
        if not self.available or not _APP_NAME.fullmatch(name):
            return ""
        try:
            monitors = json.loads(self.request("j/monitors"))
            mon = next((m for m in monitors if m.get("focused")), monitors[0])
            slots = app_slots(mon)
            cards = [c for c in self.clients()
                     if str(c.get("class", "")).startswith("bombadil-app-") and c.get("floating")]
            taken = {tuple(c.get("at") or ()) for c in cards}
            x, y = next(((sx, sy) for sx, sy in slots
                         if (sx + mon.get("x", 0), sy + mon.get("y", 0)) not in taken),
                        slots[len(cards) % APP_SLOTS])
            rule = (f'hl.window_rule({{ name = "bombadil-app-{name}", '
                    f'match = {{ class = "^(bombadil-app-{name})$" }}, move = {{ {x}, {y} }} }})')
            reply = self.request("eval " + rule).strip()
        except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError) as e:
            return f"no spot picked for {name}: {e}"
        return f"{name} opens at {x},{y}" if reply == "ok" else f"no spot picked for {name}: {reply}"

    def _panel_has_window(self, name: str) -> bool:
        return any(c.get("workspace", {}).get("name") == f"special:{name}" for c in self.clients())

    def panel(self, name: str, show: bool = True, wait: float = 20.0) -> str:
        """Slide a panel in (or out). Launches its app on first use and waits for its window,
        so a second call while the app is still starting does not launch it twice."""
        if name not in PANELS:
            raise ValueError(f"unknown panel {name!r}, known: {sorted(PANELS)}")
        note = ""
        if show and not self._panel_has_window(name):
            cmd = PANELS[name]
            if shutil.which(cmd[0]) is None:
                raise RuntimeError(f"{cmd[0]} is not installed")
            if not _launching(cmd):
                # hyprland.lua has a window rule per panel class that puts the window in its panel.
                subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + wait
            while not self._panel_has_window(name):
                if time.monotonic() > deadline:
                    note = ", its app is still starting"
                    break
                time.sleep(0.2)
        # togglespecialworkspace flips; make it idempotent by checking the active special.
        active = json.loads(self.request("j/monitors"))
        showing = any(m.get("specialWorkspace", {}).get("name") == f"special:{name}" for m in active)
        if show != showing:
            self.dispatch(f'hl.dsp.workspace.toggle_special("{name}")')
        return f"panel {name} {'shown' if show else 'hidden'}{note}"

    def screenshot(self, path: Path) -> Path:
        if shutil.which("grim") is None:
            raise RuntimeError("grim is not installed")
        subprocess.run(["grim", str(path)], check=True)
        return path

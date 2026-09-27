"""Talk to Hyprland over its IPC socket.

Panels are Hyprland "special workspaces": a scratch workspace that slides in over the
current one and slides back out. `panel("browser")` shows the browser panel, launching
Chromium into it if it is not running yet.
"""

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

PANELS: dict[str, list[str]] = {
    "browser": ["chromium", "--ozone-platform=wayland", "--remote-debugging-port=9222",
                "--class=bombadil-browser"],
    "terminal": ["foot", "--app-id=bombadil-terminal"],
    "files": ["nautilus"],
}


def _socket_path() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(base) / "hypr" / sig / ".socket.sock"


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
        out = subprocess.run(["hyprctl", "dispatch", lua], capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or out.stdout.strip() not in ("", "ok"):
            raise RuntimeError(f"hyprctl dispatch {lua!r}: {(out.stdout + out.stderr).strip()}")
        return out.stdout

    def clients(self) -> list[dict]:
        return json.loads(self.request("j/clients"))

    def _panel_has_window(self, name: str) -> bool:
        return any(c.get("workspace", {}).get("name") == f"special:{name}" for c in self.clients())

    def panel(self, name: str, show: bool = True) -> str:
        """Slide a panel in (or out). Launches its app on first use."""
        if name not in PANELS:
            raise ValueError(f"unknown panel {name!r}, known: {sorted(PANELS)}")
        if show and not self._panel_has_window(name):
            cmd = PANELS[name]
            if shutil.which(cmd[0]) is None:
                raise RuntimeError(f"{cmd[0]} is not installed")
            # hyprland.lua has a window rule per panel class that puts the window in its panel.
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        # togglespecialworkspace flips; make it idempotent by checking the active special.
        active = json.loads(self.request("j/monitors"))
        showing = any(m.get("specialWorkspace", {}).get("name") == f"special:{name}" for m in active)
        if show != showing:
            self.dispatch(f'hl.dsp.workspace.toggle_special("{name}")')
        return f"panel {name} {'shown' if show else 'hidden'}"

    def screenshot(self, path: Path) -> Path:
        if shutil.which("grim") is None:
            raise RuntimeError("grim is not installed")
        subprocess.run(["grim", str(path)], check=True)
        return path

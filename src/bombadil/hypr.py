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

    def dispatch(self, *args: str) -> str:
        return self.request("dispatch " + " ".join(args))

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
            self.dispatch("exec", f"[workspace special:{name} silent]", *cmd)
        # togglespecialworkspace flips; make it idempotent by checking the active special.
        active = json.loads(self.request("j/monitors"))
        showing = any(m.get("specialWorkspace", {}).get("name") == f"special:{name}" for m in active)
        if show != showing:
            self.dispatch("togglespecialworkspace", name)
        return f"panel {name} {'shown' if show else 'hidden'}"

    def screenshot(self, path: Path) -> Path:
        if shutil.which("grim") is None:
            raise RuntimeError("grim is not installed")
        subprocess.run(["grim", str(path)], check=True)
        return path

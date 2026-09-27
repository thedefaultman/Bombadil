"""os-mcp: the OS as tools.

A small MCP server (JSON-RPC over stdio) that gives whichever agent CLI is running the
same abilities: slide panels in and out, create and show native apps, screenshot,
snapshot and roll back, notify. Claude Code and Codex both load it from their MCP
config, so switching provider changes nothing here.

Implemented by hand rather than with the `mcp` package to keep the base image small.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import apps, hypr, paths, snapshots

PROTOCOL_VERSION = "2025-06-18"
Tool = tuple[dict, Callable[[dict], Any]]


class OsTools:
    def __init__(self, hyprland: hypr.Hyprland | None = None, snaps: snapshots.Snapshots | None = None):
        self.hypr = hyprland or hypr.Hyprland()
        self.snaps = snaps or snapshots.Snapshots()
        self.tools: dict[str, Tool] = {}
        self._register()

    def _tool(self, name: str, description: str, properties: dict, required: list[str] | None = None):
        def deco(fn):
            self.tools[name] = ({
                "name": name,
                "description": description,
                "inputSchema": {"type": "object", "properties": properties, "required": required or []},
            }, fn)
            return fn
        return deco

    def _register(self):
        t = self._tool
        panel_names = sorted(hypr.PANELS)

        @t("show_panel", "Slide a panel in over the screen (browser, terminal, files). Launches the app if needed.",
           {"name": {"type": "string", "enum": panel_names}}, ["name"])
        def show_panel(a):
            return self.hypr.panel(a["name"], show=True)

        @t("hide_panel", "Slide a panel back out.", {"name": {"type": "string", "enum": panel_names}}, ["name"])
        def hide_panel(a):
            return self.hypr.panel(a["name"], show=False)

        @t("create_app",
           "Create a native app from QML (Qt Quick) and optional Python, then open it as a real window. "
           "Use `import Bombadil` and its `AppWindow` component (not `Window`, which is QtQuick's) for the "
           "standard look; a `Backend` class in "
           "the Python is exposed to QML as `backend`. The file is hot reloaded, so call again to update.",
           {"title": {"type": "string"}, "qml": {"type": "string"}, "python": {"type": "string"},
            "description": {"type": "string"}, "open": {"type": "boolean", "default": True}},
           ["title", "qml"])
        def create_app(a):
            app = apps.create(a["title"], a["qml"], a.get("python"), a.get("description", ""))
            if not a.get("open", True):
                return f"app {app.name} written to {app.path}"
            if _is_running(app.name) and a.get("python") is not None:
                # The QML hot reloads, the Python behind it does not: restart for a new backend.
                _stop(app.name)
            if not _is_running(app.name):
                apps.run(app.name)
            return f"app {app.name} written to {app.path}; " + _app_state(app.name)

        @t("open_app", "Open a previously generated app.", {"name": {"type": "string"}}, ["name"])
        def open_app(a):
            if not _is_running(a["name"]):
                apps.run(a["name"])
            return _app_state(a["name"])

        @t("list_apps", "List generated apps.", {})
        def list_apps(_a):
            return [{"name": x.name, "title": x.title, "path": str(x.path)} for x in apps.list_apps()]

        @t("app_template", "Return the starter main.qml and app.py the agent should build from.", {})
        def app_template(_a):
            tpl = _share() / "app-template"
            return {"main.qml": (tpl / "main.qml").read_text(), "app.py": (tpl / "app.py").read_text()}

        @t("screenshot", "Take a screenshot of the whole screen and return it as an image.", {})
        def screenshot(_a):
            out = Path(tempfile.mkdtemp()) / "screen.png"
            self.hypr.screenshot(out)
            return {"image": base64.b64encode(out.read_bytes()).decode(), "mimeType": "image/png"}

        @t("snapshot", "Take a system snapshot you can roll back to.", {"description": {"type": "string"}}, ["description"])
        def snapshot(a):
            s = self.snaps.create(a["description"])
            return f"snapshot {s.number}" if s else "snapshots unavailable on this system"

        @t("list_snapshots", "List recent snapshots.", {})
        def list_snapshots(_a):
            return [{"number": s.number, "description": s.description} for s in self.snaps.list()]

        @t("rollback", "Roll the system back to a snapshot (takes effect on reboot). "
           "Without a number, undoes the last agent turn.",
           {"number": {"type": "integer"}})
        def rollback(a):
            if "number" in a:
                return "rolled back, reboot to apply" if self.snaps.rollback(a["number"]) else "snapshots unavailable"
            s = self.snaps.undo_last_turn()
            return f"rolled back to snapshot {s.number} ({s.description}), reboot to apply" if s else "nothing to undo"

        @t("notify", "Show a desktop notification.", {"title": {"type": "string"}, "body": {"type": "string"}}, ["title"])
        def notify(a):
            subprocess.run(["notify-send", a["title"], a.get("body", "")], check=False)
            return "shown"

    # MCP plumbing

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return _result(mid, {"protocolVersion": PROTOCOL_VERSION,
                                 "capabilities": {"tools": {}},
                                 "serverInfo": {"name": "bombadil-os", "version": "0.1.0"}})
        if method == "notifications/initialized" or mid is None:
            return None
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": [spec for spec, _ in self.tools.values()]})
        if method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments", {}) or {}
            if name not in self.tools:
                return _error(mid, -32602, f"unknown tool {name}")
            try:
                out = self.tools[name][1](args)
            except Exception as e:  # noqa: BLE001 - the agent should see failures as text, not a dead server
                return _result(mid, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
            return _result(mid, {"content": _content(out)})
        return _error(mid, -32601, f"method not found: {method}")

    def serve(self, stdin=sys.stdin, stdout=sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            reply = self.handle(msg)
            if reply is not None:
                stdout.write(json.dumps(reply) + "\n")
                stdout.flush()


def _content(out) -> list[dict]:
    if isinstance(out, dict) and "image" in out:
        return [{"type": "image", "data": out["image"], "mimeType": out["mimeType"]}]
    if isinstance(out, str):
        return [{"type": "text", "text": out}]
    return [{"type": "text", "text": json.dumps(out, indent=2)}]


def _result(mid, result): return {"jsonrpc": "2.0", "id": mid, "result": result}
def _error(mid, code, message): return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _share() -> Path:
    local = Path(__file__).resolve().parents[2] / "share"
    return local if local.exists() else paths.share_dir()


def _is_running(name: str) -> bool:
    r = subprocess.run(["pgrep", "-f", f"bombadil-app run {name}$"], capture_output=True, check=False)
    return r.returncode == 0


def _stop(name: str) -> None:
    subprocess.run(["pkill", "-f", f"bombadil-app run {name}$"], capture_output=True, check=False)
    for _ in range(20):
        if not _is_running(name):
            return
        time.sleep(0.1)


def _app_state(name: str, wait: float = 1.5) -> str:
    """Give the window a moment, then say whether it is up and pass on any QML errors."""
    log = apps.log_path(name)
    size = log.stat().st_size if log.exists() else 0
    time.sleep(wait)
    new = log.read_text(errors="replace")[size:].strip() if log.exists() else ""
    state = "running" if _is_running(name) else "NOT running (it exited)"
    return f"the app is {state}" + (f". Its log says:\n{new[-2000:]}" if new else "")


def main() -> None:
    # stdout is the JSON-RPC transport: serve on a private copy of it and point fd 1 at stderr,
    # so no child process (snapper, grim, an app) or stray print can write into the stream.
    transport = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    OsTools().serve(sys.stdin, transport)

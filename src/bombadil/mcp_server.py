"""os-mcp: the OS as tools.

A small MCP server (JSON-RPC over stdio) that gives whichever agent CLI is running the
same abilities: slide panels in and out, create and show native apps, draw pictures,
screenshot, snapshot and roll back, notify, arrange the desk and run jobs in the background.
Claude Code and Codex both load it from their MCP config, so switching provider changes
nothing here.

Implemented by hand rather than with the `mcp` package to keep the base image small.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import apps, hypr, paths, snapshots
from .desk import RAILS, WIDGETS

PROTOCOL_VERSION = "2025-06-18"
Tool = tuple[dict, Callable[[dict], Any]]
DESK_TIMEOUT = 10.0   # seconds agentd has to answer a desk request
JOB_TIMEOUT = 25.0    # and a job request: starting one waits for systemd


class ToolError(Exception):
    """A tool that did not do what was asked, and says why in plain words: the agent sees the
    text as it is, not as a Python error."""


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
                self.hypr.place_app(app.name)
                apps.run(app.name)
            return f"app {app.name} written to {app.path}; " + _app_state(app.name)

        @t("open_app", "Open a previously generated app.", {"name": {"type": "string"}}, ["name"])
        def open_app(a):
            if not _is_running(a["name"]):
                self.hypr.place_app(a["name"])
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

        @t("desk",
           "Arrange the desk: the widgets in the two rails beside the pill (now, watching, needs, away, "
           "machine, alive). This works ONLY when the person asked for the desk or a widget in their own "
           "words this turn (\"put watching on the right\", \"hide machine\", \"fold the desk\"). In any "
           "other turn it is refused and nothing changes; never rearrange the desk on your own. Ops: show and "
           "hide a widget (needs cannot be hidden); move a widget to a rail (left or right) and a rank "
           "(0 is nearest the pill, and without a rank it goes last); fold and unfold every widget to a "
           "chip beside the pill and back; state says what is where.",
           {"op": {"type": "string", "enum": ["show", "hide", "move", "fold", "unfold", "state"]},
            "widget": {"type": "string", "enum": list(WIDGETS)},
            "rail": {"type": "string", "enum": list(RAILS)},
            "rank": {"type": "integer", "minimum": 0}},
           ["op"])
        def desk(a):
            return _desk(a)

        @t("job",
           "Run something in the background and keep it on the desk. Use this instead of `&`, `nohup` or a "
           "long `sleep` for anything that takes more than a few seconds or that the person will want to "
           "hear about later (a download, an install, a build, a timer, waiting for something to finish): "
           "the desk's Watching card counts it, says so in one line when it ends, and stops it when the "
           "person asks, and a job you start keeps running after your turn ends. Ops: start runs `command` "
           "(a shell command; what it prints goes to a log, and a line like 43% in it becomes the meter) "
           "under `title`, a short name the person will read (\"Ubuntu 26.04 ISO\"). kind job is the "
           "default; kind watch is a one-shot watcher on something already running, whose command waits "
           "until that ends (`tail --pid=1234 -f /dev/null`, or `while pgrep -x make >/dev/null; do sleep "
           "2; done`) and whose title names the thing (\"the build\"). With `seconds` instead of a "
           "command it is a timer, and a command given with seconds runs once the time is up. list says "
           "what is running; stop ends one by its id.",
           {"op": {"type": "string", "enum": ["start", "list", "stop"]},
            "title": {"type": "string"}, "command": {"type": "string"},
            "kind": {"type": "string", "enum": ["job", "watch"]},
            "seconds": {"type": "integer", "minimum": 1}, "id": {"type": "string"}},
           ["op"])
        def job(a):
            return _job(a)

        from . import cardtools  # pictures: show_card, system_map
        cardtools.register(self)

        from .mail import tools as mail_tools  # mail_search, mail_read, mail_mark, mail_draft, mail_show
        mail_tools.register(self)

        from .appkit import tools as app_tools  # the app kit's tools; they replace the app tools above
        app_tools.register(self)   # last: the tools of the app kit are listed together, at the end

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
            except ToolError as e:
                return _result(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
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
    if getattr(out, "is_mcp_content", False):  # appkit.tools.Blocks: already text/image blocks
        return list(out)
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


def _desk(a: dict) -> str:
    """Ask agentd to change the desk and wait for its answer. agentd keeps the desk and decides
    whether this turn's words asked for it; this only carries the request, for the turn agentd
    started this process in (BOMBADIL_TURN)."""
    turn = os.environ.get("BOMBADIL_TURN", "")
    if not turn.isdigit():
        raise ToolError("The desk can only be changed from inside a turn, and this process was not told "
                        "which.")
    msg = {"type": "desk-tool", "id": uuid.uuid4().hex, "turn": int(turn),
           **{k: a[k] for k in ("op", "widget", "rail", "rank") if a.get(k) is not None}}
    reply = _ask_agentd(msg, "desk-result", DESK_TIMEOUT)
    if not reply.get("ok"):
        raise ToolError(str(reply.get("text") or "The desk was not changed."))
    return str(reply.get("text") or "Done.")


def _job(a: dict) -> str:
    """Ask agentd to start, list or stop a background job and wait for its answer. agentd keeps
    the jobs and checks that this turn is the one running; this only carries the request. The
    job's own id travels as `job`, since `id` names the request."""
    turn = os.environ.get("BOMBADIL_TURN", "")
    if not turn.isdigit():
        raise ToolError("Background jobs can only be managed from inside a turn, and this process was not "
                        "told which.")
    msg = {"type": "job-tool", "id": uuid.uuid4().hex, "turn": int(turn),
           **{k: a[k] for k in ("op", "title", "command", "kind", "seconds") if a.get(k) is not None}}
    if a.get("id") is not None:
        msg["job"] = a["id"]
    reply = _ask_agentd(msg, "job-result", JOB_TIMEOUT, "the job table", "`list` says what is running.")
    if not reply.get("ok"):
        raise ToolError(str(reply.get("text") or "Nothing was changed."))
    return str(reply.get("text") or "Done.")


def _ask_agentd(msg: dict, answer: str, timeout: float, subject: str = "the desk",
                recheck: str = "the `state` op says what it is now.") -> dict:
    """Send one line to agentd and wait for the reply of type `answer` with this message's id.
    agentd greets every client with its status and names, and broadcasts what the turn does:
    all of that is skipped. `subject` is what may be unchanged when no answer comes."""
    path = paths.socket_path()
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall((json.dumps(msg) + "\n").encode())
            buf = b""
            while True:
                s.settimeout(max(deadline - time.monotonic(), 0.001))
                chunk = s.recv(65536)
                if not chunk:
                    raise ToolError(f"agentd hung up before it answered, so {subject} may be unchanged.")
                *lines, buf = (buf + chunk).split(b"\n")
                for line in lines:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(m, dict) and m.get("type") == answer and m.get("id") == msg["id"]:
                        return m
    except TimeoutError:
        raise ToolError(f"agentd did not answer within {timeout:g} seconds, so {subject} may be unchanged; "
                        f"{recheck}") from None
    except OSError as e:
        raise ToolError(f"agentd is not answering on {path} ({e.strerror or e}), so {subject} is unchanged."
                        ) from None


def main() -> None:
    # stdout is the JSON-RPC transport: serve on a private copy of it and point fd 1 at stderr,
    # so no child process (snapper, grim, an app) or stray print can write into the stream.
    transport = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    OsTools().serve(sys.stdin, transport)

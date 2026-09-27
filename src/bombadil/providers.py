"""Provider adapters: how agentd runs Claude Code and Codex.

Each adapter builds the command for one turn and turns the CLI's streaming output into
Bombadil events: {"kind": "text"|"tool"|"result"|"error", ...}. Both run in their
full-access mode; Bombadil has no guard rails, the undo is the snapshot.

The CLI flags live only here, so a CLI change is a one-file fix.
"""

import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

SYSTEM_PROMPT = (
    "You are the operating system's agent on Bombadil, a Linux distro whose main interface is you. "
    "The user talks to you instead of clicking around. Use the bombadil-os tools to show what they ask "
    "for: slide the browser in with show_panel, build native apps with create_app (Qt Quick/QML, hot "
    "reloaded, no web servers), take screenshots to check your work, and use rollback when the user says "
    "undo. You have full access to this machine as the user, with passwordless sudo; act, don't ask for "
    "permission. Keep spoken replies short: the UI is a small bar, the work shows up on screen."
)


@dataclass
class Turn:
    prompt: str
    session_id: str | None = None   # provider's id for continuing the conversation
    cwd: Path = field(default_factory=Path.home)


def mcp_config(path: Path, mcp_command: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"bombadil-os": {"command": mcp_command, "args": []}}}))
    return path


class Provider:
    """One provider CLI. agentd writes the prompt to the CLI's stdin (so a prompt that starts
    with "-" or names a subcommand is never parsed as arguments) and feeds each stdout line to
    `parse` as it arrives, so the bar streams the reply."""
    name = ""
    binary = ""

    def __init__(self, mcp_command: str, model: str | None = None):
        self.mcp_command = mcp_command
        self.model = model

    @property
    def installed(self) -> bool:
        return shutil.which(self.binary) is not None

    def command(self, turn: Turn, workdir: Path) -> list[str]:
        raise NotImplementedError

    def parse(self, line: str) -> Iterator[dict]:
        """Events for one line of the CLI's output."""
        raise NotImplementedError

    def finish(self) -> Iterator[dict]:
        """Events once the output has ended."""
        return iter(())

    def events(self, lines) -> Iterator[dict]:
        for line in lines:
            yield from self.parse(line)
        yield from self.finish()

    def login_command(self) -> list[str]:
        return [self.binary]


def _json(line: str) -> dict | None:
    try:
        m = json.loads(line)
    except json.JSONDecodeError:
        return None
    return m if isinstance(m, dict) else None


class Claude(Provider):
    name = "claude"
    binary = "claude"

    def command(self, turn: Turn, workdir: Path) -> list[str]:
        cmd = [self.binary, "-p",
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions",
               "--mcp-config", str(mcp_config(workdir / "claude-mcp.json", self.mcp_command)),
               "--append-system-prompt", SYSTEM_PROMPT]
        if self.model:
            cmd += ["--model", self.model]
        if turn.session_id:
            cmd += ["--resume", turn.session_id]
        return cmd

    def parse(self, line):
        m = _json(line)
        if m is None:
            return
        t = m.get("type")
        if t == "system" and m.get("subtype") == "init":
            yield {"kind": "session", "session_id": m.get("session_id")}
            servers = {s.get("name"): s.get("status") for s in m.get("mcp_servers", []) if isinstance(s, dict)}
            if servers.get("bombadil-os") not in ("connected", "pending"):
                yield {"kind": "error", "text": f"the OS tools did not start (bombadil-os: {servers.get('bombadil-os', 'missing')})"}
        elif t == "assistant":
            for block in m.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    yield {"kind": "text", "text": block["text"]}
                elif block.get("type") == "tool_use":
                    yield {"kind": "tool", "name": block.get("name"), "input": block.get("input", {})}
        elif t == "result":
            ok = not m.get("is_error", False)
            text = m.get("result") or ""
            if not ok and not text:
                errs = m.get("errors") or []
                text = "\n".join(e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errs)
            yield {"kind": "result", "ok": ok, "text": text, "session_id": m.get("session_id"),
                   "subtype": m.get("subtype"), "terminal_reason": m.get("terminal_reason"),
                   "num_turns": m.get("num_turns")}

    def login_command(self):
        return [self.binary, "auth", "login"]


# What the OS tools need from the session; Codex starts MCP servers with only HOME/PATH/etc.
MCP_ENV = ["HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DISPLAY",
           "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
           "XDG_SESSION_TYPE", "BOMBADIL_TURN_SNAPSHOT", "BOMBADIL_SOCKET", "BOMBADIL_APPS",
           "BOMBADIL_STATE", "BOMBADIL_SHARE"]


class Codex(Provider):
    name = "codex"
    binary = "codex"

    def command(self, turn: Turn, workdir: Path) -> list[str]:
        self._last_text = ""
        # The same overrides on a fresh and a resumed turn; a resumed turn without them loses the
        # OS tools. developer_instructions appends to Codex's prompt (instructions replaces it).
        overrides = ["-c", f"mcp_servers.bombadil-os.command={json.dumps(self.mcp_command)}",
                     "-c", f"mcp_servers.bombadil-os.env_vars={json.dumps(MCP_ENV)}",
                     "-c", f"developer_instructions={json.dumps(SYSTEM_PROMPT)}",
                     "--skip-git-repo-check"]
        if self.model:
            overrides += ["--model", self.model]
        base = [self.binary, "exec"]
        if turn.session_id:
            # `resume` takes no -C; agentd already runs the CLI in the turn's cwd.
            return [*base, "resume", "--json", "--dangerously-bypass-approvals-and-sandbox",
                    *overrides, turn.session_id, "-"]
        return [*base, "--json", "--dangerously-bypass-approvals-and-sandbox", *overrides,
                "-C", str(turn.cwd), "-"]

    def parse(self, line):
        m = _json(line)
        if m is None:
            return
        t = m.get("type", "")
        item = m.get("item", {})
        if t == "thread.started":
            yield {"kind": "session", "session_id": m.get("thread_id")}
        elif t == "item.completed" and item.get("type") == "agent_message":
            self._last_text = item.get("text", "")
            yield {"kind": "text", "text": self._last_text}
        elif t == "item.started" and item.get("type") in ("command_execution", "mcp_tool_call"):
            yield {"kind": "tool", "name": item.get("command") or item.get("tool"),
                   "input": item.get("arguments", {})}
        elif t == "turn.completed":
            yield {"kind": "result", "ok": True, "text": getattr(self, "_last_text", "")}
        elif t == "turn.failed":
            err = m.get("error") or {}
            text = err.get("message", "codex turn failed") if isinstance(err, dict) else str(err)
            yield {"kind": "result", "ok": False, "text": text}
        elif t == "error":
            # Top-level errors are retry notices ("Reconnecting... 2/5"); turn.failed is the failure.
            yield {"kind": "text", "text": m.get("message", "")}

    def login_command(self):
        return [self.binary, "login"]


PROVIDERS: dict[str, type[Provider]] = {"claude": Claude, "codex": Codex}


def get(name: str, mcp_command: str | None = None, model: str | None = None) -> Provider:
    mcp = mcp_command or shutil.which("bombadil-os-mcp") or str(
        Path(__file__).resolve().parents[2] / "bin" / "bombadil-os-mcp")
    return PROVIDERS[name](mcp, model)


class Fake(Provider):
    """For tests and for running the shell without any provider: echoes the prompt."""
    name = "fake"
    binary = os.environ.get("BOMBADIL_FAKE_PROVIDER", "cat")

    @property
    def installed(self) -> bool:
        return True

    def command(self, turn, workdir):
        self._seen = []
        return [self.binary]

    def parse(self, line):
        if line.strip():
            self._seen.append(line.strip())
            yield {"kind": "text", "text": f"echo: {line.strip()}"}

    def finish(self):
        text = " ".join(getattr(self, "_seen", []))
        yield {"kind": "result", "ok": True, "text": f"echo: {text}"}


PROVIDERS["fake"] = Fake

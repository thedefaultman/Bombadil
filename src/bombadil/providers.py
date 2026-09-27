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

    def events(self, lines: Iterator[str]) -> Iterator[dict]:
        raise NotImplementedError

    def login_command(self) -> list[str]:
        return [self.binary]


class Claude(Provider):
    name = "claude"
    binary = "claude"

    def command(self, turn: Turn, workdir: Path) -> list[str]:
        cmd = [self.binary, "-p", turn.prompt,
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions",
               "--mcp-config", str(mcp_config(workdir / "claude-mcp.json", self.mcp_command)),
               "--append-system-prompt", SYSTEM_PROMPT]
        if self.model:
            cmd += ["--model", self.model]
        if turn.session_id:
            cmd += ["--resume", turn.session_id]
        return cmd

    def events(self, lines):
        for line in lines:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = m.get("type")
            if t == "system" and m.get("subtype") == "init":
                yield {"kind": "session", "session_id": m.get("session_id")}
            elif t == "assistant":
                for block in m.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        yield {"kind": "text", "text": block["text"]}
                    elif block.get("type") == "tool_use":
                        yield {"kind": "tool", "name": block.get("name"), "input": block.get("input", {})}
            elif t == "result":
                yield {"kind": "result", "ok": not m.get("is_error", False),
                       "text": m.get("result", ""), "session_id": m.get("session_id")}

    def login_command(self):
        return [self.binary, "/login"]


class Codex(Provider):
    name = "codex"
    binary = "codex"

    def command(self, turn: Turn, workdir: Path) -> list[str]:
        cmd = [self.binary, "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
               "--skip-git-repo-check", "-C", str(turn.cwd),
               "-c", f'mcp_servers.bombadil-os.command="{self.mcp_command}"',
               "-c", f"instructions={json.dumps(SYSTEM_PROMPT)}"]
        if self.model:
            cmd += ["--model", self.model]
        if turn.session_id:
            cmd = [self.binary, "exec", "resume", "--json", "--dangerously-bypass-approvals-and-sandbox",
                   turn.session_id]
        cmd.append(turn.prompt)
        return cmd

    def events(self, lines):
        for line in lines:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = m.get("type", "")
            item = m.get("item", {})
            if t == "thread.started":
                yield {"kind": "session", "session_id": m.get("thread_id")}
            elif t == "item.completed" and item.get("type") == "agent_message":
                yield {"kind": "text", "text": item.get("text", "")}
            elif t == "item.started" and item.get("type") in ("command_execution", "mcp_tool_call"):
                yield {"kind": "tool", "name": item.get("command") or item.get("tool"),
                       "input": item.get("arguments", {})}
            elif t == "turn.completed":
                yield {"kind": "result", "ok": True, "text": ""}
            elif t == "error":
                yield {"kind": "error", "text": m.get("message", "codex error")}

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
        return [self.binary]

    def events(self, lines):
        text = "".join(lines).strip()
        yield {"kind": "text", "text": f"echo: {text}"}
        yield {"kind": "result", "ok": True, "text": f"echo: {text}"}


PROVIDERS["fake"] = Fake

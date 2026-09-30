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
    "permission. Install software with `sudo pacman -Syu --noconfirm --needed <packages>`, and never "
    "`pacman -Sy` alone (Arch breaks on a partial upgrade). If an upgrade replaced the kernel, tell the "
    "user a restart is needed: until then modprobe cannot load modules. The user sees your work on "
    "screen and your final reply as at most four lines above the bar: one or two plain sentences saying what you did, "
    "no markdown, no lists."
)


DIAGNOSTIC = "[ede_diagnostic]"   # what the CLI prints when a turn was cut short


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
    # How many times agentd's no-progress wait (agentd.NO_PROGRESS_SECS) this CLI may stay silent
    # before the line says it is not answering.
    quiet_factor = 1.0

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

    def is_progress(self, line: str) -> bool:
        """Does this output line show the provider is working? Lines that parse to no event
        still do (a thinking delta is nothing to show and everything to a watchdog); only a
        CLI's own notices about a connection that is not working do not."""
        return True

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


def _result_text(content) -> str:
    """A tool result's text: a string, or a list of text (and image) blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')}]"
                         for b in content if isinstance(b, dict))
    return "" if content is None else json.dumps(content)


class Claude(Provider):
    name = "claude"
    binary = "claude"

    def command(self, turn: Turn, workdir: Path) -> list[str]:
        cmd = [self.binary, "-p",
               "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--permission-mode", "bypassPermissions", "--dangerously-skip-permissions",
               "--mcp-config", str(mcp_config(workdir / "claude-mcp.json", self.mcp_command)),
               # Only Bombadil's own tools: the account's claude.ai connectors (Gmail, Drive) would
               # load too and end replies with notices to authorize them.
               "--strict-mcp-config",
               "--append-system-prompt", SYSTEM_PROMPT]
        if self.model:
            cmd += ["--model", self.model]
        if turn.session_id:
            cmd += ["--resume", turn.session_id]
        return cmd

    def is_progress(self, line):
        m = _json(line)
        # "status" and "api_retry" are the CLI talking about itself (retrying an unreachable API).
        return not (m and m.get("type") == "system" and m.get("subtype") in ("status", "api_retry"))

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
        elif t == "stream_event":
            # --include-partial-messages: the reply and each tool call as they are written, for the
            # live line only (the complete message follows as "assistant").
            e = m.get("event") or {}
            et = e.get("type")
            if et == "content_block_start":
                block = e.get("content_block") or {}
                if block.get("type") == "tool_use":
                    yield {"kind": "tool_start", "index": e.get("index", 0), "name": block.get("name", ""),
                           "id": block.get("id")}
                elif block.get("type") in ("thinking", "redacted_thinking"):
                    yield {"kind": "thinking"}
            elif et == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    yield {"kind": "text_delta", "text": d["text"]}
                elif d.get("type") == "input_json_delta":
                    yield {"kind": "tool_input", "index": e.get("index", 0), "partial": d.get("partial_json", "")}
        elif t == "assistant":
            for block in m.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    yield {"kind": "text", "text": block["text"]}
                elif block.get("type") == "tool_use":
                    yield {"kind": "tool", "name": block.get("name"), "input": block.get("input", {}),
                           "id": block.get("id")}
        elif t == "user":
            content = m.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    yield {"kind": "tool_result", "id": block.get("tool_use_id"),
                           "output": _result_text(block.get("content")), "error": bool(block.get("is_error"))}
        elif t == "result":
            ok = not m.get("is_error", False)
            text = m.get("result") or ""
            if text.startswith(DIAGNOSTIC):
                text = ""
            if not ok and not text:
                errs = [e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in m.get("errors") or []]
                # A Stop leaves the CLI's own diagnostic in errors[] with result null
                # ({"is_error": true, "terminal_reason": "aborted_streaming"}): not words for the user.
                text = "\n".join(e for e in errs if not e.startswith(DIAGNOSTIC))
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
    # It prints nothing while it reasons, so a long think looks like a dead connection: wait longer.
    quiet_factor = 6.0

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
        elif t == "item.started" and item.get("type") == "command_execution":
            yield {"kind": "tool", "name": "Bash", "input": {"command": item.get("command", "")}, "id": item.get("id")}
        elif t == "item.started" and item.get("type") == "mcp_tool_call":
            yield {"kind": "tool", "name": f"mcp__{item.get('server', '')}__{item.get('tool', '')}",
                   "input": item.get("arguments") or {}, "id": item.get("id")}
        elif t == "item.completed" and item.get("type") == "reasoning":
            # Codex streams no text, but its reasoning summary is a plain heading: "**Installing ffmpeg**".
            head = (item.get("text") or "").strip().splitlines()
            yield {"kind": "thinking", "text": head[0].strip("*# ").strip() if head else ""}
        elif t in ("item.started", "item.updated") and item.get("type") == "todo_list":
            todo = next((i for i in item.get("items") or [] if isinstance(i, dict) and not i.get("completed")), None)
            if todo:
                yield {"kind": "tool", "name": "TodoWrite",
                       "input": {"todos": [{"content": todo.get("text", ""), "status": "in_progress"}]}}
        elif t == "item.completed" and item.get("type") == "command_execution":
            code = item.get("exit_code")
            yield {"kind": "tool_result", "id": item.get("id"), "output": item.get("aggregated_output") or "",
                   "error": code not in (0, None), "exit_code": code}
        elif t == "item.completed" and item.get("type") == "mcp_tool_call":
            res, err = item.get("result"), item.get("error")
            out = err.get("message", "") if isinstance(err, dict) else (err or "")
            if not out and isinstance(res, dict):
                out = _result_text(res.get("content"))
            yield {"kind": "tool_result", "id": item.get("id"), "output": out, "error": bool(err)}
        elif t == "item.started" and item.get("type") == "file_change":
            yield {"kind": "file_change", "id": item.get("id"), "changes": item.get("changes") or []}
        elif t == "item.completed" and item.get("type") == "file_change":
            yield {"kind": "tool_result", "id": item.get("id"), "output": "",
                   "error": item.get("status") == "failed"}
        elif t == "item.completed" and item.get("type") == "web_search":
            # No id: it arrives once, already finished, and nothing ever answers it (an id would
            # mark a tool as running for the rest of the turn).
            yield {"kind": "tool", "name": "WebSearch", "input": {"query": item.get("query", "")}}
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


class Shell(Provider):
    """A "!command" typed into the pill: sh runs it, its output streams like the agent's words."""
    name = "shell"
    binary = "sh"

    def __init__(self):
        super().__init__("", None)
        self.tail: list[str] = []
        self.returncode: int | None = None

    def parse(self, line):
        line = line.rstrip("\n")
        self.tail = (self.tail + [line])[-200:]
        if line.strip():
            yield {"kind": "output", "text": line}

    def finish(self):
        out = "\n".join(self.tail).strip()
        yield {"kind": "tool_result", "id": "shell", "output": out, "error": self.returncode not in (0, None),
               "exit_code": self.returncode}
        last = "\n".join(line for line in self.tail if line.strip())
        if self.returncode in (0, None):
            yield {"kind": "result", "ok": True, "text": "\n".join(last.splitlines()[-4:])}
        else:
            text = "\n".join(last.splitlines()[-3:])
            yield {"kind": "result", "ok": False, "text": (text + "\n" if text else "") + f"(exit {self.returncode})"}


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

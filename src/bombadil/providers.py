"""Provider adapters: how agentd runs Claude Code and Codex.

Each adapter builds the command for one turn and turns the CLI's streaming output into
Bombadil events: {"kind": "text"|"tool"|"result"|"error", ...}. Both run in their
full-access mode; Bombadil has no guard rails, the undo is the snapshot.

What the CLIs say about the turn itself (the model, the cost, the tokens, a rate-limit notice,
a line of a type the adapter has never seen) comes out as one more event kind, "meta":
{"kind": "meta", "model"?, "cost"?, "usage"?, "rate_limit"?, "drift"?}. agentd keeps it for the
turn's ledger row and shows it to nobody. `usage` is {"input", "output", "cache_read",
"cache_write"} for both CLIs, and "input" never counts what was read from the cache.

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
    "permission. The user sees your work on screen and your final reply as at most four lines above the "
    "bar: one or two plain sentences saying what you did, no markdown, no lists."
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

    def env(self) -> dict[str, str]:
        """What the CLI needs in its environment on top of the daemon's own."""
        return {}

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


def _drift(m: dict, known) -> Iterator[dict]:
    """A line that is valid JSON with a `type` the adapter has never heard of. It is counted, not
    acted on: a CLI that changes its stream shows up here before it breaks anything."""
    if "type" in m:
        t = m["type"]
        if not isinstance(t, str) or t not in known:
            yield {"kind": "meta", "drift": str(t)[:60] or "(empty)"}


def _count(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _usage(input_tokens, output, cache_read, cache_write) -> dict | None:
    """The four numbers every row carries. None when the CLI gave none of them; a number it
    left out is 0."""
    counts = [_count(v) for v in (input_tokens, output, cache_read, cache_write)]
    if all(c is None for c in counts):
        return None
    return dict(zip(("input", "output", "cache_read", "cache_write"), (c or 0 for c in counts)))


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
               "--append-system-prompt", SYSTEM_PROMPT]
        if self.model:
            cmd += ["--model", self.model]
        if turn.session_id:
            cmd += ["--resume", turn.session_id]
        return cmd

    def env(self):
        # The plan on the desk is Claude Code's own task list, which some models only get when asked.
        return {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}

    # Every `type` this parser reads or knowingly lets pass; any other is drift.
    known_types = frozenset({"system", "stream_event", "assistant", "user", "result", "rate_limit_event"})

    def parse(self, line):
        m = _json(line)
        if m is None:
            return
        yield from _drift(m, self.known_types)
        parent = m.get("parent_tool_use_id")
        for ev in self._events(m):
            # A subagent's tools are its own work, not the turn's: the plan must not read them.
            if isinstance(parent, str) and parent and ev["kind"] in ("tool_start", "tool", "tool_result"):
                ev["parent"] = parent
            yield ev

    def _events(self, m):
        t = m.get("type")
        if t == "system" and m.get("subtype") == "init":
            yield {"kind": "session", "session_id": m.get("session_id")}
            if isinstance(m.get("model"), str) and m["model"]:
                yield {"kind": "meta", "model": m["model"]}
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
        elif t == "rate_limit_event":
            if isinstance(m.get("rate_limit_info"), dict):
                yield {"kind": "meta", "rate_limit": m["rate_limit_info"]}
        elif t == "result":
            ok = not m.get("is_error", False)
            text = m.get("result") or ""
            if not ok and not text:
                errs = m.get("errors") or []
                text = "\n".join(e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errs)
            meta = {}
            cost = m.get("total_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                meta["cost"] = cost
            u = m.get("usage")
            if isinstance(u, dict):
                usage = _usage(u.get("input_tokens"), u.get("output_tokens"),
                               u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"))
                if usage:
                    meta["usage"] = usage
            if meta:
                yield {"kind": "meta", **meta}
            yield {"kind": "result", "ok": ok, "text": text, "session_id": m.get("session_id"),
                   "subtype": m.get("subtype"), "terminal_reason": m.get("terminal_reason"),
                   "num_turns": m.get("num_turns")}

    def login_command(self):
        return [self.binary, "auth", "login"]


# What the OS tools need from the session; Codex starts MCP servers with only HOME/PATH/etc.
MCP_ENV = ["HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DISPLAY",
           "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
           "XDG_SESSION_TYPE", "BOMBADIL_TURN_SNAPSHOT", "BOMBADIL_SOCKET", "BOMBADIL_APPS",
           "BOMBADIL_STATE", "BOMBADIL_SHARE", "BOMBADIL_TURN"]


def _codex_todos(items) -> list[dict]:
    """Codex's todo_list ({text, completed} per item) as TodoWrite's todos: the first item not
    done is the one under way, the rest are waiting."""
    todos = []
    current = False
    for i in items if isinstance(items, list) else []:
        if not isinstance(i, dict):
            continue
        if i.get("completed"):
            status = "completed"
        elif not current:
            status, current = "in_progress", True
        else:
            status = "pending"
        todos.append({"content": str(i.get("text") or ""), "status": status, "activeForm": None})
    return todos


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
                     # Codex offers update_plan (the plan on the desk) only when its config says so.
                     "-c", "tools.update_plan.enabled=true",
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

    known_types = frozenset({"thread.started", "turn.started", "turn.completed", "turn.failed",
                             "item.started", "item.updated", "item.completed", "error"})

    def parse(self, line):
        m = _json(line)
        if m is None:
            return
        yield from _drift(m, self.known_types)
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
        elif t in ("item.started", "item.updated", "item.completed") and item.get("type") == "todo_list":
            # The whole plan on every change, the way Claude's TodoWrite sends it.
            todos = _codex_todos(item.get("items"))
            if todos:
                yield {"kind": "tool", "name": "TodoWrite", "input": {"todos": todos}, "id": item.get("id")}
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
            yield {"kind": "tool", "name": "WebSearch", "input": {"query": item.get("query", "")}, "id": item.get("id")}
        elif t == "turn.completed":
            meta = {}
            if self.model:
                meta["model"] = self.model   # Codex does not say which model answered, only what was asked for
            u = m.get("usage")
            if isinstance(u, dict):
                # Codex's input_tokens counts the cached part too; a row's "input" does not.
                fresh, cached = _count(u.get("input_tokens")), _count(u.get("cached_input_tokens"))
                usage = _usage(None if fresh is None else max(fresh - (cached or 0), 0),
                               u.get("output_tokens"), cached, None)
                if usage:
                    meta["usage"] = usage
            if meta:
                yield {"kind": "meta", **meta}
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

import json
from pathlib import Path

from bombadil import providers


def test_claude_command_and_events(tmp_path):
    p = providers.Claude("/usr/bin/bombadil-os-mcp", model="opus")
    cmd = p.command(providers.Turn("hi", session_id="abc"), tmp_path)
    assert cmd[:2] == ["claude", "-p"] and "hi" not in cmd  # the prompt goes on stdin
    assert "--dangerously-skip-permissions" in cmd and "--resume" in cmd and "--model" in cmd
    cfg = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text())
    assert cfg["mcpServers"]["bombadil-os"]["command"] == "/usr/bin/bombadil-os-mcp"
    assert cmd[cmd.index("--mcp-config") + 2] == "--strict-mcp-config"   # no claude.ai connectors beside it

    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1",
                    "mcp_servers": [{"name": "bombadil-os", "status": "connected"}]}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Opening"}, {"type": "tool_use", "name": "show_panel", "input": {"name": "browser"}}]}}),
        "garbage",
        json.dumps({"type": "result", "result": "done", "is_error": False, "session_id": "s1"}),
    ]
    ev = list(p.events(lines))
    assert [e["kind"] for e in ev] == ["session", "text", "tool", "result"]
    assert ev[2]["input"] == {"name": "browser"}


def test_claude_error_results_keep_their_reason_and_mcp_failures_show():
    p = providers.Claude("x")
    ev = list(p.events([
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1",
                    "mcp_servers": [{"name": "bombadil-os", "status": "failed"}]}),
        json.dumps({"type": "result", "is_error": True, "subtype": "error_during_execution",
                    "errors": ["No conversation found with session ID: s0"], "num_turns": 0}),
    ]))
    assert [e["kind"] for e in ev] == ["session", "error", "result"]
    assert "failed" in ev[1]["text"]
    assert ev[2]["ok"] is False and "No conversation found" in ev[2]["text"] and ev[2]["num_turns"] == 0


def test_codex_command_and_events(tmp_path):
    p = providers.Codex("/usr/bin/bombadil-os-mcp")
    cmd = p.command(providers.Turn("hi"), tmp_path)
    assert cmd[:2] == ["codex", "exec"] and cmd[-1] == "-" and "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert any(c.startswith("developer_instructions=") for c in cmd)
    assert not any(c.startswith("instructions=") for c in cmd)
    resumed = p.command(providers.Turn("again", session_id="t1"), tmp_path)
    assert resumed[2] == "resume" and resumed[-2:] == ["t1", "-"] and "-C" not in resumed
    # A resumed turn keeps the OS tools and the environment they need.
    assert any("mcp_servers.bombadil-os.command=" in c for c in resumed)
    env = next(c for c in resumed if "env_vars=" in c)
    assert "HYPRLAND_INSTANCE_SIGNATURE" in env and "WAYLAND_DISPLAY" in env
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "t1"}),
        json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Hello"}}),
        json.dumps({"type": "error", "message": "Reconnecting... 2/5"}),
        json.dumps({"type": "turn.completed"}),
    ]
    ev = list(p.events(lines))
    assert [e["kind"] for e in ev] == ["session", "tool", "text", "text", "result"]
    assert ev[-1]["text"] == "Hello"
    failed = list(p.events([json.dumps({"type": "turn.failed", "error": {"message": "quota"}})]))
    assert failed == [{"kind": "result", "ok": False, "text": "quota"}]


def test_registry():
    assert providers.get("claude").name == "claude"
    assert providers.get("codex").name == "codex"


FIXTURES = Path(__file__).parent / "fixtures"


def _kinds(p, name):
    return [e for line in (FIXTURES / name).read_text().splitlines() for e in p.parse(line)]


def test_claude_streams_the_reply_and_each_tool_call_as_they_are_written():
    p = providers.Claude("x")
    assert "--include-partial-messages" in p.command(providers.Turn("hi"), Path("/tmp"))
    ev = _kinds(p, "claude-install-ffmpeg.jsonl")
    kinds = [e["kind"] for e in ev]
    # Streamed pieces come before the complete message they belong to.
    assert kinds.index("text_delta") < kinds.index("text")
    assert kinds.index("tool_start") < kinds.index("tool_input") < kinds.index("tool")
    start = next(e for e in ev if e["kind"] == "tool_start")
    tool = next(e for e in ev if e["kind"] == "tool")
    assert start["name"] == tool["name"] == "Bash" and start["id"] == tool["id"]
    partial = "".join(e["partial"] for e in ev if e["kind"] == "tool_input" and e["index"] == start["index"])
    assert json.loads(partial) == tool["input"]
    result = next(e for e in ev if e["kind"] == "tool_result")
    assert result["id"] == tool["id"] and result["error"] is False
    assert ev[-1]["kind"] == "result" and ev[-1]["ok"]


def test_claude_stopped_mid_tool_ends_with_its_reason():
    ev = _kinds(providers.Claude("x"), "claude-interrupted.jsonl")
    assert ev[-1]["kind"] == "result" and ev[-1]["terminal_reason"] == "aborted_tools"
    assert any(e["kind"] == "tool_result" and e["error"] for e in ev)


def test_claude_raw_diagnostics_after_a_stop_are_not_the_result():
    p = providers.Claude("x")
    diag = "[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=null"
    ev = list(p.events([json.dumps({"type": "result", "result": diag, "is_error": False, "session_id": "s1"})]))
    assert ev[0]["kind"] == "result" and ev[0]["text"] == "" and ev[0]["ok"] is True
    ev = list(p.events([json.dumps({"type": "result", "result": "Done.", "is_error": False})]))
    assert ev[0]["text"] == "Done."


def test_the_real_cli_puts_the_stop_diagnostic_in_errors_with_a_null_result():
    # Captured from claude 2.x after Stop: the words are in errors[], "result" is null.
    p = providers.Claude("x")
    line = json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True, "result": None,
        "errors": ["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=null"],
        "terminal_reason": "aborted_streaming", "session_id": "s1"})
    ev = list(p.events([line]))[0]
    assert ev["text"] == "" and ev["ok"] is False and ev["terminal_reason"] == "aborted_streaming"
    # A real failure next to the diagnostic still says what failed.
    line = json.dumps({"type": "result", "is_error": True, "result": None,
                       "errors": ["[ede_diagnostic] x", "No conversation found with session ID: gone"]})
    assert list(p.events([line]))[0]["text"] == "No conversation found with session ID: gone"


def test_claude_tool_results_as_text_blocks():
    p = providers.Claude("x")
    line = json.dumps({"type": "user", "message": {"content": [{
        "type": "tool_result", "tool_use_id": "t9", "is_error": True,
        "content": [{"type": "text", "text": "no such file"}, {"type": "image", "source": {}}]}]}})
    assert list(p.parse(line)) == [{"kind": "tool_result", "id": "t9", "output": "no such file\n[image]", "error": True}]
    # A user message that is plain text (a queued prompt echoed back) is not a tool result.
    assert list(p.parse(json.dumps({"type": "user", "message": {"content": "hello"}}))) == []


def test_codex_items_become_steps_and_results():
    p = providers.Codex("x")
    p.command(providers.Turn("hi"), Path("/tmp"))
    lines = [
        {"type": "item.completed", "item": {"id": "r", "type": "reasoning", "text": "**Installing ffmpeg**\n\nfirst"}},
        {"type": "item.started", "item": {"id": "c", "type": "command_execution",
                                          "command": "/bin/bash -lc 'sudo pacman -S ffmpeg'", "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": "x",
                                            "aggregated_output": "error: target not found", "exit_code": 1}},
        {"type": "item.started", "item": {"id": "m", "type": "mcp_tool_call", "server": "bombadil-os",
                                          "tool": "show_panel", "arguments": {"name": "browser"}}},
        {"type": "item.completed", "item": {"id": "m", "type": "mcp_tool_call", "server": "bombadil-os",
                                            "tool": "show_panel", "result": {"content": [{"type": "text", "text": "ok"}]}}},
        {"type": "item.started", "item": {"id": "f", "type": "file_change", "changes": [{"path": "/etc/hosts", "kind": "update"}]}},
        {"type": "item.completed", "item": {"id": "f", "type": "file_change", "status": "failed"}},
        {"type": "item.updated", "item": {"id": "l", "type": "todo_list", "items": [
            {"text": "Install ffmpeg", "completed": True}, {"text": "Open the browser", "completed": False}]}},
    ]
    ev = [e for m in lines for e in p.parse(json.dumps(m))]
    assert ev[0] == {"kind": "thinking", "text": "Installing ffmpeg"}
    assert ev[1]["name"] == "Bash" and "pacman" in ev[1]["input"]["command"]
    assert ev[2] == {"kind": "tool_result", "id": "c", "output": "error: target not found", "error": True, "exit_code": 1}
    assert ev[3]["name"] == "mcp__bombadil-os__show_panel" and ev[3]["input"] == {"name": "browser"}
    assert ev[4] == {"kind": "tool_result", "id": "m", "output": "ok", "error": False}
    assert ev[5]["kind"] == "file_change" and ev[6]["error"] is True
    assert ev[7]["input"]["todos"][0]["content"] == "Open the browser"


def test_shell_turns_stream_output_and_end_with_the_exit_code():
    p = providers.Shell()
    out = [e for line in ["one\n", "\n", "two\n"] for e in p.parse(line)]
    assert out == [{"kind": "output", "text": "one"}, {"kind": "output", "text": "two"}]
    p.returncode = 2
    end = list(p.finish())
    assert end[0]["error"] and end[0]["exit_code"] == 2
    assert end[1] == {"kind": "result", "ok": False, "text": "one\ntwo\n(exit 2)"}

import json
from pathlib import Path

import pytest

from bombadil import providers


def test_claude_command_and_events(tmp_path):
    p = providers.Claude("/usr/bin/bombadil-os-mcp", model="opus")
    cmd = p.command(providers.Turn("hi", session_id="abc"), tmp_path)
    assert cmd[:2] == ["claude", "-p"] and "hi" not in cmd  # the prompt goes on stdin
    assert "--dangerously-skip-permissions" in cmd and "--resume" in cmd and "--model" in cmd
    cfg = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text())
    assert cfg["mcpServers"]["bombadil-os"]["command"] == "/usr/bin/bombadil-os-mcp"

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
    # Each finished step ends the message its reason belonged to.
    assert [e["kind"] for e in ev].count("message_start") == 3
    ev = [e for e in ev if e["kind"] != "message_start"]
    assert ev[0] == {"kind": "thinking", "text": "Installing ffmpeg"}
    assert ev[1]["name"] == "Bash" and "pacman" in ev[1]["input"]["command"]
    assert ev[2] == {"kind": "tool_result", "id": "c", "output": "error: target not found", "error": True, "exit_code": 1}
    assert ev[3]["name"] == "mcp__bombadil-os__show_panel" and ev[3]["input"] == {"name": "browser"}
    assert ev[4] == {"kind": "tool_result", "id": "m", "output": "ok", "error": False}
    assert ev[5]["kind"] == "file_change" and ev[6]["error"] is True
    # The whole list, done items included: the first one not done is the one under way.
    assert ev[7]["kind"] == "tool" and ev[7]["name"] == "TodoWrite" and ev[7]["id"] == "l"
    assert ev[7]["input"]["todos"] == [
        {"content": "Install ffmpeg", "status": "completed", "activeForm": None},
        {"content": "Open the browser", "status": "in_progress", "activeForm": None}]


def test_shell_turns_stream_output_and_end_with_the_exit_code():
    p = providers.Shell()
    out = [e for line in ["one\n", "\n", "two\n"] for e in p.parse(line)]
    assert out == [{"kind": "output", "text": "one"}, {"kind": "output", "text": "two"}]
    p.returncode = 2
    end = list(p.finish())
    assert end[0]["error"] and end[0]["exit_code"] == 2
    assert end[1] == {"kind": "result", "ok": False, "text": "one\ntwo\n(exit 2)"}


def test_claude_says_where_each_model_message_starts():
    """The sentence before a step is that message's reason: the Narrator needs its boundaries."""
    p = providers.Claude("x")
    ev = _kinds(p, "claude-install-ffmpeg.jsonl")
    kinds = [e["kind"] for e in ev]
    assert kinds.count("message_start") >= 1
    # A message starts before anything it says.
    assert kinds.index("message_start") < kinds.index("text_delta")
    assert list(p.parse(json.dumps({"type": "stream_event", "event": {"type": "message_start"}}))) == [
        {"kind": "message_start"}]


def test_every_turn_says_where_the_kit_lives_and_how_to_import_it(tmp_path, monkeypatch):
    """A fresh session must not go searching the disk for Theme.qml."""
    share = tmp_path / "share"
    (share / "qml" / "Bombadil").mkdir(parents=True)
    (share / "skills" / "bombadil-apps").mkdir(parents=True)
    monkeypatch.setattr(providers, "__file__", str(tmp_path / "nowhere" / "src" / "bombadil" / "providers.py"))
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path))
    prompt = providers.system_prompt()
    assert "import Bombadil" in prompt and f"{share}/qml/Bombadil/" in prompt
    assert f"{share}/skills/bombadil-apps/SKILL.md" in prompt and "never search the disk" in prompt
    claude = providers.Claude("/usr/bin/bombadil-os-mcp").command(providers.Turn("hi", session_id="s"), tmp_path)
    assert claude[claude.index("--append-system-prompt") + 1] == prompt        # resumed turns get it too
    codex = providers.Codex("/usr/bin/bombadil-os-mcp").command(providers.Turn("hi", session_id="s"), tmp_path)
    assert f"developer_instructions={json.dumps(prompt)}" in codex


def test_kit_paths_on_this_checkout_exist():
    kit, skill = providers.kit_paths()
    assert (kit / "Theme.qml").is_file() and (skill / "SKILL.md").is_file()


def test_the_system_prompt_asks_for_reasons_plans_and_pictures():
    prompt = providers.system_prompt()
    assert "say in one short plain sentence why" in prompt
    assert "write the plan first with your task tool" in prompt
    assert "show it as a picture (system_map for this machine, show_card otherwise), then say one line" in prompt


def _todo_list(kind, items, item_id="l"):
    return json.dumps({"type": kind, "item": {"id": item_id, "type": "todo_list", "items": items}})


def test_codex_sends_the_whole_plan_on_every_change():
    p = providers.Codex("x")
    plan = [{"text": "Install ffmpeg", "completed": False}, {"text": "Open the browser", "completed": False},
            {"text": "Check the sound", "completed": False}]
    started = list(p.parse(_todo_list("item.started", plan)))
    assert started == [{"kind": "tool", "name": "TodoWrite", "id": "l", "input": {"todos": [
        {"content": "Install ffmpeg", "status": "in_progress", "activeForm": None},
        {"content": "Open the browser", "status": "pending", "activeForm": None},
        {"content": "Check the sound", "status": "pending", "activeForm": None}]}}]
    plan[0]["completed"] = plan[1]["completed"] = True
    updated = list(p.parse(_todo_list("item.updated", plan)))
    assert [t["status"] for t in updated[0]["input"]["todos"]] == ["completed", "completed", "in_progress"]
    # Every item done: the list still goes out, so the last step is ticked.
    plan[2]["completed"] = True
    done = list(p.parse(_todo_list("item.updated", plan)))
    assert [t["status"] for t in done[0]["input"]["todos"]] == ["completed"] * 3
    # The list Codex closes the turn with is the final one.
    closed = list(p.parse(_todo_list("item.completed", plan)))
    assert [t["status"] for t in closed[0]["input"]["todos"]] == ["completed"] * 3


@pytest.mark.parametrize("items", [[], None, "nope", [3, None]])
def test_a_codex_plan_without_steps_says_nothing(items):
    for kind in ("item.started", "item.updated", "item.completed"):
        assert list(providers.Codex("x").parse(_todo_list(kind, items))) == []


def _claude_line(**m):
    return json.dumps(m)


def test_claude_forwards_the_parent_of_a_subagents_tools_and_only_then():
    p = providers.Claude("x")
    call = {"type": "tool_use", "id": "t1", "name": "TaskCreate", "input": {"subject": "Read"}}
    sub = list(p.parse(_claude_line(type="assistant", message={"content": [call]}, parent_tool_use_id="toolu_A")))
    assert sub == [{"kind": "tool", "name": "TaskCreate", "input": {"subject": "Read"}, "id": "t1",
                    "parent": "toolu_A"}]
    result = _claude_line(type="user", parent_tool_use_id="toolu_A", message={"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "Task #1 created successfully: Read"}]})
    [got] = p.parse(result)
    assert got["kind"] == "tool_result" and got["parent"] == "toolu_A"
    start = _claude_line(type="stream_event", parent_tool_use_id="toolu_A", event={
        "type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "Bash"}})
    [got] = p.parse(start)
    assert got["kind"] == "tool_start" and got["parent"] == "toolu_A"
    # The turn's own tools carry no such key, null or absent.
    for extra in ({"parent_tool_use_id": None}, {}):
        main = list(p.parse(_claude_line(type="assistant", message={"content": [call]}, **extra)))
        assert "parent" not in main[0]
    # Words are not tool calls: nothing reads a subagent's text as plan.
    text = list(p.parse(_claude_line(type="assistant", parent_tool_use_id="toolu_A",
                                     message={"content": [{"type": "text", "text": "hi"}]})))
    assert text == [{"kind": "text", "text": "hi"}]


def test_the_plan_tools_are_switched_on_for_the_machines_own_turns(tmp_path):
    # Claude Code offers TodoWrite/TaskCreate on some models only when asked, and the way to ask is
    # the CLI's environment (agentd merges this over its own).
    assert providers.Claude("x").env() == {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}
    for other in (providers.Codex("x"), providers.Shell(), providers.Fake("x")):
        assert other.env() == {}
    # Codex registers update_plan only when its config says so: on a fresh and on a resumed turn alike.
    codex = providers.Codex("x")
    for turn in (providers.Turn("hi"), providers.Turn("again", session_id="t1")):
        cmd = codex.command(turn, tmp_path)
        assert cmd[cmd.index("tools.update_plan.enabled=true") - 1] == "-c"

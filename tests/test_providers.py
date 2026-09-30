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
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet-4-5",
                    "mcp_servers": [{"name": "bombadil-os", "status": "connected"}]}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Opening"}, {"type": "tool_use", "name": "show_panel", "input": {"name": "browser"}}]}}),
        "garbage",
        json.dumps({"type": "result", "result": "done", "is_error": False, "session_id": "s1",
                    "total_cost_usd": 0.02, "usage": {"input_tokens": 5, "output_tokens": 7}}),
    ]
    ev = list(p.events(lines))
    # What the CLI says about the turn comes as meta events, before the result they belong with.
    assert [e["kind"] for e in ev] == ["session", "meta", "text", "tool", "meta", "result"]
    assert ev[1] == {"kind": "meta", "model": "claude-sonnet-4-5"}
    assert ev[4] == {"kind": "meta", "cost": 0.02, "usage": {"input": 5, "output": 7, "cache_read": 0, "cache_write": 0}}
    assert ev[3]["input"] == {"name": "browser"}


def test_claude_error_results_keep_their_reason_and_mcp_failures_show():
    p = providers.Claude("x")
    ev = list(p.events([
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1",
                    "mcp_servers": [{"name": "bombadil-os", "status": "failed"}]}),
        json.dumps({"type": "result", "is_error": True, "subtype": "error_during_execution",
                    "errors": ["No conversation found with session ID: s0"], "num_turns": 0}),
    ]))
    assert [e["kind"] for e in ev] == ["session", "error", "result"]   # says nothing of model or cost: no meta
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
    assert [e["kind"] for e in ev] == ["session", "tool", "text", "text", "result"]   # no usage, no model: no meta
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


def _metas(events):
    return [e for e in events if e["kind"] == "meta"]


def test_claude_keeps_the_model_the_cost_the_tokens_and_a_rate_limit_notice():
    ev = _kinds(providers.Claude("x"), "claude-meta.jsonl")
    assert [e["kind"] for e in ev] == ["session", "meta", "meta", "text", "meta", "meta", "result"]
    model, limit, drift, result_meta = _metas(ev)
    assert model == {"kind": "meta", "model": "claude-sonnet-4-5"}
    assert limit == {"kind": "meta", "rate_limit": {
        "status": "allowed", "resetsAt": 1790684400, "rateLimitType": "five_hour", "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled", "isUsingOverage": False}}
    assert drift == {"kind": "meta", "drift": "prompt_suggestion"}
    assert result_meta == {"kind": "meta", "cost": 0.0412, "usage": {
        "input": 310, "output": 188, "cache_read": 18240, "cache_write": 2100}}
    assert ev[-1]["kind"] == "result" and ev[-1]["text"] == "You have 212 GB free."


def test_the_fixtures_the_adapter_was_written_against_show_no_drift():
    """Every line a real Claude turn prints is of a type the parser knows; if this fails the parser is wrong,
    or the fixture is newer than it."""
    for name in ("claude-install-ffmpeg.jsonl", "claude-interrupted.jsonl"):
        ev = _kinds(providers.Claude("x"), name)
        assert not [m for m in _metas(ev) if "drift" in m], name
    ev = _kinds(providers.Claude("x"), "claude-install-ffmpeg.jsonl")
    assert _metas(ev) == [
        {"kind": "meta", "model": "claude-sonnet-4-5"},
        {"kind": "meta", "cost": 0.0015599999999999998, "usage": {"input": 100, "output": 84, "cache_read": 0,
                                                                   "cache_write": 0}}]
    assert _metas(_kinds(providers.Claude("x"), "claude-interrupted.jsonl"))[-1]["cost"] == 0.0007799999999999999


def test_a_line_of_an_unknown_type_is_drift_and_does_nothing_else():
    p = providers.Claude("x")
    known = json.dumps({"type": "user", "message": {"content": "hello"}})
    assert list(p.parse(known)) == []
    assert list(p.parse(json.dumps({"type": "hologram", "x": 1}))) == [{"kind": "meta", "drift": "hologram"}]
    # Not JSON, JSON that is not an object, and an object with no type are not drift: there is nothing to name.
    for line in ("garbage", "[1, 2]", "3", '"type"', json.dumps({"no": "type"}), ""):
        assert list(p.parse(line)) == [], line
    # A type that is not even a word still counts, and cannot break the parser.
    for odd, name in ((None, "None"), (7, "7"), (["a"], "['a']"), ({"a": 1}, "{'a': 1}"), ("", "(empty)")):
        assert list(p.parse(json.dumps({"type": odd}))) == [{"kind": "meta", "drift": name}]
    assert next(iter(p.parse(json.dumps({"type": "x" * 500}))))["drift"] == "x" * 60


def test_every_type_the_claude_parser_handles_is_one_it_knows():
    handled = {"system", "stream_event", "assistant", "user", "result", "rate_limit_event"}
    assert providers.Claude.known_types == handled
    for t in handled:
        assert not [e for e in providers.Claude("x").parse(json.dumps({"type": t})) if e.get("drift")]


def test_claude_meta_ignores_what_it_cannot_use():
    p = providers.Claude("x")
    assert list(p.parse(json.dumps({"type": "system", "subtype": "init", "model": ""}))) == [
        {"kind": "session", "session_id": None},
        {"kind": "error", "text": "the OS tools did not start (bombadil-os: missing)"}]
    assert list(p.parse(json.dumps({"type": "rate_limit_event", "rate_limit_info": "soon"}))) == []
    odd = json.dumps({"type": "result", "result": "ok", "total_cost_usd": True,
                      "usage": {"input_tokens": "many", "output_tokens": -1}})
    assert [e["kind"] for e in p.parse(odd)] == ["result"]
    part = json.dumps({"type": "result", "result": "ok", "usage": {"output_tokens": 9}})
    assert next(iter(p.parse(part))) == {"kind": "meta", "usage": {"input": 0, "output": 9, "cache_read": 0,
                                                                "cache_write": 0}}
    assert [e["kind"] for e in p.parse(json.dumps({"type": "result", "result": "ok", "usage": {}}))] == ["result"]


def test_codex_keeps_its_token_counts_and_the_model_only_when_one_was_asked_for():
    lines = [json.dumps({"type": "thread.started", "thread_id": "t1"}), json.dumps({"type": "turn.started"}),
             json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Hello"}}),
             json.dumps({"type": "turn.completed", "usage": {"input_tokens": 24763, "cached_input_tokens": 24448,
                                                              "output_tokens": 122}})]
    plain = providers.Codex("x")
    plain.command(providers.Turn("hi"), Path("/tmp"))
    ev = list(plain.events(lines))
    # Its input_tokens include the cached ones; a row's input does not.
    assert _metas(ev) == [{"kind": "meta", "usage": {"input": 315, "output": 122, "cache_read": 24448,
                                                     "cache_write": 0}}]
    assert [e["kind"] for e in ev] == ["session", "text", "meta", "result"]   # turn.started is known, says nothing
    asked = providers.Codex("x", model="some-model")
    asked.command(providers.Turn("hi"), Path("/tmp"))
    assert _metas(list(asked.events(lines)))[0]["model"] == "some-model"
    # A model but no usage: the model alone.
    assert list(asked.parse(json.dumps({"type": "turn.completed"}))) == [
        {"kind": "meta", "model": "some-model"}, {"kind": "result", "ok": True, "text": "Hello"}]
    # Counts that do not add up are not made to.
    sums = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 9}})
    assert next(iter(plain.parse(sums)))["usage"] == {"input": 0, "output": 0, "cache_read": 9, "cache_write": 0}


def test_codex_counts_an_event_type_it_does_not_know_as_drift():
    p = providers.Codex("x")
    assert list(p.parse(json.dumps({"type": "turn.interrupted"}))) == [{"kind": "meta", "drift": "turn.interrupted"}]
    for t in p.known_types:
        assert not [e for e in p.parse(json.dumps({"type": t})) if e.get("drift")], t
    assert list(p.parse(json.dumps({"item": {"type": "agent_message"}}))) == []   # no type, nothing to name


def test_the_shell_and_the_fake_provider_say_nothing_about_themselves():
    sh = providers.Shell()
    assert not _metas(list(sh.parse("line\n")) + list(sh.finish()))
    fake = providers.Fake("x")
    fake.command(providers.Turn("hi"), Path("/tmp"))
    assert not _metas(list(fake.events(["hi"])))

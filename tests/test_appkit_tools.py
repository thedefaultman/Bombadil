"""The app tools os-mcp gives the agent (appkit/tools.py), with the check and Hyprland faked."""

import base64
import builtins
import json
import sys
import textwrap
from pathlib import Path

import pytest

from bombadil import hypr, mcp_server, snapshots
from bombadil.appkit import placement, tools

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
QML = "import QtQuick\nimport Bombadil\nAppWindow { title: \"Todo\" }\n"


class FakeHypr(hypr.Hyprland):
    available = True

    def request(self, command, timeout=10):
        return "[]" if command.startswith("j/") else "ok"


def make():
    return mcp_server.OsTools(FakeHypr(), snapshots.Snapshots(configs_dir=Path("/nonexistent")))


def call(server, tool, **args):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return r["result"]


@pytest.fixture
def fake_check(home, tmp_path, monkeypatch):
    """`bombadil-app check` replaced by a script that answers like it (no Qt anywhere)."""
    script = tmp_path / "fake-check"
    script.write_text(textwrap.dedent(f"""\
        import json, sys
        from pathlib import Path
        args = sys.argv[1:]
        target, png = Path(args[1]), args[args.index("--screenshot") + 1]
        open(png, "wb").write({PNG!r})
        ok = "broken" not in (target / "main.qml").read_text()
        print(json.dumps({{"app": target.name, "ok": ok, "loaded": True, "target": str(target),
                          "errors": [] if ok else ["main.qml:3:1: broken is not defined"],
                          "warnings": [], "console": ["hi"], "screenshot": png, "size": [560, 680]}}))
        """))
    monkeypatch.setattr(tools.apps, "runner", lambda: [sys.executable, str(script)])
    state = {"running": {}, "shown": set(), "started": [], "shown_calls": []}
    monkeypatch.setattr(placement, "running", lambda: state["running"])
    monkeypatch.setattr(placement, "shown", lambda h=None: state["shown"])
    monkeypatch.setattr(placement.apps, "run", lambda name: state["started"].append(name))
    return state


def no_qt(monkeypatch):
    """Fail loudly if anything imports PySide6, even when another test already loaded it."""
    real = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] == "PySide6":
            raise AssertionError(f"os-mcp imported {name}")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def test_create_app_checks_opens_and_returns_the_screenshot(home, fake_check, monkeypatch):
    s = make()
    no_qt(monkeypatch)
    r = call(s, "create_app", title="Todo List", qml=QML, files={"Row.qml": "import QtQuick\nItem {}\n"},
             icon="list", description="things to do")
    assert not r.get("isError"), r
    text, image = r["content"]
    assert text["type"] == "text" and "check passed" in text["text"]
    summary = json.loads(text["text"].split("\n", 1)[1])
    assert summary["app"] == "todo-list" and summary["ok"] and summary["console"] == ["hi"]
    assert summary["reloaded"] is False and summary["window"].startswith("started todo-list")
    assert image == {"type": "image", "data": base64.b64encode(PNG).decode(), "mimeType": "image/png"}
    assert (home / "Apps/todo-list/Row.qml").exists() and fake_check["started"] == ["todo-list"]


def test_create_app_on_a_running_app_reloads_and_shows_it(home, fake_check, monkeypatch):
    s = make()
    call(s, "create_app", title="Todo", qml=QML, open=False)
    assert fake_check["started"] == []
    fake_check["running"] = {"todo": [4242]}
    shown = []
    monkeypatch.setattr(placement, "show", lambda name, h=None, start=True: shown.append(name) or f"{name} shown")
    r = call(s, "create_app", title="Todo", qml=QML.replace("Todo", "Todo 2"))
    summary = json.loads(r["content"][0]["text"].split("\n", 1)[1])
    assert summary["reloaded"] is True and summary["window"] == "todo shown" and shown == ["todo"]
    assert fake_check["started"] == []


def test_a_failed_check_still_writes_the_files_and_says_what_to_fix(home, fake_check):
    r = call(make(), "create_app", title="Todo", qml=QML + "// broken\n", open=False)
    text = r["content"][0]["text"]
    assert "check FAILED" in text and "main.qml:3:1: broken is not defined" in text
    assert "broken" in (home / "Apps/todo/main.qml").read_text()


def test_bad_files_are_refused_before_anything_is_written(home, fake_check):
    r = call(make(), "create_app", title="Todo", qml=QML, files={"data/state.json": "{}"})
    assert r["isError"] and "data/" in r["content"][0]["text"]
    assert not (home / "Apps/todo").exists()


def test_check_app_and_a_crashing_check(home, fake_check, monkeypatch):
    s = make()
    call(s, "create_app", title="Todo", qml=QML, open=False)
    r = call(s, "check_app", name="todo")
    assert [b["type"] for b in r["content"]] == ["text", "image"]
    assert call(s, "check_app", name="nothing")["isError"]
    monkeypatch.setattr(tools.apps, "runner", lambda: [sys.executable, "-c", "import sys; sys.exit('boom')"])
    r = call(s, "check_app", name="todo")
    assert len(r["content"]) == 1 and "check crashed (exit 1)" in r["content"][0]["text"] and "boom" in r["content"][0]["text"]


def test_the_check_gets_the_apps_directory_not_a_name_relative_to_the_cwd(home, fake_check, tmp_path, monkeypatch):
    call(make(), "create_app", title="Todo", qml=QML, open=False)
    decoy = tmp_path / "cwd"
    (decoy / "todo").mkdir(parents=True)
    (decoy / "todo" / "main.qml").write_text(QML + "// broken\n")
    monkeypatch.chdir(decoy)       # a `todo/main.qml` where the MCP server happens to run
    result = tools.run_check("todo")
    assert result["target"] == str(home / "Apps" / "todo") and result["ok"]


def test_the_runtime_doc_states_checks_default_wait():
    from bombadil.appkit import check

    doc = (tools.skill_dir() / "references" / "runtime.md").read_text()
    assert f"`--wait MS`, default {check.WAIT_MS}" in doc


def test_list_apps_says_what_is_running_and_shown(home, fake_check):
    s = make()
    for title in ("Alpha", "Beta", "Gamma"):
        call(s, "create_app", title=title, qml=QML, open=False)
    fake_check["running"] = {"alpha": [1], "beta": [2]}
    fake_check["shown"] = {"beta"}
    listed = json.loads(call(s, "list_apps")["content"][0]["text"])
    assert [(a["name"], a["running"], a["shown"]) for a in listed] == [
        ("alpha", True, False), ("beta", True, True), ("gamma", False, False)]
    assert listed[0]["path"].endswith("Apps/alpha") and listed[0]["title"] == "Alpha"


def test_window_tools_go_through_placement(home, fake_check, monkeypatch):
    s = make()
    call(s, "create_app", title="Todo", qml=QML, open=False)
    fake_check["running"] = {"todo": [7]}
    calls = []
    for verb in ("show", "hide"):
        monkeypatch.setattr(placement, verb, lambda name, h=None, verb=verb, **k: calls.append(verb) or f"{name} {verb}")
    monkeypatch.setattr(placement, "close", lambda name: calls.append("close") or f"{name} closed")
    assert call(s, "show_app", name="todo")["content"][0]["text"] == "todo show"
    assert call(s, "hide_app", name="todo")["content"][0]["text"] == "todo hide"
    assert call(s, "close_app", name="todo")["content"][0]["text"] == "todo closed"
    assert call(s, "open_app", name="todo")["content"][0]["text"] == "todo show"
    assert calls == ["show", "hide", "close", "show"]
    assert call(s, "hide_app", name="../../etc")["isError"]


def test_open_app_reports_how_the_load_went(home, fake_check, monkeypatch):
    s = make()
    call(s, "create_app", title="Todo", qml=QML, open=False)
    monkeypatch.setattr(tools, "OPEN_WAIT", 0.3)
    r = json.loads(call(s, "open_app", name="todo")["content"][0]["text"])
    assert r["app"] == "todo" and r["loaded"] == "not yet" and fake_check["started"] == ["todo"]

    def start(name):
        (home / "state/apps").mkdir(parents=True, exist_ok=True)
        (home / "state/apps/todo.status.json").write_text(json.dumps({"ok": False, "errors": ["main.qml:1: x"]}))
    monkeypatch.setattr(placement.apps, "run", start)
    r = json.loads(call(s, "open_app", name="todo")["content"][0]["text"])
    assert r["ok"] is False and r["errors"] == ["main.qml:1: x"]


def test_app_status(home, fake_check):
    s = make()
    call(s, "create_app", title="Todo", qml=QML, open=False)
    st = json.loads(call(s, "app_status", name="todo")["content"][0]["text"])
    assert st["app"] == "todo" and st["running"] is False and st["log"] == []


def test_app_guide_topics(home, tmp_path, monkeypatch):
    s = make()
    text = call(s, "app_guide")["content"][0]["text"]
    assert "bombadil-apps" in text[:200] and "app_guide(topic)" in text
    assert "hot reload" in call(s, "app_guide", topic="runtime")["content"][0]["text"].lower()

    skill = tmp_path / "skill"
    (skill / "references").mkdir(parents=True)
    (skill / "examples" / "memory").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Guide\n")
    (skill / "references" / "patterns.md").write_text("# Patterns\n")
    (skill / "examples" / "memory" / "main.qml").write_text("AppWindow {}\n")
    monkeypatch.setattr(tools, "skill_dir", lambda: skill)
    assert tools.guide_topics() == (["patterns"], ["memory"])
    assert "'patterns', or an example app's full source: 'memory'" in tools.guide()
    example = tools.guide("memory")
    assert "## main.qml\n```qml\nAppWindow {}\n```" in example
    r = call(make(), "app_guide", topic="nope")
    assert r["isError"] and "'memory'" in r["content"][0]["text"]
    # The tool description lists the topics too, so the agent knows them before calling.
    desc = {t["name"]: t["description"] for t, _ in make().tools.values()}["app_guide"]
    assert "'patterns'" in desc and "'memory'" in desc
    monkeypatch.setattr(tools, "skill_dir", lambda: None)
    assert "not installed" in tools.guide()


def test_app_tools_are_listed_together_and_point_at_the_guide():
    names = [t["name"] for t, _ in make().tools.values()]
    app_tools = ["app_guide", "create_app", "check_app", "open_app", "show_app", "hide_app", "close_app",
                 "app_status", "list_apps", "app_template"]
    assert names[-len(app_tools):] == app_tools
    create = next(t for t, _ in make().tools.values() if t["name"] == "create_app")
    assert "app_guide" in create["description"] and create["inputSchema"]["required"] == ["title", "qml"]
    assert set(create["inputSchema"]["properties"]) == {"title", "qml", "files", "python", "description", "icon", "open"}

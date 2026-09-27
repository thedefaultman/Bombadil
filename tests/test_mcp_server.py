import io
import json

from bombadil import hypr, mcp_server, snapshots
from bombadil.appkit import tools as app_tools


class FakeHypr(hypr.Hyprland):
    def __init__(self):
        self.calls = []

    @property
    def available(self):
        return True

    def panel(self, name, show=True):
        if name not in hypr.PANELS:
            raise ValueError(name)
        self.calls.append((name, show))
        return f"panel {name} {'shown' if show else 'hidden'}"


class FakeSnaps(snapshots.Snapshots):
    def __init__(self):
        self.n = 0
        self.rolled = None

    @property
    def available(self):
        return True

    def create(self, description):
        self.n += 1
        return snapshots.Snapshot(self.n, description)

    def list(self, limit=20):
        return [snapshots.Snapshot(i, f"turn:{i}: x") for i in range(1, self.n + 1)]

    def rollback(self, number):
        self.rolled = number
        return True


def rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def call(server, tool, **args):
    r = rpc(server, "tools/call", {"name": tool, "arguments": args})
    return r["result"]


def make():
    return mcp_server.OsTools(FakeHypr(), FakeSnaps())


def test_initialize_and_list():
    s = make()
    init = rpc(s, "initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["serverInfo"]["name"] == "bombadil-os"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in rpc(s, "tools/list")["result"]["tools"]}
    assert {"show_panel", "hide_panel", "create_app", "rollback", "screenshot"} <= names


def test_panels():
    s = make()
    assert call(s, "show_panel", name="browser")["content"][0]["text"] == "panel browser shown"
    assert call(s, "hide_panel", name="browser")["content"][0]["text"] == "panel browser hidden"
    assert s.hypr.calls == [("browser", True), ("browser", False)]


def test_tool_errors_are_reported_not_fatal():
    s = make()
    r = call(s, "show_panel", name="nope")
    assert r["isError"] and "ValueError" in r["content"][0]["text"]
    assert rpc(s, "tools/call", {"name": "missing"})["error"]["code"] == -32602


def test_create_app_writes_files(home, monkeypatch):
    monkeypatch.setattr(mcp_server.apps, "run", lambda name: None)
    monkeypatch.setattr(app_tools, "run_check", lambda name: {"ok": True})
    s = make()
    r = call(s, "create_app", title="Todo", qml="import QtQuick\nItem{}\n")
    assert "todo" in r["content"][0]["text"] and (home / "Apps/todo/main.qml").exists()
    listed = json.loads(call(s, "list_apps")["content"][0]["text"])
    assert listed[0]["name"] == "todo"


def test_content_blocks_pass_through():
    blocks = app_tools.Blocks([{"type": "text", "text": "hi"}, {"type": "image", "data": "", "mimeType": "image/png"}])
    assert mcp_server._content(blocks) == list(blocks)
    assert json.loads(mcp_server._content([{"type": "x"}])[0]["text"]) == [{"type": "x"}]


def test_snapshot_and_undo():
    s = make()
    call(s, "snapshot", description="turn:1: hi")
    call(s, "snapshot", description="turn:2: more")
    r = call(s, "rollback")
    assert "snapshot 2" in r["content"][0]["text"] and s.snaps.rolled == 2


def test_serve_over_stdio():
    s = make()
    out = io.StringIO()
    s.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n\n"), out)
    assert json.loads(out.getvalue())["id"] == 7


def test_template_available():
    tpl = json.loads(call(make(), "app_template")["content"][0]["text"])
    assert "import Bombadil" in tpl["main.qml"] and "class Backend" in tpl["app.py"]

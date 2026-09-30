import io
import json
import socket
import threading
import time

from bombadil import desk, hypr, mcp_server, paths, providers, snapshots


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
    s = make()
    r = call(s, "create_app", title="Todo", qml="import QtQuick\nItem{}\n")
    assert "todo" in r["content"][0]["text"] and (home / "Apps/todo/main.qml").exists()
    listed = json.loads(call(s, "list_apps")["content"][0]["text"])
    assert listed[0]["name"] == "todo"


def test_snapshot_and_undo(monkeypatch):
    monkeypatch.delenv("BOMBADIL_TURN_SNAPSHOT", raising=False)
    s = make()
    call(s, "snapshot", description="turn:1: hi")
    call(s, "snapshot", description="turn:2: more")
    r = call(s, "rollback")
    assert "snapshot 2" in r["content"][0]["text"] and s.snaps.rolled == 2


def test_undo_inside_a_turn_skips_that_turns_snapshot(monkeypatch):
    # "install htop" took snapshot 1; "undo that" is turn 2 and took snapshot 2 (htop installed).
    s = make()
    call(s, "snapshot", description="turn:1: install htop")
    call(s, "snapshot", description="turn:2: undo that")
    monkeypatch.setenv("BOMBADIL_TURN_SNAPSHOT", "2")
    r = call(s, "rollback")
    assert "snapshot 1" in r["content"][0]["text"] and s.snaps.rolled == 1


def test_serve_over_stdio():
    s = make()
    out = io.StringIO()
    s.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n\n"), out)
    assert json.loads(out.getvalue())["id"] == 7


def test_template_available():
    tpl = json.loads(call(make(), "app_template")["content"][0]["text"])
    assert "import Bombadil" in tpl["main.qml"] and "class Backend" in tpl["app.py"]


def _agentd(answer):
    """A socket where agentd would be. It greets the way agentd does, notes the one line it is
    sent, and sends back what `answer(msg)` returns (messages, or raw bytes). It hangs up when
    the client does."""
    path = paths.socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    got = []

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.sendall(b'{"type": "status", "busy": true}\n{"type": "entries", "entries": []}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            msg = json.loads(buf.split(b"\n")[0])
            got.append(msg)
            for out in answer(msg):
                conn.sendall(out if isinstance(out, bytes) else (json.dumps(out) + "\n").encode())
                time.sleep(0.05)
            while conn.recv(65536):
                pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, got


def _said(r):
    return r["content"][0]["text"]


def test_the_desk_tool_is_listed_with_what_it_can_do_and_no_more():
    tool = next(t for t in rpc(make(), "tools/list")["result"]["tools"] if t["name"] == "desk")
    props = tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["required"] == ["op"]
    assert props["op"]["enum"] == ["show", "hide", "move", "fold", "unfold", "state"]   # no make, no remove
    assert props["widget"]["enum"] == list(desk.WIDGETS) and props["rail"]["enum"] == ["left", "right"]
    assert props["rank"]["type"] == "integer"
    # It says plainly that it is refused unless the person asked for the desk.
    assert "ONLY" in tool["description"] and "asked for the desk" in tool["description"]
    assert "never rearrange the desk on your own" in tool["description"]


def test_the_desk_tool_asks_agentd_for_its_turn_and_returns_its_words(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "7")

    def answer(msg):
        return [{"type": "desk", "folded": False},                                          # a broadcast
                {"type": "event", "kind": "text", "turn": 7, "text": "hello"},
                {"type": "desk-result", "id": "someone-elses", "ok": False, "text": "not mine"},
                b"this is not json\n",
                {"type": "desk-result", "id": msg["id"], "ok": True, "text": "Put Machine away."}]
    srv, got = _agentd(answer)
    r = call(make(), "desk", op="hide", widget="machine")
    assert _said(r) == "Put Machine away." and "isError" not in r
    assert len(got) == 1 and len(got[0].pop("id")) == 32
    assert got == [{"type": "desk-tool", "turn": 7, "op": "hide", "widget": "machine"}]
    srv.close()


def test_only_what_was_given_is_sent(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "3")
    srv, got = _agentd(lambda m: [{"type": "desk-result", "id": m["id"], "ok": True, "text": "done"}])
    call(make(), "desk", op="move", widget="now", rail="right", rank=0)
    srv.close()
    srv, more = _agentd(lambda m: [{"type": "desk-result", "id": m["id"], "ok": True, "text": "done"}])
    call(make(), "desk", op="state")
    srv.close()
    assert [{k: v for k, v in m.items() if k != "id"} for m in got + more] == [
        {"type": "desk-tool", "turn": 3, "op": "move", "widget": "now", "rail": "right", "rank": 0},
        {"type": "desk-tool", "turn": 3, "op": "state"}]


def test_a_refusal_reaches_the_agent_as_an_error_in_agentds_own_words(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "2")
    no = "The person did not ask for the desk in this turn, so it stays as it is."
    srv, _ = _agentd(lambda m: [{"type": "desk-result", "id": m["id"], "ok": False, "text": no}])
    r = call(make(), "desk", op="hide", widget="machine")
    assert r["isError"] is True and _said(r) == no      # no "ToolError:" in front of it
    srv.close()


def test_the_answer_may_arrive_in_pieces(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "2")

    def answer(m):
        whole = (json.dumps({"type": "desk-result", "id": m["id"], "ok": True, "text": "Folded the desk."})
                 + "\n").encode()
        return [whole[:20], whole[20:]]
    srv, _ = _agentd(answer)
    assert _said(call(make(), "desk", op="fold")) == "Folded the desk."
    srv.close()


def test_without_a_turn_the_tool_says_so_and_asks_nobody(home, monkeypatch):
    monkeypatch.delenv("BOMBADIL_TURN", raising=False)
    srv, got = _agentd(lambda m: [])
    r = call(make(), "desk", op="state")
    assert r["isError"] is True and "inside a turn" in _said(r)
    time.sleep(0.1)
    assert got == []
    srv.close()


def test_agentd_not_running_is_a_clear_error(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "1")
    r = call(make(), "desk", op="state")
    assert r["isError"] is True and "agentd is not answering" in _said(r) and "unchanged" in _said(r)
    assert not _said(r).startswith(("OSError", "FileNotFoundError"))


def test_an_agentd_that_never_answers_is_a_clear_error_after_the_timeout(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "1")
    monkeypatch.setattr(mcp_server, "DESK_TIMEOUT", 0.3)
    srv, got = _agentd(lambda m: [{"type": "desk-result", "id": "not-this-one", "ok": True, "text": "no"}])
    t0 = time.monotonic()
    r = call(make(), "desk", op="hide", widget="machine")
    assert time.monotonic() - t0 < 2
    assert r["isError"] is True and "did not answer within 0.3 seconds" in _said(r)
    assert got and got[0]["op"] == "hide"
    srv.close()


def test_an_agentd_that_hangs_up_is_a_clear_error(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TURN", "1")
    path = paths.socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    r = call(make(), "desk", op="state")
    assert r["isError"] is True and "hung up" in _said(r)
    srv.close()


def test_codex_hands_the_turn_to_the_tool_too():
    # Codex starts MCP servers with an allow-list of the environment.
    assert "BOMBADIL_TURN" in providers.MCP_ENV and "BOMBADIL_SOCKET" in providers.MCP_ENV

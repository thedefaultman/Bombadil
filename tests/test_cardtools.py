"""show_card and system_map: what the agent sends, what comes back, and the card reaching agentd."""

import asyncio
import json
import threading

import pytest

from bombadil import cards, cardtools, mcp_server, sysmap


class FakeAgentd:
    """A socket that greets like agentd and answers a card with card_ack."""

    def __init__(self, path, shown=True, reply=None):
        import socket
        self.path, self.shown, self.reply = path, shown, reply
        self.got: list[dict] = []
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(path))
        self.sock.listen(1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.sock.accept()
        with conn, conn.makefile("rwb") as f:
            f.write(b'{"type": "status", "busy": false}\n{"type": "entries", "entries": []}\n')
            f.flush()
            msg = json.loads(f.readline())
            self.got.append(msg)
            f.write((json.dumps(self.reply or {"type": "card_ack", "shown": self.shown}) + "\n").encode())
            f.flush()

    def close(self):
        self.sock.close()


@pytest.fixture
def tools(home):
    return mcp_server.OsTools()


def call(tools, name, args):
    return tools.tools[name][1](args)


DIAGRAM = {"shape": "chain", "title": "How a VPN works",
           "nodes": [{"label": "Laptop"}, {"label": "Tunnel", "state": "new"}, {"label": "Internet"}],
           "say": "Everything goes through the tunnel first."}


def test_the_picture_tools_are_listed_with_their_inputs(tools):
    specs = {t["name"]: t for t, _ in tools.tools.values()}
    show, smap = specs["show_card"], specs["system_map"]
    assert show["inputSchema"]["required"] == ["shape", "title", "nodes"]
    assert show["inputSchema"]["properties"]["shape"]["enum"] == ["chain", "layers", "compare", "timeline"]
    assert show["inputSchema"]["properties"]["nodes"]["items"]["properties"]["state"]["enum"] == list(cards.STATES)
    assert smap["inputSchema"]["properties"]["kind"]["enum"] == list(sysmap.KINDS)
    assert "never draw its state from memory" in show["description"]
    # Both stay well inside what a model reads every turn.
    assert len(json.dumps([show, smap])) < 6500


def test_show_card_sends_the_checked_card_and_returns_the_picture_in_words(tools, home):
    fake = FakeAgentd(home / "run" / "agentd.sock") if (home / "run").mkdir() is None else None
    out = call(tools, "show_card", DIAGRAM)
    assert out == ("How a VPN works: Laptop → Tunnel [new] → Internet\nEverything goes through the tunnel first."
                   "\n(shown above the bar)")
    sent = fake.got[0]
    assert sent["type"] == "card" and sent["card"]["type"] == "diagram"
    assert sent["card"]["links"] == [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}]
    fake.close()


def test_show_card_returns_every_fixable_error_at_once(tools):
    with pytest.raises(ValueError) as e:
        call(tools, "show_card", {"shape": "spiral", "title": "", "nodes": [{"label": "x" * 40}]})
    text = str(e.value)
    assert text.startswith("The picture was not drawn:")
    assert "shape must be one of" in text and "title is required" in text and "label is 40 characters" in text
    with pytest.raises(ValueError, match="only diagram is"):
        call(tools, "show_card", {**DIAGRAM, "kind": "list"})


def test_the_error_reaches_the_agent_as_text_not_a_dead_server(tools):
    reply = tools.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "show_card", "arguments": {"shape": "chain", "title": "t", "nodes": []}}})
    assert reply["result"]["isError"] is True
    assert "nodes must be a list with at least one box" in reply["result"]["content"][0]["text"]


def test_with_no_agentd_the_picture_is_still_given_in_words(tools, home):
    out = call(tools, "show_card", DIAGRAM)
    assert out.startswith("How a VPN works: Laptop → Tunnel [new] → Internet")
    assert out.endswith("(agentd is not running, so nothing could be drawn.)")


def test_with_no_bar_attached_it_says_so(tools, home):
    (home / "run").mkdir()
    fake = FakeAgentd(home / "run" / "agentd.sock", shown=False)
    assert call(tools, "show_card", DIAGRAM).endswith("(No screen is showing it: here it is in words.)")
    fake.close()


def test_agentd_refusing_the_card_passes_its_reasons_on(home):
    (home / "run").mkdir()
    fake = FakeAgentd(home / "run" / "agentd.sock", reply={"type": "card_ack", "shown": False, "errors": ["bad thing"]})
    assert cardtools.deliver({"x": 1}) == (False, "bad thing")
    fake.close()


def test_system_map_draws_the_real_machine_and_the_agent_only_points(tools, home, monkeypatch):
    (home / "run").mkdir()
    fake = FakeAgentd(home / "run" / "agentd.sock")
    card, _ = cards.validate_diagram({"shape": "chain", "title": "How you're connected",
                                      "nodes": [{"id": "laptop", "label": "This laptop"},
                                                {"id": "router", "label": "Router"}], "say": "All of it answers."})
    card["source"] = "network"
    seen = []
    monkeypatch.setattr(sysmap, "capture", lambda kind, target="", **kw: seen.append((kind, target, kw)) or
                        {"card": card, "facts": []})
    monkeypatch.setenv("BOMBADIL_PROVIDER", "codex")
    out = call(tools, "system_map", {"kind": "network", "highlight": "router", "say": "Your router is old."})
    assert seen == [("network", "", {"provider": "codex"})]
    assert out == "How you're connected: This laptop → Router\nYour router is old.\n(shown above the bar)"
    sent = fake.got[0]["card"]
    assert sent["highlight"] == ["router"] and sent["source"] == "network"
    fake.close()


def test_system_map_that_cannot_read_says_why_as_the_answer(tools, monkeypatch):
    def unavailable(kind, target="", **kw):
        raise sysmap.Unavailable("Could not read the screens: Hyprland did not answer.")
    monkeypatch.setattr(sysmap, "capture", unavailable)
    assert call(tools, "system_map", {"kind": "screens"}) == "Could not read the screens: Hyprland did not answer."


def test_system_map_for_a_kind_it_cannot_draw(tools):
    assert "I can draw network, boot" in call(tools, "system_map", {"kind": "printers"})


def test_the_provider_comes_from_the_environment_then_the_config(home, monkeypatch):
    monkeypatch.delenv("BOMBADIL_PROVIDER", raising=False)
    assert cardtools.provider_name() == "claude"
    (home / "config").mkdir()
    (home / "config" / "config.toml").write_text('provider = "codex"\n')
    assert cardtools.provider_name() == "codex"
    monkeypatch.setenv("BOMBADIL_PROVIDER", "claude")
    assert cardtools.provider_name() == "claude"


@pytest.mark.asyncio
async def test_a_card_really_crosses_the_socket_to_agentd(home):
    from bombadil import agentd, providers
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server = asyncio.create_task(d.serve())
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    r, w = await asyncio.open_unix_connection(str(d.socket_path))
    await r.readline()
    await r.readline()
    card, _ = cards.validate_diagram(DIAGRAM)
    shown = await asyncio.to_thread(cardtools.deliver, card)
    assert shown == (True, "")
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), 5))
        if m.get("kind") == "card":
            break
    assert m["card"]["title"] == "How a VPN works"
    w.close()
    server.cancel()

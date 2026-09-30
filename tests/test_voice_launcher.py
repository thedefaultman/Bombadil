"""The launcher word `voice`: exact, local, offline, and answered by agentd."""

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import pytest

from bombadil import agentd, apps, launcher, persona, providers

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text", [
    "voice", "Voice", "VOICE.", "  voice  ", "voice?", "open voice", "Show voice", "go to voice", "launch voice",
])
def test_the_word_voice_opens_the_card_locally(home, text):
    action = launcher.match(text, [])
    assert action is not None and (action.kind, action.verb) == ("voice", "open")


@pytest.mark.parametrize("text", [
    "change my voice", "be less chatty", "call me Dan", "voice card", "what is my voice", "the voice",
    "turn the voice down", "use the merry voice", "!voice", "close voice", "hide voice", "voicemail", "voices",
    "open the voice card", "голос voice",
])
def test_anything_more_than_the_word_goes_to_the_agent(home, text):
    assert launcher.match(text, []) is None


def test_an_app_you_made_called_voice_wins(home):
    apps.create("Voice", "import QtQuick\nItem {}\n")
    assert launcher.match("voice").kind == "app"


def test_the_word_is_a_utility_word_so_it_completes_but_never_outranks_a_core_one():
    assert "voice" in launcher.UTILITY_COMMANDS and "voice" not in launcher.CORE_COMMANDS
    assert "voice" not in launcher.NO_COMPLETE
    entry = next(e for e in launcher.entries([]) if e["name"] == "voice")
    assert entry["kind"] == "command" and entry["words"] == ["voice"] and entry["title"] == "Voice"


def test_a_button_can_send_the_word_by_name():
    assert agentd._action({"action": "voice"}) == launcher.Action("voice")
    assert agentd._action({"action": "voices"}) is None


@pytest.mark.asyncio
async def test_bombadil_ask_voice_prints_its_done_text_and_opens_the_card(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_GREET_DELAY", "0")
    persona.save("Dan", "plain")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server = asyncio.create_task(d.serve())
    try:
        for _ in range(100):
            if d.socket_path.exists():
                break
            await asyncio.sleep(0.02)
        r, w = await asyncio.open_unix_connection(str(d.socket_path), limit=1 << 24)
        w.write(b'{"type": "bar", "idle": false}\n')
        await w.drain()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "bin" / "bombadil"), "ask", "voice", env=dict(os.environ),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 10)
        assert proc.returncode == 0, err
        assert out.decode() == "Pick a voice, or press Esc.\n"
        seen = []
        while True:
            msg = json.loads(await asyncio.wait_for(r.readline(), 3))
            seen.append(msg)
            if msg.get("type") == "persona_ask":
                break
        assert (msg["current"], msg["name"], msg["voice"]) == (True, "Dan", "plain")
        w.close()
    finally:
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server

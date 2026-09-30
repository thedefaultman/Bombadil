"""The Noticed window and the loop service, together: a real AgentD with the real service on a real socket,
and the real window (share/apps/noticed) in a child process pressing real buttons. The unit tests of each
side use a double of the other; this is where the two are shown to speak the same thing.

Nothing opens a browser or touches the network: the service's opener and fetcher are injected.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
sys.path.insert(0, str(Path(__file__).parent))
from test_loop_service import (  # noqa: E402  (fixtures and helpers shared with the service tests)
    Clock,
    boot,
    connect,
    hear,
    machine,  # noqa: F401
    passwords_asks,
    plant,
    say,
    seed,
    words_file,
)
from test_noticed_app import PRELUDE, ROOT, RUNNER  # noqa: E402

from bombadil import agentd, providers  # noqa: E402
from bombadil.loop.service import LoopService  # noqa: E402

# The prelude starts a fake agentd of its own; here the socket belongs to the real one.
REAL = PRELUDE.replace(
    'short = tempfile.mkdtemp(prefix="nt")\nsockpath = os.path.join(short, "s")\n'
    'os.environ["BOMBADIL_SOCKET"] = sockpath\nfake = FakeAgentd(sockpath)\n',
    'sockpath = os.environ["BOMBADIL_SOCKET"]\n'
    'fake = type("Real", (), {"stop": lambda self: None})()\n')
assert REAL != PRELUDE

BODY = r'''
assert wait_until(settled, 8000), "the window never heard a list"
OUT["texts"] = texts()
assert has("show me my passwords") or has("my passwords"), texts()
# The offered ask has the service's own button: make the word.
ids = [i.objectName() for i in items() if i.objectName().startswith("btn:primary:")]
OUT["primary_buttons"] = ids
assert ids, texts()
click(ids[0])
def visible(prefix):
    return [i.objectName() for i in items() if i.objectName().startswith(prefix) and i.isVisible()]
assert wait_until(lambda: visible("btn:undo:"), 8000), texts()
OUT["after_make"] = texts()
undo = visible("btn:undo:")
click(undo[0])
assert wait_until(lambda: visible("btn:putback:"), 8000), texts()
click(visible("btn:putback:")[0])
assert wait_until(lambda: visible("btn:undo:"), 8000), texts()
# A finding: why, then the send card with what goes and what stays, then the page.
why = visible("btn:why:")
OUT["why_buttons"] = why
if why:
    click(why[0])
    OUT["why_texts"] = [t for t in texts() if t.startswith("Expected") or t.startswith("Seen")]
send = visible("btn:send:")
assert send, texts()
click(send[0])
assert wait_until(lambda: find("sendCard") is not None, 8000), texts()
assert wait_until(lambda: find("btn:open") is not None and find("btn:open").isEnabled(), 8000), texts()
OUT["send_text"] = prop("sendText", "text") or ""
click("btn:open")
pump(800)
OUT["done"] = True
'''


@pytest.mark.asyncio
async def test_the_window_and_the_service_speak_the_same_thing(machine, monkeypatch):  # noqa: F811
    if not (ROOT / "share" / "qml" / "Bombadil" / "qmldir").exists():
        pytest.skip("the app kit is not in this tree")
    seed(passwords_asks())
    found = plant()
    opened, asked = [], []
    sock = Path(tempfile.mkdtemp(prefix="nw")) / "a.sock"
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), socket_path=sock)
    d.loop = LoopService(d, clock=Clock(), opener=lambda url: opened.append(url) or "panel",
                         fetcher=lambda url, timeout: asked.append(url) or {"items": []})
    d.loop.debounce = 0.05
    server = await boot(d)
    try:
        # The bar: hello, so an offer may be made, then the state.
        r, w = await connect(d)
        await say(w, {"type": "hello", "client": "bar", "pid": 1, "build": "x"})
        await say(w, {"type": "noticed_state"})
        await hear(r, lambda m: m.get("type") == "noticed" and m["count"] >= 1)
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
               "PYTHONPATH": str(ROOT / "src"), "BOMBADIL_SOCKET": str(sock)}
        script = (REAL + f"BODY = {BODY!r}\n" + RUNNER
                  + "\nhost.close()\nprint('RESULT ' + json.dumps(OUT, default=str))\n")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 120)
        text = out.decode()
        assert proc.returncode == 0, f"{text}\n{err.decode()[-4000:]}"
        lines = [x for x in text.splitlines() if x.startswith("RESULT ")]
        assert lines, text
        result = json.loads(lines[-1].removeprefix("RESULT "))
    finally:
        d.loop.stop()
        server.cancel()
    assert result["done"] is True
    assert result["primary_buttons"] and result["why_buttons"]
    assert any(t.startswith("Expected: ") for t in result["why_texts"])
    assert result["send_text"].startswith("Title: ") and "my passwords" not in result["send_text"]
    assert [w["phrase"] for w in words_file()] == ["my passwords"]        # undone and put back: it stands
    assert opened and opened[0].startswith("https://github.com/") and found.fp.rsplit(":", 1)[-1] in asked[0]

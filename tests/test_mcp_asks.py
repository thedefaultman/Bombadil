"""os-mcp's read-only `asks` tool: what the person asks most, from their own counts, and nothing else."""

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bombadil import hypr, mcp_server, snapshots
from bombadil.loop import store
from bombadil.loop.offers import Config

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
import golden_corpus as gc

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400
NOTHING = "Nothing counted yet."


@pytest.fixture
def server(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_LOOP", str(home / "loop"))
    return mcp_server.OsTools(hypr.Hyprland(), snapshots.Snapshots())


def call(server, **args):
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "asks", "arguments": args}})
    return reply["result"]


def text(server, **args) -> str:
    result = call(server, **args)
    assert not result.get("isError"), result
    [part] = result["content"]
    assert part["type"] == "text"
    return part["text"]


def seed(home, *repeats):
    """Each (ask, app, times) is one request made that many times on as many days, counted by the store."""
    now = time.time()
    rows = []
    for g, (said, app, times) in enumerate(repeats):
        for i in range(times):
            t = now - (2 * (times - i) + 1) * DAY + g * 3600
            rows.append({"id": f"{int(t * 1000)}-{g}{i}", "text": said, "t": t, "day": 0, "seconds": 8,
                         "events": [["mcp__bombadil-os__open_app", {"name": app}]], "group": "x"})
    turns, _ = gc.write_corpus(home / "state", rows)
    s = store.LoopStore(home / "loop" / "loop.db", app_list=gc.app_list(), config=Config())
    s.ingest(turns, now)
    lines = s.asks_text(10, now)
    s.close()
    return lines


def test_the_tool_is_listed_with_one_optional_argument_and_says_what_it_reads(server):
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    spec = {t["name"]: t for t in listed}["asks"]
    assert spec["inputSchema"]["properties"].keys() == {"limit"} and spec["inputSchema"]["required"] == []
    assert "own history on this machine" in spec["description"] and "Nothing else" in spec["description"]


def test_with_nothing_counted_it_says_so_and_makes_nothing(server, home):
    assert text(server) == NOTHING
    assert text(server, limit=3) == NOTHING
    assert not (home / "loop").exists()


def test_it_tells_what_is_asked_most_in_his_own_words(server, home):
    lines = seed(home, ("show me my passwords", "passwords", 4), ("show me my runs", "runs", 3))
    got = text(server)
    assert got == "\n".join(lines)
    first, sentence, second, _ = got.splitlines()
    assert first.startswith("4 times on 4 days") and "passwords" in first
    assert sentence == "    “show me my passwords”"
    assert second.startswith("3 times on 3 days") and "runs" in second


def test_limit_keeps_the_heaviest_and_an_odd_one_is_not_an_error(server, home):
    seed(home, ("show me my passwords", "passwords", 4), ("show me my runs", "runs", 3))
    one = text(server, limit=1)
    assert one.count(" times on ") == 1 and "passwords" in one
    for odd in (0, -4, 10**9, "2", None, "many", 1.5, [3]):
        assert " times on " in text(server, limit=odd)
    assert text(server, limit=0).count(" times on ") == 1      # below the smallest is the smallest


def test_it_reads_and_writes_nothing(server, home):
    seed(home, ("show me my passwords", "passwords", 4))
    path = home / "loop" / "loop.db"
    before = hashlib.sha1(path.read_bytes()).hexdigest()
    mtime = path.stat().st_mtime_ns
    for _ in range(3):
        text(server)
    assert hashlib.sha1(path.read_bytes()).hexdigest() == before and path.stat().st_mtime_ns == mtime
    s = store.LoopStore(path, app_list=gc.app_list(), config=Config())
    assert s.status()["requests"] == 4 and s.waiting() == 0
    s.close()


def test_the_connection_it_reads_with_cannot_write(server, home, monkeypatch):
    seed(home, ("show me my passwords", "passwords", 4))
    calls = []
    real = sqlite3.connect

    def connect(target, *a, **kw):
        calls.append(target)
        conn = real(target, *a, **kw)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM requests")
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert " times on " in text(server)
    assert len(calls) == 1 and calls[0].endswith("?mode=ro")


def test_a_file_that_is_not_a_database_or_has_no_tables_is_nothing_counted_yet(server, home):
    (home / "loop").mkdir()
    for content in (b"this is not a database", b"", b"\x00" * 900):
        (home / "loop" / "loop.db").write_bytes(content)
        assert text(server) == NOTHING
    assert (home / "loop" / "loop.db").read_bytes() == b"\x00" * 900


def test_it_answers_over_stdio_as_the_agent_would_ask(home, tmp_path):
    lines = seed(home, ("show me my passwords", "passwords", 4))
    env = {"HOME": str(home), "BOMBADIL_STATE": str(home / "state"), "BOMBADIL_LOOP": str(home / "loop"),
           "BOMBADIL_CONFIG": str(home / "config"), "XDG_DATA_HOME": str(home / "share"),
           "BOMBADIL_APPS": str(home / "Apps"), "PATH": "/usr/bin:/bin"}
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    ask = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "asks", "arguments": {}}}
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-os-mcp")], text=True, capture_output=True,
                          input=json.dumps(hello) + "\n" + json.dumps(ask) + "\n", timeout=60, env=env,
                          check=False)
    reply = json.loads(done.stdout.splitlines()[-1])
    assert reply["id"] == 2 and '"isError": true' not in done.stdout
    assert reply["result"]["content"] == [{"type": "text", "text": "\n".join(lines)}]

"""Ledger v2: the rows a real AgentD run writes (docs/LOOP.md), and a sample of them kept as a
fixture so the format and the code cannot drift apart unseen."""

import asyncio
import json
from pathlib import Path

import pytest

from bombadil import agentd, paths, providers, snapshots

FIXTURE = Path(__file__).parent / "fixtures" / "loop" / "ledger_v2.jsonl"


async def _client(path):
    return await asyncio.open_unix_connection(str(path), limit=1 << 24)


async def _start(d):
    server = asyncio.create_task(d.serve())
    for _ in range(50):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)
    r, w = await _client(d.socket_path)
    await r.readline()   # status
    await r.readline()   # entries
    return server, r, w


async def _send(writer, /, **msg):
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()


async def _until(r, pred, timeout=8):
    out = []
    while True:
        m = json.loads(await asyncio.wait_for(r.readline(), timeout))
        out.append(m)
        if pred(m):
            return out


def _kind(kind, **fields):
    return lambda m: m.get("kind") == kind and all(m.get(k) == v for k, v in fields.items())


async def _ledger(n, timeout=5):
    """The ledger's rows once there are `n` of them: a turn's row is written just after its turn_end."""
    rows = []
    for _ in range(int(timeout / 0.02)):
        try:
            rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
        except OSError:
            rows = []
        if len(rows) >= n:
            return rows
        await asyncio.sleep(0.02)
    raise AssertionError(f"only {len(rows)} rows after {timeout}s")


class Scripted(providers.Claude):
    """The real Claude parser over a Python one-liner that prints stream-json lines."""

    def __init__(self, *lines):
        super().__init__("x")
        self.script = "import json, sys\nsys.stdin.read()\n" + "".join(
            f"print(json.dumps({line!r}), flush=True)\n" for line in lines)

    @property
    def installed(self):
        return True

    def command(self, turn, workdir):
        return ["python3", "-c", self.script]


class RecordingSnaps(agentd._NoSnapshots):
    available = True

    def __init__(self):
        self.made = []

    def create(self, description):
        s = snapshots.Snapshot(len(self.made) + 1, description)
        self.made.append(s)
        return s

    def list(self, limit=20):
        return list(self.made)

    def rollback(self, number):
        return True


INIT = {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet-4-5",
        "mcp_servers": [{"name": "bombadil-os", "status": "connected"}]}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": "s1",
          "total_cost_usd": 0.0123, "usage": {"input_tokens": 120, "output_tokens": 45,
                                               "cache_read_input_tokens": 900, "cache_creation_input_tokens": 30}}


def _tool(name, tid):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": {}}]}}


@pytest.mark.asyncio
async def test_a_model_turn_writes_a_ledger_v2_row(home):
    provider = Scripted(
        INIT,
        # The streamed start of a call is for the live line only; the complete message is the step.
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "tool_use", "id": "t1", "name": "Bash"}}},
        _tool("Bash", "t1"), _tool("mcp__bombadil-os__show_panel", "t2"), _tool("Bash", "t3"),
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour",
                                                          "resetsAt": 1790003600}},
        {"type": "hologram", "x": 1}, {"type": "hologram"}, {"type": "brand.new"},
        RESULT)
    d = agentd.AgentD(provider, agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="what is on the screen")
    msgs = await _until(r, _kind("turn_end"))
    row = (await _ledger(1))[0]
    assert row["v"] == 2 and row["n"] == 1 and row["origin"] == "typed"
    assert row["id"].endswith("-1") and row["id"] == Path(row["details"]).stem
    assert row["started"] <= row["t"] and row["seconds"] >= 0
    assert row["tools"] == {"n": 3, "names": ["Bash", "mcp__bombadil-os__show_panel"]}
    assert row["model"] == "claude-sonnet-4-5" and row["cost"] == 0.0123
    assert row["usage"] == {"input": 120, "output": 45, "cache_read": 900, "cache_write": 30}
    assert row["rate_limit"] == {"status": "allowed", "rateLimitType": "five_hour", "resetsAt": 1790003600}
    assert row["drift"] == {"hologram": 2, "brand.new": 1}
    # What the row keeps is shown to nobody and is not written to the turn's own log.
    assert not [m for m in msgs if m.get("kind") == "meta"]
    kept = [json.loads(line)["kind"] for line in Path(row["details"]).read_text().splitlines()]
    assert "meta" not in kept and kept.count("tool") == 3
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_provider_that_says_nothing_leaves_nulls_and_no_drift_key(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="hello")
    await _until(r, _kind("turn_end"))
    row = (await _ledger(1))[0]
    assert (row["model"], row["cost"], row["usage"], row["rate_limit"]) == (None, None, None, None)
    assert row["tools"] == {"n": 0, "names": []} and "drift" not in row
    await _send(w, type="prompt", text="!echo hi")
    await _until(r, _kind("turn_end"))
    shell = (await _ledger(2))[1]
    assert shell["provider"] == "shell" and shell["tools"] == {"n": 1, "names": ["Bash"]} and shell["n"] == 2
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_stream_gone_wild_counts_its_odd_lines_under_one_name(home):
    odd = [{"type": f"odd{i}"} for i in range(agentd.MAX_DRIFT_TYPES + 5)]
    d = agentd.AgentD(Scripted(INIT, *odd, RESULT), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="hello")
    await _until(r, _kind("turn_end"))
    drift = (await _ledger(1))[0]["drift"]
    assert len(drift) == agentd.MAX_DRIFT_TYPES + 1 and drift["other"] == 5
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_origin_comes_from_the_message_or_the_apps_prefix_and_follows_a_queued_turn(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="!sleep 0.4")
    await _until(r, _kind("turn_start"))
    for msg in ({"text": "from the cli", "origin": "cli"}, {"text": "[from app notes] what is on my disk"},
                {"text": "plain, bad origin", "origin": "nonsense"}, {"text": "from the loop", "origin": "loop"},
                {"text": "[from app notes] but told so", "origin": "typed"}):
        await _send(w, type="prompt", **msg)
    rows = await _ledger(6)
    assert [(row["prompt"], row["origin"]) for row in rows] == [
        ("!sleep 0.4", "typed"), ("from the cli", "cli"), ("[from app notes] what is on my disk", "app"),
        ("plain, bad origin", "typed"), ("from the loop", "loop"), ("[from app notes] but told so", "typed")]
    assert not d._origins   # each is forgotten once its turn has started
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_unqueued_prompt_leaves_no_origin_behind(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="!sleep 0.4")
    await _until(r, _kind("turn_start"))
    await _send(w, type="prompt", text="never mind", origin="cli")
    queued = (await _until(r, _kind("queued")))[-1]
    await _send(w, type="unqueue", turn=queued["turn"])
    await _until(r, _kind("unqueued"))
    assert not d._origins
    await _until(r, _kind("turn_end"))   # let the running turn finish before the daemon goes
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_loop_can_start_a_turn_of_its_own_with_no_client(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await d.handle({"type": "prompt", "text": "make the word my passwords", "origin": "loop"}, None)
    await _until(r, _kind("turn_end"))
    assert (await _ledger(1))[0]["origin"] == "loop"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_stopped_turn_and_the_stop_that_ended_it_are_linked(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="!sleep 30")
    await _until(r, _kind("tool"))
    await _send(w, type="prompt", text="stop")
    await _until(r, _kind("turn_end"))
    rows = await _ledger(2)
    stop = next(x for x in rows if x.get("kind") == "local")
    turn = next(x for x in rows if x.get("kind") is None)
    assert turn["stopped"] is True and turn["id"].endswith("-1")
    assert (stop["action"], stop["verb"], stop["via"], stop["word"], stop["of"], stop["of_snapshot"]) == (
        "stop", "stop", "typed", None, turn["id"], None)
    assert stop["v"] == 2 and stop["prompt"] == "stop" and stop["result"] == "Stopping." and stop["ok"] is True
    # Esc and `bombadil stop` send a message, not a word: the same row, asked for by a button.
    await _send(w, type="prompt", text="!sleep 30")
    await _until(r, lambda m: m.get("kind") == "tool" and m.get("turn") == 2)
    await _send(w, type="stop")
    await _until(r, _kind("turn_end"))
    rows = await _ledger(4)
    turns = [x for x in rows if x.get("kind") is None]
    by_button = [x for x in rows if x.get("kind") == "local"][1]
    assert (by_button["via"], by_button["of"]) == ("button", turns[1]["id"])
    # Nothing running: still a row for the word, and it says there was nothing to stop.
    await _send(w, type="prompt", text="stop")
    rows = await _ledger(5)
    assert rows[-1]["of"] is None and rows[-1]["result"] == "Nothing is running."
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_stop_before_the_cli_starts_still_gives_the_turn_its_id(home):
    class SlowSnaps(RecordingSnaps):
        def create(self, description):
            import time
            time.sleep(0.8)
            return super().create(description)

    d = agentd.AgentD(providers.Fake("x"), SlowSnaps())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="hello")
    await _until(r, lambda m: m.get("text") == "Saving a restore point")
    await _send(w, type="stop")
    await _until(r, _kind("turn_end"))
    rows = await _ledger(2)
    turn = next(x for x in rows if x.get("kind") is None)
    stop = next(x for x in rows if x.get("kind") == "local")
    assert turn["stopped"] is True and turn["id"].endswith("-1") and turn["snapshot"] == 1
    assert turn["tools"] == {"n": 0, "names": []} and turn["v"] == 2 and turn["origin"] == "typed"
    assert stop["of"] == turn["id"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_undo_row_names_the_restore_point_and_the_turn_it_took_back(home):
    d = agentd.AgentD(providers.Fake("x"), RecordingSnaps())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="!echo one")
    await _until(r, _kind("turn_end"))
    await _send(w, type="prompt", text="!echo two")
    await _until(r, _kind("turn_end"))
    await _send(w, type="prompt", text="undo")
    await _until(r, _kind("local", phase="done"))
    rows = await _ledger(3)
    undo = rows[-1]
    assert undo["kind"] == "local" and undo["action"] == "undo" and undo["ok"] is True
    assert (undo["verb"], undo["via"], undo["word"]) == ("undo", "typed", None)
    assert undo["of_snapshot"] == 2 and undo["of"] == rows[1]["id"] and rows[1]["snapshot"] == 2
    # The Undo button sends a message, and says so; one more undo goes one turn further back.
    await _send(w, type="local", action="undo")
    rows = await _ledger(4)
    assert rows[-1]["via"] == "button" and rows[-1]["prompt"] == "undo" and rows[-1]["of_snapshot"] == 1
    assert rows[-1]["of"] == rows[0]["id"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_failed_undo_links_nothing_and_an_unknown_turn_leaves_of_empty(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="undo")
    await _until(r, _kind("local", phase="done"))
    row = (await _ledger(1))[0]
    assert row["ok"] is False and row["of"] is None and row["of_snapshot"] is None
    # A restore point from before this agentd started is known by its number only.
    d.snaps = d.launcher.snaps = snaps = RecordingSnaps()
    snaps.made.append(snapshots.Snapshot(9, "turn:9: something from yesterday"))
    await _send(w, type="prompt", text="undo")
    await _until(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done" and m.get("ok"))
    row = (await _ledger(2))[1]
    assert row["of_snapshot"] == 9 and row["of"] is None
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_word_the_loop_made_is_logged_as_a_word(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    made = agentd.launcher.Action("app", "passwords", "open", "Passwords")
    made.word = "my passwords"      # what launcher.match sets on a match from words.toml
    monkeypatch.setattr(agentd.launcher, "match", lambda text, app_list=None: made)
    monkeypatch.setattr(d.launcher, "run", lambda action: (True, "Opened Passwords."))
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="my passwords")
    await _until(r, lambda m: m.get("phase") == "done")
    row = (await _ledger(1))[0]
    assert (row["prompt"], row["action"], row["target"], row["verb"]) == ("my passwords", "app", "passwords", "open")
    assert (row["via"], row["word"]) == ("word", "my passwords")
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_launcher_rows_say_what_was_done_and_how_it_was_asked(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    monkeypatch.setattr(d.launcher, "run", lambda action: (True, "Done."))
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="close the browser")   # a panel: run() itself turns close into hide
    await _until(r, lambda m: m.get("phase") == "done")
    await _send(w, type="prompt", text="wifi")
    await _until(r, lambda m: m.get("phase") == "done" and m.get("action") == "wifi")
    rows = await _ledger(2)
    assert [(x["prompt"], x["action"], x["verb"], x["via"], x["of"], x["word"]) for x in rows] == [
        ("close the browser", "panel", "close", "typed", None, None), ("wifi", "wifi", "wifi", "typed", None, None)]
    w.close()
    server.cancel()


# -- the sample kept in tests/fixtures/loop/ledger_v2.jsonl --

MODEL_KEYS = {"t", "prompt", "result", "ok", "snapshot", "provider", "session", "stopped", "summary", "details",
              "id", "started", "seconds", "origin", "tools", "model", "cost", "usage", "rate_limit", "v", "n"}
LOCAL_KEYS = {"t", "kind", "prompt", "action", "target", "result", "ok", "v", "verb", "via", "word", "of",
              "of_snapshot"}


def _same_shape(row, sample):
    """The same keys, and where both rows have a value under one, the same kind of value."""
    def kind(v):
        return "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else type(v).__name__
    assert set(row) == set(sample), set(row) ^ set(sample)
    for k in row:
        if row[k] is not None and sample[k] is not None:
            assert kind(row[k]) == kind(sample[k]), k


def _sample():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_the_sample_follows_the_format_in_docs_loop():
    rows = _sample()
    model = [r for r in rows if "kind" not in r]
    local = [r for r in rows if r.get("kind") == "local"]
    assert model and local and len(model) + len(local) == len(rows)
    for r in model:
        assert MODEL_KEYS <= set(r) <= MODEL_KEYS | {"drift"}
        assert r["v"] == 2 and r["id"].endswith(f"-{r['n']}") and r["started"] <= r["t"]
        assert r["origin"] in ("typed", "button", "app", "session", "routine", "loop", "retry", "cli")
        assert set(r["tools"]) == {"n", "names"} and len(r["tools"]["names"]) <= r["tools"]["n"]
        assert r["usage"] is None or set(r["usage"]) == {"input", "output", "cache_read", "cache_write"}
        assert "drift" not in r or r["drift"]   # only ever written when there is something in it
    for r in local:
        assert set(r) == LOCAL_KEYS and r["v"] == 2
        assert r["via"] in ("typed", "button", "word") and (r["via"] == "word") == bool(r["word"])
    assert {r["action"] for r in local} >= {"app", "stop", "undo"}
    assert any(r["origin"] == "app" for r in model) and any(r["stopped"] for r in model)
    undo = next(r for r in local if r["action"] == "undo")
    assert undo["of"] in {r["id"] for r in model} and undo["of_snapshot"] in {r["snapshot"] for r in model}
    stop = next(r for r in local if r["action"] == "stop")
    assert stop["of"] in {r["id"] for r in model if r["stopped"]}


@pytest.mark.asyncio
async def test_what_agentd_writes_has_the_shape_of_the_sample(home):
    """Every kind of row in the sample is what a run writes today, key for key."""
    sample = _sample()
    d = agentd.AgentD(Scripted(INIT, _tool("Bash", "t1"), {"type": "hologram"}, RESULT), RecordingSnaps())
    server, r, w = await _start(d)
    await _send(w, type="prompt", text="[from app notes] how full is my disk")
    await _until(r, _kind("turn_end"))
    await _send(w, type="prompt", text="undo")
    await _until(r, _kind("local", phase="done"))
    await _send(w, type="prompt", text="!sleep 30")
    await _until(r, _kind("tool"))
    await _send(w, type="prompt", text="stop")
    await _until(r, _kind("turn_end"))
    rows = await _ledger(4)
    drifted, undo = rows[:2]
    stop = next(x for x in rows if x.get("action") == "stop")
    stopped = next(x for x in rows if x.get("stopped"))
    _same_shape(drifted, next(s for s in sample if s.get("drift")))
    _same_shape(stopped, next(s for s in sample if s.get("stopped") and "kind" not in s))
    _same_shape(undo, next(s for s in sample if s.get("action") == "undo"))
    _same_shape(stop, next(s for s in sample if s.get("action") == "stop"))
    w.close()
    server.cancel()

"""What the bar says about itself: Signals on its own (injected clock, temp dirs), then agentd
hearing the bar over its socket."""

import asyncio
import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from bombadil import agentd, paths, providers
from bombadil.loop import signals
from bombadil.loop.signals import Signals

T0 = 1_790_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def loop(tmp_path):
    return tmp_path / "loop"


@pytest.fixture
def clock():
    return Clock()


def rows(loop, kind=None):
    got = signals.read_events(loop / "signals.jsonl")
    return [r for r in got if kind is None or r["kind"] == kind]


def hello(pid=4242, build="d9dde3b"):
    return {"type": "hello", "client": "bar", "pid": pid, "build": build}


# -- the bar --

def test_hello_is_written_down_and_the_bar_file_follows(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    assert rows(loop) == [{"t": T0, "kind": "hello", "client": "bar", "pid": 4242, "build": "d9dde3b"}]
    assert signals.read_bar(loop / "bar.json") == {"pid": 4242, "connected_at": T0, "alive_at": T0,
                                                   "build": "d9dde3b", "screens": {}}


def test_a_different_pid_and_a_hello_after_the_bar_went_are_restarts(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello(pid=100))
    s.hello(hello(pid=100))          # the same bar again: nothing happened
    assert rows(loop, "restart") == []
    clock.advance(60)
    s.hello(hello(pid=200))          # a different process while the old connection was still there
    assert rows(loop, "restart") == [{"t": T0 + 60, "kind": "restart", "pid": 200, "was": 100}]
    clock.advance(60)
    s.bar_gone()
    clock.advance(1)
    s.hello(hello(pid=200))          # the same pid, but the connection went in between
    restarts = rows(loop, "restart")
    assert [(r["pid"], r["was"]) for r in restarts] == [(200, 100), (200, 200)]
    assert len(rows(loop, "hello")) == 4


def test_the_first_hello_after_agentd_starts_is_no_restart(loop, clock):
    Signals(loop, clock).hello(hello(pid=7))
    assert rows(loop, "restart") == []


def test_alive_is_kept_and_the_bar_file_is_rewritten_at_most_every_two_seconds(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    bar = loop / "bar.json"
    written = bar.stat().st_mtime_ns
    clock.advance(0.5)
    s.alive()
    assert bar.stat().st_mtime_ns == written and signals.read_bar(bar)["alive_at"] == T0   # too soon
    clock.advance(1.0)
    s.tick()                                                                                # still too soon
    assert signals.read_bar(bar)["alive_at"] == T0
    clock.advance(1.0)
    s.tick()                                                                                # 2.5 s after the last write
    assert signals.read_bar(bar)["alive_at"] == T0 + 0.5                                    # what was kept waiting
    clock.advance(5)
    s.alive()
    assert signals.read_bar(bar)["alive_at"] == T0 + 7.5


def test_a_tick_with_nothing_to_do_writes_nothing(loop, clock):
    s = Signals(loop, clock)
    for _ in range(3):
        clock.advance(3)
        s.tick()
    assert not loop.exists() or not list(loop.iterdir())


def test_rects_are_kept_per_screen_and_what_is_not_a_rect_is_dropped(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    s.rects({"type": "rects", "screen": "Virtual-1", "w": 1920, "h": 1080, "rects": [
        {"name": "pill", "x": 700.0, "y": 1000, "w": 520, "h": 44},
        {"name": "line", "x": 10.5, "y": 20, "w": 30, "h": 40},
        {"name": "no height", "x": 1, "y": 2, "w": 3},
        "not a rect", {"name": "nan", "x": float("nan"), "y": 0, "w": 1, "h": 1}, {"x": True, "y": 0, "w": 1, "h": 1},
    ]})
    s.rects({"type": "rects", "screen": "eDP-1", "w": 1280, "h": 800, "rects": []})
    s.rects({"type": "rects", "rects": []})                 # no screen: nothing to file it under
    s.rects({"type": "rects", "screen": "x", "rects": "no"})   # rects that are no list are none
    clock.advance(3)
    s.tick()
    bar = signals.read_bar(loop / "bar.json")
    assert bar["screens"]["Virtual-1"] == {"w": 1920, "h": 1080, "rects": [
        {"name": "pill", "x": 700, "y": 1000, "w": 520, "h": 44}, {"name": "line", "x": 10.5, "y": 20, "w": 30, "h": 40}]}
    assert bar["screens"]["eDP-1"] == {"w": 1280, "h": 800, "rects": []}
    assert bar["screens"]["x"]["rects"] == [] and set(bar["screens"]) == {"Virtual-1", "eDP-1", "x"}
    # A new connection reports its parts afresh; the old bar's are not kept.
    s.hello(hello(pid=5))
    assert signals.read_bar(loop / "bar.json")["screens"] == {}


def test_the_number_of_screens_and_rects_is_bounded(loop, clock):
    s = Signals(loop, clock)
    for i in range(signals.MAX_SCREENS + 4):
        s.rects({"screen": f"S{i}", "w": 1, "h": 1, "rects": []})
    s.rects({"screen": "S0", "w": 1, "h": 1, "rects": [{"name": "r", "x": 0, "y": 0, "w": 1, "h": 1}] * 500})
    clock.advance(3)
    s.tick()
    bar = signals.read_bar(loop / "bar.json")
    assert len(bar["screens"]) == signals.MAX_SCREENS and len(bar["screens"]["S0"]["rects"]) == signals.MAX_RECTS


def test_the_bar_going_leaves_its_last_report_and_only_an_alive_counts_as_alive(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    clock.advance(1)
    s.alive()
    clock.advance(0.5)
    s.rects({"screen": "Virtual-1", "w": 1920, "h": 1080, "rects": [{"name": "pill", "x": 1, "y": 2, "w": 3, "h": 4}]})
    clock.advance(0.2)
    s.bar_gone()
    bar = signals.read_bar(loop / "bar.json")
    # Caught up at once, nothing lost; a rects message is not a heartbeat.
    assert bar["alive_at"] == T0 + 1 and bar["screens"]["Virtual-1"]["rects"]
    assert bar["connected_at"] == T0


# -- summons --

def test_summons_count_up_and_an_ack_in_time_leaves_nothing_to_report(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    ids = [s.summon(), s.summon("Virtual-1"), s.summon()]
    assert ids == [1, 2, 3]
    s.focus_ack({"type": "focus_ack", "id": 2, "ms": 120})
    s.focus_ack({"type": "focus_ack", "id": 1, "ms": 80.5})
    s.focus_ack({"type": "focus_ack", "id": 3, "ms": 400})
    clock.advance(5)
    s.tick()
    assert rows(loop, "focus_timeout") == []
    assert [(r["id"], r.get("screen")) for r in rows(loop, "summon")] == [(1, None), (2, "Virtual-1"), (3, None)]
    assert [(r["id"], r["ms"]) for r in rows(loop, "focus_ack")] == [(2, 120), (1, 80.5), (3, 400)]
    assert all("late" not in r for r in rows(loop, "focus_ack"))


def test_a_summon_nobody_answers_is_written_up_once_after_a_second_and_a_half(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    sid = s.summon()
    clock.advance(1.4)
    s.tick()
    assert rows(loop, "focus_timeout") == []
    clock.advance(0.2)
    s.tick()
    clock.advance(3)
    s.tick()
    assert rows(loop, "focus_timeout") == [{"t": T0 + 1.6, "kind": "focus_timeout", "id": sid, "waited": 1.6,
                                            "bar": True}]
    # An ack after that is still written, and says it was late: slow, not stuck.
    s.focus_ack({"id": sid, "ms": 2100})
    assert rows(loop, "focus_ack")[-1] == {"t": T0 + 4.6, "kind": "focus_ack", "id": sid, "ms": 2100, "late": True}


def test_a_timeout_says_when_no_bar_was_there_to_answer(loop, clock):
    s = Signals(loop, clock)
    s.summon()
    clock.advance(2)
    s.tick()
    assert rows(loop, "focus_timeout")[0]["bar"] is False


def test_tick_takes_its_own_time_when_given_one(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    s.summon()
    s.tick(T0 + 10)
    assert rows(loop, "focus_timeout")[0]["waited"] == 10


def test_an_ack_that_makes_no_sense_is_a_row_and_nothing_more(loop, clock):
    s = Signals(loop, clock)
    s.focus_ack({"id": "x", "ms": "fast"})
    s.focus_ack({})
    assert [(r["id"], r["ms"]) for r in rows(loop, "focus_ack")] == [(None, None), (None, None)]


def test_friction_keeps_plain_values_and_cannot_overwrite_the_row_itself(loop, clock):
    s = Signals(loop, clock)
    s.friction({"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": True,
                "kind": "hello", "t": 5, "nested": {"a": 1}, "list": [1], "long": "x" * 500})
    (row,) = rows(loop, "friction")
    assert row == {"t": T0, "kind": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": True, "long": "x" * 80}


def test_the_summons_waiting_for_an_ack_are_bounded(loop, clock):
    s = Signals(loop, clock)
    for _ in range(signals.MAX_WAITING + 50):
        s.summon()
    assert len(s._summons) == signals.MAX_WAITING


# -- writing --

def test_bar_json_is_never_seen_half_written(loop, clock):
    s = Signals(loop, clock)
    s.hello(hello())
    seen, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            bar = signals.read_bar(loop / "bar.json")
            seen.append(bar is not None and bar["screens"] is not None)

    t = threading.Thread(target=reader)
    t.start()
    big = [{"name": f"r{i}", "x": i, "y": i, "w": 5, "h": 5} for i in range(200)]
    for i in range(300):
        clock.advance(2.5)
        s.rects({"screen": f"S{i % 3}", "w": 1, "h": 1, "rects": big})
    stop.set()
    t.join()
    assert seen and all(seen)
    assert not [p for p in loop.iterdir() if p.name.endswith(".tmp")]


def test_a_write_that_fails_leaves_the_old_file_and_costs_nothing(loop, clock, monkeypatch, capsys):
    s = Signals(loop, clock)
    s.hello(hello(pid=1))
    before = (loop / "bar.json").read_text()
    clock.advance(3)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    s.hello(hello(pid=2))                     # writes its rows, fails on the file
    assert (loop / "bar.json").read_text() == before
    assert not [p for p in loop.iterdir() if p.name.endswith(".tmp")]
    monkeypatch.undo()
    clock.advance(3)
    s.tick()                                   # the change that could not be written goes out on a later tick
    assert signals.read_bar(loop / "bar.json")["pid"] == 2
    assert capsys.readouterr().err.count("disk full") == 1


def test_a_disk_that_takes_no_writes_costs_a_line_once_and_never_a_call(tmp_path, clock, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    s = Signals(blocked / "loop", clock)
    s.hello(hello())
    s.alive()
    s.rects({"screen": "a", "w": 1, "h": 1, "rects": []})
    assert s.summon() == 1 and s.summon() == 2
    s.focus_ack({"id": 1, "ms": 5})
    s.friction({"what": "esc"})
    s.agentd_started("/run/x.sock")
    for _ in range(3):
        clock.advance(3)
        s.tick()
    s.bar_gone()
    err = capsys.readouterr().err
    assert err and all(line.startswith("signals:") for line in err.splitlines())
    assert len(err.splitlines()) == len(set(err.splitlines()))   # each trouble said once, not every tick


def test_a_message_of_the_wrong_shape_never_raises(loop, clock, capsys):
    s = Signals(loop, clock)
    s.hello({"pid": "four", "build": {"a": 1}})
    s.hello({"pid": True})
    s.rects({"screen": 5, "rects": [None, 3, {"x": "a"}]})
    s.rects({"screen": "ok", "w": "wide", "h": None, "rects": [{"x": 1, "y": 2, "w": 3, "h": 4, "name": None}]})
    s.friction({1: 2, None: 3})
    s.focus_ack({"id": [1], "ms": {}})
    assert capsys.readouterr().err == ""
    assert rows(loop, "restart") == []        # no pid, no pid: the same bar


def test_the_loop_directory_is_the_paths_one_when_none_is_given(home, clock):
    s = Signals(clock=clock)
    s.hello(hello())
    assert (paths.loop_dir() / "signals.jsonl").exists() and (paths.loop_dir() / "bar.json").exists()
    assert signals.read_events()[0]["kind"] == "hello" and signals.read_bar()["pid"] == 4242


def test_ticks_from_a_thread_and_messages_on_another_do_not_trip_over_each_other(loop):
    s = Signals(loop)
    s.hello(hello())
    stop = threading.Event()
    errors = []

    def ticker():
        try:
            while not stop.is_set():
                s.tick(time.time() + 10)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=ticker)
    t.start()
    for _ in range(200):
        s.summon()
        s.alive()
    stop.set()
    t.join()
    assert not errors
    assert {r["id"] for r in rows(loop, "summon")} == set(range(1, 201))
    assert {r["id"] for r in rows(loop, "focus_timeout")} <= set(range(1, 201))


# -- reading --

def test_read_events_gives_the_rows_after_a_time_and_skips_what_is_not_a_row(tmp_path):
    path = tmp_path / "signals.jsonl"
    path.write_text("\n".join([
        json.dumps({"t": 100.0, "kind": "hello"}), "not json", json.dumps([1, 2]), json.dumps({"kind": "no t"}),
        json.dumps({"t": "later", "kind": "odd"}), json.dumps({"t": 200.5, "kind": "summon", "id": 1}),
        json.dumps({"t": 300, "kind": "friction"}), "", '{"t": 400, "kind":']) + "\n")
    assert [r["kind"] for r in signals.read_events(path)] == ["hello", "summon", "friction"]
    assert [r["kind"] for r in signals.read_events(path, since=100.0)] == ["summon", "friction"]   # after, not at
    assert [r["kind"] for r in signals.read_events(path, since=250)] == ["friction"]
    assert signals.read_events(path, since=1000) == []
    assert signals.read_events(tmp_path / "missing.jsonl") == []


def test_read_bar_is_none_until_the_bar_has_made_a_report(tmp_path):
    assert signals.read_bar(tmp_path / "bar.json") is None
    (tmp_path / "bar.json").write_text("{half")
    assert signals.read_bar(tmp_path / "bar.json") is None
    (tmp_path / "bar.json").write_text("[1]")
    assert signals.read_bar(tmp_path / "bar.json") is None


# -- agentd.json and the build --

def test_agentd_started_says_who_is_serving(loop, clock):
    Signals(loop, clock).agentd_started("/run/user/1000/bombadil/agentd.sock")
    info = json.loads((loop / "agentd.json").read_text())
    assert set(info) == {"pid", "started", "build", "socket"}
    assert info["pid"] == os.getpid() and info["started"] == T0 and info["socket"] == "/run/user/1000/bombadil/agentd.sock"
    assert isinstance(info["build"], str)


def _elsewhere(monkeypatch, tmp_path):
    """Move where the module thinks its tree is, so there is no VERSION and no git there."""
    monkeypatch.setattr(signals, "__file__", str(tmp_path / "tree" / "src" / "bombadil" / "loop" / "signals.py"))
    return tmp_path / "tree"


def test_the_build_is_the_version_stamp_shipped_with_the_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "share"))
    tree = _elsewhere(monkeypatch, tmp_path)
    assert signals.build_id() == ""
    tree.mkdir(parents=True)
    (tree / "VERSION").write_text("2026.09.30-7\n\n")                   # beside src and bin
    assert signals.build_id() == "2026.09.30-7"
    (tmp_path / "share").mkdir()
    (tmp_path / "share" / "VERSION").write_text("  b7c1d2e  \nsecond line ignored\n")   # in the share dir
    assert signals.build_id() == "b7c1d2e"
    (tmp_path / "share" / "VERSION").write_text("\n")                   # an empty stamp says nothing
    assert signals.build_id() == "2026.09.30-7"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_without_a_stamp_the_build_is_the_checkouts_git_short_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "share"))
    tree = _elsewhere(monkeypatch, tmp_path)
    tree.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(tree)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    want = subprocess.run([*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert want and signals.build_id() == want


def test_a_tree_git_cannot_read_has_no_build(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "share"))
    tree = _elsewhere(monkeypatch, tmp_path)
    (tree / ".git").mkdir(parents=True)        # looks like a checkout, is not one
    assert signals.build_id() == ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    assert signals.build_id() == ""


# -- agentd hears the bar --

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


async def _say(writer, /, **msg):
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()


async def _wait(cond, timeout=5):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never happened")


@pytest.mark.asyncio
async def test_ping_is_answered_to_whoever_asked_and_nobody_else(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    r2, w2 = await _client(d.socket_path)
    await r2.readline(), await r2.readline()
    await _say(w, type="ping")
    pong = json.loads(await asyncio.wait_for(r.readline(), 5))
    assert pong["type"] == "pong" and pong["pid"] == os.getpid() and abs(pong["t"] - time.time()) < 5
    await _say(w2, type="status")
    assert json.loads(await asyncio.wait_for(r2.readline(), 5))["type"] == "status"   # no pong queued before it
    w.close(), w2.close()
    server.cancel()


@pytest.mark.asyncio
async def test_what_the_bar_says_is_written_down_and_its_hangup_is_noted(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, _, w = await _start(d)
    await _say(w, type="hello", client="bar", pid=4321, build="abc1234")
    await _say(w, type="alive", t=1)
    await _say(w, type="rects", screen="Virtual-1", w=1920, h=1080,
               rects=[{"name": "pill", "x": 700, "y": 1000, "w": 520, "h": 44}])
    await _say(w, type="friction", what="esc", count=3, seconds=10, drawer=True)
    await _wait(lambda: len(signals.read_events()) >= 2)
    kinds = [e["kind"] for e in signals.read_events()]
    assert kinds == ["hello", "friction"]
    bar = signals.read_bar()
    assert bar["pid"] == 4321 and bar["build"] == "abc1234"
    # The bar goes, and a new one says hello: a restart, however quick.
    w.close()
    await _wait(lambda: d._bar is None)
    r2, w2 = await _client(d.socket_path)
    await r2.readline(), await r2.readline()
    await _say(w2, type="hello", client="bar", pid=4400, build="abc1234")
    await _wait(lambda: len(signals.read_events()) >= 4)
    assert [e["kind"] for e in signals.read_events()][2:] == ["hello", "restart"]
    assert signals.read_events()[-1]["was"] == 4321
    # The rects the dead bar made are not the new one's.
    assert signals.read_bar()["screens"] == {} and signals.read_bar()["pid"] == 4400
    w2.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_new_bar_that_says_hello_before_the_old_one_hangs_up_is_one_restart_not_two(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, _, w = await _start(d)
    await _say(w, type="hello", client="bar", pid=1, build="b")
    await _wait(lambda: d._bar is not None)
    r2, w2 = await _client(d.socket_path)
    await r2.readline(), await r2.readline()
    await _say(w2, type="hello", client="bar", pid=2, build="b")
    await _wait(lambda: len(signals.read_events()) >= 3)
    w.close()                                   # the old connection ends after the new hello
    await asyncio.sleep(0.3)
    assert d._bar is not None and d.signals._connected
    assert [e["kind"] for e in signals.read_events()] == ["hello", "hello", "restart"]
    w2.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_client_that_is_not_the_bar_is_not_the_bar(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _say(w, type="hello", client="doctor", pid=9)
    await _say(w, type="ping")
    await r.readline()
    assert d._bar is None and signals.read_events() == []
    w.close()
    await asyncio.sleep(0.1)
    assert signals.read_events() == []          # its hangup is nobody's restart
    server.cancel()


@pytest.mark.asyncio
async def test_a_summon_carries_an_id_the_bar_acks_and_one_nobody_acks_times_out(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server, r, w = await _start(d)
    await _say(w, type="hello", client="bar", pid=1, build="b")
    await _say(w, type="summon")
    await _say(w, type="summon", screen="Virtual-1")
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "summon", "id": 1}
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "summon", "id": 2}
    await _say(w, type="focus_ack", id=1, ms=90)
    await _wait(lambda: any(e["kind"] == "focus_ack" for e in signals.read_events()))
    d.signals.tick(time.time() + 2)
    assert [(e["kind"], e["id"]) for e in signals.read_events() if e["kind"] != "hello"] == [
        ("summon", 1), ("summon", 2), ("focus_ack", 1), ("focus_timeout", 2)]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_serving_says_who_serves_and_the_three_second_loop_ticks(home, monkeypatch):
    real_sleep = asyncio.sleep

    async def quick(delay, *a, **k):
        return await real_sleep(0.05 if delay == 3 else delay, *a, **k)
    monkeypatch.setattr(asyncio, "sleep", quick)
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    ticks = []
    d.signals.tick = lambda now=None: ticks.append(now)
    server, _, w = await _start(d)
    await _wait(lambda: (paths.loop_dir() / "agentd.json").exists())
    info = json.loads((paths.loop_dir() / "agentd.json").read_text())
    assert info["pid"] == os.getpid() and info["socket"] == str(d.socket_path)
    await _wait(lambda: len(ticks) >= 2)
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_unwritable_loop_directory_costs_the_bar_nothing(home, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory should be")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    d.signals = Signals(blocked / "loop")
    server, r, w = await _start(d)
    await _say(w, type="hello", client="bar", pid=1, build="b")
    await _say(w, type="summon")
    assert json.loads(await asyncio.wait_for(r.readline(), 5)) == {"type": "summon", "id": 1}
    await _say(w, type="prompt", text="hello")
    while json.loads(await asyncio.wait_for(r.readline(), 5)).get("kind") != "turn_end":
        pass
    w.close()
    server.cancel()


# -- the loop's service hangs off agentd --

class Service:
    def __init__(self):
        self.seen = []

    def on_row(self, row):
        self.seen.append(("row", row["prompt"]))

    def on_client(self, writer):
        self.seen.append(("client", writer is not None))

    async def on_message(self, msg, writer):
        self.seen.append(("message", msg["type"]))


@pytest.mark.asyncio
async def test_the_service_hears_of_clients_rows_and_messages_agentd_does_not_know(home):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    assert d.loop is None
    d.loop = service = Service()
    server, _, w = await _start(d)
    assert service.seen == [("client", True)]
    await _say(w, type="noticed_state")
    await _say(w, type="prompt", text="hello")
    await _wait(lambda: ("row", "hello") in service.seen)
    assert ("message", "noticed_state") in service.seen
    assert ("message", "ping") not in service.seen          # what agentd handles is not passed on
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_service_that_breaks_or_is_half_written_costs_nothing(home, capsys):
    class Broken:
        def on_row(self, row):
            raise RuntimeError("the service fell over")

        async def on_message(self, msg, writer):
            raise RuntimeError("and so did this")
        # no on_client at all: a service need not listen for everything

    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    d.loop = Broken()
    server, r, w = await _start(d)
    await _say(w, type="noticed_state")
    await _say(w, type="prompt", text="hello")
    while json.loads(await asyncio.wait_for(r.readline(), 5)).get("kind") != "turn_end":
        pass
    await _wait(lambda: paths.turns_log().exists() and paths.turns_log().read_text())
    await _say(w, type="ping")
    while json.loads(await asyncio.wait_for(r.readline(), 5))["type"] != "pong":
        pass
    assert json.loads(paths.turns_log().read_text().splitlines()[0])["prompt"] == "hello"
    err = capsys.readouterr().err
    assert "the service fell over" in err and "and so did this" in err
    w.close()
    server.cancel()

"""LoopService: the `noticed` state, his taps, the words, the app turn and the trail.

Unit tests run the service against a small double of agentd (the same hooks, a recorded inbox per client,
an injected clock). A few run a real AgentD with the fake provider over its socket. Everything lives in
temp dirs (the `home` fixture points every path there), and nothing opens a browser or touches the
network: the issue page's opener and the issue search's fetcher are injected.
"""

import asyncio
import functools
import json
import sqlite3
import sys
import threading
import time
import tomllib
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from bombadil import agentd, apps, launcher, paths, providers
from bombadil.loop import db, findings, probes, refine, report, words
from bombadil.loop import service as service_mod
from bombadil.loop.service import LoopService
from bombadil.loop.store import LoopStore

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
import golden_corpus as gc

DAY = 86400
NOW = gc.epoch(5, "23:00")   # three asks for "my passwords" on three days have been made by now


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Client:
    """One connected client: everything agentd sent it, in order."""

    def __init__(self, name="bar"):
        self.name = name
        self.inbox: list[dict] = []

    def of(self, type_, **match):
        return [m for m in self.inbox if m.get("type") == type_ and all(m.get(k) == v for k, v in match.items())]

    def last(self, type_):
        got = self.of(type_)
        return got[-1] if got else None


class FakeLauncher:
    def __init__(self):
        self.ran: list[tuple] = []

    def run(self, action):
        self.ran.append((action.kind, action.target, action.verb))
        return True, "ok"


class FakeAgent:
    """What the service needs of agentd: clients and a way to send, broadcast, write a ledger row
    (which calls back `on_row`, as the real writer does), start a turn, and say whether a turn runs."""

    def __init__(self):
        self.clients: dict[Client, None] = {}
        self.current = None
        self.pending: list = []
        self.access = "ready"
        self.signals = SimpleNamespace(_connected=True)
        self.launcher = FakeLauncher()
        self.provider = SimpleNamespace(name="fake")
        self.prompts: list[dict] = []
        self.loop = None

    def connect(self, name="bar") -> Client:
        client = Client(name)
        self.clients[client] = None
        return client

    async def _send(self, writer, msg):
        if writer is not None and writer in self.clients:
            writer.inbox.append(json.loads(json.dumps(msg)))

    async def broadcast(self, msg):
        for client in self.clients:
            client.inbox.append(json.loads(json.dumps(msg)))

    def _log_line(self, entry):
        paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
        with paths.turns_log().open("a") as f:
            f.write(json.dumps(entry) + "\n")
        self.loop.on_row(entry)

    async def handle(self, msg, writer):
        self.prompts.append(msg)


def seed(asks):
    """Write the asks (and their per-turn logs) where agentd keeps its ledger."""
    return gc.write_corpus(paths.state_dir(), asks)


def passwords_asks():
    return [a for a in gc.load_asks() if a["t"] <= NOW and a["group"] in ("passwords", "weather", "network")]


def run_asks():
    """Three asks on three days that want one small app: nothing holds it yet."""
    def ask(i, day, text):
        return {"id": f"run{i}", "day": day, "at": "10:00", "text": text, "group": "g", "seconds": 20,
                "changed": True, "events": [f"echo '{i}' >> ~/runs.csv"], "t": gc.epoch(day, "10:00")}
    return [ask(1, 0, "log a 5 km run"), ask(2, 1, "log a 7 km run"), ask(3, 2, "log a 10 km run")]


def make_apps(*names):
    for name in names or ("passwords", "runs", "tracker"):
        apps.create(name.title(), "import QtQuick\nItem {}\n")


@pytest.fixture
def machine(home, monkeypatch):
    """Apps he has, a clean words file, and a trap under everything that would leave the machine: the
    issue search, the browser and the clipboard. A test that means to use one injects its own; any
    other call is recorded and fails the test when it ends (the code under test swallows errors, so
    raising here would not show)."""
    monkeypatch.delenv("BOMBADIL_LOOP", raising=False)
    monkeypatch.setattr(service_mod, "TICK", 3600.0)        # a test moves the clock and calls tick() itself
    make_apps()
    words._cache = None
    touched: list[str] = []

    def trap(name, result):
        def reached(*args, **kwargs):
            touched.append(name)
            return result
        return reached
    monkeypatch.setattr(report, "_get_json", trap("the network", {}))
    monkeypatch.setattr(report, "open_issue_page", trap("the browser", ""))
    monkeypatch.setattr(report, "copy_text", trap("the clipboard", False))
    yield home
    assert touched == [], f"a test reached {touched}"


async def until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


class Rig:
    """A service beside a FakeAgent, started, with one bar connected."""

    def __init__(self, clock=None, bar=True, **kw):
        self.clock = clock or Clock()
        self.agent = FakeAgent()
        self.agent.signals._connected = bar
        self.service = LoopService(self.agent, clock=self.clock, **kw)
        self.service.debounce = 0.02
        self.agent.loop = self.service
        self.opened: list[str] = []
        self.bar = self.agent.connect()

    async def start(self, wait=True):
        self.service.start()
        if wait:
            await self.booted()
        return self

    async def booted(self):
        await until(lambda: self.service._store is not None and self.service._ingested > 0)
        await self.settle()

    async def settle(self):
        """Every task the service has running is done."""
        await asyncio.sleep(0)
        while [t for t in self.service._tasks if not t.done() and t is not self.ticker()]:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.01)

    def ticker(self):
        return next((t for t in self.service._tasks if "_ticker" in repr(t)), None)

    async def ask_state(self):
        await self.service.on_message({"type": "noticed_state"}, self.bar)
        return self.bar.last("noticed")

    async def do(self, op, id="", form=None, client=None):
        client = client or self.bar
        before = len(client.inbox)
        msg = {"type": "noticed_do", "op": op, "id": id}
        if form:
            msg["form"] = form
        await self.service.on_message(msg, client)
        got = [m for m in client.inbox[before:] if m["type"] == "noticed_result"]
        assert len(got) == 1, client.inbox[before:]
        return got[0]

    async def full(self):
        await self.service.on_message({"type": "noticed_list"}, self.bar)
        return self.bar.last("noticed_full")

    def stop(self):
        self.service.stop()

    def store(self):
        """A second connection, from the test's own thread, to read what the service wrote."""
        return LoopStore(paths.loop_db())

    def turns(self, kind=None):
        rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines()] \
            if paths.turns_log().exists() else []
        return [r for r in rows if kind is None or r.get("kind") == kind]


@pytest.fixture
def rig_of(machine):
    made: list[Rig] = []

    def build(asks=None, **kw) -> Rig:
        if asks is not None:
            seed(asks)
        rig = Rig(**kw)
        made.append(rig)
        return rig
    yield build
    for rig in made:
        rig.stop()


def the_offer(state):
    rows = [r for r in state["rows"] if r["kind"] == "offer"]
    return rows[0] if rows else None


# -- the state: connect, idle, an offer --

@pytest.mark.asyncio
async def test_a_client_that_connects_is_told_what_is_noticed_even_when_nothing_is(rig_of):
    rig = rig_of()
    await rig.start()
    await rig.service.on_client(rig.bar)
    assert rig.bar.inbox == [{"type": "noticed", "count": 0, "hidden": False, "resting": "", "lately": "",
                              "rows": []}]


@pytest.mark.asyncio
async def test_three_asks_on_three_days_make_one_offer_row_when_a_bar_is_there_and_nothing_runs(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    state = await rig.ask_state()
    row = the_offer(state)
    assert state["count"] == 1 and state["hidden"] is False and [r["kind"] for r in state["rows"]] == ["offer"]
    said = {a["text"] for a in passwords_asks() if a["group"] == "passwords"}
    assert row["id"] == "gpw1" and row["title"] in said           # his own words, the newest of them
    assert row["meta"] == "3 times on 3 days"
    assert row["what"] == "Say “my passwords” and Passwords opens. No model, under a tenth of a second."
    assert row["primary"] == {"label": "Make the word", "op": "accept", "form": "word"}
    assert row["others"] == [] and row["forms"] == [{"form": "word", "label": "A word", "recommended": True}]
    store = rig.store()
    assert store.waiting() == 1 and store.group("gpw1").state == "offered"
    # The same offer again: nothing new is recorded, and nothing changed, so nobody else is told twice.
    before = len(rig.bar.of("noticed"))
    again = await rig.ask_state()
    assert again == state and len(rig.bar.of("noticed")) == before + 1
    assert store.conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_an_offer_is_fetched_only_while_idle_and_a_bar_is_connected(rig_of):
    rig = rig_of(passwords_asks(), bar=False)
    await rig.start()
    assert (await rig.ask_state())["rows"] == []                 # no bar yet
    assert rig.store().waiting() == 0
    rig.agent.signals._connected = True
    rig.agent.current = 4                                        # a turn runs
    assert (await rig.ask_state())["rows"] == [] and rig.store().waiting() == 0
    rig.agent.current = None
    rig.agent.pending = [(5, "queued")]                          # something is queued
    assert (await rig.ask_state())["rows"] == [] and rig.store().waiting() == 0
    rig.agent.pending = []
    state = await rig.ask_state()                                # idle, a bar: now
    assert the_offer(state) is not None and rig.store().waiting() == 1
    # Once shown it stays in the state while a turn runs: it was recorded when it was shown.
    rig.agent.current = 6
    assert the_offer(await rig.ask_state()) is not None


@pytest.mark.asyncio
async def test_the_state_reaches_every_client_when_it_changes_and_only_then(rig_of):
    rig = rig_of(passwords_asks())
    window = rig.agent.connect("window")
    await rig.start()
    await rig.ask_state()
    assert rig.bar.of("noticed")[-1]["count"] == 1 and window.of("noticed")[-1]["count"] == 1
    told = len(window.of("noticed"))
    await rig.service._refresh()                                 # nothing changed: nobody is told again
    assert len(window.of("noticed")) == told
    await rig.do("not_now", "gpw1")
    assert window.of("noticed")[-1]["count"] == 0 and len(window.of("noticed")) == told + 1


@pytest.mark.asyncio
async def test_an_offer_for_an_app_says_make_an_app(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    assert row["title"] == "log a 10 km run" and row["primary"] == {"label": "Make an app", "op": "accept",
                                                                     "form": "app"}
    assert len(row["primary"]["label"].split()) <= 3


@pytest.mark.asyncio
async def test_an_offer_that_only_says_the_thing_already_opens_is_got_it(rig_of):
    asks = [a for a in gc.load_asks() if a["group"] == "open-browser" and a["t"] <= gc.epoch(11, "23:30")]
    rig = rig_of(asks, clock=Clock(gc.epoch(11, "23:30")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    assert row["primary"]["label"] == "Got it" and row["primary"]["op"] == "got_it" and row["others"] == []
    result = await rig.do("got_it", row["id"])
    assert result["ok"] and rig.store().group(row["id"]).state == "got_it"
    assert (await rig.ask_state())["rows"] == []


# -- accepting a word --

def words_file():
    path = paths.words_file()
    return tomllib.loads(path.read_text()).get("word", []) if path.exists() else []


@pytest.mark.asyncio
async def test_accepting_a_word_makes_it_writes_the_trail_through_agentd_and_hands_the_pill_an_undo(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    result = await rig.do("accept", "gpw1", "word")
    assert result == {"type": "noticed_result", "op": "accept", "id": "gpw1", "ok": True,
                      "text": "Made “my passwords” open Passwords."}
    [word] = words_file()
    assert (word["phrase"], word["opens"], word["from_group"], word["away"]) == (
        "my passwords", {"kind": "app", "name": "passwords"}, "gpw1", False)
    assert launcher.match("my passwords", apps.list_apps()).word == "my passwords"
    # The store knows: the group is made, the word counts from now, nothing waits.
    store = rig.store()
    assert store.group("gpw1").state == "made" and store.waiting() == 0
    assert "my passwords" in store.words_last_used()
    # The trail is one improve row, written by agentd's own writer.
    [row] = rig.turns("improve")
    assert row["what"] == "word" and row["title"] == "Made “my passwords” open Passwords."
    assert row["group"] == "gpw1" and row["undo"] == {"op": "remove_word", "phrase": "my passwords",
                              "opens": {"kind": "app", "name": "passwords"}}
    assert row["undone"] is False and row["v"] == 2 and row["id"].startswith("i") and "of" not in row
    # The line above the pill is the receipt, to the one who tapped, with an Undo that comes back here.
    [line] = [m for m in rig.bar.inbox if m["type"] == "event" and m.get("kind") == "local"]
    assert line["text"] == row["title"] and line["ok"] is True and line["turn"] is None
    assert line["undo_msg"] == {"type": "noticed_do", "op": "undo", "id": row["id"]}
    # The chip is gone, and the Lately line counts the change.
    state = rig.bar.last("noticed")
    assert state["count"] == 0 and state["rows"] == [] and state["lately"] == "1 change this week"


@pytest.mark.asyncio
async def test_the_receipt_goes_only_to_the_client_that_tapped(rig_of):
    rig = rig_of(passwords_asks())
    window = rig.agent.connect("window")
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    assert [m for m in window.inbox if m["type"] == "event"] == []
    assert window.last("noticed")["count"] == 0          # but everyone hears the state changed
    assert window.of("noticed_result") == []


@pytest.mark.asyncio
async def test_undo_takes_the_word_out_answers_not_now_and_leaves_the_trail_honest(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    [made] = rig.turns("improve")
    undone = await rig.do("undo", made["id"])
    assert undone["ok"] and undone["text"] == "Took out the word “my passwords”."
    assert words_file() == [] and launcher.match("my passwords", apps.list_apps()) is None
    assert rig.store().group("gpw1").state == "not_now"
    rows = rig.turns("improve")
    assert [r["title"] for r in rows][-1] == "Took out the word “my passwords”."
    assert rows[-1]["of"] == made["id"] and rows[-1]["undone"] is True and rows[-1]["what"] == "word"
    # The receipt of the undo says so above the pill, and is not itself undoable.
    last = [m for m in rig.bar.inbox if m["type"] == "event"][-1]
    assert last["text"] == "Took out the word “my passwords”." and "undo_msg" not in last
    # Again: already undone, and nothing is written twice.
    again = await rig.do("undo", made["id"])
    assert again["ok"] and again["text"] == "That is already undone." and len(rig.turns("improve")) == 2
    full = await rig.full()
    assert full["changes"] == [{"id": made["id"], "title": made["title"], "t": made["t"], "what": "word",
                                "undone": True, "can_undo": False}]
    assert rig.bar.last("noticed")["lately"] == ""
    missing = await rig.do("undo", "i0-0")
    assert missing["ok"] is False and "cannot be found" in missing["text"]


@pytest.mark.asyncio
async def test_a_word_that_already_means_something_is_not_made_and_the_offer_becomes_got_it(rig_of, monkeypatch):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    monkeypatch.setattr(launcher, "means", lambda text, app_list=None: "an app")   # it has a meaning by now
    result = await rig.do("accept", "gpw1", "word")
    assert result["ok"] is False
    assert result["text"] == "“my passwords” already means something on this computer, so nothing was made."
    assert words_file() == [] and rig.turns("improve") == []
    assert rig.store().group("gpw1").state == "got_it"
    assert [m for m in rig.bar.inbox if m["type"] == "event"] == []


@pytest.mark.asyncio
async def test_a_word_is_not_made_twice_or_for_an_app_that_is_gone(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    import shutil
    shutil.rmtree(paths.apps_dir() / "passwords")
    gone = await rig.do("accept", "gpw1")
    assert gone["ok"] is False and gone["text"] == "That app is not here any more, so nothing was made."
    assert words_file() == [] and rig.store().group("gpw1").state == "offered"
    make_apps("passwords")
    assert (await rig.do("accept", "gpw1"))["ok"]
    twice = await rig.do("accept", "gpw1")
    assert twice["ok"] and twice["text"] == "That is already made." and len(words_file()) == 1
    nothing = await rig.do("accept", "gnope")
    assert nothing["ok"] is False and nothing["text"] == "That is not on the list any more."


@pytest.mark.asyncio
async def test_accept_with_a_form_that_cannot_be_made_here_changes_nothing(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    for form in ("routine", "widget", "E"):
        res = await rig.do("accept", "gpw1", form)
        assert res["ok"] is False and res["text"] == "That way of doing it cannot be made here yet."
    odd = await rig.do("accept", "gpw1", "nonsense")
    assert odd["ok"] is False and "does not know" in odd["text"]
    assert words_file() == [] and rig.store().group("gpw1").state == "offered"


# -- not now, never, got it: kept across a restart --

@pytest.mark.asyncio
async def test_never_is_remembered_across_a_restart_and_can_be_brought_back(rig_of):
    asks = [a for a in gc.load_asks() if a["t"] <= gc.epoch(6, "23:30")]
    rig = rig_of(asks, clock=Clock(gc.epoch(6, "23:30")))
    await rig.start()
    first = the_offer(await rig.ask_state())
    never = await rig.do("never", first["id"])
    assert never["ok"] and never["text"] == "Okay. That will not be offered again."
    rig.stop()
    # A new service, the same loop.db: the group is still said no to, and listed so he can bring it back.
    again = await rig_of(clock=Clock(gc.epoch(6, "23:30"))).start()
    full = await again.full()
    assert [s["id"] for s in full["said_no"]] == [first["id"]] and full["said_no"][0]["form"] == "A word"
    assert full["said_no"][0]["title"] == LoopStore(paths.loop_db()).said_no()[0]["sentence"]
    assert all(r["id"] != first["id"] for r in (await again.ask_state())["rows"])
    back = await again.do("bring_back", first["id"])
    assert back["ok"] and back["text"] == "Okay. That can come up again."
    assert (await again.full())["said_no"] == []


@pytest.mark.asyncio
async def test_not_now_hides_the_group_until_it_doubles_and_survives_a_restart(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    row = the_offer(await rig.ask_state())
    said = await rig.do("not_now", row["id"])
    assert said["ok"] and said["text"] == "Okay. That will not come up again for a while."
    assert rig.store().group(row["id"]).state == "not_now"
    rig.stop()
    again = await rig_of().start()
    assert (await again.ask_state())["rows"] == []
    assert (await again.full())["asks"][0]["state"] == "not_now"
    unknown = await again.do("not_now", "gnothing")
    assert unknown["ok"] is False and unknown["text"] == "That is not on the list any more."


# -- accepting an app: an ordinary turn, asked on his tap --

async def make_the_app(rig):
    """Accept the app offer, let the turn make an app, and give the service the turn's row.
    Returns (the offer's id, the trail row the service wrote)."""
    row = the_offer(await rig.ask_state())
    got = await rig.do("accept", row["id"], "app")
    assert got["ok"], got
    [sent] = rig.agent.prompts
    apps.create("Run log", "import QtQuick\nItem {}\n")
    rig.agent._log_line({"t": rig.clock(), "id": "t1-1", "prompt": sent["text"], "result": "Made Run log.",
                         "ok": True, "origin": "loop", "v": 2})
    await until(lambda: rig.turns("improve"))
    return row["id"], rig.turns("improve")[0]


@pytest.mark.asyncio
async def test_accepting_an_app_asks_one_ordinary_turn_and_the_app_it_makes_goes_in_the_trail(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    counted = rig.store().conn.execute("SELECT COUNT(*) FROM requests WHERE counted=1").fetchone()[0]
    got = await rig.do("accept", row["id"], "app")
    assert got["ok"] and got["text"] == "Making a small app for it now. It will say what it made when it is done."
    [sent] = rig.agent.prompts
    assert sent["type"] == "prompt" and sent["origin"] == "loop"
    for said in ("log a 5 km run", "log a 7 km run", "log a 10 km run"):      # his own words, as he typed them
        assert said in sent["text"]
    assert "3 times on 3 days" in sent["text"] and "app kit" in sent["text"]
    assert rig.store().group(row["id"]).state == "made" and rig.turns("improve") == []   # nothing yet changed
    made = apps.create("Run log", "import QtQuick\nItem {}\n")
    assert made.name == "run-log"
    rig.agent._log_line({"t": rig.clock(), "id": "t1-1", "prompt": sent["text"], "result": "Made Run log.",
                         "ok": True, "origin": "loop", "v": 2})
    await until(lambda: rig.turns("improve"))
    [change] = rig.turns("improve")
    assert change["what"] == "app" and change["group"] == row["id"] and change["undone"] is False
    assert change["title"].startswith("Made the app Run log from “log a ")
    assert change["undo"] == {"op": "trash_app", "name": "run-log"}
    await rig.settle()
    assert rig.bar.last("noticed")["lately"] == "1 change this week"
    # The turn that made it is not one more ask: the loop's own request is never counted.
    await until(lambda: rig.service._debounce_handle is None)
    await rig.settle()
    assert rig.store().conn.execute("SELECT COUNT(*) FROM requests WHERE counted=1").fetchone()[0] == counted
    # A turn that is not the one a tap started (another origin, or other words) writes nothing.
    rig.agent._log_line({"t": rig.clock(), "id": "t2-1", "prompt": "make me a thing", "ok": True,
                         "origin": "loop", "v": 2})
    await rig.settle()
    assert len(rig.turns("improve")) == 1


@pytest.mark.asyncio
async def test_an_app_that_was_not_made_puts_the_ask_back_to_not_now(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    await rig.do("accept", row["id"], "app")
    [sent] = rig.agent.prompts
    assert rig.store().group(row["id"]).state == "made"
    rig.agent._log_line({"t": rig.clock(), "id": "t1-1", "prompt": sent["text"], "result": "It did not work.",
                         "ok": False, "origin": "loop", "v": 2})
    await until(lambda: rig.store().group(row["id"]).state == "not_now")
    assert rig.turns("improve") == []


@pytest.mark.asyncio
async def test_no_app_is_asked_for_while_the_ai_is_not_signed_in(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    rig.agent.access = "signed_out"
    got = await rig.do("accept", row["id"], "app")
    assert got["ok"] is False and "not signed in" in got["text"]
    assert rig.agent.prompts == [] and rig.store().group(row["id"]).state == "offered"


@pytest.mark.asyncio
async def test_undo_puts_the_app_in_the_trash_and_bring_back_brings_it_back(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    group, change = await make_the_app(rig)
    undone = await rig.do("undo", change["id"])
    assert undone == {"type": "noticed_result", "op": "undo", "id": change["id"], "ok": True,
                      "text": "Put the app Run log away."}
    assert "run-log" not in [a.name for a in apps.list_apps()] and ("app", "run-log", "close") in rig.agent.launcher.ran
    [trashed] = list((paths.loop_dir() / "trash").iterdir())
    assert trashed.name.startswith("run-log-") and (trashed / "main.qml").exists()
    assert rig.store().group(group).state == "not_now"
    last = rig.turns("improve")[-1]
    assert last["of"] == change["id"] and last["undone"] is True and last["trash"] == str(trashed)
    assert rig.bar.last("noticed")["lately"] == ""
    # Bring it back: the same folder, and the change stands again (so it can be undone again).
    back = await rig.do("bring_back", change["id"])
    assert back["ok"] and back["text"] == "Brought the app Run log back."
    assert "run-log" in [a.name for a in apps.list_apps()] and list((paths.loop_dir() / "trash").iterdir()) == []
    [full_change] = (await rig.full())["changes"]
    assert full_change["undone"] is False and full_change["can_undo"] is True
    assert rig.bar.last("noticed")["lately"] == "1 change this week"
    again = await rig.do("bring_back", change["id"])
    assert again["ok"] is False and "was not put away" in again["text"]
    assert (await rig.do("undo", change["id"]))["ok"]         # and once more


@pytest.mark.asyncio
async def test_an_app_is_not_brought_back_over_one_with_its_name(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    _, change = await make_the_app(rig)
    await rig.do("undo", change["id"])
    apps.create("Run log", "import QtQuick\nRectangle {}\n")          # he made another meanwhile
    back = await rig.do("bring_back", change["id"])
    assert back["ok"] is False and back["text"] == (
        "There is an app with that name already, so the old one stays in the trash.")
    assert "Rectangle" in (paths.apps_dir() / "run-log" / "main.qml").read_text()
    assert len(list((paths.loop_dir() / "trash").iterdir())) == 1


@pytest.mark.asyncio
async def test_a_word_he_took_out_comes_back_with_put_it_back_and_one_that_stands_does_not(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    [made] = rig.turns("improve")
    standing = await rig.do("bring_back", made["id"])
    assert standing["ok"] is False and "nothing to bring back" in standing["text"]
    await rig.do("undo", made["id"])
    assert words_file() == []
    back = await rig.do("bring_back", made["id"])
    assert back["ok"] and back["text"] == "Made “my passwords” open Passwords again."
    assert [w["phrase"] for w in words_file()] == ["my passwords"]
    rows = rig.turns("improve")
    assert rows[-1]["of"] == made["id"] and rows[-1]["undone"] is False and rows[-1]["what"] == "word"
    [change] = (await rig.full())["changes"]
    assert change["undone"] is False and change["can_undo"] is True
    # And it can be taken out again.
    again = await rig.do("undo", made["id"])
    assert again["ok"] and words_file() == []


@pytest.mark.asyncio
async def test_an_ask_waiting_on_him_carries_its_button_what_it_does_and_the_other_ways_in_the_window_list(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()                                            # the offer is made: the group is "offered"
    full = await rig.full()
    mine = next(a for a in full["asks"] if a["id"] == "gpw1")
    assert mine["state"] == "offered"
    assert mine["primary"] == {"label": "Make the word", "op": "accept", "form": "word"}
    assert mine["what"] and isinstance(mine["others"], list)
    await rig.do("accept", "gpw1")
    mine = next(a for a in (await rig.full())["asks"] if a["id"] == "gpw1")
    assert mine["state"] == "made" and "primary" not in mine and "what" not in mine


@pytest.mark.asyncio
async def test_a_found_row_in_the_window_list_carries_what_was_expected_and_what_was_seen(rig_of):
    rig = rig_of()
    found = plant()
    await rig.start()
    [entry] = (await rig.full())["found"]
    assert entry["id"] == found.fp
    assert entry["why"] == [f"Expected: {found.expected}", f"Seen: {found.observed}"]


@pytest.mark.asyncio
async def test_an_undo_never_moves_anything_the_trail_does_not_name_inside_the_trash_or_his_apps(rig_of):
    # Rows the service did not write (the file is his, and anyone can add a line to it).
    rows = [
        {"t": NOW - 30, "kind": "improve", "id": "ix-1", "what": "app", "title": "Made the app Evil.", "group": None,
         "undo": {"op": "trash_app", "name": "../../etc"}, "undone": False, "v": 2},
        {"t": NOW - 20, "kind": "improve", "id": "ix-2", "what": "app", "title": "Made a thing.", "group": None,
         "undo": {"op": "rm -rf", "name": "x"}, "undone": False, "v": 2},
        {"t": NOW - 10, "kind": "improve", "id": "ix-3", "what": "app", "title": "Made the app Runs.", "group": None,
         "undo": {"op": "trash_app", "name": "runs"}, "undone": False, "v": 2},
        {"t": NOW - 5, "kind": "improve", "id": "ix-4", "what": "app", "title": "Put the app Runs away.",
         "group": None, "of": "ix-3", "undone": True, "trash": str(paths.state_dir().parent), "name": "runs", "v": 2},
    ]
    paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
    paths.turns_log().write_text("{not json\n" + "\n".join(json.dumps(r) for r in rows) + "\n"
                                 + json.dumps({"kind": "improve", "t": 1}) + "\n" + json.dumps([1, 2]) + "\n")
    rig = rig_of()
    await rig.start()
    full = await rig.full()
    by_id = {c["id"]: c for c in full["changes"]}
    assert list(by_id) == ["ix-3", "ix-2", "ix-1"]              # newest first; the answer row is not a change
    assert by_id["ix-2"]["can_undo"] is False and by_id["ix-3"]["undone"] is True
    bad_name = await rig.do("undo", "ix-1")
    assert bad_name["ok"] is False and "there is nothing to put away" in bad_name["text"]
    unknown = await rig.do("undo", "ix-2")
    assert unknown["ok"] is False and unknown["text"] == "That change cannot be taken back from here."
    away = await rig.do("bring_back", "ix-3")                   # its "trash" is not inside the trash folder
    assert away["ok"] is False and away["text"] == "That app is no longer in the trash."
    assert (paths.apps_dir() / "runs" / "main.qml").exists() and paths.state_dir().exists()
    assert rig.agent.launcher.ran == []


# -- preview --

@pytest.mark.asyncio
async def test_a_preview_shows_what_it_would_do_and_changes_nothing(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    row = the_offer(await rig.ask_state())
    got = await rig.do("preview", row["id"], "word")
    assert got["ok"] and got["text"] == "This is what it would do."
    assert got["preview"].startswith("You said: “") and "Nothing else changes, and Undo puts it back." in got["preview"]
    assert rig.store().group(row["id"]).state == "offered" and words_file() == [] and rig.turns("improve") == []
    assert (await rig.do("preview", row["id"]))["ok"]                  # the recommended form, when none is named
    nothing = await rig.do("preview", "gnope")
    assert nothing["ok"] is False and nothing["text"] == "There is nothing to show for that."
    assert (await rig.do("preview", row["id"], "nonsense"))["ok"] is False


# -- hiding noticed, and the word --

@pytest.mark.asyncio
async def test_hiding_noticed_stops_the_offers_survives_a_restart_and_showing_brings_them_back(rig_of):
    rig = rig_of(passwords_asks(), bar=False)            # no bar yet: nothing has been offered
    await rig.start()
    assert await rig.service.noticed_word("hide") == (True, "Noticed is hidden. Say “show noticed” to bring it back.")
    assert rig.bar.last("noticed") == {"type": "noticed", "count": 0, "hidden": True, "resting": "", "lately": "",
                                       "rows": []}
    rig.agent.signals._connected = True
    assert (await rig.ask_state())["rows"] == [] and rig.store().waiting() == 0    # so nothing is recorded as shown
    full = await rig.full()
    assert full["hidden"] is True and full["held"] is True and full["asks"]      # counting goes on
    rig.stop()
    again = await rig_of().start()
    assert (await again.ask_state())["hidden"] is True
    assert await again.service.noticed_word("show") == (True, "Opened Noticed.")
    assert again.bar.of("noticed_open") == [{"type": "noticed_open"}]
    state = await again.ask_state()
    assert state["hidden"] is False and the_offer(state) is not None


@pytest.mark.asyncio
async def test_the_hide_and_show_taps_do_the_same_and_say_so(rig_of):
    rig = rig_of(passwords_asks())
    window = rig.agent.connect("window")
    await rig.start()
    hid = await rig.do("hide")
    assert hid["ok"] and hid["text"] == "Noticed is hidden. Say “show noticed” to bring it back."
    assert window.last("noticed")["hidden"] is True                  # everyone hears
    shown = await rig.do("show")
    assert shown["ok"] and shown["text"] == "Noticed is back." and window.of("noticed_open")
    assert window.last("noticed")["hidden"] is False


@pytest.mark.asyncio
async def test_the_word_opens_the_noticed_app_when_it_is_here_and_quietly_does_not_when_it_is_not(rig_of):
    rig = rig_of()
    await rig.start()
    assert await rig.service.noticed_word("open") == (True, "Opened Noticed.")
    assert rig.agent.launcher.ran == []                              # no app of that name here
    apps.create("Noticed", "import QtQuick\nItem {}\n")
    assert await rig.service.noticed_word("open") == (True, "Opened Noticed.")
    assert rig.agent.launcher.ran == [("app", "noticed", "open")]
    opened = await rig.do("open")
    assert opened["ok"] and opened["text"] == "Opened the Noticed window."
    assert rig.agent.launcher.ran[-1] == ("app", "noticed", "open")


# -- what it found: a report he reads, and sends himself --

def a_finding(kind="event", id="bar-socket", component="bar", observed="the bar lost its socket", at=None):
    return probes.Result(False, id, component, id, "the bar keeps its socket", observed, {}, None, kind,
                         "The bar lost its connection to Bombadil.", at)


def plant(result=None, now=NOW, **context):
    """A counted finding in loop.db, written from the test's own connection, as the prober would."""
    store = findings.FindingsStore(db.connect(paths.loop_db()))
    try:
        found = store.record(result or a_finding(), now, **context)
    finally:
        store.close()
    assert found is not None
    return found


def finding_state(fp):
    store = findings.FindingsStore(db.connect(paths.loop_db()))
    try:
        return [f.state for f in store.all() if f.fp == fp]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_finding_becomes_a_row_a_held_report_and_an_issue_page_he_submits_himself(rig_of):
    opened, asked = [], []
    rig = rig_of(opener=lambda url: opened.append(url) or "panel",
                 fetcher=lambda url, timeout: asked.append(url) or {"items": []})
    found = plant()
    await rig.start()
    [row] = (await rig.ask_state())["rows"]
    assert row["kind"] == "found" and row["id"] == found.fp == row["fp"]
    assert row["title"] == "The bar lost its connection to Bombadil." and row["meta"] == "1 time on 1 day"
    assert row["primary"] == {"label": "Send to the project", "op": "report"}
    assert row["what"] == "Bombadil cannot fix this here. You can send it to the project."
    assert asked == [] and opened == []                           # nothing is asked or sent until he says so
    # See the report: it is held on this computer, and what is shown is what is held.
    seen = await rig.do("report", found.fp)
    assert seen["ok"] and seen["text"] == "The report is ready. Nothing is sent until you press Submit on the page."
    assert set(seen["preview"]) == {"goes", "stays", "text"} and seen["preview"]["goes"] and seen["preview"]["stays"]
    held = paths.loop_dir() / "reports" / f"{findings.evidence_dir(found.fp).name}.md"
    assert held.read_text() == seen["preview"]["text"] and opened == [] and asked == []
    [row] = rig.bar.last("noticed")["rows"]
    assert row["kind"] == "report" and row["primary"] == {"label": "See the report", "op": "report"}
    assert row["what"] == "It is held on this computer. Nothing is sent until you press Submit."
    [entry] = (await rig.full())["found"]
    assert entry["state"] == "reported" and entry["can_send"] is True and entry["preview"] == seen["preview"]
    # Send: the project's issues are searched for this problem, then its page opens with the report filled in.
    sent = await rig.do("send", found.fp)
    assert sent["ok"] and sent["text"] == (
        "The issue page is open with the report filled in. Press Submit there if it looks right.")
    [url] = opened
    assert url.startswith("https://github.com/") and "issues/new" in url and "found-by-bombadil" in url
    [search] = asked
    assert search.startswith("https://api.github.com/search/issues") and found.fp.rsplit(":", 1)[-1] in search
    assert finding_state(found.fp) == ["sent"] and rig.bar.last("noticed")["rows"] == []
    [entry] = (await rig.full())["found"]
    assert entry["state"] == "sent" and entry["can_send"] is False and "preview" not in entry
    again = await rig.do("send", found.fp)                           # it is sent: not again
    assert again["ok"] is False and again["text"] == "Look at the report first, then send it." and len(opened) == 1


@pytest.mark.asyncio
async def test_a_problem_the_project_already_has_is_not_sent_again(rig_of):
    opened = []
    found = plant()
    tag = f"[fp {found.fp.rsplit(':', 1)[-1]}]"
    rig = rig_of(opener=lambda url: opened.append(url) or "panel",
                 fetcher=lambda url, timeout: {"items": [{"number": 42, "state": "open", "title": f"Bar {tag}"}]})
    await rig.start()
    await rig.ask_state()
    await rig.do("report", found.fp)
    sent = await rig.do("send", found.fp)
    assert sent["ok"] and sent["text"] == "Already reported (#42). Nothing more was sent."
    assert opened == [] and finding_state(found.fp) == ["sent"]


@pytest.mark.asyncio
async def test_a_page_that_will_not_open_leaves_the_report_held_and_he_can_try_again(rig_of):
    answers = ["", "panel"]
    opened = []
    rig = rig_of(opener=lambda url: opened.append(url) or answers.pop(0), fetcher=lambda url, timeout: {"items": []})
    found = plant()
    await rig.start()
    await rig.ask_state()
    before = await rig.do("send", found.fp)                          # no report looked at yet
    assert before["ok"] is False and before["text"] == "Look at the report first, then send it."
    assert opened == []
    await rig.do("report", found.fp)
    failed = await rig.do("send", found.fp)
    assert failed["ok"] is False and failed["text"] == (
        "The issue page would not open. The report is still held on this computer.")
    assert finding_state(found.fp) == ["reported"]
    assert (await rig.do("send", found.fp))["ok"] and len(opened) == 2 and finding_state(found.fp) == ["sent"]
    missing = await rig.do("send", "bar:nothing:000000")
    assert missing["ok"] is False and missing["text"] == "Look at the report first, then send it."
    gone = await rig.do("report", "bar:nothing:000000")
    assert gone["ok"] is False and gone["text"] == "That is not in the list of things it found any more."


@pytest.mark.asyncio
async def test_a_report_too_long_for_a_link_opens_an_empty_page_and_goes_to_the_clipboard(rig_of, monkeypatch):
    opened, copied = [], []
    monkeypatch.setattr(report, "MAX_URL", 200)
    monkeypatch.setattr(report, "copy_text", lambda text, timeout=3.0: copied.append(text) or True)
    rig = rig_of(opener=lambda url: opened.append(url) or "panel", fetcher=lambda url, timeout: {"items": []})
    found = plant()
    await rig.start()
    await rig.ask_state()
    shown = (await rig.do("report", found.fp))["preview"]
    sent = await rig.do("send", found.fp)
    assert sent["ok"] and sent["text"] == (
        "The issue page is open. It was too long for a link, so it is on the clipboard: paste it into the page.")
    assert opened == ["https://github.com/thedefaultman/Bombadil/issues/new"] and copied == [shown["text"]]


@pytest.mark.asyncio
async def test_without_a_clipboard_the_card_does_not_promise_a_paste(rig_of, monkeypatch):
    monkeypatch.setattr(report, "MAX_URL", 200)
    monkeypatch.setattr(report, "copy_text", lambda text, timeout=3.0: False)      # no wl-copy here
    rig = rig_of(opener=lambda url: "panel", fetcher=lambda url, timeout: {"items": []})
    found = plant()
    await rig.start()
    await rig.ask_state()
    await rig.do("report", found.fp)
    sent = await rig.do("send", found.fp)
    assert sent["ok"] and "so the page is empty" in sent["text"] and "held on this computer" in sent["text"]
    assert "paste" not in sent["text"]


@pytest.mark.asyncio
async def test_not_now_never_and_got_it_on_a_finding_keep_it_from_coming_back(rig_of):
    rig = rig_of()
    a = plant(a_finding(id="bar-socket"))
    b = plant(a_finding(id="pill-stuck", observed="the pill did not clear"))
    c = plant(a_finding(id="desk-empty", observed="the desk has no cards"))
    await rig.start()
    state = await rig.ask_state()
    assert state["count"] == 3 and {r["id"] for r in state["rows"]} == {a.fp, b.fp, c.fp}
    assert (await rig.do("not_now", a.fp))["ok"] and (await rig.do("never", b.fp))["ok"]
    assert (await rig.do("got_it", c.fp))["ok"]
    assert [finding_state(x.fp) for x in (a, b, c)] == [["dismissed"], ["never"], ["dismissed"]]
    assert (await rig.ask_state())["rows"] == []
    rig.stop()
    again = await rig_of().start()
    assert (await again.ask_state())["rows"] == []


@pytest.mark.asyncio
async def test_clear_what_it_found_drops_findings_and_held_reports_but_not_a_no(rig_of):
    rig = rig_of()
    kept = plant(a_finding(id="pill-stuck", observed="the pill did not clear"))
    gone = plant()
    await rig.start()
    await rig.ask_state()
    await rig.do("never", kept.fp)
    await rig.do("report", gone.fp)
    held = paths.loop_dir() / "reports" / f"{findings.evidence_dir(gone.fp).name}.md"
    assert held.exists() and findings.evidence_dir(gone.fp).exists()
    cleared = await rig.do("clear_found")
    assert cleared["ok"] and cleared["text"] == (
        "Cleared what it found. A problem that is still there will be found again.")
    assert not held.exists() and not findings.evidence_dir(gone.fp).exists()
    assert rig.bar.last("noticed")["rows"] == [] and finding_state(gone.fp) == []
    store = findings.FindingsStore(db.connect(paths.loop_db()))
    assert [f.fp for f in store.all(("never",))] == [kept.fp]      # still his no
    store.close()


# -- forgetting --

@pytest.mark.asyncio
async def test_forget_what_i_asked_empties_the_counts_and_keeps_the_words_made_from_them(rig_of, monkeypatch):
    forgot = []
    monkeypatch.setattr(refine, "forget", lambda: forgot.append(threading.get_ident()))
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    assert rig.store().asks_report(20, NOW)
    got = await rig.do("forget_asks")
    assert got["ok"] and got["text"] == "Forgot what you asked. The words made from it stay."
    assert rig.store().asks_report(20, NOW) == [] and len(words_file()) == 1
    assert (await rig.full())["asks"] == [] and rig.bar.last("noticed")["rows"] == []
    assert len(forgot) == 1 and forgot[0] != threading.get_ident()      # his pinned model answers too, off the loop


# -- privacy --

@pytest.mark.asyncio
async def test_nothing_of_his_reaches_a_found_row_a_report_a_link_or_stderr_except_the_offer_he_is_meant_to_see(
        rig_of, capsys, monkeypatch):
    his_words = "zq-plum-" + "7781"
    asks = [dict(a, text=f"{a['text']} {his_words}") for a in passwords_asks()]
    opened = []
    rig = rig_of(asks, opener=lambda url: opened.append(url) or "panel",
                 fetcher=lambda url, timeout: {"items": []})
    found = plant(a_finding(observed=f"the bar lost its socket after {his_words}"),
                  turn={"prompt": his_words, "t": NOW, "ok": False, "result": his_words},
                  log=[f"pill: {his_words} is still there", "bar: socket closed"])
    await rig.start()
    state = await rig.ask_state()
    offer = the_offer(state)
    assert his_words in json.dumps(offer)                         # the one place it is meant to be: what he asked
    await rig.do("report", found.fp)
    await rig.do("send", found.fp)
    await rig.full()
    # A step that breaks with his words in its message: the line says what broke, not what he said.
    real = LoopStore.resting
    monkeypatch.setattr(LoopStore, "resting", lambda self, now=None: (_ for _ in ()).throw(ValueError(his_words)))
    await rig.service._refresh()
    monkeypatch.setattr(LoopStore, "resting", real)
    await rig.settle()
    everything_else = []
    for m in rig.bar.inbox:
        if m["type"] == "noticed":
            everything_else += [r for r in m["rows"] if r["kind"] != "offer"]
        elif m["type"] == "noticed_full":
            everything_else += [m["found"], m["changes"], m["words"]]
        elif m["type"] == "noticed_result":
            everything_else.append(m)
    blob = json.dumps(everything_else)
    assert "found" in blob and "goes" in blob and his_words not in blob
    assert his_words not in "".join(urllib.parse.unquote(u) for u in opened) and len(opened) == 1
    for path in (paths.loop_dir() / "reports", findings.evidence_dir(found.fp)):
        for file in path.rglob("*"):
            assert file.is_dir() or his_words not in file.read_text(), file
    err = capsys.readouterr().err
    assert "loop: state: ValueError" in err and his_words not in err


# -- cadence: what is done when, on an injected clock --

def spy(monkeypatch, cls, name, seen: list):
    """Record each call to `cls.name` with the thread it ran on."""
    real = getattr(cls, name)

    @functools.wraps(real)
    def wrapper(self, *args, **kwargs):
        seen.append(threading.get_ident())
        return real(self, *args, **kwargs)
    monkeypatch.setattr(cls, name, wrapper)


@pytest.mark.asyncio
async def test_turns_are_read_at_start_after_a_burst_of_rows_and_every_ten_minutes(rig_of, monkeypatch):
    reads: list[int] = []
    spy(monkeypatch, LoopStore, "ingest", reads)
    rig = rig_of(passwords_asks())
    await rig.start()
    assert len(reads) == 1                                        # catching up with what agentd missed
    for i in range(6):                                            # a burst of finished rows is one look
        rig.agent._log_line({"t": NOW + i, "kind": "local", "action": "panel", "verb": "open", "ok": True})
    await until(lambda: len(reads) == 2)
    await asyncio.sleep(0.15)
    await rig.settle()
    assert len(reads) == 2
    rig.clock.advance(599)
    await rig.service.tick()
    assert len(reads) == 2                                        # not yet ten minutes
    rig.clock.advance(2)
    await rig.service.tick()
    assert len(reads) == 3
    await rig.service.tick()
    assert len(reads) == 3                                        # and then not on every tick
    rig.clock.advance(-3600)                                      # the clock was set back: look, then carry on
    await rig.service.tick()
    assert len(reads) == 4
    await rig.service.tick()
    assert len(reads) == 4


@pytest.mark.asyncio
async def test_the_findings_are_read_every_twenty_seconds_only_while_a_client_is_there(rig_of, monkeypatch):
    polls: list[int] = []
    spy(monkeypatch, findings.FindingsStore, "open_findings", polls)
    rig = rig_of()
    await rig.start()
    base = len(polls)
    rig.clock.advance(19)
    await rig.service.tick()
    assert len(polls) == base
    rig.clock.advance(2)
    await rig.service.tick()
    assert len(polls) == base + 1
    rig.agent.clients.clear()                                      # nobody to tell
    rig.clock.advance(300)
    await rig.service.tick()
    assert len(polls) == base + 1
    # What the prober found in the meantime reaches the bar's chip within the half minute.
    plant(now=rig.clock())
    rig.agent.clients[rig.bar] = None
    rig.clock.advance(21)
    await rig.service.tick()
    assert [r["kind"] for r in rig.bar.last("noticed")["rows"]] == ["found"]


@pytest.mark.asyncio
async def test_a_word_nobody_said_for_28_days_is_put_away_once_a_day_and_his_undo_brings_it_back(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    await rig.service.tick()
    rig.clock.advance(27 * DAY)
    await rig.service.tick()
    assert words_file()[0]["away"] is False                       # 27 days: it stays
    rig.clock.advance(2 * DAY)
    await rig.service.tick()
    [word] = words_file()
    assert word["away"] is True and launcher.match("my passwords", apps.list_apps()) is None
    put_away = rig.turns("improve")[-1]
    assert put_away["title"] == "Put the word “my passwords” away: it was not used for 28 days."
    assert put_away["what"] == "word" and put_away["undo"] == {"op": "bring_back_word", "phrase": "my passwords"}
    count = len(rig.turns("improve"))
    await rig.service.tick()
    rig.clock.advance(3600)
    await rig.service.tick()
    assert len(rig.turns("improve")) == count                     # once a day, not on every tick
    # The window lists it as a change he can take back.
    full = await rig.full()
    assert full["changes"][0]["id"] == put_away["id"] and full["changes"][0]["can_undo"] is True
    assert full["words"] == [{"phrase": "my passwords", "opens": "Passwords", "away": True}]
    back = await rig.do("undo", put_away["id"])
    assert back["ok"] and back["text"] == "Brought back the word “my passwords”."
    assert words_file()[0]["away"] is False and launcher.match("my passwords", apps.list_apps()).word == "my passwords"
    # A day later it is not put away again at once.
    rig.clock.advance(DAY + 1)
    await rig.service.tick()
    assert words_file()[0]["away"] is False


@pytest.mark.asyncio
async def test_the_daily_sweep_is_once_a_day_across_a_restart_and_a_word_he_used_stays(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    # He said it on day 20: that is the clock for this word.
    rig.agent._log_line({"t": NOW + 20 * DAY, "kind": "local", "via": "word", "word": "my passwords",
                         "action": "app", "verb": "open", "ok": True, "v": 2})
    await until(lambda: rig.store().words_last_used().get("my passwords") == NOW + 20 * DAY)
    rig.clock.advance(29 * DAY)
    await rig.service.tick()
    assert words_file()[0]["away"] is False
    swept = rig.store().conn.execute("SELECT value FROM service_state WHERE key='swept'").fetchone()[0]
    assert float(swept) == rig.clock()
    rig.stop()
    again = await rig_of(clock=Clock(rig.clock() + 3600)).start()     # an hour later, after a restart
    assert again.service._swept == float(swept)
    await again.service.tick()
    assert again.service._swept == float(swept)                        # not swept again until a day has passed


@pytest.mark.asyncio
async def test_a_word_used_is_noted_at_once_and_counted_once_when_the_row_is_read_again(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    t = NOW + 60
    rig.agent._log_line({"t": t, "kind": "local", "via": "word", "word": "my passwords", "action": "app",
                         "verb": "open", "ok": True, "v": 2})
    await until(lambda: rig.store().words_last_used().get("my passwords") == t)
    await asyncio.sleep(0.15)                                           # the debounced read of the same row
    await rig.settle()
    count = rig.store().conn.execute("SELECT count FROM words_used WHERE phrase='my passwords'").fetchone()[0]
    assert count == 1
    # A row that is not a word's use notes nothing.
    rig.agent._log_line({"t": t + 1, "kind": "local", "via": "typed", "word": None, "action": "app", "ok": True})
    await asyncio.sleep(0.1)
    assert rig.store().conn.execute("SELECT count FROM words_used WHERE phrase='my passwords'").fetchone()[0] == 1


# -- one thread, not the event loop --

@pytest.mark.asyncio
async def test_one_worker_thread_touches_the_databases_and_it_is_not_the_event_loop(rig_of, monkeypatch):
    seen: list[int] = []
    for name in ("ingest", "ripe_offer", "answer", "group", "asks_report", "said_no", "note_word_made",
                 "note_word_used", "resting", "waiting", "forget_asks", "words_last_used", "peek_offer",
                 "bring_back"):
        spy(monkeypatch, LoopStore, name, seen)
    for name in ("open_findings", "all", "get", "mark", "clear_found"):
        spy(monkeypatch, findings.FindingsStore, name, seen)
    rig = rig_of(passwords_asks(), opener=lambda url: "panel", fetcher=lambda url, timeout: {"items": []})
    found = plant()
    seen.clear()                                       # the test's own writing above is not the service
    await rig.start()
    await rig.ask_state()
    await rig.do("preview", "gpw1")
    await rig.do("accept", "gpw1")
    await rig.do("report", found.fp)
    await rig.do("send", found.fp)
    await rig.full()
    await rig.do("hide")
    await rig.do("show")
    await rig.do("forget_asks")
    await rig.do("clear_found")
    rig.agent._log_line({"t": NOW + 1, "kind": "local", "via": "word", "word": "my passwords", "ok": True})
    await asyncio.sleep(0.1)
    assert len(seen) > 20
    assert len(set(seen)) == 1 and threading.get_ident() not in seen


# -- when loop.db cannot be used --

@pytest.mark.asyncio
async def test_a_loop_db_that_cannot_be_opened_costs_one_line_and_nothing_else(rig_of, capsys):
    paths.loop_db().parent.mkdir(parents=True, exist_ok=True)
    paths.loop_db().write_bytes(b"this is not a database " * 50)
    rig = rig_of(passwords_asks())
    await rig.start(wait=False)
    await until(lambda: rig.service._failed_at is not None)
    await rig.settle()
    await rig.service.on_client(rig.bar)
    assert rig.bar.last("noticed") == {"type": "noticed", "count": 0, "hidden": False, "resting": "", "lately": "",
                                       "rows": []}
    for op in ("not_now", "accept", "hide", "forget_asks", "clear_found", "preview", "send"):
        res = await rig.do(op, "gpw1")
        assert res["ok"] is False and res["text"] == service_mod.NOT_SAVED, op
    assert (await rig.do("open"))["ok"] is True                        # needs no notes
    full = await rig.full()
    assert full["asks"] == [] and full["found"] == [] and full["changes"] == []
    assert await rig.service.noticed_word("hide") == (False, service_mod.NOT_SAVED)
    rig.agent._log_line({"t": NOW, "kind": "local", "via": "word", "word": "x", "ok": True})     # and a row is fine
    await asyncio.sleep(0.1)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("loop:")]
    assert len(lines) == 1 and "loop.db cannot be used" in lines[0]
    # Still broken ten minutes later: tried again, and no second line for the same trouble.
    rig.clock.advance(601)
    await rig.service.tick()
    assert rig.service._store is None and capsys.readouterr().err == ""
    # Mended: the next try opens it and everything works.
    for leftover in paths.loop_dir().glob("loop.db*"):
        leftover.unlink()
    rig.clock.advance(601)
    await rig.service.tick()
    assert rig.service._store is not None
    assert the_offer(await rig.ask_state()) is not None


# -- the model step --

def refine_on():
    path = paths.loop_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[refine]\nenabled = true\n")


@pytest.mark.asyncio
async def test_the_model_step_is_off_unless_his_config_says_so(rig_of, monkeypatch):
    called = []
    monkeypatch.setattr(refine, "ask", lambda *a, **k: called.append(k))
    rig = rig_of(passwords_asks())
    await rig.start()
    assert the_offer(await rig.ask_state()) is not None
    assert called == []


@pytest.mark.asyncio
async def test_the_model_step_runs_in_the_worker_for_the_group_about_to_be_offered(rig_of, monkeypatch):
    refine_on()
    called = []

    def ask(members, **kw):
        called.append((threading.get_ident(), list(members), kw))
        return refine.Answer(tuple(i for i, _ in members), "Open passwords", "word")
    monkeypatch.setattr(refine, "ask", ask)
    rig = rig_of(passwords_asks(), bar=False)
    await rig.start()
    await rig.ask_state()                                          # no bar: nothing is about to be offered
    rig.agent.current = 3
    rig.agent.signals._connected = True
    await rig.ask_state()                                          # a turn runs: not now either
    assert called == []
    rig.agent.current = None
    row = the_offer(await rig.ask_state())
    [(thread, members, kw)] = called
    assert thread != threading.get_ident() and kw["timeout"] == service_mod.REFINE_TIMEOUT
    assert len(members) == 3 and all(isinstance(i, str) and text for i, text in members)
    assert "word" in kw["allowed_forms"] and kw["provider"] == "fake" and kw["away"] is False
    assert rig.store().group(row["id"]).label == "Open passwords"
    called.clear()
    await rig.ask_state()                                          # an offer already showing is not asked about again
    assert called == []


@pytest.mark.asyncio
async def test_a_group_the_model_says_is_not_one_request_is_held_back(rig_of, monkeypatch):
    refine_on()
    monkeypatch.setattr(refine, "ask", lambda members, **kw: refine.Answer((members[0][0],), "Odd", "word"))
    rig = rig_of(passwords_asks())
    await rig.start()
    state = await rig.ask_state()
    assert the_offer(state) is None
    assert rig.store().group("gpw1").state == "not_now" and rig.store().waiting() == 0


@pytest.mark.asyncio
async def test_a_model_step_that_breaks_costs_one_line_and_the_offer_is_made_anyway(rig_of, monkeypatch, capsys):
    refine_on()

    def ask(members, **kw):
        raise RuntimeError(members[0][1])            # the text of his prompt must not reach stderr
    monkeypatch.setattr(refine, "ask", ask)
    rig = rig_of(passwords_asks())
    await rig.start()
    assert the_offer(await rig.ask_state()) is not None
    await rig.ask_state()
    err = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
    assert err == ["loop: refine: RuntimeError"]


# -- a tap that is not one, or breaks --

@pytest.mark.asyncio
async def test_a_tap_that_makes_no_sense_gets_a_plain_answer_and_the_state_after_it(rig_of, capsys):
    rig = rig_of(passwords_asks())
    await rig.start()
    taps = [({"op": "frobnicate"}, "Noticed cannot do “frobnicate”."),
            ({"op": "rm\x00\n-rf"}, "Noticed cannot do “rm-rf”."),
            ({"op": "x" * 500}, "Noticed cannot do “" + "x" * 30 + "”."),
            ({}, "Noticed did not hear what to do."), ({"op": ["undo"]}, "Noticed did not hear what to do."),
            ({"op": "not_now", "id": 5}, "That is not on the list any more."),
            ({"op": "accept", "id": None}, "That is not on the list any more."),
            ({"op": "undo", "id": {"a": 1}}, "That change cannot be found any more."),
            ({"op": "accept", "id": "gpw1", "form": ["word"]}, None),
            ({"op": "other_ways", "id": "gpw1"}, "Those are the other ways it could be done.")]
    for msg, text in taps:
        before = len(rig.bar.inbox)
        await rig.service.on_message({"type": "noticed_do", **msg}, rig.bar)
        got = rig.bar.inbox[before:]                    # (a receipt may come before, a second state after)
        kinds = [m["type"] for m in got]
        assert kinds.count("noticed_result") == 1 and "noticed" in kinds[kinds.index("noticed_result"):], msg
        if text is not None:
            assert got[kinds.index("noticed_result")]["text"] == text, msg
    # Another type of message is not its business, and answers nothing.
    before = len(rig.bar.inbox)
    await rig.service.on_message({"type": "hello"}, rig.bar)
    await rig.service.on_message({}, rig.bar)
    assert rig.bar.inbox[before:] == []
    assert capsys.readouterr().err == ""


@pytest.mark.asyncio
async def test_a_tap_that_breaks_is_one_line_with_no_words_of_his_and_the_bar_is_still_answered(rig_of, monkeypatch, capsys):
    his_words = "bl" + "ue-quince-" + "4471"

    def broken(self, *a, **k):
        raise RuntimeError(his_words)
    rig = rig_of(passwords_asks())
    await rig.start()
    monkeypatch.setattr(LoopStore, "answer", broken)
    res = await rig.do("not_now", "gpw1")
    assert res["ok"] is False and res["text"] == "That did not work."
    assert rig.bar.inbox[-1]["type"] == "noticed"
    err = capsys.readouterr().err
    assert err == "loop: not_now: RuntimeError\n" and his_words not in err


@pytest.mark.asyncio
async def test_two_taps_on_one_offer_at_once_make_one_word(rig_of):
    rig = rig_of(passwords_asks())
    window = rig.agent.connect("window")
    await rig.start()
    await rig.ask_state()
    msg = {"type": "noticed_do", "op": "accept", "id": "gpw1"}
    await asyncio.gather(rig.service.on_message(msg, rig.bar), rig.service.on_message(msg, window))
    texts = {m["text"] for c in (rig.bar, window) for m in c.of("noticed_result")}
    assert len(words_file()) == 1 and len(rig.turns("improve")) == 1
    assert "Made “my passwords” open Passwords." in texts and len(texts) == 2
    assert all(m["ok"] for c in (rig.bar, window) for m in c.of("noticed_result"))


@pytest.mark.asyncio
async def test_a_service_that_was_stopped_or_never_started_raises_nothing(rig_of):
    rig = rig_of(passwords_asks())
    service = rig.service
    service.on_row({"t": NOW, "kind": "local"})                   # before start: nothing to do yet
    await service.on_client(rig.bar)
    assert rig.bar.last("noticed")["count"] == 0
    await service.tick()
    await rig.start()
    rig.stop()
    rig.stop()
    await service.tick()
    service.on_row({"t": NOW, "kind": "local"})
    service.on_row("not a row")
    service.on_row({"kind": "improve"})
    service.on_row({"kind": "local", "via": "word", "word": "x", "t": "soon"})
    res = await rig.do("not_now", "gpw1")
    assert res["ok"] is False
    await asyncio.sleep(0.05)
    assert not [t for t in service._tasks if not t.done()]


@pytest.mark.asyncio
async def test_starting_the_service_makes_sure_the_prober_is_up_and_never_waits_for_it(rig_of):
    calls = []
    rig = rig_of(passwords_asks(), prober=lambda: calls.append(1))
    await rig.start()
    await until(lambda: calls)
    assert calls == [1]
    # No prober given (tests, a dev session): nothing is started.
    quiet = rig_of(passwords_asks())
    await quiet.start()
    assert quiet.service.prober is None


@pytest.mark.asyncio
async def test_a_prober_that_cannot_be_started_costs_one_line_and_nothing_else(rig_of):
    def no_systemd():
        raise FileNotFoundError(2, "No such file or directory", "systemctl")

    rig = rig_of(passwords_asks(), prober=no_systemd)
    await rig.start()
    await until(lambda: any(line.startswith("loop: prober: FileNotFoundError") for line in rig.service._said))
    await rig.settle()
    state = await rig.ask_state()
    assert state["count"] == 1                                    # the rest of the loop is unaffected


def test_the_real_prober_start_asks_the_users_systemd_for_the_unit_without_waiting(monkeypatch):
    from bombadil.loop import service as svc
    seen = {}
    monkeypatch.setattr(svc.subprocess, "run", lambda argv, **kw: seen.update(argv=argv, **kw))
    svc.start_prober()
    assert seen["argv"] == ["systemctl", "--user", "start", "--no-block", "bombadil-probe.service"]
    assert seen["timeout"] == 10 and seen["check"] is False


# -- the window's list --

@pytest.mark.asyncio
async def test_the_window_list_has_the_asks_the_words_the_changes_the_found_and_the_nos(rig_of):
    rig = rig_of(passwords_asks())
    plant()
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    full = await rig.full()
    assert set(full) == {"type", "hidden", "held", "resting", "asks", "changes", "found", "said_no", "words"}
    assert full["hidden"] is False and full["held"] is False and full["resting"] == ""
    assert full["words"] == [{"phrase": "my passwords", "opens": "Passwords", "away": False}]
    assert [c["what"] for c in full["changes"]] == ["word"] and full["found"][0]["state"] == "open"
    mine = next(a for a in full["asks"] if a["id"] == "gpw1")
    assert mine["n"] == 3 and mine["days"] == 3 and mine["state"] == "made" and len(mine["sentences"]) >= 1
    assert {"id", "title", "n", "days", "last", "state", "sentences", "became"} <= set(mine)
    assert full["said_no"] == []


@pytest.mark.asyncio
async def test_two_nos_in_a_row_rest_the_offers_and_the_chip_and_window_say_until_when(rig_of):
    rig = rig_of(gc.load_asks(), clock=Clock(gc.epoch(5, "23:30")))
    await rig.start()
    first = the_offer(await rig.ask_state())
    assert first["id"] == "gpw1" and (await rig.full())["held"] is False
    await rig.do("never", first["id"])
    rig.clock.t = gc.epoch(11, "23:30")
    second = the_offer(await rig.ask_state())
    assert second is not None and second["id"] != first["id"]
    await rig.do("never", second["id"])
    rig.clock.t = gc.epoch(12, "23:30")
    state = await rig.ask_state()
    assert state["resting"].startswith("Resting offers until ") and state["rows"] == []
    full = await rig.full()
    assert full["held"] is True and full["resting"] == state["resting"] and len(full["said_no"]) == 2


# -- two processes make the same tables --

def test_a_table_the_prober_made_a_moment_ago_is_looked_at_again_and_any_other_trouble_is_not(monkeypatch):
    monkeypatch.setattr(service_mod.time, "sleep", lambda s: None)
    tries = []

    def races():
        tries.append(1)
        if len(tries) < 3:
            raise sqlite3.OperationalError("table findings already exists")
        return "opened"
    assert service_mod._retry(races) == "opened" and len(tries) == 3
    other = []

    def locked():
        other.append(1)
        raise sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError):
        service_mod._retry(locked)
    assert len(other) == 1                                           # not a race: said at once
    always = []

    def forever():
        always.append(1)
        raise sqlite3.OperationalError("table findings already exists")
    with pytest.raises(sqlite3.OperationalError):
        service_mod._retry(forever)
    assert len(always) == 4                                          # and not for ever


# -- the store's additions, and what it leaves alone --

def test_peek_offer_says_what_would_be_offered_and_records_nothing(machine):
    seed(passwords_asks())
    store = LoopStore(paths.loop_db())
    store.ingest(now=NOW)
    group, rec = store.peek_offer(NOW)
    assert group.id == "gpw1" and rec.form.letter == "A" and store.waiting() == 0
    assert store.group("gpw1").state == "counting"
    offer = store.ripe_offer(NOW)
    assert offer.group.id == "gpw1" and offer.rec.form.letter == rec.form.letter
    assert store.peek_offer(NOW) is None                                   # one is showing: nothing more to pick
    empty = LoopStore(paths.loop_dir() / "empty.db")
    assert empty.peek_offer(NOW) is None


def test_rename_group_changes_the_label_and_nothing_else(machine):
    seed(passwords_asks())
    store = LoopStore(paths.loop_db())
    store.ingest(now=NOW)
    before = store.group("gpw1")
    assert store.rename_group("gpw1", "Passwords, please") is True
    after = LoopStore(paths.loop_db()).group("gpw1")                        # read by another connection
    assert after.label == "Passwords, please" and before.label != after.label
    assert (after.n, after.state, after.opens, after.word, list(after.days)) == (
        before.n, before.state, before.opens, before.word, list(before.days))
    assert store.rename_group("gnope", "x") is False


def test_words_last_used_maps_each_word_to_when_it_was_last_used_or_made(machine):
    store = LoopStore(paths.loop_db())
    assert store.words_last_used() == {}
    store.note_word_made("my passwords", 5.0)
    store.note_word_made("the weather", 6.0)
    store.note_word_used("my passwords", 9.0)
    assert store.words_last_used() == {"my passwords": 9.0, "the weather": 6.0}


def test_improve_rows_and_rows_of_other_kinds_are_never_counted_as_asks(machine):
    seed(passwords_asks())
    store = LoopStore(paths.loop_db())
    store.ingest(now=NOW)
    counted = store.conn.execute("SELECT COUNT(*) FROM requests WHERE counted=1").fetchone()[0]
    groups = [(g.id, g.n) for g in store.groups(now=NOW)]
    extra = [{"t": NOW + 1, "kind": "improve", "id": "i1-1", "what": "word", "title": "Made a word.", "group": "gpw1",
              "undo": {"op": "remove_word", "phrase": "x"}, "undone": False, "v": 2},
             {"t": NOW + 2, "kind": "improve", "id": "i1-2", "what": "word", "title": "Took it out.", "of": "i1-1",
              "undone": True, "v": 2, "prompt": "open my passwords", "ok": True, "result": "done"},
             {"t": NOW + 3, "kind": "something-new", "prompt": "open my passwords", "ok": True, "id": "t9-1"},
             {"t": NOW + 4, "kind": "local", "via": "word", "word": "my passwords", "action": "app", "ok": True}]
    with paths.turns_log().open("a") as f:
        f.writelines(json.dumps(r) + "\n" for r in extra)
    result = store.ingest(now=NOW + 10)
    assert result.new == 0
    assert store.conn.execute("SELECT COUNT(*) FROM requests WHERE counted=1").fetchone()[0] == counted
    assert [(g.id, g.n) for g in store.groups(now=NOW + 10)] == groups


# -- a real agentd, a real socket --
#
# The service is pre-built with the fixed clock (the same hooks agentd gives a service it makes itself),
# so what is ripe does not depend on the day the tests run. The client is the bar: it says hello, then asks
# for `noticed_state`, as shell/LoopState.qml does.

class Slow(providers.Claude):
    """A Claude adapter whose "CLI" is a Python program printing stream-json lines, after `script`."""
    name = "claude"

    def __init__(self, script):
        super().__init__("x")
        self.script = script

    @property
    def installed(self):
        return True

    def command(self, turn, workdir):
        return [sys.executable, "-c", self.script]

    def signed_in(self):
        return True


def reply(text="done", before=""):
    said = repr(text)
    return (before + "import json, sys\nsys.stdin.read()\n"
            "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': "
            + said + "}]}}), flush=True)\n"
            "print(json.dumps({'type': 'result', 'result': " + said + ", 'session_id': 's1'}), flush=True)\n")


def daemon(provider=None, clock=None, service=True, **kw):
    d = agentd.AgentD(provider or providers.Fake("x"), agentd._NoSnapshots(), **kw)
    if service:
        d.loop = LoopService(d, clock=clock or Clock())
        d.loop.debounce = 0.05
    return d


async def boot(d):
    server = asyncio.create_task(d.serve())
    for _ in range(300):
        if d.socket_path.exists() and d.access != "checking":
            break
        await asyncio.sleep(0.02)
    return server


async def connect(d):
    return await asyncio.open_unix_connection(str(d.socket_path), limit=1 << 24)


async def say(w, msg):
    w.write((json.dumps(msg) + "\n").encode())
    await w.drain()


async def hear(r, pred, timeout=6.0):
    """Read until a message satisfies `pred`; returns it and everything read on the way."""
    seen = []
    end = asyncio.get_running_loop().time() + timeout
    while True:
        line = await asyncio.wait_for(r.readline(), max(0.05, end - asyncio.get_running_loop().time()))
        assert line, f"agentd closed the socket after {seen}"
        seen.append(json.loads(line))
        if pred(seen[-1]):
            return seen[-1], seen


def is_(type_, **match):
    return lambda m: m.get("type") == type_ and all(m.get(k) == v for k, v in match.items())


async def the_bar(d):
    """A client that says it is the bar, and has read what agentd says first."""
    r, w = await connect(d)
    await hear(r, is_("noticed"))
    await say(w, {"type": "hello", "client": "bar", "pid": 4242, "build": "test"})
    return r, w


async def noticed_now(r, w, pred=None):
    await say(w, {"type": "noticed_state"})
    return (await hear(r, lambda m: m.get("type") == "noticed" and (pred is None or pred(m))))[0]


async def stop(d, server, *writers):
    for w in writers:
        w.close()
    server.cancel()
    await asyncio.gather(server, return_exceptions=True)


@pytest.mark.asyncio
async def test_agentd_tells_a_client_what_is_noticed_and_offers_only_to_a_bar_when_nothing_runs(machine):
    seed(passwords_asks())
    d = daemon(Slow(reply(before="import time\ntime.sleep(1.2)\n")))
    server = await boot(d)
    r, w = await connect(d)
    first, seen = await hear(r, is_("noticed"))
    assert [m["type"] for m in seen[:3]] == ["status", "entries", "setup"] and first["count"] == 0
    # Caught up with turns.jsonl (else the first look could land in the moment between his hello and his turn).
    await until(lambda: d.loop._ingested > 0 and not [t for t in d.loop._tasks if "_ticker" not in repr(t)])
    assert (await noticed_now(r, w))["rows"] == []          # a client that has not said it is the bar
    await say(w, {"type": "hello", "client": "bar", "pid": 4242, "build": "test"})
    await say(w, {"type": "prompt", "text": "a slow one"})
    await hear(r, lambda m: m.get("kind") == "turn_start")
    assert (await noticed_now(r, w))["rows"] == []          # a turn runs: nothing is offered, nothing recorded
    assert LoopStore(paths.loop_db()).waiting() == 0
    await hear(r, lambda m: m.get("kind") == "turn_end")
    state = await noticed_now(r, w, lambda m: m["rows"])    # idle, and the bar is here
    [row] = state["rows"]
    assert row["kind"] == "offer" and row["id"] == "gpw1" and row["primary"]["label"] == "Make the word"
    assert LoopStore(paths.loop_db()).waiting() == 1
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_agentd_makes_the_service_when_asked_to_and_a_client_hears_from_it(machine):
    d = daemon(service=False, loop_service=True)
    assert d.loop is None
    server = await boot(d)
    assert isinstance(d.loop, LoopService)
    r, w = await connect(d)
    first, _ = await hear(r, is_("noticed"))
    assert first == {"type": "noticed", "count": 0, "hidden": False, "resting": "", "lately": "", "rows": []}
    await say(w, {"type": "noticed_list"})
    full, _ = await hear(r, is_("noticed_full"))
    assert full["asks"] == [] and full["words"] == []
    await stop(d, server, w)
    assert d.loop._stopped                                      # serve() stopped it on its way out


@pytest.mark.asyncio
async def test_accepting_a_word_over_the_socket_writes_the_trail_once_and_undo_round_trips(machine):
    seed(passwords_asks())
    d = daemon()
    server = await boot(d)
    r, w = await the_bar(d)
    window_r, window_w = await connect(d)
    await hear(window_r, is_("noticed"))
    await noticed_now(r, w, lambda m: m["rows"])
    await say(w, {"type": "noticed_do", "op": "accept", "id": "gpw1", "form": "word"})
    result, seen = await hear(r, is_("noticed_result"))
    assert result["ok"] is True and result["text"] == "Made “my passwords” open Passwords."
    receipt = next(m for m in seen if m.get("kind") == "local")
    assert receipt["text"] == result["text"] and receipt["turn"] is None
    state, _ = await hear(r, is_("noticed"))
    assert state["count"] == 0 and state["lately"] == "1 change this week"
    # The one who did not tap hears the new state, and no receipt.
    heard = (await hear(window_r, lambda m: m.get("type") == "noticed" and m["count"] == 0))[1]
    assert not [m for m in heard if m.get("kind") == "local"]
    # The ledger has one trail row, written by agentd's own writer.
    rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    [trail] = [x for x in rows if x.get("kind") == "improve"]
    assert trail["undo"] == {"op": "remove_word", "phrase": "my passwords",
                              "opens": {"kind": "app", "name": "passwords"}} and receipt["undo_msg"]["id"] == trail["id"]
    # His Undo on the line sends exactly what the line carries.
    await say(w, receipt["undo_msg"])
    undone, _ = await hear(r, is_("noticed_result"))
    assert undone["ok"] and undone["text"] == "Took out the word “my passwords”." and words_file() == []
    rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines() if '"improve"' in line]
    assert [x["undone"] for x in rows] == [False, True] and rows[1]["of"] == trail["id"]
    await stop(d, server, w, window_w)


@pytest.mark.asyncio
async def test_a_word_he_says_is_noted_as_used_from_its_row(machine, monkeypatch):
    seed(passwords_asks())
    d = daemon()
    ran = []
    monkeypatch.setattr(d.launcher, "run", lambda action: ran.append((action.kind, action.target)) or (True, "Opened."))
    server = await boot(d)
    r, w = await the_bar(d)
    await noticed_now(r, w, lambda m: m["rows"])
    await say(w, {"type": "noticed_do", "op": "accept", "id": "gpw1"})
    await hear(r, is_("noticed_result"))
    await say(w, {"type": "prompt", "text": "my passwords"})
    done, _ = await hear(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert done["ok"] is True and ran == [("app", "passwords")]
    row = [json.loads(line) for line in paths.turns_log().read_text().splitlines()][-1]
    assert row["kind"] == "local" and row["via"] == "word" and row["word"] == "my passwords"
    await until(lambda: LoopStore(paths.loop_db()).words_last_used().get("my passwords") == row["t"])
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_accepting_an_app_runs_one_turn_of_his_and_undo_puts_the_app_in_the_trash(machine, monkeypatch):
    seed(run_asks())
    make = ("import os\nd = os.path.join(os.environ['BOMBADIL_APPS'], 'run-log')\nos.makedirs(d, exist_ok=True)\n"
            "open(os.path.join(d, 'main.qml'), 'w').write('import QtQuick\\nItem {}\\n')\n"
            "open(os.path.join(d, 'app.toml'), 'w').write('title = \"Run log\"\\n')\n")
    d = daemon(Slow(reply("Made Run log.", before=make)), clock=Clock(gc.epoch(2, "12:00")))
    ran = []
    monkeypatch.setattr(d.launcher, "run", lambda action: ran.append((action.kind, action.target, action.verb))
                        or (True, "ok"))
    server = await boot(d)
    r, w = await the_bar(d)
    row = (await noticed_now(r, w, lambda m: m["rows"]))["rows"][0]
    assert row["primary"]["label"] == "Make an app"
    await say(w, {"type": "noticed_do", "op": "accept", "id": row["id"], "form": "app"})
    result, early = await hear(r, is_("noticed_result"))
    assert result["ok"] and result["text"].startswith("Making a small app")
    _, later = await hear(r, lambda m: m.get("kind") == "turn_end")
    start = next(m for m in early + later if m.get("kind") == "turn_start")
    assert "log a 10 km run" in start["prompt"] and "app kit" in start["prompt"]
    state = await noticed_now(r, w, lambda m: m["lately"])
    assert state["lately"] == "1 change this week" and state["rows"] == []
    rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    [turn] = [x for x in rows if x.get("kind") is None and x.get("origin") == "loop"]
    assert turn["prompt"] == start["prompt"] and turn["ok"] is True
    [trail] = [x for x in rows if x.get("kind") == "improve"]
    assert trail["what"] == "app" and trail["undo"] == {"op": "trash_app", "name": "run-log"}
    # The turn is his like any other, and it is not counted as one more ask.
    await until(lambda: d.loop._debounce_handle is None)
    await asyncio.sleep(0.2)
    assert LoopStore(paths.loop_db()).conn.execute("SELECT COUNT(*) FROM requests WHERE counted=1").fetchone()[0] == 3
    await say(w, {"type": "noticed_do", "op": "undo", "id": trail["id"]})
    undone, _ = await hear(r, is_("noticed_result"))
    assert undone["ok"] and undone["text"] == "Put the app Run log away."
    assert not (paths.apps_dir() / "run-log").exists() and ("app", "run-log", "close") in ran
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_the_words_hide_noticed_show_noticed_and_noticed_go_to_the_service(machine, monkeypatch):
    d = daemon()
    monkeypatch.setattr(d.launcher, "run", lambda action: (_ for _ in ()).throw(AssertionError("not the launcher")))
    server = await boot(d)
    r, w = await the_bar(d)
    await until(lambda: d.loop._ingested > 0)
    await say(w, {"type": "prompt", "text": "hide noticed"})
    done, seen = await hear(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert [m["text"] for m in seen if m.get("kind") == "local"] == [
        "Hiding Noticed", "Noticed is hidden. Say “show noticed” to bring it back."] and done["ok"] is True
    hidden = await noticed_now(r, w, lambda m: m["hidden"])
    assert hidden["count"] == 0
    await say(w, {"type": "prompt", "text": "show noticed"})
    done, seen = await hear(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert done["text"] == "Opened Noticed." and any(m.get("type") == "noticed_open" for m in seen)
    assert (await noticed_now(r, w, lambda m: not m["hidden"]))["hidden"] is False
    await say(w, {"type": "prompt", "text": "noticed"})
    done, _ = await hear(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert done["text"] == "Opened Noticed."
    rows = [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    assert [(x["action"], x["verb"], x["ok"]) for x in rows if x.get("kind") == "local"] == [
        ("noticed", "hide", True), ("noticed", "open", True), ("noticed", "open", True)]
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_without_a_service_the_word_noticed_says_it_is_not_running(machine):
    d = daemon(service=False)
    server = await boot(d)
    r, w = await connect(d)
    await hear(r, is_("setup"))
    await say(w, {"type": "prompt", "text": "noticed"})
    done, _ = await hear(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert done["ok"] is False and done["text"] == "Noticed is not running."
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_a_service_that_breaks_while_he_says_noticed_costs_a_line_and_not_the_pill(machine, capsys):
    d = daemon()

    async def broken(verb):
        raise RuntimeError("no")
    d.loop.noticed_word = broken
    server = await boot(d)
    r, w = await connect(d)
    await hear(r, is_("setup"))
    await say(w, {"type": "prompt", "text": "noticed"})
    done, _ = await hear(r, lambda m: m.get("kind") == "local" and m.get("phase") == "done")
    assert done["ok"] is False and done["text"] == "Noticed is not running."
    assert "agentd: loop noticed: RuntimeError: no" in capsys.readouterr().err
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_a_loop_db_that_is_broken_leaves_agentd_serving_its_turns(machine, capsys):
    paths.loop_db().parent.mkdir(parents=True, exist_ok=True)
    paths.loop_db().write_bytes(b"not a database at all " * 40)
    d = daemon()
    server = await boot(d)
    r, w = await the_bar(d)
    await say(w, {"type": "prompt", "text": "tell me a joke"})
    _, seen = await hear(r, lambda m: m.get("kind") == "turn_end")
    assert next(m for m in seen if m.get("kind") == "text")["text"] == "echo: tell me a joke"
    assert (await noticed_now(r, w))["rows"] == []
    await say(w, {"type": "noticed_do", "op": "not_now", "id": "gpw1"})
    result, _ = await hear(r, is_("noticed_result"))
    assert result["ok"] is False and result["text"] == service_mod.NOT_SAVED
    await asyncio.sleep(0.2)                                    # the row of the turn is read, and fails quietly
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("loop:")]
    assert len(lines) == 1 and "loop.db cannot be used" in lines[0]
    await stop(d, server, w)


@pytest.mark.asyncio
async def test_a_finished_turn_is_read_soon_after_and_the_ledger_keeps_one_writer(machine, monkeypatch):
    d = daemon()
    written = []
    real = d._log_line
    monkeypatch.setattr(d, "_log_line", lambda entry: (written.append(entry.get("kind")), real(entry))[1])
    server = await boot(d)
    r, w = await the_bar(d)
    await say(w, {"type": "prompt", "text": "tell me a joke"})
    await hear(r, lambda m: m.get("kind") == "turn_end")
    await until(lambda: LoopStore(paths.loop_db()).conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1)
    assert written == [None]                                     # the service wrote nothing of its own
    await stop(d, server, w)

"""agentd's side of Tips: the card it tells every client, the shell's ops, the summon of a tip's button, and
what it counts as tried from its own events (the words sent, a launcher action, a tool, a job, a "!" command,
a stop).

The provider is the Fake echo or a Python one-liner standing in for the Claude CLI; Systemd and the sketch of
sysmap are fakes too. Nothing here calls a model, the network or a window. Tips is on in these tests (conftest
turns it off for every other test file, so no other client is sent a card)."""

import asyncio
import json
import time
from datetime import date

import pytest
from test_agentd import (
    DESK_TURN,
    ONE_TURN,
    PLANNED,
    SLOW_INSTALL,
    Scripted,
    _ask,
    _client,
    _events_until,
    _heard,
    _read_until,
    _ready,
    _say,
    _start_job,
)
from test_jobs import Systemd

from bombadil import agentd, apps, paths, procs, providers
from bombadil.jobs import Jobs
from bombadil.tips import Tip, Tips, read_table

TIP_DELAY = 0.4
PICTURE = {"card": {"type": "diagram", "text": "a picture"}}


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch):
    monkeypatch.setattr(agentd.sysmap, "snapshot", lambda *a, **k: {})
    monkeypatch.setattr(agentd, "TIPS_TURN_AFTER", TIP_DELAY)


class Day:
    """The clock tips.py counts days of use by, which the test moves."""

    def __init__(self, day=date(2026, 11, 9)):
        self.day = day

    def __call__(self):
        return self.day


class Installed(agentd._NoSnapshots):
    available = True        # an installed disk: restore points work, so "undo" is something to teach


class Turns(Scripted):
    """A provider whose turns run these scripts one after the other (the last one again and again)."""

    def __init__(self, *scripts):
        super().__init__(scripts[0])
        self.scripts, self.started = list(scripts), 0

    def command(self, turn, workdir):
        self.script = self.scripts[min(self.started, len(self.scripts) - 1)]
        self.started += 1
        return super().command(turn, workdir)


def tips_on(day=None, **hooks):
    """Tips on the shipped table, its state file in the test's own state folder."""
    return Tips(clock=day or Day(), **hooks)


def machine(provider=None, tips=None, **kw):
    return agentd.AgentD(provider or providers.Fake("x"), agentd._NoSnapshots(), tips=tips or tips_on(), **kw)


def _card(m):
    return m.get("type") == "tips" and "card" in m


def _list(m):
    return m.get("type") == "tips" and "list" in m


def _done(m):
    return m.get("kind") == "local" and m.get("phase") == "done"


async def _connect(d):
    """A client past its greeting."""
    r, w = await _client(d.socket_path)
    for _ in range(3):
        await r.readline()   # status, entries, setup
    return r, w


async def _serve(d, state="ready"):
    server = asyncio.create_task(d.serve())
    await _ready(d, state)
    r, w = await _connect(d)
    return server, r, w, (await _events_until(r, _card))[-1]


async def _summoned(r):
    return (await _events_until(r, lambda m: m.get("type") == "summon"))[-1]


async def _tips_heard(r, seconds=0.5):
    return [m for m in await _heard(r, seconds) if m.get("type") == "tips"]


async def _eventually(check, seconds=5.0):
    end = asyncio.get_running_loop().time() + seconds
    while not check():
        assert asyncio.get_running_loop().time() < end, "it did not happen"
        await asyncio.sleep(0.02)


# -- what the shell is told --

@pytest.mark.asyncio
async def test_a_client_that_connects_is_told_the_card_once_and_the_others_nothing(home):
    d = machine()
    server, r, w, first = await _serve(d)
    assert first == {"type": "tips", "present": True, "card": {
        "id": "make-an-app", "line": "Say what you need. It gets made.", "words": "Make me a ticketing app",
        "marks": [], "why": "Start here · 1 of 6", "index": 1, "count": 6,
        "tip": d.tips.listed()[0].as_dict()}}
    assert first["card"]["tip"]["captions"] == ["You ask", "It builds", "You have it"]
    beats = first["card"]["tip"]["beats"]
    assert beats and beats == [dict(b) for b in d.tips.listed()[0].beats]     # the scene goes over the socket whole
    assert json.loads(json.dumps(beats)) == beats
    r2, w2 = await _connect(d)
    assert (await _events_until(r2, _card))[-1] == first
    assert await _tips_heard(r) == []          # nothing changed for the first one, so it is not told again
    json.loads(paths.tips_file().read_text())     # setup was ready: the first week began and is written
    assert d.tips.state.first_ready == "2026-11-09"
    w.close(), w2.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_card_follows_setup_never_shown_before_the_ai_is_ready_and_gone_when_it_signs_out(home):
    d = machine(chosen=False)
    server, r, w, first = await _serve(d, "choose")
    assert first == {"type": "tips", "present": False, "card": None}     # setup owns the screen
    assert d.tips.state.first_ready == ""
    d.chosen = True                            # the person picked an AI, and it came up signed in
    await d._set_access("ready")
    ready = (await _events_until(r, _card))[-1]
    assert ready["present"] is True and ready["card"]["id"] == "make-an-app" and d.tips.state.first_ready
    await d._set_access("signed_out")
    assert (await _events_until(r, _card))[-1] == {"type": "tips", "present": False, "card": None}
    await d._set_access("resting")             # resting is still an AI that works, so the card is back
    assert (await _events_until(r, _card))[-1]["present"] is True
    assert await _tips_heard(r) == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_turn_that_changes_nothing_the_card_says_sends_nothing(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    await _ask(w, "tell me a joke")
    await _read_until(r, "turn_end")
    assert d.tips.state.days == ["2026-11-09"]       # it was a day of use, and the card is the same
    assert await _tips_heard(r, 1.0) == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_app_that_arrives_changes_the_list_and_not_the_card_so_nothing_is_sent(home, monkeypatch):
    d = machine(tips=tips_on())
    d.tips.tried(["make-an-app", "picture", "esc", "timer", "machine"])     # bang is what is left
    server, r, w, first = await _serve(d)
    assert first["card"]["id"] == "bang"
    assert [x["id"] for x in d.tips.listing()].count("open-app") == 0
    apps.create("Tickets", "import QtQuick\nItem {}\n")
    await d._tips_refresh()
    assert (await _tips_heard(r))[-1:] == []     # the card did not change: an app only changes the list
    assert "open-app" in [x["id"] for x in d.tips.listing()]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_an_app_that_arrives_is_seen_when_the_bar_is_told_of_it(home, monkeypatch):
    """Tips follows the apps as the pill's list does: the watcher that tells the bar tells Tips."""
    monkeypatch.setattr(agentd, "APPS_POLL", 0.05)
    d = machine()
    server, _, w, _ = await _serve(d)
    await asyncio.sleep(0.2)                                  # the watcher's first look is not news
    assert d.tips.facts.app == ""
    apps.create("Tickets", "import QtQuick\nItem {}\n")
    await _eventually(lambda: d.tips.facts.app == "Tickets")
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_turn_that_made_an_app_is_seen_when_it_ends_without_waiting_for_the_watcher(home):
    makes = ("import os, pathlib, sys\nsys.stdin.read()\n"
             "d = pathlib.Path(os.environ['BOMBADIL_APPS']) / 'notes'\nd.mkdir(parents=True)\n"
             "(d / 'main.qml').write_text('import QtQuick\\nItem {}\\n')\n"
             "(d / 'app.toml').write_text('title = \"Notes\"\\n')\n"
             "import json; print(json.dumps({'type': 'result', 'result': 'done'}), flush=True)\n")
    d = machine(provider=Scripted(makes))
    server, r, w, _ = await _serve(d)
    await _ask(w, "make me a notes app")
    await _read_until(r, "turn_end")
    await _eventually(lambda: d.tips.facts.app == "Notes", 2.0)     # the watcher looks every 3 s: this is not it
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_slow_look_never_tells_the_shell_what_was_true_before_the_ai_signed_out(home, monkeypatch):
    d = machine()
    d.chosen, d.access = True, "ready"
    d.tips.ready()
    told = []

    async def hear(msg):
        told.append(msg)
    monkeypatch.setattr(d, "broadcast", hear)
    real, slow_once = agentd.read_facts, {"slow": True}

    def slow(**kw):
        facts = real(**kw)
        if slow_once.pop("slow", False):
            time.sleep(0.4)                                   # the first look dawdles over the folders
        return facts
    monkeypatch.setattr(agentd, "read_facts", slow)
    a = asyncio.create_task(d._tips_refresh())
    await asyncio.sleep(0.1)
    d.access = "signed_out"                                   # and the AI signs out meanwhile
    b = asyncio.create_task(d._tips_refresh())
    await asyncio.gather(a, b)
    assert [m["present"] for m in told] == [False]            # it was never told the card was there
    assert d.tips.facts.ai == ""


@pytest.mark.asyncio
async def test_two_looks_at_once_leave_the_newer_one_standing(home, monkeypatch):
    d = machine()
    d.chosen, d.access = True, "ready"
    real, slow_once = agentd.read_facts, {"slow": True}

    def slow(**kw):
        facts = real(**kw)                                    # the folders as they are when it looks ...
        if slow_once.pop("slow", False):
            time.sleep(0.4)                                   # ... and a long while before it says so
        return facts
    monkeypatch.setattr(agentd, "read_facts", slow)
    a = asyncio.create_task(d._tips_refresh())
    await asyncio.sleep(0.1)
    apps.create("Tickets", "import QtQuick\nItem {}\n")
    b = asyncio.create_task(d._tips_refresh())
    await asyncio.gather(a, b)
    assert d.tips.facts.app == "Tickets"                      # the old look did not land on top of the new


@pytest.mark.asyncio
async def test_a_card_that_cannot_be_sent_costs_no_client_and_no_turn(home, capsys):
    """A row JSON cannot carry never gets past the loader; if one did, every client would still be served."""
    bad = Tip("dated", "start", 1, "Say it.", "Do it", needs=(), tried_when=("words",),
              captions=("a", "b", "c"), beats=({"hold": date(1979, 5, 27)},))
    d = machine(tips=Tips(rows=[bad], clock=Day()))
    server = asyncio.create_task(d.serve())
    await _ready(d)
    r, w = await _client(d.socket_path)
    await _ask(w, "tell me a joke")
    msgs = await _read_until(r, "turn_end")
    assert not [m for m in msgs if m.get("type") == "tips"]
    assert "agentd: tips: TypeError" in capsys.readouterr().err
    r2, w2 = await _client(d.socket_path)                    # and a second client is as well served
    await _ask(w2, "and another")
    await _read_until(r2, "turn_end")
    w.close(), w2.close()
    server.cancel()


# -- the shell's ops --

@pytest.mark.asyncio
async def test_the_arrows_turn_the_card_and_it_is_sent_only_when_it_moved(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "tips", "op": "turn", "dir": 1})
    second = (await _events_until(r, _card))[-1]
    assert second["card"]["id"] == "picture" and second["card"]["why"] == "Start here · 2 of 6"
    await _say(w, {"type": "tips", "op": "turn", "dir": -1})
    await _say(w, {"type": "tips", "op": "turn", "dir": -1})
    assert (await _events_until(r, _card))[-1]["card"]["id"] == "make-an-app"
    assert (await _events_until(r, _card))[-1]["card"]["id"] == "bang"
    for bad in (0, 3, "1", None, True):
        await _say(w, {"type": "tips", "op": "turn", "dir": bad})
    await _say(w, {"type": "tips", "op": "nothing"})
    assert await _tips_heard(r) == [] and d.tips.state.tried == []      # turning is not trying
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_open_keeps_the_tip_for_the_app_and_calls_the_hook_and_opens_no_window(home, monkeypatch):
    asked = []
    d = machine(tips=tips_on(on_open=lambda tip, play: asked.append((tip, play))))
    ran = []
    monkeypatch.setattr(d.launcher, "run", lambda action: ran.append(action) or (True, "x"))
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "tips", "op": "open", "id": "picture", "play": True})
    await _eventually(lambda: asked)
    assert asked == [("picture", True)] and json.loads(paths.tips_file().read_text())["open_on"] == "picture"
    await _say(w, {"type": "tips", "op": "open", "id": "", "play": False})
    await _say(w, {"type": "tips", "op": "open", "id": "undo"})            # not backed on the live USB
    await _say(w, {"type": "tips", "op": "open", "id": 4})
    await _eventually(lambda: len(asked) == 4)
    assert asked[1:] == [("", False)] * 3 and d.tips.state.open_on == ""
    assert ran == [] and await _tips_heard(r) == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_list_is_answered_on_that_connection_and_holds_what_this_machine_backs(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    app_r, app_w = await _connect(d)
    await _events_until(app_r, _card)
    d.tips.tried(["picture"])
    await _say(app_w, {"type": "tips", "op": "list"})
    answer = (await _events_until(app_r, _list))[-1]
    ids = [t["id"] for t in answer["list"]]
    # The fake AI is on, nothing else: no app, no project, no installed disk, no card seen. The desk can keep a
    # widget, so that tip is here (the word "widgets" is a launcher word).
    assert ids == ["make-an-app", "picture", "esc", "timer", "machine", "bang", "passwords", "whyhere",
                   "draw", "toolchain", "disk", "pause", "keep", "why"]
    assert [t["tried"] for t in answer["list"]][:3] == [False, True, False]
    assert not any(t["new"] for t in answer["list"])
    pause = next(t for t in answer["list"] if t["id"] == "pause")
    assert pause["line"] == "Out of Fake? Your apps still work." and pause["words"] == "pause Fake"
    assert not [m for m in await _heard(r) if _list(m)]       # the card's client was not sent it
    app_w.close(), w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_ops_look_at_the_machine_first_so_a_new_app_is_not_waited_for(home):
    asked = []
    d = machine(tips=tips_on(on_open=lambda tip, play: asked.append((tip, play))))
    server, r, w, _ = await _serve(d)
    apps.create("Tickets", "import QtQuick\nItem {}\n")      # made a moment ago: the watcher has not looked
    await _say(w, {"type": "tips", "op": "open", "id": "open-app", "play": False})
    await _eventually(lambda: asked)
    assert asked == [("open-app", False)]                     # a tip the machine backs now, not as it was
    apps.create("Notes", "import QtQuick\nItem {}\n")
    await _say(w, {"type": "tips", "op": "list"})
    answer = (await _events_until(r, _list))[-1]
    assert next(t["words"] for t in answer["list"] if t["id"] == "open-app") == "Notes"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_list_that_makes_a_tip_known_sends_the_card_it_now_allows(home):
    """A tip that came with an update waits in the app; once the app has listed it, it can be on the card."""
    old = [r for r in read_table() if r.id != "bang"]
    first = Tips(rows=old, clock=Day())
    first.ready()
    first.tried([r.id for r in old])
    d = machine()                                             # the new table, the same file
    server, r, w, hello = await _serve(d)
    assert hello == {"type": "tips", "present": False, "card": None}
    await _say(w, {"type": "tips", "op": "list"})
    heard = await _events_until(r, _card)
    assert next(m for m in heard if _list(m))["list"][5]["new"] is True
    assert heard[-1]["present"] is True and heard[-1]["card"]["id"] == "bang"
    w.close()
    server.cancel()


@pytest.mark.parametrize("snaps, offered", [(agentd._NoSnapshots, False), (Installed, True)])
@pytest.mark.asyncio
async def test_undo_is_a_tip_only_on_an_installed_disk(home, snaps, offered):
    d = agentd.AgentD(providers.Fake("x"), snaps(), tips=tips_on())
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "tips", "op": "list"})
    ids = [t["id"] for t in (await _events_until(r, _list))[-1]["list"]]
    assert ("undo" in ids) is offered
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_tips_button_summons_the_pill_with_tip_true_and_unedited_words_try_it(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    other_r, other_w = await _connect(d)
    await _events_until(other_r, _card)
    await _say(other_w, {"type": "summon", "text": "Set a 25 minute timer", "tip": True, "id": "timer"})
    assert await _summoned(r) == {"type": "summon", "text": "Set a 25 minute timer", "tip": True}
    await _ask(w, "Set a 25 minute timer for tea")                       # edited: the person's own ask
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == []
    await _ask(w, "Set a 25 minute timer")
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == ["timer"]
    await _say(other_w, {"type": "summon", "text": "About ~/a.pdf: "})    # the Brain's: no tip, as it was
    assert (await _summoned(r)) == {"type": "summon", "text": "About ~/a.pdf: "}
    await _say(other_w, {"type": "summon", "text": "x", "tip": "yes", "id": "bang"})   # only true is true
    assert (await _summoned(r)) == {"type": "summon", "text": "x"}
    await _say(other_w, {"type": "summon", "tip": True})
    assert (await _summoned(r)) == {"type": "summon", "tip": True}
    other_w.close(), w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_words_a_button_summoned_are_remembered_for_its_tip_and_only_a_tip_true_summon_is(home):
    """"zzz qqq" is nobody's own words: only the summon links it to a tip."""
    d = machine()
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "summon", "text": "zzz qqq", "tip": True, "id": "bang"})
    await _summoned(r)
    await _ask(w, "zzz qqq now")                                          # edited: the person's own ask
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == []
    await _ask(w, "zzz qqq")
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == ["bang"]
    for odd in ({"text": "aaa bbb", "id": "esc"}, {"text": "aaa bbb", "tip": "yes", "id": "esc"},
                {"text": "aaa bbb", "tip": True, "id": "no-such-tip"}):
        await _say(w, {"type": "summon", **odd})
        await _summoned(r)
        await _ask(w, "aaa bbb")
        await _read_until(r, "turn_end")
    assert d.tips.state.tried == ["bang"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_with_tips_off_nothing_is_sent_and_nothing_is_kept(home, monkeypatch):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())      # conftest turns Tips off
    assert d.tips is None
    server = asyncio.create_task(d.serve())
    await _ready(d)
    r, w = await _connect(d)
    await _say(w, {"type": "tips", "op": "list"})
    await _say(w, {"type": "summon", "text": "hi", "tip": True, "id": "bang"})
    # (what agentd says to every client on connecting, the coding sessions among it, is not what this is about)
    assert [m for m in await _heard(r) if m["type"] in ("summon", "tips")] == [
        {"type": "summon", "text": "hi", "tip": True}]
    await _ask(w, "tell me a joke")
    await _read_until(r, "turn_end")
    assert not paths.tips_file().exists()
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_default_tips_hear_of_the_desk_putting_them_away_and_keeping_them(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_TIPS", "1")
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    assert d.tips is not None and d.tips.hidden() is False and d.tips.kept() is False
    d.desk.hidden.add("tips")                   # once the desk has a Tips widget, putting it away is this
    assert d.tips.hidden() is True
    d.desk.kept = {"tips"}                      # and keeping it (the desk's second piece) is this
    assert d.tips.kept() is True
    soon = []
    monkeypatch.setattr(d, "_tips_soon", lambda: soon.append(1))
    d._desk_dirty = True
    await d._desk_broadcast()                   # a change of the desk is a change of what Tips shows
    d.desk.apply("hide", "machine")
    d._desk_dirty = True
    await d._desk_broadcast()
    assert soon == [1]


# -- a day of use --

@pytest.mark.asyncio
async def test_a_day_of_use_is_a_day_the_person_typed_not_an_app_or_a_helper(home):
    day = Day()
    d = machine(tips=tips_on(day))
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "prompt", "text": "build it", "asked_by": "builder"})
    await _read_until(r, "turn_end")
    await _ask(w, "[from app notes] tidy this")
    await _read_until(r, "turn_end")
    assert d.tips.state.days == []
    await _ask(w, "desk")                                           # a launcher word is typed in the pill too
    await _events_until(r, _done)
    assert d.tips.state.days == ["2026-11-09"]
    day.day = date(2026, 11, 10)
    await _ask(w, "tell me a joke")
    await _read_until(r, "turn_end")
    assert d.tips.state.days == ["2026-11-09", "2026-11-10"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_prompt_from_a_process_inside_a_turn_is_not_the_person_typing(home, monkeypatch):
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: None)      # no scope: the process tree marks the turn
    says = ("import json, os, socket, sys, time\nsys.stdin.read()\n"
            "s = socket.socket(socket.AF_UNIX)\ns.connect(os.environ['BOMBADIL_SOCKET'])\n"
            "s.sendall((json.dumps({'type': 'prompt', 'text': 'hello there'}) + '\\n').encode())\n"
            "time.sleep(0.4)\ns.close()\n"
            "print(json.dumps({'type': 'result', 'result': 'done'}), flush=True)\n")
    d = machine(provider=Turns(says, ONE_TURN))
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "prompt", "text": "go", "asked_by": "builder"})   # not the person's either
    await _read_until(r, "turn_end")
    await _read_until(r, "turn_end")                                         # the turn the agent's process asked for
    assert d.tips.state.days == []
    await _ask(w, "hello again")                                             # this process is not in a turn
    await _read_until(r, "turn_end")
    assert d.tips.state.days == ["2026-11-09"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_card_goes_with_the_seventh_day_of_use(home):
    day = Day()
    t = tips_on(day)
    for n in range(6):
        day.day = date(2026, 11, 1 + n)
        t.use()
    day.day = date(2026, 12, 1)
    d = machine(tips=t)
    server, r, w, first = await _serve(d)
    assert first["present"] is True
    await _ask(w, "tell me a joke")
    assert (await _events_until(r, _card))[-1] == {"type": "tips", "present": False, "card": None}
    w.close()
    server.cancel()


# -- what counts a tip as tried --

async def _typed(d, w, r, text):
    await _ask(w, text)
    await _events_until(r, _done)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("typed, tried", [
    ("desk", ["desk"]), ("undo", ["undo"]), ("where did this come from", ["whyhere"]),
    ("how's the machine", ["machine"]), ("show machine", ["machine"]), ("hide machine", []),
    ("tickets", ["open-app"]), ("open tickets", ["open-app"]), ("close tickets", []),
    ("how am I connected", ["picture"]), ("where did my disk go", ["picture", "disk"]),
    ("widgets", ["keep"]), ("show my widgets", ["keep"]),
    ("brain", []), ("history", []), ("tell me about desks", None)])
async def test_a_launcher_word_that_worked_tries_the_tip_it_is(home, monkeypatch, typed, tried):
    apps.create("Tickets", "import QtQuick\nItem {}\n")
    d = machine()
    monkeypatch.setattr(d.launcher, "run", lambda action: (True, "Done."))
    monkeypatch.setattr(agentd.sysmap, "capture", lambda *a, **k: PICTURE)
    server, r, w, _ = await _serve(d)
    if tried is None:                                  # not a launcher word: the model's turn, nothing tried
        await _ask(w, typed)
        await _read_until(r, "turn_end")
        tried = []
    else:
        await _typed(d, w, r, typed)
    assert sorted(d.tips.state.tried) == sorted(tried)
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_launcher_word_that_failed_tries_nothing(home, monkeypatch):
    d = machine()
    monkeypatch.setattr(d.launcher, "run", lambda action: (False, "Could not."))
    server, r, w, _ = await _serve(d)
    await _typed(d, w, r, "desk")
    assert d.tips.state.tried == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_pausing_tries_the_tip_and_resuming_does_not(home):
    d = machine(provider=Scripted(ONE_TURN))
    server, r, w, _ = await _serve(d)
    await _typed(d, w, r, "resume claude")
    assert d.tips.state.tried == []
    await _typed(d, w, r, "pause claude")
    assert d.tips.state.tried == ["pause"]
    w.close()
    server.cancel()


def _tool_turn(error):
    return ("import json, sys\nsys.stdin.read()\n"
            "def out(o): print(json.dumps(o), flush=True)\n"
            "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': "
            "'mcp__bombadil-os__create_app', 'input': {'title': 'Tickets', "
            "'qml': 'import QtQuick\\nItem {}\\n'}}]}})\n"
            "out({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', "
            f"'content': 'ok', 'is_error': {error}}}]}}}})\n"
            "out({'type': 'result', 'result': 'done', 'session_id': 's1'})\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("error, tried", [(False, ["make-an-app"]), (True, [])])
async def test_a_tool_the_turn_used_tries_the_tip_when_it_worked(home, error, tried):
    d = machine(provider=Scripted(_tool_turn(error)))
    server, r, w, _ = await _serve(d)
    await _ask(w, "I need to keep track of tickets")      # nothing like the tip's words
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == tried
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_shell_command_tries_the_bang_tip(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    await _ask(w, "!echo hello")
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == ["bang"]
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_helpers_shell_command_is_not_the_person_typing_one(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "prompt", "text": "!echo hello", "asked_by": "builder"})
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == [] and d.tips.state.days == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_turn_that_is_stopped_tries_esc_and_a_stop_with_nothing_running_does_not(home):
    d = machine(provider=Scripted(DESK_TURN))
    server, r, w, _, tool_w, _ = await _in_a_turn_with(d, "install docker and set it up")
    assert d.tips.state.tried == []
    await _say(w, {"type": "stop"})
    await _read_until(r, "turn_end")
    assert d.tips.state.tried == ["esc"]
    d.tips.state.tried.clear()
    await _typed(d, w, r, "stop")                               # the word, with nothing to stop
    assert d.tips.state.tried == []
    w.close(), tool_w.close()
    server.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("how, tried", [
    ({"type": "stop"}, ["esc"]), ({"type": "cancel"}, ["esc"]), ("stop", ["esc"]), ("Stop it", None),
    ("undo", ["undo"]), ("restart", []), ("shutdown", []), ({"type": "setup_action", "id": "cancel"}, [])])
async def test_only_the_person_stopping_a_turn_is_the_esc_tip(home, monkeypatch, how, tried):
    """Undo, restart, shutdown and the setup card's cancel stop a turn too, and none of them is Esc."""
    d = machine(provider=Scripted(DESK_TURN))
    monkeypatch.setattr(d.launcher, "run", lambda action: (True, "Done."))      # restart and shutdown are not run
    server, r, w, _, tool_w, _ = await _in_a_turn_with(d, "install docker and set it up")
    if isinstance(how, dict):
        await _say(w, how)
    else:
        await _ask(w, how)
    if tried is None:                                       # "Stop it" is a sentence for the model, not the word
        await _say(w, {"type": "stop"})
        tried = ["esc"]
    await _read_until(r, "turn_end")
    await asyncio.sleep(0.1)
    assert d.tips.state.tried == tried
    w.close(), tool_w.close()
    server.cancel()



async def _in_a_turn_with(d, prompt):
    """The turn waits, and a second client stands in for its tools (as test_agentd's `_in_a_turn`)."""
    server, r, w, first = await _serve(d)
    tool_r, tool_w = await _connect(d)
    await _ask(w, prompt)
    await _events_until(r, lambda m: m.get("kind") == "text")
    return server, r, w, tool_r, tool_w, first


@pytest.mark.asyncio
@pytest.mark.parametrize("job, tried", [
    ({"kind": "timer", "seconds": 600, "title": "", "command": ""}, ["timer"]),
    ({"title": "Build", "command": "make"}, ["tests"]),
    ({"title": "Watch", "command": "true", "kind": "watch"}, [])])
async def test_a_job_the_turn_starts_tries_the_tip_for_that_kind(home, monkeypatch, job, tried):
    monkeypatch.setattr(agentd, "JOBS_POLL", 0.05)
    d = machine(provider=Scripted(DESK_TURN), stopper=procs.Stopper(grace=1.0), jobs=Jobs(runner=Systemd()))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn_with(d, "keep an eye on it")
    await _say(tool_w, {"type": "job-tool", "id": "s", "turn": 1, "op": "start", **job})
    assert (await _events_until(tool_r, lambda m: m.get("type") == "job-result"))[-1]["ok"] is True
    assert d.tips.state.tried == tried
    await _say(w, {"type": "stop"})
    await _read_until(r, "turn_end")
    w.close(), tool_w.close()
    server.cancel()


# -- the desk card the tip about the desk waits for --

@pytest.mark.asyncio
async def test_a_plan_of_two_steps_is_a_card_seen(home):
    d = machine(provider=Scripted(PLANNED))
    server, r, w, _ = await _serve(d)
    assert d.tips.state.desk_seen is False and "desk" not in [t["id"] for t in d.tips.listing()]
    await _ask(w, "install ffmpeg and open the browser")
    await _events_until(r, lambda m: m.get("kind") == "plan")
    assert d.tips.state.desk_seen is True
    assert "desk" in [t["id"] for t in d.tips.listing()]
    await _say(w, {"type": "stop"})
    await _read_until(r, "turn_end")
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_step_that_touches_the_system_is_a_card_seen(home):
    d = machine(provider=Scripted(SLOW_INSTALL), stopper=procs.Stopper(grace=1.0))
    server, r, w, _ = await _serve(d)
    await _ask(w, "install docker")
    assert d.tips.state.desk_seen is False
    await _events_until(r, lambda m: m.get("kind") == "status" and m.get("risk") == "system")
    assert d.tips.state.desk_seen is True                   # Now rises for it
    await _say(w, {"type": "stop"})
    await _read_until(r, "turn_end")
    w.close()
    server.cancel()


ONE_STEP = ("import json, sys\nsys.stdin.read()\n"
            "def out(o): print(json.dumps(o), flush=True)\n"
            "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'TaskCreate',"
            " 'input': {'subject': 'Install ffmpeg', 'description': 'd'}}]}})\n"
            "out({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content':"
            " 'Task #1 created successfully: Install ffmpeg'}]}})\n"
            "out({'type': 'result', 'result': 'done', 'session_id': 's1'})\n")


@pytest.mark.asyncio
async def test_a_plan_of_one_step_raises_no_card_so_it_is_not_one_seen(home):
    d = machine(provider=Scripted(ONE_STEP))
    server, r, w, _ = await _serve(d)
    await _ask(w, "install ffmpeg")
    msgs = await _read_until(r, "turn_end")
    assert {len(m["steps"]) for m in msgs if m.get("kind") == "plan"} == {1}      # (the plan, and again at the end)
    assert d.tips.state.desk_seen is False
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_machine_card_appearing_is_a_card_seen_and_not_appearing_is_not(home):
    d = machine()
    await d._vitals_send({"type": "vitals", "present": False, "rows": []})
    assert d.tips.state.desk_seen is False
    await d._vitals_send({"type": "vitals", "present": True, "rows": []})
    assert d.tips.state.desk_seen is True



@pytest.mark.asyncio
async def test_a_job_on_the_desk_is_a_card_seen_too(home, monkeypatch):
    monkeypatch.setattr(agentd, "JOBS_POLL", 0.05)
    d = machine(provider=Scripted(DESK_TURN), stopper=procs.Stopper(grace=1.0), jobs=Jobs(runner=Systemd()))
    server, r, w, tool_r, tool_w, _ = await _in_a_turn_with(d, "get me the ubuntu iso")
    assert d.tips.state.desk_seen is False
    await _start_job(tool_r, tool_w)
    await _eventually(lambda: d.tips.state.desk_seen)
    await _say(w, {"type": "stop"})
    await _read_until(r, "turn_end")
    w.close(), tool_w.close()
    server.cancel()


# -- the card turns after a turn that tried a tip --

@pytest.mark.asyncio
async def test_the_card_turns_a_while_after_a_turn_that_tried_a_tip_ends_and_not_before(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "summon", "text": "Make me a ticketing app", "tip": True, "id": "make-an-app"})
    await _ask(w, "Make me a ticketing app")
    await _read_until(r, "turn_end")
    ended = asyncio.get_running_loop().time()
    assert d.tips.state.tried == ["make-an-app"]
    turned = (await _events_until(r, _card, 5))[-1]
    assert asyncio.get_running_loop().time() - ended >= TIP_DELAY * 0.8
    assert turned["card"]["id"] == "picture" and turned["card"]["why"] == "Start here · 2 of 6"
    assert await _tips_heard(r, 1.0) == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_turn_that_tried_nothing_never_turns_the_card(home):
    d = machine()
    server, r, w, _ = await _serve(d)
    for text in ("tell me a joke", "and another"):
        await _ask(w, text)
        await _read_until(r, "turn_end")
    assert await _tips_heard(r, TIP_DELAY * 3) == []
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_the_card_waits_for_a_later_turn_that_begins_before_it_turns(home, monkeypatch):
    monkeypatch.setattr(agentd, "TIPS_TURN_AFTER", 0.6)
    slow = ("import json, sys, time\nsys.stdin.read()\ntime.sleep(1.2)\n"
            "print(json.dumps({'type': 'result', 'result': 'done', 'session_id': 's1'}), flush=True)\n")
    d = machine(provider=Scripted(slow))
    server, r, w, _ = await _serve(d)
    await _say(w, {"type": "summon", "text": "Make me a ticketing app", "tip": True, "id": "make-an-app"})
    await _ask(w, "Make me a ticketing app")
    await _read_until(r, "turn_end")
    await _ask(w, "and now something else")                 # begins before the card has turned
    during = await _read_until(r, "turn_end")
    assert not [m for m in during if m.get("type") == "tips"]
    assert (await _events_until(r, _card, 5))[-1]["card"]["id"] == "picture"      # after this turn, not in it
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_word_that_tried_the_tip_on_the_card_turns_the_card_after_it_with_no_turn(home, monkeypatch):
    t = tips_on()
    t.ready()
    t.tried(["make-an-app", "picture", "esc", "timer"])
    d = machine(tips=t)
    monkeypatch.setattr(d.launcher, "run", lambda action: (True, "Done."))
    server, r, w, first = await _serve(d)
    assert first["card"]["id"] == "machine" and first["card"]["why"] == "Start here · 5 of 6"
    await _ask(w, "how's the machine")
    assert [m for m in await _events_until(r, _done)][-1]["ok"] is True
    ended = asyncio.get_running_loop().time()
    turned = (await _events_until(r, _card, 5))[-1]
    assert asyncio.get_running_loop().time() - ended >= TIP_DELAY * 0.8
    assert turned["card"]["id"] == "bang" and turned["card"]["why"] == "Start here · 6 of 6"
    w.close()
    server.cancel()


@pytest.mark.asyncio
async def test_a_word_that_tried_a_tip_not_on_the_card_leaves_the_card_where_it_is(home, monkeypatch):
    d = machine()
    monkeypatch.setattr(agentd.sysmap, "capture", lambda *a, **k: PICTURE)
    server, r, w, first = await _serve(d)
    assert first["card"]["id"] == "make-an-app"
    await _typed(d, w, r, "how am I connected")
    assert d.tips.state.tried == ["picture"]
    assert await _tips_heard(r, TIP_DELAY * 3) == []       # the card is the one it was: its tip was not tried
    w.close()
    server.cancel()

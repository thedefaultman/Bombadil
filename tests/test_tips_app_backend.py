"""The Tips app's backend (share/apps/tips/app.py) against a fake agentd and a temp tips.json.

The backend runs on this thread's Qt event loop, the way the window runs it, and is driven through the slots QML
calls. `FakeAgentd` (tests/tips_app_lab.py) plays agentd on a thread of its own: it greets a client the way agentd
does, answers the one request the app makes and records every line and how many connections are open.

What is held to what, in the order of the file: the list and its order, what is shown (only what agentd lists),
choosing a tip, the words a button puts in the pill, the ticks and where they come from (tips.json, read and never
written), the press on the desk card that opens the app on a tip, one-shot connections and nothing else, agentd
away and back, and the limits of a line.
"""

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tips_app_lab as lab
from PySide6.QtGui import QGuiApplication

from bombadil import tips as tips_mod


@pytest.fixture(scope="module")
def qt():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def mod(monkeypatch):
    """The app's module with short clocks, so a test does not wait the seconds a person would."""
    m = lab.load_app()
    monkeypatch.setattr(m, "RETRY_MS", 120)
    monkeypatch.setattr(m, "QUIET_MS", 1500)
    monkeypatch.setattr(m, "FRESH_MS", 0)
    monkeypatch.setattr(m, "ANSWER_MS", 600)
    monkeypatch.setattr(m, "SETTLE_MS", 40)
    monkeypatch.setattr(m, "WATCH_MS", 150)
    monkeypatch.setattr(m, "CHECKING", False)
    return m


@pytest.fixture
def state(home):
    return home / "state" / "tips.json"


@pytest.fixture
def agentd(home, qt):
    made: list[lab.FakeAgentd] = []

    def make(tips=None, start=True):
        (home / "run").mkdir(parents=True, exist_ok=True)
        fake = lab.FakeAgentd(home / "run" / "agentd.sock", lab.listing(home) if tips is None else tips)
        if start:
            fake.start()
        made.append(fake)
        return fake
    yield make
    for fake in made:
        fake.stop()


@pytest.fixture
def backend(mod, qt):
    made = []

    def make():
        b = mod.Backend()
        made.append(b)
        return b
    yield make
    for b in made:
        b.deleteLater()
    lab.spin(20)


def ids(b) -> list[str]:
    return [t["id"] for t in b.tips]


def rows(b) -> list[dict]:
    return b.model.rows()


def up(b) -> bool:
    return lab.wait_until(lambda: b.connected and b.count > 0)


def out_now(b, mod) -> list:
    """The requests this backend has out at this moment, counted on its own side: each is a socket of its own
    until it is finished. (What the fake agentd counts lags behind a close, so under load it can see two where
    the app never had more than one.)"""
    return [o for o in b.findChildren(mod.Once) if not o._over]


def settle(fake, ms: int = 250):
    """Everything the app opened has been closed again."""
    lab.spin(ms)
    assert lab.wait_until(lambda: fake.open == 0)


# ---- the list and its order -----------------------------------------------------------------------------------

def test_the_list_is_what_agentd_said_in_the_order_of_the_sections(agentd, backend):
    fake = agentd()
    b = backend()
    assert up(b)
    assert b.count == len(fake.tips) == 20
    kinds = [(r["kind"], r["title"] or r["key"]) for r in rows(b)]
    assert [t for k, t in kinds if k == "section"] == ["Start here", "Work", "Code", "This machine"]
    assert kinds[0] == ("section", "Start here")
    assert len(rows(b)) == 20 + 4
    # Inside a section, the fixed order the table gave.
    start = [t["id"] for t in b.tips if t["section"] == "start"]
    assert start == ["make-an-app", "picture", "esc", "timer", "machine", "bang"]
    assert ids(b)[:6] == start


def test_the_first_tip_is_in_front_and_the_list_says_where_it_is(agentd, backend):
    agentd()
    b = backend()
    assert up(b)
    assert b.selected == "make-an-app"
    assert b.selectedRow == 1                      # row 0 is the heading
    tip = b.selectedTip
    assert tip["id"] == "make-an-app" and tip["words"] == "Make me a ticketing app"
    assert tip["line"] and tip["captions"] and tip["beats"]


def test_it_shows_only_what_agentd_lists(agentd, backend, home):
    few = lab.listing(home, facts=tips_mod.Facts(ai="Claude"))
    assert 0 < len(few) < 20
    agentd(few)
    b = backend()
    assert up(b)
    assert ids(b) == [t["id"] for t in few]
    shown = {r["key"] for r in rows(b) if r["kind"] == "tip"}
    for gone in ("change-app", "open-app", "tests", "undo", "keep"):      # gates this machine does not meet
        assert gone not in shown


def test_a_section_with_nothing_to_show_has_no_heading(agentd, backend, home):
    agentd([t for t in lab.listing(home) if t["section"] != "code"])
    b = backend()
    assert up(b)
    assert [r["title"] for r in rows(b) if r["kind"] == "section"] == ["Start here", "Work", "This machine"]


def test_an_empty_list_is_connected_with_nothing_to_show(agentd, backend):
    agentd([])
    b = backend()
    assert lab.wait_until(lambda: b.connected)
    assert b.count == 0 and b.selected == "" and b.selectedRow == -1 and b.selectedTip == {}
    assert rows(b) == []


def test_new_tips_are_in_new_alone_after_the_sections(agentd, backend, home):
    agentd(lab.listing(home, new=("bang", "undo")))
    b = backend()
    assert up(b)
    sections = [r["title"] for r in rows(b) if r["kind"] == "section"]
    assert sections == ["Start here", "Work", "Code", "This machine", "New"]
    under_new = [r["key"] for r in rows(b)[-3:]]
    assert under_new == ["section:new", "bang", "undo"]
    keys = [r["key"] for r in rows(b) if r["kind"] == "tip"]
    assert len(keys) == len(set(keys)) == 20                    # each tip once
    assert ids(b)[-2:] == ["bang", "undo"]                       # Down goes through them last


def test_a_tip_that_was_new_stays_in_new_while_the_app_is_open(agentd, backend, home):
    """Asking is looking: agentd makes what it listed known, and the next list no longer says new."""
    fake = agentd(lab.listing(home, new=("bang",)))
    b = backend()
    assert up(b)
    assert rows(b)[-1]["key"] == "bang"
    fake.tips = lab.listing(home)
    b.refresh()
    assert lab.wait_until(lambda: len(fake.of("tips", "list")) >= 2)
    lab.spin(60)
    assert [r["title"] for r in rows(b) if r["kind"] == "section"][-1] == "New"
    assert rows(b)[-1]["key"] == "bang"


def test_a_tip_with_only_marks_shows_its_marks_where_its_words_would_be(agentd, backend):
    agentd()
    b = backend()
    assert up(b)
    row = next(r for r in rows(b) if r["key"] == "esc")
    assert row["words"] == "Esc"
    tip = next(t for t in b.tips if t["id"] == "esc")
    assert tip["words"] == "" and tip["marks"] == ["Esc"]


def test_the_rows_a_list_is_made_of_carry_what_the_window_reads(agentd, backend):
    agentd()
    b = backend()
    assert up(b)
    roles = {bytes(v).decode() for v in b.model.roleNames().values()}
    assert roles == {"kind", "key", "title", "line", "words", "marks", "tried"}
    heading, first = rows(b)[0], rows(b)[1]
    assert heading["kind"] == "section" and heading["line"] == ""
    assert first["kind"] == "tip" and first["line"] and first["words"]


# ---- choosing a tip --------------------------------------------------------------------------------------------

def test_move_goes_through_the_tips_in_the_order_shown_and_stops_at_both_ends(agentd, backend):
    agentd()
    b = backend()
    assert up(b)
    order = ids(b)
    b.move(-1)
    assert b.selected == order[0]                    # not past the first
    seen = [b.selected]
    for _ in range(len(order) + 3):
        b.move(1)
        if b.selected != seen[-1]:
            seen.append(b.selected)
    assert seen == order                              # every tip once, then the end holds
    assert b.selected == order[-1]
    b.move(-1)
    assert b.selected == order[-2]


def test_selecting_says_so_once_and_only_when_it_is_a_different_tip(agentd, backend):
    agentd()
    b = backend()
    assert up(b)
    changed, rowed = [], []
    b.selectedChanged.connect(lambda: changed.append(b.selected))
    b.selectedRowChanged.connect(lambda: rowed.append(b.selectedRow))
    b.select("timer")
    b.select("timer")
    b.move(-1)
    b.select("not-a-tip")
    b.select("")
    assert changed == ["timer", "esc"]
    assert rowed == [4, 3]
    assert b.selected == "esc"


def test_the_tip_in_front_is_already_the_new_one_when_the_window_is_told(agentd, backend):
    """The window reads `selectedTip` when `selected` changes: the row has to be current by then."""
    agentd()
    b = backend()
    assert up(b)
    seen = []
    b.selectedChanged.connect(lambda: seen.append(b.selectedTip["id"]))
    b.select("bang")
    b.move(-1)
    assert seen == ["bang", "machine"]


def test_a_refresh_that_changes_nothing_does_not_reset_the_list_or_replay_the_tip(agentd, backend):
    fake = agentd()
    b = backend()
    assert up(b)
    b.select("timer")
    resets, changed, plays = [], [], []
    b.model.modelReset.connect(lambda: resets.append(1))
    b.model.dataChanged.connect(lambda *_: changed.append(1))
    b.selectedChanged.connect(lambda: plays.append(b.selected))
    b.refresh()
    assert lab.wait_until(lambda: len(fake.of("tips", "list")) >= 2)
    lab.spin(80)
    assert resets == [] and changed == [] and plays == []
    assert b.selected == "timer"


def test_the_tip_in_front_goes_back_to_the_first_when_it_is_no_longer_listed(agentd, backend):
    fake = agentd()
    b = backend()
    assert up(b)
    b.select("disk")
    fake.tips = [t for t in fake.tips if t["id"] != "disk"]
    b.refresh()
    assert lab.wait_until(lambda: "disk" not in ids(b))
    assert b.selected == "make-an-app"


# ---- the words a button puts in the pill ----------------------------------------------------------------------

@pytest.mark.parametrize("words,draft", [
    ("Make me a ticketing app", "Make me a ticketing app"),
    ("In Tickets, add…", "In Tickets, add "),               # the dots go, the cursor waits after a space
    ("  In   Tickets,\tadd…  ", "In Tickets, add "),
    ("!uname -r", "!uname -r"),
    ("Where did my disk go?", "Where did my disk go?"),
    ("…", " "),
    ("", ""),
    (None, ""),
])
def test_draft_of_keeps_the_exact_words_except_a_trailing_ellipsis(mod, words, draft):
    assert mod.draft_of(words) == draft


def test_the_draft_of_a_listed_tip_is_its_words_and_of_anything_else_nothing(agentd, backend):
    agentd()
    b = backend()
    assert up(b)
    assert b.draftOf("make-an-app") == "Make me a ticketing app"
    assert b.draftOf("change-app") == "In Tickets, add "
    assert b.draftOf("esc") == ""                    # marks only: no button, nothing to put in the pill
    assert b.draftOf("nope") == ""


# ---- ticks: from the list, and from tips.json as agentd writes it -------------------------------------------

def test_a_tip_agentd_lists_as_tried_has_its_tick(agentd, backend, home):
    agentd(lab.listing(home, tried=("make-an-app", "picture")))
    b = backend()
    assert up(b)
    assert {r["key"] for r in rows(b) if r["kind"] == "tip" and r["tried"]} == {"make-an-app", "picture"}
    assert next(t for t in b.tips if t["id"] == "picture")["tried"] is True
    assert next(t for t in b.tips if t["id"] == "timer")["tried"] is False


def test_a_tip_tried_while_the_app_is_open_ticks_from_tips_json_and_the_list_keeps_its_place(
        agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    lab.write_state(state, tried=[])
    b = backend()
    assert up(b)
    b.select("machine")
    resets, ticked, front = [], [], []
    b.model.modelReset.connect(lambda: resets.append(1))
    b.model.dataChanged.connect(lambda top, bottom, *_: ticked.append((top.row(), bottom.row())))
    b.selectedChanged.connect(lambda: front.append(b.selected))
    # What agentd does when the words a tip summoned are sent: it remembers, and writes the file.
    fake.tips = lab.listing(home, tried=("esc",))
    lab.write_state(state, tried=["esc"])
    assert lab.wait_until(lambda: next(r for r in rows(b) if r["key"] == "esc")["tried"])
    lab.spin(200)
    assert resets == []                                # the list was not rebuilt, so it kept its scroll
    assert (3, 3) in ticked                             # the row of "esc" changed and no other
    assert {t for t, _ in ticked} == {3}
    assert front == [] and b.selected == "machine"      # nothing replays, nothing moves
    assert next(t for t in b.tips if t["id"] == "esc")["tried"] is True
    # What a turn tried may have changed what the machine can back: the app asks again.
    assert len(fake.of("tips", "list")) >= 2


def test_a_tick_shows_before_the_list_is_asked_again(agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    lab.write_state(state, tried=[])
    b = backend()
    assert up(b)
    fake.mode = "silent"                                # agentd is slow: the file is the quick way to know
    lab.write_state(state, tried=["timer"])
    assert lab.wait_until(lambda: next(r for r in rows(b) if r["key"] == "timer")["tried"])


def test_a_tip_that_is_no_longer_tried_loses_its_tick_with_the_file(agentd, backend, home, state):
    fake = agentd(lab.listing(home, tried=("timer",)))
    lab.write_state(state, tried=["timer"])
    b = backend()
    assert up(b)
    assert next(r for r in rows(b) if r["key"] == "timer")["tried"]
    fake.tips = lab.listing(home)
    lab.write_state(state, tried=[])
    assert lab.wait_until(lambda: not next(r for r in rows(b) if r["key"] == "timer")["tried"])


def test_a_state_file_that_is_not_what_agentd_writes_changes_nothing(agentd, backend, home, state):
    agentd(lab.listing(home, tried=("timer",)))
    b = backend()
    assert up(b)
    for text in ("", "{", "[]", "null", '{"tried": "timer"}', '{"tried": [1, null, "TIMER", "../x", "timer"]}',
                 '{"open_on": 7}', '{"open_on": "Not An Id"}'):
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(text)
        lab.spin(120)
        assert b.connected and b.selected == "make-an-app"
    assert next(r for r in rows(b) if r["key"] == "timer")["tried"] is True


def test_the_app_never_writes_tips_json(agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    b = backend()
    assert up(b)
    assert not state.exists() and not state.parent.exists()      # no file, no folder made for one
    lab.write_state(state, tried=["timer"], open_on="esc")
    before = (state.read_bytes(), state.stat().st_mtime_ns)
    b.select("disk")
    b.move(1)
    b.refresh()
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 1)
    lab.spin(400)
    assert (state.read_bytes(), state.stat().st_mtime_ns) == before
    assert sorted(p.name for p in state.parent.iterdir()) == ["tips.json"]      # no temporary file either


# ---- the press on the desk card -----------------------------------------------------------------------------------

def test_the_app_opens_on_the_tip_the_card_named_and_says_it_read_it(agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    lab.write_state(state, open_on="timer")
    b = backend()
    assert up(b)
    assert b.selected == "timer"
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 1)
    assert fake.of("tips", "opened") == [{"type": "tips", "op": "opened", "id": "timer"}]
    # It said so on a connection of its own, and the connection that asked for the list asked for nothing else.
    asked = [c for c in fake.by_connection if any(m.get("op") == "list" for m in c)]
    assert asked and all(len(c) == 1 for c in asked)


def test_a_press_while_the_app_is_open_shows_that_tip_and_the_same_press_again_plays_it_again(
        agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    lab.write_state(state, open_on="")
    b = backend()
    assert up(b)
    front, again = [], []
    b.selectedChanged.connect(lambda: front.append(b.selected))
    b.openRequested.connect(again.append)
    lab.write_state(state, open_on="disk")
    assert lab.wait_until(lambda: b.selected == "disk")
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 1)
    assert front == ["disk"] and again == []
    # agentd clears the request when it hears "opened"; the next press on the same tip is a request again.
    lab.write_state(state, open_on="")
    lab.spin(150)
    lab.write_state(state, open_on="disk")
    assert lab.wait_until(lambda: again == ["disk"])
    assert front == ["disk"]                          # the tip did not change: the window plays it from the start
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 2)


def test_the_same_open_on_read_again_does_not_move_the_window_back(agentd, backend, home, state):
    """agentd clears `open_on` when it hears "opened", but an agentd that did not (an older one, or the line was
    lost) leaves it: the next look at the file must not take the person back to that tip."""
    fake = agentd(lab.listing(home))
    lab.write_state(state, open_on="disk")
    b = backend()
    assert up(b)
    assert b.selected == "disk"
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 1)
    b.select("timer")                                  # the person went somewhere else
    asked = []
    b.openRequested.connect(asked.append)
    lab.write_state(state, tried=["esc"], open_on="disk")        # a tick: the file changes, `open_on` is as it was
    assert lab.wait_until(lambda: next(r for r in rows(b) if r["key"] == "esc")["tried"])
    lab.spin(300)
    assert b.selected == "timer" and asked == []
    assert len(fake.of("tips", "opened")) == 1                   # and it did not say it read a request again


def test_a_request_for_a_tip_that_is_not_listed_is_read_and_changes_nothing(agentd, backend, home, state):
    fake = agentd(lab.listing(home, facts=tips_mod.Facts(ai="Claude")))
    b = backend()
    assert up(b)
    lab.write_state(state, open_on="keep")           # a tip this machine cannot back
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 1)
    assert b.selected == "make-an-app"
    assert fake.of("tips", "opened")[0]["id"] == "keep"


def test_a_request_that_comes_before_the_list_waits_for_it(agentd, backend, home, state):
    fake = agentd(start=False)
    lab.write_state(state, open_on="disk")
    b = backend()
    lab.spin(150)
    assert not b.connected and b.selected == ""
    fake.start()
    assert lab.wait_until(lambda: b.connected and b.selected == "disk")
    assert lab.wait_until(lambda: len(fake.of("tips", "opened")) == 1)


# ---- one-shot connections and nothing else ------------------------------------------------------------------------

def test_every_connection_carries_one_line_and_is_closed_after_it(agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    lab.write_state(state, tried=[], open_on="disk")
    b = backend()
    assert up(b)
    for _ in range(3):
        b.refresh()
        lab.spin(60)
    lab.write_state(state, tried=["timer"])
    lab.wait_until(lambda: len(fake.of("tips", "list")) >= 3)
    settle(fake)
    assert fake.connections >= 3
    assert all(len(c) <= 1 for c in fake.by_connection)
    assert {m["op"] for m in fake.lines} <= {"list", "opened"}
    assert all(m["type"] == "tips" for m in fake.lines)
    assert fake.open == 0
    lab.spin(500)                                      # idle: nothing is held open, nothing is asked
    seen = fake.connections
    lab.spin(300)
    assert fake.open == 0 and fake.connections == seen


def test_a_request_made_while_one_is_out_is_asked_once_more_and_not_once_each(agentd, backend):
    fake = agentd()
    fake.hooks[("tips", "list")] = lambda _m: time.sleep(0.15)
    b = backend()
    assert up(b)
    settle(fake)
    before = len(fake.of("tips", "list"))
    for _ in range(6):
        b.refresh()                                    # the first goes out; the other five are one more, together
    assert lab.wait_until(lambda: len(fake.of("tips", "list")) - before == 2)
    lab.spin(500)
    assert len(fake.of("tips", "list")) - before == 2


def test_a_folder_that_is_watched_needs_no_clock_and_one_that_is_not_there_yet_is_looked_at_until_it_is(
        mod, monkeypatch, agentd, backend, home, state):
    """The watcher says when tips.json changes. A clock under it would wake an idle machine for as long as the
    window is kept, so it runs only while there is nothing to watch (the state folder is not made yet)."""
    monkeypatch.setattr(mod, "WATCH_MS", 60)
    agentd()
    assert not state.parent.exists()
    b = backend()
    assert b._net.isActive() is True                   # nothing to watch yet: look every WATCH_MS
    lab.write_state(state, tried=["timer"])            # the folder and the file appear
    assert lab.wait_until(lambda: b._net.isActive() is False)    # watched now: no clock
    assert lab.wait_until(lambda: b._seen_tried == frozenset({"timer"}))
    # From here the watcher alone carries every change, with no clock to be the reason a tick shows.
    lab.write_state(state, tried=["timer", "esc"])
    assert lab.wait_until(lambda: b._seen_tried == frozenset({"timer", "esc"}))
    assert b._net.isActive() is False
    b2 = backend()                                      # a folder that is there from the start: no clock at all
    assert b2._net.isActive() is False


def test_a_check_makes_no_connection_and_reads_nothing(mod, monkeypatch, agentd, backend, home, state):
    fake = agentd()
    lab.write_state(state, open_on="timer", tried=["esc"])
    monkeypatch.setattr(mod, "CHECKING", True)
    b = backend()
    b.refresh()
    lab.spin(400)
    assert fake.connections == 0
    assert not b.connected and b.count == 0 and b.selected == ""


# ---- agentd away and back ------------------------------------------------------------------------------------------

def test_it_says_so_while_agentd_is_away_and_keeps_trying_until_it_is_back(agentd, backend):
    fake = agentd(start=False)
    b = backend()
    lab.spin(350)                                      # several tries on a 120 ms clock
    assert not b.connected and b.count == 0
    fake.start()
    assert lab.wait_until(lambda: b.connected and b.count == 20)
    assert b.selected == "make-an-app"
    asked = len(fake.of("tips", "list"))
    lab.spin(400)                                      # it stops asking once it has the list
    assert len(fake.of("tips", "list")) == asked


def test_an_agentd_that_hangs_up_or_says_nothing_is_away_and_nothing_is_left_open(agentd, backend, mod):
    fake = agentd()
    fake.mode = "hangup"
    b = backend()
    lab.spin(300)
    assert not b.connected
    fake.mode = "silent"
    seen = fake.connections
    most = 0
    end = time.monotonic() + 6
    while time.monotonic() < end and fake.connections <= seen + 1:   # it gives up on one connection, tries another
        lab.spin(15)
        most = max(most, len(out_now(b, mod)))
    assert fake.connections > seen + 1
    assert not b.connected
    assert most <= 1                                   # and never has two out at once (counted here, not by the fake)
    fake.mode = "answer"
    assert lab.wait_until(lambda: b.connected and b.count == 20, 6000)
    settle(fake)


def test_an_agentd_that_takes_the_line_and_says_nothing_is_not_asked_back_to_back(agentd, backend, mod):
    """Nothing at the socket fails at once and is looked for often. Something that listens and says nothing holds a
    socket for the whole wait each time, so after one silent wait the next try is QUIET_MS away, and a request the
    clock or a tick made while that one was out is not started the moment it ends."""
    fake = agentd()
    fake.mode = "silent"
    b = backend()
    assert lab.wait_until(lambda: fake.connections == 1)
    b.refresh()                                         # a tick while the request is out: it is not a reason to
    lab.spin(1300)                                      # start another the moment this one lapses (at 600 ms)
    assert not b.connected and fake.connections == 1
    assert len(out_now(b, mod)) == 0                    # and between the tries nothing is held open
    fake.mode = "answer"
    assert lab.wait_until(lambda: b.connected and b.count == 20, 5000)    # the next try, QUIET_MS after the first
    assert lab.wait_until(lambda: b._retry.isActive() is False)


def test_agentd_with_nothing_to_list_answers_and_the_app_holds_nothing(agentd, backend, mod):
    """Tips off in agentd: it answers the list with an empty one. The app is connected, has nothing to show, and
    does not keep a socket or a clock going."""
    fake = agentd([])
    b = backend()
    assert lab.wait_until(lambda: b.connected)
    assert b.count == 0 and b.selected == "" and rows(b) == []
    seen = fake.connections
    lab.spin(500)
    assert fake.connections == seen == 1 and out_now(b, mod) == [] and b._retry.isActive() is False
    settle(fake)


def test_looking_asks_once_and_finds_out_that_agentd_went_away_after_the_list(agentd, backend, mod):
    """The window asks each time it comes forward. Once the list has come nothing else asks, so an agentd that
    went away is found out by the look, and the window says so (and tries again until it is back)."""
    fake = agentd()
    b = backend()
    assert up(b)
    assert b._retry.isActive() is False
    fake.stop()
    lab.spin(400)
    assert b.connected                                  # nobody has looked: a clock would be the only other way
    b.look()
    assert lab.wait_until(lambda: not b.connected)
    assert b._retry.isActive() is True
    fake.start()
    assert lab.wait_until(lambda: b.connected and b.count == 20)
    assert b._retry.isActive() is False


def test_a_look_just_after_the_list_came_asks_nothing(mod, monkeypatch, agentd, backend):
    """The window starts and is shown in the same moment, and comes forward again after a click elsewhere: an
    answer that is a moment old is as new as a look would be."""
    monkeypatch.setattr(mod, "FRESH_MS", 700)
    fake = agentd()
    b = backend()
    assert up(b)
    asked = len(fake.of("tips", "list"))
    b.look()
    b.look()
    lab.spin(200)
    assert len(fake.of("tips", "list")) == asked
    lab.spin(600)                                       # a look after that moment asks
    b.look()
    assert lab.wait_until(lambda: len(fake.of("tips", "list")) == asked + 1)


def test_looking_while_a_request_is_out_asks_nothing_more(agentd, backend, mod):
    fake = agentd()
    fake.hooks[("tips", "list")] = lambda _m: time.sleep(0.15)
    b = backend()
    assert up(b)
    settle(fake)
    before = len(fake.of("tips", "list"))
    for _ in range(6):
        b.look()                                        # the first asks; the rest find it out, and are its answer
    assert lab.wait_until(lambda: len(fake.of("tips", "list")) - before >= 1)
    lab.spin(800)                                       # (one more would start when the first is answered, 150 ms on)
    assert len(fake.of("tips", "list")) - before == 1


def test_the_tip_in_front_survives_agentd_going_away_and_coming_back(agentd, backend, home, state):
    fake = agentd(lab.listing(home))
    lab.write_state(state, tried=[])
    b = backend()
    assert up(b)
    b.select("disk")
    fake.stop()
    lab.write_state(state, tried=["timer"])           # a change in the file makes the app ask, and nobody answers
    assert lab.wait_until(lambda: not b.connected)
    assert b.selected == "disk"
    fake.tips = lab.listing(home, tried=("timer",))
    fake.start()
    assert lab.wait_until(lambda: b.connected)
    assert b.selected == "disk" and b.count == 20
    assert next(r for r in rows(b) if r["key"] == "timer")["tried"]


def test_the_connected_state_says_when_it_changes_and_not_otherwise(agentd, backend):
    fake = agentd(start=False)
    b = backend()
    flips = []
    b.connectedChanged.connect(lambda: flips.append(b.connected))
    lab.spin(300)
    assert flips == []                                 # away, and still away
    fake.start()
    assert lab.wait_until(lambda: flips == [True])
    lab.spin(200)
    assert flips == [True]


# ---- the limits of a line ---------------------------------------------------------------------------------------------

def test_what_agentd_could_not_have_meant_is_dropped(agentd, backend, home):
    good = lab.listing(home)[:3]
    long_line = "x" * 500
    junk = [
        "text", 7, None, [], {},
        {"id": "Capital", "section": "start", "line": "a line"},
        {"id": "../etc", "section": "start", "line": "a line"},
        {"id": "ok-id", "section": "unknown", "line": "a line"},
        {"id": "ok-id", "section": "start", "line": "   "},
        {"id": "ok-id", "section": "start"},
        {"id": 5, "section": "start", "line": "a line"},
        dict(good[0]),                                  # the same id twice
        {"id": "long", "section": "work", "line": long_line, "words": long_line, "marks": ["m"] * 9 + [3, " "],
         "captions": ["a", "b", "c", "d"], "beats": [{"hold": 1}, "nope"], "order": "x"},
    ]
    agentd(good[:2] + junk + good[2:])
    b = backend()
    assert up(b)
    assert ids(b) == [t["id"] for t in good] + ["long"]
    long = next(t for t in b.tips if t["id"] == "long")
    assert len(long["line"]) == 200 and len(long["words"]) == 300
    assert long["marks"] == ["m"] * 4
    assert long["captions"] == ["a", "b", "c"]
    assert long["beats"] == [{"hold": 1}]
    assert long["order"] == 0 and long["card"] is True and long["tried"] is False


def test_the_sections_are_the_ones_agentd_has(mod):
    """The app keeps its own copy of the sections (it is standalone): a section added to the table and not here
    would drop every tip of it from the window without a word, so the two are held to each other."""
    assert list(mod.SECTIONS) == list(tips_mod.SECTIONS.items())


def test_an_entry_of_the_wrong_kinds_is_dropped_or_cut_and_never_raises(mod):
    base = {"id": "a-tip", "section": "start", "line": "a line"}
    for field, wrong in (("marks", 5), ("marks", "Esc"), ("marks", {"a": 1}), ("captions", "abc"), ("captions", 3),
                         ("beats", "x"), ("beats", 7), ("beats", {"hold": 1}), ("words", ["a"]), ("order", "3"),
                         ("card", "no"), ("tried", 1), ("new", "yes")):
        tip = mod.clean({**base, field: wrong})
        assert tip is not None, (field, wrong)
        assert tip["marks"] == [] and tip["captions"] == [] and tip["beats"] == [], (field, wrong)
        assert tip["words"] == "" and tip["order"] == 0 and tip["card"] is True and tip["tried"] is False
        assert tip["new"] is False
    for section in ([], {}, ["start"], 3, None, 1.5):             # not a name, and not always hashable
        assert mod.clean({**base, "section": section}) is None, section


def test_text_is_plain_one_line_and_never_markup(mod):
    tip = mod.clean({"id": "a-tip", "section": "start", "line": "<b>one</b>\n  two\tthree", "words": " a\n b "})
    assert tip["line"] == "<b>one</b> two three" and tip["words"] == "a b"


def test_a_list_that_is_not_a_list_is_not_an_answer(agentd, backend):
    fake = agentd()
    fake.tips = "nope"
    b = backend()
    lab.spin(300)
    assert not b.connected                              # the answer must be a list; the one-shot waits for one


def test_the_socket_and_the_state_follow_the_environment(mod, monkeypatch, tmp_path):
    monkeypatch.delenv("BOMBADIL_SOCKET", raising=False)
    monkeypatch.setenv("BOMBADIL_RUNTIME", str(tmp_path / "rt"))
    assert mod.agent_socket() == str(tmp_path / "rt" / "agentd.sock")
    monkeypatch.setenv("BOMBADIL_SOCKET", str(tmp_path / "s.sock"))
    assert mod.agent_socket() == str(tmp_path / "s.sock")
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "st"))
    assert mod.tips_file() == tmp_path / "st" / "tips.json"
    monkeypatch.delenv("BOMBADIL_STATE")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert mod.tips_file() == tmp_path / "xdg" / "bombadil" / "tips.json"


def test_the_state_path_is_the_one_agentd_writes(mod, home):
    from bombadil import paths
    assert mod.tips_file() == paths.tips_file()


def test_nothing_in_the_app_writes_a_file():
    """The one writer of tips.json is agentd: the app's source has no way to write a file."""
    src = (lab.APP_DIR / "app.py").read_text()
    for word in (".write_text", ".write_bytes", "open(", "os.replace", "os.rename", "shutil", "json.dump("):
        assert word not in src, word

"""Fixes to the service's ops and to what it tells him.

The window opens from the app that ships; a problem he said Not now to is counted and comes back; two
sends at once make one page; a word that cannot be noted is not left live; an app turn that made nothing
puts its ask back; a put-away word can be put back; an offer is only made when it can be done; the
already-reported answer says so and opens that issue. Shares the rig and the fixtures of
test_loop_service.py.
"""

import asyncio
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
import golden_corpus as gc
import test_loop_service as base
from test_loop_service import (
    DAY,
    NOW,
    Clock,
    a_finding,
    finding_state,
    make_the_app,
    passwords_asks,
    plant,
    run_asks,
    the_offer,
    words_file,
)

from bombadil import apps, paths
from bombadil.loop import db, findings, forms, probes, report, words
from bombadil.loop.findings import FindingsStore
from bombadil.loop.habits import Group
from bombadil.loop.store import LoopStore

machine = base.machine      # the fixtures of the service tests, found here by name
rig_of = base.rig_of


def finds() -> FindingsStore:
    """The prober's own connection to loop.db, from the test's thread."""
    return FindingsStore(db.connect(paths.loop_db()))


# -- the Noticed window opens from the app that ships (not only from ~/Apps) --

@pytest.mark.asyncio
async def test_send_to_the_project_opens_the_window_from_the_app_that_ships(rig_of):
    rig = rig_of()
    found = plant()
    await rig.start()
    await rig.ask_state()
    assert not (paths.apps_dir() / "noticed").exists()
    seen = await rig.do("report", found.fp)
    assert seen["ok"] and seen["text"] == (
        "The report is ready. Nothing is sent until you press Submit on the page.")
    assert rig.agent.launcher.ran == [("app", "noticed", "open")]


@pytest.mark.asyncio
async def test_a_report_whose_window_will_not_open_says_so_and_still_carries_the_preview(rig_of, monkeypatch):
    monkeypatch.setattr(apps, "builtin_dir", lambda: paths.state_dir() / "no-apps")
    rig = rig_of()
    found = plant()
    await rig.start()
    await rig.ask_state()
    seen = await rig.do("report", found.fp)
    assert seen["ok"] is True and "window would not open" in seen["text"] and "“noticed”" in seen["text"]
    assert seen["preview"]["goes"] and finding_state(found.fp) == ["reported"]
    assert rig.agent.launcher.ran == []


@pytest.mark.asyncio
async def test_his_own_copy_of_noticed_opens_as_it_always_did(rig_of):
    apps.create("Noticed", "import QtQuick\nItem {}\n")
    rig = rig_of()
    await rig.start()
    assert (await rig.do("open"))["ok"] and rig.agent.launcher.ran == [("app", "noticed", "open")]


# -- Not now on a problem it found: counted, quiet, listed, and it comes back --

def at_t(n, t0=NOW):
    """The time of the n-th sighting: a day apart, so each is its own episode."""
    return t0 + n * DAY


def test_a_dismissed_finding_keeps_counting_and_comes_back_when_it_has_happened_twice_as_many_times(home):
    store = FindingsStore()
    for n in range(3):
        found = store.record(a_finding(), at_t(n))
    store.mark(found.fp, "dismissed", at_t(2))
    assert store.record(a_finding(), at_t(3)) is None and store.record(a_finding(), at_t(4)) is None
    quiet = store.get(found.fp)
    assert quiet.state == "dismissed" and quiet.n == 5 and quiet.days == 5      # counted, not raised
    assert store.open_findings() == []
    woke = store.record(a_finding(), at_t(5))                                    # 6 is twice 3
    assert woke.state == "open" and woke.n == 6 and [f.fp for f in store.open_findings()] == [found.fp]
    assert store.said_no() == []


def test_a_dismissed_finding_comes_back_after_30_days_even_when_the_count_has_not_doubled(home):
    store = FindingsStore()
    for n in range(3):
        found = store.record(a_finding(), at_t(n))
    store.mark(found.fp, "dismissed", at_t(2))
    assert store.record(a_finding(), at_t(2) + 29 * DAY) is None                 # 4 of 6, 29 days
    assert store.get(found.fp).state == "dismissed"
    woke = store.record(a_finding(), at_t(2) + 31 * DAY)                         # 5 of 6, but 31 days
    assert woke.state == "open" and woke.n == 5


def test_a_state_that_is_still_red_when_a_not_now_is_over_is_raised_again(home, monkeypatch):
    monkeypatch.setattr(findings, "SNOOZE_DAYS", 0.01)                           # 14 minutes
    store = FindingsStore()
    red = a_finding(kind="invariant")
    found = store.record(red, NOW)
    store.mark(found.fp, "dismissed", NOW + 60)
    assert store.record(red, NOW + 600) is None                                  # the same episode, too soon
    assert store.get(found.fp).state == "dismissed"
    woke = store.record(red, NOW + 1300)                                         # still the same episode
    assert woke.state == "open" and woke.n == 1


def test_never_is_not_counted_and_not_now_is_a_note_that_bring_back_removes(home):
    store = FindingsStore()
    found = store.record(a_finding(), NOW)
    store.mark(found.fp, "never", NOW + 5)
    assert store.record(a_finding(), at_t(40)) is None and store.get(found.fp).n == 1
    [(listed, said_at)] = store.said_no()
    assert listed.fp == found.fp and said_at == NOW + 5
    store.mark(found.fp, "dismissed", NOW + 9)
    assert store.said_no()[0][1] == NOW + 9
    assert store.mark(found.fp, "open").state == "open" and store.said_no() == []
    assert store.conn.execute("SELECT COUNT(*) FROM finding_nos").fetchone()[0] == 0
    assert store.mark("nothing:here:000000", "dismissed") is None
    assert store.conn.execute("SELECT COUNT(*) FROM finding_nos").fetchone()[0] == 0


def test_a_not_now_from_before_the_notes_were_kept_starts_its_rest_at_the_next_sighting(home):
    store = FindingsStore()
    found = store.record(a_finding(), NOW)
    store.conn.execute("UPDATE findings SET state='dismissed' WHERE fp=?", (found.fp,))
    assert store.record(a_finding(), at_t(2)) is None                            # not at once
    assert store.record(a_finding(), at_t(3)) is None and store.get(found.fp).n == 3
    assert store.record(a_finding(), at_t(2) + 31 * DAY).state == "open"


def test_a_loop_db_made_before_the_notes_gets_the_table(home):
    conn = db.connect(paths.loop_db())
    db.schema(conn, "findings", findings.SCHEMA[:1])
    store = FindingsStore(conn)
    assert store.conn.execute("SELECT COUNT(*) FROM finding_nos").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_not_now_on_a_found_problem_says_for_how_long_lists_it_and_brings_it_back(rig_of):
    rig = rig_of()
    found = plant()
    await rig.start()
    assert [r["kind"] for r in (await rig.ask_state())["rows"]] == ["found"]
    said = await rig.do("not_now", found.fp)
    assert said["ok"] and said["text"] == (
        "Okay. That will stay quiet for 30 days, or until it has happened twice as many times.")
    assert finding_state(found.fp) == ["dismissed"] and (await rig.ask_state())["rows"] == []
    full = await rig.full()
    assert full["found"] == [] and full["said_no"] == [
        {"id": found.fp, "title": found.title, "t": NOW, "form": ""}]


@pytest.mark.asyncio
async def test_a_found_problem_that_happens_again_comes_back_to_the_chip(rig_of):
    rig = rig_of()
    found = plant()
    await rig.start()
    await rig.do("not_now", found.fp)
    store = finds()
    woke = store.record(a_finding(), at_t(1))
    store.close()
    assert woke is not None and woke.fp == found.fp
    assert [r["id"] for r in (await rig.ask_state())["rows"]] == [found.fp]
    assert (await rig.full())["said_no"] == []


@pytest.mark.asyncio
async def test_bring_back_opens_a_problem_he_said_no_to_and_answers_plainly_otherwise(rig_of):
    rig = rig_of()
    a = plant(a_finding(id="bar-socket"))
    b = plant(a_finding(id="pill-stuck", observed="the pill did not clear"))
    await rig.start()
    await rig.ask_state()
    never = await rig.do("never", a.fp)
    assert never["ok"] and never["text"] == "Okay. That will not come up again."
    await rig.do("not_now", b.fp)
    full = await rig.full()
    assert {r["id"] for r in full["said_no"]} == {a.fp, b.fp} and full["found"] == []
    back = await rig.do("bring_back", a.fp)
    assert back["ok"] and back["text"] == "Okay. It is back in what Bombadil found."
    assert finding_state(a.fp) == ["open"] and finding_state(b.fp) == ["dismissed"]
    assert [r["id"] for r in (await rig.ask_state())["rows"]] == [a.fp]
    assert [r["id"] for r in (await rig.full())["said_no"]] == [b.fp]
    again = await rig.do("bring_back", a.fp)                                     # it is not said no to now
    assert again["ok"] is False and again["text"] == "That is not on the list any more."
    unknown = await rig.do("bring_back", "bar:nothing:000000")
    assert unknown["ok"] is False and unknown["text"] == "That is not on the list any more."


# -- two sends at once --

@pytest.mark.asyncio
async def test_two_sends_at_once_open_one_page_and_run_one_search(rig_of):
    opened, asked = [], []

    def search(url, timeout):
        asked.append(url)
        time.sleep(0.05)
        return {"items": []}
    rig = rig_of(opener=lambda url: opened.append(url) or "panel", fetcher=search)
    window = rig.agent.connect("window")
    found = plant()
    await rig.start()
    await rig.ask_state()
    await rig.do("report", found.fp)
    one, two = await asyncio.gather(rig.do("send", found.fp), rig.do("send", found.fp, client=window))
    assert sorted((one["ok"], two["ok"])) == [False, True]
    assert {one["text"], two["text"]} >= {"That is already being sent."}
    assert len(opened) == 1 and len(asked) == 1 and finding_state(found.fp) == ["sent"]
    assert rig.service._sending == set()


# -- a word and a database that will not take the note --

@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["note_word_made", "answer"])
async def test_a_word_is_not_left_live_when_the_notes_will_not_take_it(rig_of, monkeypatch, step):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    real = getattr(LoopStore, step)
    failed = []

    def locked(self, *args, **kwargs):
        if not failed and args and args[0] in ("gpw1", "my passwords"):          # this word's own note, once
            failed.append(True)
            raise sqlite3.OperationalError("database is locked")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(LoopStore, step, locked)
    first = await rig.do("accept", "gpw1", "word")
    assert failed and first["ok"] is False and first["text"] == "That did not work."
    assert words_file() == [] and rig.turns("improve") == []                     # nothing live, nothing half done
    assert rig.store().group("gpw1").state == "offered"
    second = await rig.do("accept", "gpw1", "word")                              # and a second tap can make it
    assert second["ok"] and second["text"] == "Made “my passwords” open Passwords."
    assert [w["phrase"] for w in words_file()] == ["my passwords"] and len(rig.turns("improve")) == 1
    assert rig.store().group("gpw1").state == "made"


# -- an app turn that made nothing, and one that was lost with agentd --

def an_app_turn_row(rig, sent, ok=True):
    rig.agent._log_line({"t": rig.clock(), "id": "t9-1", "prompt": sent["text"], "result": "ok" if ok else "no",
                         "ok": ok, "origin": "loop", "v": 2})


@pytest.mark.asyncio
async def test_an_app_turn_that_went_well_but_made_no_app_puts_the_ask_back_to_not_now(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    await rig.do("accept", row["id"], "app")
    [sent] = rig.agent.prompts
    assert rig.store().group(row["id"]).state == "made"
    an_app_turn_row(rig, sent)                                                   # it only answered in words
    await until_state(rig, row["id"], "not_now")
    assert rig.turns("improve") == []
    assert rig.store().conn.execute("SELECT COUNT(*) FROM service_state WHERE key LIKE 'app:%'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_an_app_turn_that_changed_an_app_of_his_stays_made_and_is_in_the_trail_without_an_undo(rig_of):
    rig = rig_of(run_asks(), clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    row = the_offer(await rig.ask_state())
    await rig.do("accept", row["id"], "app")
    [sent] = rig.agent.prompts
    apps.create("Runs", "import QtQuick\nItem { id: runs }\n")                   # the turn changed an app in place
    an_app_turn_row(rig, sent)
    await until_rows(rig, 1)
    [change] = rig.turns("improve")
    assert change["what"] == "app" and change["group"] == row["id"] and change["undo"] is None
    assert change["title"].startswith("Changed the app Runs from “log a ")
    assert rig.store().group(row["id"]).state == "made"
    [listed] = (await rig.full())["changes"]
    assert listed["can_undo"] is False and listed["undone"] is False


@pytest.mark.asyncio
async def test_an_app_turn_lost_with_agentd_puts_its_ask_back_at_the_next_start(rig_of):
    clock = Clock(gc.epoch(2, "12:00"))
    rig = rig_of(run_asks(), clock=clock)
    await rig.start()
    row = the_offer(await rig.ask_state())
    await rig.do("accept", row["id"], "app")
    assert rig.store().group(row["id"]).state == "made"
    rig.stop()                                                                   # agentd went away: no row came
    again = await rig_of(clock=clock).start()
    assert again.store().group(row["id"]).state == "not_now"
    assert again.store().conn.execute("SELECT COUNT(*) FROM service_state WHERE key LIKE 'app:%'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_an_app_that_was_made_stays_made_across_a_restart(rig_of):
    clock = Clock(gc.epoch(2, "12:00"))
    rig = rig_of(run_asks(), clock=clock)
    await rig.start()
    group, _ = await make_the_app(rig)
    rig.stop()
    again = await rig_of(clock=clock).start()
    assert again.store().group(group).state == "made"


async def until_state(rig, group, state):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if rig.store().group(group).state == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{group} never became {state}")


async def until_rows(rig, n):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if len(rig.turns("improve")) >= n:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no trail row")


# -- Put it back for a word the sweep put away --

@pytest.mark.asyncio
async def test_put_it_back_for_a_swept_word_puts_it_away_again_and_it_can_be_undone_once_more(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    await rig.service.tick()
    rig.clock.advance(29 * DAY)
    await rig.service.tick()
    swept = rig.turns("improve")[-1]
    assert swept["undo"]["op"] == "bring_back_word" and words_file()[0]["away"] is True
    standing = await rig.do("bring_back", swept["id"])                           # nothing was undone yet
    assert standing["ok"] is False and standing["text"] == (
        "That word is already put away, so there is nothing to put back.")
    assert (await rig.do("undo", swept["id"]))["ok"] and words_file()[0]["away"] is False
    back = await rig.do("bring_back", swept["id"])
    assert back["ok"] and back["text"] == "Put the word “my passwords” away again."
    assert words_file()[0]["away"] is True
    [change] = [c for c in (await rig.full())["changes"] if c["id"] == swept["id"]]
    assert change["undone"] is False and change["can_undo"] is True
    last = rig.turns("improve")[-1]
    assert last["of"] == swept["id"] and last["undone"] is False and last["what"] == "word"
    assert (await rig.do("undo", swept["id"]))["ok"] and words_file()[0]["away"] is False


@pytest.mark.asyncio
async def test_put_it_back_for_a_swept_word_that_is_gone_says_so(rig_of):
    rig = rig_of(passwords_asks())
    await rig.start()
    await rig.ask_state()
    await rig.do("accept", "gpw1")
    await rig.service.tick()
    rig.clock.advance(29 * DAY)
    await rig.service.tick()
    swept = rig.turns("improve")[-1]
    await rig.do("undo", swept["id"])
    words.remove("my passwords")
    back = await rig.do("bring_back", swept["id"])
    assert back["ok"] is False and back["text"] == "“my passwords” is not a word that is in use any more."


# -- an offer to make a word with no phrase is never made --

def tracker_group(**kw):
    kw = {"n": 3, "verbs": {"open": 3}, "routes": ["app:tracker", "opened"], "opens": "app:tracker",
          "label": "tracker weekly summary left", "named": ["app:tracker"], "word": "",
          "sentences": ["show me my tracker for the weekly summary on the left screen please"],
          "times": [NOW, NOW + DAY, NOW + 2 * DAY], **kw}
    return Group(id="g1", **kw)


def test_a_group_with_nothing_to_say_as_a_word_is_not_offered_as_one():
    g = tracker_group()
    assert forms.ideal(g) == "A" and forms.recommend(g) is None
    assert forms.other_ways(g, "D") == []
    with_word = tracker_group(word="my tracker")
    assert forms.recommend(with_word).form.letter == "A"


@pytest.mark.asyncio
async def test_asks_too_long_to_be_a_word_make_no_offer(rig_of):
    def ask(i, day, text):
        return {"id": f"lt{i}", "day": day, "at": "10:00", "text": text, "group": "tracker", "seconds": 5,
                "events": [["mcp__bombadil-os__open_app", {"name": "tracker"}]], "t": gc.epoch(day, "10:00")}
    asks = [ask(1, 0, "show me my tracker for the weekly summary on the left screen please"),
            ask(2, 1, "can you open my tracker for the weekly summary on the left screen"),
            ask(3, 2, "pull up my tracker for the weekly summary on the left screen")]
    rig = rig_of(asks, clock=Clock(gc.epoch(2, "12:00")))
    await rig.start()
    state = await rig.ask_state()
    assert the_offer(state) is None and rig.store().waiting() == 0
    assert [g.opens for g in rig.store().groups()] == ["app:tracker"]            # it was counted, just not offered


# -- the wording --

def test_no_check_names_the_ai_in_its_title():
    titles = [p.title for p in probes.PROBES.values()]
    assert titles and not [t for t in titles if re.search(r"\bAI\b", t)]


def test_what_the_window_says_it_lists_is_what_there_is():
    hint = (Path(__file__).resolve().parents[1] / "share" / "apps" / "noticed" / "ChangesSection.qml").read_text()
    assert "or fixed" not in hint


# -- already reported --

@pytest.mark.asyncio
async def test_a_problem_the_project_already_has_opens_its_issue_and_puts_a_line_on_the_clipboard(rig_of, monkeypatch):
    opened, copied = [], []
    monkeypatch.setattr(report, "copy_text", lambda text, timeout=3.0: copied.append(text) or True)
    found = plant()
    tag = f"[fp {found.fp.rsplit(':', 1)[-1]}]"
    rig = rig_of(opener=lambda url: opened.append(url) or "panel",
                 fetcher=lambda url, timeout: {"items": [{"number": 42, "state": "open", "title": f"Bar {tag}"}]})
    await rig.start()
    await rig.ask_state()
    await rig.do("report", found.fp)
    sent = await rig.do("send", found.fp)
    assert sent["ok"] and sent["text"] == (
        "Already reported (#42), so nothing new was sent. Its page is open. If you want to add that it happened "
        "again, a line for that is on the clipboard: paste it there.")
    assert opened == ["https://github.com/thedefaultman/Bombadil/issues/42"]
    [line] = copied
    assert line.startswith("Seen again: 1 time on 1 day")
    # The finding stays in the window, said to be the project's already.
    [entry] = (await rig.full())["found"]
    assert entry["state"] == "sent" and entry["can_send"] is False
    assert entry["meta"] == "1 time on 1 day · already reported as #42"
    assert rig.bar.last("noticed")["rows"] == []


@pytest.mark.asyncio
async def test_an_already_reported_problem_whose_page_will_not_open_is_still_said_plainly(rig_of):
    found = plant()
    tag = f"[fp {found.fp.rsplit(':', 1)[-1]}]"
    rig = rig_of(opener=lambda url: "",
                 fetcher=lambda url, timeout: {"items": [{"number": 7, "state": "closed", "title": f"Bar {tag}"}]})
    await rig.start()
    await rig.ask_state()
    await rig.do("report", found.fp)
    sent = await rig.do("send", found.fp)
    assert sent["ok"] and sent["text"] == "Already reported (#7), so nothing new was sent."
    assert finding_state(found.fp) == ["sent"]


@pytest.mark.asyncio
async def test_clearing_what_it_found_forgets_which_issue_it_was(rig_of, monkeypatch):
    monkeypatch.setattr(report, "copy_text", lambda text, timeout=3.0: False)
    found = plant()
    tag = f"[fp {found.fp.rsplit(':', 1)[-1]}]"
    rig = rig_of(opener=lambda url: "panel",
                 fetcher=lambda url, timeout: {"items": [{"number": 42, "state": "open", "title": f"Bar {tag}"}]})
    await rig.start()
    await rig.ask_state()
    await rig.do("report", found.fp)
    await rig.do("send", found.fp)

    def noted():
        return rig.store().conn.execute("SELECT COUNT(*) FROM service_state WHERE key LIKE 'issue:%'").fetchone()[0]
    assert noted() == 1
    assert (await rig.do("clear_found"))["ok"] and noted() == 0

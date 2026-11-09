"""Away's list and its card: what is worth a row since the person left, and what the message says.

Nothing here reads the real turn log or the clock: the rows, the sessions and the times are handed in.
"""

import json
import time

import pytest

from bombadil import away
from bombadil.away import Away, items, read_tail

NOW = 1_790_000_000.0       # 2026-09-21 14:13:20 UTC
LEFT = NOW - 3 * 3600


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def turn(t=LEFT + 600, prompt="install ffmpeg", ok=True, **more):
    return {"t": t, "prompt": prompt, "result": "All done.", "ok": ok, "snapshot": None, "summary": "",
            "stopped": False, "n": None, **more}


def job(t=LEFT + 900, target="Ubuntu ISO", result="Ubuntu ISO is done in 3 min.", ok=True, **more):
    return {"t": t, "kind": "local", "prompt": "background job", "action": "job", "target": target,
            "result": result, "ok": ok, **more}


def keys(found):
    return [i["key"] for i in found]


# -- the turns --

def test_a_turn_that_ended_while_away_is_finished_and_says_the_ask():
    (i,) = items([turn(n=7)], since=LEFT, now=NOW)
    assert (i["key"], i["kind"], i["title"], i["sub"]) == ("turn:7", "finished", "install ffmpeg", "")


def test_a_turn_that_changed_something_says_what_the_narrator_said():
    (i,) = items([turn(n=8, summary="Installed ffmpeg.", snapshot=41)], since=LEFT, now=NOW)
    assert (i["kind"], i["title"]) == ("changed", "Installed ffmpeg.")
    (i,) = items([turn(n=9, summary="Wrote a file.", files={"wrote": ["/home/x/a.txt"], "read": []})],
                 since=LEFT, now=NOW)
    assert i["kind"] == "changed"


def test_a_summary_with_nothing_written_and_no_restore_point_is_only_finished():
    (i,) = items([turn(summary="Looked at it.", files={"wrote": [], "read": ["/etc/hosts"]})], since=LEFT, now=NOW)
    assert i["kind"] == "finished" and i["title"] == "install ffmpeg"


def test_a_failed_turn_says_how_it_ended():
    (i,) = items([turn(ok=False, result="\n\nCould not reach the mirror.\nSecond line.")], since=LEFT, now=NOW)
    assert (i["kind"], i["sub"]) == ("failed", "Could not reach the mirror.")
    (i,) = items([turn(ok=False, result="")], since=LEFT, now=NOW)
    assert i["sub"] == "It did not finish."


@pytest.mark.parametrize("row", [
    turn(prompt="!ls -la"),                         # the person ran it themselves
    turn(prompt="[from app notes] summarize"),      # an app asked
    turn(stopped=True),                             # the person stopped it
    turn(requeued=True),                            # waiting for a limit to lift: it has not ended
    turn(ok=None),                                  # cut off
    turn(prompt="   "),
    turn(t=LEFT - 1),                               # before they left
    turn(t=LEFT),                                   # at the moment they left
    turn(t=NOW + 3600),                             # from the future
    {"t": LEFT + 100, "kind": "improve", "prompt": "tidy", "ok": True},   # the loop's rows are not turns
    {"t": LEFT + 100, "kind": "local", "action": "undo", "prompt": "undo", "ok": True},
])
def test_what_is_not_news_is_left_out(row):
    assert items([row], since=LEFT, now=NOW) == []


def test_a_title_is_cut_to_a_line():
    (i,) = items([turn(prompt="please " + "very " * 30 + "carefully tidy my downloads")], since=LEFT, now=NOW)
    assert len(i["title"]) == away.TITLE and i["title"].endswith("…")


def test_a_turn_with_no_number_is_keyed_by_when_it_ended():
    (i,) = items([turn(t=LEFT + 61.9)], since=LEFT, now=NOW)
    assert i["key"] == "turn:%d" % (LEFT + 61)
    (i,) = items([turn(n=True)], since=LEFT, now=NOW)     # a bool is not a number
    assert i["key"].startswith("turn:") and i["key"] != "turn:True"


# -- the jobs --

def test_a_job_that_ended_while_away_is_a_row_and_a_failed_one_can_say_why():
    ok, bad = items([job(job="a1"), job(target="Build", result="Build failed: exit 2", ok=False, job="b2")],
                    since=LEFT, now=NOW)[::-1]
    assert (ok["kind"], ok["key"], ok["sub"]) == ("finished", "job:a1", "Ubuntu ISO is done in 3 min.")
    assert "job" not in ok
    assert (bad["kind"], bad["key"], bad["job"], bad["title"]) == ("failed", "job:b2", "b2", "Build")


def test_a_job_the_person_stopped_is_not_news_and_other_local_rows_are_not_jobs():
    assert items([job(stopped=True, result="Stopped Ubuntu ISO.")], since=LEFT, now=NOW) == []
    assert items([{"t": LEFT + 5, "kind": "local", "action": "desk", "prompt": "at the desk", "ok": True}],
                 since=LEFT, now=NOW) == []


def test_a_job_row_with_no_id_is_keyed_by_when_it_ended():
    (i,) = items([job(ok=False)], since=LEFT, now=NOW)
    assert i["key"] == "job:%d" % (LEFT + 900) and "job" not in i      # nothing to ask Why? of


# -- the sessions --

def session(key="bombadil/reviewer", state="asked", **more):
    return {"key": key, "state": state, "alive": True, "unseen": False, "projectTitle": "Bombadil",
            "role": "reviewer", "last": "\nApply the migration?\nsecond", "since": NOW - 600, **more}


def test_a_session_that_asks_is_waiting_and_has_an_open_button_to_act_on():
    (i,) = items([], [session()], since=LEFT, now=NOW)
    assert (i["kind"], i["key"], i["session"], i["title"], i["sub"]) == (
        "waiting", "dev:bombadil/reviewer", "bombadil/reviewer", "reviewer on Bombadil", "Apply the migration?")


def test_a_session_that_finished_or_failed_unseen_is_a_row_and_a_seen_one_is_not():
    got = items([], [session("a", "done", unseen=True), session("b", "failed", unseen=True),
                     session("c", "done"), session("d", "failed"), session("e", "working", unseen=True),
                     session("f", "idle", unseen=True), session("g", "asleep", unseen=True)], since=LEFT, now=NOW)
    assert [(i["key"], i["kind"]) for i in got] == [("dev:b", "failed"), ("dev:a", "finished")]


def test_a_session_with_no_role_is_its_project_and_with_nothing_its_key():
    assert items([], [session(role="")], since=LEFT, now=NOW)[0]["title"] == "Bombadil"
    assert items([], [session(role="", projectTitle="")], since=LEFT, now=NOW)[0]["title"] == "bombadil/reviewer"
    assert items([], [session(role="", projectTitle="", project="notes")], since=LEFT, now=NOW)[0]["title"] == "notes"


@pytest.mark.parametrize("s", [None, "x", 3, {}, {"key": None, "state": "asked"}, {"key": "", "state": "asked"},
                               {"state": "asked"}])
def test_a_session_that_is_not_one_is_left_out(s):
    assert items([], [s], since=LEFT, now=NOW) == []


# -- the order, and what is not a row --

def test_the_order_is_failed_waiting_finished_changed_and_the_newest_first_in_each():
    rows = [turn(t=LEFT + 100, n=1, summary="Made a note.", snapshot=3), turn(t=LEFT + 200, n=2),
            turn(t=LEFT + 300, n=3), turn(t=LEFT + 400, n=4, ok=False), job(t=LEFT + 500, job="j")]
    got = items(rows, [session(since=LEFT + 250)], since=LEFT, now=NOW)
    assert keys(got) == ["turn:4", "dev:bombadil/reviewer", "job:j", "turn:3", "turn:2", "turn:1"]
    assert [i["kind"] for i in got] == ["failed", "waiting", "finished", "finished", "finished", "changed"]


def test_the_same_key_is_one_row_and_the_last_word_wins():
    got = items([turn(n=5, prompt="first"), turn(n=5, prompt="second", t=LEFT + 700)], since=LEFT, now=NOW)
    assert [i["title"] for i in got] == ["second"]


@pytest.mark.parametrize("junk", [None, "x", 4, [], {"t": "soon"}, {"t": float("nan")}, {"t": float("inf"), "prompt": "x"},
                                   {"prompt": "no time", "ok": True}, {"t": LEFT + 5, "prompt": 7, "ok": True}])
def test_a_row_it_cannot_make_sense_of_is_skipped_and_the_rest_still_count(junk):
    assert keys(items([junk, turn(n=1)], since=LEFT, now=NOW)) == ["turn:1"]


# -- reading the log --

def test_read_tail_gives_the_rows_oldest_first_and_drops_what_it_cannot_read(tmp_path):
    log = tmp_path / "turns.jsonl"
    log.write_text(json.dumps(turn(n=1)) + "\n" + "{not json\n" + "[1, 2]\n" + json.dumps(turn(n=2)) + "\n"
                   + '{"t": 3, "pro')     # cut by a crash
    assert [r["n"] for r in read_tail(log)] == [1, 2]


def test_read_tail_reads_only_the_end_and_never_a_row_cut_at_its_start(tmp_path):
    log = tmp_path / "turns.jsonl"
    rows = [turn(n=i, prompt="p" * 60) for i in range(1, 41)]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = read_tail(log, limit=1500)
    assert 3 <= len(got) < 40 and got[-1]["n"] == 40
    assert [r["n"] for r in got] == list(range(got[0]["n"], 41))     # whole rows, in order, to the end


def test_read_tail_of_nothing_is_nothing(tmp_path):
    assert read_tail(tmp_path / "none.jsonl") == []
    (tmp_path / "empty.jsonl").write_text("")
    assert read_tail(tmp_path / "empty.jsonl") == []


# -- the card --

@pytest.fixture
def card(tmp_path):
    log = tmp_path / "turns.jsonl"
    log.write_text("")
    a = Away(path=tmp_path / "away.json", log=log, clock=lambda: NOW)
    a.log = log
    return a


def write(a, *rows):
    a.log.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_nothing_happened_is_no_card_when_they_came_back_and_a_line_when_they_asked(card):
    assert card.back(LEFT) == {"type": "away", "since": 0, "count": 0, "why": "", "rows": []}
    assert card.up is False
    msg = card.ask(since=LEFT)
    assert card.up is True and msg["count"] == 0 and msg["since"] == LEFT
    assert msg["rows"] == [{"key": "none", "kind": "plain", "title": "Nothing happened", "sub": "", "tone": "you",
                            "button": ""}]
    assert msg["why"] == "since 11:13 · a click or your next ask puts this away"


def test_the_card_is_rows_the_shell_can_draw_and_a_count(card):
    write(card, turn(t=LEFT + 60, n=1, summary="Installed ffmpeg.", snapshot=2), turn(t=LEFT + 120, n=2, ok=False,
          result="Out of space."), job(t=LEFT + 180, job="j1", ok=False, target="Build", result="Build failed"))
    msg = card.back(LEFT, sessions=[session()])
    assert msg["type"] == "away" and msg["count"] == 4 and msg["since"] == LEFT
    assert [(r["key"], r["tone"], r["button"]) for r in msg["rows"]] == [
        ("job:j1", "red", "Why?"), ("turn:2", "red", ""), ("dev:bombadil/reviewer", "sessions", "Open"),
        ("turn:1", "machine", "")]
    assert all(r["kind"] == "dot" for r in msg["rows"])
    assert msg["rows"][1]["sub"] == "Out of space."
    assert msg["rows"][3]["sub"] == "at 11:14"      # no words of its own: when it ended


def test_more_than_four_rows_are_counted_in_one(card):
    write(card, *[turn(t=LEFT + 60 * i, n=i) for i in range(1, 8)])
    msg = card.back(LEFT)
    assert msg["count"] == 7 and len(msg["rows"]) == away.ROWS + 1
    assert msg["rows"][-1] == {"key": "more", "kind": "plain", "title": "and 3 more", "sub": "", "tone": "you",
                               "button": ""}
    assert [r["key"] for r in msg["rows"][:-1]] == ["turn:7", "turn:6", "turn:5", "turn:4"]


def test_the_time_says_the_weekday_when_it_was_not_today(card):
    write(card, turn(t=NOW - 3 * 86400, n=1))
    assert card.ask(since=NOW - 4 * 86400)["why"].startswith("since Thu 14:13")


def test_a_card_is_up_until_it_is_seen_and_seeing_it_remembers_when(card, tmp_path):
    write(card, turn(n=1))
    card.back(LEFT)
    assert card.up and card.message()["count"] == 1
    assert card.seen(now=NOW + 5) is True
    assert card.up is False and card.message()["rows"] == []
    assert card.seen() is False                         # nothing was up: nothing to remember
    assert card.seen_at == NOW + 5
    assert json.loads((tmp_path / "away.json").read_text()) == {"seen_at": NOW + 5}
    assert Away(path=tmp_path / "away.json", log=card.log, clock=lambda: NOW).seen_at == NOW + 5


def test_an_ask_looks_back_to_the_last_time_it_was_seen_and_a_day_when_it_never_was(card):
    write(card, turn(t=NOW - 100000, n=1), turn(t=NOW - 70000, n=2), turn(t=NOW - 3000, n=3))
    assert [r["key"] for r in card.ask()["rows"]] == ["turn:3", "turn:2"]       # a day: not the one before
    card.seen_at = NOW - 5000
    assert [r["key"] for r in card.ask()["rows"]] == ["turn:3"]
    assert [r["key"] for r in card.ask(since=NOW - 80000)["rows"]] == ["turn:3", "turn:2"]    # as the caller says


def test_no_look_goes_back_further_than_a_week(card):
    write(card, turn(t=NOW - 10 * 86400, n=1), turn(t=NOW - 6 * 86400, n=2))
    msg = card.back(NOW - 30 * 86400)
    assert [r["key"] for r in msg["rows"]] == ["turn:2"] and msg["since"] == NOW - away.MAX_LOOKBACK


def test_the_message_is_a_copy(card):
    write(card, turn(n=1))
    card.back(LEFT)
    card.message()["rows"].clear()
    assert card.up


@pytest.mark.parametrize("text", ["", "{", "[]", '{"seen_at": "soon"}', '{"seen_at": -4}', '{"seen_at": null}', "null"])
def test_a_state_file_that_says_nothing_sensible_is_no_memory(tmp_path, text):
    (tmp_path / "away.json").write_text(text)
    assert Away(path=tmp_path / "away.json", log=tmp_path / "none", clock=lambda: NOW).seen_at is None


def test_a_state_folder_that_cannot_be_written_costs_only_the_memory(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    log = tmp_path / "turns.jsonl"
    log.write_text(json.dumps(turn(n=1)) + "\n")
    a = Away(path=blocked / "away.json", log=log, clock=lambda: NOW)
    a.back(LEFT)
    assert a.seen() is True and a.seen_at == NOW          # kept in memory, not on disk


def test_the_real_log_is_read_when_none_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path))
    (tmp_path / "turns.jsonl").write_text(json.dumps(turn(n=1)) + "\n")
    msg = Away(clock=lambda: NOW).back(LEFT)
    assert [r["key"] for r in msg["rows"]] == ["turn:1"]

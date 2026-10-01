"""finder.py: what on this computer a sentence nearly names. Pure: nothing here calls a model or the network."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bombadil import apps, finder, launcher

UTC = timezone.utc
AT = datetime(2026, 10, 1, 12, 30, tzinfo=UTC).timestamp()


def _app(name, title, description=""):
    return apps.App(name, Path("/apps") / name, title, description)


APPS = [_app("passwords", "Passwords", "Keeps your passwords and logins"),
        _app("tracker", "Tracker", "Tracks invoices and habits"),
        _app("notes", "Notes", "Quick notes"),
        _app("memory-viewer", "Memory Viewer")]


def _ask(prompt, day, summary="Done.", **more):
    t = datetime(2026, 9, day, 10, 0, tzinfo=UTC).timestamp()
    return {"t": t, "prompt": prompt, "ok": True, "summary": summary, "details": f"/state/turns/{day}-1.jsonl", **more}


ASKS = [_ask("file the March invoice", 3, "Filed the invoice."), _ask("make the tracker blue", 5),
        _ask("what is eating my disk", 7, "Cleaned the cache.")]


def _find(text, **kw):
    return finder.find(text, app_list=APPS, entries=launcher.entries(APPS), asks=ASKS, at=AT, tz=UTC, **kw)


def _labels(text, **kw):
    return [m.label for m in _find(text, **kw)]


# -- what it finds --

def test_an_app_is_found_by_a_word_of_its_title_in_a_sentence():
    got = _find("my password app")
    assert [(m.kind, m.label, m.say) for m in got] == [("app", "Passwords", "passwords")]
    assert got[0].score == 1.0 and got[0].hint == "App"


def test_an_app_is_found_by_what_the_agent_said_it_does():
    got = _find("where do I keep my logins")
    assert got[0].label == "Passwords" and got[0].score < 1.0   # the description counts for less than the title


def test_a_past_ask_is_found_with_its_day_and_opens_its_details():
    got = _find("the march invoice")
    assert got[0].kind == "ask"
    assert got[0].label == "You asked: file the March invoice (3 Sep)"
    assert got[0].path == "/state/turns/3-1.jsonl" and got[0].hint == "Its steps"
    assert [m.label for m in got][1:] == ["Tracker"]           # its description says it tracks invoices


def test_a_launcher_word_is_found_by_the_name_it_goes_by():
    got = _find("where is the browser please")
    assert (got[0].kind, got[0].label, got[0].say, got[0].hint) == ("word", "Browser", "browser", "Opens it")
    assert _find("can I see the battery")[0].label == "Battery"


def test_a_slip_of_the_fingers_still_finds_it():
    assert _labels("passwrd") == ["Passwords"]
    assert _labels("pasword manager")[0] == "Passwords"
    assert _labels("memry viewer")[0] == "Memory Viewer"
    assert _labels("cat") == []          # a short word is not guessed at


def test_plurals_and_beginnings_match():
    assert _labels("notes")[0] == "Notes"
    assert _labels("my note")[0] == "Notes"
    assert _labels("track my habits")[0] == "Tracker"
    assert _labels("memory")[0] == "Memory Viewer"


def test_the_best_come_first_and_at_most_three_are_shown():
    got = _find("notes about the tracker invoice")
    assert len(got) <= 3 and got[0].score >= got[-1].score
    many = [_app(f"memo{i}", f"Memo {i}") for i in range(8)]
    assert len(finder.find("memo", app_list=many, entries=[], asks=[])) == 3
    assert len(finder.find("memo", limit=5, app_list=many, entries=[], asks=[])) == 5


def test_a_sentence_with_nothing_on_this_computer_finds_nothing():
    assert _find("what is the capital of france") == []
    assert _find("make it purple") == []                       # "make" and "it" are not words that find
    assert _find("") == [] and _find("the a my") == []


def test_a_sentence_can_find_an_ask_that_shares_its_one_meaningful_word():
    assert [m.label for m in _find("make it blue")] == ["You asked: make the tracker blue (5 Sep)"]


def test_a_thing_that_covers_more_of_the_sentence_beats_one_that_covers_less():
    got = _find("make the tracker blue")
    assert got[0].kind == "ask" and got[0].label.startswith("You asked: make the tracker blue")
    assert got[1].label == "Tracker"


def test_an_app_comes_before_a_word_and_a_word_before_an_ask_when_they_cover_the_same():
    things = [finder.Thing("ask", "ask", "", "sound", "sound", t=5.0), finder.Thing("word", "word", "", "sound", "sound"),
              finder.Thing("app", "app", "", "sound", "sound")]
    assert [m.kind for m in finder.rank("sound", things)] == ["app", "word", "ask"]


def test_the_same_ask_made_twice_is_offered_once_with_the_newest_day():
    again = [_ask("file the March invoice", 3), _ask("file the March invoice", 9)]
    got = finder.find("march invoice", app_list=[], entries=[], asks=again, at=AT, tz=UTC)
    assert [m.label for m in got] == ["You asked: file the March invoice (9 Sep)"]


def test_a_long_sentence_is_not_matched_by_one_stray_word():
    assert _find("please could you tell me how many people live in the tracker office of paris and why") == []


# -- the words that mean nothing --

def test_the_words_that_carry_no_meaning_are_left_out_of_a_sentence():
    assert finder.tokens("Open the app for my Passwords, please!") == ["passwords"]
    assert finder.tokens("a an the") == [] and finder.tokens("") == []
    assert finder.tokens("notes notes NOTES") == ["notes"]


# -- days --

@pytest.mark.parametrize("day, want", [(1, "today"), (0, "yesterday")])
def test_a_recent_ask_is_today_or_yesterday(day, want):
    t = datetime(2026, 9 + (1 if day else 0), 30 if not day else 1, 9, 0, tzinfo=UTC).timestamp()
    assert finder.day(t, AT, UTC) == want


def test_an_older_ask_says_its_date_and_the_year_only_when_it_is_not_this_one():
    assert finder.day(datetime(2026, 9, 3, 10, tzinfo=UTC).timestamp(), AT, UTC) == "3 Sep"
    assert finder.day(datetime(2025, 12, 24, 10, tzinfo=UTC).timestamp(), AT, UTC) == "24 Dec 2025"


def test_a_label_is_cut_to_fit_a_chip():
    long = _ask("write me a poem about the way the light falls on the kitchen table in the early morning", 3)
    got = finder.find("poem kitchen table", app_list=[], entries=[], asks=[long], at=AT, tz=UTC)
    assert len(got[0].label) <= 60 and got[0].label.endswith("(3 Sep)") and "…" in got[0].label


# -- the past asks on disk --

def test_only_asks_that_worked_and_were_the_users_own_are_kept(home):
    rows = [
        {"t": 1, "prompt": "good one", "ok": True, "summary": "Did it."},
        {"t": 2, "prompt": "stopped one", "ok": None, "stopped": True},
        {"t": 3, "prompt": "failed one", "ok": False},
        {"t": 4, "prompt": "waiting for the limit", "ok": False, "requeued": True},
        {"t": 5, "prompt": "!ls", "ok": True},
        {"t": 6, "prompt": "[from app notes] summarise this", "ok": True},
        {"t": 7, "kind": "local", "prompt": "undo", "ok": True},
        {"t": 8, "prompt": "", "ok": True},
        {"t": 9, "prompt": "second good one", "ok": True},
    ]
    log = paths_log(home)
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n[1]\n")
    assert [e["prompt"] for e in finder.read_asks()] == ["good one", "second good one"]


def paths_log(home) -> Path:
    from bombadil import paths
    paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
    return paths.turns_log()


def test_a_missing_log_has_no_asks_and_only_the_latest_are_read(home):
    assert finder.read_asks() == []
    log = paths_log(home)
    log.write_text("\n".join(json.dumps({"t": i, "prompt": f"ask {i}", "ok": True}) for i in range(1, 30)) + "\n")
    assert [e["prompt"] for e in finder.read_asks(limit=3)] == ["ask 27", "ask 28", "ask 29"]


def test_finding_reads_this_computer_when_given_nothing_else(home):
    apps.create("Passwords", "import QtQuick\nItem {}\n", description="Keeps your logins")
    paths_log(home).write_text(json.dumps({"t": AT, "prompt": "install ffmpeg", "ok": True, "details": "/d/1-1.jsonl"}) + "\n")
    assert [m.label for m in finder.find("my logins")] == ["Passwords"]
    got = finder.find("ffmpeg")
    assert got[0].kind == "ask" and got[0].path == "/d/1-1.jsonl"


def test_every_launcher_word_offered_opens_what_it_says_by_a_phrase_the_launcher_accepts():
    words = [t for t in finder.things(app_list=[], entries=launcher.entries([]), asks=[]) if t.kind == "word"]
    assert len(words) > 5
    for t in words:
        action = launcher.match(t.say, [])
        assert action is not None and action.kind in {"panel", "widget"} | finder.SHOWS, t


def test_a_widget_is_offered_by_the_phrase_that_opens_it_and_a_command_is_never_offered():
    machine = [m for m in _find("how is the machine doing")]
    assert machine and machine[0].label == "Machine" and machine[0].say == "open machine"
    offered = {t.say for t in finder.things(app_list=[], entries=launcher.entries([]), asks=[]) if t.kind == "word"}
    assert not offered & {"undo", "hide", "sign in", "restart", "shutdown", "shut down"}
    assert {"battery", "wifi", "history"} <= offered      # a command that only shows something may be offered
    for sentence in ("undo that", "restart the computer", "sign in again", "shut down"):
        assert [m for m in _find(sentence) if m.kind == "word"] == []

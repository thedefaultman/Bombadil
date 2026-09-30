"""`bombadil history` and the welcome line's boot rows: a boot row is an anchor, never an entry."""

import json
import time

import pytest

from bombadil import greet, paths, watch

EMPTY = "Nothing to go back to yet. Undo starts after the first change."
EMPTY_LIVE = "Nothing to go back to yet. Undo starts once Bombadil is installed."


@pytest.fixture(autouse=True)
def off_live(monkeypatch):
    monkeypatch.setenv("BOMBADIL_LIVE", "0")


def write(*rows):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))


def boot(t, greeted=True):
    return {"t": t, "kind": "local", "action": "boot", "prompt": "", "greeted": greeted}


def test_a_boot_row_is_not_a_line_of_the_history(home):
    t = time.mktime((2026, 9, 30, 8, 0, 0, 0, 0, -1))
    write(boot(t), {"t": t + 60, "prompt": "install ffmpeg", "summary": "Installed ffmpeg.", "snapshot": 4},
          {"t": t + 120, "kind": "local", "prompt": "passwords", "action": "app", "result": "Opened Passwords."},
          boot(t + 3600, greeted=False))
    out = watch.history_lines()
    assert len(out) == 3   # one day, two entries
    assert any("install ffmpeg" in x and "Installed ffmpeg." in x for x in out)
    assert any("passwords: Opened Passwords." in x for x in out)
    assert not any(x.rstrip().endswith(":") or "  :" in x for x in out)


def test_boot_rows_do_not_use_up_the_limit(home):
    t = time.time()
    write({"t": t, "prompt": "the only turn", "summary": "Did it."}, *[boot(t + i) for i in range(1, 60)])
    out = watch.history_lines(limit=1)
    assert len(out) == 2 and "the only turn" in out[1]


def test_a_log_with_nothing_but_boots_is_an_empty_place_with_its_one_way_out(home):
    assert watch.history_lines() == [EMPTY]    # no log at all
    write(boot(time.time()), boot(time.time() + 1))
    assert watch.history_lines() == [EMPTY]
    assert greet.empty("history") == EMPTY


def test_on_the_live_system_the_line_says_when_undo_starts(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_LIVE", "1")
    assert watch.history_lines() == [EMPTY_LIVE]


def test_the_old_words_remain_when_the_table_is_gone(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_VOICE_LINES", str(home / "missing.toml"))
    assert watch.history_lines() == ["Nothing yet."]


def test_rows_that_are_not_objects_are_skipped_not_fatal(home):
    paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
    paths.turns_log().write_text('5\n"x"\n[1]\nnot json\n' + json.dumps({"t": 1790000000, "prompt": "a"}) + "\n")
    assert any("  a" in x for x in watch.history_lines())

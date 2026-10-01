"""rest.py: the state file other processes read, the reading of a provider's reset time, and the words.

Times are in UTC or a fixed offset: the lines say the user's local clock, and these tests must not
depend on the machine's zone files.
"""

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bombadil import rest

UTC = timezone.utc
# Thursday 1 October 2026, 12:30 UTC.
AT = datetime(2026, 10, 1, 12, 30, tzinfo=UTC).timestamp()


def _at(*args, tz=UTC) -> float:
    return datetime(*args, tzinfo=tz).timestamp()


# -- the state file --

def test_a_limit_is_kept_in_the_state_directory_and_read_back(home):
    r = rest.set_limit("claude", rest.Limit("limit", "five_hour", AT + 3600, "text"), AT)
    assert r == rest.Rest("claude", "limit", "five_hour", AT + 3600, AT)
    assert rest.path() == home / "state" / "rest.json"
    assert rest.limit("claude", AT) == r
    assert rest.current("claude", AT) == r
    assert rest.limit("codex", AT) is None
    assert json.loads(rest.path().read_text())["providers"]["claude"]["kind"] == "five_hour"


def test_the_file_can_be_read_by_other_users_and_is_written_whole(home):
    rest.set_limit("claude", rest.Limit("limit", None, AT + 60), AT)
    mode = stat.S_IMODE(os.stat(rest.path()).st_mode)
    assert mode == 0o644
    assert not list(rest.path().parent.glob("*.tmp"))


def test_a_limit_lapses_a_minute_after_its_time_and_a_reader_never_sees_it_after(home):
    rest.set_limit("claude", rest.Limit("limit", "five_hour", AT + 600), AT)
    assert rest.limit("claude", AT + 600 + rest.RESET_GRACE - 1) is not None
    assert rest.limit("claude", AT + 600 + rest.RESET_GRACE) is None
    assert rest.read(AT + 700)["providers"] == {}
    assert rest.due("claude") == AT + 600 + rest.RESET_GRACE


def test_a_limit_with_no_time_lasts_until_it_is_cleared(home):
    rest.set_limit("codex", rest.Limit("spend", None, None), AT)
    assert rest.limit("codex", AT + 10 ** 7).why == "spend"
    assert rest.due("codex") is None
    rest.clear_limit("codex")
    assert rest.limit("codex", AT) is None
    rest.clear_limit("codex")   # nothing there: nothing to say


def test_a_refusal_that_names_a_time_already_past_is_tried_again_in_five_minutes(home):
    r = rest.set_limit("claude", rest.Limit("limit", "five_hour", AT - 3600), AT)
    assert r.until == AT + rest.RETRY_AFTER_REFUSAL
    just = rest.set_limit("claude", rest.Limit("limit", None, AT - rest.RESET_GRACE + 5), AT)
    assert just.until == AT - rest.RESET_GRACE + 5   # not yet lapsed: the provider's own time stays


def test_a_pause_by_hand_outranks_a_limit_and_survives_until_cleared(home):
    rest.set_limit("claude", rest.Limit("limit", "five_hour", AT + 600), AT)
    rest.set_hand("claude", AT)
    assert rest.current("claude", AT).why == "hand"
    assert rest.hand("claude").since == AT
    assert rest.clear_hand("claude") is True
    assert rest.current("claude", AT).why == "limit"   # what is left shows when the switch is back on
    assert rest.clear_hand("claude") is False
    rest.set_hand("codex")
    assert rest.read(AT + 10 ** 9)["hand"] == {"codex": rest.read()["hand"]["codex"]}


def test_each_provider_rests_on_its_own(home):
    rest.set_limit("claude", rest.Limit("limit", None, AT + 600), AT)
    rest.set_hand("codex", AT)
    assert rest.current("claude", AT).why == "limit"
    assert rest.current("codex", AT).why == "hand"
    assert rest.current("fake", AT) is None


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"providers": 3, "hand": []}', '{"providers": {"claude": 3}}'])
def test_a_broken_file_reads_as_nothing_and_is_replaced_by_the_next_write(home, content):
    rest.path().parent.mkdir(parents=True, exist_ok=True)
    rest.path().write_text(content)
    assert rest.current("claude", AT) is None
    rest.set_hand("claude", AT)
    assert rest.current("claude", AT).why == "hand"


# -- times --

def test_when_says_a_clock_today_a_weekday_within_six_days_and_a_date_beyond():
    assert rest.when(AT + 3600, AT, UTC) == "13:30"
    assert rest.when(AT + 3600 * 12, AT, UTC) == "Friday 00:30"
    assert rest.when(AT + 3600 * 12, AT, UTC, short=True) == "Fri 00:30"
    assert rest.when(_at(2026, 10, 7, 9, 0), AT, UTC) == "Wednesday 09:00"   # six days on
    assert rest.when(_at(2026, 10, 8, 9, 0), AT, UTC) == "8 Oct"
    assert rest.when(_at(2026, 11, 1, 9, 0), AT, UTC, short=True) == "1 Nov"
    assert rest.when(_at(2027, 1, 5, 9, 0), AT, UTC) == "5 Jan 2027"


def test_when_follows_the_calendar_day_of_the_zone_not_twenty_four_hours():
    late = datetime(2026, 10, 1, 23, 30, tzinfo=UTC).timestamp()
    assert rest.when(late + 3600, late, UTC) == "Friday 00:30"
    west = timezone(timedelta(hours=-7))
    assert rest.when(late + 3600, late, west) == "17:30"   # still the same afternoon there


def test_the_word_tomorrow_is_never_said():
    for hours in range(1, 400, 7):
        assert "tomorrow" not in rest.when(AT + hours * 3600, AT, UTC).lower()


@pytest.mark.parametrize("text, want", [
    ("You've hit your session limit · resets 3:45pm (UTC)", _at(2026, 10, 1, 15, 45)),
    ("resets 3:45pm", _at(2026, 10, 1, 15, 45)),
    ("resets 11:00am", _at(2026, 10, 2, 11, 0)),            # a clock already gone by is the next one
    ("resets 12:30pm", _at(2026, 10, 2, 12, 30)),            # 12:30 is exactly now: the next one
    ("resets 12am", _at(2026, 10, 2, 0, 0)),
    ("resets 12:15pm", _at(2026, 10, 2, 12, 15)),
    ("resets Oct 3, 3pm", _at(2026, 10, 3, 15, 0)),
    ("resets Mon 12:00am", _at(2026, 10, 5, 0, 0)),
    ("You’ve hit your usage limit. Try again at 3:45 PM.", _at(2026, 10, 1, 15, 45)),   # curly apostrophe
    ("try again at Oct 2nd, 2026 3:45 PM", _at(2026, 10, 2, 15, 45)),
    ("Try again at Jan 5th 9 AM", _at(2027, 1, 5, 9, 0)),    # next year, for a date that has gone
    ("retry at 3:45 PM", _at(2026, 10, 1, 15, 45)),
])
def test_a_providers_time_is_read_from_its_sentence(text, want):
    assert rest.parse_time(text, AT, UTC) == want


@pytest.mark.parametrize("text", ["", "no time here", "resets soon", "resets 13:00pm", "resets 3:99pm", "resets Feb 30, 3pm"])
def test_a_sentence_without_a_usable_time_gives_none(text):
    assert rest.parse_time(text, AT, UTC) is None


def test_a_named_zone_in_the_sentence_wins_over_the_local_one():
    zone = ZoneInfo("America/Vancouver")
    got = rest.parse_time("resets 3:45pm (America/Vancouver)", AT, UTC)
    start = datetime.fromtimestamp(AT, zone)
    want = start.replace(hour=15, minute=45, second=0, microsecond=0)
    if want <= start:
        want += timedelta(days=1)
    assert got == want.timestamp()
    assert rest.parse_time("resets 3:45pm (Not/AZone)", AT, UTC) == _at(2026, 10, 1, 15, 45)


def test_the_page_to_raise_a_limit_is_the_providers_own_or_its_usage_page():
    own = "Upgrade at https://claude.ai/upgrade?x=1. Resets 3pm"
    assert rest.page_in(own, "claude") == "https://claude.ai/upgrade?x=1"
    assert rest.page_in("see https://evil.example/pay", "claude") == rest.RAISE_PAGES["claude"]
    assert rest.page_in("see https://claude.ai.evil.example/pay", "claude") == rest.RAISE_PAGES["claude"]
    assert rest.page_in("", "codex") == rest.RAISE_PAGES["codex"]
    assert rest.page_in("", "fake") == ""


# -- the words --

def _words(r, title="Claude"):
    return rest.words(r, title, AT, UTC)


def test_the_lines_for_a_timed_limit():
    w = _words(rest.Rest("claude", "limit", "five_hour", AT + 3600, AT))
    assert w["line"] == "Claude is at its limit until 13:30. Your apps and files still work."
    assert w["hint"] == "Open or find anything. Asks wait for 13:30."
    assert (w["wait"], w["note"], w["when"], w["row"]) == ("13:30", "At 13:30", "13:30", "at its limit until 13:30")
    week = _words(rest.Rest("claude", "limit", "seven_day", _at(2026, 10, 3, 9, 0), AT))
    assert week["line"] == "Claude is at its weekly limit until Saturday 09:00. Your apps and files still work."
    assert week["wait"] == "Sat 09:00"


@pytest.mark.parametrize("kind, word", [("seven_day_opus", "Opus limit"), ("seven_day_sonnet", "Sonnet limit"),
                                        ("seven_day_overage_included", "Fable limit"), (None, "limit"),
                                        ("something_new", "limit")])
def test_a_window_is_named_the_way_the_provider_names_it(kind, word):
    assert f"at its {word} until" in _words(rest.Rest("claude", "limit", kind, AT + 60, AT))["line"]


def test_a_spending_limit_is_named_for_what_it_is_and_offers_no_time_when_it_has_none():
    timed = _words(rest.Rest("claude", "spend", "overage", AT + 3600, AT))
    assert timed["line"] == "Claude is at its spending limit until 13:30. Your apps and files still work."
    bare = _words(rest.Rest("claude", "spend", None, None, AT))
    assert bare["line"] == "Claude is at its spending limit. Your apps and files still work."
    assert bare["hint"] == "Open or find anything. Asks wait for Claude."
    assert (bare["wait"], bare["note"], bare["when"]) == ("limit", "At its spending limit", None)
    assert _words(rest.Rest("codex", "limit", None, None, AT), "Codex")["note"] == "At its limit"


def test_a_pause_says_so_and_what_waits_for_what():
    w = _words(rest.Rest("claude", "hand", since=AT))
    assert w["line"] == "Claude is paused. Your apps and files still work."
    assert w["hint"] == "Open or find anything. Asks wait until you resume Claude."
    assert (w["wait"], w["note"], w["when"]) == ("paused", "Paused", None)


def test_every_line_is_short_and_plain():
    rests = [rest.Rest("claude", why, kind, until, AT)
             for why in ("limit", "spend", "hand") for kind in (None, "five_hour", "seven_day_opus")
             for until in (None, AT + 60, AT + 86400 * 3, AT + 86400 * 30)]
    for r in rests:
        for text in _words(r).values():
            if isinstance(text, str):
                assert "—" not in text and "token" not in text.lower() and len(text) < 100, text
    for text in (rest.cut_off("Claude", rests[1], "1 package and 2 files", AT, UTC), rest.back("Claude", 12),
                 rest.trying("Claude", 2)):
        assert "—" not in text and len(text) < 100, text


def test_a_turn_the_limit_stopped_halfway_says_what_it_changed_and_when_it_carries_on():
    timed = rest.Rest("claude", "limit", "five_hour", AT + 3600, AT)
    assert rest.cut_off("Claude", timed, "2 files", AT, UTC) == \
        "Claude hit its limit partway, after changing 2 files. It carries on at 13:30."
    assert rest.cut_off("Claude", timed, "", AT, UTC) == "Claude hit its limit partway. It carries on at 13:30."
    spend = rest.Rest("claude", "spend", None, None, AT)
    assert rest.cut_off("Claude", spend, "1 package", AT, UTC) == \
        "Claude hit its spending limit partway, after changing 1 package. It carries on once the limit is raised."
    assert rest.cut_off("Codex", rest.Rest("codex", "limit", None, None, AT), "", AT, UTC) == \
        "Codex hit its limit partway. It carries on once the limit is lifted."


def test_coming_back_says_what_runs_now():
    assert rest.back("Claude") == "Claude is back."
    assert rest.back("Claude", 1) == "Claude is back. Running your waiting ask."
    assert rest.back("Claude", 3) == "Claude is back. Running your 3 waiting asks."
    assert rest.trying("Claude", 2) == "Trying Claude again. Running your 2 waiting asks."


def test_the_line_for_a_kept_ask_says_when_it_runs_and_whether_anything_on_the_computer_matches():
    timed = rest.Rest("claude", "limit", "five_hour", AT + 3600, AT)
    assert rest.kept(timed, "Claude", True, AT, UTC) == "Kept for 13:30. Found on this computer:"
    assert rest.kept(timed, "Claude", False, AT, UTC) == "Kept for 13:30. Nothing on this computer matches."
    week = rest.Rest("claude", "limit", "seven_day", _at(2026, 10, 3, 9, 0), AT)
    assert rest.kept(week, "Claude", True, AT, UTC) == "Kept for Saturday 09:00. Found on this computer:"
    bare = rest.Rest("claude", "spend", None, None, AT)
    assert rest.kept(bare, "Codex", True) == "Kept until Codex is back. Found on this computer:"
    paused = rest.Rest("claude", "hand", since=AT)
    assert rest.kept(paused, "Claude", False) == "Kept until you resume Claude. Nothing on this computer matches."
    for text in (rest.kept(timed, "Claude", True, AT, UTC), rest.kept(paused, "Claude", False)):
        assert len(text) < 100 and "\u2014" not in text

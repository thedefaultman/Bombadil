import time

import pytest

from bombadil.loop import forms, offers
from bombadil.loop.habits import Group
from bombadil.loop.offers import ACCEPT, EXPIRED, GOT_IT, NEVER, NOT_NOW, Config, Past

DAY = 86400
CFG = Config()


def at(day, hour=12, minute=0):
    """Local epoch seconds on September `day` 2026 at hour:minute; `day` may go past the month's end."""
    return time.mktime((2026, 9, day, hour, minute, 0, 0, 0, -1))


NOW = at(28, 10)


def group(ages, seconds=15.0, steps=1, gid="g1", near_miss=False, opens="app:passwords", now=NOW, **kw):
    """A group asked `ages` days (and hours) before `now`: a number of days, or a (days, hours) pair."""
    times = sorted(now - (a[0] * DAY + a[1] * 3600 if isinstance(a, tuple) else a * DAY) for a in ages)
    n = len(times)
    kw.setdefault("verbs", {"open": n})
    kw.setdefault("routes", ["app:passwords", "opened"] if opens else ["memory"])
    kw.setdefault("label", "passwords open")
    kw.setdefault("sentences", ["show me my passwords"])
    kw.setdefault("word", "my passwords")
    return Group(id=gid, n=n, first=times[0], last=times[-1], times=times, days=sorted({time.strftime("%Y-%m-%d", time.localtime(t)) for t in times}),
                 seconds=[seconds] * n, steps=[steps] * n, near_miss=near_miss, opens=opens, **kw)


# -- the numbers --

def test_the_defaults_are_the_briefs():
    assert (CFG.asks, CFG.days, CFG.window_days, CFG.seconds, CFG.steps) == (3, 2, 21, 45, 3)
    assert (CFG.raised_asks, CFG.take_window, CFG.take_min) == (5, 10, 3)
    assert (CFG.per_day, CFG.per_week, CFG.not_now_days, CFG.expire_days) == (1, 3, 30, 14)
    assert (CFG.rest_days, CFG.rest_after, CFG.never_forms) == (30, 2, 3)


def test_the_config_file_retunes_the_numbers(home, monkeypatch):
    loop = home / "loop"
    loop.mkdir()
    monkeypatch.setenv("BOMBADIL_LOOP", str(loop))
    (loop / "config.toml").write_text("[offers]\nasks = 4\ndays = 3\nseconds = 30.5\nper_week = 2\n")
    cfg = offers.load_config()
    assert (cfg.asks, cfg.days, cfg.seconds, cfg.per_week) == (4, 3, 30.5, 2)
    assert cfg.window_days == 21                                         # what it does not say stays


def test_a_missing_or_broken_config_leaves_the_defaults(home, tmp_path):
    assert offers.load_config(tmp_path / "nope.toml") == CFG
    path = tmp_path / "config.toml"
    path.write_text("[offers\nasks = ")
    assert offers.load_config(path) == CFG
    path.write_bytes(b"\xff\xfe\x00 not text")
    assert offers.load_config(path) == CFG
    path.write_text("offers = 5\n")
    assert offers.load_config(path) == CFG


@pytest.mark.parametrize("body", [
    "asks = 0", "asks = -3", "asks = 2.5", "asks = 'three'", "asks = true", "asks = [3]", "seconds = -1",
    "seconds = 'many'", "window_days = 0", "unknown = 4",
])
def test_a_value_that_makes_no_sense_leaves_its_default(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(f"[offers]\n{body}\nper_day = 2\n")
    cfg = offers.load_config(path)
    assert cfg == Config(per_day=2)


def test_the_config_has_a_hash_that_moves_with_it():
    assert offers.config_hash(CFG) == offers.config_hash(Config())
    assert offers.config_hash(CFG) != offers.config_hash(Config(asks=4))


# -- ripeness --

def test_three_asks_on_two_days_worth_a_minute_are_ripe():
    g = group([0, 1, 2], seconds=15)                     # 45 seconds together
    assert offers.ripe(g, NOW, CFG) == (True, "")


def test_too_few_asks():
    assert offers.ripe(group([0, 1], seconds=60), NOW, CFG) == (False, "too few asks")


def test_three_asks_on_one_day_are_one_day():
    g = group([(0, 0), (0, 1), (0, 2)], seconds=60)
    assert offers.ripe(g, at(28, 23), CFG) == (False, "all on one day")
    assert offers.ripe(group([(0, 0), (0, 1), (1, 0)], seconds=60), NOW, CFG)[0]


def test_a_day_is_the_local_date():
    late = [at(27, 23, 50), at(28, 0, 10), at(28, 0, 20)]
    g = Group(id="g", n=3, times=late, seconds=[20.0] * 3, steps=[1] * 3, first=late[0], last=late[-1])
    assert offers.ripe(g, at(28, 12), CFG) == (True, "")


def test_asks_older_than_the_window_do_not_count():
    assert offers.ripe(group([0, 1, 22], seconds=60), NOW, CFG) == (False, "too few asks")
    assert offers.ripe(group([0, 1, 20], seconds=60), NOW, CFG)[0]
    assert offers.ripe(group([0, 1, 21], seconds=60), NOW, CFG)[0]           # the edge is in


def test_a_group_asked_only_later_than_now_is_not_counted_yet():
    assert offers.ripe(group([-1, -2, 0], seconds=60), NOW, CFG)[0] is False


def test_it_must_be_worth_building():
    assert offers.ripe(group([0, 1, 2], seconds=10), NOW, CFG) == (False, "not worth building")
    assert offers.ripe(group([0, 1, 2], seconds=15), NOW, CFG)[0]            # 45 s exactly
    assert offers.ripe(group([0, 1, 2], seconds=14.9), NOW, CFG)[0] is False
    assert offers.ripe(group([0, 1, 2], seconds=5, steps=3), NOW, CFG)[0]    # each ran three steps
    mixed = group([0, 1, 2], seconds=5, steps=3)
    mixed.steps[0] = 2
    assert not offers.ripe(mixed, NOW, CFG)[0]
    assert offers.ripe(group([0, 1, 2], seconds=5, near_miss=True), NOW, CFG)[0]     # a near miss of a word


def test_the_bar_can_be_raised():
    g = group([0, 1, 2, 3], seconds=20)
    assert offers.ripe(g, NOW, CFG, need=5) == (False, "too few asks")
    assert offers.ripe(group([0, 1, 2, 3, 4], seconds=20), NOW, CFG, need=5)[0]


def test_a_config_changes_what_is_ripe():
    assert not offers.ripe(group([0, 1], seconds=60), NOW, CFG)[0]
    assert offers.ripe(group([0, 1], seconds=60), NOW, Config(asks=2))[0]
    assert not offers.ripe(group([(0, 0), (0, 1), (1, 0)], seconds=60), NOW, Config(days=3))[0]


# -- the bar --

def past(outcome, shown=NOW - 5 * DAY, answered=None, form="A"):
    return Past(shown, outcome, NOW - 4 * DAY if outcome and answered is None else answered, form)


def test_the_bar_is_three_asks_until_ten_offers_have_been_answered():
    assert offers.asks_needed([], CFG) == 3
    assert offers.asks_needed([past(NOT_NOW, NOW - i * DAY) for i in range(9)], CFG) == 3


def test_the_bar_rises_to_five_when_fewer_than_three_of_the_last_ten_were_taken():
    shown = [past(NOT_NOW, NOW - (30 - i) * DAY) for i in range(10)]
    assert offers.asks_needed(shown, CFG) == 5
    taken = [past(ACCEPT if i in (2, 5, 8) else NOT_NOW, NOW - (30 - i) * DAY) for i in range(10)]
    assert offers.asks_needed(taken, CFG) == 3
    some = [past(ACCEPT if i in (2, 5) else NEVER, NOW - (30 - i) * DAY) for i in range(10)]
    assert offers.asks_needed(some, CFG) == 5
    assert offers.asks_needed([past(GOT_IT, NOW - (30 - i) * DAY) for i in range(10)], CFG) == 3   # "got it" is taken


def test_only_the_last_ten_say_whether_offers_are_taken():
    old_taken = [past(ACCEPT, NOW - (60 - i) * DAY) for i in range(6)]
    recent_not = [past(NOT_NOW, NOW - (20 - i) * DAY) for i in range(10)]
    assert offers.asks_needed(old_taken + recent_not, CFG) == 5
    assert offers.asks_needed(old_taken + recent_not[:4] + [past(ACCEPT, NOW - DAY)] * 3 + recent_not[7:], CFG) == 3


def test_an_offer_still_waiting_says_nothing_about_being_taken():
    assert offers.asks_needed([past("", NOW - i * DAY) for i in range(12)], CFG) == 3


# -- cadence --

def test_one_a_day():
    assert offers.cadence_ok([], NOW, CFG) == (True, "")
    assert offers.cadence_ok([past(NOT_NOW, NOW - 3600)], NOW, CFG) == (False, "one a day")
    assert offers.cadence_ok([past(NOT_NOW, NOW - DAY + 1)], NOW, CFG)[0] is False
    assert offers.cadence_ok([past(NOT_NOW, NOW - DAY)], NOW, CFG) == (True, "")


def test_three_a_week():
    three = [past(NOT_NOW, NOW - (1.5 + i) * DAY) for i in range(3)]
    assert offers.cadence_ok(three, NOW, CFG) == (False, "three a week")
    assert offers.cadence_ok(three[:2], NOW, CFG)[0]
    old = [past(NOT_NOW, NOW - (5 + i) * DAY) for i in range(3)]       # 5, 6, 7 days ago: the last is out
    assert offers.cadence_ok(old, NOW, CFG)[0]
    assert offers.cadence_ok(three, NOW, Config(per_week=4))[0]
    assert offers.cadence_ok([past(NOT_NOW, NOW - 3 * 3600)], NOW, Config(per_day=2))[0]


def test_an_offer_shown_later_than_now_does_not_block():
    assert offers.cadence_ok([past(NOT_NOW, NOW + 3600)], NOW, CFG)[0]


# -- silence and rest --

def test_not_now_is_over_when_the_count_doubles_or_thirty_days_pass():
    assert not offers.released(3, NOW - 10 * DAY, 5, NOW, CFG)
    assert offers.released(3, NOW - 10 * DAY, 6, NOW, CFG)
    assert not offers.released(3, NOW - 29 * DAY, 3, NOW, CFG)
    assert offers.released(3, NOW - 30 * DAY, 3, NOW, CFG)
    assert offers.released(0, NOW, 2, NOW, CFG)                       # a count of nothing doubles at two


def test_an_offer_expires_after_fourteen_days():
    assert not offers.expired(NOW - 14 * DAY + 1, NOW, CFG)
    assert offers.expired(NOW - 14 * DAY, NOW, CFG)
    assert offers.expired(NOW - 3 * DAY, NOW, Config(expire_days=3))


def test_two_silent_expiries_in_a_row_rest_all_offers_for_thirty_days():
    a = past(EXPIRED, NOW - 50 * DAY, NOW - 36 * DAY)
    b = past(EXPIRED, NOW - 30 * DAY, NOW - 16 * DAY)
    assert offers.resting_until([a], NOW, CFG) == 0
    until = offers.resting_until([a, b], NOW, CFG)
    assert until == b.answered_t + 30 * DAY > NOW
    assert offers.resting_until([a, b], NOW + 15 * DAY, CFG) == 0     # the rest is over
    assert offers.resting_until([a, b], until - 1, CFG) == until
    assert offers.resting_until([a, b], until, CFG) == 0


def test_two_nevers_in_a_row_rest_them_too():
    a = past(NEVER, NOW - 12 * DAY, NOW - 11 * DAY)
    b = past(NEVER, NOW - 6 * DAY, NOW - 5 * DAY)
    assert offers.resting_until([a, b], NOW, CFG) == b.answered_t + 30 * DAY


def test_anything_between_breaks_the_row():
    e1, e2 = past(EXPIRED, NOW - 40 * DAY, NOW - 26 * DAY), past(EXPIRED, NOW - 14 * DAY, NOW - 1 * DAY)
    taken = past(ACCEPT, NOW - 21 * DAY, NOW - 20 * DAY)
    assert offers.resting_until([e1, taken, e2], NOW, CFG) == 0
    not_now = past(NOT_NOW, NOW - 21 * DAY, NOW - 20 * DAY)
    assert offers.resting_until([e1, not_now, e2], NOW, CFG) == 0
    never = past(NEVER, NOW - 21 * DAY, NOW - 20 * DAY)
    assert offers.resting_until([e1, never, e2], NOW, CFG) == 0       # an expiry and a Never are not a row
    waiting = past("", NOW - DAY)
    assert offers.resting_until([e1, e2, waiting], NOW, CFG) > 0      # one still waiting changes nothing


def test_a_row_of_two_rests_once_and_then_counts_afresh():
    es = [past(EXPIRED, NOW - (200 - 30 * i) * DAY, NOW - (186 - 30 * i) * DAY) for i in range(3)]
    assert offers.resting_until(es[:2], NOW, CFG) == 0                # long over
    e = [past(EXPIRED, NOW - 20 * DAY, NOW - 9 * DAY), past(EXPIRED, NOW - 10 * DAY, NOW - 2 * DAY)]
    assert offers.resting_until(es + e, NOW, CFG) > 0


def test_the_one_line_the_card_says_while_offers_rest():
    until = at(30 + 3)                                                # 3 October
    assert offers.rest_line(until) == "Resting offers until 3 Oct"


def test_three_nevers_on_one_form_stop_offering_it():
    assert offers.stopped_forms(["A", "A"], CFG) == set()
    assert offers.stopped_forms(["A", "word", "A"], CFG) == {"A"}     # the letter or the id
    assert offers.stopped_forms(["A", "D", "A", "D", "D"], CFG) == {"D"}
    assert offers.stopped_forms(["", None, "A"], CFG) == set()
    assert offers.stopped_forms(["A", "A"], Config(never_forms=2)) == {"A"}


# -- the one offer --

def test_the_heaviest_ripe_group_is_offered_first():
    light = group([10, 11, 12], seconds=20, gid="light")
    heavy = group([0, 1, 2, 3], seconds=20, gid="heavy")
    picked = offers.next_offer([light, heavy], [], NOW, CFG)
    assert picked[0].id == "heavy" and picked[1].form.letter == "A"


def test_nothing_ripe_nothing_offered():
    assert offers.next_offer([group([0, 1], seconds=60)], [], NOW, CFG) is None
    assert offers.next_offer([], [], NOW, CFG) is None


def test_resting_or_over_the_allowance_nothing_is_offered():
    g = group([0, 1, 2], seconds=20)
    assert offers.next_offer([g], [past(NOT_NOW, NOW - 3600)], NOW, CFG) is None
    rests = [past(EXPIRED, NOW - 30 * DAY, NOW - 16 * DAY), past(EXPIRED, NOW - 20 * DAY, NOW - 6 * DAY)]
    assert offers.next_offer([g], rests, NOW, CFG) is None
    later = at(28 + 25, 10)                                           # 23 October: the rest is over
    assert offers.next_offer([group([0, 1, 2], seconds=20, now=later)], rests, later, CFG)


def test_the_raised_bar_holds_a_group_back():
    g = group([0, 1, 2, 3], seconds=20)
    discouraged = [past(NOT_NOW, NOW - (40 - 3 * i) * DAY) for i in range(10)]
    assert offers.next_offer([g], discouraged, NOW, CFG) is None
    assert offers.next_offer([group([0, 1, 2, 3, 4], seconds=20)], discouraged, NOW, CFG)


def test_a_group_that_left_the_list_is_not_offered():
    old = group([35, 36, 37], seconds=60)
    assert offers.next_offer([old], [], NOW, CFG) is None


def test_a_form_that_cannot_be_built_is_not_offered():
    g = group([0, 1, 2], seconds=20, verbs={"tell-me-when": 3}, routes=["files:jobs"], opens="")
    assert offers.next_offer([g], [], NOW, CFG) is None                       # a watcher waits for Autopilot
    assert offers.next_offer([g], [], NOW, CFG, built=forms.BUILT | {"autopilot"})[1].form.letter == "F"


def test_a_form_stopped_three_times_is_passed_over():
    g = group([0, 1, 2], seconds=20)
    assert offers.next_offer([g], [], NOW, CFG, stopped={"A"}) is None


def test_the_next_group_is_offered_when_the_first_has_no_form():
    nothing = group([0, 1, 2, 3], seconds=30, gid="nothing", verbs={"ask": 4}, routes=[], opens="")
    word = group([0, 1, 2], seconds=20, gid="word")
    assert offers.next_offer([nothing, word], [], NOW, CFG)[0].id == "word"


# -- the row --

def test_the_row_is_what_the_widget_shows():
    g = group([0, 1, 2], seconds=20)
    rec = forms.recommend(g, titles={"app:passwords": "Passwords"})
    row = offers.Offer(7, g, rec, NOW).to_row()
    assert row["id"] == "g1" and row["offer"] == 7 and row["kind"] == "offer"
    assert row["title"] == "show me my passwords"                          # their own words
    assert row["meta"] == "3 times on 3 days"
    assert row["what"] == "Say “my passwords” and Passwords opens. No model, under a tenth of a second."
    assert row["primary"] == {"label": "Make the word", "op": "accept", "form": "word"}
    assert [o["op"] for o in row["others"]] == ["not_now", "never"]
    assert row["forms"] == [{"form": "word", "label": "A word", "recommended": True}]
    assert set(row) == {"id", "offer", "kind", "title", "meta", "what", "primary", "others", "forms"}


def test_the_button_is_at_most_three_words_for_every_form():
    assert all(len(label.split()) <= 3 for label in offers.BUTTONS.values())
    assert set(offers.BUTTONS) == set("ABCDEFG")


def test_a_word_that_exists_says_got_it():
    g = group([0, 1, 2], seconds=20, existing="passwords")
    rec = forms.recommend(g)
    row = offers.Offer(1, g, rec, NOW).to_row()
    assert row["primary"] == {"label": "Got it", "op": "got_it", "form": "word"}
    assert row["what"] == "Passwords already opens with the word passwords."


def test_other_ways_are_offered_only_when_there_are_some():
    g = group([0, 1, 2], seconds=20, verbs={"ask": 3}, routes=["memory", "processes"], opens="")
    rec = forms.recommend(g, forms.BUILT | {"answer_cards", "agent_widgets"})
    row = offers.Offer(2, g, rec, NOW).to_row()
    assert [o["op"] for o in row["others"]] == ["other_ways", "not_now", "never"]
    assert len(row["forms"]) == 2 and row["forms"][0]["recommended"] and "recommended" not in row["forms"][1]


def test_one_day_is_said_in_the_singular():
    g = group([(0, 0), (0, 1), (0, 2)], seconds=60)
    rec = forms.recommend(g)
    assert offers.Offer(1, g, rec, NOW).to_row()["meta"] == "3 times on 1 day"

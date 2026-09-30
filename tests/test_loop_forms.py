import time

import pytest

from bombadil.loop import forms
from bombadil.loop.habits import Group

DAY = 86400


def at(day, hour, minute=0):
    """Local epoch seconds on September `day` 2026 (the 7th is a Monday) at hour:minute."""
    return time.mktime((2026, 9, day, hour, minute, 0, 0, 0, -1))


def group(n=3, verbs=None, routes=(), **kw):
    """A group as the counting leaves it, with only what a test cares about filled in."""
    verbs = verbs if verbs is not None else {"ask": n}
    kw.setdefault("times", [at(7 + i, (9 + 5 * i) % 24) for i in range(n)])   # no one time of day
    kw.setdefault("label", "some ask")
    kw.setdefault("sentences", ["some ask"])
    return Group(id="g1", n=n, verbs=verbs, routes=list(routes), **kw)


def word_group(**kw):
    kw = {"verbs": {"open": 3}, "routes": ["app:passwords", "opened"], "opens": "app:passwords",
          "label": "passwords open", "sentences": ["show me my passwords"], "word": "my passwords",
          "named": ["app:passwords"], **kw}
    return group(**kw)


ALL = frozenset({"words", "answer_cards", "agent_widgets", "create_app", "per_app_git", "autopilot", "remember"})


# -- the table --

def test_there_are_nine_forms_a_to_i_and_two_are_never_offered():
    assert [f.letter for f in forms.FORMS] == list("ABCDEFGHI")
    assert [f.letter for f in forms.FORMS if not f.offered] == ["H", "I"]
    assert forms.get("A") is forms.get("word") and forms.get("D").id == "app"
    with pytest.raises(KeyError):
        forms.get("Z")
    assert forms.TIE_ORDER == ("A", "G", "B", "F", "E", "C", "D")


def test_on_day_one_only_a_word_and_a_new_app_can_be_built():
    assert forms.BUILT == {"words", "create_app"}
    assert forms.available("A") == (True, "")
    assert forms.available("D") == (True, "")
    for letter, piece in (("B", "answer cards"), ("C", "widgets the agent makes"), ("E", "Autopilot"),
                          ("F", "Autopilot"), ("G", "remember")):
        ok, why = forms.available(letter)
        assert not ok and piece in why, (letter, why)
    assert not forms.available("H")[0] and "quietly" in forms.available("H")[1]
    assert all(forms.available(f.letter, ALL)[0] for f in forms.FORMS if f.offered)


# -- which forms a group fits --

def test_a_group_that_only_opens_one_thing_fits_a_word():
    g = word_group()
    assert forms.ideal(g) == "A" and "A" in forms.fits(g)


def test_a_group_that_opens_nothing_in_particular_does_not_fit_a_word():
    assert "A" not in forms.fits(group(verbs={"open": 3}, routes=["app:passwords"]))


def test_a_repeated_correction_of_style_fits_a_preference():
    g = group(n=4, verbs={"change": 4}, style=3, routes=["style:brevity"])
    assert forms.ideal(g) == "G"
    assert forms.ideal(group(n=4, style=2)) != "G"               # under 60%


def test_asks_that_poll_fit_a_watcher():
    g = group(verbs={"tell-me-when": 3}, routes=["files:jobs"])
    assert forms.ideal(g) == "F"
    assert forms.ideal(group(verbs={"tell-me-when": 1, "ask": 2})) != "F"


def test_the_same_change_near_one_time_on_three_days_fits_a_routine():
    times = [at(7, 8, 5), at(8, 8, 20), at(9, 7, 55), at(11, 8, 10)]
    g = group(n=4, verbs={"change": 4}, changed=4, times=times, routes=["packages"])
    assert forms.ideal(g) == "E"
    minutes, weekday = forms.clock(g)
    assert abs(minutes - 8 * 60) <= 20 and weekday == ""
    assert forms.clock(group(times=[at(7, 8), at(8, 8)])) is None                   # two days
    assert forms.clock(group(times=[at(7, 8), at(8, 13), at(9, 19)])) is None        # no one time of day
    same_day = group(times=[at(7, 8), at(7, 8, 10), at(7, 8, 20)])
    assert forms.clock(same_day) is None                                              # one day is one day
    mondays = group(times=[at(7, 9), at(14, 9, 10), at(21, 8, 55)])
    assert forms.clock(mondays)[1] == "Mondays"


def test_a_routine_is_for_changes_not_for_looking():
    times = [at(7, 8), at(8, 8), at(9, 8)]
    assert forms.ideal(group(verbs={"ask": 3}, times=times, routes=["weather"])) != "E"


def test_asks_that_read_the_machine_fit_a_card_and_a_live_answer_a_widget():
    slow = group(n=4, verbs={"ask": 4}, routes=["files:jobs", "kind:list"])
    assert forms.ideal(slow) == "B"
    live = group(n=4, verbs={"ask": 4}, routes=["memory", "processes"])
    assert forms.ideal(live) == "C"
    changes = group(n=4, verbs={"ask": 4}, changed=3, routes=["memory"])
    assert forms.ideal(changes) != "C"
    assert forms.ideal(group(verbs={"ask": 3}, routes=[])) == ""                   # it read nothing


def test_asks_that_add_to_one_set_of_data_fit_an_app():
    g = group(n=4, verbs={"log": 4}, changed=4, routes=["app:runs"])
    assert forms.ideal(g) == "D" and forms.holder(g) == "app:runs"
    files = group(n=4, verbs={"log": 3, "make": 1}, changed=4, routes=["files:home"])
    assert forms.ideal(files) == "D" and forms.holder(files) == ""
    assert forms.ideal(group(n=4, verbs={"log": 4}, changed=0, routes=["app:runs"])) == ""
    assert forms.ideal(group(n=4, verbs={"log": 4}, changed=4, routes=["memory"])) == ""


def test_retries_are_a_finding_not_an_offer():
    g = word_group(friction=2)
    assert forms.ideal(g) == "I"
    assert forms.recommend(g) is None and forms.recommend(g, ALL) is None
    assert forms.ideal(word_group(friction=1)) == "A"
    assert forms.ideal(word_group(n=6, friction=2)) == "A"                          # 2 of 6 is not most


def test_ties_go_to_the_smallest_surface_and_the_cheapest_undo():
    g = group(n=4, verbs={"ask": 4, "tell-me-when": 4}, routes=["memory"], style=4, opens="app:x",
              times=[at(7, 8), at(8, 8), at(9, 8)], changed=4)
    order = forms.fits(g)
    assert order == sorted(order, key=forms.TIE_ORDER.index)
    assert order[:2] == ["A", "G"]


# -- choosing --

def test_a_word_is_what_a_near_miss_becomes_on_day_one():
    rec = forms.recommend(word_group(), titles={"app:passwords": "Passwords"})
    assert rec.form.letter == "A" and rec.op == "accept" and not rec.already
    assert rec.sentence == "Say “my passwords” and Passwords opens. No model, under a tenth of a second."
    assert rec.others == []


def test_what_already_has_a_word_only_gets_a_got_it():
    g = word_group(existing="passwords")
    rec = forms.recommend(g, titles={"app:passwords": "Passwords"})
    assert (rec.form.letter, rec.op, rec.already) == ("A", "got_it", True)
    assert rec.sentence == "Passwords already opens with the word passwords."


def test_a_panel_has_its_own_name():
    g = word_group(opens="panel:browser", routes=["panel:browser", "opened"], existing="browser", word="browser")
    assert forms.recommend(g).sentence == "The browser already opens with the word browser."
    assert forms.thing_title("panel:browser") == "the browser"
    assert forms.thing_title("panel:files") == "Files"


def test_a_form_that_cannot_be_built_is_passed_over_for_one_that_can():
    g = group(n=4, verbs={"log": 4}, changed=4, routes=["files:home"], style=0)
    rec = forms.recommend(g)
    assert rec.form.letter == "D" and rec.ideal == "D"
    # the brief's pick is a card, but only a word or an app can be made today, and nothing else fits
    reads = group(n=4, verbs={"ask": 4}, routes=["files:jobs"])
    assert forms.ideal(reads) == "B" and forms.recommend(reads) is None
    both = group(n=4, verbs={"ask": 2, "log": 2}, changed=4, routes=["files:jobs"])
    assert forms.recommend(both).form.letter == "D"


def test_extending_the_app_that_holds_the_data_waits_for_per_app_git():
    g = group(n=4, verbs={"log": 4}, changed=4, routes=["app:runs"])
    assert forms.recommend(g) is None                               # a second app beside it would not do
    rec = forms.recommend(g, ALL)
    assert rec.form.letter == "D"


def test_with_everything_built_up_to_two_other_ways_are_offered():
    g = group(n=4, verbs={"ask": 4}, routes=["memory", "processes"])
    rec = forms.recommend(g, ALL)
    assert rec.form.letter == "C" and [f.letter for f in rec.others] == ["B"]
    crowded = group(n=4, verbs={"ask": 4, "tell-me-when": 4}, style=4, opens="app:x", routes=["memory"],
                    times=[at(7, 8), at(8, 8), at(9, 8)], changed=4)
    rec = forms.recommend(crowded, ALL)
    assert len(rec.others) <= 2 and rec.form not in rec.others
    assert len(forms.other_ways(crowded, rec.form.letter, ALL)) <= 2


def test_a_form_he_said_never_to_three_times_is_not_offered_again():
    g = word_group()
    assert forms.recommend(g, stopped=["A"]) is None
    assert forms.recommend(g, stopped=["word"]) is None             # by id or by letter
    both = word_group(verbs={"ask": 3})                               # it also reads: a card would do
    assert forms.recommend(both, ALL).form.letter == "A"
    assert forms.recommend(both, ALL, stopped=["A"]).form.letter == "B"


def test_on_day_one_nothing_else_is_ever_recommended():
    archetypes = [
        word_group(), word_group(existing="passwords"),
        group(n=4, verbs={"log": 4}, changed=4, routes=["files:home"]),
        group(n=4, verbs={"log": 4}, changed=4, routes=["app:runs"]),
        group(n=4, verbs={"ask": 4}, routes=["memory"]), group(n=4, verbs={"ask": 4}, routes=["files:jobs"]),
        group(n=4, verbs={"change": 4}, style=4, routes=["style:brevity"]),
        group(n=4, verbs={"tell-me-when": 4}, routes=["files:jobs"]),
        group(n=4, verbs={"change": 4}, changed=4, times=[at(7, 8), at(8, 8), at(9, 8)], routes=["packages"]),
    ]
    for g in archetypes:
        rec = forms.recommend(g)
        assert rec is None or rec.form.letter in ("A", "D"), g


# -- saying it --

def test_each_form_says_what_would_happen_in_his_words():
    g = word_group(label="passwords open", sentences=["show me my passwords", "open passwords"])
    titles = {"app:passwords": "Passwords"}
    assert "my passwords" in forms.describe(g, "A", titles)
    assert "passwords open" in forms.describe(g, "D", titles)
    assert "show me my passwords" in forms.describe(g, "G", titles)
    assert "passwords open" in forms.describe(g, "F", titles)
    routine = group(n=3, verbs={"change": 3}, changed=3, times=[at(7, 8), at(8, 8), at(9, 8)],
                    sentences=["update my system"])
    assert forms.describe(routine, "E") == "Do “update my system” for you around 08:00. You can stop it from Autopilot."
    weekly = group(n=3, verbs={"change": 3}, changed=3, times=[at(7, 8), at(14, 8), at(21, 8)],
                   sentences=["update my system"])
    assert "every Monday at 08:00" in forms.describe(weekly, "E")


def test_a_group_with_no_words_left_still_has_a_sentence():
    g = group(label="", sentences=[], word="")
    assert forms.describe(g, "D")                                   # never a KeyError, never an empty line
    assert forms.thing_title("") == "it"
    assert forms.thing_title("app:moon-phase") == "Moon Phase"


def test_the_preview_is_made_of_his_counts_and_his_words_with_no_model():
    g = word_group(days=["2026-09-07", "2026-09-09", "2026-09-12"], sentences=["show me my passwords"])
    lines = forms.preview(g, "A", {"app:passwords": "Passwords"}).splitlines()
    assert lines[0] == "You said: “show me my passwords”"
    assert lines[1] == "3 times on 3 days."
    assert lines[2].startswith("A word: Say “my passwords” and Passwords opens.")
    assert lines[3] == "Nothing else changes, and Undo puts it back."
    one = forms.preview(word_group(n=1, days=["2026-09-07"]), "A")
    assert "1 times on 1 day." in one

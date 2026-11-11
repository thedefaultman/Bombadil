"""Shaping the desk: a widget is kept (on the desk at rest), needed (the default) or off (put away).
Only a widget with a calm face can be kept; "hide" on a kept widget is a step, not a leap; the Widgets card's
parts set a state at once; "reset" is the desk as it ships."""

import random
import threading
import tomllib

import pytest

from bombadil import desk, paths


def fresh() -> desk.Desk:
    return desk.Desk()


def kept(d: desk.Desk) -> list[str]:
    return d.snapshot()["kept"]


def test_the_shipped_desk_keeps_nothing_and_can_be_asked_which_state_each_widget_is_in(home):
    d = fresh()
    assert kept(d) == []
    assert [d.state_of(w) for w in ("now", "watching", "needs", "machine", "alive")] == \
        ["needed", "needed", "needed", "needed", "off"]
    assert d.state_of("Machine") == "needed" and d.state_of("needs you") == "needed"
    assert d.state_of("sofa") is None and d.state_of(None) is None


@pytest.mark.parametrize("ops, ok, text", [
    ([("keep", "machine")], True, "Machine stays on the desk."),
    ([("keep", "Machine")], True, "Machine stays on the desk."),
    ([("keep", "machine"), ("keep", "machine")], True, "Machine is already kept on the desk."),
    # A widget with nothing to show at rest says when it does show, and nothing changes.
    ([("keep", "now")], True, "Now shows only while a turn has steps to follow."),
    ([("keep", "watching")], True, "Watching shows only while something counts."),
    ([("keep", "needs")], True, "Needs you shows only while two or more sessions wait."),
    ([("show", "alive"), ("keep", "alive")], True, "Alive shows only while something is being touched."),
    ([("keep", "away")], True, "Away shows only after you have been away."),
    ([("keep", "sofa")], False, ("There is no widget called 'sofa'. The widgets are Now, Watching, Alive, "
                                 "Needs you, Away and Machine.")),
    ([("keep", None)], False, "Which widget? The widgets are Now, Watching, Alive, Needs you, Away and Machine."),
    # ...unless it is put away, where it does not show at all: the answer says how it comes back. Alive ships that way.
    ([("hide", "watching"), ("keep", "watching")], True, "Watching is put away. Say show watching to bring it back."),
    ([("keep", "alive")], True, "Alive is put away. Say show alive to bring it back."),
    # Hide a kept widget: first it is no longer kept, and only the next hide puts it away.
    ([("keep", "machine"), ("hide", "machine")], True,
     "Machine is no longer kept. It still comes up when something needs a look."),
    ([("keep", "machine"), ("hide", "machine"), ("hide", "machine")], True, "Put Machine away."),
    ([("keep", "machine"), ("hide", "machine"), ("hide", "machine"), ("hide", "machine")], True,
     "Machine is already put away."),
    # Keeping what was put away brings it back, kept.
    ([("hide", "machine"), ("keep", "machine")], True, "Machine stays on the desk."),
    # Showing a kept widget changes nothing: it is on the desk.
    ([("keep", "machine"), ("show", "machine")], True, "Here is the machine."),
    ([("keep", "machine"), ("fold", "machine"), ("keep", "machine")], True, "Unfolded Machine."),
    # The Widgets card.
    ([("widgets",)], True, "Here are the widgets."),
    ([("set", "machine", None, None, "keep")], True, "Machine stays on the desk."),
    ([("set", "machine", None, None, "needed")], True, "Machine is already on the desk."),
    ([("set", "machine", None, None, "off")], True, "Put Machine away."),
    ([("set", "machine", None, None, "off"), ("set", "machine", None, None, "off")], True,
     "Machine is already put away."),
    ([("set", "machine", None, None, "off"), ("set", "machine", None, None, "needed")], True,
     "Put Machine on the desk."),
    ([("keep", "machine"), ("set", "machine", None, None, "needed")], True,
     "Machine is no longer kept. It still comes up when something needs a look."),
    # Off from kept is one press: it does not stop at "no longer kept".
    ([("keep", "machine"), ("set", "machine", None, None, "off")], True, "Put Machine away."),
    ([("set", "needs", None, None, "off")], False, "Needs you cannot be hidden."),
    ([("set", "needs", None, None, "needed")], True, "Needs you is already on the desk."),
    ([("set", "needs", None, None, "keep")], True, "Needs you shows only while two or more sessions wait."),
    ([("set", "now", None, None, "keep")], True, "Now shows only while a turn has steps to follow."),
    ([("set", "machine", None, None, "loud")], False, "A widget is kept, shown when needed, or off."),
    ([("set", "machine", None, None, None)], False, "A widget is kept, shown when needed, or off."),
    ([("set", "sofa", None, None, "off")], False, ("There is no widget called 'sofa'. The widgets are Now, "
                                                    "Watching, Alive, Needs you, Away and Machine.")),
    ([("set", None, None, None, "off")], False,
     "Which widget? The widgets are Now, Watching, Alive, Needs you, Away and Machine."),
])
def test_what_keeping_says_and_does(home, ops, ok, text):
    d = fresh()
    for op in ops[:-1]:
        d.apply(*op)
    assert d.apply(*ops[-1]) == (ok, text)


def test_a_widget_that_cannot_be_kept_is_never_kept_by_any_door(home):
    d = fresh()
    for w in ("now", "watching", "needs", "alive", "away"):
        d.apply("keep", w)
        d.apply("set", w, state="keep")
    assert kept(d) == [] and not paths.desk_file().exists()


def test_a_kept_widget_is_off_the_desks_off_list_and_the_other_way_round(home):
    d = fresh()
    d.apply("keep", "machine")
    s = d.snapshot()
    assert s["kept"] == ["machine"] and "machine" not in s["hidden"]
    d.apply("set", "machine", state="off")
    s = d.snapshot()
    assert s["kept"] == [] and "machine" in s["hidden"]
    d.apply("keep", "machine")
    s = d.snapshot()
    assert s["kept"] == ["machine"] and "machine" not in s["hidden"]


def test_keeping_a_widget_that_was_folded_by_hand_gives_its_card_back(home):
    d = fresh()
    d.apply("fold", "machine")
    assert d.apply("keep", "machine") == (True, "Machine stays on the desk.")
    assert d.snapshot()["stripped"] == [] and kept(d) == ["machine"]
    # It was kept already and folded again: asking for it unfolds it, and that is a change that is told.
    d.apply("fold", "machine")
    calls = []
    d.on_change = lambda: calls.append(1)
    assert d.apply("keep", "machine") == (True, "Unfolded Machine.")
    assert calls == [1] and d.snapshot()["stripped"] == []


def test_the_state_in_words_says_what_is_kept(home):
    d = fresh()
    d.apply("keep", "machine")
    assert d.apply("state")[1].endswith("Right rail: Needs you, Away, Machine (kept on the desk).")
    d.apply("fold", "machine")
    assert d.apply("state")[1].endswith("Machine (kept on the desk) (folded to a strip).")


def test_a_change_of_state_calls_back_once_and_a_repeat_does_not(home):
    d = fresh()
    seen = []
    d.on_change = lambda: seen.append(kept(d))
    d.apply("keep", "machine")
    d.apply("keep", "machine")                          # already kept
    d.apply("set", "machine", state="keep")             # already kept
    d.apply("keep", "now")                              # nothing to show at rest: nothing changed
    d.apply("set", "needs", state="off")                # refused
    d.apply("set", "machine", state="loud")             # refused
    d.apply("widgets")                                  # a card opens; the desk is as it was
    d.apply("set", "machine", state="needed")
    assert seen == [["machine"], []]


def test_a_kept_widget_survives_a_restart_and_the_file_says_so(home):
    d = fresh()
    d.apply("keep", "machine")
    again = desk.Desk().load()
    assert again.snapshot() == d.snapshot() and kept(again) == ["machine"]
    assert tomllib.loads(paths.desk_file().read_text())["kept"] == ["machine"]
    again.apply("hide", "machine")
    assert tomllib.loads(paths.desk_file().read_text())["kept"] == []
    assert kept(desk.Desk().load()) == []


def test_a_desk_file_from_before_keeping_is_a_desk_that_keeps_nothing(home):
    path = paths.desk_file()
    path.parent.mkdir(parents=True)
    path.write_text('folded = false\nhidden = ["alive", "machine"]\nstripped = []\n')
    d = desk.Desk().load()
    assert kept(d) == [] and d.state_of("machine") == "off"


@pytest.mark.parametrize("content, expected", [
    ('kept = ["machine"]\n', ["machine"]),
    ('kept = ["machine", "machine"]\n', ["machine"]),
    ('kept = ["Machine"]\n', []),                       # the file names ids, not words
    ('kept = ["now", "watching", "needs", "alive", "away"]\n', []),      # nothing without a calm face
    ('kept = ["sofa", 3, true, ["machine"]]\n', []),
    ('kept = "machine"\n', []),
    ('kept = 3\n', []),
    ('kept = []\n', []),
    ('kept = ["machine"]\nhidden = ["machine"]\n', []),     # of the two a hand-edited file can say, off wins
    ('hidden = ["machine"]\nkept = ["machine"]\n', []),
    ('kept = ["machine"]\nhidden = ["alive"]\n', ["machine"]),
])
def test_a_hand_edited_kept_list_keeps_only_what_can_be_kept(home, content, expected):
    path = paths.desk_file()
    path.parent.mkdir(parents=True)
    path.write_text(content)
    d = desk.Desk().load()
    assert kept(d) == expected
    # What it makes of the file is a desk that is consistent, and that says the same after a round trip.
    d.save()
    assert desk.Desk().load().snapshot() == d.snapshot()


def test_reset_is_the_desk_as_it_ships_and_leaves_the_screen_alone(home):
    d = fresh()
    d.screen = "DP-1"
    d.apply("keep", "machine")
    d.apply("hide", "now")
    d.apply("show", "alive")
    d.apply("move", "watching", "right", 0)
    d.apply("fold", "needs")
    d.apply("toggle")
    assert d.apply("reset") == (True, "The desk is back as it came.")
    shipped = fresh().snapshot() | {"screen": "DP-1"}
    assert d.snapshot() == shipped
    assert desk.Desk().load().snapshot() == shipped
    assert d.apply("reset") == (True, "The desk is already as it came.")


def test_reset_of_a_desk_that_is_as_it_came_changes_and_saves_nothing(home):
    d = fresh()
    calls = []
    d.on_change = lambda: calls.append(1)
    assert d.apply("reset") == (True, "The desk is already as it came.")
    assert calls == [] and not paths.desk_file().exists()


def test_reset_calls_back_once(home):
    d = fresh()
    d.apply("keep", "machine")
    calls = []
    d.on_change = lambda: calls.append(1)
    d.apply("reset")
    d.apply("reset")
    assert calls == [1]


@pytest.mark.parametrize("op", [
    ("toggle",), ("fold", "machine"), ("hide", "now"), ("keep", "machine"),
    ("move", "now", "left", 1), ("move", "watching", "right", 0),
    ("set", "machine", None, None, "off"),
])
def test_reset_after_any_one_change_puts_that_one_back_and_says_so(home, op):
    # The check for "already as it came" is only as good as what it compares: each part of the desk by itself.
    d = fresh()
    d.apply(*op)
    assert d.snapshot() != fresh().snapshot(), op                      # the change is a real one
    calls = []
    d.on_change = lambda: calls.append(1)
    assert d.apply("reset") == (True, "The desk is back as it came.")
    assert calls == [1]
    assert d.snapshot() == fresh().snapshot()
    assert desk.Desk().load().snapshot() == fresh().snapshot()           # ...and the file too, so a restart agrees


def test_reset_after_showing_alive_puts_it_away_again(home):
    d = fresh()
    d.apply("show", "alive")
    assert "alive" not in d.hidden
    assert d.apply("reset") == (True, "The desk is back as it came.") and "alive" in d.hidden


def test_a_repeated_off_changes_nothing_saves_nothing_and_calls_nobody(home):
    d = fresh()
    d.apply("set", "machine", state="off")
    stamp = paths.desk_file().stat().st_mtime_ns
    calls = []
    d.on_change = lambda: calls.append(1)
    assert d.apply("set", "machine", state="off") == (True, "Machine is already put away.")
    assert d.apply("hide", "machine") == (True, "Machine is already put away.")
    assert calls == [] and paths.desk_file().stat().st_mtime_ns == stamp


@pytest.mark.parametrize("op", [("keep", "away"), ("set", "away", None, None, "keep"), ("set", "away", None, None, "needed"),
                                ("set", "machine", None, None, "keep"), ("keep", "machine"), ("reset",),
                                ("widgets",), ("set", "away", None, None, "off")])
def test_none_of_the_keep_ops_asks_for_a_card(home, op):
    # "show away" and "show machine" raise their card on request (on_ask). Keeping, setting, resetting and opening the
    # Widgets card are not a request for one: "keep away" must never bring the Away card up.
    d = fresh()
    asked = []
    d.on_ask = asked.append
    d.apply(*op)
    assert asked == []


def test_keep_away_changes_nothing_so_an_away_card_with_no_rows_is_never_kept_and_still_goes_when_seen(home):
    # Away shows only after the person has been away, and it has no calm face: there is nothing to keep, so nothing is,
    # and the Away card stays what agentd's `away` message makes it (rows to show, or gone once seen).
    d = fresh()
    calls = []
    d.on_change = lambda: calls.append(1)
    assert d.apply("keep", "away") == (True, "Away shows only after you have been away.")
    assert d.apply("set", "away", state="keep") == (True, "Away shows only after you have been away.")
    assert calls == [] and not paths.desk_file().exists()
    assert d.kept == set() and "away" not in d.hidden
    away = next(r for r in d.snapshot()["widgets"] if r["id"] == "away")
    assert away["can"] == ["needed", "off"]                          # Keep is drawn faint on its row


def test_a_file_that_keeps_away_by_hand_keeps_nothing(home):
    paths.desk_file().parent.mkdir(parents=True, exist_ok=True)
    paths.desk_file().write_text('kept = ["away", "alive", "machine"]\n')
    assert kept(desk.Desk().load()) == ["machine"]


def test_the_widgets_card_lists_what_the_desk_lists_and_what_each_can_be(home):
    rows = fresh().snapshot()["widgets"]
    assert [r["id"] for r in rows] == ["now", "watching", "needs", "away", "machine"]      # Alive waits for its feed
    assert {r["id"]: r["can"] for r in rows} == {
        "now": ["needed", "off"], "watching": ["needed", "off"], "needs": ["needed"], "away": ["needed", "off"],
        "machine": ["keep", "needed", "off"]}
    assert all(r["title"] and r["question"].endswith("?") for r in rows)
    # Every state a row says it can take is one the desk really takes, from wherever the widget stands.
    for r in rows:
        for first in ("keep", "needed", "off"):
            for state in r["can"]:
                d = fresh()
                d.apply("set", r["id"], state=first)
                ok, _ = d.apply("set", r["id"], state=state)
                assert ok and d.state_of(r["id"]) == state, (r["id"], first, state)


def test_which_states_a_widget_can_take():
    assert desk.can("machine") == ("keep", "needed", "off")
    assert desk.can("now") == ("needed", "off") and desk.can("watching") == ("needed", "off")
    assert desk.can("needs") == ("needed",) and desk.can("Needs you") == ("needed",)
    assert desk.can("sofa") == () and desk.can(None) == ()


def test_asking_for_the_widgets_card_calls_the_listener_and_a_broken_one_costs_nothing(home, capsys):
    d = fresh()
    asked = []
    d.on_widgets = lambda: asked.append(1)
    assert d.apply("widgets") == (True, "Here are the widgets.")
    d.apply("state")
    d.apply("keep", "machine")
    assert asked == [1]
    d.on_widgets = lambda: 1 / 0
    assert d.apply("widgets") == (True, "Here are the widgets.")
    assert "on_widgets" in capsys.readouterr().err


def test_the_line_for_a_kept_widget_names_it_the_way_the_desk_does(home):
    d = fresh()
    assert d.apply("keep", "machine")[1].startswith(desk.title("machine"))


def _consistent(d: desk.Desk):
    s = d.snapshot()
    assert set(s["kept"]) <= {w for w, wd in desk.WIDGETS.items() if wd.calm}
    assert not set(s["kept"]) & set(s["hidden"])                # kept and off are not both true
    assert s["kept"] == [w for w in desk.WIDGETS if w in s["kept"]]
    assert "needs" not in s["hidden"]
    for r in s["widgets"]:
        assert d.state_of(r["id"]) in ("keep", "needed", "off")
        if d.state_of(r["id"]) != "needed":
            assert d.state_of(r["id"]) in r["can"]


def test_any_run_of_ops_leaves_a_consistent_desk_that_round_trips_kept_included(home):
    rng = random.Random(11)
    d = fresh()
    for _ in range(600):
        w = rng.choice([*desk.WIDGETS, "needs you", "Machine", "sofa", None])
        op = rng.choice(["hide", "show", "keep", "keep", "set", "set", "reset", "widgets", "toggle", "fold",
                         "unfold", "move", "state", "dance"])
        d.apply(op, w, rng.choice([None, "left", "right", "up"]), rng.choice([None, -1, 0, 1, 2, 9, "x", 1.5]),
                rng.choice([None, "keep", "needed", "off", "off", "needed", "loud", 3]))
        _consistent(d)
    assert desk.Desk().load().snapshot() == d.snapshot()


def test_the_launcher_threads_and_the_shell_can_keep_and_reset_at_once(home):
    d = fresh()
    errors = []

    def worker(seed):
        rng = random.Random(seed)
        try:
            for _ in range(150):
                d.apply(rng.choice(["keep", "set", "hide", "show", "reset", "state"]),
                        rng.choice(["machine", "now", "needs"]), None, None,
                        rng.choice(["keep", "needed", "off"]))
                _consistent(d)
        except Exception as e:  # noqa: BLE001 - what the test is about
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert desk.Desk().load().snapshot() == d.snapshot()


@pytest.mark.parametrize("prompt", [
    "keep machine", "keep the machine on the desk", "keep the machine on my desk please", "show me my widgets",
    "open the widgets card", "reset the desk", "put the desk back", "which widgets can I keep?",
    "keep the machine up", "keep machine where it is",
])
def test_the_words_that_ask_for_the_desk_include_keeping_and_the_widgets(prompt):
    assert desk.asked_for_desk(prompt)


@pytest.mark.parametrize("prompt", [
    "keep the machine running", "keep machine learning notes", "keep the machine learning course",
    "how do I keep my machine cool?", "keep going",
])
def test_the_words_that_keep_something_else_do_not(prompt):
    assert not desk.asked_for_desk(prompt)

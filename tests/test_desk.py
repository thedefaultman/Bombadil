import random
import threading
import tomllib

import pytest

from bombadil import desk, paths


def fresh(home) -> desk.Desk:
    return desk.Desk()


def test_a_new_desk_is_the_one_the_shell_expects(home):
    assert desk.Desk().snapshot() == {
        "type": "desk", "folded": False, "hidden": ["alive"], "stripped": [], "screen": "",
        "rails": {"now": "left", "watching": "left", "needs": "right", "machine": "right", "away": "right",
                  "alive": "left"},
        "order": {"left": ["now", "watching", "alive"], "right": ["needs", "away", "machine"]}}


@pytest.mark.parametrize("ops, ok, text", [
    ([("hide", "machine")], True, "Put Machine away."),
    ([("hide", "Machine")], True, "Put Machine away."),
    ([("hide", "machine"), ("show", "machine")], True, "Here is the machine. It is back on the desk."),
    ([("show", "machine")], True, "Here is the machine."),
    ([("hide", "machine"), ("hide", "machine")], True, "Machine is already put away."),
    ([("show", "now")], True, "Now is already on the desk."),
    ([("show", "alive")], True, "Put Alive on the desk."),
    ([("hide", "needs")], False, "Needs you cannot be hidden."),
    ([("hide", "needs you")], False, "Needs you cannot be hidden."),
    ([("hide", "while you were away")], True, "Put Away away."),
    ([("toggle",)], True, "Folded the desk."),
    ([("toggle",), ("toggle",)], True, "Unfolded the desk."),
    ([("fold",), ("fold",)], True, "The desk is already folded."),
    ([("unfold",)], True, "The desk is already unfolded."),
    ([("fold",), ("unfold",)], True, "Unfolded the desk."),
    ([("fold", "machine")], True, "Folded Machine to its strip."),
    ([("fold", "Needs you")], True, "Folded Needs you to its strip."),
    ([("fold", "machine"), ("fold", "machine")], True, "Machine is already folded."),
    ([("unfold", "machine")], True, "Machine is already unfolded."),
    ([("fold", "machine"), ("unfold", "machine")], True, "Unfolded Machine."),
    ([("fold", "sofa")], False, ("There is no widget called 'sofa'. The widgets are Now, Watching, Alive, "
                                 "Needs you, Away and Machine.")),
    ([("fold", "machine"), ("show", "machine")], True, "Here is the machine."),
    ([("fold", "watching"), ("show", "watching")], True, "Unfolded Watching."),
    ([("fold", "alive"), ("show", "alive")], True, "Put Alive on the desk."),
    ([("fold", "watching"), ("move", "watching", None, 1)], True, "Unfolded Watching."),
    ([("fold", "watching"), ("move", "watching", "left")], True, "Unfolded Watching."),
    ([("fold", "watching"), ("move", "watching", "right", 0)], True, "Moved Watching to the right rail."),
    ([("fold", "now"), ("move", "now", None, 2)], True, "Moved Now further from the pill."),
    ([("hide", "sofa")], False, ("There is no widget called 'sofa'. The widgets are Now, Watching, Alive, "
                                 "Needs you, Away and Machine.")),
    ([("hide", None)], False, "Which widget? The widgets are Now, Watching, Alive, Needs you, Away and Machine."),
    ([("make", "batch")], False, "The desk cannot make."),
    ([("move", "watching", "right", 0)], True, "Moved Watching to the right rail."),
    ([("move", "watching", "right")], True, "Moved Watching to the right rail."),
    ([("move", "watching", "left")], True, "Watching is already in the left rail."),
    ([("move", "watching", None, 1)], True, "Watching is already there."),
    ([("move", "now", None, 2)], True, "Moved Now further from the pill."),
    ([("move", "alive", "left", 0)], True, "Moved Alive nearer the pill."),
    ([("move", "watching", "top", 0)], False, "The rails are left and right."),
    ([("move", "watching", "left", "first")], False, "The place is a whole number: 0 is nearest the pill."),
    ([("move", "watching", "left", True)], False, "The place is a whole number: 0 is nearest the pill."),
    ([("move", "watching")], False, "Say which rail, or which place in it, to move it to."),
])
def test_what_each_op_does_and_says(home, ops, ok, text):
    d = fresh(home)
    for op in ops[:-1]:
        d.apply(*op)
    assert d.apply(*ops[-1]) == (ok, text)


def test_a_move_puts_the_widget_where_it_was_asked(home):
    d = fresh(home)
    d.apply("move", "watching", "right", 0)
    assert d.snapshot()["order"] == {"left": ["now", "alive"], "right": ["watching", "needs", "away", "machine"]}
    assert d.snapshot()["rails"]["watching"] == "right"
    d.apply("move", "now", "right")        # no place: last, so it never takes the slot by the pill
    assert d.snapshot()["order"]["right"] == ["watching", "needs", "away", "machine", "now"]
    d.apply("move", "now", "right", 99)    # past the end is the end; below zero is the pill
    assert d.snapshot()["order"]["right"][-1] == "now"
    d.apply("move", "machine", "right", -3)
    assert d.snapshot()["order"]["right"][0] == "machine"


def test_folding_one_widget_is_not_folding_the_desk(home):
    d = fresh(home)
    d.apply("fold", "machine")
    assert d.snapshot()["stripped"] == ["machine"] and d.snapshot()["folded"] is False
    d.apply("toggle")
    assert d.snapshot()["stripped"] == ["machine"] and d.snapshot()["folded"] is True
    d.apply("unfold")        # the word "desk" unfolds the desk and leaves the hand-folded card where it is
    assert d.snapshot()["stripped"] == ["machine"] and d.snapshot()["folded"] is False
    d.apply("unfold", "machine")
    assert d.snapshot()["stripped"] == []


def test_a_hand_folded_widget_keeps_its_place_and_asking_or_moving_gives_the_card_back(home):
    d = fresh(home)
    before = d.snapshot()["order"]
    d.apply("fold", "watching")
    assert d.snapshot()["order"] == before                       # still second in its rail, only a strip
    assert d.apply("hide", "watching") == (True, "Put Watching away.")
    assert d.snapshot()["stripped"] == ["watching"]              # put away is another thing
    assert d.apply("show", "watching") == (True, "Put Watching on the desk.")
    assert d.snapshot()["stripped"] == []                        # asking wants the card
    d.apply("fold", "needs")
    assert d.apply("hide", "needs")[0] is False and d.snapshot()["stripped"] == ["needs"]
    d.apply("move", "needs", "left", 0)                          # a strip dragged into a rail
    assert d.snapshot()["stripped"] == [] and d.snapshot()["order"]["left"][0] == "needs"


def test_an_unchanged_move_still_gives_the_card_back_and_says_it_changed(home):
    d = fresh(home)
    changes = []
    d.on_change = lambda: changes.append(1)
    d.apply("fold", "watching")
    d.apply("move", "watching", None, 1)
    assert d.snapshot()["stripped"] == [] and len(changes) == 2
    d.apply("move", "watching", None, 1)          # now it is only where it already is
    assert len(changes) == 2


@pytest.mark.parametrize("widget", ["machine", "watching"])
def test_showing_a_hand_folded_widget_is_a_change_that_is_saved_and_told(home, widget):
    d = fresh(home)
    d.apply("fold", widget)
    changes = []
    d.on_change = lambda: changes.append(1)
    d.apply("show", widget)
    assert d.snapshot()["stripped"] == [] and changes == [1]
    assert desk.Desk().load().snapshot()["stripped"] == []


def test_hiding_keeps_the_place_so_showing_puts_it_back(home):
    d = fresh(home)
    before = d.snapshot()["order"]
    d.apply("hide", "watching")
    assert d.snapshot()["order"] == before and "watching" in d.snapshot()["hidden"]
    d.apply("show", "watching")
    assert d.snapshot()["order"] == before and d.snapshot()["hidden"] == ["alive"]


def test_needs_can_never_be_hidden_even_by_a_hand_edited_file(home):
    d = fresh(home)
    assert d.apply("hide", "needs")[0] is False and "needs" not in d.snapshot()["hidden"]
    path = paths.desk_file()
    path.parent.mkdir(parents=True)
    path.write_text('hidden = ["needs", "machine"]\n')
    assert desk.Desk().load().snapshot()["hidden"] == ["machine"]


def test_state_is_said_in_words_and_changes_nothing(home):
    d = fresh(home)
    assert d.apply("state") == (True, ("The desk is open. Left rail, nearest the pill first: Now, Watching, "
                                       "Alive (put away). Right rail: Needs you, Away, Machine."))
    assert not paths.desk_file().exists()
    d.apply("fold", "machine")
    assert d.apply("state")[1].endswith("Right rail: Needs you, Away, Machine (folded to a strip).")
    d.apply("unfold", "machine")
    d.apply("fold")
    assert d.apply("state")[1].startswith("The desk is folded to strips.")


def test_a_change_survives_a_restart(home):
    d = fresh(home)
    d.apply("hide", "machine")
    d.apply("show", "alive")
    d.apply("move", "watching", "right", 1)
    d.apply("fold", "needs")
    d.apply("toggle")
    again = desk.Desk().load()
    assert again.snapshot() == d.snapshot()
    assert again.snapshot()["folded"] is True and again.snapshot()["hidden"] == ["machine"]
    assert again.snapshot()["stripped"] == ["needs"]
    assert again.snapshot()["order"]["right"] == ["needs", "watching", "away", "machine"]


def test_the_file_is_small_toml_anyone_can_read(home):
    d = fresh(home)
    d.apply("move", "now", "right", 0)
    d.screen = "DP-1"
    d.save()
    text = paths.desk_file().read_text()
    assert text.startswith("# Bombadil's desk")
    data = tomllib.loads(text)
    assert data["folded"] is False and data["screen"] == "DP-1" and data["hidden"] == ["alive"]
    assert data["stripped"] == []
    assert data["rails"]["now"] == "right" and data["order"]["right"][0] == "now"
    assert not list(paths.desk_file().parent.glob("*.tmp"))   # written whole, then moved into place


def test_no_file_is_the_default_desk_and_load_writes_nothing(home):
    assert desk.Desk().load().snapshot() == desk.Desk().snapshot()
    assert not paths.desk_file().exists()


@pytest.mark.parametrize("content", [
    "this is = not [valid toml",
    "\x00\x01\x02",
    "",
    "folded = 5\nhidden = 'machine'\n[rails]\nnow = 3\n[order]\nleft = 'now'\n",
    "[rails]\nnow = ['left']\n[order]\nleft = [['now'], 4, {a = 1}]\nright = 7\n",
    "hidden = [['a'], 3, 'nothing']\nscreen = 4\n",
    "stripped = 'machine'\n",
    "stripped = [['machine'], 3, 'nothing', {a = 1}]\n",
])
def test_a_corrupt_or_odd_file_gives_a_working_default_desk(home, content):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_text(content)
    d = desk.Desk().load()
    assert d.snapshot()["order"] == {"left": ["now", "watching", "alive"], "right": ["needs", "away", "machine"]}
    assert d.snapshot()["rails"] == desk.Desk().snapshot()["rails"] and d.snapshot()["screen"] == ""
    assert d.apply("hide", "machine") == (True, "Put Machine away.")   # and the file can be saved over
    assert "machine" in desk.Desk().load().snapshot()["hidden"]


def test_a_hand_edit_may_fold_any_widget_even_needs_you_but_only_the_ones_there_are(home):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_text('stripped = ["needs", "ghost", "machine", 5, "machine"]\n')
    assert desk.Desk().load().snapshot()["stripped"] == ["needs", "machine"]   # in the widgets' own order


@pytest.mark.parametrize("screen", [
    '"a\\u0007b"', '"' + "x" * 41 + '"', '"DP-1\\nfolded"', '""', '"ÄÖ"', '"<script>"',
])
def test_a_screen_name_is_an_output_name_or_nothing(home, screen):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_text(f"screen = {screen}\n")
    assert desk.Desk().load().snapshot()["screen"] == ""


@pytest.mark.parametrize("screen", ["DP-1", "HDMI-A-1", "Virtual-1", "eDP-1", "Dell Inc. 27:a/b"])
def test_an_output_name_is_kept(home, screen):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_text(f'screen = "{screen}"\n')
    assert desk.Desk().load().snapshot()["screen"] == screen


def test_a_file_that_fails_in_some_unexpected_way_is_still_the_default_desk(home, monkeypatch):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_text("folded = true\n")
    with monkeypatch.context() as m:
        m.setattr(desk.tomllib, "loads", lambda _t: (_ for _ in ()).throw(RecursionError("deep")))
        assert desk.Desk().load().snapshot() == desk.Desk().snapshot()
    paths.desk_file().unlink()
    paths.desk_file().mkdir()   # a directory where the file should be
    assert desk.Desk().load().snapshot() == desk.Desk().snapshot()


def test_bytes_that_are_not_text_are_no_crash(home):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_bytes(b"\xff\xfe\x00folded = true")
    assert desk.Desk().load().snapshot()["folded"] is False


def test_a_hand_edit_moves_a_widget_and_the_rest_is_kept(home):
    paths.desk_file().parent.mkdir(parents=True)
    paths.desk_file().write_text(
        'folded = true\nhidden = []\nscreen = "HDMI-A-1"\n'
        '[rails]\nwatching = "right"\nnope = "left"\nmachine = "sideways"\n'
        '[order]\nleft = ["alive", "now", "watching", "ghost", "now"]\nright = ["machine"]\n')
    s = desk.Desk().load().snapshot()
    assert s["folded"] is True and s["hidden"] == [] and s["screen"] == "HDMI-A-1"
    # watching is listed under left but its rail says right, so it is placed on the right with
    # the widgets the order does not list; machine keeps its default rail (the odd value is
    # ignored) and the place the order gives it.
    assert s["rails"]["watching"] == "right" and s["rails"]["machine"] == "right"
    assert s["order"] == {"left": ["alive", "now"], "right": ["machine", "watching", "needs", "away"]}


def test_a_disk_that_will_not_take_the_file_costs_nothing(home, capsys):
    (home / "state").write_text("a file where the directory should be")
    d = fresh(home)
    assert d.apply("hide", "machine") == (True, "Put Machine away.")
    assert "machine" in d.snapshot()["hidden"]
    assert "could not save" in capsys.readouterr().err


def test_a_change_calls_back_once_and_no_change_does_not(home):
    d = fresh(home)
    calls = []
    d.on_change = lambda: calls.append(d.snapshot()["hidden"])
    d.apply("hide", "machine")
    d.apply("hide", "machine")      # already away
    d.apply("hide", "needs")        # refused
    d.apply("state")
    d.apply("move", "watching", "left", 1)   # already there
    d.apply("show", "machine")
    assert calls == [["alive", "machine"], ["alive"]]


def test_showing_the_machine_is_also_asking_for_it(home):
    d = fresh(home)
    asked, changed = [], []
    d.on_ask = asked.append
    d.on_change = lambda: changed.append(d.snapshot()["hidden"])
    assert d.apply("show", "machine") == (True, "Here is the machine.")
    assert d.apply("show", "Machine") == (True, "Here is the machine.")   # asked again, nothing to save
    d.apply("hide", "machine")
    d.apply("show", "machine")
    d.apply("show", "watching")      # no question to answer
    d.apply("hide", "machine")       # hiding is not asking
    d.apply("show", "sofa")          # refused
    assert asked == ["machine", "machine", "machine"]
    assert changed == [["alive", "machine"], ["alive"], ["alive", "machine"]]


def test_a_broken_ask_listener_never_costs_the_answer(home, capsys):
    d = fresh(home)

    def boom(_wid):
        raise RuntimeError("no")
    d.on_ask = boom
    assert d.apply("show", "machine") == (True, "Here is the machine.")
    assert "on_ask: RuntimeError: no" in capsys.readouterr().err


def test_a_broken_listener_never_costs_the_change(home, capsys):
    d = fresh(home)

    def boom():
        raise RuntimeError("no")
    d.on_change = boom
    assert d.apply("hide", "machine") == (True, "Put Machine away.")
    assert "machine" in desk.Desk().load().snapshot()["hidden"]
    assert "on_change: RuntimeError: no" in capsys.readouterr().err


def test_the_launcher_threads_and_the_shell_can_change_it_at_once(home):
    d = fresh(home)
    seen = []
    d.on_change = lambda: seen.append(1)
    errors = []
    widgets = ["now", "watching", "machine", "away", "alive", "needs"]

    def work(seed):
        rng = random.Random(seed)
        try:
            for _ in range(60):
                w = rng.choice(widgets)
                move = (w, rng.choice(desk.RAILS), rng.choice([None, 0, 1, 5]))
                do = rng.choice([("hide", w), ("show", w), ("toggle",), ("move", *move), ("state",)])
                d.apply(*do)
                d.snapshot()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and seen
    _consistent(d.snapshot())
    assert desk.Desk().load().snapshot() == d.snapshot()   # what is on disk is the final state


def _consistent(s):
    """Every widget is in exactly one rail's order, the one its rails entry names."""
    everyone = s["order"]["left"] + s["order"]["right"]
    assert sorted(everyone) == sorted(desk.WIDGETS)
    assert set(s["stripped"]) <= set(desk.WIDGETS) and s["stripped"] == [w for w in desk.WIDGETS if w in s["stripped"]]
    for r in desk.RAILS:
        assert all(s["rails"][w] == r for w in s["order"][r])
    assert "needs" not in s["hidden"]


def test_any_run_of_ops_leaves_a_consistent_desk_that_round_trips(home):
    rng = random.Random(7)
    d = fresh(home)
    for _ in range(300):
        w = rng.choice([*desk.WIDGETS, "needs you", "Now", "sofa", None])
        op = rng.choice(["hide", "show", "toggle", "fold", "unfold", "move", "move", "state", "dance"])
        d.apply(op, w, rng.choice([None, "left", "right", "up"]), rng.choice([None, -1, 0, 1, 2, 9, "x", 1.5]))
        _consistent(d.snapshot())
    assert desk.Desk().load().snapshot() == d.snapshot()


@pytest.mark.parametrize("word, widget", [
    ("now", "now"), ("Route", "now"), ("watching", "watching"), ("needs you", "needs"), ("Needs", "needs"),
    ("needs-you", "needs"), ("away", "away"), ("while you were away", "away"), ("machine", "machine"),
    ("alive", "alive"), ("Needs You.", None), ("", None), (None, None), ("desk", None), ("пришло", None),
])
def test_the_words_a_widget_answers_to(word, widget):
    assert desk.find(word) == widget


def test_the_title_the_line_uses():
    assert desk.title("needs") == "Needs you" and desk.title("machine") == "Machine"
    assert desk.title("batch") == "batch" and desk.title(None) == "a widget"


@pytest.mark.parametrize("prompt", [
    "show me the desk", "what is on my desk?", "Fold the DESK", "put widgets on the right",
    "make a widget for my streak",
    "hide machine", "show alive", "put watching on the right", "keep needs you where it is",
    "pin my batch. also bring the machine card back", "move now to the right rail",
    "Show my batch on the desk", "the widget is in the way", "hide the machine card",
    "show while you were away", "please hide machine", "put up my desk",
    "unfold the machine card", "unfold watching", "unfold needs you", "unfold the machine please",
    "fold the machine into a strip", "fold watching to a strip",
])
def test_the_words_that_ask_for_the_desk(prompt):
    assert desk.asked_for_desk(prompt)


@pytest.mark.parametrize("prompt", [
    "", None, "install docker", "tell me a joke", "what is using my memory", "install a desktop environment",
    "is the machine slow?", "the download is now at 40%", "show me my files", "put the kettle on. it is now late",
    "hide", "I am away until monday", "make me a password manager", "keep going",
    "why is the sky blue. show your work",
    # A widget's name in a sentence about something else, and "desk" as a thing in a room.
    "show me the machine's logs", "keep watching the build", "hide the machine from the guest wifi",
    "keep away from the machine", "put away the groceries", "bring me the machine specs",
    "I am at the help desk all day", "a standing desk for the office", "write to the front desk",
    "tidy my desk job list", "make a password manager",
    "unfold the map of the machine's drives", "unfold the paper and read it", "fold the laundry into the machine",
])
def test_the_words_that_do_not(prompt):
    assert not desk.asked_for_desk(prompt)


def test_the_gate_reads_one_sentence_at_a_time():
    assert not desk.asked_for_desk("Show me the logs. The machine restarted.")
    assert desk.asked_for_desk("Show me the logs; then hide the machine")
    assert not desk.asked_for_desk("hide the logs\nnow")


def test_the_state_file_is_the_one_paths_names(home):
    assert paths.desk_file() == home / "state" / "desk.toml"
    fresh(home).apply("toggle")
    assert tomllib.loads((home / "state" / "desk.toml").read_text())["folded"] is True


@pytest.mark.parametrize("args, text", [
    ({"op": "hide", "widget": "machine"}, "Putting Machine away"),
    ({"op": "show", "widget": "needs"}, "Putting Needs you on the desk"),
    ({"op": "show", "widget": "while you were away"}, "Putting Away on the desk"),
    ({"op": "move", "widget": "watching", "rail": "right", "rank": 0}, "Moving Watching to the right rail"),
    ({"op": "move", "widget": "now", "rank": 1}, "Moving Now"),
    ({"op": "fold"}, "Folding the desk"),
    ({"op": "unfold"}, "Unfolding the desk"),
    ({"op": "fold", "widget": "machine"}, "Folding Machine"),
    ({"op": "unfold", "widget": "needs"}, "Unfolding Needs you"),
    ({"op": "fold", "widget": ""}, "Folding the desk"),
    ({"op": "state"}, "Looking at the desk"),
    ({}, "Looking at the desk"),
])
def test_the_line_says_what_the_desk_tool_does_and_claims_no_change(args, text):
    from bombadil import narrate
    step = narrate.tool_step("mcp__bombadil-os__desk", args)
    assert (step.text, step.done, step.changes) == (text, None, False)


def test_a_desk_call_leaves_no_closing_sentence_and_no_undo():
    """The call may be refused or change nothing, and the desk is outside the restore points."""
    from bombadil import narrate
    n = narrate.Narrator()
    line = n.on_event({"kind": "tool", "name": "mcp__bombadil-os__desk",
                       "input": {"op": "hide", "widget": "machine"}})
    assert line["text"] == "Putting Machine away" and line["risk"] is None
    n.on_event({"kind": "tool", "name": "mcp__bombadil-os__desk", "input": {"op": "fold"}})
    assert n.summary() == "" and n.done == []


@pytest.mark.parametrize("args", [{"op": ["hide"], "widget": ["machine"]}, {"op": 5, "widget": {"a": 1}},
                                  {"op": "move", "widget": "now", "rail": ["left"]}, {"widget": None}])
def test_odd_desk_arguments_never_break_the_line(args):
    from bombadil import narrate
    assert narrate.tool_step("mcp__bombadil-os__desk", args).text

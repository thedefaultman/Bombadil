"""The scene vocabulary and its validator (src/bombadil/tip_scene.py), without Qt.

The tips table's own tests call `validate` on every row; these check the validator itself, that the
timing and vocabulary the renderer's JS keeps are the ones this module keeps, and that docs/TIP-SCENES.md
lists the whole vocabulary and its example is a valid scene.
"""

import copy
import re
import tomllib
from pathlib import Path

import pytest
import tip_fixtures as fx

from bombadil import tip_scene as ts

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "share" / "qml" / "Bombadil" / "TipScene.js").read_text()
DOC = ROOT / "docs" / "TIP-SCENES.md"


def tip(*beats, captions=("You ask", "It works", "You see it"), **more):
    return {"id": "t", "line": "A line.", "words": "", "marks": [], "captions": list(captions),
            "beats": list(beats), **more}


def problems(*beats, **kw):
    return ts.validate(tip(*beats, **kw))


# -- the fixtures are sound, so the renderer's tests stand on valid scenes --

@pytest.mark.parametrize("fixture", fx.ALL, ids=lambda t: t["id"])
def test_every_fixture_tip_is_valid(fixture):
    assert ts.validate(fixture) == []
    assert ts.duration(fixture["beats"]) <= ts.MAX_SECONDS * 1000


def test_the_fixtures_between_them_use_every_beat_kind_key_and_modifier():
    used = {k for f in fx.ALL for b in f["beats"] for k in b}
    assert set(ts.DRAWS) <= used, set(ts.DRAWS) - used
    assert set(ts.MODIFIERS) <= used, set(ts.MODIFIERS) - used
    keys = {b["key"] for f in fx.ALL for b in f["beats"] if "key" in b}
    assert keys == set(ts.KEYS)
    faces = {b["stone"] for f in fx.ALL for b in f["beats"] if "stone" in b}
    assert faces == set(ts.FACES), set(ts.FACES) - faces
    cards = {b["card"] for f in fx.ALL for b in f["beats"] if "card" in b}
    assert cards == set(ts.CARDS)
    tags = {b["tag"] for f in fx.ALL for b in f["beats"] if "tag" in b}
    assert tags >= {"template", "model", "output", "caption"}             # `typed` is the type beat's own tag
    assert {b["tone"] for f in fx.ALL for b in f["beats"] if "tone" in b} == set(ts.TONES)
    assert {b["from"] for f in fx.ALL for b in f["beats"] if "from" in b} >= {"left"}


# -- keys the vocabulary does not know --

def test_an_unknown_beat_key_is_named_with_its_beat():
    out = problems({"key": "enter"}, {"line": "x", "tag": "template", "blink": True})
    assert out == ["beat 2: unknown key 'blink'"]


def test_a_beat_that_draws_nothing_is_reported():
    out = problems({"tag": "template"})
    assert any("draws nothing" in m for m in out)
    assert any("'tag' goes with" in m for m in out)


def test_a_modifier_without_its_drawing_key_is_reported():
    out = problems({"key": "enter", "buttons": ["Undo"]}, {"type": "x", "counter": True})
    assert any(m.startswith("beat 1: 'buttons' goes with 'line'") for m in out)
    assert any(m.startswith("beat 2: 'counter' goes with 'line'") for m in out)


def test_a_beat_must_be_a_table():
    assert any("a beat is a table" in m for m in problems("enter"))
    assert ts.validate({"beats": "no", "captions": []}) == ["beats is a list of tables"]


def test_every_key_value_the_vocabulary_takes_is_checked():
    bad = [
        {"key": "ctrl"}, {"type": ""}, {"type": "x", "ghost": ""}, {"stone": "angry"},
        {"line": "x", "tag": "template", "tone": "green"}, {"line": "x", "tag": "template", "command": ""},
        {"line": "x", "tag": "template", "counter": -1}, {"line": "x", "tag": "template", "counter": "yes"},
        {"line": "x", "tag": "template", "buttons": []}, {"line": "x", "tag": "template", "buttons": ["a", "b", "c"]},
        {"window": 3, "tag": "model"}, {"window": "W", "tag": "model", "from": "top"},
        {"window": "W", "tag": "model", "doc": "yes"}, {"window": "W", "tag": "model", "lock": ""},
        {"window": "W", "tag": "model", "columns": [["Due", 3]]}, {"window": "W", "tag": "model", "rows": "x"},
        {"card": "tips", "tag": "template"}, {"card": "now", "tag": "template", "rows": [["a", "later"]]},
        {"card": "machine", "tag": "template", "rows": [["memory", 140]]},
        {"card": "watching", "tag": "template", "title": ""},
        {"picture": "P", "tag": "template"}, {"picture": "P", "tag": "template", "boxes": ["a"], "bars": [["a", 5]]},
        {"picture": "P", "tag": "template", "boxes": ["a"], "lit": 3},
        {"picture": "P", "tag": "template", "bars": [["a", 5], ["b", 90]], "lit": 1},         # lit goes with boxes
        {"picture": 3, "tag": "template", "boxes": ["a"]}, {"picture": "P", "tag": "template", "boxes": [1]},
        {"line": 5, "tag": "template"}, {"line": True, "tag": "template"}, {"line": ["a"], "tag": "template"},
        {"window": "W", "tag": "model", "rows": ["a", "b", "c", "d"]},                       # the poster holds three
        {"picture": "P", "tag": "template", "bars": [["a", 130]]},
        {"picture": "P", "tag": "template", "bars": [["a", 30, "red"]]},
        {"queue": "tidy my downloads"}, {"queue": [""]}, {"queue": [3]}, {"queue": ["a", "b", "c"]},
        {"queue": ["a"], "tag": "typed", "wait": ""}, {"queue": ["a"], "tag": "typed", "wait": 3},
        {"queue": ["a"], "tag": "typed", "wait": ["paused"]}, {"line": "x", "tag": "template", "wait": "paused"},
        {"fold": "yes"}, {"fold": True, "strips": [3], "tag": "template"}, {"hold": 0}, {"hold": "long"},
        {"hold": 100, "phase": 3}, {"hold": 100, "phase": True}, {"hold": 100, "phase": 1.0},
    ]
    for beat in bad:
        assert ts.validate(tip(beat)) != [], beat


def test_values_the_vocabulary_takes_are_not_reported():
    good = [
        {"key": "tab"}, {"type": "x", "ghost": "y"}, {"stone": "resting"},
        {"line": "x", "tag": "output", "tone": "red", "command": "ls", "counter": 5, "buttons": ["Details"]},
        {"window": "W", "tag": "model", "rows": ["a", ["b", "c"]], "columns": [["Due", "x"]], "from": "right"},
        {"window": False}, {"window": ""}, {"card": "now", "tag": "template", "rows": [["a", "done"]]},
        {"card": "machine", "tag": "template", "rows": [["a", 100]]},
        {"picture": "P", "tag": "template", "bars": [["a", 5], ["b", 90, "amber"]]},
        {"picture": "P", "tag": "template", "boxes": ["a", "b"], "lit": 1},
        {"window": "W", "tag": "model", "rows": ["a", "b", "c"]},
        {"fold": False}, {"hold": 1}, {"line": "", "stone": "rest"},
        {"queue": []}, {"queue": ["tidy my downloads"], "tag": "template", "wait": "paused"},
        {"queue": ["a", "b"], "tag": "template"},
    ]
    for beat in good:
        assert ts.validate(tip(beat)) == [], beat


# JSON and TOML hand over what was typed: a list where a word goes is a typo to report, not a crash.
JUNK = ([], ["super"], ["a", "b"], {}, {"a": 1}, 5, -1, 1.5, True, False, "x", "")


@pytest.mark.parametrize("key", sorted(ts.KNOWN))
def test_a_value_of_the_wrong_kind_is_reported_and_never_raises(key):
    for value in JUNK:
        beat = {key: value}
        ts.validate(tip(beat))
        ts.validate(tip({"line": "x", "tag": "template", key: value}))
        ts.duration([beat])
        ts.phase_starts([beat, {"hold": 5, "phase": 2}])


@pytest.mark.parametrize("beat, word", [
    ({"key": ["super"]}, "key"), ({"stone": ["working"]}, "stone"), ({"stone": {"a": 1}}, "stone"),
    ({"line": 5, "tag": "template"}, "line"), ({"line": ["a"], "tag": "template"}, "line"),
    ({"hold": 100, "phase": True}, "phase"), ({"hold": True}, "hold"),
])
def test_a_wrong_kind_of_value_names_its_key(beat, word):
    out = problems(beat)
    assert out and any(word in m for m in out), (beat, out)
    assert ts.validate(tip({"line": "", "stone": "rest"})) == []


def test_a_boolean_is_not_a_phase_and_a_line_may_be_empty():
    assert ts.phase_starts([{"hold": 300, "phase": True}, {"hold": 300}, {"hold": 300}]) == (300, 600)
    assert problems({"line": ""}) == []


def test_a_row_of_the_window_beyond_the_third_is_reported_and_a_card_may_have_more():
    out = problems({"window": "W", "tag": "model", "rows": list("abcd")})
    assert out == ["beat 1: a window shows at most 3 rows, so the card's poster holds them all (this has 4)"]
    assert problems({"card": "now", "tag": "template", "rows": [["a", "done"]] * 5}) == []


def test_a_queue_holds_at_most_two_asks_and_a_wait_needs_a_queue_and_a_tag():
    assert problems({"queue": ["a", "b", "c"]}) == [
        "beat 1: queue is the asks that wait, one to 2 of them, as typed ([] puts the chips away)"]
    assert problems({"queue": ["tidy my downloads"], "wait": "paused"}) == [
        "beat 1: has strings and no tag (one of template, typed, model, output, caption)"]
    assert problems({"line": "x", "tag": "template", "wait": "paused"}) != []            # wait goes with queue
    assert problems({"queue": []}) == []                                              # taking the chips away shows nothing


def test_lit_lights_a_box_and_goes_with_boxes():
    out = problems({"picture": "P", "tag": "template", "bars": [["a", 5], ["b", 9]], "lit": 0})
    assert any("goes with boxes" in m for m in out)
    assert problems({"picture": "P", "tag": "template", "boxes": ["a", "b"], "lit": 1}) == []


def test_a_character_past_the_basic_plane_counts_as_the_renderer_counts_it():
    # JavaScript's length is in UTF-16 units: an emoji is two, so 40 ms more than a letter takes
    assert ts.beat_ms({"type": "\U0001F600"}) == 2 * 40 + 240
    assert ts.beat_ms({"type": "ab"}) == 2 * 40 + 240
    assert ts.beat_ms({"type": "a\U0001F600b"}) == 4 * 40 + 240


# -- time --

def test_a_scene_over_ten_seconds_is_reported_and_ten_is_allowed():
    slow = [{"hold": 4000}, {"hold": 4000}, {"hold": 2001}]
    out = problems(*slow)
    assert out == ["the scene runs 10.0 s, over the 10 s limit"]
    assert problems({"hold": 5000}, {"hold": 5000}) == []


def test_strict_holds_a_scene_to_six_to_eight_seconds():
    assert ts.validate(tip({"hold": 3000}), strict=True) != []
    assert ts.validate(tip({"hold": 6000}), strict=True) == []
    assert ts.validate(tip({"hold": 8000}), strict=True) == []
    assert ts.validate(tip({"hold": 8100}), strict=True) != []
    assert ts.validate(tip({"hold": 3000})) == []                               # a draft is not held to it


def test_how_long_each_beat_takes():
    assert ts.beat_ms({"key": "enter"}) == 360 and 300 <= ts.KEY_MS <= 400
    assert ts.beat_ms({"type": "x" * 25}) == 25 * 40 + 240                      # 25 characters a second
    assert ts.beat_ms({"stone": "done"}) == 700 and ts.beat_ms({"stone": "rest"}) == 300
    assert ts.beat_ms({"line": "x", "tag": "caption"}) == 900 and ts.beat_ms({"line": ""}) == 200
    assert ts.beat_ms({"window": "W", "rows": ["a", "b"]}) == 300 + 400 + 300      # slides on in 300
    assert ts.beat_ms({"window": "W", "reload": True, "columns": [["h", "a"]]}) == 200 + 300
    assert ts.beat_ms({"window": False}) == 300
    assert ts.beat_ms({"card": "now"}) == 800
    assert ts.beat_ms({"picture": "P", "boxes": list("abcd")}) == 300 + 4 * 320 + 600
    assert ts.beat_ms({"fold": True}) == 1000
    assert ts.beat_ms({"queue": ["a"]}) == 300 and ts.beat_ms({"queue": []}) == 300 == ts.QUEUE_MS
    assert ts.beat_ms({"hold": 250}) == 250
    assert ts.beat_ms({"key": "enter", "hold": 40}) == 400                      # a hold adds to the beat
    assert ts.beat_ms({"key": "enter", "stone": "working"}) == 600             # keys in a beat run together
    assert ts.SLIDE_MS == 300


def test_beat_times_lay_the_beats_end_to_end():
    beats = [{"key": "enter"}, {"hold": 100}, {"line": "x", "tag": "caption"}]
    assert ts.beat_times(beats) == [(0, 360), (360, 100), (460, 900)]
    assert ts.duration(beats) == 1360 and ts.duration([]) == 0


def test_phases_start_at_the_marked_beats_or_at_thirds():
    beats = [{"key": "enter"}, {"hold": 400, "phase": 1}, {"hold": 400}, {"hold": 400, "phase": 2}, {"hold": 100}]
    assert ts.phase_starts(beats) == (360, 1160)
    plain = [{"hold": 300}, {"hold": 300}, {"hold": 300}]
    assert ts.phase_starts(plain) == (300, 600)
    assert ts.validate(tip(*beats)) == []
    assert any("phase marks" in m for m in problems({"hold": 400, "phase": 2}, {"hold": 400, "phase": 1}))
    assert any("phase marks" in m for m in problems({"hold": 400, "phase": 1}, {"hold": 400}))


# -- the truth rule: every string is tagged --

def test_a_string_without_a_tag_is_reported_for_every_beat_that_shows_one():
    for beat in ({"line": "Made it."}, {"window": "Tickets"}, {"card": "watching", "rows": [["a", "b"]]},
                 {"picture": "P", "boxes": ["a"]}, {"fold": True, "strips": ["1 counting"]}):
        out = problems(beat)
        assert out == ["beat 1: has strings and no tag (one of template, typed, model, output, caption)"], beat
    assert problems({"line": "Made it.", "tag": "template"}) == []


def test_an_empty_line_a_hiding_window_and_a_plain_fold_need_no_tag():
    assert problems({"line": ""}, {"window": False}, {"window": ""}, {"fold": False}, {"key": "esc"}) == []


def test_what_is_typed_is_typed_and_needs_no_tag():
    assert problems({"type": "hello"}) == []
    assert problems({"type": "hello", "tag": "typed"}) == []
    assert any("tagged typed" in m for m in problems({"type": "hello", "tag": "model"}))


def test_a_tag_outside_the_five_is_reported():
    out = problems({"line": "x", "tag": "guess"})
    assert out == ["beat 1: tag must be one of template, typed, model, output, caption (not 'guess')"]


def test_strings_lists_each_string_with_its_tag():
    rows = ts.strings(fx.CHANGE)
    assert ("typed", "In Tickets, add a due date column") in rows
    assert ("model", "Tickets") in rows and ("model", "Printer jams on floor 3") in rows
    assert ("model", "High") in rows and ("model", "Oct 3") in rows
    assert ("template", "Changing Tickets, 180 lines") in rows
    assert ("caption", "You ask") in rows
    esc = ts.strings(fx.ESC)
    assert ("template", "pacman -S docker") in esc
    assert ("template", "Undo") in ts.strings(fx.BUILD)
    assert all(tag in ts.TAGS for tag, _ in rows + esc)
    assert [t for t in ts.strings(fx.MACHINE) if t[1] == "42"] == []              # a meter is a number, not words


def test_strings_has_every_place_a_scene_shows_words():
    """Each source is asserted by itself: a string the truth test never sees is a string nobody checks."""
    def of(tip):
        return ts.strings(tip)

    assert ("model", "Locked after 5 minutes idle") in of(fx.LOCK) and ("model", "Passwords") in of(fx.LOCK)
    assert ("template", "Install rust and set it up") in of(fx.ROUTE)                  # a card's title
    assert ("template", "step 2 of 3") in of(fx.ROUTE)                                  # and its why line
    assert ("template", "The machine is fine") in of(fx.MACHINE)
    assert ("template", "How you're connected") in of(fx.PICTURE)                      # a picture's title
    assert [("template", n) for n in ("Computer", "Wi-Fi", "Router", "Internet")] == [
        t for t in of(fx.PICTURE) if t[1] in ("Computer", "Wi-Fi", "Router", "Internet")]    # its boxes, in order
    assert ("template", "Your disks") in of(fx.DISK)
    assert [("template", n) for n in ("/", "/home", "USB")] == [t for t in of(fx.DISK) if t[1] in ("/", "/home", "USB")]
    assert ("template", "1 counting") in of(fx.TIMER)                                   # a strip
    assert ("template", "Timer, 25 min") in of(fx.TIMER) and ("template", "25:00") in of(fx.TIMER)
    assert ("output", "6.17.2-arch1-1") in of(fx.BANG) and ("template", "Details") in of(fx.BANG)
    waits = of(fx.WAITS)
    assert ("typed", "tidy my downloads") in waits                              # a waiting ask is what was typed
    assert ("template", "paused") in waits                                      # and what its chip says
    assert {t for t in of(fx.ROUTE) if t[1] == "Install rustup"} == {("model", "Install rustup")}     # the plan is the model's


# -- captions --

def test_there_are_three_captions_of_at_most_three_words():
    ok = ["You ask", "It builds", "You have it"]
    assert ts.validate(tip({"hold": 1}, captions=ok)) == []
    for captions in ([], ok[:2], ok + ["Four"], None, "three"):
        out = ts.validate({"beats": [{"hold": 1}], "captions": captions})
        assert any(m.startswith("captions: there are three") for m in out), captions
    out = problems({"hold": 1}, captions=["You ask", "It builds the whole app", "Done"])
    assert out == ["captions: 'It builds the whole app' is over 3 words"]
    assert any("each is a few words" in m for m in problems({"hold": 1}, captions=["a", "", "c"]))


def test_a_row_without_beats_is_reported_unless_it_is_allowed():
    row = {"id": "x", "captions": ["a", "b", "c"], "beats": []}
    assert ts.validate(row) == ["no beats"]
    assert ts.validate(row, allow_empty=True) == []
    assert ts.validate({"captions": ["a", "b", "c"]}, allow_empty=True) == []


def test_validate_does_not_change_the_tip():
    t = copy.deepcopy(fx.BUILD)
    ts.validate(t)
    ts.strings(t)
    assert t == fx.BUILD


# -- one set of numbers: the renderer's JS keeps the same timing and vocabulary --

def test_the_renderer_keeps_the_same_timing_as_the_validator():
    js = {m.group(1): int(m.group(2)) for m in re.finditer(r"^var (\w+) = (\d+)\b", JS, re.MULTILINE)}
    mine = {k: v for k, v in vars(ts).items() if k.endswith("_MS") and isinstance(v, int)}
    assert mine, "the python timing constants"
    for name, value in mine.items():
        assert js.get(name) == value, (name, js.get(name), value)
    stones = re.search(r"^var STONE_MS = \{([^}]*)\}", JS, re.MULTILINE).group(1)
    assert {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", stones)} == ts.STONE_MS


def test_the_renderer_keeps_the_same_vocabulary_as_the_validator():
    def js_list(name):
        body = re.search(rf"^var {name} = \[(.*?)\]", JS, re.MULTILINE | re.DOTALL).group(1)
        return re.findall(r'"([^"]+)"', body)

    assert js_list("DRAWS") == list(ts.DRAWS)
    assert js_list("FACES") == list(ts.FACES)
    assert js_list("MODIFIERS") == list(ts.MODIFIERS)
    labels = re.search(r"^var KEY_LABELS = \{([^}]*)\}", JS, re.MULTILINE).group(1)
    assert dict(re.findall(r'(\w+):\s*"([^"]+)"', labels)) == ts.KEYS


# -- the documentation lists the whole vocabulary --

def test_the_documentation_names_every_beat_key_value_and_tag():
    doc = DOC.read_text()
    for word in (*ts.DRAWS, *ts.MODIFIERS, *ts.KEYS, *ts.FACES, *ts.TONES, *ts.TAGS, *ts.CARDS, *ts.FROM,
                 *ts.NOW_STATES):
        assert f"`{word}`" in doc, word
    assert f"runs {ts.TARGET_SECONDS[0]} to {ts.TARGET_SECONDS[1]} s and never over {ts.MAX_SECONDS}" in doc
    assert "MAX_WINDOW_ROWS" in doc and ts.MAX_WINDOW_ROWS == 3 and "holds three rows on the card" in doc


def test_the_documentations_example_is_a_valid_scene():
    block = re.search(r"```toml\n(.*?)```", DOC.read_text(), re.DOTALL).group(1)
    beats = tomllib.loads(block)["beats"]
    tip = {"captions": ["You ask", "It builds", "You have it"], "beats": beats}
    assert ts.validate(tip) == []
    assert set(ts.DRAWS) & {k for b in beats for k in b} >= {"key", "type", "stone", "line", "window"}

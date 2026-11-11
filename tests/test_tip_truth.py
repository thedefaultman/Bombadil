"""Every string a tip's scene shows is true: a sentence the code says, the tip's own words, a plausible reply, or
a command's output (docs/TIP-SCENES.md, "The truth rule").

A storyboard could show a line the real machine never says, and the day a real line changes it would go on
showing the old one. So the sentences of narrate, launcher, desk, jobs, vitals, rest, sysmap, agentd and the
shell's own QML are made here by calling the real functions with fixture inputs (tests/tip_truth.py), and every
`template` string of every scene is held to them, whole. A real line that changes fails here, in the scene that
shows it. What the code cannot be asked is named below, one string at a time, with why.
"""

import difflib
import re
from collections import Counter
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtQml", exc_type=ImportError)

import tip_truth as tt
from test_brain_focus import brain  # noqa: F401  (a fixture)
from test_desk_qml import app, desk  # noqa: F401  (fixtures)
from test_pill_qml import bar  # noqa: F401  (a fixture)

from bombadil import desk as desk_module
from bombadil import launcher, narrate, paths, tip_scene, tips

ROWS, PROBLEMS = tips.load_rows(paths.tips_table().read_text(encoding="utf-8"))
BY_ID = {r.id: r for r in ROWS}


def scene(row):
    return {"id": row.id, "beats": [dict(b) for b in row.beats], "captions": list(row.captions),
            "words": row.words, "marks": list(row.marks)}


# -- the template strings no code can be asked for, named one by one --
# (tip, role, string): why. A string is here only while a scene shows it and no code says it; the day the code does
# say it, the test below fails until the entry is taken out and the string is held to the code.

PIECE_2 = ("The Widgets card and its Keep choice are the keep piece's (the `keep` gate). It is not on main, so no "
           "code says its words yet. The scene is built to the brief's table and is shown nowhere until the gate opens.")
UNTIED = {
    ("keep", "window", "Widgets"): PIECE_2,
    ("keep", "value", "Keep"): PIECE_2,
    ("keep", "value", "When needed"): PIECE_2,
}


# -- every string a scene shows, with where it is --

def fields(tip):
    """(role, tag, text) of every string, in the order of tip_scene.strings but with where each one is."""
    out = []
    for b in tip["beats"]:
        tag = b.get("tag", "typed" if "type" in b else "")
        if "type" in b:
            out.append(("typed", "typed", b["type"]))
        if b.get("line"):
            out.append(("line", tag, b["line"]))
        if b.get("command"):
            out.append(("command", tag, b["command"]))
        out.extend(("button", "template", x) for x in b.get("buttons") or [])
        out.extend(("queued", "typed", x) for x in b.get("queue") or [])
        if b.get("wait"):
            out.append(("wait", tag, b["wait"]))
        if tip_scene.shows_window(b):
            out.append(("window", tag, b["window"]))
        if b.get("lock"):
            out.append(("lock", tag, b["lock"]))
        row_tag = "model" if b.get("card") == "now" else tag
        for r in b.get("rows") or []:
            out.append(("label", row_tag, r if isinstance(r, str) else str(r[0])))
            if not isinstance(r, str) and len(r) > 1 and isinstance(r[1], str) and r[1] not in tip_scene.NOW_STATES:
                out.append(("value", row_tag, r[1]))
        if b.get("title"):
            out.append(("title", "template", b["title"]))
        if b.get("why"):
            out.append(("why", "template", b["why"]))
        for c in b.get("columns") or []:
            out.extend(("cell", tag, x) for x in c)
        if b.get("picture"):
            out.append(("picture", tag, b["picture"]))
        out.extend(("box", tag, x) for x in b.get("boxes") or [])
        out.extend(("bar", tag, x[0]) for x in b.get("bars") or [])
        out.extend(("strip", tag, x) for x in b.get("strips") or [])
    return out


@pytest.fixture
def said(home, monkeypatch, brain, desk, bar):  # noqa: F811
    """Everything the code says, by where it is said: the real functions called with fixture inputs."""
    s = tt.Said()
    tt.narrated(s, home)
    tt.launched(s, home, monkeypatch)
    tt.rested(s)
    tt.vital(s)
    tt.drawn(s)
    tt.agent_says(s, monkeypatch)
    tt.brain_says(s, brain, monkeypatch)
    tt.shell_says(s)
    tt.desk_says(s, desk, s.touched)
    tt.bang_ends(s, bar, monkeypatch)
    tt.pill_says(s, bar)
    tt.buttons(s)
    return s


def test_the_walk_over_a_scene_sees_every_string_tip_scene_strings_does():
    """The roles here are the places `strings()` reads, so a new place words can stand is not missed by this test."""
    for row in ROWS:
        tip = scene(row)
        mine = Counter((tag, text) for _, tag, text in fields(tip))
        theirs = Counter(x for x in tip_scene.strings(tip) if x[0] != "caption")
        assert mine == theirs, row.id


# -- template: the code's own sentence, whole --

def close(said, role, text):
    near = difflib.get_close_matches(text, list(said.pool[role]), n=2, cutoff=0.5)
    return f" (the code says {near[0]!r} for a {role})" if near else f" (the code says nothing like it for a {role})"


def test_every_template_string_is_a_sentence_the_code_says_whole(said):
    """Placeholders are filled by the fixtures: the code is asked for the real sentence and the scene's must be it."""
    wrong = []
    for row in ROWS:
        for role, tag, text in fields(scene(row)):
            if tag != "template" or (row.id, role, text) in UNTIED:
                continue
            if not said.has(role, text):
                wrong.append(f"{row.id}: {role} {text!r}{close(said, role, text)}")
    assert wrong == [], "\n".join(wrong)


def test_a_template_string_the_code_cannot_be_asked_for_is_named_with_why_and_is_still_there(said):
    for (tid, role, text), why in UNTIED.items():
        assert any(r == role and t == text and tag == "template"
                   for r, tag, t in fields(scene(BY_ID[tid]))), f"{tid}: {text!r} is not in the scene any more"
        assert not said.has(role, text), f"{tid}: the code says {text!r} now, so hold the scene to it"
        assert len(why) > 40


def test_the_roles_keep_a_string_to_the_place_the_code_says_it():
    """A word the code says as a line must not pass as a button: each pool is of one role."""
    said = tt.Said()
    said.add("line", "Done.", "somewhere")
    assert said.has("line", "Done.") and not said.has("button", "Done.") and not said.has("strip", "Done.")


# -- a step: its words, its command and its edge are one step --

def test_a_line_with_a_command_has_the_edge_that_command_has(said):
    for row in ROWS:
        for b in scene(row)["beats"]:
            if b.get("command"):
                risk = said.risk.get((b["line"], b["command"]))
                assert risk in ("system", "irreversible"), f"{row.id}: {b['line']!r} is not a step with {b['command']!r}"
                assert b.get("tone") == {"system": "amber", "irreversible": "red"}[risk], row.id
            elif b.get("tone"):
                assert False, f"{row.id}: a tone with no command: {b['line']!r}"
            elif b.get("line"):
                # A step with an edge is never drawn without it.
                edged = [k for k, r in said.risk.items() if k[0] == b["line"] and r]
                assert edged == [] or b["line"] not in {k[0] for k in edged}, f"{row.id}: {b['line']!r} lost its edge"


# -- buttons: Undo and Details are for a turn that changed something --

def test_a_typed_command_changes_nothing_so_the_pill_gives_its_output_no_buttons(said):
    """agentd ends a `!command` with changed false (no step of it is a change), and the real pill draws no Undo and
    no Details under it: a bang scene shows exactly the buttons the pill shows."""
    assert said.bang.end["changed"] is False and said.bang.end["irreversible"] is False
    assert said.bang.buttons == [] and said.bang.line
    outputs = [b for row in ROWS for b in row.beats if b.get("tag") == "output"]
    assert outputs, "no scene shows a command's output"
    for b in outputs:
        assert list(b.get("buttons") or ()) == said.bang.buttons, b["line"]


def test_a_line_has_buttons_only_after_a_step_that_changed_something(said):
    """Undo and Details close a turn that changed something. So a scene that draws them has shown, before, a step
    the narrator counts as a change (it gives that step a `done`)."""
    seen = 0
    for row in ROWS:
        steps = []
        for b in row.beats:
            if b.get("line"):
                steps.append(b["line"])
            if b.get("buttons"):
                seen += 1
                assert any(line in said.changes for line in steps), f"{row.id}: buttons after no step that changed anything"
    assert seen >= 5


# -- consistency: the strings of one scene agree with each other --

def beats_of(tid):
    return BY_ID[tid].beats


def test_a_window_is_the_app_the_line_before_it_names():
    for row in ROWS:
        said_app = None
        for b in row.beats:
            line = b.get("line") or ""
            m = re.fullmatch(r"(?:Building|Changing) (.+?)(?:, \d+ lines)?|Opened (\w+)\.", line)
            if m:
                said_app = m.group(1) or m.group(2)
            if said_app and tip_scene.shows_window(b) and b.get("tag") == "model":
                assert b["window"] == said_app, f"{row.id}: the line says {said_app!r}, the window {b['window']!r}"
                said_app = None


def test_a_picture_is_the_one_the_line_before_it_says_it_is_drawing():
    for row in ROWS:
        line = None
        for b in row.beats:
            line = b.get("line") or line
            if not b.get("picture"):
                continue
            quoted = re.fullmatch(r"Drawing “(.+)”", line or "")
            plain = re.fullmatch(r"Drawing (.+)", line or "")
            assert (quoted and quoted.group(1) == b["picture"]) or (
                plain and plain.group(1).lower() == b["picture"].lower()), (row.id, line, b["picture"])
        shows = [b["line"] for b in row.beats if re.fullmatch(r"Showing (.+)\.", b.get("line") or "")]
        for text in shows:
            assert text[len("Showing "):-1].lower() in [b["picture"].lower() for b in row.beats if b.get("picture")]


def test_the_boxes_of_the_network_picture_are_the_nodes_the_capture_draws(said):
    boxes = next(b["boxes"] for b in BY_ID["picture"].beats if b.get("boxes"))
    real = [n["label"] for n in said.network["card"]["nodes"]]
    assert boxes == real[:len(boxes)] and len(boxes) >= 3               # this computer out to the internet, in order
    assert next(b["picture"] for b in BY_ID["picture"].beats if b.get("picture")) == said.network["card"]["title"]
    assert "lit" not in next(b for b in BY_ID["picture"].beats if b.get("boxes"))      # nothing is broken


def test_the_bars_are_the_filesystems_the_disk_picture_draws_and_the_full_one_is_amber(said):
    bars = next(b["bars"] for b in BY_ID["disk"].beats if b.get("bars"))
    assert {x[0] for x in bars} <= set(said.bars)                       # filesystems the picture really draws
    for label, pct, *tone in bars:
        real_pct, warn = said.bars[label]
        assert pct == real_pct, label
        assert (tone == ["amber"]) == warn, label
    assert next(b["picture"] for b in BY_ID["disk"].beats if b.get("picture")) == said.disks["card"]["title"]


def test_a_finished_job_reads_the_same_on_its_card_and_on_the_line(said):
    """A job that ends is "done in 3 min" on its Watching row, "finished" on the card, and "<title> is done in 3 min."
    on the line, all at the same moment: a scene never shows the line done beside a card that is still counting."""
    beats = BY_ID["tests"].beats
    ended = next(b for b in beats if "is done in" in b.get("line", ""))
    assert ended.get("card") == "watching" and ended["why"] == "finished"
    (label, value), = ended["rows"]
    assert said.has("value", value) and said.has("why", ended["why"]) and said.has("label", label)
    assert ended["line"] == f"{label} is {value}."
    for b in beats[:beats.index(ended)]:
        assert "is done" not in b.get("line", "")


def test_the_machine_card_shows_the_rows_the_vitals_make_and_how_full_each_meter_is(said):
    for tid in ("machine", "keep"):
        b = next(b for b in BY_ID[tid].beats if b.get("card") == "machine")
        for label, meter in b["rows"]:
            assert said.machine[label] == meter, (tid, label)


def test_the_stop_is_of_the_step_that_was_running_and_undo_is_of_what_was_asked(said):
    esc = BY_ID["esc"].beats
    step = next(b for b in esc if b.get("command"))["line"]
    stopped = next(b["line"] for b in esc if b.get("line", "").startswith("Stopped while"))
    assert stopped == f"Stopped while {step[0].lower() + step[1:]}."
    undo = BY_ID["undo"].beats
    asked = next(b["type"] for b in undo if "type" in b)
    assert re.search(f"before “{re.escape(asked)}”", next(b["line"] for b in undo if "Undone" in b.get("line", "")))
    assert next(b["line"] for b in undo if b.get("buttons")) == "Installed htop."


def test_a_folded_desk_shows_the_strips_of_the_cards_it_folded_and_then_unfolds():
    beats = BY_ID["desk"].beats
    folds = [b for b in beats if "fold" in b]
    assert [b["fold"] for b in folds] == [True, False]                      # "desk" folds it, "desk" again unfolds
    now = next(b for b in beats if b.get("card") == "now")
    counting = [r for b in beats if b.get("card") == "watching" for r in b["rows"]]
    shown = [now["why"].split(" · ")[0].lower()] + ([f"{len(counting)} counting"] if counting else [])
    assert folds[0]["strips"] == shown
    assert next(b for b in BY_ID["timer"].beats if b.get("fold"))["strips"] == ["1 counting"]
    # each answer is said as the desk says it, and the cards come back in the same beat as the second one
    d = desk_module.Desk()
    said_fold, said_unfold = d.apply("toggle")[1], d.apply("toggle")[1]
    assert [b["line"] for b in beats if b.get("line") in (said_fold, said_unfold)] == [said_fold, said_unfold]
    assert folds[1]["line"] == said_unfold and "line" not in folds[0]


def test_a_waiting_ask_is_one_the_person_typed_and_says_why_it_waits_in_the_chip_words_the_code_has(said):
    beats = BY_ID["pause"].beats
    queued = next(b for b in beats if b.get("queue"))
    typed = [b["type"] for b in beats if "type" in b]
    assert all(ask in typed for ask in queued["queue"])
    assert queued["wait"] == "paused" and said.has("wait", queued["wait"])
    assert all(b.get("queue") in (None, [], queued["queue"]) for b in beats)


def test_the_reason_the_scene_shows_is_what_why_answers_with(said):
    line = next(b["line"] for b in BY_ID["why"].beats if b.get("tag") == "model")
    assert narrate.reason_from(line) == line                         # one plain sentence, as the narrator keeps it
    assert said.reason == said.reason_said == line                   # and why_text() gives it back unchanged


# -- typed: the tip's own words --

def filled(words):
    return words.format(**tt.EXAMPLE)


# What else a scene types besides the tip's words: the ask a stop or an undo is of, what waits, what follows.
EXTRA = {"esc": {"install docker and set it up"}, "pause": {"tidy my downloads", "resume claude"},
         "whyhere": {"brain"}, "undo": {"install htop"}}
# Where a typed string goes: the launcher's action, or "model" for a sentence for the agent.
GOES = {"How am I connected?": "picture", "Where did my disk go?": "picture", "How's the machine?": "widget",
        "pause Claude": "rest", "resume claude": "rest", "undo": "undo", "desk": "desk", "brain": "brain",
        "where did this come from?": "whyhere", "why": "why", "!uname -r": "bang"}


def goes(text):
    if text.startswith("!"):
        return "bang"
    found = launcher.match(text, [SimpleNamespace(name="tickets", title="Tickets")], busy=True)
    return found.kind if found else "model"


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
def test_what_a_scene_types_is_the_tips_words_and_goes_where_the_tip_says(row):
    typed = [b["type"] for b in row.beats if "type" in b]
    if row.words:
        words = filled(row.words)
        if row.id == "open-app":
            ghost = next(b["ghost"] for b in row.beats if b.get("ghost"))
            assert (typed[0] + ghost).lower() == words.lower()                # "tic" and the faint "kets"
        elif words.endswith("\u2026"):
            assert typed[0].startswith(words[:-1]) and len(typed[0]) > len(words)
        else:
            assert typed[0] == words, row.id
    allowed = {typed[0]} | set(row.marks) | EXTRA.get(row.id, set())
    assert set(typed) <= allowed, f"{row.id}: types {sorted(set(typed) - allowed)} that are not the tip's"
    assert not row.marks or row.id == "esc" or row.marks[0] in typed, row.id
    for text in dict.fromkeys(typed):
        if row.id == "open-app":
            continue                                                          # "tic" is the start of a name
        want = GOES.get(text, "model")
        assert goes(text) == want, f"{row.id}: {text!r} goes to {goes(text)!r}, the scene says {want!r}"
    if row.id == "esc":
        assert any(b.get("key") == "esc" for b in row.beats)


def test_the_word_a_scene_completes_is_the_word_the_pill_completes(bar):  # noqa: F811
    apps = [SimpleNamespace(name="tickets", title="Tickets", description="")]
    try:
        complete = tt.entries_complete(bar, apps)
    except Exception as e:  # noqa: BLE001 - the entries need the real app records
        pytest.fail(f"entries: {e}")
    b = next(b for b in BY_ID["open-app"].beats if b.get("ghost"))
    assert complete(b["type"]) == b["ghost"]


# -- output: a command's own output, in mono --

def test_a_commands_output_is_what_the_command_prints_in_the_shape_a_bang_shows(said):
    import subprocess

    out = [(b["line"], b) for row in ROWS for b in row.beats if b.get("tag") == "output"]
    assert [line for line, _ in out] == ["6.17.2-arch1-1"]
    line = out[0][0]
    real = subprocess.run(["uname", "-r"], capture_output=True, text=True, check=True).stdout.strip()
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?-\S+", line) and re.fullmatch(r"\d+\.\d+(\.\d+)?\S*", real)
    events, result = said.shell
    assert events == [{"kind": "output", "text": line}] and result["text"] == line        # the result is the output
    command = next(b["type"] for row in ROWS for b in row.beats if b.get("tag") == "output" or "type" in b
                   and b["type"].startswith("!"))
    assert command == "!uname -r" and goes(command) == "bang"


# -- model: a reply in the agent's register --

AGENT_NO = [(re.compile(r"[*_`#]|^\s*[-•] |^\d+\. "), "markdown"), (re.compile(r"\n"), "a second line"),
            (re.compile("[\U0001F300-\U0001FAFF☀-➿]"), "an emoji")]
CARD_LINE = 36        # about what the card's line holds at 12 px, so four lines are 144 characters
VOICE_NO = [(re.compile(r"!"), "an exclamation mark"), (re.compile("—"), "an em-dash"),
            (re.compile(r"\b(will|tomorrow|later)\b", re.IGNORECASE), "will, tomorrow or later")]


def test_a_model_string_is_at_most_four_plain_lines_in_the_agents_register():
    for row in ROWS:
        for role, tag, text in fields(scene(row)):
            if tag != "model":
                continue
            assert len(text) <= 4 * CARD_LINE, f"{row.id}: {text!r} is more than four lines on the card"
            for rule, name in AGENT_NO:
                assert not rule.search(text), f"{row.id}: {text!r} has {name}"
            if role == "line":
                assert text[0].isupper() and text[-1] in ".?", f"{row.id}: {text!r} is not a sentence"


def test_no_string_of_a_scene_breaks_the_voice_rules():
    """The tips' own rules (test_tips.py) over what the scenes say too. What is typed is the person's own."""
    for row in ROWS:
        for role, tag, text in fields(scene(row)):
            if tag in ("typed", "output"):
                continue
            for rule, name in VOICE_NO:
                assert not rule.search(text), f"{row.id}: {text!r} has {name}"


def test_every_string_has_one_of_the_five_tags_and_a_beat_with_strings_has_its_tag():
    for row in ROWS:
        for tag, text in tip_scene.strings(scene(row)):
            assert tag in tip_scene.TAGS, (row.id, text)
        assert tip_scene.validate(scene(row), strict=True) == [], row.id

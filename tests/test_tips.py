"""The tips table and tips.py: the rules every tip keeps, what a machine can back, the state file, the card and
what counts a tip as tried. Nothing here talks to agentd, a model or a socket: the machine is a `Facts` value or
a folder in tmp_path, and the clock is a date the test hands in. test_agentd_tips.py has agentd's side."""

import json
import os
import re
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from bombadil import apps, jobs, launcher, paths, tips
from bombadil.tips import (
    BANG,
    Facts,
    Tips,
    backed,
    fill,
    job_keys,
    listed,
    load_rows,
    local_keys,
    newest_app,
    newest_project,
    parse_state,
    read_facts,
    tool_keys,
)

TABLE = paths.tips_table()
ROWS, PROBLEMS = load_rows(TABLE.read_text(encoding="utf-8"))
BY_ID = {r.id: r for r in ROWS}
# The brief's seed (docs/design/tips-and-desk-defaults-brief.md, "The seed"), in its order. The rows that wait for
# their pieces (a coding session by name, mail, noticed) are not here: they ship with those PRs.
SEED = ["make-an-app", "picture", "esc", "timer", "machine", "bang",
        "change-app", "open-app", "passwords", "whyhere", "draw",
        "toolchain", "tests", "code-picture",
        "disk", "pause", "desk", "keep", "why", "undo"]
APP_ONLY = {"draw", "tests", "code-picture"}
MARKED = {"esc": ["Esc"], "whyhere": ["where did this come from?"], "why": ["why"], "undo": ["undo"]}
KEYS = {"Esc", "Tab", "Enter", "Super"}
# Gates whose code is not on main: a tip that needs one is shown nowhere yet, so what it says is not checked
# against the launcher (piece 2 makes "widgets" a launcher word).
NOT_ON_MAIN = {"sessions", "mail", "noticed", "keep"}

SAMPLE = Facts(ai="Claude", installed=True, app="Customer Dashboard", project="myapp", desk_seen=True, keep=True)
THEIRS = SimpleNamespace(name="customer-dashboard", title="Customer Dashboard")


# -- the rules, over the real table --

def test_the_table_loads_clean_and_holds_the_seed():
    assert PROBLEMS == []
    assert [r.id for r in tips.ordered(ROWS)] == SEED
    assert {r.id: r.section for r in ROWS} == {
        **dict.fromkeys(SEED[:6], "start"), **dict.fromkeys(SEED[6:11], "work"),
        **dict.fromkeys(SEED[11:14], "code"), **dict.fromkeys(SEED[14:], "machine")}


def test_ids_are_unique_kebab_case_and_orders_are_unique_in_a_section():
    ids = [r.id for r in ROWS]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", i) for i in ids)
    assert all(r.section in tips.SECTIONS for r in ROWS)
    places = [(r.section, r.order) for r in ROWS]
    assert len(places) == len(set(places)) and all(o >= 1 for _, o in places)


def test_needs_are_gates_and_nothing_waiting_for_its_piece_ships():
    assert all(set(r.needs) <= set(tips.GATES) for r in ROWS)
    assert BY_ID["keep"].needs == ("keep",) and BY_ID["desk"].needs == ("desk-seen",)
    # The coding sessions' tip, mail's and noticed's come with their PRs, gated until their code is on main.
    assert not any(set(r.needs) & {"sessions", "mail", "noticed"} for r in ROWS)
    assert all(r.beats == () for r in ROWS)      # the scenes are the next piece's


def test_marks_and_the_cards_the_brief_decided():
    assert {r.id: list(r.marks) for r in ROWS if r.marks} == MARKED
    assert all(r.words == "" for r in ROWS if r.marks)                 # a mark is not a button
    assert {r.id for r in ROWS if not r.card} == APP_ONLY
    assert all(r.words or r.marks for r in ROWS)                       # every tip shows a button or a mark


def test_every_row_is_a_sound_scene_so_far_as_it_has_one():
    """tip_scene.validate (the scene piece's vocabulary and timing, docs/TIP-SCENES.md) over every row: a row
    whose beats are not written yet is allowed, one whose beats are written has to be right, so a bad scene in
    the table fails here and not on a person's screen."""
    from bombadil import tip_scene

    for r in ROWS:
        tip = {"beats": [dict(b) for b in r.beats], "captions": list(r.captions)}
        assert tip_scene.validate(tip, allow_empty=True) == [], r.id


VOICE_NO = [(re.compile(r"!"), "an exclamation mark"), (re.compile("—"), "an em-dash"),
            (re.compile(r"\b(will|tomorrow|later)\b", re.IGNORECASE), "will, tomorrow or later"),
            (re.compile(r"[\n\r]"), "a second line")]


def _visible(r):
    """Every string a person reads on a tip (its words are what they would type, so they are their own)."""
    return [r.line, *r.captions, *r.marks]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
def test_a_tip_follows_the_voice_rules(row):
    for text in _visible(row):
        for rule, name in VOICE_NO:
            assert not rule.search(text), f"{row.id}: {text!r} has {name}"
    assert row.line == row.line.strip() and row.line[-1] in ".?"
    assert len(row.captions) == 3
    assert all(1 <= len(c.split()) <= 3 for c in row.captions), row.captions


def test_captions_are_the_default_three_unless_the_brief_words_a_tips_own():
    """The brief words the captions of one tip, the first; every other row has the default three, as a proposal
    for the voice thread."""
    assert BY_ID["make-an-app"].captions == ("You ask", "It builds", "You have it")
    assert {r.captions for r in ROWS if r.id != "make-an-app"} == {("You ask", "It works", "You have it")}


def _qt():
    """Qt's gui module and an application, or (None, None) where PySide6 is not installed."""
    try:
        from PySide6 import QtGui
    except ImportError:
        return None, None
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    return QtGui, app


@pytest.fixture(scope="module")
def ink():
    """Width in pixels of text in Inter at a size and weight, or None where Inter (or PySide6) is not installed:
    the test then holds the characters to what fits, so a machine without the font still checks the rule."""
    QtGui, _ = _qt()
    if QtGui is None or "Inter" not in QtGui.QFontDatabase.families():
        yield None
        return

    def width(text, px, weight="normal"):
        font = QtGui.QFont("Inter")
        font.setPixelSize(px)
        font.setWeight(QtGui.QFont.Weight.Medium if weight == "medium" else QtGui.QFont.Weight.Normal)
        return QtGui.QFontMetricsF(font).horizontalAdvance(text)
    yield width


CARD_ROW = 272            # the card is 300 wide with 14 px each side
LINE_CHARS = 45           # what fits 272 px at 13 px when Inter is not here to measure (the longest line is 43)
BUTTON_CHARS = 27         # the same for a card's button or marks beside "All tips ›" (the longest is 27)
BUTTON_INSET = 2 * 12 + 2  # the button's padding and its 1 px borders
ROW_GAP = 10
ALL_TIPS = "All tips ›"


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
def test_the_line_fits_the_card_and_so_does_the_button_with_all_tips(row, ink):
    tip = tips.resolve(row, SAMPLE)
    assert tip is not None
    shown = tip.words or " ".join(tip.marks)
    if ink is None:
        assert len(tip.line) <= LINE_CHARS
        assert not row.card or len(shown) <= BUTTON_CHARS, f"{row.id}: {shown!r} is too long for the card's row"
        return
    assert ink(tip.line, 13) <= CARD_ROW, f"{row.id}: {tip.line!r}"
    if row.card:
        # The button (or the marks, which take its place) and "All tips ›" share the card's bottom row.
        used = ink(shown, 13, "medium") + BUTTON_INSET + ROW_GAP + ink(ALL_TIPS, 12)
        assert used <= CARD_ROW, f"{row.id}: {shown!r} and {ALL_TIPS!r} take {used:.1f} px of {CARD_ROW}"


def test_the_character_bounds_that_stand_in_for_inter_hold_for_the_shipped_tips():
    """Where Inter is not installed the width test falls back on these, so they must be true of the table."""
    for row in ROWS:
        tip = tips.resolve(row, SAMPLE)
        assert len(tip.line) <= LINE_CHARS, row.id
        assert not row.card or len(tip.words or " ".join(tip.marks)) <= BUTTON_CHARS, row.id


def test_a_tip_that_does_not_fit_the_card_is_one_the_app_shows(ink):
    """The reverse of the rule above: the three that are app-only really would not fit the row."""
    if ink is None:
        pytest.skip("Inter is not installed here, so widths cannot be measured")
    for tid in APP_ONLY:
        tip = tips.resolve(BY_ID[tid], SAMPLE)
        assert ink(tip.words, 13, "medium") + BUTTON_INSET + ROW_GAP + ink(ALL_TIPS, 12) > CARD_ROW


def _what(words):
    """Where words typed in the pill go: a "!" command, a launcher word (its action), or the model."""
    if words.startswith("!"):
        return "bang", None
    action = launcher.match(words, [THEIRS], busy=True)
    return ("local", action) if action else ("model", None)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.id)
def test_a_tips_words_reach_something_and_the_tip_is_counted_when_they_do(row):
    tip = tips.resolve(row, SAMPLE)
    assert row.tried_when, "nothing would ever count this tip as tried"
    for placeholder in re.findall(r"\{(\w+)\}", row.line + row.words):
        assert placeholder in ("ai", "app", "project")
        assert placeholder == "ai" or placeholder in row.needs       # an app, a project: gated by what it names
    spoken = [tip.words] if tip.words else [m for m in tip.marks if m not in KEYS]
    for words in spoken:
        where, action = _what(words)
        if where == "model":
            # A model ask: the machine needs its AI, and some tool, job or the words themselves count it.
            if NOT_ON_MAIN & set(row.needs):
                continue
            assert "model" in row.needs, f"{row.id}: {words!r} goes to the model"
            assert any(e == "words" or e.startswith(("tool:", "job:")) for e in row.tried_when)
        elif where == "bang":
            assert "model" not in row.needs and ("words" in row.tried_when or "bang" in row.tried_when)
        else:
            # A launcher word: no model, and the action it makes is the evidence that counts the tip.
            if tip.words:
                assert "model" not in row.needs, f"{row.id}: {words!r} is a launcher word, it needs no AI"
            seen = set(local_keys(action.kind, action.target, action.verb))
            assert seen & set(row.tried_when), f"{row.id}: {action} leaves {seen}, not {row.tried_when}"


def test_the_esc_tip_is_counted_by_a_turn_that_is_stopped():
    assert BY_ID["esc"].tried_when == ("local:stop",) and BY_ID["esc"].marks == ("Esc",)


def test_every_local_evidence_names_an_action_the_launcher_has():
    kinds = {k for table in (launcher.CORE_COMMANDS, launcher.BRAIN_COMMANDS, launcher.UTILITY_COMMANDS) for k in table}
    kinds |= {"picture", "widget", "app", "panel", "mail", "rest", "provider", "signin", "model", "why"}
    for row in ROWS:
        for e in row.tried_when:
            if e.startswith("local:"):
                assert e.split(":")[1] in kinds, f"{row.id}: {e}"
    assert tips.JOB_KINDS == jobs.KINDS


# -- reading a table --

def _row(**over):
    base = {"id": "a-tip", "section": "start", "order": 1, "line": "Say it.", "words": "Do it", "marks": [],
            "needs": [], "card": True, "tried_when": ["words"], "captions": ["One", "Two", "Three"], "beats": []}
    return {**base, **over}


def _toml(*rows):
    out = []
    for r in rows:
        out.append("[[tip]]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in r.items()))
    return "\n\n".join(out) + "\n"


@pytest.mark.parametrize("over, why", [
    ({"id": "Bad_Id"}, "id"), ({"id": ""}, "id"), ({"section": "play"}, "section"), ({"order": "1"}, "order"),
    ({"order": True}, "order"), ({"line": "  "}, "line"), ({"needs": ["wishes"]}, "gate"),
    ({"needs": "model"}, "gate"), ({"tried_when": ["clicks"]}, "evidence"), ({"tried_when": ["local:"]}, "evidence"),
    ({"captions": ["One", "Two"]}, "captions"), ({"card": "yes"}, "card"), ({"beats": ["x"]}, "beats"),
    ({"line": "Hello {name}."}, "placeholder"), ({"words": "pause {AI}"}, "placeholder"),
    ({"need": ["model"]}, "unknown field need"), ({"beat": []}, "unknown field beat")])
def test_a_row_that_is_not_one_is_left_out_with_why(over, why):
    rows, problems = load_rows(_toml(_row(), _row(id="fine", order=2), _row(**{"id": "broken", "order": 3, **over})))
    assert [r.id for r in rows] == ["a-tip", "fine"]
    assert why in " ".join(problems)


def _with_beats(beat):
    """A table of one good row and one whose beats are written as TOML (json.dumps cannot write a date)."""
    own = "\n".join(f"{k} = {json.dumps(v)}" for k, v in _row(id="beaten", order=2).items() if k != "beats")
    return _toml(_row()) + f"\n[[tip]]\n{own}\nbeats = [ {beat} ]\n"


@pytest.mark.parametrize("beat", ["{ hold = 1979-05-27 }", "{ hold = 07:32:00 }", "{ hold = nan }", "{ hold = inf }"])
def test_a_row_whose_beats_json_cannot_carry_is_left_out_and_cannot_reach_a_client(beat):
    """TOML holds dates, times and nan, which json.dumps refuses; a row sent to a client with one in it would fail
    every connection."""
    rows, problems = load_rows(_with_beats(beat))
    assert [r.id for r in rows] == ["a-tip"] and "JSON cannot carry" in " ".join(problems)


def test_a_row_whose_beats_are_plain_values_is_kept_and_can_be_sent():
    rows, problems = load_rows(_with_beats('{ key = "super" }, { type = "Hello", hold = 400 }'))
    assert problems == [] and [r.id for r in rows] == ["a-tip", "beaten"]
    assert json.loads(json.dumps(rows[1].as_dict()))["beats"] == [{"key": "super"}, {"type": "Hello", "hold": 400}]


def test_a_second_row_with_an_id_is_left_out_and_a_shared_place_is_reported():
    rows, problems = load_rows(_toml(_row(), _row(line="Again."), _row(id="other")))
    assert [r.id for r in rows] == ["a-tip", "other"] and rows[0].line == "Say it."
    assert any("used twice" in p for p in problems) and any("two tips have this place" in p for p in problems)


def test_a_table_that_cannot_be_read_gives_no_tips_and_no_crash(tmp_path, capsys):
    assert load_rows("this is [not toml")[0] == [] and load_rows("a = 1")[0] == []
    assert tips.read_table(tmp_path / "missing.toml") == []
    assert "cannot be read" in capsys.readouterr().err
    (tmp_path / "t.toml").write_bytes(b"\xff\xfe\x00")
    assert tips.read_table(tmp_path / "t.toml") == []


def test_the_shipped_table_is_read_in_the_fixed_order():
    assert [r.id for r in tips.read_table()] == SEED


# -- what a machine can back --

def _ids(facts):
    return [t.id for t in listed(ROWS, facts)]


def test_a_machine_with_nothing_backs_only_the_tips_that_need_nothing():
    assert _ids(Facts()) == ["picture", "machine", "bang", "whyhere", "disk"]


def test_each_gate_brings_its_tips_and_only_those():
    base = set(_ids(Facts()))
    model = set(_ids(Facts(ai="Claude"))) - base
    assert model == {"make-an-app", "esc", "timer", "passwords", "draw", "toolchain", "pause", "why"}
    assert set(_ids(Facts(installed=True))) - base == {"undo"}
    assert set(_ids(Facts(app="Tickets"))) - base == {"open-app"}                       # change-app also needs the AI
    assert set(_ids(Facts(ai="Claude", app="Tickets"))) - base - model == {"open-app", "change-app"}
    assert set(_ids(Facts(project="myapp"))) - base == set()
    assert set(_ids(Facts(ai="Claude", project="myapp"))) - base - model == {"tests", "code-picture"}
    assert set(_ids(Facts(desk_seen=True))) - base == {"desk"}
    assert set(_ids(Facts(keep=True))) - base == {"keep"}
    # Nothing waits on these yet: a machine that has them backs nothing more.
    assert set(_ids(Facts(sessions=True, mail=True, noticed=True))) == base


def test_the_full_machine_backs_all_twenty_in_order():
    assert _ids(Facts(ai="Claude", installed=True, app="Tickets", project="myapp", desk_seen=True, keep=True)) == SEED


def test_placeholders_are_filled_from_the_machine():
    facts = Facts(ai="Claude", app="Tickets", project="myapp")
    got = {t.id: t for t in listed(ROWS, facts)}
    assert got["change-app"].words == "In Tickets, add…"
    assert got["open-app"].words == "Tickets"
    assert got["pause"].line == "Out of Claude? Your apps still work." and got["pause"].words == "pause Claude"
    assert got["tests"].words == "Run the tests in ~/Projects/myapp in the background"
    assert got["code-picture"].words == "Draw how the modules in ~/Projects/myapp depend on each other"
    codex = {t.id: t for t in listed(ROWS, replace(facts, ai="Codex"))}["pause"]
    assert codex.line == "Out of Codex? Your apps still work." and codex.words == "pause Codex"
    assert fill("{ai} {ai}", facts) == "Claude Claude" and fill("no marks", Facts()) == "no marks"
    assert fill("In {app}", Facts()) is None and fill("{project}", Facts(app="x")) is None


def test_a_tip_whose_placeholder_cannot_be_filled_is_not_backed_whatever_its_gates():
    row = load_rows(_toml(_row(line="Out of {ai}?", words="pause {ai}", needs=[])))[0][0]
    assert not backed(row, Facts()) and backed(row, Facts(ai="Claude"))
    assert not backed(load_rows(_toml(_row(words="open {app}")))[0][0], Facts(project="p"))
    assert tips.resolve(row, Facts()) is None


def test_the_filled_row_is_what_the_card_and_the_app_are_sent():
    t = tips.resolve(BY_ID["open-app"], Facts(app="Tickets"))
    assert t.words == "Tickets" and t.line == "Type an app's name to open it." and BY_ID["open-app"].words == "{app}"
    assert t.as_dict() == {"id": "open-app", "section": "work", "order": 2, "line": "Type an app's name to open it.",
                           "words": "Tickets", "marks": [], "needs": ["app"], "card": True,
                           "tried_when": ["local:app"], "captions": ["You ask", "It works", "You have it"],
                           "beats": []}


# -- reading the machine --

def _app(root, name, title=None, age=0, qml=True):
    d = root / name
    d.mkdir(parents=True)
    if title is not None:
        (d / "app.toml").write_text(f"title = {json.dumps(title)}\n")
    if qml:
        (d / "main.qml").write_text("import QtQuick\nItem {}\n")
        os.utime(d / "main.qml", (1_790_000_000 - age, 1_790_000_000 - age))
    return d


def test_the_newest_app_is_the_one_written_last_and_is_named_by_its_title(tmp_path):
    _app(tmp_path, "old", "Old One", age=500)
    _app(tmp_path, "tickets", "Tickets", age=10)
    _app(tmp_path, "no-main", "Not an app", qml=False)
    (tmp_path / "file.txt").write_text("x")
    assert newest_app(tmp_path) == "Tickets"
    assert newest_app(tmp_path / "missing") == "" and newest_app(tmp_path / "file.txt") == ""
    _app(tmp_path, "habits", None, age=1)
    assert newest_app(tmp_path) == "habits"                                 # no title: the folder's name


def test_a_name_that_could_not_go_into_a_button_safely_is_never_used(tmp_path):
    """Words from the machine reach the pill: a title that is a shell command, a path or too long must not."""
    _app(tmp_path, "good", "Good One", age=500)
    for n, title in enumerate(("!rm -rf ~", "~/secrets", "A title that is much too long for a button", "Wow!",
                               "$(reboot)", "a;b", "-n", "")):
        _app(tmp_path, f"a-folder-name-too-long-{n}", title, age=n)
    assert newest_app(tmp_path) == "Good One"
    _app(tmp_path, "tickets", "!reboot", age=0)
    assert newest_app(tmp_path) == "tickets"                                # the folder's name stands in for it
    assert tips.fit("Café Ünï", tips._TITLE, 18) == "Café Ünï" and tips.fit("Two   words", tips._TITLE, 18) == "Two words"


@pytest.mark.parametrize("title", ["Lock", "Desk", "History", "Mail", "Restart", "Shutdown", "Stop", "Undo", "Brain"])
def test_an_app_whose_title_the_launcher_keeps_for_a_command_is_not_the_one_a_tip_says_to_type(tmp_path, title):
    """"Type an app's name to open it" must be true: typing "Lock" locks the screen, whatever app is called that."""
    _app(tmp_path, "tickets", "Tickets", age=500)
    _app(tmp_path, apps.slug(title), title, age=1)                             # the newer one, in the folder it gets
    assert launcher.match(title, [SimpleNamespace(name=apps.slug(title), title=title)]).kind != "app"
    assert newest_app(tmp_path) == "Tickets"
    only = tmp_path / "only"
    _app(only, apps.slug(title), title)
    assert newest_app(only) == ""                                              # no app a tip can name: no tip
    assert not backed(BY_ID["open-app"], Facts(ai="Claude", app=newest_app(only)))


def test_an_app_that_a_command_word_hides_by_title_is_still_named_by_its_folder_when_that_opens_it(tmp_path):
    _app(tmp_path, "my-lock", "Lock", age=1)
    assert newest_app(tmp_path) == "my-lock"
    assert launcher.match("my-lock", [apps.App("my-lock", tmp_path, "Lock")]).kind == "app"


def test_the_newest_project_is_the_newest_folder_that_is_a_plain_name(tmp_path):
    for n, name in enumerate(("alpha", "beta", ".hidden", "has space", "gamma")):
        d = tmp_path / name
        d.mkdir()
        os.utime(d, (1_790_000_000 + n, 1_790_000_000 + n))
    (tmp_path / "notes.txt").write_text("x")
    os.utime(tmp_path / "notes.txt", (1_790_001_000, 1_790_001_000))
    assert newest_project(tmp_path) == "gamma"
    assert newest_project(tmp_path / "missing") == ""
    os.utime(tmp_path / "beta", (1_790_000_100, 1_790_000_100))
    assert newest_project(tmp_path) == "beta"


def test_read_facts_puts_the_machine_together(tmp_path):
    _app(tmp_path / "Apps", "tickets", "Tickets")
    (tmp_path / "Projects" / "myapp").mkdir(parents=True)
    facts = read_facts(ai="Claude", installed=True, desk_seen=True, apps_dir=tmp_path / "Apps",
                       projects_dir=tmp_path / "Projects", has_module=lambda name: name == "bombadil.dev")
    assert facts == Facts(ai="Claude", installed=True, app="Tickets", project="myapp", sessions=True,
                          desk_seen=True)
    bare = read_facts(apps_dir=tmp_path / "x", projects_dir=tmp_path / "y", has_module=lambda n: False)
    assert bare == Facts() and not bare.model
    # Where the coding-sessions code is not on this machine, there is no module to find.
    assert read_facts(apps_dir=tmp_path, projects_dir=tmp_path, has_module=lambda n: False).sessions is False
    # and where it is (it is, since the coding sessions merged), the fact is true with no argument at all.
    assert read_facts(apps_dir=tmp_path, projects_dir=tmp_path).sessions is True


def test_the_apps_a_machine_has_are_read_the_way_the_launcher_reads_them(home):
    """{app} is what the launcher answers to: typing it opens the app."""
    apps.create("Customer Dashboard", "import QtQuick\nItem {}\n")
    title = newest_app(paths.apps_dir())
    assert title == "Customer Dashboard"
    action = launcher.match(title)
    assert action.kind == "app" and action.target == "customer-dashboard" and action.verb == "open"


# -- the state file --

NOW = date(2026, 11, 9)


def machine(tmp_path, today=NOW, **facts):
    """Tips on the real table in a state file of its own, with a clock the test moves."""
    clock = {"day": today}
    t = Tips(rows=ROWS, path=tmp_path / "tips.json", clock=lambda: clock["day"])
    t.facts = Facts(**({"ai": "Claude"} | facts))
    t.clock = clock
    return t


def test_a_new_person_has_an_empty_state_and_nothing_is_written_until_something_happens(tmp_path):
    t = machine(tmp_path)
    assert t.state == tips.State() and not (tmp_path / "tips.json").exists()
    t.message(), t.listed(), t.turn(1), t.advance()
    assert not (tmp_path / "tips.json").exists()


def test_the_state_round_trips_through_its_file(tmp_path):
    t = machine(tmp_path)
    assert t.ready() and t.use() and t.tried(["picture", "bang"]) == ["picture", "bang"] and t.card_seen()
    t.facts = Facts(ai="Claude")
    assert t.open("picture", True) == "picture"
    data = json.loads((tmp_path / "tips.json").read_text())
    assert data == {"first_ready": "2026-11-09", "days": ["2026-11-09"], "tried": ["picture", "bang"],
                    "known": SEED, "open_on": "picture", "desk_seen": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tips.json"]          # no temporary file left behind
    again = machine(tmp_path)
    assert again.state == t.state


@pytest.mark.parametrize("text", ["", "   ", "not json", "[1, 2]", "null", "7", '{"first_ready": ', "\x00\x01",
                                  '{"days": 3, "tried": "picture"}'])
def test_a_missing_empty_or_damaged_file_starts_a_new_person_and_never_raises(tmp_path, text):
    (tmp_path / "tips.json").write_text(text)
    t = machine(tmp_path)
    assert t.state == tips.State()
    assert t.ready() and t.message()["present"] is True


def test_a_damaged_part_falls_back_alone_and_unknown_things_are_dropped(tmp_path):
    (tmp_path / "tips.json").write_text(json.dumps({
        "first_ready": "yesterday", "days": ["2026-11-01", "2026-13-45", 7, "2026-11-01", "20261102", "2026-10-30"],
        "tried": ["picture", "Not An Id", 4, "picture", "gone-tip", "bang"], "known": "all", "open_on": ["x"],
        "desk_seen": "yes"}))
    s = machine(tmp_path).state
    assert s.first_ready == "" and s.days == ["2026-10-30", "2026-11-01"]
    assert s.tried == ["picture", "gone-tip", "bang"]       # a retired id stays: it can never look new again
    assert s.known == [] and s.open_on == "" and s.desk_seen is False
    (tmp_path / "tips.json").write_text(json.dumps({"first_ready": "2026-11-01", "tried": ["picture"]}))
    assert machine(tmp_path).state.known == SEED            # begun, but knows nothing of "known": none are new


def test_a_file_begun_when_the_table_could_not_be_read_does_not_hide_every_tip_after(tmp_path):
    path = tmp_path / "tips.json"
    broken = Tips(rows=[], path=path, clock=lambda: NOW)           # the table was unreadable on that day
    assert broken.ready() and json.loads(path.read_text())["known"] == []
    again = machine(tmp_path)
    assert again.state.known == SEED and again.message()["card"]["id"] == "make-an-app"
    path.write_text(json.dumps({"first_ready": "2026-11-09", "known": [None, 7, "Bad Id"]}))      # or by hand
    assert machine(tmp_path).state.known == SEED
    path.write_text(json.dumps({"first_ready": "2026-11-09", "known": ["bang"]}))     # a real list is believed
    assert machine(tmp_path).state.known == ["bang"]
    path.write_text(json.dumps({"known": []}))                       # before setup was ever ready: nothing yet
    assert machine(tmp_path).state.known == []


def test_a_save_that_fails_halfway_keeps_the_file_it_had_and_leaves_nothing_behind(tmp_path, monkeypatch, capsys):
    t = machine(tmp_path)
    assert t.ready()
    before = (tmp_path / "tips.json").read_text()
    renamed = []

    def refused(src, dst):
        renamed.append((os.path.basename(src), os.path.exists(src)))     # the new text is written aside first
        raise OSError("disk full")
    monkeypatch.setattr(tips.os, "replace", refused)
    assert t.tried(["bang"]) == ["bang"] and t.state.tried == ["bang"]      # kept in memory until agentd stops
    assert renamed == [("tips.json.tmp", True)]
    assert (tmp_path / "tips.json").read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tips.json"]
    assert "could not save" in capsys.readouterr().err


def test_a_file_that_cannot_be_read_or_written_never_costs_agentd(tmp_path, capsys):
    (tmp_path / "tips.json").mkdir()                        # not a file at all
    t = machine(tmp_path)
    assert t.state == tips.State() and "cannot be read" in capsys.readouterr().err
    assert t.ready() and t.state.first_ready == "2026-11-09"            # kept in memory, said on stderr
    assert "could not save" in capsys.readouterr().err
    blocked = machine(tmp_path / "blocked" / "deeper")
    (tmp_path / "blocked").write_text("a file where a folder should be")
    assert blocked.use() and blocked.state.days == ["2026-11-09"]


# -- the card, the first week and the why line --

def test_nothing_is_said_before_setup_is_ready_or_while_signed_out(tmp_path):
    t = machine(tmp_path)
    assert t.message() == {"type": "tips", "present": False, "card": None}
    t.ready()
    assert t.message()["present"] is True
    t.facts = Facts(ai="")                                  # signed out again, or no AI chosen
    assert t.message() == {"type": "tips", "present": False, "card": None}
    t.facts = Facts(ai="Codex")
    assert t.message()["present"] is True


def test_the_card_is_the_first_start_here_tip_with_its_why_line(tmp_path):
    t = machine(tmp_path)
    t.ready()
    assert t.message() == {"type": "tips", "present": True, "card": {
        "id": "make-an-app", "line": "Say what you need. It gets made.", "words": "Make me a ticketing app",
        "marks": [], "why": "Start here · 1 of 6", "index": 1, "count": 6, "tip": BY_ID["make-an-app"].as_dict()}}
    assert t.message()["card"]["tip"]["captions"] == ["You ask", "It builds", "You have it"]


def test_the_card_stays_on_its_tip_until_it_is_turned_or_a_turn_has_ended(tmp_path):
    t = machine(tmp_path)
    t.ready()
    first = t.message()
    assert t.tried(["make-an-app", "picture"]) == ["make-an-app", "picture"]
    assert t.message() == first                              # trying a tip does not move the card by itself
    assert t.advance() is True
    card = t.message()["card"]
    assert card["id"] == "esc" and card["why"] == "Start here · 3 of 6" and card["words"] == ""
    assert card["marks"] == ["Esc"] and card["index"] == 3 and card["count"] == 6
    assert t.advance() is False                              # already on the first not tried


def test_the_arrows_turn_through_start_here_and_wrap(tmp_path):
    t = machine(tmp_path)
    t.ready()
    ids = []
    for _ in range(7):
        ids.append(t.message()["card"]["id"])
        assert t.turn(1) is True
    assert ids == SEED[:6] + ["make-an-app"]
    assert t.message()["card"]["id"] == "picture"
    assert t.turn(-1) is True and t.turn(-1) is True and t.message()["card"]["id"] == "bang"
    assert t.message()["card"]["why"] == "Start here · 6 of 6"
    assert [t.turn(d) for d in (0, 2, None, "1", True, 1.0, -1.0, [1])] == [False] * 8     # a whole number only
    assert machine(tmp_path / "other").turn(1) is False      # nothing to turn before the card is there


def test_a_tip_the_card_is_on_that_the_machine_stops_backing_gives_way(tmp_path):
    t = machine(tmp_path)
    t.ready()
    t.turn(1), t.turn(1)
    assert t.message()["card"]["id"] == "esc"
    t.facts = Facts(ai="")
    assert t.message()["present"] is False
    t.facts = Facts(ai="Claude")
    assert t.message()["card"]["id"] == "esc"                # still on it: nothing moved it
    t.tried(["esc"])
    assert t.message()["card"]["id"] == "esc" and t.advance() and t.message()["card"]["id"] == "make-an-app"


def test_the_card_goes_when_the_six_are_tried_or_the_week_is_over_or_it_is_put_away(tmp_path):
    t = machine(tmp_path)
    t.ready()
    assert t.message()["card"]["id"] == "make-an-app"
    t.tried(SEED[:5])
    assert t.message()["present"] is True and t.message()["card"]["id"] == "make-an-app"   # it stays where it is
    assert t.advance() and t.message()["card"]["id"] == "bang"
    t.tried(["bang"])
    assert t.message() == {"type": "tips", "present": False, "card": None}
    away = {"on": False}
    t2 = Tips(rows=ROWS, path=tmp_path / "two.json", clock=lambda: NOW, hidden=lambda: away["on"])
    t2.facts = Facts(ai="Claude")
    t2.ready()
    assert t2.message()["present"] is True
    away["on"] = True
    assert t2.message()["present"] is False
    away["on"] = False
    assert t2.message()["present"] is True


def test_the_first_week_is_seven_days_of_typing_in_the_pill_not_seven_days_on_the_calendar(tmp_path):
    t = machine(tmp_path)
    assert t.ready() and t.ready() is False and t.state.first_ready == "2026-11-09"
    for n in range(6):
        t.clock["day"] = NOW + timedelta(days=n * 3)         # a day of use every third day
        assert t.use() is True and t.use() is False          # a second prompt on the same day is the same day
        assert t.message()["present"] is True, f"day of use {n + 1}"
    t.clock["day"] = NOW + timedelta(days=400)               # a long time later, with six days of use: still here
    assert t.message()["present"] is True
    assert t.use() is True and len(t.state.days) == 7
    assert t.message() == {"type": "tips", "present": False, "card": None}
    t.ready()
    assert t.state.first_ready == "2026-11-09"               # the first time only


def test_what_the_stick_taught_is_what_the_installed_disk_starts_from(tmp_path):
    stick = machine(tmp_path)
    stick.ready(), stick.use(), stick.tried(["make-an-app", "picture"]), stick.advance()
    installed = machine(tmp_path, installed=True)            # the same file, copied to the new disk
    assert installed.state.tried == ["make-an-app", "picture"]
    assert installed.message()["card"]["id"] == "esc" and installed.message()["card"]["why"] == "Start here · 3 of 6"
    assert installed.ready() is False and installed.state.first_ready == "2026-11-09"


def test_kept_is_the_hook_for_a_card_that_stays_and_turns_through_every_tip_that_fits(tmp_path):
    keep = {"on": False}
    t = Tips(rows=ROWS, path=tmp_path / "tips.json", clock=lambda: NOW, kept=lambda: keep["on"])
    t.facts = Facts(ai="Claude", installed=True, app="Tickets", project="myapp", desk_seen=True)
    t.ready()
    t.tried(SEED)                                            # everything tried, and then the week is over
    assert t.message()["present"] is False
    keep["on"] = True
    assert t.message()["present"] is True
    seen = {}
    while (card := t.message()["card"])["id"] not in seen:
        seen[card["id"]] = card["why"]
        t.turn(1)
    # Start here, then the sections in order; only a tip whose button fits the card, and only one this machine backs.
    assert list(seen) == [i for i in SEED if i not in APP_ONLY and i != "keep"]
    assert seen["change-app"] == "Work · 1 of 4" and seen["whyhere"] == "Work · 4 of 4"
    assert seen["disk"] == "This machine · 1 of 5" and seen["undo"] == "This machine · 5 of 5"


# -- what counts a tip as tried --

def test_showing_a_tip_never_counts(tmp_path):
    t = machine(tmp_path)
    t.ready()
    for _ in range(8):
        t.message(), t.listing(), t.turn(1), t.advance(), t.open("picture")
    assert t.state.tried == []


@pytest.mark.parametrize("keys, tried", [
    (local_keys("picture", "network", "open"), ["picture"]),
    (local_keys("picture", "disks", "open"), ["picture", "disk"]),
    (local_keys("picture", "service:NetworkManager"), ["picture"]),
    (local_keys("widget", "machine", "open"), ["machine"]),
    (local_keys("widget", "machine", "hide"), []),
    (local_keys("widget", "watching", "open"), []),
    (local_keys("app", "tickets", "open"), ["open-app"]),
    (local_keys("app", "tickets", "close"), []),
    (local_keys("rest", "claude", "pause"), ["pause"]),
    (local_keys("rest", "claude", "resume"), []),
    (local_keys("desk"), ["desk"]), (local_keys("whyhere"), ["whyhere"]), (local_keys("why"), ["why"]),
    (local_keys("undo"), ["undo"]), (local_keys("stop"), ["esc"]), (local_keys("brain"), []),
    (tool_keys("mcp__bombadil-os__create_app"), ["make-an-app"]), (tool_keys("create_app"), ["make-an-app"]),
    (tool_keys("mcp__bombadil-os__show_card"), ["picture", "draw"]),
    (tool_keys("mcp__bombadil-os__system_map"), ["picture"]),
    (tool_keys("mcp__bombadil-os__open_app"), []), (tool_keys("Bash"), []), (tool_keys(None), []),
    (job_keys("timer"), ["timer"]), (job_keys("job"), ["tests"]), (job_keys("watch"), []),
    (BANG, ["bang"])])
def test_what_agentd_sees_counts_the_tips_whose_evidence_it_is(tmp_path, keys, tried):
    t = machine(tmp_path)
    assert t.saw(*keys) == tried and t.state.tried == tried
    assert t.saw(*keys) == []                               # a tip is tried once


def test_the_evidence_is_built_as_tried_when_writes_it():
    assert local_keys("picture", "disks") == ("local:picture", "local:picture:disks")
    assert local_keys("undo") == ("local:undo",) and local_keys("app", "x", "hide") == ()
    assert tool_keys("mcp__bombadil-os__create_app") == ("tool:mcp__bombadil-os__create_app", "tool:create_app")
    assert tool_keys("Bash") == ("tool:Bash",) and tool_keys("a b") == ()
    assert job_keys("timer") == ("job:start", "job:start:timer") and job_keys("odd") == ("job:start",)


def test_words_sent_as_the_button_put_them_count_and_edited_words_do_not(tmp_path):
    t = machine(tmp_path)
    assert t.summoned("Make me a ticketing app", "make-an-app") is True
    assert t.sent("make me a ticketing app for four people") == []     # its own ask, with more: tool:create_app counts
    t = machine(tmp_path / "b")
    t.summoned("Set a 25 minute timer", "timer")
    assert t.sent("Set a 25 minute timer for tea") == []     # edited: the person's own ask, not the tip's
    assert t.sent("Set a 25 minute timer") == ["timer"]
    assert t.sent("  Set a   25 minute  timer ") == []        # tried once only
    assert t.state.tried == ["timer"]


def test_words_that_end_in_an_ellipsis_are_the_start_of_an_ask(tmp_path):
    t = machine(tmp_path, app="Tickets")
    t.summoned("In Tickets, add", "change-app")              # the pill's cursor waits after the words
    assert t.sent("In Tickets, add a due date column") == ["change-app"]
    t = machine(tmp_path / "b", app="Tickets")
    assert t.sent("in tickets, add a due date column") == ["change-app"]     # typed by hand: the same words
    assert t.sent("In Notes, add a tag") == []
    t = machine(tmp_path / "c", app="Tickets")
    t.summoned("In Tickets, add…", "change-app")
    assert t.sent("In Tickets, add a Due column") == ["change-app"]


def test_a_tips_own_words_typed_by_hand_count_where_the_tip_says_so(tmp_path):
    t = machine(tmp_path, project="myapp")
    assert t.sent("Make me a password manager.") == ["passwords"]
    assert t.sent("install rust and set it up") == ["toolchain"]
    assert t.sent("Run the tests in ~/Projects/myapp in the background") == ["tests"]
    assert t.sent("run the tests in ~/Projects/other in the background") == []
    assert t.sent("How am I connected?") == []               # a picture word counts by what it does, not by its text
    assert t.sent("") == [] and t.sent("   ") == [] and t.sent(None) == []
    t = machine(tmp_path / "b")                              # no project: its words cannot be filled, so no match
    assert t.sent("Run the tests in ~/Projects/myapp in the background") == []


def test_a_summon_of_something_that_is_no_tip_is_forgotten(tmp_path):
    t = machine(tmp_path)
    assert [t.summoned("x", "no-such-tip"), t.summoned("", "picture"), t.summoned("x", None),
            t.summoned("x", 7)] == [False] * 4
    for n in range(tips.MAX_SUMMONED + 5):
        t.summoned(f"words {n}", "picture")
    assert len(t._summoned) == tips.MAX_SUMMONED
    assert t.sent("words 0") == [] and t.sent("words 28") == ["picture"]


def test_tried_ignores_ids_that_are_not_tips(tmp_path):
    t = machine(tmp_path)
    assert t.tried(["picture", "nope", "picture", "bang"]) == ["picture", "bang"] and t.state.tried == ["picture", "bang"]


# -- the app's list --

def test_the_list_holds_only_what_the_machine_can_back_with_what_the_app_draws_from(tmp_path):
    t = machine(tmp_path, ai="")
    t.ready()
    t.tried(["picture"])
    rows = t.listing()
    assert [r["id"] for r in rows] == ["picture", "machine", "bang", "whyhere", "disk"]
    assert rows[0] == {"id": "picture", "section": "start", "order": 2, "line": "Ask for a picture, not a paragraph.",
                       "words": "How am I connected?", "marks": [], "card": True, "tried": True, "new": False,
                       "captions": ["You ask", "It works", "You have it"], "beats": []}
    assert [r["tried"] for r in rows] == [True, False, False, False, False]
    t.facts = Facts(ai="Claude", app="Tickets", project="myapp", installed=True, desk_seen=True)
    full = t.listing()
    assert [r["id"] for r in full] == [i for i in SEED if i != "keep"]
    assert {r["id"]: r["card"] for r in full if not r["card"]} == dict.fromkeys(APP_ONLY, False)
    assert next(r for r in full if r["id"] == "whyhere")["marks"] == ["where did this come from?"]
    assert next(r for r in full if r["id"] == "open-app")["words"] == "Tickets"


def test_a_tip_that_arrives_with_an_update_is_new_until_the_app_has_listed_it(tmp_path):
    old = ROWS[:-1]
    first = Tips(rows=old, path=tmp_path / "tips.json", clock=lambda: NOW)
    first.facts = Facts(ai="Claude", installed=True)
    first.ready()
    assert first.state.known == [r.id for r in old] and not any(r["new"] for r in first.listing())
    after = Tips(rows=ROWS, path=tmp_path / "tips.json", clock=lambda: NOW)       # agentd restarts on the new table
    after.facts = Facts(ai="Claude", installed=True)
    assert [r["id"] for r in after.listing() if r["new"]] == ["undo"]
    assert not any(r["new"] for r in after.listing())                              # it has been in front of them
    assert after.state.known == SEED[:-1] + ["undo"]
    assert machine(tmp_path / "fresh").listing()[0]["new"] is False                # before setup: nothing is new


def test_a_tip_that_is_new_waits_in_the_app_and_never_reaches_the_card_unasked(tmp_path):
    old = [r for r in ROWS if r.id != "bang"]
    first = Tips(rows=old, path=tmp_path / "tips.json", clock=lambda: NOW)
    first.facts = Facts(ai="Claude")
    first.ready()
    first.tried([r.id for r in old])
    after = Tips(rows=ROWS, path=tmp_path / "tips.json", clock=lambda: NOW)
    after.facts = Facts(ai="Claude")
    assert after.message() == {"type": "tips", "present": False, "card": None}   # an untried Start-here tip, but new
    assert [r["id"] for r in after.listing() if r["new"]] == ["bang"]
    assert after.message()["present"] is True and after.message()["card"]["id"] == "bang"   # now they have seen it


# -- the app is asked to open --

def test_open_keeps_the_tip_for_the_app_and_calls_its_hook_and_opens_nothing(tmp_path):
    asked = []
    t = Tips(rows=ROWS, path=tmp_path / "tips.json", clock=lambda: NOW, on_open=lambda i, p: asked.append((i, p)))
    t.facts = Facts(ai="Claude")
    assert t.open("picture", True) == "picture" and t.state.open_on == "picture"
    assert json.loads((tmp_path / "tips.json").read_text())["open_on"] == "picture"
    assert t.open("keep") == "" and t.open("nope") == "" and t.open(7) == "" and t.open("") == ""
    assert t.state.open_on == ""
    assert asked == [("picture", True), ("", False), ("", False), ("", False), ("", False)]


def test_a_hook_that_breaks_never_costs_the_request(tmp_path, capsys):
    def broken(*_):
        raise RuntimeError("no app")
    t = Tips(rows=ROWS, path=tmp_path / "tips.json", clock=lambda: NOW, on_open=broken)
    t.facts = Facts(ai="Claude")
    assert t.open("picture") == "picture" and "on_open: RuntimeError: no app" in capsys.readouterr().err


def test_desk_seen_unlocks_the_desk_tip_and_is_kept(tmp_path):
    t = machine(tmp_path)
    assert "desk" not in [r["id"] for r in t.listing()]
    assert t.card_seen() and t.card_seen() is False
    assert "desk" in [r["id"] for r in t.listing()]
    assert machine(tmp_path).state.desk_seen is True


def test_parse_state_alone_is_pure():
    assert parse_state(None) == tips.State() and parse_state("") == tips.State()
    s = parse_state(json.dumps({"first_ready": "2026-11-09", "days": ["2026-11-09"], "tried": ["bang"],
                                "known": ["bang"], "open_on": "bang", "desk_seen": True}), SEED)
    assert s == tips.State("2026-11-09", ["2026-11-09"], ["bang"], ["bang"], "bang", True)
    assert parse_state(json.dumps({"first_ready": "2026-11-09", "known": []}), SEED).known == SEED

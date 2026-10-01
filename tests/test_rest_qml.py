"""The pill while the AI rests (out of plan or spending, or paused by hand), in an offscreen window.

The line, its button, the empty field's hint, the waiting chips, the turn the limit stopped halfway,
the line that says the AI is back, and the AI card with its switches: PillState, StatusLine,
SetupChips, QueueChips and AiCard are plain Qt Quick, and the harness is test_pill_qml's. The stone's
own drawing is in test_stone_qml.py. Set BOMBADIL_SCREENS=<dir> to save a picture of each state.
"""

from pathlib import Path

import pytest
import test_desk_qml as desk_tests
import test_pill_qml as base

SHELL = Path(__file__).resolve().parents[1] / "shell"

LIMIT_LINE = "Claude is at its limit until 15:00. Your apps and files still work."
PAUSED_LINE = "Claude is paused. Your apps and files still work."
BACK_LINE = "Claude is back. Running your 3 waiting asks."
CUT_LINE = "Claude hit its limit partway, after changing 2 files. It carries on at 15:00."

LIMIT = dict(type="setup", state="resting", provider="claude", title="Claude", line=LIMIT_LINE, tone="step",
             actions=[], phase=None, view=None,
             rest=dict(provider="claude", why="limit", kind="five_hour", until=1759329600, when="15:00",
                       hint="Open or find anything. Asks wait for 15:00.", note="At 15:00", wait="15:00"))
PAUSED = dict(type="setup", state="resting", provider="claude", title="Claude", line=PAUSED_LINE, tone="step",
              actions=[{"id": "resume", "label": "Resume Claude", "style": "primary"}], phase=None, view=None,
              rest=dict(provider="claude", why="hand", kind=None, until=None, when=None,
                        hint="Open or find anything. Asks wait until you resume Claude.", note="Paused",
                        wait="paused"))
SPEND = dict(type="setup", state="resting", provider="claude", title="Claude",
             line="Claude is at its spending limit until 15:00. Your apps and files still work.", tone="step",
             actions=[{"id": "raise", "label": "Raise the limit", "style": "quiet"}], phase=None, view=None,
             rest=dict(provider="claude", why="spend", kind="overage", until=1759329600, when="15:00",
                       hint="Open or find anything. Asks wait for 15:00.", note="At 15:00", wait="15:00"))
BACK = dict(type="setup", state="ready", provider="claude", title="Claude", line=BACK_LINE, tone="done",
            actions=[], phase=None, view=None)

ROWS = [{"name": "claude", "title": "Claude", "state": "ready", "text": "ready", "on": True,
         "enabled": True, "current": True},
        {"name": "codex", "title": "Codex", "state": "signed_out", "text": "not signed in", "on": False,
         "enabled": False, "current": False}]


@pytest.fixture(scope="module")
def app():
    return base.QtGui.QGuiApplication.instance() or base.QtGui.QGuiApplication([])


@pytest.fixture
def bar(app, tmp_path):
    b = base.Bar(app, tmp_path)
    b.send(type="status", busy=False, provider="claude", queue=[])
    b.send(type="entries", entries=[{"name": "passwords", "title": "Passwords", "kind": "app",
                                     "words": ["passwords"]}])
    yield b
    b.win.close()
    b.engine.deleteLater()
    b.pump()


def prop(bar, name):
    v = bar.pill.property(name)
    return v.toVariant() if hasattr(v, "toVariant") else v


def texts(it):
    """Every Text under an item."""
    out, todo = [], [it]
    while todo:
        x = todo.pop()
        todo.extend(x.childItems())
        if x.metaObject().className().startswith("QQuickText"):
            out.append(x.property("text"))
    return out


def chip_texts(bar):
    """The words of each waiting chip, left to right, as a set (its label, its prompt and the x)."""
    chips = sorted(bar.items("queuedChip"), key=lambda it: it.mapToScene(base.QtCore.QPointF(0, 0)).x())
    return [set(texts(c)) for c in chips]


def status(bar, queue, **more):
    bar.send(type="status", busy=False, provider="claude", queue=queue, **more)


def rest_status(bar, queue, setup=LIMIT):
    status(bar, queue, setup="resting", rest=setup["rest"])


def long_ago(bar, ms=13000):
    bar.pill.setProperty("lineAt", bar.pill.property("lineAt") - ms)
    bar.pump(0.6)


def not_red(bar):
    return bar.item("statusLine").property("edge").alpha() == 0


# -- the line, its button, the stone and the field --

def test_a_limit_says_so_once_in_a_step_line_with_no_red_and_a_resting_stone(bar):
    bar.send(**LIMIT)
    assert bar.text() == LIMIT_LINE
    assert prop(bar, "mode") == "resting" and prop(bar, "source") == "step"
    assert prop(bar, "face") == "resting" and prop(bar, "ready") is False
    assert not_red(bar) and not bar.chips()          # a limit with a time lifts by itself: no button
    assert not bar.shown("undoButton") and not bar.shown("detailsButton") and not bar.shown("counter")
    bar.snap("rest-1-limit")
    assert bar.warnings == []


def test_the_empty_field_carries_the_state_and_the_line_fades_after_twelve_seconds(bar):
    assert prop(bar, "restHint") == ""
    bar.send(**LIMIT)
    assert prop(bar, "restHint") == "Open or find anything. Asks wait for 15:00."
    assert prop(bar, "fadeAfter") == 12000
    bar.pump(0.6)
    assert prop(bar, "mode") == "resting"           # not before its time
    long_ago(bar)
    assert prop(bar, "mode") == "idle" and prop(bar, "face") == "resting"
    assert prop(bar, "restHint") == "Open or find anything. Asks wait for 15:00."
    # The same message again (a bar that reconnects) is not news: the line stays away.
    bar.send(**LIMIT)
    assert prop(bar, "mode") == "idle"


def test_shell_qml_wires_the_hint_the_card_and_the_stone():
    # shell.qml needs Quickshell, so it cannot load here: its few lines are read instead.
    text = (SHELL / "shell.qml").read_text()
    assert 'pillState.restHint || "Ask anything"' in text           # the placeholder
    assert "Region { item: aiCard.visible ? aiCard : null }" in text   # clicks on the card reach it
    assert "AiCard {" in text and "pill: pillState" in text.split("AiCard {", 1)[1].split("}", 1)[0]
    assert "pillState.stoppable ? pillState.stop() : pillState.toggleAi()" in text   # the stone: Stop, else the card
    assert 'if (text !== "") pillState.closeAi()' in text            # typing puts it away
    assert text.index("pillState.aiOpen) { pillState.closeAi()") > text.index("if (pillState.stoppable) pillState.stop()")   # Esc: Stop first


def test_a_pause_by_hand_has_a_resume_button_and_a_changed_line_says_itself_again(bar):
    bar.send(**LIMIT)
    long_ago(bar)
    assert prop(bar, "mode") == "idle"
    bar.send(**PAUSED)                              # paused on top of the limit: new words, said again
    assert bar.text() == PAUSED_LINE and prop(bar, "mode") == "resting"
    assert [label for label, _ in bar.chips()] == ["Resume Claude"]
    assert prop(bar, "restHint") == "Open or find anything. Asks wait until you resume Claude."
    bar.snap("rest-2-paused")
    before = bar.win.property("handOffs")
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "resume"}
    assert bar.win.property("handOffs") == before    # it opens nothing: the keyboard stays where it is


def test_raise_the_limit_opens_a_page_and_then_offers_try_again(bar):
    bar.send(**SPEND)
    assert [label for label, _ in bar.chips()] == ["Raise the limit"] and not_red(bar)
    before = bar.win.property("handOffs")
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "raise"}
    assert bar.win.property("handOffs") == before + 1   # the page takes the keyboard
    bar.pill.setProperty("lineAt", bar.pill.property("lineAt") - 8000)
    bar.send(**{**SPEND, "actions": [{"id": "retry", "label": "Try again", "style": "primary"}]})
    assert [label for label, _ in bar.chips()] == ["Try again"]
    bar.pump(0.6)
    assert prop(bar, "mode") == "resting"           # a pressed button keeps its line a while longer
    handed = bar.win.property("handOffs")
    bar.click_item(bar.chips()[0][1])
    assert bar.sent[-1] == {"type": "setup_action", "id": "retry"}
    assert bar.win.property("handOffs") == handed


def test_summoning_the_pill_says_it_again_unless_a_line_is_being_read(bar):
    bar.send(type="summon")
    assert prop(bar, "mode") == "idle"              # nothing rests: nothing to say
    bar.send(**LIMIT)
    long_ago(bar)
    bar.send(type="summon")
    assert bar.text() == LIMIT_LINE and prop(bar, "mode") == "resting"
    long_ago(bar)
    # A turn has the line: the resting line waits for it, and comes after.
    bar.call("submit", "!ls")
    bar.send(kind="turn_start", turn=1, prompt="!ls")
    bar.send(type="summon")
    assert prop(bar, "mode") == "working"
    bar.send(kind="result", turn=1, ok=True, text="Apps  Documents")
    bar.send(kind="turn_end", turn=1, seconds=0.1, changed=False, summary="")
    assert bar.text() == "Apps  Documents"
    bar.call("dismiss")
    assert bar.text() == LIMIT_LINE and prop(bar, "mode") == "resting"
    bar.call("dismiss")
    assert prop(bar, "mode") == "idle"              # and only once


# -- what waits, and what does not --

def test_asks_wait_as_chips_that_say_when_never_next_and_never_on_it(bar):
    bar.send(**LIMIT)
    long_ago(bar)
    assert bar.call("submit", "make me a habit tracker") is True
    assert bar.sent[-1] == {"type": "prompt", "text": "make me a habit tracker"}
    assert bar.text() != "On it" and not prop(bar, "optimistic") and not prop(bar, "stoppable")
    assert bar.text() == LIMIT_LINE and prop(bar, "mode") == "resting"   # typing an ask that must wait says why
    bar.send(type="queued", turn=1)
    bar.send(kind="queued", turn=1, prompt="make me a habit tracker")
    assert chip_texts(bar) and "15:00" in chip_texts(bar)[0] and "next" not in chip_texts(bar)[0]
    rest_status(bar, [{"turn": 1, "prompt": "make me a habit tracker", "wait": "15:00"},
                      {"turn": 2, "prompt": "and a budget one", "wait": "15:00"}])
    assert [{"15:00", "make me a habit tracker", "×"}, {"15:00", "and a budget one", "×"}] == chip_texts(bar)
    assert prop(bar, "face") == "resting" and bar.text() == LIMIT_LINE
    bar.snap("rest-3-chips")
    bar.call("unqueue", 1)
    assert bar.sent[-1] == {"type": "unqueue", "turn": 1}


def test_a_chip_says_paused_while_paused_and_next_when_the_ai_works(bar):
    bar.send(**PAUSED)
    rest_status(bar, [{"turn": 1, "prompt": "make me a habit tracker", "wait": "paused"}], PAUSED)
    assert "paused" in chip_texts(bar)[0] and "next" not in chip_texts(bar)[0]
    bar.send(**BACK)
    status(bar, [{"turn": 1, "prompt": "make me a habit tracker"}], setup="ready")
    assert "next" in chip_texts(bar)[0]


def test_the_chip_of_an_ask_from_an_app_names_the_app_not_the_tag(bar):
    bar.send(**LIMIT)
    rest_status(bar, [{"turn": 1, "prompt": "[from app notes] summarise this", "wait": "15:00"},
                      {"turn": 2, "prompt": "[from app habit-tracker] what is due", "wait": "15:00"}])
    got = [t for chip in chip_texts(bar) for t in chip]
    assert "Notes: summarise this" in got and "Habit tracker: what is due" in got
    assert not [t for t in got if "[from app" in t]


def test_an_ask_replaced_by_its_apps_newer_one_just_loses_its_chip(bar):
    bar.send(**LIMIT)
    long_ago(bar)
    rest_status(bar, [{"turn": 1, "prompt": "[from app notes] summarise this", "wait": "15:00"}])
    bar.send(kind="unqueued", turn=1, replaced=True)
    bar.send(kind="queued", turn=2, prompt="[from app notes] summarise that")
    rest_status(bar, [{"turn": 2, "prompt": "[from app notes] summarise that", "wait": "15:00"}])
    assert len(bar.items("queuedChip")) == 1 and prop(bar, "mode") == "idle"
    assert bar.warnings == []


def test_a_shell_command_still_runs_while_the_ai_rests_and_the_stone_says_so(bar):
    bar.send(**LIMIT)
    long_ago(bar)
    assert bar.call("submit", "!ls") is True
    assert bar.text() == "On it" and prop(bar, "face") == "working"
    bar.send(kind="turn_start", turn=1, prompt="!ls")
    assert prop(bar, "face") == "working"
    bar.send(kind="result", turn=1, ok=True, text="Apps  Documents")
    bar.send(kind="turn_end", turn=1, seconds=0.1, changed=False, summary="")
    assert bar.text() == "Apps  Documents" and prop(bar, "face") == "resting"   # not the hop of "done"


def test_an_exact_launcher_word_brings_no_resting_line(bar):
    bar.send(**LIMIT)
    long_ago(bar)
    assert bar.call("submit", "passwords") is True
    assert prop(bar, "mode") == "idle" and not prop(bar, "optimistic")
    bar.send(type="local", action="app")
    bar.send(kind="local", turn=None, action="app", phase="done", ok=True, text="Opened Passwords.")
    assert bar.text() == "Opened Passwords."
    long_ago(bar, 6000)
    assert prop(bar, "mode") == "idle"              # and the resting line does not come back after it


def test_events_it_does_not_know_are_ignored(bar):
    bar.send(**LIMIT)
    bar.send(kind="rest", turn=1, provider="claude", why="limit", window="five_hour", until=1759329600,
             text="You've hit your session limit")
    assert prop(bar, "mode") == "resting" and bar.text() == LIMIT_LINE and bar.warnings == []


# -- a turn the limit stopped halfway --

def cut_off(bar, changed=True, line=CUT_LINE):
    bar.call("submit", "make me a habit tracker")
    bar.send(kind="turn_start", turn=1, prompt="make me a habit tracker")
    bar.send(kind="status", turn=1, text="Writing tracker/main.qml", source="step")
    bar.send(kind="turn_end", turn=1, seconds=40, changed=changed, summary="", stopped=False, line=line,
             requeued=True, irreversible=False, read=[])
    # What agentd sends next: the turn goes back to the front of the queue, and the machine rests.
    bar.send(kind="queued", turn=1, prompt="make me a habit tracker")
    bar.send(**LIMIT)
    rest_status(bar, [{"turn": 1, "prompt": "make me a habit tracker", "wait": "15:00"}])


def test_a_turn_the_limit_stopped_closes_in_a_step_line_with_undo_and_details(bar):
    cut_off(bar)
    assert bar.text() == CUT_LINE and prop(bar, "mode") == "closing"
    assert prop(bar, "source") == "step" and not_red(bar) and "Done." not in bar.text()
    assert bar.shown("undoButton") and bar.shown("detailsButton") and prop(bar, "sticky")
    assert prop(bar, "face") == "resting"           # hollow from the first moment, never the hop of "done"
    assert chip_texts(bar) == [{"15:00", "make me a habit tracker", "×"}]
    bar.snap("rest-4-cut-off")
    before = len(bar.sent)
    bar.click("undoButton")
    assert bar.sent[before:] == [{"type": "local", "action": "undo"}]
    bar.click("detailsButton")
    assert bar.sent[-1] == {"type": "details", "turn": 1}
    assert bar.warnings == []


def test_the_cut_off_line_stays_until_dismissed_and_the_resting_line_takes_over(bar):
    cut_off(bar)
    bar.pump(0.6)
    assert bar.text() == CUT_LINE                   # a line with Undo is not taken over by the state
    bar.call("dismiss")
    assert bar.text() == LIMIT_LINE and prop(bar, "mode") == "resting" and not bar.shown("undoButton")
    long_ago(bar)
    assert prop(bar, "mode") == "idle" and prop(bar, "face") == "resting"


def test_a_cut_off_turn_that_changed_nothing_says_the_resting_line_once(bar):
    cut_off(bar, changed=False, line=LIMIT_LINE)
    assert bar.text() == LIMIT_LINE and prop(bar, "mode") == "closing" and not_red(bar)
    assert not bar.shown("undoButton") and not prop(bar, "sticky")
    assert prop(bar, "face") == "resting"
    long_ago(bar)
    assert prop(bar, "mode") == "idle"              # not once more after it fades


def test_a_cut_off_turn_shows_no_error_even_if_the_cli_said_something(bar):
    bar.call("submit", "make me a habit tracker")
    bar.send(kind="turn_start", turn=1, prompt="make me a habit tracker")
    bar.send(kind="error", turn=1, text="API Error: 429")
    bar.send(kind="turn_end", turn=1, seconds=2, changed=True, summary="Wrote 2 files.", line=CUT_LINE, requeued=True)
    assert bar.text() == CUT_LINE and prop(bar, "source") == "step" and not_red(bar)


def test_typing_an_ask_over_the_cut_off_line_brings_the_resting_line(bar):
    cut_off(bar)
    assert bar.call("submit", "and a budget one too") is True
    assert bar.text() == LIMIT_LINE and prop(bar, "mode") == "resting"


# -- back --

def test_back_by_itself_says_so_for_eight_seconds_and_the_stone_fills(bar):
    bar.send(**LIMIT)
    rest_status(bar, [{"turn": 1, "prompt": "make me a habit tracker", "wait": "15:00"}])
    bar.send(**BACK)
    assert bar.text() == BACK_LINE and prop(bar, "mode") == "local" and prop(bar, "fadeAfter") == 8000
    assert prop(bar, "face") == "rest" and prop(bar, "ready") is True and prop(bar, "restHint") == ""
    assert not bar.chips() and not_red(bar)
    bar.snap("rest-5-back")
    long_ago(bar, 9000)
    assert prop(bar, "mode") == "idle"


def test_the_back_line_goes_on_being_said_over_the_ask_it_starts(bar):
    bar.send(**LIMIT)
    bar.send(**BACK)
    bar.send(kind="turn_start", turn=1, prompt="make me a habit tracker")
    assert prop(bar, "mode") == "working" and prop(bar, "line") == "On it"
    assert bar.text() == BACK_LINE                  # the turn starts at once: its first words must not wipe it
    bar.send(kind="result", turn=1, ok=True, text="Made the tracker.")
    bar.send(kind="turn_end", turn=1, seconds=9, changed=False, summary="")
    assert bar.text() == "Made the tracker."        # and the turn's own line is not hidden behind it


def test_back_after_a_pause_with_nothing_waiting(bar):
    bar.send(**PAUSED)
    bar.send(**{**BACK, "line": "Claude is back."})
    assert bar.text() == "Claude is back." and prop(bar, "face") == "rest" and not bar.chips()


def test_a_limit_that_lapses_without_a_line_just_clears_it(bar):
    bar.send(**LIMIT)
    bar.send(**{**BACK, "line": ""})
    assert prop(bar, "mode") == "idle" and prop(bar, "face") == "rest"


def test_a_sign_in_problem_outranks_resting_and_resting_takes_over_after_it(bar):
    bar.send(**LIMIT)
    bar.send(type="setup", state="signed_out", line="Sign in to Claude to start.", tone="step",
             actions=[{"id": "signin", "label": "Sign in", "style": "primary"}])
    assert prop(bar, "face") == "needs" and prop(bar, "mode") == "setup" and prop(bar, "rest") is None
    assert [label for label, _ in bar.chips()] == ["Sign in"]
    bar.send(**LIMIT)
    assert prop(bar, "face") == "resting" and prop(bar, "mode") == "resting"


def test_the_stone_is_never_needs_while_the_ai_rests(bar):
    for setup in (LIMIT, PAUSED, SPEND):
        bar.send(**setup)
        assert prop(bar, "face") == "resting"
        long_ago(bar)
        assert prop(bar, "face") == "resting"
    bar.call("lost")
    assert prop(bar, "face") == "offline"           # no agentd outranks it
    bar.send(type="status", busy=False, provider="claude", queue=[])
    assert prop(bar, "face") == "resting"


# -- the AI card --

def open_card(bar, rows=ROWS):
    bar.call("toggleAi")
    bar.send(type="ai", rows=rows)
    bar.pump(0.5)       # the card rises; a click lands once it has stopped moving


def test_a_click_on_the_stone_opens_the_card_with_one_row_per_ai(bar):
    assert not bar.shown("aiCard") and prop(bar, "aiOpen") is False
    bar.call("toggleAi")
    assert bar.sent[-1] == {"type": "ai", "op": "get"} and prop(bar, "aiOpen") is True
    bar.pump(0.4)
    assert not bar.shown("aiCard")                  # nothing to show until agentd answers
    bar.send(type="ai", rows=ROWS)
    bar.pump(0.5)
    assert bar.shown("aiCard") and len(bar.items("aiRow")) == 2
    rows = sorted(bar.items("aiRow"), key=lambda it: it.mapToScene(base.QtCore.QPointF(0, 0)).y())
    assert [sorted(texts(r)) for r in rows] == [["Claude", "· ready"], ["Codex", "· not signed in"]]
    switches = sorted(bar.items("aiSwitch"), key=lambda it: it.mapToScene(base.QtCore.QPointF(0, 0)).y())
    assert [s.property("on") for s in switches] == [True, False]
    assert [s.property("usable") for s in switches] == [True, False]
    assert [s.property("opacity") for s in switches] == [1, 0.4]
    bar.snap("rest-6-card")
    assert bar.warnings == []


def test_a_switch_pauses_and_resumes_and_a_dimmed_one_does_nothing(bar):
    open_card(bar)
    on, off = sorted(bar.items("aiSwitch"), key=lambda it: it.mapToScene(base.QtCore.QPointF(0, 0)).y())
    before = len(bar.sent)
    bar.click_item(off)
    assert len(bar.sent) == before                  # Codex is not signed in: nothing to pause or resume
    bar.click_item(on)
    assert bar.sent[before:] == [{"type": "ai", "op": "pause", "provider": "claude"}]
    assert on.property("on") is True                # the switch follows agentd, not the tap
    paused = [{**ROWS[0], "state": "paused", "text": "paused", "on": False}, ROWS[1]]
    bar.send(type="ai", rows=paused)
    bar.pump(0.4)
    assert on.property("on") is False
    assert "· paused" in [t for r in bar.items("aiRow") for t in texts(r)]
    bar.click_item(on)
    assert bar.sent[-1] == {"type": "ai", "op": "resume", "provider": "claude"}
    bar.snap("rest-7-card-paused")


def test_a_limit_row_reads_at_its_limit_and_its_switch_is_on_and_works(bar):
    row = {"name": "claude", "title": "Claude", "state": "limit", "text": "at its limit until 15:00", "on": True,
           "enabled": True, "current": True}
    open_card(bar, [row, ROWS[1]])
    assert "· at its limit until 15:00" in [t for r in bar.items("aiRow") for t in texts(r)]
    switch = min(bar.items("aiSwitch"), key=lambda it: it.mapToScene(base.QtCore.QPointF(0, 0)).y())
    bar.click_item(switch)
    assert bar.sent[-1] == {"type": "ai", "op": "pause", "provider": "claude"}


def test_a_second_click_esc_and_a_turn_close_the_card(bar):
    open_card(bar)
    bar.call("toggleAi")
    assert prop(bar, "aiOpen") is False
    bar.pump(0.5)
    assert not bar.shown("aiCard")
    open_card(bar)
    bar.call("closeAi")                             # Esc, and typing, in shell.qml
    bar.pump(0.5)
    assert prop(bar, "aiOpen") is False and not bar.shown("aiCard")
    bar.call("toggleAi")
    assert prop(bar, "aiOpen") is True
    bar.call("submit", "what time is it")           # a turn begins: the stone is Stop now
    assert prop(bar, "stoppable") is True and prop(bar, "aiOpen") is False


def test_the_card_does_not_open_while_a_turn_runs_and_goes_when_agentd_does(bar):
    bar.call("submit", "what time is it")
    bar.call("toggleAi")                            # the stone is Stop now
    assert prop(bar, "aiOpen") is False and bar.sent[-1] == {"type": "prompt", "text": "what time is it"}
    bar.send(kind="turn_start", turn=1, prompt="what time is it")
    bar.send(kind="turn_end", turn=1, seconds=1, changed=False, summary="")
    open_card(bar)
    assert bar.shown("aiCard")
    bar.call("lost")
    bar.pump(0.5)
    assert prop(bar, "aiOpen") is False and not bar.shown("aiCard")
    before = len(bar.sent)
    bar.call("toggleAi")
    assert prop(bar, "aiOpen") is False and len(bar.sent) == before
    assert bar.pill.property("flash") == "Not connected to the agent yet."
    bar.send(type="status", busy=False, provider="claude", queue=[])      # it is back
    bar.call("toggleAi")
    assert prop(bar, "aiOpen") is True and bar.sent[-1] == {"type": "ai", "op": "get"}


def test_rows_that_are_not_a_list_leave_no_card(bar):
    bar.call("toggleAi")
    bar.send(type="ai", rows=None)
    bar.pump(0.4)
    assert not bar.shown("aiCard") and prop(bar, "aiRows") == []
    assert bar.warnings == []


def test_the_card_and_the_rest_faces_use_the_themes_family(bar):
    open_card(bar)
    bar.send(**PAUSED)
    families = {(it.property("text"), it.property("font").family())
                for it in _all_texts(bar)}
    assert {"Claude", "Resume Claude", PAUSED_LINE} <= {t for t, _ in families}
    assert {f for _, f in families} == {base.THEME["fontFamily"]}


def _all_texts(bar):
    found, todo = [], [bar.win.contentItem()]
    while todo:
        it = todo.pop()
        todo.extend(it.childItems())
        if it.metaObject().className().startswith("QQuickText") and it.isVisible():
            found.append(it)
    return found


# -- the desk --

def test_a_turn_the_limit_stopped_takes_its_card_off_the_desk_at_once(app, tmp_path):
    steps = [("Make the files", "Making the files", "completed"), ("Write the app", "Writing the app", "in_progress")]
    d = desk_tests.Desk(app, tmp_path)
    try:
        d.send(type="status", busy=False, provider="claude", queue=[])
        d.turn(1, "make me a habit tracker", steps=steps)
        assert d.prop("nowVisible")
        d.end(1, requeued=True, line=CUT_LINE)
        assert not d.prop("nowVisible")                      # not "done in 58 s": it was not
        assert d.pill.property("mode") == "closing"          # the closing line, with its Undo, is the pill's
        assert d.warnings == []
    finally:
        d.win.close()
        d.engine.deleteLater()
        d.pump()

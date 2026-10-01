"""Fixes from the second review of the loop, in the bar's QML and the Noticed window: the card and its
status line, what a second Super tap says, and what the window does when agentd comes back or a report
is asked for. One test per defect, each of which failed before its fix."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_loop_qml import FOUND, MORE, OFFER, app, bar  # noqa: E402,F401  (the bar, built the way shell.qml builds it)
from test_noticed_app import drive  # noqa: E402  (the window and a fake agentd, in a child process)

SHELL = Path(__file__).resolve().parents[1] / "shell"


def reported(**kw):
    """FOUND as the service lists it once its report was asked for: now a row that says "See the report"."""
    return dict(FOUND, kind="report", primary={"label": "See the report", "op": "report"}, **kw)


def card_of(bar):
    return bar.box(bar.item("noticedCard"))


# -- a second tap on Super --

def test_a_summon_given_back_says_so_once_and_is_not_acked_after(bar):
    bar.loop_send(type="summon", id=5)
    bar.call("summonCancelled")
    assert bar.drain() == [{"type": "focus_cancel", "id": 5}]
    bar.call("inputFocused")                          # nothing is waiting for the keyboard any more
    bar.call("summonCancelled")
    assert bar.drain() == []
    bar.call("summonCancelled")
    assert bar.drain() == []


def test_a_summon_that_got_its_ack_has_nothing_left_to_cancel(bar):
    bar.loop_send(type="summon", id=6)
    bar.call("inputFocused")
    assert [m["type"] for m in bar.drain()] == ["focus_ack"]
    bar.call("summonCancelled")
    assert bar.drain() == []


def test_shell_gives_the_keyboard_back_through_the_loop_state():
    """shell.qml's summon() toggles the pill; the tap that turns it off tells the loop state."""
    text = (SHELL / "shell.qml").read_text()
    body = text[text.index("function summon()"):text.index("function release()")]
    assert 'root.summonedOn === name ? "" : name' in body
    assert 'if (root.summonedOn === "") loopState.summonCancelled()' in body


# -- the report is the Noticed window's to show --

LONG_REPORT = "\n".join(["Title: Details drawer takes no keyboard (Hyprland 0.56.2) [fp 3c91a0]",
                         "Seen: 3 times on 2 days, build d9dde3b"] + [f"picture row {i}" for i in range(40)])


def test_the_answer_to_a_report_never_reaches_the_card(bar):
    bar.noticed(FOUND)
    bar.peek()
    bar.press("noticedPrimary")
    assert bar.drain() == [{"type": "noticed_do", "op": "report", "id": "f1"}]
    bar.loop_send(type="noticed_result", op="report", id="f1", ok=True, text="",
                  preview={"text": LONG_REPORT, "goes": ["a"], "stays": ["b"]})
    bar.noticed(reported())                           # the row stays, now to be seen in the window
    bar.leave()
    bar.peek()
    assert bar.call("snapshot")["result"]["preview"] == ""
    assert not bar.shown("noticedResult")
    assert card_of(bar).y1 - card_of(bar).y0 < 400   # a card with one row, not the report under it
    for op in ("send", "report"):
        bar.loop_send(type="noticed_result", op=op, id="f1", ok=True, text="", preview=LONG_REPORT)
        assert not bar.shown("noticedResult")


def test_a_report_that_failed_still_says_why_under_its_row(bar):
    bar.noticed(FOUND)
    bar.peek()
    bar.loop_send(type="noticed_result", op="report", id="f1", ok=False, text="There is nothing to write up yet.")
    assert bar.text("noticedResultText") == "There is nothing to write up yet."


def test_a_long_answer_is_clipped_so_the_card_stays_a_card(bar):
    bar.noticed(OFFER)
    bar.peek()
    bar.loop_send(type="noticed_result", op="preview", id="a1", ok=True, text="", preview=LONG_REPORT)
    assert bar.shown("noticedResult")
    box = card_of(bar)
    assert box.y1 - box.y0 < 500 and box.y0 >= 0
    answer = bar.item("noticedResultText")
    assert answer.property("lineCount") <= 6


# -- the card and the status line --

def test_the_card_beside_the_pill_clears_the_status_line_and_its_undo(bar):
    undo = {"type": "noticed_do", "op": "undo", "id": "w1"}
    bar.noticed(OFFER, MORE)
    bar.pill_send(kind="local", action="word", phase="done", ok=True, text="Made “my passwords” open Passwords.",
                  undo_msg=undo)
    bar.pump(0.3)
    assert bar.shown("undoButton") and bar.item("noticedChip").property("beside") is True
    bar.click(bar.item("noticedChip"))
    assert bar.shown("noticedCard")
    card, line, undo_box = card_of(bar), bar.box(bar.item("statusLine")), bar.box(bar.item("undoButton"))
    assert card.y1 <= line.y0 and card.y1 <= undo_box.y0     # above the line, not over its right end
    bar.snap("card-above-receipt")


def test_the_card_beside_the_pill_is_where_it_was_when_nothing_else_is_up(bar):
    bar.noticed(OFFER)
    bar.peek()
    card, pill = card_of(bar), bar.box(bar.item("pillBox"))
    assert pill.y0 - card.y1 == 6                              # six above the pill, as before


# -- Esc at a card --

def test_a_burst_of_esc_that_began_at_a_kept_card_says_a_card_was_up(bar):
    bar.noticed(OFFER)
    bar.click(bar.item("noticedChip"))
    bar.drain()
    bar.set(fixedNow=30000.0)
    for step in range(3):
        bar.set(fixedNow=30000.0 + step * 1000)
        bar.call("esc")                               # the first of them puts the card away
    assert bar.prop("kept") is False
    assert bar.drain() == [{"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": False,
                            "card": True}]


def test_a_burst_with_the_drawer_up_and_no_card_says_the_drawer_only(bar):
    bar.set(drawer=True, fixedNow=40000.0)
    for step in range(3):
        bar.set(fixedNow=40000.0 + step * 1000)
        bar.call("esc")
    assert bar.drain() == [{"type": "friction", "what": "esc", "count": 3, "seconds": 10, "drawer": True,
                            "card": False}]


# -- agentd goes while the card is up --

def test_a_card_does_not_stay_up_with_buttons_that_do_nothing_when_agentd_goes(bar):
    bar.noticed(OFFER)
    bar.click(bar.item("noticedChip"))
    bar.press("noticedOtherWays")
    bar.loop_send(type="noticed_result", op="preview", id="a1", ok=True, text="", preview="Seen 4 times.")
    bar.drain()
    assert bar.shown("noticedCard") and bar.shown("noticedResult")
    bar.set(linked=False)
    assert not bar.shown("noticedCard") and not bar.shown("noticedChip")
    assert bar.prop("kept") is False and bar.prop("peeked") is False
    assert bar.prop("focusedRow") == "" and bar.prop("result") is None and bar.prop("pending") == ""
    bar.set(linked=True)
    bar.pump(0.1)
    assert bar.shown("noticedChip") and not bar.shown("noticedCard")     # the chip is back, the card is not
    bar.peek()
    assert not bar.shown("noticedResult")


def test_a_peeked_card_goes_with_agentd_too(bar):
    bar.noticed(OFFER)
    bar.peek()
    bar.set(linked=False)
    assert not bar.shown("noticedCard") and bar.prop("peeked") is False


# -- the Noticed window --

def test_a_window_does_not_show_an_old_list_as_ready_after_agentd_comes_back_silent(home):
    out = drive(home, """
        fake.start()
        assert wait_until(settled)
        OUT["first"] = backend.property("ready") and shows("What you ask most")
        fake.answer_lists = False              # the agentd that comes back says nothing about noticed
        fake.stop()
        assert wait_until(lambda: not backend.property("connected"), 3000)
        OUT["after_loss"] = backend.property("ready")
        fake.start()
        assert wait_until(lambda: backend.property("connected"), 5000)
        listed = fake.lists
        pump(1200)
        OUT["after_reconnect"] = backend.property("ready")
        OUT["old_list_shown"] = shows("What you ask most")
        OUT["asked_again"] = fake.lists - listed
        fake.answer_lists = True
        assert wait_until(lambda: backend.property("ready") and shows("What you ask most"), 5000)
        OUT["back"] = True
    """)
    assert out["first"] is True and out["after_loss"] is False
    assert out["after_reconnect"] is False and out["old_list_shown"] is False
    assert out["asked_again"] >= 2                 # the tick keeps asking until agentd answers
    assert out["back"] is True


def test_a_report_asked_for_while_another_card_is_open_takes_the_card(home):
    out = drive(home, """
        fake.full["found"][0].update(state="reported", preview=PREVIEW)
        fake.start()
        assert wait_until(lambda: find("sendText") is not None), "the held report did not open by itself"
        OUT["first"] = [root.property("sendId"), prop("sendText", "text")[:30]]
        # He pressed "See the report" for the other row in the bar: agentd marks it and the lists change.
        second = dict(PREVIEW, text="Title: Two apps opened on top of each other [fp 77b2e1]")
        fake.full["found"][1].update(state="reported", preview=second)
        backend.refresh()
        assert wait_until(lambda: root.property("sendId") == "f-2"), root.property("sendId")
        assert wait_until(lambda: (prop("sendText", "text") or "").startswith("Title: Two apps"))
        OUT["second"] = [root.property("sendId"), prop("sendText", "text")[:30]]
        # The list coming again changes nothing, and Back still puts the card away.
        backend.refresh()
        pump(800)
        OUT["stays"] = root.property("sendId")
        click("btn:back")
        assert wait_until(lambda: find("section:found") is not None)
        OUT["lists"] = find("sendText") is None
    """)
    assert out["first"][0] == "f-1" and out["first"][1].startswith("Title: Details drawer")
    assert out["second"] == ["f-2", "Title: Two apps opened on top "]
    assert out["stays"] == "f-2" and out["lists"] is True

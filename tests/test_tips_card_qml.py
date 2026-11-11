"""The Tips card on the desk (shell/TipsCard.qml), drawn offscreen inside the real rail with the real DeskState.

The messages are the ones agentd really builds: a `Tips` (src/bombadil/tips.py) over the shipped table, with a
clock and a machine the test hands it, so what the card draws is what a new person's desk would get. Every card
tip of the table is drawn and checked for overflow. Set BOMBADIL_SCREENS=<dir> to save a picture of each (and
look at them: tips-card-<id>-<width>x<height>.png, tips-card-poster-*.png for a card whose scene has beats).
"""

import os
import re
import time
from datetime import date
from pathlib import Path

import pytest
import tip_fixtures as fx
from qml_running import running_things
from qml_theme import THEME
from test_desk_qml import Desk, plain

from bombadil import desk as desk_py
from bombadil import tips as tips_mod
from bombadil.tips import Facts, Tips

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
SAMPLE = Facts(ai="Claude", installed=True, app="Customer Dashboard", project="myapp", desk_seen=True, keep=True)
SIZES = [(1280, 720), (1920, 1080)]
FADE = "QQuickNumberAnimation"


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


def early(started, limit):
    """Is it still early enough in a fade that began at `started` (time.monotonic()) to see it half way? A pump asks
    for 30 or 50 ms and a box under load gives it more, so the 120 or 200 ms fade can be over by the time the test
    looks. A check made then says nothing about the card, so a mid-fade check is made only while it is early (`limit`
    is well under the fade's length) and the end state, which no slow box changes, is always checked."""
    return time.monotonic() - started < limit


def ready(d):
    """The shell as it is after sign-in: setup ready, the ready line gone, nothing running."""
    d.send(type="status", busy=False, provider="claude", queue=[])
    d.send(type="setup", state="ready", line="", tone="done", actions=[])


class Card:
    """A desk with the Tips card up, and what the card does to it."""

    def __init__(self, app, tmp_path, width, height, facts=SAMPLE, kept=False, up=True):
        self.desk = Desk(app, tmp_path, width, height)
        self.tmp = tmp_path
        self.kept = kept          # what the desk says of Tips (desk.py): a test moves it with keep()
        self.desk_py = desk_py.Desk(tmp_path / "desk.toml")
        self.tips = Tips(path=tmp_path / "tips.json", clock=lambda: date(2026, 11, 9), kept=lambda: self.kept)
        self.tips.facts = facts
        self.tips.ready()
        if up:
            ready(self.desk)
            self.show()
            self.desk.pump(0.4)       # the arrival: the desk's own fade and scale, once

    # -- agentd's side --

    def show(self, msg=None):
        self.desk.send(**(msg or self.tips.message()))

    def turn(self, direction=1):
        """What agentd does when the card asks to turn: the next card, as the message it sends."""
        self.tips.turn(direction)
        self.show()

    def end_the_first_week(self):
        """The person has used the pill on seven days and tried the six Start-here tips: Tips has nothing more to
        say to a new person, so agentd says no card, unless they keep it."""
        self.tips.state.days = [f"2026-11-{day:02d}" for day in range(1, 8)]
        self.tips.state.tried = [t.id for t in self.tips.listed() if t.section == "start"]

    def keep(self, *ops):
        """The person's words or presses at the desk, as agentd's Desk applies them (desk.py), and what follows:
        the desk's message, and the card as Tips now says it (a kept Tips reads `desk.kept`)."""
        for op in ops:
            ok, said = self.desk_py.apply(*op)
            assert ok, said
        snap = self.desk_py.snapshot()
        self.kept = "tips" in snap["kept"]
        self.desk.send(**snap)
        self.show()
        return snap

    # -- looking --

    @property
    def card(self):
        return self.desk.item("tipsCard")

    def named(self, name):
        """The visible item called `name` on the card (the face in front, when two are up)."""
        found = [it for it in self.desk.items(name) if self._under_card(it)]
        return found[0] if found else None

    def all(self, name, visible_only=True):
        return [it for it in self.desk.items(name, visible_only) if self._under_card(it)]

    @staticmethod
    def _under_card(item):
        while item is not None:
            if item.objectName() == "tipsCard":
                return True
            item = item.parentItem()
        return False

    def front(self):
        faces = [f for f in self.all("tipsFaceA") + self.all("tipsFaceB") if f.property("opacity") == 1]
        assert len(faces) == 1, "one face is in front"
        return faces[0]

    def inside(self, item, name):
        return self.desk.inside(item, name)

    def text(self, name):
        it = self.named(name)
        return None if it is None else it.property("text")

    @property
    def sent(self):
        return self.desk.sent

    def forget(self):
        self.desk.win.setProperty("sent", [])
        self.desk.win.setProperty("pillSent", [])

    def hover(self, x=150, y=100):
        pt = self.card.mapToScene(QtCore.QPointF(x, y)).toPoint()
        QtTest.QTest.mouseMove(self.desk.win, pt)
        self.desk.pump(0.3)

    def leave(self):
        QtTest.QTest.mouseMove(self.desk.win, QtCore.QPoint(5, 5))
        self.desk.pump(0.3)

    def wheel(self, dy, x=150, y=100):
        pt = QtCore.QPointF(self.card.mapToScene(QtCore.QPointF(x, y)))
        ev = QtGui.QWheelEvent(pt, pt, QtCore.QPoint(0, 0), QtCore.QPoint(0, dy), QtCore.Qt.NoButton,
                               QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
        QtCore.QCoreApplication.sendEvent(self.desk.win, ev)
        self.desk.pump(0.05)

    def click(self, item, x=None, y=None):
        self.desk.click(item, x, y)

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.desk.pump(0.3)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.desk.win.grabWindow().save(str(Path(out) / f"{name}.png"))

    def image(self):
        self.desk.pump(0.3)
        return self.desk.win.grabWindow()

    def theme(self):
        return self.desk.engine.singletonInstance("Bombadil", "Theme")

    def close(self):
        self.desk.win.close()
        self.desk.engine.deleteLater()
        self.desk.pump()


@pytest.fixture
def make(app, tmp_path):
    made = []

    def build(width=1280, height=720, **kw):
        sub = tmp_path / f"card{len(made)}"
        sub.mkdir()
        c = Card(app, sub, width, height, **kw)
        made.append(c)
        return c
    yield build
    for c in made:
        c.close()


def color(item, prop="color"):
    return item.property(prop).name().lower()


def px(font):
    return font.pixelSize()


def evaluate(item, source):
    """A QML expression in `item`'s scope (a Rectangle's grouped `border` cannot be read as a property)."""
    ex = QtQml.QQmlExpression(QtQml.QQmlEngine.contextForObject(item), item, source)
    value, _ = ex.evaluate()
    assert not ex.hasError(), ex.error().toString()
    return value


# -- the card, as the brief draws it --

@pytest.mark.parametrize("width, height", SIZES)
def test_the_card_is_300_by_290_and_has_what_the_brief_says_top_down(make, width, height):
    c = make(width, height)
    d = c.desk
    assert d.faces["tips"] == "full" and d.warnings == []
    slot = d.slots["tips"]
    assert (slot["w"], slot["h"]) == (300, 290)
    assert slot["x"] == d.prop("screenWidth") - 16 - 300 and slot["y"] + 290 == d.prop("railBottomY")   # alone: nearest the pill
    card = c.card
    assert (card.width(), card.height()) == (300, 290)

    title = c.named("tipsTitle")
    assert title.property("text") == "Tips" and px(title.property("font")) == 14
    assert title.property("font").weight() == QtGui.QFont.Weight.DemiBold
    assert color(title, "color") == THEME["fg"]
    why = c.named("tipsWhy")
    assert why.property("text") == "Start here · 1 of 6" and px(why.property("font")) == 12
    assert color(why) == THEME["muted"]
    assert why.property("font").featureValue(QtGui.QFont.Tag("tnum")) == 1            # tabular figures: 1 of 6 and 2 of 6 are as wide

    face = c.front()
    picture = c.inside(face, "tipsPicture")
    assert (picture.width(), picture.height()) == (272, 153)
    assert c.desk.at(picture)[0] - c.desk.at(card)[0] == 14 and c.desk.at(picture)[1] - c.desk.at(card)[1] == 58
    stages = [s for s in c.all("stage") if c.inside(face, "tipsPicture") is not None]
    assert stages and stages[0].property("width") == 272 and stages[0].property("height") == 153   # card density

    line = c.inside(face, "tipsLine")
    assert line.property("text") == "Say what you need. It gets made." and px(line.property("font")) == 13
    assert color(line) == THEME["fg"] and line.property("implicitWidth") <= 272 and not line.property("truncated")

    button = c.inside(face, "tipsButton")
    label = c.inside(button, "tipsButtonText")
    assert label.property("text") == "Make me a ticketing app" and px(label.property("font")) == 13
    assert color(label) == THEME["fg"] and not label.property("truncated")
    assert (button.height(), button.property("radius")) == (28, 14)
    assert evaluate(button, "border.width") == 1
    assert evaluate(button, "border.color.toString()").lower() == THEME["borderStrong"].lower()
    assert button.property("color").name(QtGui.QColor.HexArgb) == THEME["glassRaised"].lower()

    all_tips = c.named("tipsAll")
    assert all_tips.property("text") == "All tips ›" and px(all_tips.property("font")) == 12
    assert color(all_tips) == THEME["muted"]
    assert abs(d.at(all_tips)[0] + all_tips.width() - (d.at(card)[0] + 286)) <= 1       # at the right, in the row
    assert d.at(button)[0] + button.width() + 10 <= d.at(all_tips)[0]                   # and the two never touch
    assert abs((d.at(button)[1] + 14) - (d.at(all_tips)[1] + all_tips.height() / 2)) <= 1   # on the button's centre line
    c.snap(f"tips-card-first-{width}x{height}")


def test_the_card_has_no_dots_no_timer_and_nothing_running_at_rest(make):
    c = make()
    c.desk.pump(0.5)
    assert running_things(c.desk.win) == []
    assert not [it for it in c.all("", False) if "dot" in it.objectName().lower() or "page" in it.objectName().lower()]
    source = (SHELL / "TipsCard.qml").read_text()
    assert "Timer" not in source and "Infinite" not in source and "SequentialAnimation" not in source
    # the scene is a still poster, never played: nothing in it runs either
    for stage in c.all("stage"):
        assert stage.parentItem() is not None
    assert running_things(c.card) == []


def test_the_card_draws_no_edge_bar_no_dot_and_no_accent(make):
    c = make()
    edge = c.desk.item("tipsEdge")
    assert edge is not None and not edge.isVisible()
    assert c.desk.faces["tips"] == "full"
    # nothing in the header or the rows is the accent orange: the face is neutral
    img = c.image()
    card = c.card
    left, top = c.desk.at(card)
    orange = QtGui.QColor(THEME["accent"])
    for dx in range(2, 298, 3):                       # the header, the line and the button rows
        for dy in list(range(4, 56, 4)) + list(range(220, 288, 4)):
            p = QtGui.QColor(img.pixel(left + dx, top + dy))
            assert abs(p.red() - orange.red()) + abs(p.green() - orange.green()) + abs(p.blue() - orange.blue()) > 60, (dx, dy)


# -- hover: the arrows and the x --

def test_the_arrows_and_the_x_fade_in_with_the_pointer_and_out_with_it(make):
    c = make()
    controls = c.named("tipsControls") or c.desk.item("tipsControls")
    assert controls.property("opacity") == 0 and not controls.isVisible()          # none shows at rest
    pt = c.card.mapToScene(QtCore.QPointF(150, 100)).toPoint()
    started = time.monotonic()
    QtTest.QTest.mouseMove(c.desk.win, pt)
    c.desk.pump(0.03)
    if early(started, 0.08):
        assert running_things(c.desk.win) == [FADE]                                # the 120 ms fade, then it is done
    c.desk.pump(0.4)
    assert controls.property("opacity") == 1 and controls.isVisible() and running_things(c.desk.win) == []
    prev, nxt, hide = (c.desk.item(n) for n in ("tipsPrev", "tipsNext", "tipsHide"))
    for ctl in (prev, nxt, hide):
        assert (ctl.width(), ctl.height()) == (24, 24)
    # muted icons, in a row at the top right: a chevron each way, then the x
    assert [color(ctl.childItems()[0], "color") for ctl in (prev, nxt, hide)] == [THEME["muted"]] * 3
    assert c.desk.at(hide)[0] + 24 == c.desk.at(c.card)[0] + 300 - 8 and c.desk.at(hide)[1] == c.desk.at(c.card)[1] + 8
    assert c.desk.at(prev)[0] < c.desk.at(nxt)[0] < c.desk.at(hide)[0]
    c.snap("tips-card-hover-1280x720")
    c.leave()
    assert controls.property("opacity") == 0 and not controls.isVisible()


def test_the_controls_send_the_turn_and_the_hide_and_nothing_else(make):
    c = make()
    c.hover()
    c.forget()
    c.click(c.desk.item("tipsPrev"))
    c.click(c.desk.item("tipsNext"))
    assert c.sent == [{"type": "tips", "op": "turn", "dir": -1}, {"type": "tips", "op": "turn", "dir": 1}]
    c.forget()
    c.click(c.desk.item("tipsHide"))
    # Off, at once, as the Widgets card's Off is: agentd's Desk.apply answers "Put Tips away. Say tips to see them
    # again." on the line, and a kept Tips is not first only "no longer kept" (which "hide tips" says)
    assert c.sent == [{"type": "desk", "op": "set", "widget": "tips", "state": "off"}]
    assert c.desk.pill.property("mode") == "idle"
    # the card changes only when agentd says so: nothing was turned or taken away by the shell itself
    assert c.desk.faces["tips"] == "full" and c.named("tipsLine").property("text") == "Say what you need. It gets made."
    c.desk.send(type="desk", hidden=["tips"])
    assert c.desk.faces["tips"] == "hidden"


def test_the_wheel_turns_tips_one_notch_at_a_time(make):
    c = make()
    c.hover()
    c.forget()
    c.wheel(-120)
    assert c.sent == [{"type": "tips", "op": "turn", "dir": 1}]
    c.wheel(120)
    assert c.sent[-1] == {"type": "tips", "op": "turn", "dir": -1} and len(c.sent) == 2
    c.forget()
    c.wheel(-40)
    c.wheel(-40)
    assert c.sent == []                                  # a touchpad's small steps add up to a notch
    c.wheel(-40)
    assert c.sent == [{"type": "tips", "op": "turn", "dir": 1}]
    c.forget()
    c.wheel(-80)
    c.wheel(120)                                         # a turn the other way starts over
    assert c.sent == [{"type": "tips", "op": "turn", "dir": -1}]
    c.forget()
    c.wheel(-80)
    c.leave()
    c.hover()
    c.wheel(-80)
    assert c.sent == []                                  # and so does leaving the card
    c.leave()
    c.wheel(-240, 600, 300)                              # a wheel over the wallpaper is not the card's
    assert c.sent == []


def test_the_card_turns_only_when_agentd_sends_the_next_one(make):
    c = make()
    c.hover()
    c.click(c.desk.item("tipsNext"))
    assert c.text("tipsLine") == "Say what you need. It gets made."          # a press only asks
    c.turn(1)
    c.desk.pump(0.4)
    assert c.text("tipsLine") == "Ask for a picture, not a paragraph."
    assert c.text("tipsWhy") == "Start here · 2 of 6" and c.text("tipsTitle") == "Tips"


# -- what the button, the title, the picture and All tips send --

def test_the_button_puts_the_words_in_the_pill_through_agentd_and_sends_nothing_else(make):
    c = make()
    c.forget()
    c.click(c.inside(c.front(), "tipsButton"))
    assert c.sent == [{"type": "summon", "text": "Make me a ticketing app", "tip": True, "id": "make-an-app"}]
    assert c.desk.win.property("pillSent") == [] or plain(c.desk.win.property("pillSent")) == []    # nothing runs on a click
    # the same for another tip, and for one whose words end in an ellipsis (the pill cuts it)
    c.turn(1)
    c.desk.pump(0.4)
    c.forget()
    c.click(c.inside(c.front(), "tipsButton"))
    assert c.sent == [{"type": "summon", "text": "How am I connected?", "tip": True, "id": "picture"}]


def test_the_title_the_picture_and_all_tips_open_the_app_on_this_tip(make):
    c = make()
    c.forget()
    c.click(c.named("tipsTitle"))
    assert c.sent == [{"type": "tips", "op": "open", "id": "make-an-app", "play": False}]
    c.forget()
    c.click(c.inside(c.front(), "tipsPicture"))
    assert c.sent == [{"type": "tips", "op": "open", "id": "make-an-app", "play": True}]
    c.forget()
    c.click(c.named("tipsAll"))
    assert c.sent == [{"type": "tips", "op": "open", "id": "make-an-app", "play": False}]
    c.turn(1)
    c.desk.pump(0.4)
    c.forget()
    c.click(c.inside(c.front(), "tipsPicture"))
    assert c.sent == [{"type": "tips", "op": "open", "id": "picture", "play": True}]


def test_a_press_for_a_tip_that_is_not_the_cards_sends_nothing(make):
    c = make()
    c.forget()
    c.desk.call("tipsTry", "picture")                    # the card is on another tip
    c.desk.call("tipsTry", "")
    assert c.sent == []
    c.desk.send(type="tips", present=False, card=None)
    c.desk.call("tipsTry", "make-an-app")                # the card is gone
    c.desk.call("tipsTurn", 1)
    assert c.sent == []


def test_a_tip_with_no_button_draws_its_marks_which_are_not_buttons(make):
    c = make(kept=True)
    seen = {}
    while len(seen) < 20:
        card = c.tips.message()["card"]
        if card["id"] in seen:
            break
        seen[card["id"]] = card
        c.turn(1)
    for tid, marks in (("esc", ["Esc"]), ("whyhere", ["where did this come from?"]), ("why", ["why"]),
                       ("undo", ["undo"])):
        c.show({"type": "tips", "present": True, "card": seen[tid]})
        c.desk.pump(0.4)
        face = c.front()
        assert not c.inside(face, "tipsButton"), tid
        row = c.inside(face, "tipsMarks")
        assert row is not None and row.isVisible(), tid
        drawn = [m for m in row.childItems() if m.objectName() == "tipsMark"]
        assert [m.childItems()[0].property("text") for m in drawn] == marks
        assert all(m.height() == 24 and m.property("color").alpha() == 0 for m in drawn)   # outlines, not raised
        assert c.desk.at(drawn[-1])[0] + drawn[-1].width() + 10 <= c.desk.at(c.named("tipsAll"))[0]
        c.forget()
        for m in drawn:
            c.click(m)
        assert c.sent == [], tid                                            # a mark is not a button
    c.snap("tips-card-marks-esc-1280x720")
    c.show({"type": "tips", "present": True, "card": seen["esc"]})
    c.desk.pump(0.4)
    keys = [m for m in c.inside(c.front(), "tipsMarks").childItems() if m.objectName() == "tipsMark"]
    assert keys[0].property("radius") == 6                                   # Esc is a key
    c.show({"type": "tips", "present": True, "card": seen["why"]})
    c.desk.pump(0.4)
    words = [m for m in c.inside(c.front(), "tipsMarks").childItems() if m.objectName() == "tipsMark"]
    assert words[0].property("radius") == 12                                 # a word is not


# -- every card tip of the real table --

def all_card_messages(tmp_path, facts=SAMPLE):
    t = Tips(path=tmp_path / "all.json", clock=lambda: date(2026, 11, 9), kept=lambda: True)
    t.facts = facts
    t.ready()
    out = {}
    for _ in range(60):
        msg = t.message()
        if msg["card"]["id"] in out:
            break
        out[msg["card"]["id"]] = msg
        t.turn(1)
    return out


def test_every_card_tip_of_the_real_table_renders_without_overflow(make, tmp_path):
    messages = all_card_messages(tmp_path)
    table = {r.id: r for r in tips_mod.read_table()}
    cards = [i for i, r in table.items() if r.card and tips_mod.backed(r, SAMPLE)]
    assert sorted(messages) == sorted(cards) and len(cards) == 17                  # the three app-only tips are the app's
    for width, height in SIZES:
        c = make(width, height)
        for tid, msg in messages.items():
            c.show(msg)
            c.desk.pump(0.35)
            face = c.front()
            card, line = c.card, c.inside(face, "tipsLine")
            assert (card.width(), card.height()) == (300, 290), tid
            assert line.property("implicitWidth") <= 272 and not line.property("truncated"), (tid, line.property("text"))
            assert c.text("tipsWhy") == msg["card"]["why"], tid
            button, marks = c.inside(face, "tipsButton"), c.inside(face, "tipsMarks")
            room = 272 - c.named("tipsAll").property("implicitWidth") - 10
            if msg["card"]["words"]:
                label = c.inside(button, "tipsButtonText")
                assert label.property("text") == msg["card"]["words"] and not label.property("truncated"), tid
                assert button.width() <= room and marks is None or not marks.isVisible(), tid
            else:
                assert not button and marks.isVisible(), tid
                assert sum(m.width() for m in marks.childItems()) + 6 * (len(marks.childItems()) - 1) <= room, tid
            assert c.desk.warnings == [], (tid, c.desk.warnings)
            c.snap(f"tips-card-{tid}-{width}x{height}")
        c.close()


def test_a_long_name_in_a_button_is_elided_and_never_overflows(make):
    longest = "M" * 18
    facts = Facts(ai="Claude", installed=True, app=longest, project="myapp", desk_seen=True, keep=True)
    c = make(kept=True, facts=facts)
    for _ in range(20):
        msg = c.tips.message()
        if msg["card"]["id"] == "change-app":
            break
        c.turn(1)
    assert msg["card"]["words"] == f"In {longest}, add…"
    c.show(msg)
    c.desk.pump(0.4)
    face = c.front()
    button = c.inside(face, "tipsButton")
    label = c.inside(button, "tipsButtonText")
    room = 272 - c.named("tipsAll").property("implicitWidth") - 10
    assert label.property("implicitWidth") + 26 > room                              # the test's premise: it does not fit
    assert label.property("truncated") and button.width() == room
    assert c.desk.at(button)[0] + button.width() + 10 <= c.desk.at(c.named("tipsAll"))[0] + 1     # (at() rounds)
    c.snap("tips-card-long-name-1280x720")
    # a press still sends the whole words, not the elided ones
    c.forget()
    c.click(button)
    assert c.sent[-1]["text"] == f"In {longest}, add…"


# -- the poster --

def test_the_picture_is_the_tips_poster_drawn_at_card_density(make):
    c = make()
    plain_img = c.image()
    row = dict(fx.BUILD, id="make-an-app", card=True, needs=[], tried_when=[], section="start", order=1)
    c.show({"type": "tips", "present": True,
            "card": {"id": "make-an-app", "line": row["line"], "words": row["words"], "marks": [],
                     "why": "Start here · 1 of 6", "index": 1, "count": 6, "tip": row}})
    c.desk.pump(0.4)
    with_beats = c.image()
    assert with_beats != plain_img                                                    # the payoff frame is drawn
    stage = c.all("stage")[0]
    assert stage.property("width") == 272 and stage.property("height") == 153
    assert running_things(c.desk.win) == []                                           # and still: it never plays
    c.snap("tips-card-poster-build-1280x720")
    for fixture in (fx.PICTURE, fx.ESC, fx.TIMER, fx.MACHINE, fx.DISK):
        c.show({"type": "tips", "present": True,
                "card": {"id": fixture["id"], "line": fixture["line"], "words": fixture["words"],
                         "marks": fixture["marks"], "why": "Start here · 2 of 6", "index": 2, "count": 6,
                         "tip": fixture}})
        c.desk.pump(0.4)
        assert c.desk.warnings == []
        c.snap(f"tips-card-poster-{fixture['id']}-1280x720")


# -- what moves: the crossfade, and only that --

def test_turning_a_tip_crossfades_the_picture_the_line_and_the_button(make):
    c = make()
    first = c.front()
    assert first.objectName() == "tipsFaceA"
    started = time.monotonic()
    c.turn(1)                                                                         # (send pumps 50 ms)
    if early(started, 0.12):
        assert running_things(c.desk.win) == [FADE, FADE]                             # one face out, one face in
    c.desk.pump(0.03)
    a, b = c.desk.item("tipsFaceA"), c.desk.item("tipsFaceB")
    if early(started, 0.15):                                                          # (the fade is 200 ms)
        assert a.isVisible() and b.isVisible() and 0 < a.property("opacity") < 1 and 0 < b.property("opacity") < 1
    c.desk.pump(0.4)
    assert running_things(c.desk.win) == []
    assert not a.isVisible() and b.isVisible() and b.property("opacity") == 1         # the old face is gone, not hidden under
    assert c.inside(b, "tipsLine").property("text") == "Ask for a picture, not a paragraph."
    assert c.inside(b, "tipsButtonText").property("text") == "How am I connected?"
    # the title is the same card throughout, and a turn back crosses over the other way
    c.turn(-1)
    c.desk.pump(0.4)
    assert c.desk.item("tipsFaceA").isVisible() and not c.desk.item("tipsFaceB").isVisible()
    assert c.text("tipsLine") == "Say what you need. It gets made."
    assert c.card.height() == 290


def test_the_same_tip_saying_something_new_changes_in_place_without_a_crossfade(make):
    c = make()
    msg = c.tips.message()
    msg["card"] = dict(msg["card"], why="Start here · 1 of 5", count=5)
    c.show(msg)
    c.desk.app.processEvents()
    assert running_things(c.desk.win) == []
    assert c.text("tipsWhy") == "Start here · 1 of 5" and c.front().objectName() == "tipsFaceA"


def test_under_reduced_motion_the_hover_and_the_turn_are_instant(make):
    c = make()
    c.theme().setProperty("reducedMotion", True)
    controls = c.desk.item("tipsControls")
    pt = c.card.mapToScene(QtCore.QPointF(150, 100)).toPoint()
    QtTest.QTest.mouseMove(c.desk.win, pt)
    c.desk.app.processEvents()
    assert controls.property("opacity") == 1 and running_things(c.desk.win) == []
    c.turn(1)
    c.desk.app.processEvents()
    a, b = c.desk.item("tipsFaceA"), c.desk.item("tipsFaceB")
    assert running_things(c.desk.win) == [] and not a.isVisible() and b.property("opacity") == 1
    c.leave()
    assert controls.property("opacity") == 0
    c.theme().setProperty("reducedMotion", False)


def test_the_card_loads_and_works_with_no_qml_warnings(make):
    c = make(kept=True)
    c.hover()
    for _ in range(4):
        c.turn(1)
        c.wheel(-120)
    c.desk.cover((1000, 300, 280, 400))                        # a window over it: it folds
    c.desk.pump(0.3)
    c.desk.cover()
    c.desk.pump(0.6)
    c.desk.send(type="tips", present=False, card=None)
    c.desk.send(type="tips", present=True, card={"id": "x"})   # no line: no card
    c.desk.call("lost")
    assert c.desk.warnings == []


# -- when the card is there: agentd says so, setup is over, and the shell is calm --

def setup(c, state, line="", **kw):
    c.desk.send(type="setup", state=state, line=line, tone=kw.pop("tone", "step"), actions=[], **kw)


def test_the_card_waits_for_setup_to_be_ready_and_goes_when_it_is_not(make):
    c = make(up=False)
    c.show()                                                  # agentd has something to say, setup has not started
    assert c.desk.prop("tipsModel") is not None and c.desk.faces["tips"] == "hidden"
    for state in ("choose", "checking", "signed_out", "offline", "signing_in"):
        setup(c, state, "Sign in to Claude.")
        assert c.desk.faces["tips"] == "hidden" and not c.desk.prop("present")["tips"], state
    c.desk.send(type="status", busy=False, provider="claude", queue=[])
    setup(c, "ready")
    assert c.desk.faces["tips"] == "full" and c.desk.slots["tips"]["h"] == 290
    setup(c, "signed_out", "Sign in to Claude.")              # signed out again: the card goes, it is not in the way
    assert c.desk.faces["tips"] == "hidden"
    setup(c, "ready")
    assert c.desk.faces["tips"] == "full"


def test_the_ai_resting_neither_brings_the_card_nor_takes_it_away(make):
    c = make()
    rest = {"provider": "claude", "why": "limit", "kind": "limit", "until": "15:00", "when": 0, "hint": "", "note": ""}
    setup(c, "resting", "Claude is resting. Asks wait.", rest=rest)
    c.desk.pump(0.3)
    assert c.desk.faces["tips"] == "full"                                # it was up: it stays up through the rest
    setup(c, "ready")
    assert c.desk.faces["tips"] == "full"
    # and a desk that first hears of the rest has the card too, once the line it said has faded
    d = make(up=False)
    d.desk.send(type="status", busy=False, provider="claude", queue=[])
    setup(d, "resting", "Claude is resting. Asks wait.", rest=rest)
    d.show()
    assert d.desk.pill.property("mode") == "resting" and d.desk.faces["tips"] == "hidden"
    d.desk.pill_call("dismiss")
    assert d.desk.pill.property("mode") == "idle" and d.desk.faces["tips"] == "full"


def test_the_card_never_first_appears_during_a_turn_or_under_a_line_and_stays_once_it_is_up(make):
    c = make(up=False)
    c.desk.send(type="status", busy=False, provider="claude", queue=[])
    setup(c, "signing_in", "Waiting for the browser.")
    c.desk.turn(1, prompt="make a ticketing app")             # typed during sign-in: the turn runs, the card waits
    c.show()
    setup(c, "ready", "Signed in to Claude. Ask me for anything.")
    assert c.desk.faces["tips"] == "hidden"
    c.desk.end(1)
    assert c.desk.pill.property("mode") == "closing" and c.desk.faces["tips"] == "hidden"
    c.desk.pill_call("dismiss")                               # the closing line fades: 12 s after the turn ends
    assert c.desk.pill.property("mode") == "idle" and c.desk.faces["tips"] == "full"
    c.desk.pump(0.4)
    # up: a turn, its closing line and the ready line do not take it away
    c.desk.turn(2, prompt="install ffmpeg", steps=[("Find it", True, "running"), ("Install it", False, "pending")])
    assert c.desk.faces["tips"] == "full" and c.desk.shown("deskCard-now")
    c.desk.end(2)
    assert c.desk.faces["tips"] == "full"
    c.desk.pill_call("dismiss")
    assert c.desk.faces["tips"] == "full"


def test_agentd_taking_the_card_away_or_the_person_putting_it_away_removes_it_and_it_returns_when_calm(make):
    c = make()
    assert c.desk.faces["tips"] == "full"
    c.desk.send(type="tips", present=False, card=None)
    assert c.desk.faces["tips"] == "hidden" and "tips" not in c.desk.slots and c.desk.prop("rightStrips") == []
    c.desk.turn(1)                                            # agentd has it back in the middle of a turn: it waits
    c.show()
    assert c.desk.faces["tips"] == "hidden"
    c.desk.end(1)
    c.desk.pill_call("dismiss")
    assert c.desk.faces["tips"] == "full"
    c.desk.send(type="desk", hidden=["tips"])                 # "put Tips away"
    assert c.desk.faces["tips"] == "hidden" and c.desk.prop("tipsModel") is not None
    c.desk.pump(0.4)
    assert not c.desk.shown("deskCard-tips") and not c.desk.shown("deskStrip-tips")
    c.desk.send(type="desk", hidden=[])                       # "tips" again
    assert c.desk.faces["tips"] == "full"


def test_the_card_is_gone_when_agentd_is_and_back_when_agentd_says_it_again(make):
    c = make()
    c.desk.call("lost")
    assert c.desk.prop("tipsModel") is None and c.desk.faces["tips"] == "hidden"
    c.desk.pump(0.4)
    assert not c.desk.shown("deskCard-tips") and c.desk.warnings == []
    c.show()                                                  # agentd tells a client that connects
    assert c.desk.faces["tips"] == "full"


def test_windows_fold_the_card_to_its_strip_full_screen_hides_it_and_the_strip_opens_the_app(make):
    c = make(1280, 720)
    slot = c.desk.slots["tips"]
    c.desk.cover((slot["x"] + 20, slot["y"] + 20, 100, 100))
    assert c.desk.faces["tips"] == "strip"
    assert [(s["id"], s["text"]) for s in c.desk.prop("rightStrips")] == [("tips", "tips")]
    c.desk.pump(0.6)
    strip = c.desk.item("deskStrip-tips")
    assert strip.isVisible() and not c.desk.shown("deskCard-tips")
    c.forget()
    c.click(strip)
    assert c.sent == [{"type": "tips", "op": "open", "id": "", "play": False}]       # the app on the first tip
    c.desk.cover()
    c.desk.pump(0.6)                                          # the strip is a card again after 400 ms
    assert c.desk.faces["tips"] == "full"
    c.desk.cover((0, 0, 1280, 720, True))
    assert c.desk.faces["tips"] == "hidden" and c.desk.prop("capsule")
    c.desk.cover()
    c.desk.pump(0.6)
    assert c.desk.faces["tips"] == "full"
    assert c.desk.warnings == []


def test_a_narrow_screen_keeps_the_card_as_a_strip_and_the_card_is_never_under_the_pills_line(make):
    narrow = make(1000, 700)
    assert narrow.desk.prop("narrow") and narrow.desk.faces["tips"] == "strip"
    wide = make(1280, 720)
    assert not wide.desk.prop("narrow") and wide.desk.faces["tips"] == "full"
    # the pill gives the card its width: the line above it never covers the card (stageRoom counts a full rail)
    assert wide.desk.prop("pillTarget") <= 1280 - 2 * (300 + 16 + 12)
    wide.desk.send(type="desk", hidden=["tips"])
    assert wide.desk.prop("pillTarget") >= 1280 - 2 * (300 + 16 + 12)


def test_tips_is_the_top_of_the_right_rail_and_keeps_its_size_when_the_rest_of_the_desk_comes(make):
    c = make(1920, 1080)
    before = c.desk.slots["tips"]
    assert before["y"] + 290 == c.desk.prop("railBottomY")                       # alone, it is the nearest the pill
    c.desk.vitals()                                          # the machine and the away cards come under it
    c.desk.back()
    assert c.desk.faces["machine"] == "full" and c.desk.faces["away"] == "full" and c.desk.faces["tips"] == "full"
    slots = c.desk.slots
    assert slots["tips"]["y"] + 290 <= slots["machine"]["y"] < slots["machine"]["y"] + slots["machine"]["h"]
    assert slots["machine"]["y"] + slots["machine"]["h"] <= slots["away"]["y"]       # the top of the rail: bottom up
    assert slots["away"]["y"] + slots["away"]["h"] == c.desk.prop("railBottomY")
    assert (slots["tips"]["x"], slots["tips"]["w"], slots["tips"]["h"]) == (before["x"], before["w"], 290)


def test_the_snapshot_says_what_the_card_holds(make):
    c = make()
    snap = c.desk.call("snapshot")
    assert snap["tips"]["id"] == "make-an-app" and snap["tips"]["words"] == "Make me a ticketing app"
    c.desk.call("lost")
    assert c.desk.call("snapshot")["tips"] is None


# -- what the message may carry --

def test_a_message_that_is_not_a_card_takes_the_card_away_and_garbage_never_breaks_it(make):
    c = make()
    for bad in (None, {}, {"id": "x"}, {"line": "y"}, {"id": 3, "line": "y"}, "card", [1], {"id": "a", "line": ""}):
        c.desk.send(type="tips", present=True, card=bad)
        assert c.desk.prop("tipsModel") is None and c.desk.faces["tips"] == "hidden", bad
    c.desk.send(type="tips", present=False, card=SAMPLE_CARD)
    assert c.desk.prop("tipsModel") is None
    c.desk.send(type="tips", present=True, card=SAMPLE_CARD)
    model = c.desk.prop("tipsModel")
    assert model["id"] == "t" and model["marks"] == ["a", "b", "c"] and model["index"] == 0 and model["count"] == 0
    assert model["tip"]["beats"] == [] and model["tip"]["captions"] == []
    assert c.desk.faces["tips"] == "full" and c.desk.warnings == []
    # the app's list is for the app's own connection: it never changes the card
    c.desk.send(type="tips", list=[{"id": "other"}])
    assert c.desk.prop("tipsModel")["id"] == "t"
    # an identical message changes nothing: the model is not set again
    c.desk.win.setProperty("tipsChanges", 0)
    c.desk.send(type="tips", present=True, card=SAMPLE_CARD)
    assert c.desk.win.property("tipsChanges") == 0


SAMPLE_CARD = {"id": "t", "line": "A line.", "words": "", "marks": ["a", "b", "c", "d", 5, ""], "why": "Start here · 1 of 6"}


def test_the_source_keeps_the_rules_the_guard_cannot_see():
    """No page dots, no timer and no loop, and every duration is the Theme's (tests/test_motion.py holds the rest)."""
    source = (SHELL / "TipsCard.qml").read_text()
    assert [d.strip() for d in re.findall(r"duration:\s*([^\n}]+)", source)] == ["Kit.Theme.normal", "Kit.Theme.fast"]
    assert "Repeater" in source and source.count("Repeater") == 1                     # the marks, nothing like dots
    assert "pageIndicator" not in source.lower() and "PageIndicator" not in source
    assert "interval" not in source


# -- a Tips the person keeps (desk.py: Tips is a calm widget; docs/TIPS.md "A kept Tips card") --
# Agentd decides what Tips has to say, from the first week and the desk's `kept`; the shell draws what it says
# with the rules it always has: setup over, calm at its first appearance, and nothing over the persona card.

def test_a_kept_card_after_the_first_week_is_the_same_still_card_and_leaves_when_it_is_no_longer_kept(make):
    c = make()
    c.end_the_first_week()
    c.show()                                                  # agentd: nothing more to say to a new person
    assert c.desk.faces["tips"] == "hidden" and c.desk.prop("tipsModel") is None
    snap = c.keep(("keep", "tips"))                           # the words "keep tips", or Keep on the Widgets card
    assert snap["kept"] == ["tips"] and c.desk.prop("kept") == ["tips"]
    assert c.desk.faces["tips"] == "full" and c.desk.slots["tips"]["h"] == 290           # the same card, as ever
    assert c.text("tipsLine") == "Say what you need. It gets made."          # the first tip, as it was at the start
    assert c.text("tipsWhy") == "Start here · 1 of 6"
    c.desk.pump(0.5)
    assert running_things(c.desk.win) == []                    # and as still: nothing runs at rest
    c.keep(("set", "tips", None, None, "needed"))             # "hide tips" says no longer kept, and so does Keep -> When needed
    assert c.desk.prop("kept") == [] and c.desk.faces["tips"] == "hidden"
    assert c.desk.warnings == []


def test_a_kept_card_turns_through_the_other_tips_and_draws_each_without_overflow(make):
    c = make(kept=True)
    c.end_the_first_week()
    c.show()
    seen = []
    for _ in range(40):
        seen.append((c.text("tipsWhy"), c.text("tipsLine")))
        c.turn(1)
        c.desk.pump(0.3)
        if c.text("tipsLine") == seen[0][1]:
            break
    sections = {why.split(" · ")[0] for why, _ in seen}
    assert {"Start here", "Work", "This machine"} <= sections    # Start here, then the others whose button fits
    assert len({line for _, line in seen}) == len(seen) >= 12    # each once, round the card
    assert c.desk.warnings == []


def test_a_kept_card_never_first_appears_during_a_turn_or_under_a_line_and_stays_once_it_is_up(make):
    c = make(up=False, kept=True)
    c.end_the_first_week()
    c.desk.send(type="status", busy=False, provider="claude", queue=[])
    setup(c, "signing_in", "Waiting for the browser.")
    c.desk.turn(1, prompt="make a ticketing app")             # typed during sign-in: the turn runs, the card waits
    c.show()
    setup(c, "ready", "Signed in to Claude. Ask me for anything.")
    assert c.desk.prop("tipsModel") is not None and c.desk.faces["tips"] == "hidden"
    c.desk.end(1)
    assert c.desk.pill.property("mode") == "closing" and c.desk.faces["tips"] == "hidden"
    c.desk.pill_call("dismiss")                               # the closing line fades
    assert c.desk.pill.property("mode") == "idle" and c.desk.faces["tips"] == "full"
    c.desk.pump(0.4)
    c.desk.turn(2, prompt="install ffmpeg", steps=[("Find it", True, "running"), ("Install it", False, "pending")])
    assert c.desk.faces["tips"] == "full"                     # up: a turn does not take it away
    c.desk.end(2)
    c.desk.pill_call("dismiss")
    assert c.desk.faces["tips"] == "full"


def test_a_kept_card_waits_for_the_answer_to_keep_tips_to_fade(make):
    c = make()
    c.end_the_first_week()
    c.show()
    # the words are a launcher answer: the line "Tips stays on the desk." is on the pill until it fades
    c.desk.send(kind="local", turn=None, action="desk", target="tips", phase="done", ok=True,
                text="Tips stays on the desk.")
    assert c.desk.pill.property("mode") == "local"
    c.keep(("keep", "tips"))
    assert c.desk.faces["tips"] == "hidden"
    c.desk.pill_call("dismiss")
    assert c.desk.pill.property("mode") == "idle" and c.desk.faces["tips"] == "full"


def test_a_persona_card_holds_back_the_card_kept_or_not_and_the_card_comes_when_it_is_answered(make):
    for kept in (False, True):
        c = make(up=False, kept=kept)
        if kept:
            c.end_the_first_week()
        c.desk.send(type="status", busy=False, provider="claude", queue=[])
        c.desk.send(type="persona_ask", line="What should I call you?", name="", voice="", current=False, voices=[])
        setup(c, "ready")
        c.show()
        assert c.desk.prop("tipsModel") is not None and c.desk.faces["tips"] == "hidden", kept
        c.desk.pill_call("personaAnswer", "Ada", "")
        assert c.desk.faces["tips"] == "full", kept


def test_the_x_on_a_kept_card_puts_it_away_at_once_and_keep_brings_it_back(make):
    c = make(kept=True)
    c.keep(("keep", "tips"))
    c.end_the_first_week()
    c.hover()
    c.forget()
    c.click(c.desk.item("tipsHide"))
    assert c.sent == [{"type": "desk", "op": "set", "widget": "tips", "state": "off"}]
    # what agentd's desk does with it: one press, the leap (the word "hide tips" would only stop keeping it)
    ok, said = c.desk_py.apply("set", "tips", state="off")
    assert (ok, said) == (True, "Put Tips away. Say tips to see them again.")
    snap = c.keep()
    assert snap["hidden"] == ["alive", "tips"] and snap["kept"] == [] and c.desk.faces["tips"] == "hidden"
    snap = c.keep(("keep", "tips"))                           # "keep tips" brings it back, for good
    assert snap["hidden"] == ["alive"] and snap["kept"] == ["tips"] and c.desk.faces["tips"] == "full"


def test_a_kept_card_folds_to_its_strip_under_a_window_and_the_strip_opens_the_app(make):
    c = make()
    c.end_the_first_week()
    c.keep(("keep", "tips"))
    slot = c.desk.slots["tips"]
    c.desk.cover((slot["x"] + 20, slot["y"] + 20, 100, 100))
    assert c.desk.faces["tips"] == "strip"
    c.desk.pump(0.6)
    c.forget()
    c.click(c.desk.item("deskStrip-tips"))
    assert c.sent == [{"type": "tips", "op": "open", "id": "", "play": False}]
    c.desk.cover()
    c.desk.pump(0.6)
    assert c.desk.faces["tips"] == "full" and c.desk.warnings == []

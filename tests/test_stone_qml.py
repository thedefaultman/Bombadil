"""The stone (Kit.Stone, share/qml/Bombadil/Stone.qml): Bombadil's mark, drawn and animated offscreen.

Each face is rendered at 1x on a flat ground and read back pixel by pixel. Set BOMBADIL_SCREENS=<dir>
to save a picture of each face at 8x.
"""

import math
import os
import re
import time
from pathlib import Path

import pytest
from qml_running import running_things
from qml_theme import THEME

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "share" / "qml" / "Bombadil"
GROUND = "#101214"      # what the window behind the stone is
CUT = "#ff00ff"         # the b's colour in these tests, so it cannot be mistaken for anything else

HARNESS = """
import QtQuick
import "%s" as Kit

Window {
    width: 24 + 2 * %d; height: 24 + 2 * %d; visible: true; color: "%s"
    Kit.Stone { id: stone; objectName: "stone"; x: %d; y: %d; ground: "%s"; settle: %d; %s }
}
"""


class Mark:
    def __init__(self, app, tmp_path, ground=CUT, settle=0, pad=0, extra=""):
        # pad: ground left around the 24 px slot, to see what the stone draws outside it. extra: more
        # properties for the stone, as QML ("animate: false; small: true").
        qml = tmp_path / "stone.qml"
        qml.write_text(HARNESS % (KIT.as_uri(), pad, pad, GROUND, pad, pad, ground, settle, extra))
        self.app = app
        self.engine = QtQml.QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        self.stone = self.win.findChild(QtCore.QObject, "stone")
        self.pump()

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.005)

    def set(self, **props):
        for k, v in props.items():
            self.stone.setProperty(k, v)
        self.pump()

    def image(self):
        self.pump(0.02)
        return self.win.grabWindow().convertToFormat(QtGui.QImage.Format_RGB32)

    def at(self, x, y):
        return self.image().pixelColor(x, y).name()

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            self.image().scaled(192, 192, QtCore.Qt.IgnoreAspectRatio).save(str(Path(out) / f"stone-{name}.png"))


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


@pytest.fixture
def mark(app, tmp_path):
    m = Mark(app, tmp_path)
    yield m
    m.win.close()
    m.engine.deleteLater()
    m.pump()


def near(a, b, tol=12):
    qa, qb = QtGui.QColor(a), QtGui.QColor(b)
    return max(abs(qa.red() - qb.red()), abs(qa.green() - qb.green()), abs(qa.blue() - qb.blue())) <= tol


def silhouette(img):
    return {(x, y) for x in range(img.width()) for y in range(img.height()) if not near(img.pixelColor(x, y).name(), GROUND, 6)}


def cut_pixels(img):
    return {(x, y) for x in range(img.width()) for y in range(img.height()) if near(img.pixelColor(x, y).name(), CUT, 6)}


def test_rest_is_a_green_stone_with_a_b_cut_through_it(mark):
    assert mark.warnings == []
    img = mark.image()
    assert near(img.pixelColor(12, 8).name(), THEME["good"])          # above the b's bar
    assert near(img.pixelColor(8, 12).name(), CUT)                    # the b's stem
    assert near(img.pixelColor(1, 1).name(), GROUND)                  # outside the stone
    assert near(img.pixelColor(22, 22).name(), GROUND)
    mark.snap("rest")


def test_the_b_stays_inside_the_stone_in_every_pose(mark):
    # The cut is drawn in the ground's colour on top of the stone, so a b that reached the edge would
    # show as cut pixels touching the window's ground. The stone turns about its centroid and the b
    # stays still: none of these poses may let them meet.
    for turn in (0, 17, 45, 60, 90, 120):
        mark.set(face="rest", turn=turn)
        img = mark.image()
        cut = cut_pixels(img)
        assert len(cut) > 15
        for x, y in cut:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    assert near(img.pixelColor(x + dx, y + dy).name(), GROUND, 6) is False, (turn, x, y)


def test_a_third_of_a_turn_leaves_the_stone_where_it_was(mark):
    # The roll ends on a pose that looks like where it began, so the next beat starts unseen. That
    # only holds if the pivot is the stone's centroid, (12, 12.975).
    mark.set(face="rest")
    before = silhouette(mark.image())
    mark.set(turn=120)
    after = silhouette(mark.image())
    assert len(before ^ after) <= 8, len(before ^ after)


def test_working_is_orange_and_rolls_while_the_b_holds_still(mark):
    mark.set(face="working")
    assert near(mark.at(12, 8), THEME["accent"])
    silhouettes = set()
    for _ in range(25):                 # one full beat is a second
        mark.pump(0.04)
        img = mark.image()
        assert near(img.pixelColor(8, 12).name(), CUT)        # the b's stem never moves
        silhouettes.add(frozenset(silhouette(img)))
    assert len(silhouettes) > 3         # the stone did turn
    mark.snap("working")


def test_the_first_frames_keep_the_resting_green_then_the_face_shows(app, tmp_path):
    # The scene graph drops a colour change made in the first frames after the bar starts (the VM's
    # stone stayed orange at rest). So the stone is painted resting green for the first 400 ms
    # whatever the face is, then follows it: a starting face that settles to rest never shows a
    # colour change, and one that is still starting turns orange afterwards.
    m = Mark(app, tmp_path, settle=400)
    try:
        m.set(face="starting")
        assert near(m.at(12, 8), THEME["good"])
        m.set(face="rest")
        m.pump(0.5)
        assert near(m.at(12, 8), THEME["good"])
        m.set(face="starting")
        assert near(m.at(12, 8), THEME["accent"])
    finally:
        m.win.close()
        m.engine.deleteLater()
        m.pump()


def test_a_face_that_waits_is_amber_once_the_stone_has_settled(app, tmp_path):
    m = Mark(app, tmp_path, settle=400)
    try:
        m.set(face="needs")
        assert near(m.at(12, 8), THEME["good"])
        m.pump(0.5)
        assert near(m.at(12, 8), THEME["warn"])
    finally:
        m.win.close()
        m.engine.deleteLater()
        m.pump()


def test_starting_rolls_like_working(mark):
    mark.set(face="starting")
    assert near(mark.at(12, 8), THEME["accent"])
    seen = {mark.stone.property("turn") for _ in range(20) if not mark.pump(0.05)}
    assert len(seen) > 2


def test_reduced_motion_pulses_instead_of_rolling(mark):
    mark.set(face="working", reducedMotion=True)
    turns = set()
    for _ in range(14):
        mark.pump(0.06)
        turns.add(mark.stone.property("turn"))
    assert turns == {0.0}
    colours = {mark.at(12, 8) for _ in range(12) if not mark.pump(0.06)}
    assert len(colours) > 2             # the stone's colour fades and comes back


def test_listening_leans_toward_the_text_and_stays(mark):
    mark.set(face="rest")
    rest = silhouette(mark.image())
    mark.set(face="listening")
    mark.pump(0.5)
    assert abs(mark.stone.property("lean") - 6) < 0.01
    leaning = silhouette(mark.image())
    assert leaning != rest and near(mark.at(12, 8), THEME["good"])
    mark.pump(0.4)
    assert silhouette(mark.image()) == leaning
    mark.snap("listening")
    mark.set(face="rest")
    mark.pump(0.5)
    assert silhouette(mark.image()) == rest


def test_needs_you_is_amber_with_a_glow_and_two_knocks(mark):
    mark.set(face="needs")
    assert near(mark.at(12, 8), THEME["warn"])
    knocks, glows = [], set()
    end = time.monotonic() + 1.55       # one beat is 1.6 s: by the clock, so a slow machine still samples one
    while time.monotonic() < end:
        mark.pump(0.04)
        knocks.append(mark.stone.property("knock"))
        glows.add(round(mark.stone.property("glowOpacity"), 1))
    peaks = sum(1 for a, b, c in zip(knocks, knocks[1:], knocks[2:]) if b > a and b >= c and b > 4)
    assert peaks == 2, knocks
    assert max(knocks) <= 8.01
    assert max(glows) >= 0.5 and min(glows) <= 0.3
    # The glow is a fade around the stone, not a ring: a pixel just outside the stone is tinted,
    # and a pixel far from it is not.
    mark.set(face="needs", reducedMotion=True)    # held still at 0.45
    outside = QtGui.QColor(mark.at(2, 13))
    assert outside.red() > QtGui.QColor(GROUND).red() + 10 and outside.red() > outside.blue()
    assert not near(mark.at(0, 13), GROUND, 6)            # and it reaches the edge of the slot
    mark.snap("needs")


def test_the_glow_reaches_past_the_slot_and_fades_out(app, tmp_path):
    # At 24 px the old glow (radius 11) was a 1 px halo that did not read. It now spreads into the
    # pill's padding, 5 px past the stone, and is gone by 16 px from the stone's centre.
    m = Mark(app, tmp_path, pad=8)
    try:
        m.set(face="needs", reducedMotion=True)
        img = m.image()
        def lit(x, y):
            c = img.pixelColor(x, y)
            return c.red() > QtGui.QColor(GROUND).red() + 8 and c.red() > c.blue()
        assert lit(8 - 3, 8 + 13)                        # 3 px left of the slot, level with the b
        assert lit(8 + 12, 8 + 24 + 2)                   # 2 px under the slot
        assert near(img.pixelColor(2, 8 + 13).name(), GROUND, 3)      # 6 px out: gone
        assert near(img.pixelColor(8 + 12, 8 + 24 + 6).name(), GROUND, 3)
    finally:
        m.win.close()
        m.engine.deleteLater()
        m.pump()


def test_done_hops_once_then_sits_green(mark):
    mark.set(face="rest")
    base = mark.image()
    mark.set(face="done")
    moved = False
    for _ in range(24):
        mark.pump(0.03)
        moved = moved or silhouette(mark.image()) != silhouette(base)
    assert moved
    mark.pump(0.4)
    assert silhouette(mark.image()) == silhouette(base)
    assert near(mark.at(12, 8), THEME["good"])
    assert (mark.stone.property("hopSx"), mark.stone.property("hopSy"), mark.stone.property("hopY")) == (1.0, 1.0, 0.0)


def test_stopped_is_the_stop_buttons_grey_square(mark):
    mark.set(face="stopped")
    img = mark.image()
    assert near(img.pixelColor(12, 12).name(), THEME["muted"])
    assert near(img.pixelColor(7, 7).name(), THEME["muted"])
    assert near(img.pixelColor(2, 2).name(), GROUND) and near(img.pixelColor(21, 21).name(), GROUND)
    assert not cut_pixels(img)           # no stone, no b
    mark.snap("stopped")


def hues(img, tone, tol=30):
    return {(x, y) for x in range(img.width()) for y in range(img.height()) if near(img.pixelColor(x, y).name(), tone, tol)}


def test_resting_is_a_whole_grey_outline_over_a_faint_fill_with_the_b_cut_in(mark):
    mark.set(face="resting")
    img = mark.image()
    # Grey and hollow: not the stone's green, and none of the colours that mean something else.
    for tone in ("good", "warn", "accent", "bad"):
        assert not hues(img, THEME[tone]), tone
    inside = img.pixelColor(12, 8)
    assert not near(inside.name(), GROUND, 6) and not near(inside.name(), THEME["muted"], 12)   # a fill, fainter than the line
    assert max(inside.red(), inside.green(), inside.blue()) - min(inside.red(), inside.green(), inside.blue()) < 12
    assert len(hues(img, THEME["muted"], 40)) > 60                     # the outline
    # It keeps rest's 24 px grid: nothing outside the stone's own silhouette.
    mark.set(face="rest")
    assert len(silhouette(img) - silhouette(mark.image())) <= 4
    # The b is cut in the ground's colour, as on the other faces (here the test's magenta).
    assert near(img.pixelColor(8, 12).name(), CUT) and len(cut_pixels(img)) > 15
    mark.set(face="resting")
    mark.snap("resting")


def test_resting_outline_is_unbroken_and_the_stone_stands_still(mark):
    mark.set(face="resting")
    img = mark.image()
    outline = hues(img, THEME["muted"], 40)
    # Flood the outside from a corner without crossing the outline: it must not reach the inside.
    seen, todo = set(), [(0, 0)]
    while todo:
        x, y = todo.pop()
        if (x, y) in seen or (x, y) in outline or not (0 <= x < 24 and 0 <= y < 24):
            continue
        seen.add((x, y))
        todo += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    assert (12, 8) not in seen and (12, 16) not in seen and (6, 14) not in seen
    # Still: no roll, no knock, no lean, no hop, and nothing moves meanwhile.
    mark.pump(0.7)
    assert mark.image() == img
    assert [mark.stone.property(n) for n in ("turn", "knock", "lean", "hopY")] == [0.0, 0.0, 0.0, 0.0]
    assert mark.stone.property("rolling") is False


def test_resting_is_not_any_other_face(mark):
    shots = {}
    for face in ("rest", "offline", "stopped", "needs", "working", "resting"):
        mark.set(face=face, reducedMotion=True)       # held still, so the pictures can be compared
        shots[face] = mark.image()

    def differ(a, b):
        return sum(1 for x in range(24) for y in range(24) if not near(a.pixelColor(x, y).name(), b.pixelColor(x, y).name(), 12))

    for other in ("rest", "offline", "stopped", "needs", "working"):
        assert differ(shots["resting"], shots[other]) > 40, other


def test_offline_is_a_broken_red_outline_and_the_b_stays(mark):
    mark.set(face="offline")
    img = mark.image()
    assert near(img.pixelColor(11, 8).name(), GROUND)                # the stone is gone: hollow
    assert near(img.pixelColor(12, 12).name(), GROUND) or near(img.pixelColor(12, 12).name(), THEME["bad"])
    red = [(x, y) for x in range(24) for y in range(24) if near(img.pixelColor(x, y).name(), THEME["bad"], 30)]
    assert len(red) > 40
    assert not cut_pixels(img)
    # Six dashes: walking the outline's pixels, the ring is broken (there are gaps with no red).
    outline = [(x, y) for x, y in red if not (7 <= x <= 18 and 7 <= y <= 18)]
    assert 0 < len(outline) < 120
    mark.snap("offline")


# -- the stone in the kit: animate, ink and the 16 px drawing --

def build(app, tmp_path, extra, **kw):
    m = Mark(app, tmp_path, extra=extra, **kw)
    assert m.warnings == []
    return m


def close(m):
    m.win.close()
    m.engine.deleteLater()
    m.pump()


def test_animate_off_paints_the_face_at_once_and_runs_nothing(app, tmp_path):
    # `animate` gates every animation and the settle timer. With a 400 ms settle, an animated stone is
    # painted resting green first (the VM's workaround); one that does not animate shows its face's colour
    # in the first frame, because there is no first frame to lose.
    m = build(app, tmp_path, "animate: false", settle=400)
    try:
        for face, tone in (("working", "accent"), ("needs", "warn"), ("starting", "accent"), ("rest", "good")):
            m.set(face=face)
            assert near(m.at(12, 8), THEME[tone]), face
        assert running_things(m.win) == []
        for face in ("working", "needs", "done", "listening", "rest"):
            m.set(face=face)
            assert running_things(m.win) == [], face
        m.set(face="working")
        before = silhouette(m.image())
        m.pump(0.7)
        assert silhouette(m.image()) == before
        assert [m.stone.property(n) for n in ("turn", "knock", "hopY")] == [0.0, 0.0, 0.0]
    finally:
        close(m)


def test_animate_on_still_runs_and_turning_it_off_stops_everything(app, tmp_path):
    m = build(app, tmp_path, "face: \"needs\"", settle=400)
    try:
        assert running_things(m.win), "an animated needs stone has its timer and its knocks"
        assert "QQmlTimer" in running_things(m.win)
        m.set(animate=False)
        assert running_things(m.win) == []
        assert near(m.at(12, 8), THEME["warn"])
    finally:
        close(m)


def test_animate_off_leans_and_holds_the_glow_without_easing(app, tmp_path):
    m = build(app, tmp_path, "animate: false")
    try:
        m.set(face="listening")
        assert m.stone.property("lean") == 6          # no 240 ms ease: it is there in the first frame
        m.set(face="needs")
        assert abs(m.stone.property("glowOpacity") - 0.45) < 1e-9     # the held glow, where reduced motion rests it
        glow = QtGui.QColor(m.at(2, 13))
        assert glow.red() > QtGui.QColor(GROUND).red() + 10 and glow.red() > glow.blue()
        # A host that draws it a frame at a time writes the pose itself, and it stays written.
        m.set(face="working", turn=60)
        a = silhouette(m.image())
        m.pump(0.3)
        assert m.stone.property("turn") == 60 and silhouette(m.image()) == a
        m.set(turn=0)
        assert silhouette(m.image()) != a
    finally:
        close(m)


def test_animate_off_wins_over_reduced_motion_nothing_pulses_or_fades(app, tmp_path):
    """Reduced motion swaps the roll for a pulse and the hop for a fade; a stone that does not animate has
    neither, because it is posed by its host a frame at a time."""
    m = build(app, tmp_path, "animate: false; reducedMotion: true")
    try:
        for face in ("working", "starting", "done", "needs", "rest"):
            m.set(face=face)
            m.pump(0.1)
            assert running_things(m.win) == [], face
    finally:
        close(m)
    m = build(app, tmp_path, "reducedMotion: true; face: \"working\"")           # animated: the pulse is what runs
    try:
        assert running_things(m.win), "an animated stone under reduced motion still pulses"
        m.set(animate=False)
        assert running_things(m.win) == []
    finally:
        close(m)


INKED = ("rest", "listening", "working", "needs", "done", "stopped", "resting", "offline", "starting")


def test_ink_draws_every_face_in_fg_with_no_state_colour(app, tmp_path):
    m = build(app, tmp_path, "ink: \"fg\"; animate: false")
    try:
        assert near(m.at(12, 8), THEME["fg"]) and m.stone.property("inked") is True
        for face in INKED:
            m.set(face=face)
            img = m.image()
            for tone in ("good", "accent", "warn", "bad"):
                assert not hues(img, THEME[tone], 40), (face, tone)
            assert len(hues(img, THEME["fg"], 40)) > 20, face      # and it is drawn
        m.set(face="needs")
        assert near(m.at(12, 8), THEME["fg"])                   # the body is ink ...
        glow = QtGui.QColor(m.at(2, 13))                        # ... and so is the glow outside it, not amber
        ground = QtGui.QColor(GROUND)
        assert glow.red() > ground.red() + 10 and abs(glow.red() - glow.blue()) < 8, glow.name()
        m.snap("ink-needs")
        m.set(face="rest")
        m.snap("ink-rest")
    finally:
        close(m)


def test_ink_muted_and_true_and_off(app, tmp_path):
    m = build(app, tmp_path, "ink: \"muted\"; animate: false")
    try:
        assert near(m.at(12, 8), THEME["muted"])
        m.set(face="stopped")
        assert near(m.at(12, 12), THEME["muted"])
        m.set(face="rest", ink=True)                # true is the primary ink
        assert near(m.at(12, 8), THEME["fg"])
        m.set(ink="")                              # off: the pill's own colours
        assert near(m.at(12, 8), THEME["good"]) and m.stone.property("inked") is False
        m.set(ink=False)
        assert near(m.at(12, 8), THEME["good"])
    finally:
        close(m)


def test_ink_stone_still_moves_the_way_its_face_says(app, tmp_path):
    # Colour is not what says the state: an inked working stone still rolls and the b holds still.
    m = build(app, tmp_path, "ink: \"fg\"; face: \"working\"")
    try:
        shapes = set()
        for _ in range(20):
            m.pump(0.05)
            img = m.image()
            assert near(img.pixelColor(8, 12).name(), CUT)
            shapes.add(frozenset(silhouette(img)))
        assert len(shapes) > 3
    finally:
        close(m)


def cut16(img):
    """The b at 16 px, whose 1.5 px lines never land on whole pixels: anything clearly more magenta than green."""
    return hues(img, CUT, 70)


def box(pixels):
    xs, ys = [p[0] for p in pixels], [p[1] for p in pixels]
    return min(xs), min(ys), max(xs), max(ys)


def test_small_is_the_16_px_drawing_full_bleed_with_the_b_cut_in(app, tmp_path):
    m = build(app, tmp_path, "small: true; animate: false")
    try:
        assert (m.stone.property("width"), m.stone.property("height")) == (16.0, 16.0)
        img = m.image()
        # The stone fills its 16 px square and nothing falls outside it (the window is 24 px).
        assert box(silhouette(img)) == (0, 0, 15, 15)
        assert near(img.pixelColor(20, 20).name(), GROUND) and near(img.pixelColor(17, 8).name(), GROUND)
        assert near(img.pixelColor(8, 4).name(), THEME["good"])                  # above the b's bar
        assert near(img.pixelColor(4, 9).name(), CUT, 40)                        # the b's stem
        assert len(cut16(img)) > 10                                              # the b is about 2 px across
        assert near(img.pixelColor(1, 1).name(), GROUND) and near(img.pixelColor(14, 1).name(), GROUND)
        # It is not the 24 px drawing scaled: that one is 18 px wide inside its 24 px slot.
        big = build(app, tmp_path, "animate: false")
        try:
            assert box(silhouette(big.image())) == (3, 3, 20, 20)
        finally:
            close(big)
        m.snap("small-rest")
    finally:
        close(m)


def test_small_cut_stays_inside_the_stone_and_a_third_of_a_turn_leaves_it_as_it_was(app, tmp_path):
    m = build(app, tmp_path, "small: true; animate: false")
    try:
        for turn in (0, 17, 45, 60, 90, 120):
            m.set(turn=turn)
            img = m.image()
            cut = cut16(img)
            assert len(cut) > 10, turn
            for x, y in cut:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        assert near(img.pixelColor(x + dx, y + dy).name(), GROUND, 6) is False, (turn, x, y)
        m.set(turn=0)
        before = silhouette(m.image())
        m.set(turn=120)
        assert len(before ^ silhouette(m.image())) <= 8
        # The lean rocks on the 16 px stone's own arc and the b rides with it, level.
        m.set(turn=0, lean=6)
        leaning = m.image()
        assert silhouette(leaning) != before and len(cut16(leaning)) > 10
    finally:
        close(m)


def test_small_squashes_about_its_foot_so_it_stays_on_the_ground_it_stands_on(app, tmp_path):
    """The hop squashes the stone about where it stands: the 16 px drawing's foot is its bottom edge, so a squash
    changes its top and its sides and leaves its bottom row where it was."""
    m = build(app, tmp_path, "small: true; animate: false")
    try:
        at_rest = box(silhouette(m.image()))
        assert at_rest == (0, 0, 15, 15)
        m.set(hopSx=1.1, hopSy=0.84)
        _x0, y0, _x1, y1 = box(silhouette(m.image()))
        assert y1 == 15 and y0 > 0                       # the foot holds, the top comes down
        m.set(hopSx=0.97, hopSy=1.04, hopY=-3)           # the stretch at the top of the hop leaves the ground
        assert box(silhouette(m.image()))[3] < 15
    finally:
        close(m)


def test_small_faces_stopped_resting_and_offline(app, tmp_path):
    m = build(app, tmp_path, "small: true; animate: false")
    try:
        m.set(face="stopped")
        img = m.image()
        assert near(img.pixelColor(8, 8).name(), THEME["muted"]) and near(img.pixelColor(4, 4).name(), THEME["muted"])
        assert near(img.pixelColor(1, 1).name(), GROUND) and near(img.pixelColor(14, 14).name(), GROUND)
        assert not cut16(img)
        m.set(face="resting")
        img = m.image()
        for tone in ("good", "warn", "accent", "bad"):
            assert not hues(img, THEME[tone])
        assert len(hues(img, THEME["muted"], 40)) > 40 and len(cut16(img)) > 10
        m.snap("small-resting")
        m.set(face="offline")
        img = m.image()
        assert near(img.pixelColor(7, 5).name(), GROUND) and len(hues(img, THEME["bad"], 30)) > 30
        m.snap("small-offline")
        m.set(face="needs")
        img = m.image()
        assert near(img.pixelColor(8, 4).name(), THEME["warn"])
        assert not near(m.at(8, 18), GROUND, 6)         # the glow reaches past the 16 px square
        m.snap("small-needs")
    finally:
        close(m)


def test_the_16_px_stone_is_the_brands_own_drawing(app, tmp_path):
    # One drawing everywhere: the stone and its b are the path of docs/brand's 16 px symbolic icon.
    icon = ROOT / "iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil-symbolic.svg"
    d = re.search(r' d="([^"]+)"', icon.read_text()).group(1)
    stone_path, b_outline = d[: d.index("Z") + 1], d[d.index("Z") + 1:]
    m = build(app, tmp_path, "small: true")
    try:
        assert m.stone.property("stonePath") == stone_path
        assert b_outline in (KIT / "Stone.qml").read_text()
        # The 24 px drawing keeps the brand's 24 px grid.
        m.set(small=False)
        assert m.stone.property("stonePath").startswith("M10.65 3.362A2.7 2.7")
    finally:
        close(m)


def test_the_16_px_outline_is_the_stone_inset_by_half_the_line(app, tmp_path):
    # Resting and offline draw the stone as a 1.5 px line whose outer edge is the stone's: the same
    # shape at W 14.5 in the square inset 0.75 on every side, as the 24 px one is W 16.5 inset 0.75 from 18.
    def reuleaux(w, x0, y0):
        r, big, s = 0.15 * w, 0.85 * w, 0.7 * w
        top = (x0 + w / 2, y0 + r)
        left = (top[0] - s / 2, top[1] + s * math.sqrt(3) / 2)
        right = (top[0] + s / 2, left[1])

        def toward(c, o):
            d = math.dist(c, o)
            return (c[0] + r * (c[0] - o[0]) / d, c[1] + r * (c[1] - o[1]) / d)

        pts = [toward(top, right), toward(top, left), toward(right, left), toward(right, top), toward(left, top),
               toward(left, right)]
        return pts, (r, big)

    m = build(app, tmp_path, "small: true")
    try:
        pts, (r, big) = reuleaux(14.5, 0.75, 0.75)
        nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", m.stone.property("outlinePath"))]
        assert abs(nums[0] - pts[0][0]) < 0.002 and abs(nums[1] - pts[0][1]) < 0.002
        assert abs(nums[2] - r) < 0.002 and abs(nums[9] - big) < 0.002
    finally:
        close(m)


def test_small_and_ink_and_animate_leave_the_pills_stone_as_it_was(mark):
    # The pill's defaults: animate on, 24 px, state colours.
    assert mark.stone.property("animate") is True and mark.stone.property("small") is False
    assert mark.stone.property("ink") in ("", False) and mark.stone.property("inked") is False
    assert (mark.stone.property("width"), mark.stone.property("height")) == (24.0, 24.0)
    assert near(mark.at(12, 8), THEME["good"])

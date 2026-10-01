"""The stone in the pill (shell/Stone.qml): Bombadil's mark, drawn and animated offscreen.

Each face is rendered at 1x on a flat ground and read back pixel by pixel. Set BOMBADIL_SCREENS=<dir>
to save a picture of each face at 8x.
"""

import os
import time
from pathlib import Path

import pytest
from qml_theme import THEME

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)

SHELL = Path(__file__).resolve().parents[1] / "shell"
GROUND = "#101214"      # what the window behind the stone is
CUT = "#ff00ff"         # the b's colour in these tests, so it cannot be mistaken for anything else

HARNESS = """
import QtQuick
import "%s"

Window {
    width: 24 + 2 * %d; height: 24 + 2 * %d; visible: true; color: "%s"
    Stone { id: stone; objectName: "stone"; x: %d; y: %d; ground: "%s"; settle: %d }
}
"""


class Mark:
    def __init__(self, app, tmp_path, ground=CUT, settle=0, pad=0):
        # pad: ground left around the 24 px slot, to see what the stone draws outside it.
        qml = tmp_path / "stone.qml"
        qml.write_text(HARNESS % (SHELL.as_uri(), pad, pad, GROUND, pad, pad, ground, settle))
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

"""Kit.TipScene (share/qml/Bombadil/TipScene.qml): a tip's scene, drawn offscreen from fixture tips.

A scene is a function of its clock, so a frame at a chosen t is grabbed and read back without waiting.
Set BOMBADIL_SCREENS=<dir> to save a picture of the frames (tip-<name>-card.png, -app-<ms>.png, -stills.png).
"""

import copy
import os
import time
from pathlib import Path

import pytest
import tip_fixtures as fx
from qml_running import running_things
from qml_theme import THEME

from bombadil import tip_scene

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "share" / "qml" / "Bombadil"
GROUND = "#202428"

HARNESS = """
import QtQuick
import "%s" as Kit

Window {
    id: win
    width: 640; height: 400; visible: true; color: "%s"
    property int finishedCount: 0
    Kit.TipScene {
        id: scene; objectName: "scene"; x: 12; y: 12
        density: "%s"; reducedMotion: %s
        onFinished: win.finishedCount += 1
    }
}
"""


class Scene:
    def __init__(self, app, tmp_path, tip, density="card", reduced=False):
        self.app = app
        qml = tmp_path / "scene.qml"
        qml.write_text(HARNESS % (KIT.as_uri(), GROUND, density, "true" if reduced else "false"))
        self.engine = QtQml.QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        self.scene = self.win.findChild(QtCore.QObject, "scene")
        self.tip = tip
        self.scene.setProperty("tip", copy.deepcopy(tip))
        self.pump()

    def pump(self, seconds=0.03):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.004)

    # -- the scene's own interface --
    def call(self, name, *args):
        ok = QtCore.QMetaObject.invokeMethod(
            self.scene, name, *[QtCore.Q_ARG("QVariant", a) for a in args])
        assert ok, name
        self.pump()

    def seek(self, ms):
        self.call("seek", ms)
        return self

    def prop(self, name):
        return self.scene.property(name)

    def set(self, **props):
        for k, v in props.items():
            self.scene.setProperty(k, v)
        self.pump()

    @property
    def duration(self):
        return self.prop("duration")

    @property
    def frame(self):
        return plain(self.prop("frame"))

    # -- the picture --
    def image(self):
        self.pump(0.02)
        return self.win.grabWindow().convertToFormat(QtGui.QImage.Format_RGB32)

    def at(self, x, y):
        return self.image().pixelColor(12 + x, 12 + y).name()

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            w, h = int(self.scene.width()), int(self.scene.height())
            img = self.image().copy(12, 12, max(w, 1), max(h, 1))
            img.save(str(Path(out) / f"tip-{self.tip['id']}-{name}.png"))

    # -- the items --
    def items(self, name=None):
        out, todo = [], [self.scene]
        while todo:
            item = todo.pop()
            if name is None or item.objectName() == name:
                out.append(item)
            todo.extend(item.childItems())
        return out

    def item(self, name):
        found = [i for i in self.items(name) if i.isVisible()]
        return found[0] if found else None

    def texts(self):
        """(text, pixelSize, objectName) of every Text that shows something."""
        out = []
        for i in self.items():
            if i.metaObject().className().startswith("QQuickText") and i.isVisible() and i.property("text"):
                out.append((i.property("text"), i.property("font").pixelSize(), i.objectName()))
        return out

    def close(self):
        self.win.close()
        self.engine.deleteLater()
        self.pump()


def plain(v):
    """A JS value from the scene as plain Python (a var property comes back as a QJSValue or a dict)."""
    if hasattr(v, "toVariant"):
        v = v.toVariant()
    return v


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


@pytest.fixture
def make(app, tmp_path):
    made = []

    def build(tip, density="card", reduced=False):
        where = tmp_path / f"s{len(made)}"
        where.mkdir()
        s = Scene(app, where, tip, density, reduced)
        made.append(s)
        assert s.warnings == [], s.warnings
        return s

    yield build
    for s in made:
        s.close()


def near(a, b, tol=14):
    qa, qb = QtGui.QColor(a), QtGui.QColor(b)
    return max(abs(qa.red() - qb.red()), abs(qa.green() - qb.green()), abs(qa.blue() - qb.blue())) <= tol


def colour_count(img, tone, tol=14, box=None):
    x0, y0, x1, y1 = box or (0, 0, img.width(), img.height())
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if near(img.pixelColor(x, y).name(), THEME[tone], tol))


def beat_start(tip, index):
    return tip_scene.beat_times(tip["beats"])[index][0]


def beat_of(tip, key, value):
    """The index of the first beat with `key` set to `value`, so a test names the beat it means."""
    return next(i for i, b in enumerate(tip["beats"]) if b.get(key) == value)


# -- the clock: a scene is a function of t --

@pytest.mark.parametrize("tip", fx.ALL, ids=lambda t: t["id"])
def test_the_duration_is_the_python_helpers_to_the_millisecond(make, tip):
    s = make(tip)
    assert s.duration == tip_scene.duration(tip["beats"])
    assert tip_scene.validate(tip) == []
    assert s.duration <= 10_000


def test_the_phase_comes_from_the_captions_beat_ranges(make):
    s = make(fx.BUILD, "app")
    starts = tip_scene.phase_starts(fx.BUILD["beats"])
    assert starts == (beat_start(fx.BUILD, 2), beat_start(fx.BUILD, 5))
    for t, phase in ((0, 0), (starts[0] - 1, 0), (starts[0], 1), (starts[1] - 1, 1), (starts[1], 2), (s.duration, 2)):
        s.seek(t)
        assert s.prop("phase") == phase, t
    s.seek(0)
    caps = [s.item(f"caption-{i}") for i in range(3)]
    assert [c.property("text") for c in caps] == ["You ask", "It builds", "You have it"]


def test_without_marks_the_phases_are_thirds(make):
    tip = copy.deepcopy(fx.BANG)
    s = make(tip, "app")
    third = tip_scene.duration(tip["beats"]) // 3
    s.seek(third - 1)
    assert s.prop("phase") == 0
    s.seek(third)
    assert s.prop("phase") == 1
    s.seek(2 * third)
    assert s.prop("phase") == 2


def test_seek_clamps_and_stops_the_clock(make):
    s = make(fx.BUILD, "app")
    s.seek(-50)
    assert s.prop("t") == 0
    s.seek(1e9)
    assert s.prop("t") == s.duration and not s.prop("playing")
    s.seek(1234)
    assert s.prop("t") == 1234


def test_the_frame_at_a_chosen_t_is_the_same_every_time(make):
    s = make(fx.BUILD, "app")
    s.seek(3000)
    a = s.image()
    s.seek(500)
    s.seek(3000)
    assert s.image() == a
    assert s.frame == plain(s.prop("frame"))


# -- every beat, as frames --

def test_a_key_is_a_keycap_pressed_for_300_to_400_ms(make):
    assert 300 <= tip_scene.KEY_MS <= 400
    s = make(fx.BUILD, "app")
    for t, shown, down in ((50, True, False), (170, True, True), (300, True, False), (360, False, False)):
        s.seek(t)
        cap = s.item("key")
        assert (cap is not None) == shown, t
        if cap is not None:
            assert (cap.property("y") > s.item("pill").property("y") + (52 - 30) / 2 + 1) == down, t
    assert tip_scene.KEYS["super"] == "⊞"
    s.seek(200)
    assert s.frame["key"]["label"] == "⊞"


def test_the_other_keys_are_drawn_with_their_marks(make):
    labels = {}
    keys = (
        (fx.BUILD, beat_of(fx.BUILD, "key", "enter"), "enter"),
        (fx.ESC, beat_of(fx.ESC, "key", "esc"), "esc"),
        (fx.OPEN, beat_of(fx.OPEN, "key", "tab"), "tab"),
        (fx.BANG, beat_of(fx.BANG, "key", "bang"), "bang"),
    )
    for tip, index, name in keys:
        s = make(tip, "app")
        s.seek(beat_start(tip, index) + 180)
        labels[name] = s.frame["key"]["label"]
    assert labels == {"enter": "↵", "esc": "Esc", "tab": "Tab", "bang": "!"}


def test_typing_is_25_characters_a_second_and_ends_with_the_ghost(make):
    words = fx.BUILD["words"]
    start = beat_start(fx.BUILD, 1)
    s = make(fx.BUILD, "app")
    for chars in (0, 5, 10, len(words)):
        s.seek(start + chars * 40)
        assert s.frame["pill"]["text"] == words[:chars], chars
    s.seek(start + 400)                                           # a second is 25 characters, so 10 in 400 ms
    assert s.item("pillText").property("text") == words[:10]
    s = make(fx.OPEN, "app")
    typed_at = beat_start(fx.OPEN, beat_of(fx.OPEN, "ghost", "kets"))
    s.seek(typed_at + 40 * 3)
    assert s.frame["pill"]["ghost"] == "kets"
    assert s.item("pillGhost").property("text") == "kets"
    s.seek(beat_start(fx.OPEN, beat_of(fx.OPEN, "key", "tab")) + 150)  # Tab takes the completion ...
    assert s.frame["pill"]["text"] == "tickets" and s.frame["pill"]["ghost"] == ""
    s.seek(beat_start(fx.OPEN, beat_of(fx.OPEN, "key", "enter")) + 150)      # ... and Enter sends it
    assert s.frame["pill"]["text"] == ""


def test_the_stone_rolls_hops_and_knocks_and_never_bounces(make):
    s = make(fx.BUILD, "app")
    start = beat_start(fx.BUILD, 3)
    turns = []
    for dt in range(0, 1200, 25):
        s.seek(start + dt)
        assert s.frame["stone"]["face"] == "working"
        turns.append(s.frame["stone"]["turn"])
    assert max(turns) == 120 and min(turns) == 0 and turns[:12] == [0.0] * 12          # holds 300 ms, then turns
    rolling = turns[: turns.index(120) + 1]
    assert rolling == sorted(rolling)                                                  # one way, never back
    stone = s.item("stone")
    s.seek(start + 700)
    assert stone.property("turn") == s.frame["stone"]["turn"] and 0 < stone.property("turn") < 120
    # done: one hop, 700 ms, then still
    hop_at = beat_start(fx.BUILD, 6)
    ys = []
    for dt in range(0, 700, 20):
        s.seek(hop_at + dt)
        ys.append(s.frame["stone"]["hopY"])
    assert min(ys) < -2.5 and max(ys) == 0 and ys[0] == 0
    s.seek(hop_at + 700)
    assert (s.frame["stone"]["hopY"], s.frame["stone"]["hopSx"], s.frame["stone"]["hopSy"]) == (0, 1, 1)
    # needs: two knocks of up to 8 degrees to the right, on a 1.6 s beat, the glow breathing 0.25 to 0.6
    s = make(fx.ROUTE, "app")
    knock_at = beat_start(fx.ROUTE, 5)
    knocks, glows = [], []
    for dt in range(0, 1600, 16):
        s.seek(knock_at + dt)
        knocks.append(s.frame["stone"]["knock"])
        glows.append(s.frame["stone"]["glow"])
    peaks = sum(1 for a, b, c in zip(knocks, knocks[1:], knocks[2:], strict=False) if b > a and b >= c and b > 4)
    assert peaks == 2 and 0 <= min(knocks) and max(knocks) <= 8.0001
    assert min(glows) >= 0.25 - 1e-9 and max(glows) <= 0.6 + 1e-9 and max(glows) > 0.55


def test_the_stone_leans_after_super_and_rests_after_enter(make):
    s = make(fx.BUILD, "app")
    s.seek(beat_start(fx.BUILD, 0) + 100)
    assert s.frame["stone"]["face"] == "rest"
    s.seek(beat_start(fx.BUILD, 1) + 200)
    assert s.frame["stone"]["face"] == "listening" and s.frame["pill"]["border"] == "listening"
    s.seek(beat_start(fx.BUILD, 1) + 600)
    assert abs(s.frame["stone"]["lean"] - 6) < 0.01
    s.seek(beat_start(fx.BUILD, 2) + 200)                          # Enter: the words leave, the stone rests
    assert s.frame["stone"]["face"] == "rest" and s.frame["pill"]["text"] == ""


def test_the_line_has_a_tone_a_command_a_counter_and_buttons(make):
    s = make(fx.ESC, "app")
    at = beat_start(fx.ESC, 2) + 1500
    s.seek(at)
    line = s.frame["line"]
    assert (line["text"], line["tone"], line["command"]) == ("Installing docker", "amber", "pacman -S docker")
    assert s.item("lineText").property("text") == "Installing docker"
    assert s.item("lineCommand").property("text") == "pacman -S docker"
    assert s.item("lineCommand").property("font").family() == "monospace"
    assert s.frame["line"]["secs"] == 1 and s.item("lineCounter").property("text") == "1s"
    s.seek(beat_start(fx.ESC, 5) + 400)                            # it keeps counting until the stone stops
    assert s.frame["line"]["secs"] == 2 and s.item("lineCounter").property("text") == "2s"
    s.seek(beat_start(fx.ESC, 6) + 500)                            # a stopped stone: no counter any more
    assert s.frame["line"]["secs"] == -1 and s.frame["stone"]["face"] == "stopped"
    s = make(fx.BUILD, "app")
    s.seek(s.duration)
    assert s.frame["line"]["buttons"] == ["Undo", "Details"] and len(s.items("lineButton")) == 2
    s = make(fx.BANG, "app")
    s.seek(s.duration)
    assert s.frame["line"]["mono"] is True and s.frame["line"]["text"] == "6.17.2-arch1-1"
    s = make(fx.ROUTE, "app")
    s.seek(beat_start(fx.ROUTE, 3) + 600)
    assert s.frame["line"]["tone"] == "red"


def test_the_counter_starts_where_it_says_and_counts_while_the_stone_works(make):
    s = make(fx.CHANGE, "app")
    start = beat_start(fx.CHANGE, 3)
    s.seek(start + 100)
    assert s.frame["line"]["secs"] == 7                            # counter = 7: it is already at 7 s
    s.seek(start + 1000)
    assert s.frame["line"]["secs"] == 8


def test_a_window_slides_in_300_ms_on_the_system_curve_and_never_overshoots(make):
    assert tip_scene.SLIDE_MS == 300
    s = make(fx.BUILD, "app")
    start = beat_start(fx.BUILD, 5)
    slides = []
    for dt in (0, 50, 100, 150, 200, 250, 300, 400):
        s.seek(start + dt)
        slides.append(s.frame["win"]["slide"])
    assert slides[0] == 0 and slides[-1] == 1 and slides[-2] == 1
    assert slides == sorted(slides) and max(slides) <= 1               # no bounce
    assert slides[3] > 0.5                                             # (0.16, 1, 0.3, 1) is nearly there at half time
    s.seek(start + 150)
    y_mid = s.item("window").property("y")
    s.seek(start + 300)
    assert s.item("window").property("y") < y_mid                      # it rises into its place


def test_window_rows_arrive_one_after_another_and_a_reload_adds_a_column_in_place(make):
    s = make(fx.BUILD, "app")
    start = beat_start(fx.BUILD, 5) + 300
    s.seek(start + 100)
    alphas = [r["alpha"] for r in s.frame["win"]["rows"]]
    assert alphas[0] > 0 and alphas[1] == 0 and alphas[2] == 0
    s.seek(start + 600)
    assert [r["alpha"] for r in s.frame["win"]["rows"]] == [1, 1, 1]
    assert len(s.items("windowRow")) == 3
    s = make(fx.CHANGE, "app")
    shown = beat_start(fx.CHANGE, 4)
    s.seek(shown - 1)
    before = s.item("window").property("y"), s.item("window").property("x")
    assert s.frame["win"]["cols"] == []
    s.seek(shown + 600)
    assert (s.item("window").property("y"), s.item("window").property("x")) == before        # in place: no slide
    cols = s.frame["win"]["cols"]
    assert cols[0]["head"] == "Due" and cols[0]["cells"] == ["Oct 3", "Oct 6", "Oct 9"] and cols[0]["alpha"] == 1
    assert s.frame["win"]["slide"] == 1
    assert "Due" in [t for t, _, _ in s.texts()]


def test_a_window_can_show_a_lock_screen_a_document_and_slide_from_the_left(make):
    s = make(fx.LOCK, "app")
    s.seek(s.duration)
    assert s.frame["win"]["lock"] == "Locked after 5 minutes idle"
    assert "Locked after 5 minutes idle" in [t for t, _, _ in s.texts()]
    assert s.items("windowRow") == []
    s.snap("app-end")
    s = make(fx.TIMER, "app")
    start = beat_start(fx.TIMER, 4)
    s.seek(start)
    assert s.frame["win"]["from"] == "left" and s.frame["win"]["doc"] is True
    s.seek(start + 100)
    far = s.item("window").property("x")
    s.seek(start + 300)
    assert far < s.item("window").property("x")                       # it comes in from the left edge


def test_a_card_rises_and_updates_in_place_and_folds_to_a_strip(make):
    s = make(fx.TIMER, "app")
    start = beat_start(fx.TIMER, 2)
    s.seek(start + 75)
    card = s.item("card-watching")
    assert card.property("transformOrigin") == QtQuick.QQuickItem.TransformOrigin.BottomLeft
    assert 0.6 < card.property("scale") < 1 and 0 < card.property("opacity") < 1       # the desk's own arrival
    s.seek(start + 160)
    assert card.property("scale") == 1 and card.property("opacity") == 1
    texts = [t for t, _, _ in s.texts()]
    assert "Timer, 25 min" in texts and "25:00" in texts
    s.seek(beat_start(fx.TIMER, 3) + 100)
    assert "24:59" in [t for t, _, _ in s.texts()] and s.item("card-watching").property("scale") == 1
    s.seek(s.duration)
    assert s.item("card-watching").property("opacity") == 0                    # folded
    strip = s.item("strip")
    assert strip is not None and [t for t, _, n in s.texts() if t == "1 counting"]
    assert s.item("pill").property("x") > 64                                   # the pill narrows for the strip
    s = make(fx.MACHINE, "app")
    s.seek(s.duration)
    assert "The machine is fine" in [t for t, _, _ in s.texts()] and s.item("card-machine") is not None
    origin = QtQuick.QQuickItem.TransformOrigin
    assert s.item("card-machine").property("transformOrigin") == origin.BottomRight       # it arrives from its rail's corner
    s = make(fx.ROUTE, "app")
    s.seek(s.duration)
    assert [t for t, _, _ in s.texts() if t == "Install rust and set it up"]
    assert s.item("card-now") is not None


def test_a_fold_can_be_taken_back(make):
    tip = copy.deepcopy(fx.TIMER)
    tip["beats"].append({"fold": False, "tag": "template"})
    s = make(tip, "app")
    s.seek(s.duration)
    assert s.item("card-watching").property("opacity") == 1 and s.frame["fold"]["p"] == 0
    assert s.item("strip") is None


def test_a_picture_card_streams_its_boxes_in_and_lights_one(make):
    s = make(fx.PICTURE, "app")
    start = beat_start(fx.PICTURE, 4)
    s.seek(start + 300 + 100)
    boxes = [b["alpha"] for b in s.frame["pic"]["boxes"]]
    assert boxes[0] > 0 and boxes[1] == 0                                 # one at a time
    s.seek(start + 300 + 320 * 4 + 200)
    assert [b["alpha"] for b in s.frame["pic"]["boxes"]] == [1, 1, 1, 1]
    assert s.frame["pic"]["lit"] == 2 and len(s.items("box")) == 4
    box_texts = [t for t, _, _ in s.texts()]
    assert "Router" in box_texts and "How you're connected" in box_texts
    lit = sorted(s.items("box"), key=lambda i: i.mapToScene(QtCore.QPointF(0, 0)).x())[2]     # left to right
    ring = s.image()
    top_left = lit.mapToScene(QtCore.QPointF(0, 0))
    x0, y0 = int(top_left.x()), int(top_left.y())
    assert colour_count(ring, "warn", 14, (x0 + 8, y0, x0 + int(lit.width()) - 8, y0 + 2)) > 8      # its 2 px ring
    s = make(fx.DISK, "app")
    s.seek(s.duration)
    assert len(s.items("bar")) == 3
    s.snap("app-end")


def test_a_hold_is_a_pause_and_adds_to_a_beat(make):
    hold = {"beats": [{"hold": 700}, {"key": "enter", "hold": 40}, {"line": "x", "tag": "caption", "hold": 100}]}
    assert tip_scene.beat_times(hold["beats"]) == [(0, 700), (700, 400), (1100, 1000)]
    s = make({"id": "h", "captions": ["a", "b", "c"], **hold}, "app")
    assert s.duration == 2100


# -- the densities --

@pytest.mark.parametrize("tip", fx.ALL, ids=lambda t: t["id"])
def test_the_poster_is_the_last_frame(make, tip):
    card = make(tip, "card")
    assert card.frame["ended"] is True
    app = make(tip, "app")
    app.seek(app.duration)
    assert card.frame == app.frame                                  # the same frame, drawn at two sizes
    card.seek(0)                                                    # and the clock does not move a poster
    assert card.frame == app.frame
    assert card.prop("phase") == 2
    card.snap("card")
    app.snap("app-end")


def test_the_card_is_272_by_153_with_a_16_px_ink_stone_and_a_32_px_pill(make):
    s = make(fx.BUILD, "card")
    assert (s.scene.width(), s.scene.height()) == (272.0, 153.0)
    stage = s.item("stage")
    assert (stage.width(), stage.height()) == (272.0, 153.0)
    pill = s.item("pill")
    assert pill.property("height") == 32 and pill.property("radius") == 16
    stone = s.item("stone")
    assert (stone.property("width"), stone.property("height")) == (16.0, 16.0)
    assert stone.property("small") is True and stone.property("ink") == "fg" and stone.property("animate") is False
    ground = [i for i in s.items() if i.metaObject().className().startswith("QQuickRectangle")
              and i.property("radius") == 8 and i.width() == 272]
    assert ground, "the sunken ground has 8 px corners"


def test_the_card_never_sets_text_below_12_px_and_does_not_count(make):
    for tip in fx.ALL:
        s = make(tip, "card")
        shown = s.texts()
        for text, size, name in shown:
            assert size >= 12, (tip["id"], text, size, name)
        assert s.item("lineCounter") is None                          # no counting on a poster
        words = {text for _, text in tip_scene.strings(tip)} | set(tip_scene.KEYS.values())
        assert {text for text, _, _ in shown} <= words | {"Ask anything", "Now", "Watching", "Machine", "›"}, tip["id"]


def test_a_line_is_one_line_whole_at_every_density_and_in_the_stills(make):
    for tip in fx.ALL:
        for density, reduced in (("card", False), ("app", False), ("app", True)):
            s = make(tip, density, reduced)
            for ms in range(0, int(s.duration) + 1, 400):
                s.seek(ms)
                for item in s.items("lineText"):
                    if item.isVisible() and item.property("text"):
                        assert not item.property("truncated"), (tip["id"], density, ms, item.property("text"))
                        assert item.property("lineCount") == 1, (tip["id"], density, ms)
            # the line box is one line high, so nothing under it is ever covered by a second line
            for box in s.items("line"):
                assert box.height() in (22.0, 36.0)


def test_the_words_come_before_the_command_when_a_line_is_tight(make):
    s = make(fx.BUSY_END, "card")
    text, = [i for i in s.items("lineText") if i.isVisible()]
    assert text.property("text") == "Installing docker" and not text.property("truncated")
    command = [i for i in s.items("lineCommand") if i.isVisible()]
    assert len(command) <= 1
    assert not command or command[0].x() > text.x() + text.width()        # it never sits on the words
    s = make(fx.ESC, "app")
    s.seek(beat_start(fx.ESC, 2) + 1500)
    assert s.item("lineCommand").property("text") == "pacman -S docker"       # with room, the command is shown


def test_the_stills_grow_to_full_size_with_the_width_the_host_gives(make):
    s = make(fx.BUILD, "app", reduced=True)
    assert s.scene.width() == 576.0
    small = {size for _, size, _ in s.texts()}
    first = s.item("still-0")
    assert abs(first.property("scale") - (576 - 24) / 3 / 272) < 1e-6            # a thumbnail by default
    s.set(width=840)
    assert s.item("still-0").property("scale") == 1                              # and at full size, 12 px text
    assert min(size for _, size, _ in s.texts()) >= 12 and small


def test_the_cards_line_buttons_are_unlabelled_outlines(make):
    s = make(fx.BUILD, "card")
    buttons = s.items("lineButton")
    assert len(buttons) == 2
    labels = [t for t, _, _ in s.texts()]
    assert "Undo" not in labels and "Details" not in labels
    assert all(b.property("height") == 12 for b in buttons)
    app = make(fx.BUILD, "app")
    app.seek(app.duration)
    assert {"Undo", "Details"} <= {t for t, _, _ in app.texts()}


def test_machine_pictures_on_the_card_are_generic_shapes(make):
    for tip in (fx.PICTURE, fx.DISK, fx.MACHINE):
        s = make(tip, "card")
        texts = [t for t, _, _ in s.texts()]
        for name in ("Computer", "Wi-Fi", "Router", "Internet", "/home", "USB", "memory", "disk", "network",
                     "How you're connected", "Your disks", "42", "61", "62", "91"):
            assert name not in texts, (tip["id"], name)
        assert s.item("lineCommand") is None
    s = make(fx.PICTURE, "card")
    assert len(s.items("box")) == 4
    s = make(fx.DISK, "card")
    assert len(s.items("bar")) == 3


def test_the_card_stone_is_ink_and_the_app_stone_has_its_real_colours(make):
    quiet = ("good", "accent", "warn", "bad")
    s = make(fx.BUSY_END, "card")
    img = s.image()
    for tone in quiet:
        assert colour_count(img, tone, 20, (12, 12, 12 + 272, 12 + 153)) == 0, tone
    assert colour_count(img, "fg", 20, (12, 12, 12 + 272, 12 + 153)) > 30
    app = make(fx.BUSY_END, "app")
    app.seek(app.duration)
    img = app.image()
    assert colour_count(img, "accent", 14, (12, 12, 12 + 576, 12 + 324)) > 100           # rolling orange, and its border
    assert colour_count(img, "warn", 14, (12, 12, 12 + 576, 12 + 324)) > 10               # the amber edge
    app = make(fx.BUILD, "app")
    app.seek(app.duration)
    img = app.image()
    assert colour_count(img, "accent", 14, (12, 12, 12 + 576, 12 + 324)) == 0            # orange only where it acts
    assert colour_count(img, "good", 14, (12, 12, 12 + 576, 12 + 324)) > 50              # the stone at rest, green
    app.seek(0)
    assert colour_count(app.image(), "accent", 14, (12, 12, 12 + 576, 12 + 324)) == 0


def test_the_card_ground_has_two_faint_glows(make):
    s = make({"id": "blank", "captions": ["a", "b", "c"], "beats": [{"hold": 100}]}, "card")
    img = s.image()

    def lum(x, y):
        c = img.pixelColor(12 + x, 12 + y)
        return c.red() + c.green() + c.blue()
    assert lum(50, 4) > lum(136, 70) + 3                            # the one from above at the left
    assert lum(250, 148) > lum(200, 70) + 1                         # and the one below at the right


def test_the_app_stage_is_576_by_324_with_a_52_px_pill_and_16_px_text(make):
    s = make(fx.BUILD, "app")
    s.seek(beat_start(fx.BUILD, 1) + 400)
    assert (s.item("stage").width(), s.item("stage").height()) == (576.0, 324.0)
    assert s.item("pill").property("height") == 52
    stone = s.item("stone")
    assert (stone.property("width"), stone.property("height")) == (24.0, 24.0) and stone.property("small") is False
    assert stone.property("ink") == ""
    assert s.item("pillText").property("font").pixelSize() == 16
    s.seek(beat_start(fx.BUILD, 4) + 600)
    assert s.item("lineText").property("font").pixelSize() == 15
    assert [c for c in (s.scene.width(), s.scene.height())] == [576.0, 324.0 + 10 + s.item("captions").height()]


def test_the_app_shows_its_three_captions_with_the_current_one_in_fg(make):
    s = make(fx.BUILD, "app")
    for t, current in ((0, 0), (2500, 1), (s.duration, 2)):
        s.seek(t)
        colours = [QtGui.QColor(s.item(f"caption-{i}").property("color")).name() for i in range(3)]
        assert [c == QtGui.QColor(THEME["fg"]).name() for c in colours] == [i == current for i in range(3)], t
    cap = s.item("caption-0")
    assert cap.mapToItem(s.scene, QtCore.QPointF(0, 0)).y() >= 324          # under the stage
    assert cap.property("font").pixelSize() == 12
    s.snap("app-mid")


def test_the_stage_and_the_card_set_every_text_in_inter(make):
    for density in ("card", "app"):
        s = make(fx.BUILD, density)
        s.seek(s.duration)
        for i in s.items():
            if i.metaObject().className().startswith("QQuickText") and i.property("text"):
                fam = i.property("font").family()
                assert fam == "Inter" or i.objectName() in ("lineCommand",) or fam == "monospace", (density, i.property("text"), fam)


# -- reduced motion --

def test_reduced_motion_shows_three_stills_side_by_side_with_their_captions(make):
    s = make(fx.BUILD, "app", reduced=True)
    assert s.item("stage") is None and s.item("stills") is not None
    stills = [s.item(f"still-{i}") for i in range(3)]
    assert all(st is not None for st in stills)
    xs = [st.mapToItem(s.scene, QtCore.QPointF(0, 0)).x() for st in stills]
    assert xs == sorted(xs) and len(set(xs)) == 3
    caps = [s.item(f"still-caption-{i}").property("text") for i in range(3)]
    assert caps == ["You ask", "It builds", "You have it"]
    assert s.item("captions") is None                                  # the row under a stage is not drawn
    img = s.image()
    third = int(s.scene.width() / 3)
    pieces = [img.copy(12 + k * third, 12, third - 6, 110) for k in range(3)]
    assert pieces[0] != pieces[1] and pieces[1] != pieces[2] and pieces[0] != pieces[2]
    s.snap("stills")


def test_the_stills_are_the_last_frame_of_each_phase(make):
    s = make(fx.BUILD, "app", reduced=True)
    texts = [t for t, _, n in s.texts() if n == "pillText"]
    assert fx.BUILD["words"] in texts                                   # phase 0: the words are in the pill, unsent
    lines = [t for t, _, n in s.texts() if n == "lineText"]
    assert "Building Tickets, 140 lines" in lines                       # phase 1
    assert "Made Tickets. It is open now." in lines                     # phase 2: the payoff
    wins = [t for t, _, n in s.texts() if n == "windowTitle"]
    assert wins == ["Tickets"]


def test_reduced_motion_plays_nothing_and_settles_at_once(make):
    s = make(fx.BUILD, "app", reduced=True)
    s.call("play")
    assert not s.prop("playing") and running_things(s.win) == []
    assert s.prop("t") == s.duration
    s.pump(0.1)
    assert s.win.property("finishedCount") == 1


# -- nothing runs while not playing --

def test_nothing_runs_while_the_scene_is_not_playing(make):
    for density, reduced in (("card", False), ("app", False), ("app", True)):
        s = make(fx.BUILD, density, reduced)
        assert running_things(s.win) == [], (density, reduced)
        s.seek(1500)
        assert running_things(s.win) == []
        s.seek(s.duration)
        assert running_things(s.win) == []
        assert not s.prop("playing")
    # even with a stone that rolls and a stone that knocks on the frame: the stone is posed, not animated
    for tip, index in ((fx.BUILD, 3), (fx.ROUTE, 5)):
        s = make(tip, "app")
        s.seek(beat_start(tip, index) + 500)
        assert s.frame["stone"]["face"] in ("working", "needs") and running_things(s.win) == []
        s.pump(0.3)
        assert running_things(s.win) == []


def test_the_card_never_plays(make):
    s = make(fx.BUILD, "card")
    s.call("play")
    assert not s.prop("playing") and running_things(s.win) == []
    s.pump(0.1)
    assert s.win.property("finishedCount") == 1 and s.prop("t") == s.duration
    img = s.image()
    s.pump(0.3)
    assert s.image() == img


def test_playing_runs_one_animation_and_nothing_else_then_emits_finished_and_holds(make):
    s = make(fx.SHORT, "app")
    s.call("play")
    assert s.prop("playing")
    assert running_things(s.win) == ["QQuickNumberAnimation"]          # the clock, and no timer, no stone animation
    seen = []
    end = time.monotonic() + 3
    while s.prop("playing") and time.monotonic() < end:
        s.pump(0.05)
        seen.append(s.prop("t"))
    assert seen == sorted(seen) and seen[0] < s.duration
    assert not s.prop("playing") and s.prop("t") == s.duration
    assert s.win.property("finishedCount") == 1
    assert running_things(s.win) == []
    last = s.image()
    s.pump(0.4)
    assert s.image() == last and s.win.property("finishedCount") == 1          # it holds, and does not loop
    assert s.prop("t") == s.duration


def test_the_clock_runs_in_real_time_and_replay_starts_again(make):
    s = make(fx.SHORT, "app")
    started = time.monotonic()
    s.call("play")
    s.pump(0.5)
    t = s.prop("t")
    assert 380 <= t <= 900 and abs(t - (time.monotonic() - started) * 1000) < 400
    s.pump(1.2)
    assert not s.prop("playing") and s.win.property("finishedCount") == 1
    s.call("replay")
    assert s.prop("playing") and s.prop("t") < 200
    s.pump(1.6)
    assert not s.prop("playing") and s.win.property("finishedCount") == 2


def test_play_goes_on_from_a_seek_and_a_new_tip_starts_from_the_top(make):
    s = make(fx.SHORT, "app")
    s.seek(1000)
    s.call("play")
    s.pump(0.6)
    assert not s.prop("playing") and s.win.property("finishedCount") == 1
    s.set(tip=copy.deepcopy(fx.BANG))
    assert s.prop("t") == 0 and s.duration == tip_scene.duration(fx.BANG["beats"])
    s.seek(500)
    s.call("play")
    assert s.prop("playing")
    s.set(tip=copy.deepcopy(fx.SHORT))
    assert not s.prop("playing") and s.prop("t") == 0 and running_things(s.win) == []


def test_a_tip_with_no_beats_draws_a_resting_pill(make):
    s = make({"id": "empty", "captions": ["a", "b", "c"], "beats": []}, "app")
    assert s.duration == 0
    s.call("play")
    s.pump(0.1)
    assert s.item("pill") is not None and s.item("line") is None and s.item("window") is None
    assert s.item("pillText").property("text") == "Ask anything"
    assert running_things(s.win) == []


def test_the_vocabulary_the_scene_draws_is_the_one_the_validator_knows(make):
    s = make(fx.BUILD, "app")
    vocab = plain(s.prop("vocabulary"))
    assert set(vocab["draws"]) == set(tip_scene.DRAWS)
    assert set(vocab["modifiers"]) == set(tip_scene.MODIFIERS)
    assert set(vocab["faces"]) == set(tip_scene.FACES)
    assert set(vocab["keys"]) == set(tip_scene.KEYS)

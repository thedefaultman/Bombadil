"""Kit.TipScene (share/qml/Bombadil/TipScene.qml): a tip's scene, drawn offscreen from fixture tips.

A scene is a function of its clock, so a frame at a chosen t is grabbed and read back without waiting.
Set BOMBADIL_SCREENS=<dir> to save a picture of the frames (tip-<name>-card.png, -app-<ms>.png, -stills.png).
"""

import copy
import os
import sys
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
        %s
        onFinished: win.finishedCount += 1
    }
}
"""


# What QML says at run time. A binding that throws (a TypeError on a value that is null for a moment) is a message
# on stderr, not an exception and not an engine warning, so it is collected here and every test that draws a scene
# fails on one (the `make` fixture here and `drawn` in test_tips.py both check it).
QML_MESSAGES = []


def _collect(mode, ctx, msg):
    if ".qml" in (ctx.file or "") or "TypeError" in msg or "ReferenceError" in msg:
        QML_MESSAGES.append(msg)
    else:
        print(msg, file=sys.stderr)


class Scene:
    def __init__(self, app, tmp_path, tip, density="card", reduced=False):
        self.app = app
        QtCore.qInstallMessageHandler(_collect)       # again for each scene: another test file may have taken it
        qml = tmp_path / "scene.qml"
        # density None: the scene's own defaults (a card, with motion on)
        props = "" if density is None else f'density: "{density}"; reducedMotion: {"true" if reduced else "false"}'
        qml.write_text(HARNESS % (KIT.as_uri(), GROUND, props))
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
    del QML_MESSAGES[:]

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
    assert QML_MESSAGES == [], QML_MESSAGES


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


@pytest.mark.parametrize("density", ["card", "app"])
@pytest.mark.parametrize("tip", fx.ALL, ids=lambda t: t["id"])
def test_seeking_back_to_the_start_raises_no_qml_error(make, tip, density):
    """What Replay does (seek(0), then play) after a scene has reached its end: the line and the queue are gone from
    the frame for a moment before what draws them is hidden, and a binding that read their alpha threw a TypeError."""
    s = make(tip, density)
    for ms in (s.duration, 0, s.duration / 2, 0, s.duration):
        s.seek(ms)
    s.call("replay")
    s.seek(0)
    assert QML_MESSAGES == [], QML_MESSAGES


def test_the_frame_at_a_chosen_t_is_the_same_every_time(make):
    s = make(fx.BUILD, "app")
    s.seek(3000)
    a, frame = s.image(), s.frame
    s.seek(500)
    assert s.frame != frame
    s.seek(3000)
    assert s.image() == a and s.frame == frame                      # the same t, the same frame, the same picture


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


def test_a_listening_stone_leans_six_degrees_as_it_plays_and_rest_stands_it_up(make):
    """`stone = "listening"` alone is the lean the brief draws after Super: it leans in 240 ms, it does not
    sit at 0 degrees until the last frame."""
    tip = {"id": "lean", "captions": ["a", "b", "c"],
           "beats": [{"stone": "listening"}, {"hold": 1000}, {"stone": "rest"}, {"hold": 400}]}
    assert tip_scene.validate(tip) == []
    s = make(tip, "app")
    leans = {}
    for t in (0, 60, 120, 300, 1000, 1299):
        s.seek(t)
        leans[t] = s.frame["stone"]["lean"]
        assert s.frame["stone"]["face"] == "listening", t
    assert leans[0] == 0 and 0 < leans[60] < leans[120] < 6 and abs(leans[300] - 6) < 0.01 and abs(leans[1299] - 6) < 0.01
    s.seek(1500)                                                        # `rest` stands it up
    assert s.frame["stone"]["face"] == "rest" and s.frame["stone"]["lean"] == 0
    s.seek(s.duration)
    assert s.frame["stone"]["face"] == "rest" and s.frame["stone"]["lean"] == 0
    s = make(tip, "app")
    s.seek(1299)
    assert abs(s.item("stone").property("lean") - 6) < 0.01             # and the stone itself is posed by it


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
    assert s.frame["win"]["cols"] == [] and len(s.frame["win"]["rows"]) == 3
    s.seek(shown + 100)                                      # a fresh window would be sliding in, and empty
    win = s.frame["win"]
    assert win["slide"] == 1 and len(win["rows"]) == 3 and all(r["alpha"] == 1 for r in win["rows"])
    assert (s.item("window").property("y"), s.item("window").property("x")) == before
    assert len(s.items("windowRow")) == 3
    s.seek(shown + 600)
    assert (s.item("window").property("y"), s.item("window").property("x")) == before        # in place: no slide
    assert len(s.items("windowRow")) == 3
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


@pytest.mark.parametrize("density", ["card", "app"])
def test_nothing_in_a_window_is_clipped_by_its_border_and_a_lock_has_air_under_its_title(make, density):
    seen = set()
    for tip in fx.ALL:
        s = make(tip, density)
        s.seek(s.duration)
        win = s.item("window")
        if win is None:
            continue
        for name in ("windowRows", "windowDoc", "windowLock"):
            body = s.item(name)
            if body is None:
                continue
            seen.add(name)
            assert body.y() + body.height() <= win.height() - 1, (tip["id"], density, name)
            title = s.item("windowTitle")
            assert body.y() >= title.y() + title.height() - 1, (tip["id"], density, name)     # under the title
        if s.item("windowLock") is not None:
            lock = s.item("windowLock")
            assert lock.y() >= s.item("windowTitle").y() + s.item("windowTitle").height() + 2    # with a gap
    assert seen == {"windowRows", "windowDoc", "windowLock"}


def test_a_window_holds_the_rows_the_validator_lets_it_have(make):
    """The validator's limit is what the poster really holds: three rows fit the card, four the stage."""
    def rows(n):
        return {"id": "rows", "captions": ["a", "b", "c"], "beats": [
            {"window": "W", "tag": "model", "rows": [f"row {i}" for i in range(n)]}]}

    s = make(rows(tip_scene.MAX_WINDOW_ROWS), "card")
    assert s.item("windowRows").y() + s.item("windowRows").height() <= s.item("window").height() - 1
    s = make(rows(tip_scene.MAX_WINDOW_ROWS + 1), "card")
    assert s.item("windowRows").y() + s.item("windowRows").height() > s.item("window").height() - 1
    s = make(rows(4), "app")
    s.seek(s.duration)
    assert s.item("windowRows").y() + s.item("windowRows").height() <= s.item("window").height() - 1


def test_a_line_is_cleared_a_window_slides_out_and_the_counter_stops(make):
    s = make(fx.CLEARS, "app")
    s.seek(beat_start(fx.CLEARS, 2) + 1000)
    assert s.frame["line"]["secs"] >= 1 and s.item("lineCounter") is not None and s.item("window") is not None
    s.seek(beat_start(fx.CLEARS, 3) + 500)                       # counter = false: the words stay, the count goes
    assert s.frame["line"]["text"] == "Still working" and s.frame["line"]["secs"] == -1
    assert s.item("lineCounter") is None and s.frame["stone"]["face"] == "working"
    s.seek(beat_start(fx.CLEARS, 4) + 100)                       # line = "": nothing is on the line
    assert s.frame["line"] is None and s.item("line") is None and s.item("window") is not None
    out = beat_start(fx.CLEARS, 5)
    s.seek(out)
    assert s.frame["win"]["slide"] == 1
    s.seek(out + 150)                                            # window = false: it slides out in 300 ms
    assert 0 < s.frame["win"]["slide"] < 1 and s.item("window") is not None
    s.seek(out + 300)
    assert s.frame["win"] is None and s.item("window") is None
    s.seek(s.duration)
    assert s.frame["win"] is None and s.frame["line"] is None
    assert s.duration == tip_scene.duration(fx.CLEARS["beats"])


def test_columns_come_in_one_every_200_ms_and_a_window_can_slide_from_the_right(make):
    tip = {"id": "cols", "captions": ["a", "b", "c"], "beats": [
        {"window": "W", "tag": "model", "from": "right", "rows": ["a", "b"],
         "columns": [["One", "1", "2"], ["Two", "3", "4"]]}]}
    s = make(tip, "app")
    start = 300 + 2 * 200                                          # the slide, then the rows, then the columns
    s.seek(start + 100)
    one, two = s.frame["win"]["cols"]
    assert 0 < one["alpha"] < 1 and two["alpha"] == 0
    s.seek(start + 300)
    one, two = s.frame["win"]["cols"]
    assert one["alpha"] == 1 and 0 < two["alpha"] < 1
    s.seek(start + 500)
    assert [c["alpha"] for c in s.frame["win"]["cols"]] == [1, 1]
    s.seek(50)
    far = s.item("window").property("x")
    s.seek(300)
    assert s.item("window").property("x") < far                    # it comes in from the right edge
    assert s.item("window").property("x") + s.item("window").property("width") <= s.item("stage").width()


def test_the_cards_unfold_and_the_strips_fade_out_as_they_do(make):
    tip = copy.deepcopy(fx.TIMER)
    tip["beats"].append({"fold": False, "tag": "template"})
    s = make(tip, "app")
    back = beat_start(tip, len(tip["beats"]) - 1)
    s.seek(back - 1)
    assert s.item("strips").property("opacity") == 1
    s.seek(back + 75)                                              # half way: the strip is still there, fading
    strips = s.item("strips")
    assert strips is not None and 0.3 < strips.property("opacity") < 0.7 and s.item("strip") is not None
    assert 0 < s.item("card-watching").property("opacity") < 1
    s.seek(back + 300)
    assert s.item("strip") is None and s.item("card-watching").property("opacity") == 1


def test_a_card_rises_and_updates_in_place_and_folds_to_a_strip(make):
    s = make(fx.TIMER, "app")
    start = beat_start(fx.TIMER, 2)
    s.seek(beat_start(fx.TIMER, 3) + 100)                       # told again, the card is changed in place, not drawn twice
    assert len(s.items("card-watching")) == 1
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
    meters = sorted(s.items("meter"), key=lambda i: i.mapToScene(QtCore.QPointF(0, 0)).y())
    assert [round(m.childItems()[0].width() / m.width(), 2) for m in meters] == [0.42, 0.61, 0.12]
    origin = QtQuick.QQuickItem.TransformOrigin
    assert s.item("card-machine").property("transformOrigin") == origin.BottomRight       # it arrives from its rail's corner
    s = make(fx.ROUTE, "app")
    s.seek(s.duration)
    assert [t for t, _, _ in s.texts() if t == "Install rust and set it up"]
    assert s.item("card-now") is not None
    dots = sorted((d for d in s.items("cardDot") if d.isVisible()), key=lambda i: i.mapToScene(QtCore.QPointF(0, 0)).y())
    assert [QtGui.QColor(d.property("color")).name() for d in dots][:2] == [
        QtGui.QColor(THEME["good"]).name(), QtGui.QColor(THEME["accent"]).name()]      # done, then the step it is on
    assert QtGui.QColor(dots[2].property("color")).alpha() == 0                          # the next step is a ring
    assert len(s.items("card-now")) == 1 and len(s.items("card-watching")) == 0


def test_the_stage_card_says_a_task_and_its_step_whole_and_the_poster_card_keeps_one_line(make):
    """The stage's card is 208 wide and wraps its heading and its why to two lines; the poster's has no room to
    spare, so it keeps one line and elides. A tip's scene is never drawn with a clipped task on the stage."""
    long_task = "Install rust and set it up on this machine"
    tip = dict(fx.ROUTE, beats=[dict(b, title=long_task) if b.get("card") == "now" else b for b in fx.ROUTE["beats"]])
    s = make(tip, "app")
    s.seek(s.duration)
    card = s.item("card-now")
    assert card is not None and card.width() == 208
    said = [i for i in s.items() if i.metaObject().className().startswith("QQuickText") and i.isVisible()
            and i.property("text") == long_task]
    assert len(said) == 1 and said[0].property("lineCount") == 2 and not said[0].property("truncated")
    assert not [i for i in s.items() if i.metaObject().className().startswith("QQuickText") and i.isVisible()
                and i.property("truncated")]
    p = make(tip, "card")
    p.seek(p.duration)
    heading = [i for i in p.items() if i.metaObject().className().startswith("QQuickText") and i.isVisible()
               and i.property("text") == long_task]
    assert len(heading) == 1 and heading[0].property("lineCount") == 1 and heading[0].property("truncated")


def scene_box(s, item):
    """An item's rectangle in the scene's own coordinates: (left, top, right, bottom)."""
    p = item.mapToItem(s.scene, QtCore.QPointF(0, 0))
    return p.x(), p.y(), p.x() + item.width(), p.y() + item.height()


@pytest.mark.parametrize("density", ["card", "app"])
@pytest.mark.parametrize("tip", [fx.TIMER, fx.FOLDS], ids=lambda t: t["id"])
def test_a_folded_strip_stands_clear_of_the_pill_at_both_densities(make, tip, density):
    """One strip (a timer) and two (the desk word): the pill is what the strips leave, none runs under it."""
    s = make(tip, density)
    s.seek(s.duration)
    strips = sorted((i for i in s.items("strip") if i.isVisible()), key=lambda i: scene_box(s, i)[0])
    assert len(strips) == (1 if tip is fx.TIMER else 2)
    pill = scene_box(s, s.item("pill"))
    stage = s.item("stage").width()
    floor = 72 if density == "card" else 160
    assert pill[2] <= stage and pill[2] - pill[0] >= floor                      # the pill keeps a few words' room
    previous = 0
    for strip in strips:
        left, _, right, _ = scene_box(s, strip)
        assert left >= previous and right < pill[0], (left, right, pill)           # beside the pill, never under it
        previous = right
    words = [i for i in s.items("stripText") if i.isVisible()]
    wanted = next(b["strips"] for b in tip["beats"] if "strips" in b)
    assert sorted(w.property("text") for w in words) == sorted(wanted) and not any(w.property("truncated") for w in words)
    for strip in strips:                                                        # on the pill's row, centred on it
        assert abs((scene_box(s, strip)[1] + scene_box(s, strip)[3]) / 2 - (pill[1] + pill[3]) / 2) < 0.6


def test_no_strip_of_any_fixture_overlaps_the_pill_at_any_frame_it_stands(make):
    seen = 0
    for tip in (t for t in fx.ALL if any(b.get("fold") for b in t["beats"])):         # the scenes that fold
        for density in ("card", "app"):
            s = make(tip, density)
            for ms in range(0, int(s.duration) + 1, 250):
                s.seek(ms)
                pill = scene_box(s, s.item("pill"))
                for strip in s.items("strip"):
                    if strip.isVisible() and s.frame["fold"]["p"] >= 1:
                        seen += 1
                        assert scene_box(s, strip)[2] < pill[0], (tip["id"], density, ms)
    assert seen > 10


def test_the_pill_is_whole_before_a_fold_and_after_it_is_taken_back(make):
    tip = copy.deepcopy(fx.FOLDS)
    tip["beats"].append({"fold": False, "tag": "template"})
    for density, side in (("card", 24), ("app", 64)):
        s = make(tip, density)
        s.seek(0 if density == "app" else 1)
        pill = scene_box(s, s.item("pill"))
        stage = s.item("stage").width()
        assert (pill[0], stage - pill[2]) == (side, side)                          # centred, as it rests
        s.seek(s.duration)
        assert s.item("strip") is None
        pill = scene_box(s, s.item("pill"))
        assert (pill[0], stage - pill[2]) == (side, side)


@pytest.mark.parametrize("density", ["card", "app"])
def test_strips_too_long_for_the_stage_are_elided_and_the_pill_keeps_its_room(make, density):
    tip = {"id": "strips-long", "captions": ["a", "b", "c"], "beats": [
        {"fold": True, "tag": "template", "strips": ["step 2 of 2 and a long way to go", "1 counting down",
                                                      "nothing else to say about it"]}]}
    s = make(tip, density)
    s.seek(s.duration)
    pill = scene_box(s, s.item("pill"))
    strips = [i for i in s.items("strip") if i.isVisible()]
    assert len(strips) == 3 and max(scene_box(s, i)[2] for i in strips) < pill[0]
    assert pill[2] - pill[0] >= (72 if density == "card" else 160)
    assert any(i.property("truncated") for i in s.items("stripText"))


def test_a_keycap_pressed_while_strips_stand_is_not_drawn_over_them(make):
    tip = copy.deepcopy(fx.FOLDS)
    tip["beats"].append({"key": "enter"})
    s = make(tip, "app")                                                             # a poster has no keycap
    s.seek(beat_start(tip, len(tip["beats"]) - 1) + 180)
    key = scene_box(s, s.item("key"))
    strips = [i for i in s.items("strip") if i.isVisible()]
    assert len(strips) == 2
    for strip in strips:
        left, top, right, bottom = scene_box(s, strip)
        assert key[0] >= right or key[2] <= left or key[3] <= top or key[1] >= bottom
    pill = scene_box(s, s.item("pill"))
    assert key[0] > pill[2] - 1 and key[2] <= s.item("stage").width()                # in the margin on the right
    s.seek(beat_start(tip, 3) + 180)                                                 # before the fold: on the left, as ever
    key = scene_box(s, s.item("key"))
    assert key[2] <= scene_box(s, s.item("pill"))[0] + 1


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
    start = beat_start(fx.DISK, 3)
    s.seek(start + 300 + 100)                                             # bars grow one after another
    grow = [b["grow"] for b in s.frame["pic"]["bars"]]
    assert 0 < grow[0] < 1 and grow[1] == 0 and grow[2] == 0
    s.seek(s.duration)
    assert len(s.items("bar")) == 3 and [b["grow"] for b in s.frame["pic"]["bars"]] == [1, 1, 1]
    bars = sorted(s.items("bar"), key=lambda i: i.mapToScene(QtCore.QPointF(0, 0)).y())
    fills = [b.childItems()[0] for b in bars]
    assert [round(f.width() / b.width(), 2) for f, b in zip(fills, bars, strict=True)] == [0.62, 0.91, 0.12]
    img = s.image()

    def box_of(item):
        p = item.mapToScene(QtCore.QPointF(0, 0))
        return int(p.x()) + 4, int(p.y()), int(p.x() + item.width() * 0.9), int(p.y() + item.height())

    assert colour_count(img, "warn", 14, box_of(bars[1])) > 20 and colour_count(img, "warn", 14, box_of(bars[0])) == 0
    s.snap("app-end")


@pytest.mark.parametrize("density, chip_h, floor", [("card", 18, 12), ("app", 26, 12)])
def test_an_ask_that_waits_is_a_grey_chip_between_the_line_and_the_pill(make, density, chip_h, floor):
    kept = beat_of(fx.WAITS, "wait", "paused")
    waiting = {**fx.WAITS, "id": "fixture-waiting", "beats": fx.WAITS["beats"][:kept + 1]}   # it ends on the chip
    s = make(waiting, density)
    if density == "app":
        s.seek(beat_start(waiting, kept) - 1)
        assert s.frame["queue"] is None and s.item("queueChip") is None       # nothing waits before it is asked
        s.seek(beat_start(waiting, kept) + 400)
    else:
        s.seek(0)                                                              # the poster is the last frame
    assert s.frame["queue"]["asks"] == ["tidy my downloads"] and s.frame["queue"]["wait"] == "paused"
    chip, pill, line = s.item("queueChip"), s.item("pill"), s.item("line")
    assert chip is not None and chip.height() == chip_h
    top = chip.mapToItem(s.scene, QtCore.QPointF(0, 0)).y()
    assert top + chip.height() <= pill.y()                                       # above the pill
    assert line is None or line.mapToItem(s.scene, QtCore.QPointF(0, 0)).y() + line.height() <= top   # the line above it
    texts = {n: (t, size) for t, size, n in s.texts() if n in ("queueWait", "queueAsk")}
    assert texts["queueWait"][0] == "paused" and texts["queueAsk"][0] == "tidy my downloads"
    assert min(size for _, size in texts.values()) >= floor
    ask = next(i for i in s.items("queueAsk") if i.isVisible())
    assert not ask.property("truncated")                                       # the ask is whole
    s.snap(f"queue-{density}")
    whole = make(fx.WAITS, "app")
    whole.seek(whole.duration)
    assert whole.frame["queue"] is None and whole.item("queueChip") is None   # the asks run, the chips go


def test_two_waiting_asks_share_the_line_and_a_chip_without_a_wait_says_next(make):
    tip = {"id": "fixture-two-waits", "captions": ["a", "b", "c"], "beats": [
        {"stone": "resting"}, {"queue": ["tidy my downloads", "rename the photos from the trip to Lisbon"],
                               "tag": "typed"}, {"hold": 300}]}
    for density in ("card", "app"):
        s = make(tip, density)
        s.seek(s.duration)
        chips = sorted((i for i in s.items("queueChip") if i.isVisible()),
                       key=lambda i: i.mapToItem(s.scene, QtCore.QPointF(0, 0)).x())
        assert len(chips) == 2 and s.frame["queue"]["wait"] == "next"
        assert [i.property("text") for i in s.items("queueWait") if i.isVisible()] == ["next", "next"]
        xs = [c.mapToItem(s.scene, QtCore.QPointF(0, 0)).x() for c in chips]
        assert all(0 <= x and x + c.width() <= s.scene.width() for x, c in zip(xs, chips, strict=True))
        assert xs[0] + chips[0].width() <= xs[1]                               # side by side, not over each other


def test_a_long_ask_in_the_pill_shows_its_end_not_its_start(make):
    words = "tidy my downloads, then rename the photos from the trip to Lisbon by the day"
    tip = {"id": "fixture-long-ask", "captions": ["a", "b", "c"], "beats": [{"type": words}, {"hold": 100}]}
    for density in ("card", "app"):
        s = make(tip, density)
        s.seek(s.duration)
        text = s.item("pillText")
        row = text.parentItem()                                               # the row the words and the caret sit in
        box = row.parentItem()                                                # the clip that is the pill's field
        assert box.property("clip") and row.width() > box.width()             # longer than the field ...
        assert row.x() < 0 and abs(row.x() + row.width() - box.width()) <= 2.5    # ... so its end is in view
        assert text.property("text") == words


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
    make(tip, "app", True).snap("stills")


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
        still = make(tip, "app", True)                                  # and so are the reduced-motion stills
        assert min(size for _, size, _ in still.texts()) >= 12, tip["id"]


def inter_here():
    """Where Inter is not installed the widths here are another font's, so a fit test cannot say anything."""
    return "Inter" in QtGui.QFontDatabase.families()


def test_a_line_and_a_row_are_whole_at_every_density_and_in_the_stills(make, app):
    """The same loop is the check for a tip's own lines: nothing a scene shows is elided."""
    if not inter_here():
        pytest.skip("Inter is not installed here, so widths cannot be measured")
    for tip in fx.ALL:
        for density, reduced in (("card", False), ("app", False), ("app", True)):
            s = make(tip, density, reduced)
            for ms in range(0, int(s.duration) + 1, 400):
                s.seek(ms)
                for item in s.items("lineText"):
                    if item.isVisible() and item.property("text"):
                        where = (tip["id"], density, ms, item.property("text"))
                        assert not item.property("truncated"), where
                        assert item.property("lineCount") <= 4, where
                        if s.frame["stone"]["face"] == "working":
                            assert item.property("lineCount") == 1, where
                for item in s.items("windowRowLabel"):
                    if item.isVisible() and item.property("text"):
                        assert not item.property("truncated"), (tip["id"], density, ms, item.property("text"))
            for ms in (s.duration,):
                s.seek(ms)
                for box in s.items("line"):
                    if box.isVisible():
                        assert box.y() + box.height() <= s.item("pill").y(), (tip["id"], density)    # never over the pill


def test_a_line_is_one_line_while_the_stone_works_and_up_to_four_otherwise_growing_up_from_the_pill(make):
    """As the real line (shell/StatusLine.qml: one while working, four otherwise): a long reply is shown whole."""
    undo = next(b["line"] for b in fx.UNDO["beats"] if "line" in b)
    for density, one, gap in (("card", 22.0, 5), ("app", 36.0, 8)):
        s = make(fx.UNDO, density)
        s.seek(s.duration)
        text, box, pill = s.item("lineText"), s.item("line"), s.item("pill")
        assert text.property("text") == undo and not text.property("truncated")
        assert 2 <= text.property("lineCount") <= 4 and box.height() > one
        assert abs(box.y() + box.height() - (pill.y() - gap)) < 0.01       # its foot is where a one-line line's is
    # while the stone works it is one line and what does not fit is elided, as in the real bar
    tip = {"id": "long-working", "captions": ["a", "b", "c"],
           "beats": [{"stone": "working", "line": undo, "tag": "template"}, {"hold": 300}]}
    for density, one in (("card", 22.0), ("app", 36.0)):
        s = make(tip, density)
        text, box = s.item("lineText"), s.item("line")
        assert text.property("lineCount") == 1 and text.property("truncated") and box.height() == one


def test_a_tall_line_lifts_the_cards_and_the_picture_it_would_otherwise_cover(make):
    undo = next(b["line"] for b in fx.UNDO["beats"] if "line" in b)
    tip = {"id": "tall", "captions": ["a", "b", "c"], "beats": [
        {"card": "machine", "tag": "template", "rows": [["memory", 40]]},
        {"card": "now", "tag": "template", "title": "Install", "rows": [["a", "current"]]},
        {"picture": "P", "tag": "template", "boxes": ["a", "b"]},
        {"stone": "done", "line": undo, "tag": "template"}]}
    for density in ("card", "app"):
        s = make(tip, density)
        s.seek(s.duration)
        box = s.item("line")
        top = box.y()
        for name in ("card-machine", "card-now", "picture"):
            item = s.item(name)
            assert item.mapToItem(s.scene, QtCore.QPointF(0, 0)).y() + item.height() <= top + 0.01, (density, name)


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


def test_the_stills_are_at_full_size_and_wrap_where_three_do_not_fit_across(make):
    """12 px text is the card's floor and the stills hold it: 576 px takes two across and one under."""
    s = make(fx.BUILD, "app", reduced=True)
    assert s.scene.width() == 576.0

    def corners():
        return [s.item(f"still-{i}").mapToItem(s.scene, QtCore.QPointF(0, 0)) for i in range(3)]

    assert all(s.item(f"still-{i}").property("scale") == 1 for i in range(3))
    assert min(size for _, size, _ in s.texts()) >= 12
    a, b, c = corners()
    assert a.y() == b.y() and a.x() < b.x() and c.x() == a.x() and c.y() > a.y()           # two, then one
    assert s.scene.height() >= 2 * 153 and s.scene.implicitHeight() == s.scene.height()
    s.set(width=840)
    a, b, c = corners()
    assert a.y() == b.y() == c.y() and a.x() < b.x() < c.x()                              # all three across
    assert all(s.item(f"still-{i}").property("scale") == 1 for i in range(3)) and s.scene.height() < 200
    s.set(width=200)                                                      # a host narrower than one card shrinks them
    assert s.item("still-0").property("scale") == 200 / 272
    s.set(width=576)
    assert min(size for _, size, _ in s.texts()) >= 12


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
    for t in (200, 900, 1500, 2500, 3500):                              # pictures of the scene in motion
        s.seek(t)
        s.snap(f"app-{t}")
    cap = s.item("caption-0")
    assert cap.mapToItem(s.scene, QtCore.QPointF(0, 0)).y() >= 324          # under the stage
    assert cap.property("font").pixelSize() == 12


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
    places = [st.mapToItem(s.scene, QtCore.QPointF(0, 0)) for st in stills]
    order = sorted(range(3), key=lambda i: (places[i].y(), places[i].x()))
    assert order == [0, 1, 2] and len({(p.x(), p.y()) for p in places}) == 3        # in reading order, none on another
    caps = [s.item(f"still-caption-{i}").property("text") for i in range(3)]
    assert caps == ["You ask", "It builds", "You have it"]
    assert s.item("captions") is None                                  # the row under a stage is not drawn
    img = s.image()
    pieces = [img.copy(12 + int(p.x()), 12 + int(p.y()), 272, 153) for p in places]
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


def test_a_phase_that_ends_on_enter_is_drawn_before_the_key_lands(make):
    tip = {"id": "enter-first", "captions": ["a", "b", "c"], "beats": [
        {"type": "hello"}, {"key": "enter"},
        {"stone": "working", "line": "On it", "tag": "template", "phase": 1}, {"hold": 400},
        {"line": "Done.", "tag": "model", "phase": 2, "stone": "done"}]}
    assert tip_scene.validate(tip) == []
    s = make(tip, "app", reduced=True)
    assert s.item("still-0").findChild(QtCore.QObject, "pillText").property("text") == "hello"    # unsent
    assert s.item("still-1").findChild(QtCore.QObject, "pillText").property("text") == ""            # sent: the words left


def test_reduced_motion_plays_nothing_and_settles_at_once(make):
    s = make(fx.BUILD, "app", reduced=True)
    s.call("play")
    assert not s.prop("playing") and running_things(s.win) == []
    assert s.prop("t") == s.duration
    s.pump(0.1)
    assert s.win.property("finishedCount") == 1
    s.seek(0)                                                       # the stills are the payoff, whatever t says
    assert s.frame["ended"] is True and s.prop("phase") == 2 and s.frame["line"]["text"] == "Made Tickets. It is open now."


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


def test_play_at_the_end_starts_from_the_top(make):
    s = make(fx.SHORT, "app")
    s.seek(s.duration)
    s.call("play")
    assert s.prop("playing") and s.prop("t") < s.duration / 2
    s.pump(1.6)
    assert not s.prop("playing") and s.prop("t") == s.duration and s.win.property("finishedCount") == 1


def test_a_scene_with_no_properties_is_a_card_with_motion_on_by_default(make):
    s = make(fx.BUILD, None)
    assert (s.prop("density"), s.prop("reducedMotion")) == ("card", False)
    assert (s.scene.width(), s.scene.height()) == (272.0, 153.0) and s.prop("poster") is True
    s.call("play")                                                  # a poster: it settles at once and never plays
    assert not s.prop("playing") and s.prop("t") == s.duration
    s.set(density="app")                                            # and as a stage with motion on it plays
    s.seek(0)
    s.call("play")
    assert s.prop("playing") and running_things(s.win) == ["QQuickNumberAnimation"]


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

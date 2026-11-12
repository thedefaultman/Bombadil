"""Reduced motion: one switch, `Theme.reducedMotion`, read by everything that moves (docs/MOTION.md).

The switch is set from BOMBADIL_REDUCE_MOTION=1 by the shell (shell.qml) and by the app runtime
(appkit/engine.py). These tests prove that: the Theme's default and what its motion tokens do, the runtime
setting it through real QML (an app, `bombadil-app check`, a hot reload), the desk's fold, strips, pill
width, wash and ring going instant while they still animate with the switch off, the kit's spinner and
indeterminate bar pulsing instead of moving, and a guard that scans the QML for an animation that forgot
the rule. A QML singleton is one instance per engine, so a test sets it on its own engine and the next
test's engine starts from false; each test that sets it still puts it back.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from test_appkit_kit import run as run_kit
from test_appkit_runtime import ITEM_QML, drive, make_app
from test_desk_cards_qml import NEEDS, Cards
from test_desk_cards_qml import now as now_card
from test_desk_cards_qml import rows as rows_card
from test_desk_qml import TWO, Desk, desk_msg
from test_stone_qml import Mark

from bombadil.appkit import engine as kit_engine

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)

ROOT = Path(__file__).resolve().parents[1]
THEME_QML = ROOT / "share" / "qml" / "Bombadil" / "Theme.qml"
SHELL_QML = ROOT / "shell" / "shell.qml"


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


def theme_of(engine):
    """The engine's Theme singleton (made on first ask, the way the runtime does)."""
    theme = engine.singletonInstance("Bombadil", "Theme")
    assert isinstance(theme, QtCore.QObject), "the kit is not on the import path"
    return theme


def reduce(engine, on=True):
    theme_of(engine).setProperty("reducedMotion", on)


@pytest.fixture
def desk(app, tmp_path):
    d = Desk(app, tmp_path)
    d.send(type="status", busy=False, provider="claude", queue=[])
    yield d
    reduce(d.engine, False)
    d.win.close()
    d.engine.deleteLater()
    d.pump()


@pytest.fixture
def cards(app, tmp_path):
    c = Cards(app, tmp_path)
    yield c
    reduce(c.engine, False)
    c.close()


MODES = pytest.mark.parametrize("reduced", [False, True], ids=["motion", "reduced"])


# -- the Theme --

def test_the_theme_is_not_reduced_until_something_sets_it(app, tmp_path):
    qml = tmp_path / "t.qml"
    qml.write_text(textwrap.dedent("""\
        import QtQuick
        import Bombadil
        Item {
            objectName: "probe"
            property bool reduced: Theme.reducedMotion
            property var tokens: [Theme.fast, Theme.normal, Theme.slow, Theme.duration(150), Theme.pulseLow]
        }
        """))
    engine = QtQml.QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    comp = QtQml.QQmlComponent(engine, QtCore.QUrl.fromLocalFile(str(qml)))
    probe = comp.create()
    assert probe is not None, [e.toString() for e in comp.errors()]

    def tokens():
        return [round(float(t), 2) for t in probe.property("tokens").toVariant()]

    assert probe.property("reduced") is False
    assert tokens() == [120, 200, 300, 150, 0.3]              # the design's durations, untouched
    reduce(engine, True)
    assert probe.property("reduced") is True                  # a binding to it follows
    assert tokens() == [0, 0, 0, 0, 0.3]                      # every duration is instant; the pulse level stays
    reduce(engine, False)
    assert probe.property("reduced") is False and tokens() == [120, 200, 300, 150, 0.3]
    assert not warnings, warnings


def test_the_theme_switch_is_writable_and_false_by_default():
    # Writable (not readonly): the shell and the runtime write it. What the tokens do with it is the test above.
    text = THEME_QML.read_text()
    assert re.search(r"^\s*property bool reducedMotion: false\b", text, re.MULTILINE)


# -- the shell and the runtime set it --

def test_the_shell_sets_the_theme_from_the_variable_and_hands_it_on(app, tmp_path):
    shell = SHELL_QML.read_text()
    assert 'Quickshell.env("BOMBADIL_REDUCE_MOTION") === "1"' in shell
    binding = re.search(r"^\s*(Binding \{ target: Kit\.Theme; property: \"reducedMotion\".*\})\s*$", shell,
                        re.MULTILINE)
    assert binding, "shell.qml does not bind Kit.Theme.reducedMotion"
    # The stone and the wallpaper follow the Theme now, not the shell's own copy of the variable.
    assert re.search(r"Wallpaper \{[^}]*\breducedMotion: Kit\.Theme\.reducedMotion\b", shell)
    assert re.search(r"^\s*reducedMotion: Kit\.Theme\.reducedMotion$", shell, re.MULTILINE)   # the stone, at least
    assert "reducedMotion: root.reducedMotion" not in shell

    # The very Binding line, in a root that has the shell's property: Quickshell is not needed to see that a
    # Binding on the Theme singleton lands.
    for value in ("true", "false"):
        qml = tmp_path / f"shell-{value}.qml"
        qml.write_text(f"import QtQuick\nimport Bombadil as Kit\nItem {{ id: root\n"
                       f"    readonly property bool reducedMotion: {value}\n    {binding.group(1)}\n"
                       f"    property bool seen: Kit.Theme.reducedMotion\n}}\n")
        engine = QtQml.QQmlApplicationEngine()
        comp = QtQml.QQmlComponent(engine, QtCore.QUrl.fromLocalFile(str(qml)))
        root = comp.create()
        assert root is not None, [e.toString() for e in comp.errors()]
        assert theme_of(engine).property("reducedMotion") is (value == "true")
        assert root.property("seen") is (value == "true")


@pytest.mark.parametrize("value,expected", [("1", True), ("0", False), ("", False), ("true", False), (None, False)])
def test_the_runtime_sets_the_theme_from_the_variable(home, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("BOMBADIL_REDUCE_MOTION", raising=False)
    else:
        monkeypatch.setenv("BOMBADIL_REDUCE_MOTION", value)
    assert kit_engine.reduced_motion() is expected
    main = ITEM_QML.replace('text: "hello v1 "', 'text: "motion " + Theme.reducedMotion + " " + Theme.fast + " "')
    make_app("demo", {"main.qml": main})
    out = drive("demo", """
        out["start"] = [host.start(), label()]
        write("main.qml", (d / "main.qml").read_text().replace("motion", "again"))
        out["reload"] = [wait_for(lambda: reloads() == 1), label()]
        from PySide6.QtCore import QObject
        theme = host.engine.singletonInstance("Bombadil", "Theme")
        out["theme"] = [isinstance(theme, QObject), theme.property("reducedMotion")]
    """)
    shown = f"{str(expected).lower()} {0 if expected else 120} "
    assert out["start"] == [True, f"motion {shown}"]
    assert out["reload"] == [True, f"again {shown}"]            # a reload keeps the switch
    assert out["theme"] == [True, expected]


def test_check_still_works_with_reduced_motion_and_the_app_sees_it(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_REDUCE_MOTION", "1")
    main = ITEM_QML.replace('console.log("loaded")', 'console.log("reduced " + Theme.reducedMotion)')
    make_app("demo", {"main.qml": main, "app.toml": 'title = "Demo"\n'})
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), "check", "demo"],
                       capture_output=True, text=True, env=env, timeout=90, check=False)
    result = json.loads(r.stdout)
    assert r.returncode == 0 and result["ok"] and result["errors"] == []
    assert result["console"] == ["reduced true"]


def test_a_kit_that_cannot_be_found_leaves_the_runtime_working(app):
    saved = os.environ.pop("QML2_IMPORT_PATH", None)
    try:
        engine = QtQml.QQmlApplicationEngine()      # no import path of its own, and none in the environment
        assert kit_engine.set_reduced_motion(engine) is False
    finally:
        if saved is not None:
            os.environ["QML2_IMPORT_PATH"] = saved


# -- the kit's own motion --

BUSY = """
import QtQuick.Controls
Item {
    width: 220; height: 80
    property bool reduced: Theme.reducedMotion
    property bool going: true
    BusyIndicator { id: busy; running: parent.going; width: 24; height: 24 }
    ProgressBar { id: bar; indeterminate: parent.going; y: 40; width: 200 }
    property Item arc: busy.contentItem.children[0]
    property Item segment: bar.contentItem.children[1]
}
"""

SAMPLE = """
root_arc, root_segment = root.property("arc"), root.property("segment")
samples = []
for _ in range(10):
    spin(110)
    samples.append([round(root_arc.property("rotation"), 1), round(root_arc.property("opacity"), 2),
                    round(root_segment.property("opacity"), 2), round(root_segment.property("width"), 1)])
out["track"] = root_segment.parentItem().property("width")
out["reduced"] = root.property("reduced")
out["rotations"] = sorted({s[0] for s in samples})
out["arc_opacity"] = [min(s[1] for s in samples), max(s[1] for s in samples)]
out["segment_opacity"] = [min(s[2] for s in samples), max(s[2] for s in samples)]
out["segment_widths"] = len({s[3] for s in samples})
out["segment_width"] = samples[-1][3]
"""

# Stop them in the middle of a pulse: the opacity they were left at must come back to 1.
STOP = """
root_arc, root_segment = root.property("arc"), root.property("segment")
out["dipped"] = wait_until(lambda: root_arc.property("opacity") < 0.8 and root_segment.property("opacity") < 0.8)
root.setProperty("going", False)
spin(100)
out["after"] = [root_arc.property("opacity"), root_segment.property("opacity")]
"""


def test_a_spinner_and_an_indeterminate_bar_move_without_the_switch(home, monkeypatch):
    monkeypatch.delenv("BOMBADIL_REDUCE_MOTION", raising=False)
    out = run_kit(home, BUSY, SAMPLE)
    assert out["reduced"] is False
    assert len(out["rotations"]) > 3                         # the arc turns
    assert out["arc_opacity"] == [1, 1] and out["segment_opacity"] == [1, 1]
    assert out["segment_widths"] > 3                         # the bar's segment sweeps


def test_a_spinner_and_an_indeterminate_bar_pulse_under_reduced_motion(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_REDUCE_MOTION", "1")
    out = run_kit(home, BUSY, SAMPLE)
    assert out["reduced"] is True
    assert out["rotations"] == [0]                           # the arc holds still
    assert out["segment_widths"] == 1                        # so does the segment, and it is there to be seen
    assert 0 < out["segment_width"] < out["track"], out
    for low, high in (out["arc_opacity"], out["segment_opacity"]):
        assert low < 0.6 and high > 0.8, out                 # and both pulse, one second a round, as the stone does


def test_a_pulse_stopped_in_the_middle_leaves_the_spinner_and_the_bar_whole(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_REDUCE_MOTION", "1")
    out = run_kit(home, BUSY, STOP)
    assert out["dipped"] is True
    assert out["after"] == [1, 1]                            # not left dim at wherever the pulse was


# -- the desk --

@MODES
def test_a_card_folds_and_unfolds_instantly_under_reduced_motion(desk, reduced):
    reduce(desk.engine, reduced)
    desk.set("foldMs", 400)                                  # long enough to see a fold on its way
    desk.set("unfoldDelayMs", 60)
    desk.turn(1, steps=TWO)
    desk.pump(0.6)
    card = desk.item("deskCard-now")
    assert desk.faces["now"] == "full" and card.property("opacity") == 1 and card.property("scale") == 1
    slot = desk.slots["now"]
    desk.cover((slot["x"] + 40, slot["y"] + 10, 400, 300))
    assert desk.faces["now"] == "strip"
    desk.pump(0.1)
    if reduced:
        assert card.property("opacity") == 0 and card.property("scale") == 0.6 and not card.isVisible()
    else:
        assert 0 < card.property("opacity") < 1 and card.isVisible()          # on its way out
    desk.pump(0.6)
    assert card.property("opacity") == 0 and not card.isVisible()
    desk.cover()
    desk.pump(0.12)                                          # the delay has passed: the card is on its way back
    assert desk.faces["now"] == "full"
    if reduced:
        assert card.property("opacity") == 1 and card.property("scale") == 1 and card.isVisible()
    else:
        assert 0 < card.property("opacity") < 1
    desk.pump(0.6)
    assert card.property("opacity") == 1 and card.property("scale") == 1


@MODES
def test_the_strip_a_card_folds_into_is_there_at_once_under_reduced_motion(desk, reduced):
    reduce(desk.engine, reduced)
    desk.turn(1, steps=TWO)
    desk.set("foldMs", 400)
    desk.send(**desk_msg(folded=True))
    strip = desk.item("deskStrip-now")
    if reduced:
        assert strip.property("opacity") == 1
    else:
        assert strip.property("opacity") < 1
    desk.pump(0.6)
    assert strip.property("opacity") == 1


@MODES
def test_the_pill_changes_width_instantly_under_reduced_motion(desk, reduced):
    reduce(desk.engine, reduced)
    desk.set("pillAnimMs", 2000)                             # the shell's is 150 ms; slow, a width on its way is seen
    assert desk.prop("pillWidth") == 900 == desk.prop("pillTarget")
    desk.cover((700, 300, 400, 300))                         # a window on the stage: the pill narrows
    assert desk.prop("pillTarget") == 360
    if reduced:
        assert desk.prop("pillWidth") == 360
    else:
        assert 360 < desk.prop("pillWidth") < 900
    desk.cover()                                             # the window goes: the pill widens again
    desk.pump(0.1)
    assert desk.prop("pillTarget") == 900
    assert desk.prop("pillWidth") == 900 if reduced else desk.prop("pillWidth") < 900   # climbing, or there


@MODES
def test_a_new_row_washes_its_colour_only_with_motion(cards, reduced):
    reduce(cards.engine, reduced)
    (card,) = cards.show(("rows", "needs", rows_card(*NEEDS)))
    assert card.property("washLevel") == 0
    cards.call(card, "wash", "#5b9bd5")
    if reduced:
        assert card.property("washLevel") == 0               # the row is there; no wash behind it
    else:
        assert card.property("washLevel") > 0.5
        cards.pump(0.8)
        assert card.property("washLevel") == 0


def ring_samples(cards, ring, n=8):
    seen = []
    for _ in range(n):
        cards.pump(0.12)
        seen.append((round(ring.property("scale"), 3), round(ring.property("opacity"), 3)))
    return seen


def test_the_ring_breathes_with_motion_and_holds_still_under_reduced_motion(cards):
    (card,) = cards.show(("now", "now", now_card()))
    ring = cards.one(card, "nowRing")
    breathing = ring_samples(cards, ring)
    assert max(s for s, _ in breathing) - min(s for s, _ in breathing) > 0.3
    reduce(cards.engine, True)                               # switched on while it breathes: it comes to rest
    cards.pump(0.1)
    assert ring_samples(cards, ring) == [(1.2, 0.6)] * 8
    reduce(cards.engine, False)                              # and breathes again when switched off
    again = ring_samples(cards, ring)
    assert max(s for s, _ in again) - min(s for s, _ in again) > 0.3


def test_a_card_built_under_reduced_motion_has_a_still_ring(cards):
    reduce(cards.engine, True)
    (card,) = cards.show(("now", "now", now_card()))
    ring = cards.one(card, "nowRing")
    assert ring.isVisible() and ring_samples(cards, ring, 4) == [(1.2, 0.6)] * 4


DIAGRAM = """
import QtQuick
import Bombadil
Item {
    width: 900; height: 600
    Diagram { id: d; objectName: "diagram"; x: 14; y: 14; width: parent.width - 28 }
}
"""


def find_item(root, name):
    todo = [root]
    while todo:
        item = todo.pop()
        if item.objectName() == name:
            return item
        todo.extend(item.childItems())
    return None


@MODES
def test_the_diagram_moves_its_boxes_only_with_motion(app, tmp_path, reduced):
    from bombadil import cards
    qml = tmp_path / "diagram.qml"
    qml.write_text(DIAGRAM)
    engine = QtQml.QQmlApplicationEngine()
    comp = QtQml.QQmlComponent(engine, QtCore.QUrl.fromLocalFile(str(qml)))
    root = comp.create()
    assert root is not None, [e.toString() for e in comp.errors()]
    reduce(engine, reduced)

    def pump(ms):
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(ms, loop.quit)
        loop.exec()

    diagram = find_item(root, "diagram")
    spec, errors = cards.validate_diagram({"shape": "chain", "title": "How a VPN works",
                                           "nodes": [{"label": "Laptop"}, {"label": "Tunnel"}, {"label": "Internet"}]})
    assert spec is not None, errors
    diagram.setProperty("spec", spec)
    pump(400)
    box = find_item(root, "box-n2")
    start = (box.property("x"), box.property("width"))
    diagram.setProperty("width", 300)                        # the row narrows: the box moves and shrinks
    seen = []
    for _ in range(8):
        pump(20)
        seen.append((box.property("x"), box.property("width")))
    pump(400)
    final = (box.property("x"), box.property("width"))
    assert final != start
    between = [v for v in seen if v not in (start, final)]
    assert (not between) if reduced else between, (start, seen, final)
    reduce(engine, False)


# -- the stone, and the shell's other changes --

def test_the_stone_leans_at_once_under_reduced_motion(app, tmp_path):
    mark = Mark(app, tmp_path)
    mark.stone.setProperty("face", "listening")
    assert mark.stone.property("lean") < 6                   # on its way, 240 ms
    mark.pump(0.5)
    assert mark.stone.property("lean") == 6
    mark.set(face="rest")
    mark.pump(0.5)
    mark.set(reducedMotion=True)
    mark.stone.setProperty("face", "listening")
    assert mark.stone.property("lean") == 6                  # no time to get there


# -- the guard: every animation in the QML honours the switch --

# Waits that are not movement, so the rule does not apply to them: (file name, the text that matches) ->
# (how many times it stands in that file, why). The count keeps an entry from allowing a second wait.
ALLOWED = {
    ("ScrollBar.qml", "duration: 600"): (1, "a wait before the bar hides; the fade after it uses Theme.normal"),
    ("ScrollIndicator.qml", "duration: 600"): (1, "a wait before the indicator hides; the fade uses Theme.normal"),
    # A working coding session's dot pulses its opacity, 1 to 0.3 and back: the pulse is what the stone falls back
    # to under reduced motion, so it is already the still-friendly form and nothing in it moves.
    ("SessionChips.qml", "loops: Animation.Infinite"): (1, "the working dot's opacity pulse, the form the stone falls back to"),
    ("SessionChips.qml", "duration: 600"): (2, "the two halves of that pulse"),
    # The tip scene's own clock: its duration is the scene's length, set by play(), which never starts it under
    # reducedMotion (it settles on the last frame, and the app stage draws three stills instead).
    ("TipScene.qml", "NumberAnimation without a duration"): (1, "the scene clock, never started under reducedMotion"),
}

MOTION = re.compile(r"(?:^|[\s:;{}])(Behavior|Transition|[A-Z]\w*(?:Animation|Animator))(?:\s+on\s+[\w.]+)?\s*$")
# An animation that carries a value over time by itself: it needs a duration that honours the switch, or a gate.
LEAF = re.compile(r"^(?:Number|Color|Property|Rotation|Vector3d|Anchor|Path|Pause|Smoothed|Spring)Animation$"
                  r"|^\w+Animator$")
DURATION = re.compile(r"\bduration\s*:\s*([^\n;]*)")
INFINITE = re.compile(r"\bloops\s*:\s*Animation\.Infinite\b")
GATE = re.compile(r"\b(?:running|enabled|when)\s*:\s*([^\n;]*)")
HONOURS = re.compile(r"Theme\.|reducedMotion")      # a duration from the Theme, or one that reads the switch


def blank(text):
    """The QML with its comments and the insides of its strings blanked, so a brace in one does not count."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append(c + re.sub(r"[^\n]", " ", text[i + 1:j]) + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def brace_blocks(clean):
    """[(open, close, header)] for every { } pair; the header is what stands before the brace on its statement."""
    found, stack = [], []
    for i, c in enumerate(clean):
        if c == "{":
            start = max(clean.rfind(ch, 0, i) for ch in "\n;{}") + 1
            stack.append((i, clean[start:i]))
        elif c == "}" and stack:
            opened, header = stack.pop()
            found.append((opened, i, header))
    return found


def own(clean, opened, closed):
    """A block's own lines: its text without the blocks inside it."""
    depth, out = 0, []
    for c in clean[opened:closed + 1]:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 1:
            out.append(c)
    return "".join(out)


def forgetful(text, name="x.qml", used=None):
    """What in this QML animates without honouring the switch: the (what, line) of each. A leaf animation
    (`NumberAnimation`, `ColorAnimation`, an Animator, ...) passes when its `duration` is 0, comes from the
    Theme (`Theme.fast`, `Theme.duration(...)`) or reads `reducedMotion`; any other duration, literal or from a
    property or a library (`T.washMs`), no duration at all, or a spring, fails. An endless loop fails. Either
    passes if the animation or one around it is gated: its own `running`, `enabled` or `when` reads
    `reducedMotion`. A wait in ALLOWED passes; `used` counts them. A `reducedMotion` somewhere else in the
    same block gates nothing."""
    clean = blank(text)
    blocks = sorted(((o, c, MOTION.search(h).group(1)) for o, c, h in brace_blocks(clean) if MOTION.search(h)))
    out = []
    for o, c, kind in blocks:
        mine = own(clean, o, c)
        found = []
        if LEAF.search(kind):
            if (d := DURATION.search(mine)) is None:
                found.append(f"{kind} without a duration")
            elif not (re.fullmatch(r"0+(?:\.0*)?", d.group(1).strip()) or HONOURS.search(d.group(1))):
                found.append(f"duration: {d.group(1).strip()}")
        if INFINITE.search(mine):
            found.append("loops: Animation.Infinite")
        if not found:
            continue
        around = [(oo, cc) for oo, cc, _ in blocks if oo <= o and c <= cc]
        if any("reducedMotion" in g for oo, cc in around for g in GATE.findall(own(clean, oo, cc))):
            continue
        for what in found:
            if (name, what) in ALLOWED:
                if used is not None:
                    used[(name, what)] = used.get((name, what), 0) + 1
                continue
            out.append((what, clean.count("\n", 0, o) + 1))
    return out


def qml_files():
    found = sorted(ROOT.glob("shell/*.qml")) + sorted(ROOT.glob("share/qml/Bombadil/**/*.qml"))
    return found + sorted(ROOT.glob("share/apps/**/*.qml"))


def test_no_animation_ignores_reduced_motion():
    used = {}
    bad = {str(p.relative_to(ROOT)): found
           for p in qml_files() if (found := forgetful(p.read_text(), p.name, used))}
    assert not bad, (
        f"{bad}: an animation that does not honour reduced motion. Take its duration from Theme.fast, normal "
        "or slow, or wrap it in Theme.duration(ms), or gate it on Theme.reducedMotion; docs/MOTION.md says "
        "how. A wait that is not a movement goes in ALLOWED, with its reason.")
    # An entry that matches nothing is stale, and one that matches more than it counts allows a new wait.
    assert used == {key: times for key, (times, _) in ALLOWED.items()}


def test_the_guard_catches_what_it_is_for():
    bad = """
        Item {
            Behavior on x { NumberAnimation { duration: 140 } }
            NumberAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite }
            transitions: Transition { ColorAnimation { duration: 90 } }
            Behavior on a { NumberAnimation { duration: T.washMs } }
            Behavior on b { NumberAnimation { duration: rail.desk.foldMs; easing.type: Easing.OutCubic } }
            Behavior on c { NumberAnimation { duration: ms } }
            Behavior on d { NumberAnimation { } }
            Behavior on e { SpringAnimation { spring: 2 } }
            Behavior on f { SmoothedAnimation { velocity: 200 } }
            Behavior on g { OpacityAnimator { duration: 100 } }
            Timer { interval: 500 }
        }"""
    assert [m for m, _ in forgetful(bad)] == [
        "duration: 140", "duration: 900", "loops: Animation.Infinite", "duration: 90", "duration: T.washMs",
        "duration: rail.desk.foldMs", "duration: ms", "NumberAnimation without a duration",
        "SpringAnimation without a duration", "SmoothedAnimation without a duration", "duration: 100"]
    # A `reducedMotion` somewhere in the same block gates nothing: only a gate on the animation, or on one
    # around it, or a duration that reads the switch.
    mixed = """
        SequentialAnimation {
            running: true
            NumberAnimation { duration: wall.reducedMotion ? 0 : 200 }
            NumberAnimation { duration: 300 }
            NumberAnimation { duration: Theme.duration(100) }
            property bool seen: Theme.reducedMotion
        }"""
    assert [m for m, _ in forgetful(mixed)] == ["duration: 300"]
    good = """
        Item {
            property int duration: 5
            Behavior on x { NumberAnimation { duration: Theme.duration(140) } }
            Behavior on y { NumberAnimation { duration: Kit.Theme.fast } }
            Behavior on w { NumberAnimation { duration: wall.reducedMotion ? 0 : 200 } }
            NumberAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite
                                          running: spinning && !Theme.reducedMotion }
            SequentialAnimation { running: Theme.reducedMotion; loops: Animation.Infinite
                                  NumberAnimation { to: 0.3; duration: 500 }
                                  NumberAnimation { to: 1; duration: 500 } }
            Behavior on v { enabled: !Theme.reducedMotion; SpringAnimation { spring: 2 } }
            Behavior on z { NumberAnimation { duration: 0 } }
            // Behavior on q { NumberAnimation { duration: 77 } }
            Text { text: "NumberAnimation { duration: 88 }" }
        }"""
    assert forgetful(good) == []
    # An allowed wait is only allowed in the file the list names, and is counted there.
    wait = "Transition { SequentialAnimation { PauseAnimation { duration: 600 } } }"
    used = {}
    assert forgetful(wait, "ScrollBar.qml", used) == [] and forgetful(wait, "Other.qml") != []
    assert used == {("ScrollBar.qml", "duration: 600"): 1}


def test_the_guard_covers_the_places_that_move():
    names = {p.name for p in qml_files()}
    assert {"DeskRail.qml", "DeskCard.qml", "DeskState.qml", "DeskStrips.qml", "Stone.qml", "Wallpaper.qml",
            "Diagram.qml", "BusyIndicator.qml", "ProgressBar.qml", "ScrollBar.qml", "main.qml"} <= names
    assert len(qml_files()) > 90
    for (name, _), (times, why) in ALLOWED.items():
        assert name in names and times >= 1 and why

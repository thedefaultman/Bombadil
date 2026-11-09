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


def test_the_theme_source_says_what_the_docs_say():
    text = THEME_QML.read_text()
    # Writable (not readonly), false by default: the shell and the runtime write it.
    assert re.search(r"^\s*property bool reducedMotion: false\b", text, re.MULTILINE)
    # The three motion tokens follow it, so `duration: Theme.fast` is enough for a component.
    for name, ms in (("fast", 120), ("normal", 200), ("slow", 300)):
        assert re.search(rf"readonly property int {name}: reducedMotion \? 0 : {ms}\b", text), name
    assert "function duration(ms) { return reducedMotion ? 0 : ms }" in text


# -- the shell and the runtime set it --

def test_the_shell_sets_the_theme_from_the_variable_and_hands_it_on(app, tmp_path):
    shell = SHELL_QML.read_text()
    assert 'Quickshell.env("BOMBADIL_REDUCE_MOTION") === "1"' in shell
    binding = re.search(r"^\s*(Binding \{ target: Kit\.Theme; property: \"reducedMotion\".*\})\s*$", shell,
                        re.MULTILINE)
    assert binding, "shell.qml does not bind Kit.Theme.reducedMotion"
    # The stone and the wallpaper follow the Theme now, not the shell's own copy of the variable.
    assert "Wallpaper { reducedMotion: Kit.Theme.reducedMotion }" in shell
    assert len(re.findall(r"^\s*reducedMotion: Kit\.Theme\.reducedMotion$", shell, re.MULTILINE)) == 1
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
        assert kit_engine.set_reduced_motion(engine, True) is False
    finally:
        if saved is not None:
            os.environ["QML2_IMPORT_PATH"] = saved


# -- the kit's own motion --

BUSY = """
import QtQuick.Controls
Item {
    width: 220; height: 80
    property bool reduced: Theme.reducedMotion
    BusyIndicator { id: busy; running: true; width: 24; height: 24 }
    ProgressBar { id: bar; indeterminate: true; y: 40; width: 200 }
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
out["reduced"] = root.property("reduced")
out["rotations"] = sorted({s[0] for s in samples})
out["arc_opacity"] = [min(s[1] for s in samples), max(s[1] for s in samples)]
out["segment_opacity"] = [min(s[2] for s in samples), max(s[2] for s in samples)]
out["segment_widths"] = len({s[3] for s in samples})
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
    assert out["segment_widths"] == 1                        # so does the segment
    for low, high in (out["arc_opacity"], out["segment_opacity"]):
        assert low < 0.6 and high > 0.8, out                 # and both pulse, one second a round, as the stone does


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

# Literal durations and endless loops the rule does not apply to, because the motion itself is not what they
# are: (file name, the text that matches) -> why.
ALLOWED = {
    ("ScrollBar.qml", "duration: 600"): "a wait before the bar hides; the fade after it uses Theme.normal",
    ("ScrollIndicator.qml", "duration: 600"): "a wait before the indicator hides; the fade after it uses Theme.normal",
}

MOTION = re.compile(r"(?:^|[\s:;{}])(Behavior|Transition|[A-Z]\w*(?:Animation|Animator))(?:\s+on\s+[\w.]+)?\s*$")
LITERAL = re.compile(r"\bduration\s*:\s*(\d+)\b|\bloops\s*:\s*Animation\.Infinite\b")


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


def forgetful(text, name="x.qml"):
    """What in this QML animates by a literal duration, or loops for ever, without the switch: the
    (match, line) of each. An animation passes when its outermost animation, Behavior or Transition
    mentions `reducedMotion` (it is gated or reads the switch) or goes through `Theme.duration(`; a
    duration from `Theme.fast`, `normal` or `slow` is not literal and honours the switch by itself."""
    clean = blank(text)
    motion = [b for b in brace_blocks(clean) if MOTION.search(b[2])]
    out = []
    for m in LITERAL.finditer(clean):
        if m.group(1) is not None and int(m.group(1)) == 0:
            continue
        around = [b for b in motion if b[0] < m.start() < b[1]]
        if not around:
            continue                                         # a `duration` that is not an animation's
        outer = min(around, key=lambda b: b[0])
        body = clean[outer[0]:outer[1]]
        if "reducedMotion" in body or "Theme.duration(" in body:
            continue
        if (name, m.group(0)) in ALLOWED:
            continue
        out.append((m.group(0), clean.count("\n", 0, m.start()) + 1))
    return out


def qml_files():
    found = sorted(ROOT.glob("shell/*.qml")) + sorted(ROOT.glob("share/qml/Bombadil/**/*.qml"))
    return found + sorted(ROOT.glob("share/apps/**/*.qml"))


def test_no_animation_ignores_reduced_motion():
    bad = {str(p.relative_to(ROOT)): found for p in qml_files() if (found := forgetful(p.read_text(), p.name))}
    assert not bad, (
        f"{bad}: an animation with a literal duration, or a loop, that does not honour reduced motion. Use "
        "Theme.fast, normal or slow, or Theme.duration(ms), or gate it on Theme.reducedMotion; docs/MOTION.md "
        "says how. A wait that is not a movement goes in ALLOWED, with its reason.")


def test_the_guard_catches_what_it_is_for():
    bad = """
        Item {
            Behavior on x { NumberAnimation { duration: 140 } }
            NumberAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite }
            transitions: Transition { ColorAnimation { duration: 90 } }
            Timer { interval: 500 }
        }"""
    assert [m for m, _ in forgetful(bad)] == ["duration: 140", "duration: 900", "loops: Animation.Infinite",
                                              "duration: 90"]
    good = """
        Item {
            property int duration: 5
            Behavior on x { NumberAnimation { duration: Theme.duration(140) } }
            Behavior on y { NumberAnimation { duration: Kit.Theme.fast } }
            Behavior on w { NumberAnimation { duration: wall.reducedMotion ? 0 : 200 } }
            NumberAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite
                                          running: spinning && !Theme.reducedMotion }
            Behavior on z { NumberAnimation { duration: 0 } }
            // Behavior on q { NumberAnimation { duration: 77 } }
            Text { text: "NumberAnimation { duration: 88 }" }
        }"""
    assert forgetful(good) == []
    # An allowed wait is only allowed in the file the list names.
    wait = "Transition { SequentialAnimation { PauseAnimation { duration: 600 } } }"
    assert forgetful(wait, "ScrollBar.qml") == [] and forgetful(wait, "Other.qml") != []


def test_the_guard_covers_the_places_that_move():
    names = {p.name for p in qml_files()}
    assert {"DeskRail.qml", "DeskCard.qml", "DeskState.qml", "DeskStrips.qml", "Stone.qml", "Wallpaper.qml",
            "Diagram.qml", "BusyIndicator.qml", "ProgressBar.qml", "ScrollBar.qml", "main.qml"} <= names
    assert len(qml_files()) > 90
    for (name, _), why in ALLOWED.items():
        assert name in names and why

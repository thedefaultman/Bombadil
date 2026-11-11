"""The Tips window (share/apps/tips/main.qml) as the person sees it: the list, the stage, and what the keys and the
button do, above all the one thing it must never do, which is run anything: a tip's button hides the window and puts
its words in the pill, and the person's own Enter runs them.

Each scenario runs in a child process (offscreen, software renderer): a QGuiApplication and the `App` singleton
exist once per process. The child runs the runtime's own `Host` on the real app folder, with the real backend, and a
fake agentd in a thread (tests/tips_app_lab.py) that lists the shipped tips and records every line it is sent, in a
temp home. It drives the window with mouse and key events (QTest), reads what is drawn from the item tree and prints
what it found as JSON for the test to judge. With BOMBADIL_SCREENS=<dir> the scenarios also save pictures there
(tips-app-<name>.png; every shipped tip plays its own scene, the fixture scenes of tests/tip_fixtures.py are only
for a row that arrives without beats).

What is held to what: the list has its sections and only the tips agentd lists; a tip plays once when it is chosen
and holds its payoff, and nothing advances or loops by itself; Replay and Space play it again; Up and Down choose;
Enter and the button hide the window first and then put exactly the tip's words in the pill, sending nothing, and a
tip with only marks does nothing; Esc hides the window; under reduced motion the stage is three stills; a tick
shows when a tip is tried and leaves the list where it was; the window says so when agentd is away and does not
change while the AI rests; every tip fits 960 x 516; and the window is neutral, with no orange of its own.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "share" / "apps" / "tips"
QML_FILES = sorted(APP.glob("*.qml"))

# Runs in the child: the helpers every scenario uses, and `boot()`, which starts the fake agentd and the window.
PRELUDE = r'''
import json, os, sys, time
from pathlib import Path
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from bombadil import tips as tips_mod
from bombadil.appkit import context, engine, runtime
import tips_app_lab as lab
from tips_app_lab import spin, wait_until

home = Path(os.environ["HOME"])
out = {}
log = []                  # "hide" and the summons, in the order they happened
fake = host = win = root = backend = theme = None
FACTS = {"full": lab.MACHINE, "few": tips_mod.Facts(ai="Claude"), "none": tips_mod.Facts()}


def boot(*, tried=(), new=(), facts="full", up=True, tips=None, state=None, status=None):
    global fake, host, win, root, backend, theme
    (home / "run").mkdir(parents=True, exist_ok=True)
    listed = tips if tips is not None else lab.listing(home, FACTS[facts], tried=tried, new=new)
    fake = lab.FakeAgentd(home / "run" / "agentd.sock", listed)
    if status:
        fake.status = status
    fake.hooks[("summon", None)] = lambda m: log.append(["summon", m])
    if up:
        fake.start()
    if state is not None:
        lab.write_state(home / "state" / "tips.json", **state)
    ctx = context.for_app("tips", check=False)
    engine.make_app(ctx)
    host = runtime.Host(ctx)
    assert host.start(), host.collector.summary()
    win = host.current_window()
    host.app.on_hide = lambda: log.append(["hide"])
    root, backend = host.root, host.backend
    theme = host.engine.singletonInstance("Bombadil", "Theme")
    spin(150)


def listed(count=None, ms=10000):
    assert wait_until(lambda: backend.connected and backend.count > 0 and (count is None or backend.count == count), ms)
    spin(200)


def walk(item):
    yield item
    for c in item.childItems():
        yield from walk(c)


def scene_rect(item):
    p = item.mapToScene(QPointF(0, 0))
    return (p.x(), p.y(), p.x() + item.width(), p.y() + item.height())


def on_screen(item):
    """Visible, inside the window, and not scrolled or clipped out of sight by an ancestor."""
    if not item.isVisible() or item.width() <= 0 or item.height() <= 0 or item.opacity() <= 0:
        return False
    x0, y0, x1, y1 = scene_rect(item)
    bx0, by0, bx1, by1 = 0, 0, win.width(), win.height()
    a = item.parentItem()
    while a is not None:
        if a.property("clip") is True:
            ax0, ay0, ax1, ay1 = scene_rect(a)
            bx0, by0, bx1, by1 = max(bx0, ax0), max(by0, ay0), min(bx1, ax1), min(by1, ay1)
        a = a.parentItem()
    return min(x1, bx1) - max(x0, bx0) > 1 and min(y1, by1) - max(y0, by0) > 1


def lineage(item):
    m, names = item.metaObject(), []
    while m is not None:
        names.append(m.className())
        m = m.superClass()
    return names


def text_items(only_visible=True):
    for it in walk(root):
        if "QQuickText" in lineage(it) and (not only_visible or on_screen(it)):
            t = it.property("text")
            if isinstance(t, str) and t != "":
                yield it


def texts(only_visible=True):
    return [it.property("text") for it in text_items(only_visible)]


def has(text):
    return text in texts()


def find(name):
    found = [it for it in walk(root) if it.objectName() == name]
    assert found, f"no item named {name}"
    return found[0]


def find_all(name):
    return [it for it in walk(root) if it.objectName() == name]


def shown(name):
    return any(on_screen(it) for it in find_all(name))


def centre(item):
    x0, y0, x1, y1 = scene_rect(item)
    return QPoint(int((x0 + x1) / 2), int((y0 + y1) / 2))


def click(item):
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(item))
    spin(60)


def click_text(text):
    hits = [it for it in text_items() if it.property("text") == text]
    assert hits, f"no visible text {text!r} among {texts()}"
    click(hits[0])


def key(k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(win, k, mod)
    spin(60)


def ev(item, code):
    from PySide6.QtQml import QQmlExpression
    return QQmlExpression(host.engine.rootContext(), item, code).evaluate()[0]


def scene():
    return find("scene")


def playing():
    return scene().property("playing") is True


def clock():
    s = scene()
    return [s.property("t"), s.property("duration")]


def held():
    t, d = clock()
    return d > 0 and t >= d and not playing()


def summons():
    return [e[1] for e in log if e[0] == "summon"]


def shot(name):
    d = os.environ.get("BOMBADIL_SCREENS")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        win.grabWindow().save(str(Path(d) / f"tips-app-{name}.png"))


def cleanup():
    host.close()
    if fake is not None:
        fake.stop()
'''


def drive(home, body: str, *, env=None, timeout=150) -> dict:
    """Runs `body` in a child that has the helpers of PRELUDE and returns what it put in `out`."""
    run_env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
               "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")])}
    for name in ("BOMBADIL_CHECK", "BOMBADIL_REDUCE_MOTION", "HYPRLAND_INSTANCE_SIGNATURE"):
        run_env.pop(name, None)
    run_env["BOMBADIL_SOCKET"] = str(home / "run" / "agentd.sock")
    run_env.update(env or {})
    script = (PRELUDE + "\ntry:\n" + textwrap.indent(textwrap.dedent(body), "    ")
              + "\nfinally:\n    cleanup()\nprint('RESULT ' + json.dumps(out))\n")
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=run_env,
                       timeout=timeout, check=False)
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"child failed (exit {r.returncode}):\n{r.stdout[-3000:]}\n{r.stderr[-4000:]}"
    return json.loads(lines[-1].removeprefix("RESULT "))


@pytest.fixture(autouse=True)
def places(home, monkeypatch):
    monkeypatch.delenv("BOMBADIL_CHECK", raising=False)
    monkeypatch.delenv("BOMBADIL_REDUCE_MOTION", raising=False)
    monkeypatch.setenv("BOMBADIL_SOCKET", str(home / "run" / "agentd.sock"))
    return home


# ---- the list ---------------------------------------------------------------------------------------------------

def test_the_list_has_its_sections_and_every_tip_agentd_lists_with_its_words_and_ticks(home):
    out = drive(home, """
        boot(tried=("make-an-app", "picture"))
        listed(20)
        shot("list")
        out["sections"] = [it.property("text") for it in find_all("section") if it.isVisible()]
        rows = [it for it in find_all("tipRow") if it.isVisible()]
        out["rows"] = [[ev(r, "line"), ev(r, "words"), ev(r, "tried")] for r in rows]
        out["backend"] = [[t["line"], t["words"] or "  ".join(t["marks"])] for t in backend.tips]
        out["ticks"] = sum(1 for it in find_all("rowTick") if on_screen(it))
        out["tick_texts"] = [t for t in texts() if t == "tried"]
        out["front"] = [backend.selected, find("tipLine").property("text"), find("wordsButton").property("text")]
        out["selected_rows"] = [ev(r, "line") for r in rows if ev(r, "selected")]
        out["head"] = texts()[:1]
        out["away_shown"] = has("The agent is not answering")
        from PySide6.QtGui import QFont
        font = find("tipLine").property("font")
        out["sizes"] = [find("wordsButton").height(), font.pixelSize(), font.weight() == QFont.Weight.DemiBold,
                        scene().width()]
    """)
    assert out["sizes"] == [36, 17, True, 576]       # the spec: a 36 px button, the line 17 DemiBold, a 576 px stage
    assert out["sections"] == ["Start here", "Work", "Code", "This machine"]      # no New: nothing is new
    assert [r[:2] for r in out["rows"]] == out["backend"]
    assert len(out["rows"]) == 20
    assert [r[2] for r in out["rows"]].count(True) == 2 and out["rows"][0][2] and out["rows"][1][2]
    assert out["ticks"] == 2 and out["tick_texts"] == ["tried", "tried"]
    assert out["front"][0] == "make-an-app" and out["front"][2] == "Make me a ticketing app"
    assert out["selected_rows"] == [out["front"][1]]
    assert out["head"] == ["Tips"] and out["away_shown"] is False


def test_only_the_tips_agentd_lists_are_anywhere_in_the_window(home):
    out = drive(home, """
        boot(facts="few")
        listed()
        full = lab.listing(home, lab.MACHINE)
        out["listed"] = [t["line"] for t in backend.tips]
        out["unlisted"] = [t["line"] for t in full if t["line"] not in out["listed"]]
        every = texts(only_visible=False)               # every row exists, on screen or not
        out["unlisted_found"] = [line for line in out["unlisted"] if line in every]
        out["listed_found"] = [line for line in out["listed"] if line in every]
        out["sections"] = [it.property("text") for it in find_all("section") if it.isVisible()]
        out["ids"] = [t["id"] for t in backend.tips]
        rows = [it.property("text") for n in ("rowLine", "rowWords", "rowTick") for it in find_all(n)]
        out["coming_soon"] = [t for t in rows if isinstance(t, str) and
                              ("soon" in t.lower() or "locked" in t.lower() or "needs" in t.lower())]
    """)
    assert 0 < len(out["listed"]) < 20 and out["unlisted"]
    assert out["unlisted_found"] == [], "a tip the machine cannot back is not in the window, not even greyed"
    assert out["listed_found"] == out["listed"]
    assert "New" not in out["sections"]
    assert "keep" not in out["ids"] and "undo" not in out["ids"]
    assert out["coming_soon"] == []


def test_new_is_a_section_only_for_tips_an_update_brought_and_down_goes_through_it_last(home):
    out = drive(home, """
        boot(new=("bang", "undo"))
        listed(20)
        out["sections"] = [it.property("text") for it in find_all("section") if it.isVisible()]
        keys = [it.property("text") for it in find_all("rowLine")]
        out["once"] = [keys.count(t) for t in (backend.tips[5]["line"],)]
        out["last_two"] = [t["id"] for t in backend.tips[-2:]]
        backend.select("why")                              # the last tip of This machine
        spin(200)
        key(Qt.Key.Key_Down)
        out["front"] = backend.selected
        out["new_heading_shown"] = any(on_screen(it) and it.property("text") == "New" for it in find_all("section"))
        key(Qt.Key.Key_Down)
        out["front2"] = backend.selected
        spin(200)
        out["row_shown"] = [ev(r, "line") for r in find_all("tipRow") if r.isVisible() and on_screen(r) and ev(r, "selected")]
        shot("new")
    """)
    assert out["sections"] == ["Start here", "Work", "Code", "This machine", "New"]
    assert out["once"] == [1] and out["last_two"] == ["bang", "undo"]
    assert out["front"] == "bang" and out["front2"] == "undo"
    assert out["new_heading_shown"] is True and len(out["row_shown"]) == 1


# ---- the stage ----------------------------------------------------------------------------------------------------

def test_a_chosen_tip_plays_once_and_holds_its_payoff_and_replay_and_space_play_it_again(home):
    out = drive(home, """
        boot(tried=("make-an-app", "picture"))
        listed(20)
        out["playing_at_first"] = wait_until(playing, 4000)
        spin(900)
        shot("playing")
        out["mid"] = clock() + [find("scene").property("phase")]
        out["replay_shown"] = shown("replayButton")
        out["finished"] = wait_until(held, 15000)
        spin(300)
        out["end"] = clock() + [find("scene").property("phase")]
        out["captions"] = [[t, it.property("color").name()] for it in text_items()
                           for t in [it.property("text")] if t in ("You ask", "It builds", "You have it")]
        out["fg"], out["muted"] = theme.property("fg").name(), theme.property("muted").name()
        shot("held")
        # nothing advances, loops or moves by itself
        spin(1800)
        out["later"] = clock() + [playing(), backend.selected]
        # Replay plays it again from the start and holds it
        click(find("replayButton"))
        out["after_replay"] = [playing(), clock()[0] < clock()[1]]
        wait_until(held, 15000)
        key(Qt.Key.Key_Space)
        out["after_space"] = [playing(), clock()[0] < 800]
        spin(1200)
        key(Qt.Key.Key_Space)                              # while it plays: from the start again
        out["space_midway"] = clock()[0]
        wait_until(held, 15000)
        # choosing another tip plays that one, once
        click_text("Ask for a picture, not a paragraph.")
        out["picked"] = [backend.selected, ev(find("scene"), "tip.id"), wait_until(playing, 2000)]
        out["picture_line"] = find("tipLine").property("text")
        wait_until(held, 15000)
        out["picture_end"] = clock()
    """, timeout=170)
    assert out["playing_at_first"] is True and out["replay_shown"] is True
    t, d, phase = out["mid"]
    assert 0 < t < d and phase in (0, 1)
    assert out["finished"] is True
    t, d, phase = out["end"]
    assert t == d > 4000 and phase == 2, "the last frame is held"
    assert [c[0] for c in out["captions"]] == ["You ask", "It builds", "You have it"]
    assert [c[1] == out["fg"] for c in out["captions"]] == [False, False, True], "the caption of the phase it is in is fg"
    assert out["captions"][0][1] == out["muted"]
    assert out["later"][0] == out["later"][1] and out["later"][2:] == [False, "make-an-app"]
    assert out["after_replay"] == [True, True]
    assert out["after_space"] == [True, True]
    assert out["space_midway"] < 600, "Space restarts a tip that is playing"
    assert out["picked"] == ["picture", "picture", True]
    assert out["picture_line"] == "Ask for a picture, not a paragraph."
    assert out["picture_end"][0] == out["picture_end"][1]


def test_up_and_down_choose_the_tip_and_each_plays_from_its_start(home):
    out = drive(home, """
        boot()
        listed(20)
        wait_until(held, 15000)
        order = [t["id"] for t in backend.tips]
        key(Qt.Key.Key_Down)
        out["down"] = [backend.selected, ev(find("scene"), "tip.id"), wait_until(playing, 2000)]
        key(Qt.Key.Key_Down)
        out["down2"] = [backend.selected, ev(find("scene"), "tip.id")]
        key(Qt.Key.Key_Up)
        out["up"] = [backend.selected, ev(find("scene"), "tip.id")]
        key(Qt.Key.Key_Up)
        key(Qt.Key.Key_Up)
        out["top"] = backend.selected
        out["order"] = order[:3]
        out["line"] = find("tipLine").property("text")
        out["ids_line"] = backend.selectedTip["line"]
        out["log"] = log[:]
    """)
    assert out["order"][:3] == ["make-an-app", "picture", "esc"]
    assert out["down"] == ["picture", "picture", True]
    assert out["down2"] == ["esc", "esc"] and out["up"] == ["picture", "picture"]
    assert out["top"] == "make-an-app", "Up stops at the first tip"
    assert out["line"] == out["ids_line"]
    assert out["log"] == [], "choosing a tip hides nothing and puts nothing in the pill"


def test_a_tip_with_no_scene_of_its_own_yet_has_an_empty_stage_and_nothing_to_replay(home):
    """Every shipped tip has a scene, but a tip may arrive without one (a row being added, an older table): the
    window shows it as it shows any other, with an empty stage, and nothing to replay. The row here is made up."""
    out = drive(home, """
        bare = {"id": "no-scene-yet", "section": "work", "order": 99, "line": "A tip that came without its scene.",
                "words": "Show me the next thing", "marks": [], "card": True, "tried": False, "new": False,
                "captions": ["one", "two", "three"], "beats": []}
        boot(tips=lab.listing(home) + [bare])
        listed(21)
        out["first"] = [backend.selected, wait_until(playing, 2000), clock()[1] > 0, shown("replayButton")]
        wait_until(held, 15000)
        backend.select("no-scene-yet")                     # from a tip that played to one with no scene
        spin(300)
        out["front"] = backend.selected
        out["scene_tip"] = ev(find("scene"), "tip.id")
        out["duration"] = clock()[1]
        out["playing"] = playing()
        out["replay"] = shown("replayButton")
        out["button"] = shown("wordsButton")
        out["line"] = find("tipLine").property("text")
        shot("no-scene")
        key(Qt.Key.Key_Space)
        spin(200)
        out["space"] = playing()
        key(Qt.Key.Key_Down)                               # on to the next tip, which has a scene and plays
        out["next"] = [backend.selected, wait_until(playing, 2000), clock()[1] > 0, shown("replayButton")]
        wait_until(held, 15000)
        key(Qt.Key.Key_Up)                                 # and back: the stage is empty again
        spin(300)
        out["back"] = [backend.selected, clock()[1], playing(), shown("replayButton")]
    """)
    assert out["first"] == ["make-an-app", True, True, True]
    assert out["front"] == "no-scene-yet" and out["scene_tip"] == "no-scene-yet" and out["duration"] == 0
    assert out["playing"] is False and out["replay"] is False and out["button"] is True
    assert out["line"] == "A tip that came without its scene."
    assert out["space"] is False
    assert out["next"][0] != "no-scene-yet" and out["next"][1:] == [True, True, True]
    assert out["back"] == ["no-scene-yet", 0, False, False]


# ---- the button and Enter: the words in the pill, nothing run ------------------------------------------------

def test_enter_and_the_button_hide_the_window_first_and_then_put_the_exact_words_in_the_pill(home):
    out = drive(home, """
        boot()
        listed(20)
        key(Qt.Key.Key_Return)
        wait_until(lambda: summons(), 3000)
        out["enter"] = log[:]
        del log[:]
        click(find("wordsButton"))
        wait_until(lambda: summons(), 3000)
        out["click"] = log[:]
        del log[:]
        backend.select("change-app")
        spin(100)
        out["label"] = find("wordsButton").property("text")
        key(Qt.Key.Key_Enter)                              # the keypad's Enter too
        wait_until(lambda: summons(), 3000)
        out["dots"] = log[:]
        spin(500)
        # Agentd was sent the summons and the Tips app's own list requests, and nothing that runs: no prompt
        out["types"] = sorted({m["type"] for m in fake.lines})
        out["summons"] = [m for m in fake.lines if m["type"] == "summon"]
        out["other"] = sorted({m.get("op") for m in fake.lines if m["type"] == "tips"})
    """)
    first = out["enter"]
    assert [e[0] for e in first] == ["hide", "summon"], "the window goes first, then the words"
    assert first[1][1] == {"type": "summon", "text": "Make me a ticketing app", "tip": True, "id": "make-an-app"}
    assert [e[0] for e in out["click"]] == ["hide", "summon"]
    assert out["click"][1][1]["text"] == "Make me a ticketing app" and out["click"][1][1]["tip"] is True
    assert out["label"] == "In Tickets, add…"
    assert [e[0] for e in out["dots"]] == ["hide", "summon"]
    assert out["dots"][1][1]["text"] == "In Tickets, add ", "the dots go and the cursor waits after a space"
    assert out["dots"][1][1]["id"] == "change-app"
    assert out["types"] == ["summon", "tips"], "nothing is sent as a prompt, an ask or a command"
    assert len(out["summons"]) == 3 and set(out["other"]) <= {"list", "opened"}


def test_a_tip_with_only_marks_has_no_button_and_enter_does_nothing(home):
    out = drive(home, """
        boot()
        listed(20)
        backend.select("esc")
        spin(200)
        out["button"] = shown("wordsButton")
        out["marks"] = shown("marks")
        out["mark_texts"] = [it.property("text") for it in text_items() if it.property("text") == "Esc"]
        key(Qt.Key.Key_Return)
        click(find("marks"))
        spin(400)
        out["log"] = log[:]
        out["summons"] = [m for m in fake.lines if m["type"] == "summon"]
        shot("marks")
        backend.select("whyhere")
        spin(200)
        out["long_mark"] = [shown("marks"), [it.property("text") for it in text_items()
                                             if it.property("text") == "where did this come from?"]]
    """)
    assert out["button"] is False and out["marks"] is True and "Esc" in out["mark_texts"]
    assert out["log"] == [] and out["summons"] == []
    assert out["long_mark"][0] is True and out["long_mark"][1]


def test_esc_hides_the_window_and_puts_nothing_in_the_pill(home):
    out = drive(home, """
        boot()
        listed(20)
        wait_until(held, 15000)
        key(Qt.Key.Key_Escape)
        spin(400)
        out["log"] = log[:]
        out["summons"] = [m for m in fake.lines if m["type"] == "summon"]
        out["front"] = backend.selected
    """)
    assert out["log"] == [["hide"]] and out["summons"] == [] and out["front"] == "make-an-app"


def test_keys_with_a_modifier_are_not_the_windows_and_other_keys_do_nothing(home):
    out = drive(home, """
        boot()
        listed(20)
        key(Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
        key(Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier)
        key(Qt.Key.Key_Space, Qt.KeyboardModifier.MetaModifier)
        key(Qt.Key.Key_A)
        key(Qt.Key.Key_Tab)
        key(Qt.Key.Key_Right)
        out["front"] = backend.selected
        out["log"] = log[:]
    """)
    assert out["front"] == "make-an-app" and out["log"] == []


def test_the_press_is_never_a_click_that_runs_and_the_window_holds_no_connection_of_its_own(home):
    out = drive(home, """
        boot()
        listed(20)
        click(find("wordsButton"))
        wait_until(lambda: summons(), 3000)
        spin(800)
        wait_until(lambda: fake.open == 0, 3000)
        out["open"] = fake.open
        out["per_connection"] = max(len(c) for c in fake.by_connection)
        out["lines"] = [[m["type"], m.get("op")] for m in fake.lines]
        out["connections"] = fake.connections
        spin(1500)
        out["still"] = [fake.open, fake.connections - out["connections"]]
    """)
    assert out["open"] == 0, "every connection the window made is closed again: nothing is held open"
    assert out["per_connection"] == 1
    assert sorted(out["lines"]) == [["summon", None], ["tips", "list"]]
    assert out["still"] == [0, 0], "and it asks for nothing while it sits there"


# ---- opened on a tip ------------------------------------------------------------------------------------------------

def test_the_window_opens_on_the_tip_the_card_named_plays_it_and_says_it_read_the_request(home):
    out = drive(home, """
        boot(state={"open_on": "disk"})
        listed(20)
        out["front"] = [backend.selected, ev(find("scene"), "tip.id"), wait_until(playing, 3000)]
        wait_until(lambda: fake.of("tips", "opened"), 3000)
        out["opened"] = fake.of("tips", "opened")
        # a press on the same tip again, once agentd has cleared the request: it plays again
        wait_until(held, 15000)
        lab.write_state(home / "state" / "tips.json", open_on="")
        out["cleared"] = wait_until(lambda: backend._open_seen == "", 5000)      # the app has read the empty request
        lab.write_state(home / "state" / "tips.json", open_on="disk")
        out["again"] = wait_until(playing, 9000)
        # the line that says it was read goes out on a connection of its own, after the window plays the tip
        out["opened_twice"] = wait_until(lambda: len(fake.of("tips", "opened")) == 2, 5000)
        out["still_front"] = backend.selected
    """, timeout=170)
    assert out["front"] == ["disk", "disk", True]
    assert out["opened"] == [{"type": "tips", "op": "opened", "id": "disk"}]
    assert out["cleared"] is True and out["again"] is True and out["opened_twice"] is True
    assert out["still_front"] == "disk"


# ---- reduced motion ---------------------------------------------------------------------------------------------------

def test_under_reduced_motion_the_stage_is_three_stills_with_their_captions_and_nothing_plays(home):
    out = drive(home, """
        boot()
        listed(20)
        out["reduced"] = [theme.property("reducedMotion"), find("scene").property("stills")]
        out["playing"] = playing()
        out["clock"] = clock()
        out["replay"] = shown("replayButton")
        names = ["You ask", "It builds", "You have it"]
        caps = [it for it in text_items() if it.property("text") in names]
        out["captions"] = [it.property("text") for it in caps]
        out["colours"] = sorted({it.property("color").name() for it in caps})
        out["fg"] = theme.property("fg").name()
        rects = [scene_rect(it) for it in caps]
        out["row"] = [abs(r[1] - rects[0][1]) < 1 for r in rects]
        out["left_to_right"] = rects[0][0] < rects[1][0]        # the second stands right of the first
        out["inside_stage"] = [r[0] >= 16 - 1 and r[2] <= 592 + 1 for r in rects]
        out["second_row"] = [abs(rects[2][0] - rects[0][0]) < 1, rects[2][1] > rects[0][3]]
        out["scene_rect"] = scene_rect(find("scene"))
        out["card_size"] = [scene_rect(find("still-%d" % i))[2:] for i in range(3)]
        line, box = scene_rect(find("tipLine")), scene_rect(find("stageBox"))
        out["clear"] = [out["scene_rect"][3] <= line[1] - 4, out["scene_rect"][1] >= box[1] - 6]
        out["line_stays"] = [box[3] - box[1], line[1] - box[3]]
        shot("stills")
        key(Qt.Key.Key_Space)
        spin(300)
        out["space"] = [playing(), clock()]
        key(Qt.Key.Key_Down)
        out["next"] = [backend.selected, playing(), clock()[0] == clock()[1]]
        key(Qt.Key.Key_Return)
        wait_until(lambda: summons(), 3000)
        out["log"] = [e[0] for e in log]
        out["button_still_works"] = summons()[0]["id"] if summons() else None
    """, env={"BOMBADIL_REDUCE_MOTION": "1"})
    assert out["reduced"] == [True, True]
    assert out["playing"] is False and out["clock"][0] == out["clock"][1] > 0
    assert out["replay"] is False
    assert out["captions"] == ["You ask", "It builds", "You have it"]
    # the stills are the card's size (272 px), so on the stage's 576 px two stand across and the third under the first
    assert out["row"] == [True, True, False] and out["left_to_right"] is True
    assert out["second_row"] == [True, True] and out["inside_stage"] == [True] * 3
    assert [round(r[0] - l) for r, l in zip(out["card_size"], (16, 300, 16))] == [272] * 3
    assert out["clear"] == [True, True], "the taller stills touch neither the title above nor the line below"
    assert out["line_stays"] == [350, 12], "the line and the button stand where they do while a tip plays"
    assert out["colours"] == [out["fg"]], "no phase is in front under three stills: every caption is the ink"
    assert out["space"][0] is False and out["next"][1] is False and out["next"][2] is True
    assert out["log"] == ["hide", "summon"] and out["button_still_works"] == "picture"


# ---- ticks, New, away, resting ---------------------------------------------------------------------------------------

def test_a_tip_tried_while_the_window_is_open_gets_its_tick_and_nothing_else_moves(home):
    out = drive(home, """
        boot(tried=("make-an-app",), state={"tried": ["make-an-app"]})
        listed(20)
        backend.select("disk")
        spin(300)
        wait_until(held, 15000)
        view = find("list")
        before = [view.property("contentY"), sum(1 for it in find_all("rowTick") if on_screen(it)), backend.selected,
                  clock()]
        disk_row = [r for r in find_all("tipRow") if ev(r, "line") == backend.selectedTip["line"]][0]
        out["disk_before"] = ev(disk_row, "tried")
        fake.tips = lab.listing(home, tried=("make-an-app", "disk"))
        lab.write_state(home / "state" / "tips.json", tried=["make-an-app", "disk"])
        out["ticked"] = wait_until(lambda: ev(disk_row, "tried") is True, 5000)
        spin(500)
        after = [view.property("contentY"), sum(1 for it in find_all("rowTick") if on_screen(it)), backend.selected,
                 clock()]
        out["before"], out["after"] = before, after
        out["playing"] = playing()
        out["tick_in_row"] = [on_screen(it) for it in find_all("rowTick") if it.isVisible()]
        shot("ticked")
    """, timeout=170)
    assert out["disk_before"] is False and out["ticked"] is True
    b, a = out["before"], out["after"]
    assert a[0] == b[0], "the list did not move"
    assert a[1] == b[1] + 1, "one tick more on screen"
    assert a[2] == b[2] == "disk" and a[3] == b[3], "the tip in front is the same and was not played again"
    assert out["playing"] is False


def test_the_window_says_so_while_agentd_is_away_and_has_the_list_when_it_comes_back(home):
    out = drive(home, """
        boot(up=False)
        spin(1200)
        out["away"] = [has("The agent is not answering"), has("Tips come back as soon as it does."), shown("content"),
                       backend.connected]
        shot("away")
        key(Qt.Key.Key_Return)
        key(Qt.Key.Key_Down)
        out["keys_while_away"] = [log[:], summons()]
        fake.start()
        out["back"] = wait_until(lambda: backend.connected and backend.count == 20, 9000)
        spin(300)
        out["after"] = [has("The agent is not answering"), shown("content"), backend.selected]
    """, timeout=170)
    assert out["away"] == [True, True, False, False]
    assert out["keys_while_away"] == [[], []]
    assert out["back"] is True and out["after"] == [False, True, "make-an-app"]


def test_the_window_asks_once_each_time_it_comes_forward_and_finds_out_that_agentd_went_away(home):
    """Once the list has come nothing asks again by itself. The window is hidden and shown from its drawer, which
    to it is losing and taking the keyboard: that is when it looks, and a look that finds nobody says so."""
    out = drive(home, """
        boot()
        listed(20)
        spin(300)
        sys.modules[type(backend).__module__].FRESH_MS = 0       # (a look a moment after an answer is skipped)
        from PySide6.QtGui import QWindow
        other = QWindow()
        other.resize(100, 100)
        other.show()

        def forward():          # hidden, then shown: the window loses the keyboard and takes it back
            other.requestActivate()
            spin(200)
            assert not win.isActive()
            win.requestActivate()
            spin(400)
            assert win.isActive()

        asked = len(fake.of("tips", "list"))
        forward()
        out["asked_when_it_came_forward"] = len(fake.of("tips", "list")) - asked
        fake.stop()
        spin(300)
        out["before_a_look"] = [backend.connected, has("The agent is not answering")]
        forward()
        out["after_a_look"] = [backend.connected, has("The agent is not answering"), shown("content")]
        shot("gone")
        fake.start()
        out["back"] = wait_until(lambda: backend.connected and backend.count == 20, 9000)
        spin(300)
        out["back_shows"] = [has("The agent is not answering"), shown("content"), backend.selected]
    """, timeout=170)
    assert out["asked_when_it_came_forward"] == 1
    assert out["before_a_look"] == [True, False]       # nobody looked: it still shows what it had
    assert out["after_a_look"] == [False, True, False]
    assert out["back"] is True and out["back_shows"] == [False, True, "make-an-app"]


def test_nothing_to_list_says_what_is_empty_and_why(home):
    out = drive(home, """
        boot(tips=[])
        assert wait_until(lambda: backend.connected, 8000)
        spin(300)
        out["texts"] = texts()
        out["content"] = shown("content")
        key(Qt.Key.Key_Return)
        key(Qt.Key.Key_Down)
        key(Qt.Key.Key_Space)
        out["log"] = log[:]
    """)
    assert "No tips to show on this computer" in out["texts"]                  # what is empty, and
    assert "Tips are switched off here, or their table could not be read." in out["texts"]       # why
    assert out["content"] is False
    assert "The agent is not answering" not in out["texts"] and out["log"] == []


def test_while_the_ai_rests_the_list_is_the_same_and_the_button_still_fills_the_pill(home):
    out = drive(home, """
        boot(status={"type": "status", "busy": False, "provider": "claude", "setup": "resting",
                     "rest": {"provider": "claude", "why": "limit", "kind": "plan", "until": time.time() + 3600}})
        listed(20)
        out["lines"] = [t["line"] for t in backend.tips]
        out["same"] = out["lines"] == [t["line"] for t in lab.listing(home, lab.MACHINE)]
        out["sections"] = [it.property("text") for it in find_all("section") if it.isVisible()]
        out["button"] = shown("wordsButton")
        key(Qt.Key.Key_Return)
        wait_until(lambda: summons(), 3000)
        out["log"] = [e[0] for e in log]
    """)
    assert out["same"] is True and out["sections"] == ["Start here", "Work", "Code", "This machine"]
    assert out["button"] is True and out["log"] == ["hide", "summon"]


# ---- the window fits ------------------------------------------------------------------------------------------------

def test_every_tip_fits_960_by_516_and_a_very_long_one_does_not_push_anything_out(home):
    out = drive(home, """
        # Both have a scene: a tip with one has Replay beside its button (or its marks), which is what the long
        # words must leave room for (a row with no beats has no Replay and would not show that).
        long_one = {"id": "long-one", "section": "work", "order": 9, "line": "A very long line " * 12,
                    "words": "and very long words " * 15, "marks": [], "card": True, "tried": False, "new": False,
                    "captions": ["one", "two", "three"], "beats": lab.fx.BUILD["beats"]}
        long_mark = dict(long_one, id="long-marks", order=10, words="", marks=["a mark that goes on " * 3, "Esc", "x"])
        boot(tips=lab.listing(home, tried=("esc",), new=("undo",)) + [long_one, long_mark])
        listed(22)
        W, H = win.width(), win.height()
        out["size"] = [W, H]
        bad, lefts = [], []
        for tip in list(backend.tips):
            backend.select(tip["id"])
            spin(80)
            names = ["stageBox", "tipLine", "actions", "wordsButton", "marks", "replayButton", "list", "content"]
            for n in names:
                for it in find_all(n):
                    if not it.isVisible():
                        continue
                    x0, y0, x1, y1 = scene_rect(it)
                    if x0 < -0.5 or y0 < -0.5 or x1 > W + 0.5 or y1 > H + 0.5:
                        bad.append([tip["id"], n, round(x0), round(y0), round(x1), round(y1)])
                    if n not in ("list", "content") and x1 > 592.5:
                        lefts.append([tip["id"], n, round(x1)])
            words, replay = find("wordsButton"), find("replayButton")
            if words.isVisible() and replay.isVisible() and scene_rect(words)[2] > scene_rect(replay)[0] + 0.5:
                bad.append([tip["id"], "words over replay"])
            if tip["id"] in ("long-one", "long-marks") and not replay.isVisible():
                bad.append([tip["id"], "no Replay beside the long words"])
            if tip["id"] == "make-an-app":
                shot("fit-first")
            if tip["id"] in ("long-one", "long-marks"):
                shot("fit-" + tip["id"])
        out["bad"], out["lefts"] = bad, lefts
        out["count"] = len(backend.tips)
        # the list's last row, in view, whole
        backend.select("long-marks")
        spin(300)
        view = find("list")
        last = [r for r in find_all("tipRow") if r.isVisible() and ev(r, "selected")][0]
        vy0, vy1 = scene_rect(view)[1], scene_rect(view)[3]
        ry0, ry1 = scene_rect(last)[1], scene_rect(last)[3]
        out["last_row_whole"] = [ry0 >= vy0 - 0.5, ry1 <= vy1 + 0.5]
        out["list_bottom"] = vy1
        # a short window cannot be asked for: the window is 960 x 516, and the kit gives it no scroll of its own
        out["lines_elided"] = find("tipLine").property("truncated")
    """, timeout=170)
    assert out["size"] == [960, 516] and out["count"] == 22
    assert out["bad"] == [], out["bad"]
    assert out["lefts"] == [], "nothing on the left passes the stage's 576 px"
    assert out["last_row_whole"] == [True, True] and out["list_bottom"] <= 500.5
    assert out["lines_elided"] is True, "a line that does not fit says so with its end, it does not run on"


def test_the_list_scrolls_with_the_keys_so_the_tip_in_front_is_always_in_view(home):
    out = drive(home, """
        boot(new=("bang", "undo"))
        listed(20)
        view = find("list")
        bad = []
        for _ in range(21):
            key(Qt.Key.Key_Down)
            spin(40)
            row = [r for r in find_all("tipRow") if r.isVisible() and ev(r, "selected")][0]
            vy0, vy1 = scene_rect(view)[1], scene_rect(view)[3]
            ry0, ry1 = scene_rect(row)[1], scene_rect(row)[3]
            if ry0 < vy0 - 0.5 or ry1 > vy1 + 0.5:
                bad.append([backend.selected, round(ry0), round(ry1), round(vy0), round(vy1)])
        out["down_bad"] = bad
        out["end"] = backend.selected
        bad = []
        for _ in range(21):
            key(Qt.Key.Key_Up)
            spin(40)
            row = [r for r in find_all("tipRow") if r.isVisible() and ev(r, "selected")][0]
            vy0, vy1 = scene_rect(view)[1], scene_rect(view)[3]
            ry0, ry1 = scene_rect(row)[1], scene_rect(row)[3]
            if ry0 < vy0 - 0.5 or ry1 > vy1 + 0.5:
                bad.append([backend.selected, round(ry0), round(ry1), round(vy0), round(vy1)])
        out["up_bad"] = bad
        out["top"] = [backend.selected, view.property("contentY")]
    """, timeout=170)
    assert out["down_bad"] == [] and out["up_bad"] == []
    assert out["end"] == "undo" and out["top"] == ["make-an-app", 0]


# ---- no orange of its own --------------------------------------------------------------------------------------------

ORANGE = r'''
        boot(tried=("make-an-app",))
        listed(20)
        wait_until(held, 15000)
        accent = {tuple(theme.property(n).getRgb()[:3]) for n in
                  ("accent", "accentHover", "accentPressed", "accentInk", "accentOnLight")}
        sx0, sy0, sx1, sy1 = scene_rect(find("scene"))
        sub = {id(i) for i in walk(find("scene"))}
        stray = []
        for it in walk(root):
            if id(it) in sub or not on_screen(it):
                continue
            c = it.property("color")
            if isinstance(c, QColor) and c.alpha() > 0 and tuple(c.getRgb()[:3]) in accent:
                stray.append([it.metaObject().className(), it.objectName(), "color"])
            if "QQuickRectangle" in lineage(it):
                width, colour = ev(it, "border.width"), ev(it, "border.color")
                if width and isinstance(colour, QColor) and colour.alpha() > 0 and tuple(colour.getRgb()[:3]) in accent:
                    stray.append([it.metaObject().className(), it.objectName(), "border"])
        out["stray_items"] = stray
        img = win.grabWindow()
        outside = []
        for y in range(img.height()):
            for x in range(img.width()):
                if sx0 - 2 <= x <= sx1 + 2 and sy0 - 2 <= y <= sy1 + 2:
                    continue
                h, s, v, _ = img.pixelColor(x, y).getHsvF()
                if 0.03 <= h <= 0.075 and s >= 0.45 and v >= 0.55:
                    outside.append([x, y])
        out["orange_outside"] = outside[:20]
'''


def test_the_window_has_no_orange_of_its_own_in_the_tree_or_the_pixels(home):
    out = drive(home, ORANGE, timeout=170)
    assert out["stray_items"] == [], out["stray_items"]
    assert out["orange_outside"] == [], f"orange outside the stage at {out['orange_outside']}"


# ---- the files --------------------------------------------------------------------------------------------------------

def code_of(path: Path) -> str:
    return re.sub(r"(?m)(^|\s)//.*$", "", path.read_text())          # without the comments


def test_nothing_in_the_window_moves_loops_advances_or_makes_a_sound_on_its_own():
    for qml in QML_FILES:
        src = code_of(qml)
        assert not re.search(r"\bTimer\b|SoundEffect|MediaPlayer|Audio|SequentialAnimation|ParallelAnimation", src), qml.name
        assert "Animation.Infinite" not in src and "loops" not in src, qml.name
        assert "Qt.callLater" not in src or qml.name == "TipList.qml", qml.name
        assert not re.search(r"Theme\.accent|accentSoft|accentInk|accentHover|accentPressed|tone\s*:\s*\"accent\"|highlighted",
                             src), qml.name
    # The one place a tip's words leave the window: after it hides.
    main = code_of(APP / "main.qml")
    press = main[main.index("function press()"):]
    press = press[:press.index("\n    }\n") + 6]
    assert press.index("App.hide()") < press.index("Agent.draft(")
    assert len(re.findall(r"Agent\.\w+", main)) == 1 and "Agent.draft(backend.draftOf(win.tip.id), win.tip.id)" in main
    for qml in QML_FILES:
        assert "Agent.ask" not in qml.read_text() and "Agent.send" not in qml.read_text()
    assert len(re.findall(r"\bpress\(\)", main)) == 3       # the definition, Enter and the button


def test_the_words_the_window_says_keep_to_the_voice():
    for qml in QML_FILES:
        for literal in re.findall(r'"([^"\\]*)"', code_of(qml)):
            if not re.search(r"[A-Za-z]{3,} [A-Za-z]|^[A-Z][a-z]+$", literal):
                continue
            assert "!" not in literal and "—" not in literal, (qml.name, literal)
            assert not re.search(r"\b(will|tomorrow|later)\b", literal, re.IGNORECASE), (qml.name, literal)
    for text in ("The agent is not answering", "Tips come back as soon as it does.", "No tips to show on this computer",
                 "Tips are switched off here, or their table could not be read.", "Start here", "Work", "Code", "This machine", "New", "tried", "Replay"):
        assert text in "".join(q.read_text() for q in QML_FILES) + (APP / "app.py").read_text(), text
    assert (APP / "app.toml").read_text().count("What to ask, shown in action") == 1


def test_the_app_passes_bombadil_app_check(home):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"}
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), "check", "tips"], capture_output=True,
                       text=True, env=env, timeout=120, check=False)
    result = json.loads(r.stdout)
    assert r.returncode == 0 and result["ok"] and result["loaded"], result
    assert result["errors"] == [] and result["warnings"] == [] and result["size"] == [960, 516]

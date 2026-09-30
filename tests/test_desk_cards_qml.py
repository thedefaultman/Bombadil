"""The desk's card faces and strip chip, drawn offscreen and driven like a rail would.

DeskCard, NowCard, RowsCard and DeskStrip are plain Qt Quick, so they load here without a
compositor. Set BOMBADIL_SCREENS=<dir> to save a picture of each card in each of its states.
"""

import os
import re
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
QtQuick = pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)

SHELL = Path(__file__).resolve().parents[1] / "shell"

# The faces are read from here (SHELL by default); the integrator points a rail's tests elsewhere.
THEME = {}
for name, value in re.findall(r'^var (\w+) = ("#[0-9a-f]+"|[\d.]+)', (SHELL / "DeskTheme.js").read_text(), re.M):
    THEME[name] = value.strip('"') if value.startswith('"') else float(value)

# One Loader per card, the way a rail's Repeater builds them; the test says what to build.
HARNESS = """
import QtQuick
import "%s"

Window {
    id: w
    width: 760; height: 520; visible: true
    color: "#101214"
    property var specs: []
    property var events: []
    function note(kind, name, args) { w.events = w.events.concat([{kind: kind, card: name, args: args}]) }
    Image { anchors.fill: parent; source: "%s" }
    Flow {
        objectName: "flow"
        x: 24; y: 24; width: parent.width - 48; spacing: 24
        Repeater {
            model: w.specs
            Loader {
                id: slot
                required property var modelData
                readonly property var spec: modelData
                objectName: spec.name
                sourceComponent: spec.kind === "now" ? nowC : spec.kind === "rows" ? rowsC : stripC
                onLoaded: { for (const k in spec.props) item[k] = spec.props[k] }
            }
        }
    }
    Component { id: nowC; NowCard { onTitleClicked: w.note("titleClicked", parent.spec.name, []) } }
    Component {
        id: rowsC
        RowsCard {
            onTitleClicked: w.note("titleClicked", parent.spec.name, [])
            onRowAction: (key, action) => w.note("rowAction", parent.spec.name, [key, action])
            onRowRemove: key => w.note("rowRemove", parent.spec.name, [key])
        }
    }
    Component {
        id: stripC
        DeskStrip {
            onClicked: w.note("clicked", parent.spec.name, [])
            onHoverChanged: over => w.note("hover", parent.spec.name, [over])
        }
    }
}
"""


def paint_wallpaper(path, width, height):
    """The design page's ground: #101214 with a cool wash top left and a warm one bottom right."""
    img = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor("#101214"))
    p = QtGui.QPainter(img)
    for cx, cy, r, colour in ((0.18, 0.08, 0.70, "#1e2a34"), (0.92, 0.96, 0.55, "#34241d")):
        p.save()
        p.translate(cx * width, cy * height)
        p.scale(1, height / width)     # an SVG radius in percent is an ellipse on a box that is not square
        near = QtGui.QColor(colour)
        far = QtGui.QColor(colour)
        far.setAlpha(0)
        grad = QtGui.QRadialGradient(0, 0, r * width)
        grad.setColorAt(0, near)
        grad.setColorAt(1, far)
        p.fillRect(QtCore.QRectF(-width, -width, 3 * width, 3 * width), grad)
        p.restore()
    p.end()
    img.save(str(path))


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Cards:
    def __init__(self, app, tmp_path):
        self.app = app
        self.warnings = []
        # Binding errors and loops arrive as messages, not as engine warnings; catch them from the start.
        self.previous = QtCore.qInstallMessageHandler(
            lambda mode, ctx, msg: self.warnings.append(msg)
            if ".qml" in (ctx.file or "") or "TypeError" in msg else None)
        paint_wallpaper(tmp_path / "wallpaper.png", 760, 520)
        qml = tmp_path / "harness.qml"
        qml.write_text(HARNESS % (SHELL.as_uri(), (tmp_path / "wallpaper.png").as_uri()))
        self.engine = QtQml.QQmlApplicationEngine()
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        self.pump()

    def close(self):
        self.win.close()
        self.engine.deleteLater()
        self.pump()
        QtCore.qInstallMessageHandler(self.previous)

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    # -- building --

    def show(self, *specs):
        """Build cards: (kind, name, {property: value}) for kind now, rows or strip."""
        self.win.setProperty("specs", [{"kind": k, "name": n, "props": p} for k, n, p in specs])
        self.pump(0.1)
        return [self.card(n) for _, n, _ in specs]

    def card(self, name):
        loader = self.find(self.win.contentItem(), name, visible_only=False)[0]
        return loader.property("item")

    def find(self, root, name, visible_only=True):
        found, todo = [], [root]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return sorted(found, key=lambda i: (i.mapToScene(QtCore.QPointF(0, 0)).y(),
                                            i.mapToScene(QtCore.QPointF(0, 0)).x()))

    def one(self, root, name, visible_only=True):
        found = self.find(root, name, visible_only)
        assert len(found) == 1, f"{name}: {len(found)} found"
        return found[0]

    def set(self, card, **props):
        for k, v in props.items():
            card.setProperty(k, v)
        self.pump()

    def call(self, item, name, *args):
        QtCore.QMetaObject.invokeMethod(item, name, QtCore.Qt.DirectConnection,
                                        *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()

    # -- reading --

    def geometry(self, item, root=None):
        """x, y, width, height of an item in its card's coordinates (or the root's)."""
        root = root or self.win.contentItem()
        pos = item.mapToItem(root, QtCore.QPointF(0, 0))
        return pos.x(), pos.y(), item.width(), item.height()

    @property
    def events(self):
        raw = self.win.property("events")
        raw = raw.toVariant() if hasattr(raw, "toVariant") else raw
        return [dict(e) for e in raw]

    def centre(self, item):
        return item.mapToScene(QtCore.QPointF(item.width() / 2, item.height() / 2)).toPoint()

    def click(self, item):
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, self.centre(item))
        self.pump()

    def hover(self, item):
        QtTest.QTest.mouseMove(self.win, self.centre(item))
        self.pump(0.1)

    def away(self):
        QtTest.QTest.mouseMove(self.win, QtCore.QPoint(750, 510))
        self.pump(0.1)

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.4)   # let the wash and the first pulse settle
            Path(out).mkdir(parents=True, exist_ok=True)
            flow = self.win.contentItem().childItems()[-1]
            box = flow.property("childrenRect")
            rect = QtCore.QRect(0, 0, int(box.width()) + 48, int(box.height()) + 48)
            self.win.grabWindow().copy(rect).save(str(Path(out) / f"desk-cards-{name}.png"))


@pytest.fixture
def cards(app, tmp_path):
    c = Cards(app, tmp_path)
    yield c
    c.close()


def colour(name):
    return QtGui.QColor(THEME[name]).name()


# -- models the way DeskState will send them --

def steps(*pairs):
    return [{"id": str(i + 1), "label": label, "status": status} for i, (label, status) in enumerate(pairs)]


ROUTE = steps(("Save a restore point", "completed"), ("Installing docker", "in_progress"),
              ("Enable the service", "pending"), ("Check it works", "pending"))


def now(**kw):
    model = {"title": "install docker and set it up for my projects",
             "why": "Step 2 of 4 · 1 package so far · Esc stops", "steps": ROUTE, "edge": "machine",
             "command": "", "caption": "", "done": False, "running": True}
    model.update(kw)
    return {"model": model}


def row(key, kind="dot", title="", sub="", tone="you", button="", remove=False, meter=None, meter_text="",
        pulse=False):
    return {"key": key, "kind": kind, "title": title or key, "sub": sub, "meter": meter,
            "meterText": meter_text, "tone": tone, "pulse": pulse, "button": button, "remove": remove}


def rows(*rs, title="Needs you", why="Tab walks these · one alone is just the line"):
    return {"model": {"title": title, "why": why, "rows": list(rs)}}


NEEDS = (row("reviewer", title="reviewer on Bombadil", sub="apply the migration to the local database?",
             tone="you", button="Open"),
         row("builder", title="builder asks: install qemu-full", sub="a coding session, no sudo of its own",
             tone="sessions", button="Do it", pulse=True))
WATCHING = (row("iso", "meter", "Ubuntu 26.04 ISO", tone="machine", meter=0.43, meter_text="12 MB/s · 4 min"),
            row("build", title="Tell me when the build finishes", sub="VM smoke test · 6 min so far",
                tone="machine", remove=True),
            row("timer", title="Timer, 10 min", sub="6:40 left", tone="sessions", remove=True, pulse=True))


# -- the chrome --

def test_a_card_is_300_wide_with_the_page_geometry_and_a_pointer_title(cards):
    (card,) = cards.show(("rows", "empty", rows(title="Needs you", why="nothing waits")))
    assert (card.width(), card.height()) == (THEME["cardWidth"], 68)     # 50 header + 18 padding
    title = cards.one(card, "rowsTitle")
    why = cards.one(card, "rowsWhy")
    assert title.property("text") == "Needs you" and why.property("text") == "nothing waits"
    assert title.x() == 14 and why.x() == 14
    font = title.property("font")
    assert font.pixelSize() == 14 and font.weight() == QtGui.QFont.DemiBold
    assert not cards.one(card, "rowsEdge", visible_only=False).isVisible()
    cards.snap("chrome-empty")


def test_an_edge_bar_pushes_the_words_six_pixels_in_and_takes_the_stateful_colour(cards):
    (card,) = cards.show(("now", "now", now()))
    edge = cards.one(card, "nowEdge")
    assert cards.geometry(edge, card) == (8, 12, 3, card.height() - 24)
    assert edge.property("radius") == 1.5
    assert cards.one(card, "nowTitle").x() == 20 and cards.one(card, "nowWhy").x() == 20
    for kind, expected in (("machine", "machine"), ("ok", "ok"), ("amber", "amber"), ("red", "red")):
        cards.set(card, model=now(edge=kind)["model"])
        assert edge.property("color").name() == colour(expected)
    cards.set(card, model=now(edge="")["model"])
    assert not edge.isVisible()
    assert cards.one(card, "nowTitle").x() == 14


def test_title_and_why_elide_inside_the_card_and_the_ask_is_never_markup(cards):
    ask = "install docker and set it up for all of my projects, then tidy the downloads folder " * 2
    (card,) = cards.show(("now", "now", now(title=ask, why="a why line " * 30 + "<b>not bold</b>")))
    for name in ("nowTitle", "nowWhy"):
        it = cards.one(card, name)
        assert it.x() + it.width() == card.width() - 14
        assert it.property("contentWidth") <= it.width() + 0.5
        assert it.property("truncated")
        assert it.property("maximumLineCount") == 1
    (card,) = cards.show(("now", "now", now(title="<b>bold?</b> & more")))
    assert cards.one(card, "nowTitle").property("text") == "<b>bold?</b> & more"
    title = cards.one(card, "nowTitle")
    painted = QtGui.QFontMetricsF(title.property("font")).horizontalAdvance(title.property("text"))
    assert abs(title.property("contentWidth") - painted) < 2      # markup would have been swallowed


def test_a_tap_on_the_title_reports_and_a_tap_elsewhere_does_not(cards):
    (card,) = cards.show(("now", "now", now()))
    cards.click(cards.one(card, "nowWhy"))
    cards.click(cards.find(card, "nowLabel")[0])
    assert cards.events == []
    cards.click(cards.one(card, "nowTitle"))
    assert cards.events == [{"kind": "titleClicked", "card": "now", "args": []}]
    (rows_card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    cards.click(cards.one(rows_card, "rowsTitle"))
    assert cards.events[-1] == {"kind": "titleClicked", "card": "needs", "args": []}


def test_wash_paints_the_left_edge_in_the_colour_and_fades_in_600_ms(cards):
    (card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    assert card.property("washLevel") == 0
    cards.call(card, "wash", "#5b9bd5")
    assert card.property("washLevel") > 0.5
    assert card.property("washColor").name() == "#5b9bd5"
    wash = cards.one(card, "rowsWash", visible_only=False)
    assert cards.geometry(wash, card)[:2] == (0, 0) and wash.height() == card.height()
    cards.pump(0.25)
    mid = card.property("washLevel")
    assert 0 < mid < 1
    cards.pump(0.6)
    assert card.property("washLevel") == 0
    cards.snap("chrome-wash")


# -- Now --

@pytest.mark.parametrize("n", [0, 1, 2, 4, 6])
def test_now_is_68_plus_26_a_step_tall(cards, n):
    route = steps(*[(f"step {i}", "pending") for i in range(n)])
    (card,) = cards.show(("now", "now", now(steps=route)))
    assert card.height() == 68 + 26 * n
    assert len(cards.find(card, "nowRow")) == n


def test_a_command_row_adds_20_and_a_caption_row_more_and_later_steps_move_down(cards):
    (card,) = cards.show(("now", "plain", now()))
    assert card.height() == 172        # four steps, nothing marked
    assert [cards.geometry(r, card)[1] for r in cards.find(card, "nowRow")] == [58 + 26 * i for i in range(4)]
    cards.set(card, model=now(edge="amber", command="sudo pacman -S --noconfirm docker")["model"])
    assert card.height() == 192        # page.md: 192 running with a command
    ys = [cards.geometry(r, card)[1] for r in cards.find(card, "nowRow")]
    assert ys[1] - ys[0] == 26 and ys[2] - ys[1] == 26 + 20 and ys[3] - ys[2] == 26
    cards.set(card, model=now(edge="red", command="rm -rf /var/lib/docker",
                              caption="can't be undone · Esc stops it")["model"])
    assert card.height() == 192 + 18
    cards.snap("now-3-red-command")


def test_page_md_geometry_of_the_route(cards):
    (card,) = cards.show(("now", "now", now(edge="amber", command="sudo pacman -S docker")))
    rows_ = cards.find(card, "nowRow")
    # The first disc's centre is 71 down; labels start 38 in; the pitch is 26.
    first = cards.geometry(rows_[0], card)
    assert first[1] + 13 == 71
    label = cards.one(rows_[0], "nowLabel")
    assert label.x() == 38
    command = cards.one(card, "nowCommand")
    bar = cards.one(card, "nowCommandBar")
    current = cards.geometry(rows_[1], card)
    assert cards.geometry(bar, card)[:2] == (34, current[1] + 13 + 11)
    assert bar.width() == 3 and bar.height() == 16 and bar.property("radius") == 1.5
    assert command.x() == 44 and command.property("font").pixelSize() == 11
    assert command.property("font").family() == "monospace"


def test_a_step_is_done_current_or_pending_by_its_look(cards):
    (card,) = cards.show(("now", "now", now()))
    done, current, pending, pending2 = cards.find(card, "nowRow")
    assert [r.property("phase") for r in (done, current, pending, pending2)] == \
        ["done", "current", "pending", "pending"]
    assert done.property("ticked") and not current.property("ticked") and not pending.property("ticked")
    assert done.property("discColor").name() == colour("ok")
    assert current.property("discColor").name() == colour("machine")
    assert pending.property("discColor").alpha() == 0
    assert done.property("labelColor").name() == colour("fg")
    assert current.property("labelColor").name() == colour("fg")
    assert pending.property("labelColor").name() == colour("muted")
    label = lambda r: cards.one(r, "nowLabel")   # noqa: E731
    assert label(current).property("font").weight() == QtGui.QFont.Medium
    assert label(done).property("font").weight() == QtGui.QFont.Normal
    assert label(current).property("text") == "Installing docker"
    assert label(pending).property("text") == "Enable the service"
    # Only the current step has a ring; only a pending step has the grey outline.
    rings = cards.find(card, "nowRing")
    assert len(rings) == 1 and abs(rings[0].width() - 19) < 0.01
    assert (rings[0].parent() == current and rings[0].x() + 9.5 == 22 and rings[0].y() + 9.5 == 13) or (
        (rings[0].x() + 9.5, rings[0].y() + 9.5) == (22, 13))
    cards.snap("now-1-running")


def test_the_plan_words_and_the_plain_words_read_the_same(cards):
    plain = steps(("a", "done"), ("b", "current"), ("c", "whatever"))
    (card,) = cards.show(("now", "now", now(steps=plain)))
    assert [r.property("phase") for r in cards.find(card, "nowRow")] == ["done", "current", "pending"]
    assert [r.property("status") for r in cards.find(card, "nowRow")] == ["done", "current", "whatever"]


def test_the_ring_breathes_from_small_and_strong_to_large_and_gone(cards):
    (card,) = cards.show(("now", "now", now()))
    ring = cards.one(card, "nowRing")
    seen = []
    for _ in range(12):
        cards.pump(0.15)
        seen.append((ring.property("scale"), ring.property("opacity")))
    scales = [s for s, _ in seen]
    opacities = [o for _, o in seen]
    assert all(0.7 - 0.01 <= s <= 1.7 + 0.01 for s in scales)
    assert all(0 - 0.01 <= o <= 0.95 + 0.01 for o in opacities)
    assert max(scales) - min(scales) > 0.5 and max(opacities) - min(opacities) > 0.4


def test_a_marked_step_shows_its_command_under_the_current_one_in_amber(cards):
    (card,) = cards.show(("now", "now", now(edge="amber", command="sudo pacman -S --noconfirm docker")))
    command = cards.one(card, "nowCommand")
    bar = cards.one(card, "nowCommandBar")
    assert command.property("text") == "sudo pacman -S --noconfirm docker"
    assert command.property("color").name() == colour("amberText")
    assert bar.property("color").name() == colour("amber")
    current = cards.find(card, "nowRow")[1]
    assert cards.geometry(command, card)[1] > cards.geometry(current, card)[1] + 13
    assert cards.geometry(command, card)[1] < cards.geometry(cards.find(card, "nowRow")[2], card)[1]
    assert not cards.find(card, "nowCaption")
    cards.snap("now-2-system-command")


def test_a_step_no_restore_point_can_undo_is_red_with_its_caption_under_the_command(cards):
    caption = "can't be undone · Esc stops it"
    (card,) = cards.show(("now", "now", now(edge="red", command="rm -rf /var/lib/docker", caption=caption)))
    cmd = cards.one(card, "nowCommand")
    cap = cards.one(card, "nowCaption")
    assert cap.property("text") == caption and cap.property("font").pixelSize() == 11
    assert cards.geometry(cap, card)[1] >= cards.geometry(cmd, card)[1] + cmd.height()
    assert cmd.property("color").name() == colour("redText")
    assert cap.property("color").name() == colour("redText")
    assert cards.one(card, "nowCommandBar").property("color").name() == colour("red")
    assert cards.one(card, "nowEdge").property("color").name() == colour("red")
    cards.snap("now-3-irreversible")


def test_a_command_outside_any_plan_sits_under_the_last_step_or_alone(cards):
    (card,) = cards.show(("now", "now", now(steps=steps(("Saving a restore point", "completed")),
                                             edge="amber", command="sudo systemctl restart docker")))
    only = cards.find(card, "nowRow")[0]
    assert cards.geometry(cards.one(card, "nowCommand"), card)[1] > cards.geometry(only, card)[1] + 13
    assert card.height() == 68 + 26 + 20
    (card,) = cards.show(("now", "now", now(steps=[], edge="amber", command="sudo systemctl restart docker")))
    assert cards.one(card, "nowCommand").isVisible() and card.height() == 68 + 20


def test_a_long_command_and_step_label_elide_inside_the_card(cards):
    long = "sudo pacman -S --noconfirm " + "very-long-package-name " * 12
    route = steps(("a very long step name that keeps going " * 5, "in_progress"))
    (card,) = cards.show(("now", "now", now(steps=route, edge="amber", command=long)))
    for name in ("nowCommand", "nowLabel"):
        it = cards.one(card, name)
        assert it.x() + it.width() == card.width() - 14
        assert it.property("contentWidth") <= it.width() + 0.5 and it.property("truncated")


def test_a_finished_turn_ticks_every_step_turns_green_and_drops_the_command(cards):
    done = now(done=True, running=False, edge="ok", why="done in 58 s · Undo is above the pill",
               command="sudo pacman -S docker", caption="can't be undone · Esc stops it")
    (card,) = cards.show(("now", "now", done))
    assert all(r.property("ticked") for r in cards.find(card, "nowRow"))
    assert not cards.find(card, "nowRing")
    assert not cards.find(card, "nowCommand") and not cards.find(card, "nowCaption")
    assert cards.one(card, "nowEdge").property("color").name() == colour("ok")
    assert card.height() == 172        # page.md: 172 done with four steps
    assert cards.one(card, "nowWhy").property("text") == "done in 58 s · Undo is above the pill"
    cards.snap("now-4-done")


def test_asked_by_builder_titles_read_as_the_title_says(cards):
    (card,) = cards.show(("now", "now", now(title="install qemu-full · asked by builder")))
    assert cards.one(card, "nowTitle").property("text") == "install qemu-full · asked by builder"
    cards.snap("now-5-asked-by-builder")


def test_a_changing_plan_updates_the_rows_in_place(cards):
    (card,) = cards.show(("now", "now", now()))
    rows_ = cards.find(card, "nowRow")
    rows_[1].setProperty("marker", 7)     # a delegate that is built again loses it
    ring = cards.one(card, "nowRing")
    ring.setProperty("marker", 9)
    moved = steps(("Save a restore point", "completed"), ("Installing docker", "completed"),
                  ("Enabling the service", "in_progress"), ("Check it works", "pending"))
    cards.set(card, model=now(steps=moved, why="Step 3 of 4 · Esc stops")["model"])
    again = cards.find(card, "nowRow")
    assert again[1].property("marker") == 7 and again[1].property("phase") == "done"
    assert again[2].property("phase") == "current"
    assert cards.one(card, "nowWhy").property("text") == "Step 3 of 4 · Esc stops"
    # The ring moves to the new current step; a new plan of another length builds the right rows.
    assert len(cards.find(card, "nowRing")) == 1
    cards.set(card, model=now(steps=moved[:2])["model"])
    assert len(cards.find(card, "nowRow")) == 2 and card.height() == 68 + 52


def test_a_model_with_nothing_in_it_draws_an_empty_card(cards):
    (card,) = cards.show(("now", "now", {"model": {}}), )
    assert card.height() == 68 and cards.find(card, "nowRow") == []
    cards.set(card, model=None)
    assert card.height() == 68


# -- rows --

def test_rows_cards_are_50_plus_the_rows_plus_18_tall(cards):
    needs, watching = cards.show(("rows", "needs", rows(*NEEDS)), ("rows", "watching", rows(*WATCHING)))
    assert needs.height() == 156           # page.md: Needs you 50 + 88 + 18
    assert watching.height() == 190        # Watching 50 + 34 + 88 + 18
    away = rows(row("a"), row("b"), row("c", "plain"))
    (card,) = cards.show(("rows", "away", away))
    assert card.height() == 200
    r = cards.find(card, "rowsRow")
    assert [x.height() for x in r] == [44, 44, 44]
    assert [cards.geometry(x, card)[1] for x in r] == [50, 94, 138]
    (card,) = cards.show(("rows", "batch", rows(row("m", "meter", meter=0.7, tone="sessions"), row("d"))))
    assert card.height() == 146            # Batch 50 + 34 + 44 + 18


def test_a_meter_row_has_label_meta_percent_and_a_track_filled_by_who(cards):
    (card,) = cards.show(("rows", "watching", rows(*WATCHING[:1], title="Watching")))
    r = cards.one(card, "rowsRow")
    track = cards.one(r, "rowsMeter")
    assert cards.geometry(track, r) == (14, 28, 272, 6) and track.property("radius") == 3
    assert track.property("color").name() == colour("raised")
    assert abs(track.property("fillWidth") - 0.43 * 272) < 0.01
    assert track.property("fillColor").name() == colour("machine")
    assert cards.one(r, "rowsPercent").property("text") == "43%"
    assert cards.one(r, "rowsMeta").property("text") == "12 MB/s · 4 min"
    title = cards.one(r, "rowsRowTitle")
    assert title.x() == 14 and title.property("text") == "Ubuntu 26.04 ISO"
    pct = cards.one(r, "rowsPercent")
    assert abs(pct.x() + pct.width() - 286) < 0.01
    meta = cards.one(r, "rowsMeta")
    assert abs(meta.x() + meta.width() - 250) < 0.01
    assert title.x() + title.width() <= meta.x()
    cards.snap("rows-meter")


@pytest.mark.parametrize("tone", ["machine", "sessions", "you", "ok", "red"])
def test_tones_map_to_who(cards, tone):
    (card,) = cards.show(("rows", "c", rows(row("m", "meter", meter=0.5, tone=tone),
                                            row("d", tone=tone))))
    r_meter, r_dot = cards.find(card, "rowsRow")
    assert cards.one(r_meter, "rowsMeter").property("fillColor").name() == colour(tone)
    assert cards.one(r_dot, "rowsDot").property("color").name() == colour(tone)


def test_a_meter_that_is_odd_is_clamped_or_empty(cards):
    (card,) = cards.show(("rows", "c", rows(row("over", "meter", meter=1.7), row("under", "meter", meter=-3),
                                            row("none", "meter", meter=None), row("zero", "meter", meter=0))))
    over, under, none, zero = cards.find(card, "rowsRow")
    assert cards.one(over, "rowsMeter").property("fillWidth") == 272
    assert cards.one(over, "rowsPercent").property("text") == "100%"
    assert cards.one(under, "rowsMeter").property("fillWidth") == 0
    assert cards.one(none, "rowsMeter").property("fillWidth") == 0
    assert not cards.find(none, "rowsPercent")
    assert cards.one(zero, "rowsPercent").property("text") == "0%"


def test_a_dot_row_has_its_dot_title_sub_and_button_where_the_page_puts_them(cards):
    (card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    first, second = cards.find(card, "rowsRow")
    dot = cards.one(first, "rowsDot")
    assert (cards.geometry(dot, first)[0] + 4.5, cards.geometry(dot, first)[1] + 4.5) == (19, 22)
    assert dot.width() == 9 and dot.property("color").name() == colour("you")
    title = cards.one(first, "rowsRowTitle")
    sub = cards.one(first, "rowsRowSub")
    assert title.x() == 32 and sub.x() == 32
    assert title.property("font").pixelSize() >= 12 and sub.property("font").pixelSize() == 11
    assert title.property("color").name() == colour("fg") and sub.property("color").name() == colour("muted")
    button = cards.one(first, "rowsButton")
    x, y, w, h = cards.geometry(button, first)
    assert (y, h) == (12, 22) and abs(x + w - 286) < 0.01 and w >= 46
    assert button.property("radius") == 11
    # Words stop short of the button.
    assert title.x() + title.width() <= x - 8 + 0.01 and sub.x() + sub.width() <= x - 8 + 0.01
    cards.snap("rows-needs-you")


def test_open_is_quiet_and_do_it_is_orange_with_white_on_it(cards):
    (card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    open_row, do_row = cards.find(card, "rowsRow")
    quiet = cards.one(open_row, "rowsButton")
    loud = cards.one(do_row, "rowsButton")
    assert quiet.property("text") == "Open" and loud.property("text") == "Do it"
    assert not quiet.property("primary") and loud.property("primary")
    assert quiet.property("color").name() == colour("raised")
    assert loud.property("color").name() == colour("machine")
    assert loud.width() >= 53


def test_the_buttons_report_their_row_and_their_words_and_nothing_else_does(cards):
    (card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    open_row, do_row = cards.find(card, "rowsRow")
    cards.click(cards.one(open_row, "rowsRowTitle"))
    cards.click(cards.one(do_row, "rowsRowSub"))
    cards.click(cards.one(do_row, "rowsDot"))
    assert cards.events == []
    cards.click(cards.one(open_row, "rowsButton"))
    cards.click(cards.one(do_row, "rowsButton"))
    assert cards.events == [
        {"kind": "rowAction", "card": "needs", "args": ["reviewer", "Open"]},
        {"kind": "rowAction", "card": "needs", "args": ["builder", "Do it"]},
    ]


def test_the_small_x_removes_its_row_and_a_row_can_have_both(cards):
    (card,) = cards.show(("rows", "watching", rows(*WATCHING)))
    meter, oneshot, timer = cards.find(card, "rowsRow")
    assert not cards.find(meter, "rowsRemove") and not cards.find(meter, "rowsButton")
    drop = cards.one(oneshot, "rowsRemove")
    x, y, w, h = cards.geometry(drop, oneshot)
    assert abs(x + w - 286) < 0.01 and not cards.find(oneshot, "rowsButton")
    cards.click(drop)
    cards.click(cards.one(timer, "rowsRemove"))
    assert cards.events == [{"kind": "rowRemove", "card": "watching", "args": ["build"]},
                            {"kind": "rowRemove", "card": "watching", "args": ["timer"]}]
    both = row("job", title="pacman -Syu", sub="pacman: could not resolve host", tone="red",
               button="Why?", remove=True)
    (card,) = cards.show(("rows", "failed", rows(both, title="Watching")))
    only = cards.one(card, "rowsRow")
    b = cards.geometry(cards.one(only, "rowsButton"), only)
    d = cards.geometry(cards.one(only, "rowsRemove"), only)
    assert b[0] + b[2] <= d[0] and abs(d[0] + d[2] - 286) < 0.01
    cards.click(cards.one(only, "rowsButton"))
    assert cards.events[-1] == {"kind": "rowAction", "card": "failed", "args": ["job", "Why?"]}


def test_a_press_reports_the_row_it_landed_on_even_if_the_rows_move_before_the_release(cards):
    (card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    do_button = cards.one(cards.find(card, "rowsRow")[1], "rowsButton")
    at = cards.centre(do_button)
    QtTest.QTest.mousePress(cards.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
    cards.pump()
    # The row pressed goes away and another takes the first place: the second place now holds "reviewer".
    cards.set(card, model=rows(row("other", title="other", button="Do it"), NEEDS[0])["model"])
    assert cards.one(cards.find(card, "rowsRow")[1], "rowsButton").property("text") == "Open"
    QtTest.QTest.mouseRelease(cards.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
    cards.pump()
    assert cards.events == [{"kind": "rowAction", "card": "needs", "args": ["builder", "Do it"]}]


def test_only_a_live_dot_in_the_machines_or_a_sessions_colour_pulses(cards):
    rs = (row("a", tone="machine", pulse=True), row("b", tone="sessions", pulse=True),
          row("c", tone="you", pulse=True), row("d", tone="machine"), row("e", "plain", tone="machine", pulse=True))
    (card,) = cards.show(("rows", "c", rows(*rs)))
    pulsing = [r.property("pulsing") for r in cards.find(card, "rowsRow")]
    assert pulsing == [True, True, False, False, True]
    visible_rings = [it for r in cards.find(card, "rowsRow") for it in cards.find(r, "rowsRing")]
    assert len(visible_rings) == 2       # a plain row has no dot to ring
    assert all(abs(it.width() - 16) < 0.01 for it in visible_rings)
    cards.pump(0.4)
    assert {it.property("opacity") < 0.95 for it in visible_rings} == {True}


def test_a_plain_row_has_no_dot_and_starts_at_14(cards):
    (card,) = cards.show(("rows", "away", rows(row("undo", "plain", title="09:05  tidy my downloads",
                                                   sub="23 files into 4 folders", button="Undo"))))
    r = cards.one(card, "rowsRow")
    assert not cards.find(r, "rowsDot")
    assert cards.one(r, "rowsRowTitle").x() == 14 and cards.one(r, "rowsRowSub").x() == 14


def test_a_finished_row_is_green_and_a_failed_one_is_red_with_its_last_line_and_why(cards):
    finished = row("iso", title="Ubuntu 26.04 ISO", sub="done", tone="ok")
    failed = row("upd", title="pacman -Syu", sub="pacman: could not resolve host", tone="red", button="Why?")
    (card,) = cards.show(("rows", "watching", rows(finished, failed, title="Watching",
                                                   why="2 counting · each ends with one line in the pill")))
    done, bad = cards.find(card, "rowsRow")
    assert cards.one(done, "rowsDot").property("color").name() == colour("ok")
    assert cards.one(bad, "rowsDot").property("color").name() == colour("red")
    assert cards.one(bad, "rowsRowSub").property("text") == "pacman: could not resolve host"
    assert cards.one(bad, "rowsButton").property("text") == "Why?"
    cards.snap("rows-finished-and-failed")


def test_long_row_words_elide_inside_the_card_and_short_of_the_button(cards):
    long = "a title that is far too long for a row in a card three hundred wide " * 3
    (card,) = cards.show(("rows", "c", rows(row("a", title=long, sub=long, button="Do it"),
                                            row("b", title=long, sub=long),
                                            row("c", "meter", long, meter=0.3, meter_text="14.6 of 16 GB · mostly builder"))))
    a, b, c = cards.find(card, "rowsRow")
    button_x = cards.geometry(cards.one(a, "rowsButton"), a)[0]
    for name in ("rowsRowTitle", "rowsRowSub"):
        it = cards.one(a, name)
        assert it.x() + it.width() <= button_x - 8 + 0.01 and it.property("truncated")
        it = cards.one(b, name)
        assert it.x() + it.width() == 286 and it.property("contentWidth") <= it.width() + 0.5
    title = cards.one(c, "rowsRowTitle")
    meta = cards.one(c, "rowsMeta")
    assert title.x() + title.width() <= meta.x() and meta.x() + meta.width() <= 250.01


def test_rows_update_in_place_and_a_new_row_washes_its_colour_on_the_edge(cards):
    (card,) = cards.show(("rows", "watching", rows(*WATCHING)))
    assert card.property("washLevel") == 0
    meter = cards.find(card, "rowsRow")[0]
    meter.setProperty("marker", 3)
    ring = cards.find(cards.find(card, "rowsRow")[2], "rowsRing")[0]
    ring.setProperty("marker", 4)
    # A meter that moves is not a new row: nothing washes and nothing is built again.
    moved = (row("iso", "meter", "Ubuntu 26.04 ISO", tone="machine", meter=0.44, meter_text="12 MB/s · 4 min"),
             ) + WATCHING[1:]
    cards.set(card, model=rows(*moved, title="Watching")["model"])
    assert card.property("washLevel") == 0
    again = cards.find(card, "rowsRow")
    assert again[0].property("marker") == 3
    assert abs(cards.one(again[0], "rowsMeter").property("fillWidth") - 0.44 * 272) < 0.01
    assert cards.find(again[2], "rowsRing")[0].property("marker") == 4
    # A row that leaves is not either.
    cards.set(card, model=rows(*moved[:2], title="Watching")["model"])
    assert card.property("washLevel") == 0 and card.height() == 50 + 34 + 44 + 18
    # A new one washes, in its own colour, and the wash fades.
    cards.set(card, model=rows(*moved[:2], row("dl", tone="sessions", title="Batch"), title="Watching")["model"])
    assert card.property("washLevel") > 0.4
    assert card.property("washColor").name() == colour("sessions")
    cards.pump(0.8)
    assert card.property("washLevel") == 0


def test_a_card_built_with_rows_does_not_wash_for_them(cards):
    (card,) = cards.show(("rows", "needs", rows(*NEEDS)))
    cards.pump(0.1)
    assert card.property("washLevel") == 0


# -- the strip --

def test_a_strip_is_28_tall_and_as_wide_as_its_words_plus_24_and_14_for_a_dot(cards):
    plain, dotted, ringed = cards.show(
        ("strip", "plain", {"text": "ISO 43%"}),
        ("strip", "dotted", {"text": "step 2 of 4", "dot": THEME["machine"], "ring": True}),
        ("strip", "amber", {"text": "memory 91%", "textColor": THEME["amber"]}))
    for it in (plain, dotted, ringed):
        assert it.height() == THEME["stripHeight"] == 28 and it.property("radius") == 14
        assert it.objectName() == "deskStrip"
    text = lambda s: cards.one(s, "deskStripText")   # noqa: E731
    assert abs(plain.width() - (text(plain).property("implicitWidth") + 24)) < 0.01
    assert abs(dotted.width() - (text(dotted).property("implicitWidth") + 24 + 14)) < 0.01
    assert text(plain).x() == 12 and text(dotted).x() == 26
    assert text(plain).property("color").name() == colour("muted")
    assert text(ringed).property("color").name() == colour("amber")
    assert text(plain).property("font").pixelSize() == 13
    cards.snap("strip-plain")


def test_the_dot_ring_and_mark_sit_where_the_page_puts_them(cards):
    dotted, ringed, marked, bare = cards.show(
        ("strip", "dotted", {"text": "a", "dot": THEME["sessions"]}),
        ("strip", "ringed", {"text": "step 2 of 4", "dot": THEME["machine"], "ring": True}),
        ("strip", "marked", {"text": "2 need you", "dot": THEME["you"], "mark": True, "outlined": True}),
        ("strip", "bare", {"text": "ISO 43%", "ring": True, "mark": True}))
    dot = cards.one(dotted, "deskStripDot")
    assert (cards.geometry(dot, dotted)[0] + 4, cards.geometry(dot, dotted)[1] + 4) == (16, 14)
    assert dot.width() == 8 and dot.property("color").name() == colour("sessions")
    assert not cards.find(dotted, "deskStripMark") and not cards.find(dotted, "deskStripRing")
    ring = cards.find(ringed, "deskStripRing")
    assert len(ring) == 1 and abs(ring[0].width() - 14) < 0.01
    assert (ring[0].x() + 7, ring[0].y() + 7) == (16, 14)       # not through mapToItem: it is scaling
    mark = cards.one(marked, "deskStripMark")
    assert mark.width() == 6 and mark.property("color").name() == colour("you")
    assert (cards.geometry(mark, marked)[0] + 3, cards.geometry(mark, marked)[1] + 3) == (16 + 5, 14 - 5)
    # Without a dot there is nothing to ring or mark.
    assert not cards.find(bare, "deskStripDot") and not cards.find(bare, "deskStripMark")
    assert not cards.find(bare, "deskStripRing")
    cards.snap("strip-dots")


def test_a_strip_border_is_stronger_when_outlined_or_hovered(cards):
    quiet, loud = cards.show(("strip", "quiet", {"text": "ISO 43%"}),
                             ("strip", "loud", {"text": "2 need you", "outlined": True}))
    assert quiet.property("borderColor").name() == colour("border")
    assert loud.property("borderColor").name() == colour("borderStrong")
    cards.hover(quiet)
    assert quiet.property("hovered") and quiet.property("borderColor").name() == colour("borderStrong")
    cards.away()
    assert quiet.property("borderColor").name() == colour("border")


def test_a_strip_reports_clicks_and_hover_in_and_out(cards):
    a, b = cards.show(("strip", "a", {"text": "ISO 43%"}), ("strip", "b", {"text": "2 need you"}))
    cards.hover(a)
    assert cards.events == [{"kind": "hover", "card": "a", "args": [True]}]
    cards.away()
    assert cards.events[1:] == [{"kind": "hover", "card": "a", "args": [False]}]
    cards.click(b)
    assert cards.events[2:] == [{"kind": "hover", "card": "b", "args": [True]},
                                {"kind": "clicked", "card": "b", "args": []}]


def test_a_strip_elides_at_a_fixed_width_like_the_queue_chips(cards):
    (strip,) = cards.show(("strip", "long", {"text": "3 alive · " + "builder, you, reviewer, " * 10}))
    text = cards.one(strip, "deskStripText")
    assert text.width() == strip.property("maxTextWidth") == 220
    assert text.property("truncated") and strip.width() == 220 + 24
    cards.set(strip, maxTextWidth=100)
    assert strip.width() == 124


def test_the_gallery_of_states_draws_without_qml_warnings(cards):
    cards.show(("now", "a", now()), ("now", "b", now(edge="amber", command="sudo pacman -S docker")),
               ("now", "c", now(edge="red", command="rm -rf /x", caption="can't be undone · Esc stops it")),
               ("now", "d", now(done=True, edge="ok")), ("rows", "e", rows(*NEEDS)),
               ("rows", "f", rows(*WATCHING)), ("rows", "g", rows()),
               ("strip", "h", {"text": "step 2 of 4", "dot": THEME["machine"], "ring": True}),
               ("strip", "i", {"text": "2 need you", "dot": THEME["you"], "mark": True, "outlined": True}))
    cards.pump(0.3)
    cards.snap("overview")
    cards.show()      # and taking them all away again
    assert cards.warnings == []


def test_every_state_can_be_photographed(cards):
    states = {
        "now-running": ("now", now()),
        "now-system": ("now", now(edge="amber", command="sudo pacman -S --noconfirm docker")),
        "now-irreversible": ("now", now(edge="red", command="rm -rf /var/lib/docker",
                                        caption="can't be undone · Esc stops it")),
        "now-done": ("now", now(done=True, running=False, edge="ok", why="done in 58 s · Undo is above the pill")),
        "rows-watching": ("rows", rows(*WATCHING, title="Watching", why="3 counting · each ends with one line in the pill")),
        "rows-needs": ("rows", rows(*NEEDS)),
    }
    for name, (kind, props) in states.items():
        cards.show((kind, name, props))
        cards.snap(name)
    assert cards.warnings == []

"""Diagram.qml (the kit's picture component), drawn from cards in an offscreen window.

Set BOMBADIL_SCREENS=<dir> to save a picture of each one.
"""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
QtCore = pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
QtQml = pytest.importorskip("PySide6.QtQml", exc_type=ImportError)
pytest.importorskip("PySide6.QtQuick", exc_type=ImportError)
QtTest = pytest.importorskip("PySide6.QtTest", exc_type=ImportError)

from bombadil import cards, sysmap  # noqa: E402

import test_sysmap as fixtures  # noqa: E402  (recorded command output)

KIT = Path(__file__).resolve().parents[1] / "share" / "qml" / "Bombadil"

HARNESS = """
import QtQuick
import "%s" as Kit

Window {
    id: w
    width: %d; height: 700; visible: true; color: "#2b3440"
    property var opened: []
    property var picked: []
    Kit.Diagram {
        id: d
        objectName: "diagram"
        x: 14; y: 14; width: parent.width - 28
        onOpened: target => w.opened = w.opened.concat([target])
        onPicked: node => w.picked = w.picked.concat([node.id])
    }
}
"""


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Picture:
    def __init__(self, app, tmp_path, width=900):
        self.app = app
        qml = tmp_path / "harness.qml"
        qml.write_text(HARNESS % (KIT.as_uri(), width))
        self.engine = QtQml.QQmlApplicationEngine()
        self.warnings: list[str] = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        QtCore.qInstallMessageHandler(lambda mode, ctx, msg: self.warnings.append(msg)
                                      if ".qml" in (ctx.file or "") or "TypeError" in msg else None)
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        self.d = self.win.findChild(QtCore.QObject, "diagram")
        self.pump()

    def pump(self, ms=300):
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def show(self, spec):
        self.d.setProperty("spec", spec)
        self.pump()
        return self

    def find(self, name, root=None):
        """An item by objectName, through the visual tree (Repeater delegates are not QObject children)."""
        todo = [root or self.win.contentItem()]
        while todo:
            item = todo.pop()
            if item.objectName() == name:
                return item
            todo.extend(item.childItems())
        return None

    def find_all(self, root=None):
        todo, out = [root or self.win.contentItem()], []
        while todo:
            item = todo.pop()
            out.append(item)
            todo.extend(item.childItems())
        return out

    def box(self, node_id):
        return self.find(f"box-{node_id}")

    def rect(self, node_id):
        b = self.box(node_id)
        return (b.property("x"), b.property("y"), b.property("width"), b.property("height"))

    def text(self, name):
        t = self.find(name)
        return t.property("text") if t is not None and t.property("visible") else None

    def click(self, node_id):
        b = self.box(node_id) or self.find(f"step-{node_id}")
        x, y = b.property("x") + b.property("width") / 2, b.property("y") + b.property("height") / 2
        p = self.find("diagramCanvas")
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier,
                                QtCore.QPoint(int(14 + p.property("x") + x), int(14 + p.property("y") + y)))
        self.pump(100)

    def save(self, name):
        shots = os.environ.get("BOMBADIL_SCREENS")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            self.win.setHeight(int(self.d.property("implicitHeight")) + 40)
            self.pump(100)
            self.win.grabWindow().save(str(Path(shots) / f"diagram-{name}.png"))


@pytest.fixture
def pic(app, tmp_path):
    return Picture(app, tmp_path)


def card(**over):
    spec = {"shape": "chain", "title": "How a VPN works", "nodes": [{"label": "Laptop"}, {"label": "Tunnel"},
                                                                  {"label": "Internet"}]}
    spec.update(over)
    out, errors = cards.validate_diagram(spec)
    assert out is not None, errors
    return out


# -- chain --

def test_a_chain_is_one_row_when_it_fits_and_wraps_to_rows_when_it_does_not(app, tmp_path):
    wide = Picture(app, tmp_path / "a", 900) if (tmp_path / "a").mkdir() is None else None
    wide.show(card(nodes=[{"label": f"Step {i}"} for i in range(1, 6)]))
    ys = {wide.rect(f"n{i}")[1] for i in range(1, 6)}
    assert len(ys) == 1
    xs = [wide.rect(f"n{i}")[0] for i in range(1, 6)]
    assert xs == sorted(xs) and xs[0] >= 0
    assert max(wide.rect(f"n{i}")[0] + wide.rect(f"n{i}")[2] for i in range(1, 6)) <= 900 - 28
    assert wide.warnings == []
    wide.save("chain")
    narrow = Picture(app, tmp_path / "b", 420) if (tmp_path / "b").mkdir() is None else None
    narrow.show(card(nodes=[{"label": f"Step {i}"} for i in range(1, 8)]))
    rows = sorted({narrow.rect(f"n{i}")[1] for i in range(1, 8)})
    assert len(rows) >= 2
    for i in range(1, 8):   # nothing is wider than the picture or overlaps its neighbour
        x, y, w, h = narrow.rect(f"n{i}")
        assert x >= 0 and x + w <= 420 - 28 + 1
    assert narrow.warnings == []


def test_the_picture_grows_to_hold_its_rows_title_and_sentence(pic):
    pic.show(card(say="Everything goes through the tunnel first."))
    h1 = pic.d.property("implicitHeight")
    assert pic.text("diagramTitle") == "How a VPN works"
    assert pic.text("diagramSay") == "Everything goes through the tunnel first."
    pic.show(card(nodes=[{"label": f"Step {i}"} for i in range(1, 12)]))
    assert pic.d.property("implicitHeight") > h1 - 20
    pic.d.setProperty("showTitle", False)
    pic.pump(100)
    assert pic.text("diagramTitle") is None and pic.text("diagramSay") is None
    assert pic.warnings == []


def test_a_link_label_makes_room_between_the_boxes(pic):
    plain = card()
    labelled = card(links=[{"from": "n1", "to": "n2", "label": "encrypted"}, {"from": "n2", "to": "n3"}])
    pic.show(plain)
    gap_plain = pic.rect("n2")[0] - (pic.rect("n1")[0] + pic.rect("n1")[2])
    pic.show(labelled)
    gap_labelled = pic.rect("n2")[0] - (pic.rect("n1")[0] + pic.rect("n1")[2])
    assert gap_labelled > gap_plain + 40


# -- states --

def test_states_are_shown_by_colour_and_by_a_mark(pic):
    pic.show(card(nodes=[{"label": "a"}, {"label": "b", "state": "warn"}, {"label": "c", "state": "bad"},
                         {"label": "d", "state": "new"}, {"label": "e", "state": "gone"},
                         {"label": "f", "state": "active"}]))
    border = {k: pic.box(f"n{i}").property("edge").name()
              for i, k in enumerate("abcdef", 1)}
    assert border == {"a": "#2a2f36", "b": "#e0a93b", "c": "#d05555", "d": "#d97757", "e": "#5c636b", "f": "#5b9bd5"}
    assert pic.box("n5").property("opacity") < 1                    # gone is dim...
    label = pic.find("boxLabel", pic.box("n5"))
    assert label.property("font").strikeOut() is True               # ...and struck through
    assert pic.find("boxLabel", pic.box("n1")).property("font").strikeOut() is False
    assert pic.warnings == []
    pic.save("states")


def test_the_highlight_is_lit_and_the_rest_dims(pic):
    pic.show(card(highlight=["n2"]))
    assert pic.box("n2").property("opacity") == 1
    assert pic.box("n1").property("opacity") < 1 and pic.box("n3").property("opacity") < 1
    assert pic.box("n2").property("edgeWidth") == 2
    pic.show(card())
    assert pic.box("n1").property("opacity") == 1


# -- layers --

def test_layers_put_a_box_below_what_points_at_it(pic):
    spec = {"shape": "layers", "title": "What Bluetooth needs",
            "nodes": [{"id": i, "label": i} for i in "abcd"],
            "links": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}, {"from": "b", "to": "d"}]}
    pic.show(cards.validate_diagram(spec)[0])
    a, b, c, d = (pic.rect(i) for i in "abcd")
    assert a[1] < b[1] == c[1] < d[1]
    assert b[0] < c[0]
    assert a[0] < b[0] + b[2] and a[0] + a[2] > c[0]   # the parent sits over its children
    assert pic.warnings == []
    pic.save("layers")


# -- compare --

def test_compare_puts_before_left_after_right_and_a_key_on_one_row(pic):
    pic.show(card(shape="compare", nodes=[
        {"label": "DNS", "side": "before", "sub": "192.168.1.1", "key": "dns", "state": "gone"},
        {"label": "DNS", "side": "after", "sub": "1.1.1.1", "key": "dns", "state": "new"},
        {"label": "Tunnel", "side": "after", "state": "new"}]))
    before, after, tunnel = pic.rect("n1"), pic.rect("n2"), pic.rect("n3")
    assert before[0] < after[0] and before[1] == after[1] < tunnel[1]
    assert after[0] == tunnel[0] and after[0] + after[2] == 900 - 28
    assert pic.warnings == []
    pic.save("compare")


# -- timeline --

def test_timeline_rows_carry_their_time_and_a_bar_as_long_as_it_took(pic):
    pic.show(sysmap.capture_boot(fixtures.fake({"systemd-analyze critical-chain": fixtures.CHAIN,
                                                "systemd-analyze time": fixtures.TIME}))["card"])
    steps = [pic.find(f"step-u{i}") for i in range(1, 13)]
    assert all(s is not None for s in steps)
    assert [s.property("y") for s in steps] == [i * 30 for i in range(12)]
    slow = next(s for s in steps if s.property("state") == "warn")
    assert slow.property("took") == pytest.approx(4.25) and slow.property("longest") == pytest.approx(4.25)
    assert pic.warnings == []
    pic.save("timeline")


# -- clicking --

def test_a_box_that_opens_something_says_what_when_clicked(pic):
    pic.show(card(nodes=[{"label": "NetworkManager", "opens": {"kind": "unit", "value": "NetworkManager.service"}},
                         {"label": "Nothing to open"}]))
    pic.click("n1")
    assert pic.win.property("opened").toVariant() == [{"kind": "unit", "value": "NetworkManager.service"}]
    pic.click("n2")
    assert pic.win.property("opened").toVariant() == [{"kind": "unit", "value": "NetworkManager.service"}]
    assert pic.win.property("picked").toVariant() == ["n1", "n2"]


def test_a_timeline_row_opens_its_thing_too(pic):
    pic.show(sysmap.capture_boot(fixtures.fake({"systemd-analyze critical-chain": fixtures.CHAIN}))["card"])
    pic.click("u7")
    assert pic.win.property("opened").toVariant()[0]["kind"] == "unit"


# -- a card still being written, and bad data --

def test_a_card_being_written_says_so_and_grows_a_box_at_a_time(pic):
    part = cards.partial_diagram('{"shape": "chain", "title": "How a VPN works", "nodes": [{"label": "Laptop"}, '
                                 '{"label": "Tunnel"}, {"label": "Int')
    pic.show(part)
    assert pic.box("n1") is not None and pic.box("n2") is not None and pic.box("n3") is None
    drawing = [c for c in pic.find_all() if c.property("text") == "drawing…"]
    assert drawing and drawing[0].property("visible") is True
    pic.show(card())
    drawing = [c for c in pic.find_all() if c.property("text") == "drawing…"]
    assert not drawing[0].property("visible")
    assert pic.box("n3") is not None and pic.warnings == []
    pic.save("drawing")


@pytest.mark.parametrize("spec", [None, {}, {"shape": "layers"}, {"shape": "compare", "nodes": [{"id": "a", "label": "a"}]},
                                  {"shape": "timeline", "nodes": [{"id": "a", "label": "a", "weight": "x"}]},
                                  {"shape": "chain", "nodes": [{"id": "a", "label": "a"}], "links": [{"from": "a", "to": "zz"}]},
                                  {"shape": "chain", "nodes": "not a list"}, "a string"])
def test_odd_data_draws_nothing_and_warns_about_nothing(pic, spec):
    pic.show(spec)
    assert pic.warnings == []


def test_a_card_from_json_draws_the_same_as_one_from_python(pic):
    import json
    c = card(highlight=["n2"])
    pic.show(c)
    before = [pic.rect(f"n{i}") for i in (1, 2, 3)]
    pic.d.setProperty("spec", None)
    pic.pump(100)
    pic.win.setProperty("js", json.dumps(c))
    pic.show(json.loads(json.dumps(c)))
    assert [pic.rect(f"n{i}") for i in (1, 2, 3)] == before

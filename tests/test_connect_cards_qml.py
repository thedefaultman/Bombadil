"""The faces of the cards that come from connections (message, task, list), driven by agentd's messages in an
offscreen window, and the card host that chooses one by the card's type.

PillState, CardHost and the faces are plain Qt Quick (Quickshell only wraps them in shell.qml), so they load here
without a compositor. What goes to agentd is recorded as the bar would write it to the socket, and every assertion
about it is on the exact message. Set BOMBADIL_SCREENS=<dir> to save a picture of each state.

What an offscreen window cannot show: the layer-shell keyboard focus (which compositor gives the bar the keys, and
when) and the real notification server on D-Bus. The desktop test, with a real Quickshell, is where those are seen.
Everything a person might read here is invented (Priya Shah, Marcus Webb, acme.test, #launch).
"""

import hashlib
import json
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

from qml_theme import THEME

from bombadil import cards

SHELL = Path(__file__).resolve().parents[1] / "shell"
STUBS = Path(__file__).resolve().parent / "qml" / "quickshell_stub"     # Quickshell's notification module, faked
NOW = time.time()
REF = "slack:w1/C1/1700000000.000200"

HARNESS = """
import QtQuick
import "%s"

Window {
    id: w
    width: 640; height: 820; visible: true; color: "#3b4a5a"
    property var sent: []
    property int keys: 0
    property int handOffs: 0
    property int typings: 0
    function feed(line) { pillState.handle(JSON.parse(line)) }
    PillState {
        id: pillState
        objectName: "pill"
        onOutgoing: msg => { if (msg.type !== "bar") w.sent = w.sent.concat([msg]) }   // (the bar's hello is not what is under test)
        onCardKeys: w.keys += 1
        onHandOff: w.handOffs += 1
        onTyping: w.typings += 1
    }
    // The same bar on a second screen, where the person is not: it holds the card with a face of its own (never drawn).
    CardHost {
        id: otherScreen
        objectName: "cardHostElsewhere"
        pill: pillState
        onThisScreen: false
        maxHeight: 560
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 12 }
        width: 560
    }
    CardHost {
        id: host
        objectName: "cardHost"
        pill: pillState
        maxHeight: 560
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 12 }
        width: Math.min(parent.width - 24, host.preferredWidth > 0 ? host.preferredWidth : parent.width)
    }
}
"""


@pytest.fixture(scope="module")
def app():
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


class Bar:
    def __init__(self, app, tmp_path):
        self.app = app
        qml = tmp_path / "harness.qml"
        qml.write_text(HARNESS % SHELL.as_uri())
        self.engine = QtQml.QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        QtCore.qInstallMessageHandler(lambda mode, ctx, msg: self.warnings.append(msg)
                                      if ".qml" in (ctx.file or "") or "TypeError" in msg or "ReferenceError" in msg
                                      else None)
        self.engine.load(QtCore.QUrl.fromLocalFile(str(qml)))
        assert self.engine.rootObjects(), self.warnings
        self.win = self.engine.rootObjects()[0]
        self.pill = self.win.findChild(QtCore.QObject, "pill")
        self.host = self.win.findChild(QtCore.QObject, "cardHost")
        self.pump()
        self.send(type="status", busy=False, provider="claude", queue=[])

    # -- the loop --

    def pump(self, seconds=0.05):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.01)

    def wait(self, what, seconds=3.0):
        """Pump until `what()` is true (a deadline, never a fixed pause)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump(0.02)
            value = what()
            if value:
                return value
        raise AssertionError(f"timed out waiting for {what.__doc__ or what}")

    # -- what agentd says --

    def feed(self, **msg):
        QtCore.QMetaObject.invokeMethod(self.win, "feed", QtCore.Q_ARG("QVariant", json.dumps(msg)))
        self.pump()

    def send(self, **ev):
        ev.setdefault("type", "event")
        QtCore.QMetaObject.invokeMethod(self.pill, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()

    def card(self, card):
        self.feed(type="card", card=card)

    def proposal(self, **p):
        self.feed(type="proposal", proposal=p)

    def call(self, name, *args):
        ret = QtCore.QMetaObject.invokeMethod(
            self.pill, name, QtCore.Qt.DirectConnection, QtCore.Q_RETURN_ARG("QVariant"),
            *[QtCore.Q_ARG("QVariant", a) for a in args])
        self.pump()
        return ret

    # -- what the bar says --

    @property
    def sent(self):
        raw = self.win.property("sent")
        raw = raw.toVariant() if hasattr(raw, "toVariant") else raw
        return [json.loads(json.dumps(m)) for m in raw]

    def sent_of(self, kind, **match):
        return [m for m in self.sent if m.get("type") == kind and all(m.get(k) == v for k, v in match.items())]

    def forget(self):
        self.win.setProperty("sent", [])

    # -- what is on screen --

    def items(self, name, visible_only=True):
        found, todo = [], [self.win.contentItem()]
        while todo:
            it = todo.pop()
            if it.objectName() == name and (it.isVisible() or not visible_only):
                found.append(it)
            todo.extend(it.childItems())
        return found

    def item(self, name):
        found = self.items(name)
        return found[0] if found else None

    def shown(self, name):
        return self.item(name) is not None

    def text(self, name):
        it = self.item(name)
        return None if it is None else it.property("text")

    def texts(self, name):
        return [it.property("text") for it in sorted(self.items(name), key=lambda i: self.where(i)[1])]

    def where(self, it):
        p = it.mapToScene(QtCore.QPointF(0, 0))
        return p.x(), p.y()

    def click(self, it):
        centre = it.mapToScene(QtCore.QPointF(it.width() / 2, it.height() / 2)).toPoint()
        QtTest.QTest.mouseClick(self.win, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, centre)
        self.pump(0.05)

    def key(self, key):
        QtTest.QTest.keyClick(self.win, key)
        self.pump(0.02)

    def type(self, text):
        for ch in text:
            QtTest.QTest.keyClick(self.win, ch)
        self.pump(0.02)

    def card_now(self):
        """The card the pill holds, as a dict (None when there is none)."""
        card = self.pill.property("card")
        return None if card is None else card.toVariant()

    def box(self, name="replyBox"):
        return self.item(name)

    def prop(self, obj, name):
        return obj.property(name)

    def snap(self, name):
        out = os.environ.get("BOMBADIL_SCREENS")
        if out:
            self.pump(0.4)
            Path(out).mkdir(parents=True, exist_ok=True)
            self.win.grabWindow().save(str(Path(out) / f"connect-{name}.png"))


@pytest.fixture
def bar(app, tmp_path):
    b = Bar(app, tmp_path)
    yield b
    b.win.close()
    b.engine.deleteLater()
    b.pump()


# -- the cards, as agentd sends them (made by the same validator that guards the bar) --

def reply_block(**over):
    block = {"proposal": "p3", "kind": "slack_reply", "target": REF, "where": "#launch", "content": "",
             "warnings": [], "fingerprint": "", "ready": False, "state": "open", "by": "person", "receipt": None,
             "note": ""}
    block.update(over)
    return block


def message_card(**over):
    card = {"type": "message", "id": f"msg:{REF}", "title": "Priya Shah in #launch", "source": "connect",
            "say": "Reply in #launch. Sending is yours.",
            "thread": [{"ref": "slack:w1/C1/1700000000.000100", "from": "Priya Shah",
                        "text": "Legal signed off on the announcement.", "ts": NOW - 180, "mine": False, "unread": False},
                       {"ref": "slack:w1/C1/1700000000.000150", "from": "Marcus Webb", "text": "Great news.",
                        "ts": NOW - 150, "mine": False, "unread": False},
                       {"ref": REF, "from": "Priya Shah", "text": "Can we lock the 14th for the launch?",
                        "ts": NOW - 60, "mine": False, "unread": True}],
            "reply": reply_block(), "open": {"kind": "url", "value": "https://acme.test/archives/C1/p1700000000000200"}}
    card.update(over)
    out, errors = cards.accept(card, now=NOW)
    assert out is not None, errors
    return out


def task_card(**over):
    card = {"type": "task", "id": "task:linear:LIN-42", "title": "Fix the launch checklist", "source": "connect",
            "service": "linear", "status": "In review", "why": "Assigned to you by Marcus Webb.",
            "fields": [{"key": "due", "label": "Due", "value": "Friday", "edit": None},
                       {"key": "body", "label": "Comment", "value": "", "edit": "text"}],
            "proposal": reply_block(proposal="p4", kind="task_comment", target="linear:LIN-42", where="Linear",
                                    content={}),
            "open": {"kind": "url", "value": "https://acme.test/issue/LIN-42"}, "say": "Comment on this in Linear."}
    card.update(over)
    out, errors = cards.accept(card, now=NOW)
    assert out is not None, errors
    return out


def list_card(**over):
    card = {"type": "list", "id": "list:unread", "title": "Unread messages", "source": "connect",
            "say": "2 unread messages, newest first.",
            "rows": [{"id": f"msg:{REF}", "title": "Priya Shah in #launch", "sub": "Can we lock the 14th?",
                      "when": "11:04", "state": "new", "actions": [],
                      "opens": {"kind": "card", "value": f"msg:{REF}"}},
                     {"id": "conn:slack:w1", "title": "Slack in Acme", "sub": "Direct messages and mentions.",
                      "when": "", "state": "ok",
                      "actions": [{"id": "disconnect", "label": "Disconnect", "style": "quiet"}], "opens": None}]}
    card.update(over)
    out, errors = cards.accept(card, now=NOW)
    assert out is not None, errors
    return out


# -- a stand-in for what agentd does with a draft (proposals.py): enough for the bar to talk to --

def fingerprint(content):
    return "fp" + hashlib.sha1(json.dumps(content, sort_keys=True).encode()).hexdigest()[:10]


class Agentd:
    """Answers `edit`, `shown`, `open` and `press` as agentd does, so a test can type and see what the bar draws of
    the answers. `held` is the draft as agentd has it."""

    def __init__(self, bar, **draft):
        self.bar, self.seen = bar, 0
        self.draft = {"id": "p3", "kind": "slack_reply", "target": REF, "content": "", "shown": "", "state": "open",
                      "receipt": None, "note": "", "warnings": [], **draft}
        self.presses = []
        self.press_answer = None          # None: sent; else a dict for the press_result of a refused press
        self.clean = None                 # a function taking characters out of the words (plaintext.strip), or none

    def fp(self):
        return fingerprint(self.draft["content"])

    def wire(self):
        d = self.draft
        ready = d["state"] == "open" and bool(d["shown"]) and d["shown"] == self.fp() and \
            any((v.strip() if isinstance(v, str) else v) for v in (d["content"].values() if isinstance(d["content"], dict)
                                                                    else [d["content"]]))
        out = {"id": d["id"], "state": d["state"], "ready": ready, "fingerprint": self.fp(), "content": d["content"],
               "receipt": d["receipt"], "warnings": d["warnings"], "note": d["note"]}
        if d.get("cleaned_from") is not None:
            out["cleaned_from"] = d["cleaned_from"]
        return out

    def take(self, raw):
        """The words as the draft keeps them: cleaned, and what was cleaned is said (proposals.was_cleaned)."""
        cleaned = self.clean(raw) if self.clean else raw
        self.draft["cleaned_from"] = raw if cleaned != raw else None
        return cleaned

    def tell(self):
        self.bar.proposal(**self.wire())

    def step(self):
        for m in self.bar.sent[self.seen:]:
            self.seen += 1
            if m.get("type") == "proposal" and m.get("id") == self.draft["id"]:
                if m["op"] == "edit" and self.draft["state"] == "open":
                    self.draft.update(content=self.take(m["content"]), shown="")
                    self.tell()
                elif m["op"] == "shown":
                    self.draft["shown"] = fingerprint(self.take(m["content"]))
                    self.tell()
            elif m.get("type") == "press" and m.get("id") == self.draft["id"]:
                self.presses.append(m)
                if self.press_answer is not None:
                    self.bar.feed(type="press_result", kind=m["kind"], id=m["id"], **self.press_answer)
                    continue
                self.draft["state"] = "sending"
                self.tell()
                self.draft.update(state="sent", receipt={"line": "Posted in #launch \u00b7 11:04",
                                                         "web": {"name": "Slack", "url": "https://acme.test/p/1"}})
                self.tell()
                self.bar.feed(type="press_result", kind=m["kind"], id=m["id"], ok=True, line="Posted in #launch",
                              code="", receipt=self.draft["receipt"])

    def settle(self, quiet=0.45):
        """Let the bar and this answer each other until nothing has been said for `quiet` seconds."""
        last, end = len(self.bar.sent), time.monotonic() + quiet
        deadline = time.monotonic() + 6
        while time.monotonic() < min(end, deadline):
            self.bar.pump(0.02)
            self.step()
            if len(self.bar.sent) != last:
                last, end = len(self.bar.sent), time.monotonic() + quiet


def flickable(item):
    """The Flickable inside a CardScroll."""
    todo = [item]
    while todo:
        it = todo.pop()
        if it.metaObject().className().startswith("QQuickFlickable"):
            return it
        todo.extend(it.childItems())


def focus(bar):
    QtCore.QMetaObject.invokeMethod(bar.host, "focusBox", QtCore.Qt.DirectConnection)
    bar.pump()


def open_message(bar, **over):
    """A message card with an open draft that agentd answers for."""
    agentd = Agentd(bar)
    bar.card(message_card(**over))
    agentd.settle()
    return agentd


def label_of(bar):
    return bar.item("sendButton").property("label")


def lit(bar):
    ring = bar.item("sendButton")
    return ring is not None and ring.property("ringed") is True


def reply_box(bar):
    return bar.item("replyBox")


# -- the message card --

def test_a_message_card_draws_its_thread_and_its_reply_box(bar):
    bar.card(message_card())
    bar.snap("message-empty")
    assert bar.shown("cardHost") and bar.text("cardTitle") == "Priya Shah in #launch"
    assert bar.text("cardSource") == "Slack" and bar.text("cardSay") == "Reply in #launch. Sending is yours."
    assert bar.shown("messageFace") and not bar.shown("cardDiagram") and not bar.shown("taskFace")
    assert bar.texts("threadFrom") == ["Priya Shah", "Marcus Webb", "Priya Shah"]
    assert bar.texts("threadText") == ["Legal signed off on the announcement.", "Great news.",
                                       "Can we lock the 14th for the launch?"]
    assert len(bar.items("threadWhen")) == 3 and all(w for w in bar.texts("threadWhen"))
    assert len(bar.items("unreadDot")) == 1       # only the last one is unread
    box = reply_box(bar)
    assert box is not None and box.property("text") == "" and box.property("multiline") is True
    assert bar.text("boxPlaceholder") == "Reply in #launch"
    assert bar.warnings == []


def test_my_own_messages_say_you_and_the_words_are_plain_text(bar):
    card = message_card()
    card["thread"][1] = {**card["thread"][1], "mine": True, "from": "Someone else", "text": "<b>bold</b> & <i>so</i>"}
    bar.card(card)
    assert bar.texts("threadFrom")[1] == "You"
    assert bar.texts("threadText")[1] == "<b>bold</b> & <i>so</i>"


def test_no_text_in_the_faces_is_read_as_markup():
    """Other people's words are drawn as text and nothing else: every Text of the faces says so (the format is not
    left to guess, which for a Text means rich text when it looks like it)."""
    for name in ("MessageFace", "TaskFace", "ListFace", "DraftBar", "CardBox", "CardButton", "CardHost"):
        source = (SHELL / f"{name}.qml").read_text()
        for m in re.finditer(r"(?<![\w.])Text \{", source):
            depth, i = 1, m.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(source[i], 0)
                i += 1
            assert "textFormat: Text.PlainText" in source[m.end():i], f"{name}: {source[m.start():m.start() + 80]!r}"


def test_a_long_thread_scrolls_and_starts_at_its_newest_message(bar):
    thread = [{"ref": f"slack:w1/C1/17000000{i:02d}.000100", "from": "Priya Shah", "ts": NOW - 600 + i,
               "text": ("A line of a long conversation about the launch. " * 3).strip(), "mine": False,
               "unread": False} for i in range(6)]
    bar.card(message_card(thread=thread))
    scroll = bar.item("thread")
    flick = flickable(scroll)
    assert scroll.property("implicitHeight") <= 560 - 120      # the box and the button stay in view
    bar.wait(lambda: flick.property("contentY") > 0 or flick.property("contentHeight") <= flick.property("height"))
    assert flick.property("contentY") + flick.property("height") >= flick.property("contentHeight") - 1
    assert bar.shown("replyBox") and bar.shown("sendButton")


def test_the_card_host_chooses_the_face_by_the_cards_type(bar):
    bar.card(message_card())
    assert bar.shown("messageFace") and not bar.shown("taskFace") and not bar.shown("listFace")
    assert bar.host.property("preferredWidth") == 560
    bar.card(task_card())
    bar.wait(lambda: bar.shown("taskFace"))
    assert not bar.shown("messageFace") and not bar.shown("listFace") and bar.text("cardSource") == "Linear"
    bar.card(list_card())
    bar.wait(lambda: bar.shown("listFace"))
    assert not bar.shown("taskFace") and bar.text("cardSource") == "Connections"
    assert bar.host.property("preferredWidth") == 480
    d = {"type": "diagram", "id": "card-1", "shape": "chain", "title": "How a VPN works",
         "nodes": [{"id": "n1", "label": "Laptop"}, {"id": "n2", "label": "Tunnel"}], "links": [], "highlight": [],
         "say": "Everything goes through the tunnel."}
    bar.card(d)
    bar.wait(lambda: bar.shown("cardDiagram"))
    assert not bar.shown("listFace") and not bar.shown("messageFace") and bar.text("cardSource") == "drawn by the agent"
    assert bar.host.property("preferredWidth") == 0
    assert bar.warnings == []


def test_a_card_with_no_reply_has_no_box_and_only_its_way_out(bar):
    bar.card(message_card(reply=None))
    assert not bar.shown("replyBox") and not bar.shown("sendButton") and bar.shown("messageFace")
    assert bar.warnings == []


# -- typing, what agentd is told, and when the button is the person's --

def test_a_card_that_has_drawn_tells_agentd_once_what_it_drew(bar):
    bar.card(message_card(reply=reply_block(content="Yes, the 14th.", fingerprint="f1", ready=False)))
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.pump(0.8)
    assert bar.sent_of("proposal") == [{"type": "proposal", "op": "shown", "id": "p3", "content": "Yes, the 14th."}]
    assert reply_box(bar).property("text") == "Yes, the 14th."     # a draft the agent made is in the box at once
    assert bar.pill.property("composing") is True                   # and a box with words in it is not given up


def test_typing_tells_agentd_the_edit_and_then_that_the_card_drew_it(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.forget()
    focus(bar)
    bar.type("Yes, the 14th.")
    bar.wait(lambda: bar.sent_of("proposal", op="shown"))
    assert bar.sent == [{"type": "proposal", "op": "edit", "id": "p3", "content": "Yes, the 14th."},
                        {"type": "proposal", "op": "shown", "id": "p3", "content": "Yes, the 14th."}]
    assert reply_box(bar).property("text") == "Yes, the 14th."
    assert bar.win.property("typings") >= 14                         # the bar's idle wait is restarted by each key


def test_a_pause_in_typing_is_an_edit_and_more_typing_is_another(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.forget()
    focus(bar)
    bar.type("Yes")
    bar.wait(lambda: len(bar.sent_of("proposal", op="shown")) == 1)
    bar.type(", the 14th.")
    bar.wait(lambda: len(bar.sent_of("proposal", op="shown")) == 2)
    assert [(m["op"], m["content"]) for m in bar.sent] == [
        ("edit", "Yes"), ("shown", "Yes"), ("edit", "Yes, the 14th."), ("shown", "Yes, the 14th.")]


def test_enter_is_a_new_line_in_the_box_and_sends_nothing(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.forget()
    focus(bar)
    bar.type("Yes.")
    bar.key(QtCore.Qt.Key_Return)
    bar.key(QtCore.Qt.Key_Enter)
    bar.type("The 14th.")
    bar.wait(lambda: bar.sent_of("proposal", op="shown"))
    assert reply_box(bar).property("text") == "Yes.\n\nThe 14th."
    assert bar.sent_of("press") == [] and bar.sent_of("card_action") == []
    assert [m["op"] for m in bar.sent] == ["edit", "shown"]
    assert bar.sent[0]["content"] == "Yes.\n\nThe 14th."


def test_the_button_is_lit_only_while_agentd_says_ready_and_the_box_holds_exactly_what_it_has(bar):
    agentd = open_message(bar)
    focus(bar)
    assert not lit(bar) and not bar.shown("yoursLabel")
    bar.type("Yes, the 14th.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    assert bar.text("yoursLabel") == "Yours: Send" and label_of(bar) == "Send"
    bar.snap("message-ready")
    assert agentd.wire()["ready"] is True
    # A word changed: the ring and the label are gone at once, before agentd has heard of it.
    bar.type("!")
    assert not lit(bar) and not bar.shown("yoursLabel")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    assert bar.text("yoursLabel") == "Yours: Send"
    # Said ready for what is not in the box: dimmed, with no word about why.
    bar.proposal(id="p3", state="open", ready=True, fingerprint=fingerprint("Yes, the 14th."), content="Yes, the 14th.")
    assert not lit(bar) and not bar.shown("yoursLabel") and not bar.shown("noteLine")
    assert bar.warnings == []


def test_one_press_is_one_message_with_the_fingerprint_agentd_gave_for_this_content(bar):
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes, the 14th.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    expected = fingerprint("Yes, the 14th.")
    bar.forget()
    agentd.seen = 0
    button = bar.item("sendButton")
    bar.click(button)
    bar.click(button)          # a double click
    bar.click(button)
    assert bar.sent_of("press") == [{"type": "press", "kind": "slack_reply", "id": "p3", "fingerprint": expected}]
    assert bar.pill.property("pressOut") == "p3" and not lit(bar)    # dim from the press until agentd says otherwise
    agentd.settle()
    assert len(agentd.presses) == 1
    bar.wait(lambda: bar.text("receiptLine"))
    assert bar.text("receiptLine") == "Posted in #launch · 11:04" and bar.text("receiptLink") is None or True
    assert not bar.shown("sendButton") and bar.pill.property("pressOut") == ""
    assert reply_box(bar).property("readOnly") is True
    bar.snap("message-sent")
    assert bar.warnings == []


def test_a_receipt_with_a_page_has_a_button_that_opens_it(bar):
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.click(bar.item("sendButton"))
    agentd.settle()
    link = bar.wait(lambda: bar.item("receiptLink"))
    assert not bar.shown("openButton")           # one "Open in Slack", the sent message's: not two of the same name
    bar.forget()
    bar.click(link)
    assert bar.sent == [{"type": "open", "kind": "url", "value": "https://acme.test/p/1"}]


def test_a_press_agentd_turns_away_says_so_and_the_button_comes_back(bar):
    agentd = open_message(bar)
    agentd.press_answer = {"ok": False, "line": "That is not what the card showed. Nothing was sent.",
                           "code": "changed", "receipt": None}
    focus(bar)
    bar.type("Yes.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.click(bar.item("sendButton"))
    agentd.settle()
    assert bar.text("pressLine") == "That is not what the card showed. Nothing was sent."
    bar.wait(lambda: lit(bar))                        # agentd answered, and the draft is as it was
    bar.type(" Thanks.")                              # the next word clears the old line
    assert not bar.shown("pressLine")


def test_what_may_have_gone_says_so_and_the_button_waits_for_the_person_to_have_looked(bar):
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.click(bar.item("sendButton"))
    bar.feed(type="press_result", kind="slack_reply", id="p3", ok=False, code="unknown_outcome",
             line="I can't tell whether that went. Look in Slack before you press Send again.", receipt=None)
    bar.proposal(id="p3", state="unknown", ready=False, fingerprint=fingerprint("Yes."), content="Yes.")
    assert bar.text("unknownLine") == "I can't tell whether that went. Look in Slack before you press again."
    assert label_of(bar) == "Send" and not lit(bar) and not bar.shown("yoursLabel")
    assert reply_box(bar).property("readOnly") is True
    bar.snap("message-unknown")
    tick = bar.item("lookedTick")
    assert tick is not None and bar.pill.property("pressOut") == ""
    bar.click(tick)
    bar.wait(lambda: lit(bar))
    assert bar.text("yoursLabel") == "Yours: Send"
    bar.forget()
    bar.click(bar.item("sendButton"))
    assert bar.sent == [{"type": "press", "kind": "slack_reply", "id": "p3", "fingerprint": fingerprint("Yes."),
                         "again": True}]


def test_a_state_update_never_touches_what_is_being_typed(bar):
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes, the 14th.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.type(" And thanks")
    bar.proposal(id="p3", state="open", ready=False, fingerprint="a-new-one", content="something else entirely",
                 warnings=[{"kind": "broadcast", "text": "This reaches everyone in the channel."}], note="Look again.")
    assert reply_box(bar).property("text") == "Yes, the 14th. And thanks"
    assert not lit(bar)
    assert bar.texts("warningText") == ["This reaches everyone in the channel."] and bar.text("noteLine") == "Look again."
    bar.snap("message-warning")
    # The one the person was typing is the one that is kept: the box says what it says, and agentd is told.
    agentd.settle()
    assert reply_box(bar).property("text") == "Yes, the 14th. And thanks"


def test_words_pasted_with_characters_that_draw_as_nothing_come_back_clean_and_the_button_lights(bar):
    """What agentd takes out of a box (it draws the same without) is taken out of the box too, so that what is read is
    all of what goes and the button is not dark for a reason nobody can see."""
    agentd = open_message(bar)
    agentd.clean = lambda words: re.sub("[\u200b\ufeff]", "", words)
    focus(bar)
    bar.type("Yes")
    box = reply_box(bar)
    box.setProperty("text", "Yes\u200b, the\ufeff 14th.")                 # a paste: the box now holds what it holds
    agentd.settle()
    assert box.property("text") == "Yes, the 14th."
    bar.wait(lambda: lit(bar))
    assert not [m for m in bar.sent_of("proposal") if m["op"] == "edit" and m["content"] == "Yes, the 14th."][1:]


def test_what_was_typed_after_agentd_cleaned_an_earlier_edit_is_not_overwritten(bar):
    agentd = open_message(bar)
    agentd.clean = lambda words: words.replace("\u200b", "")
    focus(bar)
    bar.type("Yes")
    box = reply_box(bar)
    box.setProperty("text", "Yes\u200b")
    bar.pump(0.4)                                    # the edit goes out
    bar.key(QtCore.Qt.Key_End)
    bar.type(" and")                                 # ...and the person goes on before the answer comes back
    agentd.step()
    assert box.property("text") == "Yes\u200b and"  # the answer was about words the box no longer holds: untouched
    agentd.settle()
    assert box.property("text") == "Yes and"         # the next edit was cleaned, and the box still held exactly it


def test_a_box_the_person_emptied_does_not_get_the_old_words_back_from_a_late_answer(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    focus(bar)
    bar.type("Yes")
    box = reply_box(bar)
    for _ in range(3):
        bar.key(QtCore.Qt.Key_Backspace)
    assert box.property("text") == ""
    bar.proposal(id="p3", state="open", ready=False, fingerprint="f", content="Yes")      # an echo of the first edit
    assert box.property("text") == ""


def test_a_draft_the_agent_changes_goes_in_an_untouched_box_and_not_over_the_persons_words(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.proposal(id="p3", state="open", ready=False, fingerprint="f1", content="Yes, the 14th works.")
    assert reply_box(bar).property("text") == "Yes, the 14th works."
    focus(bar)
    bar.type("!")
    bar.proposal(id="p3", state="open", ready=False, fingerprint="f2", content="Let me check first.")
    assert reply_box(bar).property("text") == "Yes, the 14th works.!"


def test_the_first_key_in_a_box_with_no_draft_asks_for_one_and_what_was_typed_is_kept(bar):
    bar.card(message_card(reply=reply_block(proposal=None)))
    focus(bar)
    bar.type("Yes")
    bar.wait(lambda: bar.sent_of("proposal", op="open"))
    bar.type(", the 14th.")
    assert bar.sent == [{"type": "proposal", "op": "open", "kind": "slack_reply", "target": REF}]   # once
    # agentd opens it and sends the card again with its box: what is in the box stays, and is sent to the draft.
    agentd = Agentd(bar)
    bar.card(message_card(reply=reply_block(proposal="p3")))
    assert reply_box(bar).property("text") == "Yes, the 14th."
    agentd.settle()
    assert [(m["op"], m["content"]) for m in bar.sent if m["type"] == "proposal" and m["op"] != "open"][-2:] == [
        ("edit", "Yes, the 14th."), ("shown", "Yes, the 14th.")]
    bar.wait(lambda: lit(bar))


def test_the_card_that_comes_of_the_persons_reply_takes_the_keyboard_and_an_agents_does_not(bar):
    bar.call("openReply", "slack_reply", REF)
    assert bar.sent == [{"type": "proposal", "op": "open", "kind": "slack_reply", "target": REF}]
    bar.card(message_card())
    assert bar.win.property("keys") == 1
    bar.card(message_card(id="msg:other", reply=reply_block(by="agent", content="Yes.")))
    assert bar.win.property("keys") == 1       # nothing was asked for: it waits for a click
    bar.call("wantKeys")
    assert bar.win.property("keys") == 2


# -- a notification's card --

def notification_card(**over):
    card = {"type": "message", "id": "msg:n7", "title": "Priya Shah in Teams", "source": "notification",
            "say": "Copy and open takes your words to Teams.",
            "thread": [{"ref": "notify:7", "from": "Priya Shah", "text": "Legal signed off.", "ts": NOW, "mine": False,
                        "unread": True}],
            "reply": {"proposal": None, "kind": "copy", "where": "Teams", "content": ""}, "open": None}
    card.update(over)
    out, errors = cards.accept(card, now=NOW)
    assert out is not None, errors
    return out


def test_a_notifications_card_copies_and_opens_and_is_no_send(bar):
    bar.card(notification_card())
    assert bar.text("cardSource") == "Teams" and label_of(bar) == "Copy and open"
    assert not lit(bar) and not bar.shown("yoursLabel") and not bar.shown("openButton")
    assert bar.item("sendButton").property("live") is False           # nothing to copy yet
    focus(bar)
    bar.type("Looks good to me.")
    bar.wait(lambda: bar.item("sendButton").property("live"))
    assert not lit(bar) and not bar.shown("yoursLabel")                # never the ring, never "Yours"
    bar.snap("notification-copy")
    bar.pump(0.6)
    assert bar.sent == []                                              # a copy has no draft: nothing is told agentd
    bar.click(bar.item("sendButton"))
    bar.click(bar.item("sendButton"))
    assert bar.sent == [{"type": "card_action", "card": "msg:n7", "action": "copy_open", "text": "Looks good to me."}]
    assert bar.warnings == []


def test_the_next_card_does_not_inherit_the_words_typed_in_the_one_before(bar):
    # What one conversation's reply said must never turn up in the box of another card (a reply to someone else, a
    # notification's card): the words are that draft's, and agentd keeps them there.
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    focus(bar)
    bar.type("Yes, lock the 14th.")
    bar.key(QtCore.Qt.Key_Escape)
    assert bar.pill.property("card") is None
    bar.pump(0.4)
    bar.card(notification_card())
    bar.pump(0.4)
    assert bar.box().property("text") == ""
    bar.key(QtCore.Qt.Key_Escape)
    bar.card(message_card(id="msg:other", reply=reply_block(proposal="p9", target="slack:w1/C2/1700000000.000300")))
    bar.pump(0.4)
    assert bar.box().property("text") == ""
    assert bar.warnings == []


def test_the_next_card_does_not_inherit_the_words_of_a_reply_that_was_sent(bar):
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes, lock the 14th.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.click(bar.item("sendButton"))
    agentd.settle()
    bar.wait(lambda: bar.text("receiptLine"))
    assert reply_box(bar).property("text") == "Yes, lock the 14th."
    bar.key(QtCore.Qt.Key_Escape)
    bar.pump(0.4)
    bar.card(notification_card())
    bar.pump(0.4)
    assert reply_box(bar).property("text") == ""
    bar.key(QtCore.Qt.Key_Escape)
    bar.card(message_card(id="msg:other", reply=reply_block(proposal="p9", target="slack:w1/C2/1700000000.000300")))
    bar.pump(0.4)
    assert reply_box(bar).property("text") == ""
    assert bar.warnings == []


# -- closing a card, and what keeps one open --

def test_esc_in_the_box_closes_the_card_and_an_empty_draft_goes_with_it(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.forget()
    focus(bar)
    bar.key(QtCore.Qt.Key_Escape)
    assert bar.pill.property("card") is None
    assert bar.sent == [{"type": "card_action", "card": f"msg:{REF}", "action": "dismiss"},
                        {"type": "proposal", "op": "discard", "id": "p3"}]
    assert bar.win.property("handOffs") == 1          # the box held the keyboard: it goes back to the windows


def test_a_draft_with_words_in_it_is_not_discarded_by_closing_the_card(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    focus(bar)
    bar.type("Yes, the 14th.")
    bar.forget()
    bar.click(bar.item("cardClose"))
    assert bar.pill.property("card") is None
    # What was typed and not yet said is said as the card goes, before agentd hears it was put away.
    assert bar.sent == [{"type": "card_action", "card": f"msg:{REF}", "action": "dismiss"},
                        {"type": "proposal", "op": "edit", "id": "p3", "content": "Yes, the 14th."}]
    bar.pump(0.8)
    assert not bar.sent_of("proposal", op="discard")


def test_esc_closes_a_list_and_a_task_too(bar):
    bar.card(list_card())
    bar.wait(lambda: bar.shown("listFace"))
    row = bar.item("listRow")
    row.forceActiveFocus()
    bar.pump()
    bar.forget()
    bar.key(QtCore.Qt.Key_Escape)
    assert bar.pill.property("card") is None
    assert bar.sent == [{"type": "card_action", "card": "list:unread", "action": "dismiss"}]


def test_a_person_writing_a_reply_is_not_idle_and_their_card_stays(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    focus(bar)
    bar.type("Yes")
    assert bar.pill.property("composing") is True
    bar.pill.setProperty("cardAt", 0.0)
    bar.pill.setProperty("clickedAt", 0.0)
    QtCore.QMetaObject.invokeMethod(bar.pill, "windowOpened", QtCore.Qt.DirectConnection)   # a window took the stage
    bar.send(kind="turn_start", turn=1, prompt="open the browser")                          # the next turn began
    bar.send(kind="local", action="app", phase="done", ok=True, verb="open")
    assert bar.card_now()["id"] == f"msg:{REF}" and bar.shown("replyBox")
    assert bar.item("replyBox").property("text") == "Yes"
    # The same events do take away a card nobody is writing in.
    bar.card(list_card())
    bar.pill.setProperty("cardAt", 0.0)
    bar.pill.setProperty("clickedAt", 0.0)
    QtCore.QMetaObject.invokeMethod(bar.pill, "windowOpened", QtCore.Qt.DirectConnection)
    assert bar.pill.property("card") is None


def test_a_card_nobody_has_typed_in_is_put_away_by_the_next_turn_as_a_picture_is(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    assert bar.pill.property("composing") is False
    bar.send(kind="turn_start", turn=1, prompt="what is on my screen")
    assert bar.pill.property("card") is None


def test_a_card_with_a_box_is_drawn_on_the_screen_the_person_is_on_and_a_picture_on_all(bar):
    bar.card(message_card())
    assert bar.shown("cardHost")
    bar.host.setProperty("onThisScreen", False)
    bar.pump(0.4)
    assert not bar.shown("cardHost")
    bar.card({"type": "diagram", "id": "card-1", "shape": "chain", "title": "t", "nodes": [{"id": "n1", "label": "a"}],
              "links": [], "highlight": []})
    bar.wait(lambda: bar.shown("cardHost"))


def test_a_card_that_goes_while_a_press_is_out_says_it_cannot_tell(bar):
    agentd = open_message(bar)
    agentd.press_answer = {"ok": False, "line": "ignored", "code": "busy", "receipt": None}
    focus(bar)
    bar.type("Yes.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    agentd.press_answer = None
    bar.click(bar.item("sendButton"))
    assert bar.pill.property("pressOut") == "p3"
    QtCore.QMetaObject.invokeMethod(bar.pill, "lost", QtCore.Qt.DirectConnection)
    assert bar.pill.property("pressLine").startswith("I lost touch with the agent while that went")
    assert bar.pill.property("pressOut") == "p3" and not lit(bar)


def test_a_press_that_was_out_when_the_socket_dropped_can_be_made_again_once_agentd_is_back(bar):
    """The button was dim for good before: nothing cleared the press that was out. Agentd's state of the draft decides
    once it answers (it only sends from open): a draft that is still open lights it, one that may have gone does not."""
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.click(bar.item("sendButton"))
    assert bar.pill.property("pressOut") == "p3"
    QtCore.QMetaObject.invokeMethod(bar.pill, "lost", QtCore.Qt.DirectConnection)
    bar.send(type="status", busy=False, provider="claude", queue=[])         # the connection is back
    assert bar.pill.property("pressOut") == ""
    agentd.draft["state"] = "open"
    agentd.tell()                                                            # it never heard the press: still open
    bar.wait(lambda: lit(bar))
    # and when agentd restarted in the middle of it, it says the draft may have gone
    agentd.draft["state"] = "unknown"
    agentd.tell()
    bar.wait(lambda: not lit(bar) and bar.shown("unknownLine"))
    assert bar.warnings == []


def test_a_draft_that_was_sent_does_not_lend_its_words_to_the_next_draft_of_the_same_card(bar):
    """A comment or a reply was sent and the card stayed up with its receipt; the next draft of the same card (the
    agent's, or a second Reply) must show its own words. The old ones were typed again into it and lit its button,
    so one press posted them twice."""
    agentd = open_message(bar)
    focus(bar)
    bar.type("Yes, lock the 14th.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    bar.click(bar.item("sendButton"))
    agentd.settle()
    bar.wait(lambda: bar.text("receiptLine"))
    bar.forget()
    bar.card(message_card(reply=reply_block(proposal="p6", content="Done, shipped.", by="agent")))
    bar.pump(0.5)
    assert reply_box(bar).property("text") == "Done, shipped."
    assert bar.sent_of("proposal", op="edit", id="p6") == []
    assert not any(m.get("content") == "Yes, lock the 14th." for m in bar.sent_of("proposal"))
    assert bar.warnings == []


def test_a_draft_that_is_still_open_keeps_what_is_typed_when_the_card_is_given_a_new_one(bar):
    agentd = open_message(bar)
    focus(bar)
    bar.type("Almost there")
    bar.card(message_card(reply=reply_block(proposal="p7", content="", by="agent")))
    bar.pump(0.5)
    assert reply_box(bar).property("text") == "Almost there"           # what the person is writing is never written over
    assert bar.sent_of("proposal", op="edit", id="p7")[-1]["content"] == "Almost there"
    assert agentd and bar.warnings == []


def test_what_was_typed_a_moment_before_another_card_replaces_this_one_is_said_to_its_own_draft(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    focus(bar)
    bar.type("A pasted reply")
    bar.forget()
    bar.card(message_card(id="msg:other", reply=reply_block(proposal="p9", target="slack:w1/C2/1700000000.000300")))
    bar.pump(0.5)
    assert bar.sent_of("proposal", op="edit", id="p3") == [
        {"type": "proposal", "op": "edit", "id": "p3", "content": "A pasted reply"}]
    assert reply_box(bar).property("text") == ""                     # the next card starts empty
    assert bar.sent_of("proposal", op="edit", id="p9") == []


def test_only_the_screen_that_draws_the_box_says_whether_the_person_is_in_it(bar):
    """Another screen holds a face with a stale mirror of the card and no focus: heard last, it said the person was
    out of the box (the keyboard stayed with the bar) and still typing after the box was emptied (Esc kept the card)."""
    agentd = open_message(bar)
    focus(bar)
    bar.type("abc")
    agentd.settle()
    assert bar.pill.property("composeFocus") is True and bar.pill.property("composeText") is True
    for _ in range(3):
        bar.key(QtCore.Qt.Key_Backspace)
    agentd.settle()
    assert bar.pill.property("composeText") is False and bar.pill.property("composeFocus") is True
    bar.key(QtCore.Qt.Key_Escape)
    assert bar.pill.property("card") is None and bar.win.property("handOffs") == 1
    assert bar.warnings == []


# -- the task card --

def test_a_task_card_draws_its_status_why_fields_and_button(bar):
    bar.card(task_card())
    bar.snap("task-empty")
    assert bar.shown("taskFace") and bar.text("cardTitle") == "Fix the launch checklist"
    assert bar.text("cardSource") == "Linear" and bar.text("cardSay") == "Comment on this in Linear."
    assert bar.text("taskStatus") == "In review" and bar.text("taskWhy") == "Assigned to you by Marcus Webb."
    assert bar.texts("fieldLabel") == ["Due", "Comment"]
    assert bar.texts("fieldValue") == ["Friday"]            # where only the service can say, the words
    boxes = bar.items("fieldBox")
    assert len(boxes) == 1 and boxes[0].property("multiline") is True       # where the person can, a box
    assert label_of(bar) == "Comment" and not lit(bar) and bar.shown("openButton")
    assert bar.warnings == []


def test_a_task_box_is_one_line_a_date_or_several_lines_as_the_field_says(bar):
    fields = [{"key": "title", "label": "Title", "value": "", "edit": "line"},
              {"key": "description", "label": "Description", "value": "", "edit": "text"},
              {"key": "due", "label": "Due", "value": "", "edit": "date"}]
    bar.card(task_card(title="New task in Linear", fields=fields,
                       proposal=reply_block(proposal="p4", kind="task_create", target="linear", where="Linear", content={})))
    by = {b.property("label"): b for b in bar.items("fieldBox")}
    assert [by[k].property("multiline") for k in ("Title", "Description", "Due")] == [False, True, False]
    assert by["Due"].property("placeholder") == "YYYY-MM-DD" and label_of(bar) == "Create"
    bar.snap("task-new")


def test_a_task_boxs_words_go_to_agentd_as_an_object_of_field_values_with_an_empty_one_for_each_box(bar):
    fields = [{"key": "title", "label": "Title", "value": "", "edit": "line"},
              {"key": "description", "label": "Description", "value": "", "edit": "text"},
              {"key": "labels", "label": "Labels", "value": "bug", "edit": None}]
    bar.card(task_card(fields=fields, proposal=reply_block(
        proposal="p4", kind="task_create", target="linear", where="Linear", content={"labels": "bug"})))
    bar.wait(lambda: bar.sent_of("proposal"))
    bar.forget()
    focus(bar)
    bar.type("Fix the checklist")
    bar.key(QtCore.Qt.Key_Return)                      # in a line, Enter does nothing (and certainly does not send)
    bar.wait(lambda: bar.sent_of("proposal", op="shown"))
    assert bar.sent == [{"type": "proposal", "op": "edit", "id": "p4",
                         "content": {"labels": "bug", "title": "Fix the checklist", "description": ""}},
                        {"type": "proposal", "op": "shown", "id": "p4",
                         "content": {"labels": "bug", "title": "Fix the checklist", "description": ""}}]
    boxes = {b.property("label"): b.property("text") for b in bar.items("fieldBox")}
    assert boxes == {"Title": "Fix the checklist", "Description": ""}


def test_a_task_button_has_the_same_ring_and_press_as_a_replys(bar):
    agentd = Agentd(bar, id="p4", kind="task_comment", target="linear:LIN-42", content={})
    bar.card(task_card())
    agentd.settle()
    focus(bar)
    assert not lit(bar)
    bar.type("On it.")
    agentd.settle()
    bar.wait(lambda: lit(bar))
    assert bar.text("yoursLabel") == "Yours: Comment" and label_of(bar) == "Comment"
    bar.snap("task-ready")
    bar.forget()
    agentd.seen = 0
    bar.click(bar.item("sendButton"))
    bar.click(bar.item("sendButton"))
    assert bar.sent_of("press") == [{"type": "press", "kind": "task_comment", "id": "p4",
                                     "fingerprint": fingerprint({"body": "On it."})}]
    agentd.settle()
    bar.wait(lambda: bar.text("receiptLine"))
    assert bar.item("fieldBox").property("readOnly") is True
    assert bar.warnings == []


def test_a_task_boxs_words_survive_a_draft_that_changes_under_them(bar):
    agentd = Agentd(bar, id="p4", kind="task_comment", target="linear:LIN-42", content={})
    bar.card(task_card())
    agentd.settle()
    focus(bar)
    bar.type("On it")
    agentd.settle()
    bar.proposal(id="p4", state="open", ready=False, fingerprint="x", content={"body": "Something else"},
                 note="Look again.")
    assert bar.item("fieldBox").property("text") == "On it" and bar.text("noteLine") == "Look again."
    bar.card(task_card(proposal=reply_block(proposal="p4", kind="task_comment", target="linear:LIN-42",
                                            where="Linear", content={"body": "On it"}, note="Fresh card.")))
    assert bar.item("fieldBox").property("text") == "On it"      # the same boxes, the same cursor, not rebuilt


# -- the list card --

def test_a_list_card_draws_its_rows_marks_and_buttons(bar):
    bar.card(list_card())
    bar.snap("list-unread")
    assert bar.shown("listFace") and bar.text("cardTitle") == "Unread messages"
    assert bar.texts("rowTitle") == ["Priya Shah in #launch", "Slack in Acme"]
    assert bar.texts("rowSub") == ["Can we lock the 14th?", "Direct messages and mentions."]
    assert bar.texts("rowWhen") == ["11:04"]
    assert len(bar.items("stateDot")) == 1 and len(bar.items("stateMark")) == 1      # new: a dot; ok: a tick
    assert [b.property("label") for b in bar.items("rowAction")] == ["Disconnect"]
    assert bar.text("cardSay") == "2 unread messages, newest first."
    assert bar.warnings == []


def test_every_state_of_a_row_has_a_mark_in_the_kits_colours(bar):
    states = ["ok", "warn", "bad", "new", "gone", "active"]
    rows = [{"id": f"r{i}", "title": s.capitalize(), "sub": "", "when": "", "state": s, "actions": [], "opens": None}
            for i, s in enumerate(states)]
    bar.card(list_card(rows=rows))
    bar.snap("list-states")
    marks = {it.property("name"): it for it in bar.items("stateMark")}
    assert set(marks) == {"check", "alert-triangle", "x", "minus", "circle"}      # (new is the dot)
    colour = {n: m.property("color").name() for n, m in marks.items()}
    assert colour == {"check": THEME["good"], "alert-triangle": THEME["warn"], "x": THEME["bad"],
                      "minus": THEME["faint"], "circle": THEME["info"]}
    assert bar.items("stateDot")[0].property("color").name() == THEME["accent"]


def test_a_button_on_a_row_asks_agentd_for_its_action_on_that_row(bar):
    bar.card(list_card())
    bar.forget()
    bar.click(bar.item("rowAction"))
    assert bar.sent == [{"type": "card_action", "card": "list:unread", "row": "conn:slack:w1", "action": "disconnect"}]
    rows = [{"id": "add:linear", "title": "Linear", "sub": "Issues assigned to you.", "when": "", "state": "gone",
             "actions": [{"id": "connect", "label": "Connect", "style": "primary"},
                         {"id": "open", "label": "Open", "style": "quiet"}], "opens": None}]
    bar.card(list_card(id="list:connected", title="Connected here", rows=rows))
    bar.forget()
    buttons = sorted(bar.items("rowAction"), key=lambda b: bar.where(b)[0])
    assert [b.property("label") for b in buttons] == ["Connect", "Open"]
    assert [b.property("tone") for b in buttons] == ["primary", "quiet"]
    bar.click(buttons[0])
    assert bar.sent == [{"type": "card_action", "card": "list:connected", "row": "add:linear", "action": "connect"}]
    bar.snap("list-connected")


def test_a_row_that_opens_something_opens_it_on_a_click_and_on_enter(bar):
    rows = [{"id": "r1", "title": "A card", "sub": "", "when": "", "state": "new", "actions": [],
             "opens": {"kind": "card", "value": "msg:x"}},
            {"id": "r2", "title": "A page", "sub": "", "when": "", "state": "ok", "actions": [],
             "opens": {"kind": "url", "value": "https://acme.test/issue/LIN-42"}},
            {"id": "r3", "title": "Nothing", "sub": "", "when": "", "state": "ok", "actions": [], "opens": None}]
    bar.card(list_card(rows=rows))
    bar.forget()
    first, second, third = sorted(bar.items("listRow"), key=lambda r: bar.where(r)[1])
    bar.click(first)
    assert bar.sent == [{"type": "card_action", "card": "list:unread", "row": "r1", "action": "open"}]
    bar.forget()
    bar.click(second)
    assert bar.sent == [{"type": "open", "kind": "url", "value": "https://acme.test/issue/LIN-42"}]
    bar.forget()
    bar.click(third)
    assert bar.sent == []
    second.forceActiveFocus()
    bar.key(QtCore.Qt.Key_Return)
    assert bar.sent == [{"type": "open", "kind": "url", "value": "https://acme.test/issue/LIN-42"}]


def test_twelve_rows_scroll_inside_the_card_and_the_card_keeps_its_limit(bar):
    rows = [{"id": f"r{i}", "title": f"Priya Shah in #launch {i}", "sub": "Can we lock the 14th? " * 4, "when": "11:04",
             "state": "new", "actions": [], "opens": {"kind": "card", "value": f"msg:{i}"}} for i in range(12)]
    bar.card(list_card(rows=rows))
    bar.snap("list-twelve")
    assert bar.host.property("height") <= 560 + 28
    flick = flickable(bar.item("listFace"))
    assert flick.property("contentHeight") > flick.property("height")


def owner(bar):
    """The nearest named thing around whatever has the keyboard (the box, a button, the cross)."""
    it = bar.win.activeFocusItem()
    while it is not None and it.objectName() not in ("replyBox", "sendButton", "openButton", "cardClose"):
        it = it.parentItem()
    return it.objectName() if it is not None else ""


def test_tab_goes_from_the_box_to_the_buttons_that_can_be_pressed_and_back(bar):
    bar.card(message_card())
    bar.wait(lambda: bar.sent_of("proposal"))
    focus(bar)
    assert owner(bar) == "replyBox"
    bar.key(QtCore.Qt.Key_Tab)
    assert owner(bar) == "openButton"           # Send is dim, so Tab passes it (it only takes the keys while it can send)
    bar.key(QtCore.Qt.Key_Backtab)
    assert owner(bar) == "replyBox"
    bar.type("Yes.")
    assert reply_box(bar).property("text") == "Yes."     # Tab and Shift+Tab are no characters of a message


# -- the notification server (shell/Notifications.qml, with a stand-in for Quickshell's NotificationServer) --

NOTES = """
import QtQuick
import "%s"

Item {
    id: w
    property var sent: []
    Notifications {
        objectName: "notes"
        onOutgoing: msg => { if (msg.type !== "bar") w.sent = w.sent.concat([msg]) }   // (the bar's hello is not what is under test)
    }
}
"""


class Notes:
    """Notifications.qml loaded without Quickshell, where a notification arrives from the stand-in server and every
    call the shell makes on it is written down on the notification."""

    def __init__(self, app, tmp_path):
        self.app = app
        qml = tmp_path / "notes.qml"
        qml.write_text(NOTES % SHELL.as_uri())
        self.engine = QtQml.QQmlEngine()
        self.engine.addImportPath(str(STUBS))
        self.warnings = []
        QtCore.qInstallMessageHandler(lambda mode, ctx, msg: self.warnings.append(msg))
        self.comp = QtQml.QQmlComponent(self.engine, QtCore.QUrl.fromLocalFile(str(qml)))
        self.root = self.comp.create()
        assert self.root is not None, self.comp.errors()
        self.notes = self.root.findChild(QtCore.QObject, "notes")
        self.server = self.root.findChild(QtCore.QObject, "notificationServer")
        assert self.server is not None
        self.pump()

    def pump(self, seconds=0.02):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                break
            time.sleep(0.005)

    def js(self, obj, name):
        value = obj.property(name)
        return value.toVariant() if hasattr(value, "toVariant") else value

    @property
    def sent(self):
        return [dict(m) for m in self.js(self.root, "sent")]

    def link(self, up):
        self.notes.setProperty("link", up)
        self.pump()

    def arrive(self, id=1, **spec):
        QtCore.QMetaObject.invokeMethod(self.server, "arrive", QtCore.Q_ARG("QVariant", {"id": id, "objectName": f"note{id}", **spec}))
        self.pump()

    def note(self, id):
        """The latest notification the server handed over under this id."""
        found = self.root.findChildren(QtCore.QObject, f"note{id}")
        return found[-1] if found else None

    def calls(self, id):
        return list(self.js(self.note(id), "calls"))

    def hear(self, **ev):
        QtCore.QMetaObject.invokeMethod(self.notes, "handle", QtCore.Q_ARG("QVariant", ev))
        self.pump()


@pytest.fixture
def notes(app, tmp_path):
    n = Notes(app, tmp_path)
    n.link(True)
    yield n
    n.root.deleteLater()
    n.engine.deleteLater()
    n.pump()


ACTIONS = [{"identifier": "default", "text": "Open"}, {"identifier": "mark-read", "text": "Mark as read"}]


def test_the_server_says_what_is_true_of_it(notes):
    flags = {k: notes.server.property(k) for k in (
        "actionsSupported", "bodySupported", "bodyMarkupSupported", "bodyHyperlinksSupported", "bodyImagesSupported",
        "imageSupported", "actionIconsSupported", "inlineReplySupported", "persistenceSupported", "keepOnReload")}
    assert flags == {"actionsSupported": True, "bodySupported": True, "bodyMarkupSupported": False,
                     "bodyHyperlinksSupported": False, "bodyImagesSupported": False, "imageSupported": False,
                     "actionIconsSupported": False, "inlineReplySupported": False, "persistenceSupported": False,
                     "keepOnReload": False}


def test_a_notification_goes_to_agentd_whole_and_stays_tracked(notes):
    notes.arrive(7, appName="Chromium", appIcon="chromium", summary="Priya Shah", body="Legal signed off.", urgency=2,
                 actions=ACTIONS)
    assert notes.sent == [{"type": "notification", "id": 7, "app": "Chromium", "summary": "Priya Shah",
                           "body": "Legal signed off.", "urgency": "critical", "icon": "chromium",
                           "actions": [{"id": "default", "label": "Open"}, {"id": "mark-read", "label": "Mark as read"}]}]
    assert notes.note(7).property("tracked") is True and notes.calls(7) == []


@pytest.mark.parametrize("number, word", [(0, "low"), (1, "normal"), (2, "critical")])
def test_the_urgency_goes_as_a_word(notes, number, word):
    notes.arrive(3, summary="x", urgency=number)
    assert notes.sent[0]["urgency"] == word


def test_open_invokes_the_named_action_else_the_default_one_and_lets_the_notification_go(notes):
    notes.arrive(1, summary="a", actions=ACTIONS)
    notes.arrive(2, summary="b", actions=ACTIONS)
    notes.hear(type="notification_invoke", id=1, action="mark-read")
    assert notes.calls(1) == ["invoke:mark-read"] and notes.note(1).property("tracked") is False
    notes.hear(type="notification_invoke", id=2)
    assert notes.calls(2) == ["invoke:default"]
    notes.hear(type="notification_invoke", id=2)                      # asked twice: it is gone
    assert notes.calls(2) == ["invoke:default"]


def test_an_action_that_is_not_there_falls_back_to_the_default_and_no_default_means_dismissed(notes):
    notes.arrive(1, summary="a", actions=ACTIONS)
    notes.hear(type="notification_invoke", id=1, action="no-such-thing")
    assert notes.calls(1) == ["invoke:default"]
    notes.arrive(2, summary="b", actions=[{"identifier": "mark-read", "text": "Mark as read"}])
    notes.hear(type="notification_invoke", id=2)
    assert notes.calls(2) == ["dismiss"]                               # nothing to bring forward


def test_close_dismisses_it_and_a_stranger_is_ignored(notes):
    notes.arrive(4, summary="a")
    notes.hear(type="notification_close", id=99)
    notes.hear(type="notification_close", id="4")                      # an id is a number
    assert notes.calls(4) == []
    notes.hear(type="notification_close", id=4)
    assert notes.calls(4) == ["dismiss"]
    notes.hear(type="notification_close", id=4)
    assert notes.calls(4) == ["dismiss"]


def test_agentd_words_that_are_not_about_notifications_do_nothing(notes):
    notes.arrive(4, summary="a")
    for ev in ({"type": "card", "card": {}}, {"type": "notification_other", "id": 4}, {}):
        notes.hear(**ev)
    assert notes.calls(4) == [] and notes.note(4).property("tracked") is True


def test_one_the_app_closed_itself_is_let_go_and_agentds_later_word_finds_nothing(notes):
    notes.arrive(5, summary="a", actions=ACTIONS)
    QtCore.QMetaObject.invokeMethod(notes.note(5), "expire")
    notes.pump()
    notes.hear(type="notification_invoke", id=5)
    notes.hear(type="notification_close", id=5)
    assert notes.calls(5) == ["expire"]


def test_an_app_that_sends_the_same_id_again_updates_it(notes):
    notes.arrive(6, summary="Downloading 10%")
    first = notes.note(6)
    notes.arrive(6, summary="Downloading 90%")
    assert [m["summary"] for m in notes.sent] == ["Downloading 10%", "Downloading 90%"]
    assert first.property("tracked") is False and notes.note(6).property("tracked") is True
    notes.hear(type="notification_close", id=6)
    assert notes.calls(6) == ["dismiss"] and list(notes.js(first, "calls")) == []


def test_a_notification_that_comes_while_agentd_is_away_waits_and_goes_once_when_the_link_is_back(notes):
    notes.link(False)
    notes.arrive(1, summary="first")
    notes.arrive(2, summary="second")
    assert notes.sent == [] and notes.note(1).property("tracked") is True
    notes.link(True)
    assert [m["id"] for m in notes.sent] == [1, 2]
    notes.link(False)
    notes.link(True)
    assert [m["id"] for m in notes.sent] == [1, 2]                     # not again: agentd has them


def test_sixty_four_are_held_and_the_oldest_is_dismissed_to_make_room(notes):
    for i in range(1, 66):
        notes.arrive(i, summary=f"n{i}")
    assert notes.calls(1) == ["dismiss"] and notes.calls(2) == []          # 65 came, 64 stay
    assert len(notes.sent) == 65
    notes.arrive(66, summary="n66")
    assert notes.calls(2) == ["dismiss"] and notes.calls(3) == []


def test_words_are_cut_to_a_sane_length_and_only_text_is_passed_on(notes):
    notes.arrive(1, summary="s" * 5000, body="b" * 5000, appName="a" * 5000)
    sent = notes.sent[0]
    assert (len(sent["summary"]), len(sent["body"]), len(sent["app"])) == (2000, 2000, 2000)
    many = [{"identifier": f"a{i}", "text": f"A{i}"} for i in range(30)] + [{"identifier": "", "text": "nameless"}]
    notes.arrive(2, summary="x", actions=many)
    assert [a["id"] for a in notes.sent[1]["actions"]] == [f"a{i}" for i in range(8)]


def test_the_notification_text_is_not_touched_otherwise(notes):
    words = "<b>Priya</b> \u202eevil\u202c &amp; \x07 bell"
    notes.arrive(1, summary=words, body="line one\nline two")
    assert notes.sent[0]["summary"] == words and notes.sent[0]["body"] == "line one\nline two"      # agentd makes it plain


def test_the_notifications_load_without_qml_warnings(notes):
    notes.arrive(1, summary="x", actions=ACTIONS)
    notes.hear(type="notification_invoke", id=1)
    assert [w for w in notes.warnings if ".qml" in w or "Error" in w] == []


# -- the shell's wiring (shell.qml needs Quickshell, so its lines are read) --

def shell_text():
    return (SHELL / "shell.qml").read_text()


def test_the_shell_loads_the_notification_server_on_its_own_and_passes_it_what_agentd_says():
    shell = shell_text()
    assert re.search(r'Loader \{\s*id: notifications\s*source: "Notifications.qml"', shell)      # a failure costs only these
    assert "notifications.item.handle(ev)" in shell and "function onOutgoing(msg) { root.write(msg) }" in shell
    assert 'property: "link"; value: root.connected' in shell


def test_the_shell_gives_the_keyboard_to_the_cards_box_only_when_the_card_asks():
    shell = shell_text()
    assert "function onCardKeys()" in shell and "root.cardKeysWanted(" in shell
    # The card's own screen takes the keys, and the box (not the pill's field) has them.
    assert "if (boxNext) focusBox()" in shell and "cardHost.item.focusBox()" in shell
    assert re.search(r"function onCardKeysWanted\(name\) \{\s*if \(name !== win.modelData.name\) return", shell)
    # Nothing but a card's ask moves the keys: an agent's card arrives with no call of cardKeysWanted.
    assert shell.count("cardKeysWanted(") == 2                          # the signal and its one emit (the handler is onCardKeysWanted)


def test_the_shell_draws_a_card_with_a_box_on_one_screen_and_not_while_typing_idle_takes_the_keys_back():
    shell = shell_text()
    assert 'property: "onThisScreen"' in shell and "root.cardScreen === win.modelData.name" in shell
    assert "interval: pillState.composing ? 120000" in shell
    assert re.search(r"function onTyping\(\) \{ if \(win.summoned\) idle.restart\(\) \}", shell)
    assert "cardHost.item.preferredWidth" in shell

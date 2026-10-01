"""The Mail window (share/apps/mail/main.qml) as the person sees it: what it says in each state, what its keys and
buttons do, and the one thing it must never do, which is send without the person's own press on Send.

Each scenario runs in a child process (offscreen, software renderer): a QGuiApplication and the `App` singleton
exist once per process. The child runs the runtime's own `Host` on the real app folder, with the real backend, and a
mail service on the fake engine and a fake agentd in threads of its own (tests/mail_app_lab.py), all in a temp home;
it drives the window with mouse and key events (QTest), reads what is drawn from the item tree, and prints what it
found as JSON for the test to judge. With BOMBADIL_SCREENS=<dir> the screenshot scenario also saves its pictures
there (sample mail only: the made-up mailbox of BOMBADIL_MAIL_SAMPLE, no service).

What is held to what: every state says its one sentence; a stranger's markup is text; Send follows the backend's
gate and is the only thing that presses; a typed Return never sends; Esc leaves the reply box before it closes the
window; the keys act on the mail that is open; the window gets by in 900 px and below; and the orange of the
window is the ring on Send and nothing else, in the item tree, in the files and in the pixels.
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
APP = ROOT / "share" / "apps" / "mail"
QML_FILES = sorted(APP.glob("*.qml"))

# Runs in the child: starts the lab and the window, and has the helpers every scenario uses.
PRELUDE = r'''
import json, os, sys, time
from pathlib import Path
from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from bombadil.appkit import context, engine, runtime
from mail_app_lab import (HOSTILE, INVOICE, LAUNCH, LEO, NEWS, PHOTOS, SAM, Lab, scratch_env, spin, wait_until)

W, H = int(os.environ["MAILT_W"]), int(os.environ["MAILT_H"])
SAMPLE = os.environ.get("MAILT_SAMPLE")        # None: a lab; else BOMBADIL_MAIL_SAMPLE ("" is the list)
home = Path(os.environ["HOME"])
out = {}
lb = None
if SAMPLE is None:
    scratch_env(home, os.environ.__setitem__)
    lb = Lab(home, samples=os.environ.get("MAILT_SAMPLES", "1") == "1").start()
    if os.environ.get("MAILT_SERVICE") == "down":
        lb.stop_service()
else:
    os.environ["BOMBADIL_MAIL_SAMPLE"] = SAMPLE
ctx = context.for_app("mail", check=SAMPLE is not None)
qt = engine.make_app(ctx)
host = runtime.Host(ctx)
assert host.start(), host.collector.summary()
win = host.current_window()
win.resize(W, H)
hidden = []
if not ctx.check:
    host.app.on_hide = lambda: hidden.append(1)
root = host.root
backend = host.backend
started = []
backend._start = lambda prog, args: started.append([os.path.basename(prog), args]) or True
spin(150)


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


def kind(item):
    return item.metaObject().className()


def lineage(item):
    m = item.metaObject()
    names = []
    while m is not None:
        names.append(m.className())
        m = m.superClass()
    return names


def is_text(item):
    """Text, TextEdit or TextInput, whether it is one itself or a QML type made from one (PlainText, MailText)."""
    return any(n in ("QQuickText", "QQuickTextEdit", "QQuickTextInput") for n in lineage(item))


def text_items(only_visible=True):
    for it in walk(root):
        if is_text(it) and (not only_visible or on_screen(it)):
            t = it.property("text")
            if isinstance(t, str) and t != "":
                yield it


def texts():
    return [it.property("text") for it in text_items()]


def has(text):
    return any(t == text for t in texts())


def has_part(text):
    return any(text in t for t in texts())


def find(name, visible=None):
    found = [it for it in walk(root) if it.objectName() == name and (visible is None or on_screen(it) == visible)]
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


def click_text(text, nth=0):
    """Click the visible Text with exactly this text (a button's label, a row's words)."""
    hits = [it for it in text_items() if it.property("text") == text]
    assert hits, f"no visible text {text!r} among {texts()}"
    click(hits[nth])


def key(k, mod=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(win, k, mod)
    spin(40)


def type_text(s):
    for ch in s:
        QTest.keyClick(win, ch)
        spin(5)
    spin(40)


def row_items():
    return [it for it in walk(root) if kind(it).startswith("QQuickItem") is False and it.objectName() == "unreadDot"]


def ev(item, code):
    """What a QML expression says in the scope of this item (a property the Python side cannot convert)."""
    from PySide6.QtQml import QQmlExpression
    return QQmlExpression(host.engine.rootContext(), item, code).evaluate()


def text_format(item):
    """"plain" when the item draws its text as it is (Text and TextEdit: PlainText is 0; a TextInput has no format)."""
    value, undefined = ev(item, "textFormat")
    return "plain" if undefined or value == 0 else "markup:" + str(value)


def orange(c):
    """Is this colour (RGB, whatever its alpha) one of the accent tokens?"""
    rgb = (c.red(), c.green(), c.blue())
    return rgb in ACCENT_RGB


theme = host.engine.singletonInstance("Bombadil", "Theme") if hasattr(host.engine, "singletonInstance") else None
ACCENT_RGB = {(0xd9, 0x77, 0x57), (0xe3, 0x8a, 0x6c), (0xc4, 0x63, 0x3f), (0xf2, 0xc4, 0xb3), (0xb4, 0x53, 0x2f)}


def shot(path=None):
    img = win.grabWindow()
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img.save(str(path))
    return img


def cleanup():
    host.close()
    if lb is not None:
        lb.close()
'''


def drive(home, body: str, *, size=(1280, 800), sample=None, samples=True, service=None, env=None,
          timeout=150) -> dict:
    """Runs `body` in a child that has the window up (see PRELUDE) and returns what it put in `out`."""
    run_env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
               "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
               "MAILT_W": str(size[0]), "MAILT_H": str(size[1]), "MAILT_SAMPLES": "1" if samples else "0"}
    run_env.pop("BOMBADIL_CHECK", None)
    run_env.pop("BOMBADIL_MAIL_SAMPLE", None)
    run_env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
    if sample is not None:
        run_env["MAILT_SAMPLE"] = sample
    if service is not None:
        run_env["MAILT_SERVICE"] = service
    run_env.update(env or {})
    script = (PRELUDE + "\ntry:\n" + textwrap.indent(textwrap.dedent(body), "    ")
              + "\nfinally:\n    cleanup()\nprint('RESULT ' + json.dumps(out))\n")
    r = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=run_env, timeout=timeout, check=False
    )
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"child failed (exit {r.returncode}):\n{r.stdout[-3000:]}\n{r.stderr[-4000:]}"
    return json.loads(lines[-1].removeprefix("RESULT "))


@pytest.fixture(autouse=True)
def places(home, monkeypatch):
    from mail_app_lab import scratch_env
    scratch_env(home, monkeypatch.setenv)
    monkeypatch.delenv("BOMBADIL_CHECK", raising=False)
    monkeypatch.delenv("BOMBADIL_MAIL_SAMPLE", raising=False)
    return home


# ---- what the window says -------------------------------------------------------------------------------------

def test_the_list_a_mail_and_what_can_be_done_with_it(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah") and has("Needs a reply"), 10, "the list")
        out["texts"] = texts()
        out["dots"] = sum(1 for it in find_all("unreadDot") if on_screen(it))
        out["clips"] = sum(1 for it in find_all("clip") if on_screen(it))
        out["unread_in_backend"] = sum(1 for m in backend.messages if m["unread"])
        out["clips_in_backend"] = sum(1 for m in backend.messages if m["attachments"])
        out["new_mail"] = on_screen(find("newMail"))
        # open the first mail with a click on its row
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        spin(200)
        out["opened"] = texts()
        out["actions"] = [n for n in ("replyButton", "replyAllButton", "forwardButton", "archiveButton", "deleteButton",
                                      "flagButton", "unreadButton", "needsReplyButton", "webLink") if shown(n)]
        out["dots_after"] = sum(1 for it in find_all("unreadDot") if on_screen(it))
        out["unread_after"] = lb.ask("list", view="all")["messages"][0]["unread"]
        click(find("webLink"))
        out["started"] = started[:]
        # the other mail: its attachment, saved
        click_text("Invoice F-0907 for September")
        wait_until(lambda: backend.mailState == "ready" and backend.opened["id"] == INVOICE, 8, "the invoice")
        spin(200)
        out["invoice"] = texts()
        click(find("saveAttachment"))
        wait_until(lambda: backend.quiet.startswith("Saved"), 8, "the file to be saved")
        spin(100)
        out["quiet"] = [t for t in texts() if t.startswith("Saved")]
    """, timeout=120)
    t = out["texts"]
    for words in ("Mail", "All inboxes", "maya@acme.example", "maya.reyes@gmail.example", "Needs a reply", "Drafts",
                  "Priya Shah", "Launch date by noon?", "Sam Ortiz", "Pricing copy: ok to go?",
                  "Brightline Billing", "Invoice F-0907 for September", "Add an account", "New mail"):
        assert words in t, f"{words!r} is not on the screen: {t}"
    assert any("admin" in s and "approve" in s for s in t), "the blocked account's note"
    assert "Open" in t
    assert out["dots"] == out["unread_in_backend"] > 0 and out["clips"] == out["clips_in_backend"] > 0
    assert out["new_mail"] is True
    o = out["opened"]
    for words in ("From", "To", "Time", "Priya Shah <priya@acme.example>", "maya@acme.example", "Reply", "Reply all",
                  "Forward", "Needs a reply", "Open in Gmail"):
        assert words in o, f"{words!r}: {o}"
    assert any("Legal is asking for the launch date" in s for s in o)
    assert out["actions"] == ["replyButton", "replyAllButton", "forwardButton", "archiveButton", "deleteButton",
                              "flagButton", "unreadButton", "needsReplyButton", "webLink"]
    assert out["dots_after"] == out["dots"] and out["unread_after"] is True, "opening a mail leaves it unread"
    assert out["started"] == [["bombadil", ["open", out["started"][0][1][1]]]]
    assert out["started"][0][1][1].startswith("https://mail.google.com/")
    assert "invoice-F-0907.pdf" in out["invoice"] and "Save" in out["invoice"]
    assert out["quiet"] == ["Saved to Downloads as invoice-F-0907.pdf"]


def test_a_strangers_markup_is_only_text(home):
    out = drive(home, """
        lb.engine.inject_new_mail("maya@acme.example", '"<b>Boss</b> <i>Ann</i>\\u202e" <ann@example.test>',
            '<h1>Big</h1> <a href="https://evil.example/x">click</a> &amp; <img src="x"> **md**',
            '<script>alert(1)</script> <a href="https://evil.example/">link</a>\\n[md](https://e.example) https://plain.example/p\\n'
            '<b>bold</b> &lt;tag&gt; fdp\\u202eexe.',
            ts=time.time())
        wait_until(lambda: any("Big" in t for t in texts()), 10, "the new mail")
        out["row"] = texts()
        click_text('<h1>Big</h1> <a href="https://evil.example/x">click</a> &amp; <img src="x"> **md**')
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        spin(250)
        out["open"] = texts()
        # every drawn text that has markup in it is drawn as plain text, whatever kind of text item draws it
        out["markup"] = [[it.property("text")[:20], text_format(it)] for it in text_items()
                         if "<" in it.property("text") or "&amp;" in it.property("text") or "**" in it.property("text")]
        out["plain_values"] = [text_format(find(name)) for name in ("mailSubject", "mailBody")]
        # the sample's own hostile mail: only words, and nothing came of them
        click_text("Action required: verify your mailbox today")
        wait_until(lambda: backend.mailState == "ready" and backend.opened["id"] == HOSTILE, 8, "the hostile mail")
        spin(250)
        out["hostile"] = texts()
        out["drafts"] = lb.ask("list", view="drafts")
        out["started"] = started[:]
        out["presses"] = lb.agentd.presses()
        out["folder"] = lb.ask("read", id=HOSTILE)["message"]["folder"]
        # an HTML-only mail comes as words, with its note
        click_text("Flow Weekly: five habits of calm teams")
        wait_until(lambda: backend.mailState == "ready" and backend.opened["id"] == NEWS, 8, "the newsletter")
        spin(250)
        out["news"] = texts()
    """)
    # the sender's name lost its direction marks and kept its markup as letters
    assert "<b>Boss</b> <i>Ann</i>" in out["row"]
    subject = '<h1>Big</h1> <a href="https://evil.example/x">click</a> &amp; <img src="x"> **md**'
    assert subject in out["row"] and subject in out["open"]
    assert any(s.startswith("<script>alert(1)</script> <a href=") and "<b>bold</b> &lt;tag&gt; fdpexe." in s
               for s in out["open"])
    assert '"<b>Boss</b> <i>Ann</i>" <ann@example.test>' in out["open"]
    assert out["markup"] and out["plain_values"] == ["plain", "plain"]
    assert all(fmt == "plain" for _, fmt in out["markup"]), out["markup"]
    assert any("NOTICE TO THE AI ASSISTANT" in s for s in out["hostile"])
    assert out["drafts"] == [] and out["started"] == [] and out["presses"] == [] and out["folder"] == "inbox"
    assert "This mail came as a web page. Only its words are shown." in out["news"]
    assert not any("<" in s and ("html" in s.lower() or "<div" in s) for s in out["news"])


def test_the_first_run_a_sign_in_and_the_empty_views(home):
    out = drive(home, """
        wait_until(lambda: has("Your email address"), 10, "the first-run field")
        out["first"] = texts()
        out["sidebar_shown"] = has("All inboxes")
        out["add_enabled_empty"] = find("addButton").property("enabled")
        field = find("addressField")
        click(field)
        type_text("someone@workspace.example")
        out["typed"] = field.property("text")
        out["add_enabled"] = find("addButton").property("enabled")
        click(find("addButton"))
        wait_until(lambda: has("Signing in to someone@workspace.example"), 10, "the sign-in")
        out["signin"] = texts()
        click_text("Show Thunderbird's window")
        wait_until(lambda: has("Done"), 8, "the engine's window")
        out["staged"] = texts()
        click_text("Done")
        wait_until(lambda: has("Show Thunderbird's window"), 8, "the window put away")
        lb.engine.complete_signin("someone@workspace.example")
        wait_until(lambda: has("All inboxes"), 10, "the account")
        wait_until(lambda: has("Your inbox is empty."), 10, "the empty inbox")
        out["inbox"] = texts()
        click_text("Needs a reply")
        wait_until(lambda: has("Nothing needs a reply."), 8, "the empty view")
        out["needs"] = texts()
        click_text("Drafts")
        wait_until(lambda: has("No drafts."), 8, "the empty view")
        out["drafts"] = texts()
        click(find("searchField"))
        type_text("zzz")
        wait_until(lambda: has("Nothing found.") or has("No drafts."), 8)
        out["search"] = texts()
    """, samples=False, timeout=150)
    assert "Your email address" in out["first"] and "Add" in out["first"]
    assert out["sidebar_shown"] is False and out["add_enabled_empty"] is False
    assert out["typed"] == "someone@workspace.example" and out["add_enabled"] is True
    assert "Signing in to someone@workspace.example" in out["signin"] and "Show Thunderbird's window" in out["signin"]
    assert "Done" in out["staged"] and "Show Thunderbird's window" not in out["staged"]
    assert "Your inbox is empty." in out["inbox"] and "All inboxes" in out["inbox"]
    assert "Nothing needs a reply." in out["needs"] and "No drafts." in out["drafts"]


def test_a_service_that_is_not_there_and_an_engine_that_goes(home):
    gone = drive(home, """
        spin(300)
        out["texts"] = texts()
        out["note"] = [t for t in texts() if "not running" in t]
        lb.run(lb._start_service())
        wait_until(lambda: has("All inboxes") and has("Priya Shah"), 10, "the window to find the service")
        out["after"] = texts()
    """, service="down")
    assert gone["note"] == ["Mail is not running yet. This fills in as soon as it starts."]
    assert "All inboxes" not in gone["texts"] and "All inboxes" in gone["after"]
    assert not any("not running" in t for t in gone["after"])
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        lb.engine.set_up(False)
        wait_until(lambda: backend.engineNote != "", 8, "the engine's state")
        spin(200)
        out["note"] = backend.engineNote
        out["texts"] = texts()
        # what was loaded is still there, and a list that cannot be read is one plain line
        click_text("maya.reyes@gmail.example", 0)
        wait_until(lambda: backend.listState == "error", 8, "the error")
        spin(200)
        out["error"] = backend.listError
        out["error_texts"] = texts()
        lb.engine.set_up(True)
        wait_until(lambda: backend.engineNote == "" and backend.listState == "ready", 8, "the engine back")
        spin(100)
        out["back"] = texts()
        # a quiet "loading" while the first list is on its way
        backend._list_state = "loading"
        backend._messages = []
        backend.listChanged.emit()
        spin(100)
        out["loading"] = texts()
    """)
    assert out["note"] and out["note"] in out["texts"] and "Priya Shah" in out["texts"]
    assert out["error"] in out["error_texts"] and "\n" not in out["error"]
    assert not any(code in t for t in out["error_texts"] for code in ("engine_down", "engine_error", "internal"))
    assert out["note"] not in out["back"]
    assert "Loading" in out["loading"]


def test_a_blocked_account_says_why_and_has_a_link_to_the_web(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        out["sidebar"] = texts()
        link = [it for it in text_items() if it.property("text") == "Open"]
        out["links"] = len(link)
        click(link[0])
        out["started"] = started[:]
    """)
    assert any("admin" in t for t in out["sidebar"]) and out["links"] >= 1
    assert out["started"] == [["bombadil", ["open", "https://outlook.office.com/mail/"]]]


# ---- the reply box, Send and the keys -------------------------------------------------------------------------

def test_send_follows_the_gate_is_the_one_press_and_a_typed_return_never_sends(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        click(find("replyButton"))
        wait_until(lambda: backend.draft is not None, 8, "the draft")
        spin(200)
        send = find("sendButton")
        out["box"] = texts()
        out["to"] = find("toRow").property("text")
        out["subject"] = find("subjectRow").property("text")
        out["label"] = find("yoursLabel").property("text")
        # until the box has been told to the service, and the button has been pressable a moment, it is dimmed
        out["early_note"] = [t for t in texts() if t in ("Waiting for Mail to confirm what you see.",)]
        click(send)                       # a click on a Send that is not live presses nothing
        out["presses_early"] = list(lb.agentd.presses())
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send to be live")
        out["live"] = send.property("live")
        out["note_live"] = find("sendNote").property("text")
        # typing: Send goes dim at once and is live again once the new draft is drawn
        key(Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        type_text(" Yes.")
        out["typing_live"] = send.property("live")
        out["typing_note"] = find("sendNote").property("text")
        out["body"] = find("bodyEditor").property("text")
        # a Return typed in the editor is a new line, not a send
        key(Qt.Key.Key_Return)
        key(Qt.Key.Key_Enter)
        out["body_after_return"] = find("bodyEditor").property("text")
        spin(300)
        out["presses_after_return"] = list(lb.agentd.presses())
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send to be live again")
        click(send)
        wait_until(lambda: backend.receipt is not None, 10, "the receipt")
        spin(300)
        out["presses"] = list(lb.agentd.presses())
        out["receipt"] = texts()
        out["draft_after"] = backend.draft is None
        out["sent"] = [[a["email"] for a in m["to"]] + [m["body"]] for m in lb.engine.sent]
    """, timeout=150)
    assert out["to"] == "Priya Shah <priya@acme.example>" and out["subject"] == "Re: Launch date by noon?"
    assert out["label"] == "Yours: Send" and "Reply" in out["box"] and "Attach a file" in out["box"]
    assert out["presses_early"] == [] and out["live"] is True and out["note_live"] == ""
    assert out["typing_live"] is False and out["typing_note"] in ("Saving", "Waiting for Mail to confirm what you see.")
    assert out["body"].endswith(" Yes.") and out["body_after_return"].endswith(" Yes.\n\n")
    assert out["presses_after_return"] == []
    assert len(out["presses"]) == 1
    press = out["presses"][0]
    assert list(press) == ["type", "kind", "id", "fingerprint"] and press["type"] == "press" and press["kind"] == "mail"
    assert any(t.startswith("Sent to Priya from maya@acme.example") for t in out["receipt"])
    assert "Open in Gmail" in out["receipt"] and out["draft_after"] is True
    assert len(out["sent"]) == 1 and out["sent"][0][0] == "priya@acme.example" and out["sent"][0][1].endswith("Yes.\n\n")


def test_esc_leaves_the_reply_box_first_and_then_closes_the_window(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        key(Qt.Key.Key_R)                  # the keyboard's reply: the mail is open, nothing is being typed
        wait_until(lambda: backend.draft is not None, 8, "the draft")
        spin(300)
        out["box_open"] = shown("replyBox")
        type_text("abc#R")                 # letters typed in the box are text: the mail is not archived or trashed
        out["body"] = find("bodyEditor").property("text")
        key(Qt.Key.Key_Escape)
        spin(300)
        out["box_after_esc"] = shown("replyBox")
        out["hidden_after_first"] = len(hidden)
        out["draft_kept"] = len(lb.ask("list", view="drafts"))
        out["typed_kept"] = lb.ask("list", view="drafts")[0]["body"] if lb.ask("list", view="drafts") else None
        out["folder"] = lb.ask("read", id=LAUNCH)["message"]["folder"]
        key(Qt.Key.Key_Escape)
        spin(200)
        out["hidden_after_second"] = len(hidden)
        # in the search field Esc clears the words first
        key(Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        type_text("pricing")
        out["search"] = find("searchField").property("text")
        key(Qt.Key.Key_Escape)
        out["search_after_esc"] = find("searchField").property("text")
        out["hidden_after_search"] = len(hidden)
    """, timeout=150)
    assert out["box_open"] is True and out["body"].endswith("abc#R")
    assert out["box_after_esc"] is False and out["hidden_after_first"] == 0
    assert out["draft_kept"] == 1 and out["typed_kept"].endswith("abc#R") and out["folder"] == "inbox"
    assert out["hidden_after_second"] == 1
    assert out["search"] == "pricing" and out["search_after_esc"] == "" and out["hidden_after_search"] == 1


def test_the_keys_move_in_the_list_open_reply_archive_and_delete(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        ids = [m["id"] for m in backend.messages]
        out["ids"] = ids
        list_view = find("mailList")
        list_view.forceActiveFocus()
        spin(100)
        key(Qt.Key.Key_Down)
        key(Qt.Key.Key_Down)
        out["cursor"] = list_view.property("currentIndex")
        out["opened_by_arrows"] = backend.opened is not None
        key(Qt.Key.Key_Return)
        wait_until(lambda: backend.opened is not None and backend.mailState == "ready", 8, "Return to open")
        out["opened"] = backend.opened["id"]
        key(Qt.Key.Key_A)
        wait_until(lambda: out["opened"] not in [m["id"] for m in backend.messages], 8, "the archive")
        out["archived"] = lb.ask("read", id=out["opened"])["message"]["folder"]
        wait_until(lambda: backend.opened is not None and backend.mailState == "ready", 8, "the next mail")
        out["next"] = backend.opened["id"]
        QTest.keyClick(win, "#")
        wait_until(lambda: out["next"] not in [m["id"] for m in backend.messages], 8, "the delete")
        out["trashed"] = lb.ask("read", id=out["next"])["message"]["folder"]
        out["presses"] = list(lb.agentd.presses())
    """, timeout=150)
    assert out["cursor"] == 1 and out["opened_by_arrows"] is False    # the first Down lands on the first row
    assert out["opened"] == out["ids"][1] and out["archived"] == "archive"
    assert out["next"] != out["opened"] and out["trashed"] == "trash"
    assert out["presses"] == []


def test_below_900_the_left_column_is_tabs_and_below_640_one_pane_shows(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        out["wide"] = {"tabs": shown("viewTabs"), "add": has("Add an account"), "reader": has("Pick a mail to read it.")}
        win.resize(880, 700)
        spin(300)
        out["narrow"] = {"tabs": shown("viewTabs"), "add": has("Add an account"), "list": has("Priya Shah"),
                         "reader": has("Pick a mail to read it."), "side": any(t.startswith("All inboxes  ") for t in texts()),
                         "views": [t for t in texts() if t.startswith(("Needs a reply", "Drafts"))],
                         "note": has_part("asks an admin to approve mail apps."), "open": has("Open")}
        # every tab is in sight: none is scrolled out past the edge of the window
        tab_boxes = [scene_rect(it) for it in text_items() if it.property("text").startswith(
            ("All inboxes", "maya", "Needs a reply", "Drafts"))]
        out["tab_boxes_inside"] = all(0 <= x0 and x1 <= win.width() for x0, _, x1, _ in tab_boxes) and len(tab_boxes) >= 6
        win.resize(600, 700)
        spin(300)
        out["single"] = {"list": has("Priya Shah"), "reader": has("Pick a mail to read it.")}
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        spin(300)
        out["single_open"] = {"list": has("Sam Ortiz"), "back": has("Back"), "from": has("Priya Shah <priya@acme.example>")}
        click_text("Back")
        spin(300)
        out["single_back"] = {"list": has("Sam Ortiz"), "opened": backend.opened is not None}
    """, size=(1000, 700))
    assert out["wide"] == {"tabs": False, "add": True, "reader": True}
    nar = out["narrow"]
    assert nar["tabs"] is True and nar["add"] is True and nar["list"] is True and nar["side"] is True
    assert nar["reader"] is True and len(nar["views"]) == 2 and out["tab_boxes_inside"] is True
    assert nar["note"] is True and nar["open"] is True, "an account that cannot be read still says so, with its link"
    assert out["single"] == {"list": True, "reader": False}
    assert out["single_open"] == {"list": False, "back": True, "from": True}
    assert out["single_back"] == {"list": True, "opened": False}


# ---- the orange of the window is Send's ------------------------------------------------------------------------

def test_the_only_orange_is_the_ring_on_send_in_the_tree_the_files_and_the_pixels(home):
    out = drive(home, """
        # a draft with a warning, from the agent, opened the way a notice's Open would
        d = lb.ask("draft", agent=True, kind="reply", reply_to=LAUNCH, to="legal@acme-partners.example",
                   body="The 14th works.", tainted=True)
        out["warnings"] = [w["kind"] for w in d["warnings"]]
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        lb.push_show(reply=d["id"])
        wait_until(lambda: backend.draft is not None and backend.mailState == "ready", 10, "the draft")
        wait_until(lambda: backend.canSend and find("sendButton").property("armed"), 10, "Send")
        click(find("bodyEditor"))
        spin(300)
        bar = find("sendBar")
        out["warning_text"] = [t for t in texts() if "legal@acme-partners.example" in t]
        bx0, by0, bx1, by1 = scene_rect(bar)
        ring = find("sendRing")
        out["ring_width"] = ev(ring, "border.width")[0]
        out["ring_orange"] = orange(ev(ring, "border.color")[0])
        # the item tree: any rectangle, text or icon of an accent colour is inside the Send bar
        stray = []
        for it in walk(root):
            if not on_screen(it):
                continue
            colours = []
            c = it.property("color")
            if isinstance(c, QColor):
                colours.append(("color", c))
            if "QQuickRectangle" in lineage(it):
                width, colour = ev(it, "border.width")[0], ev(it, "border.color")[0]
                if width and width > 0 and isinstance(colour, QColor):
                    colours.append(("border", colour))
            for what, c in colours:
                if c.alpha() > 0 and orange(c):
                    x0, y0, x1, y1 = scene_rect(it)
                    if not (x0 >= bx0 - 1 and y0 >= by0 - 1 and x1 <= bx1 + 1 and y1 <= by1 + 1):
                        stray.append([kind(it), it.objectName(), what])
        out["stray_items"] = stray
        # the pixels: orange (the accent's hue and strength) only in the Send bar
        img = shot(os.environ.get("MAILT_SHOT"))
        orange_px, outside = 0, []
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                h, s, v, _ = c.getHsvF()
                if 0.03 <= h <= 0.075 and s >= 0.45 and v >= 0.55:
                    orange_px += 1
                    if not (bx0 - 3 <= x <= bx1 + 3 and by0 - 3 <= y <= by1 + 3):
                        outside.append([x, y])
        out["orange_px"], out["orange_outside"] = orange_px, outside[:20]
        out["bar"] = [bx0, by0, bx1, by1]
    """, timeout=150)
    assert out["warnings"] == ["new_address"] and out["warning_text"]
    assert out["ring_width"] == 2 and out["ring_orange"] is True
    assert out["stray_items"] == [], out["stray_items"]
    assert out["orange_px"] > 50, "the ring on Send is drawn"
    assert out["orange_outside"] == [], f"orange outside the Send bar, at {out['orange_outside']}"
    # the files: the accent is named nowhere else
    for qml in QML_FILES:
        src = qml.read_text()
        if qml.name != "SendBar.qml":
            assert not re.search(r"Theme\.accent|accentSoft|accentInk|accentHover|accentPressed", src), qml.name
            assert not re.search(r"highlighted\s*:|\bTheme\.tone\(\"accent\"\)|tone\s*:\s*\"accent\"", src), qml.name


def test_nothing_presses_but_the_send_buttons_own_click_and_nothing_is_rich_text():
    sources = {q.name: q.read_text() for q in QML_FILES}
    code = {name: re.sub(r"(?m)(^|\s)//.*$", "", src) for name, src in sources.items()}     # without the comments
    calls = [(name, m.start()) for name, src in code.items() for m in re.finditer(r"\bpress\s*\(", src)]
    assert [name for name, _ in calls] == ["SendBar.qml"], calls
    bar = sources["SendBar.qml"]
    assert len(re.findall(r"backend\.press\(\)", bar)) == 1
    activate = bar[bar.index("function activate()"):]
    activate = activate[:activate.index("\n            }\n") + 14]
    assert "bar.live" in activate and "backend.press()" in activate
    # activate() is called by the click and by the keys on the button itself, and by nothing else
    callers = [m.start() for m in re.finditer(r"send\.activate\(\)", bar)]
    assert len(callers) >= 4 and all(
        re.search(r"(onTapped|Keys\.on\w+Pressed|onPressAction)\s*:", bar[max(0, c - 60):c]) for c in callers)
    assert not re.search(r"Shortcut\b[^}]*activate|Qt\.callLater\([^)]*activate|Timer[^}]*activate", bar)
    # no other file can say "press" to anything: the word is not in a handler, an action or a shortcut
    for name, src in sources.items():
        if name != "SendBar.qml":
            assert "backend.press" not in src and "agent" not in src.lower().replace("agentd", ""), name
    # a stranger's words are never drawn by a Text that guesses its format, or by the kit's own rich-text widgets
    for name, src in sources.items():
        if name != "PlainText.qml":
            assert not re.search(r"(^|[\s{])Text\s*\{", src), f"{name} draws a bare Text"
        assert not re.search(r"\b(Body|Caption|Label|EmptyState|Heading|Mono|DetailGrid|Badge)\s*\{", src), name
        assert not re.search(r"textFormat\s*:\s*(Text|TextEdit)\.(Rich|Styled|Auto|Markdown)", src), name
        assert "linkActivated" not in src and "onLinkHovered" not in src and "Qt.openUrlExternally" not in src, name
        assert not re.search(r"DropArea|DragHandler|onDropped|Drag\.", src), f"{name}: files come in by the chooser only"
    assert "Text.PlainText" in sources["PlainText.qml"] and "TextEdit.PlainText" in sources["MailText.qml"]


# ---- the pictures -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("size", [(1280, 800), (900, 700)])
def test_the_sample_pictures(home, size):
    shots = os.environ.get("BOMBADIL_SCREENS")
    names = {"": "mail-list", "reply": "mail-reply", "first-run": "mail-first-run"}
    for sample, name in names.items():
        suffix = "" if size == (1280, 800) else f"-{size[0]}"
        shot = Path(shots or home / "shots") / f"{name}{suffix}.png"
        out = drive(home, f"""
            spin(1200)
            out["texts"] = texts()
            img = shot({str(shot)!r})
            out["size"] = [img.width(), img.height()]
            colours = set()
            for y in range(0, img.height(), 7):
                for x in range(0, img.width(), 7):
                    colours.add(img.pixel(x, y))
            out["colours"] = len(colours)
            out["errors"] = host.collector.summary()["errors"] + host.collector.summary()["warnings"]
        """, size=size, sample=sample, timeout=90)
        assert out["size"] == list(size) and out["colours"] > 12 and out["errors"] == []
        assert shot.exists() and shot.stat().st_size > 5000
        if sample == "":
            assert "Launch date by noon?" in out["texts"] and "Hi Maya,\n\nLegal needs the launch date by noon. Does the 14th work on your side?\n\nPriya" in out["texts"]
        elif sample == "reply":
            assert "Reply" in out["texts"] and "Send" in out["texts"] and "Yours: Send" in out["texts"]
            assert any("legal@acme-partners.example" in t for t in out["texts"])
            assert "Your Gmail signature and the quoted message are added when it sends." in out["texts"]
        else:
            assert "Your email address" in out["texts"] and "All inboxes" not in out["texts"]

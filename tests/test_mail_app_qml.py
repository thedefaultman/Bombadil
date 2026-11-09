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


def visible_size(item):
    """How much of the item can be seen: its width and height inside the window and every clipping ancestor."""
    x0, y0, x1, y1 = scene_rect(item)
    bx0, by0, bx1, by1 = 0, 0, win.width(), win.height()
    a = item.parentItem()
    while a is not None:
        if a.property("clip") is True:
            ax0, ay0, ax1, ay1 = scene_rect(a)
            bx0, by0, bx1, by1 = max(bx0, ax0), max(by0, ay0), min(bx1, ax1), min(by1, ay1)
        a = a.parentItem()
    return max(0, min(x1, bx1) - max(x0, bx0)), max(0, min(y1, by1) - max(y0, by0))


def on_screen(item):
    """Visible, inside the window, and not scrolled or clipped out of sight by an ancestor."""
    if not item.isVisible() or item.width() <= 0 or item.height() <= 0 or item.opacity() <= 0:
        return False
    w, h = visible_size(item)
    return w > 1 and h > 1


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
        out["hint_colour"] = ev(find("readerHint"), "color")[0].name()
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
    assert sum(1 for s in t if "admin" in s and "approve" in s) == 1, "the blocked account's note, said once"
    assert "Open" in t
    assert out["dots"] == out["unread_in_backend"] > 0 and out["clips"] == out["clips_in_backend"] > 0
    assert out["new_mail"] is True
    assert out["hint_colour"] == "#8b939c", "the only instruction in the empty pane is readable (Theme.muted)"
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


def test_a_mail_that_is_one_unbroken_line_is_laid_out_in_a_blink(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        lb.engine.inject_new_mail("maya@acme.example", "Blob <blob@example.test>", "Blob", "A" * 200_000,
                                  ts=time.time())
        wait_until(lambda: any(t == "Blob" for t in texts()), 10, "the mail")
        shot()                                    # (the window has been drawn once)
        click_text("Blob")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        t0 = time.monotonic()
        shot()
        out["seconds"] = time.monotonic() - t0
        text = find("mailBody").property("text")
        out["run"], out["length"] = max(len(r) for r in text.split()), len(text)
        out["note"] = find("mailNote").property("text")      # (below the text, out of sight)
    """)
    assert out["run"] <= 500 and out["length"] <= 50_200
    assert out["note"] == "This is the start of a long mail. The rest is on the web."
    assert out["seconds"] < 2.0, f"laying the mail out took {out['seconds']:.1f} s (unfolded it takes about 3)"


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


def test_a_box_that_is_being_sent_takes_no_typing_and_send_is_for_what_it_shows(home):
    out = drive(home, """
        from bombadil.mail import service
        service.SEND_S = 5.0
        lb.engine.send_delay = 1.5
        lb.engine.fail_send("engine_error", "The provider refused the message.")
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        click(find("replyButton"))
        wait_until(lambda: backend.draft is not None, 8, "the draft")
        send = find("sendButton")
        body = find("bodyEditor")
        click(body)
        type_text("Yes.")
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send to be live")
        out["before"] = [body.property("readOnly"), find("toRow").property("readOnly")]
        click(send)
        wait_until(lambda: backend.pressState == "sending", 5, "the press")
        out["during"] = [body.property("readOnly"), find("toRow").property("readOnly"),
                         find("subjectRow").property("readOnly"), find("sendLabel").property("text")]
        click(body)
        type_text("EXTRA WORDS")
        out["body_during"] = body.property("text")
        wait_until(lambda: backend.pressLine != "" and backend.pressState == "", 10, "the refusal")
        spin(400)
        out["line"] = backend.pressLine
        out["after"] = [body.property("readOnly"), body.property("text"), backend.draftFields["body"]]
        lb.engine.send_ok()
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send to be live again")
        click(send)
        wait_until(lambda: backend.receipt is not None, 10, "the receipt")
        out["sent"] = [m["body"] for m in lb.engine.sent]
    """, timeout=150)
    assert out["before"] == [False, False] and out["during"] == [True, True, True, "Sending"]
    assert out["body_during"] == "Yes." and out["line"].startswith("The provider refused the message.")
    after_readonly, shown_text, held_text = out["after"]
    assert after_readonly is False and shown_text == held_text == "Yes."
    assert [b for b in out["sent"]] == ["Yes."]


def test_a_sign_in_that_goes_wrong_has_a_way_out_and_never_hides_the_field(home):
    first = drive(home, """
        wait_until(lambda: has("Your email address"), 10, "the first-run field")
        click(find("addressField"))
        type_text("someone@workspace.example")
        click(find("addButton"))
        wait_until(lambda: has("Signing in to someone@workspace.example"), 10, "the sign-in")
        out["signin"] = {"give_up": shown("giveUp"), "field": shown("addressField")}
        click(find("giveUp"))
        wait_until(lambda: shown("addressField") and not shown("signinBlock"), 10, "the field again")
        out["accounts"] = len(backend.accounts)
        click(find("addressField"))
        type_text("other@workspace.example")
        click(find("addButton"))
        wait_until(lambda: has("Signing in to other@workspace.example"), 10, "the second sign-in")
    """, samples=False)
    assert first["signin"] == {"give_up": True, "field": False} and first["accounts"] == 0
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        backend.addAccount("someone@workspace.example")
        wait_until(lambda: backend.signingIn is not None, 10, "the sign-in")
        wait_until(lambda: any(t == "Signing in" for t in texts()), 8, "its note in the left column")
        out["note"] = [t for t in texts() if t == "Signing in"]
        out["side_engine"] = shown("sideEngine")
        click(find("addAccount"))
        wait_until(lambda: shown("addressField"), 8, "the field, beside the sign-in")
        out["both"] = {"field": shown("addressField"), "block": shown("signinBlock"), "give_up": shown("giveUp"),
                       "title": has("Add an account"), "texts": has("Signing in to someone@workspace.example")}
        click(find("giveUp"))
        wait_until(lambda: backend.signingIn is None and len(backend.accounts) == 3, 10, "the account to go")
        spin(300)
        out["after"] = {"block": shown("signinBlock"), "field": shown("addressField"), "texts": has_part("Signing in")}
    """, env={"HOME": str(home / "again")})       # (a home of its own: the first run left an account in its notes)
    assert out["note"] == ["Signing in"] and out["side_engine"] is True
    assert out["both"] == {"field": True, "block": True, "give_up": True, "title": True, "texts": True}
    assert out["after"] == {"block": False, "field": True, "texts": False}


def test_an_account_still_fetching_says_what_it_waits_for_in_the_left_column(home):
    out = drive(home, """
        wait_until(lambda: has("Your email address"), 10, "the first-run field")
        click(find("addressField"))
        type_text("someone@icloud.com")
        click(find("addButton"))
        wait_until(lambda: has("All inboxes"), 10, "the account")
        wait_until(lambda: any("app-specific password" in t for t in texts()), 10, "the account's note")
        out["note"] = [t for t in texts() if "app-specific password" in t]
        out["engine"] = shown("sideEngine")
        wait_until(lambda: backend.listState != "loading", 10, "the list")
        out["list"] = [t for t in texts() if t in ("Your inbox is empty.", "Nothing to show yet.")]
        out["waiting"] = backend.listWaiting
    """, samples=False)
    assert len(out["note"]) == 1 and out["engine"] is True
    assert out["waiting"] is True and out["list"] == ["Nothing to show yet."]


def test_a_long_list_of_recipients_wraps_and_says_how_long_it_is(home):
    out = drive(home, """
        to = [f"person{i}@example.test" for i in range(13)]
        cc = [f"copy{i}@example.test" for i in range(6)]
        d = lb.ask("draft", kind="new", to=to, cc=cc, subject="Everyone", body="Hello.")
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        lb.push_show(reply=d["id"])
        wait_until(lambda: backend.draft is not None, 10, "the draft")
        spin(500)
        row = find("toRow")
        field_h = ev(row, "input.height")[0]
        content_h = ev(row, "input.field.contentHeight")[0]
        out["to"] = {"text": row.property("text"), "height": field_h, "content": content_h,
                     "visible": visible_size(row)[1]}
        out["cc"] = {"text": find("ccRow").property("text"), "shown": shown("ccRow")}
        out["notes"] = [it.property("text") for it in find_all("fieldNote") if on_screen(it)]
        out["from_truncated"] = ev(find("boxFrom"), "truncated")[0]
        out["from"] = find("boxFrom").property("text")
        out["from_visible"] = visible_size(find("boxFrom"))[0] > 100
    """, size=(900, 700))
    assert all(f"person{i}@example.test" in out["to"]["text"] for i in range(13))
    assert out["cc"]["shown"] is True and all(f"copy{i}@example.test" in out["cc"]["text"] for i in range(6))
    # as tall as its words need (up to four lines), not one line with the rest out of sight
    assert out["to"]["height"] >= min(out["to"]["content"] + 16, 4 * 17 + 16) - 2 and out["to"]["height"] > 50
    assert out["notes"] == ["13 people", "6 people"]
    assert out["from_truncated"] is False and out["from"].startswith("from Maya Reyes <maya@acme.example>")


def test_on_a_short_pane_the_mail_is_one_line_and_the_box_is_read_whole(home):
    out = drive(home, """
        spin(800)
        box = find("bodyEditor")
        out["editor"] = [visible_size(box)[1], box.height()]
        out["summary"] = [t for t in texts() if t.startswith("Replying to")]
        out["toggle"] = [t for t in texts() if t.endswith("the mail")]
        out["mail_text"] = has_part("Legal needs the launch date")
        out["send"] = shown("sendButton")
        out["bar_bottom"] = scene_rect(find("sendBar"))[3] <= win.height()
        click_text("Show the mail")
        spin(400)
        out["shown_text"] = has_part("Legal needs the launch date")
        out["toggle_after"] = [t for t in texts() if t.endswith("the mail")]
    """, size=(900, 700), sample="reply")
    assert out["summary"] == ["Replying to Priya Shah: Launch date by noon?"] and out["toggle"] == ["Show the mail"]
    assert out["mail_text"] is False and out["send"] is True and out["bar_bottom"] is True
    seen, whole = out["editor"]
    assert seen >= 100 and seen >= whole - 1, f"the editor is cut off: {seen} of {whole}"
    assert out["shown_text"] is True and out["toggle_after"] == ["Hide the mail"]


def test_a_draft_changed_under_send_is_said_and_send_waits_for_it(home):
    out = drive(home, """
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        click(find("replyButton"))
        wait_until(lambda: backend.draft is not None, 8, "the draft")
        send = find("sendButton")
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send to be live")
        out["note_before"] = shown("changeNote")
        lb.ask("draft_edit", agent=True, id=backend.draft["id"], to="legal@acme-partners.example",
               body="Changed by Bombadil.", tainted=True)
        t0 = time.monotonic()
        wait_until(lambda: backend.changeNote != "", 8, "the note")
        out["note"] = [t for t in texts() if t.startswith("This draft was changed while it was open")]
        went_dark = None
        live_at = None
        while time.monotonic() - t0 < 6:
            live = bool(send.property("live"))
            if not live and went_dark is None:
                went_dark = time.monotonic() - t0
            if live and went_dark is not None:
                live_at = time.monotonic() - t0
                break
            spin(20)
        out["dark"], out["live_at"] = went_dark is not None, live_at
        out["body"] = find("bodyEditor").property("text")
        click(find("bodyEditor"))
        type_text("!")
        out["note_after_typing"] = shown("changeNote")
    """, timeout=150)
    assert out["note_before"] is False and len(out["note"]) == 1 and out["dark"] is True
    assert out["live_at"] is not None and out["live_at"] >= 1.5, f"Send was live again after {out['live_at']} s"
    assert out["body"] == "Changed by Bombadil." and out["note_after_typing"] is False


def test_an_unknown_outcome_asks_for_a_look_in_sent_each_time(home):
    out = drive(home, """
        lb.engine.hang_send()
        wait_until(lambda: has("Priya Shah"), 10, "the list")
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        click(find("replyButton"))
        wait_until(lambda: backend.draft is not None, 8, "the draft")
        send = find("sendButton")
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send to be live")
        click(send)
        wait_until(lambda: backend.unknownOutcome and backend.pressState == "", 10, "the unknown outcome")
        spin(500)
        out["first"] = {"texts": [t for t in texts() if t.startswith(("I can't tell", "Tick the box"))],
                        "tick": shown("lookedTick"), "link": shown("sentLink"), "live": bool(send.property("live")),
                        "label": find("sendLabel").property("text")}
        click(find("sentLink"))
        out["opened"] = started[:]
        click(find("lookedTick"))
        wait_until(lambda: backend.looked, 5, "the tick")
        wait_until(lambda: backend.canSend and send.property("armed"), 10, "Send again to be live")
        out["ticked"] = find("sendLabel").property("text")
        click(send)
        wait_until(lambda: backend.pressState == "" and backend.unknownOutcome and not backend.looked, 10,
                   "the second unknown outcome")
        spin(600)
        out["second"] = {"tick_checked": bool(find("lookedTick").property("checked")),
                         "label": find("sendLabel").property("text"), "live": bool(send.property("live"))}
        out["presses"] = lb.agentd.presses()
    """, timeout=150)
    first = out["first"]
    assert first["texts"] == ["I can't tell whether that went. Look in Sent before you press Send again."]
    assert first["tick"] is True and first["link"] is True and first["live"] is False and first["label"] == "Send"
    assert out["opened"] and out["opened"][0][1][0] == "open" and out["opened"][0][1][1].startswith("https://")
    assert out["ticked"] == "Send again"
    assert out["second"] == {"tick_checked": False, "label": "Send", "live": False}
    assert len(out["presses"]) == 2 and "again" not in out["presses"][0] and out["presses"][1]["again"] is True


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
        spin(300)
        # it is said, nothing else is opened for the person, and the keyboard is on what moved up into its place
        out["after_a"] = {"opened": backend.opened is None, "quiet": backend.quiet,
                          "cursor": list_view.property("currentIndex"),
                          "shown": [t for t in texts() if t.startswith("Archived: ")]}
        key(Qt.Key.Key_Return)
        wait_until(lambda: backend.opened is not None and backend.mailState == "ready", 8, "the next mail")
        out["next"] = backend.opened["id"]
        out["next_is_under"] = out["next"] == ids[2]
        QTest.keyClick(win, "#")
        wait_until(lambda: out["next"] not in [m["id"] for m in backend.messages], 8, "the delete")
        out["trashed"] = lb.ask("read", id=out["next"])["message"]["folder"]
        spin(200)
        out["after_hash"] = backend.quiet
        out["presses"] = list(lb.agentd.presses())
    """, timeout=150)
    assert out["cursor"] == 1 and out["opened_by_arrows"] is False    # the first Down lands on the first row
    assert out["opened"] == out["ids"][1] and out["archived"] == "archive"
    after = out["after_a"]
    assert after["opened"] is True and after["quiet"].startswith("Archived: ") and after["shown"] == [after["quiet"]]
    assert after["cursor"] == 1
    assert out["next_is_under"] is True and out["trashed"] == "trash"
    assert out["after_hash"].startswith("Moved to Trash: ")
    assert out["presses"] == []


def test_a_held_key_acts_once_and_typing_a_word_to_look_for_does_neither(home):
    out = drive(home, """
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QKeyEvent
        wait_until(lambda: has("Priya Shah"), 10, "the list")

        def held(k, text, repeats=5):
            # the first press and then what a held key sends: auto-repeats, with no release between them
            for repeat in [False] + [True] * repeats:
                ev = QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text, repeat, 1)
                QCoreApplication.sendEvent(win, ev)
                spin(60)

        def folders():
            found = lb.ask("search", limit=100)["messages"]
            return sorted(m["folder"] for m in found if m["folder"] in ("archive", "trash"))

        ids = [m["id"] for m in backend.messages]
        out["start"] = folders()
        list_view = find("mailList")
        list_view.forceActiveFocus()
        click_text("Launch date by noon?")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail")
        list_view.forceActiveFocus()
        # repeats of a key that was pressed before this mail was open do nothing
        for _ in range(4):
            QCoreApplication.sendEvent(win, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                                                      Qt.KeyboardModifier.NoModifier, "a", True, 1))
            spin(40)
        out["after_repeats_only"] = [folders() == out["start"], backend.opened is not None, backend.searchText]
        held(Qt.Key.Key_A, "a")
        spin(400)
        out["after_held_a"] = folders()
        out["opened_after_a"] = backend.opened is not None
        # a held # is one delete of the mail that was open (none is open now: nothing is deleted)
        click_text("Pricing copy: ok to go?")
        wait_until(lambda: backend.mailState == "ready", 8, "the second mail")
        list_view.forceActiveFocus()
        held(Qt.Key.Key_NumberSign, "#")
        spin(400)
        out["after_held_hash"] = [folders(), lb.ask("read", id=SAM)["message"]["folder"],
                                  lb.ask("read", id=LEO)["message"]["folder"]]
        # held R: one draft
        click_text("A or B for the empty state?")
        wait_until(lambda: backend.mailState == "ready", 8, "the third mail")
        list_view.forceActiveFocus()
        held(Qt.Key.Key_R, "r")
        wait_until(lambda: backend.draft is not None, 8, "the draft")
        spin(400)
        out["drafts_after_held_r"] = len(lb.ask("list", view="drafts"))
        key(Qt.Key.Key_Escape)
        # a word typed to look for, with a mail open and the keyboard on the list: it goes to the search
        click_text("Brightline Billing")
        wait_until(lambda: backend.mailState == "ready", 8, "the mail again")
        list_view.forceActiveFocus()
        before = folders()
        drafts_before = len(lb.ask("list", view="drafts"))
        type_text("invoice")
        out["search"] = find("searchField").property("text")
        out["backend_search"] = backend.searchText
        out["unchanged"] = folders() == before and len(lb.ask("list", view="drafts")) == drafts_before
        # with no mail open the letters of the shortcuts are letters
        key(Qt.Key.Key_Escape)
        backend.closeMail()
        backend.search("")
        list_view.forceActiveFocus()
        spin(100)
        type_text("ar")
        out["search_ar"] = find("searchField").property("text")
        out["unchanged_ar"] = folders() == before
        out["drafts_end"] = len(lb.ask("list", view="drafts"))
    """, timeout=150)
    start = out["start"]
    assert out["after_repeats_only"] == [True, True, ""]
    assert out["after_held_a"] == sorted(start + ["archive"]) and out["opened_after_a"] is False
    assert out["after_held_hash"] == [sorted(start + ["archive"]), "trash", "inbox"]
    assert out["drafts_after_held_r"] == 1
    assert out["search"] == "invoice" and out["backend_search"] == "invoice" and out["unchanged"] is True
    assert out["search_ar"] == "ar" and out["unchanged_ar"] is True and out["drafts_end"] == 1


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
        # a selection is a wash of the ink and not of the accent, and the words are in the window's own font
        key(Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        spin(200)
        out["selected"] = ev(find("bodyEditor"), "field.selectedText")[0]
        out["font"] = ev(find("bodyEditor"), "field.font.family")[0]
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
    assert out["selected"] == "The 14th works." and out["font"] == "Inter"
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
        re.search(r"(onTapped|Keys\.on\w+Pressed)\s*:", bar[max(0, c - 60):c]) for c in callers)
    # not by name either: assistive technology that presses buttons without a key or a click is not let press Send
    assert not re.search(r"onPressAction", code["SendBar.qml"])
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

"""The Mail window's backend (share/apps/mail/app.py) against the real mail service on the fake engine.

A `Lab` runs a `Service` with a `FakeEngine` (sample mailboxes, a hostile mail among them) in a thread of its
own, in a temp home, next to a small fake agentd on a socket of its own. The backend runs on this thread's Qt event
loop, the way the window runs it, and is driven through the slots QML calls. Nothing here starts Thunderbird, asks a
nameserver, reaches past loopback or leaves the temp dir; every process the test makes is gone at its end.

What is held to what, in the order of the file: the words the window uses, the lists and views, one mail and what
can be done with it, the reply box and the draft the service keeps for it, the Send gate (every condition that
shuts it), the press (one line, exactly, and never without a click) and what its answer does, a service or agentd
that goes away and comes back, what other programs ask the window to show, and the limits of a line in both
directions.
"""

import asyncio
import concurrent.futures
import json
import os
import re
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mail_app_lab import (
    APP_PY,
    HOSTILE,
    INVOICE,
    LAUNCH,
    LEO,
    NEWS,
    PHOTOS,
    SAM,
    UNKNOWN_LINE,
    Lab,
    load_app,
    scratch_env,
    spin,
    wait_until,
)
from PySide6.QtGui import QGuiApplication


@pytest.fixture(scope="module")
def qt():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture(autouse=True)
def places(home, monkeypatch):
    """Every path the service uses is in the temp home, and nothing may ask a real nameserver."""
    scratch_env(home, monkeypatch.setenv)
    monkeypatch.delenv("BOMBADIL_CHECK", raising=False)
    monkeypatch.delenv("BOMBADIL_MAIL_SAMPLE", raising=False)


@pytest.fixture
def lab(home, qt):
    made: list[Lab] = []
    backends: list = []

    def make(**kw):
        lb = Lab(home, **kw).start()
        made.append(lb)
        lb.backends = backends
        return lb
    yield make
    for b in backends:
        b.stop()
    spin(30)
    for lb in made:
        lb.close()


class Calls(list):
    """What the backend asked the service, in order: (op, args)."""

    def ops(self, op: str) -> list[dict]:
        return [a for o, a in self if o == op]


@pytest.fixture
def window(lab, monkeypatch):
    """A Backend, started after the lab: `window(lb)` returns it with its record of requests and the processes it
    would have started. The clocks that make a person wait are shortened; the one that makes typing wait
    (DEBOUNCE_MS) is not."""
    def make(lb, *, wait: bool = True, **consts):
        mod = load_app()
        mod.RECONNECT_MS = consts.pop("RECONNECT_MS", 100)
        mod.SETTLE_MS = consts.pop("SETTLE_MS", 80)
        for k, v in consts.items():
            setattr(mod, k, v)
        started: list[tuple] = []
        monkeypatch.setattr(mod.Backend, "_start", lambda self, prog, args: started.append((prog, args)) or True)
        calls = Calls()
        real = mod.Backend._request

        def logged(self, op, on=None, timeout=mod.REQUEST_S, slow=False, **args):
            calls.append((op, dict(args)))
            return real(self, op, on=on, timeout=timeout, slow=slow, **args)
        monkeypatch.setattr(mod.Backend, "_request", logged)
        b = mod.Backend()
        b.mod, b.calls, b.started, b.shows = mod, calls, started, []
        b.showRequested.connect(lambda: b.shows.append(time.monotonic()))
        lb.backends.append(b)
        if wait:
            wait_until(lambda: b.connected and b.ready and b.listState != "loading", what="the window to load")
        return b
    return make


def rows(b) -> list[str]:
    return [m["id"] for m in b.messages]


def reply_open(lab, b, mail=LAUNCH, kind="reply"):
    """Open a mail and make the reply box for it; returns the draft once the box is up."""
    b.openMail(mail)
    wait_until(lambda: b.mailState == "ready", what="the mail to open")
    b.reply(kind)
    wait_until(lambda: b.draft is not None, what="the draft to open")
    return b.draft


def shown(b) -> bool:
    return b.canSend


def settle_box(b):
    """What QML does once it has drawn the draft: report it, and wait for the service's yes."""
    wait_until(lambda: not b._pending_edits(), what="the edits to be saved")
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b.canSend, what="Send to open")


# ---- the words the window uses ------------------------------------------------------------------------------------

def test_words(qt):
    app = load_app()
    assert app.addr_label({"name": "Priya Shah", "email": "priya@acme.example"}) == "Priya Shah"
    assert app.addr_label({"name": "", "email": "priya@acme.example"}) == "priya@acme.example"
    assert app.addr_full({"name": "Priya Shah", "email": "p@acme.example"}) == "Priya Shah <p@acme.example>"
    assert app.addr_full({"name": 'Shah, "P"', "email": "p@acme.example"}) == '"Shah, \\"P\\"" <p@acme.example>'
    assert app.addr_full({"name": "", "email": "p@acme.example"}) == "p@acme.example"
    assert app.addr_full(None) == ""
    # a stranger's name cannot turn the line around or hide where it ends
    assert app._line("fdp\u202e.exe\u200b\x00 \n  twice") == "fdp.exe twice"
    assert app._text("a\u202eb\r\nc\td\x07") == "ab\nc\td"
    now = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))
    assert app.when_label(now - 3600, now) == "11:00"
    assert app.when_label(now - 2 * 86400, now) in ("Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon")
    assert app.when_label(now - 30 * 86400, now) == "1 Sep"
    assert app.when_label(now - 400 * 86400, now).endswith("2025")
    assert app.when_label("junk") == "" and app.when_label(None) == ""
    assert app.size_label(512) == "512 B" and app.size_label(182000) == "182 KB" and app.size_label(2_500_000) == "2.5 MB"
    assert app.size_label("x") == ""
    assert app.safe_url("https://mail.google.com/mail/u/0/#inbox/abc")
    for bad in ("http://mail.google.com/", "javascript:alert(1)", "file:///etc/passwd", "https://a b", "https://x\n.y/",
                "", None, 7, "https://", "https://e.example/\x00"):
        assert not app.safe_url(bad), bad
    # the service normalises what is typed; a field it would not change is not an edit
    assert app._norm("to", "Priya <P@acme.example>, p@acme.example") == ["p@acme.example"]
    assert app._norm("subject", "  Re:   hi ") == "Re: hi"
    assert app._norm("body", "a\x00b") == "ab"


def test_the_app_is_only_a_window_of_the_service(qt):
    """It reads and writes nothing of its own and imports nothing from src/bombadil: the socket is the whole of it."""
    src = APP_PY.read_text()
    assert not re.search(r"^\s*(from|import)\s+bombadil", src, re.MULTILINE)
    for forbidden in (r"\bopen\(", r"write_text", r"write_bytes", r"QFile", r"QSaveFile", r"QSettings", r"sqlite3",
                      r"logging", r"subprocess", r"os\.system", r"shutil\.(copy|move|rmtree)"):
        assert not re.search(forbidden, src), forbidden
    # the one line that says "press" to agentd, and it is in `press`
    presses = [m.start() for m in re.finditer(r'"type": "press"', src)]
    assert len(presses) == 1
    head = src[:presses[0]]
    assert head.rfind("    def ") > 0 and re.search(r"def press\(self\)", head[head.rfind("    def "):])
    assert src.count("self._agent.send(") == 1 and "def press" in src[:src.index("self._agent.send(")]
    assert not re.search(r"\bprint\(", src.replace('print(f"mail: {type(e).__name__} while handling an answer", flush=True)', ""))


# ---- the lists and the views --------------------------------------------------------------------------------------

def test_lists_views_and_accounts(lab, window):
    lb = lab()
    b = window(lb)
    side = {r["id"]: r for r in b.sidebar}
    assert [r["id"] for r in b.sidebar] == ["all", "acct:a1", "acct:a2", "acct:a3", "needs_reply", "drafts"]
    assert side["all"]["name"] == "All inboxes" and b.unreadText == f"{side['all']['count']} unread"
    assert side["acct:a1"]["name"] == "maya@acme.example" and side["acct:a1"]["count"] > 0
    # an account that cannot be read says so quietly, and has a way to the web
    blocked = side["acct:a3"]
    assert blocked["state"] == "blocked" and "admin" in blocked["note"] and blocked["count"] == 0
    assert blocked["web"] == {"name": "Outlook", "url": "https://outlook.office.com/mail/"}
    assert side["needs_reply"]["name"] == "Needs a reply" and side["drafts"]["name"] == "Drafts"
    assert b.view == "all" and b.viewName == "All inboxes" and b.listState == "ready"
    # the inbox of every readable account, newest first, with the blocked one named as not read
    ids = rows(b)
    assert LAUNCH in ids and PHOTOS in ids and "a1/sent-launch-copy@acme.example" not in ids
    stamps = [m["ts"] for m in b.messages]
    assert stamps == sorted(stamps, reverse=True)
    assert [s["account"] for s in b.skipped] == ["a3"] and b.skipped[0]["email"] == "maya.reyes@lakeside.example"
    assert b.skipped[0]["web"]["url"].startswith("https://outlook.office.com/")
    launch = next(m for m in b.messages if m["id"] == LAUNCH)
    assert launch["sender"] == "Priya Shah" and launch["address"] == "priya@acme.example"
    assert launch["subject"] == "Launch date by noon?" and launch["unread"] is True and launch["attachments"] is False
    assert launch["when"] and launch["needsReply"] is False
    assert next(m for m in b.messages if m["id"] == INVOICE)["attachments"] is True
    # one account's view
    b.setView("acct:a2")
    wait_until(lambda: b.listState == "ready" and rows(b) and all(i.startswith("a2/") for i in rows(b)))
    assert b.view == "acct:a2" and b.viewName == "maya.reyes@gmail.example" and PHOTOS in rows(b)
    assert b.skipped == []
    b.setView("nonsense")
    assert b.view == "acct:a2"


def test_the_empty_views_and_a_search(lab, window):
    lb = lab()
    b = window(lb)
    b.setView("needs_reply")
    wait_until(lambda: b.view == "needs_reply" and b.listState == "ready")
    assert b.messages == []
    b.setView("drafts")
    wait_until(lambda: b.view == "drafts" and b.listState == "ready")
    assert b.messages == []
    # the sidebar counts what the service counts
    lb.ask("mark_reply", id=SAM, needs=True, why="Asks for a yes on the pricing copy.")
    lb.ask("mark_reply", id=LEO, needs=True, why="Blocked until you pick A or B.")
    wait_until(lambda: next(r for r in b.sidebar if r["id"] == "needs_reply")["count"] == 2, what="the count")
    b.setView("needs_reply")
    wait_until(lambda: set(rows(b)) == {SAM, LEO})
    assert {m["id"]: m["why"] for m in b.messages}[SAM] == "Asks for a yes on the pricing copy."
    # a search of Needs a reply is over its rows, in the window
    b.search("pricing")
    wait_until(lambda: rows(b) == [SAM])
    b.search("blocked until")
    wait_until(lambda: rows(b) == [LEO], what="a search of the why")
    b.search("nothing like this")
    wait_until(lambda: rows(b) == [])
    assert b.searchText == "nothing like this"
    b.search("")
    wait_until(lambda: set(rows(b)) == {SAM, LEO})
    # a search of a mailbox goes to the service, after a pause, once
    b.setView("all")
    wait_until(lambda: b.view == "all" and b.listState == "ready" and len(b.messages) > 3)
    before = len(b.calls.ops("search"))
    for text in ("i", "in", "inv", "invoice"):
        b.search(text)
    wait_until(lambda: rows(b) == [INVOICE], what="the search result")
    assert len(b.calls.ops("search")) == before + 1 and b.calls.ops("search")[-1]["text"] == "invoice"
    b.setView("acct:a2")           # a new view starts with no search
    wait_until(lambda: b.view == "acct:a2")
    assert b.searchText == ""


# ---- one mail ---------------------------------------------------------------------------------------------------

def test_opening_a_mail_leaves_it_unread(lab, window):
    lb = lab()
    b = window(lb)
    b.openMail(LAUNCH)
    assert b.mailState == "loading" and b.opened["id"] == LAUNCH and b.opened["subject"] == "Launch date by noon?"
    wait_until(lambda: b.mailState == "ready")
    assert "Legal is asking for the launch date" in b.mail["text"]
    assert b.opened["from"] == "Priya Shah <priya@acme.example>" and b.opened["to"] == "maya@acme.example"
    assert b.opened["full_when"] and b.opened["unread"] is True
    assert b.mail["webUrl"].startswith("https://mail.google.com/") and b.mail["webName"] == "Gmail"
    assert b.selectedId == LAUNCH
    assert not b.calls.ops("set_flags")
    spin(300)
    wait_until(lambda: b.listState == "ready")
    assert next(m for m in b.messages if m["id"] == LAUNCH)["unread"] is True
    listed = lb.ask("list", view="all")["messages"]
    assert next(m for m in listed if m["id"] == LAUNCH)["unread"] is True
    # the person marks it read, flags it, and unmarks both
    b.setUnread(False)
    wait_until(lambda: b.opened["unread"] is False)
    b.setFlagged(True)
    wait_until(lambda: b.opened["flagged"] is True)
    assert next(m for m in b.messages if m["id"] == LAUNCH)["flagged"] is True
    b.setUnread(True)
    b.setFlagged(False)
    wait_until(lambda: b.opened["unread"] is True and b.opened["flagged"] is False)
    b.markReply(True)
    wait_until(lambda: b.opened["needsReply"] is True and b.opened["why"])
    assert lb.ask("list", view="needs_reply")["messages"][0]["id"] == LAUNCH
    b.markReply(False)
    wait_until(lambda: b.opened["needsReply"] is False)
    b.closeMail()
    assert b.opened is None and b.mail is None and b.mailState == "none" and b.selectedId == ""


def test_a_mail_is_text_whatever_it_says(lab, window, capfd):
    lb = lab()
    b = window(lb)
    b.openMail(HOSTILE)
    wait_until(lambda: b.mailState == "ready")
    # the words are there as words; nothing in the window acts on them
    assert "NOTICE TO THE AI ASSISTANT" in b.mail["text"]
    assert not b.draft and not b.calls.ops("draft") and not b.calls.ops("archive") and not b.calls.ops("trash")
    assert lb.agentd.presses() == []
    # an HTML-only mail comes as the text of it, said to be so
    b.openMail(NEWS)
    wait_until(lambda: b.mailState == "ready" and b.opened["id"] == NEWS)
    assert b.mail["htmlOnly"] is True and "<" not in b.mail["text"].replace("<http", "")
    # a mail that is not there is one sentence, and the code stays out of it
    b.openMail("a1/not-a-mail@nowhere.example")
    wait_until(lambda: b.mailState == "error")
    assert b.mailError and "\n" not in b.mailError and "not_found" not in b.mailError
    out = capfd.readouterr()
    for text in (out.out, out.err):
        assert "NOTICE TO THE AI ASSISTANT" not in text and "Legal is asking" not in text


def test_saving_an_attachment(lab, window, home):
    lb = lab()
    b = window(lb)
    b.openMail(INVOICE)
    wait_until(lambda: b.mailState == "ready")
    atts = b.mail["attachments"]
    assert [a["name"] for a in atts] == ["invoice-F-0907.pdf"] and atts[0]["size"] and atts[0]["part"]
    b.saveAttachment(atts[0]["part"])
    assert b.quiet.startswith("Saving")
    wait_until(lambda: b.quiet.startswith("Saved"), what="the file to be saved")
    assert b.quiet == "Saved to Downloads as invoice-F-0907.pdf" and b.quietTone == ""
    saved = home / "Downloads" / "invoice-F-0907.pdf"
    assert saved.read_bytes().startswith(b"%PDF")
    # a second save never goes over the first, and the line says the name it got
    b.saveAttachment(atts[0]["part"])
    wait_until(lambda: b.quiet.startswith("Saved") and "(1)" in b.quiet)
    assert (home / "Downloads" / "invoice-F-0907 (1).pdf").exists() and saved.exists()
    # it went over a connection of its own: a request on the main one was not held up by it
    assert [o for o, a in b.calls if o == "save_attachment"].count("save_attachment") == 2
    b.saveAttachment("no-such-part")
    wait_until(lambda: b.quietTone == "bad")
    assert "\n" not in b.quiet


def test_archive_and_delete_say_what_they_did_and_open_nothing_else(lab, window):
    """The next mail is not opened by itself: a second press of the same key would put away a mail that was never
    looked at. What was put away is said, and a request that is already out is not made twice."""
    lb = lab()
    b = window(lb)
    unread = [m["id"] for m in b.messages if m["unread"]]
    assert len(unread) >= 3
    first = unread[0]
    subject = next(m["subject"] for m in b.messages if m["id"] == first)
    b.openMail(first)
    wait_until(lambda: b.mailState == "ready")
    b.archive()
    b.archive()                                        # a held key: one request
    wait_until(lambda: first not in rows(b) and b.selectedId == "", what="the mail to go")
    assert len(b.calls.ops("archive")) == 1
    assert b.quiet == f"Archived: {subject}" and b.quietTone == "" and b.opened is None and b.mail is None
    assert lb.ask("read", id=first)["message"]["folder"] == "archive"
    spin(300)
    assert b.opened is None and b.selectedId == ""     # nothing else was opened for the person
    nxt = unread[1]
    b.openMail(nxt)
    wait_until(lambda: b.mailState == "ready" and b.opened["id"] == nxt)
    b.trash()
    wait_until(lambda: nxt not in rows(b))
    assert lb.ask("read", id=nxt)["message"]["folder"] == "trash"
    assert b.quiet.startswith("Moved to Trash: ") and b.opened is None
    assert lb.agentd.presses() == []
    # with the keyboard's own id as the argument too
    other = next(i for i in rows(b) if i not in (first, nxt))
    b.archive(other)
    wait_until(lambda: other not in rows(b))
    # a refusal is said as it is and the mail stays
    lb.engine.set_up(False)
    wait_until(lambda: b.engineNote != "")
    keep = rows(b)[0]
    b.archive(keep)
    wait_until(lambda: b.quietTone == "bad")
    assert keep in rows(b)


def test_links_go_to_the_browser_only_when_they_are_web_links(lab, window):
    lb = lab()
    b = window(lb)
    assert b.openWeb("https://mail.google.com/mail/u/maya@acme.example/#inbox/1") is True
    prog, args = b.started[-1]
    assert prog.endswith("bombadil") and args == ["open", "https://mail.google.com/mail/u/maya@acme.example/#inbox/1"]
    n = len(b.started)
    for bad in ("javascript:alert(1)", "file:///home/x/.ssh/id_rsa", "http://insecure.example/", "https://a b.example/",
                "https://x.example/\n--help", "-n", ""):
        assert b.openWeb(bad) is False
    assert len(b.started) == n and b.quietTone == "bad"


# ---- the reply box and the draft the service keeps for it -----------------------------------------------------------

def test_reply_makes_the_persons_draft_and_opens_it(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    assert d["kind"] == "reply" and d["reply_to"] == LAUNCH and d["created_by"] == "person"
    assert [a["email"] for a in d["to"]] == ["priya@acme.example"] and d["from"]["email"] == "maya@acme.example"
    assert d["subject"].startswith("Re: ") and b.draftKind == "Reply" and b.draftFrom.startswith("Maya Reyes <")
    assert b.draftFields["to"] == "Priya Shah <priya@acme.example>" and b.draftFields["subject"] == d["subject"]
    # the window asked for a draft as the person: it set no author, no "typed" and no "tainted" of its own
    made = b.calls.ops("draft")
    assert len(made) == 1 and made[0] == {"kind": "reply", "reply_to": LAUNCH}
    assert lb.draft_of(d["id"])["created_by"] == "person"
    # asking again goes on with the draft there is, instead of making a second
    b.reply("reply")
    spin(400)
    assert len(b.calls.ops("draft")) == 1 and b.draft["id"] == d["id"]
    assert len(lb.ask("list", view="drafts")) == 1
    # reply to all and forward are drafts of their own
    b.reply("reply_all")
    wait_until(lambda: b.draft and b.draft["kind"] == "reply_all", what="reply all")
    assert b.draftKind == "Reply to all"
    b.reply("forward")
    wait_until(lambda: b.draft and b.draft["kind"] == "forward", what="forward")
    assert b.draftKind == "Forward" and b.draft["to"] == []
    assert len(lb.ask("list", view="drafts")) == 3
    b.reply("nonsense")
    spin(100)
    assert b.draft["kind"] == "forward"


def test_what_is_typed_is_one_edit_after_a_pause_and_then_shown(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    first_fp = b.draft["fingerprint"]
    edits = len(b.calls.ops("draft_edit"))
    # keys at 100 ms apart are one edit, with the last text, a pause after the last key
    t0 = time.monotonic()
    for text in ("Th", "Thanks", "Thanks, the 14th works."):
        b.editField("body", text)
        spin(100)
    assert len(b.calls.ops("draft_edit")) == edits, "an edit went before the pause"
    assert b.canSend is False and b.sendNote == "Saving"
    wait_until(lambda: len(b.calls.ops("draft_edit")) == edits + 1, what="the edit")
    assert time.monotonic() - t0 >= 0.4
    assert b.calls.ops("draft_edit")[-1] == {"id": d["id"], "body": "Thanks, the 14th works."}
    wait_until(lambda: b.draft["fingerprint"] != first_fp, what="the answer")
    new_fp = b.draft["fingerprint"]
    # the answer's fingerprint is not shown until the box says it drew it, and then the service has it
    assert b.canSend is False and b.sendNote == "Waiting for Mail to confirm what you see."
    assert lb.draft_of(d["id"])["body"] == "Thanks, the 14th works."
    b.reportShown(d["id"], first_fp)             # the old one: nothing
    spin(200)
    assert b.canSend is False and not [a for a in b.calls.ops("draft_shown") if a["fingerprint"] == first_fp][1:]
    b.reportShown(d["id"], new_fp)
    wait_until(lambda: b.canSend, what="Send to open")
    assert b.calls.ops("draft_shown")[-1] == {"id": d["id"], "fingerprint": new_fp}
    # said once for a draft that has not changed
    n = len(b.calls.ops("draft_shown"))
    b.reportShown(d["id"], new_fp)
    b.reportShown(d["id"], new_fp)
    spin(150)
    assert len(b.calls.ops("draft_shown")) == n
    # a field is its own request, and the service's refusal of one is said under it
    b.editField("to", "not an address at all <<<")
    b.editField("subject", "Re: the date")
    wait_until(lambda: b.fieldErrors.get("to"), what="the refusal")
    assert "\n" not in b.fieldErrors["to"] and b.canSend is False and b.sendNote == b.fieldErrors["to"]
    wait_until(lambda: lb.draft_of(d["id"])["subject"] == "Re: the date")
    b.editField("to", "priya@acme.example")
    wait_until(lambda: not b.fieldErrors, what="the error to clear")
    assert [a["email"] for a in b.draft["to"]] == ["priya@acme.example"]


def test_an_edit_made_elsewhere_is_taken_and_has_to_be_shown_again(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    fp = b.draft["fingerprint"]
    # the agent (a process in a turn) changes the body; the service tells the window and the box takes it
    lb.ask("draft_edit", agent=True, id=d["id"], body="Agent's words.", tainted=True)
    wait_until(lambda: b.draft["fingerprint"] != fp, what="the box to take the new draft")
    assert b.draft["body"] == "Agent's words." and b.draftFields["body"] == "Agent's words."
    assert b.canSend is False and b.sendNote == "Waiting for Mail to confirm what you see."
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b.canSend)
    # what the person has typed since is theirs: it is not taken from them
    b.editField("body", "Mine.")
    lb.ask("draft_edit", agent=True, id=d["id"], subject="Agent subject", tainted=True)
    wait_until(lambda: b.draft["subject"] == "Agent subject")
    wait_until(lambda: b.draft["body"] == "Mine.")
    assert b.draftFields["body"] == "Mine." and b.draftFields["subject"] == "Agent subject"


def test_attachments_are_added_and_taken_off(lab, window, home):
    lb = lab()
    b = window(lb)
    reply_open(lab, b)
    settle_box(b)
    doc = home / "docs" / "launch-plan.txt"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("the plan\n")
    fp = b.draft["fingerprint"]
    b.addFiles([doc.as_uri()])
    wait_until(lambda: b.draft["attachments"], what="the file")
    assert [a["name"] for a in b.draft["attachments"]] == ["launch-plan.txt"] and b.draft["fingerprint"] != fp
    assert b.canSend is False
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b.canSend)
    b.addFiles([str(home / "docs" / "missing.txt")])
    wait_until(lambda: b.attachError, what="the refusal")
    assert "\n" not in b.attachError and len(b.draft["attachments"]) == 1
    b.removeAttachment("launch-plan.txt")
    wait_until(lambda: not b.draft["attachments"])
    assert b.canSend is False and b.attachError == ""


def test_drafts_view_lists_both_makers_and_discard(lab, window):
    lb = lab()
    b = window(lb)
    mine = lb.ask("draft", kind="reply", reply_to=SAM, body="Yes.")
    agents = lb.ask("draft", agent=True, kind="reply", reply_to=LEO, body="A, I think.", tainted=False)
    assert agents["created_by"] == "agent" and mine["created_by"] == "person"
    wait_until(lambda: next(r for r in b.sidebar if r["id"] == "drafts")["count"] == 2, what="the count")
    b.setView("drafts")
    wait_until(lambda: len(b.messages) == 2)
    byid = {m["id"]: m for m in b.messages}
    assert byid[agents["id"]]["why"] == "Written by Bombadil" and byid[mine["id"]]["why"] is None
    assert all(m["draft"] for m in b.messages) and byid[mine["id"]]["sender"] == "Sam Ortiz"
    # either opens in the same box, with the mail it answers above it
    b.openDraft(agents["id"])
    wait_until(lambda: b.draft and b.draft["id"] == agents["id"])
    assert b.selectedId == agents["id"] and b.draftFields["body"] == "A, I think."
    wait_until(lambda: b.opened and b.opened["id"] == LEO and b.mailState == "ready")
    b.openDraft(mine["id"])
    wait_until(lambda: b.draft and b.draft["id"] == mine["id"])
    wait_until(lambda: b.opened and b.opened["id"] == SAM)
    # discard takes it out of the list and the count, and the box away
    b.discardDraft()
    wait_until(lambda: b.draft is None and rows(b) == [agents["id"]], what="the draft to go")
    assert lb.ask("draft_get", id=mine["id"])["state"] == "discarded"
    wait_until(lambda: next(r for r in b.sidebar if r["id"] == "drafts")["count"] == 1)
    # leaving the box keeps the draft, with what was typed in it
    b.openDraft(agents["id"])
    wait_until(lambda: b.draft and b.draft["id"] == agents["id"])
    b.editField("body", "B, then.")
    b.closeDraft()
    assert b.draft is None
    wait_until(lambda: lb.draft_of(agents["id"])["body"] == "B, then.", what="the words to be kept")
    assert lb.draft_of(agents["id"])["state"] == "open"


# ---- the Send gate -----------------------------------------------------------------------------------------------

def test_send_opens_only_when_everything_is_true(lab, window):
    lb = lab()
    b = window(lb)
    # no draft
    assert b.canSend is False and b.sendNote == "There is no draft."
    d = reply_open(lab, b)
    # drawn is not enough: the service has to have been told which draft was drawn
    assert b.draft["fingerprint"] and b.canSend is False
    assert b.sendNote == "Waiting for Mail to confirm what you see."
    b.press()                                       # a press that the gate refuses writes nothing
    spin(200)
    assert lb.agentd.presses() == []
    settle_box(b)
    assert b.canSend is True and b.sendNote == "" and b.sendLabel == "Send" and b.pressLine == ""
    # a key typed: not pressable until the edit is saved and the new draft is drawn
    b.editField("subject", "Re: a new subject")
    assert b.canSend is False and b.sendNote == "Saving"
    wait_until(lambda: b.sendNote == "Waiting for Mail to confirm what you see.", what="the edit to be answered")
    b.reportShown(d["id"], b.draft["fingerprint"])
    wait_until(lambda: b.canSend)
    # agentd is not there
    lb.stop_agentd()
    wait_until(lambda: not b.agentConnected)
    assert b.canSend is False and b.sendNote == "Not connected"
    b.press()
    spin(100)
    assert lb.agentd.presses() == []
    lb.start_agentd()
    wait_until(lambda: b.agentConnected and b.canSend, what="agentd to be found again")
    # the service is not there
    lb.stop_service()
    wait_until(lambda: not b.connected)
    assert b.canSend is False and b.sendNote == "Mail is not running yet."
    b.press()
    spin(100)
    assert lb.agentd.presses() == []
    lb.run(lb._start_service())
    wait_until(lambda: b.connected and b.draft is not None)
    # a restarted service is asked again what the draft is, and the box has to show it again: until the box says it
    # has, what the service was last told is not taken for what is on screen now
    wait_until(lambda: b.sendNote == "Waiting for Mail to confirm what you see.", what="the draft to be shown again")
    assert b.canSend is False and b.calls.ops("draft_get")
    shown_before = len(b.calls.ops("draft_shown"))
    settle_box(b)
    assert len(b.calls.ops("draft_shown")) == shown_before + 1
    assert lb.agentd.presses() == []


def test_a_draft_with_nobody_to_send_to_cannot_be_sent(lab, window):
    lb = lab()
    b = window(lb)
    b.openMail(LAUNCH)
    wait_until(lambda: b.mailState == "ready")
    b.reply("forward")
    wait_until(lambda: b.draft is not None)
    wait_until(lambda: not b._pending_edits())
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b._shown == b.draft["fingerprint"])
    assert b.canSend is False and b.sendNote == "Add who it is going to."
    b.editField("to", "Sam Ortiz <sam@acme.example>")
    wait_until(lambda: b.draft["to"] and not b._pending_edits())
    assert b.canSend is False
    settle_box(b)
    assert b.canSend is True
    b.editField("to", "")
    wait_until(lambda: not b.draft["to"] and not b._pending_edits())
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b._shown == b.draft["fingerprint"])
    assert b.canSend is False and b.sendNote == "Add who it is going to."
    assert lb.agentd.presses() == []


def test_a_draft_that_is_being_sent_is_not_pressable(lab, window):
    lb = lab()
    lb.engine.hang_send()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    # another press (a second window, a terminal) is in the service; this window sees the draft as sending
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        job = pool.submit(lb.ask, "send", id=d["id"], fingerprint=b.draft["fingerprint"])
        wait_until(lambda: b.draft["state"] == "sending", what="the draft to be seen sending")
        assert b.canSend is False and b.sendNote == "This is being sent."
        job.result(10)
    wait_until(lambda: b.draft["state"] == "unknown", what="the draft to be unknown")
    assert b.unknownOutcome is True and b.pressLine == UNKNOWN_LINE
    assert lb.agentd.presses() == []


# ---- the press --------------------------------------------------------------------------------------------------

def test_a_press_is_one_exact_line_and_a_receipt_ends_the_box(lab, window):
    lb = lab()
    lb.agentd.mode = "ok"
    lb.agentd.noise = [b'{"type": "event", "kind": "text", "text": "' + b"x" * (3 << 20) + b'"}',
                       b'{"type": "status", "busy": false}', b"not json at all", b'["a list"]']
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    fp = b.draft["fingerprint"]
    b.press()
    assert b.pressState == "sending" and b.sendLabel == "Sending" and b.canSend is False
    assert b.sendNote == "Waiting for Mail to say it went."
    b.press()                                       # a second press while one is out is not a second line
    wait_until(lambda: b.draft is None, what="the receipt")
    assert lb.agentd.presses() == [{"type": "press", "kind": "mail", "id": d["id"], "fingerprint": fp}]
    assert list(lb.agentd.presses()[0]) == ["type", "kind", "id", "fingerprint"]
    wanted = f'{{"type": "press", "kind": "mail", "id": "{d["id"]}", "fingerprint": "{fp}"}}\n'
    assert [raw for raw in lb.agentd.raw if b'"press"' in raw] == [wanted.encode()]
    assert b.receipt["line"] == "Sent to Priya from maya@acme.example · 09:08"
    assert b.receipt["webName"] == "Gmail" and b.receipt["webUrl"] == "https://mail.google.com/"
    assert b.pressState == "" and b.canSend is False
    # the next unread mail is open (the list is never left empty-handed), and the receipt stays under it
    unread = [m["id"] for m in b.messages if m["unread"] and m["id"] != LAUNCH]
    wait_until(lambda: b.selectedId in unread, what="the next unread mail")
    assert b.receipt is not None
    b.dismissReceipt()
    assert b.receipt is None


def test_the_whole_press_through_agentd_to_the_service_and_the_engine(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    b.editField("body", "The 14th works on my side.")
    settle_box(b)
    b.press()
    wait_until(lambda: b.draft is None and b.receipt is not None, what="the receipt")
    assert b.receipt["line"].startswith("Sent to Priya from maya@acme.example")
    assert b.receipt["webUrl"].startswith("https://")
    assert len(lb.agentd.presses()) == 1
    sent = lb.engine.sent
    assert len(sent) == 1 and sent[0]["body"].startswith("The 14th works on my side.")
    assert [a["email"] for a in sent[0]["to"]] == ["priya@acme.example"]
    assert lb.draft_of(d["id"])["state"] == "sent"
    # the mail that was answered no longer needs a reply; the sidebar and the list say so
    spin(400)
    assert all(not m["needsReply"] for m in b.messages) and b.draft is None
    wait_until(lambda: next(r for r in b.sidebar if r["id"] == "drafts")["count"] == 0)
    assert len(lb.agentd.presses()) == 1


def test_a_failed_press_says_agentds_words_and_keeps_the_draft(lab, window):
    lb = lab()
    lb.agentd.mode = "fail"
    lb.agentd.code, lb.agentd.sentence = "engine_down", "Thunderbird is not answering. Nothing was sent."
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    b.press()
    wait_until(lambda: b.pressLine, what="the sentence")
    assert b.pressLine == "Thunderbird is not answering. Nothing was sent." and b.pressState == ""
    assert b.draft["id"] == d["id"] and b.receipt is None and b.unknownOutcome is False
    assert b.canSend is True and b.sendLabel == "Send"     # it can be pressed again, by the person, and only so
    spin(500)
    assert len(lb.agentd.presses()) == 1
    # a draft that changed under the press is looked at again and has to be shown again
    lb.agentd.code, lb.agentd.sentence = "changed", "That draft changed. Look at it again."
    b.press()
    wait_until(lambda: b.pressLine == "That draft changed. Look at it again.")
    wait_until(lambda: b.canSend is False and b.sendNote == "Waiting for Mail to confirm what you see.")
    b.editField("body", "Now different.")
    assert b.canSend is False and b.sendNote == "Saving"
    wait_until(lambda: b.sendNote == "Waiting for Mail to confirm what you see.", what="the new draft")
    b.press()                                       # not shown yet: no press
    spin(200)
    assert len(lb.agentd.presses()) == 2


def test_an_unknown_outcome_needs_a_look_in_sent_and_then_says_again(lab, window):
    lb = lab()
    lb.engine.hang_send()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    fp = b.draft["fingerprint"]
    b.press()
    wait_until(lambda: b.unknownOutcome, 8.0, "the outcome to be unknown")
    assert b.pressLine == UNKNOWN_LINE and b.sendLabel == "Send" and b.looked is False
    assert b.draft["id"] == d["id"] and b.draft["state"] == "unknown"
    # the box draws it again, as it does with every answer, and the service is told
    b.reportShown(d["id"], b.draft["fingerprint"])
    wait_until(lambda: b._shown == b.draft["fingerprint"])
    assert b.canSend is False and b.sendNote == "Tick the box once you have looked in Sent."
    b.press()
    spin(150)
    assert len(lb.agentd.presses()) == 1
    b.setLooked(True)
    assert b.looked is True and b.sendLabel == "Send again" and b.canSend is True
    lb.engine.send_ok()
    b.press()
    wait_until(lambda: b.draft is None and b.receipt is not None, what="the receipt")
    first, second = lb.agentd.presses()
    assert first == {"type": "press", "kind": "mail", "id": d["id"], "fingerprint": fp}
    assert second == {"type": "press", "kind": "mail", "id": d["id"], "fingerprint": fp, "again": True}
    assert b.looked is False


def test_the_looked_tick_means_nothing_for_a_draft_that_is_not_unknown(lab, window):
    lb = lab()
    b = window(lb)
    reply_open(lab, b)
    settle_box(b)
    b.setLooked(True)
    assert b.looked is False and b.sendLabel == "Send"
    b.press()
    wait_until(lambda: b.draft is None or b.pressState == "")
    assert "again" not in lb.agentd.presses()[0]


def test_agentd_going_away_mid_press_is_settled_by_asking_the_service(lab, window):
    lb = lab()
    lb.agentd.mode = "hangup"
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    b.press()
    wait_until(lambda: b.pressState == "checking" or b.pressLine, what="the loss to be seen")
    assert b.canSend is False
    # nothing went: after a few looks the window says so, and Send is the person's again once agentd is back
    wait_until(lambda: b.pressLine.startswith("Nothing was sent"), 8.0, "the window to settle")
    wait_until(lambda: b.agentConnected and b.canSend, what="agentd to be found")
    assert b.draft["id"] == d["id"] and b.receipt is None and len(lb.agentd.presses()) == 1
    assert lb.draft_of(d["id"])["state"] == "open"
    # it went, and agentd was lost before it said so: the service knows, and the receipt is shown
    lb.agentd.mode = "service_then_hangup"
    b.press()
    wait_until(lambda: b.receipt is not None and b.draft is None, 8.0, "the receipt")
    assert len(lb.engine.sent) == 1 and len(lb.agentd.presses()) == 2


def test_a_press_agentd_never_answers_is_looked_into_by_asking_the_service(lab, window):
    lb = lab()
    lb.agentd.mode = "silent"          # agentd takes the press and says nothing, and stays connected
    b = window(lb, PRESS_S=0.2)
    d = reply_open(lab, b)
    settle_box(b)
    b.press()
    assert b.pressState == "sending" and b.canSend is False
    wait_until(lambda: b.pressLine.startswith("Nothing was sent"), 10.0, "the window to look into it")
    assert b.pressState == "" and b.draft["id"] == d["id"] and b.receipt is None
    assert len(lb.agentd.presses()) == 1 and lb.engine.sent == []
    wait_until(lambda: b.canSend, what="Send to be the person's again")


def test_a_request_on_a_connection_of_its_own_lets_go_of_it_when_the_service_is_not_there(lab, window):
    lb = lab()
    b = window(lb)
    b.openMail(INVOICE)
    wait_until(lambda: b.mailState == "ready")
    part = b.mail["attachments"][0]["part"]
    lb.stop_service()
    wait_until(lambda: not b.connected, what="the loss to be seen")
    b.saveAttachment(part)
    wait_until(lambda: b.quietTone == "bad", 5.0, "the failure to be said at once, not after the long wait")
    assert b.quiet == "Mail is not running yet."
    assert not b._slow and not b._slow_of and not b._pending


def test_a_sent_push_ends_the_box_once_whichever_comes_first(lab, window):
    lb = lab()
    lb.agentd.mode = "silent"
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    b.press()
    assert b.pressState == "sending"
    receipts = []
    b.receiptChanged.connect(lambda: receipts.append(b.receipt))
    # the service's own answer to the same press, as another client of it would make it
    assert "receipt" in lb.ask("send", id=d["id"], fingerprint=b.draft["fingerprint"])
    wait_until(lambda: b.draft is None and b.receipt is not None, what="the sent push")
    assert b.pressState == "" and b.receipt["line"].startswith("Sent to")
    spin(300)
    assert len(lb.engine.sent) == 1 and len({json.dumps(r, sort_keys=True) for r in receipts if r is not None}) == 1


# ---- a service, an engine or agentd that goes away ------------------------------------------------------------------

def test_the_window_finds_the_service_again_after_it_restarts(lab, window):
    lb = lab()
    b = window(lb)
    wait_until(lambda: len(b.messages) > 3)
    before = rows(b)
    d = reply_open(lab, b)
    settle_box(b)
    b.editField("body", "Typed before it went away.")
    wait_until(lambda: not b._pending_edits())
    lb.stop_service()
    wait_until(lambda: not b.connected, what="the loss to be seen")
    # what was loaded stays readable, with a quiet line saying why
    assert rows(b) == before and b.ready and b.engineNote == "Mail is not running yet. This reconnects by itself."
    assert b.canSend is False and b.opened["id"] == LAUNCH
    b.search("anything")                           # asked while it is away: an answer that says so, not a hang
    spin(200)
    b.search("")
    lb.run(lb._start_service())
    wait_until(lambda: b.connected and b.listState == "ready", 8.0, "the window to reconnect")
    assert b.engineNote == "" and len(b.calls.ops("subscribe")) == 2
    assert len(b.calls.ops("requested")) == 1           # asked once, at the start
    assert rows(b) == before
    # the draft is still there (the service keeps it) and is taken again as the service has it
    assert b.draft["id"] == d["id"] and b.draftFields["body"] == "Typed before it went away."
    settle_box(b)
    # and again when only the engine goes
    lb.engine.set_up(False)
    wait_until(lambda: b.engineNote != "", what="the engine's state")
    assert rows(b) == before and "\n" not in b.engineNote
    lb.engine.set_up(True)
    wait_until(lambda: b.engineNote == "", what="the engine to be back")


def test_a_list_that_cannot_be_read_is_one_plain_line(lab, window):
    lb = lab()
    b = window(lb)
    wait_until(lambda: len(b.messages) > 3)
    lb.engine.set_up(False)
    wait_until(lambda: b.engineNote != "")
    lists = len(b.calls.ops("list"))
    b.refresh()                                    # the list that is there stays, and the note above it says why:
    wait_until(lambda: len(b.calls.ops("list")) > lists)
    spin(300)
    assert b.quiet == "" and len(b.messages) > 3   # the same sentence is not also said at the foot
    b.setView("acct:a2")
    wait_until(lambda: b.listState == "error", what="the list's error")
    assert b.messages == [] and b.listError and "\n" not in b.listError
    for code in ("engine_down", "engine_error", "internal", "not_found"):
        assert code not in b.listError
    lb.engine.set_up(True)
    wait_until(lambda: b.engineNote == "")
    b.refresh()
    wait_until(lambda: b.listState == "ready" and b.messages, what="the list")


# ---- what other programs ask the window to show --------------------------------------------------------------------

def test_a_show_that_came_before_the_window_is_taken_once_without_raising_it(lab, window):
    lb = lab()
    lb.ask("mark_reply", id=SAM, needs=True, why="Asks for a yes.")
    lb.push_show(view="needs_reply", id=SAM)
    b = window(lb)
    wait_until(lambda: b.view == "needs_reply" and b.opened and b.opened["id"] == SAM and b.mailState == "ready")
    assert b.shows == [] and b.messages[0]["id"] == SAM
    assert len(b.calls.ops("requested")) == 1
    assert lb.ask("requested") is None


def test_a_show_push_switches_and_raises(lab, window):
    lb = lab()
    b = window(lb)
    d = lb.ask("draft", agent=True, kind="reply", reply_to=LEO, body="A.", tainted=False)
    lb.push_show(reply=d["id"])
    wait_until(lambda: b.draft and b.draft["id"] == d["id"], what="the draft to open")
    assert b.view == "drafts" and len(b.shows) == 1
    wait_until(lambda: b.opened and b.opened["id"] == LEO)
    lb.push_show(view="acct:a2")
    wait_until(lambda: b.view == "acct:a2" and b.draft is None)
    assert len(b.shows) == 2
    lb.push_show(id=PHOTOS)
    wait_until(lambda: b.opened and b.opened["id"] == PHOTOS)
    assert len(b.shows) == 3
    # a show that was already acted on (the same seq) is not acted on again, and one for nothing asks only to raise
    seq = b._show_seq + 1
    b._show({"seq": seq, "view": "needs_reply"})
    assert b.view == "needs_reply" and len(b.shows) == 4
    b.setView("all")
    b._show({"seq": seq, "view": "needs_reply"})
    assert b.view == "all" and len(b.shows) == 4
    b._show({"seq": seq + 1})
    assert len(b.shows) == 5


def test_new_mail_and_changes_refresh_the_list_in_a_burst(lab, window):
    lb = lab()
    b = window(lb)
    wait_until(lambda: len(b.messages) > 3)
    before = len(b.messages)
    lists = len(b.calls.ops("list"))
    for n in range(5):
        lb.engine.inject_new_mail("maya@acme.example", "Zed <zed@acme.example>", f"Zed's note {n}", "hello")
    wait_until(lambda: len(b.messages) == before + 5, what="the new mail")
    assert len(b.calls.ops("list")) - lists <= 3, "a burst of pushes is a few refreshes, not one each"
    assert b.messages[0]["subject"].startswith("Zed's note")


# ---- the first run and signing in -------------------------------------------------------------------------------

def test_first_run_one_address_then_signing_in_in_thunderbirds_window(lab, window):
    lb = lab(samples=False)
    b = window(lb)
    assert b.ready and b.noAccount is True and b.accounts == [] and b.engine == "off"
    assert [r["id"] for r in b.sidebar] == ["all", "needs_reply", "drafts"]
    b.addAccount("   ")
    spin(100)
    assert not b.calls.ops("add_account")
    b.addAccount("someone@workspace.example")
    assert b.adding is True
    wait_until(lambda: b.adding is False and b.accounts, 8.0, "the account")
    acct = b.accounts[0]
    assert acct["email"] == "someone@workspace.example" and acct["state"] == "signin"
    assert b.noAccount is True and b.signingIn["id"] == acct["id"] and b.addError == ""
    assert b.engineNote == ""                        # no account yet to read: the window says it in its own way
    # Thunderbird's window is brought up on request, and put away with Done
    assert b.staged is False
    b.showEngine()
    wait_until(lambda: b.staged, what="the engine's window")
    assert lb.process.calls[-1:] and "stage" in str(lb.process.calls[-1])
    b.hideEngine()
    wait_until(lambda: not b.staged)
    lb.engine.complete_signin(acct["email"])
    wait_until(lambda: b.noAccount is False and b.accounts[0]["state"] in ("ok", "syncing"), 8.0, "the sign-in to finish")
    assert b.sidebar[1]["kind"] == "account"
    # a refused address is one sentence, and the field stays as it was
    b.addAccount("not an address")
    wait_until(lambda: b.addError, what="the refusal")
    assert "\n" not in b.addError and "bad_request" not in b.addError and b.adding is False


def test_an_address_on_a_domain_with_no_mail_server_still_gets_an_account_to_try(lab, window):
    lb = lab(samples=False)
    b = window(lb)
    b.addAccount("priya@example.org")
    wait_until(lambda: b.accounts, 8.0, "the account")
    assert b.accounts[0]["email"] == "priya@example.org" and b.noAccount is False
    assert b.accounts[0]["state"] in ("syncing", "ok")


# ---- what the box shows is what the draft has --------------------------------------------------------------------------

def test_a_box_that_cannot_take_typing_never_shows_words_the_draft_does_not_hold(lab, window):
    """The draft is being sent (here by another client), so the box takes no typing; if some still reaches the
    backend the box is put back to what is there, and when the send fails and the draft is open again Send is for what
    the box shows."""
    from bombadil.mail import service
    lb = lab()
    lb.engine.send_delay = 1.2
    lb.engine.fail_send("engine_error", "The provider refused the message.")
    service.SEND_S = 5.0
    b = window(lb)
    d = reply_open(lab, b)
    b.editField("body", "The 14th works.")
    settle_box(b)
    loads: list[int] = []
    b.draftLoaded.connect(lambda: loads.append(1))
    assert b.editable is True
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        job = pool.submit(lb.ask, "send", id=d["id"], fingerprint=b.draft["fingerprint"])
        wait_until(lambda: b.draft["state"] == "sending", what="the draft to be seen sending")
        assert b.editable is False and b.canSend is False
        before = len(loads)
        b.editField("body", "EXTRA WORDS")
        spin(100)
        assert b.draftFields["body"] == "The 14th works." and len(loads) > before, "the box is put back"
        assert not b._pending_edits()
        assert job.result(10)["code"] == "engine_error"
    wait_until(lambda: b.draft["state"] == "open" and b.editable, what="the draft to be open again")
    assert b.draftFields["body"] == "The 14th works." and lb.draft_of(d["id"])["body"] == "The 14th works."
    settle_box(b)
    assert b.draft["body"] == b.draftFields["body"] == "The 14th works."
    b.editField("body", "Fine.")
    wait_until(lambda: lb.draft_of(d["id"])["body"] == "Fine.")
    # nor while a press of this window is out
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b.canSend)
    b.press()
    assert b.pressState == "sending" and b.editable is False
    b.editField("subject", "Not now")
    assert b._want["subject"] == b.draft["subject"] == lb.draft_of(d["id"])["subject"]


def test_a_second_unknown_outcome_asks_for_a_fresh_look_in_sent(lab, window):
    lb = lab()
    lb.engine.hang_send()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    b.press()
    wait_until(lambda: b.unknownOutcome, 8.0, "the first unknown outcome")
    b.reportShown(d["id"], b.draft["fingerprint"])
    wait_until(lambda: b._shown == b.draft["fingerprint"])
    b.setLooked(True)
    assert b.sendLabel == "Send again" and b.canSend is True
    b.press()                                        # again: the person looked, and pressed
    assert b.looked is False and b.sendLabel == "Sending"
    wait_until(lambda: b.pressState == "" and b.unknownOutcome, 8.0, "the second unknown outcome")
    b.reportShown(d["id"], b.draft["fingerprint"])
    wait_until(lambda: b._shown == b.draft["fingerprint"])
    assert b.looked is False and b.sendLabel == "Send" and b.canSend is False
    assert b.sendNote == "Tick the box once you have looked in Sent." and b.ticking is True
    b.press()
    spin(150)
    first, second = lb.agentd.presses()
    assert "again" not in first and second["again"] is True      # and no third press without a new look
    b.setLooked(True)
    assert b.canSend is True and b.ticking is False


def test_nothing_was_sent_is_said_only_when_the_service_says_so(lab, window):
    lb = lab()
    lb.agentd.mode = "hangup"
    b = window(lb, SETTLE_MS=500, SETTLE_TRIES=3, SETTLE_OPEN_TRIES=50)
    d = reply_open(lab, b)
    settle_box(b)
    b.press()
    lb.stop_service()                                # the press went nowhere and the service is not there to say so
    wait_until(lambda: b.pressState == "" and b.pressLine, 8.0, "the window to give up looking")
    assert b.pressLine == UNKNOWN_LINE and "Nothing was sent" not in b.pressLine
    assert b.canSend is False
    lb.run(lb._start_service())
    wait_until(lambda: b.connected, what="the service")
    # the service answers that the draft is open: now it is known that nothing went
    wait_until(lambda: b.pressLine.startswith("Nothing was sent"), 8.0, "the service's word")
    # the draft then goes out through another window and is not known to have gone: one sentence, not two
    lb.engine.hang_send()
    settle_box(b)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        job = pool.submit(lb.ask, "send", id=d["id"], fingerprint=b.draft["fingerprint"])
        wait_until(lambda: b.draft["state"] == "sending")
        job.result(10)
    wait_until(lambda: b.draft["state"] == "unknown", what="the draft to be unknown")
    assert b.pressLine == UNKNOWN_LINE and b.unknownOutcome is True
    b._press_line = "Nothing was sent. Press Send again if you still want it to go."    # a stale line is not shown
    assert b.pressLine == UNKNOWN_LINE


def test_a_draft_the_service_calls_sent_or_discarded_ends_the_box(lab, window):
    lb = lab()
    b = window(lb)
    reply_open(lab, b)
    settle_box(b)
    gone = dict(b.draft, state="sent", receipt={"line": "Sent to Priya from maya@acme.example · 09:08", "web": None})
    b._adopt(gone)
    assert b.draft is None and b.receipt["line"].startswith("Sent to Priya") and b.pressState == ""
    reply_open(lab, b, SAM)
    b._adopt(dict(b.draft, state="discarded"))
    assert b.draft is None and b.quiet == "That draft was discarded."


def test_a_draft_somebody_else_changed_is_said_and_send_waits_longer(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    assert b.changeNote == "" and b.armDelay == 350
    b.editField("body", "My own words.")                 # what the person typed is never "somebody else"
    wait_until(lambda: b.draft["body"] == "My own words.")
    assert b.changeNote == "" and b.armDelay == 350
    settle_box(b)
    lb.ask("draft_edit", agent=True, id=d["id"], body="Agent's words.", tainted=True)
    wait_until(lambda: b.draft["body"] == "Agent's words.", what="the draft to be taken")
    assert b.changeNote.startswith("This draft was changed while it was open") and b.armDelay == 1800
    settle_box(b)
    assert b.changeNote and b.canSend is True            # it can be sent, once read, and the button is slower
    b.editField("body", "Mine again.")
    assert b.changeNote == "" and b.armDelay == 350


def test_a_draft_of_the_agent_says_whose_words_they_are(lab, window):
    lb = lab()
    b = window(lb)
    made = lb.ask("draft", agent=True, kind="reply", reply_to=LEO, body="A, I think.", tainted=False)
    b.openMail(LEO)
    wait_until(lambda: b.mailState == "ready")
    b.reply("reply")                                     # goes on with the draft that is there, which is Bombadil's
    wait_until(lambda: b.draft is not None)
    assert b.draft["id"] == made["id"] and b.draftAuthor == "Written by Bombadil"
    assert len(lb.ask("list", view="drafts")) == 1
    mine = reply_open(lab, b, SAM)
    assert mine["created_by"] == "person" and b.draftAuthor == ""


def test_a_reply_asked_for_twice_at_once_is_one_draft(lab, window):
    lb = lab()
    b = window(lb)
    b.openMail(LAUNCH)
    wait_until(lambda: b.mailState == "ready")
    b.reply("reply")
    b.reply("reply")
    wait_until(lambda: b.draft is not None)
    spin(400)
    assert len(b.calls.ops("draft")) == 1 and len(lb.ask("list", view="drafts")) == 1
    assert len([a for a in b.calls.ops("list") if a.get("view") == "drafts"]) <= 2


# ---- what came while the line was down ---------------------------------------------------------------------------------

def test_what_was_typed_while_mail_was_away_is_saved_when_it_is_back(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    b.editField("body", "Typed while it was away.")
    lb.stop_service()
    wait_until(lambda: not b.connected, what="the loss to be seen")
    wait_until(lambda: b.fieldErrors.get("body"), what="the edit to be refused for want of a service")
    assert b.canSend is False and b.sendNote == "Mail is not running yet."
    lb.run(lb._start_service())
    wait_until(lambda: b.connected, 8.0, "the service")
    wait_until(lambda: lb.draft_of(d["id"])["body"] == "Typed while it was away.", 8.0, "the words to be saved")
    assert not b.fieldErrors and b.attachError == ""
    settle_box(b)
    assert b.canSend is True


def test_a_refused_field_typed_back_to_what_the_service_has_is_not_refused_any_more(lab, window):
    lb = lab()
    b = window(lb)
    reply_open(lab, b)
    settle_box(b)
    kept = b.draftFields["to"]
    b.editField("to", "not an address at all <<<")
    wait_until(lambda: b.fieldErrors.get("to"), what="the refusal")
    assert b.canSend is False
    b.editField("to", kept)                              # back to the words the service already has: nothing to send
    wait_until(lambda: not b.fieldErrors, what="the refusal to be let go")
    b.reportShown(b.draft["id"], b.draft["fingerprint"])
    wait_until(lambda: b.canSend, what="Send to be possible again")


def test_an_edit_in_flight_when_the_line_drops_is_sent_again(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    b.editField("body", "In flight when it went.")
    b.flush()
    b._mail_line._sock.abort()                           # the link goes with the edit written (or not) and no answer
    wait_until(lambda: b.connected and not b._pending_edits(), 8.0, "the edit to be settled after the reconnect")
    assert lb.draft_of(d["id"])["body"] == "In flight when it went." and not b.fieldErrors
    settle_box(b)
    assert b.canSend is True


# ---- a show for a draft, and one that came before the window -----------------------------------------------------------

def test_a_show_for_a_draft_opens_the_draft_live_and_before_the_window(lab, window):
    """agentd's typed "send it" is `show {view: drafts, id: <draft>}`: the window opens the box, not a mail."""
    lb = lab()
    d = lb.ask("draft", agent=True, kind="reply", reply_to=LEO, body="A.", tainted=False)
    lb.push_show(view="drafts", id=d["id"])
    b = window(lb)
    wait_until(lambda: b.draft is not None and b.draft["id"] == d["id"], what="the draft to open")
    assert b.view == "drafts" and b.shows == [] and b.mailError == ""
    wait_until(lambda: b.opened and b.opened["id"] == LEO and b.mailState == "ready")
    assert b.draftFields["body"] == "A." and b.canSend is False        # drawn, not yet told to the service
    # while the window runs, from another view
    b.setView("acct:a2")
    wait_until(lambda: b.draft is None and b.view == "acct:a2")
    lb.push_show(view="drafts", id=d["id"])
    wait_until(lambda: b.draft is not None and b.draft["id"] == d["id"], what="the draft to open again")
    assert b.view == "drafts" and len(b.shows) == 1 and b.mailError == ""
    # a draft's id with no view says where it is
    b.setView("all")
    wait_until(lambda: b.draft is None and b.view == "all")
    lb.push_show(id=d["id"])
    wait_until(lambda: b.draft is not None and b.view == "drafts")
    assert len(b.shows) == 2


def test_a_show_that_names_a_mail_and_no_view_still_loads_the_list(lab, window):
    lb = lab()
    lb.push_show(id=SAM)                                  # what the new-mail notice's Open sends
    b = window(lb, wait=False)
    wait_until(lambda: b.opened is not None and b.opened["id"] == SAM and b.mailState == "ready")
    wait_until(lambda: b.listState == "ready" and len(b.messages) > 3, what="the list beside the mail")
    assert b.view == "all" and b.shows == []


# ---- the first answer, a line that cannot be read, a mail that is made to be slow ---------------------------------------

class FlakyService:
    """A mail service whose first answer to `status` is an error and whose later ones are well."""

    def __init__(self, lab, path: Path, *, first: str = "error"):
        self.statuses, self.first = 0, first
        self.server = lab.run(self._start(path))

    async def _start(self, path):
        return await asyncio.start_unix_server(self._serve, path=str(path))

    async def _serve(self, reader, writer):
        while True:
            raw = await reader.readline()
            if not raw:
                break
            msg = json.loads(raw)
            rid, op = msg.get("rid"), msg.get("op")
            if op == "status":
                self.statuses += 1
                if self.statuses == 1 and self.first == "error":
                    answer = {"rid": rid, "ok": False, "error": "Mail could not do that just now.", "code": "internal"}
                else:
                    answer = {"rid": rid, "ok": True, "result": {
                        "engine": "up", "detail": "", "text": "", "accounts": [], "unread": 0, "needs_reply": 0,
                        "drafts": 0, "fake": True}}
            elif op == "list":
                answer = {"rid": rid, "ok": True, "result": {"view": "all", "messages": [], "cursor": None,
                                                              "more": False, "skipped": []}}
            else:
                answer = {"rid": rid, "ok": True, "result": None}
            writer.write((json.dumps(answer) + "\n").encode())
            await writer.drain()
        writer.close()


def test_a_first_answer_that_fails_is_asked_for_again(lab, window, home, monkeypatch):
    lb = lab()
    sock = home / "run" / "flaky.sock"
    srv = FlakyService(lb, sock)
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(sock))
    b = window(lb, wait=False)
    wait_until(lambda: b.connected, what="the connection")
    wait_until(lambda: b.loadText == "Mail is not answering. Trying again.", what="the sentence")
    assert b.ready is False
    wait_until(lambda: b.ready, 8.0, "the answer to the second ask")
    assert b.loadText == "Loading" and srv.statuses >= 2
    lb.run(_close(srv))


def test_a_line_nested_too_deep_to_read_costs_the_lines_behind_it_nothing(lab, qt, home):
    lb = lab()
    app = load_app()
    got = []
    deep = b"[" * 300_000 + b"]" * 300_000 + b"\n"
    srv = LongLines(lb, home / "run" / "deep.sock", [deep + b'{"n": 2}\n'])
    link = app.Line(lambda: str(home / "run" / "deep.sock"), app.MAIL_LINE_MAX, parent=None)
    link.got.connect(got.append)
    link.start()
    wait_until(lambda: got, what="the line behind the deep one")
    assert [m.get("n") for m in got] == [2]
    link.stop()
    lb.run(_close(srv))


def test_a_mail_that_is_one_unbroken_line_is_cut_and_folded(lab, window):
    """Laying out one run of characters with no blank in it takes time that grows with its square, so what the page
    is given has none longer than RUN_MAX and is at most MAIL_TEXT_MAX long."""
    lb = lab()
    b = window(lb)
    app = b.mod
    lb.engine.inject_new_mail("maya@acme.example", "Blob <blob@example.test>", "Blob", "A" * 200_000)
    wait_until(lambda: any(m["subject"] == "Blob" for m in b.messages), what="the new mail")
    mid = next(m["id"] for m in b.messages if m["subject"] == "Blob")
    t0 = time.monotonic()
    b.openMail(mid)
    wait_until(lambda: b.mailState == "ready", 8.0, "the mail")
    text = b.mail["text"]
    assert time.monotonic() - t0 < 5
    assert len(text) <= app.MAIL_TEXT_MAX + app.MAIL_TEXT_MAX // app.RUN_MAX and b.mail["truncated"] is True
    assert max(len(run) for run in text.split()) <= app.RUN_MAX
    assert text.replace("\n", "") == "A" * len(text.replace("\n", ""))
    # words and lines with blanks are left as they are, whatever the script
    plain = "Hi there,\n\nÉté 今日 — see https://example.org/" + "x" * 480 + "\nBye"
    assert app._text(plain) == plain
    assert max(len(r) for r in app._text("é" * 100_000).split()) <= app.RUN_MAX
    t0 = time.monotonic()
    app._text("a" * 499 + " " + "b" * 501 + " " + ("c" * 400 + " ") * 125)
    assert time.monotonic() - t0 < 1


# ---- what the list says and what it keeps ------------------------------------------------------------------------------

def test_a_refresh_does_not_unflag_or_unread_a_mail_opened_from_needs_a_reply(lab, window):
    lb = lab()
    b = window(lb)
    lb.ask("set_flags", id=SAM, flagged=True)
    lb.ask("mark_reply", id=SAM, needs=True, why="Asks for a yes on the pricing copy.")
    b.setView("needs_reply")
    wait_until(lambda: rows(b) == [SAM], what="Needs a reply")
    b.openMail(SAM)
    wait_until(lambda: b.mailState == "ready")
    assert b.opened["unread"] is True and b.opened["flagged"] is True and b.opened["needsReply"] is True
    lists = len(b.calls.ops("list"))
    b.refresh()
    wait_until(lambda: len(b.calls.ops("list")) > lists, what="the refresh")
    spin(400)
    assert b.opened["unread"] is True and b.opened["flagged"] is True and b.opened["needsReply"] is True
    assert b.opened["why"] == "Asks for a yes on the pricing copy."


def test_an_account_still_fetching_says_what_it_waits_for_and_an_inbox_that_cannot_be_read_is_not_empty(lab, window):
    lb = lab(samples=False)
    b = window(lb)
    b.addAccount("someone@icloud.com")
    wait_until(lambda: b.accounts, 8.0, "the account")
    acct = b.accounts[0]
    assert acct["state"] == "syncing" and "app-specific password" in acct["note"]
    row = b.sidebar[1]
    assert row["note"] == acct["note"] and row["engine"] is True      # the step is done in Thunderbird's window
    assert b.listWaiting is True and b.noAccount is False
    # a blocked one is explained by the left column, so the foot of the list does not say it a second time
    lb.engine.add_account("someone@school.example", "microsoft", state="blocked",
                          detail="school.example asks an admin to approve mail apps.")
    b.refresh()
    wait_until(lambda: len(b.accounts) == 2 and any(a["state"] == "blocked" for a in b.accounts), 8.0, "the account")
    b.setView("all")
    wait_until(lambda: b.listState in ("ready", "error"), 8.0, "the list")
    blocked = next(a for a in b.accounts if a["state"] == "blocked")
    assert next(r for r in b.sidebar if r["id"] == f"acct:{blocked['id']}")["note"] == blocked["note"]
    assert all(s["explained"] for s in b.skipped) and b.skippedHere == []


def test_a_sign_in_that_is_given_up_on_is_not_a_dead_end(lab, window):
    lb = lab(samples=False)
    b = window(lb)
    b.addAccount("someone@workspace.example")
    wait_until(lambda: b.accounts and b.signingIn is not None, 8.0, "the sign-in")
    assert b.noAccount is True
    b.removeAccount(b.signingIn["id"])
    wait_until(lambda: b.accounts == [], 8.0, "the account to go")
    assert b.signingIn is None and b.noAccount is True and b.addError == ""
    b.addAccount("other@workspace.example")                # and another can be tried
    wait_until(lambda: b.signingIn is not None and b.signingIn["email"] == "other@workspace.example", 8.0, "the sign-in")


# ---- the limits of a line --------------------------------------------------------------------------------------

def test_a_long_draft_in_another_script_goes_as_text_and_one_too_long_does_not_go_at_all(lab, window):
    lb = lab()
    b = window(lb)
    d = reply_open(lab, b)
    settle_box(b)
    text = "é" * 280_000                       # 560 000 bytes: over 1 MiB were it written as \u escapes
    b.editField("body", text)
    wait_until(lambda: b.draft["body"] == text, 10.0, "the long body to be taken")
    assert not b.fieldErrors
    edits = len(b.calls.ops("draft_edit"))
    b.editField("body", "é" * 2_000_000)
    wait_until(lambda: b.fieldErrors.get("body"), 10.0, "the refusal of a line that is too long")
    own = b.fieldErrors["body"]
    assert "too long" in own.lower() and "\n" not in own
    assert b.canSend is False and b.sendNote == b.fieldErrors["body"]
    assert lb.draft_of(d["id"])["body"] == text and len(b.calls.ops("draft_edit")) == edits + 1
    # the service's own limit (500 000 characters, 600 000 bytes) is its sentence, under the field
    b.editField("body", "é" * 400_000)
    wait_until(lambda: b.fieldErrors.get("body") not in (None, own), 10.0, "the service's answer")
    assert "\n" not in b.fieldErrors["body"] and b.canSend is False
    assert lb.draft_of(d["id"])["body"] == text


class LongLines:
    """A server that says what it is told to, to see how `Line` takes a line that is long."""

    def __init__(self, lab, path: Path, chunks: list[bytes]):
        self.path, self.chunks, self.connections = path, chunks, 0
        self.server = lab.run(self._start())

    async def _start(self):
        return await asyncio.start_unix_server(self._serve, path=str(self.path))

    async def _serve(self, reader, writer):
        self.connections += 1
        for chunk in self.chunks:
            writer.write(chunk)
            await writer.drain()
        await asyncio.sleep(1.0)
        writer.close()


def line_of(size: int, **extra) -> bytes:
    return (json.dumps({"push": "x", "pad": "a" * size, **extra}) + "\n").encode()


def test_a_line_is_read_whole_up_to_its_limit(lab, qt, home):
    lb = lab()
    app = load_app()
    got = []
    srv = LongLines(lb, home / "run" / "long.sock", [line_of(3 << 20, n=1), b'{"n": 2}\n', b"junk\n", b"[1]\n", b'{"n": 3}\n'])
    link = app.Line(lambda: str(home / "run" / "long.sock"), app.MAIL_LINE_MAX, parent=None)
    link.got.connect(got.append)
    link.start()
    wait_until(lambda: len(got) >= 3, what="the lines")
    assert [m.get("n") for m in got] == [1, 2, 3] and got[0]["pad"] == "a" * (3 << 20)
    link.stop()
    lb.run(_close(srv))


async def _close(srv):
    srv.server.close()


def test_a_line_over_the_limit_ends_a_link_that_needs_it_and_is_skipped_on_one_that_does_not(lab, qt, home):
    lb = lab()
    app = load_app()
    for drop in (False, True):
        sock = home / "run" / f"over-{drop}.sock"
        got, closed = [], []
        srv = LongLines(lb, sock, [line_of(300_000, n=1), b'{"n": 2}\n'])
        link = app.Line(lambda sock=sock: str(sock), 100_000, drop=drop, retry=False)
        link.got.connect(got.append)
        link.closed.connect(lambda closed=closed: closed.append(1))
        link.start()
        if drop:
            wait_until(lambda got=got: [m.get("n") for m in got] == [2], what="the line after the long one")
        else:
            wait_until(lambda closed=closed: closed, what="the link to be ended")
            assert not [m for m in got if m.get("n") == 1]
        link.stop()
        lb.run(_close(srv))


def test_nothing_of_a_mail_is_written_or_said_by_the_window(lab, window, home, capfd):
    lb = lab()
    b = window(lb)
    for mail in (LAUNCH, HOSTILE, INVOICE):
        b.openMail(mail)
        wait_until(lambda mail=mail: b.mailState == "ready" and b.opened["id"] == mail)
    b.reply("reply")
    wait_until(lambda: b.draft is not None)
    b.closeDraft()
    spin(300)
    out = capfd.readouterr()
    markers = (b"Legal is asking for the launch date", b"NOTICE TO THE AI ASSISTANT", b"made for tests")
    for text in (out.out.encode(), out.err.encode()):
        assert not any(m in text for m in markers)
    for path in home.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            found = [m for m in markers if m in data]
            assert not found, f"mail text {found} found in {path}"

"""The add-on in a real Thunderbird, answering the mail service's side of the engine protocol (docs/MAIL.md).

What runs: the lab's local Dovecot and SMTP sink (tests/mail/lab/mailserver.py, loopback only, seeded from
tests/mail/lab/seed and 130 more mails), a real Thunderbird 157 started hidden on a profile of its own with
`share/mail/extension` installed as the .xpi the engine would install, the real `bin/bombadil-mail-host`, and a
stand-in for the service (tests/mail/lab/engine_client.py) that sends the requests and reads the answers and
events. Everything lives in a temporary directory with its own HOME; no real mail provider, home directory or
runtime directory is touched, and the only network is 127.0.0.1.

What is held to: hello comes first; accounts and folders as they are; `list` over 137 messages in pages with the
service's `before` rule (every message once, newest first); `find` in its several ways; `get` of plain, HTML-only
and non-ASCII mail without marking anything read; an attachment arriving whole (SHA-256); `mark` and `move`
reaching the server, not only Thunderbird; `new_mail` when mail arrives; a new mail, a reply that keeps its
thread, a reply to all and a forward going out through the SMTP sink with an uploaded attachment intact; the
same without the `messages.send` permission; keys that survive a restart; and the 55-second answer when the
outgoing server refuses (Thunderbird then puts up a dialog nobody can see, and never answers).

The file skips cleanly without a Thunderbird (BOMBADIL_TEST_THUNDERBIRD, else the lab's unpacked 157) or a
Dovecot, and never leaves a process running: Thunderbird's whole process group is killed in every case.
"""

import atexit
import datetime
import email
import email.message
import email.utils
import hashlib
import shutil
import sys
import time
from email import policy
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent / "mail" / "lab"
sys.path.insert(0, str(LAB))

import addon_lab
from engine_client import EngineFailed

THUNDERBIRD = addon_lab.find_thunderbird()
SEEDS = addon_lab.SEED
BULK = 130
SEEDED = 7                       # unread mails in the inbox: the seeds
INBOX = SEEDED + BULK

pytestmark = [
    pytest.mark.skipif(THUNDERBIRD is None, reason="no Thunderbird unpacked (BOMBADIL_TEST_THUNDERBIRD)"),
    pytest.mark.skipif(shutil.which("dovecot") is None, reason="no Dovecot for the lab's local mail server"),
]

SINCE = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC).timestamp()
UNTIL = datetime.datetime(2026, 9, 29, 23, 59, 59, tzinfo=datetime.UTC).timestamp()


# -- the lab --


def started(**options):
    """A lab, started, with its account synced; stopped by its caller, and by the interpreter's exit if not."""
    box = addon_lab.AddonLab(addon_lab.new_root(), THUNDERBIRD, **options)
    atexit.register(box.stop)
    try:
        box.start(timeout=75)
    except BaseException:
        box.stop()
        box.remove()      # a lab that did not start is not left behind, with its processes or its directory
        raise
    return box


def account_of(box):
    return box.engine.request("accounts")[0]["engine_id"]


def wait_synced(box, inbox=INBOX, timeout=90):
    """Until the account has its folders and what the lab seeded in them, which is when Thunderbird has synced it:
    the Inbox at once, the Archive and Sent folders some seconds after (autosync visits them one by one)."""
    end = time.time() + timeout
    account = None
    while time.time() < end:
        rows = box.engine.request("accounts")
        account = rows[0]["engine_id"] if rows else None
        if account and rows[0]["folders"]["inbox"]:
            counts = [len(box.engine.request("list", account=account, folder=folder, limit=500)["messages"])
                      for folder in ("inbox", "archive", "sent")]
            if counts[0] >= inbox and counts[1] >= 1 and counts[2] >= 1:
                return account
        time.sleep(0.5)
    raise AssertionError(f"the account did not sync in {timeout} s\n{box.tail_log(15)}")


@pytest.fixture(scope="module")
def lab():
    box = None
    try:
        box = started(bulk=BULK)
        wait_synced(box)
        yield box
    finally:
        if box is not None:
            box.stop()
            box.remove()


@pytest.fixture(scope="module")
def account(lab):
    return account_of(lab)


def request(box, op, timeout=20, **args):
    return box.engine.request(op, timeout=timeout, **args)


def pages(box, account, limit=50, **extra):
    """Every message of the inbox the way the service reads it: `before` is the last time seen, and what was
    shown already is dropped."""
    seen, before, rounds = [], None, 0
    while True:
        args = {"account": account, "folder": "inbox", "limit": limit, **extra}
        if before is not None:
            args["before"] = before
        page = request(box, "list", **args)
        have = {m["key"] for m in seen}
        seen.extend(m for m in page["messages"] if m["key"] not in have)
        rounds += 1
        assert rounds < 20, "paging does not end"
        if not page["more"]:
            return seen, rounds
        before = page["messages"][-1]["ts"]


def keys(listing):
    return [m["key"] for m in (listing["messages"] if isinstance(listing, dict) else listing)]


def parse(raw):
    return email.message_from_bytes(raw, policy=policy.default)


def seed_mail(name):
    return parse((SEEDS / name).read_bytes())


def sink(box, n):
    """The n-th mail (from 1) the SMTP sink took: its record and the mail as parsed."""
    record = box.server.wait_sent(n, 30)[n - 1]
    return record, parse(box.server.sent_eml(n))


def until(check, timeout=40, what="the condition"):
    """Polls `check()` (which returns something true when it is so) and returns what it returned. For what
    Thunderbird only learns later: a folder that was moved into shows the mail when autosync has visited it,
    some seconds after the server has it."""
    end = time.time() + timeout
    while True:
        found = check()
        if found:
            return found
        assert time.time() < end, f"{what} did not come about in {timeout} s"
        time.sleep(0.5)


def text_of(mail):
    """The plain text of a mail as the person who reads it sees it (the wire has CRLF line ends)."""
    return mail.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n").strip()


def in_folder(copies, folder):
    """The copies in a folder that the server still shows (a deleted one waits for its expunge)."""
    return [c for c in copies if c[0] == folder and "T" not in c[1]]


# -- what the service is told first --


def test_hello_is_the_first_frame_and_says_what_thunderbird_is(lab):
    first = lab.engine.frames[0]
    assert first["event"] == "hello"
    assert first["version"] == 1
    assert first["app"] == "Thunderbird"
    assert int(first["app_version"].split(".")[0]) >= 153, "the add-on's strict_min_version"
    assert "send" in first["caps"]
    assert lab.engine.hello_frames == 1
    info = request(lab, "info")
    assert info == {"version": 1, "app": "Thunderbird", "app_version": first["app_version"],
                    "api": {"messages_send": True, "compose_reply": True}}


def test_accounts_say_who_the_person_is_and_what_folders_there_are(lab, account):
    (row,) = request(lab, "accounts")
    assert row["engine_id"] == account
    assert row["type"] == "imap"
    assert row["emails"] == ["test@example.test"]
    assert [(i["email"], i["name"]) for i in row["identities"]] == [("test@example.test", "Lab Tester")]
    assert row["folders"] == {"inbox": True, "sent": True, "drafts": True, "archive": True, "trash": True}
    assert row["unread"] == SEEDED
    assert row["state"] == "ok"
    assert row["detail"] == ""


# -- list --


def test_list_pages_over_a_hundred_and_thirty_seven_mails_each_once_newest_first(lab, account):
    seen, rounds = pages(lab, account, limit=50)
    assert rounds == 3
    assert len(seen) == INBOX
    assert len({m["key"] for m in seen}) == INBOX
    times = [m["ts"] for m in seen]
    assert times == sorted(times, reverse=True)
    assert seen[0]["key"] == "unicode-1@example.de"
    assert seen[SEEDED]["key"] == "bulk-129@example.org"
    assert seen[-1]["key"] == "bulk-0@example.org"
    assert all(m["folder"] == "inbox" and m["account"] == account for m in seen)
    whole = request(lab, "list", account=account, folder="inbox", limit=500)
    assert len(whole["messages"]) == INBOX and whole["more"] is False
    first = request(lab, "list", account=account, folder="inbox", limit=100)
    assert len(first["messages"]) == 100 and first["more"] is True
    # `before` is inclusive: the message at that very second is shown again
    again = request(lab, "list", account=account, folder="inbox", limit=10, before=first["messages"][-1]["ts"])
    assert again["messages"][0]["key"] == first["messages"][-1]["key"]


def test_list_other_folders_and_the_unread_ones(lab, account):
    archive = request(lab, "list", account=account, folder="archive", limit=10)
    assert keys(archive) == ["archived-1@example.org"]
    assert archive["messages"][0]["folder"] == "archive" and archive["messages"][0]["unread"] is False
    sent = request(lab, "list", account=account, folder="sent", limit=10)
    assert keys(sent) == ["sent-1@example.test"]
    assert sent["messages"][0]["folder"] == "sent"
    unread = request(lab, "list", account=account, folder="inbox", unread=True, limit=100)
    assert len(unread["messages"]) == SEEDED and unread["more"] is False
    assert not any(k.startswith("bulk-") for k in keys(unread))
    flagged = [m for m in request(lab, "list", account=account, folder="inbox", limit=20)["messages"] if m["flagged"]]
    assert keys(flagged) == ["plan-2@example.org"]
    with pytest.raises(EngineFailed) as e:
        request(lab, "list", account="no-such-account", folder="inbox")
    assert e.value.code == "not_found"


def test_the_mail_that_has_attachments_says_so(lab, account):
    listed = request(lab, "list", account=account, folder="inbox", limit=20)["messages"]
    assert [m["key"] for m in listed if m["attachments"]] == ["invoice-4711@example.com"]
    unicode_mail = next(m for m in listed if m["key"] == "unicode-1@example.de")
    assert unicode_mail["subject"] == "Grüße aus Zürich ☃"
    assert unicode_mail["from"] == {"name": "Jürgen Müller", "email": "juergen@example.de"}


# -- find --


def test_find_by_text_without_regard_to_case_and_in_the_body(lab):
    assert keys(request(lab, "find", text="INVOICE", limit=10)) == ["invoice-4711@example.com"]
    assert keys(request(lab, "find", text="attachbodymarker", limit=10)) == ["invoice-4711@example.com"]
    assert keys(request(lab, "find", text="htmlonlymarker", limit=10)) == ["news-1@news.example.org"]
    assert keys(request(lab, "find", text="no such words anywhere", limit=10)) == []
    out = request(lab, "find", text="Bulk mail 07", limit=20)
    assert keys(out) == [f"bulk-{i}@example.org" for i in range(79, 69, -1)]
    assert "partial" not in out


def test_find_in_the_body_of_old_mail_in_any_case_it_is_written_and_once_per_message(lab):
    """A search for words that are only in bodies is made by Thunderbird itself, window by window of time, so a
    hundred and thirty mails newer than none of it cost nothing; it matches the case the words are typed in, and
    capitalised, in title case and in capitals, which is how mail is written."""
    everyone = keys(request(lab, "find", text="hello tester", limit=500))
    assert "welcome-1@example.org" in everyone and "bulk-0@example.org" in everyone and "bulk-129@example.org" in everyone
    assert len(everyone) == len(set(everyone)) >= BULK + 1
    assert keys(request(lab, "find", text="HELLO TESTER", limit=3)) == everyone[:3]
    assert keys(request(lab, "find", text="ZZ-not-in-any-mail-ZZ", limit=10)) == []
    assert "partial" not in request(lab, "find", text="hello tester", limit=500)


def test_a_mail_in_two_folders_is_found_once_and_as_the_inbox_copy(lab, account):
    raw = (
        "From: Dora Double <dora@example.org>\r\nTo: Lab Tester <test@example.test>\r\n"
        "Subject: Doublemarker in two places\r\n"
        f"Date: {email.utils.formatdate(localtime=False)}\r\nMessage-ID: <double-1@example.org>\r\n"
        "MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nThe same mail twice.\r\n"
    ).encode()
    lab.server.deliver(raw, "Archive", seen=True)
    lab.server.deliver(raw, "INBOX", seen=True)
    until(lambda: all("double-1@example.org" in keys(request(lab, "list", account=account, folder=folder, limit=20))
                      for folder in ("inbox", "archive")), what="both copies of the mail")
    found = request(lab, "find", text="doublemarker", limit=10)
    assert keys(found) == ["double-1@example.org"], "one result for one message"
    assert found["messages"][0]["folder"] == "inbox"
    assert keys(request(lab, "find", text="doublemarker", folders=["archive"], limit=10)) == ["double-1@example.org"]


def test_find_by_people_subject_dates_and_flags(lab):
    # the bulk mails are copies of the welcome mail, so they are from Alice too
    from_alice = keys(request(lab, "find", **{"from": "alice@example.org"}, limit=500))
    assert len(from_alice) == BULK + 2 and {"welcome-1@example.org", "archived-1@example.org"} <= set(from_alice)
    assert from_alice[0] == "welcome-1@example.org"
    assert keys(request(lab, "find", **{"from": "ALICE"}, limit=500)) == from_alice
    assert keys(request(lab, "find", **{"from": "alice@example.org"}, subject="welcome", limit=10)) == [
        "welcome-1@example.org"]
    assert keys(request(lab, "find", subject="project plan", limit=10)) == ["plan-2@example.org", "plan-1@example.org"]
    assert keys(request(lab, "find", subject="project plan", limit=1)) == ["plan-2@example.org"]
    assert keys(request(lab, "find", to="carol@example.com", limit=10)) == ["lunch-1@example.net"]
    assert len(request(lab, "find", unread=True, limit=100)["messages"]) == SEEDED
    assert keys(request(lab, "find", flagged=True, limit=10)) == ["plan-2@example.org"]
    assert keys(request(lab, "find", attachments=True, limit=10)) == ["invoice-4711@example.com"]
    day = request(lab, "find", since=SINCE, until=UNTIL, limit=20)
    assert sorted(keys(day)) == ["invoice-4711@example.com", "lunch-1@example.net", "plan-1@example.org",
                                 "plan-2@example.org"]
    times = [m["ts"] for m in day["messages"]]
    assert times == sorted(times, reverse=True) and SINCE <= min(times) and max(times) <= UNTIL
    assert keys(request(lab, "find", folders=["sent"], limit=10)) == ["sent-1@example.test"]
    assert keys(request(lab, "find", folders=["trash"], limit=10)) == []


# -- get --


def test_get_plain_html_only_and_non_ascii(lab, account):
    plain = request(lab, "get", account=account, key="welcome-1@example.org")
    assert plain["text"].startswith("Hello Tester,") and plain["html"] is None
    assert plain["headers"]["message-id"] == "<welcome-1@example.org>"
    assert plain["message"]["subject"] == "Welcome to the lab" and plain["message"]["unread"] is True
    assert plain["attachments"] == []
    html = request(lab, "get", account=account, key="news-1@news.example.org")
    assert html["text"] is None and "HTMLONLYMARKER" in html["html"] and "<h1>" in html["html"]
    unicode_mail = request(lab, "get", account=account, key="unicode-1@example.de")
    assert "☃ UNICODEMARKER" in unicode_mail["text"] and unicode_mail["html"] is None
    both = request(lab, "get", account=account, key="lunch-1@example.net")
    assert both["text"] and both["html"] is None
    assert [a["email"] for a in both["message"]["cc"]] == ["carol@example.com"]
    reply = request(lab, "get", account=account, key="plan-2@example.org")
    assert reply["headers"]["in-reply-to"] == "<plan-1@example.org>"
    assert reply["headers"]["references"] == "<plan-1@example.org>"
    assert reply["message"]["thread"] == "plan-1@example.org"


def test_reading_leaves_mail_unread_in_thunderbird_and_on_the_server(lab, account):
    for key in ("welcome-1@example.org", "news-1@news.example.org", "unicode-1@example.de"):
        request(lab, "get", account=account, key=key)
    time.sleep(2)
    assert len(request(lab, "list", account=account, folder="inbox", unread=True, limit=100)["messages"]) == SEEDED
    for _folder, flags in lab.server_copies("welcome-1@example.org"):
        assert "S" not in flags, "reading set the seen flag on the server"


def test_get_no_such_mail_is_not_found(lab, account):
    with pytest.raises(EngineFailed) as e:
        request(lab, "get", account=account, key="nothing-like-it@example.org")
    assert e.value.code == "not_found"


# -- attachments --


def test_an_attachment_arrives_whole(lab, account):
    got = request(lab, "get", account=account, key="invoice-4711@example.com")
    assert [(a["name"], a["content_type"], a["inline"]) for a in got["attachments"]] == [
        ("invoice-4711.pdf", "application/pdf", False), ("notes.txt", "text/plain", False)]
    assert got["message"]["attachments"] is True
    seed = {part.get_filename(): part.get_payload(decode=True) for part in seed_mail("04-attachment.eml").iter_attachments()}
    for attachment in got["attachments"]:
        info = request(lab, "attachment", account=account, key="invoice-4711@example.com", part=attachment["part"])
        assert info["name"] == attachment["name"] and info["size"] == attachment["size"]
        data = lab.engine.fetch(info, timeout=30)
        want = seed[attachment["name"]]
        assert len(data) == info["size"] == len(want)
        assert hashlib.sha256(data).hexdigest() == hashlib.sha256(want).hexdigest()
    with pytest.raises(EngineFailed) as e:
        request(lab, "attachment", account=account, key="invoice-4711@example.com", part="9.9")
    assert e.value.code == "not_found"


def test_a_large_attachment_is_saved_in_many_pieces_and_arrives_whole(lab, account):
    data = payload(5_000_000, salt=11)
    made = email.message.EmailMessage(policy=policy.SMTP)
    made["From"] = "Alice Example <alice@example.org>"
    made["To"] = "Lab Tester <test@example.test>"
    made["Subject"] = "A large file for you"
    made["Date"] = "Tue, 29 Sep 2026 12:00:00 +0000"
    made["Message-ID"] = "<large-1@example.org>"
    made.set_content("The file is attached.\n")
    made.add_attachment(data, maintype="application", subtype="octet-stream", filename="large.bin")
    lab.server.deliver(made.as_bytes(), "INBOX", seen=True)
    got = until(lambda: request(lab, "get", account=account, key="large-1@example.org")
                if "large-1@example.org" in keys(request(lab, "list", account=account, folder="inbox", limit=30)) else None,
                what="the large mail")
    (attached,) = got["attachments"]
    started_at = time.time()
    info = request(lab, "attachment", account=account, key="large-1@example.org", part=attached["part"])
    assert info["size"] == len(data)
    saved = lab.engine.fetch(info, timeout=60)
    assert hashlib.sha256(saved).hexdigest() == hashlib.sha256(data).hexdigest()
    assert time.time() - started_at < 40


# -- known --


def test_known_from_the_sent_folder(lab):
    out = request(lab, "known", emails=["alice@example.org", "stranger@example.net", "ALICE@example.org"])
    assert out == {"alice@example.org": True, "stranger@example.net": False, "ALICE@example.org": True}


# -- events --


def test_new_mail_is_told_when_it_arrives(lab, account):
    """Before the tests that mark mail: after a flag change on the Inbox Thunderbird may take up to its periodic
    check (a minute) to notice new mail, instead of the second or so that its IDLE connection takes."""
    mark = lab.engine.mark()
    raw = (
        "From: Fiona Fresh <fiona@example.org>\r\nTo: Lab Tester <test@example.test>\r\n"
        "Subject: Fresh mail ☃\r\n"
        f"Date: {email.utils.formatdate(localtime=False)}\r\nMessage-ID: <fresh-1@example.org>\r\n"
        "MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nArrived while Thunderbird ran.\r\n"
    )
    lab.server.deliver(raw.encode(), "INBOX", seen=False)
    event = lab.engine.wait_event(
        "new_mail", 80, after=mark, where=lambda e: any(m["key"] == "fresh-1@example.org" for m in e["messages"]))
    assert event["account"] == account
    message = next(m for m in event["messages"] if m["key"] == "fresh-1@example.org")
    assert message["folder"] == "inbox" and message["unread"] is True
    assert message["from"] == {"name": "Fiona Fresh", "email": "fiona@example.org"}
    assert message["subject"] == "Fresh mail ☃"
    lab.engine.wait_event("counts_changed", 20, after=mark)
    assert "fresh-1@example.org" in keys(request(lab, "list", account=account, folder="inbox", limit=5))


# -- mark and move reach the server --


def test_mark_reaches_the_server(lab, account):
    key = "welcome-1@example.org"
    request(lab, "mark", account=account, key=key, read=True)
    copies = lab.wait_server(key, lambda c: any("S" in f for _, f in c), timeout=30)
    assert [folder for folder, flags in copies if "S" in flags] == ["INBOX"], copies
    assert next(m for m in request(lab, "list", account=account, folder="inbox", limit=20)["messages"]
                if m["key"] == key)["unread"] is False
    request(lab, "mark", account=account, key=key, flagged=True)
    copies = lab.wait_server(key, lambda c: any("F" in f for _, f in c), timeout=30)
    assert any("F" in flags for _, flags in copies), copies
    request(lab, "mark", account=account, key=key, read=False, flagged=False)
    copies = lab.wait_server(key, lambda c: all("F" not in f and "S" not in f for _, f in c), timeout=30)
    assert all("F" not in flags and "S" not in flags for _, flags in copies), copies
    request(lab, "mark", account=account, key="plan-2@example.org", flagged=False)
    copies = lab.wait_server("plan-2@example.org", lambda c: all("F" not in f for _, f in c), timeout=30)
    assert all("F" not in flags for _, flags in copies), copies
    with pytest.raises(EngineFailed) as e:
        request(lab, "mark", account=account, key="nothing-like-it@example.org", read=True)
    assert e.value.code == "not_found"


def test_move_to_the_archive_and_the_trash_and_back(lab, account):
    key = "lunch-1@example.net"
    request(lab, "move", account=account, key=key, to="archive")
    copies = lab.wait_server(key, lambda c: in_folder(c, "Archive") and not in_folder(c, "INBOX"), timeout=30)
    assert in_folder(copies, "Archive") and not in_folder(copies, "INBOX"), copies
    assert key not in keys(request(lab, "list", account=account, folder="inbox", limit=200))
    until(lambda: key in keys(request(lab, "list", account=account, folder="archive", limit=20)), what="the Archive")
    assert request(lab, "get", account=account, key=key)["message"]["folder"] == "archive"

    news = "news-1@news.example.org"
    request(lab, "move", account=account, key=news, to="trash")
    copies = lab.wait_server(news, lambda c: in_folder(c, "Trash") and not in_folder(c, "INBOX"), timeout=30)
    assert in_folder(copies, "Trash") and not in_folder(copies, "INBOX"), copies
    until(lambda: keys(request(lab, "list", account=account, folder="trash", limit=20)) == [news], what="the Trash")
    request(lab, "move", account=account, key=news, to="inbox")
    copies = lab.wait_server(news, lambda c: in_folder(c, "INBOX") and not in_folder(c, "Trash"), timeout=30)
    assert in_folder(copies, "INBOX") and not in_folder(copies, "Trash"), copies
    until(lambda: news in keys(request(lab, "list", account=account, folder="inbox", limit=200)), what="the Inbox")
    request(lab, "move", account=account, key=key, to="inbox")   # the archived lunch mail goes back, for what follows
    lab.wait_server(key, lambda c: in_folder(c, "INBOX"), timeout=30)


# -- send --


def payload(size, salt=1):
    return bytes((i * 7 + salt) % 251 for i in range(size))


def test_a_new_mail_with_an_attachment_goes_out_whole(lab, account):
    before = len(lab.server.sent())
    data = payload(700_000)
    xfer = lab.engine.upload(data)
    answer = request(
        lab, "send", timeout=70, account=account, kind="new", reply_to=None,
        to=[{"name": "Zed Zebra Ünï", "email": "zed@example.net"}], cc=[{"name": "", "email": "cc@example.net"}],
        bcc=[{"name": "", "email": "bcc@example.net"}], subject="Hello ☃ from Bombadil",
        body="Line one\n\nLine two ✓\n",
        attachments=[{"name": "data.bin", "content_type": "application/octet-stream", "xfer": xfer}])
    assert answer["saved"] is True
    record, sent = sink(lab, before + 1)
    assert record["mail_from"] == "test@example.test"
    assert sorted(record["rcpt_to"]) == ["bcc@example.net", "cc@example.net", "zed@example.net"]
    assert sent["Message-ID"] == f"<{answer['message_id']}>"
    assert str(sent["From"]) == "Lab Tester <test@example.test>"
    assert sent["To"].addresses[0].display_name == "Zed Zebra Ünï" and sent["To"].addresses[0].addr_spec == "zed@example.net"
    assert sent["Cc"].addresses[0].addr_spec == "cc@example.net"
    assert sent["Bcc"] is None, "a blind copy is not in the headers"
    assert sent["Subject"] == "Hello ☃ from Bombadil"
    assert sent["In-Reply-To"] is None
    assert text_of(sent) == "Line one\n\nLine two ✓"
    (attached,) = list(sent.iter_attachments())
    assert attached.get_filename() == "data.bin"
    assert hashlib.sha256(attached.get_payload(decode=True)).hexdigest() == hashlib.sha256(data).hexdigest()
    copies = lab.wait_server(answer["message_id"], lambda c: in_folder(c, "Sent"), timeout=30, folders=("Sent",))
    assert in_folder(copies, "Sent"), "the copy is in the Sent folder on the server"
    assert answer["message_id"] in keys(request(lab, "list", account=account, folder="sent", limit=5))
    assert request(lab, "known", emails=["zed@example.net", "bcc@example.net"]) == {
        "zed@example.net": True, "bcc@example.net": True}


def test_a_large_attachment_is_sent_in_pieces_and_arrives_intact(lab, account):
    before = len(lab.server.sent())
    data = payload(8_000_000, salt=5)
    started_at = time.time()
    xfer = lab.engine.upload(data)
    request(lab, "send", timeout=70, account=account, kind="new", reply_to=None,
            to=[{"name": "", "email": "big@example.net"}], cc=[], bcc=[], subject="A large file", body="See it.\n",
            attachments=[{"name": "big.bin", "content_type": "application/octet-stream", "xfer": xfer}])
    _record, sent = sink(lab, before + 1)
    (attached,) = list(sent.iter_attachments())
    assert hashlib.sha256(attached.get_payload(decode=True)).hexdigest() == hashlib.sha256(data).hexdigest()
    assert time.time() - started_at < 60


def test_a_mail_with_no_subject_goes_out_without_a_window(lab, account):
    before = len(lab.server.sent())
    answer = request(lab, "send", timeout=70, account=account, kind="new", reply_to=None,
                     to=[{"name": "", "email": "zed@example.net"}], cc=[], bcc=[], subject="", body="Nothing to say.\n",
                     attachments=[])
    assert answer["message_id"]
    _record, sent = sink(lab, before + 1)
    assert not sent["Subject"]
    assert text_of(sent) == "Nothing to say."


def test_a_reply_keeps_its_thread(lab, account):
    before = len(lab.server.sent())
    answer = request(
        lab, "send", timeout=70, account=account, kind="reply", reply_to="plan-2@example.org",
        to=[{"name": "Dave Planner", "email": "dave@example.org"}], cc=[], bcc=[], subject="Re: Project plan",
        body="Thanks, that looks good.\n\n> Following up on my own mail: any news on the plan?\n", attachments=[])
    assert answer["saved"] is True
    record, sent = sink(lab, before + 1)
    assert record["rcpt_to"] == ["dave@example.org"]
    assert sent["In-Reply-To"] == "<plan-2@example.org>"
    assert sent["References"] == "<plan-1@example.org> <plan-2@example.org>"
    assert sent["Subject"] == "Re: Project plan"
    assert text_of(sent).startswith("Thanks, that looks good.")
    assert list(sent.iter_attachments()) == []


def test_reply_all_and_forward_with_a_file(lab, account):
    before = len(lab.server.sent())
    request(lab, "send", timeout=70, account=account, kind="reply_all", reply_to="lunch-1@example.net",
            to=[{"name": "Bob Builder", "email": "bob@example.net"}], cc=[{"name": "", "email": "carol@example.com"}],
            bcc=[], subject="Re: Lunch on Friday?", body="Friday works.\n", attachments=[])
    record, reply = sink(lab, before + 1)
    assert sorted(record["rcpt_to"]) == ["bob@example.net", "carol@example.com"]
    assert reply["In-Reply-To"] == "<lunch-1@example.net>"
    assert reply["Cc"].addresses[0].addr_spec == "carol@example.com"

    data = payload(5000, salt=9)
    xfer = lab.engine.upload(data)
    request(lab, "send", timeout=70, account=account, kind="forward", reply_to="invoice-4711@example.com",
            to=[{"name": "", "email": "fwd@example.net"}], cc=[], bcc=[], subject="Fwd: Invoice 4711",
            body="For you.\n", attachments=[{"name": "mine.bin", "content_type": "application/octet-stream", "xfer": xfer}])
    _record, forwarded = sink(lab, before + 2)
    assert forwarded["Subject"] == "Fwd: Invoice 4711"
    assert forwarded["X-Forwarded-Message-Id"] == "<invoice-4711@example.com>"
    names = [a.get_filename() for a in forwarded.iter_attachments()]
    assert names == ["mine.bin"], "the original's attachments are not carried along unasked"
    (attached,) = list(forwarded.iter_attachments())
    assert attached.get_payload(decode=True) == data


def test_a_send_that_was_not_asked_for_does_not_happen(lab, account):
    """Nothing but a `send` request sends: a request that is refused sends nothing, and nothing else is in the sink."""
    before = len(lab.server.sent())
    for bad in ({"to": []}, {"to": [{"name": "", "email": "not an address"}]}, {"kind": "reply", "reply_to": None},
                {"kind": "reply", "reply_to": "nothing-like-it@example.org"}):
        args = {"account": account, "kind": "new", "reply_to": None, "to": [{"name": "", "email": "a@example.net"}],
                "cc": [], "bcc": [], "subject": "x", "body": "x", "attachments": [], **bad}
        with pytest.raises(EngineFailed) as e:
            request(lab, "send", timeout=30, **args)
        assert e.value.code in ("bad_request", "not_found")
    time.sleep(2)
    assert len(lab.server.sent()) == before


# -- keys outlive Thunderbird --


def test_a_restart_changes_no_key_and_hello_comes_again(lab, account):
    before = keys(request(lab, "list", account=account, folder="inbox", limit=500))
    assert len(before) >= INBOX
    lab.restart_thunderbird(timeout=75)
    assert lab.engine.hello_frames >= 2
    end = time.time() + 60
    while True:
        try:
            after = keys(request(lab, "list", account=account, folder="inbox", limit=500))
            if len(after) >= len(before):
                break
        except (EngineFailed, TimeoutError):
            pass
        assert time.time() < end, "the inbox did not come back"
        time.sleep(1)
    assert after == before
    assert request(lab, "get", account=account, key="plan-2@example.org")["text"]


# -- without the permission for the quiet way of sending --


@pytest.fixture(scope="module")
def plain():
    box = None
    try:
        box = started(optional_permissions=())
        wait_synced(box, inbox=SEEDED)
        yield box
    finally:
        if box is not None:
            box.stop()
            box.remove()


def test_without_messages_send_a_mail_goes_through_a_compose_window_and_nothing_is_left_open(plain):
    account = account_of(plain)
    assert request(plain, "info")["api"]["messages_send"] is False
    assert "messages_send" not in plain.engine.frames[0]["caps"]
    before = len(plain.server.sent())
    data = payload(400_000, salt=3)
    xfer = plain.engine.upload(data)
    answer = request(plain, "send", timeout=70, account=account, kind="new", reply_to=None,
                     to=[{"name": "", "email": "zed@example.net"}], cc=[], bcc=[], subject="Through a window",
                     body="Words.\n", attachments=[{"name": "d.bin", "content_type": "application/octet-stream",
                                                    "xfer": xfer}])
    assert answer["message_id"] and answer["saved"] is True
    _record, sent = sink(plain, before + 1)
    assert sent["Subject"] == "Through a window"
    (attached,) = list(sent.iter_attachments())
    assert hashlib.sha256(attached.get_payload(decode=True)).hexdigest() == hashlib.sha256(data).hexdigest()
    reply = request(plain, "send", timeout=70, account=account, kind="reply", reply_to="plan-2@example.org",
                    to=[{"name": "", "email": "dave@example.org"}], cc=[], bcc=[], subject="Re: Project plan",
                    body="Reply.\n", attachments=[])
    assert reply["message_id"]
    _record, threaded = sink(plain, before + 2)
    assert threaded["In-Reply-To"] == "<plan-2@example.org>"
    # a window would stop to ask about a mail with no subject and wait for ever: that is refused before it opens
    started_at = time.time()
    with pytest.raises(EngineFailed) as e:
        request(plain, "send", timeout=30, account=account, kind="new", reply_to=None,
                to=[{"name": "", "email": "zed@example.net"}], cc=[], bcc=[], subject="", body="Words.\n",
                attachments=[])
    assert e.value.code == "engine_error" and "no subject" in str(e.value) and "Nothing was sent" in str(e.value)
    assert time.time() - started_at < 10
    time.sleep(1)
    assert len(plain.server.sent()) == before + 2
    assert request(plain, "info")["version"] == 1     # and Thunderbird is still answering after the windows closed


# -- a sender with a signature --

SIGNATURE = "The Lab Signature"


@pytest.fixture(scope="module")
def signed():
    """An identity with a signature, which is what makes Thunderbird compose a new mail's foot itself."""
    box = None
    try:
        box = started(extra_prefs=(
            f'user_pref("mail.identity.id1.htmlSigText", "{SIGNATURE}");\n'
            'user_pref("mail.identity.id1.htmlSigFormat", false);\n'
            'user_pref("mail.identity.id1.attach_signature", false);\n'))
        wait_synced(box, inbox=SEEDED)
        yield box
    finally:
        if box is not None:
            box.stop()
            box.remove()


def test_the_signature_and_what_thunderbird_quotes_stay_under_the_persons_words(signed):
    """The draft says a reply's quote and the signature are added when it sends: the words go above what
    Thunderbird composed in its window, and nothing of that is lost or doubled."""
    account = account_of(signed)
    before = len(signed.server.sent())
    to = [{"name": "", "email": "zed@example.net"}]
    request(signed, "send", timeout=70, account=account, kind="new", reply_to=None, to=to, cc=[], bcc=[],
            subject="Signed new mail", body="Words for a new mail.\n", attachments=[])
    _record, new = sink(signed, before + 1)
    body = text_of(new)
    assert body.startswith("Words for a new mail.")
    assert body.count(SIGNATURE) == 1 and body.index(SIGNATURE) > body.index("new mail.")

    request(signed, "send", timeout=70, account=account, kind="reply", reply_to="plan-2@example.org",
            to=[{"name": "", "email": "dave@example.org"}], cc=[], bcc=[], subject="Re: Project plan",
            body="My answer.\n", attachments=[])
    _record, reply = sink(signed, before + 2)
    body = text_of(reply)
    assert reply["In-Reply-To"] == "<plan-2@example.org>"
    assert body.startswith("My answer.")
    assert body.count("Following up on my own mail") == 1, "the quoted mail is there, once"
    assert body.count(SIGNATURE) == 1
    assert body.index("My answer.") < body.index("Following up on my own mail") < body.index(SIGNATURE)

    request(signed, "send", timeout=70, account=account, kind="forward", reply_to="plan-1@example.org", to=to,
            cc=[], bcc=[], subject="Fwd: Project plan", body="For you.\n", attachments=[])
    _record, forwarded = sink(signed, before + 3)
    body = text_of(forwarded)
    assert body.startswith("For you."), body
    assert body.count("Here is the project plan.") == 1, f"the forwarded mail is there, once: {body!r}"
    # Thunderbird 157 composes no signature in an inline forward, whatever sig_on_fwd says (seen in its own window);
    # what is held to is that the add-on does not add one, nor lose the forwarded mail
    assert body.count(SIGNATURE) == 0, body
    assert body.index("For you.") < body.index("Here is the project plan.")


# -- when the outgoing server refuses --


@pytest.fixture(scope="module")
def refusing():
    box = None
    try:
        box = started(smtp_refuses=True)
        wait_synced(box, inbox=SEEDED)
        yield box
    finally:
        if box is not None:
            box.stop()
            box.remove()


def test_a_send_that_thunderbird_never_answers_is_unknown_outcome_at_fifty_five_seconds(refusing):
    """The outgoing port refuses connections, and Thunderbird puts up a dialog (window nobody sees) and leaves
    the call pending until someone clicks it. The add-on gives up at 55 s from the request, says it cannot tell,
    refuses a second send for the account while the first is still unanswered, and goes on answering reads."""
    account = account_of(refusing)
    args = {"account": account, "kind": "new", "reply_to": None, "to": [{"name": "", "email": "zed@example.net"}],
            "cc": [], "bcc": [], "subject": "Never sent", "body": "Words.\n", "attachments": []}
    started_at = time.time()
    with pytest.raises(EngineFailed) as e:
        request(refusing, "send", timeout=75, **args)
    took = time.time() - started_at
    assert e.value.code == "unknown_outcome"
    assert 50 <= took <= 62, f"answered after {took:.1f} s"
    assert "Sent folder" in str(e.value)
    assert refusing.server.sent() == [], "nothing reached the outgoing server"
    started_at = time.time()
    with pytest.raises(EngineFailed) as e:
        request(refusing, "send", timeout=30, **args)
    assert e.value.code == "engine_error" and "Nothing was sent" in str(e.value)
    assert time.time() - started_at < 10
    assert len(request(refusing, "list", account=account, folder="inbox", limit=5)["messages"]) == 5
    assert request(refusing, "info")["version"] == 1

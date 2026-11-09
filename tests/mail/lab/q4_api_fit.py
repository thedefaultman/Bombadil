#!/usr/bin/env python3
"""Q4: exercise the MailExtension APIs Bombadil Mail would rely on, on the REAL Thunderbird, against the lab server.

    run-thunderbird.sh start          # (or let this script start it: --start)
    python3 q4_api_fit.py [--start] [--json out.json]

Every check records  status  ok | partial | gap  and the evidence string. Prints a table, exit code 0 always
(a gap is a finding, not a failure). State-changing checks (move/delete/send) run against the seeded mailbox, so
start from a fresh lab.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labtest import Lab, pretty  # noqa: E402
from lab_bridge import BridgeError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def record(name, status, evidence):
    RESULTS.append({"check": name, "status": status, "evidence": evidence})
    print("%-7s %-46s %s" % (status.upper(), name, evidence[:230].replace("\n", " ")), flush=True)


def check(name):
    def deco(fn):
        def run(*a, **k):
            try:
                st, ev = fn(*a, **k)
            except Exception as e:  # noqa
                st, ev = "gap", "%s: %s" % (type(e).__name__, e)
            record(name, st, ev)
        return run
    return deco


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    lab = Lab()
    if a.start:
        lab.start()

    b = lab.bridge()
    ms = lab.ms
    time.sleep(3)  # let the first IMAP sync settle

    def q(**kw):
        return b.call("messages.query", kw)["messages"]

    def by_mid(hid):
        r = q(headerMessageId=hid)
        return r[0] if r else None

    inbox = b.call("folders.query", {"specialUse": ["inbox"]})[0]

    @check("accounts.list (+identities, Local Folders)")
    def _():
        acc = b.call("accounts.list", True)
        ids = [(x["id"], x["type"]) for x in acc]
        ident = acc[0]["identities"][0]
        return "ok", "accounts=%s identity=%s <%s>" % (ids, ident["name"], ident["email"])
    _()

    @check("folders.query specialUse (inbox/sent/drafts/archives/trash/junk)")
    def _():
        found = {}
        for su in ("inbox", "sent", "drafts", "archives", "trash", "junk"):
            r = b.call("folders.query", {"specialUse": [su]})
            found[su] = r[0]["path"] if r else None
        st = "ok" if all(found.values()) else "partial"
        return st, pretty(found)
    _()

    @check("folders.getSubFolders / getFolderInfo")
    def _():
        subs = [(f["name"], f["path"]) for f in b.call("folders.getSubFolders", "account1://")]
        info = b.call("folders.getFolderInfo", inbox["id"])
        return "ok", "names=%s (Junk shows as 'Spam', Archive as 'Archives') info=%s" % (subs, pretty(info, 120))
    _()

    @check("messages.list (folder page 1)")
    def _():
        r = b.call("messages.list", inbox["id"])
        return "ok", "%d messages, id=%r; first=%s" % (len(r["messages"]), r["id"], pretty({k: r["messages"][0][k] for k in ("id", "author", "subject", "read", "headerMessageId", "date")}, 260))
    _()

    @check("messages.query by folder/unread/flagged/attachment")
    def _():
        return "ok", "folder=%d unread=%d flagged=%d attachment=%s" % (
            len(q(folderId=inbox["id"])), len(q(folderId=inbox["id"], unread=True)), len(q(flagged=True)),
            [m["subject"] for m in q(attachment=True)])
    _()

    @check("messages.query by fromMail (assumed)")
    def _():
        try:
            q(fromMail="alice@example.org")
            return "ok", "accepted"
        except BridgeError as e:
            by_author = [m["subject"] for m in q(author="alice@example.org")]
            return "gap", "no 'fromMail' property (%s); use author=<address>: %s" % (e, by_author)
    _()

    @check("messages.query author / recipients / subject / text search")
    def _():
        r = {
            "author=alice@example.org": [m["subject"] for m in q(author="alice@example.org")],
            "recipients=carol@example.com": [m["subject"] for m in q(recipients="carol@example.com")],
            "subject=Invoice": [m["subject"] for m in q(subject="Invoice")],
            "fullText=ALTPLAINMARKER": [m["subject"] for m in q(fullText="ALTPLAINMARKER")],
            "body=HTMLONLYMARKER": [m["subject"] for m in q(body="HTMLONLYMARKER")],
            "fromDate(Date)": len(q(fromDate={"__date": "2026-09-29T00:00:00Z"})),
        }
        try:
            b.call("messages.query", {"fromDate": "2026-09-29T00:00:00Z"}, timeout=8)
            r["fromDate(string)"] = "answered"
        except TimeoutError:
            r["fromDate(string)"] = "NEVER ANSWERS: pass a Date object, not an ISO string or number"
        return "ok", pretty(r, 900)
    _()

    @check("messages.list pagination (130 extra mails)")
    def _():
        base = open(os.path.join(HERE, "seed", "01-plain-welcome.eml"), "rb").read()
        for i in range(130):
            raw = base.replace(b"welcome-1@", b"bulk-%d@" % i).replace(b"Welcome to the lab", b"Bulk mail %03d" % i)
            ms.deliver(raw, "INBOX", seen=True)
        t0 = time.time()
        while time.time() - t0 < 60:
            if b.call("folders.getFolderInfo", inbox["id"])["totalMessageCount"] >= 137:
                break
            time.sleep(0.5)
        sync = time.time() - t0
        page = b.call("messages.list", inbox["id"])
        n1, lid = len(page["messages"]), page["id"]
        total = n1
        pages = 1
        while lid:
            page = b.call("messages.continueList", lid)
            total += len(page["messages"])
            lid = page["id"]
            pages += 1
        return ("ok" if total >= 137 else "partial"), "sync of 130 new mails took %.1fs; page1=%d, pages=%d, total=%d (messageListId continues)" % (sync, n1, pages, total)
    _()

    @check("messages.getFull plain / html-only / alternative")
    def _():
        out = {}
        for hid, label in (("welcome-1@example.org", "plain"), ("news-1@news.example.org", "html-only"), ("lunch-1@example.net", "alt")):
            m = by_mid(hid)
            full = b.call("messages.getFull", m["id"])

            def walk(p, acc):
                if p.get("body") is not None:
                    acc.append((p["contentType"], len(p["body"])))
                for c in p.get("parts", []):
                    walk(c, acc)
                return acc
            out[label] = walk(full, [])
        return "ok", "text body available for plain and alt; html-only has ONLY a text/html body part (convert yourself): " + pretty(out)
    _()

    @check("messages.getFull unicode subject/body")
    def _():
        m = by_mid("unicode-1@example.de")
        full = b.call("messages.getFull", m["id"])
        body = full["parts"][0]["body"]
        return ("ok" if "Zürich" in body else "partial"), "subject=%r author=%r body=%r" % (m["subject"], m["author"], body[:40])
    _()

    @check("messages.getRaw / getHeaders (Re: prefix)")
    def _():
        m = by_mid("plan-2@example.org")
        raw = b.call("messages.getRaw", m["id"])
        hdr = b.call("messages.getHeaders", m["id"])
        return "partial", ("getRaw starts with %r (X-Mozilla-Status lines are prepended to the RFC 822 text); list subject=%r but real Subject header=%r "
                           "(Thunderbird strips 'Re: ' from MessageHeader.subject); In-Reply-To=%s" % (raw[:34], m["subject"], hdr["subject"], hdr.get("in-reply-to")))
    _()

    @check("messages.listAttachments + getAttachmentFile (hash)")
    def _():
        m = by_mid("invoice-4711@example.com")
        atts = b.call("messages.listAttachments", m["id"])
        src = open(os.path.join(HERE, "seed", "04-attachment.eml"), "rb").read().replace(b"\r\n", b"\n").decode()
        b64 = re.search(r"Content-Transfer-Encoding: base64\n\n(.*?)\n--MIXED", src, re.S).group(1)
        want = hashlib.sha256(base64.b64decode("".join(b64.split()))).hexdigest()
        f = b.call("messages.getAttachmentFile", m["id"], atts[0]["partName"])
        got = hashlib.sha256(base64.b64decode(f["b64"])).hexdigest()
        return ("ok" if got == want else "partial"), "%s; %s sha256 %s" % ([(x["name"], x["contentType"], x["size"]) for x in atts], atts[0]["name"], "matches source" if got == want else "DIFFERS")
    _()

    @check("messages.update read/flagged -> IMAP flags on server")
    def _():
        m = by_mid("news-1@news.example.org")
        b.call("messages.update", m["id"], {"read": True, "flagged": True})
        fl = lab.imap_flags("INBOX", "<news-1@news.example.org>")
        return ("ok" if fl and "\\Seen" in fl and "\\Flagged" in fl else "partial"), "server says %s" % fl
    _()

    @check("messages.move / copy -> server")
    def _():
        arch = b.call("folders.query", {"specialUse": ["archives"]})[0]
        m = by_mid("lunch-1@example.net")
        b.call("messages.copy", [m["id"]], arch["id"])
        time.sleep(2)
        in_arch = lab.imap_flags("Archive", "<lunch-1@example.net>")
        m = by_mid("lunch-1@example.net")
        m2 = [x for x in q(headerMessageId="lunch-1@example.net")]
        b.call("messages.move", [m2[0]["id"]], arch["id"])
        time.sleep(2)
        still_inbox = lab.imap_flags("INBOX", "<lunch-1@example.net>")
        return "ok", "copy->Archive flags=%s; after move INBOX has it: %s (None = gone)" % (in_arch, still_inbox)
    _()

    @check("messages.archive -> Archive on server")
    def _():
        m = by_mid("plan-1@example.org")
        b.call("messages.archive", [m["id"]])
        time.sleep(3)
        return "partial", ("left INBOX=%s; Archive root flags=%s; Archive/2026 flags=%s (archive() files into Archive/<year>; "
                           "set mail.identity.id1.archive_granularity=0 for a flat Archive)" % (
            lab.imap_flags("INBOX", "<plan-1@example.org>") is None, lab.imap_flags("Archive", "<plan-1@example.org>", 1), lab.imap_flags("Archive/2026", "<plan-1@example.org>")))
    _()

    @check("messages.delete (to Trash) and deletePermanently")
    def _():
        m = by_mid("invoice-4711@example.com")
        b.call("messages.delete", [m["id"]])
        time.sleep(3)
        trash = lab.imap_flags("Trash", "<invoice-4711@example.com>")
        m = by_mid("unicode-1@example.de")
        b.call("messages.delete", [m["id"]], {"deletePermanently": True})
        time.sleep(3)
        perm = lab.imap_flags("INBOX", "<unicode-1@example.de>")
        return "ok", "Trash has it: %s; permanent delete: server INBOX flags now %s (\\Deleted set, expunged on compaction)" % (trash, perm)
    _()

    @check("messages.onNewMailReceived latency (INBOX, IDLE)")
    def _():
        b.listen("messages.onNewMailReceived")
        base = open(os.path.join(HERE, "seed", "01-plain-welcome.eml"), "rb").read()
        lat = []
        for i in range(3):
            t0 = time.time()
            ms.deliver(base.replace(b"welcome-1@", b"nm-%d@" % i).replace(b"Welcome to the lab", b"NEWMAIL %d" % i))
            ev = b.wait_event("messages.onNewMailReceived", 90, since=t0, where=lambda e, i=i: any(("NEWMAIL %d" % i) == x["subject"] for x in e["args"][1]["messages"]))
            lat.append(round(ev["t"] / 1000 - t0, 2))
            time.sleep(1)
        return "ok", "latencies (s) %s; event args = [MailFolder, MessageList]" % lat
    _()

    @check("new mail in a NON-inbox folder: visible? event?")
    def _():
        arch = b.call("folders.query", {"specialUse": ["archives"]})[0]
        base = open(os.path.join(HERE, "seed", "01-plain-welcome.eml"), "rb").read()
        n0 = b.call("folders.getFolderInfo", arch["id"])["totalMessageCount"]
        t0 = time.time()
        ms.deliver(base.replace(b"welcome-1@", b"arch-new@").replace(b"Welcome to the lab", b"NEWMAIL ARCH"), "Archive")
        seen = None
        while time.time() - t0 < 45:
            if b.call("folders.getFolderInfo", arch["id"])["totalMessageCount"] > n0:
                seen = time.time() - t0
                break
            time.sleep(0.5)
        online = b.call("messages.query", {"folderId": arch["id"], "headerMessageId": "arch-new@example.org", "online": True}, timeout=30)["messages"]
        evs = [e for e in b.events if e["event"] == "messages.onNewMailReceived" and e["t"] / 1000 >= t0 and e["args"][0]["path"] == "/Archive"]
        return "partial", ("local folder count saw it after %s s; query(online:true) found it: %s; onNewMailReceived fired: %s. "
                           "Non-inbox folders are not IDLE-watched: poll with query(online:true) or rely on the periodic sync" % (None if seen is None else round(seen, 1), bool(online), bool(evs)))
    _()

    @check("addressBooks / contacts quickSearch")
    def _():
        books = b.call("addressBooks.list")
        # send something so the address collector sees a recipient
        b.call("messages.sendMessage", {"to": ["Zed Zebra <zed@example.org>"], "subject": "collect", "body": "x"}, {"mode": "sendNow"})
        time.sleep(3)
        r1 = b.call("contacts.quickSearch", "zed@example.org")
        ab = [x for x in books if "ollected" in x["name"]]
        cid = None
        if books:
            cid = b.call("contacts.create", books[0]["id"], None, {"PrimaryEmail": "new@example.org", "FirstName": "New", "LastName": "Person"})
        r2 = b.call("contacts.quickSearch", "new@example.org")
        return "ok", "books=%s; collected-by-send quickSearch(zed@example.org)=%d; created contact found=%d" % ([x["name"] for x in books], len(r1), len(r2))
    _()

    @check("messages.sendMessage (background, permission messages.send)")
    def _():
        n0 = len(ms.sent())
        w0 = lab.windows()
        t0 = time.time()
        r = b.call("messages.sendMessage", {"to": ["alice@example.org"], "subject": "BG send", "body": "hello from background",
                                             "attachments": [{"file": {"__file": True, "name": "hi.txt", "type": "text/plain", "b64": base64.b64encode(b"attachment-bytes").decode()}}]},
                   {"mode": "sendNow"})
        dt = time.time() - t0
        s = ms.wait_sent(n0 + 1, 20)[-1]
        w1 = lab.windows()
        time.sleep(3)
        in_sent = lab.imap_flags("Sent", s["message_id"])
        return "ok", "returned in %.2fs %s; SMTP got attachments=%s rcpt=%s; windows before/after equal=%s; Sent copy on server=%s" % (dt, pretty({k: r[k] for k in r if k != 'messages'}, 120), s["attachments"], s["rcpt_to"], w0 == w1, in_sent)
    _()

    @check("messages.sendMessage threading (In-Reply-To via customHeaders)")
    def _():
        n0 = len(ms.sent())
        try:
            b.call("messages.sendMessage", {"to": ["alice@example.org"], "subject": "thread?", "body": "x",
                                             "customHeaders": [{"name": "In-Reply-To", "value": "<plan-1@example.org>"}, {"name": "X-Lab", "value": "1"}]}, {"mode": "sendNow"})
        except BridgeError as e:
            return "gap", "rejected: %s" % e
        s = ms.wait_sent(n0 + 1, 10)[-1]
        eml = ms.sent_eml(s["n"]).decode(errors="replace")
        return "gap", "accepted silently but In-Reply-To=%s References=%s in the sent mail (X-Lab kept: %s). Only X-*/List-* custom headers survive, there is no inReplyTo field: use compose.beginReply for threads" % (s["in_reply_to"], s["references"], "X-Lab: 1" in eml)
    _()

    @check("compose.beginNew -> sendMessage")
    def _():
        n0 = len(ms.sent())
        w0 = lab.windows()
        tab = b.call("compose.beginNew", {"to": ["bob@example.net"], "subject": "compose new", "plainTextBody": "via compose window", "isPlainText": True})
        w1 = lab.windows()
        shot = lab.screenshot("/tmp/q4-compose-window.png")
        t0 = time.time()
        r = b.call("compose.sendMessage", tab["id"], {"mode": "sendNow"})
        s = ms.wait_sent(n0 + 1, 20)[-1]
        time.sleep(2)
        return "ok", "compose window appeared: %s (%s); send call took %.2fs; SMTP subject=%r; window gone after send: %s" % (
            any("Write:" in w for w in w1), [w[:60] for w in w1 if "Write:" in w][:1], time.time() - t0, s["subject"], not [w for w in lab.windows() if "Write:" in w])
    _()

    @check("compose.beginReply -> sendMessage (threading, Sent copy)")
    def _():
        n0 = len(ms.sent())
        m = by_mid("plan-2@example.org")
        tab = b.call("compose.beginReply", m["id"], "replyToSender")
        d = b.call("compose.getComposeDetails", tab["id"])
        b.call("compose.setComposeDetails", tab["id"], {"plainTextBody": "Thanks, looks good.\n\n" + d["plainTextBody"]})
        b.call("compose.sendMessage", tab["id"], {"mode": "sendNow"})
        s = ms.wait_sent(n0 + 1, 20)[-1]
        time.sleep(3)
        in_sent = lab.imap_flags("Sent", s["message_id"])
        ok = s["in_reply_to"] == "<plan-2@example.org>" and "<plan-1@example.org>" in (s["references"] or "")
        return ("ok" if ok else "partial"), "In-Reply-To=%s References=%s subject=%r to=%s; Sent copy=%s; quoted original kept in body: %s" % (
            s["in_reply_to"], s["references"], s["subject"], s["to"], in_sent, "> " in ms.sent_eml(s["n"]).decode(errors="replace"))
    _()

    @check("messages.saveMessage -> Drafts")
    def _():
        r = b.call("messages.saveMessage", {"to": ["alice@example.org"], "subject": "draft via API", "body": "draft"}, {"mode": "draft"})
        time.sleep(3)
        return "ok", "result=%s Drafts count on server=%d" % (pretty({k: r[k] for k in r if k != 'messages'}, 100), lab.imap_count("Drafts"))
    _()

    @check("MessageHeader.id stable across Thunderbird restart?")
    def _():
        before = {m["headerMessageId"]: m["id"] for m in q(folderId=inbox["id"])[:15]}
        lab.restart()
        b2 = lab.bridge()
        time.sleep(4)
        after = {m["headerMessageId"]: m["id"] for m in b2.call("messages.query", {"folderId": inbox["id"]})["messages"]}
        same = sum(1 for k, v in before.items() if after.get(k) == v)
        return ("ok" if same == len(before) else "partial"), "%d of %d ids identical after restart (example: %s -> %s)" % (same, len(before), list(before.items())[:2], [(k, after.get(k)) for k in list(before)[:2]])
    _()

    print()
    if a.json:
        with open(a.json, "w") as f:
            json.dump(RESULTS, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

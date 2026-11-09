#!/usr/bin/env python3
"""Q5: the profile that mail/engine.py writes, on a real Thunderbird against the lab's local mail server.

The engine (not make_profile.py) prepares the profile, seeds the account, starts Thunderbird headless and stops it;
bin/bombadil-mail-host is the real host; the lab's extension (a generic messenger.* bridge, installed as the add-on
by naming its id) and lab_bridge.Bridge stand in for the service. So this checks what a production start writes:
that the add-on loads from the .xpi, that its optional permission is granted without a prompt, that the account
syncs, and where Thunderbird puts the copy of a sent mail, a draft and an archived mail when the identity's folder
prefs are left unset (they are, so that Thunderbird finds the provider's own folders by special-use flag).

    python3 q5_engine_profile.py [--tb DIR_OF_THUNDERBIRD_BINARY] [--json out.json] [--keep]

Needs Dovecot (mailserver.py). Everything lives in /tmp/bombadil-q5 (removed unless --keep); loopback only.
"""
import argparse
import imaplib
import json
import os
import shutil
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

from lab_bridge import Bridge
from mailserver import PASSWORD, USER, MailServer

LAB_ID = "bombadil-mail-lab@bombadil.lab"
BASE = "/tmp/bombadil-q5"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def imap_count(port, folder):
    m = imaplib.IMAP4("127.0.0.1", port)
    try:
        m.login(USER, PASSWORD)
        typ, data = m.select(folder)
        return int(data[0]) if typ == "OK" else None
    finally:
        m.logout()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tb", default=os.path.expanduser("~/.cache/bombadil-lab/thunderbird-157.0"))
    ap.add_argument("--json")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE)
    os.chmod(BASE, 0o755)   # the mail server runs as `nobody` when started as root
    imap_port, smtp_port = free_port(), free_port()
    env = {"HOME": f"{BASE}/home", "BOMBADIL_STATE": f"{BASE}/state", "BOMBADIL_RUNTIME": f"{BASE}/run",
           "BOMBADIL_MAIL_PROFILE": f"{BASE}/profile", "BOMBADIL_MAIL_SOCKET": f"{BASE}/run/mail.sock"}
    os.environ.update(env)
    for var in ("DISPLAY", "WAYLAND_DISPLAY"):
        os.environ.pop(var, None)
    os.makedirs(f"{BASE}/run")

    import seed_logins

    from bombadil.mail import accounts, engine

    out = {}
    ms = MailServer(f"{BASE}/mail", imap_port, smtp_port)
    bridge = proc = None
    try:
        ms.start(seed=os.path.join(HERE, "seed"))
        proc = engine.ThunderbirdProcess(binary=os.path.join(args.tb, "thunderbird"),
                                         addon_dir=os.path.join(HERE, "extension"), addon_id=LAB_ID,
                                         host=os.path.join(REPO, "bin", "bombadil-mail-host"))
        lab_server = accounts.Provider("imap", "Lab", "127.0.0.1", imap_port, "none", "127.0.0.1", smtp_port, "none",
                                       "password", "", "", 20_000_000, "")
        proc.prepare()
        proc.seed_account({"id": "a1", "email": USER, "name": "", "sender": "Lab Tester"}, lab_server)
        seed_logins.seed(str(proc.profile), [("imap://127.0.0.1", "imap://127.0.0.1", USER, PASSWORD),
                                             ("smtp://127.0.0.1", "smtp://127.0.0.1", USER, PASSWORD)], args.tb)
        bridge = Bridge(env["BOMBADIL_MAIL_SOCKET"])
        t0 = time.time()
        proc.start()
        bridge.wait_connected(60)
        out["add-on connected after (s)"] = round(time.time() - t0, 2)
        out["hello frames"] = [f for f in bridge.frames if "op" in f or "hello" in f][:2]

        for _ in range(60):
            accts = bridge.call("accounts.list")
            inbox = bridge.call("folders.query", {"specialUse": ["inbox"]})
            if inbox and bridge.call("folders.getFolderInfo", inbox[0]["id"])["totalMessageCount"] >= 7:
                break
            time.sleep(0.5)
        out["accounts"] = [{"type": a["type"], "name": a["name"], "identities": [i["email"] for i in a["identities"]]}
                           for a in accts]
        out["special folders"] = {k: [f["path"] for f in bridge.call("folders.query", {"specialUse": [k]})]
                                  for k in ("inbox", "sent", "drafts", "archives", "trash", "junk", "templates")}

        n_sent0 = imap_count(imap_port, "Sent")
        r = bridge.call("messages.sendMessage", {"to": ["alice@example.org"], "subject": "q5 new", "body": "hi"},
                        {"mode": "sendNow"})
        out["sendMessage (optional permission granted by the engine)"] = sorted(r)
        time.sleep(4)
        out["Sent count on the server, before and after a new mail"] = [n_sent0, imap_count(imap_port, "Sent")]

        m = bridge.call("messages.query", {"headerMessageId": "plan-2@example.org"})["messages"][0]
        tab = bridge.call("compose.beginReply", m["id"], "replyToSender")
        bridge.call("compose.sendMessage", tab["id"], {"mode": "sendNow"}, timeout=60)
        time.sleep(4)
        sent = ms.sent()
        out["reply In-Reply-To"] = sent[-1]["in_reply_to"]
        out["Sent count after a reply"] = imap_count(imap_port, "Sent")

        m = bridge.call("messages.query", {"headerMessageId": "plan-1@example.org"})["messages"][0]
        bridge.call("messages.archive", [m["id"]])
        time.sleep(4)
        out["Archive count (flat)"] = imap_count(imap_port, "Archive")
        out["Archive/<year> exists"] = imap_count(imap_port, "Archive/2026")
        local = [f["path"] for a in bridge.call("accounts.list") if a["type"] == "none"
                 for f in bridge.call("folders.getSubFolders", a["id"], True)]
        out["Local Folders folders (copies must not land here)"] = local
        out["processes while up"] = proc.running()
    finally:
        t0 = time.time()
        if proc is not None:
            proc.stop()
            out["stop took (s)"] = round(time.time() - t0, 2)
            out["running after stop"] = proc.running()
        if bridge is not None:
            bridge.close()
        ms.stop()
        if proc is not None:
            out["profile files"] = sorted(os.listdir(str(proc.profile)))[:80]
            try:
                out["prefs.js mentions the account"] = USER in (proc.profile / "prefs.js").read_text()
                out["engine log tail"] = proc.log_path.read_text(errors="replace")[-1500:]
            except OSError as e:
                out["prefs.js"] = str(e)
    text = json.dumps(out, indent=1, default=str)
    print(text)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
    if not args.keep:
        shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    main()

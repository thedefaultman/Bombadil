#!/usr/bin/env python3
"""Q4 follow-up: how does mail that arrives in a folder OTHER than the Inbox (Archive, Junk, Sent, a filter target)
reach the add-on?  Thunderbird IDLEs only the selected/Inbox folder; other folders are visited by the periodic
check (mail.server.serverN.check_time, here 1 minute) and only if check_all_folders_for_new is on.

    python3 q4_other_folders.py [--json out.json] [--wait 150]

Runs three labs in sequence (control; mail.server.server1.check_all_folders_for_new=true; IMAP autosync/offline download off) and for each folder
records: seconds until the local folder count grew, seconds until messages.query({online:false}) found the message,
whether messages.onNewMailReceived fired for it.
"""
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labtest import Lab  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDERS = ("INBOX", "Archive", "Junk", "Sent")


def one_run(lab, label, extra, wait):
    tmp = tempfile.mkdtemp(prefix="q4of-")
    pf = os.path.join(tmp, "x.js")
    open(pf, "w").write(extra + "\n")
    lab.start(LAB_EXTRA_PREFS=pf)
    b = lab.bridge()
    time.sleep(3)
    b.listen("messages.onNewMailReceived")
    try:
        b.listen("folders.onFolderInfoChanged")
    except Exception as e:  # noqa: BLE001
        print("cannot listen to folders.onFolderInfoChanged: %s" % e)
    base = open(os.path.join(HERE, "seed", "01-plain-welcome.eml"), "rb").read()
    folders = {f["path"]: f for f in [x for a in b.call("accounts.list") if a["type"] == "imap" for x in b.call("folders.getSubFolders", a["id"], False)]}
    t0 = time.time()
    want = {}
    for name in FOLDERS:
        path = "/INBOX" if name == "INBOX" else "/" + name
        if path not in folders:
            want[name] = {"error": "folder %s not in %s" % (path, sorted(folders))}
            continue
        mid = "of-%s-%d" % (name.lower(), int(t0))
        lab.ms.deliver(base.replace(b"welcome-1@", (mid.split("@")[0] + "@").encode()).replace(b"Welcome to the lab", ("OTHERFOLDER %s" % name).encode()), name)
        want[name] = {"path": path, "mid": mid, "count0": b.call("folders.getFolderInfo", folders[path]["id"])["totalMessageCount"]}
    res = {n: {"count grew after (s)": None, "found by query after (s)": None, "onNewMailReceived": None} for n in want if "error" not in want[n]}
    while time.time() - t0 < wait:
        for n, w in want.items():
            if "error" in w:
                continue
            r = res[n]
            if r["count grew after (s)"] is None and b.call("folders.getFolderInfo", folders[w["path"]]["id"])["totalMessageCount"] > w["count0"]:
                r["count grew after (s)"] = round(time.time() - t0, 1)
            if r["found by query after (s)"] is None and b.call("messages.query", {"folderId": folders[w["path"]]["id"], "headerMessageId": w["mid"] + "@example.org"})["messages"]:
                r["found by query after (s)"] = round(time.time() - t0, 1)
        if all(r["count grew after (s)"] is not None for r in res.values()):
            break
        time.sleep(2)
    for n, w in want.items():
        if "error" in w:
            res[n] = w
            continue
        evs = [e for e in b.events if e["event"] == "messages.onNewMailReceived" and e["args"][0]["path"] == w["path"] and e["t"] / 1000 >= t0]
        res[n]["onNewMailReceived"] = ("%.1fs" % (evs[0]["t"] / 1000 - t0)) if evs else "no event"
        fe = [e for e in b.events if e["event"] == "folders.onFolderInfoChanged" and e["args"][0]["path"] == w["path"] and e["t"] / 1000 >= t0
              and (e["args"][1] or {}).get("totalMessageCount", 0) > w["count0"]]
        res[n]["folders.onFolderInfoChanged (total grew)"] = ("%.1fs" % (fe[0]["t"] / 1000 - t0)) if fe else "no event"
    # explicit online query for what is still unseen locally
    for n, w in want.items():
        if "error" in w or res[n]["found by query after (s)"] is not None:
            continue
        got = b.call("messages.query", {"folderId": folders[w["path"]]["id"], "headerMessageId": w["mid"] + "@example.org", "online": True}, timeout=60)["messages"]
        res[n]["query(online:true) finds it"] = bool(got)
    print("[%s] %s" % (label, json.dumps(res)), flush=True)
    lab.stop()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--wait", type=int, default=100)
    a = ap.parse_args()
    lab = Lab()
    out = {}
    out["control (defaults: check_all_folders_for_new unset)"] = one_run(lab, "control", "", a.wait)
    out["check_all_folders_for_new=true"] = one_run(lab, "check_all", 'user_pref("mail.server.server1.check_all_folders_for_new", true);', a.wait)
    out["autosync_offline_stores=false, offline_download=false"] = one_run(
        lab, "no_autosync", 'user_pref("mail.server.server1.autosync_offline_stores", false);\nuser_pref("mail.server.server1.offline_download", false);', a.wait)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()

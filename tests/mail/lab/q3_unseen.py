#!/usr/bin/env python3
"""Q3/Q5: can Thunderbird run "unseen"?  --headless, unmapped/minimised windows, footprint, first-run windows.

    python3 q3_unseen.py headless|hidden|footprint|firstrun|compose|all [--json out.json]

  headless   --headless with NO X display at all: starts? syncs IMAP? runs the add-on? new-mail events? compose+send?
  hidden     normal window on Xvfb, then xdotool windowunmap / openbox minimise: still syncing / running the add-on?
  footprint  cold start to "add-on connected", idle RSS/PSS and CPU after 2 min, for windowed / unmapped / headless
  firstrun   what pops up when the quiet prefs are missing (and with no account), and which single pref suppresses what
  compose    Q5: compose.sendMessage with the compose window never shown / minimised / unfocused / unmapped
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labtest import Lab, pretty  # noqa: E402

OUT = {}


def say(section, key, value):
    OUT.setdefault(section, {})[key] = value
    print("[%s] %s: %s" % (section, key, value if isinstance(value, str) else json.dumps(value, default=str)), flush=True)


def xdo(lab, *args):
    return subprocess.run(["xdotool", *args], capture_output=True, text=True, env=lab.env()).stdout.strip()


def main_window(lab):
    ids = xdo(lab, "search", "--name", "Mozilla Thunderbird").split()
    return ids[0] if ids else None


def new_mail_roundtrip(lab, b, label):
    base = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", "01-plain-welcome.eml"), "rb").read()
    b.listen("messages.onNewMailReceived")
    t0 = time.time()
    tag = "NEWMAIL %s %d" % (label, int(t0))
    lab.ms.deliver(base.replace(b"welcome-1@", b"nm-%d@" % int(t0)).replace(b"Welcome to the lab", tag.encode()))
    try:
        ev = b.wait_event("messages.onNewMailReceived", 60, since=t0, where=lambda e: any(tag == x["subject"] for x in e["args"][1]["messages"]))
        return "event after %.2fs" % (ev["t"] / 1000 - t0)
    except TimeoutError:
        return "NO event in 60s"


def reply_and_send(lab, b, prepare=None):
    """compose.beginReply + sendMessage; `prepare(tab)` runs between them (minimise/unmap/unfocus)."""
    n0 = len(lab.ms.sent())
    m = b.call("messages.query", {"headerMessageId": "plan-2@example.org"})["messages"][0]
    t0 = time.time()
    tab = b.call("compose.beginReply", m["id"], "replyToSender")
    t_begin = time.time() - t0
    info = prepare(tab) if prepare else None
    t1 = time.time()
    b.call("compose.sendMessage", tab["id"], {"mode": "sendNow"}, timeout=60)
    s = lab.ms.wait_sent(n0 + 1, 30)[-1]
    return {"beginReply_s": round(t_begin, 2), "sendMessage_s": round(time.time() - t1, 2), "prepare": info,
            "in_reply_to": s["in_reply_to"], "smtp_ok": True}


# ---------------------------------------------------------------------------------------------
def t_headless(lab):
    t = time.time()
    try:
        lab.start(LAB_HEADLESS=1, LAB_NO_DISPLAY=1, LAB_WAIT=40)
    except RuntimeError as e:
        say("headless", "start", "FAILED: %s; tb.log: %s" % (str(e)[-200:], open(os.path.join(lab.dir, "tb.log"), errors="replace").read()[-600:]))
        lab.stop()
        return
    say("headless", "start", "add-on connected %.1fs after launch; DISPLAY unset; process alive: %s" % (time.time() - t, bool(lab.tb_pids())))
    b = lab.bridge()
    time.sleep(3)
    say("headless", "accounts/folders", pretty([(a["id"], a["type"]) for a in b.call("accounts.list")]))
    say("headless", "IMAP synced", "Inbox totalMessageCount=%s (seed has 7)" % b.call("folders.getFolderInfo", "account1://INBOX")["totalMessageCount"])
    say("headless", "new mail", new_mail_roundtrip(lab, b, "headless"))
    n0 = len(lab.ms.sent())
    b.call("messages.sendMessage", {"to": ["alice@example.org"], "subject": "headless bg send", "body": "x"}, {"mode": "sendNow"})
    say("headless", "messages.sendMessage", "SMTP received: %s" % bool(lab.ms.wait_sent(n0 + 1, 20)))
    try:
        say("headless", "compose.beginReply+sendMessage", reply_and_send(lab, b))
    except Exception as e:  # noqa
        say("headless", "compose.beginReply+sendMessage", "FAILED: %s" % e)
    say("headless", "windows API", pretty([(w["id"], w["type"], w["state"]) for w in b.call("windows.getAll")]))
    time.sleep(20)
    say("headless", "still alive after 20s more", bool(lab.tb_pids()) and b.ping()["pong"])
    lab.stop()


def t_hidden(lab):
    lab.start(LAB_WM="openbox")
    b = lab.bridge()
    time.sleep(3)
    w = main_window(lab)
    say("hidden", "main window id", w)
    # 1. unmap (closest X11 stand-in for a Hyprland special workspace that nobody visits)
    xdo(lab, "windowunmap", w)
    time.sleep(1)
    say("hidden", "after windowunmap: xwininfo map state", subprocess.run(["xwininfo", "-id", w], capture_output=True, text=True, env=lab.env()).stdout.count("IsUnMapped") and "IsUnMapped" or "mapped")
    say("hidden", "unmapped: API", pretty(b.call("folders.getFolderInfo", "account1://INBOX")))
    say("hidden", "unmapped: new mail", new_mail_roundtrip(lab, b, "unmapped"))
    try:
        say("hidden", "unmapped: background send", bool(b.call("messages.sendMessage", {"to": ["alice@example.org"], "subject": "unmapped send", "body": "x"}, {"mode": "sendNow"}) is not None))
    except Exception as e:  # noqa
        say("hidden", "unmapped: background send", "FAILED %s" % e)
    xdo(lab, "windowmap", w)
    time.sleep(1)
    # 2. minimise through the window manager
    xdo(lab, "windowminimize", w)
    time.sleep(1)
    xprop = subprocess.run(["xprop", "-id", w, "_NET_WM_STATE"], capture_output=True, text=True, env=lab.env()).stdout.strip()
    say("hidden", "after windowminimize (openbox)", xprop)
    say("hidden", "minimised: new mail", new_mail_roundtrip(lab, b, "minimised"))
    say("hidden", "minimised: API", pretty(b.call("messages.query", {"folderId": "account1://INBOX", "unread": True})["messages"][:1]))
    lab.stop()


def sample(lab, seconds):
    c0, t0 = lab.cpu_seconds(), time.time()
    time.sleep(seconds)
    return (lab.cpu_seconds() - c0) / (time.time() - t0) * 100.0


def t_footprint(lab):
    rows = {}
    for label, env, hide in (("windowed (Xvfb, no WM)", {}, False), ("windowed, then unmapped", {}, True), ("--headless, no display", {"LAB_HEADLESS": 1, "LAB_NO_DISPLAY": 1}, False)):
        starts = []
        for i in range(3):  # cold = fresh profile each time (file cache warm)
            lab.start(LAB_WAIT=40, **env)
            # launch of Thunderbird -> the add-on's hello frame reaching the host (fresh profile, warm file cache)
            starts.append(round(os.path.getmtime(os.path.join(lab.dir, "hello.json")) - float(open(os.path.join(lab.dir, "start.time")).read()), 2))
            if i < 2:
                lab.stop()
        b = lab.bridge()
        if hide:
            xdo(lab, "windowunmap", main_window(lab))
        time.sleep(110)
        rss, pss, cpu = lab.rss_mb(), lab.pss_mb(), sample(lab, 20)
        nproc = len(lab.tb_pids())
        rows[label] = {"cold start -> add-on connected (s, 3 runs)": starts, "processes": nproc, "RSS sum MB": round(rss, 0), "PSS sum MB": round(pss, 0),
                       "CPU % of one core, avg over 20 s at t=2min": round(cpu, 1)}
        say("footprint", label, rows[label])
        lab.stop()
    # warm restart of an existing profile
    lab.start()
    t = time.time()
    lab.restart()
    say("footprint", "restart on existing profile -> add-on connected (s)", round(time.time() - t, 2))
    lab.stop()


def snapshot(lab, name, b):
    tabs = []
    try:
        for w in b.call("windows.getAll", {"populate": True}):
            tabs.append((w["type"], [t.get("type") + ":" + str(t.get("url", ""))[:50] for t in w.get("tabs", [])]))
    except Exception as e:  # noqa
        tabs = ["windows.getAll failed %s" % e]
    lab.screenshot("/tmp/q3-%s.png" % name)
    return {"x windows": [w[:70] for w in lab.windows()], "tabs": tabs}


def t_firstrun(lab):
    runs = [
        ("baseline (all quiet prefs)", {}),
        ("NO quiet prefs, account present", {"LAB_NO_QUIET": 1}),
        ("NO account, quiet prefs incl. suppress_dialog_on_startup", {"LAB_KIND": "none"}),
        ("NO account, NO quiet prefs (true first run)", {"LAB_KIND": "none", "LAB_NO_QUIET": 1}),
        ("ablate mail.shell.checkDefaultClient", {"LAB_ABLATE": "mail.shell.checkDefaultClient"}),
        ("ablate mailnews.start_page.enabled", {"LAB_ABLATE": "mailnews.start_page.enabled"}),
        ("ablate mail.rights.version", {"LAB_ABLATE": "mail.rights.version"}),
        ("ablate mail.provider.suppress_dialog_on_startup (account present)", {"LAB_ABLATE": "mail.provider.suppress_dialog_on_startup"}),
        ("ablate mail.chat.enabled + inappnotifications", {"LAB_ABLATE": "mail.chat.enabled,mail.inappnotifications.enabled"}),
    ]
    for name, env in runs:
        try:
            lab.start(LAB_WAIT=25, **env)
            b = lab.bridge()
            time.sleep(6)
            say("firstrun", name, snapshot(lab, name.split()[0] + str(len(OUT.get("firstrun", {}))), b))
        except Exception as e:  # noqa
            say("firstrun", name, "no add-on/hello: %s" % str(e)[-150:])
            lab.screenshot("/tmp/q3-%s.png" % name.split()[0])
            say("firstrun", name + " [x windows]", [w[:70] for w in lab.windows()])
        lab.stop()


def t_compose(lab):
    lab.start(LAB_WM="openbox")
    b = lab.bridge()
    time.sleep(3)
    xprop = lambda wid: subprocess.run(["xprop", "-id", wid, "_NET_WM_STATE"], capture_output=True, text=True, env=lab.env()).stdout.strip()  # noqa: E731

    def compose_wid():
        ids = xdo(lab, "search", "--name", "Write:").split()
        return ids[0] if ids else None

    say("compose", "A normal (window visible and focused)", reply_and_send(lab, b))

    def minimise_api(tab):
        b.call("windows.update", tab["windowId"], {"state": "minimized"})
        time.sleep(1)
        w = compose_wid()
        return "windows.update(minimized) -> %s" % (xprop(w) if w else "no X window")
    say("compose", "B minimised via windows.update before send", reply_and_send(lab, b, minimise_api))

    def minimise_wm(tab):
        w = compose_wid()
        xdo(lab, "windowminimize", w)
        time.sleep(1)
        return "xdotool windowminimize -> %s" % xprop(w)
    say("compose", "C minimised via window manager before send", reply_and_send(lab, b, minimise_wm))

    def unmap(tab):
        w = compose_wid()
        xdo(lab, "windowunmap", w)
        time.sleep(1)
        return "xdotool windowunmap; xwininfo: %s" % ("IsUnMapped" if "IsUnMapped" in subprocess.run(["xwininfo", "-id", w], capture_output=True, text=True, env=lab.env()).stdout else "mapped")
    say("compose", "D unmapped (hidden workspace stand-in) before send", reply_and_send(lab, b, unmap))

    def unfocus(tab):
        # put focus on the main window so the compose window never has it
        mw = main_window(lab)
        xdo(lab, "windowfocus", mw)
        time.sleep(1)
        return "focused window now: %s (compose window id %s)" % (xdo(lab, "getwindowfocus"), compose_wid())
    say("compose", "E compose window not focused (focus moved to main window)", reply_and_send(lab, b, unfocus))

    def nofocus_update(tab):
        b.call("windows.update", tab["windowId"], {"focused": False})
        return "windows.update(focused:false); focus=%s" % xdo(lab, "getwindowfocus")
    say("compose", "F windows.update focused:false", reply_and_send(lab, b, nofocus_update))
    # beginNew variants for a fresh message
    n0 = len(lab.ms.sent())
    tab = b.call("compose.beginNew", {"to": ["bob@example.net"], "subject": "new hidden", "plainTextBody": "hi", "isPlainText": True})
    w = compose_wid()
    xdo(lab, "windowunmap", w)
    b.call("compose.sendMessage", tab["id"], {"mode": "sendNow"}, timeout=60)
    say("compose", "G beginNew + unmap + send", "SMTP received: %s" % bool(lab.ms.wait_sent(n0 + 1, 30)))
    lab.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="all")
    ap.add_argument("--json")
    a = ap.parse_args()
    lab = Lab()
    todo = ["headless", "hidden", "compose", "firstrun", "footprint"] if a.what == "all" else [a.what]
    for w in todo:
        print("=== %s" % w, flush=True)
        globals()["t_" + w](lab)
    if a.json:
        json.dump(OUT, open(a.json, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()

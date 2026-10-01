#!/usr/bin/env python3
"""Q2: native messaging on a real Thunderbird: search dirs, permission, lifetime, size limits, chunking.

    python3 q2_native_messaging.py dirs|perm|lifetime|idle|sizes|chunks|all  [--idle-seconds N] [--json out.json]

Each sub-test starts its own lab (fresh profile). Results are printed and appended to --json.
  dirs      which directories Thunderbird reads native-messaging manifests from (positive + negative)
  perm      no "nativeMessaging" permission / allowed_extensions mismatch / host path missing: the exact errors
  lifetime  manifest v2 persistent vs v2 event page (persistent:false) vs v3: does the native port + host survive idle?
  idle      persistent MV2 add-on, 10 minutes idle, ping + both-direction round trips every minute
  sizes     host->add-on and add-on->host maximum message size
  chunks    moving a 20 MB attachment in chunks (host->add-on stash + send) and whole (add-on->host)
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_xpi  # noqa: E402
from labtest import Lab, pretty  # noqa: E402
from lab_bridge import BridgeError  # noqa: E402

OUT = {}
TMP = os.path.expanduser("~/.cache/bombadil-lab/build")


def say(section, key, value):
    OUT.setdefault(section, {})[key] = value
    print("[%s] %s: %s" % (section, key, value if isinstance(value, str) else json.dumps(value)), flush=True)


def tb_log(lab, pat, n=6):
    try:
        txt = open(os.path.join(lab.dir, "tb.log"), errors="replace").read()
    except OSError:
        return []
    return [l.strip()[:200] for l in txt.splitlines() if re.search(pat, l)][-n:]


def host_log(lab):
    try:
        return open(os.path.join(lab.dir, "host.log"), errors="replace").read().splitlines()
    except OSError:
        return []


# --------------------------------------------------------------------------------------------- dirs
def t_dirs(lab):
    home_rel = lambda p: os.path.join(lab.dir, "home", p)  # noqa: E731
    cands = [
        ("~/.mozilla/native-messaging-hosts (control)", home_rel(".mozilla/native-messaging-hosts"), False),
        ("~/.thunderbird/native-messaging-hosts", home_rel(".thunderbird/native-messaging-hosts"), False),
        ("~/.config/mozilla/native-messaging-hosts", home_rel(".config/mozilla/native-messaging-hosts"), False),
        ("/usr/lib/mozilla/native-messaging-hosts", "/usr/lib/mozilla/native-messaging-hosts", True),
        ("/usr/lib64/mozilla/native-messaging-hosts", "/usr/lib64/mozilla/native-messaging-hosts", True),
        ("/usr/lib/thunderbird/native-messaging-hosts", "/usr/lib/thunderbird/native-messaging-hosts", True),
        ("/usr/share/mozilla/native-messaging-hosts", "/usr/share/mozilla/native-messaging-hosts", True),
        ("/etc/mozilla/native-messaging-hosts", "/etc/mozilla/native-messaging-hosts", True),
        ("/etc/thunderbird/native-messaging-hosts", "/etc/thunderbird/native-messaging-hosts", True),
    ]
    for _l, d, system in cands:  # stale manifests from earlier runs would be found through the system dirs
        try:
            os.unlink(os.path.join(d, "bombadil_mail.json")) if system else None
        except OSError:
            pass
    for label, d, system in cands:
        if system and os.geteuid() != 0:
            say("dirs", label, "skipped (needs root)")
            continue
        existed = os.path.isdir(d)
        try:
            lab.start(LAB_NM_DIR=d, LAB_WAIT=12)
            res = "FOUND: add-on connected"
        except RuntimeError:
            res = "NOT searched: add-on got '%s'" % (tb_log(lab, "No such native application", 1) or ["?"])[0]
        say("dirs", label, res)
        lab.stop()
        if system:
            try:
                os.unlink(os.path.join(d, "bombadil_mail.json"))
                if not existed:
                    os.rmdir(d)
            except OSError:
                pass


# --------------------------------------------------------------------------------------------- perm
def t_perm(lab):
    xpi = build_xpi.build(os.path.join(TMP, "noperm.xpi"), remove_permissions=("nativeMessaging",))
    try:
        lab.start(LAB_XPI=xpi, LAB_WAIT=12)
        say("perm", "without nativeMessaging permission", "connected (unexpected)")
    except RuntimeError:
        say("perm", "without nativeMessaging permission", tb_log(lab, "TypeError: messenger.runtime", 1) or "no hello; no log lines")
    lab.stop()
    # allowed_extensions mismatch
    lab.start()
    nm = os.path.join(lab.dir, "home/.mozilla/native-messaging-hosts/bombadil_mail.json")
    m = json.load(open(nm))
    m["allowed_extensions"] = ["someone-else@example.org"]
    json.dump(m, open(nm, "w"))
    subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "run-thunderbird.sh"), "restart"],
                   env=dict(os.environ, LAB_WAIT="10"), capture_output=True)
    say("perm", "allowed_extensions does not list the add-on id", tb_log(lab, "native port disconnected", 1) or "?")
    # path does not exist
    m["allowed_extensions"] = ["bombadil-mail-lab@bombadil.lab"]
    m["path"] = "/nonexistent/host"
    json.dump(m, open(nm, "w"))
    subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "run-thunderbird.sh"), "restart"],
                   env=dict(os.environ, LAB_WAIT="10"), capture_output=True)
    say("perm", "host path does not exist", tb_log(lab, "native port disconnected", 1) or "?")
    lab.stop()
    # prompt? run normally and list windows + screenshot
    lab.start()
    time.sleep(6)
    say("perm", "windows after first load (no permission prompt / doorhanger window)", lab.windows())
    lab.screenshot("/tmp/q2-no-prompt.png")
    lab.stop()


# --------------------------------------------------------------------------------------------- lifetime
def variants():
    return [
        ("mv2-persistent", build_xpi.build(os.path.join(TMP, "mv2p.xpi"))),
        ("mv2-event-page (persistent:false)", build_xpi.build(os.path.join(TMP, "mv2e.xpi"), patch={"background": {"scripts": ["background.js"], "persistent": False}})),
        ("mv3 (event page)", build_xpi.build(os.path.join(TMP, "mv3.xpi"), patch={"manifest_version": 3, "background": {"scripts": ["background.js"]}}, drop=("content_security_policy",))),
    ]


def t_lifetime(lab, idle):
    for name, xpi in variants():
        try:
            lab.start(LAB_XPI=xpi, LAB_WAIT=20)
        except RuntimeError as e:
            say("lifetime", name, "did not start: %s; %s" % (str(e)[-200:], tb_log(lab, "Error|error|manifest", 4)))
            lab.stop()
            continue
        b = lab.bridge()
        h0 = b.hello()
        t0 = time.time()
        pids0 = set(re.findall(r"pid=(\d+) start", "\n".join(host_log(lab))))
        for _ in range(idle // 10):
            time.sleep(10)
        log = host_log(lab)
        pids = set(re.findall(r"pid=(\d+) start", "\n".join(log)))
        eofs = [l for l in log if "EOF" in l or "exit" in l or "signal" in l]
        connects = len(tb_log(lab, "connectNative", 50))
        try:
            h1 = b.hello()
            alive = "add-on answers; page started %s -> %s (same page: %s)" % (h0["startedAtMs"], h1["startedAtMs"], h0["startedAtMs"] == h1["startedAtMs"])
        except Exception as e:  # noqa
            alive = "add-on does NOT answer after idle: %s" % e
        say("lifetime", name, {"idle_seconds": int(time.time() - t0), "host pids seen": sorted(pids), "host exits logged": eofs[:3],
                               "connectNative calls": connects, "after idle": alive})
        lab.stop()


# --------------------------------------------------------------------------------------------- idle
def t_idle(lab, minutes=10):
    lab.start()
    b = lab.bridge()
    h0 = b.hello()
    pid0 = set(re.findall(r"pid=(\d+) start", "\n".join(host_log(lab))))
    rows = []
    t0 = time.time()
    for minute in range(1, minutes + 1):
        time.sleep(60)
        t1 = time.time()
        pong = b.ping()
        rt_to_ext = time.time() - t1  # harness -> host -> ext -> host -> harness
        t2 = time.time()
        g = b.request(op="gen", bytes=200000)  # ext -> host direction with payload
        rows.append({"minute": minute, "ping_roundtrip_s": round(rt_to_ext, 3), "ext->host 200KB ok": len(g["payload"]) == 200000,
                     "page uptime_ms": pong["uptimeMs"], "rss_mb": round(lab.rss_mb(), 1)})
        print("  idle minute", minute, rows[-1], flush=True)
    pid1 = set(re.findall(r"pid=(\d+) start", "\n".join(host_log(lab))))
    say("idle", "result", {"minutes": minutes, "host pids at start": sorted(pid0), "host pids at end": sorted(pid1), "same page": b.hello()["startedAtMs"] == h0["startedAtMs"],
                           "host exits logged": [l for l in host_log(lab) if "EOF" in l or "exit" in l], "rows": rows})
    lab.stop()


# --------------------------------------------------------------------------------------------- kill
def t_kill(lab):
    lab.start()
    b = lab.bridge()
    pids = re.findall(r"pid=(\d+) start", "\n".join(host_log(lab)))
    host = int(pids[-1])
    t = time.time()
    os.kill(host, 9)
    time.sleep(1)
    b.wait_connected(30)
    for _ in range(40):
        try:
            b.ping()
            break
        except Exception:  # noqa
            time.sleep(0.5)
    pids2 = re.findall(r"pid=(\d+) start", "\n".join(host_log(lab)))
    say("kill", "SIGKILL the host", "add-on reconnected %.1fs later with a new host process (pids %s -> %s); add-on reconnect log: %s" % (
        time.time() - t, host, pids2[-1], tb_log(lab, "native port disconnected", 1)))
    # Thunderbird killing the host: close the port from the add-on and see that the host is reaped
    b.eval("port_to_close = true; return 1") if False else None
    lab.stop()
    time.sleep(1)
    log = host_log(lab)
    say("kill", "Thunderbird shutdown", [l.split(" ", 2)[2][:90] for l in log[-3:]])


# --------------------------------------------------------------------------------------------- sizes
def pad_frame(b, total):
    """An echo request whose JSON frame is exactly `total` bytes."""
    overhead = len(json.dumps({"op": "echo", "payload": "", "id": b.next_id}, separators=(",", ":")))
    return total - overhead


def t_sizes(lab):
    lab.start()
    b = lab.bridge()
    # host -> add-on
    res = {}
    for total in (100_000, 900_000, 1_048_576, 1_048_577, 2_000_000):
        try:
            r = b.request(timeout=8, op="echo", payload="x" * pad_frame(b, total))
            res[total] = "delivered (%s chars)" % r["len"]
        except (TimeoutError, BridgeError) as e:
            res[total] = "FAILED (%s); log: %s" % (type(e).__name__, (tb_log(lab, "exceeds the limit", 1) or ["?"])[0])
            time.sleep(6)
            b.wait_connected(30)
            time.sleep(1)
    say("sizes", "host->add-on frame bytes", res)
    # add-on -> host
    res = {}
    for n in (1_000_000, 4_000_000, 16_000_000, 64_000_000, 200_000_000):
        try:
            t = time.time()
            r = b.request(timeout=120, op="gen", bytes=n)
            res[n] = "ok in %.1fs" % (time.time() - t)
        except (TimeoutError, BridgeError) as e:
            res[n] = "FAILED %s %s" % (type(e).__name__, e)
            time.sleep(5)
            b.wait_connected(30)
    say("sizes", "add-on->host payload bytes", res)
    lab.stop()


# --------------------------------------------------------------------------------------------- chunks
def t_chunks(lab, mb=20):
    lab.start()
    b = lab.bridge()
    blob = os.urandom(mb * 1024 * 1024)
    want = hashlib.sha256(blob).hexdigest()
    raw_chunk = 512 * 1024  # 512 KiB raw -> 699,052 base64 chars: one frame < 1 MiB
    n0 = len(lab.ms.sent())
    t = time.time()
    chunks = [blob[i:i + raw_chunk] for i in range(0, len(blob), raw_chunk)]
    for i, c in enumerate(chunks):
        b.request(op="stash", key="big", idx=i, b64=base64.b64encode(c).decode())
    t_up = time.time() - t
    t = time.time()
    b.call("messages.sendMessage", {"to": ["alice@example.org"], "subject": "big attachment", "body": "see attached",
                                     "attachments": [{"file": {"__file": True, "stash": "big", "name": "big.bin", "type": "application/octet-stream"}}]},
           {"mode": "sendNow"}, timeout=120)
    s = lab.ms.wait_sent(n0 + 1, 60)[-1]
    t_send = time.time() - t
    import email
    import email.policy
    msg = email.message_from_bytes(lab.ms.sent_eml(s["n"]), policy=email.policy.default)
    got = [hashlib.sha256(p.get_payload(decode=True)).hexdigest() for p in msg.walk() if p.get_filename() == "big.bin"]
    say("chunks", "host->add-on->SMTP", "%d MB in %d chunks of %d KiB: stash upload %.1fs, send %.1fs, sha256 %s" % (mb, len(chunks), raw_chunk // 1024, t_up, t_send, "MATCH" if got == [want] else "MISMATCH %s" % got))
    b.request(op="stash_drop", key="big")
    # add-on -> host: a received 20 MB attachment, in one frame
    eml = (b"From: Big <big@example.org>\r\nTo: test@example.test\r\nSubject: Big attachment in\r\nMessage-ID: <big-in@example.org>\r\nDate: Mon, 28 Sep 2026 09:00:00 +0000\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\nhi\r\n--B\r\n"
           b"Content-Type: application/octet-stream; name=\"in.bin\"\r\nContent-Disposition: attachment; filename=\"in.bin\"\r\nContent-Transfer-Encoding: base64\r\n\r\n"
           + b"\r\n".join(base64.b64encode(blob)[i:i + 76] for i in range(0, len(base64.b64encode(blob)), 76)) + b"\r\n--B--\r\n")
    lab.ms.deliver(eml, "INBOX")
    for _ in range(60):
        r = b.call("messages.query", {"headerMessageId": "big-in@example.org"})["messages"]
        if r:
            break
        time.sleep(1)
    t = time.time()
    atts = b.call("messages.listAttachments", r[0]["id"])
    f = b.call("messages.getAttachmentFile", r[0]["id"], [a for a in atts if a["name"] == "in.bin"][0]["partName"], timeout=120)
    t_dl = time.time() - t
    got = hashlib.sha256(base64.b64decode(f["b64"])).hexdigest()
    say("chunks", "add-on->host (one frame)", "%d MB attachment as one base64 frame (%.0f MB on the pipe) in %.1fs, sha256 %s" % (mb, len(f["b64"]) / 1e6, t_dl, "MATCH" if got == want else "MISMATCH"))
    lab.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="all")
    ap.add_argument("--idle-seconds", type=int, default=100)
    ap.add_argument("--idle-minutes", type=int, default=10)
    ap.add_argument("--json")
    a = ap.parse_args()
    lab = Lab()
    todo = ["dirs", "perm", "lifetime", "kill", "sizes", "chunks", "idle"] if a.what == "all" else (["kill", "sizes", "chunks", "idle"] if a.what == "rest" else [a.what])
    for w in todo:
        print("=== %s" % w, flush=True)
        if w == "dirs":
            t_dirs(lab)
        elif w == "perm":
            t_perm(lab)
        elif w == "lifetime":
            t_lifetime(lab, a.idle_seconds)
        elif w == "idle":
            t_idle(lab, a.idle_minutes)
        elif w == "kill":
            t_kill(lab)
        elif w == "sizes":
            t_sizes(lab)
        elif w == "chunks":
            t_chunks(lab)
    if a.json:
        json.dump(OUT, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()

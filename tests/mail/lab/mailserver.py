#!/usr/bin/env python3
"""Local mail server for the Bombadil Mail lab: Dovecot (IMAP) + a Python SMTP sink.

No external network, no TLS, one fixed user.

    IMAP  127.0.0.1:1143   LOGIN test@example.test / lab     (Dovecot 2.3, plain, no STARTTLS)
    SMTP  127.0.0.1:1025   any AUTH accepted, nothing relayed (the "sink")

Folders (Maildir, LAYOUT=fs, hierarchy delimiter "/"):
    INBOX  Drafts  Sent  Trash  Archive  Junk      (RFC 6154 special-use flags set)

Dovecot runs as an unprivileged user. If this script is started as root it
chowns its work dir to `nobody` and drops privileges before starting anything
(Dovecot then takes the same code path as for any non-root user).

What arrives over SMTP is recorded in <dir>/smtp/sent.json (list of records,
see SmtpSink._record) and <dir>/smtp/NNNN.eml (the exact bytes of DATA). A
mail addressed to the test user is also delivered into its INBOX, so a reply
to yourself shows up as new mail.

CLI (all commands take --dir, default $BOMBADIL_LAB_DIR/mail, else /tmp/bombadil-lab/mail):
    mailserver.py serve   [--seed DIR]        run in the foreground (what run-thunderbird.sh uses)
    mailserver.py stop
    mailserver.py seed DIR                    deliver DIR/**/*.eml (see seed())
    mailserver.py deliver FILE.eml [--folder INBOX] [--seen]
    mailserver.py sent [--wait N --timeout S] print the SMTP captures as JSON
    mailserver.py status

Python: `from mailserver import MailServer; s = MailServer(dir); s.start(seed=...);
s.deliver(raw); s.wait_sent(1); s.sent(); s.stop()`.

Seeding conventions (seed()): a top-level *.eml goes to INBOX; a sub-directory
named after a folder (Archive/, Sent/, ...) delivers into that folder; a file
named x.seen.eml is delivered as read, x.flagged.eml as flagged (may combine:
x.seen.flagged.eml). Bytes are delivered verbatim.
"""
import argparse
import asyncio
import email
import email.policy
import json
import os
import pwd
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

USER = "test@example.test"
PASSWORD = "lab"
IMAP_PORT = 1143
SMTP_PORT = 1025
FOLDERS = {  # name -> RFC 6154 special-use (None for none)
    "Drafts": "\\Drafts",
    "Sent": "\\Sent",
    "Trash": "\\Trash",
    "Archive": "\\Archive",
    "Junk": "\\Junk",
}
DEFAULT_DIR = os.path.join(os.environ.get("BOMBADIL_LAB_DIR", "/tmp/bombadil-lab"), "mail")

_seq_lock = threading.Lock()
_seq = 0


def _unique(size):
    global _seq
    with _seq_lock:
        _seq += 1
        n = _seq
    return "%d.M%dP%dQ%d.lab,S=%d" % (int(time.time()), int((time.time() % 1) * 1e6), os.getpid(), n, size)


class Maildirs:
    """Deliver raw messages into the test user's Maildir (LAYOUT=fs)."""

    def __init__(self, root):
        self.root = root  # .../mail/<user>

    def folder_dir(self, folder):
        return self.root if folder.upper() == "INBOX" else os.path.join(self.root, folder)

    def deliver(self, raw, folder="INBOX", flags=""):
        d = self.folder_dir(folder)
        for sub in ("tmp", "new", "cur"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        name = _unique(len(raw))
        tmp = os.path.join(d, "tmp", name)
        with open(tmp, "wb") as f:
            f.write(raw)
        if flags:  # read/flagged etc. go straight to cur/ with info suffix
            final = os.path.join(d, "cur", "%s:2,%s" % (name, "".join(sorted(flags))))
        else:
            final = os.path.join(d, "new", name)
        os.rename(tmp, final)
        return final


# --------------------------------------------------------------------------- SMTP sink
class SmtpSink:
    def __init__(self, outdir, maildirs, port=SMTP_PORT, host="127.0.0.1"):
        self.outdir = outdir
        self.maildirs = maildirs
        self.port = port
        self.host = host
        self.records = []
        os.makedirs(outdir, exist_ok=True)
        self._write_json()

    def _write_json(self):
        tmp = os.path.join(self.outdir, "sent.json.tmp")
        with open(tmp, "w") as f:
            json.dump(self.records, f, indent=1)
        os.replace(tmp, os.path.join(self.outdir, "sent.json"))

    def _record(self, mail_from, rcpts, data, auth_user, peer):
        n = len(self.records) + 1
        path = os.path.join(self.outdir, "%04d.eml" % n)
        with open(path, "wb") as f:
            f.write(data)
        msg = email.message_from_bytes(data, policy=email.policy.default)
        rec = {
            "n": n,
            "received_at": time.time(),
            "mail_from": mail_from,
            "rcpt_to": rcpts,
            "auth_user": auth_user,
            "peer": peer,
            "size": len(data),
            "eml": path,
            "message_id": msg["Message-ID"] and str(msg["Message-ID"]),
            "in_reply_to": msg["In-Reply-To"] and str(msg["In-Reply-To"]),
            "references": msg["References"] and str(msg["References"]),
            "from": msg["From"] and str(msg["From"]),
            "to": msg["To"] and str(msg["To"]),
            "cc": msg["Cc"] and str(msg["Cc"]),
            "subject": msg["Subject"] and str(msg["Subject"]),
            "content_type": msg.get_content_type(),
            "attachments": [p.get_filename() for p in msg.walk() if p.get_filename()],
        }
        self.records.append(rec)
        self._write_json()
        if any(r.lower().strip("<>") == USER for r in rcpts):
            self.maildirs.deliver(data, "INBOX")
        return rec

    async def _client(self, reader, writer):
        peer = "%s:%s" % writer.get_extra_info("peername")[:2]
        mail_from, rcpts, auth_user = None, [], None

        async def reply(line):
            writer.write(line.encode() + b"\r\n")
            await writer.drain()

        await reply("220 lab.example.test ESMTP bombadil-lab")
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                cmd = line.split(" ", 1)[0].upper()
                if cmd in ("EHLO", "HELO"):
                    if cmd == "EHLO":
                        writer.write(b"250-lab.example.test\r\n250-PIPELINING\r\n250-SIZE 52428800\r\n"
                                     b"250-8BITMIME\r\n250-ENHANCEDSTATUSCODES\r\n250 AUTH PLAIN LOGIN\r\n")
                        await writer.drain()
                    else:
                        await reply("250 lab.example.test")
                elif cmd == "AUTH":
                    parts = line.split()
                    import base64
                    mech = parts[1].upper() if len(parts) > 1 else ""
                    if mech == "PLAIN":
                        if len(parts) > 2:
                            blob = parts[2]
                        else:
                            await reply("334 ")
                            blob = (await reader.readline()).decode().strip()
                        try:
                            auth_user = base64.b64decode(blob).split(b"\0")[1].decode()
                        except Exception:
                            auth_user = "?"
                        await reply("235 2.7.0 Authentication successful")
                    elif mech == "LOGIN":
                        await reply("334 " + base64.b64encode(b"Username:").decode())
                        u = (await reader.readline()).decode().strip()
                        await reply("334 " + base64.b64encode(b"Password:").decode())
                        await reader.readline()
                        try:
                            auth_user = base64.b64decode(u).decode()
                        except Exception:
                            auth_user = "?"
                        await reply("235 2.7.0 Authentication successful")
                    else:
                        await reply("504 5.5.4 Unrecognized authentication type")
                elif cmd == "MAIL":
                    mail_from = line.split(":", 1)[1].strip().split()[0].strip("<>")
                    rcpts = []
                    await reply("250 2.1.0 OK")
                elif cmd == "RCPT":
                    rcpts.append(line.split(":", 1)[1].strip().split()[0].strip("<>"))
                    await reply("250 2.1.5 OK")
                elif cmd == "DATA":
                    if mail_from is None or not rcpts:
                        await reply("503 5.5.1 need MAIL and RCPT first")
                        continue
                    await reply("354 End data with <CR><LF>.<CR><LF>")
                    chunks = []
                    while True:
                        l = await reader.readline()
                        if not l:
                            raise ConnectionError("eof in DATA")
                        if l == b".\r\n":
                            break
                        if l.startswith(b".."):
                            l = l[1:]
                        chunks.append(l)
                    rec = self._record(mail_from, rcpts, b"".join(chunks), auth_user, peer)
                    mail_from, rcpts = None, []
                    await reply("250 2.0.0 OK queued as lab-%04d" % rec["n"])
                elif cmd == "RSET":
                    mail_from, rcpts = None, []
                    await reply("250 2.0.0 OK")
                elif cmd == "NOOP":
                    await reply("250 2.0.0 OK")
                elif cmd == "QUIT":
                    await reply("221 2.0.0 bye")
                    break
                elif cmd in ("STARTTLS", "VRFY", "EXPN"):
                    await reply("502 5.5.1 not implemented")
                else:
                    await reply("500 5.5.1 unknown command")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    def serve_forever(self, ready=None):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        server = loop.run_until_complete(asyncio.start_server(self._client, self.host, self.port, limit=1 << 26))
        if ready:
            ready.set()
        loop.run_forever()
        server.close()


# --------------------------------------------------------------------------- Dovecot config
DOVECOT_CONF = """\
# generated by tests/mail/lab/mailserver.py: do not edit
base_dir = {d}/run
state_dir = {d}/run
log_path = {d}/dovecot.log
info_log_path = {d}/dovecot.log
protocols = imap
listen = 127.0.0.1
ssl = no
disable_plaintext_auth = no
auth_mechanisms = plain login
default_internal_user = {user}
default_internal_group = {group}
default_login_user = {user}
first_valid_uid = {uid}
last_valid_uid = {uid}
first_valid_gid = {gid}
last_valid_gid = {gid}
mail_uid = {uid}
mail_gid = {gid}
mail_location = maildir:{d}/mail/%u:LAYOUT=fs
mail_home = {d}/mail/%u
mailbox_list_index = no
mail_fsync = never
passdb {{
  driver = static
  args = password={password}
}}
userdb {{
  driver = static
  args = uid={uid} gid={gid} home={d}/mail/%u
}}
namespace inbox {{
  inbox = yes
  separator = /
{mailboxes}}}
service imap-login {{
  chroot =
  user = {user}
  inet_listener imap {{
    port = {imap_port}
  }}
  inet_listener imaps {{
    port = 0
  }}
}}
service anvil {{
  chroot =
}}
service auth {{
  user = {user}
}}
service auth-worker {{
  user = {user}
}}
"""


def write_dovecot_conf(d, imap_port, user, group, uid, gid):
    mailboxes = "".join(
        "  mailbox %s {\n    auto = subscribe\n    special_use = %s\n  }\n" % (n, su) for n, su in FOLDERS.items()
    )
    path = os.path.join(d, "dovecot.conf")
    with open(path, "w") as f:
        f.write(DOVECOT_CONF.format(d=d, user=user, group=group, uid=uid, gid=gid, password=PASSWORD,
                                    imap_port=imap_port, mailboxes=mailboxes))
    return path


def seed(maildirs, directory):
    """Deliver every .eml under `directory`; returns [(path, folder)]."""
    done = []
    for root, _dirs, files in sorted(os.walk(directory)):
        rel = os.path.relpath(root, directory)
        folder = "INBOX" if rel == "." else rel
        for fn in sorted(files):
            if not fn.endswith(".eml"):
                continue
            parts = fn.split(".")
            flags = ("S" if "seen" in parts else "") + ("F" if "flagged" in parts else "")
            with open(os.path.join(root, fn), "rb") as f:
                raw = f.read()
            maildirs.deliver(raw, folder, flags)
            done.append((os.path.join(root, fn), folder))
    return done


# --------------------------------------------------------------------------- server process
def _drop_privileges(d):
    if os.geteuid() != 0:
        return
    pw = pwd.getpwnam("nobody")
    for dirpath, dirnames, filenames in os.walk(d):
        os.chown(dirpath, pw.pw_uid, pw.pw_gid)
        for fn in filenames:
            os.chown(os.path.join(dirpath, fn), pw.pw_uid, pw.pw_gid)
    os.setgroups([])
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ["HOME"] = d


def serve(d, imap_port, smtp_port, seed_dir=None):
    os.makedirs(os.path.join(d, "run"), exist_ok=True)
    os.makedirs(os.path.join(d, "mail", USER), exist_ok=True)
    # parents must be traversable by the unprivileged user
    try:
        os.chmod(d, 0o755)
    except OSError:
        pass
    _drop_privileges(d)
    pw = pwd.getpwuid(os.geteuid())
    import grp
    gname = grp.getgrgid(os.getegid()).gr_name
    conf = write_dovecot_conf(d, imap_port, pw.pw_name, gname, os.geteuid(), os.getegid())
    maildirs = Maildirs(os.path.join(d, "mail", USER))
    with open(os.path.join(d, "server.pid"), "w") as f:
        f.write(str(os.getpid()))
    dv = subprocess.Popen(["dovecot", "-F", "-c", conf], stdout=open(os.path.join(d, "dovecot.stdout"), "w"),
                          stderr=subprocess.STDOUT, start_new_session=True)
    sink = SmtpSink(os.path.join(d, "smtp"), maildirs, smtp_port)
    ready = threading.Event()
    threading.Thread(target=sink.serve_forever, args=(ready,), daemon=True).start()
    ready.wait(5)
    # wait for IMAP
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", imap_port), 1).close()
            break
        except OSError:
            if dv.poll() is not None:
                sys.exit("dovecot exited: see %s/dovecot.log" % d)
            time.sleep(0.1)
    if seed_dir:
        seed(maildirs, seed_dir)
    with open(os.path.join(d, "ready"), "w") as f:
        f.write("ok\n")

    def stop(*_a):
        try:
            os.killpg(dv.pid, signal.SIGTERM)
        except OSError:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        if dv.poll() is not None:
            sys.exit("dovecot died: see %s/dovecot.log" % d)
        time.sleep(0.5)


class MailServer:
    """Handle used from tests: start/stop the server subprocess, inspect/inject mail."""

    def __init__(self, directory=DEFAULT_DIR, imap_port=IMAP_PORT, smtp_port=SMTP_PORT):
        self.dir = directory
        self.imap_port = imap_port
        self.smtp_port = smtp_port
        self.proc = None

    @property
    def maildirs(self):
        return Maildirs(os.path.join(self.dir, "mail", USER))

    def start(self, seed=None, timeout=30):
        os.makedirs(self.dir, exist_ok=True)
        ready = os.path.join(self.dir, "ready")
        if os.path.exists(ready):
            os.unlink(ready)
        cmd = [sys.executable, os.path.abspath(__file__), "serve", "--dir", self.dir,
               "--imap-port", str(self.imap_port), "--smtp-port", str(self.smtp_port)]
        if seed:
            cmd += ["--seed", seed]
        self.proc = subprocess.Popen(cmd, stdout=open(os.path.join(self.dir, "server.out"), "w"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(ready):
                return self
            if self.proc.poll() is not None:
                raise RuntimeError("mail server exited: " + open(os.path.join(self.dir, "server.out")).read())
            time.sleep(0.1)
        raise TimeoutError("mail server not ready")

    def stop(self):
        pidf = os.path.join(self.dir, "server.pid")
        if os.path.exists(pidf):
            try:
                os.kill(int(open(pidf).read()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        if self.proc:
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def deliver(self, raw, folder="INBOX", seen=False):
        if isinstance(raw, str):
            raw = raw.encode()
        return self.maildirs.deliver(raw, folder, "S" if seen else "")

    def seed(self, directory):
        return seed(self.maildirs, directory)

    def sent(self):
        try:
            with open(os.path.join(self.dir, "smtp", "sent.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def wait_sent(self, n=1, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.sent()
            if len(s) >= n:
                return s
            time.sleep(0.2)
        raise TimeoutError("expected %d SMTP messages, got %d" % (n, len(self.sent())))

    def sent_eml(self, n):
        with open(self.sent()[n - 1]["eml"], "rb") as f:
            return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["serve", "stop", "seed", "deliver", "sent", "status"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--imap-port", type=int, default=IMAP_PORT)
    ap.add_argument("--smtp-port", type=int, default=SMTP_PORT)
    ap.add_argument("--seed")
    ap.add_argument("--folder", default="INBOX")
    ap.add_argument("--seen", action="store_true")
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=30)
    a = ap.parse_args(argv)
    ms = MailServer(a.dir, a.imap_port, a.smtp_port)
    if a.cmd == "serve":
        serve(os.path.abspath(a.dir), a.imap_port, a.smtp_port, a.seed)
    elif a.cmd == "stop":
        ms.stop()
    elif a.cmd == "seed":
        for p, f in ms.seed(a.arg):
            print("%s -> %s" % (p, f))
    elif a.cmd == "deliver":
        with open(a.arg, "rb") as f:
            print(ms.deliver(f.read(), a.folder, a.seen))
    elif a.cmd == "sent":
        print(json.dumps(ms.wait_sent(a.wait, a.timeout) if a.wait else ms.sent(), indent=1))
    elif a.cmd == "status":
        ok = os.path.exists(os.path.join(a.dir, "ready"))
        print("ready" if ok else "not running")


if __name__ == "__main__":
    main()

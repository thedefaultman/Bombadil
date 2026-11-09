"""Run the real add-on (share/mail/extension) in a real Thunderbird against the lab's local mail server.

    lab = AddonLab(root, thunderbird_binary)
    lab.start()                       # server, profile, host, Thunderbird (headless); returns when the add-on said hello
    lab.engine.request("accounts")    # the service's side of the protocol (engine_client.py)
    lab.stop()                        # always, in a `finally`: it kills the whole process group
                                      # (and if the test run itself is killed, a watcher does the same)

Everything lives under `root`, which must be short (a Unix socket path) and must be traversable by the
unprivileged user the lab's Dovecot drops to when this runs as root; `new_root()` makes one. Nothing
touches the real home directory: Thunderbird runs with HOME inside `root`, the native-messaging
manifest is written there, and the only network is loopback.

The profile is made as `make_profile.py` makes it (quiet first run, a Local Folders account, one IMAP
account at the lab server) with the add-on installed as `extensions/<id>.xpi`. `optional_permissions`
says whether the profile grants `messages.send`, which Thunderbird gives only to a profile that records
it (it cannot be a manifest permission), and `extra_prefs` is appended to user.js.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import make_profile
from engine_client import EngineClient
from mailserver import MailServer

ADDON_ID = "bombadil-mail@bombadil.local"
EXTENSION = REPO / "share" / "mail" / "extension"
SEED = HERE / "seed"
LAB_HOST = HERE / "host" / "bombadil_mail_host.py"
REAL_HOST = REPO / "bin" / "bombadil-mail-host"
DEFAULT_THUNDERBIRD = Path.home() / ".cache" / "bombadil-lab" / "thunderbird-157.0" / "thunderbird"


def find_thunderbird():
    """The Thunderbird binary a test may use, or None: $BOMBADIL_TEST_THUNDERBIRD, else the lab's unpacked 157."""
    given = os.environ.get("BOMBADIL_TEST_THUNDERBIRD")
    for candidate in (Path(given) if given else None, DEFAULT_THUNDERBIRD):
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def new_root():
    root = Path(tempfile.mkdtemp(prefix="bm-addon-", dir="/tmp"))
    root.chmod(0o755)
    return root


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# What watches over a lab from outside the test run. A run that is killed (a CI timeout, a SIGKILL) cannot run
# its `finally`, and would leave Thunderbird, Dovecot and the mail server running for good. This small program
# is started in a session of its own with a pipe from the run; it reads until the pipe ends, and if what came
# was not "done" the run is gone without having stopped the lab, so it ends every process whose command line
# names the lab's directory (Thunderbird's helper processes and Dovecot's children follow their parents) and
# removes the directory. A lab that is stopped properly says "done" first.
WATCHER = r"""
import os, shutil, signal, sys, time
root = sys.argv[1]
if sys.stdin.buffer.read().strip() == b"done":
    sys.exit(0)
def named():
    found = []
    for entry in os.listdir("/proc"):
        if entry.isdigit() and int(entry) != os.getpid():
            try:
                line = open("/proc/%s/cmdline" % entry, "rb").read().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if root + "/" in line:
                found.append(int(entry))
    return found
for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 1)):
    for pid in named():
        try:
            os.kill(pid, sig)
        except OSError:
            pass
    end = time.time() + wait
    while named() and time.time() < end:
        time.sleep(0.2)
shutil.rmtree(root, ignore_errors=True)
"""


def build_xpi(out, source=EXTENSION):
    """The extension directory as an unsigned XPI (a zip with manifest.json at its top)."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(Path(source).rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(source).as_posix())
    return out


class AddonLab:
    def __init__(self, root, thunderbird, *, extension=EXTENSION, optional_permissions=("messages.send",),
                 extra_prefs="", seed=SEED, bulk=0, host=None, smtp_refuses=False):
        self.root = Path(root)
        self.thunderbird = Path(thunderbird)
        self.extension = Path(extension)
        self.optional_permissions = tuple(optional_permissions)
        self.extra_prefs = extra_prefs
        self.seed = seed
        self.bulk = bulk
        self.host = Path(host) if host else (REAL_HOST if REAL_HOST.exists() else LAB_HOST)
        self.imap_port = free_port()
        self.smtp_port = free_port()
        # The account's outgoing server is a port nothing listens on (the sink is still where it would have gone).
        self.profile_smtp_port = free_port() if smtp_refuses else self.smtp_port
        self.server = None
        self.engine = None
        self.proc = None
        self.watcher = None
        self.home = self.root / "home"
        self.profile = self.root / "profile"
        self.log = self.root / "tb.log"

    # -- lifecycle

    def start(self, timeout=60):
        try:
            self._start(timeout)
        except BaseException:
            self.stop()
            raise
        return self

    def _start(self, timeout):
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o755)
        self.watcher = subprocess.Popen([sys.executable, "-c", WATCHER, str(self.root)], stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.server = MailServer(str(self.root / "srv"), self.imap_port, self.smtp_port).start(seed=str(self.seed))
        if self.bulk:
            self.deliver_bulk(self.bulk)
        self.home.mkdir(exist_ok=True)
        self.profile.mkdir(exist_ok=True)
        xpi = build_xpi(self.root / "addon.xpi", self.extension)
        extra = self.root / "extra-prefs.js"
        extra.write_text(self.extra_prefs + "\n")
        make_profile.EXT_ID = ADDON_ID
        make_profile.write_profile(
            str(self.profile), kind="lab", imap_port=self.imap_port, smtp_port=self.profile_smtp_port, load="profile-xpi",
            xpi=str(xpi), nss_dir=str(self.thunderbird.parent), extra_user_js=str(extra), addon_prefs=True,
            optional_permissions=self.optional_permissions)
        self._native_manifest()
        self.engine = EngineClient(self.root / "mail.sock")
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(HOME=str(self.home), MOZ_CRASHREPORTER_DISABLE="1", MOZ_CRASHREPORTER_NO_REPORT="1",
                   NO_AT_BRIDGE="1")
        self._spawn(env)
        self.engine.wait_connected(timeout)
        self.engine.wait_event("hello", timeout)

    def _spawn(self, env):
        with open(self.log, "ab") as log:
            self.proc = subprocess.Popen(
                [str(self.thunderbird), "-no-remote", "-profile", str(self.profile), "--headless"], env=env,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def _native_manifest(self):
        wrapper = self.root / "host.sh"
        sock = self.root / "mail.sock"
        if self.host == LAB_HOST:
            command = f'exec python3 "{self.host}" --sock "{sock}"'
        else:
            command = f'BOMBADIL_MAIL_SOCKET="{sock}" exec "{self.host}"'
        wrapper.write_text(f"#!/bin/sh\n{command}\n")
        wrapper.chmod(0o755)
        folder = self.home / ".mozilla" / "native-messaging-hosts"
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {"name": "bombadil_mail", "description": "Bombadil Mail test host", "path": str(wrapper),
                    "type": "stdio", "allowed_extensions": [ADDON_ID]}
        (folder / "bombadil_mail.json").write_text(json.dumps(manifest))

    def stop(self):
        """Kill Thunderbird's whole process group, then the servers, then remove nothing: the caller owns `root`."""
        if self.proc is not None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)
                except OSError:
                    break
                try:
                    self.proc.wait(8 if sig == signal.SIGTERM else 3)
                    break
                except subprocess.TimeoutExpired:
                    continue
            self.proc = None
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        if self.server is not None:
            self.server.stop()
            self.server = None
        if self.watcher is not None:
            try:
                self.watcher.stdin.write(b"done\n")
                self.watcher.stdin.close()
                self.watcher.wait(5)
            except (OSError, subprocess.SubprocessError):
                self.watcher.kill()
            self.watcher = None

    def restart_thunderbird(self, timeout=60):
        """What the service does to clear a stuck dialog: stop Thunderbird and start it again on the same profile."""
        mark = self.engine.mark()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except OSError:
                break
            try:
                self.proc.wait(8 if sig == signal.SIGTERM else 3)
                break
            except subprocess.TimeoutExpired:
                continue
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(HOME=str(self.home), MOZ_CRASHREPORTER_DISABLE="1", MOZ_CRASHREPORTER_NO_REPORT="1",
                   NO_AT_BRIDGE="1")
        self._spawn(env)
        self.engine.wait_event("hello", timeout, after=mark)

    # -- the mail server

    def deliver_bulk(self, n, folder="INBOX"):
        """`n` extra mails, read, with subjects "Bulk mail 000" ..., one a minute apart, ending at the seed's start."""
        base = (SEED / "01-plain-welcome.eml").read_bytes()
        for i in range(n):
            raw = base.replace(b"welcome-1@", b"bulk-%d@" % i).replace(b"Welcome to the lab", b"Bulk mail %03d" % i)
            raw = raw.replace(b"Mon, 28 Sep 2026 09:00:00 +0000", self._date(i))
            self.server.deliver(raw, folder, seen=True)

    @staticmethod
    def _date(i):
        stamp = time.gmtime(1_790_000_000 + i * 60)
        return time.strftime("%a, %d %b %Y %H:%M:%S +0000", stamp).encode()

    def smtp_wait(self, n, timeout=30):
        return self.server.wait_sent(n, timeout)

    def server_copies(self, message_id, folders=("INBOX", "Archive", "Sent", "Trash", "Drafts")):
        """Where the mail server has the message with this Message-ID, as (folder, flags) pairs: what the
        server holds, which is what proves a change went all the way there and not only into Thunderbird."""
        needle = b"Message-ID: <" + message_id.encode() + b">"
        found = []
        for folder in folders:
            base = self.server.maildirs.folder_dir(folder)
            for sub in ("cur", "new"):
                directory = os.path.join(base, sub)
                if not os.path.isdir(directory):
                    continue
                for name in os.listdir(directory):
                    try:
                        with open(os.path.join(directory, name), "rb") as f:
                            head = f.read(4096)
                    except OSError:
                        continue
                    if needle.lower() in head.lower():
                        flags = name.split(":2,", 1)[1] if ":2," in name else ""
                        found.append((folder, flags))
        return found

    def wait_server(self, message_id, check, timeout=30, folders=("INBOX", "Archive", "Sent", "Trash", "Drafts")):
        """The copies of a message once `check(copies)` says so; the last ones seen when it never does."""
        end = time.time() + timeout
        while True:
            copies = self.server_copies(message_id, folders)
            if check(copies) or time.time() > end:
                return copies
            time.sleep(0.25)

    def tail_log(self, lines=40):
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def remove(self):
        shutil.rmtree(self.root, ignore_errors=True)

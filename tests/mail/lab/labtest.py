"""Shared helpers for the lab's question scripts (q2_*.py, q3_*.py, q4_*.py, q5_*.py).

    from labtest import Lab
    lab = Lab()                       # uses $BOMBADIL_LAB_DIR (default /tmp/bombadil-lab)
    lab.start(LAB_HEADLESS="1")       # run-thunderbird.sh start with env overrides; waits for the add-on
    b = lab.bridge()                  # lab_bridge.Bridge, connected to the add-on
    lab.ms                            # mailserver.MailServer handle (deliver(), sent(), wait_sent())
    lab.imap()                        # imaplib connection as the test user
    lab.stop()
"""
import imaplib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lab_bridge import Bridge  # noqa: E402
from mailserver import MailServer  # noqa: E402

RUN = os.path.join(HERE, "run-thunderbird.sh")


class Lab:
    def __init__(self, lab_dir=None):
        self.dir = lab_dir or os.environ.get("BOMBADIL_LAB_DIR", "/tmp/bombadil-lab")
        self.imap_port = int(os.environ.get("LAB_IMAP_PORT", "1143"))
        self.smtp_port = int(os.environ.get("LAB_SMTP_PORT", "1025"))
        self.ms = MailServer(os.path.join(self.dir, "mail"), self.imap_port, self.smtp_port)
        self._bridge = None
        self.start_seconds = None
        self.display = None

    # -- lifecycle
    def start(self, **env):
        e = dict(os.environ)
        e["BOMBADIL_LAB_DIR"] = self.dir
        e.update({k: str(v) for k, v in env.items()})
        t = time.time()
        p = subprocess.run([RUN, "start"], env=e, capture_output=True, text=True)
        self.start_seconds = time.time() - t
        if p.returncode not in (0,):
            raise RuntimeError("run-thunderbird.sh start failed (%d): %s" % (p.returncode, p.stderr[-800:]))
        self._display()
        return self

    def restart(self, **env):
        e = dict(os.environ)
        e["BOMBADIL_LAB_DIR"] = self.dir
        e.update({k: str(v) for k, v in env.items()})
        subprocess.run([RUN, "restart"], env=e, check=True, capture_output=True)
        return self

    def stop(self):
        if self._bridge:
            self._bridge.close()
            self._bridge = None
        subprocess.run([RUN, "stop"], env=dict(os.environ, BOMBADIL_LAB_DIR=self.dir), capture_output=True)

    def _display(self):
        """Remember the lab's DISPLAY WITHOUT exporting it: run-thunderbird.sh starts its own Xvfb only when $DISPLAY
        is unset, so a stale DISPLAY in this process would make the next start() use a dead display."""
        try:
            self.display = open(os.path.join(self.dir, "display")).read().strip()
        except OSError:
            self.display = None

    def env(self):
        self._display()
        e = dict(os.environ)
        if self.display:
            e["DISPLAY"] = self.display
        return e

    def bridge(self, timeout=60):
        if self._bridge is None:
            self._bridge = Bridge(os.path.join(self.dir, "host.sock"))
        self._bridge.wait_connected(timeout)
        return self._bridge

    # -- conveniences
    def imap(self):
        m = imaplib.IMAP4("127.0.0.1", self.imap_port)
        m.login("test@example.test", "lab")
        return m

    def imap_flags(self, folder, message_id, tries=10):
        """Server-side FLAGS of the message with this Message-ID in `folder` ([] if absent, None if not found)."""
        for _ in range(tries):
            m = self.imap()
            try:
                m.select(folder)
                typ, data = m.search(None, "HEADER", "Message-ID", message_id)
                nums = data[0].split()
                if nums:
                    typ, d = m.fetch(nums[0], "(FLAGS)")
                    return d[0].decode()
            finally:
                m.logout()
            time.sleep(0.5)
        return None

    def imap_count(self, folder):
        m = self.imap()
        try:
            typ, data = m.select(folder)
            return int(data[0])
        finally:
            m.logout()

    def windows(self):
        """Top-level X windows with a name (xwininfo)."""
        out = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True, env=self.env()).stdout
        res = []
        for l in out.splitlines():
            l = l.strip()
            if l.startswith("0x") and '"' in l and "has no name" not in l:
                res.append(l)
        return res

    def screenshot(self, path):
        subprocess.run(["import", "-window", "root", path], check=False, env=self.env())
        return path

    def tb_pids(self):
        """PIDs of this lab's Thunderbird process tree (launcher, main, content/gpu/socket/utility children),
        excluding the native-messaging host and its helpers."""
        try:
            root = int(open(os.path.join(self.dir, "tb.pid")).read())
        except (OSError, ValueError):
            return []
        kids = {}
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                st = open("/proc/%s/stat" % d).read()
                ppid = int(st.rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            kids.setdefault(ppid, []).append(int(d))
        out, todo = [], [root]
        while todo:
            p = todo.pop()
            try:
                cmd = open("/proc/%d/cmdline" % p).read()
            except OSError:
                continue
            if "bombadil_mail_host" in cmd:
                continue
            out.append(p)
            todo += kids.get(p, [])
        return out

    def pss_mb(self):
        total = 0
        for p in self.tb_pids():
            try:
                for l in open("/proc/%d/smaps_rollup" % p):
                    if l.startswith("Pss:"):
                        total += int(l.split()[1])
            except OSError:
                pass
        return total / 1024.0

    def rss_mb(self):
        total = 0
        for p in self.tb_pids():
            try:
                for l in open("/proc/%d/status" % p):
                    if l.startswith("VmRSS:"):
                        total += int(l.split()[1])
            except OSError:
                pass
        return total / 1024.0

    def cpu_seconds(self):
        tck = os.sysconf("SC_CLK_TCK")
        total = 0.0
        for p in self.tb_pids():
            try:
                f = open("/proc/%d/stat" % p).read().rsplit(")", 1)[1].split()
                total += (int(f[11]) + int(f[12])) / tck
            except (OSError, IndexError):
                pass
        return total


def pretty(o, n=400):
    s = json.dumps(o, default=str)
    return s if len(s) <= n else s[:n] + "..."

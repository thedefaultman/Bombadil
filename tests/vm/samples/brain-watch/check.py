"""The brain watcher on Arch's real kernel and the installed btrfs layout, as root in the VM
(tests/vm/btrfs-kernel.sh; / is subvol @, /home is subvol @home, /home/user/Projects is a
nested subvolume, user is uid 1000). Prints one PASS/FAIL line per numbered check."""

import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/test/src")
from bombadil.brain import fanotify as fan  # noqa: E402

SOCK = "/run/bombadil-brain/watch.sock"
STATE = "/var/lib/bombadil-brain"
UID = 1000
HOME = "/home/user"
CG = "/sys/fs/cgroup/bombadil-turn-test.scope"
PY = sys.executable
AS_USER = ["setpriv", f"--reuid={UID}", f"--regid={UID}", "--clear-groups"]
MASK = fan.FAN_CREATE | fan.FAN_DELETE | fan.FAN_RENAME | fan.FAN_CLOSE_WRITE | fan.FAN_ONDIR
WRITER = ("import os, sys, time\n"
          "with open(sys.argv[1], 'w') as f: f.write('hi')\n"
          "print(os.getpid(), flush=True)\n"
          "time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 5)\n")
results: dict[int, bool] = {}


def say(text: str) -> None:
    print(text, flush=True)


def result(n: int, ok: bool, text: str) -> None:
    results[n] = ok
    say(f"{'PASS' if ok else 'FAIL'} {n}. {text}")


def short(e: dict | None) -> str:
    if e is None:
        return "nothing"
    e = dict(e)
    if e.get("chain"):
        e["chain"] = [c[:2] for c in e["chain"]]
    return json.dumps(e)


class Stream:
    """One client's lines, read in a thread. Bench saves are only counted, not parsed."""

    def __init__(self, uid: int | None = None, bench: str = f"{HOME}/bench/"):
        cmd = [PY, "/test/client.py", SOCK]
        if uid is not None:
            cmd = [*AS_USER[:1], f"--reuid={uid}", f"--regid={uid}", "--clear-groups", *cmd]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        self.events: list[dict] = []
        self.bench_key = f'"path":"{bench}'.encode()
        self.bench_writes = 0
        self.bench_last = 0.0
        self.lines = 0
        self.lock = threading.Lock()
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _read(self) -> None:
        assert self.p.stdout is not None
        for line in self.p.stdout:
            now = time.monotonic()
            with self.lock:
                self.lines += 1
                if self.bench_key in line:
                    if b'"op":"write"' in line:
                        self.bench_writes += 1
                        self.bench_last = now
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    e = {"op": "unparsable", "line": line[:200].decode(errors="replace")}
                e["_at"] = now
                self.events.append(e)

    def find(self, pred) -> list[dict]:
        with self.lock:
            return [e for e in self.events if pred(e)]

    def wait(self, pred, timeout: float = 60, count: int = 1) -> list[dict]:
        end = time.monotonic() + timeout
        while True:
            got = self.find(pred)
            if len(got) >= count or time.monotonic() > end:
                return got
            time.sleep(0.1)

    def close(self) -> None:
        self.p.terminate()
        self.p.wait(10)


def start_watcher() -> subprocess.Popen:
    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    log = open("/tmp/watch.log", "ab")
    p = subprocess.Popen([PY, "-m", "bombadil.brain.watch"], stderr=log,
                         env={**os.environ, "PYTHONPATH": "/test/src"})
    end = time.monotonic() + 120
    while not os.path.exists(SOCK) and time.monotonic() < end and p.poll() is None:
        time.sleep(0.1)
    return p


def stop_watcher(p: subprocess.Popen) -> None:
    p.send_signal(signal.SIGTERM)
    try:
        p.wait(60)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()


def try_mark(path: str, flags: int) -> str:
    fd = fan.init(fan.FAN_CLASS_NOTIF | fan.FAN_CLOEXEC | flags)
    try:
        fan.mark(fd, fan.FAN_MARK_ADD | fan.FAN_MARK_FILESYSTEM, MASK if flags & fan.FAN_REPORT_NAME
                 else fan.FAN_CLOSE_WRITE, path)
        return "ok"
    except OSError as e:
        return f"errno {e.errno} ({os.strerror(e.errno)})"
    finally:
        os.close(fd)


def cpu(pid: int) -> float:
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def move_to_cg():
    with open(f"{CG}/cgroup.procs", "w") as f:
        f.write(str(os.getpid()))


def user_writer(path: str, secs: float = 5, cgroup: bool = False) -> tuple[subprocess.Popen, int]:
    p = subprocess.Popen([*AS_USER, PY, "-c", WRITER, path, str(secs)], stdout=subprocess.PIPE, text=True,
                         preexec_fn=move_to_cg if cgroup else None)
    assert p.stdout is not None
    return p, int(p.stdout.readline())


def main() -> int:
    dfid = fan.FAN_REPORT_DFID_NAME | fan.FAN_REPORT_PIDFD
    # 1 and 2: what the brief says fails.
    for n, path in ((1, "/home"), (2, "/")):
        got = try_mark(path, dfid)
        also = f"FID only: {try_mark(path, fan.FAN_REPORT_FID)}; no FID reporting: {try_mark(path, 0)}"
        result(n, got.startswith("errno 18 "), f"FAN_MARK_FILESYSTEM with DFID_NAME+PIDFD on {path} "
               f"(subvolume): {got}. [{also}]")

    # 3: the watcher's own way through: subvolid=5, read-only, private mount namespace.
    w = start_watcher()
    root = Stream()
    user = Stream(UID)
    hello = user.wait(lambda e: e.get("op") == "hello", 60)
    h = hello[0] if hello else None
    ours = open("/proc/self/mountinfo").read()
    try:
        theirs = [line for line in open(f"/proc/{w.pid}/mountinfo") if " /run/bombadil-brain/top " in line]
    except OSError:
        theirs = []
    ok = (h is not None and h.get("watching") is True and h.get("fs") == "btrfs" and bool(theirs)
          and "subvolid=5" in theirs[0] and " ro," in theirs[0] and "/run/bombadil-brain/top" not in ours)
    result(3, ok, f"mark on a fresh subvolid=5 read-only mount in the watcher's own mount namespace: hello={short(h)}; "
           f"watcher's mountinfo: {theirs[0].strip() if theirs else 'no top mount'}; "
           f"visible outside its namespace: {'/run/bombadil-brain/top' in ours}")
    if not ok:
        say(open("/tmp/watch.log").read())

    # 4: create + close-write by uid 1000 in /home/user.
    def saved(path, op):
        return lambda e: e.get("path") == path and e.get("op") == op

    def check_write(n: int, path: str, where: str) -> None:
        p, pid = user_writer(path)
        c = user.wait(saved(path, "create"), 60)
        wr = user.wait(saved(path, "write"), 60)
        r = root.wait(saved(path, "write"), 30)
        e = wr[0] if wr else None
        ok = bool(c) and e is not None and e["pid"] == pid and e["uid"] == UID and e["gone"] is False and bool(r)
        result(n, ok, f"{where}: create={bool(c)}, write={short(e)} (writer pid {pid}); root client too: {bool(r)}")
        p.kill()
        p.wait()

    check_write(4, f"{HOME}/check4.txt", "a save by uid 1000 in /home/user (subvolume @home)")
    # 5: the nested subvolume.
    check_write(5, f"{HOME}/Projects/check5.txt", "a save by uid 1000 in /home/user/Projects (nested subvolume)")

    # 6: one rename event with both real paths; a delete.
    a, b = f"{HOME}/check4.txt", f"{HOME}/check6-renamed.txt"
    subprocess.run([*AS_USER, "mv", a, b], check=True)
    ren = user.wait(lambda e: e.get("op") == "rename" and e.get("path") == b, 60)
    subprocess.run([*AS_USER, "rm", b], check=True)
    dele = user.wait(saved(b, "delete"), 60)
    # A directory tree removed: the directory's own delete must come through (its files'
    # deletes may be skipped if the directory is gone before they are read).
    d = f"{HOME}/check6-dir"
    subprocess.run([*AS_USER, "sh", "-c", f"mkdir -p {d}/sub && echo x > {d}/sub/f && rm -rf {d}"], check=True)
    ddel = user.wait(lambda e: e.get("op") == "delete" and e.get("path") == d and e.get("dir") is True, 60)
    inside = user.find(lambda e: e.get("op") == "delete" and e.get("path", "").startswith(d + "/"))
    ok = len(ren) == 1 and ren[0].get("old") == a and bool(dele) and bool(ddel)
    result(6, ok, f"rename as one event: {short(ren[0] if ren else None)} (count {len(ren)}); "
           f"delete: {short(dele[0] if dele else None)}; rm -rf of a directory: its delete {bool(ddel)}, "
           f"deletes inside it {sorted(e['path'] for e in inside)}")

    # 7: a writer in a turn's cgroup, live and short-lived.
    os.makedirs(CG, exist_ok=True)
    live = f"{HOME}/check7-live.txt"
    p, pid = user_writer(live, cgroup=True)
    got = user.wait(saved(live, "write"), 60)
    e_live = got[0] if got else None
    p.kill()
    p.wait()
    # The short-lived writer: stop the watcher while `sh -c 'echo x > f'` runs and exits, so
    # it is surely reaped before its event is read; the outer shell stays in the scope.
    short_path = f"{HOME}/check7-short.txt"
    w.send_signal(signal.SIGSTOP)
    outer = subprocess.Popen(["sh", "-c", f"sh -c 'echo x > {short_path}'; sleep 4"], preexec_fn=move_to_cg)
    time.sleep(1)
    w.send_signal(signal.SIGCONT)
    got = user.wait(saved(short_path, "write"), 60)
    e_short = got[0] if got else None
    # The same without stopping the watcher: whatever the timing gives.
    natural = f"{HOME}/check7-natural.txt"
    outer2 = subprocess.Popen(["sh", "-c", f"sh -c 'echo x > {natural}'; sleep 3"], preexec_fn=move_to_cg)
    got = user.wait(saved(natural, "write"), 60)
    e_nat = got[0] if got else None
    outer.wait()
    outer2.wait()
    scope = "/bombadil-turn-test.scope"
    ok = (e_live is not None and e_live["cgroup"] == scope and e_live["pid"] == pid and not e_live["gone"]
          and e_short is not None and e_short["cgroup"] == scope and e_short["gone"] is True
          and e_short["pid"] == outer.pid)
    result(7, ok, f"turn cgroup: live writer {short(e_live)}; short-lived writer (reaped before its event was "
           f"read, outer shell pid {outer.pid}): {short(e_short)}; same without stopping the watcher: {short(e_nat)}")
    try:
        os.rmdir(CG)
    except OSError:
        pass

    # 8: throughput.
    bench = f"{HOME}/bench"
    os.makedirs(bench, exist_ok=True)
    os.chown(bench, UID, UID)
    n = 20000
    code = ("import sys, time\nt0 = time.time()\nfor i in range(%d):\n"
            "    with open(f'%s/f{i}', 'w') as f: f.write('x')\nprint('%%.2f' %% (time.time() - t0))\n") % (n, bench)
    c0 = cpu(w.pid)
    t0 = time.monotonic()
    wt = subprocess.run([*AS_USER, PY, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    end = time.monotonic() + 600
    last_count, last_change = -1, time.monotonic()
    while time.monotonic() < end:
        with user.lock:
            got = user.bench_writes
        if got >= n:
            break
        if got != last_count:
            last_count, last_change = got, time.monotonic()
        elif time.monotonic() - last_change > 60:
            break
        time.sleep(0.2)
    with user.lock:
        got, last = user.bench_writes, user.bench_last
    c1 = cpu(w.pid)
    over = user.find(lambda e: e.get("op") in ("overflow", "unparsable"))
    took = max(0.0, last - t0)
    result(8, got == n and not over, f"throughput: one process wrote {n} files in {wt}s; {got} saves arrived, "
           f"the last {took:.1f}s after it started ({got / took if took else 0:.0f} saves/s); lost {n - got}; "
           f"overflow lines {len(over)}; watcher CPU {c1 - c0:.1f}s")

    # 10 (before 9, which restarts the watcher): /etc is routed to every user; other homes are not.
    subprocess.run(["sh", "-c", "echo x > /etc/bombadil-check10; echo y > /var/tmp/bombadil-check10; "
                    "mkdir -p /home/other && echo z > /home/other/check10.txt"], check=True)
    r_etc = root.wait(saved("/etc/bombadil-check10", "write"), 60)
    u_etc = user.wait(saved("/etc/bombadil-check10", "write"), 60)
    r_other = root.wait(saved("/home/other/check10.txt", "write"), 60)
    time.sleep(2)
    u_other = user.find(lambda e: e.get("path", "").startswith("/home/other"))
    any_var = root.find(lambda e: e.get("path", "").startswith("/var/tmp"))
    ok = bool(r_etc) and bool(u_etc) and bool(r_other) and not u_other and not any_var
    result(10, ok, f"system path: {short(u_etc[0] if u_etc else None)}; to root {bool(r_etc)}, to uid 1000 "
           f"{bool(u_etc)}; /home/other to root {bool(r_other)}, to uid 1000 {bool(u_other)}; "
           f"/var/tmp sent: {bool(any_var)}")

    # 9: offline catch-up.
    user.close()
    root.close()
    stop_watcher(w)
    say(f"generations after the stop: {open(f'{STATE}/generations.json').read().strip()}")
    offline = [f"{HOME}/off1.txt", f"{HOME}/Projects/off2.txt", f"{HOME}/off3.txt"]
    subprocess.run([*AS_USER, "sh", "-c", " && ".join(f"echo {i} > {p}" for i, p in enumerate(offline))],
                   check=True)
    w = start_watcher()
    time.sleep(2)
    user = Stream(UID)
    caught = user.wait(lambda e: e.get("op") == "caught_up", 120)
    offs = user.find(lambda e: e.get("op") == "offline")
    paths = {e["path"] for e in offs}
    at = caught[0]["_at"] if caught else None
    before = all(e["_at"] <= at for e in offs if e["path"] in offline) if at else False
    ok = bool(caught) and all(p in paths for p in offline) and before
    sample = [short(e) for e in offs if e["path"] in offline]
    result(9, ok, f"offline catch-up: {len(offs)} offline events ({len(paths & set(offline))} of the 3 new files), "
           f"then caught_up={short(caught[0] if caught else None)}; {sample}")
    user.close()
    stop_watcher(w)

    say("--- watcher log ---")
    say(open("/tmp/watch.log").read().rstrip())
    passed = sum(results.values())
    say(f"--- {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) == 10 else 1


if __name__ == "__main__":
    sys.exit(main())

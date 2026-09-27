"""Stop means stop: end a turn and everything it started, sudo'd commands included.

Each turn's CLI runs in its own systemd user scope when the session has a user manager,
so every process it starts stays findable in one cgroup, even ones that daemonized or
were reparented. Without systemd (dev machines, tests) the turn is the CLI's process tree.

Windows the turn opened (the browser, the terminal, an app) are yours now, so Stop leaves
them and their children alone. Everything else first gets SIGINT: the CLI saves the
conversation and sudo relays it to its command. Whatever is left gets SIGTERM, then
SIGKILL. Processes owned by root cannot be signalled by the user, so those without a sudo
to relay go through `sudo -n kill`; the agent has passwordless sudo on Bombadil.

pacman is the exception. Killed mid-package it leaves files on disk that its database
does not know about, and the next install fails. On SIGINT it finishes the package it is
extracting and then stops cleanly, so Stop freezes the rest of the turn (so the agent
cannot start it again), interrupts pacman alone, waits for it however long that takes, and
only then stops everything else. pacman and its hooks never get SIGTERM or SIGKILL. If
pacman left its lock behind, it is removed so the next install does not fail with "unable
to lock database".
"""

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")
CGROUP_ROOT = Path("/sys/fs/cgroup")
# The windows a turn may open; they and their children survive Stop. Matched on whole
# arguments, so a script that merely mentions one of them is not spared.
PROTECTED_ARGS = ("--class=bombadil-browser", "--app-id=bombadil-terminal", "--app-id=bombadil-details")
PACMAN_LOCK = Path("/var/lib/pacman/db.lck")


@dataclass
class Proc:
    pid: int
    ppid: int
    uid: int
    start: int         # start time in clock ticks: tells a reused pid apart
    comm: str
    cmdline: str       # argv joined with NUL, as /proc has it
    pgrp: int = 0      # process group


def read(pid: int) -> Proc | None:
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        cmd = (PROC / str(pid) / "cmdline").read_bytes().rstrip(b"\0").decode(errors="replace")
        status = (PROC / str(pid) / "status").read_text()
    except (OSError, ValueError):
        return None
    # comm is in parentheses and may itself contain spaces and parentheses.
    comm = stat[stat.index("(") + 1: stat.rindex(")")]
    fields = stat[stat.rindex(")") + 2:].split()
    uid = next((int(line.split()[1]) for line in status.splitlines() if line.startswith("Uid:")), -1)
    return Proc(pid, int(fields[1]), uid, int(fields[19]), comm, cmd, int(fields[2]))


def all_procs() -> dict[int, Proc]:
    out = {}
    for d in PROC.iterdir():
        if d.name.isdigit():
            p = read(int(d.name))
            if p is not None:
                out[p.pid] = p
    return out


def descendants(root: int, procs: dict[int, Proc]) -> set[int]:
    children: dict[int, list[int]] = {}
    for p in procs.values():
        children.setdefault(p.ppid, []).append(p.pid)
    out, todo = set(), [root]
    while todo:
        pid = todo.pop()
        for c in children.get(pid, []):
            if c not in out:
                out.add(c)
                todo.append(c)
    return out


def cgroup_of(pid: int) -> Path | None:
    """The cgroup v2 directory of a process, when it is a turn scope of ours."""
    try:
        for line in (PROC / str(pid) / "cgroup").read_text().splitlines():
            if line.startswith("0::"):
                path = CGROUP_ROOT / line[3:].lstrip("/")
                return path if path.name.startswith("bombadil-turn-") else None
    except OSError:
        pass
    return None


def scope_cgroup(unit: str | None, runner=subprocess.run) -> Path | None:
    """The cgroup of a turn's scope by its unit name, for when its first process is gone."""
    if not unit or not scope_supported():
        return None
    try:
        r = runner(["systemctl", "--user", "show", "-P", "ControlGroup", f"{unit}.scope"],
                   capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    cg = (r.stdout or "").strip()
    path = CGROUP_ROOT / cg.lstrip("/") if cg else None
    return path if path is not None and path.name.startswith("bombadil-turn-") else None


def cgroup_pids(cg: Path) -> set[int]:
    try:
        return {int(x) for x in (cg / "cgroup.procs").read_text().split()}
    except (OSError, ValueError):
        return set()


_scope_ok: bool | None = None


# --expand-environment=no: systemd-run would otherwise expand $VARS in the command's arguments.
SCOPE = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--expand-environment=no"]


def scope_supported() -> bool:
    """Can this session put a command in a transient systemd user scope? Checked once, with
    the same flags a turn uses, so an older systemd without one of them falls back cleanly."""
    global _scope_ok
    if _scope_ok is None:
        _scope_ok = False
        if os.environ.get("BOMBADIL_NO_SCOPE") != "1" and Path("/run/systemd/system").exists():
            try:
                r = subprocess.run([*SCOPE, "--", "true"], capture_output=True, timeout=5, check=False,
                                   stdin=subprocess.DEVNULL)
                _scope_ok = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
    return _scope_ok


def in_scope(cmd: list[str], unit: str) -> list[str]:
    """`cmd`, run in its own scope when possible. systemd-run --scope execs the command in
    place, so the pid, stdin and stdout are the command's own."""
    if not scope_supported():
        return cmd
    return [*SCOPE, f"--unit={unit}", "--", *cmd]


def _protected(pid: int, procs: dict[int, Proc], memo: dict[int, bool]) -> bool:
    """Is this process a window the turn opened, or a child of one?"""
    seen = []
    p = procs.get(pid)
    result = False
    while p is not None:
        if p.pid in memo:
            result = memo[p.pid]
            break
        seen.append(p.pid)
        if _is_window(p.cmdline):
            result = True
            break
        p = procs.get(p.ppid)
    for s in seen:
        memo[s] = result
    return result


class Stopper:
    def __init__(self, sudo=("sudo", "-n"), grace: float = 3.0, runner=subprocess.run,
                 pacman_grace: float = 900.0):
        self.sudo = list(sudo)
        self.grace = grace
        self.pacman_grace = pacman_grace   # how long a package may take to finish after Stop
        self._run = runner

    def members(self, root: int, cg: Path | None = None, pgid: int | None = None,
                roots=()) -> dict[int, Proc]:
        """Every live process of the turn rooted at `root`, minus the windows it opened: the
        root's descendants, everything in its scope's cgroup and process group, and the
        descendants of `roots` (earlier members, which may have been reparented since)."""
        procs = all_procs()
        pids = descendants(root, procs) | {root}
        for r in roots:
            pids |= descendants(r, procs) | {r}
        if cg is None:
            cg = cgroup_of(root)
        if cg is not None:
            pids |= cgroup_pids(cg)
        if pgid is not None and pgid > 1:
            pids |= {p.pid for p in procs.values() if p.pgrp == pgid}
        pids.discard(os.getpid())
        memo: dict[int, bool] = {}
        return {pid: procs[pid] for pid in pids if pid in procs and not _protected(pid, procs, memo)}

    def _signal(self, targets: dict[int, Proc], sig: int, relayed: bool = False) -> None:
        """Send `sig` to each target that is still the same process. With `relayed`, root
        commands whose sudo is a target too are left to sudo, which passes the signal on."""
        need_root = []
        for p in targets.values():
            now = read(p.pid)
            if now is None or now.start != p.start:
                continue  # gone, or the pid now belongs to someone else
            if relayed and p.uid == 0:
                parent = targets.get(p.ppid)
                if parent is not None and parent.comm == "sudo" and parent.uid != 0:
                    continue
            try:
                os.kill(p.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                need_root.append(p.pid)
        if need_root:
            self._run([*self.sudo, "kill", f"-{signal.Signals(sig).name.removeprefix('SIG')}", "--",
                       *map(str, need_root)], capture_output=True, check=False, timeout=5)

    @staticmethod
    def _alive(targets: dict[int, Proc]) -> dict[int, Proc]:
        out = {}
        for pid, p in targets.items():
            now = read(pid)
            if now is not None and now.start == p.start and not _zombie(pid):
                out[pid] = p
        return out

    def _wait(self, targets: dict[int, Proc], seconds: float) -> dict[int, Proc]:
        deadline = time.monotonic() + seconds
        alive = self._alive(targets)
        while alive and time.monotonic() < deadline:
            time.sleep(0.1)
            alive = self._alive(alive)
        return alive

    @staticmethod
    def _pacman_tree(members: dict[int, Proc]) -> tuple[dict[int, Proc], dict[int, Proc]]:
        """(pacman and the sudo above it, pacman's hooks and scriptlets) among the members."""
        pacmen = {pid: p for pid, p in members.items() if p.comm == "pacman"}
        if not pacmen:
            return {}, {}
        top = dict(pacmen)
        for p in pacmen.values():
            parent = members.get(p.ppid)
            while parent is not None and parent.comm == "sudo":
                top[parent.pid] = parent
                parent = members.get(parent.ppid)
        procs = all_procs()
        below = set()
        for pid in pacmen:
            below |= descendants(pid, procs)
        hooks = {pid: members.get(pid) or procs[pid] for pid in below if pid in procs and pid not in top}
        return top, hooks

    def stop(self, root: int, unit: str | None = None, notify=None) -> dict:
        """End the turn rooted at `root` (its scope is `unit`, when it has one). `notify(text)`
        is told when Stop has to wait for something. Returns what it did, for the log."""
        # Found once, while the root may still be alive: after it exits, its scope and
        # process group are the only ways to reach what it left running.
        cg = cgroup_of(root) or scope_cgroup(unit, self._run)
        first = self.members(root, cg, root)
        out = {"interrupted": 0, "terminated": 0, "killed": 0, "waited": 0.0}
        pacman, hooks = self._pacman_tree(first)
        busy = {}   # pacman still running after its long grace: left alone for good
        interrupted: set[int] = set()
        if pacman:
            frozen = {pid: p for pid, p in first.items() if pid not in pacman and pid not in hooks}
            self._signal(frozen, signal.SIGSTOP)
            try:
                if notify is not None:
                    notify("Stopping after this package")
                t0 = time.monotonic()
                self._signal(pacman, signal.SIGINT, relayed=True)
                busy = self._wait({pid: p for pid, p in pacman.items() if p.comm == "pacman"}, self.pacman_grace)
                out["waited"] = round(time.monotonic() - t0, 1)
            finally:
                # The rest gets its SIGINT before it runs again.
                self._signal(frozen, signal.SIGINT, relayed=True)
                self._signal(frozen, signal.SIGCONT)
                interrupted = set(frozen)
        spare = set(busy)
        if busy:
            spare |= set(pacman) | set(hooks)
        targets = {pid: p for pid, p in self.members(root, cg, root, roots=first).items() if pid not in spare}
        # 1. SIGINT: the CLI saves its conversation, sudo relays it.
        self._signal({pid: p for pid, p in targets.items() if pid not in interrupted}, signal.SIGINT, relayed=True)
        left = self._wait(targets, self.grace)
        # Anything that started meanwhile belongs to the turn too, even if its parent is gone.
        now = self.members(root, cg, root, roots=list(first) + list(targets))
        left.update({pid: p for pid, p in now.items() if pid not in targets and pid not in spare})
        # 2. SIGTERM, then 3. SIGKILL for whatever is still there.
        killed = {}
        if left:
            self._signal(left, signal.SIGTERM)
            killed = self._wait(left, 1.0)
            if killed:
                self._signal(killed, signal.SIGKILL)
        if pacman or any(p.comm == "pacman" for p in left.values()):
            self._unlock_pacman()
        out.update(interrupted=len(targets) + len(pacman), terminated=len(left), killed=len(killed))
        return out

    def _unlock_pacman(self) -> None:
        # Only a lock nobody holds: pacman may still be finishing on its own.
        for _ in range(20):
            if not any(p.comm == "pacman" for p in all_procs().values()):
                break
            time.sleep(0.25)
        else:
            return
        if PACMAN_LOCK.exists():
            self._run([*self.sudo, "rm", "-f", str(PACMAN_LOCK)], capture_output=True, check=False, timeout=5)


def _is_window(cmdline: str) -> bool:
    """cmdline is NUL-joined argv as read from /proc (see `read`)."""
    argv = cmdline.split("\0")
    if any(a in PROTECTED_ARGS for a in argv):
        return True
    names = [os.path.basename(a) for a in argv[:2]]
    if names and names[0] in ("nautilus",):
        return True
    # bombadil-app run <name>, started directly or as `python3 .../bombadil-app run <name>`
    return any(n == "bombadil-app" and argv[i + 1:i + 2] == ["run"] for i, n in enumerate(names))


def _zombie(pid: int) -> bool:
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        return stat[stat.rindex(")") + 2] == "Z"
    except (OSError, ValueError, IndexError):
        return True

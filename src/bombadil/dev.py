"""Coding sessions: the vendors' own tools, kept alive by Bombadil and brought back by name.

"claude latchkey" in the pill starts the real Claude Code in ~/Projects/latchkey, unchanged,
inside an invisible zellij session (no bars, no keys of its own) in its own systemd scope. The
window is only a viewer: closing it detaches, and typing the name again attaches a new viewer,
mid-sentence, scrollback and all. A session ends when its tool exits or on "end <name>";
agentd restarting, the bar restarting or the compositor crashing touch none of them.

Names: a session is a role on a project ("reviewer on Bombadil"). Without a role it is named
after its tool ("claude on Latchkey"), so typing "claude latchkey" again brings that one back.
The first session on a repository works in the checkout; each further one gets a copy of its
own (a git worktree under ~/Projects/.work/<repo>/<role>). Plain shells always open in the
checkout and never take it from anyone.

The registry (~/.local/state/bombadil/dev/sessions.json) is written by agentd alone. The tools'
hooks (bombadil-signal) and the wrapper that runs each tool in its pane (`bombadil dev run`)
report to agentd, which turns them into the dots beside the pill: moving while a session works,
lit when it is your turn (it asked something, or finished and you have not looked), red when it
failed, dim when it is asleep. Sessions started by hand in a plain terminal are seen through the
same hooks and listed as "yours"; Bombadil never wraps or restarts them.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import hypr, narrate, paths, procs

TOOLS = ("claude", "codex", "shell")
TOOL_TITLES = {"claude": "Claude", "codex": "Codex", "shell": "Shell"}
ROLE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
VIEWER = "bombadil-session-"       # the viewer windows' app id prefix
PENDING_SECONDS = 20               # a session may take this long to show up in zellij
LINES_KEPT = 6
ENDED_KEPT = 30 * 86400            # an ended session is forgotten after this long

# How the hooks' events move a session between states.
WORKING = {"UserPromptSubmit", "PostToolUse"}
ASKING = {"PermissionRequest", "Elicitation"}


def key(s: str) -> str:
    """'Latch Key' -> 'latchkey', as the launcher folds words; '' when it is not a plain word."""
    k = re.sub(r"[\s_-]+", "", str(s).lower())
    return k if re.fullmatch(r"[a-z0-9]+", k) else ""


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def project_title(name: str) -> str:
    """'latchkey' -> 'Latchkey'; a name with capitals already stays as it is ('iOS-app')."""
    return name if name != name.lower() else name[:1].upper() + name[1:]


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _first_line(text: str, n: int = 140) -> str:
    for line in str(text or "").splitlines():
        line = " ".join(line.split()).strip(" #*>`-")
        if line:
            return line if len(line) <= n else line[: n - 1].rstrip() + "…"
    return ""


def _pid_start(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat[stat.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return 0


def _pid_alive(pid: int, start: int) -> bool:
    now = _pid_start(pid) if pid > 0 else 0
    return now != 0 and (not start or now == start)


def share_file(rel: str) -> Path:
    """A file Bombadil ships. The whole tree sits at /usr/share/bombadil, so the checkout and the
    installed system find it the same way."""
    return Path(__file__).resolve().parents[2] / "share" / rel


def bombadil_bin() -> str:
    local = Path(__file__).resolve().parents[2] / "bin" / "bombadil"
    return str(local) if local.exists() else (shutil.which("bombadil") or "bombadil")


@dataclass
class Session:
    project: str                  # the project's folder name under ~/Projects ("latchkey")
    role: str                     # "reviewer"; the tool's name when none was given
    tool: str                     # claude | codex | shell
    folder: str                   # the checkout, or its copy
    copy: bool = False            # a Bombadil copy under ~/Projects/.work
    conversation: str = ""        # the tool's own session id: resume uses it
    zellij: str = ""              # the zellij session; "" for a session started by hand
    unit: str = ""                # its systemd scope
    run: str = ""                 # this run of it: tells a late word from an earlier run apart
    state: str = "idle"           # working | idle | asked | done | failed | asleep | ended
    alive: bool = True
    unseen: bool = False          # finished or failed while you were elsewhere
    started: float = 0.0
    since: float = 0.0            # when its state last changed
    boot: str = ""
    last: str = ""                # the question it asked, or the line it finished with
    lines: list = field(default_factory=list)   # a few plain lines, for the hover peek
    yours: bool = False           # started by hand in a plain terminal: seen, never managed
    pid: int = 0                  # the tool's process, from its hooks
    pid_start: int = 0
    code: int | None = None       # how its tool exited
    why: str = ""                 # its last screen, when it failed

    @property
    def key(self) -> str:
        return f"yours/{self.conversation}" if self.yours else f"{slug(self.project)}/{self.role}"

    @property
    def title(self) -> str:
        return f"{self.role} on {project_title(self.project)}"

    @property
    def app_id(self) -> str:
        return VIEWER + slug(self.key)

    def view(self) -> dict:
        """What the bar gets: enough for the dots and the hover peek, nothing raw."""
        return {"key": self.key, "project": self.project, "projectTitle": project_title(self.project),
                "role": self.role, "tool": self.tool, "toolTitle": TOOL_TITLES.get(self.tool, self.tool),
                "title": self.title, "state": self.state, "alive": self.alive, "unseen": self.unseen,
                "yours": self.yours, "copy": self.copy, "since": self.since, "last": self.last,
                "lines": list(self.lines[-4:])}

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


class Missing(Exception):
    """A word the launcher took for a session or a project turned out not to be one."""


class Dev:
    """The coding sessions, as agentd sees them. Every method is safe to call from any thread;
    `on_change` is called (from that thread) after anything the bar shows changed."""

    def __init__(self, hyprland: hypr.Hyprland | None = None, runner=subprocess.run, spawn=subprocess.Popen,
                 clock=time.time, on_change=None, load: bool = True):
        self.hypr = hyprland or hypr.Hyprland()
        self._run = runner
        self._spawn = spawn
        self.clock = clock
        self.on_change = on_change
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.front: str | None = None     # the session whose window is in front
        self.boot = boot_id()
        if load:
            self._load()

    # -- the registry --

    @staticmethod
    def registry() -> Path:
        return paths.dev_dir() / "sessions.json"

    def _load(self) -> None:
        try:
            data = json.loads(self.registry().read_text())
        except (OSError, ValueError):
            data = {}
        now = self.clock()
        for d in data.get("sessions", []) if isinstance(data, dict) else []:
            try:
                s = Session.from_dict(d)
            except TypeError:
                continue
            if s.yours and not _pid_alive(s.pid, s.pid_start):
                continue
            if s.state == "ended" and now - s.since > ENDED_KEPT:
                continue
            if s.alive and not s.yours and s.boot != self.boot:
                # Nothing resumes on its own after a restart: the dot comes back dim.
                s.alive, s.state, s.since = False, "asleep", now
                s.last = "Stopped by a restart"
            self.sessions[s.key] = s

    def _save(self) -> None:
        path = self.registry()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sessions": [asdict(s) for s in self.sessions.values()]}, indent=1))
        os.replace(tmp, path)

    def _changed(self) -> None:
        self._save()
        if self.on_change is not None:
            self.on_change()

    # -- names --

    def projects(self) -> dict[str, Path]:
        """{launcher key: folder} for each project folder in ~/Projects."""
        root = paths.projects_dir()
        out: dict[str, Path] = {}
        try:
            dirs = sorted(root.iterdir()) if root.is_dir() else []
        except OSError:
            return out
        for d in dirs:
            k = key(d.name)
            if k and not d.name.startswith(".") and k not in out and d.is_dir():
                out[k] = d
        return out

    def project(self, word: str) -> Path | None:
        k = key(word)
        return self.projects().get(k) if k else None

    def named(self, word: str) -> list[Session]:
        """Sessions a bare word names: a role you gave ("reviewer"), never the tool's own
        name (typing "claude" alone names no session), newest first."""
        k = key(word)
        if not k:
            return []
        with self.lock:
            found = [s for s in self.sessions.values()
                     if not s.yours and s.state != "ended" and key(s.role) == k and s.role != s.tool]
        return sorted(found, key=lambda s: -s.started)

    def on_project(self, folder: Path) -> list[Session]:
        with self.lock:
            return sorted((s for s in self.sessions.values()
                           if s.state != "ended" and key(s.project) == key(folder.name)),
                          key=lambda s: (-(s.alive), -max(s.since, s.started)))

    def names(self) -> list[dict]:
        """Launcher entries: each project with its three forms, and each named session."""
        out = []
        for k, d in self.projects().items():
            title = project_title(d.name)
            out.append({"name": k, "title": title, "kind": "project", "words": [d.name.lower()]})
            for tool in TOOLS:
                out.append({"name": f"{tool} {k}", "title": f"{TOOL_TITLES[tool]} on {title}", "kind": "session",
                            "words": [f"{tool} {d.name.lower()}"]})
        with self.lock:
            for s in self.sessions.values():
                if not s.yours and s.state != "ended" and s.role != s.tool:
                    out.append({"name": s.key, "title": s.title, "kind": "session", "words": [s.role]})
        return out

    # -- starting, bringing back and ending --

    def open(self, tool: str, project: str, role: str = "") -> tuple[bool, str]:
        """`claude|codex|shell <project> [role]`: bring that session back, resume it, or start it."""
        folder = self.project(project)
        if folder is None:
            raise Missing(project)
        role = role or tool
        if tool not in TOOLS:
            return False, f"{tool} is not a coding tool here: use claude, codex or shell."
        if not ROLE_RE.fullmatch(role):
            return False, "A session's name is a short word: letters, digits and dashes."
        with self.lock:
            k = f"{slug(folder.name)}/{role}"
            s = self.sessions.get(k)
            if s is not None and s.alive:
                if s.tool != tool:
                    return False, (f"{s.title} is a {TOOL_TITLES[s.tool]} session. "
                                   f"Say “{s.tool} {folder.name.lower()} {role}” to bring it back.")
                return self.bring(s)
            if s is not None and s.tool == tool and s.state != "ended" and s.conversation \
                    and Path(s.folder).is_dir():
                return self._launch(s, resume=True)
            return self._start(tool, folder, role, previous=s)

    def bring(self, s: Session) -> tuple[bool, str]:
        """A session by its name: its window to the front, a new viewer, or a resume."""
        with self.lock:
            if s.yours:
                return False, f"{s.title} runs in a terminal of yours; Bombadil cannot bring it back."
            if not s.alive:
                if s.state == "ended" or not Path(s.folder).is_dir():
                    return self._start(s.tool, Path(paths.projects_dir() / s.project), s.role, previous=s)
                if s.tool != "shell" and not s.conversation:
                    return self._start(s.tool, Path(paths.projects_dir() / s.project), s.role, previous=s)
                return self._launch(s, resume=s.tool != "shell")
            return True, f"Back to {s.title}.{self._show(s)}"

    def open_project(self, folder: Path) -> tuple[bool, str]:
        """A project's name alone: its most recent session, or how to start one."""
        found = self.on_project(folder)
        if not found:
            name = folder.name.lower()
            return True, (f"Nothing runs on {project_title(folder.name)} yet. "
                          f"Say “claude {name}”, “codex {name}” or “shell {name}”.")
        return self.bring(found[0])

    def end(self, s: Session) -> tuple[bool, str]:
        with self.lock:
            if s.yours:
                return False, f"{s.title} runs in a terminal of yours: end it there."
            if not s.alive:
                s.state, s.since, s.unseen = "ended", self.clock(), False
                self._changed()
                return True, f"Ended {s.title}."
            s.state, s.alive, s.since, s.unseen = "ended", False, self.clock(), False
            self._changed()
        self._kill(s)
        kept = " Its copy is kept." if s.copy else ""
        return True, f"Ended {s.title}.{kept}"

    def _kill(self, s: Session) -> None:
        if s.zellij:
            self._run([zellij(), "kill-session", s.zellij], capture_output=True, check=False, timeout=10)
        if s.unit and procs.scope_supported():
            # A hung zellij server: stopping its scope ends everything in it.
            self._run(["systemctl", "--user", "stop", f"{s.unit}.scope"], capture_output=True, check=False,
                      timeout=10)

    def _start(self, tool: str, folder: Path, role: str, previous: Session | None = None) -> tuple[bool, str]:
        where, copy, note = str(folder), False, ""
        if previous is not None and previous.copy and Path(previous.folder).is_dir():
            where, copy = previous.folder, True
        elif tool != "shell" and self._checkout_taken(folder):
            made = self._make_copy(folder, role)
            if made is None:
                note = " in the same folder, which is not a git repository"
            else:
                where, copy = str(made), True
        now = self.clock()
        s = Session(project=folder.name, role=role, tool=tool, folder=where, copy=copy,
                    conversation=str(uuid.uuid4()) if tool == "claude" else "",
                    zellij=f"bombadil-{slug(folder.name)[:24]}-{role}", started=now, since=now,
                    boot=self.boot)
        ok, line = self._launch(s, resume=False)
        if ok:
            shown = line.split(".", 1)[1] if "." in line else ""
            line = f"Started {s.title}" + (" in its own copy." if copy else (note + ".")) + shown
        return ok, line

    def _checkout_taken(self, folder: Path) -> bool:
        return any(s.alive and s.tool != "shell" and not s.copy and Path(s.folder) == folder
                   for s in self.sessions.values())

    def _make_copy(self, folder: Path, role: str) -> Path | None:
        """A git worktree of `folder` on its own branch, at ~/Projects/.work/<repo>/<role>."""
        if not (folder / ".git").exists():
            return None
        dest = paths.projects_dir() / ".work" / folder.name / role
        if dest.is_dir() and any(dest.iterdir()):
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        git = ["git", "-C", str(folder)]
        self._run([*git, "worktree", "prune"], capture_output=True, check=False, timeout=20)
        branch = f"bombadil/{role}"
        has = self._run([*git, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                        capture_output=True, check=False, timeout=20).returncode == 0
        args = [str(dest), branch] if has else ["-b", branch, str(dest), "HEAD"]
        self._run([*git, "worktree", "add", "--quiet", *args], capture_output=True, text=True, check=True, timeout=60)
        return dest

    def _launch(self, s: Session, resume: bool) -> tuple[bool, str]:
        """Start `s` in its own zellij session and scope, then open a viewer on it."""
        now = self.clock()
        if s.zellij in (self.listed() or ()):
            # Still running after all (agentd missed it): never start a second one.
            s.alive, s.state, s.since = True, "idle", now
            self.sessions[s.key] = s
            shown = self._show(s)
            self._changed()
            return True, f"Back to {s.title}.{shown}"
        s.alive, s.state, s.since, s.boot, s.code, s.why, s.unseen = True, "idle", now, self.boot, None, "", False
        s.pid = s.pid_start = 0
        s.last = "Resuming" if resume else "Starting"
        s.run = uuid.uuid4().hex[:12]
        s.unit = f"bombadil-dev-{slug(s.project)[:24]}-{s.role}-{s.run}"
        self.sessions[s.key] = s
        self._save()   # the wrapper reads its session from the registry
        z = zellij()
        # A dead session of that name would be resurrected instead of started.
        self._run([z, "delete-session", s.zellij], capture_output=True, check=False, timeout=10)
        cmd = [z, "--config", str(share_file("zellij/config.kdl")), "attach", "--create-background", s.zellij,
               "--close-on-exit", "--", bombadil_bin(), "dev", "run", s.key] + (["--resume"] if resume else [])
        if procs.scope_supported():
            # Its own scope in a slice per project: outside agentd, the bar and the compositor,
            # so none of them takes a session down with it.
            cmd = [*procs.SCOPE, f"--unit={s.unit}", f"--slice=bombadil-dev-{slug(s.project)[:24].replace('-', '_')}.slice",
                   "--", *cmd]
        else:
            s.unit = ""
        try:
            r = self._run(cmd, cwd=s.folder, env=session_env(s), stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, check=False, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as e:
            r = subprocess.CompletedProcess(cmd, 1, "", str(e))
        if r.returncode != 0 or not self._wait_listed(s.zellij):
            s.alive, s.state, s.last = False, "failed", _first_line(r.stderr or r.stdout) or "zellij did not start"
            self._changed()
            return False, f"Could not start {s.title}: {s.last}"
        s.last = ""
        shown = self._show(s)
        self._changed()
        return True, f"{'Resumed' if resume else 'Started'} {s.title}.{shown}"

    def _wait_listed(self, name: str, seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if name in (self.listed() or ()):
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)

    def listed(self) -> set[str] | None:
        """The zellij sessions running now; None when zellij could not be asked."""
        try:
            r = self._run([zellij(), "list-sessions", "--short", "--no-formatting"], capture_output=True, text=True,
                          check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0 and "No active zellij sessions" not in (r.stdout or "") + (r.stderr or ""):
            return None
        return {line.split()[0] for line in (r.stdout or "").splitlines() if line.strip() and "No active" not in line}

    # -- windows --

    def _show(self, s: Session) -> str:
        """Its viewer to the front, or a new viewer when none is open. Returns "" or what went
        wrong: the session runs either way."""
        try:
            self._viewer(s)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            return f" Its window did not open: {_first_line(str(e), 80)}"
        return ""

    def _viewer(self, s: Session) -> None:
        if self.hypr.available:
            try:
                if any(c.get("class") == s.app_id for c in self.hypr.clients()):
                    self.hypr.dispatch(f'hl.dsp.focus({{ window = "class:^({s.app_id})$" }})')
                    return
            except (OSError, RuntimeError, ValueError):
                pass
        elif self._run(["pgrep", "-f", "--", f" attach {re.escape(s.zellij)}$"], capture_output=True,
                       check=False).returncode == 0:
            return   # no compositor to ask: a viewer is attached, leave it be
        attach = [zellij(), "--config", str(share_file("zellij/config.kdl")), "attach", s.zellij]
        viewer = [f"--app-id={s.app_id}", f"--title={s.title}", f"--working-directory={s.folder}", "--", *attach]
        if foot_server() and shutil.which("footclient"):
            r = self._run(["footclient", "--no-wait", *viewer], capture_output=True, check=False, timeout=5)
            if r.returncode == 0:
                return
        self._spawn(["foot", *viewer], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True, env=viewer_env())

    def focused(self, window_class: str) -> None:
        """The window in front changed (Hyprland's activewindow event)."""
        k = None
        if window_class.startswith(VIEWER):
            with self.lock:
                k = next((s.key for s in self.sessions.values() if s.app_id == window_class), None)
        with self.lock:
            if k == self.front:
                return
            self.front = k
            s = self.sessions.get(k) if k else None
            if s is not None:
                self._looked(s)
        if self.on_change is not None:
            self.on_change()

    def _looked(self, s: Session) -> None:
        s.unseen = False
        if s.state == "done":
            s.state = "idle"

    # -- what the tools report --

    def signal(self, m: dict) -> Session | None:
        """One event from a tool's hook (bombadil-signal) or from a session's wrapper. Returns
        the session it was about, if any."""
        event = str(m.get("event", ""))
        with self.lock:
            s = self._session_for(m)
            if s is None:
                return None
            now = self.clock()
            before = (s.state, s.alive, s.last, s.unseen)
            text = str(m.get("text") or "")
            if m.get("pid"):
                s.pid, s.pid_start = int(m["pid"]), int(m.get("pid_start") or 0)
            if event == "SessionStart":
                if m.get("session_id"):
                    s.conversation = str(m["session_id"])   # /clear and /resume switch conversations
                if s.state not in ("working", "asked"):
                    s.state = "idle"
                s.last = ""
            elif event in WORKING:
                if event == "UserPromptSubmit":
                    self._line(s, "› " + _first_line(text, 100))
                    s.last = ""
                s.state, s.unseen = "working", False
            elif event in ASKING or (event == "PreToolUse" and m.get("tool_name") == "AskUserQuestion"):
                question = asked_text(m) or s.last
                if s.state != "asked" or question != s.last:
                    s.unseen = self.front != s.key
                    self._line(s, "? " + question)
                s.state, s.last = "asked", question
            elif event == "Notification":
                kind = m.get("notification_type")
                if kind == "permission_prompt" and s.state != "asked":
                    s.state, s.last = "asked", _first_line(text) or "waiting for you"
                    s.unseen = self.front != s.key
                elif kind == "idle_prompt" and s.state == "working":
                    s.state = "idle"   # Claude has no hook for Esc: its idle notice ends the turn
            elif event in ("Stop", "Interrupt", "agent-turn-complete"):
                s.last = _first_line(text) if event != "Interrupt" else "Interrupted"
                if s.last:
                    self._line(s, "✓ " + s.last if event != "Interrupt" else s.last)
                s.state = "idle" if (self.front == s.key or event == "Interrupt") else "done"
                s.unseen = s.state == "done"
            elif event == "StopFailure":
                s.state, s.unseen = "failed", self.front != s.key
                s.last = _first_line(text) or "The turn failed"
                self._line(s, "✗ " + s.last)
            elif event == "CwdChanged":
                if m.get("cwd") and s.yours:
                    s.folder = str(m["cwd"])
            elif event == "SessionEnd":
                if s.yours:
                    del self.sessions[s.key]
                    self._changed()
                    return s
            elif event == "exit":
                self._exited(s, m.get("code"), text)
            if (s.state, s.alive, s.last, s.unseen) != before:
                s.since = now
            self._changed()
            return s

    def _exited(self, s: Session, code, why: str) -> None:
        s.alive, s.code, s.pid = False, code, 0
        if code in (0, None) or s.tool == "shell":
            s.state, s.unseen = "ended", False
        else:
            s.state, s.unseen = "failed", True
            s.last = f"{TOOL_TITLES[s.tool]} stopped unexpectedly (exit {code})"
            s.why = why

    def _session_for(self, m: dict) -> Session | None:
        dev_id = str(m.get("dev_id") or "")
        if dev_id and dev_id in self.sessions:
            s = self.sessions[dev_id]
            if s.state == "ended" or (m.get("run") and m["run"] != s.run):
                return None   # ended, or an earlier run of the same name still saying goodbye
            return s
        if dev_id or m.get("event") == "exit":
            return None   # a session Bombadil ended and forgot
        sid = str(m.get("session_id") or "")
        if not sid:
            return None
        s = self.sessions.get(f"yours/{sid}")
        if s is None:
            if m.get("event") == "SessionEnd":
                return None
            s = self._yours(m, sid)
        return s

    def _yours(self, m: dict, sid: str) -> Session:
        """A session you started by hand: listed by the folder it runs in."""
        cwd = Path(str(m.get("cwd") or paths.home()))
        root, project = paths.projects_dir(), cwd.name or "home"
        try:
            rel = cwd.relative_to(root).parts
            if rel and rel[0] == ".work" and len(rel) > 1:
                project = rel[1]
            elif rel:
                project = rel[0]
        except ValueError:
            if cwd == paths.home():
                project = "home"
        now = self.clock()
        tool = str(m.get("tool") or "claude")
        s = Session(project=project, role=tool, tool=tool, folder=str(cwd), conversation=sid, yours=True,
                    started=now, since=now, boot=self.boot)
        self.sessions[s.key] = s
        return s

    def _line(self, s: Session, line: str) -> None:
        if line.strip("›?✓✗ "):
            s.lines = (s.lines + [line])[-LINES_KEPT:]

    def check(self) -> None:
        """Every few seconds: notice sessions that went away without saying so."""
        with self.lock:
            ours = [s for s in self.sessions.values() if s.alive and not s.yours]
            yours = [s for s in self.sessions.values() if s.yours]
        changed = False
        listed = self.listed() if ours else set()
        if listed is None:
            return   # zellij could not be asked: never take that for "gone"
        now = self.clock()
        with self.lock:
            for s in ours:
                if s.alive and s.zellij not in listed and now - s.since > PENDING_SECONDS \
                        and self.sessions.get(s.key) is s:
                    s.alive, s.state, s.unseen, s.since = False, "failed", True, now
                    s.last = s.last if s.code is not None else "Stopped unexpectedly"
                    changed = True
            for s in yours:
                if s.pid and not _pid_alive(s.pid, s.pid_start) and self.sessions.get(s.key) is s:
                    del self.sessions[s.key]
                    changed = True
            if changed:
                self._changed()

    # -- whose turn it is --

    def attention(self) -> list[Session]:
        """Sessions waiting for you that you have not looked at since, the one in front aside:
        questions first, then finished, then failed, oldest first within each. A session you
        looked at keeps its lit dot but leaves the line."""
        rank = {"asked": 0, "done": 1, "failed": 2}
        with self.lock:
            out = [s for s in self.sessions.values() if s.key != self.front and s.unseen
                   and ((s.state == "asked" and s.alive) or s.state in ("done", "failed"))]
        return sorted(out, key=lambda s: (rank[s.state], s.since))

    def line(self) -> str:
        """The one line in the pill while something waits for you ("" when nothing does)."""
        waiting = self.attention()
        if not waiting:
            return ""
        if len(waiting) == 1:
            s = waiting[0]
            if s.state == "asked":
                return f"{s.title}: {s.last or 'waiting for you'}"
            if s.state == "done":
                return f"{s.title} finished" + (f": {s.last}" if s.last else ".")
            return f"{s.title} stopped: {s.last}" if s.last else f"{s.title} stopped."
        # A named role reads alone ("api and reviewer"); a tool's name needs its project.
        roles = [s.role if s.role != s.tool else s.title for s in waiting]
        names = roles if len(set(roles)) == len(roles) else [s.title for s in waiting]
        both = ", ".join(names[:-1]) + " and " + names[-1]
        return f"{both} are waiting" if len(names) < 5 else f"{len(names)} sessions are waiting"

    def next(self) -> tuple[bool, str]:
        """Tab from the empty pill: the next session waiting for you comes to the front."""
        waiting = self.attention()
        if not waiting:
            return True, "Nothing needs you."
        s = waiting[0]
        with self.lock:
            if s.state == "failed" and not s.alive:
                s.unseen = False
                self._changed()
                return True, f"{s.title} stopped: {s.last}. Type its name to resume it."
        ok, line = self.bring(s)
        with self.lock:
            self._looked(s)
            self.front = s.key if s.alive else self.front
            self._changed()
        return ok, line

    def snapshot(self) -> dict:
        with self.lock:
            shown = [s for s in self.sessions.values() if s.state != "ended"]
            shown.sort(key=lambda s: (key(s.project), s.yours, s.started))
            return {"type": "dev", "sessions": [s.view() for s in shown], "front": self.front,
                    "attention": [s.key for s in self.attention()], "line": self.line()}

    def listing(self) -> list[str]:
        """"what's running?" as plain lines, for the details drawer."""
        out = []
        now = self.clock()
        with self.lock:
            for s in sorted(self.sessions.values(), key=lambda s: (key(s.project), s.started)):
                if s.state == "ended":
                    continue
                mins = int((now - s.since) // 60)
                since = "just now" if mins < 1 else (f"{mins} min" if mins < 120 else f"{mins // 60} h")
                what = {"working": "working", "idle": "ready", "asked": "waiting for you", "done": "finished",
                        "failed": "stopped", "asleep": "asleep"}.get(s.state, s.state)
                where = " · own copy" if s.copy else (" · yours" if s.yours else "")
                out.append(f"{s.title:<32} {TOOL_TITLES.get(s.tool, s.tool):<7} {what} for {since}{where}")
                if s.last:
                    out.append(f"    {s.last}")
        return out or ["No coding sessions. Say “claude <project>” to start one."]


def asked_text(m: dict) -> str:
    """'Run the migration?' from a hook's tool name and (trimmed) input."""
    inp = m.get("tool_input") if isinstance(m.get("tool_input"), dict) else {}
    name = str(m.get("tool_name") or "")
    qs = inp.get("questions")
    if isinstance(qs, list) and qs and isinstance(qs[0], dict) and qs[0].get("question"):
        return _first_line(str(qs[0]["question"]))
    if name == "Bash":
        what = _first_line(str(inp.get("description") or "")) or _first_line(str(inp.get("command") or ""), 60)
        return (what.rstrip(".?") + "?") if what else "run a command?"
    if name:
        step = narrate.tool_step(name, inp)
        if step is not None:
            return "allow " + step.text[:1].lower() + step.text[1:] + "?"
    return _first_line(str(m.get("text") or ""))


def zellij() -> str:
    return shutil.which("zellij") or "zellij"


def foot_server() -> bool:
    run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    return Path(run, f"foot-{display}.sock").exists()


def _clean_env() -> dict:
    env = dict(os.environ)
    # agentd's own turn marks (a session started from a turn is still not the machine's agent),
    # and a zellij we may be running inside.
    for k in ("BOMBADIL_OS", "BOMBADIL_TURN_SNAPSHOT", "ZELLIJ", "ZELLIJ_SESSION_NAME", "ZELLIJ_PANE_ID"):
        env.pop(k, None)
    return env


def viewer_env() -> dict:
    return _clean_env()


def session_env(s: Session) -> dict:
    env = _clean_env()
    env.update(BOMBADIL_SESSION="1", BOMBADIL_DEV_ID=s.key, BOMBADIL_DEV_RUN=s.run, BOMBADIL_PROJECT=s.project)
    return env


def command_for(s: Session, resume: bool) -> list[str]:
    """The tool's own command line: unchanged, only named and pointed at its conversation."""
    if s.tool == "claude":
        argv = ["claude", "--resume" if resume else "--session-id", s.conversation, "-n", s.title]
    elif s.tool == "codex":
        # --no-daemon: the shared Codex daemon would give every session one environment (and its
        # hooks the daemon's), and it updates itself under running work.
        argv = ["codex", "--no-daemon"] + (["resume", s.conversation] if resume and s.conversation else [])
    else:
        return [os.environ.get("SHELL") or "/bin/bash"]
    if shutil.which("mise"):
        argv = ["mise", "exec", "--", *argv]   # the project's own toolchains, as its shell would have them
    return argv


def run(dev_key: str, resume: bool = False, report=None) -> int:
    """`bombadil dev run <key>`: the command in a session's zellij pane. Runs the tool, then says
    how it exited and ends the zellij session, so an exited tool never leaves an empty one."""
    try:
        data = json.loads(Dev.registry().read_text())
        s = next(x for x in map(Session.from_dict, data.get("sessions", [])) if x.key == dev_key)
    except (OSError, ValueError, StopIteration, TypeError, AttributeError) as e:
        print(f"bombadil: no coding session {dev_key!r} ({e})", file=sys.stderr)
        return 2
    argv = command_for(s, resume)
    # Ctrl+C and friends are the tool's; this wrapper only waits. A handler (not SIG_IGN) is
    # reset to the default in the child, so the tool gets its signals as usual.
    for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP):
        signal.signal(sig, lambda *_: None)
    try:
        code = subprocess.call(argv, cwd=s.folder)
    except OSError as e:
        print(f"bombadil: could not start {argv[0]}: {e}", file=sys.stderr)
        code = 127
    if code in (-signal.SIGHUP, -signal.SIGTERM, -signal.SIGKILL):
        return 0   # ended from outside ("end", a restart): whoever did it knows
    why = ""
    name = os.environ.get("ZELLIJ_SESSION_NAME")
    if code != 0 and s.tool != "shell" and name:
        # The last screen, for "Why?": it goes with the session.
        path = paths.dev_dir() / f"{slug(dev_key)}.last"
        pane = os.environ.get("ZELLIJ_PANE_ID", "0")
        _quietly([zellij(), "action", "dump-screen", "--full", "--pane-id", f"terminal_{pane}", "--path", str(path)])
        why = str(path) if path.exists() else ""
    (report or send_signal)({"type": "dev-signal", "event": "exit", "dev_id": dev_key, "code": code, "text": why,
                             "run": os.environ.get("BOMBADIL_DEV_RUN", "")})
    if name:
        _quietly([zellij(), "kill-session", name])
    return code


def _quietly(argv: list[str]) -> None:
    try:
        subprocess.run(argv, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def spool() -> Path:
    """Signals that arrived while agentd was down; it reads them when it starts."""
    return paths.dev_dir() / "signals.jsonl"


def send_signal(msg: dict) -> None:
    """Give agentd one message, or leave it in the spool when agentd is not there."""
    import socket
    line = (json.dumps(msg) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(str(paths.socket_path()))
            sock.sendall(line)
            sock.shutdown(socket.SHUT_WR)
            while sock.recv(65536):
                pass
        return
    except OSError:
        pass
    try:
        spool().parent.mkdir(parents=True, exist_ok=True)
        with spool().open("ab") as f:
            f.write(line)
    except OSError:
        pass


def take_spool() -> list[dict]:
    path = spool()
    try:
        text = path.read_text()
        path.unlink()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if isinstance(m, dict):
            out.append(m)
    return out

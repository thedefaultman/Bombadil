"""The jobs registry: what the machine is counting in the background.

A job is a command the agent started with the os-mcp `job` tool (a download, an install, a
build), a watcher on something already running ("tell me when the build finishes") or a timer.
Each runs as a transient systemd user unit, so it outlives the turn that started it and Stop
on the turn never reaches it. `Jobs` keeps one record per job in ~/.local/state/bombadil/jobs/:

    <id>.json   the record: title, kind, when it started, its state, what it last said
    <id>.log    the job's output, appended by the job itself
    <id>.exit   its exit status, written by the job's wrapper when the command ends

`poll()` reads those files, and asks systemd whether a job with no exit status is still there;
`snapshot()` is the table the shell draws as the Watching card. agentd owns the loop that calls
poll and the words said when a job ends (`ending`); this module only knows what happened.

Every method takes the lock, since agentd calls them from worker threads and from its loop.
Ids are ones this module makes (hex) and are checked before any path or unit name is built from
one; a title never becomes part of a unit name or a command line, and a command is quoted only
through shlex. Nothing here starts a model turn, and stopping a job never involves one.
"""

import json
import math
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import paths

KINDS = ("job", "watch", "timer")
STATES = ("running", "done", "failed")
ID_RE = re.compile(r"[0-9a-f]{4,12}")

DONE_STAYS = 15.0            # seconds a finished job's row stays in the table
FAILED_STAYS = 30 * 60.0     # a failed one stays until dismissed, or this long
KEEP = 24 * 3600.0           # a record and its log are deleted this long after the job ended
MAX_RUNNING = 20             # jobs counting at once; more is a runaway loop, not a passenger's list
MAX_LAST = 120               # characters of the last line of output
TAIL = 4096                  # bytes at the end of the log that say how far along it is
MAX_TITLE = 80
MAX_COMMAND = 20_000
MAX_SECONDS = 7 * 24 * 3600
CALL_TIMEOUT = 15.0          # seconds one systemd call may take

_PERCENT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?%")
_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


class JobError(Exception):
    """Something asked of the registry that cannot be done, said as one plain sentence."""


# -- words --

def span(seconds: float) -> str:
    """How long, the way the desk says it: "40 s", "3 min", "1 h 5 min"."""
    n = max(0, int(seconds + 0.5))
    if n < 60:
        return f"{n} s"
    minutes = int(n / 60 + 0.5)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h" + (f" {minutes} min" if minutes else "")


def timer_title(seconds: float) -> str:
    return f"Timer, {span(seconds)}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _clean(text, limit: int = MAX_TITLE) -> str:
    """One line of printable text: the title as it is shown."""
    s = " ".join(str(text if text is not None else "").split())
    return "".join(c for c in s if c.isprintable())[:limit].strip()


def ending(rec: dict) -> tuple[str, bool]:
    """The one line for a job that has just ended, and whether it went well: "Ubuntu 26.04 ISO is
    done in 3 min.", "Timer, 10 min is up.", "Build failed: pacman: could not resolve host"."""
    title = _cap(rec["title"])
    if rec["state"] == "failed":
        last = rec.get("last") or ""
        return (f"{title} failed: {last}" if last else f"{title} failed."), False
    if rec["kind"] == "timer":
        return f"{title} is up.", True
    return f"{title} is done in {span((rec.get('ended') or rec['started']) - rec['started'])}.", True


def started_text(rec: dict, log: Path) -> str:
    """What the agent is told when it starts a job."""
    if rec["kind"] == "timer":
        length = rec["deadline"] - rec["started"]
        named = "" if rec["title"] == timer_title(length) else f" ({rec['title']})"
        return (f"Timer set for {span(length)}{named} as job {rec['id']}. The desk counts it down and says "
                "so when it is up.")
    return (f"Started {rec['title']} as job {rec['id']}. The desk counts it and says so when it ends; "
            f"its output goes to {log}. Stop it with op stop and id {rec['id']}.")


def title_of(job_id) -> str:
    """The title of the job with this id, or "" when there is none. For the agent's line."""
    if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id):
        return ""
    try:
        return _clean(json.loads((paths.jobs_dir() / f"{job_id}.json").read_text(encoding="utf-8"))["title"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


# -- reading what a job wrote --

def reading(text: str) -> tuple[float | None, str]:
    """How far along the output says the job is (the last NN%, clamped to 0..100, else None) and
    its last non-empty line, at most MAX_LAST characters. Progress bars redraw with a carriage
    return, so those count as lines too."""
    text = _ESCAPES.sub("", text)
    found = _PERCENT.findall(text)
    pct = min(100.0, max(0.0, float(found[-1]))) if found else None
    last = next((line for line in (x.strip() for x in reversed(re.split(r"[\r\n]+", text))) if line), "")
    if len(last) > MAX_LAST:
        last = last[:MAX_LAST - 1] + "…"
    return pct, last


def _tail(path: Path, size: int = TAIL) -> str:
    try:
        with path.open("rb") as f:
            end = f.seek(0, os.SEEK_END)
            f.seek(max(0, end - size))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _why(e: Exception) -> str:
    """Why a systemd call did not work, in a few words."""
    if isinstance(e, subprocess.TimeoutExpired):
        return "systemd did not answer in time"
    if isinstance(e, FileNotFoundError):
        return "systemd is not available here"
    return " ".join(str(e).split())[:200] or type(e).__name__


def _said(r) -> str:
    """The last line a failed command said, without its full stop."""
    lines = [x.strip() for x in str(getattr(r, "stderr", "") or getattr(r, "stdout", "") or "").splitlines()
             if x.strip()]
    return lines[-1].rstrip(".")[:200] if lines else f"systemd said {getattr(r, 'returncode', 'no')}"


def _unit(kind: str, job_id: str) -> str:
    return f"bombadil-{'timer' if kind == 'timer' else 'job'}-{job_id}"


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _seconds(x) -> int:
    """A timer's length, whole seconds, or JobError."""
    try:
        n = None if x is None or isinstance(x, bool) else float(x)
    except (TypeError, ValueError):
        n = None
    if n is None or not math.isfinite(n):
        raise JobError("A timer needs a number of seconds.")
    if not 1 <= n <= MAX_SECONDS:
        raise JobError("A timer runs for between 1 second and 7 days.")
    return math.ceil(n)


class Jobs:
    def __init__(self, directory: Path | None = None, runner=subprocess.run, clock=time.time):
        self._dir = directory
        self._run = runner
        self._clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._load()

    @property
    def dir(self) -> Path:
        return self._dir or paths.jobs_dir()

    def _file(self, job_id: str, ext: str) -> Path:
        if not ID_RE.fullmatch(job_id):
            raise ValueError(f"not a job id: {job_id!r}")
        return self.dir / f"{job_id}.{ext}"

    def _known(self, job_id) -> dict | None:
        """The record for an id somebody sent us, None for anything that is not one of ours."""
        if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id):
            return None
        return self._jobs.get(job_id)

    # -- keeping the records --

    def _load(self):
        """Read the records a last run left, so a restarted agentd counts what is still running."""
        with self._lock:
            try:
                files = sorted(self.dir.glob("*.json"))
            except OSError:
                return
            for f in files:
                rec = self._read(f)
                if rec is not None:
                    self._jobs[rec["id"]] = rec
            self._expire(self._clock())

    @staticmethod
    def _read(f: Path) -> dict | None:
        """One record, or None when the file is not a job of ours. The file's name is its id."""
        if not ID_RE.fullmatch(f.stem):
            return None
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (not isinstance(raw, dict) or raw.get("id") != f.stem or raw.get("kind") not in KINDS
                or raw.get("state") not in STATES or not _number(raw.get("started"))):
            return None

        def opt(key):
            return float(raw[key]) if _number(raw.get(key)) else None
        code = raw.get("exit")
        return {"id": f.stem, "title": _clean(raw.get("title")) or "A job", "kind": raw["kind"],
                "command": str(raw.get("command") or "")[:MAX_COMMAND], "started": float(raw["started"]),
                "deadline": opt("deadline"), "ended": opt("ended"), "state": raw["state"],
                "exit": code if isinstance(code, int) and not isinstance(code, bool) else None,
                "pct": opt("pct"), "last": _clean(raw.get("last"), MAX_LAST),
                "dismissed": raw.get("dismissed") is True}

    def _save(self, rec: dict) -> bool:
        """Write the record, all or nothing. A disk that will not take it never costs the job."""
        path = self._file(rec["id"], "json")
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            print(f"jobs: could not save {path}: {e}", file=sys.stderr)
            return False
        return True

    def _forget(self, rec: dict):
        """Drop a job and everything it left."""
        self._jobs.pop(rec["id"], None)
        for ext in ("json", "log", "exit"):
            try:
                self._file(rec["id"], ext).unlink()
            except OSError:
                pass

    def _expire(self, now: float):
        for rec in list(self._jobs.values()):
            if rec["state"] != "running" and rec["ended"] is not None and now - rec["ended"] > KEEP:
                if not rec["dismissed"]:
                    self._reset_failed(rec)
                self._forget(rec)

    def _reset_failed(self, rec: dict):
        """A unit that failed stays loaded until told otherwise; nobody needs it once its row is gone."""
        if rec["state"] == "failed":
            try:
                self._run(["systemctl", "--user", "reset-failed", _unit(rec["kind"], rec["id"])],
                          capture_output=True, text=True, timeout=CALL_TIMEOUT, check=False)
            except (OSError, subprocess.SubprocessError):
                pass

    # -- starting --

    def start(self, title, command="", kind="job", seconds=None, cwd=None) -> dict:
        """Run `command` as a job (or a watch, a one-shot watcher whose command waits for what it
        watches) or, with `seconds`, count down a timer, which runs `command` when it is up if it
        has one. Returns the record. Raises JobError, in a plain sentence, when it cannot."""
        if seconds is not None or kind == "timer":
            kind, secs = "timer", _seconds(seconds)
        elif kind in ("job", "watch"):
            secs = 0
        else:
            raise JobError(f"There is no kind of job called {str(kind)[:20]!r}. Use job or watch, or give "
                           "seconds for a timer.")
        title, command = _clean(title), str(command if command is not None else "").strip()
        if kind == "timer":
            title = title or timer_title(secs)
        else:
            if not title:
                raise JobError("Give the job a short name for the desk to show, like “Ubuntu 26.04 ISO”.")
            if not command:
                raise JobError("Say what to run: the command is empty.")
        if len(command) > MAX_COMMAND:
            raise JobError(f"The command is too long ({len(command)} characters); put it in a script and "
                           "run that.")
        if "\x00" in command:
            raise JobError("The command has a NUL byte in it.")
        folder = None
        if cwd:
            folder = Path(str(cwd)).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise JobError(f"{cwd} is not a folder.")
        with self._lock:
            now = self._clock()
            self._expire(now)
            if sum(r["state"] == "running" for r in self._jobs.values()) >= MAX_RUNNING:
                raise JobError(f"{MAX_RUNNING} jobs are already running. Stop one before starting another.")
            job_id = self._new_id()
            rec = {"id": job_id, "title": title, "kind": kind, "command": command, "started": now,
                   "deadline": now + secs if kind == "timer" else None, "ended": None, "state": "running",
                   "exit": None, "pct": None, "last": "", "dismissed": False}
            if not self._save(rec):
                raise JobError(f"Could not start {title}: the jobs folder {self.dir} cannot be written.")
            try:
                r = self._run(self._argv(rec, secs, folder), capture_output=True, text=True,
                              timeout=CALL_TIMEOUT, check=False, stdin=subprocess.DEVNULL)
                refused = _said(r) if r.returncode != 0 else ""
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                refused = _why(e)
            if refused:
                self._forget(rec)
                raise JobError(f"Could not start {title}: {refused}.")
            self._jobs[job_id] = rec
            return dict(rec)

    def _new_id(self) -> str:
        for _ in range(50):
            job_id = secrets.token_hex(3)
            if job_id not in self._jobs and not self._file(job_id, "json").exists():
                return job_id
        raise JobError("Could not find a free job id.")

    def _wrapper(self, rec: dict) -> str:
        """What the unit's shell runs: the command with its output appended to the log, then its exit
        status written to the exit file, and the unit ends with it. The command runs in a shell of
        its own so an `exit` in it cannot skip the status. A timer with no command just says it is up."""
        exit_file = shlex.quote(str(self._file(rec["id"], "exit")))
        if rec["kind"] == "timer" and not rec["command"]:
            return f"echo 0 > {exit_file}"
        log = shlex.quote(str(self._file(rec["id"], "log")))
        return f"/bin/sh -c {shlex.quote(rec['command'])} >> {log} 2>&1; s=$?; echo $s > {exit_file}; exit $s"

    def _argv(self, rec: dict, secs: int, folder: Path | None) -> list[str]:
        unit = _unit(rec["kind"], rec["id"])
        # systemd expands $NAME in a unit's command line; $$ is the one dollar sign the shell sees.
        wrapper = self._wrapper(rec).replace("$", "$$")
        where = [f"--working-directory={folder}"] if folder else []
        if rec["kind"] == "timer":
            # A timer may fire up to a minute late unless it is told to be exact.
            return ["systemd-run", "--user", f"--unit={unit}", f"--on-active={secs}s",
                    "--timer-property=AccuracySec=1s", *where, "/bin/sh", "-c", wrapper]
        return ["systemd-run", "--user", f"--unit={unit}", f"--description={rec['title']}", *where,
                "/bin/sh", "-c", wrapper]

    # -- looking --

    def running(self) -> bool:
        with self._lock:
            return any(r["state"] == "running" for r in self._jobs.values())

    def poll(self) -> list[dict]:
        """Look once at every running job. Returns the jobs that have ended (done or failed) since
        the last look, each once. A job that was stopped from outside leaves without a word."""
        ended = []
        with self._lock:
            now = self._clock()
            for rec in list(self._jobs.values()):
                if rec["state"] == "running" and self._look(rec, now):
                    ended.append(dict(rec))
            self._expire(now)
        return ended

    def _look(self, rec: dict, now: float) -> bool:
        """One look at a running job: True when it has just ended."""
        code = self._exit_code(rec)
        if code is None:
            unit = self._unit_state(rec)
            if unit == "active":
                if self._reading(rec):
                    self._save(rec)
                return False
            code = self._exit_code(rec)   # it may have written its status as its unit ended
            if code is None and unit == "gone":
                self._forget(rec)         # stopped from outside: systemctl stop, a reboot
                return False
        rec.update(state="done" if code == 0 else "failed", exit=code, ended=now)
        self._reading(rec)
        if rec["state"] == "done" and rec["pct"] is not None:
            rec["pct"] = 100.0
        if rec["state"] == "failed" and not rec["last"]:
            rec["last"] = f"exit status {code}" if code is not None else "it stopped before it finished"
        self._save(rec)
        return True

    def _exit_code(self, rec: dict) -> int | None:
        try:
            return int(self._file(rec["id"], "exit").read_text().strip())
        except (OSError, ValueError):
            return None   # none yet, or one still being written

    def _reading(self, rec: dict) -> bool:
        """Take the progress and the last line from the end of the log. True when they changed."""
        pct, last = reading(_tail(self._file(rec["id"], "log")))
        changed = (pct, last) != (rec["pct"], rec["last"])
        rec["pct"], rec["last"] = pct, last
        return changed

    def _units(self, rec: dict) -> list[str]:
        unit = _unit(rec["kind"], rec["id"])
        return [f"{unit}.timer", f"{unit}.service"] if rec["kind"] == "timer" else [unit]

    def _unit_state(self, rec: dict) -> str:
        """"active" while systemd has the unit (or a timer, waiting or running its command), "failed"
        when it died without a status, "gone" when it is not there. When systemd cannot be asked,
        "active": a job is never dropped on a guess."""
        try:
            r = self._run(["systemctl", "--user", "is-active", *self._units(rec)], capture_output=True,
                          text=True, timeout=CALL_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError):
            return "active"
        states = str(r.stdout or "").split()
        if not states:
            return "active"
        if any(s in ("active", "activating", "reloading", "deactivating") for s in states):
            return "active"
        return "failed" if "failed" in states else "gone"

    # -- what the shell and the agent see --

    def _showing(self, rec: dict, now: float) -> bool:
        if rec["dismissed"]:
            return False
        if rec["state"] == "running" or rec["ended"] is None:
            return True
        return now - rec["ended"] <= (DONE_STAYS if rec["state"] == "done" else FAILED_STAYS)

    def snapshot(self) -> dict:
        """The table, in the order the jobs began: what the Watching card shows."""
        with self._lock:
            now = self._clock()
            rows = [{"id": r["id"], "title": r["title"], "kind": r["kind"], "state": r["state"],
                     "started": r["started"], "deadline": r["deadline"], "ended": r["ended"],
                     "pct": r["pct"], "last": r["last"], "unit": _unit(r["kind"], r["id"])}
                    for r in sorted(self._jobs.values(), key=lambda r: (r["started"], r["id"]))
                    if self._showing(r, now)]
            return {"type": "jobs", "jobs": rows}

    def listing(self) -> str:
        """The table in words, for the agent's `list`."""
        with self._lock:
            now = self._clock()
            rows = self.snapshot()["jobs"]
            lines = []
            for row in rows:
                if row["state"] == "running":
                    if row["kind"] == "timer":
                        state = f"{span(max(0, row['deadline'] - now))} left"
                    else:
                        state = f"running {span(now - row['started'])}"
                else:
                    state = row["state"]
                said = [row["id"], row["title"], state]
                if row["pct"] is not None and row["state"] == "running":
                    said.append(f"{row['pct']:.0f}%")
                if row["last"]:
                    said.append(row["last"])
                lines.append(" · ".join(said))
            return "\n".join(lines) if lines else "Nothing is running in the background."

    def log_path(self, job_id) -> Path | None:
        with self._lock:
            return self._file(job_id, "log") if self._known(job_id) is not None else None

    def why(self, job_id) -> list[str] | None:
        """The command that shows a job's output in the details drawer, or None for no such job. It
        waits for a key, as the drawer's other programs do, so Esc closes it."""
        with self._lock:
            rec = self._known(job_id)
            if rec is None:
                return None
            script = ("printf '\\033[1m%s\\033[0m\\n\\n' \"$2\"; tail -n 300 -- \"$1\" 2>/dev/null; "
                      "printf '\\n\\033[2mPress Esc to close.\\033[0m'; read -rsn1")
            return ["bash", "-c", script, "bash", str(self._file(job_id, "log")), rec["title"]]

    # -- ending --

    def stop(self, job_id) -> dict | None:
        """Stop a job (`systemctl --user stop` on its unit, and on a timer's own unit too) and drop it
        at once, with its output. Returns its record, or None when there is no such job. Raises
        JobError when systemd would not stop it, and then the job stays."""
        with self._lock:
            rec = self._known(job_id)
            if rec is None:
                return None
            if rec["state"] == "running":
                units = [_unit(rec["kind"], rec["id"])] + ([f"{_unit(rec['kind'], rec['id'])}.timer"]
                                                           if rec["kind"] == "timer" else [])
                try:
                    r = self._run(["systemctl", "--user", "stop", *units], capture_output=True, text=True,
                                  timeout=CALL_TIMEOUT, check=False)
                    refused = _said(r) if r.returncode != 0 and self._unit_state(rec) == "active" else ""
                except (OSError, subprocess.SubprocessError) as e:
                    refused = _why(e)
                if refused:
                    raise JobError(f"Could not stop {rec['title']}: {refused}.")
            self._reset_failed(rec)
            self._forget(rec)
            return dict(rec)

    def dismiss(self, job_id) -> dict | None:
        """Drop a finished job's row (the record and its output stay for a day). A job still
        running is not dismissed: it is stopped, or it ends. Returns the record, or None."""
        with self._lock:
            rec = self._known(job_id)
            if rec is None or rec["state"] == "running":
                return None
            rec["dismissed"] = True
            self._save(rec)
            self._reset_failed(rec)
            return dict(rec)

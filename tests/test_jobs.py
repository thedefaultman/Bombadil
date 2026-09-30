import json
import random
import re
import shlex
import subprocess
import threading

import pytest

from bombadil import jobs, paths
from bombadil.jobs import JobError, Jobs

T0 = 1_790_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Systemd:
    """Stands in for systemd-run and systemctl. It keeps what it is asked, and every unit it starts
    is active until a test says otherwise. A unit it has never seen is not loaded."""

    def __init__(self):
        self.calls = []
        self.states = {}        # unit name (with its suffix) -> active | inactive | failed
        self.refuse = None      # systemd-run fails, saying this
        self.raises = None      # every call raises this
        self.stuck = set()      # units that will not stop

    @staticmethod
    def _name(n):
        return n if "." in n else n + ".service"

    def __call__(self, argv, **kw):
        assert kw.get("check") is False and kw.get("timeout")   # nothing raises for a status, nothing hangs
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        if argv[0] == "systemd-run":
            if self.refuse:
                return subprocess.CompletedProcess(argv, 1, "", self.refuse)
            unit = next(a for a in argv if a.startswith("--unit=")).split("=", 1)[1]
            timer = any(a.startswith("--on-active=") for a in argv)
            self.states[self._name(unit) if not timer else unit + ".timer"] = "active"
            return subprocess.CompletedProcess(argv, 0, "", f"Running as unit: {unit}.service\n")
        verb, names = argv[2], [self._name(n) for n in argv[3:]]
        if verb == "is-active":
            out = [self.states.get(n, "inactive") for n in names]
            return subprocess.CompletedProcess(argv, 0 if "active" in out else 3, "\n".join(out) + "\n", "")
        if verb == "stop":
            if any(n in self.stuck for n in names):
                return subprocess.CompletedProcess(argv, 1, "", "Failed to stop unit: Access denied.\n")
            missing = [n for n in names if n not in self.states]
            for n in names:
                if n in self.states:
                    self.states[n] = "inactive"
            return subprocess.CompletedProcess(argv, 5 if missing else 0, "", "Unit not loaded.\n")
        return subprocess.CompletedProcess(argv, 0, "", "")   # reset-failed

    def of(self, verb):
        return [c for c in self.calls if c[0] == "systemctl" and c[2] == verb]


def make(tmp_path, *, sd=None, clock=None, directory=None):
    sd, clock = sd or Systemd(), clock or Clock()
    return Jobs(directory or tmp_path / "jobs", sd, clock), sd, clock


def begin(j, clock, title, command="x", **kw):
    """Start a job, and let a second go by so the table's order is the order they began in."""
    rec = j.start(title, command, **kw)
    clock.t += 1
    return rec


def finish(j, job_id, code=0, output=None):
    """What a job's wrapper leaves when its command ends."""
    if output is not None:
        with j._file(job_id, "log").open("a") as f:
            f.write(output)
    j._file(job_id, "exit").write_text(f"{code}\n")


def log(j, job_id, text):
    j._file(job_id, "log").parent.mkdir(parents=True, exist_ok=True)
    with j._file(job_id, "log").open("a") as f:
        f.write(text)


def row(j, job_id):
    return next(r for r in j.snapshot()["jobs"] if r["id"] == job_id)


# -- starting --

def test_a_job_starts_as_a_user_unit_whose_wrapper_keeps_its_output_and_its_exit_status(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Ubuntu 26.04 ISO", "curl -O https://example.org/ubuntu.iso")
    assert re.fullmatch(r"[0-9a-f]{6}", rec["id"])
    job_id = rec["id"]
    out = shlex.quote(str(tmp_path / "jobs" / f"{job_id}.log"))
    exit_file = shlex.quote(str(tmp_path / "jobs" / f"{job_id}.exit"))
    assert sd.calls == [[
        "systemd-run", "--user", f"--unit=bombadil-job-{job_id}", "--description=Ubuntu 26.04 ISO", "/bin/sh", "-c",
        (f"/bin/sh -c {shlex.quote('curl -O https://example.org/ubuntu.iso')} >> {out} 2>&1; "
         f"s=$$?; echo $$s > {exit_file}; exit $$s")]]
    assert rec == {"id": job_id, "title": "Ubuntu 26.04 ISO", "kind": "job",
                   "command": "curl -O https://example.org/ubuntu.iso", "started": T0, "deadline": None,
                   "ended": None, "state": "running", "exit": None, "pct": None, "last": "", "dismissed": False}
    assert json.loads((tmp_path / "jobs" / f"{job_id}.json").read_text()) == rec
    assert row(j, job_id) == {"id": job_id, "title": "Ubuntu 26.04 ISO", "kind": "job", "state": "running",
                              "started": T0, "deadline": None, "ended": None, "pct": None, "last": "",
                              "unit": f"bombadil-job-{job_id}"}
    assert j.snapshot()["type"] == "jobs" and j.running()


def test_a_watcher_is_a_job_of_its_own_kind(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("the build", "tail --pid=1234 -f /dev/null", kind="watch")
    assert rec["kind"] == "watch" and sd.calls[0][2] == f"--unit=bombadil-job-{rec['id']}"
    assert row(j, rec["id"])["kind"] == "watch"


def test_a_timer_is_a_timer_unit_that_says_it_is_up(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Timer, 10 min", "", seconds=600)
    job_id = rec["id"]
    exit_file = shlex.quote(str(tmp_path / "jobs" / f"{job_id}.exit"))
    assert sd.calls == [["systemd-run", "--user", f"--unit=bombadil-timer-{job_id}", "--on-active=600s",
                         "--timer-property=AccuracySec=1s", "/bin/sh", "-c", f"echo 0 > {exit_file}"]]
    assert (rec["kind"], rec["deadline"], rec["state"]) == ("timer", T0 + 600, "running")
    assert row(j, job_id)["unit"] == f"bombadil-timer-{job_id}"
    # No title: it is named by its length. A length that is not whole rounds up.
    assert j.start("", "", seconds=90)["title"] == "Timer, 2 min"
    assert j.start(None, None, kind="job", seconds=2.2)["title"] == "Timer, 3 s"
    assert sd.calls[-1][3] == "--on-active=3s"


def test_a_timer_with_a_command_runs_it_when_the_time_is_up(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Stretch", "notify-send 'Stretch'", seconds=1500)
    job_id = rec["id"]
    argv = sd.calls[0]
    assert argv[:5] == ["systemd-run", "--user", f"--unit=bombadil-timer-{job_id}", "--on-active=1500s",
                        "--timer-property=AccuracySec=1s"]
    assert argv[5:7] == ["/bin/sh", "-c"] and argv[7].startswith("/bin/sh -c 'notify-send '\"'\"'Stretch'\"'\"''")
    assert f">> {tmp_path}/jobs/{job_id}.log 2>&1; s=$$?; echo $$s > " in argv[7]


def test_a_folder_to_start_in_is_passed_on(tmp_path):
    j, sd, _ = make(tmp_path)
    j.start("Build", "make", cwd=str(tmp_path))
    j.start("Soon", "make", seconds=5, cwd=str(tmp_path))
    assert [a for c in sd.calls for a in c if a.startswith("--working-directory")] == [
        f"--working-directory={tmp_path}"] * 2
    with pytest.raises(JobError, match="is not a folder"):
        j.start("Build", "make", cwd=str(tmp_path / "nope"))
    with pytest.raises(JobError, match="is not a folder"):
        j.start("Build", "make", cwd="relative/dir")
    assert len(sd.calls) == 2


def test_the_wrapper_really_keeps_the_output_and_the_status(tmp_path):
    # What systemd hands the shell is the argument with each $$ turned into $; run that, for real.
    j, sd, _ = make(tmp_path)
    rec = j.start("Noisy", "echo \"home is $HOME\"; printf '50%%\\n' >&2; echo 'it'\"'\"'s'; exit 3")
    wrapper = sd.calls[0][-1].replace("$$", "$")
    for _ in range(2):
        r = subprocess.run(["/bin/sh", "-c", wrapper], env={"HOME": "/h", "PATH": "/usr/bin:/bin"},
                           capture_output=True, text=True, check=False)
        assert r.returncode == 3 and r.stdout == "" and r.stderr == ""   # all of it goes to the log
    assert j._file(rec["id"], "log").read_text() == "home is /h\n50%\nit's\n" * 2   # appended, not replaced
    assert j._file(rec["id"], "exit").read_text() == "3\n"
    # An exit in the command, a syntax error or a missing program cannot skip the status.
    for command, code in (("exit 0", 0), ("if then", 2), ("no-such-program-anywhere", 127)):
        rec = j.start("Odd", command)
        r = subprocess.run(["/bin/sh", "-c", sd.calls[-1][-1].replace("$$", "$")], capture_output=True,
                           text=True, check=False)
        assert r.returncode == code and j._file(rec["id"], "exit").read_text() == f"{code}\n"
    timer = j.start("Soon", "", seconds=5)
    assert subprocess.run(["/bin/sh", "-c", sd.calls[-1][-1]], check=False).returncode == 0
    assert j._file(timer["id"], "exit").read_text().strip() == "0"


def test_a_title_is_never_part_of_a_unit_name_or_a_command_line(tmp_path):
    j, sd, _ = make(tmp_path)
    evil = "x; rm -rf / $(touch pwned) `id` \n\t\x1b[31m\x00 — ü"
    rec = j.start(evil, "true")
    argv = sd.calls[0]
    assert rec["title"] == "x; rm -rf / $(touch pwned) `id` [31m — ü"
    assert argv[2] == f"--unit=bombadil-job-{rec['id']}" and re.fullmatch(r"--unit=bombadil-job-[0-9a-f]{6}", argv[2])
    assert argv[3] == f"--description={rec['title']}"     # one argument: nothing reads it as shell
    assert "rm -rf" not in argv[-1] and "pwned" not in argv[-1]
    assert len(j.start("t" * 300, "true")["title"]) == jobs.MAX_TITLE


def test_what_cannot_be_started_is_said_plainly_and_runs_nothing(tmp_path):
    j, sd, _ = make(tmp_path)
    for args, words in [
        ({"title": "", "command": "true"}, "Give the job a short name"),
        ({"title": "  ", "command": "true"}, "Give the job a short name"),
        ({"title": "Build", "command": ""}, "command is empty"),
        ({"title": "Build", "command": None}, "command is empty"),
        ({"title": "Build", "command": "true", "kind": "daemon"}, "no kind of job called 'daemon'"),
        ({"title": "Build", "command": "true", "kind": "timer"}, "needs a number of seconds"),
        ({"title": "T", "command": "", "seconds": 0}, "between 1 second and 7 days"),
        ({"title": "T", "command": "", "seconds": -5}, "between 1 second and 7 days"),
        ({"title": "T", "command": "", "seconds": 10 ** 9}, "between 1 second and 7 days"),
        ({"title": "T", "command": "", "seconds": "soon"}, "needs a number of seconds"),
        ({"title": "T", "command": "", "seconds": True}, "needs a number of seconds"),
        ({"title": "T", "command": "", "seconds": float("nan")}, "needs a number of seconds"),
        ({"title": "Build", "command": "true\x00rm"}, "NUL byte"),
        ({"title": "Build", "command": "x" * 20_001}, "too long"),
    ]:
        with pytest.raises(JobError, match=words):
            j.start(**args)
    assert sd.calls == []
    assert not (tmp_path / "jobs").exists() or list((tmp_path / "jobs").iterdir()) == []
    assert j.snapshot() == {"type": "jobs", "jobs": []}
    assert j.start("T", "", seconds="90")["deadline"] == T0 + 90   # a number in a string is a number


def test_a_systemd_that_refuses_leaves_nothing_behind(tmp_path):
    j, sd, _ = make(tmp_path)
    sd.refuse = "Failed to connect to bus: No medium found\n"
    with pytest.raises(JobError) as e:
        j.start("Build", "make")
    assert str(e.value) == "Could not start Build: Failed to connect to bus: No medium found."
    assert list((tmp_path / "jobs").iterdir()) == [] and j.snapshot()["jobs"] == [] and not j.running()
    sd.refuse, sd.raises = None, FileNotFoundError(2, "No such file", "systemd-run")
    with pytest.raises(JobError, match=r"^Could not start Build: systemd is not available here\.$"):
        j.start("Build", "make")
    sd.raises = subprocess.TimeoutExpired("systemd-run", 15)
    with pytest.raises(JobError, match="systemd did not answer in time"):
        j.start("Build", "make")
    assert list((tmp_path / "jobs").iterdir()) == []


def test_a_jobs_folder_that_cannot_be_written_is_said(tmp_path):
    (tmp_path / "jobs").write_text("a file, not a folder")
    j, sd, _ = make(tmp_path)
    with pytest.raises(JobError, match="cannot be written"):
        j.start("Build", "make")
    assert sd.calls == []


def test_a_runaway_cannot_start_more_than_a_handful_of_jobs(tmp_path):
    j, sd, _ = make(tmp_path)
    for i in range(jobs.MAX_RUNNING):
        j.start(f"Job {i}", "sleep 100")
    with pytest.raises(JobError, match="20 jobs are already running"):
        j.start("One more", "sleep 100")
    assert len(sd.calls) == jobs.MAX_RUNNING
    ids = {r["id"] for r in j.snapshot()["jobs"]}
    assert len(ids) == jobs.MAX_RUNNING   # ids never repeat


# -- looking --

def test_a_finished_job_is_done_once_and_poll_says_so_once(tmp_path):
    j, sd, clock = make(tmp_path)
    rec = j.start("Ubuntu 26.04 ISO", "curl -O x")
    log(j, rec["id"], "  % Total\n 97% 12 MB/s\n")
    assert j.poll() == [] and row(j, rec["id"])["pct"] == 97.0
    clock.t += 180
    finish(j, rec["id"], 0, "100% 12 MB/s\n")
    (done,) = j.poll()
    assert (done["id"], done["state"], done["exit"], done["ended"]) == (rec["id"], "done", 0, T0 + 180)
    assert row(j, rec["id"])["state"] == "done" and row(j, rec["id"])["pct"] == 100.0
    assert row(j, rec["id"])["last"] == "100% 12 MB/s"
    assert j.poll() == [] and not j.running()
    assert len(sd.calls) == 2   # systemd-run, and the one is-active while it ran: a status is answer enough
    assert json.loads(j._file(rec["id"], "json").read_text())["state"] == "done"


def test_a_job_that_says_no_progress_has_none_and_a_done_one_does_not_invent_it(tmp_path):
    j, _, _ = make(tmp_path)
    rec = j.start("Build", "make")
    log(j, rec["id"], "compiling a.c\ncompiling b.c\n")
    j.poll()
    assert row(j, rec["id"])["pct"] is None and row(j, rec["id"])["last"] == "compiling b.c"
    finish(j, rec["id"], 0)
    j.poll()
    assert row(j, rec["id"])["pct"] is None


def test_a_failed_job_keeps_its_last_line(tmp_path):
    j, _, clock = make(tmp_path)
    a, b = j.start("Install", "sudo pacman -S x"), j.start("Fetch", "false")
    finish(j, a["id"], 1, "resolving…\npacman: could not resolve host\n\n")
    finish(j, b["id"], 2)
    failed = {r["id"]: r for r in j.poll()}
    assert failed[a["id"]]["state"] == "failed" and failed[a["id"]]["exit"] == 1
    assert row(j, a["id"])["last"] == "pacman: could not resolve host"
    assert row(j, b["id"])["last"] == "exit status 2"
    assert row(j, b["id"])["ended"] == clock.t


@pytest.mark.parametrize("output, pct, last", [
    ("", None, ""),
    ("a\n\n\n", None, "a"),
    ("  12%\n 43%\n", 43.0, "43%"),
    ("12.5% of 30 MB\n", 12.5, "12.5% of 30 MB"),
    ("100% 0%\n", 0.0, "100% 0%"),
    ("cpu at 250%\n", 100.0, "cpu at 250%"),
    ("1 / 3\n", None, "1 / 3"),
    ("progress 41%\r 42%\r 43% 12 MB/s\r", 43.0, "43% 12 MB/s"),       # a bar redraws with \r
    ("\x1b[2K\x1b[32m 57%\x1b[0m done\n", 57.0, "57% done"),
    ("rate 5 % of quota\n", 5.0, "rate 5 % of quota"),
    ("a\n" + "x" * 300 + "\n", None, "x" * 119 + "…"),
    ("ünïcode 9% — ok\n", 9.0, "ünïcode 9% — ok"),
])
def test_what_the_output_says_about_progress_and_its_last_line(output, pct, last):
    assert jobs.reading(output) == (pct, last)
    assert len(jobs.reading(output)[1]) <= jobs.MAX_LAST


def test_only_the_end_of_a_long_log_counts(tmp_path):
    j, _, _ = make(tmp_path)
    rec = j.start("Big", "run")
    log(j, rec["id"], "99%\n" + "x" * 5000 + "\n")
    j.poll()
    assert row(j, rec["id"])["pct"] is None       # the 99% is further back than the last 4 KB
    log(j, rec["id"], "\n 7%\n")
    j.poll()
    assert row(j, rec["id"])["pct"] == 7.0 and row(j, rec["id"])["last"] == "7%"
    # A log cut in the middle of a character, or with bytes that are not text, still reads.
    j._file(rec["id"], "log").write_bytes(b"\xff\xfe stuff \xe2\x82 30%")
    j.poll()
    assert row(j, rec["id"])["pct"] == 30.0


def test_progress_is_kept_on_disk_for_the_next_agentd(tmp_path):
    j, _, _ = make(tmp_path)
    rec = j.start("ISO", "curl")
    log(j, rec["id"], "43%\n")
    j.poll()
    saved = json.loads(j._file(rec["id"], "json").read_text())
    assert (saved["pct"], saved["last"]) == (43.0, "43%")


def test_the_unit_is_asked_about_only_when_there_is_no_exit_status(tmp_path):
    j, sd, _ = make(tmp_path)
    job, timer = j.start("Build", "make"), j.start("Soon", "", seconds=60)
    j.poll()
    assert sd.of("is-active") == [["systemctl", "--user", "is-active", f"bombadil-job-{job['id']}"],
                                  ["systemctl", "--user", "is-active", f"bombadil-timer-{timer['id']}.timer",
                                   f"bombadil-timer-{timer['id']}.service"]]
    finish(j, job["id"], 0)
    sd.calls.clear()
    j.poll()
    assert [c[-1] for c in sd.of("is-active")] == [f"bombadil-timer-{timer['id']}.service"]   # only the timer


def test_a_timer_ends_when_it_writes_its_status(tmp_path):
    j, _, clock = make(tmp_path)
    timer = j.start("Timer, 10 min", "", seconds=600)
    clock.t += 601
    assert j.poll() == []
    finish(j, timer["id"], 0)
    (done,) = j.poll()
    assert done["state"] == "done" and row(j, timer["id"])["deadline"] == T0 + 600
    assert jobs.ending(done) == ("Timer, 10 min is up.", True)


def test_a_job_stopped_from_outside_leaves_without_a_word(tmp_path):
    j, sd, _ = make(tmp_path)
    a, b = j.start("Stopped by hand", "sleep 100"), j.start("Still going", "sleep 100")
    sd.states[f"bombadil-job-{a['id']}.service"] = "inactive"    # systemctl --user stop, or a reboot
    assert j.poll() == []
    assert [r["id"] for r in j.snapshot()["jobs"]] == [b["id"]]
    assert sorted(p.name for p in (tmp_path / "jobs").iterdir()) == [f"{b['id']}.json"]


def test_a_unit_that_died_without_a_status_failed(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Killed", "sleep 100")
    log(j, rec["id"], "working…\n")
    sd.states[f"bombadil-job-{rec['id']}.service"] = "failed"    # the out-of-memory killer, a signal
    (failed,) = j.poll()
    assert failed["state"] == "failed" and failed["exit"] is None
    assert row(j, rec["id"])["last"] == "working…"
    quiet = j.start("Quiet", "sleep 100")
    sd.states[f"bombadil-job-{quiet['id']}.service"] = "failed"
    j.poll()
    assert row(j, quiet["id"])["last"] == "it stopped before it finished"


def test_a_status_written_as_the_unit_ended_is_not_a_stop(tmp_path):
    # The unit is gone by the time it is asked, but it wrote its status in between.
    j, sd, _ = make(tmp_path)
    rec = j.start("Quick", "true")

    def ask(argv, **kw):
        if argv[2:4] == ["is-active", f"bombadil-job-{rec['id']}"]:
            finish(j, rec["id"], 0)
            return subprocess.CompletedProcess(argv, 3, "inactive\n", "")
        return sd(argv, **kw)
    j._run = ask
    assert [r["state"] for r in j.poll()] == ["done"]


def test_a_systemd_that_cannot_be_asked_never_costs_a_job(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Build", "make")
    sd.raises = OSError("no session bus")
    assert j.poll() == [] and row(j, rec["id"])["state"] == "running"
    sd.raises = None
    silent = subprocess.CompletedProcess([], 1, "", "Failed to connect to bus")
    j._run = lambda argv, **kw: silent
    assert j.poll() == [] and row(j, rec["id"])["state"] == "running"


# -- stopping and dismissing --

def test_stop_stops_the_unit_and_drops_the_job_with_its_output(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Ubuntu ISO", "curl")
    log(j, rec["id"], "43%\n")
    stopped = j.stop(rec["id"])
    assert stopped["title"] == "Ubuntu ISO"
    assert sd.of("stop") == [["systemctl", "--user", "stop", f"bombadil-job-{rec['id']}"]]
    assert j.snapshot()["jobs"] == [] and not j.running() and list((tmp_path / "jobs").iterdir()) == []
    assert j.poll() == [] and j.stop(rec["id"]) is None


def test_stop_on_a_timer_stops_its_timer_unit_too(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Timer, 10 min", "", seconds=600)
    assert j.stop(rec["id"])["kind"] == "timer"
    unit = f"bombadil-timer-{rec['id']}"
    assert sd.of("stop") == [["systemctl", "--user", "stop", unit, f"{unit}.timer"]]
    assert sd.states[f"{unit}.timer"] == "inactive" and j.snapshot()["jobs"] == []


def test_a_unit_that_will_not_stop_keeps_its_row(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Stubborn", "sleep 100")
    sd.stuck.add(f"bombadil-job-{rec['id']}.service")
    with pytest.raises(JobError, match=r"^Could not stop Stubborn: Failed to stop unit: Access denied\.$"):
        j.stop(rec["id"])
    assert row(j, rec["id"])["state"] == "running" and j._file(rec["id"], "json").exists()
    sd.raises = OSError("no session bus")
    with pytest.raises(JobError, match="Could not stop Stubborn"):
        j.stop(rec["id"])
    assert j.running()


def test_stopping_a_job_that_just_ended_is_fine(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Quick", "true")
    sd.states.clear()     # the unit is gone: systemctl says not loaded, and the job is over anyway
    assert j.stop(rec["id"])["id"] == rec["id"] and j.snapshot()["jobs"] == []


def test_stop_on_a_finished_row_just_drops_it(tmp_path):
    j, sd, _ = make(tmp_path)
    rec = j.start("Quick", "true")
    finish(j, rec["id"], 1, "boom\n")
    j.poll()
    assert j.stop(rec["id"])["state"] == "failed"
    assert sd.of("stop") == [] and j.snapshot()["jobs"] == []


def test_dismiss_drops_a_finished_row_and_nothing_else(tmp_path):
    j, sd, clock = make(tmp_path)
    running, done, failed = (begin(j, clock, "Going"), begin(j, clock, "Done"), begin(j, clock, "Failed"))
    finish(j, done["id"], 0)
    finish(j, failed["id"], 1, "boom\n")
    j.poll()
    assert j.dismiss(running["id"]) is None                  # a running job is stopped, not dismissed
    assert j.dismiss(failed["id"])["id"] == failed["id"]
    assert [r["id"] for r in j.snapshot()["jobs"]] == [running["id"], done["id"]]
    assert sd.of("reset-failed") == [["systemctl", "--user", "reset-failed", f"bombadil-job-{failed['id']}"]]
    assert j.dismiss(done["id"]) and len(sd.of("reset-failed")) == 1     # a done one has nothing to reset
    assert [r["id"] for r in j.snapshot()["jobs"]] == [running["id"]]
    assert j._file(failed["id"], "log").exists()             # its output stays for a day
    assert j.dismiss("0000") is None and j.dismiss(failed["id"]) is not None   # again: still fine
    # The row stays gone across a restart.
    again, _, _ = make(tmp_path, sd=sd, clock=clock)
    assert [r["id"] for r in again.snapshot()["jobs"]] == [running["id"]]


# -- how long a row stays --

def test_a_done_row_stays_15_seconds_and_a_failed_one_until_dismissed_or_30_minutes(tmp_path):
    j, _, clock = make(tmp_path)
    ok, bad, going = begin(j, clock, "Ok"), begin(j, clock, "Bad"), begin(j, clock, "Going")
    finish(j, ok["id"], 0)
    finish(j, bad["id"], 3, "nope\n")
    j.poll()
    ids = lambda: [r["id"] for r in j.snapshot()["jobs"]]
    assert ids() == [ok["id"], bad["id"], going["id"]]
    clock.t += 14.9
    assert ids() == [ok["id"], bad["id"], going["id"]]
    clock.t += 0.2
    assert ids() == [bad["id"], going["id"]]
    clock.t += 29 * 60
    assert ids() == [bad["id"], going["id"]]
    clock.t += 60
    assert ids() == [going["id"]]
    assert j.listing() == f"{going['id']} · Going · running 30 min"
    assert j._file(ok["id"], "json").exists()           # the records stay for a day


def test_records_older_than_a_day_are_deleted_and_running_jobs_are_not(tmp_path):
    j, sd, clock = make(tmp_path)
    ok, bad, going = begin(j, clock, "Ok"), begin(j, clock, "Bad"), begin(j, clock, "Going")
    finish(j, ok["id"], 0)
    finish(j, bad["id"], 1, "boom\n")
    j.poll()
    clock.t += 24 * 3600 - 1
    j.poll()
    assert j._file(ok["id"], "json").exists() and j._file(ok["id"], "exit").exists()
    clock.t += 2
    j.poll()
    assert sorted(p.name for p in (tmp_path / "jobs").iterdir()) == [f"{going['id']}.json"]
    assert sd.of("reset-failed") == [["systemctl", "--user", "reset-failed", f"bombadil-job-{bad['id']}"]]
    assert j.running() and row(j, going["id"])["state"] == "running"   # a day old, still counting
    # When its unit goes, it leaves like any other.
    sd.states[f"bombadil-job-{going['id']}.service"] = "inactive"
    j.poll()
    assert list((tmp_path / "jobs").iterdir()) == []


def test_the_table_is_in_the_order_the_jobs_began(tmp_path):
    j, _, clock = make(tmp_path)
    first = j.start("First", "x")
    clock.t += 5
    second = j.start("Second", "x")
    clock.t += 5
    third = j.start("Third", "x")
    finish(j, first["id"], 0)
    j.poll()
    assert [r["title"] for r in j.snapshot()["jobs"]] == ["First", "Second", "Third"]
    assert [r["id"] for r in j.snapshot()["jobs"]] == [first["id"], second["id"], third["id"]]


# -- a restarted agentd --

def test_a_restarted_agentd_counts_what_is_still_running(tmp_path):
    j, sd, clock = make(tmp_path)
    going = j.start("Going", "curl")
    timer = j.start("Timer, 5 min", "", seconds=300)
    log(j, going["id"], "61% 9 MB/s\n")
    j.poll()
    before = j.snapshot()
    again, _, _ = make(tmp_path, sd=sd, clock=clock)
    assert again.snapshot() == before and again.running()
    assert again.poll() == []
    # What ended while it was away is found, once.
    finish(again, going["id"], 0, "100% 9 MB/s\n")
    assert [r["id"] for r in again.poll()] == [going["id"]]
    third, _, _ = make(tmp_path, sd=sd, clock=clock)
    assert third.poll() == [] and row(third, going["id"])["state"] == "done"
    assert row(third, timer["id"])["deadline"] == T0 + 300
    # A job whose unit is gone (a reboot) is dropped at the first look.
    sd.states.clear()
    assert third.poll() == [] and third.snapshot()["jobs"] == [row(third, going["id"])]


def test_a_folder_of_strangers_is_not_a_table(tmp_path):
    d = tmp_path / "jobs"
    d.mkdir()
    good = {"id": "abcd12", "title": "Good", "kind": "job", "state": "running", "started": T0, "command": "x"}
    (d / "abcd12.json").write_text(json.dumps(good))
    (d / "evil.json").write_text(json.dumps({**good, "id": "evil"}))                    # not an id
    (d / "abcd13.json").write_text(json.dumps({**good, "id": "abcd12"}))                # a name that lies
    (d / "abcd14.json").write_text("{not json")
    (d / "abcd15.json").write_text(json.dumps({**good, "id": "abcd15", "kind": "daemon"}))
    (d / "abcd16.json").write_text(json.dumps({**good, "id": "abcd16", "state": "lost"}))
    (d / "abcd17.json").write_text(json.dumps({**good, "id": "abcd17", "started": "yesterday"}))
    (d / "abcd18.json").write_text("[1, 2]")
    (d / "abcd19.json").write_bytes(b"\xff\xfe")
    (d / "ABCD20.json").write_text(json.dumps({**good, "id": "ABCD20"}))
    (d / "abcd21.json").mkdir()
    (d / "notes.txt").write_text("hello")
    j, _, _ = make(tmp_path)
    assert [r["id"] for r in j.snapshot()["jobs"]] == ["abcd12"]
    # Odd values in a good record are made safe, never trusted.
    (d / "abcd22.json").write_text(json.dumps({**good, "id": "abcd22", "title": "T\n" * 100, "pct": "x",
                                               "exit": True, "last": 5, "deadline": float("inf"),
                                               "unit": "../../etc/passwd"}))
    k, _, _ = make(tmp_path)
    odd = row(k, "abcd22")
    assert odd["pct"] is None and odd["last"] == "5" and odd["deadline"] is None
    assert len(odd["title"]) <= jobs.MAX_TITLE and odd["unit"] == "bombadil-job-abcd22"
    # Every timer has a deadline and nothing else does, whatever a file says.
    (d / "abcd23.json").write_text(json.dumps({**good, "id": "abcd23", "kind": "timer", "deadline": None}))
    (d / "abcd24.json").write_text(json.dumps({**good, "id": "abcd24", "deadline": T0 + 5}))
    k, _, _ = make(tmp_path)
    assert row(k, "abcd23")["deadline"] == T0 and row(k, "abcd24")["deadline"] is None
    assert k.listing().count("left") == 1


# -- ids --

BAD_IDS = ["", "../../etc/passwd", "../abcd12", "ABCD12", "abcd12\n", " abcd12", "abcd12;ls", "xyz", "abc",
           "a" * 13, "abcd1", "abcd12.json", "abcd12/../abcd12", None, 5, ["abcd12"], {"id": "abcd12"}, True]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_an_id_that_is_not_one_of_ours_reaches_no_path_and_no_unit(tmp_path, bad):
    j, sd, _ = make(tmp_path)
    rec = j.start("Real", "sleep 100")
    calls = len(sd.calls)
    assert j.stop(bad) is None and j.dismiss(bad) is None and j.why(bad) is None and j.log_path(bad) is None
    assert len(sd.calls) == calls and row(j, rec["id"])["state"] == "running"
    assert jobs.title_of(bad) == ""


def test_an_id_with_a_newline_is_not_the_id_before_it(tmp_path):
    j, _, _ = make(tmp_path)
    rec = j.start("Real", "sleep 100")
    assert j.stop(rec["id"] + "\n") is None and j.log_path(rec["id"] + "\n") is None and j.running()
    assert j.log_path(rec["id"]) == tmp_path / "jobs" / f"{rec['id']}.log"


def test_the_ids_it_makes_fit_the_rule(tmp_path):
    j, _, _ = make(tmp_path)
    for i in range(jobs.MAX_RUNNING):
        assert jobs.ID_RE.fullmatch(j.start(f"J{i}", "x")["id"])
    with pytest.raises(ValueError):
        j._file("../x", "log")


# -- words --

@pytest.mark.parametrize("seconds, said", [
    (0, "0 s"), (0.4, "0 s"), (40, "40 s"), (59.4, "59 s"), (59.6, "1 min"), (60, "1 min"), (90, "2 min"),
    (180, "3 min"), (599, "10 min"), (3599, "1 h"), (3600, "1 h"), (3900, "1 h 5 min"), (7260, "2 h 1 min"),
    (-5, "0 s"),
])
def test_how_long_the_desk_says(seconds, said):
    assert jobs.span(seconds) == said


def _rec(state, kind="job", title="Ubuntu 26.04 ISO", last="", took=180):
    return {"id": "abcd12", "title": title, "kind": kind, "state": state, "started": T0, "ended": T0 + took,
            "last": last, "deadline": None}


@pytest.mark.parametrize("rec, text, ok", [
    (_rec("done"), "Ubuntu 26.04 ISO is done in 3 min.", True),
    (_rec("done", title="the build", took=40), "The build is done in 40 s.", True),
    (_rec("done", "watch", "the build", took=400), "The build is done in 7 min.", True),
    (_rec("done", "timer", "Timer, 10 min", took=600), "Timer, 10 min is up.", True),
    (_rec("done", "timer", "Tea"), "Tea is up.", True),
    (_rec("failed", title="Build", last="pacman: could not resolve host"),
     "Build failed: pacman: could not resolve host", False),
    (_rec("failed", title="build"), "Build failed.", False),
    (_rec("failed", "timer", "Stretch", "notify-send: no bus"), "Stretch failed: notify-send: no bus", False),
])
def test_the_line_a_job_ends_with(rec, text, ok):
    assert jobs.ending(rec) == (text, ok)


def test_what_the_agent_is_told_when_it_starts_one(tmp_path):
    j, _, _ = make(tmp_path)
    job, timer = j.start("Ubuntu ISO", "curl"), j.start("Timer, 10 min", "", seconds=600)
    said = jobs.started_text(job, j.log_path(job["id"]))
    assert said == (f"Started Ubuntu ISO as job {job['id']}. The desk counts it and says so when it ends; "
                    f"its output goes to {tmp_path}/jobs/{job['id']}.log. Stop it with op stop and id {job['id']}.")
    assert jobs.started_text(timer, None) == (f"Timer set for 10 min as job {timer['id']}. The desk counts it "
                                              "down and says so when it is up.")
    tea = j.start("Tea", "", seconds=300)
    assert jobs.started_text(tea, None).startswith("Timer set for 5 min (Tea) as job ")


def test_what_list_says(tmp_path):
    j, _, clock = make(tmp_path)
    assert j.listing() == "Nothing is running in the background."
    iso, timer, bad = (begin(j, clock, "Ubuntu ISO", "curl"), begin(j, clock, "Tea", "", seconds=300),
                       begin(j, clock, "Build", "make"))
    log(j, iso["id"], "43% 12 MB/s\n")
    finish(j, bad["id"], 2, "make: *** [all] Error 2\n")
    clock.t += 125
    j.poll()
    assert j.listing().splitlines() == [
        f"{iso['id']} · Ubuntu ISO · running 2 min · 43% · 43% 12 MB/s",
        f"{timer['id']} · Tea · 3 min left",
        f"{bad['id']} · Build · failed · make: *** [all] Error 2"]


def test_the_title_the_line_uses_for_a_job_by_its_id(home):
    j = Jobs(runner=Systemd(), clock=Clock())
    rec = j.start("Ubuntu ISO", "curl")
    assert jobs.title_of(rec["id"]) == "Ubuntu ISO"
    assert jobs.title_of("ffffff") == "" and jobs.title_of(None) == ""


def test_the_output_in_the_drawer(tmp_path):
    j, _, _ = make(tmp_path)
    rec = j.start("Odd %s $HOME `x`", "make")
    log(j, rec["id"], "".join(f"line {i}\n" for i in range(500)))
    argv = j.why(rec["id"])
    assert argv[:2] == ["bash", "-c"] and argv[3:] == ["bash", str(tmp_path / "jobs" / f"{rec['id']}.log"),
                                                       "Odd %s $HOME `x`"]
    r = subprocess.run(argv, input="\x1b", capture_output=True, text=True, check=False, timeout=10)
    assert r.returncode == 0 and "Odd %s $HOME `x`" in r.stdout and "Press Esc to close." in r.stdout
    assert "line 499" in r.stdout and "line 200" in r.stdout and "line 199" not in r.stdout
    empty = j.start("Quiet", "true")      # no output yet: no log file, still a window with a title
    r = subprocess.run(j.why(empty["id"]), input="\x1b", capture_output=True, text=True, check=False, timeout=10)
    assert r.returncode == 0 and "Quiet" in r.stdout


# -- where it lives, and who may be in it at once --

def test_the_folder_is_the_one_paths_names(home):
    assert paths.jobs_dir() == home / "state" / "jobs"
    j = Jobs(runner=Systemd(), clock=Clock())
    rec = j.start("Build", "make")
    assert (home / "state" / "jobs" / f"{rec['id']}.json").exists()
    assert Jobs(runner=Systemd(), clock=Clock()).snapshot()["jobs"][0]["id"] == rec["id"]


def test_launcher_threads_and_the_loop_can_use_it_at_once(tmp_path):
    j, sd, clock = make(tmp_path)
    errors, started = [], []

    def work(seed):
        rng = random.Random(seed)
        try:
            for _ in range(60):
                known = [r["id"] for r in j.snapshot()["jobs"]] or ["ffffff"]
                do = rng.choice(["start", "start", "poll", "snapshot", "stop", "dismiss", "finish", "list"])
                if do == "start":
                    try:
                        started.append(j.start(f"J{seed}", "x", seconds=rng.choice([None, 30]))["id"])
                    except JobError:
                        pass   # 20 at once is the limit
                elif do == "poll":
                    j.poll()
                elif do == "snapshot":
                    j.snapshot()
                elif do == "list":
                    j.listing()
                elif do == "stop":
                    j.stop(rng.choice(known))
                elif do == "dismiss":
                    j.dismiss(rng.choice(known))
                elif (tmp_path / "jobs").exists():
                    finish(j, rng.choice(known), rng.choice([0, 1]), "50%\n")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and started
    table = j.snapshot()["jobs"]
    assert len({r["id"] for r in table}) == len(table)
    assert Jobs(tmp_path / "jobs", sd, clock).snapshot() == j.snapshot()   # what is on disk is the table

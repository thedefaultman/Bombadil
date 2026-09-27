"""Coding sessions: names, the registry, the hooks' states and the one line in the pill."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bombadil import dev, launcher, paths

ROOT = Path(__file__).resolve().parents[1]


class FakeZellij:
    """Stands in for subprocess.run: records every command; zellij sessions start and end."""

    def __init__(self):
        self.calls = []
        self.running = set()
        self.envs = []

    def __call__(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "git":
            return subprocess.run(argv, **{k: v for k, v in kw.items() if k != "timeout"})
        z = next((i for i, a in enumerate(argv) if a.endswith("zellij")), None)
        out = ""
        if z is not None:
            rest = argv[z + 1:]
            if rest[:1] == ["list-sessions"]:
                out = "\n".join(sorted(self.running))
            elif "--create-background" in rest:
                self.running.add(rest[rest.index("--create-background") + 1])
                self.envs.append(kw.get("env") or {})
            elif rest[:1] == ["kill-session"]:
                self.running.discard(rest[1])
        return subprocess.CompletedProcess(argv, 1 if argv[0] == "pgrep" else 0, out, "")

    def started(self):
        return [c for c in self.calls if "--create-background" in c]


class Spawns(list):
    def __call__(self, argv, **kw):
        self.append(list(argv))


@pytest.fixture
def projects(home, monkeypatch):
    root = home / "Projects"
    for name in ("latchkey", "Bombadil", "notes"):
        (root / name).mkdir(parents=True)
    for name in ("latchkey", "Bombadil"):
        subprocess.run(["git", "init", "-q", "-b", "main", str(root / name)], check=True)
        subprocess.run(["git", "-C", str(root / name), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
                        "--allow-empty", "-m", "first"], check=True)
    monkeypatch.setenv("BOMBADIL_NO_SCOPE", "1")
    monkeypatch.setattr(dev.procs, "_scope_ok", None)
    return root


def _dev(clock=None):
    z, spawns = FakeZellij(), Spawns()
    d = dev.Dev(runner=z, spawn=spawns, clock=clock or time.time)
    d.hypr = type("NoHypr", (), {"available": False})()
    return d, z, spawns


# -- words --

def test_launcher_forms_name_projects_and_sessions(projects):
    d, _, _ = _dev()
    a = launcher.match("claude latchkey", [], d)
    assert (a.kind, a.verb, a.tool, a.target, a.role, a.title) == \
        ("session", "start", "claude", "latchkey", "claude", "claude on Latchkey")
    a = launcher.match("Codex bombadil reviewer", [], d)
    assert (a.kind, a.tool, a.target, a.role, a.title) == ("session", "codex", "Bombadil", "reviewer",
                                                         "reviewer on Bombadil")
    assert launcher.match("shell notes", [], d).tool == "shell"
    a = launcher.match("latchkey", [], d)
    assert (a.kind, a.target) == ("project", "latchkey")
    assert launcher.match("what's running?", [], d).kind == "sessions"
    # Not ours: a project that is not there, a sentence, the tool's name alone.
    for text in ("claude nothere", "claude what is this", "claude", "codex", "end", "reviewer"):
        assert launcher.match(text, [], d) is None, text
    # Without sessions (tests, a client that has none) nothing changes.
    assert launcher.match("claude latchkey", []) is None
    # Core words still win.
    assert launcher.match("undo", [], d).kind == "undo"


def test_a_named_session_comes_back_by_its_name(projects):
    d, _, _ = _dev()
    ok, line = d.open("claude", "bombadil", "reviewer")
    assert ok and line == "Started reviewer on Bombadil.", line
    a = launcher.match("reviewer", [], d)
    assert (a.kind, a.verb, a.target) == ("session", "open", "bombadil/reviewer")
    assert launcher.match("switch to reviewer", [], d).target == "bombadil/reviewer"
    assert launcher.match("reviewer on bombadil", [], d).target == "bombadil/reviewer"
    a = launcher.match("end reviewer", [], d)
    assert (a.kind, a.target) == ("end", "bombadil/reviewer")
    # The same role on two projects: say which.
    d.open("claude", "latchkey", "reviewer")
    a = launcher.match("reviewer", [], d)
    assert a.kind == "choose" and a.title == "reviewer is on Latchkey and Bombadil"
    assert launcher.match("reviewer latchkey", [], d).target == "latchkey/reviewer"
    names = {e["name"]: e for e in launcher.entries([], d)}
    assert names["latchkey"]["kind"] == "project" and "claude latchkey" in names
    assert names["bombadil/reviewer"]["words"] == ["reviewer"]


def test_end_names_sessions_a_role_cannot(projects):
    d, z, _ = _dev()
    run = launcher.Launcher(sessions=d, runner=z, spawn=Spawns())
    # A project with nothing on it; a session that is not running.
    assert run.run(launcher.match("end latchkey", [], d)) == (False, "Nothing runs on Latchkey.")
    assert run.run(launcher.match("end claude latchkey", [], d)) == (False, "Claude on Latchkey is not running.")
    d.open("claude", "latchkey")
    a = launcher.match("end claude latchkey", [], d)
    assert (a.kind, a.target) == ("end", "latchkey/claude")
    # The project's name ends its one session...
    a = launcher.match("end latchkey", [], d)
    assert (a.kind, a.target) == ("end", "latchkey/claude")
    # ...and with two, asks which.
    d.open("codex", "latchkey", "reviewer")
    a = launcher.match("end latchkey", [], d)
    assert a.kind == "choose"
    assert run.run(a) == (False, "Latchkey runs reviewer and claude: say which, as in “end reviewer latchkey”.")
    assert run.run(launcher.match("end codex latchkey reviewer", [], d)) == (True, "Ended reviewer on Latchkey. Its copy is kept.")
    assert run.run(launcher.match("end latchkey", [], d)) == (True, "Ended claude on Latchkey.")
    assert run.run(launcher.match("end claude latchkey", [], d)) == (False, "Claude on Latchkey is not running.")
    # A role on two projects: the choice keeps the verb.
    d.open("claude", "latchkey", "builder")
    d.open("claude", "bombadil", "builder")
    a = launcher.match("end builder", [], d)
    assert run.run(a) == (False, "Builder is on Bombadil and Latchkey: say which, as in “end builder bombadil”.")


# -- starting, bringing back, ending --

def test_first_session_takes_the_checkout_and_the_next_one_a_copy(projects):
    d, z, spawns = _dev()
    ok, line = d.open("claude", "latchkey")
    assert ok and line == "Started claude on Latchkey."
    first = d.sessions["latchkey/claude"]
    assert first.folder == str(projects / "latchkey") and not first.copy
    cmd = z.started()[0]
    assert cmd[-6:] == ["--close-on-exit", "--", dev.bombadil_bin(), "dev", "run", "latchkey/claude"]
    assert cmd[cmd.index("--create-background") + 1] == "bombadil-latchkey-claude"
    env = z.envs[0]
    assert env["BOMBADIL_SESSION"] == "1" and env["BOMBADIL_DEV_ID"] == "latchkey/claude"
    assert "BOMBADIL_OS" not in env
    # The viewer: foot on the zellij session, with an app id the window rules and focus use.
    assert spawns[0][0] == "foot" and "--app-id=bombadil-session-latchkey-claude" in spawns[0]
    assert spawns[0][-2:] == ["attach", "bombadil-latchkey-claude"]

    ok, line = d.open("codex", "latchkey", "api")
    assert line == "Started api on Latchkey in its own copy."
    api = d.sessions["latchkey/api"]
    assert api.copy and api.folder == str(projects / ".work" / "latchkey" / "api")
    assert (Path(api.folder) / ".git").exists()
    branch = subprocess.run(["git", "-C", api.folder, "branch", "--show-current"], capture_output=True, text=True)
    assert branch.stdout.strip() == "bombadil/api"
    # A shell never takes the checkout or a copy.
    d.open("shell", "latchkey")
    assert d.sessions["latchkey/shell"].folder == str(projects / "latchkey")
    # A folder that is no git repository: the second session shares it and the line says so.
    d.open("claude", "notes")
    assert d.open("codex", "notes", "b")[1] == "Started b on Notes in the same folder, which is not a git repository."


def test_typing_the_same_name_brings_it_back_and_never_starts_two(projects):
    d, z, spawns = _dev()
    d.open("claude", "latchkey")
    assert d.open("claude", "latchkey") == (True, "Back to claude on Latchkey.")
    assert len(z.started()) == 1 and len(spawns) == 2   # a second viewer, no second session
    ok, line = d.open("codex", "latchkey", "claude")
    assert not ok and "is a Claude session" in line


def test_end_kills_the_session_and_keeps_its_copy(projects):
    d, z, _ = _dev()
    d.open("claude", "bombadil")
    d.open("claude", "bombadil", "builder")
    ok, line = d.end(d.sessions["bombadil/builder"])
    assert ok and line == "Ended builder on Bombadil. Its copy is kept."
    assert ["kill-session", "bombadil-bombadil-builder"] in [c[-2:] for c in z.calls if c[0].endswith("zellij")]
    assert Path(d.sessions["bombadil/builder"].folder).is_dir()
    assert d.snapshot()["sessions"][-1]["key"] == "bombadil/claude"   # ended ones are not shown
    # Starting it again reuses its copy, with a new conversation.
    old = d.sessions["bombadil/builder"].conversation
    assert d.open("claude", "bombadil", "builder")[1] == "Started builder on Bombadil in its own copy."
    assert d.sessions["bombadil/builder"].conversation != old


def test_after_a_restart_sessions_are_asleep_and_resume_by_id(projects, monkeypatch):
    d, z, _ = _dev()
    d.open("claude", "latchkey")
    conv = d.sessions["latchkey/claude"].conversation
    monkeypatch.setattr(dev, "boot_id", lambda: "another-boot")
    d2, z2, _ = _dev()
    s = d2.sessions["latchkey/claude"]
    assert (s.alive, s.state, s.last) == (False, "asleep", "Stopped by a restart")
    assert d2.snapshot()["sessions"][0]["state"] == "asleep"
    assert d2.open("claude", "latchkey") == (True, "Resumed claude on Latchkey.")
    assert z2.started()[0][-1] == "--resume" and d2.sessions["latchkey/claude"].conversation == conv


def test_agentd_restarting_keeps_running_sessions(projects):
    d, z, _ = _dev()
    d.open("claude", "latchkey")
    d2 = dev.Dev(runner=z, spawn=Spawns())
    d2.hypr = d.hypr
    d2.check()
    assert d2.sessions["latchkey/claude"].alive
    z.running.clear()   # its zellij went away without a word
    d2.sessions["latchkey/claude"].since -= 60
    d2.check()
    s = d2.sessions["latchkey/claude"]
    assert (s.alive, s.state, s.last) == (False, "failed", "Stopped unexpectedly")


def test_a_zellij_that_cannot_be_asked_is_never_taken_for_gone(projects):
    d, z, _ = _dev()
    d.open("claude", "latchkey")
    d.sessions["latchkey/claude"].since -= 60
    d._run = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "zellij: broken")
    d.check()
    assert d.sessions["latchkey/claude"].alive


# -- what the hooks say --

def _sig(d, event, **kw):
    m = {"type": "dev-signal", "tool": "claude", "event": event, "dev_id": "bombadil/reviewer",
         "session_id": "c0ffee", **kw}
    return d.signal(m)


def test_hooks_move_a_session_through_its_states(projects):
    d, _, _ = _dev()
    d.open("claude", "bombadil", "reviewer")
    s = d.sessions["bombadil/reviewer"]
    _sig(d, "SessionStart", session_id="new-id")
    assert s.conversation == "new-id" and s.state == "idle"
    _sig(d, "UserPromptSubmit", text="fix the flaky test")
    assert s.state == "working" and s.lines[-1] == "› fix the flaky test"
    _sig(d, "PreToolUse", tool_name="AskUserQuestion",
         tool_input={"questions": [{"question": "Run the migration on the local database?"}]})
    assert s.state == "asked" and s.last == "Run the migration on the local database?"
    assert d.line() == "reviewer on Bombadil: Run the migration on the local database?"
    _sig(d, "PostToolUse", tool_name="AskUserQuestion")
    assert s.state == "working" and d.line() == ""
    _sig(d, "PermissionRequest", tool_name="Bash", tool_input={"command": "npm test", "description": "Run the tests"})
    assert s.last == "Run the tests?"
    _sig(d, "PostToolUse", tool_name="Bash")
    _sig(d, "Stop", text="Fixed the race in snapper.\n\nDetails follow.")
    assert (s.state, s.unseen) == ("done", True)
    assert d.line() == "reviewer on Bombadil finished: Fixed the race in snapper."
    # Looking at it is what makes it seen.
    d.focused("bombadil-session-bombadil-reviewer")
    assert (s.state, s.unseen) == ("idle", False) and d.line() == ""
    # The session in front never announces itself.
    _sig(d, "UserPromptSubmit", text="and the docs")
    _sig(d, "Stop", text="Done.")
    assert s.state == "idle" and d.line() == ""
    _sig(d, "UserPromptSubmit", text="again")
    _sig(d, "StopFailure", text="API Error: overloaded")
    assert s.state == "failed"
    _sig(d, "UserPromptSubmit", text="retry")
    assert s.state == "working"
    _sig(d, "Notification", notification_type="idle_prompt", text="Claude is waiting for your input")
    assert s.state == "idle"   # Esc has no hook; the idle notice ends the moving dot


def test_several_waiting_merge_into_one_line_and_tab_walks_them(projects):
    t = [1000.0]
    d, z, spawns = _dev(clock=lambda: t[0])
    for role in ("api", "reviewer"):
        d.open("claude", "bombadil", role)
    t[0] += 10
    d.signal({"event": "Stop", "dev_id": "bombadil/api", "text": "Done."})
    t[0] += 10
    d.signal({"event": "PermissionRequest", "dev_id": "bombadil/reviewer", "tool_name": "Bash",
              "tool_input": {"command": "rm -rf build"}})
    assert d.line() == "reviewer and api are waiting"   # a question comes before a finished turn
    assert d.snapshot()["attention"] == ["bombadil/reviewer", "bombadil/api"]
    before = len(spawns)
    assert d.next() == (True, "Back to reviewer on Bombadil.")
    assert d.front == "bombadil/reviewer" and len(spawns) == before + 1
    assert d.line() == "api on Bombadil finished: Done."
    assert d.next() == (True, "Back to api on Bombadil.")
    # reviewer still asks (its dot stays lit), but you have seen it: the walk ends.
    assert d.sessions["bombadil/reviewer"].state == "asked"
    assert d.line() == "" and d.next() == (True, "Nothing needs you.")
    # A new question from it is news again.
    d.signal({"event": "PermissionRequest", "dev_id": "bombadil/reviewer", "tool_name": "Bash",
              "tool_input": {"command": "git push", "description": "Push the branch"}})
    assert d.line() == "reviewer on Bombadil: Push the branch?"


def test_bringing_a_session_back_by_name_is_looking_at_it(projects):
    d, _, _ = _dev()
    d.open("claude", "bombadil", "api")
    d.signal({"event": "Stop", "dev_id": "bombadil/api", "text": "Done."})
    assert d.line() == "api on Bombadil finished: Done."
    # No focus event arrives (another compositor, a missed event): typing its name is enough.
    assert d.open("claude", "bombadil", "api") == (True, "Back to api on Bombadil.")
    s = d.sessions["bombadil/api"]
    assert (s.state, s.unseen, d.front) == ("idle", False, "bombadil/api") and d.line() == ""


def test_a_tool_that_crashes_turns_red_and_resumes_by_its_name(projects):
    d, z, _ = _dev()
    d.open("claude", "bombadil", "builder")
    s = d.sessions["bombadil/builder"]
    d.signal({"event": "exit", "dev_id": "bombadil/builder", "code": 1, "text": "/tmp/last", "run": s.run})
    assert (s.alive, s.state, s.unseen, s.why) == (False, "failed", True, "/tmp/last")
    assert d.line() == "builder on Bombadil stopped: Claude stopped unexpectedly (exit 1)"
    assert d.next()[1].endswith("Type its name to resume it.")
    assert d.line() == ""
    z.running.discard(s.zellij)   # the wrapper ends its zellij session
    assert d.bring(s) == (True, "Resumed builder on Bombadil.")
    # An exit from an earlier run of the same name is not this run's.
    d.signal({"event": "exit", "dev_id": "bombadil/builder", "code": 0, "run": "an-earlier-run"})
    assert s.alive
    d.signal({"event": "exit", "dev_id": "bombadil/builder", "code": 0, "run": s.run})
    assert (s.alive, s.state) == (False, "ended")


def test_sessions_started_by_hand_are_listed_as_yours(projects):
    d, _, _ = _dev()
    me = os.getpid()
    d.signal({"event": "SessionStart", "tool": "claude", "session_id": "abc", "cwd": str(projects / "latchkey" / "src"),
              "pid": me, "pid_start": dev._pid_start(me)})
    s = d.sessions["yours/abc"]
    assert s.yours and s.project == "latchkey" and s.title == "claude on Latchkey"
    d.signal({"event": "UserPromptSubmit", "session_id": "abc", "text": "hi"})
    assert s.state == "working"
    assert launcher.match("claude latchkey", [], d).verb == "start"   # never taken over
    d.signal({"event": "SessionEnd", "session_id": "abc"})
    assert "yours/abc" not in d.sessions
    # One that died without saying goodbye goes with its process.
    d.signal({"event": "SessionStart", "tool": "codex", "session_id": "gone", "cwd": str(projects / ".work" / "Bombadil"
                                                                                       / "kit"), "pid": 999999})
    assert d.sessions["yours/gone"].project == "Bombadil"
    d.check()
    assert "yours/gone" not in d.sessions


def test_the_registry_is_what_the_wrapper_and_the_list_read(projects):
    d, _, _ = _dev()
    d.open("claude", "latchkey")
    data = json.loads(dev.Dev.registry().read_text())
    assert data["sessions"][0]["tool"] == "claude"
    s = dev.Session.from_dict(data["sessions"][0])
    argv = dev.command_for(s, resume=False)
    assert argv[-4:] == ["--session-id", s.conversation, "-n", "claude on Latchkey"]
    assert dev.command_for(s, resume=True)[-4:-2] == ["--resume", s.conversation]
    s.tool, s.conversation = "codex", "t-1"
    assert dev.command_for(s, resume=True)[-4:] == ["codex", "--no-daemon", "resume", "t-1"]
    assert any("claude on Latchkey" in line for line in d.listing())


def test_the_wrapper_reports_how_its_tool_ended(projects, tmp_path, monkeypatch):
    d, _, _ = _dev()
    d.open("shell", "latchkey")
    d.open("claude", "latchkey")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text("#!/bin/sh\nexit 3\n")
    (fake / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    monkeypatch.setenv("SHELL", "/bin/true")
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    which = shutil.which
    monkeypatch.setattr(dev.shutil, "which", lambda name: None if name == "mise" else which(name))
    got = []
    assert dev.run("latchkey/claude", report=got.append) == 3
    assert got[0]["event"] == "exit" and got[0]["code"] == 3 and got[0]["dev_id"] == "latchkey/claude"
    got.clear()
    assert dev.run("latchkey/shell", report=got.append) == 0
    assert got[0]["code"] == 0
    assert dev.run("latchkey/nothing", report=got.append) == 2


# -- bombadil-signal --

def _signal(args, payload, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("BOMBADIL_")}
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-signal"), *args], input=payload, env=e,
                          capture_output=True, text=True, timeout=10)


def test_the_hook_program_spools_when_agentd_is_away_and_skips_the_machine(home):
    env = {"BOMBADIL_STATE": str(home / "state"), "BOMBADIL_RUNTIME": str(home / "run"),
           "BOMBADIL_DEV_ID": "latchkey/claude", "CLAUDE_PID": str(os.getpid())}
    long = "x" * 5000
    payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": "/w", "tool_name": "Bash",
                          "tool_input": {"command": long, "description": "Run it", "content": long}})
    r = _signal(["claude"], payload, env)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    msgs = dev.take_spool()
    assert len(msgs) == 1 and dev.take_spool() == []
    m = msgs[0]
    assert (m["event"], m["dev_id"], m["pid"]) == ("PreToolUse", "latchkey/claude", os.getpid())
    assert m["tool_input"] == {"description": "Run it", "command": "x" * 300}   # trimmed, no file bodies
    # The machine's own agent is not a coding session.
    _signal(["claude"], payload, {**env, "BOMBADIL_OS": "1"})
    assert dev.take_spool() == []
    # Garbage in never fails the tool.
    assert _signal(["claude"], "{not json", env).returncode == 0
    # Codex's notify passes its JSON as an argument.
    note = json.dumps({"type": "agent-turn-complete", "thread-id": "t-9", "cwd": "/w",
                       "last-assistant-message": "All green."})
    _signal(["codex-notify", note], "", env)
    m = dev.take_spool()[0]
    assert (m["tool"], m["event"], m["session_id"], m["text"]) == ("codex", "agent-turn-complete", "t-9", "All green.")


def test_managed_hooks_cover_the_events_the_dots_need():
    claude = json.loads((ROOT / "iso/airootfs/etc/claude-code/managed-settings.d/50-bombadil.json").read_text())
    hooks = claude["hooks"]
    for ev in ("SessionStart", "SessionEnd", "UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop",
               "StopFailure", "Notification"):
        assert hooks[ev][0]["hooks"][0]["command"] == "bombadil-signal claude", ev
        assert hooks[ev][0]["hooks"][0]["async"] is True
    assert hooks["PreToolUse"][0]["matcher"] == "AskUserQuestion"
    import tomllib
    codex = tomllib.loads((ROOT / "iso/airootfs/etc/codex/requirements.toml").read_text())
    for ev in ("SessionStart", "UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop", "Interrupt",
               "SessionEnd"):
        assert codex["hooks"][ev][0]["hooks"][0]["command"] == "bombadil-signal codex", ev
    skel = tomllib.loads((ROOT / "iso/airootfs/etc/skel/.codex/config.toml").read_text())
    assert skel["notify"] == ["bombadil-signal", "codex-notify"]


def test_paths_default_to_the_projects_folder(home):
    assert paths.projects_dir() == home / "Projects"
    assert dev.share_file("zellij/config.kdl").exists()

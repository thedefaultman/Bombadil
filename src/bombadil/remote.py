"""Remote control: a Claude Code session on this computer that a person steers from another device
(claude.ai/code in any browser, or the Claude app), so a computer can be set up, tested and built
on from a phone or a laptop without sitting at it.

Claude Code's own `claude remote-control` does the connecting. It is a program that wants a terminal
(it asks once whether to trust the folder it runs in, and shows its link and a QR code there), so it
runs inside a detached tmux session. That makes it survive a closed terminal, keeps its link where
`bombadil remote show` can look at it, and keeps what it said if it stops.

    bombadil remote [start [FOLDER]]   start it (or say it is already on)
    bombadil remote status | url       is it on, and the link to open
    bombadil remote show               look at it; Ctrl-b then d leaves it running
    bombadil remote stop               turn it off

The pill answers the words "remote" and "remote control" by starting it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SESSION = "bombadil-remote"

URL = re.compile(r"https://claude\.(?:ai|com)/code(?:/|\?)\S+")
TRUST = re.compile(r"Trust\b.*\[y/N\]", re.I)
SIGN_IN = ("Sign in to Claude first (say “sign in” in the pill, or run `bombadil signin claude`), then try again. "
           "Remote control needs a claude.ai account; an API key does not work.")
# What the program says when it cannot start, and the plain thing to do about it. First match wins.
PROBLEMS = [
    (re.compile(r"not signed in|must be logged in|requires? claude\.ai subscription|auth login|full-scope|/login", re.I), SIGN_IN),
    (re.compile(r"disabled by your organization|not available for your organization|disableRemoteControl", re.I),
     "Remote control is turned off for your organization. An owner can turn it on at claude.ai/admin-settings/claude-code."),
    (re.compile(r"couldn.t verify|could not verify|couldn.t reach|could not reach|unable to reach|enotfound|offline",
                re.I),
     "Claude could not be reached. Check the Wi-Fi, then try again."),
    (re.compile(r"Workspace not trusted", re.I),
     "Claude does not trust that folder yet. Run `bombadil remote show`, answer y once, then press Ctrl-b and d."),
]


@dataclass
class State:
    running: bool = False       # the tmux session exists
    alive: bool = False         # and the program is still running in it
    url: str | None = None      # the link to open, once the program has printed it
    needs_trust: bool = False   # it is waiting for a yes about the folder
    problem: str | None = None  # why it stopped or cannot start, in plain words
    tail: str = ""              # the last lines it said, for a problem nothing above knows


def _own_scope() -> list[str]:
    """Start tmux in a scope of its own, so that restarting whatever started it (the pill's daemon, a login
    session) does not take the remote session down with it."""
    if shutil.which("systemd-run") and os.environ.get("XDG_RUNTIME_DIR"):
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--description=Bombadil remote control"]
    return []


def default_folder() -> Path:
    """A folder Bombadil makes for the remote session to work in: empty, so trusting it turns nothing on.
    (In the home folder itself Claude Code asks again at every start.)"""
    return Path.home() / "Projects" / "remote"


# What the remote session's own folder is set up with: no permission questions (the person is on another
# device, and the agent has the whole computer in Bombadil anyway) and a restore point before every
# prompt, so "undo" in the pill takes back what a remote session did, as it does for a turn from the pill.
FOLDER_SETTINGS = {
    "permissions": {"defaultMode": "bypassPermissions"},
    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bombadil snapshot --hook"}]}]},
}


def prepare_folder(folder: Path) -> None:
    """Make the default work folder, with its settings (never over settings that are already there)."""
    folder.mkdir(parents=True, exist_ok=True)
    settings = folder / ".claude" / "settings.json"
    if not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(FOLDER_SETTINGS, indent=2) + "\n")


def session_name() -> str:
    return f"Bombadil on {socket.gethostname() or 'this computer'}"


def _tmux(*args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["tmux", *args], capture_output=True, text=True)


def read_state(run=subprocess.run) -> State:
    if _tmux("has-session", "-t", SESSION, run=run).returncode != 0:
        return State()
    dead = _tmux("list-panes", "-t", SESSION, "-F", "#{pane_dead}", run=run).stdout.split()
    text = _tmux("capture-pane", "-p", "-J", "-t", SESSION, "-S", "-200", run=run).stdout
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    st = State(running=True, alive=not (dead and dead[0] == "1"), tail="\n".join(lines[-6:]))
    m = URL.search(text)
    st.url = m.group(0).rstrip(".,;)") if m else None
    st.needs_trust = bool(TRUST.search("\n".join(lines[-8:]))) and st.url is None
    if not st.alive or not st.url:
        for pattern, words in PROBLEMS:
            if pattern.search(text):
                st.problem = words
                break
    return st


def describe(st: State) -> str:
    line = f"Remote control is on. Open claude.ai/code or the Claude app and pick “{session_name()}”."
    return f"{line} Link: {st.url}" if st.url else line


def start(folder: str | Path | None = None, *, run=subprocess.run, sleep=time.sleep, tries: int = 25) -> tuple[bool, str]:
    """Start remote control, or say it is already on. Waits (up to `tries` seconds) for it to be up or to say why not."""
    if shutil.which("claude") is None:
        return False, "Claude Code is not installed on this computer."
    if shutil.which("tmux") is None:
        return False, "tmux is not installed, and remote control needs it."
    auto_trust = folder is None
    where = default_folder() if folder is None else Path(folder).expanduser()
    if auto_trust:
        prepare_folder(where)
    elif not where.is_dir():
        return False, f"{where} is not a folder."
    st = read_state(run)
    if st.alive:
        return True, describe(st)
    if st.running:   # it stopped; what it said is in `st`, and a fresh start replaces it
        _tmux("kill-session", "-t", SESSION, run=run)
    command = f"claude remote-control --name {shlex.quote(session_name())}"
    started = run([*_own_scope(), "tmux", "new-session", "-d", "-s", SESSION, "-c", str(where), "-x", "110", "-y", "30",
                   command, ";", "set-option", "-t", SESSION, "remain-on-exit", "on"], capture_output=True, text=True)
    if started.returncode != 0:
        return False, f"Could not start it: {(started.stderr or started.stdout).strip() or 'tmux failed'}"
    answered = 0   # polls since the question was answered, 0 until it is
    for _ in range(tries):
        sleep(1)
        st = read_state(run)
        if st.url:
            return True, describe(st)
        if st.needs_trust:
            if answered and answered < 4:   # the screen has not redrawn yet
                answered += 1
                continue
            if not auto_trust or answered:
                return False, (f"Claude asks whether to trust {where}. Run `bombadil remote show`, answer y once, "
                               "then press Ctrl-b and d to leave it running.")
            _tmux("send-keys", "-t", SESSION, "y", "Enter", run=run)   # the folder is the one Bombadil just made
            answered = 1
            continue
        if st.problem or (st.running and not st.alive):
            return False, st.problem or f"It stopped. What it said:\n{st.tail}"
    return True, ("Remote control is starting. Run `bombadil remote show` to watch it, or `bombadil remote url` "
                  f"for the link once it is up. In claude.ai/code pick “{session_name()}”.")


def status(run=subprocess.run) -> tuple[bool, str]:
    if shutil.which("tmux") is None:
        return False, "tmux is not installed, and remote control needs it."
    st = read_state(run)
    if st.alive:
        return True, describe(st)
    if st.running:
        return False, st.problem or f"Remote control stopped. What it said:\n{st.tail}"
    return False, "Remote control is off. Start it with `bombadil remote`, or say “remote” in the pill."


def url(run=subprocess.run) -> tuple[bool, str]:
    st = read_state(run)
    return (True, st.url) if st.alive and st.url else (False, "No link yet. Run `bombadil remote`.")


def stop(run=subprocess.run) -> tuple[bool, str]:
    if not read_state(run).running:
        return True, "Remote control was already off."
    _tmux("kill-session", "-t", SESSION, run=run)
    return True, "Remote control is off."


def show(run=subprocess.run) -> int:
    """Attach this terminal to the session. Ctrl-b then d lets go and leaves it running."""
    if not read_state(run).running:
        print("Remote control is off. Start it with `bombadil remote`.")
        return 1
    return subprocess.call(["tmux", "attach-session", "-t", SESSION])


def main(args: list[str]) -> int:
    cmd = args[0] if args else "start"
    if cmd == "show":
        return show()
    if cmd == "start":
        ok, text = start(args[1] if len(args) > 1 else None)
    elif cmd == "status":
        ok, text = status()
    elif cmd == "url":
        ok, text = url()
    elif cmd == "stop":
        ok, text = stop()
    else:
        print(__doc__)
        return 2
    print(text)
    return 0 if ok else 1

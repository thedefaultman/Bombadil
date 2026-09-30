import json
from pathlib import Path

import pytest

from bombadil import narrate, providers
from bombadil.narrate import IRREVERSIBLE, SYSTEM

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("command, text, risk", [
    ("sudo pacman -S --noconfirm ffmpeg", "Installing ffmpeg", SYSTEM),
    ("sudo pacman -S --needed ffmpeg x264 lame", "Installing ffmpeg, x264 and 1 more", SYSTEM),
    ("sudo pacman -Syu --noconfirm", "Updating the system", SYSTEM),
    ("sudo pacman -Rns docker", "Removing docker", SYSTEM),
    ("pacman -Ss ffmpeg", "Looking up “ffmpeg”", None),
    ("pacman -Qi ffmpeg", "Checking installed packages", None),
    ("yay -S --noconfirm visual-studio-code-bin", "Installing visual-studio-code-bin from the AUR", SYSTEM),
    ("sudo systemctl enable --now docker.service", "Turning on docker", SYSTEM),
    ("systemctl --user restart pipewire", "Restarting pipewire", None),
    ("systemctl status sshd", "Checking sshd", None),
    ("cd /tmp && git clone https://github.com/hyprwm/Hyprland.git", "Downloading Hyprland", None),
    ("curl -fsSLo /tmp/x.tar.gz https://example.com/files/x.tar.gz", "Downloading x.tar.gz", None),
    ("curl -s https://api.github.com/repos/a/b", "Fetching api.github.com", None),
    ("pip install --user requests", "Installing requests", None),
    ("python3 -m pip install -r requirements.txt", "Installing dependencies", None),
    ("npm install", "Installing dependencies", None),
    ("ls -la ~/Downloads", "Looking through files", None),
    ("grep -rn TODO src/", "Searching for “TODO”", None),
    ("cat ~/.config/hypr/hyprland.lua", "Reading hyprland.lua", None),
    ("echo 'Host *' | sudo tee -a /etc/ssh/ssh_config", "Writing ssh_config", SYSTEM),
    ("sudo sed -i 's/a/b/' /etc/pacman.conf", "Editing pacman.conf", SYSTEM),
    ("df -h", "Checking disk space", None),
    ("sudo mkfs.ext4 /dev/sdb1", "Formatting /dev/sdb1", IRREVERSIBLE),
    ("sudo dd if=arch.iso of=/dev/sdb bs=4M", "Writing arch.iso to /dev/sdb", IRREVERSIBLE),
    ("rm -rf ~/Downloads/old", "Deleting old", IRREVERSIBLE),
    ("rm -f /tmp/scratch.txt", "Deleting scratch.txt", None),
    ("sudo rm -rf /etc/nginx/sites-enabled/default", "Deleting default", SYSTEM),
    ("/bin/bash -lc 'sudo pacman -S --noconfirm ffmpeg'", "Installing ffmpeg", SYSTEM),
    ("nmcli device wifi connect HomeNet password hunter2", "Connecting to HomeNet", SYSTEM),
    ("hyprctl reload", "Reloading the desktop settings", None),
    ("sleep 5", "Waiting", None),
    ("sudo sh -c 'pacman -S --noconfirm docker && systemctl enable --now docker'", "Installing docker", SYSTEM),
    ("bash -c 'mkfs.ext4 /dev/sdb1'", "Formatting /dev/sdb1", IRREVERSIBLE),
    ("make 2>&1 | tail -20", "Building", None),
])
def test_shell_commands_in_plain_words(command, text, risk):
    step = narrate.shell_step(command)
    assert step.text == text
    assert step.risk == risk
    if risk:
        # A marked step shows the exact command, never a paraphrase.
        assert step.command == " ".join(narrate._unwrap(command).split())


def test_unknown_commands_use_the_description_or_the_program():
    assert narrate.shell_step("frobnicate --all", "Install frobnicator plugins").text == "Installing frobnicator plugins"
    assert narrate.shell_step("frobnicate --all", "Frobnicator plugin run").text == "Running frobnicate"
    assert narrate.shell_step("sudo frobnicate").risk == SYSTEM
    assert narrate.shell_step("sudo sh -c 'echo installing; sleep 120'", "Install docker").text == "Installing docker"
    assert narrate.shell_step("python3 -c 'print(1)'", "Check the Python version").text == "Checking the Python version"


def test_gerunds():
    assert [narrate.gerund(v) for v in ("run", "make", "install", "set", "tie", "see", "list")] == [
        "running", "making", "installing", "setting", "tying", "seeing", "listing"]


@pytest.mark.parametrize("name, args, text, done", [
    ("mcp__bombadil-os__show_panel", {"name": "browser"}, "Opening the browser", "Opened the browser"),
    ("mcp__bombadil-os__hide_panel", {"name": "files"}, "Putting Files away", None),
    ("mcp__bombadil-os__create_app", {"title": "Passwords", "qml": "a\nb\nc\n", "python": "x\ny"},
     "Building Passwords, 5 lines", "Made Passwords"),
    ("mcp__bombadil-os__screenshot", {}, "Looking at the screen", None),
    ("mcp__bombadil-os__rollback", {}, "Undoing the last change", "Undid the last change"),
    ("mcp__github__create_issue", {}, "Using github", None),
    ("Read", {"file_path": "/etc/fstab"}, "Reading fstab", None),
    ("Write", {"file_path": "/home/u/notes.md", "content": "a\nb\n"}, "Writing notes.md, 2 lines", "Wrote notes.md"),
    ("Edit", {"file_path": "/home/u/.bashrc"}, "Editing .bashrc", "Edited .bashrc"),
    ("WebSearch", {"query": "arch linux btrfs snapper"}, "Searching the web for “arch linux btrfs snapper”", None),
    ("WebFetch", {"url": "https://wiki.archlinux.org/title/Btrfs"}, "Reading wiki.archlinux.org", None),
    ("Agent", {"description": "Find config files"}, "Finding config files", None),
    ("TaskUpdate", {"taskId": "1", "status": "in_progress", "activeForm": "Installing ffmpeg"}, "Installing ffmpeg", None),
])
def test_tools_in_plain_words(home, name, args, text, done):
    step = narrate.tool_step(name, args)
    assert (step.text, step.done) == (text, done)


def test_changing_an_existing_app_says_so(home):
    (home / "Apps" / "passwords").mkdir(parents=True)
    (home / "Apps" / "passwords" / "main.qml").write_text("")
    assert narrate.tool_step("mcp__bombadil-os__create_app", {"title": "Passwords", "qml": "x"}).text == "Changing Passwords"


def test_internals_never_show():
    assert narrate.tool_step("ToolSearch", {"query": "x"}) is None
    assert narrate.tool_step("SomeNewTool", {}).text == "Working on it"
    assert "mcp__" not in narrate.tool_step("mcp__bombadil-os__nope", {}).text


def test_system_file_edits_are_marked():
    step = narrate.tool_step("Edit", {"file_path": "/etc/hosts"})
    assert step.risk == SYSTEM and step.command == "edit /etc/hosts"
    fc = narrate.file_change_step([{"path": "/etc/hosts", "kind": "update"}, {"path": "/home/u/a", "kind": "add"}])
    assert fc.text == "Editing 2 files" and fc.risk == SYSTEM


def test_a_building_app_counts_its_lines_as_they_stream(home):
    n = narrate.Narrator()
    first = n.on_event({"kind": "tool_start", "index": 1, "name": "mcp__bombadil-os__create_app"})
    assert first["text"] == "Building an app"
    lines = []
    # The tool input as raw JSON text, the way it streams: newlines are escaped as \n.
    partial = r'{"title": "Passwords", "qml": "import QtQuick\nimport Bombadil\n'
    for chunk in (partial[:10], partial[10:], r'AppWindow {\n', r'  title: \"x\"\n', r'}\n"}'):
        out = n.on_event({"kind": "tool_input", "index": 1, "partial": chunk})
        if out:
            lines.append(out["text"])
    assert lines == ["Building Passwords, 2 lines", "Building Passwords, 3 lines",
                     "Building Passwords, 4 lines", "Building Passwords, 5 lines"]
    # Nothing counts as made until the whole call arrives.
    assert n.done == []
    n.on_event({"kind": "tool", "name": "mcp__bombadil-os__create_app",
                "input": {"title": "Passwords", "qml": "a\nb\nc\nd\ne\n"}})
    assert n.summary() == "Made Passwords."


def test_summary_and_stop_lines():
    n = narrate.Narrator()
    assert n.summary() == "" and n.stopped_line() == "Stopped."
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S ffmpeg"}})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "ls"}})
    n.on_event({"kind": "tool", "name": "mcp__bombadil-os__show_panel", "input": {"name": "browser"}})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo systemctl enable --now docker"}})
    n.on_event({"kind": "tool", "name": "Edit", "input": {"file_path": "/home/u/a.txt"}})
    assert n.summary() == "Installed ffmpeg, opened the browser, turned on docker and 1 more."
    assert n.system and not n.irreversible
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S docker"}})
    assert n.stopped_line() == "Stopped while installing docker."


def test_the_agents_own_words_show_their_newest_line():
    n = narrate.Narrator()
    assert n.on_event({"kind": "text_delta", "text": "I'll install"})["text"] == "I'll install"
    out = n.on_event({"kind": "text_delta", "text": " ffmpeg.\nThen open"})
    assert out == {"text": "Then open", "risk": None, "command": None, "source": "agent"}


def test_claude_task_list_names_the_step_it_starts():
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "TaskCreate", "id": "t1",
                "input": {"subject": "Install ffmpeg", "activeForm": "Installing ffmpeg"}})
    n.on_event({"kind": "tool_result", "id": "t1", "output": "Task #3 created successfully: Install ffmpeg"})
    out = n.on_event({"kind": "tool", "name": "TaskUpdate", "input": {"taskId": "3", "status": "in_progress"}})
    assert out["text"] == "Installing ffmpeg"
    assert n.on_event({"kind": "tool", "name": "TaskUpdate", "input": {"taskId": "3", "status": "completed"}}) is None


def test_a_real_claude_stream_narrates_as_it_arrives():
    """The stream Claude Code 2.1.283 printed for "install ffmpeg" (captured against a fake API)."""
    p = providers.Claude("x")
    n = narrate.Narrator()
    lines = []
    for raw in (FIXTURES / "claude-install-ffmpeg.jsonl").read_text().splitlines():
        for ev in p.parse(raw):
            out = n.on_event(ev)
            if out:
                lines.append(out["text"])
    assert lines[:3] == ["Thinking", "I'll install", "I'll install ffmpeg."]
    assert "Installing ffmpeg with pacman" in lines
    assert lines[-1] == "ffmpeg is installed."


def test_codex_reasoning_headings_become_the_line():
    p = providers.Codex("x")
    n = narrate.Narrator()
    evs = list(p.parse(json.dumps({"type": "item.completed", "item": {
        "id": "item_0", "type": "reasoning", "text": "**Installing ffmpeg with pacman**\n\nI will..."}})))
    assert n.on_event(evs[0])["text"] == "Installing ffmpeg with pacman"


def test_each_line_is_said_once_and_a_repeated_step_comes_back_after_the_agent_spoke():
    n = narrate.Narrator()
    bash = {"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S ffmpeg"}}
    assert n.on_event(bash)["text"] == "Installing ffmpeg"
    assert n.on_event(bash) is None
    assert n.on_event({"kind": "text_delta", "text": "Retrying."})["text"] == "Retrying."
    assert n.on_event({"kind": "text", "text": "Retrying."}) is None   # the full message, already shown
    assert n.on_event(bash)["text"] == "Installing ffmpeg"


@pytest.mark.parametrize("command, text, risk", [
    # Deleting in home written with $HOME, or above it, cannot be undone either.
    ('rm -rf "$HOME/Documents"', "Deleting Documents", IRREVERSIBLE),
    ("rm -rf ${HOME}/old", "Deleting old", IRREVERSIBLE),
    ('rm -rf "$HOME"', None, IRREVERSIBLE),
    ("sudo rm -rf /home", "Deleting home", IRREVERSIBLE),
    ("rm -rf /", None, IRREVERSIBLE),
    ("find ~ -name '*.tmp' -exec rm -f {} +", "Looking through files", IRREVERSIBLE),
    ("cd /tmp && rm -rf build", "Deleting build", None),
    ("rm -rf build", "Deleting build", IRREVERSIBLE),
    ("rm -rf $SOMEDIR/x", "Deleting x", None),
    # Force pushes and cleans with the flag anywhere.
    ("git push origin main --force", None, IRREVERSIBLE),
    ("git push -u origin +main", None, IRREVERSIBLE),
    ("git -C ~/src/app push --force-with-lease", None, IRREVERSIBLE),
    ("git clean -d -f", None, IRREVERSIBLE),
    ("git push -u origin main", "Pushing changes", None),
    # Any shell option cluster with -c.
    ('bash -ec "mkfs.ext4 /dev/sdb1"', "Formatting /dev/sdb1", IRREVERSIBLE),
    ("bash -e -o pipefail -c 'wipefs -a /dev/sdb'", "Erasing /dev/sdb", IRREVERSIBLE),
    # Reading disks and system files is not changing them.
    ("sudo cryptsetup open /dev/sda2 root", "Unlocking /dev/sda2", SYSTEM),
    ("sudo cryptsetup status root", "Looking at the disks", SYSTEM),
    ("dd if=/dev/sda of=/dev/null bs=1M", "Reading /dev/sda", None),
    ("sudo dd if=/dev/nvme0n1 of=~/backup.img", "Writing backup.img", SYSTEM),
    ("sudo sfdisk -d /dev/sda", "Looking at the disks", SYSTEM),
    ("sfdisk -l -o Device,Size", "Looking at the disks", None),
    ("wipefs /dev/sdb", "Looking at the disks", None),
    ("sudo sfdisk /dev/sdb < layout.txt", "Changing the partitions on sdb", IRREVERSIBLE),
    ('sed -n "1,200p" /etc/pacman.conf', None, None),
    ("bash -lc 'sed -n 1,50p /etc/fstab'", None, None),
    ("cp /usr/share/applications/firefox.desktop ~/.local/share/applications/", "Copying firefox.desktop", None),
    ("ln -s /usr/bin/python3 ~/bin/python", "Linking python", None),
    ("sudo sed -i 's/#Color/Color/' /etc/pacman.conf", "Editing pacman.conf", SYSTEM),
    ("cp my.conf /etc/my.conf", "Copying my.conf", SYSTEM),
])
def test_marks_follow_what_a_command_really_does(command, text, risk, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "dan"))
    step = narrate.shell_step(command)
    if text is not None:
        assert step.text == text
    assert step.risk == risk


def test_a_heredoc_is_what_it_writes_not_commands():
    script = "cat > ~/bin/cleanup.sh <<'EOF'\n#!/bin/sh\nrm -rf ~/.cache/thumbnails\nEOF"
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": script}})
    assert n.step.text == "Writing cleanup.sh" and n.step.risk is None
    assert n.summary() == "Wrote cleanup.sh." and not n.irreversible
    readme = "cat > ~/README.md <<EOF\nRun:\n  sudo pacman -S ffmpeg\nEOF\necho done"
    assert narrate.shell_step(readme).text == "Writing README.md"
    assert narrate.shell_step(readme).risk is None
    assert narrate.shell_step("cat << 'EOF' > ~/a.txt\nhi\nEOF").text == "Writing a.txt"
    tabs = "cat <<-END > notes.txt\n\thello\n\tEND\nrm -rf ~/x"
    assert narrate.shell_step(tabs).risk == IRREVERSIBLE   # the command after the body still counts


# Made up at run time, so no secret scanner mistakes these fixtures for credentials.
FAKE = "not" + "-a-" + "real-one"


@pytest.mark.parametrize("command, host", [
    (f"curl https://user:{FAKE}@api.example.com/v1/x", "api.example.com"),
    (f"curl -u admin:{FAKE} example.com/api", "example.com"),
    (f'curl -H "Authorization: Bearer {FAKE}" api.example.com/v1', "api.example.com"),
    ("curl -sS -X POST -d '{\"a\": 1}' https://api.example.com/v1/items", "api.example.com"),
    ("http POST api.example.com/items name=x", "api.example.com"),
])
def test_a_fetch_names_its_host_never_its_credentials(command, host):
    assert narrate.shell_step(command).text == f"Fetching {host}"


def test_web_tools_strip_credentials_too():
    url = f"https://u:{FAKE}@x.com/a"
    assert narrate.tool_step("WebFetch", {"url": url}).text == "Reading x.com"
    assert narrate.tool_step("mcp__bombadil-os__open_url", {"url": url}).text == "Opening x.com"


def test_a_tool_that_has_not_said_its_argument_yet_gets_plain_words():
    n = narrate.Narrator()
    assert n.on_event({"kind": "tool_start", "name": "Edit", "index": 0})["text"] == "Editing a file"
    assert n.stopped_line() == "Stopped while editing a file."
    assert n.on_event({"kind": "tool_input", "index": 0, "partial": '{"file_path": "/home/d/app.py", "old'})["text"] \
        == "Editing app.py"
    assert narrate.tool_step("Grep", {}).text == "Searching"
    assert narrate.tool_step("WebSearch", {}).text == "Searching the web"
    assert narrate.tool_step("Write", {}).text == "Writing a file"


@pytest.mark.parametrize("ev", [
    {"kind": "tool", "name": "mcp__bombadil-os__show_panel", "input": {"name": ["browser"]}},
    {"kind": "tool", "name": "mcp__bombadil-os__create_app", "input": {"title": 5}},
    {"kind": "tool", "name": "mcp__bombadil-os__create_app", "input": {"title": {"a": 1}}},
    {"kind": "text", "text": None},
    {"kind": "text_delta", "text": None},
    {"kind": "output", "text": None},
    {"kind": "file_change", "changes": 3},
])
def test_odd_input_never_raises(ev):
    narrate.Narrator().on_event(ev)


# -- the plan --

def _tool(name, call=None, **inp):
    return {"kind": "tool", "name": name, "id": call, "input": inp}


def _result(call, output, error=False):
    return {"kind": "tool_result", "id": call, "output": output, "error": error}


def _made(n, call, tid, subject, **inp):
    """A TaskCreate and the result that names the task."""
    n.on_event(_tool("TaskCreate", call, subject=subject, description="d", **inp))
    n.on_event(_result(call, f"Task #{tid} created successfully: {subject}"))


def _rows(n):
    return [(r["id"], r["subject"], r["active"], r["status"]) for r in n.plan]


def test_a_created_task_is_a_row_that_its_result_then_names():
    n = narrate.Narrator()
    assert n.take_plan() is None   # nothing yet, so nothing to send
    n.on_event(_tool("TaskCreate", "c1", subject="Install ffmpeg", description="d", activeForm="Installing ffmpeg"))
    n.on_event(_tool("TaskCreate", "c2", subject="Open the browser", description="d"))
    # Not confirmed yet: no id, in the order made. Without an activeForm the subject read as a verb.
    assert n.take_plan() == [
        {"id": None, "subject": "Install ffmpeg", "active": "Installing ffmpeg", "status": "pending"},
        {"id": None, "subject": "Open the browser", "active": "Opening the browser", "status": "pending"}]
    assert n.take_plan() is None   # unchanged since it was taken
    # The second finishes first: rows the CLI has confirmed come before the ones it has not.
    n.on_event(_result("c2", "Task #2 created successfully: Open the browser"))
    assert [r[0] for r in _rows(n)] == ["2", None]
    n.on_event(_result("c1", "Task #1 created successfully: Install ffmpeg"))
    assert [r[0] for r in _rows(n)] == ["1", "2"]
    # A subject that is not a verb has no present-tense form; the desk shows the subject.
    _made(n, "c3", 3, "ffmpeg works")
    assert _rows(n)[2] == ("3", "ffmpeg works", None, "pending")


def test_task_updates_move_a_step_along_and_only_a_real_change_is_sent():
    n = narrate.Narrator()
    _made(n, "c1", 1, "Install ffmpeg", activeForm="Installing ffmpeg")
    _made(n, "c2", 2, "Open the browser")
    n.take_plan()
    line = n.on_event(_tool("TaskUpdate", "u1", taskId="1", status="in_progress"))
    assert line["text"] == "Installing ffmpeg"
    assert [r["status"] for r in n.take_plan()] == ["in_progress", "pending"]
    # Ticking a step off changes the plan but not the line, so it is the plan that says so.
    assert n.on_event(_tool("TaskUpdate", "u2", taskId="1", status="completed")) is None
    assert [r["status"] for r in n.take_plan()] == ["completed", "pending"]
    # Nothing a person could see changed: nothing to send.
    n.on_event(_tool("TaskUpdate", "u3", taskId="2", owner="me", description="more words"))
    n.on_event(_tool("TaskUpdate", "u4", taskId="1", status="completed"))
    assert n.take_plan() is None
    # New words for a task come with it, and a new subject drops the old present tense.
    n.on_event(_tool("TaskUpdate", "u5", taskId="2", subject="Open the browser and log in"))
    assert _rows(n)[1] == ("2", "Open the browser and log in", "Opening the browser and log in", "pending")
    n.on_event(_tool("TaskUpdate", "u6", taskId="2", activeForm="Logging in"))
    assert _rows(n)[1][2] == "Logging in"
    assert n.take_plan()[1]["active"] == "Logging in"


def test_a_failed_create_leaves_no_row_and_the_plan_says_so():
    n = narrate.Narrator()
    n.on_event(_tool("TaskCreate", "c1", subject="Install ffmpeg", description="d"))
    assert len(n.take_plan()) == 1
    n.on_event(_result("c1", "InputValidationError: description is required", error=True))
    assert n.take_plan() == []   # changed: the row is gone
    # A result that names no task leaves nobody able to update it later, so there is no row either.
    n.on_event(_tool("TaskCreate", "c2", subject="Install ffmpeg", description="d"))
    n.on_event(_result("c2", "Something else happened"))
    assert n.plan == []
    # A create the CLI never gave an id is ignored rather than left waiting for a result.
    n.on_event(_tool("TaskCreate", None, subject="Install ffmpeg", description="d"))
    assert n.plan == []


def test_deleted_tasks_leave_the_plan_and_unknown_ones_are_not_shown_as_a_number():
    n = narrate.Narrator()
    _made(n, "c1", 1, "Install ffmpeg")
    _made(n, "c2", 2, "Open the browser")
    n.on_event(_tool("TaskUpdate", "u1", taskId="1", status="deleted"))
    assert [r[0] for r in _rows(n)] == ["2"]
    n.on_event(_tool("TaskUpdate", "u2", taskId="9", status="deleted"))   # never seen: nothing
    n.take_plan()
    # Made in an earlier turn: with no words of its own there is nothing to show.
    n.on_event(_tool("TaskUpdate", "u3", taskId="7", status="in_progress"))
    n.on_event(_tool("TaskUpdate", "u4", taskId="8", status="completed", owner="me"))
    assert n.take_plan() is None
    # With its subject, an unknown task is added as it stands.
    n.on_event(_tool("TaskUpdate", "u5", taskId="7", status="in_progress", subject="Check the sound"))
    assert _rows(n)[-1] == ("7", "Check the sound", "Checking the sound", "in_progress")


def test_claudes_own_spellings_of_a_task_update_are_read():
    n = narrate.Narrator()
    _made(n, "c1", 1, "Install ffmpeg")
    _made(n, "c2", 2, "Open the browser")
    n.on_event(_tool("TaskUpdate", "u1", id="1", status="in_progress", active_form="Installing ffmpeg now"))
    assert _rows(n)[0] == ("1", "Install ffmpeg", "Installing ffmpeg now", "in_progress")
    line = n.on_event(_tool("TaskUpdate", "u2", task_id="2", status="in_progress"))
    assert line["text"] == "Opening the browser"
    n.on_event(_tool("TaskCreate", "c3", subject="Check the sound", description="d", active_form="Listening"))
    assert n.plan[-1]["active"] == "Listening"
    # A status the schema does not have changes nothing.
    n.on_event(_tool("TaskUpdate", "u3", taskId="1", status="blocked"))
    assert n.plan[0]["status"] == "in_progress"


def test_a_subagents_tasks_are_not_the_turns_plan():
    n = narrate.Narrator()
    _made(n, "c1", 1, "Install ffmpeg", activeForm="Installing ffmpeg")
    n.take_plan()
    sub = {"parent": "toolu_agent"}
    # Its list is numbered from 1 like ours: it must neither add rows nor change ours.
    n.on_event({**_tool("TaskCreate", "s1", subject="Read the config", description="d"), **sub})
    n.on_event({**_result("s1", "Task #1 created successfully: Read the config"), **sub})
    line = n.on_event({**_tool("TaskUpdate", "s2", taskId="1", status="in_progress"), **sub})
    n.on_event({**_tool("TaskUpdate", "s3", taskId="1", status="completed"), **sub})
    n.on_event({**_tool("TodoWrite", "s4", todos=[{"content": "Read", "status": "pending"}]), **sub})
    assert line is None   # ours is "Installing ffmpeg", not the subagent's task
    assert n.take_plan() is None
    assert _rows(n) == [("1", "Install ffmpeg", "Installing ffmpeg", "pending")]


def test_todowrite_replaces_the_whole_plan_with_places_for_ids():
    n = narrate.Narrator()
    line = n.on_event(_tool("TodoWrite", "w1", todos=[
        {"content": "Install ffmpeg", "status": "in_progress", "activeForm": "Installing ffmpeg"},
        {"content": "Open the browser", "status": "pending"},
        {"content": "", "status": "pending"},
        {"content": "Check the sound", "status": "surprise"},
        "not a todo"]))
    assert line["text"] == "Installing ffmpeg"
    assert n.take_plan() == [
        {"id": "1", "subject": "Install ffmpeg", "active": "Installing ffmpeg", "status": "in_progress"},
        {"id": "2", "subject": "Open the browser", "active": "Opening the browser", "status": "pending"},
        {"id": "3", "subject": "Check the sound", "active": "Checking the sound", "status": "pending"}]
    # The next call is the whole list again: what it leaves out is gone.
    n.on_event(_tool("TodoWrite", "w2", todos=[
        {"content": "Install ffmpeg", "status": "completed"},
        {"content": "Open the browser", "status": "in_progress"}]))
    assert [(r["id"], r["status"]) for r in n.take_plan()] == [("1", "completed"), ("2", "in_progress")]
    n.on_event(_tool("TodoWrite", "w3", todos="nothing to see"))
    assert n.take_plan() is None
    n.on_event(_tool("TodoWrite", "w4", todos=[]))
    assert n.take_plan() == []


def test_the_last_step_under_way_is_the_current_one_and_a_finished_list_is_not_a_new_line():
    n = narrate.Narrator()
    todos = [{"content": "Install ffmpeg", "status": "in_progress"},
             {"content": "Open the browser", "status": "in_progress"}]
    assert n.on_event(_tool("TodoWrite", "w1", todos=todos))["text"] == "Opening the browser"
    done = [{**t, "status": "completed"} for t in todos]
    # Ticking the last one off: the plan changes, the line stays what it was.
    assert n.on_event(_tool("TodoWrite", "w2", todos=done)) is None
    assert [r["status"] for r in n.take_plan()] == ["completed", "completed"]


def test_a_task_list_result_brings_the_table_to_what_the_list_says():
    n = narrate.Narrator()
    _made(n, "c1", 1, "Install ffmpeg (with x264)", activeForm="Installing ffmpeg with x264")
    n.on_event(_tool("TaskCreate", "c2", subject="Pending create", description="d"))
    n.take_plan()
    n.on_event(_tool("TaskList", "l1"))
    n.on_event(_result("l1", "#1 [completed] Install ffmpeg (with x264) (me)\n"
                             "#4 [in_progress] Open the browser [blocked by #1]\n"
                             "#5 [pending] Check the sound (you) [blocked by #4]"))
    # A task we know keeps our words, the list only says where it stands; new ones are read from it.
    # The create still waiting for its result is not lost.
    assert _rows(n) == [
        ("1", "Install ffmpeg (with x264)", "Installing ffmpeg with x264", "completed"),
        ("4", "Open the browser", "Opening the browser", "in_progress"),
        ("5", "Check the sound", "Checking the sound", "pending"),
        (None, "Pending create", None, "pending")]
    # An empty list is a real answer; an error is not one.
    n.on_event(_tool("TaskList", "l2"))
    n.on_event(_result("l2", "Error: could not read the list", error=True))
    assert len(n.plan) == 4
    n.on_event(_tool("TaskList", "l3"))
    n.on_event(_result("l3", "No tasks found"))
    assert [r["id"] for r in n.plan] == [None]
    # The result of a call that was not a TaskList is not read as one.
    n.on_event(_result("bash1", "#9 [pending] Not a task"))
    assert [r["id"] for r in n.plan] == [None]


def test_a_number_made_again_is_the_task_made_last():
    n = narrate.Narrator()
    _made(n, "c1", 1, "Install ffmpeg")
    _made(n, "c2", 1, "Install x264")   # the list was started over
    assert _rows(n) == [("1", "Install x264", "Installing x264", "pending")]


def test_a_long_plan_is_cut_and_long_words_too():
    n = narrate.Narrator()
    for i in range(narrate.MAX_PLAN + 5):
        _made(n, f"c{i}", i + 1, f"Step {i} " + "very long " * 30)
    assert len(n.plan) == narrate.MAX_PLAN
    assert all(len(r["subject"]) <= narrate.MAX_TASK_TEXT for r in n.plan)
    n.on_event(_tool("TaskUpdate", "u1", taskId="99", status="in_progress", subject="One too many"))
    assert len(n.plan) == narrate.MAX_PLAN
    n.on_event(_tool("TodoWrite", "w1", todos=[{"content": f"Step {i}"} for i in range(narrate.MAX_PLAN + 5)]))
    assert len(n.plan) == narrate.MAX_PLAN


def test_a_real_claude_stream_with_a_task_list_builds_the_plan_as_it_goes():
    """A turn that keeps a task list, a subagent's own list inside it. Reconstructed from the schemas
    of Claude Code 2.1.284 in the envelope of the real capture (see claude-plan.jsonl)."""
    p = providers.Claude("x")
    n = narrate.Narrator()
    plans, lines = [], []
    for raw in (FIXTURES / "claude-plan.jsonl").read_text().splitlines():
        for ev in p.parse(raw):
            line = n.on_event(ev)
            if line and line["source"] == "step":
                lines.append((line["text"], line["risk"]))
            plan = n.take_plan()
            if plan is not None:
                plans.append(" ".join(f"{r['id']}:{r['status']}" for r in plan))
    assert plans == [
        "None:pending",
        "None:pending None:pending",
        "1:pending None:pending",
        "1:pending 2:pending",
        "1:in_progress 2:pending",
        "1:completed 2:pending",
        "1:completed 2:in_progress",
        "1:completed 2:completed"]
    assert [r["subject"] for r in n.plan] == ["Install ffmpeg", "Open the browser"]
    # The line names the step it starts, then what runs under it with its mark.
    assert lines[:4] == [("Planning the steps", None), ("Installing ffmpeg", None), ("Installing ffmpeg", "system"),
                         ("Opening the browser", None)]
    assert n.touched_counts() == {"package": 1} and n.touched_text() == "1 package so far"
    assert n.summary() == "Installed ffmpeg and opened the browser."


@pytest.mark.parametrize("ev", [
    {"kind": "tool", "name": "TaskCreate", "id": ["x"], "input": {"subject": ["a"]}},
    {"kind": "tool", "name": "TaskCreate", "id": "c", "input": {"subject": {"a": 1}, "activeForm": 3}},
    {"kind": "tool", "name": "TaskCreate", "id": "c", "input": None},
    {"kind": "tool", "name": "TaskUpdate", "input": {"taskId": ["1"], "status": ["done"]}},
    {"kind": "tool", "name": "TaskUpdate", "input": {"taskId": 1, "status": "in_progress", "subject": 5}},
    {"kind": "tool", "name": "TaskUpdate", "input": None},
    {"kind": "tool", "name": "TodoWrite", "input": {"todos": [None, 3, {"content": None}, {"content": ["a"], "status": []}]}},
    {"kind": "tool", "name": "TodoWrite", "input": {"todos": "nope"}},
    {"kind": "tool", "name": "TaskList", "id": ["l"], "input": {}},
    {"kind": "tool_result", "id": None, "output": 5, "error": None},
    {"kind": "tool_result", "id": {"a": 1}, "output": None},
])
def test_odd_plan_events_never_raise(ev):
    n = narrate.Narrator()
    n.on_event(ev)
    n.take_plan()
    n.on_event(_tool("TaskList", "l1"))
    n.on_event(_result("l1", None))
    n.take_plan()


# -- what the turn has touched --

@pytest.mark.parametrize("command, counts", [
    ("sudo pacman -S --noconfirm ffmpeg", {"package": 1}),
    ("sudo pacman -S --needed ffmpeg x264 lame", {"package": 3}),
    ("sudo pacman -Rns docker", {"package": 1}),
    ("pip install --user requests rich", {"package": 2}),
    ("pip uninstall -y requests", {"package": 1}),
    ("sudo systemctl enable --now docker.service", {"service": 1}),
    ("systemctl --user restart pipewire wireplumber", {"service": 2}),
    # One command line, two kinds: the line names the first change, the count keeps them all.
    ("sudo sh -c 'pacman -S --noconfirm docker && systemctl enable --now docker'", {"package": 1, "service": 1}),
    ("sudo pacman -S --noconfirm docker && sudo systemctl enable docker", {"package": 1, "service": 1}),
    ("mkdir -p ~/a/b && touch ~/a/b/c.txt", {"file": 2}),
    ("echo hi > notes.txt", {"file": 1}),
    ("cat > ~/x.sh <<'EOF'\necho hi\nEOF\nchmod +x ~/x.sh", {"file": 1}),
    ("cp a.txt b.txt", {"file": 1}),
    ("mv a b c dir/", {"file": 3}),
    ("rm -f /tmp/a /tmp/b", {"file": 2}),
    ("chmod 755 run.sh", {"file": 1}),
    ("chown -R me:me ~/data", {"file": 1}),
    ("sudo sed -i 's/a/b/' /etc/pacman.conf", {"file": 1}),
    ("echo x | sudo tee -a /etc/motd", {"file": 1}),
    ("ln -s /usr/bin/python3 ~/bin/python", {"file": 1}),
    # Looking is not changing, and what has no countable thing in it counts nothing.
    ("ls ~/Downloads", {}),
    ("cat ~/a.txt", {}),
    ("pacman -Ss ffmpeg", {}),
    ("sudo pacman -Syu --noconfirm", {}),
    ("npm install", {}),
    ("systemctl status sshd", {}),
    ("sudo systemctl daemon-reload", {}),
    ("echo hi > /dev/null", {}),
])
def test_a_shell_command_says_what_it_touches(home, command, counts):
    n = narrate.Narrator()
    n.on_event(_tool("Bash", "b1", command=command))
    assert n.touched_counts() == counts


def test_the_tools_that_write_say_what_they_touch(home):
    n = narrate.Narrator()
    n.on_event(_tool("Write", "w", file_path="/home/u/notes.md", content="a\nb\n"))
    n.on_event(_tool("Edit", "e", file_path="/home/u/.bashrc", old_string="a", new_string="b"))
    n.on_event(_tool("Read", "r", file_path="/home/u/other.md"))
    n.on_event({"kind": "file_change", "id": "f", "changes": [{"path": "/etc/hosts", "kind": "update"},
                                                               {"path": "/home/u/new.txt", "kind": "add"}]})
    n.on_event(_tool("mcp__bombadil-os__create_app", "a", title="Passwords", qml="Item {}"))
    n.on_event(_tool("mcp__bombadil-os__create_app", "a2", title="Passwords", qml="Item { }"))   # again: the same app
    n.on_event(_tool("mcp__bombadil-os__create_app", "a3", title="Notes", qml="Item {}"))
    n.on_event({"kind": "tool", "name": "mcp__bombadil-os__show_panel", "id": "p", "input": {"name": "browser"}})
    assert n.touched_counts() == {"file": 4, "app": 2}


def test_a_file_counts_once_however_it_was_changed_and_spelled(home):
    n = narrate.Narrator()
    doc = str(home / "notes.md")
    n.on_event(_tool("Write", "w1", file_path=doc, content="a"))
    n.on_event(_tool("Edit", "e1", file_path=doc, old_string="a", new_string="b"))
    n.on_event(_tool("Bash", "b1", command="echo more >> ~/notes.md"))
    n.on_event(_tool("Bash", "b2", command="sed -i s/b/c/ $HOME/notes.md"))
    assert n.touched_counts() == {"file": 1}
    n.on_event(_tool("Write", "w2", file_path=str(home / "other.md"), content="x"))
    assert n.touched_counts() == {"file": 2}


def test_a_step_still_being_written_counts_only_once_it_is_whole(home):
    n = narrate.Narrator()
    n.on_event({"kind": "tool_start", "index": 0, "name": "Write"})
    n.on_event({"kind": "tool_input", "index": 0, "partial": '{"file_path": "/home/u/a.txt", "content": "x'})
    n.on_event({"kind": "tool_start", "index": 1, "name": "mcp__bombadil-os__create_app"})
    n.on_event({"kind": "tool_input", "index": 1, "partial": '{"title": "Passwords", "qml": "a\\nb\\nc'})
    assert n.touched == {} and n.touched_text() == ""
    n.on_event(_tool("Write", "w", file_path="/home/u/a.txt", content="x"))
    assert n.touched_counts() == {"file": 1}


def test_the_count_in_words():
    n = narrate.Narrator()
    assert n.touched_text() == "" and n.touched_counts() == {}
    n.on_event(_tool("Bash", "b1", command="sudo pacman -S --noconfirm ffmpeg"))
    assert n.touched_text() == "1 package so far"
    n.on_event(_tool("Bash", "b2", command="sudo pacman -S --noconfirm x264 lame"))
    assert n.touched_text() == "3 packages so far"
    n.on_event(_tool("Write", "w", file_path="/home/u/a.txt", content="x"))
    assert n.touched_text() == "3 packages and 1 file so far"
    n.on_event(_tool("Bash", "b3", command="sudo systemctl enable --now docker"))
    assert n.touched_text() == "3 packages, 1 service and 1 file so far"
    n.on_event(_tool("mcp__bombadil-os__create_app", "a", title="Passwords", qml="Item {}"))
    assert n.touched_text() == "3 packages, 1 service, 1 file and 1 app so far"
    assert n.touched_counts() == {"package": 3, "service": 1, "file": 1, "app": 1}


# -- the job tool: the line says what it did --

JOB = "mcp__bombadil-os__job"


@pytest.mark.parametrize("args, text", [
    ({"op": "start", "title": "Ubuntu 26.04 ISO", "command": "curl -O https://x/y.iso"}, "Watching Ubuntu 26.04 ISO"),
    ({"op": "start", "title": "the build", "command": "tail --pid=1 -f /dev/null", "kind": "watch"},
     "Watching the build"),
    ({"op": "start", "title": "  Two   words\n", "command": "x"}, "Watching Two words"),
    ({"op": "start", "seconds": 600}, "Watching Timer, 10 min"),
    ({"op": "start", "title": "Tea", "seconds": 300}, "Watching Tea"),
    ({"op": "start", "command": "sleep 100"}, "Starting a background job"),
    ({"op": "start", "seconds": 0, "command": "x"}, "Starting a background job"),
    ({"op": "start", "title": "x" * 80, "command": "x"}, "Watching " + "x" * 40),
    ({"op": "list"}, "Checking the background jobs"),
    ({}, "Checking the background jobs"),
])
def test_the_line_says_what_the_job_tool_did(home, args, text):
    step = narrate.tool_step(JOB, args)
    # A background job is nothing a restore point could undo, so it is no change and has no closing sentence.
    assert (step.text, step.done, step.risk, step.command) == (text, None, None, None)


def test_a_stop_names_the_job_it_stops(home):
    from bombadil import jobs
    j = jobs.Jobs(runner=lambda argv, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    rec = j.start("Ubuntu 26.04 ISO", "curl -O https://x/y.iso")
    step = narrate.tool_step(JOB, {"op": "stop", "id": rec["id"]})
    assert (step.text, step.done) == ("Stopped Ubuntu 26.04 ISO", None)
    # A job that is not there, or an id that is not one, is still said plainly and reads nothing odd.
    for a in ({"op": "stop", "id": "ffffff"}, {"op": "stop"}, {"op": "stop", "id": "../../etc/passwd"},
              {"op": "stop", "id": ["x"]}, {"op": "stop", "id": 5}):
        assert narrate.tool_step(JOB, a).text == "Stopped a background job"


def test_the_job_line_goes_to_the_pill_and_leaves_no_undo_behind(home):
    n = narrate.Narrator()
    line = n.on_event({"kind": "tool", "name": JOB, "input": {"op": "start", "title": "Ubuntu 26.04 ISO",
                                                              "command": "curl"}})
    assert line == {"text": "Watching Ubuntu 26.04 ISO", "risk": None, "command": None, "source": "step"}
    n.on_event({"kind": "tool", "name": JOB, "input": {"op": "list"}})
    assert n.summary() == "" and n.done == [] and n.touched_counts() == {} and not n.system
    assert n.stopped_line() == "Stopped while checking the background jobs."


@pytest.mark.parametrize("args", [
    {"op": ["start"], "title": ["x"], "seconds": ["5"]}, {"op": 5, "title": {"a": 1}},
    {"op": "start", "title": None, "seconds": True}, {"op": "start", "seconds": "soon"},
    {"op": "start", "seconds": float("inf")}, {"op": "start", "seconds": "1e999"}, {"op": "stop", "id": None},
])
def test_odd_job_arguments_never_break_the_line(home, args):
    assert narrate.tool_step(JOB, args).text

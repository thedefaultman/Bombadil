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

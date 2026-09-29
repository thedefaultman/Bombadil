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


# -- why, and after reading what --

@pytest.mark.parametrize("said, because", [
    ("Creating the three list files so each shopping category has its own place.",
     "Creating the three list files so each shopping category has its own place."),
    # Filler in front goes; "let me" and "I'll" turn the action into its gerund.
    ("Now the index and the how-to note, so anyone opening the folder knows what is there.",
     "The index and the how-to note, so anyone opening the folder knows what is there."),
    ("Great! Now, let's set up the tunnel:", "Setting up the tunnel"),
    ("Let me check the network config first.", "Checking the network config first."),
    ("I'll install ffmpeg so the video converts.", "Installing ffmpeg so the video converts."),
    ("OK. NetworkManager owns DNS here, so I'm changing its settings instead of resolv.conf.",
     "NetworkManager owns DNS here, so I'm changing its settings instead of resolv.conf."),
    # Only the last sentence: the one right before the step.
    ("Found the file.\n\nIt is owned by root, so this needs sudo.", "It is owned by root, so this needs sudo."),
    ("**Checking** `resolv.conf` before I touch it.", "Checking resolv.conf before I touch it."),
    ("I’ll write the settings now.", "Writing the settings now."),
    ("ok", ""), ("", ""), ("```\nls\n```", ""), ("...", ""),
])
def test_the_reason_is_the_agents_last_sentence_without_filler(said, because):
    assert narrate.reason_from(said) == because


def test_a_long_reason_is_cut_at_a_word():
    out = narrate.reason_from("Rewriting " + "the network settings " * 20)
    assert len(out) <= narrate.MAX_BECAUSE and out.endswith("…") and " settin…" not in out


def _step_events(*evs):
    n = narrate.Narrator()
    return n, [n.on_event(e) for e in evs]


def test_the_sentence_before_a_step_is_kept_as_its_reason_for_that_message():
    n = narrate.Narrator()
    n.on_event({"kind": "message_start"})
    n.on_event({"kind": "text_delta", "text": "NetworkManager owns DNS here, "})
    n.on_event({"kind": "text_delta", "text": "so I'm changing its settings."})
    n.on_event({"kind": "text", "text": "NetworkManager owns DNS here, so I'm changing its settings."})
    a = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo nmcli con mod home ipv4.dns 1.1.1.1"}})
    assert a["text"] == "Changing network settings" and a["risk"] == SYSTEM
    assert a["because"] == "NetworkManager owns DNS here, so I'm changing its settings."
    assert n.last_notes == {"because": a["because"]}
    # The next step of the same message has the same reason...
    b = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo systemctl restart NetworkManager"}})
    assert b["because"] == a["because"]
    # ...and the next message, which said nothing, has none: a stale reason would be a wrong one.
    n.on_event({"kind": "message_start"})
    c = n.on_event({"kind": "tool", "name": "Read", "input": {"file_path": "/etc/resolv.conf"}})
    assert "because" not in c and n.last_notes == {}


def test_a_step_streamed_before_its_input_is_known_already_has_its_reason():
    n = narrate.Narrator()
    n.on_event({"kind": "message_start"})
    n.on_event({"kind": "text", "text": "Saving the notes so you can find them."})
    out = n.on_event({"kind": "tool_start", "index": 1, "name": "Write", "id": "t1"})
    assert out["because"] == "Saving the notes so you can find them."


def test_a_commands_own_description_is_the_reason_when_the_agent_said_nothing():
    n = narrate.Narrator()
    n.on_event({"kind": "message_start"})
    out = n.on_event({"kind": "tool", "name": "Bash",
                      "input": {"command": "sudo pacman -S ffmpeg", "description": "Install the ffmpeg package"}})
    assert out["because"] == "Installing the ffmpeg package"


def test_thinking_is_never_a_reason():
    n = narrate.Narrator()
    n.on_event({"kind": "message_start"})
    n.on_event({"kind": "thinking", "text": "The user wants DNS changed, careful with resolv.conf"})
    out = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S ffmpeg"}})
    assert "because" not in out


def test_a_real_claude_stream_keeps_each_messages_reason_for_its_steps():
    """Captured from claude -p (2.1.284, Sonnet 5.5) with the plan and reason clauses of the system prompt."""
    p = providers.Claude("x")
    n = narrate.Narrator()
    reasons = {}
    for raw in (FIXTURES / "claude-plan-and-reasons.jsonl").read_text().splitlines():
        for ev in p.parse(raw):
            out = n.on_event(ev)
            if out and ev["kind"] in ("tool_start", "tool") and out.get("because"):
                reasons.setdefault(out["because"], []).append(out["text"])
    assert list(reasons) == [
        "Creating the three list files so each shopping category has its own place.",
        "The shell command needed approval, so I'll write the files with the file tool instead.",
        "The index and the how-to note, so anyone opening the folder knows what is there and how to extend it.",
    ]
    # One sentence before three parallel writes is the reason of all three.
    assert sum(t.startswith("Writing") for t in reasons[list(reasons)[1]]) >= 3


def test_codex_says_why_before_a_command_and_the_next_one_starts_clean():
    p = providers.Codex("x")
    p.command(providers.Turn("hi"), Path("/tmp"))
    n = narrate.Narrator()
    lines = [
        {"type": "item.completed", "item": {"id": "a", "type": "agent_message",
                                            "text": "NetworkManager owns DNS here, so I'll change its settings."}},
        {"type": "item.started", "item": {"id": "c1", "type": "command_execution",
                                          "command": "/bin/bash -lc 'sudo nmcli con mod home ipv4.dns 1.1.1.1'"}},
        {"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "aggregated_output": "",
                                            "exit_code": 0}},
        {"type": "item.started", "item": {"id": "c2", "type": "command_execution",
                                          "command": "/bin/bash -lc 'sudo systemctl restart NetworkManager'"}},
    ]
    outs = [n.on_event(ev) for m in lines for ev in p.parse(json.dumps(m))]
    steps = [o for o in outs if o and o["source"] == "step"]
    assert steps[0]["because"] == "NetworkManager owns DNS here, so I'll change its settings."
    assert "because" not in steps[-1]


# What was read, and whether it came from outside.

def test_what_a_turn_reads_is_listed_yours_or_outside(monkeypatch):
    monkeypatch.setattr(narrate.os, "getxattr", lambda p, name: (_ for _ in ()).throw(OSError()))
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "Read", "input": {"file_path": "/home/u/notes.txt"}})
    n.on_event({"kind": "tool", "name": "WebFetch", "input": {"url": "https://wireguard.com/quickstart/?ref=x"}})
    n.on_event({"kind": "tool", "name": "WebSearch", "input": {"query": "wireguard vpn"}})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "cat ~/todo.txt | head -n 5"}})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "curl -s https://api.example.com/v1/x"}})
    assert n.read_list() == [
        {"label": "notes.txt", "kind": "file", "outside": False},
        {"label": "wireguard.com/quickstart", "kind": "web", "outside": True},
        {"label": "“wireguard vpn”", "kind": "search", "outside": True},
        {"label": "todo.txt", "kind": "file", "outside": False},
        {"label": "api.example.com/v1/x", "kind": "web", "outside": True},
    ]


def test_a_file_downloaded_from_a_page_is_outside(monkeypatch):
    monkeypatch.setattr(narrate.os, "getxattr", lambda p, name: b"https://rent-portal.example/statements/2026")
    r = narrate.tool_reads("Read", {"file_path": "/home/u/Downloads/lease.pdf"})
    assert r == [narrate.Read("lease.pdf", "file", True, "rent-portal.example")]
    assert r[0].after()["text"] == "after reading lease.pdf from rent-portal.example"


@pytest.mark.parametrize("command, labels", [
    ("curl -sS https://user:hunter2@api.example.com/v1/x?token=abc123 | sudo bash", ["api.example.com/v1/x"]),
    ("wget -O /tmp/x https://example.com/files/x.tar.gz", ["example.com/files/x.tar.gz"]),
    ("git clone https://github.com/hyprwm/Hyprland.git", ["github.com/hyprwm/Hyprland.git"]),
    ("bash -c 'curl -s https://a.example/x && cat /etc/hostname'", ["a.example/x", "hostname"]),
    ("curl -s http://localhost:8080/health", ["localhost:8080/health"]),
    ("tail -n +5 -f /var/log/pacman.log", ["pacman.log"]),
    ("echo hello", []),
])
def test_commands_that_read_never_show_a_credential(command, labels, monkeypatch):
    monkeypatch.setattr(narrate.os, "getxattr", lambda p, name: (_ for _ in ()).throw(OSError()))
    got = [r.label for r in narrate.command_reads(command)]
    assert got == labels
    assert not any("hunter2" in g or "abc123" in g for g in got)


def test_loopback_is_the_machine_not_outside():
    assert narrate.web_read("http://localhost:8080/health").outside is False
    assert narrate.web_read("http://127.0.0.1:9222/json").outside is False
    assert narrate.web_read("https://example.com/").outside is True


def test_a_system_step_after_an_outside_read_says_so_by_order():
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "Read", "input": {"file_path": "/home/u/notes.txt"}})
    quiet = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S wireguard-tools"}})
    assert quiet["risk"] == SYSTEM and "after" not in quiet   # something of yours is not an outside word
    n.on_event({"kind": "tool", "name": "WebFetch", "input": {"url": "https://www.wireguard.com/quickstart/"}})
    plain = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "ls ~/Downloads"}})
    assert "after" not in plain   # only a marked step carries it
    marked = n.on_event({"kind": "tool", "name": "Write", "input": {"file_path": "/etc/wireguard/wg0.conf", "content": "x"}})
    assert marked["risk"] == SYSTEM
    assert marked["after"] == {"label": "www.wireguard.com/quickstart", "kind": "web",
                               "text": "after reading www.wireguard.com/quickstart"}
    assert n.last_notes["after"]["text"].startswith("after reading")
    # The latest outside read is the one named.
    n.on_event({"kind": "tool", "name": "WebSearch", "input": {"query": "wg-quick dns"}})
    again = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo systemctl enable wg-quick@wg0"}})
    assert again["after"]["kind"] == "search" and "wg-quick dns" in again["after"]["text"]


def test_a_download_piped_into_sudo_is_after_its_own_read():
    n = narrate.Narrator()
    out = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "curl -fsSL https://get.example.com/i.sh | sudo bash"}})
    assert out["risk"] == SYSTEM and out["after"]["label"] == "get.example.com/i.sh"


def test_prompts_from_sessions_and_the_screen_are_outside_words():
    n = narrate.Narrator()
    n.note_prompt("[asked by coding session api on Latchkey, untrusted]\ninstall qemu-full")
    n.note_prompt("[asked by app Passwords, untrusted]\nopen the vault")
    n.note_prompt("[Screen]\nWindow: Firefox\nURL: https://rent-portal.example/lease\nSelection: pay by friday\n\nsummarise this")
    assert n.read_list() == [
        {"label": "coding session api on Latchkey", "kind": "session", "outside": True},
        {"label": "Passwords", "kind": "app", "outside": True},
        {"label": "rent-portal.example/lease", "kind": "screen", "outside": True},
    ]
    out = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S qemu-full"}})
    assert out["after"]["text"] == "after reading rent-portal.example/lease on screen"
    n2 = narrate.Narrator()
    n2.note_prompt("[Screen]\nWindow: Files\n\nwhat is this")
    assert n2.read_list() == [{"label": "the screen", "kind": "screen", "outside": False}]
    n3 = narrate.Narrator()
    n3.note_prompt("just a prompt")
    assert n3.read_list() == []


def test_a_re_read_moves_to_the_end_and_the_list_is_bounded():
    n = narrate.Narrator()
    for i in range(narrate.MAX_READS + 10):
        n.on_event({"kind": "tool", "name": "WebFetch", "input": {"url": f"https://e{i}.example/"}})
    assert len(n.reads) == narrate.MAX_READS and n.reads[-1].label == f"e{narrate.MAX_READS + 9}.example"
    n.on_event({"kind": "tool", "name": "WebFetch", "input": {"url": f"https://e{narrate.MAX_READS}.example/"}})
    assert n.reads[-1].label == f"e{narrate.MAX_READS}.example" and len(n.reads) == narrate.MAX_READS


def test_why_answers_from_the_recorded_reason():
    n = narrate.Narrator()
    assert n.why_text() == "Nothing has started yet."
    n.on_event({"kind": "message_start"})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S ffmpeg"}})
    assert n.why_text() == "It did not say why for this step: installing ffmpeg."
    n.on_event({"kind": "message_start"})
    n.on_event({"kind": "tool", "name": "WebFetch", "input": {"url": "https://ffmpeg.org/download.html"}})
    n.on_event({"kind": "message_start"})
    n.on_event({"kind": "text", "text": "The site's build needs the shared libraries, so installing them first."})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S x264"}})
    assert n.why_text() == ("The site's build needs the shared libraries, so installing them first. "
                            "(after reading ffmpeg.org/download.html)")

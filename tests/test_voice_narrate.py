"""Changing how Bombadil talks is a plain line: no Undo, no summary entry, no Details row. And the apps a
turn made are remembered for "Since last time you built ..."."""

import json

import pytest

from bombadil import apps, narrate, paths

WORDS = "Changing how I talk to you"


def persona_path() -> str:
    return str(paths.persona_file())


def is_talk(step) -> bool:
    return step is not None and step.text == WORDS and step.done is None and step.risk is None \
        and step.command is None and not step.changes


@pytest.mark.parametrize("name, args", [
    ("Write", {"content": 'name = "Dan"\nvoice = "plain"\ngreet = true\n'}),
    ("Edit", {"old_string": "merry", "new_string": "plain"}),
    ("MultiEdit", {"edits": [{"old_string": "a", "new_string": "b"}]}),
])
@pytest.mark.parametrize("spelling", ["{p}", "~/config/persona.toml", "$HOME/config/persona.toml",
                                       "{p}/../persona.toml", "{home}/config//persona.toml"])
def test_an_edit_of_persona_toml_with_the_agents_file_tools_is_no_change(home, name, args, spelling):
    path = spelling.format(p=persona_path(), home=home)
    if spelling == "{p}/../persona.toml":
        path = str(paths.config_dir() / "x" / ".." / "persona.toml")
    step = narrate.tool_step(name, {"file_path": path, **args})
    assert is_talk(step)


def test_a_notebook_edit_and_the_default_location(home, monkeypatch):
    assert is_talk(narrate.tool_step("NotebookEdit", {"notebook_path": persona_path()}))
    monkeypatch.delenv("BOMBADIL_CONFIG")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert is_talk(narrate.tool_step("Edit", {"file_path": str(home / ".config" / "bombadil" / "persona.toml")}))
    assert is_talk(narrate.tool_step("Write", {"file_path": "~/.config/bombadil/persona.toml", "content": "x"}))


@pytest.mark.parametrize("path", [
    "persona.toml", "other/persona.toml", "{cfg}/persona.toml.bak", "{cfg}/config.toml", "{cfg}/persona.tomll",
    "{cfg}/sub/persona.toml", "{cfg}/../persona.toml", "", "/etc/persona.toml",
])
def test_nothing_else_is_mistaken_for_it(home, path):
    path = path.format(cfg=paths.config_dir())
    for name in ("Write", "Edit"):
        step = narrate.tool_step(name, {"file_path": path, "content": "x"})
        assert step is not None and step.text != WORDS and step.changes


def test_odd_paths_never_raise(home):
    for bad in (None, 5, ["a"], {"a": 1}, "a\0b", "~nobody/x", "$"):
        narrate.tool_step("Edit", {"file_path": bad})
        narrate.tool_step("Write", {"file_path": bad, "content": "x"})
        narrate.file_change_step([{"path": bad, "kind": "update"}])
        assert narrate._is_persona(bad) is False


def test_codex_file_changes(home):
    p = persona_path()
    assert is_talk(narrate.file_change_step([{"path": p, "kind": "update"}]))
    assert is_talk(narrate.file_change_step([{"path": p, "kind": "add"}]))
    # Two changes are two changes, unless both are this file.
    other = narrate.file_change_step([{"path": p, "kind": "update"}, {"path": "/home/d/a.py", "kind": "update"}])
    assert other.changes and other.text == "Editing 2 files"
    assert narrate.file_change_step([{"path": "/home/d/a.py", "kind": "update"}]).done == "Edited a.py"


def test_while_the_path_is_still_being_written(home):
    p = json.dumps(persona_path())
    assert is_talk(narrate.partial_step("Write", '{"file_path": ' + p + ', "content": "na'))
    assert is_talk(narrate.partial_step("Edit", '{"file_path": ' + p + ', "old_str'))
    assert is_talk(narrate.partial_step("MultiEdit", '{"file_path": ' + p))
    step = narrate.partial_step("Write", '{"file_path": "/home/d/a.py", "content": "x')
    assert step.text == "Writing a.py"


@pytest.mark.parametrize("command", [
    "cat > {p} <<EOF\nname = \"Dan\"\nvoice = \"plain\"\nEOF",
    "echo 'voice = \"plain\"' > {p}",
    "printf 'name = \"Dan\"\\n' >> {p}",
    "sed -i 's/merry/plain/' {p}",
    "sed -i -e 's/merry/plain/' {p}",
    "echo 'name = \"Dan\"' | tee {p}",
    "echo 'x' | tee -a {p}",
    "bash -lc \"sed -i 's/merry/plain/' {p}\"",
    "cd ~ && sed -i 's/merry/plain/' {p} && echo done",
    "sed -i 's/merry/plain/' {p} && cat {p}",
])
def test_a_simple_shell_edit_of_persona_toml_is_no_change(home, command):
    assert is_talk(narrate.shell_step(command.format(p=persona_path())))


@pytest.mark.parametrize("command", [
    # a relative path is the file in the folder a `cd` went to
    "cd {cfg} && sed -i 's/merry/plain/' persona.toml",
    "cd {cfg} && sed -i 's/merry/plain/' ./persona.toml",
    "cd {cfg} && echo 'voice = \"plain\"' > persona.toml",
    "cd {cfg} ; printf 'x' | tee persona.toml",
    "cd {cfg}; cat > persona.toml <<EOF\nvoice = \"plain\"\nEOF",
    "cd ~ && cd config && sed -i 's/merry/plain/' persona.toml",
    "cd /tmp && cd {cfg} && sed -i 's/merry/plain/' persona.toml",
    "cd {cfg}/sub && sed -i 's/merry/plain/' ../persona.toml",
    "cd {cfg} && sed -i -e 's/merry/plain/' persona.toml",
    "cd {cfg} && sed -ie 's/merry/plain/' persona.toml",
    # sed reads its script from -e or -f, which is not a file it edits
    "sed -i -e 's/a/b/' -e 's/c/d/' {p}",
    "sed -i --expression='s/a/b/' {p}",
    "sed -i -f /tmp/x.sed {p}",
    "sed -i.bak 's/a/b/' {p}",
    "sed --in-place 's/a/b/' {p}",
    "sed 's/a/b/' {p} > {p}.tmp && mv {p}.tmp {p}",
    # what goes to /dev/null is not a change
    "echo 'x' > {p} 2>/dev/null",
    "echo 'x' | tee {p} > /dev/null",
    "echo 'x' | tee {p} /dev/null",
    # a copy, a move and an install into it, and the atomic replace
    "cp /tmp/new.toml {p}",
    "cp -f /tmp/new.toml {p}",
    "mv /tmp/new.toml {p}",
    "mv -f /tmp/a.toml /tmp/b.toml {p}",
    "install /tmp/new.toml {p}",
    "install -m 644 /tmp/new.toml {p}",
    "echo 'x' > {p}.tmp && mv {p}.tmp {p}",
    "echo 'x' > {p}.4321.tmp && mv {p}.4321.tmp {p}",
    "cd {cfg} && printf 'x' > persona.toml.tmp && mv persona.toml.tmp persona.toml",
    "cat /tmp/new.toml > {p}.tmp && mv -f {p}.tmp {p} && cat {p}",
])
def test_the_same_file_by_another_route_is_no_change_either(home, command):
    assert is_talk(narrate.shell_step(command.format(p=persona_path(), cfg=paths.config_dir())))


@pytest.mark.parametrize("command, text, done", [
    # not the file: another folder, no folder known, another name
    ("cd /tmp && sed -i 's/a/b/' persona.toml", "Editing persona.toml", "Edited persona.toml"),
    ("cd {cfg}/.. && sed -i 's/a/b/' persona.toml", "Editing persona.toml", "Edited persona.toml"),
    ("sed -i 's/a/b/' persona.toml", "Editing persona.toml", "Edited persona.toml"),
    ("cd {cfg} && sed -i 's/a/b/' config.toml", "Editing config.toml", "Edited config.toml"),
    ("cd {cfg} && sed -i 's/a/b/' persona.toml.bak", "Editing persona.toml.bak", "Edited persona.toml.bak"),
    ("cd {cfg} && echo hi > notes.txt", "Writing notes.txt", "Wrote notes.txt"),
    ("echo x > /tmp/persona.toml.tmp && mv /tmp/persona.toml.tmp /tmp/persona.toml", "Writing persona.toml.tmp",
     "Wrote persona.toml.tmp"),
    ("cp {p} /tmp/persona.toml", "Copying persona.toml", "Copied persona.toml"),
    ("cp /tmp/new.toml {cfg}/config.toml", "Copying new.toml", "Copied new.toml"),
    ("mv {p} /tmp/old.toml", "Moving persona.toml", "Moved persona.toml"),
])
def test_a_relative_path_is_only_the_file_where_the_cd_went(home, command, text, done):
    step = narrate.shell_step(command.format(p=persona_path(), cfg=paths.config_dir()))
    assert (step.text, step.done) == (text, done)


@pytest.mark.parametrize("command, text, risk", [
    # persona.toml in the segment does not take its own flags, its command or its Undo away
    ("mkfs.ext4 /dev/sdb1 >{p}", "Formatting /dev/sdb1", narrate.IRREVERSIBLE),
    ("mkfs.ext4 /dev/sdb1 > persona.toml", "Formatting /dev/sdb1", narrate.IRREVERSIBLE),
    ("dd if=/dev/zero of=/dev/sda > {p}", "Writing to /dev/sda", narrate.IRREVERSIBLE),
    ("rm -rf ~/Documents > {p}", "Deleting Documents", narrate.IRREVERSIBLE),
    ("shred -u ~/notes.txt >> {p}", "Wiping notes.txt", narrate.IRREVERSIBLE),
    ("tee {p} /etc/fstab", "Writing fstab", narrate.SYSTEM),
    ("tee /etc/fstab {p}", "Writing fstab", narrate.SYSTEM),
    ("sed -i s/a/b/ {p} /etc/pacman.conf", "Editing pacman.conf", narrate.SYSTEM),
    ("sed -i -e s/a/b/ /etc/pacman.conf {p}", "Editing pacman.conf", narrate.SYSTEM),
    ("echo x > /etc/hosts > {p}", "Writing hosts", narrate.SYSTEM),
    ("sudo pacman -S ffmpeg > {p}", "Installing ffmpeg", narrate.SYSTEM),
    ("sudo tee {p}", "Writing persona.toml", narrate.SYSTEM),
    ("sudo sed -i s/a/b/ {p}", "Editing persona.toml", narrate.SYSTEM),
    ("sudo cp /tmp/new.toml {p}", "Copying new.toml", narrate.SYSTEM),
    ("echo x | sudo tee {p}", "Writing persona.toml", narrate.SYSTEM),
    ("mv /etc/fstab {p}", "Moving fstab", narrate.SYSTEM),
])
def test_a_persona_redirect_does_not_hide_the_segments_own_risk(home, command, text, risk):
    command = command.format(p=persona_path())
    step = narrate.shell_step(command)
    assert step.text == text and step.risk == risk and step.changes
    assert (step.command is not None) == (risk is not None)
    if risk:
        assert step.command == " ".join(command.split())


@pytest.mark.parametrize("command, text, touched", [
    # another file in the same segment is narrated as if persona.toml were not there
    ("sed -i 's/x/y/' {p} ~/notes.txt", "Editing notes.txt", ("~/notes.txt",)),
    ("sed -i 's/x/y/' ~/notes.txt {p}", "Editing notes.txt", ("~/notes.txt",)),
    ("echo x | tee {p} ~/notes.txt", "Writing notes.txt", ("~/notes.txt",)),
    ("echo x | tee ~/notes.txt {p}", "Writing notes.txt", ("~/notes.txt",)),
    ("echo x | tee -a ~/notes.txt {p} ~/more.txt", "Writing more.txt", ("~/notes.txt", "~/more.txt")),
    ("echo x > ~/notes.txt > {p}", "Writing notes.txt", ("~/notes.txt",)),
    ("echo x > {p} > ~/notes.txt", "Writing notes.txt", ("~/notes.txt",)),
    ("cat /tmp/a > {p} > ~/notes.txt", "Writing notes.txt", ("~/notes.txt",)),
])
def test_another_file_in_the_segment_keeps_its_own_entry_and_undo(home, command, text, touched):
    step = narrate.shell_step(command.format(p=persona_path()))
    assert step.text == text and step.changes and step.risk is None and step.command is None
    assert step.done == text.replace("Editing", "Edited").replace("Writing", "Wrote")
    assert step.touched == {"file": touched}


@pytest.mark.parametrize("command", [
    "python3 gen.py > {p}", "curl -s https://example.com/voice.toml > {p}", "jq . /tmp/x.json > {p}",
    "sort /tmp/x > {p}", "ln -sf /tmp/x {p}", "touch {p}", "rm {p}", "truncate -s 0 {p}", "chmod 600 {p}",
])
def test_only_echo_printf_cat_tee_sed_and_the_copies_are_called_talking(home, command):
    # Anything else that writes the file says what it is, as it always did.
    step = narrate.shell_step(command.format(p=persona_path()))
    assert step.text != WORDS


@pytest.mark.parametrize("command, text, done", [
    ("cat {p}", "Reading persona.toml", None),
    ("grep voice {p}", "Searching for “voice”", None),
    ("cp {p} /tmp/backup.toml", "Copying persona.toml", "Copied persona.toml"),
    ("echo hi > {cfg}/config.toml", "Writing config.toml", "Wrote config.toml"),
    ("sed -i 's/a/b/' {cfg}/config.toml", "Editing config.toml", "Edited config.toml"),
])
def test_other_commands_on_the_file_keep_their_words(home, command, text, done):
    step = narrate.shell_step(command.format(p=persona_path(), cfg=paths.config_dir()))
    assert (step.text, step.done) == (text, done)


def test_a_command_that_also_changes_the_system_keeps_its_change(home):
    command = f"sudo pacman -S --noconfirm ffmpeg && echo 'voice = \"plain\"' > {persona_path()}"
    step = narrate.shell_step(command)
    assert step.text == "Installing ffmpeg" and step.done == "Installed ffmpeg" and step.risk == narrate.SYSTEM
    # Changing how it talks is not the change, so a mixed command with something irreversible stays marked.
    step = narrate.shell_step(f"rm -rf ~/Documents && sed -i 's/a/b/' {persona_path()}")
    assert step.risk == narrate.IRREVERSIBLE and step.changes


def test_the_narrator_says_it_plainly_and_records_nothing_to_undo(home):
    n = narrate.Narrator()
    edit = {"kind": "tool", "name": "Edit", "input": {"file_path": persona_path()}}
    assert n.on_event(edit)["text"] == WORDS
    assert n.done == [] and n.summary() == "" and n.irreversible is False and n.system is False
    # Streaming: the line starts as a generic write and becomes the plain words once the path is known.
    m = narrate.Narrator()
    m.on_event({"kind": "tool_start", "index": 0, "name": "Write"})
    line = m.on_event({"kind": "tool_input", "index": 0, "partial": '{"file_path": ' + json.dumps(persona_path())})
    assert line["text"] == WORDS
    m.on_event({"kind": "tool", "name": "Write", "input": {"file_path": persona_path(), "content": "x"}})
    assert m.done == [] and m.summary() == ""
    c = narrate.Narrator()
    c.on_event({"kind": "file_change", "changes": [{"path": persona_path(), "kind": "update"}]})
    assert c.done == []


def test_a_cd_into_the_default_location(home, monkeypatch):
    monkeypatch.delenv("BOMBADIL_CONFIG")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for command in ("cd ~/.config/bombadil && sed -i 's/merry/plain/' persona.toml",
                    "cd $HOME/.config/bombadil && echo 'voice = \"plain\"' > persona.toml",
                    'cd "${HOME}/.config/bombadil" ; printf x | tee persona.toml',
                    "cd ~/.config && cd bombadil && sed -i 's/plain/quiet/' persona.toml"):
        assert is_talk(narrate.shell_step(command)), command
    assert narrate.shell_step("cd ~/.config && sed -i 's/a/b/' persona.toml").changes


def test_the_narrator_keeps_the_risk_and_the_receipt_of_a_command_that_also_writes_the_file(home):
    n = narrate.Narrator()
    line = n.on_event({"kind": "tool", "name": "Bash", "input": {"command": f"mkfs.ext4 /dev/sdb1 >{persona_path()}"}})
    assert line["text"] == "Formatting /dev/sdb1" and line["risk"] == narrate.IRREVERSIBLE and line["command"]
    assert n.irreversible is True and n.done == ["Formatted /dev/sdb1"]
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": f"echo x | tee {persona_path()} /etc/fstab"}})
    assert n.system is True and n.done == ["Wrote fstab"] and n.summary() == "Wrote fstab."
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": f"sed -i 's/a/b/' {persona_path()} ~/notes.txt"}})
    assert n.done == ["Edited notes.txt"] and n.touched == {"file": {str(home / "notes.txt")}}


def test_it_does_not_hide_the_turns_other_changes(home):
    n = narrate.Narrator()
    n.on_event({"kind": "tool", "name": "Edit", "input": {"file_path": persona_path()}})
    n.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S ffmpeg"}})
    assert n.done == ["Installed ffmpeg"] and n.summary() == "Installed ffmpeg."


# -- the apps a turn made --

def create(title, server="bombadil-os", **extra):
    return {"kind": "tool", "name": f"mcp__{server}__create_app", "input": {"title": title, "qml": "Item {}", **extra}}


def test_a_new_app_is_remembered_and_a_changed_one_is_not(home):
    apps.create("Tracker", "import QtQuick\nItem {}\n")
    n = narrate.Narrator()
    n.on_event(create("Passwords"))
    n.on_event(create("Tracker"))
    n.on_event(create("Passwords"))   # twice in one turn is still one app
    assert n.made == ["Passwords"]
    assert n.done == ["Made Passwords", "Changed Tracker"]   # the closing sentence says what it always did


def test_what_is_not_an_app_made_by_the_os_tool_is_not_remembered(home):
    n = narrate.Narrator()
    for ev in (create("Passwords", server="other"), create(""), create("  "),
               {"kind": "tool", "name": "create_app", "input": {"title": "X"}},
               {"kind": "tool", "name": "mcp__bombadil-os__open_app", "input": {"title": "X", "name": "x"}},
               {"kind": "tool", "name": "mcp__bombadil-os__create_app", "input": ["title"]},
               {"kind": "tool", "name": None, "input": None},
               {"kind": "tool_start", "index": 0, "name": "mcp__bombadil-os__create_app"}):
        n.on_event(ev)
    assert n.made == []


def test_an_app_name_is_cleaned_like_the_one_on_the_line(home):
    n = narrate.Narrator()
    n.on_event(create("  Passwords   and\nTracker  " + "x" * 80))
    assert n.made == [narrate._app_title("  Passwords   and\nTracker  " + "x" * 80)]
    assert len(n.made[0]) <= 40 and "\n" not in n.made[0]

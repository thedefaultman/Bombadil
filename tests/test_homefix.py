"""homefix: the steps that put a person's home right for the Bombadil that is installed (src/bombadil/homefix.py)."""

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bombadil import agentd, homefix, notices, paths

ROOT = Path(__file__).resolve().parents[1]
HYPR = ROOT / "share" / "hypr"
OLD = HYPR / "old"
SHIPPED = sorted(OLD.glob("*.lua")) + [HYPR / "bombadil.lua"]
STUB = homefix.STUB
LUA = ".config/hypr/hyprland.lua"
SKILLS = "/usr/share/bombadil/share/skills/"
PRE_SPLIT = OLD / "94afe47b.lua"   # the skel file as it was the last time it held everything
VM_MONITOR = ('hl.monitor({ output = "Virtual-1", mode = "1600x900@60", position = "0x0", scale = 1 })'
              " -- the VM window (scripts/wsl-vm.sh)")
SKEL_STUB = ROOT / "packaging" / "bombadil" / "root" / "etc" / "skel" / ".config" / "hypr" / "hyprland.lua"


def tree(root: Path) -> dict:
    """Everything under root, with the bytes of each file and the target of each link."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = ("link", os.readlink(p))
        elif p.is_dir():
            out[rel] = ("dir", stat.S_IMODE(p.stat().st_mode))
        else:
            out[rel] = ("file", p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
    return out


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_lines(text: str) -> list[str]:
    """The lines that are not comments or blank."""
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("--")]


def assert_whole(text: str) -> None:
    """Nothing in the file is left open or closed too often: a syntax error here would stop Bombadil's part too."""
    rows = homefix.scan(text.splitlines())
    assert rows and not any(r.bad for r in rows) and rows[-1].end == 0 and not rows[-1].open


def make_skel(root: Path) -> Path:
    (root / ".config/hypr").mkdir(parents=True)
    (root / LUA).write_text(STUB)
    (root / ".config/chromium-flags.conf").write_text("--no-first-run\n")
    (root / ".config/bombadil").mkdir()
    (root / ".config/bombadil/wallpaper").write_text("# the picture under the desk\n")
    for tool in (".claude", ".agents"):
        (root / tool / "skills").mkdir(parents=True)
        for name in homefix.SKILLS:
            (root / tool / "skills" / name).symlink_to(SKILLS + name)
    return root


class Person:
    """A fake home, a fake /etc/skel, and a state folder of their own: nothing here is the real machine's."""

    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.skel = make_skel(tmp_path / "skel")
        self.log: list[str] = []

    def run(self, **kw) -> homefix.Report:
        kw.setdefault("skel", self.skel)
        return homefix.run(home=self.home, share=ROOT, log=self.log.append, **kw)

    @property
    def lua(self) -> Path:
        return self.home / LUA

    @property
    def before(self) -> Path:
        return self.lua.with_name(homefix.BEFORE_SPLIT)

    def write_lua(self, text) -> None:
        self.lua.parent.mkdir(parents=True, exist_ok=True)
        self.lua.write_bytes(text if isinstance(text, bytes) else text.encode())

    def state(self) -> dict:
        return json.loads(paths.home_file().read_text())

    def twice(self) -> homefix.Report:
        """Run the steps, then again: the second run must change nothing, in the home or in home.json."""
        first = self.run()
        after = (tree(self.home), paths.home_file().read_bytes())
        self.run()
        assert (tree(self.home), paths.home_file().read_bytes()) == after
        return first


@pytest.fixture
def person(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.delenv("BOMBADIL_SHARE", raising=False)
    # No luac and no hyprctl unless a test puts one here; only the plain tools the fake ones call are let in.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("cat", "grep", "sleep"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    monkeypatch.setenv("PATH", str(bin_dir))
    return Person(tmp_path)


def fake_tool(tmp_path: Path, name: str, script: str) -> None:
    path = tmp_path / "bin" / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)


# -- step 1: the config split --

@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_a_home_equal_to_a_shipped_version_becomes_the_stub_alone(person, path):
    person.write_lua(path.read_bytes())
    report = person.twice()
    assert person.lua.read_text() == STUB
    assert person.before.read_bytes() == path.read_bytes()
    assert report.steps["1"] == "stub-only" and report.notes == []


def test_the_last_version_before_the_split_is_among_the_old_ones():
    assert PRE_SPLIT.is_file() and len(list(OLD.glob("*.lua"))) >= 33


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_the_vm_monitor_line_is_carried_after_the_stub_even_when_it_is_the_only_change(person, path):
    person.write_lua(path.read_text() + VM_MONITOR + "\n")
    person.twice()
    text = person.lua.read_text()
    assert text.startswith(STUB)
    assert code_lines(text[len(STUB):]) == [VM_MONITOR]
    assert_whole(text)


def test_a_block_of_several_lines_is_carried_whole(person):
    block = ['hl.window_rule({', '  name = "mine",', '  match = { class = "^(foo)$" },', '  float = true,', '})']
    bind = 'hl.bind("SUPER + X", hl.dsp.exec_cmd("foo"))'
    person.write_lua(PRE_SPLIT.read_text() + "\n".join(block) + "\n\n" + bind + "\n")
    report = person.twice()
    text = person.lua.read_text()
    assert text.startswith(STUB)
    assert code_lines(text[len(STUB):]) == block + [bind]
    assert "\n".join(block) + "\n\n" + bind + "\n" in text        # in order, the blank line between kept
    assert_whole(text)
    assert report.steps["1"] == "migrated" and report.notes == []
    assert person.state()["steps"]["1"]["kept"] == 2


def test_a_user_edit_inside_hl_config_is_commented_out_not_carried_as_a_fragment(person):
    edited = PRE_SPLIT.read_text().replace("gaps_in = 6", "gaps_in = 3").replace('kb_layout = "us"', 'kb_layout = "de"')
    person.write_lua(edited + 'hl.bind("SUPER + Y", hl.dsp.exec_cmd("bar"))\n')
    report = person.twice()
    text = person.lua.read_text()
    body = text[len(STUB):]
    assert code_lines(body) == ['hl.bind("SUPER + Y", hl.dsp.exec_cmd("bar"))']
    left = body[body.index(homefix.LEFT_OVER):]
    assert "-- gaps_in = 3," in left and '-- input = { kb_layout = "de", follow_mouse = 1 },' in left
    assert "gaps_in" not in body[:body.index(homefix.LEFT_OVER)]
    assert_whole(text)
    assert person.before.read_text() == edited + 'hl.bind("SUPER + Y", hl.dsp.exec_cmd("bar"))\n'
    assert len(report.notes) == 1 and homefix.BEFORE_SPLIT in report.notes[0] and "2 lines" in report.notes[0]
    assert person.state()["steps"]["1"]["left"] == 2


def test_a_comment_changed_inside_one_of_bombadils_statements_is_kept_and_not_made_a_fuss_of(person):
    text = PRE_SPLIT.read_text().replace("    -- One foot server for the coding sessions' windows, so a session's window rises at once.\n",
                                         "    -- my own words\n")
    assert text != PRE_SPLIT.read_text()
    person.write_lua(text)
    report = person.twice()
    written = person.lua.read_text()
    assert "-- my own words" in written[written.index(homefix.LEFT_OVER):] and code_lines(written[len(STUB):]) == []
    assert report.notes == [] and person.state()["steps"]["1"]["left"] == 0


def test_text_the_scanner_cannot_make_sense_of_does_not_crash_it():
    assert statements("caf\u00e9 \u00b2 hl.a()\n") == [(0, 1, False)]


def test_lines_the_person_deleted_come_back_with_bombadils_part_and_the_file_says_so(person):
    text = PRE_SPLIT.read_text().replace('    hl.exec_cmd("foot --server")\n', "")
    assert text != PRE_SPLIT.read_text()
    person.write_lua(text)
    person.twice()
    written = person.lua.read_text()
    assert written.startswith(STUB) and code_lines(written[len(STUB):]) == []
    assert "took out" in written and "comes from the system" in written


def test_a_file_made_of_two_versions_does_not_carry_what_an_older_version_shipped(person):
    old_bind = 'hl.bind("SUPER + SUPER_L", hl.dsp.exec_cmd("bombadil pill"), { release = true })'
    assert old_bind in (OLD / "041c3ba6.lua").read_text() and old_bind not in PRE_SPLIT.read_text()
    person.write_lua(PRE_SPLIT.read_text() + old_bind + "\n")
    person.twice()
    assert person.lua.read_text() == STUB


def test_blocks_and_locals_are_not_statements_to_carry(person):
    extra = 'local mod = "ALT"\nif os.getenv("X") then\n  hl.bind(mod .. " + A", hl.dsp.exec_cmd("a"))\nend\n'
    person.write_lua(PRE_SPLIT.read_text() + extra + 'hl.bind("SUPER + K", hl.dsp.exec_cmd("k"))\n')
    person.twice()
    body = person.lua.read_text()[len(STUB):]
    assert code_lines(body[:body.index(homefix.LEFT_OVER)]) == ['hl.bind("SUPER + K", hl.dsp.exec_cmd("k"))']
    for line in ('local mod = "ALT"', 'if os.getenv("X") then', "end"):
        assert f"-- {line}" in body
    assert_whole(person.lua.read_text())


def test_a_trailing_comma_is_a_fragment_and_not_carried(person):
    person.write_lua(PRE_SPLIT.read_text() + 'hl.bind("SUPER + K", hl.dsp.exec_cmd("k")),\n')
    person.run()
    assert code_lines(person.lua.read_text()[len(STUB):]) == []


def test_a_before_split_that_is_already_there_is_never_overwritten(person):
    person.before.parent.mkdir(parents=True)
    person.before.write_text("-- the first one\n")
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    person.twice()
    assert person.before.read_text() == "-- the first one\n"
    assert VM_MONITOR in person.lua.read_text()


def test_a_home_that_already_has_the_stub_is_untouched(person):
    own = STUB + 'hl.bind("SUPER + K", hl.dsp.exec_cmd("k"))\n'
    person.write_lua(own)
    os.utime(person.lua, ns=(1_000_000_000, 1_000_000_000))
    before = person.lua.stat()
    person.twice()
    after = person.lua.stat()
    assert person.lua.read_text() == own and not person.before.exists()
    assert (after.st_mtime_ns, after.st_ino) == (before.st_mtime_ns, before.st_ino)
    assert person.state()["steps"]["1"]["result"] == "already"


def test_a_missing_hyprland_lua_gets_the_stub(person):
    assert not person.lua.parent.exists()
    report = person.twice()
    assert person.lua.read_text() == STUB and not person.before.exists()
    assert report.steps["1"] == "stub-written"


def test_the_stub_comes_from_the_skel_when_it_has_one(person):
    person.skel.joinpath(LUA).write_text(STUB.replace("Your own lines go below.", "Yours go below.", 1))
    person.run()
    assert "Yours go below." in person.lua.read_text()


def test_the_file_keeps_its_mode_and_the_old_one_is_a_byte_for_byte_copy(person):
    raw = PRE_SPLIT.read_bytes() + b"-- caf\xc3\xa9 \xff\n" + VM_MONITOR.encode() + b"\r\n"
    person.write_lua(raw)
    person.lua.chmod(0o600)
    person.run()
    assert person.before.read_bytes() == raw
    assert stat.S_IMODE(person.lua.stat().st_mode) == 0o600
    assert stat.S_IMODE(person.before.stat().st_mode) == 0o600


def test_a_symlinked_hyprland_lua_is_replaced_by_a_file_and_what_it_pointed_at_is_left_alone(person, tmp_path):
    dotfiles = tmp_path / "dotfiles.lua"
    dotfiles.write_text(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    person.lua.parent.mkdir(parents=True)
    person.lua.symlink_to(dotfiles)
    person.twice()
    assert not person.lua.is_symlink() and VM_MONITOR in person.lua.read_text()
    assert dotfiles.read_text() == PRE_SPLIT.read_text() + VM_MONITOR + "\n"
    assert person.before.read_text() == dotfiles.read_text()


def test_a_dangling_symlink_is_left_alone_and_the_step_fails(person):
    person.lua.parent.mkdir(parents=True)
    person.lua.symlink_to(person.home / "nowhere.lua")
    report = person.run()
    assert person.lua.is_symlink() and "1" not in report.steps
    assert any("step 1" in line for line in person.log)
    assert person.state()["failed"]["1"]["attempts"] == 1


def test_a_file_that_is_not_bombadils_to_compare_fails_instead_of_guessing(person, tmp_path):
    share = tmp_path / "share"
    (share / "share" / "hypr").mkdir(parents=True)          # no bombadil.lua in it
    person.write_lua(PRE_SPLIT.read_bytes())
    homefix.run(home=person.home, share=share, log=person.log.append, skel=person.skel)
    assert person.lua.read_bytes() == PRE_SPLIT.read_bytes() and not person.before.exists()
    assert any("step 1" in line for line in person.log)


def test_a_luac_that_rejects_the_file_sends_what_would_be_carried_to_the_comments(person, tmp_path):
    fake_tool(tmp_path, "luac", 'grep -q SYNTAXERR && exit 1\nexit 0\n')
    person.write_lua(PRE_SPLIT.read_text() + 'hl.bind("SUPER + K", hl.dsp.exec_cmd("k")) SYNTAXERR\n')
    report = person.run()
    body = person.lua.read_text()[len(STUB):]
    assert code_lines(body) == []
    assert '-- hl.bind("SUPER + K", hl.dsp.exec_cmd("k")) SYNTAXERR' in body
    assert len(report.notes) == 1


def test_a_luac_that_accepts_the_file_changes_nothing(person, tmp_path):
    fake_tool(tmp_path, "luac", "cat > /dev/null\nexit 0\n")
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    person.run()
    assert code_lines(person.lua.read_text()[len(STUB):]) == [VM_MONITOR]


def test_hyprland_is_asked_to_reload_once_after_the_file_changed(person, tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    fake_tool(tmp_path, "hyprctl", f'echo "$@" >> {calls}\n')
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    person.run()
    assert calls.read_text() == "reload\n"
    person.run()
    assert calls.read_text() == "reload\n"                  # nothing changed the second time


def test_no_reload_without_a_session_or_without_hyprctl(person, tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    fake_tool(tmp_path, "hyprctl", f'echo "$@" >> {calls}\n')
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    person.run()                                            # a hyprctl, but no session to talk to
    assert not calls.exists()
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    (tmp_path / "bin" / "hyprctl").unlink()
    person.lua.unlink()
    person.before.unlink()
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    report = person.run()                                   # a session, but no hyprctl
    assert report.steps["1"] == "migrated" and not person.log


def test_a_reload_that_hangs_is_given_up_on_and_does_not_fail_the_step(person, tmp_path, monkeypatch):
    fake_tool(tmp_path, "hyprctl", "sleep 20\n")
    monkeypatch.setattr(homefix, "RELOAD_TIMEOUT", 0.3)
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    began = time.monotonic()
    report = person.run()
    assert time.monotonic() - began < 10
    assert report.steps["1"] == "migrated" and VM_MONITOR in person.lua.read_text()


# -- Lua, as far as telling statements apart goes --

def statements(text: str):
    return homefix.statements(homefix.scan(text.splitlines()))


def test_a_function_block_inside_a_call_is_one_statement():
    assert statements('hl.on("x", function()\n  hl.exec_cmd("a")\nend)\nhl.config({})\n') == [
        (0, 3, False), (3, 4, False)]


def test_brackets_and_dashes_inside_strings_and_comments_are_not_nesting():
    text = 'hl.exec_cmd("echo ) -- ((")  -- a comment (((\nhl.exec_cmd(\'}\')\n-- ]] ))\n'
    assert statements(text) == [(0, 1, False), (1, 2, False), (2, 3, False)]


def test_long_strings_and_long_comments_run_over_lines():
    text = 'x = [[ (\n) ]]\n--[==[ one\n ]] still\n]==] hl.a()\nhl.b()\n'
    assert statements(text) == [(0, 2, False), (2, 5, False), (5, 6, False)]


def test_a_stray_closer_or_an_open_string_or_an_unclosed_block_is_not_whole():
    assert statements("})\n") == [(0, 1, True)]
    assert statements('hl.a("oops)\n') == [(0, 1, True)]
    assert statements("hl.a({\n") == [(0, 1, True)]
    assert statements("if x then\n") == [(0, 1, True)]


def test_only_comments_blanks_and_whole_hl_calls_are_fit_to_carry():
    def fit(line):
        rows = homefix.scan([line])
        return homefix._carryable(rows, 0, 1, rows[0].bad)
    assert fit("") and fit("-- a comment") and fit('hl.bind("a", hl.dsp.exec_cmd("b")) -- ok')
    assert not fit("gaps_in = 3,") and not fit('local x = "y"') and not fit("end") and not fit('hl.bind("a", b),')
    assert not fit("})") and not fit('hl.bind("a')


# -- step 2: the skel entries --

def test_missing_skel_entries_are_added_once(person):
    report = person.twice()
    for tool in (".claude", ".agents"):
        for name in homefix.SKILLS:
            link = person.home / tool / "skills" / name
            assert link.is_symlink() and os.readlink(link) == SKILLS + name
    assert (person.home / ".config/chromium-flags.conf").read_text() == "--no-first-run\n"
    assert (person.home / ".config/bombadil/wallpaper").is_file()
    assert report.steps["2"] == "added"
    seen = person.state()["skel_seen"]
    assert ".claude/skills/bombadil-mail" in seen and ".config/chromium-flags.conf" in seen and LUA not in seen
    # What the person deletes is not brought back, and a new run does not offer it again.
    (person.home / ".config/chromium-flags.conf").unlink()
    (person.home / ".claude/skills/bombadil-mail").unlink()
    person.run()
    assert not (person.home / ".config/chromium-flags.conf").exists()
    assert not (person.home / ".claude/skills/bombadil-mail").exists()


def test_a_later_release_adds_its_new_entries_without_a_code_change(person):
    person.run()
    (person.skel / ".config/new-thing.conf").write_text("new\n")
    (person.skel / ".claude/skills/bombadil-newer").symlink_to(SKILLS + "bombadil-newer")
    report = person.twice()
    assert (person.home / ".config/new-thing.conf").read_text() == "new\n"
    assert os.readlink(person.home / ".claude/skills/bombadil-newer") == SKILLS + "bombadil-newer"
    assert report.steps["2"] == "added"
    assert person.state()["steps"]["2"]["added"] == [".claude/skills/bombadil-newer", ".config/new-thing.conf"]


def test_what_is_already_there_is_never_opened_or_changed(person):
    (person.home / ".config").mkdir()
    (person.home / ".config/chromium-flags.conf").write_text("--mine\n")
    (person.home / ".claude/skills").mkdir(parents=True)
    (person.home / ".claude/skills/bombadil-apps").symlink_to(person.home / "my-skill")
    (person.home / ".config/bombadil").mkdir()
    (person.home / ".config/bombadil/wallpaper").write_text("/home/me/pic.jpg\n")
    person.run()
    assert (person.home / ".config/chromium-flags.conf").read_text() == "--mine\n"
    assert os.readlink(person.home / ".claude/skills/bombadil-apps") == str(person.home / "my-skill")
    assert (person.home / ".config/bombadil/wallpaper").read_text() == "/home/me/pic.jpg\n"
    assert ".config/chromium-flags.conf" in person.state()["skel_seen"]


def test_sign_in_and_the_persons_settings_are_byte_identical_and_never_offered(person):
    have = {
        ".claude.json": b'{"oauthAccount": {"emailAddress": "x"}}',
        ".claude/.credentials.json": b'{"claudeAiOauth": {"accessToken": "t"}}',
        ".claude/settings.json": b'{"model": "opus"}',
        ".claude/projects/p/s.jsonl": b"{}\n",
        ".codex/auth.json": b'{"OPENAI_API_KEY": "k"}',
        ".config/bombadil/persona.toml": b'name = "Dan"\n',
        ".config/bombadil/desk.toml": b"[desk]\n",
    }
    for rel, data in have.items():
        (person.home / rel).parent.mkdir(parents=True, exist_ok=True)
        (person.home / rel).write_bytes(data)
    # A skel that, in some later release, carries more than the skill links would not get it into these.
    for rel in (".claude.json", ".claude/CLAUDE.md", ".claude/settings.json", ".codex/config.toml", ".codex/auth.json",
                ".config/bombadil/persona.toml", ".config/bombadil/other.toml"):
        (person.skel / rel).parent.mkdir(parents=True, exist_ok=True)
        (person.skel / rel).write_text("from skel\n")
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    hashes = {rel: digest(person.home / rel) for rel in have}
    person.twice()
    assert {rel: digest(person.home / rel) for rel in have} == hashes
    for rel in (".claude/CLAUDE.md", ".codex/config.toml", ".config/bombadil/other.toml"):
        assert not (person.home / rel).exists(), rel
    assert (person.home / ".claude/skills/bombadil-apps").is_symlink()


def built_in_entries_are_there(home: Path) -> None:
    for tool in (".claude", ".agents"):
        for name in homefix.SKILLS:
            assert os.readlink(home / tool / "skills" / name) == str(ROOT / "share" / "skills" / name)
    assert "--no-first-run" in (home / ".config/chromium-flags.conf").read_text()
    assert (home / ".config/bombadil/wallpaper").read_text().startswith("# The picture under the desk")


def test_the_built_in_list_is_used_for_what_the_skel_lacks(person, tmp_path):
    person.run(skel=tmp_path / "no-skel")
    built_in_entries_are_there(person.home)
    # A skel with other people's files in it and none of ours gets the same.
    stock = tmp_path / "stock-skel"
    stock.mkdir()
    (stock / ".bashrc").write_text("# stock\n")
    for rel in (".claude", ".agents", ".config"):
        shutil.rmtree(person.home / rel)
    paths.home_file().unlink()
    person.run(skel=stock)
    built_in_entries_are_there(person.home)
    # Other packages' dotfiles are not Bombadil's to bring back: a home that lacks one on purpose keeps it gone.
    assert not (person.home / ".bashrc").exists()


def test_the_built_in_texts_are_the_ones_the_package_ships():
    skel = ROOT / "packaging" / "bombadil" / "root" / "etc" / "skel"
    if not skel.is_dir():
        pytest.skip("no packaging tree here")
    assert STUB == SKEL_STUB.read_text()
    for rel, text in homefix.FALLBACK_FILES.items():
        assert (skel / rel).read_text() == text, rel
    for tool in (".claude", ".agents"):
        for name in homefix.SKILLS:
            assert os.readlink(skel / tool / "skills" / name) == SKILLS + name


def test_the_stub_carries_the_same_last_resort_start_hook_as_bombadils_part():
    hook = "systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell"
    assert hook in STUB and hook in (HYPR / "bombadil.lua").read_text()
    assert homefix.LOAD_LINE in STUB
    assert STUB.endswith("-- Your own lines go below. They run after Bombadil's, so a monitor or a bind here wins.\n")


# -- the state file --

def test_the_state_file_records_each_step_and_is_not_rewritten_when_nothing_changes(person):
    person.write_lua(PRE_SPLIT.read_bytes())
    person.run()
    doc = person.state()
    assert doc["version"] == 1 and set(doc) == {"version", "steps", "skel_seen", "failed"}
    assert doc["steps"]["1"]["result"] == "stub-only" and doc["steps"]["2"]["result"] == "added"
    assert doc["failed"] == {} and "at" in doc["steps"]["1"] and "bombadil" in doc["steps"]["1"]
    before = paths.home_file().stat()
    time.sleep(0.01)
    person.run()
    assert (paths.home_file().stat().st_mtime_ns, paths.home_file().stat().st_ino) == (before.st_mtime_ns, before.st_ino)


def test_a_lost_state_file_makes_the_steps_run_again_harmlessly(person):
    person.write_lua(PRE_SPLIT.read_text() + VM_MONITOR + "\n")
    person.run()
    after = tree(person.home)
    paths.home_file().unlink()
    (person.home / ".config/chromium-flags.conf").write_text("--mine\n")      # the person's own, later
    person.run()
    after[".config/chromium-flags.conf"] = ("file", b"--mine\n", after[".config/chromium-flags.conf"][2])
    assert tree(person.home) == after
    assert person.state()["steps"]["1"]["result"] == "already"


@pytest.mark.parametrize("content", [
    "not json {", "[]", '{"version": 2, "steps": {}, "failed": {}, "skel_seen": []}',
    '{"version": 1, "steps": [], "failed": {}, "skel_seen": []}',
    '{"version": 1, "steps": {}, "failed": {}, "skel_seen": [1, 2]}', "",
])
def test_a_state_file_that_cannot_be_believed_is_set_aside_and_the_table_starts_empty(person, content):
    paths.home_file().parent.mkdir(parents=True)
    paths.home_file().write_text(content)
    person.write_lua(PRE_SPLIT.read_bytes())
    person.run()
    aside = list(paths.home_file().parent.glob("home.json.bad-*"))
    assert len(aside) == 1 and aside[0].read_text() == content
    assert person.state()["steps"]["1"]["result"] == "stub-only" and person.lua.read_text() == STUB


def test_a_failure_record_that_is_nonsense_is_forgotten_and_nothing_else_is(person):
    paths.home_file().parent.mkdir(parents=True)
    paths.home_file().write_text(json.dumps({"version": 1, "steps": {}, "skel_seen": ["x"],
                                             "failed": {"1": "oops", "2": {"attempts": "many"}}}))
    person.run()
    assert not list(paths.home_file().parent.glob("home.json.bad-*"))
    assert person.state()["failed"] == {} and "x" in person.state()["skel_seen"] and person.lua.read_text() == STUB


def test_a_state_path_that_is_a_directory_is_set_aside_too(person):
    paths.home_file().mkdir(parents=True)
    person.run()
    assert person.state()["version"] == 1 and list(paths.home_file().parent.glob("home.json.bad-*"))


def test_run_never_raises_and_says_why(person, tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "a-file"))
    (tmp_path / "a-file").write_text("not a folder")
    report = person.run()
    assert report.steps == {} and person.log
    # Nor with no log given: it goes to stderr.
    assert homefix.run(home=person.home, share=ROOT, skel=person.skel).steps == {}


def test_a_step_that_keeps_failing_is_tried_three_times_and_then_left_alone(person):
    person.lua.mkdir(parents=True)                           # hyprland.lua is a folder: it cannot be read
    for _ in range(5):
        report = person.run()
        assert report.steps == {"2": "added"} or report.steps == {"2": "seen"}   # step 2 does not wait on step 1
    failures = [line for line in person.log if line.startswith("step 1")]
    assert len(failures) == 3 and "attempt 3 of 3" in failures[-1]
    assert person.state()["failed"]["1"]["attempts"] == 3


def test_a_step_that_works_again_forgets_its_failures(person):
    person.lua.mkdir(parents=True)
    person.run()
    assert person.state()["failed"]["1"]["attempts"] == 1
    person.lua.rmdir()
    person.run()
    assert person.state()["failed"] == {} and person.lua.read_text() == STUB


def test_a_second_homefix_holding_the_lock_means_this_one_does_nothing(person):
    paths.home_file().parent.mkdir(parents=True)
    with open(paths.home_file().with_name("home.json.lock"), "w") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        report = person.run()
    assert report.steps == {} and not person.lua.exists() and any("another homefix" in line for line in person.log)
    assert person.run().steps["1"] == "stub-written"          # and when the lock is free it goes on


# -- when it runs --

@pytest.fixture
def installed(tmp_path, monkeypatch):
    share = tmp_path / "usr-share-bombadil"
    share.mkdir()
    monkeypatch.setattr(homefix, "INSTALLED_SHARE", share)
    monkeypatch.setattr(paths, "share_dir", lambda: share)
    monkeypatch.delenv("BOMBADIL_SHARE", raising=False)
    monkeypatch.delenv("BOMBADIL_NO_HOMEFIX", raising=False)
    return share


def test_it_runs_only_on_an_installed_system(installed, tmp_path, monkeypatch):
    assert not homefix.should_run()                                      # no VERSION: not the package
    (installed / "VERSION").write_text("2026.10.06 (d9dde3b)\n")
    assert homefix.should_run()
    monkeypatch.setenv("BOMBADIL_NO_HOMEFIX", "1")                       # the kill switch
    assert not homefix.should_run()
    monkeypatch.delenv("BOMBADIL_NO_HOMEFIX")
    monkeypatch.setenv("BOMBADIL_SHARE", str(installed))                 # a developer's tree, even this path
    assert not homefix.should_run()
    monkeypatch.delenv("BOMBADIL_SHARE")
    monkeypatch.setattr(paths, "share_dir", lambda: tmp_path / "checkout")
    (tmp_path / "checkout").mkdir()
    (tmp_path / "checkout" / "VERSION").write_text("x\n")
    assert not homefix.should_run()


def test_the_installed_tree_is_where_the_package_puts_it(monkeypatch):
    monkeypatch.delenv("BOMBADIL_SHARE", raising=False)
    assert homefix.INSTALLED_SHARE == Path("/usr/share/bombadil") == paths.share_dir()
    assert homefix.SKEL == Path("/etc/skel")


class _Stop(Exception):
    pass


@pytest.fixture
def started(monkeypatch):
    """agentd.main() with the daemon replaced by one that only says it was built: what ran, in what order."""
    order: list = []
    made: list = []

    class Daemon:
        def __init__(self, *a, **k):
            order.append("AgentD built")
            self.socket_path = Path("/nonexistent/agentd.sock")
            self.clients = {}
            self.notices = notices.Notices()
            made.append(self)

        async def serve(self):
            order.append("socket opened")

        def end_signin_now(self):
            pass

    monkeypatch.setenv("BOMBADIL_PROVIDER", "fake")
    monkeypatch.setattr(agentd, "AgentD", Daemon)
    monkeypatch.setattr(agentd.sysmap, "warm_unit_names", lambda: None)
    return order, made


def test_agentd_fixes_the_home_before_it_builds_the_daemon_or_opens_the_socket(home, started, monkeypatch):
    order, _ = started
    monkeypatch.setattr(homefix, "should_run", lambda: True)
    monkeypatch.setattr(homefix, "run", lambda: order.append("homefix") or homefix.Report())
    assert agentd.main([]) == 0
    assert order == ["homefix", "AgentD built", "socket opened"]


def test_agentd_does_not_touch_a_home_when_it_is_not_an_installed_system(home, started, monkeypatch):
    order, _ = started
    monkeypatch.setattr(homefix, "should_run", lambda: False)
    monkeypatch.setattr(homefix, "run", lambda: order.append("homefix"))
    assert agentd.main([]) == 0
    assert order == ["AgentD built", "socket opened"]


def test_on_this_machine_agentd_does_not_run_homefix_without_the_package(home, started, monkeypatch):
    if (homefix.INSTALLED_SHARE / "VERSION").exists():
        pytest.skip("this machine has the package")
    order, _ = started
    monkeypatch.setattr(homefix, "run", lambda: order.append("homefix"))
    assert agentd.main([]) == 0
    assert "homefix" not in order


def test_a_homefix_that_raises_does_not_stop_agentd_and_says_why(home, started, monkeypatch, capsys):
    order, _ = started
    monkeypatch.setattr(homefix, "should_run", lambda: True)

    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(homefix, "run", boom)
    assert agentd.main([]) == 0
    assert order == ["AgentD built", "socket opened"]
    assert "agentd: homefix: RuntimeError: boom" in capsys.readouterr().err


def test_what_homefix_could_not_keep_is_told_once_above_the_pill(home, started, monkeypatch):
    _, made = started
    monkeypatch.setattr(homefix, "should_run", lambda: True)
    monkeypatch.setattr(homefix, "run", lambda: homefix.Report(notes=["I could not carry over 2 lines."]))
    assert agentd.main([]) == 0
    live = made[0].notices.live()
    assert [n["line"] for n in live] == ["I could not carry over 2 lines."] and live[0]["source"] == "home"


# -- the old texts --

def test_every_earlier_text_of_bombadils_part_is_kept_in_old():
    """homefix tells the person's lines from Bombadil's with these. When this fails, run
    scripts/hypr-old-versions.py and commit what it writes with the change to share/hypr/bombadil.lua."""
    script = ROOT / "scripts" / "hypr-old-versions.py"
    done = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True, check=False)
    if done.returncode == 2:
        pytest.skip("no git history here")
    assert done.returncode == 0, done.stdout + done.stderr


def test_each_old_file_is_named_for_its_git_blob():
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git here")
    for path in sorted(OLD.glob("*.lua")):
        blob = subprocess.run([git, "hash-object", str(path)], capture_output=True, text=True, check=False).stdout.strip()
        assert blob.startswith(path.stem) and len(path.stem) == 8, path.name


def test_the_script_asks_for_the_previous_text_when_bombadils_part_changes(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git here")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "share/hypr").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "hypr-old-versions.py", repo / "scripts")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

    def sh(*cmd):
        return subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, check=False)

    def script(*args):
        return sh(sys.executable, "scripts/hypr-old-versions.py", *args)

    def commit(text):
        (repo / "share/hypr/bombadil.lua").write_text(text)
        assert sh(git, "add", "-A").returncode == 0 and sh(git, "commit", "-q", "-m", "x").returncode == 0

    assert script("--check").returncode == 2                          # no history yet: nothing to read
    sh(git, "init", "-q")
    commit("-- one\n")
    assert script("--check").returncode == 0                          # the committed text is the current one
    (repo / "share/hypr/bombadil.lua").write_text("-- two\n")         # the change being made
    done = script("--check")
    assert done.returncode == 1 and "share/hypr/old/" in done.stdout and "hypr-old-versions.py" in done.stderr
    assert script().returncode == 0
    old = list((repo / "share/hypr/old").glob("*.lua"))
    assert [p.read_text() for p in old] == ["-- one\n"]               # the previous text, not the one being written
    assert script("--check").returncode == 0
    commit("-- two\n")
    assert script("--check").returncode == 0
    (repo / "share/hypr/bombadil.lua").write_text("-- three\n")
    assert script("--check").returncode == 1                          # now "two" is the one to keep
    script()
    assert sorted(p.read_text() for p in (repo / "share/hypr/old").glob("*.lua")) == ["-- one\n", "-- two\n"]

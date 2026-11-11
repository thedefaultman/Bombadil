"""Which Bombadil this is: VERSION and install.json read by version.py, and the three places that say it
(`bombadil version`, the pill's word, agentd's status). No model, no daemon, no pacman."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bombadil import agentd, apps, launcher, paths, providers, version

ROOT = Path(__file__).resolve().parents[1]
LINE = "Bombadil 2026.10.06 (d9dde3b), installed 2 Oct"


class System:
    def __init__(self, tmp_path: Path):
        self.share = tmp_path / "share"
        self.share.mkdir()
        self.record = tmp_path / "install.json"

    def version(self, text: str) -> None:
        (self.share / "VERSION").write_text(text)

    def install(self, **fields) -> None:
        self.record.write_text(json.dumps(fields))


@pytest.fixture
def system(tmp_path, monkeypatch):
    s = System(tmp_path)
    monkeypatch.setenv("BOMBADIL_SHARE", str(s.share))
    monkeypatch.setenv("BOMBADIL_INSTALL_RECORD", str(s.record))
    s.version("2026.10.06 (d9dde3b)\n")
    s.install(mode="install", date="2026-10-02T09:14:00+0000", first_installed="2026-10-02T09:14:00+0000")
    return s


# -- the line --

def test_the_line_names_the_version_the_commit_and_the_day_it_was_installed(system):
    assert version.version_line() == LINE


def test_a_refresh_is_named_when_it_was_another_day(system):
    system.install(mode="refresh", date="2026-10-20T18:00:00+0000", first_installed="2026-10-02T09:14:00+0000")
    assert version.version_line() == "Bombadil 2026.10.06 (d9dde3b), installed 2 Oct, refreshed 20 Oct"


def test_a_refresh_on_the_day_of_the_install_is_not_named(system):
    system.install(mode="refresh", date="2026-10-02T20:00:00+0000", first_installed="2026-10-02T09:14:00+0000")
    assert version.version_line() == LINE


def test_a_later_date_with_no_refresh_in_the_record_is_not_called_one(system):
    system.install(mode="install", date="2026-10-20T18:00:00+0000", first_installed="2026-10-02T09:14:00+0000")
    assert version.version_line() == LINE


def test_a_record_from_before_first_installed_gives_its_own_date(system):
    system.install(mode="install", date="2026-10-02T09:14:00+0000")
    assert version.version_line() == LINE


def test_a_refreshed_old_record_says_installed_by_the_only_date_it_has(system):
    system.install(mode="refresh", date="2026-10-20T18:00:00+0000")
    assert version.version_line() == "Bombadil 2026.10.06 (d9dde3b), installed 20 Oct"


def test_the_day_is_the_one_written_down_not_the_one_in_this_machines_zone(system):
    system.install(mode="install", date="2026-10-02T23:30:00-0700", first_installed="2026-10-02T23:30:00-0700")
    assert version.version_line().endswith("installed 2 Oct")


def test_months_do_not_depend_on_the_locale(system):
    for n, name in enumerate(version.MONTHS, 1):
        system.install(mode="install", date=f"2026-{n:02d}-09T09:00:00+0000")
        assert version.version_line().endswith(f"installed 9 {name}")


def test_the_version_moves_with_the_file_and_the_install_date_stays(system):
    system.version("2026.11.12 (abc1234)\n")
    assert version.version_line() == "Bombadil 2026.11.12 (abc1234), installed 2 Oct"


def test_with_no_install_record_it_still_says_which_version(system):
    system.record.unlink()
    assert version.version_line() == "Bombadil 2026.10.06 (d9dde3b)"


def test_a_version_line_with_no_commit(system):
    system.version("2026.10.06\n")
    assert version.version_line() == "Bombadil 2026.10.06, installed 2 Oct"


def test_with_no_version_file_it_says_so(system):
    (system.share / "VERSION").unlink()
    assert version.version_line() == "Bombadil (version unknown)"
    assert version.read() == ""


@pytest.mark.parametrize("text", ["", "\n", "  \n\n", "()\n", "( )"])
def test_a_version_file_with_nothing_in_it_says_so(system, text):
    system.version(text)
    assert version.version_line() == "Bombadil (version unknown)"


@pytest.mark.parametrize("content", ["not json {", "[]", "[1, 2]", '"x"', "null", "",
                                     '{"date": 5, "first_installed": ["2026"], "mode": 3}',
                                     '{"date": "yesterday", "mode": "refresh"}'])
def test_a_record_that_cannot_be_read_costs_the_install_day_and_nothing_else(system, content):
    system.record.write_text(content)
    assert version.version_line() == "Bombadil 2026.10.06 (d9dde3b)"


def test_a_record_that_is_a_directory_or_unreadable_costs_the_install_day_and_nothing_else(system):
    system.record.unlink()
    system.record.mkdir()
    assert version.version_line() == "Bombadil 2026.10.06 (d9dde3b)"


def test_a_version_file_that_is_not_text_does_not_raise(system):
    (system.share / "VERSION").write_bytes(b"2026.10.06 (d9\xffde3b)\n")
    assert version.version_line().startswith("Bombadil 2026.10.06")


def test_the_line_never_raises(system, monkeypatch):
    monkeypatch.setattr(version, "facts", lambda: 1 / 0)
    assert version.version_line() == "Bombadil (version unknown)"


def test_the_line_does_not_ask_pacman_or_run_anything(system, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("ran a command")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "system", refuse)
    assert version.version_line() == LINE and version.facts()["commit"] == "d9dde3b"


def test_facts_gives_every_piece(system):
    system.install(mode="refresh", date="2026-10-20T18:00:00+0000", first_installed="2026-10-02T09:14:00+0000")
    assert version.facts() == {"version": "2026.10.06 (d9dde3b)", "number": "2026.10.06", "commit": "d9dde3b",
                               "installed": "2026-10-02T09:14:00+0000", "refreshed": "2026-10-20T18:00:00+0000",
                               "mode": "refresh"}


def test_facts_says_none_for_what_no_file_says(system):
    (system.share / "VERSION").unlink()
    system.record.unlink()
    assert version.facts() == {"version": None, "number": None, "commit": None, "installed": None,
                               "refreshed": None, "mode": None}


# -- the paths --

def test_the_files_are_where_the_package_and_the_installer_put_them(monkeypatch, tmp_path):
    monkeypatch.delenv("BOMBADIL_SHARE", raising=False)
    monkeypatch.delenv("BOMBADIL_INSTALL_RECORD", raising=False)
    assert paths.version_file() == Path("/usr/share/bombadil/VERSION")
    assert paths.install_record() == Path("/var/lib/bombadil/install.json")
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "s"))
    monkeypatch.setenv("BOMBADIL_INSTALL_RECORD", str(tmp_path / "r.json"))
    assert paths.version_file() == tmp_path / "s" / "VERSION" and paths.install_record() == tmp_path / "r.json"


def test_homes_record_of_what_was_done_is_in_the_state_folder(home):
    assert paths.home_file() == paths.state_dir() / "home.json"


# -- `bombadil version` --

def bombadil(*args, env=None):
    base = {**os.environ, "BOMBADIL_SOCKET": "/nonexistent/agentd.sock", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil"), *args], capture_output=True, text=True,
                          env={**base, **(env or {})}, timeout=60, check=False)


def test_the_command_prints_the_line_with_no_agentd_running(system):
    done = bombadil("version")
    assert (done.returncode, done.stdout, done.stderr) == (0, LINE + "\n", "")


def test_the_command_can_give_the_facts_as_json(system):
    done = bombadil("version", "--json")
    assert done.returncode == 0 and json.loads(done.stdout)["commit"] == "d9dde3b"


def test_the_command_says_so_when_it_cannot_tell(tmp_path):
    done = bombadil("version", env={"BOMBADIL_SHARE": str(tmp_path / "none"),
                                    "BOMBADIL_INSTALL_RECORD": str(tmp_path / "none.json")})
    assert (done.returncode, done.stdout) == (0, "Bombadil (version unknown)\n")


def test_the_usage_lists_the_command():
    assert "bombadil version" in bombadil().stdout


# -- the pill's word --

@pytest.mark.parametrize("text", ["version", "Version", "VERSION?", "what version is this", "What version is this?",
                                  "what version", "which version", "  which   version  "])
def test_these_words_are_answered_locally(home, text):
    action = launcher.match(text, [])
    assert action is not None and action.kind == "version"


@pytest.mark.parametrize("text", ["what version of python is this", "version control", "versions", "version 2",
                                  "which version should i use", "what version is firefox", "!version",
                                  "what's the version of the kernel", "bombadil version", "update"])
def test_anything_more_goes_to_the_agent(home, text):
    action = launcher.match(text, [])
    assert action is None or action.kind != "version"


def test_an_app_you_made_called_version_wins(home):
    apps.create("Version", "import QtQuick\nItem {}\n")
    assert launcher.match("version").kind == "app"


def test_it_is_a_command_word_that_is_typed_and_not_completed():
    assert "version" in launcher.UTILITY_COMMANDS and "version" not in launcher.CORE_COMMANDS
    assert "version" in launcher.NO_COMPLETE
    assert not [e for e in launcher.entries([]) if e["name"] == "version"]


def test_the_launcher_answers_with_the_line_and_says_what_it_does(home, system):
    action = launcher.match("what version is this", [])
    lx = launcher.Launcher()
    assert lx.run(action) == (True, LINE)
    assert lx.doing(action) == "Looking it up" and lx.failed(action) == "Could not look it up"


def test_a_button_can_send_the_word_by_name():
    assert agentd._action({"action": "version"}) == launcher.Action("version")


@pytest.mark.asyncio
async def test_the_pill_answers_it_without_asking_a_model(home, system):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    server = asyncio.create_task(d.serve())
    try:
        for _ in range(100):
            if d.socket_path.exists():
                break
            await asyncio.sleep(0.05)
        r, w = await asyncio.open_unix_connection(str(d.socket_path), limit=1 << 24)
        w.write(b'{"type": "prompt", "text": "what version is this?"}\n')
        await w.drain()
        seen = []
        while True:
            msg = json.loads(await asyncio.wait_for(r.readline(), 5))
            seen.append(msg)
            if msg.get("kind") == "local" and msg.get("phase") == "done":
                break
        assert (msg["action"], msg["ok"], msg["text"]) == ("version", True, LINE)
        assert not [m for m in seen if m.get("kind") in ("turn_start", "text")]       # no model was asked
        w.close()
    finally:
        server.cancel()


# -- agentd's status --

def test_the_status_says_which_version_this_process_started_on_and_which_is_installed_now(home, system):
    d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
    first = d._status()
    assert first["version"] == first["version_installed"] == "2026.10.06 (d9dde3b)"
    system.version("2026.11.12 (abc1234)\n")                       # an update replaced the files under it
    later = d._status()
    assert later["version"] == "2026.10.06 (d9dde3b)" and later["version_installed"] == "2026.11.12 (abc1234)"


def test_the_status_has_empty_versions_when_there_is_no_package(home, tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "none"))
    status = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())._status()
    assert status["version"] == "" and status["version_installed"] == ""


# -- what the agent is told --

def test_the_agent_is_told_what_bombadil_is_and_how_it_updates():
    prompt = providers.system_prompt()
    assert "the package `bombadil`" in prompt and "that same pacman -Syu, in the same restore point" in prompt
    assert "/usr/share/bombadil or /usr/bin by hand" in prompt
    assert "next sign-in or when the user says restart" in prompt
    assert "run `bombadil version`" in prompt
    # What was there before is all still there.
    assert "pacman -Syu --noconfirm --needed" in prompt and "never `pacman -Sy` alone" in prompt
    assert "If an upgrade replaced the kernel, tell the user a restart is needed" in prompt

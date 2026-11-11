"""Remote control: the words it answers to, and what `bombadil remote` says in each state it can be in.

tmux and claude are replaced by a script of answers, so nothing here starts anything.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bombadil import launcher, remote

LINK = "https://claude.ai/code/session_01AbCdEfGh"


class FakeTmux:
    """Answers the tmux calls remote.py makes. `screens` is what the session shows on each look, in order."""

    def __init__(self, screens=(), session=False, dead=False, die_on_start=False):
        self.calls: list[list[str]] = []
        self.scopes: list[list[str]] = []
        self.session = session
        self.dead, self.die_on_start = dead, die_on_start
        self.screens = list(screens)
        self.screen = ""

    def __call__(self, argv, **_kw):
        if argv[0] == "systemd-run":   # the scope it is started in; what is run is what the rest of the line says
            self.scopes.append(argv[:argv.index("tmux")])
            argv = argv[argv.index("tmux"):]
        self.calls.append(argv)
        sub = argv[1]
        out, rc = "", 0
        if sub == "has-session":
            rc = 0 if self.session else 1
        elif sub == "new-session":
            self.session, self.dead = True, self.die_on_start
        elif sub == "kill-session":
            self.session, self.screen = False, ""
        elif sub == "list-panes":
            out = "1\n" if self.dead else "0\n"
        elif sub == "capture-pane":
            if self.screens:
                self.screen = self.screens.pop(0)
            out = self.screen
        return subprocess.CompletedProcess(argv, rc, out, "")

    def sent(self):
        return [c for c in self.calls if c[1] == "send-keys"]

    def started(self):
        return [c for c in self.calls if c[1] == "new-session"]


@pytest.fixture(autouse=True)
def installed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(remote.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(remote.socket, "gethostname", lambda: "thinkpad")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)


def go(tmux, folder=None):
    return remote.start(folder, run=tmux, sleep=lambda _s: None, tries=6)


def test_it_starts_claude_in_a_detached_tmux_session_and_gives_the_link():
    tmux = FakeTmux(screens=["Starting...", f"Remote Control is ready\n{LINK}\nPress space for a QR code"])
    ok, text = go(tmux)
    assert ok and LINK in text and "Bombadil on thinkpad" in text
    (call,) = tmux.started()
    assert call[:5] == ["tmux", "new-session", "-d", "-s", remote.SESSION]
    assert "claude remote-control --name 'Bombadil on thinkpad'" in call
    assert "remain-on-exit" in call   # what it said stays when it stops


def test_the_work_folder_is_made_and_is_the_one_the_session_starts_in(tmp_path):
    tmux = FakeTmux(screens=[LINK])
    go(tmux)
    folder = tmp_path / "Projects" / "remote"
    assert folder.is_dir() and str(folder) == tmux.started()[0][tmux.started()[0].index("-c") + 1]


def test_the_default_folder_asks_no_permission_questions_and_takes_a_restore_point_before_each_prompt(tmp_path):
    go(FakeTmux(screens=[LINK]))
    settings = json.loads((tmp_path / "Projects/remote/.claude/settings.json").read_text())
    assert settings["permissions"]["defaultMode"] == "bypassPermissions"
    (hook,) = settings["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert hook["command"] == "bombadil snapshot --hook"


def test_settings_already_in_the_folder_are_never_replaced(tmp_path):
    f = tmp_path / "Projects/remote/.claude/settings.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"mine": true}')
    go(FakeTmux(screens=[LINK]))
    assert f.read_text() == '{"mine": true}'


def test_a_folder_the_person_chose_is_left_exactly_as_it_is(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    go(FakeTmux(screens=[LINK]), mine)
    assert list(mine.iterdir()) == []


def test_already_on_is_said_not_started_again():
    tmux = FakeTmux(screens=[LINK], session=True)
    ok, text = go(tmux)
    assert ok and LINK in text and tmux.started() == []


def test_a_session_that_stopped_is_replaced_by_a_fresh_one():
    tmux = FakeTmux(screens=["it stopped", LINK], session=True, dead=True)
    ok, text = go(tmux)
    assert [c[1] for c in tmux.calls if c[1] in ("kill-session", "new-session")] == ["kill-session", "new-session"]
    assert ok and LINK in text


def test_the_trust_question_for_the_folder_bombadil_made_is_answered_once():
    ask = "Trust /root/Projects/remote? [y/N]"
    tmux = FakeTmux(screens=[ask, ask, LINK])
    ok, _ = go(tmux)
    assert ok and len(tmux.sent()) == 1 and tmux.sent()[0][-2:] == ["y", "Enter"]


def test_the_trust_question_is_not_answered_in_the_same_instant_it_is_drawn():
    # Claude Code throws away a key that comes within 250 ms of the question, so the first answer would be lost.
    ask = "Trust /root/Projects/remote? [y/N]"
    tmux = FakeTmux(screens=[ask, LINK])
    order = []

    def watching(argv, **kw):
        if argv[1] == "send-keys":
            order.append("send")
        return tmux(argv, **kw)

    ok, _ = remote.start(None, run=watching, sleep=lambda s: order.append(s), tries=6)
    assert ok and order.index("send") > 0
    assert order[order.index("send") - 1] >= 0.25   # it waited before the answer, after seeing the question


def test_the_trust_question_that_is_still_there_after_an_answer_is_answered_again_a_few_times_then_left_to_the_person():
    ask = "Trust /root/Projects/remote? [y/N]"
    tmux = FakeTmux(screens=[ask])   # the screen never moves on: every answer was thrown away
    ok, text = remote.start(None, run=tmux, sleep=lambda _s: None, tries=25)
    assert not ok and "bombadil remote show" in text
    assert len(tmux.sent()) == remote.ANSWER_TRIES


def test_a_second_answer_waits_for_the_screen_to_redraw_first():
    ask = "Trust /root/Projects/remote? [y/N]"
    tmux = FakeTmux(screens=[ask, ask, LINK])   # the first answer works, the screen is just slow to show it
    ok, _ = go(tmux)
    assert ok and len(tmux.sent()) == 1


def test_a_default_folder_that_already_holds_a_project_is_not_trusted_for_the_person(tmp_path):
    # Trusting a folder turns on the hooks, servers and commands in it. Bombadil only vouches for a folder it made.
    folder = tmp_path / "Projects/remote"
    folder.mkdir(parents=True)
    (folder / ".mcp.json").write_text("{}")
    tmux = FakeTmux(screens=[f"Trust {folder}? [y/N]"])
    ok, text = go(tmux)
    assert not ok and tmux.sent() == [] and "bombadil remote show" in text


def test_settings_the_person_changed_make_the_folder_theirs_to_trust(tmp_path):
    f = tmp_path / "Projects/remote/.claude/settings.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({**remote.FOLDER_SETTINGS, "hooks": {"SessionStart": []}}))
    tmux = FakeTmux(screens=["Trust it? [y/N]"])
    ok, _ = go(tmux)
    assert not ok and tmux.sent() == []


def test_a_second_start_in_the_folder_bombadil_made_still_answers_for_the_person(tmp_path):
    go(FakeTmux(screens=[LINK]))   # the first start makes the folder and its settings
    tmux = FakeTmux(screens=["Trust it? [y/N]", LINK])
    ok, _ = go(tmux)
    assert ok and len(tmux.sent()) == 1


def test_a_session_that_is_up_but_not_connected_is_not_called_on():
    waiting = FakeTmux(screens=["Trust /root/Projects/remote? [y/N]"] * 3, session=True)
    ok, text = remote.status(waiting)
    assert not ok and "bombadil remote show" in text
    assert remote.url(waiting)[0] is False and "bombadil remote show" in remote.url(waiting)[1]
    connecting = FakeTmux(screens=["Connecting..."] * 2, session=True)
    ok, text = remote.status(connecting)
    assert ok and "starting" in text and "Link:" not in text


def test_starting_while_it_waits_at_the_question_answers_it_without_starting_another_session():
    tmux = FakeTmux(screens=["Trust it? [y/N]", "Trust it? [y/N]", LINK], session=True)
    ok, text = go(tmux)
    assert ok and LINK in text and tmux.started() == [] and len(tmux.sent()) == 1


def test_starting_while_it_is_connecting_waits_for_the_link_instead_of_calling_it_on():
    tmux = FakeTmux(screens=["Connecting...", "Connecting...", LINK], session=True)
    ok, text = go(tmux)
    assert ok and LINK in text and tmux.started() == []


def test_without_tmux_the_other_commands_say_so_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr(remote.shutil, "which", lambda name: None if name == "tmux" else f"/usr/bin/{name}")

    def no_such_program(argv, **_kw):
        raise FileNotFoundError(argv[0])

    assert remote.url(no_such_program) == (False, remote.NO_TMUX)
    assert remote.stop(no_such_program)[0] is True
    assert remote.show(no_such_program) == 1 and remote.NO_TMUX in capsys.readouterr().out


def test_the_trust_question_for_a_folder_the_person_chose_is_left_to_them(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    tmux = FakeTmux(screens=[f"Trust {mine}? [y/N]"])
    ok, text = go(tmux, mine)
    assert not ok and tmux.sent() == []
    assert "bombadil remote show" in text and str(mine) in text


def test_a_folder_that_is_not_there_is_refused_before_anything_starts(tmp_path):
    tmux = FakeTmux()
    ok, text = go(tmux, tmp_path / "nope")
    assert not ok and "not a folder" in text and tmux.calls == []


@pytest.mark.parametrize("said,expect", [
    ("Error: Remote Control requires claude.ai subscription auth.", "Sign in to Claude first"),
    # what Claude Code 2.1 really printed on a computer that was not signed in
    ("Error: You must be logged in to use Remote Control.\nRemote Control is only available with claude.ai "
     "subscriptions. Run `claude auth login` to sign in with your claude.ai account.", "Sign in to Claude first"),
    ("You are not signed in. Run /login", "Sign in to Claude first"),
    ("Remote Control is disabled by your organization's policy", "owner can turn it on"),
    ("Couldn't verify your organization's policy for remote control", "Wi-Fi"),
])
def test_a_start_that_cannot_happen_says_what_to_do_in_plain_words(said, expect):
    ok, text = go(FakeTmux(screens=[said]))
    assert not ok and expect in text


def test_a_start_that_stops_for_a_reason_nothing_knows_shows_what_it_said():
    ok, text = go(FakeTmux(screens=["boom\nsomething odd happened"], die_on_start=True))
    assert not ok and "something odd happened" in text


def test_a_slow_start_is_not_called_a_failure():
    ok, text = go(FakeTmux(screens=["Connecting..."] * 8))
    assert ok and "starting" in text


def test_it_needs_claude_and_tmux(monkeypatch):
    for missing in ("claude", "tmux"):
        monkeypatch.setattr(remote.shutil, "which", lambda name, m=missing: None if name == m else f"/usr/bin/{name}")
        tmux = FakeTmux()
        ok, text = go(tmux)
        assert not ok and missing in text.lower() and tmux.calls == []


def test_status_url_and_stop():
    on = FakeTmux(screens=[LINK, LINK, LINK], session=True)
    assert remote.status(on) == (True, remote.describe(remote.State(url=LINK)))
    assert remote.url(on) == (True, LINK)
    assert remote.stop(on) == (True, "Remote control is off.")
    assert remote.stop(on) == (True, "Remote control was already off.")
    ok, text = remote.status(on)
    assert not ok and "Remote control is off" in text
    assert remote.url(on)[0] is False


def test_the_link_is_cut_at_the_end_of_the_sentence_it_is_in():
    st = remote.read_state(FakeTmux(screens=[f"Open {LINK}."], session=True))
    assert st.url == LINK


def test_the_pill_starts_it_from_its_words_and_only_from_them():
    for words in ("remote", "remote control", "Start remote control"):
        assert launcher.match(words, []).kind == "remote"
    # A sentence about a remote anything is the model's.
    assert launcher.match("why is my remote desktop slow", []) is None


def test_the_pill_action_reports_what_start_said(monkeypatch):
    monkeypatch.setattr(remote, "start", lambda *a, **k: (True, "Remote control is on."))
    lch = launcher.Launcher.__new__(launcher.Launcher)
    assert lch._remote(None) == (True, "Remote control is on.")
    assert launcher.Launcher.doing(launcher.Action("remote", "", "open", "")) == "Starting remote control"


def test_the_command_is_wired_into_bombadil():
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "bin/bombadil"), "remote", "bogus"], capture_output=True, text=True)
    assert r.returncode == 2 and "bombadil remote" in r.stdout


def test_install_opens_the_installer_in_the_drawer_on_the_stick_and_says_it_is_done_on_an_installed_computer(monkeypatch):
    opened = []
    lch = launcher.Launcher.__new__(launcher.Launcher)
    lch.details = lambda argv, toggle=False: opened.append(argv) or "shown"
    monkeypatch.setattr(launcher, "_live", lambda: True)
    assert lch._install(None) == (True, "Opened the installer.") and opened == [["sudo", "bombadil-install"]]
    monkeypatch.setattr(launcher, "_live", lambda: False)
    ok, text = lch._install(None)
    assert not ok and "already installed" in text and len(opened) == 1


def test_a_hook_prompt_becomes_the_label_of_a_restore_point_undo_can_find():
    from bombadil import snapshots
    label = snapshots.label_from_hook(json.dumps({"prompt": "  install   the\nnvidia driver  "}))
    assert label == "turn: remote: install the nvidia driver" and label.startswith("turn:")
    assert snapshots.label_from_hook("not json") == "turn: remote"
    assert snapshots.label_from_hook(json.dumps({"prompt": "x" * 200})) == "turn: remote: " + "x" * 60
    assert snapshots.label_from_hook("[]") == "turn: remote"


def test_taking_a_restore_point_by_hand_never_fails_the_prompt_it_is_a_hook_for():
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "bin/bombadil"), "snapshot", "--hook"], input='{"prompt": "hi"}',
                       capture_output=True, text=True)
    assert r.returncode == 0   # no snapper here: it says nothing and goes on


def _hook(tmp_path, marker):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    if marker is not None:
        (state / "undo.json").write_text(json.dumps(marker))
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "bin/bombadil"), "snapshot", "--hook"], input='{"prompt": "hi"}',
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                                                            "BOMBADIL_STATE": str(state)})
    assert r.returncode == 0, r.stderr
    return state / "undo.json"


def test_a_turn_from_a_remote_session_starts_a_new_history_so_the_pills_undo_takes_that_turn_back(tmp_path):
    # The pill clears the marker of its last undo at every turn; the hook must too, or "undo" goes past this turn.
    assert not _hook(tmp_path, {"snapshot": 7, "what": "old turn", "boot": "an-earlier-boot", "t": 1}).exists()


def test_a_turn_from_a_remote_session_leaves_an_undo_that_still_waits_for_its_restart(tmp_path):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    marker = _hook(tmp_path, {"snapshot": 7, "what": "old turn", "boot": boot, "t": 1})
    assert marker.exists() and json.loads(marker.read_text())["snapshot"] == 7


def test_tmux_starts_in_a_scope_of_its_own_when_there_is_a_user_session(monkeypatch):
    tmux = FakeTmux(screens=[LINK])
    go(tmux)
    assert tmux.scopes == []   # no user session here: plain tmux
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    tmux = FakeTmux(screens=[LINK])
    go(tmux)
    assert tmux.scopes and tmux.scopes[0][:3] == ["systemd-run", "--user", "--scope"]

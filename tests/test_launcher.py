import json
import tomllib

import pytest

from bombadil import apps, desk, launcher, paths, snapshots
from bombadil.loop import words


def _apps(home):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    apps.create("Memory Viewer", "import QtQuick\nItem {}\n")
    return apps.list_apps()


@pytest.mark.parametrize("text, kind, target, verb", [
    ("browser", "panel", "browser", "open"),
    ("Chrome", "panel", "browser", "open"),
    ("open the browser", "panel", "browser", "open"),
    ("internet", "panel", "browser", "open"),
    ("terminal", "panel", "terminal", "open"),
    ("files", "panel", "files", "open"),
    ("hide browser", "panel", "browser", "hide"),
    ("close the terminal", "panel", "terminal", "close"),
    ("passwords", "app", "passwords", "open"),
    ("Passwords.", "app", "passwords", "open"),
    ("open memory viewer", "app", "memory-viewer", "open"),
    ("memory-viewer", "app", "memory-viewer", "open"),
    ("quit passwords", "app", "passwords", "close"),
    ("undo", "undo", "", "open"),
    ("Undo that", "undo", "", "open"),
    ("stop", "stop", "", "open"),
    ("history", "history", "", "open"),
    ("wifi", "wifi", "", "open"),
    ("open wi-fi", "wifi", "", "open"),
    ("battery", "battery", "", "open"),
    ("shut down", "shutdown", "", "open"),
    ("desk", "desk", "", "open"),
    ("Desk.", "desk", "", "open"),
    ("show machine", "widget", "machine", "open"),
    ("Show the Machine", "widget", "machine", "open"),
    ("open alive", "widget", "alive", "open"),
    ("bring up watching", "widget", "watching", "open"),
    ("show now", "widget", "now", "open"),
    ("show the route", "widget", "now", "open"),
    ("show needs you", "widget", "needs", "open"),
    ("show while you were away", "widget", "away", "open"),
    ("hide machine", "widget", "machine", "hide"),
    ("hide now.", "widget", "now", "hide"),
    ("hide needs you", "widget", "needs", "hide"),
    ("put away alive", "widget", "alive", "hide"),
    ("put watching away", "widget", "watching", "hide"),
    ("put the machine away", "widget", "machine", "hide"),
    ("close watching", "widget", "watching", "close"),
])
def test_exact_words_open_locally(home, text, kind, target, verb):
    a = launcher.match(text, _apps(home))
    assert a is not None and (a.kind, a.target, a.verb) == (kind, target, verb)


@pytest.mark.parametrize("text", [
    "open the browser and search for flights", "make me a password manager", "undo the docker install",
    "pass", "!ls", "", "what is using my memory", "start docker", "the wifi is slow",
    # A sentence in another script keeps its one launcher word only in ASCII; it is still a sentence.
    "Как сделать restart?", "shutdownしないで", "不要 undo", "为什么 browser 很慢", "почему wifi не работает?",
    "¿restart?", "电脑 restart 以后很慢", "turn off", "restart?", "open the browser, please",
    # "!" always means a shell command, even before a launcher word.
    "!shutdown", "!restart", "!undo", "!files", " !history",
    # The desk word is exact; a sentence with it in is for the agent.
    "what is on my desk?", "show me the desk", "hide the desk", "clean my desk", "desk please",
    # A question asks; it does not tell, so it is the agent's to answer, for the desk as for restart.
    "desk?", "show machine?", "hide now?", "open alive ?",
    # A widget's name needs one of the verbs that put a thing on the screen: bare it is a word,
    # and with other verbs it is a sentence.
    "now", "away", "machine", "alive", "watching", "needs you", "the machine", "route",
    "start now", "run now", "exit now", "quit machine", "kill machine", "launch alive", "go to away",
    "show me machine", "hide machine now", "put watching on the right", "put watching away please",
    "put away", "make a widget", "show my batch on the desk", "hide the machine widget",
])
def test_everything_else_goes_to_the_agent(home, text):
    assert launcher.match(text, _apps(home)) is None


def test_an_app_you_made_wins_over_a_utility_word(home):
    apps.create("Sound", "import QtQuick\nItem {}\n")
    assert launcher.match("sound").kind == "app"
    assert launcher.match("undo").kind == "undo"   # core commands always mean the same thing


def test_entries_for_completion(home):
    e = launcher.entries(_apps(home))
    assert e[0]["kind"] == "app"
    names = [x["name"] for x in e]
    assert "browser" in names and "undo" in names
    assert "shutdown" not in names and "restart" not in names   # never one Tab away


def test_the_desk_and_its_widgets_are_in_the_names_the_pill_completes(home):
    e = launcher.entries([])
    kinds = [x["kind"] for x in e]
    rank = {"app": 0, "panel": 1, "widget": 2, "command": 3}
    assert kinds == sorted(kinds, key=rank.get)   # apps, then panels, widgets and commands
    widgets = {x["name"]: x for x in e if x["kind"] == "widget"}
    assert list(widgets) == ["now", "watching", "alive", "needs", "away", "machine"]
    assert widgets["needs"] == {"name": "needs", "title": "Needs you", "kind": "widget",
                                "words": ["needs", "needs you"]}
    assert "while you were away" in widgets["away"]["words"] and "route" in widgets["now"]["words"]
    assert {"name": "desk", "title": "Desk", "kind": "command", "words": ["desk"]} in e


def test_an_app_you_named_like_a_widget_still_opens_and_the_desk_word_is_never_shadowed(home):
    apps.create("Now", "import QtQuick\nItem {}\n")
    apps.create("Desk", "import QtQuick\nItem {}\n")
    for text, verb in (("now", "open"), ("show now", "open"), ("hide now", "hide"), ("close now", "close")):
        a = launcher.match(text)
        assert (a.kind, a.target, a.verb) == ("app", "now", verb)
    assert launcher.match("put now away") is None            # no such form for apps: the agent's
    assert launcher.match("show machine").kind == "widget"   # the app does not take the others
    assert launcher.match("desk").kind == "desk"
    assert launcher.match("open desk").kind == "app"         # a verb and a name is the app's to answer


class Snaps(snapshots.Snapshots):
    def __init__(self, n):
        self.n = n
        self.rolled = []

    @property
    def available(self):
        return True

    def list(self, limit=20):
        return [snapshots.Snapshot(i, f"turn:{i}: prompt {i}") for i in range(1, self.n + 1)]

    def rollback(self, number):
        self.rolled.append(number)
        return True


def test_undo_goes_back_one_turn_at_a_time_until_a_new_turn(home, monkeypatch):
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-1")
    s = Snaps(3)
    lx = launcher.Launcher(snaps=s)
    ok, text = lx.run(launcher.Action("undo"))
    assert ok and s.rolled == [3]
    assert "“prompt 3”" in text and "restart" in text and "home folder" in text
    lx.run(launcher.Action("undo"))
    assert s.rolled == [3, 2]
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-2")   # the restart applied it
    lx.clear_undo()                   # agentd does this when a new turn starts
    s.n = 4
    lx.run(launcher.Action("undo"))
    assert s.rolled == [3, 2, 4]


def test_a_turn_before_the_restart_does_not_bring_back_what_was_undone(home, monkeypatch):
    """Until the restart applies an undo, the running root still has the undone change; the
    next turn's restore point has it too, and rolling back to that would restore it."""
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-1")
    s = Snaps(2)
    lx = launcher.Launcher(snaps=s)
    lx.run(launcher.Action("undo"))            # undo turn 2, "prompt 2"
    lx.clear_undo()                            # turn 3 starts in the same boot
    s.n = 3
    ok, text = lx.run(launcher.Action("undo"))
    assert ok and s.rolled == [2]              # nothing new: the restart already covers turn 3
    assert "Already undone" in text and "“prompt 2”" in text
    lx.run(launcher.Action("undo"))            # asked again: one turn further back
    assert s.rolled == [2, 1]


def test_undo_without_restore_points_says_so(home):
    lx = launcher.Launcher(snaps=snapshots.Snapshots(configs_dir=home / "nope"))
    ok, text = lx.run(launcher.Action("undo"))
    assert not ok and "restore points" in text
    ok, text = launcher.Launcher(snaps=Snaps(0)).run(launcher.Action("undo"))
    assert not ok and text == "Nothing to undo yet."


class FakeHypr:
    def __init__(self):
        self.calls = []
        self.available = True

    def panel(self, name, show=True):
        self.calls.append(("panel", name, show))
        return f"panel {name} shown"

    def dispatch(self, lua):
        self.calls.append(("dispatch", lua))
        return "ok"

    def request(self, cmd):
        return json.dumps([{"name": "DP-1", "focused": True, "specialWorkspace": {"name": "special:browser"}},
                           {"name": "DP-2", "focused": False, "specialWorkspace": {"name": ""}}])

    def clients(self):
        return []


def test_panels_open_and_hide_through_hyprland(home):
    h = FakeHypr()
    lx = launcher.Launcher(hyprland=h, snaps=Snaps(0))
    assert lx.run(launcher.match("chrome", [])) == (True, "Opened the browser.")
    assert lx.run(launcher.match("hide files", [])) == (True, "Put Files away.")
    assert lx.run(launcher.Action("hide")) == (True, "Put everything away.")
    assert h.calls == [("panel", "browser", True), ("panel", "files", False),
                       ("dispatch", 'hl.dsp.workspace.toggle_special("browser")')]


def test_an_open_app_comes_forward_instead_of_starting_twice(home, monkeypatch):
    _apps(home)
    h = FakeHypr()
    started = []
    monkeypatch.setattr(launcher, "_placement", lambda: None)
    monkeypatch.setattr(launcher.apps, "run", lambda name: started.append(name))
    lx = launcher.Launcher(hyprland=h, snaps=Snaps(0))
    monkeypatch.setattr(launcher, "_app_running", lambda name: False)
    assert lx.run(launcher.match("passwords")) == (True, "Opened Passwords.")
    monkeypatch.setattr(launcher, "_app_running", lambda name: True)
    lx.run(launcher.match("passwords"))
    assert started == ["passwords"]
    assert h.calls == [("dispatch", 'hl.dsp.focus({ window = "class:^(bombadil-app-passwords)$" })')]


def test_failures_are_one_plain_line(home):
    class Broken(FakeHypr):
        def panel(self, name, show=True):
            raise RuntimeError("chromium is not installed")
    ok, text = launcher.Launcher(hyprland=Broken(), snaps=Snaps(0)).run(launcher.match("browser", []))
    assert not ok and text == "Could not open the browser: chromium is not installed"
    assert "\n" not in text


def test_a_failed_command_says_why_in_its_own_words(home):
    import subprocess

    def fail(argv, **k):
        raise subprocess.CalledProcessError(1, argv, output=b"", stderr=b"warning: x\nsudo: a password is required\n")
    ok, text = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0), runner=fail).run(launcher.Action("restart"))
    assert (ok, text) == (False, "Could not restart: sudo: a password is required")


def test_a_folder_that_is_not_an_app_is_skipped(home):
    _apps(home)
    (home / "Apps" / "passwords.bak").mkdir()
    (home / "Apps" / "passwords.bak" / "main.qml").write_text("Item {}")
    (home / "Apps" / "notes").mkdir()
    (home / "Apps" / "notes" / "main.qml").write_text("Item {}")
    (home / "Apps" / "notes" / "app.toml").write_text('title = "\\ud83c"\n')   # not valid TOML
    names = [a.name for a in launcher.known_apps()]
    assert names == ["memory-viewer", "notes", "passwords"]
    assert launcher.match("stop").kind == "stop"
    assert launcher.match("notes").target == "notes"
    assert [e["name"] for e in launcher.entries()][:3] == names


def test_an_app_title_in_any_script_still_opens(home):
    apps.create("Café", "import QtQuick\nItem {}\n")
    a = launcher.match("café")
    assert a is not None and a.kind == "app" and a.target == "caf"
    assert launcher.match("open Café").verb == "open"


class Foot:
    """A drawer's foot process: `pid`, and poll() says whether it has exited."""
    def __init__(self, pid, exited=None):
        self.pid, self.exited = pid, exited

    def poll(self):
        return self.exited


class Ran:
    def __init__(self, returncode):
        self.returncode = returncode


def _drawer(monkeypatch, h, foot=None, open_=False):
    """A Launcher whose drawer is foot (pid 4242), and whether pgrep finds one open."""
    spawned, ran = [], []
    monkeypatch.setattr(launcher.shutil, "which", lambda b: f"/usr/bin/{b}")

    def runner(argv, **k):
        ran.append(argv)
        return Ran(0 if open_ or argv[0] == "pkill" else 1)

    def spawn(argv, **k):
        spawned.append(argv)
        return foot or Foot(4242)

    return launcher.Launcher(hyprland=h, snaps=Snaps(0), runner=runner, spawn=spawn), spawned, ran


class MappingHypr(FakeHypr):
    """Hyprland whose clients list shows the drawer's window only after a few asks, as a
    window maps a moment after its process starts."""
    def __init__(self, after=3):
        super().__init__()
        self.asks, self.after = 0, after

    def clients(self):
        self.asks += 1
        return [{"class": "bombadil-details", "pid": 4242}] if self.asks > self.after else []


def test_details_drawer_runs_in_foot_and_slides_in_with_the_keyboard(home, monkeypatch):
    h = MappingHypr()
    lx, spawned, ran = _drawer(monkeypatch, h)
    assert lx.details(["bombadil", "watch"]) == "shown"
    assert spawned == [["/usr/bin/foot", "--app-id=bombadil-details", "--title=Details", "bombadil", "watch"]]
    assert ran == [["pkill", "-f", "--", "--app-id=bombadil-details"]]
    # Its rule is silent and showing the workspace before the window maps leaves the keyboard
    # on the bar: wait for the window, then focus it, which shows the drawer with the keyboard.
    assert h.asks == 4
    assert h.calls == [("dispatch", 'hl.dsp.focus({ window = "pid:4242" })')]


def test_details_again_closes_the_drawer_it_shows(home, monkeypatch):
    h = MappingHypr(after=0)
    lx, spawned, ran = _drawer(monkeypatch, h, open_=True)
    lx.details(["bombadil", "watch", "--file", "/t/1.jsonl", "--follow"], toggle=True)
    ran.clear()
    # The turn ended meanwhile, so the second click has no --follow: still the same drawer.
    assert lx.details(["bombadil", "watch", "--file", "/t/1.jsonl"], toggle=True) == "hidden"
    assert ran == [["pgrep", "-f", "--", "--app-id=bombadil-details"], ["pkill", "-f", "--", "--app-id=bombadil-details"]]
    assert len(spawned) == 1
    # Another turn's details, or the history, replace what the drawer shows instead.
    assert lx.details(["bombadil", "watch", "--file", "/t/2.jsonl"], toggle=True) == "shown"
    assert lx.details(["bombadil", "history"], toggle=True) == "shown"
    assert len(spawned) == 3


def test_details_again_opens_it_when_a_key_closed_it_meanwhile(home, monkeypatch):
    lx, spawned, _ = _drawer(monkeypatch, MappingHypr(after=0), open_=False)
    lx.details(["bombadil", "watch"], toggle=True)
    assert lx.details(["bombadil", "watch"], toggle=True) == "shown"   # pgrep finds none open
    assert len(spawned) == 2


def test_close_details_puts_the_drawer_away(home, monkeypatch):
    lx, _, ran = _drawer(monkeypatch, MappingHypr(after=0))
    lx.details(["bombadil", "watch"])
    ran.clear()
    assert lx.close_details() is True
    assert ran == [["pkill", "-f", "--", "--app-id=bombadil-details"]]
    assert lx.details(["bombadil", "watch"], toggle=True) == "shown"   # nothing to toggle off now


def test_a_drawer_closed_before_its_window_maps_is_not_shown_empty(home, monkeypatch):
    h = MappingHypr(after=10 ** 6)
    lx, _, _ = _drawer(monkeypatch, h, foot=Foot(4242, exited=-15))
    lx.details(["bombadil", "watch"])
    assert h.calls == []


def test_a_drawer_slow_to_map_still_slides_in(home, monkeypatch):
    h = MappingHypr(after=10 ** 6)
    lx, _, _ = _drawer(monkeypatch, h)
    lx._focus_drawer(Foot(4242), wait=0.2)
    assert h.calls == [("dispatch", 'hl.dsp.focus({ workspace = "special:details" })')]


def _lx(home, **k):
    return launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0), desk=desk.Desk(), **k)


@pytest.mark.parametrize("words, want", [
    (["hide machine"], [(True, "Put Machine away.")]),
    (["hide machine", "show machine"], [(True, "Put Machine away."), (True, "Put Machine on the desk.")]),
    (["close the machine"], [(True, "Put Machine away.")]),
    (["put watching away", "put watching away"],
     [(True, "Put Watching away."), (True, "Watching is already put away.")]),
    (["show alive"], [(True, "Put Alive on the desk.")]),
    (["show now"], [(True, "Now is already on the desk.")]),
    (["hide needs you"], [(False, "Needs you cannot be hidden.")]),
    (["hide needs"], [(False, "Needs you cannot be hidden.")]),
    (["desk", "desk", "desk"],
     [(True, "Folded the desk."), (True, "Unfolded the desk."), (True, "Folded the desk.")]),
])
def test_the_desk_words_change_the_desk_and_say_what_they_did(home, words, want):
    lx = _lx(home)
    assert [lx.run(launcher.match(w, [])) for w in words] == want


def test_the_words_change_what_the_shell_is_told_and_what_the_next_start_reads(home):
    lx = _lx(home)
    told = []
    lx.desk_state.on_change = lambda: told.append(lx.desk_state.snapshot())
    lx.run(launcher.match("hide machine", []))
    lx.run(launcher.match("desk", []))
    lx.run(launcher.match("hide needs you", []))     # refused: nothing changes, nobody is told
    assert [(t["folded"], t["hidden"]) for t in told] == [(False, ["alive", "machine"]), (True, ["alive", "machine"])]
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).desk_state.snapshot() == told[-1]


def test_the_line_says_what_it_is_doing_and_what_went_wrong(home):
    doing = launcher.Launcher.doing
    failed = launcher.Launcher.failed
    assert doing(launcher.match("hide machine", [])) == "Putting Machine away"
    assert doing(launcher.match("close needs you", [])) == "Putting Needs you away"
    assert doing(launcher.match("show alive", [])) == "Putting Alive on the desk"
    assert doing(launcher.match("desk", [])) == "Changing the desk"
    assert failed(launcher.match("hide machine", [])) == "Could not put Machine away"
    assert failed(launcher.match("show alive", [])) == "Could not put Alive on the desk"
    assert failed(launcher.match("desk", [])) == "Could not change the desk"


def test_a_desk_that_breaks_is_one_plain_line(home):
    lx = _lx(home)

    def boom(*a, **k):
        raise OSError("disk on fire")
    lx.desk_state.apply = boom
    assert lx.run(launcher.match("hide machine", [])) == (False, "Could not put Machine away: disk on fire")
    assert lx.run(launcher.match("desk", [])) == (False, "Could not change the desk: disk on fire")


def test_hide_alone_still_puts_the_drawers_away_not_a_widget(home):
    h = FakeHypr()
    lx = launcher.Launcher(hyprland=h, snaps=Snaps(0), desk=desk.Desk())
    for word in ("hide", "hide it", "hide everything", "put it away"):
        assert launcher.match(word, []).kind == "hide"
    assert lx.run(launcher.match("hide", [])) == (True, "Put everything away.")
    assert lx.desk_state.snapshot()["hidden"] == ["alive"]


def test_a_launcher_made_alone_reads_the_desk_that_was_saved(home):
    saved = desk.Desk()
    saved.apply("hide", "machine")
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).desk_state.snapshot()["hidden"] == \
        ["alive", "machine"]


# -- words Bombadil made (words.toml) --

def _words(*rows):
    """Write words.toml by hand: (phrase, kind, name[, away]) rows, some of which add() would
    refuse (a name an app took later), because the launcher must cope with whatever is in the file."""
    text = "".join(
        f'[[word]]\nphrase = {json.dumps(r[0], ensure_ascii=False)}\n'
        f'opens = {{ kind = "{r[1]}", name = "{r[2]}" }}\naway = {"true" if len(r) > 3 and r[3] else "false"}\n\n'
        for r in rows)
    paths.words_file().parent.mkdir(parents=True, exist_ok=True)
    paths.words_file().write_text(text)
    words._cache = None


def test_a_word_opens_what_it_was_made_for(home):
    _words(("show me my passwords", "app", "passwords"), ("show my stuff", "panel", "files"))
    apps_ = _apps(home)
    a = launcher.match("show me my passwords", apps_)
    assert a == launcher.Action("app", "passwords", "open", "Passwords", "show me my passwords")
    assert launcher.match("show my stuff", apps_) == launcher.Action("panel", "files", "open", "Files", "show my stuff")
    assert launcher.match("passwords", apps_).word == "" and launcher.Action("app").word == ""


@pytest.mark.parametrize("text", [
    "show me my passwords", "Show Me My Passwords", "show me my passwords.", " SHOW  me   my passwords!! ",
    "show me my passwords?", "open show me my passwords", "launch show me my passwords",
    "go to show me my passwords",
])
def test_a_word_matches_its_phrase_exactly_and_after_an_opening_verb(home, text):
    _words(("show me my passwords", "app", "passwords"))
    a = launcher.match(text, _apps(home))
    assert a is not None and (a.kind, a.target, a.verb, a.word) == ("app", "passwords", "open", "show me my passwords")


@pytest.mark.parametrize("text", [
    "show me my passwords please", "show me my password", "please show me my passwords", "my passwords",
    "show me my passwords and my files", "show me my passwords, then lock", "show me my passwords\nmake it big",
    "close show me my passwords", "hide show me my passwords", "what do show me my passwords mean",
    "!show me my passwords", " !show me my passwords", "", "show me",
])
def test_a_word_is_never_a_guess(home, text):
    _words(("show me my passwords", "app", "passwords"))
    assert launcher.match(text, _apps(home)) is None


def test_a_word_in_another_script_matches_only_as_a_whole(home):
    _words(("мои пароли", "app", "passwords"), ("café", "app", "passwords"))
    apps_ = _apps(home)
    assert launcher.match("Мои пароли!", apps_).word == "мои пароли"
    assert launcher.match("open café", apps_).word == "café"
    for text in ("открой мои пароли", "¿мои пароли", "мои пароли и файлы", "мои пароли?!?x"):
        assert launcher.match(text, apps_) is None


def test_nothing_starting_with_a_bang_is_ever_a_word(home):
    _words(("ls", "app", "passwords"), ("list all", "app", "passwords"))
    apps_ = _apps(home)
    assert launcher.match("ls", apps_).word == "ls"
    for text in ("!ls", "! ls", "!list all", "  !ls"):
        assert launcher.match(text, apps_) is None


def test_a_word_never_beats_anything_the_machine_already_has(home):
    """Hand-written rows for every kind of name: each still means what it always meant."""
    apps_ = _apps(home)
    _words(("passwords", "app", "memory-viewer"), ("open passwords", "app", "memory-viewer"),
           ("browser", "app", "passwords"), ("chrome", "panel", "files"), ("the terminal", "app", "passwords"),
           ("undo", "app", "passwords"), ("stop it", "app", "passwords"), ("history", "panel", "files"),
           ("wifi", "app", "passwords"), ("sound", "panel", "files"), ("battery", "app", "passwords"),
           ("noticed", "app", "passwords"), ("hide noticed", "app", "passwords"),
           ("show noticed", "app", "passwords"), ("memory viewer", "app", "passwords"))
    want = {"passwords": ("app", "passwords"), "open passwords": ("app", "passwords"),
            "browser": ("panel", "browser"), "chrome": ("panel", "browser"), "the terminal": ("panel", "terminal"),
            "undo": ("undo", ""), "stop it": ("stop", ""), "history": ("history", ""), "wifi": ("wifi", ""),
            "sound": ("sound", ""), "battery": ("battery", ""), "noticed": ("noticed", "open"),
            "hide noticed": ("noticed", "hide"), "show noticed": ("noticed", "open"),
            "memory viewer": ("app", "memory-viewer")}
    for text, (kind, target) in want.items():
        a = launcher.match(text, apps_)
        assert (a.kind, a.target, a.word) == (kind, target, ""), text


def test_a_word_gives_way_to_an_app_made_later(home):
    _words(("notes", "app", "passwords"))
    a = launcher.match("notes", _apps(home))
    assert (a.target, a.word) == ("passwords", "notes")
    apps.create("Notes", "import QtQuick\nItem {}\n")
    a = launcher.match("notes")
    assert (a.target, a.word) == ("notes", "")


def test_a_word_whose_app_is_gone_or_which_is_put_away_matches_nothing(home):
    _words(("my ghost", "app", "ghost"), ("my panel", "panel", "chromium"), ("my passwords", "app", "passwords", True))
    apps_ = _apps(home)
    for text in ("my ghost", "open my ghost", "my panel", "my passwords", "show my passwords"):
        assert launcher.match(text, apps_) is None
    apps.create("Ghost", "import QtQuick\nItem {}\n")
    assert launcher.match("my ghost").target == "ghost"          # the app came back: so does the word
    words.bring_back("my passwords")
    assert launcher.match("my passwords", apps_).word == "my passwords"


def test_a_word_made_by_add_works_and_undoes(home):
    apps_ = _apps(home)
    assert launcher.match("show me my passwords", apps_) is None
    words.add("show me my passwords", {"kind": "app", "name": "passwords"}, group="g-1")
    assert launcher.match("show me my passwords", apps_).target == "passwords"
    words.put_away("show me my passwords")
    assert launcher.match("show me my passwords", apps_) is None
    words.bring_back("show me my passwords")
    words.remove("show me my passwords")
    assert launcher.match("show me my passwords", apps_) is None


def test_match_leaves_words_out_when_asked_and_means_says_what_is_taken(home):
    _words(("show me my passwords", "app", "passwords"))
    apps_ = _apps(home)
    assert launcher.match("show me my passwords", apps_, use_words=False) is None
    assert launcher.means("show me my passwords", apps_) == ""          # a word is not a name the machine has
    assert [launcher.means(t, apps_) for t in ("passwords", "Chrome", "undo", "noticed", "wifi", "!ls", "")] == [
        "an app", "a panel", "a command", "a command", "a utility word", "", ""]


def test_a_broken_words_file_costs_the_launcher_nothing(home, capsys):
    paths.words_file().parent.mkdir(parents=True)
    paths.words_file().write_text('[[word]\nphrase = "x')
    apps_ = _apps(home)
    assert launcher.match("what is using my memory", apps_) is None
    assert launcher.match("passwords", apps_).kind == "app" and launcher.match("undo", apps_).kind == "undo"
    assert capsys.readouterr().err.count("does not parse") == 1


def test_every_enter_reads_the_words_file_at_most_once_while_it_is_unchanged(home, monkeypatch):
    _words(("show me my passwords", "app", "passwords"))
    apps_ = _apps(home)
    parses = []
    real = tomllib.loads
    monkeypatch.setattr(words.tomllib, "loads", lambda text: parses.append(1) or real(text))
    for _ in range(50):
        assert launcher.match("what is eating my memory", apps_) is None
        assert launcher.match("show me my passwords", apps_) is not None
    assert len(parses) == 1


def test_a_word_runs_like_the_app_it_opens(home, monkeypatch):
    _words(("show me my passwords", "app", "passwords"))
    _apps(home)
    started = []
    monkeypatch.setattr(launcher, "_placement", lambda: None)
    monkeypatch.setattr(launcher, "_app_running", lambda name: False)
    monkeypatch.setattr(launcher.apps, "run", lambda name: started.append(name))
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))
    action = launcher.match("show me my passwords")
    assert lx.doing(action) == "Opening Passwords" and lx.failed(action) == "Could not open Passwords"
    assert lx.run(action) == (True, "Opened Passwords.") and started == ["passwords"]


# -- "noticed": the widget's name --

@pytest.mark.parametrize("text, verb", [
    ("noticed", "open"), ("Noticed", "open"), ("Noticed.", "open"), ("  noticed  ", "open"),
    ("open noticed", "open"), ("show noticed", "open"), ("Show Noticed!", "open"), ("launch noticed", "open"),
    ("open the noticed", "open"), ("hide noticed", "hide"), ("Hide Noticed", "hide"), ("put away noticed", "hide"),
])
def test_the_noticed_words(home, text, verb):
    a = launcher.match(text, _apps(home))
    # The verb rides in target too, so whichever the loop service reads is right.
    assert a == launcher.Action("noticed", verb, verb, "Noticed")


@pytest.mark.parametrize("text", [
    "noticed this", "what have you noticed", "noticed stuff", "is noticed open", "close noticed", "quit noticed",
    "hide noticed and show passwords", "show me noticed", "unnoticed", "not noticed", "¿noticed", "почему noticed",
    "!noticed", "!hide noticed", " !show noticed", "noticed 2", "hide everything noticed", "no ticed", "no-ticed",
])
def test_anything_else_with_noticed_in_it_goes_to_the_agent(home, text):
    assert launcher.match(text, _apps(home)) is None


def test_an_app_called_noticed_keeps_the_name(home):
    apps.create("Noticed", "import QtQuick\nItem {}\n")
    for text, verb in (("noticed", "open"), ("show noticed", "open"), ("hide noticed", "hide"),
                       ("close noticed", "close")):
        a = launcher.match(text)
        assert (a.kind, a.target, a.verb) == ("app", "noticed", verb), text
    assert [e["kind"] for e in launcher.entries() if e["name"] == "noticed"] == ["app"]


def test_noticed_is_a_name_tab_completes(home):
    (e,) = [e for e in launcher.entries(_apps(home)) if e["name"] == "noticed"]
    assert e == {"name": "noticed", "title": "Noticed", "kind": "command", "words": ["noticed"]}
    assert launcher.entries(_apps(home))[-1] == e


def test_noticed_has_its_own_sentences_and_is_not_run_by_the_launcher(home):
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))
    opening, hiding = launcher.match("noticed", []), launcher.match("hide noticed", [])
    assert (lx.doing(opening), lx.doing(hiding)) == ("Opening Noticed", "Hiding Noticed")
    assert (lx.failed(opening), lx.failed(hiding)) == ("Could not open Noticed", "Could not hide Noticed")
    # agentd hands these to the loop service; if one gets here, nothing is running to do it.
    assert lx.run(opening) == (False, "Noticed is not running.")
    assert lx.run(hiding) == (False, "Noticed is not running.")

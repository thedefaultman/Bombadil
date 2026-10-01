import json

import pytest
from mail_stub import mail_service  # noqa: F401 - the `mail` fixture

from bombadil import apps, desk, launcher, snapshots


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
        self.placed = []
        self.available = True

    def place_app(self, name):
        self.placed.append(name)
        return ""

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
    assert h.placed == ["passwords"]   # only the open that starts it picks a spot; the re-open just focuses


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


def test_why_is_a_launcher_word_only_while_a_turn_runs():
    assert launcher.match("why") is None and launcher.match("why?") is None
    assert launcher.match("why?", busy=True) == launcher.Action("why")
    assert launcher.match("  Why  ", busy=True) == launcher.Action("why")
    # Only the bare word: a sentence, or someone else's word, goes to the agent.
    assert launcher.match("why is the sky blue", busy=True) is None
    assert launcher.match("почему", busy=True) is None


def _placing(monkeypatch, said):
    """The app kit's placement, saying `said` whatever it is asked; and what it was asked."""
    import types
    asked = []

    def do(verb):
        return lambda name: asked.append((verb, name)) or said
    monkeypatch.setattr(launcher, "_placement", lambda: types.SimpleNamespace(
        show=do("show"), hide=do("hide"), close=do("close")))
    return asked


@pytest.mark.parametrize("text, said, expected", [
    ("passwords", "passwords shown", (True, "Opened Passwords.")),
    ("passwords", "started passwords; it slides in when its window opens", (True, "Opened Passwords.")),
    ("hide passwords", "passwords hidden", (True, "Put Passwords away.")),
    ("quit passwords", "passwords closed", (True, "Closed Passwords.")),
    ("quit passwords", "passwords did not quit within 3 s (stuck?) and was killed; unsaved changes are lost",
     (True, "Closed Passwords.")),
    # What placement found instead of the thing done: said as it is, with the app's title.
    ("quit memory viewer", "memory-viewer is not running", (True, "Memory Viewer is not running.")),
    ("hide memory viewer", "memory-viewer is not on screen", (True, "Memory Viewer is not on screen.")),
    ("hide passwords", "Hyprland is not running; nothing to hide", (True, "Hyprland is not running; nothing to hide.")),
    ("quit passwords", "passwords did not quit within 3 s and could not be killed",
     (False, "Could not close Passwords: passwords did not quit within 3 s and could not be killed")),
])
def test_an_app_action_says_what_placement_found(home, monkeypatch, text, said, expected):
    _apps(home)
    asked = _placing(monkeypatch, said)
    a = launcher.match(text)
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).run(a) == expected
    assert asked == [({"open": "show", "hide": "hide", "close": "close"}[a.verb], a.target)]


def test_closing_an_app_that_is_not_running_does_not_say_it_closed(home, monkeypatch):
    from bombadil.appkit import placement
    _apps(home)
    monkeypatch.setattr(placement, "running", lambda: {})
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))
    assert lx.run(launcher.match("quit memory viewer")) == (True, "Memory Viewer is not running.")
    # A drawer nobody can look at (no Hyprland here) was not put away either.
    assert lx.run(launcher.match("hide passwords")) == (True, "Hyprland is not running; nothing to hide.")


# -- picture words --

@pytest.mark.parametrize("text, target", [
    ("how am I connected?", "network"), ("Am I online", "network"), ("How am I connected", "network"),
    ("what starts when I boot?", "boot"), ("what runs at boot", "boot"),
    ("where did my disk go?", "disks"), ("Where did my space go", "disks"),
    ("what's playing where", "sound"), ("whats playing where?", "sound"), ("what’s playing where", "sound"),
    ("my screens", "screens"), ("my monitors.", "screens"),
])
def test_whole_questions_about_the_machine_draw_a_picture_locally(home, text, target):
    a = launcher.match(text, [])
    assert (a.kind, a.target) == ("picture", target)


@pytest.mark.parametrize("text", [
    "how am I connected to the printer?", "why is my wifi slow", "how do I get connected", "draw how am i connected",
    "what starts when I boot up the vm and why", "where did my disk go wrong", "!how am I connected",
    "how am I connected 😀", "как я подключён",
])
def test_anything_longer_or_different_goes_to_the_agent(home, text):
    assert launcher.match(text, []) is None


def test_what_does_a_service_need_asks_the_machine_whether_it_exists(home, monkeypatch):
    seen = []
    monkeypatch.setattr(launcher.sysmap, "service_exists", lambda name: seen.append(name) or name == "bluetooth")
    a = launcher.match("What does bluetooth need?", [])
    assert (a.kind, a.target, a.title) == ("picture", "service:bluetooth", "what bluetooth needs")
    assert launcher.match("what does the bluetooth service depend on", [])
    assert launcher.match("what does the moon need", []) is None     # no such service: a question for the agent
    assert launcher.match("what does bluetooth; rm need", []) is None  # not even looked up
    assert seen == ["bluetooth", "bluetooth", "moon"]


def test_a_picture_word_never_hides_an_app_you_made(home):
    apps.create("How am I connected", "import QtQuick\nItem {}\n")
    a = launcher.match("how am i connected", apps.list_apps())
    assert (a.kind, a.verb) == ("app", "open")


def test_network_alone_still_opens_wifi(home):
    assert launcher.match("network", []).kind == "wifi"


def test_picture_actions_have_words_for_the_line(home):
    a = launcher.match("my disks", [])
    assert launcher.Launcher.doing(a) == "Drawing your disks"
    assert launcher.Launcher.failed(a) == "Could not draw your disks"


# -- what a box in a picture names --

def _drawn(spawned):
    """What the drawer was asked to run: foot's own arguments dropped."""
    return [a[3:] for a in spawned if a[0].endswith("foot")]


def test_a_service_and_a_package_open_in_the_drawer_as_arguments_never_as_shell_text(home, monkeypatch):
    lx, spawned, _ = _drawer(monkeypatch, FakeHypr())
    bomb = launcher._bombadil()
    assert lx.open_thing("unit", "NetworkManager.service") == (True, "Showing NetworkManager.service.")
    assert lx.open_thing("package", "wireguard-tools") == (True, "Showing wireguard-tools.")
    first, second = _drawn(spawned)
    assert first == [bomb, "view", "--", "systemctl", "status", "--no-pager", "-l", "--", "NetworkManager.service"]
    assert second[:5] == [bomb, "view", "--", "sh", "-c"] and "pacman -Qi" in second[5]
    assert "wireguard" not in second[5] and second[-2:] == ["sh", "wireguard-tools"]   # the name is $1, not script


def test_a_folder_a_text_file_and_another_file_each_open_their_own_way(home, monkeypatch):
    lx, spawned, _ = _drawer(monkeypatch, FakeHypr())
    bomb = launcher._bombadil()
    (home / "notes").mkdir()
    (home / "notes" / "todo.txt").write_text("call mum\n")
    (home / "notes" / "photo.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert lx.open_thing("path", str(home / "notes")) == (True, "Showing notes.")
    assert lx.open_thing("path", str(home / "notes" / "todo.txt")) == (True, "Showing todo.txt.")
    assert lx.open_thing("path", str(home / "notes" / "photo.bin")) == (True, "Opened photo.bin.")
    folder, text = _drawn(spawned)[:2]
    assert folder == [bomb, "view", "--", "ls", "-la", "-p", "--", str(home / "notes")]
    assert text == [bomb, "view", "--file", str(home / "notes" / "todo.txt")]
    assert spawned[-1] == ["xdg-open", str(home / "notes" / "photo.bin")]


def test_a_drawer_whose_program_ended_before_its_window_showed_is_not_called_shown(home, monkeypatch):
    h = MappingHypr(after=10 ** 6)
    lx, spawned, _ = _drawer(monkeypatch, h, foot=Foot(4242, exited=1))
    assert lx.open_thing("unit", "NetworkManager.service") == (False, "Could not open NetworkManager.service.")
    (home / "a.txt").write_text("hello")
    assert lx.open_thing("path", str(home / "a.txt")) == (False, "Could not open a.txt.")
    assert h.calls == []                      # nothing was ever slid in


def test_the_drawers_viewer_needs_nothing_installed_beyond_bombadil_itself(home, monkeypatch):
    lx, spawned, _ = _drawer(monkeypatch, FakeHypr())
    monkeypatch.setattr(launcher.shutil, "which", lambda b: "/usr/bin/foot" if b == "foot" else None)   # no less, no pager
    assert lx.open_thing("unit", "sshd.service")[0] is True
    assert "less" not in " ".join(" ".join(a) for a in _drawn(spawned))


def test_a_path_that_is_gone_or_unopenable_says_so(home, monkeypatch):
    lx, spawned, _ = _drawer(monkeypatch, FakeHypr())
    assert lx.open_thing("path", str(home / "nope.txt")) == (False, "nope.txt is not there.")
    (home / "x.bin").write_bytes(b"\x00\x01")
    monkeypatch.setattr(launcher.shutil, "which", lambda b: None if b == "xdg-open" else f"/usr/bin/{b}")
    assert lx.open_thing("path", str(home / "x.bin")) == (False, "Nothing here opens x.bin.")
    assert lx.open_thing("turn", "3")[0] is False and spawned == []


def test_a_tilde_path_opens_in_the_home_folder(home, monkeypatch):
    lx, spawned, _ = _drawer(monkeypatch, FakeHypr())
    (home / "a.txt").write_text("hello")
    assert lx.open_thing("path", "~/a.txt") == (True, "Showing a.txt.")
    assert _drawn(spawned)[0] == [launcher._bombadil(), "view", "--file", str(home / "a.txt")]


def test_a_page_opens_in_the_browser_panel_the_way_every_other_link_does(monkeypatch):
    h = FakeHypr()
    lx = launcher.Launcher(hyprland=h, snaps=Snaps(0))
    opened = []
    monkeypatch.setattr(launcher.browser, "open_url", lambda url, hyprland=None, **kw: opened.append((url, hyprland)))
    assert lx.open_thing("url", "https://www.wireguard.com/quickstart/") == (True, "Opened the page in the browser.")
    assert opened == [("https://www.wireguard.com/quickstart/", h)]


def test_a_page_the_browser_would_not_open_says_why(monkeypatch):
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))

    def refuse(url, hyprland=None, **kw):
        raise RuntimeError("the browser is still starting")

    monkeypatch.setattr(launcher.browser, "open_url", refuse)
    assert lx.open_thing("url", "https://example.com/") == (False, "Could not open the page: the browser is still starting.")


def test_text_is_told_from_binary_by_its_first_bytes(tmp_path):
    (tmp_path / "t").write_text("héllo wörld\n" * 500)           # multi-byte characters may straddle the 4 KB edge
    (tmp_path / "b").write_bytes(b"MZ\x90\x00\x03")
    (tmp_path / "l").write_bytes(b"caf\xe9 latin-1")
    assert launcher._is_text(tmp_path / "t") and not launcher._is_text(tmp_path / "b")
    assert not launcher._is_text(tmp_path / "l") and not launcher._is_text(tmp_path / "missing")


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


# -- mail --

@pytest.mark.parametrize("text, verb", [
    ("mail", "open"), ("Mail.", "open"), ("email", "open"), ("e-mail", "open"), ("inbox", "open"),
    ("open my mail", "open"), ("show the inbox", "open"), ("go to my email", "open"),
    ("close mail", "close"), ("quit the inbox", "close"), ("hide mail", "hide"), ("put away the inbox", "hide"),
])
def test_the_mail_words_open_the_mail_window_and_nothing_else_does(home, text, verb):
    a = launcher.match(text, _apps(home))
    assert a is not None and (a.kind, a.target, a.verb, a.title) == ("mail", "mail", verb, "Mail")


@pytest.mark.parametrize("text", [
    "check my mail", "mail priya that i am late", "what is in my inbox?", "read my email",
    "send mail to priya", "!mail", "mailbox", "inbox zero", "open my mail and reply to priya", "show me my inbox",
    "email priya", "reply to the mail from leo", "send", "send it",
])
def test_a_sentence_about_mail_is_the_agents(home, text):
    assert launcher.match(text, _apps(home)) is None


@pytest.mark.parametrize("text", ["send", "Send it", "send it.", "SEND THAT!", "yes send", "yes, send it", " send this "])
def test_a_bare_send_is_a_send_word(text):
    assert launcher.is_send_word(text)


@pytest.mark.parametrize("text", ["send it?", "send it to priya", "please send", "!send it", "send me the file",
                                  "", "sendit", "send it now", "don't send it", "senden", "send it please"])
def test_anything_more_than_send_is_not(text):
    assert not launcher.is_send_word(text)


def test_mail_is_in_the_entries_once_with_or_without_the_kit_app(home):
    def mails(app_list):
        return [e for e in launcher.entries(app_list) if e["name"] == "mail"]
    [e] = mails([])
    assert e == {"name": "mail", "title": "Mail", "kind": "app", "words": ["mail", "email", "inbox"]}
    apps.create("Mail", "import QtQuick\nItem {}\n")         # an app of yours with the same name
    assert len(mails(launcher.known_apps())) == 1


def test_the_line_says_what_mail_is_doing_and_what_went_wrong(home):
    doing, failed = launcher.Launcher.doing, launcher.Launcher.failed
    assert doing(launcher.match("mail", [])) == "Opening Mail"
    assert doing(launcher.match("hide mail", [])) == "Putting Mail away"
    assert doing(launcher.Action("send")) == "Sending is yours"
    assert failed(launcher.match("mail", [])) == "Could not open Mail"
    assert failed(launcher.match("close mail", [])) == "Could not close Mail"


def test_opening_mail_asks_the_service_for_every_inbox_and_slides_the_window_in(home, mail, monkeypatch):
    asked = _placing(monkeypatch, "mail shown")
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))
    assert lx.run(launcher.match("inbox", [])) == (True, "Opened Mail.")
    assert [(r["op"], r["view"]) for r in mail.asked("show")] == [("show", "all")]
    assert asked == [("show", "mail")]


def test_a_view_and_a_draft_are_asked_for_as_they_are_given(home, mail, monkeypatch):
    asked = _placing(monkeypatch, "mail shown")
    launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).open_mail(id="a1/k1", reply="d1")
    [req] = mail.asked("show")
    assert (req["id"], req["reply"], "view" in req) == ("a1/k1", "d1", False)
    assert asked == [("show", "mail")]


def test_mail_opens_when_the_service_is_not_there_and_the_window_says_so_itself(home, monkeypatch):
    asked = _placing(monkeypatch, "mail shown")
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))
    assert lx.run(launcher.match("mail", [])) == (True, "Opened Mail.")
    assert asked == [("show", "mail")]


def test_a_service_that_hangs_holds_the_open_for_a_second_at_most(home, mail, monkeypatch):
    import time
    monkeypatch.setattr(launcher, "MAIL_SHOW_SECONDS", 0.2)
    _placing(monkeypatch, "mail shown")
    mail.delay("show", "hang")
    t = time.monotonic()
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).run(launcher.match("mail", [])) == (True, "Opened Mail.")
    assert time.monotonic() - t < 2


def test_a_service_that_says_no_does_not_stop_the_window(home, mail, monkeypatch):
    asked = _placing(monkeypatch, "mail shown")
    mail.fail("show", "The views are all, needs_reply, drafts and acct:<id>.", "bad_request")
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).run(launcher.match("mail", []))[0] is True
    assert asked == [("show", "mail")]


def test_mail_without_the_app_kit_starts_its_window_once_and_then_brings_it_forward(home, monkeypatch):
    h = FakeHypr()
    started = []
    monkeypatch.setattr(launcher, "_placement", lambda: None)
    monkeypatch.setattr(launcher.apps, "run", lambda name: started.append(name))
    lx = launcher.Launcher(hyprland=h, snaps=Snaps(0))
    monkeypatch.setattr(launcher, "_app_running", lambda name: False)
    assert lx.run(launcher.match("mail", [])) == (True, "Opened Mail.")
    monkeypatch.setattr(launcher, "_app_running", lambda name: True)
    lx.run(launcher.match("mail", []))
    assert started == ["mail"] and h.placed == ["mail"]
    assert h.calls == [("dispatch", 'hl.dsp.focus({ window = "class:^(bombadil-app-mail)$" })')]


@pytest.mark.parametrize("text, verb, said, expected", [
    ("hide mail", "hide", "mail hidden", (True, "Put Mail away.")),
    ("close mail", "close", "mail closed", (True, "Closed Mail.")),
    ("close mail", "close", "mail is not running", (True, "Mail is not running.")),
])
def test_putting_mail_away_or_closing_it_is_the_kit_apps_own_doing(home, monkeypatch, text, verb, said, expected):
    asked = _placing(monkeypatch, said)
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).run(launcher.match(text, [])) == expected
    assert asked == [(verb, "mail")]


def test_an_app_of_yours_called_mail_does_not_stand_in_for_the_window_that_sends(home, mail, monkeypatch):
    # app_dir would run the app of yours instead of the real one, and whatever Send it draws would be a press.
    apps.create("Mail", "import QtQuick\nItem {}\n")
    asked = _placing(monkeypatch, "mail shown")
    ok, text = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).run(launcher.match("mail", []))
    assert not ok and text.startswith("Could not open Mail: an app of yours called mail stands in front of Mail")
    assert asked == []


def test_send_is_answered_with_where_the_button_is_and_nothing_is_sent(home, mail):
    lx = launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0))
    assert lx.run(launcher.Action("send")) == (True, "Sending is yours. It's under the pointer.")
    assert mail.requests == [] and mail.connections == 0

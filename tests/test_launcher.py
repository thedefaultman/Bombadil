import json

import pytest

from bombadil import apps, launcher, snapshots


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

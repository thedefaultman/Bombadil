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


def test_undo_goes_back_one_turn_at_a_time_until_a_new_turn(home):
    s = Snaps(3)
    lx = launcher.Launcher(snaps=s)
    ok, text = lx.run(launcher.Action("undo"))
    assert ok and s.rolled == [3]
    assert "“prompt 3”" in text and "restart" in text and "home folder" in text
    lx.run(launcher.Action("undo"))
    assert s.rolled == [3, 2]
    lx.clear_undo()                   # agentd does this when a new turn starts
    s.n = 4
    lx.run(launcher.Action("undo"))
    assert s.rolled == [3, 2, 4]


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
    assert not ok and text == "Could not open: chromium is not installed"
    assert "\n" not in text


def test_details_drawer_runs_in_foot_and_slides_in(home, monkeypatch):
    h = FakeHypr()
    spawned, ran = [], []
    monkeypatch.setattr(launcher.shutil, "which", lambda b: f"/usr/bin/{b}")
    lx = launcher.Launcher(hyprland=h, snaps=Snaps(0), runner=lambda *a, **k: ran.append(a[0]),
                           spawn=lambda argv, **k: spawned.append(argv))
    lx.details(["bombadil", "watch"])
    assert spawned == [["/usr/bin/foot", "--app-id=bombadil-details", "--title=Details", "bombadil", "watch"]]
    assert ran == [["pkill", "-f", "--app-id=bombadil-details"]]
    assert h.calls == [("dispatch", 'hl.dsp.focus({ workspace = "special:details" })')]

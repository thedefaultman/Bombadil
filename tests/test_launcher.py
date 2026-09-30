import json

import pytest

from bombadil import apps, desk, launcher, snapshots
from bombadil.brain import client as brain_client


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


# -- the brain's words --

@pytest.mark.parametrize("text, kind", [
    ("brain", "brain"), ("Brain", "brain"), ("the brain", "brain"), ("open the brain", "brain"),
    ("show the brain", "brain"), ("open brain", "brain"), ("Brain.", "brain"),
    ("why is this here?", "why"), ("Why is this here", "why"), ("where did this come from?", "why"),
    ("where is this from", "why"), ("who made this?", "why"), ("What made this?", "why"),
])
def test_the_brains_words_open_locally(home, text, kind):
    a = launcher.match(text, _apps(home))
    assert a is not None and a.kind == kind


@pytest.mark.parametrize("text", [
    "brain surgery", "why is this here and what does it do", "who made this app?", "the brain is slow",
    "why is this file here", "!brain", "почему brain",
])
def test_sentences_about_the_brain_go_to_the_agent(home, text):
    assert launcher.match(text, _apps(home)) is None


def test_the_brain_word_wins_over_an_app_but_not_over_a_core_command(home):
    apps.create("Brain", "import QtQuick\nItem {}\n")
    assert launcher.match("brain").kind == "brain"
    assert launcher.match("undo").kind == "undo"


def test_brain_is_offered_for_tab_but_the_questions_are_not(home):
    e = {x["name"]: x for x in launcher.entries(_apps(home))}
    assert e["brain"]["kind"] == "command" and e["brain"]["title"] == "Brain" and e["brain"]["words"] == ["brain"]
    assert "why" not in e


class Brain:
    """brain_client.request as the launcher sees it: answers, or a brain that is not there."""

    def __init__(self, answers=None, down=False, error=None, slow=False):
        self.answers = answers or {}
        self.down = down
        self.error = error
        self.slow = slow
        self.asked = []

    def __call__(self, op, timeout=2.0, **args):
        self.asked.append((op, args))
        if self.slow:
            try:
                raise TimeoutError("timed out")
            except TimeoutError as e:
                raise brain_client.BrainUnavailable("timed out") from e
        if self.down:
            raise brain_client.BrainUnavailable("[Errno 2] No such file or directory")
        if self.error:
            raise brain_client.BrainError(self.error)
        return self.answers.get(op)


@pytest.fixture
def brain_lx(home, monkeypatch):
    """A launcher whose "this", brain and app runner are fakes."""
    state = {"this": None, "opened": [], "running": False}
    h = FakeHypr()

    def resolve(hypr=None):
        state["hypr"] = hypr
        return state["this"]
    monkeypatch.setattr(launcher.this, "resolve", resolve)
    monkeypatch.setattr(launcher, "_placement", lambda: None)
    monkeypatch.setattr(launcher, "_app_running", lambda name: state["running"])
    monkeypatch.setattr(launcher.apps, "run", lambda name: state["opened"].append(name))

    def use(brain):
        monkeypatch.setattr(launcher.brain_client, "request", brain)
        return brain
    state["use"] = use
    state["hypr_obj"] = h
    return launcher.Launcher(hyprland=h, snaps=Snaps(0)), state


def test_brain_opens_the_brain_on_what_is_in_front(home, brain_lx):
    lx, state = brain_lx
    lease = str(home / "Downloads" / "lease-2026.pdf")
    state["this"] = lease
    b = state["use"](Brain({"show": {"ref": lease, "seq": 3, "title": "lease-2026.pdf"}}))
    assert lx.run(launcher.match("brain")) == (True, "Opened the Brain on lease-2026.pdf.")
    assert b.asked == [("show", {"ref": lease})]
    assert state["opened"] == ["brain"] and state["hypr"] is state["hypr_obj"]
    # The title can come inside the thing, and a long one is cut to fit the line.
    b.answers["show"] = {"ref": lease, "thing": {"title": "A  very\nlong title " + "x" * 80}}
    ok, text = lx.run(launcher.Action("brain"))
    assert ok and text.startswith("Opened the Brain on A very long title x") and text.endswith("….")
    assert len(text) < 90


def test_brain_with_nothing_in_front_opens_on_home(home, brain_lx):
    lx, state = brain_lx
    b = state["use"](Brain({"show": {"ref": str(home), "title": "Home"}}))
    assert lx.run(launcher.Action("brain")) == (True, "Opened the Brain.")
    assert b.asked == [("show", {"ref": str(home)})]
    assert state["opened"] == ["brain"]


def test_an_open_brain_comes_forward_instead_of_starting_twice(home, brain_lx):
    lx, state = brain_lx
    state["running"] = True
    state["use"](Brain({"show": {"ref": str(home)}}))
    assert lx.run(launcher.Action("brain")) == (True, "Opened the Brain.")
    assert state["opened"] == []
    assert state["hypr_obj"].calls == [("dispatch", 'hl.dsp.focus({ window = "class:^(bombadil-app-brain)$" })')]


def test_the_brain_slides_in_from_its_drawer_when_the_kit_has_drawers(home, brain_lx, monkeypatch):
    lx, state = brain_lx
    shown = []

    class Placement:
        @staticmethod
        def show(name):
            shown.append(name)
    monkeypatch.setattr(launcher, "_placement", lambda: Placement)
    state["use"](Brain({"show": {"ref": str(home)}}))
    assert lx.run(launcher.Action("brain"))[0] is True
    assert shown == ["brain"] and state["opened"] == []


def test_brain_says_so_when_the_brain_is_not_running(home, brain_lx):
    lx, state = brain_lx
    state["this"] = "app:passwords"
    state["use"](Brain(down=True))
    assert lx.run(launcher.Action("brain")) == (False, "The brain is not running yet.")
    assert state["opened"] == []     # nothing to open it on
    state["use"](Brain(error="The brain could not open that."))
    assert lx.run(launcher.Action("brain")) == (False, "The brain could not open that.")
    assert state["opened"] == []


def test_a_brain_window_that_cannot_open_is_one_plain_line(home, brain_lx, monkeypatch):
    lx, state = brain_lx
    state["use"](Brain({"show": {"ref": str(home)}}))

    def missing(name):
        raise FileNotFoundError(f"no app named {name!r}")
    monkeypatch.setattr(launcher.apps, "run", missing)
    assert lx.run(launcher.Action("brain")) == (False, "Could not open the Brain: no app named 'brain'")


def test_why_answers_in_the_line_without_a_model(home, brain_lx):
    lx, state = brain_lx
    script = str(home / "setup-wg.sh")
    state["this"] = script
    said = "Made by the machine in turn 41, “install the VPN”, on Monday. Nothing has opened it since."
    b = state["use"](Brain({"why": said}))
    assert lx.run(launcher.match("why is this here?")) == (True, said)
    assert b.asked == [("why", {"ref": script})]
    b.answers["why"] = {"text": "You made it in the terminal on 3 Sep."}
    assert lx.run(launcher.Action("why")) == (True, "You made it in the terminal on 3 Sep.")
    b.answers["why"] = None
    assert lx.run(launcher.Action("why")) == (True, "The brain does not know this yet.")
    assert state["opened"] == []     # the Brain window stays where it is


def test_why_with_nothing_in_front_or_no_brain(home, brain_lx):
    lx, state = brain_lx
    b = state["use"](Brain({"why": "x"}))
    assert lx.run(launcher.Action("why")) == (False, "Nothing is in front to ask about.")
    assert b.asked == []
    state["this"] = "https://rent.example/lease"
    state["use"](Brain(down=True))
    assert lx.run(launcher.Action("why")) == (False, "The brain is not running yet.")
    state["use"](Brain(error="The brain does not know this yet."))
    assert lx.run(launcher.Action("why")) == (False, "The brain does not know this yet.")
    # Busy (its first index) is not the same as not running.
    state["use"](Brain(slow=True))
    assert lx.run(launcher.Action("why")) == (False, "The brain did not answer in time.")
    assert lx.run(launcher.Action("brain")) == (False, "The brain did not answer in time.")


def test_the_line_while_the_brain_works(home):
    assert launcher.Launcher.doing(launcher.Action("brain")) == "Opening the Brain"
    assert launcher.Launcher.doing(launcher.Action("why")) == "Looking it up"
    assert launcher.Launcher.failed(launcher.Action("brain")) == "Could not open the Brain"
    assert launcher.Launcher.failed(launcher.Action("why")) == "Could not look it up"


def test_a_stalled_compositor_makes_the_request_raise_instead_of_hanging(home, monkeypatch, tmp_path):
    """"this" asks Hyprland from the launcher's thread: a compositor that accepts and never
    answers must cost a raised TimeoutError, not that thread for good."""
    import socket
    import time

    from bombadil import hypr

    real_socket, waits = socket.socket, []

    class Quick(real_socket):
        def settimeout(self, value):
            waits.append(value)
            super().settimeout(min(value, 0.3) if value else value)   # the test is not to take 5 s

    run = tmp_path / "x"
    (run / "hypr" / "s").mkdir(parents=True)
    server = real_socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(run / "hypr" / "s" / ".socket.sock"))
    server.listen(1)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "s")
    monkeypatch.setattr(hypr.socket, "socket", Quick)
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            hypr.Hyprland().request("j/activewindow")
        assert time.monotonic() - t0 < 2 and waits and all(0 < w <= 10 for w in waits)
    finally:
        server.close()


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



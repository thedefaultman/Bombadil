"""The launcher's words for shaping the desk: "keep machine", "keep machine on the desk" and "widgets".
Answered here, offline, without the model; anything that is a sentence goes to the agent."""

import pytest
from test_launcher import FakeHypr, Snaps

from bombadil import apps, desk, launcher


def _lx():
    return launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0), desk=desk.Desk())


@pytest.mark.parametrize("text, target", [
    ("keep machine", "machine"),
    ("Keep Machine", "machine"),
    ("keep the machine", "machine"),
    ("keep machine on the desk", "machine"),
    ("keep machine on my desk", "machine"),
    ("keep the machine on the desk.", "machine"),
    ("  keep   machine  ", "machine"),
    ("keep watching", "watching"),
    ("keep now", "now"),
    ("keep route", "now"),
    ("keep needs you", "needs"),
    ("keep needs", "needs"),
    ("keep away", "away"),
    ("keep while you were away", "away"),
])
def test_keep_and_a_widget_is_the_desk_keeping_it(home, text, target):
    a = launcher.match(text, [])
    assert (a.kind, a.target, a.verb, a.title) == ("widget", target, "keep", desk.title(target))


@pytest.mark.parametrize("text", [
    # Alive has no row on the Widgets card yet (it waits for its feed), so these are the agent's sentences.
    "keep alive", "keep alive on the desk",
    # A question asks, a sentence is the agent's, and a verb alone is nothing.
    "keep machine?", "keep it", "keep", "keep machine please", "keep machine on", "keep machine on the right",
    "keep an eye on the machine", "keep the machine running", "keep machine on the desk please",
    "keep desk", "keep the desk open", "keep on the desk", "keep widgets", "keep my widgets",
    "hold machine", "pin machine", "keep machine away",
    # Another script or a sign in it is for the agent, as for every launcher word.
    "keep machiné", "keep «machine»", "keep machine ✓",
])
def test_a_sentence_with_keep_in_it_is_the_agents(home, text):
    assert launcher.match(text, []) is None


@pytest.mark.parametrize("text", [
    "widgets", "Widgets", "WIDGETS.", "my widgets", "the widgets", "show widgets", "show my widgets",
    "show the widgets", "open widgets", "open my widgets", "open the widgets",
])
def test_widgets_opens_the_widgets_card(home, text):
    a = launcher.match(text, [])
    assert (a.kind, a.target) == ("widgets", "")


@pytest.mark.parametrize("text", [
    "widgets?", "widget", "show me the widgets", "what are widgets", "which widgets are on", "hide widgets",
    "close widgets", "put widgets away", "widgets please", "show widgets on the desk", "!widgets",
    "keep widgets", "reset the desk", "widgets on the left",
])
def test_anything_more_than_the_words_is_for_the_agent(home, text):
    assert launcher.match(text, []) is None


def test_an_app_you_named_widgets_does_not_shadow_the_card_and_keep_is_never_an_apps(home):
    apps.create("Widgets", "import QtQuick\nItem {}\n")
    apps.create("Machine", "import QtQuick\nItem {}\n")
    assert launcher.match("widgets").kind == "widgets"             # core words always mean the same thing
    assert launcher.match("open widgets").kind == "widgets"
    a = launcher.match("keep machine")
    assert (a.kind, a.target, a.verb) == ("widget", "machine", "keep")      # an app cannot be kept on the desk
    assert launcher.match("machine").kind == "app"                           # and its own name still opens it


def test_widgets_is_a_completion_and_keep_is_not_a_word_to_complete(home):
    e = launcher.entries([])
    assert {"name": "widgets", "title": "Widgets", "kind": "command", "words": ["widgets"]} in e
    assert not any(x["name"].startswith("keep") for x in e)
    # What the pill is told so that "keep ..." hints and completes as "show ..." does: the widgets on the card.
    assert {x["name"]: x["keep"] for x in e if x["kind"] == "widget"} == {
        "now": True, "watching": True, "needs": True, "machine": True, "alive": False, "away": True,
        "tips": True}
    assert not any("keep" in x for x in e if x["kind"] != "widget")


def test_the_words_say_what_they_do_on_the_desk(home):
    lx = _lx()
    assert [lx.run(launcher.match(w, [])) for w in ("keep machine", "keep machine on the desk")] == [
        (True, "Machine stays on the desk."), (True, "Machine is already kept on the desk.")]
    assert lx.desk_state.snapshot()["kept"] == ["machine"]
    # "hide machine" first stops keeping it, then puts it away; "show machine" is the desk as it is.
    assert [lx.run(launcher.match(w, [])) for w in ("hide machine", "hide machine", "show machine")] == [
        (True, "Machine is no longer kept. It still comes up when something needs a look."),
        (True, "Put Machine away."),
        (True, "Here is the machine. It is back on the desk.")]
    assert lx.desk_state.snapshot()["kept"] == []


def test_keeping_a_widget_with_nothing_to_show_at_rest_says_when_it_shows_and_changes_nothing(home):
    lx = _lx()
    assert lx.run(launcher.match("keep watching", [])) == (True, "Watching shows only while something counts.")
    assert lx.run(launcher.match("keep now", [])) == (True, "Now shows only while a turn has steps to follow.")
    assert lx.run(launcher.match("keep needs you", [])) == (
        True, "Needs you shows only while two or more sessions wait.")
    assert lx.desk_state.snapshot()["kept"] == []


def test_keep_watching_changes_nothing_on_disk(home):
    from bombadil import paths
    lx = _lx()
    lx.run(launcher.match("keep watching", []))
    assert not paths.desk_file().exists()


def test_keeping_tells_the_shell_once_and_survives_the_next_start(home):
    lx = _lx()
    told = []
    lx.desk_state.on_change = lambda: told.append(lx.desk_state.snapshot()["kept"])
    lx.run(launcher.match("keep machine", []))
    lx.run(launcher.match("keep machine", []))      # already kept: nobody is told
    lx.run(launcher.match("keep now", []))          # nothing to show at rest: nobody is told
    assert told == [["machine"]]
    assert launcher.Launcher(hyprland=FakeHypr(), snaps=Snaps(0)).desk_state.snapshot()["kept"] == ["machine"]


def test_the_widgets_word_asks_the_card_open_and_changes_nothing(home):
    lx = _lx()
    opened, told = [], []
    lx.desk_state.on_widgets = lambda: opened.append(1)
    lx.desk_state.on_change = lambda: told.append(1)
    assert lx.run(launcher.match("widgets", [])) == (True, "Here are the widgets.")
    assert opened == [1] and told == []


def test_the_line_says_what_it_is_doing_and_what_went_wrong(home):
    doing, failed = launcher.Launcher.doing, launcher.Launcher.failed
    assert doing(launcher.match("keep machine", [])) == "Keeping Machine on the desk"
    assert doing(launcher.match("keep machine on the desk", [])) == "Keeping Machine on the desk"
    assert doing(launcher.match("widgets", [])) == "Opening the widgets"
    assert failed(launcher.match("keep machine", [])) == "Could not keep Machine on the desk"
    assert failed(launcher.match("widgets", [])) == "Could not open the widgets"
    # Unchanged words keep their lines.
    assert doing(launcher.match("hide machine", [])) == "Putting Machine away"
    assert doing(launcher.match("show machine", [])) == "Putting Machine on the desk"


def test_a_desk_that_breaks_is_one_plain_line(home):
    lx = _lx()

    def boom(*a, **k):
        raise OSError("disk on fire")
    lx.desk_state.apply = boom
    assert lx.run(launcher.match("keep machine", [])) == (False, "Could not keep Machine on the desk: disk on fire")
    assert lx.run(launcher.match("widgets", [])) == (False, "Could not open the widgets: disk on fire")

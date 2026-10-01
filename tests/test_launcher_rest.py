"""The words that rest an AI by hand, and Undo skipping the empty restore point of a refused turn."""

import pytest
from test_launcher import Snaps

from bombadil import launcher


@pytest.mark.parametrize("text, target, verb, title", [
    ("pause claude", "claude", "pause", "Claude"),
    ("Pause Claude.", "claude", "pause", "Claude"),
    ("pause codex", "codex", "pause", "Codex"),
    ("pause the ai", "ai", "pause", "the AI"),
    ("pause ai", "ai", "pause", "the AI"),
    ("resume claude", "claude", "resume", "Claude"),
    ("resume codex", "codex", "resume", "Codex"),
    ("Resume the AI", "ai", "resume", "the AI"),
])
def test_pausing_and_resuming_an_ai_are_launcher_words(text, target, verb, title):
    a = launcher.match(text, [])
    assert (a.kind, a.target, a.verb, a.title) == ("rest", target, verb, title)


@pytest.mark.parametrize("text", [
    "pause claude please",            # a sentence is an ask, not a word
    "pause the music",
    "resume",
    "pause",
    "can you pause claude",
    "pause claude and codex",
])
def test_anything_more_than_the_words_goes_to_the_agent(text):
    assert launcher.match(text, []) is None


def test_use_claude_still_picks_the_provider():
    a = launcher.match("use claude", [])
    assert (a.kind, a.target) == ("provider", "claude")


def test_an_app_whose_name_starts_with_pause_still_opens(home):
    from bombadil import apps
    apps.create("Pause Music", "import QtQuick\nItem {}\n")
    a = launcher.match("pause music", apps.list_apps())
    assert (a.kind, a.target) == ("app", "pause-music")


def test_a_turn_refused_at_once_is_skipped_by_undo_and_the_one_before_it_is_taken_back(home, monkeypatch):
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-1")
    s = Snaps(3)                           # turn 3 is the refused one: it changed nothing
    lx = launcher.Launcher(snaps=s)
    before = lx.undo_marker()
    lx.clear_undo()                        # what the turn did when it started
    lx.skip_restore_point(3, before)
    ok, text = lx.run(launcher.Action("undo"))
    assert ok and s.rolled == [2] and "“prompt 2”" in text


def test_the_undo_the_user_had_made_before_the_refused_turn_carries_on_from_where_it_was(home, monkeypatch):
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-1")
    s = Snaps(4)
    lx = launcher.Launcher(snaps=s)
    lx.run(launcher.Action("undo"))        # took turn 4 back; the next undo goes to 3
    assert s.rolled == [4]
    before = lx.undo_marker()
    lx.clear_undo()                        # a turn starts (it is the one the limit refuses) ...
    s.n = 5                                # ... and takes restore point 5
    lx.skip_restore_point(5, before)
    lx.run(launcher.Action("undo"))
    assert s.rolled == [4, 3]              # not 5: nothing was changed there


def test_an_undo_the_restart_has_applied_is_put_back_too_so_the_next_undo_goes_one_turn_further(home, monkeypatch):
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-1")
    s = Snaps(3)
    lx = launcher.Launcher(snaps=s)
    lx.run(launcher.Action("undo"))
    assert s.rolled == [3]
    before = lx.undo_marker()
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-2")   # restarted: that undo is applied
    lx.clear_undo()                        # the refused turn starts a new history
    assert lx.undo_marker() is None
    s.n = 4
    lx.skip_restore_point(4, before)
    lx.run(launcher.Action("undo"))
    assert s.rolled == [3, 2]              # the same as with no refused turn in between


def test_a_pending_undo_stays_and_covers_the_empty_point_so_it_adds_no_extra_press(home, monkeypatch):
    monkeypatch.setattr(launcher, "_boot_id", lambda: "boot-1")
    s = Snaps(3)
    lx = launcher.Launcher(snaps=s)
    lx.run(launcher.Action("undo"))        # turn 3 taken back, waiting for the restart
    before = lx.undo_marker()
    lx.clear_undo()                        # a pending undo is not cleared
    s.n = 4                                # the refused turn's point
    lx.skip_restore_point(4, before)
    assert lx.undo_marker() == {**before, "covered": 4}
    ok, text = lx.run(launcher.Action("undo"))
    assert ok and "Already undone" not in text and s.rolled == [3, 2]

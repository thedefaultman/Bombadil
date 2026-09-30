import ast
import copy
import itertools
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bombadil import greet, paths
from bombadil.greet import Fact, Greeting, Ledger, build, commit, goodbye, invitation
from bombadil.persona import Persona

VOICES = ("merry", "plain", "quiet")
NAME24 = "Jean-Baptiste Emmanuel Z"
NOW = datetime(2026, 9, 30, 8, 40, tzinfo=UTC)   # a Wednesday morning
TODAY = "2026-09-30"
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def not_live(monkeypatch):
    monkeypatch.setenv("BOMBADIL_LIVE", "0")


def person(voice="merry", name="Daniel", greet_on=True) -> Persona:
    return Persona(name, voice, greet_on)


def at(h, m=0, day=30) -> datetime:
    return datetime(2026, 9, day, h, m, tzinfo=UTC)


def first_of_day() -> Ledger:
    return Ledger(setup_day="2026-09-28", last_boot_day="2026-09-29")


def booted_today() -> Ledger:
    return Ledger(setup_day="2026-09-28", last_boot_day=TODAY)


UPDATES = Fact("updates", "updates", "214 updates are waiting, 4 of them security fixes", "214/4", standing=True)
UPDATES_PLAIN = Fact("updates", "updates", "214 updates are waiting", "214/0", standing=True)
CUT = Fact("dev:batch:stopped", "stopped", "batch was stopped by the restart at 14 of 20", "1759000000.0")
BUILDER = Fact("turn:1", "finished", "builder finished (4 files)")
ISO = Fact("turn:2", "finished", "the ISO download is done")
BATCH = Fact("turn:3", "finished", "batch finished, 300 done, 20 failed")
MADE = Fact("made", "made", "Passwords and Tracker", "Passwords|Tracker")
PUTBACK = Fact("putback:1", "putback", "That change cut the network, so I put it back. Redo it?")
FAILED = Fact("turns-failed", "failed", "A request failed", "1:5.0")
WAITING = Fact("dev:rev:waiting", "waiting", "reviewer is waiting")

# The table of the voice brief (design/voice-brief.md, "The words"), copied so this test stands alone.
# For the name "Daniel": merry, plain, quiet. None is silence.
MOMENTS = {
    "first": ("Welcome, Daniel. Let's go for a walk!", "Welcome, Daniel.", "Welcome, Daniel."),
    "boot-am": ("Good morning, Daniel. Where to today?", "Good morning, Daniel.", None),
    "boot-utc": ("Welcome, Daniel. Shall we wander somewhere?", "Welcome, Daniel.", None),
    "boot-news": ("Good morning, Daniel. 214 updates are waiting, 4 of them security fixes.",
                  "Good morning, Daniel. 214 updates are waiting, 4 of them security fixes.",
                  "214 updates are waiting, 4 of them security fixes."),
    "boot-cut": ("Welcome, Daniel. batch was stopped by the restart at 14 of 20.",
                 "Welcome, Daniel. batch was stopped by the restart at 14 of 20.",
                 "batch was stopped by the restart at 14 of 20."),
    "night": ("Late one, Daniel.", "Welcome, Daniel.", None),
    "back-short": ("While you were away: builder finished (4 files), the ISO download is done.",
                   "While you were away: builder finished (4 files), the ISO download is done.",
                   "While you were away: builder finished (4 files), the ISO download is done."),
    "back-long": ("Welcome back, Daniel. While you were away: batch finished, 300 done, 20 failed.",
                  "Welcome back, Daniel. While you were away: batch finished, 300 done, 20 failed.",
                  "While you were away: batch finished, 300 done, 20 failed."),
    "back-week": ("Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting.",
                  "Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting.",
                  "Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting."),
    "back-none": (None, None, None),
    "bye-night": ("Good night, Daniel. batch stops at 14 of 20 and waits for you.",
                  "Shutting down. batch stops at 14 of 20.", "Shutting down."),
    "bye-day": ("See you, Daniel.", "Shutting down.", "Shutting down."),
    "putback": ("That change cut the network, so I put it back. Redo it?",
                "That change cut the network, so I put it back. Redo it?",
                "That change cut the network, so I put it back. Redo it?"),
    "installed": ("Welcome home, Daniel. Undo works from here.", "Installed. Undo works from here.",
                  "Installed. Undo works from here."),
}

# The empty places of the brief: key and slots in lines.toml, and the line. The two places that draw
# nothing (every window closed, a desk widget with nothing to say) have no line.
EMPTIES = [
    ("focus_folder", {"place": "Receipts"}, "Receipts is empty. Drop files in, or ask for what belongs here."),
    ("focus_moved", {"place": "Downloads", "file": "lease-2026.pdf", "target": "Lease", "when": "Tuesday"},
     "Downloads is empty. Its last file, lease-2026.pdf, went into Lease on Tuesday."),
    ("focus_links", {"since": "27 Sep"},
     "No links yet. The Brain has watched since 27 Sep, and nothing else has touched this."),
    ("focus_match", {"query": "lease", "since": "27 Sep"},
     "Nothing called “lease”. The Brain only knows what happened since 27 Sep."),
    ("map_small", {"areas": 2, "days": 3},
     "The Map is still small: 2 areas after 3 days. It grows as you save, download and ask."),
    ("history", {}, "Nothing to go back to yet. Undo starts after the first change."),
    ("needs_you", {}, "Nothing needs you."),
    ("running", {}, "Nothing is running."),
    ("watching", {}, "Nothing is being watched. Ask me to keep an eye on something and it shows here with a stop button."),
    ("know", {}, "Only your name so far. Tell me what is worth keeping."),
    ("app", {"things": "passwords"}, "No passwords yet. Add one and it stays on this computer."),
    ("apps", {}, "No apps yet. Say what you need and I will make one."),
]
CHIPS = [
    ("focus_folder", {}, "Find what belongs here"),
    ("focus_links", {}, "What is this?"),
    ("focus_match", {"query": "lease"}, "Find lease for me"),
    ("map_small", {}, "files"),
    ("running", {"project": "latchkey"}, "claude latchkey"),
    ("watching", {}, "Tell me if my disk gets nearly full"),
    ("know", {}, "Remember that…"),
    ("apps", {}, "Make me a password manager"),
]


def moment(mid: str, voice: str) -> str | None:
    """The situation of each row of the brief's table, run through build() or goodbye()."""
    p = person(voice)
    if mid == "first":
        g = build("first", p, NOW, ledger=Ledger(), day=0)
    elif mid == "boot-am":
        g = build("boot", p, NOW, ledger=first_of_day(), day=1)
    elif mid == "boot-utc":
        g = build("boot", p, NOW, ledger=first_of_day(), tz_set=False, day=2)
    elif mid == "boot-news":
        g = build("boot", p, NOW, ledger=first_of_day(), facts=[UPDATES], day=3)
    elif mid == "boot-cut":  # a restart: the machine had already booted today
        g = build("boot", p, NOW, ledger=booted_today(), facts=[CUT], day=1)
    elif mid == "night":
        g = build("boot", p, at(2, 30), ledger=first_of_day(), day=1)
    elif mid == "back-short":
        g = build("return", p, at(15), away=2 * 3600, facts=[BUILDER, ISO], ledger=first_of_day(), day=1)
    elif mid == "back-long":
        g = build("return", p, at(15), away=5 * 3600, facts=[BATCH], ledger=first_of_day(), day=1)
    elif mid == "back-week":
        g = build("return", p, NOW, away=8 * 86400, facts=[MADE, UPDATES_PLAIN], ledger=first_of_day(), day=1)
    elif mid == "back-none":
        g = build("return", p, NOW, away=8 * 86400, ledger=first_of_day(), day=1)
    elif mid == "bye-night":
        return goodbye(p, at(22, 15), stopping=["batch stops at 14 of 20"])
    elif mid == "bye-day":
        return goodbye(p, at(14))
    elif mid == "putback":
        g = build("return", p, NOW, away=5 * 3600, facts=[PUTBACK], ledger=first_of_day(), day=1)
    else:
        g = build("installed", p, NOW, ledger=first_of_day(), day=1)
    return g.text if g else None


@pytest.mark.parametrize("mid", MOMENTS)
@pytest.mark.parametrize("voice", VOICES)
def test_golden_every_cell_of_the_brief_is_reproduced(mid, voice):
    assert moment(mid, voice) == MOMENTS[mid][VOICES.index(voice)]


def test_the_golden_table_is_complete():
    assert len(MOMENTS) == 14
    for mid in MOMENTS:
        moment(mid, "merry")


@pytest.mark.parametrize(("key", "slots", "line"), EMPTIES)
def test_golden_every_empty_place_is_reproduced(key, slots, line):
    assert greet.empty(key, **slots) == line


@pytest.mark.parametrize(("key", "slots", "label"), CHIPS)
def test_golden_every_doorway_chip(key, slots, label):
    assert greet.empty_chip(key, **slots) == label


def test_empty_places_follow_the_format():
    for key, slots, line in EMPTIES:
        assert "—" not in line and "–" not in line and "!" not in line, key
        assert not line.lower().startswith("nothing here yet") and len(line) <= 100, key
        assert not re.search(r"\b(?:oops|sorry|hmm)\b|let me", line.lower()), key
        assert "Daniel" not in line and "{" not in line, key
        assert line.count(". ") <= 1, key  # two short sentences at most
    for key in greet.lines()["empty"]:
        assert key.endswith("_live") or any(key == k for k, _, _ in EMPTIES), key


def test_empty_is_none_for_a_place_with_no_line_and_for_a_missing_slot():
    assert greet.empty("rest") is None and greet.empty("") is None
    assert greet.empty("focus_folder") is None  # the place is missing
    assert greet.empty("map_small", areas=2) is None
    assert greet.empty_chip("needs_you") is None and greet.empty_chip("focus_match") is None


def test_empty_counts_one_and_many():
    assert greet.empty("map_small", areas=1, days=1) == (
        "The Map is still small: 1 area after 1 day. It grows as you save, download and ask.")
    assert greet.empty("map_small", areas=0, days=10).startswith("The Map is still small: 0 areas after 10 days.")


def test_the_history_line_changes_on_a_live_usb(monkeypatch):
    monkeypatch.setenv("BOMBADIL_LIVE", "1")
    assert greet.empty("history") == "Nothing to go back to yet. Undo starts once Bombadil is installed."
    assert greet.empty("history_live").endswith("installed.")
    assert greet.empty("running") == "Nothing is running."
    monkeypatch.setenv("BOMBADIL_LIVE", "0")
    assert greet.empty("history").endswith("after the first change.")


# every line of the table, in every voice: a grid of situations

NAMES = ("", "Daniel", NAME24)
FACT_SETS = ((), (UPDATES,), (CUT, UPDATES), (BUILDER, ISO), (BATCH,), (MADE, UPDATES_PLAIN), (PUTBACK,), (FAILED,),
             (WAITING, BUILDER), (BUILDER, MADE), (UPDATES, CUT, BATCH, MADE, FAILED))


def grid():
    for moment_, voice, name, facts, zone, hour, away, day in itertools.product(
            ("first", "boot", "return", "installed"), VOICES, ("", NAME24), FACT_SETS, (True, False), (2, 8, 21),
            (None, 1800, 7200, 5 * 3600, 8 * 86400), (0, 1)):
        if moment_ == "return" and away is None:
            continue
        ledger = first_of_day() if day % 2 else booted_today()
        g = build(moment_, person(voice, name), at(hour), facts=facts, ledger=ledger, tz_set=zone, away=away, day=day)
        yield moment_, voice, name, facts, g


def test_every_line_obeys_the_brief():
    count = 0
    for moment_, voice, name, facts, g in grid():
        if g is None:
            continue
        count += 1
        t = g.text
        where = (moment_, voice, name, [f.kind for f in facts], t)
        assert 0 < len(t) <= 100 and t == t.strip() and "\n" not in t, where
        assert "{" not in t and "}" not in t, where
        assert "—" not in t and "–" not in t, where
        assert t.count("!") == (1 if t.endswith("!") else 0) and (voice == "merry" or "!" not in t), where
        assert not re.search(r"\b(?:oops|sorry|hmm)\b|let me|nothing here yet", t.lower()), where
        assert not re.search(r"\b(?:will|tomorrow|later)\b", t.lower()), where
        assert t.endswith((".", "?", "!")), where
        assert not re.search(r" ,|,,|\.\.|,\.|\s\s|^[,.]|, [.?!]", t), where
        assert t.count(name) <= 1 if name else True, where
        if not name:
            assert "Daniel" not in t and not re.search(r"\b(?:friend|user)\b", t), where
        if g.facts and g.facts[0].kind in ("putback", "failed"):
            assert name not in t or not name, where  # a failure outranks a hello: no name, no opener
            assert t == g.facts[0].text.rstrip(".") + ("" if g.facts[0].text.endswith("?") else "."), where
        assert g.moment == moment_ and g.first == (moment_ == "first"), where
    assert count > 3000


def test_every_goodbye_obeys_the_brief():
    for voice, name, zone, hour, stopping in itertools.product(
            VOICES, NAMES, (True, False), range(24),
            ([], ["batch stops at 14 of 20"], ["a stops at 1 of 2", "b stops at 3 of 4", "c stops at 5 of 6"],
             ["x" * 60, "y" * 60], ["z" * 120])):
        t = goodbye(person(voice, name), at(hour), stopping=stopping, tz_set=zone)
        where = (voice, name, zone, hour, stopping, t)
        assert 0 < len(t) <= 100 and "{" not in t and "—" not in t and "–" not in t and "\n" not in t, where
        assert t.count("!") == 0 or voice == "merry", where
        assert not re.search(r" ,|,,|\.\.|,\.|\s\s|^[,.]", t), where
        assert ("Daniel" not in t) if not name else t.count(name) <= 1, where
        if "Good night" in t:
            assert zone and (hour >= 20 or hour < 5) and voice == "merry", where


def test_no_english_in_the_code_beyond_what_the_table_holds():
    tree = ast.parse(Path(greet.__file__).read_text())
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    words = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in docstrings and re.search(r"[A-Za-z]{2,}[ ,.][A-Za-z]{2,}", n.value)]
    assert words == []


def test_the_shipped_table_has_every_cell():
    table = greet.lines()
    assert paths.voice_lines() == Path(greet.__file__).resolve().parents[2] / "share" / "voice" / "lines.toml"
    assert set(table) >= {"voices", "card", "opener", "join", "clause", "invite", "fact", "merry", "plain", "quiet",
                          "empty", "empty_chip"}
    assert set(table["opener"]) == {"welcome", "back", "morning", "afternoon", "evening", "late"}
    assert len(table["invite"]["pool"]) == 7 and table["invite"]["walk"] == "Let's go for a walk!"
    for voice in VOICES:
        assert "installed" in table[voice] and set(table[voice]["bye"]) == {"night", "day", "none", "stop"}
        for ctx in ("first", "boot", "night", "long", "back", "away"):
            assert "facts" in table[voice][ctx], (voice, ctx)
    for key in ("updates", "security", "stopped", "stopped_at", "finished", "failed", "requests_finished",
                "requests_failed", "more"):
        assert key in table["fact"]


def test_the_table_has_no_dash_and_a_bang_only_in_merry():
    def strings(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from strings(v, (*path, k))
        elif isinstance(node, list):
            for v in node:
                yield path, v
        else:
            yield path, node

    for path, s in strings(greet.lines()):
        assert "—" not in s and "–" not in s, path
        if "!" in s:
            assert path[0] in ("merry", "card", "invite"), path
    for v in ("plain", "quiet"):
        assert all("!" not in s for _, s in strings(greet.lines()[v]))
    assert [s for p, s in strings(greet.lines()) if "!" in s and p[0] in ("card", "invite")] == [
        "Welcome{n}. Let's go for a walk!", "Let's go for a walk!"]


# the opener table

def text(moment_, voice="merry", hour=8, **kw):
    kw.setdefault("ledger", first_of_day())
    g = build(moment_, person(voice), at(hour), **kw)
    return g.text if g else None


@pytest.mark.parametrize("voice", VOICES)
def test_the_first_hello_is_welcome_at_any_hour(voice):
    for hour in (0, 3, 8, 13, 19, 23):
        assert text("first", voice, hour, ledger=Ledger()).startswith("Welcome, Daniel.")


@pytest.mark.parametrize(("hour", "word"), [
    (5, "Good morning"), (8, "Good morning"), (11, "Good morning"), (12, "Good afternoon"), (17, "Good afternoon"),
    (18, "Good evening"), (23, "Good evening")])
def test_a_time_of_day_word_on_the_first_boot_of_the_day_with_a_zone(hour, word):
    for voice in ("merry", "plain"):
        assert text("boot", voice, hour, day=1).startswith(f"{word}, Daniel.")


def test_the_minutes_do_not_move_the_boundaries():
    assert text("boot", "plain", 11).startswith("Good morning")
    assert build("boot", person("plain"), at(11, 59), ledger=first_of_day()).text == "Good morning, Daniel."
    assert build("boot", person("plain"), at(12, 0), ledger=first_of_day()).text == "Good afternoon, Daniel."
    assert build("boot", person("plain"), at(17, 59), ledger=first_of_day()).text == "Good afternoon, Daniel."
    assert build("boot", person("plain"), at(18, 0), ledger=first_of_day()).text == "Good evening, Daniel."
    assert build("boot", person("plain"), at(4, 59), ledger=first_of_day()).text == "Welcome, Daniel."
    assert build("boot", person("plain"), at(5, 0), ledger=first_of_day()).text == "Good morning, Daniel."


@pytest.mark.parametrize("voice", ("merry", "plain"))
def test_a_second_boot_on_the_same_day_is_welcome(voice):
    assert text("boot", voice, 14, ledger=booted_today()).startswith("Welcome, Daniel.")


def test_a_boot_with_no_ledger_yet_counts_as_the_first_of_the_day():
    assert build("boot", person("plain"), NOW, ledger=Ledger()).text == "Good morning, Daniel."
    assert build("boot", person("plain"), NOW).text == "Good morning, Daniel."


def test_late_one_is_merry_only_between_midnight_and_five_with_a_zone():
    assert [text("boot", "merry", h) for h in (0, 2, 4)] == ["Late one, Daniel."] * 3
    assert text("boot", "merry", 5).startswith("Good morning")
    assert build("boot", person("merry"), at(4, 59), ledger=first_of_day()).text == "Late one, Daniel."
    assert [text("boot", "plain", h) for h in (0, 2, 4)] == ["Welcome, Daniel."] * 3
    assert [text("boot", "quiet", h) for h in (0, 2, 4)] == [None] * 3
    # one aside per boot and no comment on the hour after it: no invitation, even on a second boot that night
    assert text("boot", "merry", 3, ledger=booted_today(), day=2) == "Late one, Daniel."


def test_late_one_keeps_the_news():
    assert text("boot", "merry", 3, facts=[UPDATES]) == "Late one, Daniel. " + UPDATES.text + "."
    assert text("boot", "plain", 3, facts=[UPDATES]) == "Welcome, Daniel. " + UPDATES.text + "."


@pytest.mark.parametrize("voice", VOICES)
def test_with_no_time_zone_set_there_is_only_welcome(voice):
    for hour in range(24):
        for ledger in (first_of_day(), booted_today(), Ledger()):
            for facts in ((), (UPDATES,)):
                g = build("boot", person(voice), at(hour), facts=facts, ledger=ledger, tz_set=False, day=1)
                if g is None:
                    assert voice == "quiet" and not facts
                    continue
                assert not re.search(r"Good (?:morning|afternoon|evening|night)|Late one", g.text), g.text
                if voice != "quiet":
                    assert g.text.startswith("Welcome, Daniel."), g.text


def test_a_return_ignores_the_time_zone_and_says_welcome_back():
    a = text("return", facts=[BATCH], away=5 * 3600, tz_set=True)
    assert a == text("return", facts=[BATCH], away=5 * 3600, tz_set=False)
    assert a.startswith("Welcome back, Daniel.")


# returns: how long away decides the opener

def test_a_return_needs_thirty_minutes_and_something_to_say():
    assert text("return", facts=[BATCH], away=29 * 60) is None
    assert text("return", facts=[BATCH], away=0) is None
    assert text("return", facts=[BATCH]) is None  # no idea how long: nothing
    assert text("return", away=5 * 3600) is None
    assert text("return", away=30 * 60) is None
    assert text("return", facts=[BATCH], away=30 * 60) == "While you were away: " + BATCH.text + "."
    assert text("return", facts=[PUTBACK], away=29 * 60) is None


@pytest.mark.parametrize("voice", VOICES)
def test_a_break_under_four_hours_says_only_the_news(voice):
    for away in (30 * 60, 3600, 3 * 3600, 4 * 3600 - 1):
        assert text("return", voice, 15, facts=[BATCH], away=away) == "While you were away: " + BATCH.text + "."


def test_four_hours_or_a_night_is_welcome_back_but_not_for_quiet():
    want = "Welcome back, Daniel. While you were away: " + BATCH.text + "."
    assert text("return", "merry", 15, facts=[BATCH], away=4 * 3600) == want
    assert text("return", "plain", 15, facts=[BATCH], away=4 * 3600) == want
    assert text("return", "quiet", 15, facts=[BATCH], away=4 * 3600) == "While you were away: " + BATCH.text + "."
    # two hours, but across midnight
    assert text("return", "merry", 1, facts=[BATCH], away=2 * 3600, ledger=Ledger()) == want
    assert text("return", "merry", 1, facts=[BATCH], away=50 * 60) == "While you were away: " + BATCH.text + "."
    assert text("return", "merry", 0, facts=[BATCH], away=50 * 60 * 2) == want  # 22:20 the evening before


def test_three_days_or_more_is_welcome_back_for_every_voice_and_adds_what_you_made():
    for voice in VOICES:
        t = text("return", voice, facts=[MADE], away=3 * 86400)
        assert t == "Welcome back, Daniel. Since last time you built Passwords and Tracker."
        assert text("return", voice, facts=[MADE], away=3 * 86400 - 1) is None  # made is news only after 3 days


def test_what_you_made_is_left_out_before_three_days():
    assert text("return", facts=[MADE, BATCH], away=5 * 3600) == (
        "Welcome back, Daniel. While you were away: " + BATCH.text + ".")
    assert text("boot", facts=[MADE], day=1) == "Good morning, Daniel. Where to today?"


def test_a_boot_after_three_days_is_welcome_back_from_the_ledger():
    led = Ledger(setup_day="2026-09-01", last_boot_day="2026-09-27")
    assert text("boot", "plain", ledger=led) == "Welcome back, Daniel."
    assert text("boot", "quiet", ledger=led) == "Welcome back, Daniel."
    assert text("boot", "merry", ledger=led, day=29).startswith("Welcome back, Daniel. ")
    assert text("boot", "merry", ledger=led, facts=[MADE, UPDATES_PLAIN]) == (
        "Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting.")
    two = Ledger(last_boot_day="2026-09-28")
    assert text("boot", "plain", ledger=two) == "Good morning, Daniel."
    assert text("boot", "plain", ledger=two, away=3 * 86400) == "Welcome back, Daniel."  # an explicit time wins
    assert text("boot", "plain", ledger=led, away=3600) == "Good morning, Daniel."
    assert text("boot", "plain", ledger=Ledger(last_boot_day="garbage")) == "Good morning, Daniel."


# the three voices

def test_merry_adds_an_invitation_with_no_news_plain_adds_nothing_quiet_says_nothing():
    assert text("boot", "merry", day=1) == "Good morning, Daniel. Where to today?"
    assert text("boot", "plain", day=1) == "Good morning, Daniel."
    assert text("boot", "quiet", day=1) is None
    for v in VOICES:
        assert text("return", v, away=8 * 86400) is None


def test_with_news_no_voice_adds_an_invitation():
    for v in VOICES:
        t = text("boot", v, facts=[UPDATES], day=1)
        assert "Where to" not in t and t.endswith("security fixes.")


def test_quiet_greets_on_the_first_boot_ever_installed_and_after_three_days_only():
    q = person("quiet")
    assert build("first", q, NOW, ledger=Ledger()).text == "Welcome, Daniel."
    assert build("installed", q, NOW, ledger=Ledger()).text == "Installed. Undo works from here."
    assert build("boot", q, NOW, ledger=first_of_day()) is None
    assert build("boot", q, NOW, ledger=booted_today(), facts=[BUILDER]).text == BUILDER.text + "."
    assert build("boot", q, NOW, ledger=Ledger(last_boot_day="2026-09-20")).text == "Welcome back, Daniel."
    for facts in ([UPDATES], [BATCH], [CUT], [BUILDER, ISO]):
        for hour in (1, 8, 15, 22):
            t = build("boot", q, at(hour), ledger=first_of_day(), facts=facts).text
            assert "Daniel" not in t and not t.startswith(("Welcome", "Good", "Late"))


def test_the_news_is_worded_the_same_in_every_voice():
    for facts in ([UPDATES], [CUT], [BUILDER, ISO]):
        bodies = set()
        for v in VOICES:
            t = build("boot", person(v), NOW, ledger=booted_today(), facts=facts).text
            bodies.add(t.removeprefix("Welcome, Daniel. "))
        assert len(bodies) == 1


def test_the_first_hello_carries_the_first_flag_and_the_day_zero_invitation():
    g = build("first", person("merry"), NOW, ledger=Ledger(), day=0)
    assert g.first and g.moment == "first" and g.text.endswith("Let's go for a walk!")
    assert not build("boot", person("merry"), NOW, ledger=first_of_day()).first
    assert build("first", person("merry"), NOW, ledger=Ledger(), day=1).text.endswith("Where to today?")


def test_the_first_hello_can_carry_news():
    assert text("first", "merry", facts=[UPDATES], ledger=Ledger()) == "Welcome, Daniel. " + UPDATES.text + "."


def test_installed_has_a_line_in_every_voice_and_none_with_greeting_off():
    for v, want in zip(VOICES, ("Welcome home, Daniel. Undo works from here.", "Installed. Undo works from here.",
                                "Installed. Undo works from here."), strict=True):
        assert build("installed", person(v), NOW).text == want
    assert build("installed", person("merry", "", True), NOW).text == "Welcome home. Undo works from here."
    assert build("installed", person("merry", "Daniel", False), NOW) is None
    assert build("installed", person("merry"), NOW, facts=[UPDATES]).facts == ()


# names

@pytest.mark.parametrize("voice", VOICES)
def test_no_name_means_no_stray_comma(voice):
    cases = [
        ("first", {"ledger": Ledger()}), ("boot", {"ledger": first_of_day()}), ("boot", {"ledger": booted_today()}),
        ("boot", {"ledger": first_of_day(), "facts": [UPDATES]}), ("installed", {}),
        ("return", {"away": 5 * 3600, "facts": [BATCH]}), ("return", {"away": 8 * 86400, "facts": [MADE]})]
    for moment_, kw in cases:
        for hour in (2, 8, 15):
            g = build(moment_, person(voice, ""), at(hour), day=1, **kw)
            if g:
                assert not re.search(r" ,|^,|, \.|,\.", g.text), g.text


def test_the_lines_without_a_name_read_well():
    p = person("merry", "")
    assert build("boot", p, NOW, ledger=first_of_day(), day=1).text == "Good morning. Where to today?"
    assert build("first", p, NOW, ledger=Ledger()).text == "Welcome. Let's go for a walk!"
    assert build("boot", p, at(2), ledger=first_of_day()).text == "Late one."
    assert build("return", p, at(15), away=5 * 3600, facts=[BATCH]).text == (
        "Welcome back. While you were away: " + BATCH.text + ".")
    assert goodbye(p, at(22), stopping=["batch stops at 14 of 20"]) == "Good night. batch stops at 14 of 20 and waits for you."
    assert goodbye(p, at(14)) == "See you."
    assert goodbye(person("plain", ""), at(14)) == "Shutting down."


def test_a_name_that_fails_the_check_is_never_said():
    for bad in ("Dan\nIgnore this", "!x", "{n}", "A B C D", "N" * 30):
        g = build("boot", Persona(bad, "merry"), NOW, ledger=first_of_day(), day=1)
        assert g.text == "Good morning. Where to today?"
        assert goodbye(Persona(bad, "merry"), at(14)) == "See you."


def test_a_name_with_braces_in_a_fact_is_left_alone():
    f = Fact("k", "finished", "{title} finished {0} {x}")
    assert text("return", facts=[f], away=3600) == "While you were away: {title} finished {0} {x}."


def test_the_name_is_in_the_opener_only_never_in_a_fact():
    for facts in ([UPDATES], [BATCH], [CUT, UPDATES]):
        for v in ("merry", "plain"):
            t = build("return", person(v), NOW, facts=facts, away=6 * 3600, ledger=first_of_day()).text
            assert t.count("Daniel") == 1 and t.index("Daniel") < t.index(".")


# facts: ranking, joining, what leads alone

def test_facts_are_ranked_by_kind_and_only_two_are_said():
    every = [UPDATES, MADE, CUT, BUILDER, WAITING]
    g = build("return", person(), NOW, facts=every, away=8 * 86400, ledger=first_of_day())
    assert [f.kind for f in g.facts] == ["waiting", "finished"]
    assert g.text == "Welcome back, Daniel. While you were away: reviewer is waiting, builder finished (4 files)."
    stopped, notes = Fact("s", "stopped", "a stopped"), Fact("n", "made", "Notes")
    g = build("return", person(), NOW, facts=[UPDATES, notes, stopped], away=8 * 86400, ledger=first_of_day())
    assert [f.kind for f in g.facts] == ["stopped", "made"]
    assert g.text == "Welcome back, Daniel. While you were away: a stopped, and since last time you built Notes."
    g = build("return", person(), NOW, facts=[UPDATES_PLAIN, notes], away=8 * 86400, ledger=first_of_day())
    assert [f.kind for f in g.facts] == ["made", "updates"]
    order = [PUTBACK, FAILED, WAITING, BUILDER, CUT, MADE, UPDATES]
    for a, b in itertools.combinations(order, 2):
        g = build("boot", person("plain"), NOW, facts=[b, a], ledger=booted_today(), away=8 * 86400)
        assert g.facts[0] == a


def test_a_fact_of_an_unknown_kind_or_a_repeated_key_is_ignored():
    odd = Fact("x", "gossip", "the weather is nice")
    dup = Fact("updates", "updates", "1 update is waiting", "1/0")
    assert text("boot", "plain", facts=[odd], day=1) == "Good morning, Daniel."
    g = build("boot", person("plain"), NOW, facts=[UPDATES, dup], ledger=first_of_day())
    assert g.facts == (UPDATES,)


def test_two_news_items_share_one_prefix_and_a_comma():
    assert text("return", facts=[BUILDER, ISO], away=3600) == (
        "While you were away: builder finished (4 files), the ISO download is done.")
    assert text("return", facts=[BUILDER, UPDATES_PLAIN], away=3600) == (
        "While you were away: builder finished (4 files), and 214 updates are waiting.")
    assert text("return", facts=[fact("batch finished", "b"), Fact("n", "made", "Notes")], away=8 * 86400) == (
        "Welcome back, Daniel. While you were away: batch finished, and since last time you built Notes.")


def test_two_facts_at_a_boot_are_joined_with_and():
    assert text("boot", "plain", facts=[CUT, UPDATES_PLAIN], ledger=booted_today()) == (
        "Welcome, Daniel. batch was stopped by the restart at 14 of 20, and 214 updates are waiting.")
    assert text("boot", "plain", facts=[BUILDER, CUT], ledger=booted_today()) == (
        "Welcome, Daniel. builder finished (4 files), and batch was stopped by the restart at 14 of 20.")


def test_what_failed_or_was_put_back_leads_the_line_alone():
    for v in VOICES:
        for hour in (2, 8, 15, 22):
            for moment_ in ("first", "boot"):
                t = build(moment_, person(v), at(hour), facts=[UPDATES, PUTBACK, BUILDER], ledger=first_of_day()).text
                assert t == PUTBACK.text
            t = build("return", person(v), at(hour), facts=[UPDATES, FAILED, BUILDER], away=6 * 3600,
                      ledger=first_of_day()).text
            assert t == "A request failed."
    g = build("boot", person(), NOW, facts=[FAILED, PUTBACK], ledger=first_of_day())
    assert g.facts == (PUTBACK,) and g.text == PUTBACK.text


def test_a_failure_is_said_even_when_greeting_is_off():
    p = person("merry", "Daniel", False)
    assert build("boot", p, NOW, facts=[PUTBACK], ledger=first_of_day()).text == PUTBACK.text
    assert build("boot", p, NOW, facts=[UPDATES, BUILDER], ledger=first_of_day()) is None
    assert build("boot", p, NOW, ledger=first_of_day()) is None
    assert build("first", p, NOW, ledger=Ledger()) is None
    assert build("return", p, NOW, facts=[BATCH], away=5 * 3600) is None


def test_an_unknown_moment_and_a_table_that_is_missing_say_nothing(monkeypatch, tmp_path):
    assert build("lunch", person(), NOW, ledger=first_of_day()) is None
    monkeypatch.setenv("BOMBADIL_VOICE_LINES", str(tmp_path / "missing.toml"))
    assert greet.lines() == {}
    assert build("boot", person(), NOW, ledger=first_of_day()) is None
    assert build("installed", person(), NOW) is None
    assert goodbye(person(), NOW) == ""
    assert greet.empty("history") is None and greet.empty_chip("apps") is None
    assert greet.phrase("updates", count=2) == "" and greet.join_names(["a", "b"]) == "a"


def test_a_table_that_is_not_toml_says_nothing(monkeypatch, tmp_path):
    bad = tmp_path / "lines.toml"
    bad.write_text("this is [not toml")
    monkeypatch.setenv("BOMBADIL_VOICE_LINES", str(bad))
    assert greet.lines() == {} and build("boot", person(), NOW, ledger=first_of_day()) is None


def test_an_unknown_voice_reads_as_merry():
    assert build("boot", Persona("Daniel", "shouting"), NOW, ledger=first_of_day(), day=1).text == (
        "Good morning, Daniel. Where to today?")


# the budget

def fact(text_, key="f", kind="finished"):
    return Fact(key, kind, text_)


def test_the_budget_drops_the_second_fact_then_the_name():
    p = person("merry", NAME24)
    assert len(NAME24) == 24
    a, b = fact("a" * 30, "a"), fact("b" * 30, "b")
    g = build("boot", p, NOW, facts=[a, b], ledger=booted_today())
    assert g.text == f"Welcome, {NAME24}. " + "a" * 30 + "." and g.facts == (a,)
    both = f"Welcome, Daniel. {'a' * 30}, and {'b' * 30}."
    assert build("boot", person(), NOW, facts=[a, b], ledger=booted_today()).text == both and len(both) <= 100
    long = fact("c" * 79, "c")  # with the name: over 100. without: fits
    g = build("boot", p, NOW, facts=[long], ledger=booted_today())
    assert g.text == "Welcome. " + "c" * 79 + "." and g.facts == (long,)
    assert len(g.text) <= 100


def test_the_budget_with_two_long_facts_and_a_long_name():
    x, y = fact("x" * 50, "x"), fact("y" * 50, "y")
    g = build("boot", person("merry", NAME24), NOW, facts=[x, y], ledger=booted_today())
    assert g.text == f"Welcome, {NAME24}. " + "x" * 50 + "." and g.facts == (x,)
    g = build("return", person("merry", NAME24), NOW, facts=[x, y], away=5 * 3600, ledger=booted_today())
    assert g.text == "Welcome back. While you were away: " + "x" * 50 + "." and g.facts == (x,)  # the name went too
    cases = (("boot", {}), ("boot", {"away": 8 * 86400}), ("return", {"away": 5 * 3600}),
             ("return", {"away": 8 * 86400}))
    for voice, (kind_moment, kw) in itertools.product(("merry", "plain"), cases):
        for facts in ([x, y], [fact("x" * 45, "x"), fact("y" * 70, "y")], [fact("x" * 60, "x"), fact("y" * 60, "y")]):
            g = build(kind_moment, person(voice, NAME24), NOW, facts=facts, ledger=booted_today(), **kw)
            assert g and len(g.text) <= 100, g
            assert g.facts == (facts[0],), g  # the second fact goes first


def test_the_budget_picks_the_fullest_line_that_fits():
    p = person("plain", NAME24)
    facts = [fact("a" * 20, "a"), fact("b" * 20, "b")]
    g = build("boot", p, NOW, facts=facts, ledger=booted_today())
    assert g.text == f"Welcome, {NAME24}. {'a' * 20}, and {'b' * 20}." and len(g.text) <= 100
    g = build("boot", p, NOW, facts=[fact("a" * 30, "a"), fact("b" * 30, "b")], ledger=booted_today())
    assert g.text == f"Welcome, {NAME24}. {'a' * 30}." and len(g.facts) == 1


def test_a_fact_too_long_for_a_line_leads_bare_and_what_was_left_out_is_not_recorded():
    f = fact("z" * 120, "z")
    g = build("boot", person("merry", NAME24), NOW, facts=[f, fact("w", "w")], ledger=booted_today())
    assert g.text == "z" * 120 + "." and g.facts == (f,)
    led = booted_today()
    commit(led, g, NOW)
    assert "z" in led.said and "w" not in led.said  # the dropped fact is said next time


def test_a_return_prefix_stays_until_the_fact_stands_alone():
    p = person("merry", NAME24)
    g = build("return", p, NOW, facts=[fact("q" * 60, "q")], away=5 * 3600, ledger=booted_today())
    assert g.text == "Welcome back. While you were away: " + "q" * 60 + "." and len(g.text) <= 100
    g = build("return", p, NOW, facts=[fact("q" * 70, "q")], away=5 * 3600, ledger=booted_today())
    assert g.text == "While you were away: " + "q" * 70 + "." and len(g.text) <= 100


def test_the_goodbye_budget_drops_the_second_clause_then_the_name_then_the_clause():
    p = person("merry", NAME24)
    one, two = "a" * 25 + " stops at 1 of 2", "b" * 25 + " stops at 3 of 4"
    t = goodbye(p, at(22), stopping=[one, two])
    assert t == f"Good night, {NAME24}. {one} and waits for you." and len(t) <= 100
    t = goodbye(person("merry", NAME24), at(22), stopping=["c" * 60])
    assert t == "Good night. " + "c" * 60 + " and waits for you." and len(t) <= 100
    t = goodbye(p, at(22), stopping=["d" * 120])
    assert t == f"Good night, {NAME24}."
    short = ["a stops at 1 of 2", "b stops at 3 of 4"]
    assert goodbye(person("plain", NAME24), at(22), stopping=short) == "Shutting down. a stops at 1 of 2 and b stops at 3 of 4."
    assert goodbye(person("plain", NAME24), at(22), stopping=[one, two]) == f"Shutting down. {one}."


# goodbyes

def test_goodbyes_by_voice_and_hour():
    stop = ["batch stops at 14 of 20"]
    assert goodbye(person("merry"), at(14), stopping=stop) == "See you, Daniel. batch stops at 14 of 20 and waits for you."
    assert goodbye(person("merry"), at(22)) == "Good night, Daniel."
    assert goodbye(person("plain"), at(14), stopping=stop) == "Shutting down. batch stops at 14 of 20."
    assert goodbye(person("quiet"), at(22), stopping=stop) == "Shutting down."
    assert goodbye(person("quiet"), at(14)) == "Shutting down."


def test_good_night_is_only_a_goodbye_after_eight_before_five_with_a_zone_set():
    def w(h, zone=True):
        return goodbye(person("merry"), at(h), tz_set=zone)
    assert [w(h) for h in (19, 20, 23, 0, 4, 5, 12)] == [
        "See you, Daniel.", "Good night, Daniel.", "Good night, Daniel.", "Good night, Daniel.",
        "Good night, Daniel.", "See you, Daniel.", "See you, Daniel."]
    assert [w(h, False) for h in (22, 2)] == ["See you, Daniel."] * 2
    assert goodbye(person("merry"), at(19, 59)) == "See you, Daniel."
    assert goodbye(person("merry"), at(20, 0)) == "Good night, Daniel."
    assert goodbye(person("merry"), at(4, 59)) == "Good night, Daniel."


def test_a_goodbye_clause_is_tidied_and_several_are_joined():
    t = goodbye(person("plain"), at(14), stopping=["a stops at 1 of 2.", "  ", 7, None, "b stops at 3 of 4", "c stops"])
    assert t == "Shutting down. a stops at 1 of 2 and b stops at 3 of 4."
    assert goodbye(person("merry"), at(14), stopping=[" ", ""]) == "See you, Daniel."


def test_with_greeting_off_the_goodbye_is_plain():
    p = person("merry", "Daniel", False)
    assert goodbye(p, at(22), stopping=["batch stops at 14 of 20"]) == "Shutting down. batch stops at 14 of 20."
    assert goodbye(p, at(14)) == "Shutting down."


# invitations

def test_the_walk_is_day_zero_and_every_fourth_day():
    assert invitation(0) == "Let's go for a walk!"
    assert [d for d in range(200) if invitation(d) == "Let's go for a walk!"] == list(range(0, 200, 4))
    assert invitation(-3) == invitation(0) == "Let's go for a walk!"


def test_the_other_seven_come_in_a_fixed_order_and_not_back_within_nine_days():
    pool = greet.lines()["invite"]["pool"]
    assert pool == ["Where to today?", "Shall we wander somewhere?", "What shall we make?", "Ready when you are.",
                    "What's on the road today?", "Somewhere new today?", "Let's see where this goes."]
    assert [invitation(d) for d in (1, 2, 3, 5, 6, 7, 9)] == pool
    last: dict[str, int] = {}
    for d in range(1, 500):
        if d % 4 == 0:
            assert invitation(d) == "Let's go for a walk!"
            continue
        line = invitation(d)
        assert line in pool
        assert d - last.get(line, -100) >= 9, (d, line)
        last[line] = d
    assert set(last) == set(pool)
    assert invitation(10) == invitation(1) and invitation(10) != invitation(9)


def test_only_the_walk_has_a_bang_of_the_eight():
    lines_ = [invitation(d) for d in range(12)]
    assert sorted({x for x in lines_ if "!" in x}) == ["Let's go for a walk!"]
    assert len(set(lines_)) == 8


def test_merry_follows_the_day_count_and_never_a_random_choice():
    seen = [build("boot", person(), NOW, ledger=first_of_day(), day=d).text for d in range(12)]
    assert seen == [f"Good morning, Daniel. {invitation(d)}" for d in range(12)]
    assert build("boot", person(), NOW, ledger=first_of_day(), day=5) == build(
        "boot", person(), NOW, ledger=first_of_day(), day=5)


def test_days_since_setup():
    led = Ledger(setup_day="2026-09-28")
    assert greet.days_since(led, NOW) == 2
    assert greet.days_since(led, at(23, 59, 28)) == 0
    assert greet.days_since(led, at(0, 0, 29)) == 1
    assert greet.days_since(Ledger(), NOW) == 0
    assert greet.days_since(Ledger(setup_day="2027-01-01"), NOW) == 0
    assert greet.days_since(Ledger(setup_day="garbage"), NOW) == 0


# the ledger

def test_a_fact_is_said_once():
    led = first_of_day()
    g = build("return", person(), NOW, facts=[BATCH], away=5 * 3600, ledger=led)
    assert g is not None
    commit(led, g, NOW)
    assert build("return", person(), NOW, facts=[BATCH], away=5 * 3600, ledger=led) is None
    g = build("boot", person("plain"), NOW, facts=[BATCH], ledger=led)
    assert g.facts == ()


def test_said_again_when_the_value_changes():
    led = first_of_day()
    commit(led, build("boot", person(), NOW, facts=[UPDATES], ledger=led), NOW)
    assert build("boot", person("plain"), NOW + HOUR, facts=[UPDATES], ledger=led).facts == ()
    changed = Fact("updates", "updates", "215 updates are waiting, 4 of them security fixes", "215/4", True)
    g = build("boot", person("plain"), NOW + HOUR, facts=[changed], ledger=led)
    assert g.facts == (changed,)
    assert led.said["updates"] == {"value": "214/4", "day": TODAY}


def test_a_standing_fact_is_said_again_after_seven_days():
    led = first_of_day()
    commit(led, Greeting("x", (UPDATES,), moment="boot"), datetime(2026, 9, 1, 9, tzinfo=UTC))
    said = led.said["updates"]["day"]
    assert said == "2026-09-01"
    same = Fact("updates", "updates", UPDATES.text, "214/4", standing=True)
    for days, expect in ((1, False), (6, False), (7, True), (8, True), (40, True)):
        g = build("boot", person("plain"), datetime(2026, 9, 1, 9, tzinfo=UTC) + timedelta(days=days), facts=[same],
                  ledger=Ledger(said={"updates": {"value": "214/4", "day": said}}, last_boot_day="2026-09-01"))
        assert bool(g.facts) == expect, days


def test_a_fact_that_is_not_standing_is_never_said_again_with_the_same_value():
    f = Fact("turn:9", "finished", "batch finished", "v")
    led = Ledger(said={"turn:9": {"value": "v", "day": "2026-01-01"}}, last_boot_day="2026-09-29")
    assert build("boot", person("plain"), NOW, facts=[f], ledger=led).facts == ()
    assert build("boot", person("plain"), NOW, facts=[Fact("turn:9", "finished", "batch finished", "w")],
                 ledger=led).facts != ()


def test_a_damaged_ledger_entry_does_not_hide_a_fact():
    for said in ({"updates": "x"}, {"updates": {}}, {"updates": {"value": "214/4"}},
                 {"updates": {"value": "214/4", "day": 7}}, {"updates": {"value": "214/4", "day": "junk"}}):
        led = Ledger(said=said, last_boot_day="2026-09-29")
        g = build("boot", person("plain"), NOW, facts=[UPDATES], ledger=led)
        assert g.facts == (UPDATES,), said


def test_build_is_pure_and_deterministic():
    led = Ledger(said={"x": {"value": "1", "day": "2026-09-01"}}, setup_day="2026-09-01", last_boot_day="2026-09-20")
    before = copy.deepcopy(led)
    facts = [UPDATES, MADE, BUILDER]
    results = [build("return", person(), NOW, facts=facts, away=9 * 86400, ledger=led, day=3) for _ in range(3)]
    assert results[0] == results[1] == results[2] and results[0] is not None
    assert led == before and facts == [UPDATES, MADE, BUILDER]
    assert build("boot", person(), NOW, facts=tuple(facts), ledger=led) == build("boot", person(), NOW, facts=facts,
                                                                              ledger=led)


def test_build_does_not_touch_the_clock_the_disk_or_the_environment(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("impure")
    greet.lines()  # the table is read once, before
    with monkeypatch.context() as m:
        m.setattr(time, "time", boom)
        m.setattr(time, "tzset", boom)
        m.setattr(Path, "read_text", boom)
        m.setattr(Path, "exists", boom)
        m.setattr(os, "readlink", boom)
        assert build("boot", person(), NOW, ledger=first_of_day(), day=1).text == (
            "Good morning, Daniel. Where to today?")
        assert goodbye(person(), at(22), stopping=["x stops"]).startswith("Good night")
    for fn in (build, goodbye):
        assert not {"time", "os", "random", "open", "Path", "tz_set"} & set(fn.__code__.co_names)


def test_now_may_be_a_datetime_or_an_epoch_second():
    local = time.mktime((2026, 9, 30, 8, 40, 0, 0, 0, -1))  # 08:40 on the machine's own clock
    assert build("boot", person(), local, ledger=first_of_day(), day=1) == build(
        "boot", person(), NOW, ledger=first_of_day(), day=1)
    assert goodbye(person(), time.mktime((2026, 9, 30, 22, 15, 0, 0, 0, -1))) == goodbye(person(), at(22, 15))
    assert greet.days_since(Ledger(setup_day="2026-09-28"), local) == 2


def test_commit_records_what_was_said_and_when():
    led = Ledger()
    g = build("first", person(), NOW, facts=[UPDATES], ledger=led)
    commit(led, g, NOW)
    assert led.said == {"updates": {"value": "214/4", "day": TODAY}}
    assert led.last_boot_day == TODAY and led.setup_day == TODAY and led.last_return_at == 0.0
    commit(led, Greeting("x", moment="boot"), NOW + DAY)
    assert led.last_boot_day == "2026-10-01" and led.setup_day == TODAY
    g = build("return", person(), NOW + DAY, facts=[BATCH], away=5 * 3600, ledger=led)
    commit(led, g, NOW + DAY)
    assert led.last_return_at == (NOW + DAY).timestamp() and led.said["turn:3"]["day"] == "2026-10-01"
    assert led.last_boot_day == "2026-10-01"  # a return is not a boot
    commit(led, build("installed", person(), NOW), NOW)
    assert led.said["installed"] == {"value": "", "day": TODAY}


def test_a_silent_boot_is_recorded_with_an_empty_greeting():
    led = first_of_day()
    commit(led, Greeting("", moment="boot"), NOW)
    assert led.last_boot_day == TODAY and led.said == {}


def test_the_ledger_forgets_old_facts_but_never_that_it_installed():
    led = Ledger(said={"old": {"value": "", "day": "2026-01-01"}, "installed": {"value": "", "day": "2025-01-01"},
                       "recent": {"value": "", "day": "2026-09-01"}})
    commit(led, Greeting("x", moment="boot"), NOW)
    assert set(led.said) == {"installed", "recent"}


def test_installed_is_said_once_because_the_ledger_remembers_it():
    led = Ledger()
    assert "installed" not in led.said
    commit(led, build("installed", person(), NOW), NOW)
    assert "installed" in led.said and led.said["installed"]["day"] == TODAY


def test_the_ledger_round_trips_and_survives_junk():
    led = Ledger({"a": {"value": "1", "day": TODAY}}, "2026-09-01", TODAY, 1759230000.5)
    assert Ledger.from_dict(led.to_dict()) == led
    assert Ledger.from_dict(json.loads(json.dumps(led.to_dict()))) == led
    for junk in (None, [], "x", 7, {}, {"said": []}, {"said": {"a": 1}}, {"setup_day": 5},
                 {"last_return_at": "soon"}, {"last_return_at": True}, {"last_return_at": float("nan")},
                 {"last_return_at": float("inf")}):
        got = Ledger.from_dict(junk)
        assert got.said == {} and got.setup_day == "" and got.last_boot_day == "" and got.last_return_at == 0.0
    assert Ledger.from_dict({"said": {"a": {"value": "1", "day": "d"}, "b": 3}, "setup_day": "s"}) == Ledger(
        {"a": {"value": "1", "day": "d"}}, "s")


def test_the_ledger_file_round_trips_atomically(home):
    assert greet.load_ledger() == Ledger()
    led = Ledger({"updates": {"value": "214/4", "day": TODAY}}, "2026-09-28", TODAY, 12.5)
    assert greet.save_ledger(led) is True
    assert paths.ledger_file().name == "said.json" and paths.ledger_file().parent == paths.state_dir()
    assert json.loads(paths.ledger_file().read_text())["setup_day"] == "2026-09-28"
    assert greet.load_ledger() == led
    assert [p.name for p in paths.state_dir().iterdir()] == ["said.json"]


def test_a_broken_ledger_file_is_an_empty_ledger(home):
    paths.state_dir().mkdir(parents=True)
    for junk in (b"not json", b"\xff\xfe", b"[1, 2]", b'{"said": 7}', b"", b"[" * 6000):
        paths.ledger_file().write_bytes(junk)
        assert greet.load_ledger() == Ledger(), junk
    greet.save_ledger(Ledger(setup_day="2026-09-28"))  # and the next save repairs it
    assert greet.load_ledger().setup_day == "2026-09-28"


def test_saving_the_ledger_never_raises(home):
    paths.state_dir().parent.mkdir(parents=True, exist_ok=True)
    paths.state_dir().write_text("a file where the folder belongs")
    assert greet.save_ledger(Ledger()) is False


# the clock and the machine

@pytest.mark.parametrize(("target", "expect"), [
    ("/usr/share/zoneinfo/Europe/Berlin", True), ("/usr/share/zoneinfo/America/New_York", True),
    ("../usr/share/zoneinfo/Asia/Tokyo", True), ("/usr/share/zoneinfo/Etc/GMT+5", True),
    ("/usr/share/zoneinfo/Europe/London", True), ("/usr/share/zoneinfo/Africa/Abidjan", True),
    ("/usr/share/zoneinfo/UTC", False), ("/usr/share/zoneinfo/Etc/UTC", False), ("/usr/share/zoneinfo/UCT", False),
    ("/usr/share/zoneinfo/Etc/UCT", False), ("/usr/share/zoneinfo/Universal", False),
    ("/usr/share/zoneinfo/Etc/Universal", False), ("/usr/share/zoneinfo/Zulu", False),
    ("/usr/share/zoneinfo/Etc/Zulu", False), ("/usr/share/zoneinfo/GMT", False), ("/usr/share/zoneinfo/Etc/GMT", False),
    ("/usr/share/zoneinfo/Greenwich", False), ("/usr/share/zoneinfo/Etc/Greenwich", False),
    ("../usr/share/zoneinfo/UTC", False), ("/usr/share/zoneinfo/", False), ("/somewhere/else", False)])
def test_the_time_zone_is_set_when_localtime_names_a_real_zone(tmp_path, monkeypatch, target, expect):
    link = tmp_path / "localtime"
    link.symlink_to(target)
    monkeypatch.setenv("BOMBADIL_LOCALTIME", str(link))
    assert paths.localtime_link() == link
    assert greet.tz_set() is expect


def test_the_time_zone_is_not_set_without_a_link(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_LOCALTIME", str(tmp_path / "missing"))
    assert greet.tz_set() is False
    plain = tmp_path / "copied"
    plain.write_bytes(b"TZif2")
    monkeypatch.setenv("BOMBADIL_LOCALTIME", str(plain))
    assert greet.tz_set() is False  # a copy has no name to read: no time-of-day word


def test_tz_set_reads_the_zone_again_before_the_clock_is_used(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(time, "tzset", lambda: calls.append(1))
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/Europe/Berlin")
    monkeypatch.setenv("BOMBADIL_LOCALTIME", str(link))
    assert greet.tz_set() is True and calls == [1]
    link.unlink()
    link.symlink_to("/usr/share/zoneinfo/Etc/UTC")
    assert greet.tz_set() is False and calls == [1, 1]  # a zone set with timedatectl shows without a restart


def test_the_default_localtime_is_etc_localtime(monkeypatch):
    monkeypatch.delenv("BOMBADIL_LOCALTIME", raising=False)
    assert paths.localtime_link() == Path("/etc/localtime")


def test_live_is_the_archiso_folder_unless_the_environment_says_otherwise(monkeypatch):
    class Fake:
        there = True

        def __init__(self, p):
            self.p = p

        def exists(self):
            return self.p == "/run/archiso" and Fake.there

    monkeypatch.setattr(greet, "Path", Fake)
    monkeypatch.delenv("BOMBADIL_LIVE")
    assert greet.is_live() is True
    Fake.there = False
    assert greet.is_live() is False
    monkeypatch.setenv("BOMBADIL_LIVE", "1")
    assert greet.is_live() is True
    Fake.there = True
    monkeypatch.setenv("BOMBADIL_LIVE", "0")
    assert greet.is_live() is False
    monkeypatch.setenv("BOMBADIL_LIVE", "maybe")
    assert greet.is_live() is True


def test_the_paths_of_the_voice(home, monkeypatch):
    assert paths.persona_file() == paths.config_dir() / "persona.toml"
    assert paths.ledger_file() == paths.state_dir() / "said.json"
    assert paths.greeted_marker() == paths.runtime_dir() / "greeted"
    monkeypatch.delenv("BOMBADIL_UPDATES", raising=False)
    assert paths.updates_file() == Path("/var/lib/bombadil/updates.json")
    monkeypatch.setenv("BOMBADIL_UPDATES", str(home / "u.json"))
    assert paths.updates_file() == home / "u.json"
    monkeypatch.delenv("BOMBADIL_VOICE_LINES", raising=False)
    assert paths.voice_lines().is_file() and paths.voice_lines().name == "lines.toml"
    monkeypatch.setenv("BOMBADIL_VOICE_LINES", str(home / "l.toml"))
    assert paths.voice_lines() == home / "l.toml"
    monkeypatch.setenv("BOMBADIL_LOCALTIME", str(home / "lt"))
    assert paths.localtime_link() == home / "lt"

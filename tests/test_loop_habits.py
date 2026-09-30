import itertools
import sys
import time
from pathlib import Path

import pytest

from bombadil import apps as apps_mod
from bombadil.loop import habits, ledger, route
from bombadil.loop.habits import Group, Grouper
from bombadil.loop.ledger import Request

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
import golden_corpus as gc  # noqa: E402

T0 = 1_788_000_000.0
DAY = 86400
APPS = gc.app_list()


def things():
    return habits.things_from_launcher(APPS)


def req(text, t=T0, *, seconds=10.0, rid=None, **kw):
    kw.setdefault("ok", True)
    return Request(id=rid or f"r{int(t)}", t=t, day=ledger.day_of(t), text=text, seconds=seconds, ended=t + seconds, **kw)


def profile(text, topics=(), t=T0, rid=None, **kw):
    r = req(text, t, rid=rid, **kw)
    return habits.make_profile(r, topics, things(), APPS)


def verb(text, topics=()):
    return profile(text, topics).verb


# -- what counts --

def test_the_join_numbers_are_the_briefs():
    assert (habits.TEXT_ALONE, habits.W_TEXT, habits.W_ROUTE, habits.JOIN) == (0.8, 0.4, 0.6, 0.5)
    assert (habits.HALF_LIFE_DAYS, habits.LIST_DAYS, habits.TEXT_DAYS) == (14, 30, 90)
    assert (habits.UNDO_WINDOW, habits.REPHRASE_WINDOW, habits.MAX_TEXT) == (60, 120, 300)


def test_an_ordinary_ask_counts():
    assert habits.counted(req("show me my passwords"), None, [], (), things()) == (True, "")


@pytest.mark.parametrize("origin", ["button", "app", "session", "routine", "loop", "retry", "cli"])
def test_only_what_he_typed_counts(origin):
    assert habits.counted(req("tidy downloads", origin=origin)) == (False, "origin")


def test_empty_shell_and_sign_in():
    assert habits.counted(req("")) == (False, "empty")
    assert habits.counted(req("[from app tracker]   ")) == (False, "empty")
    assert habits.counted(req("!ls -la ~")) == (False, "shell")
    assert habits.counted(req("  !echo hi")) == (False, "shell")
    assert habits.counted(req("show my runs", ok=False, signin=True)) == (False, "sign-in")


def test_a_sign_in_turn_is_not_friction():
    assert habits.friction_of(req("x", ok=False, signin=True)) == ""
    assert habits.friction_of(req("x", ok=False)) == "failed"


@pytest.mark.parametrize("text", [
    "what's my wifi password", "show me my ssh keys", "read my .env file", "what is in the api key file",
    "cat ~/.gnupg/pubring.kbx", "where is my seed phrase", "give me my recovery codes", "what's my 2fa code",
    "show my secrets", "what token does this use", "what is the password for the router",
])
def test_private_words_never_count(text):
    assert habits.is_private_text(text)
    assert habits.counted(req(text)) == (False, "private")


@pytest.mark.parametrize("text", [
    "show me my passwords", "open the password manager", "can you open passwords please", "passwords app",
    "how do I make my passwords stronger",
])
def test_asking_for_the_passwords_app_is_not_private(text):
    assert not habits.is_private_text(text)


def test_a_private_route_is_private_whatever_he_said():
    assert habits.counted(req("tidy up"), None, ["files:home", route.PRIVATE]) == (False, "private")
    assert habits.counted(req("tidy up"), None, ["files:home"]) == (True, "")


def test_long_text_is_a_paste_not_an_ask():
    assert habits.counted(req("a" * 300)) == (True, "")
    assert habits.counted(req("a" * 301)) == (False, "long")
    assert habits.counted(req("[from app x] " + "a" * 300)) == (True, "")     # the prefix does not count


@pytest.mark.parametrize("kw, why", [
    ({"stopped": True}, "stopped"), ({"stopped_at": T0 + 3}, "stopped"), ({"ok": False}, "failed"),
    ({"ok": None}, "failed"),
])
def test_a_turn_that_did_not_finish_is_friction(kw, why):
    r = req("what's eating my ram", **kw)
    assert habits.counted(r) == (False, why)
    assert why in habits.FRICTION


def test_an_undo_within_a_minute_means_it_was_not_wanted():
    r = req("make the pill bigger", seconds=20)
    r.undone_at = r.ended + 60
    assert habits.counted(r) == (False, "undone")
    r.undone_at = r.ended + 61
    assert habits.counted(r) == (True, "")             # kept for a while: that was a change of mind
    r.undone_at = None
    assert habits.counted(r) == (True, "")


def test_stopped_wins_over_failed_and_private_over_friction():
    r = req("what's my wifi password", ok=False, stopped=True)
    assert habits.counted(r) == (False, "private")
    assert habits.counted(req("hello", ok=False, stopped=True)) == (False, "stopped")


def test_an_ask_said_again_right_after_a_failure_is_a_rephrase():
    failed = req("show me my memory usage", T0, ok=False, rid="a")
    again = req("what is my memory usage", T0 + 40, rid="b")
    assert habits.counted(again, failed, [], (), things()) == (False, "rephrase")
    # a different thing is a new ask
    assert habits.counted(req("tidy downloads", T0 + 40, rid="c"), failed) == (True, "")
    # too late to be a retry
    late = req("what is my memory usage", failed.ended + 121, rid="d")
    assert habits.counted(late, failed) == (True, "")
    soon = req("what is my memory usage", failed.ended + 119, rid="e")
    assert habits.counted(soon, failed) == (False, "rephrase")
    # the turn before it worked: nothing to retry
    worked = req("show me my memory usage", T0, rid="f")
    assert habits.counted(again, worked) == (True, "")
    assert habits.counted(again, again) == (True, "")


def test_a_rephrase_can_share_only_a_named_thing():
    failed = req("pull up the vault", T0, ok=False, rid="a")
    again = req("passwords please", T0 + 30, rid="b")
    # no content word in common, but "the vault" is not a thing; with passwords named in both it is
    failed2 = req("can you open passwords", T0, ok=False, rid="c")
    assert habits.counted(again, failed, [], (), things()) == (True, "")
    assert habits.counted(again, failed2, [], (), things()) == (False, "rephrase")


def test_never_keeps_later_asks_like_it_out():
    asked = req("show me my passwords", rid="a")
    p = habits.make_profile(asked, ["app:passwords", "opened"], things(), APPS)
    later = req("can you open passwords please", T0 + DAY, rid="b")
    assert habits.counted(later, None, ["app:passwords", "opened"], [p], things()) == (False, "never")
    other = req("what's eating my ram", T0 + DAY, rid="c")
    assert habits.counted(other, None, ["memory", "processes"], [p], things()) == (True, "")
    assert habits.counted(later, None, ["app:passwords", "opened"], [], things()) == (True, "")


def test_a_never_signature_holds_no_sentence_of_his():
    p = profile("show me my passwords please", ["app:passwords", "opened"])
    sig = habits.signature([p])
    assert "show me" not in repr(sig) and "please" not in repr(sig)
    back = habits.from_signature(sig)
    assert back and habits.joins(profile("can you open passwords", ["app:passwords", "opened"]), back[0])
    assert habits.from_signature([["broken"], None, 5]) == []
    assert habits.from_signature(None) == []
    assert len(habits.signature([profile(f"ask {i}") for i in range(9)])) == 5


# -- words --

def test_clean_text_drops_what_the_pill_adds():
    assert habits.clean_text("[from app tracker] log my run") == "log my run"
    assert habits.clean_text("About ~/notes/todo.txt: summarise it") == "summarise it"
    assert habits.clean_text("  plain  ") == "plain"


@pytest.mark.parametrize("a, b", [
    ("installing ffmpeg", "I'd like to install ffmpeg please"),
    ("install ffmpeg", "can you get ffmpeg installed"),
    ("what's eating my RAM?", "what is using memory"),
    ("timer for 25 minutes", "a timer for 10 minutes"),
    ("Wi-Fi is slow", "wifi slow"),
    ("tidy downloads", "clean up my downloads"),
    ("show me /home/me/a.txt", "show me ~/docs/b.pdf"),
    ("go to https://a.example.org/x", "pull up www.b.example.com"),
])
def test_what_he_meant_is_what_is_compared(a, b):
    assert sorted(habits.normalise(a).split()) == sorted(habits.normalise(b).split())


def test_things_that_differ_stay_different():
    assert habits.normalise("install ffmpeg") != habits.normalise("install docker")
    assert habits.normalise("memory") != habits.normalise("disk")
    assert habits.normalise("what is the weather") != habits.normalise("what is the time")


@pytest.mark.parametrize("word, root", [
    ("installing", "install"), ("installed", "install"), ("installs", "install"), ("running", "run"),
    ("processes", "process"), ("batteries", "battery"), ("boxes", "box"), ("tried", "try"), ("class", "class"),
    ("bus", "bus"), ("memory", "memory"), ("a", "a"), ("ram", "ram"),
])
def test_stem_is_consistent_not_a_dictionary(word, root):
    assert habits.stem(word) == root


def test_trigram_overlap():
    a = habits.trigrams("memory use")
    assert habits.text_overlap(a, a) == 1.0
    assert habits.text_overlap(a, habits.trigrams("weather rain")) < 0.2
    assert habits.text_overlap(a, frozenset()) == 0.0
    assert habits.text_overlap(frozenset(), frozenset()) == 0.0
    assert 0.3 < habits.text_overlap(a, habits.trigrams("memory free")) < 0.9


# -- the ten kinds --

@pytest.mark.parametrize("text, kind", [
    ("what's eating my ram", "ask"), ("is my internet down", "ask"), ("how's the build going", "ask"),
    ("what's changed in the repo", "ask"), ("show me the git status", "ask"), ("list running containers", "ask"),
    ("where did I put the insurance pdf", "ask"), ("show the last 20 lines of the system log", "ask"),
    ("git log", "ask"), ("explain what a mutex is", "ask"),
    ("install ffmpeg", "install"), ("can you get ffmpeg installed", "install"), ("update my system", "install"),
    ("set up docker", "install"),
    ("tidy downloads", "tidy"), ("clean up my downloads folder", "tidy"), ("delete old stuff in downloads", "tidy"),
    ("set a timer for 25 minutes", "tell-me-when"), ("tell me when it's done", "tell-me-when"),
    ("is the batch job finished yet", "tell-me-when"), ("remind me in 20 minutes to stretch", "tell-me-when"),
    ("log my run: 5k in 28 minutes", "log"), ("put 5 miles in the run log", "log"),
    ("add a run: 8.2 km, 47 min", "log"), ("record a run of 6 km", "log"),
    ("make the screen dimmer", "change"), ("turn the brightness up a bit", "change"),
    ("it's too bright in here", "change"), ("volume to 30", "change"), ("kill firefox", "change"),
    ("restart networkmanager", "change"), ("turn on bluetooth", "change"), ("play some jazz", "change"),
    ("next song", "change"), ("zip up my documents folder", "change"), ("shorter answers please", "change"),
    ("add a dark mode toggle to the runs app", "make"), ("write a haiku about rain", "make"),
    ("create an app that shows the moon phase", "make"), ("draft an email to my landlord", "make"),
    ("fix the crash in the tracker", "fix"), ("the tracker crashes on start", "fix"), ("why did my build fail", "ask"),
    ("find my tax pdf", "find"), ("locate the lease agreement pdf", "find"),
    ("go to https://example.org", "open"), ("open ~/Downloads/report.pdf", "open"),
    ("can you open passwords please", "open"), ("open the browser", "open"),
])
def test_the_kind_of_ask(text, kind):
    assert verb(text) == kind


def test_a_noun_is_not_a_verb():
    assert verb("how's the build going") == "ask"
    assert verb("build me a tracker") == "make"
    assert verb("my install is broken") != "install"
    assert verb("what's in my downloads") == "ask"      # "downloads" is a folder, not to download
    assert verb("download the new wallpaper") == "install"
    assert verb("the logs from sshd") == "ask"


def test_a_verb_of_the_ask_must_be_one_he_said():
    assert verb("tidy downloads") != verb("what's in downloads")
    assert verb("") == "ask"
    assert verb("   ?") == "ask"


# -- named things --

def test_named_things_come_from_the_launcher_tables():
    t = habits.things_from_launcher([apps_mod.App("memory-viewer", Path("."), "Memory Viewer")])
    assert t["app:memory-viewer"] == ("memory viewer",)
    assert t["panel:browser"] == ("browser",) and t["util:wifi"] == ("wifi",)
    for said in ("open the Memory Viewer", "memory-viewer", "memoryviewer please", "my memory viewers"):
        assert habits.named_things(said, t) == {"app:memory-viewer"}, said
    assert habits.named_things("is the wifi ok", t) == {"util:wifi"}
    assert habits.named_things("open the browser", t) == {"panel:browser"}
    assert habits.named_things("anything", {}) == frozenset()
    assert habits.named_things("the memory of it", t) == frozenset()


def test_a_password_near_miss_is_a_bare_open_the_launcher_missed():
    assert profile("can you open passwords please").near_miss
    assert profile("show me my passwords").near_miss
    assert not profile("open passwords").near_miss              # the launcher answers it: nothing to learn
    assert not profile("what's in my passwords").near_miss
    assert not profile("tidy downloads").near_miss
    assert not profile("open the passwords app and add a login for the router").near_miss


def test_what_he_would_say_to_a_word_for_it():
    assert habits.word_phrase("show me my passwords") == "my passwords"
    assert habits.word_phrase("can you open passwords please") == "passwords"
    assert habits.word_phrase("open the browser") == "browser"
    assert habits.word_phrase("Open my Runs app") == "my runs"
    assert habits.word_phrase("a really long phrase with many words here") == ""
    assert habits.word_phrase("open ~/Downloads/a.pdf") == ""
    assert habits.word_phrase("") == ""


def test_a_word_that_already_does_it_is_found_by_the_launcher():
    assert habits.existing_word("passwords", APPS) == "passwords"
    assert habits.existing_word("my passwords", APPS) == ""
    assert habits.existing_word("browser", APPS) == "browser"
    assert habits.existing_word("", APPS) == ""


def test_a_sentence_is_his_own_words_shortened_only_when_long():
    assert habits.sentence_of("  show me   my passwords ") == "show me my passwords"
    long = "x" * 200
    assert len(habits.sentence_of(long)) == 80 and habits.sentence_of(long).endswith("…")
    assert habits.sentence_of("[from app a] hello") == "hello"


# -- the join rule --

def light(tokens, verb_="ask", route_=(), named=()):
    return habits.light_profile(tokens, verb_, named, route_)


def test_text_alone_joins_at_eight_tenths():
    same = light(["install", "ffmpeg"], "install")
    assert habits.score(same, same) == 1.0 and habits.joins(same, same)
    near = light(["install", "vlc"], "install")
    overlap = habits.text_overlap(same.grams, near.grams)
    assert 0.3 < overlap < 0.8
    assert habits.score(same, near) == pytest.approx(0.4 * overlap)
    assert not habits.joins(same, near)                       # not enough words, and no route to help


def test_route_joins_words_that_share_nothing():
    a = light(["eat", "ram"], route_=["memory", "processes"])
    b = light(["hog", "mem"], route_=["memory", "processes"])
    assert habits.text_overlap(a.grams, b.grams) < 0.3
    assert habits.score(a, b) == pytest.approx(0.6 + 0.4 * habits.text_overlap(a.grams, b.grams))
    assert habits.joins(a, b)
    assert not habits.joins(light(["eat", "ram"]), light(["hog", "mem"]))        # the same words, no route


def test_half_the_route_is_not_enough_without_words():
    a = light(["alpha", "beta"], route_=["memory", "processes"])
    b = light(["gamma", "delta"], route_=["memory"])
    assert habits.score(a, b) < habits.JOIN
    c = light(["alpha", "beta", "gamma"], route_=["memory"])
    assert habits.text_overlap(a.grams, c.grams) >= 0.6
    assert habits.joins(a, c)                                 # half the route and most of the words


def test_the_route_weighs_more_than_the_words():
    words = light(["weather", "rain"], route_=["weather"])
    other_route = light(["weather", "rain"], route_=["network"])
    assert habits.score(words, other_route) == 1.0           # the same words alone are enough at 0.8
    a = light(["weather", "rain", "now"], route_=["weather"])
    b = light(["rain", "umbrella", "soon"], route_=["network"])
    assert not habits.joins(a, b)


def test_asks_naming_different_things_never_join():
    a = light(["open"], "open", ["opened"], named=["app:passwords"])
    b = light(["open"], "open", ["opened"], named=["app:runs"])
    assert habits.score(a, b) == 0.0 and not habits.joins(a, b)
    c = light(["open"], "open", ["opened"])
    assert habits.joins(a, c)                                 # one names nothing: no conflict


def test_asks_that_do_different_things_never_join():
    a = light(["download", "folder"], "tidy", ["files:downloads"])
    b = light(["download", "folder"], "ask", ["files:downloads"])
    assert habits.score(a, b) == 0.0                          # same words, same place: clean up vs look
    assert habits.joins(a, light(["download", "folder"], "tidy", ["files:downloads"]))
    for x, y in (("open", "ask"), ("ask", "tell-me-when"), ("open", "tell-me-when")):
        assert habits.same_kind(x, y) and habits.same_kind(y, x)
    for x, y in (("tidy", "ask"), ("log", "make"), ("change", "install"), ("find", "ask"), ("make", "fix")):
        assert not habits.same_kind(x, y)


def test_two_changes_to_one_app_are_not_one_request():
    edit = ["app:runs"]
    a = light(["add", "dark", "mode", "toggle", "runs"], "make", edit)
    b = light(["show", "pace", "min", "km", "runs"], "make", edit)
    assert habits.score(a, b) < habits.JOIN                   # the same place is not the same request
    assert habits.score(a, light(["add", "dark", "mode", "toggle", "runs"], "make", [])) == 1.0


def test_joining_ignores_which_asks_were_first():
    a = profile("what's eating my ram", ["memory", "processes"])
    b = profile("memory hog?", ["processes", "memory"])
    assert habits.score(a, b) == habits.score(b, a) and habits.joins(a, b)


# -- groups --

def chain(texts_and_topics, start=T0, gap=DAY):
    out = []
    for i, (text, topics) in enumerate(texts_and_topics):
        out.append(profile(text, topics, t=start + i * gap, rid=f"r{i}"))
    return out


def test_a_group_is_named_for_its_first_member_and_keeps_the_name():
    g = Grouper()
    ps = chain([("show me my passwords", ["app:passwords", "opened"]),
                ("can you open passwords please", ["app:passwords", "opened"]),
                ("what's eating my ram", ["memory", "processes"]),
                ("pull up the password manager", ["app:passwords", "opened"])])
    ids = [g.add(p) for p in ps]
    assert ids == ["gr0", "gr0", "gr2", "gr0"]
    first = g.group("gr0")
    assert first.members == ["r0", "r1", "r3"] and first.n == 3 and first.id == "gr0"
    assert g.group("gr2").n == 1 and g.group("nope") is None


def test_members_never_move_when_a_bridging_ask_arrives():
    g = Grouper()
    a = profile("what's eating my ram", ["memory", "processes"], rid="a")
    b = profile("what's eating my storage", ["disk", "files:home"], rid="b", t=T0 + 1)
    assert g.add(a) == "ga" and g.add(b) == "gb"
    bridge = profile("what's eating my ram and storage", ["memory", "processes", "disk"], rid="c", t=T0 + 2)
    gid = g.add(bridge)
    assert gid in ("ga", "gb")
    assert g.group_of("a") == "ga" and g.group_of("b") == "gb"          # nothing merged, nothing moved
    assert {x.id for x in g.groups()} == {"ga", "gb"}


def test_the_groups_ids_do_not_change_as_more_arrives():
    asks = [a for a in gc.counted_asks()]
    before = {}
    g = Grouper()
    p = build(asks)
    half = len(asks) // 2
    for a in asks[:half]:
        before[a["id"]] = g.add(p[a["id"]])
    snapshot = {gid: list(acc.members and [m.id for m in acc.members]) for gid, acc in g.acc.items()}
    for a in asks[half:]:
        g.add(p[a["id"]])
    for gid, members in snapshot.items():
        assert [m.id for m in g.acc[gid].members][:len(members)] == members
    assert all(g.group_of(rid) == gid for rid, gid in before.items())
    assert all(gid == f"g{gid[1:]}" and gid[1:] in p for gid in g.acc)       # "g" + the first member's id


def test_placing_what_was_stored_rebuilds_the_same_groups_without_deciding_again():
    asks = gc.counted_asks()
    p = build(asks)
    live = Grouper()
    stored = {a["id"]: live.add(p[a["id"]]) for a in asks}
    rebuilt = Grouper()
    for a in asks:
        rebuilt.place(p[a["id"]], stored[a["id"]])
    assert rebuilt.assigned == live.assigned
    assert [g.to_dict() for g in rebuilt.groups()] == [g.to_dict() for g in live.groups()]


def test_a_retry_is_attached_as_friction_and_changes_nothing_else():
    g = Grouper()
    a = profile("what's eating my ram", ["memory", "processes"], rid="a")
    g.add(a)
    retry = profile("memory hog", ["memory", "processes"], rid="b", t=T0 + 30)
    assert g.attach_friction(retry) == "ga"
    grp = g.group("ga")
    assert grp.n == 1 and grp.friction == 1 and grp.members == ["a"]
    assert g.attach_friction(retry) == "ga" and g.group("ga").friction == 1          # once
    assert g.attach_friction(profile("tidy downloads", ["files:downloads"], rid="c")) is None


def test_removing_a_member_keeps_the_group_and_its_id():
    g = Grouper()
    for p in chain([("tidy downloads", ["files:downloads"]), ("clean up my downloads", ["files:downloads"])]):
        g.add(p)
    assert g.remove("r1") == "gr0"
    assert g.group("gr0").members == ["r0"] and g.group_of("r1") is None
    assert g.remove("r1") is None
    g.remove("r0")
    assert g.group("gr0") is None                                 # nothing left to show


def test_a_group_says_what_it_was_asked_how_often_and_what_it_opened():
    ps = chain([("show me my passwords", ["app:passwords", "opened"]),
                ("can you open passwords please", ["app:passwords", "opened"]),
                ("show me my passwords", ["app:passwords", "opened"])], gap=2 * DAY)
    g = Grouper()
    for p in ps:
        g.add(p)
    grp = g.group("gr0")
    assert (grp.n, len(grp.days), grp.first, grp.last) == (3, 3, ps[0].t, ps[2].t)
    assert grp.opens == "app:passwords" and grp.near_miss
    assert grp.sentences == ["show me my passwords", "can you open passwords please"]   # newest first, distinct
    assert "passwords" in grp.label.split() and len(grp.label.split()) <= 4
    assert grp.routes[0] == "app:passwords" and grp.named == ["app:passwords"]
    assert grp.word in ("my passwords", "passwords")
    assert grp.verb == "open" and grp.times == [p.t for p in ps] and grp.steps == [0, 0, 0]


def test_a_groups_topics_come_commonest_first_and_ties_by_name_not_by_hash_order():
    ps = chain([("what is eating my ram", ["processes", "memory", "zzz", "aaa"]),
                ("which program uses the most memory", ["memory", "aaa", "zzz", "processes"]),
                ("what is eating my ram", ["processes", "memory"])])
    g = Grouper()
    for p in ps:
        g.add(p)
    assert g.group("gr0").routes == ["memory", "processes", "aaa", "zzz"]


def test_a_group_that_also_did_something_else_is_not_an_open():
    ps = chain([("show me my passwords", ["app:passwords", "opened"]),
                ("open passwords", ["app:passwords", "opened", "memory"])])
    g = Grouper()
    for p in ps:
        g.add(p)
    assert g.group("gr0").opens == "" and not g.group("gr0").near_miss


def test_a_group_survives_being_written_and_read():
    g = Grouper()
    for p in chain([("tidy downloads", ["files:downloads"]), ("clean up my downloads", ["files:downloads"])]):
        g.add(p)
    d = g.group("gr0").to_dict()
    assert Group.from_dict({**d, "unknown": 1}).to_dict() == d
    assert Group.from_dict({"id": "x"}).n == 0


def test_the_seven_compare_members_are_the_first_two_and_the_latest_eight():
    acc = habits._Acc("g")
    for i in range(30):
        acc.add(profile(f"ask {i}", rid=f"r{i}"))
    assert [m.id for m in acc.compare_with()] == ["r0", "r1"] + [f"r{i}" for i in range(22, 30)]


# -- time --

def test_each_ask_weighs_one_and_halves_every_fourteen_days():
    now = T0 + 100 * DAY
    assert habits.decayed([now], now) == pytest.approx(1.0)
    assert habits.decayed([now - 14 * DAY], now) == pytest.approx(0.5)
    assert habits.decayed([now - 28 * DAY], now) == pytest.approx(0.25)
    assert habits.decayed([now - 14 * DAY, now], now) == pytest.approx(1.5)
    assert habits.decayed([now + DAY], now) == pytest.approx(1.0)       # an ask "from the future" is not more than one
    assert habits.decayed([], now) == 0.0


def test_a_group_leaves_the_list_after_thirty_days_without_an_ask():
    g = Group(id="g", n=3, last=T0)
    assert habits.listed(g, T0 + 30 * DAY)
    assert not habits.listed(g, T0 + 30 * DAY + 1)
    assert not habits.listed(Group(id="g", n=0, last=T0), T0)


def test_after_ninety_days_the_words_go_and_the_counts_stay():
    g = Group(id="g", label="passwords open", n=4, last=T0, days=["a", "b"], word="my passwords",
              sentences=["show me my passwords"], times=[T0 - DAY, T0])
    habits.apply_decay(g, T0 + 89 * DAY)
    assert g.label == "passwords open" and not g.text_dropped
    habits.apply_decay(g, T0 + 91 * DAY)
    assert (g.label, g.word, g.sentences, g.text_dropped) == ("", "", [], True)
    assert (g.n, g.days, g.times) == (4, ["a", "b"], [T0 - DAY, T0])
    assert g.weight == pytest.approx(habits.decayed(g.times, T0 + 91 * DAY))
    habits.apply_decay(g, T0 + 200 * DAY)
    assert g.text_dropped and g.n == 4


# -- the golden corpus --

def build(asks):
    """Profiles of the asks that count, as the store would make them."""
    t = things()
    out = {}
    prev = None
    for a in asks:
        r = ledger.request_from_row(gc.row_of(a))
        topics = route.route(gc.tool_events(a))
        ok, _ = habits.counted(r, prev, topics, (), t)
        if ok:
            out[a["id"]] = habits.make_profile(r, topics, t, APPS)
        prev = r
    return out


def scores(pairs):
    tp = fp = fn = tn = 0
    for same, joined in pairs:
        tp += same and joined
        fp += joined and not same
        fn += same and not joined
        tn += not same and not joined
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return precision, recall, (tp, fp, fn, tn)


def truth(a, b):
    return a["group"] == b["group"] and a["group"] not in ("single", "none")


def test_the_corpus_is_what_it_says(home):
    asks = gc.load_asks()
    assert len(asks) >= 140
    assert len({a["id"] for a in asks}) == len(asks)
    assert len({a["day"] for a in asks}) >= 25                        # four weeks, not one afternoon
    groups = {}
    for a in asks:
        assert a["text"] and a["group"] and isinstance(a["events"], list)
        groups.setdefault(a["group"], []).append(a)
    repeated = [g for g, m in groups.items() if g not in ("single", "none") and len(m) >= 2]
    assert len(repeated) >= 25
    assert len(groups["single"]) >= 25
    assert {a["expect"] for a in groups["none"]} == {"private", "shell", "origin"}
    said = " | ".join(a["text"] for a in asks)
    for needle in ("show me my passwords", "can you open passwords please", "what's eating my ram", "memory hog?",
                   "install ffmpeg", "how's the batch job going", "clean up my downloads folder",
                   "set a timer for 25 minutes", "log my run", "what's the weather", "is the wifi working",
                   "dim the screen", "git status", "docker ps"):
        assert needle in said
    by_day = {}
    for a in asks:
        by_day.setdefault(a["day"], []).append(a)
    assert max(len(v) for v in by_day.values()) <= 12                # spread across days


def test_the_hand_labelled_pairs_are_consistent_with_the_groups(home):
    asks = {a["id"]: a for a in gc.load_asks()}
    pairs = gc.load_pairs()
    assert len(pairs) >= 100
    assert sum(p["same"] for p in pairs) >= 40 and sum(not p["same"] for p in pairs) >= 40
    seen = set()
    for p in pairs:
        assert p["a"] in asks and p["b"] in asks and p["a"] != p["b"], p
        key = frozenset((p["a"], p["b"]))
        assert key not in seen, f"pair labelled twice: {p}"
        seen.add(key)
        assert p["why"], p
        assert p["same"] == truth(asks[p["a"]], asks[p["b"]]), p


def test_every_ask_meant_to_count_counts_and_the_rest_do_not(home):
    asks = gc.load_asks()
    t = things()
    prev = None
    for a in asks:
        r = ledger.request_from_row(gc.row_of(a))
        ok, why = habits.counted(r, prev, route.route(gc.tool_events(a)), (), t)
        if a["group"] == "none":
            assert (ok, why) == (False, a["expect"]), a
        else:
            assert (ok, why) == (True, ""), a
        prev = r


def test_precision_and_recall_on_the_hand_labelled_pairs(home):
    asks = gc.load_asks()
    p = build(asks)
    pairs = gc.load_pairs()
    precision, recall, counts = scores((x["same"], habits.joins(p[x["a"]], p[x["b"]])) for x in pairs)
    print(f"hand-labelled pairs: precision {precision:.3f} recall {recall:.3f} (tp, fp, fn, tn) {counts}")
    assert precision >= 0.90
    assert recall >= 0.6


def test_precision_and_recall_on_every_pair_of_the_corpus(home):
    asks = [a for a in gc.load_asks() if a["group"] != "none"]
    p = build(asks)
    precision, recall, counts = scores(
        (truth(a, b), habits.joins(p[a["id"]], p[b["id"]])) for a, b in itertools.combinations(asks, 2))
    print(f"all pairs: precision {precision:.3f} recall {recall:.3f} (tp, fp, fn, tn) {counts}")
    assert precision >= 0.90
    assert recall >= 0.6


def test_precision_and_recall_of_the_groups_as_they_are_made(home):
    """The grouper, not the pairwise rule: one ask joins the best group and stays in it."""
    asks = [a for a in gc.load_asks() if a["group"] != "none"]
    p = build(asks)
    g = Grouper()
    made = {a["id"]: g.add(p[a["id"]]) for a in asks}
    precision, recall, counts = scores(
        (truth(a, b), made[a["id"]] == made[b["id"]]) for a, b in itertools.combinations(asks, 2))
    print(f"grouped: precision {precision:.3f} recall {recall:.3f} (tp, fp, fn, tn) {counts}")
    assert precision >= 0.90
    assert recall >= 0.6


def test_the_second_corpus_written_after_the_rules_were_settled(home):
    """Written after the first, and looked at once before anything was changed: precision was 0.67 (the
    music player and `pkill` routes were too coarse). What it found is fixed; what is left is judged
    here. It is no longer unseen, so it guards and does not prove."""
    asks = gc.load_asks("golden_holdout.jsonl")
    p = build(asks)
    precision, recall, counts = scores(
        (truth(a, b), habits.joins(p[a["id"]], p[b["id"]])) for a, b in itertools.combinations(asks, 2))
    print(f"second corpus: precision {precision:.3f} recall {recall:.3f} (tp, fp, fn, tn) {counts}")
    assert precision >= 0.90 and recall >= 0.6


def test_paraphrases_that_share_no_words_join_through_the_route(home):
    asks = {a["id"]: a for a in gc.load_asks()}
    p = build(list(asks.values()))
    # not one word in common, not even once "ram" and "memory" are folded together
    cases = [("wx1", "wx4"), ("wx2", "wx6"), ("gs1", "gs3"), ("gs3", "gs5"), ("bat1", "bat3"), ("bri2", "bri5"),
             ("bri1", "bri3"), ("ss1", "ss3"), ("gl1", "gl3"), ("vol2", "vol4")]
    for a, b in cases:
        x, y = p[a], p[b]
        assert not set(x.tokens) & set(y.tokens), (x.text, y.text)
        assert habits.text_overlap(x.grams, y.grams) < 0.5, (x.text, y.text)
        assert habits.jaccard(x.route, y.route) >= 0.8, (x.text, y.text)
        assert habits.joins(x, y), (x.text, y.text)
        # the route did it: without it the same two asks stay apart
        bare_x = habits.make_profile(ledger.request_from_row(gc.row_of(asks[a])), [], things(), APPS)
        bare_y = habits.make_profile(ledger.request_from_row(gc.row_of(asks[b])), [], things(), APPS)
        assert not habits.joins(bare_x, bare_y), (x.text, y.text)
    # and words that differ on the surface are one when they mean the same
    assert habits.joins(p["mem1"], p["mem2"]) and set(p["mem1"].tokens) == set(p["mem2"].tokens)


def test_the_same_words_about_another_thing_do_not_join(home):
    asks = {a["id"]: a for a in gc.load_asks()}
    p = build(list(asks.values()))
    for a, b in [("mem1", "disk4"), ("mem1", "cpu1"), ("ff1", "dk1"), ("ff1", "s24"), ("s24", "s25"), ("td1", "ld1"),
                 ("gs1", "gl3"), ("bj1", "bd1"), ("net1", "ip1"), ("net2", "sig1"), ("net1", "s28"), ("vol1", "s37"),
                 ("lr1", "rr1"), ("lr1", "s08"), ("dc1", "s26"), ("pw1", "br1"), ("s16", "cpu6"), ("fd1", "s32")]:
        assert not habits.joins(p[a], p[b]), (p[a].text, p[b].text)


def test_the_passwords_asks_become_one_group_that_only_opens_an_app(home):
    asks = [a for a in gc.load_asks() if a["group"] == "passwords"]
    p = build(asks)
    assert len(p) == len(asks) >= 6
    g = Grouper()
    ids = {g.add(p[a["id"]]) for a in asks}
    assert len(ids) == 1
    group = g.group(ids.pop())
    assert group.n == len(asks) and group.opens == "app:passwords" and group.near_miss
    assert sum(1 for x in p.values() if x.near_miss) >= 4


def test_grouping_thousands_of_asks_is_fast(home):
    asks = [a for a in gc.load_asks() if a["group"] != "none"]
    p = build(asks)
    base = list(p.values())
    many = []
    for i in range(4000):
        x = base[i % len(base)]
        many.append(habits.Profile(**{**x.__dict__, "id": f"p{i}", "t": x.t + (i // len(base)) * 3600}))
    g = Grouper()
    start = time.monotonic()
    for x in many:
        g.add(x)
    took = time.monotonic() - start
    assert took < 5, f"{took:.1f}s for {len(many)} asks"
    assert len(g.acc) < 300                                       # copies of the same asks land together

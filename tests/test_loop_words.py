import os
import time
import tomllib

import pytest

from bombadil import apps, paths
from bombadil.loop import words

DAY = 86400
NOW = 1_790_000_000


@pytest.fixture(autouse=True)
def fresh_cache():
    words._cache = None
    yield
    words._cache = None


def free(_phrase):
    """A `known` that says nothing means anything: these tests need no machine."""
    return ""


def app(name="passwords"):
    return {"kind": "app", "name": name}


def on_disk():
    return tomllib.loads(paths.words_file().read_text())


def put_file(text, mtime_ns=None):
    path = paths.words_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


ROW = '[[word]]\nphrase = "{}"\nopens = {{ kind = "app", name = "passwords" }}\nmade = 5\n'


def test_add_makes_a_word_and_writes_the_row(home):
    w = words.add("Show me my passwords!", app(), group="g-1", now=NOW, known=free)
    assert w == words.Word("show me my passwords", "app", "passwords", NOW, "g-1", False)
    assert w.opens == {"kind": "app", "name": "passwords"}
    row = on_disk()["word"][0]
    assert row == {"phrase": "show me my passwords", "opens": {"kind": "app", "name": "passwords"},
                   "made": NOW, "from_group": "g-1", "away": False}
    assert words.load() == [w] and words.get("SHOW me my passwords") == w and words.lookup("show me my passwords") == w


def test_a_word_made_with_no_group_or_time_has_both_filled_in(home):
    w = words.add("my passwords", app(), known=free)
    assert w.from_group == "" and abs(w.made - time.time()) < 5


def test_remove_takes_a_word_out_put_away_or_not(home):
    words.add("one", app(), now=NOW, known=free)
    words.add("two", app(), now=NOW, known=free)
    words.put_away("two")
    assert words.remove("ONE").phrase == "one"
    assert words.remove("one") is None
    assert words.remove("two").away is True
    assert words.load() == [] and on_disk().get("word") is None


def test_put_away_and_bring_back(home):
    words.add("my passwords", app(), now=NOW, known=free)
    assert words.put_away("my passwords").away is True
    assert words.put_away("my passwords") is None          # already away: nothing to do
    assert words.lookup("my passwords") is None and words.get("my passwords").away is True
    assert words.active() == [] and len(words.load()) == 1
    back = words.bring_back("my passwords", now=NOW + 40 * DAY)
    assert back.away is False and back.made == NOW + 40 * DAY   # the 28 days start over
    assert words.bring_back("my passwords") is None
    assert words.lookup("my passwords") == back
    assert words.put_away("no such word") is None and words.bring_back("no such word") is None


def test_at_most_thirty_words_are_active(home):
    for n in range(words.MAX_WORDS):
        words.add(f"word {n}", app(), now=NOW, known=free)
    with pytest.raises(words.WordsFull) as e:
        words.add("one too many", app(), known=free)
    assert e.value.why == "full" and "30" in str(e.value)
    assert words.get("one too many") is None
    words.put_away("word 0")                     # an away word is not active, so there is room
    words.add("one too many", app(), now=NOW, known=free)
    with pytest.raises(words.WordsFull):        # and bringing one back cannot get round the cap
        words.bring_back("word 0")
    assert words.get("word 0").away is True
    assert len(words.load()) == 31 and len(words.active()) == 30


def test_a_phrase_that_already_means_something_is_refused(home):
    with pytest.raises(words.WordRefused) as e:
        words.add("passwords", app(), known=lambda p: "an app")
    assert e.value.why == "means" and "an app" in str(e.value)
    assert not paths.words_file().exists()
    words.add("my passwords", app(), now=NOW, known=free)
    with pytest.raises(words.WordRefused) as e:
        words.add("My Passwords.", app("memory"), known=free)
    assert e.value.why == "word"
    words.put_away("my passwords")
    with pytest.raises(words.WordRefused, match="put away"):
        words.add("my passwords", app(), known=free)
    assert words.get("my passwords").name == "passwords"


@pytest.mark.parametrize("phrase, what", [
    ("passwords", "an app"), ("Memory Viewer", "an app"), ("open passwords", "an app"),
    ("browser", "a panel"), ("Chrome", "a panel"), ("show the terminal", "a panel"),
    ("undo", "a command"), ("Stop it", "a command"), ("noticed", "a command"), ("hide noticed", "a command"),
    ("wifi", "a utility word"), ("open sound", "a utility word"),
])
def test_the_machines_own_names_are_never_made_a_word(home, phrase, what):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    apps.create("Memory Viewer", "import QtQuick\nItem {}\n")
    with pytest.raises(words.WordRefused) as e:
        words.add(phrase, app())
    assert e.value.why == "means" and what in str(e.value)


def test_a_free_phrase_passes_the_real_check(home):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    assert words.add("show me my passwords", app()).phrase == "show me my passwords"


@pytest.mark.parametrize("opens", [
    {"kind": "shell", "name": "rm -rf ~"}, {"kind": "command", "name": "sudo reboot"},
    {"kind": "model", "name": "claude"}, {"kind": "run", "name": "ls"}, {"kind": "sudo", "name": "x"},
    {"kind": "", "name": "passwords"}, {"name": "passwords"}, {"kind": "app"}, "app:passwords",
    ["app", "passwords"], None, {"kind": ["app"], "name": "passwords"},
])
def test_a_word_can_only_open_or_show(home, opens):
    with pytest.raises(words.WordRefused):
        words.add("my stuff", opens, known=free)
    assert not paths.words_file().exists()


@pytest.mark.parametrize("opens", [
    {"kind": "app", "name": "../etc/passwd"}, {"kind": "app", "name": "Passwords"}, {"kind": "app", "name": ""},
    {"kind": "app", "name": 5}, {"kind": "panel", "name": "chromium"}, {"kind": "panel", "name": "rm -rf ~"},
])
def test_what_a_word_opens_must_be_a_name_the_launcher_could_open(home, opens):
    with pytest.raises(words.WordRefused) as e:
        words.add("my stuff", opens, known=free)
    assert e.value.why == "target"


def test_only_the_kind_and_the_name_are_ever_kept(home):
    words.add("my stuff", {"kind": "app", "name": "passwords", "run": "rm -rf ~", "cmd": "sudo x"},
              now=NOW, known=free)
    assert on_disk()["word"][0]["opens"] == {"kind": "app", "name": "passwords"}
    assert "rm -rf" not in paths.words_file().read_text()


@pytest.mark.parametrize("phrase", ["", "   ", "...", "!", "!ls", " !rm -rf /", "!show me my passwords",
                                    "x" * 81, "a\x01b", "a\x7fb", "bad \ud83c"])
def test_phrases_that_cannot_be_a_word(home, phrase):
    with pytest.raises(words.WordRefused):
        words.add(phrase, app(), known=free)
    assert not paths.words_file().exists()


def test_a_shell_line_is_named_as_one(home):
    with pytest.raises(words.WordRefused) as e:
        words.add("!ls", app(), known=free)
    assert e.value.why == "shell"


def test_awkward_text_survives_the_file(home):
    phrases = ['say "hi" \\ there', "мои пароли", "show me my 🍲", "straße", "tab\tand  spaces", "it's [a] {b} #c"]
    for n, p in enumerate(phrases):
        words.add(p, app(), group=f"g \"{n}\"\n\\x\x01🍲 ", now=NOW, known=free)
    want = words.load()
    words._cache = None
    assert words.load() == want and len(want) == len(phrases)
    assert [w.phrase for w in want][2] == "show me my 🍲"


def test_no_home_directory_is_no_words(home, monkeypatch):
    def nowhere():
        raise RuntimeError("Could not determine home directory.")
    monkeypatch.setattr(words.paths, "words_file", nowhere)
    assert words.load() == [] and words.lookup("my passwords") is None and words.words_unused({}) == []


def test_no_file_is_no_words_and_no_noise(home, capsys):
    assert words.load() == [] and words.lookup("anything") is None
    assert capsys.readouterr().err == ""


def test_a_file_that_does_not_parse_gives_no_words_and_one_line(home, capsys):
    put_file('[[word]]\nphrase = "my passwords\nopens = {{')
    assert words.load() == [] and words.lookup("my passwords") is None
    assert words.load() == []                                   # cached: no second line
    err = capsys.readouterr().err
    assert err.count("words.toml does not parse") == 1 and err.startswith("words: ")


def test_a_file_that_is_not_text_gives_no_words(home, capsys):
    put_file("")
    paths.words_file().write_bytes(b'[[word]]\nphrase = "\xff\xfe"\n')
    assert words.load() == []
    assert "cannot read words.toml" in capsys.readouterr().err


def test_a_wrong_row_is_skipped_and_the_rest_stay(home, capsys):
    put_file(ROW.format("good one")
             + '\n[[word]]\nphrase = "no target"\n'
             + '\n[[word]]\nphrase = "shell"\nopens = { kind = "shell", name = "ls" }\n'
             + '\n[[word]]\nphrase = 5\nopens = { kind = "app", name = "x" }\n'
             + '\n[[word]]\nphrase = "bad made"\nopens = { kind = "app", name = "x" }\nmade = "yesterday"\n'
             + '\n[[word]]\nphrase = "bad away"\nopens = { kind = "app", name = "x" }\naway = "yes"\n'
             + '\n[[word]]\nphrase = "!shell"\nopens = { kind = "app", name = "x" }\n'
             + ROW.format("Good One")                           # the same phrase again
             + '\n[[word]]\nphrase = "last"\nopens = { kind = "panel", name = "files" }\n')
    assert [w.phrase for w in words.load()] == ["good one", "last"]
    err = capsys.readouterr().err
    assert err.count("skipped row") == 7 and "row 2" in err and "twice" in err


def test_a_row_with_only_a_phrase_and_a_target_is_a_word(home):
    put_file('[[word]]\nphrase = "  Open   The Notes "\nopens = { kind = "app", name = "notes" }\n')
    w = words.lookup("open the notes")
    assert w == words.Word("open the notes", "app", "notes", 0, "", False)


def test_the_word_table_not_being_a_list_is_no_words(home, capsys):
    put_file('word = "my passwords"\n')
    assert words.load() == []
    assert "no [[word]] tables" in capsys.readouterr().err


def test_a_file_that_did_not_read_well_is_kept_aside_before_it_is_rewritten(home):
    broken = '[[word]]\nphrase = "my passwords\n'
    put_file(broken)
    words.add("my notes", app("notes"), now=NOW, known=free)
    assert [w.phrase for w in words.load()] == ["my notes"]
    assert paths.words_file().with_name("words.toml.bad").read_text() == broken
    partly = ROW.format("kept") + '\n[[word]]\nphrase = "no target"\n'
    put_file(partly, mtime_ns=10 ** 18)
    words.put_away("kept")
    assert paths.words_file().with_name("words.toml.bad").read_text() == partly
    assert words.get("kept").away is True


def test_a_good_file_leaves_no_copy_behind(home):
    words.add("my notes", app("notes"), now=NOW, known=free)
    words.add("my mail", app("mail"), now=NOW, known=free)
    assert sorted(p.name for p in paths.words_file().parent.iterdir()) == ["words.toml"]


def test_reads_are_cached_until_the_file_changes(home, monkeypatch):
    put_file(ROW.format("one"), mtime_ns=10 ** 18)
    parses = []
    real = tomllib.loads
    monkeypatch.setattr(words.tomllib, "loads", lambda text: parses.append(1) or real(text))
    for _ in range(5):
        assert words.lookup("one") is not None
    assert len(parses) == 1
    put_file(ROW.format("two"), mtime_ns=10 ** 18 + 10 ** 9)   # edited by hand: a new mtime
    assert words.lookup("one") is None and words.lookup("two") is not None
    assert len(parses) == 2
    put_file(ROW.format("six") + "\n", mtime_ns=10 ** 18 + 10 ** 9)   # the same mtime, another size
    assert words.lookup("six") is not None and len(parses) == 3
    paths.words_file().unlink()
    assert words.load() == [] and len(parses) == 3


def test_a_write_shows_at_once(home):
    assert words.lookup("late") is None
    words.add("late", app(), now=NOW, known=free)
    assert words.lookup("late") is not None
    words.put_away("late")
    assert words.lookup("late") is None


def test_a_write_that_fails_leaves_the_file_and_no_litter(home, monkeypatch):
    words.add("keep me", app(), now=NOW, known=free)
    before = paths.words_file().read_text()

    def boom(src, dst):
        raise OSError("disk full")
    with monkeypatch.context() as m:
        m.setattr(words.os, "replace", boom)
        with pytest.raises(OSError):
            words.add("lose me", app(), known=free)
        with pytest.raises(OSError):
            words.remove("keep me")
    assert paths.words_file().read_text() == before
    assert sorted(p.name for p in paths.words_file().parent.iterdir()) == ["words.toml"]
    assert [w.phrase for w in words.load()] == ["keep me"]


def test_a_row_that_would_not_read_back_is_never_written(home, monkeypatch):
    words.add("keep me", app(), now=NOW, known=free)
    before = paths.words_file().read_text()
    with monkeypatch.context() as m:
        m.setattr(words, "_quote", lambda s: '"' + s + "\n")   # a serialiser bug
        with pytest.raises(ValueError):
            words.add("another", app(), known=free)
    assert paths.words_file().read_text() == before


def test_the_unused_are_found_by_the_days_since_the_last_use(home):
    made = {"old": 40, "used lately": 40, "made lately": 3, "away already": 40, "edge": 40, "just inside": 40}
    for phrase, days_ago in made.items():
        words.add(phrase, app(), now=NOW - days_ago * DAY, known=free)
    for phrase in ("away already", "edge", "just inside"):
        words.put_away(phrase)
    words.bring_back("edge", now=NOW - 28 * DAY)
    words.bring_back("just inside", now=NOW - 27 * DAY)
    used = {"Used Lately": NOW - 10 * DAY, "old": NOW - 41 * DAY, "gone": NOW, "away already": NOW - 50 * DAY,
            "bad": "not a time", None: 1}
    # Not used for 28 days or more, counting from the use, or from when it was made or brought back.
    assert words.words_unused(used, now=NOW) == ["old", "edge"]
    assert words.words_unused(used, days=60, now=NOW) == []
    assert words.words_unused({}, days=1, now=NOW) == ["old", "used lately", "made lately", "edge", "just inside"]
    assert words.words_unused({}, now=NOW) == ["old", "used lately", "edge"]
    assert words.words_unused(None, now=NOW + 1000 * DAY) == ["old", "used lately", "made lately", "edge", "just inside"]


def test_the_default_clock_is_now(home):
    words.add("fresh", app(), known=free)
    assert words.words_unused({}) == []
    assert words.words_unused({}, days=0) == ["fresh"]

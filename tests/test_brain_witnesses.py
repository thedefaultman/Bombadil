import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from bombadil.brain import witnesses
from bombadil.brain.ingest import Ingest, Who
from bombadil.brain.store import Store
from bombadil.brain.witnesses import History, Memory, PacmanLog, TurnsLog

NOW = 1790000000.0   # 2026-09-21, a fixed "now" so the tests do not depend on the clock


@pytest.fixture
def ing(home):
    store = Store(home / "state" / "brain.db")
    yield Ingest(store, str(home), xattrs=False)
    store.close()


def _thing(ing, key):
    return ing.store.by_key(key)


# -- turns.jsonl --

def _write(path, rows, tail=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.write(tail)


def test_turns_legacy_rows_are_numbered_in_order_and_launcher_rows_skipped(home, ing):
    log = home / "state" / "turns.jsonl"
    _write(log, [{"t": NOW - 300, "prompt": "install the VPN", "ok": True},
                 {"t": NOW - 200, "kind": "local", "prompt": "wifi", "action": "wifi"},
                 {"t": NOW - 100, "prompt": "fix the race", "ok": True, "summary": "Fixed it."}])
    tl = TurnsLog(ing)
    assert tl.path == log
    assert tl.read_new() == 2
    assert ing.store.turn(1)["prompt"] == "install the VPN"
    assert ing.store.turn(2)["prompt"] == "fix the race"
    assert ing.store.turn(3) is None
    assert _thing(ing, "turn:2")["meta"]["summary"] == "Fixed it."
    assert ing.store.get_meta("turns.legacy") == "2"
    assert tl.read_new() == 0   # nothing new

    # agentd after this change writes "n"; a later legacy-style row never collides with it
    _write(log, [{"t": NOW - 50, "n": 7, "prompt": "numbered", "started": NOW - 60,
                  "unit": "bombadil-turn-7-1"},
                 {"t": NOW - 10, "prompt": "no number"}])
    assert TurnsLog(ing).read_new() == 2
    assert ing.store.turn(7)["unit"] == "bombadil-turn-7-1"
    assert ing.store.turn(7)["started"] == NOW - 60
    assert ing.store.turn(8)["prompt"] == "no number"


def test_turns_rows_of_other_kinds_are_not_turns(home, ing):
    # The self-improvement loop writes "improve" rows into the same log; none of them is a turn,
    # whatever number it carries, and they do not move the count of the turns around them.
    log = home / "state" / "turns.jsonl"
    _write(log, [{"t": NOW - 500, "prompt": "install the VPN"},
                 {"t": NOW - 400, "kind": "improve", "what": "saw 'install' three times"},
                 {"t": NOW - 300, "kind": "improve", "n": 40, "prompt": "made a widget"},
                 {"t": NOW - 200, "prompt": "fix the race"},
                 {"t": NOW - 100, "kind": "next-thing-we-add", "prompt": "unknown kind"},
                 {"t": NOW - 50, "n": 3, "prompt": "numbered"}])
    assert TurnsLog(ing).read_new() == 3
    assert [ing.store.turn(n)["prompt"] for n in (1, 2, 3)] == ["install the VPN", "fix the race", "numbered"]
    assert ing.store.turn(4) is None and ing.store.turn(40) is None
    assert ing.store.get_meta("turns.legacy") == "3"


def test_turns_half_written_line_waits_and_bad_rows_are_skipped(home, ing):
    log = home / "state" / "turns.jsonl"
    log.write_text('{"t": 1, "n": 1, "prompt": "one"}\nnot json\n[1, 2]\n'
                   '{"t": "soon", "n": 2, "prompt": "bad t"}\n'
                   '{"t": 5, "n": 3, "prompt": "thr')
    tl = TurnsLog(ing)
    assert tl.read_new() == 1
    assert ing.store.turn(1) is not None
    assert ing.store.turn(2) is None   # a row that cannot be applied is skipped whole
    assert ing.store.by_key("turn:2") is None
    assert ing.store.turn(3) is None   # still being written
    with log.open("a") as f:
        f.write('ee"}\n')
    assert tl.read_new() == 1
    assert ing.store.turn(3)["prompt"] == "three"


def test_turns_row_with_an_absurd_number_is_skipped_not_stuck(home, ing):
    _write(home / "state" / "turns.jsonl",
           [{"t": 1, "n": 10**30, "prompt": "corrupt"}, {"t": 2, "prompt": "b"},
            {"t": 3, "n": 9e18, "prompt": "too big for sqlite"}])
    assert TurnsLog(ing).read_new() == 1
    assert ing.store.turn(2)["prompt"] == "b"
    assert TurnsLog(ing).read_new() == 0


def test_turns_row_with_a_field_of_the_wrong_type_does_not_stop_the_rows_after_it(home, ing, capsys):
    log = home / "state" / "turns.jsonl"
    log.write_text(
        json.dumps({"t": NOW - 40, "n": 1, "prompt": "fine"}) + "\n"
        + json.dumps({"t": NOW - 30, "n": 2, "prompt": {"text": "an object"}, "summary": ["x"], "unit": 5,
                      "ok": "yes", "snapshot": "4", "files": "none"}) + "\n"
        + '{"t": Infinity, "n": 3, "prompt": "time has no end"}\n'
        + json.dumps({"t": NOW - 10, "n": 4, "prompt": "after"}) + "\n")
    assert TurnsLog(ing).read_new() == 3
    assert ing.store.turn(4)["prompt"] == "after"
    assert ing.store.turn(3) is None                 # its time is nonsense: the row is skipped
    assert ing.store.turn(2)["prompt"] is None       # a prompt that is not words is dropped, the turn stays
    assert _thing(ing, "turn:2")["meta"] == {}       # nor the summary, ok and snapshot that were not right
    assert capsys.readouterr().err.count("\n") == 1
    assert TurnsLog(ing).read_new() == 0             # and it is not read again, stuck on the same row


def test_turns_a_row_cut_by_a_crash_then_the_row_agentd_writes_after_the_restart(home, ing):
    log = home / "state" / "turns.jsonl"
    log.write_bytes(json.dumps({"t": 1, "n": 1, "prompt": "one"}).encode() + b"\n"
                    + b'{"t": 5, "n": 2, "prompt": "cut sho')
    tl = TurnsLog(ing)
    assert tl.read_new() == 1
    with log.open("ab") as f:   # agentd: the file does not end in a newline, so a newline comes first
        f.write(b"\n" + json.dumps({"t": 6, "n": 3, "prompt": "three"}).encode() + b"\n")
    assert tl.read_new() == 1
    assert ing.store.turn(2) is None and ing.store.turn(3)["prompt"] == "three"
    log.write_bytes(log.read_bytes() + b'\xff\xfe{"t": 7, "n": 4}\n')   # bytes that are not UTF-8 either
    assert tl.read_new() == 0


def test_turns_start_over_when_the_log_is_replaced(home, ing):
    log = home / "state" / "turns.jsonl"
    _write(log, [{"t": 1, "prompt": "a"}, {"t": 2, "prompt": "b"}, {"t": 3, "prompt": "c"}])
    tl = TurnsLog(ing)
    assert tl.read_new() == 3
    # shrank: read from the top again, counting legacy rows from 1
    log.write_text(json.dumps({"t": 4, "prompt": "fresh"}) + "\n")
    assert tl.read_new() == 1
    assert ing.store.turn(1)["prompt"] == "fresh"
    # same size or longer but a different first line: also a new log
    log.write_text(json.dumps({"t": 5, "prompt": "other"}) + "\n" + json.dumps({"t": 6, "prompt": "x"})
                   + "\n")
    assert tl.read_new() == 2
    assert ing.store.turn(1)["prompt"] == "other"


def test_turns_row_files_make_things(home, ing):
    f = home / "setup-wg.sh"
    f.write_text("#!/bin/sh\n")
    _write(home / "state" / "turns.jsonl",
           [{"t": NOW, "n": 41, "prompt": "install the VPN", "started": NOW - 30,
             "files": {"wrote": [str(f)]}}])
    assert TurnsLog(ing).read_new() == 1
    thing = ing.store.by_path(str(f))
    assert thing["made_by"] == "turn"
    assert thing["made_by_thing"] == _thing(ing, "turn:41")["id"]


def test_turns_missing_or_unreadable_log_is_quiet(home, ing, capsys):
    assert TurnsLog(ing, home / "nope.jsonl").read_new() == 0
    assert capsys.readouterr().err == ""
    (home / "adir").mkdir()
    assert TurnsLog(ing, home / "adir").read_new() == 0
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "bombadil-brain" in err


# -- Chromium's History --

HISTORY_SCHEMA = """
CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR);
CREATE TABLE urls(id INTEGER PRIMARY KEY AUTOINCREMENT, url LONGVARCHAR, title LONGVARCHAR,
  visit_count INTEGER DEFAULT 0 NOT NULL, typed_count INTEGER DEFAULT 0 NOT NULL,
  last_visit_time INTEGER NOT NULL, hidden INTEGER DEFAULT 0 NOT NULL);
CREATE TABLE visits(id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL, visit_time INTEGER NOT NULL,
  from_visit INTEGER, external_referrer_url TEXT, transition INTEGER DEFAULT 0 NOT NULL, segment_id INTEGER,
  visit_duration INTEGER DEFAULT 0 NOT NULL, incremented_omnibox_typed_score BOOLEAN DEFAULT FALSE NOT NULL,
  opener_visit INTEGER, originator_cache_guid TEXT, originator_visit_id INTEGER,
  originator_from_visit INTEGER,
  originator_opener_visit INTEGER, is_known_to_sync BOOLEAN DEFAULT FALSE NOT NULL,
  consider_for_ntp_most_visited BOOLEAN DEFAULT FALSE NOT NULL, visited_link_id INTEGER DEFAULT 0 NOT NULL,
  app_id TEXT);
CREATE TABLE downloads(id INTEGER PRIMARY KEY, guid VARCHAR NOT NULL, current_path LONGVARCHAR NOT NULL,
  target_path LONGVARCHAR NOT NULL, start_time INTEGER NOT NULL, received_bytes INTEGER NOT NULL,
  total_bytes INTEGER NOT NULL, state INTEGER NOT NULL, danger_type INTEGER NOT NULL,
  interrupt_reason INTEGER NOT NULL, hash BLOB NOT NULL, end_time INTEGER NOT NULL, opened INTEGER NOT NULL,
  last_access_time INTEGER NOT NULL, transient INTEGER NOT NULL, referrer VARCHAR NOT NULL,
  site_url VARCHAR NOT NULL, embedder_download_data VARCHAR NOT NULL, tab_url VARCHAR NOT NULL,
  tab_referrer_url VARCHAR NOT NULL, http_method VARCHAR NOT NULL, by_ext_id VARCHAR NOT NULL,
  by_ext_name VARCHAR NOT NULL, etag VARCHAR NOT NULL, last_modified VARCHAR NOT NULL,
  mime_type VARCHAR(255) NOT NULL, original_mime_type VARCHAR(255) NOT NULL);
CREATE TABLE downloads_url_chains(id INTEGER NOT NULL, chain_index INTEGER NOT NULL, url LONGVARCHAR NOT NULL,
  PRIMARY KEY(id, chain_index));
"""
LINK = 0 | 0x10000000 | 0x20000000        # a link click, a chain of one
TYPED = 1 | 0x10000000 | 0x20000000


def chrome(t):
    return int((t + 11644473600) * 1_000_000)


class FakeChromium:
    """A History database with Chromium's schema, written the way Chromium writes it."""

    def __init__(self, path, wal=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None)
        if wal:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.executescript(HISTORY_SCHEMA)
        self.db.execute("INSERT INTO meta VALUES('version', '70')")

    def visit(self, url, title, t, seconds=0.0, transition=LINK, hidden=0):
        row = self.db.execute("SELECT id FROM urls WHERE url = ?", (url,)).fetchone()
        if row is None:
            uid = self.db.execute("INSERT INTO urls(url, title, visit_count, last_visit_time, hidden) "
                                  "VALUES(?, ?, 1, ?, ?)", (url, title, chrome(t), hidden)).lastrowid
        else:
            uid = row[0]
            self.db.execute("UPDATE urls SET title = ?, last_visit_time = ? WHERE id = ?",
                            (title, chrome(t), uid))
        return self.db.execute("INSERT INTO visits(url, visit_time, transition, visit_duration) "
                               "VALUES(?, ?, ?, ?)",
                               (uid, chrome(t), transition, int(seconds * 1_000_000))).lastrowid

    def download(self, target, chain, tab_url, t, state=1, referrer=""):
        did = (self.db.execute("SELECT MAX(id) FROM downloads").fetchone()[0] or 0) + 1
        self.db.execute(
            "INSERT INTO downloads VALUES(?, ?, ?, ?, ?, 10, 10, ?, 0, 0, x'', ?, 0, 0, 0, ?, '', '', ?, '', "
            "'GET', '', '', '', '', 'application/pdf', 'application/pdf')",
            (did, f"guid-{did}", target + ".crdownload", target, chrome(t - 5), state,
             chrome(t) if state == 1 else 0, referrer, tab_url))
        for i, url in enumerate(chain):
            self.db.execute("INSERT INTO downloads_url_chains VALUES(?, ?, ?)", (did, i, url))
        return did


def _chromium(home, profile="Default", **kw):
    return FakeChromium(home / ".config" / "chromium" / profile / "History", **kw)


def _events(ing, thing, kind):
    return ing.store.events(thing, kinds=(kind,))


def test_history_finds_profiles_and_env_overrides(home, ing, monkeypatch):
    a = _chromium(home)
    b = FakeChromium(home / "config" / "chromium" / "Profile 1" / "History")   # ~/.config/bombadil/chromium
    h = History(ing)
    assert h.histories() == [str(a.path), str(b.path)]
    assert str(a.path) + "-journal" in h.files() and str(b.path) in h.files()
    monkeypatch.setenv("BOMBADIL_CHROMIUM_DIRS", f"{home / 'nowhere'}:{b.path.parent.parent}")
    assert History(ing).histories() == [str(b.path)]
    assert History(ing, profiles=[a.path.parent]).histories() == [str(a.path)]   # a profile folder
    assert History(ing, profiles=[a.path]).histories() == [str(a.path)]          # a History file


def test_history_imports_visits_with_times_and_durations(home, ing):
    ch = _chromium(home)
    ch.visit("https://rent-portal.example/lease#terms", "Lease renewal 2026", NOW - 600, seconds=95.5)
    ch.visit("https://www.rent-portal.example/login", "Sign in", NOW - 700, transition=TYPED)
    ch.visit("https://ads.example/frame", "ad", NOW - 650, transition=3 | 0x30000000)        # a subframe
    ch.visit("https://t.co/abc", "", NOW - 640, transition=0x10000000)                        # first hop
    ch.visit("https://example.org/landed", "Landed", NOW - 639, transition=0x20000000 | 0x80000000)
    ch.visit("https://hidden.example/", "hidden", NOW - 630, hidden=1)
    ch.visit("chrome://settings/", "Settings", NOW - 620)
    ch.visit("file:///home/user/a.txt", "a.txt", NOW - 610)
    work = home / "run" / "brain-tmp"
    h = History(ing)
    assert h.import_new(now=NOW) == 3
    page = _thing(ing, "url:https://rent-portal.example/lease")
    assert page["title"] == "Lease renewal 2026"
    ev = _events(ing, page["id"], "visit")[0]
    assert ev["t"] == pytest.approx(NOW - 600, abs=1e-3)
    assert ev["actor"] == "you" and ev["detail"]["duration"] == 95.5
    assert ing.store.get(page["area"])["key"] == "site:rent-portal.example"
    assert _thing(ing, "url:https://www.rent-portal.example/login") is not None
    assert _thing(ing, "url:https://example.org/landed") is not None
    for gone in ("https://ads.example/frame", "https://t.co/abc", "https://hidden.example/"):
        assert _thing(ing, f"url:{gone}") is None
    assert work.is_dir() and list(work.iterdir()) == []   # the copy never outlives the import

    # at most once a minute; due() says when
    ch.visit("https://example.org/next", "Next", NOW - 5)
    assert h.import_new(now=NOW + 30) == 0
    assert h.due(now=NOW + 30) == NOW + 60
    assert h.import_new(now=NOW + 61) == 1   # only what is new
    assert len(_events(ing, page["id"], "visit")) == 1
    assert h.due(now=NOW + 200) == NOW + 200
    assert h.import_new(now=NOW + 200) == 0   # unchanged: not even copied


def test_history_title_that_is_not_utf8_does_not_stop_the_import(home, ing):
    ch = _chromium(home)
    ch.visit("https://bad.example/", "placeholder", NOW - 10)
    ch.db.execute("UPDATE urls SET title = CAST(x'4c6561736520ff' AS TEXT)")
    ch.visit("https://good.example/", "good", NOW - 5)
    assert History(ing).import_new(now=NOW) == 2
    assert _thing(ing, "url:https://bad.example/")["title"] == "Lease \ufffd"


def test_history_first_import_of_a_big_history_starts_a_month_back(home, ing, monkeypatch):
    monkeypatch.setattr(witnesses, "FIRST_BIG", 3)
    ch = _chromium(home)
    for i in range(4):
        ch.visit(f"https://old.example/{i}", "old", NOW - 90 * 86400 + i)
    ch.visit("https://new.example/", "new", NOW - 86400)
    assert History(ing).import_new(now=NOW) == 1
    assert _thing(ing, "url:https://old.example/0") is None
    # later imports go on from there, and never go back for the old ones
    ch.visit("https://newer.example/", "newer", NOW)
    assert History(ing).import_new(now=NOW + 100) == 1
    assert _thing(ing, "url:https://old.example/3") is None


def test_history_reused_visit_ids_are_still_seen(home, ing):
    ch = _chromium(home)
    ch.db.executescript("DROP TABLE visits; "
                        "CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER NOT NULL, "
                        "visit_time INTEGER NOT NULL, transition INTEGER DEFAULT 0 NOT NULL, "
                        "visit_duration INTEGER DEFAULT 0 NOT NULL)")
    ch.visit("https://a.example/", "a", NOW - 100)
    ch.visit("https://b.example/", "b", NOW - 90)
    h = History(ing)
    assert h.import_new(now=NOW) == 2
    ch.db.execute("DELETE FROM visits")   # "clear browsing data": ids start again at 1
    ch.visit("https://c.example/", "c", NOW - 10)
    assert h.import_new(now=NOW + 100) == 1
    assert _thing(ing, "url:https://c.example/") is not None


def test_history_downloads_come_from_the_page_you_were_on(home, ing):
    dl = home / "Downloads"
    dl.mkdir()
    (dl / "lease-2026.pdf").write_bytes(b"%PDF-1.7")
    (dl / "big.iso").write_bytes(b"iso")
    ch = _chromium(home)
    ch.visit("https://rent-portal.example/lease", "Lease renewal 2026", NOW - 120)
    ch.download(str(dl / "lease-2026.pdf"), ["https://rent-portal.example/get?id=1",
                                             "https://cdn.rent-portal.example/lease-2026.pdf"],
                "https://rent-portal.example/lease", NOW - 100)
    waiting = ch.download(str(dl / "big.iso"), ["https://mirror.example/big.iso"], "", NOW - 50, state=0,
                          referrer="https://mirror.example/")
    ch.download(str(dl / "cancelled.zip"), ["https://x.example/c.zip"], "", NOW - 40, state=2)
    h = History(ing)
    assert h.import_new(now=NOW) == 2   # one visit, one finished download
    pdf = ing.store.by_path(str(dl / "lease-2026.pdf"))
    ev = _events(ing, pdf["id"], "download")[0]
    assert ev["detail"]["url"] == "https://cdn.rent-portal.example/lease-2026.pdf"   # the chain's last hop
    page = _thing(ing, "url:https://rent-portal.example/lease")
    assert ev["other"] == page["id"]
    assert [ln["dst"] for ln in ing.store.links_from(pdf["id"], ("came_from",))] == [page["id"]]
    assert ing.store.by_path(str(dl / "big.iso")) is None

    # the one still downloading is looked at again once it finishes
    ch.db.execute("UPDATE downloads SET state = 1, end_time = ? WHERE id = ?", (chrome(NOW + 30), waiting))
    assert h.import_new(now=NOW + 100) == 1
    iso = ing.store.by_path(str(dl / "big.iso"))
    ev = _events(ing, iso["id"], "download")[0]
    assert ev["other"] == _thing(ing, "url:https://mirror.example/")["id"]   # no tab: the referrer
    assert ev["t"] == pytest.approx(NOW + 30, abs=1e-3)


def _touch(path, seconds):
    """A commit in the same clock tick as the last one leaves size and mtime as they were; move it on."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + int(seconds * 1e9)))


def test_history_titles_and_durations_that_chromium_writes_later_are_filled_in(home, ing):
    ch = _chromium(home)
    vid = ch.visit("https://rent-portal.example/lease", "", NOW - 300)        # loaded, not yet titled
    ch.visit("https://docs.example/a", "Docs", NOW - 200)                     # titled, still open
    h = History(ing)
    assert h.import_new(now=NOW) == 2
    page = _thing(ing, "url:https://rent-portal.example/lease")
    assert page["title"] == "https://rent-portal.example/lease"
    docs = _thing(ing, "url:https://docs.example/a")
    assert "duration" not in (_events(ing, docs["id"], "visit")[0]["detail"] or {})

    ch.db.execute("UPDATE urls SET title = 'Lease renewal 2026' WHERE id = 1")
    ch.db.execute("UPDATE visits SET visit_duration = ? WHERE id = ?", (95_500_000, vid))
    ch.db.execute("UPDATE visits SET visit_duration = 30000000 WHERE id = 2")
    _touch(ch.path, 5)
    assert h.import_new(now=NOW + 61) == 0        # no new visit, but what was missing is there now
    assert _thing(ing, "url:https://rent-portal.example/lease")["title"] == "Lease renewal 2026"
    assert _events(ing, page["id"], "visit")[0]["detail"]["duration"] == 95.5
    assert _events(ing, docs["id"], "visit")[0]["detail"]["duration"] == 30.0
    assert len(_events(ing, page["id"], "visit")) == 1
    # done with: not looked at again, so an id used by another visit later is not mistaken for it
    assert json.loads(ing.store.get_meta(f"history.{ch.path.parent}.follow")) == []


def test_history_visits_synced_from_another_device_are_not_pages_you_read_here(home, ing):
    ch = _chromium(home)
    ch.visit("https://here.example/", "Here", NOW - 20)
    ch.visit("https://phone.example/", "Phone", NOW - 10)
    ch.db.execute("UPDATE visits SET originator_cache_guid = 'my-phone' WHERE id = 2")
    assert History(ing).import_new(now=NOW) == 1
    assert _thing(ing, "url:https://phone.example/") is None


def test_history_download_from_a_chrome_tab_comes_from_its_referrer_and_keeps_no_data_url(home, ing):
    dl = home / "Downloads"
    dl.mkdir()
    (dl / "chart.png").write_bytes(b"png")
    ch = _chromium(home)
    ch.download(str(dl / "chart.png"), ["data:image/png;base64," + "A" * 200_000], "chrome://downloads/",
                NOW - 10, referrer="https://mirror.example/gallery")
    assert History(ing).import_new(now=NOW) == 1
    thing = ing.store.by_path(str(dl / "chart.png"))
    ev = _events(ing, thing["id"], "download")[0]
    assert ev["detail"]["url"] == ""    # not stored: it is the file itself
    assert ev["other"] == _thing(ing, "url:https://mirror.example/gallery")["id"]


def test_history_downloads_are_seen_again_after_clear_browsing_data(home, ing):
    dl = home / "Downloads"
    dl.mkdir()
    for name in ("old.pdf", "new.pdf"):
        (dl / name).write_bytes(b"%PDF")
    ch = _chromium(home)
    for i in range(5):
        ch.download(str(dl / "old.pdf"), ["https://a.example/old.pdf"], "https://a.example/", NOW - 900 + i)
    h = History(ing)
    assert h.import_new(now=NOW) == 5
    ch.db.execute("DELETE FROM downloads")          # ids start again at 1
    ch.db.execute("DELETE FROM downloads_url_chains")
    ch.download(str(dl / "new.pdf"), ["https://b.example/new.pdf"], "https://b.example/", NOW - 20)
    _touch(ch.path, 5)
    assert h.import_new(now=NOW + 100) == 1
    assert _events(ing, ing.store.by_path(str(dl / "new.pdf"))["id"], "download")


def test_history_copy_left_behind_by_a_crashed_import_is_swept(home, ing):
    ch = _chromium(home)
    ch.visit("https://a.example/", "a", NOW - 10)
    work = home / "tmpwork"
    stale, fresh = work / "history-crashed", work / "history-running"
    for d in (stale, fresh):
        d.mkdir(parents=True)
        (d / "History").write_bytes(b"x" * 1000)
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    assert History(ing, workdir=work).import_new(now=NOW) == 1
    assert not stale.exists() and fresh.exists()    # only what nobody could be using
    assert sorted(p.name for p in work.iterdir()) == ["history-running"]


def test_history_is_read_while_chromium_holds_it_locked_mid_transaction(home, ing):
    ch = _chromium(home)
    ch.visit("https://committed.example/", "committed", NOW - 100)
    ch.db.execute("PRAGMA locking_mode=EXCLUSIVE")
    ch.db.execute("PRAGMA cache_size=1")   # spill pages into the file before the commit
    ch.db.execute("BEGIN")
    for i in range(300):
        ch.visit(f"https://uncommitted.example/{i}", "x" * 300, NOW - 50)
    assert os.path.getsize(str(ch.path) + "-journal") > 0
    with pytest.raises(sqlite3.OperationalError):   # nobody else can open it in place
        sqlite3.connect(ch.path, timeout=0).execute("SELECT COUNT(*) FROM visits").fetchone()
    assert History(ing).import_new(now=NOW) == 1
    assert _thing(ing, "url:https://committed.example/") is not None
    assert _thing(ing, "url:https://uncommitted.example/0") is None   # the hot journal was rolled back
    ch.db.execute("ROLLBACK")


def test_history_in_wal_mode_includes_what_is_still_in_the_wal(home, ing):
    ch = _chromium(home, wal=True)
    ch.visit("https://in-wal.example/", "wal", NOW - 10)
    assert os.path.getsize(str(ch.path) + "-wal") > 0
    assert History(ing).import_new(now=NOW) == 1
    assert _thing(ing, "url:https://in-wal.example/") is not None


def test_history_that_is_not_a_database_costs_one_line(home, ing, capsys):
    p = home / ".config" / "chromium" / "Default" / "History"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"this is not sqlite" * 100)
    h = History(ing, workdir=home / "tmpwork")
    assert h.import_new(now=NOW) == 0
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "History" in err
    assert list((home / "tmpwork").iterdir()) == []


def test_history_copy_that_keeps_changing_is_tried_later(home, ing, monkeypatch, capsys):
    ch = _chromium(home)
    ch.visit("https://a.example/", "a", NOW)
    ticks = iter(range(100))
    monkeypatch.setattr(witnesses, "_signature", lambda hist: ((next(ticks), 0, 0), None, None))
    h = History(ing)
    assert h.import_new(now=NOW) == 0
    assert "kept changing" in capsys.readouterr().err
    monkeypatch.undo()
    assert h.import_new(now=NOW + 61) == 1


# -- pacman.log --

PACMAN = """\
[2026-09-27T10:00:00+0200] [PACMAN] Running 'pacman -S --noconfirm ffmpeg'
[2026-09-27T10:00:01+0200] [ALPM] transaction started
[2026-09-27T10:00:02+0200] [ALPM] installed libvpx (1.14.1-1)
[2026-09-27T10:00:03+0200] [ALPM] installed ffmpeg (2:7.1-1)
[2026-09-27T10:00:03+0200] [ALPM-SCRIPTLET] ldconfig: /usr/lib/libfoo.so.1 is not a symbolic link
[2026-09-27T10:00:04+0200] [ALPM] upgraded linux (6.10.1.arch1-1 -> 6.10.2.arch1-1)
[2026-09-27T10:00:05+0200] [ALPM] downgraded mesa (1:24.2.1-1 -> 1:24.1.0-1)
[2026-09-27T10:00:06+0200] [ALPM] removed nano (8.1-1)
[2026-09-27T10:00:06+0200] [ALPM] running '30-systemd-update.hook'...
[2026-09-27T10:00:07+0200] [ALPM] transaction completed
"""


def _pk(ing, name):
    return _thing(ing, f"package:{name}")


def test_pacman_times_and_parsing():
    t = witnesses.pacman_time("2026-09-27T10:00:00+0200")
    assert t == datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc).timestamp()
    assert witnesses.pacman_time("2026-09-27T10:00:00-0500") == t + 7 * 3600
    assert witnesses.pacman_time("2019-01-05 10:00") == time.mktime((2019, 1, 5, 10, 0, 0, 0, 0, -1))
    assert witnesses.pacman_time("yesterday") is None
    line = "[2026-09-27T10:00:04+0200] [ALPM] upgraded linux (6.10.1-1 -> 6.10.2-1)\n"
    assert witnesses.parse_pacman(line) == ("linux", "6.10.2-1", "upgrade", t + 4)
    assert witnesses.parse_pacman("[2026-09-27T10:00:04+0200] [PACMAN] installed x (1)") is None
    assert witnesses.parse_pacman("[2026-09-27T10:00:04+0200] [ALPM-SCRIPTLET] installed x (1)") is None


def test_pacman_log_attributes_to_the_turn_that_was_running(home, ing):
    log = home / "pacman.log"
    log.write_text(PACMAN)
    t0 = witnesses.pacman_time("2026-09-27T10:00:00+0200")
    turn = ing.turn(12, prompt="install ffmpeg", started=t0 - 1.5, ended=t0 + 4.2)
    assert PacmanLog(ing, log).read_new() == 5
    ff = _pk(ing, "ffmpeg")
    assert ff["meta"]["version"] == "2:7.1-1"
    ev = _events(ing, ff["id"], "install")[0]
    assert (ev["actor"], ev["actor_thing"], ev["via"]) == ("turn", turn, "pacman")
    assert ev["t"] == t0 + 3
    assert ing.store.get(ff["area"])["key"] == "system"
    assert [ln["dst"] for ln in ing.store.links_from(ff["id"], ("made_by",))] == [turn]
    assert _pk(ing, "linux")["meta"]["version"] == "6.10.2.arch1-1"
    assert _pk(ing, "mesa")["meta"]["version"] == "1:24.1.0-1"
    nano = _pk(ing, "nano")
    assert nano["deleted"] == t0 + 6
    assert _events(ing, nano["id"], "remove")[0]["actor"] == "system"   # after the turn ended
    assert _pk(ing, "30-systemd-update.hook") is None


def test_pacman_log_reads_only_what_was_added_and_uses_the_writer_seen(home, ing):
    log = home / "pacman.log"
    log.write_text(PACMAN)
    pl = PacmanLog(ing, log)
    assert pl.read_new() == 5
    assert pl.read_new() == 0
    now = datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    with log.open("a") as f:
        f.write(f"[{now}] [ALPM] installed qemu-full (9.1.0-1)\n[{now}] [ALPM] installed half")
    kit = ing.store.upsert_key("session", "session:bombadil/kit", "kit on bombadil")
    assert pl.read_new(Who("session", kit, "pacman")) == 1
    ev = _events(ing, _pk(ing, "qemu-full")["id"], "install")[0]
    assert (ev["actor"], ev["actor_thing"]) == ("session", kit)
    assert _pk(ing, "half") is None
    # rotated or truncated: start over
    log.write_text("[2026-09-28T09:00:00+0000] [ALPM] installed zellij (0.41-1)\n")
    assert pl.read_new() == 1
    assert _pk(ing, "zellij") is not None


def _alpm(t, verb, name, version):
    stamp = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return f"[{stamp}] [ALPM] {verb} {name} ({version})\n"


def test_pacman_first_read_of_a_long_log_keeps_one_line_per_package_still_installed(home, ing):
    now = time.time()
    year = 400 * 86400
    log = home / "pacman.log"
    log.write_text(
        _alpm(now - year, "installed", "alpha", "1.0-1") + _alpm(now - year + 5, "installed", "beta", "1.0-1")
        + _alpm(now - year + 10, "upgraded", "alpha", "1.0-1 -> 2.0-1")
        + _alpm(now - year + 20, "removed", "beta", "1.0-1")
        + _alpm(now - year + 30, "upgraded", "alpha", "2.0-1 -> 3.0-1")
        + "[2026-09-27T10:00:01+0000] [ALPM] transaction started\n"
        + _alpm(now - 86400, "upgraded", "alpha", "3.0-1 -> 4.0-1")
        + _alpm(now - 60, "installed", "gamma", "1.0-1") + _alpm(now, "installed", "cut", "1-1")[:-20])
    pl = PacmanLog(ing, log)
    assert pl.read_new() == 3          # alpha as it was, and what happened in the last 90 days
    assert _pk(ing, "beta") is None    # installed and removed long ago: not there to be described
    alpha = _pk(ing, "alpha")
    assert alpha["meta"]["version"] == "4.0-1"
    assert [e["detail"]["version"] for e in ing.store.events(alpha["id"])] == ["4.0-1", "3.0-1"]
    assert _pk(ing, "gamma") is not None
    assert pl.read_new() == 0
    with log.open("a") as f:
        f.write("\n" + _alpm(now + 1, "removed", "gamma", "1.0-1"))
    assert pl.read_new() == 1 and _pk(ing, "gamma")["deleted"] is not None


def test_pacman_first_read_of_a_huge_log_is_quick(home, ing):
    """A machine that has run for years has a log of a hundred thousand lines; the brain must not
    be busy with it for minutes at start."""
    now = time.time()
    log = home / "pacman.log"
    with log.open("w") as f:
        for i in range(60_000):
            f.write(_alpm(now - 1000 * 86400 + i * 60, "upgraded", f"pkg-{i % 500}",
                          f"1.{i}-1 -> 1.{i + 1}-1"))
    t0 = time.monotonic()
    assert PacmanLog(ing, log).read_new() == 500
    assert time.monotonic() - t0 < 5


def test_pacman_log_that_was_trimmed_is_read_again_without_counting_twice(home, ing):
    now = time.time()
    log = home / "pacman.log"
    lines = [_alpm(now - 300 + i, "installed", f"p{i}", "1-1") for i in range(4)]
    log.write_text("".join(lines))
    pl = PacmanLog(ing, log)
    assert pl.read_new() == 4
    log.write_text("".join(lines[2:]) + _alpm(now, "installed", "fresh", "1-1"))   # someone cut the top off
    assert pl.read_new() == 1
    assert len(ing.store.events(_pk(ing, "p3")["id"])) == 1


def test_pacman_log_missing_is_quiet(home, ing, capsys):
    assert PacmanLog(ing, home / "none.log").read_new() == 0
    assert capsys.readouterr().err == ""


# -- memory.md --

def test_memory_first_existing_file_wins_and_facts_follow_its_lines(home, ing):
    mem = home / ".bombadil" / "memory.md"
    mem.parent.mkdir()
    mem.write_text("# About Daniel\n\n- Prefers Hyprland\n- Lives in Lisbon\n")
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").symlink_to(mem)
    m = Memory(ing)
    assert str(mem) in m.files() and str(home / ".claude" / "CLAUDE.md") in m.files()
    turn = ing.turn(3, prompt="remember I live in Lisbon", started=NOW - 10, ended=NOW)
    os.utime(mem, (NOW - 5, NOW - 5))
    assert m.read() == 2   # at start: whoever was running when it was written
    facts = {t["title"]: t for t in ing.store.q("SELECT * FROM things WHERE kind = 'fact'")}
    assert set(facts) == {"Prefers Hyprland", "Lives in Lisbon"}
    assert facts["Lives in Lisbon"]["made_by_thing"] == turn
    mem.write_text("- Prefers Hyprland\n- Has a cat\n")
    assert m.read(Who("you", None, "foot")) == 2
    live = {t["title"] for t in ing.store.q("SELECT * FROM things WHERE kind = 'fact' AND deleted IS NULL")}
    assert live == {"Prefers Hyprland", "Has a cat"}
    mem.unlink()   # mid-save or gone: nothing is forgotten for it
    (home / ".claude" / "CLAUDE.md").unlink()
    assert m.read() == 0
    assert ing.store.one("SELECT COUNT(*) AS n FROM things WHERE kind = 'fact' AND deleted IS NULL")["n"] == 2


def test_memory_falls_back_to_the_cli_files_and_never_reads_private_ones(home, ing):
    agents = home / ".codex" / "AGENTS.md"
    agents.parent.mkdir()
    agents.write_text("- Uses Codex sometimes\n")
    assert Memory(ing).read() == 1
    secret = home / "secrets.md"
    secret.write_text("- the vault code is 1234\n")
    assert Memory(ing, files=[secret]).read() == 0
    assert ing.store.q("SELECT * FROM things WHERE title LIKE '%vault%'") == []

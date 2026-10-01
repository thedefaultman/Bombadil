"""The store, the database and the cost of the loop: what a review of the counting half found.

Words that are no group's kept past 90 days, a loop.db anyone on the machine could read, an idle poll that
loaded every group he ever made, an ingest that held the write lock for the whole backlog, a Forget that
kept the sentence he said no to, and a damaged loop.db with nothing said about what to do.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from bombadil import paths
from bombadil.loop import db, habits, ledger, offers, report, store
from bombadil.loop import service as service_mod
from bombadil.loop.offers import Config

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
sys.path.insert(0, str(Path(__file__).parent))
import golden_corpus as gc
from test_loop_service import (  # (the service tests' rig and what it needs)
    machine,  # noqa: F401
    passwords_asks,
    rig_of,  # noqa: F401
    until,
)

DAY = 86400
APPS = gc.app_list()


def at(day, hour=12, minute=0):
    return gc.epoch(day, f"{hour:02d}:{minute:02d}")


def fresh(tmp_path, name="loop.db", **kw):
    kw.setdefault("app_list", APPS)
    kw.setdefault("config", Config())
    return store.LoopStore(tmp_path / name, **kw)


def partial(tmp_path, asks, day):
    """A ledger holding the asks up to `day`, with every ask's own log already written."""
    turns, logs = gc.write_corpus(tmp_path / "state", asks)
    rows = [json.loads(line) for line in turns.read_text().splitlines()]
    end = gc.epoch(day, "23:59")
    turns.write_text("".join(json.dumps(r) + "\n" for r in rows if r["started"] <= end))
    return turns, logs


def append(turns, logs, asks):
    with turns.open("a") as f:
        f.writelines(json.dumps(gc.row_of(a, logs)) + "\n" for a in asks)


def dump(s):
    """Everything in the file but `meta` (whose rows may be rewritten in another order)."""
    return "\n".join(line for line in s.conn.iterdump() if not line.startswith('INSERT INTO "meta"'))


def write_rows(path, n, start=1_700_000_000.0):
    """A ledger of `n` model turns, ten minutes apart, each its own ask."""
    with path.open("w") as f:
        for i in range(n):
            t = start + i * 600
            f.write(json.dumps({"t": t + 30, "prompt": f"make thing{i} now", "ok": True, "id": f"r{i}",
                                "started": t, "seconds": 30.0, "origin": "typed", "v": 2,
                                "tools": {"n": 4, "names": ["Bash"]}}) + "\n")
    return start + n * 600


def passwords_never(tmp_path):
    """A store that has taken the passwords offer and been told Never, and its ledger and logs."""
    asks = gc.load_asks()
    turns, logs = partial(tmp_path, asks, 5)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    offer = s.ripe_offer(at(5, 23))
    assert s.answer(offer.group.id, "never", now=at(6))["state"] == "said_no"
    return s, turns, logs, asks


# -- 5: the words of what no group holds are aged out too --

def test_the_words_of_asks_held_back_by_a_never_go_after_90_days(home, tmp_path):
    s, turns, logs, asks = passwords_never(tmp_path)
    append(turns, logs, [a for a in asks if a["group"] == "passwords" and a["t"] > at(5, 23)])
    s.ingest(turns, now=at(8))
    held = "SELECT COUNT(*) FROM requests WHERE reason='never' AND text!=''"
    assert s.conn.execute(held).fetchone()[0] > 0
    last = max(a["t"] for a in asks if a["group"] == "passwords")
    s.ripe_offer(last + 60 * DAY)                               # not yet: the newest of them is not 90 days old
    assert s.conn.execute(held).fetchone()[0] > 0
    s.ripe_offer(last + 100 * DAY)
    assert s.conn.execute(held).fetchone()[0] == 0
    assert s.conn.execute("SELECT COUNT(*) FROM requests WHERE reason='never'").fetchone()[0] > 0   # counted still
    assert "show me my passwords" not in dump(s)


def test_the_words_of_a_retry_that_joined_no_group_go_after_90_days(home, tmp_path):
    s = fresh(tmp_path)
    s.ingest(gc.write_corpus(tmp_path / "state", [])[0], now=at(1))
    for i, reason in enumerate(("failed", "stopped", "undone", "rephrase")):
        s.conn.execute("INSERT INTO requests(id, t, ended, day, text, reason, grp) VALUES(?,?,?,?,?,?,NULL)",
                       (f"f{i}", at(1), at(1), "2026-09-08", "fix the wifi again", reason))
    s.ripe_offer(at(1) + 30 * DAY)
    assert s.conn.execute("SELECT COUNT(*) FROM requests WHERE text!=''").fetchone()[0] == 4
    s.ripe_offer(at(1) + 100 * DAY)
    assert s.conn.execute("SELECT COUNT(*) FROM requests WHERE text!=''").fetchone()[0] == 0


def test_old_members_of_a_group_still_being_asked_keep_their_words(home, tmp_path):
    asks = [{"id": f"w{i}", "text": "show me the weather", "day": d, "at": "09:00", "t": at(d, 9), "group": "w",
             "events": [], "seconds": 10} for i, d in enumerate((0, 40, 80, 120))]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(125))
    s.conn.execute("INSERT INTO requests(id, t, ended, day, text, reason, grp) VALUES(?,?,?,?,?,?,NULL)",
                   ("old-never", at(0), at(0), "2026-09-07", "a held back ask", "never"))
    s.ripe_offer(at(125))
    texts = dict(s.conn.execute("SELECT id, text FROM requests").fetchall())
    assert texts["old-never"] == ""
    assert all(texts[f"w{i}"] for i in range(4))                # the group is alive: regroup needs its members


# -- 42: Forget does not keep the sentence he said no to --

def test_forgetting_what_he_asks_also_forgets_the_sentence_he_said_no_to(home, tmp_path):
    s, _, _, _ = passwords_never(tmp_path)
    stored = s.said_no()[0]
    assert stored["sentence"] and stored["label"]
    s.forget_asks(now=at(7))
    after = s.said_no()
    assert [(x["id"], x["form"], x["label"]) for x in after] == [(stored["id"], stored["form"], stored["label"])]
    assert after[0]["sentence"] == ""
    assert stored["sentence"] not in dump(s)
    assert s.conn.execute("SELECT signature FROM nevers").fetchone()[0]       # what it looked like stays


def test_the_sentence_he_said_no_to_goes_with_the_words_of_its_group_after_90_days(home, tmp_path):
    s, _, _, _ = passwords_never(tmp_path)
    sentence = s.said_no()[0]["sentence"]
    s.ripe_offer(at(5, 23) + 30 * DAY)
    assert s.said_no()[0]["sentence"] == sentence
    s.ripe_offer(at(5, 23) + 100 * DAY)
    after = s.said_no()[0]
    assert after["sentence"] == "" and after["label"] and after["id"] == "gpw1"
    assert sentence not in dump(s)


# -- 8: an idle poll loads the groups that could be ripe, not every group he ever made --

def plant(s, gid, n, last, state="counting"):
    """A group of `n` asks, the last at `last`, put straight into the table (a long history in one line)."""
    times = [last - i * DAY for i in range(n)][::-1]
    g = habits.Group(id=gid, label=gid, members=[f"{gid}-{i}" for i in range(n)], first=times[0], last=last,
                     days=[f"2026-09-0{1 + i % 9}" for i in range(n)], n=n, times=times,
                     seconds=[60.0] * n, steps=[1] * n, sentences=[f"ask {gid}"])
    data = g.to_dict()
    for k in ("state", "form"):
        data.pop(k)
    s.conn.execute("INSERT INTO asks(id, label, first, last, days, n, weight, members, state, data) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (gid, gid, g.first, g.last, json.dumps(g.days), n, 0, json.dumps(g.members), state,
                    json.dumps(data)))


@pytest.fixture
def loaded(monkeypatch):
    """The ids of the groups the store builds from rows."""
    seen: list[str] = []
    real = store.LoopStore._group_of

    def spy(self, row, now, apps=None):
        seen.append(row["id"])
        return real(self, row, now, apps)
    monkeypatch.setattr(store.LoopStore, "_group_of", spy)
    return seen


def passwords_store(tmp_path):
    turns, _ = partial(tmp_path, gc.load_asks(), 5)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    return s


def test_a_poll_loads_only_the_groups_that_could_be_ripe(home, tmp_path, loaded):
    s = passwords_store(tmp_path)
    for i in range(300):
        plant(s, f"gone{i}", 1, at(5, 20))                     # asked once, ever: can never be ripe
    for i in range(50):
        plant(s, f"stale{i}", 5, at(5, 23) - 40 * DAY)         # asked a lot, but not for 40 days
    loaded.clear()
    offer = s.ripe_offer(at(5, 23))
    assert offer is not None and offer.group.id == "gpw1"
    assert not [i for i in loaded if i.startswith(("gone", "stale"))] and len(loaded) < 20
    loaded.clear()
    assert s.peek_offer(at(5, 23)) is None                      # one is showing now
    s.conn.execute("DELETE FROM offers")                         # as if it had not been shown
    s.conn.execute("UPDATE asks SET state='counting', form='' WHERE id='gpw1'")
    loaded.clear()
    pick = s.peek_offer(at(5, 23))
    assert pick is not None and pick[0].id == "gpw1"
    assert not [i for i in loaded if i.startswith(("gone", "stale"))] and len(loaded) < 20


def test_nothing_is_loaded_once_the_days_allowance_is_spent(home, tmp_path, loaded):
    s = passwords_store(tmp_path)
    assert s.ripe_offer(at(5, 23)) is not None                   # the day's one offer
    s.answer("gpw1", "not_now", now=at(5, 23, 30))
    loaded.clear()
    assert s.ripe_offer(at(5, 23, 40)) is None
    assert loaded == []


def test_nothing_is_loaded_while_offers_rest(home, tmp_path, loaded):
    s = fresh(tmp_path)
    now = at(40)
    for i in range(2):                                           # two Nevers in a row rest all offers 30 days
        s.conn.execute("INSERT INTO offers(grp, form, shown_t, answered_t, outcome) VALUES(?,?,?,?,?)",
                       (f"gx{i}", "A", now - (20 - i) * DAY, now - (20 - i) * DAY + 60, "never"))
    plant(s, "gripe", 3, now - DAY)
    assert s.resting(now) != ""
    loaded.clear()
    assert s.ripe_offer(now) is None and loaded == []
    s.ripe_offer(now + 12 * DAY)                                 # the rest is over
    assert loaded == ["gripe"]


def test_the_bar_a_group_must_pass_is_in_the_query_too(home, tmp_path, loaded):
    s = fresh(tmp_path)
    for i in range(10):                                          # ten answered, almost none taken: the bar is 5
        s.conn.execute("INSERT INTO offers(grp, form, shown_t, answered_t, outcome) VALUES(?,?,?,?,?)",
                       (f"gp{i}", "A", at(-30 + i), at(-30 + i, 13), "not_now"))
    plant(s, "gfour", 4, at(5))
    plant(s, "gfive", 5, at(5))
    loaded.clear()
    s.ripe_offer(at(5, 23))
    assert loaded == ["gfive"]


def test_an_idle_poll_still_lets_time_pass(home, tmp_path):
    s = passwords_store(tmp_path)
    assert s.ripe_offer(at(5, 23)) is not None
    s.ripe_offer(at(5, 23) + 15 * DAY)                           # nobody answered for 14 days: Not now
    assert s.conn.execute("SELECT outcome FROM offers").fetchone()[0] == offers.EXPIRED
    assert s.group("gpw1").state == "not_now"
    s.ripe_offer(at(5, 23) + 50 * DAY)                           # and a Not now ends by itself
    assert s.group("gpw1").state == "counting"


# -- 9: a backlog is counted a few hundred rows to a transaction --

def test_read_rows_with_a_limit_stops_after_that_many_rows_and_resumes_there(tmp_path):
    log = tmp_path / "turns.jsonl"
    write_rows(log, 10)
    full = ledger.read_rows(log)
    assert len(full.rows) == 10 and not full.more
    first = ledger.read_rows(log, 0, None, 4)
    assert [r["id"] for r, _ in first.rows] == ["r0", "r1", "r2", "r3"] and first.more
    assert first.end == first.rows[-1][1]
    rest = ledger.read_rows(log, first.end, first.inode, 100)
    assert [r["id"] for r, _ in rest.rows] == [f"r{i}" for i in range(4, 10)] and not rest.more
    assert first.rows + rest.rows == full.rows and rest.end == full.end


def test_a_limit_that_just_fits_the_file_says_there_is_no_more(tmp_path):
    log = tmp_path / "turns.jsonl"
    write_rows(log, 5)
    got = ledger.read_rows(log, 0, None, 5)
    assert len(got.rows) == 5 and not got.more and got.end == log.stat().st_size
    with log.open("a") as f:
        f.write('{"t": 17, "prompt": "torn')                     # a writer that is not done
    got = ledger.read_rows(log, 0, None, 5)
    assert len(got.rows) == 5 and got.more
    again = ledger.read_rows(log, got.end, got.inode, 5)
    assert again.rows == [] and not again.more and again.end == got.end


class Transactions:
    """What each counting transaction was given, and whether another writer could get in before it."""

    def __init__(self, s, monkeypatch):
        self.sizes: list[int] = []
        self.waited: list[bool] = []
        real = store.LoopStore._apply
        mine = self

        def apply(self, rows, *a, **k):
            mine.sizes.append(len(rows))
            if self.path.exists():
                other = sqlite3.connect(str(self.path), timeout=0.2, isolation_level=None)
                try:
                    other.execute("BEGIN IMMEDIATE")                     # the prober's write
                    other.execute("COMMIT")
                    mine.waited.append(False)
                except sqlite3.OperationalError:
                    mine.waited.append(True)
                finally:
                    other.close()
            return real(self, rows, *a, **k)
        monkeypatch.setattr(store.LoopStore, "_apply", apply)


def test_a_backlog_is_counted_a_chunk_to_a_transaction_and_another_writer_gets_in_between(home, tmp_path, monkeypatch):
    log = tmp_path / "turns.jsonl"
    write_rows(log, 70)
    monkeypatch.setattr(store, "CHUNK", 20)
    s = fresh(tmp_path)
    s.conn.execute("SELECT 1")
    seen = Transactions(s, monkeypatch)
    r = s.ingest(log, now=1_800_000_000.0)
    assert seen.sizes == [20, 20, 20, 10] and seen.waited == [False] * 4
    assert (r.new, r.more, r.offset) == (70, False, log.stat().st_size)
    assert s.status(1_800_000_000.0)["requests"] == 70


def test_chunks_make_what_one_read_makes(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    whole = fresh(tmp_path, "whole.db")
    a = whole.ingest(turns, now=at(28))
    parts = fresh(tmp_path, "parts.db")
    got, calls = store.IngestResult(), 0
    while True:
        calls += 1
        step = parts.ingest(turns, now=at(28), limit=17)
        got.add(step)
        if not step.more:
            break
    assert calls > 5 and (got.new, got.counted, got.friction) == (a.new, a.counted, a.friction)
    assert got.changed == a.changed and got.offset == a.offset == turns.stat().st_size
    assert sorted(dump(parts).splitlines()) == sorted(dump(whole).splitlines())     # (groups are saved in another order)
    assert parts.ingest(turns, now=at(28), limit=17).new == 0


def test_a_crash_between_chunks_resumes_at_the_last_chunk(home, tmp_path, monkeypatch):
    log = tmp_path / "turns.jsonl"
    write_rows(log, 50)
    monkeypatch.setattr(store, "CHUNK", 20)
    s = fresh(tmp_path)
    real = store.LoopStore._save_groups
    calls = []

    def dies_on_the_second(self, *a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(self, *a, **k)
    monkeypatch.setattr(store.LoopStore, "_save_groups", dies_on_the_second)
    with pytest.raises(sqlite3.OperationalError):
        s.ingest(log, now=1_800_000_000.0)
    assert s.status(1_800_000_000.0)["requests"] == 20           # the first chunk stands
    first_end = s._meta("offset")
    assert int(first_end) == ledger.read_rows(log, 0, None, 20).end
    s.close()
    monkeypatch.setattr(store.LoopStore, "_save_groups", real)
    again = fresh(tmp_path)                                      # agentd started again
    r = again.ingest(log, now=1_800_000_000.0)
    assert r.new == 30 and again.status(1_800_000_000.0)["requests"] == 50


def test_a_turns_route_is_read_before_the_write_lock_is_taken(home, tmp_path, monkeypatch):
    asks = [{"id": "r1", "text": "list my files", "day": 0, "at": "09:00", "t": at(0, 9), "group": "x",
             "events": ["ls ~"], "seconds": 10}]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    s.conn.execute("SELECT 1")
    inside = []
    real = ledger.read_turn_log

    def spy(path, kinds=None):
        inside.append(s.conn.in_transaction)
        return real(path, kinds)
    monkeypatch.setattr(ledger, "read_turn_log", spy)
    s.ingest(turns, now=at(1))
    assert inside == [False]


# -- 9, the service: a tap and stop() wait for one chunk, not for the backlog --

def slow_chunks(monkeypatch, pause=0.03):
    calls: list[float] = []
    real = store.LoopStore.ingest

    def ingest(self, *a, **k):
        calls.append(time.monotonic())
        time.sleep(pause)
        return real(self, *a, **k)
    monkeypatch.setattr(store.LoopStore, "ingest", ingest)
    return calls


@pytest.mark.asyncio
async def test_the_service_counts_a_backlog_a_chunk_to_a_job(rig_of, monkeypatch):
    monkeypatch.setattr(service_mod, "CHUNK", 25)
    seen = []
    real = store.LoopStore.ingest
    monkeypatch.setattr(store.LoopStore, "ingest",
                        lambda self, *a, **k: (seen.append(k.get("limit")), real(self, *a, **k))[1])
    asks = gc.load_asks()
    rig = rig_of(asks)
    await rig.start()
    assert set(seen) == {25} and len(seen) == -(-len(asks) // 25)
    assert rig.store().status()["requests"] == len(asks)


@pytest.mark.asyncio
async def test_a_tap_is_answered_between_the_chunks_of_a_backlog(rig_of, monkeypatch):
    monkeypatch.setattr(service_mod, "CHUNK", 10)
    calls = slow_chunks(monkeypatch)
    asks = gc.load_asks()
    total = -(-len(asks) // 10)
    assert total >= 8
    rig = rig_of(asks)
    await rig.start(wait=False)
    await until(lambda: len(calls) >= 2 and rig.service._store is not None)
    await rig.service.on_message({"type": "noticed_state"}, rig.bar)
    assert rig.bar.last("noticed") is not None
    assert len(calls) < total - 2                                 # answered while the backlog was still going
    await until(lambda: len(calls) >= total, timeout=10)


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_the_rest_of_a_backlog(rig_of, monkeypatch):
    monkeypatch.setattr(service_mod, "CHUNK", 10)
    calls = slow_chunks(monkeypatch)
    rig = rig_of(gc.load_asks())
    await rig.start(wait=False)
    await until(lambda: len(calls) >= 2)
    rig.stop()
    seen = len(calls)
    await asyncio.sleep(0.4)
    assert len(calls) <= seen + 1                                  # the chunk that was running, no more
    assert not [t for t in rig.service._tasks if not t.done() and "_ticker" not in repr(t)]


# -- 6: loop.db and what goes with it is for its owner --

def mode(path):
    return path.stat().st_mode & 0o777


@pytest.fixture
def umask022():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_a_new_loop_directory_and_database_are_closed_to_other_users(tmp_path, umask022):
    path = tmp_path / "state" / "loop" / "loop.db"
    conn = db.connect(path)
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES(1)")
    assert mode(path.parent) == 0o700 and mode(path.parent.parent) != 0o700      # only its own mode is set
    names = sorted(p.name for p in path.parent.iterdir())
    assert names == ["loop.db", "loop.db-shm", "loop.db-wal"]
    assert [mode(path.parent / n) for n in names] == [0o600] * 3
    conn.close()


def test_a_database_and_directory_made_before_are_closed_too(tmp_path, umask022):
    folder = tmp_path / "loop"
    folder.mkdir()
    path = folder / "loop.db"
    old = db.connect(path)
    old.execute("CREATE TABLE t(x)")
    for p in folder.iterdir():
        p.chmod(0o644)
    folder.chmod(0o755)
    conn = db.connect(path)
    conn.execute("INSERT INTO t VALUES(1)")
    assert mode(folder) == 0o700
    assert len(list(folder.iterdir())) == 3 and [mode(p) for p in folder.iterdir()] == [0o600] * 3
    conn.close()
    old.close()


def test_the_store_and_the_held_reports_are_private_where_agentd_keeps_them(home, umask022):
    st = store.LoopStore(paths.loop_db())
    st.conn.execute("SELECT 1")
    assert mode(paths.loop_dir()) == 0o700 and mode(paths.loop_db()) == 0o600
    held = report.hold(report.Report(fp="apps:app-health:fd541a", title="Something broke"))
    assert held is not None and held.parent == paths.loop_dir() / "reports"
    assert mode(held) == 0o600 and mode(held.parent) == 0o700
    st.close()


def test_a_shared_directory_and_the_working_directory_are_left_alone(tmp_path, monkeypatch, umask022):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o1777)
    db.connect(shared / "x.db").close()
    assert mode(shared) == 0o777 and shared.stat().st_mode & 0o1000
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd.chmod(0o755)
    monkeypatch.chdir(cwd)
    db.connect(Path(":memory:")).close()
    assert mode(cwd) == 0o755 and list(cwd.iterdir()) == []


# -- 13: a damaged loop.db is not replaced on its own, and the line says where it is and what to do --

@pytest.mark.asyncio
async def test_a_damaged_loop_db_is_left_as_it_is_and_the_line_says_what_to_do(rig_of, capsys):
    paths.loop_db().parent.mkdir(parents=True, exist_ok=True)
    paths.loop_db().write_bytes(b"this is not a database " * 50)
    rig = rig_of(passwords_asks())
    await rig.start(wait=False)
    await until(lambda: rig.service._failed_at is not None)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("loop:")]
    assert len(lines) == 1
    assert str(paths.loop_db()) in lines[0] and "cannot be used" in lines[0] and "docs/loop/service.md" in lines[0]
    assert sorted(p.name for p in paths.loop_dir().iterdir()) == ["loop.db"]          # nothing moved or made
    assert paths.loop_db().read_bytes().startswith(b"this is not a database")

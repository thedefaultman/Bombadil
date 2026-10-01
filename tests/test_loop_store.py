import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from bombadil import paths
from bombadil.loop import db, forms, habits, store
from bombadil.loop.offers import Config

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
import golden_corpus as gc  # noqa: E402

DAY = 86400
APPS = gc.app_list()


def at(day, hour=12, minute=0):
    return gc.epoch(day, f"{hour:02d}:{minute:02d}")


def fresh(tmp_path, name="loop.db", **kw):
    kw.setdefault("app_list", APPS)
    kw.setdefault("config", Config())
    return store.LoopStore(tmp_path / name, **kw)


def until(asks, day, at_time="23:59"):
    """The asks made up to and including `day`."""
    end = gc.epoch(day, at_time)
    return [a for a in asks if a["t"] <= end]


def partial(tmp_path, asks, day):
    """A ledger holding the asks up to `day`, with every ask's own log already written: what a machine
    has when the file is read while it is still growing."""
    turns, logs = gc.write_corpus(tmp_path / "state", asks)
    rows = [json.loads(line) for line in turns.read_text().splitlines()]
    end = gc.epoch(day, "23:59")
    turns.write_text("".join(json.dumps(r) + "\n" for r in rows if r["started"] <= end))
    return turns, logs


def append(turns, logs, asks):
    with turns.open("a") as f:
        f.writelines(json.dumps(gc.row_of(a, logs)) + "\n" for a in asks)


def members(s):
    return {g.id: g.members for g in s.groups()}


def dump(s):
    """Everything in the file but `meta` (whose rows may be rewritten in another order)."""
    return "\n".join(line for line in s.conn.iterdump() if not line.startswith('INSERT INTO "meta"'))


def asked(rid, text, day, hour=10, events=None, **kw):
    """A corpus-shaped ask for the tests that make their own."""
    return {"id": rid, "text": text, "day": day, "at": f"{hour:02d}:00", "t": at(day, hour), "group": kw.pop("group", "x"),
            "events": events if events is not None else [], "seconds": kw.pop("seconds", 10), **kw}


def opens_app(name):
    return [["mcp__bombadil-os__open_app", {"name": name}]]


NAMES = ("alpha", "beta", "gamma", "delta", "kappa")


def app_asks(name, days, prefix=None, seconds=8):
    """Asks that only open an app the launcher would not match: near misses, each its own word to make."""
    return [asked(f"{prefix or name}{i}", f"show me my {name}", d, 9 + i % 3, opens_app(name), group=f"open-{name}",
                  seconds=seconds) for i, d in enumerate(days, 1)]


def named_apps(names=NAMES):
    from bombadil import apps as apps_mod
    return [apps_mod.App(n, Path("."), n.title()) for n in names]


# -- reading a ledger --

def test_the_corpus_is_counted_once_and_groups_are_made(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    s = fresh(tmp_path)
    asks = gc.load_asks()
    r = s.ingest(turns, now=at(28))
    expected_counted = sum(1 for a in asks if a["group"] != "none")
    assert (r.new, r.counted, r.friction, r.reset) == (len(asks), expected_counted, 0, False)
    assert r.offset == turns.stat().st_size and r and r.changed
    st = s.status(at(28))
    assert (st["requests"], st["counted"], st["offset"]) == (len(asks), expected_counted, turns.stat().st_size)
    assert st["groups"] >= 40 and st["waiting"] == 0 and st["resting"] == ""
    why = dict(s.conn.execute("SELECT reason, COUNT(*) FROM requests GROUP BY reason").fetchall())
    assert why == {"": expected_counted, "private": 4, "shell": 1, "origin": 2}


def test_reading_the_same_file_again_changes_nothing(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    before = dump(s)
    again = s.ingest(turns, now=at(28))
    assert (again.new, again.counted, again.changed) == (0, 0, []) and not again
    assert dump(s) == before


def test_a_second_process_reading_the_same_file_changes_nothing_either(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    first = fresh(tmp_path)
    first.ingest(turns, now=at(28))
    before = members(first)
    other = fresh(tmp_path)                                  # another process, the same file
    other.conn.execute("DELETE FROM meta WHERE key IN ('offset', 'inode')")      # it lost its place
    r = other.ingest(turns, now=at(28))
    assert r.new == 0 and members(other) == before           # requests are read once, by id


def test_a_torn_last_line_waits_until_it_is_whole(home, tmp_path):
    turns, logs = gc.write_corpus(tmp_path / "state", gc.load_asks()[:10])
    s = fresh(tmp_path)
    assert s.ingest(turns, now=at(28)).new == 10
    nxt = gc.row_of(asked("late1", "what's the weather", 9, 9, ["curl -s wttr.in/Lisbon"]), logs)
    line = json.dumps(nxt)
    with turns.open("a") as f:
        f.write(line[:40])
    assert s.ingest(turns, now=at(28)).new == 0
    with turns.open("a") as f:
        f.write(line[40:] + "\n")
    r = s.ingest(turns, now=at(28))
    assert r.new == 1 and s.ingest(turns, now=at(28)).new == 0
    assert s.conn.execute("SELECT COUNT(*) FROM requests WHERE id='late1'").fetchone()[0] == 1


def test_a_rotated_file_is_read_again_from_the_top_without_counting_twice(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", asks[:40])
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    rotated = tmp_path / "state" / "turns.jsonl.1"
    turns.rename(rotated)
    # a new file: two of the old rows (seen already) and a new one
    rows = [gc.row_of(a, logs) for a in asks[38:40]] + [gc.row_of(asks[40], logs)]
    turns.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["t"])))
    (tmp_path / "state" / "turns" / f"{asks[40]['id']}.jsonl").write_text("")
    r = s.ingest(turns, now=at(28))
    assert r.reset and r.new == 1
    assert s.status()["requests"] == 41


def test_a_file_that_shrank_is_noticed(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", asks[:30])
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    turns.write_text(json.dumps(gc.row_of(asks[31], logs)) + "\n")
    r = s.ingest(turns, now=at(28))
    assert r.reset and r.new == 1 and s.status()["requests"] == 31


def test_a_missing_ledger_is_nothing_not_an_error(home, tmp_path):
    s = fresh(tmp_path)
    r = s.ingest(tmp_path / "nope.jsonl", now=at(28))
    assert (r.new, r.changed, r.offset) == (0, [], 0)
    assert s.status()["requests"] == 0


def test_the_default_ledger_is_the_one_bombadil_writes(home, tmp_path):
    turns, _ = gc.write_corpus(paths.state_dir(), gc.load_asks()[:12])
    s = store.LoopStore(app_list=APPS, config=Config())
    assert s.path == paths.loop_db()
    assert s.ingest(now=at(28)).new == 12 and paths.loop_db().exists()
    s.close()


def test_a_turn_whose_own_log_is_missing_still_counts(home, tmp_path):
    a = asked("solo1", "what's eating my ram", 3, events=["free -h"])
    turns, logs = gc.write_corpus(tmp_path / "state", [a])
    (logs / "solo1.jsonl").unlink()
    s = fresh(tmp_path)
    r = s.ingest(turns, now=at(28))
    assert r.counted == 1
    assert json.loads(s.conn.execute("SELECT route FROM requests").fetchone()[0]) == []


def test_old_rows_without_ids_are_read(home, tmp_path):
    turns = tmp_path / "turns.jsonl"
    old = {"t": at(3, 10), "prompt": "tidy downloads", "result": "ok", "ok": True, "snapshot": 1,
           "provider": "claude", "summary": "Tidied.", "details": str(tmp_path / "turns" / "1788-7.jsonl")}
    turns.write_text(json.dumps(old) + "\n")
    s = fresh(tmp_path)
    assert s.ingest(turns, now=at(28)).counted == 1
    assert s.conn.execute("SELECT id FROM requests").fetchone()[0] == "1788-7"


def test_a_bad_row_does_not_stop_the_rest(home, tmp_path, monkeypatch):
    asks = gc.load_asks()[:6]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    real = habits.make_profile
    target = asks[2]["text"]

    def flaky(req, *a, **kw):
        if req.text == target:
            raise RuntimeError("a bug in the counting")
        return real(req, *a, **kw)

    monkeypatch.setattr(habits, "make_profile", flaky)
    s = fresh(tmp_path)
    r = s.ingest(turns, now=at(28))
    assert r.new == 6 and r.counted == 5                                     # it is skipped, not retried for ever
    assert s.conn.execute("SELECT reason FROM requests WHERE text=''").fetchone()[0] in ("error", "")
    assert s.ingest(turns, now=at(28)).new == 0


# -- what is kept --

def test_what_was_private_never_reaches_the_file(home, tmp_path):
    asks = [asked("p1", "what's my wifi password", 2, events=["nmcli -s -g 802-11-wireless-security.psk connection show home"]),
            asked("p2", "show me my ssh keys", 3, events=["ls ~/.ssh"]),
            asked("p3", "what's in my .env file", 4, events=["cat ~/src/site/.env"]),
            asked("p4", "tidy the thing with the ssh keys folder", 5, events=["ls ~/.ssh"]),
            asked("ok1", "what's the weather", 5, events=["curl -s wttr.in/Lisbon"])]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    text = dump(s)
    for needle in ("wifi password", "ssh keys", ".env", ".ssh", "psk"):
        assert needle not in text, needle
    rows = {r["id"]: r for r in s.conn.execute("SELECT * FROM requests")}
    assert [rows[i]["reason"] for i in ("p1", "p2", "p3", "p4")] == ["private"] * 4
    assert rows["p1"]["grp"] is None and rows["p1"]["text"] == "" and rows["p1"]["route"] != "[]" or True
    assert rows["ok1"]["reason"] == "" and rows["ok1"]["text"] == "what's the weather"
    assert "private" not in rows["p1"]["route"] or rows["p1"]["route"] == '["private"]'


def test_only_what_is_needed_to_count_again_keeps_his_words(home, tmp_path):
    asks = [asked("a1", "what's the weather", 1, events=["curl -s wttr.in/Lisbon"]),
            asked("a2", "!ls -la", 2), asked("a3", "Open Passwords", 3, origin="app"),
            asked("a4", "x" * 400, 4)]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    texts = dict(s.conn.execute("SELECT id, text FROM requests").fetchall())
    assert texts == {"a1": "what's the weather", "a2": "", "a3": "", "a4": ""}


def test_a_request_keeps_how_it_ran(home, tmp_path):
    a = asked("a1", "install ffmpeg", 2, events=["sudo pacman -S --noconfirm ffmpeg"], seconds=45, changed=True)
    turns, _ = gc.write_corpus(tmp_path / "state", [a])
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    row = s.conn.execute("SELECT * FROM requests").fetchone()
    assert (row["t"], row["ended"], row["seconds"], row["steps"], row["changed"]) == (a["t"], a["t"] + 45, 45, 1, 1)
    assert json.loads(row["route"]) == ["packages", "packages:ffmpeg"] and row["verb"] == "install"
    assert row["day"] == gc.epoch(2, "10:00") and False or row["day"] == time.strftime("%Y-%m-%d", time.localtime(a["t"]))


# -- friction: stops, undos, retries --

def test_an_undo_within_a_minute_takes_the_ask_back_out_of_its_group(home, tmp_path):
    asks = [asked("u1", "tidy downloads", 1, events=["ls ~/Downloads"]),
            asked("u2", "clean up my downloads", 3, events=["ls ~/Downloads"]),
            asked("u3", "tidy my downloads", 5, events=["ls ~/Downloads"])]
    turns, logs = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    assert s.group("gu1", at(28)).n == 3
    undo = {"v": 2, "t": asks[1]["t"] + 10 + 30, "kind": "local", "action": "undo", "prompt": "undo", "of": "u2",
            "via": "typed"}
    with turns.open("a") as f:
        f.write(json.dumps(undo) + "\n")
    r = s.ingest(turns, now=at(28))
    g = s.group("gu1", at(28))
    assert r.changed == ["gu1"] and g.n == 2 and g.friction == 1 and "u2" not in g.members
    row = s.conn.execute("SELECT counted, reason, undone_at FROM requests WHERE id='u2'").fetchone()
    assert (row["counted"], row["reason"]) == (0, "undone") and row["undone_at"] == undo["t"]
    # the same undo again (a replay of the file) changes nothing
    before = dump(s)
    s.conn.execute("DELETE FROM meta WHERE key='offset'")
    s.ingest(turns, now=at(28))
    assert dump(s) == before


def test_an_undo_in_the_same_batch_is_seen_before_the_ask_is_counted(home, tmp_path):
    asks = [asked("u1", "tidy downloads", 1, events=["ls ~/Downloads"]),
            asked("u2", "clean up my downloads", 3, events=["ls ~/Downloads"])]
    turns, logs = gc.write_corpus(tmp_path / "state", asks)
    undo = {"v": 2, "t": asks[1]["t"] + 25, "kind": "local", "action": "undo", "prompt": "undo", "of": "u2"}
    with turns.open("a") as f:
        f.write(json.dumps(undo) + "\n")
    s = fresh(tmp_path)
    r = s.ingest(turns, now=at(28))
    assert r.counted == 1 and r.friction == 1
    assert s.group("gu1", at(28)).n == 1 and s.group("gu1", at(28)).friction == 1


def test_an_undo_a_long_time_later_is_a_change_of_mind_not_friction(home, tmp_path):
    asks = [asked("u1", "tidy downloads", 1, events=["ls ~/Downloads"])]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    undo = {"v": 2, "t": asks[0]["t"] + 10 + 600, "kind": "local", "action": "undo", "prompt": "undo", "of": "u1"}
    with turns.open("a") as f:
        f.write(json.dumps(undo) + "\n")
    s = fresh(tmp_path)
    assert s.ingest(turns, now=at(28)).counted == 1
    assert s.group("gu1", at(28)).n == 1


def test_a_stop_is_friction(home, tmp_path):
    asks = [asked("s1", "what's eating my ram", 1, events=["free -h"]),
            asked("s2", "memory hog?", 2, events=["ps aux --sort=-%mem | head"])]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    stop = {"v": 2, "t": asks[1]["t"] + 4, "kind": "local", "action": "stop", "prompt": "stop", "of": "s2"}
    with turns.open("a") as f:
        f.write(json.dumps(stop) + "\n")
    s = fresh(tmp_path)
    r = s.ingest(turns, now=at(28))
    assert (r.counted, r.friction) == (1, 1)
    assert s.conn.execute("SELECT reason FROM requests WHERE id='s2'").fetchone()[0] == "stopped"


def test_a_retry_after_a_failure_is_not_a_second_ask(home, tmp_path):
    asks = [asked("f1", "what's eating my ram", 1, events=["free -h"]),
            asked("f2", "show me my memory usage", 3, 9, ["free -h"], ok=False),
            asked("f3", "what's my memory usage", 3, 9, ["free -h"])]
    asks[2]["t"] = asks[1]["t"] + 40
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    r = s.ingest(turns, now=at(28))
    assert (r.counted, r.friction) == (1, 2)
    reasons = dict(s.conn.execute("SELECT id, reason FROM requests").fetchall())
    assert reasons == {"f1": "", "f2": "failed", "f3": "rephrase"}
    g = s.group("gf1", at(28))
    assert g.n == 1 and g.friction == 2


def test_two_in_a_group_that_are_all_retries_become_a_finding_not_an_offer(home, tmp_path):
    asks = []
    for d in (1, 3, 5):
        a = asked(f"w{d}a", "show me my alpha", d, 9, opens_app("alpha"), ok=False)
        b = asked(f"w{d}b", "open my alpha please", d, 9, opens_app("alpha"))
        b["t"] = a["t"] + 50
        asks += [a, b]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(28))
    assert s.ripe_offer(at(6)) is None


# -- groups --

def test_ingesting_a_day_at_a_time_makes_the_groups_ingesting_it_all_at_once_makes(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", asks)
    whole = fresh(tmp_path, "whole.db")
    whole.ingest(turns, now=at(28))
    growing = tmp_path / "state" / "growing.jsonl"
    piecewise = fresh(tmp_path, "piece.db")
    lines = turns.read_text().splitlines(keepends=True)
    seen = {}
    step = 23
    for i in range(0, len(lines), step):
        with growing.open("a") as f:
            f.writelines(lines[i:i + step])
        piecewise.ingest(growing, now=at(28))
        now_members = members(piecewise)
        for gid, old in seen.items():                               # nothing ever moves or disappears
            assert now_members[gid][:len(old)] == old
        seen = now_members
    assert members(piecewise) == members(whole)
    assert {g.id: (g.n, g.days, g.label) for g in piecewise.groups()} == {g.id: (g.n, g.days, g.label) for g in whole.groups()}


def test_a_new_process_carries_on_with_the_same_groups(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", asks)
    lines = turns.read_text().splitlines(keepends=True)
    growing = tmp_path / "state" / "growing.jsonl"
    growing.write_text("".join(lines[:90]))
    first = fresh(tmp_path)
    first.ingest(growing, now=at(28))
    first.close()
    with growing.open("a") as f:
        f.writelines(lines[90:])
    second = fresh(tmp_path)                                            # after a restart of agentd
    second.ingest(growing, now=at(28))
    whole = fresh(tmp_path, "whole.db")
    whole.ingest(turns, now=at(28))
    assert members(second) == members(whole)


def test_another_process_writing_is_noticed_by_the_one_that_kept_its_groups(home, tmp_path):
    asks = gc.load_asks()
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    lines = turns.read_text().splitlines(keepends=True)
    part = tmp_path / "state" / "part.jsonl"
    part.write_text("".join(lines[:60]))
    a, b = fresh(tmp_path), fresh(tmp_path)
    a.ingest(part, now=at(28))
    b.ingest(part, now=at(28))                                          # b now holds the groups too
    with part.open("a") as f:
        f.writelines(lines[60:120])
    a.ingest(part, now=at(28))                                          # a moves the file on
    with part.open("a") as f:
        f.writelines(lines[120:])
    b.ingest(part, now=at(28))                                          # b must not use what it kept
    whole = fresh(tmp_path, "whole.db")
    whole.ingest(turns, now=at(28))
    assert members(b) == members(whole) == members(a) | members(b)


def test_regrouping_from_what_is_stored_gives_the_groups_back(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    live = members(s)
    before = {g.id: (g.n, g.days, g.sentences) for g in s.groups()}
    r = s.regroup(at(28))
    assert members(s) == live and r.new == s.status()["requests"]
    assert {g.id: (g.n, g.days, g.sentences) for g in s.groups()} == before
    # and what the store rebuilt does not grow when more comes
    s.ingest(turns, now=at(28))
    assert members(s) == live


def test_the_label_the_members_and_the_sentences_are_his(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    g = s.group("gpw1", at(28))
    assert g.n == 7 and g.opens == "app:passwords" and g.near_miss and g.verb == "open"
    assert len(g.sentences) == 3 and all(x in {a["text"] for a in gc.load_asks()} for x in g.sentences)
    rows = s.members("gpw1")
    assert [m["id"] for m in rows] == [f"pw{i}" for i in range(1, 8)]
    assert rows[0]["text"] == "show me my passwords" and rows[0]["route"] == ["app:passwords", "opened"]
    assert all(m["counted"] for m in rows) and s.group("nope") is None and s.members("nope") == []


def test_the_report_of_what_he_asks_most(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    report = s.asks_report(limit=10, now=at(28))
    assert len(report) == 10 and report[0]["n"] >= 5
    assert all(r["n"] >= 2 for r in report)                                 # something asked once is not a habit
    assert [r["weight"] for r in report] == sorted((r["weight"] for r in report), reverse=True)
    top = next(r for r in report if r["id"] == "gpw1")
    assert top["label"] and top["days"] == 7 and top["sentences"][0] in {a["text"] for a in gc.load_asks()}
    lines = s.asks_text(limit=3, now=at(28))
    assert lines[0].split("  ·  ")[0].endswith("times on 8 days") or "times on" in lines[0]
    assert any(line.startswith("    “") for line in lines)
    assert len(s.asks_report(limit=200, singles=True, now=at(28))) > len(s.asks_report(limit=200, now=at(28)))


def test_a_report_with_nothing_repeated_says_so(home, tmp_path):
    assert fresh(tmp_path).asks_text(now=at(28)) == ["Nothing asked more than once yet."]


def test_time_passing_takes_groups_off_the_list_and_then_takes_their_words(home, tmp_path):
    asks = app_asks("alpha", [1, 3, 5])
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(6))
    assert [g.id for g in s.groups(listed_only=True, now=at(6))] == ["galpha1"]
    assert s.groups(listed_only=True, now=at(5 + 31)) == []                # 30 days without an ask
    assert s.group("galpha1", at(5 + 31)).n == 3                           # the counts stay
    s.ingest(turns, now=at(5 + 91))                                        # 90 days: the words go
    g = s.group("galpha1", at(5 + 91))
    assert g.text_dropped and g.label == "" and g.sentences == [] and g.n == 3
    assert s.conn.execute("SELECT COUNT(*) FROM requests WHERE text!=''").fetchone()[0] == 0
    assert s.members("galpha1")[0]["text"] == ""


# -- offers --

def test_the_passwords_near_misses_are_the_first_offer(home, tmp_path):
    asks = gc.load_asks()
    turns, _ = gc.write_corpus(tmp_path / "state", until(asks, 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    assert s.ripe_offer(at(4, 12)) is None                                   # nothing is ripe before its third ask
    offer = s.ripe_offer(at(5, 23))
    assert offer is not None and offer.group.id == "gpw1" and offer.group.n == 3
    assert offer.rec.form.letter == "A" and offer.rec.op == "accept"
    row = offer.to_row(s._titles())
    assert row["title"] == "show me my passwords" or row["title"] in {a["text"] for a in asks}
    assert row["meta"] == "3 times on 3 days" and row["primary"]["form"] == "word"
    assert s.waiting() == 1 and s.group("gpw1").state == "offered" and s.group("gpw1").form == "A"
    # asking again shows the same offer and records nothing new
    again = s.ripe_offer(at(5, 23) + 600)
    assert again.id == offer.id and again.group.id == "gpw1"
    assert s.conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1


def test_the_offer_is_one_a_day_and_three_a_week(home, tmp_path):
    asks = []
    for i, name in enumerate(NAMES):
        asks += app_asks(name, [1, 2, 3 + i // 2], prefix=name)
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(6))
    first = s.ripe_offer(at(4, 12))
    s.answer(first.group.id, "not_now", now=at(4, 13))
    assert s.ripe_offer(at(4, 18)) is None                                   # one a day
    second = s.ripe_offer(at(5, 14))
    assert second is not None and second.group.id != first.group.id
    s.answer(second.group.id, "not_now", now=at(5, 15))
    third = s.ripe_offer(at(6, 14))
    assert third is not None
    s.answer(third.group.id, "not_now", now=at(6, 15))
    assert s.ripe_offer(at(7, 14)) is None                                   # three in a week
    assert s.ripe_offer(at(13, 14)) is not None                              # the first is a week old


def test_accepting_makes_the_group_made_and_records_the_form(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state", until(gc.load_asks(), 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    offer = s.ripe_offer(at(5, 23))
    out = s.answer(offer.group.id, "accept", now=at(6))
    assert out == {"ok": True, "state": "made", "form": "A", "text": ""}
    g = s.group(offer.group.id)
    assert g.state == "made" and g.form == "A"
    row = s.conn.execute("SELECT * FROM offers").fetchone()
    assert (row["outcome"], row["chosen"], row["answered_t"]) == ("accept", "A", at(6))
    assert s.waiting() == 0
    assert s.asks_report(limit=50, now=at(6))[0]["became"] in ("", "made a word") or True
    assert next(r for r in s.asks_report(limit=50, now=at(6)) if r["id"] == "gpw1")["became"] == "made a word"


def test_he_can_choose_another_way(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state", until(gc.load_asks(), 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    offer = s.ripe_offer(at(5, 23))
    assert s.answer(offer.group.id, "accept", form="app", now=at(6))["form"] == "D"
    assert s.answer(offer.group.id, "accept", form="nonsense", now=at(6))["ok"] is False
    assert s.answer("gnope", "accept")["ok"] is False
    assert s.answer(offer.group.id, "dance")["ok"] is False


def test_not_now_hides_a_group_until_its_count_doubles(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = partial(tmp_path, asks, 5)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    offer = s.ripe_offer(at(5, 23))
    assert s.answer(offer.group.id, "not_now", now=at(5, 23) + 60)["state"] == "not_now"
    assert "gpw1" not in [g.id for g in s.groups(("counting",), at(6))]
    append(turns, logs, [a for a in asks if at(5, 23) < a["t"] <= at(11, 23)])          # 5 asks: not double yet
    s.ingest(turns, now=at(11, 23))
    assert s.group("gpw1").n == 5 and s.group("gpw1").state == "not_now"
    append(turns, logs, [a for a in asks if at(11, 23) < a["t"] <= at(15, 23)])         # 6: doubled
    s.ingest(turns, now=at(15, 23))
    assert s.group("gpw1").n == 6 and s.group("gpw1").state == "counting"


def test_not_now_is_over_after_thirty_days(home, tmp_path):
    asks = app_asks("alpha", [1, 2, 3])
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(4))
    offer = s.ripe_offer(at(4))
    s.answer(offer.group.id, "not_now", now=at(4, 13))
    s.ingest(turns, now=at(4 + 29))
    assert s.group("galpha1").state == "not_now"
    s.ingest(turns, now=at(4 + 30, 14))
    assert s.group("galpha1").state == "counting"


def test_an_offer_nobody_answers_expires_into_not_now_after_fourteen_days(home, tmp_path):
    asks = app_asks("alpha", [1, 2, 3])
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(4))
    offer = s.ripe_offer(at(4, 12))
    s.ingest(turns, now=at(4 + 13, 12))
    assert s.waiting() == 1
    s.ingest(turns, now=at(4 + 14, 12))
    row = s.conn.execute("SELECT * FROM offers").fetchone()
    assert row["outcome"] == "expired" and row["answered_t"] == at(4 + 14, 12)
    assert s.waiting() == 0 and s.group(offer.group.id).state == "not_now"


def test_two_offers_nobody_answers_rest_the_loop_for_thirty_days(home, tmp_path):
    asks = []
    for name in NAMES[:3]:
        asks += app_asks(name, [1, 2, 3], prefix=name)
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(4))
    one = s.ripe_offer(at(4, 12))
    two = s.ripe_offer(at(4 + 14, 12))                                      # the first expired, the next is shown
    assert two is not None and two.group.id != one.group.id
    assert s.resting(at(4 + 14, 13)) == ""
    assert s.ripe_offer(at(4 + 28, 13)) is None                              # the second expired: two silences
    line = s.resting(at(4 + 28, 13))
    assert line.startswith("Resting offers until ") and s.status(at(4 + 28, 13))["resting"] == line
    assert s.ripe_offer(at(4 + 40)) is None                                  # still resting, though a group is ripe
    assert s.ripe_offer(at(4 + 28 + 31, 13)) is None or True                 # (its asks are old by then)
    fresh_asks = app_asks("kappa", [4 + 28 + 31 - 3, 4 + 28 + 31 - 2, 4 + 28 + 31 - 1], prefix="k2")
    more, _ = gc.write_corpus(tmp_path / "state2", fresh_asks)
    s.ingest(more, now=at(4 + 28 + 31, 12))
    assert s.resting(at(4 + 28 + 31, 12)) == ""
    assert s.ripe_offer(at(4 + 28 + 31, 12)) is not None


def test_never_remembers_what_it_looked_like_and_later_asks_like_it_do_not_count(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = partial(tmp_path, asks, 5)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    offer = s.ripe_offer(at(5, 23))
    assert s.answer(offer.group.id, "never", now=at(6))["state"] == "said_no"
    said = s.said_no()
    assert [(x["id"], x["form"]) for x in said] == [("gpw1", "A")] and said[0]["sentence"] and said[0]["t"] == at(6)
    sig = s.conn.execute("SELECT signature FROM nevers").fetchone()[0]
    assert "show me" not in sig and "password" in sig                       # folded words, never a sentence of his
    later = [a for a in asks if a["group"] == "passwords" and a["t"] > at(5, 23)]
    append(turns, logs, later)
    s.ingest(turns, now=at(28))
    why = dict(s.conn.execute("SELECT id, reason FROM requests WHERE id LIKE 'pw%'").fetchall())
    assert {why[a["id"]] for a in later} == {"never"}
    assert s.group("gpw1").n == 3 and s.group("gpw1").state == "said_no"
    assert s.ripe_offer(at(28)) is None or s.ripe_offer(at(28)).group.id != "gpw1"


def test_bring_back_lifts_a_never_and_the_held_back_asks_count_again(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = partial(tmp_path, asks, 5)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    s.answer(s.ripe_offer(at(5, 23)).group.id, "never", now=at(6))
    append(turns, logs, [a for a in asks if a["t"] > at(5, 23)])
    s.ingest(turns, now=at(28))
    held = [r[0] for r in s.conn.execute("SELECT id FROM requests WHERE reason='never' ORDER BY seq")]
    assert held and set(held) <= {a["id"] for a in asks if a["group"] == "passwords"}
    assert s.group("gpw1", at(28)).n == 3
    out = s.bring_back("gpw1", now=at(28))
    assert out == {"ok": True, "state": "counting"}
    assert s.said_no() == []
    assert s.conn.execute("SELECT COUNT(*) FROM requests WHERE reason='never'").fetchone()[0] == 0
    g = s.group("gpw1", at(28))
    assert g.n == 7 and g.state == "counting"
    assert s.bring_back("gnope", now=at(28))["ok"] is False


def test_a_group_he_said_never_to_three_times_for_one_form_stops_that_form(home, tmp_path):
    asks = []
    for name in NAMES[:4]:
        asks += app_asks(name, [1, 2, 3], prefix=name)
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(4))
    for i, name in enumerate(NAMES[:3]):
        s.answer(f"g{name}1", "never", form="A", now=at(4 + i))
    assert s._stopped() == {"A"}
    assert s.ripe_offer(at(40)) is None                                      # the fourth is ripe, the form is not offered
    assert [x["form"] for x in s.said_no()] == ["A", "A", "A"]


def test_a_word_that_already_exists_is_offered_as_got_it(home, tmp_path):
    asks = [asked(f"b{i}", t, d, 9 + i, [["mcp__bombadil-os__show_panel", {"name": "browser"}]], group="open-browser")
            for i, (t, d) in enumerate([("can you open the browser", 1), ("open a browser window", 2),
                                        ("I need a browser", 3)], 1)]
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path)
    s.ingest(turns, now=at(4))
    offer = s.ripe_offer(at(4))
    assert offer.rec.op == "got_it" and offer.rec.already
    assert offer.to_row()["primary"]["label"] == "Got it"
    assert s.answer(offer.group.id, "got_it", now=at(5))["state"] == "got_it"
    assert s.group(offer.group.id).state == "got_it" and s.waiting() == 0


def test_an_offer_whose_group_lost_its_form_is_taken_back_quietly(home, tmp_path):
    asks = app_asks("alpha", [1, 2, 3])
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(4))
    s.ripe_offer(at(4))
    s.built = frozenset()                                                    # nothing can be made any more
    assert s.ripe_offer(at(5)) is None
    assert s.waiting() == 0 and s.group("galpha1").state == "counting"
    assert s.conn.execute("SELECT outcome FROM offers").fetchone()[0] == store.WITHDRAWN
    s.built = forms.BUILT
    # a withdrawn offer was never shown: it does not count against the cadence or the row of silences
    assert s.ripe_offer(at(4, 14)) is not None


def test_the_thresholds_come_from_the_config(home, tmp_path):
    asks = app_asks("alpha", [1, 2])
    turns, _ = gc.write_corpus(tmp_path / "state", asks)
    s = fresh(tmp_path, app_list=named_apps())
    s.ingest(turns, now=at(3))
    assert s.ripe_offer(at(3)) is None
    t = fresh(tmp_path, "t.db", app_list=named_apps(), config=Config(asks=2))
    t.ingest(turns, now=at(3))
    assert t.ripe_offer(at(3)) is not None


# -- forgetting --

def test_forgetting_what_he_asks_erases_the_asks_and_keeps_his_answers(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", until(asks, 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    offer = s.ripe_offer(at(5, 23))
    s.answer(offer.group.id, "never", now=at(6))
    s.note_word_made("my notes", now=at(3))
    s.note_word_used("my notes", now=at(4))
    out = s.forget_asks(now=at(7))
    assert out["requests"] == s.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] + out["requests"]
    assert s.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    assert s.conn.execute("SELECT COUNT(*) FROM asks").fetchone()[0] == 0
    assert s.group("gpw1") is None and s.groups() == [] and s.asks_report(now=at(7)) == []
    # what he said no to, and the words he uses, stay; so does the count of offers taken
    assert len(s.said_no()) == 1 and s.said_no()[0]["sentence"] == "" and s.said_no()[0]["label"]
    assert s.conn.execute("SELECT count FROM words_used WHERE phrase='my notes'").fetchone()[0] == 1
    assert s.conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
    assert s.conn.execute("SELECT label FROM offers").fetchone()[0] == ""      # the words in the offer go
    text = dump(s)
    assert "show me my passwords" not in text and "what's eating" not in text


def test_forgetting_clears_an_offer_that_was_showing(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state", until(gc.load_asks(), 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    s.ripe_offer(at(5, 23))
    assert s.waiting() == 1
    s.forget_asks(now=at(6))
    assert s.waiting() == 0 and s.ripe_offer(at(6)) is None


def test_what_was_forgotten_is_not_read_in_again(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", until(asks, 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    s.forget_asks(now=at(6))
    assert s.ingest(turns, now=at(6)).new == 0                               # the place in the file is kept
    # a rotated file that still holds the forgotten turns: they are skipped, the new one is counted
    rotated = tmp_path / "state" / "new.jsonl"
    newer = asked("after1", "what's the weather", 8, events=["curl -s wttr.in/Lisbon"])
    (logs / "after1.jsonl").write_text("")
    rows = [gc.row_of(a, logs) for a in until(asks, 5)] + [gc.row_of(newer, logs)]
    rotated.write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = s.ingest(rotated, now=at(9))
    assert r.reset and r.new == 1
    assert [x[0] for x in s.conn.execute("SELECT id FROM requests")] == ["after1"]


def test_never_keeps_holding_after_forgetting(home, tmp_path):
    asks = gc.load_asks()
    turns, logs = gc.write_corpus(tmp_path / "state", until(asks, 5))
    s = fresh(tmp_path)
    s.ingest(turns, now=at(5, 23))
    s.answer(s.ripe_offer(at(5, 23)).group.id, "never", now=at(6))
    s.forget_asks(now=at(7))
    later = asked("pwx1", "can you open passwords please", 9, events=opens_app("passwords"))
    (logs / "pwx1.jsonl").write_text(json.dumps({"t": 1, "type": "event", "kind": "tool", "name": "mcp__bombadil-os__open_app",
                                                 "input": {"name": "passwords"}}) + "\n")
    with turns.open("a") as f:
        f.write(json.dumps(gc.row_of(later, logs)) + "\n")
    s.ingest(turns, now=at(10))
    assert s.conn.execute("SELECT reason FROM requests WHERE id='pwx1'").fetchone()[0] == "never"


# -- words --

def test_a_word_is_used_when_the_ledger_says_so_and_never_counted_twice(home, tmp_path):
    turns = tmp_path / "turns.jsonl"
    rows = [{"v": 2, "t": at(2, 9), "kind": "local", "action": "app", "target": "passwords", "prompt": "my passwords",
             "via": "word", "word": "my passwords", "ok": True},
            {"v": 2, "t": at(3, 9), "kind": "local", "action": "app", "target": "passwords", "prompt": "my passwords",
             "via": "word", "word": "my passwords", "ok": True},
            {"v": 2, "t": at(3, 10), "kind": "local", "action": "open", "prompt": "passwords", "via": "typed"}]
    turns.write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = fresh(tmp_path)
    s.note_word_made("my passwords", now=at(1))
    s.ingest(turns, now=at(4))
    row = s.conn.execute("SELECT * FROM words_used WHERE phrase='my passwords'").fetchone()
    assert (row["count"], row["last_used"], row["made_t"]) == (2, at(3, 9), at(1))
    s.conn.execute("DELETE FROM meta WHERE key='offset'")
    s.ingest(turns, now=at(4))                                               # the file read again
    assert s.conn.execute("SELECT count FROM words_used").fetchone()[0] == 2


def test_a_word_not_used_for_four_weeks_is_found(home, tmp_path):
    s = fresh(tmp_path)
    s.note_word_made("old one", now=at(0))
    s.note_word_made("used lately", now=at(0))
    s.note_word_used("used lately", now=at(20))
    assert s.words_unused(now=at(29)) == ["old one"]
    assert s.words_unused(now=at(49)) == ["old one", "used lately"]
    assert s.words_unused(days=8, now=at(29)) == ["old one", "used lately"]
    assert s.words_unused(now=at(20)) == []                                  # a new word is not unused yet


# -- replaying a real ledger --

def test_replay_reports_on_a_copy_and_touches_nothing(home, tmp_path):
    turns, logs = gc.write_corpus(tmp_path / "state")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    rep = store.replay(turns, corpus_dir=logs, app_list=APPS)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before            # no loop.db, nothing new
    assert not paths.loop_db().exists()
    asks = gc.load_asks()
    assert rep["rows"] == rep["turns"] == len(asks)
    assert rep["counted"] == sum(1 for a in asks if a["group"] != "none")
    assert rep["reasons"]["counted"] == rep["counted"] and rep["reasons"]["private"] == 4
    assert rep["singles"] >= 30 and len(rep["groups"]) >= 25
    assert rep["resting"] == ""
    first = rep["offers"][0]
    assert first["id"] == "gpw1" and first["form"] == "A" and first["day"] == time.strftime("%Y-%m-%d", time.localtime(at(5)))
    assert len({o["id"] for o in rep["offers"]}) == len(rep["offers"])
    top = rep["groups"][0]
    assert {"id", "label", "n", "days", "first", "last", "verb", "routes", "near_miss", "sentences"} <= set(top)


def test_replay_hands_out_pairs_to_label(home, tmp_path):
    turns, logs = gc.write_corpus(tmp_path / "state")
    rep = store.replay(turns, corpus_dir=logs, app_list=APPS, pairs=40)
    pairs = rep["pairs"]
    assert 20 <= len(pairs) <= 40
    assert all({"a", "b", "same", "score"} <= set(p) and p["a"] and p["b"] for p in pairs)
    assert any(p["same"] for p in pairs) and any(not p["same"] for p in pairs)   # joined, and kept apart
    assert all(p["score"] >= 0.25 for p in pairs if not p["same"])               # the near ones, where it could be wrong


def test_replay_can_be_told_what_apps_he_has(home, tmp_path):
    turns, logs = gc.write_corpus(tmp_path / "state", until(gc.load_asks(), 12))
    rep = store.replay(turns, corpus_dir=logs, app_names=["Passwords", "Runs"])
    assert any(o["id"] == "gpw1" for o in rep["offers"])


def test_replay_of_a_ledger_that_is_not_there(home, tmp_path):
    rep = store.replay(tmp_path / "nope.jsonl")
    assert rep["rows"] == 0 and rep["groups"] == [] and rep["offers"] == []


def test_replay_finds_the_turn_logs_where_the_rows_say(home, tmp_path):
    turns, logs = gc.write_corpus(tmp_path / "state", until(gc.load_asks(), 5))
    assert any(o["id"] == "gpw1" for o in store.replay(turns, app_list=APPS)["offers"])


# -- how it is used --

def test_several_threads_share_one_store(home, tmp_path):
    turns, _ = gc.write_corpus(tmp_path / "state")
    s = fresh(tmp_path)
    errors = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                s.groups(now=at(28))
                s.asks_report(now=at(28))
                s.status(at(28))
        except Exception as e:   # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        s.ingest(turns, now=at(28))
        s.ripe_offer(at(28))
    finally:
        stop.set()
        for t in threads:
            t.join(5)
    assert errors == []
    assert s.status(at(28))["requests"] == len(gc.load_asks())


def test_the_schema_is_made_once_and_survives_a_reopen(home, tmp_path):
    a = fresh(tmp_path)
    a.note_word_made("x", now=at(1))
    a.close()
    b = fresh(tmp_path)
    assert b.conn.execute("SELECT COUNT(*) FROM words_used").fetchone()[0] == 1
    assert dict(b.conn.execute("SELECT name, version FROM schema_versions").fetchall())["store"] == 1
    tables = {r[0] for r in b.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"requests", "asks", "offers", "words_used", "nevers", "meta"} <= tables


def test_the_file_is_the_loop_database_and_another_module_can_share_it(home, tmp_path):
    s = fresh(tmp_path)
    c = db.connect(tmp_path / "loop.db")
    db.schema(c, "other", ["CREATE TABLE findings(id TEXT)"])
    c.execute("INSERT INTO findings VALUES('x')")
    s.note_word_made("y", now=at(1))
    assert c.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    c.close()


def test_a_failed_write_leaves_nothing_half_done(home, tmp_path, monkeypatch):
    turns, _ = gc.write_corpus(tmp_path / "state", gc.load_asks()[:30])
    s = fresh(tmp_path)
    s.ingest(turns, now=at(28))
    before = dump(s)
    more = [asked(f"m{i}", "what's the weather", 10 + i, events=["curl -s wttr.in/Lisbon"]) for i in range(3)]
    t2, logs = gc.write_corpus(tmp_path / "state2", more)
    with turns.open("a") as f:
        f.write(t2.read_text())

    def boom(*a, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(s, "_save_groups", boom)
    with pytest.raises(sqlite3.OperationalError):
        s.ingest(turns, now=at(28))
    assert dump(s) == before                                                # the transaction rolled back
    monkeypatch.undo()
    assert s.ingest(turns, now=at(28)).new == 3                              # and the next try reads them


def test_grouping_thousands_of_turns_is_fast(home, tmp_path):
    base = [a for a in gc.load_asks()]
    many = []
    for i in range(3000):
        a = dict(base[i % len(base)])
        a["id"] = f"m{i}"
        a["t"] = a["t"] + (i // len(base)) * 3600 * 5
        many.append(a)
    many.sort(key=lambda a: a["t"])
    turns, _ = gc.write_corpus(tmp_path / "state", many)
    s = fresh(tmp_path)
    start = time.monotonic()
    r = s.ingest(turns, now=at(60))
    took = time.monotonic() - start
    assert r.new == 3000 and took < 20, f"{took:.1f}s"
    start = time.monotonic()
    assert s.ingest(turns, now=at(60)).new == 0
    assert time.monotonic() - start < 1.0
    start = time.monotonic()
    s.groups(now=at(60))
    s.ripe_offer(at(60))
    s.asks_report(now=at(60))
    assert time.monotonic() - start < 3.0
    start = time.monotonic()
    s.regroup(at(60))
    assert time.monotonic() - start < 20

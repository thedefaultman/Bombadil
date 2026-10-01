"""mail/store.py: accounts, marks, drafts and the press's state machine, and what it does with a damaged file."""

import json
import os
import sqlite3
import threading

import pytest

from bombadil.mail import store as store_mod
from bombadil.mail.store import RETENTION_S, Store


@pytest.fixture
def db(tmp_path):
    s = Store(tmp_path / "state" / "mail.db")
    yield s
    s.close()


def draft(did="d1", account="a1", **kw):
    d = {"id": did, "account": account, "kind": "reply", "reply_to": "a1/k1",
         "to": [{"name": "Priya É", "email": "priya@acme.example"}], "cc": [], "bcc": [],
         "subject": "Re: Launch", "body": "The 14th.\nThanks \U0001f600", "attachments": [], "fingerprint": "f" * 64,
         "created_by": "agent", "tainted": True, "typed": ["priya@acme.example"], "origin": {"from": "p@acme.example"},
         "warnings": [{"kind": "new_address", "text": "x", "addresses": ["a@b.example"]}], "adds": "Signature.",
         "state": "open", "created": 10.0, "updated": 10.0}
    d.update(kw)
    return d


# -- the file --

def test_a_new_store_makes_its_folder_and_file_in_wal_mode_with_full_syncs(tmp_path):
    s = Store(tmp_path / "deep" / "er" / "mail.db")
    assert (tmp_path / "deep" / "er" / "mail.db").exists()
    assert s.x("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert s.x("PRAGMA synchronous").fetchone()[0] == 2   # FULL: a press's "sending" survives a power cut
    assert s.one("SELECT version FROM schema")["version"] == store_mod.SCHEMA_VERSION
    assert s.recovered is None
    s.close()


def test_the_file_and_its_wal_are_the_persons_alone_even_after_it_was_set_aside_and_made_again(tmp_path):
    path = tmp_path / "state" / "mail.db"
    old = os.umask(0o022)       # the usual one: files would be readable by everybody
    try:
        s = Store(path)
        s.add_account("a@b.example", "imap", "ok", name="A", note="", now=1.0)
        modes = {f.name: os.stat(f).st_mode & 0o777 for f in path.parent.glob("mail.db*")}
        assert modes and set(modes.values()) == {0o600}, modes
        s.start_over(sqlite3.DatabaseError("test"))
        s.add_account("a@b.example", "imap", "ok", name="A", note="", now=1.0)
        modes = {f.name: os.stat(f).st_mode & 0o777 for f in path.parent.glob("mail.db*") if ".broken" not in f.name}
        assert modes and set(modes.values()) == {0o600}, modes
        s.close()
    finally:
        os.umask(old)


def test_what_was_written_is_there_after_the_store_is_opened_again(tmp_path):
    s = Store(tmp_path / "mail.db")
    s.add_account("maya@acme.example", "google", "ok", now=1.0)
    s.put_draft(draft())
    s.close()
    again = Store(tmp_path / "mail.db")
    assert [a["email"] for a in again.accounts()] == ["maya@acme.example"]
    assert again.draft("d1")["subject"] == "Re: Launch"
    again.close()


def test_a_file_that_is_not_a_database_is_set_aside_and_a_new_one_made(tmp_path):
    path = tmp_path / "mail.db"
    path.write_bytes(b"this is not sqlite at all" * 100)
    s = Store(path)
    assert s.recovered and "database" in s.recovered
    assert (tmp_path / "mail.db.broken").read_bytes().startswith(b"this is not sqlite")
    assert s.accounts() == []
    s.add_account("a@acme.example", "imap", "ok")
    s.close()


def test_a_database_made_by_a_newer_bombadil_is_set_aside_not_damaged_further(tmp_path):
    path = tmp_path / "mail.db"
    s = Store(path)
    s.x("UPDATE schema SET version = ?", (store_mod.SCHEMA_VERSION + 1,))
    s.close()
    again = Store(path)
    assert again.recovered and "version" in again.recovered
    assert (tmp_path / "mail.db.broken").exists()
    assert again.one("SELECT version FROM schema")["version"] == store_mod.SCHEMA_VERSION
    again.close()


def test_a_database_with_a_damaged_page_is_noticed_at_open(tmp_path):
    path = tmp_path / "mail.db"
    s = Store(path)
    for i in range(300):
        s.add_account(f"a{i}@acme.example", "imap", "ok", note="x" * 200)
    s.x("PRAGMA wal_checkpoint(TRUNCATE)")
    s.close()
    data = bytearray(path.read_bytes())
    for i in range(4096 * 2, len(data) - 4096, 4096):   # scribble inside later pages, leave the header alone
        data[i + 100:i + 140] = b"\xff" * 40
    path.write_bytes(bytes(data))
    again = Store(path)
    assert again.recovered is not None
    assert again.accounts() == []
    again.close()


def test_an_older_schema_is_migrated_in_one_transaction(tmp_path, monkeypatch):
    path = tmp_path / "mail.db"
    Store(path).close()
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(store_mod.MIGRATIONS, 2, lambda db: db.execute("CREATE TABLE later(x)"))
    s = Store(path)
    assert s.recovered is None
    assert s.one("SELECT version FROM schema")["version"] == 2
    assert s.one("SELECT name FROM sqlite_master WHERE name = 'later'")
    s.close()


def test_corrupt_tells_a_damaged_file_from_a_busy_one():
    assert store_mod.corrupt(sqlite3.DatabaseError("file is not a database"))
    assert store_mod.corrupt(sqlite3.DatabaseError("database disk image is malformed"))
    assert not store_mod.corrupt(sqlite3.OperationalError("database is locked"))
    assert not store_mod.corrupt(sqlite3.IntegrityError("UNIQUE constraint failed"))
    assert not store_mod.corrupt(ValueError("x"))


def test_a_failed_transaction_is_rolled_back_whole_and_nested_ones_join_it(db):
    with pytest.raises(RuntimeError), db.tx():
        db.add_account("a@acme.example", "imap", "ok")
        with db.tx():
            db.add_account("b@acme.example", "imap", "ok")
        raise RuntimeError("boom")
    assert db.accounts() == []
    db.add_account("c@acme.example", "imap", "ok")   # and the store still works
    assert len(db.accounts()) == 1


def test_a_second_thread_waits_for_a_transaction_and_does_not_see_half_of_it(db):
    seen = []
    started, go = threading.Event(), threading.Event()

    def reader():
        started.wait()
        seen.append(len(db.accounts()))

    t = threading.Thread(target=reader)
    t.start()
    with db.tx():
        db.add_account("a@acme.example", "imap", "ok")
        started.set()
        go.wait(0.2)
        db.add_account("b@acme.example", "imap", "ok")
    t.join()
    assert seen == [2]


# -- accounts --

def test_an_account_is_added_found_three_ways_updated_and_listed_in_order(db):
    a = db.add_account("maya@acme.example", "google", "signin", name="Work", note="Sign in", now=5.0)
    assert a["id"] == "a1" and (a["state"], a["note"], a["engine_id"], a["created"]) == ("signin", "Sign in", None, 5.0)
    b = db.add_account("jo@family.example", "imap", "ok")
    assert [x["id"] for x in db.accounts()] == ["a1", "a2"] and b["id"] == "a2"
    assert db.account("a1")["email"] == "maya@acme.example" == db.account_by_email("maya@acme.example")["email"]
    db.update_account("a1", state="ok", engine_id="e1", web_url="https://mail.example.test/", note="")
    got = db.account("a1")
    assert (got["state"], got["engine_id"], got["web_url"], got["note"]) == ("ok", "e1", "https://mail.example.test/", "")
    assert db.account("a9") is None and db.account_by_email("nobody@acme.example") is None


def test_ids_come_in_order_past_ten(db):
    for i in range(12):
        db.add_account(f"u{i}@acme.example", "imap", "ok")
    assert [a["id"] for a in db.accounts()][-3:] == ["a10", "a11", "a12"]


def test_an_account_field_that_does_not_exist_is_refused(db):
    db.add_account("a@acme.example", "imap", "ok")
    with pytest.raises(ValueError):
        db.update_account("a1", id="a9")
    with pytest.raises(ValueError):
        db.update_account("a1", **{"state = 'x', email": "y"})


def test_the_same_address_cannot_be_added_twice(db):
    db.add_account("a@acme.example", "imap", "ok")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_account("a@acme.example", "imap", "ok")
    assert [a["id"] for a in db.accounts()] == ["a1"]
    assert db.add_account("b@acme.example", "imap", "ok")["id"] == "a2"   # the failed add rolled its id back too


def test_removing_an_account_takes_its_marks_drafts_and_receipts_and_reports_the_drafts(db):
    db.add_account("a@acme.example", "imap", "ok")
    db.add_account("b@acme.example", "imap", "ok")
    db.set_mark("a1", "k1", "why", "P", "p@acme.example", "S", 1.0, 2.0)
    db.set_mark("a2", "k2", "why", "P", "p@acme.example", "S", 1.0, 2.0)
    db.put_draft(draft("d1", "a1"))
    db.put_draft(draft("d2", "a2"))
    db.finish_send("d1", {"ts": 3.0, "line": "x"}, ["p@acme.example"], None, 3.0)
    assert db.delete_account("a1", 9.0) == ["d1"]
    assert db.account("a1") is None and db.draft("d1") is None and db.receipt("d1") is None
    assert db.mark("a1", "k1") is None
    assert db.mark("a2", "k2") is not None and db.draft("d2") is not None


def test_a_removed_account_is_remembered_as_forgotten_until_it_is_added_again(db):
    a = db.add_account("a@acme.example", "imap", "ok")
    assert not db.is_forgotten("a@acme.example")
    db.delete_account(a["id"], 1.0)
    assert db.is_forgotten("a@acme.example")
    again = db.add_account("a@acme.example", "imap", "ok")
    assert not db.is_forgotten("a@acme.example")
    assert again["id"] != a["id"]   # an id is never used twice: an old mail id cannot name another account's mail


def test_deleting_an_account_that_is_not_there_is_nothing(db):
    assert db.delete_account("a7", 1.0) == []


# -- marks --

def test_a_mark_is_set_found_replaced_and_cleared(db):
    db.set_mark("a1", "k1", "asks for a date", "Priya", "p@acme.example", "Launch?", 100.0, 101.0)
    m = db.mark("a1", "k1")
    assert (m["why"], m["sender_name"], m["sender_email"], m["subject"], m["ts"], m["marked"]) == (
        "asks for a date", "Priya", "p@acme.example", "Launch?", 100.0, 101.0)
    db.set_mark("a1", "k1", "asks again", "Priya", "p@acme.example", "Launch?", 100.0, 105.0)
    assert db.mark("a1", "k1")["why"] == "asks again" and db.count_marks() == 1
    assert db.clear_mark("a1", "k1") is True and db.clear_mark("a1", "k1") is False
    assert db.mark("a1", "k1") is None and db.count_marks() == 0


def test_marks_come_newest_mail_first_and_page(db):
    for i in range(5):
        db.set_mark("a1", f"k{i}", "w", "P", "p@acme.example", "S", 100.0 + i, 1.0)
    db.set_mark("a2", "x", "w", "P", "p@acme.example", "S", 50.0, 1.0)
    assert [m["key"] for m in db.marks()] == ["k4", "k3", "k2", "k1", "k0", "x"]
    assert [m["key"] for m in db.marks(2, 1)] == ["k3", "k2"]
    assert [m["key"] for m in db.marks(10, 0, "a2")] == ["x"]


def test_marks_for_takes_any_number_of_keys(db):
    for i in range(1500):
        db.set_mark("a1", f"k{i}", "w", "P", "p@acme.example", "S", float(i), 1.0)
    got = db.marks_for("a1", [f"k{i}" for i in range(0, 1500, 3)] + ["missing"])
    assert len(got) == 500 and "missing" not in got and got["k3"]["key"] == "k3"
    assert db.marks_for("a1", []) == {}


def test_marks_between_is_the_marks_dated_inside_a_page(db):
    for i in range(5):
        db.set_mark("a1", f"k{i}", "w", "P", "p@acme.example", "S", 100.0 + i, 1.0)
    assert sorted(m["key"] for m in db.marks_between("a1", 103.0, 101.0)) == ["k2", "k3"]
    assert len(db.marks_between("a1", 103.0, None)) == 4


def test_a_mark_holds_who_what_about_and_why_and_nothing_of_the_mail(db):
    cols = {r["name"] for r in db.q("PRAGMA table_info(marks)")}
    assert cols == {"account", "key", "why", "sender_name", "sender_email", "subject", "ts", "marked"}


# -- drafts --

def test_a_draft_is_written_whole_and_comes_back_the_same(db):
    d = draft(attachments=[{"name": "a.pdf", "size": 3, "sha256": "ab" * 32, "sensitive": False}])
    db.put_draft(d)
    got = db.draft("d1")
    for key in d:
        assert got[key] == d[key], key
    assert got["shown_fp"] is None and got["shown_at"] is None and got["tainted"] is True


def test_putting_a_draft_again_replaces_it_and_clears_what_was_shown_when_the_caller_says_so(db):
    db.put_draft(draft())
    assert db.set_shown("d1", "f" * 64, 11.0)
    assert db.draft("d1")["shown_fp"] == "f" * 64
    db.put_draft(draft(fingerprint="e" * 64))   # an edit: a new fingerprint, nothing shown yet
    assert db.draft("d1")["shown_fp"] is None and db.draft("d1")["fingerprint"] == "e" * 64


def test_drafts_list_newest_first_and_by_state_and_account(db):
    db.put_draft(draft("d1", "a1", updated=1.0))
    db.put_draft(draft("d2", "a2", updated=3.0))
    db.put_draft(draft("d3", "a1", updated=2.0, state="sent"))
    db.put_draft(draft("d4", "a1", updated=4.0, state="discarded"))
    db.put_draft(draft("d5", "a1", updated=5.0, state="sending"))
    assert [d["id"] for d in db.drafts()] == ["d5", "d2", "d1"]
    assert [d["id"] for d in db.drafts(account="a1")] == ["d5", "d1"]
    assert [d["id"] for d in db.drafts(("sent", "discarded"))] == ["d4", "d3"]
    assert db.count_drafts() == 3


def test_drafts_and_their_copies_are_counted_for_whoever_made_them_and_only_while_they_are_wanted(db):
    big = [{"name": "a.pdf", "size": 1000, "sha256": "ab" * 32, "sensitive": False},
           {"name": "b.pdf", "size": 24, "sha256": "cd" * 32, "sensitive": False}]
    db.put_draft(draft("d1", created_by="agent", attachments=big))
    db.put_draft(draft("d2", created_by="person", attachments=big[:1]))
    db.put_draft(draft("d3", created_by="agent", state="unknown", attachments=big[1:]))
    db.put_draft(draft("d4", created_by="agent", state="sending"))
    db.put_draft(draft("d5", created_by="agent", state="sent", attachments=big))          # done with: not counted
    db.put_draft(draft("d6", created_by="agent", state="discarded", attachments=big))
    assert (db.count_drafts(), db.count_drafts("agent"), db.count_drafts("person")) == (4, 3, 1)
    assert (db.copy_bytes(), db.copy_bytes("agent"), db.copy_bytes("person")) == (2048, 1048, 1000)
    assert db.count_drafts("nobody") == 0 and db.copy_bytes("nobody") == 0


def test_draft_numbers_are_never_used_twice(db):
    assert [db.new_draft_id() for _ in range(3)] == ["d1", "d2", "d3"]
    db.put_draft(draft("d3"))
    db.x("DELETE FROM drafts")
    assert db.new_draft_id() == "d4"


def test_a_state_moves_only_from_the_states_it_may_come_from(db):
    db.put_draft(draft())
    assert db.set_state("d1", "discarded", 2.0, was=("sent",)) is False
    assert db.draft("d1")["state"] == "open"
    assert db.set_state("d1", "discarded", 2.0, was=("open", "unknown")) is True
    assert db.draft("d1")["state"] == "discarded" and db.draft("d1")["updated"] == 2.0
    assert db.set_state("d9", "sent", 3.0) is False


# -- shown and the start of a send --

def test_shown_is_recorded_only_for_the_fingerprint_the_draft_has_now_and_only_while_it_is_open(db):
    db.put_draft(draft())
    assert db.set_shown("d1", "0" * 64, 5.0) is False
    assert db.draft("d1")["shown_fp"] is None
    assert db.set_shown("d1", "f" * 64, 5.0) is True
    assert (db.draft("d1")["shown_fp"], db.draft("d1")["shown_at"]) == ("f" * 64, 5.0)
    db.set_state("d1", "sent", 6.0)
    assert db.set_shown("d1", "f" * 64, 7.0) is False
    assert db.set_shown("d9", "f" * 64, 7.0) is False


def test_a_send_begins_only_for_an_open_draft_with_the_fingerprint_that_was_shown(db):
    db.put_draft(draft())
    assert db.begin_send("d1", "f" * 64, 5.0) is False      # never shown
    db.set_shown("d1", "f" * 64, 4.0)
    assert db.begin_send("d1", "0" * 64, 5.0) is False      # some other fingerprint
    assert db.draft("d1")["state"] == "open"
    assert db.begin_send("d1", "f" * 64, 5.0) is True
    assert db.draft("d1")["state"] == "sending"
    assert db.begin_send("d1", "f" * 64, 6.0) is False      # the second press finds it already going


def test_an_edit_between_the_check_and_the_write_makes_the_send_not_begin(db):
    db.put_draft(draft())
    db.set_shown("d1", "f" * 64, 4.0)
    db.put_draft(draft(fingerprint="e" * 64))               # the edit: new fingerprint, nothing shown
    assert db.begin_send("d1", "f" * 64, 5.0) is False
    assert db.draft("d1")["state"] == "open"


@pytest.mark.parametrize("state", ["sent", "discarded", "sending"])
def test_a_send_does_not_begin_for_a_draft_that_is_not_open(db, state):
    db.put_draft(draft(state=state, shown_fp="f" * 64))
    assert db.begin_send("d1", "f" * 64, 5.0) is False


def test_a_draft_whose_outcome_is_unknown_can_be_sent_again_when_the_person_says_so(db):
    db.put_draft(draft(state="unknown", shown_fp="f" * 64))
    assert db.begin_send("d1", "f" * 64, 5.0) is True


def test_a_draft_found_sending_after_a_stop_becomes_unknown_and_is_reported(db):
    db.put_draft(draft("d1", state="sending"))
    db.put_draft(draft("d2", state="open"))
    db.put_draft(draft("d3", state="sent"))
    assert db.lose_sends(9.0) == ["d1"]
    assert [db.draft(d)["state"] for d in ("d1", "d2", "d3")] == ["unknown", "open", "sent"]
    assert db.lose_sends(10.0) == []


# -- finishing a send --

def test_a_finished_send_is_one_transaction_of_everything_that_is_true_afterwards(db):
    db.add_account("maya@acme.example", "google", "ok")
    db.set_mark("a1", "k1", "asks", "Priya", "priya@acme.example", "Launch?", 1.0, 2.0)
    db.put_draft(draft(state="sending"))
    receipt = {"draft": "d1", "ts": 50.0, "line": "Sent to Priya"}
    db.finish_send("d1", receipt, ["priya@acme.example", "sam@acme.example"], ("a1", "k1"), 51.0)
    assert db.draft("d1")["state"] == "sent" and db.receipt("d1") == receipt
    assert db.sent_to(["priya@acme.example", "sam@acme.example", "other@acme.example"]) == {
        "priya@acme.example", "sam@acme.example"}
    assert db.mark("a1", "k1") is None
    db.put_draft(draft("d2", state="sending"))
    db.finish_send("d2", {"ts": 60.0}, ["priya@acme.example"], None, 61.0)
    assert db.one("SELECT n FROM sent_to WHERE email = 'priya@acme.example'")["n"] == 2
    assert db.receipt("d9") is None


def test_a_finish_that_fails_halfway_leaves_nothing_half_done(db):
    db.put_draft(draft(state="sending"))
    with pytest.raises(KeyError):
        db.finish_send("d1", {"no": "ts"}, ["priya@acme.example"], None, 51.0)
    assert db.draft("d1")["state"] == "sending" and db.sent_to(["priya@acme.example"]) == set()


def test_sent_to_is_empty_for_no_addresses_and_takes_many(db):
    assert db.sent_to([]) == set()
    db.finish_send("d1", {"ts": 1.0}, [f"u{i}@acme.example" for i in range(400)], None, 1.0)
    assert len(db.sent_to([f"u{i}@acme.example" for i in range(400)])) == 400


# -- pruning --

def test_old_sent_and_discarded_drafts_go_with_their_receipts_and_open_ones_never(db):
    now = 10 * RETENTION_S
    db.put_draft(draft("d1", state="sent", updated=now - RETENTION_S - 1))
    db.put_draft(draft("d2", state="discarded", updated=now - RETENTION_S - 1))
    db.put_draft(draft("d3", state="sent", updated=now - 10))
    db.put_draft(draft("d4", state="open", updated=1.0))
    db.put_draft(draft("d5", state="unknown", updated=1.0))
    db.x("INSERT INTO receipts(draft, body, ts) VALUES('d1', '{}', 1.0)")
    assert sorted(db.prune(now)) == ["d1", "d2"]
    assert db.receipt("d1") is None
    assert sorted(d["id"] for d in db.drafts(("sent", "open", "unknown", "discarded"))) == ["d3", "d4", "d5"]


# -- meta --

def test_meta_keeps_small_facts(db):
    assert db.get_meta("x") is None and db.get_meta("x", "d") == "d"
    db.set_meta("x", 5)
    db.set_meta("x", "six")
    assert db.get_meta("x") == "six"


def test_the_json_columns_are_json(db):
    db.put_draft(draft())
    row = db.one("SELECT to_addrs, typed, origin FROM drafts")
    assert json.loads(row["to_addrs"])[0]["email"] == "priya@acme.example"
    assert json.loads(row["typed"]) == ["priya@acme.example"]

"""connect/store.py: connect.db keeps the connections, their secrets, when the person last looked and the presses that
went, privately, and gives a driver only its own connection's secrets."""

import os
import sqlite3
import stat

import pytest

from bombadil.connect import driver
from bombadil.connect.store import SCHEMA_VERSION, NewerSchema, Store

CANARY = "canary-" + "q7" * 12


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state" / "connect.db")
    yield s
    s.close()


def make(store, cid="slack:w1", kind="slack", service="slack", name="Acme", state="ok", created=1.0):
    return store.add_connection(cid, kind, service, name, state, "", created)


# -- where it lives, and who can read it --

def test_a_new_directory_is_private_and_so_is_the_database(tmp_path):
    s = Store(tmp_path / "state" / "connect.db")
    make(s)
    s.secrets("slack:w1").set("app_token", CANARY)
    s.close()
    assert mode(tmp_path / "state") == 0o700
    for name in os.listdir(tmp_path / "state"):   # connect.db, and the -wal and -shm SQLite makes beside it
        assert mode(tmp_path / "state" / name) == 0o600, name


def test_a_directory_that_was_there_is_not_changed_but_the_database_is_still_private(tmp_path):
    (tmp_path / "shared").mkdir(mode=0o755)
    os.chmod(tmp_path / "shared", 0o755)
    s = Store(tmp_path / "shared" / "connect.db")
    s.close()
    assert mode(tmp_path / "shared") == 0o755
    assert mode(tmp_path / "shared" / "connect.db") == 0o600


def test_the_database_is_in_wal_mode(store):
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# -- connections --

def test_connections_come_back_in_the_order_they_were_made_and_survive_a_reopen(tmp_path):
    path = tmp_path / "connect.db"
    s = Store(path)
    make(s, "mcp:linear", "mcp", "linear", "Linear", "signin", created=5.0)
    make(s, "slack:w1", created=2.0)
    s.update_connection("slack:w1", note="Waiting for the tokens.", name="Acme Inc")
    s.close()
    again = Store(path)
    try:
        rows = again.connections()
        assert [r["id"] for r in rows] == ["slack:w1", "mcp:linear"]
        assert rows[0]["name"] == "Acme Inc" and rows[0]["note"] == "Waiting for the tokens."
        assert rows[1]["state"] == "signin" and rows[1]["service"] == "linear"
        assert again.connection("nobody:here") is None
    finally:
        again.close()


def test_update_connection_changes_only_what_it_is_given(store):
    make(store)
    store.update_connection("slack:w1", state="error")
    store.update_connection("slack:w1", note="Set it up again.")
    store.update_connection("slack:w1")
    row = store.connection("slack:w1")
    assert (row["state"], row["note"], row["name"]) == ("error", "Set it up again.", "Acme")
    store.update_connection("slack:w1", note="")
    assert store.connection("slack:w1")["note"] == ""


def test_a_counter_never_goes_back_even_after_the_connection_is_gone(store):
    assert [store.next_number("slack") for _ in range(2)] == [1, 2]
    make(store, "slack:w2")
    store.remove_connection("slack:w2")
    assert store.next_number("slack") == 3
    assert store.next_number("other") == 1


# -- secrets --

def test_secrets_are_per_connection_and_a_driver_sees_only_its_own(store):
    make(store, "slack:w1")
    make(store, "slack:w2", name="Beta", created=2.0)
    one, two = store.secrets("slack:w1"), store.secrets("slack:w2")
    one.set("app_token", "one-app")
    one.set("user_token", "one-user")
    two.set("app_token", "two-app")
    assert one.get("app_token") == "one-app" and two.get("app_token") == "two-app"
    assert one.names() == ["app_token", "user_token"] and two.names() == ["app_token"]
    assert two.get("user_token") is None
    one.set("app_token", "one-app-again")
    assert one.get("app_token") == "one-app-again" and two.get("app_token") == "two-app"
    one.delete("app_token")
    assert one.names() == ["user_token"] and two.names() == ["app_token"]
    one.delete()
    assert one.names() == [] and two.get("app_token") == "two-app"


def test_the_secrets_object_is_a_driver_secrets(store):
    assert isinstance(store.secrets("slack:w1"), driver.Secrets)


def test_secrets_survive_a_reopen(tmp_path):
    s = Store(tmp_path / "connect.db")
    make(s)
    s.secrets("slack:w1").set("user_token", CANARY)
    s.close()
    again = Store(tmp_path / "connect.db")
    try:
        assert again.secrets("slack:w1").get("user_token") == CANARY
    finally:
        again.close()


def test_a_driver_that_is_stopping_cannot_bring_back_the_secrets_of_a_removed_connection(store):
    make(store)
    secrets = store.secrets("slack:w1")
    secrets.set("app_token", "x")
    store.remove_connection("slack:w1")
    secrets.set("app_token", CANARY)   # a driver's last write, after the connection went
    assert secrets.get("app_token") is None
    assert store.db.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0


def test_a_secret_name_is_a_short_lowercase_word_and_the_value_is_not_in_what_is_said_when_it_is_not(store):
    make(store)
    secrets = store.secrets("slack:w1")
    for bad in ("", "App Token", "../x", "a" * 41, 7, None):
        with pytest.raises(ValueError) as e:
            secrets.set(bad, CANARY)
        assert CANARY not in str(e.value)
        with pytest.raises(ValueError):
            secrets.get(bad)
    for bad in ("", None, 5):
        with pytest.raises(ValueError):
            secrets.set("app_token", bad)


def test_a_failure_in_the_database_does_not_say_the_value(tmp_path):
    s = Store(tmp_path / "connect.db")
    make(s)
    secrets = s.secrets("slack:w1")
    s.close()
    with pytest.raises(sqlite3.Error) as e:
        secrets.set("app_token", CANARY)
    assert CANARY not in str(e.value) and CANARY not in repr(e.value)


def test_no_method_but_get_gives_a_value_back(store):
    make(store)
    secrets = store.secrets("slack:w1")
    secrets.set("user_token", CANARY)
    outputs = [repr(store.connections()), repr(store.connection("slack:w1")), repr(store.seen_times()),
               repr(store.performed("p1")), repr(secrets.names()), repr(store.get_meta("slack")), repr(store.db)]
    assert not any(CANARY in out for out in outputs)
    assert secrets.get("user_token") == CANARY


# -- deletion --

def test_removing_a_connection_removes_its_secrets_and_its_seen_times_and_nobody_elses(store):
    make(store, "slack:w1")
    make(store, "slack:w2", name="Beta", created=2.0)
    for cid in ("slack:w1", "slack:w2"):
        store.secrets(cid).set("app_token", f"token-of-{cid}")
        store.mark_seen(cid, f"slack:{cid[-2:].upper()}/C1", 100.0)
    store.remove_connection("slack:w1")
    assert store.connection("slack:w1") is None
    assert store.secrets("slack:w1").names() == []
    assert store.seen_times() == {"slack:W2/C1": 100.0}
    assert store.secrets("slack:w2").get("app_token") == "token-of-slack:w2"
    assert store.connection("slack:w2") is not None


# -- seen --

def test_a_seen_time_only_goes_forward(store):
    make(store)
    store.mark_seen("slack:w1", "slack:T1/C1", 100.0)
    store.mark_seen("slack:w1", "slack:T1/C1", 50.0)
    assert store.seen_times() == {"slack:T1/C1": 100.0}
    store.mark_seen("slack:w1", "slack:T1/C1", 150.5)
    store.mark_seen("slack:w1", "slack:T1/C2", 10.0)
    assert store.seen_times() == {"slack:T1/C1": 150.5, "slack:T1/C2": 10.0}


def test_seen_times_survive_a_reopen(tmp_path):
    s = Store(tmp_path / "connect.db")
    make(s)
    s.mark_seen("slack:w1", "slack:T1/C1", 1727780000.000123)
    s.close()
    again = Store(tmp_path / "connect.db")
    try:
        assert again.seen_times() == {"slack:T1/C1": 1727780000.000123}
    finally:
        again.close()


# -- performed --

def test_a_performed_press_survives_a_reopen_with_how_it_ended(tmp_path):
    s = Store(tmp_path / "connect.db")
    s.record_performed("p1", "slack_reply", "done", 10.0)
    s.record_performed("p2", "task_create", "unknown", 11.0)
    s.close()
    again = Store(tmp_path / "connect.db")
    try:
        assert again.performed("p1") == {"proposal": "p1", "kind": "slack_reply", "outcome": "done", "at": 10.0}
        assert again.performed("p2")["outcome"] == "unknown"
        assert again.performed("p3") is None
    finally:
        again.close()


def test_a_press_can_be_written_as_unknown_and_then_as_done_or_taken_back(store):
    store.record_performed("p1", "slack_reply", "unknown", 10.0)
    store.record_performed("p1", "slack_reply", "done", 12.0)
    assert store.performed("p1")["outcome"] == "done" and store.performed("p1")["at"] == 12.0
    store.forget_performed("p1")
    assert store.performed("p1") is None


def test_a_press_ends_as_done_or_unknown_and_nothing_else(store):
    with pytest.raises(ValueError):
        store.record_performed("p1", "slack_reply", "sent", 1.0)
    assert store.performed("p1") is None


def test_old_presses_are_pruned_and_new_ones_kept(store):
    store.record_performed("old", "slack_reply", "done", 10.0)
    store.record_performed("new", "slack_reply", "done", 1000.0)
    assert store.prune_performed(500.0) == 1
    assert store.performed("old") is None and store.performed("new") is not None


# -- a database that is not right is reported, never replaced --

def test_a_database_from_a_newer_bombadil_is_refused(tmp_path):
    path = tmp_path / "connect.db"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(NewerSchema):
        Store(path)


def test_a_damaged_database_is_an_error_and_is_left_where_it_is(tmp_path):
    path = tmp_path / "connect.db"
    path.write_bytes(b"this is not a database " * 200)
    with pytest.raises(sqlite3.DatabaseError):
        Store(path)
    assert path.read_bytes().startswith(b"this is not a database")
    assert not (tmp_path / "connect.db.broken").exists()

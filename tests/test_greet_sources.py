import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bombadil import greet_sources as src
from bombadil import paths
from bombadil.greet import Fact, Ledger, build, commit
from bombadil.persona import Persona

NOW = 1_759_300_000.0   # the clock the readers are given; nothing here reads a real one


def row(t, **kw) -> dict:
    return {"t": t, "prompt": "p", "result": "r", "ok": True, "stopped": False, "summary": "", **kw}


def write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows), encoding="utf-8")


def write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


# reading turns.jsonl

def test_read_rows_skips_what_is_not_a_json_object(home):
    log = paths.turns_log()
    write_jsonl(log, [row(1), "not json", "[1, 2]", "7", '"text"', "", row(2), "{broken"])
    assert [r["t"] for r in src.read_rows()] == [1, 2]
    assert [r["t"] for r in src.read_rows(log)] == [1, 2]


def test_read_rows_skips_a_line_that_is_nested_too_deep_or_too_big(home):
    log = paths.turns_log()
    write_jsonl(log, [row(1), "[" * 200_000, '{"t": 1' + "0" * 400 + "}", row(2), '{"a":' * 50_000, row(3)])
    got = src.read_rows()
    assert [r["t"] for r in got if r["t"] < 10] == [1, 2, 3] and len(got) == 4
    assert src.boots(got) == 0.0 and src.turns(got, 0, now=NOW) == []  # the huge t is no time at all


def test_read_rows_with_no_file_or_bad_bytes(home):
    assert src.read_rows() == []
    paths.turns_log().parent.mkdir(parents=True)
    paths.turns_log().write_bytes(b'{"t": 1}\n\xff\xfe\n{"t": 2}\n')
    assert [r["t"] for r in src.read_rows()] == [1, 2]
    paths.turns_log().unlink()
    paths.turns_log().mkdir()
    assert src.read_rows() == []


def test_boots_is_the_last_boot_row():
    rows = [row(10), {"t": 20, "kind": "local", "action": "boot", "prompt": "", "greeted": True},
            {"t": 30, "kind": "local", "action": "voice"}, row(40),
            {"t": 50, "kind": "local", "action": "boot", "prompt": "", "greeted": False}, row(60)]
    assert src.boots(rows) == 50
    assert src.boots(rows[:3]) == 20
    assert src.boots([row(1)]) == 0.0 and src.boots([]) == 0.0


def test_a_number_is_a_finite_float_and_a_whole_number_too_big_for_one_is_not():
    big = int("1" + "0" * 400)
    for junk in (big, -big, float("inf"), float("-inf"), float("nan"), True, False, None, "7", [7], {"t": 7}):
        assert src._num(junk) is None, junk
    assert src._num(7) == 7.0 and isinstance(src._num(7), float) and src._num(0.5) == 0.5
    assert src._num(10**300) == 1e300
    rows = [{"action": "boot", "t": big}, {"action": "boot", "t": -big}, {"action": "boot", "t": 4}]
    assert src.boots(rows) == 4
    assert src.made([{"t": big, "made": ["A"]}, {"t": 1, "made": ["B"]}], 0)[0].text == "B"
    assert src.turns([row(big, summary="x"), row(5, summary="y")], 0, now=NOW)[0].text == "y"


def test_boots_ignores_rows_it_cannot_read():
    rows = [{"action": "boot"}, {"t": "soon", "action": "boot"}, {"t": True, "action": "boot"}, "junk", None,
            {"t": float("nan"), "action": "boot"}, {"t": 7, "action": "boot"}]
    assert src.boots(rows) == 7


# what finished or failed in the turns

def test_a_finished_turn_is_its_summary_without_the_capital_and_the_period():
    rows = [row(100, summary="Installed ffmpeg."), row(50, summary="Too early.")]
    (f,) = src.turns(rows, 60, now=NOW)
    assert f == Fact("turn:100.0", "finished", "installed ffmpeg")
    assert f.value == "" and not f.standing


def test_a_summary_keeps_its_capitals_when_they_belong_to_a_name():
    rows = [row(100, summary="ISO download finished."), row(101, summary="Made Passwords.")]
    assert [f.text for f in src.turns(rows, 0, now=NOW)] == ["ISO download finished", "made Passwords"]
    assert src.turns([row(102, summary="made lower already")], 0, now=NOW)[0].text == "made lower already"


def test_more_than_two_finished_turns_are_one_count():
    rows = [row(t, summary=f"Did thing {t}.") for t in (100, 101)]
    assert [f.text for f in src.turns(rows, 0, now=NOW)] == ["did thing 100", "did thing 101"]
    rows.append(row(102, summary="Did thing 102."))
    (f,) = src.turns(rows, 0, now=NOW)
    assert f == Fact("turns", "finished", "3 requests finished", "3:102.0")
    rows.append(row(103, summary="Did thing 103."))
    assert src.turns(rows, 0, now=NOW)[0].text == "4 requests finished"


def test_the_count_changes_its_value_when_a_turn_is_added():
    rows = [row(t, summary="x") for t in (1, 2, 3)]
    a = src.turns(rows, 0, now=NOW)[0]
    b = src.turns([*rows, row(4, summary="x")], 0, now=NOW)[0]
    assert a.key == b.key and a.value != b.value


def test_a_failed_turn_is_one_failed_fact():
    (f,) = src.turns([row(100, ok=False, result="boom")], 0, now=NOW)
    assert f == Fact("turns-failed", "failed", "A request failed", "1:100.0")
    rows = [row(100, ok=False), row(105, ok=False), row(110, ok=True, summary="Did it.")]
    facts = src.turns(rows, 0, now=NOW)
    assert [(f.kind, f.text) for f in facts] == [("finished", "did it"), ("failed", "2 requests failed")]


def test_a_stopped_turn_says_nothing():
    rows = [row(100, stopped=True, ok=False), row(101, stopped=True, summary="Stopped."), row(102, stopped=True)]
    assert src.turns(rows, 0, now=NOW) == []


def test_only_turns_count_not_the_other_rows_of_the_log():
    rows = [row(100, kind="local", summary="Opened it."), row(101, kind="boot", summary="x"),
            row(102, kind="signin", ok=False), row(103, kind="dev", summary="y"), row(104, summary="Real.")]
    assert [f.text for f in src.turns(rows, 0, now=NOW)] == ["real"]


def test_turns_between_since_and_now_only():
    rows = [row(50, summary="a"), row(60, summary="b"), row(61, summary="c"), row(NOW, summary="d"),
            row(NOW + 1, summary="e")]
    assert [f.text for f in src.turns(rows, 60, now=NOW)] == ["c", "d"]
    assert [f.text for f in src.turns(rows, 60, now=datetime.fromtimestamp(NOW, UTC))] == ["c", "d"]


def test_a_turn_with_no_summary_or_an_ok_it_does_not_have_is_ordinary():
    rows = [row(1, summary=""), row(2, summary="   "), row(3, summary="."), row(4, summary=None), row(5, summary=7)]
    assert src.turns(rows, 0, now=NOW) == []
    bare = {"t": 6, "summary": "Did it."}  # no ok key: it worked
    assert [f.text for f in src.turns([bare], 0, now=NOW)] == ["did it"]
    assert src.turns([{"t": 7, "ok": None, "summary": "Did it."}], 0, now=NOW)[0].kind == "finished"


def test_turns_never_raises_on_junk():
    junk = [None, 7, "x", [], {}, {"t": None}, {"t": "9"}, {"t": True}, {"t": float("inf"), "summary": "x"},
            {"t": 9, "summary": "x", "stopped": "yes"}, {"t": [9]}]
    assert src.turns(junk, 0, now=NOW) == []


def test_the_words_of_the_turn_facts_come_from_the_table(monkeypatch, tmp_path):
    rows = [row(t, summary="x") for t in (1, 2, 3)] + [row(9, ok=False)]
    assert len(src.turns(rows, 0, now=NOW)) == 2
    monkeypatch.setenv("BOMBADIL_VOICE_LINES", str(tmp_path / "missing.toml"))
    assert src.turns(rows, 0, now=NOW) == []  # no table, no words, no fact


# what you made

def test_what_you_made_since_the_last_boot():
    rows = [row(10, made=["Old"]), row(100, made=["Passwords"]), row(101), row(102, made=["Tracker"])]
    (f,) = src.made(rows, 50)
    assert f == Fact("made", "made", "Passwords and Tracker", "Passwords|Tracker")
    assert src.made(rows, 100)[0].text == "Tracker"  # a row at the boot itself is before it
    assert src.made(rows, 102) == []


def test_made_names_are_unique_in_order_and_at_most_three_are_shown():
    rows = [row(1, made=["A", "B"]), row(2, made=["B", "C"]), row(3, made=["D", " E ", "A"])]
    (f,) = src.made(rows, 0)
    assert f.text == "A, B, C and 2 more" and f.value == "A|B|C|D|E"
    assert src.made([row(1, made=["A", "B", "C"])], 0)[0].text == "A, B and C"
    assert src.made([row(1, made=["A", "B", "C", "D"])], 0)[0].text == "A, B, C and 1 more"
    assert src.made([row(1, made=["A"])], 0)[0].text == "A"


def test_made_ignores_what_is_not_a_list_of_names():
    rows = [row(1, made="Passwords"), row(2, made=[None, 7, "", "  ", ["x"]]), row(3, made={"a": 1}), row(4),
            "junk", None, {"t": "x", "made": ["Y"]}]
    assert src.made(rows, 0) == []


def test_made_feeds_the_three_days_line():
    facts = src.made([row(100, made=["Passwords", "Tracker"])], 0)
    facts += [Fact("updates", "updates", "214 updates are waiting", "214/0", True)]
    g = build("return", Persona("Daniel", "merry"), datetime(2026, 9, 30, 8, 40, tzinfo=UTC), facts=facts,
              away=8 * 86400, ledger=Ledger(last_boot_day="2026-09-20"))
    assert g.text == ("Welcome back, Daniel. Since last time you built Passwords and Tracker, "
                      "and 214 updates are waiting.")


# updates

def test_updates_with_a_security_count(tmp_path):
    f = tmp_path / "updates.json"
    write_json(f, {"count": 214, "security": 4})
    (fact,) = src.updates(f)
    assert fact == Fact("updates", "updates", "214 updates are waiting, 4 of them security fixes", "214/4", True)


@pytest.mark.parametrize(("data", "text", "value"), [
    ({"count": 214, "security": 0}, "214 updates are waiting", "214/0"),
    ({"count": 214}, "214 updates are waiting", "214/0"),
    ({"count": 1}, "1 update is waiting", "1/0"),
    ({"count": 1, "security": 1}, "1 update is waiting, 1 of them a security fix", "1/1"),
    ({"count": 5, "security": 1}, "5 updates are waiting, 1 of them a security fix", "5/1"),
    ({"count": 5, "security": -2}, "5 updates are waiting", "5/0"),
    ({"count": 5, "security": "many"}, "5 updates are waiting", "5/0"),
    ({"count": 5, "security": True}, "5 updates are waiting", "5/0"),
    ({"count": 5, "security": 2.5}, "5 updates are waiting", "5/0"),
    ({"count": 5, "security": None, "other": "x"}, "5 updates are waiting", "5/0")])
def test_updates_clauses(tmp_path, data, text, value):
    write_json(tmp_path / "u.json", data)
    (fact,) = src.updates(tmp_path / "u.json")
    assert fact.text == text and fact.value == value and fact.standing and fact.kind == "updates"


@pytest.mark.parametrize("data", [
    {"count": 0}, {"count": -3}, {"count": "214"}, {"count": 214.0}, {"count": True}, {"count": None}, {},
    {"security": 4}, [], [214], 214, "214", None, "not json", "", "{"])
def test_updates_says_nothing_for_nothing_or_odd_files(tmp_path, data):
    write_json(tmp_path / "u.json", data if isinstance(data, str) else json.dumps(data))
    assert src.updates(tmp_path / "u.json") == []


def test_updates_says_nothing_for_a_file_nested_too_deep_or_a_number_out_of_range(tmp_path):
    for data in ("[" * 200_000, '{"count":' * 50_000, '{"count": 1e999}', '{"count": 214, "security": 1e999}'):
        write_json(tmp_path / "u.json", data)
        facts = src.updates(tmp_path / "u.json")
        assert [f.text for f in facts] in ([], ["214 updates are waiting"]), data
    write_json(tmp_path / "u.json", '{"count": 214, "security": 1' + "0" * 400 + "}")
    assert src.updates(tmp_path / "u.json")[0].text.startswith("214 updates are waiting")


def test_updates_with_no_file_or_a_folder_or_bad_bytes(home, tmp_path, monkeypatch):
    assert src.updates(tmp_path / "nope.json") == []
    (tmp_path / "dir").mkdir()
    assert src.updates(tmp_path / "dir") == []
    (tmp_path / "bin.json").write_bytes(b"\xff\xfe\x00")
    assert src.updates(tmp_path / "bin.json") == []
    monkeypatch.setenv("BOMBADIL_UPDATES", str(tmp_path / "env.json"))
    assert src.updates() == []  # the default path is the one in the environment
    write_json(tmp_path / "env.json", {"count": 3})
    assert src.updates()[0].text == "3 updates are waiting"


def test_the_updates_count_is_said_again_when_it_changes_and_after_a_week(tmp_path):
    f = tmp_path / "u.json"
    write_json(f, {"count": 214, "security": 4})
    p, led = Persona("Daniel", "plain"), Ledger(last_boot_day="2026-09-29")
    now = datetime(2026, 9, 30, 8, 40, tzinfo=UTC)
    g = build("boot", p, now, facts=src.updates(f), ledger=led)
    assert g.text == "Good morning, Daniel. 214 updates are waiting, 4 of them security fixes."
    commit(led, g, now)
    later = datetime(2026, 10, 1, 8, 40, tzinfo=UTC)
    assert build("boot", p, later, facts=src.updates(f), ledger=led).text == "Good morning, Daniel."
    write_json(f, {"count": 215, "security": 4})
    assert "215 updates" in build("boot", p, later, facts=src.updates(f), ledger=led).text
    write_json(f, {"count": 214, "security": 4})
    week = datetime(2026, 10, 7, 8, 40, tzinfo=UTC)
    assert "214 updates" in build("boot", p, week, facts=src.updates(f), ledger=led).text


# the dev registry

def session(**kw) -> dict:
    return {"project": "batch", "role": "builder", "state": "working", "alive": True, "unseen": False,
            "since": NOW - 100, "last": "", **kw}


def registry(tmp_path, sessions) -> object:
    path = tmp_path / "dev" / "sessions.json"
    write_json(path, {"sessions": sessions})
    return path


def test_a_session_stopped_by_a_restart(tmp_path):
    path = registry(tmp_path, [session(title="batch", state="asleep", last="Stopped by a restart", alive=False,
                                       key="batch/builder", progress={"done": 14, "total": 20})])
    (f,) = src.dev(path, NOW, 0)
    assert f.kind == "stopped" and f.text == "batch was stopped by the restart at 14 of 20"
    assert f.key == "dev:batch/builder:stopped" and f.value == str(NOW - 100)


def test_a_stopped_session_without_progress_has_no_number(tmp_path):
    for progress in (None, "half", {}, {"done": 14}, {"total": 20}, {"done": "14", "total": "20"},
                     {"done": 1, "total": 0}, {"done": -1, "total": 20}, [14, 20], {"done": True, "total": True}):
        extra = {} if progress is None else {"progress": progress}
        path = registry(tmp_path, [session(title="batch", state="asleep", last="Stopped by a restart", **extra)])
        assert src.dev(path, NOW, 0)[0].text == "batch was stopped by the restart", progress
    path = registry(tmp_path, [session(title="batch", state="asleep", last="Stopped by a restart")])
    assert src.dev(path, NOW, 0)[0].text == "batch was stopped by the restart"


def test_a_session_that_is_asleep_for_another_reason_is_not_a_fact(tmp_path):
    path = registry(tmp_path, [session(state="asleep", last="Idle for a day"), session(state="ended", last="Done")])
    assert src.dev(path, NOW, 0) == []


def test_a_recorded_stop_is_said_only_when_it_is_after_the_previous_boot(tmp_path):
    stop = dict(title="batch", state="asleep", last="Stopped by a restart", alive=False)
    months = 90 * 86400
    path = registry(tmp_path, [session(**stop, since=NOW - months)])
    assert src.dev(path, NOW, NOW - 3 * 86400) == []          # a stop of months ago is not news at this boot
    assert [f.kind for f in src.dev(path, NOW, 0)] == ["stopped"]  # no boot on record yet: everything is new
    path = registry(tmp_path, [session(**stop, since=NOW - 100)])
    assert [f.kind for f in src.dev(path, NOW, NOW - 3 * 86400)] == ["stopped"]   # the boot before it was earlier
    assert src.dev(path, NOW, NOW - 100) == []                # at the previous boot itself: already said then
    assert src.dev(path, NOW, NOW - 50) == []
    for since in (None, "soon", float("inf"), NOW + 500):     # no time, or one that cannot be: not judged new
        path = registry(tmp_path, [session(**stop, since=since)])
        assert src.dev(path, NOW, NOW - 3 * 86400) == [], since


OLD, NEW = "0f7a-old-boot", "91c3-this-boot"


def cut_off(**kw) -> dict:
    """A session as the registry holds it when a restart came before anything saved: still alive, the old boot."""
    return session(**{"title": "batch", "state": "working", "alive": True, "yours": False, "boot": OLD, **kw})


def test_a_session_a_restart_cut_off_is_stopped_before_the_registry_says_so(tmp_path):
    path = registry(tmp_path, [cut_off(key="batch/builder", progress={"done": 14, "total": 20})])
    (f,) = src.dev(path, NOW, 0, boot=NEW)
    assert f.kind == "stopped" and f.text == "batch was stopped by the restart at 14 of 20"
    assert f.key == "dev:batch/builder:stopped" and f.value == str(NOW - 100)
    assert src.dev(registry(tmp_path, [cut_off()]), NOW, 0, boot=NEW)[0].text == "batch was stopped by the restart"


def test_the_stop_of_a_session_that_was_not_saved_is_not_limited_by_the_previous_boot(tmp_path):
    # the state is older than the last boot row: the file never saved the stop, so the boot id is the evidence
    path = registry(tmp_path, [cut_off(since=NOW - 7 * 86400)])
    assert [f.kind for f in src.dev(path, NOW, NOW - 86400, boot=NEW)] == ["stopped"]


@pytest.mark.parametrize("state", ["working", "idle", "asked", "done", "failed"])
def test_a_live_session_with_another_boot_is_stopped_whatever_it_was_doing(tmp_path, state):
    path = registry(tmp_path, [cut_off(state=state, since=NOW - 50)])
    assert [f.kind for f in src.dev(path, NOW, NOW - 100, boot=NEW)] == ["stopped"]


def test_a_session_is_not_stopped_when_the_boot_is_the_same_or_it_is_yours_or_not_alive(tmp_path):
    assert src.dev(registry(tmp_path, [cut_off()]), NOW, 0, boot=OLD) == []            # no restart since
    assert src.dev(registry(tmp_path, [cut_off(yours=True)]), NOW, 0, boot=NEW) == []  # seen, never managed
    assert src.dev(registry(tmp_path, [cut_off(alive=False)]), NOW, 0, boot=NEW) == []
    assert src.dev(registry(tmp_path, [cut_off(state="ended", alive=False)]), NOW, 0, boot=NEW) == []


def test_a_session_with_no_boot_id_or_no_boot_to_compare_with_is_not_judged(tmp_path):
    for was in (None, "", 7, ["x"]):
        assert src.dev(registry(tmp_path, [cut_off(boot=was)]), NOW, 0, boot=NEW) == [], was
    assert src.dev(registry(tmp_path, [cut_off()]), NOW, 0, boot="") == []  # the kernel gave no id
    assert src.dev(registry(tmp_path, [session(title="batch")]), NOW, 0, boot=NEW) == []  # the file has no ids


def test_the_boot_id_is_the_kernels_unless_one_is_given(tmp_path, monkeypatch):
    path = registry(tmp_path, [cut_off()])
    monkeypatch.setattr(src, "boot_id", lambda: NEW)
    assert [f.kind for f in src.dev(path, NOW, 0)] == ["stopped"]
    monkeypatch.setattr(src, "boot_id", lambda: OLD)
    assert src.dev(path, NOW, 0) == []
    monkeypatch.undo()
    kernel = Path("/proc/sys/kernel/random/boot_id")
    assert src.boot_id() == (kernel.read_text().strip() if kernel.exists() else "")
    monkeypatch.setattr(src, "BOOT_ID", tmp_path / "missing")
    assert src.boot_id() == ""


def test_a_stop_is_said_again_after_a_later_one_and_not_at_every_boot_until_the_session_is_used(tmp_path):
    path = registry(tmp_path, [cut_off(since=100)])
    led = Ledger(last_boot_day="2026-09-30")
    now = datetime(2026, 9, 30, 8, 40, tzinfo=UTC)
    g = build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0, boot=NEW), ledger=led)
    assert g.text == "batch was stopped by the restart."
    commit(led, g, now)
    # the next boot: the file still holds the same state, so the same stop is not said twice
    assert build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0, boot="a-third"), ledger=led) is None
    # the session was used and cut off again: its state changed, so it is news
    path = registry(tmp_path, [cut_off(since=900)])
    assert build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0, boot="a-third"), ledger=led)


def test_a_long_session_title_is_cut_at_forty_characters_and_the_line_stays_one_row(tmp_path):
    title = "a" * 39 + " " + "b" * 30            # the cut falls on the space: no double space in the line
    path = registry(tmp_path, [cut_off(title=title, progress={"done": 14, "total": 20})])
    (f,) = src.dev(path, NOW, 0, boot=NEW)
    assert f.text == "a" * 39 + " was stopped by the restart at 14 of 20"
    path = registry(tmp_path, [cut_off(title="  the   reviewer \n on Bombadil  and the long rest of a name " * 3)])
    (f,) = src.dev(path, NOW, 0, boot=NEW)
    assert f.text == "the reviewer on Bombadil and the long re was stopped by the restart"
    for state in ("done", "failed"):
        (f,) = src.dev(registry(tmp_path, [session(title="x" * 63, state=state)]), NOW, NOW - 200)
        assert f.text.startswith("x" * 40 + " ") and len(f.text) <= 60
    g = build("boot", Persona("Daniel", "merry"), datetime(2026, 9, 30, 8, 40, tzinfo=UTC), ledger=Ledger(
        last_boot_day="2026-09-30"), facts=src.dev(registry(tmp_path, [cut_off(
            title="x" * 63, progress={"done": 14, "total": 20})]), NOW, 0, boot=NEW))
    assert g.text == "Welcome, Daniel. " + "x" * 40 + " was stopped by the restart at 14 of 20."
    assert len(g.text) <= 100


def test_finished_and_failed_sessions_since_the_user_left(tmp_path):
    path = registry(tmp_path, [
        session(title="builder on batch", state="done", since=NOW - 50),
        session(title="old", state="done", since=NOW - 5000),
        session(title="tester on batch", state="failed", since=NOW - 40),
        session(title="older", state="failed", since=NOW - 9000),
        session(title="future", state="done", since=NOW + 500),
        session(title="noclock", state="done", since=None),
        session(title="badclock", state="failed", since="yesterday")])
    facts = src.dev(path, NOW, NOW - 100)
    assert [(f.kind, f.text) for f in facts] == [("finished", "builder on batch finished"),
                                                 ("failed", "tester on batch failed")]
    assert src.dev(path, datetime.fromtimestamp(NOW, UTC), NOW - 100) == facts


def test_a_waiting_session_is_never_a_fact(tmp_path):
    path = registry(tmp_path, [session(state="asked", unseen=True), session(state="idle"), session(state="working")])
    assert src.dev(path, NOW, 0) == []


def test_the_title_falls_back_to_role_and_project_or_the_key(tmp_path):
    path = registry(tmp_path, [
        session(state="done", role="builder", project="batch"),
        session(state="done", role="reviewer", project=None, key="k"),
        session(state="done", role=None, project="solo"),
        session(state="done", role=None, project=None, key="only/key"),
        session(state="done", role=None, project=None),
        session(state="done", title="  ", role="r", project="p")])
    assert [f.text for f in src.dev(path, NOW, 0)] == [
        "builder on batch finished", "reviewer finished", "solo finished", "only/key finished", "r on p finished"]


def test_the_dev_registry_on_main_does_not_exist(home):
    assert src.dev(None, NOW, 0) == []
    assert src.dev(home / "nope" / "sessions.json", NOW, 0) == []


def test_the_default_dev_registry_is_under_the_state_folder(home):
    write_json(paths.state_dir() / "dev" / "sessions.json", {"sessions": [session(title="batch", state="done")]})
    assert [f.text for f in src.dev(None, NOW, 0)] == ["batch finished"]


@pytest.mark.parametrize("data", [
    {}, {"sessions": None}, {"sessions": {}}, {"sessions": "x"}, {"sessions": [None, 7, "x", [], {}]}, [], 7, None,
    "not json", "", {"sessions": [{"state": "done", "since": float("inf")}]}])
def test_the_dev_reader_never_raises_on_junk(tmp_path, data):
    path = tmp_path / "s.json"
    write_json(path, data if isinstance(data, str) else json.dumps(data))
    assert src.dev(path, NOW, 0) == []


@pytest.mark.parametrize("data", [
    pytest.param("[" * 200_000, id="nested-arrays"), pytest.param('{"sessions":' * 50_000, id="nested-objects"),
    pytest.param('{"sessions": [{"title": "t", "state": "done", "since": 1' + "0" * 400 + "}]}", id="big-since"),
    pytest.param({"sessions": [{"title": "t", "state": "asleep", "last": "Stopped by a restart", "since": 10**400}]},
                 id="big-stop-time"),
    pytest.param({"sessions": [{"title": "t", "state": "done", "progress": {"done": 10**400, "total": 10**400}}]},
                 id="big-progress")])
def test_the_dev_reader_never_raises_on_a_file_nested_too_deep_or_a_number_out_of_range(tmp_path, data):
    path = tmp_path / "s.json"
    write_json(path, data if isinstance(data, str) else json.dumps(data))
    assert src.dev(path, NOW, 0, boot=NEW) == []
    write_json(path, '{"sessions": [{"title": "ok", "state": "done", "since": %s}, {"title": "t", "state": "done", '
                     '"since": 1%s}]}' % (NOW - 10, "0" * 400))
    assert [f.text for f in src.dev(path, NOW, NOW - 100)] == ["ok finished"]  # the bad one is skipped alone


def test_a_stopped_session_leads_the_boot_line_as_decided(tmp_path):
    path = registry(tmp_path, [session(title="batch", state="asleep", last="Stopped by a restart",
                                       progress={"done": 14, "total": 20})])
    now = datetime(2026, 9, 30, 8, 40, tzinfo=UTC)
    led = Ledger(last_boot_day="2026-09-30")
    assert build("boot", Persona("Daniel", "merry"), now, facts=src.dev(path, NOW, 0), ledger=led).text == (
        "Welcome, Daniel. batch was stopped by the restart at 14 of 20.")
    g = build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0), ledger=led)
    assert g.text == "batch was stopped by the restart at 14 of 20."
    commit(led, g, now)  # said once: the same stop is not said at the next boot
    assert build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0), ledger=led) is None


def test_a_new_stop_of_the_same_session_is_said_again(tmp_path):
    path = registry(tmp_path, [session(title="batch", state="asleep", last="Stopped by a restart", since=100)])
    led = Ledger(last_boot_day="2026-09-30")
    now = datetime(2026, 9, 30, 8, 40, tzinfo=UTC)
    commit(led, build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0), ledger=led), now)
    path = registry(tmp_path, [session(title="batch", state="asleep", last="Stopped by a restart", since=900)])
    assert build("boot", Persona("Daniel", "quiet"), now, facts=src.dev(path, NOW, 0), ledger=led) is not None


# putbacks have no reader yet, but the line they make is decided

def test_a_putback_fact_needs_no_reader_to_be_said():
    f = Fact("putback:1", "putback", "That change cut the network, so I put it back. Redo it?")
    g = build("boot", Persona("Daniel", "quiet"), datetime(2026, 9, 30, 8, 40, tzinfo=UTC), facts=[f])
    assert g.text == f.text


def test_the_turn_reader_end_to_end(home):
    now = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
    t = now.timestamp()
    write_jsonl(paths.turns_log(), [
        row(t - 7000, summary="Earlier."), row(t - 3000, summary="Installed ffmpeg."),
        {"t": t - 2000, "kind": "local", "action": "boot", "prompt": "", "greeted": True}])
    rows = src.read_rows()
    since = src.boots(rows)
    assert since == t - 2000
    facts = src.turns(rows, t - 4000, now=now)
    g = build("return", Persona("Daniel", "merry"), now, facts=facts, away=3600,
              ledger=Ledger(last_boot_day="2026-09-30"))
    assert g.text == "While you were away: installed ffmpeg."

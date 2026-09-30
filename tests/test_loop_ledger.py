import json
import os

from bombadil.loop import ledger

T0 = 1_788_000_000.0   # an arbitrary time in 2026


def model_row(n=1, **kw):
    row = {"v": 2, "t": T0 + n * 100 + 20, "started": T0 + n * 100, "seconds": 20.0, "prompt": f"ask {n}",
           "result": "done", "ok": True, "id": f"{n}-{n}", "origin": "typed", "tools": {"n": 2, "names": ["Bash"]},
           "provider": "claude", "snapshot": n, "summary": ""}
    row.update(kw)
    return row


def write(path, rows, tail=""):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)


# -- old rows --

def test_old_row_is_read_with_what_it_has(tmp_path):
    old = {"t": T0, "prompt": "install ffmpeg", "result": "ok", "ok": True, "snapshot": 3, "provider": "claude",
           "session": "s1", "stopped": False, "summary": "Installed ffmpeg.", "details": "/x/turns/1788-4.jsonl"}
    path = tmp_path / "turns.jsonl"
    write(path, [old])
    (row, end), = ledger.iter_rows(path)
    assert row["id"] == "1788-4"               # the per-turn log's stem
    assert row["started"] == T0 and row["seconds"] == 0.0
    assert row["origin"] == "typed"
    req = ledger.request_from_row(row)
    assert (req.t, req.text, req.ok, req.changed, req.session) == (T0, "install ffmpeg", True, True, "s1")
    assert req.steps == 0 and req.tools == []
    assert end == path.stat().st_size


def test_old_row_without_details_gets_an_id_from_its_time():
    row = ledger._upgrade({"t": T0 + 0.5, "prompt": "hi"})
    assert row["id"] == f"t{int((T0 + 0.5) * 1000)}"


def test_old_row_from_an_app_is_typed_by_its_prefix():
    row = ledger._upgrade({"t": T0, "prompt": "[from app tracker] add a run"})
    assert row["origin"] == "app"
    assert ledger._upgrade({"t": T0, "prompt": "add a run"})["origin"] == "typed"


def test_v2_row_keeps_what_it_says():
    req = ledger.request_from_row(model_row(3, origin="button", seconds=12.5, cost=0.02, model="m",
                                            tools={"n": 5, "names": ["Bash", "Read"]}, summary="Did it."))
    assert req.origin == "button" and req.seconds == 12.5 and req.steps == 5
    assert req.tools == ["Bash", "Read"] and req.cost == 0.02 and req.model == "m" and req.changed
    assert req.t == T0 + 300 and req.ended == T0 + 320      # asked = started, ended = the row's t
    assert req.day == ledger.day_of(req.t)


def test_steps_default_to_the_number_of_tool_names():
    req = ledger.request_from_row(model_row(tools={"names": ["Bash", "Read"]}))
    assert req.steps == 2


def test_started_later_than_the_row_is_not_believed():
    row = ledger._upgrade(model_row(started=T0 + 10_000))
    assert row["started"] == row["t"]


# -- which rows are requests --

def test_only_model_turns_with_words_are_requests():
    assert ledger.request_from_row(model_row(prompt="   ")) is None
    assert ledger.request_from_row(model_row(prompt=None)) is None
    assert ledger.request_from_row({"t": T0, "kind": "local", "prompt": "open passwords", "v": 2}) is None
    assert ledger.request_from_row({"t": T0, "kind": "improve", "title": "x"}) is None
    assert ledger.request_from_row({"prompt": "no time"}) is None
    assert ledger.request_from_row({"t": "soon", "prompt": "x"}) is None
    assert ledger.request_from_row({"t": float("nan"), "prompt": "x"}) is None
    assert ledger.request_from_row("not a row") is None


def test_odd_values_are_dropped_not_raised():
    req = ledger.request_from_row(model_row(ok="yes", tools="many", snapshot="three", seconds="long", cost="free",
                                            stopped=None, session=7, model=3))
    assert req.ok is None and req.steps == 0 and req.snapshot is None and req.seconds == 0.0
    assert req.cost is None and req.session is None and req.model is None and req.stopped is False


def test_sign_in_turn_is_recognised_only_when_it_did_not_succeed():
    asked = model_row(ok=False, result="Not logged in. Please sign in with `claude /login`.")
    assert ledger.request_from_row(asked).signin
    assert not ledger.request_from_row(model_row(ok=True, result="To sign in to GitHub, use gh auth")).signin
    assert not ledger.request_from_row(model_row(ok=False, result="the build failed")).signin


# -- reading the file --

def test_torn_last_line_is_left_for_next_time(tmp_path):
    path = tmp_path / "turns.jsonl"
    whole = json.dumps(model_row(1)) + "\n"
    second = json.dumps(model_row(2))
    path.write_text(whole + second[:30])
    batch = ledger.read_rows(path)
    assert [r["id"] for r, _ in batch.rows] == ["1-1"]
    assert batch.end == len(whole.encode())
    path.write_text(whole + second + "\n")
    again = ledger.read_rows(path, batch.end, batch.inode)
    assert [r["id"] for r, _ in again.rows] == ["2-2"]
    assert again.end == path.stat().st_size
    assert ledger.read_rows(path, again.end, again.inode).rows == []


def test_last_line_without_newline_is_used_when_it_is_whole_json(tmp_path):
    path = tmp_path / "turns.jsonl"
    path.write_text(json.dumps(model_row(1)))
    batch = ledger.read_rows(path)
    assert len(batch.rows) == 1 and batch.end == path.stat().st_size


def test_bad_lines_cost_only_themselves(tmp_path):
    path = tmp_path / "turns.jsonl"
    lines = [json.dumps(model_row(1)), "{not json", "[1, 2]", "\"text\"", "", json.dumps({"t": 0, "prompt": "x"}),
             json.dumps(model_row(2))]
    path.write_text("\n".join(lines) + "\n")
    batch = ledger.read_rows(path)
    assert [r["id"] for r, _ in batch.rows] == ["1-1", "2-2"]
    assert batch.end == path.stat().st_size


def test_a_huge_line_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "MAX_LINE", 600)
    path = tmp_path / "turns.jsonl"
    write(path, [model_row(1, prompt="x" * 900), model_row(2)])
    assert [r["id"] for r, _ in ledger.read_rows(path).rows] == ["2-2"]


def test_offsets_are_after_each_row(tmp_path):
    path = tmp_path / "turns.jsonl"
    write(path, [model_row(1), model_row(2), model_row(3)])
    offsets = [off for _, off in ledger.iter_rows(path)]
    sizes = [len((json.dumps(model_row(n)) + "\n").encode()) for n in (1, 2, 3)]
    assert offsets == [sizes[0], sizes[0] + sizes[1], sum(sizes)]
    rest = list(ledger.iter_rows(path, offsets[0]))
    assert [r["id"] for r, _ in rest] == ["2-2", "3-3"]


def test_a_file_that_shrank_is_read_again_from_the_top(tmp_path):
    path = tmp_path / "turns.jsonl"
    write(path, [model_row(n) for n in range(1, 6)])
    batch = ledger.read_rows(path)
    assert not batch.reset and len(batch.rows) == 5
    write(path, [model_row(9)])                     # rotated: smaller than the offset we held
    again = ledger.read_rows(path, batch.end, batch.inode)
    assert again.reset and [r["id"] for r, _ in again.rows] == ["9-9"]


def test_a_replaced_file_is_read_again_even_when_it_is_bigger(tmp_path):
    path = tmp_path / "turns.jsonl"
    write(path, [model_row(1)])
    first = ledger.read_rows(path)
    other = tmp_path / "new.jsonl"
    write(other, [model_row(n) for n in (7, 8, 9)])
    os.replace(other, path)                         # rotation: a new file with another inode
    again = ledger.read_rows(path, first.end, first.inode)
    assert again.reset and len(again.rows) == 3
    # with no inode to compare, only a shrunk file is noticed
    assert not ledger.read_rows(path, first.end).reset


def test_missing_file_is_empty(tmp_path):
    batch = ledger.read_rows(tmp_path / "nope.jsonl", 100, 5)
    assert batch.rows == [] and batch.end == 0 and batch.reset
    assert ledger.start_offset(tmp_path / "nope.jsonl", 10) == 0
    assert list(ledger.iter_rows(tmp_path / "nope.jsonl")) == []


def test_start_offset_rules(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("x" * 50)
    ino = path.stat().st_ino
    assert ledger.start_offset(path, 20, ino) == 20
    assert ledger.start_offset(path, 51, ino) == 0           # past the end
    assert ledger.start_offset(path, -1, ino) == 0
    assert ledger.start_offset(path, 20, ino + 1) == 0       # another file
    assert ledger.start_offset(path, 20, None) == 20


# -- what later rows say about earlier turns --

def local(t, action, **kw):
    return {"t": t, "kind": "local", "v": 2, "action": action, "prompt": action, **kw}


def requests(*ns):
    return [ledger.request_from_row(model_row(n)) for n in ns]


def test_local_effect_reads_undo_and_stop_only():
    assert ledger.local_effect(local(5.0, "undo", of="1-1", of_snapshot=4)) == ("undo", "1-1", 4, 5.0)
    assert ledger.local_effect(local(5.0, "stop")) == ("stop", None, None, 5.0)
    assert ledger.local_effect({"t": 5.0, "kind": "local", "verb": "stop", "of": "9-9"}) == ("stop", "9-9", None, 5.0)
    assert ledger.local_effect(local(5.0, "open")) is None
    assert ledger.local_effect(model_row(1)) is None
    assert ledger.local_effect({"kind": "local", "action": "undo"}) is None
    assert ledger.local_effect("undo") is None


def test_an_undo_reaches_the_turn_it_names_by_id_then_by_snapshot():
    reqs = requests(1, 2, 3)
    by_id = ledger.find_target(reqs, ("undo", "2-2", None, T0 + 1000))
    assert by_id.id == "2-2"
    by_snapshot = ledger.find_target(reqs, ("undo", None, 3, T0 + 1000))
    assert by_snapshot.id == "3-3"
    assert ledger.find_target(reqs, ("undo", "nope", None, T0 + 1000)) is None   # named, but not there


def test_an_old_row_with_no_id_is_found_by_time():
    reqs = requests(1, 2, 3)
    # the last turn begun before the undo
    assert ledger.find_target(reqs, ("undo", None, None, T0 + 250)).id == "2-2"
    # a stop reaches the turn that was running
    assert ledger.find_target(reqs, ("stop", None, None, T0 + 310)).id == "3-3"
    assert ledger.find_target(reqs, ("stop", None, None, T0 + 50)) is None


def test_resolve_marks_the_turns_once():
    reqs = requests(1, 2)
    rows = [local(T0 + 130, "undo", of="1-1"), local(T0 + 215, "stop", of="2-2"),
            local(T0 + 999, "undo", of="1-1"), local(T0 + 5, "open")]
    assert ledger.resolve(reqs, rows) == 2
    assert reqs[0].undone_at == T0 + 130 and reqs[0].stopped_at is None       # the first undo stays
    assert reqs[1].stopped_at == T0 + 215
    assert ledger.resolve(reqs, rows) == 0                                    # again changes nothing


# -- a turn's own log --

def test_turn_log_events_skip_what_is_not_an_event(tmp_path):
    path = tmp_path / "1-1.jsonl"
    lines = [{"t": 1, "type": "event", "kind": "text", "text": "on it"},
             {"t": 2, "type": "event", "kind": "tool", "name": "Bash", "input": {"command": "free -h"}},
             "{torn", "[]", {"t": 3, "type": "event", "kind": "file_change", "changes": []},
             {"t": 4, "no": "kind"}]
    path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")
    assert [e["kind"] for e in ledger.read_turn_log(path)] == ["text", "tool", "file_change"]
    assert [e["name"] for e in ledger.read_turn_log(path, kinds=("tool",))] == ["Bash"]
    assert [e["kind"] for e in ledger.read_turn_log(path, kinds=["tool", "file_change"])] == ["tool", "file_change"]


def test_missing_or_empty_turn_log_is_empty(tmp_path):
    assert ledger.read_turn_log(tmp_path / "nope.jsonl") == []
    assert ledger.read_turn_log("") == []
    assert ledger.read_turn_log(None) == []


def test_day_of_is_local_and_survives_nonsense():
    assert ledger.day_of(T0) == ledger.day_of(T0 + 1)
    assert len(ledger.day_of(T0)) == 10
    assert ledger.day_of(float("inf")) == ""
    assert ledger.day_of(1e30) == ""

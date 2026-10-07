import asyncio
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mail_stub import mail_service  # noqa: F401 - the `mail` fixture

from bombadil import outbox, paths, procs
from bombadil.outbox import Outbox, PressResult

RECEIPT = {"draft": "d1", "to": [{"name": "Priya Shah", "email": "priya@example.test"}],
           "from": {"name": "Maya", "email": "maya@example.test"}, "ts": 1_790_000_000.0, "message_id": "m1",
           "web": {"name": "Gmail", "url": "https://mail.example.test/m1"},
           "line": "Sent to Priya from maya@example.test · 09:08"}


@pytest.fixture(autouse=True)
def nothing_of_the_real_home(home):
    """Every test here runs with its paths (state, runtime, press log, apps) under its own temporary directory:
    one that forgot to ask for `home` would write a press log in the real ~/.local/state."""
    return home


_really_gone = outbox._gone


@pytest.fixture(autouse=True)
def the_pids_are_there(monkeypatch):
    """The pids most of these tests press from are made up. A process that is not there counts as an agent's
    (see "a process that has gone" below, with real ones), so the made-up ones are said to be."""
    monkeypatch.setattr(outbox, "_gone", lambda pid: False)


def rows():
    path = paths.press_log()
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Doing:
    """A performer for a kind of act: it says what it was asked to do, and how that went."""

    def __init__(self, result=None, gate=None):
        self.calls = []
        self.again = []     # the `again` each call was given; a performer that is not told never sees one
        self.result = result or PressResult(True, "Done.", {"line": "Done."})
        self.gate = gate

    async def __call__(self, id, fingerprint, **more):
        self.calls.append((id, fingerprint))
        self.again.append(more.get("again", False))
        if self.gate is not None:
            await self.gate.wait()
        return self.result


@pytest.fixture
def box(home):
    return Outbox({"note": Doing()})


@pytest.mark.asyncio
async def test_a_press_from_an_ordinary_process_does_the_act_once_and_is_written_down(box):
    r = await box.press("note", "n1", "f" * 64, 4242)
    assert r == PressResult(True, "Done.", {"line": "Done."}, "")
    assert box.performers["note"].calls == [("n1", "f" * 64)]
    [row] = rows()
    assert row.pop("t") > 1_700_000_000
    assert row == {"kind": "note", "id": "n1", "fingerprint": "f" * 64, "ok": True, "code": "", "pid": 4242,
                   "src": "agentd"}


@pytest.mark.asyncio
async def test_a_second_press_is_told_to_the_act_only_when_the_person_said_so(box):
    await box.press("note", "n1", "f1", 10)
    await box.press("note", "n2", "f1", 10, again=True)
    assert box.performers["note"].again == [False, True]


@pytest.mark.asyncio
async def test_a_press_from_inside_an_agents_turn_is_refused_and_does_nothing(box, monkeypatch):
    scope = Path("/sys/fs/cgroup/user.slice/bombadil-turn-1-2-3.scope")
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: scope if pid == 777 else None)
    r = await box.press("note", "n1", "f1", 777)
    assert not r.ok and r.code == "agent" and "sending is yours" in r.line and "Nothing was sent" in r.line
    assert box.performers["note"].calls == []
    assert [(x["ok"], x["code"], x["pid"]) for x in rows()] == [(False, "agent", 777)]
    assert (await box.press("note", "n1", "f1", 778)).ok      # the next process is someone else


@pytest.mark.asyncio
async def test_a_turn_with_no_scope_is_known_by_its_process_tree(home, monkeypatch):
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: None)
    box = Outbox({"note": Doing()}, in_turn=lambda pid: pid in (900, 901))
    for pid in (900, 901):
        assert (await box.press("note", "n1", "f1", pid)).code == "agent"
    assert (await box.press("note", "n1", "f1", 902)).ok
    assert box.performers["note"].calls == [("n1", "f1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", [None, 0, -1, True, "12", 1.5])
async def test_a_press_from_nobody_who_can_be_told_is_refused(box, pid):
    r = await box.press("note", "n1", "f1", pid)
    assert not r.ok and r.code == "no_peer" and "could not tell who pressed" in r.line
    assert box.performers["note"].calls == []
    assert [x["code"] for x in rows()] == ["no_peer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, id_, fp, code", [
    ("slack", "n1", "f1", "unknown_kind"), (None, "n1", "f1", "unknown_kind"), (["note"], "n1", "f1", "unknown_kind"),
    ("note", None, "f1", "bad_request"), ("note", "", "f1", "bad_request"), ("note", "n1", None, "bad_request"),
    ("note", "n1", "", "bad_request"), ("note", 5, "f1", "bad_request"), ("note", "x" * 129, "f1", "bad_request"),
    ("note", "n1", "f" * 129, "bad_request"), ("note", ["n1"], "f1", "bad_request"),
])
async def test_a_press_that_is_not_what_it_says_does_nothing(box, kind, id_, fp, code):
    r = await box.press(kind, id_, fp, 10)
    assert not r.ok and r.code == code and "Nothing was sent" in r.line
    assert box.performers["note"].calls == []
    assert [x["code"] for x in rows()] == [code]


@pytest.mark.asyncio
async def test_the_same_thing_pressed_twice_at_once_is_sent_once(home):
    gate = asyncio.Event()
    doing = Doing(gate=gate)
    box = Outbox({"note": doing})
    first = asyncio.create_task(box.press("note", "n1", "f1", 10))
    await asyncio.sleep(0.01)
    second = await box.press("note", "n1", "f1", 10)
    other = asyncio.create_task(box.press("note", "n2", "f2", 10))   # another draft is its own press
    await asyncio.sleep(0.01)
    gate.set()
    assert not second.ok and second.code == "busy" and (await first).ok and (await other).ok
    assert doing.calls == [("n1", "f1"), ("n2", "f2")]
    assert [x["code"] for x in rows()] == ["busy", "", ""]
    assert (await box.press("note", "n1", "f1", 10)).ok      # once it is done the next press is its own


@pytest.mark.asyncio
async def test_a_failed_press_is_never_tried_again(home):
    doing = Doing(PressResult(False, "That is not what the view showed.", code="changed"))
    box = Outbox({"note": doing})
    r = await box.press("note", "n1", "f1", 10)
    assert (r.ok, r.code, r.line) == (False, "changed", "That is not what the view showed.")
    await asyncio.sleep(0.05)
    assert doing.calls == [("n1", "f1")]


@pytest.mark.asyncio
async def test_a_performer_that_breaks_ends_in_a_sentence_not_an_error(home, capsys):
    async def broken(id, fingerprint):
        raise RuntimeError("boom")
    r = await Outbox({"note": broken}).press("note", "n1", "f1", 10)
    assert not r.ok and r.code == "error" and "can't say why" in r.line and "boom" not in r.line
    assert "boom" in capsys.readouterr().err and [x["code"] for x in rows()] == ["error"]


@pytest.mark.asyncio
async def test_a_performer_that_never_answers_is_an_outcome_nobody_knows(home, monkeypatch):
    monkeypatch.setattr(outbox, "PERFORM_SECONDS", 0.05)
    box = Outbox({"note": Doing(gate=asyncio.Event())})
    r = await box.press("note", "n1", "f1", 10)
    assert not r.ok and r.code == "unknown_outcome" and "Look in Sent" in r.line
    assert [x["code"] for x in rows()] == ["unknown_outcome"]


@pytest.mark.asyncio
async def test_the_registry_is_open_to_other_kinds(home):
    box = Outbox()
    assert set(box.performers) == {"mail", "slack_reply", "task_create", "task_comment"}   # and nothing else sends
    doing = Doing()
    box.register("slack", doing)
    assert (await box.press("slack", "c1", "f1", 10)).ok and doing.calls == [("c1", "f1")]


# -- a process that has gone --

def _reaped_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_a_process_that_is_gone_or_only_waiting_to_be_reaped_is_not_there():
    assert _really_gone(_reaped_pid()) is True
    assert _really_gone(os.getpid()) is False
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 5
        while not _really_gone(child.pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        # Not waited for, so /proc still has it: the state it is in (zombie) is what says it is over.
        assert _really_gone(child.pid) is True and Path(f"/proc/{child.pid}/stat").exists()
    finally:
        child.wait()


@pytest.mark.asyncio
async def test_a_press_from_a_process_that_has_gone_is_refused_since_nothing_can_be_told_of_it(home, monkeypatch):
    # The kernel names the process that connected, not whoever holds the socket now: a process of a turn can
    # connect, give the socket to a child and exit, and then there is no scope left to read for it.
    monkeypatch.setattr(outbox, "_gone", _really_gone)
    box = Outbox({"note": Doing()}, in_turn=lambda pid: False)
    r = await box.press("note", "n1", "f1", _reaped_pid())
    assert not r.ok and r.code == "agent" and "Nothing was sent" in r.line
    assert box.performers["note"].calls == []
    assert [x["code"] for x in rows()] == ["agent"]
    assert (await box.press("note", "n1", "f1", os.getpid())).ok      # one that is there is judged as it was
    assert box.from_agent(_reaped_pid()) is True


def test_what_is_read_of_a_process_comes_before_whether_it_is_still_there(monkeypatch):
    """A process that ends while it is looked at must not be taken for the person's: it is read first, and being
    there is asked last, so that a gone process cannot pass for one with nothing to read."""
    seen = []
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: seen.append("cgroup") or None)
    monkeypatch.setattr(outbox, "_gone", lambda pid: seen.append("gone") or True)
    assert Outbox(in_turn=lambda pid: seen.append("tree") or False).from_agent(10) is True
    assert seen == ["cgroup", "tree", "gone"]


# -- what counts as pressed --

@pytest.mark.asyncio
@pytest.mark.parametrize("result, pressed", [
    (PressResult(True, "Done.", {"line": "Done."}), True),
    (PressResult(False, "x", code="unknown_outcome"), True),     # it may have gone
    (PressResult(False, "x", code="error"), True),                # nobody can say
    (PressResult(False, "x", code="changed"), False),            # turned away: nothing went
    (PressResult(False, "x", code="refused"), False),
    (PressResult(False, "x", code="engine_down"), False),
    (PressResult(False, "x", code="too_big"), False),
])
async def test_only_a_press_that_went_or_may_have_is_remembered_as_pressed(home, result, pressed):
    box = Outbox({"note": Doing(result)})
    await box.press("note", "n1", "f1", 10)
    assert box.was_pressed("note", "n1") is pressed


@pytest.mark.asyncio
async def test_a_press_that_is_going_counts_and_a_turned_away_second_press_does_not_undo_it(home):
    gate = asyncio.Event()
    box = Outbox({"note": Doing(gate=gate)})
    first = asyncio.create_task(box.press("note", "n1", "f1", 10))
    await asyncio.sleep(0.01)
    assert box.was_pressed("note", "n1")                                  # the service may report it already
    assert (await box.press("note", "n1", "f1", 10)).code == "busy"
    assert box.was_pressed("note", "n1")
    gate.set()
    assert (await first).ok and box.was_pressed("note", "n1")


@pytest.mark.asyncio
async def test_a_refused_press_does_not_make_a_send_by_another_way_look_pressed(home):
    from bombadil.mail.watch import Says
    from bombadil.notices import Notices
    box = Outbox({"mail": Doing(PressResult(False, "That is not what the view showed.", code="changed"))})
    await box.press("mail", "d1", "f1", 10)
    heard = []
    notices = Notices(heard.append)
    Says(notices, box, None, None, None).sent({**RECEIPT, "draft": "d1"})
    [said] = [m for m in heard if m["type"] == "notice"]
    assert said["tone"] == "error" and "That was not your press on Send." in said["line"]
    assert [x["code"] for x in rows()] == ["changed", "no_press"]


# -- the log --

@pytest.mark.asyncio
async def test_the_log_holds_printable_fields_and_no_words(box):
    await box.press("note\nforged", "Dear Priya,\nthe word is on the sticky note\x1b[2J " + "x" * 300, "f\x00" * 100, 10)
    [row] = rows()
    assert row["kind"] == "noteforged" and " " not in row["id"] and len(row["id"]) <= 128
    assert "\x1b" not in row["id"] and len(row["fingerprint"]) <= 128 and row["code"] == "unknown_kind"


@pytest.mark.asyncio
async def test_the_log_is_rotated_so_a_loop_of_presses_cannot_fill_the_disk(home, monkeypatch):
    monkeypatch.setattr(outbox, "LOG_ROTATE_BYTES", 600)
    box = Outbox({"note": Doing()})
    for _ in range(40):
        await box.press("note", "n1", "f" * 64, 10)
    log = paths.press_log()
    assert log.stat().st_size <= 600 + 200 and log.with_name(log.name + ".1").exists()
    assert len(list(log.parent.glob("presses.jsonl*"))) == 2     # one file kept behind, never more


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.asyncio
async def test_the_log_is_the_persons_alone_new_or_already_there(box):
    await box.press("note", "n1", "f1", 10)
    assert _mode(paths.press_log()) == 0o600
    os.chmod(paths.press_log(), 0o644)         # a file an older agentd made
    await box.press("note", "n2", "f1", 10)
    assert _mode(paths.press_log()) == 0o600 and len(rows()) == 2


@pytest.mark.asyncio
async def test_a_loop_of_refusals_is_written_once_and_cannot_push_real_presses_out(home, monkeypatch):
    monkeypatch.setattr(outbox, "LOG_ROTATE_BYTES", 2000)
    box = Outbox({"note": Doing()})
    assert (await box.press("note", "n1", "f" * 64, 10)).ok
    for _ in range(300):
        assert (await box.press("note", "n1", "f" * 64, None)).code == "no_peer"
        assert (await box.press("slack", "n1", "f" * 64, 10)).code == "unknown_kind"
    log = paths.press_log()
    assert not log.with_name(log.name + ".1").exists()                  # nothing rotated
    assert [(x["code"], x["pid"]) for x in rows()] == [("", 10), ("no_peer", 0), ("unknown_kind", 10)]


@pytest.mark.asyncio
async def test_the_same_refusal_is_written_again_for_another_process_or_after_a_while(home, monkeypatch):
    box = Outbox({"note": Doing()})
    await box.press("note", "n1", "f1", None)
    await box.press("note", "n1", "f1", None)
    await box.press("slack", "n1", "f1", 10)
    await box.press("slack", "n1", "f1", 11)         # another process
    await box.press("slack", "n1", "f1", 10)
    assert [(x["code"], x["pid"]) for x in rows()] == [("no_peer", 0), ("unknown_kind", 10), ("unknown_kind", 11)]
    monkeypatch.setattr(outbox, "REFUSAL_REPEAT_SECONDS", 0.0)
    await box.press("slack", "n1", "f1", 10)
    assert len(rows()) == 4
    # What the service refused is not a loop of the same process's: every one of those stays.
    refused = Outbox({"note": Doing(PressResult(False, "no", code="changed"))})
    for _ in range(3):
        await refused.press("note", "n1", "f1", 10)
    assert [x["code"] for x in rows()].count("changed") == 3


@pytest.mark.asyncio
async def test_a_log_that_cannot_be_written_never_stops_a_press(home, monkeypatch, capsys):
    monkeypatch.setenv("BOMBADIL_PRESS_LOG", str(home / "a-file" / "presses.jsonl"))
    (home / "a-file").write_text("not a directory")
    box = Outbox({"note": Doing()})
    assert (await box.press("note", "n1", "f1", 10)).ok
    assert "press log" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_a_press_that_went_is_known_and_a_send_nobody_pressed_is_written_down(box):
    assert not box.was_pressed("note", "n1")
    await box.press("note", "n1", "f1", 10)
    assert box.was_pressed("note", "n1") and not box.was_pressed("note", "n2")
    box.saw_send("mail", "d9")
    assert rows()[-1] | {"t": 0} == {"t": 0, "kind": "mail", "id": "d9", "fingerprint": "", "ok": True,
                                     "code": "no_press", "pid": 0, "src": "agentd"}


@pytest.mark.asyncio
async def test_only_the_last_presses_are_remembered(home, monkeypatch):
    monkeypatch.setattr(outbox, "REMEMBER", 3)
    box = Outbox({"note": Doing()})
    for i in range(5):
        await box.press("note", f"n{i}", "f1", 10)
    assert [box.was_pressed("note", f"n{i}") for i in range(5)] == [False, False, True, True, True]


# -- the mail performer --

@pytest.mark.asyncio
async def test_a_mail_press_asks_the_service_to_send_that_fingerprint_and_returns_its_receipt(home, mail):
    mail.answer("send", {"receipt": RECEIPT})
    r = await Outbox().press("mail", "d1", "a" * 64, 10)
    assert r.ok and r.line == RECEIPT["line"] and r.receipt == RECEIPT
    [sent] = mail.asked("send")
    assert {k: sent[k] for k in ("op", "id", "fingerprint")} == {"op": "send", "id": "d1", "fingerprint": "a" * 64}
    assert len(mail.asked("send")) == 1


@pytest.mark.asyncio
async def test_a_receipt_may_be_the_answer_itself(home, mail):
    mail.answer("send", RECEIPT)
    r = await Outbox().press("mail", "d1", "a" * 64, 10)
    assert r.ok and r.receipt == RECEIPT


@pytest.mark.asyncio
@pytest.mark.parametrize("code, said", [
    ("changed", "That is not what you were shown."), ("refused", "That draft is not open."),
    ("not_found", "There is no such draft."), ("too_big", "Over what your provider takes."),
    ("engine_down", "Thunderbird is not running."), ("no_account", "That account is gone."),
])
async def test_what_the_service_refuses_is_said_in_its_own_sentence_and_not_tried_again(home, mail, code, said):
    mail.fail("send", said, code)
    r = await Outbox().press("mail", "d1", "a" * 64, 10)
    assert (r.ok, r.line, r.code) == (False, said, code)
    await asyncio.sleep(0.05)
    assert len(mail.asked("send")) == 1
    assert [x["code"] for x in rows()] == [code]


@pytest.mark.asyncio
async def test_a_send_that_may_or_may_not_have_happened_says_to_look_in_sent(home, mail):
    mail.fail("send", "Thunderbird did not say whether this went.", "unknown_outcome")
    r = await Outbox().press("mail", "d1", "a" * 64, 10)
    assert not r.ok and r.code == "unknown_outcome"
    assert r.line == "I can't tell whether that went. Look in Sent before you press Send again."
    await asyncio.sleep(0.05)
    assert len(mail.asked("send")) == 1      # and it was not sent again to find out


@pytest.mark.asyncio
async def test_a_service_that_stops_answering_after_it_was_asked_is_an_unknown_outcome(home, mail, monkeypatch):
    monkeypatch.setattr(outbox, "MAIL_SEND_SECONDS", 0.3)
    mail.delay("send", "hang")
    r = await Outbox().press("mail", "d1", "a" * 64, 10)
    assert not r.ok and r.code == "unknown_outcome" and "Look in Sent" in r.line
    assert len(mail.asked("send")) == 1


@pytest.mark.asyncio
async def test_a_service_that_was_never_reached_says_nothing_was_sent(home):
    r = await Outbox().press("mail", "d1", "a" * 64, 10)
    assert not r.ok and r.code == "engine_down" and r.line == "Mail is not running yet. Nothing was sent."


# -- said once --

def test_the_sentence_about_the_press_is_said_once_and_kept(home):
    assert outbox.told_mail_press() == "I never press Send for you. Change anything in it first if you like."
    assert outbox.told_mail_press() == ""
    assert json.loads((paths.state_dir() / "told.json").read_text()).keys() == {"mail-press"}
    outbox._said.clear()             # a new process: only the file remembers
    assert outbox.told_mail_press() == ""


def test_what_was_told_is_kept_for_the_person_alone(home):
    outbox.told_mail_press()
    assert _mode(paths.state_dir() / "told.json") == 0o600 and not list(paths.state_dir().glob(".told*"))


def test_what_is_told_is_kept_beside_what_else_was_told(home):
    (paths.state_dir()).mkdir(parents=True)
    (paths.state_dir() / "told.json").write_text('{"something-else": 1}')
    assert outbox.tell_once("x", "Hello.") == "Hello." and outbox.tell_once("x", "Hello.") == ""
    assert json.loads((paths.state_dir() / "told.json").read_text()).keys() == {"something-else", "x"}


@pytest.mark.parametrize("junk", ["not json", "[1, 2]", '"text"', ""])
def test_a_damaged_file_is_as_if_nothing_was_told(home, junk):
    paths.state_dir().mkdir(parents=True)
    (paths.state_dir() / "told.json").write_text(junk)
    assert outbox.tell_once("y", "Hi.") == "Hi." and outbox.tell_once("y", "Hi.") == ""


def test_a_file_that_cannot_be_written_costs_the_sentence_once_per_process(home, monkeypatch, capsys):
    monkeypatch.setenv("BOMBADIL_STATE", str(home / "file"))
    (home / "file").write_text("x")
    assert outbox.tell_once("z", "Hi.") == "Hi." and outbox.tell_once("z", "Hi.") == ""
    assert "told.json" in capsys.readouterr().err

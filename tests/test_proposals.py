import json
import os
import stat

import pytest

from bombadil import paths, proposals
from bombadil.connect.driver import fingerprint
from bombadil.proposals import ProposalError, Proposals

REF = "slack:T1/C1/1727780000.000100"
DAY = 86400.0


@pytest.fixture(autouse=True)
def nothing_of_the_real_home(home):
    """Every test here keeps its proposals under its own temporary directory."""
    return home


class Clock:
    def __init__(self, now=1_790_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def table(home, clock):
    return Proposals(home / "proposals.json", clock)


def reply(table, text="Yes, the 14th works.", **more):
    return table.open("slack_reply", REF, text, where="#launch", **more)


# -- the record --

def test_a_proposal_is_the_record_of_the_doc_and_a_persons_reply_starts_empty(table):
    p = table.open("slack_reply", REF, where="#launch")
    assert p["id"] == "p1" and p["kind"] == "slack_reply" and p["target"] == REF and p["content"] == ""
    assert p["where"] == "#launch" and p["created_by"] == "person" and p["tainted"] is False
    assert p["state"] == "open" and p["receipt"] is None and p["warnings"] == [] and p["shown"] == ""
    assert p["fingerprint"] == fingerprint("slack_reply", REF, "") and p["ready"] is False
    assert isinstance(p["updated"], float)
    # what the shell is told never carries the person's typed words or the count of tries
    assert "typed" not in p and "attempts" not in p


def test_the_agents_proposal_has_its_words_and_says_who_made_it(table):
    p = reply(table, created_by="agent", typed="yes, the 14th works")
    assert p["created_by"] == "agent" and p["content"] == "Yes, the 14th works."
    assert table.record(p["id"])["typed"] == "yes, the 14th works"


@pytest.mark.parametrize("kind, target, content", [
    ("slack", REF, ""), ("mail", REF, ""), ("slack_reply", "", ""), ("slack_reply", "  ", ""),
    ("slack_reply", None, ""), ("slack_reply", "x" * 201, ""), ("slack_reply", REF, 5), ("slack_reply", REF, {"a": "b"}),
    ("task_create", "linear", "text"), ("task_create", "linear", {"title": ["x"]}), ("task_create", "linear", {"1x": "y"}),
])
def test_what_is_not_a_proposal_is_refused_in_a_sentence(table, kind, target, content):
    with pytest.raises(ProposalError) as e:
        table.open(kind, target, content)
    assert str(e.value).endswith(".")
    assert table.all() == []


def test_a_task_takes_its_fields_as_text_and_a_box_not_typed_in_is_not_there(table):
    p = table.open("task_create", "linear", {"title": "Ship it", "description": None, "estimate": 3, "due": ""})
    assert p["content"] == {"title": "Ship it", "estimate": "3"}
    assert p["fingerprint"] == fingerprint("task_create", "linear", p["content"])


def test_what_the_card_drew_with_an_empty_box_for_every_box_is_the_draft_the_agent_made(table):
    # The shell reports one value per box it draws, "" for the ones nobody filled in; the button lights only when
    # that is the same draft, so an empty box and a missing one must not tell the two apart.
    p = table.open("task_create", "linear", {"title": "Ship it", "due": "2026-10-09"})
    shown = table.shown(p["id"], {"title": "Ship it", "description": "", "due": "2026-10-09"})
    assert shown["ready"] is True
    edited = table.edit(p["id"], {"title": "Ship it", "description": "", "due": "2026-10-09"})
    assert edited["content"] == {"title": "Ship it", "due": "2026-10-09"} and edited["fingerprint"] == p["fingerprint"]


def test_ids_are_never_reused_and_the_counter_survives_a_restart(home, clock):
    path = home / "proposals.json"
    first = Proposals(path, clock)
    a, b = first.open("slack_reply", REF), first.open("slack_reply", REF)
    assert (a["id"], b["id"]) == ("p1", "p2")
    first.discard(b["id"])                                  # an empty one is dropped at once ...
    assert Proposals(path, clock).get("p2") is None
    assert Proposals(path, clock).open("slack_reply", REF)["id"] == "p3"      # ... and its id is not given out again


def test_the_counter_survives_when_every_proposal_was_forgotten(home, clock):
    path = home / "proposals.json"
    table = Proposals(path, clock)
    p = reply(table)
    table.begin_send(p["id"])
    table.sent(p["id"], {"line": "Posted"})
    clock.now += 31 * DAY
    again = Proposals(path, clock)
    assert again.all() == []
    assert again.open("slack_reply", REF)["id"] == "p2"


# -- the file --

def test_the_file_is_the_persons_alone_and_written_whole(home, table):
    reply(table)
    path = home / "proposals.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    doc = json.loads(path.read_text())
    assert doc["version"] == 1 and doc["next"] == 2 and [p["id"] for p in doc["proposals"]] == ["p1"]
    assert sorted(os.listdir(home)) == ["proposals.json"]        # no temporary file is left beside it


def test_a_file_that_was_wider_is_narrowed_when_it_is_rewritten(home, table):
    reply(table)
    path = home / "proposals.json"
    path.chmod(0o644)
    reply(table)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_table_comes_back_as_it_was(home, clock):
    path = home / "proposals.json"
    first = Proposals(path, clock)
    p = reply(first, created_by="agent", tainted=True, typed="the 14th", new_place=True)
    first.shown(p["id"], p["content"])
    second = Proposals(path, clock)
    got = second.get(p["id"])
    assert got["content"] == p["content"] and got["created_by"] == "agent" and got["tainted"] is True
    assert [w["kind"] for w in got["warnings"]] == ["new_place", "tainted"]     # worked out again from the content
    assert got["shown"] == "" and got["ready"] is False        # nothing is drawn yet in a new process
    assert second.record(p["id"])["typed"] == "the 14th"


def test_closed_proposals_are_forgotten_after_thirty_days_and_open_ones_are_not(home, clock):
    path = home / "proposals.json"
    table = Proposals(path, clock)
    done, kept, put_away, unsure = reply(table), reply(table), reply(table), reply(table)
    table.begin_send(done["id"])
    table.sent(done["id"], {"line": "Posted in #launch"})
    table.discard(put_away["id"])
    table.begin_send(unsure["id"])
    table.unknown(unsure["id"], "It may have gone.")
    clock.now += 29 * DAY
    assert {p["id"] for p in Proposals(path, clock).all()} == {"p1", "p2", "p3", "p4"}
    clock.now += 2 * DAY
    assert [p["id"] for p in Proposals(path, clock).all()] == [kept["id"]]


def test_a_file_that_cannot_be_read_is_set_aside_and_the_table_starts_empty_with_its_counter(home, clock):
    path = home / "proposals.json"
    path.write_text('{"version": 1, "next": 9, "proposals": [{"id": "p8"')       # cut off in the middle
    table = Proposals(path, clock)
    assert table.all() == []
    assert not path.exists() and [n for n in os.listdir(home) if n.startswith("proposals.json.bad-")]
    assert table.open("slack_reply", REF)["id"] == "p9"


@pytest.mark.parametrize("text", [
    "[]", "null", '{"version": 2, "next": 4, "proposals": []}', '{"version": 1, "proposals": "x"}', "\x00\x01 not json",
])
def test_a_file_of_a_version_or_shape_nobody_knows_is_not_believed(home, clock, text):
    path = home / "proposals.json"
    path.write_text(text)
    table = Proposals(path, clock)
    assert table.all() == []
    assert [n for n in os.listdir(home) if n.startswith("proposals.json.bad-")]
    assert table.open("slack_reply", REF)["id"] in ("p1", "p4")


def test_the_counter_of_a_file_of_another_version_is_kept_so_ids_stay_new(home, clock):
    (home / "proposals.json").write_text('{"version": 7, "next": 12, "proposals": []}')
    assert Proposals(home / "proposals.json", clock).open("slack_reply", REF)["id"] == "p12"


def test_a_record_that_is_not_valid_is_left_out_and_the_rest_is_kept(home, clock):
    path = home / "proposals.json"
    good = {"id": "p2", "kind": "slack_reply", "target": REF, "content": "Yes.", "state": "open", "updated": clock.now}
    bad = [{"id": "p1", "kind": "email", "target": REF, "content": "", "state": "open"},
           {"id": "x", "kind": "slack_reply", "target": REF, "content": "", "state": "open"},
           {"id": "p3", "kind": "slack_reply", "target": REF, "content": 5, "state": "open"},
           {"id": "p4", "kind": "slack_reply", "target": REF, "content": "", "state": "nowhere"}, "text", None]
    path.write_text(json.dumps({"version": 1, "next": 5, "proposals": [*bad, good]}))
    table = Proposals(path, clock)
    assert [p["id"] for p in table.all()] == ["p2"]
    assert table.open("slack_reply", REF)["id"] == "p5"


def test_a_press_that_was_in_flight_when_agentd_stopped_may_have_gone(home, clock):
    path = home / "proposals.json"
    table = Proposals(path, clock)
    p = reply(table)
    table.begin_send(p["id"])
    assert Proposals(path, clock).get(p["id"])["state"] == "unknown"


def test_what_was_being_sent_is_unsure_shows_what_was_pressed_on_and_is_told_to_the_bar_after_the_restart(home, clock):
    path = home / "proposals.json"
    table = Proposals(path, clock)
    p = reply(table)
    table.shown(p["id"], "Yes, the 14th works.")
    table.begin_send(p["id"])
    other = table.open("slack_reply", REF + "2", "Another.", where="#ops")        # still open: nothing to say of it
    again = Proposals(path, clock)
    [told] = again.recovered()
    assert told["id"] == p["id"] and told["state"] == "unknown" and told["ready"] is False
    assert told["shown"] == told["fingerprint"] == fingerprint("slack_reply", REF, "Yes, the 14th works.")
    assert again.get(other["id"])["state"] == "open"
    # It is the person's to press again, with the fingerprint of what they saw: the table agrees it is what was shown.
    assert again.begin_send(p["id"], again=True)["state"] == "sending"
    assert again.recovered() == []                                                  # it is being sent again: no longer unsure


def test_a_lone_surrogate_from_the_shell_is_dropped_and_never_stops_the_table(home, clock, capsys):
    """A split emoji or an odd clipboard can leave half of a character in a box. It cannot be sent or written, so it
    goes like the characters that draw as nothing, and every later change to every proposal still works."""
    table = Proposals(home / "proposals.json", clock)
    p = reply(table)
    half = chr(0xD800)
    out = table.edit(p["id"], f"Yes{half}, the 14th{half} works.")
    assert out["content"] == "Yes, the 14th works." and out["cleaned_from"] is not None
    task = table.open("task_create", "linear", {"title": f"A{half}title"}, where="Linear")
    assert task["content"] == {"title": "Atitle"}
    other = reply(table, "Fine.")
    assert table.begin_send(other["id"])["state"] == "sending" and "proposals file" not in capsys.readouterr().err
    assert Proposals(home / "proposals.json", clock).get(p["id"])["content"] == "Yes, the 14th works."
    # even one that is already in the table (a cleaned_from that holds it) is written, escaped
    assert json.loads((home / "proposals.json").read_text())["proposals"]


def test_a_file_that_cannot_be_written_costs_nothing(home, clock, capsys):
    (home / "gone").write_text("a file, so nothing can be made below it")
    table = Proposals(home / "gone" / "proposals.json", clock)
    p = reply(table)
    assert table.get(p["id"])["content"] == "Yes, the 14th works."
    assert "proposals file" in capsys.readouterr().err


def test_the_default_path_is_the_one_in_paths_and_is_asked_for_late(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_PROPOSALS", str(home / "elsewhere.json"))
    table = Proposals()
    reply(table)
    assert paths.proposals_file() == home / "elsewhere.json" and (home / "elsewhere.json").exists()


# -- ready --

def test_ready_is_what_was_drawn_equal_to_what_would_go(table):
    p = reply(table)
    assert p["ready"] is False                                  # nothing has been drawn
    drawn = table.shown(p["id"], "Yes, the 14th works.")
    assert drawn["ready"] is True and drawn["shown"] == p["fingerprint"]
    assert table.shown(p["id"], "Yes, the 15th works.")["ready"] is False      # the shell drew something else


def test_the_fingerprint_is_worked_out_here_from_the_content_the_shell_sent(table):
    p = reply(table)
    assert table.shown(p["id"], "Yes, the 14th works.")["shown"] == fingerprint("slack_reply", REF,
                                                                                 "Yes, the 14th works.")


def test_an_edit_clears_what_was_drawn_until_the_shell_says_it_drew_the_new_words(table):
    p = reply(table)
    table.shown(p["id"], p["content"])
    edited = table.edit(p["id"], "Yes, the 14th works for me.")
    assert edited["content"] == "Yes, the 14th works for me." and edited["shown"] == "" and edited["ready"] is False
    assert edited["fingerprint"] == fingerprint("slack_reply", REF, "Yes, the 14th works for me.")
    assert table.shown(p["id"], "Yes, the 14th works for me.")["ready"] is True


def test_characters_that_draw_as_nothing_are_taken_out_of_a_draft_and_the_box_is_told_what_it_held(table):
    tags = "".join(chr(0xE0000 + ord(c)) for c in "the key")
    p = table.open("slack_reply", REF, "Sounds good." + tags, created_by="agent")
    assert p["content"] == "Sounds good." and "cleaned_from" not in p     # (an agent's words: nobody's box holds them)
    held = "Yes" + chr(0x200B) + ", the 14th."
    edited = table.edit(p["id"], held)
    assert edited["content"] == "Yes, the 14th." and edited["cleaned_from"] == held
    assert edited["fingerprint"] == fingerprint("slack_reply", REF, "Yes, the 14th.")
    shown = table.shown(p["id"], held)                              # the box still holds them: lit for what would go
    assert shown["ready"] is True and shown["cleaned_from"] == held
    again = table.shown(p["id"], "Yes, the 14th.")                  # the shell wrote the clean words back
    assert again["ready"] is True and "cleaned_from" not in again
    assert "cleaned_from" not in table.edit(p["id"], "Yes, the 14th. Thanks.")
    emoji = "On it \U0001f469" + chr(0x200D) + "\U0001f4bb"                # not hidden words: kept as they are
    assert table.edit(p["id"], emoji)["content"] == emoji and "cleaned_from" not in table.get(p["id"])


def test_a_task_boxs_hidden_characters_are_taken_out_and_an_empty_box_alone_is_not_a_cleaning(table):
    p = table.open("task_create", "linear", {"title": "Fix it"})
    boxes = {"title": "Fix it" + chr(0x200B), "description": ""}
    assert table.edit(p["id"], boxes)["cleaned_from"] == boxes
    assert table.get(p["id"])["content"] == {"title": "Fix it"}
    assert "cleaned_from" not in table.edit(p["id"], {"title": "Fix it", "description": "", "due": None})


def test_a_reply_with_nothing_in_it_is_never_ready(table):
    p = table.open("slack_reply", REF)
    assert table.shown(p["id"], "")["ready"] is False
    assert table.shown(p["id"], "   \n ")["ready"] is False
    task = table.open("task_comment", "linear:LIN-42")
    assert table.shown(task["id"], {"body": ""})["ready"] is False
    assert table.shown(task["id"], {"body": "Looks right."})["ready"] is False      # not what the proposal holds
    assert table.edit(task["id"], {"body": "Looks right."})["content"] == {"body": "Looks right."}
    assert table.shown(task["id"], {"body": "Looks right."})["ready"] is True


def test_content_that_is_not_valid_for_the_kind_lights_nothing_and_changes_nothing(table):
    p = reply(table)
    assert table.edit(p["id"], {"not": "text"}) is None
    assert table.record(p["id"])["content"] == "Yes, the 14th works."
    assert table.shown(p["id"], 12)["ready"] is False


def test_only_an_open_proposal_takes_an_edit_and_only_one_that_exists(table):
    p = reply(table)
    table.begin_send(p["id"])
    assert table.edit(p["id"], "other") is None and table.edit("p99", "x") is None and table.edit(None, "x") is None
    assert table.get(p["id"])["ready"] is False and table.get("p99") is None and table.shown("p99", "x") is None


# -- warnings --

def kinds(p):
    return [w["kind"] for w in p["warnings"]]


def test_a_place_the_person_has_not_heard_from_is_a_warning_said_in_plain_words(table):
    p = reply(table, new_place=True)
    assert kinds(p) == ["new_place"]
    assert p["warnings"][0]["text"].startswith("You have not heard from or posted in this conversation lately.")
    assert kinds(reply(table, new_place=False)) == []


@pytest.mark.parametrize("text, word", [
    ("@channel the launch moved", "@channel"), ("ok @here", "@here"), ("hi @Everyone!", "@everyone"),
    ("<!channel> heads up", "@channel"), ("<!here|here> now", "@here"),
])
def test_a_reply_that_reaches_everyone_says_so(table, text, word):
    p = reply(table, text)
    assert kinds(p) == ["broadcast"] and word in p["warnings"][0]["text"]


@pytest.mark.parametrize("text", ["the channels are quiet", "write to channel@acme.test", "everyone is here", "@channels"])
def test_words_that_only_look_like_it_are_not_a_broadcast(table, text):
    assert "broadcast" not in kinds(reply(table, text))


def test_over_three_thousand_characters_is_too_long_for_one_slack_message(table):
    assert kinds(reply(table, "x" * 3000)) == []
    p = reply(table, "x" * 3001)
    assert kinds(p) == ["long"] and "3,000" in p["warnings"][0]["text"]


def test_words_the_agent_wrote_after_reading_messages_are_flagged_unless_the_person_typed_them(table):
    flagged = reply(table, "Yes, the 14th works.", created_by="agent", tainted=True, typed="say we can do the 14th")
    assert kinds(flagged) == ["tainted"] and "words you typed" in flagged["warnings"][0]["text"]
    typed = "tell her: yes,   the 14th works."
    assert kinds(reply(table, "Yes, the 14th works.", created_by="agent", tainted=True, typed=typed)) == []
    assert kinds(reply(table, "Yes, the 14th works.", created_by="agent", tainted=False, typed="")) == []
    assert kinds(reply(table, "Yes, the 14th works.", created_by="person", tainted=True)) == []   # theirs


def test_a_task_is_held_to_the_persons_words_field_by_field(table):
    said = "make a ticket: fix the login page"
    ok = table.open("task_create", "linear", {"title": "fix the login page"}, created_by="agent", tainted=True, typed=said)
    odd = table.open("task_create", "linear", {"title": "fix the login page", "description": "Priya asked for it"},
                     created_by="agent", tainted=True, typed=said)
    assert kinds(ok) == [] and kinds(odd) == ["tainted"]


def test_the_warnings_come_in_the_order_they_matter(table):
    p = reply(table, "@channel " + "x" * 3000, created_by="agent", tainted=True, new_place=True)
    assert kinds(p) == ["new_place", "broadcast", "long", "tainted"]


def test_an_edit_works_the_warnings_out_again(table):
    p = reply(table, "Fine.", created_by="agent", tainted=True, typed="")
    assert kinds(p) == ["tainted"]
    assert kinds(table.edit(p["id"], "@here fine")) == ["broadcast", "tainted"]


# -- states --

def test_a_press_goes_open_sending_sent_and_keeps_its_receipt(table):
    p = reply(table)
    table.shown(p["id"], p["content"])
    sending = table.begin_send(p["id"])
    assert sending["state"] == "sending" and sending["ready"] is False
    assert proposals.perform_id(table.record(p["id"])) == p["id"]
    sent = table.sent(p["id"], {"line": "Posted in #launch · 11:04", "web": {"name": "Slack", "url": "https://acme.test/x"}})
    assert sent["state"] == "sent" and sent["receipt"]["line"] == "Posted in #launch · 11:04" and sent["ready"] is False
    assert table.begin_send(p["id"]) is None and table.edit(p["id"], "again") is None      # a sent one is done


def test_a_send_may_only_begin_from_open_or_from_unsure_on_the_persons_second_choice(table):
    p = reply(table)
    assert table.begin_send(p["id"], again=True) is None          # not unsure
    table.begin_send(p["id"])
    assert table.begin_send(p["id"]) is None                       # already sending
    table.unknown(p["id"], "I can't tell whether that went.")
    assert table.begin_send(p["id"]) is None                       # never by itself
    again = table.begin_send(p["id"], again=True)
    assert again["state"] == "sending" and again["note"] == ""


def test_each_try_after_the_first_has_an_id_of_its_own_for_the_service(table):
    p = reply(table)
    ids = []
    for n in range(3):
        table.begin_send(p["id"], again=n > 0)
        ids.append(proposals.perform_id(table.record(p["id"])))
        table.unknown(p["id"])
    assert ids == ["p1", "p1-2", "p1-3"]


def test_a_refusal_opens_it_again_with_the_services_sentence_and_the_content_is_still_there(table):
    p = reply(table)
    table.shown(p["id"], p["content"])
    table.begin_send(p["id"])
    back = table.reopen(p["id"], "Slack is slow. Try again in a moment.")
    assert back["state"] == "open" and back["note"] == "Slack is slow. Try again in a moment."
    assert back["content"] == "Yes, the 14th works." and back["ready"] is True       # still what is drawn


def test_a_result_is_only_taken_for_a_send_that_began(table):
    p = reply(table)
    assert table.sent(p["id"], {"line": "x"}) is None and table.unknown(p["id"]) is None
    assert table.reopen(p["id"]) is None and table.sent("p99", None) is None


def test_discarding_an_empty_box_drops_it_and_a_written_one_is_kept_as_put_away(table):
    empty, written = table.open("slack_reply", REF), reply(table)
    assert table.discard(empty["id"])["state"] == "discarded" and table.get(empty["id"]) is None
    assert table.discard(written["id"])["state"] == "discarded" and table.get(written["id"])["state"] == "discarded"
    assert table.discard(written["id"]) is None and table.discard("p99") is None


def test_an_unsure_one_can_be_put_away_and_one_being_sent_cannot(table):
    p, q = reply(table), reply(table)
    table.begin_send(p["id"])
    assert table.discard(p["id"]) is None
    table.unknown(p["id"])
    assert table.discard(p["id"])["state"] == "discarded"
    table.begin_send(q["id"])
    table.sent(q["id"], {"line": "ok"})
    assert table.discard(q["id"]) is None


# -- what the shell is told --

def test_the_shell_is_told_whenever_what_it_would_see_changes(home, clock):
    told = []
    table = Proposals(home / "proposals.json", clock, on_change=told.append)
    p = table.open("slack_reply", REF, "Yes.", where="#launch")
    table.shown(p["id"], "Yes.")
    table.edit(p["id"], "Yes!")
    table.begin_send(p["id"])
    table.sent(p["id"], {"line": "Posted"})
    assert [(t["state"], t["ready"], t["content"]) for t in told] == [
        ("open", False, "Yes."), ("open", True, "Yes."), ("open", False, "Yes!"), ("sending", False, "Yes!"),
        ("sent", False, "Yes!")]
    assert all("typed" not in t for t in told)


def test_a_listener_that_breaks_never_costs_a_change(home, clock, capsys):
    def broken(_p):
        raise RuntimeError("boom")
    table = Proposals(home / "proposals.json", clock, on_change=broken)
    p = reply(table)
    assert table.get(p["id"])["content"] == "Yes, the 14th works."
    assert "proposal listener" in capsys.readouterr().err


def test_a_table_keeps_at_most_a_hundred_and_loses_the_oldest_closed_ones_first(home, clock):
    table = Proposals(home / "proposals.json", clock)
    first = reply(table)
    table.begin_send(first["id"])
    table.sent(first["id"], {"line": "ok"})
    for _ in range(100):
        clock.now += 1
        reply(table)
    ids = {p["id"] for p in table.all()}
    assert len(ids) == 100 and first["id"] not in ids and "p2" in ids

import asyncio

import pytest

from bombadil import notices
from bombadil.notices import Notices


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(**kw):
    sent = []
    clock = Clock()
    return Notices(sent.append, clock=clock, mono=clock, **kw), sent, clock


OPEN = {"id": "open", "label": "Open", "style": "primary"}


def test_a_notice_is_the_message_the_shell_gets():
    n, sent, clock = make()
    nid = n.post("mail", "Priya: launch date", "ask", [OPEN, {"id": "later", "label": "Later"}], ttl=30)
    assert nid == 1
    assert sent == [{"type": "notice", "id": 1, "source": "mail", "line": "Priya: launch date", "tone": "ask",
                     "actions": [OPEN, {"id": "later", "label": "Later", "style": "quiet"}], "ttl": 30.0,
                     "at": clock.t}]
    assert n.post("mail", "second") == 2


def test_the_stack_holds_four_and_the_oldest_makes_room():
    n, sent, _ = make()
    ids = [n.post("mail", f"line {i}") for i in range(1, 5)]
    assert len(n) == 4 and [m["type"] for m in sent] == ["notice"] * 4
    sent.clear()
    fifth = n.post("mail", "line 5")
    # The oldest is ended before the newest is said, so a client never shows five.
    assert sent[0] == {"type": "notice_end", "id": ids[0]} and sent[1]["id"] == fifth
    assert [x["id"] for x in n.live()] == [*ids[1:], fifth]


def test_an_error_is_not_pushed_off_by_news_but_a_stack_of_errors_still_makes_room():
    n, sent, _ = make()
    bad = n.post("mail", "I can't tell whether that went.", "error")
    news = [n.post("mail", f"mail {i}", "ask") for i in range(1, 4)]
    for i in range(4, 9):
        n.post("mail", f"mail {i}", "ask")
    assert bad in [x["id"] for x in n.live()] and news[0] not in [x["id"] for x in n.live()] and len(n) == 4
    errors = [n.post("mail", f"error {i}", "error") for i in range(3)]
    assert [x["tone"] for x in n.live()] == ["error"] * 4 and [x["id"] for x in n.live()][0] == bad
    last = n.post("mail", "one more", "error")             # nothing but errors: the oldest of them goes
    assert [x["id"] for x in n.live()] == [*errors, last] and bad not in [x["id"] for x in n.live()]


def test_words_from_someone_else_become_one_clean_line():
    n, sent, _ = make()
    n.post("mail", "Priya\nShah:\x1b[2J\x07 \u202egpj.exe  " + "x" * 400, "loud")
    msg = sent[-1]
    assert "\n" not in msg["line"] and "\x1b" not in msg["line"] and "\x07" not in msg["line"]
    assert "\u202e" not in msg["line"] and msg["line"].startswith("Priya Shah: [2J gpj.exe")
    assert len(msg["line"]) == notices.MAX_LINE and msg["line"].endswith("…")
    assert msg["tone"] == "step"   # not a tone: the quiet one


def test_only_well_formed_chips_get_through_and_no_more_than_three():
    n, sent, _ = make()
    n.post("mail", "x", "ask", [{"id": "a", "label": "A"}, {"id": "Bad Id", "label": "B"}, {"id": "c", "label": ""},
                                "nope", {"id": "d", "label": "D" * 100, "style": "loud"},
                                {"id": "e", "label": "E"}, {"id": "f", "label": "F"}])
    assert [(a["id"], a["style"]) for a in sent[-1]["actions"]] == [("a", "quiet"), ("d", "quiet"), ("e", "quiet")]
    assert len(sent[-1]["actions"][1]["label"]) == notices.MAX_LABEL


def test_a_notice_is_changed_where_it_stands():
    n, sent, clock = make()
    a = n.post("mail", "first", "ask", [OPEN], ttl=10)
    b = n.post("mail", "second")
    clock.t += 5
    sent.clear()
    assert n.replace(a, "Sent to Priya", "done", actions=[])
    assert sent == [{"type": "notice", "id": a, "source": "mail", "line": "Sent to Priya", "tone": "done",
                     "actions": [], "ttl": 10.0, "at": clock.t}]
    assert [x["id"] for x in n.live()] == [a, b]         # still in its place
    assert n.get(a).until == clock.t + 5                    # the ttl was not restarted: none was given
    n.replace(a, ttl=60)
    assert n.get(a).until == clock.t + 60
    n.replace(a, ttl=0)
    assert n.get(a).until is None
    n.dismiss(b)
    assert n.replace(b, "too late") is False and n.replace(99) is False


def test_a_chip_runs_its_handler_and_the_notice_ends():
    n, sent, _ = make()
    seen = []

    async def handler(action_id):
        seen.append(action_id)

    nid = n.post("mail", "x", "ask", [OPEN], handler=handler)
    assert asyncio.run(n.action(nid, "open")) is True
    assert seen == ["open"] and len(n) == 0 and sent[-1] == {"type": "notice_end", "id": nid}


def test_a_handler_can_leave_the_notice_up_or_change_it_into_something_else():
    n, _, _ = make()

    async def keeps(_a):
        return False

    async def changes(_a):
        n.replace(changes_id, "Working on it", "step", actions=[])

    k = n.post("mail", "x", "ask", [OPEN], handler=keeps)
    changes_id = n.post("mail", "y", "ask", [OPEN], handler=changes)
    assert asyncio.run(n.action(k, "open")) and asyncio.run(n.action(changes_id, "open"))
    assert {x["id"] for x in n.live()} == {k, changes_id}       # neither was ended behind its handler's back
    assert n.get(changes_id).line == "Working on it"


def test_a_chip_the_notice_does_not_have_or_a_notice_with_no_handler_does_nothing():
    n, _, _ = make()
    ran = []

    async def handler(a):
        ran.append(a)

    nid = n.post("mail", "x", "ask", [OPEN], handler=handler)
    bare = n.post("mail", "y", "ask", [OPEN])
    assert asyncio.run(n.action(nid, "delete")) is False      # not one of its chips
    assert asyncio.run(n.action(bare, "open")) is False
    assert asyncio.run(n.action(99, "open")) is False
    assert ran == [] and len(n) == 2


@pytest.mark.asyncio
async def test_a_second_tap_while_the_first_runs_does_nothing():
    n, _, _ = make()
    gate, ran = asyncio.Event(), []

    async def handler(a):
        ran.append(a)
        await gate.wait()

    nid = n.post("mail", "x", "ask", [OPEN], handler=handler)
    first = asyncio.create_task(n.action(nid, "open"))
    await asyncio.sleep(0)
    assert await n.action(nid, "open") is False          # a double click on Reply makes one draft
    gate.set()
    assert await first is True and ran == ["open"] and len(n) == 0


def test_a_handler_that_fails_leaves_the_notice_and_can_be_pressed_again():
    n, _, _ = make()
    calls = []

    async def handler(_a):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("no window")

    nid = n.post("mail", "x", "ask", [OPEN], handler=handler)
    with pytest.raises(RuntimeError):
        asyncio.run(n.action(nid, "open"))
    assert len(n) == 1
    assert asyncio.run(n.action(nid, "open")) is True and len(n) == 0


def test_dismiss_ends_it_once():
    n, sent, _ = make()
    nid = n.post("mail", "x")
    assert n.dismiss(nid) is True and n.dismiss(nid) is False
    assert [m for m in sent if m["type"] == "notice_end"] == [{"type": "notice_end", "id": nid}]


def test_a_notice_runs_out_when_its_time_is_up_and_not_before():
    n, sent, clock = make()
    short = n.post("mail", "short", ttl=10)
    long = n.post("mail", "long", ttl=100)
    stays = n.post("mail", "stays")
    assert n.next_expiry() == 10 and n.expire() == []
    clock.t += 9.9
    assert n.expire() == []
    clock.t += 0.1
    assert n.expire() == [short] and n.next_expiry() == 90
    clock.t += 1000
    assert n.expire() == [long] and n.next_expiry() is None     # the one with no ttl waits for ever
    assert [x["id"] for x in n.live()] == [stays]
    assert [m for m in sent if m["type"] == "notice_end"] == [{"type": "notice_end", "id": short},
                                                              {"type": "notice_end", "id": long}]


def test_a_client_that_connects_later_is_given_what_is_still_up_oldest_first():
    n, _, _ = make()
    a, b, c = (n.post("mail", x) for x in "abc")
    n.dismiss(b)
    assert [x["id"] for x in n.live()] == [a, c]
    assert all(x["type"] == "notice" for x in n.live())


def test_the_wire_copy_cannot_change_the_notice():
    n, _, _ = make()
    n.post("mail", "x", "ask", [OPEN])
    n.live()[0]["actions"][0]["label"] = "Delete everything"
    assert n.live()[0]["actions"][0]["label"] == "Open"

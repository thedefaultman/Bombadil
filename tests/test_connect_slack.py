"""The Slack driver (src/bombadil/connect/slack.py) against a Slack that speaks its wire (tests/connect_fakes).

The driver is built the way the service builds it: a record, a `Secrets` of its own, an `emit` that collects what
is pushed, a clock. Nothing waits in real time for a back-off: the driver is given a `sleep` that a test releases
one wait at a time. Everything invented here is invented: Acme, Alex Chen (the person), Priya Shah, Marcus Webb.
Nothing has been run against the real Slack; the fake is written from Slack's public documentation.
"""

import asyncio
import json
import os
import time

import pytest
import pytest_asyncio
from connect_fakes.fake_slack import NOW, FakeSlack, make_token

from bombadil.connect import manifest, slack
from bombadil.connect.driver import MESSAGE_KEYS, DriverError, Secrets, UnknownOutcome
from bombadil.connect.protocol import clean_message

pytestmark = pytest.mark.asyncio

ME, PRIYA, MARCUS = "U0ALEX", "U0PRIYA", "U0MARCUS"
SENTINEL = "~flush~"
AUTH_SENTENCE = "Slack no longer accepts this connection. Set it up again."


async def until(predicate, timeout: float = 5.0, what: str = "the condition") -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.005)


class MemorySecrets(Secrets):
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name=None):
        if name is None:
            self.values.clear()
        else:
            self.values.pop(name, None)

    def names(self):
        return sorted(self.values)


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Sleeper:
    """The driver's waits. `auto` lets each one through at once; otherwise each stays until `release()`."""

    def __init__(self, auto: bool = True):
        self.auto = auto
        self.delays: list[float] = []
        self._waiting: list[asyncio.Future] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self.auto:
            await asyncio.sleep(0)
            return
        waiter = asyncio.get_running_loop().create_future()
        self._waiting.append(waiter)
        await waiter

    async def release(self) -> None:
        await until(lambda: self._waiting, what="the driver to wait")
        self._waiting.pop(0).set_result(None)


class Rig:
    def __init__(self, fake: FakeSlack, driver: slack.SlackDriver, secrets: MemorySecrets, clock: Clock,
                 sleeper: Sleeper, pushes: list):
        self.fake, self.driver, self.secrets, self.clock, self.sleeper, self.pushes = (
            fake, driver, secrets, clock, sleeper, pushes)
        self.flushed = 0

    @property
    def messages(self) -> list[dict]:
        return [p["message"] for p in self.pushes
                if p["push"] == "message" and not p["message"]["text"].startswith(SENTINEL)]

    @property
    def states(self) -> list[dict]:
        return [p for p in self.pushes if p["push"] == "state"]

    def store_tokens(self) -> None:
        self.secrets.set("app_token", self.fake.app_token)
        self.secrets.set("user_token", self.fake.user_token)

    async def connected(self, settle: bool = True) -> None:
        """Both tokens are there and the driver starts, as after a restart of the service."""
        self.store_tokens()
        await self.driver.start()
        await self.fake.wait_socket()
        if settle:
            await self.settled()

    async def settled(self) -> None:
        await until(lambda: self.driver._backfill_task is None or self.driver._backfill_task.done(),
                    what="the catch-up to end")

    async def flush(self) -> None:
        """Everything delivered before this has been looked at: the worker takes events in order."""
        self.flushed += 1
        text = f"{SENTINEL}{self.flushed}"
        await self.fake.say("D0PRIYA", PRIYA, text)
        await until(lambda: any(p["push"] == "message" and p["message"]["text"] == text for p in self.pushes),
                    what="the flush message")

    async def dm(self, text: str, channel: str = "D0PRIYA", user: str = PRIYA, **more) -> dict:
        ts = await self.fake.say(channel, user, text, **more)
        await until(lambda: any(m["ts"] == float(ts) for m in self.messages), what="the message")
        return next(m for m in self.messages if m["ts"] == float(ts))


@pytest_asyncio.fixture
async def rig(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    fake = await FakeSlack().start()
    monkeypatch.setenv("BOMBADIL_SLACK_API", fake.api)
    before = set(asyncio.all_tasks())
    pushes: list[dict] = []
    secrets, clock, sleeper = MemorySecrets(), Clock(), Sleeper()
    conn = {"id": "slack:w1", "kind": "slack", "service": "slack", "name": "Slack", "state": "setup", "note": ""}
    driver = slack.SlackDriver(conn, secrets, pushes.append, clock, sleep=sleeper)
    yield Rig(fake, driver, secrets, clock, sleeper, pushes)
    await driver.stop()
    await fake.stop()
    await asyncio.sleep(0)
    left = [t for t in asyncio.all_tasks() if t not in before and t is not asyncio.current_task()
            and not t.done()]
    assert not left, left


@pytest.fixture
def utc():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


def event(fake: FakeSlack, channel: str, user: str, text: str, **more) -> dict:
    return fake.event(channel, {"type": "message", "user": user, "text": text, "ts": fake.ts(), **more})


# -- setup --

async def test_with_no_tokens_the_state_is_setup_and_the_steps_say_what_to_do(rig):
    await rig.driver.start()
    assert rig.driver.state == "setup"
    assert rig.driver.steps() == [
        {"id": "create", "say": "Create the app in your Slack workspace from the manifest.",
         "open": manifest.creation_url()},
        {"id": "app_token", "say": "Make an app-level token for it with the connections:write permission."},
        {"id": "install", "say": "Install the app to your workspace."},
        {"id": "user_token", "say": "Copy its User OAuth Token."}]
    assert [p["state"] for p in rig.states] == ["setup"] and rig.states[0]["note"]
    assert not rig.driver.can_post() and rig.fake.requests == []
    assert rig.driver.secret_rules == {"app_token": "xapp-", "user_token": "xoxp-"}


async def test_with_one_token_only_the_missing_part_is_asked_for(rig):
    rig.secrets.set("app_token", rig.fake.app_token)
    await rig.driver.start()
    assert rig.driver.state == "setup" and [s["id"] for s in rig.driver.steps()] == ["install", "user_token"]
    assert "User OAuth" in rig.driver.note
    rig.secrets.delete("app_token")
    rig.secrets.set("user_token", rig.fake.user_token)
    await rig.driver.secrets_changed()
    assert [s["id"] for s in rig.driver.steps()] == ["app_token"] and "app-level" in rig.driver.note
    assert rig.fake.requests == []


async def test_a_value_that_does_not_start_as_a_token_does_is_not_a_token(rig):
    rig.secrets.set("app_token", "not-an-app-token")
    rig.secrets.set("user_token", "also-not")
    await rig.driver.start()
    assert rig.driver.state == "setup" and len(rig.driver.steps()) == 4 and rig.fake.requests == []


async def test_the_driver_connects_when_the_second_token_arrives_and_takes_the_name_from_auth_test(rig):
    await rig.driver.start()
    rig.secrets.set("app_token", rig.fake.app_token)
    await rig.driver.secrets_changed()
    assert rig.driver.state == "setup" and rig.fake.requests == []
    rig.secrets.set("user_token", rig.fake.user_token)
    await rig.driver.secrets_changed()
    await rig.fake.wait_socket()
    assert rig.driver.state == "ok" and rig.driver.note == "" and rig.driver.can_post()
    assert rig.states[-1] == {"push": "state", "state": "ok", "note": "", "name": "Acme"}
    assert rig.driver.conn["name"] == "Acme" and "Acme" in rig.driver.reads()
    assert rig.driver.steps() == []
    assert rig.driver.team_id == "T0ACME"
    # the app token opens the socket, the person's own token does everything else
    for r in rig.fake.requests:
        assert r.token == (rig.fake.app_token if r.method == "apps.connections.open" else rig.fake.user_token)


async def test_before_it_knows_the_workspace_the_sentence_says_so(rig):
    assert rig.driver.reads() == ("Your direct messages, the messages that mention you and the threads you are in, "
                                  "in your workspace.")
    await rig.connected()
    assert rig.driver.reads() == ("Your direct messages, the messages that mention you and the threads you are in, "
                                  "in Acme.")


async def test_a_store_that_brings_nothing_new_costs_nothing(rig):
    await rig.connected()
    calls = len(rig.fake.requests)
    await rig.driver.secrets_changed()
    await rig.driver.secrets_changed()
    assert len(rig.fake.requests) == calls and rig.fake.opened == 1


async def test_a_new_token_makes_a_new_connection(rig):
    await rig.connected()
    rig.fake.user_token = make_token("user")
    rig.secrets.set("user_token", rig.fake.user_token)
    await rig.driver.secrets_changed()
    await until(lambda: rig.fake.opened == 2 and len(rig.fake.sockets) == 1, what="the new socket")
    assert len(rig.fake.calls("auth.test")) == 2 and rig.driver.state == "ok"


async def test_a_token_taken_away_puts_the_driver_back_in_setup_and_closes_the_socket(rig):
    await rig.connected()
    rig.secrets.delete("user_token")
    await rig.driver.secrets_changed()
    assert rig.driver.state == "setup" and [s["id"] for s in rig.driver.steps()] == ["install", "user_token"]
    await until(lambda: not rig.fake.sockets, what="the socket to close")
    assert not rig.driver.can_post()


# -- errors become states --

@pytest.mark.parametrize("error", ["invalid_auth", "token_revoked", "account_inactive", "not_authed"])
async def test_a_token_slack_no_longer_accepts_is_an_error_that_says_to_set_up_again(rig, error):
    rig.fake.fail("auth.test", error)
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "error" and rig.driver.note == AUTH_SENTENCE
    assert rig.states[-1] == {"push": "state", "state": "error", "note": AUTH_SENTENCE}
    assert rig.fake.opened == 0 and rig.fake.calls("apps.connections.open") == []
    assert not rig.driver.can_post() and rig.driver.steps() == []
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", "slack:T0ACME/D0PRIYA/1789988000.000001", "hello")
    assert e.value.code == "auth" and str(e.value) == AUTH_SENTENCE
    assert rig.fake.calls("chat.postMessage") == []


async def test_an_error_that_is_cleared_connects_again_on_the_next_store(rig):
    rig.fake.fail("auth.test", "invalid_auth")
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "error"
    rig.fake.clear()
    await rig.driver.secrets_changed()
    await rig.fake.wait_socket()
    assert rig.driver.state == "ok" and rig.states[-1]["state"] == "ok"


async def test_a_missing_permission_is_an_error_that_names_it(rig):
    rig.fake.missing_scope("auth.test", "im:history")
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "error"
    assert rig.driver.note == "The Slack app lacks the im:history permission. Set it up again from the manifest."
    assert rig.fake.opened == 0


@pytest.mark.parametrize("error", ["app_approval", "not_allowed_token_type", "restricted_action"])
async def test_an_administrators_wall_is_blocked(rig, error):
    rig.fake.fail("auth.test", error)
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "blocked" and rig.states[-1]["state"] == "blocked"
    assert "administrator" in rig.driver.note and rig.fake.opened == 0


async def test_an_app_token_slack_refuses_is_an_error_too(rig):
    rig.fake.fail("apps.connections.open", "invalid_auth")
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "error" and rig.driver.note == AUTH_SENTENCE and rig.fake.opened == 0
    assert [s["state"] for s in rig.states] == ["ok", "error"]


async def test_a_token_revoked_while_connected_ends_the_connection(rig):
    await rig.connected()
    rig.fake.revoke_tokens()
    await rig.fake.say("D0MARCUS", MARCUS, "A name nobody has looked up yet")
    await until(lambda: rig.driver.state == "error", what="the error")
    assert rig.driver.note == AUTH_SENTENCE and rig.messages == []
    await until(lambda: not rig.fake.sockets and not rig.driver._tasks, what="everything to stop")


async def test_a_slack_that_cannot_be_reached_is_an_error_until_it_can(rig):
    rig.sleeper.auto = False
    rig.fake.fail("auth.test", "service_unavailable", times=1)
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "error" and rig.driver.note == slack.UNREACHABLE_SENTENCE
    assert rig.sleeper.delays == [1.0]
    await rig.sleeper.release()
    await rig.fake.wait_socket()
    await until(lambda: rig.driver.state == "ok", what="the connection")
    assert [s["state"] for s in rig.states] == ["error", "ok"] and rig.states[-1]["name"] == "Acme"


async def test_a_slack_that_is_not_there_at_all_is_the_same(rig):
    rig.sleeper.auto = False
    await rig.fake.stop()
    rig.store_tokens()
    await rig.driver.start()
    assert rig.driver.state == "error" and rig.driver.note == slack.UNREACHABLE_SENTENCE
    await rig.fake.start()
    await rig.sleeper.release()
    await until(lambda: rig.driver.state == "ok", what="the connection")


# -- what is a message for the person --

async def test_a_direct_message_is_a_message_for_the_person(rig):
    await rig.connected()
    m = await rig.dm("Legal just signed off. Can we lock the 14th?")
    assert set(m) == set(MESSAGE_KEYS)
    assert m == {
        "ref": f"slack:T0ACME/D0PRIYA/{rig.fake.messages['D0PRIYA'][0]['ts']}", "source": "slack",
        "connection": "slack:w1", "conversation": {"id": "D0PRIYA", "name": "Priya Shah", "kind": "dm"},
        "thread": None, "from": {"id": PRIYA, "name": "Priya Shah"},
        "text": "Legal just signed off. Can we lock the 14th?", "ts": float(rig.fake.messages["D0PRIYA"][0]["ts"]),
        "mentions_me": False, "reason": "direct message", "unread": True, "web_url": None}
    assert clean_message(m, "slack:w1") == m          # the service's own cleaning has nothing to change


async def test_a_group_direct_message_is_a_message_for_the_person(rig):
    await rig.connected()
    m = await rig.dm("Lunch?", channel="G0TRIO", user=MARCUS)
    assert m["conversation"] == {"id": "G0TRIO", "name": "priya, marcus", "kind": "group"}
    assert m["reason"] == "direct message" and m["from"]["name"] == "Marcus Webb"


async def test_a_channel_message_that_mentions_the_person_is_for_them(rig):
    await rig.connected()
    m = await rig.dm(f"<@{ME}> can you look at the timeline?", channel="C0LAUNCH")
    assert m["reason"] == "mentioned you" and m["mentions_me"] is True
    assert m["conversation"] == {"id": "C0LAUNCH", "name": "#launch", "kind": "channel"}
    assert m["text"] == "@Alex Chen can you look at the timeline?"


async def test_a_private_channel_message_that_mentions_the_person_is_for_them(rig):
    await rig.connected()
    m = await rig.dm(f"<@{ME}> the Q4 plan is in", channel="G0ROADMAP", user=MARCUS)
    assert m["conversation"] == {"id": "G0ROADMAP", "name": "#roadmap", "kind": "private"}
    assert m["reason"] == "mentioned you"


async def test_a_mention_with_a_name_after_the_id_is_still_a_mention(rig):
    await rig.connected()
    m = await rig.dm(f"<@{ME}|alex> are you there?", channel="C0LAUNCH")
    assert m["reason"] == "mentioned you" and m["text"] == "@Alex Chen are you there?"


@pytest.mark.parametrize("text", [
    "Anyone seen the build?",
    "<!here> standup in five",
    "<!channel> freeze starts now",
    "<!everyone> welcome Marcus",
    f"<@{MARCUS}> over to you",
    "<!subteam^S0DESIGN|@design> a look please",
    f"alex is not a mention, and <@{ME[:-1]}> is somebody else",
])
async def test_a_channel_message_that_does_not_mention_the_person_is_not_for_them(rig, text):
    await rig.connected()
    await rig.fake.say("C0LAUNCH", PRIYA, text)
    await rig.flush()
    assert rig.messages == []


async def test_the_persons_own_messages_are_not_for_them_wherever_they_are(rig):
    await rig.connected()
    await rig.fake.say("D0PRIYA", ME, "On my way")
    await rig.fake.say("C0LAUNCH", ME, f"<@{ME}> note to self")
    await rig.fake.say("G0TRIO", ME, "Hello you two")
    await rig.flush()
    assert rig.messages == [] and rig.fake.calls("conversations.info") == []
    assert [r.params["user"] for r in rig.fake.calls("users.info")] == [PRIYA]      # the flush's, not theirs


async def test_a_reply_in_a_thread_the_person_started_is_for_them(rig):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", ME, "Draft of the announcement is up")
    m = await rig.dm("Looks good to me", channel="C0LAUNCH", thread_ts=root)
    assert m["reason"] == "a thread you are in" and m["mentions_me"] is False
    assert m["thread"] == f"slack:T0ACME/C0LAUNCH/{root}"
    assert m["ref"] != m["thread"]
    assert rig.fake.calls("conversations.replies") == []     # their own event said so


async def test_a_reply_in_a_thread_the_person_replied_in_is_for_them(rig):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", PRIYA, "Who owns the changelog?")
    await rig.fake.say("C0LAUNCH", ME, "I do", thread_ts=root)
    await rig.fake.say("C0LAUNCH", MARCUS, "Not that I recall", thread_ts=root)
    await until(lambda: rig.messages, what="the reply")
    assert [m["text"] for m in rig.messages] == ["Not that I recall"]
    assert rig.messages[0]["reason"] == "a thread you are in" and rig.fake.calls("conversations.replies") == []


async def test_a_thread_the_person_posted_in_before_the_driver_knew_is_looked_up_once(rig):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", PRIYA, "Who owns the changelog?", deliver=False)
    await rig.fake.say("C0LAUNCH", ME, "I do", thread_ts=root, deliver=False)
    for text in ("Thanks", "One more thing"):
        await rig.fake.say("C0LAUNCH", MARCUS, text, thread_ts=root)
    await until(lambda: len(rig.messages) == 2, what="both replies")
    assert [m["reason"] for m in rig.messages] == ["a thread you are in"] * 2
    assert len(rig.fake.calls("conversations.replies")) == 1


async def test_a_thread_the_person_is_not_in_is_looked_at_once_and_then_left_alone(rig):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", PRIYA, "Anyone up for a retro?", deliver=False)
    for text in ("Me", "Me too", "And me"):
        await rig.fake.say("C0LAUNCH", MARCUS, text, thread_ts=root)
    await rig.flush()
    assert rig.messages == [] and len(rig.fake.calls("conversations.replies")) == 1
    await rig.fake.say("C0LAUNCH", ME, "Count me in", thread_ts=root)
    await rig.fake.say("C0LAUNCH", PRIYA, "Great", thread_ts=root)
    await until(lambda: rig.messages, what="the reply after the person joined")
    assert rig.messages[0]["text"] == "Great" and len(rig.fake.calls("conversations.replies")) == 1


async def test_a_reply_in_a_direct_conversation_is_a_direct_message_with_its_thread(rig):
    await rig.connected()
    root = await rig.fake.say("D0PRIYA", PRIYA, "Question", deliver=False)
    m = await rig.dm("Answer to my own question", thread_ts=root)
    assert m["reason"] == "direct message" and m["thread"] == f"slack:T0ACME/D0PRIYA/{root}"


async def test_the_first_message_of_a_thread_has_no_thread_of_its_own(rig):
    await rig.connected()
    ts = rig.fake.ts()
    ev = rig.fake.event("D0PRIYA", {"type": "message", "user": PRIYA, "text": "Parent", "ts": ts, "thread_ts": ts})
    await rig.fake.deliver(ev)
    await until(lambda: rig.messages, what="the message")
    assert rig.messages[0]["thread"] is None


@pytest.mark.parametrize("subtype", ["message_changed", "message_deleted", "channel_join", "channel_leave",
                                     "pinned_item", "unpinned_item", "channel_topic", "channel_purpose",
                                     "channel_name", "group_join", "reminder_add", "huddle_thread"])
async def test_slacks_housekeeping_is_not_a_message(rig, subtype):
    await rig.connected()
    await rig.dm("Priya is known from now on")
    looked_up = len(rig.fake.requests)
    await rig.fake.deliver(event(rig.fake, "D0MARCUS", MARCUS, "<@U0ALEX> joined", subtype=subtype))
    await rig.fake.deliver(rig.fake.event("D0MARCUS", {
        "type": "message", "subtype": subtype, "hidden": True, "ts": rig.fake.ts(),
        "message": {"type": "message", "user": MARCUS, "text": "an edit", "ts": rig.fake.ts()}}))
    await rig.flush()
    assert len(rig.messages) == 1 and len(rig.fake.requests) == looked_up


@pytest.mark.parametrize("fields", [{}, {"text": ""}, {"text": "   \n "}, {"text": None}])
async def test_a_message_with_no_text_is_not_a_message(rig, fields):
    await rig.connected()
    ev = event(rig.fake, "D0PRIYA", PRIYA, "x")
    del ev["text"]
    ev.update(fields)
    ev["files"] = [{"id": "F0PLAN", "name": "plan.pdf"}]
    await rig.fake.deliver(ev)
    await rig.flush()
    assert rig.messages == []


@pytest.mark.parametrize("subtype", ["thread_broadcast", "file_share", "me_message"])
async def test_what_people_say_in_other_ways_is_still_a_message(rig, subtype):
    await rig.connected()
    m = await rig.dm("Here is the plan", subtype=subtype)
    assert m["text"] == "Here is the plan"


async def test_an_apps_message_in_a_direct_conversation_is_named_by_the_app(rig):
    await rig.connected()
    ts = rig.fake.ts()
    ev = rig.fake.event("D0PRIYA", {"type": "message", "subtype": "bot_message", "bot_id": "B0DEPLOY",
                                    "username": "Deploybot", "text": "Deploy finished", "ts": ts})
    await rig.fake.deliver(ev)
    await until(lambda: rig.messages, what="the message")
    assert rig.messages[0]["from"] == {"id": "B0DEPLOY", "name": "Deploybot"}


async def test_events_that_are_not_messages_are_acknowledged_and_ignored(rig):
    await rig.connected()
    conn = rig.fake.live()[0]
    for kind in ("interactive", "slash_commands"):
        await conn.sock.send(json.dumps({"envelope_id": f"e-{kind}", "type": kind, "payload": {}}))
    await conn.sock.send(json.dumps({"envelope_id": "e-reaction", "type": "events_api", "payload": {
        "event": {"type": "reaction_added", "user": PRIYA}}}))
    await conn.sock.send("this is not json")
    await conn.sock.send(json.dumps(["not", "an", "envelope"]))
    for envelope in ("e-interactive", "e-slash_commands", "e-reaction"):
        await rig.fake.wait_ack(envelope)
    await rig.flush()
    assert rig.messages == []


# -- plain words --

@pytest.mark.parametrize("raw, plain", [
    (f"<@{PRIYA}> hello", "@Priya Shah hello"),
    (f"<@{PRIYA}|priya> and <@{MARCUS}>", "@Priya Shah and @Marcus Webb"),
    ("<@U0NOBODY> who", "@Someone who"),
    ("see <#C0LAUNCH|launch> for it", "see #launch for it"),
    ("<https://acme.test/plan|the plan> is out", "the plan (https://acme.test/plan) is out"),
    ("read <https://acme.test/plan> first", "read https://acme.test/plan first"),
    ("<https://acme.test/plan|https://acme.test/plan>", "https://acme.test/plan"),
    ("write to <mailto:priya@acme.test|priya@acme.test>", "write to priya@acme.test"),
    ("<!here> and <!channel> and <!everyone>", "@here and @channel and @everyone"),
    ("<!here|here> now", "@here now"),
    ("<!subteam^S0DESIGN|@design> go", "@design go"),
    ("<!subteam^S0DESIGN> go", "@group go"),
    ("<!date^1790000000^{date_num}|2026-09-21> works", "2026-09-21 works"),
    ("fish &amp; chips, 1 &lt; 2 &gt; 0", "fish & chips, 1 < 2 > 0"),
    ("&amp;lt; stays as it was typed", "&lt; stays as it was typed"),
    ("<https://acme.test/a?x=1&amp;y=2|link>", "link (https://acme.test/a?x=1&y=2)"),
    ("two\nlines   and   spaces", "two\nlines and spaces"),
])
async def test_text_is_made_plain(rig, raw, plain):
    await rig.connected()
    m = await rig.dm(raw)
    assert m["text"] == plain


async def test_a_channel_link_without_a_label_uses_the_name_already_known(rig):
    await rig.connected()
    await rig.dm(f"<@{ME}> hi", channel="C0LAUNCH")        # makes #launch known
    m = await rig.dm("meet in <#C0LAUNCH>")
    assert m["text"] == "meet in #launch"


async def test_a_name_is_looked_up_once_and_kept_for_a_day(rig):
    await rig.connected()
    await rig.dm("one")
    await rig.dm("two")
    await rig.dm("three", channel="D0MARCUS", user=MARCUS)
    assert len(rig.fake.calls("users.info")) == 2
    rig.clock.now += 3600
    await rig.dm("four")
    assert len(rig.fake.calls("users.info")) == 2
    rig.clock.now += 86400
    await rig.dm("five")
    assert len(rig.fake.calls("users.info")) == 3


async def test_a_name_that_cannot_be_looked_up_is_someone_and_is_not_asked_for_again_at_once(rig):
    await rig.connected()
    rig.fake.fail("users.info", "user_not_found")
    m = await rig.dm("Who am I?")
    assert m["from"]["name"] == "Someone" and m["conversation"]["name"] == "Someone"
    await rig.dm("And now?")
    assert len(rig.fake.calls("users.info")) == 1


async def test_a_conversation_is_looked_up_once(rig):
    await rig.connected()
    for text in ("one", "two"):
        await rig.dm(f"<@{ME}> {text}", channel="C0LAUNCH")
    assert len(rig.fake.calls("conversations.info")) == 1


async def test_the_set_of_threads_the_person_is_in_is_bounded(rig):
    await rig.connected()
    for _ in range(slack.THREADS + 20):
        await rig.fake.say("C0LAUNCH", ME, "a thread of mine")
    await rig.flush()
    assert len(rig.driver._threads) == slack.THREADS


# -- the socket --

async def test_every_envelope_is_acknowledged_before_it_is_processed(rig):
    await rig.connected()
    gate = rig.fake.hold("users.info")
    first = await rig.fake.deliver(event(rig.fake, "D0PRIYA", PRIYA, "one"))
    await until(lambda: gate.arrived == 1, what="the lookup to begin")
    await rig.fake.wait_ack(first)
    assert rig.messages == []                                  # still being processed
    second = await rig.fake.deliver(event(rig.fake, "D0PRIYA", PRIYA, "two"))
    await rig.fake.wait_ack(second)                            # and the next one is acknowledged while it is
    assert rig.messages == []
    gate.release()
    await until(lambda: len(rig.messages) == 2, what="both messages")
    assert [m["text"] for m in rig.messages] == ["one", "two"]
    assert rig.fake.acks[:2] == [first, second]


async def test_an_acknowledgement_is_exactly_the_envelope_id(rig):
    await rig.connected()
    await rig.fake.say("D0PRIYA", PRIYA, "hello")
    await rig.flush()
    assert set(rig.fake.acks) == set(rig.fake.delivered) and rig.fake.client_messages == []


async def test_a_disconnect_is_answered_with_a_new_socket_at_once(rig):
    rig.sleeper.auto = False
    await rig.connected()
    await rig.fake.send_disconnect("refresh_requested")
    await until(lambda: rig.fake.opened == 2 and len(rig.fake.sockets) == 1, what="the new socket")
    assert rig.sleeper.delays == []
    await rig.dm("After the refresh")


async def test_a_storm_of_disconnects_is_slowed_down_after_a_few(rig):
    rig.sleeper.auto = False
    await rig.connected()
    for n in range(2, 5):
        await rig.fake.send_disconnect("too_many_websockets")
        await until(lambda: rig.fake.opened == n and len(rig.fake.sockets) == 1, what="the next socket")
    assert rig.sleeper.delays == []
    await rig.fake.send_disconnect("too_many_websockets")
    await until(lambda: rig.sleeper.delays == [1.0], what="the back-off")
    await rig.sleeper.release()
    await until(lambda: rig.fake.opened == 5, what="the next socket")


async def test_a_dropped_socket_is_reopened_after_a_back_off_that_doubles(rig):
    rig.sleeper.auto = False
    await rig.connected()
    for n, delay in enumerate([1.0, 2.0, 4.0], start=2):
        await rig.fake.drop_socket()
        await until(lambda: rig.sleeper.delays and rig.sleeper.delays[-1] == delay, what="the wait")
        await rig.sleeper.release()
        await until(lambda: rig.fake.opened == n and len(rig.fake.sockets) == 1, what="the new socket")
    assert rig.sleeper.delays == [1.0, 2.0, 4.0]
    rig.clock.now += 60                       # this one lived: the next drop starts again at one second
    await rig.fake.drop_socket()
    await until(lambda: len(rig.sleeper.delays) == 4, what="the wait")
    assert rig.sleeper.delays[-1] == 1.0
    await rig.sleeper.release()
    await until(lambda: rig.fake.opened == 5, what="the new socket")
    await rig.dm("Still here")


async def test_the_back_off_stops_at_a_minute_and_the_person_is_told_when_it_goes_on(rig):
    rig.sleeper.auto = False
    await rig.connected()
    rig.fake.fail("apps.connections.open", "service_unavailable")
    await rig.fake.drop_socket()
    for n in range(8):
        await until(lambda: len(rig.sleeper.delays) == n + 1, what="the wait")
        await rig.sleeper.release()
    await until(lambda: len(rig.sleeper.delays) == 9, what="the wait")
    assert rig.sleeper.delays[:9] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
    assert rig.driver.state == "error" and rig.driver.note == slack.UNREACHABLE_SENTENCE
    rig.fake.clear()
    await rig.sleeper.release()
    await until(lambda: rig.driver.state == "ok" and len(rig.fake.sockets) == 1, what="the connection back")
    assert [s["state"] for s in rig.states] == ["ok", "error", "ok"]
    await rig.dm("Back again")


async def test_a_socket_that_goes_quiet_is_dropped_and_reopened(rig, monkeypatch):
    monkeypatch.setattr(slack, "PING_EVERY", 0.05)
    monkeypatch.setattr(slack, "PING_TIMEOUT", 0.1)
    rig.sleeper.auto = False
    await rig.connected()
    rig.fake.go_silent()
    await until(lambda: rig.sleeper.delays == [1.0], what="the quiet socket to be dropped")
    await rig.sleeper.release()
    await until(lambda: rig.fake.opened == 2 and [c for c in rig.fake.sockets if not c.silent], what="a new socket")
    await rig.dm("Heard you")


async def test_a_socket_that_answers_its_pings_is_left_alone(rig, monkeypatch):
    monkeypatch.setattr(slack, "PING_EVERY", 0.03)
    monkeypatch.setattr(slack, "PING_TIMEOUT", 0.08)
    rig.sleeper.auto = False
    await rig.connected()
    await asyncio.sleep(0.4)
    assert rig.fake.opened == 1 and rig.sleeper.delays == [] and rig.driver.state == "ok"


# -- catching up --

def old_and_new(fake: FakeSlack) -> dict:
    ts = {}
    ts["old"] = fake.ts(days_ago=20)
    ts["recent"] = fake.ts(days_ago=2)
    ts["mine"] = fake.ts(days_ago=1)
    ts["marcus"] = fake.ts(days_ago=3)
    ts["trio"] = fake.ts(days_ago=1)
    ts["channel"] = fake.ts(days_ago=1)
    for channel, user, key, text in (
            ("D0PRIYA", PRIYA, "old", "From three weeks ago"), ("D0PRIYA", PRIYA, "recent", "Legal signed off"),
            ("D0PRIYA", ME, "mine", "Thanks, noted"), ("D0MARCUS", MARCUS, "marcus", "Standup moved to ten"),
            ("G0TRIO", MARCUS, "trio", "Lunch on Friday?"),
            ("C0LAUNCH", PRIYA, "channel", f"<@{ME}> in a channel")):
        fake.messages[channel].append({"type": "message", "user": user, "text": text, "ts": ts[key]})
    return ts


async def test_the_start_up_catch_up_emits_recent_direct_messages_once(rig):
    ts = old_and_new(rig.fake)
    await rig.connected()
    assert [m["text"] for m in rig.messages] == ["Standup moved to ten", "Legal signed off", "Lunch on Friday?"]
    assert [m["ref"] for m in rig.messages] == [
        f"slack:T0ACME/D0MARCUS/{ts['marcus']}", f"slack:T0ACME/D0PRIYA/{ts['recent']}",
        f"slack:T0ACME/G0TRIO/{ts['trio']}"]
    assert [m["reason"] for m in rig.messages] == ["direct message"] * 3
    assert [m["conversation"]["kind"] for m in rig.messages] == ["dm", "dm", "group"]
    assert rig.messages[1]["from"] == {"id": PRIYA, "name": "Priya Shah"}
    lists = rig.fake.calls("conversations.list")
    assert [r.params["types"] for r in lists] == ["im,mpim"]
    asked = rig.fake.calls("conversations.history")
    assert sorted(r.params["channel"] for r in asked) == ["D0MARCUS", "D0PRIYA", "G0TRIO"]
    assert {r.params["limit"] for r in asked} == {"20"}
    assert rig.fake.calls("conversations.replies") == []


async def test_a_message_caught_up_and_then_delivered_is_only_emitted_once(rig):
    old_and_new(rig.fake)
    await rig.connected()
    again = rig.fake.messages["D0PRIYA"][1]
    await rig.fake.deliver(rig.fake.event("D0PRIYA", again))
    await rig.flush()
    assert [m["text"] for m in rig.messages].count("Legal signed off") == 1


async def test_a_quick_reconnect_does_not_go_through_the_direct_messages_again(rig):
    old_and_new(rig.fake)
    rig.sleeper.auto = False
    await rig.connected()
    calls = len(rig.fake.calls("conversations.history"))
    await rig.fake.drop_socket()
    await rig.sleeper.release()
    await until(lambda: rig.fake.opened == 2, what="the new socket")
    await rig.flush()
    assert len(rig.fake.calls("conversations.history")) == calls and len(rig.messages) == 3


async def test_a_reconnect_after_a_long_gap_catches_up_again_without_repeating_anything(rig):
    old_and_new(rig.fake)
    rig.sleeper.auto = False
    await rig.connected()
    calls = len(rig.fake.calls("conversations.history"))
    await rig.fake.drop_socket()
    await until(lambda: rig.sleeper.delays, what="the wait")
    rig.clock.now += 600
    missed = await rig.fake.say("D0MARCUS", MARCUS, "While you were away", deliver=False)
    await rig.sleeper.release()
    await until(lambda: any(m["text"] == "While you were away" for m in rig.messages), what="the missed message")
    assert len(rig.fake.calls("conversations.history")) == calls + 3
    assert [m["text"] for m in rig.messages].count("Legal signed off") == 1 and len(rig.messages) == 4
    assert rig.messages[-1]["ref"] == f"slack:T0ACME/D0MARCUS/{missed}"


async def test_the_catch_up_pages_through_the_conversations_and_stops_at_a_hundred(rig):
    rig.fake.page_size = 30
    for n in range(120):
        uid = rig.fake.add_user(f"U1{n:04d}", f"Colleague {n}")
        channel = rig.fake.add_dm(uid)
        await rig.fake.say(channel, uid, f"Hello {n}", deliver=False)
    await rig.connected()        # the three conversations Acme starts with have nothing in them
    assert len(rig.fake.calls("conversations.history")) == 100
    assert len(rig.fake.calls("conversations.list")) == 4
    assert [m["text"] for m in rig.messages] == [f"Hello {n}" for n in range(97)]


async def test_no_more_than_five_requests_are_in_flight_at_once(rig):
    for n in range(12):
        uid = rig.fake.add_user(f"U1{n:04d}", f"Colleague {n}")
        await rig.fake.say(rig.fake.add_dm(uid), uid, f"Hello {n}", deliver=False)
    gate = rig.fake.hold("conversations.history")
    rig.store_tokens()
    start = asyncio.ensure_future(rig.driver.start())
    await until(lambda: gate.arrived >= 5, what="five requests")
    await asyncio.sleep(0.1)
    assert gate.arrived == 5
    gate.release()
    await start
    await rig.settled()
    assert rig.fake.max_inflight["conversations.history"] == 5 and len(rig.messages) == 12


async def test_a_429_is_waited_out_for_at_most_thirty_seconds_and_tried_once_more(rig):
    rig.fake.channels = {"D0PRIYA": rig.fake.channels["D0PRIYA"]}
    rig.fake.messages = {"D0PRIYA": [{"type": "message", "user": PRIYA, "text": "Hello", "ts": rig.fake.ts(1)}]}
    rig.fake.rate_limit("conversations.history", 120)
    await rig.connected()
    assert rig.sleeper.delays == [30.0] and len(rig.fake.calls("conversations.history")) == 2
    assert [m["text"] for m in rig.messages] == ["Hello"]


async def test_a_call_that_is_limited_twice_is_given_up_quietly(rig):
    rig.fake.channels = {"D0PRIYA": rig.fake.channels["D0PRIYA"]}
    rig.fake.messages = {"D0PRIYA": [{"type": "message", "user": PRIYA, "text": "Hello", "ts": rig.fake.ts(1)}]}
    rig.fake.rate_limit("conversations.history", 5, times=2)
    await rig.connected()
    assert rig.sleeper.delays == [5.0] and rig.messages == [] and rig.driver.state == "ok"


async def test_a_catch_up_that_fails_costs_the_connection_nothing(rig):
    old_and_new(rig.fake)
    rig.fake.fail("conversations.list", "internal_error")
    await rig.connected()
    assert rig.messages == [] and rig.driver.state == "ok"
    await rig.dm("Live messages still come")


# -- asked for --

async def test_messages_are_the_ones_the_driver_has_newest_first(rig):
    await rig.connected()
    refs = []
    for n in range(5):
        refs.append((await rig.dm(f"message {n}"))["ref"])
    got = await rig.driver.messages()
    assert [m["ref"] for m in got] == refs[::-1]
    assert [m["ref"] for m in await rig.driver.messages(limit=2)] == refs[:-3:-1]
    since = got[2]["ts"]
    assert [m["ref"] for m in await rig.driver.messages(since=since)] == refs[:-3:-1]
    got[0]["text"] = "changed by the caller"
    assert (await rig.driver.messages(limit=1))[0]["text"] == "message 4"


async def test_the_ring_holds_two_hundred(rig):
    await rig.connected()
    for n in range(250):
        await rig.fake.say("D0PRIYA", PRIYA, f"message {n}")
    await rig.flush()
    got = await rig.driver.messages(limit=1000)
    assert len(got) == slack.RING == 200
    assert got[0]["text"].startswith(SENTINEL) and got[-1]["text"] == "message 51"
    assert len([p for p in rig.pushes if p["push"] == "message"]) == 251


async def test_a_thread_is_its_messages_oldest_first_and_only_the_one_asked_for_has_an_address(rig):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", ME, "Draft of the announcement is up")
    reply = await rig.fake.say("C0LAUNCH", PRIYA, f"Looks good, <@{MARCUS}> too?", thread_ts=root)
    await rig.fake.say("C0LAUNCH", MARCUS, "Yes from me", thread_ts=root)
    await until(lambda: len(rig.messages) == 2, what="the replies")
    got = await rig.driver.thread(f"slack:T0ACME/C0LAUNCH/{reply}")
    assert [m["text"] for m in got] == ["Draft of the announcement is up", "Looks good, @Marcus Webb too?",
                                        "Yes from me"]
    assert [m["from"]["name"] for m in got] == ["Alex Chen", "Priya Shah", "Marcus Webb"]
    assert [m["reason"] for m in got] == ["you wrote this", "in #launch", "in #launch"]
    assert [m["thread"] for m in got] == [None, f"slack:T0ACME/C0LAUNCH/{root}", f"slack:T0ACME/C0LAUNCH/{root}"]
    assert all(set(m) == set(MESSAGE_KEYS) for m in got)
    assert [m["web_url"] for m in got] == [None, f"https://acme.slack.test/archives/C0LAUNCH/p{reply.replace('.', '')}",
                                          None]
    assert len(rig.fake.calls("chat.getPermalink")) == 1
    assert rig.fake.calls("conversations.replies")[-1].params["ts"] == root      # the ring knew the thread


async def test_a_message_that_is_not_in_a_thread_comes_with_the_conversation_before_it(rig):
    await rig.connected()
    ts = []
    for n in range(4):
        ts.append(await rig.fake.say("D0PRIYA", PRIYA if n % 2 == 0 else ME, f"line {n}"))
    got = await rig.driver.thread(f"slack:T0ACME/D0PRIYA/{ts[2]}", limit=2)
    assert [m["text"] for m in got] == ["line 1", "line 2"] and got[-1]["web_url"]
    assert [m["reason"] for m in got] == ["you wrote this", "direct message"]
    assert rig.fake.calls("conversations.history")[-1].params["latest"] == ts[2]


async def test_a_thread_of_another_workspace_or_that_is_gone_is_not_found(rig):
    await rig.connected()
    for ref in ("slack:T9OTHER/C0LAUNCH/1789988000.000100", "slack:T0ACME/C0LAUNCH/1789988000.000100",
                "slack:T0ACME/C0GONE/1789988000.000100", "nonsense", "mail:abc/def", "slack:T0ACME/C0LAUNCH"):
        with pytest.raises(DriverError) as e:
            await rig.driver.thread(ref)
        assert e.value.code in ("not_found",), ref
        assert str(e.value) and "xox" not in str(e.value)
    assert rig.fake.calls("chat.getPermalink") == []


async def test_a_thread_before_the_driver_is_connected_is_an_error_not_a_crash(rig):
    await rig.driver.start()
    with pytest.raises(DriverError):
        await rig.driver.thread("slack:T0ACME/C0LAUNCH/1789988000.000100")
    assert await rig.driver.messages() == []


async def test_what_the_service_asks_for_never_writes_to_slack(rig):
    old_and_new(rig.fake)
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", ME, "A thread of mine")
    reply = await rig.fake.say("C0LAUNCH", PRIYA, "A reply", thread_ts=root)
    await rig.flush()
    await rig.driver.messages()
    await rig.driver.thread(f"slack:T0ACME/C0LAUNCH/{reply}")
    assert rig.fake.writes() == []


# -- the press --

async def test_a_reply_to_a_direct_message_is_posted_under_that_message_and_says_so(rig, utc):
    await rig.connected()
    m = await rig.dm("Can we lock the 14th?")
    receipt = await rig.driver.perform("slack_reply", m["ref"], "Yes, the 14th works for me.")
    posted = rig.fake.posted[-1]
    assert receipt == {"line": "Replied to Priya Shah in a direct message · 11:04", "web": {
        "name": "Slack", "url": f"https://acme.slack.test/archives/D0PRIYA/p{posted['ts'].replace('.', '')}"}}
    sent = rig.fake.calls("chat.postMessage")
    assert len(sent) == 1 and sent[0].token == rig.fake.user_token
    assert sent[0].params == {"channel": "D0PRIYA", "text": "Yes, the 14th works for me.",
                              "thread_ts": m["ref"].rsplit("/", 1)[1], "unfurl_links": "false"}
    assert posted["user"] == ME


async def test_a_reply_in_a_channel_thread_goes_under_the_threads_first_message(rig, utc):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", PRIYA, "Who can lock the date?", deliver=False)
    m = await rig.dm(f"<@{ME}> you?", channel="C0LAUNCH", thread_ts=root)
    receipt = await rig.driver.perform("slack_reply", m["ref"], "I can.")
    assert receipt["line"] == "Posted in #launch · 11:04"
    assert rig.fake.calls("chat.postMessage")[0].params["thread_ts"] == root
    assert rig.fake.posted[-1]["thread_ts"] == root


async def test_a_reply_in_a_group_conversation_says_so(rig, utc):
    await rig.connected()
    m = await rig.dm("Lunch?", channel="G0TRIO", user=MARCUS)
    receipt = await rig.driver.perform("slack_reply", m["ref"], "Yes please")
    assert receipt["line"] == "Replied in a group message · 11:04"


async def test_a_message_the_driver_no_longer_holds_is_looked_up_before_it_is_answered(rig, utc):
    await rig.connected()
    root = await rig.fake.say("C0LAUNCH", PRIYA, "Who owns the changelog?", deliver=False)
    reply = await rig.fake.say("C0LAUNCH", MARCUS, "Not me", thread_ts=root, deliver=False)
    receipt = await rig.driver.perform("slack_reply", f"slack:T0ACME/C0LAUNCH/{reply}", "I will take it")
    assert rig.fake.calls("conversations.replies")[-1].params == {"channel": "C0LAUNCH", "ts": reply, "limit": "1"}
    assert rig.fake.calls("chat.postMessage")[0].params["thread_ts"] == root
    assert receipt["line"] == "Posted in #launch · 11:04"


async def test_the_words_go_as_slack_wants_them_and_nothing_is_added(rig):
    await rig.connected()
    m = await rig.dm("What do you think?")
    text = "Fish & chips <3 and a > b,\nsecond line"
    await rig.driver.perform("slack_reply", m["ref"], text)
    assert rig.fake.calls("chat.postMessage")[0].params["text"] == "Fish &amp; chips &lt;3 and a &gt; b,\nsecond line"


async def test_the_time_in_the_receipt_is_the_drivers_own_clock(rig, utc):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.clock.now += 3600 * 3 + 60 * 17
    receipt = await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert receipt["line"] == "Replied to Priya Shah in a direct message · 14:21"


async def test_a_permalink_that_cannot_be_had_does_not_turn_the_receipt_into_an_error(rig, utc):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.fail("chat.getPermalink", "message_not_found")
    receipt = await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert receipt == {"line": "Replied to Priya Shah in a direct message · 11:04"}
    rig.fake.fail("chat.getPermalink", "invalid_auth")
    assert "line" in await rig.driver.perform("slack_reply", m["ref"], "Hi again")


async def test_a_429_on_a_post_is_a_plain_error_and_nothing_is_retried(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.rate_limit("chat.postMessage", 30)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "rate_limited" and not isinstance(e.value, UnknownOutcome)
    assert len(rig.fake.calls("chat.postMessage")) == 1 and rig.fake.posted == [] and rig.sleeper.delays == []


async def test_a_refusal_is_a_plain_error_and_the_connection_stays_as_it_was(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.fail("chat.postMessage", "is_archived", times=1)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "refused" and not isinstance(e.value, UnknownOutcome)
    assert str(e.value) == "That channel is archived, so nothing can be posted in it."
    assert rig.driver.state == "ok"
    rig.fake.fail("chat.postMessage", "something_new_slack_invented", times=1)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "refused" and str(e.value) == "Slack would not post that."


async def test_a_post_that_cannot_make_a_connection_is_an_error_that_says_nothing_was_sent(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.sleeper.auto = False
    await rig.fake.stop()
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "service_down" and not isinstance(e.value, UnknownOutcome)
    assert "Nothing was sent" in str(e.value)
    await rig.fake.start()


async def test_a_connection_that_breaks_after_the_request_was_written_is_an_unknown_outcome(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.fail_after_write("chat.postMessage")
    with pytest.raises(UnknownOutcome) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "unknown_outcome"
    assert len(rig.fake.calls("chat.postMessage")) == 1 and rig.fake.posted == []


async def test_no_answer_in_time_is_an_unknown_outcome(rig, monkeypatch):
    await rig.connected()
    m = await rig.dm("Hello")
    monkeypatch.setattr(slack, "TIMEOUT", 0.2)
    gate = rig.fake.hold("chat.postMessage")
    with pytest.raises(UnknownOutcome):
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    gate.release()


@pytest.mark.parametrize("how, outcome, code", [
    (("status", 503), DriverError, "service_down"),
    (("status", 500), UnknownOutcome, "unknown_outcome"),
    (("status", 502), UnknownOutcome, "unknown_outcome"),
    (("error", "service_unavailable"), DriverError, "service_down"),
    (("error", "internal_error"), UnknownOutcome, "unknown_outcome"),
    (("error", "fatal_error"), UnknownOutcome, "unknown_outcome"),
])
async def test_slack_failing_on_its_side_is_told_apart_by_whether_it_may_have_gone(rig, how, outcome, code):
    await rig.connected()
    m = await rig.dm("Hello")
    if how[0] == "status":
        rig.fake.fail_status("chat.postMessage", how[1], times=1)
    else:
        rig.fake.fail("chat.postMessage", how[1], times=1)
    with pytest.raises(outcome) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == code
    assert isinstance(e.value, UnknownOutcome) == (outcome is UnknownOutcome)


async def test_a_token_slack_stops_accepting_at_a_press_is_an_auth_error_and_the_state_follows(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.fail("chat.postMessage", "token_revoked", times=1)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "auth" and str(e.value) == AUTH_SENTENCE
    assert rig.driver.state == "error" and rig.states[-1]["state"] == "error"
    assert not rig.driver.can_post()
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "auth" and len(rig.fake.calls("chat.postMessage")) == 1
    await until(lambda: not rig.fake.sockets, what="the socket to close")


async def test_a_missing_permission_at_a_press_names_it(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.missing_scope("chat.postMessage", "chat:write", times=1)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "refused" and "chat:write" in str(e.value)
    assert rig.driver.state == "error" and "chat:write" in rig.driver.note


async def test_an_approval_wall_at_a_press_is_blocked_but_a_post_that_is_limited_is_only_that_post(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.fail("chat.postMessage", "restricted_action", times=1)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "blocked" and str(e.value) == "Your workspace does not let you post there."
    assert rig.driver.state == "ok"
    rig.fake.fail("chat.postMessage", "app_approval", times=1)
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert e.value.code == "blocked" and rig.driver.state == "blocked"


@pytest.mark.parametrize("content", ["", "   \n", None, 5, ["hi"], "x" * 3001, "a\x00b", "tab\there", "bell\x07",
                                     "carriage\rreturn", "escape\x1b[0m", "del\x7f"])
async def test_a_reply_that_is_not_one_is_refused_before_anything_is_sent(rig, content):
    await rig.connected()
    m = await rig.dm("Hello")
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], content)
    assert e.value.code == "bad_request" and not isinstance(e.value, UnknownOutcome)
    assert rig.fake.calls("chat.postMessage") == []


async def test_a_reply_of_the_longest_size_with_newlines_is_posted(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    content = ("line\n" * 600)[:slack.MAX_REPLY]
    assert len(content) == 3000
    await rig.driver.perform("slack_reply", m["ref"], content)
    assert len(rig.fake.calls("chat.postMessage")) == 1


@pytest.mark.parametrize("target, code", [
    ("slack:T9OTHER/D0PRIYA/1789988000.000100", "not_found"),
    ("nonsense", "bad_request"), ("", "bad_request"), (None, "bad_request"),
    ("mail:abc/def", "bad_request"), ("slack:T0ACME/D0PRIYA", "bad_request"),
    ("slack:T0ACME/d0priya/1789988000.000100", "bad_request"),
    ("slack:T0ACME/D0PRIYA/not-a-time", "bad_request"),
])
async def test_a_target_that_is_not_a_message_of_this_workspace_is_refused(rig, target, code):
    await rig.connected()
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", target, "Hi")
    assert e.value.code == code
    assert rig.fake.calls("chat.postMessage") == []


async def test_only_a_reply_is_something_this_connection_does(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    for kind in ("task_create", "task_comment", "mail_send", ""):
        with pytest.raises(DriverError) as e:
            await rig.driver.perform(kind, m["ref"], "Hi")
        assert e.value.code == "refused"
    assert rig.fake.calls("chat.postMessage") == []


async def test_nothing_is_written_to_slack_unless_a_press_asks(rig):
    old_and_new(rig.fake)
    await rig.connected()
    await rig.fake.say("C0LAUNCH", PRIYA, f"<@{ME}> a mention")
    await rig.flush()
    assert rig.fake.writes() == [] and len(rig.messages) >= 4
    m = rig.messages[-1]
    await rig.driver.perform("slack_reply", m["ref"], "On it")
    assert [r.method for r in rig.fake.writes()] == ["chat.postMessage"]


async def test_a_reply_posted_through_slack_comes_back_as_the_persons_own_and_is_nothing(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    await rig.driver.perform("slack_reply", m["ref"], "Hi")
    await rig.flush()
    assert [x["text"] for x in rig.messages] == ["Hello"]


async def test_owns_is_a_reply_in_this_workspace(rig):
    assert not rig.driver.owns("slack_reply", "slack:T0ACME/D0PRIYA/1789988000.000100")     # not known yet
    await rig.connected()
    assert rig.driver.owns("slack_reply", "slack:T0ACME/D0PRIYA/1789988000.000100")
    assert not rig.driver.owns("slack_reply", "slack:T9OTHER/D0PRIYA/1789988000.000100")
    assert not rig.driver.owns("task_create", "slack:T0ACME/D0PRIYA/1789988000.000100")
    assert not rig.driver.owns("slack_reply", "linear")
    assert not rig.driver.owns("slack_reply", "")
    assert not rig.driver.owns("slack_reply", None)


# -- no token ever leaves --

async def test_no_token_appears_in_anything_that_leaves_the_driver(rig, capsys, caplog):
    caplog.set_level("DEBUG")
    seen: list[str] = []
    await rig.connected()
    ticket = rig.fake.ticket
    m = await rig.dm("Hello, <@U0ALEX>", channel="C0LAUNCH")
    await rig.dm("A direct one")
    seen.append(json.dumps(await rig.driver.thread(m["ref"])))

    async def attempt(call):
        try:
            seen.append(json.dumps(await call))
        except DriverError as e:
            seen.append(f"{e!s} {e!r} {e.code}")
            seen.append(repr(e.__cause__) + repr(e.__context__))

    rig.fake.rate_limit("chat.postMessage", 10)
    await attempt(rig.driver.perform("slack_reply", m["ref"], "one"))
    rig.fake.fail("chat.postMessage", "is_archived", times=1)
    await attempt(rig.driver.perform("slack_reply", m["ref"], "two"))
    rig.fake.fail_after_write("chat.postMessage")
    await attempt(rig.driver.perform("slack_reply", m["ref"], "three"))
    await attempt(rig.driver.perform("slack_reply", m["ref"], "four"))
    rig.sleeper.auto = False
    await rig.fake.stop()
    await attempt(rig.driver.perform("slack_reply", m["ref"], "five"))
    await attempt(rig.driver.thread(m["ref"]))
    await rig.fake.start()
    rig.fake.revoke_tokens()
    await attempt(rig.driver.perform("slack_reply", m["ref"], "six"))
    seen += [json.dumps(rig.pushes), rig.driver.note, repr(rig.driver.conn), json.dumps(rig.driver.steps()),
             capsys.readouterr().err, capsys.readouterr().out, caplog.text, rig.driver.reads()]
    await rig.driver.stop()
    blob = "\n".join(seen)
    for secret in (rig.fake.app_token, rig.fake.user_token, ticket):
        assert secret not in blob
        assert secret[-12:] not in blob
    assert len(blob) > 500


async def test_the_tokens_are_in_the_header_and_never_in_an_address(rig):
    await rig.connected()
    for r in rig.fake.requests:
        assert r.token in (rig.fake.app_token, rig.fake.user_token)
        assert all(rig.fake.user_token not in str(v) and rig.fake.app_token not in str(v) for v in r.params.values())


async def test_what_slack_says_in_an_error_cannot_carry_a_token_into_a_sentence(rig):
    await rig.connected()
    m = await rig.dm("Hello")
    rig.fake.fail("chat.postMessage", rig.fake.user_token, times=1)      # a word with a token in it
    with pytest.raises(DriverError) as e:
        await rig.driver.perform("slack_reply", m["ref"], "Hi")
    assert rig.fake.user_token not in str(e.value) and e.value.code == "refused"


# -- stopping --

async def test_stop_leaves_nothing_running_and_nothing_open(rig):
    await rig.connected()
    await rig.dm("Hello")
    await rig.driver.stop()
    assert not rig.driver._tasks and rig.driver._sock is None and rig.driver._http is None
    await until(lambda: not rig.fake.sockets, what="the socket to close")
    await rig.driver.stop()                   # and twice is no harm


async def test_nothing_is_emitted_after_stop_returns_even_by_work_that_was_in_flight(rig):
    await rig.connected()
    gate = rig.fake.hold("users.info")
    await rig.fake.deliver(event(rig.fake, "D0MARCUS", MARCUS, "Half done when you stopped"))
    await until(lambda: gate.arrived == 1, what="the lookup to begin")
    count = len(rig.pushes)
    await rig.driver.stop()
    gate.release()
    await asyncio.sleep(0.05)
    rig.driver._emit({"push": "state", "state": "error", "note": "late"})
    rig.driver._deliver({"ref": "slack:T0ACME/D0PRIYA/1789988000.000100", "ts": 1.0})
    assert len(rig.pushes) == count


async def test_stop_before_start_and_stop_during_start_are_harmless(rig):
    await rig.driver.stop()
    gate = rig.fake.hold("auth.test")
    rig.store_tokens()
    start = asyncio.ensure_future(rig.driver.start())
    await until(lambda: gate.arrived == 1, what="the first call")
    await rig.driver.stop()
    gate.release()
    await asyncio.wait_for(start, 5)
    assert rig.fake.opened == 0 and not rig.driver._tasks


async def test_a_driver_that_was_stopped_does_not_take_new_tokens(rig):
    await rig.driver.start()
    await rig.driver.stop()
    rig.store_tokens()
    await rig.driver.secrets_changed()
    assert rig.fake.requests == [] and not rig.driver._tasks

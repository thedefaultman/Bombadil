"""agentd and the voice: the name and voice card, the welcome line, return greetings, the goodbye.

Real sockets, like test_agentd.py, and a clock the test moves so a morning, a coffee break and a week
away are all one fast test.
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from bombadil import agentd, apps, greet, paths, persona, providers, voice

FIRST_LINE = "Signed in to Claude. What should I call you?"


class Clock:
    """Epoch seconds for agentd, an aware datetime for the words, both moved by advance()."""

    def __init__(self, start: datetime):
        self.start, self.elapsed = start, 0.0

    def __call__(self) -> float:
        return self.start.timestamp() + self.elapsed

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.elapsed)

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds


@pytest.fixture
def clock(home, monkeypatch):
    """08:40 on 30 Sep 2026, a zone that is set, a live system off, and no updates file."""
    zone = home / "localtime"
    zone.symlink_to("/usr/share/zoneinfo/Europe/Berlin")
    monkeypatch.setenv("BOMBADIL_LOCALTIME", str(zone))
    monkeypatch.setenv("BOMBADIL_LIVE", "1")
    monkeypatch.setenv("BOMBADIL_UPDATES", str(home / "updates.json"))
    monkeypatch.setenv("BOMBADIL_GREET_DELAY", "0")
    monkeypatch.delenv("BOMBADIL_GREET_WINDOW", raising=False)
    monkeypatch.delenv("BOMBADIL_IDLE_SECONDS", raising=False)
    return Clock(datetime(2026, 9, 30, 8, 40, tzinfo=UTC))


def make(clock, provider=None) -> agentd.AgentD:
    d = agentd.AgentD(provider or providers.Fake("x"), agentd._NoSnapshots())
    d.voice = voice.Voice(d, clock=clock, now=clock.now)
    return d


class Client:
    """A connection that keeps everything it is sent, so a test can ask for a message in any order."""

    def __init__(self, r, w):
        self.r, self.w, self.msgs, self.used = r, w, [], set()
        self._pump = asyncio.create_task(self._read())

    async def _read(self):
        with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
            while line := await self.r.readline():
                self.msgs.append(json.loads(line))

    async def send(self, type_, **fields):
        self.w.write((json.dumps({"type": type_, **fields}) + "\n").encode())
        await self.w.drain()

    def _find(self, type_, fields):
        for i, m in enumerate(self.msgs):
            if i not in self.used and m.get("type") == type_ and all(m.get(k) == v for k, v in fields.items()):
                return i
        return None

    async def get(self, type_, timeout=3.0, **fields):
        """The first message not yet taken with this type and these fields."""
        end = asyncio.get_running_loop().time() + timeout
        while (i := self._find(type_, fields)) is None:
            if asyncio.get_running_loop().time() > end:
                raise AssertionError(f"no {type_} {fields} in {self.msgs}")
            await asyncio.sleep(0.01)
        self.used.add(i)
        return self.msgs[i]

    async def none(self, type_, wait=0.15, **fields):
        await asyncio.sleep(wait)
        assert self._find(type_, fields) is None, f"unexpected {type_}: {self.msgs}"

    def close(self):
        self.w.close()
        self._pump.cancel()


@contextlib.asynccontextmanager
async def running(d):
    d.socket_path.unlink(missing_ok=True)   # a daemon that ran before in this test left its socket behind
    server = asyncio.create_task(d.serve())
    clients = []
    for _ in range(100):
        if d.socket_path.exists():
            break
        await asyncio.sleep(0.02)

    async def connect() -> Client:
        c = Client(*await asyncio.open_unix_connection(str(d.socket_path), limit=1 << 24))
        clients.append(c)
        await c.get("status")
        return c

    try:
        yield connect
    finally:
        for c in clients:
            c.close()
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server


async def hello(connect, idle=False) -> Client:
    """A bar that said hello. The status round trip is how a test knows agentd has dealt with it: a
    connection is read in order."""
    c = await connect()
    await c.send("bar", idle=idle)
    await c.send("status")
    await c.get("status")
    return c


def rows():
    try:
        return [json.loads(line) for line in paths.turns_log().read_text().splitlines()]
    except OSError:
        return []


def boot_rows():
    return [r for r in rows() if r.get("action") == "boot"]


def known(name="Daniel", voice_="merry", greet_=True, day="2026-09-29"):
    """A user who finished the card on `day`."""
    persona.save(name, voice_, greet_)
    greet.save_ledger(greet.Ledger(setup_day=day))


def at_night(clock):
    clock.start = datetime(2026, 9, 30, 22, 15, tzinfo=UTC)


# -- the card --

@pytest.mark.asyncio
async def test_the_first_hello_asks_the_card_and_saves_the_defaults_at_once(clock):
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        ask = await bar.get("persona_ask")
        assert ask["line"] == "What should I call you?" and ask["current"] is False
        assert (ask["name"], ask["voice"]) == ("", "merry")
        assert [v["id"] for v in ask["voices"]] == ["merry", "plain", "quiet"]
        assert all("{n}" in v["card"] for v in ask["voices"] if v["id"] != "quiet")
        # Any way out of the card now leaves a valid setting, and the card is not asked again.
        assert persona.load() == persona.Persona("", "merry", True)
        assert greet.load_ledger().setup_day == "2026-09-30"
        assert paths.greeted_marker().exists()
        await bar.none("welcome")


@pytest.mark.asyncio
async def test_answering_saves_folds_every_card_tells_the_session_and_welcomes_in_the_new_voice(clock):
    d = make(clock)
    async with running(d) as connect:
        a, b = await hello(connect), await hello(connect)
        await a.get("persona_ask")
        await b.get("persona_ask")
        await a.send("persona", name="Dan", voice="plain")
        for bar in (a, b):
            assert await bar.get("persona") == {"type": "persona", "name": "Dan", "voice": "plain", "greet": True}
        welcome = await a.get("welcome")
        assert welcome["text"] == "Welcome, Dan." and welcome["first"] is True
        assert persona.load() == persona.Persona("Dan", "plain", True)
        # What a resumed session needs to hear, as one of the notes before its next turn.
        assert d.notes == ["The user now goes by Dan and wants the plain voice."]
        await b.none("welcome")
        assert boot_rows() == []   # said only once the bar showed it
        await a.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        assert [r["greeted"] for r in boot_rows()] == [True]
        assert greet.load_ledger().last_boot_day == "2026-09-30"


@pytest.mark.asyncio
async def test_daniels_own_line(clock):
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.get("persona_ask")
        await bar.send("persona", name="Daniel", voice="merry")
        assert (await bar.get("welcome"))["text"] == "Welcome, Daniel. Let's go for a walk!"
        # A changed name is a note for a running session even when the voice stays the default.
        assert d.notes == ["The user now goes by Daniel and wants the merry voice."]


@pytest.mark.asyncio
async def test_esc_folds_the_card_with_the_defaults_and_the_first_hello_has_no_name(clock):
    d = make(clock)
    async with running(d) as connect:
        a, b = await hello(connect), await hello(connect)
        await a.get("persona_ask")
        await a.send("persona_skip")
        for bar in (a, b):
            assert await bar.get("persona") == {"type": "persona", "name": "", "voice": "merry", "greet": True}
        assert (await a.get("welcome"))["text"] == "Welcome. Let's go for a walk!"
        assert d.notes == [] and persona.load() == persona.Persona()
        # Skipped once means never asked again, on this connection or a new one.
        c = await hello(connect)
        await c.none("persona_ask")
        await c.send("persona_skip")   # a stale skip is nothing
        await c.none("persona")


@pytest.mark.asyncio
async def test_a_bar_that_reconnects_while_the_card_is_up_gets_it_again(clock):
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        first = await bar.get("persona_ask")
        bar.close()
        await asyncio.sleep(0.1)
        again = await hello(connect)
        assert await again.get("persona_ask") == first
        await again.send("persona", name="Dan", voice="quiet")
        await again.get("persona")
        third = await hello(connect)
        await third.none("persona_ask")


@pytest.mark.asyncio
async def test_a_bar_that_says_hello_twice_is_asked_and_greeted_once(clock):
    d = make(clock)
    known()
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.send("bar", idle=False)
        first = await bar.get("welcome")
        await bar.none("welcome")   # the second hello found one outstanding
        assert first["text"] == "Good morning, Daniel. Where to today?"


@pytest.mark.parametrize("name", [
    "x" * 25, "a b c d", "!bang", "/etc", "Dan\nInjected", 'Dan"', "Dan; rm", "9lives", "Dan <b>",
    "Dan" + chr(0x202E), ["Dan"], 5, "a" * 40_000,
])
@pytest.mark.asyncio
async def test_a_bad_name_is_refused_to_its_sender_and_the_card_stays(clock, name):
    d = make(clock)
    async with running(d) as connect:
        a, b = await hello(connect), await hello(connect)
        await a.get("persona_ask")
        await b.get("persona_ask")
        await a.send("persona", name=name, voice="plain")
        err = await a.get("event", kind="error")
        assert err["text"] == voice.BAD_NAME and err["turn"] is None
        again = await a.get("persona_ask")   # the card comes back for the sender, with nothing echoed
        assert again["name"] == "" and again["current"] is False
        assert persona.load() == persona.Persona() and d.notes == []
        await a.none("welcome")
        await b.none("persona")
        await b.none("event", kind="error")
        assert d.voice.asking is not None
        # The card still works afterwards.
        await a.send("persona", name="Dan", voice="plain")
        await a.get("persona")


@pytest.mark.asyncio
async def test_a_bad_voice_is_refused_and_so_is_a_disk_that_cannot_save(clock, monkeypatch):
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.get("persona_ask")
        await bar.send("persona", name="Dan", voice="loud")
        assert (await bar.get("event", kind="error"))["text"] == voice.BAD_VOICE

        def broken(*a, **k):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(persona, "save", broken)
        await bar.send("persona", name="Dan", voice="plain")
        assert (await bar.get("event", kind="error"))["text"] == "Could not save that: Permission denied"
        assert d.voice.asking is not None and d.notes == []


@pytest.mark.parametrize("name", ["Zoë", "Mary-Jane O'Neil", "J. R. R.", "Élise", ""])
@pytest.mark.asyncio
async def test_real_names_pass_and_an_empty_name_is_no_name(clock, name):
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.get("persona_ask")
        await bar.send("persona", name=name, voice="merry")
        assert (await bar.get("persona"))["name"] == name
        assert persona.load().name == name


@pytest.mark.asyncio
async def test_the_answer_without_a_voice_keeps_the_saved_one_and_greeting_off_stays_off(clock):
    known("Dan", "plain", greet_=False)
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await d.ask_persona(current=True)
        await bar.get("persona_ask", current=True)
        await bar.send("persona", name="Dana")
        assert await bar.get("persona") == {"type": "persona", "name": "Dana", "voice": "plain", "greet": False}


@pytest.mark.asyncio
async def test_an_answer_while_a_turn_runs_blocks_nothing(clock):
    script = (
        "import json, sys, time\n"
        "sys.stdin.read()\n"
        "time.sleep(0.8)\n"
        "print(json.dumps({'type': 'result', 'result': 'done', 'session_id': 's1'}), flush=True)\n"
    )

    class Slow(providers.Claude):
        name = "claude"

        def __init__(self):
            super().__init__("x")

        @property
        def installed(self):
            return True

        def command(self, turn, workdir):
            return ["python3", "-c", script]

    d = make(clock, Slow())
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.get("persona_ask")
        await bar.send("prompt", text="tell me a joke")
        await bar.get("event", kind="turn_start")
        await bar.send("persona", name="Dan", voice="plain")
        await bar.get("persona")
        assert d.current is not None   # the turn is still running, and nothing waited for it
        await bar.none("welcome", wait=0.1)   # no hello over a running turn
        await bar.get("event", kind="turn_end", timeout=6)
        assert persona.load().name == "Dan"


# -- asking from outside (the sign-in) --

@pytest.mark.asyncio
async def test_sign_in_asks_with_its_own_line_and_only_while_there_is_no_persona(clock):
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.get("persona_ask")   # main has no sign-in state: the hello itself asked
        await bar.send("persona_skip")
        await bar.get("persona")
        assert await d.ask_persona(FIRST_LINE) is False   # there is a persona now
        await bar.none("persona_ask")
    d2 = make(clock)
    paths.persona_file().unlink()
    async with running(d2) as connect:
        bar = await connect()                   # connected, but has not said hello: nobody to ask
        assert await d2.ask_persona(FIRST_LINE) is True
        await bar.none("persona_ask")
        await bar.send("bar", idle=False)
        assert (await bar.get("persona_ask"))["line"] == FIRST_LINE   # and the hello gets it
        assert await d2.ask_persona(FIRST_LINE + "!") is True          # a newer line replaces it in place
        assert (await bar.get("persona_ask"))["line"] == FIRST_LINE + "!"


@pytest.mark.asyncio
async def test_the_card_waits_for_sign_in_a_chosen_provider_and_an_installed_cli(clock):
    class Missing(providers.Fake):
        @property
        def installed(self):
            return False

    d = make(clock)
    d.access = "checking"
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.none("persona_ask")
        d.access = "ready"
        await d.voice.on_ready(FIRST_LINE)
        assert (await bar.get("persona_ask"))["line"] == FIRST_LINE
    for nothing in (lambda x: setattr(x, "configured", False), lambda x: setattr(x, "provider", Missing("x"))):
        paths.persona_file().unlink(missing_ok=True)
        d = make(clock)
        nothing(d)
        async with running(d) as connect:
            bar = await hello(connect)
            await bar.none("persona_ask")
            await bar.none("welcome", wait=0.05)
        assert not persona.exists()


# -- the boot line --

@pytest.mark.asyncio
async def test_the_boot_line_counts_only_once_the_bar_showed_it(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        welcome = await bar.get("welcome")
        assert welcome["text"] == "Good morning, Daniel. Where to today?" and welcome["first"] is False
        assert isinstance(welcome["id"], int)
        await asyncio.sleep(0.1)
        assert not paths.greeted_marker().exists() and boot_rows() == []
        assert greet.load_ledger().last_boot_day == ""
        await bar.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        assert paths.greeted_marker().exists()
        assert [(r["kind"], r["prompt"], r["greeted"]) for r in boot_rows()] == [("local", "", True)]
        assert greet.load_ledger().last_boot_day == "2026-09-30"
        again = await hello(connect)
        await again.none("welcome")


@pytest.mark.asyncio
async def test_the_boot_line_waits_a_moment_and_nothing_waits_for_it(clock, monkeypatch):
    known()
    monkeypatch.setenv("BOMBADIL_GREET_DELAY", "0.4")
    d = make(clock)
    async with running(d) as connect:
        bar = await connect()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await bar.send("bar", idle=False)
        await bar.send("status")
        await bar.get("status")
        assert loop.time() - t0 < 0.3   # its reader was not held up
        await bar.none("welcome", wait=0.05)
        await bar.get("welcome")
        assert loop.time() - t0 >= 0.39


@pytest.mark.asyncio
async def test_a_bar_that_left_during_the_wait_is_not_greeted_and_the_next_one_is(clock, monkeypatch):
    known()
    monkeypatch.setenv("BOMBADIL_GREET_DELAY", "0.3")
    d = make(clock)
    async with running(d) as connect:
        gone = await connect()
        await gone.send("bar", idle=False)
        await asyncio.sleep(0.05)
        gone.close()
        await asyncio.sleep(0.4)
        assert not paths.greeted_marker().exists() and d.voice._sent == {}
        again = await hello(connect)
        await again.get("welcome")


@pytest.mark.asyncio
async def test_a_dropped_welcome_is_tried_again_at_the_next_hello(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        first = await bar.get("welcome")
        bar.close()   # it was dropped: nothing came back
        await asyncio.sleep(0.1)
        assert not paths.greeted_marker().exists()
        again = await hello(connect)
        second = await again.get("welcome")
        assert second["text"] == first["text"] and second["id"] != first["id"]


@pytest.mark.asyncio
async def test_restarting_agentd_never_replays_the_boot_line(clock):
    known()
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        await bar.send("welcomed", id=(await bar.get("welcome"))["id"])
        await asyncio.sleep(0.1)
    async with running(make(clock)) as connect:   # a new daemon, the same login: the marker is still there
        await (await hello(connect)).none("welcome")


@pytest.mark.asyncio
async def test_restarting_agentd_with_the_card_up_does_not_ask_again(clock):
    async with running(make(clock)) as connect:
        await (await hello(connect)).get("persona_ask")   # defaults saved, login marked
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        await bar.none("persona_ask")
        await bar.none("welcome")


@pytest.mark.asyncio
async def test_a_welcome_nobody_answered_is_tried_again_after_a_while_and_a_late_answer_still_counts(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        first = await bar.get("welcome")
        await bar.send("presence", idle=True)
        await asyncio.sleep(0.05)
        clock.advance(voice.ACK_WAIT + 1)   # the bar dropped it and is still connected
        await bar.send("presence", idle=False)
        second = await bar.get("welcome")
        assert second["text"] == first["text"] and second["id"] != first["id"]
        await bar.send("welcomed", id=first["id"])   # it had shown the first after all
        await asyncio.sleep(0.1)
        assert len(boot_rows()) == 1 and paths.greeted_marker().exists()
        await bar.send("welcomed", id=second["id"])   # and the retry is not counted on top
        await asyncio.sleep(0.1)
        assert len(boot_rows()) == 1


@pytest.mark.asyncio
async def test_a_bar_that_starts_while_the_user_is_away_greets_when_they_are_back(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect, idle=True)
        await bar.none("welcome")
        assert not paths.greeted_marker().exists()
        clock.advance(5 * 60)
        await bar.send("presence", idle=False)
        assert (await bar.get("welcome"))["text"].startswith("Good morning, Daniel.")


@pytest.mark.asyncio
async def test_presence_it_does_not_understand_is_nothing(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.send("welcomed", id=(await bar.get("welcome"))["id"])
        for bad in ("yes", 1, None, [True]):
            await bar.send("presence", idle=bad)
        await bar.send("presence")
        await asyncio.sleep(0.1)
        assert d.voice.bars[next(iter(d.voice.bars))].idle is False
        await bar.none("event", kind="error")


@pytest.mark.asyncio
async def test_a_login_that_went_unwelcomed_for_a_quarter_of_an_hour_stays_silent(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        clock.advance(16 * 60)
        await (await hello(connect)).none("welcome")
    d = make(clock)
    async with running(d) as connect:
        await (await hello(connect)).get("welcome")


@pytest.mark.asyncio
async def test_the_window_can_be_set(clock, monkeypatch):
    known()
    monkeypatch.setenv("BOMBADIL_GREET_WINDOW", "30")
    d = make(clock)
    async with running(d) as connect:
        clock.advance(31)
        await (await hello(connect)).none("welcome")


@pytest.mark.asyncio
@pytest.mark.parametrize("p", [("Dan", "quiet", True), ("Dan", "plain", False), ("Dan", "merry", False)])
async def test_nothing_to_say_is_a_boot_all_the_same(clock, p):
    known(*p)
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.none("welcome")
        assert paths.greeted_marker().exists()
        assert [r["greeted"] for r in boot_rows()] == [False]
        assert greet.load_ledger().last_boot_day == "2026-09-30"


@pytest.mark.asyncio
async def test_no_boot_line_while_a_turn_runs_and_one_when_the_bar_comes_back(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        d.current = 4   # a turn is running
        bar = await hello(connect)
        await bar.none("welcome")
        assert not paths.greeted_marker().exists()
        d.current = None
        again = await hello(connect)
        assert (await again.get("welcome"))["text"].startswith("Good morning")


@pytest.mark.asyncio
async def test_the_boot_line_that_did_not_land_comes_when_the_user_is_back(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        d.current = 4
        bar = await hello(connect)
        await bar.send("presence", idle=True)
        await asyncio.sleep(0.05)
        d.current = None
        clock.advance(6 * 60)
        await bar.send("presence", idle=False)
        assert (await bar.get("welcome"))["text"].startswith("Good morning")


@pytest.mark.asyncio
async def test_two_bars_one_boot_line(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        a, b = await hello(connect), await hello(connect)
        welcome = await a.get("welcome")
        await b.none("welcome")
        await a.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        await (await hello(connect)).none("welcome")
        assert len(boot_rows()) == 1


@pytest.mark.asyncio
async def test_a_welcomed_that_is_not_the_bars_own_counts_for_nothing(clock):
    known()
    d = make(clock)
    async with running(d) as connect:
        a, b = await hello(connect), await hello(connect)
        welcome = await a.get("welcome")
        await b.send("welcomed", id=welcome["id"])
        for bad in ("1", True, None, 0, 99, [1], {"id": 1}, 1.5):
            await a.send("welcomed", id=bad)
        await a.send("welcomed")
        await asyncio.sleep(0.15)
        assert not paths.greeted_marker().exists() and boot_rows() == []
        await a.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        assert paths.greeted_marker().exists()
        await a.send("welcomed", id=welcome["id"])   # and a second time is nothing
        await asyncio.sleep(0.1)
        assert len(boot_rows()) == 1


@pytest.mark.asyncio
async def test_a_client_that_never_said_bar_is_never_greeted_or_asked(clock):
    d = make(clock)
    async with running(d) as connect:
        ask = await connect()                                 # like `bombadil ask`
        await ask.send("presence", idle=True)
        clock.advance(3 * 3600)
        await ask.send("presence", idle=False)
        await ask.send("welcomed", id=1)
        await ask.none("persona_ask")
        await ask.none("welcome")
        await ask.none("event", kind="error")
        assert not persona.exists()


@pytest.mark.asyncio
async def test_the_boot_line_carries_the_updates_once(clock, home):
    known()
    (home / "updates.json").write_text(json.dumps({"count": 214, "security": 4}))
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        welcome = await bar.get("welcome")
        assert welcome["text"] == "Good morning, Daniel. 214 updates are waiting, 4 of them security fixes."
        await bar.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
    paths.greeted_marker().unlink()   # the next login, the same day and the same count
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        assert (await bar.get("welcome"))["text"] == "Welcome, Daniel. Where to today?"


@pytest.mark.asyncio
async def test_a_restart_that_cut_a_session_off_is_said_at_boot(clock):
    known()
    greet.save_ledger(greet.Ledger(setup_day="2026-09-29", last_boot_day="2026-09-30"))
    registry = paths.state_dir() / "dev" / "sessions.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"sessions": [
        {"title": "batch", "state": "asleep", "last": "Stopped by a restart", "since": 5,
         "progress": {"done": 14, "total": 20}},
        {"title": "builder", "state": "done", "since": clock() - 60},   # finished last login: not news at boot
    ]}))
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        assert (await bar.get("welcome"))["text"] == "Welcome, Daniel. batch was stopped by the restart at 14 of 20."


@pytest.mark.asyncio
async def test_a_corrupt_ledger_or_persona_file_is_no_reason_for_silence_or_a_crash(clock):
    known()
    paths.ledger_file().write_text("{not json")
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        welcome = await bar.get("welcome")
        assert welcome["text"] == "Good morning, Daniel. Let's go for a walk!"   # no setup day: day 0
        await bar.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        assert json.loads(paths.ledger_file().read_text())["last_boot_day"] == "2026-09-30"
    paths.greeted_marker().unlink()
    paths.persona_file().write_text("name = = =")
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        await bar.none("persona_ask")
        assert (await bar.get("welcome"))["text"] == "Welcome. Let's go for a walk!"   # defaults: no name


@pytest.mark.asyncio
async def test_the_first_boot_of_an_installed_system_says_so_once(clock, monkeypatch):
    monkeypatch.setenv("BOMBADIL_LIVE", "0")
    persona.save("Daniel", "merry")   # copied over by the installer; the ledger is not
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        welcome = await bar.get("welcome")
        assert welcome["text"] == "Welcome home, Daniel. Undo works from here."
        await bar.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        ledger = greet.load_ledger()
        assert "installed" in ledger.said and ledger.last_boot_day == "2026-09-30" and ledger.setup_day
    paths.greeted_marker().unlink()
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        assert "home" not in (await bar.get("welcome"))["text"]


@pytest.mark.asyncio
async def test_a_live_system_is_never_welcomed_home(clock):
    persona.save("Daniel", "merry")
    async with running(make(clock)) as connect:
        assert "home" not in (await (await hello(connect)).get("welcome"))["text"]


@pytest.mark.asyncio
async def test_the_card_on_an_installed_system_is_its_welcome(clock, monkeypatch):
    monkeypatch.setenv("BOMBADIL_LIVE", "0")
    async with running(make(clock)) as connect:
        bar = await hello(connect)
        await bar.get("persona_ask")
        await bar.send("persona", name="Daniel", voice="merry")
        await bar.send("welcomed", id=(await bar.get("welcome"))["id"])
        await asyncio.sleep(0.1)
        assert "installed" in greet.load_ledger().said   # so "Welcome home" does not come a day late


# -- coming back --

def turn_row(clock, ago, summary="Installed ffmpeg.", **extra):
    row = {"t": clock() - ago, "prompt": "install ffmpeg", "result": "", "ok": True, "stopped": False,
           "summary": summary, **extra}
    paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
    with paths.turns_log().open("a") as f:
        f.write(json.dumps(row) + "\n")


def greeted():
    paths.greeted_marker().parent.mkdir(parents=True, exist_ok=True)
    paths.greeted_marker().write_text("")


async def away(connect, clock, seconds):
    """A bar that said hello, went idle and is back `seconds` later (it says so when the test does)."""
    bar = await hello(connect)
    await bar.send("presence", idle=True)
    await asyncio.sleep(0.05)
    clock.advance(seconds)
    return bar


@pytest.mark.asyncio
async def test_back_after_a_break_with_news_says_what_happened_without_a_hello(clock):
    known()
    greeted()
    async with running(make(clock)) as connect:
        bar = await away(connect, clock, 40 * 60)
        turn_row(clock, 10 * 60)
        await bar.send("presence", idle=False)
        welcome = await bar.get("welcome")
        assert welcome["text"] == "While you were away: installed ffmpeg." and welcome["first"] is False
        assert greet.load_ledger().last_return_at == 0   # the bar has not said it showed it yet
        await bar.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        assert greet.load_ledger().last_return_at == clock()
        assert boot_rows() == []   # a return is not a boot
        # A second break an hour later with nothing new says nothing: the same news is said once.
        await bar.send("presence", idle=True)
        await asyncio.sleep(0.05)
        clock.advance(3600)
        await bar.send("presence", idle=False)
        await bar.none("welcome")


@pytest.mark.asyncio
async def test_back_after_the_night_says_hello_and_no_news_or_a_short_break_says_nothing(clock):
    known()
    greeted()
    async with running(make(clock)) as connect:
        bar = await away(connect, clock, 10 * 3600)
        await bar.send("presence", idle=False)   # nothing happened
        await bar.none("welcome")
        await bar.send("presence", idle=True)
        await asyncio.sleep(0.05)
        clock.advance(10 * 60)
        turn_row(clock, 400)
        await bar.send("presence", idle=False)   # news, but only ten minutes
        await bar.none("welcome")
        await bar.send("presence", idle=True)
        await asyncio.sleep(0.05)
        clock.advance(5 * 3600)
        turn_row(clock, 2 * 3600)
        await bar.send("presence", idle=False)
        assert (await bar.get("welcome"))["text"] == "Welcome back, Daniel. While you were away: installed ffmpeg."


@pytest.mark.asyncio
async def test_a_week_away_brings_the_updates_with_the_return(clock, home):
    known()
    greeted()
    (home / "updates.json").write_text(json.dumps({"count": 214, "security": 4}))
    async with running(make(clock)) as connect:
        bar = await away(connect, clock, 4 * 86400)
        await bar.send("presence", idle=False)
        assert (await bar.get("welcome"))["text"] == \
            "Welcome back, Daniel. 214 updates are waiting, 4 of them security fixes."


@pytest.mark.asyncio
async def test_a_failure_while_away_is_said_even_with_greeting_off(clock):
    known(greet_=False)
    greeted()
    async with running(make(clock)) as connect:
        bar = await away(connect, clock, 2 * 3600)
        turn_row(clock, 600, summary="", ok=False)
        await bar.send("presence", idle=False)
        assert (await bar.get("welcome"))["text"] == "A request failed."


@pytest.mark.asyncio
async def test_a_return_is_said_once_when_two_bars_come_back_together(clock):
    known()
    greeted()
    async with running(make(clock)) as connect:
        a, b = await away(connect, clock, 0), await away(connect, clock, 0)
        clock.advance(3600)
        turn_row(clock, 600)
        await a.send("presence", idle=False)
        welcome = await a.get("welcome")
        await b.send("presence", idle=False)
        await b.none("welcome")   # one is still out
        await a.send("welcomed", id=welcome["id"])
        await asyncio.sleep(0.1)
        await b.send("presence", idle=True)
        await asyncio.sleep(0.05)
        clock.advance(31 * 60)
        turn_row(clock, 60, "Made Passwords.")
        await b.send("presence", idle=False)
        # More than half an hour since the last return line, and news: this one is fine.
        assert (await b.get("welcome"))["text"] == "While you were away: made Passwords."


@pytest.mark.asyncio
async def test_no_return_line_over_a_running_turn_or_the_card(clock):
    known()
    greeted()
    async with running(make(clock)) as connect:
        d = None
    d = make(clock)
    async with running(d) as connect:
        bar = await away(connect, clock, 3600)
        turn_row(clock, 600)
        d.current = 3
        await bar.send("presence", idle=False)
        await bar.none("welcome")
        d.current = None
        await bar.send("presence", idle=True)
        await asyncio.sleep(0.05)
        clock.advance(3600)
        await d.ask_persona(current=True)
        await bar.get("persona_ask")
        await bar.send("presence", idle=False)
        await bar.none("welcome")


# -- the goodbye --

def shutdown_with(monkeypatch, d, ok=True, text="Shutting down."):
    ran = []
    monkeypatch.setattr(d.launcher, "run", lambda action: (ran.append(action.kind), (ok, text))[1])
    return ran


def sessions(*items):
    registry = paths.state_dir() / "dev" / "sessions.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"sessions": list(items)}))


BATCH = {"title": "batch", "state": "working", "alive": True, "progress": {"done": 14, "total": 20}}


async def shut_down(connect):
    bar = await connect()
    await bar.send("prompt", text="shut down")
    start = await bar.get("event", kind="local", phase="start")
    done = await bar.get("event", kind="local", phase="done")
    return bar, start, done


@pytest.mark.asyncio
@pytest.mark.parametrize("voice_, hour, session, text", [
    ("merry", 22, True, "Good night, Dan. batch stops at 14 of 20 and waits for you."),
    ("plain", 22, True, "Shutting down. batch stops at 14 of 20."),
    ("quiet", 22, True, "Shutting down."),
    ("merry", 9, False, "See you, Dan."),
    ("plain", 9, False, "Shutting down."),
    ("merry", 9, True, "See you, Dan. batch stops at 14 of 20 and waits for you."),
])
async def test_shutdown_says_goodbye_in_the_start_text_and_again_in_the_done_text(
        clock, monkeypatch, voice_, hour, session, text):
    known("Dan", voice_)
    greeted()
    clock.start = clock.start.replace(hour=hour)
    if session:
        sessions(BATCH, {"title": "idle one", "state": "idle", "alive": True})
    d = make(clock)
    ran = shutdown_with(monkeypatch, d)
    async with running(d) as connect:
        _, start, done = await shut_down(connect)
        assert start["text"] == text and done["text"] == text and done["ok"] is True
        assert ran == ["shutdown"]
    assert [r["result"] for r in rows() if r.get("action") == "shutdown"] == [text]


@pytest.mark.asyncio
async def test_a_failed_shutdown_says_why_and_a_restart_and_an_unknown_user_keep_their_words(clock, monkeypatch):
    known("Dan")
    d = make(clock)
    shutdown_with(monkeypatch, d, ok=False, text="Could not shut down: busy")
    async with running(d) as connect:
        _, start, done = await shut_down(connect)
        assert start["text"] == "See you, Dan." and done["text"] == "Could not shut down: busy"
        bar = await connect()
        await bar.send("prompt", text="restart")
        assert (await bar.get("event", kind="local", phase="start", action="restart"))["text"] == "Restarting"
    paths.persona_file().unlink()   # before anyone chose a voice
    d = make(clock)
    shutdown_with(monkeypatch, d)
    async with running(d) as connect:
        _, start, done = await shut_down(connect)
        assert start["text"] == "Shutting down" and done["text"] == "Shutting down."


@pytest.mark.asyncio
async def test_a_hostile_session_title_cannot_make_a_long_goodbye(clock):
    known("Dan")
    at_night(clock)
    sessions({"title": "x" * 500 + "\n" + "y" * 500, "state": "working", "alive": True,
              "progress": {"done": 1, "total": 2}}, BATCH, {"state": "working", "title": ""})
    d = make(clock)
    text = await d.voice.goodbye()
    assert len(text) <= 100 and "\n" not in text and text.startswith("Good night, Dan. ")


@pytest.mark.asyncio
async def test_the_drain_before_power_off_is_bounded_and_does_not_wait_for_nothing(clock):
    d = make(clock)
    loop = asyncio.get_running_loop()

    class Stuck:
        transport = None

    t0 = loop.time()
    await d.voice.flush(1.0)
    assert loop.time() - t0 < 0.1           # nobody connected: no wait
    d.clients[Stuck()] = queue = asyncio.Queue()
    queue.put_nowait(b"never read\n")      # a client that does not read
    t0 = loop.time()
    await d.voice.flush(0.3)
    assert 0.25 < loop.time() - t0 < 0.8
    queue.get_nowait()
    t0 = loop.time()
    await d.voice.flush(1.0)
    assert loop.time() - t0 < 0.1


@pytest.mark.asyncio
async def test_a_client_that_never_reads_cannot_hold_up_a_shutdown(clock, monkeypatch):
    known("Dan")
    greeted()

    class Stuck:
        transport = None

        def close(self):
            pass

    d = make(clock)
    ran = shutdown_with(monkeypatch, d)
    async with running(d) as connect:
        d.clients[Stuck()] = asyncio.Queue()   # its messages wait for ever
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        _, start, _ = await shut_down(connect)
        assert start["text"] == "See you, Dan." and ran == ["shutdown"]
        assert loop.time() - t0 < voice.DRAIN + 1.5


# -- the word "voice" --

@pytest.mark.asyncio
async def test_the_word_voice_reopens_the_card_with_what_is_saved(clock):
    known("Dan", "plain")
    greeted()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.send("prompt", text="voice")
        assert await bar.get("local") == {"type": "local", "action": "voice"}
        assert (await bar.get("event", kind="local", phase="start"))["text"] == "Opening the voice card"
        ask = await bar.get("persona_ask")
        assert (ask["current"], ask["name"], ask["voice"]) == (True, "Dan", "plain")
        assert ask["line"] == voice.CARD_LINE and [v["id"] for v in ask["voices"]] == ["merry", "plain", "quiet"]
        done = await bar.get("event", kind="local", phase="done")
        assert done["ok"] is True and done["text"] == "Pick a voice, or press Esc."
        assert d.turns == 0 and d.pending == []   # it is a word, never a turn
        assert [r["prompt"] for r in rows() if r.get("action") == "voice"] == ["voice"]
        await bar.send("persona", name="Dana", voice="quiet")
        assert (await bar.get("persona"))["voice"] == "quiet"
        await bar.none("welcome")   # changing is not a greeting
        assert d.notes == ["The user now goes by Dana and wants the quiet voice."]
        assert persona.load() == persona.Persona("Dana", "quiet", True)


@pytest.mark.asyncio
async def test_the_voice_card_goes_to_every_bar_and_the_first_answer_folds_the_other(clock):
    known("Dan")
    greeted()
    d = make(clock)
    async with running(d) as connect:
        a, b = await hello(connect), await hello(connect)
        await a.send("prompt", text="open voice")
        await a.get("persona_ask", current=True)
        await b.get("persona_ask", current=True)
        await b.send("persona", name="Dan", voice="quiet")
        assert (await a.get("persona"))["voice"] == "quiet"
        await b.none("welcome")


@pytest.mark.asyncio
async def test_the_voice_button_and_a_card_nobody_answered(clock):
    known("Dan")
    greeted()
    d = make(clock)
    async with running(d) as connect:
        bar = await hello(connect)
        await bar.send("local", action="voice")
        await bar.get("persona_ask", current=True)
        await bar.send("persona_skip")
        await bar.get("persona")
        await bar.none("welcome")
        assert persona.load().name == "Dan" and d.notes == []
        await bar.send("prompt", text="voice")
        await bar.get("persona_ask", current=True, timeout=1)
        bar.close()
        await asyncio.sleep(0.1)
        assert d.voice.asking is None   # a card asked for by word goes with its bar
        again = await hello(connect)
        await again.none("persona_ask")


@pytest.mark.asyncio
async def test_bombadil_ask_voice_gets_its_done_event_even_with_no_bar(clock):
    known("Dan")
    d = make(clock)
    async with running(d) as connect:
        ask = await connect()   # `bombadil ask voice`: a client that is not a bar
        await ask.send("prompt", text="voice")
        assert (await ask.get("event", kind="local", phase="done"))["text"] == "Pick a voice, or press Esc."
        assert d.voice.asking is None


# -- turns.jsonl --

class Scripted(providers.Claude):
    name = "claude"

    def __init__(self, *events):
        super().__init__("x")
        self.script = "import json\n" + "\n".join(f"print(json.dumps({e!r}), flush=True)" for e in events)

    @property
    def installed(self):
        return True

    def command(self, turn, workdir):
        return ["python3", "-c", self.script]


def tool(name, **input_):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": name,
                                                          "input": input_}]}}


RESULT = {"type": "result", "result": "done", "session_id": "s1"}


@pytest.mark.asyncio
async def test_a_turn_row_says_what_it_made_and_whether_it_changed_anything(clock):
    make_app = tool("mcp__bombadil-os__create_app", title="Passwords", qml="import QtQuick\nItem {}\n")
    for events, made, changed in (
        ([make_app, RESULT], ["Passwords"], True),
        ([make_app, make_app, RESULT], ["Passwords"], True),
        ([tool("Bash", command="ls"), RESULT], [], False),
        ([RESULT], [], False),
    ):
        paths.turns_log().unlink(missing_ok=True)
        apps_dir = paths.apps_dir()
        if apps_dir.exists():
            import shutil
            shutil.rmtree(apps_dir)
        d = make(clock, Scripted(*events))
        async with running(d) as connect:
            bar = await connect()
            await bar.send("prompt", text="make it")
            await bar.get("event", kind="turn_end", timeout=6)
        row = rows()[-1]
        assert (row["made"], row["changed"]) == (made, changed), events


@pytest.mark.asyncio
async def test_changing_an_app_that_exists_is_a_change_but_not_a_new_app(clock):
    apps.create("Passwords", "import QtQuick\nItem {}\n")
    d = make(clock, Scripted(tool("mcp__bombadil-os__create_app", title="Passwords", qml="Item {}"), RESULT))
    async with running(d) as connect:
        bar = await connect()
        await bar.send("prompt", text="change it")
        await bar.get("event", kind="turn_end", timeout=6)
    row = rows()[-1]
    assert row["made"] == [] and row["changed"] is True


@pytest.mark.asyncio
async def test_editing_persona_toml_is_no_change_to_the_system(clock):
    known("Dan")
    edit = tool("Edit", file_path=str(paths.persona_file()), old_string="merry", new_string="plain")
    d = make(clock, Scripted(edit, RESULT))
    async with running(d) as connect:
        bar = await connect()
        await bar.send("prompt", text="be less chatty")
        end = await bar.get("event", kind="turn_end", timeout=6)
        assert end["changed"] is False and end["summary"] == ""
        assert (await bar.get("event", kind="status", text="Changing how I talk to you"))["source"] == "step"
    assert rows()[-1]["changed"] is False

"""bombadil-connect over a real Unix socket: every op on the fake driver, the checks of `perform` one at a time,
what happens when a driver goes wrong, and that no secret leaves the service.

`Connect` starts a `Service` in this test's event loop, in a temp dir, and `Client` talks to its socket the way agentd
does (JSON lines, answers matched by "rid", pushes kept in order). A connection is served in order, so what has to
happen while another call waits goes over a second connection. The service's own clock and sleep are the test's where
time matters (the ring's age, the pause before a driver starts again). Nothing here touches /run/bombadil-connect or
/var/lib/bombadil-connect, and the process's own cgroup is not consulted: `procs.cgroup_of` is the test's.

What the tests hold the service to, in the order of the file: it starts, refuses peers outside its range and stops
cleanly; connections can be added, set up and removed; messages are cleaned, kept in a ring with two limits, and unread
is its own note; tasks are asked for and a driver that fails is skipped; `perform` checks everything in the doc's
order, calls the driver once, and remembers; a driver that fails to start is started again with a pause that grows and
one that is missing costs only its connection; and a secret is in no answer, push, log line or exception.
"""

import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

from bombadil import procs
from bombadil.connect import fake, protocol, service
from bombadil.connect.driver import Driver, DriverError, UnknownOutcome, fingerprint
from bombadil.connect.service import Service
from bombadil.connect.store import Store

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
CANARY = "canary-" + "k3" * 14
APP = "xa" + "pp-" + "-".join(["1", "A" * 10, "b2c3" * 6])        # built at run time: nothing here is a token
USER = "xo" + "xp-" + "-".join(["1" * 10, "2" * 10, "3" * 10, "a1b2c3" * 5])
TURN = Path("/sys/fs/cgroup/user.slice/bombadil-turn-7.scope")
NOON = 1_790_000_000.0                                              # a Wednesday in 2026


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


def line(req: dict) -> bytes:
    return (json.dumps(req, ensure_ascii=False) + "\n").encode()


class Err(Exception):
    def __init__(self, code, sentence):
        super().__init__(f"{code}: {sentence}")
        self.code, self.sentence = code, sentence


class Client:
    """One connection to connect.sock. Calls are matched to their answers by "rid", pushes are kept in order."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.pending: dict[str, asyncio.Future] = {}
        self.pushes: asyncio.Queue = asyncio.Queue()
        self.seen_lines: list[bytes] = []     # every line the service sent, for the canary
        self.n = 0
        self.closed = False
        self.task = asyncio.ensure_future(self._read())

    async def _read(self):
        while True:
            try:
                raw = await self.reader.readline()
            except (OSError, ValueError):
                break
            if not raw:
                break
            self.seen_lines.append(raw)
            msg = json.loads(raw)
            if "push" in msg:
                self.pushes.put_nowait(msg)
            else:
                fut = self.pending.pop(msg.get("rid"), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        self.closed = True
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("the service closed the connection"))

    async def ask(self, op, _timeout=10.0, **args) -> dict:
        self.n += 1
        rid = f"r{self.n}"
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        self.writer.write(line({**args, "op": op, "rid": rid}))
        return await asyncio.wait_for(fut, _timeout)

    async def call(self, op, _timeout=10.0, **args):
        answer = await self.ask(op, _timeout, **args)
        if not answer["ok"]:
            raise Err(answer["code"], answer["error"])
        return answer["result"]

    async def fails(self, op, code, **args) -> str:
        """The call is refused with this code; returns the sentence."""
        answer = await self.ask(op, **args)
        assert answer["ok"] is False, f"{op} {args} was not refused: {answer}"
        assert answer["code"] == code, answer
        assert answer["error"].strip() and "\n" not in answer["error"]
        return answer["error"]

    async def push(self, name=None, timeout=3.0, where=None) -> dict:
        """The next push (of that name, and for which `where` holds), skipping others."""
        end = time.monotonic() + timeout
        while True:
            msg = await asyncio.wait_for(self.pushes.get(), max(0.01, end - time.monotonic()))
            if (name is None or msg["push"] == name) and (where is None or where(msg)):
                return msg

    async def quiet(self, name, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                msg = await asyncio.wait_for(self.pushes.get(), max(0.01, end - time.monotonic()))
            except TimeoutError:
                return
            assert msg["push"] != name, msg

    def close(self):
        self.writer.close()


async def until(fn, timeout=5.0, every=0.01):
    """Wait for `fn()` (sync or async) to be truthy, and return what it returned."""
    end = time.monotonic() + timeout
    while True:
        got = fn()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        if time.monotonic() > end:
            raise AssertionError(f"waited {timeout}s for {getattr(fn, '__name__', fn)}")
        await asyncio.sleep(every)


class Connect:
    """A service and the clients opened on it."""

    def __init__(self, svc: Service):
        self.service = svc
        self.clients: list[Client] = []
        self.task: asyncio.Task | None = None

    @classmethod
    async def start(cls, home: Path, **kw) -> "Connect":
        kw.setdefault("engine", "fake")
        self = cls(Service(home / "state", home / "connect.sock", **kw))
        self.task = asyncio.ensure_future(self.service.serve())
        await until(lambda: self.service.socket_path.exists() or self.task.done())
        if self.task.done():
            self.task.result()
        return self

    async def client(self) -> Client:
        reader, writer = await asyncio.open_unix_connection(str(self.service.socket_path), limit=1 << 26)
        c = Client(reader, writer)
        self.clients.append(c)
        return c

    async def stop(self):
        for c in self.clients:
            c.close()
        self.service.stop()
        try:
            await asyncio.wait_for(self.task, 10)
        except (asyncio.CancelledError, TimeoutError):
            pass

    def live(self, cid: str):
        return self.service.lives[cid]

    def fake(self, cid: str):
        return self.service.lives[cid].driver


@pytest.fixture(autouse=True)
def places(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_CONNECT_UIDS", "0-65535")
    monkeypatch.setenv("BOMBADIL_CONNECT_SOCKET", str(tmp_path / "connect.sock"))
    monkeypatch.setenv("BOMBADIL_CONNECT_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BOMBADIL_CONNECT_FAKE_SIGNIN_S", "0.05")
    monkeypatch.delenv("BOMBADIL_CONNECT_ENGINE", raising=False)
    monkeypatch.setattr(service, "DRIVER_S", 0.5)
    monkeypatch.setattr(service, "STOP_S", 0.5)
    return tmp_path


@pytest_asyncio.fixture
async def started():
    """Every Connect a test starts, stopped at the end."""
    made: list[Connect] = []
    yield made
    for c in made:
        await c.stop()


@pytest_asyncio.fixture
async def conn(places, started) -> Connect:
    c = await Connect.start(places, clock=Clock())
    started.append(c)
    return c


@pytest.fixture
def clock(conn):
    return conn.service.clock


@pytest_asyncio.fixture
async def api(conn) -> Client:
    return await conn.client()


def in_a_turn(monkeypatch, value=TURN):
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: value)


def message(text="Hello", ts=NOON, ref=None, **more):
    ref = ref or f"slack:TW1/D0PRIYA/{ts:.6f}"
    return {"ref": ref, "source": "slack", "conversation": {"id": "D0PRIYA", "name": "Priya Shah", "kind": "dm"},
            "from": {"id": "U0PRIYA", "name": "Priya Shah"}, "text": text, "ts": ts, "reason": "direct message", **more}


async def inject(api, text="Hello", **kw):
    got = message(text, **kw)
    await api.call("fake_inject", message=got)
    return got["ref"]


async def press(api, kind, target, content, proposal, fp=None, **more):
    return await api.ask("perform", kind=kind, target=target, content=content, proposal=proposal,
                         fingerprint=fingerprint(kind, target, content) if fp is None else fp, **more)


LAUNCH = "slack:TW1/C0LAUNCH/"          # the ref of every message the fake made in #launch starts with this


async def a_launch_ref(api) -> str:
    rows = (await api.call("messages"))["messages"]
    return next(m["ref"] for m in rows if m["ref"].startswith(LAUNCH))


# -- drivers that do what a test says --

class Scripted(Driver):
    """A driver with knobs, each test's own subclass (see `scripted`)."""
    kind = "scripted"
    secret_rules = {"app_token": "xa" + "pp-", "user_token": "xo" + "xp-"}
    prefix = "scripted:"
    fail_starts = 0              # the first this many starts raise
    start_emits: list = []
    pause_start = 0.0
    changed_raises = False
    can = True
    known: list = []
    items: list = []
    messages_delay = 0.0
    perform_result: object = None
    perform_raises = None
    perform_delay = 0.0
    thread_raises = None
    thread_result: list = []
    kinds = ("slack_reply", "task_create", "task_comment")

    def __init__(self, conn, secrets, emit, clock):
        super().__init__(conn, secrets, emit, clock)
        type(self).instances.append(self)
        self.stopped = False
        self.performs: list[tuple] = []
        self.changes = 0
        self.gate = asyncio.Event()
        self.gate.set()
        self.entered = asyncio.Event()

    async def start(self):
        type(self).starts += 1
        if self.pause_start:
            await asyncio.sleep(self.pause_start)
        if type(self).starts <= self.fail_starts:
            raise RuntimeError("could not start " + CANARY)
        for push in self.start_emits:
            self.emit(push)

    async def stop(self):
        self.stopped = True

    async def secrets_changed(self):
        self.changes += 1
        if self.changed_raises:
            raise RuntimeError("cannot use " + (self.secrets.get("app_token") or ""))

    def owns(self, kind, target):
        return kind in self.kinds and target.startswith(self.prefix)

    def can_post(self):
        return self.can

    def reads(self):
        return "Things for the test."

    def steps(self):
        return [{"id": "go", "say": "Do the thing.", "open": "https://auth.acme.test/go"}]

    async def messages(self, since=0.0, limit=50):
        if self.messages_delay:
            await asyncio.sleep(self.messages_delay)
        return list(self.known)

    async def tasks(self, limit=30):
        if self.messages_delay:
            await asyncio.sleep(self.messages_delay)
        return list(self.items)

    async def thread(self, ref, limit=20):
        if self.thread_raises is not None:
            raise self.thread_raises
        return list(self.thread_result)

    async def perform(self, kind, target, content):
        self.entered.set()
        await self.gate.wait()
        if self.perform_delay:
            await asyncio.sleep(self.perform_delay)
        if self.perform_raises is not None:
            raise self.perform_raises
        self.performs.append((kind, target, content))
        return self.perform_result if self.perform_result is not None else {"line": "Done in the test"}


def scripted(**knobs):
    """A new driver class with these knobs: its instances, and how many times it was started, are its own."""
    return type("Scripted", (Scripted,), {"instances": [], "starts": 0, **knobs})


async def start_with(places, started, drivers=None, **kw) -> Connect:
    """A service with no fake engine whose connections are made from `drivers` ({kind: class})."""
    c = await Connect.start(places, engine="", registry=drivers, **kw)
    started.append(c)
    return c


async def add_scripted(c: Connect, api: Client, service_name="slack") -> dict:
    return await api.call("add_connection", service=service_name)


def seed(places, *rows):
    """Connections in the store before the service starts: (id, kind, service, state)."""
    s = Store(places / "state" / "connect.db")
    for n, (cid, kind, svc, state) in enumerate(rows):
        s.add_connection(cid, kind, svc, svc.title(), state, "", float(n))
    s.close()


# -- starting, peers, stopping --

async def test_it_starts_with_a_workspace_and_a_tool_already_connected_on_the_fake_engine(api):
    status = await api.call("status")
    assert status["state"] == "ok"
    first, second = status["connections"]
    assert (first["id"], first["name"], first["state"], first["kind"]) == ("slack:w1", "Acme", "ok", "slack")
    assert (second["id"], second["name"], second["state"], second["service"]) == ("mcp:linear", "Linear", "ok", "linear")
    assert (await api.call("connections"))["connections"] == status["connections"]


async def test_the_socket_is_open_to_everyone_and_the_state_is_private(conn):
    assert stat.S_IMODE(os.stat(conn.service.socket_path).st_mode) == 0o666
    assert stat.S_IMODE(os.stat(conn.service.state_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(conn.service.db_path).st_mode) == 0o600


async def test_a_peer_outside_the_uid_range_is_refused_and_closed(places, started):
    c = await Connect.start(places, uids=(1000, 2000))
    started.append(c)
    reader, writer = await asyncio.open_unix_connection(str(c.service.socket_path))
    answer = json.loads(await asyncio.wait_for(reader.readline(), 3))
    assert answer["ok"] is False and answer["code"] == "refused" and answer["error"].endswith(".")
    assert await asyncio.wait_for(reader.readline(), 3) == b""      # and it is closed
    writer.close()
    assert c.service.peers == set()


async def test_the_uid_range_comes_from_the_environment_and_a_bad_one_is_the_default(monkeypatch, capsys):
    assert service._uid_range("1000-59999") == (1000, 59999)
    assert service._uid_range(" 5 - 10 ") == (5, 10)
    assert service._uid_range(None) == service.UID_RANGE
    assert capsys.readouterr().err == ""
    for bad in ("", "x", "10-5", "1000", "1000-"):
        assert service._uid_range(bad) == service.UID_RANGE
    monkeypatch.setenv("BOMBADIL_CONNECT_UIDS", "7-9")
    assert Service("a", "b").uids == (7, 9)
    monkeypatch.delenv("BOMBADIL_CONNECT_UIDS")
    assert Service("a", "b").uids == (1000, 59999)


async def test_a_request_it_cannot_understand_is_a_sentence_and_the_connection_goes_on(api, conn):
    assert "cannot" in await api.fails("frobnicate", "bad_request")
    reader, writer = await asyncio.open_unix_connection(str(conn.service.socket_path))
    writer.write(b"this is not json\n[1, 2]\n" + b'{"op": 5}\n')
    answers = [json.loads(await asyncio.wait_for(reader.readline(), 3)) for _ in range(3)]
    assert all(a["ok"] is False and a["code"] == "bad_request" and a["id"] is None for a in answers)
    writer.write(line({"id": 1, "op": "status"}))
    assert json.loads(await asyncio.wait_for(reader.readline(), 3))["ok"] is True
    writer.close()


async def test_an_answer_carries_the_id_and_the_rid_of_its_request(conn):
    reader, writer = await asyncio.open_unix_connection(str(conn.service.socket_path))
    writer.write(line({"id": "mcp:linear", "op": "connections", "rid": "x9"}))
    writer.write(line({"id": 4, "op": "nothing", "rid": 12}))
    first = json.loads(await asyncio.wait_for(reader.readline(), 3))
    second = json.loads(await asyncio.wait_for(reader.readline(), 3))
    assert (first["id"], first["rid"], first["ok"]) == ("mcp:linear", "x9", True)
    assert (second["id"], second["rid"], second["ok"]) == (4, 12, False)
    writer.close()


async def test_a_request_that_is_too_long_is_refused_and_closed(conn):
    reader, writer = await asyncio.open_unix_connection(str(conn.service.socket_path), limit=1 << 26)
    writer.write(b'{"op": "status", "pad": "' + b"a" * ((1 << 20) + 10) + b'"}\n')
    answer = json.loads(await asyncio.wait_for(reader.readline(), 5))
    assert answer["code"] == "bad_request" and "too long" in answer["error"]
    assert await asyncio.wait_for(reader.readline(), 3) == b""
    writer.close()


async def test_a_second_service_on_the_same_state_does_not_start(conn, places):
    other = Service(places / "state", places / "other.sock", engine="fake")
    with pytest.raises(service.AlreadyRunning):
        await other.serve()
    third = Service(places / "state2", conn.service.socket_path, engine="fake")
    with pytest.raises(service.AlreadyRunning):
        await third.serve()
    assert conn.service.socket_path.exists()


async def test_stopping_closes_the_socket_and_stops_every_driver(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls, "mcp": cls})
    api = await c.client()
    await api.call("add_connection", service="slack")
    await api.call("add_connection", service="linear")
    await c.stop()
    assert not c.service.socket_path.exists()
    assert len(cls.instances) == 2 and all(d.stopped for d in cls.instances)
    assert c.task.done() and not c.service.peers


async def test_the_fake_workspace_is_not_made_again_once_the_person_has_removed_it(places, started):
    first = await Connect.start(places, clock=Clock())
    api = await first.client()
    await api.call("remove_connection", id="slack:w1")
    await first.stop()
    second = await Connect.start(places, clock=Clock())
    started.append(second)
    ids = [x["id"] for x in (await (await second.client()).call("connections"))["connections"]]
    assert ids == ["mcp:linear"]


async def test_fake_inject_exists_only_on_the_fake_engine(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    await api.call("add_connection", service="slack")
    assert "only for the fake engine" in await api.fails("fake_inject", "refused", message=message())
    assert cls.instances[0].known == [] and (await api.call("messages"))["messages"] == []


# -- connections --

async def test_a_connection_is_what_the_doc_says_it_is(api):
    slack, linear = (await api.call("connections"))["connections"]
    assert set(slack) == {"id", "kind", "service", "name", "state", "note", "reads", "can_post", "steps"}
    assert slack["can_post"] is True and slack["steps"] == [] and "open" not in slack and "task_fields" not in slack
    assert slack["reads"].endswith(".")
    assert [f["key"] for f in linear["task_fields"]["create"]] == ["title", "description", "due"]
    assert linear["task_fields"]["create"][0]["required"] is True
    assert [f["edit"] for f in linear["task_fields"]["create"]] == ["line", "text", "date"]


async def test_adding_slack_makes_a_new_connection_in_setup_with_its_first_step_to_open(api):
    first = await api.call("add_connection", service="slack")
    assert first["id"] == "slack:w2" and first["state"] == "setup" and first["kind"] == "slack"
    assert first["steps"][0]["open"].startswith("https://") and first["open"] == first["steps"][0]["open"]
    assert first["can_post"] is False and first["name"] == "Slack"
    again = await api.call("add_connection", service="slack")
    assert again["id"] == "slack:w3"
    assert [c["id"] for c in (await api.call("connections"))["connections"]] == [
        "slack:w1", "mcp:linear", "slack:w2", "slack:w3"]


async def test_adding_a_work_tool_makes_one_connection_and_a_second_add_returns_it(api):
    first = await api.call("add_connection", service="notion")
    assert first["id"] == "mcp:notion" and first["kind"] == "mcp" and first["name"] == "Notion"
    assert first["state"] == "signin" and first["open"].startswith("https://") and first["can_post"] is False
    again = await api.call("add_connection", service="notion")
    assert again["id"] == "mcp:notion"
    assert [c["id"] for c in (await api.call("connections"))["connections"]].count("mcp:notion") == 1
    linear = await api.call("add_connection", service="linear")      # already there from the start
    assert linear["id"] == "mcp:linear" and linear["state"] == "ok"


async def test_a_tool_that_is_allowed_becomes_ok_and_subscribers_hear_it(conn, api):
    watcher = await conn.client()
    await watcher.call("subscribe")
    await api.call("add_connection", service="jira")
    push = await watcher.push("connection", where=lambda m: m["connection"]["state"] == "ok")
    assert push["connection"]["id"] == "mcp:jira" and push["connection"]["steps"] == []
    assert (await api.call("connections"))["connections"][-1]["state"] == "ok"


async def test_a_service_that_is_not_one_is_refused(api):
    for bad in ("teams", "", None, 7, ["slack"]):
        await api.fails("add_connection", "bad_request", service=bad)
    await api.fails("add_connection", "bad_request")


async def test_there_is_a_limit_to_how_many_connections_are_kept(api, monkeypatch):
    monkeypatch.setattr(service, "MAX_CONNECTIONS", 3)
    await api.call("add_connection", service="slack")
    assert "Disconnect" in await api.fails("add_connection", "refused", service="slack")
    assert (await api.call("add_connection", service="linear"))["id"] == "mcp:linear"   # one that exists is not new


async def test_the_workspace_takes_its_name_when_both_tokens_are_in(conn, api):
    watcher = await conn.client()
    await watcher.call("subscribe")
    made = await api.call("add_connection", service="slack")
    cid = made["id"]
    assert await api.call("store_secret", connection=cid, name="app_token", value=APP) == {"stored": True}
    assert (await api.call("connections"))["connections"][-1]["state"] == "setup"
    assert await api.call("store_secret", connection=cid, name="user_token", value=USER) == {"stored": True}
    push = await watcher.push("connection", where=lambda m: m["connection"]["state"] == "ok")
    assert push["connection"]["id"] == cid and push["connection"]["name"] == "Acme"
    assert push["connection"]["can_post"] is True and push["connection"]["steps"] == []
    assert conn.service.store.connection(cid)["state"] == "ok" and conn.service.store.connection(cid)["name"] == "Acme"


async def test_a_connection_that_is_removed_loses_its_driver_secrets_seen_times_and_messages(places, started):
    cls = scripted(known=[])
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    watcher = await c.client()
    await watcher.call("subscribe")
    made = await api.call("add_connection", service="slack")
    cid = made["id"]
    await api.call("store_secret", connection=cid, name="app_token", value=APP)
    cls.instances[0].emit({"push": "message", "message": message("Hi", ref="slack:TX/D0/1.5", ts=NOON)})
    other = await api.call("add_connection", service="slack")
    c.service.store.mark_seen(cid, "slack:TX/D0", NOON)
    c.service.seen = c.service.store.seen_times()
    assert len((await api.call("messages"))["messages"]) == 1
    await api.call("remove_connection", id=cid)
    assert cls.instances[0].stopped
    assert [x["id"] for x in (await api.call("connections"))["connections"]] == [other["id"]]
    assert (await api.call("messages"))["messages"] == []
    assert c.service.store.secrets(cid).names() == [] and c.service.store.seen_times() == {}
    gone = await watcher.push("connection", where=lambda m: m["connection"]["id"] == cid
                              and m["connection"]["state"] == "off")
    assert gone["connection"]["note"]
    await api.fails("remove_connection", "not_found", id=cid)
    await api.fails("remove_connection", "bad_request", id="../../etc")
    await api.fails("remove_connection", "bad_request")


async def test_a_removed_connection_is_gone_after_a_restart(places, started):
    first = await Connect.start(places, clock=Clock())
    api = await first.client()
    await api.call("remove_connection", id="mcp:linear")
    await first.stop()
    second = await Connect.start(places, clock=Clock())
    started.append(second)
    ids = [x["id"] for x in (await (await second.client()).call("connections"))["connections"]]
    assert ids == ["slack:w1"]


async def test_what_a_person_alone_may_do_is_refused_to_a_process_in_an_agents_turn(conn, api, monkeypatch):
    other = await conn.client()
    in_a_turn(monkeypatch)
    for op, args in (("add_connection", {"service": "slack"}), ("remove_connection", {"id": "slack:w1"}),
                     ("store_secret", {"connection": "slack:w1", "name": "app_token", "value": APP}),
                     ("seen", {"refs": []})):
        assert "agent's turn" in await other.fails(op, "refused", **args)
    assert [c["id"] for c in (await other.call("connections"))["connections"]] == ["slack:w1", "mcp:linear"]
    assert (await other.call("messages"))["messages"]      # reading is what the agent's tools are for


async def test_an_agent_cannot_hold_many_connections(places, started, monkeypatch):
    in_a_turn(monkeypatch)
    monkeypatch.setattr(service, "MAX_SCOPED_CLIENTS", 2)
    c = await Connect.start(places, clock=Clock())
    started.append(c)
    mine = [await c.client(), await c.client()]
    for each in mine:
        await each.call("status")
    third = await c.client()
    with pytest.raises((ConnectionError, asyncio.TimeoutError)):
        await third.ask("status", 1.0)
    assert (await mine[0].call("status"))["state"] == "ok"


# -- messages, and the fake's own --

async def test_messages_come_newest_first_and_are_what_the_fake_workspace_has(api):
    got = await api.call("messages")
    assert got["skipped"] == []
    rows = got["messages"]
    assert [m["from"]["name"] for m in rows] == ["Priya Shah", "Marcus Webb", "Priya Shah"]
    assert [m["ts"] for m in rows] == sorted((m["ts"] for m in rows), reverse=True)
    dm, mention, reply = rows[2], rows[1], rows[0]
    assert dm["conversation"]["kind"] == "dm" and dm["reason"] == "direct message"
    assert mention["mentions_me"] is True and mention["conversation"]["name"] == "#launch"
    assert reply["thread"] == mention["ref"]
    assert all(m["connection"] == "slack:w1" and m["unread"] is True for m in rows)
    assert set(rows[0]) == set(protocol.MESSAGE_KEYS)


async def test_messages_can_be_limited_by_time_and_by_connection(api, conn):
    rows = (await api.call("messages"))["messages"]
    assert len((await api.call("messages", limit=2))["messages"]) == 2
    assert [m["ref"] for m in (await api.call("messages", limit=1))["messages"]] == [rows[0]["ref"]]
    assert len((await api.call("messages", since=rows[1]["ts"]))["messages"]) == 1
    assert (await api.call("messages", connection="slack:w1"))["messages"] == rows
    assert (await api.call("messages", connection="mcp:linear"))["messages"] == []
    await api.fails("messages", "not_found", connection="slack:w9")
    await api.fails("messages", "bad_request", connection="nonsense")
    await api.fails("messages", "bad_request", limit="many")
    await api.fails("messages", "bad_request", since="yesterday")
    await api.fails("messages", "bad_request", unread="yes")
    assert len((await api.call("messages", limit=5000))["messages"]) == 3     # the limit is held to 100


async def test_a_message_that_is_injected_is_a_push_for_subscribers_and_is_in_the_ring(api, conn):
    watcher = await conn.client()
    await watcher.call("subscribe")
    ref = await inject(api, "Are we still on for Thursday?", ts=NOON + 5)
    push = await watcher.push("message")
    assert push["message"]["ref"] == ref and push["message"]["text"] == "Are we still on for Thursday?"
    assert push["message"]["unread"] is True and push["message"]["connection"] == "slack:w1"
    assert (await api.call("messages"))["messages"][0]["ref"] == ref
    await inject(api, "Are we still on for Thursday?", ts=NOON + 5)       # the same again says nothing new
    await watcher.quiet("message")


async def test_an_injected_message_with_less_than_a_whole_message_is_made_up_and_a_task_is_asked_for_later(api, conn):
    watcher = await conn.client()
    await watcher.call("subscribe")
    await api.call("fake_inject", message={"text": "Quick question"})
    push = await watcher.push("message")
    assert push["message"]["from"]["name"] == "Priya Shah" and push["message"]["conversation"]["kind"] == "dm"
    await api.call("fake_inject", item={"ref": "linear:LIN-77", "title": "Check the invoice", "why": "assigned to you"})
    task = await watcher.push("task")
    assert task["item"]["ref"] == "linear:LIN-77" and task["item"]["service"] == "linear"
    assert "linear:LIN-77" in [t["ref"] for t in (await api.call("tasks"))["items"]]
    await api.fails("fake_inject", "bad_request")
    await api.fails("fake_inject", "bad_request", message={}, item={})
    await api.fails("fake_inject", "no_connection", message=message(), connection="mcp:linear")
    await api.fails("fake_inject", "no_connection", message=message(), connection="slack:w9")


async def test_what_a_driver_hands_over_is_cleaned_before_anyone_sees_it(api, conn):
    watcher = await conn.client()
    await watcher.call("subscribe")
    hostile = "Hi\x00 there\x1b[31m ‮evil‬​⁦ done" + "x" * 5000
    await inject(api, hostile, ts=NOON + 9, **{"from": {"id": "U0X", "name": "Priya‮ Shah\x07"}, "extra": "no",
                                             "mentions_me": 1, "web_url": "javascript:alert(1)"})
    pushed = (await watcher.push("message"))["message"]
    listed = (await api.call("messages"))["messages"][0]
    for m in (pushed, listed):
        assert set(m) == set(protocol.MESSAGE_KEYS)
        assert not any(c in m["text"] for c in "\x00\x1b‮‬​⁦")
        assert m["text"].startswith("Hi there") and len(m["text"]) == protocol.MAX_TEXT and m["text"].endswith("…")
        assert m["from"]["name"] == "Priya Shah" and m["web_url"] is None and m["mentions_me"] is True


async def test_a_message_without_words_or_a_usable_ref_is_not_kept(api, conn):
    watcher = await conn.client()
    await watcher.call("subscribe")
    before = len((await api.call("messages"))["messages"])
    await api.call("fake_inject", message=message("", ts=NOON + 1))
    await api.call("fake_inject", message=message("​ \x00", ts=NOON + 2))
    await api.call("fake_inject", message={**message("no ref"), "ref": "no spaces allowed"})
    await watcher.quiet("message")
    assert len((await api.call("messages"))["messages"]) == before


# -- the ring --

async def test_the_ring_holds_at_most_five_hundred_messages(conn, api):
    for n in range(520):
        await inject(api, f"message {n}", ts=NOON + n, ref=f"slack:TW1/D0X/{n + 1}.000000")
    assert len(conn.service.ring) == 500
    kept = {m["text"] for m in conn.service.ring.values()}
    assert "message 519" in kept and "message 20" in kept and "message 19" not in kept and "message 0" not in kept
    assert "Legal just signed off. Can we lock the 14th for the launch?" not in kept   # the oldest go first


async def test_the_ring_drops_what_is_older_than_fourteen_days_by_the_messages_own_time(api, conn, clock):
    fresh = await inject(api, "thirteen days old", ts=clock() - 13 * 86400, ref="slack:TW1/D0X/1.000000")
    stale = await inject(api, "fifteen days old", ts=clock() - 15 * 86400, ref="slack:TW1/D0X/2.000000")
    assert fresh in conn.service.ring and stale not in conn.service.ring
    clock.now += 2 * 86400                                          # two days on, the first is past it too
    texts = {m["text"] for m in (await api.call("messages", limit=100))["messages"]}
    assert "thirteen days old" not in texts and fresh not in conn.service.ring
    assert "Legal just signed off. Can we lock the 14th for the launch?" in texts


async def test_a_ref_that_comes_again_replaces_the_message_in_the_ring(api, conn):
    watcher = await conn.client()
    await watcher.call("subscribe")
    ref = await inject(api, "first words", ts=NOON + 1, ref="slack:TW1/D0X/9.000000")
    await watcher.push("message")
    await inject(api, "words that were edited", ts=NOON + 1, ref=ref)
    assert (await watcher.push("message"))["message"]["text"] == "words that were edited"
    assert [m["text"] for m in (await api.call("messages", limit=100))["messages"] if m["ref"] == ref] == [
        "words that were edited"]


async def test_the_driver_is_asked_for_messages_only_when_the_ring_has_none_of_that_connection(places, started):
    old = message("From the driver", ts=NOON - 60, ref="slack:TX/D0/1.000000")
    cls = scripted(known=[old, message("Also the driver", ts=NOON - 30, ref="slack:TX/D0/2.000000")])
    c = await start_with(places, started, {"slack": cls}, clock=Clock())
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    await api.call("store_secret", connection=cid, name="app_token", value=APP)
    cls.instances[0].state = "ok"
    c.live(cid).record["state"] = "ok"
    assert [m["text"] for m in (await api.call("messages"))["messages"]] == ["Also the driver", "From the driver"]
    cls.instances[0].emit({"push": "message", "message": message("Pushed", ts=NOON, ref="slack:TX/D0/3.000000")})
    assert [m["text"] for m in (await api.call("messages"))["messages"]] == ["Pushed"]     # the ring has the say


async def test_a_driver_that_does_not_answer_is_skipped_with_its_state_and_the_rest_are_not_held_up(places, started):
    slow = scripted(messages_delay=5.0, known=[message("never", ref="slack:TX/D0/1.000000")])
    c = await start_with(places, started, {"slack": slow, "mcp": scripted(known=[])})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    begun = time.monotonic()
    got = await api.call("messages")
    assert time.monotonic() - begun < 3
    assert got["messages"] == [] and [s["connection"] for s in got["skipped"]] == [cid]
    assert got["skipped"][0]["state"] == "ok" and got["skipped"][0]["note"]


async def test_connections_that_are_not_ok_are_listed_as_skipped_with_their_own_words(api, conn):
    await api.call("add_connection", service="slack")
    skipped = (await api.call("messages"))["skipped"]
    assert [(s["connection"], s["state"]) for s in skipped] == [("slack:w2", "setup")]
    assert skipped[0]["note"].endswith(".")
    conn.live("slack:w1").record.update(state="error", note="Slack no longer accepts this connection. Set it up again.")
    skipped = (await api.call("messages"))["skipped"]
    assert [s["connection"] for s in skipped] == ["slack:w1", "slack:w2"]
    assert skipped[0]["note"].startswith("Slack no longer")
    assert (await api.call("messages"))["messages"]                     # what it had is still shown


# -- unread, and what the person has seen --

async def test_a_message_is_unread_until_the_person_has_seen_its_conversation_up_to_it(api, conn):
    rows = (await api.call("messages"))["messages"]
    mention, dm = rows[1], rows[2]
    assert [m["unread"] for m in rows] == [True, True, True]
    await api.call("seen", refs=[dm["ref"]])
    after = {m["ref"]: m["unread"] for m in (await api.call("messages"))["messages"]}
    assert after[dm["ref"]] is False and after[mention["ref"]] is True
    unread = (await api.call("messages", unread=True))["messages"]
    assert [m["ref"] for m in unread] == [rows[0]["ref"], mention["ref"]]
    await api.call("seen", refs=[mention["ref"]])
    assert [m["ref"] for m in (await api.call("messages", unread=True))["messages"]] == [rows[0]["ref"]]   # the reply is newer
    await api.call("seen", refs=[rows[0]["ref"]])
    assert [m["ref"] for m in (await api.call("messages", unread=True))["messages"]] == []
    assert [m["unread"] for m in (await api.call("messages"))["messages"]] == [False, False, False]


async def test_seen_goes_forward_and_never_back_and_is_per_conversation(api, conn):
    old = await inject(api, "older", ts=NOON - 100, ref="slack:TW1/D0Z/1.000000")
    newer = await inject(api, "newer", ts=NOON - 50, ref="slack:TW1/D0Z/2.000000")
    elsewhere = await inject(api, "elsewhere", ts=NOON - 40, ref="slack:TW1/D0Y/3.000000")
    await api.call("seen", refs=[newer])
    await api.call("seen", refs=[old])                                   # an older one does not move it back
    flags = {m["ref"]: m["unread"] for m in (await api.call("messages", limit=100))["messages"]}
    assert flags[old] is False and flags[newer] is False and flags[elsewhere] is True
    assert conn.service.store.seen_times()["slack:TW1/D0Z"] == NOON - 50
    await api.call("seen", refs=[old, newer, elsewhere])
    assert conn.service.store.seen_times() == {"slack:TW1/D0Z": NOON - 50, "slack:TW1/D0Y": NOON - 40}


async def test_a_message_that_arrives_after_the_person_looked_is_unread(api, conn):
    ref = await inject(api, "before", ts=NOON - 10, ref="slack:TW1/D0Z/1.000000")
    await api.call("seen", refs=[ref])
    watcher = await conn.client()
    await watcher.call("subscribe")
    await inject(api, "after", ts=NOON - 5, ref="slack:TW1/D0Z/2.000000")
    assert (await watcher.push("message"))["message"]["unread"] is True
    assert [m["text"] for m in (await api.call("messages", unread=True, limit=100))["messages"]
            if m["conversation"]["id"] == "D0PRIYA" and m["ref"].startswith("slack:TW1/D0Z")] == ["after"]


async def test_seen_is_remembered_by_a_restart_and_the_ring_is_not(places, started):
    first = await Connect.start(places, clock=Clock())
    api = await first.client()
    await api.call("fake_inject", message=message("only in memory", ts=NOON - 5, ref="slack:TW1/D0Q/1.000000"))
    await api.call("seen", refs=["slack:TW1/D0Q/1.000000"])
    await first.stop()
    second = await Connect.start(places, clock=Clock())
    started.append(second)
    api = await second.client()
    texts = [m["text"] for m in (await api.call("messages", limit=100))["messages"]]
    assert "only in memory" not in texts                                   # nothing of anyone's words was written
    assert second.service.store.seen_times() == {"slack:TW1/D0Q": NOON - 5}
    ref = await inject(api, "older than the look", ts=NOON - 6, ref="slack:TW1/D0Q/0.500000")
    assert {m["ref"]: m["unread"] for m in (await api.call("messages", limit=100))["messages"]}[ref] is False


async def test_a_message_the_ring_has_forgotten_is_seen_by_the_time_its_slack_ref_carries(places, started):
    ref = "slack:TX/D0K/1790000000.500000"
    cls = scripted(prefix="slack:TX/", kinds=("slack_reply",))
    c = await start_with(places, started, {"slack": cls}, clock=Clock())
    api = await c.client()
    await api.call("add_connection", service="slack")
    await api.call("seen", refs=[ref, "slack:TZ/D0K/1790000001.000000", "linear:LIN-4"])   # the last two are nobody's
    assert c.service.store.seen_times() == {"slack:TX/D0K": 1790000000.5}


async def test_seen_wants_a_list_of_refs(api):
    for bad in (None, "slack:T/C/1", [7], ["not a ref"], ["slack:T/C/" + str(n) for n in range(101)]):
        await api.fails("seen", "bad_request", refs=bad)
    await api.fails("seen", "bad_request")
    assert await api.call("seen", refs=[]) == {}
    assert await api.call("seen", refs=["slack:TW1/D0NONE/1.000000"]) == {}


# -- threads --

async def test_a_thread_comes_oldest_first_with_its_unread_marks(api):
    ref = await a_launch_ref(api)
    got = (await api.call("thread", ref=ref))["messages"]
    assert [m["from"]["name"] for m in got] == ["Marcus Webb", "Priya Shah"]
    assert got[0]["ts"] < got[1]["ts"] and all(m["unread"] for m in got)
    assert (await api.call("thread", ref=got[1]["ref"], limit=1))["messages"][0]["ref"] == got[1]["ref"]
    await api.call("seen", refs=[got[1]["ref"]])
    assert not any(m["unread"] for m in (await api.call("thread", ref=ref))["messages"])


async def test_a_thread_of_a_message_nobody_has_is_not_found(api):
    await api.fails("thread", "not_found", ref="slack:TW1/C0NONE/1.000000")
    await api.fails("thread", "not_found", ref="slack:TZZ/C0LAUNCH/1.000000")
    await api.fails("thread", "bad_request", ref="nonsense")
    await api.fails("thread", "bad_request")
    await api.fails("thread", "bad_request", ref=await a_launch_ref(api), limit=[1])


async def test_what_a_driver_says_when_it_cannot_do_a_thread_is_passed_on_in_its_code(places, started):
    cls = scripted(thread_raises=DriverError("Slack is slowing me down. Try again in a minute.", "rate_limited"))
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    await api.call("add_connection", service="slack")
    c.live("slack:w1").record["state"] = "ok"
    await api.fails("thread", "not_found", ref="slack:TX/D0/1.000000")          # nobody has it yet
    c.service.ring["slack:TX/D0/1.000000"] = {**message(ref="slack:TX/D0/1.000000"), "connection": "slack:w1"}
    assert "slowing me down" in await api.fails("thread", "rate_limited", ref="slack:TX/D0/1.000000")
    cls.instances[0].thread_raises = RuntimeError("something went wrong with " + CANARY)
    sentence = await api.fails("thread", "service_down", ref="slack:TX/D0/1.000000")
    assert sentence == "Slack did not answer." and CANARY not in sentence
    cls.instances[0].thread_raises = None
    cls.instances[0].thread_result = [message("second", ts=NOON, ref="slack:TX/D0/2.000000"),
                                      message("first", ts=NOON - 5, ref="slack:TX/D0/1.000000"), "junk"]
    got = await api.call("thread", ref="slack:TX/D0/1.000000")
    assert [m["text"] for m in got["messages"]] == ["first", "second"]
    assert all(m["connection"] == "slack:w1" for m in got["messages"])


# -- tasks --

async def test_tasks_are_asked_of_the_drivers_each_time_and_slack_has_none(api, conn):
    got = await api.call("tasks")
    assert got["skipped"] == []
    assert [t["ref"] for t in got["items"]] == ["linear:LIN-41", "linear:LIN-40"]
    first = got["items"][0]
    assert set(first) == set(protocol.TASK_KEYS)
    assert (first["title"], first["why"], first["status"], first["connection"]) == (
        "Review the launch checklist", "assigned to you", "In progress", "mcp:linear")
    assert len((await api.call("tasks", limit=1))["items"]) == 1
    assert [t["ref"] for t in (await api.call("tasks", service="linear"))["items"]] == ["linear:LIN-41", "linear:LIN-40"]
    assert (await api.call("tasks", service="notion"))["items"] == []
    conn.fake("mcp:linear")._tasks.append({"ref": "linear:LIN-50", "service": "linear", "title": "A new one"})
    assert len((await api.call("tasks"))["items"]) == 3                       # nothing was kept from the last time
    await api.fails("tasks", "bad_request", service="teams")
    await api.fails("tasks", "bad_request", limit="all")


async def test_a_tool_that_fails_or_is_slow_or_not_ready_is_skipped_with_its_state_and_note(places, started):
    class Failing(Scripted):
        instances: list = []
        starts = 0

        async def tasks(self, limit=30):
            raise RuntimeError("it broke " + CANARY)

    cls = {"mcp": Failing, "slack": scripted()}
    c = await start_with(places, started, cls)
    api = await c.client()
    for name in ("linear", "notion"):
        await api.call("add_connection", service=name)
    c.live("mcp:linear").record["state"] = "ok"
    c.live("mcp:notion").record.update(state="error", note="Notion no longer accepts this connection. Set it up again.")
    got = await api.call("tasks")
    assert got["items"] == []
    assert [(s["connection"], s["state"]) for s in got["skipped"]] == [("mcp:linear", "ok"), ("mcp:notion", "error")]
    assert got["skipped"][1]["note"].startswith("Notion no longer") and got["skipped"][0]["note"]
    assert not any(CANARY in ln.decode() for ln in api.seen_lines)


async def test_a_slow_tool_does_not_hold_up_the_others(places, started):
    slow = scripted(messages_delay=5.0, items=[{"ref": "linear:L-1", "service": "linear", "title": "late"}])
    c = await start_with(places, started, {"mcp": slow})
    api = await c.client()
    await api.call("add_connection", service="linear")
    c.live("mcp:linear").record["state"] = "ok"
    begun = time.monotonic()
    got = await api.call("tasks")
    assert time.monotonic() - begun < 3 and got["items"] == [] and got["skipped"][0]["connection"] == "mcp:linear"


# -- perform: each check on its own --

async def test_a_press_for_a_reply_posts_once_and_says_what_it_did(api, conn):
    ref = await a_launch_ref(api)
    answer = await press(api, "slack_reply", ref, "Yes, the 14th works.", "p1")
    assert answer["ok"] is True
    receipt = answer["result"]["receipt"]
    assert receipt["line"].startswith("Posted in #launch · ") and len(receipt["line"].split(" · ")[1]) == 5
    assert receipt["web"]["url"].startswith("https://")
    assert conn.fake("slack:w1").performed == [("slack_reply", ref, "Yes, the 14th works.")]
    assert conn.service.store.performed("p1")["outcome"] == "done"


async def test_a_reply_in_a_direct_message_says_who_it_went_to(api):
    ref = (await api.call("messages"))["messages"][2]["ref"]
    answer = await press(api, "slack_reply", ref, "On it.", "p1")
    assert answer["result"]["receipt"]["line"].startswith("Replied to Priya in a direct message · ")


async def test_a_task_is_created_and_commented_on_with_the_fields_of_its_card(api, conn):
    fields = {"title": "Send the invoice", "due": "2026-10-09"}
    answer = await press(api, "task_create", "linear", fields, "p1")
    assert answer["result"]["receipt"]["line"] == "Created LIN-43 in Linear"
    assert answer["result"]["receipt"]["web"]["name"] == "Linear"
    again = await press(api, "task_create", "linear", {"title": "Another"}, "p2")
    assert again["result"]["receipt"]["line"] == "Created LIN-44 in Linear"
    comment = await press(api, "task_comment", "linear:LIN-41", {"text": "Done on my side."}, "p3")
    assert comment["result"]["receipt"]["line"] == "Commented on LIN-41 in Linear"
    assert [p[0] for p in conn.fake("mcp:linear").performed] == ["task_create", "task_create", "task_comment"]


async def test_a_press_whose_fingerprint_is_not_the_one_worked_out_again_is_changed_and_does_nothing(api, conn):
    ref = await a_launch_ref(api)
    shown = fingerprint("slack_reply", ref, "Yes, the 14th works.")
    for fp in (shown, "0" * 64, "", "x"):
        got = await press(api, "slack_reply", ref, "Yes, the 15th works.", "p1", fp=fp)
        assert got["ok"] is False and got["code"] == "changed" and got["error"].endswith("Nothing was done.")
    for field, value in (("target", ref + "0"), ("kind", "task_create")):
        args = {"kind": "slack_reply", "target": ref, "content": "hello", "proposal": "p1",
                "fingerprint": fingerprint("slack_reply", ref, "hello")}
        args[field] = value
        got = await api.ask("perform", **args)
        assert got["code"] in ("changed",), got
    assert conn.fake("slack:w1").performed == [] and conn.service.store.performed("p1") is None


async def test_a_kind_that_is_not_one_of_the_kinds_is_refused_even_with_the_right_fingerprint(api, conn):
    ref = await a_launch_ref(api)
    got = await press(api, "delete_channel", ref, "hello", "p1")
    assert got["ok"] is False and got["code"] == "refused" and "not something I do" in got["error"]
    assert conn.fake("slack:w1").performed == []


async def test_a_press_nobody_owns_is_no_connection(api):
    for kind, target in (("slack_reply", "slack:TOTHER/C1/1.000000"), ("task_create", "notion"),
                         ("task_comment", "jira:J-1")):
        got = await press(api, kind, target, "hello" if kind == "slack_reply" else {"text": "x"}, "p1")
        assert got["ok"] is False and got["code"] == "no_connection", got


async def test_a_press_for_a_connection_that_is_not_ready_says_why_by_its_state(api, conn):
    ref = await a_launch_ref(api)
    record = conn.live("slack:w1").record
    for state, note, code in (("blocked", "Your administrator has to approve the app.", "blocked"),
                              ("error", "Slack no longer accepts this connection. Set it up again.", "service_down"),
                              ("setup", "", "no_connection"), ("signin", "", "no_connection"), ("off", "", "no_connection")):
        record.update(state=state, note=note)
        got = await press(api, "slack_reply", ref, "hello", "p1")
        assert got["ok"] is False and got["code"] == code and got["error"].endswith("Nothing was done."), got
        if note:
            assert got["error"].startswith(note)
    assert conn.fake("slack:w1").performed == [] and conn.service.store.performed("p1") is None


async def test_a_connection_that_only_reads_is_refused(api, conn):
    ref = await a_launch_ref(api)
    conn.fake("slack:w1").can_post = lambda: False
    got = await press(api, "slack_reply", ref, "hello", "p1")
    assert got["ok"] is False and got["code"] == "refused" and "only reads" in got["error"]
    assert (await api.call("connections"))["connections"][0]["can_post"] is False


async def test_a_press_that_two_connections_could_do_is_not_guessed_at(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    for _ in range(2):
        cid = (await api.call("add_connection", service="slack"))["id"]
        c.live(cid).record["state"] = "ok"
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert got["code"] == "refused" and "more than one" in got["error"].lower()
    c.live("slack:w2").record["state"] = "error"                         # now only one qualifies
    assert (await press(api, "slack_reply", "scripted:1", "hello", "p1"))["ok"] is True
    assert [len(d.performs) for d in cls.instances] == [1, 0]


async def test_a_proposal_id_that_is_not_one_is_refused(api, conn):
    ref = await a_launch_ref(api)
    for proposal in ("", "has space", "a" * 41, None, 7, "p/1"):
        got = await press(api, "slack_reply", ref, "hello", proposal)
        assert got["ok"] is False and got["code"] == "bad_request", (proposal, got)
    assert conn.fake("slack:w1").performed == []


async def test_a_press_that_is_missing_something_is_a_bad_request(api):
    for args in ({}, {"kind": "slack_reply"}, {"kind": "slack_reply", "target": "x", "content": 5, "fingerprint": "f"},
                 {"kind": "slack_reply", "target": "x", "content": ["a"], "fingerprint": "f"},
                 {"kind": "slack_reply", "target": "", "content": "a", "fingerprint": "f"},
                 {"kind": "slack_reply", "target": "x", "content": "a"}):
        await api.fails("perform", "bad_request", proposal="p1", **args)


async def test_a_second_press_of_the_same_proposal_is_already_and_does_nothing(api, conn):
    ref = await a_launch_ref(api)
    assert (await press(api, "slack_reply", ref, "Yes.", "p1"))["ok"] is True
    again = await press(api, "slack_reply", ref, "Yes.", "p1")
    assert again["ok"] is False and again["code"] == "already" and "already went" in again["error"]
    other = await press(api, "slack_reply", ref, "A different text.", "p1")
    assert other["code"] == "already"                                             # whatever it says: that press is gone
    assert len(conn.fake("slack:w1").performed) == 1
    assert (await press(api, "slack_reply", ref, "Yes.", "p2"))["ok"] is True


async def test_a_restarted_service_remembers_which_presses_were_performed(places, started):
    first = await Connect.start(places, clock=Clock())
    api = await first.client()
    ref = await a_launch_ref(api)
    assert (await press(api, "slack_reply", ref, "Yes.", "p1"))["ok"] is True
    await first.stop()
    second = await Connect.start(places, clock=Clock())
    started.append(second)
    api = await second.client()
    ref = await a_launch_ref(api)
    got = await press(api, "slack_reply", ref, "Yes.", "p1")
    assert got["code"] == "already"
    assert second.fake("slack:w1").performed == []
    assert (await press(api, "slack_reply", ref, "Yes.", "p9"))["ok"] is True


async def test_two_presses_of_one_proposal_at_once_are_one_act_and_a_busy(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls})
    first, second = await c.client(), await c.client()
    cid = (await first.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    driver = cls.instances[0]
    driver.gate.clear()
    running = asyncio.ensure_future(press(first, "slack_reply", "scripted:1", "hello", "p1"))
    await asyncio.wait_for(driver.entered.wait(), 3)
    refused = await press(second, "slack_reply", "scripted:1", "hello", "p1")
    assert refused["ok"] is False and refused["code"] == "busy"
    other = asyncio.ensure_future(press(second, "slack_reply", "scripted:1", "hello", "p2"))   # another press is its own
    driver.gate.set()
    assert (await running)["ok"] is True and (await other)["ok"] is True
    assert (await press(second, "slack_reply", "scripted:1", "hello", "p1"))["code"] == "already"
    assert [p[2] for p in driver.performs].count("hello") == 2


async def test_two_presses_that_arrive_together_never_call_the_driver_twice(places, started):
    cls = scripted(perform_delay=0.05)
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    clients = [await c.client() for _ in range(5)]
    answers = await asyncio.gather(*(press(each, "slack_reply", "scripted:1", "hello", "p1") for each in clients))
    assert [a["ok"] for a in answers].count(True) == 1
    assert {a["code"] for a in answers if not a["ok"]} <= {"busy", "already"}
    assert len(cls.instances[0].performs) == 1


async def test_a_press_from_inside_an_agents_turn_is_refused_whatever_it_says(api, conn, monkeypatch):
    ref = await a_launch_ref(api)
    in_a_turn(monkeypatch)
    for kwargs in ({}, {"fp": "0" * 64}):                      # the right fingerprint and a wrong one
        got = await press(api, "slack_reply", ref, "hello", "p1", **kwargs)
        assert got["ok"] is False and got["code"] == "agent" and "agent's turn" in got["error"]
    assert (await api.ask("perform"))["code"] == "agent"
    assert conn.fake("slack:w1").performed == [] and conn.service.store.performed("p1") is None
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: None)   # and the same press from the person goes
    assert (await press(api, "slack_reply", ref, "hello", "p1"))["ok"] is True


async def test_a_peer_that_joins_an_agents_turn_after_it_connected_is_caught(api, conn, monkeypatch):
    ref = await a_launch_ref(api)
    assert (await api.call("status"))["state"] == "ok"           # it connected as the person
    in_a_turn(monkeypatch)
    assert (await press(api, "slack_reply", ref, "hello", "p1"))["code"] == "agent"


async def test_a_peer_that_cannot_be_told_is_refused_as_the_agents(api, conn, monkeypatch):
    ref = await a_launch_ref(api)

    def blind(pid):
        raise OSError("no /proc here")
    monkeypatch.setattr(procs, "cgroup_of", blind)
    assert (await press(api, "slack_reply", ref, "hello", "p1"))["code"] == "agent"
    assert conn.fake("slack:w1").performed == []


async def test_a_peer_that_has_gone_or_was_never_named_is_the_agents(conn):
    class Writer:
        transport = None

    peer = service.Peer(Writer(), 0, 0)
    assert conn.service._scoped(peer) is True                    # the kernel could not name it
    gone = service.Peer(Writer(), 2 ** 22 + 12345, 0)
    assert conn.service._scoped(gone) is True                    # nobody is at that number now
    here = service.Peer(Writer(), os.getpid(), 0)
    assert conn.service._scoped(here) is False


async def test_a_refusal_from_the_driver_is_passed_on_with_its_code_and_is_not_recorded(api, conn):
    ref = await a_launch_ref(api)
    got = await press(api, "slack_reply", ref, "Please [refuse] this", "p1")
    assert got["ok"] is False and got["code"] == "refused" and got["error"].endswith("Nothing was done.")
    assert conn.service.store.performed("p1") is None and conn.fake("slack:w1").performed == []
    assert (await press(api, "slack_reply", ref, "A better one", "p1"))["ok"] is True       # may be pressed again


async def test_a_code_from_a_driver_that_is_not_a_known_code_is_a_refusal(places, started):
    cls = scripted(perform_raises=DriverError("Slack said no.", "teapot"))
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert (got["code"], got["error"]) == ("refused", "Slack said no.")
    cls.perform_raises = DriverError("Slack is slowing me down. Try again in a minute.", "rate_limited")
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert got["code"] == "rate_limited" and c.service.store.performed("p1") is None


async def test_the_sentence_of_an_unknown_outcome_is_the_drivers_own(places, started):
    sentence = "Slack did not say whether that went. Look in #launch before you press again."
    cls = scripted(perform_raises=UnknownOutcome(sentence))
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert (got["code"], got["error"]) == ("unknown_outcome", sentence)
    assert c.service.store.performed("p1")["outcome"] == "unknown" and cls.instances[0].performs == []


async def test_an_outcome_that_is_not_known_is_recorded_answered_and_never_retried(api, conn):
    ref = await a_launch_ref(api)
    got = await press(api, "slack_reply", ref, "This one [unknown]", "p1")
    assert got["ok"] is False and got["code"] == "unknown_outcome" and "can't tell whether that went" in got["error"]
    assert conn.service.store.performed("p1")["outcome"] == "unknown" and conn.fake("slack:w1").performed == []
    again = await press(api, "slack_reply", ref, "This one [unknown]", "p1")
    assert again["code"] == "already" and "Look where it would have gone" in again["error"]


async def test_an_unknown_outcome_is_remembered_by_a_restart(places, started):
    first = await Connect.start(places, clock=Clock())
    api = await first.client()
    ref = await a_launch_ref(api)
    await press(api, "slack_reply", ref, "[unknown]", "p1")
    await first.stop()
    second = await Connect.start(places, clock=Clock())
    started.append(second)
    api = await second.client()
    got = await press(api, "slack_reply", await a_launch_ref(api), "[unknown]", "p1")
    assert got["code"] == "already" and second.service.store.performed("p1")["outcome"] == "unknown"


async def test_a_driver_that_breaks_after_it_was_asked_leaves_an_unknown_outcome_and_says_nothing_of_why(
        places, started, capsys):
    cls = scripted(perform_raises=RuntimeError("the connection to Slack broke " + CANARY))
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert got["code"] == "unknown_outcome" and CANARY not in json.dumps(got)
    assert c.service.store.performed("p1")["outcome"] == "unknown"
    assert CANARY not in capsys.readouterr().err


async def test_a_driver_that_takes_too_long_over_a_press_is_an_unknown_outcome(places, started, monkeypatch):
    monkeypatch.setattr(service, "PERFORM_S", 0.2)
    cls = scripted(perform_delay=5.0)
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert got["code"] == "unknown_outcome" and c.service.store.performed("p1")["outcome"] == "unknown"
    assert cls.instances[0].performs == []


async def test_a_press_is_written_down_before_the_driver_is_asked(places, started):
    seen = []

    class Looks(Scripted):
        instances: list = []
        starts = 0

        async def perform(self, kind, target, content):
            seen.append(Store(places / "state" / "connect.db").performed("p1"))
            return {"line": "Done."}

    c = await start_with(places, started, {"slack": Looks})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    assert (await press(api, "slack_reply", "scripted:1", "hello", "p1"))["ok"] is True
    assert seen[0]["outcome"] == "unknown"                                         # while it was going
    assert c.service.store.performed("p1")["outcome"] == "done"                    # and when it had gone


async def test_a_service_that_stops_in_the_middle_of_a_press_remembers_it_may_have_gone(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    driver = cls.instances[0]
    driver.gate.clear()
    asyncio.ensure_future(press(api, "slack_reply", "scripted:1", "hello", "p1"))
    await asyncio.wait_for(driver.entered.wait(), 3)
    await c.stop()
    again = Store(places / "state" / "connect.db")
    try:
        assert again.performed("p1")["outcome"] == "unknown"
    finally:
        again.close()


async def test_a_receipt_is_one_plain_line_and_a_page_only_when_it_is_one(places, started):
    cls = scripted(perform_result={"line": "Posted\nin #launch ‮· 11:04", "web": {"name": "Slack", "url": "file:///x"}})
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    c.live(cid).record["state"] = "ok"
    got = await press(api, "slack_reply", "scripted:1", "hello", "p1")
    assert got["result"] == {"receipt": {"line": "Posted in #launch · 11:04"}}
    cls.perform_result = "just a string"
    assert (await press(api, "slack_reply", "scripted:1", "hello", "p2"))["result"] == {"receipt": {"line": "Done."}}
    cls.perform_result = {"line": "Posted", "web": {"name": "Slack", "url": "https://acme.test/x"}}
    assert (await press(api, "slack_reply", "scripted:1", "hello", "p3"))["result"]["receipt"]["web"] == {
        "name": "Slack", "url": "https://acme.test/x"}


# -- secrets --

async def test_a_secret_is_stored_for_the_driver_and_the_answer_is_only_that_it_was(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    answer = await api.ask("store_secret", connection=cid, name="app_token", value=APP)
    assert answer["ok"] is True and answer["result"] == {"stored": True} and "error" not in answer
    assert c.service.store.secrets(cid).get("app_token") == APP
    assert cls.instances[0].changes == 1                                       # the driver was told
    await api.call("store_secret", connection=cid, name="user_token", value=USER)
    assert cls.instances[0].changes == 2 and cls.instances[0].secrets.names() == ["app_token", "user_token"]
    assert cls.instances[0].secrets.get("user_token") == USER


async def test_only_the_secrets_a_driver_lists_are_taken_and_only_when_they_look_right(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls, "mcp": scripted(secret_rules={})})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    tool = (await api.call("add_connection", service="notion"))["id"]
    await api.fails("store_secret", "refused", connection=cid, name="password", value=APP)
    await api.fails("store_secret", "refused", connection=tool, name="app_token", value=APP)
    for bad in ("nope-" + "1" * 20, USER, "", " " + APP, APP + " ", APP + "\n", APP[:6] + "\n" + APP[6:],
                APP + "a" * 2000, "\x00" + APP):
        sentence = await api.fails("store_secret", "bad_request", connection=cid, name="app_token", value=bad)
        assert bad.strip() not in sentence or not bad.strip()
    for args in ({"connection": cid, "name": 5, "value": APP}, {"connection": cid, "name": "app_token", "value": 5},
                 {"connection": cid, "name": "app_token"}, {"connection": "nonsense", "name": "app_token", "value": APP}):
        await api.fails("store_secret", "bad_request", **args)
    await api.fails("store_secret", "not_found", connection="slack:w9", name="app_token", value=APP)
    assert c.service.store.secrets(cid).names() == [] and cls.instances[0].changes == 0
    exactly = APP + "a" * (service.MAX_SECRET - len(APP))
    assert await api.call("store_secret", connection=cid, name="app_token", value=exactly) == {"stored": True}


async def test_a_secret_is_in_no_answer_push_log_line_or_exception(places, started, capsys):
    secret = "xa" + "pp-" + CANARY
    cls = scripted(changed_raises=True)

    async def sleep(seconds):
        await asyncio.sleep(0.01)
    c = await start_with(places, started, {"slack": cls}, sleep=sleep)
    api, watcher = await c.client(), await c.client()
    await watcher.call("subscribe")
    cid = (await api.call("add_connection", service="slack"))["id"]
    answer = await api.ask("store_secret", connection=cid, name="app_token", value=secret)    # the driver raises with it
    assert answer["ok"] is True and answer["result"] == {"stored": True}
    await api.call("store_secret", connection=cid, name="app_token", value=secret + "2")
    for each in (await api.ask("connections"), await api.ask("status"), await api.ask("messages"),
                 await api.ask("tasks")):
        assert CANARY not in json.dumps(each)
    await api.ask("add_connection", service="slack")
    await api.ask("remove_connection", id=cid)
    await asyncio.sleep(0.2)
    heard = b"".join(api.seen_lines + watcher.seen_lines).decode()
    assert CANARY not in heard and "cannot use" not in heard
    logged = capsys.readouterr().err
    assert CANARY not in logged and "cannot use" not in logged
    assert logged.count("\n") >= 1                                                   # something was logged: the type
    assert "RuntimeError" in logged


async def test_a_secret_that_makes_the_service_fail_inside_is_not_in_its_log_either(places, started, capsys, monkeypatch):
    c = await start_with(places, started, {"slack": scripted()})
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]

    def boom(self, name, value):
        raise RuntimeError("could not write " + value)
    monkeypatch.setattr(type(c.service.store.secrets(cid)), "set", boom)
    got = await api.ask("store_secret", connection=cid, name="app_token", value="xa" + "pp-" + CANARY)
    assert got["ok"] is False and got["code"] == "internal" and CANARY not in json.dumps(got)
    assert CANARY not in capsys.readouterr().err


async def test_a_driver_that_raises_over_a_new_secret_is_built_again_from_what_is_stored(places, started):
    cls = scripted(changed_raises=True)
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0.01)
    c = await start_with(places, started, {"slack": cls}, sleep=sleep)
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    await api.call("store_secret", connection=cid, name="app_token", value=APP)
    await until(lambda: len(cls.instances) == 2)
    assert cls.instances[0].stopped and cls.instances[1].secrets.get("app_token") == APP and sleeps[:1] == [2.0]


# -- subscribers --

async def test_a_subscriber_that_stops_reading_is_dropped_and_does_not_slow_the_others(places, started, monkeypatch):
    monkeypatch.setattr(service, "QUEUE_MAX", 4)
    monkeypatch.setattr(service, "DRAIN_S", 30.0)
    c = await Connect.start(places, clock=Clock())
    started.append(c)
    api, good = await c.client(), await c.client()
    await good.call("subscribe")
    before = set(c.service.peers)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sock.connect(str(c.service.socket_path))
    sock.sendall(line({"id": 1, "op": "subscribe"}))
    slow = next(iter(await until(lambda: set(c.service.peers) - before)))
    await until(lambda: slow.subscribed)
    big = "words " * 600
    begun = time.monotonic()
    for n in range(300):
        await inject(api, big, ts=NOON + n, ref=f"slack:TW1/D0X/{n + 1}.000000")
    assert time.monotonic() - begun < 20
    got = [await good.push("message") for _ in range(300)]
    assert len(got) == 300
    await until(lambda: slow.closed and slow not in c.service.peers, 10)            # the one that did not read is gone
    sock.close()
    assert (await api.call("status"))["state"] == "ok"


# -- drivers that go wrong --

async def test_a_driver_that_does_not_start_is_started_again_with_a_pause_that_grows_to_a_minute(places, started, capsys):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0.005)
    cls = scripted(fail_starts=8)
    c = await start_with(places, started, {"slack": cls}, sleep=sleep)
    api = await c.client()
    watcher = await c.client()
    await watcher.call("subscribe")
    made = await api.call("add_connection", service="slack")
    assert made["state"] == "error" and "stopped" in made["note"] and made["note"].endswith(".")
    await until(lambda: cls.starts == 9)
    assert sleeps[:8] == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
    assert len(cls.instances) == 9 and all(d.stopped for d in cls.instances[:8])
    assert not cls.instances[8].stopped
    assert CANARY not in json.dumps(made) and CANARY not in capsys.readouterr().err
    pushed = await watcher.push("connection", where=lambda m: m["connection"]["state"] == "error")
    assert pushed["connection"]["id"] == made["id"]


async def test_the_pause_before_a_driver_starts_again():
    assert [service._backoff(n) for n in range(0, 9)] == [2.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]


async def test_a_driver_that_starts_again_and_says_it_is_well_clears_the_error(places, started):
    cls = scripted(fail_starts=1, start_emits=[{"push": "state", "state": "ok", "note": ""}])

    async def sleep(seconds):
        await asyncio.sleep(0.01)
    c = await start_with(places, started, {"slack": cls}, sleep=sleep)
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    await until(lambda: c.live(cid).record["state"] == "ok")
    assert c.service.store.connection(cid)["state"] == "ok" and c.service.store.connection(cid)["note"] == ""


async def test_a_driver_that_says_it_died_is_stopped_and_started_again(places, started):
    cls = scripted(start_emits=[{"push": "state", "state": "ok", "note": ""}])
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0.01)
    c = await start_with(places, started, {"slack": cls}, sleep=sleep)
    api = await c.client()
    cid = (await api.call("add_connection", service="slack"))["id"]
    await until(lambda: c.live(cid).record["state"] == "ok")
    cls.instances[0].emit({"push": "died"})
    await until(lambda: len(cls.instances) == 2)
    assert cls.instances[0].stopped and sleeps[0] == 2.0
    await until(lambda: c.live(cid).record["state"] == "ok")
    cls.instances[0].emit({"push": "state", "state": "error", "note": "from the old one"})   # a stopped driver is not heard
    assert c.live(cid).record["note"] == ""


async def test_a_driver_that_is_not_there_is_an_error_for_its_connection_only(places, started):
    seed(places, ("slack:w1", "slack", "slack", "ok"), ("mcp:linear", "mcp", "linear", "ok"))
    cls = scripted(start_emits=[{"push": "state", "state": "ok", "note": ""}])
    c = await start_with(places, started, {"slack": "bombadil.connect.not_written_yet:SlackDriver", "mcp": cls})
    api = await c.client()
    got = {x["id"]: x for x in (await api.call("connections"))["connections"]}
    assert got["slack:w1"]["state"] == "error" and got["slack:w1"]["note"] == "Slack is not available here."
    await until(lambda: c.live("mcp:linear").record["state"] == "ok")
    assert (await api.call("status"))["state"] == "ok"
    assert c.service.store.connection("slack:w1")["note"] == "Slack is not available here."
    await api.fails("store_secret", "refused", connection="slack:w1", name="app_token", value=APP)
    await api.fails("perform", "bad_request")
    made = await api.call("add_connection", service="slack")                    # and a new one says the same
    assert made["state"] == "error" and made["note"] == "Slack is not available here."


async def test_a_driver_module_that_is_there_without_the_class_or_that_fails_to_import_is_the_same_error(
        places, started, tmp_path, monkeypatch):
    (tmp_path / "brokendrv.py").write_text("raise SyntaxError('this module is being edited')\n")
    (tmp_path / "emptydrv.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    seed(places, ("slack:w1", "slack", "slack", "ok"), ("mcp:linear", "mcp", "linear", "ok"))
    c = await start_with(places, started, {"slack": "brokendrv:Driver", "mcp": "emptydrv:McpDriver"})
    api = await c.client()
    got = {x["id"]: x["note"] for x in (await api.call("connections"))["connections"]}
    assert got == {"slack:w1": "Slack is not available here.", "mcp:linear": "Linear is not available here."}


async def test_a_kind_with_no_driver_registered_is_an_error_not_a_crash(places, started):
    seed(places, ("slack:w1", "slack", "slack", "ok"))
    c = await start_with(places, started, {})
    assert (await (await c.client()).call("connections"))["connections"][0]["state"] == "error"


async def test_a_driver_that_will_not_say_what_state_it_is_in_does_not_hold_up_add_connection(places, started, monkeypatch):
    monkeypatch.setattr(service, "START_WAIT_S", 0.2)
    cls = scripted(pause_start=3.0)
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    begun = time.monotonic()
    made = await api.call("add_connection", service="slack")
    assert time.monotonic() - begun < 2 and made["state"] == "setup"
    assert (await api.call("status"))["state"] == "ok"


async def test_a_start_that_never_comes_back_is_given_up_on_and_tried_again(places, started, monkeypatch):
    monkeypatch.setattr(service, "START_S", 0.1)
    cls = scripted(pause_start=30.0)

    async def sleep(seconds):
        await asyncio.sleep(0.01)
    c = await start_with(places, started, {"slack": cls}, sleep=sleep)
    api = await c.client()
    await api.call("add_connection", service="slack")
    await until(lambda: cls.starts >= 2)
    assert cls.instances[0].stopped


async def test_what_a_driver_says_about_its_state_is_written_down_and_pushed_only_when_it_changed(places, started):
    cls = scripted(start_emits=[{"push": "state", "state": "ok", "note": "", "name": "Beta Co"}])
    c = await start_with(places, started, {"slack": cls})
    api, watcher = await c.client(), await c.client()
    await watcher.call("subscribe")
    cid = (await api.call("add_connection", service="slack"))["id"]
    assert c.live(cid).record["name"] == "Beta Co" and c.service.store.connection(cid)["name"] == "Beta Co"
    driver = cls.instances[0]
    await watcher.push("connection", where=lambda m: m["connection"]["name"] == "Beta Co")
    for _ in range(3):
        driver.emit({"push": "state", "state": "ok", "note": "", "name": "Beta Co"})        # nothing changed
    driver.emit({"push": "state", "state": "teapot", "note": "no such state"})              # not a state
    driver.emit({"push": "state", "state": "blocked", "note": "Your administrator\nhas to approve\x07 it.  "})
    pushed = await watcher.push("connection", where=lambda m: m["connection"]["state"] == "blocked")
    assert (pushed["connection"]["state"], pushed["connection"]["note"]) == ("blocked", "Your administrator has to approve it.")
    await watcher.quiet("connection")
    assert c.service.store.connection(cid)["state"] == "blocked"
    driver.emit("not a push")
    driver.emit({"push": "unheard-of"})
    driver.emit({"push": "message", "message": "not a message"})
    driver.emit({"push": "task", "item": None})
    assert (await api.call("status"))["state"] == "ok"


async def test_a_driver_that_fails_when_asked_about_itself_does_not_break_the_connection_list(places, started):
    class Broken(Scripted):
        instances: list = []
        starts = 0

        def reads(self):
            raise RuntimeError("no " + CANARY)

        def steps(self):
            return "not a list"

        def task_fields(self):
            return {"create": [{"key": "a b", "label": "A", "edit": "weird", "required": True}, "junk", {"key": ""}]}

        def can_post(self):
            raise RuntimeError("no")

    c = await start_with(places, started, {"slack": Broken})
    api = await c.client()
    made = await api.call("add_connection", service="slack")
    assert made["reads"] == "" and made["steps"] == [] and made["can_post"] is False and "open" not in made
    assert made["task_fields"] == {"create": [{"key": "a b", "label": "A", "edit": "line", "required": True}]}


async def test_steps_are_shown_while_a_connection_is_being_set_up_and_not_after(places, started):
    cls = scripted()
    c = await start_with(places, started, {"slack": cls})
    api = await c.client()
    made = await api.call("add_connection", service="slack")
    assert made["state"] == "setup" and made["steps"] == [{"id": "go", "say": "Do the thing.",
                                                           "open": "https://auth.acme.test/go"}]
    assert made["open"] == "https://auth.acme.test/go"
    cls.instances[0].emit({"push": "state", "state": "ok", "note": ""})
    shown = (await api.call("connections"))["connections"][0]
    assert shown["state"] == "ok" and shown["steps"] == [] and "open" not in shown


# -- the process --

def _run(env, *args, **kw):
    return subprocess.Popen([sys.executable, str(ROOT / "bin" / "bombadil-connect"), *args], env={**os.environ, **env},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


async def test_the_launcher_prints_what_it_is_and_refuses_what_it_is_not_given():
    def body():
        done = _run({}, "--help").communicate(timeout=20)
        assert "bombadil-connect" in done[0]
        assert _run({}, "nonsense").wait(timeout=20) == 2
    await asyncio.to_thread(body)


async def test_the_process_serves_the_fake_engine_and_stops_on_sigterm(tmp_path):
    await asyncio.to_thread(_serves_and_stops, tmp_path)


def _serves_and_stops(tmp_path):
    env = {"BOMBADIL_CONNECT_ENGINE": "fake", "BOMBADIL_CONNECT_UIDS": "0-65535",
           "BOMBADIL_CONNECT_SOCKET": str(tmp_path / "c.sock"), "BOMBADIL_CONNECT_STATE": str(tmp_path / "state")}
    proc = _run(env)
    try:
        end = time.monotonic() + 20
        while not (tmp_path / "c.sock").exists() and time.monotonic() < end:
            time.sleep(0.05)
        from bombadil.connect import client
        with client.Connection(tmp_path / "c.sock") as one:
            assert [c["id"] for c in one.request("connections")["connections"]] == ["slack:w1", "mcp:linear"]
            assert len(one.request("messages")["messages"]) == 3
        proc.terminate()
        assert proc.wait(timeout=20) == 0
        assert not (tmp_path / "c.sock").exists()
        assert "bombadil-connect: keeping" in proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()


async def test_a_second_process_and_a_socket_that_cannot_be_bound_say_so_in_one_line_and_exit_with_one(tmp_path):
    await asyncio.to_thread(_cannot_start, tmp_path)


def _cannot_start(tmp_path):
    env = {"BOMBADIL_CONNECT_ENGINE": "fake", "BOMBADIL_CONNECT_UIDS": "0-65535",
           "BOMBADIL_CONNECT_SOCKET": str(tmp_path / "c.sock"), "BOMBADIL_CONNECT_STATE": str(tmp_path / "state")}
    first = _run(env)
    try:
        end = time.monotonic() + 20
        while not (tmp_path / "c.sock").exists() and time.monotonic() < end:
            time.sleep(0.05)
        second = _run(env)
        _, err = second.communicate(timeout=20)
        assert second.returncode == 1 and "already keeping" in err and len(err.strip().splitlines()) == 1
    finally:
        first.terminate()
        first.wait(timeout=20)
    long = _run({**env, "BOMBADIL_CONNECT_SOCKET": str(tmp_path / ("x" * 200) / "c.sock")})
    _, err = long.communicate(timeout=20)
    assert long.returncode == 1 and "cannot start" in err.splitlines()[-1] and "Traceback" not in err
    fresh = _run({**env, "BOMBADIL_CONNECT_STATE": str(tmp_path / "file")})
    (tmp_path / "file").write_text("a file where the directory should be")
    _, err = fresh.communicate(timeout=20)
    assert fresh.returncode == 1 and "Traceback" not in err
    assert fake.WORKSPACE == "Acme"

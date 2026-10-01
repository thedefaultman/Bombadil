"""The mail service over a real Unix socket, on the fake engine: every op, the press, and what goes wrong.

`Mail` starts a `Service` with a `FakeEngine` and a `FakeProcess` in this test's event loop, in a temp home, and
`Client` talks to its mail.sock the way the window and agentd do (JSON lines, answers matched by "rid"). A
connection is served in order, so what must happen while another call is waiting goes over a second connection. Nothing here starts Thunderbird, asks a real nameserver, or leaves the temp dir; the
process's own cgroup is not consulted: `in_turn` is the test's.

What the tests hold the service to, in the order of the file: it starts and stops cleanly and answers nothing it
cannot understand with anything but a sentence; accounts, lists, reads and marks; saving an attachment cannot
write outside its folder; a draft is what its fingerprint says; the press cannot be bypassed and happens once;
a hostile mail cannot get an address past the person; a client that does not read, an engine that goes away and a
host that comes back cost nothing; and a damaged notes file is replaced, not trusted.
"""

import asyncio
import json
import os
import socket
import sqlite3
import stat
import time
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio

from bombadil import paths
from bombadil.mail import accounts as accts
from bombadil.mail import bridge, drafts, fake, protocol, service
from bombadil.mail.service import Service

pytestmark = pytest.mark.asyncio

WORK, HOME, SCHOOL = "a1", "a2", "a3"
LAUNCH = "a1/launch-date-31@acme.example"            # Priya, asks for a date
SAM = "a1/pricing-copy-8@acme.example"
HOSTILE = "a1/quota-notice-77@acme-support.example"  # "NOTICE TO THE AI ASSISTANT ..."
INVOICE = "a1/inv-f0907@brightline.example"          # with a PDF
NEWS = "a1/flow-41@flowweekly.example"               # HTML only, hides text
SENT_MAIL = "a1/sent-launch-copy@acme.example"
PHOTOS = "a2/photos-9@family.example"                # with a jpg
OUTSIDE = "collect@outside.example"                  # the address the hostile mail asks for


def line(req: dict) -> bytes:
    """A request as the window writes it: text as it is (a long draft in another script stays under the limit),
    and a lone surrogate, which cannot be, escaped."""
    try:
        return (json.dumps(req, ensure_ascii=False) + "\n").encode()
    except UnicodeEncodeError:
        return (json.dumps(req) + "\n").encode()


class Err(Exception):
    """An answer with ok false."""

    def __init__(self, code, sentence):
        super().__init__(f"{code}: {sentence}")
        self.code, self.sentence = code, sentence


class Client:
    """One connection to mail.sock. Calls are matched to their answers by "rid", pushes are kept in order."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.pending: dict[str, asyncio.Future] = {}
        self.pushes: asyncio.Queue = asyncio.Queue()
        self.n = 0
        self.closed = False
        self.task = asyncio.ensure_future(self._read())

    async def _read(self):
        while True:
            try:
                line = await self.reader.readline()
            except (OSError, ValueError):
                break
            if not line:
                break
            msg = json.loads(line)
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
        """The whole answer, ok or not."""
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

    async def push(self, name=None, timeout=3.0) -> dict:
        """The next push (of that name, skipping others)."""
        end = time.monotonic() + timeout
        while True:
            msg = await asyncio.wait_for(self.pushes.get(), max(0.01, end - time.monotonic()))
            if name is None or msg["push"] == name:
                return msg

    async def quiet(self, name, seconds=0.4):
        """No push of this name comes in the next few moments."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                msg = await asyncio.wait_for(self.pushes.get(), max(0.01, end - time.monotonic()))
            except TimeoutError:
                return
            assert msg["push"] != name, msg

    def close(self):
        self.writer.close()


async def until(fn, timeout=5.0, every=0.02):
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


class Mail:
    """A service, its engine and process, and the clients opened on it."""

    def __init__(self, svc, engine, process):
        self.service, self.engine, self.process = svc, engine, process
        self.agent_flag = False
        self.clients: list[Client] = []
        self.task: asyncio.Task | None = None
        self.dns: list[str] = []

    @classmethod
    async def start(cls, *, engine=None, process=True, initial=True, up=True, **kw):
        engine = engine if engine is not None else fake.FakeEngine()
        proc = fake.FakeProcess(engine) if process is True else process
        self = cls(None, engine, proc)

        def resolver(domain):
            self.dns.append(domain)
            return []
        kw.setdefault("initial_accounts", engine.account_rows() if initial else [])
        self.service = Service(engine=engine, process=proc, resolver=resolver, in_turn=lambda pid: self.agent_flag,
                               **kw)
        self.task = asyncio.ensure_future(self.service.serve())
        await until(lambda: self.service.socket_path.exists() or self.task.done())
        if self.task.done():
            self.task.result()
        if up:
            await until(lambda: self.service.engine_state == "up", 8.0)
        return self

    async def client(self, agent=False) -> Client:
        reader, writer = await asyncio.open_unix_connection(str(self.service.socket_path), limit=1 << 26)
        c = Client(reader, writer)
        if agent:
            self.agent_flag = True   # the service asks who is on the other end as it accepts the connection
            try:
                await c.call("ping")
            finally:
                self.agent_flag = False
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

    def press_rows(self) -> list[dict]:
        path = paths.press_log()
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    @property
    def files(self) -> Path:
        return paths.mail_files()

    def draft_files(self, did) -> list[str]:
        folder = self.files / "drafts" / did
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture(autouse=True)
def places(home, monkeypatch):
    """Every path the service uses is in the temp home, and nothing may ask a real nameserver."""
    monkeypatch.setenv("BOMBADIL_MAIL_DB", str(home / "state" / "mail.db"))
    monkeypatch.setenv("BOMBADIL_MAIL_FILES", str(home / "state" / "mail"))
    monkeypatch.setenv("BOMBADIL_MAIL_PROFILE", str(home / "profile"))
    monkeypatch.setenv("BOMBADIL_PRESS_LOG", str(home / "state" / "presses.jsonl"))
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(home / "run" / "mail.sock"))

    def real(domain, *a, **k):
        raise AssertionError(f"a real DNS query for {domain}")
    monkeypatch.setattr(accts, "mx_hosts", real)
    # the service's own clocks, made quick so that supervision and sends can be watched
    monkeypatch.setattr(service, "SUPERVISE_S", 0.05)
    monkeypatch.setattr(service, "PUSH_S", 0.05)
    monkeypatch.setattr(service, "BACKOFF_MIN", 0.05)
    monkeypatch.setattr(service, "BACKOFF_MAX", 0.2)
    monkeypatch.setattr(service, "SEND_S", 0.6)
    (home / "docs").mkdir()
    return home


@pytest_asyncio.fixture
async def started():
    """Every Mail a test starts, stopped at the end."""
    made: list[Mail] = []
    yield made
    for m in made:
        await m.stop()


@pytest_asyncio.fixture
async def mail(started) -> Mail:
    m = await Mail.start()
    started.append(m)
    return m


@pytest_asyncio.fixture
async def person(mail) -> Client:
    return await mail.client()


@pytest_asyncio.fixture
async def agent(mail) -> Client:
    return await mail.client(agent=True)


def write(home, name, data=b"fixture"):
    path = home / "docs" / name
    path.write_bytes(data)
    return path


async def reply_draft(c, to=LAUNCH, body="The 14th works.", **kw):
    return await c.call("draft", reply_to=to, body=body, **kw)


async def shown(c, draft):
    """What the window does when it draws a draft: says which fingerprint it drew."""
    return await c.call("draft_shown", id=draft["id"], fingerprint=draft["fingerprint"])


async def press(c, draft, **kw):
    return await c.call("send", id=draft["id"], fingerprint=draft["fingerprint"], **kw)


async def ready(c, **kw):
    """A draft that has been made and shown, ready for the press."""
    d = await reply_draft(c, **kw)
    await shown(c, d)
    return d


# -- starting, stopping, and the wire --

async def test_the_socket_is_private_and_the_service_says_it_is_up(mail, person):
    mode = stat.S_IMODE(os.stat(mail.service.socket_path).st_mode)
    assert mode == 0o600
    status = await person.call("status")
    assert status["engine"] == "up" and status["fake"] is True
    assert [a["email"] for a in status["accounts"]] == ["maya@acme.example", "maya.reyes@gmail.example",
                                                         "maya.reyes@lakeside.example"]
    assert (await person.call("ping"))["pong"] is True


async def test_a_second_service_on_the_same_notes_does_not_start_and_does_not_disturb_the_first(mail, person, home):
    second = Service(engine=fake.FakeEngine(connected=True), process=None, resolver=lambda d: [],
                     in_turn=lambda pid: False, socket_path=home / "run" / "other.sock")
    with pytest.raises(service.AlreadyRunning):
        await second.serve()
    assert not (home / "run" / "other.sock").exists()
    assert (await person.call("ping"))["pong"]


async def test_a_socket_left_by_a_service_that_died_is_replaced(started, home):
    (home / "run").mkdir()
    dead = socket.socket(socket.AF_UNIX)
    dead.bind(str(home / "run" / "mail.sock"))
    dead.close()
    m = await Mail.start()
    started.append(m)
    c = await m.client()
    assert (await c.call("ping"))["pong"]


async def test_stopping_closes_the_clients_removes_the_socket_and_lets_go_of_the_notes(started, home):
    m = await Mail.start()
    c = await m.client()
    await c.call("subscribe")
    m.service.stop()
    await asyncio.wait_for(m.task, 10)
    assert not m.service.socket_path.exists()
    await until(lambda: c.closed)
    again = await Mail.start()   # the lock was let go: a new service keeps the same notes
    started.append(again)
    assert len((await (await again.client()).call("accounts"))["accounts"]) == 3


async def test_a_socket_that_a_newer_service_made_is_not_removed_by_an_older_one_stopping(started, home):
    m = await Mail.start()
    path = m.service.socket_path
    os.unlink(path)
    other = socket.socket(socket.AF_UNIX)
    other.bind(str(path))
    m.service.stop()
    await asyncio.wait_for(m.task, 10)
    assert path.exists()
    other.close()


async def test_the_answer_carries_the_id_the_request_had_and_rid_when_given(mail):
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    for req in ({"id": 7, "op": "ping"}, {"id": "d3", "op": "ping", "rid": "x9"}, {"op": "ping", "rid": 5},
                {"id": ["a"], "op": "ping"}):
        writer.write((json.dumps(req) + "\n").encode())
        got = json.loads(await reader.readline())
        assert got["ok"] is True and got["id"] == req.get("id") and got.get("rid") == req.get("rid")
    writer.write(b'{"op": "ping", "rid": true}\n')
    assert "rid" not in json.loads(await reader.readline())
    writer.close()


async def test_a_line_that_is_not_a_request_is_a_sentence_and_the_connection_goes_on(mail):
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    for line in (b"not json\n", b"[1, 2]\n", b"5\n", b'"text"\n', b"null\n", b"\xff\xfe\n", b"\n{}\n",
                 b'{"op": 5}\n', b'{"op": "delete_everything"}\n', b'{"id": 1}\n', b'{"op": "__class__"}\n'):
        writer.write(line)
        answer = json.loads(await reader.readline())
        assert answer["ok"] is False and answer["code"] == "bad_request" and answer["error"]
    writer.write(b'{"id": 1, "op": "ping"}\n')
    assert json.loads(await reader.readline())["ok"] is True
    writer.close()


async def test_a_request_line_over_the_limit_is_refused_and_the_connection_ends_and_nobody_else_minds(mail, person):
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    writer.write(b'{"op": "ping", "x": "' + b"x" * (service.LINE_LIMIT + 10) + b'"}\n')
    answer = json.loads(await reader.readline())
    assert answer["ok"] is False and answer["code"] == "bad_request" and "too long" in answer["error"]
    assert await reader.readline() == b""
    assert (await person.call("ping"))["pong"]


async def test_deeply_nested_json_is_a_sentence_and_not_a_crash(mail, person):
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    writer.write(b'{"op": "ping", "x": ' + b"[" * 50_000 + b"]" * 50_000 + b"}\n")
    assert json.loads(await reader.readline())["code"] == "bad_request"
    writer.close()
    assert (await person.call("ping"))["pong"]


async def test_a_request_that_raises_inside_the_service_is_internal_and_costs_nothing_else(mail, person, monkeypatch,
                                                                                         capsys):
    async def boom(self, req, conn):
        raise RuntimeError("a bug")
    monkeypatch.setattr(Service, "_op_views", boom)
    sentence = await person.fails("views", "internal")
    assert "a bug" not in sentence   # what went wrong inside is for the log
    assert "RuntimeError" in capsys.readouterr().err
    assert (await person.call("ping"))["pong"]


# Arguments that are the wrong kind of thing, for each op that takes them. None of them may make the service say
# "internal": a request the service cannot understand is bad_request, or some other sentence that says why.
JUNK = [None, 0, -1, 10**30, 1.5, float("inf"), True, "", " ", "x" * 5000, "a1/k", "a1", "d1", [], [None], [1, 2],
        ["a@b.example"], {}, {"a": 1}, {"email": 5}, [[]], "\x00", "a\ud800b", "\u202e", "../../etc/passwd", "a@b.example"]
ARGS = {
    "status": [], "accounts": ["fresh"], "views": [], "ping": [], "subscribe": [], "requested": [],
    "add_account": ["email"], "remove_account": ["id"],
    "list": ["view", "limit", "cursor"], "search": ["text", "from", "account", "unread", "since", "limit"],
    "read": ["id"], "set_flags": ["id", "read", "flagged"], "archive": ["id"], "trash": ["id"],
    "mark_reply": ["id", "needs", "why"], "save_attachment": ["id", "part", "dir"],
    "draft": ["kind", "reply_to", "to", "cc", "bcc", "subject", "body", "attachments", "typed", "tainted",
              "created_by", "account"],
    "draft_edit": ["id", "to", "cc", "bcc", "subject", "body", "add_attachments", "remove_attachments", "typed"],
    "draft_get": ["id"], "draft_discard": ["id"], "draft_shown": ["id", "fingerprint"],
    "send": ["id", "fingerprint", "again"], "known": ["emails"], "show": ["view", "id", "reply"],
    "recent": ["since", "limit"], "engine_window": ["action"],
}


async def test_every_op_answers_a_sentence_to_arguments_of_the_wrong_kind(mail, person):
    assert set(ARGS) == set(service.OPS)
    d = await reply_draft(person)
    checked = 0
    for op, names in ARGS.items():
        for name in names:
            for junk in JUNK:
                if op == "remove_account" and junk in ("a1/k", "a1", "", " "):
                    continue   # "a1" is a real account: not junk, and removing it is a different test
                if op == "draft_discard" and junk == d["id"]:
                    continue   # "d1" is the draft that must still be there at the end
                args = {name: junk}
                if op in ("draft_shown", "send", "draft_edit", "draft_get", "draft_discard") and name != "id":
                    args["id"] = d["id"] if op != "draft_discard" else "d99"
                answer = await person.ask(op, **args)
                assert answer.get("code") != "internal", (op, args, answer)
                assert answer["ok"] or (answer["error"].strip() and answer["code"] in protocol.CODES), (op, args)
                checked += 1
    assert checked > 1000
    assert (await person.call("ping"))["pong"]
    assert (await person.call("draft_get", id=d["id"]))["state"] == "open"   # and nothing was sent or discarded
    assert mail.engine.sent == []


# -- accounts --

async def test_the_accounts_are_the_engines_three_with_their_states_and_web_pages(person):
    by_email = {a["email"]: a for a in (await person.call("accounts"))["accounts"]}
    work, home, school = by_email["maya@acme.example"], by_email["maya.reyes@gmail.example"], by_email[
        "maya.reyes@lakeside.example"]
    assert (work["id"], work["provider"], work["state"], work["unread"]) == ("a1", "google", "ok", 6)
    assert work["web"] == {"name": "Gmail", "url": "https://mail.google.com/mail/u/maya@acme.example/"}
    assert (home["id"], home["unread"]) == ("a2", 3)
    assert school["state"] == "blocked" and "admin" in school["note"]
    assert school["web"] == {"name": "Outlook", "url": "https://outlook.office.com/mail/"}


async def test_adding_an_address_finds_its_provider_asks_the_engine_and_says_what_to_do(mail, person):
    await person.call("subscribe")
    got = await person.call("add_account", email="Jo@Family.Example")
    assert got["email"] == "jo@family.example" and got["provider"] == "imap" and got["state"] == "syncing"
    assert mail.dns == ["family.example"]                       # the resolver was asked, and it was ours
    assert ("seed_account", "jo@family.example", "imap") in mail.process.calls
    assert (await person.push("changed"))["what"]
    await until(lambda: mail.engine.calls and any(c[0] == "accounts" for c in mail.engine.calls))


async def test_an_account_of_a_provider_that_signs_in_with_a_browser_waits_for_the_person_and_then_is_ready(mail, person):
    got = await person.call("add_account", email="new@gmail.com")
    assert got["provider"] == "google" and got["state"] == "signin" and "allow Thunderbird" in got["note"]
    assert mail.dns == []                                        # gmail.com is known: nobody was asked
    [waiting] = (await person.call("list", view=f"acct:{got['id']}"))["skipped"]    # nothing to read, and why
    assert waiting["state"] == "signin" and waiting["web"]["name"] == "Gmail"
    mail.engine.complete_signin("new@gmail.com")
    await until(lambda: mail.service.engine_state == "up")

    async def ok():
        return next(a for a in (await person.call("accounts", fresh=True))["accounts"]
                    if a["email"] == "new@gmail.com")["state"] == "ok"
    await until(ok)
    assert (await person.call("list", view=f"acct:{got['id']}"))["messages"] == []


async def test_adding_an_account_that_is_already_working_changes_nothing(mail, person):
    before = len(mail.process.calls)
    got = await person.call("add_account", email="maya@acme.example")
    assert got["id"] == "a1" and got["state"] == "ok" and len(mail.process.calls) == before


@pytest.mark.parametrize("bad", ["", "nobody", "a@b", "Jo <jo@family.example>", "a@b.example, c@d.example",
                                 " \n", "jo@family.example\nBcc: x@y.example", 5, None, ["jo@family.example"]])
async def test_what_is_not_an_email_address_is_not_added(person, bad):
    await person.fails("add_account", "bad_request", email=bad)
    assert len((await person.call("accounts"))["accounts"]) == 3


async def test_adding_one_that_thunderbird_cannot_be_set_up_for_says_so_and_keeps_the_account(mail, person):
    def boom(account, provider):
        raise OSError("read-only profile")
    mail.process.seed_account = boom
    await person.fails("add_account", "engine_error", email="jo@family.example")
    assert "jo@family.example" in [a["email"] for a in (await person.call("accounts"))["accounts"]]


async def test_a_provider_lookup_that_hangs_does_not_hang_adding(started, monkeypatch):
    monkeypatch.setattr(service, "DETECT_S", 0.2)
    m = await Mail.start()
    started.append(m)

    def slow(domain):
        time.sleep(0.6)
        return ["aspmx.l.google.com"]
    m.service.resolver = slow
    c = await m.client()
    began = time.monotonic()
    got = await c.call("add_account", email="jo@family.example")
    assert time.monotonic() - began < 0.55 and got["provider"] == "imap"   # the guess, not the hung answer


async def test_removing_an_account_forgets_it_with_its_marks_drafts_and_files_and_the_engine_is_told(mail, person,
                                                                                                   home):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="asks for a date")
    d = await reply_draft(person)
    f = write(home, "a.txt")
    d2 = await person.call("draft_edit", id=d["id"], add_attachments=[str(f)])
    assert mail.draft_files(d["id"]) == ["a.txt"] and d2["attachments"][0]["name"] == "a.txt"
    await person.call("subscribe")
    assert await person.call("remove_account", id="maya@acme.example") == {}
    assert ("forget_account", "maya@acme.example") in mail.process.calls
    assert [a["id"] for a in (await person.call("accounts"))["accounts"]] == ["a2", "a3"]
    await person.fails("draft_get", "not_found", id=d["id"])
    assert mail.draft_files(d["id"]) == []
    assert (await person.call("list", view="needs_reply"))["messages"] == []
    assert "drafts" in (await person.push("changed"))["what"]


async def test_a_removed_account_does_not_come_back_from_the_engines_list(mail, person):
    await person.call("remove_account", id="a2")
    mail.engine.add_account("maya.reyes@gmail.example", "google", state="ok")   # the engine has it again, and says so
    await asyncio.sleep(0.3)
    assert [a["id"] for a in (await person.call("accounts", fresh=True))["accounts"]] == ["a1", "a3"]


async def test_an_account_added_again_after_removal_is_a_new_account_so_old_ids_name_nothing(mail, person):
    await person.call("remove_account", id="a2")
    mail.engine.drop_account("maya.reyes@gmail.example")
    got = await person.call("add_account", email="maya.reyes@gmail.example")
    assert got["id"] == "a4"
    await person.fails("read", "no_account", id="a2/checkup-5@smilecare.example")


async def test_an_account_cannot_be_removed_while_one_of_its_messages_is_being_sent(mail, person, monkeypatch):
    d = await ready(person)
    other = await mail.client()        # the pressing connection is busy until the send is answered
    mail.engine.send_delay = 0.4
    sending = asyncio.create_task(press(person, d))
    await until(lambda: any(c[0] == "send" for c in mail.engine.calls))
    await other.fails("remove_account", "refused", id="a1")
    await sending
    assert await other.call("remove_account", id="a1") == {}


async def test_removing_the_last_account_stops_thunderbird_and_mail_is_off(mail, person):
    for acct in ("a1", "a2", "a3"):
        await person.call("remove_account", id=acct)
    await until(lambda: mail.service.engine_state == "off")
    assert mail.process.running() is False and ("stop",) in mail.process.calls
    assert (await person.call("list", view="all"))["messages"] == []
    await person.fails("draft", "no_account", to="jo@family.example", body="x")
    status = await person.call("status")
    assert status["engine"] == "off" and status["accounts"] == []


async def test_removing_an_account_that_is_not_there_is_no_account(person):
    await person.fails("remove_account", "no_account", id="a99")
    await person.fails("remove_account", "bad_request", id=None)


async def test_the_accounts_the_engine_has_are_found_again_when_the_notes_were_lost(started, home):
    engine = fake.FakeEngine(connected=True)
    m = await Mail.start(engine=engine, initial=False, process=None)
    started.append(m)
    c = await m.client()
    assert [a["email"] for a in (await c.call("accounts", fresh=True))["accounts"]] == [
        "maya@acme.example", "maya.reyes@gmail.example", "maya.reyes@lakeside.example"]
    blocked = next(a for a in (await c.call("accounts"))["accounts"] if a["id"] == "a3")
    assert blocked["state"] == "blocked" and blocked["provider"] == "microsoft"


async def test_a_sync_event_changes_an_accounts_state_and_the_window_is_told(mail, person):
    await person.call("subscribe")
    mail.engine.set_state("maya@acme.example", "error", "The server did not answer.")
    await person.push("changed")
    work = next(a for a in (await person.call("accounts"))["accounts"] if a["id"] == "a1")
    assert work["state"] == "error" and work["note"] == "The server did not answer."
    [skipped] = (await person.call("list", view="acct:a1"))["skipped"]
    assert skipped["state"] == "error" and skipped["note"] == "The server did not answer."
    mail.engine.set_state("maya@acme.example", "ok")
    await until(lambda: mail.service.engine_state == "up")

    async def back():
        return next(a for a in (await person.call("accounts"))["accounts"] if a["id"] == "a1")["state"] == "ok"
    await until(back)


async def test_the_views_are_all_the_accounts_needs_a_reply_and_drafts_with_counts(person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="asks for a date")
    await reply_draft(person)
    views = {v["id"]: v for v in (await person.call("views"))["views"]}
    assert list(views) == ["all", "acct:a1", "acct:a2", "acct:a3", "needs_reply", "drafts"]
    assert views["all"]["count"] == 9 and views["acct:a1"]["count"] == 6 and views["acct:a2"]["count"] == 3
    assert views["acct:a3"]["state"] == "blocked"
    assert views["needs_reply"]["count"] == 1 and views["drafts"]["count"] == 1
    assert views["all"]["name"] == "All inboxes" and views["acct:a1"]["name"] == "maya@acme.example"


# -- lists --

NEWEST_FIRST = [LAUNCH, "a2/checkup-5@smilecare.example", HOSTILE, SAM, "a1/empty-state-2@acme.example", PHOTOS,
                INVOICE, NEWS, "a2/stmt-9@example-bank.example"]


async def test_all_inboxes_are_one_list_newest_first_with_what_the_window_draws(person):
    got = await person.call("list", view="all")
    assert [m["id"] for m in got["messages"]] == NEWEST_FIRST
    assert got["view"] == "all" and got["cursor"] is None and got["more"] is False
    priya = got["messages"][0]
    assert priya["from"] == {"name": "Priya Shah", "email": "priya@acme.example"}
    assert priya["to"] == [{"name": "", "email": "maya@acme.example"}]
    assert (priya["account"], priya["key"], priya["subject"], priya["unread"], priya["flagged"]) == (
        "a1", "launch-date-31@acme.example", "Launch date by noon?", True, False)
    assert priya["attachments"] is False and priya["folder"] == "inbox" and priya["needs_reply"] is False
    assert priya["why"] is None and priya["thread"] == "launch" and isinstance(priya["ts"], float)
    assert next(m for m in got["messages"] if m["id"] == INVOICE)["attachments"] is True
    [skipped] = got["skipped"]
    assert skipped["account"] == "a3" and skipped["state"] == "blocked" and "admin" in skipped["note"]
    assert skipped["web"]["name"] == "Outlook"


async def test_a_list_is_paged_with_a_cursor_and_no_mail_is_shown_twice_or_missed(person):
    seen, cursor = [], None
    for _ in range(10):
        page = await person.call("list", view="all", limit=2, cursor=cursor)
        seen += [m["id"] for m in page["messages"]]
        cursor = page["cursor"]
        if not page["more"]:
            break
        assert cursor and len(page["messages"]) == 2
    assert seen == NEWEST_FIRST and cursor is None


async def test_mail_with_the_same_time_is_paged_without_repeats_or_gaps(mail, person):
    stamp = time.time() - 50 * 60
    keys = [mail.engine.add_message("maya@acme.example", "inbox", "Bulk <bulk@sender.example>", f"Same time {i}",
                                    "x", ts=stamp) for i in range(7)]
    everything = [m["id"] for m in (await person.call("list", view="all", limit=200))["messages"]]
    assert len(everything) == 16 and len(set(everything)) == 16
    for limit in (1, 2, 3, 5):
        seen, cursor = [], None
        for _ in range(40):
            page = await person.call("list", view="all", limit=limit, cursor=cursor)
            seen += [m["id"] for m in page["messages"]]
            cursor = page["cursor"]
            if not page["more"]:
                break
        assert sorted(seen) == sorted(everything) and len(seen) == len(set(seen)), limit
    assert {f"a1/{k}" for k in keys} <= set(everything)


async def test_one_accounts_inbox_by_id_or_by_address_and_a_blocked_one_says_why_it_is_empty(person):
    a2 = await person.call("list", view="acct:a2")
    assert [m["id"] for m in a2["messages"]] == [i for i in NEWEST_FIRST if i.startswith("a2/")] and a2["skipped"] == []
    assert (await person.call("list", view="acct:maya.reyes@gmail.example"))["messages"] == a2["messages"]
    school = await person.call("list", view="acct:a3")
    assert school["messages"] == [] and school["skipped"][0]["state"] == "blocked"
    await person.fails("list", "no_account", view="acct:a9")


@pytest.mark.parametrize("args", [{"view": "everything"}, {"view": 5}, {"view": ["all"]}, {"view": "acct:"},
                                  {"cursor": "garbage"}, {"cursor": 5}, {"cursor": "e30="}, {"cursor": "x" * 200_000},
                                  {"cursor": "eyJ0cyI6ICJ4IiwgInNlZW4iOiBbXX0="}])
async def test_a_list_asked_for_wrongly_is_bad_request(person, args):
    await person.fails("list", "bad_request", **args)


async def test_the_limit_is_held_to_what_a_window_could_want(person):
    assert len((await person.call("list", view="all", limit=0))["messages"]) == 1
    assert len((await person.call("list", view="all", limit="3"))["messages"]) == 3
    assert len((await person.call("list", view="all", limit=10**30))["messages"]) == 9
    assert len((await person.call("list", view="all", limit=True))["messages"]) == 9
    assert len((await person.call("list", view="all", limit=-5))["messages"]) == 1


async def test_needs_a_reply_lists_the_marks_with_why_and_who_newest_mail_first_and_pages(person):
    await person.call("mark_reply", id=SAM, needs=True, why="needs a yes on the pricing copy")
    await person.call("mark_reply", id=LAUNCH, needs=True, why="legal wants the launch date by noon")
    got = await person.call("list", view="needs_reply")
    assert [m["id"] for m in got["messages"]] == [LAUNCH, SAM]
    first = got["messages"][0]
    assert first["needs_reply"] is True and first["why"] == "legal wants the launch date by noon"
    assert first["from"] == {"name": "Priya Shah", "email": "priya@acme.example"}
    assert first["subject"] == "Launch date by noon?" and first["account"] == "a1"
    page = await person.call("list", view="needs_reply", limit=1)
    assert [m["id"] for m in page["messages"]] == [LAUNCH] and page["more"] is True
    rest = await person.call("list", view="needs_reply", limit=1, cursor=page["cursor"])
    assert [m["id"] for m in rest["messages"]] == [SAM] and rest["more"] is False and rest["cursor"] is None


async def test_drafts_are_listed_as_a_list_newest_first(person):
    assert await person.call("list", view="drafts") == []
    a = await reply_draft(person)
    b = await reply_draft(person, to=SAM, body="Yes.")
    got = await person.call("list", view="drafts")
    assert [d["id"] for d in got] == [b["id"], a["id"]] and got[0]["subject"] == "Re: Pricing copy: ok to go?"
    assert len(await person.call("list", view="drafts", limit=1)) == 1


# -- search --

async def test_search_finds_by_words_sender_and_state_across_folders_and_accounts(person):
    got = await person.call("search", text="invoice")
    assert [m["id"] for m in got["messages"]] == [INVOICE]
    assert [m["id"] for m in (await person.call("search", **{"from": "priya"}))["messages"]] == [
        LAUNCH, "a1/offsite-4@acme.example"]
    assert [m["folder"] for m in (await person.call("search", unread=False))["messages"]] == ["sent", "archive"]
    assert [m["id"] for m in (await person.call("search", account="a2", text="photos"))["messages"]] == [PHOTOS]
    assert (await person.call("search", account="maya.reyes@gmail.example", unread=True))["messages"][0]["account"] == "a2"
    assert (await person.call("search", text="zzz-nothing-like-this"))["messages"] == []


async def test_search_dates_are_epoch_seconds_or_a_day_and_a_bad_one_is_refused(person):
    assert len((await person.call("search", since=time.time() - 4800))["messages"]) == 2    # Priya and the dentist
    assert (await person.call("search", since="2999-01-01"))["messages"] == []
    assert len((await person.call("search", since="2000-01-01", limit=3))["messages"]) == 3
    for bad in ("yesterday", "", [], {}, True, "2026-13-45"):
        await person.fails("search", "bad_request", since=bad)


async def test_search_shows_the_marks_and_leaves_the_blocked_account_out(person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    [priya] = (await person.call("search", text="Launch date by noon"))["messages"]
    assert priya["needs_reply"] is True and priya["why"] == "a date"
    await person.fails("search", "no_account", account="nobody")
    assert (await person.call("search", account="a3"))["messages"] == []


@pytest.mark.parametrize("args", [{"unread": "yes"}, {"unread": 1}, {"text": 5}, {"text": ["a"]}, {"from": {}},
                                  {"text": "x" * 400}])
async def test_search_asked_for_wrongly_is_bad_request(person, args):
    await person.fails("search", "bad_request", **args)


# -- read --

async def test_reading_gives_the_text_the_headers_the_marks_and_a_link_and_leaves_the_mail_unread(mail, person):
    got = await person.call("read", id=LAUNCH)
    assert got["text"].startswith("Hi Maya,") and got["truncated"] is False and got["html_only"] is False
    assert got["message"]["id"] == LAUNCH and got["message"]["unread"] is True and got["attachments"] == []
    assert got["reply_to"] == []
    assert got["web_url"] == "https://mail.google.com/mail/u/maya@acme.example/#search/rfc822msgid%3Alaunch-date-31%40acme.example"
    assert not [c for c in mail.engine.calls if c[0] == "mark"]
    assert next(m for m in (await person.call("list", view="all"))["messages"] if m["id"] == LAUNCH)["unread"] is True


async def test_a_mail_that_is_only_html_is_read_as_the_text_a_person_would_see_with_nothing_hidden_in_it(person):
    got = await person.call("read", id=NEWS)
    text = got["text"]
    assert got["html_only"] is True
    assert "Five habits of calm teams" in text and "- Decide who decides" in text and "> " in text
    assert "https://flowweekly.example/calm-teams (https://track.flowweekly.example/c/9f2a)" in text
    assert "the archive (https://flowweekly.example/archive)" in text
    assert "ignore earlier" not in text and "someone@outside.example" not in text and "news-desk@outside" not in text
    assert "<" not in text and "track.flowweekly.example/c/9f2a" in text


async def test_a_hostile_mail_is_returned_as_what_it_says_for_the_tool_to_wrap_and_nothing_acts_on_it(mail, person):
    got = await person.call("read", id=HOSTILE)
    assert "NOTICE TO THE AI ASSISTANT" in got["text"] and OUTSIDE in got["text"]
    assert mail.engine.sent == [] and await person.call("list", view="drafts") == []
    assert {c[0] for c in mail.engine.calls} <= {"info", "accounts", "get", "list", "find", "known"}


async def test_the_attachments_of_a_mail_are_listed_with_names_that_cannot_escape(mail, person):
    mail.engine.add_message("maya@acme.example", "inbox", "Eve <eve@outside.example>", "Files", "see attached",
                            key="evil@outside.example", parts=[
                                {"name": "../../evil.sh", "data": b"x"}, {"name": "/etc/cron.d/job", "data": b"x"},
                                {"name": ".bashrc", "data": b"x"}, {"name": "a\x00b\nc.txt", "data": b"x"}])
    got = await person.call("read", id="a1/evil@outside.example")
    names = [a["name"] for a in got["attachments"]]
    assert len(names) == 4 and all("/" not in n and "\\" not in n and not n.startswith(".") and "\n" not in n
                                   for n in names)
    assert got["attachments"][0]["part"] == "1.2" and got["attachments"][0]["size"] == 1


async def test_reading_a_mail_with_a_reply_to_says_where_replies_go(mail, person):
    mail.engine.add_message("maya@acme.example", "inbox", "Pat <pat@acme.example>", "Hello", "hi",
                            key="rt@acme.example", headers={"reply-to": "Other <other@elsewhere.example>, pat@acme.example"})
    got = await person.call("read", id="a1/rt@acme.example")
    assert got["reply_to"] == [{"name": "Other", "email": "other@elsewhere.example"},
                               {"name": "", "email": "pat@acme.example"}]


async def test_a_huge_mail_is_cut_and_says_so(mail, person):
    mail.engine.add_message("maya@acme.example", "inbox", "Bulk <b@sender.example>", "Big", "word " * 100_000,
                            key="big@sender.example")
    got = await person.call("read", id="a1/big@sender.example")
    assert got["truncated"] is True and len(got["text"]) <= 200_000


async def test_a_mail_that_is_gone_is_not_found_and_its_mark_goes_with_it(mail, person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    mail.engine._mail["fake-1-maya"][:] = [r for r in mail.engine._mail["fake-1-maya"]
                                           if r["emsg"]["key"] != "launch-date-31@acme.example"]
    await person.fails("read", "not_found", id=LAUNCH)
    assert (await person.call("list", view="needs_reply"))["messages"] == []


@pytest.mark.parametrize("given, code", [("nope", "bad_request"), (5, "bad_request"), (None, "bad_request"),
                                         ("a9/k", "no_account"), ("a3/k", "refused"), ("a1/", "bad_request"),
                                         ("a1/" + "k" * 600, "bad_request")])
async def test_reading_what_cannot_be_read_says_why(person, given, code):
    await person.fails("read", code, id=given)


# -- flags, archive, trash --

async def test_a_mail_is_marked_read_and_flagged_and_the_counts_follow(mail, person):
    before = next(a for a in (await person.call("accounts"))["accounts"] if a["id"] == "a1")["unread"]
    await person.call("subscribe")
    assert await person.call("set_flags", id=LAUNCH, read=True, flagged=True) == {}
    got = next(m for m in (await person.call("list", view="all"))["messages"] if m["id"] == LAUNCH)
    assert got["unread"] is False and got["flagged"] is True
    assert (await person.push("changed"))["what"]
    after = next(a for a in (await person.call("accounts", fresh=True))["accounts"] if a["id"] == "a1")["unread"]
    assert after == before - 1
    await person.call("set_flags", id=LAUNCH, read=False)
    assert next(m for m in (await person.call("list", view="all"))["messages"] if m["id"] == LAUNCH)["unread"] is True


@pytest.mark.parametrize("args", [{}, {"read": "yes"}, {"read": 1}, {"flagged": []}, {"read": None, "flagged": None}])
async def test_flags_asked_for_wrongly_are_bad_request_and_change_nothing(mail, person, args):
    await person.fails("set_flags", "bad_request", id=LAUNCH, **args)
    assert not [c for c in mail.engine.calls if c[0] == "mark"]


@pytest.mark.parametrize("op, folder", [("archive", "archive"), ("trash", "trash")])
async def test_archiving_or_trashing_moves_it_away_and_it_no_longer_needs_a_reply(mail, person, op, folder):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    assert await person.call(op, id=LAUNCH) == {}
    assert LAUNCH not in [m["id"] for m in (await person.call("list", view="all"))["messages"]]
    assert mail.engine.message("maya@acme.example", "launch-date-31@acme.example")["folder"] == folder
    assert (await person.call("list", view="needs_reply"))["messages"] == []


async def test_a_mail_in_a_blocked_account_cannot_be_moved_and_the_sentence_says_why(person):
    sentence = await person.fails("archive", "refused", id="a3/x")
    assert "admin" in sentence


# -- marks --

async def test_a_mark_is_set_with_one_clean_line_of_why_cut_to_length_and_cleared(person):
    got = await person.call("mark_reply", id=LAUNCH, needs=True, why="  asks\nfor   a date\t" + "x" * 300)
    assert got["needs_reply"] is True and "\n" not in got["why"] and len(got["why"]) == service.WHY_MAX
    assert got["why"].startswith("asks for a date x")
    assert (await person.call("mark_reply", id=LAUNCH, needs=False)) == {"id": LAUNCH, "needs_reply": False, "why": None}
    assert (await person.call("list", view="needs_reply"))["messages"] == []
    assert (await person.call("mark_reply", id=LAUNCH, needs=True))["why"] == ""


@pytest.mark.parametrize("args", [{}, {"needs": "yes"}, {"needs": None}, {"needs": True, "why": 5},
                                  {"needs": True, "why": ["x"]}])
async def test_a_mark_asked_for_wrongly_is_bad_request(person, args):
    await person.fails("mark_reply", "bad_request", id=LAUNCH, **args)


async def test_a_mark_keeps_who_what_about_and_when_and_never_the_text(mail, person, home):
    await person.call("mark_reply", id=HOSTILE, needs=True, why="asks to verify the mailbox")
    raw = sqlite3.connect(home / "state" / "mail.db")
    row = raw.execute("SELECT * FROM marks").fetchone()
    blob = " ".join(str(x) for x in row)
    assert "IT Helpdesk" in blob and "verify your mailbox" in blob and "helpdesk@acme-support.example" in blob
    assert "NOTICE TO THE AI" not in blob and OUTSIDE not in blob
    everything = " ".join(str(x) for t in ("accounts", "drafts", "receipts", "meta", "sent_to")
                          for r in raw.execute(f"SELECT * FROM {t}").fetchall() for x in r)
    assert "NOTICE TO THE AI" not in everything


async def test_a_mark_whose_mail_left_the_inbox_some_other_way_is_dropped_at_the_next_list(mail, person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    await person.call("mark_reply", id=SAM, needs=True, why="a yes")
    rec = next(r for r in mail.engine._mail["fake-1-maya"] if r["emsg"]["key"] == "launch-date-31@acme.example")
    rec["emsg"]["folder"] = "archive"                          # archived in Thunderbird itself
    await person.call("list", view="all")
    assert [m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]] == [SAM]


async def test_a_mark_on_mail_below_the_page_that_was_read_is_kept(person):
    await person.call("mark_reply", id=NEWS, needs=True, why="old one")
    page = await person.call("list", view="all", limit=2)
    assert NEWS not in [m["id"] for m in page["messages"]] and page["more"] is True
    assert [m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]] == [NEWS]


async def test_marks_are_not_dropped_because_an_account_is_still_syncing(mail, person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    mail.engine.set_state("maya@acme.example", "syncing")

    async def syncing():
        return next(a for a in (await person.call("accounts"))["accounts"] if a["id"] == "a1")["state"] == "syncing"
    await until(syncing)
    mail.engine._mail["fake-1-maya"][:] = []   # the engine shows nothing while it syncs
    await person.call("list", view="all")
    assert [m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]] == [LAUNCH]


# -- recent, known --

async def test_recent_is_who_what_and_when_with_a_link_and_never_the_text(person):
    got = await person.call("recent", limit=4)
    assert [i["id"] for i in got["items"]] == NEWEST_FIRST[:4]
    item = got["items"][0]
    assert set(item) == {"id", "account", "from", "subject", "ts", "web_url"}
    assert item["account"] == "maya@acme.example" and item["web_url"].startswith("https://mail.google.com/")
    assert "Hi Maya" not in json.dumps(got)
    assert len((await person.call("recent", since=time.time() - 4800))["items"]) == 2
    await person.fails("recent", "bad_request", since="never")


async def test_known_says_which_addresses_the_person_has_dealt_with(mail, person):
    got = await person.call("known", emails=["priya@acme.example", "Nobody@Outside.Example", "leo@acme.example"])
    assert got == {"priya@acme.example": True, "nobody@outside.example": False, "leo@acme.example": True}
    for bad in ("x", [5], ["not an address"], ["a@b.example"] * 201, None):
        await person.fails("known", "bad_request", emails=bad)


# -- show --

async def test_a_show_is_pushed_to_whoever_listens_and_kept_once_for_a_window_that_starts_later(person, mail):
    listener = await mail.client()
    await listener.call("subscribe")
    got = await person.call("show", view="needs_reply")
    assert got["view"] == "needs_reply" and got["id"] is None and got["seq"] > 0
    pushed = await listener.push("show")
    assert pushed == {"push": "show", "view": "needs_reply", "id": None, "reply": None, "seq": got["seq"]}
    waiting = await person.call("requested")
    assert waiting["view"] == "needs_reply" and waiting["seq"] == got["seq"]
    assert await person.call("requested") is None
    second = await person.call("show", id=LAUNCH, reply="d1")
    assert second["seq"] == got["seq"] + 1 and second["reply"] == "d1"


async def test_a_show_nobody_took_is_forgotten_after_a_while(person, monkeypatch):
    monkeypatch.setattr(service, "REQUESTED_S", 0.1)
    await person.call("show", view="drafts")
    await asyncio.sleep(0.25)
    assert await person.call("requested") is None


@pytest.mark.parametrize("args", [{"view": "everything"}, {"view": 5}, {"id": ""}, {"id": 5}, {"reply": "x" * 1000},
                                  {"view": "acct:" + "x" * 50}])
async def test_a_show_asked_for_wrongly_is_bad_request(person, args):
    await person.fails("show", "bad_request", **args)
    assert await person.call("requested") is None


# -- new mail --

async def test_new_mail_from_someone_the_person_knows_is_pushed_with_known_true(mail, person):
    await person.call("subscribe")
    key = mail.engine.inject_new_mail("maya@acme.example", "Leo Park <leo@acme.example>", "Quick question", "body")
    push = await person.push("new_mail")
    assert push["known"] is True and push["message"]["id"] == f"a1/{key}" and push["message"]["unread"] is True
    assert push["message"]["from"]["email"] == "leo@acme.example"
    assert "what" in await person.push("changed")


async def test_new_mail_from_a_stranger_is_pushed_with_known_false_and_the_text_is_not_in_it(mail, person):
    await person.call("subscribe")
    mail.engine.inject_new_mail("maya@acme.example", "Eve <eve@outside.example>", "Hello", "secret body words")
    push = await person.push("new_mail")
    assert push["known"] is False and "secret body words" not in json.dumps(push)


async def test_only_new_mail_in_the_inbox_and_not_old_is_pushed_and_no_more_than_the_limit(mail, person):
    await person.call("subscribe")
    old = mail.engine.add_message("maya@acme.example", "inbox", "Old <old@sender.example>", "Old", "x",
                                  ts=time.time() - 3 * 86400)
    sent = mail.engine.add_message("maya@acme.example", "sent", "Maya <maya@acme.example>", "Sent", "x")
    fresh = [mail.engine.add_message("maya@acme.example", "inbox", f"S{i} <s{i}@sender.example>", f"New {i}", "x")
             for i in range(service.NEW_MAIL_MAX + 5)]
    emsgs = [mail.engine.message("maya@acme.example", k) for k in [old, sent, *fresh]]
    mail.engine._later(mail.engine._event, {"event": "new_mail", "account": "fake-1-maya", "messages": emsgs})
    got = []
    while True:
        try:
            got.append(await person.push("new_mail", timeout=0.6))
        except TimeoutError:
            break
    assert len(got) == service.NEW_MAIL_MAX
    assert all(p["message"]["subject"].startswith("New ") for p in got)


async def test_new_mail_for_an_account_nobody_knows_is_ignored(mail, person):
    await person.call("subscribe")
    mail.engine._later(mail.engine._event, {"event": "new_mail", "account": "nope", "messages": [
        mail.engine.message("maya@acme.example", "launch-date-31@acme.example")]})
    await person.quiet("new_mail")


@pytest.mark.parametrize("event", [{"event": "new_mail"}, {"event": "new_mail", "account": 5, "messages": "x"},
                                   {"event": "new_mail", "account": "fake-1-maya", "messages": [5, None, "x", {}]},
                                   {"event": "sync"}, {"event": "sync", "account": "fake-1-maya", "state": "bogus"},
                                   {"event": "nonsense"}, {"event": 5}, {}])
async def test_an_event_that_makes_no_sense_changes_nothing_and_the_service_goes_on(mail, person, event):
    await person.call("subscribe")
    mail.engine._later(mail.engine._event, event)
    await asyncio.sleep(0.15)
    assert (await person.call("ping"))["pong"]
    await person.quiet("new_mail", 0.2)


# -- saving an attachment --

def downloads(home) -> Path:
    return home / "Downloads"


def tree(home) -> set[str]:
    """Everything under the temp home that is a file, relative to it, apart from the service's own state."""
    return {str(p.relative_to(home)) for p in home.rglob("*") if p.is_file() and not str(p.relative_to(home)).startswith(
        ("state/", "run/", "profile/"))}


async def test_an_attachment_is_saved_in_downloads_under_its_own_name_and_the_answer_says_where_from(mail, person,
                                                                                                    home):
    got = await person.call("save_attachment", id=INVOICE, part="1.2")
    saved = Path(got["path"])
    assert saved == downloads(home) / "invoice-F-0907.pdf" and got["name"] == "invoice-F-0907.pdf"
    assert saved.read_bytes().startswith(b"%PDF-1.4") and got["size"] == saved.stat().st_size
    assert got["from"]["email"] == "billing@brightline.example" and got["subject"] == "Invoice F-0907 for September"
    assert stat_mode(saved) == 0o644
    assert list((mail.files / "inflight").glob("*")) == []   # the fetched file was moved, nothing stays behind


def stat_mode(path) -> int:
    return os.stat(path).st_mode & 0o777


async def test_saving_again_never_writes_over_a_file_that_is_there(mail, person, home):
    downloads(home).mkdir()
    (downloads(home) / "invoice-F-0907.pdf").write_bytes(b"MINE")
    first = await person.call("save_attachment", id=INVOICE, part="1.2")
    second = await person.call("save_attachment", id=INVOICE, part="1.2")
    assert (downloads(home) / "invoice-F-0907.pdf").read_bytes() == b"MINE"
    assert (first["name"], second["name"]) == ("invoice-F-0907 (1).pdf", "invoice-F-0907 (2).pdf")


async def test_a_folder_can_be_named_and_must_be_a_whole_path_to_a_folder_that_is_there(person, home):
    got = await person.call("save_attachment", id=INVOICE, part="1.2", dir=str(home / "docs"))
    assert Path(got["path"]).parent == home / "docs"
    got = await person.call("save_attachment", id=INVOICE, part="1.2", dir="~/docs")
    assert Path(got["path"]).parent == home / "docs"
    await person.fails("save_attachment", "bad_request", id=INVOICE, part="1.2", dir="docs")
    await person.fails("save_attachment", "not_found", id=INVOICE, part="1.2", dir=str(home / "nowhere"))
    await person.fails("save_attachment", "not_found", id=INVOICE, part="1.2", dir=str(home / "docs" / "a.txt"))


async def test_no_folder_that_holds_keys_or_the_services_own_state_is_written_to(mail, person, home):
    (home / ".ssh").mkdir()
    (home / ".config").mkdir()
    os.symlink(home / ".ssh", home / "docs" / "innocent")
    before = tree(home)
    for where in (home / ".ssh", home / ".config", home / "docs" / "innocent", mail.files, home / "state",
                  home / "run", "/etc/ssh", "/proc", "/etc/sudoers.d"):
        await person.fails("save_attachment", "refused" if Path(where).exists() else "not_found", id=INVOICE,
                           part="1.2", dir=str(where))
    assert tree(home) == before and not list((home / ".ssh").iterdir())


@pytest.mark.parametrize("name", ["../../evil.sh", "/etc/cron.d/job", ".bashrc", "CON.txt", "a\x00b.txt",
                                  "-rf", "x\u202efdp.exe", "....//....//x", "tab\there.txt", " . ", "..", "", "x" * 400])
async def test_no_attachment_name_makes_a_file_anywhere_but_in_the_folder_it_was_saved_to(mail, person, home, name):
    mail.engine.add_message("maya@acme.example", "inbox", "Eve <eve@outside.example>", "Files", "x",
                            key="evil@outside.example", parts=[{"name": name, "data": b"payload"}])
    before = tree(home)
    got = await person.call("save_attachment", id="a1/evil@outside.example", part="1.2")
    saved = Path(got["path"])
    assert saved.parent == downloads(home) and saved.read_bytes() == b"payload"
    assert saved.name and "/" not in saved.name and not saved.name.startswith((".", "-")) and len(saved.name.encode()) <= 120
    assert tree(home) - before == {str(saved.relative_to(home))}


async def test_a_link_left_in_the_folder_is_not_written_through(mail, person, home):
    victim = home / "docs" / "victim.txt"
    victim.write_text("precious")
    downloads(home).mkdir()
    os.symlink(victim, downloads(home) / "invoice-F-0907.pdf")
    os.symlink(home / "docs" / "nowhere", downloads(home) / "invoice-F-0907 (1).pdf")
    got = await person.call("save_attachment", id=INVOICE, part="1.2")
    assert victim.read_text() == "precious" and not (home / "docs" / "nowhere").exists()
    assert got["name"] == "invoice-F-0907 (2).pdf"


async def test_an_attachment_that_is_not_there_or_not_asked_for_properly_is_said(person):
    await person.fails("save_attachment", "not_found", id=INVOICE, part="9.9")
    await person.fails("save_attachment", "not_found", id=INVOICE, part="../../x")
    for part in (None, "", "  ", 5, ["1.2"], "x" * 65):
        await person.fails("save_attachment", "bad_request", id=INVOICE, part=part)
    await person.fails("save_attachment", "not_found", id="a1/nothing-like-it@example.test", part="1.2")
    await person.fails("save_attachment", "refused", id="a3/x", part="1.2")


async def test_an_attachment_over_what_may_be_saved_is_too_big_and_nothing_is_left(mail, person, home, monkeypatch):
    monkeypatch.setattr(bridge, "BLOB_MAX", 10)
    await person.fails("save_attachment", "too_big", id=INVOICE, part="1.2")
    assert not downloads(home).exists() or list(downloads(home).iterdir()) == []
    assert list((mail.files / "inflight").glob("*")) == []


async def test_a_big_attachment_arrives_whole_and_saves_are_not_held_up_by_each_other(mail, person, home):
    data = os.urandom(bridge.CHUNK * 2 + 17)
    mail.engine.add_message("maya@acme.example", "inbox", "Bulk <b@sender.example>", "Big", "x", key="big@sender.example",
                            parts=[{"name": "big.bin", "data": data}])
    results = await asyncio.gather(*(person.call("save_attachment", id="a1/big@sender.example", part="1.2")
                                     for _ in range(4)))
    assert sorted(r["name"] for r in results) == ["big (1).bin", "big (2).bin", "big (3).bin", "big.bin"]
    assert all(Path(r["path"]).read_bytes() == data for r in results)


# -- drafts --

async def test_a_new_draft_is_the_shape_the_window_draws_and_belongs_to_the_first_account_that_can_send(person):
    d = await person.call("draft", to=["Jo Reyes <jo@family.example>"], subject="Lunch?\nSaturday", body="Are you free?")
    assert d["id"] == "d1" and d["account"] == "a1" and d["kind"] == "new" and d["reply_to"] is None
    assert d["from"] == {"name": "Maya Reyes", "email": "maya@acme.example"}
    assert d["to"] == [{"name": "Jo Reyes", "email": "jo@family.example"}] and d["cc"] == [] and d["bcc"] == []
    assert d["subject"] == "Lunch? Saturday" and d["body"] == "Are you free?" and d["attachments"] == []
    assert d["created_by"] == "person" and d["state"] == "open" and d["receipt"] is None
    assert d["warnings"] == [] and d["adds"] == "Your Gmail signature is added when it sends."
    assert len(d["fingerprint"]) == 64 and isinstance(d["updated"], float)
    assert drafts.fingerprint(d) == d["fingerprint"]       # worked out again from what the window was given
    assert (await person.call("draft_get", id="d1")) == d


async def test_a_draft_from_another_account_by_id_address_or_name(person):
    assert (await person.call("draft", to="jo@family.example", body="x", account="a2"))["account"] == "a2"
    assert (await person.call("draft", to="jo@family.example", body="x", account="maya@acme.example"))["account"] == "a1"
    await person.fails("draft", "refused", to="jo@family.example", body="x", account="a3")
    await person.fails("draft", "no_account", to="jo@family.example", body="x", account="a9")


async def test_a_reply_answers_the_sender_with_re_and_says_what_it_answers(person):
    d = await reply_draft(person)
    assert d["kind"] == "reply" and d["reply_to"] == LAUNCH and d["account"] == "a1"
    assert d["to"] == [{"name": "Priya Shah", "email": "priya@acme.example"}] and d["cc"] == []
    assert d["subject"] == "Re: Launch date by noon?" and d["warnings"] == []


async def test_reply_all_answers_everyone_but_the_person_and_forward_has_no_one_yet(mail, person):
    mail.engine.add_message("maya@acme.example", "inbox", "Pat <pat@acme.example>", "Offsite", "x",
                            key="offsite@acme.example", to=["maya@acme.example", "Sam <sam@acme.example>"],
                            cc=["Leo <leo@acme.example>", "maya@acme.example", "sam@acme.example"])
    all_ = await person.call("draft", reply_to="a1/offsite@acme.example", kind="reply_all", body="ok")
    assert [a["email"] for a in all_["to"]] == ["pat@acme.example", "sam@acme.example"]
    assert [a["email"] for a in all_["cc"]] == ["leo@acme.example"] and all_["warnings"] == []
    fwd = await person.call("draft", reply_to="a1/offsite@acme.example", kind="forward", body="fyi")
    assert fwd["to"] == [] and fwd["subject"] == "Fwd: Offsite"
    again = await person.call("draft", reply_to=LAUNCH, kind="reply", subject="Re: My own subject", body="x")
    assert again["subject"] == "Re: My own subject"


async def test_answering_the_persons_own_mail_goes_to_whom_it_was_sent_to(person):
    d = await person.call("draft", reply_to=SENT_MAIL, body="Following up")
    assert d["to"] == [{"name": "Priya Shah", "email": "priya@acme.example"}] and d["subject"] == "Re: launch copy"


async def test_a_reply_where_the_mail_asks_for_answers_to_go_elsewhere_goes_there_and_says_so(mail, person):
    mail.engine.add_message("maya@acme.example", "inbox", "Boss <boss@acme.example>", "Budget", "x",
                            key="rt@acme.example", headers={"reply-to": "Collect <collect@outside.example>"})
    d = await person.call("draft", reply_to="a1/rt@acme.example", body="Here you go")
    assert [a["email"] for a in d["to"]] == [OUTSIDE]
    [w] = d["warnings"]
    assert w["kind"] == "other" and OUTSIDE in w["text"] and "boss@acme.example" in w["text"]


async def test_the_recipients_given_replace_those_the_mail_would_have_had(person):
    d = await person.call("draft", reply_to=LAUNCH, to=["sam@acme.example"], cc="leo@acme.example, jo@family.example",
                          bcc=[{"name": "Me", "email": "me@acme.example"}], body="x")
    assert [a["email"] for a in d["to"]] == ["sam@acme.example"]
    assert [a["email"] for a in d["cc"]] == ["leo@acme.example", "jo@family.example"]
    assert d["bcc"] == [{"name": "Me", "email": "me@acme.example"}]


@pytest.mark.parametrize("args, code", [
    ({"kind": "reply"}, "bad_request"), ({"kind": "new", "reply_to": LAUNCH}, "bad_request"),
    ({"reply_to": LAUNCH, "kind": "new"}, "bad_request"), ({"kind": "bounce", "reply_to": LAUNCH}, "bad_request"),
    ({"reply_to": "nope"}, "bad_request"), ({"reply_to": "a9/k"}, "no_account"), ({"reply_to": "a3/k"}, "refused"),
    ({"reply_to": "a1/not-a-mail@example.test"}, "not_found"),
    ({"to": ["nobody"]}, "bad_request"), ({"to": [f"u{i}@b.example" for i in range(101)]}, "bad_request"),
    ({"to": "x@y.example", "cc": [5]}, "bad_request"), ({"body": "x" * 500_001}, "bad_request"),
    ({"body": "é" * 400_000}, "bad_request"), ({"body": 5}, "bad_request"), ({"subject": "x" * 1000}, "bad_request"),
    ({"attachments": "a.txt"}, "bad_request"), ({"attachments": ["relative.txt"]}, "bad_request"),
    ({"attachments": [5]}, "bad_request"), ({"attachments": ["/nowhere/x.txt"]}, "not_found"),
    ({"attachments": ["/tmp/x"] * 21}, "bad_request"),
])
async def test_a_draft_asked_for_wrongly_is_said_and_makes_nothing(mail, person, args, code):
    base = {"to": "jo@family.example", "body": "x"} if "reply_to" not in args else {"body": "x"}
    await person.fails("draft", code, **{**base, **args})
    assert await person.call("list", view="drafts") == []
    assert not (mail.files / "drafts").exists() or list((mail.files / "drafts").iterdir()) == []


async def test_only_so_many_drafts_are_open_at_once(person, monkeypatch):
    monkeypatch.setattr(service, "MAX_DRAFTS", 3)
    for _ in range(3):
        await person.call("draft", to="jo@family.example", body="x")
    await person.fails("draft", "refused", to="jo@family.example", body="x")
    await person.call("draft_discard", id="d1")
    await person.call("draft", to="jo@family.example", body="x")


async def test_an_edit_changes_the_fingerprint_and_what_was_shown_is_no_longer_what_there_is(person):
    d = await ready(person)
    e = await person.call("draft_edit", id=d["id"], body="The 15th, actually.")
    assert e["body"] == "The 15th, actually." and e["fingerprint"] != d["fingerprint"] and e["updated"] >= d["updated"]
    assert drafts.fingerprint(e) == e["fingerprint"]
    await person.fails("send", "changed", id=d["id"], fingerprint=e["fingerprint"])   # the new one was never shown
    await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])   # the old one is not the draft
    same = await person.call("draft_edit", id=d["id"], body="The 15th, actually.")
    assert same["fingerprint"] == e["fingerprint"]                                   # the same words: the same print


async def test_an_edit_can_change_the_recipients_the_subject_and_the_attachments_and_nothing_else_moves(person, home):
    d = await reply_draft(person)
    f1, f2 = write(home, "one.txt", b"1"), write(home, "two.txt", b"22")
    e = await person.call("draft_edit", id=d["id"], to=["sam@acme.example"], cc=["leo@acme.example"],
                          subject="New subject\nhere", add_attachments=[str(f1), {"path": str(f2), "name": "second.txt"}])
    assert [a["email"] for a in e["to"]] == ["sam@acme.example"] and e["subject"] == "New subject here"
    assert [(a["name"], a["size"]) for a in e["attachments"]] == [("one.txt", 1), ("second.txt", 2)]
    assert e["kind"] == d["kind"] and e["reply_to"] == d["reply_to"] and e["account"] == d["account"]
    g = await person.call("draft_edit", id=d["id"], remove_attachments=["one.txt"], bcc=[])
    assert [a["name"] for a in g["attachments"]] == ["second.txt"]


async def test_a_failed_edit_changes_nothing_not_even_the_files_it_meant_to_drop(mail, person, home):
    d = await reply_draft(person)
    e = await person.call("draft_edit", id=d["id"], add_attachments=[str(write(home, "keep.txt", b"k"))])
    await person.fails("draft_edit", "not_found", id=d["id"], remove_attachments=["keep.txt"],
                       add_attachments=[str(home / "docs" / "missing.txt")], body="changed")
    after = await person.call("draft_get", id=d["id"])
    assert after == e and mail.draft_files(d["id"]) == ["keep.txt"]
    await person.fails("draft_edit", "bad_request", id=d["id"], remove_attachments="keep.txt")
    await person.fails("draft_edit", "bad_request", id=d["id"], add_attachments=["x"] * 21)
    assert mail.draft_files(d["id"]) == ["keep.txt"]


async def test_only_so_many_attachments_fit_on_one_draft(person, home):
    d = await reply_draft(person)
    files = [str(write(home, f"f{i}.txt", bytes([i]))) for i in range(drafts.ATTACHMENTS_MAX + 1)]
    await person.call("draft_edit", id=d["id"], add_attachments=files[:drafts.ATTACHMENTS_MAX])
    await person.fails("draft_edit", "bad_request", id=d["id"], add_attachments=files[-1:])
    e = await person.call("draft_edit", id=d["id"], add_attachments=files[-1:], remove_attachments=["f0.txt"])
    assert len(e["attachments"]) == drafts.ATTACHMENTS_MAX


async def test_editing_what_is_not_there_or_is_done_is_said(person, mail):
    await person.fails("draft_edit", "not_found", id="d99", body="x")
    await person.fails("draft_edit", "bad_request", id=None, body="x")
    await person.fails("draft_edit", "bad_request", id="d" * 40, body="x")
    d = await ready(person)
    await press(person, d)
    await person.fails("draft_edit", "refused", id=d["id"], body="too late")
    other = await reply_draft(person, to=SAM)
    await person.call("draft_discard", id=other["id"])
    await person.fails("draft_edit", "refused", id=other["id"], body="too late")


async def test_a_person_adding_a_recipient_types_it_and_an_agent_doing_it_does_not(mail, person, agent):
    d = await person.call("draft", to="jo@family.example", body="x")
    assert d["warnings"] == []
    e = await person.call("draft_edit", id=d["id"], cc=["new.person@elsewhere.example"])
    assert e["warnings"] == []
    f = await agent.call("draft_edit", id=d["id"], cc=["new.person@elsewhere.example", "other@elsewhere.example"])
    [w] = f["warnings"]
    assert w["kind"] == "new_address" and w["addresses"] == ["other@elsewhere.example"]


async def test_what_the_person_typed_is_what_a_draft_may_name_and_a_words_with_addresses_count(person):
    d = await person.call("draft", to=["x@y.example"], body="b", created_by="agent", typed="Send it to Kim@Work.example please")
    assert [w["kind"] for w in d["warnings"]] == ["new_address"] and d["warnings"][0]["addresses"] == ["x@y.example"]
    e = await person.call("draft_edit", id=d["id"], created_by="agent", to=["kim@work.example"])
    assert e["warnings"] == []


async def test_discarding_ends_the_draft_and_its_files_and_is_not_an_error_twice(mail, person, home):
    d = await reply_draft(person)
    await person.call("draft_edit", id=d["id"], add_attachments=[str(write(home, "a.txt"))])
    assert mail.draft_files(d["id"]) == ["a.txt"]
    assert await person.call("draft_discard", id=d["id"]) == {}
    assert await person.call("draft_discard", id=d["id"]) == {}
    assert mail.draft_files(d["id"]) == [] and await person.call("list", view="drafts") == []
    assert (await person.call("draft_get", id=d["id"]))["state"] == "discarded"
    await person.fails("draft_discard", "not_found", id="d99")
    await person.fails("draft_discard", "bad_request", id=5)
    sent = await ready(person)
    await press(person, sent)
    await person.fails("draft_discard", "refused", id=sent["id"])


async def test_a_draft_that_was_sent_is_there_with_its_receipt_and_not_in_the_list(person):
    d = await ready(person)
    await press(person, d)
    got = await person.call("draft_get", id=d["id"])
    assert got["state"] == "sent" and got["receipt"]["draft"] == d["id"]
    assert await person.call("list", view="drafts") == []
    await person.fails("draft_get", "not_found", id="d99")


async def test_the_window_says_which_draft_it_drew_and_only_the_draft_as_it_is_counts(person):
    d = await reply_draft(person)
    assert await person.call("draft_shown", id=d["id"], fingerprint=d["fingerprint"]) == {"id": d["id"], "shown": True}
    await person.fails("draft_shown", "changed", id=d["id"], fingerprint="0" * 64)
    await person.fails("draft_shown", "changed", id=d["id"])
    await person.fails("draft_shown", "bad_request", id=d["id"], fingerprint=5)
    await person.fails("draft_shown", "not_found", id="d99", fingerprint=d["fingerprint"])
    await person.fails("draft_shown", "bad_request", id=5, fingerprint=d["fingerprint"])
    e = await person.call("draft_edit", id=d["id"], body="new")
    await person.fails("draft_shown", "changed", id=d["id"], fingerprint=d["fingerprint"])   # drew the old one
    await person.call("draft_discard", id=d["id"])
    await person.fails("draft_shown", "refused", id=d["id"], fingerprint=e["fingerprint"])


async def test_locks_and_flights_do_not_pile_up(mail, person):
    for i in range(20):
        d = await reply_draft(person)
        await shown(person, d)
        await person.call("draft_edit", id=d["id"], body=f"b{i}")
        await person.call("draft_discard", id=d["id"])
        await person.ask("draft_shown", id=f"x{i}", fingerprint="f")
    assert mail.service._dlocks == {} and mail.service._flights == {}


# -- the press --

def calls(mail, op):
    return [c for c in mail.engine.calls if c[0] == op]


def nothing_went(mail):
    """No send was asked for, and no attachment was uploaded for one."""
    assert mail.engine.sent == [] and not calls(mail, "send") and not calls(mail, "blob")


async def state_of(c, draft):
    return (await c.call("draft_get", id=draft["id"]))["state"]


async def test_a_press_with_what_was_shown_sends_once_and_everything_that_follows_is_done(mail, person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="asks for a date")
    await person.call("subscribe")
    d = await ready(person, body="The 14th works.")
    got = await press(person, d)
    receipt = got["receipt"]
    assert got["already"] is False and receipt["draft"] == d["id"]
    assert receipt["line"].startswith("Sent to Priya") and "maya@acme.example" in receipt["line"]
    assert receipt["web"]["name"] == "Gmail" and receipt["web"]["url"].startswith("https://mail.google.com/")
    [sent] = mail.engine.sent
    assert (sent["kind"], sent["reply_to"], sent["subject"], sent["body"]) == (
        "reply", "launch-date-31@acme.example", "Re: Launch date by noon?", "The 14th works.")
    assert [a["email"] for a in sent["to"]] == ["priya@acme.example"] and sent["account"] == "fake-1-maya"
    assert (await person.push("sent"))["receipt"] == receipt
    after = await person.call("draft_get", id=d["id"])
    assert after["state"] == "sent" and after["receipt"] == receipt
    assert mail.draft_files(d["id"]) == []
    assert (await person.call("list", view="needs_reply"))["messages"] == []       # it was answered
    assert await person.call("list", view="drafts") == []
    assert len(calls(mail, "send")) == 1


async def test_pressing_a_draft_that_was_sent_gives_its_receipt_again_and_sends_nothing(mail, person):
    d = await ready(person)
    first = await press(person, d)
    again = await press(person, d)
    assert again == {"receipt": first["receipt"], "already": True}
    assert (await press(person, d, again=True))["already"] is True       # not even when the person says "again"
    assert len(mail.engine.sent) == 1


async def test_whom_a_message_went_to_is_known_afterwards_and_an_agents_draft_to_them_is_not_flagged(mail, person):
    d = await person.call("draft", to="new.friend@elsewhere.example", body="Hello")
    await shown(person, d)
    await press(person, d)
    assert await person.call("known", emails=["new.friend@elsewhere.example"]) == {
        "new.friend@elsewhere.example": True}
    agent = await mail.client(agent=True)
    e = await agent.call("draft", to="new.friend@elsewhere.example", body="Again")
    assert e["warnings"] == []


async def test_attachments_arrive_whole_and_under_their_names_even_when_they_take_several_chunks(mail, person, home):
    big = bytes(range(256)) * 5000
    assert len(big) > 3 * bridge.CHUNK
    files = [write(home, "big.bin", big), write(home, "report.pdf", b"%PDF-1.4 a tiny one\n"),
             write(home, "notes.txt", b"words")]
    d = await person.call("draft", to="jo@family.example", body="see attached", attachments=[str(f) for f in files])
    assert [a["name"] for a in d["attachments"]] == ["big.bin", "report.pdf", "notes.txt"]
    await shown(person, d)
    await press(person, d)
    [sent] = mail.engine.sent
    assert [a["name"] for a in sent["attachments"]] == ["big.bin", "report.pdf", "notes.txt"]
    assert [a["data"] for a in sent["attachments"]] == [big, b"%PDF-1.4 a tiny one\n", b"words"]
    assert [a["sha256"] for a in sent["attachments"]] == [a["sha256"] for a in d["attachments"]]
    assert [a["content_type"] for a in sent["attachments"]] == ["application/octet-stream", "application/pdf",
                                                              "text/plain"]
    assert len(calls(mail, "blob")) == -(-len(big) // bridge.CHUNK) + 2
    assert mail.draft_files(d["id"]) == []
    assert not list((mail.files / "inflight").glob("*")) if (mail.files / "inflight").exists() else True


# -- the press cannot be had with anything but what the person saw --

async def test_only_the_fingerprint_that_was_shown_and_is_current_can_be_pressed(mail, person):
    d = await reply_draft(person)
    fp = d["fingerprint"]
    await person.fails("send", "changed", id=d["id"], fingerprint=fp)        # never shown
    await shown(person, d)
    for wrong in ("0" * 64, "", None, 5, [fp], {"a": 1}, fp.upper(), fp + " ", fp[:-1]):
        await person.fails("send", "changed", id=d["id"], fingerprint=wrong)
    await person.fails("send", "changed", id=d["id"])
    assert await state_of(person, d) == "open"
    nothing_went(mail)
    e = await person.call("draft_edit", id=d["id"], body="Different words")      # changed after it was shown
    await person.fails("send", "changed", id=d["id"], fingerprint=fp)            # the old one is stale
    await person.fails("send", "changed", id=d["id"], fingerprint=e["fingerprint"])   # the new one was not shown
    nothing_went(mail)
    await shown(person, e)
    assert (await press(person, e))["already"] is False
    assert [m["body"] for m in mail.engine.sent] == ["Different words"]


async def test_an_edit_and_an_edit_back_is_the_same_fingerprint_and_still_has_to_be_shown_again(mail, person):
    d = await ready(person, body="First words")
    await person.call("draft_edit", id=d["id"], body="Second words")
    back = await person.call("draft_edit", id=d["id"], body="First words")
    assert back["fingerprint"] == d["fingerprint"]
    await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])
    nothing_went(mail)
    await shown(person, back)
    await press(person, back)


async def test_the_window_cannot_say_it_showed_a_draft_that_is_not_there_or_is_not_as_it_was(mail, person):
    d = await reply_draft(person)
    await person.fails("draft_shown", "changed", id=d["id"], fingerprint="0" * 64)
    await person.fails("send", "changed", id=d["id"], fingerprint="0" * 64)
    await person.fails("draft_shown", "not_found", id="d404", fingerprint=d["fingerprint"])
    await person.fails("send", "not_found", id="d404", fingerprint=d["fingerprint"])
    nothing_went(mail)


@pytest.mark.parametrize("damage", ["longer", "same_size", "gone", "symlink", "folder", "empty"])
async def test_an_attachment_that_is_not_the_file_that_was_attached_stops_the_press_before_anything_is_sent(
        mail, person, home, damage):
    f = write(home, "plan.txt", b"the plan")
    other = write(home, "other.txt", b"the plan")   # the same words: a link to it is still not the copy
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(f)])
    await shown(person, d)
    copy = mail.files / "drafts" / d["id"] / "plan.txt"
    copy.unlink()
    if damage == "longer":
        copy.write_bytes(b"the plan, changed")
    elif damage == "same_size":
        copy.write_bytes(b"the plaN")
    elif damage == "symlink":
        copy.symlink_to(other)
    elif damage == "folder":
        copy.mkdir()
    elif damage == "empty":
        copy.write_bytes(b"")
    sentence = await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])
    assert "plan.txt" in sentence
    assert await state_of(person, d) == "open"
    nothing_went(mail)


async def test_what_is_sent_is_the_copy_that_was_checked_and_not_the_file_that_was_attached(mail, person, home):
    f = write(home, "plan.txt", b"the plan")
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(f)])
    await shown(person, d)
    f.write_bytes(b"the original was changed afterwards")     # the person's own file: not what was attached
    await press(person, d)
    assert [a["data"] for a in mail.engine.sent[0]["attachments"]] == [b"the plan"]


async def test_a_draft_that_was_discarded_cannot_be_pressed(mail, person):
    d = await ready(person)
    await person.call("draft_discard", id=d["id"])
    await person.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
    nothing_went(mail)


async def test_a_draft_with_no_one_to_go_to_says_so_and_stays_open(mail, person):
    d = await person.call("draft", body="Dear nobody")
    await shown(person, d)
    assert "who" in await person.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
    assert await state_of(person, d) == "open"
    nothing_went(mail)


async def test_a_message_over_the_providers_limit_is_not_sent_and_says_how_big_it_is(mail, person, home, monkeypatch):
    monkeypatch.setitem(accts.PROVIDERS, "google", replace(accts.GOOGLE, size_limit=3_000_000))
    f = write(home, "scan.bin", b"\x01" * 3_000_000)
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(f)])
    await shown(person, d)
    sentence = await person.fails("send", "too_big", id=d["id"], fingerprint=d["fingerprint"])
    assert "4 MB" in sentence and "3 MB" in sentence and "Gmail" in sentence
    assert await state_of(person, d) == "open"
    nothing_went(mail)


async def test_a_message_too_long_for_one_frame_to_thunderbird_is_refused_before_sending_is_written(mail, person,
                                                                                                  monkeypatch):
    monkeypatch.setattr(bridge, "FRAME_MAX", 20_000)
    d = await person.call("draft", to="jo@family.example", body="x" * 30_000)
    await shown(person, d)
    await person.fails("send", "too_big", id=d["id"], fingerprint=d["fingerprint"])
    assert await state_of(person, d) == "open"          # it never became "sending": nobody has to look in Sent
    nothing_went(mail)


async def test_with_thunderbird_down_the_press_says_so_and_the_draft_waits_and_goes_when_it_is_back(mail, person):
    d = await ready(person)
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    await until(lambda: not mail.engine.connected)
    sentence = await person.fails("send", "engine_down", id=d["id"], fingerprint=d["fingerprint"])
    assert sentence
    assert await state_of(person, d) == "open"
    nothing_went(mail)
    mail.process.fail_start = None
    await until(lambda: mail.service.engine_state == "up")
    assert (await press(person, d))["already"] is False       # what was shown is still what was shown
    assert len(mail.engine.sent) == 1


async def test_a_send_the_engine_refuses_reopens_the_draft_and_it_can_be_pressed_again(mail, person):
    d = await ready(person)
    mail.engine.fail_send()
    sentence = await person.fails("send", "engine_error", id=d["id"], fingerprint=d["fingerprint"])
    assert "refused" in sentence and "Nothing was sent" in sentence
    assert await state_of(person, d) == "open" and mail.engine.sent == []
    mail.engine.send_ok()
    assert (await press(person, d))["already"] is False
    assert len(mail.engine.sent) == 1


async def test_a_send_that_is_answered_with_unknown_outcome_by_the_engine_is_unknown(mail, person):
    d = await ready(person)
    mail.engine.fail_send(code="unknown_outcome", sentence="Thunderbird lost track of it.")
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    assert await state_of(person, d) == "unknown"


# -- an agent cannot press --

async def test_a_process_in_an_agents_turn_cannot_press_and_the_refusal_is_logged_without_the_mail(mail, person,
                                                                                                  agent):
    d = await ready(person, body="WORDS THAT MUST NOT BE LOGGED")
    sentence = await agent.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
    assert "yours" in sentence.lower() and "Mail window" in sentence
    assert await state_of(person, d) == "open"
    nothing_went(mail)
    [row] = mail.press_rows()
    assert (row["kind"], row["code"], row["src"], row["ok"], row["id"]) == ("mail", "agent", "mail", False, d["id"])
    assert row["pid"] == os.getpid() and row["fingerprint"] == d["fingerprint"]
    assert "MUST NOT" not in json.dumps(row)
    await agent.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"], again=True)
    await agent.fails("send", "refused", id=5, fingerprint=None)             # nor with junk: it is still logged
    assert [r["code"] for r in mail.press_rows()] == ["agent", "agent", "agent"]


async def test_a_process_that_entered_an_agents_turn_after_it_connected_cannot_press_either(mail, person):
    d = await ready(person)
    mail.agent_flag = True                        # the turn began: this same connection is now in its scope
    try:
        await person.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
        await person.fails("draft_shown", "refused", id=d["id"], fingerprint=d["fingerprint"])
    finally:
        mail.agent_flag = False
    nothing_went(mail)
    assert (await press(person, d))["already"] is False       # and the same connection may, outside of a turn


async def test_an_agent_cannot_say_a_draft_was_shown_nor_do_what_is_the_persons_alone(mail, person, agent):
    d = await reply_draft(person)
    await agent.fails("draft_shown", "refused", id=d["id"], fingerprint=d["fingerprint"])
    await agent.fails("add_account", "refused", email="evil@outside.example")
    await agent.fails("remove_account", "refused", id="a1")
    for op, args in (("set_flags", {"id": LAUNCH, "read": True}), ("archive", {"id": LAUNCH}),
                     ("trash", {"id": LAUNCH}), ("save_attachment", {"id": INVOICE, "part": "1.2"}),
                     ("draft_discard", {"id": d["id"]}), ("engine_window", {"action": "stage"}), ("requested", {})):
        await agent.fails(op, "refused", **args)
    assert await state_of(person, d) == "open"
    assert len((await person.call("accounts"))["accounts"]) == 3
    assert ("stage", True) not in mail.process.calls
    assert (await person.call("list", view="all"))["messages"][0]["unread"] is True
    await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])   # and it was never shown


async def test_an_agent_may_read_and_search_and_mark_and_make_a_draft(agent):
    assert (await agent.call("search", text="launch"))["messages"]
    assert (await agent.call("read", id=LAUNCH))["message"]["id"] == LAUNCH
    await agent.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    d = await agent.call("draft", reply_to=LAUNCH, body="The 14th.")
    assert d["created_by"] == "agent"
    assert (await agent.call("draft_get", id=d["id"]))["id"] == d["id"]


# -- twice, and never twice --

async def test_two_presses_at_once_send_once_and_both_get_the_same_answer(mail, person):
    d = await ready(person)
    other = await mail.client()
    mail.engine.send_delay = 0.3
    one, two = await asyncio.gather(press(person, d), press(other, d))
    assert one == two and one["already"] is False
    assert len(mail.engine.sent) == 1 and len(calls(mail, "send")) == 1
    assert mail.service._flights == {}


async def test_a_second_press_with_another_fingerprint_while_one_is_going_is_changed(mail, person):
    d = await ready(person)
    other = await mail.client()
    mail.engine.send_delay = 0.3
    first = asyncio.create_task(press(person, d))
    await until(lambda: calls(mail, "send"))
    await other.fails("send", "changed", id=d["id"], fingerprint="0" * 64)
    assert (await first)["already"] is False
    assert len(mail.engine.sent) == 1


async def test_presses_pipelined_on_one_connection_send_once_and_the_second_is_the_first_again(mail, person):
    d = await ready(person)
    mail.engine.send_delay = 0.1
    one, two = await asyncio.gather(press(person, d), press(person, d))
    assert one["already"] is False and two == {"receipt": one["receipt"], "already": True}
    assert len(mail.engine.sent) == 1


async def test_a_press_and_an_edit_at_once_either_send_what_was_shown_or_are_refused(mail, person):
    d = await ready(person, body="Shown words")
    other = await mail.client()
    mail.engine.send_delay = 0.2
    sending = asyncio.create_task(press(person, d))
    await until(lambda: calls(mail, "send"))
    await other.fails("draft_edit", "refused", id=d["id"], body="Sneaky words")      # it is being sent
    await sending
    assert [m["body"] for m in mail.engine.sent] == ["Shown words"]


async def test_a_press_and_a_discard_at_once_do_not_both_happen(mail, person):
    d = await ready(person)
    other = await mail.client()
    mail.engine.send_delay = 0.2
    sending = asyncio.create_task(press(person, d))
    await until(lambda: calls(mail, "send"))
    await other.fails("draft_discard", "refused", id=d["id"])
    assert (await sending)["already"] is False
    assert await state_of(person, d) == "sent"


# -- a send that may or may not have gone --

async def test_a_send_nobody_answers_is_unknown_and_is_not_tried_again_unless_the_person_says_so(mail, person):
    d = await ready(person)
    mail.engine.hang_send(delivered=True)
    sentence = await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    assert "look in Sent" in sentence
    assert await state_of(person, d) == "unknown" and len(mail.engine.sent) == 1     # it did go
    assert len(calls(mail, "send")) == 1
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"], again=False)
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"], again="yes")
    assert len(calls(mail, "send")) == 1                     # nothing retried it
    mail.engine.send_ok()
    got = await press(person, d, again=True)
    assert got["already"] is False and await state_of(person, d) == "sent"
    assert len(mail.engine.sent) == 2 and len(calls(mail, "send")) == 2      # the person asked for the second


async def test_an_unknown_draft_can_be_changed_and_then_has_to_be_shown_again_before_another_try(mail, person):
    d = await ready(person)
    mail.engine.hang_send()
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    mail.engine.send_ok()
    e = await person.call("draft_edit", id=d["id"], body="Better words")
    assert e["state"] == "unknown"
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=e["fingerprint"])
    await person.fails("send", "changed", id=d["id"], fingerprint=e["fingerprint"], again=True)    # not shown yet
    await shown(person, e)
    assert (await press(person, e, again=True))["already"] is False


async def test_a_link_lost_after_the_request_was_written_is_unknown_and_thunderbird_comes_back(mail, person,
                                                                                                monkeypatch):
    monkeypatch.setattr(service, "LINK_WAIT_S", 0.3)    # the add-on does not reconnect by itself: Thunderbird restarts
    d = await ready(person)
    mail.engine.drop_on_send(delivered=False)
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    assert await state_of(person, d) == "unknown" and mail.engine.sent == []
    mail.engine.send_ok()
    await until(lambda: mail.service.engine_state == "up" and mail.engine.connected)
    assert ("restart",) in mail.process.calls or mail.process.calls.count(("start",)) >= 2
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])   # still the person's call
    assert len(calls(mail, "send")) == 1


async def test_a_link_lost_before_the_request_could_be_written_is_a_plain_no_and_the_draft_stays_open(mail, person):
    d = await ready(person)
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    await until(lambda: not mail.engine.connected)
    await person.fails("send", "engine_down", id=d["id"], fingerprint=d["fingerprint"])
    assert await state_of(person, d) == "open"


async def test_a_send_that_was_in_flight_when_the_service_is_stopped_is_unknown_when_it_starts_again(started, home):
    m = await Mail.start()
    c = await m.client()
    d = await ready(c)
    m.engine.hang_send(delivered=True)
    sending = asyncio.create_task(press(c, d))
    await until(lambda: calls(m, "send"))
    m.service.stop()
    await asyncio.wait_for(m.task, 10)
    sending.cancel()
    m.engine.send_ok()
    again = await Mail.start()
    started.append(again)
    c2 = await again.client()
    assert await state_of(c2, d) == "unknown"
    await c2.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    assert [(r["code"], r["id"], r["src"]) for r in again.press_rows()] == [("unknown_outcome", d["id"], "mail")]
    assert len(calls(again, "send")) == 0


async def test_a_service_that_is_killed_mid_send_finds_the_draft_unknown_and_says_so_in_the_press_log(started, home):
    m = await Mail.start()
    started.append(m)
    c = await m.client()
    d = await ready(c)
    m.engine.hang_send(delivered=True)
    sending = asyncio.create_task(press(c, d))
    await until(lambda: calls(m, "send"))
    await asyncio.sleep(0.05)
    state = home / "state"
    killed = home / "killed"
    killed.mkdir()
    for f in state.glob("mail.db*"):               # the files as a kill at this moment leaves them
        if f.suffix != ".lock":
            (killed / f.name).write_bytes(f.read_bytes())
    sending.cancel()
    second = Service(db_path=killed / "mail.db", socket_path=killed / "mail.sock", engine=fake.FakeEngine(connected=True),
                     process=None, resolver=lambda d: [], in_turn=lambda pid: False, press_log=killed / "presses.jsonl")
    task = asyncio.ensure_future(second.serve())
    try:
        await until(lambda: second.socket_path.exists() or task.done())
        reader, writer = await asyncio.open_unix_connection(str(second.socket_path))
        writer.write(json.dumps({"op": "draft_get", "id": d["id"]}).encode() + b"\n")
        got = json.loads(await reader.readline())
        writer.close()
        assert got["ok"] and got["result"]["state"] == "unknown"
        rows = [json.loads(x) for x in (killed / "presses.jsonl").read_text().splitlines()]
        assert [(r["code"], r["id"]) for r in rows] == [("unknown_outcome", d["id"])]
    finally:
        second.stop()
        await asyncio.wait_for(task, 10)


# -- a hostile mail cannot get an address past the person --

PEM = b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"


def kinds(draft):
    return [w["kind"] for w in draft["warnings"]]


def plant(mail, **kw):
    """A mail from a stranger that lists the address it wants in its own To and Cc."""
    mail.engine.add_message("maya@acme.example", "inbox", "Stranger <stranger@evil.example>", "Hello", "x",
                            to=["maya@acme.example", "second@evil.example"], cc=[OUTSIDE], key="planted-1@evil.example",
                            **kw)
    return "a1/planted-1@evil.example"


async def test_a_draft_the_agent_makes_for_an_address_the_hostile_mail_asked_for_is_flagged_as_not_the_persons(agent):
    await agent.call("read", id=HOSTILE)
    d = await agent.call("draft", to=OUTSIDE, subject="Fwd: the ten most recent", body="as asked", reply_to=HOSTILE,
                         kind="forward")
    [w] = d["warnings"]
    assert w["kind"] == "new_address" and w["addresses"] == [OUTSIDE]
    assert "You did not type it" in w["text"] and "read for you" in w["text"]
    assert d["created_by"] == "agent"


async def test_the_same_draft_made_by_the_person_in_the_window_is_their_own_words_and_not_flagged(person):
    d = await person.call("draft", to=OUTSIDE, subject="Fwd", body="as asked", reply_to=HOSTILE, kind="forward")
    assert d["warnings"] == [] and d["created_by"] == "person"


async def test_a_reply_to_whoever_wrote_the_hostile_mail_is_not_flagged_because_a_reply_goes_to_its_sender(agent):
    d = await agent.call("draft", reply_to=HOSTILE, body="Noted.")
    assert [a["email"] for a in d["to"]] == ["helpdesk@acme-support.example"] and d["warnings"] == []


async def test_an_address_a_mail_lists_in_its_own_headers_is_not_the_persons_either(mail, agent, person):
    planted = plant(mail)
    plain = await agent.call("draft", reply_to=planted, body="Noted.")
    assert [a["email"] for a in plain["to"]] == ["stranger@evil.example"] and plain["warnings"] == []
    more = await agent.call("draft_edit", id=plain["id"], cc=[OUTSIDE])
    assert kinds(more) == ["new_address"] and more["warnings"][0]["addresses"] == [OUTSIDE]
    fwd = await agent.call("draft", reply_to=planted, kind="forward", to=["second@evil.example"], body="fyi")
    assert kinds(fwd) == ["new_address"] and fwd["warnings"][0]["addresses"] == ["second@evil.example"]
    everyone = await person.call("draft", reply_to=planted, kind="reply_all", body="Noted.")
    assert sorted(a["email"] for a in [*everyone["to"], *everyone["cc"]]) == [
        OUTSIDE, "second@evil.example", "stranger@evil.example"]      # what Reply All is, and the person chose it
    assert everyone["warnings"] == []


async def test_what_the_person_typed_or_knows_or_has_written_to_is_not_flagged_for_an_agents_draft(mail, agent,
                                                                                                  person):
    known = await agent.call("draft", to="jo@family.example", body="x")              # in the address book
    assert known["warnings"] == []
    typed = await person.call("draft", to=OUTSIDE, body="x", created_by="agent",
                              typed=f"Please send this to {OUTSIDE.upper()}")          # agentd: the person's words
    assert typed["warnings"] == []
    stranger = await person.call("draft", to="nobody@elsewhere.example", body="x", created_by="agent",
                                 typed="Please send this to somebody")
    assert kinds(stranger) == ["new_address"]


async def test_an_agent_cannot_claim_to_be_the_person_nor_what_the_person_typed_nor_that_it_read_nothing(agent):
    d = await agent.call("draft", to=OUTSIDE, body="x", created_by="person", typed=f"send it to {OUTSIDE}",
                         tainted=False)
    assert d["created_by"] == "agent" and kinds(d) == ["new_address"]
    assert "read for you" in d["warnings"][0]["text"]
    e = await agent.call("draft_edit", id=d["id"], created_by="person", typed=f"also {OUTSIDE}", tainted=False,
                         to=[OUTSIDE, "other@elsewhere.example"])
    assert kinds(e) == ["new_address"] and e["warnings"][0]["addresses"] == [OUTSIDE, "other@elsewhere.example"]
    assert e["created_by"] == "agent"


async def test_a_bcc_is_flagged_like_any_other_recipient(agent):
    d = await agent.call("draft", to="jo@family.example", bcc=[OUTSIDE], body="x")
    assert kinds(d) == ["new_address"] and d["warnings"][0]["addresses"] == [OUTSIDE]


async def test_an_agent_adding_a_recipient_to_a_persons_draft_is_flagged_and_what_was_shown_is_stale(mail, person,
                                                                                                    agent):
    d = await person.call("draft", to="jo@family.example", body="x")
    await shown(person, d)
    e = await agent.call("draft_edit", id=d["id"], cc=[OUTSIDE])
    assert kinds(e) == ["new_address"] and e["fingerprint"] != d["fingerprint"]
    await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])
    await person.fails("send", "changed", id=d["id"], fingerprint=e["fingerprint"])          # not shown again
    nothing_went(mail)
    back = await person.call("draft_edit", id=d["id"], cc=[])      # the person looked and took it out
    assert back["warnings"] == [] and back["fingerprint"] == d["fingerprint"]


async def test_a_hostile_reply_to_sends_the_reply_there_and_says_so(mail, person, agent):
    mail.engine.add_message("maya@acme.example", "inbox", "Boss <boss@acme.example>", "Budget", "x",
                            headers={"reply-to": "Payments <pay@evil.example>"}, key="budget-1@acme.example")
    d = await agent.call("draft", reply_to="a1/budget-1@acme.example", body="Sure.")
    assert [a["email"] for a in d["to"]] == ["pay@evil.example"] and kinds(d) == ["other"]
    assert "boss@acme.example" in d["warnings"][0]["text"] and "pay@evil.example" in d["warnings"][0]["text"]
    by_person = await person.call("draft", reply_to="a1/budget-1@acme.example", body="Sure.")
    assert kinds(by_person) == ["other"]                    # the person is told too: it is not the sender


@pytest.mark.parametrize("where", [".ssh/id_rsa", ".aws/credentials", ".gnupg/secring.gpg", ".config/gcloud/creds.json",
                                   ".env", "credentials.json", "id_ed25519", "notes.kdbx", "passwords.txt",
                                   "logins.json", "cert.pem.key"])
async def test_an_agent_cannot_attach_what_looks_like_a_secret_and_a_person_is_told_when_they_do(mail, person, agent,
                                                                                                home, where):
    path = home / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real secret, only a file\n")
    sentence = await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(path)])
    assert path.name in sentence
    assert await person.call("list", view="drafts") == [] and mail.draft_files("d1") == []
    assert not (mail.files / "drafts").exists() or list((mail.files / "drafts").iterdir()) == []
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(path)])
    assert kinds(d) == ["sensitive_file"] and d["attachments"][0]["name"] == path.name
    e = await agent.fails("draft_edit", "refused", id=d["id"], add_attachments=[str(path)])
    assert [a["name"] for a in (await person.call("draft_get", id=d["id"]))["attachments"]] == [path.name]


async def test_a_key_in_a_file_with_an_innocent_name_is_not_attached_by_an_agent_and_flagged_for_a_person(person, agent,
                                                                                                        home):
    f = write(home, "holiday-plans.txt", b"x" * 20_000 + PEM)        # past where a first look would stop
    await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(f)])
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(f)])
    assert kinds(d) == ["sensitive_file"]


async def test_an_agent_cannot_attach_bombadils_own_files_nor_a_link_to_a_secret(mail, agent, home):
    mine = mail.files / "drafts" / "d777" / "x.txt"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"another draft's copy")
    await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(mine)])
    await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(home / "state" / "mail.db")])
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_bytes(b"not a real secret")
    link = home / "docs" / "harmless.txt"
    link.symlink_to(home / ".ssh" / "id_rsa")
    await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(link)])


async def test_what_a_hostile_mail_says_in_its_name_does_not_change_who_a_draft_is_from(agent):
    d = await agent.call("draft", to="jo@family.example", body="x", reply_to=HOSTILE)
    assert d["from"] == {"name": "Maya Reyes", "email": "maya@acme.example"}


async def test_a_draft_with_a_warning_can_still_be_sent_by_the_person_who_has_read_it(mail, person, agent):
    d = await agent.call("draft", to=OUTSIDE, body="Noted.")
    assert kinds(d) == ["new_address"]
    await shown(person, d)
    assert (await press(person, d))["already"] is False
    assert [a["email"] for a in mail.engine.sent[0]["to"]] == [OUTSIDE]

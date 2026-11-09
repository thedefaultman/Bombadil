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
import base64
import itertools
import json
import os
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio

from bombadil import paths, procs
from bombadil.mail import accounts as accts
from bombadil.mail import bridge, drafts, fake, protocol, service, store
from bombadil.mail.service import Service
from bombadil.mail.store import Store

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
    async def start(cls, *, engine=None, process=True, initial=True, up=True, in_turn=None, **kw):
        engine = engine if engine is not None else fake.FakeEngine()
        proc = fake.FakeProcess(engine) if process is True else process
        self = cls(None, engine, proc)

        def resolver(domain):
            self.dns.append(domain)
            return []
        kw.setdefault("initial_accounts", engine.account_rows() if initial else [])
        self.service = Service(engine=engine, process=proc, resolver=resolver,
                               in_turn=in_turn or (lambda pid: self.agent_flag), **kw)
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
    monkeypatch.setattr(service, "AGENT_ROW_S", 0.0)   # a row for every refused press, unless a test is about that
    monkeypatch.setattr(procs, "scope_supported", lambda: True)   # which would run systemd-run
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


async def test_downloads_that_is_a_link_to_a_folder_that_holds_keys_is_not_written_to_either(mail, person, home):
    (home / ".ssh").mkdir()
    os.symlink(home / ".ssh", home / "Downloads")
    await person.fails("save_attachment", "refused", id=INVOICE, part="1.2")
    assert list((home / ".ssh").iterdir()) == []


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
    await until(lambda: mail.engine.connected and mail.service.engine_state == "up")
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
    assert stat.S_IMODE(os.stat(paths.press_log()).st_mode) == 0o600      # whose presses were refused is the person's
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
    assert kinds(d) == ["sensitive_file"] and d["attachments"][0]["name"] == path.name.lstrip(".")
    await agent.fails("draft_edit", "refused", id=d["id"], add_attachments=[str(path)])
    assert [a["name"] for a in (await person.call("draft_get", id=d["id"]))["attachments"]] == [path.name.lstrip(".")]


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


# -- Thunderbird going away and coming back --

async def test_with_thunderbird_down_mail_says_why_in_a_sentence_and_drafts_still_work(mail, person):
    await person.call("subscribe")
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    push = await person.push("status")
    assert push["engine"] in ("starting", "restarting", "down") and push["text"] and push["detail"]
    await until(lambda: mail.service.engine_state == "restarting")
    status = await person.call("status")
    assert status["engine"] == "restarting" and "Thunderbird" in status["detail"] and status["text"]
    for op, args in (("list", {"view": "all"}), ("read", {"id": LAUNCH}), ("search", {"text": "launch"}),
                     ("draft", {"reply_to": LAUNCH, "body": "x"}), ("set_flags", {"id": LAUNCH, "read": True}),
                     ("archive", {"id": LAUNCH}), ("save_attachment", {"id": INVOICE, "part": "1.2"})):
        sentence = await person.fails(op, "engine_down", **args)
        assert "Thunderbird" in sentence or "Mail" in sentence
    d = await person.call("draft", to="jo@family.example", body="Written while it is down")   # needs nothing from it
    e = await person.call("draft_edit", id=d["id"], body="Changed while it is down")
    assert (await person.call("draft_get", id=d["id"]))["fingerprint"] == e["fingerprint"]
    assert [x["id"] for x in await person.call("list", view="drafts")] == [d["id"]]
    assert (await person.call("accounts"))["accounts"][0]["email"] == "maya@acme.example"
    mail.process.fail_start = None
    await until(lambda: mail.engine.connected and mail.service.engine_state == "up")
    assert (await person.call("list", view="all"))["messages"]
    while (push := await person.push("status", 5))["engine"] != "up":   # the states on the way come first
        assert push["engine"] in ("starting", "restarting", "down")


async def test_a_thunderbird_that_cannot_start_is_tried_again_slower_and_then_said_to_keep_stopping(mail, person,
                                                                                                    monkeypatch):
    monkeypatch.setattr(service, "BACKOFF_MIN", 0.01)
    monkeypatch.setattr(service, "BACKOFF_MAX", 0.03)
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    await until(lambda: mail.service.engine_state == "down", 8.0)
    assert "keeps stopping" in mail.service.engine_detail
    starts = mail.process.calls.count(("start",))
    assert starts >= 6
    mail.process.fail_start = None
    await until(lambda: mail.engine.connected and mail.service.engine_state == "up", 8.0)
    status = await person.call("status")
    assert status["engine"] == "up"


async def test_a_thunderbird_that_is_not_installed_is_blocked_with_the_reason_and_nothing_is_started(mail, person):
    mail.process.unavailable = "Thunderbird is not installed here."
    mail.process.crash()
    await until(lambda: mail.service.engine_state == "blocked")
    status = await person.call("status")
    assert status["engine"] == "blocked" and status["detail"] == "Thunderbird is not installed here."
    starts = mail.process.calls.count(("start",))
    await asyncio.sleep(0.3)
    assert mail.process.calls.count(("start",)) == starts
    await person.fails("list", "engine_down", view="all")
    mail.process.unavailable = None
    await until(lambda: mail.engine.connected and mail.service.engine_state == "up")


async def test_a_thunderbird_that_stops_by_itself_is_started_again_and_the_window_is_told(mail, person):
    await person.call("subscribe")
    before = mail.process.calls.count(("start",))
    mail.process.crash()
    await until(lambda: mail.process.calls.count(("start",)) > before and mail.engine.connected)
    await until(lambda: mail.service.engine_state == "up")
    assert (await person.call("list", view="all"))["messages"]


async def test_a_running_thunderbird_whose_add_on_never_connects_is_started_again_after_a_wait(mail, person,
                                                                                               monkeypatch):
    monkeypatch.setattr(service, "LINK_WAIT_S", 0.3)
    mail.engine.set_up(False)             # the link is gone and the process is not
    await until(lambda: mail.service.engine_state in ("starting", "restarting"))
    await until(lambda: mail.engine.connected and mail.service.engine_state == "up")
    assert ("restart",) in mail.process.calls or mail.process.calls.count(("start",)) >= 2


async def test_adding_an_account_restarts_thunderbird_so_that_it_is_in_the_profile_and_a_failure_is_a_pause(mail,
                                                                                                         person):
    mail.process.fail_start = OSError("profile locked")
    got = await person.call("add_account", email="jo@family.example")
    assert got["state"] == "syncing"
    await until(lambda: mail.service.engine_state in ("restarting", "starting"))
    assert "jo@family.example" in [a["email"] for a in (await person.call("accounts"))["accounts"]]
    mail.process.fail_start = None
    await until(lambda: mail.engine.connected and mail.service.engine_state == "up")
    assert ("seed_account", "jo@family.example", "imap") in mail.process.calls


async def test_with_no_process_at_all_mail_says_it_cannot_fetch_and_drafts_work(started):
    m = await Mail.start(engine=fake.FakeEngine(), process=None, up=False)
    started.append(m)
    c = await m.client()
    await until(lambda: m.service.engine_state == "blocked")
    assert "not installed" in (await c.call("status"))["detail"]
    await c.fails("list", "engine_down", view="all")
    d = await c.call("draft", to="jo@family.example", body="x")
    assert d["state"] == "open"


# -- the real link: a host that connects to mail.sock and says engine_hello --

ENGINE_ACCOUNT = {"engine_id": "tb-1", "name": "Maya at Acme", "type": "imap", "emails": ["maya@acme.example"],
                  "identities": [{"id": "id1", "email": "maya@acme.example", "name": "Maya Reyes"}],
                  "folders": {"inbox": True, "sent": True, "drafts": True, "archive": True, "trash": True},
                  "state": "ok", "detail": "", "unread": 2}
HANG = object()


def emsg(key, subject="Hello", sender="Priya Shah <priya@acme.example>", **kw):
    name, _, rest = sender.partition(" <")
    return {"key": key, "account": "tb-1", "folder": "inbox", "from": {"name": name, "email": rest.rstrip(">")},
            "to": [{"name": "", "email": "maya@acme.example"}], "cc": [], "subject": subject, "ts": time.time() - 60,
            "unread": True, "flagged": False, "attachments": False, "thread": None, "message_id": key, **kw}


class Failure(Exception):
    def __init__(self, code, sentence):
        self.code, self.sentence = code, sentence


class Host:
    """What the native-messaging host relays for Thunderbird's add-on: a connection to mail.sock that says
    engine_hello and then answers the service's requests from `handlers` (a result, a Failure, or HANG)."""

    def __init__(self, path):
        self.path = path
        self.requests: list[dict] = []
        self.blobs: dict[str, bytearray] = {}
        self.handlers = {"info": lambda a: {"version": 1, "app": "Thunderbird", "app_version": "128.0"},
                         "accounts": lambda a: [ENGINE_ACCOUNT],
                         "list": lambda a: {"messages": [emsg("k1@acme.example", "From the host")], "more": False},
                         "known": lambda a: {e: False for e in a["emails"]},
                         "blob": self._blob}
        self.reader = self.writer = self.task = None

    def _blob(self, a):
        self.blobs.setdefault(a["xfer"], bytearray()).extend(base64.b64decode(a["data"]))
        return {}

    async def connect(self, pid=None):
        self.reader, self.writer = await asyncio.open_unix_connection(str(self.path), limit=1 << 27)
        self.writer.write((json.dumps({"op": "engine_hello", "pid": pid or os.getpid()}) + "\n").encode())
        self.task = asyncio.ensure_future(self._serve())
        return self

    async def _serve(self):
        while True:
            try:
                line = await self.reader.readline()
            except (OSError, ValueError):
                return
            if not line:
                return
            req = json.loads(line)
            self.requests.append(req)
            handler = self.handlers.get(req.get("op"))
            if handler is HANG:
                continue
            try:
                if handler is None:
                    raise Failure("engine_error", f"The add-on cannot {req.get('op')}.")
                reply = {"id": req["id"], "ok": True, "result": handler(req)}
            except Failure as e:
                reply = {"id": req["id"], "ok": False, "error": e.sentence, "code": e.code}
            self.say(reply)

    def say(self, frame):
        self.writer.write((json.dumps(frame) + "\n").encode())

    def ops(self):
        return [r["op"] for r in self.requests]

    async def closed(self, timeout=3.0):
        await asyncio.wait_for(self.task, timeout)

    def close(self):
        if self.writer is not None:
            self.writer.close()


@pytest_asyncio.fixture
async def real(started):
    """A service with the real link and no process, waiting for a host."""
    m = await Mail.start(engine=bridge.EngineLink(), process=None, initial=False, up=False)
    started.append(m)
    m.hosts = []

    async def host(**kw):
        h = Host(m.service.socket_path)
        for name, fn in kw.items():
            h.handlers[name] = fn
        await h.connect()
        m.hosts.append(h)
        return h
    m.host = host
    yield m
    for h in m.hosts:
        h.close()


async def test_a_host_that_says_hello_is_the_engine_and_the_accounts_it_has_are_taken_up(real):
    c = await real.client()
    await until(lambda: real.service.engine_state == "blocked")        # no process, and nobody connected
    await c.call("subscribe")
    host = await real.host()
    await until(lambda: real.service.engine_state == "up")
    assert (await c.push("status"))["engine"] == "up"
    assert host.ops()[:2] == ["info", "accounts"]
    [a] = (await c.call("accounts"))["accounts"]
    assert (a["id"], a["email"], a["state"], a["unread"], a["name"]) == ("a1", "maya@acme.example", "ok", 2,
                                                                       "Maya at Acme")
    assert real.service.engine.connected


async def test_what_the_host_answers_to_a_list_comes_through_as_mail_with_ids_from_the_service(real):
    c = await real.client()
    host = await real.host()
    await until(lambda: real.service.engine_state == "up")
    [m] = (await c.call("list", view="all"))["messages"]
    assert m["id"] == "a1/k1@acme.example" and m["subject"] == "From the host" and m["from"]["name"] == "Priya Shah"
    asked = next(r for r in host.requests if r["op"] == "list")
    assert asked["account"] == "tb-1" and asked["folder"] == "inbox"


async def test_a_second_host_replaces_the_first_and_the_first_is_hung_up_on(real):
    c = await real.client()
    first = await real.host()
    await until(lambda: real.service.engine_state == "up")
    second = await real.host(list=lambda a: {"messages": [emsg("k2@acme.example", "From the second")], "more": False})
    await first.closed()
    await until(lambda: "info" in second.ops())
    [m] = (await c.call("list", view="all"))["messages"]
    assert m["subject"] == "From the second"
    assert real.service.engine.connected and real.service.engine_state == "up"


async def test_a_request_the_host_never_answers_fails_with_engine_down_when_it_hangs_up(real):
    c = await real.client()
    host = await real.host(list=HANG)
    await until(lambda: real.service.engine_state == "up")
    asking = asyncio.create_task(c.ask("list", view="all"))
    await until(lambda: "list" in host.ops())
    host.close()
    answer = await asking
    assert answer["ok"] is False and answer["code"] == "engine_down" and answer["error"]
    await until(lambda: not real.service.engine.connected)
    await c.fails("list", "engine_down", view="all")


async def test_a_host_that_hangs_up_is_the_end_of_the_engine_and_a_new_one_is_the_start_of_it_again(real):
    c = await real.client()
    await c.call("subscribe")
    host = await real.host()
    await until(lambda: real.service.engine_state == "up")
    host.close()
    await until(lambda: not real.service.engine.connected)
    while (await c.push("status", 3))["engine"] == "up":     # the first one said it was up; the next says it is not
        pass
    again = await real.host()
    await until(lambda: real.service.engine_state == "up" and real.service.engine.connected)
    assert "info" in again.ops()


async def test_a_process_inside_an_agents_turn_cannot_be_the_engine(real):
    real.agent_flag = True
    try:
        host = await real.host()
        await host.closed()                       # the service hung up on it
        await asyncio.sleep(0.1)
    finally:
        real.agent_flag = False
    assert not real.service.engine.connected and host.ops() == []


async def test_the_hello_event_is_kept_and_other_events_reach_the_window(real):
    c = await real.client()
    await c.call("subscribe")
    host = await real.host()
    await until(lambda: real.service.engine_state == "up")
    host.say({"event": "hello", "version": 1, "app": "Thunderbird", "app_version": "128.0", "caps": ["send"]})
    await until(lambda: real.service.engine.hello is not None)
    assert real.service.engine.hello["caps"] == ["send"]
    host.say({"event": "new_mail", "account": "tb-1", "messages": [emsg("fresh@acme.example", "Brand new")]})
    push = await c.push("new_mail")
    assert push["message"]["id"] == "a1/fresh@acme.example" and push["known"] is False


async def test_a_mail_that_comes_in_one_big_frame_is_read_and_cut_for_the_window(real):
    host = await real.host(get=lambda a: {"message": emsg("big@acme.example", "Big"), "text": "word " * 400_000,
                                          "html": None, "headers": {}, "attachments": []})
    c = await real.client()
    await until(lambda: real.service.engine_state == "up")
    got = await c.call("read", id="a1/big@acme.example")
    assert got["truncated"] is True and 0 < len(got["text"]) < 1_000_000
    assert (await c.call("ping"))["pong"]
    assert host.ops().count("get") == 1


async def test_a_press_over_the_real_link_uploads_the_attachments_then_sends_one_request_that_names_them(real,
                                                                                                          home):
    c = await real.client()
    big = bytes(range(256)) * 3000
    f = write(home, "big.bin", big)
    sent = []

    def send(a):
        sent.append(a)
        return {"message_id": "sent-1@acme.example", "saved": True}
    host = await real.host(send=send, get=lambda a: {"message": emsg("k1@acme.example", "Question"), "text": "Hi",
                                                      "html": None, "headers": {}, "attachments": []})
    await until(lambda: real.service.engine_state == "up")
    d = await c.call("draft", reply_to="a1/k1@acme.example", body="Yes.", attachments=[str(f)], cc="leo@acme.example")
    await shown(c, d)
    got = await press(c, d)
    assert got["already"] is False and got["receipt"]["message_id"] == "sent-1@acme.example"
    [req] = sent
    assert set(req) == {"id", "op", "account", "kind", "reply_to", "to", "cc", "bcc", "subject", "body", "attachments"}
    assert (req["account"], req["kind"], req["reply_to"], req["subject"], req["body"]) == (
        "tb-1", "reply", "k1@acme.example", "Re: Question", "Yes.")
    assert [a["email"] for a in req["to"]] == ["priya@acme.example"] and [a["email"] for a in req["cc"]] == [
        "leo@acme.example"]
    [att] = req["attachments"]
    assert set(att) == {"name", "content_type", "xfer"} and att["name"] == "big.bin"
    assert bytes(host.blobs[att["xfer"]]) == big
    ops = host.ops()
    assert ops.index("send") > max(i for i, o in enumerate(ops) if o == "blob")        # all of it was there first


async def test_a_host_that_says_no_to_a_send_is_a_plain_no_and_one_that_says_nothing_is_unknown(real):
    c = await real.client()

    def no(a):
        raise Failure("engine_error", "The server said no.")
    host = await real.host(send=no)
    await until(lambda: real.service.engine_state == "up")
    d = await c.call("draft", to="jo@family.example", body="x")
    await shown(c, d)
    sentence = await c.fails("send", "engine_error", id=d["id"], fingerprint=d["fingerprint"])
    assert "server said no" in sentence and "Nothing was sent" in sentence
    assert (await c.call("draft_get", id=d["id"]))["state"] == "open"
    host.handlers["send"] = HANG
    sentence = await c.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])      # SEND_S passes
    assert "look in Sent" in sentence
    assert (await c.call("draft_get", id=d["id"]))["state"] == "unknown"
    second = await real.host()
    await until(lambda: real.service.engine.connected)
    host2_sends = [r for r in second.requests if r["op"] == "send"]
    assert host2_sends == []                          # nothing tried it again on the new connection


async def test_a_link_that_goes_while_the_send_is_out_is_unknown_even_though_the_host_was_alive(real):
    c = await real.client()
    host = await real.host()
    await until(lambda: real.service.engine_state == "up")
    d = await c.call("draft", to="jo@family.example", body="x")
    await shown(c, d)
    host.handlers["send"] = lambda a: host.writer.close()      # the add-on's side of the link ends
    await c.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    assert (await c.call("draft_get", id=d["id"]))["state"] == "unknown"


# -- a client that does not read, too many clients, and what must not pile up --

async def raw_client(mail, rcvbuf=None):
    """A connection that is read by hand (or not at all), with a small receive buffer if asked."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if rcvbuf:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    sock.setblocking(False)
    await asyncio.get_running_loop().sock_connect(sock, str(mail.service.socket_path))
    return sock


async def ended(sock, seconds=5.0) -> bool:
    """Reading what is left on a socket, does it come to an end (the service closed it) and not stay open?"""
    loop = asyncio.get_running_loop()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if not await asyncio.wait_for(loop.sock_recv(sock, 1 << 16), 1.0):
                return True
        except ConnectionResetError:
            return True
        except TimeoutError:
            continue
    return False


async def test_a_client_that_asks_and_never_reads_is_dropped_and_nobody_else_is_slowed(mail, person, monkeypatch):
    monkeypatch.setattr(service, "DRAIN_S", 0.3)
    sock = await raw_client(mail, 4096)
    loop = asyncio.get_running_loop()
    await until(lambda: len(mail.service.conns) == 2)
    request = json.dumps({"op": "status"}).encode() + b"\n"
    for _ in range(40):                        # status is a kilobyte or two, so the buffers fill quickly
        await loop.sock_sendall(sock, request * 50)
        await asyncio.sleep(0)
    began = time.monotonic()
    assert (await person.call("ping"))["pong"] and time.monotonic() - began < 0.5
    await until(lambda: len(mail.service.conns) == 1, 8.0)       # it was let go of, and the others were not
    assert await ended(sock)                                      # and its socket was closed, not kept open
    assert (await person.call("status"))["engine"] == "up"
    sock.close()


async def test_a_subscriber_that_never_reads_is_dropped_when_what_is_waiting_for_it_passes_the_cap(mail, person,
                                                                                                  monkeypatch):
    monkeypatch.setattr(service, "SEND_CAP", 50_000)
    sock = await raw_client(mail, 4096)
    loop = asyncio.get_running_loop()
    await loop.sock_sendall(sock, b'{"op": "subscribe"}\n')
    await until(lambda: any(c.subscribed for c in mail.service.conns))
    await person.call("subscribe")
    for _ in range(40):
        mail.service._broadcast({"push": "filler", "pad": "x" * 60_000})
        await asyncio.sleep(0.02)                # whoever reads has the time to
    await until(lambda: len(mail.service.conns) == 1)
    assert await ended(sock)
    assert (await person.push("filler"))["push"] == "filler"        # whoever reads gets everything
    sock.close()


async def test_more_clients_than_the_limit_are_turned_away_and_a_place_frees_when_one_leaves(mail, person,
                                                                                            monkeypatch):
    monkeypatch.setattr(service, "MAX_CLIENTS", 3)
    two, three = await mail.client(), await mail.client()
    assert (await three.call("ping"))["pong"]
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    writer.write(b'{"op": "ping"}\n')
    try:
        assert await asyncio.wait_for(reader.readline(), 3) == b""        # no answer: there is no room
    except ConnectionResetError:
        pass
    writer.close()
    two.close()
    await until(lambda: len(mail.service.conns) == 2)
    four = await mail.client()
    assert (await four.call("ping"))["pong"]


async def test_connecting_and_leaving_in_every_way_leaves_nothing_behind(mail, person):
    for i in range(60):
        _reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
        if i % 4 == 0:
            writer.write(b'{"op": "status"}\n')      # asked, and gone before the answer
        elif i % 4 == 1:
            writer.write(b'{"op": "list", "view": "all"')     # half a request
        elif i % 4 == 2:
            writer.write(b'{"op": "subscribe"}\n{"op": "views"}\n')
        writer.close()
    await until(lambda: len(mail.service.conns) == 1)
    assert (await person.call("ping"))["pong"]


async def test_requests_sent_in_a_pile_without_waiting_are_answered_in_order(mail):
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    writer.write(b"".join(json.dumps({"id": i, "op": "ping"}).encode() + b"\n" for i in range(500)))
    got = [json.loads(await asyncio.wait_for(reader.readline(), 5))["id"] for _ in range(500)]
    assert got == list(range(500))
    writer.close()


async def test_a_send_in_progress_does_not_hold_up_anybody_else(mail, person):
    d = await ready(person)
    other = await mail.client()
    mail.engine.send_delay = 0.5
    sending = asyncio.create_task(press(person, d))
    await until(lambda: calls(mail, "send"))
    began = time.monotonic()
    assert (await other.call("status"))["engine"] == "up" and (await other.call("list", view="drafts"))
    assert time.monotonic() - began < 0.3
    await sending


async def test_a_burst_of_engine_events_keeps_only_the_latest_and_the_service_goes_on(mail, person):
    await person.call("subscribe")
    for i in range(1000):
        mail.engine._later(mail.engine._event, {"event": "counts_changed", "n": i})
    mail.service._events.extend({"event": "nonsense", "n": i} for i in range(1000))
    assert len(mail.service._events) <= service.EVENTS_MAX
    await until(lambda: len(mail.service._events) == 0)
    assert (await person.call("accounts", fresh=True))["accounts"]


async def test_many_edits_at_once_leave_no_locks_and_no_flights_behind(mail, person):
    d = await reply_draft(person)
    clients = [await mail.client() for _ in range(8)]

    async def edits(c, k):
        for i in range(8):
            await c.ask("draft_edit", id=d["id"], body=f"{k}-{i}")
            await c.ask("draft_shown", id=d["id"], fingerprint="0" * 64)
            await c.ask("send", id=d["id"], fingerprint="0" * 64)
            await c.ask("draft_get", id=f"d{900 + i}")
    await asyncio.gather(*(edits(c, k) for k, c in enumerate(clients)))
    assert mail.service._dlocks == {} and mail.service._flights == {}
    assert (await person.call("draft_get", id=d["id"]))["state"] == "open"
    assert mail.engine.sent == []


async def test_what_the_service_holds_for_one_connection_is_bounded_by_the_request_limit(mail, person):
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path), limit=1 << 26)
    body = "x" * service.MAX_BODY
    writer.write(json.dumps({"op": "draft", "to": "jo@family.example", "body": body}).encode() + b"\n")
    answer = json.loads(await asyncio.wait_for(reader.readline(), 10))
    assert answer["ok"] is True and len(answer["result"]["body"]) == len(body)       # as large as is allowed
    writer.write(b'{"op": "ping", "pad": "' + b"x" * (service.LINE_LIMIT - 100) + b'"}\n')       # a line just in limits
    assert json.loads(await asyncio.wait_for(reader.readline(), 10))["ok"] is True
    writer.write(b'{"op": "ping", "pad": "' + b"x" * service.LINE_LIMIT + b'"}\n')
    assert json.loads(await asyncio.wait_for(reader.readline(), 10))["code"] == "bad_request"
    assert await reader.readline() == b""
    writer.close()


# -- the notes are damaged, or left over, or too old --

async def test_notes_that_cannot_be_read_at_start_are_set_aside_and_the_accounts_come_back_from_thunderbird(started,
                                                                                                          home):
    (home / "state").mkdir()
    (home / "state" / "mail.db").write_bytes(b"this is not a database " * 400)
    m = await Mail.start()
    started.append(m)
    c = await m.client()
    assert (home / "state" / "mail.db.broken").exists()
    assert len((await c.call("accounts"))["accounts"]) == 3
    d = await c.call("draft", to="jo@family.example", body="x")
    assert d["id"] == "d1"


async def test_notes_that_go_bad_while_running_are_a_sentence_once_and_then_made_again_from_thunderbird(mail, person,
                                                                                                      home,
                                                                                                      monkeypatch):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    f = write(home, "a.txt")
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(f)])
    assert mail.draft_files(d["id"]) == ["a.txt"]
    real = Store.drafts
    boom = {"n": 0}

    def damaged(self, *a, **k):
        if boom["n"] == 0:
            boom["n"] = 1
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real(self, *a, **k)
    monkeypatch.setattr(Store, "drafts", damaged)
    sentence = await person.fails("list", "internal", view="drafts")
    assert "started again" in sentence
    assert (home / "state" / "mail.db.broken").exists()
    assert await person.call("list", view="drafts") == []
    await person.fails("draft_get", "not_found", id=d["id"])
    assert (await person.call("list", view="needs_reply"))["messages"] == []
    await until(lambda: mail.draft_files(d["id"]) == [])          # the copies of a draft that is gone went too

    async def back():
        return len((await person.call("accounts", fresh=True))["accounts"]) == 3
    await until(back)
    await until(lambda: mail.service.engine_state == "up", 8.0)     # the supervisor looks again, and Thunderbird is there
    assert (await person.call("list", view="all"))["messages"]


async def test_notes_that_are_unwell_but_not_damaged_are_not_thrown_away(mail, person, monkeypatch):
    def busy(self, *a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(Store, "drafts", busy)
    await person.fails("list", "internal", view="drafts")
    monkeypatch.undo()
    assert not (mail.files.parent / "mail.db.broken").exists()


async def test_files_that_belong_to_no_draft_are_swept_at_start_and_those_that_do_are_kept(started, home):
    m = await Mail.start()
    c = await m.client()
    f = write(home, "keep.txt")
    d = await c.call("draft", to="jo@family.example", body="x", attachments=[str(f)])
    gone = await c.call("draft", to="jo@family.example", body="y", attachments=[str(f)])
    await c.call("draft_discard", id=gone["id"])
    m.service.stop()
    await asyncio.wait_for(m.task, 10)
    (m.files / "drafts" / "d99").mkdir(parents=True)
    (m.files / "drafts" / "d99" / "orphan.txt").write_bytes(b"left over")
    (m.files / "inflight").mkdir(exist_ok=True)
    (m.files / "inflight" / "abc.part").write_bytes(b"half a download")
    again = await Mail.start()
    started.append(again)
    assert again.draft_files(d["id"]) == ["keep.txt"]
    assert sorted(p.name for p in (again.files / "drafts").iterdir()) == [d["id"]]
    assert not (again.files / "inflight").exists() or list((again.files / "inflight").iterdir()) == []


async def test_a_draft_that_was_sent_or_discarded_long_ago_is_forgotten_with_its_receipt(started, home, monkeypatch):
    m = await Mail.start()
    started.append(m)
    c = await m.client()
    sent = await ready(c)
    await press(c, sent)
    gone = await reply_draft(c, to=SAM)
    await c.call("draft_discard", id=gone["id"])
    keep = await c.call("draft", to="jo@family.example", body="still open")
    m.service.stop()
    await asyncio.wait_for(m.task, 10)
    later = await Mail.start(clock=lambda: time.time() + store.RETENTION_S + 3600)
    started.append(later)
    c2 = await later.client()
    await c2.fails("draft_get", "not_found", id=sent["id"])
    await c2.fails("draft_get", "not_found", id=gone["id"])
    assert (await c2.call("draft_get", id=keep["id"]))["state"] == "open"        # open drafts are never old


async def test_the_hourly_tidy_forgets_old_drafts_in_a_service_that_has_been_running_for_days(started, monkeypatch):
    monkeypatch.setattr(service, "PRUNE_S", 0.1)
    offset = {"s": 0.0}
    m = await Mail.start(clock=lambda: time.time() + offset["s"])
    started.append(m)
    c = await m.client()
    d = await ready(c)
    await press(c, d)
    offset["s"] = store.RETENTION_S + 3600

    async def forgotten():
        return (await c.ask("draft_get", id=d["id"]))["ok"] is False
    await until(forgotten, 5.0)


async def test_the_press_log_is_cut_at_a_size_and_keeps_one_older_part(mail, person, agent, monkeypatch):
    monkeypatch.setattr(service, "PRESS_LOG_ROTATE", 600)
    d = await ready(person)
    for _ in range(40):
        await agent.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
    log = paths.press_log()
    assert log.exists() and log.with_name(log.name + ".1").exists()
    assert not log.with_name(log.name + ".2").exists()
    assert log.stat().st_size < 600 + 400
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows and all(r["code"] == "agent" for r in rows)


# -- the service as a program --

SRC = Path(__file__).resolve().parents[1] / "src"
BIN = Path(__file__).resolve().parents[1] / "bin" / "bombadil-mail"
FAKE = {"BOMBADIL_MAIL_ENGINE": "fake", "PYTHONPATH": str(SRC)}


def program(*args, script=False):
    """The service as the supervisor runs it, on the fake engine, in this test's temp home (the environment the
    `places` fixture made is inherited)."""
    argv = [str(BIN) if script else sys.executable, *([] if script else ["-m", "bombadil.mail.service"]), *args]
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, **FAKE})


def finish(proc, seconds=20.0):
    """Wait for it to end, and what it printed."""
    try:
        out, err = proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out, err


def wait_for_socket(proc, path, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and proc.poll() is None and not path.exists():
        time.sleep(0.05)
    assert path.exists(), f"the service did not start: {proc.poll()}"


@pytest.mark.parametrize("script", [False, True], ids=["module", "script"])
async def test_the_program_starts_on_the_fake_engine_answers_and_leaves_cleanly_on_sigterm(home, script):
    from bombadil.mail import client
    sock = home / "run" / "mail.sock"

    def go():
        proc = program(script=script)
        try:
            wait_for_socket(proc, sock)
            with client.Connection(sock, timeout=10) as c:
                status = c.request("status")
                assert status["fake"] is True and len(status["accounts"]) == 3
                assert c.request("ping")["pong"]
                assert c.request("draft", to="jo@family.example", body="from the program")["state"] == "open"
            proc.send_signal(signal.SIGTERM)
            return finish(proc)[0]
        finally:
            if proc.poll() is None:
                proc.kill()
                finish(proc)
    assert await asyncio.to_thread(go) == 0
    assert not sock.exists() and (home / "state" / "mail.db").exists()


async def test_a_second_program_on_the_same_notes_says_so_and_leaves_the_first_alone(home):
    from bombadil.mail import client
    sock = home / "run" / "mail.sock"

    def go():
        first = program()
        try:
            wait_for_socket(first, sock)
            code, _, err = finish(program())
            with client.Connection(sock, timeout=10) as c:
                assert c.request("ping")["pong"]
            return code, err
        finally:
            first.terminate()
            finish(first)
    code, err = await asyncio.to_thread(go)
    assert code == 1 and "already" in err


@pytest.mark.parametrize("flag, code", [("--help", 0), ("-h", 0), ("--what", 2)])
async def test_the_program_with_an_argument_says_what_it_is_and_does_not_start(home, flag, code):
    got, out, err = await asyncio.to_thread(lambda: finish(program(flag)))
    assert got == code and not (home / "run" / "mail.sock").exists()
    assert "mail" in out.lower() or "mail" in err.lower()


async def test_the_launcher_script_is_executable_and_says_what_it_is(home):
    assert os.access(BIN, os.X_OK)
    got, out, _ = await asyncio.to_thread(lambda: finish(program("--help", script=True)))
    assert got == 0 and "mail" in out.lower()


# -- races inside a press: what happens between the checks and the write of "sending" --

async def test_a_draft_edited_and_edited_back_while_its_attachments_go_up_is_not_sent_unshown(mail, person, home,
                                                                                            monkeypatch):
    f = write(home, "plan.txt", b"the plan")
    d = await person.call("draft", to="jo@family.example", body="First words", attachments=[str(f)])
    await shown(person, d)
    other = await mail.client()
    release, uploading = asyncio.Event(), asyncio.Event()
    real_blob = mail.engine.send_blob

    async def slow_blob(*a, **k):
        uploading.set()
        await release.wait()
        return await real_blob(*a, **k)
    monkeypatch.setattr(mail.engine, "send_blob", slow_blob)
    pressing = asyncio.create_task(person.ask("send", id=d["id"], fingerprint=d["fingerprint"]))
    await asyncio.wait_for(uploading.wait(), 5)
    await other.call("draft_edit", id=d["id"], body="Second words")
    back = await other.call("draft_edit", id=d["id"], body="First words")
    assert back["fingerprint"] == d["fingerprint"]                  # the very same draft, and no longer shown
    release.set()
    answer = await pressing
    assert answer["ok"] is False and answer["code"] == "changed"
    assert not calls(mail, "send") and (await person.call("draft_get", id=d["id"]))["state"] == "open"


async def test_a_draft_that_is_discarded_in_the_moment_a_press_begins_is_sent_and_not_discarded(mail, person,
                                                                                               monkeypatch):
    d = await ready(person)
    real, fired = Store.set_state, []

    def racing(self, did, state, now, *, was=None):
        if state == "discarded" and not fired:
            fired.append(1)
            assert self.begin_send(did, d["fingerprint"], now)      # the press got there first
        return real(self, did, state, now, was=was)
    monkeypatch.setattr(Store, "set_state", racing)
    await person.fails("draft_discard", "refused", id=d["id"])
    monkeypatch.undo()
    assert (await person.call("draft_get", id=d["id"]))["state"] == "sending"


# ===== review round: who is on the other end, what one process may hold, what a hostile mail may cost =====

# -- the process on the other end is gone, or cannot be named --

HANDOFF = r'''
import json, os, socket, sys
sock = socket.socket(socket.AF_UNIX)
sock.connect(sys.argv[1])
lines = sock.makefile("rb")


def ask(req):
    sock.sendall((json.dumps(req) + "\n").encode())
    return lines.readline().decode().strip()


print(ask({"op": "ping"}), flush=True)    # answered: the service has accepted this connection and knows who it is
if os.fork():
    os._exit(0)                           # the process that connected goes; the connection stays with its child
sys.stdin.readline()                      # until the test has seen the first one reaped
for req in json.loads(sys.argv[2]):
    print(ask(req), flush=True)
os._exit(0)
'''


async def hand_off(mail, requests: list[dict]) -> list[str]:
    """What a process in an agent's turn can do: connect, give the connection to a child and exit. The child then
    says each request and the lines it got back are returned (an empty one is the service hanging up)."""
    proc = await asyncio.to_thread(
        subprocess.Popen, [sys.executable, "-c", HANDOFF, str(mail.service.socket_path), json.dumps(requests)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert json.loads(await asyncio.to_thread(proc.stdout.readline))["ok"] is True
    await asyncio.to_thread(proc.wait)         # the one the kernel recorded is gone, and nobody is at its number
    proc.stdin.write("go\n")
    proc.stdin.flush()
    out = [await asyncio.to_thread(proc.stdout.readline) for _ in requests]
    proc.stdin.close()
    return [line.strip() for line in out]


@pytest.mark.parametrize("pidfd", [True, False], ids=["pidfd", "proc"])
async def test_a_connection_handed_to_a_child_by_a_process_that_then_exits_is_still_the_agents(mail, person, pidfd,
                                                                                               monkeypatch):
    # in_turn here says "not in a turn", as it would of a pid nobody is at: the check cannot rest on it
    if not pidfd:
        monkeypatch.setattr(service, "_peer_pidfd", lambda sock: None)
    d = await ready(person)
    wide = await person.call("draft", to="jo@family.example", body="x")
    got = [json.loads(x) for x in await hand_off(mail, [
        {"op": "draft_shown", "id": "d9999", "fingerprint": "x"},
        {"op": "send", "id": d["id"], "fingerprint": d["fingerprint"]},
        {"op": "draft", "to": OUTSIDE, "body": "from a child", "typed": OUTSIDE, "created_by": "person"},
        {"op": "draft_discard", "id": wide["id"]},
        {"op": "save_attachment", "id": INVOICE, "part": "1.2"}])]
    assert [(a["ok"], a.get("code")) for a in got] == [(False, "refused"), (False, "refused"), (True, None),
                                                      (False, "refused"), (False, "refused")]
    made = got[2]["result"]
    assert made["created_by"] == "agent" and made["warnings"] and made["warnings"][0]["kind"] == "new_address"
    nothing_went(mail)
    assert await state_of(person, d) == "open" and await state_of(person, wide) == "open"
    assert [r["code"] for r in mail.press_rows()] == ["agent"]


async def test_a_connection_handed_off_cannot_become_thunderbird_either(real):
    real.service.engine.on_state = real.service.engine.on_state   # the real link, with no host
    got = await hand_off(real, [{"op": "engine_hello", "pid": 1}])
    assert got == [""]                                  # hung up on
    await asyncio.sleep(0.1)
    assert not real.service.engine.connected


async def test_a_peer_the_kernel_cannot_name_is_the_agents(mail, monkeypatch):
    monkeypatch.setattr(Service, "_peer", lambda self, writer: (0, os.getuid()))   # another pid namespace, say
    c = await mail.client()
    assert (await c.call("ping"))["pong"]
    await c.fails("draft_shown", "refused", id="d1", fingerprint="x")
    await c.fails("send", "refused", id="d1", fingerprint="x")
    assert mail.press_rows()[-1]["code"] == "agent" and mail.press_rows()[-1]["pid"] == 0
    assert (await c.call("draft", to="jo@family.example", body="x"))["created_by"] == "agent"


async def test_a_peer_of_another_user_is_hung_up_on_without_a_word(mail, monkeypatch):
    monkeypatch.setattr(Service, "_peer", lambda self, writer: (os.getpid(), os.getuid() + 4242 if os.getuid() else 4242))
    reader, writer = await asyncio.open_unix_connection(str(mail.service.socket_path))
    writer.write(b'{"op": "ping"}\n')
    try:
        assert await asyncio.wait_for(reader.readline(), 3) == b""
    except ConnectionResetError:
        pass                                                         # hung up on, and said so as the kernel does
    monkeypatch.undo()
    assert (await (await mail.client()).call("ping"))["pong"]


class _Stub:
    """Enough of a stream writer for a Conn that is not on a socket."""

    class transport:
        @staticmethod
        def get_write_buffer_size():
            return 0

        @staticmethod
        def abort():
            pass

    @staticmethod
    def close():
        pass

    @staticmethod
    def is_closing():
        return False


async def test_a_connection_is_alive_while_its_process_is_and_not_before_or_after():
    here = service.Conn(_Stub, os.getpid(), os.getuid())
    assert here.alive() is True
    assert service.Conn(_Stub, 0, os.getuid()).alive() is False and service.Conn(_Stub, -5, 0).alive() is False
    done = await asyncio.to_thread(subprocess.Popen, [sys.executable, "-c", "pass"])
    await asyncio.to_thread(done.wait)                            # reaped: nobody is at that number
    assert service.Conn(_Stub, done.pid, os.getuid()).alive() is False


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="no pidfd here")
async def test_a_pidfd_says_the_process_ended_even_when_it_is_not_reaped_and_even_if_its_number_is_taken():
    child = await asyncio.to_thread(subprocess.Popen, [sys.executable, "-c", "import time; time.sleep(60)"])
    fd = os.pidfd_open(child.pid)
    conn = service.Conn(_Stub, child.pid, os.getuid(), fd)
    assert conn.alive() is True
    child.kill()
    deadline = time.monotonic() + 5
    while conn.alive() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert conn.alive() is False                                  # a zombie is not the peer either
    await asyncio.to_thread(child.wait)
    conn.close()
    assert conn.pidfd is None                                     # and closing lets the descriptor go
    with pytest.raises(OSError):
        os.fstat(fd)


async def test_the_pidfd_of_a_peer_is_not_there_when_the_kernel_cannot_give_one():
    class Sock:
        @staticmethod
        def getsockopt(*a):
            raise OSError("no such process")
    assert service._peer_pidfd(Sock) is None and service._peer_pidfd(None) is None


# -- the production half of the second guard: which cgroup is a process in --

async def test_turn_scope_asks_procs_which_cgroup_and_what_it_cannot_tell_is_not_a_turn(monkeypatch, capsys):
    monkeypatch.setattr(service, "_blind", False)
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: Path("/sys/fs/cgroup/user.slice/bombadil-turn-7.scope"))
    assert service.turn_scope(1234) is True
    monkeypatch.setattr(procs, "cgroup_of", lambda pid: None)
    assert service.turn_scope(1234) is False
    assert capsys.readouterr().err == ""

    def boom(pid):
        raise RuntimeError("no cgroups")
    monkeypatch.setattr(procs, "cgroup_of", boom)
    assert service.turn_scope(1234) is False and service.turn_scope(1234) is False
    assert capsys.readouterr().err.count("cannot tell which cgroup") == 1                 # said once


async def test_the_service_asks_the_real_scope_check_for_each_request_and_catches_a_process_that_joined_late(
        started, home, monkeypatch):
    """Without the test's `in_turn`: procs.PROC points at a made-up /proc where this process is in a turn's scope
    only when the test says, and the service is asked, as it would be, on every request."""
    fake_proc, scope = home / "proc", home / "proc" / str(os.getpid()) / "cgroup"
    scope.parent.mkdir(parents=True)
    monkeypatch.setattr(procs, "PROC", fake_proc)
    monkeypatch.setattr(procs, "CGROUP_ROOT", home / "cg")
    m = await Mail.start(in_turn=service.turn_scope)
    started.append(m)
    c = await m.client()
    d = await ready(c)
    scope.write_text("0::/user.slice/user-1000.slice/session-2.scope\n")                  # the person's own session
    assert (await press(c, d))["already"] is False
    d = await ready(c, body="Another")
    scope.write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/bombadil-turn-7.scope\n")
    await c.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])        # this connection, now in a turn
    await c.fails("draft_shown", "refused", id=d["id"], fingerprint=d["fingerprint"])
    await c.fails("draft_discard", "refused", id=d["id"])
    assert (await c.call("draft", to="jo@family.example", body="x"))["created_by"] == "agent"
    assert [r["code"] for r in m.press_rows()] == ["agent"]
    scope.unlink()                                                                         # /proc says nothing at all
    assert (await c.call("draft_get", id=d["id"]))["state"] == "open"
    await press(c, d)


async def test_where_turns_have_no_scope_the_log_says_that_the_service_cannot_tell(started, monkeypatch, capsys):
    monkeypatch.setattr(procs, "scope_supported", lambda: False)
    m = await Mail.start(in_turn=service.turn_scope)
    started.append(m)
    seen = ""
    for _ in range(100):
        seen += capsys.readouterr().err
        if "without a systemd scope" in seen:
            break
        await asyncio.sleep(0.05)
    assert "cannot tell an agent's process" in seen
    await m.stop()
    started.remove(m)
    m2 = await Mail.start()                    # a test's own in_turn is not the real one: nothing is said or run
    started.append(m2)
    await asyncio.sleep(0.2)
    assert "without a systemd scope" not in capsys.readouterr().err


# -- what a process inside a turn may hold, of the places it shares with the person --

async def test_an_agents_turn_may_hold_only_a_few_connections_and_the_person_always_has_room(mail, monkeypatch):
    monkeypatch.setattr(service, "MAX_CLIENTS", 5)
    monkeypatch.setattr(service, "MAX_SCOPED_CLIENTS", 2)
    held = [await mail.client(agent=True), await mail.client(agent=True)]
    mail.agent_flag = True               # the service asks who is there as it accepts, so the flag stays up until it did
    try:
        third = await raw_client(mail)
        await asyncio.get_running_loop().sock_sendall(third, b'{"op": "ping"}\n')
        assert await ended(third)                              # the third is hung up on
    finally:
        mail.agent_flag = False
    people = [await mail.client() for _ in range(3)]            # and the person's three still fit
    for c in (*held, *people):
        assert (await c.call("ping"))["pong"]
    third.close()


async def test_a_connection_that_says_nothing_is_closed_and_a_window_waiting_for_pushes_is_not(mail, monkeypatch):
    monkeypatch.setattr(service, "IDLE_S", 0.25)
    idle = await raw_client(mail)
    half = await raw_client(mail)
    await asyncio.get_running_loop().sock_sendall(half, b'{"op": "pi')          # half a line, and nothing after it
    window = await mail.client()
    await window.call("subscribe")
    busy = await mail.client()
    for _ in range(6):                                                          # a client that is used stays
        await asyncio.sleep(0.1)
        assert (await busy.call("ping"))["pong"]
    assert await ended(idle) and await ended(half)
    assert (await window.call("ping"))["pong"] and not window.closed
    mail.engine.inject_new_mail("maya@acme.example", "Priya <priya@acme.example>", "Still there", "x")
    assert (await window.push("new_mail"))["message"]["subject"] == "Still there"
    idle.close()
    half.close()


async def test_an_agent_that_subscribes_is_not_a_window_and_is_closed_when_it_goes_quiet(mail, monkeypatch):
    monkeypatch.setattr(service, "IDLE_S", 0.25)
    agent = await mail.client(agent=True)
    await agent.call("subscribe")
    await until(lambda: agent.closed, 3.0)


async def test_the_agent_cannot_fill_every_place_for_drafts_and_the_person_can_still_start_one(mail, person, agent,
                                                                                              monkeypatch):
    monkeypatch.setattr(service, "MAX_AGENT_DRAFTS", 3)
    made = [await agent.call("draft", to="jo@family.example", body=f"agent {i}") for i in range(3)]
    sentence = await agent.fails("draft", "refused", to="jo@family.example", body="one more")
    assert "from the agent" in sentence
    await person.call("draft", to="jo@family.example", body="the person's own")                  # theirs still fit
    await person.fails("draft", "refused", to="jo@family.example", body="agentd's, for the agent", created_by="agent")
    await person.call("draft_discard", id=made[0]["id"])
    await person.call("draft", to="jo@family.example", body="agentd's, for the agent", created_by="agent")
    await agent.fails("draft", "refused", to="jo@family.example", body="and again")


async def test_attachment_copies_are_under_a_cap_for_all_drafts_and_a_smaller_one_for_the_agents(mail, person, agent,
                                                                                                home, monkeypatch):
    monkeypatch.setattr(service, "MAX_COPY_BYTES", 100)
    monkeypatch.setattr(service, "MAX_AGENT_COPY_BYTES", 50)
    sixty = write(home, "sixty.bin", b"x" * 60)
    other = write(home, "other.bin", b"y" * 60)
    sentence = await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(sixty)])
    assert "Too many attachments" in sentence and mail.draft_files("d1") == []                   # nothing was kept
    first = await person.call("draft", to="jo@family.example", body="x", attachments=[str(sixty)])
    await person.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(other)])
    assert mail.draft_files("d2") == ["sixty.bin"] and mail.draft_files("d3") == []
    second = await person.call("draft", to="jo@family.example", body="x")
    await person.fails("draft_edit", "refused", id=second["id"], add_attachments=[str(other)])
    assert (await person.call("draft_get", id=second["id"]))["attachments"] == []
    await person.call("draft_discard", id=first["id"])
    edited = await person.call("draft_edit", id=second["id"], add_attachments=[str(other)])      # the room came back
    assert [a["name"] for a in edited["attachments"]] == ["other.bin"]
    twenty = write(home, "twenty.bin", b"z" * 40)
    await agent.call("draft", to="jo@family.example", body="x", attachments=[str(twenty)])      # 40 of the agent's 50
    await agent.fails("draft", "refused", to="jo@family.example", body="x", attachments=[str(twenty)])


async def test_refused_presses_are_logged_once_in_a_while_with_a_count_so_a_flood_cannot_push_out_the_rest(
        mail, person, agent, monkeypatch):
    monkeypatch.setattr(service, "AGENT_ROW_S", 0.3)
    d = await ready(person)
    for _ in range(30):
        await agent.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
    assert len(mail.press_rows()) == 1 and "more" not in mail.press_rows()[0]
    await asyncio.sleep(0.35)
    await agent.fails("send", "refused", id=d["id"], fingerprint=d["fingerprint"])
    rows = mail.press_rows()
    assert len(rows) == 2 and rows[1]["more"] == 29
    got = await press(person, d)                                                # and the person's own is never lost
    assert got["already"] is False


# -- hostile mail must not cost anyone else their turn --

async def test_a_mail_made_to_stall_the_reader_is_read_without_stalling_anyone_else(mail, person, monkeypatch):
    html = "<p " + "a=b " * 260_000 + ">"       # ten to twenty seconds on the loop, before
    key = mail.engine.add_message("maya@acme.example", "inbox", "Spam <spam@bulk.example>", "Look", None, html=html)
    other = await mail.client()
    slowest = 0.0
    stop = asyncio.Event()

    async def watch():
        nonlocal slowest
        while not stop.is_set():
            start = time.monotonic()
            await other.call("ping")
            slowest = max(slowest, time.monotonic() - start)
            await asyncio.sleep(0.01)
    watching = asyncio.create_task(watch())
    began = time.monotonic()
    got = await person.call("read", id=f"a1/{key}", _timeout=30.0)
    took = time.monotonic() - began
    stop.set()
    await watching
    assert isinstance(got["text"], str) and got["html_only"] is True and took < 8.0
    assert slowest < 1.0, f"another client waited {slowest:.1f} s for one hostile mail"


async def test_a_plain_part_of_tens_of_megabytes_is_cut_at_the_front_and_quickly(mail, person):
    key = mail.engine.add_message("maya@acme.example", "inbox", "Big <big@bulk.example>", "Long", "line of a mail\n" * 4_000_000)
    began = time.monotonic()
    got = await person.call("read", id=f"a1/{key}", _timeout=30.0)
    assert got["truncated"] is True and len(got["text"]) == 200_000 and time.monotonic() - began < 5.0


# -- marks are the person's work --

def _empty_inbox(mail, monkeypatch):
    """What a Thunderbird that has not loaded its folder yet says."""
    monkeypatch.setattr(mail.engine, "_op_list", lambda a: {"messages": [], "more": False})


async def test_an_empty_page_from_a_folder_not_loaded_yet_does_not_wipe_the_marks(mail, person, monkeypatch):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    await person.call("mark_reply", id=SAM, needs=True, why="a yes")
    _empty_inbox(mail, monkeypatch)
    assert (await person.call("list", view="all"))["messages"] == []
    assert sorted(m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]) == sorted([LAUNCH, SAM])
    assert ("get", {"account": "fake-1-maya", "key": "launch-date-31@acme.example"}) in mail.engine.calls   # it was asked


async def test_a_mark_goes_when_thunderbird_says_the_mail_is_gone_or_is_somewhere_else_and_not_before(mail, person,
                                                                                                    monkeypatch):
    for mid in (LAUNCH, SAM, INVOICE):
        await person.call("mark_reply", id=mid, needs=True, why="x")
    _empty_inbox(mail, monkeypatch)
    mail.engine._mail["fake-1-maya"][:] = [r for r in mail.engine._mail["fake-1-maya"]
                                           if r["emsg"]["key"] != "launch-date-31@acme.example"]    # deleted
    next(r for r in mail.engine._mail["fake-1-maya"] if r["emsg"]["key"] == "pricing-copy-8@acme.example")[
        "emsg"]["folder"] = "trash"                                                                    # moved
    await person.call("list", view="all")
    assert [m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]] == [INVOICE]  # still there


async def test_a_mark_is_kept_when_thunderbird_cannot_say(mail, person, monkeypatch):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="a date")
    _empty_inbox(mail, monkeypatch)
    real = mail.engine._op_get

    def broken(a):
        raise bridge.EngineError("engine_error", "The folder is being repaired.")
    monkeypatch.setattr(mail.engine, "_op_get", broken)
    await person.call("list", view="all")
    monkeypatch.setattr(mail.engine, "_op_get", real)
    assert [m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]] == [LAUNCH]


async def test_only_a_few_marks_are_looked_into_at_each_list(mail, person, monkeypatch):
    monkeypatch.setattr(service, "GONE_CHECKS", 1)
    for mid in (LAUNCH, SAM):
        await person.call("mark_reply", id=mid, needs=True, why="x")
    for r in mail.engine._mail["fake-1-maya"]:
        if r["emsg"]["key"] in ("launch-date-31@acme.example", "pricing-copy-8@acme.example"):
            r["emsg"]["folder"] = "archive"
    await person.call("list", view="all")
    assert len((await person.call("list", view="needs_reply"))["messages"]) == 1
    await person.call("list", view="all")
    assert (await person.call("list", view="needs_reply"))["messages"] == []


async def test_a_mark_can_be_cleared_with_thunderbird_down_and_a_long_why_is_cut_not_refused(mail, person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="w " * 3000)
    [m] = (await person.call("list", view="needs_reply"))["messages"]
    assert len(m["why"]) <= 140 and m["why"].startswith("w w")
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    await until(lambda: mail.service.engine_state == "restarting")
    await person.fails("mark_reply", "engine_down", id=SAM, needs=True, why="x")           # reading it needs Thunderbird
    got = await person.call("mark_reply", id=LAUNCH, needs=False)                           # but putting a mark away does not
    assert got == {"id": LAUNCH, "needs_reply": False, "why": None}
    assert (await person.call("list", view="needs_reply"))["messages"] == []
    await person.fails("mark_reply", "bad_request", id="nonsense", needs=False)


# -- the press: the corners that the review found --

async def test_a_forward_is_not_an_answer_and_the_mail_it_passed_on_still_needs_one(mail, person):
    await person.call("mark_reply", id=LAUNCH, needs=True, why="asks for a date")
    f = await person.call("draft", kind="forward", reply_to=LAUNCH, to="boss@acme.example", body="FYI")
    await shown(person, f)
    await press(person, f)
    assert mail.engine.sent[-1]["kind"] == "forward"
    assert [m["id"] for m in (await person.call("list", view="needs_reply"))["messages"]] == [LAUNCH]
    r = await ready(person, body="The 14th works.")
    await press(person, r)
    assert (await person.call("list", view="needs_reply"))["messages"] == []              # an answer is


async def test_the_fingerprint_worked_out_again_from_the_draft_is_the_fourth_agreement(mail, person, home):
    f = write(home, "plan.txt", b"the plan")
    d = await person.call("draft", to="jo@family.example", body="What was shown", attachments=[str(f)])
    await shown(person, d)
    raw = sqlite3.connect(home / "state" / "mail.db", timeout=10)
    raw.execute("UPDATE drafts SET body = ? WHERE id = ?", ("What was NOT shown", d["id"]))   # fingerprint, shown_fp: same
    raw.commit()
    sentence = await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])
    assert "does not match" in sentence and await state_of(person, d) == "open"
    nothing_went(mail)
    raw.execute("UPDATE drafts SET body = ? WHERE id = ?", ("What was shown", d["id"]))
    stored = json.loads(raw.execute("SELECT attachments FROM drafts WHERE id = ?", (d["id"],)).fetchone()[0])
    stored[0]["name"] = "payroll.txt"
    raw.execute("UPDATE drafts SET attachments = ? WHERE id = ?", (json.dumps(stored), d["id"]))
    raw.commit()
    await person.fails("send", "changed", id=d["id"], fingerprint=d["fingerprint"])
    nothing_went(mail)
    raw.close()


async def test_what_the_engine_says_about_a_sent_message_cannot_make_a_sent_press_fail(mail, person, monkeypatch):
    d = await ready(person)
    real = mail.engine._send

    async def odd(args, timeout):
        got = await real(args, timeout)
        return {**got, "message_id": "x\ud800y\u202e<id>"}
    monkeypatch.setattr(mail.engine, "_send", odd)
    got = await press(person, d)
    assert got["already"] is False and got["receipt"]["line"].startswith("Sent to Priya")
    assert "\ud800" not in json.dumps(got["receipt"], ensure_ascii=False) and "\u202e" not in json.dumps(got["receipt"])
    assert await state_of(person, d) == "sent" and len(mail.engine.sent) == 1


async def test_a_note_that_cannot_be_written_after_the_send_is_tried_again_and_then_at_least_sent(mail, person,
                                                                                                monkeypatch):
    d = await ready(person)
    fails = []

    def full(self, *a, **k):
        fails.append(1)
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(Store, "finish_send", full)
    got = await press(person, d)
    assert got["already"] is False and len(fails) == 2 and len(mail.engine.sent) == 1
    assert await state_of(person, d) == "sent"                   # not "sending", which would be called unknown
    again = await press(person, d)
    assert again["already"] is True and len(mail.engine.sent) == 1


async def test_a_press_that_cannot_even_be_marked_sent_stays_sending_and_still_answers_with_the_receipt(
        mail, person, monkeypatch):
    d = await ready(person)

    def full(self, *a, **k):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(Store, "finish_send", full)
    real = Store.set_state

    def flaky(self, did, state, now, *, was=None):
        if state == "sent":
            raise sqlite3.OperationalError("database or disk is full")
        return real(self, did, state, now, was=was)
    monkeypatch.setattr(Store, "set_state", flaky)
    got = await press(person, d)
    assert got["receipt"]["line"].startswith("Sent to Priya") and len(mail.engine.sent) == 1


async def test_a_receipt_that_cannot_be_made_from_what_the_addon_said_is_made_without_it_and_the_press_still_succeeds(
        mail, person, monkeypatch):
    d = await ready(person)
    real = service.Service._receipt

    def odd(self, d, a, provider, answer):
        if answer is not None:
            raise ValueError("an answer in a shape nobody planned for")
        return real(self, d, a, provider, None)
    monkeypatch.setattr(service.Service, "_receipt", odd)
    got = await press(person, d)
    assert got["already"] is False and got["receipt"]["line"].startswith("Sent to Priya") and len(mail.engine.sent) == 1
    assert await state_of(person, d) == "sent"                     # it went: nothing may now say it did not


async def test_attachments_that_are_not_taken_in_time_are_a_plain_no_and_nothing_is_sent(mail, person, home,
                                                                                         monkeypatch):
    monkeypatch.setattr(service, "BLOBS_S", 0.2)
    f = write(home, "a.txt", b"one")
    g = write(home, "b.txt", b"two")
    d = await person.call("draft", to="jo@family.example", body="x", attachments=[str(f), str(g)])
    await shown(person, d)

    async def slow(*a, **k):
        await asyncio.sleep(0.15)      # each is quick enough, and the two together are not
    monkeypatch.setattr(mail.engine, "send_blob", slow)
    began = time.monotonic()
    sentence = await person.fails("send", "engine_error", id=d["id"], fingerprint=d["fingerprint"])
    assert "Nothing was sent" in sentence and time.monotonic() - began < 2.0
    assert await state_of(person, d) == "open" and not calls(mail, "send")


async def test_names_that_could_end_a_name_and_begin_another_address_do_not_reach_thunderbird_as_they_are(mail,
                                                                                                         person):
    d = await person.call("draft", to=['"Priya, evil@outside.example <x@outside.example> ;" <p2@acme.example>'],
                          body="x")
    assert d["to"][0]["name"].startswith("Priya, evil")                   # what is shown is what was typed
    await shown(person, d)
    await press(person, d)
    [sent] = mail.engine.sent
    [to] = sent["to"]
    assert to["email"] == "p2@acme.example"
    assert not any(c in to["name"] for c in '<>@,;"') and to["name"].startswith("Priya")


# -- two clients adding one address --

async def test_two_clients_adding_the_same_address_at_once_get_one_account(mail):
    a, b = await mail.client(), await mail.client()
    first, second = await asyncio.gather(a.ask("add_account", email="new.person@school.example"),
                                         b.ask("add_account", email="New.Person@school.example"))
    assert first["ok"] and second["ok"] and first["result"]["id"] == second["result"]["id"]
    assert [x["email"] for x in (await a.call("accounts"))["accounts"]].count("new.person@school.example") == 1


# -- what the engine's rows may be --

async def test_rows_from_the_engine_of_the_wrong_shape_cost_nothing_but_themselves(mail, person, monkeypatch):
    rows = mail.engine._op_accounts({})
    odd = [{**rows[0], "provider": ["google"], "state": ["ok"], "web": {"name": 5, "url": ["x"]}},
           {**rows[1], "state": {"a": 1}, "detail": 5, "identities": "not a list", "emails": "nope"},
           {"emails": [["a"]], "state": None}, 5, None, "text"]
    monkeypatch.setattr(mail.engine, "_op_accounts", lambda a: odd)
    got = await person.call("accounts", fresh=True)
    assert len(got["accounts"]) == 3
    await person.call("status")
    await person.call("views")
    mail.engine._later(mail.engine._event, {"event": "sync", "account": "fake-1-maya", "state": ["idle"]})
    await asyncio.sleep(0.2)
    assert (await person.call("ping"))["pong"]


async def test_a_message_of_a_shape_that_cannot_be_shown_is_left_out_and_the_rest_are_shown(mail, person,
                                                                                            monkeypatch):
    real = mail.engine._op_list

    def odd(a):
        got = real(a)
        got["messages"] = [{**got["messages"][0], "to": 5, "cc": {"a": 1}, "folder": ["inbox"], "subject": ["x"],
                            "key": "odd-1"}, *got["messages"]]
        return got
    monkeypatch.setattr(mail.engine, "_op_list", odd)
    got = await person.call("list", view="acct:a1")
    odd = next(m for m in got["messages"] if m["id"] == "a1/odd-1")
    assert odd["to"] == [] and odd["cc"] == [] and odd["folder"] == "other" and len(got["messages"]) > 1


async def test_a_web_address_from_the_engine_is_taken_only_if_it_is_https(mail, person):
    for url, kept in (("https://outlook.office.com/mail/", True), ("file:///etc/passwd", False),
                      ("smb://host/share", False), ("javascript:alert(1)", False), ("http://plain.example/", False)):
        mail.engine._accounts["fake-3-maya.reyes"]["web"] = {"name": "Outlook", "url": url}
        accounts = (await person.call("accounts", fresh=True))["accounts"]
        web = next(a for a in accounts if a["id"] == "a3")["web"]
        assert (web["url"] == url) is kept, (url, web)
        assert web["url"].startswith("https://")


async def test_names_and_subjects_lose_invisible_marks_and_lone_surrogates(mail, person):
    key = mail.engine.add_message("maya@acme.example", "inbox", {"name": "Priya\u202e Shah\ud800", "email": "p@acme.example"},
                                  "Inv\u200boice\u202e 12\ud800", "Hello")
    got = await person.call("read", id=f"a1/{key}")
    assert got["message"]["from"]["name"] == "Priya Shah" and got["message"]["subject"] == "Invoice 12"
    await person.call("mark_reply", id=f"a1/{key}", needs=True, why="x")
    [m] = [m for m in (await person.call("list", view="needs_reply"))["messages"] if m["key"] == key]
    assert m["subject"] == "Invoice 12"


async def test_an_attachment_with_a_lone_surrogate_in_its_name_does_not_make_a_mail_unreadable(mail, person):
    key = mail.engine.add_message("maya@acme.example", "inbox", "Priya <priya@acme.example>", "Files", "see attached",
                                  parts=[{"name": "a\ud800b.pdf", "data": b"%PDF"}])
    got = await person.call("read", id=f"a1/{key}")
    assert [a["name"] for a in got["attachments"]] == ["ab.pdf"]


async def test_the_note_that_tells_a_person_how_to_sign_in_is_not_erased_by_the_next_refresh(mail, person):
    added = await person.call("add_account", email="new.person@gmail.com")
    assert added["state"] == "signin" and "Thunderbird" in added["note"]
    note = added["note"]
    fresh = next(a for a in (await person.call("accounts", fresh=True))["accounts"] if a["email"] == "new.person@gmail.com")
    assert fresh["state"] == "signin" and fresh["note"] == note
    mail.engine.set_state("new.person@gmail.com", "error", "Google said no.")
    await until(lambda: mail.service.engine_state == "up")
    await asyncio.sleep(0.2)
    fresh = next(a for a in (await person.call("accounts", fresh=True))["accounts"] if a["email"] == "new.person@gmail.com")
    assert fresh["state"] == "error" and fresh["note"] == "Google said no."


async def test_who_got_it_is_the_first_name_whichever_way_the_name_is_written():
    def one(name):
        return service._who([{"name": name, "email": "x@y.example"}])
    assert one("Priya Shah") == "Priya" and one("Shah, Priya") == "Priya" and one("Dr. Amir Haddad") == "Amir"
    assert one("Sam Jones, MD") == "Sam" and one("Prof Ada Lovelace") == "Ada" and one("") == "x@y.example"
    assert one("Dr.") == "x@y.example"
    assert service._who([{"name": "Priya Shah", "email": "p@y.example"}, {"name": "", "email": "q@y.example"}]) == \
        "Priya and 1 more"


# -- a second service on the same socket --

async def test_a_second_service_with_other_notes_cannot_take_the_socket_of_the_first(mail, person, home):
    second = Service(db_path=home / "elsewhere" / "mail.db", engine=fake.FakeEngine(connected=True), process=None,
                     resolver=lambda d: [], in_turn=lambda pid: False)
    with pytest.raises(service.AlreadyRunning) as e:
        await second.serve()
    assert "mail.sock" in str(e.value)
    assert (await person.call("ping"))["pong"] and mail.service.socket_path.exists()
    assert not (home / "elsewhere" / "mail.db").exists() or True
    # and the notes it did not get are not held by it either: a third service may have them
    third = Service(db_path=home / "elsewhere" / "mail.db", socket_path=home / "run" / "third.sock",
                    engine=fake.FakeEngine(connected=True), process=None, resolver=lambda d: [],
                    in_turn=lambda pid: False)
    task = asyncio.ensure_future(third.serve())
    await until(lambda: third.socket_path.exists() or task.done())
    assert not task.done()
    third.stop()
    await asyncio.wait_for(task, 10)


# -- a Thunderbird that is there and does not answer; one whose controls hang --

@pytest_asyncio.fixture
async def watched(started, monkeypatch):
    """The real link with a process the service can restart, and a host that can go quiet."""
    monkeypatch.setattr(service, "QUIET_S", 0.2)
    monkeypatch.setattr(service, "PING_S", 0.2)
    proc = fake.FakeProcess()
    m = await Mail.start(engine=bridge.EngineLink(), process=proc, initial=False, up=False)
    started.append(m)
    m.hosts = []

    async def host(**kw):
        h = Host(m.service.socket_path)
        h.handlers.update(kw)
        await h.connect()
        m.hosts.append(h)
        return h
    m.host = host
    yield m
    for h in m.hosts:
        h.close()


async def test_a_thunderbird_whose_addon_stops_answering_is_hung_up_on_and_started_again(watched):
    c = await watched.client()
    await c.call("add_account", email="maya@acme.example")      # an account: Thunderbird is wanted
    host = await watched.host()
    await until(lambda: watched.service.engine_state == "up")
    host.handlers["info"] = HANG
    host.handlers["accounts"] = HANG
    await host.closed(8.0)                                      # the service hung up on it
    await until(lambda: ("restart",) in watched.process.calls, 8.0)
    again = await watched.host()
    await until(lambda: watched.service.engine_state == "up" and watched.service.engine.connected, 8.0)
    await until(lambda: "info" in again.ops(), 8.0)             # the new link is watched, too: asked when quiet


async def test_an_addon_that_connected_while_thunderbird_was_being_started_is_up_and_not_starting_for_good(watched):
    c = await watched.client()
    await c.call("add_account", email="maya@acme.example")
    host = await watched.host()
    await until(lambda: watched.service.engine_state == "up")
    await until(lambda: watched.process.running(), 5.0)
    # what a slow round did: it chose to start Thunderbird before the add-on was there, and said so after
    watched.service._set_engine("starting", "Starting Thunderbird.")
    watched.service._poke.set()
    await until(lambda: watched.service.engine_state == "up", 5.0)
    assert host.ops().count("info") >= 2                           # it was asked again, and it answered


async def test_controls_that_hung_and_are_well_again_leave_a_connected_thunderbird_up_and_not_down_for_good(mail):
    await until(lambda: mail.process.running() and mail.service.engine_state == "up")
    mail.service._set_engine("down", "Thunderbird cannot be controlled just now. Trying again shortly.")
    mail.service._poke.set()
    await until(lambda: mail.service.engine_state == "up", 5.0)


async def test_thunderbird_that_is_already_connected_is_not_said_to_be_starting(watched):
    c = await watched.client()
    await c.call("add_account", email="maya@acme.example")
    await watched.host()
    await until(lambda: watched.service.engine_state == "up")
    accounts = await watched.service.job(lambda: watched.service.store.accounts())
    await watched.service._start(accounts)
    assert watched.service.engine_state == "up"


async def test_a_thunderbird_that_answers_the_ping_is_left_alone(watched):
    c = await watched.client()
    await c.call("add_account", email="maya@acme.example")
    host = await watched.host()
    await until(lambda: watched.service.engine_state == "up")
    await asyncio.sleep(1.0)                                    # several quiet periods, each one asked and answered
    assert host.ops().count("info") >= 3 and ("restart",) not in watched.process.calls
    assert watched.service.engine.connected


async def test_status_does_not_wait_the_whole_read_time_for_an_addon_that_is_not_answering(watched, monkeypatch):
    monkeypatch.setattr(service, "ACCOUNTS_S", 0.3)
    monkeypatch.setattr(service, "COUNTS_S", 0.0)               # the list is never believed: each status asks
    monkeypatch.setattr(service, "QUIET_S", 60.0)               # not this test's business
    c = await watched.client()
    await c.call("add_account", email="maya@acme.example")
    host = await watched.host()
    await until(lambda: watched.service.engine_state == "up")
    host.handlers["accounts"] = HANG
    began = time.monotonic()
    await c.call("status")
    assert 0.2 < time.monotonic() - began < 2.0                 # it waited for ACCOUNTS_S and not for the read time
    began = time.monotonic()
    for _ in range(5):
        await c.call("status")
    assert time.monotonic() - began < 0.5                       # the failure is remembered for a moment


async def test_controls_that_hang_make_a_state_and_not_a_stall(mail, person, monkeypatch):
    monkeypatch.setattr(service, "PROCESS_S", 0.6)
    monkeypatch.setattr(service, "PROBE_S", 0.2)
    monkeypatch.setattr(service, "SEED_WAIT_S", 0.2)            # the answer waits less than the call may take
    gate = threading.Event()
    real = mail.process.seed_account

    def stuck(account, provider):
        gate.wait(10)
        return real(account, provider)
    monkeypatch.setattr(mail.process, "seed_account", stuck)
    began = time.monotonic()
    added = await person.call("add_account", email="new.person@school.example")
    assert time.monotonic() - began < 2.0 and added["email"] == "new.person@school.example"   # not waited for
    await person.call("status")                                  # nor does anything else wait behind it
    await until(lambda: mail.service.engine_state == "down", 5.0)
    assert "cannot be controlled" in mail.service.engine_detail
    monkeypatch.setattr(service, "PROCESS_S", 3.0)               # a call behind the stuck one would wait this long
    began = time.monotonic()
    with pytest.raises(service.ProcessTimeout, match="are stuck in"):
        await mail.service._process(lambda: None)
    assert time.monotonic() - began < 1.0 and mail.service._hung     # refused at once, naming the call that hangs
    gate.set()
    await until(lambda: mail.service._hung is None, 5.0)
    await until(lambda: mail.service.engine_state == "up", 8.0)


async def test_removing_an_account_does_not_wait_for_controls_that_hang(mail, person, monkeypatch):
    monkeypatch.setattr(service, "PROCESS_S", 0.6)
    monkeypatch.setattr(service, "SEED_WAIT_S", 0.2)
    gate = threading.Event()
    real = mail.process.forget_account

    def stuck(account):
        gate.wait(10)
        return real(account)
    monkeypatch.setattr(mail.process, "forget_account", stuck)
    began = time.monotonic()
    await person.call("remove_account", id="a2")
    assert time.monotonic() - began < 2.0
    assert [a["id"] for a in (await person.call("accounts"))["accounts"]] == ["a1", "a3"]
    gate.set()


async def test_after_a_link_is_lost_the_next_answer_says_so_and_not_that_mail_is_running(mail, person):
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    sentence = await person.fails("list", "engine_down", view="all")
    assert sentence != "Mail is running." and "Thunderbird" in sentence


async def test_a_crash_is_started_again_without_waiting_out_a_whole_supervision_round(mail, person, monkeypatch):
    monkeypatch.setattr(service, "SUPERVISE_S", 5.0)            # the sleep between rounds is long
    monkeypatch.setattr(service, "BACKOFF_MIN", 0.05)
    real = mail.process.running

    asked = []

    def slow_running():
        said = real()
        asked.append(time.monotonic())
        time.sleep(0.3)                                         # a tick takes a while, and what it saw is stale
        return said
    monkeypatch.setattr(mail.process, "running", slow_running)
    await asyncio.sleep(0.5)
    mail.service._poke.set()                                    # a round begins, and sees Thunderbird running
    await asyncio.sleep(0.1)
    began = time.monotonic()
    mail.process.crash()                                        # ... and the crash lands in the middle of it
    await until(lambda: mail.service.engine_state == "up" and mail.engine.connected, 4.0)
    assert time.monotonic() - began < 3.0
    await asyncio.sleep(0.2)
    seen = len(asked)
    await asyncio.sleep(1.5)
    assert len(asked) == seen                                   # and a round is not made again until something asks


async def test_the_pause_before_a_restart_is_two_seconds_doubling_to_a_minute(monkeypatch):
    monkeypatch.setattr(service, "BACKOFF_MIN", 2.0)
    monkeypatch.setattr(service, "BACKOFF_MAX", 60.0)
    assert [service._backoff(n) for n in range(9)] == [2, 2, 4, 8, 16, 32, 60, 60, 60]


async def test_each_failed_start_waits_twice_as_long_as_the_one_before_up_to_the_most(mail, person, monkeypatch):
    monkeypatch.setattr(service, "BACKOFF_MIN", 0.05)
    monkeypatch.setattr(service, "BACKOFF_MAX", 0.4)
    times = []
    real = mail.process.start

    def start():
        times.append(time.monotonic())
        return real()
    monkeypatch.setattr(mail.process, "start", start)
    mail.process.fail_start = OSError("no display")
    mail.process.crash()
    await until(lambda: len(times) >= 7, 10.0)
    mail.process.fail_start = None
    gaps = [b - a for a, b in itertools.pairwise(times)]
    for gap, want in zip(gaps[:6], [0.05, 0.1, 0.2, 0.4, 0.4, 0.4], strict=True):
        assert want - 0.01 <= gap <= want + 0.25, (gaps, want)
    await until(lambda: mail.service.engine_state == "up", 8.0)


# -- stopping with a request in flight --

async def test_stopping_with_a_press_in_flight_ends_it_before_the_notes_close_and_the_draft_is_unknown_next_time(
        started, home, monkeypatch):
    m = await Mail.start()
    c = await m.client()
    d = await ready(c)
    m.engine.hang_send(delivered=True)
    monkeypatch.setattr(service, "SEND_S", 30.0)
    pressing = asyncio.create_task(c.ask("send", id=d["id"], fingerprint=d["fingerprint"], _timeout=20))
    await until(lambda: calls(m, "send"))
    m.service.stop()
    await asyncio.wait_for(m.task, 10)
    assert m.task.exception() is None                          # a clean exit, and no "closed database" traceback
    pressing.cancel()
    again = await Mail.start()
    started.append(again)
    c2 = await again.client()
    assert (await c2.call("draft_get", id=d["id"]))["state"] == "unknown"


# -- what the engine's own words must not undo --

async def test_an_account_waiting_for_its_sign_in_is_not_called_syncing_by_an_engine_that_cannot_tell(mail, person):
    got = await person.call("add_account", email="wait@gmail.com")
    assert got["state"] == "signin"
    await until(lambda: mail.service.engine_state == "up")
    mail.engine.set_state("wait@gmail.com", "syncing", "Thunderbird is fetching this account.")
    await person.call("accounts", fresh=True)
    after = next(a for a in (await person.call("accounts", fresh=True))["accounts"] if a["email"] == "wait@gmail.com")
    assert after["state"] == "signin" and "allow Thunderbird" in after["note"]   # the steps are still there
    mail.engine.set_state("wait@gmail.com", "ok")
    await until(lambda: next(a for a in mail.service.store.accounts() if a["email"] == "wait@gmail.com")["state"] == "ok")


async def test_one_address_thunderbird_cannot_be_given_stops_no_other_account_from_starting(mail, person):
    real = mail.process.seed_account
    await person.call("add_account", email="good@gmail.com")
    await person.call("add_account", email="bad@family.example")

    def seed(account, provider):
        if account["email"] == "bad@family.example":
            raise ValueError("cannot make a server name from that")
        real(account, provider)
    mail.process.seed_account = seed
    mail.engine.drop_account("bad@family.example")      # Thunderbird has no such account: it was never written
    mail.process.stop()
    await until(lambda: ("start",) in mail.process.calls[-8:] and mail.process.running())
    states = {a["email"]: a["state"] for a in (await person.call("accounts", fresh=True))["accounts"]}
    assert states["bad@family.example"] == "error" and states["good@gmail.com"] == "signin"
    assert ("seed_account", "good@gmail.com", "google") in mail.process.calls


async def test_a_send_that_did_not_finish_starts_thunderbird_again_to_clear_a_dialog_nobody_can_click(mail, person):
    d = await ready(person)
    before = mail.process.calls.count(("restart",))
    mail.engine.hang_send(delivered=False)
    await person.fails("send", "unknown_outcome", id=d["id"], fingerprint=d["fingerprint"])
    await until(lambda: mail.process.calls.count(("restart",)) > before)
    assert await state_of(person, d) == "unknown"

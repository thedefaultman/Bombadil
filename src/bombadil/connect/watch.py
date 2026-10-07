"""agentd's ear and voice for connections: what the connection service tells it unasked, and what it says about it.

The service pushes to whoever subscribed (docs/CONNECT.md, "Wire protocol"): a message for the person, an item that
waits on them, a connection that changed state. `Watch` is the subscription, and `Says` turns each push, and each
proposal, press, launcher word and card button agentd handles, into notices above the pill and cards in the bar.

Why it is shaped this way:

- Connections are optional and must cost nothing when absent, as mail is (mail/watch.py, which this follows):
  `Watch` makes one connection attempt every RETRY_SECONDS and says nothing when it fails, tests silence with a
  `status` request, and nothing a push does can hold the reading for long. It is written out here and not inherited,
  because the socket, the idle request and the words in its log are all the other service's.
- One notice per thing. A new message in a conversation replaces that conversation's notice, a message whose ref was
  said is not said again, and a message older than two minutes when it is pushed (a backfill after a reconnect) is
  not said at all: the unread list is where it waits. One receipt per press, whichever of the press and the
  proposal's own change reaches the screen first.
- Nothing in a notice is Bombadil's sentence unless it is Bombadil's: a message is "Priya Shah in #launch: text",
  the sender first and a colon, as mail's is. Names and texts are cut and cleaned by the notice itself.
- A proposal is opened here, with what the service knows about where it goes: the thread (so a card has something to
  show and a conversation the person has not heard from in two weeks is a warning), or the task. A service that is
  not there never stops a person's own Reply: the card is made from the message that was pushed, and no warning is
  raised that cannot be known. The agent's `propose` is stricter: it must name something the service knows.
- Setup is the person's. Slack's runs in a thread of its own (slack_setup.run_setup blocks, and walks the person
  through the pages), says each step as one line that replaces the last, and stops at once on Cancel, on "cancel"
  and after SETUP_SECONDS. A tool's is a page in the panel and a notice that says what to allow there.

What it does not do: send anything (that is the outbox's press), keep a message anywhere (the service holds the ring,
and a proposal holds only the person's own reply), or read a token.
"""

import asyncio
import json
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .. import paths
from ..mail import watch as mail_watch
from ..notices import Notices, one_line
from ..proposals import AGENT, PERSON
from . import cardmake
from . import client as connect_client
from .protocol import SERVICES, conversation_key, valid_connection_id, valid_ref, web_url

RETRY_SECONDS = 5.0
IDLE_SECONDS = 45.0
PONG_SECONDS = 10.0
ANSWER_SECONDS = 5.0      # the subscribe answer
PUSH_SECONDS = 30.0       # what saying one push may take before the next is read
LINE_LIMIT = 4 << 20      # one push is a message or a connection, never this
REMEMBER = 256            # refs and ids said, so a repeated push is not said twice
OLD_SECONDS = 120.0       # a message older than this when it is pushed is not worth the pill
MESSAGE_TTL = 60.0
TASK_TTL = 120.0
RECEIPT_TTL = 120.0
WARNING_TTL = 600.0
SETUP_SECONDS = 900.0
NEW_PLACE_DAYS = 14
THREAD_LIMIT = 20
LINE_TEXT = 100           # characters of a message in its notice
SEEN_SECONDS = 1.5        # what telling the service "the person looked" may take
DOWN = connect_client.DOWN
SLOW = "Connections did not answer in time. Try again in a moment."
SILENT = ("Connections did not answer in time. It may still have done that, so look at Connected here before "
          "trying again.")
NOTHING = "Nothing is connected yet. Say connect slack."
SLACK_UNAVAILABLE = "Slack setup is not available here."


class NoAnswer(connect_client.ConnectUnavailable):
    """The service was there and was asked, and then said nothing: what was asked may have been done."""


class Unknown(Exception):
    """A proposal for something the service does not know, or cannot do: str() is the sentence for whoever asked."""


def _request(op: str, timeout: float, **args):
    conn = connect_client.Connection(timeout=timeout)   # ConnectUnavailable here: never reached, nothing was asked
    with conn:
        try:
            return conn.request(op, timeout, **args)
        except NoAnswer:
            raise
        except connect_client.ConnectUnavailable as e:
            raise NoAnswer(e.detail) from None


async def ask(op: str, timeout: float, **args):
    """One request to the service, off the loop: the client blocks. `NoAnswer` (a `ConnectUnavailable`) when it was
    reached and did not answer, so a caller can tell that from a service that is not there."""
    return await asyncio.to_thread(_request, op, timeout, **args)


def said(e: Exception, wrote: bool = False) -> str:
    """What to tell a person when the service did not do it: its own sentence, that it is not there, or, for a service
    that took the request and never answered, that it may have been done (`wrote`: a change, not a look)."""
    if isinstance(e, connect_client.ConnectError):
        return str(e)
    if isinstance(e, NoAnswer):
        return SILENT if wrote else SLOW
    return DOWN


def _json(line: bytes) -> dict:
    try:
        msg = json.loads(line)
    except ValueError:
        return {}
    return msg if isinstance(msg, dict) else {}


class Watch:
    """The subscription to connect.sock. `handle(push)` gets every push, in order; a failure in it is reported and the
    watching goes on."""

    def __init__(self, handle: Callable[[dict], Awaitable[None]], retry: float = RETRY_SECONDS,
                 idle: float = IDLE_SECONDS):
        self.handle = handle
        self.retry = retry
        self.idle = idle
        self.connected = False

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except (OSError, ValueError, asyncio.IncompleteReadError):
                pass   # not there, or it went away: the next attempt is in a few seconds
            except Exception as e:  # noqa: BLE001 - connections are optional: whatever it was, try again, never end
                print(f"agentd: connect watch: {type(e).__name__}", file=sys.stderr)   # the type, never the push
            self.connected = False
            await asyncio.sleep(self.retry)

    async def _session(self) -> None:
        reader, writer = await asyncio.open_unix_connection(str(paths.connect_socket()), limit=LINE_LIMIT)
        try:
            writer.write(b'{"id": 1, "op": "subscribe"}\n')
            await writer.drain()
            async with asyncio.timeout(ANSWER_SECONDS):
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    answer = _json(line)
                    if answer.get("id") == 1:
                        break
            if not answer.get("ok"):
                return
            self.connected = True
            waiting, pinged = self.idle, False
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), waiting)
                except TimeoutError:
                    if pinged:
                        return   # asked, and nothing came back
                    writer.write(b'{"id": 2, "op": "status"}\n')   # the service has no ping: this is as cheap
                    await writer.drain()
                    waiting, pinged = PONG_SECONDS, True
                    continue
                if not line:
                    return
                waiting, pinged = self.idle, False
                push = _json(line)
                if "push" in push:
                    await self._deliver(push)
        finally:
            writer.close()

    async def _deliver(self, push: dict) -> None:
        try:
            await asyncio.wait_for(self.handle(push), PUSH_SECONDS)
        except TimeoutError:
            print(f"agentd: connect push {push.get('push')!r}: not handled in {PUSH_SECONDS:g} s", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - one push that cannot be handled never ends the watching
            print(f"agentd: connect push {push.get('push')!r}: {type(e).__name__}: {e}", file=sys.stderr)


def _first(seen: OrderedDict, key) -> bool:
    """True the first time a key is seen, and remembers it (the last REMEMBER of them)."""
    if key in seen:
        return False
    seen[key] = None
    while len(seen) > REMEMBER:
        seen.popitem(last=False)
    return True


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class Context:
    """What a proposal needs to be drawn: where it goes, and the thread or the task around it."""
    kind: str
    target: str
    where: str = ""
    new_place: bool = False
    messages: list = field(default_factory=list)
    task: dict | None = None
    fields: dict | None = None
    service: str = ""

    def card(self, proposal: dict | None = None) -> dict | None:
        """The card for this, with the proposal when there is one; None when there is nothing to show."""
        if self.kind == "slack_reply":
            return (cardmake.message_card(self.messages, ref=self.target, proposal=proposal)
                    if self.messages else None)
        return cardmake.task_card(service=self.service, task=self.task, proposal=proposal, task_fields=self.fields)


@dataclass
class Flow:
    """A Slack setup that is running in its thread."""
    connection: str
    notice: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    deadline: float = 0.0


class Says:
    """What agentd says above the pill and in the bar about connections.

    `card(card)` shows a card (agentd checks it and keeps it for a bar that connects later) and answers whether it
    was shown; `is_open(id)` says whether that card is the one on screen; `say(text, ok)` is a line in the pill;
    `open_url(url)` opens a page in the browser panel. All are agentd's. `setup(connection_id, say, cancelled)` is
    slack_setup.run_setup, found when first needed."""

    def __init__(self, notices: Notices, outbox, card: Callable[[dict], Awaitable[bool]],
                 say: Callable[..., Awaitable[None]], open_url: Callable[[str], Awaitable[None]],
                 is_open: Callable[[str], bool] = lambda _id: False, request: Callable[..., Awaitable] = ask,
                 clock: Callable[[], float] = time.time, setup: Callable | None = None,
                 mail_request: Callable[..., Awaitable] = mail_watch.ask):
        self.notices = notices
        self.outbox = outbox
        self.card = card
        self.say = say
        self.open_url = open_url
        self.is_open = is_open
        self.request = request
        self.clock = clock
        self.mail_request = mail_request
        self._setup = setup
        self._said: OrderedDict[str, None] = OrderedDict()           # message refs already said
        self._tasks: OrderedDict[str, None] = OrderedDict()          # task refs already said
        self._receipts: OrderedDict[str, None] = OrderedDict()       # proposals whose receipt was said
        self._by_conversation: OrderedDict[str, int] = OrderedDict()  # conversation -> its notice
        self._ready: OrderedDict[str, int] = OrderedDict()           # proposal id -> its "is ready" notice
        self._contexts: OrderedDict[str, Context] = OrderedDict()    # proposal id -> what its card is drawn from
        self._refs: OrderedDict[str, str] = OrderedDict()            # a card or row id that was hashed -> its ref
        self._bad: dict[str, str] = {}      # connection -> the state a warning was said for
        self._warned: dict[str, int] = {}   # connection -> its warning notice
        self._signing: dict[str, int] = {}  # connection -> the notice that says what to allow on the tool's page
        self._flows: dict[str, Flow] = {}   # service -> a setup running in its thread
        self._background: set[asyncio.Task] = set()

    async def push(self, msg: dict) -> None:
        kind = msg.get("push")
        if kind == "message":
            self.new_message(msg.get("message"))
        elif kind == "task":
            self.new_task(msg.get("item"))
        elif kind == "connection":
            await self.connection(msg.get("connection"))

    # -- messages --

    def new_message(self, m) -> None:
        """A message for the person is one line with Reply and Open; a new one in the same conversation takes the
        place of the last. A message the person has already read, or an old one, is left to the unread list."""
        if not isinstance(m, dict) or not isinstance(m.get("ref"), str) or m.get("unread") is False:
            return
        if not _number(m.get("ts")) or self.clock() - m["ts"] > OLD_SECONDS or not _first(self._said, m["ref"]):
            return
        key = conversation_key(m["ref"])
        line = _message_line(m)

        async def handler(action_id: str) -> bool:
            if action_id == "open":
                await self.open_message(m)
            else:
                await self.reply_to(m)
            return True

        chips = [{"id": "reply", "label": "Reply", "style": "primary"},
                 {"id": "open", "label": "Open", "style": "quiet"}]
        notice = self._by_conversation.get(key)
        if notice is not None and self.notices.replace(notice, line=line, tone="step", actions=chips,
                                                       ttl=MESSAGE_TTL, handler=handler):
            return
        self._by_conversation[key] = self.notices.post("connect", line, "step", chips, MESSAGE_TTL, handler)
        while len(self._by_conversation) > REMEMBER:
            self._by_conversation.popitem(last=False)

    async def open_message(self, m: dict) -> None:
        """Open on a message: its page on the service when it has one, else the card with its thread."""
        await self.saw(m["ref"])
        url = web_url(m.get("web_url"))
        if url:
            await self.open_url(url)
            return
        ctx = await self.context("slack_reply", m["ref"], fallback=[m])
        await self.show(ctx, self._open_proposal("slack_reply", m["ref"]))

    async def reply_to(self, m: dict) -> dict | None:
        """The person's Reply on a message: a proposal of theirs, with its card."""
        await self.saw(m["ref"])
        return await self.open_proposal("slack_reply", m["ref"], fallback=[m])

    async def saw(self, *refs: str) -> None:
        """Tell the service the person looked at these conversations. Their own note, never Slack's: a failure costs
        a message that stays on the unread list a little longer."""
        try:
            await asyncio.wait_for(self.request("seen", SEEN_SECONDS, refs=list(refs)), SEEN_SECONDS + 0.5)
        except (connect_client.ConnectUnavailable, connect_client.ConnectError, TimeoutError):
            pass

    # -- tasks --

    def new_task(self, item) -> None:
        """An item that waits on the person: one line with Open and Comment."""
        if not isinstance(item, dict) or not isinstance(item.get("ref"), str) or not _first(self._tasks, item["ref"]):
            return
        why = one_line(item.get("why"), 40)
        head = cardmake.service_title(item.get("service")) + (f", {why}" if why else "")
        line = f"{head}: {one_line(item.get('title'), 120)}"

        async def handler(action_id: str) -> bool:
            if action_id == "comment":
                await self.open_proposal("task_comment", item["ref"], fallback=[item])
                return True
            url = web_url(item.get("url"))
            if url:
                await self.open_url(url)
            else:
                ctx = await self.context("task_comment", item["ref"], fallback=[item])
                await self.show(ctx, self._open_proposal("task_comment", item["ref"]))
            return True

        self.notices.post("connect", line, "step", [{"id": "open", "label": "Open", "style": "primary"},
                                                    {"id": "comment", "label": "Comment", "style": "quiet"}],
                          TASK_TTL, handler)

    # -- connections --

    async def connection(self, conn) -> None:
        """A connection changed. Trouble (`error`, `blocked`) is one warning, once per state; a tool the person
        was sent to allow is told when it is; the open "Connected here" is redrawn."""
        if not isinstance(conn, dict) or not valid_connection_id(conn.get("id")):
            return
        cid, state = conn["id"], conn.get("state")
        title = cardmake.connection_title(conn)
        signing = self._signing.pop(cid, None)
        if state in ("error", "blocked"):
            if self._bad.get(cid) != state:
                self._bad[cid] = state
                note = one_line(conn.get("note"), 140)
                line = f"{title}: {note}" if note else \
                    f"{title} is blocked." if state == "blocked" else f"{title} is not working."
                self._dismiss(self._warned.pop(cid, None))
                chips = [{"id": "show", "label": "Show", "style": "quiet"}]
                if signing is not None and self.notices.replace(signing, line=line, tone="error", actions=chips,
                                                                ttl=WARNING_TTL, handler=self._shown_handler):
                    self._warned[cid] = signing
                else:
                    self._warned[cid] = self.notices.post("connect", line, "error", chips, WARNING_TTL,
                                                          self._shown_handler)
        else:
            self._bad.pop(cid, None)
            self._dismiss(self._warned.pop(cid, None))
            if state == "ok" and signing is not None:
                self.notices.replace(signing, line=f"{title} is connected.", tone="done", actions=[], ttl=20)
            elif state == "ok" and signing is None:
                pass
            elif signing is not None:
                self._signing[cid] = signing   # still signing in: the notice stands
        if self.is_open(cardmake.CONNECTED_ID):
            await self.show_connected()

    async def _shown_handler(self, _action_id: str) -> bool:
        await self.show_connected()
        return True

    def _dismiss(self, notice: int | None) -> None:
        if notice is not None:
            self.notices.dismiss(notice)

    # -- presses and proposals --

    def pressed(self, kind: str, id: str, result) -> None:
        """What a press of the person's came to: a receipt, or an outcome nobody can be sure of."""
        if result.ok and isinstance(result.receipt, dict):
            proposal = result.receipt.get("proposal") if isinstance(result.receipt.get("proposal"), str) else id
            if isinstance(proposal, str) and _first(self._receipts, proposal):
                self._receipt(proposal, result.receipt)
        elif result.code == "unknown_outcome":
            self.notices.post("connect", result.line, "error", (), 0)

    def _receipt(self, proposal: str, receipt: dict) -> None:
        self._dismiss(self._ready.pop(proposal, None))   # it is sent: "is ready" is no longer so
        web = receipt.get("web") if isinstance(receipt.get("web"), dict) else {}
        url = web_url(web.get("url"))
        chips = [{"id": "open", "label": f"Open in {one_line(web.get('name') or 'the browser', 24)}",
                  "style": "primary"}] if url else []

        async def handler(_action_id: str) -> bool:
            await self.open_url(url)
            return True

        self.notices.post("connect", one_line(receipt.get("line") or "Done."), "done", chips, RECEIPT_TTL,
                          handler if chips else None)

    def proposal_changed(self, p: dict) -> None:
        """A proposal is not open any more: what said it was ready does not."""
        if p.get("state") in ("sent", "discarded", "unknown"):
            self._dismiss(self._ready.pop(p.get("id"), None))

    def proposal_ready(self, p: dict, who: str, told: str = "") -> int:
        """The agent's proposal is on a card, and sending it is the person's. `told` is said once, in addition."""
        what = {"slack_reply": f"Reply to {who}", "task_comment": f"Comment for {who}",
                "task_create": f"New task for {who}"}.get(p["kind"], f"Draft for {who}")
        line = f"{what} is ready. Sending is yours." + (f" {told}" if told else "")

        async def handler(_action_id: str) -> bool:
            await self.show_proposal(p["id"])
            return True

        nid = self.notices.post("connect", line, "ask", [{"id": "open", "label": "Open", "style": "primary"}], 0,
                                handler)
        self._ready[p["id"]] = nid
        while len(self._ready) > REMEMBER:
            self._ready.popitem(last=False)
        return nid

    def _open_proposal(self, kind: str, target: str) -> dict | None:
        """The newest proposal still open for this, if any: what a second Reply goes back to."""
        found = [p for p in self.outbox.proposals.all()
                 if p["kind"] == kind and p["target"] == target and p["state"] == "open"]
        return max(found, key=lambda p: p["updated"]) if found else None

    async def open_proposal(self, kind: str, target: str, *, created_by: str = PERSON, content=None,
                            tainted: bool = False, typed: str = "", fallback=(), strict: bool = False) -> dict:
        """A proposal and its card. The person's own (no content) goes back to one that is already open for the
        same place; the agent's replaces the agent's earlier one, so there is one box to press for. `strict` is the
        agent's: the target must be something the service knows (`Unknown`). Raises proposals.ProposalError for
        content that is not text."""
        ctx = await self.context(kind, target, strict=strict, fallback=fallback)
        props = self.outbox.proposals
        p = self._open_proposal(kind, target) if created_by == PERSON and content is None else None
        if p is None:
            if created_by == AGENT:
                for old in props.all():
                    if old["kind"] == kind and old["target"] == target and old["state"] == "open" \
                            and old["created_by"] == AGENT:
                        props.discard(old["id"])
            p = props.open(kind, target, content, where=ctx.where, created_by=created_by, tainted=tainted,
                           typed=typed, new_place=ctx.new_place)
        self._contexts[p["id"]] = ctx
        while len(self._contexts) > REMEMBER:
            self._contexts.popitem(last=False)
        await self.show(ctx, p)
        return p

    async def show_proposal(self, id: str) -> bool:
        """Bring a proposal's card back (the Open on "is ready")."""
        p = self.outbox.proposals.get(id)
        if p is None:
            return False
        ctx = self._contexts.get(id) or await self.context(p["kind"], p["target"])
        return await self.show(ctx, p)

    async def show(self, ctx: Context, proposal: dict | None = None) -> bool:
        card = ctx.card(proposal)
        return await self.card(card) if card is not None else False

    # -- what the service knows --

    async def context(self, kind: str, target: str, *, strict: bool = False, fallback=()) -> Context:
        """Where a proposal goes and what its card is drawn from. `fallback` is what the caller already holds (the
        message that was pushed), used when the service cannot be asked. `strict` raises `Unknown` for a target the
        service does not know, or cannot be asked about."""
        if kind == "slack_reply":
            return await self._reply_context(target, strict, fallback)
        return await self._task_context(kind, target, strict, fallback)

    async def _reply_context(self, target: str, strict: bool, fallback) -> Context:
        thread, asked = [], False
        try:
            got = await self.request("thread", 6, ref=target, limit=THREAD_LIMIT)
            asked = True
            thread = [m for m in (got.get("messages") if isinstance(got, dict) else None) or []
                      if isinstance(m, dict) and isinstance(m.get("ref"), str)]
        except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
            if strict:
                raise Unknown(said(e)) from None
        if not thread:
            thread = [m for m in fallback if isinstance(m, dict) and isinstance(m.get("ref"), str)]
        if strict and not thread:
            raise Unknown("That is not a message I know.")
        if not thread:
            return Context("slack_reply", target)
        here = next((m for m in thread if m["ref"] == target), thread[-1])
        # A conversation the person has had nothing from and has not written in for two weeks is a new place: said
        # only when the service could say what the conversation holds.
        cutoff = self.clock() - NEW_PLACE_DAYS * 86400
        recent = any(m.get("mine") is True or (_number(m.get("ts")) and m["ts"] >= cutoff) for m in thread)
        return Context("slack_reply", target, where=cardmake.where_of(here), new_place=asked and not recent,
                       messages=thread)

    async def _task_context(self, kind: str, target: str, strict: bool, fallback) -> Context:
        service = target if kind == "task_create" else target.split(":", 1)[0]
        title = cardmake.service_title(service)
        conn = None
        try:
            conns = await self._connections()
            conn = next((c for c in conns if c.get("service") == service and c.get("state") == "ok"), None)
        except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
            if strict:
                raise Unknown(said(e)) from None
        fields = conn.get("task_fields") if conn else None
        part = "create" if kind == "task_create" else "comment"
        if strict and conn is None:
            raise Unknown(f"{title} is not connected, so there is nowhere to send that. Say connect {service}.")
        if strict and not (isinstance(fields, dict) and fields.get(part)):
            raise Unknown(f"{title} is connected for reading here: it does not take "
                          f"{'new tasks' if part == 'create' else 'comments'} yet.")
        task = None
        if kind == "task_comment":
            items = [t for t in fallback if isinstance(t, dict)]
            try:
                got = await self.request("tasks", 8, service=service, limit=30)
                items = [t for t in (got.get("items") if isinstance(got, dict) else None) or []
                         if isinstance(t, dict)] or items
            except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
                if strict:
                    raise Unknown(said(e)) from None
            task = next((t for t in items if t.get("ref") == target), None)
            if strict and task is None:
                raise Unknown("That is not a task I know. tasks_waiting lists the ones that wait on the person.")
        return Context(kind, target, where=title, task=task, fields=fields, service=service)

    async def _connections(self) -> list[dict]:
        got = await self.request("connections", 4)
        rows = got.get("connections") if isinstance(got, dict) else None
        return [c for c in rows or [] if isinstance(c, dict)]

    # -- lists --

    async def show_unread(self, messages: list[dict], skipped=()) -> bool:
        """The unread messages as the list card; each row opens its message."""
        for m in messages:
            if isinstance(m, dict) and isinstance(m.get("ref"), str):
                self._ref_of(cardmake.card_id("msg", m["ref"]), m["ref"])
        return await self.card(cardmake.unread_card(messages, self.clock(), skipped))

    async def show_thread(self, messages: list[dict], ref: str | None = None) -> bool:
        """A thread as the message card, with the person's own proposal for it when one is open."""
        ctx = Context("slack_reply", ref or "", messages=messages)
        thread = [m for m in messages if isinstance(m, dict) and isinstance(m.get("ref"), str)]
        if not thread:
            return False
        ctx.target = ref if any(m["ref"] == ref for m in thread) else thread[-1]["ref"]
        ctx.where = cardmake.where_of(next(m for m in thread if m["ref"] == ctx.target))
        return await self.show(ctx, self._open_proposal("slack_reply", ctx.target))

    async def show_tasks(self) -> None:
        return None

    def _ref_of(self, card_id: str, ref: str) -> None:
        self._refs[card_id] = ref
        while len(self._refs) > REMEMBER:
            self._refs.popitem(last=False)

    async def unread_list(self) -> tuple[bool, str]:
        """(shown, the line to say): the unread messages as a list card, or one plain line when there is nothing to
        read from."""
        try:
            if not [c for c in await self._connections() if c.get("state") != "off"]:
                return False, NOTHING
            got = await self.request("messages", 8, unread=True, limit=cardmake.MAX_ROWS)
        except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
            return False, said(e)
        rows = [m for m in (got.get("messages") if isinstance(got, dict) else None) or [] if isinstance(m, dict)]
        skipped = [one_line(s.get("connection"), 40) for s in (got.get("skipped") if isinstance(got, dict) else None)
                   or [] if isinstance(s, dict)]
        shown = await self.show_unread(rows, skipped)
        return shown, "Showing your unread messages." if rows else "Nothing unread right now."

    async def connected_list(self) -> tuple[bool, str]:
        """(shown, the line to say): "Connected here", from the connection service and the mail service."""
        conns, down = [], ""
        try:
            conns = await self._connections()
        except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
            down = said(e)
        accounts = []
        try:
            got = await self.mail_request("accounts", 3)
            accounts = [a for a in (got.get("accounts") if isinstance(got, dict) else None) or []
                        if isinstance(a, dict)]
        except Exception:  # noqa: BLE001 - mail is optional: no mail service is no mail rows
            pass
        if down and not accounts:
            return False, down
        say = f"{down} Only mail is listed." if down else ""
        return await self.card(cardmake.connected_card(conns, accounts, say)), "Showing what is connected."

    async def show_connected(self) -> bool:
        return (await self.connected_list())[0]

    # -- launcher words and card buttons --

    async def word(self, kind: str, target: str) -> tuple[bool, str]:
        """A launcher word (messages, connections, connect <service>): (ok, the line to say)."""
        if kind == "connect":
            return await self.connect_service(target)
        shown, line = await (self.connected_list() if target == "connected" else self.unread_list())
        return shown or line == "Nothing unread right now.", line

    async def card_action(self, card: str, row: str | None, action: str) -> None:
        """A button on a card (the shell's `card_action`). What came of it is said in the pill."""
        if action == "open" and isinstance(row, str) and row.startswith("msg:"):
            ref = self._refs.get(row) or row[len("msg:"):]
            if not valid_ref(ref):
                return
            await self.saw(ref)
            ctx = await self.context("slack_reply", ref)
            if not await self.show(ctx, self._open_proposal("slack_reply", ref)):
                await self.say("I could not find that message any more.", False)
        elif action == "disconnect" and isinstance(row, str):
            ok, text = await self.disconnect(row)
            await self.say(text, ok)
            if self.is_open(cardmake.CONNECTED_ID) or card == cardmake.CONNECTED_ID:
                await self.show_connected()
        elif action == "connect" and isinstance(row, str) and row.startswith("add:"):
            ok, text = await self.connect_service(row[len("add:"):])
            await self.say(text, ok)
            if card == cardmake.CONNECTED_ID:
                await self.show_connected()

    async def disconnect(self, row: str) -> tuple[bool, str]:
        """Take a connection or a mail account away. What was read stays gone with it."""
        kind, _, key = row.partition(":")
        try:
            if kind == "conn" and valid_connection_id(key):
                name = next((cardmake.connection_title(c) for c in await self._connections() if c.get("id") == key),
                            "that connection")
                await self.request("remove_connection", 6, id=key)
                self._flows.pop(key.split(":", 1)[-1], None)
                return True, f"Disconnected {name}. I no longer read it."
            if kind == "mail" and key:
                got = await self.mail_request("accounts", 3)
                name = next((one_line(a.get("email") or a.get("name"), 60) for a in got.get("accounts") or []
                             if isinstance(a, dict) and a.get("id") == key), "that account")
                await self.mail_request("remove_account", 8, id=key)
                return True, f"Disconnected {name}. I no longer read it."
        except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
            return False, said(e, wrote=True)
        except Exception as e:  # noqa: BLE001 - mail's own errors are its sentences too
            return False, one_line(e, 200) or "That did not work."
        return False, "I do not know that one."

    # -- setting up --

    async def connect_service(self, service: str) -> tuple[bool, str]:
        """Start the setup of a service: Slack's recipe in the panel, or a tool's sign-in page. (ok, the line to say)."""
        if service not in SERVICES:
            return False, "I do not know that service."
        title = cardmake.service_title(service)
        try:
            if service == "slack":
                return await self._slack(title)
            conn = await self.request("add_connection", 20, service=service)
        except (connect_client.ConnectUnavailable, connect_client.ConnectError) as e:
            return False, said(e, wrote=True)
        conn = conn if isinstance(conn, dict) else {}
        state, url = conn.get("state"), web_url(conn.get("open"))
        if state == "ok":
            return True, f"{title} is already connected."
        if not url:
            return False, one_line(conn.get("note"), 160) or f"{title} would not start. Try again in a moment."
        cid = str(conn.get("id") or f"mcp:{service}")
        await self.open_url(url)
        line = f"Allow Bombadil on {title}'s page."

        async def handler(_action_id: str) -> bool:
            await self.open_url(url)
            return False

        old = self._signing.pop(cid, None)
        self._dismiss(old)
        self._signing[cid] = self.notices.post("connect", line, "ask",
                                               [{"id": "open", "label": "Open the page", "style": "quiet"}],
                                               WARNING_TTL, handler)
        return True, f"Opening {title}'s page. Allow Bombadil there."

    async def _slack(self, title: str) -> tuple[bool, str]:
        if "slack" in self._flows and self._flows["slack"].connection:
            return True, "Setting up Slack is already under way."
        try:
            setup = self._setup or _slack_setup()
        except ImportError:
            return False, SLACK_UNAVAILABLE
        # A connection that was begun and never finished is picked up again, not left behind for a second one.
        stale = next((c for c in await self._connections() if c.get("service") == "slack"
                      and c.get("state") == "setup"), None)
        conn = stale or await self.request("add_connection", 20, service="slack")
        if not isinstance(conn, dict) or not valid_connection_id(conn.get("id")):
            return False, "Slack would not start. Try again in a moment."
        loop = asyncio.get_running_loop()
        flow = Flow(conn["id"], deadline=time.monotonic() + SETUP_SECONDS)

        async def cancel(_action_id: str) -> bool:
            flow.cancel.set()
            return False

        flow.notice = self.notices.post("connect", f"Setting up {title}.", "step",
                                        [{"id": "cancel", "label": "Cancel", "style": "quiet"}], 0, cancel)
        self._flows["slack"] = flow

        def beat(call, *args) -> None:
            try:
                loop.call_soon_threadsafe(call, flow, *args)
            except RuntimeError:
                pass   # agentd is stopping

        def work() -> None:
            try:
                result = setup(flow.connection, lambda sentence: beat(self._progress, sentence),
                               lambda: flow.cancel.is_set() or time.monotonic() > flow.deadline)
            except Exception as e:  # noqa: BLE001 - a setup that breaks ends in a sentence, never in a stack trace
                print(f"agentd: slack setup: {type(e).__name__}", file=sys.stderr)
                result = {"ok": False, "say": "Slack setup stopped before it finished."}
            beat(self._finished, result)

        threading.Thread(target=work, daemon=True, name="slack-setup").start()
        return True, "Setting up Slack. I will say each step."

    def _progress(self, flow: Flow, sentence) -> None:
        text = one_line(sentence, 200)
        if text and not self.notices.replace(flow.notice, line=text, tone="step"):
            flow.notice = self.notices.post("connect", text, "step",
                                            [{"id": "cancel", "label": "Cancel", "style": "quiet"}], 0, None)

    def _finished(self, flow: Flow, result) -> None:
        self._flows.pop("slack", None)
        result = result if isinstance(result, dict) else {}
        ok = result.get("ok") is True
        text = one_line(result.get("say"), 200) or ("Slack is connected." if ok else "Slack setup stopped.")
        if not self.notices.replace(flow.notice, line=text, tone="done" if ok else "error", actions=[],
                                    ttl=30 if ok else 0, handler=None):
            self.notices.post("connect", text, "done" if ok else "error", (), 30 if ok else 0)
        if flow.cancel.is_set():
            self._spawn(self._drop(flow.connection))
        elif self.is_open(cardmake.CONNECTED_ID):
            self._spawn(self.show_connected())

    async def _drop(self, connection: str) -> None:
        """A setup that was cancelled leaves nothing half-made behind, unless it got as far as working."""
        try:
            conn = next((c for c in await self._connections() if c.get("id") == connection), None)
            if conn is not None and conn.get("state") == "setup":
                await self.request("remove_connection", 6, id=connection)
        except (connect_client.ConnectUnavailable, connect_client.ConnectError):
            pass
        if self.is_open(cardmake.CONNECTED_ID):
            await self.show_connected()

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def stop_setup(self) -> bool:
        """The person said cancel (or Esc): a setup that is running stops at its next wait. True when one was."""
        flow = self._flows.get("slack")
        if flow is None:
            return False
        flow.cancel.set()
        return True

    def close(self) -> None:
        """agentd is ending: a setup in its thread stops, and what ran in the background is let go."""
        for flow in self._flows.values():
            flow.cancel.set()
        for task in list(self._background):
            task.cancel()


def _slack_setup() -> Callable:
    """slack_setup.run_setup, imported when a setup is first asked for: the service's other half is optional."""
    from . import slack_setup
    return slack_setup.run_setup


def _message_line(m: dict) -> str:
    """"Priya Shah in #launch: Legal just signed off. Can we lock the 14th…": the sender first, a colon, then what
    they wrote, cut. A direct message has no place to name."""
    sender = m.get("from") if isinstance(m.get("from"), dict) else {}
    who = one_line(sender.get("name") or "Someone", 40)
    kind = (m.get("conversation") or {}).get("kind") if isinstance(m.get("conversation"), dict) else None
    place = "" if kind == "dm" else f" in {cardmake.where_of(m)}"
    return f"{who}{place}: {cardmake.preview(m.get('text'), LINE_TEXT)}"

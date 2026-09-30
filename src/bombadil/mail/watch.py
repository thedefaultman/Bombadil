"""agentd's ear and voice for mail: what the mail service tells it unasked, and what it says about it.

The service pushes to whoever subscribed (docs/MAIL.md, "Wire protocol"): new mail, a send that
happened, a view somebody wants shown. `Watch` is the subscription, and `Says` turns each push,
and each draft and press agentd itself handles, into notices above the pill.

Why it is shaped this way:

- Mail is optional and must cost nothing when it is absent. `Watch` makes one connection attempt
  every RETRY_SECONDS and says nothing when it fails; its task never ends and never blocks
  anything, because every wait in it is a wait on a socket in agentd's own loop.
- A connection that stays open says nothing for hours, so silence is tested: after IDLE_SECONDS
  it asks the service to ping, and a service that does not answer in time is dropped and
  reconnected to (a hung service must not look like no new mail).
- One receipt per send. The press that started a send and the service's "sent" push both
  carry it, in either order; `Says` keeps the drafts it has already said and skips the second.
  A send that reaches the service without a press from here has no press to show for it: it
  is written in the press log and said plainly, because the person did not do it.
- Everything a notice says that came from mail (a name, a subject) is cut and cleaned by the
  notice itself; it is shown, never parsed.
"""

import asyncio
import json
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from .. import paths
from ..notices import Notices, one_line
from . import client as mail_client

RETRY_SECONDS = 5.0
IDLE_SECONDS = 45.0
PONG_SECONDS = 10.0
ANSWER_SECONDS = 5.0      # the subscribe answer
LINE_LIMIT = 4 << 20      # one push is a message or a receipt, never this
REMEMBER = 256            # message and draft ids said, so a repeated push is not said twice
NEW_MAIL_TTL = 300.0
RECEIPT_TTL = 120.0
DOWN = "Mail is not running yet."


class Watch:
    """The subscription to mail.sock. `handle(push)` gets every push, in order; a failure in it is
    reported and the watching goes on."""

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
            self.connected = False
            await asyncio.sleep(self.retry)

    async def _session(self) -> None:
        reader, writer = await asyncio.open_unix_connection(str(paths.mail_socket()), limit=LINE_LIMIT)
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
                    writer.write(b'{"id": 2, "op": "ping"}\n')
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
            await self.handle(push)
        except Exception as e:  # noqa: BLE001 - one push that cannot be handled never ends the watching
            print(f"agentd: mail push {push.get('push')!r}: {type(e).__name__}: {e}", file=sys.stderr)


def _json(line: bytes) -> dict:
    try:
        msg = json.loads(line)
    except ValueError:
        return {}
    return msg if isinstance(msg, dict) else {}


async def ask(op: str, timeout: float, **args):
    """One request to the service, off the loop: the client blocks."""
    return await asyncio.to_thread(mail_client.request, op, timeout=timeout, **args)


def said(e: Exception) -> str:
    """What to tell a person when the service did not do it: its own sentence, or that it is not there."""
    return str(e) if isinstance(e, mail_client.MailError) else DOWN


def short_name(addr) -> str:
    """Who, the way a person says it: "Priya" for Priya Shah <priya@acme.example>, else the address."""
    addr = addr if isinstance(addr, dict) else {}
    words = one_line(addr.get("name") or "", 40).split()
    return words[0].strip(",") if words and words[0].strip(",") else one_line(addr.get("email") or "someone", 60)


class Says:
    """What agentd says above the pill about mail.

    `show(**view)` asks the service to show something and brings the Mail window in; `window()`
    only brings it in (the service asked for the view itself); `open_url(url)` opens a link in the
    browser panel. All three are agentd's."""

    def __init__(self, notices: Notices, outbox, show: Callable[..., Awaitable[None]],
                 window: Callable[[], Awaitable[None]], open_url: Callable[[str], Awaitable[None]],
                 request: Callable[..., Awaitable] = ask):
        self.notices = notices
        self.outbox = outbox
        self.show = show
        self.window = window
        self.open_url = open_url
        self.request = request
        self._mails: OrderedDict[str, None] = OrderedDict()     # mail ids already said
        self._receipts: OrderedDict[str, None] = OrderedDict()  # drafts whose receipt was said

    async def push(self, msg: dict) -> None:
        kind = msg.get("push")
        if kind == "new_mail":
            self.new_mail(msg)
        elif kind == "sent":
            self.sent(msg.get("receipt"))
        elif kind == "show":
            await self.window()   # somebody asked for a view: the window is there when it draws

    # -- new mail --

    def new_mail(self, msg: dict) -> None:
        """New mail from someone the person knows is one line with Reply and Open. Mail from
        anyone else waits in the view: a stranger does not get the pill."""
        mail = msg.get("message")
        if not isinstance(mail, dict) or msg.get("known") is not True:
            return
        mail_id = mail.get("id")
        if (not isinstance(mail_id, str) or mail.get("unread") is False
                or mail.get("folder") not in (None, "inbox") or not self._first(self._mails, mail_id)):
            return
        sender = mail.get("from") if isinstance(mail.get("from"), dict) else {}
        who = one_line(sender.get("name") or sender.get("email") or "Someone", 40)
        line = f"{who}: {one_line(mail.get('subject') or '(no subject)', 120)}"

        async def handler(action_id: str) -> bool:
            if action_id == "open":
                await self.show(id=mail_id)
                return True
            try:
                draft = await self.request("draft", 8, reply_to=mail_id, created_by="person")
            except (mail_client.MailUnavailable, mail_client.MailError) as e:
                self.notices.replace(notice, line=said(e), tone="error", actions=[], ttl=20)
                return False
            await self.show(**_where(draft if isinstance(draft, dict) else {"reply_to": mail_id}))
            return True

        notice = self.notices.post(
            "mail", line, "ask", [{"id": "reply", "label": "Reply", "style": "primary"},
                                  {"id": "open", "label": "Open", "style": "quiet"}], NEW_MAIL_TTL, handler)

    # -- drafts --

    def draft_ready(self, draft: dict, told: str = "") -> int:
        """A draft is in the view, and sending it is the person's. `told` is what is said once, in addition."""
        to = draft.get("to") if isinstance(draft.get("to"), list) else []
        who = short_name(to[0] if to else {}) + (f" and {len(to) - 1} more" if len(to) > 1 else "")
        what = "Reply to" if draft.get("reply_to") else "Mail to"
        line = f"{what} {who} is ready. Sending is yours." + (f" {told}" if told else "")

        async def handler(_action_id: str) -> bool:
            await self.show(**_where(draft))
            return True

        return self.notices.post("mail", line, "ask", [{"id": "open", "label": "Open", "style": "primary"}],
                                 0, handler)

    # -- sends --

    def sent(self, receipt) -> None:
        """A send the service says happened. The press that made it has said it already, unless
        the press came later than this push; and a send no press here started is said as that."""
        if not isinstance(receipt, dict) or not isinstance(receipt.get("draft"), str):
            return
        if not self._first(self._receipts, receipt["draft"]):
            return
        pressed = self.outbox.was_pressed("mail", receipt["draft"])
        if not pressed:
            self.outbox.saw_send("mail", receipt["draft"])
        self._receipt(receipt, "" if pressed else " That was not your press on Send.",
                      "done" if pressed else "error")

    def pressed(self, result) -> None:
        """What a press of the person's came to: a receipt, or an outcome nobody can be sure of."""
        if result.ok and isinstance(result.receipt, dict):
            draft = result.receipt.get("draft")
            if isinstance(draft, str) and self._first(self._receipts, draft):
                self._receipt(result.receipt, "", "done")
        elif result.code == "unknown_outcome":
            self.notices.post("mail", result.line, "error", (), 0)

    def _receipt(self, receipt: dict, more: str, tone: str) -> None:
        web = receipt.get("web") if isinstance(receipt.get("web"), dict) else {}
        url = str(web.get("url") or "")
        actions = ([{"id": "open", "label": f"Open in {one_line(web.get('name') or 'the browser', 24)}",
                     "style": "primary"}] if url.startswith(("https://", "http://")) else [])

        async def handler(_action_id: str) -> bool:
            await self.open_url(url)
            return True

        self.notices.post("mail", one_line(receipt.get("line") or "Sent.") + more, tone, actions,
                          RECEIPT_TTL if tone == "done" else 0, handler if actions else None)

    @staticmethod
    def _first(seen: OrderedDict, key: str) -> bool:
        """True the first time a key is seen, and remembers it (the last REMEMBER of them)."""
        if key in seen:
            return False
        seen[key] = None
        while len(seen) > REMEMBER:
            seen.popitem(last=False)
        return True


def _where(draft: dict) -> dict:
    """Where the Mail window goes to show a draft: on the mail it answers, reply box open, or in Drafts."""
    if isinstance(draft.get("reply_to"), str) and draft["reply_to"]:
        return {"id": draft["reply_to"], "reply": draft.get("id")}
    return {"view": "drafts", "id": draft.get("id")}

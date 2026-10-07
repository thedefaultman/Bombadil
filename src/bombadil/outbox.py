"""The outbox: where a press becomes exactly one act, and where every press is written down.

Nothing that reaches another person is sent by the agent. A draft waits in a view; the person's
press on the view's Send comes to agentd as {"type": "press", "kind": "mail", "id": draft,
"fingerprint": fp}, and `Outbox.press` is the only way from there to the service that does the
sending. A kind is a performer registered here ("mail", and the three that go through the connection service:
"slack_reply", "task_create" and "task_comment", which are proposals, proposals.py), so the rules below hold for
each without being written again.

Why it is shaped this way:

- A press from inside an agent's turn is refused. The turn's CLI and everything it started share
  one systemd scope (procs.cgroup_of), and without a scope the turn's own process tree is what
  marks it, so a press from either is not the person's. A press whose sender cannot be told is
  refused too: the check must fail toward not sending. That includes a sender that is gone: the
  kernel names the process that connected, not whoever holds the socket now, so a process of the
  turn can hand its connection to a child and exit, and what is left to look at is nothing.
- One press, at most one act. The performer is called once and its answer is final: nothing
  here retries, and a second press of the same thing while the first still runs is turned away
  instead of queued. A send that may or may not have happened is said as that, in words, with
  where to look, never guessed at.
- Every press leaves one row in presses.jsonl (when, what, which fingerprint, who, how it ended)
  and never a word of the mail. The file is private (0600) and rotated at a size; a refusal that is
  only the same one again is not written again for a few seconds, so a process that presses in a
  loop can neither fill the disk nor push the rows of real presses out of the file.
- "I never press Send for you" is said once in a person's life with the machine, so it is kept in
  told.json beside the other state.
- A proposal's press (docs/CONNECT.md) is the same press with more to check first: the proposal is open, has
  something in it, and the fingerprint that was pressed, the proposal's own and the one the shell said it drew all
  agree. It is marked `sending` before the connection service is asked, once, and ends `sent`, `unknown` (the service
  may have done it; it is never tried again by itself, and the person's own second press is a new attempt with a new
  id) or open again with the service's own sentence when it said no.

This is a rule with a check, not yet a wall: the agent runs as the person (docs/MAIL.md, "The
press"). A process it starts outside its scope, with systemd-run --user, still passes the check
here; closing that is the installed-OS brief's hardening.
"""

import asyncio
import json
import os
import re
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import paths, procs
from .connect import client as connect_client
from .connect.driver import KINDS
from .mail import client as mail_client
from .proposals import Proposals, is_empty, perform_id

PERFORM_SECONDS = 90.0      # no performer is waited for longer than this
MAIL_SEND_SECONDS = 70.0    # the service gives up on the engine at 60; this is its time to say so
CONNECT_SECONDS = 25.0      # the Slack driver gives up on a post at 20; this is the service's time to say so
LOG_ROTATE_BYTES = 1 << 20
REMEMBER = 256              # presses remembered for the "went without a press" check
REFUSAL_REPEAT_SECONDS = 5.0   # the same refusal is written to the log once per this
# What a press that did not end in a plain yes or no leaves behind: a send may have gone, so one the
# service reports afterwards is not a send nobody pressed.
UNSURE = ("unknown_outcome", "error")
# Refusals that say something about who pressed and not about the draft; a process can make a lot of them.
REPEATS = ("agent", "no_peer", "bad_request", "unknown_kind", "busy")
TOLD_MAIL = "mail-press"
TOLD_MAIL_LINE = "I never press Send for you. Change anything in it first if you like."
TOLD_CONNECT = "connect-press"

NOT_SENT = "Nothing was sent."
UNKNOWN_LINE = "I can't tell whether that went. Look in Sent before you press Send again."
MAIL_DOWN = "Mail is not running yet. " + NOT_SENT
CONNECT_DOWN = "Connections are not running yet. " + NOT_SENT
THEIR_NAMES = {"slack": "Slack", "linear": "Linear", "notion": "Notion", "jira": "Jira", "todoist": "Todoist",
               "clickup": "ClickUp"}
_FIELD = re.compile(r"[^\x21-\x7e]")   # a printable, space-free field; nothing else is ever logged


@dataclass
class PressResult:
    ok: bool
    line: str                     # one plain sentence for the person
    receipt: dict | None = None   # what the performer says it did ("line", "web", ...), when it did
    code: str = ""                # why not, for the log and for the window: "agent", "busy", "changed", ...


# Does the act for one press. It is called once, and what it returns is final. `again=True` is passed
# only when the person chose to press again on something whose outcome was not known.
Performer = Callable[..., Awaitable[PressResult]]


def _receipt(result) -> dict | None:
    if isinstance(result, dict):
        inner = result.get("receipt")
        if isinstance(inner, dict):
            return inner
        if "line" in result:
            return result
    return None


def _send_mail(draft: str, fingerprint: str, again: bool = False) -> PressResult:
    """Ask the mail service to send one draft, waiting as long as a send may take. A service that
    cannot be reached was never asked, so nothing went; one that stops answering after it was
    asked may have sent, which is said as that."""
    try:
        conn = mail_client.Connection(timeout=MAIL_SEND_SECONDS)
    except mail_client.MailUnavailable:
        return PressResult(False, MAIL_DOWN, code="engine_down")
    with conn:
        try:
            result = conn.request("send", timeout=MAIL_SEND_SECONDS, id=draft, fingerprint=fingerprint,
                                  **({"again": True} if again else {}))
        except mail_client.MailUnavailable:
            return PressResult(False, UNKNOWN_LINE, code="unknown_outcome")
        except mail_client.MailError as e:
            code = str(getattr(e, "code", "") or "error")
            return PressResult(False, UNKNOWN_LINE if code == "unknown_outcome" else str(e), code=code)
    receipt = _receipt(result)
    return PressResult(True, str((receipt or {}).get("line") or "Sent."), receipt)


async def send_mail(draft: str, fingerprint: str, again: bool = False) -> PressResult:
    return await asyncio.to_thread(_send_mail, draft, fingerprint, again)


def _unknown_connect(kind: str, target: str) -> str:
    """Where to look when nobody can say whether a proposal went: the service it was for."""
    service = str(target).split(":", 1)[0]
    where = "Slack" if kind == "slack_reply" else THEIR_NAMES.get(service, "the tool")
    return f"I can't tell whether that went. Look in {where} before you press Send again."


def _perform_connect(kind: str, target: str, content, fingerprint: str, proposal: str) -> tuple[str, PressResult]:
    """Ask the connection service to do one thing, waiting as long as it may take. What came of it is `sent`, `open`
    (the service said no, or was never reached: nothing went) or `unknown` (it was asked and then said nothing, or said
    it cannot tell, or says that proposal was done already: it may have gone). A service that cannot be reached was
    never asked."""
    try:
        conn = connect_client.Connection(timeout=CONNECT_SECONDS)
    except connect_client.ConnectUnavailable:
        return "open", PressResult(False, CONNECT_DOWN, code="service_down")
    unsure = PressResult(False, _unknown_connect(kind, target), code="unknown_outcome")
    with conn:
        try:
            result = conn.request("perform", timeout=CONNECT_SECONDS, kind=kind, target=target, content=content,
                                  fingerprint=fingerprint, proposal=proposal)
        except connect_client.ConnectUnavailable:
            return "unknown", unsure
        except connect_client.ConnectError as e:
            code = str(getattr(e, "code", "") or "error")
            if code in ("unknown_outcome", "already", "internal"):
                return "unknown", unsure
            return "open", PressResult(False, str(e), code=code)
    receipt = _receipt(result)
    return "sent", PressResult(True, str((receipt or {}).get("line") or "Done."), receipt)


def _field(value, limit: int = 128) -> str:
    return _FIELD.sub("", str(value))[:limit]


class Outbox:
    def __init__(self, performers: dict[str, Performer] | None = None,
                 in_turn: Callable[[int], bool] | None = None, proposals: Proposals | None = None):
        """`in_turn` says whether a process belongs to the turn that is running, for a turn that has
        no systemd scope (agentd knows its process tree). `proposals` is what waits for a press."""
        self.proposals = proposals if proposals is not None else Proposals()
        self.performers: dict[str, Performer] = {
            "mail": send_mail, **{kind: self._proposal_performer(kind) for kind in KINDS}, **(performers or {})}
        self.in_turn = in_turn
        self._busy: set[tuple[str, str]] = set()
        self._pressed: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._refused_at: dict[tuple, float] = {}    # when a refusal of this kind was last written

    def register(self, kind: str, performer: Performer) -> None:
        self.performers[kind] = performer

    def was_pressed(self, kind: str, id: str) -> bool:
        """Did a press of this go, or may it have? A send the service reports that none started, or that
        every press was refused for, was not the person's press. (A refusal is not remembered: a draft
        whose press was turned away once and which then goes by another way must not look pressed.)"""
        return (kind, id) in self._busy or (kind, id) in self._pressed

    async def press(self, kind, id, fingerprint, peer_pid: int | None, again: bool = False,
                    was_agent: bool = False) -> PressResult:
        """`was_agent`: the connection this came on was already known to belong to an agent's turn (or to
        nobody who could be told) when it was made, which no later look can undo."""
        result = await self._press(kind, id, fingerprint, peer_pid, again, was_agent)
        self._log(kind, id, fingerprint, result.ok, result.code, peer_pid)
        return result

    async def _press(self, kind, id, fingerprint, peer_pid, again: bool = False,
                     was_agent: bool = False) -> PressResult:
        if not isinstance(peer_pid, int) or isinstance(peer_pid, bool) or peer_pid <= 0:
            return PressResult(False, "I could not tell who pressed. " + NOT_SENT, code="no_peer")
        if was_agent or await asyncio.to_thread(self.from_agent, peer_pid):
            # A window the agent itself opened lives in that turn's scope for as long as it runs, so this is
            # also what the person sees from one: the way out is a window opened from the pill.
            return PressResult(False, "That press came from inside an agent's turn, and sending is yours. "
                                      "If this is your own window, close it and open it again from the pill. "
                                      + NOT_SENT, code="agent")
        performer = self.performers.get(kind) if isinstance(kind, str) else None
        if performer is None:
            return PressResult(False, "That is not something I send. " + NOT_SENT, code="unknown_kind")
        if not (isinstance(id, str) and isinstance(fingerprint, str) and 0 < len(id) <= 128
                and 0 < len(fingerprint) <= 128):
            return PressResult(False, "That press did not say what it was for. " + NOT_SENT,
                               code="bad_request")
        key = (kind, id)
        if key in self._busy:
            return PressResult(False, "That is already being sent.", code="busy")
        self._busy.add(key)
        result = None
        try:
            result = await asyncio.wait_for(performer(id, fingerprint, again=True) if again
                                            else performer(id, fingerprint), PERFORM_SECONDS)
        except TimeoutError:
            result = PressResult(False, UNKNOWN_LINE, code="unknown_outcome")
        except Exception as e:  # noqa: BLE001 - a press ends in a sentence, whatever the performer did
            print(f"agentd: press {kind}: {type(e).__name__}: {e}", file=sys.stderr)
            result = PressResult(False, "That did not go through, and I can't say why. Look where it would "
                                        "have gone before you press again.", code="error")
        finally:
            self._busy.discard(key)
            if result is None or result.ok or result.code in UNSURE:   # (None: cancelled, so not known)
                self._pressed[key] = None
                while len(self._pressed) > REMEMBER:
                    self._pressed.popitem(last=False)
        return result

    def _proposal_performer(self, kind: str) -> Performer:
        async def perform(id: str, fingerprint: str, again: bool = False) -> PressResult:
            return await self._perform_proposal(kind, id, fingerprint, again)
        return perform

    async def _perform_proposal(self, kind: str, id: str, fingerprint: str, again: bool) -> PressResult:
        props = self.proposals
        p = props.record(id)
        if p is None or p["kind"] != kind:
            return PressResult(False, "I no longer have that one. " + NOT_SENT, code="changed")
        if p["state"] == "sent":
            # Pressed twice: the same receipt and no second act (the service would say "already" as well).
            return PressResult(True, str((p["receipt"] or {}).get("line") or "Sent."), p["receipt"])
        again = again and p["state"] == "unknown"
        if p["state"] == "unknown" and not again:
            return PressResult(False, _unknown_connect(kind, p["target"]), code="unknown_outcome")
        if p["state"] not in ("open", "unknown"):
            return PressResult(False, "That was put away. " + NOT_SENT, code="changed")
        if is_empty(p["content"]):
            return PressResult(False, "There is nothing in it to send yet. " + NOT_SENT, code="changed")
        if not (fingerprint == p["fingerprint"] == p["shown"]):
            return PressResult(False, "That is not what the card showed. " + NOT_SENT, code="changed")
        if props.begin_send(id, again) is None:
            return PressResult(False, "That is not what the card showed. " + NOT_SENT, code="changed")
        try:
            outcome, result = await asyncio.to_thread(_perform_connect, kind, p["target"], p["content"],
                                                      p["fingerprint"], perform_id(props.record(id)))
        except BaseException:
            props.unknown(id, _unknown_connect(kind, p["target"]))   # cancelled mid-send: it may have gone
            raise
        if outcome == "sent":
            result.receipt = {**(result.receipt or {"line": result.line}), "proposal": id}
            props.sent(id, result.receipt)
        elif outcome == "unknown":
            props.unknown(id, result.line)
        else:
            props.reopen(id, result.line)
        return result

    def from_agent(self, pid: int) -> bool:
        """Is this process inside an agent's turn, or not one that can be told? What only the person does (a
        press, a chip on a notice) is refused to it. Blocking: it reads /proc.

        The process has to be there at the end of the looking, not only the start: one that is gone has no
        scope to read, which would pass for "not in a turn", and a process of the turn that connects, hands
        the socket to a child and exits is exactly that. So what is read comes first and being alive is
        checked last."""
        if procs.cgroup_of(pid) is not None or bool(self.in_turn and self.in_turn(pid)):
            return True
        return _gone(pid)

    def saw_send(self, kind: str, id: str) -> None:
        """The service reported a send that no press here started: written down as that."""
        self._log(kind, id, "", True, "no_press", 0)

    def _log(self, kind, id, fingerprint, ok: bool, code: str, pid) -> None:
        """One row: when, what, which fingerprint, from which process, how it ended. Never mail text:
        the fields are what the sender said they were, so each is cut and held to printable characters."""
        who = pid if isinstance(pid, int) and not isinstance(pid, bool) else 0
        now = time.monotonic()
        if code in REPEATS:
            # The same refusal from the same process again is the same news: a loop of them must not
            # rotate the rows of real presses out of the file.
            if now - self._refused_at.get((who, code), -REFUSAL_REPEAT_SECONDS) < REFUSAL_REPEAT_SECONDS:
                return
            if len(self._refused_at) >= REMEMBER:
                self._refused_at.clear()
            self._refused_at[(who, code)] = now
        # The service writes its own rows in the same file, with src "mail".
        row = {"t": round(time.time(), 3), "kind": _field(kind, 24), "id": _field(id),
               "fingerprint": _field(fingerprint), "ok": bool(ok), "code": code, "pid": who, "src": "agentd"}
        path = paths.press_log()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
                path.replace(path.with_name(path.name + ".1"))
            with _private(path, os.O_APPEND) as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            print(f"agentd: press log: {e}", file=sys.stderr)   # a log that fails never stops a press


def _gone(pid: int) -> bool:
    """Has this process ended, or is it only waiting to be reaped? What cannot be read is gone."""
    try:
        stat = (procs.PROC / str(pid) / "stat").read_text()
        return stat[stat.rindex(")") + 2] in "ZX"
    except (OSError, ValueError, IndexError):
        return True


def _private(path: Path, flags: int = os.O_TRUNC):
    """`path` opened for writing as text and readable by the person alone, whatever it was before."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "w")
    except BaseException:
        os.close(fd)
        raise


_said: set[tuple[Path, str]] = set()   # what this process has said, for a told.json that cannot be written


def tell_once(key: str, line: str) -> str:
    """`line` the first time it is asked for under this key, ever on this machine, then nothing.
    Kept in told.json; a file that cannot be read or written costs at worst the sentence again."""
    path = paths.state_dir() / "told.json"
    try:
        told = json.loads(path.read_text())
        told = told if isinstance(told, dict) else {}
    except (OSError, ValueError):
        told = {}
    if key in told or (path, key) in _said:
        return ""
    _said.add((path, key))
    told[key] = round(time.time())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        with _private(tmp) as f:
            f.write(json.dumps(told))
        os.replace(tmp, path)
    except OSError as e:
        print(f"agentd: told.json: {e}", file=sys.stderr)
    return line


def told_mail_press() -> str:
    """The sentence a person hears once with their first draft from the agent, or ""."""
    return tell_once(TOLD_MAIL, TOLD_MAIL_LINE)


def told_connect_press() -> str:
    """The same, once, with the first reply or task the agent puts on a card."""
    return tell_once(TOLD_CONNECT, TOLD_MAIL_LINE)

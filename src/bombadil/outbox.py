"""The outbox: where a press becomes exactly one act, and where every press is written down.

Nothing that reaches another person is sent by the agent. A draft waits in a view; the person's
press on the view's Send comes to agentd as {"type": "press", "kind": "mail", "id": draft,
"fingerprint": fp}, and `Outbox.press` is the only way from there to the service that does the
sending. A kind is a performer registered here ("mail" now; a Slack reply or a ticket later), so
the rules below hold for each without being written again.

Why it is shaped this way:

- A press from inside an agent's turn is refused. The turn's CLI and everything it started share
  one systemd scope (procs.cgroup_of), and without a scope the turn's own process tree is what
  marks it, so a press from either is not the person's. A press whose sender cannot be told is
  refused too: the check must fail toward not sending.
- One press, at most one act. The performer is called once and its answer is final: nothing
  here retries, and a second press of the same thing while the first still runs is turned away
  instead of queued. A send that may or may not have happened is said as that, in words, with
  where to look, never guessed at.
- Every press leaves one row in presses.jsonl (when, what, which fingerprint, who, how it ended)
  and never a word of the mail. The file is rotated at a size, so a process that presses in a loop
  cannot fill the disk with refusals.
- "I never press Send for you" is said once in a person's life with the machine, so it is kept in
  told.json beside the other state.

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
from .mail import client as mail_client

PERFORM_SECONDS = 90.0      # no performer is waited for longer than this
MAIL_SEND_SECONDS = 70.0    # the service gives up on the engine at 60; this is its time to say so
LOG_ROTATE_BYTES = 1 << 20
REMEMBER = 256              # presses remembered for the "went without a press" check
TOLD_MAIL = "mail-press"
TOLD_MAIL_LINE = "I never press Send for you. Change anything in it first if you like."

NOT_SENT = "Nothing was sent."
UNKNOWN_LINE = "I can't tell whether that went. Look in Sent before you press Send again."
MAIL_DOWN = "Mail is not running yet. " + NOT_SENT
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


def _field(value, limit: int = 128) -> str:
    return _FIELD.sub("", str(value))[:limit]


class Outbox:
    def __init__(self, performers: dict[str, Performer] | None = None,
                 in_turn: Callable[[int], bool] | None = None):
        """`in_turn` says whether a process belongs to the turn that is running, for a turn that has
        no systemd scope (agentd knows its process tree)."""
        self.performers: dict[str, Performer] = {"mail": send_mail, **(performers or {})}
        self.in_turn = in_turn
        self._busy: set[tuple[str, str]] = set()
        self._pressed: OrderedDict[tuple[str, str], None] = OrderedDict()

    def register(self, kind: str, performer: Performer) -> None:
        self.performers[kind] = performer

    def was_pressed(self, kind: str, id: str) -> bool:
        """Did a press of this start here? A send the service reports that none started was not
        the person's press."""
        return (kind, id) in self._pressed

    async def press(self, kind, id, fingerprint, peer_pid: int | None, again: bool = False) -> PressResult:
        result = await self._press(kind, id, fingerprint, peer_pid, again)
        self._log(kind, id, fingerprint, result.ok, result.code, peer_pid)
        return result

    async def _press(self, kind, id, fingerprint, peer_pid, again: bool = False) -> PressResult:
        if not isinstance(peer_pid, int) or isinstance(peer_pid, bool) or peer_pid <= 0:
            return PressResult(False, "I could not tell who pressed. " + NOT_SENT, code="no_peer")
        if await asyncio.to_thread(self._from_agent, peer_pid):
            return PressResult(False, "That press came from inside an agent's turn, and sending is yours. "
                                      + NOT_SENT, code="agent")
        performer = self.performers.get(kind) if isinstance(kind, str) else None
        if performer is None:
            return PressResult(False, "Nothing here sends that. " + NOT_SENT, code="unknown_kind")
        if not (isinstance(id, str) and isinstance(fingerprint, str) and 0 < len(id) <= 128
                and 0 < len(fingerprint) <= 128):
            return PressResult(False, "That press did not say what it was for. " + NOT_SENT,
                               code="bad_request")
        key = (kind, id)
        if key in self._busy:
            return PressResult(False, "That is already being sent.", code="busy")
        self._busy.add(key)
        self._pressed[key] = None
        while len(self._pressed) > REMEMBER:
            self._pressed.popitem(last=False)
        try:
            return await asyncio.wait_for(performer(id, fingerprint, again=True) if again
                                          else performer(id, fingerprint), PERFORM_SECONDS)
        except TimeoutError:
            return PressResult(False, UNKNOWN_LINE, code="unknown_outcome")
        except Exception as e:  # noqa: BLE001 - a press ends in a sentence, whatever the performer did
            print(f"agentd: press {kind}: {type(e).__name__}: {e}", file=sys.stderr)
            return PressResult(False, "That did not go through, and I can't say why. Look where it would "
                                      "have gone before you press again.", code="error")
        finally:
            self._busy.discard(key)

    def _from_agent(self, pid: int) -> bool:
        if procs.cgroup_of(pid) is not None:
            return True
        return bool(self.in_turn and self.in_turn(pid))

    def saw_send(self, kind: str, id: str) -> None:
        """The service reported a send that no press here started: written down as that."""
        self._log(kind, id, "", True, "no_press", 0)

    def _log(self, kind, id, fingerprint, ok: bool, code: str, pid) -> None:
        """One row: when, what, which fingerprint, from which process, how it ended. Never mail text:
        the fields are what the sender said they were, so each is cut and held to printable characters."""
        who = pid if isinstance(pid, int) and not isinstance(pid, bool) else 0
        # The service writes its own rows in the same file, with src "mail".
        row = {"t": round(time.time(), 3), "kind": _field(kind, 24), "id": _field(id),
               "fingerprint": _field(fingerprint), "ok": bool(ok), "code": code, "pid": who, "src": "agentd"}
        path = paths.press_log()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
                path.replace(path.with_name(path.name + ".1"))
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            print(f"agentd: press log: {e}", file=sys.stderr)   # a log that fails never stops a press


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
        tmp.write_text(json.dumps(told))
        os.replace(tmp, path)
    except OSError as e:
        print(f"agentd: told.json: {e}", file=sys.stderr)
    return line


def told_mail_press() -> str:
    """The sentence a person hears once with their first draft from the agent, or ""."""
    return tell_once(TOLD_MAIL, TOLD_MAIL_LINE)

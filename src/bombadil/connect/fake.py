"""A connection driver that is not Slack or an MCP service: a sample workspace and task list in memory, for tests and
for anyone without an account.

`FakeDriver` has the whole surface of `driver.Driver` and answers every one of its calls the way the contract
says, so the service cannot tell which kind it has. `BOMBADIL_CONNECT_ENGINE=fake` runs the service with one for
every connection and a store that starts with a connected workspace "Acme" (`slack:w1`) and `mcp:linear`: the
window, the desktop test and the VM smoke use it, and so can anyone else.

Everything the person could read is invented: Priya Shah and Marcus Webb in Acme's #launch, two tasks in a
Linear-like list. A connection added to the fake behaves like a real one that has to be set up: a workspace waits
in `setup` for the two tokens (`store_secret`, with Slack's prefixes) and a tool goes through `signin` and is
allowed a moment later (BOMBADIL_CONNECT_FAKE_SIGNIN_S, 2 s by default).

Tests steer it: `inject` delivers a message or a task as if the service had sent it (the service's `fake_inject`
op is that), `perform` fails when the content holds "[refuse]" (nothing went) or "[unknown]" (it may have), and
`performed` records everything that was done, so a test asserts exactly what left.
"""

import asyncio
import json
import os
import time
from typing import Any

from . import protocol
from .driver import Driver, DriverError, UnknownOutcome

WORKSPACE = "Acme"
SLACK_RULES = {"app_token": "xapp-", "user_token": "xoxp-"}
SIGNIN_SECONDS = 2.0
REFUSE, UNKNOWN = "[refuse]", "[unknown]"
FIRST_NUMBER = 42          # the number the first task made here gets


def _signin_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("BOMBADIL_CONNECT_FAKE_SIGNIN_S") or SIGNIN_SECONDS))
    except ValueError:
        return SIGNIN_SECONDS


def _at(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


class FakeDriver(Driver):
    kind = "fake"
    secret_rules = dict(SLACK_RULES)

    def __init__(self, conn, secrets, emit, clock):
        super().__init__(conn, secrets, emit, clock)
        self.is_slack = conn.get("kind") == "slack"
        self.service = str(conn.get("service") or "")
        self.secret_rules = dict(SLACK_RULES) if self.is_slack else {}
        self.team = "T" + "".join(c for c in str(conn.get("id", "")).split(":")[-1].upper() if c.isalnum())
        self.display = str(conn.get("name") or self.service.title())
        self.performed: list[tuple] = []         # (kind, target, content) of every press that was done
        self._messages: list[dict] = []          # what the driver remembers, newest first
        self._tasks: list[dict] = []
        self._known: set[str] = set()            # refs this driver made
        self._number = FIRST_NUMBER
        self._signin: asyncio.Task | None = None
        self._stopped = False

    # -- life --

    async def start(self) -> None:
        if self.state == "ok" or self._has_secrets():
            self._connect()
        elif self.is_slack:
            self._say("setup", "Waiting for the two Slack tokens.")
        else:
            self._say("signin", f"Waiting for you to allow Bombadil in {self.display}.")
            self._signin = asyncio.ensure_future(self._allow_later(_signin_seconds()))

    async def stop(self) -> None:
        self._stopped = True
        if self._signin is not None:
            self._signin.cancel()
            await asyncio.gather(self._signin, return_exceptions=True)
            self._signin = None

    async def secrets_changed(self) -> None:
        if self.state != "ok" and self._has_secrets():
            self._connect()

    def _has_secrets(self) -> bool:
        return self.is_slack and all(self.secrets.get(name) for name in SLACK_RULES)

    async def _allow_later(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self._signin = None
        self._connect()

    def _say(self, state: str, note: str = "", **more) -> None:
        self.state, self.note = state, note
        if not self._stopped:
            self.emit({"push": "state", "state": state, "note": note, **more})

    def _connect(self) -> None:
        name = {"name": WORKSPACE} if self.is_slack and self.conn.get("name") in (None, "", "Slack") else {}
        self.conn.update(name)
        self._say("ok", "", **name)
        if self.is_slack and not self._messages:
            for message in self._samples():   # what a driver finds out when it first connects
                self.inject("message", message)
        elif not self.is_slack and not self._tasks:
            self._tasks = self._sample_tasks()

    # -- what it says about itself --

    def reads(self) -> str:
        if self.is_slack:
            return "Direct messages, mentions and the threads you are in."
        return f"What is assigned to you or mentions you in {self.display}."

    def can_post(self) -> bool:
        return self.state == "ok"

    def task_fields(self) -> dict:
        if self.is_slack:
            return {}
        return {"create": [{"key": "title", "label": "Title", "edit": "line", "required": True},
                           {"key": "description", "label": "Notes", "edit": "text"},
                           {"key": "due", "label": "Due", "edit": "date"}],
                "comment": [{"key": "text", "label": "Comment", "edit": "text", "required": True}]}

    def steps(self) -> list[dict]:
        if self.state == "setup":
            return [{"id": "create", "say": "Make the Slack app from Bombadil's manifest.",
                     "open": "https://api.slack.test/apps?new_app=1",
                     "point": {"text": "Create", "label": "Yours: Create"}},
                    {"id": "tokens", "say": "Copy the two tokens Slack shows you."}]
        if self.state == "signin":
            return [{"id": "allow", "say": f"Allow Bombadil in {self.display}.",
                     "open": f"https://auth.acme.test/{self.service}/authorize"}]
        return []

    def owns(self, kind: str, target: str) -> bool:
        if self.is_slack:
            return kind == "slack_reply" and (target in self._known or target.startswith(f"slack:{self.team}/"))
        if kind == "task_create":
            return target == self.service
        return kind == "task_comment" and (target in self._known or target.startswith(f"{self.service}:"))

    # -- reading --

    async def messages(self, since: float = 0.0, limit: int = 50) -> list[dict]:
        return [m for m in self._messages if m["ts"] > since][:limit]

    async def thread(self, ref: str, limit: int = 20) -> list[dict]:
        found = next((m for m in self._messages if m["ref"] == ref), None)
        if found is None:
            raise DriverError("That is not a message I know.", "not_found")
        root = found.get("thread") or ref
        around = [m for m in self._messages if m["ref"] == root or m.get("thread") == root]
        return sorted(around, key=lambda m: m["ts"])[-limit:]

    async def tasks(self, limit: int = 30) -> list[dict]:
        return list(self._tasks)[:limit]

    # -- writing --

    async def perform(self, kind: str, target: str, content: Any) -> dict:
        words = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if REFUSE in words:
            raise DriverError(f"{self.display} did not accept that. Nothing was done.", "refused")
        if UNKNOWN in words:
            raise UnknownOutcome()
        now = self.clock()
        if kind == "slack_reply":
            where = next((m["conversation"] for m in self._messages if m["ref"] == target), None)
            if where is not None and where.get("kind") == "dm":
                line = f"Replied to {(where['name'].split() or ['them'])[0]} in a direct message · {_at(now)}"
            else:
                line = f"Posted in {where['name'] if where else 'Slack'} · {_at(now)}"
            parts = target.split("/")
            receipt = {"line": line,
                       "web": {"name": "Slack", "url": f"https://acme.test/archives/{parts[-2] if len(parts) > 2 else ''}"}}
        elif kind == "task_create":
            self._number += 1
            ident = f"{self.service[:3].upper()}-{self._number}"
            self._known.add(f"{self.service}:{ident}")
            receipt = {"line": f"Created {ident} in {self.display}",
                       "web": {"name": self.display, "url": f"https://{self.service}.acme.test/issue/{ident}"}}
        elif kind == "task_comment":
            ident = target.split(":", 1)[-1]
            receipt = {"line": f"Commented on {ident} in {self.display}",
                       "web": {"name": self.display, "url": f"https://{self.service}.acme.test/issue/{ident}"}}
        else:
            raise DriverError("That is not something this connection does.", "refused")
        self.performed.append((kind, target, content))
        return receipt

    # -- steering --

    def inject(self, what: str, raw: dict) -> dict:
        """Say a message (`what` "message") or a task ("item") as if the workspace or the tool had. What is missing
        from a message is made up: a ref, the time, a direct message from the sender."""
        raw = dict(raw)
        if what == "message":
            raw = self._fill(raw)
            self._known.add(raw["ref"])
            self._messages = sorted([m for m in self._messages if m["ref"] != raw["ref"]] + [raw],
                                    key=lambda m: -m["ts"])
            self.emit({"push": "message", "message": raw})
        else:
            self._tasks = [t for t in self._tasks if t.get("ref") != raw.get("ref")] + [raw]
            if isinstance(raw.get("ref"), str):
                self._known.add(raw["ref"])
            self.emit({"push": "task", "item": raw})
        return raw

    def _fill(self, raw: dict) -> dict:
        ts = raw.get("ts") if isinstance(raw.get("ts"), (int, float)) else self.clock()
        sender = raw.get("from") if isinstance(raw.get("from"), dict) else {"id": "U0PRIYA", "name": "Priya Shah"}
        conv = raw.get("conversation") if isinstance(raw.get("conversation"), dict) else {
            "id": "D0" + str(sender.get("id", "X")).lstrip("U0")[:8].upper(), "name": sender.get("name", ""),
            "kind": "dm"}
        ref = raw.get("ref") or protocol.slack_ref(self.team, conv.get("id") or "D0X", f"{ts:.6f}")
        return {"source": "slack", "connection": self.conn.get("id", ""), "mentions_me": False,
                "reason": "direct message" if conv.get("kind") == "dm" else "mentioned you", "web_url": None,
                **raw, "ref": ref, "ts": ts, "from": sender, "conversation": conv}

    def _samples(self) -> list[dict]:
        """A direct message from Priya Shah, a mention in #launch from Marcus Webb, and a reply to it."""
        now = self.clock()
        team = self.team
        launch = {"id": "C0LAUNCH", "name": "#launch", "kind": "channel"}
        priya, marcus = {"id": "U0PRIYA", "name": "Priya Shah"}, {"id": "U0MARCUS", "name": "Marcus Webb"}
        mention = protocol.slack_ref(team, "C0LAUNCH", f"{now - 780:.6f}")
        return [
            {"ref": protocol.slack_ref(team, "D0PRIYA", f"{now - 1500:.6f}"), "source": "slack",
             "conversation": {"id": "D0PRIYA", "name": "Priya Shah", "kind": "dm"}, "thread": None, "from": priya,
             "text": "Legal just signed off. Can we lock the 14th for the launch?", "ts": now - 1500,
             "mentions_me": False, "reason": "direct message",
             "web_url": "https://acme.test/archives/D0PRIYA"},
            {"ref": mention, "source": "slack", "conversation": launch, "thread": None, "from": marcus,
             "text": "Can you look at the pricing page copy before noon? The new tiers are in the draft.",
             "ts": now - 780, "mentions_me": True, "reason": "mentioned you",
             "web_url": "https://acme.test/archives/C0LAUNCH"},
            {"ref": protocol.slack_ref(team, "C0LAUNCH", f"{now - 300:.6f}"), "source": "slack",
             "conversation": launch, "thread": mention, "from": priya,
             "text": "I read it yesterday. Only the last paragraph needs a change.", "ts": now - 300,
             "mentions_me": False, "reason": "a thread you are in",
             "web_url": "https://acme.test/archives/C0LAUNCH"},
        ]

    def _sample_tasks(self) -> list[dict]:
        prefix = self.service[:3].upper()
        base = f"https://{self.service}.acme.test/issue"
        return [
            {"ref": f"{self.service}:{prefix}-41", "service": self.service, "title": "Review the launch checklist",
             "why": "assigned to you", "status": "In progress", "due": "2026-10-03",
             "url": f"{base}/{prefix}-41", "fields": [{"key": "team", "label": "Team", "value": "Launch"}]},
            {"ref": f"{self.service}:{prefix}-40", "service": self.service, "title": "Approve the pricing page copy",
             "why": "mentioned you", "status": "Todo", "due": None, "url": f"{base}/{prefix}-40",
             "fields": [{"key": "team", "label": "Team", "value": "Growth"}]},
        ]

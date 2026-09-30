"""Bombadil's voice, the daemon side: the name and voice card, the welcome line and the goodbye.

greet.py and persona.py hold the words and the rules; this holds the conversation with the bar. The bar
speaks first, because agentd cannot tell it from `bombadil ask`, and every message is optional: a client
that never sends them is never greeted, and agentd ignores types it does not know.

  bar -> agentd   {"type": "bar", "idle": false}     once per connection, on its first status
                  {"type": "presence", "idle": bool}  the bar's IdleMonitor: the user went away or came back
                  {"type": "persona", "name": "Dan", "voice": "plain"}   the card was answered
                  {"type": "persona_skip"}            Esc, or a prompt sent while the card was up
                  {"type": "welcomed", "id": n}       the bar showed welcome n (nothing for a dropped one)
  agentd -> bar   {"type": "persona_ask", "line", "name", "voice", "current", "voices": [{id, name, card}]}
                  {"type": "persona", "name", "voice", "greet"}          after a save or a skip: fold the card
                  {"type": "welcome", "id": n, "text": "...", "first": bool}   to one bar

A greeting that cannot be said is dropped, not queued, and nothing here waits for a turn or stops one.
What was shown is recorded only when the bar says it showed it (`welcomed`): the marker in the runtime
dir, the boot row in turns.jsonl and the ledger. So a line the bar dropped is tried again at the next
hello, and restarting agentd (which the marker survives) never replays one.

The card: persona.toml gets its defaults the moment the card is asked, so any way out of it leaves a valid
setting and it is never asked again. Sign-in (PR #5) calls `AgentD.ask_persona(line)` when it turns ready,
and `Voice.on_ready()` on any other change to ready, so a greeting held back by sign-in is said then.
"""

import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from . import greet, greet_sources, paths, persona

TYPES = frozenset({"bar", "presence", "persona", "persona_skip", "welcomed"})

WINDOW = 15 * 60       # a boot greeting is this login's only within this long after agentd started
DELAY = 1.0            # the boot line comes this long after the bar said hello, beside its reader
ACK_WAIT = 10.0        # a welcome the bar never answered counts as dropped after this long
IDLE_SECONDS = 300.0   # the bar's IdleMonitor timeout: it reports idle this long after the last input
DRAIN = 1.0            # how long a shutdown waits for what the clients were sent
BOOT_MOMENTS = ("boot", "installed", "first")
KEEP_SENT = 16

ASK_LINE = "What should I call you?"
CARD_LINE = "What should I call you, and how should I sound?"
OPEN_TEXT = "Opening the voice card"
DONE_TEXT = "Pick a voice, or press Esc."
BAD_NAME = "Names are one to three words of letters, 24 characters at most."
BAD_VOICE = "The voices are merry, plain and quiet."
# A coding session a shutdown will stop. The words belong in lines.toml [fact] with the others.
STOPS = "{title} stops at {done} of {total}"
STOPS_BARE = "{title} stops"


def _seconds(var: str, default: float) -> float:
    try:
        value = float(os.environ.get(var, ""))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _safe(read, *args, **kw) -> list:
    """A source that breaks is a source with nothing to say."""
    try:
        return list(read(*args, **kw))
    except Exception as e:  # noqa: BLE001 - a greeting never fails because one reader did
        name = getattr(read, "__name__", read)
        print(f"agentd: voice source {name}: {type(e).__name__}: {e}", file=sys.stderr)
        return []


@dataclass
class _Bar:
    idle: bool = False
    idle_at: float = 0.0    # when it reported idle, while it is


@dataclass
class _Sent:
    writer: object
    greeting: greet.Greeting
    at: float


class Voice:
    def __init__(self, agentd, clock=time.time, now=None):
        self.agentd = agentd
        self.clock = clock   # epoch seconds: uptime, time away, the ack wait
        self._now = now or (lambda: datetime.fromtimestamp(clock()).astimezone())   # the wall clock for words
        self.started = clock()
        self.bars: dict = {}                    # writer -> _Bar, for the connections that said "bar"
        self.asking: dict | None = None         # the card while it is up: {line, current, first}
        self._sent: dict[int, _Sent] = {}       # welcomes the bar has not answered yet
        self._seq = 0
        self._lock = asyncio.Lock()             # one greeting is decided at a time

    # -- messages from the bar --

    async def handle(self, t: str, msg: dict, writer) -> None:
        try:
            if t == "bar":
                await self._bar(msg, writer)
            elif t == "presence":
                await self._presence(msg, writer)
            elif t == "persona":
                await self._answer(msg, writer)
            elif t == "persona_skip":
                await self._skip(writer)
            elif t == "welcomed":
                self._welcomed(msg, writer)
        except Exception as e:  # noqa: BLE001 - a greeting never costs a client its connection
            print(f"agentd: voice {t}: {type(e).__name__}: {e}", file=sys.stderr)

    def on_disconnect(self, writer) -> None:
        self.bars.pop(writer, None)
        for wid in [wid for wid, s in self._sent.items() if s.writer is writer]:
            del self._sent[wid]
        if self.asking is not None and self.asking["current"] and not self.bars:
            self.asking = None   # a card asked for with the word `voice` is gone with its bar

    async def _bar(self, msg: dict, writer) -> None:
        idle = msg.get("idle") is True
        self.bars[writer] = _Bar(idle, self.clock() if idle else 0.0)
        if self.asking is not None:
            await self._ask_one(writer)   # a bar that (re)connects while the card is pending gets it again
        elif not await self.ask() and not idle:
            # A bar that starts while the user is away waits until they are back. The second's wait runs
            # beside the reader, so a key pressed in it is handled at once.
            self.agentd._background(self._boot_after(writer))

    async def _presence(self, msg: dict, writer) -> None:
        bar, idle = self.bars.get(writer), msg.get("idle")
        if bar is None or not isinstance(idle, bool):
            return   # not a bar, or not a message we know
        now = self.clock()
        if idle:
            if not bar.idle:
                bar.idle, bar.idle_at = True, now
            return
        if not bar.idle:
            return
        bar.idle = False
        # The bar says idle only after the timeout, so the user left that long before it said so.
        away = now - bar.idle_at + _seconds("BOMBADIL_IDLE_SECONDS", IDLE_SECONDS)
        if self._boot_open():
            await self._boot(writer)   # the boot line that did not land, now that someone is here
        else:
            await self._back(writer, away)

    def _welcomed(self, msg: dict, writer) -> None:
        wid = msg.get("id")
        sent = self._sent.get(wid) if isinstance(wid, int) and not isinstance(wid, bool) else None
        if sent is None or sent.writer is not writer:
            return   # unknown, already answered, or someone else's welcome
        del self._sent[wid]
        moment = sent.greeting.moment
        self._commit(sent.greeting)
        # One login has one boot line and one return is said once, whichever bar showed it.
        group = BOOT_MOMENTS if moment in BOOT_MOMENTS else (moment,)
        for other in [k for k, s in self._sent.items() if s.greeting.moment in group]:
            del self._sent[other]

    # -- the card --

    def _due(self) -> bool:
        a = self.agentd
        return (not persona.exists() and bool(a.configured) and a._setup_ready()
                and bool(a.provider.installed))

    async def ask(self, line: str | None = None, current: bool = False) -> bool:
        """Put the card up on every bar. Without `current` it asks only while persona.toml is missing
        (the first sign-in) and writes the defaults at once; with it, it reopens the card with the saved
        name and voice filled in. False when nothing was asked."""
        if self.asking is not None:
            if line:
                self.asking["line"] = line
            await self._ask_all()
            return True
        if current:
            if not self.bars:
                return False
            self.asking = {"line": line or CARD_LINE, "current": True, "first": False}
        else:
            if not self._due():
                return False
            self._begin()
            self.asking = {"line": line or ASK_LINE, "current": False, "first": True}
        await self._ask_all()
        return True

    def _begin(self) -> None:
        """The card is being asked for the first time: defaults, the day it started, and this login
        counts as greeted (the first hello takes the place of the boot line)."""
        try:
            persona.ensure_defaults()
        except OSError as e:
            print(f"agentd: voice: could not write the defaults: {e}", file=sys.stderr)
        ledger = greet.load_ledger()
        if not ledger.setup_day:
            ledger.setup_day = self._now().date().isoformat()
            greet.save_ledger(ledger)
        self._mark()

    def _ask_msg(self) -> dict:
        current = self.asking["current"]
        p = persona.load() if current else persona.Persona()
        return {"type": "persona_ask", "line": self.asking["line"], "name": p.name, "voice": p.voice,
                "current": current, "voices": persona.templates()}

    async def _ask_one(self, writer) -> None:
        await self.agentd._send(writer, self._ask_msg())

    async def _ask_all(self) -> None:
        for writer in list(self.bars):
            await self._ask_one(writer)

    async def _answer(self, msg: dict, writer) -> None:
        before = persona.load()
        name, voice = msg.get("name") or "", msg.get("voice") or before.voice
        name = name.strip() if isinstance(name, str) else None
        if name is None or (name and not persona.valid_name(name)):
            await self._reject(writer, BAD_NAME)
            return
        if voice not in persona.VOICES:
            await self._reject(writer, BAD_VOICE)
            return
        try:
            saved = persona.save(name, voice, before.greet)
        except ValueError:
            await self._reject(writer, BAD_NAME)
            return
        except OSError as e:
            await self._reject(writer, f"Could not save that: {e.strerror or type(e).__name__}")
            return
        asking, self.asking = self.asking, None
        await self._fold(writer)
        self._note(before, saved)
        if asking is not None and asking["first"]:
            await self._first(writer)

    async def _reject(self, writer, text: str) -> None:
        """A bad answer changes nothing: the sender hears why, and the card stays up for it."""
        await self.agentd._send(writer, {"type": "event", "kind": "error", "turn": None, "text": text})
        if self.asking is not None and writer in self.bars:
            await self._ask_one(writer)

    async def _skip(self, writer) -> None:
        asking, self.asking = self.asking, None
        if asking is None:
            return
        await self._fold(writer)
        if asking["first"]:
            await self._first(writer)

    async def _fold(self, writer) -> None:
        p = persona.load()
        msg = {"type": "persona", "name": p.name, "voice": p.voice, "greet": p.greet}
        for w in {*self.bars, writer}:
            await self.agentd._send(w, msg)

    def _note(self, before: persona.Persona, saved: persona.Persona) -> None:
        """Tell a session that is already running: it read the voice when it started."""
        if (saved.name, saved.voice) == (before.name, before.voice):
            return
        wants = f"wants the {saved.voice} voice"
        if saved.name:
            text = f"The user now goes by {saved.name} and {wants}."
        elif before.name:
            text = f"The user no longer goes by a name and {wants}."
        else:
            text = f"The user now {wants}."
        self.agentd.notes = [*self.agentd.notes, text][-10:]

    async def word(self, typed: str) -> None:
        """The launcher word `voice`: reopen the card with what is saved."""
        a = self.agentd
        await a.event("local", turn=None, action="voice", target="", phase="start", text=OPEN_TEXT)
        await self.ask(current=True)
        await a.event("local", turn=None, action="voice", target="", phase="done", ok=True, text=DONE_TEXT)
        a._log_line({"t": self.clock(), "kind": "local", "prompt": typed, "action": "voice", "target": "",
                     "result": DONE_TEXT, "ok": True})

    # -- welcomes --

    def _busy(self) -> bool:
        return self.agentd.current is not None or bool(self.agentd.pending)

    def _can_greet(self, writer) -> bool:
        """Something true to say to this bar now: it said hello, nothing is running, sign-in is done, the card
        is not up, and there is a persona to say it in."""
        a = self.agentd
        return (writer in self.bars and writer in a.clients and self.asking is None and not self._busy()
                and a._setup_ready() and persona.exists())

    def _pending(self, moments) -> bool:
        now = self.clock()
        return any(s.greeting.moment in moments and now - s.at < ACK_WAIT for s in self._sent.values())

    def _boot_open(self) -> bool:
        if self.clock() - self.started >= _seconds("BOMBADIL_GREET_WINDOW", WINDOW):
            return False
        if self._pending(BOOT_MOMENTS):
            return False
        try:
            return not paths.greeted_marker().exists()
        except OSError:
            return False   # cannot tell: better silent than twice

    async def _welcome(self, writer, g: greet.Greeting) -> None:
        self._seq += 1
        self._sent[self._seq] = _Sent(writer, g, self.clock())
        while len(self._sent) > KEEP_SENT:
            del self._sent[next(iter(self._sent))]
        await self.agentd._send(writer, {"type": "welcome", "id": self._seq, "text": g.text,
                                         "first": g.first})

    async def on_ready(self, line: str | None = None) -> None:
        """Sign-in turned ready: ask the card if it is due, else say the boot line a bar was waiting for."""
        if await self.ask(line):
            return
        for writer in list(self.bars):
            await self._boot(writer)

    async def _boot_after(self, writer) -> None:
        await asyncio.sleep(_seconds("BOMBADIL_GREET_DELAY", DELAY))
        await self._boot(writer)

    async def _boot(self, writer) -> None:
        async with self._lock:
            if not self._boot_open() or not self._can_greet(writer):
                return
            installed = not greet.is_live() and "installed" not in greet.load_ledger().said
            rows = [] if installed else await asyncio.to_thread(greet_sources.read_rows)
            if not self._boot_open() or not self._can_greet(writer):
                return   # something happened while the log was read
            tz, dt = greet.tz_set(), self._now()
            ledger = greet.load_ledger()
            moment = "installed" if installed else "boot"
            facts = [] if installed else self._boot_facts(rows, dt)
            g = greet.build(moment, persona.load(), dt, facts=facts, ledger=ledger, tz_set=tz,
                            day=greet.days_since(ledger, dt))
            if g is None or not g.text:
                self._commit(greet.Greeting("", moment=moment))   # nothing to say is still a boot
                return
            await self._welcome(writer, g)

    def _boot_facts(self, rows: list, dt: datetime) -> list:
        """What is true at boot. What finished in the last session you watched happen, so only the restart
        that cut a coding session off, what you made since last time and the updates."""
        since = greet_sources.boots(rows)
        facts = _safe(greet_sources.updates)
        facts += [f for f in _safe(greet_sources.dev, None, dt, since) if f.kind == "stopped"]
        return facts + _safe(greet_sources.made, rows, since)

    async def _back(self, writer, away: float) -> None:
        """The user came back after `away` seconds: one line of what happened meanwhile, or nothing."""
        if away < greet.SHORT_AWAY:
            return
        moments = ("return", *BOOT_MOMENTS)
        async with self._lock:
            if not self._can_greet(writer) or self._pending(moments):
                return
            rows = await asyncio.to_thread(greet_sources.read_rows)
            if not self._can_greet(writer) or self._pending(moments):
                return
            tz, now, dt = greet.tz_set(), self.clock(), self._now()
            ledger = greet.load_ledger()
            if now - ledger.last_return_at < greet.SHORT_AWAY:
                return   # at most one return line in half an hour
            since = now - away
            facts = _safe(greet_sources.turns, rows, since, now=now)
            facts += [f for f in _safe(greet_sources.dev, None, dt, since)
                      if f.kind in ("finished", "failed")]
            if away >= greet.WEEK_AWAY:
                facts += _safe(greet_sources.updates)
            g = greet.build("return", persona.load(), dt, facts=facts, ledger=ledger, tz_set=tz, away=away,
                            day=greet.days_since(ledger, dt))
            if g is not None and g.text:
                await self._welcome(writer, g)

    async def _first(self, writer) -> None:
        """The hello right after the card, in the voice that was just chosen."""
        async with self._lock:
            if not self._can_greet(writer):
                return
            tz, dt = greet.tz_set(), self._now()
            ledger = greet.load_ledger()
            g = greet.build("first", persona.load(), dt, ledger=ledger, tz_set=tz,
                            day=greet.days_since(ledger, dt))
            if g is None or not g.text:
                self._commit(greet.Greeting("", moment="first"))
                return
            await self._welcome(writer, g)

    # -- what was said --

    def _commit(self, g: greet.Greeting) -> None:
        dt = self._now()
        ledger = greet.load_ledger()
        greet.commit(ledger, g, dt)
        if g.moment in BOOT_MOMENTS:
            today = dt.date().isoformat()
            ledger.last_boot_day = today   # greet.commit leaves it alone for "installed"
            ledger.setup_day = ledger.setup_day or today
            if g.moment == "first" and not greet.is_live():
                # The first hello on an installed system is its welcome: "Welcome home" would be a day late.
                ledger.said.setdefault("installed", {"value": "", "day": today})
        greet.save_ledger(ledger)
        if g.moment in BOOT_MOMENTS:
            self._mark()
            try:
                self.agentd._log_line({"t": self.clock(), "kind": "local", "action": "boot", "prompt": "",
                                       "greeted": bool(g.text)})
            except OSError as e:
                print(f"agentd: voice: could not write the boot row: {e}", file=sys.stderr)

    def _mark(self) -> None:
        path = paths.greeted_marker()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{self.clock():.0f}\n")
        except OSError as e:
            print(f"agentd: voice: could not write the greeted marker: {e}", file=sys.stderr)

    # -- shutdown --

    async def goodbye(self) -> str:
        """The start text of a shutdown, in the saved voice. "" keeps the launcher's own words, as before
        anyone chose a voice."""
        try:
            if not persona.exists():
                return ""
            tz, dt = greet.tz_set(), self._now()
            return greet.goodbye(persona.load(), dt, stopping=self._stopping(), tz_set=tz)
        except Exception as e:  # noqa: BLE001 - a goodbye is never worth a failed shutdown
            print(f"agentd: voice goodbye: {type(e).__name__}: {e}", file=sys.stderr)
            return ""

    def _stopping(self) -> list[str]:
        """Clauses for the coding sessions a shutdown will stop, from the dev registry when there is one."""
        path = (paths.dev_dir() if hasattr(paths, "dev_dir") else paths.state_dir() / "dev") / "sessions.json"
        try:
            sessions = json.loads(path.read_text(encoding="utf-8")).get("sessions")
        except (OSError, ValueError, AttributeError):
            return []
        out = []
        for s in sessions if isinstance(sessions, list) else []:
            if not isinstance(s, dict) or s.get("state") != "working" or s.get("alive") is False:
                continue
            title = " ".join(greet_sources._title(s).split())[:40]
            progress = s.get("progress") if isinstance(s.get("progress"), dict) else {}
            done, total = greet_sources._num(progress.get("done")), greet_sources._num(progress.get("total"))
            if title and done is not None and total is not None and done >= 0 and total > 0:
                out.append(greet.say(STOPS, title=title, done=int(done), total=int(total)))
            elif title:
                out.append(greet.say(STOPS_BARE, title=title))
        return out[:4]

    async def flush(self, timeout: float = DRAIN) -> None:
        """Wait, for at most `timeout`, until what the clients were sent has gone out, so the line that
        says goodbye is written before the power goes. A client that does not read is not waited for."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end and not self._flushed():
            await asyncio.sleep(0.01)

    def _flushed(self) -> bool:
        for writer, queue in list(self.agentd.clients.items()):
            transport = getattr(writer, "transport", None)
            if not queue.empty() or (transport is not None and transport.get_write_buffer_size()):
                return False
        return True

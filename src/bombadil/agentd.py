"""agentd: the session daemon. The agent *is* the session; this process keeps it alive.

Runs as the user, started by Hyprland at login. Listens on a Unix socket for newline
delimited JSON. Any number of clients (the Quickshell bar, `bombadil ask`, a generated
app) can connect; every event is broadcast to all of them.

Client -> daemon:  {"type": "prompt", "text": "..."}   a turn, or a launcher word ("browser", "undo");
                                                        "asked_by": "builder" says a coding session asks
                   {"type": "stop"}                     end the running turn and all it started
                   {"type": "cancel"}                   the same as stop
                   {"type": "unqueue", "turn": n}       drop a prompt still waiting its turn
                   {"type": "local", "action": "undo"}  a launcher action by name (the Undo button)
                   {"type": "details", "turn": n}       show a turn's commands and output (drawer);
                                                        again while it shows closes it
                   {"type": "close_details"}            put the drawer away (Esc in the pill)
                   {"type": "open", "kind": "path"|"unit"|"package"|"url"|"turn", "value": "..."}
                                                        open what a box in a picture names; answered
                                                        by a "local" event with action "open"
                   {"type": "summon"}                   ask the bar to take the keyboard (Super)
                   {"type": "card", "card": {...}}      a picture to draw (os-mcp's show_card and system_map);
                                                        answered with {"type": "card_ack", "shown": bool}
                   {"type": "status"}
Daemon -> clients: {"type": "event", "kind": "turn_start"|"snapshot"|"status"|"text"|"tool"|
                    "tool_result"|"file_change"|"result"|"error"|"turn_end"|"queued"|"unqueued"|
                    "local"|"card"|"plan", "turn": n, ...}
                   {"type": "status", "busy": bool, "provider": "...", "turns": n, "queue": [...], ...}
                   {"type": "entries", "entries": [...]}  names the pill can complete and open
                   {"type": "summon"}

"status" events are the live line above the pill: {"text": "Installing ffmpeg", "risk": null |
"system" | "irreversible", "command": "sudo pacman -S ffmpeg" | null, "source": "step" | "agent"}.
A step's status may also carry "because": why it happens, in the agent's own words from just before
it acted (at most 140 characters), and "after": {"label", "kind", "text"}, on a system or irreversible
step that follows something read from outside ("after reading wireguard.com/quickstart"). The tool
event of the step carries the same two fields for Details.
A "step" status also says what the turn has changed so far: "touched": {"package": 1, "file": 2}
(kinds package, service, file, app; only those there are) and "touched_text": "1 package and 2
files so far" ("" when nothing).
"plan" events are the agent's own step list, the whole table on every change:
{"steps": [{"id": "1", "subject": "Install ffmpeg", "active": "Installing ffmpeg" | null, "status":
"pending" | "in_progress" | "completed"}]}. A step the agent has only just made has "id": null and
comes last. When several are in progress the last one is the current step.
turn_start carries "asked_by": "builder" when the turn was started for a coding session, else null.
turn_end carries how the turn ended: {"seconds", "summary": "Installed ffmpeg.", "changed",
"irreversible", "stopped", "line": "Stopped while installing ffmpeg.", "read": [{"label", "kind",
"outside"}]}; the same list goes into turns.jsonl.

"card" events carry a picture for the bar: {"card": {"type": "diagram", "id", "shape", "title", "nodes",
"links", "highlight", "say", "text", ...}}. One drawn from a request the user typed ("how am I connected")
has turn null; one os-mcp sent while a turn runs carries that turn; a receipt (what the turn changed
in the network, a service, the disks, the sound or the screens, as a before and after, "receipt": true)
follows turn_end. While Claude still writes a show_card call, cards with "partial": true (the frame and
the boxes finished so far) arrive under the id the finished card then keeps; {"id", "gone": true} takes
one back. Partial cards are not logged.

Every turn: say turn_start, snapshot the system (undo point), run one provider CLI turn with
the os-mcp server attached in its own scope, stream its events, log the turn. Launcher words
(apps, panels, undo, stop) are handled here at once and never wait for the model.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import cards, config, launcher, narrate, paths, procs, providers, snapshots, sysmap, watch

# Provider events that only feed the live line; clients get the "status" events made from them.
LINE_ONLY = {"tool_start", "tool_input", "text_delta", "thinking", "message_start"}
MAX_OUTPUT = 16_000   # characters of one command's output kept in events and the turn's log
# A client that stops reading (a hung bar) is dropped rather than allowed to hold up the
# others: its messages wait in a queue of this many, each write gets this long.
CLIENT_BACKLOG = 10_000
SEND_TIMEOUT = 5.0
# After a turn's process exits, how long its output may take to drain. Longer means a job it
# left in the background (`!server &`) still holds the pipe; the turn ends without it.
OUTPUT_GRACE = 1.0


class AgentD:
    def __init__(self, provider: providers.Provider, snaps: snapshots.Snapshots | None = None,
                 socket_path: Path | None = None, launch: launcher.Launcher | None = None,
                 stopper: procs.Stopper | None = None, explain: str = "normal"):
        self.provider = provider
        self.explain = explain                      # brief | normal | teach: at brief no receipts
        self.snaps = snaps or snapshots.Snapshots()
        self.socket_path = socket_path or paths.socket_path()
        self.launcher = launch or launcher.Launcher(snaps=self.snaps)
        self.stopper = stopper or procs.Stopper()
        self.clients: dict[asyncio.StreamWriter, asyncio.Queue] = {}
        self.session_id: str | None = None
        self.turns = 0
        self.proc: asyncio.subprocess.Process | None = None
        self.pending: list[tuple[int, str]] = []   # prompts waiting for the running turn
        self._wake = asyncio.Event()
        self.next_id = 0
        self.current: int | None = None
        self.workdir = paths.state_dir()
        self.stopping = False
        self.narrator: narrate.Narrator | None = None
        self.plan_msg: dict | None = None           # the running turn's latest plan event
        self.asked_by: dict[int, str] = {}          # queued turn -> who asked, when not the person typing
        self.notes: list[str] = []                 # what happened without the model since its last turn
        self.turn_logs: dict[int, Path] = {}
        self._entries_key = None
        self._stopped_line = ""
        self._unit: str | None = None               # the running turn's systemd scope
        self._hold = 0                              # undo/restart/shutdown running: start no turn
        self._exclusive = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()      # local actions and stops running beside the reader
        self._card_seq = 0
        self._card_stream: cards.CardStream | None = None   # show_card calls being written (Claude)
        self._stream_ids: list[str] = []            # their card ids, until the finished card takes one
        self._closed_turn: int | None = None        # the turn whose closing line is on screen
        self._befores: dict[str, asyncio.Task] = {}   # a service's facts from when a step first touched it

    # -- socket --

    async def serve(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        # Probe for systemd scopes now, not on the first Enter.
        await asyncio.to_thread(procs.scope_supported)
        server = await asyncio.start_unix_server(self._client, path=str(self.socket_path))
        worker = asyncio.create_task(self._worker())
        watcher = asyncio.create_task(self._watch_apps())
        async with server:
            await server.serve_forever()
        worker.cancel()
        watcher.cancel()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        queue: asyncio.Queue = asyncio.Queue(CLIENT_BACKLOG)
        self.clients[writer] = queue
        sender = asyncio.create_task(self._sender(writer, queue))
        try:
            await self._send(writer, self._status())
            await self._send(writer, await self._entries_msg())
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                try:
                    await self.handle(msg, writer)
                except Exception as e:  # noqa: BLE001 - one bad message never costs the connection
                    print(f"agentd: {msg.get('type')!r}: {type(e).__name__}: {e}", file=sys.stderr)
                    await self._send(writer, {"type": "event", "kind": "error", "turn": None,
                                              "text": f"agentd could not handle that: {e}"})
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError, ValueError, OSError):
            pass
        finally:
            # The client hung up or half-closed: what is already queued for it still goes out.
            if self.clients.pop(writer, None) is not None:
                try:
                    queue.put_nowait(None)
                    await asyncio.wait_for(asyncio.shield(sender), 2)
                except (asyncio.QueueFull, asyncio.TimeoutError):
                    pass
            sender.cancel()
            writer.close()

    async def handle(self, msg: dict, writer: asyncio.StreamWriter):
        t = msg.get("type")
        if t == "prompt":
            text = str(msg.get("text", "")).strip()
            if not text:
                await self._send(writer, {"type": "event", "kind": "error", "text": "empty prompt"})
                return
            action = await self._match(text)
            if action is not None:
                await self._send(writer, {"type": "local", "action": action.kind})
                self._background(self.local(action, text))
                return
            self.next_id += 1
            if msg.get("asked_by") == "builder":
                self.asked_by[self.next_id] = "builder"
            # The id lets a client (bombadil ask) follow its own turn among everyone's events.
            await self._send(writer, {"type": "queued", "turn": self.next_id})
            if self.current is not None or self.pending:
                await self.broadcast({"type": "event", "kind": "queued", "turn": self.next_id, "prompt": text})
            self.pending.append((self.next_id, text))
            self._wake.set()
            await self.broadcast(self._status())
        elif t in ("stop", "cancel"):
            await self.stop()
        elif t == "unqueue":
            before = len(self.pending)
            self.pending = [(i, p) for i, p in self.pending if i != msg.get("turn")]
            if len(self.pending) != before:
                await self.broadcast({"type": "event", "kind": "unqueued", "turn": msg.get("turn")})
                await self.broadcast(self._status())
        elif t == "local":
            action = _action(msg)
            if action is not None:
                self._background(self.local(action, str(msg.get("action"))))
        elif t == "details":
            self._background(self.details(msg.get("turn")))
        elif t == "close_details":
            self._background(asyncio.to_thread(self.launcher.close_details))
        elif t == "open":
            self._background(self.open_thing(msg.get("kind"), msg.get("value")))
        elif t == "summon":
            await self.broadcast({"type": "summon"})
        elif t == "card":
            await self._card_message(msg, writer)
        elif t == "status":
            await self._send(writer, self._status())

    def _status(self) -> dict:
        # Busy from the moment a prompt is accepted, so Esc stops it even before its turn starts.
        return {"type": "status", "busy": self.current is not None or bool(self.pending),
                "provider": self.provider.name, "turns": self.turns,
                "snapshots": self.snaps.available, "queued": len(self.pending),
                "turn": self.current, "queue": [{"turn": i, "prompt": p} for i, p in self.pending]}

    async def _entries_msg(self) -> dict:
        try:
            entries = await asyncio.to_thread(launcher.entries)
        except Exception as e:  # noqa: BLE001 - a broken app folder must not cost the bar its words
            print(f"agentd: launcher entries: {type(e).__name__}: {e}", file=sys.stderr)
            entries = launcher.entries([])
        return {"type": "entries", "entries": entries}

    async def _match(self, text: str) -> launcher.Action | None:
        try:
            # "why" is a launcher word only while a turn runs: then it asks about the step in front of you.
            return await asyncio.to_thread(launcher.match, text, None, self.current is not None)
        except Exception as e:  # noqa: BLE001 - when in doubt the agent gets the text
            print(f"agentd: launcher match: {type(e).__name__}: {e}", file=sys.stderr)
            return None

    async def _watch_apps(self):
        """Tell the bar when an app appears or goes, so it can complete the new name."""
        while True:
            await asyncio.sleep(3)
            try:
                key = await asyncio.to_thread(_apps_key)
                if key != self._entries_key:
                    first = self._entries_key is None
                    self._entries_key = key
                    if not first:
                        await self.broadcast(await self._entries_msg())
            except Exception as e:  # noqa: BLE001 - keep watching whatever one look found
                print(f"agentd: watching apps: {type(e).__name__}: {e}", file=sys.stderr)

    async def _sender(self, writer: asyncio.StreamWriter, queue: asyncio.Queue):
        """Write one client's messages in order. Nothing else waits on this client."""
        try:
            while (msg := await queue.get()) is not None:
                writer.write(msg)
                await asyncio.wait_for(writer.drain(), SEND_TIMEOUT)
        except (ConnectionResetError, BrokenPipeError, asyncio.TimeoutError, OSError):
            self._drop(writer)

    def _drop(self, writer: asyncio.StreamWriter):
        if self.clients.pop(writer, None) is not None:
            writer.close()   # its reader then sees the end, and _client cleans up

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):
        """Queue a message for one client; never waits for it to be read."""
        queue = self.clients.get(writer)
        if queue is None:
            return
        try:
            queue.put_nowait((json.dumps(msg) + "\n").encode())
        except asyncio.QueueFull:
            print("agentd: dropping a client that stopped reading", file=sys.stderr)
            self._drop(writer)

    async def broadcast(self, msg: dict):
        for w in list(self.clients):
            await self._send(w, msg)

    def _background(self, coro) -> asyncio.Task:
        """Run a slow action beside the client's reader, so a stop or an unqueue sent right
        after it is read at once."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                e = t.exception()
                print(f"agentd: {type(e).__name__}: {e}", file=sys.stderr)
        task.add_done_callback(done)
        return task

    # -- things that never wait for the model --

    async def local(self, action: launcher.Action, typed: str):
        if action.kind == "why":
            # The reason the agent gave before this step, from what was recorded: no model, no turn.
            text = self.narrator.why_text() if self.narrator else "Nothing is running."
            await self.event("local", turn=None, action="why", phase="done", ok=True, text=text)
            return
        if action.kind == "picture":
            await self.picture(action, typed)
            return
        if action.kind == "stop":
            stopping = await self.stop()
            await self.event("local", turn=None, action="stop", phase="done", ok=True,
                             text="Stopping." if stopping else "Nothing is running.")
            return
        if action.kind in ("undo", "restart", "shutdown"):
            # Undo takes back the turn that is running too, so end it first, and start no
            # queued turn until the undo is done: it would take that turn's snapshot instead.
            self._hold += 1
            try:
                async with self._exclusive:
                    if self.current is not None:
                        await self.stop()
                        while self.current is not None:
                            await asyncio.sleep(0.05)
                    await self._local(action, typed)
            finally:
                self._hold -= 1
                self._wake.set()
            return
        await self._local(action, typed)

    async def _local(self, action: launcher.Action, typed: str):
        doing = self.launcher.doing(action)
        await self.event("local", turn=None, action=action.kind, target=action.target, phase="start", text=doing)
        ok, text = await asyncio.to_thread(self.launcher.run, action)
        await self.event("local", turn=None, action=action.kind, target=action.target, phase="done", ok=ok,
                         text=text)
        if ok:
            self.notes.append(f"{typed!r}: {text}")
            self.notes = self.notes[-10:]
        self._log_line({"t": time.time(), "kind": "local", "prompt": typed, "action": action.kind,
                        "target": action.target, "result": text, "ok": ok})

    # -- pictures --

    async def _show(self, card: dict, turn: int | None, card_id: str | None = None):
        """Send a finished card to the bars. One drawn while it streamed keeps that card's id, so
        the bar swaps the picture in place."""
        self._card_seq += 1
        await self.event("card", turn=turn, card={**card, "id": card_id or f"card-{self._card_seq}"})

    async def _card_message(self, msg: dict, writer: asyncio.StreamWriter):
        card, errors = cards.accept(msg.get("card"))
        if card is None:
            await self._send(writer, {"type": "card_ack", "shown": False, "errors": errors})
            return
        streamed = self._stream_ids.pop(0) if self._stream_ids and self.current is not None else None
        await self._show(card, self.current, streamed)
        # Shown when a client besides the sender is there to draw it.
        await self._send(writer, {"type": "card_ack", "shown": len(self.clients) > 1})

    async def picture(self, action: launcher.Action, typed: str):
        """A picture word ("how am I connected") is answered here: captured from the machine, drawn
        by the bar, no model and no turn."""
        kind, _, unit = action.target.partition(":")
        await self.event("local", turn=None, action="picture", target=action.target, phase="start",
                         text=self.launcher.doing(action))
        try:
            card = (await asyncio.to_thread(sysmap.capture, kind, unit, provider=self.provider.name))["card"]
            text, ok = card.get("say") or card["title"], True
        except sysmap.Unavailable as e:
            card, text, ok = None, str(e), False
        if card is not None:
            await self._show(card, None)
        await self.event("local", turn=None, action="picture", target=action.target, phase="done", ok=ok, text=text)
        if ok:
            self.notes.append(f"{typed!r}: showed a picture. {card['text'][:400]}")
            self.notes = self.notes[-10:]
        self._log_line({"t": time.time(), "kind": "local", "prompt": typed, "action": "picture",
                        "target": action.target, "result": text, "ok": ok})

    async def open_thing(self, kind, value):
        """A click on a box in a picture. The bar sends what the card said; it is checked again here,
        since the card may have come from any process that can reach the socket."""
        target, error = cards.check_opens({"kind": kind, "value": value})
        if target is None:
            await self.event("local", turn=None, action="open", phase="done", ok=False, text=f"Cannot open that: {error}.")
            return
        if target["kind"] == "turn":
            turn = int(target["value"])
            if turn != self.current and turn not in self.turn_logs:
                await self.event("local", turn=None, action="open", phase="done", ok=False,
                                 text=f"The details of turn {turn} are not kept.")
                return
            await self.details(turn)
            return
        ok, text = await asyncio.to_thread(self._open, target)
        await self.event("local", turn=None, action="open", target=target["value"], phase="done", ok=ok, text=text)

    def _open(self, target: dict) -> tuple[bool, str]:
        try:
            return self.launcher.open_thing(target["kind"], target["value"])
        except Exception as e:  # noqa: BLE001 - one plain line, whatever broke
            return False, f"Could not open {target['value']}: {launcher._reason(e)}"

    def _stream_card(self, ev: dict):
        """Feed a provider event to the show_card follower; the card so far, when it grew."""
        if self._card_stream is None:
            return None
        try:
            card = self._card_stream.feed(ev)
        except Exception as e:  # noqa: BLE001 - a picture that cannot be followed is drawn when it is whole
            print(f"agentd: card stream: {type(e).__name__}: {e}", file=sys.stderr)
            return None
        if card is not None and card["id"] not in self._stream_ids:
            self._stream_ids.append(card["id"])
        return card

    async def _clear_streams(self, only: str | None = None):
        """Take back partial cards whose show_card call failed or never finished."""
        for sid in [only] if only else list(self._stream_ids):
            if sid in self._stream_ids:
                self._stream_ids.remove(sid)
                await self.broadcast({"type": "event", "kind": "card", "turn": self.current,
                                      "card": {"id": sid, "gone": True}})

    async def _receipt(self, turn: int, narrator: narrate.Narrator, before: "asyncio.Task | None",
                       befores: dict[str, asyncio.Task]):
        """After the closing line: what the turn changed in a part of the machine it touched, as a
        before and after. Nothing when nothing changed, when the agent drew a picture itself, or
        when another turn has started."""
        if before is None or narrator.drew:
            return
        kinds = [k for k in dict.fromkeys(k for k, _ in narrator.parts) if k != "service"]
        services = [u for k, u in narrator.parts if k == "service"][:2]
        if not kinds and not services:
            return
        try:
            was = await asyncio.wait_for(asyncio.shield(before), sysmap.BUDGET + 1.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - no before, no receipt
            return
        found = None
        if kinds:
            now = await asyncio.to_thread(sysmap.snapshot, tuple(kinds), self.provider.name)
            for k in kinds:
                if (was or {}).get(k) is not None and now.get(k) is not None:
                    found = sysmap.receipt(k, was[k], now[k])
                    if found:
                        break
        if found is None:
            for unit in services:
                task = befores.get(unit)
                b = await task if task is not None else None
                if not b or any("activating" in str(f.get("value")) or "reloading" in str(f.get("value"))
                                for f in b if f["key"] == "state"):
                    continue   # captured while it was already changing: not a before
                a = await asyncio.to_thread(sysmap.snapshot_service, unit)
                found = sysmap.receipt("service", b, a, unit) if a else None
                if found:
                    break
        if found is not None and self.current is None and self._closed_turn == turn:
            await self._show(found, turn)

    async def details(self, turn):
        path = self.turn_logs.get(turn) if turn is not None else watch.last_turn_file()
        argv = [launcher._bombadil(), "watch"] + (["--file", str(path)] if path else [])
        if turn is not None and turn == self.current:
            argv.append("--follow")
        try:
            await asyncio.to_thread(self.launcher.details, argv, True)
        except Exception as e:  # noqa: BLE001
            await self.event("local", turn=None, action="details", phase="done", ok=False,
                             text=f"Could not show the details: {e}")

    def _busy(self) -> bool:
        return self.current is not None

    async def stop(self) -> bool:
        """End the running turn and everything it started. False when nothing runs.

        Returns at once; the stopper works in the background. A turn still saving its restore
        point is marked, and ends before its CLI starts."""
        if self.current is None:
            return False
        if self.stopping:
            return True
        self.stopping = True
        self._stopped_line = self.narrator.stopped_line() if self.narrator else "Stopped."
        if self.proc is not None:
            self._background(self._stop_proc(self.proc, self._unit))
        await self.event("status", text="Stopping", risk=None, command=None, source="step")
        return True

    async def _stop_proc(self, proc: asyncio.subprocess.Process, unit: str | None):
        loop = asyncio.get_running_loop()
        turn = self.current

        def notify(text: str):
            loop.call_soon_threadsafe(lambda: self._background(
                self.event("status", turn=turn, text=text, risk=None, command=None, source="step")))
        try:
            await asyncio.to_thread(self.stopper.stop, proc.pid, unit, notify)
        except Exception as e:  # noqa: BLE001 - never leave a turn running because stop broke
            await self.event("error", turn=turn, text=f"stop: {e}")
            if proc.returncode is None:
                proc.kill()

    # -- turns --

    async def _worker(self):
        while True:
            while not self.pending or self._hold:
                self._wake.clear()
                await self._wake.wait()
            turn_id, prompt = self.pending.pop(0)
            self.stopping = False
            self._stopped_line = ""
            self.proc = None
            self._unit = None
            self.current = turn_id
            try:
                await self.turn(prompt)
            except Exception as e:  # noqa: BLE001 - keep the daemon alive whatever a turn does
                await self.event("error", text=f"{type(e).__name__}: {e}")
                await self.event("turn_end", seconds=0, stopped=self.stopping)
            finally:
                self.current = None
                self.narrator = None
                self.plan_msg = None
                self.proc = None
                self.stopping = False
                await self.broadcast(self._status())

    async def event(self, kind: str, **fields):
        fields.setdefault("turn", self.current)
        msg = {"type": "event", "kind": kind, **fields}
        log = self.turn_logs.get(fields["turn"]) if fields["turn"] is not None else None
        if log is not None and not (kind == "status" and fields.get("source") != "step"):
            _append(log, {"t": time.time(), **msg})
        await self.broadcast(msg)

    async def turn(self, prompt: str):
        shell = prompt.startswith("!")
        started = time.time()
        self.narrator = narrator = narrate.Narrator()
        if not shell:
            narrator.note_prompt(prompt)   # a [Screen] block or a coding session's request came in with it
        log = paths.state_dir() / "turns" / f"{int(started * 1000)}-{self.current}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.turn_logs[self.current] = log
        for old in sorted(self.turn_logs)[:-50]:
            self.turn_logs.pop(old, None)
        # Turns run in the order they were asked, so a mark left by one that was unqueued is dead.
        asked_by = self.asked_by.pop(self.current, None)
        self.asked_by = {i: who for i, who in self.asked_by.items() if i > self.current}
        # Something true on screen before snapper, which can take a second.
        await self.event("turn_start", prompt=prompt, snapshot=None, asked_by=asked_by)
        await self.broadcast(self._status())
        if not shell and not self.provider.installed:
            await self.event("error", text=f"{self.provider.binary} is not installed yet: press Super+Return "
                                           "and run bombadil-setup")
            await self.event("turn_end", seconds=0, summary="", changed=False, irreversible=False,
                             stopped=False, line="")
            return
        self.turns += 1
        # What this part of the machine looks like now, for the receipt; nobody waits for it.
        before = None
        self._card_stream, self._stream_ids = cards.CardStream(), []
        self._closed_turn = None
        self._befores = {}
        if not shell and self.explain != "brief":
            before = asyncio.ensure_future(asyncio.to_thread(sysmap.snapshot, sysmap.BEFORE_KINDS, self.provider.name))
        await asyncio.to_thread(self.launcher.clear_undo)
        snap = None
        if self.snaps.available:
            await self.event("status", text="Saving a restore point", risk=None, command=None, source="step")
            try:
                snap = await asyncio.to_thread(self.snaps.create, f"turn:{self.turns}: {prompt[:60]}")
            except subprocess.CalledProcessError as e:
                await self.event("error", text=f"no undo point for this turn: snapper failed ({(e.stderr or '').strip()[-200:]})")
            if snap:
                await self.event("snapshot", number=snap.number)
        if self.stopping:
            # Stopped while the restore point was saved: the CLI never starts.
            line = self._stopped_line or "Stopped."
            await self.event("turn_end", seconds=round(time.time() - started, 1), summary="", changed=False,
                             irreversible=False, stopped=True, line=line, read=narrator.read_list())
            self._log(prompt, {"text": "", "ok": None}, snap, [], True, "", narrator.read_list())
            return
        turn = providers.Turn(prompt=prompt, session_id=self.session_id)
        self.workdir.mkdir(parents=True, exist_ok=True)
        if shell:
            cmd = ["sh", "-c", prompt[1:]]
            source = providers.Shell()
        else:
            if self.notes:
                # Open, undo and the rest happened without the model; tell it before it acts.
                turn.prompt = ("[Done by the user without you since your last turn: "
                               + "; ".join(self.notes) + "]\n\n" + prompt)
                self.notes = []
            cmd = self.provider.command(turn, self.workdir)
            source = self.provider
        env = {**os.environ, **source.env()}
        if snap:
            # "undo that" runs in a turn of its own; the OS tools must roll back past this turn's
            # snapshot, not to it.
            env["BOMBADIL_TURN_SNAPSHOT"] = str(snap.number)
        unit = f"bombadil-turn-{os.getpid()}-{self.current}-{int(started)}"
        self._unit = unit if procs.scope_supported() else None
        self.proc = proc = await asyncio.create_subprocess_exec(
            *procs.in_scope(cmd, unit), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            # A typed "!command" shows its errors in its output, like a terminal would.
            stderr=asyncio.subprocess.STDOUT if shell else asyncio.subprocess.PIPE, cwd=str(turn.cwd), env=env,
            # Its own process group: what it leaves running stays findable for Stop.
            start_new_session=True,
            limit=64 * 1024 * 1024)  # a stream-json line can carry a whole screenshot
        if self.stopping:
            # Stop came while the CLI was being started.
            self._background(self._stop_proc(proc, self._unit))
        try:
            if not shell:
                proc.stdin.write(turn.prompt.encode())
                await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass   # it died at once (or was stopped); its exit says why
        stderr = asyncio.create_task(proc.stderr.read() if proc.stderr else asyncio.sleep(0, b""))
        await self.broadcast(self._status())

        result = {"text": "", "ok": None}
        pending_session = None
        reported_error = False
        if shell:
            # A typed command is one step: its words, its mark and its exact command.
            await self._on_event({"kind": "tool", "name": "Bash", "input": {"command": prompt[1:]}, "id": "shell"},
                                 turn, result, None, False)
        state = {"session": None, "error": False}

        async def pump():
            async for raw in proc.stdout:
                for ev in source.parse(raw.decode(errors="replace")):
                    state["session"], state["error"] = await self._on_event(
                        ev, turn, result, state["session"], state["error"])

        reading = asyncio.create_task(pump())
        exited = asyncio.create_task(_exited(proc))
        try:
            done, _ = await asyncio.wait({reading, exited}, return_when=asyncio.FIRST_COMPLETED)
            if reading in done:
                reading.result()
            await exited
            try:
                await asyncio.wait_for(asyncio.shield(reading), OUTPUT_GRACE)
            except asyncio.TimeoutError:
                # Something it started in the background holds the output open (`!server &`).
                # The turn is over, as in a terminal; keep emptying the pipe so that job never
                # blocks on it, without showing its output as this turn's.
                reading.cancel()
                self._background(_drain(proc.stdout))
            pending_session, reported_error = state["session"], state["error"]
            source.returncode = proc.returncode
            for ev in source.finish():
                pending_session, reported_error = await self._on_event(
                    ev, turn, result, pending_session, reported_error)
            try:
                err = ((await asyncio.wait_for(asyncio.shield(stderr), OUTPUT_GRACE)) or b"")
            except asyncio.TimeoutError:
                err = b""
                if proc.stderr is not None:
                    self._background(_drain(proc.stderr))
            err = err.decode(errors="replace").strip()[-2000:]
            if (proc.returncode not in (0, None) and not reported_error and result["ok"] is not True
                    and not self.stopping):
                await self.event("error", text=err or f"{source.name} exited with {proc.returncode}")
        finally:
            if proc.returncode is None:
                proc.kill()
                await _exited(proc)
            exited.cancel()
            reading.cancel()
            stderr.cancel()
            stopped = self.stopping
            summary = narrator.summary()
            if stopped:
                line = self._stopped_line or narrator.stopped_line()
            else:
                line = summary
            await self._clear_streams()
            await self.event("turn_end", seconds=round(time.time() - started, 1), summary=summary,
                             changed=bool(narrator.done), irreversible=narrator.irreversible,
                             stopped=stopped, line=line, read=narrator.read_list())
            self._log(prompt, result, snap, cmd, stopped, summary, narrator.read_list())
            self._closed_turn = self.current
            self._background(self._receipt(self.current, narrator, before, self._befores))

    async def _on_event(self, ev, turn, result, pending_session, reported_error):
        kind = ev["kind"]
        if kind == "session":
            return ev.get("session_id") or pending_session, reported_error
        try:
            line = self.narrator.on_event(ev) if self.narrator else None
        except Exception as e:  # noqa: BLE001 - odd input costs a line, never the turn
            print(f"agentd: narrate {kind}: {type(e).__name__}: {e}", file=sys.stderr)
            line = None
        if line is not None and not self.stopping:
            if line.get("source") == "step":
                # What the turn has changed so far, for the desk's "1 package and 2 files so far".
                line = {**line, "touched": self.narrator.touched_counts(),
                        "touched_text": self.narrator.touched_text()}
            await self.event("status", **line)
        partial = self._stream_card(ev) if kind in ("tool_start", "tool_input") else None
        if partial is not None and not self.stopping:
            # Not logged: only the finished card is (see _show).
            await self.broadcast({"type": "event", "kind": "card", "turn": self.current, "card": partial})
        if kind == "tool" and self.narrator is not None and self.explain != "brief":
            for k, unit in self.narrator.parts:
                if k == "service" and unit not in self._befores and len(self._befores) < 3:
                    # As the step is read, before it has run; a state already changing is dropped later.
                    self._befores[unit] = asyncio.ensure_future(asyncio.to_thread(sysmap.snapshot_service, unit))
        if kind == "tool_result" and ev.get("id"):
            await self._clear_streams(f"stream-{ev['id']}")   # a show_card call that failed drew nothing
        plan = self.narrator.take_plan() if self.narrator else None
        if plan is not None and not self.stopping:
            self.plan_msg = {"type": "event", "kind": "plan", "turn": self.current, "steps": plan}
            await self.event("plan", steps=plan)
        if kind in LINE_ONLY:
            return pending_session, reported_error
        if kind == "result":
            result.update(text=ev.get("text", ""), ok=ev.get("ok", True))
            if ev.get("ok", True):
                # Adopt a session id only from a turn that worked, so a dead one is not kept.
                self.session_id = ev.get("session_id") or pending_session or self.session_id
            else:
                reason = ev.get("terminal_reason") or ""
                text = "cancelled" if reason.startswith("aborted") else (ev.get("text") or "the turn failed")
                if turn.session_id and (ev.get("num_turns") == 0 or "no conversation found" in text.lower()):
                    self.session_id = None
                    text += " (the previous conversation is gone; the next prompt starts a new one)"
                if not self.stopping:
                    await self.event("error", text=text)
                reported_error = True
        elif kind == "error":
            reported_error = True
        if kind == "tool_result" and len(ev.get("output") or "") > MAX_OUTPUT:
            ev = {**ev, "output": ev["output"][:MAX_OUTPUT] + "\n[... cut]"}
        if kind in ("tool", "file_change") and self.narrator is not None:
            # The step's reason and what it followed, for Details, next to the step they explain.
            ev = {**ev, **self.narrator.last_notes}
        await self.event(kind, **{k: v for k, v in ev.items() if k != "kind"})
        return pending_session, reported_error

    def _log(self, prompt, result, snap, cmd, stopped=False, summary="", read=None):
        self._log_line({"t": time.time(), "prompt": prompt, "result": result["text"], "ok": result["ok"],
                        "snapshot": snap.number if snap else None,
                        "provider": "shell" if prompt.startswith("!") else self.provider.name,
                        "session": self.session_id, "stopped": stopped, "summary": summary,
                        # What the turn read, yours or outside: the brain's "came from" links use it.
                        "read": read or [],
                        "details": str(self.turn_logs.get(self.current, ""))})

    def _log_line(self, entry: dict):
        paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
        with paths.turns_log().open("a") as f:
            f.write(json.dumps(entry) + "\n")


def _action(msg: dict) -> launcher.Action | None:
    """A launcher action named by a button (Undo, Stop) rather than typed."""
    kind = str(msg.get("action", ""))
    if kind in launcher.CORE_COMMANDS or kind in launcher.UTILITY_COMMANDS:
        return launcher.Action(kind)
    return None


def _append(path: Path, entry: dict):
    try:
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


async def _exited(proc: asyncio.subprocess.Process) -> int | None:
    """proc.wait(), except that Python before 3.12 also waits there for every pipe to close,
    which a job left in the background keeps open for as long as it runs."""
    waiter = asyncio.ensure_future(proc.wait())
    try:
        while not waiter.done() and proc.returncode is None:
            await asyncio.wait({waiter}, timeout=0.05)
    finally:
        if not waiter.done():
            waiter.cancel()
    return proc.returncode


async def _drain(stream: asyncio.StreamReader):
    try:
        while await stream.read(65536):
            pass
    except (OSError, ValueError):
        pass


def _apps_key():
    root = paths.apps_dir()
    if not root.exists():
        return ()
    return tuple(sorted((p.name, (p / "app.toml").stat().st_mtime if (p / "app.toml").exists() else 0)
                        for p in root.iterdir() if (p / "main.qml").exists()))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = config.load()
    name = os.environ.get("BOMBADIL_PROVIDER", cfg.provider)
    provider = providers.get(name, model=cfg.model)
    if not provider.installed:
        # Keep serving so the bar connects and can say what is missing; turns report it.
        print(f"provider {name!r} ({provider.binary}) is not installed; run bombadil-setup", file=sys.stderr)
    snaps = snapshots.Snapshots() if cfg.snapshots else _NoSnapshots()
    daemon = AgentD(provider, snaps, explain=cfg.explain)
    print(f"agentd: {provider.name} on {daemon.socket_path}", file=sys.stderr)
    try:
        asyncio.run(daemon.serve())
    except KeyboardInterrupt:
        pass
    return 0


class _NoSnapshots(snapshots.Snapshots):
    available = False

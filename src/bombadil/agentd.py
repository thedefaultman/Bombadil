"""agentd: the session daemon. The agent *is* the session; this process keeps it alive.

Runs as the user, started by Hyprland at login. Listens on a Unix socket for newline
delimited JSON. Any number of clients (the Quickshell bar, `bombadil ask`, a generated
app) can connect; every event is broadcast to all of them.

Client -> daemon:  {"type": "prompt", "text": "..."}   a turn, or a launcher word ("browser", "undo");
                                                        "asked_by": "builder" says a coding session asks
                                                        (another helper's name works the same)
                   {"type": "stop"}                     end the running turn and all it started
                   {"type": "cancel"}                   the same as stop
                   {"type": "unqueue", "turn": n}       drop a prompt still waiting its turn
                   {"type": "local", "action": "undo"}  a launcher action by name (the Undo button)
                   {"type": "details", "turn": n}       show a turn's commands and output (drawer);
                                                        again while it shows closes it
                   {"type": "close_details"}            put the drawer away (Esc in the pill)
                   {"type": "summon", "text"?: "..."}   ask the bar to take the keyboard (Super), with
                                                        words to finish in the pill (the Brain's Ask)
                   {"type": "open", "kind": "path"|"unit"|"package"|"url"|"turn", "value": "..."}
                                                        open what a box in a picture names; answered
                                                        by a "local" event with action "open"
                   {"type": "card", "card": {...}}      a picture to draw (os-mcp's show_card and system_map);
                                                        answered with {"type": "card_ack", "shown": bool}
                   {"type": "setup_action", "id": "provider:codex"|"signin"|"show"|"cancel"|"wifi"}
                                                        a chip under the setup line (see below)
                   {"type": "signin", "provider": "codex"?}  sign in (again), after switching provider
                   {"type": "open_url", "url": "...", "signin": id?}  a link for the browser panel
                                                        (bombadil-browser: $BROWSER and xdg-open)
                   {"type": "desk", "op": "get"}        the desk's state (and the plan of a running turn)
                   {"type": "desk", "op": "fold"|"hide"|"show"|"move", "widget": "machine",
                    "rail": "left"|"right", "rank": 0}  change the desk; the state comes back to everyone
                   {"type": "desk-tool", "id": s, "turn": n, "op": ..., "widget": ...}
                                                        the os-mcp `desk` tool; answered with desk-result
                   {"type": "jobs", "op": "get"}        the jobs table (also sent after `desk get`)
                   {"type": "jobs", "op": "stop"|"dismiss"|"why", "id": job}
                                                        stop a job (systemctl, never the model), drop a
                                                        finished one's row, show its output in the drawer
                   {"type": "job-tool", "id": s, "turn": n, "op": "start"|"list"|"stop", "title": ...,
                    "command": ..., "kind": "job"|"watch", "seconds": n, "job": id}
                                                        the os-mcp `job` tool; answered with job-result
                   {"type": "status"}
Daemon -> clients: {"type": "event", "kind": "turn_start"|"snapshot"|"status"|"text"|"tool"|
                    "tool_result"|"file_change"|"result"|"error"|"turn_end"|"queued"|"unqueued"|
                    "local"|"card"|"plan", "turn": n, ...}
                   {"type": "status", "busy": bool, "provider": "...", "turns": n, "queue": [...], ...}
                   {"type": "entries", "entries": [...]}  names the pill can complete and open
                   {"type": "setup", "state": ..., "line": ..., "actions": [...]}  see below
                   {"type": "summon", "text"?: "..."}
                   {"type": "desk", "folded": bool, "hidden": [...], "rails": {...}, "order": {...},
                    "screen": ""}                       the desk's state: to whoever asks, and on every change
                   {"type": "desk-result", "id": s, "ok": bool, "text": "..."}
                                                        the answer to a desk-tool, to its sender only
                   {"type": "jobs", "jobs": [{"id", "title", "kind": "job"|"watch"|"timer", "state":
                    "running"|"done"|"failed", "started", "deadline", "ended", "pct", "last", "unit"}]}
                                                        the jobs table: to whoever asks, and on change
                   {"type": "job-result", "id": s, "ok": bool, "text": "..."}
                                                        the answer to a job-tool, to its sender only

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
turn_start carries "asked_by": "builder" (or another helper's name) when the turn was started for a coding
session, else null.
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

"setup" is whether the machine can talk to its AI: state "choose" (no provider picked yet: the
pill offers Claude and Codex), "checking", "signed_out", "offline" (no way to reach the sign-in
page), "signing_in" (the CLI's login runs and its page is in the browser panel, signin.py) or
"ready". Prompts wait in the queue until it is ready, and a turn that finds the login gone
signs in again and then runs once more. `line` is what the pill says about it, `tone` is
step, ask, error or done, and `actions` are its chips: [{"id", "label", "style"}].
The desk-tool changes the desk only in the turn that is running, and only when that turn's own
typed words asked for the desk (desk.asked_for_desk); otherwise it is refused.

Jobs are background commands, watchers and timers the agent starts with the `job` tool (jobs.py):
transient systemd user units that outlive the turn. The job-tool's `start` needs the turn that is
running (a stale process cannot start jobs) but not the person's words; `list` and `stop` need
neither. While the table has anything in it agentd looks at it every JOBS_POLL seconds, broadcasts
the table when it changes, and when a job ends says one line in the pill (a "local" event with no
turn) and tells the next turn's prompt.

Every turn: say turn_start, snapshot the system (undo point), run one provider CLI turn with
the os-mcp server attached in its own scope, stream its events, log the turn. Launcher words
(apps, panels, undo, stop) are handled here at once and never wait for the model.

The log (turns.jsonl) is what the brain learns turns from: each row has the turn's number,
its scope, when it began, and the files it wrote and read by its own tool calls. Numbers go
on across restarts, also past a turn a crash cut off before its row (the last one begun is
kept in the state folder's "turn"). The brain is also told when a turn starts and ends, so
it can name the turn's writes while they happen. That is a courtesy: it is sent from a
thread, never waited for, and a brain that is slow, down or broken costs the turn nothing.
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from . import browser, cards, config, launcher, narrate, paths, procs, providers, signin, snapshots, sysmap, watch
from .brain import client as brain_client
from .desk import Desk, asked_for_desk
from .jobs import JobError, Jobs, ending, started_text

# Provider events that only feed the live line; clients get the "status" events made from them.
LINE_ONLY = {"tool_start", "tool_input", "text_delta", "thinking", "message_start"}
_WHO = re.compile(r"[a-z][a-z0-9_-]{0,23}")   # a helper's name in "asked_by"
MAX_OUTPUT = 16_000   # characters of one command's output kept in events and the turn's log
# A client that stops reading (a hung bar) is dropped rather than allowed to hold up the
# others: its messages wait in a queue of this many, each write gets this long.
CLIENT_BACKLOG = 10_000
SEND_TIMEOUT = 5.0
# After a turn's process exits, how long its output may take to drain. Longer means a job it
# left in the background (`!server &`) still holds the pipe; the turn ends without it.
OUTPUT_GRACE = 1.0
# Paths kept per turn row for each of wrote and read; a turn that touched more is a build or
# a bulk edit, and the brain's watcher sees those writes anyway.
MAX_TURN_FILES = 200
# However the brain's client misbehaves, the next note is not held up longer than this.
POKE_TIMEOUT = 2.0
# Claude Code's tools that write a file, and the input that names it.
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
# What the next turn is told for the brain's words, in place of the brain's own line.
BRAIN_NOTES = {"brain": "opened the Brain", "whyhere": "the brain answered in the line"}
# The CLI says nothing while it retries an unreachable provider. With no tool running and no event
# for this long, the line says so instead of staying on its last words; the watchdog looks every tick.
NO_PROGRESS_SECS = 30.0
WATCHDOG_TICK = 5.0
# What a failed result says when the account, not the request, is the problem (then the provider's
# own first line follows, which is where "resets 5pm" is), or when it is only a busy moment.
# Anything else is passed through as it came.
LIMIT_WORDS = ("usage limit", "spend limit", "spending limit", "credit balance", "hit your limit")
LIMIT_TEXT = "This account has hit a usage or spending limit. Try again after it resets, or raise the limit."
RATE_WORDS = ("rate limit", "rate_limit", "too many requests")
RATE_TEXT = "The provider is rate limiting requests; try again in a minute."
OFFLINE_POLL = 5.0   # while there is no way to the sign-in page, look again this often
READY_LINE_SECONDS = 120.0   # how long "Signed in to Claude" is worth saying
# Changes to the desk that come close together go out as the state they end in.
DESK_DEBOUNCE = 0.03
# The same for the jobs table, which is also looked at this often (seconds) while it has anything in it.
JOBS_DEBOUNCE = 0.03
JOBS_POLL = 2.0


class AgentD:
    def __init__(self, provider: providers.Provider, snaps: snapshots.Snapshots | None = None,
                 socket_path: Path | None = None, launch: launcher.Launcher | None = None,
                 stopper: procs.Stopper | None = None, explain: str = "normal", desk: Desk | None = None,
                 jobs: Jobs | None = None, chosen: bool = True, auto_signin: bool = False, panel=None):
        self.provider = provider
        self.explain = explain                      # brief | normal | teach: at brief no receipts
        self.snaps = snaps or snapshots.Snapshots()
        self.socket_path = socket_path or paths.socket_path()
        # One desk: the launcher's words, the shell and the agent's tool all change this one.
        self.desk = desk or getattr(launch, "desk_state", None) or Desk().load()
        self.launcher = launch or launcher.Launcher(snaps=self.snaps, desk=self.desk)
        self.stopper = stopper or procs.Stopper()
        self.clients: dict[asyncio.StreamWriter, asyncio.Queue] = {}
        self.session_id: str | None = None
        # Turn numbers go on across restarts, past a turn a crash cut off before its row.
        self.turns = max(_last_turn(paths.turns_log()), _begun())
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
        self._files: TurnFiles | None = None        # what the running turn wrote and read
        self._poking: asyncio.Task | None = None    # the last note to the brain, still going
        self._card_seq = 0
        self._card_stream: cards.CardStream | None = None   # show_card calls being written (Claude)
        self._stream_ids: list[str] = []            # their card ids, until the finished card takes one
        self._closed_turn: int | None = None        # the turn whose closing line is on screen
        self._befores: dict[str, asyncio.Task] = {}   # a service's facts from when a step first touched it
        # The provider: picked yet (config or BOMBADIL_PROVIDER), and signed in (setup, above).
        self.chosen = chosen
        self.auto_signin = auto_signin              # sign in at once when the session starts signed out
        self.access = "checking"
        self._access_line = ("", "step")
        self.signin: signin.SignIn | None = None
        self._signin_task: asyncio.Task | None = None
        self._offline_task: asyncio.Task | None = None
        self._external = False                       # a sign-in started in a terminal is being watched
        self._signed_out = False                     # the running turn found the login gone
        self._starting = False                       # start_signin is between its check and its SignIn
        self._login_gone = False                     # a turn found the stored login dead; signed_in() would not say
        self._access_at = 0.0                        # when the setup state last changed
        self._turn_provider = None                   # the provider the running turn belongs to
        self._retried: set[int] = set()              # turns already run again after a sign-in
        self._turn_notes: list[str] = []             # what the running turn was told the user did without it
        self.panel = panel or browser.Panel()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.turn_prompt: str | None = None         # what the person typed for the running turn, as typed
        self.plan_msg: dict | None = None           # the running turn's latest plan event
        self._desk_dirty = False
        self._desk_last = self.desk.snapshot()      # what the shell was last told
        self.desk.on_change = self._desk_changed
        self.jobs = jobs if jobs is not None else Jobs()
        self._jobs_task: asyncio.Task | None = None  # looks at the jobs while the table has anything
        self._jobs_again = False                    # a job started while that task was deciding to stop
        self._jobs_dirty = False
        self._jobs_last = self.jobs.snapshot()      # what the shell was last told

    # -- socket --

    async def serve(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        # Probe for systemd scopes now, not on the first Enter.
        await asyncio.to_thread(procs.scope_supported)
        self._loop = asyncio.get_running_loop()
        server = await asyncio.start_unix_server(self._client, path=str(self.socket_path))
        worker = asyncio.create_task(self._worker())
        self._background(self.check_access(start=self.auto_signin))
        watcher = asyncio.create_task(self._watch_apps())
        self._jobs_kick()   # what a restarted agentd finds still running is counted again
        async with server:
            try:
                await server.serve_forever()
            finally:
                for task in (worker, watcher, self._jobs_task):
                    if task is not None:
                        task.cancel()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        queue: asyncio.Queue = asyncio.Queue(CLIENT_BACKLOG)
        self.clients[writer] = queue
        sender = asyncio.create_task(self._sender(writer, queue))
        try:
            await self._send(writer, self._status())
            await self._send(writer, await self._entries_msg())
            await self._send(writer, self._setup_msg())
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
            if action is None and self.access == "choose":
                # First boot asks which AI: typing its name answers too.
                name = launcher._lookup(launcher.normalize(text), launcher.PROVIDER_WORDS)
                action = launcher.Action("provider", name) if name else None
            if action is not None:
                await self._send(writer, {"type": "local", "action": action.kind})
                self._background(self.local(action, text))
                return
            self.next_id += 1
            who = msg.get("asked_by")
            if isinstance(who, str) and _WHO.fullmatch(who):
                self.asked_by[self.next_id] = who
            # The id lets a client (bombadil ask) follow its own turn among everyone's events.
            await self._send(writer, {"type": "queued", "turn": self.next_id})
            waits = not self._runnable(text)
            if self.current is not None or self.pending or waits:
                await self.broadcast({"type": "event", "kind": "queued", "turn": self.next_id, "prompt": text})
            self.pending.append((self.next_id, text))
            self._wake.set()
            await self.broadcast(self._status())
            if waits and self.access == "signed_out":
                # Asked for something while signed out: sign in, and the ask runs after.
                self._background(self.start_signin())
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
            text = _pill_words(msg.get("text"))
            await self.broadcast({"type": "summon", **({"text": text} if text else {})})
        elif t == "card":
            await self._card_message(msg, writer)
        elif t == "desk":
            await self._desk_op(msg, writer)
        elif t == "desk-tool":
            await self._send(writer, await self._desk_tool(msg))
        elif t == "jobs":
            await self._jobs_op(msg, writer)
        elif t == "job-tool":
            await self._send(writer, await self._job_tool(msg))
        elif t == "status":
            await self._send(writer, self._status())
        elif t == "setup_action":
            self._background(self.setup_action(str(msg.get("id", ""))))
        elif t == "signin":
            name = msg.get("provider")
            self._background(self.choose(str(name)) if name and (name != self.provider.name or not self.chosen)
                             else self.signin_asked())
        elif t == "open_url":
            self._background(self.open_url(str(msg.get("url", "")), msg.get("signin")))

    async def _step_line(self, text: str):
        """A live step line about the turn itself (the CLI is up, quiet, back), with what the turn
        has changed so far like every other step line."""
        narrator = self.narrator
        await self.event("status", text=text, risk=None, command=None, source="step",
                         touched=narrator.touched_counts() if narrator else {},
                         touched_text=narrator.touched_text() if narrator else "")

    def _status(self) -> dict:
        # Busy from the moment a prompt is accepted, so Esc stops it even before its turn starts.
        return {"type": "status",
                "busy": self.current is not None or any(self._runnable(p) for _, p in self.pending),
                "provider": self.provider.name, "setup": self.access, "turns": self.turns,
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

    # -- the desk --

    def _desk_changed(self):
        """Desk.on_change. The launcher changes the desk in a worker thread, so hop to the loop."""
        try:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._desk_soon)
        except RuntimeError:
            pass   # the loop is closed: agentd is stopping

    def _desk_soon(self):
        if not self._desk_dirty:
            self._desk_dirty = True
            self._background(self._desk_broadcast())

    async def _desk_broadcast(self):
        await asyncio.sleep(DESK_DEBOUNCE)
        self._desk_dirty = False
        state = await asyncio.to_thread(self.desk.snapshot)
        if state != self._desk_last:
            self._desk_last = state
            await self.broadcast(state)

    async def _desk_op(self, msg: dict, writer: asyncio.StreamWriter):
        """The shell asks for the desk, or changes it (a click on a strip, a drag later)."""
        op = str(msg.get("op", ""))
        if op == "get":
            await self._send(writer, await asyncio.to_thread(self.desk.snapshot))
            # A bar that restarts mid-turn still has the route.
            if self.current is not None and self.plan_msg is not None:
                await self._send(writer, self.plan_msg)
            # And what is counting, which the desk's card and strip are made from.
            await self._send(writer, await asyncio.to_thread(self.jobs.snapshot))
            return
        if op not in ("fold", "hide", "show", "move"):
            return

        def change():
            before = self.desk.snapshot()
            ok, text = self.desk.apply("toggle" if op == "fold" else op, msg.get("widget"),
                                       msg.get("rail"), msg.get("rank"))
            return ok, text, self.desk.snapshot() != before
        ok, text, changed = await asyncio.to_thread(change)
        if ok and changed:
            # Not said on the line (a gesture is its own answer), but the agent should know.
            self.notes = [*self.notes, f"at the desk: {text}"][-10:]
            self._log_line({"t": time.time(), "kind": "local", "prompt": "at the desk", "action": "desk",
                            "target": str(msg.get("widget") or ""), "result": text, "ok": True})

    async def _desk_tool(self, msg: dict) -> dict:
        """The os-mcp `desk` tool. It works in the turn that is running, and only when that
        turn's own words asked for the desk: a widget that appears unasked is a popup by another
        name. The answer goes to the one client that asked."""
        def result(ok: bool, text: str) -> dict:
            return {"type": "desk-result", "id": msg.get("id"), "ok": ok, "text": text}
        turn = msg.get("turn")
        if self.current is None or isinstance(turn, bool) or turn != self.current or self.stopping:
            return result(False, "That turn is over, so the desk stays as it is.")
        # The raw prompt, not the turn's: that one has notes in front, which quote earlier desk words.
        if not asked_for_desk(self.turn_prompt or ""):
            return result(False, "The person did not ask for the desk in this turn, so it stays as it is. "
                                 "Rearrange it only when they ask.")
        op = str(msg.get("op", ""))
        if op not in ("show", "hide", "move", "fold", "unfold", "state"):
            return result(False, f"The desk cannot {op or 'do that'}. It can show, hide, move, fold, unfold "
                                 "and say its state.")
        ok, text = await asyncio.to_thread(self.desk.apply, op, msg.get("widget"), msg.get("rail"),
                                           msg.get("rank"))
        return result(ok, text)

    # -- jobs --

    def _jobs_kick(self):
        """Look at the jobs every JOBS_POLL seconds from now, unless that is already going."""
        self._jobs_again = True
        if self._jobs_task is None or self._jobs_task.done():
            self._jobs_task = asyncio.create_task(self._jobs_loop())

    async def _jobs_loop(self):
        """Look at the table until it is empty: a running job is read, a finished one leaves on
        its own clock, and the shell is told whenever the table is not what it was."""
        while True:
            self._jobs_again = False
            try:
                for rec in await asyncio.to_thread(self.jobs.poll):
                    await self._job_ended(rec)
                self._jobs_soon()
                if not (await asyncio.to_thread(self.jobs.snapshot))["jobs"] and not self._jobs_again:
                    return
            except Exception as e:  # noqa: BLE001 - keep looking whatever one look found
                print(f"agentd: looking at jobs: {type(e).__name__}: {e}", file=sys.stderr)
            await asyncio.sleep(JOBS_POLL)

    def _jobs_soon(self):
        if not self._jobs_dirty:
            self._jobs_dirty = True
            self._background(self._jobs_broadcast())

    async def _jobs_broadcast(self):
        await asyncio.sleep(JOBS_DEBOUNCE)
        self._jobs_dirty = False
        table = await asyncio.to_thread(self.jobs.snapshot)
        if table != self._jobs_last:
            self._jobs_last = table
            await self.broadcast(table)

    async def _job_ended(self, rec: dict):
        """One line in the pill, as a launcher word's answer is, and a note for the next prompt."""
        text, ok = ending(rec)
        await self.event("local", turn=None, action="job", target=rec["id"], phase="done", ok=ok, text=text)
        note = f"background job {rec['id']}: {text}"
        if not ok:
            log = await asyncio.to_thread(self.jobs.log_path, rec["id"])
            note += f" (its output is in {log})" if log is not None else ""
        self.notes = [*self.notes, note][-10:]
        self._log_line({"t": time.time(), "kind": "local", "prompt": "background job", "action": "job",
                        "target": rec["title"], "result": text, "ok": ok})

    async def _jobs_op(self, msg: dict, writer: asyncio.StreamWriter):
        """The shell asks for the table, or acts on a row: × stops a running job, × on a finished one
        drops its row, Why? shows its output. None of them goes through the model."""
        op, job_id = str(msg.get("op", "")), msg.get("id")
        if op == "get":
            await self._send(writer, await asyncio.to_thread(self.jobs.snapshot))
        elif op == "stop":
            self._background(self._jobs_stop(job_id))
        elif op == "dismiss":
            await asyncio.to_thread(self.jobs.dismiss, job_id)
            self._jobs_soon()
        elif op == "why":
            self._background(self._jobs_why(job_id))

    async def _jobs_stop(self, job_id):
        try:
            rec = await asyncio.to_thread(self.jobs.stop, job_id)
        except JobError as e:
            await self.event("local", turn=None, action="job", target=str(job_id), phase="done", ok=False,
                             text=str(e))
            return
        self._jobs_soon()
        if rec is not None:
            # Not said on the line (the × is its own answer), but the agent should know.
            text = f"Stopped {rec['title']}."
            self.notes = [*self.notes, f"background job {rec['id']}: {text}"][-10:]
            self._log_line({"t": time.time(), "kind": "local", "prompt": "background job", "action": "job",
                            "target": rec["title"], "result": text, "ok": True})

    async def _jobs_why(self, job_id):
        argv = await asyncio.to_thread(self.jobs.why, job_id)
        if argv is None:
            return
        try:
            await asyncio.to_thread(self.launcher.details, argv, True)
        except Exception as e:  # noqa: BLE001
            await self.event("local", turn=None, action="job", target=str(job_id), phase="done", ok=False,
                             text=f"Could not show the output: {e}")

    async def _job_tool(self, msg: dict) -> dict:
        """The os-mcp `job` tool. Starting a job is ordinary work, so the person's words do not
        gate it, but it must come from the turn that is running, so a stale process cannot start
        jobs. Listing and stopping need no turn. The answer goes to the one client that asked."""
        def result(ok: bool, text: str) -> dict:
            return {"type": "job-result", "id": msg.get("id"), "ok": ok, "text": text}
        op = str(msg.get("op", ""))
        try:
            if op == "start":
                turn = msg.get("turn")
                if self.current is None or isinstance(turn, bool) or turn != self.current:
                    return result(False, "That turn is over, so nothing was started.")
                if self.stopping:
                    return result(False, "That turn is being stopped, so nothing was started.")
                rec = await asyncio.to_thread(self.jobs.start, msg.get("title"), msg.get("command"),
                                              msg.get("kind") or "job", msg.get("seconds"))
                self._jobs_kick()
                self._jobs_soon()
                return result(True, started_text(rec, await asyncio.to_thread(self.jobs.log_path, rec["id"])))
            if op == "list":
                return result(True, await asyncio.to_thread(self.jobs.listing))
            if op == "stop":
                if not msg.get("job"):
                    return result(False, "Which job? `list` says which are running.")
                rec = await asyncio.to_thread(self.jobs.stop, msg.get("job"))
                if rec is None:
                    return result(False, f"There is no job {str(msg.get('job'))[:20]!r}. `list` says which "
                                         "are running.")
                self._jobs_soon()
                return result(True, f"Stopped {rec['title']}.")
        except JobError as e:
            return result(False, str(e))
        return result(False, f"Jobs cannot {op or 'do that'}. They can start, list and stop.")

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
        if action.kind in ("signin", "provider"):
            if action.kind == "signin":
                await self.signin_asked()
            else:
                await self.choose(action.target)
            line, _, _ = self._describe()
            await self.event("local", turn=None, action=action.kind, target=action.target, phase="done",
                             ok=self.access not in ("offline",), text=line or "Done.")
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
        await self.event("local", turn=None, action=action.kind, target=action.target, verb=action.verb,
                         phase="start", text=doing)
        ok, text = await asyncio.to_thread(self.launcher.run, action)
        await self.event("local", turn=None, action=action.kind, target=action.target, verb=action.verb,
                         phase="done", ok=ok, text=text)
        if ok:
            # The brain's answers quote page titles, which whoever made the page wrote: the
            # model hears that it was asked, not what it said.
            said = BRAIN_NOTES.get(action.kind, text)
            self.notes.append(f"{typed!r}: {said}")
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
            text, ok = f"Showing {action.title}.", True   # the picture carries its own sentence
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
            # Nothing runs but the sign-in: Esc cancels that.
            if self.signin is not None and self.signin.running:
                self.signin.cancel()
                return True
            if self._external and self.access == "signing_in":
                self._external = False
                await self._set_access("signed_out", "Sign-in cancelled.")
                return True
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

    # -- the provider: picked, and signed in --

    def _title(self) -> str:
        return self.provider.title or self.provider.name

    def _signed_out_text(self, said: str) -> str:
        """What the pill says when the running turn found its login gone. On the rerun after a
        sign-in it is the CLI's own words: the login is not what is wrong."""
        if self.current in self._retried:
            return said
        tp = self._turn_provider
        return f"{tp.title or tp.name} signed you out."

    def _setup_msg(self) -> dict:
        line, tone, actions = self._describe()
        if self.access == "ready" and time.monotonic() - self._access_at > READY_LINE_SECONDS:
            line = ""   # "Signed in" is news for a moment, not for a bar that restarts hours later
        s = self.signin
        return {"type": "setup", "state": self.access, "provider": self.provider.name, "title": self._title(),
                "line": line, "tone": tone, "actions": actions,
                "phase": s.phase if s is not None else None, "view": s.view if s is not None and s.running else None}

    def _describe(self) -> tuple[str, str, list[dict]]:
        """What the pill says about the setup, and the chips under it."""
        t = self._title()
        others = [(n, c.title or n) for n, c in providers.PROVIDERS.items()
                  if n in config.PROVIDERS and n != self.provider.name]
        switch = [{"id": f"provider:{n}", "label": f"Use {title} instead", "style": "quiet"} for n, title in others]
        cancel = {"id": "cancel", "label": "Cancel", "style": "quiet"}
        if self.access == "choose":
            return ("Which AI should run this computer?", "ask",
                    [{"id": f"provider:{n}", "label": providers.PROVIDERS[n].title or n, "style": "big"}
                     for n in config.PROVIDERS])
        if self.access == "offline":
            return (f"No internet. Connect to a network to sign in to {t}.", "error",
                    [{"id": "wifi", "label": "Wi-Fi", "style": "primary"},
                     {"id": "signin", "label": "Try again", "style": "quiet"}])
        if self.access == "signing_in":
            s = self.signin
            if s is None:   # started in a terminal; that CLI finishes it, agentd only watches
                return f"Sign in to {t} in the browser", "step", []
            if s.phase == "starting":
                return f"Opening the {t} sign-in", "step", [cancel]
            if s.phase == "ending":
                return f"Cancelling the {t} sign-in", "step", []
            if s.phase == "finishing":
                return f"Finishing the {t} sign-in", "step", []
            if s.view == "hidden":
                return (f"The {t} sign-in is waiting in the browser", "step",
                        [{"id": "show", "label": "Show sign-in", "style": "primary"}, cancel])
            if s.view == "closed":
                return (f"The {t} sign-in page was closed", "step",
                        [{"id": "show", "label": "Open it again", "style": "primary"}, cancel])
            return f"Sign in to {t} in the browser", "step", [cancel]
        line, tone = self._access_line
        if self.access == "signed_out":
            return (line or f"Sign in to {t} to start.", tone,
                    [{"id": "signin", "label": "Sign in", "style": "primary"}, *switch])
        return line, tone, []

    async def _set_access(self, state: str, line: str = "", tone: str = "step"):
        self.access = state
        self._access_at = time.monotonic()
        self._access_line = (line, tone)
        await self.broadcast(self._setup_msg())
        await self.broadcast(self._status())
        if state == "ready":
            self._wake.set()

    async def check_access(self, start: bool = False, announce: bool = False):
        """Is the provider picked and signed in? `start` signs in at once when it is not."""
        if not self.chosen:
            await self._set_access("choose")
            return
        if not self.provider.installed:
            await self._set_access("ready")   # its turns say it is missing
            return
        asked = self.provider
        try:
            ok = await asyncio.to_thread(asked.signed_in)
        except Exception as e:  # noqa: BLE001 - cannot tell: let the turns find out
            print(f"agentd: sign-in check: {type(e).__name__}: {e}", file=sys.stderr)
            ok = None
        if self.provider is not asked:
            return   # picked another while this one was asked: that pick has its own check
        if ok is False:
            if start:
                await self.start_signin()   # says signing_in (or offline) itself: no flash of signed_out
            else:
                await self._set_access("signed_out")
        else:
            await self._set_access("ready", f"{self._title()} is ready. Ask me for anything." if announce else "",
                                   "done")

    async def signin_asked(self):
        """The user asked to sign in ("sign in" typed, `bombadil signin`, a chip).

        Not when the login is fine and starting one would end it: a `codex login` signs the stored
        login out as it starts, even if the new one is then called off."""
        if self.access == "ready" and self.provider.login_replaces and await self._still_signed_in():
            await self._set_access("ready", f"You are already signed in to {self._title()}.", "done")
            return
        await self.start_signin()

    async def start_signin(self):
        """Run the provider CLI's login with its page in the browser panel (signin.py)."""
        if not self.chosen:
            await self._set_access("choose")
            return
        if self.signin is not None and self.signin.running:
            await self.signin.show()
            await self.broadcast(self._setup_msg())
            return
        if self._starting:
            return   # a double tap, or two causes at once: one sign-in
        self._starting = True
        try:
            provider = self.provider
            host = provider.signin_host
            if host and not await asyncio.to_thread(signin.reachable, *host):
                if self.provider is provider:
                    await self._set_access("offline")
                    if self._offline_task is None:
                        self._offline_task = self._background(self._wait_online())
                return
            if self.provider is not provider or (self.signin is not None and self.signin.running):
                return   # picked another meanwhile
            s = signin.SignIn(provider, self._signin_changed, panel=self.panel,
                              env={"BROWSER": _bombadil_browser()})
            self.signin = s
            await self._set_access("signing_in")
            self._signin_task = self._background(self._run_signin(s))
        finally:
            self._starting = False

    async def _wait_online(self):
        try:
            while self.access == "offline":
                await asyncio.sleep(OFFLINE_POLL)
                host = self.provider.signin_host
                if host is None or await asyncio.to_thread(signin.reachable, *host):
                    break
        finally:
            self._offline_task = None
        if self.access == "offline":
            await self.start_signin()

    def _signin_changed(self, s: signin.SignIn):
        if s is self.signin and s.running:
            self._background(self.broadcast(self._setup_msg()))

    async def _run_signin(self, s: signin.SignIn):
        phase = await s.run()
        self._log_line({"t": time.time(), "kind": "signin", "provider": s.provider.name, "result": phase,
                        "reason": s.reason})
        if self.signin is not s:
            return   # replaced: the provider changed meanwhile
        self.signin = None
        t = self._title()
        if phase == "done":
            self._login_gone = False
            await self._set_access("ready", f"Signed in to {t}. Ask me for anything.", "done")
            return
        if not self._login_gone and await self._still_signed_in():
            # Called off, but the login it was for is fine (a `sign in` typed while signed in,
            # which signin_asked lets through only for a login that a new one does not replace).
            await self._set_access("ready")
            return
        if phase == "timeout":
            await self._set_access("signed_out", f"The {t} sign-in timed out.", "error")
        elif phase == "cancelled":
            await self._drop_waiting()
            await self._set_access("signed_out", "Sign-in cancelled.")
        else:
            reason = (s.reason or "it did not finish").rstrip(". ")
            await self._set_access("signed_out", f"Could not sign in to {t}: {reason}.", "error")

    async def _still_signed_in(self) -> bool:
        try:
            return await asyncio.to_thread(self.provider.signed_in) is True
        except Exception:  # noqa: BLE001
            return False

    async def _drop_waiting(self):
        """Sign-in was called off: the prompts that waited for it go too."""
        dropped = [i for i, p in self.pending if not self._runnable(p)]
        self.pending = [(i, p) for i, p in self.pending if self._runnable(p)]
        for i in dropped:
            await self.broadcast({"type": "event", "kind": "unqueued", "turn": i})

    async def _end_signin(self):
        """Call off a sign-in in progress and wait until its page is put away."""
        s, task = self.signin, self._signin_task
        self.signin = None
        if s is not None and s.running:
            s.cancel()
            if task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(task), 10)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass

    def end_signin_now(self):
        """agentd is going away: so does a login it started (a codex login would hold its port)."""
        s = self.signin
        if s is not None and s.proc is not None and s.proc.returncode is None:
            try:
                os.killpg(s.proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    async def choose(self, name: str):
        """Use this provider from now on (the choice is saved), signing in to it if needed."""
        if name not in config.PROVIDERS:
            return
        await self._end_signin()
        self._login_gone = False
        model = self.provider.model if name == self.provider.name else None
        await asyncio.to_thread(config.save_user, name, model)
        if name != self.provider.name:
            self.provider = providers.get(name, model=model)
            self.session_id = None   # the other provider's conversation means nothing to this one
        self.chosen = True
        await self.check_access(start=True, announce=True)

    async def setup_action(self, action: str):
        if action.startswith("provider:"):
            await self.choose(action.split(":", 1)[1])
        elif action == "signin":
            await self.signin_asked()
        elif action == "show" and self.signin is not None:
            await self.signin.show()
        elif action == "cancel":
            await self.stop()
        elif action == "wifi":
            await self._local(launcher.Action("wifi"), "wifi")

    async def open_url(self, url: str, signin_id=None):
        """A link for the browser panel, from bombadil-browser ($BROWSER, xdg-open)."""
        if urllib.parse.urlsplit(url).scheme not in ("http", "https", "file"):
            await self.event("error", turn=None, text=f"Not opening {url[:80]!r}: only web pages and files open here.")
            return
        s = self.signin
        if s is not None and s.running and (signin_id == s.id or (
                s.url is None and signin_id is None and self.provider.signin_url_kind(url))):
            s.browser_url(url)   # the sign-in's own page: it opens and follows it
            return
        try:
            await asyncio.to_thread(self.panel.open, url)
        except Exception as e:  # noqa: BLE001
            await self.event("error", turn=None, text=f"Could not open the link: {e}")
            return
        if (self.provider.signin_url_kind(url) and self.chosen and self.access not in ("ready", "choose")
                and not (self.signin is not None and self.signin.running)):
            # Someone ran the CLI's own login in a terminal (/login): it gets its page back and
            # saves the login; agentd watches for it to land.
            self._background(self._watch_external())

    async def _watch_external(self):
        if self._external:
            return
        self._external = True
        provider = self.provider
        # A login that is known dead is still stored: only a new one (the file changing) counts.
        before = provider.login_stamp() if self._login_gone else None
        await self._set_access("signing_in")
        try:
            deadline = time.monotonic() + signin.TIMEOUT
            while self._external and not self._signin_live() and time.monotonic() < deadline:
                await asyncio.sleep(2)
                try:
                    ok = await asyncio.to_thread(provider.signed_in)
                    fresh = before is None or provider.login_stamp() != before
                except Exception:  # noqa: BLE001
                    ok, fresh = None, False
                if provider is not self.provider:
                    return   # picked another; that pick decides the state
                if ok and fresh and self._external and not self._signin_live():
                    self._login_gone = False
                    await asyncio.to_thread(self.panel.hide)
                    await self._set_access("ready", f"Signed in to {self._title()}. Ask me for anything.", "done")
                    return
            if self._external and not self._signin_live():
                await self._set_access("signed_out")
        finally:
            self._external = False

    def _signin_live(self) -> bool:
        return self.signin is not None and self.signin.running

    async def _signed_out_turn(self, turn_id: int, prompt: str, stopped: bool = False):
        """The turn found the login gone: sign in again, and run the prompt once more after
        (unless the user had stopped it: that ask is called off)."""
        if turn_id in self._retried:
            # It ran again after a sign-in that worked and got the same answer: the login is not
            # what is wrong (a 403, an API key in the environment), and another sign-in would only
            # loop. The turn already showed the CLI's own words.
            self._login_gone = False
            return
        self._login_gone = True
        if not stopped:
            self._retried.add(turn_id)
            self.pending.insert(0, (turn_id, prompt))
            # The rerun starts from the prompt as typed: tell the model what happened without it.
            self.notes = (self._turn_notes + self.notes)[-10:]
            await self.broadcast({"type": "event", "kind": "queued", "turn": turn_id, "prompt": prompt})
        await self._set_access("signed_out", f"{self._title()} signed you out.", "error")
        await self.start_signin()

    # -- turns --

    def _runnable(self, prompt: str) -> bool:
        """Can this prompt run now? A "!command" needs no AI; the rest wait until it is ready."""
        return self.access == "ready" or prompt.startswith("!")

    def _next(self) -> tuple[int, str] | None:
        if self._hold:
            return None
        for i, (_, p) in enumerate(self.pending):
            if self._runnable(p):
                return self.pending.pop(i)
        return None

    async def _worker(self):
        while True:
            while (item := self._next()) is None:
                self._wake.clear()
                await self._wake.wait()
            turn_id, prompt = item
            self.stopping = False
            self._stopped_line = ""
            self.proc = None
            self._unit = None
            self.current = turn_id
            self._signed_out = False
            self._turn_provider = self.provider
            self._turn_notes = []
            stopped = False
            try:
                await self.turn(prompt)
            except Exception as e:  # noqa: BLE001 - keep the daemon alive whatever a turn does
                await self.event("error", text=f"{type(e).__name__}: {e}")
                await self.event("turn_end", seconds=0, stopped=self.stopping)
            finally:
                stopped = self.stopping
                self.current = None
                self.narrator = None
                self._files = None
                self.plan_msg = None
                self.proc = None
                self.stopping = False
                await self.broadcast(self._status())
            if self._signed_out and self._turn_provider is self.provider:
                # (Not when "use codex" came mid-turn: that login was not the one that failed.)
                await self._signed_out_turn(turn_id, prompt, stopped)

    async def event(self, kind: str, **fields):
        fields.setdefault("turn", self.current)
        msg = {"type": "event", "kind": kind, **fields}
        log = self.turn_logs.get(fields["turn"]) if fields["turn"] is not None else None
        if log is not None and not (kind == "status" and fields.get("source") != "step"):
            _append(log, {"t": time.time(), **msg})
        await self.broadcast(msg)

    async def turn(self, prompt: str):
        shell = prompt.startswith("!")
        asked_by = self.asked_by.pop(self.current, None)
        # What the person typed. A turn a coding session asked for has none: the desk stays as it is.
        self.turn_prompt = None if asked_by else prompt
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
        n = self.turns
        _mark_begun(n)   # before snapper and the brain hear of it
        self._files = files = TurnFiles(paths.home())
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
                snap = await asyncio.to_thread(self.snaps.create, f"turn:{n}: {prompt[:60]}")
            except subprocess.CalledProcessError as e:
                await self.event("error", text=f"no undo point for this turn: snapper failed ({(e.stderr or '').strip()[-200:]})")
            if snap:
                await self.event("snapshot", number=snap.number)
        if self.stopping:
            # Stopped while the restore point was saved: the CLI never starts.
            line = self._stopped_line or "Stopped."
            await self.event("turn_end", seconds=round(time.time() - started, 1), summary="", changed=False,
                             irreversible=False, stopped=True, line=line, read=narrator.read_list())
            self._log(prompt, {"text": "", "ok": None}, snap, [], True, "", narrator.read_list(), n=n,
                      started=started, files=files)
            self._poke(kind="turn_end", n=n)
            return
        turn = providers.Turn(prompt=prompt, session_id=self.session_id)
        files.cwd = str(turn.cwd)
        self.workdir.mkdir(parents=True, exist_ok=True)
        if shell:
            cmd = ["sh", "-c", prompt[1:]]
            source = providers.Shell()
        else:
            if self.notes:
                # Open, undo and the rest happened without the model; tell it before it acts.
                turn.prompt = ("[Done by the user without you since your last turn: "
                               + "; ".join(self.notes) + "]\n\n" + prompt)
                self._turn_notes, self.notes = self.notes, []
            cmd = self.provider.command(turn, self.workdir)
            source = self.provider
        env = {**os.environ, **source.env()}
        env["BROWSER"] = _bombadil_browser()   # a link the agent opens slides the browser panel in
        if snap:
            # "undo that" runs in a turn of its own; the OS tools must roll back past this turn's
            # snapshot, not to it.
            env["BOMBADIL_TURN_SNAPSHOT"] = str(snap.number)
        # The os-mcp `desk` tool says which turn it speaks for, and where agentd listens.
        env["BOMBADIL_TURN"] = str(self.current)
        env["BOMBADIL_SOCKET"] = str(self.socket_path)
        unit = f"bombadil-turn-{os.getpid()}-{self.current}-{int(started)}"
        self._unit = unit if procs.scope_supported() else None
        self.proc = proc = await asyncio.create_subprocess_exec(
            *procs.in_scope(cmd, unit), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            # A typed "!command" shows its errors in its output, like a terminal would.
            stderr=asyncio.subprocess.STDOUT if shell else asyncio.subprocess.PIPE, cwd=str(turn.cwd), env=env,
            # Its own process group: what it leaves running stays findable for Stop.
            start_new_session=True,
            limit=64 * 1024 * 1024)  # a stream-json line can carry a whole screenshot
        # The brain names the turn's writes by its scope; until this note arrives they are
        # "a turn now running", and the row at the end says which files were its own.
        self._poke(kind="turn_start", n=n, unit=self._unit, prompt=prompt, t=started)
        if self.stopping:
            # Stop came while the CLI was being started.
            self._background(self._stop_proc(proc, self._unit))
        elif not shell:
            # Replace "Saving a restore point": the CLI is up and the next word is the provider's.
            await self._step_line(f"Waiting for {self.provider.name.capitalize()}")
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
        # "last": when the provider last sent an event; "tools": the tool calls still running (a long
        # install says nothing until it is done); "quiet": the line says the provider went quiet.
        state = {"session": None, "error": False, "last": time.monotonic(), "tools": set(), "quiet": False}

        async def pump():
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                # Any output line counts, also one that shows nothing (a thinking delta): a long think
                # is not a dead connection. Only the CLI's own notices about retrying do not.
                if source.is_progress(line):
                    state["last"] = time.monotonic()
                    if state["quiet"]:
                        state["quiet"] = False
                        if not self.stopping:
                            await self._step_line("Thinking")
                for ev in source.parse(line):
                    # Running from the tool call to its result; the turn's result ends all of it.
                    if ev["kind"] == "tool" and ev.get("id"):
                        state["tools"].add(ev["id"])
                    elif ev["kind"] == "tool_result":
                        state["tools"].discard(ev.get("id"))
                    elif ev["kind"] == "result":
                        state["tools"].clear()
                    state["session"], state["error"] = await self._on_event(
                        ev, turn, result, state["session"], state["error"])

        async def watchdog():
            while True:
                await asyncio.sleep(WATCHDOG_TICK)
                if (not state["quiet"] and not state["tools"] and not self.stopping
                        and time.monotonic() - state["last"] > NO_PROGRESS_SECS * self.provider.quiet_factor):
                    state["quiet"] = True
                    await self._step_line(f"{self.provider.name.capitalize()} is not answering; check the connection")

        reading = asyncio.create_task(pump())
        exited = asyncio.create_task(_exited(proc))
        quiet_watch = None if shell else asyncio.create_task(watchdog())
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
            if (not reported_error and result["ok"] is not True and not self.stopping
                    and (proc.returncode not in (0, None) or self._signed_out)):
                # (Ended at the first sign the login is gone, npm's codex wrapper exits 0.)
                if not shell and (self._signed_out or self._turn_provider.signed_out(err)):
                    self._signed_out = True
                    err = self._signed_out_text(err)
                await self.event("error", text=err or f"{source.name} exited with {proc.returncode}")
        finally:
            if proc.returncode is None:
                proc.kill()
                await _exited(proc)
            exited.cancel()
            reading.cancel()
            stderr.cancel()
            if quiet_watch:
                quiet_watch.cancel()
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
            self._log(prompt, result, snap, cmd, stopped, summary, narrator.read_list(), n=n,
                      unit=self._unit, started=started, files=files)
            self._poke(kind="turn_end", n=n)   # after the row: the brain reads it from the log
            self._closed_turn = self.current
            self._background(self._receipt(self.current, narrator, before, self._befores))

    async def _on_event(self, ev, turn, result, pending_session, reported_error):
        kind = ev["kind"]
        if kind == "session":
            return ev.get("session_id") or pending_session, reported_error
        if kind == "signed_out":   # the CLI's own sign that the login is gone
            if not turn.prompt.startswith("!"):
                self._signed_out = True
                proc = self.proc
                if self._turn_provider.ends_when_signed_out and proc is not None and proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            return pending_session, reported_error
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
        if self._files is not None:
            try:
                self._files.on_event(ev)
            except Exception as e:  # noqa: BLE001 - odd input costs a file in the log, never the turn
                print(f"agentd: files of {kind}: {type(e).__name__}: {e}", file=sys.stderr)
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
                if self._turn_provider in (None, self.provider):   # (not after "use codex" mid-turn)
                    self.session_id = ev.get("session_id") or pending_session or self.session_id
            else:
                reason = ev.get("terminal_reason") or ""
                text = "cancelled" if reason.startswith("aborted") else (ev.get("text") or "the turn failed")
                if not turn.prompt.startswith("!"):   # a typed command's failure is its own, not the account's
                    text = _limit_text(text)
                if turn.session_id and (ev.get("num_turns") == 0 or "no conversation found" in text.lower()):
                    self.session_id = None
                    text += " (the previous conversation is gone; the next prompt starts a new one)"
                if not turn.prompt.startswith("!") and (self._signed_out or self._turn_provider.signed_out(text)):
                    self._signed_out = True
                    text = self._signed_out_text(text)
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

    def _log(self, prompt, result, snap, cmd, stopped=False, summary="", read=None, n=None, unit=None,
             started=None, files=None):
        self._log_line({"t": time.time(), "prompt": prompt, "result": result["text"], "ok": result["ok"],
                        "snapshot": snap.number if snap else None,
                        "provider": "shell" if prompt.startswith("!") else self.provider.name,
                        "session": self.session_id, "stopped": stopped, "summary": summary,
                        # What the turn read, yours or outside.
                        "read": read or [],
                        "details": str(self.turn_logs.get(self.current, "")),
                        "n": n, "unit": unit, "started": started,
                        "files": (files.row(settled=result["ok"] is True and not stopped) if files is not None
                                  else {"wrote": [], "read": []})})

    def _log_line(self, entry: dict):
        log = paths.turns_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry) + "\n"
        with log.open("a") as f:
            if f.tell() and not _ends_a_line(log):
                line = "\n" + line   # a row cut short by a crash stays one bad line, not two
            f.write(line)

    def _poke(self, **note) -> None:
        """Tell the brain about a turn: in order, from a thread, never waited for."""
        before = self._poking

        async def send():
            if before is not None and not before.done():
                await asyncio.wait({before})
            try:
                await asyncio.wait_for(asyncio.to_thread(brain_client.notify, "note", **note), POKE_TIMEOUT)
            except Exception:  # noqa: BLE001 - the brain is a bystander; nothing it does fails a turn
                pass
        try:
            self._poking = self._background(send())
        except Exception as e:  # noqa: BLE001
            print(f"agentd: telling the brain: {type(e).__name__}: {e}", file=sys.stderr)


class TurnFiles:
    """The files a turn wrote and read, from its own tool calls: Claude's Write, Edit,
    MultiEdit, NotebookEdit and Read, and Codex's file changes. A call that failed wrote
    nothing, so it is taken back when its result says so."""

    def __init__(self, cwd):
        self.cwd = str(cwd)
        # path -> calls that may have touched it; dicts keep the order they came in.
        self.wrote: dict[str, int] = {}
        self.read: dict[str, int] = {}
        self._calls: dict[str, list[tuple[dict, str]]] = {}

    def on_event(self, ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "tool":
            args = ev.get("input") if isinstance(ev.get("input"), dict) else {}
            name = ev.get("name")
            if name in WRITE_TOOLS:
                self._add(self.wrote, args.get(WRITE_TOOLS[name]) or args.get("file_path"), ev.get("id"))
            elif name == "Read":
                self._add(self.read, args.get("file_path"), ev.get("id"))
        elif kind == "file_change":
            for change in ev.get("changes") if isinstance(ev.get("changes"), list) else []:
                if isinstance(change, dict):
                    self._add(self.wrote, change.get("path"), ev.get("id"))
        elif kind == "tool_result" and isinstance(ev.get("id"), str):
            calls = self._calls.pop(ev["id"], [])
            if ev.get("error"):
                self._take_back(calls)

    def _add(self, bucket: dict, path, call_id) -> None:
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            return
        path = os.path.normpath(os.path.join(self.cwd, path))   # an absolute path stays as it is
        if path not in bucket and len(bucket) >= MAX_TURN_FILES:
            return
        bucket[path] = bucket.get(path, 0) + 1
        if isinstance(call_id, str) and call_id:
            self._calls.setdefault(call_id, []).append((bucket, path))

    def _take_back(self, calls: list) -> None:
        for bucket, path in calls:
            if path in bucket:
                bucket[path] -= 1
                if bucket[path] <= 0:
                    del bucket[path]

    def row(self, settled: bool = True) -> dict:
        """What to log. A turn that was stopped or died has calls that never got a result,
        such as an edit queued behind a long command: those are not known to have run."""
        if not settled:
            for calls in self._calls.values():
                self._take_back(calls)
            self._calls.clear()
        return {"wrote": list(self.wrote), "read": list(self.read)}


def _limit_text(text: str) -> str:
    """A failed result that names the account's limit says so plainly, then the provider's first
    line (it holds the reset time); a bare rate limit is a busy moment, not the account."""
    low = text.lower()
    if any(w in low for w in LIMIT_WORDS):
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return LIMIT_TEXT + (f"\n{first[:200]}" if first else "")
    if any(w in low for w in RATE_WORDS):
        return RATE_TEXT
    return text


def _bombadil_browser() -> str:
    local = Path(__file__).resolve().parents[2] / "bin" / "bombadil-browser"
    return str(local) if local.exists() else "bombadil-browser"


def _action(msg: dict) -> launcher.Action | None:
    """A launcher action named by a button (Undo, Stop) rather than typed."""
    kind = str(msg.get("action", ""))
    if kind in launcher.CORE_COMMANDS or kind in launcher.BRAIN_COMMANDS or kind in launcher.UTILITY_COMMANDS:
        return launcher.Action(kind)
    return None


def _pill_words(text) -> str:
    """Words for the pill, as one line: a file name can hold a line break or a mark that
    turns text around, and what the pill shows is what the agent gets."""
    if not isinstance(text, str):
        return ""
    line = "".join(ch if ch.isprintable() else " " for ch in text)[:500]
    return line if line.strip() else ""


def _turn_number(value) -> int | None:
    """A row's "n", as the brain reads it (brain/witnesses.py)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and 0 < value < 10**9 else None


def _last_turn(log: Path) -> int:
    """The number of the last turn in turns.jsonl. Rows from before turns had numbers count
    in order, as the brain counts them; launcher actions and other kinds of row are not turns."""
    count = 0
    try:
        with log.open("rb") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    if line.lstrip().startswith(b"{"):
                        # A row cut short by a crash: the brain may have heard of that turn
                        # when it started, so its number is never used again.
                        count += 1
                    continue
                if not paths.is_turn_row(row):
                    continue
                if row.get("n") is None:
                    count += 1
                else:
                    count = max(count + 1, _turn_number(row["n"]) or 0)
    except OSError:
        return count
    return count


def _begun_file() -> Path:
    return paths.state_dir() / "turn"


def _begun() -> int:
    """The number of the last turn begun. A turn cut off by a crash, or by the reboot it
    ran, leaves no row, yet its restore point and the brain already carry its number."""
    try:
        return _turn_number(int(_begun_file().read_text().strip())) or 0
    except (OSError, ValueError):
        return 0


def _mark_begun(n: int) -> None:
    path = _begun_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".turn.tmp")
        tmp.write_text(f"{n}\n")
        os.replace(tmp, path)
    except OSError as e:   # the turn runs anyway; only a crash in it could reuse its number
        print(f"agentd: keeping the turn number: {e}", file=sys.stderr)


def _ends_a_line(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"
    except OSError:
        return True


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
    # Picked means the user chose (first boot asks in the pill) or the environment says so.
    chosen = cfg.configured or bool(os.environ.get("BOMBADIL_PROVIDER"))
    daemon = AgentD(provider, snaps, explain=cfg.explain, chosen=chosen, auto_signin=True)
    print(f"agentd: {provider.name} on {daemon.socket_path}", file=sys.stderr)

    async def run():
        serving = asyncio.ensure_future(daemon.serve())
        # The unit file names behind "what does networkmanager need": a slow read, so before the first ask.
        asyncio.get_running_loop().run_in_executor(None, sysmap.warm_unit_names)

        def on_term():
            # A login goes first. Then the bar's connection: Python 3.12+ waits for every client
            # before a closing server lets go, and the bar never hangs up on its own.
            daemon.end_signin_now()
            for w in list(daemon.clients):
                w.close()
            serving.cancel()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, on_term)
        try:
            await serving
        except asyncio.CancelledError:
            pass
        finally:
            daemon.end_signin_now()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        daemon.end_signin_now()
    return 0


class _NoSnapshots(snapshots.Snapshots):
    available = False

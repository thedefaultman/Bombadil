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
                   {"type": "summon"}                   ask the bar to take the keyboard (Super)
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
                    "local"|"plan", "turn": n, ...}
                   {"type": "status", "busy": bool, "provider": "...", "turns": n, "queue": [...], ...}
                   {"type": "entries", "entries": [...]}  names the pill can complete and open
                   {"type": "summon"}
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
A "step" status also says what the turn has changed so far: "touched": {"package": 1, "file": 2}
(kinds package, service, file, app; only those there are) and "touched_text": "1 package and 2
files so far" ("" when nothing).
"plan" events are the agent's own step list, the whole table on every change:
{"steps": [{"id": "1", "subject": "Install ffmpeg", "active": "Installing ffmpeg" | null, "status":
"pending" | "in_progress" | "completed"}]}. A step the agent has only just made has "id": null and
comes last. When several are in progress the last one is the current step.
turn_start carries "asked_by": "builder" when the turn was started for a coding session, else null.
turn_end carries how the turn ended: {"seconds", "summary": "Installed ffmpeg.", "changed",
"irreversible", "stopped", "line": "Stopped while installing ffmpeg."}.

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
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, launcher, narrate, paths, procs, providers, snapshots, watch
from .desk import Desk, asked_for_desk
from .jobs import JobError, Jobs, ending, started_text

# Provider events that only feed the live line; clients get the "status" events made from them.
LINE_ONLY = {"tool_start", "tool_input", "text_delta", "thinking"}
MAX_OUTPUT = 16_000   # characters of one command's output kept in events and the turn's log
# A client that stops reading (a hung bar) is dropped rather than allowed to hold up the
# others: its messages wait in a queue of this many, each write gets this long.
CLIENT_BACKLOG = 10_000
SEND_TIMEOUT = 5.0
# After a turn's process exits, how long its output may take to drain. Longer means a job it
# left in the background (`!server &`) still holds the pipe; the turn ends without it.
OUTPUT_GRACE = 1.0
# Changes to the desk that come close together go out as the state they end in.
DESK_DEBOUNCE = 0.03
# The same for the jobs table, which is also looked at this often (seconds) while it has anything in it.
JOBS_DEBOUNCE = 0.03
JOBS_POLL = 2.0


class AgentD:
    def __init__(self, provider: providers.Provider, snaps: snapshots.Snapshots | None = None,
                 socket_path: Path | None = None, launch: launcher.Launcher | None = None,
                 stopper: procs.Stopper | None = None, desk: Desk | None = None, jobs: Jobs | None = None):
        self.provider = provider
        self.snaps = snaps or snapshots.Snapshots()
        self.socket_path = socket_path or paths.socket_path()
        # One desk: the launcher's words, the shell and the agent's tool all change this one.
        self.desk = desk or getattr(launch, "desk_state", None) or Desk().load()
        self.launcher = launch or launcher.Launcher(snaps=self.snaps, desk=self.desk)
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
        elif t == "summon":
            await self.broadcast({"type": "summon"})
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
            return await asyncio.to_thread(launcher.match, text)
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
        if self.current is None or msg.get("turn") != self.current:
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
        self.turn_prompt = prompt
        started = time.time()
        self.narrator = narrator = narrate.Narrator()
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
                             irreversible=False, stopped=True, line=line)
            self._log(prompt, {"text": "", "ok": None}, snap, [], True, "")
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
            await self.event("turn_end", seconds=round(time.time() - started, 1), summary=summary,
                             changed=bool(narrator.done), irreversible=narrator.irreversible,
                             stopped=stopped, line=line)
            self._log(prompt, result, snap, cmd, stopped, summary)

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
        await self.event(kind, **{k: v for k, v in ev.items() if k != "kind"})
        return pending_session, reported_error

    def _log(self, prompt, result, snap, cmd, stopped=False, summary=""):
        self._log_line({"t": time.time(), "prompt": prompt, "result": result["text"], "ok": result["ok"],
                        "snapshot": snap.number if snap else None,
                        "provider": "shell" if prompt.startswith("!") else self.provider.name,
                        "session": self.session_id, "stopped": stopped, "summary": summary,
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
    daemon = AgentD(provider, snaps)
    print(f"agentd: {provider.name} on {daemon.socket_path}", file=sys.stderr)
    try:
        asyncio.run(daemon.serve())
    except KeyboardInterrupt:
        pass
    return 0


class _NoSnapshots(snapshots.Snapshots):
    available = False

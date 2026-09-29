"""agentd: the session daemon. The agent *is* the session; this process keeps it alive.

Runs as the user, started by Hyprland at login. Listens on a Unix socket for newline
delimited JSON. Any number of clients (the Quickshell bar, `bombadil ask`, a generated
app) can connect; every event is broadcast to all of them.

Client -> daemon:  {"type": "prompt", "text": "..."}   a turn, or a launcher word ("browser", "undo")
                   {"type": "stop"}                     end the running turn and all it started
                   {"type": "cancel"}                   the same as stop
                   {"type": "unqueue", "turn": n}       drop a prompt still waiting its turn
                   {"type": "local", "action": "undo"}  a launcher action by name (the Undo button)
                   {"type": "details", "turn": n}       show a turn's commands and output (drawer);
                                                        again while it shows closes it
                   {"type": "close_details"}            put the drawer away (Esc in the pill)
                   {"type": "summon"}                   ask the bar to take the keyboard (Super)
                   {"type": "setup_action", "id": "provider:codex"|"signin"|"show"|"cancel"|"wifi"}
                                                        a chip under the setup line (see below)
                   {"type": "signin", "provider": "codex"?}  sign in (again), after switching provider
                   {"type": "open_url", "url": "...", "signin": id?}  a link for the browser panel
                                                        (bombadil-browser: $BROWSER and xdg-open)
                   {"type": "status"}
Daemon -> clients: {"type": "event", "kind": "turn_start"|"snapshot"|"status"|"text"|"tool"|
                    "tool_result"|"file_change"|"result"|"error"|"turn_end"|"queued"|"unqueued"|
                    "local", "turn": n, ...}
                   {"type": "status", "busy": bool, "provider": "...", "turns": n, "queue": [...], ...}
                   {"type": "entries", "entries": [...]}  names the pill can complete and open
                   {"type": "setup", "state": ..., "line": ..., "actions": [...]}  see below
                   {"type": "summon"}

"status" events are the live line above the pill: {"text": "Installing ffmpeg", "risk": null |
"system" | "irreversible", "command": "sudo pacman -S ffmpeg" | null, "source": "step" | "agent"}.
turn_end carries how the turn ended: {"seconds", "summary": "Installed ffmpeg.", "changed",
"irreversible", "stopped", "line": "Stopped while installing ffmpeg."}.

"setup" is whether the machine can talk to its AI: state "choose" (no provider picked yet: the
pill offers Claude and Codex), "checking", "signed_out", "offline" (no way to reach the sign-in
page), "signing_in" (the CLI's login runs and its page is in the browser panel, signin.py) or
"ready". Prompts wait in the queue until it is ready, and a turn that finds the login gone
signs in again and then runs once more. `line` is what the pill says about it, `tone` is
step, ask, error or done, and `actions` are its chips: [{"id", "label", "style"}].

Every turn: say turn_start, snapshot the system (undo point), run one provider CLI turn with
the os-mcp server attached in its own scope, stream its events, log the turn. Launcher words
(apps, panels, undo, stop) are handled here at once and never wait for the model.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from . import browser, config, launcher, narrate, paths, procs, providers, signin, snapshots, watch

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
OFFLINE_POLL = 5.0   # while there is no way to the sign-in page, look again this often
READY_LINE_SECONDS = 120.0   # how long "Signed in to Claude" is worth saying


class AgentD:
    def __init__(self, provider: providers.Provider, snaps: snapshots.Snapshots | None = None,
                 socket_path: Path | None = None, launch: launcher.Launcher | None = None,
                 stopper: procs.Stopper | None = None, chosen: bool = True, auto_signin: bool = False,
                 panel=None):
        self.provider = provider
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
        self.notes: list[str] = []                 # what happened without the model since its last turn
        self.turn_logs: dict[int, Path] = {}
        self._entries_key = None
        self._stopped_line = ""
        self._unit: str | None = None               # the running turn's systemd scope
        self._hold = 0                              # undo/restart/shutdown running: start no turn
        self._exclusive = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()      # local actions and stops running beside the reader
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
        self.panel = panel or browser.Panel()

    # -- socket --

    async def serve(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        # Probe for systemd scopes now, not on the first Enter.
        await asyncio.to_thread(procs.scope_supported)
        server = await asyncio.start_unix_server(self._client, path=str(self.socket_path))
        worker = asyncio.create_task(self._worker())
        self._background(self.check_access(start=self.auto_signin))
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
        elif t == "summon":
            await self.broadcast({"type": "summon"})
        elif t == "status":
            await self._send(writer, self._status())
        elif t == "setup_action":
            self._background(self.setup_action(str(msg.get("id", ""))))
        elif t == "signin":
            name = msg.get("provider")
            self._background(self.choose(str(name)) if name and (name != self.provider.name or not self.chosen)
                             else self.start_signin())
        elif t == "open_url":
            self._background(self.open_url(str(msg.get("url", "")), msg.get("signin")))

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

    # -- things that never wait for the model --

    async def local(self, action: launcher.Action, typed: str):
        if action.kind in ("signin", "provider"):
            if action.kind == "signin":
                await self.start_signin()
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
            # Called off, but the login it was for is fine (`sign in` typed while signed in).
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
            await self.start_signin()
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
        self._login_gone = True
        if turn_id not in self._retried and not stopped:
            self._retried.add(turn_id)
            self.pending.insert(0, (turn_id, prompt))
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
                self.proc = None
                self.stopping = False
                await self.broadcast(self._status())
            if self._signed_out:
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
        started = time.time()
        self.narrator = narrator = narrate.Narrator()
        log = paths.state_dir() / "turns" / f"{int(started * 1000)}-{self.current}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.turn_logs[self.current] = log
        for old in sorted(self.turn_logs)[:-50]:
            self.turn_logs.pop(old, None)
        # Something true on screen before snapper, which can take a second.
        await self.event("turn_start", prompt=prompt, snapshot=None)
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
        env = dict(os.environ)
        env["BROWSER"] = _bombadil_browser()   # a link the agent opens slides the browser panel in
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
            if (not reported_error and result["ok"] is not True and not self.stopping
                    and (proc.returncode not in (0, None) or self._signed_out)):
                # (Ended at the first sign the login is gone, npm's codex wrapper exits 0.)
                if not shell and (self._signed_out or self.provider.signed_out(err)):
                    self._signed_out = True
                    err = f"{self._title()} signed you out."
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
        if kind == "signed_out":   # the CLI's own sign that the login is gone
            if not turn.prompt.startswith("!"):
                self._signed_out = True
                proc = self.proc
                if self.provider.ends_when_signed_out and proc is not None and proc.returncode is None:
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
            await self.event("status", **line)
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
                if turn.session_id and (ev.get("num_turns") == 0 or "no conversation found" in text.lower()):
                    self.session_id = None
                    text += " (the previous conversation is gone; the next prompt starts a new one)"
                if not turn.prompt.startswith("!") and (self._signed_out or self.provider.signed_out(text)):
                    self._signed_out = True
                    text = f"{self._title()} signed you out."
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


def _bombadil_browser() -> str:
    local = Path(__file__).resolve().parents[2] / "bin" / "bombadil-browser"
    return str(local) if local.exists() else "bombadil-browser"


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
    # Picked means the user chose (first boot asks in the pill) or the environment says so.
    chosen = cfg.configured or bool(os.environ.get("BOMBADIL_PROVIDER"))
    daemon = AgentD(provider, snaps, chosen=chosen, auto_signin=True)
    print(f"agentd: {provider.name} on {daemon.socket_path}", file=sys.stderr)

    async def run():
        serving = asyncio.ensure_future(daemon.serve())
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, serving.cancel)
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

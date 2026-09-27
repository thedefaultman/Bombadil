"""agentd: the session daemon. The agent *is* the session; this process keeps it alive.

Runs as the user, started by Hyprland at login. Listens on a Unix socket for newline
delimited JSON. Any number of clients (the Quickshell bar, `bombadil ask`, a generated
app) can connect; every event is broadcast to all of them.

Client -> daemon:  {"type": "prompt", "text": "..."}   a turn, or a launcher word ("browser", "undo")
                   {"type": "stop"}                     end the running turn and all it started
                   {"type": "cancel"}                   the same as stop
                   {"type": "unqueue", "turn": n}       drop a prompt still waiting its turn
                   {"type": "local", "action": "undo"}  a launcher action by name (the Undo button)
                   {"type": "details", "turn": n}       show a turn's commands and output (drawer)
                   {"type": "summon"}                   ask the bar to take the keyboard (Super)
                   {"type": "status"}
Daemon -> clients: {"type": "event", "kind": "turn_start"|"snapshot"|"status"|"text"|"tool"|
                    "tool_result"|"file_change"|"result"|"error"|"turn_end"|"queued"|"unqueued"|
                    "local", "turn": n, ...}
                   {"type": "status", "busy": bool, "provider": "...", "turns": n, "queue": [...], ...}
                   {"type": "entries", "entries": [...]}  names the pill can complete and open
                   {"type": "summon"}

"status" events are the live line above the pill: {"text": "Installing ffmpeg", "risk": null |
"system" | "irreversible", "command": "sudo pacman -S ffmpeg" | null, "source": "step" | "agent"}.
turn_end carries how the turn ended: {"seconds", "summary": "Installed ffmpeg.", "changed",
"irreversible", "stopped", "line": "Stopped while installing ffmpeg."}.

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

# Provider events that only feed the live line; clients get the "status" events made from them.
LINE_ONLY = {"tool_start", "tool_input", "text_delta", "thinking"}
MAX_OUTPUT = 16_000   # characters of one command's output kept in events and the turn's log


class AgentD:
    def __init__(self, provider: providers.Provider, snaps: snapshots.Snapshots | None = None,
                 socket_path: Path | None = None, launch: launcher.Launcher | None = None,
                 stopper: procs.Stopper | None = None):
        self.provider = provider
        self.snaps = snaps or snapshots.Snapshots()
        self.socket_path = socket_path or paths.socket_path()
        self.launcher = launch or launcher.Launcher(snaps=self.snaps)
        self.stopper = stopper or procs.Stopper()
        self.clients: set[asyncio.StreamWriter] = set()
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
        self.clients.add(writer)
        await self._send(writer, self._status())
        await self._send(writer, await self._entries_msg())
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict):
                    await self.handle(msg, writer)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    async def handle(self, msg: dict, writer: asyncio.StreamWriter):
        t = msg.get("type")
        if t == "prompt":
            text = str(msg.get("text", "")).strip()
            if not text:
                await self._send(writer, {"type": "event", "kind": "error", "text": "empty prompt"})
                return
            action = await asyncio.to_thread(launcher.match, text)
            if action is not None:
                await self._send(writer, {"type": "local", "action": action.kind})
                await self.local(action, text)
                return
            self.next_id += 1
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
                await self.local(action, str(msg.get("action")))
        elif t == "details":
            await self.details(msg.get("turn"))
        elif t == "summon":
            await self.broadcast({"type": "summon"})
        elif t == "status":
            await self._send(writer, self._status())

    def _status(self) -> dict:
        return {"type": "status", "busy": self.proc is not None and self.proc.returncode is None,
                "provider": self.provider.name, "turns": self.turns,
                "snapshots": self.snaps.available, "queued": len(self.pending),
                "turn": self.current, "queue": [{"turn": i, "prompt": p} for i, p in self.pending]}

    async def _entries_msg(self) -> dict:
        return {"type": "entries", "entries": await asyncio.to_thread(launcher.entries)}

    async def _watch_apps(self):
        """Tell the bar when an app appears or goes, so it can complete the new name."""
        while True:
            await asyncio.sleep(3)
            try:
                key = await asyncio.to_thread(_apps_key)
            except OSError:
                continue
            if key != self._entries_key:
                first = self._entries_key is None
                self._entries_key = key
                if not first:
                    await self.broadcast(await self._entries_msg())

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):
        try:
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            self.clients.discard(writer)

    async def broadcast(self, msg: dict):
        for w in list(self.clients):
            await self._send(w, msg)

    # -- things that never wait for the model --

    async def local(self, action: launcher.Action, typed: str):
        if action.kind == "stop":
            if not await self.stop():
                await self.event("local", turn=None, action="stop", phase="done", ok=True,
                                 text="Nothing is running.")
            return
        if action.kind in ("undo", "restart", "shutdown") and self._busy():
            # Undo takes back the turn that is running too, so end it first.
            await self.stop()
            while self.proc is not None and self.proc.returncode is None:
                await asyncio.sleep(0.05)
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
            await asyncio.to_thread(self.launcher.details, argv)
        except Exception as e:  # noqa: BLE001
            await self.event("local", turn=None, action="details", phase="done", ok=False,
                             text=f"Could not show the details: {e}")

    def _busy(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def stop(self) -> bool:
        """End the running turn and everything it started. False when nothing runs."""
        proc = self.proc
        if proc is None or proc.returncode is not None or self.stopping:
            return self.stopping
        self.stopping = True
        line = self.narrator.stopped_line() if self.narrator else "Stopped."
        await self.event("status", text="Stopping", risk=None, command=None, source="step")
        try:
            await asyncio.to_thread(self.stopper.stop, proc.pid)
        except Exception as e:  # noqa: BLE001 - never leave a turn running because stop broke
            await self.event("error", text=f"stop: {e}")
            proc.kill()
        self._stopped_line = line
        return True

    # -- turns --

    async def _worker(self):
        while True:
            while not self.pending:
                self._wake.clear()
                await self._wake.wait()
            turn_id, prompt = self.pending.pop(0)
            self.current = turn_id
            try:
                await self.turn(prompt)
            except Exception as e:  # noqa: BLE001 - keep the daemon alive whatever a turn does
                await self.event("error", text=f"{type(e).__name__}: {e}")
                await self.event("turn_end", seconds=0)
            finally:
                self.current = None
                self.narrator = None

    async def event(self, kind: str, **fields):
        fields.setdefault("turn", self.current)
        msg = {"type": "event", "kind": kind, **fields}
        log = self.turn_logs.get(fields["turn"]) if fields["turn"] is not None else None
        if log is not None and not (kind == "status" and fields.get("source") != "step"):
            _append(log, {"t": time.time(), **msg})
        await self.broadcast(msg)

    async def turn(self, prompt: str):
        shell = prompt.startswith("!")
        if not shell and not self.provider.installed:
            await self.event("error", text=f"{self.provider.binary} is not installed yet: press Super+Return "
                                           "and run bombadil-setup")
            await self.event("turn_end", seconds=0)
            return
        self.turns += 1
        started = time.time()
        self.narrator = narrator = narrate.Narrator()
        self.stopping = False
        self._stopped_line = ""
        log = paths.state_dir() / "turns" / f"{int(started * 1000)}-{self.current}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.turn_logs[self.current] = log
        for old in sorted(self.turn_logs)[:-50]:
            self.turn_logs.pop(old, None)
        # Something true on screen before snapper, which can take a second.
        await self.event("turn_start", prompt=prompt, snapshot=None)
        await self.broadcast(self._status())
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
        if snap:
            # "undo that" runs in a turn of its own; the OS tools must roll back past this turn's
            # snapshot, not to it.
            env["BOMBADIL_TURN_SNAPSHOT"] = str(snap.number)
        unit = f"bombadil-turn-{os.getpid()}-{self.current}-{int(started)}"
        self.proc = proc = await asyncio.create_subprocess_exec(
            *procs.in_scope(cmd, unit), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            # A typed "!command" shows its errors in its output, like a terminal would.
            stderr=asyncio.subprocess.STDOUT if shell else asyncio.subprocess.PIPE, cwd=str(turn.cwd), env=env,
            limit=64 * 1024 * 1024)  # a stream-json line can carry a whole screenshot
        if not shell:
            proc.stdin.write(turn.prompt.encode())
            await proc.stdin.drain()
        proc.stdin.close()
        stderr = asyncio.create_task(proc.stderr.read() if proc.stderr else asyncio.sleep(0, b""))
        await self.broadcast(self._status())

        result = {"text": "", "ok": None}
        pending_session = None
        reported_error = False
        if shell:
            # A typed command is one step: its words, its mark and its exact command.
            await self._on_event({"kind": "tool", "name": "Bash", "input": {"command": prompt[1:]}, "id": "shell"},
                                 turn, result, None, False)
        try:
            async for raw in proc.stdout:
                for ev in source.parse(raw.decode(errors="replace")):
                    pending_session, reported_error = await self._on_event(
                        ev, turn, result, pending_session, reported_error)
            await proc.wait()
            source.returncode = proc.returncode
            for ev in source.finish():
                pending_session, reported_error = await self._on_event(
                    ev, turn, result, pending_session, reported_error)
            err = ((await stderr) or b"").decode(errors="replace").strip()[-2000:]
            if (proc.returncode not in (0, None) and not reported_error and result["ok"] is not True
                    and not self.stopping):
                await self.event("error", text=err or f"{source.name} exited with {proc.returncode}")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
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
            self.stopping = False
            self._log(prompt, result, snap, cmd, stopped, summary)
            await self.broadcast(self._status())

    async def _on_event(self, ev, turn, result, pending_session, reported_error):
        kind = ev["kind"]
        if kind == "session":
            return ev.get("session_id") or pending_session, reported_error
        line = self.narrator.on_event(ev) if self.narrator else None
        if line is not None and not self.stopping:
            await self.event("status", **line)
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

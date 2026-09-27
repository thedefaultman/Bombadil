"""agentd: the session daemon. The agent *is* the session; this process keeps it alive.

Runs as the user, started by Hyprland at login. Listens on a Unix socket for newline
delimited JSON. Any number of clients (the Quickshell bar, `bombadil ask`, a generated
app) can connect; every event is broadcast to all of them.

Client -> daemon:  {"type": "prompt", "text": "..."}
                   {"type": "cancel"}
                   {"type": "status"}
Daemon -> clients: {"type": "event", "kind": "turn_start"|"text"|"tool"|"result"|"error"|"turn_end", ...}
                   {"type": "status", "busy": bool, "provider": "...", "turns": n}

Every turn: snapshot the system (undo point), run one provider CLI turn with the
os-mcp server attached, stream its events, log the turn.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import config, paths, providers, snapshots


class AgentD:
    def __init__(self, provider: providers.Provider, snaps: snapshots.Snapshots | None = None,
                 socket_path: Path | None = None):
        self.provider = provider
        self.snaps = snaps or snapshots.Snapshots()
        self.socket_path = socket_path or paths.socket_path()
        self.clients: set[asyncio.StreamWriter] = set()
        self.session_id: str | None = None
        self.turns = 0
        self.proc: asyncio.subprocess.Process | None = None
        self.queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self.next_id = 0
        self.current: int | None = None
        self.workdir = paths.state_dir()

    # -- socket --

    async def serve(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        server = await asyncio.start_unix_server(self._client, path=str(self.socket_path))
        worker = asyncio.create_task(self._worker())
        async with server:
            await server.serve_forever()
        worker.cancel()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.clients.add(writer)
        await self._send(writer, self._status())
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
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
            self.next_id += 1
            # The id lets a client (bombadil ask) follow its own turn among everyone's events.
            await self._send(writer, {"type": "queued", "turn": self.next_id})
            await self.queue.put((self.next_id, text))
        elif t == "cancel" and self.proc and self.proc.returncode is None:
            self.proc.send_signal(signal.SIGINT)
        elif t == "status":
            await self._send(writer, self._status())

    def _status(self) -> dict:
        return {"type": "status", "busy": self.proc is not None and self.proc.returncode is None,
                "provider": self.provider.name, "turns": self.turns,
                "snapshots": self.snaps.available, "queued": self.queue.qsize()}

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):
        try:
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            self.clients.discard(writer)

    async def broadcast(self, msg: dict):
        for w in list(self.clients):
            await self._send(w, msg)

    # -- turns --

    async def _worker(self):
        while True:
            turn_id, prompt = await self.queue.get()
            self.current = turn_id
            try:
                await self.turn(prompt)
            except Exception as e:  # noqa: BLE001 - keep the daemon alive whatever a turn does
                await self.event("error", text=f"{type(e).__name__}: {e}")
                await self.event("turn_end", seconds=0)
            finally:
                self.current = None

    async def event(self, kind: str, **fields):
        await self.broadcast({"type": "event", "kind": kind, "turn": self.current, **fields})

    async def turn(self, prompt: str):
        if not self.provider.installed:
            await self.event("error", text=f"{self.provider.binary} is not installed yet: press Super+Return "
                                           "and run bombadil-setup")
            await self.event("turn_end", seconds=0)
            return
        self.turns += 1
        started = time.time()
        snap = None
        if self.snaps.available:
            try:
                snap = self.snaps.create(f"turn:{self.turns}: {prompt[:60]}")
            except subprocess.CalledProcessError as e:
                await self.event("error", text=f"no undo point for this turn: snapper failed ({(e.stderr or '').strip()[-200:]})")
        await self.event("turn_start", prompt=prompt, snapshot=snap.number if snap else None)
        turn = providers.Turn(prompt=prompt, session_id=self.session_id)
        self.workdir.mkdir(parents=True, exist_ok=True)
        cmd = self.provider.command(turn, self.workdir)
        env = dict(os.environ)
        if snap:
            # "undo that" runs in a turn of its own; the OS tools must roll back past this turn's
            # snapshot, not to it.
            env["BOMBADIL_TURN_SNAPSHOT"] = str(snap.number)
        self.proc = proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(turn.cwd), env=env,
            limit=64 * 1024 * 1024)  # a stream-json line can carry a whole screenshot
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        stderr = asyncio.create_task(proc.stderr.read())
        await self.broadcast(self._status())

        result = {"text": "", "ok": None}
        pending_session = None
        reported_error = False
        try:
            async for raw in proc.stdout:
                for ev in self.provider.parse(raw.decode(errors="replace")):
                    pending_session, reported_error = await self._on_event(
                        ev, turn, result, pending_session, reported_error)
            for ev in self.provider.finish():
                pending_session, reported_error = await self._on_event(
                    ev, turn, result, pending_session, reported_error)
            await proc.wait()
            err = (await stderr).decode(errors="replace").strip()[-2000:]
            if proc.returncode not in (0, None) and not reported_error and result["ok"] is not True:
                await self.event("error", text=err or f"{self.provider.name} exited with {proc.returncode}")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            stderr.cancel()
            await self.event("turn_end", seconds=round(time.time() - started, 1))
            self._log(prompt, result, snap, cmd)
            await self.broadcast(self._status())

    async def _on_event(self, ev, turn, result, pending_session, reported_error):
        kind = ev["kind"]
        if kind == "session":
            return ev.get("session_id") or pending_session, reported_error
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
                await self.event("error", text=text)
                reported_error = True
        elif kind == "error":
            reported_error = True
        await self.event(kind, **{k: v for k, v in ev.items() if k != "kind"})
        return pending_session, reported_error

    def _log(self, prompt, result, snap, cmd):
        paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
        with paths.turns_log().open("a") as f:
            f.write(json.dumps({"t": time.time(), "prompt": prompt, "result": result["text"], "ok": result["ok"],
                                "snapshot": snap.number if snap else None,
                                "provider": self.provider.name, "session": self.session_id}) + "\n")


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

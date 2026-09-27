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
        self.queue: asyncio.Queue[str] = asyncio.Queue()
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
        if t == "prompt" and msg.get("text", "").strip():
            await self.queue.put(msg["text"].strip())
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
            prompt = await self.queue.get()
            try:
                await self.turn(prompt)
            except Exception as e:  # noqa: BLE001 - keep the daemon alive whatever a turn does
                await self.broadcast({"type": "event", "kind": "error", "text": f"{type(e).__name__}: {e}"})

    async def turn(self, prompt: str):
        if not self.provider.installed:
            await self.broadcast({"type": "event", "kind": "error",
                                  "text": f"{self.provider.binary} is not installed yet: press Super+Return "
                                          "and run bombadil-setup"})
            await self.broadcast({"type": "event", "kind": "turn_end", "seconds": 0})
            return
        self.turns += 1
        started = time.time()
        snap = self.snaps.create(f"turn:{self.turns}: {prompt[:60]}") if self.snaps.available else None
        await self.broadcast({"type": "event", "kind": "turn_start", "prompt": prompt,
                              "snapshot": snap.number if snap else None})
        turn = providers.Turn(prompt=prompt, session_id=self.session_id)
        self.workdir.mkdir(parents=True, exist_ok=True)
        cmd = self.provider.command(turn, self.workdir)
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(turn.cwd))
        if self.provider.name == "fake":
            self.proc.stdin.write(prompt.encode())
        self.proc.stdin.close()
        await self.broadcast(self._status())

        lines = []
        async for raw in self.proc.stdout:
            lines.append(raw.decode(errors="replace"))
        events = list(self.provider.events(iter(lines)))
        result_text = ""
        for ev in events:
            if ev["kind"] == "session" and ev.get("session_id"):
                self.session_id = ev["session_id"]
                continue
            if ev["kind"] == "result":
                result_text = ev.get("text", "")
                if ev.get("session_id"):
                    self.session_id = ev["session_id"]
            await self.broadcast({"type": "event", **ev})
        await self.proc.wait()
        if self.proc.returncode not in (0, None) and not result_text:
            err = (await self.proc.stderr.read()).decode(errors="replace").strip()[-2000:]
            await self.broadcast({"type": "event", "kind": "error",
                                  "text": err or f"{self.provider.name} exited with {self.proc.returncode}"})
        await self.broadcast({"type": "event", "kind": "turn_end", "seconds": round(time.time() - started, 1)})
        self._log(prompt, result_text, snap, cmd)
        await self.broadcast(self._status())

    def _log(self, prompt, result, snap, cmd):
        paths.turns_log().parent.mkdir(parents=True, exist_ok=True)
        with paths.turns_log().open("a") as f:
            f.write(json.dumps({"t": time.time(), "prompt": prompt, "result": result,
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

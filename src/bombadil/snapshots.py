"""Undo for a system with no guard rails.

Before every agent turn that may touch the system we take a btrfs snapshot through
snapper; "undo that" rolls the root subvolume back to the snapshot taken before the
turn. When snapper is not installed (dev machines, tests) everything degrades to
no-ops that report `available = False`.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SNAPPER_CONFIGS = Path("/etc/snapper/configs")


@dataclass
class Snapshot:
    number: int
    description: str


def label_from_hook(text: str, where: str = "remote") -> str:
    """The description of a restore point taken by a Claude Code UserPromptSubmit hook, which is handed
    {"prompt": "..."} on stdin: "turn: remote: <the start of the prompt>". The "turn:" is what undo looks for."""
    try:
        prompt = str(json.loads(text).get("prompt", ""))
    except (ValueError, AttributeError):
        prompt = ""
    prompt = " ".join(prompt.split())
    return f"turn: {where}: {prompt[:60]}".rstrip(": ") if prompt else f"turn: {where}"


class Snapshots:
    def __init__(self, config_name: str = "root", runner=subprocess.run, configs_dir: Path = SNAPPER_CONFIGS):
        self.config_name = config_name
        self._run = runner
        self._configs_dir = configs_dir

    @property
    def available(self) -> bool:
        # The live ISO has snapper but no btrfs root and no config; only an installed system has undo.
        return shutil.which("snapper") is not None and (self._configs_dir / self.config_name).exists()

    def _snapper(self, *args: str) -> str:
        cmd = ["sudo", "snapper", "-c", self.config_name, "--jsonout", *args]
        return self._run(cmd, check=True, capture_output=True, text=True).stdout

    def create(self, description: str) -> Snapshot | None:
        if not self.available:
            return None
        out = self._snapper("create", "--print-number", "--description", description)
        return Snapshot(int(out.strip()), description)

    def list(self, limit: int = 20) -> list[Snapshot]:
        if not self.available:
            return []
        data = json.loads(self._snapper("list"))
        rows = data.get(self.config_name, [])
        snaps = [Snapshot(r["number"], r.get("description", "")) for r in rows if r["number"] != 0]
        return snaps[-limit:]

    def rollback(self, number: int) -> bool:
        """Make snapshot `number` the root subvolume. Takes effect on next boot.

        bombadil-rollback swaps the snapshot in under the name @ (the installer mounts / by
        name), which works with any bootloader, unlike `snapper rollback`'s default-subvolume
        scheme."""
        if not self.available:
            return False
        # Captured: this runs inside the MCP server, whose stdout is the JSON-RPC transport.
        self._run(["sudo", "bombadil-rollback", str(number)], check=True, capture_output=True, text=True)
        return True

    def undo_last_turn(self, before: int | None = None) -> Snapshot | None:
        """Roll back to the snapshot taken before the last agent turn.

        Inside a turn ("undo that"), agentd has already snapshotted the state the user wants
        undone and passes that snapshot's number as BOMBADIL_TURN_SNAPSHOT; skip it and newer."""
        if before is None and os.environ.get("BOMBADIL_TURN_SNAPSHOT", "").isdigit():
            before = int(os.environ["BOMBADIL_TURN_SNAPSHOT"])
        for snap in reversed(self.list()):
            if before is not None and snap.number >= before:
                continue
            if snap.description.startswith("turn:"):
                self.rollback(snap.number)
                return snap
        return None

"""Undo for a system with no guard rails.

Before every agent turn that may touch the system we take a btrfs snapshot through
snapper; "undo that" rolls the root subvolume back to the snapshot taken before the
turn. When snapper is not installed (dev machines, tests) everything degrades to
no-ops that report `available = False`.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Snapshot:
    number: int
    description: str


class Snapshots:
    def __init__(self, config_name: str = "root", runner=subprocess.run):
        self.config_name = config_name
        self._run = runner

    @property
    def available(self) -> bool:
        return shutil.which("snapper") is not None

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
        """Roll the root subvolume back to `number`. Takes effect on next boot."""
        if not self.available:
            return False
        self._run(["sudo", "snapper", "-c", self.config_name, "rollback", str(number)], check=True)
        return True

    def undo_last_turn(self) -> Snapshot | None:
        for snap in reversed(self.list()):
            if snap.description.startswith("turn:"):
                self.rollback(snap.number)
                return snap
        return None

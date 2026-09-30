"""Where Bombadil keeps things. Every location can be overridden by env var for development."""

import os
from pathlib import Path


def _env_path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value).expanduser() if value else default


def home() -> Path:
    return Path.home()


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return _env_path("BOMBADIL_RUNTIME", Path(base) / "bombadil")


def socket_path() -> Path:
    return _env_path("BOMBADIL_SOCKET", runtime_dir() / "agentd.sock")


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(home() / ".local" / "state")
    return _env_path("BOMBADIL_STATE", Path(base) / "bombadil")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(home() / ".config")
    return _env_path("BOMBADIL_CONFIG", Path(base) / "bombadil")


def apps_dir() -> Path:
    return _env_path("BOMBADIL_APPS", home() / "Apps")


def share_dir() -> Path:
    return _env_path("BOMBADIL_SHARE", Path("/usr/share/bombadil"))


def turns_log() -> Path:
    return state_dir() / "turns.jsonl"


def loop_dir() -> Path:
    """What the self-improvement loop keeps: counts, findings, reports, and what the bar and agentd
    report about themselves. Outside the restore points, so an undo never rewinds a count."""
    return _env_path("BOMBADIL_LOOP", state_dir() / "loop")


def loop_db() -> Path:
    return loop_dir() / "loop.db"


def words_file() -> Path:
    """The words Bombadil made from what you keep asking (launcher.py reads them)."""
    return config_dir() / "words.toml"


def desk_file() -> Path:
    return state_dir() / "desk.toml"


def jobs_dir() -> Path:
    return state_dir() / "jobs"

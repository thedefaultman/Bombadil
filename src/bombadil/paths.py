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

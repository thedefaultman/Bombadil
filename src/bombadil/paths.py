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


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(home() / ".local" / "share")
    return _env_path("BOMBADIL_DATA", Path(base) / "bombadil")


def apps_dir() -> Path:
    return _env_path("BOMBADIL_APPS", home() / "Apps")


def share_dir() -> Path:
    return _env_path("BOMBADIL_SHARE", Path("/usr/share/bombadil"))


def turns_log() -> Path:
    return state_dir() / "turns.jsonl"


def desk_file() -> Path:
    return state_dir() / "desk.toml"


def jobs_dir() -> Path:
    return state_dir() / "jobs"


def mail_socket() -> Path:
    return _env_path("BOMBADIL_MAIL_SOCKET", runtime_dir() / "mail.sock")


def mail_db() -> Path:
    return _env_path("BOMBADIL_MAIL_DB", state_dir() / "mail.db")


def mail_files() -> Path:
    """Copies of a draft's attachments and the attachments being fetched: nothing else."""
    return _env_path("BOMBADIL_MAIL_FILES", state_dir() / "mail")


def mail_profile() -> Path:
    """Thunderbird's profile, the engine's own copy of the mail."""
    return _env_path("BOMBADIL_MAIL_PROFILE", data_dir() / "mail")


def press_log() -> Path:
    return _env_path("BOMBADIL_PRESS_LOG", state_dir() / "presses.jsonl")

"""Bombadil settings: /etc/bombadil/config.toml, overridden by ~/.config/bombadil/config.toml."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import paths

SYSTEM_CONFIG = Path("/etc/bombadil/config.toml")
PROVIDERS = ("claude", "codex")
EXPLAIN = ("brief", "normal", "teach")


@dataclass
class Config:
    provider: str = "claude"
    model: str | None = None
    snapshots: bool = True
    explain: str = "normal"     # brief | normal | teach: how much it shows without being asked

    @property
    def configured(self) -> bool:
        return user_config_path().exists()


def user_config_path() -> Path:
    return paths.config_dir() / "config.toml"


def load() -> Config:
    merged: dict = {}
    for path in (SYSTEM_CONFIG, user_config_path()):
        if path.exists():
            merged.update(tomllib.loads(path.read_text()))
    cfg = Config(
        provider=merged.get("provider", "claude"),
        model=merged.get("model"),
        snapshots=bool(merged.get("snapshots", True)),
        explain=merged.get("explain", "normal"),
    )
    if cfg.explain not in EXPLAIN:
        cfg.explain = "normal"
    if cfg.provider not in PROVIDERS:
        raise ValueError(f"unknown provider {cfg.provider!r}, expected one of {PROVIDERS}")
    return cfg


def save_user(provider: str, model: str | None = None) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'provider = "{provider}"']
    if model:
        lines.append(f'model = "{model}"')
    try:   # a setting people made in words stays when the provider changes
        kept = tomllib.loads(path.read_text()).get("explain") if path.exists() else None
    except (OSError, tomllib.TOMLDecodeError):
        kept = None
    if kept in EXPLAIN:
        lines.append(f'explain = "{kept}"')
    path.write_text("\n".join(lines) + "\n")
    return path

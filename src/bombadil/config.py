"""Bombadil settings: /etc/bombadil/config.toml, overridden by ~/.config/bombadil/config.toml."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

SYSTEM_CONFIG = Path("/etc/bombadil/config.toml")
PROVIDERS = ("claude", "codex")
# The model each provider's CLI is asked for. Claude gets Sonnet: one-word replies took 8 to 19 s on
# the CLI's own default (Opus with the 1M window), and Sonnet halves the price per token. Codex has
# none here and keeps its own default until [models] codex = "..." says otherwise.
DEFAULT_MODELS = {"claude": "claude-sonnet-5-5"}
# "use opus" / "use sonnet" switch a session between these (see launcher.MODEL_WORDS).
CLAUDE_MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5"}
EXPLAIN = ("brief", "normal", "teach")


@dataclass
class Config:
    provider: str = "claude"
    model: str | None = None          # the model for `provider`, as older config files wrote it
    models: dict[str, str] = field(default_factory=dict)   # [models] claude = "...", codex = "..."
    snapshots: bool = True
    explain: str = "normal"     # brief | normal | teach: how much it shows without being asked

    def model_for(self, provider: str) -> str | None:
        """What to pass the CLI as --model: the [models] entry, else a bare `model` for the
        configured provider, else the default, else nothing (the CLI's own default)."""
        return (self.models.get(provider) or (self.model if provider == self.provider else None)
                or DEFAULT_MODELS.get(provider))

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
        models={k: v for k, v in (merged.get("models") or {}).items() if isinstance(v, str) and v},
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
    try:
        before = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        before = {}
    kept = before.get("models") or {}   # switching provider keeps the models
    lines = [f'provider = "{provider}"']
    if model:
        lines.append(f'model = "{model}"')
    if before.get("explain") in EXPLAIN:   # a setting people made in words stays when the provider changes
        lines.append(f'explain = "{before["explain"]}"')
    if kept:
        lines += ["", "[models]"] + [f'{k} = "{v}"' for k, v in kept.items() if isinstance(v, str)]
    path.write_text("\n".join(lines) + "\n")
    return path

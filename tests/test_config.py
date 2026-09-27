import pytest

from bombadil import config


def test_defaults(home):
    cfg = config.load()
    assert cfg.provider == "claude" and cfg.snapshots and not cfg.configured


def test_user_config_overrides(home):
    config.save_user("codex", model="o4-mini")
    cfg = config.load()
    assert (cfg.provider, cfg.model, cfg.configured) == ("codex", "o4-mini", True)


def test_unknown_provider_rejected(home):
    config.user_config_path().parent.mkdir(parents=True)
    config.user_config_path().write_text('provider = "gemini"\n')
    with pytest.raises(ValueError):
        config.load()

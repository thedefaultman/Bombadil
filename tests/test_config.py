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


def test_claude_defaults_to_sonnet_and_codex_to_its_own_default(home):
    cfg = config.load()
    assert cfg.model_for("claude") == "claude-sonnet-5-5" and cfg.model_for("codex") is None


def test_models_are_set_per_provider_and_a_bare_model_is_for_the_configured_one(home):
    path = config.user_config_path()
    path.parent.mkdir(parents=True)
    path.write_text('provider = "claude"\n[models]\nclaude = "claude-opus-5-5"\ncodex = "gpt-x"\n')
    cfg = config.load()
    assert (cfg.model_for("claude"), cfg.model_for("codex")) == ("claude-opus-5-5", "gpt-x")
    path.write_text('provider = "codex"\nmodel = "o4-mini"\n')
    cfg = config.load()
    assert cfg.model_for("codex") == "o4-mini" and cfg.model_for("claude") == "claude-sonnet-5-5"


def test_switching_provider_keeps_the_models(home):
    path = config.user_config_path()
    path.parent.mkdir(parents=True)
    path.write_text('provider = "claude"\n[models]\ncodex = "gpt-x"\n')
    config.save_user("codex")
    assert config.load().model_for("codex") == "gpt-x" and config.load().provider == "codex"

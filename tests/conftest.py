import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every Bombadil path at a temp dir so tests never touch the real system."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BOMBADIL_RUNTIME", str(tmp_path / "run"))
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BOMBADIL_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("BOMBADIL_APPS", str(tmp_path / "Apps"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    return tmp_path

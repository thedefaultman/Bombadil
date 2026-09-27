import pytest

from bombadil import apps

QML = 'import QtQuick\nItem {}\n'


def test_create_and_list(home):
    app = apps.create("Password Manager", QML, "class Backend: pass\n", "keeps secrets")
    assert app.name == "password-manager"
    assert (app.path / "main.qml").read_text() == QML
    assert (app.path / "app.py").exists()
    desktop = home / "share/applications/bombadil-app-password-manager.desktop"
    assert "Exec=bombadil-app run password-manager" in desktop.read_text()
    assert [a.title for a in apps.list_apps()] == ["Password Manager"]


def test_recreate_updates_in_place(home):
    apps.create("Notes", QML)
    apps.create("Notes", QML + "// v2\n")
    assert len(apps.list_apps()) == 1
    assert "v2" in (apps.app_dir("notes") / "main.qml").read_text()


def test_bad_names(home):
    with pytest.raises(ValueError):
        apps.slug("!!!")
    with pytest.raises(ValueError):
        apps.app_dir("../etc")
    with pytest.raises(FileNotFoundError):
        apps.load("nothing")

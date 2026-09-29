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


def test_toml_escaping_survives_any_title(home):
    title = 'Say "hi"\\ \n\tnow\x7f 😀'
    app = apps.create(title, QML, description='a "quoted"\nline', icon="key")
    loaded = apps.load(app.name)
    assert (loaded.title, loaded.description, loaded.icon) == (title, 'a "quoted"\nline', "key")
    desktop = (home / "share/applications" / f"bombadil-app-{app.name}.desktop").read_text()
    assert 'Name=Say "hi"\\\\ now 😀\n' in desktop and "Icon=" in desktop and desktop.count("\n") == 7


def test_extra_files_and_what_create_refuses(home):
    app = apps.create("Notes", QML, files={"EntryRow.qml": "import QtQuick\nItem {}\n", "lib/util.js": "var x = 1\n"})
    assert (app.path / "EntryRow.qml").exists() and (app.path / "lib/util.js").read_text() == "var x = 1\n"
    (app.path / "data").mkdir()
    (app.path / "data/state.json").write_text('{"keep": true}')
    for bad in ("data/state.json", "/etc/passwd", "../x.qml", "lib/../../x.qml", "a//b.qml", ".hidden.qml",
                "run.sh", "main.qml", "app.py", ""):
        with pytest.raises(ValueError):
            apps.create("Notes", QML + "// v2\n", files={bad: "x"})
    # A refused call writes nothing: not main.qml, not data.
    assert "v2" not in (app.path / "main.qml").read_text()
    assert (app.path / "data/state.json").read_text() == '{"keep": true}'
    assert not list(app.path.glob(".*.tmp"))


def test_run_logs_to_the_state_dir(home, monkeypatch):
    apps.create("Notes", QML)
    seen = {}

    def popen(cmd, **kw):
        seen.update(cmd=cmd, out=kw.get("stdout"))
        return None
    monkeypatch.setattr(apps.subprocess, "Popen", popen)
    apps.run("notes")
    assert seen["cmd"][-2:] == ["run", "notes"] and seen["cmd"][-3].endswith("bin/bombadil-app")
    assert seen["out"].name == str(apps.log_path("notes")) == str(home / "state/apps/notes.log")


def test_a_built_in_app_runs_unless_you_have_one_of_that_name(home, tmp_path, monkeypatch):
    built = tmp_path / "builtin"
    (built / "brain").mkdir(parents=True)
    (built / "brain" / "main.qml").write_text("import QtQuick\nItem {}\n")
    (built / "brain" / "app.toml").write_text('title = "Brain"\n')
    monkeypatch.setattr(apps, "builtin_dir", lambda: built)
    assert apps.load("brain").path == built / "brain"
    assert apps.load("brain").title == "Brain"
    assert "brain" not in [a.name for a in apps.list_apps()]   # yours are listed, not the OS's
    mine = apps.create("Brain", "import QtQuick\nRectangle {}\n")
    assert mine.path == home / "Apps" / "brain"
    assert apps.load("brain").path == home / "Apps" / "brain"
    assert (built / "brain" / "main.qml").read_text() == "import QtQuick\nItem {}\n"


def test_a_built_in_apps_saved_state_lives_with_the_other_apps_state(home, tmp_path, monkeypatch):
    from bombadil.appkit import context
    built = tmp_path / "builtin"
    (built / "brain").mkdir(parents=True)
    (built / "brain" / "main.qml").write_text("import QtQuick\nItem {}\n")
    monkeypatch.setattr(apps, "builtin_dir", lambda: built)
    ctx = context.for_app("brain")
    assert ctx.dir == built / "brain"
    assert ctx.data_dir == home / "state" / "apps" / "brain" / "data"
    assert context.for_app("brain").resolve("notes.md") == ctx.data_dir / "notes.md"

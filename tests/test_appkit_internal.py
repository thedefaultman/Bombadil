"""The kit's internal components (Stone, TipScene): registered for the shell and the built-in apps, and
refused in an app the agent writes. `bombadil-app check` is what create_app and check_app run, so the
agent gets the refusal as an error with its file and line."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bombadil import apps
from bombadil.appkit import check
from bombadil.appkit.context import AppContext

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "share" / "qml" / "Bombadil"
SKILL = ROOT / "share" / "skills" / "bombadil-apps"

PLAIN = """\
import QtQuick
import Bombadil

AppWindow {
    title: "Demo"
    Stone { face: "working" }
}
"""


def ctx_for(directory: Path) -> AppContext:
    return AppContext(directory.name, directory.name, directory, directory / "main.qml")


def app_with(tmp_path, files: dict[str, str], name="demo") -> AppContext:
    d = tmp_path / name
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return ctx_for(d)


REGISTERED = ("Stone", "TipScene")


def test_the_kit_registers_the_internal_components_and_the_skill_does_not_list_them():
    qmldir = (KIT / "qmldir").read_text()
    for name in REGISTERED:
        assert f"\n{name} 1.0 {name}.qml" in qmldir and (KIT / f"{name}.qml").is_file(), name
    skill = "\n".join(p.read_text() for p in SKILL.rglob("*.md"))
    for name in check.INTERNAL_COMPONENTS:
        assert name not in skill, f"{name} must not be in the skill the agent reads"
    assert set(check.INTERNAL_COMPONENTS) == {"Stone", "TipScene"}


def test_an_app_that_draws_the_stone_or_a_tip_scene_is_refused_with_file_and_line(home):
    ctx = app_with(home, {"main.qml": PLAIN})
    refused = check.refused_components(ctx)
    assert len(refused) == 1 and refused[0].startswith("main.qml:6: Stone ")
    ctx = app_with(home, {"main.qml": "import QtQuick\nimport Bombadil\n\nItem {\n  TipScene {\n    tip: ({}) }\n}\n"}, "two")
    assert [r.split(" ")[0] for r in check.refused_components(ctx)] == ["main.qml:5:"]
    assert "TipScene" in check.refused_components(ctx)[0]


def test_the_refusal_says_what_to_use_instead(home):
    ctx = app_with(home, {"main.qml": PLAIN})
    assert "Icon" in check.refused_components(ctx)[0]


@pytest.mark.parametrize("text", [
    "import QtQuick\nimport Bombadil as Kit\nItem { Kit.Stone { } }\n",
    "import QtQuick\nimport Bombadil 1.0 as K\nItem { K.TipScene { } }\n",
    "import QtQuick\nimport Bombadil\nItem {\n  Stone\n  {\n  }\n}\n",
    "import QtQuick\nimport Bombadil\nItem { Component { Stone{} } }\n",
])
def test_every_way_of_declaring_one_is_found(home, text):
    assert len(check.refused_components(app_with(home, {"main.qml": text}))) == 1


def test_other_files_of_the_app_are_read_too_but_not_its_data(home):
    ctx = app_with(home, {
        "main.qml": "import QtQuick\nimport Bombadil\nItem { }\n",
        "lib/Mark.qml": "import QtQuick\nimport Bombadil\n\nItem {\n  Stone { }\n}\n",
        "data/notes.qml": "import QtQuick\nimport Bombadil\nItem { Stone { } }\n",
    })
    assert [r.split(" ")[0] for r in check.refused_components(ctx)] == ["lib/Mark.qml:5:"]


@pytest.mark.parametrize("text", [
    "import QtQuick\nimport Bombadil\nItem {\n  // Stone { } is the pill's\n  /* TipScene {\n } */\n}\n",   # comments
    "import QtQuick\nimport Bombadil\nItem { Stoneware { } StoneAge { } Text { text: \"stone\" } }\n",         # other names
    "import QtQuick\nimport Bombadil as Kit\nItem { Stone { } }\n",     # `Stone` is not the kit's here: nothing imports it bare
    "import QtQuick\nItem { Stone { } }\n",                              # the kit is not imported at all
    "import QtQuick\nimport Bombadil.Style\nItem { Stone { } }\n",       # the Style module is another
    "import QtQuick\nimport Bombadil\nItem { property var mystone: 1; Mystone { } }\n",
])
def test_what_is_not_a_use_is_left_alone(home, text):
    assert check.refused_components(app_with(home, {"main.qml": text})) == []


OWN = 'import QtQuick\nRectangle { property string mine: "yes"; objectName: "own"; width: 20; height: 20 }\n'
USE = 'import QtQuick\n{imports}\nItem {{ Stone {{ objectName: "s" }} }}\n'


def stone_app(tmp_path, imports: str, where="Stone.qml", name="own", main="main.qml"):
    """An app whose `main` uses a bare `Stone {` after these import lines, and which has its own Stone at `where`."""
    return app_with(tmp_path, {main: USE.format(imports=imports), where: OWN}, name)


# what QML builds for a bare `Stone {`, case by case (import lines, where the app's own Stone.qml is):
CASES = {
    "kit only, own beside main": ("import Bombadil", "Stone.qml", "kit"),
    "import . below the kit": ('import Bombadil\nimport "."', "Stone.qml", "own"),
    "import . above the kit": ('import "."\nimport Bombadil', "Stone.qml", "kit"),
    "folder below the kit": ('import Bombadil\nimport "lib"', "lib/Stone.qml", "own"),
    "folder above the kit": ('import "lib"\nimport Bombadil', "lib/Stone.qml", "kit"),
    "folder below the kit, aliased": ('import Bombadil\nimport "lib" as Lib', "lib/Stone.qml", "kit"),
    "the kit aliased": ("import Bombadil as Kit", "Stone.qml", "own"),
    "folder not imported": ("import Bombadil", "lib/Stone.qml", "kit"),
}
ENGINE = """
import json, os, sys
from pathlib import Path
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
app = QGuiApplication([])
out = {}
for d in sys.argv[2:]:
    engine = QQmlEngine()
    engine.addImportPath(sys.argv[1])
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(d) / "main.qml")))
    obj = comp.create()
    s = obj.findChild(QObject, "s") if obj is not None else None
    out[Path(d).name] = ([e.toString() for e in comp.errors()][:1] if s is None
                         else "kit" if s.property("face") is not None else "own")
print("RESULT " + json.dumps(out))
"""


def test_the_check_follows_the_engine_and_refuses_exactly_where_the_kits_component_wins(home):
    """Not what we think QML does: what it does. The kit is imported by module, QML lets the later import win,
    and a folder's own files come first and weakest, so an app's own Stone.qml next to a main.qml that only says
    `import Bombadil` is silently the kit's stone. The check says so there, and only there."""
    pytest.importorskip("PySide6")
    dirs, expected = [], {}
    for i, (label, (imports, where, winner)) in enumerate(CASES.items()):
        ctx = stone_app(home, imports, where, name=f"case{i}")
        dirs.append(ctx.dir)
        expected[ctx.dir.name] = (label, winner, bool(check.refused_components(ctx)))
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"}
    r = subprocess.run([sys.executable, "-c", ENGINE, str(KIT.parent), *map(str, dirs)], capture_output=True,
                       text=True, env=env, timeout=90, check=False)
    lines = [line for line in r.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, r.stdout + r.stderr
    built = json.loads(lines[-1].removeprefix("RESULT "))
    for name, (label, winner, refused) in expected.items():
        assert built[name] == winner, (label, built[name])           # the engine builds what this table says
        assert refused == (winner == "kit"), (label, refused)           # refused exactly where the kit's is built


def test_an_own_component_hidden_by_the_kits_gets_an_error_that_says_how_to_use_it(home):
    refused = check.refused_components(stone_app(home, "import Bombadil"))
    assert len(refused) == 1 and refused[0].startswith("main.qml:3: Stone is the kit's here, not your Stone.qml")
    assert 'import "."' in refused[0] and "another name" in refused[0] and "Icon" not in refused[0]
    plain = check.refused_components(stone_app(home, "import Bombadil", "lib/Stone.qml", "elsewhere"))
    assert len(plain) == 1 and "Icon" in plain[0]                  # one the file cannot see: the plain refusal


def test_an_own_component_beside_the_file_that_uses_it_in_a_folder_is_found_there(home):
    for imports, refused in (("import Bombadil", 1), ('import Bombadil\nimport "."', 0), ('import "."\nimport Bombadil', 1)):
        ctx = app_with(home, {"main.qml": "import QtQuick\nItem { }\n", "lib/Stone.qml": OWN,
                              "lib/Mark.qml": USE.format(imports=imports)}, f"sub{refused}{len(imports)}")
        found = check.refused_components(ctx)
        line = 3 + imports.count("\n")
        assert len(found) == refused and all(f.startswith(f"lib/Mark.qml:{line}:") for f in found), (imports, found)
    ctx = app_with(home, {"main.qml": USE.format(imports="import Bombadil"), "lib/Stone.qml": OWN}, "root-only")
    assert len(check.refused_components(ctx)) == 1                  # a file does not see a Stone.qml of a folder it does not import
    ctx = app_with(home, {"main.qml": 'import QtQuick\nimport Bombadil\nItem { }\n',
                          "lib/Mark.qml": USE.format(imports="import Bombadil"), "Stone.qml": OWN}, "above")
    assert len(check.refused_components(ctx)) == 1                  # nor one in the folder above it


def test_a_use_the_kit_does_not_own_is_left_alone(home):
    ctx = app_with(home, {"main.qml": "import QtQuick\nItem { }\n",
                          ".hidden/x.qml": USE.format(imports="import Bombadil"),                    # a dot folder is not read
                          "lib/Other.qml": 'import QtQuick\nimport Bombadil\nItem { Mine.Stone { } Foo.Stone { } }\n'}, "left")
    assert check.refused_components(ctx) == []                       # `Foo.Stone` is another module's, not a bare `Stone`
    ctx = app_with(home, {"main.qml": "import QtQuick\nimport Bombadil as Kit\nItem { Kit.Stone { } }\n",
                          "Stone.qml": "import QtQuick\nItem { }\n"}, "aliased")
    assert len(check.refused_components(ctx)) == 1       # the kit's, spelled as the kit's, is still refused


def test_a_built_in_app_may_use_them(home, monkeypatch):
    builtin = home / "share-apps"
    monkeypatch.setattr(apps, "builtin_dir", lambda: builtin)
    ctx = app_with(builtin, {"main.qml": PLAIN}, "tips")
    assert apps.is_builtin(ctx.dir) and check.refused_components(ctx) == []
    mine = app_with(home / "Apps", {"main.qml": PLAIN}, "tips")      # the same name in ~/Apps is not built in
    assert len(check.refused_components(mine)) == 1


def run_cli(*args):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QT_QPA_PLATFORM": "offscreen",
           "QT_QUICK_BACKEND": "software"}
    return subprocess.run([sys.executable, str(ROOT / "bin" / "bombadil-app"), *args], capture_output=True, text=True,
                          env=env, timeout=90, check=False)


def test_check_fails_an_app_that_draws_the_stone_and_passes_it_once_it_does_not(home):
    pytest.importorskip("PySide6")
    d = apps.app_dir("demo")
    d.mkdir(parents=True)
    (d / "main.qml").write_text(PLAIN)
    r = run_cli("check", "demo")
    result = json.loads(r.stdout)
    assert r.returncode == 1 and not result["ok"] and not result["loaded"], result
    assert any(e.startswith("main.qml:6: Stone ") for e in result["errors"]), result
    (d / "main.qml").write_text(PLAIN.replace('Stone { face: "working" }', "Text { text: \"hi\" }"))
    r = run_cli("check", "demo")
    result = json.loads(r.stdout)
    assert r.returncode == 0 and result["ok"] and result["loaded"], result


def test_check_passes_an_app_with_a_stone_of_its_own_only_where_the_engine_builds_it(home):
    pytest.importorskip("PySide6")
    d = apps.app_dir("go")
    d.mkdir(parents=True)
    (d / "Stone.qml").write_text(OWN.replace("objectName", 'Component.onCompleted: console.log("own stone"); objectName'))
    main = 'import QtQuick\nimport Bombadil\n{imports}\nAppWindow {{ title: "Go"; Stone {{ }} }}\n'
    (d / "main.qml").write_text(main.format(imports=""))
    result = json.loads(run_cli("check", "go").stdout)
    assert not result["ok"] and any("import \".\"" in e for e in result["errors"]), result      # the kit's would be built
    (d / "main.qml").write_text(main.format(imports='import "."'))
    r = run_cli("check", "go")
    result = json.loads(r.stdout)
    assert r.returncode == 0 and result["ok"] and "own stone" in result["console"], result      # and now it is the app's own

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


REGISTERED = ("Stone",)      # TipScene joins when it lands (the next commit)


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


def test_an_app_with_a_component_of_its_own_by_that_name_is_left_alone(home):
    ctx = app_with(home, {"main.qml": PLAIN, "Stone.qml": "import QtQuick\nRectangle { color: \"grey\" }\n"})
    assert check.refused_components(ctx) == []
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

"""One set of colours, sizes and type for the shell and the app kit.

The kit's Theme.qml is the source. The shell's QML reads it (`Kit.Theme.x`); the desk reads
DeskTheme.js, a library of plain values, and the parity test below is what keeps the two equal.
"""

import os
import re
from pathlib import Path

import pytest
from qml_theme import THEME, THEME_QML

SHELL = Path(__file__).resolve().parents[1] / "shell"
QML = sorted(SHELL.glob("*.qml"))

# DeskTheme.js name -> Theme.qml token
DESK_COLOURS = {
    "panel": "glassCard", "strip": "glassStrip", "border": "border", "borderStrong": "borderStrong",
    "raised": "raised", "sunken": "sunken", "fg": "fg", "muted": "muted", "faint": "faint",
    "you": "fg", "machine": "accent", "sessions": "info", "ok": "good", "amber": "warn",
    "amberText": "warnInk", "red": "bad", "redText": "badInk", "onAccent": "accentFg",
}
DESK_VALUES = {"radius": "radius", "rowHeight": "rowHeight", "pillHeight": "pillHeight",
               "fontFamily": "fontFamily", "monoFamily": "monoFamily"}


def desk_theme():
    out = {}
    for name, raw in re.findall(r'^var (\w+) = ("[^"]*"|[\d.]+)', (SHELL / "DeskTheme.js").read_text(),
                                re.MULTILINE):
        out[name] = raw.strip('"') if raw.startswith('"') else float(raw)
    return out


def code(path):
    """A QML file's text without its // comments."""
    return re.sub(r"//[^\n]*", "", path.read_text())


def blocks(text, kinds=("Text", "TextField")):
    """The body of every `Text { ... }` / `TextField { ... }` in a QML file."""
    for m in re.finditer(r"(?<![\w.])(%s)\s*\{" % "|".join(kinds), text):
        depth, i, quote = 0, m.end() - 1, None
        while i < len(text):
            c = text[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield m.group(1), text[m.end():i]


def test_theme_carries_every_token_the_shell_and_the_design_system_name():
    for name in ("glassPill", "glassLine", "glassChip", "glassCard", "glassStrip", "glassRaised",
                 "borderActive", "accentInk", "warnInk", "badInk", "badLine", "accentOnLight",
                 "inkOnLight", "groundLight", "pillHeight", "radiusPill", "radiusLine",
                 "radiusLineButton", "chipHeight", "pillMaxWidth", "railWidth", "pillBorder",
                 "focusRing", "pulseLow", "slow", "fontFamily", "monoFamily"):
        assert name in THEME, name
    # The orange stays Claude's.
    assert THEME["accent"] == "#d97757"
    assert THEME["pillHeight"] == 2 * THEME["radiusPill"]


def test_desk_theme_matches_the_kits_tokens():
    desk = desk_theme()
    for name, token in {**DESK_COLOURS, **DESK_VALUES}.items():
        assert desk[name] == THEME[token], f"DeskTheme.{name} is {desk[name]}, Theme.{token} is {THEME[token]}"
    assert desk["cardWidth"] == THEME["railWidth"]
    assert desk["stripHeight"] == THEME["chipHeight"]


@pytest.mark.parametrize("path", QML, ids=lambda p: p.name)
def test_no_shell_file_hard_codes_a_colour(path):
    text = code(path)
    assert not re.findall(r"#[0-9a-fA-F]{6,8}\b", text), f"{path.name}: a hex colour; use a Theme token"
    assert not re.findall(r'"(white|black)"', text), f"{path.name}: a named colour; use a Theme token"


@pytest.mark.parametrize("path", QML, ids=lambda p: p.name)
def test_every_shell_text_sets_the_type_family(path):
    # Inter everywhere, not whatever fontconfig picks for "sans". `font: other.font` hands the
    # whole font on, which is fine.
    text = code(path)
    for kind, body in blocks(text):
        assert re.search(r"font\.family\s*:", body) or re.search(r"(?<![.\w])font\s*:", body), \
            f"{path.name}: a {kind} without font.family: {body.strip()[:80]!r}"


def test_the_shell_gets_the_kit_on_its_import_path():
    # Quickshell resolves a singleton only from inside its own directory or from the import path, so
    # `import Bombadil as Kit` needs bin/bombadil-shell (every launcher runs it) to name share/qml.
    launcher = (SHELL.parent / "bin" / "bombadil-shell").read_text()
    assert 'QML2_IMPORT_PATH="$root/share/qml' in launcher and "quickshell -p" in launcher
    assert os.access(SHELL.parent / "bin" / "bombadil-shell", os.X_OK)
    assert (SHELL.parent / "share" / "qml" / "Bombadil" / "qmldir").exists()
    assert "singleton Theme" in (THEME_QML.parent / "qmldir").read_text()
    for path in QML:
        imports = re.findall(r"^import (.*Bombadil.*)$", code(path), re.MULTILINE)
        assert all(i == "Bombadil as Kit" for i in imports), (path.name, imports)


def test_nothing_launches_quickshell_without_the_kit_path():
    # A bare `quickshell -p .../shell.qml` starts a bar whose colours are all undefined.
    root = SHELL.parent
    for path in (root / "iso" / "airootfs" / "etc" / "skel" / ".config" / "hypr" / "hyprland.lua",
                 root / "scripts" / "dev-session.sh", root / "tests" / "desktop" / "driver.py"):
        assert not re.search(r"quickshell\W+-p\s+\S*shell\.qml", path.read_text()), path.name
    assert "bombadil-shell" in (root / "scripts" / "build-iso.sh").read_text()

"""The mark's files: the icons the image installs, the README banner, the GRUB theme."""

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from qml_theme import THEME

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "iso" / "airootfs" / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
PIXMAPS = ROOT / "iso" / "airootfs" / "usr" / "share" / "pixmaps"
BRAND = ROOT / "docs" / "brand"
GRUB = ROOT / "share" / "grub" / "bombadil"


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def test_the_image_installs_the_icon_in_hicolor_and_pixmaps():
    tile = (ICONS / "bombadil.svg").read_text()
    assert tile == (PIXMAPS / "bombadil.svg").read_text()
    ET.fromstring(tile)
    assert (ICONS / "bombadil-symbolic.svg").exists()


def test_installed_icons_need_no_svg_mask():
    # Qt's SVG renderer (Quickshell, the kit's apps) ignores <mask>, so the b would vanish. The tile
    # lays the b over the stone in the tile's ground; the symbolic icon cuts it out with even-odd.
    for path in (ICONS / "bombadil.svg", ICONS / "bombadil-symbolic.svg", PIXMAPS / "bombadil.svg"):
        text = path.read_text()
        assert "<mask" not in text and "mask=" not in text, path
    tile = (ICONS / "bombadil.svg").read_text()
    assert f'fill="{THEME["panel"]}"' in tile and f'stroke="{THEME["panel"]}"' in tile
    assert f'fill="{THEME["fg"]}"' in tile
    symbolic = (ICONS / "bombadil-symbolic.svg").read_text()
    assert 'fill-rule="evenodd"' in symbolic and "#bebebe" in symbolic
    root = ET.fromstring(symbolic)
    assert root.attrib["viewBox"] == "0 0 16 16"


def test_the_readme_opens_with_the_lockup_for_dark_and_light_pages():
    readme = (ROOT / "README.md").read_text()
    assert readme.lstrip().startswith("<p align=\"center\">")
    for src in re.findall(r'(?:src|srcset)="([^"]+)"', readme[:600]):
        assert (ROOT / src).exists(), src
    assert "prefers-color-scheme: dark" in readme[:600]


def test_brand_files_have_the_sizes_github_and_grub_expect():
    assert png_size(BRAND / "bombadil-social-preview.png") == (1280, 640)
    assert png_size(BRAND / "bombadil-avatar.png") == (500, 500)
    assert png_size(GRUB / "background.png") == (1920, 1080)
    for name in ("bombadil-lockup", "bombadil-lockup-light", "bombadil-mark", "bombadil-tile"):
        ET.fromstring((BRAND / f"{name}.svg").read_text())


def test_the_grub_theme_names_its_files_and_the_ground():
    theme = (GRUB / "theme.txt").read_text()
    assert re.search(r'^desktop-image: "background.png"$', theme, re.MULTILINE)
    assert (GRUB / "background.png").exists()
    assert f'desktop-color: "{THEME["bg"]}"' in theme
    assert 'terminal-font: "Inter Regular 16"' in theme

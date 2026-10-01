"""The wallpaper: share/wallpaper/bombadil.png, drawn by scripts/make-wallpaper.py from the design
system's tokens, and shell/Wallpaper.qml, which puts it under the desk.

The picture is checked pixel by pixel against the tokens (it is the ground of the whole desk, so
every card, the pill and every window sit on it), and the shell file is checked for the one trap
Quickshell sets: a path above the shell's own folder resolves to a dead end.
"""

import importlib.util
import os
import re
import struct
import sys
from pathlib import Path

import pytest
from qml_theme import THEME

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
PICTURE = ROOT / "share" / "wallpaper" / "bombadil.png"


def rgb(token):
    h = THEME[token]
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def test_the_picture_is_a_png_in_16_by_9_big_enough_for_a_4k_screen_to_fill():
    data = PICTURE.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert (depth, colour) == (8, 2), "8-bit RGB, no alpha: it is a ground, nothing shows through it"
    assert width * 9 == height * 16 and width >= 2560
    assert len(data) < 6_000_000


def _image():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtGui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
    img = QtGui.QImage(str(PICTURE))
    assert not img.isNull()
    return img


def _mean(img, cx, cy, half=8):
    """The mean colour of the square around (cx, cy) given as fractions of the picture."""
    x, y = int(cx * img.width()), int(cy * img.height())
    tot, n = [0, 0, 0], 0
    for yy in range(y - half, y + half):
        for xx in range(x - half, x + half):
            c = img.pixelColor(xx, yy)
            tot = [tot[0] + c.red(), tot[1] + c.green(), tot[2] + c.blue()]
            n += 1
    return tuple(v / n for v in tot)


def test_the_corners_are_the_ground_falling_toward_its_darkest():
    img = _image()
    bg, sunken = rgb("bg"), rgb("sunken")
    for cx, cy in ((0.01, 0.01), (0.99, 0.01), (0.01, 0.99), (0.99, 0.99)):
        px = _mean(img, cx, cy)
        assert all(s - 2 <= v <= b + 1 for v, s, b in zip(px, sunken, bg)), (cx, cy, px)


def test_the_desks_rails_are_no_lighter_than_the_ground_so_cards_stay_the_lightest_thing():
    # The rails are 316 px of 1920 at each side; the pill is at the bottom. Whatever is there, a card
    # (panel at 96%) or the pill (94%) has to read against it.
    img = _image()
    bg, panel = rgb("bg"), rgb("panel")
    for cx in (0.05, 0.11, 0.89, 0.95):
        for cy in (0.1, 0.3, 0.5, 0.7):
            px = _mean(img, cx, cy)
            assert all(v <= b + 1 for v, b in zip(px, bg)), (cx, cy, px)
    for cx in (0.3, 0.4, 0.5, 0.6, 0.7):
        px = _mean(img, cx, 0.97)
        # the pill's row: dark enough for the pill
        assert all(p - v >= 6 for v, p in zip(px, panel)), (cx, px)


def test_the_middle_is_lit_and_the_stone_lies_in_it_without_going_past_raised():
    img = _image()
    bg, raised, overlay = rgb("bg"), rgb("raised"), rgb("overlay")
    lit = _mean(img, 0.5, 0.2)
    assert all(v - b >= 3 for v, b in zip(lit, bg)), lit                  # a light, not a flat ground
    top = _mean(img, 0.5, 0.30)
    assert all(t - v >= 5 for t, v in zip(top, lit)), (top, lit)          # the stone is there to see
    assert all(t <= r for t, r in zip(top, raised)), (top, raised)        # and quieter than a raised surface
    # the brightest pixel is the stone's hairline, still under the overlay colour (plus a step of noise)
    brightest = [0, 0, 0]
    for y in range(int(img.height() * 0.28), int(img.height() * 0.5), 3):
        for x in range(0, img.width(), 7):
            c = img.pixelColor(x, y)
            brightest = [max(b, v) for b, v in zip(brightest, (c.red(), c.green(), c.blue()))]
    assert all(v <= o + 2 for v, o in zip(brightest, overlay)), brightest


def test_the_b_is_cut_through_the_stone_so_the_ground_shows_in_it():
    img = _image()
    stone, cut = _mean(img, 0.5, 0.34), _mean(img, 0.4425, 0.5)   # the stone's body, then the b's stem
    assert all(s - c >= 4 for s, c in zip(stone, cut)), (stone, cut)


def test_nothing_in_it_is_orange_or_white_or_black():
    # Orange is the working stone's; the picture is the dark palette only (blue-grey: red never above blue).
    img = _image()
    sunken = rgb("sunken")
    worst_warm, darkest, lightest = -99, 255, 0
    for y in range(0, img.height(), 11):
        for x in range(0, img.width(), 11):
            c = img.pixelColor(x, y)
            worst_warm = max(worst_warm, c.red() - c.blue())
            darkest, lightest = min(darkest, c.red()), max(lightest, c.blue())
    assert worst_warm <= 0, worst_warm
    assert darkest >= sunken[0] - 2, darkest
    assert lightest <= rgb("overlay")[2] + 2, lightest


def _script():
    spec = importlib.util.spec_from_file_location("make_wallpaper", ROOT / "scripts" / "make-wallpaper.py")
    return spec, importlib.util.module_from_spec(spec)


def test_the_script_still_draws_the_picture_from_the_tokens(tmp_path):
    # The picture is generated: a token or the mark changed without drawing it again fails here.
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    pytest.importorskip("cairosvg")
    import numpy as np
    from PIL import Image
    spec, mod = _script()
    spec.loader.exec_module(mod)
    out = tmp_path / "again.png"
    mod.main(["--out", str(out)])
    a = np.asarray(Image.open(out).convert("RGB")).astype(int)
    b = np.asarray(Image.open(PICTURE).convert("RGB")).astype(int)
    assert a.shape == b.shape
    diff = np.abs(a - b)
    assert diff.mean() < 0.35 and np.percentile(diff, 99.9) <= 3, \
        "share/wallpaper/bombadil.png is out of date: run scripts/make-wallpaper.py"


def test_the_script_can_draw_the_ground_without_the_stone(tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    pytest.importorskip("cairosvg")
    import numpy as np
    from PIL import Image
    spec, mod = _script()
    spec.loader.exec_module(mod)
    out = tmp_path / "ground.png"
    mod.main(["--out", str(out), "--width", "640", "--height", "360", "--no-stone"])
    a = np.asarray(Image.open(out).convert("RGB")).astype(float)
    assert a.shape == (360, 640, 3)
    # the middle (where the stone would be) is the plain light: no step between stone and ground
    assert abs(a[100:140, 300:340].mean() - a[100:140, 220:260].mean()) < 4


def code(path):
    """The file without its // comments (a // inside a string, as in "file://", stays)."""
    return re.sub(r"(^|\s)//[^\n]*", "", path.read_text())


def test_the_shell_puts_the_picture_under_everything_and_takes_no_clicks():
    text = code(SHELL / "Wallpaper.qml")
    assert "WlrLayershell.layer: WlrLayer.Background" in text
    assert re.search(r'WlrLayershell\.namespace: "bombadil-wallpaper"', text)
    assert "ExclusionMode.Ignore" in text, "it reaches the screen's edge under the bar's zone"
    assert re.search(r"mask: Region \{\}", text), "an empty input region: it takes no clicks"
    assert "color: Kit.Theme.bg" in text, "a picture that will not load leaves the ground, not white"
    assert "fillMode: Image.PreserveAspectCrop" in text
    assert re.search(r"^\s*Wallpaper \{", code(SHELL / "shell.qml"), re.MULTILINE)


def test_the_picture_is_found_the_way_quickshell_can_find_it():
    # Qt.resolvedUrl("../share/...") from shell/ is `qrc:/qs-blackhole`: Quickshell hides everything
    # outside the shell's folder from QML. shellPath() is a real path, `..` and all.
    text = code(SHELL / "Wallpaper.qml")
    assert 'Quickshell.shellPath("../share/wallpaper/bombadil.png")' in text
    assert not re.search(r'resolvedUrl\(\s*"\.\./', text)
    assert (SHELL / ".." / "share" / "wallpaper" / "bombadil.png").resolve() == PICTURE


def test_the_users_own_picture_is_named_in_a_file_in_their_config_folder():
    text = code(SHELL / "Wallpaper.qml")
    assert '+ "/wallpaper"' in text and "BOMBADIL_CONFIG" in text and "XDG_CONFIG_HOME" in text
    # the same folder agentd and the CLI read (src/bombadil/paths.py:config_dir)
    sys.path.insert(0, str(ROOT / "src"))
    from bombadil import paths
    assert paths.config_dir().name == "bombadil"
    assert "printErrors: false" in text, "no file is no choice, and the log says nothing about it"
    # a picture that will not load falls back to the standard one
    assert "Image.Error" in text and "failed" in text


def _luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_a_picture_of_the_users_own_is_dimmed_so_muted_text_reads_over_the_brightest_one():
    # The glass (panel at 85 to 96%) lets a little of the picture through. Over a pure white stripe,
    # dimmed toward the ground by `dim`, the muted text must keep 4.5:1 on the quietest glass (the chips).
    text = code(SHELL / "Wallpaper.qml")
    dim = float(re.search(r"readonly property real dim: ([0-9.]+)", text).group(1))
    bg, panel, muted = rgb("bg"), rgb("panel"), rgb("muted")
    alpha = int(THEME["glassChip"][1:3], 16) / 255
    under = [255 * (1 - dim) + b * dim for b in bg]
    glass = [alpha * p + (1 - alpha) * u for p, u in zip(panel, under)]
    assert _contrast(muted, glass) >= 4.5, (dim, _contrast(muted, glass))
    # ...without dimming it away: a picture at 40% of its light is still a picture.
    assert dim <= 0.7
    assert re.search(r"color: Kit\.Theme\.bg\s+opacity: wallpaper\.own && !win\.failed \? wallpaper\.dim : 0", text), \
        "the dimming is the ground colour over the picture, only for a picture that is the user's own"


def test_the_file_new_systems_ship_holds_only_comments_so_the_standard_picture_shows_and_can_be_watched():
    # A file that does not exist cannot be watched, so a first path would want the bar started again.
    shipped = ROOT / "iso" / "airootfs" / "etc" / "skel" / ".config" / "bombadil" / "wallpaper"
    lines = [ln for ln in shipped.read_text().splitlines() if ln.strip()]
    assert lines and all(ln.lstrip().startswith("#") for ln in lines)
    text = code(SHELL / "Wallpaper.qml")
    assert 'startsWith("#")' in text, "comment lines are skipped when the picture's path is looked for"


def test_a_line_that_is_not_a_path_says_so_in_the_log_without_printing_what_is_in_it():
    # An image written into the file itself, not its path, used to fall back to the standard picture
    # without a word.
    text = code(SHELL / "Wallpaper.qml")
    warn = re.search(r"console\.warn\((.*?)\)\s*\n\s*return standard", text, re.S).group(1)
    assert "holds the path of an image" in warn and "not the image itself" in warn
    assert "line" not in re.sub(r'"[^"]*"', "", warn), "only fixed words and the file's own path are logged"

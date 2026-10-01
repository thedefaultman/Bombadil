#!/usr/bin/env python3
"""Draw Bombadil's wallpaper: share/wallpaper/bombadil.png.

The wallpaper is made only of the design system's own surfaces (share/qml/Bombadil/Theme.qml):
the ground (`bg`) lit softly from above, darkening toward `sunken` at the edges, with the mark
(docs/brand/bombadil-mark.svg) lying very quietly in the middle, as a stone between `panel` and
`raised` at its top and most of the way back down to `bg` at its foot, with a hairline of `border`
along its top edge. No orange, no white, nothing that moves. Nothing in it is lighter than `raised`
but that hairline, so the desk's cards, the pill and every window stay the lightest things on the
screen.

    pip install pillow numpy cairosvg
    scripts/make-wallpaper.py                  # the default, with the stone
    scripts/make-wallpaper.py --no-stone       # only the ground and its light
    scripts/make-wallpaper.py --out ~/Pictures/ground.png --width 3840 --height 2160

The output is dithered with a little noise: a gradient this dark has only a dozen steps between
its darkest and brightest pixel, and without noise each step shows as a band.
"""

import argparse
import io
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
THEME_QML = ROOT / "share" / "qml" / "Bombadil" / "Theme.qml"
MARK = ROOT / "docs" / "brand" / "bombadil-mark.svg"
DEFAULT_OUT = ROOT / "share" / "wallpaper" / "bombadil.png"

# Where things sit, as fractions of the picture. The desk keeps its cards in two rails at the sides
# and the pill at the bottom, so the middle is the part that stays empty at rest.
LIGHT_AT = (0.50, 0.40)       # the centre of the soft light
LIGHT_REACH = 0.60            # how far it reaches, in picture widths
STONE_HEIGHT = 0.42           # the stone's height, in picture heights
STONE_AT = (0.50, 0.45)       # the stone's centre
LIGHT_LIFT = 0.80             # how far toward `panel` the light lifts the ground at its centre
EDGE_DROP = 0.75              # how far toward `sunken` the corners fall
STONE_TOP = 0.85              # how far from `panel` toward `raised` the stone's top is
STONE_FOOT = 0.70             # how far from `bg` toward `panel` the stone's foot is
RIM_STRENGTH = 0.50           # the stone's top edge, as a share of the step from its top to `border`


def theme_colours():
    """{name: (r, g, b)} for the opaque colour tokens in Theme.qml."""
    out = {}
    token = r'readonly property color (\w+): "#([0-9a-fA-F]{6})"'
    for name, hexa in re.findall(token, THEME_QML.read_text()):
        out[name] = tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    return out


def render_svg(inner, size):
    import cairosvg
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{inner}</svg>'
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=size, output_height=size)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[..., 3].astype(float) / 255


def mark_matte(height_px):
    """The stone with its b cut through, as an alpha matte `height_px` tall, plus the stone alone.

    cairosvg draws no <mask>, so the stone and the b are drawn apart and the b is taken out here.
    The mark's own box is 64 units with the stone in 3..61."""
    svg = MARK.read_text()
    stone = re.search(r'<path d="([^"]+)" fill="[^"]+" mask=', svg).group(1)
    b = re.search(r'<mask[^>]*>.*?<path d="([^"]+)"[^>]*stroke-width="([\d.]+)"', svg, re.S)
    scale = height_px / 58.0
    size = int(round(64 * scale))
    s = render_svg(f'<path d="{stone}" fill="#fff"/>', size)
    cut = render_svg(f'<path d="{b.group(1)}" fill="none" stroke="#fff" stroke-width="{b.group(2)}"/>', size)
    lo, hi = int(round(3 * scale)), int(round(61 * scale))
    s, cut = s[lo:hi, lo:hi], cut[lo:hi, lo:hi]
    return s * (1 - cut), s


def mix(a, b, t):
    return a + (b - a) * t


def ground(width, height, c):
    """The ground: `bg`, lifted toward `panel` by a soft light, falling toward `sunken` at the edges."""
    bg, sunken, panel = (np.array(c[k], float) for k in ("bg", "sunken", "panel"))
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    d = np.hypot(xx / width - LIGHT_AT[0], (yy / height - LIGHT_AT[1]) * height / width)
    light = np.clip(1 - d / LIGHT_REACH, 0, 1) ** 2
    dc = np.hypot(xx / width - 0.5, (yy / height - 0.5) * 0.9)
    edge = np.clip((dc - 0.30) / 0.55, 0, 1) ** 1.4
    img = bg[None, None, :] + light[..., None] * ((panel - bg) * LIGHT_LIFT)[None, None, :]
    return img + edge[..., None] * ((sunken - bg) * EDGE_DROP)[None, None, :]


def lay_stone(img, c):
    """The stone, lit from above like the ground, lying over the ground at its centre."""
    height, width = img.shape[:2]
    matte, solid = mark_matte(int(round(height * STONE_HEIGHT)))
    h, w = matte.shape
    x0, y0 = int(round(width * STONE_AT[0] - w / 2)), int(round(height * STONE_AT[1] - h / 2))
    bg, panel, raised, border = (np.array(c[k], float) for k in ("bg", "panel", "raised", "border"))
    t = np.linspace(0, 1, h)[:, None, None]
    top, foot = mix(panel, raised, STONE_TOP), mix(bg, panel, STONE_FOOT)
    fill = mix(top[None, None, :], foot[None, None, :], t)
    # A hairline along the stone's outline, brightest at the top, gone by the foot.
    edge = solid - np.asarray(solid_eroded(solid), float)
    rim = (edge * np.linspace(1.0, 0.0, h)[:, None] * RIM_STRENGTH)[..., None]
    fill = fill + rim * (border - top)[None, None, :]
    region = img[y0:y0 + h, x0:x0 + w]
    region[:] = region * (1 - matte[..., None]) + fill * matte[..., None]
    return img


def solid_eroded(solid):
    """The stone's matte pulled in by about a pixel and a half, for its rim."""
    im = Image.fromarray((solid * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))
    return np.asarray(im, float) / 255


def dither(img, seed=7):
    """Round to 8 bits with triangular noise of one step, so no gradient breaks into bands."""
    rng = np.random.default_rng(seed)
    noise = rng.random(img.shape) + rng.random(img.shape) - 1.0
    return np.clip(np.round(img + noise), 0, 255).astype(np.uint8)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--height", type=int, default=1440)
    ap.add_argument("--no-stone", action="store_true", help="only the ground and its light")
    args = ap.parse_args(argv)
    c = theme_colours()
    img = ground(args.width, args.height, c)
    if not args.no_stone:
        img = lay_stone(img, c)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(dither(img)).save(args.out, optimize=True)
    print(f"{args.out} {args.width}x{args.height}")


if __name__ == "__main__":
    sys.exit(main())

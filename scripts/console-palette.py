#!/usr/bin/env python3
"""The Linux text console in the design system's colours, as kernel command line parameters.

Between the boot loader and the desk the screen is the kernel's console. Its background is colour 0
of the console's 16-colour palette, which is black unless told otherwise, so every start showed a
black screen before the desk. This prints the three parameters (vt.default_red, vt.default_grn,
vt.default_blu) that make colour 0 the desk's ground (`bg`) and the rest of the palette the system's
own tones, so a failure printed on the console is in the same colours as the rest of the system.

    scripts/console-palette.py            # the parameters, on one line
    scripts/console-palette.py --table    # the palette, one colour a line

The parameters are pasted into iso/efiboot/loader/entries/01-bombadil.conf (the live system) and
iso/airootfs/etc/default/grub.d/zz-bombadil-console.cfg (an installed one); tests/test_boot_console.py
fails when either differs from this output.
"""

import re
import sys
from pathlib import Path

THEME_QML = Path(__file__).resolve().parents[1] / "share" / "qml" / "Bombadil" / "Theme.qml"

# The console's 16 colours in its own order (ANSI: black, red, green, yellow, blue, magenta, cyan,
# white, then the bright row). A name is a token of Theme.qml; a #hex is a tone the console needs
# that no token names (a lighter tint of its neighbour, for text on the ground).
PALETTE = [
    ("black", "bg"),
    ("red", "bad"),
    ("green", "good"),
    ("yellow", "warn"),
    ("blue", "info"),
    ("magenta", "#9085e9"),       # the kit's chart series 7
    ("cyan", "#5cb3b0"),
    ("white", "fg"),
    ("bright black", "faint"),
    ("bright red", "badInk"),
    ("bright green", "#8fd19a"),
    ("bright yellow", "warnInk"),
    ("bright blue", "#8dbce6"),
    ("bright magenta", "#b6aef2"),
    ("bright cyan", "#8dd0cc"),
    ("bright white", "groundLight"),
]


def tokens():
    return dict(re.findall(r'readonly property color (\w+): "#([0-9a-fA-F]{6})"', THEME_QML.read_text()))


def colours():
    """[(name, "rrggbb")] for the 16 console colours."""
    t = tokens()
    return [(name, (t[src] if not src.startswith("#") else src[1:]).lower()) for name, src in PALETTE]


def parameters():
    cols = colours()
    out = []
    for param, i in (("vt.default_red", 0), ("vt.default_grn", 2), ("vt.default_blu", 4)):
        out.append(param + "=" + ",".join("0x" + hexa[i:i + 2] for _, hexa in cols))
    return " ".join(out)


if __name__ == "__main__":
    if "--table" in sys.argv:
        for i, (name, hexa) in enumerate(colours()):
            print(f"{i:2d}  #{hexa}  {name}")
    else:
        print(parameters())

"""The kit's tokens (share/qml/Bombadil/Theme.qml) as a dict, for the tests that check a colour."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME_QML = ROOT / "share" / "qml" / "Bombadil" / "Theme.qml"


def qml_theme():
    """{name: value} for every `readonly property <type> name: <literal>`: colours as "#AARRGGBB"
    or "#RRGGBB" strings exactly as written, numbers as int or float, strings unquoted."""
    out = {}
    for name, raw in re.findall(r'readonly property (?:color|int|real|string) (\w+): ("[^"]*"|[\d.]+)',
                                THEME_QML.read_text()):
        if raw.startswith('"'):
            out[name] = raw.strip('"')
        else:
            out[name] = float(raw) if "." in raw else int(raw)
    return out


def css(colour):
    """QML's #AARRGGBB as the design system's CSS #RRGGBBAA; #RRGGBB unchanged."""
    return "#" + colour[3:] + colour[1:3] if len(colour) == 9 else colour


THEME = qml_theme()

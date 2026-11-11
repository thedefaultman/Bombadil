"""Where the files of the bombadil package sit in the tree.

packaging/bombadil/root is a mirror of / for everything the package owns outside /usr/share/bombadil; the
tree itself (bin, src, shell, share) is at the top level. A test that reads a shipped file by path uses
these, so a move is one edit here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "packaging/bombadil/root"
# Bombadil's part of the Hyprland config: the package's, loaded by the stub in the home folder's hyprland.lua.
HYPR_LUA = ROOT / "share/hypr/bombadil.lua"

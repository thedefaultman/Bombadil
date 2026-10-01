# Bombadil's wallpaper

`bombadil.png` is the ground under the desk: the design system's own dark surfaces, lit softly from
above, with the mark lying very quietly in the middle. The bar shows it (`shell/Wallpaper.qml`, one
window per screen on the Background layer, so it is under every window, card and the pill). It is
drawn by `scripts/make-wallpaper.py` from the colour tokens in `share/qml/Bombadil/Theme.qml`; do not
edit the PNG by hand.

Design and reasoning: `docs/design/boot-and-wallpaper.md`.

## Use your own picture

Write the path of any image, on one line, into `~/.config/bombadil/wallpaper`:

```sh
echo ~/Pictures/mountains.jpg > ~/.config/bombadil/wallpaper
systemctl --user restart bombadil-shell     # the first time; after that the bar notices the file change
```

`~/` and `file://` paths work. Delete the file and the standard picture is back. A picture that is
missing or will not load falls back to the standard one. The picture fills the screen and is cropped to
its shape, so one with the same 16:9 proportions as the screen shows whole.

## Draw it again

```sh
pip install pillow numpy cairosvg
scripts/make-wallpaper.py                    # share/wallpaper/bombadil.png, 2560x1440
scripts/make-wallpaper.py --no-stone         # only the ground and its light
scripts/make-wallpaper.py --out ~/Pictures/ground.png --width 3840 --height 2160
```

`tests/test_wallpaper.py` fails when the picture no longer matches the tokens, so a changed colour
in `Theme.qml` means running the script again.

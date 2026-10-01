# Bombadil's wallpaper

`bombadil.png` is the ground under the desk: the design system's own dark surfaces, lit softly from
above, with the mark lying very quietly in the middle. The bar shows it (`shell/Wallpaper.qml`, one
window per screen on the Background layer, so it is under every window, card and the pill). It is
drawn by `scripts/make-wallpaper.py` from the colour tokens in `share/qml/Bombadil/Theme.qml`; do not
edit the PNG by hand.

Design and reasoning: `docs/design/boot-and-wallpaper.md`.

## Use your own picture

Write the path of any image on a line of its own in `~/.config/bombadil/wallpaper` (the file is there
already, with only comments in it):

```sh
echo ~/Pictures/mountains.jpg >> ~/.config/bombadil/wallpaper
```

The desk notices the change at once. The last line that is not a `#` comment is the one used, so
adding a line changes the picture. `~/` and `file://` paths work. It is the path of the picture, not
the picture itself: an image written into the file is ignored and the log says so. Empty the file or
delete it and the standard picture is back; a picture that is missing or will not load falls back to
the standard one too. On a system installed before the file was shipped, create it and start the bar
again once (log out and in), because a file that was not there when the bar started is not watched.

The picture fills the screen and is cropped to its shape, so one with the same 16:9 proportions as the
screen shows whole. A picture of your own is dimmed a little, toward the dark ground, so the cards and
the pill stay easy to read over it; the standard picture is not.

## Draw it again

```sh
pip install pillow numpy cairosvg
scripts/make-wallpaper.py                    # share/wallpaper/bombadil.png, 2560x1440
scripts/make-wallpaper.py --no-stone         # only the ground and its light
scripts/make-wallpaper.py --out ~/Pictures/ground.png --width 3840 --height 2160
```

`tests/test_wallpaper.py` fails when the picture no longer matches the tokens, so a changed colour
in `Theme.qml` means running the script again.

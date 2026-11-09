# Brand assets

> **Status:** Partly shipped
> **Pieces:** on `main`: the six files in `docs/brand/`, the stone in the pill with its nine faces, the icon files the image installs, the README banner and the wallpaper. On `main` but not installed: the GRUB screen's files. Designed, with no file or code: `LOGO=bombadil` in `os-release`, the greeter and the lock as the pill, and a separate 16 px colour drawing.
> **Code:** `docs/brand/bombadil-mark.svg`, `docs/brand/bombadil-tile.svg`, `docs/brand/bombadil-lockup.svg`, `docs/brand/bombadil-lockup-light.svg`, `docs/brand/bombadil-avatar.png`, `docs/brand/bombadil-social-preview.png`. The mark is used by `shell/Stone.qml`, `shell/PillState.qml`, `shell/shell.qml`, `README.md`, `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil.svg`, `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil-symbolic.svg`, `iso/airootfs/usr/share/pixmaps/bombadil.svg`, `share/wallpaper/bombadil.png`, `scripts/make-wallpaper.py` and `share/grub/bombadil/`.
> **Design:** [The Bombadil mark](../design/identity-brief.md)
> **Verified:** 2026-11-08 against `main` at `7176e55` The line numbers it cites were moved to the code at `ca7f80a` afterwards; the text was not read again. The stone's lean under reduced motion, and the link to the motion page, were added and checked against `main` at `f0485c2`.

Bombadil's mark is a stone with a lowercase b cut through it. It is the dot in the pill, where it says
what the machine is doing without a word, the `bombadil` icon that the image installs in its icon theme
and in pixmaps, the banner of the README and, very faintly, the middle of the wallpaper. The six files in
`docs/brand/` (besides this page) are listed below; every other place the mark appears is under
[Where the mark is used in the product](#where-the-mark-is-used-in-the-product). This page says how the
stone and the name are built, which colours the mark may take, what the identity brief allows and
forbids, how to change an asset without breaking a test or a copy, and
[what a fork replaces](#for-a-fork). Why the mark has this shape, and the options that were turned down,
are in [the identity brief](../design/identity-brief.md).

| File | What it is | Size and format | Used for |
|---|---|---|---|
| `docs/brand/bombadil-mark.svg` | The mark alone: the stone in `fg` ink with the b cut out, on a transparent ground. | SVG, `viewBox` `0 0 64 64`. The stone is 58 units wide (3 to 61). The b is a 5.6-unit stroke in a `<mask>`. | Anywhere the mark sits on a picture. `scripts/make-wallpaper.py` reads it for the wallpaper's stone, and `share/grub/bombadil/background.png` is a render of it. |
| `docs/brand/bombadil-tile.svg` | The mark on its dark tile: a `panel` rounded square with a `border` hairline, the stone centred on it. | SVG, `viewBox` `0 0 128 128`. The tile is 120 units square (4 to 124), corner radius 28. The stone is 84 units wide (22 to 106). The b is an 8.2-unit stroke in a `<mask>`. | The master of the installed icon, and the source of the two PNGs below. |
| `docs/brand/bombadil-lockup.svg` | The lockup for dark grounds: the mark, a 26-unit gap, then "bombadil" in drawn letters. The i is dotted with a small stone in `accent`. | SVG, `viewBox` `-92 -65.6 482 68`: 482 by 68 units. The mark is 64 units wide, the letters are about 63 units tall in 8.6-unit round-capped strokes, and the i's stone is 15.4 units wide. | The README banner when the viewer prefers a dark scheme. |
| `docs/brand/bombadil-lockup-light.svg` | The same lockup for light grounds: ink in `inkOnLight`, the i's stone in `accentOnLight`. | SVG, same `viewBox` and geometry as the dark lockup. | The README banner by default (`height="72"`, about 510 px wide). |
| `docs/brand/bombadil-avatar.png` | The tile as the repository's avatar. | PNG, 500 by 500, 8-bit RGB, no alpha, no text chunks. The tile is 470 px square (15 to 484) on a `bg` ground. | The repository's avatar. It is made for upload in the repository's settings; nothing in the tree shows whether it was uploaded. |
| `docs/brand/bombadil-social-preview.png` | The tile centred on `bg` as the repository's social picture. | PNG, 1280 by 640, 8-bit RGB, no alpha, no text chunks. The tile is 394 px square (x 443 to 836, y 123 to 516). | The repository's social preview, uploaded the same way. |

`docs/brand/README.md` is this page. Four of the SVGs (`bombadil-mark.svg`, `bombadil-tile.svg` and the
two lockups) cut the b with an SVG `<mask>`. `tests/test_brand.py` records that Qt's SVG renderer ignores
`<mask>`, so the b would vanish: no QML file loads these four, and the installed icons are mask-free
redraws.

## The mark

**The stone.** A rounded Reuleaux triangle: a triangle of constant width, so it rolls without bumping,
with its corners rounded. For a width W each corner has radius 0.15 W and each side is an arc of radius
0.85 W centred on the opposite corner's centre, so the outline is six arcs of two radii. It has the
same width in every direction, so its bounding box is a W by W square in any pose, and a third of a
turn (120 degrees) brings it back to exactly the same pose. It rests corner up, flat side down. Its
centroid sits 0.554 W below the top of its square, and that point is the pivot of the roll. Every
drawing in the repository uses the same two ratios:

| Drawing | Stone width W | Corner radius | Side radius | Cut (b stroke) | Cut as a share of W |
|---|---|---|---|---|---|
| `shell/Stone.qml`, in a 24 px slot | 18 | 2.7 | 15.3 | 1.6 | 0.089 |
| `docs/brand/bombadil-mark.svg` | 58 | 8.7 | 49.3 | 5.6 | 0.097 |
| `docs/brand/bombadil-tile.svg`, and the installed tile | 84 | 12.6 | 71.4 | 8.2 | 0.098 |
| the two lockups | 64 | 9.6 | 54.4 | 6.208 | 0.097 |
| the i's stone in the lockups | 15.4 | 2.31 | 13.09 | none | none |
| the `resting` and `offline` outlines in `shell/Stone.qml`, stroked 1.5 | 16.5 | 2.475 | 14.025 | the pill's 1.6 b | not applicable |
| `bombadil-symbolic.svg` | 16 | 2.4 | 13.6 | 1.5 | 0.094 |

The `resting` and `offline` outlines are the stone at 16.5, which is 18 less the 1.5 px stroke, so the
stroke's outer edge lands on the stone's edge. The stone is the same shape at every size, but the b is
drawn per size: the files are not scaled copies of `shell/Stone.qml`.

**The cut b.** A lowercase b cut clean through the stone: a straight, flat-topped stem and a bowl that is
half a pill (a bar, a half circle and a bar), meeting the stem in a square corner. Its ends are flat and
its corners are mitred (`capStyle: ShapePath.FlatCap`, `joinStyle: ShapePath.MiterJoin` in
`shell/Stone.qml`). The brief places it so that its reach from the stone's centroid stays inside the circle
that every pose of the stone covers, so the cut never breaks the stone's edge.
`test_the_b_stays_inside_the_stone_in_every_pose` renders the stone at six turn angles (0, 17, 45, 60, 90
and 120 degrees) and fails if any cut pixel has a ground pixel next to it. How the cut is made depends on
the file. In the pill the b is a stroke drawn in `ground` over the stone (or over the faint fill of the
`resting` outline), so it reads as a cut only on a flat surface of that colour (`Stone.ground`, default
`glassPill`); on the `offline` face it is a line in `bad`. In the four masked SVGs it is a transparent
hole. In the installed tile it is a stroke in the tile's colour, and in `bombadil-symbolic.svg` it is a
hole made by even-odd fill.

On the 24 px grid of `shell/Stone.qml` the stone's top corner is centred on (12, 5.7) and its centroid,
the pivot of the roll, is (12, 12.975). The b's centre line is
`M7.97 10.96H13.24A3.41 3.41 0 0 1 13.24 17.78H8.59V7.86`, stroked 1.6 px. The other drawings carry
their own b paths: the installed tile's is `M43.85 58.473H70.2A17.05 17.05 0 0 1 70.2 92.573H46.95V42.973`,
stroked 8.2.

**What stays level.** The b never tilts and never turns. While the machine works the stone turns a third
of a turn each second about its centroid and the b does not move, so the stone rolls around a b that
stays put, like a hub that never turns. In `shell/Stone.qml` the turn rotates only the stone's shape; the
b's shape is offset by `rideX` and `rideY` and has no rotation. In a lean or a knock the stone rocks on
its bottom arc (radius 15.3 about the top corner's centre) and the b slides with the stone's centre
without tilting. In the hop it hops and squashes with the stone. In the `resting` face the outline is
whole and grey and nothing moves. In the offline face the stone's outline is left as six red dashes and
the b stays in place as a red line.

The stone has nine faces in the pill: `rest`, `listening`, `working`, `starting`, `needs`, `done`,
`stopped`, `resting` and `offline`. `resting` is the whole outline in `muted` (1.5 px) over a fill of
`muted` at 30% opacity, with the b cut in `ground`, and it stands still. It shows while the AI is out of
plan or paused, and after a turn the limit cut off. What each face looks like and what sets it is in
[the design system](../design-system/README.md#the-pills-states) and [the shell](../architecture/shell.md#the-stone).
With reduced motion (`BOMBADIL_REDUCE_MOTION=1`) the roll becomes an opacity pulse, the needs-you glow
is held still, the lean is instant and the hop becomes a fade. The switch, and what it does to the rest of
the screen, is on [the motion page](../MOTION.md).

## The wordmark

The name is drawn, not set in a font. Both lockups carry the same geometry in `<path>` strokes: the
dark one in `fg` and `accent`, the light one in `inkOnLight` and `accentOnLight`. The numbers below
were read from `docs/brand/bombadil-lockup.svg`. Heights are measured up from the baseline, which is
`y = 0` in the file (the letters sit at negative `y`).

| Part | How it is drawn |
|---|---|
| The letters | Eight, `b o m b a d i l`, each an open stroke 8.6 units wide with round caps and round joins and no fill. |
| The round letters (`b`, `o`, `b`, `a`, `d`) | A ring of radius 16.5 on the stroke's centre line, its centre 20 units above the baseline. The ink runs from 0.8 below the baseline to 40.8 above it. |
| The `m` | Three vertical strokes 32 units apart, joined by two arches of radius 16 whose centres are 20.5 above the baseline. |
| The ascenders (`b`, `b`, `d`, `l`) | A stem from 58.86 to 4.3 above the baseline on the centre line: with the round caps, 63.16 units of ink, from the baseline up. |
| The x-height stems (`m`, `a`, `i`) | A stem from 35.7 to 4.3 above the baseline: 40 units of ink. |
| The `l` | Its foot is a quarter circle of radius 13.6 that turns right and ends 4.3 above the baseline. |
| The i | It has no dot of its own. A stone 15.4 units wide (corner radius 2.31, side radius 13.09, no cut) is centred on its stem, with its flat side about 9 units above the top of the stem's ink. This is the lockup's only orange. |
| The mark | The stone at 64 units wide sits on the baseline, from `y = -64` to `y = 0`, and ends 26 units before the first b's ink. |

The ink of the letters is about 387 units wide. The name is lowercase, `bombadil`, in the lockup and
"Bombadil" in prose; the command is `bombadil` ([the identity brief](../design/identity-brief.md#the-stone)).

## Colour

The mark is ink, and the colour is the state. The tokens are in `share/qml/Bombadil/Theme.qml`; the
files repeat some of them as literals.

| Token | Value | What the mark uses it for |
|---|---|---|
| `fg` | `#e6e8eb` | The stone's ink on a dark ground: `bombadil-mark.svg`, the tile, the dark lockup, the installed icon, the GRUB background. |
| `inkOnLight` | `#15181b` | The stone and the letters of the light lockup. |
| `groundLight` | `#f2f3f4` | The light page the light lockup is made for. No file draws it. |
| `accent` | `#d97757` | The stone while it works (`working`, and `starting`, which rolls the same way), and the i's stone in the dark lockup. |
| `accentOnLight` | `#b4532f` | The i's stone in the light lockup. `#d97757` on `#f2f3f4` is 2.81:1; `#b4532f` is 4.48:1. |
| `good` | `#5fb36b` | The stone at `rest`, `listening` and `done`. |
| `warn` | `#e0a93b` | The stone at `needs`, and its glow. |
| `bad` | `#d05555` | The `offline` outline and its b. |
| `muted` | `#8b939c` | The `stopped` square, and the `resting` outline with its 30% fill. |
| `panel` | `#1a1d21` | The tile's ground, and the b in the installed tile. |
| `border` | `#2a2f36` | The tile's hairline. |
| `bg` | `#101214` | The ground behind the tile in the avatar and the social preview, behind the mark in the GRUB background, and `desktop-color` in `share/grub/bombadil/theme.txt`. |
| `glassPill` | `#f01a1d21` (QML `#AARRGGBB`: `panel` at 94%) | The colour the b is cut in, in the pill. |

`bombadil-symbolic.svg` is filled `#bebebe`, which no token names; `tests/test_brand.py` requires it.

**The rule: the orange is only on the working stone and on the dot of the i.** The brief records that
`#d97757` is the exact colour of Claude's logo; Bombadil also runs Codex, so Bombadil is recognised by a
shape, never by an orange blob. In the files, orange appears in two places:
`docs/brand/bombadil-lockup.svg` (`#d97757`) and `docs/brand/bombadil-lockup-light.svg` (the darker
`#b4532f`). The two PNGs in `docs/brand/` and `share/grub/bombadil/background.png` contain no warm pixel
(no pixel with red above blue by more than 5). In `shell/Stone.qml` the only use of `accent` is the fill
of a rolling stone. The pill's border while a turn runs and the Stop button are `accent` too, but they
are not the mark. See
[colour says who](../principles.md#colour-says-who) and [original](../principles.md#original).

## Clear space and smallest size

The identity brief sets no clear space around the mark. It does give the sizes each drawing was made at,
and what the b is guaranteed. These are the brief's figures. The files and `shell/Stone.qml` carry the
stone widths and cut widths in the table above. The clearances come from the brief's own checks; the
repository's test is coarser, and fails if a cut pixel has a ground pixel among its eight neighbours.

| Where | Size | The b | Stone around the b, at the closest pose |
|---|---|---|---|
| The pill | Stone 18 px (x and y 3 to 21) in a 24 px slot | Scale 0.62, 1.6 px cut | 0.95 px in every pose |
| 16 px | Its own drawing: the stone full-bleed | Scale 0.56, 1.5 px cut | 0.7 px |
| 64 px | The mark at 64 px | | 1.3 px |
| The tile | The stone at 84 on the 128 tile | | 2.0 px |
| The lockup | The stone at 64 units on the baseline, a 26-unit gap, then the name | | |

At 16 px the b's bowl is about 2 px across: the brief says it holds at 1x, but only just. If the 18 px
stone makes the pill at rest feel too loud, the brief's fallback is a 16 px stone with the b at scale
0.55; that is an open question and nothing is built for it. A separate 16 px drawing in colour is
designed and has no file. The only 16 px file is `bombadil-symbolic.svg`: one colour, the stone filling
the 16 by 16 box, and a b with a 1.5 px stroke.

## Do and do not

From [the rules](../design/identity-brief.md#the-rules) and
[Cut, and why](../design/identity-brief.md#cut-and-why) in the identity brief. The brief does not name the
`resting` face; it is added below from `shell/Stone.qml`.

Do:

- Draw the mark in ink: `#e6e8eb` on the dark ground and `#15181b` on a light page.
- Let the stone take a colour only in the pill, and only the colour of its state. The one fixed touch of
  orange is the dot of the i.
- Use `#b4532f` for the i's stone on a light page, because `#d97757` on `#f2f3f4` is 2.8:1.
- Write the name lowercase, "bombadil", in the lockup and "Bombadil" in prose. The command is `bombadil`.
- Rest the stone corner up, flat side down.
- Keep every state readable without colour: rest is still, listening leans, working rolls, needs you
  knocks and glows, done hops once, stopped is a square, resting is a whole hollow outline, offline is a
  broken outline.
- Keep the b level: it never tilts, and it holds still while the stone turns.
- Give every movement one meaning (rolling is working, a knock is waiting on you, a hop is done, a lean
  is listening) and a still version for reduced motion.
- Use the mono mark, in black or white, for the stick's label and anything printed (designed: no file
  draws it).
- Keep the screen at rest to wallpaper and one pill with a still stone.

Do not:

- Make the mark orange, or put it on an orange or red tile. `#d97757` is Claude's colour, and an orange
  or red tile is the territory of Beats, Blogger and Ubuntu.
- Show the stone as a still orange logo. It is orange only while the machine works.
- Draw an amber ring for needs you. Next to the name Bombadil a gold ring is the One Ring; the glow is a
  soft filled one.
- Put a bare letter in the pill. It reads as a typed character; the b is cut through a stone, so it reads
  as a mark.
- Use the pill as a logo. A rounded bar with a dot reads as a toggle switch.
- Add a face, a mascot or anything from Tolkien's text or art: no hat, feather, boots, ring, runes or
  song lines. The character is in how the stone moves.
- Add a boot splash or a logo wallpaper. The shipped wallpaper does carry the stone, very faintly: the
  brief's "Where the mark goes" says the plain ground was later given a faint stone, and
  [the boot and wallpaper note](../design/boot-and-wallpaper.md#open-choice) calls it a taste choice that
  `scripts/make-wallpaper.py --no-stone` removes.

The brief judged the look-alike marks it names by eye and asks for a trademark search before a public
release. The decisions are D-1045 to D-1048, D-1050 and D-1080 to D-1087 in
[the decision log](../decisions/identity-and-design-system.md#d-1045).

## Where the mark is used in the product

| Place | Files | What shows | State |
|---|---|---|---|
| The pill | `shell/Stone.qml` draws it; `shell/shell.qml` puts it in `dotBox`, the pill's 24 px slot; `shell/PillState.qml` gives it its `face` | The stone, 18 px wide, in its nine faces. While a turn can be stopped, hovering it swaps it for the Stop button. | Shipped, tested |
| The README banner | `README.md` (lines 1 to 6), `docs/brand/bombadil-lockup.svg`, `docs/brand/bombadil-lockup-light.svg` | The lockup, 72 px high, dark or light by the viewer's colour scheme | Shipped, tested |
| The installed icon | `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil.svg` and `iso/airootfs/usr/share/pixmaps/bombadil.svg` (byte-identical): the tile, 128 by 128, with the b as a `panel` stroke instead of a mask. `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil-symbolic.svg`: 16 by 16, one path, `#bebebe`, even-odd. | An icon named `bombadil` in the icon theme and in pixmaps. `iso/airootfs/` is the image's overlay, and the installer copies the live root (`cp -ax /. /mnt/`, `iso/airootfs/usr/local/bin/bombadil-install:32`), so an installed system has the same files. Nothing in the repository selects the icon by name: `bombadil-browser.desktop` sets no `Icon=`. | The files are shipped and tested; nothing uses the icon |
| The wallpaper | `share/wallpaper/bombadil.png` (2560 by 1440), drawn by `scripts/make-wallpaper.py` from `docs/brand/bombadil-mark.svg`, shown by `shell/Wallpaper.qml` | The stone lying very quietly in the middle: between `panel` and `raised` at its top (85% of the way from `panel` to `raised`), and at its foot 70% of the way from `bg` to `panel`, with a hairline of `border` along its top. No orange. | Shipped, tested |
| GRUB's password screen | `share/grub/bombadil/background.png` (1920 by 1080), `theme.txt`, `README.md` | The mark in `fg` on `bg`, 182 px square, centred, with its top at y = 289, and GRUB's own line below it | Files only. They are in the tree and `test_the_grub_theme_names_its_files_and_the_ground` checks them, but nothing installs them: no script or `iso/` file refers to `share/grub/bombadil` (`bombadil-install` does not), although the folder's own `README.md` says the installer copies it to the ESP. The whole `share/` tree is on the image at `/usr/share/bombadil/share/`, so the folder is there, but the one GRUB drop-in (`iso/airootfs/etc/default/grub.d/zz-bombadil-console.cfg`) sets kernel parameters only and nothing sets `GRUB_THEME`, and `bombadil-install` sets `GRUB_TERMINAL_OUTPUT=console` (`iso/airootfs/usr/local/bin/bombadil-install:75`), so an installed system's GRUB draws text. |
| The repository page | `docs/brand/bombadil-avatar.png`, `docs/brand/bombadil-social-preview.png` | The tile | Made for upload in the repository's settings; not checkable from the tree |

Designed, with no drawing in `shell/`, `share/` or `iso/`: the lockup on a download page; the 16 px mark in
ink on the install card and the recovery key card; the mono mark on the stick's label; the pill itself as
the greeter and the lock (`iso/airootfs/etc/greetd/config.toml` starts Hyprland with no greeter);
`LOGO=bombadil` in `os-release` (the image has no `os-release` of its own under `iso/airootfs/etc/`); a
separate 16 px colour drawing. The stick's boot menu has no mark by design: systemd-boot draws text only
(`iso/efiboot/loader/entries/01-bombadil.conf`).

## Changing it

No script in the repository draws the files in `docs/brand/`, the installed icons or the GRUB
background. The SVGs are edited by hand, and the PNGs are renders of them. The one generator that reads
a brand file is `scripts/make-wallpaper.py`. Nothing compares the SVG drawings with `shell/Stone.qml`. The
files in `docs/brand/` are checked only by `tests/test_brand.py` (the four SVGs parse, the PNGs have the
right size, the README's banner files exist) and, for `bombadil-mark.svg`, by the wallpaper tests.

| You change | Then redo | How |
|---|---|---|
| The stone or the b in `shell/Stone.qml` (the stone path in `rockShape`, the b path used three times, for the cut, the `resting` face and the `offline` face, and the outline path used twice, for `resting` and `offline`) | The drawings in the SVG files that should match it | By hand. The paths are literal strings: `bombadil-mark.svg` (64 grid), `bombadil-tile.svg` and the two installed tiles (128), both lockups (stone, b and the i's stone), `bombadil-symbolic.svg` (16). |
| `bombadil-mark.svg` | The wallpaper and the GRUB background | `scripts/make-wallpaper.py`, and the render below |
| `bombadil-tile.svg` | The two PNGs and the installed tile | The renders below; edit the installed copy by hand (the stone and the b path are identical, the b is a stroke in `panel` and the `<mask>` is gone) and keep the two copies identical. |
| A token the mark uses | The literal copies in the SVGs and in `share/grub/bombadil/theme.txt` | By hand. [Changing a token](../design-system/README.md#changing-a-token) lists which copies a test covers. |

**The wallpaper.** `scripts/make-wallpaper.py` needs `pip install pillow numpy cairosvg`. It reads the
colours from `Theme.qml` and the stone from `docs/brand/bombadil-mark.svg`. It finds the stone with the
pattern `<path d="..." fill="..." mask=` and the b as the first `<path d="...">` after `<mask`, which must
carry a `stroke-width`. Keep that structure in the SVG.

```sh
scripts/make-wallpaper.py                 # share/wallpaper/bombadil.png, 2560 by 1440
scripts/make-wallpaper.py --no-stone      # the ground and its light only
```

**The PNGs.** Render the SVG in a renderer that honours `<mask>`, such as a browser, in a page whose
background is `#101214`, with no margin and at a device scale factor of 1. On 2026-11-08 and again on
2026-11-09 headless Chromium reproduced all three files with no differing pixel:

| File | Source | Page | Box the SVG is drawn in |
|---|---|---|---|
| `docs/brand/bombadil-avatar.png` | `bombadil-tile.svg` | 500 by 500 | 500 by 500 px, filling the page |
| `docs/brand/bombadil-social-preview.png` | `bombadil-tile.svg` | 1280 by 640 | 420 by 420 px, centred |
| `share/grub/bombadil/background.png` | `bombadil-mark.svg` | 1920 by 1080 | 200 by 200 px, its left edge at x = 860 (centred) and its top at y = 280 |

**The installed icons.** Keep `bombadil.svg` in `hicolor/scalable/apps` and in `pixmaps` byte-identical,
with no `<mask>` and no `mask=`: Qt ignores masks. The tile's ground and the b's stroke must be `panel`
and the stone `fg`; the symbolic icon must keep `fill-rule="evenodd"`, `#bebebe` and its 16 by 16
`viewBox`.

**Traps in the stone.**

- Anything that starts the bar must run `bin/bombadil-shell`. `shell.qml` reads the kit's colours by
  `import Bombadil as Kit`, and Quickshell resolves that import only from its own folder or from
  `QML2_IMPORT_PATH`, which that script sets
  ([the shell](../architecture/shell.md#theme-and-why-the-shell-starts-through-binbombadil-shell)).
  Started any other way, every `Kit.Theme.x` is undefined and the pill is white. In the image, and so on an
  installed system, the user unit `bombadil-shell.service` (`iso/airootfs/etc/systemd/user/`) runs
  `/usr/local/bin/bombadil-shell`, a link to that script, and starts it again if it dies; Hyprland's start
  hook restarts the unit when Hyprland starts
  (`iso/airootfs/etc/skel/.config/hypr/hyprland.lua:10`). To start the bar again after a change to
  `Stone.qml`, run `systemctl --user restart bombadil-shell`; Super+Ctrl+Esc does the same
  (`hyprland.lua:69`). `test_the_shell_gets_the_kit_on_its_import_path` and
  `test_nothing_launches_quickshell_without_the_kit_path` in `tests/test_theme.py` pin the launcher, and
  the headless desktop test fails on any QML error in the shell's log (`the shell logged no QML errors`
  in `tests/desktop/driver.py`).
- The first frames lose a colour change. The comment in `shell/Stone.qml` says the scene graph drops a
  colour change made in the first frames after the bar starts: on a VM the face went `starting`, then
  `rest` 39 ms later, and the stone stayed the starting orange until its next change, a hundred seconds
  on. So for the first 400 ms (`Stone.settle`) the stone is painted resting green whatever its face is,
  then follows it. The comment gives no reason why the scene graph drops it. Keep the workaround while the
  behaviour holds: `test_the_first_frames_keep_the_resting_green_then_the_face_shows` and
  `test_a_face_that_waits_is_amber_once_the_stone_has_settled` pin it.
- The needs-you glow is a filled radial fade of radius 16 about the centroid: 5 px past the stone's edge,
  into the pill's padding. At radius 11 it was a 1 px halo that did not read at 24 px.
  `test_the_glow_reaches_past_the_slot_and_fades_out` pins the reach.
- `shell/*.qml` may hold no hex colour and no `"white"` or `"black"`: `tests/test_theme.py` fails on it,
  so every face takes its colour from a `Theme` token.
- To add a face, follow [the design system's recipe](../design-system/README.md#3-add-a-state-to-the-stone).

**Tests.** The QML tests skip without PySide6; the two wallpaper tests that redraw the picture also need
`numpy`, Pillow and `cairosvg`.

| Test file | What it guards |
|---|---|
| `tests/test_stone_qml.py` (17 tests) | `shell/Stone.qml` rendered offscreen at 1x and read back pixel by pixel: the colour of every face, the b inside the stone in every pose, a third of a turn leaving the stone where it was, the b's stem never moving while it works, two knocks and the glow's reach, the lean, one hop, reduced motion, the first 400 ms, and the `resting` outline: whole, still and unlike every other face |
| `tests/test_brand.py` (5 tests) | The installed icons (identical in `hicolor` and `pixmaps`, no mask, tile colours, the symbolic icon's fill rule), the README banner (it opens with `<p align="center">`, its image files exist and a dark source is named), the PNG sizes (1280 by 640, 500 by 500, 1920 by 1080), that the four SVGs parse, the GRUB theme's file names and ground |
| `tests/test_motion.py` | Among the rest of reduced motion, that the stone leans at once under it, and that the shell binds the switch to `Kit.Theme.reducedMotion` and hands it on to the stone and to the wallpaper ([how it is tested](../MOTION.md#how-to-test-it)) |
| `tests/test_wallpaper.py` (14 tests) | The wallpaper picture against the tokens, and that `scripts/make-wallpaper.py` redraws it from `bombadil-mark.svg` |
| `tests/test_pill_qml.py`, `tests/test_desk_qml.py`, `tests/test_rest_qml.py` | Which face the pill gives: `starting`, `offline`, `rest`, `working`, `done`, `stopped` and `needs` (`tests/test_pill_qml.py`), `needs` while a session waits (`tests/test_desk_qml.py`), and `resting` while the AI is out of plan, paused or cut off by the limit (`tests/test_rest_qml.py`) |
| `tests/desktop/driver.py` | The real bar in headless sway: the stone's pixels at rest, while a turn runs, needing you and while the AI rests, and that the shell's log holds no QML error |

```sh
pytest -q tests/test_stone_qml.py tests/test_brand.py tests/test_wallpaper.py tests/test_motion.py
BOMBADIL_SCREENS=/tmp/stone pytest -q tests/test_stone_qml.py   # stone-<face>.png at 8x
```

`BOMBADIL_SCREENS` saves `rest`, `listening`, `working`, `needs`, `stopped`, `resting` and `offline`;
`done` and `starting` are not saved. On a running VM, `scripts/vm-tools/stonecrop OUT.png N INTERVAL`
takes N frames of the stone from the VM's own screen over QMP and lays them in a row.

### For a fork

[Making it yours](../forking.md#what-carries-the-identity) lists what carries the identity and what to
keep. To replace the whole brand pack, redo these in this order:

1. **The stone.** Redraw the stone, the b and the outline in `shell/Stone.qml`: the geometry there is the
   master the files copy.
2. **The six files in `docs/brand/`.** Redraw `bombadil-mark.svg` and `bombadil-tile.svg` from it, and both
   lockups (they draw the name in paths, so another name means redrawing the letters as well as the i's
   stone: see [the wordmark](#the-wordmark)). Keep the structure `scripts/make-wallpaper.py` reads in `bombadil-mark.svg`. Render
   `bombadil-avatar.png` at 500 by 500 and `bombadil-social-preview.png` at 1280 by 640, as in the table
   above.
3. **The installed icons.** Redraw `bombadil.svg` without a mask and keep its two copies identical, and
   the one-colour `bombadil-symbolic.svg`.
4. **The README banner.** Point the `<picture>` in the first six lines of `README.md` at the new
   lockups. `README.md` must begin with `<p align="center">`; every `src` and `srcset` in its first 600
   characters must be a file that exists, and `prefers-color-scheme: dark` must appear in those
   characters (`test_the_readme_opens_with_the_lockup_for_dark_and_light_pages`).
5. **The wallpaper.** Run `scripts/make-wallpaper.py`. The picture must stay 16:9, at least 2560 pixels
   wide, 8-bit RGB with no alpha and under 6 MB.
6. **GRUB.** Render `share/grub/bombadil/background.png` at 1920 by 1080 and keep `desktop-color` in
   `theme.txt` equal to `bg`, with `desktop-image: "background.png"` and `terminal-font: "Inter Regular 16"`
   as `test_the_grub_theme_names_its_files_and_the_ground` expects. Nothing installs the theme, so wiring it in is your work.
7. **The colours.** Change `share/qml/Bombadil/Theme.qml` first and follow
   [recipe 8](../forking.md#8-change-the-colour). `tests/test_theme.py:72` pins `accent` at `#d97757`: edit
   that line on purpose if you change the orange.
8. **This page.** Update its tables with the new numbers.

Then run `pytest -q tests/test_brand.py tests/test_stone_qml.py tests/test_wallpaper.py tests/test_theme.py`
and `python3 docs/tools/check_docs.py`.

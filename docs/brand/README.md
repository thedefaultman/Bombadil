# Bombadil's mark

The mark is a stone with a lowercase b cut through it. The stone is a rounded Reuleaux triangle (a
triangle of constant width, so it rolls without bumping), 18 px wide on a 24 px grid, resting on one
corner. The b is the surface the stone sits on, seen through it: it is drawn in the ground's colour,
1.6 px wide, flat-ended and square-cornered, and it never tilts, so when the stone turns it looks as
if the stone rolls around a b that stays put.

In the machine the mark is the pill's dot. It says what the machine is doing without a word.

## Files

| File | What it is |
|---|---|
| `docs/brand/bombadil-mark.svg` | The mark alone, on a transparent ground. |
| `docs/brand/bombadil-tile.svg` | The mark on its dark tile, for icons. |
| `docs/brand/bombadil-lockup.svg`, `-lockup-light.svg` | The name in drawn letters with a small stone as the i's dot, for dark and light pages (the README banner). |
| `docs/brand/bombadil-avatar.png`, `-social-preview.png` | The repository's avatar and social preview, for upload in its settings. |
| `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil{,-symbolic}.svg`, `iso/airootfs/usr/share/pixmaps/bombadil.svg` | The installed icon. None uses an SVG `<mask>`, which Qt's renderer ignores; the b is drawn in the tile's colour, or cut out with even-odd fill in the symbolic one. |
| `share/grub/bombadil/` | The theme for the password screen of an installed disk (its own README). |
| `shell/Stone.qml` | The mark in the pill, drawn and animated. The geometry in it is the drawing in the files above. |
| `tests/test_stone_qml.py`, `tests/test_brand.py` | Every face read back pixel by pixel; the files' colours and sizes. |

## Colours

Only the shared tokens (`share/qml/Bombadil/Theme.qml`), so the pill, the desk and the apps agree.

| Token | Value | Where the mark uses it |
|---|---|---|
| `good` | `#5fb36b` | the stone at rest, listening and done |
| `accent` | `#d97757` | the stone while it works, and the i's dot in the lockup; nowhere else |
| `warn` | `#e0a93b` | the stone when the machine waits on you, and its glow |
| `bad` | `#d05555` | the offline outline and its b |
| `muted` | `#8b939c` | the stopped square |
| `glassPill` | `#1a1d21` at 94% | the colour the b is cut in (`Stone.ground`) |

## The faces

`PillState.face` is the one word for what the machine is doing; `shell.qml` passes it to the stone,
and shows `listening` over `rest` while the screen holds the keyboard.

| Face | The stone | Set when |
|---|---|---|
| `rest` | green, still | nothing is going on |
| `listening` | green, leans 6 degrees toward the text (240 ms) | the screen that holds the keyboard is up |
| `working` | orange, a third of a turn a second about its centroid (hold 300 ms, turn 120 degrees in 550 ms, hold 150 ms) | a turn runs or a sign-in is under way |
| `starting` | the working roll, with "Starting" in the field | since boot, until agentd first answers |
| `needs` | amber, two 8-degree knocks every 1.6 s, with a glow that breathes | a coding session asks, or setup waits on the person |
| `done` | green, one 700 ms hop with a squash on landing | a turn just finished |
| `stopped` | the Stop button's own grey square | the closing line says the turn was stopped |
| `offline` | the outline alone, dashed, red; the b stays | agentd was there and is gone, or never came |

Reduced motion (`BOMBADIL_REDUCE_MOTION=1`) turns the roll into a pulse (opacity 1, 0.3, 1 a second),
holds the glow still at 0.45 and replaces the hop with a fade.

## Drawing it

The stone's path is on the 24 px grid: corner radius 2.7, side radius 15.3, the top corner centred on
(12, 5.7), the centroid on (12, 12.975), which is the pivot of the roll. Leaning and knocking rock the
stone on its bottom arc (radius 15.3 about the top corner's centre) and slide the b with it, so the b
stays level. The b's centre line is `M7.97 10.96H13.24A3.41 3.41 0 0 1 13.24 17.78H8.59V7.86`. Clearance
between the b and the stone's edge is 0.95 px in every pose; `test_the_b_stays_inside_the_stone_in_every_pose`
holds it there.

## What a real machine taught us

- **The shell reads the kit's colours by import, not by path.** Quickshell resolves a QML singleton
  only from inside its own config tree or from the QML import path, so `shell.qml` has
  `import Bombadil as Kit` and `bin/bombadil-shell` puts `share/qml` on `QML2_IMPORT_PATH`. Anything
  that starts the bar must run `bombadil-shell`; started any other way, every `Kit.Theme.x` is
  undefined and the pill is white. The headless desktop test fails on any QML error in the shell's log.
- **The first frames lose a colour change.** On the VM the face went `starting`, then `rest` 39 ms
  later, and the stone stayed the starting orange (217, 119, 87) until its next change, a hundred
  seconds on, though every value in QML was right. The scene graph dropped the change. So for the first
  400 ms (`Stone.settle`) the stone is painted resting green whatever its face says, then follows it.
  The cause is in the renderer and is not understood; the test
  `test_the_first_frames_keep_the_resting_green_then_the_face_shows` keeps the workaround honest.
- **The needs-you glow was a 1 px halo.** A glow of radius 11 around a stone that already fills 10
  does not read at 24 px. It is now radius 16: 5 px past the stone, into the pill's padding, and gone
  by 16 px from the centroid.

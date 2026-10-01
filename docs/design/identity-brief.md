# The Bombadil mark

> **Status:** Partly shipped
> **Code:** `shell/Stone.qml`, `shell/PillState.qml`, `shell/shell.qml`, `share/qml/Bombadil/Theme.qml`, `shell/DeskTheme.js`, `docs/brand/`, `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil.svg`, `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil-symbolic.svg`, `iso/airootfs/usr/share/pixmaps/bombadil.svg`, `share/grub/bombadil/`, `README.md`
> **Pieces:** piece 1 (one set of tokens for the shell and the kit) is shipped. Piece 2 (the mark in the machine) is partly shipped: the pill's stone and its states, the installed icons and the README banner are on `main`. The GRUB theme's files are on `main` but nothing installs them. `LOGO=bombadil` in `os-release`, the greeter and the lock as the pill, and a separate 16 px colour drawing are designed, with no file or code for them.
> **Design:** this brief and its page, [The Bombadil mark](pages/bombadil-mark.html); [Brand assets](../brand/README.md); [Design system](../design-system/README.md#the-mark-and-its-states); [Shell](../architecture/shell.md)
> **Decided by:** the project owner, 2026-09-30 (the mark: E, the riding b; the orange stays, the owner not objecting to the recommendation); the other choices under "Defaults picked" are defaults
> **Verified:** 2026-10-01 against `main` at `a30ebc8`: the files named above exist, `iso/airootfs/etc/os-release` does not exist and nothing under `iso/` or `scripts/` mentions the GRUB theme, so the two gaps above hold. The checks listed under "Checked" are the brief's own and were not re-run.

The mark is a stone with a lowercase b cut through it, and it is the pill's dot. This brief is the
design record: what was asked, the options that were drawn, what was chosen and why, where the mark goes,
and what was cut. How the stone is drawn and built today is in [`docs/brand/README.md`](../brand/README.md);
the look it belongs to is in [`design-system.md`](design-system.md); the options and the mock-ups of every
state are on the page, [`pages/bombadil-mark.html`](pages/bombadil-mark.html).

You press the power button. The firmware's logo goes, and the screen turns the dark grey of the desktop.
In the middle sits a smooth three-sided stone in light ink with a lowercase b cut clean through it, and
under it one line: "Type your password to start Bombadil." You type it. A second later the desktop is
there, wallpaper and one pill at the bottom, and the stone at the pill's left end is orange and rolling,
with the word "Starting". Then it settles, green, flat side down, before "Ask anything".

You start typing and the stone leans a little toward your words. You press Enter and it turns orange and
rolls a third of a turn every second, and the b in it stays level and still while the stone turns around
it: you ride level while the machine does the work. When the turn ends having changed something, it turns
green, gives one small hop and a squash as it lands, and sits still. When a coding session waits on you,
it turns amber, knocks twice, and waits with a soft glow breathing behind it. Press Esc and it becomes the
Stop button's grey square. Lose the agent and only its broken red outline is left, with the b still in
it. Each of those reads in a black-and-white screenshot, which the old green, orange and red dot did not.

The same stone sits on the download page and the README beside the name "bombadil" in round drawn letters
whose i is dotted with a small orange stone, and on the app tile, the stick's label and the install card.
The mark is never orange, and never on an orange tile.

## What was asked and what was decided

The owner asked for a logo, a design identity and a design system, with a few options and how each looks
in different states, keeping the look the system already had. Round one offered three marks: A, the b; B,
the dot on the i; C, the pebble. B and C were liked: B's letters, and C's animation, though C did not yet
say Bombadil to someone who saw it, so round two started from the pebble and its motion and added identity
and personality. It offered three options, D, E and F (below).

1. **The mark is E, the riding b.**
2. **The orange stays `#d97757`.** The marks are drawn in ink, and orange appears only on the stone while
   the machine works and on the dot of the i.

## The rules

1. **The stone is the machine.** The dot in the pill is the one piece of Bombadil everybody sees every
   day. The stone takes its place and does what the dot did, with a character.
2. **The mark is ink; colour is the state.** Drawn in `#e6e8eb` on the dark ground and `#15181b` on a
   light page. The stone takes a colour only in the pill, and only the colour of its state; the one fixed
   touch of orange is the dot of the i. `#d97757` is the exact colour of Claude's logo and Bombadil also
   runs Codex, so Bombadil is recognised by a shape, never by an orange blob.
3. **Colour says who, everywhere.** White is you, orange is the machine acting, blue is a coding session.
   Amber is a step that touches the system or something waiting on you, red is failed, offline or can't be
   undone, green is connected.
4. **Every state reads without colour.** Rest is still, listening leans, working rolls, needs you knocks
   and glows, done hops once, stopped is a square, offline is a broken outline.
5. **At rest, quiet.** The screen at rest stays wallpaper and one pill with a still stone. No splash, no
   logo wallpaper.
6. **Things that speak are round; things that hold have 12 px corners.** The pill, the line and the chips
   are fully round; windows, cards and panels are 12; controls are 8.
7. **Every movement means one thing.** Rolling is working, a knock is waiting on you, a hop is done, a
   lean is listening. Nothing else moves, and each has a still version for reduced motion.
8. **Original.** Nothing from Tolkien's text or art (no hat, feather, boots, ring, runes or song lines),
   no face and no mascot. The character is in how the stone moves, never in a face.

## The stone

All three round-two options use one shape: a rounded Reuleaux triangle of width W. Its corners have
radius r = 0.15 W and its sides are arcs of radius 0.85 W, each centred on the opposite corner's centre,
so the whole outline is six arcs of two radii. It has the same width in every direction, so its bounding
box is a W x W square in any pose: it can turn inside a square while touching all four sides, and a third
of a turn brings it back to exactly the same pose. It rests corner up, flat side down. Its centroid sits
0.554 W below the top of its square, and the circle every pose covers has radius 0.446 W around the
centroid.

**The character, shared by all three.**

- **Rest:** green `#5fb36b`, still.
- **Listening:** green, leans 6 degrees toward the text over 240 ms on the curve (0.16, 1, 0.3, 1) and
  stays leaning while you type. The lean is a roll on its bottom arc: a rotation about the top corner's
  centre plus a slide of 0.85 W times the angle in radians.
- **Working:** orange `#d97757`, a third of a turn per 1 s beat: hold 300 ms, turn 120 degrees in 550 ms
  on (0.55, 0, 0.3, 1), hold 150 ms. D and F roll inside their square; E turns about its centroid so the b
  can stay still. Reduced motion: an opacity pulse, 1 to 0.3 to 1 per second.
- **Needs you:** amber `#e0a93b`. Knocks twice, tipping 8 degrees to the right and landing back, then
  waits, on a 1.6 s beat. Behind it a filled radial glow breathes 0.25 to 0.6. Never a ring (next to the
  name Bombadil a gold ring is the One Ring). Reduced motion: still, glow held at 0.45. *As built, the
  glow reaches 16 px from the stone's centre: at the first brief's 11 px it was a 1 px halo at 24 px.*
- **Done:** green, 700 ms, played once: a squash to (1.06, 0.9) about its foot, a 3 px hop, a landing
  squash to (1.1, 0.84), a small rebound, still. Reduced motion: a 600 ms fade in from 0.3.
- **Stopped:** the Stop button's own square in muted `#8b939c`. Never orange.
- **Offline:** the outline alone, 1.5 px red `#d05555` with round caps, broken into six dashes, one
  centred on each corner and each side. The pill's border is `#7a2e2e`.
- **Starting:** the working roll with the word "Starting".

**The name, shared by all three.** Lowercase "bombadil" in round-capped 8.6-unit strokes, rings and arcs.
The dot on the i is a small stone (W 15.4 on letters 63 units tall), `#d97757` on dark, `#b4532f` on
light, and it is the only orange in the lockup. Lowercase "bombadil" in the lockup, "Bombadil" in prose;
the command is `bombadil`.

## The options

### D. The merry pebble

Round one's pebble exactly as it was, 12 px in the pill where the 10 px dot sat, with the character
above. It gets its identity from the company it keeps: the lockup, the download page and the README always
show it beside the name, whose i is dotted with the same stone.

*Why it works.* The smallest step from the pebble that was liked, the quietest pill, and the clearest
shape at 16 px. *Its weak spot.* Seen on its own it is still a rounded triangle: a guitar pick, a
gumdrop, and close to Vercel's triangle at 16 px on a dark tile. The character only shows while it moves,
so a screenshot, a USB stick or an app icon does not yet say Bombadil.

### E. The riding b (chosen)

The stone with a lowercase b cut clean through it: A's constructed b (a straight flat-topped stem and a
bowl that is half a pill, meeting in a square corner). When the machine works, the stone turns around the
b while the b stays level and still, like a hub that never turns. The b's point (11.5, 12.25) on its
24-unit grid sits on the stone's centroid, and its reach from there stays inside the circle every pose
covers, so the cut never breaks the edge. In the pill the stone is 18 px (x and y 3 to 21) and the b is
scale 0.62 with a 1.6 px cut, with at least 0.95 px of stone around it in every pose.

The b rides: in the knock and the lean it slides with the stone's centre but never tilts; in the hop it
hops and squashes with the stone. Offline keeps the b as a red line inside the broken outline: the stone
is gone, the b is still there. Sizes: 16 px has its own drawing (the stone full-bleed, the b at scale 0.56
with a 1.5 px cut); the tile is the stone at 84 on the 128 tile; the lockup is the stone at 64 units on the
baseline, a 26-unit gap, then the name.

*Why it works.* It says Bombadil wherever it appears, on its own: the app tile, the stick, GRUB's screen
and the pill. It keeps what was liked about the pebble (the shape, the roll, the calm), and the motion
shows the idea at a glance: the letter holds still while the stone works, which is the passenger idea in
one picture. *Its weak spot.* The pill's mark grows from a 10 px dot to an 18 px stone with a letter in
it, so the resting pill is a little louder. A bare letter in the pill reads as a typed character; a b cut
through a stone reads as a badge instead, but it is a letter all the same. A letter on an orange shape
sits near Blogger's orange B, so the stone is only orange while it turns, never as a still logo. At 16 px
the b's bowl is about 2 px across; it holds at 1x, but only just.

### F. The stone b

The stone as the bowl of a lowercase b, beside a straight stem. While the machine works it rolls against
the stem; when it needs you it knocks on the stem. In the name it is the first letter.

*Why it works.* The most playful of the three and the best lockup. *Its weak spot.* On its own the b has
a pointed bowl that reads as an l beside a fin, and at 16 px it comes apart into a bar and a triangle. In
the pill the stem stands right before the text, where it can read as the text cursor. Turned on its side
it becomes a media player's skip button, so it only ever stands corner up.

## The orange

Bombadil's accent is `#d97757`. Claude's favicon is filled `#D97757`, and Anthropic's brand uses the same
value as its primary accent. Bombadil also runs Codex, so an orange mark would read as Claude's and be
wrong half the time.

- **Keep it (chosen).** Every mark is ink; the orange appears on the stone only while the machine works,
  and as the dot of the i. The working border, the pulse and Stop stay orange. On a light page the i's
  stone uses `#b4532f`, because `#d97757` on `#f2f3f4` is 2.8:1.
- **Change it.** Pick a machine colour of our own. That restyles the working border, Stop, the pulse, the
  desk's orange marks and every design page: the part of the look the owner said they like.

## Where the mark goes

- **The pill.** The states above. The pill at rest is the old pill with the stone in the dot's seat.
- **GRUB, installed.** A drop-in under `/etc/default/grub.d/` sets `GRUB_DISTRIBUTOR="Bombadil"`,
  `GRUB_TERMINAL_OUTPUT=gfxterm`, `GRUB_GFXMODE=auto` and `GRUB_THEME=/efi/grub/themes/bombadil/theme.txt`.
  The theme lives on the ESP, because `grub.cfg` is read there before the disk is unlocked: a background
  PNG of the ground with the mark, and Inter converted with `grub-mkfont`. The files are
  `share/grub/bombadil/` (its README has the commands). GRUB prints its own "Enter passphrase for ..." when
  it unlocks a disk; our line "Type your password to start Bombadil." replaces it only if `grub.cfg` asks
  with `read -s` and passes the answer to `cryptomount -p`. Whether `grub-mkconfig`'s own `cryptomount`
  still prompts after that is for a VM to check; if it does, the screen shows the mark with GRUB's line,
  which is acceptable.
- **The stick's boot menu.** systemd-boot draws text only, so no mark there. The firmware's own logo shows
  before it.
- **No boot splash.** Plymouth stays cut. GRUB's still screen carries the mark, and the pill says Starting
  for the second before agentd answers.
- **os-release.** `NAME="Bombadil"`, `PRETTY_NAME="Bombadil"`, `ID=bombadil`, `ID_LIKE=arch`,
  `LOGO=bombadil`. Check first that nothing on the image tests `ID=arch` without reading `ID_LIKE`.
- **After a logout, and the lock.** The pill itself: Quickshell has a greetd service and a session lock,
  so the same pill asks for the password, its stone amber and knocking because it waits on you, above one
  line "Type your password." If a Quickshell greeter cannot be packaged cleanly, hyprlock with the ground
  colour, the mark and the pill's field is the fallback.
- **The install card and the recovery key card.** Cards in the kit's look; the 16 px mark takes the card's
  icon place, in ink.
- **The download page, the README and the repository.** The lockup, one sentence and one button; the
  README banner in a dark and a light copy; the tile as the repository's avatar and social picture.
- **The stick's label and anything printed.** The mono mark in black or white, never orange.
- **The wallpaper.** The plain ground `#101214` (later given a faint stone; see
  [`boot-and-wallpaper.md`](boot-and-wallpaper.md)).

## Defaults picked

- **The mark: E.** D is the quietest but does not say Bombadil on its own; F has the best lockup but the
  weakest mark.
- **Keep `#d97757`.** The alternative restyles the look the owner likes.
- **The name in round one's drawn letters with a stone for the i's dot**, in all three options.
- **A listening lean.** New in round two: the stone leans toward your words while you type.
- **A "Starting" state.** The old pill showed the offline red and "Waiting for agentd..." for a second at
  every boot.
- **The greeter and the lock are the pill.** The other option is hyprlock styled to match.
- **Stopped is grey, not orange.** Orange means working.
- **A trademark search before a public release.** The look-alikes named here were judged by eye.

## Cut, and why

- **A blend of B and C.** C was asked for; B gives only its letters.
- **The stone b turned on its side.** With the stone pointing left or right, stem and stone read as a
  media player's skip button.
- **A bare letter in the pill.** It reads as a typed character; E's b is cut through a stone, so it reads
  as a mark.
- **The pill as a logo.** A rounded bar with a dot reads as a toggle switch, and as Blogger's B.
- **The hop and the walk as marks.** The hop was a stock trajectory pictogram; the walk was a third b and
  dot whose gap closed at small sizes. The hop lives on as Done.
- **An orange mark, or a mark on an orange or red tile.** Claude's colour, and the territory of Beats,
  Blogger and Ubuntu.
- **An amber ring for Needs you.** A gold ring next to the name Bombadil is the One Ring; a soft filled
  glow instead.
- **A boot splash and a logo wallpaper.** Rest is wallpaper and a pill.
- **Tolkien imagery and a mascot.** No hat, feather, boots, ring, runes, song lines or face.

## Built

- The shell and the app kit share one set of tokens (`share/qml/Bombadil/Theme.qml`), with Inter as the
  family everywhere; the bar starts through `bin/bombadil-shell` so Quickshell can find the kit's theme.
- `shell/Stone.qml` draws the mark and `PillState.face` says which face it wears; the icons, the lockups,
  the repository's pictures and the GRUB theme are in the files table of
  [`docs/brand/README.md`](../brand/README.md), with what a real machine taught us.

## Checked

- The stone's constant width, numerically (11.000 for W = 11); E's b clearance to the stone's edge over
  every pose (0.95 px in the pill, 1.3 px at 64, 0.7 px at 16, 2.0 px in the tile); every state rendered at
  1x and 2x, as filmstrips and in greyscale.
- Claude's favicon is filled `#D97757` (fetched).
- GRUB 2's sources: `read` takes `-s`, `cryptomount` takes `-p`, and GRUB's own prompt is "Enter
  passphrase for %s%s%s (%s): ". Arch's grub sets `GRUB_DISTRIBUTOR="Arch"` and reads
  `/etc/default/grub.d/*.cfg`. systemd-boot's menu is text only. Quickshell has `Quickshell.Services.Greetd`
  and `WlSessionLock`. The kit's icons are Lucide, under ISC.
- Contrast ratios were computed from the hex values. The look-alike marks (Beats, Booking.com, Bing,
  Bazzite, Blogger, Ubuntu, Vercel, Debian) were judged by eye, not by a trademark search.

## Open questions

1. Does `grub-mkconfig`'s own `cryptomount` prompt again after a `read -s` in `grub.cfg`? (A VM check.)
2. Does a Quickshell greeter run under greetd cleanly on the image, and can it share the shell's QML?
3. Does the 18 px stone make the resting pill feel louder on a real screen? (16 px is the fallback, with
   the b at scale 0.55.)

# The Bombadil mark

> **Status:** Partly shipped
> **Code:** `shell/Stone.qml`, `shell/PillState.qml`, `shell/shell.qml`, `share/qml/Bombadil/Theme.qml`, `shell/DeskTheme.js`, `docs/brand/`, `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil.svg`, `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil-symbolic.svg`, `iso/airootfs/usr/share/pixmaps/bombadil.svg`, `share/grub/bombadil/`, `README.md`
> **Pieces:** piece 1 (one set of tokens for the shell and the kit) is shipped. Piece 2 (the mark in the machine) is partly shipped: the pill's stone and its states, the installed icons and the README banner are on `main`. The GRUB theme's files are on `main` but nothing installs them. `LOGO=bombadil` in `os-release`, the greeter and the lock as the pill, and a separate 16 px colour drawing are designed, with no file or code for them.
> **Design:** this brief and its page, [The Bombadil mark](pages/bombadil-mark.html); [Design system](../design-system/README.md#the-mark-and-its-states); [Shell](../architecture/shell.md)
> **Decided by:** the project owner, 2026-09-30 (the mark: E, the riding b; the orange stays, the owner not objecting to the recommendation); the other choices under "Decisions I picked a default for" are defaults
> **Verified:** 2026-10-01 against `main` at `26843d3`: the statuses under "Build these first", the files they name, and the stone geometry, timings and colours that `shell/Stone.qml` and `share/qml/Bombadil/Theme.qml` repeat. The sentences about the shell "today", the colour and token counts, the contrast ratios and the list under "Checked today" (vendor facts dated 30 Sep 2026) are the brief's original text and were not re-checked (see the reading note).

You press the power button. The firmware's logo goes, and the screen turns the dark grey of the desktop. In the middle sits a smooth three-sided stone in light ink with a lowercase b cut clean through it, and under it one line: "Type your password to start Bombadil." You type it. A second later the desktop is there, wallpaper and one pill at the bottom, and the stone at the pill's left end is orange and rolling, with the word "Starting". Then it settles, green, flat side down, before "Ask anything".

You start typing and the stone leans a little toward your words. You press Enter and it turns orange and rolls a third of a turn every second, and the b in it stays level and still while the stone turns around it: you ride level while the machine does the work. When the turn ends having changed something, it turns green, gives one small hop and a squash as it lands, and sits still. When a coding session waits on you, it turns amber, knocks twice, and waits with a soft glow breathing behind it. Press Esc and it becomes the Stop button's grey square. Lose the agent and only its broken red outline is left, with the b still in it. Each of those reads in a black-and-white screenshot, which today's green, orange and red dot does not.

The same stone sits on the download page and the README beside the name "bombadil" in round drawn letters whose i is dotted with a small orange stone, and on the app tile, the stick's label and the install card. The mark is never orange, and never on an orange tile.

This is round two of the design pass for the project owner's ask of 30 Sep 2026 for a logo, a design identity and a design system, keeping the look the project already had and showing a few options in different states. Round one offered A (the b), B (the dot on the i) and C (the pebble). The owner liked B and C: B was good but lacked the animation and identity, and C had good animation but lacked the identity that says this is Bombadil when someone sees it. The owner then preferred C if it could be made more distinct or given personality. So round two starts from the pebble and its motion, not from a blend of B and C. [The page](pages/bombadil-mark.html) draws the three round-two options in every state, at every size and in place. The design system is written up on its own in [docs/design-system/README.md](../design-system/README.md). The earlier briefs' decisions stand.

> **Reading note.** This brief was written on 30 Sep 2026, and "today", "now" and "new" in it mean that day. Sentences about the shell "today" (22 hard-coded colours, no font family, a dot where the stone is, the offline red at every boot) describe the code before the build; the status block above says how far each piece under "Build these first" got. Two names differ in the code: the pill's `listening` face is set by `shell/shell.qml`, not by `PillState.qml`, and the face the brief calls "needs you" is `needs` (`needsYou` is the property that raises it). The candidate drawings A to F are not in this repository: the chosen mark is in [`docs/brand/`](../brand/). The counts, the contrast ratios and the list under "Checked today" are the brief's original text and were not re-checked against code or vendors.

## Decided

1. **The mark is E, the riding b.** The project owner chose it on 30 Sep 2026.
2. **The orange stays #d97757.** The owner did not object to the recommendation: the marks are drawn in ink, and orange only appears on the stone while the machine works and on the dot of the i.

The build (pieces 1 and 2 below) goes on a branch with a pull request per piece, after the component kit merges.

## The rules

1. **The stone is the machine.** The dot in the pill is the one piece of Bombadil everybody sees every day. The stone takes its place and does what the dot did, with a character.
2. **The mark is ink; colour is the state.** Drawn in #e6e8eb on the dark ground and #15181b on a light page. The stone takes a colour only in the pill, and only the colour of its state; the one fixed touch of orange is the dot of the i. #d97757 is the exact colour of Claude's logo and Bombadil also runs Codex, so Bombadil is recognised by a shape, never by an orange blob.
3. **Colour says who, everywhere.** White is you, orange is the machine acting, blue is a coding session. Amber is a step that touches the system or something waiting on you, red is failed, offline or can't be undone, green is connected.
4. **Every state reads without colour.** Rest is still, listening leans, working rolls, needs you knocks and glows, done hops once, stopped is a square, offline is a broken outline.
5. **At rest, quiet.** The screen at rest stays wallpaper and one pill with a still stone. No splash, no logo wallpaper.
6. **Things that speak are round; things that hold have 12 px corners.** The pill, the line and the chips are fully round; windows, cards and panels are 12; controls are 8.
7. **Every movement means one thing.** Rolling is working, a knock is waiting on you, a hop is done, a lean is listening. Nothing else moves, and each has a still version for reduced motion.
8. **Original.** Nothing from Tolkien's text or art (no hat, feather, boots, ring, runes or song lines), no face and no mascot. The character is in how the stone moves, never in a face.

## Build these first

Two pieces. Both wait for the component kit, because the shell should read the kit's theme rather than grow a second one.

Where each piece stands, checked against `main` on 2026-10-01:

- **1. One set of tokens: shipped.** `share/qml/Bombadil/Theme.qml` holds `borderActive`, `glassPill`, `glassLine`, `glassChip`, `glassCard`, `accentInk`, `warnInk`, `badInk` and `badLine`, and two more the shell needed, `glassStrip` and `glassRaised`. No `.qml` file in `shell/` holds a colour literal, every `Text` and `TextField` in `shell/` sets `font.family`, and `shell/DeskTheme.js`, the one plain-values library, repeats the tokens. `tests/test_theme.py` checks each of these. Open question 3 is answered in code: the family is set to `Inter` (`Theme.fontFamily`), not left to fontconfig, and `iso/packages.x86_64` installs `inter-font`.
- **2. The mark in the machine: partly shipped.**
  - The pill's stone and its states: shipped. `shell/Stone.qml` draws the stone and the b and animates every face. `PillState.face` gives `starting`, `needs`, `working`, `stopped`, `done`, `rest` and `offline`, and `shell/shell.qml` adds `listening` for the screen that holds the keyboard. `tests/test_stone_qml.py` and `tests/test_pill_qml.py` cover them and skip without PySide6. Only setup raises `needs` on `main`: nothing outside the tests sets `needsYou`, so the desk's trigger is not wired.
  - The files: the icons are shipped (`bombadil.svg` and `bombadil-symbolic.svg` in `hicolor/scalable/apps`, `bombadil.svg` in `pixmaps`, checked by `tests/test_brand.py`). The one-colour 16 px drawing is `bombadil-symbolic.svg`; a separate colour drawing for 16 px has no file. `LOGO=bombadil` is not: the repository has no `os-release` for the image.
  - GRUB's screen: designed, with its files made. `share/grub/bombadil/` holds `theme.txt` and a 1920 by 1080 `background.png`, but `iso/airootfs/usr/local/bin/bombadil-install` does not copy them, does not make the font or write `/etc/default/grub.d/bombadil.cfg`, and sets `GRUB_TERMINAL_OUTPUT=console`.
  - The README banner and the repository: shipped. `README.md` opens with the `<picture>` lockup, and `docs/brand/` holds the avatar and the 1280 by 640 social preview. Uploading the preview in the repository settings is a manual step the code cannot show.
  - The greeter and the lock as the pill: designed. The image's `greetd` config starts the session straight away, and no Quickshell greeter or lock exists in the repository.

### 1. One set of tokens for the shell and the kit

The shell (shell/*.qml on main) draws the pill, the line and the chips with 22 hard-coded colours, 9 of them in no token, and sets no font family. The kit's Theme.qml names about forty tokens. This piece adds the few the shell needs to Theme.qml (borderActive, glassPill, glassLine, glassChip, glassCard, accentInk, warnInk, badInk, badLine), points every shell literal at a token (the drift table below), and sets Inter as the family everywhere. What changes on screen: the offline and error reds move from #c04a4a and #e05252 to the kit's #d05555, the system amber from #e8a33d to #e0a93b, and text in the shell renders in Inter if fontconfig does not already pick it (check on the ISO first: `fc-match sans-serif`). Effort S.

### 2. The mark in the machine

- **The pill's stone and its states.** A `Stone.qml` in the shell draws the chosen mark and PillState.qml gains `starting` (from boot until agentd first answers, instead of offline red), `listening`, `needsYou`, `stopped` and `done`. The spec for E is below; D is the same without the b; F adds the stem.
- **The files.** The chosen set installs as `/usr/share/icons/hicolor/scalable/apps/bombadil.svg` (plus `bombadil-symbolic.svg` from the mono file) and `/usr/share/pixmaps/bombadil.svg`; os-release gains `LOGO=bombadil`.
- **GRUB's screen**, built with the [installed-OS brief](installed-os-brief.md)'s piece 1: a gfxterm theme on the ESP (background PNG with the mark, Inter converted with grub-mkfont, one line above the password field). See "Where the mark goes".
- **The README banner and the repository.** The lockup at the top of README.md (a dark and a light copy with `<picture>`), and the tile as the social preview, which the owner uploads in the repository settings.

Effort M. The trigger for "needs you" (a session waiting on you) is the desk piece "the pill dot's needs you mark", and the trigger for "listening" is the VM fixes piece "show listening at the tap"; this piece gives both their look.

## The stone

All three options use one shape: a rounded Reuleaux triangle of width W. Its corners have radius r = 0.15 W and its sides are arcs of radius 0.85 W, each centred on the opposite corner's centre, so the whole outline is six arcs of two radii. It has the same width in every direction, so its bounding box is a W × W square in any pose: it can turn inside a square while touching all four sides, and a third of a turn brings it back to exactly the same pose. It rests corner up, flat side down. Its centroid sits 0.554 W below the top of its square, and the circle every pose covers has radius 0.446 W around the centroid.

**The character, shared by all three.**
- **Rest:** green #5fb36b, still.
- **Listening (new):** green, leans 6 degrees toward the text over 240 ms on the curve (0.16, 1, 0.3, 1) and stays leaning while you type. The lean is a roll on its bottom arc: a rotation about the top corner's centre plus a slide of 0.85 W × the angle in radians.
- **Working:** orange #d97757, a third of a turn per 1 s beat: hold 300 ms, turn 120 degrees in 550 ms on (0.55, 0, 0.3, 1), hold 150 ms. D and F roll inside their square, touching all four sides (the square stays put and the centre moves under 1 px); E turns about its centroid so the b can stay still. The pill's border turns orange as today. Reduced motion: today's opacity pulse, 1 to 0.3 to 1 per second.
- **Needs you:** amber #e0a93b. Knocks twice, tipping 8 degrees to the right and landing back (0 to 8 in 12% of the beat, ease-out; back by 22%, ease-in; again by 32% and 42%), then waits, on a 1.6 s beat. Behind it a filled radial glow (radius 0.9 W, at most 11 px) breathes 0.25 to 0.6. Never a ring. Reduced motion: still, glow held at 0.45.
- **Done:** green, 700 ms, played once: a squash to (1.06, 0.9) about its foot, a 3 px hop, a landing squash to (1.1, 0.84), a small rebound, still. Reduced motion: a 600 ms fade in from 0.3.
- **Stopped:** the Stop button's own square in muted #8b939c (10 px, radius 2; 12 px, radius 2.4 for E). Never orange.
- **Offline:** the outline alone, 1.5 px red #d05555 with round caps, broken into six dashes, one centred on each corner and each side. The pill's border is #7a2e2e as today.
- **Starting:** the working roll with the word "Starting".

**The name, shared by all three.** B's drawn letters, which the owner liked: lowercase "bombadil" in round-capped 8.6-unit strokes, rings and arcs. The dot on the i is a small stone (W 15.4 on letters 63 units tall), #d97757 on dark, #b4532f on light, and it is the only orange in the lockup.

## The options

### D. The merry pebble

C's stone exactly as it was, 12 px in the pill where today's 10 px dot sits, with the character above. It gets its identity from the company it keeps: the lockup, the download page and the README always show it beside the name, whose i is dotted with the same stone.

**Why it works.** The smallest step from the one the owner liked, the quietest pill, and the clearest shape at 16 px.

**Its weak spot.** Seen on its own it is still a rounded triangle: a guitar pick, a gumdrop, and close to Vercel's triangle at 16 px on a dark tile. The character only shows while it moves, so a screenshot, a USB stick or an app icon does not yet say Bombadil. It answers "personality" but only half of "identity".

### E. The riding b (chosen)

The stone with a lowercase b cut clean through it: A's constructed b (a straight flat-topped stem and a bowl that is half a pill, meeting in a square corner). When the machine works, the stone turns around the b while the b stays level and still, like a hub that never turns. The b's point (11.5, 12.25) on its 24-unit grid sits on the stone's centroid, and its reach from there (10.1 units times the scale, plus half the stroke) stays inside the circle every pose covers, so the cut never breaks the edge. In the pill the stone is 18 px (x and y 3 to 21) and the b is scale 0.62 with a 1.6 px cut, with at least 0.95 px of stone around it in every pose.

**States.** As the shared character. The b rides: in the knock and the lean it slides with the stone's centre but never tilts; in the hop it hops and squashes with the stone. Offline keeps the b as a red line inside the broken outline: the stone is gone, the b is still there.

**In QML.** An Item of 24 × 24. The stone is a Shape with one ShapePath of six PathArc (corner arcs radius 2.7, side arcs radius 15.3), filled with the state colour, with `transform: Rotation { origin.x: 12; origin.y: 12.975 }` for working. The b is a second Shape above it: a ShapePath with strokeWidth 1.6, FlatCap, MiterJoin, no fill, path M 7.97 10.96, H 13.24, arc radius 3.41 clockwise to 13.24 17.78, H 8.59, V 7.86, stroked in the pill's own surface colour, which reads as a cut because the b never leaves the stone. (Where the stone sits on a picture, such as the GRUB background, the SVG's real mask is used.) The knock and the lean rotate the stone about the top corner's centre (12, 5.7) and slide it by 15.3 × the angle in radians; the b slides by the centroid's shift (1.12 px at 8 degrees). The hop is a Translate and a Scale on the Item with its origin at (12, 21). The glow is a PathAngleArc circle with a RadialGradient under the stone. No images, no MultiEffect.

**Sizes.** 16 px has its own drawing (the stone full-bleed, the b at scale 0.56 with a 1.5 px cut). The tile is the stone at 84 on the 128 tile. The lockup is the stone at 64 units on the baseline, a 26-unit gap, then the name.

**Why it works.** It says Bombadil wherever it appears, on its own: the app tile, the stick, GRUB's screen and the pill. It keeps what the owner liked about C (the shape, the roll, the calm), and the motion shows the idea at a glance: the letter holds still while the stone works, which is the passenger idea in one picture.

**Its weak spot.** The pill's mark grows from today's 10 px dot to an 18 px stone with a letter in it, so the resting pill is a little louder. Round one ruled out a letter in the pill because a bare letter reads as a typed character; a b cut through a stone reads as a badge instead, but it is a letter all the same. A letter on an orange shape sits near Blogger's orange B, so the stone is only orange while it turns, never as a still logo. At 16 px the b's bowl is about 2 px across; it holds at 1x, but only just.

### F. The stone b

The stone as the bowl of a lowercase b, beside a straight stem (in the pill: stem 2 × 18 px at x 5, stone 12 px at x 7 to 19). While the machine works it rolls against the stem, always touching the stem and the ground; when it needs you it knocks on the stem. The stem takes the state colour with the stone so the two read as one letter. In the name it is the first letter, so the lockup reads bombadil with a stone for its first bowl.

**Why it works.** The most playful of the three and the best lockup: the stone is part of the name itself, and knocking on the stem is the clearest Needs you.

**Its weak spot.** On its own the b has a pointed bowl that reads as an l beside a fin, and at 16 px it comes apart into a bar and a triangle. In the pill the stem stands right before the text, where it can read as the text cursor. Turned with the stone pointing left or right it becomes a media player's skip button, so it only ever stands corner up.

## Round one

- **A, the b** (round one's pick): set aside. Its written-in motion stays an idea for later; its constructed b lives on inside E. Its spec is kept in round one's brief and its files in the round-one set a-the-b; neither is in this repository.
- **B, the dot on the i:** its drawn letters are now the name in all three options, with a stone for the dot.
- **C, the pebble:** all three options start from it; D is C plus the character.

The round-one SVG sets (a-the-b, b-dot-on-the-i, c-pebble) are not in this repository; the chosen mark's files are in `docs/brand/`.

## The orange

Bombadil's accent is #d97757. Claude's web favicon is filled #D97757, and Anthropic's brand uses the same value as its primary accent. Bombadil also runs Codex, so an orange mark would read as Claude's and be wrong half the time.

- **Keep it (my pick).** Every mark is ink; the orange appears on the stone only while the machine works, and as the dot of the i. The working border, the pulse and Stop stay orange. On a light page the i's stone uses #b4532f, because #d97757 on #f2f3f4 is 2.8:1.
- **Change it.** Pick a machine colour of our own. That restyles the working border, Stop, the pulse, the desk's orange marks and every design page: the part of the look the owner said they like.

## The design system

The full reference, with every token's use and the kit's components with notes on when to use each, is the design system: the tokens and their use are in [docs/design-system/README.md](../design-system/README.md) and the component catalogue is in [docs/architecture/app-kit.md](../architecture/app-kit.md). The summary:

**Colour.** The ground, darkest to lightest: sunken #0c0e10 (wells, code), bg #101214 (the desktop), panel #1a1d21 (the pill, cards, the line), raised #22262b (hover, inputs, line buttons), overlay #2a2f36 (menus; also the hairline border), borderStrong #3a414a, borderActive #4a525c (new: the pill while it holds the keyboard). Glass, over windows: the panel at 94% for the pill, 90% for the line, 85% for chips, 96% for desk cards. Ink: fg #e6e8eb, muted #8b939c, faint #5c636b. Marks: accent #d97757 (ink #f2c4b3, soft wash #33d97757), good #5fb36b, warn #e0a93b (ink #e8c38d), bad #d05555 (ink #f0a0a0, line #7a2e2e), info #5b9bd5. Charts: the kit's eight series colours in order, never cycled. On a light page: ink #15181b, ground #f2f3f4, accent #b4532f.

**Contrast to know.** White on the accent is 3.1:1, so orange buttons use 14 px medium text at least and the accent is never the only carrier of meaning; faint on the panel is 2.8:1, so faint is for disabled and ghost text only; bad on the panel is 4.1:1, so red marks are marks and red text uses badInk.

**Type.** Inter, which the ISO already ships, for everything; the system monospace for commands. The shell speaks at pill 16, line 15, meta 13 and caption 12. Apps hold at body 14, caption 12, label 12/500, heading-3 14/600, heading 17/600, stat 22/600, title 22/700, display 34/700, mono 13. Numbers that tick use tabular figures.

**Shape.** Things that speak are fully round: the pill (52, radius 26), the line (radius 14), chips (28, radius 14), line buttons (24, radius 12). Things that hold have 12: windows (Hyprland rounding 12), cards, panels. Controls have 8. The focus ring is a 2 px accent outline 3 px outside. Borders are 1 px; the pill's is 1.5 px.

**Space.** gapSmall 6, gap 12, pad 16; control 36, row 44, pill 52, chip 28; the pill and the line at most 900 wide and 12 from the screen edge; desk rails 300; Hyprland gaps 6 inside and 12 outside.

**Motion.** 120 ms for sizes, 200 ms for fades, 300 ms for the pill's border colour; windows and panels slide on the bezier (0.16, 1, 0.3, 1). The stone rolls while working, knocks when it needs you, hops once when done and leans while you type; with reduced motion, working falls back to one pulse (1 to 0.3 and back, 500 ms each way) and nothing else moves. Lines stay 12 s after a turn, 5 s after a local answer, 15 s after an undo. Everything that moves has a still version for reduced motion.

**Depth.** No drop shadows on the ground; popups get the kit's two-layer shadow. Hyprland blurs behind glass (size 6, 2 passes).

**Icons.** Lucide (ISC), 2 px stroke, round caps and joins, 16 px in buttons, 24 in toolbars, tinted to their text; the 77 in the kit are the vocabulary. The mark sits with them: E's b is cut 1.6 px wide in the 24 px slot, close to Lucide's 2 px line.

**Drift to fix when the shell adopts the tokens** (piece 1):

| Shell today | Where | Becomes |
|---|---|---|
| no font family | every Text in shell/ | Inter, from the theme |
| #c04a4a | offline dot, error edge (shell.qml, StatusLine.qml) | bad #d05555 |
| #e05252 | irreversible edge (StatusLine.qml) | bad #d05555 |
| #e8a33d | system edge (StatusLine.qml) | warn #e0a93b |
| #e8c38d, #f0a0a0, #f2c4b3, #7a2e2e | command, error text, Stop, offline border | warnInk, badInk, accentInk, badLine |
| #3a2a26 | Stop's hover wash (shell.qml) | accentSoft |
| #5d646c, #6b737c | ghost completion, the chip's "next" | faint |
| #a9b0b8 | chip prompt text (QueueChips.qml) | muted |
| #353b43 | line button border (LineButton.qml) | borderStrong |
| #4a525c | the pill's border while it holds the keyboard | borderActive |
| three alpha panels | pill, line, chips | glassPill, glassLine, glassChip |
| --os-red #e06a6a, --os-amber #e0a84a | the published design pages' mock-ups | bad, warn |

## Where the mark goes

- **The pill.** The states above. The pill at rest is today's pill with the stone in the dot's seat.
- **GRUB, installed.** `/etc/default/grub.d/bombadil.cfg` (Arch's grub reads drop-ins there) sets `GRUB_DISTRIBUTOR="Bombadil"` (already in the installed-OS brief), `GRUB_TERMINAL_OUTPUT=gfxterm`, `GRUB_GFXMODE=auto` and `GRUB_THEME=/efi/grub/themes/bombadil/theme.txt`. The theme lives on the ESP, because grub.cfg is read there before the disk is unlocked: a background PNG of the ground with the mark (rendered from the SVG at 1920×1080 and 3840×2160), and Inter at 16 and 24 converted with grub-mkfont. The menu stays hidden as the VM fixes piece decided. GRUB prints its own "Enter passphrase for hd0,gpt2 (…)" when it unlocks a disk; our line "Type your password to start Bombadil." replaces it only if grub.cfg asks with `read -s` and passes the answer to `cryptomount -p`. Both exist in GRUB's sources; whether grub-mkconfig's own `cryptomount` still prompts after that is to check in the VM. If it does, the screen shows the mark with GRUB's line, and that is acceptable.
- **The stick's boot menu.** systemd-boot draws text only, so no mark there; the entries read "Bombadil", "Safe graphics", "Reboot Into Firmware Interface". The firmware's own logo shows before it.
- **No boot splash.** Plymouth stays cut. GRUB's still screen carries the mark, and the pill says Starting for the second before agentd answers.
- **os-release.** `NAME="Bombadil"`, `PRETTY_NAME="Bombadil"`, `ID=bombadil`, `ID_LIKE=arch`, `LOGO=bombadil`, `HOME_URL` once there is a page. Check first that nothing on the image tests `ID=arch` without reading `ID_LIKE` (grep the image for `ID=arch` and `/etc/os-release`); fastfetch will show its generic logo instead of Arch's, which is fine. The file belongs in the installed-OS brief's release package.
- **After a logout, and the lock.** The pill itself: Quickshell has a greetd service and a session lock, so the same pill asks for the password, its stone amber and knocking because it waits on you, above one line "Type your password." This changes the installed-OS brief's piece 4, which picks hyprlock and greetd's plain prompt; hypridle keeps the timing and calls `loginctl lock-session`. If a Quickshell greeter cannot be packaged cleanly, hyprlock with the ground colour, the mark and the pill's field is the fallback.
- **The install card and the recovery key card.** Cards in the kit's look; the 16 px mark takes the card's icon place, in ink. Their words and rows are the installed-OS brief's.
- **The download page, the README and the repository.** The lockup, one sentence and one button; the README banner in a dark and a light copy; the tile as the repository's avatar and social picture.
- **The stick's label and anything printed.** The mono mark in black or white, never orange.
- **The wallpaper.** Stays the plain ground #101214.

## Decisions I picked a default for

**The mark: E, the riding b (chosen 30 Sep 2026).** D is the quietest but does not say Bombadil on its own; F has the best lockup but the weakest mark.

**Keep #d97757.** The alternative restyles the look the owner likes.

**The name in B's drawn letters with a stone for the i's dot**, in all three. Round one's default was Inter SemiBold; the owner liked B's letters.

**A listening lean.** New in round two: the stone leans toward your words while you type. It gives the VM fixes piece "show listening at the tap" a look.

**Lowercase "bombadil" in the lockup, "Bombadil" in prose.** The command is `bombadil`.

**A new "Starting" state.** Today the pill shows the offline red and "Waiting for agentd…" for a second at every boot.

**The greeter and the lock are the pill.** Other option: hyprlock styled to match.

**Stopped is grey, not orange.** Orange means working.

**No splash, no logo wallpaper.** Rest means nothing on screen but the pill.

**A trademark search before a public release.** The look-alikes named here were judged by eye.

## Owned by other pieces of work

- **Setup voice and welcome lines** owns every word the pill and the screens say: the name card, greetings, goodbyes, "Starting" if it wants another word, and the lines on GRUB's screen and the greeter. This brief proposes "Type your password to start Bombadil." and "Type your password." in the Plain register.
- **Bombadil as an installed OS** owns the install flow and its screens: the download page's steps, the stick's menu, GRUB and its unlock, greetd, the install and recovery key cards, and the release package. This brief gives it the marks, the GRUB theme and the look of the greeter and the lock (which changes its piece 4, above).
- **App skill and native component kit** owns the kit's code. Piece 1 adds tokens to its Theme.qml after it merges.
- **Desk rails and first widgets** owns when the pill's dot says "needs you"; this brief owns how it looks.
- **VM fixes** ("show listening at the tap") owns when the pill is listening; this brief owns the lean.

## Cut, and why

- **A blend of B and C.** The owner asked for the pebble; B gives only its letters.
- **The stone b turned on its side.** With the stone pointing left or right, stem and stone read as a media player's skip button.
- **A bare letter in the pill.** A letter alone in the slot reads as a typed character; E's b is cut through a stone, so it reads as a mark.
- **The pill as a logo.** A rounded bar with a dot reads as a toggle switch, and as Blogger's B.
- **The hop and the walk as marks.** The hop was a stock trajectory pictogram; the walk was a third b and dot whose gap closed at small sizes. The hop lives on as Done.
- **An orange mark, or a mark on an orange or red tile.** Claude's colour, and the territory of Beats, Blogger and Ubuntu.
- **An amber ring for Needs you.** Next to the name Bombadil a gold ring is the One Ring; a soft filled glow instead.
- **A boot splash.** Plymouth is cut; GRUB's still screen carries the mark, then the pill rolls and says Starting.
- **A logo wallpaper.** Rest is wallpaper and a pill.
- **Tolkien imagery and a mascot.** No hat, feather, boots, ring, runes, song lines or face.

## Extends or changes the earlier briefs

- **The pill ([ux-brief](ux-brief.md)).** Changed: the 10 px dot becomes the stone (12 px in D and F, 18 px in E); four new states (Starting, Listening, Needs you, Stopped) and a shape or motion for every state.
- **[Bombadil installed](installed-os-brief.md), piece 4.** Changed: the greeter and the lock are the pill, not hyprlock and greetd's plain prompt.
- **[Bombadil installed](installed-os-brief.md), GRUB.** Extended: the theme, its font and the line above the password.
- **The design pages.** Their OS reds and ambers move to the kit's values from the next page on.

## Checked today

- Round two (drawn on 30 Sep 2026): the stone's constant width checked numerically at 11.000 for W = 11; E's b clearance to the stone's edge computed over every pose (0.95 px in the pill, 1.3 px at 64, 0.7 px at 16, 2.0 px in the tile); every state rendered at 1x and 2x, as filmstrips, and in greyscale, and looked at.

- Claude's web favicon is filled #D97757 (fetched).
- GRUB 2 sources (the rhboot/grub2 mirror on GitHub, because the GNU pages did not answer): `read` takes `-s`, `cryptomount` takes `-p`, and GRUB's own prompt is "Enter passphrase for %s%s%s (%s): ".
- Arch's grub sets `GRUB_DISTRIBUTOR="Arch"` and reads `/etc/default/grub.d/*.cfg`.
- systemd-boot's menu is text only.
- Quickshell has `Quickshell.Services.Greetd` and `WlSessionLock`.
- Code: the dot's colours at shell/shell.qml:266 and the pill's border at :244 on `main` at 2318be9; the tokens in share/qml/Bombadil/Theme.qml of the component kit at 4374b26; the kit's icons are Lucide under ISC.
- Contrast ratios computed from the hex values.
- The look-alike marks (Beats, Booking.com, Bing, Bazzite, Blogger, Ubuntu, Vercel, Debian) were judged by eye, not by a trademark search.

## Open questions

1. Does grub-mkconfig's own `cryptomount` prompt again after a `read -s` in grub.cfg? (VM check, with the installed-OS piece.)
2. Does a Quickshell greeter run under greetd cleanly on the ISO, and can it share the shell's QML?
3. What does fontconfig pick for the shell's text today?
4. Does the 18 px stone make the resting pill feel louder on the real screen? (Check in a VM once the stone is built; 16 px is the fallback, with the b at scale 0.55.)

## The page and the files

The page is [pages/bombadil-mark.html](pages/bombadil-mark.html) (version 2, round two). The round-two SVG sets (d-merry-pebble, e-riding-b, f-stone-b, shared-wordmark) each held the mark, the one-colour mark, the 16 px drawing, the tile, the lockup in dark, light and one colour, the seven state glyphs (rest, listening, working, needs, done, stopped, offline) and a contact sheet. Their generator (`node gen.mjs`, with `geo.mjs` holding the stone's geometry and the roll) and round one's sets sat beside them. None of those files is in this repository. The chosen mark's files are in `docs/brand/` (`bombadil-mark.svg`, `bombadil-tile.svg`, `bombadil-lockup.svg`, `bombadil-lockup-light.svg`, `bombadil-avatar.png`, `bombadil-social-preview.png`), and the stone's geometry is also in `shell/Stone.qml`.

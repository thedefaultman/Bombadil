# The Bombadil design system

Bombadil is a Linux desktop whose main interface is an AI agent. The screen at rest is wallpaper and one pill at the bottom; the agent's work appears as a line above the pill, as cards, as panels that slide in, and as native apps it writes. The person using it is a passenger: everything the machine does is shown plainly and can be undone. This system is the look the screen has, written down so the shell (Quickshell QML), the component kit (`Bombadil.Style`, `Theme.qml`) and every design page read one set of values. Nothing here restyles it. The tokens are `share/qml/Bombadil/Theme.qml` (the shell and the app kit read the same file); the mark is in [`identity-brief.md`](identity-brief.md) and [`../brand/README.md`](../brand/README.md).

## Content fundamentals

- Write plainly, in full sentences, in the Plain register the [voice brief](voice-brief.md) defines. The machine speaks as "I" only in the agent's own replies; the interface never says "we".
- Sentence case everywhere: "Ask anything", "Install on this computer", "Your recovery key". Title Case only for app names ("Passwords").
- The product is "Bombadil" in prose and `bombadil` as the command and in the lockup.
- Name what changed and how to take it back: "Made Passwords. It is open now." with Undo and Details. A stopped turn says "Stopped." A missing agent says "Waiting for agentd…".
- Buttons say exactly what happens, naming the object when it can't be undone: "Erase Samsung 980 and install", not "Continue".
- No emoji, no exclamation marks in system text, no "Oops". Greetings, goodbyes and every line a voice may change belong to the voice brief.

## Colour

**Colour says who.** `fg` (white) is you, `accent` (orange) is the machine acting, `info` (blue) is a coding session. `warn` (amber) is a step that touches the system or something waiting on you; `bad` (red) is failed, offline or can't be undone; `good` (green) is connected. Every dot, edge, meter and strip follows this. Never use `accent` for anything the machine is not doing right now.

- **The ground.** `bg` is the desktop and window backgrounds. `panel` holds the pill, the line, chips, cards and the bar. `raised` is hover, selected rows, inputs and line buttons. `overlay` is menus, popups and tooltips. `sunken` is wells, editors and code. `border` is every hairline; `borderStrong` is hovered and pressed outlines; `borderActive` is the pill's border while it holds the keyboard.
- **Glass.** The shell's surfaces float over windows, so they use `glassPill` (94 %), `glassLine` (90 %), `glassChip` (85 %) and `glassCard` (96 %), with Hyprland's blur behind. Apps are opaque and use `bg` and `panel`.
- **Ink.** Body text is `fg`. Secondary text, the clock and placeholders are `muted`. `faint` is only for disabled text, the Tab ghost and "next" (2.8:1 on `panel`), never for anything a person must read.
- **Marks and their inks.** A status has a mark (dot, edge, icon), an ink for text and sometimes a line: `accent` / `accentInk` / `accentSoft`; `warn` / `warnInk`; `bad` / `badInk` / `badLine`; `good`; `info`. Coloured text on the dark ground uses the ink, never the mark: `bad` text on `panel` is 4.1:1.
- **Filled accent.** Primary buttons are `accent` with `accentFg` text at 14 px medium or larger (3.1:1), `accentHover` and `accentPressed` for their states. One primary button per view.
- **Charts** use `series-1` to `series-8` in order and never cycle; `grid` and `axis` for their rules.
- **On a light page** (the README, the download page, printed things): `inkOnLight` on `groundLight`, and `accentOnLight` for the dot and links, because `accent` on white is 2.8:1.

## The mark

The mark is the riding b: a smooth three-sided stone (a rounded Reuleaux triangle, the same width in every direction) with a lowercase b cut through it. While the machine works the stone turns and the b stays level. The Logos group holds the mark, its lockup and the name on its own.

- The mark is ink: `fg` on the dark ground, `inkOnLight` on a light page, black or white in print. The stone takes a colour only in the pill, and only its state's colour (`good` at rest, `accent` while working, `warn` when it needs you). The one fixed touch of orange is the stone that dots the i in the name. `accent` is the exact colour of Claude's logo and Bombadil also runs Codex, so never draw the mark in orange and never put it on an orange or red tile.
- The tile (app and installer icon) is the mark in ink on `panel` with `radius` corners; it is for 32 px and up. At 16 px use the 16 px drawing, never the tile scaled down.
- In the pill the stone sits where the old 10 px dot sat, at 18 px, with the b cut 1.6 px wide.
- Every state reads without colour: rest still, listening a lean toward the text, working a roll, needs you two knocks and a soft glow that breathes, done one hop, stopped a grey square, offline a broken `bad` outline. No state uses a stroked amber ring.
- No Tolkien imagery, no face, no mascot, no logo wallpaper, no boot splash. The character is in how the stone moves.

## Type

- Inter for everything (the ISO ships it); `"Noto Sans Mono"` for commands and code. Use tabular figures for anything that ticks.
- The shell speaks in the Shell group: `pill-input` 16, `line` 15, `meta` 13, `shell-caption` 12, `command` mono 12.
- Apps hold in the Apps group: `body` 14 for text, `caption` 12 for secondary, `label` 12/500 for field labels, `heading-3` 14/600, `heading` 17/600, `title` 22/700 for a window's title row, `stat` 22/600 for values, `display` 34/700 for one big number or word per view, `mono` 13.
- Don't set text below 12 px or above 34 px inside the OS.

## Shape and space

- Things that speak are fully round, radius half the height: the pill (`pillHeight`, `radiusPill`), the line (`radiusLine`), chips (`chipHeight`, radius 14) and line buttons (24 px, `radiusLineButton`).
- Things that hold have `radius` (12): windows, cards, panels. Controls have `radiusSmall` (8).
- Borders are `hairline`; the pill's is `pillBorder`. The focus ring is `focusRing` in `accent`, 3 px outside the control.
- Space in steps of `gapSmall` (6), `gap` (12) and `pad` (16). Controls are `controlHeight` (36), list rows `rowHeight` (44). The pill and the line are at most `pillMaxWidth` and sit 12 px from the screen edge; desk rails are `railWidth`.

## Motion

- 120 ms for sizes, 200 ms for fades, 300 ms for the pill's border colour. Windows and panels slide on the bezier (0.16, 1, 0.3, 1).
- Every movement means one thing. The stone rolls a third of a turn per second while working, knocks twice on a 1.6 s beat when it needs you (its glow breathes on the same beat), hops once when done and leans toward the text while you type. Nothing else moves.
- Under reduced motion, working falls back to one pulse (opacity 1 to `pulseLow` and back, 500 ms each way), needs you holds its glow, and the rest stay still.
- The line stays 12 s after a turn, 5 s after a local answer, 15 s after an undo.
- Every movement has a still version for reduced motion.

## Depth

No shadows on the ground. Popups, menus and tooltips get `shadowPopup`. Glass surfaces rely on Hyprland's blur (size 6, 2 passes), not on shadows.

## Iconography

Lucide (ISC licence, lucide-static 0.544.0), the 77 SVGs in the Icons group and in the kit at `share/qml/Bombadil/icons/`. They are drawn at 24 × 24 with a 2 px stroke, round caps and joins, and `currentColor`, so tint them to their text colour (`fg`, `muted`, or a status ink) through the kit's `Icon`; an `<img>` of the file draws black. Use 16 px in buttons and rows, 24 px in toolbars. Don't mix in other icon sets or emoji; if a needed icon is missing, add it from Lucide with its name.

## The pill's states

| State | Stone | Border |
| --- | --- | --- |
| Starting | `accent`, rolling, with "Starting" | `border` |
| Rest | `good`, still | `border` |
| Listening | `good`, leaning toward the text | `borderActive` |
| Working | `accent`, rolling | `accent` |
| Needs you | `warn`, knocking, with a breathing glow | `border` |
| Done | `good`, one hop, then still | `border` |
| Stopped | a `muted` square | `border` |
| Offline | a broken `bad` outline | `badLine` |

Starting, Listening, Needs you and Stopped came with the mark; before it the pill showed the offline red while the agent started.

## Shell literals, replaced

The shell used to draw with literals that drifted from these tokens. They are tokens now: `#c04a4a` and `#e05252` became `bad`; `#e8a33d` became `warn`; `#e8c38d`, `#f0a0a0`, `#f2c4b3` and `#7a2e2e` became `warnInk`, `badInk`, `accentInk` and `badLine`; Stop's `#3a2a26` wash became `accentSoft`; `#5d646c` and `#6b737c` became `faint`; `#a9b0b8` became `muted`; `#353b43` became `borderStrong`; `#4a525c` became `borderActive`; the three alpha panels became `glassPill`, `glassLine` and `glassChip`; and every Text takes Inter. A new literal in `shell/` should be a token first: `tests/test_theme.py` fails on one.

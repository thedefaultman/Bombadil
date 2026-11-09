# Tip scenes

A tip is shown on a small copy of Bombadil's own screen: the pill, the line above it, a window, a desk
card. The Tips app plays it once and holds the last frame; the desk's Tips card shows that last frame as a
still poster. Both are drawn by one component, `Kit.TipScene`, from a short list of beats. This page is how
that works and the exact vocabulary a beat is written in. Why a tip moves at all, and how little, is in
[`design/tips-and-desk-defaults-brief.md`](design/tips-and-desk-defaults-brief.md) ("How a tip moves").

## The pieces

| File | What it is |
|---|---|
| `share/qml/Bombadil/TipScene.qml` | `Kit.TipScene`: draws the frame at the card's measures (a poster) or the app's (plays). Pure QtQuick: `Theme`, `QtQuick.Shapes` and `Kit.Stone`, with `font.family` set on every Text. Internal: not in the apps skill, and `bombadil-app check` refuses it in an app the agent writes. |
| `share/qml/Bombadil/TipScene.js` | A `.pragma library` that says what is on the mini screen at any time `t`: `compile(beats)` lays the beats end to end, `frameAt(compiled, t)` returns the frame. It draws nothing and runs nothing. |
| `share/qml/Bombadil/Stone.qml` | `Kit.Stone`, the mark: the pill's stone and the scenes' stone. See [The stone in the kit](#the-stone-in-the-kit). |
| `src/bombadil/tip_scene.py` | The same vocabulary and timing for Python, standard library only, and the validator. `tests/test_tips.py` calls `validate(row, allow_empty=True)` on every row of `share/tips/tips.toml`, so a bad scene in the table fails there. |
| `tests/tip_fixtures.py` | Seventeen tips written for the tests, which between them use every beat, key and modifier. |
| `tests/test_tip_scene_qml.py`, `tests/test_tip_scene.py` | The renderer's frames, sizes and "nothing runs" tests, and the validator's. |

## Why storyboards, not video

A recorded clip is true by construction, but it is heavy, blurry at 300 px, stale the day the look changes,
and needs a video player in the shell. A storyboard is a few lines of text per tip, drawn live from the theme's
tokens, so it is always in the current look and crisp at any size. The price is that it can lie: it could show a
line the real machine never says. The truth rule below is what pays for that.

## From a tip row to a frame

```mermaid
flowchart LR
    ROW["tip row (tips.toml)<br/>line, words, marks,<br/>captions, beats"] --> VAL["tip_scene.validate<br/>keys, values, tags,<br/>10 s, three captions"]
    ROW --> STR["tip_scene.strings<br/>every string with its tag"]
    STR --> TRUTH["the truth test, added with the beats<br/>template: matched to the code<br/>typed: the tip's words"]
    ROW --> COMP["TipScene.js compile<br/>beats end to end:<br/>duration, phases"]
    CLK["t, in ms<br/>one animation while playing;<br/>seek(ms) sets it"] --> FR
    COMP --> FR["frameAt(compiled, t)<br/>pill, stone, key, line,<br/>window, cards, picture, fold"]
    FR --> ST["Stage at card or app measures<br/>Kit.Stone posed from the frame"]
    ST --> POSTER["card: the last frame, still"]
    ST --> PLAY["app: plays, holds the payoff"]
    ST --> STILLS["reduced motion: three stills<br/>and their captions"]
```

A scene is a function of its clock. The frame at any `t` is drawn from the beats alone, so a test or a still asks
for it without waiting, and the same `t` is the same picture every time. The one thing that moves is `t`, advanced
by a single `NumberAnimation` that exists only between `play()` and the end. There is no timer, no `Behavior` and
no loop in the scene, and the mini stone is a `Kit.Stone` with `animate: false` whose pose (`turn`, `knock`,
`lean`, `hopY`, `hopSx`, `hopSy`, `glowOpacity`) the scene writes from the frame.

## Using it

```qml
TipScene { tip: row; density: "card" }                                    // the desk card's poster
TipScene { tip: row; density: "app"; reducedMotion: Theme.reducedMotion
           onFinished: replayButton.enabled = true }                       // the Tips app's stage
```

| Property or function | |
|---|---|
| `tip` | A row of the tips table, or any `var` with `line`, `words`, `marks`, `captions` (three) and `beats`. |
| `density` | `"card"` (the default) or `"app"`. |
| `reducedMotion` | Default `false`. The host binds it to `Theme.reducedMotion`; the scene does not read the theme itself. |
| `t` | The clock in ms. Writable. Setting `tip` puts it back at 0 and stops the clock. |
| `duration` | Readonly. Where the last beat ends, in ms. |
| `phase` | Readonly, 0 to 2: the caption that is current. |
| `playing` | Readonly. True only between `play()` and the end. |
| `frame` | Readonly. The frame at the clock, as plain data (what the tests read). |
| `play()` | Plays from `t` (from the start when `t` is at the end) and emits `finished` at the end, where it holds. On the card and under reduced motion nothing plays: it settles on the last frame at once and emits `finished`. |
| `replay()` | `seek(0)` then `play()`. |
| `seek(ms)` | Stops the clock and puts it at `ms`, clamped to the scene. |
| `finished` | The scene reached its last frame. |

## The two densities

| | Card (the poster) | App (the stage) |
|---|---|---|
| Size | 272 x 153, radius 8 | 576 x 324, radius 12, and the captions in a row under it |
| Ground | the sunken surface with the wallpaper's two faint glows | the same |
| Moves | never: it shows the last frame whatever `t` is | plays once and holds the last frame |
| Stone | the 16 px drawing, in ink (`fg`, no state colour) | the 24 px drawing in its real colours, orange only where it acts |
| Pill | 32 px | 52 px |
| Text | 12 px, never smaller | 16 px; the line is 15 px; captions 12 px |
| Line buttons | unlabelled outlines (no Undo sits on a widget) | labelled |
| Counter | none | the seconds, while the stone works |
| Pictures of the machine | generic shapes: no names, numbers or values | the names and the figures |
| Captions | none | three, in a row under the stage, the current one in `fg` |

**Reduced motion** (app density) draws three stills instead of a stage, each the last frame of its phase with its
caption under it, and `play()` settles at once. A phase that ends on Enter is drawn before the key lands, so its
still has the words in the pill. Each still is the card's measures at full size, 12 px text and never smaller, so
they flow: two across and one under on the app's 576 px, all three across from 840 px, and a host narrower than
one card (272 px) shrinks them to its width. On a still the line buttons are unlabelled outlines, as on the card.

**A line is as long as the real one.** While the stone works it is one line and what does not fit is elided;
otherwise it is up to four lines, as `shell/StatusLine.qml` has it (`maximumLineCount`), so a reply is shown
whole: the undo reply, "Undone. System files go back to before “install htop” when you restart. Your home
folder and apps stay as they are.", is three lines on the card and two on the stage. The line grows upward from
the pill, and what stands above it (the rails' cards, the picture) rises to make room as it arrives. The words
keep their room first, then the buttons, then the counter, and the command takes what is left or is left out.
On the card, at 12 px, a line holds about 36 characters, or about 29 beside the two buttons. The tests fail on
any fixture line or window row that is elided at either density (a working line apart), and the same loop is
the check for the tips' own lines.

**Strips and the pill.** Folded, the strips stand on the left in the pill's row, and the pill is what they
leave: its right edge stays where it was and its left edge is where the strips end, so it never runs under one,
however many there are. It keeps at least 72 px on the card and 160 on the stage, and strips too long to leave
that are elided. A keycap pressed while strips stand is drawn in the margin on the right.

**A window** is as tall as what is in it, up to the room above the line: its rows, its lines of grey (`doc`) or
its lock screen under the title. It holds three rows on the card's poster and four on the stage, which is why
the validator allows a window three (`MAX_WINDOW_ROWS`); a `card` may have more.

## The ten beats

A scene is a list of beats, played one after the other. A beat is a table. Its drawing keys say what happens, its
modifier keys say how; the keys of one beat happen together, so the beat lasts as long as its longest one, and
`hold` adds a pause after it (alone, it is the pause).

| Beat | What it draws | Modifiers |
|---|---|---|
| `key` | a keycap pressed: `super`, `enter`, `esc`, `tab` or `bang` | `phase` |
| `type` | words typed into the mini pill, 25 characters a second | `ghost`, `phase` |
| `stone` | the stone takes a face: `rest`, `listening`, `working`, `needs`, `done`, `stopped` or `resting` | `phase` |
| `line` | the line above the pill; `""` clears it | `tag`, `tone`, `command`, `counter`, `buttons`, `phase` |
| `queue` | asks that wait, as grey chips above the line; `[]` takes them away | `tag`, `wait`, `phase` |
| `window` | an app window slides in from its drawer (a title); `false` or `""` slides it out | `tag`, `rows`, `columns`, `lock`, `doc`, `reload`, `from`, `phase` |
| `card` | a desk card: `watching`, `now` or `machine` | `tag`, `rows`, `title`, `why`, `phase` |
| `picture` | a picture card above the line (its title) | `tag`, `boxes` or `bars`, `lit`, `phase` |
| `fold` | the desk's cards fold to strips (`true`) or come back (`false`) | `tag`, `strips`, `phase` |
| `hold` | a pause, in ms | `phase` |

A beat needs at least one drawing key, and a modifier needs a drawing key it goes with. The validator says so.

### The vocabulary, key by key

| Key | Takes | Meaning |
|---|---|---|
| `key` | `super`, `enter`, `esc`, `tab`, `bang` | Drawn as the marks ⊞, ↵, Esc, Tab and !. It comes in, is down from about 100 ms to 260 ms, where its effect lands at 120 ms, and is gone by 360 ms. `super` makes the stone lean toward the text; `enter` sends what is in the pill, which empties it; `tab` takes the faint completion. |
| `type` | a string | The words, typed from the first character at 25 a second (counted in UTF-16 units, as the renderer's JavaScript counts) and then 240 ms of rest. What is typed is the tip's own words, so it is `typed` and needs no `tag`. |
| `ghost` | a string | With `type`: the faint completion that follows the typed words once they are all in. |
| `stone` | `rest`, `listening`, `working`, `needs`, `done`, `stopped`, `resting` | The stone's face. `working` rolls a third of a turn a second, `needs` knocks twice on a 1.6 s beat with the glow breathing, `done` hops once, `listening` leans six degrees toward the text (over 240 ms, as it does after `super` or typing), `stopped` is the grey square, `resting` the hollow grey stone, `rest` stands still and stands a leaning stone up. While the scene plays the stone shows the motion; on its last frame it stands still in its face. |
| `line` | a string | The line above the pill (`""` clears it). Needs a `tag`. |
| `tag` | `template`, `typed`, `model`, `output`, `caption` | Where the beat's strings come from. See [The truth rule](#the-truth-rule). |
| `tone` | `amber`, `red` | The line's marked edge: amber for a step that touches the system, red for one no restore point can undo. On the card it is drawn in ink. |
| `command` | a string | With `line`: the command, in mono. |
| `counter` | `true`, `false`, or a number of seconds | With `line`: the seconds counter, which counts while the stone works. A number starts it from there. `false` stops it. |
| `buttons` | one or two labels, such as `["Undo", "Details"]` | With `line`: the line's buttons. They are tagged `template`. |
| `queue` | a list of at most two asks, or `[]` | Asks the machine keeps while it cannot run them: a grey chip for each, between the line and the pill, holding the ask in the user's own words (they are `typed`). `[]` takes them away. |
| `wait` | a string | With `queue`: the small word on each chip that says why it waits ("paused"). Without it a chip says "next". Needs a `tag`. |
| `window` | a title, or `false` or `""` | An app window slides in from its drawer in 300 ms. A title with `reload` and a window already open changes it in place. |
| `rows` | strings, or `[label, value]` lists | With `window`: its rows, at most three, arriving 200 ms apart. With `card`: its rows (see `card`). |
| `columns` | `[heading, cell, cell, ...]` lists | With `window`: columns added to the rows, one every 200 ms. |
| `lock` | a string | With `window`: the window shows its lock screen with these words instead of rows. |
| `doc` | `true` or `false` | With `window`: its body is a document, a few lines, not rows. |
| `reload` | `true` or `false` | With `window`: the window is already open and reloads in place, with no slide. |
| `from` | `bottom`, `left`, `right` | With `window`: the edge it slides in from. `bottom` is the default. |
| `card` | `watching`, `now`, `machine` | A desk card arrives (the desk's own fade and scale from 0.6 over 150 ms), or, if it is already there, changes in place. Fields not named again keep what they had. |
| `title` | a string | With `card`: its title. Now's is the task. |
| `why` | a string | With `card`: the small line under the title ("step 2 of 3"). |
| `picture` | a title | A picture card above the line. On the card the title is not drawn. |
| `boxes` | a list of names | With `picture`: boxes streaming in, one every 320 ms, joined by arrows. |
| `bars` | `[label, percent]` or `[label, percent, "amber"]` lists | With `picture`: bars that grow, one every 320 ms, as a disk does. |
| `lit` | an index | With `picture` and `boxes`: the box that is lit. A bar has its `amber` tone for emphasis instead. |
| `fold` | `true` or `false` | The cards fold to strips beside the pill, or come back. |
| `strips` | a list of labels | With `fold`: what the strips say ("1 counting"). Needs a `tag`. |
| `hold` | milliseconds | A pause. With another key it adds to that beat. |
| `phase` | `1` or `2` | On the beat that opens the second and the third caption. Give both, in order, or neither: with none, the phases are thirds of the scene. |

Rows by card:

| Card | A row is | The value |
|---|---|---|
| `watching` | `[label, value]`: a job and its reading, such as `["Timer, 25 min", "24:59"]` | any string |
| `now` | `[label, state]`: a step of the route | `done`, `current` or `next` |
| `machine` | `[label, meter]`: a reading and how full it is | a whole number, 0 to 100 |

### An example

```toml
beats = [
  { key = "super" },
  { type = "Make me a ticketing app" },
  { key = "enter", phase = 1 },
  { stone = "working", line = "On it", tag = "template", counter = true },
  { line = "Building Tickets, 140 lines", tag = "template" },
  { window = "Tickets", tag = "model", rows = [["Printer jams on floor 3", "High"]], phase = 2 },
  { line = "Made Tickets. It is open now.", tag = "model", buttons = ["Undo", "Details"], stone = "done" },
]
```

## Timing

A scene runs 6 to 8 s and never over 10: `validate(tip)` reports one over 10 s, and `validate(tip, strict=True)`
holds a shipped tip to 6 to 8 s. The numbers are in `tip_scene.py`, mirrored in `TipScene.js`, and
`test_the_renderer_keeps_the_same_timing_as_the_validator` fails if they drift apart.

| What | Takes |
|---|---|
| `key` | 360 ms, in the 300 to 400 the brief asks for |
| `type` | 40 ms a character (25 a second) and 240 ms of rest |
| `stone` | `done` 700 ms (the hop), `needs` 1600 ms (one beat of knocks), `working` 600 ms, the others 300 ms |
| `line` | 900 ms, the first 200 a fade; clearing one takes 200 ms |
| `window` | 300 ms to slide on (none for a `reload`), 200 ms per row and per column, then 300 ms to read; sliding out takes 300 ms |
| `card` | 800 ms: 150 ms of arrival and a pause |
| `picture` | 300 ms to slide on, 320 ms per box or bar, then 600 ms to read |
| `fold` | 1000 ms, the fade 150 ms |
| `queue` | 300 ms, the fade 200 ms |
| `hold` | what it says |

Slides take 300 ms on the system's curve, (0.16, 1, 0.3, 1), which does not overshoot; fades take 200 ms. Nothing
bounces and nothing loops. Every scene ends on its payoff frame and holds it for ever: that frame is the
reduced-motion still and, drawn at card density, the card's poster.

Captions: three per tip, at most three words each, one for each phase. The phase that is current is the one the
clock is inside.

## The stone in the kit

`Kit.Stone` (`share/qml/Bombadil/Stone.qml`) is the mark, drawn and animated. It moved out of `shell/` because a
second place draws it, and it is internal for the same reason `TipScene` is. The pill uses it as it always did.
Three properties are new, each defaulting to what the pill needs:

| Property | Default | |
|---|---|---|
| `animate` | `true` | `false` gates every animation and the settle timer: the stone is painted at once in its face's colour, and its pose is plain values the host writes. Nothing runs. This is how a scene draws it one frame at a time. |
| `ink` | `""` | `"fg"` or `"muted"` draws the stone, its glow, square, outline and dashes in that ink, with no state colour: for use outside the pill. |
| `small` | `false` | The identity brief's 16 px drawing: the stone full-bleed and the b cut as the brand's own outline, the path in `bombadil-symbolic.svg`. The card's mini pill uses it. |

The geometry and the tests that hold it are in [`brand/README.md`](brand/README.md#stone-in-the-kit); the look is
in the [identity brief](design/identity-brief.md). The stone keeps its own `reducedMotion`, default `false`.

## The truth rule

A storyboard must not show what the real machine would not say. So every string a scene shows carries a tag, and
a beat with strings (`line`, `window`, `card`, `picture`, a `fold` with `strips`) needs its `tag`:

| Tag | The string is | Checked by |
|---|---|---|
| `template` | a sentence the code really says, placeholders filled ("Building Tickets, 140 lines") | matched whole against the sentences in the code, so it fails the day one changes |
| `typed` | the tip's own words, what the button puts in the pill | `type` beats are always `typed`; compared with the tip's `words` |
| `model` | a reply in the agent's register, at most four plain lines | read by a person: it is a plausible answer, not a fixed one |
| `output` | a command's example output, drawn in mono | read by a person |
| `caption` | one of the three phase captions | at most three words |

`tip_scene.strings(tip)` returns every string a scene shows as `(tag, text)`, the captions included, for the tips
table's own tests. Button labels are `template`. A row's string value (`"High"`, `"24:59"`) is read with the
row's tag; a number is a meter and is not text.

## Tests

- `tests/test_tip_scene_qml.py` draws the fixture tips offscreen and grabs frames at chosen `t`: every beat kind,
  the poster being the last frame, the card's sizes and 12 px floor, no words or numbers in a poster's machine
  pictures, the stone's colours and ink, the app's measures, reduced motion's three stills, that nothing runs
  while the scene is not playing (no running animation or timer anywhere under the window), that `play()` runs
  one animation, emits `finished` once and holds, and that every line fits on one line.
  Set `BOMBADIL_SCREENS=<dir>` to save pictures of the frames.
- `tests/test_tip_scene.py` tests the validator: unknown keys, bad values (a list or a number where a word goes
  is reported, never a crash), strings without a tag, over 10 s, the wrong number of captions; and that the
  renderer's JS keeps the timing and vocabulary the validator keeps.
- `tests/test_tips.py` validates every row of the tips table with `tip_scene.validate`.
- `tests/test_stone_qml.py` holds the stone: its faces, its pose, `animate`, `ink`, the 16 px drawing.
- `tests/test_appkit_internal.py` holds that `bombadil-app check` refuses `Stone` and `TipScene` in an app, that an
  app's own component of that name is let through only where QML really builds it, and that the load refuses too.

## Writing the beats for a tip

1. Say the tip in three phases (you ask, it works, you have it) and give each a caption of at most three words.
2. Write the beats, putting `phase = 1` and `phase = 2` on the beats that open the second and third phase.
3. Tag every string. Anything the code says is `template`, and it has to be the real sentence.
4. Run `validate(tip, strict=True)`: it names the beat that is wrong, and says if the scene is not 6 to 8 s.
5. Look at the poster and the stage with `BOMBADIL_SCREENS`. The poster is the last beat's state, so end on the
   payoff, and keep a line short enough for the card.

## The frame

`TipScene.frame` (and `Scene.frameAt(compiled, t, still)` in `TipScene.js`) is plain data, what the tests read
and what `Stage` in `TipScene.qml` binds to. Every field is a function of `t` alone. `null` means the thing is
not on the screen.

| Field | Holds |
|---|---|
| `t`, `ended`, `phase` | the clock, whether the last frame has been reached (the stone then stands still), the current caption, 0 to 2 |
| `key` | `null`, or `{label, name, alpha, down}`: the keycap, and whether it is pressed |
| `pill` | `{text, ghost, caret, border, placeholder}`; `border` is `"working"`, `"listening"` or `"idle"` |
| `stone` | `{face, turn, knock, lean, glow, hopY, hopSx, hopSy}`: the face and the pose `Kit.Stone` is given |
| `line` | `null`, or `{text, tag, tone, command, buttons, alpha, mono, secs}`; `secs` is `-1` with no counter |
| `win` | `null`, or `{title, lock, doc, from, slide, alpha, rows, cols}`; a row is `{label, cells, value, alpha}`, a column `{head, cells, alpha}` |
| `cards`, `cardsLeft`, `cardsRight` | `{kind, rows, title, why, arrive, shown}` for each desk card; `machine` stands in the right rail, the others in the left |
| `fold` | `{folded, p, strips}`: how far the cards have folded, and the strips' labels |
| `pic` | `null`, or `{title, boxes, bars, lit, slide, alpha}`; a box is `{label, alpha}`, a bar `{label, pct, tone, grow}` |

## Adding a beat kind

A new kind of beat is a small piece of `TipScene` (the brief's examples: session dots and the knock when the coding
sessions land, a Mail window when Mail lands). It touches these places, and the tests fail until each agrees with
the others:

1. `src/bombadil/tip_scene.py`: the drawing key in `DRAWS` and each modifier in `MODIFIERS` (with the drawing keys
   it goes with), its length in `beat_ms`, its values in `_check_beat`, its words in `strings()` if it shows any.
2. `share/qml/Bombadil/TipScene.js`: the same lists (`DRAWS`, `MODIFIERS`), its length in `beatMs`, what it sets in
   `frameAt` (a thing remembers when it was set, `since`, so its motion is a function of `t - since`) and its field
   on the frame.
3. `share/qml/Bombadil/TipScene.qml`: draw the field in `Stage` at both densities, from `m` (the measures) and `f`
   (the frame) alone, with `font.family` on every Text. Give it an `objectName` for the tests. If it moves, it moves
   because `t` does: no timer, no `Behavior`, no animation of its own (`tests/test_motion.py` and the "nothing
   runs" tests fail it).
4. `tests/tip_fixtures.py`: a fixture that uses it, so `test_the_fixtures_between_them_use_every_beat_kind...`
   holds; `tests/test_tip_scene_qml.py`: a frame test and a look at its poster. `test_the_renderer_keeps_the_same_...`
   fails if the two lists or the timing drift apart.
5. This page: the beats table, the vocabulary table and the timing table (`test_the_documentation_names_...` fails
   until every key and value is in backticks here).

A drawing that is a component of its own (a mini Mail window) goes in `share/qml/Bombadil/` beside `TipScene.qml`,
and is internal for the same reason: see the next section.

## The internal components

`Kit.Stone` and `Kit.TipScene` are in the kit's `qmldir` so the shell and the built-in apps can use them, and they
are internal: not in the apps skill (a test keeps them out), and refused in an app the agent writes.
`refused_components` in `src/bombadil/appkit/check.py` is what refuses them. It runs in `bombadil-app check` (so in
`create_app` and `check_app`) and on every load and reload of an app, and it reports `file:line: Name ...` with what
to use instead. `INTERNAL_COMPONENTS` maps each name to that message: a new internal component is one more entry
(and one more line under the "Internal" comment in the `qmldir`).

- It reads every `.qml` file of the app except `data/` and hidden folders, ignores comments, and finds a use as an
  object declaration: `Stone {` after `import Bombadil`, or `Kit.Stone {` after `import Bombadil as Kit`.
- The built-in apps under `share/apps` (the Tips app) are let through: `apps.is_builtin`.
- QML lets the later import win, and a folder's own files are imported first and weakest. An app with its own
  `Stone.qml` beside a `main.qml` that says `import Bombadil` therefore gets the kit's stone, not its own. The check
  follows the engine: a use is let through where `import "."` (or `import "lib"` for a file in a folder) comes after
  `import Bombadil` in that file, and refused otherwise, with the message that says to add `import "."` below
  `import Bombadil` or to rename the file. `Kit.Stone` is always the kit's, and `Stone` beside
  `import Bombadil as Kit` is always the app's own.
- A refused app does not load, but its `app.py` is still read, so fixing the QML does not lose an edit to it.

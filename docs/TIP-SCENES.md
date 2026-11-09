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
| `share/tips/tips.toml` | The twenty tips, each with its scene in `beats`: see [The twenty scenes](#the-twenty-scenes). |
| `src/bombadil/tip_scene.py` | The same vocabulary and timing for Python, standard library only, and the validator. `tests/test_tips.py` calls `validate(row, strict=True)` on every row of `share/tips/tips.toml`, so a bad scene in the table fails there. |
| `tests/tip_fixtures.py` | Seventeen tips written for the renderer's tests, which between them use every beat, key and modifier. The renderer is tested against these, not against the twenty tips, so a tip's wording can change without a renderer test failing. |
| `tests/test_tip_scene_qml.py`, `tests/test_tip_scene.py` | The renderer's frames, sizes and "nothing runs" tests, and the validator's. |
| `tests/tip_truth.py`, `tests/test_tip_truth.py` | The truth test: the sentences the real code says, made by calling it, and every `template` string of every scene held to them. See [The truth test](#the-truth-test). |

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
    STR --> TRUTH["the truth test (test_tip_truth.py)<br/>template: the code's own sentences<br/>typed: the tip's words"]
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
| Desk cards | 84 wide; the title and the why each on one line, elided | 208 wide; the title and the why wrap to two lines, so a task is said whole and a step is not clipped |
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

**The pill's field** follows the end of an ask longer than the pill, as the real field does: the last words stay in
view and the first are scrolled out.

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
| `typed` | the tip's own words, what the button puts in the pill | `type` beats are always `typed`; the test compares them with the tip's `words`, and asks the launcher where each goes |
| `model` | a reply in the agent's register, at most four plain lines. Also the example content of a window (its title, rows, lock words) and the steps of a `now` card, which are the agent's own plan, whatever the beat's tag says | a person reads it for plausibility; the test holds it to four lines, plain, in the agent's register and the voice rules (no markdown, no second line, no emoji, no "!") |
| `output` | a command's example output, drawn in mono | the test runs the real command and holds the example to the shape of its output, and to the shell's own result |
| `caption` | one of the three phase captions | at most three words |

`tip_scene.strings(tip)` returns every string a scene shows as `(tag, text)`, the captions included, for the tips
table's own tests. Button labels are `template`. A row's string value (`"High"`, `"24:59"`) is read with the
row's tag; a number is a meter and is not text.

## The truth test

`tests/test_tip_truth.py` is what makes the truth rule more than a promise. It does not keep a copy of the
sentences: `tests/tip_truth.py` makes them, by calling the real code with fixture inputs and recording each
sentence by the role it plays (a line, a command, a button, a chip's word, a card's title, why, label or value, a
picture and its boxes and bars, a strip, a window, a lock). Then it walks every scene, and every string a scene
shows must be there, **whole**, for its role: a line is not found among the buttons, nor a strip among the lines.

| Where the sentences are made | By |
|---|---|
| A step ("Building Tickets, 140 lines", "Installing docker" and its command, the amber edge), the reply ("Installed htop."), "Stopped while installing docker.", the reason | `narrate.tool_step`, `shell_step`, `Narrator.summary`, `stopped_line`, `why_text` |
| What the launcher answers ("Opened Tickets.", "Folded the desk.", "Undone. System files go back to before ...", the Brain's lines), the lines of the pictures and the widget | `launcher.Launcher` (its `_app`, `_widget`, `_undo`, `_brain`, `_whyhere`, `doing`), `desk.Desk.apply`, `agentd.AgentD.picture` and `stop` |
| A timer's title and its ending, the Machine card's rows and its why | `jobs.timer_title` and `ending`, `vitals.Vitals` |
| The pause and its chip words | `rest.words`, `kept`, `back` |
| The network picture and the disk picture, with their boxes and bars | `sysmap.capture_network` and `capture_disks` on the fixtures of `tests/test_sysmap.py` |
| The Watching and Now cards' labels, values and why, the strips, "On it", the buttons | the shell's own QML, driven by the harnesses of `tests/test_desk_qml.py` and `tests/test_pill_qml.py` |
| A command's output | `providers.Shell`, and the command itself |
| The Brain's sentence about a file | the fixture of `tests/test_brain_focus.py` |

What else the test holds, beyond "this sentence is said":

- **One step is one step.** A line with a `command` has the amber edge that command has; a window is the app the
  line before it names ("Building Tickets" then a `Tickets` window); a picture is the one "Drawing ..." and
  "Showing ..." say it is; a stop is of the step that was running and an undo of what was asked.
- **Pictures are the real ones.** The boxes of the network picture are a prefix of the nodes `capture_network`
  draws; the bars are filesystems `capture_disks` draws, amber exactly where it warns; the Machine card shows the
  rows the vitals make and how full each meter is; a fold shows the strips of the cards it folds; the reason is
  what `why` answers with.
- **Typed is the tip's words.** What a scene types is the tip's `words` (a trailing `…` means more follows, and
  `{app}` is filled), apart from the asks a stop, an undo or a pause is of, named in the test. Each goes where the
  scene says (the launcher's `match` says a picture, a widget, a rest, an undo, a command), and the word a scene
  completes is the word the pill completes.
- **A `model` string is plain.** At most four lines on the card, no markdown, no second line, no emoji, a
  sentence when it is a line.
- **The voice rules hold** over everything a scene says that is not the person's own: no "!", no em-dash, no
  "will", "tomorrow" or "later".
- **Every string has one of the five tags**, and `fields()` in the test, which says where each string is, is held
  equal to `tip_scene.strings`.

### What no code can be asked for

A string can be shown that no code says yet. It is named in `UNTIED` in `tests/test_tip_truth.py`, one string at a
time, as `(tip, role, string)` with why. Today it is three, all of the `keep` tip, whose Widgets card and Keep
choice are the keep piece's and are not on main: the window `Widgets` and the values `Keep` and `When needed`. An
entry is only allowed while the scene shows the string and no code says it; the day the code does say it, the test
fails until the entry is taken out, so the exceptions cannot outlast their reason.

### Keeping a scene true when a line changes

1. A real line changes (a word in `narrate`, a reply in the launcher, a card's label). The test that holds it
   fails in the scene that shows it: it names the tip, the role and the string, and the nearest sentence the code
   does say.
2. Change the scene's string to the new sentence in `share/tips/tips.toml`. If the sentence is longer, look at
   the poster and the stage again (below): the card holds about 36 characters on a line.
3. If the line is made by a function the registry does not call yet, add the call to `tests/tip_truth.py` with the
   role it plays. A scene string is never added to the registry by hand: the registry only ever holds what the code
   said when it was called.
4. A string that really cannot be asked of the code goes in `UNTIED` with its reason, and the PR that makes the
   code say it takes it out again.

To see the test do its work, change one real sentence, or one word of a scene's string: the test fails for that
scene alone and passes again when they agree.

## The twenty scenes

One scene for each row of `share/tips/tips.toml`, each 6 to 8 s, each tagged on every string, each ending on what
the tip gives you (its last frame is its poster and its reduced-motion still). They are written against the real
machine: what a scene says is what the code says (see [The truth test](#the-truth-test)), what it types is the
tip's words, and what it draws is what the desk draws.

| Tip | The scene | Its sentences come from |
|---|---|---|
| `make-an-app` | Super, the ask, Enter; "On it", "Building Tickets, 140 lines"; the Tickets window slides in; the reply, with Undo and Details | the pill, `narrate.tool_step`; the reply and the window's rows are the agent's |
| `picture` | the ask; "Drawing how you're connected"; the four boxes of the picture, streaming in; "Showing how you're connected." | `launcher`, `agentd.picture`, `sysmap.capture_network` |
| `esc` | an amber install step with its command, counting; Esc; "Stopping"; the grey square; "Stopped while installing docker." | `narrate.shell_step`, `Narrator.stopped_line` |
| `timer` | the ask; "Watching Timer, 25 min"; the Watching card counting; a window slides over it; the card folds to "1 counting" | `jobs.timer_title`, the Watching card, the desk's strip |
| `machine` | the ask; "Here is the machine."; the Machine card, its why and three meters | `launcher`, `desk.ASKABLE`, `vitals.Vitals` |
| `bang` | the bang key and `!uname -r`; "On it"; the output in mono, with Details | the pill; the output is what `uname -r` prints |
| `change-app` | Tickets open; the ask; "Changing Tickets, 180 lines"; the window reloads in place with a Due column; the reply | `narrate.tool_step`; the reply is the agent's |
| `open-app` | Super; "tic" and the faint "kets"; Tab, Enter; "Opened Tickets."; the window | `launcher.Launcher._app` |
| `passwords` | the ask; the build lines; the window on its lock screen; the reply says it locks after 5 minutes idle | `narrate.tool_step`; the lock words and the reply are the agent's |
| `whyhere` | a file open; "where did this come from?"; the Brain's own sentence about it; the word "brain"; the Brain window | the Brain's `focus` and `launcher._whyhere`, `_brain` |
| `draw` | the ask; "Drawing “How a VPN works”"; four boxes; the agent's explanation | `agentd.picture`; the explanation is the agent's |
| `toolchain` | the Now card with the route and its why; an amber step with its command; the next step; "Installed rust. cargo is ready." with Undo and Details | the Now card's own words, `narrate.shell_step`; the plan's steps are the agent's |
| `tests` | the ask; "Watching Shop tests"; the Watching card counting; the reply; "Shop tests is done in 3 min." | `jobs.ending`, the Watching card |
| `code-picture` | the ask; "Drawing “How the shop modules fit”"; cli, server and store with one lit; the explanation | `agentd.picture`; the boxes and the explanation are the agent's |
| `disk` | "Drawing your disks"; three bars, the fullest amber; "Showing your disks." | `sysmap.capture_disks` |
| `pause` | "pause Claude"; the pause line; an ask that waits as a grey chip; "resume claude"; "Claude is back. Running your waiting ask." | `rest.words`, `kept`, `back` |
| `desk` | a Now card and a Watching card; "desk"; both fold to two strips; "Folded the desk." | `desk.Desk.apply`, the strips' own words |
| `keep` | "widgets"; the Widgets card with its Keep choice; the Machine card kept | not on main: `UNTIED`, see above |
| `why` | an amber install still counting; Super, "why"; the agent's reason | `Narrator.why_text` |
| `undo` | an install and "Installed htop." with Undo; "undo"; "Undoing the last change"; the real undo reply | `narrate`, `launcher.Launcher._undo` |

Where a scene is less than the whole tip, it says so here, so the next person does not take it for a mistake:

- `desk` ends folded and does not draw the second word that unfolds the desk: with the cards back the poster's
  Watching card runs off the top of it.
- `why` ends on the reason it gives, not on the return to the step: the reason is the payoff, and the step it
  came from is the scene's first half.
- `code-picture` draws the picture but not a click on a box that opens the file.
- `passwords` shows the lock screen, but not the time that passes before it locks.
- `picture` is drawn for a machine on a cable, four boxes, which is one of the shapes `capture_network` makes.
- `keep` draws the Widgets card as a window and not the restart a kept widget survives; its three strings are
  `UNTIED` until the keep piece lands.
- The example content of a window (the Tickets rows, Notes, the Brain's rows) is tagged `model`: it is the
  agent's work, not a sentence of the code.

## Tests

- `tests/test_tip_scene_qml.py` draws the fixture tips offscreen and grabs frames at chosen `t`: every beat kind,
  the poster being the last frame, the card's sizes and 12 px floor, no words or numbers in a poster's machine
  pictures, the stone's colours and ink, the app's measures (the card's width, the heading that wraps on the stage
  and elides on the poster), reduced motion's three stills, that nothing runs while the scene is not playing (no
  running animation or timer anywhere under the window), that `play()` runs one animation, emits `finished`
  once and holds, and that every line fits on one line.
  Set `BOMBADIL_SCREENS=<dir>` to save pictures of the frames.
- `tests/test_tip_scene.py` tests the validator: unknown keys, bad values (a list or a number where a word goes
  is reported, never a crash), strings without a tag, over 10 s, the wrong number of captions; and that the
  renderer's JS keeps the timing and vocabulary the validator keeps.
- `tests/test_tip_truth.py` is the truth test above. It needs PySide6 (the shell's own QML makes some of the
  sentences) and is skipped as a whole where QtQml is not installed.
- `tests/test_tips.py` holds the twenty scenes as a table: every row has beats and `validate(tip, strict=True)`
  passes, each runs 6 to 10 s and ends on its payoff, not a clearing. It draws them: every row with `card = true`
  renders its poster with no text under 12 px and nothing off the poster, no poster shows a name, a number or a
  value of the machine or a label on a button, and none uses orange beyond the card's state colours; every tip
  renders at the app's density at its last frame with nothing elided; three stage frames of each Start-here tip
  (the ask, the work, the result) differ from one another, and reduced motion draws three stills.
  It saves the contact sheet of the twenty posters and the one of the twenty stage payoffs on every run, and
  keeps them in `BOMBADIL_SCREENS` when that is set, with a picture of each tip and the three stage frames of each
  Start-here tip.
- `tests/test_stone_qml.py` holds the stone: its faces, its pose, `animate`, `ink`, the 16 px drawing.
- `tests/test_appkit_internal.py` holds that `bombadil-app check` refuses `Stone` and `TipScene` in an app, that an
  app's own component of that name is let through only where QML really builds it, and that the load refuses too.

## Writing the beats for a tip

1. Say the tip in three phases (you ask, it works, you have it) and give each a caption of at most three words.
2. Write the beats, putting `phase = 1` and `phase = 2` on the beats that open the second and third phase. Type
   the tip's own words, and let what the machine says be what the code says: find the sentence in the code first
   (`narrate`, `launcher`, `desk`, `jobs`, `vitals`, `rest`, `sysmap`) and copy it, with its placeholders filled.
3. Tag every string. Anything the code says is `template`, and it has to be the real sentence. The agent's reply,
   the example content of a window and the steps of a plan are `model`; a command's output is `output`.
4. Run `tests/test_tip_truth.py`. A `template` string that is not a sentence the code says fails there with the
   nearest one that is. If the code cannot be asked for it yet, name it in `UNTIED` with why, and say which
   change takes it out.
5. Run `tests/test_tips.py`: `validate(tip, strict=True)` names the beat that is wrong and says if the scene is
   not 6 to 8 s, and the render tests fail on a poster with text under 12 px or a stage with anything elided.
6. Look at the poster and the stage with `BOMBADIL_SCREENS=<dir>` (the two contact sheets, and the three stage
   frames of a Start-here tip). The poster is the last beat's state, so end on the payoff, and keep a line short
   enough for the card.

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

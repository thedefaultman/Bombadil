# Tips, and the desk after boot

You choose Claude, sign in, and the browser panel slides away. The line says "Signed in to Claude. Ask me for anything." and fades. Just above the pill a faint line reads "Super to talk. Esc to stop. Say undo to go back." Then one card settles into the bottom-right corner, the first card you have ever seen on this machine. It says **Tips**, and under that "Start here · 1 of 6". Its picture is a small drawing of this same screen: a pill, a line above it, and a window called Tickets with three rows in it. Under the picture there is one line, "Say what you need. It gets made.", and one button that reads exactly what you would type: **Make me a ticketing app**. Nothing on the card moves. You press the button and the words appear in the real pill with the cursor at the end, waiting. You press Enter, the stone rolls orange, the line counts "Building Tickets, 140 lines", and Tickets slides in, just as the picture showed. A little later the card turns to the next tip: "Ask for a picture, not a paragraph." [How am I connected?]. You click the picture instead of the button, and the Tips app slides in from its drawer and plays that tip once, in seven seconds: the key, the words typing in, the stone, the picture card rising, the closing line. Then it holds. After your first week the card is gone, and the desk is wallpaper and a pill again. "tips" brings the app back any time.

This is the design for two things the owner asked on 1 Oct 2026: whether the desk has widgets by default after boot, with the user able to change them and set their own defaults; and a Tips widget, a carousel on the desk that opens into a full app, where each tip is one line next to a short animation of the workflow in action, seeded with coding and productivity workflows that grow as Bombadil grows. It sits on The Bombadil Desk (widgets brief, 27 Sep), How Bombadil Should Feel (UX brief), Bombadil's Voice, Bombadil Tends Itself (self-improvement), the identity brief and the design system, whose decisions stay in force except where the "Changes" section names one and argues it. It was made from six research sweeps of the code on main (dbe6e8a) and the unmerged branches, three independent drafts, a merge, and three adversarial checks (rules, buildability, a new user's eyes). Nothing is built until the owner answers the four questions below.

## The short answer: the desk after boot today

**There are no widgets on the desk after boot, on purpose.** The widgets brief, confirmed on 28 Sep, says a widget is present only while it has something to say, so a machine at rest is wallpaper and the pill (widgets-brief rule 4; `shell/DeskState.qml` presence rules; `tests/test_desk_qml.py` checks that a resting desk has no cards and no strips; the rail windows are not even mapped).

| Widget | When it shows today | Built? |
|---|---|---|
| Now | during a turn with a plan of two or more steps, or a step that touches the system | yes |
| Watching | while a job, download or timer counts; a job that failed in the last 30 minutes can show at boot | yes |
| Machine | when memory, disk, heat or the sessions' ceiling crosses its line, or for 30 s after "how's the machine" | yes |
| Needs you | when two or more coding sessions wait | drawn, but fed only by the coding-sessions branch (PR #4) |
| Away, Alive | never: no feed and no face yet | designed only |

**What a user can change today:** the word "desk" folds every card to a chip beside the pill and back; "show machine", "hide watching" and the like show or put away one widget; a sentence such as "put watching on the right" goes to the agent's `desk` tool, which works only when your own words asked for the desk. All of it is kept in `~/.local/state/bombadil/desk.toml`, outside the restore points, so it survives a reboot and an undo never moves it. Needs you cannot be put away.

**What a user cannot do today:** keep a widget up at rest; see a list of the widgets there are; make a widget of their own (designed, not built); or change anything with a click or a drag (the strips' click and hover are not wired; the desk thread is building dragging now). And nothing teaches what to ask: there is no tips word, "what can you do" gets at most four plain lines from the model, and the first-minute pieces the UX brief designed (example chips above the empty pill, the ten-second tour) exist on no branch.

## Four questions

The owner answers with one letter each, for example "1a 2a 3b 4a". The recommendation is marked.

**1. After the first sign-in, what should a new user see of Tips on the desk?**
- **a) (recommended)** A still Tips card in the bottom-right corner for their first week: one tip at a time, a picture of the result, one line, and a button with the exact words. It turns when they press its arrows or after they try a tip, and it leaves by itself after seven days of use, once the six first tips are tried, or as soon as they close it.
- b) A small "tips" label beside the pill for the first week that opens the same card on hover or click, the way the "noticed" chip works.
- c) Nothing on the desk: Tips is only an app they open by typing "tips", "help" or "what can you do".

*Why a:* the card is the first thing a new person sees, and it shows what to ask without being asked; after the first week the desk is the wallpaper and the pill again. A label is easy to miss on someone's first day, and an app nobody knows about is never opened. A card that turns by itself every few seconds is left out: on this desk anything that moves means the machine is working.

**2. When someone presses a tip's button, such as "Make me a ticketing app", what should happen?**
- **a) (recommended)** The sentence appears in the pill with the cursor at the end and waits: Enter runs it, Esc drops it, and they can add their own details first.
- b) It runs at once.
- c) Quick ones that need no AI ("How am I connected?") run at once; ones that build or install something wait in the pill.

*Why a:* pressing Enter themselves teaches a new person where to type, lets them make the words theirs ("...for a team of four"), and never starts a minute-long build by accident. Whatever is chosen, the voice thread's empty-place buttons should behave the same, so a button never means two things.

**3. How should someone choose which widgets are on their desk after every boot?**
- a) With words only: "keep machine on the desk" keeps it up after every boot; "hide machine" stops keeping it. A tip teaches it.
- **b) (recommended)** The same words, plus one small Widgets card (type "widgets") that lists every widget with Keep, When needed and Off.
- c) They don't: widgets keep appearing only when they have something to say, and Tips in the first week is the one exception.

*Why b:* the owner asked that users can change widgets as they want and set their defaults after boot; a card that lists what exists is the visual way to do that, and it is the same shape as the AI card that already opens from the stone. It changes nothing for anyone who never opens it: the shipped default stays the quiet desk.

**4. Should Tips replace the first-minute pieces that were designed but never built: the three example buttons above the empty pill and the ten-second tour?**
- **a) (recommended)** Yes: the Tips card becomes the one place a new user sees what to ask. The faint first-day line "Super to talk. Esc to stop. Say undo to go back.", the one-time "Changed your mind? Say undo." and the Install button on the USB stick stay.
- b) Replace only the tour; the three example buttons still appear above the empty pill on a Super tap, taken from the same list as Tips.
- c) Keep both as designed, and have the Tips card wait until the tour has played.

*Why a:* none of those pieces exists yet, and two places suggesting things in the same first minute compete for the same glance. The faint line stays because it is the only place the Super key is written down. The habit chips meant to replace the example chips after about 20 asks go too: noticing repeats is Noticed's job.

## Build first

Two pieces, each its own PR with its docs, as every piece is.

### 1. Tips (L)

In this order, each step tested before the next:

1. **Reduced motion everywhere (S).** `Theme.qml` gains a writable `reducedMotion`, false by default. `shell.qml` sets it from `BOMBADIL_REDUCE_MOTION` at start (it reads the variable today but passes it only to the stone and the wallpaper), and the app runtime sets it from the same variable, so apps can see it for the first time. The desk's fold, wash, ring and pill width honour it, as the widgets brief already requires and the code does not yet do.
2. **The tips table and `tips.py` (M).** `share/tips/tips.toml` holds one row per tip (below). `src/bombadil/tips.py` in agentd decides which tips this machine can back (a tip whose gate is not met is shown nowhere), keeps `~/.local/state/bombadil/tips.json` (the first-ready date, the days of use, the tried ids, the known ids, the tip to open on; agentd is its only writer), marks tips tried from agentd's own live events with no model, and sends one `tips` message to the shell only when something changes. `bombadil-install` copies `tips.json` and `desk.toml` to the installed disk, so what the stick taught is kept.
3. **`Kit.Stone` and `Kit.TipScene` (L).** The stone moves from `shell/Stone.qml` into the kit as an internal component and gains `animate` (gates every animation and its settle timer), `ink` (draws in fg or muted with no state colour, for use outside the pill) and the identity brief's 16 px drawing; the pill keeps using it with animate on. `TipScene` is pure QtQuick (Theme, Shapes, Kit.Stone, and Kit.Diagram at app size only, with `font.family` on every Text), the same way `Kit.Diagram` already serves both the shell's card host and apps. It has two densities: card (a still poster) and app (plays). Both are internal: not in the skill's components list, and `check_app` refuses them in apps the agent writes.
4. **The Tips app and its words (M).** A built-in kit app in `share/apps/tips`, opened by local words (a `TIPS` table in `launcher.py` like the Brain's). `Agent.draft(text)` joins the kit's `Agent`: a one-shot summon that fills the pill, the Brain's "Ask about this" slot made general.
5. **The Tips card on the desk (M).** A `tips` entry in `desk.py`'s `WIDGETS` at the top of the right rail, following the DESK.md recipe for a new widget, and a `TipsCard` face in `DeskRail`'s loader. Its presence comes from the `tips` message. Its button sends `{type: "summon", text, tip: true}` through `DeskState.outgoing`; its title, picture and "All tips ›" ask agentd to open the app on that tip.

### 2. Your desk (M)

`keep` for widgets with a calm face, the Widgets card, and a calm Machine. A `keep` op in `desk.py` and on the agent's `desk` tool (the gate already accepts the verb), `kept = [...]` in `desk.toml` beside the desk thread's `stripped`, the local words "keep <widget>" and "keep <widget> on the desk", the word "widgets", a `WidgetsCard.qml` above the pill (the AI card's shape), and a calm vitals mode for a kept Machine. This touches the desk's code, so it belongs with the desk thread's drag work.

**Later:** strip click and hover-peek for every strip, and drag by the title (desk thread); tips per app from the apps' `[[does]]` rows; "Watch again" from a turn's event log, using the same player; scenes recorded by the headless desktop test, if hand-written storyboards drift.

## Tips' own rules

1. **A tip is one line, one picture and the exact words.** No paragraph anywhere, in the card or the app. The line says what you get; the picture shows it; the button is the sentence you would type.
2. **A tip is true on this machine.** It shows only what this machine can do right now, as it really looks: the same words on the line, the same stone, the same cards. A tip the machine cannot back (its code is not merged, there is no app or project yet, undo on the live USB) is not shown anywhere, not even greyed.
3. **Nothing runs on a click.** A tip's button fills the pill; your Enter runs it, as your own ask with its restore point (widgets rule 7: no click starts work you did not ask for).
4. **Only the app moves.** The desk card is a still picture that changes only when you turn it or try a tip. Scenes play inside the Tips app you opened, once, and hold their last frame. Nothing loops, nothing auto-advances, nothing plays at rest, and every scene has a still version.
5. **It never speaks.** Tips has no line above the pill, no mark on the stone, no sound, and never appears during a turn or over setup.
6. **It steps aside.** After the first week Tips is gone from the desk unless you keep it, and new tips after an update wait quietly inside the app.
7. **Tips teaches what you have not asked; Noticed handles what you repeat.** Tips never offers to make a word, an app or a widget from what you do. The agent never writes tips.

## The desk after boot

**The rule stays, with one reading added.** The desk still shows only what has something to say. In your first week, an untried Start-here tip is something to say. A widget you keep always has something to say. Nothing is kept by default.

- **First boot** (the live USB, or an install that skipped the stick): the wallpaper, the pill, the faint first-day line, and the Tips card alone in the right rail, so it sits nearest the pill. On the live USB every boot is a first boot, because nothing survives a restart; every live boot also asks for the AI and the sign-in again.
- **The first week** (seven days on which you typed in the pill): the same card after every boot, on the first tip you have not tried. The why line reads "Start here · 3 of 6".
- **Tips leaves** when all six Start-here tips are tried, after seven days of use, or at once on its × or "hide tips", which answers "Put Tips away. Say tips to see them again." That choice is in `desk.toml` and survives the install.
- **After that:** the wallpaper and the pill, as confirmed on 28 Sep, with no switch to flip. "tips" opens the app. "keep tips on the desk" brings the card back for good; it then shows the first untried tip at each boot and turns through Start here, then the other tips whose button fits the card.
- **Kept widgets:** "keep machine on the desk" (or Keep on the Widgets card) shows the calm Machine card after every boot.
- **Unchanged:** Machine still rises past a line; Watching still shows a recent failure; Needs you appears once the coding sessions land; the desk you leave (put away, folded, rails, order) is the desk you boot to.

## Shaping your desk

**Three states per widget.** Keep (on the desk at rest, after every boot), When needed (today's rule: only with something to say), Off (put away). Only a widget with a calm face can be kept: today that is Tips and Machine. Now, Watching and Needs you have nothing to show when nothing is happening, so they offer When needed and Off (Needs you only When needed, as decided). Away, Alive, Noticed and widgets the agent makes join the list when they are built.

**The Widgets card (piece 2, if 3b).** Type "widgets" and a 360 px card rises above the pill, the same shape as the AI card the stone opens: title "Widgets", then one row per widget with its name in 13 px, the question it answers in 12 px muted ("Is the machine in trouble?", the widgets brief's own), and a small three-part control on the right, Keep · When needed · Off. The chosen part is raised with fg text; nothing on it is orange, because orange is the machine acting. A press changes `desk.toml` through the same `Desk.apply` the words use, and the line says what changed (proposed: "Machine stays on the desk."). "Reset the desk" at the bottom puts every widget, rail and order back to the shipped default. Esc or a click elsewhere closes it.

**Words** (local, no model): "keep machine" and "keep machine on the desk" keep it. While Machine is kept, "hide machine" only stops keeping it and answers "Machine is no longer kept. It still comes up when something needs a look."; a second "hide machine" puts it away as it does today, which also stops its warnings. "keep watching" answers "Watching shows only while something counts." and changes nothing. "show machine" still raises it for 30 s. "put tips on the left" and other sentences go to the agent's desk tool as today.

**A kept Machine** runs a calm vitals mode: a sample every 5 s, figures rounded so a message goes out only when a shown figure changes, no walk of the memory groups while calm, rows and meters changed in place without motion. Its card reads "The machine is fine" until a line is crossed, when it says why, as it does today.

**Moving:** by sentence today, and by dragging a card by its title once the desk thread's drag is proven in the VM.

## The Tips card

**Family.** A standard desk card: 300 × 290, the same height for every tip so the cards above it never jump; radius 12, the card glass, a 1 px border; no edge bar, no dot and no accent, because colour says who and a tip is nobody's. It fits a 1280 × 720 rail, which holds 600 px.

**Top down:**
1. **Title** "Tips", 14/600. A click opens the app on this tip.
2. **Why line**, 12 muted with tabular figures: "Start here · 1 of 6".
3. **Picture**, 272 × 153, radius 8: the sunken ground with the wallpaper's two faint glows, and a still poster of the tip's payoff drawn at card density (the 16 px stone in ink, a 32 px mini pill, 12 px text, never smaller). Line buttons are unlabelled outlines, so no Undo sits on a widget. Pictures of the machine are generic shapes with no names, numbers or values. A click opens the app on this tip, playing.
4. **One line**, 13 fg, tested to fit 272 px.
5. **Bottom row:** the button with the exact sentence (28 high, radius 14, raised glass, a 1 px strong border, 13 fg), or, for a tip whose words only make sense mid-turn (Esc, why, undo, where did this come from), those words or keys drawn as marks with no button; then "All tips ›" in muted 12 at the right.

**On hover:** ‹ and › (24 px muted icon buttons, top right) and a × fade in over 120 ms; the mouse wheel also turns tips. There are no page dots: on this desk a dot means who.

**What moves, and only this:** the arrival (the desk's own fade and scale from 0.6 over 150 ms toward the rail's corner, once); a 200 ms crossfade of picture, line and button when you turn a tip, or 12 s after a tried tip's turn ends; and the standard fold to its strip under windows (150 ms out, 400 ms back). No timer, no loop, no auto-advance, no scene on the desk. Under reduced motion every change is instant.

**Strip:** 28 high, "tips" in muted 13, no dot. A click opens the Tips app, which is the card's full face, as Noticed's chip opens its window.

**Placement and presence.** The right rail's top slot, the least urgent; from the bottom, Needs you, Away, Machine, Tips (if Noticed becomes a rail widget it takes this slot). Alone, it compacts to the slot nearest the pill. On screens under 1016 px it is only ever a strip. It lives on the desk screen; a button fills the pill on the screen you clicked. It appears only when setup is ready, no setup, persona or recovery-key card is up, and the ready, welcome or hello line has faded; it never first appears during a turn. It folds under windows like every card, is gone under a full-screen window and when agentd drops, and does not react to the AI resting (it re-sorts only at the next boot or after a tip is tried). Like every card it narrows the pill: to 710 px on a 1366 px screen and 624 px on 1280, for the first week.

## The Tips app

A built-in kit app, `share/apps/tips`: title "Tips", icon "sparkles", description "What to ask, shown in action". An opaque window of 960 × 516, which fits a 1280 × 720 screen even with an app chip in the bar. It slides in from its drawer; Esc hides it.

**It opens from** the words "tips", "open tips", "show tips", "what can you do", "what can I ask" and "help"; from the card's title, picture and "All tips ›", each on that tip; and from the "tips" strip.

**Left: the stage.** 576 × 324, where the tip plays at app density: the 24 px stone in its real colours, a 52 px pill, 16 px text and a 15 px line, the real screen in close-up. The tip's three phase captions sit in a row just under the stage, 12 px, the current one in fg ("You ask · It works · You have it" unless the tip says otherwise), so they never cover the mini pill. Under them, the one line in 17/600, then the button with the exact words (36 high, neutral, never orange) and Replay beside it.

**Right: the list.** A sectioned list under Start here, Work, Code, This machine, and New (only after an update brings tips). Each row shows the line and the words in muted text, and a tick with "tried" once tried.

**Behaviour.** Selecting a tip plays it once and holds the payoff. Replay or Space plays it again; ↑ ↓ select; Enter presses the button; Esc hides. The button hides the app first, then fills the pill, so the real thing happens on a clear screen. It never auto-advances, never loops and has no sound. Under reduced motion each tip shows three stills side by side with their captions. While the AI rests the list does not change: model tips still fill the pill, and Enter queues the ask as the waiting chip.

**Not in it:** paragraphs, search (until there are about 40 tips), progress counts or streaks, settings, and "coming soon" rows.

**Data.** `app.py` reads `share/tips` and watches `tips.json`, and talks to agentd only in one-shot requests (an open socket would count as a bar for the machine sampler).

## How a tip moves

**Storyboards, not video.** `Kit.TipScene` draws each tip from a short list of beats with `Kit.Stone` and mini faces redrawn from the theme tokens, so a tip is always in the current look, crisp at any size, and a few lines of text to add. Recorded clips were the alternative: true by construction, but heavy, blurry at 300 px, stale whenever the look changes, and they need a video player in the shell.

**Nine beats:**

| Beat | What it draws |
|---|---|
| `key` | a keycap pressed: ⊞, ↵, Esc, Tab or ! |
| `type` | words typing into the mini pill at 25 characters a second |
| `stone` | the stone's face: rest, listening, working, needs you, done, stopped, resting |
| `line` | the line above the pill: text, tone, amber or red edge, the mono command, the seconds counter |
| `window` | an app window sliding in from its drawer, its rows, or a change to them |
| `card` | a desk card: Watching, Now or Machine |
| `picture` | a picture card above the line, boxes streaming in |
| `fold` | the desk's cards folding to strips, or back |
| `hold` | a pause |

**Timing.** 6 to 8 s, never over 10. Keys take 300 to 400 ms; slides 300 ms on the system's curve (0.16, 1, 0.3, 1); fades 200 ms; the stone uses its own roll and hop. No bounce, no loop. Every scene ends on its payoff frame and holds it; that frame is the reduced-motion still and, drawn at card density, the card's poster. Three phase captions per tip, at most three words each.

**The mini machine moves as the real one.** The stone rolls a third of a turn while the mini machine works, hops once when it is done, knocks when something waits for you, so watching a tip also teaches what the real stone means. On the app stage the mini machine has its real colours (orange only where it acts); on the desk card the stone is drawn in ink.

**Truth.** Every string a scene shows is tagged: *template* (matched whole, placeholders filled, against the real sentences in narrate, launcher, desk, jobs, vitals, rest and agentd, so "Building Tickets, 140 lines" fails the test the day that line changes), *typed* (the tip's own words), *model* (a reply in the agent's register, at most four plain lines), *output* (a command's example output, in mono) or *caption*. Every poster renders offscreen in the tests.

**Cost.** Every animation runs only while the scene is playing and reduced motion is off. At rest the card has no timer and no running animation. Before shipping, measure the shell's CPU with `pidstat` over 60 s at rest with and without the card, and frame counts with `QSG_RENDER_TIMING`, on the VM's software renderer and on real hardware.

## Trying a tip

1. **A press** on the card's button or the app's puts the exact words in the pill through agentd's existing `summon` message (`agentd.py`, `shell.qml` `summon(text)`), which already takes the keyboard on the focused screen with the cursor at the end; the stone leans.
2. **Nothing is sent** (2a). Enter sends it as your own ask with its restore point; Esc drops it; you can edit it first. A button ending in "…" fills the words before the ellipsis and leaves the cursor for your detail ("In Tickets, add…").
3. **Your own words are never replaced.** A tip's summon carries `tip: true`, and the pill keeps unsent words you typed (the Brain's "Ask about this" still replaces them, as today).
4. **While the AI rests,** model tips still fill the pill and Enter queues them as the waiting chip; tips that need no AI ("How am I connected?", "!uname -r") run at once, offline.
5. **Tried.** agentd marks a tip tried from its live events with no model: when the words sent equal the words the tip summoned, or when the turn's evidence matches the tip's `tried_when` (a local action such as the network picture, the Machine question, a `!` command, a stop, undo or desk; `create_app` among the tools; a job start). A tip is never tried just because it was shown. Once the self-improvement ledger lands, an unedited tip ask is logged with origin "button", so Noticed never counts it; an edited one counts as typed.
6. **After the turn,** 12 s after it ends, the card crossfades to the next untried tip and the app ticks the row.

## The first boot, minute by minute

On the live USB on main plus this design. S is the moment sign-in completes.

- **0:00** The ground colour. The wallpaper fades in once. The stone rolls orange beside "Starting".
- **About 0:03** "Which AI should run this computer?" with the Claude and Codex buttons; the stone knocks amber. No Tips card: setup owns the screen.
- **Sign-in** (30 to 90 s): the browser panel slides in and back out.
- **S** "Signed in to Claude. Ask me for anything." with a still green stone. When the voice branch is merged, the name card and the first hello come here; on the live USB, the Install button and its undo note come first once they are built. Tips waits for all of them.
- **S + 8 s** The line fades. The faint first-day line sits just above the pill until the first Super or Esc. The Tips card arrives with the desk's own motion: "Tips", "Start here · 1 of 6", the poster of a Tickets window over a mini pill, "Say what you need. It gets made.", [Make me a ticketing app] and "All tips ›". On a 1366 px screen the pill eases from 900 to 710 px. From here nothing moves.
- **A click on the picture** slides the Tips app in on this tip, and its scene plays once, about 7 s: the ⊞ key, the words typing in, ↵, the stone rolling orange, "Building Tickets, 140 lines", the window sliding in, the closing reply with Undo and Details, the hop. Then it holds.
- **A press on the button** fills the real pill. Enter runs the real thing at real speed.
- **After the turn:** Tickets probably covers the right rail, so the card folds to its "tips" strip. 12 s after the turn ends the card turns to tip 2, "Ask for a picture, not a paragraph." [How am I connected?], which answers offline in under a second.
- **If you typed during sign-in,** the card first appears 12 s after that turn ends.
- **The installed first boot** is already signed in. When the voice branch is merged it says "Welcome home. Undo works from here." and the recovery key card follows; the Tips card comes after both, on the first tip not tried on the stick, and the undo tip now shows in the app.

## The seed

Twenty tips, all backed by main today, each with its gate. Lines are proposals for the voice thread, in the teaching register: one line, no name, no "!", no em-dash, no "will", "tomorrow" or "later". `{app}`, `{project}` and `{ai}` are filled from the machine: the newest app in `~/Apps`, the newest folder in `~/Projects`, the AI in use.

| # | Section | Line | Button (exact words) | Needs | What the scene shows |
|---|---|---|---|---|---|
| 1 | Start here | Say what you need. It gets made. | Make me a ticketing app | the AI | ⊞, the words, ↵; "On it", the stone rolls; "Building Tickets, 140 lines"; Tickets slides in; the closing reply with Undo and Details; the hop |
| 2 | Start here | Ask for a picture, not a paragraph. | How am I connected? | nothing | the words; "Drawing how you're connected" with no AI turn; a picture card of this computer out to the internet; "Showing how you're connected." |
| 3 | Start here | Esc stops anything it is doing. | (the Esc key) | the AI | "install docker and set it up" runs with an amber edge and its command; Esc; "Stopping"; the grey square; "Stopped while installing docker." |
| 4 | Start here | Set a timer. It counts on the desk. | Set a 25 minute timer | the AI | the words; a Watching card rises, "Timer, 25 min" counting; a window slides over and the card folds to "1 counting" |
| 5 | Start here | Ask how the machine is doing. | How's the machine? | nothing | the words; "Here is the machine." at once; the Machine card rises with its meters |
| 6 | Start here | Run a command without the AI. | !uname -r | nothing | ! and the command; it runs with no AI turn; the output on the line in mono |
| 7 | Work | Change an app by saying what to add. | In {app}, add… | the AI, an app | Tickets open; "In Tickets, add a due date column"; "Changing Tickets, 180 lines"; the window reloads in place with a Due column |
| 8 | Work | Type an app's name to open it. | {app} | an app | "tic" and the faint ghost "kets"; Tab, ↵; "Opened Tickets." at once; it slides in |
| 9 | Work | A password manager that locks itself. | Make me a password manager | the AI | "Building Passwords, 210 lines"; the window on its lock screen; later, idle, locked again |
| 10 | Work | Open a file, then ask where it came from. | (words: where did this come from?) | a file in front | a PDF in front; the answer from the Brain at once; "brain" opens the Brain on it |
| 11 | Work | Ask it to draw how something works. | How does a VPN work? Draw it | the AI; app only | boxes stream in: laptop, tunnel, VPN server, website; one sentence under it |
| 12 | Code | Install a toolchain, watch each step. | Install rust and set it up | the AI | a Now card with the route; "Installing rustup" with the amber edge and command; steps tick; Undo |
| 13 | Code | Run tests in the background, keep going. | Run the tests in ~/Projects/{project} in the background | the AI, a project; app only | a Watching card "{Project} tests" counting while the stone goes green; later "is done in 3 min." |
| 14 | Code | Ask it to draw how your code fits. | Draw how the modules in ~/Projects/{project} depend on each other | the AI, a project; app only | a layers picture streams in, one box lit; a click opens that file |
| 15 | This machine | Ask where your disk space went. | Where did my disk go? | nothing | "Drawing your disks"; each disk and how full, the fullest amber |
| 16 | This machine | Out of {ai}? Your apps still work. | pause {ai} | nothing | the hollow grey stone and "Claude is paused…"; an ask waits as a grey chip; "resume claude"; "Claude is back. Running your waiting ask." |
| 17 | This machine | Say desk to fold the cards, desk to unfold. | desk | a card other than Tips seen | Now and Watching fold to "step 2 of 2" and "1 counting"; "Folded the desk."; again, "Unfolded the desk." |
| 18 | This machine | Keep a widget on your desk. | widgets (3b) or Keep machine on the desk (3a) | piece 2 | the Widgets card, Keep on Machine; a restart; the Machine card is there after boot |
| 19 | This machine | Type why while it works. | (words: why) | the AI, mid-turn | "Installing docker" running; "why"; the reason flashes in the agent's words; back to the step |
| 20 | This machine | Say undo to go back a step. | (words: undo) | an installed disk | "install htop" closed with Undo; "undo"; "Undone. System files go back to before “install htop” when you restart. Your home folder and apps stay as they are." |

**Waiting for their pieces:** "claude {project}" (a coding session by name) and the amber stone's your-turn knock come with the coding-sessions branch (PR #4); "What needs me in mail?" with the mail work; "noticed" with the self-improvement branch. Their rows ship with those PRs, gated until the code is on main.

**A row in `share/tips/tips.toml`:**

```toml
[[tip]]
id = "make-an-app"
section = "start"            # start, work, code, machine
order = 1
line = "Say what you need. It gets made."
words = "Make me a ticketing app"
needs = ["model"]            # model, installed, app, project, sessions, mail, noticed, keep, desk-seen
tried_when = ["tool:create_app", "words"]
captions = ["You ask", "It builds", "You have it"]
beats = [
  { key = "super" },
  { type = "Make me a ticketing app" },
  { key = "enter" },
  { stone = "working", line = "On it", counter = true },
  { line = "Building Tickets, 140 lines", tag = "template" },
  { window = "Tickets", rows = ["Printer jams on floor 3", "VPN drops every hour", "Order a new laptop"] },
  { line = "Made Tickets. It is open now.", tag = "model", buttons = ["Undo", "Details"], stone = "done" },
]
```

## How tips grow

- **Tips are rows, not code.** The voice thread words them; `tips.toml` moves beside `share/voice/lines.toml` once the voice PR merges.
- **A PR that adds something a person can ask ships its tip row and beats in the same PR**, the way it ships its docs, gated by a `needs` flag until its code is on main. `docs/CONTRIBUTING` gains that one line.
- **The tests fail a tip** when its `needs` flag is unknown, its line breaks the rules or overflows 272 px at 13 px, its button and "All tips ›" overflow the card row while the card can show it, its words reach nothing (a launcher word, a model ask or a `!` command), its scene runs over 10 s, its poster fails to render, or a template string is not backed by the code.
- **A new kind of beat** is a small piece of `TipScene`: session dots and the knock when the coding sessions land, the Mail window when Mail lands.
- **Order is fixed, never random:** Start here, then the sections in order, untried first.
- **After an update,** tips with unknown ids appear in the app's New section only: no strip, no card, nothing unasked. Retired ids are never reused.
- **Later:** tips per app from the apps' `[[does]]` rows; search past about 40 tips; a section split past about nine; the player becomes "Watch again" for real turns.

## Decisions

Each forks; a default is picked and argued, with the other options for the record.

- **Is anything on the desk at rest after boot?** Default: nothing, as confirmed, except Tips in a new user's first week and whatever the user keeps. Other options: Tips always until put away; Machine kept by default; nothing ever.
- **Where does the Tips card sit?** Default: the right rail's top slot, the least urgent, which compacts next to the pill on a first boot. The right rail is yours, and a tip is for you. Other options: the left rail (the machine's); above the pill (the conversation's column, never a widget's).
- **Does the card animate?** Default: no; it is a still poster that turns on its arrows or after a tried tip, and the app plays. Motion on this desk means the machine is working. Other options: play on hover; play once on arrival; rotate every few seconds.
- **What is on the card?** Default: title, why line, picture, one line, the button, "All tips ›". Other options: the line alone; page dots (dots mean who on this desk).
- **How are scenes made?** Default: storyboards drawn live by a kit component in the current look. Other options: clips recorded from the headless desktop test; still pictures only.
- **Where do scenes play?** Default: only in the Tips app, once, holding the last frame. Other options: in the card on hover; looping in the app.
- **What does the button do?** Default: fills the pill and waits (question 2). Other options: runs at once; runs only the quick ones.
- **How long does Tips stay?** Default: the first week of use, or six Start-here tips tried, or until closed. Other options: until every tip is tried; until closed.
- **What counts as tried?** Default: agentd's live events, with no model: the summoned words sent, or the tip's evidence (a local action, a tool, a job). Other options: a click; reading the turn log after the fact.
- **Which tips show?** Default: only those this machine can back now; the rest are not listed. Other options: greyed with "works once…" (the voice brief hides what the machine cannot back).
- **How do users set defaults?** Default: Keep, When needed, Off per widget, by words and the Widgets card (question 3). Other options: words only; a Settings app.
- **What can be kept?** Default: only widgets with a calm face, Tips and Machine today. Other options: every widget, with an empty face when idle.
- **What do new tips after an update do?** Default: wait in the app's New section, with nothing on the desk. Other options: a "2 new" strip for a few days.
- **What opens the app?** Default: "tips", "what can you do", "what can I ask", "help", the card and its strip. Other options: "tips" only.
- **Does Tips replace the unbuilt first-minute pieces?** Default: yes, the example buttons and the tour; the faint line, the one-time undo line and the Install button stay (question 4).

## Changes to confirmed decisions

Each is named so the owner's answers confirm it knowingly.

- **The rest state** (widgets rule 4 and "Are widgets always on? Nothing at rest"; identity rule 5; voice rule 1 and its cut of a standing welcome): Tips stands at rest during a new user's first seven days of use, then leaves by itself. A widget the user keeps stands at rest, only because they asked. A kept Machine's rounded figures change in place without motion.
- **The voice brief's cut of "tips of the day"** and "nothing appears mid-day": Tips never speaks on the line, never rotates, and first appears only after an arrival. It is still a teaching surface shown unasked in the first week, which is what the owner asked for.
- **Self-improvement rule 2 and strip-first** ("0 cards raised on their own"): Tips raises a full card by itself in the first week. A new person's first look is not a mid-task interruption, which is what the dismissal research measured. Question 1b keeps strip-first if the owner prefers it.
- **Desk rule 1** gains a ninth question, "What can I ask it to do?", as Noticed added the eighth.
- **The Noticed slot:** Tips takes the right rail's top slot until Noticed becomes a rail widget.
- **"Nothing else moves"** (identity rule 7, design system): scenes move only inside the Tips app the user opened. They play once, hold their last frame, never loop, and have a still version. The desk card never animates.
- **Colour and the stone** (design system "colour says who", identity rule 2): the app stage shows the mini machine in its real colours, because it teaches what they mean. The card draws the stone in ink.
- **"What skips the model is a small exact list"** and **"no new words for the desk"**: this adds the local words "tips", "what can you do", "what can I ask", "help" and "widgets", and the verb "keep".
- **"keep"** gets one meaning, "stays on the desk at rest". When "keep this on the desk" for cards and apps is built (widgets brief), a card or app first becomes a widget, then is kept.
- **Voice rule 5** (a chip is a sentence you could have typed, and sends it): every tip button fills the pill and waits (question 2); the empty-place chips follow the same rule, whichever is chosen.
- **The UX brief's first minute:** Tips replaces the unbuilt example chips and the ten-second tour (question 4). The habit chips after about 20 asks are left to Noticed. "Super brings the chips back" loses its chips. The faint first-day line, the one-time "Changed your mind? Say undo." and the live-USB Install chip stay.
- **"Who draws widgets"** and widgets rule 5: Tips adds a face drawn by an internal kit component from typed data, never agent QML. The stone moves into the kit. The poster's line buttons are unlabelled outlines.
- **"Are widgets always on? Other options: a switch per widget"** (turned down on 28 Sep): the Widgets card brings a per-widget choice back, because the owner has now asked for exactly that; the default it ships with is the quiet desk.

## Cut, and why

- **A carousel that turns by itself.** On this desk motion means the machine is working; a rotating card would be the one thing moving at rest, and a cost no one asked for.
- **Animation on the desk card,** even on hover. A poster says enough at a glance; the click that plays it also opens the place to see more.
- **Page dots.** Dots mean who on the desk.
- **A "2 new tips" strip after updates.** Nothing unasked after the first week; new tips wait in the app.
- **Greyed "works once…" tips.** A tip the machine cannot back is not shown, as the voice brief decided for chips.
- **Paragraphs, step lists and "learn more" pages** in the card or the app. One line per tip.
- **Progress, streaks and "3 of 20 done" counts.** The voice brief cut streaks; the tick on a tried row is enough.
- **Tips spoken on the line, a mark on the stone, a sound.** Tips never speaks.
- **The agent writing tips, or Tips offering to build words or apps from repeats.** That is Noticed's job.
- **Random order.** The same desk teaches the same thing; untried first, in a fixed order.
- **Real figures in posters.** Pictures of the machine are generic shapes, so a poster never runs the machine's pictures at rest.

## Risks, and what to check before building

- **Three confirmed cuts are reversed** (the rest state, tips of the day, strip-first). The fallback is question 1c, the app alone, which still needs the new words, the button rule and motion inside an app.
- **The first tip is the slowest ask:** a build takes about a minute, and a weak first app is the worst first impression. Tip 2 answers in a second. Test tip 1 on both providers; the counter may not tick on Codex.
- **Focus handover:** a press on the Bottom-layer rail window must hand the keyboard to the Overlay pill through `summon`. Check on one and two monitors.
- **The pill narrows** to 710 px at 1366 and 624 px at 1280 for the first week.
- **Scene drift:** the truth test catches changed wording, not changed window behaviour; model replies are only plausible.
- **Tried detection misses paraphrases** until the self-improvement ledger lands.
- **Unmerged neighbours:** the voice branch's name card, hello and welcome-home line come first, and its line rules are duplicated in the tips test until it merges; Tab and Enter on the empty pill stay reserved for the coding sessions' walk; question 1b needs the desk thread's per-widget strip in the shell.
- **Measure:** the render cost of a still, mapped rail window on the VM's software renderer and on real hardware; the kept Machine's cadence; the Tips app at 960 × 516 on 1280 × 720 with an app chip showing.
- **On the live USB** the card shows on every boot, and apps made there vanish at restart, so tips that need an app re-gate; tip 12's download must fit the live USB's writable space.

## Who owns what

- **The desk thread** owns the desk's code: the Tips card's face and widget entry (piece 1, step 5, or with the Tips build by agreement) and all of piece 2.
- **The voice thread** owns the wording: every tip line and the new local answers are proposals until it words them; `tips.toml` moves beside `lines.toml`.
- **The app kit** is how the Tips app is made (`share/apps/tips`, built-in apps under `share/apps/<name>/`); `Kit.Stone` and `Kit.TipScene` are internal kit components.
- **A Tips build thread** (started on the owner's answer) builds piece 1 steps 1 to 4.
- **Every later piece** that adds something a person can ask ships its tip row.

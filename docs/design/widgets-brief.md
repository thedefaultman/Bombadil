# The desk: widgets for a passenger

You type "install docker and set it up for my projects" and press Enter. The line above the pill says "On it", and a small card rises in the bottom-left corner with your words as its title and the route under them: four steps, the first ticked, the second pulsing orange with the exact command in amber, the last two grey. Beside it, Watching counts down a download and a timer. In the bottom-right corner, Needs you lists the two coding sessions waiting for you, one of them asking for root, and one tap gives it; above it, Machine says builder is near its memory limit, in blue. Then you open the browser. It slides in over the rails, and every card folds into a chip beside the pill, "step 2 of 4", "ISO 43%", "2 need you", so nothing is lost and nothing is in the way. Close the browser and the cards come back where they were. Nothing pops, nothing asks, and there is one word and one gesture to learn: "desk" folds everything to chips and back, and a drag by the title moves a card. The chips beside the pill are the ones the earlier briefs already decided, grown a full face for when there is room.

This is the widget design for Bombadil's desktop. It sits on top of How Bombadil should feel, Building on Bombadil and Bombadil's Brain (all 27 Sep 2026), whose 29, 19 and 17 decisions stay in force. Daniel asked for widgets that are modular and that move, hide and make room for the apps, popups and other visuals the AI brings, on a machine where the AI drives and he rides along and gets things explained visually. The desk is how widgets live with the pill and the wallpaper; it does not re-argue the rest state, and a checker went through every decision in the three briefs before this reached him (the "Cut" list says what it took out). Daniel accepted the design and all fifteen defaults on 28 Sep 2026, so they are confirmed decisions, and the build of pieces 1 and 2 starts from here.

## The rules

1. **Every widget answers one question a passenger asks.** What is it doing, and why? What is it keeping an eye on? What is waiting for me? Who is working? What happened while I was gone? Is the machine in trouble? And, only when asked for, what is being touched right now? A widget that answers none of these is not built.
2. **The middle belongs to the answer; the margins belong to the passenger.** Windows, panels and cards land where the earlier briefs put them, and widgets live in two rails at the sides and in the pill's row. A widget never sits under anything: whatever the AI brings has right of way, and the widget folds toward its edge and comes back when the thing leaves.
3. **Three faces, and the small ones already exist.** Every widget has a full face (a card in a rail), a strip (a chip beside the pill: the session dots, the job chips and the your-turn line the earlier briefs decided) and a dot (a mark on the pill). The desk always shows the largest face that fits.
4. **Present only with something to say.** No plan, no Now; nothing counting, no Watching; one session waiting is the line, not a card. A machine at rest is wallpaper and a pill, exactly as decided, and there is no vitals strip. The desk fills exactly as the machine gets busy, which is itself the explanation.
5. **Widgets show what the line cannot.** The line above the pill stays the one voice: the verb, the seconds, the closing sentence and Undo live there and nowhere else. A widget adds the route, the list, the meter, the who.
6. **Colour says who.** White for you, orange for the machine's turn, blue for coding sessions, the brain's three colours, on every meter, dot and strip, so a glance says who is using the memory or who touched the file.
7. **No click starts work you did not ask for, and nothing pops.** Do it and Open send your answer on as a turn with its restore point or bring a session forward; Undo, Stop and Why? never touch the model. No widget takes the keyboard, no widget interrupts, and the agent rearranges the desk only when you ask. Built-in widgets are shell QML; a widget the agent makes publishes typed JSON that the shell draws with the same faces, the rule the answer cards already follow.

## The widgets

Seven, each in the kit's look (Theme.qml tokens: panel #1a1d21 at 96%, 12px corners, Inter 14, 44px rows), 300px wide in a rail, with a title, one line of why, and rows.

### 1. Now: what is it doing, and why?

**What you see:** When a turn states a plan of two or more steps, or touches the system, a card rises in the bottom-left rail with your ask as its title, in your words, and the route under it: a tick for each done step, the current one pulsing orange with the step's own words, the next ones grey. A step that touches the system gets the amber edge and the exact command under it (the marks the line uses); one no restore point can undo gets the red edge and, while it runs, the words "can't be undone · Esc stops it" under the command, so the colour is explained at the one moment it matters. The line of why counts what the turn has touched so far ("Step 2 of 4 · 1 package so far · Esc stops"). A turn a coding session asked for is titled "install qemu-full · asked by builder". When the turn ends every step ticks, the why line says "done in 58 s · Undo is above the pill", and the card folds to its strip and leaves with the line. Nothing the line already says is repeated here: no verb, no seconds, no closing sentence, no Undo.

**Why:** With no permission prompts, watching the work is the safety model, and a passenger wants the route, not only the current street. The line stays one line; the route is where the plan goes.

**Feeds:** agentd keeps a table of the turn's steps from the CLI's own task tools, which both providers have: Claude Code's TaskCreate and TaskUpdate calls in stream-json (TodoWrite on older versions), one task per call, so create adds a grey step with its subject, in_progress makes it the current one, completed ticks it; Codex's update_plan arrives as a todo list whose items carry only done or not done, so its current step is the first unfinished one. agentd broadcasts the whole table as each `plan` event, stamped with the turn like status events, and DeskState drops a plan from another turn the way PillState drops a stale status. turn_start gains `asked_by` for session-asked turns; status events carry risk and command as today. **Kit:** Panel, ListRow, Badge, Mono, the StatusLine's edge colours. **Strip:** "step 2 of 4" with the orange dot. **Dot:** the pill's own orange dot.

### 2. Watching: what is it keeping an eye on?

**What you see:** One row per thing that is counting right now: a Meter for a job with progress ("Ubuntu 26.04 ISO · 43% · 12 MB/s · 4 min"), a countdown for a timer, a plain row for a one-shot watcher on something running ("Tell me when the build finishes · 6 min so far"), each with a small × that stops it. A finished job turns green and stays 12 seconds; a failed one turns red with its last line of output under its name ("pacman: could not resolve host") and "Why?", which opens the rest in the terminal panel. A card that is still counting when a newer card replaces it (a timer, a download) moves in here instead of dying, so "timer 10 minutes" survives your next question. Standing watchers, the morning "tell me which updates matter" kind, are not rows: they run once and speak through the pill, and "what are you watching?" lists them with their stop buttons, as decided. Appears with the first thing counting; leaves when nothing is.

**Why:** Jobs and watchers are how the machine works in the background without taking the screen, and the passenger should see them counting without asking.

**Feeds:** a jobs registry in agentd (each `systemd-run --user` job from a turn writes a job file with name, unit, started, progress and result; timers are the timer cards; one-shot watchers are the systemd user timers agentd makes with a target that ends). **Kit:** Panel, ListRow, Meter, IconButton, Badge. **Strip:** the background-job chips the first brief decided ("ISO 43%"). **Dot:** none; with no room in the row, a job is a mark on the pill's dot.

### 3. Needs you: what is waiting for me?

**What you see:** The coding sessions' asks, one row each with the row's own button: a session whose turn it is ("reviewer on Bombadil: apply the migration? [Open]") and a session asking the machine for something it may not do itself ("builder asks: install qemu-full [Do it]"). The rows are the second brief's Tab walk, in its order, shown as rows; Tab from the empty pill still walks them and Enter on an empty pill still does nothing. One session waiting is the your-turn line in the pill, as decided, and no card; the card rises when two or more wait, and leaves when the walk is empty. The agent's own rare question, a notification you have not seen and a provider's limit or sign-out stay where the first brief put them, in the pill, and never move to the desk. Needs you cannot be hidden; it is silent when nothing waits.

**Why:** Daniel runs several sessions at once, and the moment two of them want him is the moment one list beats one line.

**Feeds:** dev.py's your-turn and request events from the managed hooks (second brief, pieces 2 and 3). **Kit:** Panel, ListRow, Badge, Button. **Strip:** the your-turn line in the pill ("2 need you", with the white mark). **Dot:** the mark on the pill's dot the first brief decided.

### 4. Sessions: who is working?

**What you see:** The project chips beside the pill with a dot per session (moving while it works, lit when it is your turn, red when it failed, dim when asleep) are the widget: hovering a dot peeks its session, as decided. Its full face is the "what's running?" card, one row per session grouped by project with name, state, last plain line, files changed, PR state and the check's result; it opens on asking or on a click on the chip and leaves like any card. It never stands in a rail: a permanent sessions sidebar is the IDE the second brief refused, and the pill is the one list. After a reboot the dots come back dim.

**Why:** the dots are already the answer to "who is working?"; the card is there for the moment you want the detail, and gone the moment you do not.

**Feeds:** the dev registry (~/.local/state/bombadil/dev/sessions.json), the hooks, checkpoint refs and the check results. **Kit:** Panel, ListRow, Badge, Caption. **Strip:** the project chips, folded into one chip above three projects, exactly as decided. **Dot:** the dots inside the chip.

### 5. Away: what happened while I was gone?

**What you see:** When you come back after a break, the "while you were away" group the first two briefs decided, as a card in the right rail instead of only a line: what finished, who is waiting, what failed, and what changed with Undo just this for turns older than the one on the line. Click a row and the Brain opens in Time at that moment. It leaves once you have seen it: a click, or your next ask.

**Why:** the welcome-back moment is the one time a passenger reads a list, and the list already exists; the card is the same text with room for three rows and their buttons.

**Feeds:** turns.jsonl, the snapper pairs, the dev registry and the jobs registry, grouped locally as the first brief decided; the brain's timeline when it lands. **Kit:** Panel, ListRow, LineButton. **Strip:** "back · 3 things". **Dot:** none.

### 6. Machine: is the machine in trouble?

**What you see:** Nothing, most of the day. When something crosses a line the briefs already speak about, memory near the sessions' limit, the disk nearly full, the processor hot, a card rises with one word of why ("builder is near its memory limit") and the meters stacked by who is using them (orange the machine's turn, blue coding sessions, white you and your apps), plus the network rate. Clicking the disk meter opens the Map in size mode ("what's eating my disk"). "How's the machine?" opens the same face on request. It leaves when everything is back under its line. Battery, brightness and volume stay in the pill and the status card off the clock, as decided.

**Why:** a passenger does not read gauges; the one thing Bombadil can add to a vitals meter is who, and the one time to show it is when a meter says something is wrong.

**Feeds:** a 1 Hz sampler in agentd reading /proc/stat, /proc/meminfo, the cgroup files of bombadil-turn-* scopes, bombadil-dev.slice and the app units (cpu.stat, memory.current), thermal zones and NetworkManager. **Kit:** Meter, StackedBar, Sparkline, Stat. **Strip:** the line that was crossed ("memory 91%", "hot · 82°"). **Dot:** none.

### 7. Alive: what is being touched right now? (opt in)

**What you see:** A small corner of the Brain's Map: the twenty or so most alive things, each a dot that pulses in the actor's colour when it is touched, with the current turn's files lit and named. It is on the desk only after "show alive", and "hide alive" puts it away; while on, it is present while anything pulsed in the last minute. Click a dot: Focus on it. Comes after brain piece 1, when there is something to draw.

**Why:** It is the brain's ambient face and the one widget that shows the machine and the sessions at work on files while it happens; opt in, because the Map is one word away and the desk at rest stays quiet.

**Feeds:** bombadil-brain's subscribe stream. **Kit:** the Map's scene item. **Strip:** "3 alive · builder, you". **Dot:** none.

### Widgets the agent makes

"Show my batch on the desk" or "make a widget that shows my Tracker streak" makes an eighth widget. It is an app in ~/Apps with a `[widget]` section in app.toml (title, rail, face kinds), and its app.py publishes its state through a `Widget.set({...})` call in the same typed face kinds the built-in widgets use (steps, rows, meters, timeline, stat, text); the runtime forwards it to agentd and the shell draws it. It gets hot reload, persisted state and the per-app git undo like any app, and "delete Batch" removes it. A pin still turns a card into an app, as decided; "keep this on the desk" makes that app also a widget, so the card's face lives on in a rail. A widget whose app stops publishing is not a stuck desk: after two missed updates its face dims and says "stale · 3 min", and nothing an app publishes can block the shell. A free QML face, drawn by the app's own process and placed on the rail through layer-shell, comes later, never inside Quickshell: the answer cards' rule, kept.

## The desk

**Regions.** The **stage** is the middle, where windows, panels and cards land as decided. Two **rails**, 300px wide, run down the sides from 40px below the top edge to the pill's row; full faces stack in them from the bottom up, nearest the pill first. The **row** is the pill's own row, laid out as left strips, the pill, right strips: the pill takes the stage's width less the strips, never below the decided 360px, strips elide at a fixed width the way the chips do today, and strips to the left of the pill belong to the left rail's widgets, strips to the right to the right rail's. The **column** above the pill (the status line, cards, the shelf) is the conversation's; no widget ever uses it, and when a rail's bottom slot is open the column's width is capped to the stage (900px at most, as today).

**Which rail.** The left rail is the machine's: Now, Watching, Alive, bottom up. The right rail is yours: Needs you, Away, Machine, bottom up. Widgets the agent makes go to the rail you name, else the left one above Now. Because a widget with nothing to say is absent, the stacks compact: with no turn running, Watching sits at the bottom left.

**What fits.** Each rail holds what fits. From the bottom up, a widget takes its full face if its full height fits in the room left; it and everything above it fold to strips otherwise. On a 1920x1080 screen everything fits; on a 1280x720 laptop two per rail fit and a third is a strip. Three strips per side at most; beyond that they merge into one counting strip ("+2", the second brief's own fold above three projects).

**Right of way.** Whatever the AI brings covers the rails freely; nothing reserves space, so windows land exactly where the first brief put them. The shell knows every window's rectangle and every panel's, and its own cards and shelf. A widget whose slot is covered, even partly, folds within 150 ms toward its edge into its strip beside the pill, and unfolds 400 ms after the cover leaves, so a window being dragged past does not flicker the rail. It folds widget by widget: a window over the top of a rail leaves the widget by the pill alone. A centred app window on the stage touches no rail, so it changes nothing; the browser, a large session terminal, a reader or a wide app folds whatever it covers; a card in the column folds nothing.

**Modes.** The desk follows the pill's three decided modes. Open desk, nothing in front: full faces. Shared desk, an app or panel in front: the pill narrows to 360px as decided, covered widgets fold, the rest stay; the compositor already dims what lies under a special workspace, so the app is the thing in front and the desk adds no dim of its own. Immersive, full-screen video or a game: the capsule pill only, the desk hidden, the marks on the dot the only trace. "desk" is the one word of its own: it folds every full face to its strip and back, so a cleared desk still has everything within a hover.

**Peek and open.** Hovering a strip raises its full face above the row for as long as you hover, without unfolding the rail; clicking a strip keeps that face up until you click elsewhere, and it never takes the keyboard from what you were doing (Esc closes it only while the pill has the keyboard). Clicking a full face's title opens the thing behind it: Now opens the turn's details in the terminal panel, Away opens Time, Machine opens the Map in size mode.

**Move, hide, show.** Drag a full face by its title to the other rail, to another rank, or into the row to fold it by hand; drag a strip into a rail to give it a slot. The words are the launcher's own: widget names join the name list after apps, so "hide machine" and "show alive" are the verbs that already put a window away and bring one up, on a new noun; "desk" folds everything to strips and back. A sentence ("put watching on the right") is for the agent, which does it with the `desk` tool. Positions and hidden widgets persist in ~/.local/state/bombadil/desk.toml, beside the dev registry and outside the restore points as that directory already is, so an undo never moves your desk; each change is logged as a local action so the agent knows the desk.

**The agent and the desk.** os-mcp gains one tool, `desk`, with show, hide, move, make and remove. It works only in a turn whose words asked for the desk (show, keep, pin, hide, put, make a widget) and refuses otherwise, and the line says what it did ("Put Batch on the desk. It follows the session and leaves when it ends."). A widget that appears unasked would be a popup by another name.

**Drawn by whom.** Built-in widgets are shell QML in two new Quickshell PanelWindows, one per rail, on the Bottom layer (above the wallpaper, below every window), with `exclusionMode: Normal` and `exclusiveZone: 0` so they reserve nothing and still stop above the bar's zone, and an input mask so clicks beside the faces reach the wallpaper. Coverage comes from Quickshell's Hyprland toplevel list and events (open, close, move, special workspace shown or hidden, fullscreen) plus the bar's own cards; because Hyprland sends no event while a floating window is dragged, the shell refreshes the client list every 500 ms while any window is on the stage and only listens otherwise. Strips live in the bar window beside the pill, and their rectangles join the bar's input mask. Folding is one animation across the two windows: the face shrinks toward its edge while its strip grows in the row. A drag that crosses from a rail window into the bar, or back, is resolved by DeskState from screen coordinates, since both windows are one process and the shell knows their geometry; the first build ships the words and adds the drag once that is proven in the VM. The shell imports the kit's Theme through QML_IMPORT_PATH instead of copying colour literals, so the bar, the cards, the widgets and the apps share one theme. Motion is small and means something: a fold is 150 ms out and 400 ms back, a new row gets a 600 ms wash of its colour on the card's left edge (never a bounce), the working dot breathes, and with reduced motion all of it is instant. The desk's state (which widget has which face) is one QML object, DeskState, driven by agentd's events the same way PillState is, and tested offscreen the same way.

**Several screens.** The desk lives on the pill's screen, the one Super focuses; other screens show wallpaper until asked ("put the desk on the left screen"). Which screen that is remains the first brief's open question.

## A day at the desk

**09:05.** You sit down. Wallpaper, the pill, a Bombadil chip with three dim dots, and one card: "While you were away: batch finished, 300 done; reviewer is waiting; tidy my downloads, Undo". You click Open on reviewer's row and the card is gone.

**10:30, a long install.** "install docker and set it up for my projects". Now rises with the route, four steps. The second step goes amber with its command; you read it, you do not have to answer it. At 58 seconds every step ticks, the closing line above the pill says "Installed Docker." with Undo, and the card leaves with the line.

**11:15, two sessions want you.** The line says "builder asks: install qemu-full", and reviewer's turn comes at the same moment, so Needs you rises with both rows. You tap Do it. The install is an ordinary turn in Now, titled "install qemu-full · asked by builder", amber, with a restore point, and the row leaves.

**12:00, the browser.** You open the Arch Wiki. The panel slides in over both rails, and four cards become four chips beside the narrow pill. You hover "2 need you" to peek, close the browser, and the cards are back where they were before you notice the fold.

**14:30, a batch.** "show my batch on the desk". A Batch widget rises above Now with a meter, 14 of 20, blue, and the line says why it is there.

**15:10, memory.** builder passes the sessions' memory limit. Machine rises on the right with "builder is near its memory limit" and a meter that is mostly blue; you know what to end, and the card leaves when it is back under the line.

**18:30.** "desk". Every card folds to a chip beside the pill; wallpaper, the pill and a row of chips. "desk" again and they are back.

## Build these first

Two pieces a build thread can take. Both edit shell.qml, so they follow the merges of PR #2 (the kit) and PR #4 (the session dots, which are Sessions' strip).

### 1. The desk, and Now

**What changes:** two rail PanelWindows on the Bottom layer with `exclusionMode: Normal`, `exclusiveZone: 0` and input masks; the row laid out as left strips, the pill, right strips; DeskState.qml (faces, slots, capacity, fold and unfold from Hyprland coverage with the 150/400 ms hysteresis, the three modes, persistence in desk.toml); the fold animation; the 500 ms client refresh while a window is up; widget names in launcher.py's name list and the word "desk"; the `desk` tool in os-mcp, gated on the turn's words; the typed face kinds (steps, rows, meters, timeline, stat, text) as shell components built from the kit's Theme, imported through QML_IMPORT_PATH. Now: agentd keeps the per-turn task table from TaskCreate/TaskUpdate and Codex's todo list and broadcasts it as `plan` events with the turn id, adds `asked_by` to turn_start and the touched counts to its status events; the route face reads plan, status, risk, command and turn_end, and the red step's caption.

**Before building:** check in the VM that a Bottom-layer PanelWindow with `exclusiveZone: 0` draws above the wallpaper and below floating and special-workspace windows on Hyprland 0.56 and reserves nothing (windows still centre in the full stage); that a 500 ms refresh of Quickshell's toplevel rectangles keeps up with a dragged window without costing the shell; how far Hyprland's `dim_special` reaches (if it dims Bottom-layer surfaces, that is the shared-desk look and the shell adds none); and that a press in a rail window reaches DeskState with screen coordinates when it ends over the bar window.

**Effort:** M to L.

### 2. Needs you, and Watching

**What changes:** the rows face for both; Needs you reads dev.py's your-turn and request events and owns the Tab walk the second brief specified, so there is one queue, with the card only above one waiting session; Watching gets the jobs registry in agentd (a job file per `systemd-run --user` unit a turn starts, progress written by the job's own output where it has any, the timer cards, one-shot watchers) and the chips the first brief decided become its strip; a counting card survives replacement by moving here. The Stop × runs `systemctl --user stop` on the unit, never the model.

**Effort:** M.

**Then:** Away (turns.jsonl and the registries now, the brain's timeline when it lands), Machine (the sampler with cgroup attribution and its lines), Sessions' card as the chip's peek (after PR #4), Alive (after brain piece 1), and widgets the agent makes (the `[widget]` section, Widget.set, "keep this on the desk").

## Decisions, confirmed by Daniel on 28 Sep 2026

Each of these forks; I picked a default and say why, with the alternative for the record. Daniel confirmed all fifteen on 28 Sep 2026.

**Where do widgets live?** Default: two rails at the sides, 300px wide, stacked from the bottom. The stage keeps the middle and the column keeps the conversation, so the answer lands where the first brief put it and the eye moves sideways from the line to the route. Other options: a grid of tiles on the wallpaper (fights the stage for the middle); a single dock along the bottom (the row is already that, for strips); a corner of the screen only.

**Do widgets make room, or do windows avoid them?** Default: widgets yield; nothing reserves space. The answer is the thing itself and must land where it lands; a rail with an exclusive zone would shrink every window and push the browser off centre. Other options: rails reserve space and windows centre in what is left; windows avoid rails only when they fit.

**Are widgets always on?** Default: present only with something to say, and nothing at rest, not even a vitals strip. This keeps the confirmed rest state and turns the desk itself into the explanation: a busy machine has a busy desk, a quiet one is wallpaper and a pill. Other options: always on; a strip of vitals at rest; a switch per widget.

**How many sizes?** Default: three faces, full, strip and dot, where the strip and the dot are the chips and marks the first two briefs already decided. One concept, not two. Other options: one size that hides; freely resizable cards.

**What when the rail is short?** Default: what fits gets a full face, the rest fold to strips, and strips beyond three per side merge into one counting chip. Other options: scrolling rails; shrinking every face.

**Which rail for what?** Default: left the machine's (Now, Watching, Alive), right yours (Needs you, Away, Machine), nearest the pill most urgent. Needs you sits by the clock and the dot's mark, Now sits beside the line. Other options: one rail; by widget age.

**Where does the route go?** Default: Now in the left rail, showing only the route, never what the line says. The line stays one line and the column stays the conversation's, as decided; the route is a widget so it folds like one, and it appears only when there is a route to show. Other options: the line grows into the route while a turn runs; a card for every turn.

**How fast does it fold and come back?** Default: fold within 150 ms, unfold 400 ms after the cover leaves, and a partial cover counts, widget by widget. Fast out of the way, calm coming back, no flicker while a window is dragged past, and a window over the top of a rail leaves the widget by the pill alone. Other options: symmetric 200 ms; fold only when fully covered; fold the whole rail when any of it is touched.

**Who draws widgets?** Default: the shell, from typed data, for built-in and agent-made widgets alike; free QML in the widget's own process later. The cards' rule, kept: nothing the agent writes runs inside the one way to talk to the machine. Other options: every widget its own process from the start (a PySide6 process per widget on a Bottom layer needs layer-shell-qt, which the kit does not have yet); widget QML loaded into Quickshell.

**Can the agent rearrange the desk?** Default: only when asked; the `desk` tool refuses in a turn whose words did not ask for the desk. The first brief's agent never starts work on its own, and a widget that appears unasked is a popup by another name. Other options: the agent curates the desk freely; the agent may add a widget when it explains why; never.

**Peek or open?** Default: hover a strip to peek, click to keep the face up until you click away, and never take the keyboard. The second brief's hover-peek on a dot, generalised to every strip. Other options: click to unfold the whole rail; no peek.

**Can everything be hidden?** Default: everything but Needs you, which is the only place two sessions' requests can be answered together and which is silent when empty. Other options: everything; nothing.

**Which colours?** Default: the brain's three, white you, orange the machine, blue sessions, on every meter and dot, and the line's amber and red for system and irreversible steps. Other options: a colour per widget; per resource.

**One screen or all?** Default: the pill's screen only. Parallel sessions are where a second screen matters, and which screen the pill owns is still the first brief's open question; the desk follows that answer. Other options: rails on every screen.

**Does the desk exist on the live USB?** Default: yes, all of it; Away says undo starts after install, as the pill already does. Other option: strips only until installed.

## Cut, and why

- **Today, a card of the day's receipts.** The brain brief folded Rewind into Time so there is one place for what happened; a second history in a rail would disagree with it by afternoon. Away keeps the one moment a list is wanted.
- **A vitals strip at rest ("quiet · 61%").** The rest state is wallpaper and a pill, decided; a number with nothing wrong is a gauge, and the battery is in the pill.
- **A standing Sessions card.** The second brief names a permanent sessions sidebar as the IDE failure mode; the dots are the widget, the card is a peek.
- **A notification centre.** Needs you holds only the sessions' asks; the agent's question, notifications and provider lines stay in the pill as decided.
- **Undo on a widget.** Three Undo buttons for one turn is a puzzle; Undo stays on the line, and only Away offers "Undo just this" for older turns.
- **A clock, weather, calendar or notes widget.** Any desktop has them, none answers a passenger's question about this machine, and the clock is in the pill.
- **A dock or app icons on the wallpaper.** The pill is the launcher, and typing a name is faster than finding an icon.
- **A chat or transcript widget.** The one way to turn the desktop back into a chat window.
- **A token or cost counter.** Internals on screen; the limit line in the pill covers the moment it matters.
- **Resizing widgets by hand, or a grid you arrange freely.** Three faces are the sizes, and positions that drift are positions you cannot learn; slots in a rail hold still, like the Map's areas.
- **New words for the desk.** "widgets", "clear the desk" and "put X on the right" would be five forms to learn; "desk" and the launcher's own show and hide are enough, and a sentence goes to the agent.
- **Rails on the lock screen.** The agent has sudo; the desk shows what it did, and that stays behind the lock.

## Extends or changes the earlier briefs

- **The screen at rest is wallpaper and a pill:** unchanged. Widgets appear only with something to say; nothing is added at rest.
- **Background jobs as chips left of the pill:** kept as Watching's strip; its full face is new. **A card goes away when its job is done, a newer one replaces it, or Esc:** extended; a card still counting moves into Watching instead of dying.
- **One chip per project with a dot per session; hover peeks; "what's running?" opens a card:** kept exactly; the card is Sessions' full face and still opens on asking or a click, never on its own.
- **A notification is one line in the pill with a mark on the dot; the your-turn line and the Tab walk; the agent's question and provider trouble in the pill:** kept; Needs you is the walk shown as rows when two or more sessions wait, and Tab walks the same rows.
- **"What are you watching?" with stop buttons:** kept as the card for standing watchers; Watching shows only what is counting now.
- **"Welcome back" and "while you were away":** kept; Away is the same group as a card with buttons, and leaves once seen.
- **The closing line is the receipt, with Undo and Details:** unchanged; Now shows the ticked route and says Undo is above the pill; the red step's "can't be undone" now also appears while it runs, with "Esc stops it".
- **The pill narrows to 360px with an app in front; a capsule for full-screen:** unchanged; the desk's modes follow them, and the row is laid out as strips, pill, strips.
- **The stage, the shelf, cards in the column:** unchanged; widgets never use the column.
- **What skips the model:** extended with the word "desk", and with widget names in the launcher's name list so "show" and "hide" reach them. Hover-peek and click-to-keep now apply to every strip, not only the session dots.
- **A pin turns a card into an app:** kept; "keep this on the desk" makes that app also a widget.
- **Answer cards are typed JSON drawn by the shell; agent QML later in its own process:** the same rule now covers widgets.
- **Rewind folds into Time; the Map's colours:** unchanged; Away opens Time, the desk uses the Map's colours, and a corner of the Map lives on the desk only after "show alive".
- **The agent never starts work on its own:** kept, and extended to the desk: the `desk` tool works only in a turn that asked for the desk.
- **Home exclusions from the restore points:** unchanged; the desk's state lives in ~/.local/state/bombadil, which is already excluded.
- **Several monitors:** still open; the desk is on the pill's screen.

## Checked today, and to verify before building

- The bar today is one Quickshell PanelWindow per screen on the Overlay layer with an input mask and `exclusiveZone: 64` (shell.qml); Quickshell's PanelWindow reserves its whole width by default when anchored to three edges, so the rails set `exclusionMode: Normal` and `exclusiveZone: 0` explicitly.
- PillState.qml is fed by agentd's JSON events, drops kinds it does not know, and guards status against another turn's id; agentd stamps every event with the current turn. DeskState follows the same pattern: `plan` carries the turn and is matched, `desk` events are not turn-bound.
- Claude Code 2.1.283's stream has no TodoWrite; the plan arrives as TaskCreate and TaskUpdate, one task per call, which narrate.py already reads one at a time (tests/fixtures/claude-install-ffmpeg.jsonl, narrate.py). Codex's todo list arrives per update with only done or not done per item, and providers.py today keeps only the first unfinished one; the `plan` event needs the whole list.
- Apps open floating and centred in their own special workspace from a window rule (placement.py), sized to the monitor's usable area less the bar's reserved zone; with no exclusive zone on the rails, a 560x680 app on a 1280-wide screen never touches a 300px rail. Hyprland's `dim_special` is at its default (0.2) in the shipped config, so what lies under a special workspace is already dimmed.
- The pill row today is one centred Rectangle capped at 900px with the queue chips above it (shell.qml); the row as strips, pill, strips is new layout in the bar window.
- The kit has Panel, ListRow, Meter, Ring, Sparkline, StackedBar, Badge and Stat (share/qml/Bombadil on the app-kit branch), so every face is composition, not new drawing, except the Map corner, which the Brain app owns.
- To verify in the VM: a Bottom-layer surface under floating and special-workspace windows on Hyprland 0.56 with the Lua config, reserving nothing; toplevel rectangles from Quickshell.Hyprland refreshed at 500 ms during a drag, and the special-workspace shown and hidden events; how far `dim_special` reaches; that the cgroup files of turn scopes, the dev slice and app units are readable by the user for the Machine sampler; that a press in a rail window ending over the bar window can be resolved from screen coordinates.

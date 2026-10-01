# The shell

> **Status:** Partly shipped
> **Code:** `shell/shell.qml`, `shell/PillState.qml`, `shell/StatusLine.qml`, `shell/QueueChips.qml`, `shell/SetupChips.qml`, `shell/FoundChips.qml`, `shell/NoticeChips.qml`, `shell/AiCard.qml`, `shell/LineButton.qml`, `shell/Stone.qml`, `shell/HyprCover.qml`, `shell/Wallpaper.qml`, `shell/CardHost.qml` (hosting only), `bin/bombadil-shell`, `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`, `iso/airootfs/etc/skel/.config/bombadil/wallpaper`
> **Design:** [UX brief](../design/ux-brief.md#the-rules), [The Bombadil mark](../design/identity-brief.md#the-stone), [The poor man switch](../design/poor-man-switch-brief.md#the-pill-while-the-ai-rests), [Boot and the wallpaper](../design/boot-and-wallpaper.md), [the pill's states in the design system](../design-system/README.md#the-pills-states)
> **Verified:** 2026-10-01 against `main` at `969b80b`

The shell is the whole visible interface at login: one Quickshell program that draws a pill at the bottom of every screen, with the line and the chips above it (the line also says what other services ask agentd to say, such as new mail), the stone at its left end (a click on it opens the AI card), the picture host, the windows of the desk and the wallpaper under everything. It keeps no conversation: it turns what agentd says into what is on screen and sends back what the person types or clicks. It exists so that something true is on screen the moment Enter is pressed, and so that stop, undo and the launcher words never wait for a model. The states for an AI that is out of plan or paused are described here from the shell's side and in [the poor man switch](poor-man-switch.md) from agentd's; notices are described here and in [mail](mail.md).

## How it works

```mermaid
flowchart LR
    agentd["agentd socket"] -->|"one JSON object per line, root.handle"| pill["PillState"]
    agentd --> desk["DeskState"]
    pill -->|"mode, line, notice, risk, command, flash"| line["StatusLine, NoticeChips"]
    pill -->|"face"| stone["Stone"]
    pill -->|"queue, setupActions, foundChips"| chips["QueueChips, SetupChips, FoundChips"]
    pill -->|"card, aiOpen, aiRows"| cards["CardHost, AiCard"]
    pill -->|"summoned(text)"| focus["root.summon, keyboard focus"]
    desk -->|"needsYou"| pill
    desk -->|"windowOpened"| pill
    desk -->|"capsule, pillWidth, strips"| bar["the pill's width and the desk windows"]
    hypr["Hyprland events"] --> cover["HyprCover"]
    cover -->|"setWindows"| desk
    hypr --> appchips["app chips"]
```

**Start.** Hyprland runs `agentd`, `bombadil-shell` and `mako` on `hyprland.start`, then hands Mail's user unit this session's screen and restarts it (`systemctl --user import-environment ... && systemctl --user restart bombadil-mail.service`, `hyprland.lua:7-16`; [mail.md](mail.md)). `bin/bombadil-shell` puts `share/qml` on `QML2_IMPORT_PATH` and runs `quickshell -p <root>/shell/shell.qml`. `shell.qml` makes one `PillState` and one `DeskState` for the whole session and one `PanelWindow` per screen (`Variants { model: Quickshell.screens }`). The line, the chips and the stone read the same state on every screen. What differs per screen is the keyboard, the pill's width and the desk's strips. `shell.qml` also makes a `Wallpaper` ([the wallpaper](#the-wallpaper)).

**Connecting.** The socket path is `$BOMBADIL_SOCKET`, else `$XDG_RUNTIME_DIR/bombadil/agentd.sock`. A comment in `shell.qml` says a Quickshell `Socket` that failed to connect does not retry, so each attempt is a fresh `Socket` made from the `link` component. While `root.connected` is false a 1500 ms `Timer` (running from the start) destroys the last socket and makes the next. When the connection state changes, the shell sets `root.connected` and `deskState.connected`; a connect sets `pillState.connected`, and a drop calls `pillState.lost()` and `deskState.lost()`. agentd greets each client that connects with `status`, `entries`, `setup` and a `notice` for each notice nobody has ended (`src/bombadil/agentd.py:394-404`, [agentd.md](agentd.md#the-socket)), and `DeskState` asks for `desk get` as soon as it is connected, so a restarted bar is told what is running without any state of its own.

**Handling.** `SplitParser` hands each line to `root.handle`, which parses it (a line that is not JSON is dropped) and gives the object to `pillState.handle` and `deskState.handle`. Both objects emit `outgoing(msg)`; `root.write` sends it as one JSON line and does nothing while the socket is down. The pill's methods that send (`stop`, `unqueue`, `undo`, `details`, `setupAction`, `openThing`, `noticeAction`, `dismissNotice`, `openFound`, `setAi` and the opening half of `toggleAi`) check `_offline()` first and flash "Not connected to the agent yet." on the line; `submit` says the same as a `local` line and keeps the typed text; `closeDetails` stays silent. So a click while disconnected is answered, not lost.

**Hyprland.** Two things come from the compositor and not from agentd. The app chips are built from `Hyprland.toplevels` whose workspace is named `special:app-<name>`, refreshed on the raw events `openwindow`, `closewindow` and `movewindowv2`, with the shown workspace tracked from `activespecial`. What puts an app's window in that workspace is `src/bombadil/appkit/placement.py` (see [app-kit.md](app-kit.md)): before the window first maps, `prepare()` adds a runtime window rule named `bombadil-app-<name>` that floats and centres it at its own size, shrunk to the screen, with `workspace = special:app-<name>`. The static `bombadil-apps` rule in `hyprland.lua` floats and centres every `bombadil-app-*` window at 540 by 660 and names no workspace. `HyprCover` reports where the windows are to `DeskState.setWindows`, which decides whether the desk's cards fold and whether the pill becomes a capsule, and emits `windowOpened` when the list grew (`shell.qml:46` connects it to `pillState.windowOpened()`, which puts a picture away: [the picture host](#the-picture-host)). The app chips send nothing to the desk.

### The bar window

| Property | Value | `shell/shell.qml` |
|---|---|---|
| One window per screen | `Variants` over `Quickshell.screens`, each a `PanelWindow` | 192-194 |
| Edges | anchored left, right and bottom: as wide as the screen | 204 |
| Layer and name | `WlrLayer.Overlay`, namespace `bombadil-bar` | 207-208 |
| Background | `"transparent"` | 206 |
| Height | `column.implicitHeight + 24` (the column has 12 px margins) | 205 |
| Exclusive zone | `64 + (appChips.visible ? appChips.implicitHeight + column.spacing : 0)`: the pill's 52 plus 12 below it, plus the app chips row | 221 |
| Input mask | a `Region` of `cardHost`, `statusLine`, `foundChips` (only while visible), `setupChips` (only while visible), `chips`, `appChips`, `aiCard` (only while visible), `pillBox`, `stripsLeft` and `stripsRight` | 223-234 |

The window is as wide as the screen and transparent, so the mask is what lets clicks beside and above the pill reach the windows behind it. A control that is not listed in `mask` receives no clicks. `setupChips` is the one entry guarded by `visible`; the comment gives the reason (a hidden item keeps its last place). `cardHost`, `statusLine`, `chips` and `appChips` also hide themselves and have no guard (see [Known gaps](#known-gaps)). `DeskStrips` is zero wide and high when it has nothing to show, so that the mask takes no room for it (its own comment).

The `ColumnLayout` (`id: column`, 12 px margins, 8 px spacing) stacks these, top to bottom:

| Item | Is | Width limit |
|---|---|---|
| `cardHost` | a `Loader` for `CardHost.qml`, set up with `setSource`, so a picture that will not load or draw (a failure in `CardHost.qml` or in the kit's `Diagram`) costs the pictures and not the bar; the `Bombadil` module itself is not isolated this way (see [Theme](#theme-and-why-the-shell-starts-through-binbombadil-shell)); `maxHeight` is 60% of the screen; its `suppressed` follows `win.capsule` | `max(360, win.pillMax)` |
| `statusLine` | `StatusLine.qml` | `max(360, win.pillMax)` |
| `setupChips` | `SetupChips.qml`, shown while the setup line shows | `Kit.Theme.pillMaxWidth` |
| `chips` | `QueueChips.qml` | `max(360, win.pillMax)` |
| `appChips` | one chip per running app | `Kit.Theme.pillMaxWidth` |
| `pillBox` | the pill: stone, text field, hint, clock | `win.pillMax` |

`win.pillMax` is `deskState.pillWidth` on the screen the desk lives on and `Kit.Theme.pillMaxWidth` (900) on the others. On the desk's screen a window sharing the stage narrows the pill to 360, and a full-screen window turns it into a capsule of 100 px with the stone and the clock only: the field is hidden and disabled, "nothing can be typed blind" (`shell.qml:432`), and the picture is put away ([the picture host](#the-picture-host)). The widths come from `DeskState` ([desk.md](desk.md)). The desk's strips (`stripsLeft`, `stripsRight`) are children of this window, placed beside the pill. The desk's rails are separate windows (`DeskRails`, namespaces `bombadil-desk-left` and `bombadil-desk-right`, on `WlrLayer.Bottom`).

The pill (`pillBox`, `Kit.Theme.pillHeight` high, `glassPill`) holds, left to right:

- the stone slot, 24 px wide. While a turn can be stopped (`pillState.stoppable`) and the pointer is over it (not in the capsule), the stone gives way to a `Stop` button (a 9 px accent square and the word) and the slot widens to the button's width plus 16 px, on an `accentSoft` wash; a tap calls `pillState.stop()`.
- the text field. Its placeholder is "Starting" while the face is `starting`, else "Ask anything" when connected, else "Waiting for agentd…". A ghost row draws the rest of a name that Tab would complete, in `faint`.
- a hint `↵ <name>` when the typed words exactly name something the launcher opens ([the launcher mirror](#the-launcher-mirror-in-pillstate)).
- the clock, `HH:mm`, refreshed every 30 s.

The border is one rule, first match wins: `accent` while `pillState.busy`, `borderActive` while this screen's pill holds the keyboard, `badLine` when the face is `offline`, else `border`. It is `Kit.Theme.pillBorder` wide and its colour change takes `Kit.Theme.slow` (300 ms).

### Keyboard focus

On Hyprland the pill takes keys only while it is summoned. `root.summonedOn` holds the name of the one screen whose pill has the keyboard (`""` for none), so only one pill holds it at a time.

```mermaid
stateDiagram-v2
    [*] --> Released
    Released --> Summoned : Super tap, Alt+Space, a click on the pill or a summon with words
    Summoned --> Released : second tap, Enter, Esc on an empty pill, click away, idle, hand-off
```

| Compositor | Not summoned | Summoned |
|---|---|---|
| Hyprland (`HYPRLAND_INSTANCE_SIGNATURE` is set) | `WlrKeyboardFocus.None` | `WlrKeyboardFocus.OnDemand`, plus a `HyprlandFocusGrab` on the window |
| Any other (the headless sway test) | `OnDemand` | `Exclusive` |

The comment at `shell.qml:192-199` gives the reasons: the grab gives the pill the keyboard at once and a click anywhere else takes it away; going from `OnDemand` back to `None` makes Hyprland hand the keyboard to the window the person was in; and `Exclusive` is never used on Hyprland because Hyprland ends any grab when a layer turns exclusive, so the keys went to the window under the pointer. No pytest test runs the focus, mask or layer rules, and the desktop test and the smoke exercise them only in part (see [Tests](#tests)).

| Input | What happens | Made by |
|---|---|---|
| Tap `Super` | the pill on the focused monitor takes the keyboard; a second tap gives it back | bind `SUPER + SUPER_L` and `SUPER + SUPER_R` with `{ release = true }` runs `bombadil pill`, which sends `{"type": "summon"}` with no text; agentd broadcasts it; `PillState` emits `summoned("")`; `root.summon("")` toggles `summonedOn` (the focused monitor, else the first screen) |
| `Alt+Space` | the same | bind `ALT + space` runs `bombadil pill` |
| A summon with words | the pill on the focused monitor takes the keyboard and the words replace what was typed, with the cursor at the end. It never gives the keyboard back, so a repeat does not toggle | a client sends `{"type": "summon", "text": "About ~/lease.pdf: "}` (the Brain's Ask about this does, `share/apps/brain/app.py:554`; [agentd.md](agentd.md#messages-a-client-sends)); `PillState` emits `summoned(text)`; `root.summon(text)` stores `root.draft` and sets `summonedOn`; `takeDraft()` of the summoned window fills the field |
| Click on the pill | summons that screen (Hyprland only; elsewhere the layer takes clicks `OnDemand` by itself) | `TapHandler` on `pillBox` and on the field call `summonHere()` |
| `Enter` | sends the line as a `prompt`, clears the field and gives the keyboard back. A blank line, or one typed while disconnected, sends nothing and keeps the field and the keyboard | `onAccepted`: `pillState.submit(text)` returns true or false |
| `Esc` while a turn runs or a sign-in is under way | sends `stop`; the pill keeps the keyboard | `pillState.stoppable` |
| `Esc` with text typed | clears the text | `onEscapePressed` |
| `Esc` on an empty pill | puts the line and the picture away, sends `close_details`, gives the keyboard back | `dismiss()`, `closeDetails()`, `release()` |
| `Tab` | types the rest of the suggested name | `pillState.completion(text)` |
| Click anywhere else | the focus grab clears and the keyboard goes back | `HyprlandFocusGrab.onCleared` calls `release()` |
| Nothing typed for 20 s, or for 60 s with text in the pill | the keyboard goes back; the text stays | the `idle` `Timer`, restarted by `onTextChanged` |
| Details, every setup chip except Cancel, a picture's box that names a turn | the keyboard goes back before the drawer opens, so the drawer can take it | `PillState.handOff()`, which `shell.qml` connects to `release()` |
| `Super+Escape` | `bombadil stop`: ends the turn and everything it started. Nothing in the shell is involved | bind in `hyprland.lua` |
| `Super+Ctrl+Escape` | kills Quickshell and starts the bar again | bind: `pkill -x quickshell; bombadil-shell` |

`Esc`, `Enter` and `Tab` reach the field only while the pill holds the keyboard. When it does not, `Esc` goes to the window in front, and `Super+Escape` is the way to stop from anywhere. When the pill is summoned and its screen's face is `rest`, the stone shows `listening`.

### The pill's modes

`PillState.mode` is what the line shows. `optimistic` is a flag inside `working`: "On it" was drawn on Enter, before agentd confirmed a turn.

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> onIt : Enter
    onIt --> working : turn_start
    working --> closing : turn_end
    closing --> idle : fade or Esc
    onIt --> local : answered without a turn
    working --> local : connection lost
    local --> idle : fade or Esc
    idle --> setup : AI not ready
    setup --> idle : AI ready
```

The diagram is the main path. The rest, from `PillState.qml`: Enter from `closing` or `local` goes to `onIt` as it does from `idle`. `turn_start` goes to `working` from any mode, and a `status` event inside a turn rewrites the line without leaving `working`. An answer agentd gives without the model (a `local` event, or an error with no turn) makes mode `local` from `idle`, `closing` or `setup`. A turn that is queued and ends before it starts goes from `onIt` to `closing`. When a line fades or `Esc` puts it away while the AI is not ready, the setup line comes back instead of `idle`. A setup that becomes ready with a line to say ("Signed in to Claude. Ask me for anything.") goes to `local` for 8 s.

| Mode | When | What shows | Leaves when |
|---|---|---|---|
| `idle` | nothing to say | no line: `StatusLine` has height 0 and opacity 0, and its timer is stopped | any of the rows below |
| `working` | `submit` (as `onIt`, the line "On it"), or `turn_start`, or a `status` message that reports a running turn the bar did not know of (the line "Working") | the live line, one line long, its seconds counter from the first second, and an edge: amber for `risk` `system`, red for `irreversible`. A marked step also shows its exact `command`, its `because` and its `after`. A `local` answer shows over the line as a flash and the line comes back | `turn_end`, a `local` answer when no turn was confirmed, `lost()` |
| `closing` | `turn_end` | if the turn was stopped, the stop line (agentd's `line`, else "Stopped."); else, when the turn had an error and its result was not ok or there was none, the error's first two lines with a red edge; else the result text (up to four lines), else the summary, else "Done." (a turn with an error and an ok result shows the result); "can't be undone" after an irreversible step; a click on the line is Details. Details and Undo (while `sticky`) are also buttons, but only when the turn `changed` something or was `irreversible` | the fade, `Esc`, the next prompt or turn |
| `local` | an answer agentd gave without the model, an error with no turn, "Lost touch with the agent. Reconnecting.", "Not connected to the agent yet.", or the one-time "ready" line | the text; a red edge for an error | the fade, `Esc`, the next prompt |
| `setup` | `setup` message whose state is not ready (`choose`, `signed_out`, `offline`, `signing_in`) | the setup line and the `SetupChips`; a red edge when its tone is `error` | the state becomes ready |

`ready` is `setupState` of `""`, `"ready"` or `"checking"`. While it is false, a typed line is still sent as a `prompt`, no "On it" is drawn, and agentd queues it (a line that starts with `!` is the exception and runs). A turn has the line while it runs: a `setup` message that arrives mid-turn is stored, and while the AI is still not ready the setup line comes back when the turn's line goes (`_putLineAway`); a ready "Signed in" line that arrives mid-turn is not shown later. The "Signed in" line is said once to a bar that saw the state change, and never to a bar that has just started.

### The stone

`Stone.qml` draws the mark in the pill's left slot: a 24 by 24 item with an 18 px rounded Reuleaux triangle and a lowercase b cut through it. `PillState.face` decides the face (first match wins, in this order), and `shell.qml` changes `rest` to `listening` on the screen that holds the keyboard. For its first `settle` (400 ms) the stone is painted the resting green whatever its face says, then follows it (`Stone.qml:33-40`): the comment says the scene graph dropped a colour change made in the first frames after the bar started, so a face that went `starting` and then `rest` stayed orange. A bar that connects within 400 ms never shows the orange.

| `face` | `PillState.face` gives it when | Stone |
|---|---|---|
| `starting` | not connected, agentd has not answered since the bar started, and the 15 s `booting` timer in `shell.qml` is running | orange, rolling, after the first 400 ms; the placeholder says "Starting" |
| `offline` | not connected, and agentd was seen or the boot window is over | broken red outline with the b as a red line |
| `needs` | `needsYou` (the desk sets it while a coding session waits), or mode `setup` with state `choose`, `signed_out` or `offline` | amber, two knocks and a breathing glow on a 1.6 s beat |
| `working` | `busy`, `optimistic`, mode `working`, or setup state `signing_in` | orange, a third of a turn per second about the b, which stays still |
| `stopped` | mode `closing` and the turn was stopped | a 12 px muted (grey) rounded square: the shape of the Stop button's square, not its orange |
| `done` | mode `closing`, not stopped, not an error | green, one 700 ms hop, then still |
| `rest` | otherwise, including a closing line that is an error | green, still |
| `listening` | `rest` on the screen whose pill holds the keyboard (`shell.qml:408`) | green, leaning 6 degrees toward the text |

The motion, from `Stone.qml`: the roll holds 300 ms, turns 120 degrees in 550 ms on the curve (0.55, 0, 0.3, 1) and holds 150 ms; the knocks tip 8 degrees (192, 160, 160 and 160 ms, then a 928 ms wait); the glow runs 0.25 to 0.6 to 0.25 in 560 and 1040 ms and reaches 16 px from the stone's centre, past the 24 px slot into the pill's padding; the lean eases over 240 ms on (0.16, 1, 0.3, 1). The b is drawn in `ground` (default `Kit.Theme.glassPill`), so it reads as a cut only on a flat surface of that colour. With `reducedMotion` the roll becomes an opacity pulse (to `Kit.Theme.pulseLow` and back, 500 ms each way), the knock stops and the glow is held at 0.45, and the hop becomes a 600 ms fade in from 0.3. `shell.qml` sets `reducedMotion` from `BOMBADIL_REDUCE_MOTION=1` and hands it to the stone and to `Wallpaper` (which then shows its picture and its dimming without a fade); nothing in the repository sets that variable. The look is explained in [the design system](../design-system/README.md#the-mark-and-its-states) and why in [the identity brief](../design/identity-brief.md#the-stone).

### The line above the pill

`StatusLine.qml` shows `pill.line` (or `pill.flash` over it) while `mode` is not `idle` or a flash is up.

| What | Value | Where |
|---|---|---|
| Seconds counter | a 250 ms `Timer` that runs only while the line is shown and not held by a picture (below); the counter shows while working from 1 s on, in tabular figures | `StatusLine.qml:43-58, 116-124` |
| A turn's closing line stays | `fadeAfter`, 12000 ms after `turn_end` | `PillState.qml:49, 220` |
| A receipt picture after the closing line | both restart their clock; `fadeAfter` is raised to at least 15000 ms | `PillState.qml:112` |
| A local answer | 5000 ms; after an undo that finished 15000 ms; after a picture that finished 8000 ms | `PillState.qml:238-239` |
| "Signed in" line | 8000 ms | `PillState.qml:265` |
| "Lost touch with the agent. Reconnecting." | 8000 ms | `PillState.qml:317` |
| "Not connected to the agent yet." after Enter while disconnected | 4000 ms | `PillState.qml:282-286` |
| A flash over a running turn (a launcher answer, an error with no turn, a stop), and the answer to a click while disconnected | 3500 ms; 8000 ms for the answer to `why` | `PillState.qml:52, 190, 212, 232, 324` |
| When a line fades | mode `closing` or `local`, not `sticky`, `pictureStays` false, no line or picture hovered on any screen (`hovers` is 0), and `fadeAfter` passed; checked on the 250 ms tick | `StatusLine.qml:55-57` |
| A picture the person asked for | keeps the finished line it came with: `pictureStays` is true while `card` is set and is neither a receipt nor half drawn. The line does not fade and the timer sleeps (it runs only while a turn works or a flash is up) until the picture goes. Esc puts the line away with the picture; after the × the line fades by the row above: on the next tick if `fadeAfter` has already passed since `lineAt`, and when it passes if not. A receipt fades with its line | `PillState.qml:60`, `StatusLine.qml:49` |
| `sticky` | a turn that changed something and was not stopped keeps its line, with Undo, until the next prompt, Undo (`PillState.qml:342`) or Esc (`_putLineAway`, `PillState.qml:358`) | `PillState.qml:219` |
| Fades and growth | opacity `Kit.Theme.normal` (200 ms); height `Kit.Theme.fast` (120 ms) | `StatusLine.qml:30-31` |

Layout rules in the same file: while working the line is one line (`NoWrap`, `maximumLineCount` 1; two lines for a flash) and the agent's own words (`source` `agent`) show their newest end: `newest()` cuts them at a word, with a "…" before it, as many words as fit (`Text.ElideLeft` is left to cut one word that alone fills the line); in the other modes it wraps to at most four lines. The edge is `bad` for a working step of risk `irreversible` and for an error outside a running turn, `warn` for risk `system`. `because` shows on hover, and always on a marked step; `after` shows only on a marked step. Undo and Details are `LineButton`s (a 24 px high text button), shown in a row that exists only for a closing line that `changed` or was `irreversible`.

### Chips

| Chips | Component | From | A click |
|---|---|---|---|
| Queued prompts, grey, "next" and the text, and an ×. Objects named `queuedChip` | `QueueChips.qml` | `pill.queue`: replaced by every `status`, added by a `queued` event, removed by `unqueued` and by the `turn_start` of that turn | the × calls `pill.unqueue(turn)`: sends `unqueue` and drops the chip at once (not while disconnected) |
| Setup choices under the setup line. Objects named `setupChip` | `SetupChips.qml` | `pill.setupActions`, from the `setup` message: `[{id, label, style}]`. Visible only while mode is `setup` | `pill.setupAction(id)`: hands the keyboard off unless `id` is `cancel`, then sends `setup_action` |
| One chip per running app, with a × | in `shell.qml` | `Hyprland.toplevels` on workspaces named `special:app-<name>`, sorted by name; the title is the window's title | the chip runs `hl.dsp.workspace.toggle_special("app-<name>")` through `Hyprland.dispatch`; the × runs `bombadil-app close <name>` |

A setup chip's `style` is `big` (46 px high, at least 132 wide, 17 px semibold), `primary` (30 px, raised, accent border) or anything else (30 px, quiet). The chip shown for the app whose workspace is on screen has an accent border. The setup chips are built by agentd (`AgentD._describe`): a big chip for each provider on first boot, "Sign in" and "Use <provider> instead" when signed out, "Show sign-in", "Open it again" and "Cancel" during a sign-in, and "Wi-Fi" and "Try again" when offline. Their flow is in [browser-and-signin.md](browser-and-signin.md).

### The launcher mirror in `PillState`

`PillState.entries` is the `entries` message: the names the launcher opens (`kind` `app`, `panel`, `widget` or `command`, each with `words`). `completion(text)` returns the rest of a name for Tab, and `exact(text)` names what an exact word would open, drawn as `↵ <name>`. Both mirror `launcher.py`: only spaces, hyphens and underscores fold away (`_key`), a line that starts with `!` is never a word, a question mark at the end keeps a widget or `desk` for the agent, and a widget's name counts only after one of `open`, `show`, `close`, `hide`. The hint exists so the person sees that a word opens here without the model. It is a hint: agentd decides.

### The picture host

`CardHost.qml` draws `pill.card` (a diagram from `show_card`, `system_map` or a receipt) above the line with the kit's `Diagram`: a title, "from this machine" or "drawn by the agent" (or "drawing…" while partial), a close ×, the diagram in a scrolling `Flickable`, and one `say` sentence. It keeps the last card while it fades out. Its height is not animated: the bar's window is as tall as its contents, so a height that grew over a few frames would resize the window every frame, and Hyprland's layer animation would swing the pill with each resize (the file's comment; `hyprland.lua` turns that animation off, [below](#the-hyprland-config-the-iso-ships)). The card takes its room at once, fades in and rises 14 px inside it, and gives the room back once it has faded out. `PillState` takes a card from an `event` `card` (type `diagram` only; `{id, gone}` takes one back). One card is shown at a time and a newer one replaces it. A box that names something calls `pill.openThing(target)`, which sends `open` (or `details` for a turn). What a card holds and how it is validated is in [cards-and-pictures.md](cards-and-pictures.md).

| What happens | What it does to the picture |
|---|---|
| `Esc` (`dismiss()`) | puts the picture away, and a finished line with it |
| the × (`dismissCard()`) | puts the picture away and nothing else. The line follows its own rules ([the line](#the-line-above-the-pill)): a `sticky` closing line stays with its Undo. The comments at `PillState.qml:59` and `:115` say the × puts both away; the code does not |
| `turn_start` | clears it |
| a picture word that failed to draw (`local`, `action` `picture`, `phase` `done`, `ok` false) | puts the old one away |
| a launcher open that finished ok (`local`, `phase` `done`, `ok` true, `verb` `open`, `action` `brain`, `app` or `panel`) | puts it away, so it is not over the window that opened. A hide, a close, a failed open and a click on a box (`action` `open`) keep it |
| the connection lost while it is half drawn | takes it away |
| a full-screen window on this screen (`CardHost.suppressed`, bound to `win.capsule` in `shell.qml:286`) | hides it and gives its room back; it is still there and comes back when the window goes |
| the line it came with fading | a receipt goes with its line; a picture the person asked for stays ([the line](#the-line-above-the-pill)) |

### The wallpaper

`shell/Wallpaper.qml` is a `Scope` that `shell.qml` makes once. For each screen it opens a window on `WlrLayer.Background` (namespace `bombadil-wallpaper`, `ExclusionMode.Ignore` so that it reaches the screen's edge under the bar's zone, an empty `mask` so that it takes no clicks) in the ground colour `Kit.Theme.bg`, and the picture fades in over it once (`Kit.Theme.slow`), so a slow, missing or broken picture leaves the plain ground. The standard picture is `share/wallpaper/bombadil.png`, found with `Quickshell.shellPath("../share/wallpaper/bombadil.png")`: `Qt.resolvedUrl("../share/...")` from `shell/` gives a dead end (`qrc:/qs-blackhole`), because Quickshell hides what lies outside the shell's folder. [Boot and the wallpaper](../design/boot-and-wallpaper.md) has the reasons, how the picture is drawn and the pictures.

| The `wallpaper` file in the config folder | The desk shows |
|---|---|
| missing, empty, or only `#` lines | the standard picture, undimmed |
| the last line that is not a `#` comment starts with `/`, `~/` or `file://` | that image, cropped to fill the screen (`PreserveAspectCrop`), with a layer of `Kit.Theme.bg` over it at opacity `dim` (0.6) |
| that line is anything else (an image written into the file is the usual case) | the standard picture, and a `console.warn` that names the file and never what is in it |
| the image will not load | the standard picture on that screen, undimmed; the next change of the choice tries again |

The config folder is `$BOMBADIL_CONFIG`, else `$XDG_CONFIG_HOME/bombadil`, else `~/.config/bombadil`, the folder `paths.config_dir` gives agentd. A `FileView` with `watchChanges` reloads the file when it changes. The skeleton ships the file with only comments in it, because a file that does not exist cannot be watched (the comment in `Wallpaper.qml`): with it there, a first path is noticed with no restart; where the file did not exist when the bar started, a first one needs the bar started again (`Super+Ctrl+Escape`). `dim` is 0.6 so that the muted text keeps a contrast of 4.5 on the quietest glass (`glassChip`) over a pure white picture; `tests/test_wallpaper.py` works that out from the tokens. `reducedMotion` takes the fade-in and the fade of the dim to 0.

### The desk's hosts

`shell.qml` hosts the desk and does not draw it: `DeskState` (fed by the same messages), a `DeskRails` per screen (inactive on all but the desk's screen), the two `DeskStrips` in the bar window, and `HyprCover`. The desk's screen is the one `desk.toml` names, else the first. `HyprCover` is the compositor-facing half: it asks Hyprland where the windows are and gives `desk.setWindows` the list.

| Part | What it does (`shell/HyprCover.qml`) |
|---|---|
| Refresh | `Hyprland.refreshMonitors()` and `refreshToplevels()`, then a `settle()` 40 ms later, and again 250 ms later for a slow answer |
| Events that refresh | `openwindow`, `closewindow`, `movewindow`, `movewindowv2`, `workspace`, `workspacev2`, `activespecial`, `activespecialv2`, `fullscreen`, `monitoradded`, `monitorremoved`, `focusedmon`, `changefloatingmode` |
| Polling | every 300 ms (`pollMs`) while any window is on the stage, because nothing reports a drag |
| `settle()` | takes windows on the desk screen's active workspace or its shown special workspace, drops unmapped and hidden ones, and gives each `{x, y, w, h, kind, fullscreen}` in screen coordinates; `kind` is `panel` for a special workspace; `fullscreen` is true for Hyprland's states 2 and 3 |
| Startup | if the monitor's own answer has not arrived, it asks again every 100 ms, at most 20 times |
| Without Hyprland | does nothing; `qs ipc call desk cover` stands in |

### Theme, and why the shell starts through `bin/bombadil-shell`

The bar's files that draw (`shell.qml`, `StatusLine`, `QueueChips`, `SetupChips`, `LineButton`, `Stone`, `CardHost` and `Wallpaper`) import the kit as `import Bombadil as Kit` and read `Kit.Theme.<token>`: colours (`glassPill`, `glassLine`, `glassChip`, `borderActive`, `accent`, `warn`, `bad`, `good`, `fg`, `muted`, `faint` and the rest), sizes (`pillHeight`, `radiusPill`, `chipHeight`, `pillMaxWidth`, `promptSize`, `lineSize`), type (`fontFamily`, `monoFamily`) and motion (`fast`, `normal`, `slow`, `pulseLow`). The desk's files (`DeskState`, `DeskCard`, `DeskRail`, `DeskRails`, `DeskStrip`, `DeskStrips`, `NowCard` and `RowsCard`) read `shell/DeskTheme.js` as `T` instead, a `.pragma library` script of plain values that cannot import a directory. `tests/test_theme.py` keeps the 25 of its 33 values that have a token equal to that token; `railMargin`, `railTop`, `railBottom`, `cardGap`, `stripGap`, `foldMs`, `unfoldDelayMs` and `washMs` are layout and timing values with no token, and nothing compares them. `PillState` and `HyprCover` use no theme values.

Quickshell resolves a singleton only from the shell's own directory or from the import path, and a bare `quickshell -p shell/shell.qml` starts a bar whose colours are all undefined (`tests/test_theme.py:115`). So `bin/bombadil-shell` sets `QML2_IMPORT_PATH` to `<root>/share/qml` (keeping what was already there), finds `<root>` with `readlink -f` so the ISO's symlink in `/usr/local/bin` still reaches `/usr/share/bombadil`, and `exec`s `quickshell -p <root>/shell/shell.qml "$@"`. Every starter uses it: `hyprland.lua` (at login and in the restart bind), `scripts/dev-session.sh` and `tests/desktop/driver.py`. A test fails if any of them calls `quickshell -p ...shell.qml` itself.

### The Hyprland config the ISO ships

`iso/airootfs/etc/skel/.config/hypr/hyprland.lua` is the session's whole Hyprland config. `bombadil-live.service` creates the session user with `useradd -m`, which fills the home folder from `/etc/skel` when it makes the home, and `bombadil-install` runs `cp -aT /etc/skel /home/user` in the installed system, which overwrites same-named files in a home that already exists. greetd starts `start-hyprland` as that user ([iso-and-install.md](iso-and-install.md)).

| Part | Lines | What it does |
|---|---|---|
| Monitors | 2-5 | any output at its preferred mode; `Virtual-1` forced to 1920x1080@60 because a QEMU guest reports the host window's size as its preferred mode |
| Start | 7-12 | `hyprland.start` runs `agentd`, `bombadil-shell` and `mako` |
| Look | 14-43 | gaps 6 inside and 12 outside, 1 px borders (`rgba(d97757ee)` active, `rgba(2a2f36ee)` inactive), `dwindle`, rounding 12, blur size 6 with 2 passes, background `0xff101214` (the ground until the wallpaper is up, in `0xAARRGGBB`: without the alpha byte Hyprland draws black), `follow_mouse = 1`; Hyprland's logo and splash off (`disable_hyprland_logo`, `disable_splash_rendering`); the keyboard layout fixed to `us` (`input.kb_layout`, line 37), which a fork for another layout changes; the `ease` curve (0.16, 1) (0.3, 1) for windows, special workspaces (`slidevert`) and fades |
| Layer animation | 44-47 | `hl.animation({ leaf = "layers", enabled = false })`: the bar is a layer, and Hyprland slid it whenever a picture resized it (the file's comment), so layers take their new size at once. Pinned by `test_hyprland_does_not_slide_the_bar_when_a_picture_resizes_it` |
| Panel keys | 49-54 | `SUPER + B`, `T`, `F` toggle the special workspaces `browser`, `terminal` and `files`; `SUPER + Q` closes the window; `SUPER + Return` runs `foot` |
| Pill key | 55-61 | `SUPER + SUPER_L` and `SUPER + SUPER_R` with `{ release = true }`, and `ALT + space`, run `bombadil pill`. The comment says a Super tap fires on release and Hyprland drops it when another key, a click or a drag happened meanwhile; Alt+Space is for hosts where Super never arrives, such as a VM window on Windows, where the Windows key opens the Start menu |
| Stop and rescue | 62-65 | `SUPER + Escape` runs `bombadil stop`; `SUPER + CTRL + Escape` runs `pkill -x quickshell; bombadil-shell` |
| Window rules | 67-81 | `bombadil-apps` (class `^(bombadil-app-.*)$`: float, centre, 540 by 660); `panel-browser`, `panel-terminal`, `panel-files` and `panel-details` put the classes `bombadil-browser`, `bombadil-terminal`, `org.gnome.Nautilus` and `bombadil-details` on `special:browser`, `special:terminal`, `special:files` and `special:details`, all `silent` |

The colours in this file are literals that repeat `Theme.qml` values. Only `background_color` is checked, against Theme `bg`, by `tests/test_boot_console.py::test_hyprlands_own_background_is_the_opaque_ground`. The two border colours (`d97757ee` and `2a2f36ee`, Theme `accent` and `border` with an alpha byte) are pinned by no test. `tests/test_theme.py` reads the file only to reject a launch of `quickshell` that bypasses `bin/bombadil-shell`. `mako` is started and the shell draws no notifications.

## Interfaces other pieces depend on

### Messages the shell reads

Field lists are in [agentd.md](agentd.md#messages-agentd-sends). This table says what the shell does with them.

| Message | `PillState` | `DeskState` |
|---|---|---|
| `status` (type) | `connected = true`, `busy`, `provider`, `queue`; a busy status that names a turn the bar is not following starts the "Working" line | resumes or clears the plan it follows |
| `entries` | `entries`, for Tab and the `↵` hint | |
| `setup` | `state`, `line`, `tone` and `actions` (`provider`, `title`, `phase`, `view` are not read) | |
| `summon` | emits `summoned(text)`: `text` when it is a string, else `""`. An empty text toggles the keyboard, a non-empty one fills the field ([Keyboard focus](#keyboard-focus)) | |
| `local` (type) | agentd took the typed word: the "On it" becomes the line "…", mode `local` | |
| `queued` (type) | follows the turn id agentd gave the prompt | |
| `event` `queued`, `unqueued` | adds or removes a queued chip | |
| `event` `turn_start` | clears the picture and the line fields, "On it", mode `working` | starts following the turn |
| `event` `status` | `text`, `source`, `risk`, `command`, `because`, `after.text`, for the running turn | the risk and command of the step |
| `event` `result`, `error` | kept for the closing line; an error with no turn is a flash or a `local` line | kept for the `now` card |
| `event` `turn_end` | mode `closing`, with Undo while `changed` and not stopped | closes the plan |
| `event` `local` | a flash while a turn runs, else mode `local`; picks the fade time; clears the picture for a failed picture and for a finished open of `brain`, `app` or `panel` ([the picture host](#the-picture-host)) | |
| `event` `card` | `card` (type `diagram` only, partial or whole, or `{id, gone}`) | |
| `event` `plan`, `tool_result` | not read | the plan and the step's end |
| `desk`, `jobs`, `dev` | not read | the desk's layout, the Watching rows, the Needs you rows; while a Needs you row exists, a `Binding` in `DeskState` sets `PillState.needsYou` |
| `event` `snapshot`, `text`, `tool`, `file_change`, `output`; `card_ack`, `desk-result`, `job-result` | not read | not read |

### Messages the shell sends

| Message | Sent when | From |
|---|---|---|
| `{"type": "prompt", "text"}` | Enter on a non-blank line | `PillState.submit` |
| `{"type": "stop"}` | `Esc` or the Stop button while `stoppable` | `PillState.stop` |
| `{"type": "unqueue", "turn"}` | the × on a queued chip | `PillState.unqueue` |
| `{"type": "local", "action": "undo"}` | the Undo button | `PillState.undo` |
| `{"type": "details", "turn"}` | the Details button, a click on a closing line, a picture's box that names a turn, the title of the `now` card | `PillState.details`, `openThing` |
| `{"type": "close_details"}` | `Esc` on an empty pill, while connected | `PillState.closeDetails` |
| `{"type": "open", "kind", "value"}` | a picture's box that names a file, a service, a package or a page | `PillState.openThing` |
| `{"type": "setup_action", "id"}` | a setup chip | `PillState.setupAction` |
| `{"type": "desk", "op", ...}`, `{"type": "jobs", "op", "id"}` | the desk's gestures, and `desk get` on connect | `DeskState` |
| `{"type": "dev", "action": "open", "key"}` | the Open button of a Needs you row | `DeskState`; agentd on `main` has no handler for it |

### Commands, keys and surfaces

| Name | Meaning |
|---|---|
| `bin/bombadil-shell [quickshell options]` | starts the bar |
| `scripts/dev-session.sh` | runs agentd and the bar from the checkout on an existing Hyprland desktop: sets `BOMBADIL_PROVIDER` (default `fake`) and `BOMBADIL_SHARE`, starts `bin/agentd`, then `bombadil-shell` ([development.md](../contributing/development.md#run-a-dev-session-against-a-checkout)) |
| `bombadil pill` | sends `summon` with no text; the Super and Alt+Space binds run it |
| `bombadil stop` | sends `stop`; `SUPER + Escape` runs it |
| `quickshell ipc -p shell/shell.qml call desk state` | the desk's state as JSON (`IpcHandler`, target `desk`) |
| `quickshell ipc -p shell/shell.qml call desk cover '{"windows": [{"x": 0, "y": 560, "w": 500, "h": 160}]}'` | stand-in windows for a session without Hyprland (`fullscreen` is optional) |
| `quickshell ipc -p shell/shell.qml call desk inject '<json message>'` | feeds `root.handle` a message as agentd would send it |
| `bombadil-app close <name>` | run by an app chip's × |
| layer namespaces | `bombadil-bar` (Overlay), `bombadil-desk-left` and `bombadil-desk-right` (Bottom), `bombadil-wallpaper` (Background) |
| special workspaces | `special:app-<name>` for each app, set by the per-app window rule of `src/bombadil/appkit/placement.py` and read by the chips; `special:browser`, `special:terminal`, `special:files`, `special:details` for the panels and the drawer |

### Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `BOMBADIL_SOCKET` | `shell.qml:90` | the socket path; else `$XDG_RUNTIME_DIR/bombadil/agentd.sock` |
| `HYPRLAND_INSTANCE_SIGNATURE` | `shell.qml:22`, `HyprCover.qml:15` | set by Hyprland; chooses the focus rules and turns `HyprCover` on |
| `BOMBADIL_REDUCE_MOTION` | `shell.qml:24` | `1` makes the stone pulse instead of roll and knock, and the wallpaper appear and dim without a fade |
| `QML2_IMPORT_PATH` | set by `bin/bombadil-shell` | gives Quickshell the kit |
| `BOMBADIL_CONFIG` | `shell/Wallpaper.qml` | the config folder that holds the `wallpaper` file; else `$XDG_CONFIG_HOME/bombadil`, else `~/.config/bombadil` |
| `BOMBADIL_SCREENS` | the offscreen QML tests (`tests/test_pill_qml.py`, `test_stone_qml.py`, `test_desk_qml.py`, `test_desk_cards_qml.py`, `test_diagram_qml.py`) | a folder to save a picture of each state in |

## Where state lives

The shell writes no file. It reads the environment variables above, the socket, Hyprland's state and the `wallpaper` file.

| What | Where |
|---|---|
| The line, the queue, the face, the setup state, the picture, `hovers` | properties of `PillState` in the shell's memory. A restarted bar rebuilds them from agentd's greeting, and from `status` if a turn is running |
| Which screen holds the keyboard, and words an app put in the pill that no pill has taken yet | `root.summonedOn` and `root.draft` in `shell.qml` |
| The desk's layout | agentd's `desk.toml` in its state folder, sent as `desk` messages ([desk.md](desk.md)) |
| The running apps | Hyprland's special workspaces; the chips read `Hyprland.toplevels` |
| The socket | `$XDG_RUNTIME_DIR/bombadil/agentd.sock`, owned by agentd |
| The wallpaper's choice | `wallpaper` in the config folder: the last line that is not a `#` comment names an image, read by `Wallpaper.qml`, which watches the file. The skeleton file (`iso/airootfs/etc/skel/.config/bombadil/wallpaper`) holds only comments. The installer also copies the live user's `~/.config/bombadil` to the installed home (`iso/airootfs/usr/local/bin/bombadil-install:73-77`), so the file comes along and a picture it names under the live home does not |
| The Hyprland config | `/etc/skel/.config/hypr/hyprland.lua`, copied to `~/.config/hypr/hyprland.lua` when the home folder is made or when the installer runs |

## Principles it keeps

- [One pill, three things to learn](../principles.md#stupidly-simple): the bar is the pill, the line and chips that appear on demand, and the keys are Super or Alt+Space, Esc and the word `undo`. The trap is a second control surface (a button row, a menu, a settings page) in the bar window; look for the version that is a word in the pill.
- [Something true in 200 ms](../principles.md#something-true-in-200-ms): `PillState.submit` draws "On it" and the working face on the keypress and `agentd` confirms later. The trap is waiting for `turn_start`, an animation or a reply before showing a state; every added state needs a line or a face that appears on the event that causes it.
- [Quiet at rest](../principles.md#quiet-at-rest): at rest the screen is the wallpaper, the pill and a still stone, and `StatusLine`'s timer runs only while a line shows and no picture holds it. Every movement means one thing (roll, knock, hop, lean). Roll, knock, glow and hop have a reduced-motion version; the lean's 240 ms ease has none. The trap is a movement that means nothing or has no still version; reduced motion reaches only the stone (not its lean) and the wallpaper's fade-in and dim.
- [Colour says who](../principles.md#colour-says-who): the bar's colours are `Kit.Theme` tokens, the desk's are `DeskTheme.js` values that `tests/test_theme.py` keeps equal to their tokens, and the same test fails a hex literal or a missing `font.family` in any `shell/*.qml`. The trap is using `accent` for decoration: orange means the machine acting, amber a step that touches the system, red a failure or a step that cannot be undone.
- [Recovery never goes through the part that broke](../principles.md#recovery-without-the-broken-part): `Super+Escape` runs `bombadil stop`, which writes to agentd's socket directly, and `Super+Ctrl+Escape` restarts the bar; neither goes through the model or through the shell's QML. The trap is moving a recovery action into a control that needs the shell to be alive.
- [Every piece degrades](../principles.md#degrade-and-recover): the bar reconnects every 1500 ms with a fresh socket, says "Waiting for agentd…" while it waits, and hosts the picture card in a `Loader` so a card that cannot load or draw (`CardHost.qml` or the kit's `Diagram` failing) costs the cards and never the bar. A wallpaper that is missing or will not load leaves the plain ground. The trap is a component that fails to load and takes the whole window with it; host anything that depends on a part that can be missing the way `cardHost` is. The `Bombadil` module is not such a part: every file of the bar imports it, which is why the bar starts through `bin/bombadil-shell`.

## Extending it

There is no registry in the shell. `shell.qml` names each component by file name, and the test harness imports the directory, so a file in `shell/` is available by its name.

### Add a pill state, a line field or a face

1. Check that agentd already sends what you need ([agentd.md](agentd.md#events-the-shell-can-receive)). If not, add the event or the field there first.
2. In `shell/PillState.qml` add the property. Set it in `handle(ev)`: a top-level `ev.type` branch for a message, or a `case` in `switch (ev.kind)` for an event. Reset it where the others are reset (`turn_start` and `turn_end` clear `risk`, `command`, `because` and `after`).
3. If it changes the stone, edit the `face` binding. The first match wins, so the order is the precedence (`needs` outranks `working`; `tests/test_pill_qml.py::test_needs_you_outranks_a_running_turn` pins it). A face that only one screen shows is mapped in `shell.qml` as `listening` is (`face: win.summoned && pillState.face === "rest" ? "listening" : pillState.face`). The faces themselves are drawn in `shell/Stone.qml`; [the design system's recipe](../design-system/README.md#3-add-a-state-to-the-stone) lists the steps.
4. If it shows on the line, bind it in `shell/StatusLine.qml`: a `Text` with an `objectName`, `font.family: Kit.Theme.fontFamily` and a `visible` rule, or a change to `edge`.
5. Add a test to `tests/test_pill_qml.py` with `bar.send(kind=..., turn=...)`, `bar.call("method", ...)`, `bar.text("name")` and `bar.shown("name")`.

### Add a chip row

1. Copy `shell/QueueChips.qml`: a `RowLayout` with `required property var pill`, a `visible` rule on a `PillState` array (the row hides itself when the array is empty), and a `Repeater` whose delegate is a `Rectangle` of `Kit.Theme.chipHeight` with an `objectName`. Colours come from `glassChip` and `border`; each `Text` sets `font.family`.
2. In `PillState.qml` add the array property, fill it from the message, and add the method the click calls. Guard it with `if (_offline()) return` and send through `outgoing(...)`, as `unqueue` does. Call `handOff()` first if the click opens a window that needs the keyboard, as `setupAction` does.
3. In `shell.qml` add the row to the `ColumnLayout` (`id: column`) at the place it should sit, with `Layout.fillWidth: false`, `Layout.alignment: Qt.AlignHCenter` and a `Layout.maximumWidth`, as the other rows have. Add the row to the window's `mask`: a row outside the mask receives no clicks. For a row that hides itself, write the entry as `Region { item: <id>.visible ? <id> : null }`, as `setupChips` does (`shell/shell.qml:209`), because a hidden item keeps its last place in the mask. The other self-hiding rows are listed without that guard ([Known gaps](#known-gaps)), so do not copy their entries without checking, by clicking where the row was, that the window behind gets the click. Add its height to `exclusiveZone` only if tiled windows must be kept clear of it, as for the app chips; the line, the queue chips and the picture are drawn over windows.
4. Add the row to `HARNESS` in `tests/test_pill_qml.py`, or the offscreen tests will not see it.

A setup chip needs no shell change: agentd returns `{id, label, style}` from its setup description (`style` of `big`, `primary`, or anything else for the quiet one) and handles the `id` in `setup_action` ([browser-and-signin.md](browser-and-signin.md)). The shell hands the keyboard off for every id except `cancel`.

### Add a keybind

A bind that needs only Hyprland is one line in `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`, next to the panel keys: `hl.bind("SUPER + X", hl.dsp.exec_cmd("..."))`. The binds already taken are `SUPER` with `B`, `T`, `F`, `Q`, `Return`, `Escape` and `CTRL + Escape`, the two Super taps and `ALT + space`. There is no table of binds; this file is the table.

A bind that must reach the pill follows the path `summon` takes:

1. In `bin/bombadil` add a command that calls `send({"type": "<word>"})` (it returns at once, so a bind never hangs) and add its line to the docstring.
2. In `AgentD.handle` (`src/bombadil/agentd.py`) add a branch that broadcasts the message (`summon` is the `elif t == "summon"` branch at `:347-349`, which also passes optional `text` through `_pill_words`; [agentd.md](agentd.md)), and add the message to the client-protocol list in the module docstring, where each socket message is documented (`summon` is at lines 17 and 48).
3. In `PillState.handle` add `if (ev.type === "<word>") { <signal>(...); return }` and declare the signal with the arguments it carries (`signal summoned(string text)` is declared at `PillState.qml:12`; the branch that emits it is at `:146`).
4. In `shell.qml` connect it in the `PillState { }` block, as `onSummoned: text => root.summon(text)` is (`shell.qml:30`). The connection must take the signal's arguments.
5. Bind it in `hyprland.lua` with `hl.dsp.exec_cmd("bombadil <word>")`. A modifier tapped alone needs `{ release = true }`.
6. Cover it: add a broadcast test beside `test_key_binds_reach_the_bar` in `tests/test_agentd.py` (it asserts the `summon` line at :360, and :416 does the same inside another test; `test_a_summon_can_carry_words_for_the_pill` at :366 is the model for a message with a field); extend the regex in `tests/test_iso_profile.py::test_the_pill_opens_from_super_and_from_alt_space` for the bind (it matches `bombadil pill` only); add a `PillState` test: connect the new signal in the `PillState { }` block of `HARNESS` and collect its arguments in a property, as `onSummoned` fills `summons` and `onHandOff` counts `handOffs` (`tests/test_pill_qml.py:36-43`), then assert on that property; `test_a_summon_can_carry_words_for_the_pill` at `:556` is the model, driven with `bar.send(type="<word>")`; and add a `keys` step to `iso/airootfs/usr/local/bin/bombadil-smoke`, which is where the binds are exercised in a real session.

The repository file is a skeleton: `useradd -m` copies it only when it makes the home, the installer copies it over the home it finds, and nothing copies it again, so a changed repository file does not reach a system that is already installed. `update-in-place`, the development tool that copies a checkout into a VM, leaves `~/.config/hypr/hyprland.lua` to be changed by hand (`scripts/vm-tools/update-in-place:14-16`).

### Add a shell component

1. Create `shell/<Name>.qml` with `import QtQuick` and `import Bombadil as Kit`. The alias must be exactly that: `tests/test_theme.py` rejects another. Take colours, sizes and motion from `Kit.Theme`, set `font.family: Kit.Theme.fontFamily` (`monoFamily` for commands) on every `Text` and `TextField`, and write no hex colour and no `"white"` or `"black"`; the test fails the file.
2. Keep logic in plain QtQuick (`PillState` has no Quickshell type, so pytest loads it). Put `Quickshell.*` types (windows, `Hyprland`, `Socket`) in files the offscreen tests do not load, as `HyprCover.qml`, `DeskRails.qml` and `Wallpaper.qml` do.
3. If it can fail to load, host it through `Loader { Component.onCompleted: setSource("<Name>.qml", {...}) }` as `cardHost` does.
4. Place it in `shell.qml`: in the bar window's column (and add its `Region` to the mask), or as its own window per screen, as `DeskRails` and `Wallpaper` are, with a `WlrLayershell.namespace` and a `mask`.
5. Test it as `tests/test_pill_qml.py` tests the line: add it to `HARNESS` and give each thing a test looks for an `objectName`. A file that holds Quickshell types is checked as text, as `tests/test_wallpaper.py` does for `Wallpaper.qml`.
6. Nothing else registers it: `scripts/build-iso.sh` copies the `shell` folder whole to `/usr/share/bombadil/shell`.

### Add a status-line behaviour

- **An added field on a step.** `because` and `after` are the examples. agentd adds the field to the `status` event; `PillState.qml` reads it in `case "status"` and clears it in `turn_start` and `turn_end`; `StatusLine.qml` gets a `Text` with an `objectName` and a `visible` rule that includes `mode === "working"` and `flash === ""`. Test: send a `status` with the field and check `bar.shown("name")` (`test_resting_on_the_line_shows_why_and_a_marked_step_always_does` is the model).
- **Something that happens with time.** Use the 250 ms `Timer` in `StatusLine.qml` (`objectName: "lineTimer"`). Its `running` is `bar.shown` less a finished line that a picture holds (`pictureStays`, mode not `working`, no flash), so a behaviour that must tick under a picture the person asked for needs that condition extended. How long a line stays is `PillState.fadeAfter`, set where the line appears together with `lineAt`; a local answer picks its time in the `local` case by `action` and `phase`. A test sets `fadeAfter` small and pumps events (`test_launcher_answers_replace_on_it_and_fade`; `test_a_picture_you_asked_for_keeps_its_line_so_the_picture_does_not_drop_when_the_line_goes` reads `lineTimer`'s `running`).
- **Something hover keeps alive.** Count it in `pill.hovers` the way `StatusLine` and `CardHost` do: add on enter, subtract on leave and in `Component.onDestruction`.

## Tests

| File | Covers |
|---|---|
| `tests/test_pill_qml.py` (64 cases) | `PillState`, `StatusLine`, `QueueChips`, `SetupChips` and `CardHost` in an offscreen window with a harness that mirrors `shell.qml`'s column: a turn's steps, the closing line and Undo, risk edges, `because` and `after`, flashes, fades and hover, queued chips, setup and sign-in chips, the stone's faces, the launcher's `completion` and `exact`, a `summon` with words, pictures (a picture the person asked for keeps its line and its timer sleeps, a receipt goes with its line, a full-screen window puts the picture away, a launcher open of the Brain, an app or a panel clears it, a failed picture does), a lost connection |
| `tests/test_stone_qml.py` (14 tests) | each face of `Stone.qml` rendered offscreen and read back by pixel: colours, the b staying inside the stone in every pose, a third of a turn looking like the start, two knocks, reduced motion, the hop, the 400 ms `settle` (green first, then the face), the glow reaching past the slot |
| `tests/test_theme.py` (40 cases) | 25 of `DeskTheme.js`'s 33 values equal to their `Theme.qml` token (the layout and timing values have none), the tokens the shell needs, no colour literal and a `font.family` in every `shell/*.qml`, the `Bombadil as Kit` import, and `bin/bombadil-shell` as the only way the shell starts |
| `tests/test_desk_qml.py` | `DeskState` driven beside `PillState` by a harness that repeats what `shell.qml` does with a line and lays out its windows; its cases are described in [desk.md](desk.md) |
| `tests/test_agentd.py` | the agentd side of the binds: `test_key_binds_reach_the_bar` runs `bombadil pill` and checks that agentd broadcasts `summon`, and `test_a_summon_can_carry_words_for_the_pill` checks the `text` (the rest is in [agentd.md](agentd.md)) |
| `tests/test_iso_profile.py` (7 tests) | among others, that `hyprland.lua` binds `bombadil pill` to `SUPER + SUPER_L`, `SUPER + SUPER_R` and `ALT + space`, and turns the `layers` animation off |
| `tests/test_wallpaper.py` (14 tests) | the picture against the tokens, and the host in the shell as text: `Wallpaper.qml` is on `WlrLayer.Background` with namespace `bombadil-wallpaper`, an empty `mask`, the picture found through `Quickshell.shellPath`, the choice read from the `wallpaper` file in the config folder, the dim worked out from the tokens, the skeleton file holding only comments, the warning that names no content, and a `Wallpaper {` entry in `shell.qml` ([boot and wallpaper](../design/boot-and-wallpaper.md)). The two tests that draw the picture again skip without `numpy`, `pillow` and `cairosvg` |
| `tests/test_boot_console.py` (8 tests) | the boot side, and two checks on the shell's session: `hyprland.lua`'s `background_color` is `0xff` plus Theme `bg`, and the greetd config starts Hyprland through `start-hyprland` as `user` |
| `tests/desktop/run.sh` | the real bar, agentd and the Claude Code CLI against a scripted API (`fake_api.py`) in headless sway inside Docker, driven by keystrokes (`wtype`) and `bombadil pill`, with a PASS or FAIL per check. It asserts agentd's events (`turn_start` under 200 ms after Enter, a step's `risk` and `command`, `queued`, `stopped`, the launcher's local answers, a streamed card), the stone's colour and the picture's glass pixels in screenshots, the drawer's window and focus, the desk's state through `quickshell ipc`, the wallpaper's pixels (a first path with no restart, the dim, the last non-comment line, the fallbacks, one warning in the log) and no QML errors in the log. It saves screenshots of "On it", the Tab ghost, the exact-word hint, the chips and the 12 s fade (for example `02-on-it-150ms` and `12-queued-chip`) and asserts nothing about what they show. Results land in `out/desktop` (override with `OUT=`; `run.sh` empties that folder first): the screenshots, `driver.log` (one `PASS` or `FAIL` line per check, ending with `DONE pass=N fail=M`), `quickshell.log`, `events.jsonl` and `results.json`. `run.sh` exits non-zero unless the log says `fail=0` |
| `iso/airootfs/usr/local/bin/bombadil-smoke` | in a booted ISO, among others: `quickshell-running`, `hypr-config-ok`, `bar-layer`, `virtual-display-size`, `super-tap-then-launcher`, `alt-space-then-launcher`, `super-escape-stops`, `details-has-keyboard`, `open-unit-has-keyboard`, `pill-esc-closes-details` and the `signin-*` group |

```sh
python3 -m pip install -e '.[apps,dev]'
pytest -q tests/test_pill_qml.py tests/test_stone_qml.py tests/test_theme.py tests/test_wallpaper.py tests/test_iso_profile.py tests/test_boot_console.py
pytest -q tests/test_pill_qml.py -k "stone"
BOMBADIL_SCREENS=/tmp/pill pytest -q tests/test_pill_qml.py     # also saves a picture of each state
CLAUDE_BIN=/path/to/claude tests/desktop/run.sh                 # needs Docker and a self-contained claude binary; output in out/desktop
```

The QML tests skip without PySide6. `tests/conftest.py` puts `share/qml` on `QML2_IMPORT_PATH` for every offscreen test, as `bin/bombadil-shell` does for the real bar, so a new offscreen harness needs no import path of its own. No pytest test loads `shell.qml` itself (`tests/test_theme.py` and `tests/test_wallpaper.py` read it as text). Its focus rules, input mask, reconnection, app chips and `HyprCover` execute only in `tests/desktop` (without Hyprland, so `HyprCover` is inactive there and the `Exclusive` branch of the focus rule is the one that runs; agentd is started before the bar and nothing restarts it) and in the VM smoke, but no check in either asserts the mask, reconnection, the app chips or `HyprCover`'s Hyprland path. The focus grab is exercised only by the smoke's Super and Alt+Space steps. [development.md](../contributing/development.md) describes the layers of tests and the VM tools.

## Known gaps

- The socket path ignores `BOMBADIL_RUNTIME` and has no fallback for an unset `XDG_RUNTIME_DIR` (`shell/shell.qml:90`); agentd honours both (`src/bombadil/paths.py:16-22`: `runtime_dir` takes `BOMBADIL_RUNTIME`, else `XDG_RUNTIME_DIR` with a `/run/user/<uid>` fallback, and `socket_path` takes `BOMBADIL_SOCKET`). With only `BOMBADIL_RUNTIME` set, the two sides look in different places.
- The launcher mirror is smaller than the launcher. `PillState._verbs` has seven verbs and does not drop "the" (`shell/PillState.qml:380`); `launcher.py` accepts `run`, `bring up`, `go to`, `switch to`, `exit`, `kill` and `put away` as well and drops "the" (`src/bombadil/launcher.py:92-94, 130-135`). "open the browser" opens the browser but shows no `↵ Browser` hint and no completion.
- The bar's exclusive zone is the literal 64 (`shell/shell.qml:204`) and the desk's `rowZone` is another literal 64 (`shell/DeskState.qml:157`). Neither is derived from `Theme.pillHeight` (52) and the 12 px margin; changing the pill's height needs both edited.
- A local error line that has no turn does not set `fadeAfter` (`shell/PillState.qml:191`), so it stays as long as the previous line's setting, 15 s after an undo line.
- Reduced motion reaches only the stone (not its lean) and the wallpaper's fade-in and the fade of its dim (`shell/Stone.qml`, `shell/Wallpaper.qml:96, 108`): the line's, the picture's and the pill border's fades and the desk's motion ignore it. `BOMBADIL_REDUCE_MOTION` is set by nothing in the repository (`shell/shell.qml:24`).
- The Needs you rows come from the `dev` message, and agentd on `main` neither handles nor sends it (`src/bombadil/agentd.py`, `handle`). `DeskState` sets `PillState.needsYou` through a `Binding` while a row exists (`shell/DeskState.qml:122, 270-278`), so the `needs` face for a waiting coding session works, but only `desk inject`, `tests/test_desk_qml.py` and `tests/desktop` produce rows; on a real session the `needs` face comes only from setup. The Open button sends `{"type": "dev", ...}` (`shell/DeskState.qml:23`) and nothing answers it.
- Designed in the briefs and absent from `shell/`: the "this" chip, example chips and the first-day hint, a notification line with a mark on the stone, battery and music beside the clock, drop and paste onto the pill, voice input (push to talk). The pill's row has the stone, the field, the hint and the clock. `hyprland.lua` still starts `mako`. Bombadil's own words (the greeting lines, a name and voice card) are in progress on a branch that is not on `main` ([voice.md](voice.md)).
- Nothing supervises the bar or agentd on `main`; user units that restart them are in progress ([the roadmap](../roadmap.md#in-progress)). `hyprland.start` runs `agentd` and `bombadil-shell` once (`hyprland.lua:7-12`), and no unit restarts either (the only `Restart=` lines are the brain's two units, `Restart=on-failure`, in `iso/airootfs/etc/systemd/user/bombadil-brain.service` and `iso/airootfs/etc/systemd/system/bombadil-brain-watch.service`). A dead bar stays dead until `Super+Ctrl+Escape`. A dead agentd leaves the pill saying "Waiting for agentd…" (`shell/shell.qml:433`) until someone starts one: the bar reconnects to a returning agentd (`shell/shell.qml:103-109`) but cannot bring it back, and `bombadil-setup` starts one only when no agentd answers ([agentd.md](agentd.md#starting-it)).
- `cardHost`, `statusLine`, `chips` (the queue) and `appChips` hide themselves, yet their entries in the input mask have no `visible` guard (`shell/shell.qml:207-211`), while `setupChips` has one because a hidden item keeps its last place (`:209`). Whether the unguarded rows leave a dead input area above the pill was not tested: no test loads `shell.qml`'s mask.
- `openThing` hands the keyboard off only for a box that names a turn (`shell/PillState.qml:122-125`). For a file, a service or a package the drawer is opened by agentd and the pill keeps the keyboard if it held it. No test covers a click in a picture while the pill is summoned.
- A `wallpaper` file that does not exist when the bar starts is not watched, so on a home without the skeleton's copy (a system made before the skeleton shipped it) a first file needs the bar started again (the comment in `shell/Wallpaper.qml:13-17`). The desktop test starts from the shipped file and does not try the missing one.

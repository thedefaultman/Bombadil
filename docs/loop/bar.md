# The bar's part of the loop

The "noticed" chip beside the pill, the card that rises from it, and what the bar tells agentd about
itself. Contract: `docs/LOOP.md`. Files: `shell/LoopState.qml`, `NoticedChip.qml`, `NoticedCard.qml`,
a small wiring in `shell/shell.qml`, `undo_msg` in `PillState.qml` and `StatusLine.qml`, two variants
(`primary`, `quiet`) in `LineButton.qml`. Tests: `tests/test_loop_qml.py`, `tests/test_loop_fixes_bar_qml.py`.

## What they see

Nothing until agentd says something waits. Then a small muted chip, "noticed 2", right of the pill and
centred on it (no dot, no colour, no motion). Hover it (after 250 ms) and a 300 px card rises above it:
"Noticed", one line of why ("1 idea · 1 change to look at"), at most three rows, then "Resting offers
until 3 Nov" when resting and "Lately: 2 changes this week · See all". A row is the offer: their own words,
"4 times on 3 days", one line of what would happen, one button (pressing it is the ask; there is no
second question), a quiet "Other ways" that opens the other forms as small choices, and quiet "Not now" /
"Never" at the right of the meta line. Click the chip: the card stays up and agentd opens the Noticed
window; click again, press Esc, or click elsewhere and it goes. Nothing is shown during a turn, and the
chip never turns up on its own while the pill has the keyboard. When the chip is beside the pill the card
rises above everything the bar draws in the pill's column (the status line with its Undo and Details, the
chips), so it never covers them. If agentd goes away the card goes with the chip: there is nobody to answer
a button, and a card with dead buttons would say nothing.

## LoopState (plain QtQuick, no Quickshell types)

Feed it with `handle(ev)`; it speaks through `outgoing(msg)` (shell.qml writes that to the socket).

| handles | does |
|---|---|
| `noticed` | sets `count`, `hidden`, `resting`, `lately`, `rows`; clears `pending`; closes the card when `hidden` turns on or the last row is gone |
| `noticed_open` | `kept = true`, emits `opened()` (shell.qml picks the screen and takes the keyboard) |
| `noticed_result` | `result = {op, id, ok, text, preview}`: a preview or a failure shows under its row. The answer to `report` and `send` carries no preview here: the Noticed window shows the whole report, and a card with it would be taller than the screen. A long preview is clipped to six lines |
| `summon` | remembers `id` and the time, for `focus_ack` (or `focus_cancel`) |

Properties: `count`, `hidden`, `rows`, `resting`, `lately`, `result`, `pending` (row id a tap waits on),
`connected`, `busy`, `typing`, `drawer` (set by shell.qml from `root.connected`, `pillState.stoppable`,
`root.summonedOn !== ""`, `pillState.drawerUp`), `pid`, `build`, `chipVisible` (readonly: count > 0, not
hidden, not busy, connected; turns on only while not `typing`, stays on once on), `peeked` (hover),
`kept` (click or "noticed"), `cardOpen` (readonly: either), `cardScreen` (the card shows on that
screen's window only), `focusedRow` (id of the row whose Other ways are open), `shownRows` (first three),
timing knobs `peekDelay` (250), `leaveDelay` (300), `aliveMs` (5000), `focusWaitMs` (3000) and
`fixedNow` (epoch ms; tests set it, -1 is the real clock, `_now()` reads it).

Functions the window calls: `hoverChip(screen, on)`, `hoverCard(screen, on)`, `chipClicked(screen)`,
`closeCard()`, `keyboardLost(screen)`, `esc()` (true when it put a kept card away), `inputFocused()`,
`summonCancelled()`, `reportRects(screen, w, h, dy, [[name, item], ...])`, `hello()`, `openWindow()`.

Pure functions for rows: `primary(row)` -> `{label, op, form?}` (the row's own, else by kind: "Make it"
accept, "Use it" accept, "Send to the project" report); `others(row)` -> at most two `{label, op, form?}`
(the row's `others`, else its `forms` without the recommended one and the primary's); `quiet(row)` ->
Not now + Never, only Not now for a `change`, none for a `got_it` primary; `why()`, `latelyLine()`,
`restingLine()` (accepts "3 Nov", an ISO date, epoch seconds, or a whole "Resting offers until ..." line).
Actions: `press(row)`, `choose(row, way)`, `notNow(row)`, `never(row)`, `toggleWays(row)`; each sends
`{type:"noticed_do", op, id, form?}` and only once per row until agentd answers (`noticed` or
`noticed_result`). `report` and `send` also put the card away: the Noticed window takes over.

## What the bar sends

- `hello {client:"bar", pid, build}` on connect (from a zero timer: the Socket may not be stored yet),
  then `noticed_state`, then every window reports its rectangles. `build` is `BOMBADIL_BUILD` or `""`;
  `pid` is `Quickshell.processId` or 0.
- `alive {t}` every 5 s while connected; `t` is epoch seconds.
- `rects {screen, w, h, rects:[{name,x,y,w,h}]}` per screen in the screen's own logical pixels (the layer
  sits on the bottom edge, so `y` includes the window's offset). Names: `statusLine`, `chips`, `pillBox`,
  `noticedChip`, `noticedCard`; only what is showing. Sent 250 ms after the layout settles, only when it
  differs from what agentd last heard, and again after a reconnect.
- `focus_ack {id, ms}` when a summon arrived and the input then has the keyboard within 3 s. A summon that
  never gets the keyboard sends nothing, and agentd writes it up when the time is out.
- `focus_cancel {id}` when a summon is answered by giving the keyboard back: a second tap on Super toggles
  the pill off, so no ack is coming and none was meant to. shell.qml's `summon()` calls
  `loopState.summonCancelled()` when the tap leaves `summonedOn` empty; it says it once, for the summon
  that was just answered, and an earlier summon that never got the keyboard stays unanswered.
- `friction {what:"esc", count:3, seconds:10, drawer, card}` once per burst: a burst starts with an Esc
  while a drawer (as far as the bar knows) or the card is up, and every Esc within 10 s of the last belongs
  to it. `drawer` and `card` say what was up when the burst began (a kept card is put away by the first
  press, so asking at the end would always say "nothing").
- `noticed_do {op, id?, form?}`: `open` (chip click, See all), `accept`, `not_now`, `never`, `report`, or
  whatever a row's own buttons say.

## The line's own Undo

A local event may carry `undo_msg`. The line then shows the receipt with Undo (and no Details), it stays
until the next prompt like any change, and Undo sends `undo_msg` instead of the machine's undo
(`PillState.undoMsg`; any new line clears it). Pressing Undo takes the button away at once. If agentd
cannot do it, it answers with a local line that says so in one plain sentence (`ok: false`, with no Undo of
its own), and the line shows that instead of the receipt.

## Traps

- Only Hyprland's summon path counts as "the pill has the keyboard" (`summonedOn`); on other compositors
  the chip may appear while they type.
- A kept card lasts while the pill has the keyboard: a click elsewhere clears the focus grab and puts the
  card away. If opening the Noticed window takes Hyprland's focus and clears the grab, the card goes at
  once; that is checked only on a real Hyprland.
- Esc is only seen while the pill has the keyboard, so the details drawer (its own window) never reports
  its Esc; `drawerUp` is a guess from Details and close_details. Expect a burst of three Esc right after a
  put-away to count; the probe wants three sightings on two days anyway.
- When the desk lands, its strips also sit right of the pill: bind `NoticedChip.taken` to their width plus
  the gap after them (the chip moves past them, or above the pill when that leaves no room).
- Hovering grows the layer for the card (exclusive zone stays 64). The chip and card are zero-sized while
  hidden so the input mask takes no room for them.
- Rows from JSON are real arrays; a test's variant lists are not, so lists go through `_list()`.
- QML tests need libEGL (they skip without it, like `test_pill_qml.py`).

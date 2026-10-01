# The Noticed window

A kit app. It opens from the bar chip (`noticed_do open`), or when he says "noticed". Contract: `docs/LOOP.md`. Files: `share/apps/noticed/` (`app.py`, `main.qml`,
`text.js`, one QML file per section, `SendCard.qml`, `Line.qml`, `Section.qml`, `Well.qml`,
`FootLink.qml`). Tests: `tests/test_noticed_app.py`.

## What he sees

A header line in plain words ("3 ideas · 1 thing it found", "Nothing waiting", "Hidden"), then five
sections. A section with nothing in it is hidden; when all five are empty the first says so once.

- **What you ask most**: his own words, "4 times on 3 days", what it became. Where an offer waits, one
  button (the row's own, else "Make it") and "Other ways", which opens the other forms, "Show me", "Not
  now" and "Never".
- **Changed itself**: newest first, each with its sentence, when, and Undo. An undone one says so and
  offers "Put it back".
- **Found**: a plain sentence, the count, "Why?" (only when agentd sent the evidence) and "Send to the
  project". That opens the Send card inside the window: what goes, what stays here, the exact text, then
  "Open the issue page", "Not now", "Never for this". Opening the page puts the text in its address, so
  GitHub gets it then; nothing is posted until he presses Submit on the page.
- **You said no to**: each with "Bring back".
- **Words**: the words he made, what each opens, "Bring back" on a put-away one. There is no "Put away":
  the contract has no op for it (see followups in the build notes).
- A footer: muted "Forget what I ask" and "Clear what it found" (each asks once; what he said no to
  stays said), and "Hide noticed" / "Show noticed".

No ids on screen. The `objectName`s the tests use carry them (`btn:undo:<id>`), because an id is what
`noticed_do` needs.

## How it talks

`Backend` in `app.py` owns one `QLocalSocket` to agentd (`paths.socket_path()`), newline-delimited JSON,
the way the kit's `Agent` does. No thread, nothing to stop on a hot reload.

| sends | when |
|---|---|
| `noticed_list` | on connect, after any `noticed_result`, after a `noticed` or `noticed_open`, and every few seconds while no list has come |
| `noticed_do {op, id?, form?}` | a button; `id` and `form` are left out when empty |

| hears | does |
|---|---|
| `noticed_full` | cleans it (`clean_full`), sets `full` when it changed, emits `listed` |
| `noticed_result` | settles the tap that waits on it, emits `answered(op, id, ok, text, preview)`, asks again |
| `noticed` | asks again, unless it is an echo (below) |
| `noticed_open` | asks again |

Ops the window sends: `accept`, `other_ways`, `preview`, `not_now`, `never`, `got_it`, `report`, `send`,
`undo`, `bring_back`, `forget_asks`, `clear_found`, `hide`, `show`. `bring_back` carries a change id, a
said-no id or a word's phrase (a word has no id).

`Backend` API for QML: properties `connected`, `ready` (connected and a list has come), `full`,
`pending` (keys of taps waiting: the row id, or `op:<op>` for ones with no id); slots `act(op, id)`,
`actForm(op, id, form)`, `refresh()`; signals `answered`, `listed`. Tunables for tests: `tick_ms`,
`ask_after_ms`, `ask_gap_ms`, `echo_s`, `answer_s`.

## Rules the code keeps

- **Never loops.** Asking is coalesced: one `noticed_list` per `ask_after_ms`, never within `ask_gap_ms`
  of the last. A `noticed` that arrives within `echo_s` of our own request and says the same thing as the
  last one is the answer to our own asking, so it is not asked about again. A burst of 60 `noticed`
  messages costs at most two requests.
- **Never stuck.** A tap with no answer is let go after `answer_s` with "Bombadil did not answer." A
  second tap on a row that still waits is ignored, so nothing is sent twice.
- **Survives agentd.** Not connected: the window shows "Bombadil is not listening right now." and tries
  again every `tick_ms` (3 s). A tap while away says so and sends nothing. Reconnects ask again.
- **Survives bad messages.** Every field is read through a cleaner (`_text`, `_int`, `_rows`, ...). A
  missing field is an empty one, a bad row is dropped, at most 50 rows a list, and a bad line costs one
  log line a minute. A partial line over 16 MB is dropped.
- **A failed tap shows where it was made**: under its row (`answer:<id>`), in the warning colour. A failed tap with no row becomes a toast. A `send` that worked closes the card.
- **Report flow.** `report` marks the window as waiting (`reporting`); the preview arrives on the found
  row in the next `noticed_full`. The Send card shows "Writing it up..." until then, and after 4 s
  says "Nothing written up yet." instead. A held `reported` row with a preview opens the card by itself on the first list,
  and "Back" sends nothing.
- **Check mode.** With `BOMBADIL_CHECK` set the backend does not connect (`bombadil-app check` must not).

## Traps

- **His words are text, never markup.** `Text` defaults to AutoText; every Text that shows a phrase, a
  title or a preview sets `Text.PlainText`. A test puts `<b>` and `<img>` in a phrase and checks
  `textFormat`.
- **Do not bind `visible` to `visibleChildren`** of the same item: it is effective visibility, so the
  binding feeds on itself and the item never shows.
- **Repeater delegates are not in the QObject tree.** `findChild` cannot see a row. The tests walk
  `childItems()` from the window (`items()`, `find(name)` in `PRELUDE`).
- **Clicks, not calls.** The tests press the real buttons with `QTest.mouseClick` on the window, so a
  button that is covered, disabled or off-screen fails the test. A row that is scrolled away is
  revealed first.
- **`app.py` is exec'd, not imported.** No relative imports; `from bombadil import paths` works.
- **Mutating a shared dict.** `FORMS` is read only; copy before changing.
- **The file list test.** `test_the_window_loads_clean_in_a_check_and_does_not_connect` also lists the
  exact files in the app folder. Add a file, add its name there.
- **Rows the service must drop.** On `not_now` or `never` for a found row, the service must drop the
  `reported` state and the preview. Otherwise the held report opens the Send card again at the next
  window open.

## Tests

`tests/test_noticed_app.py` skips itself when `share/qml/Bombadil/qmldir` is absent. Each scenario runs in
a child process (QML state does not leak between them) with the real `main.qml` under `runtime.Host`,
offscreen, and a fake agentd on a Unix socket in a temp home. The fake applies each `noticed_do` to its
own lists, so the window's second `noticed_list` sees the change. Nothing opens a browser or touches the
network. Set `NOTICED_SHOTS=<dir>` to write a screenshot of each state there (not a pass condition).

What this cannot check, and needs a real Hyprland or a real agentd for: the slide-in and where the
window lands, focus when it opens from the chip, the wording of real result texts, and the Inter
font (the test machine has none, so text is a little wider there than on his).

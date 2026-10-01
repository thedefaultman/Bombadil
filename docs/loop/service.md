# The service's part of the loop

What agentd runs beside its turns to turn the counts and the findings into "noticed": the state the
bar's chip shows, what his taps do, the words it makes, the app turn, and the trail of what changed.
Contract: `docs/LOOP.md` (messages, "Service rules"). Files: `src/bombadil/loop/service.py`, a small
wiring in `src/bombadil/agentd.py`, three small additions to `loop/store.py`. Tests:
`tests/test_loop_service.py`.

The service is a guest in agentd. Nothing it does is on a turn's path: it never makes a turn wait, never
touches the pill, and one broken step costs one line on stderr (once per kind of trouble) and never a
turn, a client or agentd.

## What it does

- Reads `turns.jsonl` into the counts (`LoopStore.ingest`): when agentd starts, about 2 s after the last
  of a burst of rows, and at least every 10 minutes.
- Keeps the `noticed` message (count, hidden, resting, lately, at most three rows) and sends it to a client
  when it connects or asks, and to everyone when it changes (compared as JSON, so nothing is said twice).
  While a client is connected it reads the prober's findings from loop.db about every 20 s.
- Fetches an offer (which records it as shown) only while no turn runs, nothing is queued and the bar
  has said hello. An offer already showing stays in the state whatever he does.
- Answers every `noticed_do` with `noticed_result` to the sender, then a fresh `noticed`.
- Makes a word (a row in `words.toml`, no model) or asks one ordinary turn for an app, and writes each
  change into the trail, so the window can list it and Undo can take it back.
- Notes every use of a word at once (and the ingest reads the same row later; a use is counted once),
  and once a day puts away the words nobody said for 28 days.
- Runs the optional model step (`refine.py`) for the group about to be offered, only when his config
  turns it on.

## Threads

One worker thread (a one-thread executor, `bombadil-loop`) owns the `LoopStore` and the `FindingsStore`
connections; an SQLite connection belongs to the thread that made it, and the event loop never waits on
SQLite. Every database touch is a `_w_*` method run there with `run_in_executor`. Slow things that are not
the database run in other threads (`asyncio.to_thread`): the issue search, opening the issue page, moving
an app's folder, listing the apps, reading a big turns.jsonl for the trail. The one slow thing the worker
itself does is the optional model step (its own 30 s timeout, holding the other database work behind it),
so that step runs only at a moment an offer could be made, never while a turn runs.

The hooks agentd calls (`on_row`, `on_client`, `on_message`) only schedule work. `on_row` is called from
inside agentd's ledger writer, so it never blocks.

## API

```python
LoopService(agentd, *, loop_dir=None, clock=time.time, opener=None, fetcher=None)
```

- `clock()` gives epoch seconds for every rule here; tests move it. `loop_dir` defaults to
  `paths.loop_dir()` (it only places the trash). `opener(url)` opens the issue page (default
  `report.open_issue_page`), `fetcher(url, timeout)` searches the project's issues (default
  `report.already_reported`'s own). A test passes both; nothing in the suite touches the network, a
  browser or the clipboard (a fixture traps them).
- `start()` (on the running event loop: opens the stores, reads the trail, catches up, starts the clock),
  `stop()` (cancels tasks, closes the stores; safe twice, safe after the loop is closed).
- Hooks: `on_row(entry)`, `async on_client(writer)`, `async on_message(msg, writer)`.
- `async noticed_word(verb) -> (ok, line)`: the launcher word. `"hide"` holds offers and hides the chip;
  anything else shows it again, broadcasts `noticed_open` and opens the Noticed app when one is installed
  (quietly not when it is not).
- `async tick()`: what is due at `clock()` (the service's own timer calls it every 5 s; tests call it).
- `debounce` (seconds, default 2.0).

### What agentd does for it (`agentd.py`)

- `AgentD(..., loop_service=True)` makes a `LoopService(self, prober=start_prober)` (`prober` starts `bombadil-probe.service` through the user's systemd, once, off the event loop) when the socket listens and starts it;
  `main()` passes it. Left off, or given a `d.loop` of the tests' own, agentd does what it always did (a
  loop that has no `start`/`stop`/`noticed_word` is fine). A service that cannot start costs one line and
  agentd runs without one.
- `serve()` stops it on the way out.
- `_run_action(action)`: a launcher action of kind `"noticed"` goes to `loop.noticed_word(action.verb)`;
  without a service the launcher's own answer stands ("Noticed is not running."). The ledger row of the
  word keeps `verb` ("open" or "hide") for kind `noticed`, as it does for apps and panels.

### Messages (beyond `docs/LOOP.md`)

- `noticed_do` ops and what each answers in `noticed_result.text`:

| op | does | `text` (ok) |
|---|---|---|
| `open` | opens the Noticed app if it is here | "Opened the Noticed window." (or empty) |
| `accept` `form:"word"` | a row in words.toml, trail row, receipt to the sender with `undo_msg` | "Made “my passwords” open Passwords." |
| `accept` `form:"app"` | one ordinary turn (origin `loop`) in his own words; the group is "made" | "Making a small app for it now. It will say what it made when it is done." |
| `not_now` `never` `got_it` | answers the offer (or dismisses / never a finding) | "Okay. That will not come up again for a while." / "Okay. That will not be offered again." / "Okay, nothing to make." |
| `other_ways` | nothing to do (the bar shows the other forms itself) | "Those are the other ways it could be done." |
| `preview` | `forms.preview` for a group (a string), the report's preview for a finding | "This is what it would do." + `preview` |
| `report` | builds and holds the report, marks it `reported` | "The report is ready. Opening the issue page sends it to GitHub as part of the address; nothing is posted until you press Submit on the page." + `preview` `{text, goes, stays}` |
| `send` | (after `report`) searches the project's issues for an open one with the fingerprint, opens the prefilled page with the report held at `report` (not one built again), marks it `sent` | "The issue page is open with the report filled in. Press Submit there if it looks right." |
| `undo` | takes a trail row back: a word out, an app into the trash, a put-away word back | "Took out the word “…”." / "Put the app … away." / "Brought back the word “…”." |
| `bring_back` | a put-away word, a group he said no to, or an app that went to the trash | "Okay. That can come up again." / "Brought the app … back." |
| `forget_asks` | empties the counts (and the model step's pinned answers); words stay | "Forgot what you asked. The words made from it stay." |
| `clear_found` | drops findings and held reports he has not said no to | "Cleared what it found. A problem that is still there will be found again." |
| `hide` `show` | as the words do | "Noticed is hidden. Say “show noticed” to bring it back." / "Noticed is back." |

  A failure is `ok:false` with one plain sentence; an unknown op is "Noticed cannot do “…”." and never
  raises. `id` is always read as text.
- A receipt (`{"type":"event","kind":"local","turn":null,"action":"noticed","phase":"done","ok":…,
  "text":…,"undo_msg":{"type":"noticed_do","op":"undo","id":"i…"}}`) goes to the client that tapped, not to
  everyone. It is not a ledger row; the trail row is.
- `noticed_full` is as `docs/LOOP.md` says. `changes` come from the trail (each change once, newest first;
  `undone` says whether the latest row that answers it undid it; `can_undo` only for a word, an app, or a
  put-away word).

### The trail: `improve` rows in turns.jsonl

```
{"t", "kind": "improve", "id": "i<epoch ms>-<n>", "what": "word"|"app", "title", "group": "gpw1"|null,
 "undo": {"op": "remove_word"|"trash_app"|"bring_back_word", ...}|null, "undone": false, "v": 2}
```

A row that answers another carries `of` (its id) and `undone`; an undone app also carries `trash` (where
the folder went) and `name`. Only agentd's own writer appends to turns.jsonl (`_log_line`, which calls
`on_row` back: the service adds the row to its trail there and says nothing, because whoever wrote it
ends by publishing the new state). The ledger, the counts and `LoopStore.ingest` skip `improve` rows (and
every kind they do not know); a test proves it. At start the service reads the ones already in the file
(the last 500) off the event loop. One line that is not JSON costs that line.

An undo never moves a folder the trail does not name: an app name must be a valid name, and what is moved
back must be inside `loop_dir()/trash`.

## State rules

- `rows` are the waiting offer, then what was found and not yet reported, then held reports; at most
  three are sent, `count` is all of them. Not now and Never are the bar's own quiet buttons, so a row's
  `others` holds only other ways to do it.
- The button says "Make the word" (form A), "Make an app" (form D), "Got it" (when he already has a way);
  anything else keeps `offers.BUTTONS`.
- Hidden (`hide noticed`) is kept in loop.db (`service_state`) and survives a restart; counting and
  checking go on, nothing is offered. A new offer is never fetched while hidden.
- `lately` is "N changes this week" from the trail, counting only what still stands.
- `resting` is `LoopStore.resting(now)`.
- An offer is fetched only when `_idle()`: no turn, nothing queued, a bar connected. The bar's own `hello`
  does not reach the service; the `noticed_state` it sends right after does, and so does the poll every
  20 s, so an offer shows up then and not before.

## An app (form D)

The tap starts an ordinary turn through `agentd.handle` (origin `loop`, so it is never counted as an
ask): his own sentences (up to three), how often he asked, and "please make me a small Bombadil app for
it, using the app kit". It is his turn to read and Stop and undo like any other. The group is "made" at
once; when the turn's row says it did not go well (failed or stopped) the group goes back to "not now".
When it did, the apps folder is compared with what it was at the tap, and each new app becomes a trail
row with a Trash undo. Undo closes the app's window (through the launcher) and moves the folder into
`loop_dir()/trash/<name>-<time>`; Bring back moves it back when its name is free.

## Words

Making one goes through `words.add` (which refuses what the launcher already means; that answers the
offer "Got it"), `store.note_word_made`, and a trail row with `remove_word`. Undo takes the word out and
answers the group "not now". A word's use is noted from its ledger row (`kind: "local"`, `via: "word"`);
`LoopStore.note_word_used` counts only a use newer than the last, so the later ingest of the same row
counts nothing twice. Once a day (and once a day across restarts: the time is in loop.db) every word
unused for 28 days (`words.words_unused` over `LoopStore.words_last_used`) is put away with a trail row
whose undo brings it back.

## The model step

`refine.py`'s one optional call, off unless `[refine] enabled = true`. It runs on the worker thread,
only at the moment an offer could be made (idle, a bar), for the group `LoopStore.peek_offer` says is
next, with a 30 s timeout. The answer only renames the group (`rename_group`) or, when the model says
fewer than the required asks are really one request, holds it back as "not now". It never adds a member.
A failure is one stderr line and the offer is made as counted.

## Additions to `loop/store.py` (additive, tested in `tests/test_loop_service.py`)

- `peek_offer(now) -> (Group, Recommendation) | None`: what `ripe_offer` would make a new offer of,
  recording nothing; None while an offer waits or nothing is ripe.
- `rename_group(group_id, label) -> bool`: another label for a group; counts, members and state stay.
  (A later ingest that changes the group may compute its label again.)
- `words_last_used() -> {phrase: last_used}`.

## Traps

- The service is created only by `AgentD(loop_service=True)` (or a test setting `d.loop`), so every other
  test of agentd sees exactly the messages it always did.
- A `noticed` message carries `lately` from the clock, so it is compared as JSON and sent again only
  when it differs; the sender of a tap always gets one after the result.
- A tap on an offer that is not recorded any more is "That is not on the list any more." and changes
  nothing; two taps at once on the same offer make one word.
- Nothing of his prompts goes into a stderr line: a line has the step's name and the kind of error
  (plus the SQLite message for a database error), never the error's own text. A found row, a report and
  the issue link are built from findings evidence, which holds none of his words. His own sentences are
  in the offer row he is meant to see, in the window's list of what he asked, in the prompt of the app
  turn (his own turn), and in a change's title for an app ("Made the app X from “…”").
- `loop.db` that cannot be opened: one line on stderr; the state stays empty, every tap answers "Noticed
  cannot look at its notes right now, so nothing was changed.", and the open is tried again every 10
  minutes.
- A test that calls the real `report.open_issue_page`, the issue search or `wl-copy` fails at its end (the
  fixture in `test_loop_service.py` records the calls): pass an `opener`, a `fetcher`, or patch `copy_text`.

## What needs a real machine

Everything above is tested with a double of agentd, and with a real `AgentD` over its socket and the fake
provider. Not checked anywhere yet:

- Real Hyprland: "open noticed" raising the Noticed window, and an app's window closing when its change is
  undone (both go through `launcher.run`, faked in the tests).
- The browser panel and GitHub: `report.open_issue_page` (Chromium's debugging port, `xdg-open`), the issue
  search (`already_reported`), and `wl-copy` for a report too long for a link.
- A real model: what an app turn makes from the prompt the service writes (the wording, the app kit, how
  long it takes), and the refine step against the real claude and codex commands (see `refine.py`).
- His real ledger: which groups form, what is offered when, and what an undone or hidden state feels like
  over days. The cadence (10 minutes, 20 s, daily) is tested with an injected clock, not over days.
- The bar's QML with this service: the tests use a fake client that speaks the same messages.
- The prober and the service writing loop.db at the same moment in two processes (WAL, the busy timeout
  and a retry for a table the other one just made are in place; only reasoned, and the retry is unit-tested).

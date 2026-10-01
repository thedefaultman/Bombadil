# The service's part of the loop

What agentd runs beside its turns to turn the counts and the findings into "noticed": the state the
bar's chip shows, what their taps do, the words it makes, the app turn, and the trail of what changed.
Contract: `docs/LOOP.md` (messages, "Service rules"). Files: `src/bombadil/loop/service.py`, a small
wiring in `src/bombadil/agentd.py`, three small additions to `loop/store.py`. Tests:
`tests/test_loop_service.py` and `tests/test_loop_fixes_service.py`.

The service is a guest in agentd. Nothing it does is on a turn's path: it never makes a turn wait, never
touches the pill, and one broken step costs one line on stderr (once per kind of trouble) and never a
turn, a client or agentd.

## What it does

- Reads `turns.jsonl` into the counts (`LoopStore.ingest`): when agentd starts, about 2 s after the last
  of a burst of rows, and at least every 10 minutes. A long backlog is counted 150 rows at a time (see
  Threads).
- Keeps the `noticed` message (count, hidden, resting, lately, at most three rows) and sends it to a client
  when it connects or asks, and to everyone when it changes (compared as JSON, so nothing is said twice).
  While a client is connected it reads the prober's findings from loop.db about every 20 s.
- Fetches an offer (which records it as shown) only while no turn runs, nothing is queued and the bar
  has said hello. An offer already showing stays in the state whatever they do.
- Answers every `noticed_do` with `noticed_result` to the sender, then a fresh `noticed`.
- Makes a word (a row in `words.toml`, no model) or asks one ordinary turn for an app, and writes each
  change into the trail, so the window can list it and Undo can take it back.
- Notes every use of a word at once (and the ingest reads the same row later; a use is counted once),
  and once a day puts away the words nobody said for 28 days.
- Runs the optional model step (`refine.py`) for the group about to be offered, only when their config
  turns it on.

## Threads

One worker thread (a one-thread executor, `bombadil-loop`) owns the `LoopStore` and the `FindingsStore`
connections; an SQLite connection belongs to the thread that made it, and the event loop never waits on
SQLite. Every database touch is a `_w_*` method run there with `run_in_executor`. Slow things that are not
the database run in other threads (`asyncio.to_thread`): the issue search, opening the issue page, moving
an app's folder, listing the apps, reading a big turns.jsonl for the trail. The one slow thing the worker
itself does is the optional model step (its own 30 s timeout, holding the other database work behind it),
so that step runs only at a moment an offer could be made, never while a turn runs.

A look at `turns.jsonl` is not one long job. `_ingest_now` submits one chunk (`LoopStore.ingest(limit=150)`,
its own transaction, the byte offset saved in it) as one worker job, waits for it, pauses 50 ms, and
submits the next while the store says there is more. A tap, a poll or the prober's write gets in between
the chunks; a crash resumes at the last committed chunk; and `stop()` marks the service stopped, so a
chunk still queued does nothing and agentd waits for at most the one that is running (the worker thread
is joined when the interpreter exits).

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
  anything else shows it again, broadcasts `noticed_open` and opens the Noticed window. It says "Opened
  Noticed." only when a window opened; when none could (no such app, or the launcher failed) it answers
  `ok` false with "The Noticed window would not open."

The window is the Noticed app, which ships with Bombadil (`share/apps/noticed`). The service finds it the
way an app is run: a copy of their own in `~/Apps` first, then the built-in one (`apps.load`). It does not
use `launcher.known_apps()`, which lists only their apps, so the app does not need to be copied anywhere.
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
| `open` | opens the Noticed window | "Opened the Noticed window." (`ok` false, "The Noticed window would not open.", when none opened) |
| `accept` `form:"word"` | a row in words.toml, trail row, receipt to the sender with `undo_msg` | "Made “my passwords” open Passwords." |
| `accept` `form:"app"` | one ordinary turn (origin `loop`) in their own words; the group is "made" | "Making a small app for it now. It will say what it made when it is done." |
| `not_now` `never` `got_it` | answers the offer | "Okay. That will not come up again for a while." / "Okay. That will not be offered again." / "Okay, nothing to make." |
| `not_now` `got_it` on a found problem | dismisses it: kept quiet for 30 days, or until it has happened twice as many times | "Okay. That will stay quiet for 30 days, or until it has happened twice as many times." |
| `never` on a found problem | not raised again, until they bring it back | "Okay. That will not come up again." |
| `other_ways` | nothing to do (the bar shows the other forms itself) | "Those are the other ways it could be done." |
| `preview` | `forms.preview` for a group (a string), the report's preview for a finding | "This is what it would do." + `preview` |
| `report` | builds and holds the report, marks it `reported`, opens the Noticed window | "The report is ready. Opening the issue page sends it to GitHub as part of the address; nothing is posted until you press Submit on the page." + `preview` `{text, goes, stays}` (when the window would not open, the text says so and how to look at it) |
| `send` | (after `report`) searches the project's issues for an open one with the fingerprint, opens the prefilled page with the report held at `report` (not one built again), marks it `sent`. A second `send` for the same report while one runs is refused. | "The issue page is open with the report filled in. Press Submit there if it looks right." / "That is already being sent." |
| `send`, the project has it | marks it `sent`, opens that issue instead and, when there is a clipboard, puts "Seen again: 3 times on 2 days, build …" on it for them to paste there | "Already reported (#42), so nothing new was sent. Its page is open. If you want to add that it happened again, a line for that is on the clipboard: paste it there." |
| `undo` | takes a trail row back: a word out, an app into the trash, a put-away word back | "Took out the word “…”." / "Put the app … away." / "Brought back the word “…”." |
| `bring_back` | a put-away word, a group or a found problem they said no to, an app that went to the trash, or an undone change (a word they took out is made again; a word the sweep put away is put away again) | "Okay. That can come up again." / "Okay. It is back in what Bombadil found." / "Brought the app … back." / "Put the word “…” away again." |
| `forget_asks` | empties the counts (and the model step's pinned answers, and the sentences kept with what they said no to); words stay | "Forgot what you asked. The words made from it stay." |
| `clear_found` | drops findings and held reports they have not said no to | "Cleared what it found. A problem that is still there will be found again." |
| `hide` `show` | as the words do | "Noticed is hidden. Say “show noticed” to bring it back." / "Noticed is back." |

  A failure is `ok:false` with one plain sentence; an unknown op is "Noticed cannot do “…”." and never
  raises. `id` is always read as text.
- A receipt (`{"type":"event","kind":"local","turn":null,"action":"noticed","phase":"done","ok":…,
  "text":…,"undo_msg":{"type":"noticed_do","op":"undo","id":"i…"}}`) goes to the client that tapped, not to
  everyone. It is not a ledger row; the trail row is. An `undo` that fails sends the same kind of line with
  `ok:false` and the failure's sentence (no `undo_msg`), because the status line's Undo button is gone from
  the line once pressed and the answer to the tap is not drawn there.
- `noticed_full` is as `docs/LOOP.md` says. `changes` come from the trail (each change once, newest first;
  `undone` says whether the latest row that answers it undid it; `can_undo` only for a word, an app, or a
  put-away word). `said_no` lists the groups they said Never to (`form` is the form's id, "word" or "app",
  which the window words itself) and the found problems they said Not now or Never to (`form` empty,
  `t` when they said it). A found row that was `sent` because the project already had the problem carries
  "already reported as #42" in its `meta`.

### The trail: `improve` rows in turns.jsonl

```
{"t", "kind": "improve", "id": "i<epoch ms>-<n>", "what": "word"|"app", "title", "group": "gpw1"|null,
 "undo": {"op": "remove_word"|"trash_app"|"bring_back_word", ...}|null, "undone": false, "v": 2}
```

An app turn that changed an app of theirs (and made no new one) writes a row `what: "app"` with `undo: null`: its
folder is theirs, and the turn's own Undo has the old files.

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
- The button says "Make the word" (form A), "Make an app" (form D), "Got it" (when they already have a way);
  anything else keeps `offers.BUTTONS`.
- Hidden (`hide noticed`) is kept in loop.db (`service_state`) and survives a restart; counting and
  checking go on, nothing is offered. A new offer is never fetched while hidden. `service_state` also holds
  when words were last swept, a note `app:<group>` for each app turn waiting for its row, and the number of
  the issue (`issue:<fp>`) of a problem the project already had.
- A found problem they said Not now to is not in the chip or the window's Found list, but it is counted, and
  it is under "You said no to" with Bring back; it returns by itself (see `probes.md`: twice as many times,
  or 30 days). One they said Never to stays under "You said no to" until they bring it back.
- `lately` is "N changes this week" from the trail, counting only what still stands.
- `resting` is `LoopStore.resting(now)`.
- An offer is fetched only when `_idle()`: no turn, nothing queued, a bar connected. The bar's own `hello`
  does not reach the service; the `noticed_state` it sends right after does, and so does the poll every
  20 s, so an offer shows up then and not before.

## An app (form D)

The tap starts an ordinary turn through `agentd.handle` (origin `loop`, so it is never counted as an
ask): their own sentences (up to three), how often they asked, and "please make me a small Bombadil app for
it, using the app kit". It is their turn to read and Stop and undo like any other. The group is "made" at
once, with a note in loop.db that its turn is waiting for its row. When the turn's row says it did not go
well (failed or stopped), or it went well but no app of theirs was made or changed, the group goes back to
"not now". When an app was made, or changed, the apps folder is compared with what it was at the tap (each
app's files: names, sizes and times): each new app becomes a trail row with a Trash undo, and an app
changed in place a row without one. Undo closes the app's window (through the launcher) and moves the
folder into `loop_dir()/trash/<name>-<time>`; Bring back moves it back when its name is free. Signed out,
the tap answers "Bombadil needs you to sign in before it can make an app." and starts nothing.

A turn lost with agentd (it restarted while the turn was queued or running) never gets a row. At the next
start every group whose note is still there, and that is still "made", goes back to "not now".

## Words

Making one goes through `words.add` (which refuses what the launcher already means; that answers the
offer "Got it"), `store.note_word_made`, and a trail row with `remove_word`. When the notes in loop.db
cannot be written after the word was (a locked or full database), the word is taken out again, so the tap
answers "That did not work." truthfully and a second tap can make it; nothing is left half done. A group
whose asks have no phrase to say (each longer than four words, or with a path or a number in it) is not
offered as a word at all (`forms._usable`). Undo takes the word out and answers the group "not now". A word's use is noted from its ledger row (`kind: "local"`, `via: "word"`);
`LoopStore.note_word_used` counts only a use newer than the last, so the later ingest of the same row
counts nothing twice. Once a day (and once a day across restarts: the time is in loop.db) every word
unused for 28 days (`words.words_unused` over `LoopStore.words_last_used`) is put away with a trail row
whose undo brings it back. "Put it back" on such an undone change puts the word away again (the word is
still there, so it is not made again); on a word they took out it makes the word again.

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
- Nothing of their prompts goes into a stderr line: a line has the step's name and the kind of error
  (plus the SQLite message for a database error), never the error's own text. A found row, a report and
  the issue link are built from findings evidence, which holds none of their words. Their own sentences are
  in the offer row they are meant to see, in the window's list of what they asked, in the prompt of the app
  turn (their own turn), and in a change's title for an app ("Made the app X from “…”").
- `loop.db` that cannot be opened: one line on stderr that names the file; the state stays empty, every
  tap answers "Noticed cannot look at its notes right now, so nothing was changed.", and the open is tried
  again every 10 minutes (no restart is needed). A damaged file (not a database, or its pages are broken)
  is not moved or replaced on its own, because it holds what cannot be made again: what they said no to, what
  was offered, and the mark that keeps what they forgot from being read in again. To start over, delete
  `loop.db` with its `-wal` and `-shm` (or move them away); the next try makes a new one and counts
  `turns.jsonl` again from the top, which brings back the asks they forgot and loses their Nevers. A file that
  is only busy or on a disk that is full or read-only is not damaged, and opens again by itself.
- `loop.db` and everything beside it is for its owner: the loop directory is 0700 and the files 0600
  (including ones an older version made), because the database holds their own sentences.
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
- Their real ledger: which groups form, what is offered when, and what an undone or hidden state feels like
  over days. The cadence (10 minutes, 20 s, daily) is tested with an injected clock, not over days.
- The bar's QML with this service: the tests use a fake client that speaks the same messages.
- The prober and the service writing loop.db at the same moment in two processes (WAL, the busy timeout
  and a retry for a table the other one just made are in place; only reasoned, and the retry is unit-tested).

## Known gaps

- A poll for an offer still takes a write transaction (to let time pass: expiries, Not now, the 90-day
  words), so it waits behind a chunk of a backlog; the first poll after a long quiet spell may spend about a
  second dropping the words of many old groups at once. What it costs afterwards is about 10 ms at 12,000
  groups, not nothing.
- After a restart the groups are rebuilt from the stored requests (a second or two for thousands of
  requests). It happens before the first write transaction, not inside it, but it is still on the worker.
- "Bring back" of something they said Never to regroups every stored request in one transaction
  (`LoopStore.regroup`); with thousands of requests that holds the write lock for seconds. It is chunked
  nowhere yet.
- An app turn they take out of agentd's queue (the pill's Unqueue) is not told to the service, so its ask stays
  "made" until agentd starts again; at the next start it goes back to "not now".
- The line the already-reported answer puts on the clipboard is the totals so far. The count at the time of
  sending is not kept, so it cannot say how many times the problem happened since it was reported.

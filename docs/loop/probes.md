# Probes and findings

Bombadil's checks on itself, and the place what they find is kept. A probe is a pure function over what
the machine already says; it finds a problem or it does not. The store decides when a sighting counts, and
writes it down with evidence that holds nothing of his. Format and rules: `docs/LOOP.md` ("From signal to
finding"). This page says what the code does, how each probe was red on what a real VM run found, and
where it can bite.

Files: `src/bombadil/loop/probes.py`, `findings.py`. Fixtures: `tests/fixtures/loop/probes/`.

## Probes (`loop/probes.py`)

```python
Observation(clients, monitors, layers, activewindow, configerrors, bar, events, agentd, coredumps,
            ledger, tool_results, turn_errors, groups, medians, apps, now, details_at, agentd_started)
                                      # every field optional; None is "could not get it"
Result(ok, id, component, rule, expected="", observed="", evidence={}, retry_after=None,
       kind="invariant", title="", at=None)   # ok True / False / None (not checked)
PROBES: dict[str, Probe]              # Probe(id, component, kind, title, what, needs, run)
ids() get(id) red() green() unchecked()  @probe(id, component, kind, title, what, needs)
run_probe(which, obs, retried=False) -> list[Result]      # never raises
run_all(obs, only=None, skip=(), retried=False) -> list[Result]
loads(text) -> object | None          # hyprctl's JSON, or None when it answered in prose
scrub_text(text)  strip_line(text)  window_kind(client)  write_json_atomic(path, obj)
attach_words(finding, prompts, fired=None) -> list[str]   # the one function that writes a file
```

- A probe only reads. No socket, no process, no file (except `attach_words`, into a finding's own
  evidence). Collecting the data, and when to run, are the runner's.
- **Not checked is not fine and not red.** A missing field, garbage where a list should be, a monitor
  that hyprctl did not list, all give `ok=None` with `observed = "not checked: ..."`. Nothing is ever read
  as a bug because something was missing.
- A probe that raises is a red `Result` with component `loop` and rule `probe-raised`. A probe may return a
  list of Results (one per crash, one per unanswered summon).
- `kind` is the probe's own. An `invariant` that is red carries `retry_after=0.5`; everything else
  counts on sight (`friction` when it repeats). `retried=True` is the second look and asks for no more.
- `Result.at` is when the thing happened, for facts already past (a crash, a turn, a summon), so the same
  fact read on every run is one sighting. `None` is the state of the machine now.
- Evidence holds numbers, sizes, class kinds and rule names. Never a window title, a prompt, a path
  under home, or an app's name. `window_kind` gives `bombadil-app`, `bombadil-details`, `panel` or `other`.

### What each probe checks

| Probe | Kind | Checks | Needs |
|---|---|---|---|
| `drawer-focus` | invariant | `special:details` is shown and mapped, so `bombadil-details` must be the active window; not judged for 2 s after `details_at`, nor after a summon | clients, monitors, activewindow |
| `apps-stacked` | invariant | no two floating `bombadil-app-*` windows share (workspace, position, size) | clients |
| `window-oversize` | invariant | no Bombadil window (class `bombadil-*`, or in a `special:` workspace) is larger than its monitor in logical pixels + 2 | clients, monitors |
| `window-under-bar` | invariant | no floating app on a visible workspace overlaps a bar rect by 4 px or more; screen-local coordinates; not checked when the bar is older than 30 s or sent no rects | clients, monitors, bar |
| `monitor-narrow` | invariant | every enabled monitor is 1024 logical pixels wide or more (scale and rotation handled) | monitors |
| `hypr-config` | invariant | `configerrors` is empty (paths scrubbed) | configerrors |
| `bar-layer` | invariant | the `bombadil-bar` namespace is on some layer; `{}` is not checked | layers |
| `summon-focus` | event | every `summon` has a `focus_ack` within 1.5 s (late ack and no ack are both red); `focus_timeout` with `bar: false` is nothing; not judged for 4.5 s | events |
| `agentd-ping` | invariant | a real connection to agentd's socket got a pong | agentd |
| `bar-alive` | invariant | `bar.json`'s `alive_at` is under 15 s old; not for 30 s after `agentd_started` | bar, now |
| `bar-restarts` | event | fewer than 3 `restart` rows in 600 s (one result per burst) | events |
| `coredump` | crash | no dump of agentd, the bar, Hyprland, an app, os-mcp or the browser panel in 7 days (each dump is one sighting at its own time; microseconds or seconds both read) | coredumps |
| `turn-failed` | friction | a turn with `ok` false or null that was not stopped, not a `!command`, and not signed out, a limit or offline; the error's first line is shown with every echo of his asks taken out (see below) | ledger |
| `os-tools` | event | no turn's `result`, or error event, says the OS tools did not start (the reason is kept) | ledger |
| `tool-errors` | friction | no os-mcp tool fails in 40% or more of at least 5 calls in 3 days | tool_results |
| `app-check` | friction | `create_app` does not fail its own check twice for the same missing piece | tool_results |
| `undo-soon` | friction | no undo within 60 s of the turn it undoes finishing (matched by `of`, else `of_snapshot`) | ledger |
| `stop-soon` | friction | no turn stopped within 5 s of starting | ledger |
| `rephrase` | friction | no typed ask repeated within 120 s of a failed, stopped or undone turn (shared words, or similarity 0.6) | ledger |
| `slow-turn` | friction | no turn took 3 times its group's median | ledger, groups, medians |
| `provider-drift` | drift | no turn had a stream message type the provider adapter does not know (`drift` in the row) | ledger |
| `esc-friction` | friction | no bar `friction` row `what: esc` of 3 presses or more in 10 s or less (the drawer or a card up is noted) | events |
| `app-health` | invariant | no app's status is not ok with its process gone, and no FATAL line after the log's last `---` run marker; the sentence says only that a log has a fatal line (the line goes to the evidence and, stripped, to the fingerprint) | apps |

### Red on the six the VM found, green on the fixed state

Each has a bad and a fixed fixture (`tests/fixtures/loop/probes/<name>-{bad,fixed}.json`, read into an
`Observation`). Test: `tests/test_loop_probes.py`, "the six". On the bad state exactly the listed probes
are red and nothing else in `run_all`; on the fixed state nothing is red.

| Found | Fixture | Probe | How it is red |
|---|---|---|---|
| Drawer opened without the keyboard (PR #6) | `drawer` | `drawer-focus` | `special:details` shown, `bombadil-details` mapped, `activewindow` is `{}`: "the drawer is open and the active window is none" |
| A Super tap left the pill without it (PR #7) | `super-tap` | `summon-focus` | a `summon` with no `focus_ack`, then `focus_timeout {bar: true}` |
| The finished line covered a new app (thread 13) | `line-over-app` | `window-under-bar` | a floating app's rectangle overlaps the bar's `line` rect on the active workspace |
| Every new app opened at one spot (thread 13) | `apps-stacked` | `apps-stacked` | three floating `bombadil-app-*` at the same position and size |
| QEMU's screen stuck at 640x480 (PR #7) | `monitor-640` | `monitor-narrow` and `window-oversize` | 640 wide, and a 540x660 app taller than the 480 it is on |

"The launcher smoke check passed for the wrong reason" is a hand-written report, not a probe.

## Findings (`loop/findings.py`)

```python
store = FindingsStore(conn=None)              # db.connect() when none; tables in loop.db; one thread
store.record(result, now=None, *, obs=None, log=(), turn=None, tools=(), versions=None) -> Finding | None
store.record_retry(first, again, now=None, **context) -> Finding | None
store.quarantined(now=None) -> set[str]       # probe ids to pass as run_all(skip=...)
store.release(probe_id)
store.open_findings() -> list[Finding]        # open or reported, newest first
store.all(states=None)  store.pending()       # counted in any state; friction not yet counted
store.get(fp) -> Finding | None               # counted only
store.mark(fp, state, now=None) -> Finding | None   # open | reported | sent | dismissed | never
store.said_no() -> [(Finding, when)]          # dismissed and never, newest first
store.clear_found() -> int                    # "Clear what it found"
store.add_words(fp, prompts) -> list[str]     # his words of trouble, beside a finding's probe firing
Finding(fp, component, rule, title, expected, observed, first, last, n, days, state, fixable,
        evidence, probe, kind)                # .report_only, .to_dict()
fingerprint(component, rule, line) -> str     # "<component>:<rule>:<sha1(strip_line(line))[:6]>"
fingerprint_of(result)  evidence_dir(fp) -> Path  build_bundle(fp, result, now, ...) -> dict
```

`record` and `record_retry` return the `Finding` when the sighting was new and counted, else `None`
(green, not checked, waiting for its retry, quarantined, dismissed and not yet over, the same episode,
friction not often enough). `n` and `days` count episodes and days, not runs.

### The runner's loop

```python
results = run_all(obs, skip=store.quarantined(now))
for r in results:
    if r.ok is not False:
        continue
    if r.retry_after is not None:                       # an invariant: look again
        time.sleep(r.retry_after)                       # off the event loop
        again = run_probe(r.id, fresh_obs, retried=True)
        store.record_retry(r, again, now, obs=fresh_obs, log=tail, turn=row, versions=v)
    else:
        store.record(r, now, obs=obs, log=tail, turn=row, versions=v)
```

### Rules, as coded

- **Invariant**: counts only if red again on the retry. Red, then green, is one flip. The third flip of one
  probe in 7 days quarantines it (`quarantined()` names it; it comes back by itself when the flips age out)
  and raises an event finding `loop:flaky-probe` about it. A retry that could not be checked is not a flip.
- **Event, crash, drift**: count on the first sighting. A result with `at` is one sighting per `(fp, at)`.
- **Friction**: counts at 3 sightings on 2 different days, or at once when a non-friction probe fired within
  5 minutes of any sighting (looking both ways). Until then `pending()`; a pending friction is forgotten
  after 30 days.
- **Episodes**: a state with no `at` seen again within 30 minutes of the last sighting is the same sighting,
  so an hour of the same red state is one.
- **Never** findings are not counted or raised again (`mark(fp, "open")` brings them back). **Dismissed**
  (Not now) findings are still counted (`n` and `days` go on) but not raised, until the count is twice what it
  was when he said Not now, or 30 days have passed (`SNOOZE_DAYS`): the next sighting after that, or a state
  that is still red then, opens it again and returns it. The count and the time are kept in `finding_nos`
  (one row per dismissed or never finding; a Not now with no row, from before it existed, starts its rest at
  the next sighting). `clear_found()` drops the rest with their evidence and held report; a problem still there is found again next run, an event or
  crash already read is not (its sightings stay, marked cleared).
- **Loop findings** (component `loop`: flaky probe, probe raised) are never `fixable`: the loop cannot edit
  its judges. Today nothing is `fixable`; the mender sets it.
- **Fingerprint**: `component:rule:` and the first 6 hex of the sha1 of `strip_line(first observed line)`:
  paths, times, numbers, hex and request ids removed, lower-cased. Same problem, same fingerprint, whatever
  pid or path. A probe whose sentence must not carry a line it read (`app-health`) puts that line, stripped,
  in `evidence["fp_line"]`; the fingerprint is made of it, and it is never written down.
- **Flaky probe**: the finding about it carries the versions the runner passed to `record_retry` (and no
  other evidence of the window the probe looked at), so its report names the build.

### Evidence

`evidence_dir(fp)/evidence.json` is built from typed fields, then run through a recursive scrub:
the probe id and `command` (`bombadil probe <id>`), windows without titles (class kind, workspace,
position, size), monitors, layers (namespaces and boxes), the bar's rects, the last 40 log lines, the turn
row without its words, the turn's tool events, versions, and `words`. Not in it: a screenshot, a prompt, a
title, a path under home, a token, the app's name. Secrets in the observation or the turn row are found and
removed from every string by their value as well as by shape: one of 12 characters or more wherever it is,
a shorter one only as a whole word (a typed "undo" is not in "undone"), and never from a probe's own title
and expected sentence. The loop's own rows are not secrets: a local row whose prompt is its own action
("undo", "stop") adds nothing, and the asks a provider could echo are the model turns only. `attach_words` keeps only the trouble words
(still, won't, stuck, broken, frozen, again, not working, doesn't work, nothing happens), never the sentence,
and only from prompts within 5 minutes of a probe firing.

## Traps

- **Nothing here may touch a turn.** Nothing raises; a full disk or locked database is one stderr line per
  distinct trouble and the call returns as if nothing was seen. `FindingsStore()` itself can raise when
  `loop.db` cannot be opened: build it inside the runner's own guard.
- **`activewindow` and layer surfaces.** When a layer surface (the pill) holds the keyboard, Hyprland's
  `activewindow` may still name the last window or say `{}`. The drawer probe steps aside after a summon for
  that reason, but only a real Hyprland shows whether `{}` means "no focus" in that state.
- **Bar rects.** `window-under-bar` assumes rects are in the screen's own coordinates and keyed by the
  monitor's name. Report only what is drawn over apps (the overlay: pill, line, chips), not the bottom-layer
  desk rails, or every app beside a rail is red.
- **Coredumps** come as `coredumpctl list --json=short`: `time` in microseconds, `sig`, `exe`. The runner
  adds `comm` and `cmdline` when it has them; without them the program is read from `exe`.
- **`details_at`** is when the drawer last opened (from Hyprland's `activespecial` event), so the 2 s grace is
  honest. Without it the probe judges an open drawer at once, and a just-opened one can flicker red for a
  moment (the retry catches that).
- **`agentd_started`** is `agentd.json`'s start time. Without it `bar-alive` has no grace after agentd
  restarts: a bar that has not said hello yet is red once its `alive_at` is 15 s old.
- **`tool_results`, `turn_errors`** are read from the per-turn logs by the runner: `{turn, tool, ok, t,
  text}` and `{turn, t, text}`. `groups` and `medians` come from the habits module; `slow-turn` is not
  checked without both.
- **Events count on the first sighting.** `summon-focus` and `bar-restarts` are facts, but a noisy bar could
  make them noisy; moving them to "counts at 2" is one line in `_counts`.
- **The app name is left out of `app-health`'s evidence** on purpose: it is his words.
- **A provider's error line.** A CLI may echo his ask in its error, as it was, in JSON (escapes undone), over
  several lines, in another case or cut short. `turn-failed` takes out of the whole error every run of four
  of his words in a row (or of 24 characters), whole words only, and the whole of an ask shorter than four
  words; the first line is taken after that, so the error's own words stay and fingerprints stay apart.
- **A path takes the rest of its name.** `scrub_text` and `strip_line` end a path at the next `": "`, a quote
  or bracket, or the end of the line, so a path with a space in its name goes whole; the words after an
  unquoted path go with it (a Hyprland config error reads `Config error in file <path>: no such function`).
- **Fixture geometry is constructed**, not captured. The shapes are hyprctl's; the numbers are chosen to
  show each bug. Nothing here was run against a live Hyprland.
- `summon-focus` uses 1.5 s for the ack (the task says so); the brief's 300 ms is a target, not a limit.

## What needs a real Hyprland (or real data)

- That the fixtures match what `hyprctl -j clients|monitors|layers|activewindow` prints on the real
  machine, including `activewindow` when the pill holds the keyboard, and `specialWorkspace` after Details.
- That the bar's rect names and coordinates match its `rects` message, and only overlay rects are sent.
- That `coredumpctl list --json=short` has the fields the `coredump` probe reads.
- `slow-turn`, `tool-errors` and `rephrase` thresholds (3x, 40% of 5, 120 s) are guesses until there are real
  turns to look at.
- There is no "fixed" state for a finding yet; `sent` is the last one, `reported` is a held report.
- A `bombadil probe <id>` command is in every bundle; it belongs to the CLI part and does not exist here.

## Known gaps

- A word he typed that is also a word of a probe's own sentence ("again", "stopped") is blanked from that sentence when it is a whole word, because a short secret is taken out as one.

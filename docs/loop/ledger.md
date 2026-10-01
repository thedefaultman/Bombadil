# Ledger v2 and what the bar says about itself

The part of the loop that has to exist first: history cannot be recovered, so every turn and every
launcher action is written down with what the loop will need later, and everything the bar says
about itself is kept where the probes can read it. Format: `docs/LOOP.md` ("Ledger v2", "Files",
"agentd ↔ clients"). This page says what the code does beyond that, and where it can bite.

Files: `src/bombadil/agentd.py`, `providers.py`, `watch.py`, `loop/signals.py`.

## What agentd writes

`turns.jsonl` rows as in LOOP.md, with these choices:

- **`n`** is on model rows too: the turn number clients see in events (`turn`), the suffix of `id`.
  It restarts at 1 with every agentd; `id` does not repeat.
- **`origin`** comes from the prompt message (`typed`, `button`, `app`, `session`, `routine`,
  `loop`, `retry`, `cli`; anything else is ignored), else `app` for a text starting `[from app `,
  else `typed`. It follows the queued turn, not the connection.
- **`tools.n`** counts provider `tool` events (a Bash call, an MCP call, a web search, a Codex to-do
  update), never the streamed `tool_start`/`tool_input` pieces. A `!command` is one step, `Bash`.
- **`model`, `cost`, `usage`, `rate_limit`** are merged from the provider's `meta` events, last one wins.
  `usage.input` never includes cache reads, for either provider (Codex's `input_tokens` does, so it
  is reduced by `cached_input_tokens`). `cost` is what the CLI reports as dollars; Codex reports none.
  Codex says no model: it is filled only when one is configured.
- **`drift`** is `{"<type>": n}`, only when non-empty, at most 20 distinct types (then `"other"`).
- **Local rows** always carry `v, verb, via, word, of, of_snapshot` (null when not applicable).
  `verb` is the action's verb for an app or panel (`open`, `close`, `hide`; read before the launcher
  runs, which may turn a panel's close into hide) and the action's name for everything else
  (`undo`, `stop`, `history`, `wifi`, ...). `via` is `word` when the action carries a non-empty
  `.word` (read with `getattr`), `button` for a `{"type":"local"}` message, else `typed`.
- **Stop rows.** The word "stop" and the `{"type":"stop"}` message (Esc, `bombadil stop`) both log one.
  `of` is the id of the turn that was running, null when nothing ran (the word) - the message logs only
  when it stopped something, `via: button`. The stopped turn's own row has `stopped: true` and the same id.
- **Undo rows.** `of_snapshot` is `launcher.last_undo()` after the undo ran (only when it worked);
  `of` is the turn that snapshot was taken for, from an in-memory map of the last 200. After an agentd
  restart `of` is null and readers must match by `of_snapshot`. The launcher's "Already undone ..."
  answer is a successful undo and is logged with the marker's snapshot as well.
- A turn stopped while its restore point was saved still gets a full row (id included). A turn whose
  CLI is not installed gets none, as before.

`watch.history_lines` shows local rows as before, `improve` rows as a dim line with the row's `title`,
skips kinds it does not know and rows that are not objects, and shows `--:--` for a row with no usable `t`.

## Reading it (`loop/ledger.py`)

`read_rows(path, offset=0, inode=None, limit=None) -> Batch` returns the usable rows after a byte offset,
each with the offset just after it, and where the next read starts (`end`). With `limit` it stops after
that many rows: `end` is then just after the last of them and `more` says another line follows, so a
caller can take a long file a chunk at a time (`LoopStore.ingest` does, 150 rows to a database
transaction). A torn last line is left for next time, as before.

## Provider `meta` events

`{"kind": "meta", "model"?, "cost"?, "usage"?, "rate_limit"?, "drift"?}`: one event per fact, never
broadcast, never written to the per-turn log. Claude: model from the init line, `cost` and `usage` from
the result line (emitted just before the `result` event), `rate_limit` from a `rate_limit_event` line
(its `rate_limit_info`). Codex: `usage` (and the configured model) from `turn.completed`.

Drift: a line that is valid JSON with a `type` the adapter does not know. The known sets sit next to
each parser (`Claude.known_types`, `Codex.known_types`). **Claude's set is what the fixtures show plus
`rate_limit_event`**; a type the real CLI sends routinely that is not in them will show as drift on day
one. Look at the first real counts before trusting them.

## Signals (`loop/signals.py`)

```python
Signals(loop_dir=None, clock=time.time)     # loop_dir None: paths.loop_dir(), looked up at each write
  .hello(msg) .alive() .rects(msg) .focus_ack(msg) .friction(msg) .bar_gone()
  .summon(screen="") -> int                 # 1, 2, 3 ... per Signals; restarts at 1 with agentd
  .tick(now=None)                           # timeouts, and bar.json caught up; every 3 s from agentd
  .agentd_started(socket_path)              # writes agentd.json; runs git, so call it off the event loop
read_events(path=None, since=0.0) -> list[dict]   # rows with t > since, oldest first; bad lines skipped
read_bar(path=None) -> dict | None
build_id() -> str                           # VERSION, else git short hash, else ""
```

`signals.jsonl` rows: `hello {client, pid, build}`, `summon {id, screen?}`, `focus_ack {id, ms, late?}`,
`focus_timeout {id, waited, bar}`, `friction {what, count, seconds, drawer, ...}`, `restart {pid, was}`.
Every row has `t` (epoch seconds). `alive` and `rects` write no row, only `bar.json`.

- `focus_timeout` is written once per summon that had no `focus_ack` 1.5 s later, found by `tick`, which
  agentd runs every 3 s: `waited` is between 1.5 and 4.5 s, and the ack's own `ms` is the real delay.
  `bar` is whether a bar was connected when it was summoned; without one the timeout says nothing about
  focus. An ack that still comes is written with `late: true`.
- `restart` is written after a `hello` from a different pid than the last one, or after any `hello`
  once `bar_gone` has been called (the same pid included). The first hello after agentd starts is none.
- `bar.json` is whole or absent (written beside, then renamed) and at most 2 s behind, except that
  `hello` and `bar_gone` write at once. `alive_at` is agentd's clock when the last `alive` (or the hello)
  arrived; a `rects` message is not a heartbeat. After `bar_gone` the file stays and `alive_at` goes
  stale. A new `hello` clears `screens`.
- Rects are sanitised (numbers only; at most 16 screens, 200 rects each). `friction` keeps plain values
  (at most 10 keys) and cannot overwrite `t` or `kind`.
- `build_id()` looks for `VERSION` in `paths.share_dir()`, beside it, and beside `src/` (the tree that is
  running), first line, then `git rev-parse --short HEAD` in that tree. The bar's `build` in `hello` is
  the bar's own and is stored as sent.
- Nothing here raises. A write that fails costs one stderr line per distinct trouble, never the call.

## In agentd

- Hears `hello` (only `client: "bar"`; the hangup of *that* connection calls `bar_gone`, and a newer
  bar's hello moves the claim so the old one's later hangup is not a gone), `alive`, `rects`,
  `focus_ack`, `friction`. `ping` gets `{"type":"pong","t","pid"}` to the sender only.
- `summon` is broadcast as `{"type":"summon","id":n}`.
- `agentd.json` is written once the socket listens, from a background task.
- `self.loop = None` is where the loop's service goes. `_loop_hook(name, *args)` calls
  `self.loop.on_<name>(*args)` when it exists (sync or async, exceptions printed and dropped):
  `on_row(entry)` after every ledger row is written (local rows, stop rows and model rows alike, and any
  row written through `_log_line`), `on_client(writer)` when a client has been greeted, and
  `on_message(msg, writer)` for a message type agentd itself does not handle (`noticed_do`, ...).
  A service needs only the `on_*` it wants. To answer a client: `await agentd._send(writer, msg)`.
- To start a turn of its own, the service calls
  `await agentd.handle({"type": "prompt", "text": ..., "origin": "loop"}, None)`: no client is needed, and
  the text goes through the launcher first like any prompt.

## Traps

- A row is written a moment after the `turn_end` event, not before it: a test that reads the ledger
  right after `turn_end` must wait for the row.
- A stop row is written *before* the stopped turn's row (the stop comes first, the turn ends after).
- `Action.word` is another agent's field; nothing here fails without it.
- Summon ids restart at 1 when agentd restarts; pair an ack with its summon by `t` as well.
- `signals.jsonl` only grows. Nothing prunes it yet.

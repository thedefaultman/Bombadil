# agentd

> **Status:** Shipped
> **Code:** `src/bombadil/agentd.py`, `src/bombadil/providers.py`, `src/bombadil/launcher.py`, `src/bombadil/procs.py`, `src/bombadil/config.py`, `src/bombadil/paths.py`, `bin/agentd`, `bin/bombadil`
> **Design:** [UX brief, piece 1](../design/ux-brief.md#1-you-can-see-what-it-is-doing-as-it-happens) and [piece 2](../design/ux-brief.md#2-the-pill-is-the-launcher-and-stop-means-stop), [Foundation choices, section 4](../design/foundation-choices.md#4-how-the-agent-runs-as-the-os--clis-official-agent-clis-as-the-engine-our-own-daemon-around-them)
> **Verified:** 2026-10-01 against `main` at `26843d3`

`agentd` is the session daemon. It listens on a Unix socket for newline-delimited JSON and runs one turn at a time by starting the provider's own CLI (Claude Code or Codex) in full-access mode with the OS tools attached. It broadcasts everything that happens to every connected client: the shell, `bombadil ask` and os-mcp. It also answers the words that must never wait for a model (open, stop, undo, `!command`), and it owns the restore point, Stop and the turn log that make full access safe to give.

The design settled that the vendor CLI does the thinking and the tool use, and the daemon owns the conversation, the connection to the screen and the snapshots. Everything the shell shows about a turn is made from the events this page lists.

## How it works

### One turn

```mermaid
sequenceDiagram
    participant C as Client
    participant A as agentd
    participant S as snapper
    participant P as Provider CLI
    participant M as os-mcp
    C->>A: prompt
    A-->>C: queued with the turn id
    A-->>C: event turn_start
    A->>S: create "turn:N: prompt"
    S-->>A: snapshot number
    A-->>C: event snapshot
    A->>P: start in a systemd scope, prompt on stdin
    P->>M: start the MCP server named in its config
    loop until the CLI exits
        P-->>A: one line of stream output
        A-->>C: status, text, tool, tool_result, plan and card events
        opt a tool that needs agentd
            P->>M: tool call
            M->>A: desk-tool, job-tool or card
            A-->>M: desk-result, job-result or card_ack
            M-->>P: tool result
        end
    end
    P-->>A: result line, then exit
    A-->>C: event turn_end
    A->>A: append to turns.jsonl
```

What happens to a prompt that becomes a turn, in order (all of it in `src/bombadil/agentd.py`):

1. A `prompt` that is not a launcher word gets the next turn id and goes on the `pending` list. The sender is told `{"type": "queued", "turn": n}` at once. The single `_worker` task takes the first prompt that can run (setup is `ready`, or the line starts with `!`) when no turn is running.
2. `turn_start` goes out before anything slow. The restore point is saved after it, so something true is on screen while snapper works.
3. If the provider's CLI is not installed (and the line is not a `!` command), the turn says so with an `error` event and ends with an empty `turn_end`. No line is added to `turns.jsonl` for it.
4. The turn counter `turns` goes up, `launcher.clear_undo()` runs (it keeps the undo marker while an undo still waits for its restart), and, unless the line starts with `!` or `explain` is `brief`, a capture of the network, disks, sound and screens starts in the background for the receipt.
5. If the snapshot object says `available`, the status line says "Saving a restore point", `Snapshots.create("turn:<turns>: <first 60 characters of the prompt>")` runs off the event loop and a `snapshot` event carries its number. A failing `snapper create` (a non-zero exit) is an `error` event ("no undo point for this turn: ...") and the turn goes on. Any other exception from it ends the turn through the worker's handler: an `error` event and a short `turn_end`, the CLI never starts, and no line goes to `turns.jsonl`. See [restore-points.md](restore-points.md) for what snapper and `bombadil-rollback` do.
6. If Stop arrived while the restore point was saved, the CLI never starts: `turn_end` with `stopped: true`, and the turn is logged.
7. The command is built by the provider (see Providers below). If the person did things without the model since the last turn (a launcher word, a desk gesture, a job that ended), they are put in front of the prompt as `[Done by the user without you since your last turn: ...]`. A `!` line does not take them.
8. The CLI starts in its own systemd user scope, with the prompt written to its stdin and stdin closed. The status line says "Waiting for Claude" (or Codex, Fake). Every stdout line goes to `provider.parse`; each event goes through the `Narrator` (the live line, the plan), the card follower and `_on_event`, which broadcasts it.
9. When the process exits, agentd lets the output drain for at most `OUTPUT_GRACE` (1 s), asks the adapter for its `finish()` events, reads stderr for at most 1 s and, if the exit was not clean and neither an error nor a good result was reported, sends an `error` event with stderr's last 2000 characters or `<cli> exited with <code>`.
10. `turn_end` closes the turn and one line is appended to `turns.jsonl`. After it, a receipt card may follow: a before and after of what the turn changed in the network, a service, the disks, the sound or the screens. See [cards-and-pictures.md](cards-and-pictures.md).
11. The worker clears `current` and broadcasts `status`. If the CLI said the login is gone, agentd signs in again and runs the same prompt once more ([browser-and-signin.md](browser-and-signin.md)).

### Where a typed line goes

```mermaid
flowchart TB
    typed["a line typed in the pill: a prompt message"] --> blank{"blank?"}
    blank -- yes --> err["error event: empty prompt"]
    blank -- no --> word{"exact launcher word, or a provider name at first boot?"}
    word -- yes --> local["reply local, then run the action: no turn, no restore point, no model"]
    word -- no --> queue["give it a turn id, reply queued, append to pending"]
    queue --> ready{"setup is ready, or the line starts with !"}
    ready -- no --> wait["waits in pending"]
    wait -- sign-in done --> worker
    ready -- yes --> worker["worker starts it when no turn is running"]
    worker --> bang{"starts with !"}
    bang -- yes --> sh["the turn runs sh -c on the rest of the line"]
    bang -- no --> cli["the turn runs the provider CLI"]
```

`launcher.match` decides the second diamond. It is exact on purpose: after lowercasing and trimming, the whole line must be a known word, with an optional verb in front. A parser that guesses would give the machine two brains that sometimes disagree. A line that starts with `!` never matches, whatever follows. A line in another script, or with extra words, goes to the agent. The words are in the launcher table under Interfaces.

A launcher action runs beside the socket reader (`_background`), so a `stop` sent right after it is read at once. `AgentD.local` sends it one of three ways. `why`, `picture`, `signin`, `provider` and `stop` need agentd's own state and are done in place. `undo`, `restart` and `shutdown` first raise `_hold`, so no queued turn starts until the action is done, then take the `_exclusive` lock, stop the running turn and wait until it has ended, run the action, and lower `_hold`: an undo that raced a turn would roll back to that turn's own restore point. Everything else goes to `Launcher.run`, which calls Hyprland, the apps and snapper directly, in a worker so the event loop stays free.

### One turn at a time, and the queue

- There is one `_worker` task, so there is one running turn (`current`) and the rest wait in `pending`, a list of `(turn id, prompt)`. Turn ids come from `next_id`, assigned when the prompt is accepted, so they follow the order of asking.
- `queued` events are broadcast when something is already running or waiting, or when the prompt must wait for sign-in. The shell draws them as grey chips. `{"type": "unqueue", "turn": n}` removes one and broadcasts `unqueued`.
- `_next` takes the first prompt that is runnable, so a `!` line passes over prompts that wait for sign-in. Stop does not empty the queue: the next prompt starts when the stopped turn has ended.
- Cancelling a sign-in drops the prompts that waited for it (`_drop_waiting`, an `unqueued` event each). A turn that found its login gone goes back to the head of the queue once.
- The queue, the turn ids and the turn counter are in memory. They start again at 1 when agentd starts.
- `status.busy` is true from the moment a runnable prompt is accepted, so Esc stops it even before its turn has started.

### Stop

`{"type": "stop"}` (or `cancel`), the launcher words `stop`, `cancel`, `stop it` and `stop that`, and `bombadil stop` all end in `AgentD.stop()`. It returns at once and the work is done in the background:

1. With no turn running, Stop cancels a sign-in in progress; otherwise it returns false and the launcher word answers "Nothing is running."
2. With a turn running it sets `stopping`, takes the closing line from the `Narrator` ("Stopped while installing docker."), sends a `status` "Stopping", and hands the process to `procs.Stopper`. From then on the turn's own status, plan and failure events are suppressed: a Stop is not reported as an error.
3. `Stopper.stop` finds every process of the turn: the descendants of the CLI, everything in its scope's cgroup (`bombadil-turn-<pid>-<turn>-<seconds>`) and its process group. It spares the windows the turn opened: processes started with `--class=bombadil-browser`, `--app-id=bombadil-terminal` or `--app-id=bombadil-details`, `nautilus` and `bombadil-app run <name>`, and their children (`PROTECTED_ARGS`, `_is_window`).
4. Everything else gets SIGINT first, so the CLI saves its conversation and `sudo` relays the signal to a root command. After 3 s (`grace`), what is left gets SIGTERM, then SIGKILL after 1 s. A root process with no `sudo` to relay goes through `sudo -n kill`.
5. `pacman` is the exception. A pacman that was running when Stop began is never sent SIGTERM or SIGKILL (one that starts during Stop is treated like any other process). The rest of the turn is frozen (SIGSTOP), pacman gets SIGINT and finishes its package for as long as that takes (up to `pacman_grace`, 900 s), the status line says "Stopping after this package", and only then is the rest stopped. A stale `/var/lib/pacman/db.lck` is removed if no pacman runs.
6. Without a systemd user manager (`BOMBADIL_NO_SCOPE=1`, no `/run/systemd/system`, or a failed probe at start) the turn is just the CLI's process tree and process group.

### The restore point

The restore point is taken in `AgentD.turn`, after `turn_start` and before the CLI starts, for every turn that gets that far: model turns and `!` lines alike. Launcher words take none. Its number goes out as a `snapshot` event, into `turns.jsonl` as `snapshot`, and into the CLI's environment as `BOMBADIL_TURN_SNAPSHOT`, so that "undo that" run by the agent rolls back past this turn's own restore point and not to it. The launcher word `undo` is handled by agentd itself (`Launcher._undo`), with no model turn. With `snapshots = false` in the config, or without snapper and its `root` config, `Snapshots.available` is false and the turn skips the step.

### Providers

A provider adapter (`src/bombadil/providers.py`) builds the command for one turn and turns the CLI's output lines into events. The CLI flags live only there.

| | `Claude` | `Codex` | `Fake` | `Shell` |
|---|---|---|---|---|
| Registered | `PROVIDERS["claude"]` | `PROVIDERS["codex"]` | `PROVIDERS["fake"]` | no: made by agentd for a `!` line |
| Binary | `claude` | `codex` | `$BOMBADIL_FAKE_PROVIDER`, else `cat` | `sh` |
| Prompt | on stdin | on stdin (`-` as the last argument) | on stdin | none: `sh -c <rest of the line>` |
| Full access | `--permission-mode bypassPermissions --dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | not applicable | runs as the user |
| os-mcp | `--mcp-config <state>/claude-mcp.json --strict-mcp-config` | `-c mcp_servers.bombadil-os.command=...` and `-c mcp_servers.bombadil-os.env_vars=[...]` | none | none |
| System prompt | `--append-system-prompt` | `-c developer_instructions=...` | none | none |
| Resume | `--resume <session id>` | `exec resume ... <session id> -` | none | none |
| Plan tools | env `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` | `-c tools.update_plan.enabled=true` | none | none |
| Silence allowed | 30 s (`quiet_factor` 1.0) | 180 s (`quiet_factor` 6.0) | 30 s | no watchdog |

Claude, as built by `Claude.command`:

```sh
claude -p --output-format stream-json --verbose --include-partial-messages \
  --permission-mode bypassPermissions --dangerously-skip-permissions \
  --mcp-config <state dir>/claude-mcp.json --strict-mcp-config \
  --append-system-prompt "<system prompt>" [--model <model>] [--resume <session id>]
```

A comment in `Claude.command` gives the reason for `--strict-mcp-config`: without it the account's own connectors (mail, drive) load beside the OS tools and end replies with notices to authorize them. `--include-partial-messages` is what lets the live line show the reply and each tool call as they are written.

Codex, as built by `Codex.command`, fresh and resumed:

```sh
codex exec --json --dangerously-bypass-approvals-and-sandbox <overrides> -C <cwd> -
codex exec resume --json --dangerously-bypass-approvals-and-sandbox <overrides> <session id> -
# <overrides> = -c mcp_servers.bombadil-os.command="<os-mcp>" -c mcp_servers.bombadil-os.env_vars=[...]
#   -c developer_instructions="<system prompt>" -c tools.update_plan.enabled=true
#   --skip-git-repo-check [--model <model>]
```

The overrides are the same on a fresh and a resumed turn; a comment in `Codex.command` says a resumed turn without them loses the OS tools (`tests/test_providers.py` asserts they are present on a resumed command). `resume` takes no `-C`; agentd already starts the CLI in the turn's `cwd` (the home folder, `Turn.cwd`).

**Session resume.** `AgentD.session_id` is the provider's conversation id. It is adopted only from a `result` that worked (Claude's result line carries it, Codex's comes from `thread.started`), so a dead id is not kept. It is cleared when the person switches provider, and when the CLI says the conversation is gone (`num_turns == 0` or "no conversation found"), with a plain note on the error. It is held in memory and is not restored at start. It is only recorded in `turns.jsonl` as `session`.

**How os-mcp is passed in.** agentd never starts os-mcp. It chooses the command (`providers.get`: `bombadil-os-mcp` on `PATH`, else `bin/bombadil-os-mcp` in the checkout), registers it with the CLI as the MCP server `bombadil-os`, and gives the CLI the environment the server needs to call back: `BOMBADIL_TURN`, `BOMBADIL_SOCKET` and, when a restore point was taken, `BOMBADIL_TURN_SNAPSHOT`. The CLI starts the server as its child. A comment on `MCP_ENV` says Codex starts MCP servers with only a few variables (`HOME`, `PATH` and the like), so the adapter lists what to forward there. If Claude's `init` line does not report `bombadil-os` as `connected` or `pending`, the adapter yields an `error` event ("the OS tools did not start"). The tools themselves are in [os-mcp.md](os-mcp.md).

**The system prompt** is built by `providers.system_prompt()` and sent on every turn, fresh or resumed, to both CLIs. It says who the agent is, to use the `bombadil-os` tools, where the QML kit and the `bombadil-apps` skill are (`kit_paths()`: the checkout's `share/`, else `$BOMBADIL_SHARE/share`, `$BOMBADIL_SHARE`, else `/usr/share/bombadil/share`), that the agent has full access with passwordless sudo and should not ask, how to install packages (`sudo pacman -Syu --noconfirm --needed`, never `pacman -Sy` alone), that the final reply is at most four plain lines, to write a plan first for three or more steps, to give a one-sentence reason before each step that changes the machine, and to show answers that have parts as a picture (`system_map`, `show_card`) with one line after.

**The plan.** The plan on the desk is made from the CLI's own task list. Claude's `TaskCreate`, `TaskUpdate`, `TaskList` and `TodoWrite` calls need `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` on some models (set by `Claude.env()`). Codex's `todo_list` items are translated to a `TodoWrite` call by the adapter (`_codex_todos`), and `update_plan` is switched on in its config. The `Narrator` keeps the table and agentd sends it as a `plan` event whenever it changes.

**`Shell` and `Fake`.** A line that starts with `!` is run by a `Shell` adapter that agentd makes for it: `sh -c <rest of the line>` in the home folder, stderr merged into stdout, no provider environment, and a restore point first like any other turn. `parse` turns each non-blank output line into an `output` event, and `finish` closes with a `tool_result` (id `shell`, the last 200 lines) and a `result` whose failure text ends in `(exit N)`. `Fake` is for tests and for running the shell with no provider (`BOMBADIL_PROVIDER=fake`): it runs `cat`, so it answers `echo: <prompt>` as a `text` event and a `result`. It is always installed, declares no os-mcp, and is not in `config.PROVIDERS`, so a config file cannot name it.

### Starting it

| Where | How |
|---|---|
| Live ISO and installed system | The session's Hyprland config runs `agentd` on `hyprland.start` (`iso/airootfs/etc/skel/.config/hypr/hyprland.lua`). The ISO build copies the tree to `/usr/share/bombadil` and links `/usr/local/bin/agentd` to `/usr/share/bombadil/bin/agentd` (`scripts/build-iso.sh`). Nothing restarts it if it dies. See [iso-and-install.md](iso-and-install.md). |
| Start by hand, or restart | `hyprctl dispatch 'hl.dsp.exec_cmd("agentd")'` starts one, and `bombadil-setup` runs the same dispatch when no agentd answers. It does not replace a running agentd: stop that one first (`restart_agentd` in `iso/airootfs/usr/local/bin/bombadil-smoke` does `pkill -u user -f 'bin/agentd'` and waits for it to end). |
| Development | `BOMBADIL_PROVIDER=fake bin/agentd`, or `scripts/dev-session.sh`, which also starts the shell and points `BOMBADIL_SHARE` at the checkout. On a live session set `BOMBADIL_SOCKET` (or `BOMBADIL_RUNTIME`) to a different path first, or the new agentd takes over the live one's socket path. See [development.md](../contributing/development.md). |

`bin/agentd` puts `src/` on `sys.path` and calls `bombadil.agentd.main`, which takes no arguments. `main` loads the config, picks the provider (`BOMBADIL_PROVIDER` first, then `provider`), builds `AgentD` and serves. A provider whose CLI is missing does not stop it: agentd keeps serving so the bar can say what is missing. `SIGTERM` ends a sign-in it started, closes every client and stops. On start it removes any file at the socket path, live or stale: it does not check that nobody listens, so a second agentd leaves the first running with no socket. It then probes for systemd scopes once (`procs.scope_supported`), so the first Enter does not pay for it.

## Interfaces other pieces depend on

### The socket

| | |
|---|---|
| Path | `paths.socket_path()`: `$BOMBADIL_SOCKET`, else `<runtime dir>/agentd.sock`, where the runtime dir is `$BOMBADIL_RUNTIME`, else `$XDG_RUNTIME_DIR/bombadil` (`/run/user/<uid>/bombadil` when the variable is not set) |
| Framing | one JSON object per line, in both directions. A line that is not JSON or not an object is skipped. A message with an unknown `type` is ignored without an answer. A line longer than 65,536 bytes (the asyncio stream limit, which `serve` leaves at its default) ends the connection without an answer, so a client that sends a long prompt or a `card` keeps it under that. |
| Greeting | on connect, in this order: `status`, `entries`, `setup` |
| Broadcast | every `event`, and `status`, `setup`, `entries`, `desk`, `jobs`, `summon`, go to every client. A reply to one request goes to the sender only. |
| Slow clients | each client has a queue of `CLIENT_BACKLOG` (10,000) messages and each write gets `SEND_TIMEOUT` (5 s). A client that stops reading is dropped, so it cannot hold up the others. |
| A message that raises | the sender gets an `error` event ("agentd could not handle that: ...") with `turn: null`, and the connection stays open. A line over the size limit is not this case: it closes the connection. |
| Access | agentd sets no mode on the socket and does not check who connects. |

### Messages a client sends

| `type` | Fields | What agentd does | Answer |
|---|---|---|---|
| `prompt` | `text`; optional `asked_by` (a helper's name, `[a-z][a-z0-9_-]{0,23}`, for a turn a coding session asked for) | routes the line (diagram above) | launcher word: `{"type": "local", "action": kind}` to the sender, then `local` events. A turn: `{"type": "queued", "turn": n}` to the sender. Blank: an `error` event "empty prompt" to the sender |
| `stop`, `cancel` | none | `AgentD.stop()` | the turn ends with `turn_end`; nothing if idle |
| `unqueue` | `turn` | drops a waiting prompt | `unqueued` event and `status`, only if one was removed |
| `local` | `action`: one of the names in `CORE_COMMANDS` or `UTILITY_COMMANDS` | runs that launcher action (the Undo button) | `local` events; an unknown name is ignored, and there is no `{"type": "local"}` reply as there is for a typed word |
| `details` | `turn` (optional) | opens the turn's log in the details drawer (`bombadil watch --file`, `--follow` for the running turn); again while it shows closes it | `local` event with `action: "details"` only on failure |
| `close_details` | none | closes the drawer | none |
| `open` | `kind`: `path`, `unit`, `package`, `url` or `turn`; `value` | checks again with `cards.check_opens`, then opens it | `path`, `unit`, `package` and `url` answer with a `local` event, `action: "open"`, and so does a value that fails the check. `turn` opens the details drawer and answers only on failure (`local` with `action: "open"` when the turn's log is not kept, or `action: "details"`) |
| `summon` | none | broadcasts `{"type": "summon"}` | every client |
| `card` | `card` | `cards.accept`, then draws it | `{"type": "card_ack", "shown": bool, "errors"?: [...]}` to the sender; `shown` is true when a client besides the sender is connected |
| `desk` | `op`: `get`, `fold`, `hide`, `show`, `move`; `widget`, `rail`, `rank` | `get` answers; the others change the desk | `desk` state to everyone on change. `get` also sends the plan of a running turn and the `jobs` table |
| `desk-tool` | `id`, `turn`, `op` (`show`, `hide`, `move`, `fold`, `unfold`, `state`; any other value is refused with a plain sentence), `widget`, `rail`, `rank` | the os-mcp `desk` tool; refused unless `turn` is the running turn, not being stopped, and its typed words asked for the desk | `desk-result` to the sender |
| `jobs` | `op`: `get`, `stop`, `dismiss`, `why`; `id` | acts on the jobs table, never through the model | `jobs` table; `why` opens the output in the drawer |
| `job-tool` | `id`, `turn`, `op`: `start`, `list`, `stop`; `title`, `command`, `kind`, `seconds`, `job` | the os-mcp `job` tool; `start` needs the running turn | `job-result` to the sender |
| `status` | none | reads the state | `status` |
| `setup_action` | `id`: `provider:<name>`, `signin`, `show`, `cancel`, `wifi` | a chip under the setup line | `setup` and `status` |
| `signin` | `provider` (optional) | signs in, or switches provider first | `setup` messages |
| `open_url` | `url` (http, https or file), `signin` (optional id) | opens the browser panel; a link of the running sign-in goes to that sign-in | none, or an `error` event |

The desk and the jobs table are in [desk.md](desk.md); sign-in in [browser-and-signin.md](browser-and-signin.md); `card` and `open` in [cards-and-pictures.md](cards-and-pictures.md).

### Messages agentd sends

| `type` | Fields | Sent |
|---|---|---|
| `status` | `busy`, `provider`, `setup`, `turns`, `snapshots` (is a restore point possible), `queued` (count), `turn` (running id or null), `queue` (`[{turn, prompt}]`) | on connect, on request, and after every change to the queue, the turn or the setup |
| `entries` | `entries`: `[{name, title, kind, words}]` with `kind` `app`, `panel`, `widget` or `command` | on connect, and again when the apps in `BOMBADIL_APPS` change (looked at every 3 s) |
| `setup` | `state`, `provider`, `title`, `line`, `tone`, `actions` (`[{id, label, style}]`), `phase`, `view` | on connect and on every change. `state` is `choose`, `checking`, `signed_out`, `offline`, `signing_in` or `ready` |
| `queued` | `turn` | to the sender of a `prompt` that became a turn |
| `local` | `action` | to the sender of a `prompt` that was a launcher word |
| `event` | `kind`, `turn`, and the fields of that kind | the next table |
| `summon` | none | after a `summon` message |
| `desk` | `folded`, `hidden`, `rails`, `order`, `screen` | answer to `desk get`, and to everyone when the desk changes (changes within 30 ms go out as the state they end in) |
| `desk-result`, `job-result` | `id`, `ok`, `text` | to the sender of the tool message |
| `jobs` | `jobs`: `[{id, title, kind, state, started, deadline, ended, pct, last, unit}]` | on request, and to everyone when the table changes (looked at every 2 s while it has rows) |
| `card_ack` | `shown`, `errors` | to the sender of a `card` |

### Events the shell can receive

Every `event` message is `{"type": "event", "kind": ..., "turn": n or null, ...}`. Events with a turn id are also appended to that turn's log file, except `status` events whose `source` is `agent`, partial cards, `queued` and `unqueued`.

| `kind` | Fields | When |
|---|---|---|
| `queued` | `turn`, `prompt` | a prompt waits behind a running turn, behind another prompt, or for sign-in; also a turn that runs again after a sign-in |
| `unqueued` | `turn` | a waiting prompt was removed |
| `turn_start` | `prompt`, `snapshot` (always null here), `asked_by` | the turn begins, before the restore point |
| `status` | `text`, `risk` (null, `system` or `irreversible`), `command` (the exact command or null), `source` (`step` or `agent`); on steps also `touched` (counts by `package`, `service`, `file`, `app`), `touched_text`; optional `because` (the agent's reason, at most 140 characters), `after` (`{label, kind, text}`, on a risky step that follows something read from outside) | the live line above the pill |
| `snapshot` | `number` | the restore point was saved |
| `text` | `text` | a complete message from the model |
| `tool` | `id`, `name`, `input`, optional `parent`, `because`, `after` | a tool call, as the CLI names it (`Bash`, `Edit`, `mcp__bombadil-os__show_panel`) |
| `tool_result` | `id`, `output` (cut at `MAX_OUTPUT`, 16,000 characters), `error`, optional `exit_code`, `parent` | the tool finished |
| `file_change` | `id`, `changes`, optional `because`, `after` | Codex edited files |
| `output` | `text` | one line of a `!` command's output |
| `plan` | `steps`: `[{id, subject, active, status}]`, the whole table | the plan changed. `status` is `pending`, `in_progress` or `completed`; a step just made has `id: null` and comes last |
| `card` | `card` (it holds `partial: true` while a `show_card` call is being written), or `{id, gone: true}` to take one back | a picture to draw. A receipt follows `turn_end` |
| `result` | `ok`, `text`; Claude adds `session_id`, `subtype`, `terminal_reason`, `num_turns` | the provider finished |
| `error` | `text` | a failure, in plain words. Account limits and rate limits are rewritten (`_limit_text`); a `!` line's failure is passed through |
| `turn_end` | `seconds`, `summary`, `changed`, `irreversible`, `stopped`, `line` (the closing sentence), `read` (`[{label, kind, outside, origin?}]`) | the turn is over. An early end sends fewer: no `read` when the CLI is missing, and only `seconds` and `stopped` when the turn raised |
| `local` | `action`, `target`, `phase` (`start` or `done`), `ok`, `text`; `turn` is null | a launcher word, a picture word or an ended job, answered without the model |

Provider lines that only feed the live line (`tool_start`, `tool_input`, `text_delta`, `thinking`, `message_start`) are never broadcast. `session` and `signed_out` are consumed by agentd. The order inside a turn is: `turn_start`, a `status` "Saving a restore point" and `snapshot` when there is a snapshot, a `status` "Waiting for <provider>", then `status`, `text`, `tool`, `tool_result`, `plan` and `card` as they come, `result` (and `error`), `turn_end`, and possibly a receipt `card`.

### Launcher words

These are handled by `launcher.match` and never reach the model. A line is compared after lowercasing and trimming spaces and `. ! ? , ; :`. Table words also ignore spaces, hyphens and underscores (`wi-fi` is `wifi`); picture questions are matched whole.

| Group | Words | Action | Does |
|---|---|---|---|
| Stop | `stop`, `cancel`, `stop it`, `stop that` | `stop` | `AgentD.stop()`; answers "Stopping." or "Nothing is running." |
| Undo | `undo`, `undo that`, `undo it`, `undo the last change` | `undo` | ends the running turn, then `Launcher._undo` rolls the system back to the restore point saved before the latest turn, and one turn further back on each repeat. A new turn clears the marker only once a restart has applied the undo; before that, the next `undo` after a new turn says it is already undone, then continues further back. It says what it covered and applies at the next restart |
| History | `history`, `rewind` | `history` | `bombadil history` in the details drawer |
| Put away | `hide`, `hide it`, `hide that`, `hide everything`, `put it away`, `put that away` | `hide` | puts away every panel or app drawer that is showing |
| Desk | `desk` | `desk` | folds or unfolds the desk. With a `?` at the end the line goes to the agent |
| Lock | `lock`, `lock screen`, `lock the screen` | `lock` | `hyprlock` |
| Restart, shut down | `restart`, `reboot`, `restart the computer`; `shut down`, `shutdown`, `power off`, `poweroff` | `restart`, `shutdown` | ends the running turn, then `systemctl reboot` or `poweroff`. With a `?` at the end the line goes to the agent |
| Sign in | `sign in`, `log in`, `login`, `signin`, `sign in again`, `log in again`, `sign me in`, `log me in` | `signin` | `AgentD.signin_asked` |
| Provider | a verb (`use`, `switch to`, `change to`, `sign in to`, `log in to`, `sign into`, `log into`, `sign in with`, `log in with`) and `claude`, `claude code`, `anthropic`, `codex`, `openai codex` or `chatgpt` | `provider` | `AgentD.choose`: saves the choice, signs in. At first boot the bare name works too |
| Why | `why`, only while a turn runs | `why` | answers from the reason the agent gave before the step (`Narrator.why_text`) |
| Panels | `browser` (also `web browser`, `web`, `chrome`, `chromium`, `google`, `internet`), `terminal` (`console`, `shell`), `files` (`file manager`, `folders`, `my files`) | `panel` | slides it in; with `close` or `hide`, puts it away |
| Apps | an app's name or title in `~/Apps` | `app` | opens it, or brings it forward if it runs |
| Utilities | `wifi` (`wi-fi`, `wi fi`, `network`, `networks`), `sound` (`volume`, `audio`), `brightness`, `battery` | the same | opens Wi-Fi settings, or reads the level in one line |
| Widgets | a widget word with `open`, `show`, `bring up`, `close`, `hide`, `put away`, or `put <widget> away` | `widget` | shows or hides it ([desk.md](desk.md)) |
| Pictures | whole questions such as `how am i connected`, `what starts when i boot`, `my disks`, `my screens`, `what's playing where`, and `what does <service> need` | `picture` | drawn from the machine by `sysmap`, no turn ([cards-and-pictures.md](cards-and-pictures.md)) |
| Shell | any line starting with `!` | none | a turn that runs `sh -c` on the rest of the line |

Verbs before an app, panel or widget: `open`, `show`, `launch`, `start`, `run`, `bring up`, `go to`, `switch to` (open); `close`, `quit`, `exit`, `kill` (close); `hide`, `put away` (hide). Apps and panels take them all, a widget only the ones that mean a thing on the screen. Order of checking: `why` while busy, pictures, sign-in, provider switch, core commands (stop, undo, history, hide, desk, lock, restart, shutdown), apps, panels, utilities, widgets. An app you made called "Sound" wins over the utility word, and no app can shadow a core command.

Actions that run through `Launcher.run` are logged to `turns.jsonl` and, when they worked, noted for the next model turn. `why`, `stop`, `signin` and `provider` are neither.

### Configuration

`config.load()` reads `/etc/bombadil/config.toml`, then `<config dir>/config.toml` (`$BOMBADIL_CONFIG`, else `$XDG_CONFIG_HOME/bombadil`, else `~/.config/bombadil`), key by key. The ISO ships `iso/airootfs/etc/bombadil/config.toml` with `provider = "claude"` and `snapshots = true`.

| Key | Values | Default | Effect |
|---|---|---|---|
| `provider` | `claude`, `codex` | `claude` | the adapter. Any other value raises `ValueError`, and agentd exits at start |
| `model` | a model name | none | passed as `--model` |
| `snapshots` | `true`, `false` | `true` | `false` makes agentd use a no-op snapshot object: no restore points |
| `explain` | `brief`, `normal`, `teach` | `normal` | `brief` switches off the before and after receipts. `teach` is accepted and behaves as `normal`. Any other value becomes `normal` |

The setup is "configured" when the user's file exists. Without it, and without `BOMBADIL_PROVIDER`, agentd starts in setup state `choose` and the pill asks which AI.

### Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `BOMBADIL_PROVIDER` | `agentd.main`, `cardtools.provider_name` | `claude`, `codex` or `fake`. Beats the config and counts as "chosen" |
| `BOMBADIL_SOCKET` | `paths.socket_path` | the socket path. agentd also sets it for each turn's process |
| `BOMBADIL_RUNTIME` | `paths.runtime_dir` | the folder of the socket |
| `BOMBADIL_STATE` | `paths.state_dir` | state folder; else `$XDG_STATE_HOME/bombadil`, else `~/.local/state/bombadil` |
| `BOMBADIL_CONFIG` | `paths.config_dir` | config folder |
| `BOMBADIL_DATA` | `paths.data_dir` | data folder; else `$XDG_DATA_HOME/bombadil`, else `~/.local/share/bombadil` |
| `BOMBADIL_APPS` | `paths.apps_dir` | generated apps; default `~/Apps` |
| `BOMBADIL_SHARE` | `paths.share_dir` | the installed tree; default `/usr/share/bombadil` |
| `BOMBADIL_NO_SCOPE` | `procs.scope_supported` | `1` runs turns without a systemd scope |
| `BOMBADIL_FAKE_PROVIDER` | `providers.Fake` | the program the Fake provider runs; default `cat`; read when the module is imported |
| `BOMBADIL_FAKE_SIGNIN`, `BOMBADIL_FAKE_SIGNIN_HOST` | `providers.Fake` | play a sign-in (`auto`, `manual`, `never`, `fail`) and an unreachable host ([browser-and-signin.md](browser-and-signin.md)) |
| `BOMBADIL_SIGNIN_TIMEOUT` | `signin.py` | seconds a sign-in page may wait; default 600 |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | `Claude.credentials`, `Codex.credentials` | where each CLI keeps its login; agentd reads only the file's modification time |

agentd sets these for the process of each turn, a model turn or a `!` line: `BROWSER` (the path of `bin/bombadil-browser`, so a link the agent opens slides the browser panel in), `BOMBADIL_TURN`, `BOMBADIL_SOCKET`, and `BOMBADIL_TURN_SNAPSHOT` when a restore point was taken. A model turn also gets the provider's own `env()`.

### The terminal client

`bin/bombadil` talks to the same socket and is the reference for a small client.

| Command | Sends | Behaviour |
|---|---|---|
| `bombadil ask TEXT` | `prompt` | follows its own turn id and prints `text` and `output` lines, tool lines and errors until `turn_end`. For a launcher word it prints the answer and exits 0 or 1 |
| `bombadil status` | `status` | prints the status message |
| `bombadil stop` | `stop` | sends and returns at once, for key binds |
| `bombadil pill` | `summon` | the same |
| `bombadil provider claude` or `codex` | `setup_action` `provider:<name>` | prints the setup line until it settles; exit 0 ready, 1 otherwise, 3 when nothing answers. With no socket it writes the config instead |
| `bombadil signin [provider]` | `signin` | prints the setup line like `provider` does. It never writes the config: with no agentd it prints "agentd is not running" and exits 3 |
| `bombadil undo` | none | calls `Snapshots.undo_last_turn()` itself, without agentd |
| `bombadil open URL` | `open_url`, through `bin/bombadil-browser` | opens a link in the browser panel |
| `bombadil watch`, `history`, `view` | none | read the turn logs (or a file or command) and print them in the terminal they run in. They show in the details drawer when agentd or the launcher starts them there |

### The turn log

`turns.jsonl` in the state folder is append-only, one JSON object per line. Three shapes share it:

| Shape | Keys |
|---|---|
| A turn | `t` (epoch seconds), `prompt`, `result` (final text), `ok` (true, false or null), `snapshot` (number or null), `provider` (`claude`, `codex`, `fake`, or `shell` for a `!` line), `session`, `stopped`, `summary`, `read` (what the turn read: `[{label, kind, outside, origin?}]`), `details` (path of the turn's event log) |
| A local action | `t`, `kind: "local"`, `prompt` (the typed words, `at the desk` for a gesture, `background job` for a job that ended or was stopped), `action`, `target`, `result`, `ok` |
| A sign-in | `t`, `kind: "signin"`, `provider`, `result`, `reason` |

Each turn also has an event log, `<state dir>/turns/<unix ms>-<turn>.jsonl`: its events, one per line, each with a `t`. `bombadil watch` replays it and `bombadil history` reads `turns.jsonl`. agentd remembers the paths of the last 50 turns it ran.

### Limits and timings

| Constant | Value | Meaning |
|---|---|---|
| `NO_PROGRESS_SECS` x `quiet_factor` | 30 s, 180 s for Codex | with no tool running and no output, the line says "<Provider> is not answering; check the connection" (checked every `WATCHDOG_TICK`, 5 s; `!` lines have no watchdog) |
| `OUTPUT_GRACE` | 1 s | how long a finished process's output may take to drain; a job left running with `&` does not hold the turn |
| `MAX_OUTPUT` | 16,000 characters | one tool result in events and the turn's event log |
| `CLIENT_BACKLOG`, `SEND_TIMEOUT` | 10,000, 5 s | see the socket table |
| `DESK_DEBOUNCE`, `JOBS_DEBOUNCE`, `JOBS_POLL` | 0.03 s, 0.03 s, 2 s | how desk and jobs changes are batched and polled |
| `OFFLINE_POLL`, `READY_LINE_SECONDS` | 5 s, 120 s | setup: how often to look for a connection, and how long "Signed in" is worth saying |

## Where state lives

| What | Where | Lifetime |
|---|---|---|
| Socket | `$BOMBADIL_SOCKET`, else `<runtime dir>/agentd.sock` | removed and created again at each start |
| Turn log | `<state dir>/turns.jsonl` | append-only, never rotated |
| Turn event logs | `<state dir>/turns/<unix ms>-<turn>.jsonl` | never pruned |
| Claude's MCP config | `<state dir>/claude-mcp.json` | written again at each Claude turn |
| Undo marker | `<state dir>/undo.json` | written by the `undo` word, kept until a restart has applied the undo |
| Desk and jobs | `<state dir>/desk.toml`, `<state dir>/jobs/` | see [desk.md](desk.md) |
| Settings | `/etc/bombadil/config.toml`, `<config dir>/config.toml` | the pill writes the user's file when the person picks a provider |
| Turn scopes | systemd user scopes `bombadil-turn-<pid>-<turn>-<seconds>` | one per turn, gone when it ends |
| In memory only | the session id (each turn line in `turns.jsonl` records it as `session`, but agentd never reads it back), `pending`, turn ids and the turn counter, the notes for the next prompt (the last 10), the setup state, the clients | lost when agentd stops |
| The CLI's own | the conversation store and the login of Claude Code or Codex | not touched by agentd |

`<state dir>` is `paths.state_dir()`, which is `~/.local/state/bombadil` unless `BOMBADIL_STATE` or `XDG_STATE_HOME` says otherwise. The restore points themselves are snapper's, see [restore-points.md](restore-points.md).

## Principles it keeps

- [Full access, with undo instead of guard rails](../principles.md#full-access-with-undo). Both adapters start the CLI with every approval and sandbox switched off, and the restore point is saved before the CLI starts, for `!` lines as well. The trap: adding an approval step to an adapter, or putting a side effect ahead of `Snapshots.create` in `AgentD.turn`.
- [Something true is on screen within 200 ms](../principles.md#something-true-in-200-ms). A prompt is answered with `queued` at once, `status.busy` is true from acceptance, `turn_start` is sent before snapper runs (`test_turn_start_comes_before_the_restore_point`), and blocking calls go through `asyncio.to_thread`. The trap: awaiting snapper, a `sysmap` capture or the CLI before the first event, or calling a blocking function on the event loop.
- [Recovery never goes through the part that broke](../principles.md#recovery-without-the-broken-part). Stop, undo, open and `!` lines are answered by agentd with no model, no network and no sign-in; Stop finds what the turn started through its cgroup; a hung client is dropped. The trap: sending a launcher word to the agent, putting a launcher word behind `access == "ready"`, or writing a handler that blocks the socket reader instead of using `_background`.
- [Records answer before models, and the machine explains itself](../principles.md#records-before-models). The live line, `why`, the plan, the receipt, the history and the details all come from the event stream and the logs, not from asking the model again. The launcher list is exact on purpose. The trap: fuzzy matching in `launcher.match`, or an extra model call to summarise a turn.
- [One machine, one conversation, one memory you can read](../principles.md#one-machine-one-conversation). One worker, one queue, one session id, and the same os-mcp and prompt for both providers. What the person did without the model is logged and told to the next prompt. The trap: running two turns at once (two agents with sudo on one machine), or a local action that skips `_local`'s log and note.
- [Every piece degrades, and nothing waits on a model it does not need](../principles.md#degrade-and-recover). A missing CLI, no snapper, no systemd, no network for sign-in and a client that stopped reading each cost a plain line and nothing else. The trap: letting an exception in one message or one narration bug end the connection or the turn (see the `except` clauses in `_client`, `_on_event` and `_worker`).

## Extending it

### Add a provider

1. In `src/bombadil/providers.py`, subclass `Provider` and set `name`, `title` and `binary`. Write `command(turn, workdir)`: the prompt stays out of the arguments (agentd writes `turn.prompt` to stdin); pass the CLI's full-access flags; attach the server with `self.mcp_command` under the name `bombadil-os`, the same on a fresh and a resumed turn; send `system_prompt()`; add `--model` when `self.model` is set; resume with `turn.session_id`.
2. Write `parse(line)` so it yields the common events: `session` (`session_id`), `text`, `tool` (`name`, `input`, `id`), `tool_result` (`id`, `output`, `error`), `result` (`ok`, `text`), `error`, `signed_out`. Name MCP tools `mcp__bombadil-os__<tool>` and send the plan as a `TodoWrite` tool call with `todos` of `{content, status, activeForm}`, because `Narrator` knows those names. Optional events for the live line only: `message_start`, `thinking`, `text_delta`, `tool_start`, `tool_input`. Add `finish()` for events at the end of the output, `is_progress()` if the CLI prints retry notices, `quiet_factor` if it is silent while it thinks, and `env()` for anything it needs in its environment.
3. Register it: add the class to the `PROVIDERS` dict (`providers.py`), or assign `PROVIDERS["name"] = Cls` as `Fake` does at the bottom of the file. `providers.get(name)` and `BOMBADIL_PROVIDER=name` then work.
4. To offer it to people, add its name to `config.PROVIDERS`. That tuple is what `config.load`, `AgentD.choose`, `bombadil provider`, `bombadil signin` and the chips from `AgentD._describe` check. Add its words to `launcher.PROVIDER_WORDS` for "use <name>". Add its host to `sysmap.PROVIDER_HOSTS` so the network picture names it (an unknown provider falls back to Claude's entry).
5. Sign-in: implement `signin_command` (or `login_command`), `signin_url_kind`, `code_from_url`, `signed_in`, `credentials`, `SIGNED_OUT`, `SIGNIN_ERRORS` and `signin_host`, as [browser-and-signin.md](browser-and-signin.md) describes. A `signed_in()` that returns `None` makes agentd treat the provider as ready.
6. To offer the provider on the ISO and in the terminal setup, also update `iso/airootfs/usr/local/bin/bombadil-setup` (the `select provider in` list, the install `case` and the login `case`), `scripts/build-iso.sh` (the CLI package in its `npm install -g` line) and `iso/airootfs/usr/local/bin/bombadil-install` (the login files it copies, now `.claude .claude.json .codex`, so the installed system starts signed in). Without them the provider works in agentd and the pill but cannot be picked in `bombadil-setup`, is not on the ISO, and its login is not carried to the installed system. See [iso-and-install.md](iso-and-install.md).
7. Tests: save a real run as `tests/fixtures/<name>-*.jsonl` and parse it the way `tests/test_providers.py` does (`_kinds`), assert `command()` for a fresh and a resumed turn, and drive agentd with a `Scripted` subclass as `tests/test_agentd.py` does.

### Add a launcher word

1. To give an existing action another name, add the word to `PANEL_WORDS`, `CORE_COMMANDS`, `UTILITY_COMMANDS`, `PICTURE_PHRASES` or `PROVIDER_WORDS` in `src/bombadil/launcher.py`. Nothing else changes.
2. For a new action, add `"<kind>": [words]` to `CORE_COMMANDS` (checked before apps: it must always mean the same thing) or to `UTILITY_COMMANDS` (checked after apps and panels: an app of that name wins).
3. Add the method `Launcher._<kind>(self, action)` returning `(ok, one plain sentence)`. `Launcher.run` finds it with `getattr(self, f"_{action.kind}")`; a kind with no method answers "Nothing here can <kind> yet."
4. Add the line for while it works in `Launcher.doing` and the failure line in `Launcher.failed`, or the line says "On it" and "That did not work".
5. `entries()` offers the first word of each command in `CORE_COMMANDS` and `UTILITY_COMMANDS` for Tab completion, except the kinds in `NO_COMPLETE`, plus a hard-coded `sign in` entry. Other aliases are matched when typed but are not completed. Put the kind in `NO_COMPLETE` if one Tab must never land on it. `agentd._action` accepts `{"type": "local", "action": "<kind>"}` for any kind in the two tables, so a button needs no agentd change.
6. If the action needs agentd's own state (the running turn, the clients, the sign-in), handle it in `AgentD.local` as `why`, `stop`, `signin` and `provider` are. If it changes the system under a running turn, join `undo`, `restart` and `shutdown` in the branch that takes `_exclusive` and `_hold`.
7. Tests: a row in `test_exact_words_open_locally` and a sentence in `test_everything_else_goes_to_the_agent` (`tests/test_launcher.py`), and an agentd test that replaces `d.launcher.run`, as `test_what_happened_without_the_model_is_told_to_the_next_turn` does.

### Add an event type the shell can show

1. From a provider: yield `{"kind": "<name>", ...}` in the adapter's `parse`. `AgentD._on_event` broadcasts every kind except `session`, `signed_out` and those in `LINE_ONLY`, with the fields as given and the turn id added, and the turn's event log records it. To feed only the live line, add the kind to `LINE_ONLY` and have `Narrator._line` return a `status` dict for it.
2. From agentd itself: `await self.event("<name>", ...)` broadcasts it and logs it for the running turn. `await self.broadcast({"type": "event", "kind": "<name>", ...})` broadcasts without logging.
3. Show it: add a `case` to the `switch (ev.kind)` in `handle` of `shell/PillState.qml` (the pill) or `shell/DeskState.qml` (the desk); `shell/shell.qml` hands every message to both. For the Details drawer, add a branch in `src/bombadil/watch.py` (`one`). `bombadil ask` prints only `text`, `output`, `tool` and `error`. See [shell.md](shell.md).
4. Add the kind and its fields to the event table above and to the module docstring of `agentd.py`. Test with `_read_until(r, "<name>")` as the tests in `tests/test_agentd.py` do.

### Add a socket message

1. Add `elif t == "<name>":` to `AgentD.handle`. An unknown type is ignored without an answer, so a mistyped one fails quietly.
2. Send a quick answer with `await self._send(writer, msg)` (to the sender) or `await self.broadcast(msg)` (to everyone). Wrap slow work in `self._background(...)`, so a `stop` or `unqueue` sent right after is read at once.
3. If an os-mcp tool will use it, follow `desk-tool` and `job-tool`: carry an `id`, check that `turn` is `self.current` (and not a boolean, and not `self.stopping`), and answer with `<name>-result` to the sender only. On the os-mcp side call `mcp_server._ask_agentd(msg, "<name>-result", timeout)`. See [os-mcp.md](os-mcp.md).
4. If the message changes shared state that every client shows, push the whole state on change and batch bursts as the desk and the jobs do (`_desk_soon`, `_jobs_soon`).
5. Clients: the shell writes through `outgoing({...})` in `PillState.qml` and `DeskState.qml`; a script uses `bin/bombadil`'s `talk` or `send`.
6. Update the message tables above and the docstring of `agentd.py`. Test with `_start(d)` and `_say(w, {...})` from `tests/test_agentd.py`.

### Add a config key

1. Add the field and its default to `Config` in `src/bombadil/config.py`, and read it in `config.load` with `merged.get("<key>", default)`. Validate it there when only some values make sense: `explain` falls back to `normal`, `provider` raises.
2. Use it where agentd starts: `agentd.main` reads `cfg` and passes what it needs to `AgentD(...)` or picks an object from it, as `snapshots` and `explain` do.
3. If the person can set it from the pill or the terminal, also keep it in `config.save_user`. That function rewrites the user's file with only `provider`, `model` and a kept `explain`, so any other key is dropped the next time a provider is chosen. That is the `snapshots` item under Known gaps.
4. Add a row to the Configuration table above (and a line to `iso/airootfs/etc/bombadil/config.toml` if the ISO ships a default), and a case to `tests/test_config.py`: the default, the user's file overriding it, and a `save_user` round trip if step 3 applies.

## Tests

| File | Covers |
|---|---|
| `tests/test_agentd.py` | the socket, turns, the queue, Stop, `!` lines, the restore point order, notes, the desk and job messages, plan and card events, the watchdog, limit texts (100 tests) |
| `tests/test_agentd_signin.py` | the setup states and sign-in inside agentd ([browser-and-signin.md](browser-and-signin.md)) |
| `tests/test_providers.py` | the commands and the event streams of Claude, Codex, Shell and Fake. `tests/fixtures/*.jsonl` holds captured Claude streams (four runs and a signed-out one) and one captured Codex stream (signed out); the other Codex lines are hand-written in the test |
| `tests/test_launcher.py` | the exact words, what goes to the agent, undo, the drawer |
| `tests/test_procs.py` | Stop: scopes, `sudo`, the protected windows, a reused pid, pacman |
| `tests/test_config.py` | defaults, the user's file, an unknown provider |

```sh
python3 -m pytest -q tests/test_agentd.py tests/test_providers.py tests/test_config.py tests/test_procs.py tests/test_launcher.py
python3 -m pytest -q -x -k "stop or queue" tests/test_agentd.py
```

The tests need `pytest` and `pytest-asyncio` (`pip install -e '.[dev]'`) and a Linux `/proc`. They point every Bombadil path at a temporary folder (the `home` fixture in `tests/conftest.py`). On 2026-10-01 these five files passed together: 319 tests. More in [development.md](../contributing/development.md).

## Known gaps

- The conversation does not survive a restart of agentd. `session_id` is held in memory and is not restored at start (`src/bombadil/agentd.py`, `__init__` and `_on_event`; `_log` only writes it to `turns.jsonl`), so every start begins a new conversation. The design keeps one conversation across reboots, with a fresh session seeded from memory after a long idle ([UX brief, decisions](../design/ux-brief.md#decisions-i-picked-a-default-for)); that is not built.
- Turn ids and the `turn:<n>` in restore point descriptions start again at 1 each time agentd starts (`agentd.py`, `self.turns` and `self.next_id`, and the `snaps.create` call). Undo only looks for the `turn:` prefix, so it still works, but the numbers are not unique across runs.
- One CLI process is started per turn (`agentd.py`, `create_subprocess_exec`). The design's warm Claude process kept alive with `--input-format stream-json` is not built; neither are a model chosen per turn, a `redo` word (`launcher.py`, `CORE_COMMANDS`) or a health check that puts changes back by itself.
- `config.save_user` writes only `provider`, `model` and a kept `explain` (`src/bombadil/config.py`), and `AgentD.choose` calls it with no model when the provider changes. A user's `snapshots = false` is dropped whenever a provider is chosen (in the pill, with `use codex`, or with `bombadil provider`), and the system default of `true` applies again at the next start.
- A `provider` that is not `claude` or `codex` in `config.toml` makes `config.load` raise in `main` (`agentd.py`, `config.py`), and agentd exits with a traceback. `fake` is only accepted through `BOMBADIL_PROVIDER`.
- `explain = "teach"` has no behaviour of its own: the code only tests for `brief` (`agentd.py`).
- Nothing restarts agentd after a crash. It starts once per Hyprland session (`iso/airootfs/etc/skel/.config/hypr/hyprland.lua`) and there is no unit for it. The shell reconnects by itself when it comes back.
- The shell sends `{"type": "dev", ...}` for the Needs you card (`shell/DeskState.qml`) and reads `dev` messages, but `AgentD.handle` has no `dev` branch on `main`. The coding-session work that adds it is in progress, see [coding-sessions.md](coding-sessions.md).
- `details` for a turn agentd has no log for (more than 50 turns back, or before a restart) opens the latest turn's log instead (`agentd.py`, `details`, with the default of `bombadil watch`).
- agentd sets no permissions on its socket and does not check the peer (`agentd.py`, `serve`). Any process that can open the socket can send any message, `stop` and `restart` included. The desk gate is a courtesy check, not a boundary ([desk.md](desk.md)).
- Under Codex only the variables in `MCP_ENV` reach os-mcp (`providers.py`, per the comment on that list). `BOMBADIL_RUNTIME`, `BOMBADIL_CONFIG`, `BOMBADIL_DATA` and `BOMBADIL_PROVIDER` are not in it, so a development setup that relies on them for os-mcp differs between the two providers.

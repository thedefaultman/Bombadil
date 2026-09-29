# Native bindings (`import Bombadil`)

These are real Python objects the runtime registers into the `Bombadil` module, so an app
reaches the system without writing any Python. They exist only inside `bombadil-app`
(which is the only place apps run, including `check`).

Paths: `~` expands to the home directory; a relative `TextFile` path (and a Store's or
Vault's file) resolves inside the app's data directory (`App.dataDir`, i.e.
`~/Apps/<name>/data/`). A `Command` runs in the home directory, so relative paths in a
command resolve there, not in `data/` (pass `App.dataDir + "/x"`). Byte counts are in bytes.
Every `interval` is in milliseconds; other durations (`autoLock`, `uptime`,
`clearAfterSeconds`) are in seconds, and `started` is a Unix time. Arrays and objects these
types give you are ordinary JS values (`Array.isArray`, `.map`, `.filter` all work), and a
missing value is `null`.

## App (singleton)

| Member | |
|---|---|
| `App.name` | the app's directory name (`"password-manager"`) |
| `App.title` | from `app.toml` |
| `App.dir` | `~/Apps/<name>` as a path string |
| `App.dataDir` | `~/Apps/<name>/data`, created on first use; never overwritten by `create_app` |
| `App.reloads` | how many times the UI has hot reloaded (0 on start) |
| `App.checking` | true while `check` renders the app offscreen (nothing is saved then) |
| `App.show()` / `App.hide()` / `App.toggle()` | slide the app's window in or out |
| `App.close()` | quit the app (it leaves the bar) |
| `App.notify(title, body = "")` | desktop notification |
| `App.openUrl(url)` | open a link in the browser panel (shared by every app; it stays open when the app is closed or killed) |

## Store

Persistent state. Every property you declare on a `Store` is saved to
`data/<name>.json` when it changes and restored the next time the app starts (and on
hot reload).

```qml
Store {
    id: store
    property var entries: []        // arrays and objects are fine
    property string filter: ""
    property int selected: -1
}
```

| Member | |
|---|---|
| `name` | file name without `.json`; default `"state"`. Two stores need two names. |
| `loaded` | true once the saved values were applied |
| `save()` | write now (normally automatic: at most 300 ms after a change, also while a value keeps changing, like a sampler or a stopwatch, and before a reload or quit) |
| `reset()` | delete the file and go back to the declared defaults |

Saved values are applied before any `Component.onCompleted` runs, so the app never sees
the defaults first. Declare plain values, not bindings (`property int n: list.count` would
be overwritten by the saved number). Values go through JSON: `color`, `url` and `date`
properties are saved as strings and come back as the same value; `NaN` and `Infinity`
(anywhere, also inside arrays) are saved as `null`. Not saved: `readonly` properties,
object properties (`property Item x`) and names starting with `_`. A saved value this
version cannot take stays in the file: one for a property that was renamed or removed
(until `reset()`), and one whose property changed type (until the app sets that
property), so undoing the edit brings it back. A file that is not valid JSON (or not a
JSON object) is renamed to `<name>.json.bad` (`.bad.2`, ... if that exists; never
overwritten) and the Store starts from the defaults. Nothing is written during `check`.

**Assign, don't mutate.** A change is seen when the property is assigned:
`store.entries = store.entries.concat([item])`, not `store.entries.push(item)`. To edit
one element: `const e = store.entries.slice(); e[i] = changed; store.entries = e`.

## Vault

Encrypted storage for secrets (scrypt key derivation + AES-256-GCM). The file is
`data/<name>.vault` (mode 600); nothing is written in the clear.

```qml
Vault { id: vault; name: "passwords" }
// first run:   vault.create(password)        -> bool
// later:       vault.unlock(password)        -> bool (false = wrong password)
// then:        vault.data                    (any JSON value, e.g. an array of entries)
//              vault.data = newValue         (re-encrypts and saves immediately; if the save
//                                             fails, error says why and data keeps its old value,
//                                             or becomes null when the vault changed on disk)
```

| Member | |
|---|---|
| `name` | default `"vault"` |
| `exists` | a vault file is there |
| `unlocked` | data is readable |
| `data` | the decrypted value; `null` while locked. Assign to save (same rule as Store: assign a new array/object) |
| `error` | last error message: `"wrong password"`, `"a vault already exists"`, `"empty password"`, `"no vault yet"`, `"the vault is locked"`, `"cannot save: ..."`, `"not JSON: ..."`, `"the vault file is damaged: ..."`, `"the vault changed on disk; unlock it again"`; `""` after a success. The last one: a save refuses to overwrite a file that something else (another process, a restored backup) changed since this vault last read or wrote it, and locks (`data` becomes `null`) so the user unlocks the fresh content |
| `autoLock` | seconds without reading or assigning `data` before it locks again; default 300, 0 = never. The countdown includes time the machine was asleep (it is checked about once a second), so a vault left open before a suspend locks right after resume. Close anything that shows a secret when it does: `onUnlockedChanged: if (!unlocked) editor.close()` |
| `create(password)` | make a new empty vault (`data` = `[]`) and unlock it; false if one exists |
| `unlock(password)` / `lock()` | |
| `changePassword(old, new)` | bool; re-encrypts with the new password and leaves the vault unlocked |
| `generatePassword(length = 20, symbols = true)` | a random password (from `secrets`) with at least one lowercase, uppercase, digit (and symbol) |
| `strength(password)` | 0..4 estimate (0 = common or very short, 4 = strong); the same score as `PasswordField`'s meter |

After assigning `data`, check `vault.error` before telling the user it was saved
(`examples/password-manager/main.qml`, `save()` and `remove()`).

Several `Vault` objects with the same `name` (for example one in a dialog) are views of
one vault: saving, unlocking or locking through one shows in all of them, and using any
of them keeps them all from auto-locking. The shortest `autoLock` among them wins: a main
Vault set to 60 locks all of them after 60 s idle, even beside a dialog Vault left at the
default 300 or one with `autoLock: 0`.

`create`, `unlock` and `changePassword` take about half a second (scrypt with 128 MiB, as
OWASP recommends; that is the point), so call them from a button, not from a binding.
During `check` nothing is written: the vault lives in memory for that run, so `create`,
`lock`, `unlock`, `changePassword` and assigning `data` behave as they will for the user.

## System (singleton)

Live system state, refreshed every second while the app runs. All properties are
bindable, so `Label { text: Fmt.percent(System.cpu) }` just stays current.

| Property | |
|---|---|
| `cpu` | total CPU busy fraction 0..1 |
| `cpus` | array of per-core fractions |
| `cpuCount` | |
| `memory` | `{ total, used, available, free, cached, buffers, shared, swapTotal, swapUsed, swapCached, dirty }` in bytes (`used` = total - available; `cached` = page cache + reclaimable slab, like `free`) |
| `memoryUsage` | `memory.used / memory.total` (0..1) |
| `meminfo` | every `/proc/meminfo` field, in bytes, keyed by its name (`"Slab"`, `"AnonPages"`, ...; the `HugePages_*` counts stay counts) |
| `pressure` | `{ cpu, memory, io }` PSI "some avg10" as percentages 0..100 (divide by 100 for `Fmt.percent`); `null` when the kernel has no PSI |
| `load` | `[1, 5, 15]` minute load averages |
| `uptime` | seconds |
| `processCount` | |
| `disks` | `[{ mount, device, fs, total, used, free }]` for local filesystems, one entry per device (refreshed every 10 s; `free` is what a user can still write). Network mounts (NFS, SMB, sshfs, ...) are left out: asking a dead server would freeze the app |
| `network` | `{ rx, tx }` bytes per second over all non-loopback interfaces (0 until the second sample) |
| `battery` | `{ present, percent, charging }` (`percent` 0..100; `present: false` and `percent: null` on desktops and VMs) |
| `temperature` | hottest thermal zone in °C, or `null` |
| `hostname`, `kernel`, `user` | strings |
| `interval` | refresh period in ms (1000, at least 100); settable |

The first values are there as soon as the app starts (`cpu` starts as the average since
boot).

## Processes

A live process list. Create one only where you show processes (it reads `/proc` on each
refresh).

```qml
Processes { id: procs; sortBy: "memory"; limit: 50; filter: search.text }
DataTable { rows: procs.list; ... }
```

| Member | |
|---|---|
| `interval` | ms between refreshes (2000, at least 100); 0 = read once, then only on `refresh()` |
| `sortBy` | `"memory"`, `"cpu"`, `"name"`, `"pid"` (or any other field of `list`); `descending` (true) |
| `limit` | keep the top N; default 0 = all |
| `filter` | case-insensitive match on name or command line |
| `list` | `[{ pid, ppid, name, command, user, state, cpu, memory, memoryPercent, threads, started }]` (`cpu` is a fraction of one core, `memory` is RSS in bytes, `memoryPercent` is 0..100 of RAM, `state` is `"running"`, `"sleeping"`, `"waiting"` (disk), `"idle"`, `"stopped"` or `"zombie"`, `started` is a Unix time) |
| `count` | processes after filtering, before `limit` |
| `refresh()` | now |
| `details(pid)` | `{ pid, ppid, name, command, exe, cwd, user, state, rss, pss, uss, swap, shared, threads, fds, started, oomScore }` from `/proc/<pid>` (`pss`/`uss`/`swap`/`shared` from `smaps_rollup`), read once per call; a field the user may not read (another user's process) is `null`; `null` if the process is gone |
| `kill(pid, signal = "TERM")` | bool; `signal` is `"TERM"`, `"KILL"`, `"STOP"`, `"CONT"`, `"INT"`, `"HUP"`; always false during `check` |

`list` is filled when the `Processes` is created, so it already has rows in
`Component.onCompleted` and in `check`'s screenshot (`cpu` in that first reading is each
process's average since it started). Changing `sortBy`, `descending`, `limit` or `filter`
re-sorts the last reading at once.

To keep a details view live, mention `procs.list` in the binding so it re-reads on every
refresh:

```qml
readonly property var proc: procs.list && table.current ? procs.details(table.current.pid) : null
```

## Command

Runs a program without blocking the UI.

```qml
Command { id: ip; command: "ip -j addr"; running: true }    // shell string, runs on start
Label { text: ip.json ? ip.json.length + " interfaces" : "…" }
Command { id: ls; program: "ls"; args: ["-la", "~"]; onFinished: (code, out) => console.log(out) }
Button { onClicked: ls.run() }
Command { id: sorter; command: "sort -u"; stdin: names.join("\n"); running: true }   // input, then EOF
```

| Member | |
|---|---|
| `command` | a shell string, run with `sh -c` |
| `program` + `args` | or a program and argument list (no shell); `~` in args expands |
| `running` | set true to start (`running: true` starts once the app has loaded); true while it runs; set false to kill the current run (a poller still runs again at its next tick: set `interval: 0` to stop it) |
| `interval` | ms (at least 100); when > 0 it runs on start and re-runs that often, skipping a tick while a run is still going (a poller; no `running: true` needed) |
| `stdin` | text written to the program's input when each run starts; then the input is closed, so `sort`, `wc`, `jq` or `ssh host cmd` see the end and finish |
| `interactive` | false; true keeps the input open for `write(text)` (a REPL, `bc`) until the program ends or is killed |
| `stdout`, `stderr`, `exitCode` | of the last run (`exitCode` is -1 before the first run and after a crash, 127 when the program does not exist) |
| `lines` | `stdout` split into lines |
| `json` | `stdout` parsed as JSON, or `null` |
| `run()` / `run(extraArgs)` / `kill()` / `write(text)` | `run` restarts a run that is still going; `extraArgs` are appended as separate arguments (for a `command`, as `"$@"` after its last command, so no quoting is needed); `kill` ends the program and everything it started; `write` sends text to the program's input and needs `interactive: true` |
| `finished(exitCode, stdout)` | signal |

A poller's output properties change when a run finishes, so it never shows half an answer
(until then they keep the last run's values). Any other run (`running: true`, `run()`, a
`tail -f`) shows its output as it arrives, a few times a second. Only the last MiB or so of
`stdout` and of `stderr` is kept. Commands run in the home directory (relative paths
resolve there) and do run during `check`.

## TextFile

Read, write and watch one file.

```qml
TextFile { id: notes; path: "notes.md" }          // data/notes.md
Editor { text: notes.text }                        // or Editor { path: ... } directly
Button { text: "Save"; onClicked: notes.save(editor.text) }
```

| Member | |
|---|---|
| `path` | |
| `text` | current content (`""` if missing); assigning it changes it in memory only |
| `exists`, `error` | `error` is `""` or why the last read/write failed (a missing file is not an error) |
| `watch` | re-read when the file changes on disk (true); also catches editors that save by renaming |
| `save(text)` | bool; atomic write (creates folders, keeps the file's mode, follows a symlink); `save()` writes the current `text` |
| `reload()`, `remove()` | |
| `changedOnDisk()` | signal: something else changed the file (`text` already holds the new content) |

During `check` nothing is written: `save` only updates `text`.

## Clipboard (singleton)

`Clipboard.copy(text, clearAfterSeconds = 0)`; `Clipboard.text` (read, bindable). Passing
`clearAfterSeconds` clears it later if it still holds that text (use 30 for secrets) and
marks it as a secret so clipboard managers that honour the hint skip it. That countdown
includes time the machine was asleep; it is checked once a second, so it can clear up to
a second late.

## Agent (singleton)

The app can talk to the OS agent, the same one the user types to in the bar.

| Member | |
|---|---|
| `Agent.connected`, `Agent.busy`, `Agent.provider` | |
| `Agent.ask(prompt)` | send a prompt as the user would; the answer also shows in the bar. Sent as soon as agentd is reachable |
| `Agent.reply` | text of the answer to this app's latest `ask`, growing as it streams (errors appear as `"Error: ..."`, including a provider that is not installed and a lost connection) |
| `replied(text)` | signal when that answer is complete, also after an error |

The prompt is prefixed with `[from app <name>]` so the agent knows where it came from
and can, for example, edit this app in response. During `check` Agent never connects.

## Highlighter

Syntax highlighting for any `TextEdit`/`TextArea` (the kit's `Editor` uses it):
`Highlighter { textDocument: area.textDocument; language: "python" }`. Languages:
`plain`, `markdown`, `python`, `json`, `qml`, `javascript`, `shell`, `toml`, `ini`;
`language` also takes a file name or extension (`"notes.md"`, `".py"`, `"sh"`), and
anything unknown is `plain`. Colors follow the theme.

## Anything else

`app.py` can still define a `Backend(QObject)` class that is exposed as `backend` for
work the bindings above do not cover (a database, an API client). Prefer the bindings:
they need no code and hot reload with the QML.

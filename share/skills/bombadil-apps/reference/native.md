# Native bindings (`import Bombadil`)

These are real Python objects the runtime registers into the `Bombadil` module, so an app
reaches the system without writing any Python. They exist only inside `bombadil-app`
(which is the only place apps run, including `check`).

Paths: `~` expands to the home directory; a relative path resolves inside the app's data
directory (`App.dataDir`, i.e. `~/Apps/<name>/data/`). Byte counts are in bytes, times
in seconds unless named `...Ms`.

## App (singleton)

| Member | |
|---|---|
| `App.name` | the app's directory name (`"password-manager"`) |
| `App.title` | from `app.toml` |
| `App.dir` | `~/Apps/<name>` as a path string |
| `App.dataDir` | `~/Apps/<name>/data`, created on first use; never overwritten by `create_app` |
| `App.reloads` | how many times the UI has hot reloaded (0 on start) |
| `App.show()` / `App.hide()` / `App.toggle()` | slide the app's window in or out |
| `App.close()` | quit the app (it leaves the bar) |
| `App.notify(title, body = "")` | desktop notification |
| `App.openUrl(url)` | open a link in the browser panel |

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
| `save()` | write now (normally automatic, 300 ms after a change and on quit) |
| `reset()` | delete the file and go back to the declared defaults |

**Assign, don't mutate.** A change is seen when the property is assigned:
`store.entries = store.entries.concat([item])`, not `store.entries.push(item)`. To edit
one element: `const e = store.entries.slice(); e[i] = changed; store.entries = e`.

## Vault

Encrypted storage for secrets (scrypt key derivation + AES-256-GCM). The file is
`data/<name>.vault`; nothing is written in the clear.

```qml
Vault { id: vault; name: "passwords" }
// first run:   vault.create(password)        -> bool
// later:       vault.unlock(password)        -> bool (false = wrong password)
// then:        vault.data                    (any JSON value, e.g. an array of entries)
//              vault.data = newValue         (re-encrypts and saves immediately)
```

| Member | |
|---|---|
| `name` | default `"vault"` |
| `exists` | a vault file is there |
| `unlocked` | data is readable |
| `data` | the decrypted value; `null` while locked. Assign to save (same rule as Store: assign a new array/object) |
| `error` | last error message (`"wrong password"`) |
| `autoLock` | seconds of no `data` access before it locks again; default 300, 0 = never |
| `create(password)` | make a new empty vault (`data` = `[]`) and unlock it |
| `unlock(password)` / `lock()` | |
| `changePassword(old, new)` | bool |
| `generatePassword(length = 20, symbols = true)` | a random password (from `secrets`) |
| `strength(password)` | 0..4 estimate |

## System (singleton)

Live system state, refreshed every second while the app runs. All properties are
bindable, so `Label { text: Fmt.percent(System.cpu) }` just stays current.

| Property | |
|---|---|
| `cpu` | total CPU busy fraction 0..1 |
| `cpus` | array of per-core fractions |
| `cpuCount` | |
| `memory` | `{ total, used, available, free, cached, buffers, shared, swapTotal, swapUsed, swapCached, dirty }` in bytes (`used` = total - available) |
| `memoryUsage` | `memory.used / memory.total` |
| `meminfo` | every `/proc/meminfo` field, in bytes, keyed by its name (`"Slab"`, `"AnonPages"`, ...) |
| `pressure` | `{ cpu, memory, io }` PSI "some avg10" percentages, `null` when unavailable |
| `load` | `[1, 5, 15]` minute load averages |
| `uptime` | seconds |
| `processCount` | |
| `disks` | `[{ mount, device, fs, total, used, free }]` for real filesystems (refreshed every 10 s) |
| `network` | `{ rx, tx }` bytes per second over all non-loopback interfaces |
| `battery` | `{ present, percent, charging }` (`present: false` on desktops and VMs) |
| `temperature` | hottest thermal zone in °C, or `null` |
| `hostname`, `kernel`, `user` | strings |
| `interval` | refresh period in ms (1000) |

## Processes

A live process list. Create one only where you show processes (it reads `/proc` on each
refresh).

```qml
Processes { id: procs; sortBy: "memory"; limit: 50; filter: search.text }
DataTable { rows: procs.list; ... }
```

| Member | |
|---|---|
| `interval` | ms between refreshes (2000); 0 = only on `refresh()` |
| `sortBy` | `"memory"`, `"cpu"`, `"name"`, `"pid"`; `descending` (true) |
| `limit` | keep the top N (0 = all) |
| `filter` | case-insensitive match on name or command line |
| `list` | `[{ pid, ppid, name, command, user, state, cpu, memory, memoryPercent, threads, started }]` (`cpu` is a fraction of one core, `memory` is RSS in bytes, `started` is a Unix time) |
| `count` | processes after filtering, before `limit` |
| `refresh()` | now |
| `details(pid)` | `{ pid, name, command, exe, cwd, user, rss, pss, uss, swap, shared, threads, fds, started, oomScore }` from `/proc/<pid>` (`pss`/`uss`/`swap` from `smaps_rollup`); `null` if gone |
| `kill(pid, signal = "TERM")` | bool; `signal` is `"TERM"`, `"KILL"`, `"STOP"`, `"CONT"`, `"INT"`, `"HUP"` |

## Command

Runs a program without blocking the UI.

```qml
Command { id: ip; command: "ip -j addr"; running: true }    // shell string, runs on start
Label { text: ip.json ? ip.json.length + " interfaces" : "…" }
Command { id: ls; program: "ls"; args: ["-la", "~"]; onFinished: (code, out) => console.log(out) }
Button { onClicked: ls.run() }
```

| Member | |
|---|---|
| `command` | a shell string, run with `sh -c` |
| `program` + `args` | or a program and argument list (no shell); `~` in args expands |
| `running` | set true to start; true while it runs |
| `interval` | ms; when > 0 it re-runs that often (a poller) |
| `stdout`, `stderr`, `exitCode` | of the last run |
| `lines` | `stdout` split into lines |
| `json` | `stdout` parsed as JSON, or `null` |
| `run()` / `run(extraArgs)` / `kill()` / `write(text)` (to stdin) | |
| `finished(exitCode, stdout)` | signal |

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
| `text` | current content ("" if missing) |
| `exists`, `error` | |
| `watch` | re-read when the file changes on disk (true) |
| `save(text)` | atomic write (creates folders); `save()` writes the current `text` |
| `reload()`, `remove()` | |
| `changedOnDisk()` | signal |

## Clipboard (singleton)

`Clipboard.copy(text, clearAfterSeconds = 0)`; `Clipboard.text` (read). Passing
`clearAfterSeconds` clears it later if it still holds that text (use 30 for secrets).

## Agent (singleton)

The app can talk to the OS agent, the same one the user types to in the bar.

| Member | |
|---|---|
| `Agent.connected`, `Agent.busy`, `Agent.provider` | |
| `Agent.ask(prompt)` | send a prompt as the user would; the answer also shows in the bar |
| `Agent.reply` | text of the answer to this app's latest `ask`, growing as it streams |
| `replied(text)` | signal when that answer is complete |

The prompt is prefixed with `[from app <name>]` so the agent knows where it came from
and can, for example, edit this app in response.

## Highlighter

Syntax highlighting for any `TextEdit`/`TextArea` (the kit's `Editor` uses it):
`Highlighter { textDocument: area.textDocument; language: "python" }`. Languages:
`plain`, `markdown`, `python`, `json`, `qml`, `javascript`, `shell`, `toml`, `ini`. Colors
follow the theme.

## Anything else

`app.py` can still define a `Backend(QObject)` class that is exposed as `backend` for
work the bindings above do not cover (a database, an API client). Prefer the bindings:
they need no code and hot reload with the QML.

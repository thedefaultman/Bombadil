# The app kit

> **Status:** Shipped
> **Code:** `bin/bombadil-app`, `src/bombadil/apps.py`, `src/bombadil/app_runtime.py`, `src/bombadil/appkit/`, `share/qml/Bombadil/`, `share/app-template/`, `share/skills/bombadil-apps/`
> **Design:** [Foundation choices, section 5](../design/foundation-choices.md#5-how-generated-apps-are-built-and-shown--qml-qt-quick-via-pyside6), [UX brief, piece 3](../design/ux-brief.md#3-apps-change-in-front-of-you-and-undo-in-a-second)
> **Verified:** 2026-10-01 against `main` at `26843d3`

The app kit is everything that turns a folder of QML into a native window. An app is a directory under `~/Apps/<name>/` with a `main.qml` written against the kit (`import Bombadil`). `bombadil-app run` opens it as a real native window in its own slide-in drawer, reloads it in place on every write and keeps the last working version on screen when an edit does not load. `bombadil-app check` loads it offscreen, so the agent gets errors with `file:line` and a screenshot without a display. The kit exists so that what the agent builds is a real app with no build step and no port, and so that every app looks like the rest of the system.

Designed and not built: a per-app history with undo, and two system-prompt rules about building a skeleton first and editing in place (see [Known gaps](#known-gaps)).

## How it works

```mermaid
flowchart TB
    tool["create_app(title, qml, files, python)"] --> write["apps.create: files, app.toml, .desktop entry"]
    write --> ask{"open is true?"}
    ask -- yes --> show["placement.show: start bombadil-app run, or slide in"]
    ask -- no --> runcheck
    show --> runcheck["run_check: bombadil-app check, offscreen"]
    runcheck --> result["ok, errors with file:line, screenshot"]
    write -.->|"a running app's watcher sees the write"| load
    show -.->|"a stopped app starts here"| load
    load["Host.load: compile main.qml, then swap"] --> compiled{"compiled and created?"}
    compiled -- yes --> live["replacement root in the same window, state restored"]
    compiled -- no --> keep["old UI stays with a red banner, or the error list"]
```

**The tool path.** `create_app` (`src/bombadil/appkit/tools.py:167`) calls `apps.create` (`src/bombadil/apps.py:101`). The title becomes the name (`slug`, which must match `^[a-z0-9][a-z0-9-]{0,40}$`). Every key of `files` is checked first: a plain relative path, never under `data/`, no hidden parts, one of `.qml .js .mjs .json .txt .svg`, and not `main.qml`. A refused call writes nothing. Then extra files are written first, `app.py` if `python` was given, `app.toml`, and `main.qml` last, each through a hidden temp file and a rename, so the reloader never reads half a file. The launcher entry `bombadil-app-<name>.desktop` is written last. `create` never writes under `data/` and never deletes a file, so calling it again with the same title is an edit in place that keeps the saved state.

**Show, then check.** Unless `open` is false, `placement.show` runs before the check, so the person watches the app (or its reload) while the check runs (`tools.py:193-198`). A stopped app is started detached by `apps.run`, with stdout and stderr appended to its log. A running app is not restarted: its own watcher reloads it. `run_check` then runs `bombadil-app check <app folder> --screenshot <state>/apps/<name>.check.png` in a subprocess with a 40 s limit, so a Qt crash never takes the MCP server down. `report` returns the summary as text (`ok`, `errors`, `warnings`, `console`, `size`, `reloaded`, `window`) followed by the screenshot as an image block. The tool catalogue is in [os-mcp.md](os-mcp.md).

**The runtime path.** `bombadil-app run <name>` (`appkit/runtime.py:833`) takes a per-app lock, sends output to the log when it was not started from a terminal, builds the `QGuiApplication` (`engine.make_app`) and a `Host`, and calls `Host.start`, which calls `Host.load` and then starts watching. `load` compiles `main.qml` with a `QQmlComponent` before it touches the screen. Only when that succeeds does it create the root, put it on screen and discard the old root. A failed load leaves whatever was on screen where it was.

**Hot reload.** A `QFileSystemWatcher` covers the app folder (up to 64 folders, skipping hidden folders, `__pycache__`, `node_modules` and the top-level `data/`) and the folder above it, so a folder that is removed and written again is noticed. Any change event there starts a 150 ms debounce. Then the runtime compares a signature (mtime, size, inode) of the watched files, which are `.qml`, `.js`, `.mjs`, `.py` and `.toml` files and `qmldir`, and reloads only if one differs (`runtime.py:30-32,659-668`). At the start of each reload `App.aboutToReload` fires, so every `Store` saves, and the replacement restores from the file. A `Vault` that was unlocked stays unlocked, because its key is cached in memory by file.

What the window shows is one of three things, and `status.json` calls it `showing`:

```mermaid
stateDiagram-v2
    [*] --> errors: first load fails
    [*] --> current: first load succeeds
    errors --> current: a later load succeeds
    current --> previous: a reload fails
    previous --> current: a later load succeeds
    current --> current: a reload succeeds
```

`errors` is the built-in error list (`ERROR_VIEW` in `runtime.py`): the title, "could not load", and each error. `previous` is the old UI with the red banner `AppWindow` draws from `App.lastError`: click it to read the whole error.

**The Python backend.** An optional `app.py` defines `Backend(QObject)`, exposed to QML as the context property `backend`. It is compiled and executed afresh on start, on every change to a `.py` file, and on every reload while it is failing (no `.pyc`, a module named `bombadil_app_<name>_<n>`). The runtime points `backend` at the fresh object only once the edited QML compiles, so a failed reload leaves the old UI on its old backend. Before the old backend is dropped, its `QThread`s are asked to quit and waited for up to 2 s, because Qt aborts the process when a running `QThread` is destroyed; one that does not stop keeps its backend alive (`runtime.py:444-459`). An exception in `app.py` or in a backend slot is an app error shown as `app.py:12: NameError: ...` (`python_error`). The bindings below make `app.py` unnecessary for most apps, and the skill says to prefer them.

### The app folder

```
~/Apps/<name>/
  main.qml     required; the root object is AppWindow
  *.qml *.js   optional components and libraries; EntryRow.qml is used as EntryRow {}
  app.py       optional; a Backend(QObject) class, exposed to QML as `backend`
  app.toml     title, description, icon (written by create)
  data/        the app's saved state; create never writes here
```

`app.toml` holds three strings, written by `apps.create` and read by `apps.read_meta` (an unreadable file reads as `{}`):

| Key | Used by |
|---|---|
| `title` | `App.title`, the window title when the root sets none, the launcher name, and the title `list_apps` shows. The runtime re-reads it on every reload |
| `description` | the launcher comment |
| `icon` | the launcher icon only: the name of a kit icon, turned into the path of `share/qml/Bombadil/icons/<icon>.svg` when that file exists. It is not the icon in the app's title row: that is `AppWindow.icon`, set in QML |

An app of the same name in `~/Apps` takes the place of a built-in one (see [Add a built-in app](#add-a-built-in-app)).

### The runtime: window, drawer, size and saved state

| Concern | What the code does |
|---|---|
| One window per app | The runtime owns a `QQuickWindow` (`_runtime_window`) and parents the `AppWindow` item into it, so a reload swaps the content in the same window: same place, same size, no flicker. A root that is itself a `Window` (first-milestone apps) keeps that window, which is recreated on each reload. Any other root type is an error: "the root object must be AppWindow" |
| Identity | `setDesktopFileName("bombadil-app-<name>")` makes the Wayland `app_id`, which Hyprland rules and the bar match on. The window title follows `AppWindow.title` |
| The drawer | Before the window first maps, `placement.prepare` adds a Hyprland window rule named `bombadil-app-<name>` that opens it floating, centered, at its size, in the special workspace `special:app-<name>`, so it slides in. `show` focuses that workspace, `hide` toggles it off if it is on screen, `toggle` does either, `close` sends SIGTERM and, after 3 s, kills the app with the programs its `Command`s started. Without Hyprland these calls report "Hyprland is not running" and do nothing, `show` still starts a stopped app, and the app opens as a plain window |
| Keys and the bar | `Esc` inside an app hides it and `Ctrl+W` closes it (both in `AppWindow.qml`). The bar draws a chip for each running app and a click toggles its workspace; that is the shell's side, in [shell.md](shell.md) |
| Size | The first size is the root's `width` x `height` after bindings run (560 x 680 when unset), clamped to 240 x 160 .. 3840 x 2160 and shrunk to the focused monitor's room: its logical size less the area the bar reserves, 24 px of margin on each side and 56 px of height for the line above the prompt (`placement.usable_area`, `fit`). After that the size the person leaves is kept. An edit that changes the declared size resizes the window to it |
| Saved size | 600 ms after a resize the runtime writes `<state>/apps/<name>.window.json` (`width`, `height`, `declared`). A size the runtime shrank to fit the screen is not saved, and a saved size is ignored when `declared` no longer matches |
| Saved state | `Store` writes `data/<name>.json` 300 ms after a change and on reload and quit; `Vault` writes `data/<name>.vault`; `TextFile` writes where its `path` says. A built-in app's folder is read-only, so its `data/` lives under `<state>/apps/<name>/data/` |
| One instance | `run` holds an exclusive `flock` on `<state>/apps/<name>.lock`; a second `run` shows the first and exits 0 |
| Leaving | `close()` emits `aboutToReload` once more, saves the size, and destroys the UI so every `Store` saves and every `Command` ends its program before `bombadil-app` leaves with `os._exit` |
| Errors out | After each load, and when messages arrive, the runtime writes `<state>/apps/<name>.status.json` (`app`, `ok`, `loaded`, `errors`, `warnings`, `console`, `reloads`, `showing`, `size`, `pid`, `at`). A launched app logs to `<state>/apps/<name>.log`, moved to `.log.1` past 1 MB. The status and window files are best effort: a full disk costs a log line, not the app |

### The layers

```mermaid
flowchart TB
    app["the app: main.qml, extra qml and js, optional app.py"]
    comps["kit components: share/qml/Bombadil, listed in qmldir"]
    native["native bindings: src/bombadil/appkit/native, registered into the same module"]
    ctl["Bombadil.Style: the Qt Quick Controls restyled"]
    theme["Theme and Fmt: tokens and formatters"]
    shell["the shell: shell/*.qml reads Theme and Diagram"]
    app --> comps
    app --> native
    app -->|"chosen by the engine"| ctl
    comps --> ctl
    comps --> native
    comps --> theme
    ctl --> theme
    shell --> theme
```

`import Bombadil` is one import for two halves that Qt merges: the QML files listed in `share/qml/Bombadil/qmldir` and the Python types registered under the URI `Bombadil` 1.0 before any QML loads. `engine.qml_dirs` lists the `share/qml` of the checkout ahead of the installed tree (`$BOMBADIL_SHARE/share/qml`, default `/usr/share/bombadil/share/qml`), keeping the ones that exist, and `engine.make_engine` puts them on the import path in that order, so a checkout's kit wins over the installed one. Only `Store.qml` among the kit's files has `import Bombadil`; the others resolve the module's types from their own folder. The style is chosen by the engine, so an app never imports it.

### The kit

All 35 entries of `share/qml/Bombadil/qmldir` (two singletons and 33 types), one line each. The properties and signals of each are in `share/skills/bombadil-apps/references/components.md`, which is also what the agent reads; the look in words is in [the design system](../design-system/README.md).

| Group | Component | What it is |
|---|---|---|
| Layout | `AppWindow` | The root: title row (`title`, `subtitle`, `icon`, `actions`), OS palette and font for every control inside, `toast(text, tone)`, the reload banner, `Esc` and `Ctrl+W`. Still accepts the window properties of first-milestone apps (`color`, `font`, `menuBar`, `header`, `footer`, size limits, `closing`) |
| Layout | `Panel` | A card on `Theme.panel` with an optional header (`title`, `subtitle`, `actions`); children stack in a column, and one with `Layout.fillHeight` takes the free height |
| Layout | `ScrollPane` | A vertical scroll area whose children stack in a column as wide as the pane |
| Layout | `Spacer` | Takes the free space along its layout: horizontal in a row, vertical in a column |
| Layout | `Divider` | A hairline, optionally `vertical` |
| Text | `Heading` | A title, `level` 1 (screen), 2 (section) or 3 (group) |
| Text | `Body` | Running text in `Theme.fg` that wraps and fills the layout's width |
| Text | `Caption` | Secondary text in `Theme.muted`, 12 px |
| Text | `Mono` | A read-only, selectable `TextEdit` in the mono font, for paths, ids and hashes |
| Text | `Icon` | A Lucide icon from `icons/` (77 files, `icons/LICENSE`) tinted to `color` |
| Text | `Badge` | A small status pill: `text`, `tone`, `icon` |
| Input | `IconButton` | A square flat icon button with a tooltip, optionally `checkable`, with a `tone` |
| Input | `SearchField` | A `TextField` with a search icon and clear button; `Ctrl+F` focuses it and `Esc` clears it |
| Input | `Form` | A column of `Field`s; with `labelWidth` the labels sit to the left |
| Input | `Field` | A label, one control stretched to its width, and a hint or error; `required` adds a dot |
| Input | `PasswordField` | A `TextField` for secrets: hidden text, an eye button, an optional 4-step strength meter (`showStrength`, `strength` 0 to 4) |
| Input | `Editor` | A code and text editor: mono font, line numbers, syntax colours through `Highlighter`; with `path` it loads and saves through `TextFile`, shows a dot for unsaved changes and saves on `Ctrl+S` |
| Data | `Stat` | A headline number with its label, an optional delta and a `Sparkline` trend |
| Data | `DetailGrid` | Label and value rows in two aligned columns; a row can be `mono`, `copyable` or `secret` (a secret row cannot be selected and its copy clears the clipboard after 30 s) |
| Data | `ItemList` | A selectable, keyboard-navigable list of `ListRow`s over an array or a model; the selection follows the same item when the array is replaced (matched by `id`, `uuid`, `key` or `pid`, else by content); signals `activated` and `contextRequested` |
| Data | `ListRow` | One 44 px row: icon, title, subtitle, trailing text or item; signals `clicked`, `doubleClicked`, `contextRequested` |
| Data | `DataTable` | A sortable table with a sticky header; columns are `{key, title, width, align, format, mono, sortable}`; scroll position and selection survive a replaced `rows` |
| Data | `EmptyState` | What a list or screen shows when it is empty or locked: icon, title, text, an optional action button |
| Dialogs | `ConfirmDialog` | A modal "are you sure" with `title`, `text`, `confirmText`; `danger` makes the confirm button red and gives Cancel the focus; signal `confirmed` |
| State | `Store` | Every property declared on it is saved to `data/<name>.json` and restored before any `Component.onCompleted`; written in QML over `KitFiles` |
| Charts | `Series` | Records `value` every `interval` ms into `values` (up to `capacity`), for a chart or `Sparkline` |
| Charts | `LineChart` | Lines over time on one y axis, drawn on a canvas; legend, hover crosshair and tooltip |
| Charts | `BarChart` | Bars for a few named values, horizontal or vertical |
| Charts | `Sparkline` | A tiny trend line with no axes |
| Charts | `Meter` | A 0..1 fraction as a bar; the fill turns warn above `warnAt` (0.75) and bad above `badAt` (0.9) |
| Charts | `Ring` | The same fraction as a circular gauge |
| Charts | `StackedBar` | One bar split into parts, with a legend |
| Pictures | `Diagram` | Boxes and links from a `spec` in one of four fixed shapes (`chain`, `layers`, `compare`, `timeline`); the shell's card host uses the same component, see [cards-and-pictures.md](cards-and-pictures.md) |
| Singletons | `Theme` | The tokens, `icon(name)`, `tone(name)`, `alpha(color, a)` |
| Singletons | `Fmt` | `bytes`, `percent`, `number`, `compact`, `duration`, `time`, `date`, `dateTime`, `relative`; a missing value formats as a dash glyph (U+2014), never `NaN` |

Dialogs and menus not in this table are Qt's own, drawn by the style below. A kit component never needs `import Bombadil` for `Theme`, `Fmt` or its siblings.

### Theme and the style

`share/qml/Bombadil/Theme.qml` is a singleton of tokens: surfaces (`bg`, `panel`, `raised`, `overlay`, `sunken`), borders, ink (`fg`, `muted`, `faint`), the accent and the status tones, eight chart colours in `series`, shape and rhythm (`radius`, `radiusSmall`, `pad`, `gap`, `controlHeight`, `rowHeight`), type (Inter, `textSize` 14) and motion (`fast`, `normal`, `slow`). No QML file in `share/qml/Bombadil/` other than `Theme.qml` has a hex colour or a named colour. The shell reads the same file (`import Bombadil as Kit`, with `bin/bombadil-shell` putting `share/qml` on the import path), and `tests/test_theme.py` keeps `shell/DeskTheme.js` equal to the tokens it names.

The style is what makes plain Qt Quick controls look like the OS:

- `engine.configure` sets `QT_QUICK_CONTROLS_STYLE=Bombadil.Style` unless it is set, and `make_app` calls `QQuickStyle.setStyle("Bombadil.Style")` and sets the application font to Inter at 14 px.
- `share/qml/Bombadil/Style/qmldir` registers 41 controls, each on a Qt Quick Templates base (`T.Button`, `T.TextField`, ...) that reads `Theme`: the buttons, text inputs, `ComboBox`, `SpinBox`, checks and radios, `Switch`, sliders, `ProgressBar`, tabs, menus, `Popup`, `Dialog`, `Drawer`, delegates, `Label`, containers, scroll bars, `SplitView`, `StackView`, `ToolTip` and `ApplicationWindow`. Three internal types, `FocusRing`, `Shadow` and `EditMenu`, are shared by them.
- A control the style does not list falls back to Qt's Basic style, which reads the palette. `AppWindow` writes Theme colours into the palette roles of its frame and of its host window (so popups see them), and `Style/ApplicationWindow.qml` does the same for a plain `ApplicationWindow`.
- Two properties go beyond Qt's API: `Button.danger` and `MenuItem.danger` (destructive, red). `highlighted` is the one primary button and `flat` has no surface until hovered.

### Native bindings

Ten Python types are registered into the `Bombadil` module by `appkit/native/__init__.py`. `register(ctx)` runs once per process, from `engine.make_engine`, and calls each module's own `register(ctx)`. They import PySide6, which is why only `engine`, `runtime`, `check` and `native` do: agentd and os-mcp never load Qt.

| Type | Registered as | What it gives QML | Registered in |
|---|---|---|---|
| `App` | singleton instance, made at registration | `name`, `title`, `dir`, `dataDir`, `checking`, `reloads`, `lastError`; `show()`, `hide()`, `toggle()`, `close()`, `notify(title, body)`, `openUrl(url)`; the signal `aboutToReload` | `native/app.py:127` |
| `KitFiles` | singleton, made on first use | the Python half of `Store`: `resolve`, `exists`, `readText`, `writeText`, `storeKeys`, `snapshot`, `quarantine`, `remove`. Not for apps | `native/files.py:205` |
| `System` | singleton, made and polling on first use | `cpu`, `cpus`, `cpuCount`, `memory`, `memoryUsage`, `meminfo`, `pressure`, `load`, `uptime`, `processCount`, `disks`, `network`, `battery`, `temperature`, `hostname`, `kernel`, `user`, `interval`, read from `/proc` and `/sys` every second (disks, battery, host and user every 10 s) | `native/system.py:311` |
| `Processes` | type | `interval`, `sortBy`, `descending`, `limit`, `filter`, `list`, `count`, `refresh()`, `details(pid)`, `kill(pid, signal)` | `native/processes.py:296` |
| `Command` | type | `command` or `program` with `args`, `running`, `interval`, `stdin`, `interactive`, `stdout`, `stderr`, `exitCode`, `lines`, `json`, `run()`, `kill()`, `write()`, `finished(code, output)`; runs without blocking, in a session of its own, ended with the UI | `native/command.py:365` |
| `TextFile` | type | `path`, `text`, `exists`, `error`, `watch`, `save()`, `reload()`, `remove()`, `changedOnDisk`; every `TextFile` shares one inotify watcher | `native/textfile.py:216` |
| `Vault` | type | an encrypted JSON value (scrypt and AES-256-GCM) in `data/<name>.vault`: `name`, `exists`, `unlocked`, `data`, `error`, `autoLock`, `create()`, `unlock()`, `lock()`, `changePassword()`, `generatePassword()`, `strength()` | `native/vault.py:493` |
| `Clipboard` | singleton, made on first use | `text`, `copy(text, clearAfterSeconds)` | `native/clipboard.py:62` |
| `Agent` | singleton, connects when made | `connected`, `busy`, `provider`, `reply`, `ask(prompt)`, `replied(text)`: a line to agentd's socket, so the app talks to the same session as the bar, prefixed `[from app <name>]` ([agentd.md](agentd.md)) | `native/agent.py:176` |
| `Highlighter` | type | `textDocument`, `language` (`plain`, `markdown`, `python`, `json`, `qml`, `javascript`, `shell`, `toml`, `ini`, or a file name or extension) | `native/highlighter.py:268` |

Rules every type follows: nothing is written, connected or sent when `ctx.check` is true (`bombadil-app check`); arrays and objects reach QML as real JS values through `js_value` and an empty value as `null` through `to_js`; a path is `~`-expanded, and a relative path resolves inside `data/` (`AppContext.resolve`), except for a `Command`, which runs in the home folder. Units are in `share/skills/bombadil-apps/references/native.md`.

### `bombadil-app check`

`bombadil-app check <name|folder|file.qml> [--screenshot PNG] [--wait MS] [--size WxH]` loads an app through the same `Host` as `run`, so the picture is what `run` shows.

1. `check.main` copies stdout aside and points fd 1 at stderr, so only the JSON result reaches stdout and what `app.py` prints cannot break it. A watchdog process is forked before Qt starts.
2. `engine.configure(check=True)` sets `QT_QPA_PLATFORM=offscreen`, `QT_QUICK_BACKEND=software` (unless set) and `BOMBADIL_CHECK=1`, and Qt's own storage (`Settings`, `LocalStorage`) goes to its test location, not the person's configuration.
3. `Host.start` loads once, with no watcher and no placement. The check then runs the event loop for `--wait` ms (default 1200), resizes to `--size` if given, and saves `grabWindow()` to the PNG.
4. A `Collector` sorts every message into `errors`, `warnings` and `console`. QML load errors and `engine.warnings` arrive with `file:line`, and paths are shown relative to the app. A runtime message is an error when it matches `FATAL` (for example `Error:`, "is not defined", "Cannot assign", "Binding loop", "is not a type") and a warning otherwise; Qt platform noise matching `NOISE` is dropped; `console.log` goes to `console`.
5. The result is `{app, ok, loaded, errors, warnings, console, screenshot, size, seconds}`, with at most 20 entries of each list. The exit code is 0 when `ok`, 1 otherwise. For example, an `AppWindow` with `Panel { nosuchprop: 3 }` returns `"main.qml:8:13: Cannot assign to non-existent property \"nosuchprop\""` and `ok: false`.
6. If the app does not settle within `--wait` plus 25 s (a loop that never ends), the watchdog stops the check, kills the programs its `Command`s started, prints the result the check could not (`ok: false`, "the app did not settle ...") and kills it.

A check is read-only. `Store`, `Vault` and `TextFile` keep their writes in memory or drop them, `Agent` never connects, `Clipboard.copy`, `App.notify`, `App.openUrl` and the window calls do nothing, and `Processes.kill` returns false. `Command`s do run, so the screenshot shows real data. `App.checking` and `BOMBADIL_CHECK=1` let an app or the programs it starts tell.

### The skill and the examples

The skill `share/skills/bombadil-apps/` teaches the agent to build an app in one pass:

| File | Holds |
|---|---|
| `SKILL.md` | the loop (pick a pattern, write `main.qml`, `create_app`, read the errors and the screenshot), a skeleton, the rules that prevent the usual failures (four imports, `AppWindow` root, `Layout.*` not anchors, `Theme` not colours, assign never mutate, no `QtCharts` or `QtWebEngine`), a table of patterns and the kit at a glance |
| `references/components.md` | every component with its properties, the styled controls, `Theme`, `Fmt` and the icon list |
| `references/native.md` | every native binding |
| `references/runtime.md` | the contract: the app on disk, loading and hot reload, window size, where it shows up, errors and logs, commands and tools |
| `examples/memory`, `examples/password-manager` | two complete apps (an `app.toml`, a `main.qml` and components) |

It reaches both agent CLIs three ways. `iso/airootfs/etc/skel/.claude/skills/bombadil-apps` and `iso/airootfs/etc/skel/.agents/skills/bombadil-apps` are links to `/usr/share/bombadil/share/skills/bombadil-apps` (the ISO smoke test checks both, `iso/airootfs/usr/local/bin/bombadil-smoke:99`; which CLI reads which folder is that CLI's own convention). `providers.system_prompt()` tells both where the kit and the skill are (`kit_paths()`) and to call `app_guide` first. And `app_guide(topic?)` serves it over MCP, so a provider with no skill support gets it too: with no topic it returns `SKILL.md` and a footer naming the topics; a topic is a reference's file name without `.md` (`components`, `native`, `runtime`) or an example's folder name, which returns each `.qml`, `.js`, `.py`, `.toml` and `.md` file of the example as a fenced block. Topics are read from the folder on each call, so a file added to `references/` becomes a topic with no code change.

The examples are built from the skill alone. `memory` shows live RAM (`System`, `Series`, `Processes`, `LineChart`, `StackedBar`, `DataTable`, and a `ProcessDetails.qml` component). `password-manager` is a `Vault` behind a lock screen (`PasswordField`, `ItemList`, `DetailGrid`, `ConfirmDialog`, `LockScreen.qml`, `EntryDialog.qml`). Both load with `ok: true` under `bombadil-app check`, as does `share/app-template/` (the starter `app_template` returns: a `Store`, an `ItemList` and a toast in `main.qml`, and an `app.py` with a `Backend` that `main.qml` does not use). The ISO smoke test copies each example into `~/Apps` and opens it with `open_app` (`iso/airootfs/usr/local/bin/bombadil-smoke:96`).

## Interfaces other pieces depend on

**Commands** (`bin/bombadil-app`, `src/bombadil/appkit/cli.py`)

| Command | Does |
|---|---|
| `bombadil-app run <name>` | opens the app and hot reloads it; when it is already running, shows it and exits 0 |
| `bombadil-app check <target> [--screenshot PNG] [--wait MS] [--size WxH]` | the check above; `target` is an app name, an app folder or a `.qml` file (a gallery); JSON on stdout; exit 0 when `ok` |
| `bombadil-app list` | one line per app in `~/Apps`: name, `shown` or `running` or blank, title, path |
| `bombadil-app create <title> <qml> [--python F] [--description T] [--icon NAME]` | `apps.create` from files; prints the folder (there is no `--files`) |
| `bombadil-app show`, `hide`, `toggle`, `close <name>` | `appkit/placement.py`; `show` starts the app if needed; `close` is what a bar chip's x runs |
| `bombadil-app status <name>` | `status.json` merged with `running` and the last 40 lines of the log, as JSON. No Qt |

**Agent tools** registered by `appkit/tools.py:147`, described in [os-mcp.md](os-mcp.md): `app_guide`, `create_app`, `check_app`, `open_app`, `show_app`, `hide_app`, `close_app`, `app_status`, `list_apps`, `app_template`. They replace the four first-milestone app tools of `mcp_server.py` and are listed together at the end.

**Environment variables**

| Variable | Meaning |
|---|---|
| `BOMBADIL_APPS` | the apps folder, default `~/Apps` (`paths.apps_dir`) |
| `BOMBADIL_STATE` | the state folder, default `~/.local/state/bombadil`; apps use `<state>/apps/` |
| `BOMBADIL_SHARE` | the installed tree, default `/usr/share/bombadil`; where the kit and skill are found when the checkout has none |
| `BOMBADIL_SOCKET`, `BOMBADIL_RUNTIME` | where `Agent` finds agentd's socket |
| `XDG_DATA_HOME` | where the `.desktop` entry goes, under `applications/` |
| `BOMBADIL_CHECK` | `1` during a check (set by `engine.configure`, removed for `run`); read by `app.py` and the programs `Command`s run |
| `QT_QUICK_CONTROLS_STYLE` | defaults to `Bombadil.Style` |
| `QT_QPA_PLATFORM`, `QT_QUICK_BACKEND` | a check sets `offscreen` and, unless set, `software` |

**The compositor contract.** Window class and `app_id` `bombadil-app-<name>`; special workspace `special:app-<name>`; a runtime window rule per app, named `bombadil-app-<name>`, added by `placement.prepare`; and a static rule `bombadil-apps` in `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` that floats and centers every `bombadil-app-*` window at 540 x 660. The shell builds its chips from toplevels in `special:app-*` workspaces ([shell.md](shell.md)).

**Files an app and its tools read and write** are listed under Where state lives. **The QML contract** an agent writes against is `import Bombadil` with an `AppWindow` root, `backend` when `app.py` exists, and the kit and native names above.

## Where state lives

| What | Where | Written by |
|---|---|---|
| The app | `~/Apps/<name>/` (`main.qml`, extra files, `app.py`, `app.toml`) | `apps.create`, or by hand |
| The app's saved state | `~/Apps/<name>/data/`: `<store>.json` (default `state.json`), `<vault>.vault` (default `vault.vault`, mode 600), a `TextFile`'s relative path | `Store`, `Vault`, `TextFile`; never `create` |
| A built-in app | `share/apps/<name>/`, read-only; its saved state in `<state>/apps/<name>/data/` | shipped with the tree |
| Launcher entry | `$XDG_DATA_HOME/applications/bombadil-app-<name>.desktop` (default `~/.local/share/applications`) | `apps.create` only |
| Log | `<state>/apps/<name>.log`, then `.log.1` | the running app, or `apps.run` |
| Last load result | `<state>/apps/<name>.status.json` | the running app |
| Window size | `<state>/apps/<name>.window.json` | the running app |
| Single-instance lock | `<state>/apps/<name>.lock` | the running app |
| Last agent check picture | `<state>/apps/<name>.check.png` | `tools.run_check` |
| Vault keys | memory of the app process, by file; survive a hot reload, not a restart or the `autoLock` deadline | `Vault` |
| The drawer | the Hyprland special workspace `special:app-<name>` and its window rule | `placement` |
| Qt's own storage during a check | Qt's test location under the home folder | Qt |

`<state>` is `paths.state_dir()`. Apps are in the home folder, which no restore point covers ([restore-points.md](restore-points.md)).

## Principles it keeps

- [Real native apps](../principles.md#real-native-apps): an app is a folder of QML in a real window, opened by name, with no server and no port. The agent's tool is `create_app`, and the skill forbids web pages, `QtWebEngine` and terminal UIs. The trap is a feature that only works as a local web page or a web view: it does not belong in the kit.
- [One design language](../principles.md#one-design-language): every component and every style control reads `Theme`, and plain Qt controls are restyled so an app that never mentions colour still matches the shell. The trap is a hex colour or a one-off radius in a component or an app: nothing but this convention stops it in the kit (`tests/test_theme.py` guards only the shell). `Highlighter` is an example of drift: it holds its own hex palette in Python.
- [Degrade and recover](../principles.md#degrade-and-recover): a reload that fails leaves the last working UI up with the reason; an app that never loaded shows its errors; without Hyprland an app opens as a plain window; a full disk costs the log, not the app. The trap is Qt leaking out of the kit: `agentd` and os-mcp must not import `engine`, `runtime`, `check` or `native`, which import PySide6.
- [Plain files stay the truth](../principles.md#plain-files-stay-the-truth): an app is text files in a folder, written atomically, and `data/` is never written by `create`. A `Store` keeps a saved value it cannot take, and a file that is not valid JSON is renamed `.bad`, never overwritten. The trap is a "cleanup" that trims the saved file to the declared properties or writes into `data/`.
- [Nothing runs unseen](../principles.md#nothing-runs-unseen): a running app is a chip the person can close, a `Command` ends with the UI that made it, a check changes nothing, and `Agent.ask` marks its prompt `[from app <name>]`. The trap is a native type that starts background work that outlives its object, or that acts during a check.

## Extending it

### Add a kit component

1. Write `share/qml/Bombadil/<Name>.qml`. Use `Theme`, `Fmt` and sibling components by name, and no colour literals. Import `QtQuick` and, if needed, `QtQuick.Controls` and `QtQuick.Layouts`. The module's own types, native ones included, resolve without `import Bombadil` (`AppWindow.qml` uses `App`, `Editor.qml` uses `TextFile`); only `Store.qml` has that import.
2. Add `<Name> 1.0 <Name>.qml` to `share/qml/Bombadil/qmldir` (`singleton <Name> 1.0 <Name>.qml` for a singleton). Without the line an app's `import Bombadil` reports "<Name> is not a type" (checked on a copy of the kit on 2026-10-01).
3. If it needs an icon, add `share/qml/Bombadil/icons/<name>.svg` and its name to the icon list at the end of `references/components.md`.
4. Document it: a `### <Name>` section with a property table under the matching group in `share/skills/bombadil-apps/references/components.md`, and its name in "The kit at a glance" of `SKILL.md` (and the patterns table if it adds a shape). `app_guide("components")` serves that file, so no code changes.
5. Use it in a gallery, `tests/qml/components_gallery.qml` (`charts_gallery.qml` for a chart), in each state it has. Run `bin/bombadil-app check tests/qml/components_gallery.qml --screenshot out.png` and expect `"ok": true` with no errors or warnings, and look at the picture.
6. If it has logic (selection, keys, focus), add a test to `tests/test_appkit_kit.py` with `run(home, qml, body)`.

Nothing enforces steps 2, 4 and 5 except the first use: no test compares `qmldir` with the reference or the galleries. `Store` and `Diagram` are in no gallery (`Diagram` has `tests/test_diagram_qml.py`).

### Add a native binding

1. Write `src/bombadil/appkit/native/<thing>.py` with a `QObject` class and a module-level `register(ctx)`. Import `MAJOR, MINOR, URI` from the package. Choose how it is made:
   - a type QML creates, one per use: `qmlRegisterType(Cls, URI, MAJOR, MINOR, "Name")`; read the context in `__init__` with `native.context()` (`Command`, `Processes`, `TextFile`, `Vault`, `Highlighter`);
   - a singleton made on the first mention in QML: `qmlRegisterSingletonType(Cls, URI, MAJOR, MINOR, "Name", lambda engine: Cls(ctx))` (`System`, `Clipboard`, `Agent`, `KitFiles`);
   - an instance the runtime also needs: `qmlRegisterSingletonInstance` (only `App`).
2. Register the module: add it to the import on line 23 and to the tuple on line 25 of `src/bombadil/appkit/native/__init__.py`. That is the only registration point; `engine.make_engine` calls it before the engine exists.
3. Honour `ctx.check`: no writes, no sockets, no processes you would not want in a screenshot (see `KitFiles.writeText`, `Agent.start`, `Processes.kill`).
4. Give QML real values: `js_value(self, data)` for arrays and objects, `to_js` so `None` becomes `null`, `from_js` for what QML passes in. End anything you started when the object is destroyed (`Command` does it in `destroyed`), because a hot reload destroys objects.
5. Document it in `share/skills/bombadil-apps/references/native.md` and the "Native:" line of `SKILL.md`, and add it to the comment at the top of `share/qml/Bombadil/qmldir`.
6. Test it in `tests/test_appkit_native.py` (the `kit` and `checking` fixtures), with a case for check mode.

### Add a built-in app

There is a place for one and no app in it on `main`. Put the app in `share/apps/<name>/` (`main.qml`, `app.toml`, optional `app.py` and components); `apps.builtin_dir()` is `share/apps` at the root of the tree, and `scripts/build-iso.sh` copies the whole `share/` tree to `/usr/share/bombadil`, so nothing else ships it. Then:

- `bombadil-app run <name>` and `apps.load(name)` find it only when `~/Apps/<name>/main.qml` does not exist; an app of the person's with that name takes its place, and the built-in files are never changed.
- `apps.list_apps()` lists `~/Apps` only, so a built-in app is not in `list_apps`; a tool that needs it names it.
- Its saved state is in `<state>/apps/<name>/data/`, because its folder is read-only (`AppContext.data_dir`).
- `apps.create` is the only code that writes a launcher entry, so a built-in app has none unless something writes one.
- Check it with `bin/bombadil-app check share/apps/<name> --screenshot out.png`, and copy the pattern of `tests/test_apps.py::test_a_built_in_app_runs_unless_you_have_one_of_that_name`.

### Add a field to `app.toml`

1. Add the field, with a default, to the `App` dataclass in `src/bombadil/apps.py`, read it in `apps.load`, and write it in `apps.create` (through `_toml_str`, which escapes any text). Add the argument to `create`.
2. Expose it where it is set: the `create_app` input schema in `appkit/tools.py` (`tests/test_appkit_tools.py` asserts the exact set of properties) and a flag on `bombadil-app create` in `appkit/cli.py`.
3. If QML should see it, carry it on `AppContext` (`appkit/context.py`, including `for_target`, which reads `title` itself) and add a constant `Property` to `App` in `appkit/native/app.py`. If it should follow edits on reload, update it in `Host._refresh_meta` as `title` is.
4. Document it: "An app on disk" in `references/runtime.md` and the `App` table in `references/native.md`.
5. Add the field to the round trip in `tests/test_apps.py::test_toml_escaping_survives_any_title`.

## Tests

| File | Covers |
|---|---|
| `tests/test_apps.py` | names and slugs, `create` and what it refuses, TOML escaping, the launcher entry, built-in apps and their data folder |
| `tests/test_appkit_tools.py` | the app tools: `create_app` show-then-check, a failed check, `app_guide` topics, tool order and schema |
| `tests/test_appkit_placement.py`, `tests/test_appkit_runtime.py` | Hyprland requests, retries, `close`, `usable_area`, hot reload, saved size, the error view, status, log, check read-only and the watchdog, Store across edits |
| `tests/test_appkit_reload.py` | backend reload, a backend's QThread, the old UI as it leaves, a removed folder, full disk, log rotation |
| `tests/test_appkit_kit.py` | `AppWindow`, `ItemList`, `DataTable` selection and touch, `Theme.alpha`, `Fmt`, `Store` |
| `tests/test_appkit_native.py`, `tests/test_appkit_agent.py` | every native type, in and out of check mode; the `Agent` protocol |
| `tests/test_diagram_qml.py`, `tests/test_theme.py` | `Diagram` shapes; the shell reading the kit's tokens |
| `tests/qml/*.qml` | galleries and a demo app, loaded by hand; pytest does not run them |

```sh
pytest -q tests/test_apps.py tests/test_appkit_tools.py tests/test_appkit_placement.py
pytest -q tests/test_appkit_kit.py tests/test_appkit_runtime.py tests/test_appkit_reload.py \
  tests/test_appkit_agent.py tests/test_appkit_native.py tests/test_diagram_qml.py
bin/bombadil-app check tests/qml/style_gallery.qml --screenshot gallery.png
```

The second command needs PySide6, and the `Vault` tests need `cryptography`. On 2026-10-01 the first passed 31 tests and the second 170. `style_gallery.qml`, `components_gallery.qml`, `charts_gallery.qml` (with `--wait 2500`) and `appwindow_demo.qml` each loaded with `ok: true` and no errors or warnings. How to set up and run everything is in [development.md](../contributing/development.md).

## Known gaps

- **No history or undo for an app.** The design (UX brief, piece 3) makes each app folder a git repository with a commit per turn. Nothing in `src/bombadil/appkit/` or `apps.py` uses git, and `~/Apps`, with each app's `data/`, is in no restore point, so undoing a turn that built an app leaves the app in place ([restore-points.md](restore-points.md)).
- **The two system-prompt rules are not there.** The design adds "build the skeleton first" and "change existing apps with the edit tool". `providers.system_prompt()` has neither; the skill tells the agent to call `create_app` again with the whole file.
- **Edits only add.** `apps.create` never deletes a file, and an `app.py` stays when `python` is omitted (`apps.py:112-118`). No app tool deletes an app or a file in it, so the agent uses the shell for that.
- **`src/bombadil/app_runtime.py` has no caller.** It is a seven-line shim "kept for callers of the first milestone"; nothing in the tree imports it.
- **Three styled controls are in no gallery or test:** `Drawer`, `StackView` and `ScrollIndicator` (`share/qml/Bombadil/Style/`).
- **`Highlighter` keeps its own hex palette** (`native/highlighter.py:18-36`). Only some entries equal `Theme` tokens, and no test ties the two, so a change of theme leaves the editor's colours behind.
- **`appDir` is set and unused.** `engine.make_engine` sets a root context property `appDir` (`engine.py:69`) that no QML, test or skill file uses; `App.dir` is the documented way.
- **`pyproject.toml` does not declare `cryptography`.** The `apps` extra lists PySide6 only (`pyproject.toml:9`), and `Vault` needs `cryptography` (`native/vault.py:114`); the ISO installs `python-cryptography`.
- **A first-milestone cascade placement is still in the tree.** `Hyprland.place_app` and `app_slots` in `src/bombadil/hypr.py` are called only by code the kit has replaced: the fallback in `launcher.py` for when `appkit.placement` cannot be imported (`_placement()`, line 671) and the first-milestone `create_app` and `open_app` in `mcp_server.py` (lines 85 and 92), which `appkit.tools.register` pops from the tool table; see [os-mcp.md](os-mcp.md).

Other open items are in [known-issues.md](../known-issues.md).

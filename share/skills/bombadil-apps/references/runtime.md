# The app runtime contract

What `bombadil-app` promises an app, and what an app has to do in return.

## An app on disk

```
~/Apps/<name>/
  main.qml     required. Root object is AppWindow.
  *.qml        optional extra components; EntryRow.qml is usable as `EntryRow {}` in main.qml
  *.js         optional JS libraries (`import "util.js" as Util`)
  app.py       optional Python: a `Backend(QObject)` class, exposed to QML as `backend`
  app.toml     title, description, icon (written by create_app)
  data/        the app's saved state (Store, Vault, TextFile). create_app never touches it
```

`<name>` is the title lowercased with dashes (`"Password Manager"` → `password-manager`).
Calling `create_app` again with the same title updates the same app.

Some apps ship with the OS (`/usr/share/bombadil/share/apps/<name>/`, read-only). They run
by name like yours (`bombadil-app run <name>`), keep their saved state in
`~/.local/state/bombadil/apps/<name>/data/`, and are not listed among your apps. An app of
your own with the same name takes its place.

## Loading and hot reload

- The runtime owns the native window; `main.qml`'s `AppWindow` is placed inside it, and
  the window's title follows `AppWindow.title` (its size: see Window size below).
- Every write to a `.qml`, `.js`, `.py`, `qmldir` or `app.toml` in the app directory (or a
  folder in it) reloads the UI within ~0.2 s, in the same window (no flicker, same size,
  same place). Writes under `data/` never trigger a reload.
- A change to `app.py` re-imports it first, but QML sees the new `backend` only once the
  new `main.qml` compiles. If it does not compile, the old UI stays up on its old
  `backend`, and the next reload that compiles (even a QML-only edit) uses the new one. A
  `main.qml` that compiles but then fails to create (a `QtObject` root, a required
  property left unset) still switches `backend`, so the old UI that stays up then runs on
  the new one. Nothing the old UI reports while it switches over is counted as an error,
  including errors from an old `backend` it kept in a property (deleted, it reads as
  `null`). While `app.py` fails to load, every reload tries it again and its error stays
  in the status until it is fixed, also across QML-only edits (the last good version keeps
  running meanwhile). A `Backend` whose `__init__` never calls `super().__init__()` is
  such an error (`TypeError: Backend is not usable: its __init__ never calls
  super().__init__()`): QML cannot use it.
- The old UI is unfocused before the new one is created. Its focus-loss handler (say
  `onActiveFocusChanged: if (!activeFocus) backend.save(text)`) runs once, at that point,
  on the `backend` the new UI will use, and what it reports is not counted, whether or not
  the new `main.qml` takes focus as it is created (`Component.onCompleted:
  field.forceActiveFocus()`). Once the new UI is up, the old one is destroyed before `App.reloads`
  and `App.lastError` change, so its bindings on them never run. If the new UI fails to
  load, the old UI stays and gets its focus back.
- On an `app.py` reload the `QThread`s of the replaced `Backend` (its children, or held as
  attributes of it) are asked to `quit()` and waited on for up to 2 s, because Qt aborts
  the whole app when a running `QThread` is destroyed. A thread that has not stopped is
  logged and its old `Backend` is kept alive rather than destroyed, so a thread should end
  when asked: the default `run()` (an event loop) stops on `quit()`, a `run()` of your own
  must return by itself. A `Backend` that is not a `QObject` is left alone: nothing is
  stopped or waited on. Python `threading` threads are not touched.
- The app's folder being removed or moved away and then written again (`rm -rf
  ~/Apps/<name>`, then `create_app`) is picked up by the running app: while the folder is
  gone the old UI stays up with the error `main.qml: No such file or directory`, and it
  reloads by itself when the files are back, with no restart needed. A running app with a
  Store writes `data/<name>.json` back into the folder at once, so `rm -rf` does not clear
  its saved state; to start from scratch, `close_app` it first (or call the Store's
  `reset()`).
- If the new version fails to load, the old UI stays up with a red banner showing the
  first error, and the error is recorded (see below). Fix the file and it reloads.
- If the app has never loaded successfully, the window shows the error list instead.
- `Store` state is saved before each reload and restored after it, so the app comes back
  where it was. A `Vault` that was unlocked stays unlocked across reloads (not across
  restarts, and not past its `autoLock`).
- An edit never costs saved data: a Store keeps the saved value of a property the new
  version no longer declares (renamed `entries` to `items`; kept until `reset()`) or cannot
  take (a new type; kept until the app sets that property) in its file, so undoing the
  edit brings it back. A `data/<name>.json` that is not valid JSON is renamed to
  `<name>.json.bad` (`.bad.2`, ... if that exists) and the Store starts from its defaults.

## Window size

- The first size is `AppWindow.width` × `height` (bindings count: `width: Theme.pad * 30`;
  560 × 680 when not set). After that the size the user leaves the window at is kept
  between runs. An edit that changes `width`/`height` resizes the window to the new values.
- Design for about 1200×760 at most; bigger windows are shrunk to fit the screen (the
  focused monitor without the bar, less a 24 px margin and room for the finished line above
  the prompt), so lay out with `Layout`s that
  can shrink rather than fixed sizes.

## Where it shows up

- Each app lives in its own slide-in drawer (a Hyprland special workspace named
  `app-<name>`), floating and centered at its own size. Opening the app, or updating it
  with `create_app`, slides it in; `Esc` inside the app, or clicking its chip in the bar,
  slides it out. Only one drawer (an app or the browser) is shown at a time.
- The bar shows a chip for every running app, labelled with its `AppWindow.title`; the
  highlighted chip is the one on screen.
  Clicking a chip toggles that app, its × closes it.
- `Ctrl+W` or the chip's × quits the app; state is saved first. When the × (or
  `close_app`) finds the app has not quit after 3 s (stuck in a loop), it kills it
  together with the programs its Commands started, and unsaved changes are lost.

## Errors and logs

- `bombadil-app check <name>` loads the app offscreen, lets it run for 1200 ms
  (`--wait MS`, default 1200) and prints JSON:
  `{ ok, loaded, errors, warnings, console, screenshot, size }`. Errors are QML load
  errors and runtime JS errors (`ReferenceError`, `TypeError`, bad assignments, binding
  loops) with `file:line`. `console.log` output is in `console`. The check's stdout
  carries only the JSON result: what `app.py` prints (`print()`, or programs it starts
  writing to stdout) goes to stderr during a check and is not in `console`, so use
  `console.log` in QML for output that should appear in the result. `create_app` runs this
  for you and returns the result with the screenshot. A check still busy 25 s after the
  wait (a loop that never ends) is stopped, its Commands are killed, and the result says
  the app did not settle.
- A running app writes `~/.local/state/bombadil/apps/<name>.status.json` after every
  (re)load, and again when new errors or console output arrive: `{ ok, loaded, errors,
  warnings, console, reloads, showing, size, at }`, where `showing` is `"current"`,
  `"previous"` (the last reload failed; the old UI is up) or `"errors"` (it never
  loaded). Exceptions raised in `app.py` count as errors (`app.py:12: NameError: ...`).
- The status file and `<name>.window.json` (the saved size) are best-effort: if they
  cannot be written (a full disk), the app still starts and runs, and the log says
  `cannot write <name>.status.json: ...` (or `cannot save the window size: ...`) when
  the log has room (on a full disk it is usually full too).
  `app_status` then shows the last status that was written, which may be stale.
- A running app's stdout/stderr go to `~/.local/state/bombadil/apps/<name>.log`, unless
  it was started from a terminal (then it prints there). The log is moved to `.log.1`
  past 1 MB, at start and while the app runs (checked about every half second).
  `app_status` and `bombadil-app status` show its last 40 lines, read from its last
  64 KiB, so a very long last line can mean fewer lines are shown. `print()` output is
  line-buffered: each line reaches the log as it is printed, so the status shows it while
  the app runs. When the log is on a full disk, what it has no room for (console.log
  lines, errors, tracebacks, `app.py`'s own prints) is dropped from it; the app keeps
  running, and errors and console lines still reach the status file when that can be
  written.
- Check mode is read-only: Store, Vault and TextFile never write, window/agent/clipboard
  calls do nothing, and Qt's own storage (QtCore `Settings`, `LocalStorage`) goes to a
  throwaway test location (`~/.qttest`) instead of the user's `~/.config` and
  `~/.local/share`. `Command`s do run so the screenshot has real data. `App.checking` is
  true if an app wants to skip something during a check, and the check's environment has
  `BOMBADIL_CHECK=1`, so `app.py` (`os.environ`) and the programs Commands run can tell too.

## Commands

```
bombadil-app run <name>              open (hot reloads on edit); if it is running, show it
bombadil-app check <name|dir|file.qml> [--screenshot out.png] [--size WxH] [--wait MS]
bombadil-app show|hide|toggle|close <name>
bombadil-app status <name>           last load result + log tail
bombadil-app list
```

## The agent's tools (bombadil-os MCP server)

| Tool | |
|---|---|
| `app_guide(topic?)` | this guide; `topic` = `components`, `native`, `runtime`, or an example name |
| `create_app(title, qml, files?, python?, description?, icon?, open?)` | write the app, check it, open or reload it; returns the check result and a screenshot |
| `check_app(name)` | check again and get a fresh screenshot of the running app's QML |
| `open_app(name)` / `show_app(name)` / `hide_app(name)` / `close_app(name)` | |
| `app_status(name)` | the running app's last load result and log tail |
| `list_apps()` | every app with `running` and `shown` |

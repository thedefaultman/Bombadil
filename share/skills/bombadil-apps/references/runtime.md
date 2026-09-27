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

## Loading and hot reload

- The runtime owns the native window; `main.qml`'s `AppWindow` is placed inside it, and
  the window's title follows `AppWindow.title`. Its size: see below.
- Every write to a `.qml`, `.js`, `.py`, `qmldir` or `app.toml` in the app directory (or a
  folder in it) reloads the UI within ~0.2 s, in the same window (no flicker, same size,
  same place). Writes under `data/` never trigger a reload.
- A change to `app.py` re-imports it and makes a new `backend` before the QML reloads.
  While `app.py` fails to load, every reload tries it again and its error stays in the
  status until it is fixed (the last good version keeps running meanwhile).
- If the new version fails to load, the old UI stays up with a red banner showing the
  first error, and the error is recorded (see below). Fix the file and it reloads.
- If the app has never loaded successfully, the window shows the error list instead.
- `Store` state is saved before each reload and restored after it, so the app comes back
  where it was. A `Vault` that was unlocked stays unlocked across reloads (not across
  restarts).
- An edit never costs saved data: a Store keeps the saved value of a property the new
  version no longer declares (renamed `entries` to `items`) or cannot take (a new type) in
  its file until the app sets that property again, so undoing the edit brings it back.
  `reset()` drops them. A `data/<name>.json` that is not valid JSON is renamed to
  `<name>.json.bad` (`.bad.2`, ... if that exists) and the Store starts from its defaults.

## Window size

- The first size is `AppWindow.width` × `height` (bindings count: `width: Theme.pad * 30`;
  560 × 680 when not set). After that the size the user leaves the window at is kept
  between runs. An edit that changes `width`/`height` resizes the window to the new values.
- Design for about 1200×760 at most; bigger windows are shrunk to fit the screen (the
  focused monitor without the bar, less a 24 px margin), so lay out with `Layout`s that
  can shrink rather than fixed sizes.

## Where it shows up

- Each app lives in its own slide-in drawer (a Hyprland special workspace named
  `app-<name>`), floating and centered at its own size. Opening the app, or updating it
  with `create_app`, slides it in; `Esc` inside the app, or clicking its chip in the bar,
  slides it out. Only one drawer (an app or the browser) is shown at a time.
- The bar shows a chip for every running app; the highlighted chip is the one on screen.
  Clicking a chip toggles that app, its × closes it.
- `Ctrl+W` or the chip's × quits the app; state is saved first. An app that does not quit
  within 3 s (stuck in a loop) is killed, and then unsaved changes are lost.

## Errors and logs

- `bombadil-app check <name>` loads the app offscreen, lets it run for 1200 ms
  (`--wait MS`, default 1200) and prints JSON:
  `{ ok, loaded, errors, warnings, console, screenshot, size }`. Errors are QML load
  errors and runtime JS errors (`ReferenceError`, `TypeError`, bad assignments, binding
  loops) with `file:line`. `console.log` output is in `console`. `create_app` runs this
  for you and returns the result with the screenshot. A check still busy 25 s after the
  wait (a loop that never ends) is stopped, its Commands are killed, and the result says
  the app did not settle.
- A running app writes `~/.local/state/bombadil/apps/<name>.status.json` after every
  (re)load, and again when new errors or console output arrive: `{ ok, loaded, errors,
  warnings, console, reloads, showing, size, at }`, where `showing` is `"current"`,
  `"previous"` (the last reload failed; the old UI is up) or `"errors"` (it never
  loaded). Exceptions raised in `app.py` count as errors (`app.py:12: NameError: ...`).
  Its stdout/stderr go to `~/.local/state/bombadil/apps/<name>.log` (moved to `.log.1`
  past 1 MB) however it was started, unless it was started from a terminal.
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

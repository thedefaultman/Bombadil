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

- The runtime owns the native window; `main.qml`'s `AppWindow` is placed inside it. The
  window's title follows `AppWindow.title` and its first size is `AppWindow.width` ×
  `height`; after that the size the user leaves it at is kept between runs. An edit that
  changes `width`/`height` resizes the window to the new values.
- Every write to a `.qml`, `.js`, `.py`, `qmldir` or `app.toml` in the app directory (or a
  folder in it) reloads the UI within ~0.2 s, in the same window (no flicker, same size,
  same place). Writes under `data/` never trigger a reload.
- A change to `app.py` re-imports it and makes a new `backend` before the QML reloads.
- If the new version fails to load, the old UI stays up with a red banner showing the
  first error, and the error is recorded (see below). Fix the file and it reloads.
- If the app has never loaded successfully, the window shows the error list instead.
- `Store` state is saved before each reload and restored after it, so the app comes back
  where it was. A `Vault` that was unlocked stays unlocked across reloads (not across
  restarts).

## Where it shows up

- Each app lives in its own slide-in drawer (a Hyprland special workspace named
  `app-<name>`), floating and centered at its own size. Opening the app, or updating it
  with `create_app`, slides it in; `Esc` inside the app, or clicking its chip in the bar,
  slides it out. Only one drawer (an app or the browser) is shown at a time.
- The bar shows a chip for every running app; the highlighted chip is the one on screen.
  Clicking a chip toggles that app, its × closes it.
- `Ctrl+W` or the chip's × quits the app; state is saved first.

## Errors and logs

- `bombadil-app check <name>` loads the app offscreen for ~1 s and prints JSON:
  `{ ok, loaded, errors, warnings, console, screenshot, size }`. Errors are QML load
  errors and runtime JS errors (`ReferenceError`, `TypeError`, bad assignments, binding
  loops) with `file:line`. `console.log` output is in `console`. `create_app` runs this
  for you and returns the result with the screenshot.
- A running app writes `~/.local/state/bombadil/apps/<name>.status.json` after every
  (re)load, and again when new errors or console output arrive: `{ ok, loaded, errors,
  warnings, console, reloads, showing, size, at }`, where `showing` is `"current"`,
  `"previous"` (the last reload failed; the old UI is up) or `"errors"` (it never
  loaded). Exceptions raised in `app.py` count as errors (`app.py:12: NameError: ...`).
  Its stdout/stderr go to `~/.local/state/bombadil/apps/<name>.log`.
- Check mode is read-only: Store, Vault and TextFile never write, window/agent/clipboard
  calls do nothing. `Command`s do run so the screenshot has real data; `App.checking` is
  true if an app wants to skip something during a check.

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
| `app_guide(topic?)` | this guide; `topic` = `components`, `native`, `runtime`, `patterns`, or an example name |
| `create_app(title, qml, files?, python?, description?, icon?, open?)` | write the app, check it, open or reload it; returns the check result and a screenshot |
| `check_app(name)` | check again and get a fresh screenshot of the running app's QML |
| `open_app(name)` / `show_app(name)` / `hide_app(name)` / `close_app(name)` | |
| `app_status(name)` | the running app's last load result and log tail |
| `list_apps()` | every app with `running` and `shown` |

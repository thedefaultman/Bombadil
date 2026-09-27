---
name: bombadil-apps
description: Build native apps for the Bombadil user on the spot with QML and the Bombadil kit (real windows that slide in, no web servers or ports). Use whenever the user asks for an app, tool, viewer, dashboard, manager, tracker, monitor, editor or any other visual interface, or asks to change one.
---

# Building Bombadil apps

On Bombadil, anything the user wants to *see or use* becomes a native app: a QML file
using the Bombadil kit, created with the `create_app` tool of the `bombadil-os` MCP
server. It opens as a real window that slides in over the desktop within a second. Never
build a web page, a local server, a port, an Electron app or a terminal UI for this.

## The loop

1. Pick the closest pattern below. For a full example, read
   `examples/password-manager/main.qml` or `examples/memory/main.qml` next to this file
   (or call `app_guide("password-manager")` / `app_guide("memory")`).
2. Write `main.qml` rooted at `AppWindow`, composed from kit components and native
   bindings. Add `app.py` only for something the bindings cannot do.
3. Call `create_app(title, qml)`. It writes `~/Apps/<name>/`, loads the app offscreen,
   and returns `ok`, errors with `file:line`, `console.log` output and a **screenshot**.
4. `ok: false` → fix exactly what the errors name and call `create_app` again with the
   whole file. `ok: true` → look at the screenshot like a designer (clipped text, empty
   areas, misalignment, unreadable contrast, placeholder data) and fix what you see.
5. Tell the user in one short line what you built. It is already on screen.

To change an app later, call `create_app` again with the same title: it hot reloads in
place and keeps the app's saved state. `list_apps`, `open_app`, `show_app`, `hide_app`,
`close_app`, `check_app` and `app_status` manage what exists.

## Skeleton

```qml
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

AppWindow {
    id: win
    title: "Reading List"
    icon: "list"
    width: 760; height: 560

    Store { id: store; property var books: [] }     // saved across runs

    RowLayout {
        Layout.fillWidth: true; Layout.fillHeight: true
        spacing: Theme.gap
        ItemList {
            id: list
            Layout.preferredWidth: 260; Layout.fillHeight: true
            model: store.books
            titleRole: "title"; subtitleRole: "author"
        }
        Panel {
            Layout.fillWidth: true; Layout.fillHeight: true
            title: list.current ? list.current.title : "No book selected"
            DetailGrid { Layout.fillWidth: true; rows: list.current ? [{ label: "Author", value: list.current.author }] : [] }
        }
    }
}
```

## Rules that prevent the usual failures

- Use exactly these four imports, in this order. Nothing else is needed for the kit.
- The root is `AppWindow`. Its children stack vertically in a `ColumnLayout`: size them
  with `Layout.fillWidth` / `Layout.fillHeight` / `Layout.preferredWidth`, never anchors.
  Inside any `RowLayout`/`ColumnLayout` use `Layout.*`, never `anchors` or `x`/`y`.
- Colors, radii, spacing and fonts come from `Theme` (`Theme.fg`, `Theme.muted`,
  `Theme.accent`, `Theme.gap`, ...). Never hard-code a color.
- Plain `Button`, `TextField`, `ComboBox`, `Switch`, `TabBar`, `Dialog`, ... are already
  styled. `highlighted: true` makes the one primary button; `icon.source: Theme.icon("plus")`.
- Anything the user would expect to find again goes in a `Store` (or a `Vault` for
  secrets). **Assign, never mutate**: `store.items = store.items.concat([x])`, not `push`.
- Live system data comes from `System`, `Processes` and `Command`; charts get history from
  a `Series`. Don't write your own Timer + file polling.
- JS-array models: delegates declare `required property var modelData` and
  `required property int index`. For long lists use `ItemList`, `DataTable` or `ListView`,
  never a `Repeater` over hundreds of items.
- No `QtCharts`, `QtWebEngine`, `Qt5Compat.GraphicalEffects`, `MultiEffect`, shaders or
  `layer.effect`: they are not installed or not rendered by the check.
- Show real data or a real empty state (`EmptyState`), never lorem ipsum.
- Keep `main.qml` under ~250 lines; move a repeated piece into its own file via
  `create_app(..., files: {"EntryRow.qml": "..."})` and use it as `EntryRow {}`.

## Patterns

| The user wants | Shape |
|---|---|
| a manager / notes / contacts / bookmarks | `RowLayout { ItemList (260 px) ; Panel { DetailGrid or Form } }`, `SearchField` in `actions`, a `Store` |
| a monitor / dashboard | a `GridLayout` of `Stat`, `Ring`, `Meter`; `Panel`s holding `LineChart`s fed by `Series { value: System.cpu }` |
| a table explorer (processes, files, logs) | `SearchField` in `actions`, a full-size `DataTable`, a detail `Panel` or `Dialog` on `activated` |
| a form tool (converter, generator, calculator) | a `Panel` with a `Form` of `Field`s and a result `Mono` with a copy `IconButton` |
| an editor (notes, config files, scripts) | `Editor { path: "~/notes.md" }` with a file list beside it |
| anything with secrets | `Vault` behind an unlock screen (`EmptyState` + `PasswordField`), `Clipboard.copy(pw, 30)` |

## The kit at a glance

Layout and surfaces: `AppWindow`, `Panel`, `ScrollPane`, `Spacer`, `Divider`.
Text: `Heading`, `Body`, `Caption`, `Mono`. Bits: `Icon`, `IconButton`, `Badge`, `Stat`,
`EmptyState`, `DetailGrid`. Lists: `SearchField`, `ItemList`, `ListRow`, `DataTable`.
Forms: `Form`, `Field`, `PasswordField`. Editing: `Editor`. Feedback: `ConfirmDialog`,
`win.toast(text, tone)`. Charts: `Series`, `LineChart`, `BarChart`, `Sparkline`, `Meter`,
`Ring`, `StackedBar`. Formatting: `Fmt.bytes()`, `Fmt.percent()`, `Fmt.duration()`,
`Fmt.relative()`, ...

Native: `App` (name, dataDir, hide(), notify(), openUrl()), `Store`, `Vault`, `System`
(cpu, memory, disks, network, ...), `Processes`, `Command`, `TextFile`, `Clipboard`,
`Agent` (ask the OS agent from inside the app), `Highlighter`.

Full APIs: `reference/components.md`, `reference/native.md`, `reference/runtime.md`
next to this file, or `app_guide("components")`, `app_guide("native")`,
`app_guide("runtime")`. Read the reference for any component whose properties you are
not sure of; guessing a property name is the most common error.

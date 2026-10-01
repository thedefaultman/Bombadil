# Kit components (`import Bombadil`)

Everything here comes from one import. Standard `QtQuick.Controls` (Button, TextField,
ComboBox, CheckBox, Switch, Slider, TabBar, Menu, Dialog, ...) are already styled with
the OS look by the runtime, so use them as you normally would. The kit adds the pieces
Qt does not have.

Conventions used by every component:

- A `tone` is one of `"accent"`, `"good"`, `"warn"`, `"bad"`, `"info"`, `"muted"`, or `""`
  (neutral).
- An `icon` is a name from the icon list at the bottom (`"copy"`, `"trash"`, ...).
- Components size themselves (`implicitWidth`/`implicitHeight`); inside a `ColumnLayout` or
  `RowLayout` give the one that should stretch `Layout.fillWidth: true` /
  `Layout.fillHeight: true`.

## Styled controls

Restyled: `Button`, `RoundButton`, `ToolButton`, `TextField`, `TextArea`, `ComboBox`,
`SpinBox`, `CheckBox`, `RadioButton`, `Switch`, `Slider`, `RangeSlider`, `ProgressBar`,
`BusyIndicator`, `TabBar`, `TabButton`, `ToolBar`, `ToolSeparator`, `ToolTip`, `Menu`,
`MenuItem`, `MenuSeparator`, `Popup`, `Dialog`, `DialogButtonBox`, `Drawer`, `ItemDelegate`,
`CheckDelegate`, `RadioDelegate`, `SwitchDelegate`, `Label`, `Frame`, `Pane`, `Page`,
`GroupBox`, `ScrollView`, `ScrollBar`, `ScrollIndicator`, `SplitView`, `StackView`,
`ApplicationWindow`. Heights are `Theme.controlHeight` (36), rows `Theme.rowHeight` (44);
disabled controls fade, keyboard focus shows an accent ring.

`Button` looks (they combine): default is a raised neutral button; `highlighted: true` is the
accent primary action (one per view); `flat: true` has no surface until hovered; the extra
`danger: true` is destructive (red text and border, filled red with `highlighted`);
`checkable` + `checked` is accent-tinted. `icon.source: Theme.icon("plus")` puts a 16 px
icon before the text, tinted to the text color (`icon.color` overrides); with no `text` it
is a square icon button.

- `ToolButton { icon.source: Theme.icon("settings") }`: 32 px flat icon button, muted until
  hovered, accent when `checked`. Add `ToolTip.text: "..."; ToolTip.visible: hovered`.
- `Dialog` is modal and centered by default; `standardButtons: Dialog.Save | Dialog.Cancel`
  puts the buttons at the bottom right with the affirmative one highlighted and last. A
  dialog is never taller than the window (its content is clipped), so put a long form in
  a `ScrollPane`. For buttons of your own (a Save that is disabled until the form is
  valid), use a custom footer and name the primary one with `defaultButton` (the box
  resets `highlighted` on every button it holds):

  ```qml
  Dialog {
      id: editor; title: "New entry"
      Form { Field { label: "Name"; TextField { id: name } } }
      footer: DialogButtonBox {
          defaultButton: save
          Button { text: "Cancel"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
          Button { id: save; text: "Save"; enabled: name.text !== ""; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
      }
      onAccepted: add(name.text)
  }
  ```
- `MenuItem { danger: true }` is a red destructive entry. `TabBar` draws text tabs with an
  accent underline; `ItemDelegate { highlighted: true }` is a selected row.
- `TextArea` wraps by default; put it in a `ScrollView` when the text can grow.
- Keyboard shortcuts: `Shortcut { sequence: "Ctrl+N"; onActivated: add() }`. Use the string
  form; `StandardKey.New` maps to several keys and warns.

## Theme (singleton)

Tokens: `Theme.bg`, `panel`, `raised`, `overlay`, `sunken`, `border`, `borderStrong`, `fg`,
`muted`, `faint`, `accent`, `accentHover`, `accentPressed`, `accentFg`, `accentSoft`,
`good`, `warn`, `bad`, `info`, `series` (array of 8 chart colors, use in order), `grid`,
`axis`, `radius` (12), `radiusSmall` (8), `pad` (16), `gap` (12), `gapSmall` (6),
`controlHeight` (36), `rowHeight` (44), `fontFamily`, `monoFamily`, `textSize` (14),
`font`, `monoFont`, `captionSize`, `headingSize`, `titleSize`, `displaySize`, `fast`,
`normal` (animation ms).

Functions: `Theme.icon(name)` → url for `icon.source`; `Theme.tone(name)` → color;
`Theme.alpha(color, a)` → same color with alpha. `color` may be a color or a color string,
such as a `Theme.series` entry or a color loaded from JSON or a Store
(`Theme.alpha(Theme.series[0], 0.2)`); an invalid color string is reported as an error.

## Fmt (singleton, formatting)

| Call | Example result |
|---|---|
| `Fmt.bytes(n, digits = 1)` | `"1.4 GiB"`, `"512 B"` |
| `Fmt.percent(x, digits = 0)` (x in 0..1) | `"42%"` |
| `Fmt.number(n, digits = 0)` | `"12,840"` |
| `Fmt.compact(n)` | `"12.8K"`, `"4.2M"` |
| `Fmt.duration(seconds)` | `"3d 4h"`, `"12m 5s"`, `"4.2s"`, `"800 ms"` |
| `Fmt.time(date)` / `Fmt.date(date)` / `Fmt.dateTime(date)` | `"14:05"`, `"27 Sep 2026"`, `"27 Sep 2026, 14:05"` |
| `Fmt.relative(date)` | `"just now"`, `"5 min ago"`, `"3 h ago"`, `"yesterday"`, `"4 days ago"`, then the date (`"in 5 min"` for the future) |

`date` may be a `Date`, milliseconds, Unix seconds (any number below 10^11, like
`Processes` `started`), or an ISO string. A missing value (`null`, `undefined`, `NaN`)
formats as `"—"`, so a binding never shows `NaN`.

## Window and layout

### AppWindow
The root of every app. The runtime puts it in a native window and slides that window in;
AppWindow draws the OS frame: a title row, the OS palette and fonts for every control
inside, a toast area, an error banner during development, and standard keys (Esc hides
the app, Ctrl+W closes it).

| Property | Type | Default | |
|---|---|---|---|
| `title` | string | `App.title` | shown in the title row and as the window title |
| `subtitle` | string | `""` | muted text after the title |
| `icon` | string | `""` | icon name shown before the title |
| `actions` | list&lt;Item&gt; | `[]` | items placed at the right of the title row (buttons, a search field) |
| `padding` | int | `Theme.pad` | margin around the content |
| `spacing` | int | `Theme.gap` | between children |
| `showHeader` | bool | `true` | hide the title row for a chromeless app |
| `width` / `height` | int | 560 / 680 | size on first open; later opens keep the size the user left |

Function: `toast(text, tone = "")` shows a short message at the bottom for 2.5 s (a
`"good"` tone adds a check, `"warn"`/`"bad"` a warning sign).

Children stack top to bottom in a `ColumnLayout` filling the content area. They stay
packed at the top unless one has `Layout.fillHeight: true`, which then takes the free
height, so give the main child `Layout.fillWidth: true; Layout.fillHeight: true`. A child
with `focus: true` has the keyboard when the app opens. Popups and `Dialog`s work as
usual (they open over the whole window). When a hot reload fails, a red banner at the top
shows the first error line; clicking it shows the whole error.

```qml
AppWindow {
    title: "Notes"
    icon: "file-text"
    actions: [ Button { text: "New"; icon.source: Theme.icon("plus"); highlighted: true; onClicked: add() } ]
    Panel { Layout.fillWidth: true; Layout.fillHeight: true }
}
```

### Panel
A card: a rounded `Theme.panel` surface with optional header.

| Property | Type | Default | |
|---|---|---|---|
| `title` | string | `""` | header text; no header when empty and no `actions` |
| `subtitle` | string | `""` | muted text under the title |
| `actions` | list&lt;Item&gt; | `[]` | items at the right of the header |
| `padding` | int | `Theme.pad` | |
| `spacing` | int | `Theme.gap` | between children |
| `flat` | bool | `false` | no background, just the layout |

Children stack in a `ColumnLayout`, packed at the top unless one has
`Layout.fillHeight: true` (a list or table that takes the rest). Panels side by side in
a `RowLayout` or `GridLayout` all get the height of the tallest one, so a row of cards
lines up without extra settings; a Panel with `Layout.fillHeight: true` takes the row's
full height instead. Items in `actions` refer to the Panel's own properties through its
id (`details.title`), not bare names.

### Spacer
`Spacer {}` takes the free space along its layout: width in a `RowLayout` (pushes the
next items to the right), height in a `ColumnLayout` (pushes them to the bottom).

### Divider
A 1 px `Theme.border` line. `vertical: false` by default; fills the layout's width (or
height when vertical).

### ScrollPane
A vertical scroll area for content taller than the window. Children go into a
`ColumnLayout` as wide as the pane. Properties: `spacing` (`Theme.gap`), `padding` (0).

## Text

| Component | Look |
|---|---|
| `Heading { text; level: 1 }` | level 1 = `titleSize` bold, 2 = `headingSize` semibold, 3 = `textSize` semibold |
| `Body { text }` | `textSize`, `Theme.fg`, wraps |
| `Caption { text }` | `captionSize`, `Theme.muted`, wraps |
| `Mono { text }` | `monoFont`, `Theme.fg`, selectable |

`Heading`, `Body` and `Caption` are `Text`-based (`Body`/`Caption` have
`wrapMode: Text.Wrap`); any `Text` property can be set. `Mono` is a read-only `TextEdit`
so it can be selected and copied; it has no `elide` or `maximumLineCount`. To shorten a
long path or command line, use `Caption { font.family: Theme.monoFamily; wrapMode:
Text.NoWrap; elide: Text.ElideMiddle }` for one line, or `maximumLineCount: 3; elide:
Text.ElideRight` for a few. `Body`, `Caption` and `Mono` fill the width of their layout,
so long text wraps instead of running off the window.

## Small pieces

### Icon
`Icon { name: "copy"; size: 18; color: Theme.fg }`. Lucide icons, tinted to `color`.

### IconButton
A square flat button with just an icon and a tooltip.
Properties: `icon` (name), `tooltip`, `tone` (tints the icon), `size` (32), `checkable`,
`checked`, `hovered` (read-only). Signal: `clicked()`. Clicking it does not take the
keyboard focus from a field or list.

### Badge
A small pill. `Badge { text: "weak"; tone: "bad" }`. Properties: `text`, `tone`, `icon`.

### Stat
A headline number.

| Property | Type | |
|---|---|---|
| `label` | string | sentence case, no colon |
| `value` | string | the formatted number (`Fmt.bytes(x)`) |
| `detail` | string | small muted line under the value (one line; longer text is elided) |
| `delta` | string | e.g. `"+12%"`, colored by `deltaTone` |
| `deltaTone` | string | tone for the delta |
| `trend` | list&lt;real&gt; | optional values; draws a sparkline under the number (in the delta's tone, else `Theme.series[0]`) |

Stats in a row line up at the top; wrapped in `Panel`s in a `RowLayout`, the cards share
one height. For cards of equal width too, give the row `uniformCellSizes: true` and each
Panel `Layout.fillWidth: true`:

```qml
RowLayout {
    Layout.fillWidth: true
    uniformCellSizes: true
    Panel { Layout.fillWidth: true; Stat { label: "Used"; value: Fmt.bytes(System.memory.used) } }
    Panel { Layout.fillWidth: true; Stat { label: "CPU"; value: Fmt.percent(System.cpu) } }
}
```

### EmptyState
For an empty list or a locked screen. Properties: `icon`, `title`, `text`, `actionText`;
signal `action()` (a button is shown when `actionText` is set).

### DetailGrid
Two-column label/value pairs. `rows: [{ label: "PID", value: "1234" }, ...]`; optional
`mono: true` on a row renders its value in the mono font, `copyable: true` adds a copy
button (copies to the clipboard; add `secret: true` for a password so the clipboard clears
after 30 s). An empty or missing value shows `"—"`. Values can be selected, except in
`secret: true` rows: a mouse selection goes to the primary selection, which never clears,
so for secrets use `copyable: true` (the clipboard clears after 30 s). Mark password rows
`secret: true` even when nothing is copyable.

## Lists and tables

### SearchField
A `TextField` with a search icon and a clear button. Ctrl+F focuses it, Esc clears it.
Property `text`; signal `accepted()`. Use its `text` to filter your model. It fills the
width of its layout; in `actions` give it `Layout.preferredWidth: 200;
Layout.fillWidth: false`. (Qt has its own `SearchField`; with the usual imports the kit's
wins, so do not add `import QtQuick.Controls.Basic`.)

### ItemList
A styled, selectable, keyboard-navigable list.

| Property | Type | Default | |
|---|---|---|---|
| `model` | array or model | | an array of objects is easiest; an array of strings, a list from Python or a `ListModel` also work |
| `titleRole` | string | `"title"` | field shown as the main line |
| `subtitleRole` | string | `"subtitle"` | field shown muted under it (optional) |
| `iconRole` | string | `""` | field holding an icon name |
| `trailingRole` | string | `""` | field shown at the right (muted) |
| `currentIndex` | int | -1 | selected row |
| `current` | var | (read-only) | the selected model item or `null` |
| `emptyText` | string | `"Nothing here yet"` | shown when the model is empty |
| `delegate` | Component | a `ListRow` | replace for fully custom rows |
| `count` | int | (read-only) | number of rows |

Signals: `activated(int index, var item)` (tap, click or Enter), `contextRequested(int
index, var item)` (right click; it selects the row too). Up/Down/Home/End move the
selection once the list has focus (click it, or `focus: true`).

When `model` is replaced by a new array (filtering, an edit saved to a `Store`), the
selection stays on the same item, matched by its `id`, `uuid`, `key` or `pid` field, else
by equal content, and the scroll position is kept. When a selected item with an `id`,
`uuid`, `key` or `pid` field is gone from the new array (deleted, filtered out),
`currentIndex` becomes -1 and `current` `null`; set `currentIndex` yourself to select a
neighbour. An item with none of those fields is matched by content: if it is gone while
the count stays the same, the selection keeps its row, because the item was probably
edited in place (a changed count gives -1 and `null` too).

A `ListModel` is not matched like that: Qt's `ListView` keeps the selection on its item
while rows are added or removed around it, and when the selected row itself is removed a
neighbouring row is selected (the next one, the previous for the last row; -1 when none is
left).

A custom `delegate` is a normal `ListView` delegate (`index`, `modelData`); set
`selected: ListView.isCurrentItem` on a `ListRow`. Every kind of delegate is selected when
it is pressed and activated when it is tapped or clicked: a `ListRow`, a plain `Item`, or
a delegate whose root is a button (`ItemDelegate`, `CheckDelegate`, `SwitchDelegate`). The
delegate's own `onClicked` (a `ListRow`'s or a button's) still runs, first. If that
`onClicked` moves its item (mark as read in a list sorted unread first), the selection and
`activated` follow the item to its new index when it has an `id`, `uuid`, `key` or `pid`
field. An item without one that its `onClicked` edits keeps its row, and `activated`
reports whatever item is now in that row, so give such items an `id`. If it removes the
item (ticking a todo in a list that hides done items), the selection becomes -1/`null` and
`activated` is not emitted. A model or array replaced between the press and the release (a
poller) does not change which item the tap is about: the pressed item stays selected,
matched as above, or -1 when it is gone. The rows are rebuilt with the array, so a click
that spans the replacement does not run a `ListRow`'s `onClicked`, and a button row's
`onClicked` and `activated` do not run at all. A delegate that takes the press some other way,
such as a `MouseArea` over the row or a `Button` inside an `Item`, has to set
`ListView.view.currentIndex = index` itself.

A double-click on a `ListRow`, a plain `Item` or the default row emits `activated` once,
on its first click (a `ListRow` also emits `doubleClicked()` for the second). A button row
is clicked twice: its `onClicked` and `activated` both run twice. A press that turns into a
scroll (a touch drag), or that the mouse drags away, gives the previous selection back
without `activated` and without moving the list (a `SwitchDelegate` is the exception for
the mouse: its own drag gesture keeps the press, so it is selected and activated on
release), so scrolling a touch list never changes
`current` or jumps it to a selection that is off-screen. A tap on a partly visible row
scrolls it fully into view.

On its own an ItemList is as tall as its rows, up to eight, then scrolls; give it
`Layout.fillHeight: true` to take the free height instead.

### ListRow
The row `ItemList` uses; use it in your own `ListView` delegates.
Properties: `title`, `subtitle`, `icon`, `trailing` (string), `selected`, `trailingItem`
(an Item placed at the right, e.g. a Badge or IconButton), `hovered` (read-only).
Signals: `clicked()`, `doubleClicked()`, `contextRequested()` (right click). A double-click
is a `clicked()` for the first click and a `doubleClicked()` for the second.

### DataTable
A sortable table with a sticky header.

| Property | Type | |
|---|---|---|
| `columns` | array | `[{ key, title, width, align, format, mono }]` |
| `rows` | array of objects | the data (a `ListModel` or a list from Python also works) |
| `sortKey` | string | column key currently sorted (click a header to change) |
| `sortDescending` | bool | |
| `currentIndex` | int | selected row in the *sorted* order |
| `current` | var | (read-only) selected row object |
| `emptyText` | string | default `"Nothing to show"` |
| `count` | int | (read-only) number of rows |

Column fields: `key` (field name), `title`, `width` (a number is a flex weight, default 1;
a string like `"80px"` is fixed), `align` (`"left"` / `"right"` / `"center"`; numbers
default right), `format` (function `(value, row) => string`), `mono` (bool), `sortable`
(default true). A flex column never gets narrower than its title, and a number column
keeps room for its values; names elide.

Signals: `activated(var row)` (double-click or Enter), `contextRequested(var row)` (right
click; it selects the row too). Clicking a header sorts by it (numbers start descending);
clicking again flips the order. Up/Down, PageUp/PageDown, Home/End and Enter work once
the table has focus. A row is selected when it is pressed, not when the button is
released, so a refresh that re-sorts the table between the press and the click leaves the
pressed row selected, wherever it went. A double-click emits `activated` once. A press that
turns into a touch scroll, or that the mouse drags away, gives the previous selection back
without moving the list, and a tap on a partly visible row scrolls it fully into view.
When `rows` is replaced (a poller refreshing every second), the scroll position stays and
the selection follows the same row, matched by its `id`, `uuid`, `key` or `pid` field.
When that row is gone from the new rows (a process that exited, or one that dropped out
of a `limit`), `currentIndex` becomes -1 and `current` `null`, so details panels and
actions bound to `current` must handle `null` (`table.current ? table.current.pid : -1`).
Rows with none of those fields are matched by content; one that is gone keeps its place
when the count is unchanged (its values changed), else it is deselected too. A `ListModel`
passed as `rows` is copied into plain objects and followed as rows are added, removed or
changed (`ListModel.move()` is not noticed: the table shows the old order until the next
add, remove or change): `current` is a copy (change the model with `setProperty`, not through `current`),
and removing the selected row deselects it (-1/`null`) like any other row that is gone.

```qml
DataTable {
    Layout.fillWidth: true; Layout.fillHeight: true
    columns: [
        { key: "name", title: "Process", width: 3 },
        { key: "pid", title: "PID", width: "70px", mono: true },
        { key: "memory", title: "Memory", format: v => Fmt.bytes(v) }
    ]
    rows: procs.list
    sortKey: "memory"; sortDescending: true
}
```

## Forms

### Form
A `ColumnLayout` for `Field`s with `Theme.gap` spacing. Property `labelWidth`: when set,
labels sit to the left at that width instead of above. It fills the width of its layout;
for a narrower form (an unlock screen) give it `Layout.fillWidth: false;
Layout.preferredWidth: 320; Layout.alignment: Qt.AlignHCenter`.

### Field
A labeled wrapper around one control. Properties: `label`, `hint` (muted help under the
control), `error` (red text; replaces the hint when not empty), `required` (adds a dot).
The control is the child; inputs stretch to the field's width, while buttons, checkboxes
and switches keep their own.

```qml
Form {
    Layout.fillWidth: true
    Field { label: "Website"; TextField { id: site; placeholderText: "github.com" } }
    Field { label: "Password"; error: pw.text ? "" : "Required"; PasswordField { id: pw } }
}
```

### PasswordField
A `TextField` with `echoMode: TextInput.Password` and an eye button to reveal it.
Extra properties: `revealed` (bool), `showStrength` (bool, draws a 4-step meter under
it, using `strength`), `strength` (0..4, computed from `text`: length and character
variety, minus common passwords, repeats and sequences like `1234`; a `Vault` instance's
`strength(password)` gives the same score, e.g. `vault.strength(pw.text)` with
`Vault { id: vault }`, see native.md; pass the text, not the field). Everything else is `TextField` (`text`,
`placeholderText`, `onAccepted`, ...). It fills the width of its layout, and a revealed
password shows in the mono font. While `revealed`, the text cannot be selected with the
mouse, because it would stay in the primary selection. `revealed` is never reset for
you: set it back to false once the secret is done with (for example after unlocking), or
the next password typed shows in clear. With `showStrength`, a button placed beside it
in a `RowLayout` needs `Layout.alignment: Qt.AlignTop` to line up with the field rather
than field plus meter.

## Editing

### Editor
A code/text editor with line numbers and syntax highlighting.

| Property | Type | Default | |
|---|---|---|---|
| `text` | string | | the content (with `path`, what was loaded from the file) |
| `path` | string | `""` | when set, loads that file, shows a dirty dot, Ctrl+S saves |
| `language` | string | from `path`'s extension | `"plain"`, `"markdown"`, `"python"`, `"json"`, `"qml"`, `"javascript"`, `"shell"`, `"toml"`, `"ini"` |
| `readOnly` | bool | false | |
| `lineNumbers` | bool | true | |
| `wrap` | bool | false | |
| `dirty` | bool | (read-only) | text differs from the file |

Functions: `save()` (writes `path` when set, then emits `saved()`), `reload()` (drops
unsaved edits). Signal `saved()`. With `path`, a change on disk is picked up unless you
have unsaved edits.

## Dialogs and feedback

### ConfirmDialog
`ConfirmDialog { id: confirm; title: "Delete entry?"; text: "This cannot be undone.";
confirmText: "Delete"; danger: true; onConfirmed: remove() }` then `confirm.open()`.
Properties: `title`, `text`, `confirmText` (`"Confirm"`), `cancelText` (`"Cancel"`),
`danger` (red confirm button; Cancel gets the focus). It is a `Dialog`, so `rejected()`
and `closed()` work too.

When the thing being confirmed comes from live data (a selected row in a table that
refreshes), copy it when the dialog opens so a refresh cannot change what gets deleted:
`ConfirmDialog { id: confirm; property var target: null; onConfirmed: procs.kill(target.pid) }`
and a button with `enabled: table.current !== null; onClicked: { confirm.target = table.current; confirm.open() }`.

For anything else use `Dialog` from QtQuick.Controls (already styled) and
`toast()` on your AppWindow (`win.toast("Saved", "good")`, where `win` is its id) for transient messages.

## Charts

Charts take series colors from `Theme.series` in order (the first series is
`Theme.series[0]`; an item's own `color` overrides it), keep all text in the text colors,
show a tooltip on hover and animate changes. LineChart and Sparkline fill their size, so
give them `Layout.fillWidth: true` and a `Layout.preferredHeight`; the others have a
natural height. Plot one measure per chart: two measures with different units are two
charts, never two y axes. When series can come and go (a filter), give each a fixed
`color: Theme.series[n]` so the others keep theirs; past 8 series, fold the rest into
"Other".

`format` properties take a function `v => string` and default to compact numbers
(`"12.8K"`, `"0.25"`); pass `v => Fmt.bytes(v)` for sizes and `v => Fmt.percent(v)` for
0..1 fractions. Every chart with a tooltip also has `hoverIndex` (int, -1 when nothing is
hovered): the hovered point, bar or part. Setting it shows that tooltip.

### Series
Records a value over time for a chart.
`Series { id: cpu; value: System.cpu; capacity: 120 }` takes a sample of `value` every
`interval` ms (1000), also when it did not change, so point `i` of a full series is
`(capacity - 1 - i)` seconds old and a flat line still moves along. The oldest point drops
off past `capacity`. Read `values` (array, oldest first), `last`, `min`, `max`, `average`
(all 0 while empty). Function `clear()`. With `interval: 0` a point is added only when
`value` changes or you call `push(v)` (for events rather than a clock).

### LineChart

| Property | Type | |
|---|---|---|
| `series` | array | `[{ name, values, color? }]`; `values` is an array of numbers (equal spacing, newest last; `null` leaves a gap) or of `{x, y}` with numeric x (e.g. ms) |
| `yMin` / `yMax` | real | fixed range; default auto from the data (0-based when all values are >= 0, top rounded up to a round tick; byte axes step in whole KiB/MiB/GiB) |
| `format` | function | formats y values for axis and tooltip (`v => Fmt.bytes(v)`) |
| `xFormat` | function | formats x for the tooltip and the labels under both ends of the x axis (no x labels without it); for plain numbers x is the index |
| `capacity` | int | x slots for plain numbers; set it to the Series' capacity so a live chart fills in from the right instead of stretching (default: the longest series) |
| `area` | bool | fill under the lines with a 10% wash (default true for one series) |
| `legend` | bool | default true when there are 2+ series |
| `gridLines` | int | about this many horizontal gridlines (4); ticks land on round numbers |

The last point of each line carries a dot; hovering shows a crosshair and one tooltip
with every series at that x.

```qml
Series { id: mem; value: System.memory.used; capacity: 120 }
LineChart {
    Layout.fillWidth: true; Layout.preferredHeight: 200
    series: [{ name: "Used", values: mem.values }]
    capacity: mem.capacity
    yMax: System.memory.total
    format: v => Fmt.bytes(v)
    xFormat: i => i === mem.capacity - 1 ? "now" : Fmt.duration(mem.capacity - 1 - i) + " ago"
}
```

### BarChart

| Property | Type | |
|---|---|---|
| `bars` | array | `[{ label, value, color? }]`, in the order to show (sort them yourself); every bar is `Theme.series[0]` unless it sets `color` |
| `horizontal` | bool | default true (labels on the left read best); false draws columns with labels under them |
| `format` | function | value labels and tooltip |
| `max` | real | fixed scale max; default the largest value |

Each bar shows its value at the tip. Height: 32 px per bar when horizontal.

### Sparkline
A tiny line with no axes. `Sparkline { values: cpu.values; color: Theme.series[0] }`.
Properties: `values`, `color`, `area` (true), `min`/`max` (auto: the data's own range),
`format` (tooltip).

### Meter
A horizontal bar for a 0..1 fraction. `Meter { value: 0.72; label: "Memory"; detail:
"11.2 of 16 GiB" }`. The label, muted detail and percentage sit above the bar.
Properties: `value`, `label`, `detail`, `tone` (default: accent, warn above `warnAt`
(0.75), bad above `badAt` (0.9)), `warnAt`, `badAt`.

### Ring
A circular gauge with the percentage in the middle and the label (and detail) under it.
`Ring { value: 0.42; label: "CPU"; size: 96 }`. Properties as `Meter` plus `size` (96)
and `thickness` (8).

### StackedBar
One bar split into parts, with a legend of each part's value under it. Properties:
`parts` (`[{ label, value, color? }]`), `total` (the rest is drawn as free space; default
the sum of the parts), `format` (legend and tooltip values), `legend` (true), `freeLabel`
(`"Free"`: the legend entry for the free space; `""` hides it).

```qml
StackedBar {
    Layout.fillWidth: true
    total: System.memory.total
    format: v => Fmt.bytes(v)
    parts: [{ label: "Apps", value: System.memory.used }, { label: "Cache", value: System.memory.cached }]
}
```

## Pictures

### Diagram
Boxes and the lines between them, from data, in one of four fixed shapes. The layout is fixed and
never a force layout: the same data draws the same picture. It is the same component the bar uses
for the pictures the OS draws above the pill (`show_card`, `system_map`), so a picture in an app
looks like those. Use it when an answer has parts, order or change; for numbers over time use a
chart instead.

```qml
Diagram {
    Layout.fillWidth: true
    spec: ({
        shape: "chain", title: "How the backup runs",
        nodes: [{ id: "a", label: "Timer", sub: "daily, 03:00" },
                { id: "b", label: "rsync", state: "active" },
                { id: "c", label: "Backup disk", state: "warn", note: "82% full",
                  opens: { kind: "path", value: "/mnt/backup" } }],
        say: "The disk is nearly full, so the next run may fail."
    })
    onOpened: target => Qt.openUrlExternally("file://" + target.value)   // a box with `opens` is a button
}
```

`spec` is the card `show_card` takes:

- `shape`: `chain` (left to right, wrapping into rows when it does not fit; arrows in order unless
  `linked: false` or `links` are given), `layers` (a box sits below every box that points at it;
  give `links`, nothing else needed), `compare` (every node has `side: "before"` or `"after"`; the
  same `key` on both sides puts them on one row), `timeline` (one row per node: `time` as text and
  `weight` as a number for the bar's length).
- `title` (60 characters), `say` (one plain sentence, 160), `highlight` (ids lit, the rest dimmed).
- `nodes` (at most 12): `label` (32), `sub` (60), `note` (60, under the box), `id` (needed by links and
  highlight), `state`, `icon` (a kit icon name), `opens`.
- `state`: `ok`, `warn` (amber, a triangle), `bad` (red, an x), `new` (orange, a plus), `gone` (dim and
  struck through), `active` (blue). Colour is never the only sign.
- `links` (at most 16): `{ from, to, label (24), state }`.
- `opens`: `{ kind, value }` with kind `path`, `unit`, `package`, `url` or `turn`. The component only
  emits `opened(target)`; the bar's host opens it, and in an app you decide what it does.

Properties: `spec`, `showTitle` (true: the title above and the `say` line under), `minBoxWidth` (132),
`maxBoxWidth` (208), `maxCompareWidth` (230) and `compareGap` (76) for a `compare` (the two columns
sit together in the middle, the arrow in the gap). A chain lays itself out for its link labels, so
give a chain's labels with its nodes or leave them out: a `partial` chain that has no links yet
leaves room for labels of about 14 characters, so it does not jump when they arrive. Signals: `opened(target)` for a box with `opens`, `picked(node)` for any click.
A spec with `partial: true` shows "drawing…" (a card still being written). Bad data draws nothing and
warns about nothing.

## Icons

`activity` `alert-triangle` `archive` `arrow-down` `arrow-left` `arrow-right` `arrow-up`
`bell` `bot` `calendar` `check` `chevron-down` `chevron-left` `chevron-right`
`chevron-up` `circle` `clipboard` `clock` `code` `copy` `cpu` `database` `download`
`edit` `external-link` `eye` `eye-off` `file` `file-text` `filter` `folder` `gauge`
`globe` `hard-drive` `heart` `info` `key` `layers` `link` `list` `lock` `log-out`
`memory-stick` `menu` `minus` `moon` `more-horizontal` `more-vertical` `music` `network`
`pause` `pencil` `play` `plus` `refresh` `rotate-ccw` `save` `search` `send` `settings`
`shield` `sliders` `sort-asc` `sort-desc` `sparkles` `square` `star` `sun` `terminal`
`trash` `unlock` `upload` `user` `wand` `wifi` `x` `zap`

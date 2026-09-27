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

TODO(style): which QtQuick.Controls are restyled, and the extra `Button` options.

## Theme (singleton)

Tokens: `Theme.bg`, `panel`, `raised`, `overlay`, `sunken`, `border`, `borderStrong`, `fg`,
`muted`, `faint`, `accent`, `accentHover`, `accentPressed`, `accentFg`, `accentSoft`,
`good`, `warn`, `bad`, `info`, `series` (array of 8 chart colors, use in order), `grid`,
`axis`, `radius` (12), `radiusSmall` (8), `pad` (16), `gap` (12), `gapSmall` (6),
`controlHeight` (36), `rowHeight` (44), `fontFamily`, `monoFamily`, `textSize` (14),
`font`, `monoFont`, `captionSize`, `headingSize`, `titleSize`, `displaySize`, `fast`,
`normal` (animation ms).

Functions: `Theme.icon(name)` → url for `icon.source`; `Theme.tone(name)` → color;
`Theme.alpha(color, a)` → same color with alpha.

## Fmt (singleton, formatting)

| Call | Example result |
|---|---|
| `Fmt.bytes(n, digits = 1)` | `"1.4 GiB"`, `"512 B"` |
| `Fmt.percent(x, digits = 0)` (x in 0..1) | `"42%"` |
| `Fmt.number(n, digits = 0)` | `"12,840"` |
| `Fmt.compact(n)` | `"12.8K"`, `"4.2M"` |
| `Fmt.duration(seconds)` | `"3d 4h"`, `"12m 5s"`, `"800 ms"` |
| `Fmt.time(date)` / `Fmt.date(date)` / `Fmt.dateTime(date)` | `"14:05"`, `"27 Sep 2026"` |
| `Fmt.relative(date)` | `"just now"`, `"5 min ago"`, `"yesterday"` |

`date` may be a `Date`, milliseconds, or an ISO string.

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

Function: `toast(text, tone = "")` shows a short message at the bottom for 2.5 s.

Children are laid out in a `ColumnLayout` filling the content area, so give the main
child `Layout.fillWidth: true; Layout.fillHeight: true`. Popups and `Dialog`s work as
usual (they open over the whole window).

```qml
AppWindow {
    title: "Notes"
    icon: "file-text"
    actions: [ Button { text: "New"; icon.source: Theme.icon("plus"); highlighted: true; onClicked: add() } ]
    Panel { Layout.fillWidth: true; Layout.fillHeight: true }
}
```

`Window` is an alias of `AppWindow` kept for apps from the first milestone.

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

Children go into a `ColumnLayout`.

### Spacer
`Item { Layout.fillWidth: true; Layout.fillHeight: true }`; takes the free space in a
row or column.

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

All four are `Text`-based (`Body`/`Caption` have `wrapMode: Text.Wrap`); any `Text`
property can be set. `Mono` is a read-only `TextEdit` so it can be selected and copied.

## Small pieces

### Icon
`Icon { name: "copy"; size: 18; color: Theme.fg }`. Lucide icons, tinted to `color`.

### IconButton
A square flat button with just an icon and a tooltip.
Properties: `icon` (name), `tooltip`, `tone` (tints the icon), `size` (32), `checkable`,
`checked`. Signal: `clicked()`.

### Badge
A small pill. `Badge { text: "weak"; tone: "bad" }`. Properties: `text`, `tone`, `icon`.

### Stat
A headline number.

| Property | Type | |
|---|---|---|
| `label` | string | sentence case, no colon |
| `value` | string | the formatted number (`Fmt.bytes(x)`) |
| `detail` | string | small muted line under the value |
| `delta` | string | e.g. `"+12%"`, colored by `deltaTone` |
| `deltaTone` | string | tone for the delta |
| `trend` | list&lt;real&gt; | optional values; draws a sparkline under the number |

### EmptyState
For an empty list or a locked screen. Properties: `icon`, `title`, `text`, `actionText`;
signal `action()` (a button is shown when `actionText` is set).

### DetailGrid
Two-column label/value pairs. `rows: [{ label: "PID", value: "1234" }, ...]`; optional
`mono: true` on a row renders its value in the mono font, `copyable: true` adds a copy
button.

## Lists and tables

### SearchField
A `TextField` with a search icon and a clear button. Ctrl+F focuses it. Property `text`;
signal `accepted()`. Use its `text` to filter your model.

### ItemList
A styled, selectable, keyboard-navigable list.

| Property | Type | Default | |
|---|---|---|---|
| `model` | array or model | | an array of objects is easiest |
| `titleRole` | string | `"title"` | field shown as the main line |
| `subtitleRole` | string | `"subtitle"` | field shown muted under it (optional) |
| `iconRole` | string | `""` | field holding an icon name |
| `trailingRole` | string | `""` | field shown at the right (muted) |
| `currentIndex` | int | -1 | selected row |
| `current` | var | (read-only) | the selected model item or `null` |
| `emptyText` | string | `"Nothing here yet"` | shown when the model is empty |
| `delegate` | Component | a `ListRow` | replace for fully custom rows |

Signals: `activated(int index, var item)` (click or Enter), `contextRequested(int index,
var item)` (right click).

### ListRow
The row `ItemList` uses; use it in your own `ListView` delegates.
Properties: `title`, `subtitle`, `icon`, `trailing` (string), `selected`, `trailingItem`
(an Item placed at the right, e.g. a Badge or IconButton). Signals: `clicked()`,
`doubleClicked()`.

### DataTable
A sortable table with a sticky header.

| Property | Type | |
|---|---|---|
| `columns` | array | `[{ key, title, width, align, format, mono }]` |
| `rows` | array of objects | the data |
| `sortKey` | string | column key currently sorted (click a header to change) |
| `sortDescending` | bool | |
| `currentIndex` | int | selected row in the *sorted* order |
| `current` | var | (read-only) selected row object |
| `emptyText` | string | |

Column fields: `key` (field name), `title`, `width` (a number is a flex weight, default 1;
a string like `"80px"` is fixed), `align` (`"left"` / `"right"` / `"center"`; numbers
default right), `format` (function `value => string`), `mono` (bool), `sortable` (default
true).

Signals: `activated(var row)`, `contextRequested(var row)`.

```qml
DataTable {
    Layout.fillWidth: true; Layout.fillHeight: true
    columns: [
        { key: "name", title: "Process", width: 3 },
        { key: "pid", title: "PID", width: "70px", mono: true },
        { key: "rss", title: "Memory", format: v => Fmt.bytes(v) }
    ]
    rows: procs.list
    sortKey: "rss"; sortDescending: true
}
```

## Forms

### Form
A `ColumnLayout` for `Field`s with `Theme.gap` spacing. Property `labelWidth`: when set,
labels sit to the left at that width instead of above.

### Field
A labeled wrapper around one control. Properties: `label`, `hint` (muted help under the
control), `error` (red text; replaces the hint when not empty), `required` (adds a dot).
The control is the child.

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
it, using `strength`), `strength` (0..4, computed from `text`).

## Editing

### Editor
A code/text editor with line numbers and syntax highlighting.

| Property | Type | Default | |
|---|---|---|---|
| `text` | string | | the content |
| `path` | string | `""` | when set, loads that file, shows a dirty dot, Ctrl+S saves |
| `language` | string | from `path` | `"plain"`, `"markdown"`, `"python"`, `"json"`, `"qml"`, `"javascript"`, `"shell"`, `"toml"`, `"ini"` |
| `readOnly` | bool | false | |
| `lineNumbers` | bool | true | |
| `wrap` | bool | false | |
| `dirty` | bool | (read-only) | text differs from the file |

Functions: `save()`, `reload()`. Signal `saved()`.

## Dialogs and feedback

### ConfirmDialog
`ConfirmDialog { id: confirm; title: "Delete entry?"; text: "This cannot be undone.";
confirmText: "Delete"; danger: true; onConfirmed: remove() }` then `confirm.open()`.

For anything else use `Dialog` from QtQuick.Controls (already styled) and
`AppWindow.toast()` for transient messages.

## Charts

All charts are drawn with Canvas, follow the series colors in `Theme.series` in order,
show a hover tooltip, and animate new values. They size to their layout; give them a
`Layout.preferredHeight`.

### Series
Records a changing value over time for a chart.
`Series { id: cpu; value: System.cpu; capacity: 120 }`. Each time `value` changes a
point is appended. Read `values` (array), `last`, `min`, `max`, `average`. Function
`clear()`, `push(v)` (append manually instead of binding `value`).

### LineChart

| Property | Type | |
|---|---|---|
| `series` | array | `[{ name, values, color? }]`; `values` is an array of numbers (equal spacing) or of `{x, y}` |
| `yMin` / `yMax` | real | fixed range; default auto from the data (0-based when all values are >= 0) |
| `format` | function | formats y values for axis and tooltip (`v => Fmt.bytes(v)`) |
| `xFormat` | function | formats x for the tooltip |
| `area` | bool | fill under the lines with a 10% wash (default true for one series) |
| `legend` | bool | default true when there are 2+ series |
| `gridLines` | int | horizontal gridlines (4) |

### BarChart

| Property | Type | |
|---|---|---|
| `bars` | array | `[{ label, value, color? }]` |
| `horizontal` | bool | default true (labels on the left read best) |
| `format` | function | value labels and tooltip |
| `max` | real | fixed scale max; default the largest value |

### Sparkline
A tiny line with no axes. `Sparkline { values: cpu.values; color: Theme.series[0] }`.
Properties: `values`, `color`, `area` (true), `min`/`max` (auto).

### Meter
A horizontal bar for a 0..1 fraction. `Meter { value: 0.72; label: "Memory"; detail:
"11.2 of 16 GiB" }`. Properties: `value`, `label`, `detail`, `tone` (default: accent,
warn above `warnAt` (0.75), bad above `badAt` (0.9)), `warnAt`, `badAt`.

### Ring
A circular gauge. `Ring { value: 0.42; label: "CPU"; size: 96 }`. Properties as `Meter`
plus `size` and `thickness`.

### StackedBar
One bar split into parts with a legend. `StackedBar { total: mem.total; parts: [{ label:
"Apps", value: used }, { label: "Cache", value: cached }] }`. Properties: `parts`,
`total` (the rest is drawn as free space), `format`, `legend` (true).

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

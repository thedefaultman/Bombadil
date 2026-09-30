.pragma library
// The desk's colours and sizes, in one place. Each one is a token of the app kit's Theme.qml
// (share/qml/Bombadil/Theme.qml), named in its comment; tests/test_theme.py fails when a value here
// drifts from its token. It stays a library of plain values, not a Theme import, because a
// .pragma library script cannot import a directory and the desk's logic (DeskState) reads it too.
// Who a colour is for: white is you, orange is the machine's turn, blue is coding sessions.

// Surfaces
var panel = "#f51a1d21"          // glassCard: a card's fill, #1a1d21 at 96%
var strip = "#f21a1d21"          // glassStrip: a strip's fill, #1a1d21 at 95%
var border = "#2a2f36"
var borderStrong = "#3a414a"
var raised = "#22262b"           // a meter's track, a quiet button
var sunken = "#0c0e10"

// Ink
var fg = "#e6e8eb"
var muted = "#8b939c"
var faint = "#5c636b"

// Who
var you = "#e6e8eb"              // fg
var machine = "#d97757"          // accent
var sessions = "#5b9bd5"         // info

// State (the same edge colours as StatusLine.qml)
var ok = "#5fb36b"               // good
var amber = "#e0a93b"            // warn: a step that touches the system
var amberText = "#e8c38d"        // warnInk: its exact command
var red = "#d05555"              // bad: a step no restore point can undo, an error
var redText = "#f0a0a0"          // badInk
var onAccent = "#ffffff"         // accentFg: text on the accent

// Type
var fontFamily = "Inter"         // Theme.fontFamily
var monoFamily = "monospace"     // Theme.monoFamily

// Shape
var cardWidth = 300
var radius = 12
var railMargin = 16              // from the screen's edge
var railTop = 40                 // below the top edge
var railBottom = 16              // above the pill's row
var cardGap = 12
var stripHeight = 28
var stripGap = 12
var rowHeight = 44
var pillHeight = 52

// Motion (ms): out of the way fast, back calmly
var foldMs = 150
var unfoldDelayMs = 400
var washMs = 600

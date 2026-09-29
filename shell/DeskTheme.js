.pragma library
// The desk's colours and sizes, in one place. They are the values the app kit's Theme.qml carries
// (share/qml/Bombadil/Theme.qml on the app-kit branch) and the ones the earlier shell files use,
// so a card, a strip, the line and an app read as one system. The shell does not import the kit
// yet; when it does, this file is the only one to change. Who a colour is for: white is you,
// orange is the machine's turn, blue is coding sessions.

// Surfaces
var panel = "#f51a1d21"          // a card's fill: #1a1d21 at 96%
var strip = "#f21a1d21"          // a strip's fill: #1a1d21 at 95%
var border = "#2a2f36"
var borderStrong = "#3a414a"
var raised = "#22262b"           // a meter's track, a quiet button
var sunken = "#0c0e10"

// Ink
var fg = "#e6e8eb"
var muted = "#8b939c"
var faint = "#5c636b"

// Who
var you = "#e6e8eb"
var machine = "#d97757"
var sessions = "#5b9bd5"

// State (the same edge colours as StatusLine.qml)
var ok = "#5fb36b"
var amber = "#e8a33d"            // a step that touches the system
var amberText = "#e8c38d"        // its exact command
var red = "#e05252"              // a step no restore point can undo
var redText = "#f0a0a0"
var redSoft = "#c04a4a"

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

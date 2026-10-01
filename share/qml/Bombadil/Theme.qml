pragma Singleton
import QtQuick

// The OS look, as tokens. Every kit component and the Bombadil.Style controls read
// these, so an app that uses them looks like the rest of the system.
QtObject {
    // Surfaces, darkest to lightest
    readonly property color bg: "#101214"          // window background
    readonly property color panel: "#1a1d21"       // cards, panels, the bar
    readonly property color raised: "#22262b"      // hover, selected rows, inputs
    readonly property color overlay: "#2a2f36"     // popups, menus, tooltips
    readonly property color sunken: "#0c0e10"      // editors, code, wells
    readonly property color border: "#2a2f36"      // hairlines between surfaces
    readonly property color borderStrong: "#3a414a" // focused/hovered outlines
    readonly property color borderActive: "#4a525c" // the outline of whatever holds the keyboard
    // Glass: the panels that float over windows, panel #1a1d21 at a set opacity. (QML order is
    // #AARRGGBB; the design system's CSS writes the same colours #RRGGBBAA.)
    readonly property color glassPill: "#f01a1d21"  // the prompt bar, 94%
    readonly property color glassLine: "#e61a1d21"  // the line above it, 90%
    readonly property color glassChip: "#d91a1d21"  // queued prompts, quiet chips, 85%
    readonly property color glassCard: "#f51a1d21"  // the desk's cards, 96%
    readonly property color glassStrip: "#f21a1d21" // the desk's strips, 95%
    readonly property color glassRaised: "#f022262b" // a chosen chip, raised at 94%

    // Ink
    readonly property color fg: "#e6e8eb"          // primary text
    readonly property color muted: "#8b939c"       // secondary text, captions
    readonly property color faint: "#5c636b"       // placeholders, disabled, axis ticks

    // Accent (the Bombadil orange) and status tones
    readonly property color accent: "#d97757"
    readonly property color accentHover: "#e38a6c"
    readonly property color accentPressed: "#c4633f"
    readonly property color accentFg: "#ffffff"    // text on accent
    readonly property color accentSoft: "#33d97757" // tinted wash behind accent things
    readonly property color accentInk: "#f2c4b3"   // text on an accent wash (Stop)
    readonly property color accentOnLight: "#b4532f" // the accent where the ground is light (print, the lockup)
    readonly property color good: "#5fb36b"
    readonly property color warn: "#e0a93b"
    readonly property color warnInk: "#e8c38d"     // text that names a system step (its command)
    readonly property color bad: "#d05555"
    readonly property color badInk: "#f0a0a0"      // error text
    readonly property color badLine: "#7a2e2e"     // the outline of something offline
    readonly property color info: "#5b9bd5"

    // Where the ground is light: print, the stick's label, the lockup on white
    readonly property color inkOnLight: "#15181b"
    readonly property color groundLight: "#f2f3f4"

    // Chart series, in fixed order: series 1 is `series[0]`. Validated for the dark
    // surface; use them in this order and never cycle past the end.
    readonly property var series: ["#3987e5", "#d95926", "#199e70", "#c98500",
                                   "#d55181", "#008300", "#9085e9", "#e66767"]
    readonly property color grid: "#262a30"        // chart gridlines
    readonly property color axis: "#383e46"        // chart baseline

    // Shape and rhythm
    readonly property int radius: 12               // windows, cards
    readonly property int radiusSmall: 8           // controls, rows
    readonly property int pad: 16                  // default padding inside a card/window
    readonly property int gap: 12                  // default spacing between siblings
    readonly property int gapSmall: 6
    readonly property int controlHeight: 36
    readonly property int rowHeight: 44
    // The shell: the prompt bar, the line above it, chips, the desk
    readonly property int pillHeight: 52
    readonly property int radiusPill: 26           // half the pill's height
    readonly property int radiusLine: 14           // the line above the pill; chips and strips (28 high) are pills too
    readonly property int radiusLineButton: 12     // Undo, Details
    readonly property int chipHeight: 28
    readonly property int pillMaxWidth: 900
    readonly property int railWidth: 300           // a desk card's width
    readonly property real pillBorder: 1.5
    readonly property int focusRing: 2

    // Type
    readonly property string fontFamily: "Inter"
    readonly property string monoFamily: "monospace"
    readonly property int textSize: 14
    readonly property font font: Qt.font({ family: fontFamily, pixelSize: textSize })
    readonly property font monoFont: Qt.font({ family: monoFamily, pixelSize: 13 })
    readonly property int captionSize: 12
    readonly property int smallSize: 13            // counters, chips, the clock
    readonly property int lineSize: 15             // the line above the pill
    readonly property int promptSize: 16           // what you type in the pill
    readonly property int headingSize: 17
    readonly property int titleSize: 22
    readonly property int displaySize: 34

    // Motion
    readonly property int fast: 120
    readonly property int normal: 200
    readonly property int slow: 300
    readonly property real pulseLow: 0.3           // the dimmest a pulsing thing gets (reduced motion)

    // `Button { icon.source: Theme.icon("copy") }`; names are the files in icons/.
    function icon(name) { return Qt.resolvedUrl("icons/" + name + ".svg") }
    // Color for a tone name used across the kit: "accent", "good", "warn", "bad", "info", "muted".
    function tone(name) {
        switch (name) {
        case "accent": return accent
        case "good": return good
        case "warn": return warn
        case "bad": return bad
        case "info": return info
        case "muted": return muted
        default: return fg
        }
    }
    // Same color with a new alpha (0..1). `c` may be a color or a string like "#3987e5"
    // (`series` entries, colors from JSON).
    function alpha(c, a) { const k = Qt.color(c); return Qt.rgba(k.r, k.g, k.b, a) }
}

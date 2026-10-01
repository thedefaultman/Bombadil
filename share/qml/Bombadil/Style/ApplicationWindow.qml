import QtQuick
import QtQuick.Templates as T
import Bombadil

// A plain ApplicationWindow gets the OS background, and a palette so the few controls this
// style leaves to Basic (Dial, Tumbler, PageIndicator, ...) match too.
T.ApplicationWindow {
    color: Theme.bg

    palette.window: Theme.bg
    palette.windowText: Theme.fg
    palette.base: Theme.raised
    palette.alternateBase: Theme.panel
    palette.text: Theme.fg
    palette.button: Theme.raised
    palette.buttonText: Theme.fg
    palette.brightText: Theme.accentFg
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.accentFg
    palette.placeholderText: Theme.faint
    palette.toolTipBase: Theme.overlay
    palette.toolTipText: Theme.fg
    palette.link: Theme.accent
    palette.light: Theme.overlay
    palette.midlight: Theme.raised
    palette.mid: Theme.borderStrong
    palette.dark: Theme.muted
    palette.shadow: Theme.sunken
}

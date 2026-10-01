pragma ComponentBehavior: Bound
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import Bombadil as Kit

// The desk's ground: one picture per screen on the Background layer, under every window, card and
// the pill. The picture is share/wallpaper/bombadil.png (drawn by scripts/make-wallpaper.py from the
// design system's own surfaces) over the ground colour, so one that will not load leaves the plain
// ground, never a black or white screen. It takes no clicks and never moves.
//
// The picture is the user's to change: ~/.config/bombadil/wallpaper holds the path of any image (the
// last line that is not a # comment, so adding a line changes it; ~/ and file:// work). Take the file
// away, or empty it, and the standard one is back. The shell reads the file when it starts and again
// whenever it changes. New systems ship the file with only comments in it, because a file that does
// not exist cannot be watched, and then a first picture wants the bar started again.
//
// A picture of the user's own is dimmed toward the ground (`dim`), so the desk's glass and its
// muted text read over any picture, a white one included. The standard picture is drawn dark and
// is not dimmed.
Scope {
    id: wallpaper
    property bool reducedMotion: false

    // How far toward the ground a picture of the user's own is pulled. At 0.6 the muted text keeps a
    // contrast of 4.5 on the quietest glass (the chips, 85%) over a pure white stripe.
    readonly property real dim: 0.6

    // Not Qt.resolvedUrl("../share/..."): Quickshell resolves anything outside the shell's own
    // folder to a dead end (qrc:/qs-blackhole). share/ sits beside shell/ in the repository and in
    // the installed tree (/usr/share/bombadil), so the path is made from the shell's folder.
    readonly property url standard: "file://" + Quickshell.shellPath("../share/wallpaper/bombadil.png")

    function _configDir() {
        const own = Quickshell.env("BOMBADIL_CONFIG")
        if (own) return own
        return (Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")) + "/bombadil"
    }

    // The user's choice: the last line that is not a comment, naming an image. A missing or empty
    // file is no choice, and says nothing. A line that is not a path (an image written into the file
    // itself is the usual one) says so once in the log, never what is in it.
    FileView {
        id: choice
        path: wallpaper._configDir() + "/wallpaper"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
    }
    function _pick(text) {
        const lines = text.split("\n").map(l => l.trim()).filter(l => l !== "" && !l.startsWith("#"))
        if (lines.length === 0) return standard
        let p = lines[lines.length - 1]
        if (p.startsWith("~/")) p = Quickshell.env("HOME") + p.slice(1)
        if (p.startsWith("/")) return "file://" + p
        if (p.startsWith("file://")) return p
        console.warn("wallpaper: " + _configDir() + "/wallpaper holds the path of an image, on one line,"
                     + " not the image itself (a path starts with / or ~/ or file://)")
        return standard
    }
    readonly property url chosen: _pick(choice.text())
    // Whether the picture is the user's own (and so dimmed).
    readonly property bool own: chosen.toString() !== standard.toString()

    Variants {
        model: Quickshell.screens
        PanelWindow {
            id: win
            required property var modelData
            // Set when the chosen picture would not load: the standard one stands in for it.
            property bool failed: false
            screen: modelData
            anchors { top: true; bottom: true; left: true; right: true }
            // Under the bar's zone too: the picture fills the screen to its edge.
            exclusionMode: ExclusionMode.Ignore
            color: Kit.Theme.bg
            mask: Region {}
            WlrLayershell.layer: WlrLayer.Background
            WlrLayershell.namespace: "bombadil-wallpaper"

            Connections {
                target: wallpaper
                function onChosenChanged() { win.failed = false }
            }

            Image {
                anchors.fill: parent
                source: win.failed ? wallpaper.standard : wallpaper.chosen
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                smooth: true
                // The ground comes first and the picture settles into it, once.
                opacity: status === Image.Ready ? 1 : 0
                Behavior on opacity {
                    NumberAnimation { duration: wallpaper.reducedMotion ? 0 : Kit.Theme.slow }
                }
                onStatusChanged: if (status === Image.Error) win.failed = true
            }

            // The dimming over a picture of the user's own, in the ground colour: the picture's
            // brightest part is no brighter than the ground plus 40% of its own light.
            Rectangle {
                anchors.fill: parent
                color: Kit.Theme.bg
                opacity: wallpaper.own && !win.failed ? wallpaper.dim : 0
                Behavior on opacity {
                    NumberAnimation { duration: wallpaper.reducedMotion ? 0 : Kit.Theme.slow }
                }
            }
        }
    }
}

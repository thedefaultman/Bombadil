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
// The picture is the user's to change: ~/.config/bombadil/wallpaper holds the path of any image
// (one line; ~/ and file:// work). Take the file away and the standard one is back. The shell reads
// the file when it starts and again whenever it changes, so a first one wants a restart of the bar
// (`systemctl --user restart bombadil-shell`).
Scope {
    id: wallpaper
    property bool reducedMotion: false

    // Not Qt.resolvedUrl("../share/..."): Quickshell resolves anything outside the shell's own
    // folder to a dead end (qrc:/qs-blackhole). share/ sits beside shell/ in the repository and in
    // the installed tree (/usr/share/bombadil), so the path is made from the shell's folder.
    readonly property url standard: "file://" + Quickshell.shellPath("../share/wallpaper/bombadil.png")

    function _configDir() {
        const own = Quickshell.env("BOMBADIL_CONFIG")
        if (own) return own
        return (Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")) + "/bombadil"
    }

    // The user's choice: one line naming an image. A missing file is no choice, and says nothing.
    FileView {
        id: choice
        path: wallpaper._configDir() + "/wallpaper"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
    }
    readonly property url chosen: {
        let p = choice.text().trim().split("\n")[0].trim()
        if (p === "") return standard
        if (p.startsWith("~/")) p = Quickshell.env("HOME") + p.slice(1)
        if (p.startsWith("/")) return "file://" + p
        if (p.startsWith("file://")) return p
        return standard
    }

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
        }
    }
}

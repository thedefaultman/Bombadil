pragma ComponentBehavior: Bound
import Quickshell
import Quickshell.Wayland
import QtQuick
import "DeskTheme.js" as T

// The two rails on one screen: a window each side on the Bottom layer, under every app window
// and over the wallpaper. They reserve nothing (the bar's zone is the only one), stop where the
// bar's zone starts, and take input only where a card is. On a screen the desk does not live on,
// or while a full-screen window has the stage, there is no window at all.
Scope {
    id: rails
    required property var desk      // a DeskState
    property var screen: null
    property bool active: true

    component Side: PanelWindow {
        id: win
        required property string side
        screen: rails.screen
        visible: rails.active && !rails.desk.capsule && rail.shown
        anchors { top: true; bottom: true; left: side === "left"; right: side === "right" }
        margins { top: T.railTop; left: T.railMargin; right: T.railMargin }
        implicitWidth: T.cardWidth
        exclusionMode: ExclusionMode.Normal
        exclusiveZone: 0
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Bottom
        WlrLayershell.namespace: "bombadil-desk-" + side
        mask: Region { item: rail.hitArea }

        DeskRail {
            id: rail
            desk: rails.desk
            side: win.side
            width: T.cardWidth
            height: win.height
        }
    }

    Side { side: "left" }
    Side { side: "right" }
}

pragma ComponentBehavior: Bound
import QtQuick
import "DeskTheme.js" as T

// The strips of one side, in the pill's row: the left ones right-aligned to the pill and growing
// outward, the right ones left-aligned. A strip that a card just folded into fades in.
Item {
    id: strips
    required property var desk        // a DeskState
    required property string side     // "left" or "right"
    property bool active: true        // false on a screen the desk does not live on
    property real pillEdge: 0         // x of the pill's edge on this side
    property real pillCentreY: 0

    readonly property var entries: side === "left" ? desk.leftStrips : desk.rightStrips
    readonly property bool shown: active && entries.length > 0 && !desk.capsule

    // Empty when there is nothing to show, so the bar's input mask takes no room for it.
    width: shown ? row.implicitWidth : 0
    height: shown ? T.stripHeight : 0
    x: side === "left" ? pillEdge - T.stripGap - width : pillEdge + T.stripGap
    y: pillCentreY - T.stripHeight / 2

    // The pill is as wide as the stage leaves it: DeskState needs to know how much the strips take.
    Binding {
        target: strips.desk
        property: strips.side === "left" ? "leftStripsWidth" : "rightStripsWidth"
        value: row.implicitWidth
        when: strips.active
    }

    Row {
        id: row
        spacing: T.stripGap
        layoutDirection: strips.side === "left" ? Qt.RightToLeft : Qt.LeftToRight

        Repeater {
            model: strips.entries

            DeskStrip {
                required property var modelData
                objectName: modelData.plus ? "deskStripMore" : "deskStrip-" + modelData.id
                text: modelData.text
                dot: modelData.dot
                ring: modelData.ring
                mark: modelData.mark
                textColor: modelData.textColor
                outlined: modelData.outlined
                opacity: 0
                Behavior on opacity { NumberAnimation { duration: strips.desk.foldMs } }
                Component.onCompleted: opacity = 1
            }
        }
    }
}

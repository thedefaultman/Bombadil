pragma ComponentBehavior: Bound
import QtQuick
import "DeskTheme.js" as T

// The strips of one side, in the pill's row: the left ones right-aligned to the pill and growing
// outward, the right ones left-aligned. A strip that a card just folded into fades in. A strip can
// be dragged into a rail, and a card dragged over the row leaves its ghost chip at the outer end.
Item {
    id: strips
    required property var desk        // a DeskState
    required property string side     // "left" or "right"
    property bool active: true        // false on a screen the desk does not live on
    property real pillEdge: 0         // x of the pill's edge on this side
    property real pillCentreY: 0
    property point origin: Qt.point(0, 0)   // the bar window's top-left on the screen, for a drag

    readonly property var entries: side === "left" ? desk.leftStrips : desk.rightStrips
    readonly property bool shown: active && entries.length > 0 && !desk.capsule
    // The chip of the card in hand, while it is over the row and was taken from this side.
    readonly property var ghost: active && desk.foldChip !== null && desk.drag.side === side ? desk.foldChip : null

    // Empty when there is nothing to show, so the bar's input mask takes no room for it; and not
    // drawn at all on a screen the desk does not live on (its row would spill out of the zero width).
    visible: shown || ghost !== null
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
        // Another screen's instance takes over when the desk moves; this one must not put the old
        // value back over what that one has just said.
        restoreMode: Binding.RestoreNone
    }

    Row {
        id: row
        spacing: T.stripGap
        layoutDirection: strips.side === "left" ? Qt.RightToLeft : Qt.LeftToRight

        // One item per place, reading its strip by place: a strip whose text changes (an ISO's
        // percent) updates in place, so the others neither blink nor lose the pointer.
        Repeater {
            model: strips.entries.length

            DeskStrip {
                id: chip
                required property int index
                readonly property var entry: strips.entries[index] || ({})
                objectName: entry.plus ? "deskStripMore" : "deskStrip-" + entry.id
                text: entry.text || ""
                dot: entry.dot || ""
                ring: !!entry.ring
                mark: !!entry.mark
                textColor: entry.textColor !== undefined ? entry.textColor : T.muted
                outlined: !!entry.outlined
                opacity: 0
                Behavior on opacity { NumberAnimation { duration: strips.desk.foldMs } }
                Component.onCompleted: opacity = 1

                // Taken by place, so the widget is read when the drag starts. The "+N" chip is no widget's
                // (its id is "more"), and a drag of what is not there is refused.
                DeskDrag {
                    desk: strips.desk
                    widget: chip.entry.id || ""
                    from: "strip"
                    origin: strips.origin
                }
                // The strip in hand, and the one a drop was sent for until agentd answers, are dimmed.
                Rectangle {
                    objectName: "deskStripDim"
                    anchors.fill: parent
                    radius: parent.radius
                    color: T.strip
                    opacity: 0.55
                    visible: strips.desk.settling === chip.entry.id
                             || (strips.desk.dragging && strips.desk.drag.id === chip.entry.id)
                }
            }
        }
    }

    // What the card in hand would become if it were dropped on the row, past the strips this side has:
    // outside the row, so it neither moves the pill nor widens what the bar's mask holds.
    DeskStrip {
        objectName: "deskStripGhost"
        readonly property real apart: strips.shown ? T.stripGap : 0   // a chip's gap, if one stands before it
        visible: strips.ghost !== null
        text: strips.ghost ? strips.ghost.text : ""
        dot: strips.ghost ? strips.ghost.dot : ""
        mark: strips.ghost ? strips.ghost.mark : false
        textColor: strips.ghost ? strips.ghost.textColor : T.muted
        outlined: true
        opacity: 0.6
        x: strips.side === "left" ? -apart - width : strips.width + apart
    }
}

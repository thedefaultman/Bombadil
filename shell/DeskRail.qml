pragma ComponentBehavior: Bound
import QtQuick
import "DeskTheme.js" as T

// One rail's content: the card of every widget that lives on this side, each at its slot. A card
// is always here; folding shrinks it toward the rail's outer bottom corner while it fades, and
// unfolding is the same move back. A widget with no face of its own yet draws nothing.
Item {
    id: rail
    required property var desk        // a DeskState
    required property string side     // "left" or "right"

    // What the faces cover, in this rail's own coordinates: the window's input mask, so a click
    // beside a card reaches the wallpaper.
    readonly property var hitRects: {
        const out = []
        for (const id of desk.order[side]) {
            const s = desk.slots[id]
            if (s && desk.faces[id] === "full")
                out.push({ x: s.x - desk.railX(side), y: s.y - T.railTop, w: s.w, h: s.h })
        }
        return out
    }

    // Whether the rail's window is up: while a card is meant to be in full, and for as long as one
    // takes to fold away. It follows what the desk says, never the animation: a window that is not
    // up runs no animation, so a card that waited for its own opacity to show the window would
    // never come back.
    readonly property bool anyFull: hitRects.length > 0
    property bool shown: anyFull || leaving.running
    onAnyFullChanged: if (!anyFull) leaving.restart()
    Timer {
        id: leaving
        interval: rail.desk.foldMs + 80
    }

    // One box around every full card, for the window's input mask: a click beside the cards goes
    // through to what is behind the rail.
    readonly property var hitBox: {
        if (hitRects.length === 0) return { x: 0, y: 0, w: 0, h: 0 }
        let x0 = Infinity, y0 = Infinity, x1 = 0, y1 = 0
        for (const r of hitRects) {
            x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y)
            x1 = Math.max(x1, r.x + r.w); y1 = Math.max(y1, r.y + r.h)
        }
        return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
    }
    Item {
        id: box
        objectName: "hitArea"
        x: rail.hitBox.x; y: rail.hitBox.y; width: rail.hitBox.w; height: rail.hitBox.h
    }
    readonly property Item hitArea: box

    Repeater {
        model: rail.desk.order[rail.side]

        Item {
            id: cell
            required property string modelData
            objectName: "deskCard-" + modelData

            // Where the slot is; a card that leaves folds where it stood.
            readonly property var slot: rail.desk.slots[modelData] || null
            property var placed: null
            onSlotChanged: if (slot) placed = slot
            Component.onCompleted: if (slot) placed = slot

            readonly property bool full: rail.desk.faces[modelData] === "full"

            x: placed ? placed.x - rail.desk.railX(rail.side) : 0
            y: placed ? placed.y - T.railTop : 0
            width: T.cardWidth
            height: placed ? placed.h : 0
            opacity: full ? 1 : 0
            scale: full ? 1 : 0.6
            visible: opacity > 0
            transformOrigin: rail.side === "left" ? Item.BottomLeft : Item.BottomRight
            Behavior on opacity { NumberAnimation { duration: rail.desk.foldMs; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: rail.desk.foldMs; easing.type: Easing.OutCubic } }

            Loader {
                anchors.bottom: parent.bottom
                sourceComponent: cell.modelData === "now" ? nowFace
                               : cell.modelData === "watching" || cell.modelData === "needs"
                                 || cell.modelData === "machine" ? rowsFace : null
            }

            Component {
                id: nowFace
                NowCard {
                    model: rail.desk.nowModel
                    onTitleClicked: rail.desk.openDetails()
                }
            }
            Component {
                id: rowsFace
                RowsCard {
                    readonly property var current: cell.modelData === "watching" ? rail.desk.watchModel
                        : cell.modelData === "machine" ? rail.desk.machineModel : rail.desk.needsModel
                    // Machine has no model while it is calm: its card folds away with what it last said.
                    property var last: null
                    onCurrentChanged: if (current) last = current
                    Component.onCompleted: if (current) last = current
                    model: current || last
                    onRowAction: (key, action) => rail.desk.rowAction(cell.modelData, key, action)
                    onRowRemove: key => rail.desk.rowRemove(cell.modelData, key)
                    onRowOpen: (key, opens) => rail.desk.rowOpen(cell.modelData, key, opens)
                }
            }
        }
    }
}

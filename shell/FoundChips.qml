import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The chips under "Kept for 15:00. Found on this computer:": up to three things the sentence you typed
// nearly names (an app, a launcher word, a past ask of yours), each with a small word for what it is
// ("App", "Opens it", "Its steps"). Nothing opens by itself: a press sends the chip's id to agentd,
// which opens the thing and lets go of the kept ask it came from. They are there while that line is.
RowLayout {
    id: chips
    required property var pill       // a PillState

    visible: pill.foundChips.length > 0
    spacing: 8

    Repeater {
        model: chips.pill.foundChips
        Rectangle {
            id: chip
            required property var modelData
            objectName: "foundChip"
            // As wide as its words (a past ask's label is up to 60 characters). A row too wide for the bar
            // shrinks its chips, and cuts their labels short.
            Layout.fillWidth: true
            Layout.minimumWidth: 96
            Layout.maximumWidth: 520
            implicitWidth: row.implicitWidth + 28
            implicitHeight: 30
            radius: implicitHeight / 2
            color: tap.pressed ? Kit.Theme.borderStrong : hover.hovered ? Kit.Theme.border : Kit.Theme.glassChip
            border.width: 1
            border.color: hover.hovered ? Kit.Theme.borderStrong : Kit.Theme.border
            Behavior on border.color { ColorAnimation { duration: Kit.Theme.normal } }

            RowLayout {
                id: row
                anchors { fill: parent; leftMargin: 14; rightMargin: 14 }
                spacing: 8
                // The user's own words ("You asked: ..."): plain text, never markup.
                Text {
                    objectName: "foundLabel"
                    font.family: Kit.Theme.fontFamily
                    Layout.fillWidth: true
                    Layout.maximumWidth: 420
                    text: chip.modelData.label
                    color: Kit.Theme.fg
                    font.pixelSize: Kit.Theme.smallSize
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }
                Text {
                    objectName: "foundHint"
                    font.family: Kit.Theme.fontFamily
                    visible: text !== ""
                    text: chip.modelData.hint || ""
                    color: Kit.Theme.muted
                    font.pixelSize: Kit.Theme.captionSize
                    textFormat: Text.PlainText
                }
            }

            // A chip being reached for holds the line (and so itself) in place, as the line's own hover does.
            // Counted once, and let go the moment it hides or goes (a hidden chip gets no hover-out).
            property bool held: false
            function hold(on) {
                if (on === held) return
                held = on
                chips.pill.hovers = Math.max(0, chips.pill.hovers + (on ? 1 : -1))
            }
            onVisibleChanged: if (!visible) hold(false)
            Component.onDestruction: hold(false)
            HoverHandler {
                id: hover
                cursorShape: Qt.PointingHandCursor
                onHoveredChanged: chip.hold(hovered)
            }
            TapHandler {
                id: tap
                gesturePolicy: TapHandler.ReleaseWithinBounds
                onTapped: chips.pill.openFound(chip.modelData.id)
            }
        }
    }
}

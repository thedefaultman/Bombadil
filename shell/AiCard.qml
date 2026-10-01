import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The card a click on the stone opens when no turn runs: one row per AI ("Claude · ready") with a
// switch. On lets the AI answer; off rests it by hand, and the stone goes hollow. A switch that
// cannot be flipped (not signed in, not installed) is dimmed. The rows are agentd's: a switch
// follows what agentd says, not the tap. Esc, a second click on the stone or typing puts it away
// (PillState.aiOpen).
Rectangle {
    id: card
    required property var pill       // a PillState

    readonly property bool shown: pill.aiOpen && pill.aiRows.length > 0

    implicitWidth: 360
    implicitHeight: shown ? rows.implicitHeight + 24 : 0
    radius: Kit.Theme.radiusLine
    color: Kit.Theme.glassLine
    border.width: 1
    border.color: Kit.Theme.border
    opacity: shown ? 1 : 0
    visible: opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: Kit.Theme.normal } }
    Behavior on implicitHeight { NumberAnimation { duration: Kit.Theme.fast; easing.type: Easing.OutCubic } }

    ColumnLayout {
        id: rows
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12; leftMargin: 18; rightMargin: 12 }
        spacing: 4

        // By position, so a row that agentd sends again is the same row and its switch slides.
        Repeater {
            model: card.pill.aiRows.length
            RowLayout {
                id: row
                required property int index
                readonly property var ai: card.pill.aiRows[index] || ({})
                objectName: "aiRow"
                Layout.fillWidth: true
                spacing: 8

                Text {
                    objectName: "aiTitle"
                    font.family: Kit.Theme.fontFamily
                    text: row.ai.title
                    color: Kit.Theme.fg
                    font.pixelSize: Kit.Theme.smallSize
                    textFormat: Text.PlainText
                }
                Text {
                    objectName: "aiText"
                    font.family: Kit.Theme.fontFamily
                    Layout.fillWidth: true
                    text: "· " + (row.ai.text || "")
                    color: Kit.Theme.muted
                    font.pixelSize: Kit.Theme.smallSize
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }

                // The switch, as the kit's own (36 by 20, the knob 14): orange when on.
                Rectangle {
                    id: toggle
                    objectName: "aiSwitch"
                    readonly property bool on: row.ai.on === true
                    readonly property bool usable: row.ai.enabled === true
                    implicitWidth: 36
                    implicitHeight: 20
                    Layout.topMargin: 6
                    Layout.bottomMargin: 6
                    radius: height / 2
                    opacity: usable ? 1 : 0.4
                    color: on ? (tap.pressed ? Kit.Theme.accentPressed : hover.hovered ? Kit.Theme.accentHover : Kit.Theme.accent)
                              : (hover.hovered ? Qt.lighter(Kit.Theme.borderStrong, 1.2) : Kit.Theme.borderStrong)
                    Behavior on color { ColorAnimation { duration: Kit.Theme.fast } }

                    Rectangle {
                        x: toggle.on ? parent.width - width - 3 : 3
                        y: 3
                        width: 14; height: 14; radius: 7
                        color: toggle.on ? Kit.Theme.accentFg : Kit.Theme.fg
                        Behavior on x { NumberAnimation { duration: Kit.Theme.fast; easing.type: Easing.OutCubic } }
                    }
                    HoverHandler { id: hover; enabled: toggle.usable; cursorShape: Qt.PointingHandCursor; margin: 6 }
                    TapHandler {
                        id: tap
                        enabled: toggle.usable
                        margin: 6
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: card.pill.setAi(row.ai.name, !toggle.on)
                    }
                }
            }
        }
    }
}

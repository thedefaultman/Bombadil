import QtQuick
import QtQuick.Layouts

// Beside the pill: one chip per project with a dot for each coding session. A dot moves while
// its session works, is lit when it is your turn, red when it failed and dim when it sleeps.
// Hover a dot to peek, click it to bring that session to the front. More than three projects
// fold into one chip ("4 projects · 1 waiting") that opens on a click.
RowLayout {
    id: chips
    required property var dev

    visible: dev.sessions.length > 0
    spacing: 6

    // The folded chip.
    Rectangle {
        objectName: "foldChip"
        visible: chips.dev.folded
        implicitWidth: foldText.implicitWidth + 24
        implicitHeight: 28
        radius: 14
        color: "#d91a1d21"
        border.width: 1
        border.color: chips.dev.waiting > 0 ? "#6b5446" : "#2a2f36"
        Text {
            id: foldText
            anchors.centerIn: parent
            text: chips.dev.foldText
            color: "#a9b0b8"
            font.pixelSize: 12
        }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: chips.dev.expanded = true }
    }

    Repeater {
        model: chips.dev.folded ? [] : chips.dev.projects
        Rectangle {
            id: chip
            required property var modelData
            objectName: "projectChip"
            implicitWidth: row.implicitWidth + 20
            implicitHeight: 28
            radius: 14
            color: "#d91a1d21"
            border.width: 1
            border.color: chip.modelData.waiting > 0 ? "#6b5446" : "#2a2f36"
            Behavior on border.color { ColorAnimation { duration: 300 } }

            RowLayout {
                id: row
                anchors.centerIn: parent
                spacing: 7
                Text {
                    text: chip.modelData.title
                    color: "#a9b0b8"
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                    Layout.maximumWidth: 120
                    elide: Text.ElideRight
                }
                Row {
                    spacing: 5
                    Repeater {
                        model: chip.modelData.sessions
                        Item {
                            id: dot
                            required property var modelData
                            objectName: "sessionDot"
                            property string key: dot.modelData.key
                            property string look: chips.dev.look(dot.modelData)
                            width: 10; height: 10

                            // Lit: a soft halo says it is your turn.
                            Rectangle {
                                anchors.centerIn: parent
                                width: 16; height: 16; radius: 8
                                color: "transparent"
                                border.width: 2
                                border.color: "#55ffd9b8"
                                visible: dot.look === "turn"
                            }
                            Rectangle {
                                id: disc
                                anchors.fill: parent
                                radius: 5
                                // A session you started by hand is a ring: seen, not managed.
                                color: dot.modelData.yours ? "transparent" : chips.dev.color(dot.modelData)
                                border.width: dot.modelData.yours ? 2 : 0
                                border.color: chips.dev.color(dot.modelData)
                                opacity: dot.look === "asleep" ? 0.7 : 1
                                Behavior on color { ColorAnimation { duration: 250 } }
                                SequentialAnimation on opacity {
                                    running: dot.look === "working"; loops: Animation.Infinite
                                    NumberAnimation { to: 0.3; duration: 600; easing.type: Easing.InOutSine }
                                    NumberAnimation { to: 1; duration: 600; easing.type: Easing.InOutSine }
                                    onRunningChanged: if (!running) disc.opacity = dot.look === "asleep" ? 0.7 : 1
                                }
                            }
                            HoverHandler {
                                margin: 4
                                cursorShape: Qt.PointingHandCursor
                                onHoveredChanged: hovered ? chips.dev.peek(dot.key) : chips.dev.unpeek(dot.key)
                            }
                            TapHandler { margin: 4; onTapped: chips.dev.open(dot.key) }
                        }
                    }
                }
            }
        }
    }
}

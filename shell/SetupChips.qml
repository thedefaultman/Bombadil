import QtQuick
import QtQuick.Layouts

// The chips under the setup line: which AI runs the machine (two big chips on first boot),
// Sign in, Show sign-in, Wi-Fi. A click sends the chip's id to agentd.
RowLayout {
    id: chips
    required property var pill

    visible: pill.setupActions.length > 0 && pill.mode === "setup"
    spacing: 8

    Repeater {
        model: chips.pill.setupActions
        Rectangle {
            id: chip
            required property var modelData
            readonly property bool big: modelData.style === "big"
            readonly property bool primary: modelData.style === "primary"
            objectName: "setupChip"
            implicitWidth: Math.max(big ? 132 : 0, label.implicitWidth + (big ? 48 : 28))
            implicitHeight: big ? 46 : 30
            radius: implicitHeight / 2
            color: tap.pressed ? "#353b43" : hover.hovered ? "#2a2f36" : (big || primary ? "#f022262b" : "#d91a1d21")
            border.width: 1
            border.color: big || primary ? (hover.hovered ? "#e89a80" : "#d97757") : "#2a2f36"
            Behavior on border.color { ColorAnimation { duration: 150 } }

            Text {
                id: label
                anchors.centerIn: parent
                text: chip.modelData.label
                color: chip.big || chip.primary ? "#e6e8eb" : "#a9b0b8"
                font.pixelSize: chip.big ? 17 : 13
                font.weight: chip.big ? Font.DemiBold : Font.Normal
                textFormat: Text.PlainText
            }
            HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                id: tap
                gesturePolicy: TapHandler.ReleaseWithinBounds
                onTapped: chips.pill.setupAction(chip.modelData.id)
            }
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Where you have been, left to right: "Bombadil › snapshots.py › test_snapshots.py".
// Clicking one goes back to it.
RowLayout {
    id: trail
    property var steps: []
    signal jumped(int index)
    spacing: 2

    IconButton {
        icon: "arrow-left"
        tooltip: "Back (Alt+Left)"
        enabled: trail.steps.length > 1
        opacity: enabled ? 1 : 0.35
        onClicked: trail.jumped(trail.steps.length - 2)
    }

    Repeater {
        model: trail.steps
        RowLayout {
            spacing: 6
            Layout.leftMargin: index > 0 ? 4 : 2
            readonly property bool last: index === trail.steps.length - 1
            Text {
                visible: index > 0
                text: "›"
                color: Theme.faint
                font.pixelSize: Theme.textSize
                font.family: Theme.fontFamily
            }
            Text {
                text: modelData.title || "…"
                color: parent.last ? Theme.fg : (hover.containsMouse ? Theme.fg : Theme.muted)
                font.pixelSize: Theme.textSize
                font.family: Theme.fontFamily
                font.weight: parent.last ? Font.DemiBold : Font.Normal
                elide: Text.ElideMiddle
                Layout.maximumWidth: 220
                MouseArea {
                    id: hover
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: !parent.parent.last
                    cursorShape: Qt.PointingHandCursor
                    onClicked: trail.jumped(index)
                }
            }
        }
    }
    Spacer {}
}

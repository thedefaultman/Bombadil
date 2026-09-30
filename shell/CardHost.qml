import QtQuick
import QtQuick.Layouts
import "../share/qml/Bombadil" as Kit

// The picture above the line: a diagram the machine drew from itself (system_map, a receipt) or
// the agent drew with show_card. One at a time. It draws with the kit's Diagram, so it looks like
// every other picture in the OS; this file only holds it: the title, where it came from, the ×,
// and the sentence under it. Esc or × puts it away, a box that names something opens it.
// (The first brief's other card kinds - list, checklist, timer - get their own face beside the
// diagram here.)
Rectangle {
    id: host
    required property var pill       // a PillState
    property int maxHeight: 560      // taller pictures scroll

    // The card being shown, kept a moment after it goes so it fades instead of blanking.
    readonly property var current: pill.card
    property var last: null
    onCurrentChanged: if (current) last = current
    readonly property bool shown: !!current
    readonly property bool partial: !!(last && last.partial)

    implicitHeight: shown ? content.implicitHeight + 28 : 0
    radius: 14
    color: "#e61a1d21"
    border.width: 1
    border.color: "#2a2f36"
    opacity: shown ? 1 : 0
    visible: opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: 200 } }
    Behavior on implicitHeight { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    // Resting the mouse on a picture keeps the closing line (and a receipt with it) from fading.
    HoverHandler {
        id: hover
        onHoveredChanged: host.pill.hovers = Math.max(0, host.pill.hovers + (hovered ? 1 : -1))
    }
    Component.onDestruction: if (hover.hovered) host.pill.hovers = Math.max(0, host.pill.hovers - 1)

    Accessible.role: Accessible.StaticText
    Accessible.name: last && last.text ? last.text : ""

    ColumnLayout {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 14; leftMargin: 18; rightMargin: 14 }
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Text {
                objectName: "cardTitle"
                Layout.fillWidth: true
                text: host.last && host.last.title ? host.last.title : ""
                color: "#e6e8eb"
                font.pixelSize: 15
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }
            // Where the picture came from: the machine itself, or the agent's own drawing.
            Text {
                objectName: "cardSource"
                text: host.partial ? "drawing…"
                    : host.last && host.last.source ? "from this machine"
                    : "drawn by the agent"
                color: "#8b939c"
                font.pixelSize: 12
            }
            Text {
                objectName: "cardClose"
                text: "×"
                color: closeArea.containsMouse ? "#e6e8eb" : "#8b939c"
                font.pixelSize: 18
                Accessible.role: Accessible.Button
                Accessible.name: "Close the picture"
                MouseArea {
                    id: closeArea
                    anchors.fill: parent
                    anchors.margins: -8
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: host.pill.dismissCard()
                }
            }
        }

        Flickable {
            id: flick
            objectName: "cardScroll"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(diagram.implicitHeight, host.maxHeight - 120)
            contentWidth: width
            contentHeight: diagram.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true

            Kit.Diagram {
                id: diagram
                objectName: "cardDiagram"
                width: flick.width
                spec: host.last
                showTitle: false
                onOpened: target => host.pill.openThing(target)
            }
        }

        Text {
            objectName: "cardSay"
            Layout.fillWidth: true
            visible: !!(host.last && host.last.say)
            text: host.last && host.last.say ? host.last.say : ""
            color: "#e6e8eb"
            font.pixelSize: 14
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
    }
}

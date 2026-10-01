import QtQuick
import QtQuick.Layouts
// The kit is on the import path (bin/bombadil-shell), like for every other file in the shell.
import Bombadil as Kit

// The picture above the line: a diagram the machine drew from itself (system_map, a receipt) or
// the agent drew with show_card. One at a time. It draws with the kit's Diagram, so it looks like
// every other picture in the OS; this file only holds it: the title, where it came from, the ×,
// and the sentence under it. Esc or × puts it away, a box that names something opens it.
// (The first brief's other card kinds - list, checklist, timer - get their own face beside the
// diagram here.)
Rectangle {
    id: host
    objectName: "cardHost"
    required property var pill       // a PillState
    property int maxHeight: 560      // taller pictures scroll

    // The card being shown, kept a moment after it goes so it fades instead of blanking.
    readonly property var current: pill.card
    property var last: null
    onCurrentChanged: if (current) last = current
    readonly property bool shown: !!current
    readonly property bool partial: !!(last && last.partial)

    // The height is not animated. The bar's window is as tall as its contents, so a height that
    // grows over a few frames resizes the window every frame, and the compositor shows the pill
    // jumping while it catches up. The window grows once; the card eases in inside the space, by
    // fading and rising a few pixels instead, and gives the space back once it has faded out.
    implicitHeight: shown || opacity > 0 ? content.implicitHeight + 28 : 0
    radius: Kit.Theme.radiusLine
    color: Kit.Theme.glassLine
    border.width: 1
    border.color: Kit.Theme.border
    opacity: shown ? 1 : 0
    visible: opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: Kit.Theme.normal } }
    transform: Translate {
        y: host.shown ? 0 : 14
        Behavior on y { NumberAnimation { duration: Kit.Theme.normal; easing.type: Easing.OutCubic } }
    }

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
                font.family: Kit.Theme.fontFamily
                color: Kit.Theme.fg
                font.pixelSize: Kit.Theme.lineSize
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
                font.family: Kit.Theme.fontFamily
                color: Kit.Theme.muted
                font.pixelSize: Kit.Theme.captionSize
            }
            Text {
                objectName: "cardClose"
                text: "×"
                font.family: Kit.Theme.fontFamily
                color: closeArea.containsMouse ? Kit.Theme.fg : Kit.Theme.muted
                font.pixelSize: Kit.Theme.headingSize
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
            font.family: Kit.Theme.fontFamily
            color: Kit.Theme.fg
            font.pixelSize: Kit.Theme.textSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
    }
}

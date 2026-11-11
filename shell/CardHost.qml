import QtQuick
import QtQuick.Layouts
// The kit is on the import path (bin/bombadil-shell), like for every other file in the shell.
import Bombadil as Kit

// The card above the line. A diagram the machine drew from itself (system_map, a receipt) or the agent
// drew with show_card is drawn with the kit's Diagram, so it looks like every other picture in the OS.
// The three kinds that come from the person's connections have a face of their own beside it: a message
// (the thread and a box to reply in), a task (its fields and a button) and a list (rows with buttons).
// One at a time. This file only holds the card: the title, where it came from, the ×, the face by the
// card's type, and the sentence under it. Esc or × puts it away; a box that names something opens it.
//
// A card with a box in it is not drawn on every screen the way a picture is: two boxes for one draft would
// be two people typing into one proposal. `onThisScreen` says which screen has it (shell.qml).
Rectangle {
    id: host
    objectName: "cardHost"
    required property var pill       // a PillState
    property int maxHeight: 560      // taller pictures scroll
    // Put away while a full-screen window has the screen (the pill is a dot and a clock then): a
    // picture hanging over it, in the middle of whatever it shows, is in the way. It comes back after.
    property bool suppressed: false
    // The screen the person is on holds a card with a box (a picture is drawn on all of them).
    property bool onThisScreen: true

    // The card being shown, kept a moment after it goes so it fades instead of blanking.
    readonly property var current: pill.card
    property var last: null
    onCurrentChanged: if (current) last = current
    readonly property string kind: last && last.type ? String(last.type) : ""
    readonly property bool shown: !!current && !suppressed && (onThisScreen || kind === "diagram")
    readonly property bool partial: !!(last && last.partial)
    // A card of the person's connections is about as wide as its words need (a diagram takes the width it is given).
    readonly property int preferredWidth: kind === "message" || kind === "task" ? 560 : kind === "list" ? 480 : 0
    readonly property int bodyMax: Math.max(160, maxHeight - 120)

    // The keyboard goes to the card's box (the bar was just given it): false when this card has none.
    function focusBox() {
        const f = face.item
        if (f && f.focusBox) { f.focusBox(); return true }
        return false
    }

    // The height is not animated. The bar's window is as tall as its contents, so a height that
    // grows over a few frames resizes the window every frame (each resize is a blip on the screen,
    // and a compositor that animates layers, as Hyprland does unless its "layers" animation is off,
    // swings the pill with every one). The window grows once; the card eases in inside the space,
    // by fading and rising a few pixels instead, and gives the space back once it has faded out.
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
                text: host.kind !== "diagram" ? (host.last && host.last.via ? host.last.via : "")
                    : host.partial ? "drawing…"
                    : host.last && host.last.source ? "from this machine"
                    : "drawn by the agent"
                font.family: Kit.Theme.fontFamily
                color: Kit.Theme.muted
                font.pixelSize: Kit.Theme.captionSize
                textFormat: Text.PlainText
            }
            Text {
                objectName: "cardClose"
                text: "×"
                textFormat: Text.PlainText
                font.family: Kit.Theme.fontFamily
                color: closeArea.containsMouse ? Kit.Theme.fg : Kit.Theme.muted
                font.pixelSize: Kit.Theme.headingSize
                Accessible.role: Accessible.Button
                Accessible.name: host.kind === "diagram" ? "Close the picture" : "Close the card"
                activeFocusOnTab: true
                Keys.onReturnPressed: host.pill.dismissCard()
                Keys.onSpacePressed: host.pill.dismissCard()
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
            visible: host.kind === "diagram" || host.kind === ""
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(diagram.implicitHeight, host.bodyMax)
            contentWidth: width
            contentHeight: diagram.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            clip: true

            Kit.Diagram {
                id: diagram
                objectName: "cardDiagram"
                width: flick.width
                spec: host.kind === "diagram" ? host.last : null
                showTitle: false
                onOpened: target => host.pill.openThing(target)
            }
        }

        // The face of a connection's card, by its type. Built when the card comes and dropped when another kind
        // does, so what it holds (a box, a timer) is never one card's left over in the next.
        Loader {
            id: face
            objectName: "cardFace"
            Layout.fillWidth: true
            visible: status === Loader.Ready
            sourceComponent: host.kind === "message" ? messageFace
                           : host.kind === "task" ? taskFace
                           : host.kind === "list" ? listFace : null
        }
        Component { id: messageFace; MessageFace { pill: host.pill; card: host.last; maxHeight: host.bodyMax } }
        Component { id: taskFace; TaskFace { pill: host.pill; card: host.last; maxHeight: host.bodyMax } }
        Component { id: listFace; ListFace { pill: host.pill; card: host.last; maxHeight: host.bodyMax } }

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

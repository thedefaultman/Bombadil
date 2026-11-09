import QtQuick
import Bombadil as Kit

// A button on a card, in three tones. Quiet is a plain edge. Primary has an orange edge: the one thing to do here.
// Send is the person's press (Send, Create, Comment): its orange ring is drawn only while it can be pressed, and
// when it cannot it is dimmed and says nothing about why (the card says what there is to say, in its own line).
// A button that is not live takes neither a click nor a key.
//
// It is reached with Tab, and Return, Enter and Space on it press it; nothing else on a card does.
FocusScope {
    id: button
    property string label: ""
    property string tone: "quiet"        // quiet, primary or send
    property bool live: true
    property bool compact: false         // the buttons in a list row
    readonly property bool ringed: tone === "send" && live
    readonly property alias hovered: hover.hovered

    signal activated()

    function activate() {
        if (live) activated()
    }

    implicitWidth: text.implicitWidth + (compact ? Kit.Theme.gap : Kit.Theme.pad) * 2
    implicitHeight: compact ? Kit.Theme.chipHeight : Kit.Theme.controlHeight
    activeFocusOnTab: live
    Accessible.role: Accessible.Button
    Accessible.name: label

    Rectangle {
        id: face
        objectName: "buttonFace"
        anchors.fill: parent
        radius: button.compact ? height / 2 : Kit.Theme.radiusSmall
        color: tap.pressed && button.live ? Kit.Theme.panel
             : hover.hovered && button.live ? Kit.Theme.overlay : Kit.Theme.raised
        border.width: button.ringed ? 2 : 1
        border.color: button.ringed || (button.tone === "primary" && button.live) ? Kit.Theme.accent : Kit.Theme.border
        opacity: button.live ? 1 : 0.5
        Behavior on border.color { ColorAnimation { duration: Kit.Theme.fast } }
    }
    // Where the keyboard is: a second, plain line outside the button.
    Rectangle {
        anchors.fill: parent
        anchors.margins: -Kit.Theme.focusRing - 1
        radius: face.radius + Kit.Theme.focusRing + 1
        color: "transparent"
        border.width: button.activeFocus ? Kit.Theme.focusRing : 0
        border.color: Kit.Theme.borderActive
    }
    Text {
        id: text
        objectName: "buttonLabel"
        anchors.centerIn: parent
        text: button.label
        color: button.live ? Kit.Theme.fg : Kit.Theme.muted
        opacity: button.live ? 1 : 0.6
        font.family: Kit.Theme.fontFamily
        font.pixelSize: button.compact ? Kit.Theme.smallSize : Kit.Theme.textSize
        font.weight: button.tone === "quiet" ? Font.Normal : Font.DemiBold
        textFormat: Text.PlainText
    }

    HoverHandler { id: hover; enabled: button.live; cursorShape: Qt.PointingHandCursor }
    TapHandler {
        id: tap
        enabled: button.live
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: button.activate()
    }
    Keys.onReturnPressed: button.activate()
    Keys.onEnterPressed: button.activate()
    Keys.onSpacePressed: button.activate()
}

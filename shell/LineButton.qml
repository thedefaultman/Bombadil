import QtQuick

// A small text button for the line above the pill (Undo, Details) and for the Noticed card:
// `primary` is the one thing a row asks (the colours of the Stop dot), `quiet` is bare text.
Rectangle {
    id: button
    property string label: ""
    property bool primary: false
    property bool quiet: false
    signal clicked()

    implicitWidth: text.implicitWidth + (quiet ? 12 : 20)
    implicitHeight: quiet ? 22 : 24
    radius: 12
    color: quiet ? "transparent"
         : primary ? (tap.pressed ? "#4a342d" : hover.hovered ? "#432f29" : "#3a2a26")
         : tap.pressed ? "#353b43" : hover.hovered ? "#2a2f36" : "#22262b"
    border.width: quiet ? 0 : 1
    border.color: primary ? "#6b4538" : "#353b43"
    opacity: enabled ? 1 : 0.5

    Text {
        id: text
        anchors.centerIn: parent
        text: button.label
        color: button.primary ? "#f2c4b3" : button.quiet ? (hover.hovered ? "#e6e8eb" : "#8b939c") : "#e6e8eb"
        font.pixelSize: 12
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: button.clicked() }
}

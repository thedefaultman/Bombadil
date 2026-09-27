import QtQuick

// A small text button for the line above the pill (Undo, Details).
Rectangle {
    id: button
    property string label: ""
    signal clicked()

    implicitWidth: text.implicitWidth + 20
    implicitHeight: 24
    radius: 12
    color: tap.pressed ? "#353b43" : hover.hovered ? "#2a2f36" : "#22262b"
    border.width: 1
    border.color: "#353b43"

    Text {
        id: text
        anchors.centerIn: parent
        text: button.label
        color: "#e6e8eb"
        font.pixelSize: 12
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: button.clicked() }
}

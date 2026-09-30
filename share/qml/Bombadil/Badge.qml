import QtQuick

// A small status pill: `Badge { text: "weak"; tone: "bad" }`.
Rectangle {
    id: root

    property string text
    property string tone: ""
    property string icon: ""

    readonly property color ink: tone ? Theme.tone(tone) : Theme.muted

    implicitWidth: row.implicitWidth + 16
    implicitHeight: 20
    radius: height / 2
    color: tone ? Theme.alpha(ink, 0.16) : Theme.raised
    border.width: tone ? 0 : 1
    border.color: Theme.border

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 4

        Icon {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.icon !== ""
            name: root.icon
            size: 12
            color: root.ink
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            color: root.ink
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
            font.weight: Font.Medium
        }
    }
}

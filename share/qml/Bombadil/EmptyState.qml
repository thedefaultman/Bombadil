import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// What a list or screen shows when there is nothing in it (or it is locked).
Item {
    id: root

    property string icon: ""
    property string title: ""
    property string text: ""
    property string actionText: ""

    signal action()

    implicitWidth: Math.max(240, column.implicitWidth)
    implicitHeight: column.implicitHeight + 2 * Theme.pad

    ColumnLayout {
        id: column
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * Theme.pad, 340)
        spacing: Theme.gapSmall

        Rectangle {
            visible: root.icon !== ""
            Layout.alignment: Qt.AlignHCenter
            Layout.bottomMargin: Theme.gapSmall
            implicitWidth: 48
            implicitHeight: 48
            radius: width / 2
            color: Theme.raised
            border.color: Theme.border
            Icon {
                anchors.centerIn: parent
                name: root.icon
                size: 22
                color: Theme.muted
            }
        }
        Text {
            visible: text !== ""
            Layout.fillWidth: true
            text: root.title
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize + 1
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }
        Text {
            visible: text !== ""
            Layout.fillWidth: true
            text: root.text
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: root.title ? Theme.captionSize + 1 : Theme.textSize
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }
        Button {
            visible: root.actionText !== ""
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: Theme.gapSmall
            text: root.actionText
            highlighted: true
            onClicked: root.action()
        }
    }
}

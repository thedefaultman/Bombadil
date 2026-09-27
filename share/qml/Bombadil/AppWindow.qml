import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The standard frame for a generated app: dark, rounded, with a title row.
ApplicationWindow {
    id: win
    visible: true
    width: 520
    height: 640
    color: Theme.bg
    default property alias content: body.data
    property string subtitle: ""

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.pad
        RowLayout {
            Layout.fillWidth: true
            Label { text: win.title; color: Theme.fg; font.pixelSize: 22; font.bold: true }
            Label { text: win.subtitle; color: Theme.muted; Layout.fillWidth: true }
        }
        Item {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
        }
    }
}

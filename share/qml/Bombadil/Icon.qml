import QtQuick
import QtQuick.Controls.impl

// A Lucide icon from icons/, tinted to `color`.
Item {
    id: root

    property string name
    property int size: 18
    property color color: Theme.fg

    implicitWidth: size
    implicitHeight: size

    IconImage {
        anchors.centerIn: parent
        width: root.size
        height: root.size
        source: root.name ? Theme.icon(root.name) : ""
        sourceSize: Qt.size(root.size, root.size)
        color: root.color
    }
}

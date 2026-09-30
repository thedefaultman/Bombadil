import QtQuick
import QtQuick.Layouts

// A hairline between groups.
Rectangle {
    property bool vertical: false

    color: Theme.border
    implicitWidth: vertical ? 1 : 0
    implicitHeight: vertical ? 0 : 1
    Layout.fillWidth: !vertical
    Layout.fillHeight: vertical
}

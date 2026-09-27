import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A vertical scroll area; children stack in a column as wide as the pane.
Flickable {
    id: root

    property int spacing: Theme.gap
    property int padding: 0
    default property alias content: column.data

    implicitWidth: column.implicitWidth + 2 * padding
    implicitHeight: column.implicitHeight + 2 * padding
    contentWidth: width
    contentHeight: column.implicitHeight + 2 * padding
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.VerticalFlick
    acceptedButtons: Qt.NoButton

    ScrollBar.vertical: ScrollBar { policy: root.contentHeight > root.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }

    ColumnLayout {
        id: column
        x: root.padding
        y: root.padding
        width: root.width - 2 * root.padding
        spacing: root.spacing
    }
}

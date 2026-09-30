import QtQuick
import QtQuick.Layouts

// One 44 px row: icon, title with an optional subtitle, and trailing text or an item.
// ItemList uses it; use it in your own ListView delegates too.
Item {
    id: root

    property string title
    property string subtitle
    property string icon: ""
    property string trailing
    property bool selected: false
    property Item trailingItem: null
    readonly property alias hovered: hover.hovered

    signal clicked()
    signal doubleClicked()
    signal contextRequested()

    implicitWidth: 240
    implicitHeight: Theme.rowHeight

    onTrailingItemChanged: _placeTrailing()
    Component.onCompleted: _placeTrailing()
    function _placeTrailing() {
        if (trailingItem)
            trailingItem.parent = slot
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: root.selected ? Theme.accentSoft : root.hovered ? Theme.raised : Theme.alpha(Theme.raised, 0)
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }

    Rectangle {
        visible: root.selected
        x: 0
        width: 2
        height: parent.height - 20
        anchors.verticalCenter: parent.verticalCenter
        radius: 1
        color: Theme.accent
    }

    HoverHandler { id: hover }
    TapHandler {
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onTapped: (point, button) => {
            if (button === Qt.RightButton)
                root.contextRequested()
            else if (tapCount === 2)
                root.doubleClicked()
            else
                root.clicked()
        }
    }

    RowLayout {
        anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
        spacing: Theme.gap

        Icon {
            visible: root.icon !== ""
            name: root.icon
            size: 18
            color: root.selected ? Theme.accent : Theme.muted
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 1
            Text {
                Layout.fillWidth: true
                text: root.title
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.textSize
                font.weight: root.subtitle ? Font.Medium : Font.Normal
                elide: Text.ElideRight
            }
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                text: root.subtitle
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
                elide: Text.ElideRight
            }
        }
        Text {
            visible: text !== ""
            text: root.trailing
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
            font.features: { "tnum": 1 }
        }
        RowLayout {
            id: slot
            visible: root.trailingItem !== null
            spacing: 0
        }
    }
}

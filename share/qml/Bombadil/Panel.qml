import QtQuick
import QtQuick.Layouts

// A card: a rounded panel surface with an optional header. Children stack in a column;
// one with Layout.fillHeight takes the free height, otherwise they stay at the top.
Item {
    id: root

    property string title
    property string subtitle
    property alias actions: actionRow.data
    property int padding: Theme.pad
    property int spacing: Theme.gap
    property bool flat: false
    default property alias content: body.data

    implicitWidth: Math.max(header.visible ? header.implicitWidth + 2 * _headerPad : 0, body.implicitWidth + 2 * padding)
    implicitHeight: (header.visible ? _headerPad + header.implicitHeight + spacing : padding) + body.implicitHeight + padding
    // A layout may squeeze a panel only when its content can shrink (a list, a table).
    Layout.minimumHeight: body.fills ? implicitHeight - body.implicitHeight : implicitHeight
    // Cards side by side in a row or grid get the tallest one's height; one with
    // Layout.fillHeight takes the row's full height.
    Layout.alignment: Qt.AlignTop
    Layout.preferredHeight: {
        if (!_inRow)
            return -1
        let tallest = implicitHeight
        for (const c of parent.children)
            if (c.visible)
                tallest = Math.max(tallest, c.implicitHeight)
        return tallest
    }

    readonly property bool _inRow: parent instanceof RowLayout || parent instanceof GridLayout
    readonly property int _headerPad: Math.max(padding, Theme.pad)

    // A child takes the free height when it has Layout.fillHeight (and no maximum). Nested
    // layouts default to fillHeight, so they count only when something inside them does.
    function _grows(item) {
        if (!item.visible || !item.Layout.fillHeight || item.Layout.maximumHeight < 1e30)   // "no maximum" reads as FLT_MAX or Infinity
            return false
        if (!(item instanceof ColumnLayout || item instanceof RowLayout || item instanceof GridLayout))
            return true
        for (const c of item.children)
            if (_grows(c))
                return true
        return false
    }

    Rectangle {
        anchors.fill: parent
        visible: !root.flat
        color: Theme.panel
        radius: Theme.radius
        border.color: Theme.border
    }

    RowLayout {
        id: header
        visible: root.title !== "" || actionRow.children.length > 0
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: root._headerPad }
        spacing: Theme.gap

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                text: root.title
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.textSize
                font.weight: Font.DemiBold
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
        RowLayout {
            id: actionRow
            spacing: Theme.gapSmall
        }
    }

    Item {
        id: area
        anchors {
            left: parent.left; right: parent.right; bottom: parent.bottom
            top: header.visible ? header.bottom : parent.top
            leftMargin: root.padding; rightMargin: root.padding; bottomMargin: root.padding
            topMargin: header.visible ? root.spacing : root.padding
        }

        ColumnLayout {
            id: body
            // Packed at the top unless a child asks for the free height.
            readonly property bool fills: {
                for (const c of children)
                    if (root._grows(c))
                        return true
                return false
            }
            width: parent.width
            height: fills ? parent.height : implicitHeight
            spacing: root.spacing
        }
    }
}

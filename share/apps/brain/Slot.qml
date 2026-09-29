import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// A fixed place for one kind of link: "Came from" on the left, "Read with" on the right,
// "Used with" below. At most five lines, then "+12 more".
Panel {
    id: slot
    property string label: ""
    property var links: null           // {items: [LINE], more: n}
    property string emptyText: ""
    property int shown: 5
    property int columns: 1
    signal picked(string ref)
    signal moreRequested()

    readonly property var items: (links && links.items) ? links.items : []
    readonly property int more: (links && links.more) ? links.more : 0

    padding: 12
    spacing: 4

    Text {
        text: slot.label.toUpperCase()
        color: Theme.faint
        font.pixelSize: 11
        font.family: Theme.fontFamily
        font.weight: Font.DemiBold
        font.letterSpacing: 0.8
        Layout.leftMargin: 8
        Layout.bottomMargin: 2
    }

    GridLayout {
        Layout.fillWidth: true
        Layout.fillHeight: false   // as in Trail.qml: keeps the kit from sizing it mid-rebuild
        columns: slot.columns
        columnSpacing: 4
        rowSpacing: 0
        uniformCellWidths: true
        Repeater {
            model: slot.items.slice(0, slot.shown)
            Line {
                line: modelData
                onPicked: ref => slot.picked(ref)
            }
        }
    }

    Text {
        visible: slot.items.length === 0
        text: slot.emptyText
        color: Theme.faint
        font.pixelSize: Theme.captionSize
        font.family: Theme.fontFamily
        wrapMode: Text.Wrap
        Layout.fillWidth: true
        Layout.leftMargin: 8
    }

    Button {
        visible: slot.more > 0 || slot.items.length > slot.shown
        flat: true
        text: "+" + (slot.more + Math.max(0, slot.items.length - slot.shown)) + " more"
        Layout.leftMargin: 2
        implicitHeight: 28
        onClicked: slot.moreRequested()
    }
}

import QtQuick
import QtQuick.Layouts

// A headline number with its label, an optional delta and a trend line.
ColumnLayout {
    id: root

    property string label
    property string value
    property string detail
    property string delta
    property string deltaTone: ""
    property var trend: []

    spacing: 4
    // Stats side by side line up by their labels, whatever their height.
    Layout.alignment: Qt.AlignTop

    Text {
        Layout.fillWidth: true
        text: root.label
        color: Theme.muted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionSize
        font.weight: Font.Medium
        elide: Text.ElideRight
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: root.value
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.titleSize
            font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
        }
        Text {
            Layout.alignment: Qt.AlignBaseline
            visible: text !== ""
            text: root.delta
            color: root.deltaTone ? Theme.tone(root.deltaTone) : Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
            font.weight: Font.Medium
        }
        Item { Layout.fillWidth: true }
    }
    Text {
        Layout.fillWidth: true
        visible: text !== ""
        text: root.detail
        color: Theme.muted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionSize
        elide: Text.ElideRight
    }
    Sparkline {
        visible: root.trend && root.trend.length > 1
        Layout.fillWidth: true
        Layout.topMargin: 4
        Layout.preferredHeight: 28
        values: root.trend || []
        color: root.deltaTone ? Theme.tone(root.deltaTone) : Theme.series[0]
    }
}

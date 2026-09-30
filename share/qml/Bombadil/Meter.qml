import QtQuick

// A 0..1 fraction as a bar: label and detail above, percentage at the right. The fill
// turns warn above `warnAt` and bad above `badAt` unless `tone` is set; the track is a
// dim step of the fill so the state reads across the whole bar.
Item {
    id: root

    property real value: 0
    property string label: ""
    property string detail: ""
    property string tone: ""
    property real warnAt: 0.75
    property real badAt: 0.9

    readonly property real _v: Math.max(0, Math.min(1, isNaN(value) ? 0 : value))
    readonly property color _fill: tone !== "" ? Theme.tone(tone)
                                              : _v > badAt ? Theme.bad : _v > warnAt ? Theme.warn : Theme.accent

    implicitWidth: 240
    implicitHeight: head.height + 8 + track.height

    Item {
        id: head
        width: parent.width
        height: nameText.height

        Text {
            id: nameText
            width: Math.min(implicitWidth, parent.width - pct.width - 12)
            elide: Text.ElideRight
            text: root.label
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
        }
        Text {
            x: nameText.width + (nameText.width > 0 ? 8 : 0)
            anchors.baseline: nameText.baseline
            width: Math.max(0, pct.x - x - 12)
            elide: Text.ElideRight
            text: root.detail
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
        }
        Text {
            id: pct
            anchors.right: parent.right
            anchors.baseline: nameText.baseline
            text: Fmt.percent(root._v)
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
            font.weight: Font.Medium
            font.features: { "tnum": 1 }
        }
    }

    Rectangle {
        id: track
        y: head.height + 8
        width: parent.width
        height: 8
        radius: 4
        color: Theme.alpha(root._fill, 0.2)

        Rectangle {
            height: parent.height
            width: parent.width * root._v
            radius: 4
            color: root._fill
            Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
        }
    }
}

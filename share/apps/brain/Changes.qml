import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "who.js" as Who

// On top: how often the thing changed this week, as a small line, and who changed it last.
Panel {
    id: strip
    property var changes: null        // {count_week, days: [7], items: [{t, when, why, kind, actor}], more}
    readonly property var items: (changes && changes.items) ? changes.items : []
    readonly property int week: changes ? (changes.count_week || 0) : 0

    padding: 12

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: false   // the strip is as tall as it is; see Trail.qml
        spacing: Theme.pad

        ColumnLayout {
            spacing: 0
            Text {
                text: "CHANGES"
                color: Theme.faint
                font.pixelSize: 11
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                font.letterSpacing: 0.8
            }
            Text {
                text: strip.week === 0 ? "None this week" : (strip.week === 1 ? "1 this week" : strip.week + " this week")
                textFormat: Text.PlainText
                color: Theme.fg
                font.pixelSize: Theme.headingSize
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
            }
        }

        Sparkline {
            Layout.preferredWidth: 140
            Layout.preferredHeight: 32
            values: (strip.changes && strip.changes.days) ? strip.changes.days : [0, 0, 0, 0, 0, 0, 0]
            min: 0
            color: Theme.accent
            format: v => v === 1 ? "1 change" : v + " changes"
        }

        Divider { vertical: true; Layout.preferredHeight: 32 }

        Flow {
            Layout.fillWidth: true
            spacing: Theme.gap
            clip: true
            Layout.preferredHeight: 36
            Repeater {
                model: strip.items.slice(0, 3)
                Row {
                    spacing: 6
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Who.dot(modelData.actor, Theme)
                    }
                    Text {
                        text: (modelData.why || modelData.kind || "") + (modelData.when ? ", " + modelData.when : "")
                        textFormat: Text.PlainText
                        color: index === 0 ? Theme.fg : Theme.muted
                        font.pixelSize: Theme.captionSize
                        font.family: Theme.fontFamily
                        elide: Text.ElideRight
                        width: Math.min(implicitWidth, 300)
                    }
                }
            }
            Text {
                visible: strip.items.length === 0 && strip.week === 0
                text: "Nothing has changed it since the brain started watching."
                color: Theme.faint
                font.pixelSize: Theme.captionSize
                font.family: Theme.fontFamily
            }
        }
    }
}

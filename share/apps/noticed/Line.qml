import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// One row of a list: what it is, in their words, a muted line under it, and what they can do with it at the
// right. Whatever is put inside goes under the text (an answer, the reasons, the other ways). A row
// wraps rather than elides, since the words are the point. Their words are plain text, never markup.
Item {
    id: root

    property string title
    property string subtitle
    property string note
    property string badge
    property string badgeTone: ""
    property bool divider: false
    property alias actions: actionRow.data
    default property alias extra: extraColumn.data

    Layout.fillWidth: true
    implicitWidth: 320
    implicitHeight: column.implicitHeight + 2 * Theme.gapSmall + (divider ? 1 : 0)

    Rectangle {
        visible: root.divider
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 1
        color: Theme.border
    }

    ColumnLayout {
        id: column
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; bottomMargin: Theme.gapSmall }
        spacing: Theme.gapSmall

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 2

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.gapSmall
                    Body {
                        objectName: "title"
                        text: root.title
                        textFormat: Text.PlainText
                        font.weight: Font.Medium
                    }
                    Badge {
                        objectName: "badge"
                        visible: root.badge !== ""
                        Layout.alignment: Qt.AlignTop
                        text: root.badge
                        tone: root.badgeTone
                    }
                }
                Caption {
                    objectName: "subtitle"
                    visible: text !== ""
                    text: root.subtitle
                    textFormat: Text.PlainText
                }
                Caption {
                    objectName: "note"
                    visible: text !== ""
                    text: root.note
                    textFormat: Text.PlainText
                }
            }
            RowLayout {
                id: actionRow
                Layout.alignment: Qt.AlignTop
                spacing: Theme.gapSmall
            }
        }
        ColumnLayout {
            id: extraColumn
            Layout.fillWidth: true
            spacing: Theme.gapSmall
        }
    }
}

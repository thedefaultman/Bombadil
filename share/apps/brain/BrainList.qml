import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "who.js" as Who

// A list of things (a folder's entries, search results), drawn here rather than with the
// kit's ItemList so that a file name or a page title is only ever text: the kit's rows take
// "<img src=…>" in a title for markup and would fetch it.
// Each item: {ref, title, subtitle, trailing, icon, actor}. The dot says who touched it last.
FocusScope {
    id: list
    property var model: []
    property alias currentIndex: view.currentIndex
    property string emptyText: "Nothing here yet"
    readonly property alias count: view.count
    signal activated(int index, var item)

    implicitWidth: 240
    implicitHeight: view.count > 0 ? Math.min(view.contentHeight, 8 * (Theme.rowHeight + view.spacing)) : 120

    // Make one row the current one and bring it into view; the list may not be laid out yet.
    function select(i) {
        view.currentIndex = i
        Qt.callLater(() => view.positionViewAtIndex(i, ListView.Center))
    }
    function activate() {
        if (view.currentIndex >= 0 && view.currentIndex < list.model.length)
            list.activated(view.currentIndex, list.model[view.currentIndex])
    }
    Keys.onReturnPressed: activate()
    Keys.onEnterPressed: activate()

    ListView {
        id: view
        anchors.fill: parent
        focus: true
        clip: true
        spacing: 2
        currentIndex: -1
        boundsBehavior: Flickable.StopAtBounds
        keyNavigationEnabled: true
        highlightFollowsCurrentItem: false
        model: list.model
        ScrollBar.vertical: ScrollBar {}
        onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)

        delegate: Item {
            id: row
            required property int index
            required property var modelData
            width: ListView.view.width
            height: Theme.rowHeight

            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: row.ListView.isCurrentItem ? Theme.accentSoft : (hover.hovered ? Theme.raised : "transparent")
            }
            Rectangle {
                visible: row.ListView.isCurrentItem
                width: 2; height: parent.height - 20
                anchors.verticalCenter: parent.verticalCenter
                radius: 1
                color: Theme.accent
            }
            HoverHandler { id: hover }
            TapHandler {
                onTapped: { view.currentIndex = row.index; list.activated(row.index, row.modelData) }
            }

            RowLayout {
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                spacing: Theme.gap
                Icon {
                    visible: !!row.modelData.icon
                    name: row.modelData.icon || "file"
                    size: 18
                    color: row.ListView.isCurrentItem ? Theme.accent : Theme.muted
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 1
                    Text {
                        Layout.fillWidth: true
                        text: row.modelData.title || ""
                        textFormat: Text.PlainText
                        color: Theme.fg
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.textSize
                        font.weight: row.modelData.subtitle ? Font.Medium : Font.Normal
                        elide: Text.ElideMiddle
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: row.modelData.subtitle || ""
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionSize
                        elide: Text.ElideRight
                    }
                }
                Rectangle {
                    visible: !!row.modelData.actor
                    width: 8; height: 8; radius: 4
                    Layout.alignment: Qt.AlignVCenter
                    color: Who.dot(row.modelData.actor, Theme)
                }
                Text {
                    visible: text !== ""
                    text: row.modelData.trailing || ""
                    textFormat: Text.PlainText
                    color: Theme.faint
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                }
            }
        }
    }

    Text {
        anchors.centerIn: parent
        visible: view.count === 0
        text: list.emptyText
        textFormat: Text.PlainText
        color: Theme.faint
        font.family: Theme.fontFamily
        font.pixelSize: Theme.textSize
    }
}

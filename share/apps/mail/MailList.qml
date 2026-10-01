import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "words.js" as Words

// The middle column: a search, the mail in this view newest first, and under it the accounts that were not read.
FocusScope {
    id: root

    readonly property alias view: list
    readonly property alias searchField: search
    readonly property bool why: backend.view === "needs_reply" || backend.view === "drafts"

    signal opened(string id)

    function focusSearch() {
        search.forceActiveFocus()
        search.field.selectAll()
    }
    // A letter typed with the keyboard on the list (and no field) is the start of a search, as it is in a file manager.
    function typeToSearch(ch) {
        search.forceActiveFocus()
        search.field.cursorPosition = search.field.length
        search.field.insert(search.field.length, ch)
        backend.search(search.text)
    }

    // The list is an array that is handed over whole whenever the service says something changed; the position in it
    // and the row the keyboard is on stay where they were, by the mail's id.
    property string cursorId: ""
    property string syncedView: ""
    function sync() {
        const y = list.contentY
        const was = list.currentIndex
        const sameView = root.syncedView === backend.view
        root.syncedView = backend.view
        const id = sameView && was >= 0 && was < list.count ? root.cursorId : ""
        list.model = backend.messages
        let at = -1
        for (let i = 0; i < backend.messages.length; i++)
            if (backend.messages[i].id === id) {
                at = i
                break
            }
        if (at < 0)
            at = indexOfOpen()
        // a mail put away leaves the keyboard where it was: on whatever moved up into its place
        if (at < 0 && id !== "" && backend.messages.length > 0)
            at = Math.min(was, backend.messages.length - 1)
        list.currentIndex = at
        list.contentY = Math.max(0, Math.min(y, list.contentHeight - list.height))
    }
    function indexOfOpen() {
        for (let i = 0; i < backend.messages.length; i++)
            if (backend.messages[i].id === backend.selectedId)
                return i
        return -1
    }
    Component.onCompleted: sync()
    Connections {
        target: backend
        function onListChanged() { root.sync() }
        function onSelectionChanged() {
            const i = root.indexOfOpen()
            if (i >= 0 && list.currentIndex !== i) {
                list.currentIndex = i
                root.cursorId = backend.messages[i].id
            }
        }
        function onViewChanged() {
            if (search.text !== backend.searchText)
                search.text = backend.searchText
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.panel
        border.color: Theme.border
    }

    ColumnLayout {
        anchors { fill: parent; margins: 10 }
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 4
            spacing: 6
            PlainText {
                Layout.fillWidth: true
                text: backend.viewName
                elide: Text.ElideRight
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
            }
            IconButton {
                icon: "refresh"
                size: 28
                tooltip: "Check for mail"
                onClicked: backend.refresh()
            }
        }

        QuietInput {
            id: search
            objectName: "searchField"
            search: true
            placeholder: "Search"
            label: "Search mail"
            onEdited: backend.search(text)
            onDownPressed: list.forceActiveFocus()
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: list
                objectName: "mailList"
                anchors.fill: parent
                focus: true
                clip: true
                spacing: 2
                currentIndex: -1
                boundsBehavior: Flickable.StopAtBounds
                keyNavigationEnabled: true
                highlightFollowsCurrentItem: false
                activeFocusOnTab: true
                ScrollBar.vertical: ScrollBar {}

                delegate: MailRow {
                    required property var modelData
                    required property int index
                    info: modelData
                    open: info.id === backend.selectedId
                    current: ListView.isCurrentItem && list.activeFocus
                    showWhy: root.why
                    onClicked: {
                        list.currentIndex = index
                        root.cursorId = info.id
                        list.forceActiveFocus()
                        root.opened(info.id)
                    }
                }

                onCurrentIndexChanged: {
                    if (currentIndex >= 0 && currentIndex < backend.messages.length)
                        root.cursorId = backend.messages[currentIndex].id
                    if (currentIndex >= 0)
                        positionViewAtIndex(currentIndex, ListView.Contain)
                }
                Keys.onReturnPressed: openCursor()
                Keys.onEnterPressed: openCursor()
                function openCursor() {
                    if (currentIndex >= 0 && currentIndex < backend.messages.length)
                        root.opened(backend.messages[currentIndex].id)
                }
            }

            ColumnLayout {
                visible: list.count === 0
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
                spacing: 4
                PlainText {
                    objectName: "listState"
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: Theme.muted
                    text: backend.listState === "loading" ? "Loading"
                        : backend.listState === "error" ? backend.listError
                        : Words.emptyLine(backend.view, backend.searchText !== "", backend.listWaiting)
                }
            }
        }

        QuietButton {
            visible: backend.listMore
            Layout.alignment: Qt.AlignHCenter
            text: "Show more"
            compact: true
            onClicked: backend.loadMore()
        }

        // Accounts the service could not read for this list that the left column does not already explain.
        ColumnLayout {
            objectName: "skipped"
            visible: backend.skippedHere.length > 0
            Layout.fillWidth: true
            spacing: 4
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
            Repeater {
                model: backend.skippedHere
                delegate: ColumnLayout {
                    id: skip
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 2
                    PlainText {
                        Layout.fillWidth: true
                        text: skip.modelData.email + (skip.modelData.note ? ": " + skip.modelData.note : "")
                        wrapMode: Text.Wrap
                        font.pixelSize: Theme.captionSize
                        color: Theme.muted
                    }
                    LinkButton {
                        visible: !!skip.modelData.web
                        text: "Open"
                        onClicked: backend.openWeb(skip.modelData.web.url)
                    }
                }
            }
        }
    }
}

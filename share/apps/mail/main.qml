import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Mail: every account in one list. Left, the views and the accounts; in the middle, the mail in the view; on the
// right, the mail that is open and, under it, the reply box. Below 900 px the left column becomes a row of tabs
// over the list, and below 640 px one pane shows at a time.
//
// The orange in this window is the ring on Send (SendBar.qml) and nothing else: the person's own press is the one
// thing that lets a mail go, and it is the one thing drawn in the machine's colour. Every pane draws what came in a
// mail as plain text (PlainText, MailText).
AppWindow {
    id: win
    title: "Mail"
    subtitle: backend.unreadText
    width: 1180
    height: 760

    readonly property bool narrow: width < 900
    readonly property bool single: width < 640
    readonly property bool reading: !!backend.opened || !!backend.draft || !!backend.receipt
    readonly property bool setupShown: backend.ready && (backend.noAccount || setupOpen)
    readonly property bool mainShown: backend.ready && !setupShown
    // Esc leaves the reply box first; with none open (and nothing else to leave) it closes the window as in every app.
    readonly property bool escapeMine: !!backend.draft || (setupOpen && !backend.noAccount)
                                       || (single && !!backend.opened)
    property bool setupOpen: false

    actions: [
        QuietButton {
            objectName: "newMail"
            visible: win.mainShown
            text: "New mail"
            icon: "pencil"
            onClicked: backend.newMail()
        }
    ]

    function openFromList(id) {
        if (backend.view === "drafts")
            backend.openDraft(id)
        else
            backend.openMail(id)
    }
    function leave() {
        if (backend.draft)
            backend.closeDraft()
        else if (win.setupOpen && !backend.noAccount)
            win.setupOpen = false
        else
            backend.closeMail()
        list.view.forceActiveFocus()
    }

    Connections {
        target: backend
        function onShowRequested() { App.show() }
    }

    Keys.onShortcutOverride: event => {
        if (event.key === Qt.Key_Escape && win.escapeMine)
            event.accepted = true
    }
    Keys.onEscapePressed: event => {
        if (win.escapeMine)
            win.leave()
        else
            event.accepted = false
    }
    // R replies, A archives and # deletes the mail that is open. Typing in a field never gets here: the field has it.
    Keys.onPressed: event => {
        if ((event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) || !win.mainShown
                || !backend.opened || backend.draft)
            return
        if (event.key === Qt.Key_R) {
            backend.reply("reply")
            event.accepted = true
        } else if (event.key === Qt.Key_A) {
            backend.archive()
            event.accepted = true
        } else if (event.key === Qt.Key_NumberSign || event.text === "#") {
            backend.trash()
            event.accepted = true
        }
    }

    Shortcut {
        sequence: "Ctrl+F"
        context: Qt.WindowShortcut
        onActivated: list.focusSearch()
    }
    Shortcut {
        sequence: "Ctrl+R"
        context: Qt.WindowShortcut
        onActivated: backend.refresh()
    }

    // The engine, or the service, is not there: said once, quietly. What was loaded stays readable.
    RowLayout {
        objectName: "engineNote"
        visible: backend.ready && backend.engineNote !== ""
        Layout.fillWidth: true
        spacing: 8
        Icon { name: "info"; size: 15; color: Theme.muted }
        PlainText {
            Layout.fillWidth: true
            text: backend.engineNote
            elide: Text.ElideRight
            font.pixelSize: Theme.smallSize
            color: Theme.muted
        }
    }

    PlainText {
        objectName: "notReady"
        visible: !backend.ready
        Layout.fillWidth: true
        Layout.fillHeight: true
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
        color: Theme.muted
        text: backend.connected ? "Loading" : "Mail is not running yet. This fills in as soon as it starts."
    }

    SetupPanel {
        visible: win.setupShown
        inPlace: !backend.noAccount
        Layout.fillWidth: true
        Layout.fillHeight: true
        onClosed: win.setupOpen = false
    }

    // Below 900 px the left column is a few lines over the list: the views and the accounts as tabs that wrap onto a
    // second line rather than run out of sight, then the note of any account that cannot be read just now.
    ColumnLayout {
        id: tabs
        objectName: "viewTabs"
        visible: win.narrow && win.mainShown
        Layout.fillWidth: true
        spacing: 4

        Flow {
            id: tabRow
            Layout.fillWidth: true
            Layout.preferredHeight: childrenRect.height
            spacing: 6
            Repeater {
                model: backend.sidebar
                delegate: QuietButton {
                    required property var modelData
                    text: modelData.name + (modelData.count > 0 ? "  " + modelData.count : "")
                    checked: backend.view === modelData.id
                    onClicked: backend.setView(modelData.id)
                }
            }
            QuietButton {
                objectName: "addAccountTab"
                text: "Add an account"
                icon: "plus"
                flat: true
                onClicked: win.setupOpen = true
            }
        }
        Repeater {
            model: backend.sidebar
            delegate: RowLayout {
                required property var modelData
                visible: modelData.note !== ""
                Layout.fillWidth: true
                spacing: 8
                PlainText {
                    Layout.fillWidth: true
                    text: modelData.name + ": " + modelData.note
                    wrapMode: Text.Wrap
                    font.pixelSize: Theme.captionSize
                    color: Theme.muted
                }
                LinkButton {
                    visible: !!modelData.web
                    text: "Open"
                    onClicked: backend.openWeb(modelData.web.url)
                }
            }
        }
    }

    RowLayout {
        id: panes
        visible: win.mainShown
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Theme.gap

        Sidebar {
            visible: !win.narrow
            Layout.preferredWidth: Math.max(206, Math.min(244, win.width * 0.19))
            Layout.fillHeight: true
            onAddAccount: win.setupOpen = true
        }
        MailList {
            id: list
            focus: true
            visible: !win.single || !win.reading
            Layout.preferredWidth: win.single ? panes.width : Math.max(236, Math.min(340, win.width * 0.26))
            Layout.fillWidth: win.single
            Layout.fillHeight: true
            onOpened: id => win.openFromList(id)
        }
        Reader {
            visible: !win.single || win.reading
            back: win.single
            Layout.fillWidth: true
            Layout.minimumWidth: 300
            Layout.fillHeight: true
            onBackRequested: win.leave()
        }
    }

    // What the last thing did, in one quiet line (a file saved, a mail that would not move).
    PlainText {
        objectName: "quiet"
        Layout.fillWidth: true
        Layout.preferredHeight: 18
        text: backend.quiet
        elide: Text.ElideRight
        font.pixelSize: Theme.smallSize
        color: backend.quietTone === "bad" ? Theme.badInk : Theme.muted
    }
}

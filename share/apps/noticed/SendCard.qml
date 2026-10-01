import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// What sending a bug to the project would do, shown before anything happens: what goes, what stays on
// this machine, and the exact text. The page it opens is filled in, which sends the text to GitHub in
// the page's address, and he presses Submit there himself; nothing is sent from here.
ColumnLayout {
    id: root

    property var row: null            // the found row: {title, preview: {text, goes, stays}, ...}
    property bool busy: false          // a tap is waiting for its answer
    property bool waiting: false       // the report is being written again: what shows may be old

    readonly property var preview: row ? row.preview : ({})
    readonly property bool ready: !waiting && !!preview.text && preview.text !== ""

    signal back()
    signal openPage()
    signal notNow()
    signal never()

    spacing: Theme.gap

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gapSmall
        IconButton {
            objectName: "btn:back"
            icon: "arrow-left"
            tooltip: "Back"
            onClicked: root.back()
        }
        Heading {
            Layout.fillWidth: true
            level: 2
            text: "Send this to the project?"
        }
    }
    Caption {
        text: "This is everything that would be sent. Opening the page sends it to GitHub as part of the address; nothing is posted until you press Submit."
    }

    EmptyState {
        objectName: "sendWaiting"
        visible: !root.ready
        Layout.fillWidth: true
        Layout.fillHeight: true
        icon: "clock"
        title: root.waiting ? "Writing it up…" : "Nothing written up yet."
        text: root.waiting ? "" : "Bombadil has not finished this report. Go back and try again in a moment."
    }

    ScrollPane {
        objectName: "sendBody"
        visible: root.ready
        Layout.fillWidth: true
        Layout.fillHeight: true

        Body {
            objectName: "sendTitle"
            text: root.row ? root.row.title : ""
            textFormat: Text.PlainText
            font.weight: Font.Medium
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            uniformCellSizes: true

            Panel {
                objectName: "sendGoes"
                Layout.fillWidth: true
                title: "What goes"
                Repeater {
                    model: root.preview.goes || []
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Theme.gapSmall
                        Icon { Layout.alignment: Qt.AlignTop; name: "check"; size: 16; color: Theme.good }
                        Body { text: parent.modelData; textFormat: Text.PlainText }
                    }
                }
            }
            Panel {
                objectName: "sendStays"
                Layout.fillWidth: true
                title: "What stays here"
                Repeater {
                    model: root.preview.stays || []
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Theme.gapSmall
                        Icon { Layout.alignment: Qt.AlignTop; name: "lock"; size: 16; color: Theme.muted }
                        Body { text: parent.modelData; textFormat: Text.PlainText }
                    }
                }
            }
        }
        Panel {
            Layout.fillWidth: true
            title: "The exact text"
            subtitle: "It goes into the page as it is, and you can change it there"
            Mono {
                objectName: "sendText"
                text: root.preview.text || ""
            }
        }
    }

    Divider {}
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gapSmall
        Button {
            objectName: "btn:open"
            text: "Open the issue page"
            highlighted: true
            enabled: root.ready && !root.busy
            onClicked: root.openPage()
        }
        Button {
            objectName: "btn:notnow"
            text: "Not now"
            enabled: !root.busy
            onClicked: root.notNow()
        }
        Button {
            objectName: "btn:never"
            text: "Never for this"
            flat: true
            enabled: !root.busy
            onClicked: root.never()
        }
        Spacer {}
    }
}

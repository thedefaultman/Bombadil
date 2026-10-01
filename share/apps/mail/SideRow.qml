import QtQuick
import QtQuick.Layouts
import Bombadil
import "words.js" as Words

// One line of the left column: a view (All inboxes, Needs a reply, Drafts) or an account, with its count, and under
// an account that cannot be read just now a quiet note, and a link to the web when the provider has to be asked.
FocusScope {
    id: root

    required property var info          // {id, name, kind, count, note, state, web}
    property bool selected: false

    signal picked()
    signal webRequested(string url)

    readonly property bool account: info.kind === "account"
    // what the name has of the line: its width less the count and the gap before it
    readonly property real room: line.width - (countText.visible ? countText.implicitWidth + line.spacing : 0)

    // An address that does not fit loses the end of its name and keeps its domain, since two accounts often share a
    // name and differ in the domain ("maya.re…@gmail.example" and "maya.re…@lakeside.example").
    FontMetrics {
        id: metrics
        font.family: Theme.fontFamily
        font.pixelSize: Theme.smallSize
    }
    function fitted(email, room) {
        const at = email.lastIndexOf("@")
        if (room <= 0 || at < 1 || metrics.advanceWidth(email) <= room)
            return email
        const domain = email.slice(at)
        for (let n = at - 1; n >= 2; n--) {
            const tryText = email.slice(0, n) + "\u2026" + domain
            if (metrics.advanceWidth(tryText) <= room)
                return tryText
        }
        return email.slice(0, 2) + "\u2026" + domain
    }

    implicitHeight: col.implicitHeight + 4
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: info.name + (info.count > 0 ? ", " + info.count : "")

    Keys.onReturnPressed: root.picked()
    Keys.onEnterPressed: root.picked()
    Keys.onSpacePressed: root.picked()

    ColumnLayout {
        id: col
        anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 2 }
        spacing: 0

        Item {
            id: head
            Layout.fillWidth: true
            implicitHeight: 34

            HoverHandler { id: hover }
            TapHandler { onTapped: root.picked() }

            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: root.selected ? Theme.raised : hover.hovered ? Theme.alpha(Theme.raised, 0.6) : "transparent"
                border.width: root.activeFocus ? 1.5 : 0
                border.color: Theme.fg
            }
            Rectangle {
                visible: root.selected
                anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                width: 2
                height: 16
                radius: 1
                color: Theme.fg
            }
            RowLayout {
                id: line
                anchors { fill: parent; leftMargin: root.account ? 22 : 12; rightMargin: 10 }
                spacing: 8
                Icon {
                    visible: !root.account
                    name: Words.viewIcon(root.info)
                    size: 16
                    color: root.selected ? Theme.fg : Theme.muted
                }
                PlainText {
                    id: nameText
                    Layout.fillWidth: true
                    Layout.preferredWidth: 10       // what the text says never decides the width it is given
                    Layout.minimumWidth: 0
                    text: root.account ? root.fitted(root.info.name, root.room) : root.info.name
                    elide: Text.ElideRight
                    font.pixelSize: root.account ? Theme.smallSize : Theme.textSize
                    font.weight: root.selected ? Font.DemiBold : Font.Normal
                    color: !root.account || root.info.state === "ok" || root.info.state === "syncing" ? Theme.fg : Theme.muted
                }
                PlainText {
                    id: countText
                    visible: root.info.count > 0
                    text: Words.countText(root.info.count)
                    font.pixelSize: Theme.smallSize
                    font.weight: root.info.kind === "all" || root.account ? Font.DemiBold : Font.Normal
                    color: root.info.kind === "all" || root.account ? Theme.fg : Theme.muted
                }
            }
        }

        PlainText {
            visible: root.info.note !== ""
            Layout.fillWidth: true
            Layout.leftMargin: 22
            Layout.rightMargin: 10
            Layout.topMargin: 2
            text: root.info.note
            wrapMode: Text.Wrap
            font.pixelSize: Theme.captionSize
            color: Theme.muted
        }
        LinkButton {
            visible: !!root.info.web
            Layout.leftMargin: 22
            Layout.topMargin: 2
            text: "Open"
            onClicked: root.webRequested(root.info.web.url)
        }
        // What the note asks for (finishing a sign-in, a password for an app) is done in Thunderbird's own window.
        LinkButton {
            objectName: "sideEngine"
            visible: !!root.info.engine
            Layout.leftMargin: 22
            Layout.topMargin: 2
            text: backend.staged ? "Done" : "Show Thunderbird's window"
            icon: ""
            onClicked: backend.staged ? backend.hideEngine() : backend.showEngine()
        }
    }
}

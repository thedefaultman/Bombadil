import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The bugs Bombadil found in itself: a plain sentence, how often, Why? (what it saw), and Send to the
// project, which opens the Send card.
Section {
    id: root

    property var rows: []
    property var pending: []
    property var answers: ({})
    property string whyOpen: ""       // the row whose reasons are showing

    signal act(string op, string id, string form)

    objectName: "section:found"
    title: "Found"
    hint: "Bugs Bombadil found in itself"
    count: rows.length
    emptyText: "Bombadil has not found anything wrong."

    Repeater {
        model: root.rows

        Line {
            id: row
            required property var modelData
            required property int index
            readonly property bool busy: root.pending.indexOf(modelData.id) >= 0
            readonly property var answer: root.answers[modelData.id]
            readonly property bool why: root.whyOpen === modelData.id
            readonly property bool sent: modelData.state === "sent"

            objectName: "row:" + modelData.id
            divider: index > 0
            title: modelData.title
            subtitle: modelData.meta
            badge: sent ? "Sent" : modelData.state === "reported" ? "Ready to send" : ""
            badgeTone: sent ? "good" : "info"

            actions: [
                Button {
                    objectName: "btn:why:" + row.modelData.id
                    visible: row.modelData.why.length > 0
                    text: "Why?"
                    flat: true
                    checkable: true
                    checked: row.why
                    onClicked: root.whyOpen = row.why ? "" : row.modelData.id
                },
                Button {
                    objectName: "btn:send:" + row.modelData.id
                    visible: row.modelData.can_send && !row.sent
                    text: "Send to the project"
                    enabled: !row.busy
                    onClicked: root.act("report", row.modelData.id, "")
                }
            ]

            Well {
                objectName: "why:" + row.modelData.id
                visible: row.why && text !== ""
                text: row.modelData.why.join("\n")
            }
            Well {
                objectName: "answer:" + row.modelData.id
                text: row.answer ? row.answer.text : ""
                bad: row.answer ? !row.answer.ok : false
            }
        }
    }
}

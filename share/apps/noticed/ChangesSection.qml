import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// What Bombadil made or changed itself, newest first. Each has Undo; one that was undone says so and
// offers to put it back.
Section {
    id: root

    property var rows: []
    property var pending: []
    property var answers: ({})

    signal act(string op, string id, string form)

    objectName: "section:changes"
    title: "Changed itself"
    hint: "What Bombadil made or fixed, newest first"
    count: rows.length
    emptyText: "Bombadil has not changed anything by itself."

    Repeater {
        model: root.rows

        Line {
            id: row
            required property var modelData
            required property int index
            readonly property bool busy: root.pending.indexOf(modelData.id) >= 0
            readonly property var answer: root.answers[modelData.id]

            objectName: "row:" + modelData.id
            divider: index > 0
            title: modelData.title
            subtitle: modelData.t > 0 ? Fmt.relative(modelData.t) : ""
            badge: modelData.undone ? "Undone" : ""

            actions: [
                Button {
                    objectName: "btn:undo:" + row.modelData.id
                    visible: row.modelData.can_undo
                    text: "Undo"
                    enabled: !row.busy
                    onClicked: root.act("undo", row.modelData.id, "")
                },
                Button {
                    objectName: "btn:putback:" + row.modelData.id
                    visible: row.modelData.undone
                    text: "Put it back"
                    enabled: !row.busy
                    onClicked: root.act("bring_back", row.modelData.id, "")
                }
            ]

            Well {
                objectName: "answer:" + row.modelData.id
                text: row.answer ? row.answer.text : ""
                bad: row.answer ? !row.answer.ok : false
            }
        }
    }
}

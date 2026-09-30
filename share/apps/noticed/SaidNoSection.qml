import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// What he said no to. Bombadil does not offer these again until he brings one back.
Section {
    id: root

    property var rows: []
    property var pending: []
    property var answers: ({})

    signal act(string op, string id, string form)

    objectName: "section:said_no"
    title: "You said no to"
    hint: "Bombadil will not offer these again"
    count: rows.length
    emptyText: "You have not said no to anything."

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
            subtitle: (modelData.t > 0 ? "Said no " + Fmt.relative(modelData.t) : "")
                      + (modelData.form !== "" ? (modelData.t > 0 ? " · " : "") + "it was going to be " + modelData.form : "")

            actions: [
                Button {
                    objectName: "btn:bringback:" + row.modelData.id
                    text: "Bring back"
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

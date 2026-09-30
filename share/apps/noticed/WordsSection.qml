import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The short words Bombadil made from what he asks: say one and the thing opens. One that went unused
// for weeks was put away, and can be brought back.
Section {
    id: root

    property var rows: []
    property var pending: []
    property var answers: ({})

    signal act(string op, string id, string form)

    objectName: "section:words"
    title: "Words"
    hint: "Say one of these and the thing opens"
    count: rows.length

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
            title: "“" + modelData.phrase + "”"
            subtitle: modelData.opens !== "" ? "Opens " + modelData.opens : ""
            badge: modelData.away ? "Put away" : ""
            note: modelData.away ? "Not used for a while, so it was put away." : ""

            actions: [
                Button {
                    objectName: "btn:bringback:" + row.modelData.id
                    visible: row.modelData.away
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

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "text.js" as T

// What he asks most: his own words, how often, what it became, and where an offer waits its button
// (the row's own) and "Other ways".
Section {
    id: root

    property var rows: []
    property var pending: []
    property var answers: ({})
    property string waysOpen: ""      // the row whose other ways are showing

    signal act(string op, string id, string form)

    // A row that is no longer an offer has nothing to open; one that turns into an offer again starts shut.
    onRowsChanged: {
        if (waysOpen !== "" && !rows.some(r => r.id === waysOpen && r.offered))
            waysOpen = ""
    }
    // Choosing ends the looking around; a preview leaves the choices up to choose from.
    function choose(op, id, form) {
        if (op !== "preview")
            waysOpen = ""
        act(op, id, form)
    }

    objectName: "section:asks"
    title: "What you ask most"
    hint: "The things you typed more than once"
    count: rows.length
    emptyText: "Nothing you ask for more than once yet."

    Repeater {
        model: root.rows

        Line {
            id: row
            required property var modelData
            required property int index
            readonly property bool busy: root.pending.indexOf(modelData.id) >= 0
            readonly property var answer: root.answers[modelData.id]
            readonly property bool ways: root.waysOpen === modelData.id

            objectName: "row:" + modelData.id
            divider: index > 0
            title: modelData.title
            subtitle: T.times(modelData.n, modelData.days)
                      + (modelData.last > 0 ? " · last asked " + Fmt.relative(modelData.last) : "")
            note: modelData.sentences.map(s => "“" + s + "”").join("\n")
            badge: modelData.offered ? "" : T.sentence(modelData.became)
            badgeTone: modelData.state === "made" ? "good" : ""

            actions: [
                Button {
                    objectName: "btn:primary:" + row.modelData.id
                    visible: row.modelData.offered
                    text: row.modelData.primary.label
                    highlighted: true
                    enabled: !row.busy
                    onClicked: root.act(row.modelData.primary.op, row.modelData.id, row.modelData.primary.form)
                },
                Button {
                    objectName: "btn:ways:" + row.modelData.id
                    visible: row.modelData.offered
                    text: "Other ways"
                    flat: true
                    checkable: true
                    checked: row.ways
                    onClicked: root.waysOpen = row.ways ? "" : row.modelData.id
                }
            ]

            Caption {
                visible: text !== "" && row.modelData.offered
                text: row.modelData.what
                textFormat: Text.PlainText
            }
            Flow {
                objectName: "ways:" + row.modelData.id
                visible: row.ways && row.modelData.offered
                Layout.fillWidth: true
                spacing: Theme.gapSmall
                Repeater {
                    model: row.modelData.ways
                    Button {
                        required property var modelData
                        objectName: "btn:way:" + row.modelData.id + ":" + modelData.form
                        text: modelData.label
                        enabled: !row.busy
                        onClicked: root.choose(modelData.op, row.modelData.id, modelData.form)
                    }
                }
                Button {
                    objectName: "btn:preview:" + row.modelData.id
                    text: "Show me"
                    flat: true
                    enabled: !row.busy
                    onClicked: root.choose("preview", row.modelData.id, row.modelData.primary.form)
                }
                Button {
                    objectName: "btn:not_now:" + row.modelData.id
                    text: "Not now"
                    flat: true
                    enabled: !row.busy
                    onClicked: root.choose("not_now", row.modelData.id, "")
                }
                Button {
                    objectName: "btn:never:" + row.modelData.id
                    text: "Never"
                    flat: true
                    enabled: !row.busy
                    onClicked: root.choose("never", row.modelData.id, "")
                }
            }
            Well {
                objectName: "answer:" + row.modelData.id
                text: row.answer ? row.answer.text : ""
                bad: row.answer ? !row.answer.ok : false
            }
        }
    }
}

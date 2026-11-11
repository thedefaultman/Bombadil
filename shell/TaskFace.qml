import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The body of a task card: what the service says about the item (its status, why it is the person's), each of its
// fields as a labelled row (a box where the person can change it, a plain row where only the service can), and the
// button, "Create" or "Comment", with the same ring and the same rules as a reply's Send (Draft, DraftBar).
//
// A box's words go to agentd as an object of field values: `{key: text}` for every box, an empty one as "" (a box
// the person has not typed in), and whatever else the draft holds for a field that has no box, as it was.
FocusScope {
    id: face
    objectName: "taskFace"

    required property var pill
    property var card: null
    property int maxHeight: 440

    readonly property var proposal: draft.reply
    readonly property string via: card && card.via ? String(card.via) : ""

    // The boxes are rebuilt only when the fields change, never for a draft that did: a person in the middle of a
    // word is not given a new box.
    property var _fields: []
    property var _values: ({})           // what each box holds now, by key
    property var _loaded: ({})           // what was last put in from outside, by key (for a box that is built later)
    readonly property string _sig: JSON.stringify((card && Array.isArray(card.fields) ? card.fields : [])
        .map(f => [f.key, f.label, f.edit, f.edit ? "" : f.value]))
    readonly property var _keys: _fields.filter(f => !!f.edit).map(f => f.key)

    function _refresh() { _fields = card && Array.isArray(card.fields) ? card.fields : [] }
    on_SigChanged: _refresh()
    Component.onCompleted: _refresh()

    function _set(key, text) {
        const next = Object.assign({}, _values)
        next[key] = text
        _values = next
    }
    function focusBox() {
        for (let i = 0; i < fieldRepeater.count; i++) {
            const it = fieldRepeater.itemAt(i)
            if (it && it.box) { it.box.focusEnd(); return }
        }
    }
    function _load(content) {
        _loaded = content
        for (let i = 0; i < fieldRepeater.count; i++) {
            const it = fieldRepeater.itemAt(i)
            if (it && it.box) it.box.setText(content[it.key] !== undefined ? String(content[it.key]) : "")
        }
    }

    implicitHeight: column.implicitHeight
    Keys.onEscapePressed: face.pill.dismissCard()

    Draft {
        id: draft
        pill: face.pill
        card: face.card
        fields: true
        boxKeys: face._keys
        current: face._values
        boxFocus: face._anyFocus
        onWrite: content => face._load(content)
    }
    property bool _anyFocus: false

    ColumnLayout {
        id: column
        anchors { left: parent.left; right: parent.right; top: parent.top }
        spacing: Kit.Theme.gap

        Text {
            id: taskStatus
            objectName: "taskStatus"
            visible: text !== ""
            Layout.fillWidth: true
            text: face.card && face.card.status ? face.card.status : ""
            color: Kit.Theme.muted
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.smallSize
            textFormat: Text.PlainText
            elide: Text.ElideRight
        }
        Text {
            id: taskWhy
            objectName: "taskWhy"
            visible: text !== ""
            Layout.fillWidth: true
            text: face.card && face.card.why ? face.card.why : ""
            color: Kit.Theme.fg
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.textSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
        }

        CardScroll {
            id: scroll
            Layout.fillWidth: true
            visible: face._fields.length > 0
            spacing: Kit.Theme.gap
            maxHeight: Math.max(Kit.Theme.rowHeight * 2, face.maxHeight - bar.implicitHeight - Kit.Theme.gap
                                - (taskStatus.visible ? taskStatus.height : 0) - (taskWhy.visible ? taskWhy.height : 0))

            Repeater {
                id: fieldRepeater
                model: face._fields
                // A row: a label, and under it a box (or, where only the service can say, the words).
                ColumnLayout {
                    id: entry
                    required property var modelData
                    // (a row that is going has no data any more, and its bindings are still asked)
                    readonly property var spec: modelData || ({})
                    readonly property string key: String(spec.key)
                    readonly property string edit: spec.edit ? String(spec.edit) : ""
                    readonly property string label: spec.label ? String(spec.label) : ""
                    readonly property string value: spec.value !== undefined && spec.value !== null ? String(spec.value) : ""
                    // (only a field the person can change has a box; a plain row is a Text and has none)
                    readonly property var box: edit !== "" ? boxLoader.item : null
                    objectName: "taskField"
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        objectName: "fieldLabel"
                        text: entry.label
                        color: Kit.Theme.muted
                        font.family: Kit.Theme.fontFamily
                        font.pixelSize: Kit.Theme.captionSize
                        textFormat: Text.PlainText
                    }
                    Loader {
                        id: boxLoader
                        Layout.fillWidth: true
                        sourceComponent: entry && entry.edit !== "" ? editor : reader
                    }

                    Component {
                        id: editor
                        CardBox {
                            objectName: "fieldBox"
                            readonly property string edit: (entry && entry.edit) || ""
                            multiline: edit === "text"
                            minLines: edit === "text" ? 2 : 1
                            maxLines: edit === "text" ? 6 : 1
                            label: (entry && entry.label) || ""
                            placeholder: edit === "date" ? "YYYY-MM-DD" : ""
                            readOnly: !draft.editable
                            Component.onCompleted: {
                                const have = face._loaded[entry.key]
                                if (have !== undefined) setText(String(have))
                                face._set(entry.key, text)
                            }
                            onTextChanged: if (entry) face._set(entry.key, text)
                            onEdited: draft.changed()
                            onEscaped: face.pill.dismissCard()
                            onTaken: face.pill.wantKeys()
                            onActiveFocusChanged: face._focusMoved()
                        }
                    }
                    Component {
                        id: reader
                        Text {
                            objectName: "fieldValue"
                            text: (entry && entry.value) || ""
                            color: Kit.Theme.fg
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.textSize
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }

        DraftBar {
            id: bar
            Layout.fillWidth: true
            draft: draft
            pill: face.pill
            card: face.card
            visible: !!face.proposal
            extra: [
                CardButton {
                    objectName: "openButton"
                    visible: !!(face.card && face.card.open) && !bar.receiptOpens
                    compact: true
                    label: "Open in " + face.via
                    onActivated: face.pill.cardAction(face.card.id, "open")
                }
            ]
        }
    }

    function _focusMoved() {
        let any = false
        for (let i = 0; i < fieldRepeater.count; i++) {
            const it = fieldRepeater.itemAt(i)
            if (it && it.box && it.box.activeFocus) any = true
        }
        _anyFocus = any
    }
}

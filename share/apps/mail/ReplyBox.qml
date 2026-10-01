import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The reply box: who it goes to, the subject, what it says, and its files. What the person types goes to the service a
// moment after the last key and nowhere else; Send, the warnings and the line about what the provider adds are in
// SendBar, under it. The box tells the service which draft it has drawn (reportShown), once the window has drawn it.
// While the draft cannot take an edit (it is being sent) every field is read-only: what is shown is what is there.
ColumnLayout {
    id: box
    objectName: "replyBox"

    readonly property var d: backend.draft || ({})
    readonly property bool showCc: ccOpen || (!!d.cc && d.cc.length > 0)
    readonly property bool showBcc: bccOpen || (!!d.bcc && d.bcc.length > 0)
    property bool ccOpen: false
    property bool bccOpen: false
    property bool loading: false        // a text set from the backend, not typed: it is not an edit
    property bool pathOpen: false
    property var chooser: null
    property string lastId: ""

    signal opened()                     // a draft that was not in the box is now

    spacing: 6

    function load() {
        const f = backend.draftFields
        loading = true
        to.text = f.to || ""
        cc.text = f.cc || ""
        bcc.text = f.bcc || ""
        subject.text = f.subject || ""
        body.text = f.body || ""
        loading = false
        for (const row of [to, cc, bcc, subject])
            row.input.field.cursorPosition = 0   // a long field shows its start, not its end
        body.field.cursorPosition = 0
    }

    // A long list says how long it is, beside the field that shows only a few lines of it.
    function count(list) {
        return list && list.length > 3 ? list.length + " people" : ""
    }

    function focusFirst() {
        if (!backend.draft)
            return
        if (!d.to || d.to.length === 0)
            to.input.forceActiveFocus()
        else
            body.forceActiveFocus()
    }

    // A file chooser when the system has one; else a field for the file's whole path.
    function attach() {
        if (chooser === null) {
            try {
                chooser = Qt.createQmlObject(
                    'import QtQuick.Dialogs\nFileDialog { fileMode: FileDialog.OpenFiles; title: "Attach files" }',
                    box, "chooser")
                chooser.accepted.connect(() => backend.addFiles(chooser.selectedFiles.map(u => u.toString())))
            } catch (e) {
                chooser = null
                pathOpen = true
                path.input.forceActiveFocus()
                return
            }
        }
        chooser.open()
    }

    Component.onCompleted: {
        load()
        if (backend.draft) {            // already there when the window came up
            lastId = backend.draft.id
            Qt.callLater(focusFirst)
        }
        reportSoon()
    }

    // ---- telling the service what was drawn ----

    readonly property var host: Window.window
    property bool owed: false
    function reportSoon() {
        owed = true
        if (host)
            host.update()
    }
    function report() {
        owed = false
        if (!backend.draft || !visible || !host || !host.visible)
            return
        backend.reportShown(backend.draft.id, backend.draft.fingerprint)
    }
    onVisibleChanged: if (visible) reportSoon()
    Connections {
        target: box.host
        // The frame that follows the change is the one that has the new draft in it.
        function onFrameSwapped() { if (box.owed) box.report() }
    }
    Connections {
        target: backend
        function onDraftChanged() { box.reportSoon() }
        function onDraftLoaded() {
            box.load()
            const id = backend.draft ? backend.draft.id : ""
            if (id !== box.lastId) {
                box.lastId = id
                box.ccOpen = box.bccOpen = false
                box.pathOpen = false
                if (id !== "") {
                    box.opened()
                    Qt.callLater(box.focusFirst)
                }
            }
        }
    }

    // ---- what is drawn ----

    Flow {
        Layout.fillWidth: true
        spacing: 8
        PlainText {
            objectName: "boxKind"
            text: backend.draftKind
            font.weight: Font.DemiBold
            font.pixelSize: Theme.headingSize
        }
        QuietButton {
            objectName: "ccButton"
            visible: !box.showCc
            text: "Cc"
            flat: true
            compact: true
            onClicked: box.ccOpen = true
        }
        QuietButton {
            objectName: "bccButton"
            visible: !box.showBcc
            text: "Bcc"
            flat: true
            compact: true
            onClicked: box.bccOpen = true
        }
        QuietButton {
            objectName: "attachButton"
            text: "Attach a file"
            icon: "paperclip"
            compact: true
            onClicked: box.attach()
        }
    }
    // Who it is from and whose words they are, on a line of their own so that neither is cut short: the account that
    // sends is where the press is, and a draft Bombadil wrote says so.
    PlainText {
        objectName: "boxFrom"
        visible: text !== ""
        Layout.fillWidth: true
        text: [backend.draftAuthor, backend.draftFrom !== "" ? "from " + backend.draftFrom : ""]
            .filter(t => t !== "").join(" \u00b7 ")
        wrapMode: Text.Wrap
        color: Theme.muted
        font.pixelSize: Theme.smallSize
    }
    PlainText {
        objectName: "attachError"
        visible: backend.attachError !== ""
        Layout.fillWidth: true
        text: backend.attachError
        wrapMode: Text.Wrap
        font.pixelSize: Theme.captionSize
        color: Theme.badInk
    }

    FieldRow {
        id: to
        objectName: "toRow"
        Layout.fillWidth: true
        label: "To"
        error: backend.fieldErrors.to || ""
        readOnly: !backend.editable
        note: box.count(box.d.to)
        onEdited: if (!box.loading) backend.editField("to", text)
    }
    FieldRow {
        id: cc
        objectName: "ccRow"
        visible: box.showCc
        Layout.fillWidth: true
        label: "Cc"
        error: backend.fieldErrors.cc || ""
        readOnly: !backend.editable
        note: box.count(box.d.cc)
        onEdited: if (!box.loading) backend.editField("cc", text)
    }
    FieldRow {
        id: bcc
        objectName: "bccRow"
        visible: box.showBcc
        Layout.fillWidth: true
        label: "Bcc"
        error: backend.fieldErrors.bcc || ""
        readOnly: !backend.editable
        note: box.count(box.d.bcc)
        onEdited: if (!box.loading) backend.editField("bcc", text)
    }
    FieldRow {
        id: subject
        objectName: "subjectRow"
        Layout.fillWidth: true
        label: "Subject"
        error: backend.fieldErrors.subject || ""
        readOnly: !backend.editable
        onEdited: if (!box.loading) backend.editField("subject", text)
    }

    Flow {
        objectName: "boxAttachments"
        visible: !!box.d.attachments && box.d.attachments.length > 0
        Layout.fillWidth: true
        spacing: 6
        Repeater {
            model: box.d.attachments || []
            delegate: Rectangle {
                id: chip
                required property var modelData
                height: 30
                width: chipRow.implicitWidth + 12
                radius: Theme.radiusSmall
                color: Theme.raised
                border.color: Theme.borderStrong
                Row {
                    id: chipRow
                    anchors.centerIn: parent
                    spacing: 6
                    Icon { anchors.verticalCenter: parent.verticalCenter; name: "paperclip"; size: 14; color: Theme.muted }
                    PlainText {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.min(implicitWidth, 220)
                        text: chip.modelData.name
                        elide: Text.ElideMiddle
                        font.pixelSize: Theme.smallSize
                    }
                    QuietButton {
                        objectName: "removeAttachment"
                        anchors.verticalCenter: parent.verticalCenter
                        icon: "x"
                        flat: true
                        compact: true
                        tooltip: "Take " + chip.modelData.name + " off"
                        onClicked: backend.removeAttachment(chip.modelData.name)
                    }
                }
            }
        }
    }

    QuietArea {
        id: body
        objectName: "bodyEditor"
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredHeight: 104     // five lines, even on a short pane; it takes what the pane has beyond that
        Layout.minimumHeight: 104
        label: "What you write"
        placeholder: "Write your reply"
        readOnly: !backend.editable
        onEdited: if (!box.loading) backend.editField("body", text)
    }
    PlainText {
        visible: backend.fieldErrors.body !== undefined
        Layout.fillWidth: true
        text: backend.fieldErrors.body || ""
        wrapMode: Text.Wrap
        font.pixelSize: Theme.captionSize
        color: Theme.badInk
    }

    FieldRow {
        id: path
        visible: box.pathOpen
        Layout.fillWidth: true
        label: "File"
        placeholder: "The whole path of the file"
        onAccepted: {
            if (path.text.trim() !== "") {
                backend.addFiles([path.text.trim()])
                path.text = ""
            }
        }
    }
}

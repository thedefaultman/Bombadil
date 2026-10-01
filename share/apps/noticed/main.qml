import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "text.js" as T

// Noticed: what Bombadil has noticed about what you ask, what it made from that, and the bugs it found
// in itself. It draws what agentd says (`backend.full`) and sends what he presses; nothing is decided
// here, and nothing is posted without him pressing Submit on the page the Send card opens.
AppWindow {
    id: win
    title: "Noticed"
    icon: "eye"
    width: 620; height: 700
    subtitle: stack.currentIndex === 1 ? T.header(full) : ""

    readonly property var full: backend.full
    // The lists, the Send card, or the line that says why there is nothing to draw.
    readonly property string view: !backend.ready ? "away" : sendRow !== null ? "send" : "lists"
    readonly property var sendRow: findFound(sendId)
    readonly property bool everythingEmpty: full.asks.length + full.changes.length + full.found.length
                                            + full.said_no.length + full.words.length === 0

    property string sendId: ""             // the found row whose Send card is open
    property var declined: ({})            // rows whose card he put away: it does not open by itself again
    property var reportedSeen: ({})        // the rows that were waiting as reports in the last list
    property var answers: ({})             // what agentd said to a tap, by row: a preview or what went wrong
    property bool slow: false              // connected, and agentd has still not answered
    property bool reporting: false         // a report was asked for and the lists have not come back since
    property bool reportAnswered: false

    function findFound(id) {
        return id === "" ? null : full.found.find(f => f.id === id) ?? null
    }

    function act(op, id, form) {
        const a = Object.assign({}, answers)
        delete a[id]
        answers = a
        if (op === "report") {
            const d = Object.assign({}, declined)
            delete d[id]
            declined = d
            sendId = id
            reporting = true
            reportAnswered = false
            sendWait.restart()
        }
        backend.actForm(op, id, form)
    }

    function decline(id) {
        const d = Object.assign({}, declined)
        d[id] = true
        declined = d
        sendId = ""
    }

    // What he just asked the Send card for (the report, written by agentd) opens by itself, and so does
    // one he left waiting when the window closed.
    function showReport() {
        if (sendId !== "" && sendRow === null)
            sendId = ""
        const held = full.found.filter(f => f.state === "reported" && f.preview.text && !declined[f.id])
        // A row that has only just become a report was asked for just now (in the bar, or here): it takes
        // the card, even from another that is open. Otherwise an open card stays, and a report left
        // waiting opens when there is none.
        const asked = held.find(f => !reportedSeen[f.id])
        const seen = {}
        for (const f of full.found)
            if (f.state === "reported" && f.preview.text)
                seen[f.id] = true
        reportedSeen = seen
        if (asked)
            sendId = asked.id
        else if (sendId === "" && held.length > 0)
            sendId = held[0].id
    }
    onFullChanged: showReport()

    Connections {
        target: backend
        function onAnswered(op, id, ok, text, preview) {
            const a = Object.assign({}, win.answers)
            if (id !== "" && !ok)
                a[id] = { ok: false, text: text || "That did not work." }
            else if (id !== "" && op === "preview" && preview.text)
                a[id] = { ok: true, text: preview.text }
            win.answers = a
            if (op === "report") {
                win.reportAnswered = true
                if (!ok) {
                    win.reporting = false
                    win.sendId = ""
                }
            } else if (op === "send") {
                win.sendId = ""
            }
            if (op === "report" && ok)
                return
            if (!ok && id === "")
                win.toast(text || "That did not work.", "bad")
            else if (ok && text !== "" && op !== "preview")
                win.toast(text, "good")
        }
        // The report is fresh once the lists have come back after its answer.
        function onListed() {
            if (win.reportAnswered) {
                win.reporting = false
                win.reportAnswered = false
            }
        }
        function onReadyChanged() { win.slow = false }
        function onConnectedChanged() { win.slow = false }
    }
    Timer { id: sendWait; interval: 4000; onTriggered: win.reporting = false }
    Timer { interval: 2000; running: backend.connected && !backend.ready; onTriggered: win.slow = true }

    StackLayout {
        id: stack
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: win.view === "lists" ? 1 : win.view === "send" ? 2 : 0

        // Bombadil is away, or has said nothing yet.
        EmptyState {
            objectName: "away"
            icon: "moon"
            title: T.awayTitle(backend.connected)
            text: T.awayText(backend.connected)
            visible: !backend.connected || win.slow
        }

        ColumnLayout {
            spacing: Theme.gap

            ScrollPane {
                objectName: "lists"
                Layout.fillWidth: true
                Layout.fillHeight: true

                Caption {
                    objectName: "note"
                    visible: text !== ""
                    text: win.full.hidden
                          ? "Noticed is hidden. Bombadil still counts quietly, and offers nothing."
                          : T.restingLine(win.full.resting)
                }
                AsksSection {
                    rows: win.full.asks; pending: backend.pending; answers: win.answers
                    everythingEmpty: win.everythingEmpty
                    onAct: (op, id, form) => win.act(op, id, form)
                }
                ChangesSection {
                    rows: win.full.changes; pending: backend.pending; answers: win.answers
                    everythingEmpty: win.everythingEmpty
                    onAct: (op, id, form) => win.act(op, id, form)
                }
                FoundSection {
                    rows: win.full.found; pending: backend.pending; answers: win.answers
                    everythingEmpty: win.everythingEmpty
                    onAct: (op, id, form) => win.act(op, id, form)
                }
                WordsSection {
                    rows: win.full.words; pending: backend.pending; answers: win.answers
                    onAct: (op, id, form) => win.act(op, id, form)
                }
                SaidNoSection {
                    rows: win.full.said_no; pending: backend.pending; answers: win.answers
                    everythingEmpty: win.everythingEmpty
                    onAct: (op, id, form) => win.act(op, id, form)
                }
            }

            Divider {}
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gap
                FootLink {
                    objectName: "foot:forget"
                    text: "Forget what I ask"
                    enabled: backend.pending.indexOf("op:forget_asks") < 0
                    onClicked: forget.open()
                }
                FootLink {
                    objectName: "foot:clear"
                    text: "Clear what it found"
                    enabled: backend.pending.indexOf("op:clear_found") < 0
                    onClicked: clear.open()
                }
                Spacer {}
                FootLink {
                    objectName: "foot:hide"
                    text: win.full.hidden ? "Show noticed" : "Hide noticed"
                    enabled: backend.pending.indexOf("op:hide") < 0 && backend.pending.indexOf("op:show") < 0
                    onClicked: win.act(win.full.hidden ? "show" : "hide", "", "")
                }
            }
        }

        SendCard {
            objectName: "sendCard"
            row: win.sendRow
            busy: win.sendRow !== null && backend.pending.indexOf(win.sendRow.id) >= 0
            waiting: win.reporting
            onBack: win.decline(win.sendId)
            onOpenPage: win.act("send", win.sendId, "")
            onNotNow: {
                const id = win.sendId
                win.decline(id)
                win.act("not_now", id, "")
            }
            onNever: {
                const id = win.sendId
                win.decline(id)
                win.act("never", id, "")
            }
        }
    }

    ConfirmDialog {
        id: forget
        objectName: "confirm:forget"
        title: "Forget what you ask?"
        text: "Bombadil forgets what it counted of your asks. What you said no to stays said, and the words it made stay."
        confirmText: "Forget"
        danger: true
        onConfirmed: win.act("forget_asks", "", "")
    }
    ConfirmDialog {
        id: clear
        objectName: "confirm:clear"
        title: "Clear what it found?"
        text: "Bombadil drops the bugs it found and the reports it was holding. Nothing was sent. If a bug still happens, it may find it again."
        confirmText: "Clear"
        danger: true
        onConfirmed: win.act("clear_found", "", "")
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "who.js" as Who

// The thing in the middle: its name, where it lives, who made it, and the thing itself
// (the text, the picture, the first page, or the list of what is in a folder).
Panel {
    id: card
    property var answer: null
    readonly property var thing: answer ? (answer.thing || {}) : {}
    readonly property var preview: answer ? (answer.preview || { type: "none" }) : { type: "none" }
    readonly property var children_: answer ? answer.children : null
    readonly property var description: answer ? answer.description : null
    readonly property bool isFolder: ["folder", "home", "project", "app"].indexOf(thing.kind) >= 0
    readonly property bool isFile: !!thing.path && !isFolder
    readonly property string folderOf: thing.path ? thing.path.replace(/\/[^\/]*$/, "") || "/" : ""
    signal picked(string ref)
    signal said(string text, string tone)

    padding: Theme.pad
    spacing: Theme.gapSmall

    // Title row
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gap
        Icon {
            name: Who.icon(card.thing.kind, card.thing.title)
            size: 22
            color: card.thing.deleted ? Theme.faint : Theme.muted
        }
        Heading {
            Layout.fillWidth: true
            level: 1
            text: card.thing.title || ""
            textFormat: Text.PlainText
            elide: Text.ElideMiddle
            color: card.thing.deleted ? Theme.muted : Theme.fg
        }
        Badge { visible: !!card.thing.private; text: "private"; icon: "lock" }
        Badge { visible: !!card.thing.deleted; text: "gone"; tone: "muted" }
    }

    Caption {
        text: {
            const bits = []
            const area = card.thing.area
            if (area && area.title && area.ref !== card.thing.ref) bits.push(area.title)
            if (card.thing.path) bits.push(backend.tilde(card.isFolder ? card.thing.path : card.folderOf))
            else if (card.thing.url) bits.push(card.thing.url)
            if (card.isFile && card.thing.size !== null && card.thing.size !== undefined) bits.push(Fmt.bytes(card.thing.size))
            if (card.children_ && card.children_.total !== undefined)
                bits.push(card.children_.total === 1 ? "1 item" : Fmt.number(card.children_.total) + " items")
            return bits.join("  ·  ")
        }
        textFormat: Text.PlainText
        elide: Text.ElideMiddle
        maximumLineCount: 1
        wrapMode: Text.NoWrap
    }

    // Who made it, in words; the model's one line when there is one (greyed when stale)
    Body {
        text: [card.thing.made || "", card.thing.last || ""].filter(s => s).join(" ")
        textFormat: Text.PlainText
        visible: text !== ""
        color: Theme.fg
    }
    Body {
        readonly property var d: card.description
        visible: !!d && (!!d.text || !!d.pending)
        // What a model wrote about it is a sentence, never markup.
        text: !d ? "" : (d.text ? d.text + (d.stale ? (d.pending ? "  (out of date, rewriting)" : "  (out of date)") : "")
                                 : "Writing a line about it…")
        textFormat: Text.PlainText
        color: !d || d.stale || !d.text ? Theme.faint : Theme.muted
        font.italic: true
    }

    // What you can do with it
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gapSmall
        visible: !card.thing.deleted
        Button {
            visible: card.isFile || !!card.thing.url || card.thing.kind === "app"
            text: card.thing.kind === "app" ? "Open app" : (card.thing.url ? "Open in the browser" : "Open")
            icon.source: Theme.icon("external-link")
            highlighted: true
            onClicked: {
                if (card.thing.url) { App.openUrl(card.thing.url); return }
                const ok = card.thing.kind === "app" ? backend.openApp(card.thing.ref.replace(/^app:/, ""))
                                                   : backend.openFile(card.thing.path)
                if (!ok) card.said("Nothing on this machine opens this kind of file yet.", "warn")
            }
        }
        Button {
            visible: card.isFile
            text: "Show in folder"
            icon.source: Theme.icon("folder")
            onClicked: backend.showInFolder(card.thing.path, card.folderOf)
        }
        Button {
            visible: card.isFolder && !!card.thing.path
            text: "Open a terminal here"
            icon.source: Theme.icon("terminal")
            onClicked: if (!backend.terminalHere(card.thing.path)) card.said("There is no terminal to open.", "warn")
        }
        Button {
            text: "Ask about this"
            icon.source: Theme.icon("sparkles")
            onClicked: {
                const about = card.thing.path ? backend.tilde(card.thing.path) : (card.thing.url || card.thing.title)
                if (!backend.ask(about)) card.said("The machine's agent is not running.", "warn")
            }
        }
        Spacer {}
    }

    Divider { Layout.topMargin: 4 }

    // The thing itself
    Loader {
        Layout.fillWidth: true
        Layout.fillHeight: true
        sourceComponent: {
            if (card.thing.deleted) return gone
            if (card.children_ && card.children_.items) return list
            switch (card.preview.type) {
            case "text": return backend.fileBytes(card.preview.path || card.thing.path || "") > 2000000 ? tooBig : text
            case "image": return image
            case "pdf": return pdf
            case "turn": case "fact": case "package": case "session": case "site": case "page": return words
            }
            return nothing
        }
    }

    Component {
        id: text
        Editor {
            path: card.preview.path || card.thing.path || ""
            language: Who.language(card.thing.title)
            readOnly: true
        }
    }
    Component {
        id: image
        Rectangle {
            color: Theme.sunken
            radius: Theme.radiusSmall
            Image {
                anchors.fill: parent
                anchors.margins: 8
                source: card.preview.path ? "file://" + card.preview.path.split("/").map(encodeURIComponent).join("/") : ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                sourceSize.width: 1600
                sourceSize.height: 1600
            }
        }
    }
    Component {
        id: pdf
        Rectangle {
            id: page
            color: Theme.sunken
            radius: Theme.radiusSmall
            property string url: backend.pdfPage(card.preview.path || "")
            property bool failed: false
            Connections {
                target: backend
                function onPreviewReady(path) {
                    if (path === card.preview.path) page.url = backend.pdfPage(path)
                }
                function onPreviewFailed(path) {
                    if (path === card.preview.path) page.failed = true
                }
            }
            Image {
                anchors.fill: parent
                anchors.margins: 8
                source: page.url
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                visible: page.url !== ""
            }
            Caption {
                anchors.centerIn: parent
                width: implicitWidth
                visible: page.url === ""
                horizontalAlignment: Text.AlignHCenter
                text: page.failed || !backend.canDrawPdf ? "No picture of this one. Open it to read it."
                                                         : "Drawing the first page…"
            }
        }
    }
    Component {
        id: words
        ScrollPane {
            Body {
                text: card.preview.text || ""
                textFormat: Text.PlainText
                visible: text !== ""
            }
            Mono {
                text: card.preview.url || ""
                textFormat: TextEdit.PlainText
                visible: text !== ""
            }
        }
    }
    Component {
        id: list
        ColumnLayout {
            spacing: 4
            // A turn is a list of what it changed, and its summary is what it said it did.
            Body {
                visible: card.preview.type === "turn" && text !== ""
                text: card.preview.text || ""
                textFormat: Text.PlainText
                color: Theme.muted
            }
            BrainList {
                id: files
                Layout.fillWidth: true
                Layout.fillHeight: true
                emptyText: card.thing.kind === "turn" ? "It changed no files." : "Nothing in here."
                model: ((card.children_ && card.children_.items) || []).map(c => ({
                    ref: c.ref, title: c.name,
                    subtitle: c.private ? "private" : (c.who || ""),
                    trailing: c.when || "",
                    actor: c.private ? "" : (c.actor || ""),
                    icon: c.private ? "lock" : Who.icon(c.kind, c.name)
                }))
                onActivated: (i, item) => card.picked(item.ref)
                Connections {
                    target: backend
                    function onSelectChanged() { files.pick() }
                }
                onModelChanged: pick()
                // Show in folder: the entry the thing in the middle came from.
                function pick() {
                    if (!backend.select) return
                    for (let i = 0; i < model.length; i++)
                        if (model[i].ref === backend.select) {
                            select(i)
                            forceActiveFocus()
                            backend.settled()
                            return
                        }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !!card.children_ && card.children_.total > (card.children_.items || []).length
                Caption {
                    Layout.fillWidth: true
                    text: "Showing " + ((card.children_ && card.children_.items) || []).length + " of "
                          + Fmt.number(card.children_ ? card.children_.total : 0) + ", folders first, then newest."
                }
                Button {
                    flat: true
                    text: "Show more"
                    onClicked: backend.moreChildren()
                }
            }
        }
    }
    Component {
        id: tooBig
        EmptyState { icon: "file-text"; title: "Too big to show here"; text: "Open it to read it." }
    }
    Component {
        id: gone
        EmptyState {
            icon: "trash"
            title: "It is gone"
            text: "Its history stays here: who made it, what it was used with, and every change."
        }
    }
    Component {
        id: nothing
        EmptyState {
            icon: card.thing.private ? "lock" : Who.icon(card.thing.kind, card.thing.title)
            title: card.thing.private ? "Private" : "No preview"
            text: card.thing.private ? "The brain knows its name and who made it, and never reads what is inside."
                                     : "Open it to see what is inside."
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "who.js" as Who

// Focus: the thing in front in the middle, and everything around it in fixed places.
// Where it came from on the left, what it was read with on the right, what it is used with
// below, its changes on top. Click any of them and it slides to the middle.
AppWindow {
    id: win
    title: "Brain"
    subtitle: backend.status
    icon: "network"
    width: 1180; height: 760
    actions: [
        SearchField {
            id: search
            Layout.preferredWidth: 280; Layout.fillWidth: false
            placeholderText: "Find anything"
            onTextChanged: { win.wantFirst = false; backend.search(text) }
            // Enter opens the first match, once the matches are for what is typed now.
            onAccepted: {
                if (backend.resultsFor === text.trim()) {
                    if (backend.results.length > 0) win.pickResult(backend.results[0])
                } else if (text.trim() !== "") {
                    win.wantFirst = true
                    backend.searchNow()
                }
            }
            Keys.onDownPressed: if (found.visible) { results.forceActiveFocus(); results.currentIndex = 0 }
        }
    ]

    readonly property var answer: backend.focus
    readonly property var slots: answer ? (answer.slots || {}) : {}
    readonly property var thing: answer ? (answer.thing || {}) : {}
    readonly property bool sides: win.count(slots.came_from) + win.count(slots.read_with) > 0
    readonly property int shown: backend.expanded ? 50 : 5
    property bool wantFirst: false

    function count(slot) { return slot && slot.items ? slot.items.length : 0 }
    function pickResult(item) {
        win.wantFirst = false
        search.text = ""
        search.forceActiveFocus()   // the list the pick came from must not keep the drop-down open
        backend.open(item.ref)
    }

    Connections {
        target: backend
        function onShowRequested() { App.show() }
        function onToast(text, tone) { win.toast(text, tone) }
        function onResultsChanged() {
            if (win.wantFirst && backend.resultsFor === search.text.trim()) {
                win.wantFirst = false
                if (backend.results.length > 0) win.pickResult(backend.results[0])
            }
        }
    }
    Shortcut { sequence: "Alt+Left"; onActivated: backend.back() }
    Shortcut { sequence: "Ctrl+R"; onActivated: backend.refresh() }

    Trail {
        Layout.fillWidth: true
        steps: backend.trail
        visible: backend.connected && steps.length > 0
        onJumped: i => backend.jump(i)
    }

    EmptyState {
        Layout.fillWidth: true; Layout.fillHeight: true
        visible: !backend.connected
        icon: "network"
        title: win.answer ? "The brain went away" : "The brain is not running yet"
        text: win.answer ? "This comes back where you were as soon as it answers again."
                         : "It starts with your session and learns from every save, download and turn. "
                           + "This fills in as soon as it answers."
    }

    EmptyState {
        Layout.fillWidth: true; Layout.fillHeight: true
        visible: backend.connected && !win.answer && backend.error !== ""
        icon: "search"
        title: backend.error
        text: "Try another thing, or search for it above."
        actionText: backend.canBack ? "Go back" : ""
        onAction: backend.back()
    }

    BusyIndicator {
        Layout.alignment: Qt.AlignCenter
        Layout.fillHeight: true
        visible: backend.connected && !win.answer && backend.error === ""
        running: visible
    }

    Changes {
        Layout.fillWidth: true
        visible: backend.connected && !!win.answer
        changes: win.slots.changes || null
    }

    RowLayout {
        Layout.fillWidth: true; Layout.fillHeight: true
        visible: backend.connected && !!win.answer
        spacing: Theme.gap

        Slot {
            Layout.preferredWidth: 300; Layout.fillHeight: true
            visible: win.sides
            label: "Came from"
            links: win.slots.came_from || null
            shown: win.shown
            emptyText: "The brain has not seen who made it."
            onPicked: ref => backend.go(ref)
            onMoreRequested: backend.more()
        }
        Preview {
            Layout.fillWidth: true; Layout.fillHeight: true
            answer: win.answer
            onPicked: ref => backend.go(ref)
            onSaid: (text, tone) => win.toast(text, tone)
        }
        Slot {
            Layout.preferredWidth: 300; Layout.fillHeight: true
            visible: win.sides
            label: "Read with"
            links: win.slots.read_with || null
            shown: win.shown
            emptyText: "Nothing was open while it changed."
            onPicked: ref => backend.go(ref)
            onMoreRequested: backend.more()
        }
    }

    Slot {
        Layout.fillWidth: true
        visible: backend.connected && !!win.answer && win.count(win.slots.used_with) > 0
        label: "Used with"
        columns: 3
        links: win.slots.used_with || null
        shown: backend.expanded ? 50 : 6
        onPicked: ref => backend.go(ref)
        onMoreRequested: backend.more()
    }

    // Search results drop down under the field.
    Popup {
        id: found
        parent: search
        x: search.width - width
        y: search.height + 6
        width: 440
        height: Math.min(results.implicitHeight + 16, 420)
        padding: 8
        visible: search.text !== "" && search.activeFocus || results.activeFocus
        closePolicy: Popup.NoAutoClose
        BrainList {
            id: results
            anchors.fill: parent
            emptyText: "Nothing matches “" + search.text + "” yet."
            model: backend.results.map(r => ({
                ref: r.ref, title: r.title,
                subtitle: r.why || r.path || "", trailing: r.when || "",
                icon: Who.icon(r.kind, r.title)
            }))
            onActivated: (i, item) => win.pickResult(item)
            // Esc would hide the window (the kit binds it) unless the list claims it first.
            Keys.onShortcutOverride: event => event.accepted = event.key === Qt.Key_Escape
            Keys.onEscapePressed: { search.text = ""; search.forceActiveFocus() }
        }
    }
}

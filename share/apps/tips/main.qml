import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Tips: what to ask, shown in action. Left, the stage, where the tip in front plays once at app size (a close-up
// of the real screen) and holds its last frame, with its three phase captions in a row under it, then its one
// line, then the button with the exact words and Replay beside it (a tip with no button shows its marks in
// place of it). Right, the list of every tip this machine can back, by section.
//
// The button hides this window and then puts its words in the pill (Agent.draft): nothing runs on a click, the
// person's own Enter does. Nothing here advances by itself, loops or makes a sound. Under reduced motion the
// stage is three stills with their captions. Up and Down select, Enter presses the button, Space plays the tip
// again, Esc hides the window (the kit's key). The orange of the machine is nowhere in it: a tip is nobody's.
// docs/TIPS.md, "The Tips app".
AppWindow {
    id: win
    title: "Tips"
    width: 960
    height: 516
    focus: true

    readonly property var tip: backend.selectedTip
    readonly property bool hasTip: !!tip && !!tip.id
    readonly property bool hasWords: hasTip && tip.words !== ""
    readonly property bool hasMarks: hasTip && tip.words === "" && tip.marks.length > 0
    readonly property bool plays: !Theme.reducedMotion && scene.duration > 0

    // What the button does: this window goes away first, so the real thing happens on a clear screen, and then
    // the words are in the pill with the cursor at the end. Nothing is sent.
    function press() {
        if (!win.hasWords)
            return
        App.hide()
        Agent.draft(backend.draftOf(win.tip.id), win.tip.id)
    }
    function replay() {
        if (win.plays)
            scene.replay()
    }
    // The tip in front plays once from its start and holds its last frame.
    function show() {
        const now = backend.selectedTip
        scene.tip = now && now.id ? now : ({})
        if (now && now.id)
            scene.replay()
    }

    Component.onCompleted: {
        win.forceActiveFocus()
        win.show()
    }

    Connections {
        target: backend
        function onSelectedChanged() { win.show() }
        function onOpenRequested() { win.show() }
    }

    Keys.onPressed: event => {
        if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            return
        if (event.key === Qt.Key_Up)
            backend.move(-1)
        else if (event.key === Qt.Key_Down)
            backend.move(1)
        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (!event.isAutoRepeat)
                win.press()
        } else if (event.key === Qt.Key_Space) {
            if (!event.isAutoRepeat)
                win.replay()
        } else
            return
        event.accepted = true
    }

    EmptyState {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: !backend.connected
        icon: "sparkles"
        title: "The agent is not answering"
        text: "Tips come back as soon as it does."
    }

    EmptyState {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: backend.connected && backend.count === 0
        icon: "sparkles"
        title: "No tips on this computer yet"
    }

    RowLayout {
        objectName: "content"
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: backend.connected && backend.count > 0
        spacing: Theme.pad + 4

        ColumnLayout {
            Layout.preferredWidth: 576
            Layout.maximumWidth: 576
            Layout.alignment: Qt.AlignTop
            spacing: Theme.gap

            // The stage, and under it the three phase captions (the scene draws both). Its height is the one the
            // scene has while it plays, so that the line and the button do not move when the stills show instead.
            Item {
                objectName: "stageBox"
                Layout.preferredWidth: 576
                Layout.preferredHeight: 350
                TipScene {
                    id: scene
                    objectName: "scene"
                    density: "app"
                    reducedMotion: Theme.reducedMotion
                    y: Theme.reducedMotion ? Math.round((parent.height - height) / 2) : 0
                }
            }

            Text {
                objectName: "tipLine"
                Layout.fillWidth: true
                text: win.hasTip ? win.tip.line : ""
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
            }

            RowLayout {
                objectName: "actions"
                Layout.fillWidth: true
                spacing: Theme.gap
                PillButton {
                    objectName: "wordsButton"
                    visible: win.hasWords
                    text: win.hasWords ? win.tip.words : ""
                    Layout.preferredWidth: implicitWidth
                    Layout.maximumWidth: 576 - (replayButton.visible ? replayButton.implicitWidth + Theme.gap : 0)
                    onClicked: win.press()
                }
                Marks {
                    objectName: "marks"
                    visible: win.hasMarks
                    marks: win.hasMarks ? win.tip.marks : []
                    Layout.alignment: Qt.AlignVCenter
                }
                PillButton {
                    id: replayButton
                    objectName: "replayButton"
                    visible: win.hasTip && win.plays
                    quiet: true
                    icon: "rotate-ccw"
                    text: "Replay"
                    onClicked: win.replay()
                }
                Item { Layout.fillWidth: true }
            }
        }

        TipList {
            objectName: "list"
            Layout.fillWidth: true
            Layout.fillHeight: true
            current: backend.selectedRow
            onPicked: id => backend.select(id)
        }
    }
}

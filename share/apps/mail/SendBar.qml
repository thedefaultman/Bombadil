import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Under the reply box: what to look at before sending (the warnings, what the provider adds), and Send.
//
// Send is the person's. It carries the orange ring, and the label "Yours: Send" next to it says so; nothing else in
// this window is orange. It can be pressed only when the backend says the draft on screen is the one the service
// has, and that the service has been told it was drawn; otherwise it is dimmed and one quiet line says why. The
// press is made in `activate()`, from a click or a key on the button itself: that is the only line in the QML that
// calls press(). (Assistive technology that presses buttons by name is not given that one: a screen reader's user
// reaches Send by Tab and presses it with Space or Return, which are keys on the button like any other.)
ColumnLayout {
    id: bar
    objectName: "sendBar"

    readonly property var d: backend.draft || ({})
    readonly property bool live: send.armed && backend.canSend
    property bool confirming: false

    spacing: 8

    Connections {
        target: backend
        function onDraftLoaded() { bar.confirming = false }
    }

    // Warnings: a plain sentence each, with the addresses in it named. Amber, since they are the system's.
    Rectangle {
        objectName: "warnings"
        visible: !!bar.d.warnings && bar.d.warnings.length > 0
        Layout.fillWidth: true
        implicitHeight: warnCol.implicitHeight + 16
        radius: Theme.radiusSmall
        color: Theme.alpha(Theme.warn, 0.12)
        border.color: Theme.alpha(Theme.warn, 0.5)
        ColumnLayout {
            id: warnCol
            anchors { fill: parent; margins: 8 }
            spacing: 6
            Repeater {
                model: bar.d.warnings || []
                delegate: RowLayout {
                    id: warn
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 8
                    Icon { Layout.alignment: Qt.AlignTop; name: "alert-triangle"; size: 16; color: Theme.warn }
                    PlainText {
                        Layout.fillWidth: true
                        text: warn.modelData.text
                        wrapMode: Text.Wrap
                        color: Theme.warnInk
                        font.pixelSize: Theme.smallSize
                    }
                }
            }
        }
    }

    PlainText {
        objectName: "adds"
        visible: !!bar.d.adds
        Layout.fillWidth: true
        text: bar.d.adds || ""
        wrapMode: Text.Wrap
        font.pixelSize: Theme.captionSize
        color: Theme.muted
    }

    // Somebody else changed this draft while it was open (Bombadil, another window): what is shown is what Send
    // would send now, and the person is asked to read it again.
    PlainText {
        objectName: "changeNote"
        visible: backend.changeNote !== ""
        Layout.fillWidth: true
        text: backend.changeNote
        wrapMode: Text.Wrap
        font.pixelSize: Theme.smallSize
        color: Theme.warnInk
    }

    // What became of the last press, in agentd's own words (or the service's, for a draft that may have gone). A
    // draft that may have gone points to where Sent is: the provider's own mail.
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        visible: backend.pressLine !== ""
        PlainText {
            objectName: "pressLine"
            Layout.fillWidth: true
            text: backend.pressLine
            wrapMode: Text.Wrap
            font.pixelSize: Theme.smallSize
            color: backend.unknownOutcome ? Theme.warnInk : Theme.badInk
        }
        LinkButton {
            objectName: "sentLink"
            Layout.alignment: Qt.AlignTop
            visible: backend.unknownOutcome && !!backend.draftWeb
            text: backend.draftWeb && backend.draftWeb.name ? "Open " + backend.draftWeb.name : "Open on the web"
            onClicked: backend.openWeb(backend.draftWeb.url)
        }
    }

    Tick {
        objectName: "lookedTick"
        visible: backend.unknownOutcome
        text: "I looked in Sent"
        checked: backend.looked
        onToggled: value => backend.setLooked(value)
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 12

        FocusScope {
            id: send
            objectName: "sendButton"

            // Not pressable until it has been pressable for a moment: a click that was on its way to something else
            // when the draft changed under the pointer does not send.
            property bool armed: false
            readonly property bool live: bar.live
            readonly property bool hovered: hover.hovered

            function activate() {
                if (bar.live)
                    backend.press()
            }

            implicitWidth: sendRow.implicitWidth + 36
            implicitHeight: 38
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: backend.sendLabel

            Timer {
                id: arm
                interval: backend.armDelay      // longer for a draft that somebody else just changed
                onTriggered: send.armed = backend.canSend
            }
            Connections {
                target: backend
                function onGateChanged() {
                    if (!backend.canSend) {
                        send.armed = false
                        arm.stop()
                    } else if (!send.armed && !arm.running) {
                        arm.start()
                    }
                }
            }
            Component.onCompleted: if (backend.canSend) arm.start()

            HoverHandler { id: hover }
            TapHandler {
                id: tap
                onTapped: send.activate()
            }

            Rectangle {
                objectName: "sendRing"
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: tap.pressed && bar.live ? Theme.panel : send.hovered && bar.live ? Theme.overlay : Theme.raised
                border.width: 2
                border.color: Theme.accent
                opacity: bar.live ? 1 : 0.4
            }
            Row {
                id: sendRow
                anchors.centerIn: parent
                spacing: 8
                opacity: bar.live ? 1 : 0.5
                Icon {
                    anchors.verticalCenter: parent.verticalCenter
                    name: "send"
                    size: 16
                    color: Theme.fg
                }
                PlainText {
                    objectName: "sendLabel"
                    anchors.verticalCenter: parent.verticalCenter
                    text: backend.sendLabel
                    font.weight: Font.DemiBold
                }
            }

            Keys.onReturnPressed: send.activate()
            Keys.onEnterPressed: send.activate()
            Keys.onSpacePressed: send.activate()
        }

        PlainText {
            objectName: "yoursLabel"
            text: "Yours: Send"
            font.pixelSize: Theme.captionSize
            font.weight: Font.Medium
            color: Theme.accentInk
            opacity: bar.live ? 1 : 0.5     // dim with the button: what is not pressable is not "yours" yet
        }

        Item { Layout.fillWidth: true }

        QuietButton {
            objectName: "discardButton"
            visible: !bar.confirming
            text: "Discard"
            tone: "bad"
            compact: true
            flat: true
            enabled: backend.pressState === ""
            onClicked: bar.confirming = true
        }
        PlainText {
            visible: bar.confirming
            text: "Discard this draft?"
            font.pixelSize: Theme.smallSize
            color: Theme.muted
        }
        QuietButton {
            objectName: "discardConfirm"
            visible: bar.confirming
            text: "Discard"
            tone: "bad"
            compact: true
            onClicked: {
                bar.confirming = false
                backend.discardDraft()
            }
        }
        QuietButton {
            objectName: "discardKeep"
            visible: bar.confirming
            text: "Keep"
            compact: true
            onClicked: bar.confirming = false
        }
    }

    PlainText {
        objectName: "sendNote"
        visible: text !== ""
        Layout.fillWidth: true
        // (not the sentence the tick above it already says)
        text: bar.live || backend.ticking ? "" : backend.sendNote
        wrapMode: Text.Wrap
        font.pixelSize: Theme.captionSize
        color: Theme.muted
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Every Bombadil.Style control in its states, on the window (Theme.bg) and a card
// (Theme.panel), plus open popups. Render it with:
//   bin/bombadil-app check tests/qml/style_gallery.qml --screenshot gallery.png
Item {
    id: root
    width: 1320
    height: 1480

    component Caption: Label {
        color: Theme.muted
        font.pixelSize: Theme.captionSize
        font.weight: Font.DemiBold
        Layout.topMargin: 4
    }

    component Showcase: ColumnLayout {
        id: sc
        property bool focusField: false
        spacing: 12

        Caption { text: "Buttons" }
        RowLayout {
            spacing: 8
            Button { text: "Default" }
            Button { text: "Save"; highlighted: true; icon.source: Theme.icon("check") }
            Button { text: "Flat"; flat: true }
            Button { text: "Delete"; danger: true; icon.source: Theme.icon("trash") }
            Button { text: "Delete"; danger: true; highlighted: true }
            Button { text: "Remove"; danger: true; flat: true }
        }
        RowLayout {
            spacing: 8
            Button { text: "New"; icon.source: Theme.icon("plus") }
            Button { text: "Pinned"; checkable: true; checked: true; icon.source: Theme.icon("star") }
            Button { icon.source: Theme.icon("copy") }
            Button { text: "Disabled"; enabled: false }
            Button { text: "Disabled"; highlighted: true; enabled: false }
            RoundButton { icon.source: Theme.icon("plus") }
            RoundButton { icon.source: Theme.icon("send"); highlighted: true }
        }
        RowLayout {
            spacing: 4
            ToolButton { icon.source: Theme.icon("arrow-left"); text: "Back" }
            ToolSeparator {}
            ToolButton { icon.source: Theme.icon("refresh") }
            ToolButton { icon.source: Theme.icon("star"); checkable: true; checked: true }
            ToolButton { icon.source: Theme.icon("settings") }
            ToolButton { icon.source: Theme.icon("trash"); enabled: false }
            ToolSeparator {}
            BusyIndicator { running: true }
            Item { Layout.fillWidth: true }
        }

        Caption { text: "Inputs" }
        RowLayout {
            spacing: 8
            TextField { placeholderText: "Search passwords"; Layout.fillWidth: true }
            TextField {
                text: "hello@bombadil.dev"
                focus: sc.focusField
                Layout.fillWidth: true
                // The menu below takes focus when it opens; hand it back so the focused look shows.
                Timer { interval: 300; running: sc.focusField; onTriggered: parent.forceActiveFocus() }
            }
            TextField { text: "Read only"; enabled: false; Layout.preferredWidth: 110 }
        }
        RowLayout {
            spacing: 8
            ComboBox { model: ["Last 5 minutes", "Last hour", "Today"]; Layout.fillWidth: true }
            ComboBox { editable: true; model: ["github.com", "gitlab.com"]; Layout.fillWidth: true }
            SpinBox { value: 12; to: 99 }
        }
        TextArea {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            text: "Notes wrap onto several lines. Selection and focus use the accent; the frame is the same as a TextField."
        }

        Caption { text: "Choices" }
        RowLayout {
            spacing: 16
            CheckBox { text: "Remember"; checked: true }
            CheckBox { text: "Sync" }
            CheckBox { text: "Some"; tristate: true; checkState: Qt.PartiallyChecked }
            CheckBox { text: "Off"; checked: true; enabled: false }
        }
        RowLayout {
            spacing: 16
            RadioButton { text: "Daily"; checked: true }
            RadioButton { text: "Weekly" }
            RadioButton { text: "Never"; enabled: false }
            Switch { text: "Wi-Fi"; checked: true }
            Switch { text: "Bluetooth" }
            Switch { enabled: false; checked: true }
        }

        Caption { text: "Ranges" }
        RowLayout {
            spacing: 16
            Slider { value: 0.4; Layout.fillWidth: true }
            RangeSlider { first.value: 0.2; second.value: 0.7; Layout.fillWidth: true }
        }
        RowLayout {
            spacing: 16
            ProgressBar { value: 0.65; Layout.fillWidth: true }
            ProgressBar { indeterminate: true; Layout.fillWidth: true }
            Slider { value: 0.7; enabled: false; Layout.preferredWidth: 120 }
        }

        Caption { text: "Tabs and lists" }
        TabBar {
            Layout.fillWidth: true
            currentIndex: 1
            TabButton { text: "Overview" }
            TabButton { text: "Processes" }
            TabButton { text: "Memory"; icon.source: Theme.icon("memory-stick") }
            TabButton { text: "Disabled"; enabled: false }
        }
        RowLayout {
            spacing: 12
            ColumnLayout {
                spacing: 2
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                ItemDelegate { text: "github.com"; icon.source: Theme.icon("globe"); Layout.fillWidth: true }
                ItemDelegate { text: "Bank"; icon.source: Theme.icon("key"); highlighted: true; Layout.fillWidth: true }
                ItemDelegate { text: "Archived"; icon.source: Theme.icon("archive"); enabled: false; Layout.fillWidth: true }
            }
            ColumnLayout {
                spacing: 2
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                CheckDelegate { text: "Show hidden files"; checked: true; Layout.fillWidth: true }
                SwitchDelegate { text: "Launch at login"; checked: true; Layout.fillWidth: true }
                RadioDelegate { text: "Compact rows"; Layout.fillWidth: true }
            }
        }

        Caption { text: "Containers" }
        RowLayout {
            spacing: 12
            Frame {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                Label { text: "Frame"; anchors.fill: parent }
            }
            GroupBox {
                title: "GroupBox"
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Label { text: "Content" }
            }
            Pane {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                Label { text: "Pane" }
            }
        }
        RowLayout {
            spacing: 12
            Layout.preferredHeight: 110
            Page {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                header: ToolBar {
                    RowLayout {
                        anchors.fill: parent
                        ToolButton { icon.source: Theme.icon("menu") }
                        Label { text: "Page with ToolBar"; font.weight: Font.DemiBold; Layout.fillWidth: true }
                        ToolButton { icon.source: Theme.icon("more-vertical") }
                    }
                }
                ScrollView {
                    anchors.fill: parent
                    anchors.topMargin: 6
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    Label {
                        width: 240
                        wrapMode: Text.Wrap
                        color: Theme.muted
                        text: "A ScrollView with its thin scroll bar forced on. Lines keep going so there is something to scroll through in this small area, and more, and more."
                    }
                }
            }
            SplitView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                Item {
                    SplitView.preferredWidth: 110
                    Label { anchors.centerIn: parent; text: "Split"; color: Theme.muted }
                }
                Item {
                    Label { anchors.centerIn: parent; text: "View"; color: Theme.muted }
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.bg
    }

    Label {
        id: heading
        x: 24
        y: 18
        text: "Bombadil.Style"
        font.pixelSize: Theme.titleSize
        font.weight: Font.Bold
    }
    Label {
        anchors.left: heading.right
        anchors.leftMargin: 12
        anchors.baseline: heading.baseline
        text: "left: Theme.bg   right: Theme.panel"
        color: Theme.muted
    }

    Showcase {
        id: onBg
        x: 24
        y: 64
        width: 612
        focusField: true
    }

    Rectangle {
        id: card
        x: 660
        y: 52
        width: 636
        height: onPanel.implicitHeight + 24
        radius: Theme.radius
        color: Theme.panel
        border.color: Theme.border

        Showcase {
            id: onPanel
            x: 12
            y: 12
            width: parent.width - 24
        }
    }

    // Popups, opened on start.
    Label {
        id: popupsCaption
        x: 24
        y: card.y + card.height + 20
        text: "Popups"
        color: Theme.muted
        font.pixelSize: Theme.captionSize
        font.weight: Font.DemiBold
    }

    Button {
        id: menuButton
        x: 24
        y: popupsCaption.y + 24
        text: "Actions"
        icon.source: Theme.icon("more-horizontal")
    }
    Menu {
        id: menu
        MenuItem { text: "Copy password"; icon.source: Theme.icon("copy") }
        MenuItem { text: "Edit"; icon.source: Theme.icon("pencil") }
        MenuItem { text: "Show in list"; checkable: true; checked: true }
        MenuItem { text: "Unavailable"; enabled: false }
        Menu { title: "Move to" }
        MenuSeparator {}
        MenuItem { text: "Delete"; icon.source: Theme.icon("trash"); danger: true }
    }

    ComboBox {
        id: openCombo
        x: 280
        y: menuButton.y
        width: 200
        currentIndex: 1
        model: ["Name", "Memory", "CPU", "PID"]
    }

    ToolButton {
        id: tipButton
        x: 530
        y: menuButton.y + 36
        icon.source: Theme.icon("copy")
        ToolTip.visible: true
        ToolTip.text: "Copy password"
    }

    Item {
        id: dialogSpot
        x: 660
        y: menuButton.y
        width: 360
        height: 180
    }
    Dialog {
        id: dialog
        parent: dialogSpot
        x: 0
        y: 0
        modal: false
        closePolicy: Popup.NoAutoClose
        title: "Delete entry?"
        standardButtons: Dialog.Cancel | Dialog.Ok
        Label {
            width: 320
            wrapMode: Text.Wrap
            color: Theme.muted
            text: "github.com will be removed from the vault. This cannot be undone."
        }
    }

    Component.onCompleted: {
        menu.popup(menuButton, 0, menuButton.height + 4)
        openCombo.popup.open()
        dialog.open()
    }
}

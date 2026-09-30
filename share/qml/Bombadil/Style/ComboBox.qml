pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// Drop-down: a raised field with a chevron; the list opens in an overlay popup below it
// with a check on the current item. `editable: true` makes the field a text input.
T.ComboBox {
    id: control

    // The shown text is elided, so size by the full text (unless a widest-text policy is set).
    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            (editable || implicitContentWidthPolicy !== T.ComboBox.ContentItemImplicitWidth
                             ? implicitContentWidth : Math.ceil(fullText.advanceWidth))
                            + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)

    // Text starts 12 px in; the chevron sits 10 px from the other edge, 8 px after the text.
    readonly property real chevronSpace: indicator && indicator.visible ? indicator.width + 18 : 12
    leftPadding: mirrored ? chevronSpace : 12
    rightPadding: mirrored ? 12 : chevronSpace
    opacity: enabled ? 1 : 0.4

    delegate: T.ItemDelegate {
        id: row

        required property int index

        width: ListView.view.width
        implicitHeight: 32
        leftPadding: 10
        rightPadding: 8
        text: control.textAt(index)
        highlighted: control.highlightedIndex === index
        hoverEnabled: control.hoverEnabled
        font: control.font

        contentItem: Item {
            implicitHeight: label.implicitHeight
            Text {
                id: label
                anchors.left: parent.left
                anchors.right: tick.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: row.text
                font: row.font
                color: Theme.fg
                elide: Text.ElideRight
            }
            IconImage {
                id: tick
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: 14
                height: 14
                sourceSize: Qt.size(14, 14)
                source: Theme.icon("check")
                color: Theme.accent
                visible: control.currentIndex === row.index
            }
        }

        background: Rectangle {
            radius: Theme.radiusSmall
            color: row.down ? Theme.alpha(Theme.fg, 0.12)
                 : row.highlighted ? Theme.alpha(Theme.fg, 0.07) : Theme.alpha(Theme.fg, 0)
        }
    }

    indicator: IconImage {
        x: control.mirrored ? 10 : control.width - width - 10
        y: (control.height - height) / 2
        width: 16
        height: 16
        sourceSize: Qt.size(16, 16)
        source: Theme.icon("chevron-down")
        color: control.hovered || control.popup.visible ? Theme.fg : Theme.muted
        rotation: control.popup.visible ? 180 : 0

        Behavior on rotation { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
    }

    contentItem: T.TextField {
        padding: 0
        text: control.editable ? control.editText : metrics.elidedText
        enabled: control.editable
        autoScroll: control.editable
        readOnly: control.down
        inputMethodHints: control.inputMethodHints
        validator: control.validator
        selectByMouse: control.selectTextByMouse

        font: control.font
        color: Theme.fg
        selectionColor: Theme.alpha(Theme.accent, 0.45)
        selectedTextColor: Theme.fg
        verticalAlignment: Text.AlignVCenter
        clip: true

        T.ContextMenu.menu: EditMenu { editor: parent }

        TextMetrics {
            id: metrics
            font: control.font
            text: control.displayText
            elide: Text.ElideRight
            elideWidth: control.availableWidth
        }
        TextMetrics {
            id: fullText
            font: control.font
            text: control.displayText
        }
    }

    background: Rectangle {
        implicitWidth: 160
        implicitHeight: Theme.controlHeight
        radius: Theme.radiusSmall
        color: control.editable ? Theme.raised
             : control.down ? Theme.panel : control.hovered ? Theme.overlay : Theme.raised
        border.width: control.editable && control.activeFocus ? 1.5 : 1
        border.color: control.editable && control.activeFocus ? Theme.accent
                    : control.editable ? (control.hovered ? Theme.borderStrong : Theme.border)
                    : Theme.borderStrong

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        FocusRing { visible: control.visualFocus && !control.editable }
    }

    popup: T.Popup {
        y: control.height + 4
        width: Math.max(control.width, 160)
        implicitHeight: contentItem.implicitHeight + topPadding + bottomPadding
        height: Math.min(implicitHeight, (T.Overlay.overlay ? T.Overlay.overlay.height : implicitHeight)
                                         - topMargin - bottomMargin)
        topMargin: 8
        bottomMargin: 8
        padding: 4
        font: control.font

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.delegateModel
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 0

            T.ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            radius: Theme.radius
            color: Theme.overlay
            border.color: Theme.borderStrong

            Shadow { radius: Theme.radius }
        }

        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
        }
    }
}

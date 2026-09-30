import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// Number field like TextField, with minus / plus buttons inside its ends.
T.SpinBox {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            contentItem.implicitWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             up.implicitIndicatorHeight, down.implicitIndicatorHeight)

    leftPadding: padding + (mirrored ? (up.indicator ? up.indicator.width : 0) : (down.indicator ? down.indicator.width : 0))
    rightPadding: padding + (mirrored ? (down.indicator ? down.indicator.width : 0) : (up.indicator ? up.indicator.width : 0))
    opacity: enabled ? 1 : 0.4

    validator: IntValidator {
        locale: control.locale.name
        bottom: Math.min(control.from, control.to)
        top: Math.max(control.from, control.to)
    }

    contentItem: TextInput {
        z: 2
        text: control.displayText
        clip: width < implicitWidth
        font: control.font
        color: Theme.fg
        selectionColor: Theme.alpha(Theme.accent, 0.45)
        selectedTextColor: Theme.fg
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Qt.AlignVCenter
        readOnly: !control.editable
        validator: control.validator
        inputMethodHints: control.inputMethodHints

        T.ContextMenu.menu: EditMenu { editor: parent }
    }

    up.indicator: Item {
        x: control.mirrored ? 0 : control.width - width
        height: control.height
        implicitWidth: 34
        implicitHeight: Theme.controlHeight

        Rectangle {
            anchors.fill: parent
            anchors.margins: 4
            radius: Theme.radiusSmall - 4
            color: control.up.pressed ? Theme.alpha(Theme.fg, 0.14)
                 : control.up.hovered ? Theme.alpha(Theme.fg, 0.07) : Theme.alpha(Theme.fg, 0)
        }
        IconImage {
            anchors.centerIn: parent
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: Theme.icon("plus")
            color: !parent.enabled ? Theme.faint : control.up.hovered ? Theme.fg : Theme.muted
        }
    }

    down.indicator: Item {
        x: control.mirrored ? control.width - width : 0
        height: control.height
        implicitWidth: 34
        implicitHeight: Theme.controlHeight

        Rectangle {
            anchors.fill: parent
            anchors.margins: 4
            radius: Theme.radiusSmall - 4
            color: control.down.pressed ? Theme.alpha(Theme.fg, 0.14)
                 : control.down.hovered ? Theme.alpha(Theme.fg, 0.07) : Theme.alpha(Theme.fg, 0)
        }
        IconImage {
            anchors.centerIn: parent
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: Theme.icon("minus")
            color: !parent.enabled ? Theme.faint : control.down.hovered ? Theme.fg : Theme.muted
        }
    }

    background: Rectangle {
        implicitWidth: 120
        implicitHeight: Theme.controlHeight
        radius: Theme.radiusSmall
        color: Theme.raised
        border.width: control.activeFocus ? 1.5 : 1
        border.color: control.activeFocus ? Theme.accent : control.hovered ? Theme.borderStrong : Theme.border

        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
}

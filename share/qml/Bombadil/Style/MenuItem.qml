import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// A 32 px menu row: optional icon, text, check mark when checkable, chevron for submenus.
// `danger: true` colors it red for destructive actions.
T.MenuItem {
    id: control

    property bool danger: false

    // Items without an icon or check still line up with their siblings' text.
    readonly property bool indent: {
        if (icon.source.toString() !== "" || checkable || !menu)
            return false
        for (let i = 0; i < menu.count; ++i) {
            const item = menu.itemAt(i)
            if (item && item !== control && (item.checkable || (item.icon && item.icon.source.toString() !== "")))
                return true
        }
        return false
    }

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)

    padding: 6
    horizontalPadding: 10
    spacing: 10
    opacity: enabled ? 1 : 0.4

    icon.width: 16
    icon.height: 16

    contentItem: IconLabel {
        readonly property real arrowPadding: control.subMenu && control.arrow ? control.arrow.width + control.spacing : 0
        readonly property real indicatorPadding: (control.checkable && control.indicator ? control.indicator.width + control.spacing : 0)
                                                 + (control.indent ? 16 + control.spacing : 0)

        leftPadding: !control.mirrored ? indicatorPadding : arrowPadding
        rightPadding: control.mirrored ? indicatorPadding : arrowPadding
        spacing: control.spacing
        mirrored: control.mirrored
        display: control.display
        alignment: Qt.AlignLeft
        icon: control.icon
        defaultIconColor: control.danger ? Theme.bad : control.highlighted ? Theme.fg : Theme.muted
        text: control.text
        font: control.font
        color: control.danger ? Theme.bad : Theme.fg
    }

    indicator: Item {
        x: control.mirrored ? control.width - width - control.rightPadding : control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        implicitWidth: 16
        implicitHeight: 16
        visible: control.checkable

        IconImage {
            anchors.centerIn: parent
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: Theme.icon("check")
            color: Theme.accent
            visible: control.checked
        }
    }

    arrow: IconImage {
        x: control.mirrored ? control.leftPadding : control.width - width - control.rightPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        width: 14
        height: 14
        sourceSize: Qt.size(14, 14)
        visible: control.subMenu
        mirror: control.mirrored
        source: control.subMenu ? Theme.icon("chevron-right") : ""
        color: Theme.muted
    }

    background: Rectangle {
        implicitWidth: 180
        implicitHeight: 32
        radius: Theme.radiusSmall
        color: {
            const ink = control.danger ? Theme.bad : Theme.fg
            if (control.down)
                return Theme.alpha(ink, control.danger ? 0.2 : 0.12)
            if (control.highlighted)
                return Theme.alpha(ink, control.danger ? 0.13 : 0.07)
            return Theme.alpha(ink, 0)
        }
    }
}

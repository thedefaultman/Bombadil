import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// Push button. Default: a raised neutral surface. `highlighted`: the accent primary action.
// `flat`: no surface until hovered. `danger`: destructive (filled red when also highlighted).
T.Button {
    id: control

    property bool danger: false

    readonly property color ink: highlighted ? Theme.accentFg : danger ? Theme.bad : Theme.fg
    readonly property color fill: {
        if (highlighted) {
            const base = danger ? Theme.bad : Theme.accent
            if (down)
                return danger ? Qt.darker(base, 1.12) : Theme.accentPressed
            if (hovered)
                return danger ? Qt.lighter(base, 1.1) : Theme.accentHover
            return base
        }
        if (danger)
            return Theme.alpha(Theme.bad, down ? 0.22 : hovered ? 0.14 : flat ? 0 : 0.06)
        if (checked)
            return Theme.accentSoft
        if (flat)
            return down ? Theme.overlay : hovered ? Theme.raised : Theme.alpha(Theme.raised, 0)
        return down ? Theme.panel : hovered ? Theme.overlay : Theme.raised
    }
    readonly property color edge: {
        if (highlighted)
            return fill
        if (danger)
            return flat ? Theme.alpha(Theme.bad, 0) : Theme.alpha(Theme.bad, hovered ? 0.6 : 0.4)
        if (checked)
            return Theme.alpha(Theme.accent, 0.6)
        if (flat)
            return Theme.alpha(Theme.borderStrong, 0)
        return Theme.borderStrong
    }

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 8
    horizontalPadding: text && display !== T.AbstractButton.IconOnly ? 14 : 10
    spacing: 8
    font.weight: Font.Medium
    opacity: enabled ? 1 : 0.4

    icon.width: 16
    icon.height: 16

    contentItem: IconLabel {
        spacing: control.spacing
        mirrored: control.mirrored
        display: control.display
        icon: control.icon
        defaultIconColor: control.ink
        text: control.text
        font: control.font
        color: control.ink
    }

    background: Rectangle {
        implicitWidth: control.text && control.display !== T.AbstractButton.IconOnly ? 64 : Theme.controlHeight
        implicitHeight: Theme.controlHeight
        radius: Theme.radiusSmall
        color: control.fill
        border.width: 1
        border.color: control.edge

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        FocusRing { visible: control.visualFocus }
    }
}

import QtQuick
import QtQuick.Templates as T
import Bombadil

// Outlined group with a small muted title inside its top edge.
T.GroupBox {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding,
                            implicitLabelWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    spacing: 10
    padding: Theme.pad
    topPadding: padding + (implicitLabelWidth > 0 ? implicitLabelHeight + spacing : 0)
    opacity: enabled ? 1 : 0.4

    label: Label {
        x: control.leftPadding
        y: control.padding
        width: control.availableWidth
        text: control.title
        color: Theme.muted
        font.pixelSize: Theme.captionSize
        font.weight: Font.DemiBold
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        radius: Theme.radius
        color: "transparent"
        border.color: Theme.border
    }
}

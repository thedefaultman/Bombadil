import QtQuick
import QtQuick.Templates as T
import Bombadil

// Modal card centered in the window over a dimmed backdrop: title, content, and a
// right-aligned DialogButtonBox (the accept button is the accent one).
T.Dialog {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding,
                            implicitHeaderWidth,
                            implicitFooterWidth)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding
                             + (implicitHeaderHeight > 0 ? implicitHeaderHeight + spacing : 0)
                             + (implicitFooterHeight > 0 ? implicitFooterHeight + spacing : 0))

    parent: T.Overlay.overlay
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    // Never taller than the window; the content is clipped (put a long form in a ScrollPane).
    height: parent ? Math.min(implicitHeight, parent.height - 2 * Theme.pad) : implicitHeight
    Component.onCompleted: if (contentItem) contentItem.clip = true
    modal: true
    margins: Theme.pad
    padding: 20
    topPadding: title ? 10 : 20

    background: Rectangle {
        implicitWidth: 360
        radius: Theme.radius
        color: Theme.panel
        border.color: Theme.border

        Shadow { radius: Theme.radius }
    }

    header: Label {
        text: control.title
        visible: control.title !== ""
        font.pixelSize: Theme.headingSize
        font.weight: Font.DemiBold
        leftPadding: 20
        rightPadding: 20
        topPadding: 18
    }

    footer: DialogButtonBox {
        visible: count > 0
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
        NumberAnimation { property: "scale"; from: 1; to: 0.98; duration: Theme.fast }
    }

    T.Overlay.modal: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.6)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }
    T.Overlay.modeless: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.25)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }
}

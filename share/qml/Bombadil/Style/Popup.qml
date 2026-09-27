import QtQuick
import QtQuick.Templates as T
import Bombadil

// Generic floating surface: overlay color, 12 px radius, soft shadow, fades in.
T.Popup {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: Theme.gap

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

    T.Overlay.modal: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.6)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }
    T.Overlay.modeless: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.25)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }
}

import QtQuick
import QtQuick.Templates as T
import Bombadil

// Context / drop-down menu: overlay surface with a soft shadow, 32 px rows.
T.Menu {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    margins: 8
    padding: 4
    overlap: 4

    delegate: MenuItem {}

    contentItem: ListView {
        implicitHeight: contentHeight
        model: control.contentModel
        interactive: contentHeight + control.topPadding + control.bottomPadding > control.height
        clip: true
        currentIndex: control.currentIndex

        ScrollIndicator.vertical: ScrollIndicator {}
    }

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: 40
        radius: Theme.radius
        color: Theme.overlay
        border.color: Theme.borderStrong

        Shadow { radius: Theme.radius }
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast }
        NumberAnimation { property: "scale"; from: 0.97; to: 1; duration: Theme.fast; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
    }

    T.Overlay.modal: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.5)
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    }
    T.Overlay.modeless: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.2)
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    }
}

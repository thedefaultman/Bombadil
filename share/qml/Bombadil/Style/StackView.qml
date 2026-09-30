import QtQuick
import QtQuick.Templates as T
import Bombadil

// Page stack: pushed pages fade in while sliding 24 px from the right; pop reverses it.
T.StackView {
    id: control

    readonly property real shift: (mirrored ? -1 : 1) * 24

    pushEnter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
        NumberAnimation { property: "x"; from: control.shift; to: 0; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
    pushExit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
        NumberAnimation { property: "x"; from: 0; to: -control.shift; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
    popEnter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
        NumberAnimation { property: "x"; from: -control.shift; to: 0; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
    popExit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
        NumberAnimation { property: "x"; from: 0; to: control.shift; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
    replaceEnter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
        NumberAnimation { property: "x"; from: control.shift; to: 0; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
    replaceExit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
        NumberAnimation { property: "x"; from: 0; to: -control.shift; duration: Theme.normal; easing.type: Easing.OutCubic }
    }
}

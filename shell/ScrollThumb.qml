import QtQuick
import Bombadil as Kit

// A thin mark at the right edge of a Flickable that has more than it shows: where you are in it. It is drawn beside
// the Flickable (a sibling in the same parent), takes no clicks, and is not there when everything fits.
Rectangle {
    id: thumb
    required property Flickable flick
    readonly property real room: flick.height - 2 * margin
    readonly property real span: Math.max(1, flick.contentHeight - flick.height)
    readonly property int margin: Kit.Theme.gapSmall / 2

    visible: flick.contentHeight > flick.height + 1
    width: Kit.Theme.focusRing
    height: Math.max(Kit.Theme.pad, room * flick.height / Math.max(1, flick.contentHeight))
    radius: width / 2
    color: Kit.Theme.borderStrong
    x: flick.x + flick.width - width - margin
    y: flick.y + margin + (room - height) * Math.min(1, Math.max(0, flick.contentY / span))
}

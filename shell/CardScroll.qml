import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// What a face puts rows or boxes in: a column as tall as its contents up to `maxHeight`, and a scroll past that,
// with a thin mark that says there is more. A face's own scroll rather than the host's, so what must stay in view
// (the reply box and its button) is not inside what scrolls.
Item {
    id: scroll
    default property alias content: inner.data
    property int maxHeight: 400
    property int spacing: Kit.Theme.gapSmall
    property alias flick: flick

    // Scrolled to the end when it first fills (a thread starts at its newest message).
    function toEnd() { flick.contentY = Math.max(0, flick.contentHeight - flick.height) }

    implicitHeight: Math.min(inner.implicitHeight, maxHeight)

    Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: inner.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        clip: true

        ColumnLayout {
            id: inner
            width: flick.width
            spacing: scroll.spacing
        }
    }
    ScrollThumb { flick: flick }
}

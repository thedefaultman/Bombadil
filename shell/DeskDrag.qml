import QtQuick

// What a card's grip and a strip share: a press and drag with the left button that tells DeskState
// where the pointer is, in screen coordinates, until it lets go. A handler only knows its own
// window, and a layer-shell window cannot ask where it sits, so the window's top-left comes in as
// `origin`. The grab goes on delivering positions beyond the window's edge, which is how a drag from
// a rail ends over the bar; DeskState says what lies there, from the desk's own geometry.
// A tap, or a slide shorter than the drag threshold, is not a drag: it stays whatever the item does
// with a tap.
DragHandler {
    id: grab
    required property var desk       // a DeskState
    property string widget: ""       // read when the drag starts: a strip is built by place, not by id
    property string from: "rail"     // "rail" for a card's grip, "strip" for a chip
    property point origin: Qt.point(0, 0)   // the window's top-left on the screen

    // The widget this handler picked up, while DeskState still has it in hand. A drag can end from
    // inside (the card folded, the socket went) with the button still down; then this is not ours.
    property string held: ""
    readonly property bool mine: held !== "" && desk.dragging && desk.drag.id === held

    target: null
    acceptedButtons: Qt.LeftButton

    function _screen(p) { return Qt.point(p.x + origin.x, p.y + origin.y) }

    // The threshold is crossed: the drag starts where the pointer is now.
    onActiveChanged: {
        if (!active || mine) return     // a second button can drop the handler out and back in: it is the same drag
        const at = _screen(centroid.scenePosition)
        held = desk.dragStart(widget, from, at.x, at.y) ? widget : ""
    }
    onCentroidChanged: {
        if (!active || !mine) return
        const at = _screen(centroid.scenePosition)
        desk.dragMove(at.x, at.y)
    }
    // A release drops it where the pointer is. Anything else that ends the grab (the window losing
    // focus, a handler taking it over) lets go of it the same way a release does, but the pointer has
    // not been lifted: that is no drop, only the end of the drag.
    onGrabChanged: (transition, point) => {
        if (transition !== PointerDevice.UngrabExclusive && transition !== PointerDevice.CancelGrabExclusive) return
        if (mine) {
            const at = _screen(point.scenePosition)
            if (transition === PointerDevice.UngrabExclusive && point.state === EventPoint.Released) desk.dragEnd(at.x, at.y)
            else desk.dragCancel()
        }
        held = ""
    }

    // A handle that goes away mid-drag takes the rest of the stream with it.
    Component.onDestruction: if (mine) desk.dragCancel()
}

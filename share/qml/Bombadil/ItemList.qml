import QtQuick
import QtQuick.Controls
import QtQuick.Templates as T

// A selectable, keyboard-navigable list of ListRows. An array of objects is the easiest
// model; when a new array is assigned the selection follows the same item (matched by
// `id`/`uuid`/`key`/`pid`, else by content) and the scroll position is kept.
FocusScope {
    id: root

    property var model
    property string titleRole: "title"
    property string subtitleRole: "subtitle"
    property string iconRole: ""
    property string trailingRole: ""
    property alias currentIndex: view.currentIndex
    readonly property var current: _itemAt(view.currentIndex)
    readonly property alias count: view.count
    property string emptyText: "Nothing here yet"
    property Component delegate: defaultRow

    signal activated(int index, var item)
    signal contextRequested(int index, var item)

    implicitWidth: 240
    // Up to 8 rows tall on its own; give it Layout.fillHeight to take the free space.
    implicitHeight: view.count > 0 ? Math.min(view.contentHeight, 8 * (Theme.rowHeight + view.spacing)) : 160

    function _itemAt(i) {
        const m = root.model
        if (i === undefined || i < 0 || m === undefined || m === null)
            return null
        if (_isList(m))
            return i < m.length ? m[i] : null
        if (typeof m.get === "function")
            return i < m.count ? m.get(i) : null
        return null
    }

    // A JS array or anything array-like (a list property from Python), as opposed to a model.
    function _isList(m) {
        return Array.isArray(m) || (!!m && typeof m === "object" && typeof m.length === "number"
                                    && typeof m.get !== "function")
    }

    function _key(item) {
        if (item === null || item === undefined)
            return undefined
        if (typeof item !== "object")
            return String(item)
        for (const k of ["id", "uuid", "key", "pid"])
            if (item[k] !== undefined)
                return k + ":" + item[k]
        return JSON.stringify(item)
    }

    function _hasId(item) {
        return !!item && typeof item === "object"
            && ["id", "uuid", "key", "pid"].some(k => item[k] !== undefined)
    }

    // A new array would reset the view to the top and the first row; keep both instead.
    function _sync() {
        const m = root.model
        if (!_isList(m)) {
            view.model = m
            return
        }
        const prevIndex = view.currentIndex
        const prevKey = _key(_selected)
        const prevHasId = _hasId(_selected)
        const prevCount = view.count
        const y = view.contentY
        view.model = m
        let index = prevIndex < m.length ? prevIndex : -1
        if (prevKey !== undefined) {
            let found = -1
            for (let i = 0; i < m.length && found < 0; i++)
                if (_key(m[i]) === prevKey)
                    found = i
            // Only an item known by its content keeps its row when it is gone: it was
            // probably edited in place. One with an id that is gone is deselected.
            if (found >= 0 || prevHasId || m.length !== prevCount)
                index = found
        }
        if (view.currentIndex !== index)
            view.currentIndex = index
        _selected = _itemAt(index)
        view.contentY = Math.max(0, Math.min(y, view.contentHeight - view.height))
    }
    property var _selected: null
    // Delegates whose clicked() already selects their row.
    property var _hooked: []
    // The row a press selected and the selection it replaced, for a press that turns into a scroll.
    property var _pressed: null
    // Set while a press selects or gives back a row: the list must not scroll under the finger.
    property bool _holding: false
    onModelChanged: _sync()
    Component.onCompleted: _sync()

    Component {
        id: defaultRow
        ListRow {
            id: row
            required property int index
            required property var model
            readonly property var entry: model.modelData !== undefined ? model.modelData : model
            readonly property bool plain: entry === null || typeof entry !== "object"
            width: ListView.view.width
            selected: ListView.isCurrentItem
            title: plain ? String(entry) : root._show(entry[root.titleRole])
            subtitle: plain || !root.subtitleRole ? "" : root._show(entry[root.subtitleRole])
            icon: plain || !root.iconRole ? "" : root._show(entry[root.iconRole])
            trailing: plain || !root.trailingRole ? "" : root._show(entry[root.trailingRole])
        }
    }

    function _show(v) {
        return v === undefined || v === null ? "" : String(v)
    }

    ListView {
        id: view
        anchors.fill: parent
        focus: true
        clip: true
        spacing: 2
        currentIndex: -1
        reuseItems: true
        boundsBehavior: Flickable.StopAtBounds
        acceptedButtons: Qt.NoButton
        keyNavigationEnabled: true
        highlightFollowsCurrentItem: false
        delegate: root.delegate

        ScrollBar.vertical: ScrollBar {}

        onCurrentIndexChanged: {
            root._selected = root._itemAt(currentIndex)
            if (currentIndex >= 0 && !root._holding)
                positionViewAtIndex(currentIndex, ListView.Contain)
        }

        // A button row the finger left before the drag began sends no canceled(), so the drag does.
        onDragStarted: root._cancelPress()

        // A press selects the row under it, a tap activates it.
        TapHandler {
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onPressedChanged: if (pressed) root._press(view.indexAt(point.position.x, point.position.y))
            onCanceled: root._cancelPress()
            onTapped: (point, button) => root._tapped(button, tapCount)
        }

        // A delegate that is a button (ItemDelegate, CheckDelegate) takes the press, so the
        // TapHandler never sees it: its own pressed and clicked() select and activate the row.
        Connections {
            target: view.contentItem
            function onChildrenChanged() {
                // Reused rows stay children; destroyed ones leave, and leave the list.
                const hooked = []
                for (const c of view.contentItem.children) {
                    if (!(c instanceof T.AbstractButton))
                        continue
                    if (root._hooked.indexOf(c) < 0) {
                        c.pressedChanged.connect(() => {
                            if (c.pressed)
                                root._press(view.indexAt(c.x + c.width / 2, c.y + c.height / 2))
                        })
                        c.canceled.connect(() => root._cancelPress())
                        c.clicked.connect(() => {
                            view.positionViewAtIndex(view.currentIndex, ListView.Contain)
                            root._activateCurrent()
                        })
                    }
                    hooked.push(c)
                }
                root._hooked = hooked
            }
        }

        Keys.onReturnPressed: root._activateCurrent()
        Keys.onEnterPressed: root._activateCurrent()
        Keys.onPressed: event => {
            if (event.key === Qt.Key_Home && count > 0) {
                currentIndex = 0
                event.accepted = true
            } else if (event.key === Qt.Key_End && count > 0) {
                currentIndex = count - 1
                event.accepted = true
            }
        }
    }

    // Selected on press: the row's own onClicked runs first and may move or remove its item,
    // and the selection follows the item, not the row. A press that turns into a touch scroll
    // gives the selection back.
    function _press(i) {
        view.forceActiveFocus()
        _pressed = i >= 0 ? { row: i, before: view.currentIndex } : null
        if (i >= 0)
            _setCurrent(i)
    }

    function _setCurrent(i) {
        _holding = true
        view.currentIndex = i
        _holding = false
    }

    function _cancelPress() {
        const p = _pressed
        _pressed = null
        if (p && view.currentIndex === p.row)
            _setCurrent(p.before < view.count ? p.before : -1)
    }

    // A double-click activates on its first click only.
    function _tapped(button, count) {
        const pressed = _pressed !== null
        _pressed = null
        const i = view.currentIndex
        if (!pressed || i < 0)
            return
        view.positionViewAtIndex(i, ListView.Contain)
        if (button === Qt.RightButton)
            contextRequested(i, _itemAt(i))
        else if (count === 1)
            activated(i, _itemAt(i))
    }

    function _activateCurrent() {
        if (view.currentIndex >= 0)
            activated(view.currentIndex, _itemAt(view.currentIndex))
    }

    EmptyState {
        anchors.fill: parent
        visible: view.count === 0 && root.emptyText !== ""
        text: root.emptyText
    }
}

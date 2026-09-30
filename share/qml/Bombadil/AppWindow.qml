import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window as QW

// The root of every app. The runtime owns the native window and places this item in it,
// so a hot reload swaps the content without re-creating the window. AppWindow draws the
// OS frame: title row, palette and font for every control inside, toasts, the reload
// error banner, and the standard keys (Esc hides the app, Ctrl+W closes it).
// A FocusScope, so a child with `focus: true` gets the keyboard when the window opens.
FocusScope {
    id: root

    property string title: App.title
    property string subtitle: ""
    property string icon: ""
    property alias actions: actionRow.data
    property int padding: Theme.pad
    property int spacing: Theme.gap
    property bool showHeader: true
    default property alias content: body.data

    // Set by first-milestone apps, from when AppWindow was an ApplicationWindow. The bars
    // are placed above and below the content; the size limits and closing are not used.
    property color color: Theme.bg
    property font font: Theme.font
    property Item menuBar: null
    property Item header: null
    property Item footer: null
    property int minimumWidth: 0
    property int minimumHeight: 0
    property int maximumWidth: 16777215
    property int maximumHeight: 16777215
    signal closing(var close)

    width: 560
    height: 680

    // Shows a short message at the bottom for 2.5 s. tone: "", "good", "warn", "bad", "info", "accent".
    function toast(text, tone) {
        toastText.text = text
        toastBox.tone = tone || ""
        toastBox.shown = true
        toastTimer.restart()
    }

    // A child takes the free height when it has Layout.fillHeight (and no maximum). Nested
    // layouts default to fillHeight, so they count only when something inside them does.
    function _grows(item) {
        if (!item.visible || !item.Layout.fillHeight || item.Layout.maximumHeight < 1e30)   // "no maximum" reads as FLT_MAX or Infinity
            return false
        if (!(item instanceof ColumnLayout || item instanceof RowLayout || item instanceof GridLayout))
            return true
        for (const c of item.children)
            if (_grows(c))
                return true
        return false
    }

    // Qt's palette roles in OS colors, for any control that reads the palette.
    readonly property var _paletteRoles: ({
        window: Theme.bg, windowText: Theme.fg, base: Theme.raised, alternateBase: Theme.panel,
        text: Theme.fg, button: Theme.raised, buttonText: Theme.fg, brightText: Theme.fg,
        highlight: Theme.accent, highlightedText: Theme.accentFg, placeholderText: Theme.faint,
        toolTipBase: Theme.overlay, toolTipText: Theme.fg, light: Theme.borderStrong,
        midlight: Theme.border, mid: Theme.borderStrong, dark: Theme.muted, shadow: Theme.sunken,
        link: Theme.accent, linkVisited: Theme.accent, accent: Theme.accent
    })
    readonly property var _disabledRoles: ({
        windowText: Theme.faint, text: Theme.faint, buttonText: Theme.faint,
        highlight: Theme.borderStrong, highlightedText: Theme.muted
    })

    function _applyPalette(p) {
        for (const role in _paletteRoles)
            p[role] = _paletteRoles[role]
        for (const role in _disabledRoles)
            p.disabled[role] = _disabledRoles[role]
    }

    // The window gets the palette too, for popups. They take the window's palette when they
    // first see the window and never again, so the first time it is set the content is
    // re-attached to pick it up. (Later hot reloads find it already set.)
    readonly property var _hostWindow: QW.Window.window
    on_HostWindowChanged: {
        if (!_hostWindow)
            return
        const fresh = !Qt.colorEqual(_hostWindow.palette.window, Theme.bg)
        _applyPalette(_hostWindow.palette)
        if (fresh) {
            const p = frame.contentItem.parent
            frame.contentItem.parent = null
            frame.contentItem.parent = p
        }
    }

    Component.onCompleted: _applyPalette(frame.palette)

    // Puts a first-milestone bar in its slot, in place of the one before it.
    function _dock(item, slot) {
        for (const c of Array.from(slot.children))
            if (c !== item)
                c.parent = null
        if (item) {
            item.parent = slot
            item.width = Qt.binding(() => slot.width)
        }
    }
    onMenuBarChanged: _dock(menuBar, menuSlot)
    onHeaderChanged: _dock(header, headerSlot)
    onFooterChanged: _dock(footer, footerSlot)

    Rectangle {
        anchors.fill: parent
        color: root.color
    }

    Control {
        id: frame
        anchors.fill: parent
        font: root.font
        padding: 0

        contentItem: Item {
            Item {
                id: menuSlot
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: root.menuBar && root.menuBar.visible ? root.menuBar.height : 0
            }
            Item {
                id: headerSlot
                anchors { left: parent.left; right: parent.right; top: menuSlot.bottom }
                height: root.header && root.header.visible ? root.header.height : 0
            }
            Item {
                id: footerSlot
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                height: root.footer && root.footer.visible ? root.footer.height : 0
            }

            RowLayout {
                id: titleRow
                visible: root.showHeader
                anchors { left: parent.left; right: parent.right; top: headerSlot.bottom; margins: Theme.pad }
                height: Theme.controlHeight
                spacing: Theme.gap

                Rectangle {
                    visible: root.icon !== ""
                    implicitWidth: 28
                    implicitHeight: 28
                    radius: Theme.radiusSmall
                    color: Theme.accentSoft
                    Icon {
                        anchors.centerIn: parent
                        name: root.icon
                        size: 16
                        color: Theme.accent
                    }
                }
                Text {
                    text: root.title
                    color: Theme.fg
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.headingSize
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Layout.maximumWidth: titleRow.width / 2
                }
                Text {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignBaseline
                    text: root.subtitle
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.textSize
                    elide: Text.ElideRight
                }
                RowLayout {
                    id: actionRow
                    spacing: Theme.gapSmall
                }
            }

            Item {
                id: area
                anchors {
                    left: parent.left; right: parent.right; bottom: footerSlot.top
                    top: root.showHeader ? titleRow.bottom : headerSlot.bottom
                    leftMargin: root.padding; rightMargin: root.padding; bottomMargin: root.padding
                    topMargin: root.showHeader ? Theme.gap : root.padding
                }

                ColumnLayout {
                    id: body
                    // Packed at the top unless a child asks for the free height.
                    readonly property bool fills: {
                        for (const c of children)
                            if (root._grows(c))
                                return true
                        return false
                    }
                    width: parent.width
                    height: fills ? parent.height : implicitHeight
                    spacing: root.spacing
                }
            }
        }
    }

    // The last hot reload failed: the previous UI stays up and this says why.
    Rectangle {
        id: banner

        property bool expanded: false
        readonly property string error: App.lastError

        visible: error !== ""
        onErrorChanged: expanded = false
        z: 20
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.gapSmall }
        height: expanded ? Math.min(details.implicitHeight + 52, root.height * 0.6) : 40
        radius: Theme.radiusSmall
        color: Theme.panel
        border.color: Theme.alpha(Theme.bad, 0.7)
        clip: true

        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: Theme.alpha(Theme.bad, 0.14)
        }

        MouseArea {
            id: bannerMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: banner.expanded = !banner.expanded
        }

        RowLayout {
            id: bannerRow
            anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 12; rightMargin: 12 }
            height: 40
            spacing: 10
            Icon { name: "alert-triangle"; size: 16; color: Theme.bad }
            Text {
                text: "Reload failed"
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.textSize
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                visible: !banner.expanded
                text: banner.error.split("\n")[0]
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.textSize
                elide: Text.ElideRight
            }
            Item { Layout.fillWidth: true; visible: banner.expanded }
            Icon { name: banner.expanded ? "chevron-up" : "chevron-down"; size: 16; color: Theme.muted }
        }

        Flickable {
            visible: banner.expanded
            anchors { left: parent.left; right: parent.right; top: bannerRow.bottom; bottom: parent.bottom
                      leftMargin: 12; rightMargin: 12; bottomMargin: 12 }
            contentHeight: details.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            Mono {
                id: details
                width: parent.width
                text: banner.error
            }
        }

        ToolTip.visible: bannerMouse.containsMouse && !banner.expanded
        ToolTip.text: banner.error
        ToolTip.delay: 500
    }

    Rectangle {
        id: toastBox

        property bool shown: false
        property string tone: ""

        z: 30
        visible: opacity > 0
        opacity: shown ? 1 : 0
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height - height - Theme.pad - (shown ? 8 : 0)
        width: Math.min(toastRow.implicitWidth + 32, parent.width - 2 * Theme.pad)
        height: 36
        radius: height / 2
        color: Theme.overlay
        border.color: Theme.borderStrong

        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        Behavior on y { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }

        RowLayout {
            id: toastRow
            anchors.centerIn: parent
            width: Math.min(implicitWidth, toastBox.width - 32)
            spacing: 8
            Icon {
                visible: toastBox.tone !== ""
                name: toastBox.tone === "good" ? "check"
                    : toastBox.tone === "bad" || toastBox.tone === "warn" ? "alert-triangle" : "info"
                size: 16
                color: Theme.tone(toastBox.tone)
            }
            Text {
                id: toastText
                Layout.fillWidth: true
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.textSize
                elide: Text.ElideRight
            }
        }

        Timer {
            id: toastTimer
            interval: 2500
            onTriggered: toastBox.shown = false
        }
    }

    Shortcut {
        sequence: "Esc"
        context: Qt.WindowShortcut
        onActivated: App.hide()
    }
    Shortcut {
        sequence: "Ctrl+W"
        context: Qt.WindowShortcut
        onActivated: App.close()
    }
}

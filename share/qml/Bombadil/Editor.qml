import QtQuick
import QtQuick.Controls

// A code/text editor: mono font, line numbers, syntax colors. With `path` it edits that
// file: it loads it, marks unsaved changes with a dot, and Ctrl+S saves.
FocusScope {
    id: root

    property alias text: area.text
    property string path: ""
    property string language: _guessLanguage(path)
    property alias readOnly: area.readOnly
    property bool lineNumbers: true
    property bool wrap: false
    readonly property bool dirty: path !== "" && area.text !== _saved

    signal saved()

    property string _saved: ""
    property string _loadedPath: ""

    implicitWidth: 320
    implicitHeight: 240

    // Writes the file (when `path` is set) and emits saved().
    function save() {
        if (path !== "") {
            if (!file.save(area.text))
                return false
            _saved = area.text
        }
        saved()
        return true
    }

    // Throws away unsaved edits and reads the file again.
    function reload() {
        if (path === "")
            return
        file.reload()
        _saved = file.text
        if (area.text !== file.text)
            area.text = file.text
    }

    function _guessLanguage(p) {
        const name = String(p).toLowerCase().split("/").pop()
        const ext = name.includes(".") ? name.split(".").pop() : ""
        const map = {
            md: "markdown", markdown: "markdown", py: "python", pyw: "python", json: "json",
            qml: "qml", js: "javascript", mjs: "javascript", cjs: "javascript", ts: "javascript",
            sh: "shell", bash: "shell", zsh: "shell", toml: "toml", ini: "ini", conf: "ini",
            cfg: "ini", desktop: "ini", service: "ini"
        }
        if (map[ext])
            return map[ext]
        return name === ".bashrc" || name === ".zshrc" || name === ".profile" ? "shell" : "plain"
    }

    TextFile {
        id: file
        path: root.path
        // Loaded, or changed on disk: take it unless the user has unsaved edits to this file.
        onTextChanged: {
            const edited = root.dirty && root._loadedPath === path
            root._saved = text
            root._loadedPath = path
            if (!edited && area.text !== text)
                area.text = text
        }
    }

    Highlighter {
        textDocument: area.textDocument
        language: root.language
    }

    Shortcut {
        sequence: "Ctrl+S"
        context: Qt.WindowShortcut
        enabled: root.path !== "" && !root.readOnly
        onActivated: root.save()
    }

    FontMetrics {
        id: metrics
        font: Theme.monoFont
    }

    // Start offset of every line, for the gutter.
    readonly property var _lineStarts: {
        const t = area.text
        const starts = [0]
        for (let i = t.indexOf("\n"); i >= 0; i = t.indexOf("\n", i + 1))
            starts.push(i + 1)
        return starts
    }

    function _lineAt(pos) {
        const starts = _lineStarts
        let lo = 0, hi = starts.length - 1
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1
            if (starts[mid] <= pos)
                lo = mid
            else
                hi = mid - 1
        }
        return lo
    }

    // The line numbers that are on screen and where they go (wrapped lines take more room).
    readonly property var _visibleLines: {
        if (!lineNumbers)
            return []
        const starts = _lineStarts
        const top = flick.contentY, bottom = top + flick.height
        // The document fills in after the text (at creation); only ask for laid-out positions.
        const length = area.length
        void area.contentHeight
        void area.width
        let lo = 0, hi = starts.length - 1
        while (hi > 0 && starts[hi] > length)
            hi--
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1
            if (area.positionToRectangle(starts[mid]).y <= top)
                lo = mid
            else
                hi = mid - 1
        }
        const out = []
        for (let i = lo; i < starts.length && starts[i] <= length; i++) {
            const r = area.positionToRectangle(starts[i])
            if (r.y > bottom)
                break
            out.push({ n: i + 1, y: r.y - top, h: r.height })
        }
        return out
    }
    readonly property int _cursorLine: _lineAt(area.cursorPosition)

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: Theme.sunken
        border.color: area.activeFocus ? Theme.borderStrong : Theme.border
    }

    Item {
        id: gutter
        visible: root.lineNumbers
        x: 1
        y: 1
        width: visible ? Math.max(3, String(root._lineStarts.length).length) * metrics.advanceWidth("0") + 24 : 0
        height: parent.height - 2
        clip: true

        Repeater {
            model: root._visibleLines.length
            delegate: Text {
                required property int index
                readonly property var line: root._visibleLines[index] || { n: 0, y: 0, h: 0 }
                x: 0
                y: flick.y - gutter.y + line.y
                width: gutter.width - 12
                height: line.h
                horizontalAlignment: Text.AlignRight
                text: line.n
                color: line.n === root._cursorLine + 1 && area.activeFocus ? Theme.muted : Theme.faint
                font: Theme.monoFont
            }
        }
    }

    Flickable {
        id: flick
        anchors { fill: parent; margins: 1; leftMargin: gutter.width + 1 }
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        acceptedButtons: Qt.NoButton

        ScrollBar.vertical: ScrollBar {}
        ScrollBar.horizontal: ScrollBar {}

        TextArea.flickable: TextArea {
            id: area
            focus: true
            font: Theme.monoFont
            color: Theme.fg
            selectionColor: Theme.alpha(Theme.accent, 0.35)
            selectedTextColor: Theme.fg
            placeholderTextColor: Theme.faint
            textFormat: TextEdit.PlainText
            wrapMode: root.wrap ? TextEdit.Wrap : TextEdit.NoWrap
            tabStopDistance: metrics.advanceWidth(" ") * 4
            selectByMouse: true
            persistentSelection: true
            padding: 12
            leftPadding: root.lineNumbers ? 4 : 12
            background: null

            // The cursor's line, faintly lit.
            Rectangle {
                z: -1
                visible: area.activeFocus && !area.readOnly && area.selectedText === ""
                x: 0
                y: area.cursorRectangle.y
                width: Math.max(area.width, flick.width)
                height: area.cursorRectangle.height
                color: Theme.alpha(Theme.raised, 0.6)
            }
        }
    }

    Rectangle {
        visible: root.dirty
        anchors { top: parent.top; right: parent.right; margins: 10 }
        width: 8
        height: 8
        radius: 4
        color: Theme.accent
        HoverHandler { id: dirtyHover }
        ToolTip.visible: dirtyHover.hovered
        ToolTip.text: "Unsaved changes (Ctrl+S saves)"
    }
}

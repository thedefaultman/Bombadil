import QtQuick
import QtQuick.Shapes
import "TipScene.js" as Scene

// A tip's scene: a small drawing of Bombadil's own screen (the pill, the line above it, a window, the
// desk's cards, a picture) that plays a tip's beats, or stands still on its last frame. It is drawn from
// the theme's tokens and Kit.Stone, so it is always in the current look, crisp at any size, and a few
// lines of text to add. docs/TIP-SCENES.md has the beats, the vocabulary and the reasons; the brief is
// docs/design/tips-and-desk-defaults-brief.md ("How a tip moves").
//
//   TipScene { tip: row; density: "card" }                          // the desk card's still poster
//   TipScene { tip: row; density: "app"; reducedMotion: Theme.reducedMotion; onFinished: ... }
//
// `tip` is one row of the tips table: line, words, marks, captions (three) and beats (a list of
// tables). Internal, like Kit.Stone: not in the apps skill, and `bombadil-app check` refuses it in an
// app the agent writes. The Tips app and the desk's Tips card are what draw it.
//
// A scene is a function of its clock `t` (ms): the frame at any t is drawn from the beats alone, so a
// test or a still asks for one without waiting (`seek(ms)`). Nothing runs while the scene is not
// playing, and there is no timer, no Behavior and no loop anywhere in it: the one thing that moves is
// `t`, advanced by a single animation that exists only between play() and the end. The mini stone is a
// Kit.Stone with animate off whose pose the scene writes from `t`.
//
// card (272 x 153): the poster, the last beat's resulting state, at once, whatever `t` is. The 16 px
//   stone in ink (no state colour), a 32 px mini pill, 12 px text and never smaller, line buttons as
//   unlabelled outlines, no counter, and pictures of the machine as generic shapes with no names or
//   numbers. It never plays.
// app (576 x 324, and the captions in a row under it): plays. The 24 px stone in its real colours
//   (orange only where it acts), a 52 px pill, 16 px text, a 15 px line. The three phase captions sit
//   under the stage, the current one in fg. Under reducedMotion it shows three stills (each the last frame
//   of its phase, at the card's size, flowing onto a second row where three do not fit) with their
//   captions, and play() settles at once.
Item {
    id: root

    property var tip: ({})
    property string density: "card"        // "card" or "app"
    property bool reducedMotion: false     // the user binds it to Theme.reducedMotion
    property real t: 0                     // the master clock, ms (a scene is a function of it)

    readonly property bool poster: density !== "app"
    readonly property bool stills: !poster && reducedMotion
    readonly property var compiled: Scene.compile(tip && tip.beats ? tip.beats : [])
    readonly property real duration: compiled.duration
    // What time the frame is drawn at: the poster and the stills are the payoff, whatever `t` says.
    readonly property real clock: poster || stills ? duration : Math.max(0, Math.min(t, duration))
    readonly property int phase: Scene.phaseAt(compiled, clock)
    readonly property var frame: Scene.frameAt(compiled, clock, false)
    readonly property var captions: tip && tip.captions ? Array.prototype.slice.call(tip.captions) : []
    readonly property bool playing: clockAnim.running
    readonly property var vocabulary: ({ draws: Scene.DRAWS, modifiers: Scene.MODIFIERS, faces: Scene.FACES,
                                         keys: Object.keys(Scene.KEY_LABELS) })

    signal finished()

    // Plays from where t is (from the start when it is at the end), and emits finished at the end, where
    // the scene holds its last frame. Where nothing plays (the poster, reduced motion) it settles at once.
    function play() {
        clockAnim.stop()
        if (poster || reducedMotion || duration <= 0) {
            t = duration
            Qt.callLater(root.finished)
            return
        }
        if (t >= duration) t = 0
        clockAnim.from = t
        clockAnim.to = duration
        clockAnim.duration = Math.max(1, duration - t)
        clockAnim.start()
    }
    function replay() { seek(0); play() }
    // Stops the clock and puts it at ms (clamped to the scene): the frame at that time.
    function seek(ms) {
        clockAnim.stop()
        t = Math.max(0, Math.min(ms, duration))
    }
    onTipChanged: { clockAnim.stop(); t = 0 }

    NumberAnimation {
        id: clockAnim
        target: root
        property: "t"
        easing.type: Easing.Linear
        running: false
        onFinished: root.finished()
    }

    // -- the two sets of measures --

    readonly property var cardM: ({
        w: 272, h: 153, radius: 8, pillH: 32, pillBottom: 6, pillSide: 24, stone: 16, small: true,
        text: 12, lineText: 12, title: 12, row: 12, caption: 12, rowH: 15, pad: 5, gap: 5, lineH: 22, cardW: 84,
        winW: 232, winH: 75, winTop: 6, winRadius: 6, slide: 10, picW: 236, boxH: 20, border: 1,
        stripH: 18, keyH: 20, buttonW: 18, buttonH: 12, margin: 8, pillMin: 72, chipH: 18
    })
    readonly property var appM: ({
        w: 576, h: 324, radius: 12, pillH: 52, pillBottom: 14, pillSide: 64, stone: 24, small: false,
        text: 16, lineText: 15, title: 16, row: 14, caption: 12, rowH: 28, pad: 12, gap: 8, lineH: 36, cardW: 208,
        winW: 380, winH: 176, winTop: 14, winRadius: 12, slide: 28, picW: 440, boxH: 40, border: 1.5,
        stripH: 28, keyH: 30, buttonW: 0, buttonH: 24, margin: 12, pillMin: 160, chipH: 26
    })

    implicitWidth: poster ? cardM.w : appM.w
    implicitHeight: poster ? cardM.h
                  : stills ? stillsRow.height
                  : appM.h + 10 + captionsRow.height

    // The desk card's poster, or the app's stage.
    Stage {
        id: stage
        objectName: "stage"
        visible: !root.stills
        m: root.poster ? root.cardM : root.appM
        f: root.frame
        ink: root.poster
    }

    // The phase captions under the stage, the current one in fg.
    Row {
        id: captionsRow
        objectName: "captions"
        visible: !root.poster && !root.stills
        x: 0; y: root.appM.h + 10
        spacing: 8
        Repeater {
            model: root.captions
            delegate: Row {
                id: cap
                required property int index
                required property string modelData
                spacing: 8
                Text {
                    objectName: "caption-" + cap.index
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                    text: cap.modelData
                    color: cap.index === root.phase ? Theme.fg : Theme.muted
                }
                Text {
                    visible: cap.index < root.captions.length - 1
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                    text: "·"
                    color: Theme.faint
                }
            }
        }
    }

    // Reduced motion: three stills, each the last frame of its phase, with its caption under it. Each is
    // drawn at the card's measures, 12 px text and never smaller, so where three do not fit across they wrap
    // onto a second row: two and one on the app's 576 px stage, all three from 840 px. A host narrower than
    // one card shrinks them to its width.
    Flow {
        id: stillsRow
        objectName: "stills"
        visible: root.stills
        width: root.width
        spacing: 12
        readonly property real cw: Math.min(root.cardM.w, root.width)
        readonly property real k: cw / root.cardM.w
        Repeater {
            model: root.stills ? 3 : 0
            delegate: Column {
                id: still
                required property int index
                spacing: 6
                Item {
                    width: stillsRow.cw
                    height: root.cardM.h * stillsRow.k
                    Stage {
                        objectName: "still-" + still.index
                        scale: stillsRow.k
                        transformOrigin: Item.TopLeft
                        m: root.cardM
                        f: Scene.frameAt(root.compiled, Scene.phaseEnd(root.compiled, still.index), true)
                        ink: false
                    }
                }
                Text {
                    objectName: "still-caption-" + still.index
                    width: stillsRow.cw
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                    text: still.index < root.captions.length ? root.captions[still.index] : ""
                    color: Theme.fg
                    elide: Text.ElideRight
                }
            }
        }
    }

    // ---------------------------------------------------------------------------------------------
    // The mini screen: one frame of the scene, drawn at the measures `m`. Everything is a function of
    // `f` (the frame) and `m`; nothing here has a state of its own.

    component Stage: Item {
        id: st
        property var m
        property var f
        property bool ink: false
        width: m.w
        height: m.h

        // A frame is a fresh object every time, so a Repeater takes only a count and each delegate reads its
        // own entry by index: the delegates stay, and what they show moves with the clock.
        readonly property var blank: ({ kind: "", rows: [], cells: [], label: "", head: "", alpha: 0, grow: 0,
                                        pct: 0, tone: "", shown: 0, arrive: 0, why: "", title: "", value: undefined })
        function at(list, i) { return list && i >= 0 && i < list.length ? list[i] : blank }

        readonly property real pillTop: m.h - m.pillBottom - m.pillH
        // Waiting asks stand as chips just above the pill, under the line (shell/QueueChips.qml), and lift it.
        readonly property real queueLift: queueRow.visible ? (m.chipH + m.gap) * queueRow.q.alpha : 0
        readonly property real slotTop: pillTop - m.gap - m.lineH - queueLift   // where a one-line line sits
        // The rails and the picture rest on the line: a line of several lines grows upward from the pill and
        // lifts them as it arrives, so it never covers a card or the picture.
        readonly property real lineLift: lineBox.visible ? (lineBox.height - m.lineH) * lineBox.ln.alpha : 0
        readonly property real railBottom: slotTop - m.gap - lineLift
        // Folded, the strips stand beside the pill on the left and the pill is what they leave, with its
        // right edge where it was: the pill never runs under a strip, however many there are.
        readonly property real stripsEnd: m.margin / 2 + stripsRow.width + m.gap
        readonly property real side: m.pillSide + Math.max(0, stripsEnd - m.pillSide) * f.fold.p
        // what the strips may take together, so that the pill keeps a few words' width
        readonly property real stripsRoom: m.w - m.pillSide - m.margin / 2 - m.gap - m.pillMin

        // The ground: the sunken surface with two faint glows, one from above at the left and one from
        // below at the right, so a poster is never a flat rectangle. Both are neutral light.
        Rectangle { anchors.fill: parent; radius: st.m.radius; color: Theme.sunken }
        Shape {
            anchors.fill: parent
            ShapePath {
                strokeColor: "transparent"
                fillGradient: RadialGradient {
                    centerX: st.m.w * 0.18; centerY: 0; centerRadius: st.m.w * 0.62
                    focalX: st.m.w * 0.18; focalY: 0
                    GradientStop { position: 0; color: Theme.alpha(Theme.fg, 0.055) }
                    GradientStop { position: 1; color: Theme.alpha(Theme.fg, 0) }
                }
                PathRectangle { width: st.m.w; height: st.m.h; radius: st.m.radius }
            }
            ShapePath {
                strokeColor: "transparent"
                fillGradient: RadialGradient {
                    centerX: st.m.w * 0.92; centerY: st.m.h; centerRadius: st.m.w * 0.5
                    focalX: st.m.w * 0.92; focalY: st.m.h
                    GradientStop { position: 0; color: Theme.alpha(Theme.fg, 0.04) }
                    GradientStop { position: 1; color: Theme.alpha(Theme.fg, 0) }
                }
                PathRectangle { width: st.m.w; height: st.m.h; radius: st.m.radius }
            }
        }

        // -- the desk's cards, in the rails --
        Column {
            id: leftRail
            objectName: "railLeft"
            x: st.m.margin / 2
            y: st.railBottom - height
            spacing: st.m.gap / 2 + 1
            Repeater {
                model: st.f.cardsLeft.length
                delegate: Card { required property int index; c: st.at(st.f.cardsLeft, index); m: st.m; ink: st.ink; fromRight: false }
            }
        }
        Column {
            id: rightRail
            objectName: "railRight"
            x: st.m.w - st.m.cardW - st.m.margin / 2
            y: st.railBottom - height
            spacing: st.m.gap / 2 + 1
            Repeater {
                model: st.f.cardsRight.length
                delegate: Card { required property int index; c: st.at(st.f.cardsRight, index); m: st.m; ink: st.ink; fromRight: true }
            }
        }

        // -- a window, sliding in from its drawer --
        Rectangle {
            id: win
            objectName: "window"
            readonly property var w: st.f.win
            visible: w !== null
            readonly property real slide: w ? w.slide : 0
            readonly property string from: w ? w.from : "bottom"
            // as tall as what is in it (its rows, its lines of grey, its lock screen), never past the room above the line
            readonly property real head: st.m.pad + st.m.title + st.m.gap          // where the body begins, under the title
            readonly property real wanted: !w ? 0 : w.doc ? head + docLines.height + st.m.pad
                : w.lock !== "" ? head + lockBody.height + st.m.pad
                : 2 * st.m.pad + st.m.title + st.m.gap + w.rows.length * (st.m.rowH + (st.m.small ? 1 : 4)) + 4
            // the room above whatever stands over the pill (the line, a picture), so a window never sits under one
            readonly property real room: Math.min(lineBox.visible ? lineBox.y : st.railBottom, pic.visible ? pic.y : st.railBottom)
                                         - st.m.gap - st.m.winTop
            width: st.m.winW
            height: Math.min(st.m.winH, room, Math.max(wanted, st.m.title + 2 * st.m.pad + st.m.gap))
            x: (from === "left" ? st.m.margin : from === "right" ? st.m.w - width - st.m.margin : (st.m.w - width) / 2)
               + (from === "left" ? -(1 - slide) * (width + st.m.margin) : from === "right" ? (1 - slide) * (width + st.m.margin) : 0)
            y: st.m.winTop + (from === "bottom" ? (1 - slide) * st.m.slide : 0)
            opacity: w ? w.alpha : 0
            radius: st.m.winRadius
            color: Theme.bg
            border.width: 1
            border.color: st.ink ? Theme.border : Theme.borderStrong
            clip: true

            Text {
                id: winTitle
                objectName: "windowTitle"
                x: st.m.pad; y: st.m.pad - 1
                width: parent.width - 2 * st.m.pad
                font.family: Theme.fontFamily
                font.pixelSize: st.m.title
                font.weight: Font.DemiBold
                text: win.w ? win.w.title : ""
                color: Theme.fg
                elide: Text.ElideRight
            }
            // column headings, right of the title
            Row {
                visible: win.w !== null && win.w.cols.length > 0 && !win.w.doc
                anchors.right: parent.right; anchors.rightMargin: st.m.pad + 2
                y: st.m.pad
                spacing: 4
                layoutDirection: Qt.RightToLeft
                Repeater {
                    model: win.w ? win.w.cols.length : 0
                    delegate: Text {
                        required property int index
                        readonly property var d: st.at(win.w ? win.w.cols : null, index)
                        width: st.m.small ? 34 : 64
                        horizontalAlignment: Text.AlignRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionSize
                        text: d.head
                        color: Theme.muted
                        opacity: d.alpha
                        elide: Text.ElideRight
                    }
                }
            }
            Column {
                id: winRows
                objectName: "windowRows"
                visible: win.w !== null && win.w.lock === "" && !win.w.doc
                x: st.m.pad - 2; y: st.m.pad + st.m.title + st.m.gap
                width: parent.width - 2 * st.m.pad + 4
                spacing: st.m.small ? 1 : 4
                Repeater {
                    model: win.w ? win.w.rows.length : 0
                    delegate: Rectangle {
                        id: wrow
                        required property int index
                        readonly property var d: st.at(win.w ? win.w.rows : null, index)
                        objectName: "windowRow"
                        width: winRows.width
                        height: st.m.rowH
                        radius: st.m.small ? 4 : 6
                        color: Theme.panel
                        opacity: d.alpha
                        Text {
                            objectName: "windowRowLabel"
                            x: 6; anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - 12 - cells.width
                            font.family: Theme.fontFamily
                            font.pixelSize: st.m.row
                            text: wrow.d.label
                            color: Theme.fg
                            elide: Text.ElideRight
                        }
                        Row {
                            id: cells
                            anchors.right: parent.right; anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 4
                            layoutDirection: Qt.RightToLeft
                            Rectangle {
                                visible: wrow.d.value !== undefined
                                width: badge.implicitWidth + 10; height: badge.implicitHeight + 2; radius: height / 2
                                color: Theme.raised
                                Text {
                                    id: badge
                                    anchors.centerIn: parent
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionSize
                                    text: wrow.d.value === undefined ? "" : String(wrow.d.value)
                                    color: Theme.muted
                                }
                            }
                            Repeater {
                                model: win.w ? win.w.cols.length : 0
                                delegate: Text {
                                    required property int index
                                    readonly property var d: st.at(win.w ? win.w.cols : null, index)
                                    width: st.m.small ? 34 : 64
                                    horizontalAlignment: Text.AlignRight
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionSize
                                    text: wrow.index < d.cells.length ? d.cells[wrow.index] : ""
                                    color: Theme.fg
                                    opacity: d.alpha
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
            // a document: lines of grey
            Column {
                id: docLines
                objectName: "windowDoc"
                visible: win.w !== null && win.w.doc
                x: st.m.pad; y: win.head
                spacing: st.m.small ? 4 : 7
                Repeater {
                    model: [1, 0.55, 0.9, 0.4, 0.75]
                    delegate: Rectangle {
                        required property real modelData
                        width: (win.width - 2 * st.m.pad) * modelData; height: st.m.small ? 4 : 6; radius: height / 2
                        color: Theme.raised
                    }
                }
            }
            // the lock screen: its words and the password field, waiting
            Column {
                id: lockBody
                objectName: "windowLock"
                visible: win.w !== null && win.w.lock !== ""
                anchors.horizontalCenter: parent.horizontalCenter
                y: win.head
                spacing: st.m.gap
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: win.width - 2 * st.m.pad
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: st.m.row
                    text: win.w ? win.w.lock : ""
                    color: Theme.muted
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                }
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: win.width * 0.5; height: st.m.small ? 18 : 30; radius: 6
                    color: Theme.panel
                    border.width: 1
                    border.color: st.ink ? Theme.borderActive : Theme.accent
                }
            }
        }

        // -- a picture card above the line: boxes streaming in, one lit --
        Rectangle {
            id: pic
            objectName: "picture"
            readonly property var p: st.f.pic
            visible: p !== null
            width: st.m.picW
            height: picBody.height + 2 * st.m.pad
            x: (st.m.w - width) / 2
            y: st.railBottom - height + (p ? (1 - p.slide) * st.m.slide / 2 : 0)
            opacity: p ? p.alpha : 0
            radius: st.m.small ? 8 : 12
            color: Theme.glassLine
            border.width: 1
            border.color: Theme.border
            Column {
                id: picBody
                x: st.m.pad; y: st.m.pad
                width: parent.width - 2 * st.m.pad
                spacing: st.m.gap
                // the title; a poster draws a bar in its place, because it has no names
                Text {
                    visible: !st.ink
                    font.family: Theme.fontFamily
                    font.pixelSize: st.m.row
                    font.weight: Font.DemiBold
                    text: pic.p ? pic.p.title : ""
                    color: Theme.fg
                }
                Rectangle { visible: st.ink; width: parent.width * 0.35; height: 5; radius: 2.5; color: Theme.borderStrong }
                Row {
                    id: boxRow
                    objectName: "boxes"
                    visible: pic.p !== null && pic.p.boxes.length > 0
                    width: parent.width
                    spacing: 4
                    readonly property int n: pic.p ? pic.p.boxes.length : 0
                    Repeater {
                        model: pic.p ? pic.p.boxes.length : 0
                        delegate: Row {
                            id: bx
                            required property int index
                            readonly property var d: st.at(pic.p ? pic.p.boxes : null, index)
                            spacing: 4
                            readonly property bool lit: pic.p !== null && pic.p.lit === index
                            opacity: d.alpha
                            Text {
                                visible: bx.index > 0
                                anchors.verticalCenter: parent.verticalCenter
                                font.family: Theme.fontFamily
                                font.pixelSize: st.m.row
                                text: "›"
                                color: Theme.faint
                            }
                            Rectangle {
                                objectName: "box"
                                width: Math.max(8, (boxRow.width - (boxRow.n - 1) * 16) / boxRow.n - 4)
                                height: st.m.boxH
                                radius: st.m.small ? 5 : 6
                                color: Theme.raised
                                border.width: bx.lit ? 2 : 1
                                border.color: bx.lit ? (st.ink ? Theme.fg : Theme.warn) : Theme.borderStrong
                                Text {
                                    visible: !st.ink
                                    anchors.centerIn: parent
                                    width: parent.width - 6
                                    horizontalAlignment: Text.AlignHCenter
                                    font.family: Theme.fontFamily
                                    font.pixelSize: st.m.row
                                    text: bx.d.label
                                    color: bx.lit ? Theme.warnInk : Theme.fg
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
                Column {
                    visible: pic.p !== null && pic.p.bars.length > 0
                    width: parent.width
                    spacing: st.m.small ? 4 : 8
                    Repeater {
                        model: pic.p ? pic.p.bars.length : 0
                        delegate: Row {
                            id: br
                            required property int index
                            readonly property var d: st.at(pic.p ? pic.p.bars : null, index)
                            spacing: 8
                            readonly property bool amber: d.tone === "amber"
                            Text {
                                visible: !st.ink
                                width: 72
                                anchors.verticalCenter: parent.verticalCenter
                                font.family: Theme.fontFamily
                                font.pixelSize: st.m.row
                                text: br.d.label
                                color: Theme.muted
                            }
                            Rectangle {
                                objectName: "bar"
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.parent.width - (st.ink ? 0 : 80)
                                height: st.m.small ? 6 : 10; radius: height / 2
                                color: Theme.raised
                                Rectangle {
                                    width: parent.width * br.d.pct / 100 * br.d.grow; height: parent.height; radius: height / 2
                                    color: br.amber ? (st.ink ? Theme.fg : Theme.warn) : (st.ink ? Theme.muted : Theme.muted)
                                }
                            }
                        }
                    }
                }
            }
        }

        // -- strips: the cards folded beside the pill --
        Row {
            id: stripsRow
            objectName: "strips"
            visible: st.f.fold.p > 0 && st.f.fold.strips.length > 0
            opacity: st.f.fold.p
            x: st.m.margin / 2
            y: st.pillTop + (st.m.pillH - st.m.stripH) / 2
            spacing: 4
            Repeater {
                model: st.f.fold.strips.length
                delegate: Rectangle {
                    id: strip
                    required property int index
                    readonly property string label: index < st.f.fold.strips.length ? st.f.fold.strips[index] : ""
                    // each takes its words' width, or its share of what the strips may take, whichever is less
                    readonly property real share: (st.stripsRoom - stripsRow.spacing * (st.f.fold.strips.length - 1))
                                                  / Math.max(1, st.f.fold.strips.length)
                    objectName: "strip"
                    height: st.m.stripH; radius: height / 2
                    width: Math.min(stripText.implicitWidth + 16, share)
                    color: Theme.glassStrip
                    border.width: 1; border.color: Theme.border
                    Text {
                        id: stripText
                        objectName: "stripText"
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, strip.width - 16)
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: st.m.small ? 12 : Theme.smallSize
                        text: strip.label
                        color: Theme.muted
                    }
                }
            }
        }

        // -- the line above the pill --
        Rectangle {
            id: lineBox
            objectName: "line"
            readonly property var ln: st.f.line
            readonly property real maxW: st.m.w - 2 * st.m.margin
            readonly property real padL: 16
            readonly property real padR: 12
            readonly property real secW: ln && ln.secs >= 1 && !st.ink ? secMetrics.width + 8 : 0
            readonly property real btnW: ln ? buttons.width + (buttons.width > 0 ? 8 : 0) : 0
            // The words come first and keep their room; the command takes what is left, or is left out.
            readonly property real avail: maxW - padL - padR - secW - btnW
            readonly property real textW: Math.min(lineMetrics.width + 2, avail)
            readonly property real cmdRoom: avail - textW - 8
            readonly property real cmdW: ln && ln.command !== "" && cmdRoom >= 56
                                         ? Math.min(cmdMetrics.width + 2, cmdRoom) + 8 : 0
            // As the real line: one line while the stone works, and up to four once it has something to say
            // (shell/StatusLine.qml). It grows upward from the pill, and what stands above it makes room.
            readonly property bool wraps: ln !== null && st.f.stone.face !== "working"
            visible: ln !== null
            opacity: ln ? ln.alpha : 0
            width: Math.min(maxW, padL + padR + textW + cmdW + secW + btnW)
            height: st.m.lineH + Math.max(0, lineText.lineCount - 1) * lineText.contentHeight / Math.max(1, lineText.lineCount)
            x: (st.m.w - width) / 2
            y: st.slotTop + st.m.lineH - height + (ln ? (1 - ln.alpha) * 4 : 0)
            radius: Math.min(height / 2, Theme.radiusLine)
            color: Theme.glassLine
            border.width: 1
            border.color: Theme.border

            TextMetrics { id: lineMetrics; font: lineText.font; text: lineBox.ln ? lineBox.ln.text : "" }
            TextMetrics { id: cmdMetrics; font: cmdText.font; text: lineBox.ln ? lineBox.ln.command : "" }
            TextMetrics { id: secMetrics; font: secText.font; text: lineBox.ln && lineBox.ln.secs >= 1 ? lineBox.ln.secs + "s" : "" }

            // the marked edge: amber for a step that touches the system, red for one no restore point can undo
            Rectangle {
                visible: lineBox.ln !== null && lineBox.ln.tone !== ""
                x: 7; y: 9; width: 3; height: parent.height - 18; radius: 1.5
                color: !lineBox.ln ? "transparent" : st.ink ? Theme.muted : lineBox.ln.tone === "red" ? Theme.bad : Theme.warn
            }
            Row {
                x: lineBox.padL; anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Text {
                    id: lineText
                    objectName: "lineText"
                    width: lineBox.textW
                    font.family: lineBox.ln && lineBox.ln.mono ? Theme.monoFamily : Theme.fontFamily
                    font.pixelSize: st.m.lineText
                    text: lineBox.ln ? lineBox.ln.text : ""
                    color: lineBox.ln && lineBox.ln.tone === "red" && !st.ink ? Theme.badInk : Theme.fg
                    wrapMode: lineBox.wraps ? Text.Wrap : Text.NoWrap
                    maximumLineCount: lineBox.wraps ? 4 : 1
                    elide: Text.ElideRight
                }
                Text {
                    id: cmdText
                    objectName: "lineCommand"
                    visible: lineBox.cmdW > 0
                    width: Math.max(0, lineBox.cmdW - 8)
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                    font.family: Theme.monoFamily
                    font.pixelSize: 12
                    text: lineBox.ln ? lineBox.ln.command : ""
                    color: st.ink ? Theme.muted : Theme.warnInk
                }
                Text {
                    id: secText
                    objectName: "lineCounter"
                    visible: lineBox.secW > 0
                    anchors.verticalCenter: parent.verticalCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.smallSize
                    font.features: { "tnum": 1 }
                    text: lineBox.ln && lineBox.ln.secs >= 1 ? lineBox.ln.secs + "s" : ""
                    color: Theme.muted
                }
                Row {
                    id: buttons
                    objectName: "lineButtons"
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Repeater {
                        model: lineBox.ln ? lineBox.ln.buttons.length : 0
                        delegate: Rectangle {
                            id: lb
                            required property int index
                            readonly property string label: lineBox.ln && index < lineBox.ln.buttons.length ? lineBox.ln.buttons[index] : ""
                            objectName: "lineButton"
                            width: st.m.small ? st.m.buttonW : lbText.implicitWidth + 20      // small: outlines, no labels
                            height: st.m.buttonH
                            radius: height / 2
                            color: Theme.raised
                            border.width: 1; border.color: Theme.borderStrong
                            Text {
                                id: lbText
                                visible: !st.m.small
                                anchors.centerIn: parent
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionSize
                                text: lb.label
                                color: Theme.fg
                            }
                        }
                    }
                }
            }
        }

        // -- asks that wait, as grey chips under the line: the label (what they wait for), then the words --
        Row {
            id: queueRow
            objectName: "queue"
            readonly property var q: st.f.queue
            readonly property int n: q ? q.asks.length : 0
            visible: q !== null
            opacity: q ? q.alpha : 0
            spacing: 6
            x: (st.m.w - width) / 2
            y: st.pillTop - st.m.gap - st.m.chipH + (q ? (1 - q.alpha) * 4 : 0)
            Repeater {
                model: queueRow.n
                delegate: Rectangle {
                    id: qc
                    required property int index
                    readonly property string ask: queueRow.q && index < queueRow.q.asks.length ? queueRow.q.asks[index] : ""
                    objectName: "queueChip"
                    // each takes its words' width, or its share of the line's room, whichever is less
                    readonly property real room: (st.m.w - 2 * st.m.margin - queueRow.spacing * (queueRow.n - 1)) / Math.max(1, queueRow.n)
                    width: Math.min(chipRow.implicitWidth + 20, room)
                    height: st.m.chipH
                    radius: height / 2
                    color: Theme.glassChip
                    border.width: 1; border.color: Theme.border
                    Row {
                        id: chipRow
                        anchors.centerIn: parent
                        spacing: 8
                        Text {
                            id: waitText
                            objectName: "queueWait"
                            anchors.verticalCenter: parent.verticalCenter
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionSize
                            text: queueRow.q ? queueRow.q.wait : ""
                            color: Theme.faint
                        }
                        Text {
                            objectName: "queueAsk"
                            anchors.verticalCenter: parent.verticalCenter
                            width: Math.min(implicitWidth, qc.room - 20 - waitText.implicitWidth - chipRow.spacing)
                            elide: Text.ElideRight
                            font.family: Theme.fontFamily
                            font.pixelSize: st.m.small ? 12 : Theme.smallSize
                            text: qc.ask
                            color: Theme.muted
                        }
                    }
                }
            }
        }

        // -- the pill --
        Rectangle {
            id: pill
            objectName: "pill"
            x: st.side
            y: st.pillTop
            width: st.m.w - st.side - st.m.pillSide
            height: st.m.pillH
            radius: height / 2
            color: Theme.glassPill
            border.width: st.m.border
            border.color: st.f.pill.border === "working" && !st.ink ? Theme.accent
                        : st.f.pill.border !== "idle" ? Theme.borderActive : Theme.border

            Stone {
                id: stone
                objectName: "stone"
                x: st.m.small ? 8 : 12
                anchors.verticalCenter: parent.verticalCenter
                small: st.m.small
                animate: false
                ink: st.ink ? "fg" : ""
                ground: Theme.panel
                face: st.f.stone.face
                turn: st.f.stone.turn
                knock: st.f.stone.knock
                lean: st.f.stone.lean
                glowOpacity: st.f.stone.glow
                hopY: st.f.stone.hopY
                hopSx: st.f.stone.hopSx
                hopSy: st.f.stone.hopSy
            }
            Item {
                id: fieldBox
                x: stone.x + stone.width + 8
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(0, parent.width - x - (st.m.small ? 12 : 64))
                height: st.m.text + 8
                clip: true
                Row {
                    id: field
                    anchors.verticalCenter: parent.verticalCenter
                    // an ask longer than the pill shows its end, where the caret is
                    x: Math.min(0, fieldBox.width - width - 2)
                    Rectangle {      // the text cursor: solid, because nothing here blinks
                        visible: st.f.pill.caret && st.f.pill.text === ""
                        anchors.verticalCenter: parent.verticalCenter
                        width: 1.5; height: st.m.text + 2; color: Theme.fg
                    }
                    Text {
                        id: typed
                        objectName: "pillText"
                        font.family: Theme.fontFamily
                        font.pixelSize: st.m.text
                        text: st.f.pill.text !== "" ? st.f.pill.text : (st.f.pill.placeholder ? "Ask anything" : "")
                        color: st.f.pill.text !== "" ? Theme.fg : Theme.muted
                    }
                    Rectangle {
                        visible: st.f.pill.caret && st.f.pill.text !== "" && st.f.pill.ghost === ""
                        anchors.verticalCenter: parent.verticalCenter
                        width: 1.5; height: st.m.text + 2; color: Theme.fg
                    }
                    Text {
                        objectName: "pillGhost"
                        font.family: Theme.fontFamily
                        font.pixelSize: st.m.text
                        text: st.f.pill.ghost
                        color: Theme.faint
                    }
                }
            }
            Text {
                visible: !st.m.small
                anchors.right: parent.right; anchors.rightMargin: 18
                anchors.verticalCenter: parent.verticalCenter
                font.family: Theme.fontFamily
                font.pixelSize: Theme.smallSize
                font.features: { "tnum": 1 }
                text: "09:41"
                color: Theme.muted
            }
        }

        // -- a keycap, pressed, in the margin beside the pill --
        Rectangle {
            id: keycap
            objectName: "key"
            readonly property var k: st.f.key
            visible: k !== null
            opacity: k ? k.alpha : 0
            width: Math.max(height, keyText.implicitWidth + 14)
            height: st.m.keyH
            radius: 6
            color: Theme.fg
            // in the margin on the left of the pill, or on its right while the strips stand on the left
            x: stripsRow.visible ? Math.min(st.m.w - width - 2, st.m.w - (st.m.pillSide + width) / 2)
                                 : Math.max(2, (st.side - width) / 2)
            y: st.pillTop + (st.m.pillH - height) / 2 + (k && k.down ? 2 : 0)
            Rectangle {      // the cap's lower edge, gone while it is down
                visible: keycap.k !== null && !keycap.k.down
                x: 0; y: parent.height - 2; width: parent.width; height: 2; radius: 1; color: Theme.muted
                z: -1
            }
            Text {
                id: keyText
                anchors.centerIn: parent
                font.family: Theme.fontFamily
                font.pixelSize: st.m.small ? 12 : 14
                font.weight: Font.DemiBold
                text: keycap.k ? keycap.k.label : ""
                color: Theme.inkOnLight
            }
        }
    }

    // One desk card: Watching (rows of a label and a value), Now (the route's steps), Machine (meters).
    // A poster draws its rows as shapes with no words or figures: it is a picture of the machine.
    component Card: Rectangle {
        id: cd
        property var c
        property var m
        property bool ink: false
        property bool fromRight: false
        objectName: "card-" + c.kind
        width: m.cardW
        height: body.height + 2 * (m.small ? 5 : 8)
        radius: m.small ? 6 : 10
        color: Theme.glassCard
        border.width: 1
        border.color: Theme.border
        opacity: c.shown
        scale: 0.6 + 0.4 * c.arrive
        transformOrigin: fromRight ? Item.BottomRight : Item.BottomLeft
        readonly property string heading: c.kind === "now" && c.title !== "" ? c.title
                                         : c.kind === "now" ? "Now" : c.kind === "watching" ? "Watching" : "Machine"
        Column {
            id: body
            x: m.small ? 6 : 10; y: m.small ? 5 : 8
            width: parent.width - 2 * x
            spacing: m.small ? 3 : 4
            Text {
                width: parent.width
                font.family: Theme.fontFamily
                font.pixelSize: m.small ? 12 : Theme.smallSize
                font.weight: Font.DemiBold
                text: cd.heading
                color: Theme.fg
                elide: Text.ElideRight
                wrapMode: m.small ? Text.NoWrap : Text.Wrap          // the stage's card has the room to say a task whole
                maximumLineCount: m.small ? 1 : 2
            }
            Text {
                visible: !cd.ink && cd.c.why !== ""
                width: parent.width
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
                text: cd.c.why
                color: Theme.muted
                elide: Text.ElideRight
                wrapMode: m.small ? Text.NoWrap : Text.Wrap
                maximumLineCount: m.small ? 1 : 2
            }
            Repeater {
                model: cd.c.rows.length
                delegate: Item {
                    id: cr
                    required property int index
                    readonly property var d: index < cd.c.rows.length ? cd.c.rows[index] : ({ label: "", value: undefined })
                    width: body.width
                    height: cd.ink ? 9 : 18
                    readonly property bool current: cd.c.kind === "now" && d.value === "current"
                    readonly property bool done: cd.c.kind === "now" && d.value === "done"
                    // a poster: a dot and a bar. The app: the words, the figure, the meter.
                    Rectangle {
                        id: dot
                        objectName: "cardDot"
                        visible: cd.c.kind === "now" || cd.ink
                        x: 0; anchors.verticalCenter: parent.verticalCenter
                        width: 6; height: 6; radius: 3
                        color: cr.done ? (cd.ink ? Theme.muted : Theme.good) : cr.current ? (cd.ink ? Theme.fg : Theme.accent) : "transparent"
                        border.width: 1
                        border.color: cr.done || cr.current ? color : Theme.faint
                    }
                    Rectangle {
                        visible: cd.ink
                        x: dot.width + 5; anchors.verticalCenter: parent.verticalCenter
                        width: (parent.width - 12) * (0.5 + 0.12 * (cr.d.label.length % 4)); height: 5; radius: 2.5
                        color: Theme.borderStrong
                    }
                    Text {
                        visible: !cd.ink
                        x: dot.visible ? 12 : 0; anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - x - (cd.c.kind === "watching" ? valueText.implicitWidth + 6 : 0)
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionSize
                        text: cr.d.label
                        color: cr.current ? Theme.fg : cr.done ? Theme.muted : cd.c.kind === "now" ? Theme.faint : Theme.muted
                        elide: Text.ElideRight
                    }
                    Text {
                        id: valueText
                        visible: !cd.ink && cd.c.kind === "watching" && cr.d.value !== undefined
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.textSize
                        font.weight: Font.DemiBold
                        font.features: { "tnum": 1 }
                        text: cr.d.value === undefined ? "" : String(cr.d.value)
                        color: Theme.fg
                    }
                    Rectangle {
                        objectName: "meter"
                        visible: !cd.ink && cd.c.kind === "machine"
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        width: parent.width * 0.45; height: 6; radius: 3
                        color: Theme.raised
                        Rectangle {
                            objectName: "meterFill"
                            width: parent.width * Math.max(0, Math.min(100, Number(cr.d.value) || 0)) / 100
                            height: parent.height; radius: 3
                            color: Theme.muted
                        }
                    }
                }
            }
        }
    }
}

import QtQuick
import QtQuick.Shapes

// The Bombadil mark: a stone (a rounded Reuleaux triangle, 18 px wide on a 24 px grid, resting
// corner up) with a lowercase b cut through it. It says what the machine is doing without a word:
//   rest       green, still
//   listening  green, leans toward what you type
//   working    orange, turns a third of a turn a second around the b, which never moves
//   needs      amber, knocks twice and waits, with a glow that breathes behind it
//   done       green, one hop with a squash on landing, then still
//   resting    the AI is out of plan or paused: the outline whole in grey over a faint fill, the b cut in; still
//   stopped    the Stop button's own grey square
//   offline    the outline alone, broken, red; the b stays
//   starting   the working roll (the pill adds the word "Starting")
// Drawn on the brand's 24 px grid (design/identity-brief.md, option E); the geometry below is that
// brief's, so the pill, the icon files and the README banner are one drawing.
//
// It lives in the kit because two places draw it: the pill (shell.qml, 24 px, state colours, animated)
// and the tip scenes (TipScene.qml: 16 px in ink on the desk card, 24 px in colour on the Tips stage,
// its pose written by the scene's clock). It is an internal component: it is not in the apps skill's
// list, and `bombadil-app check` refuses it in an app the agent writes (appkit/check.py).
//
//   Stone { face: "working" }                                    // the pill: 24 px, colours, animated
//   Stone { small: true; ink: "fg" }                             // the 16 px drawing, in ink
//   Stone { animate: false; face: "needs" }                      // painted at once, nothing moves
//
// `ink`: "" or false keeps the state colours (the pill). "fg" or true draws the stone and everything
// of it (glow, square, outline, dashes) in the primary text colour, "muted" in the secondary one: no
// green, orange, amber or red, because colour says who and a poster belongs to nobody. Every face
// still reads by its shape and motion.
// `small`: the identity brief's 16 px drawing: the stone full-bleed (W 16, corners 2.4, sides 13.6) and
// the b cut as the brand's own outline (the one in bombadil-symbolic.svg), 1.5 px wide.
// `animate`: false gates every animation and the settle timer. The stone is painted at once in its
// face's colour, the pose properties (turn, knock, lean, hopY, hopSx, hopSy, glowOpacity) are plain
// values a host may write, and nothing runs. TipScene does this: a scene is a function of its clock.
Item {
    id: stone

    property string face: "rest"
    // The colour the b is cut in: the surface the stone sits on.
    property color ground: Theme.glassPill
    // Working pulses instead of rolling, and "needs you" holds its glow still.
    property bool reducedMotion: false
    // Gates every animation and the settle timer (see above).
    property bool animate: true
    // "", false, "fg"/true or "muted": see above.
    property var ink: ""
    // The 16 px drawing instead of the 24 px one.
    property bool small: false

    implicitWidth: small ? 16 : 24
    implicitHeight: small ? 16 : 24

    readonly property bool inked: ink === true || ink === "fg" || ink === "muted"
    readonly property color inkColor: ink === "muted" ? Theme.muted : Theme.fg
    readonly property bool rolling: face === "working" || face === "starting"
    readonly property bool drawn: face !== "stopped" && face !== "offline" && face !== "resting"
    readonly property color fill: inked ? inkColor : rolling ? Theme.accent : face === "needs" ? Theme.warn : Theme.good
    // The tones of the marks that are not the stone's fill.
    readonly property color glowTone: inked ? inkColor : Theme.warn
    readonly property color greyTone: inked ? inkColor : Theme.muted
    readonly property color redTone: inked ? inkColor : Theme.bad

    // What the stone is painted with. The scene graph drops a colour change made in the first
    // frames after the bar starts: on the VM the face went starting, then rest 39 ms later, and the
    // stone stayed the starting orange until its next change, a hundred seconds on. So for the first
    // `settle` ms the stone is painted resting green whatever its face says, then follows `fill`
    // (a face that is still starting or needing you is painted from then on). Without animation there
    // is no first frame to lose and no waiting: it is painted in its face's colour at once.
    property int settle: 400
    property color paint: animate ? Theme.good : fill
    Timer { interval: stone.settle; running: stone.animate; onTriggered: stone.paint = Qt.binding(() => stone.fill) }

    // The 24 px stone: corner radius 2.7, side radius 15.3, top corner centred on (12, 5.7), centroid at
    // (12, 12.975). It rocks on its bottom arc (radius 15.3 about the top corner's centre) to lean
    // and to knock, and turns about its centroid to work. The 16 px stone is the same shape at W 16
    // (corners 2.4, sides 13.6, top corner on (8, 2.4), centroid on (8, 8.866)): the identity brief's
    // proportions, 0.15 W and 0.85 W, with the centroid 0.554 W below the top.
    readonly property real cx: small ? 8 : 12
    readonly property real cy: small ? 8.866 : 12.975
    readonly property real topY: small ? 2.4 : 5.7
    readonly property real foot: small ? 16 : 21      // where it stands: the hop squashes about it
    readonly property real armR: cy - topY            // top corner's centre to the centroid
    readonly property real arcR: small ? 13.6 : 15.3  // what it rocks on
    readonly property real toRad: Math.PI / 180

    // The paths. The 16 px stone and the b cut in it are bombadil-symbolic.svg's, so the icon files and
    // this are one drawing (tests/test_stone_qml.py compares them). The outlines are the stone inset
    // by half the 1.5 px line, for the faces that are a line.
    readonly property string stonePath: small
        ? "M6.8 0.322A2.4 2.4 0 0 1 9.2 0.322A13.6 13.6 0 0 1 16 12.099A2.4 2.4 0 0 1 14.8 14.178"
            + "A13.6 13.6 0 0 1 1.2 14.178A2.4 2.4 0 0 1 0 12.099A13.6 13.6 0 0 1 6.8 0.322Z"
        : "M10.65 3.362A2.7 2.7 0 0 1 13.35 3.362A15.3 15.3 0 0 1 21 16.612A2.7 2.7 0 0 1 19.65 18.95"
            + "A15.3 15.3 0 0 1 4.35 18.95A2.7 2.7 0 0 1 3 16.612A15.3 15.3 0 0 1 10.65 3.362Z"
    readonly property string outlinePath: small
        ? "M6.912 1.041A2.175 2.175 0 0 1 9.088 1.041A12.325 12.325 0 0 1 15.25 11.715A2.175 2.175 0 0 1 14.162 13.599"
            + "A12.325 12.325 0 0 1 1.838 13.599A2.175 2.175 0 0 1 0.75 11.715A12.325 12.325 0 0 1 6.912 1.041Z"
        : "M10.763 4.082A2.475 2.475 0 0 1 13.238 4.082A14.025 14.025 0 0 1 20.25 16.228A2.475 2.475 0 0 1 19.013 18.371"
            + "A14.025 14.025 0 0 1 4.988 18.371A2.475 2.475 0 0 1 3.75 16.228A14.025 14.025 0 0 1 10.762 4.082Z"

    // Pose, in degrees. lean follows the face; turn, knock and the hop are written by the animations
    // (or, with animate off, by whoever draws the stone a frame at a time).
    property real lean: face === "listening" ? 6 : 0
    property real turn: 0
    property real knock: 0
    readonly property real rock: lean + knock
    property real hopY: 0
    property real hopSx: 1
    property real hopSy: 1
    // The glow's strength: the breathing writes 0.25 to 0.6 and back. Held still (reduced motion, or
    // no animation) it is 0.45, which is where the reduced-motion glow rests.
    property real glowOpacity: animate ? 0.25 : 0.45

    Behavior on lean {
        enabled: stone.animate
        NumberAnimation { duration: stone.reducedMotion ? 0 : 240; easing.type: Easing.BezierSpline; easing.bezierCurve: [0.16, 1, 0.3, 1, 1, 1] }
    }

    // The b rides: it slides with the stone's centre but never tilts.
    readonly property real rideX: arcR * rock * toRad - armR * Math.sin(rock * toRad)
    readonly property real rideY: armR * (Math.cos(rock * toRad) - 1)

    // The b, cut through the stone: at 24 px a line 1.6 px wide, flat-ended and square-cornered; at 16 px
    // the brand's own outline of it, 1.5 px wide, filled.
    component BCut: Item {
        id: cut
        property bool tiny: false
        property color tone: "magenta"
        Shape {
            anchors.fill: parent
            visible: !cut.tiny
            ShapePath {
                fillColor: "transparent"
                strokeColor: cut.tone
                strokeWidth: 1.6
                capStyle: ShapePath.FlatCap
                joinStyle: ShapePath.MiterJoin
                PathSvg { path: "M7.97 10.96H13.24A3.41 3.41 0 0 1 13.24 17.78H8.59V7.86" }
            }
        }
        Shape {
            anchors.fill: parent
            visible: cut.tiny
            ShapePath {
                fillColor: cut.tone
                strokeColor: "transparent"
                fillRule: ShapePath.OddEvenFill
                PathSvg { path: "M4.17 4.246H5.67V6.296H9.12A3.83 3.83 0 0 1 9.12 13.956H4.17ZM5.67 7.796H9.12A2.33 2.33 0 0 1 9.12 12.456H5.67Z" }
            }
        }
    }

    // Needs you: the glow, a filled radial fade under the stone (never a ring). At 24 px it reaches 16 px
    // from the centroid, 5 px past the stone's edge and past the 24 px slot into the pill's padding: at
    // 11 px it was a 1 px halo that did not read at 1x. The 16 px stone's reaches 14.
    Shape {
        anchors.fill: parent
        visible: stone.face === "needs"
        opacity: stone.reducedMotion ? 0.45 : stone.glowOpacity
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: stone.cx; centerY: stone.cy; centerRadius: stone.small ? 14 : 16
                focalX: stone.cx; focalY: stone.cy
                GradientStop { position: 0; color: stone.glowTone }
                GradientStop { position: 0.58; color: Theme.alpha(stone.glowTone, 0.7) }
                GradientStop { position: 1; color: Theme.alpha(stone.glowTone, 0) }
            }
            PathAngleArc { centerX: stone.cx; centerY: stone.cy; radiusX: stone.small ? 14 : 16; radiusY: stone.small ? 14 : 16; startAngle: 0; sweepAngle: 360 }
        }
    }

    // The mark proper: the hop squashes it about its foot.
    Item {
        id: mark
        width: stone.width; height: stone.height
        visible: stone.drawn
        y: stone.hopY
        transform: Scale { origin.x: stone.cx; origin.y: stone.foot; xScale: stone.hopSx; yScale: stone.hopSy }

        Shape {
            id: rockShape
            width: stone.width; height: stone.height
            x: stone.arcR * stone.rock * stone.toRad
            transform: [
                Rotation { origin.x: stone.cx; origin.y: stone.topY; angle: stone.rock },
                Rotation { origin.x: stone.cx; origin.y: stone.cy; angle: stone.turn }
            ]
            ShapePath {
                fillColor: stone.paint
                strokeColor: "transparent"
                PathSvg { path: stone.stonePath }
            }
        }

        // The cut: the b drawn in the surface colour.
        BCut {
            width: stone.width; height: stone.height
            x: stone.rideX
            y: stone.rideY
            tiny: stone.small
            tone: stone.ground
        }
    }

    // Stopped: the Stop button's own square, muted. Never orange.
    Rectangle {
        visible: stone.face === "stopped"
        x: stone.small ? 3 : 6; y: stone.small ? 3 : 6
        width: stone.small ? 10 : 12; height: width; radius: stone.small ? 2 : 2.4
        color: stone.greyTone
    }

    // Resting: the stone as a hollow, grey and still. The outline is the offline one, whole, and a
    // faint grey fill keeps the b legible: it is cut in the ground's colour as on every other face.
    Shape {
        anchors.fill: parent
        visible: stone.face === "resting"
        ShapePath {
            fillColor: Theme.alpha(stone.greyTone, 0.3)
            strokeColor: stone.greyTone
            strokeWidth: 1.5
            joinStyle: ShapePath.MiterJoin
            PathSvg { path: stone.outlinePath }
        }
    }
    BCut {
        width: stone.width; height: stone.height
        visible: stone.face === "resting"
        tiny: stone.small
        tone: stone.ground
    }

    // Offline: the outline alone, 1.5 px, six round-capped dashes (one on each corner and each
    // side), and the b still in place as a red line.
    Shape {
        anchors.fill: parent
        visible: stone.face === "offline"
        ShapePath {
            fillColor: "transparent"
            strokeColor: stone.redTone
            strokeWidth: 1.5
            capStyle: ShapePath.RoundCap
            strokeStyle: ShapePath.DashLine
            // Qt counts dashes in pen widths. 24 px: 4.125 4.102 4.95 4.102 px, offset 0.767 px; the 16 px
            // outline is 14.5 wide against 16.5, so the same pattern at 0.879.
            dashPattern: stone.small ? [3.625 / 1.5, 3.605 / 1.5, 4.35 / 1.5, 3.605 / 1.5]
                                     : [4.125 / 1.5, 4.102 / 1.5, 4.95 / 1.5, 4.102 / 1.5]
            dashOffset: (stone.small ? 0.674 : 0.767) / 1.5
            PathSvg { path: stone.outlinePath }
        }
    }
    BCut {
        width: stone.width; height: stone.height
        visible: stone.face === "offline"
        tiny: stone.small
        tone: stone.redTone
    }

    // -- motion --

    // Working: hold 300 ms, turn a third of a turn in 550 ms, hold 150 ms. A third of a turn
    // leaves the stone looking as it did, so the next beat starts from 0 unseen.
    SequentialAnimation {
        running: stone.animate && stone.rolling && !stone.reducedMotion
        loops: Animation.Infinite
        PauseAnimation { duration: 300 }
        NumberAnimation {
            target: stone; property: "turn"; from: 0; to: 120; duration: 550
            easing.type: Easing.BezierSpline; easing.bezierCurve: [0.55, 0, 0.3, 1, 1, 1]
        }
        PauseAnimation { duration: 150 }
        onStopped: stone.turn = 0
    }
    // Reduced motion: the old pulse, 1 to 0.3 to 1 a second.
    SequentialAnimation {
        running: stone.animate && stone.rolling && stone.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { target: mark; property: "opacity"; to: Theme.pulseLow; duration: 500 }
        NumberAnimation { target: mark; property: "opacity"; to: 1; duration: 500 }
        onStopped: mark.opacity = 1
    }

    // Needs you: two knocks tipping 8 degrees to the right, then a wait, on a 1.6 s beat; the
    // glow breathes 0.25 to 0.6 and back on the same beat.
    SequentialAnimation {
        running: stone.animate && stone.face === "needs" && !stone.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { target: stone; property: "knock"; to: 8; duration: 192; easing.type: Easing.OutQuad }
        NumberAnimation { target: stone; property: "knock"; to: 0; duration: 160; easing.type: Easing.InQuad }
        NumberAnimation { target: stone; property: "knock"; to: 8; duration: 160; easing.type: Easing.OutQuad }
        NumberAnimation { target: stone; property: "knock"; to: 0; duration: 160; easing.type: Easing.InQuad }
        PauseAnimation { duration: 928 }
        onStopped: stone.knock = 0
    }
    SequentialAnimation {
        running: stone.animate && stone.face === "needs" && !stone.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { target: stone; property: "glowOpacity"; from: 0.25; to: 0.6; duration: 560; easing.type: Easing.InOutSine }
        NumberAnimation { target: stone; property: "glowOpacity"; to: 0.25; duration: 1040; easing.type: Easing.InOutSine }
        onStopped: stone.glowOpacity = 0.25
    }

    // Done: once, 700 ms: squash to (1.06, 0.9) about the foot, hop 3 px, land squashed to
    // (1.1, 0.84), a small rebound, still. Reduced motion: a fade in from 0.3.
    SequentialAnimation {
        running: stone.animate && stone.face === "done" && !stone.reducedMotion
        ParallelAnimation {
            NumberAnimation { target: stone; property: "hopSx"; to: 1.06; duration: 70; easing.type: Easing.OutQuad }
            NumberAnimation { target: stone; property: "hopSy"; to: 0.9; duration: 70; easing.type: Easing.OutQuad }
        }
        ParallelAnimation {
            NumberAnimation { target: stone; property: "hopSx"; to: 0.97; duration: 196; easing.type: Easing.OutQuad }
            NumberAnimation { target: stone; property: "hopSy"; to: 1.04; duration: 196; easing.type: Easing.OutQuad }
            NumberAnimation { target: stone; property: "hopY"; to: -3; duration: 196; easing.type: Easing.OutQuad }
        }
        ParallelAnimation {
            NumberAnimation { target: stone; property: "hopSx"; to: 1.1; duration: 168; easing.type: Easing.InQuad }
            NumberAnimation { target: stone; property: "hopSy"; to: 0.84; duration: 168; easing.type: Easing.InQuad }
            NumberAnimation { target: stone; property: "hopY"; to: 0; duration: 168; easing.type: Easing.InQuad }
        }
        ParallelAnimation {
            NumberAnimation { target: stone; property: "hopSx"; to: 0.98; duration: 112; easing.type: Easing.InOutQuad }
            NumberAnimation { target: stone; property: "hopSy"; to: 1.03; duration: 112; easing.type: Easing.InOutQuad }
        }
        ParallelAnimation {
            NumberAnimation { target: stone; property: "hopSx"; to: 1; duration: 154; easing.type: Easing.InOutQuad }
            NumberAnimation { target: stone; property: "hopSy"; to: 1; duration: 154; easing.type: Easing.InOutQuad }
        }
        onStopped: { stone.hopSx = 1; stone.hopSy = 1; stone.hopY = 0 }
    }
    SequentialAnimation {
        running: stone.animate && stone.face === "done" && stone.reducedMotion
        NumberAnimation { target: mark; property: "opacity"; from: 0.3; to: 1; duration: 600; easing.type: Easing.OutQuad }
        onStopped: mark.opacity = 1
    }
}

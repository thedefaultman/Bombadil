import QtQuick
import QtQuick.Shapes
import Bombadil as Kit

// The Bombadil mark in the pill: a stone (a rounded Reuleaux triangle, 18 px wide, resting corner
// up) with a lowercase b cut through it. It says what the machine is doing without a word:
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
Item {
    id: stone

    property string face: "rest"
    // The colour the b is cut in: the surface the stone sits on.
    property color ground: Kit.Theme.glassPill
    // Working pulses instead of rolling, and "needs you" holds its glow still.
    property bool reducedMotion: false

    implicitWidth: 24
    implicitHeight: 24

    readonly property bool rolling: face === "working" || face === "starting"
    readonly property bool drawn: face !== "stopped" && face !== "offline" && face !== "resting"
    readonly property color fill: rolling ? Kit.Theme.accent : face === "needs" ? Kit.Theme.warn : Kit.Theme.good

    // What the stone is painted with. The scene graph drops a colour change made in the first
    // frames after the bar starts: on the VM the face went starting, then rest 39 ms later, and the
    // stone stayed the starting orange until its next change, a hundred seconds on. So for the first
    // `settle` ms the stone is painted resting green whatever its face says, then follows `fill`
    // (a face that is still starting or needing you is painted from then on).
    property int settle: 400
    property color paint: Kit.Theme.good
    Timer { interval: stone.settle; running: true; onTriggered: stone.paint = Qt.binding(() => stone.fill) }

    // The stone: corner radius 2.7, side radius 15.3, top corner centred on (12, 5.7), centroid at
    // (12, 12.975). It rocks on its bottom arc (radius 15.3 about the top corner's centre) to lean
    // and to knock, and turns about its centroid to work.
    readonly property real armR: 7.275        // top corner's centre to the centroid
    readonly property real arcR: 15.3         // what it rocks on
    readonly property real toRad: Math.PI / 180

    // Pose, in degrees. lean follows the face; turn, knock and the hop are written by the animations.
    property real lean: face === "listening" ? 6 : 0
    property real turn: 0
    property real knock: 0
    readonly property real rock: lean + knock
    property real hopY: 0
    property real hopSx: 1
    property real hopSy: 1
    property real glowOpacity: 0.25

    Behavior on lean { NumberAnimation { duration: 240; easing.type: Easing.BezierSpline; easing.bezierCurve: [0.16, 1, 0.3, 1, 1, 1] } }

    // The b rides: it slides with the stone's centre but never tilts.
    readonly property real rideX: arcR * rock * toRad - armR * Math.sin(rock * toRad)
    readonly property real rideY: armR * (Math.cos(rock * toRad) - 1)

    // Needs you: the glow, a filled radial fade under the stone (never a ring). It reaches 16 px from
    // the centroid, 5 px past the stone's edge and past the 24 px slot into the pill's padding: at 11 px
    // it was a 1 px halo that did not read at 1x.
    Shape {
        anchors.fill: parent
        visible: stone.face === "needs"
        opacity: stone.reducedMotion ? 0.45 : stone.glowOpacity
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: 12; centerY: 12.975; centerRadius: 16
                focalX: 12; focalY: 12.975
                GradientStop { position: 0; color: Kit.Theme.warn }
                GradientStop { position: 0.58; color: Kit.Theme.alpha(Kit.Theme.warn, 0.7) }
                GradientStop { position: 1; color: Kit.Theme.alpha(Kit.Theme.warn, 0) }
            }
            PathAngleArc { centerX: 12; centerY: 12.975; radiusX: 16; radiusY: 16; startAngle: 0; sweepAngle: 360 }
        }
    }

    // The mark proper: the hop squashes it about its foot.
    Item {
        id: mark
        width: stone.width; height: stone.height
        visible: stone.drawn
        y: stone.hopY
        transform: Scale { origin.x: 12; origin.y: 21; xScale: stone.hopSx; yScale: stone.hopSy }

        Shape {
            id: rockShape
            width: stone.width; height: stone.height
            x: stone.arcR * stone.rock * stone.toRad
            transform: [
                Rotation { origin.x: 12; origin.y: 5.7; angle: stone.rock },
                Rotation { origin.x: 12; origin.y: 12.975; angle: stone.turn }
            ]
            ShapePath {
                fillColor: stone.paint
                strokeColor: "transparent"
                PathSvg {
                    path: "M10.65 3.362A2.7 2.7 0 0 1 13.35 3.362A15.3 15.3 0 0 1 21 16.612A2.7 2.7 0 0 1 19.65 18.95"
                        + "A15.3 15.3 0 0 1 4.35 18.95A2.7 2.7 0 0 1 3 16.612A15.3 15.3 0 0 1 10.65 3.362Z"
                }
            }
        }

        // The cut: the b drawn in the surface colour, 1.6 px wide, flat-ended, square-cornered.
        Shape {
            width: stone.width; height: stone.height
            x: stone.rideX
            y: stone.rideY
            ShapePath {
                fillColor: "transparent"
                strokeColor: stone.ground
                strokeWidth: 1.6
                capStyle: ShapePath.FlatCap
                joinStyle: ShapePath.MiterJoin
                PathSvg { path: "M7.97 10.96H13.24A3.41 3.41 0 0 1 13.24 17.78H8.59V7.86" }
            }
        }
    }

    // Stopped: the Stop button's own square, muted. Never orange.
    Rectangle {
        visible: stone.face === "stopped"
        x: 6; y: 6; width: 12; height: 12; radius: 2.4
        color: Kit.Theme.muted
    }

    // Resting: the stone as a hollow, grey and still. The outline is the offline one, whole, and a
    // faint grey fill keeps the b legible: it is cut in the ground's colour as on every other face.
    Shape {
        anchors.fill: parent
        visible: stone.face === "resting"
        ShapePath {
            fillColor: Kit.Theme.alpha(Kit.Theme.muted, 0.3)
            strokeColor: Kit.Theme.muted
            strokeWidth: 1.5
            joinStyle: ShapePath.MiterJoin
            PathSvg {
                path: "M10.763 4.082A2.475 2.475 0 0 1 13.238 4.082A14.025 14.025 0 0 1 20.25 16.228A2.475 2.475 0 0 1 19.013 18.371"
                    + "A14.025 14.025 0 0 1 4.988 18.371A2.475 2.475 0 0 1 3.75 16.228A14.025 14.025 0 0 1 10.762 4.082Z"
            }
        }
        Shape {
            anchors.fill: parent
            ShapePath {
                fillColor: "transparent"
                strokeColor: stone.ground
                strokeWidth: 1.6
                capStyle: ShapePath.FlatCap
                joinStyle: ShapePath.MiterJoin
                PathSvg { path: "M7.97 10.96H13.24A3.41 3.41 0 0 1 13.24 17.78H8.59V7.86" }
            }
        }
    }

    // Offline: the outline alone, 1.5 px, six round-capped dashes (one on each corner and each
    // side), and the b still in place as a red line.
    Shape {
        anchors.fill: parent
        visible: stone.face === "offline"
        ShapePath {
            fillColor: "transparent"
            strokeColor: Kit.Theme.bad
            strokeWidth: 1.5
            capStyle: ShapePath.RoundCap
            strokeStyle: ShapePath.DashLine
            // Qt counts dashes in pen widths: 4.125 4.102 4.95 4.102 px, offset 0.767 px.
            dashPattern: [4.125 / 1.5, 4.102 / 1.5, 4.95 / 1.5, 4.102 / 1.5]
            dashOffset: 0.767 / 1.5
            PathSvg {
                path: "M10.763 4.082A2.475 2.475 0 0 1 13.238 4.082A14.025 14.025 0 0 1 20.25 16.228A2.475 2.475 0 0 1 19.013 18.371"
                    + "A14.025 14.025 0 0 1 4.988 18.371A2.475 2.475 0 0 1 3.75 16.228A14.025 14.025 0 0 1 10.762 4.082Z"
            }
        }
        Shape {
            anchors.fill: parent
            ShapePath {
                fillColor: "transparent"
                strokeColor: Kit.Theme.bad
                strokeWidth: 1.6
                capStyle: ShapePath.FlatCap
                joinStyle: ShapePath.MiterJoin
                PathSvg { path: "M7.97 10.96H13.24A3.41 3.41 0 0 1 13.24 17.78H8.59V7.86" }
            }
        }
    }

    // -- motion --

    // Working: hold 300 ms, turn a third of a turn in 550 ms, hold 150 ms. A third of a turn
    // leaves the stone looking as it did, so the next beat starts from 0 unseen.
    SequentialAnimation {
        running: stone.rolling && !stone.reducedMotion
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
        running: stone.rolling && stone.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { target: mark; property: "opacity"; to: Kit.Theme.pulseLow; duration: 500 }
        NumberAnimation { target: mark; property: "opacity"; to: 1; duration: 500 }
        onStopped: mark.opacity = 1
    }

    // Needs you: two knocks tipping 8 degrees to the right, then a wait, on a 1.6 s beat; the
    // glow breathes 0.25 to 0.6 and back on the same beat.
    SequentialAnimation {
        running: stone.face === "needs" && !stone.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { target: stone; property: "knock"; to: 8; duration: 192; easing.type: Easing.OutQuad }
        NumberAnimation { target: stone; property: "knock"; to: 0; duration: 160; easing.type: Easing.InQuad }
        NumberAnimation { target: stone; property: "knock"; to: 8; duration: 160; easing.type: Easing.OutQuad }
        NumberAnimation { target: stone; property: "knock"; to: 0; duration: 160; easing.type: Easing.InQuad }
        PauseAnimation { duration: 928 }
        onStopped: stone.knock = 0
    }
    SequentialAnimation {
        running: stone.face === "needs" && !stone.reducedMotion
        loops: Animation.Infinite
        NumberAnimation { target: stone; property: "glowOpacity"; from: 0.25; to: 0.6; duration: 560; easing.type: Easing.InOutSine }
        NumberAnimation { target: stone; property: "glowOpacity"; to: 0.25; duration: 1040; easing.type: Easing.InOutSine }
        onStopped: stone.glowOpacity = 0.25
    }

    // Done: once, 700 ms: squash to (1.06, 0.9) about the foot, hop 3 px, land squashed to
    // (1.1, 0.84), a small rebound, still. Reduced motion: a fade in from 0.3.
    SequentialAnimation {
        running: stone.face === "done" && !stone.reducedMotion
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
        running: stone.face === "done" && stone.reducedMotion
        NumberAnimation { target: mark; property: "opacity"; from: 0.3; to: 1; duration: 600; easing.type: Easing.OutQuad }
        onStopped: mark.opacity = 1
    }
}

import QtQuick
import QtQuick.Layouts
import Bombadil

// Every chart in realistic states, for `bombadil-app check tests/qml/charts_gallery.qml
// --screenshot out.png --wait 2500`. Set `showHover` to render the hover tooltips.
Rectangle {
    id: gallery

    property bool showHover: false
    property real liveValue: 0.35

    width: 1120
    height: 1160
    color: Theme.bg

    readonly property real gib: 1073741824

    // Deterministic random walk so screenshots are comparable run to run.
    function walk(n, start, step, seed, lo, hi) {
        var out = [], v = start, s = seed
        for (var i = 0; i < n; i++) {
            s = (s * 16807) % 2147483647
            v = Math.max(lo, Math.min(hi, v + (s / 2147483647 - 0.5) * step))
            out.push(v)
        }
        return out
    }
    function ago(i, n) { return i >= n - 1 ? "now" : Fmt.duration(n - 1 - i) + " ago" }

    component Card: Rectangle {
        default property alias content: body.data
        property string title
        property string subtitle
        color: Theme.panel
        radius: Theme.radius
        Layout.fillWidth: true
        ColumnLayout {
            id: body
            anchors.fill: parent
            anchors.margins: Theme.pad
            spacing: Theme.gap
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text { text: title; color: Theme.fg; font.family: Theme.fontFamily; font.pixelSize: Theme.textSize; font.weight: Font.DemiBold }
                Text { text: subtitle; color: Theme.muted; font.family: Theme.fontFamily; font.pixelSize: Theme.captionSize; Layout.fillWidth: true }
            }
        }
    }

    Series { id: live; value: gallery.liveValue; capacity: 60 }
    Timer {
        property int seed: 5
        interval: 80
        running: true
        repeat: true
        onTriggered: {
            seed = (seed * 16807) % 2147483647
            gallery.liveValue = Math.max(0.05, Math.min(0.95, gallery.liveValue + (seed / 2147483647 - 0.5) * 0.12))
        }
    }

    GridLayout {
        anchors.fill: parent
        anchors.margins: 20
        columns: 2
        columnSpacing: 16
        rowSpacing: 16

        Card {
            title: "Memory used"
            subtitle: "last 2 minutes"
            Layout.preferredHeight: 250
            LineChart {
                Layout.fillWidth: true
                Layout.fillHeight: true
                series: [{ name: "Used", values: gallery.walk(120, 9.1 * gallery.gib, 0.35 * gallery.gib, 7, 6 * gallery.gib, 12 * gallery.gib) }]
                format: v => Fmt.bytes(v)
                xFormat: i => gallery.ago(i, 120)
                hoverIndex: gallery.showHover ? 96 : -1
            }
        }
        Card {
            title: "CPU by mode"
            subtitle: "share of all cores"
            Layout.preferredHeight: 250
            LineChart {
                Layout.fillWidth: true
                Layout.fillHeight: true
                series: [
                    { name: "User", values: gallery.walk(120, 0.32, 0.08, 3, 0.04, 0.9) },
                    { name: "System", values: gallery.walk(120, 0.12, 0.05, 11, 0.02, 0.5) },
                    { name: "IO wait", values: gallery.walk(120, 0.05, 0.03, 19, 0, 0.3) }
                ]
                format: v => Fmt.percent(v)
                xFormat: i => gallery.ago(i, 120)
                hoverIndex: gallery.showHover ? 70 : -1
            }
        }

        Card {
            title: "Live load"
            subtitle: "Series, capacity 60, fills from the right"
            Layout.preferredHeight: 250
            LineChart {
                Layout.fillWidth: true
                Layout.fillHeight: true
                series: [{ name: "Load", values: live.values }]
                capacity: live.capacity
                yMax: 1
                format: v => Fmt.percent(v)
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text { text: Fmt.percent(live.last); color: Theme.fg; font.family: Theme.fontFamily; font.pixelSize: Theme.headingSize; font.weight: Font.DemiBold }
                Text { text: "avg " + Fmt.percent(live.average) + " · max " + Fmt.percent(live.max); color: Theme.muted; font.family: Theme.fontFamily; font.pixelSize: Theme.captionSize; Layout.fillWidth: true }
                Sparkline { values: live.values; Layout.preferredWidth: 120; Layout.preferredHeight: 28; format: v => Fmt.percent(v) }
            }
        }
        Card {
            title: "Top processes"
            subtitle: "resident memory"
            Layout.preferredHeight: 250
            BarChart {
                Layout.fillWidth: true
                Layout.fillHeight: true
                format: v => Fmt.bytes(v)
                hoverIndex: gallery.showHover ? 2 : -1
                bars: [
                    { label: "firefox", value: 1.84 * gallery.gib },
                    { label: "code", value: 1.21 * gallery.gib },
                    { label: "gnome-shell", value: 0.62 * gallery.gib },
                    { label: "claude", value: 0.51 * gallery.gib },
                    { label: "Xwayland", value: 0.18 * gallery.gib },
                    { label: "pipewire", value: 0.09 * gallery.gib }
                ]
            }
        }

        Card {
            title: "Pressure"
            subtitle: "Meter at 0.3 / 0.8 / 0.95"
            Layout.preferredHeight: 200
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 16
                Meter { Layout.fillWidth: true; value: 0.3; label: "Memory"; detail: "4.8 of 16 GiB" }
                Meter { Layout.fillWidth: true; value: 0.8; label: "Disk"; detail: "410 of 512 GB" }
                Meter { Layout.fillWidth: true; value: 0.95; label: "Swap"; detail: "7.6 of 8 GiB" }
            }
            Item { Layout.fillHeight: true }
        }
        Card {
            title: "Gauges"
            subtitle: "Ring"
            Layout.preferredHeight: 200
            RowLayout {
                Layout.fillWidth: true
                spacing: 24
                Ring { value: 0.42; label: "CPU"; detail: "8 cores" }
                Ring { value: 0.81; label: "GPU"; detail: "62 °C" }
                Ring { value: 0.96; label: "Disk"; detail: "491 of 512 GB" }
                Ring { value: live.last; label: "Live"; size: 64; thickness: 6 }
                Item { Layout.fillWidth: true }
            }
            Item { Layout.fillHeight: true }
        }

        Card {
            title: "Memory"
            subtitle: "16 GiB total"
            Layout.preferredHeight: 150
            StackedBar {
                Layout.fillWidth: true
                total: 16 * gallery.gib
                format: v => Fmt.bytes(v)
                hoverIndex: gallery.showHover ? 1 : -1
                parts: [
                    { label: "Apps", value: 6.3 * gallery.gib },
                    { label: "Cache", value: 4.1 * gallery.gib },
                    { label: "Buffers", value: 0.6 * gallery.gib }
                ]
            }
            Item { Layout.fillHeight: true }
        }
        Card {
            title: "Trends"
            subtitle: "Sparkline"
            Layout.preferredHeight: 150
            RowLayout {
                Layout.fillWidth: true
                spacing: 24
                Repeater {
                    model: [
                        { name: "Network in", seed: 23, color: Theme.series[0] },
                        { name: "Disk read", seed: 31, color: Theme.series[1] },
                        { name: "Temperature", seed: 47, color: Theme.series[2] }
                    ]
                    delegate: ColumnLayout {
                        required property var modelData
                        required property int index
                        spacing: 4
                        Layout.fillWidth: true
                        Text { text: modelData.name; color: Theme.muted; font.family: Theme.fontFamily; font.pixelSize: Theme.captionSize }
                        Sparkline {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            values: gallery.walk(40, 50, 18, modelData.seed, 5, 100)
                            color: modelData.color
                            area: index !== 2
                            hoverIndex: gallery.showHover && index === 0 ? 30 : -1
                        }
                    }
                }
            }
            Item { Layout.fillHeight: true }
        }

        Card {
            title: "Commits per weekday"
            subtitle: "BarChart, horizontal: false"
            Layout.columnSpan: 2
            Layout.preferredHeight: 190
            BarChart {
                Layout.fillWidth: true
                Layout.fillHeight: true
                horizontal: false
                format: v => Fmt.number(v)
                hoverIndex: gallery.showHover ? 3 : -1
                bars: [
                    { label: "Mon", value: 42 }, { label: "Tue", value: 58 }, { label: "Wed", value: 61 },
                    { label: "Thu", value: 47 }, { label: "Fri", value: 33 }, { label: "Sat", value: 6 },
                    { label: "Sun", value: 3 }
                ]
            }
        }
    }
}

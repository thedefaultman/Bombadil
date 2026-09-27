import QtQuick

// Records a value over time for a chart: bind `value` and hand `values` to a LineChart or
// Sparkline. It samples every `interval` ms, so a flat value still moves the chart along
// and point i of n is always (n - 1 - i) * interval old. Every append makes a new array so
// bindings on `values` update.
QtObject {
    id: root

    property var value
    property int capacity: 120
    property int interval: 1000     // ms between samples; 0 = a point each time `value` changes (or push())
    property var values: []

    readonly property real last: values.length ? values[values.length - 1] : 0
    readonly property real min: values.length ? Math.min.apply(null, values) : 0
    readonly property real max: values.length ? Math.max.apply(null, values) : 0
    readonly property real average: {
        var sum = 0
        for (var i = 0; i < values.length; i++)
            sum += values[i]
        return values.length ? sum / values.length : 0
    }

    function push(v) {
        if (v === undefined || v === null)
            return
        v = Number(v)
        if (!isFinite(v))
            return
        var next = values.slice(Math.max(0, values.length - Math.max(1, capacity) + 1))
        next.push(v)
        values = next
    }

    function clear() { values = [] }

    property Timer _sampler: Timer {
        interval: Math.max(50, root.interval)
        repeat: true
        running: root.interval > 0
        onTriggered: root.push(root.value)
    }

    Component.onCompleted: if (interval > 0) push(value)
    onValueChanged: if (interval <= 0) push(value)
    onCapacityChanged: if (values.length > capacity) values = values.slice(values.length - Math.max(1, capacity))
}

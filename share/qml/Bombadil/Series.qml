import QtQuick

// Records a changing value over time for a chart: bind `value` (or call push()) and
// hand `values` to a LineChart or Sparkline. Every append makes a new array so
// bindings on `values` update.
QtObject {
    id: root

    property var value
    property int capacity: 120
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

    onValueChanged: push(value)
    onCapacityChanged: if (values.length > capacity) values = values.slice(values.length - Math.max(1, capacity))
}

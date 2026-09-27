pragma Singleton
import QtQuick

// Formatting helpers so every app writes sizes, times and counts the same way.
// Missing values (null, undefined, NaN) come back as "—" so a binding never shows "NaN".
QtObject {
    readonly property string none: "—"
    readonly property var months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    function _bad(n) { return n === null || n === undefined || n === "" || !isFinite(Number(n)) }

    function _group(s) {
        const parts = s.split(".")
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",")
        return parts.join(".")
    }

    // Binary units, like the kernel and `free -h`: 1536 -> "1.5 KiB".
    function bytes(n, digits) {
        if (_bad(n))
            return none
        n = Number(n)
        const d = digits === undefined ? 1 : digits
        const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        let v = Math.abs(n), i = 0
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
        if (i > 0 && Number(v.toFixed(d)) >= 1024 && i < units.length - 1) { v /= 1024; i++ }
        const s = i === 0 ? String(Math.round(v)) : v.toFixed(d)
        return (n < 0 ? "-" : "") + s + "\u00a0" + units[i]   // no-break space: "458.2 MiB" never wraps apart
    }

    // x is a fraction: 0.42 -> "42%".
    function percent(x, digits) {
        if (_bad(x))
            return none
        return (Number(x) * 100).toFixed(digits === undefined ? 0 : digits) + "%"
    }

    function number(n, digits) {
        if (_bad(n))
            return none
        return _group(Number(n).toFixed(digits === undefined ? 0 : digits))
    }

    // 12840 -> "12.8K", 4200000 -> "4.2M"; three significant digits at most.
    function compact(n) {
        if (_bad(n))
            return none
        n = Number(n)
        const units = ["", "K", "M", "B", "T"]
        let v = Math.abs(n), i = 0
        while (v >= 999.95 && i < units.length - 1) { v /= 1000; i++ }
        let s = i === 0 && Number.isInteger(v) ? String(v) : v.toFixed(v < 100 ? 1 : 0)
        if (s.endsWith(".0"))
            s = s.slice(0, -2)
        return (n < 0 ? "-" : "") + s + units[i]
    }

    // Seconds to the two largest units: "3d 4h", "12m 5s", "4.2s", "800 ms".
    function duration(seconds) {
        if (_bad(seconds))
            return none
        let s = Math.abs(Number(seconds))
        const sign = Number(seconds) < 0 ? "-" : ""
        if (s < 1)
            return sign + Math.round(s * 1000) + "\u00a0ms"
        if (s < 10)
            return sign + (Math.round(s * 10) / 10) + "s"
        s = Math.round(s)
        const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600)
        const m = Math.floor(s % 3600 / 60), sec = s % 60
        const pair = (a, ua, b, ub) => sign + a + ua + (b ? "\u00a0" + b + ub : "")
        if (d > 0)
            return pair(d, "d", h, "h")
        if (h > 0)
            return pair(h, "h", m, "m")
        if (m > 0)
            return pair(m, "m", sec, "s")
        return sign + sec + "s"
    }

    // A Date, milliseconds, Unix seconds (numbers below 1e11) or an ISO string.
    function toDate(value) {
        if (value === null || value === undefined || value === "")
            return null
        if (value instanceof Date)
            return isNaN(value.getTime()) ? null : value
        if (typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value))) {
            const n = Number(value)
            return new Date(n < 1e11 ? n * 1000 : n)
        }
        const d = new Date(String(value))
        return isNaN(d.getTime()) ? null : d
    }

    function _pad(n) { return n < 10 ? "0" + n : String(n) }

    function time(value) {
        const d = toDate(value)
        return d ? _pad(d.getHours()) + ":" + _pad(d.getMinutes()) : none
    }

    function date(value) {
        const d = toDate(value)
        return d ? d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() : none
    }

    function dateTime(value) {
        const d = toDate(value)
        return d ? date(d) + ", " + time(d) : none
    }

    // "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then the date.
    function relative(value) {
        const d = toDate(value)
        if (!d)
            return none
        const now = new Date()
        const diff = (now.getTime() - d.getTime()) / 1000
        const future = diff < 0
        const s = Math.abs(diff)
        const say = text => future ? "in " + text : text + " ago"
        if (s < 60)
            return "just now"
        if (s < 3600)
            return say(Math.floor(s / 60) + " min")
        const startOf = t => new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()
        const days = Math.round((startOf(now) - startOf(d)) / 86400000)
        if (days === 0)
            return say(Math.floor(s / 3600) + " h")
        if (days === 1)
            return "yesterday"
        if (days === -1)
            return "tomorrow"
        if (Math.abs(days) < 7)
            return future ? "in " + (-days) + " days" : days + " days ago"
        return date(d)
    }
}

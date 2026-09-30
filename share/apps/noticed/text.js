.pragma library

// The window's plain sentences, kept apart from the drawing so each is one small function.

function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many)
}

// "4 times on 3 days"
function times(n, days) {
    return n > 0 ? plural(n, "time", "times") + " on " + plural(Math.max(days, 1), "day", "days") : ""
}

// The line beside the title: "3 ideas · 1 thing it found", or "Nothing waiting" (or "Hidden").
function header(full) {
    if (full.hidden)
        return "Hidden"
    const ideas = full.asks.filter(a => a.offered).length
    const found = full.found.filter(f => f.state === "open" || f.state === "reported").length
    const parts = []
    if (ideas > 0)
        parts.push(plural(ideas, "idea", "ideas"))
    if (found > 0)
        parts.push(plural(found, "thing it found", "things it found"))
    return parts.length > 0 ? parts.join(" · ") : "Nothing waiting"
}

function sentence(s) {
    return s.length > 0 ? s.charAt(0).toUpperCase() + s.slice(1) : s
}

function restingLine(day) {
    return day !== "" ? "Resting offers until " + day + "." : ""
}

// Why the window is showing no lists.
function awayTitle(connected) {
    return connected ? "Bombadil has not answered yet." : "Bombadil is not listening right now."
}

function awayText(connected) {
    return connected ? "Noticed asks again every few seconds."
                     : "Noticed keeps trying and fills in when Bombadil is back."
}

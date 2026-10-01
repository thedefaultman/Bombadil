.pragma library

// What the window says when a view has nothing in it, and the icon for a row of the left column.

function emptyLine(view, searching, waiting) {
    if (searching)
        return "Nothing found."
    if (view === "needs_reply")
        return "Nothing needs a reply."
    if (view === "drafts")
        return "No drafts."
    // an inbox that cannot be read yet is not an empty one (the note on its account says why)
    return waiting ? "Nothing to show yet." : "Your inbox is empty."
}

function viewIcon(row) {
    if (row.kind === "all")
        return "inbox"
    if (row.kind === "account")
        return "mail"
    return row.id === "needs_reply" ? "reply" : "pencil"
}

function kindIcon(kind) {
    return kind === "forward" ? "forward" : kind === "reply_all" ? "reply-all" : "reply"
}

// "3" for a count worth showing, "" for none (a zero is left out).
function countText(n) {
    return n > 0 ? String(n) : ""
}

// The addresses on a draft as one string, for the screen reader's sake and the tests'.
function list(items) {
    return (items || []).join(", ")
}

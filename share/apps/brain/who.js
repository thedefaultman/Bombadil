.pragma library
// Colour says who touched a thing: white for you, orange for the machine's agent, blue for
// coding sessions (the brief's three colours). Everything else stays quiet.

function dot(actor, theme) {
    switch (actor) {
    case "you": return theme.fg
    case "turn": return theme.accent
    case "session": return theme.info
    case "app": return theme.muted
    default: return theme.faint
    }
}

// An icon for each kind of thing the brain knows.
function icon(kind, name) {
    switch (kind) {
    case "folder": case "home": return "folder"
    case "project": return "code"
    case "app": return "layers"
    case "page": case "site": return "globe"
    case "turn": return "bot"
    case "session": return "terminal"
    case "package": return "archive"
    case "fact": return "info"
    case "system": return "settings"
    case "download": return "download"
    }
    const n = String(name || "").toLowerCase()
    if (/\.(py|js|ts|qml|rs|go|c|h|cpp|sh|lua|java|rb|toml|json|ya?ml)$/.test(n)) return "code"
    if (/\.(md|txt|pdf|rst|org|tex|docx?|odt)$/.test(n)) return "file-text"
    return "file"
}

// The Editor's language for a file name, so code reads as code.
function language(name) {
    const m = /\.([a-z0-9]+)$/i.exec(String(name || ""))
    const ext = m ? m[1].toLowerCase() : ""
    switch (ext) {
    case "py": return "python"
    case "json": return "json"
    case "qml": return "qml"
    case "js": case "mjs": case "ts": return "javascript"
    case "sh": case "bash": case "zsh": return "shell"
    case "toml": return "toml"
    case "ini": case "conf": case "cfg": case "service": case "desktop": return "ini"
    case "md": case "markdown": return "markdown"
    }
    if (/^\.?(bashrc|zshrc|profile|bash_profile)$/.test(String(name || ""))) return "shell"
    return "plain"
}

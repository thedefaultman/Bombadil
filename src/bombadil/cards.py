"""Cards: pictures the shell draws from data.

The agent (show_card) or the machine itself (system_map, the launcher's picture words,
receipts) hands the shell a `diagram` card: a shape, up to a dozen boxes and the links between
them. Nothing here draws. This module checks the data, lays the one shape that needs a layout
(layers) out, writes the text twin every picture has (for copying, captions and screen
readers, made from the same data so it cannot disagree with the picture), and reads a card
that is still being written, so the frame and the first boxes can show while the rest streams.

Layout is fixed and never a force layout: the same data always draws the same picture.
"""

import json
import re
from pathlib import Path

SHAPES = ("chain", "layers", "compare", "timeline")
STATES = ("ok", "warn", "bad", "new", "gone", "active")
SIDES = ("before", "after")
OPENS = ("path", "unit", "package", "url", "turn")
SOURCES = ("network", "boot", "service", "disks", "sound", "screens")   # what the machine itself drew

MAX_NODES = 12
MAX_LINKS = 16
MAX_TITLE = 60
MAX_LABEL = 32
MAX_SUB = 60
MAX_SAY = 160
MAX_LINK_LABEL = 24

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,40}$")
_UNIT_RE = re.compile(r"^[\w@.:\\-]{1,120}\.(service|socket|timer|target|mount|path|slice|scope|device|swap)$")
_PACKAGE_RE = re.compile(r"^[a-z0-9@._+-]{1,80}$")


def icon_names() -> set[str]:
    """The kit's icons: what a node's `icon` may name."""
    for share in (Path(__file__).resolve().parents[2] / "share", Path("/usr/share/bombadil/share")):
        d = share / "qml" / "Bombadil" / "icons"
        if d.is_dir():
            return {p.stem for p in d.glob("*.svg")}
    return set()


def _text(v, limit: int) -> str:
    return " ".join(str(v).split())[:limit]


def _cut(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def check_opens(opens) -> tuple[dict | None, str | None]:
    """A box opens the thing it names: (the cleaned target, or an error)."""
    if not isinstance(opens, dict):
        return None, "opens must be {kind, value}"
    kind, value = opens.get("kind"), opens.get("value")
    if kind not in OPENS:
        return None, f"opens.kind must be one of {', '.join(OPENS)}"
    value = str(value if value is not None else "").strip()
    if kind == "path" and not (value.startswith(("/", "~/")) or value == "~"):
        return None, "opens.value for a path must be absolute or start with ~/"
    if kind == "unit" and not _UNIT_RE.match(value):
        return None, "opens.value for a unit must look like NetworkManager.service"
    if kind == "package" and not _PACKAGE_RE.match(value):
        return None, "opens.value for a package must be a package name"
    if kind == "url" and not re.match(r"^https?://[^\s]+$", value):
        return None, "opens.value for a url must start with http:// or https://"
    if kind == "turn" and not re.fullmatch(r"\d{1,9}", value):
        return None, "opens.value for a turn must be its number"
    return {"kind": kind, "value": value}, None


def validate_diagram(spec: dict) -> tuple[dict | None, list[str]]:
    """The card for this diagram input, or the errors to fix (each names what and why).

    Adds what the shell needs and the model should not have to write: ids, the links of a chain,
    the rank of each box in layers, and the row of each box in compare."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return None, ["the diagram must be an object"]
    shape = spec.get("shape")
    if shape not in SHAPES:
        errors.append(f"shape must be one of {', '.join(SHAPES)}")
    nodes_in = spec.get("nodes")
    if not isinstance(nodes_in, list) or not nodes_in:
        errors.append("nodes must be a list with at least one box")
        nodes_in = []
    if len(nodes_in) > MAX_NODES:
        errors.append(f"{len(nodes_in)} nodes; a picture holds at most {MAX_NODES}. Group some, or leave the "
                      "detail for a window")
    icons = icon_names()
    nodes: list[dict] = []
    seen: set[str] = set()
    for i, n in enumerate(nodes_in[:MAX_NODES]):
        at = f"nodes[{i}]"
        if not isinstance(n, dict):
            errors.append(f"{at} must be an object")
            continue
        label = _text(n.get("label", ""), 500)
        if not label:
            errors.append(f"{at}.label is empty")
        elif len(label) > MAX_LABEL:
            errors.append(f"{at}.label is {len(label)} characters; the limit is {MAX_LABEL} "
                          f"(put the rest in sub): {label[:40]!r}")
        nid = str(n.get("id") or f"n{i + 1}")
        if not _ID_RE.match(nid):
            errors.append(f"{at}.id {nid!r} may use letters, digits and _ . : @ / - only, up to 40")
        if nid in seen:
            errors.append(f"{at}.id {nid!r} is used twice")
        seen.add(nid)
        node: dict = {"id": nid, "label": _cut(label, MAX_LABEL)}
        sub = _text(n.get("sub", ""), 500)
        if sub:
            if len(sub) > MAX_SUB:
                errors.append(f"{at}.sub is {len(sub)} characters; the limit is {MAX_SUB}")
            node["sub"] = _cut(sub, MAX_SUB)
        state = n.get("state")
        if state not in (None, ""):
            if state not in STATES:
                errors.append(f"{at}.state must be one of {', '.join(STATES)}")
            else:
                node["state"] = state
        icon = n.get("icon")
        if icon:
            if icons and icon not in icons:
                errors.append(f"{at}.icon {icon!r} is not a kit icon (see references/components.md, Icons)")
            else:
                node["icon"] = str(icon)
        if n.get("side") not in (None, ""):
            if n["side"] not in SIDES:
                errors.append(f"{at}.side must be before or after")
            else:
                node["side"] = n["side"]
        if n.get("time") not in (None, ""):
            node["time"] = _text(n["time"], 24)
        if n.get("weight") not in (None, ""):
            try:
                node["weight"] = max(0.0, float(n["weight"]))
            except (TypeError, ValueError):
                errors.append(f"{at}.weight must be a number")
        note = _text(n.get("note", ""), 500)
        if note:
            node["note"] = _cut(note, MAX_SUB)
        if n.get("key"):
            node["key"] = _text(n["key"], 40)
        if n.get("volatile"):
            node["volatile"] = True
        if n.get("opens") is not None:
            opens, err = check_opens(n["opens"])
            if err:
                errors.append(f"{at}.{err}")
            elif opens:
                node["opens"] = opens
        nodes.append(node)

    if shape == "compare":
        for i, n in enumerate(nodes):
            if "side" not in n:
                errors.append(f"nodes[{i}].side is required in a compare picture: before or after")
    ids = {n["id"] for n in nodes}
    links_in = spec.get("links")
    links: list[dict] = []
    if links_in is not None and not isinstance(links_in, list):
        errors.append("links must be a list")
        links_in = []
    for i, ln in enumerate(links_in or []):
        at = f"links[{i}]"
        if not isinstance(ln, dict):
            errors.append(f"{at} must be an object")
            continue
        a, b = str(ln.get("from", "")), str(ln.get("to", ""))
        if a not in ids or b not in ids:
            errors.append(f"{at} joins {a!r} to {b!r}; both must be node ids ({', '.join(sorted(ids)[:12])})")
            continue
        link: dict = {"from": a, "to": b}
        if ln.get("label"):
            label = _text(ln["label"], 500)
            if len(label) > MAX_LINK_LABEL:
                errors.append(f"{at}.label is {len(label)} characters; the limit is {MAX_LINK_LABEL}")
            link["label"] = _cut(label, MAX_LINK_LABEL)
        if ln.get("state") in STATES:
            link["state"] = ln["state"]
        links.append(link)
    if len(links) > MAX_LINKS:
        errors.append(f"{len(links)} links; a picture holds at most {MAX_LINKS}")
        links = links[:MAX_LINKS]

    title = _text(spec.get("title", ""), 500)
    if not title:
        errors.append("title is required: what the picture shows, in a few words")
    elif len(title) > MAX_TITLE:
        errors.append(f"title is {len(title)} characters; the limit is {MAX_TITLE}")
    say = _text(spec.get("say", ""), 1000)
    if len(say) > MAX_SAY:
        errors.append(f"say is {len(say)} characters; the limit is {MAX_SAY}. One sentence")
    highlight = spec.get("highlight")
    highlight = [highlight] if isinstance(highlight, str) else (highlight if isinstance(highlight, list) else [])
    for h in highlight:
        if h not in ids:
            errors.append(f"highlight {h!r} is not a node id")
    if errors:
        return None, errors

    if shape == "chain" and not links and spec.get("linked", True) is not False:
        links = [{"from": a["id"], "to": b["id"]} for a, b in zip(nodes, nodes[1:])]
    if shape == "layers":
        nodes = rank_layers(nodes, links)
    if shape == "compare":
        keys: dict[str, int] = {}
        for n in nodes:
            n["row"] = keys.setdefault(n.get("key") or n["id"], len(keys))
    card = {"type": "diagram", "shape": shape, "title": _cut(title, MAX_TITLE), "nodes": nodes, "links": links,
            "highlight": [str(h) for h in highlight]}
    if say:
        card["say"] = _cut(say, MAX_SAY)
    card["text"] = text_of(card)
    return card, []


def accept(card: dict) -> tuple[dict | None, list[str]]:
    """A card from another process (os-mcp's show_card and system_map): checked again here, so
    nothing malformed reaches the bar whoever sent it. Keeps what only the OS itself adds."""
    if not isinstance(card, dict):
        return None, ["the card must be an object"]
    out, errors = validate_diagram(card)
    if out is None:
        return None, errors
    if card.get("source") in SOURCES:
        out["source"] = card["source"]
    target = card.get("target")
    if isinstance(target, str) and re.fullmatch(r"[\w@.:-]{1,80}", target):
        out["target"] = target
    if card.get("receipt") is True:
        out["receipt"] = True
    return out, []


# -- layout: layers --

def rank_layers(nodes: list[dict], links: list[dict]) -> list[dict]:
    """Give each box a `rank` (its row, from the top: a box sits below everything that points
    at it) and a `col` (its place in the row, ordered to keep links from crossing). About 100
    lines rather than a layout engine: a picture is at most a dozen boxes, and the same input
    must always draw the same picture."""
    ids = [n["id"] for n in nodes]
    out: dict[str, list[str]] = {i: [] for i in ids}
    for ln in links:
        if ln["to"] not in out[ln["from"]] and ln["from"] != ln["to"]:
            out[ln["from"]].append(ln["to"])
    # Break cycles by dropping the edges that close one (depth-first, in the order given).
    state: dict[str, int] = {}
    edges: dict[str, list[str]] = {i: [] for i in ids}

    def visit(u: str):
        state[u] = 1
        for v in out[u]:
            if state.get(v) == 1:
                continue   # back edge: leaves the picture's order alone
            edges[u].append(v)
            if v not in state:
                visit(v)
        state[u] = 2

    for i in ids:
        if i not in state:
            visit(i)
    rank = {i: 0 for i in ids}
    for _ in range(len(ids)):   # longest path from a source
        changed = False
        for u in ids:
            for v in edges[u]:
                if rank[v] < rank[u] + 1:
                    rank[v] = rank[u] + 1
                    changed = True
        if not changed:
            break
    rows: dict[int, list[str]] = {}
    for i in ids:
        rows.setdefault(rank[i], []).append(i)
    parents: dict[str, list[str]] = {i: [] for i in ids}
    for u in ids:
        for v in edges[u]:
            parents[v].append(u)
    pos = {i: k for r in rows.values() for k, i in enumerate(r)}
    for _ in range(4):   # barycenter sweeps: each box moves toward the middle of its parents
        for r in sorted(rows)[1:]:
            rows[r].sort(key=lambda i: (sum(pos[p] for p in parents[i]) / len(parents[i]) if parents[i] else pos[i],
                                        pos[i]))
            for k, i in enumerate(rows[r]):
                pos[i] = k
    by_id = {n["id"]: n for n in nodes}
    for r, members in rows.items():
        for k, i in enumerate(members):
            by_id[i]["rank"], by_id[i]["col"] = r, k
    return nodes


# -- the text twin --

def text_of(card: dict) -> str:
    """The picture in words, from the same data: for copying, captions and screen readers."""
    nodes = card.get("nodes") or []
    by_id = {n["id"]: n for n in nodes}
    shape = card.get("shape")

    def name(n: dict, state: bool = True) -> str:
        s = n["label"] + (f" ({n['sub']})" if n.get("sub") else "")
        mark = {"warn": " [slow or weak]", "bad": " [broken]", "new": " [new]", "gone": " [gone]"}.get(n.get("state"), "")
        return s + (mark if state else "")

    lines: list[str] = []
    if shape == "chain":
        lines.append(" → ".join(name(n) for n in nodes) if card.get("links") else "; ".join(name(n) for n in nodes))
    elif shape == "layers":
        for n in sorted(nodes, key=lambda n: (n.get("rank", 0), n.get("col", 0))):
            needs = [by_id[ln["to"]]["label"] for ln in card.get("links") or [] if ln["from"] == n["id"]]
            lines.append(name(n) + (f" needs {', '.join(needs)}" if needs else ""))
    elif shape == "compare":
        for side in SIDES:
            items = [name(n) for n in sorted((n for n in nodes if n.get("side") == side), key=lambda n: n.get("row", 0))]
            lines.append(f"{side.capitalize()}: " + ("; ".join(items) if items else "nothing"))
    elif shape == "timeline":
        for n in nodes:
            when = f"{n['time']}: " if n.get("time") else ""
            lines.append(when + name(n))
    body = "\n".join(lines) if shape in ("layers", "timeline", "compare") else "".join(lines)
    text = f"{card.get('title', '')}: {body}" if shape == "chain" else f"{card.get('title', '')}\n{body}"
    if card.get("say"):
        text += f"\n{card['say']}"
    return text.strip()


# -- a card still being written --

_STR = r'"((?:[^"\\]|\\.)*)"'


def _complete_objects(text: str, key: str) -> list[dict]:
    """The finished `{...}` items of the array `key` in JSON that may stop anywhere."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not m:
        return []
    out: list[dict] = []
    depth, start, in_str, esc = 0, -1, False, False
    for i in range(m.end(), len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    out.append(obj)
                start = -1
            elif depth < 0:
                break
        elif c == "]" and depth == 0:
            break
    return out


def partial_diagram(text: str) -> dict | None:
    """What show_card's input says so far: its frame and each finished box. None until it has a
    title or a box. Boxes are shown as written (validation and the layout come with the whole
    card); a box the shell cannot draw yet is left out."""
    out: dict = {}
    for key in ("shape", "title"):
        m = re.search(rf'"{key}"\s*:\s*{_STR}', text)
        if m:
            try:
                out[key] = json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                pass
    nodes = []
    for n in _complete_objects(text, "nodes")[:MAX_NODES]:
        label = _text(n.get("label", ""), MAX_LABEL)
        if label:
            nodes.append({"id": str(n.get("id") or f"n{len(nodes) + 1}"), "label": label,
                          **({"sub": _text(n["sub"], MAX_SUB)} if n.get("sub") else {}),
                          **({"state": n["state"]} if n.get("state") in STATES else {}),
                          **({"side": n["side"]} if n.get("side") in SIDES else {})})
    if not out.get("title") and not nodes:
        return None
    return {"type": "diagram", "shape": out.get("shape") if out.get("shape") in SHAPES else "chain",
            "title": _cut(_text(out.get("title", ""), 500), MAX_TITLE), "nodes": nodes, "links": [],
            "highlight": [], "partial": True}


class CardStream:
    """Follows show_card calls as Claude writes them (tool_start, tool_input). `feed` returns the
    card so far when it grew: a new title, shape or finished box."""

    def __init__(self):
        self._calls: dict[int, dict] = {}

    def feed(self, ev: dict) -> dict | None:
        kind = ev.get("kind")
        idx = ev.get("index", 0)
        if kind == "tool_start":
            name = str(ev.get("name") or "")
            if name.endswith("__show_card"):
                self._calls[idx] = {"json": "", "seen": None, "id": ev.get("id")}
            return None
        call = self._calls.get(idx)
        if kind != "tool_input" or call is None:
            return None
        call["json"] += ev.get("partial", "")
        card = partial_diagram(call["json"])
        if card is None:
            return None
        seen = (card["shape"], card["title"], len(card["nodes"]))
        if seen == call["seen"]:
            return None
        call["seen"] = seen
        card["id"] = f"stream-{call['id'] or idx}"
        return card

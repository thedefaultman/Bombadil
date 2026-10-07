"""Cards: pictures the shell draws from data.

The agent (show_card) or the machine itself (system_map, the launcher's picture words,
receipts) hands the shell a `diagram` card: a shape, up to a dozen boxes and the links between
them. Nothing here draws. This module checks the data, lays the one shape that needs a layout
(layers) out, writes the text twin every picture has (for copying, captions and screen
readers, made from the same data so it cannot disagree with the picture), and reads a card
that is still being written, so the frame and the first boxes can show while the rest streams.

Layout is fixed and never a force layout: the same data always draws the same picture.

Three more kinds come from connections, never from the agent's drawing tool: a `message` (the last
messages of a thread and a reply box), a `task` (a work item with boxes for what is sent) and a
`list` (rows with a mark and up to two buttons). They are made by agentd (connect/cardmake.py) and
checked here again, whoever made them, so nothing malformed reaches the bar. Other people's words
are in them: every text is cut to its limit and made plain (no control, invisible or
direction-changing characters). The one exception is the content of a reply, which is what would be
sent: it is not altered but refused when it has such characters or is too long, because a box that
shows something other than what would go must never light the Send button. Keys that are not part
of a kind's shape are dropped. Where a card says something the picture also says (the source word,
the time of a message, the label of the button), it is worked out here once, so the picture and the
text twin are made from the same words.
"""

import json
import math
import re
import time
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


# -- the kinds that come from connections: message, task, list --

KINDS = ("message", "task", "list")
CARD_SOURCES = ("connect", "notification", "mail")
REPLY_KINDS = ("slack_reply", "task_create", "task_comment", "copy")     # `copy` is no send: see the shell
PROPOSAL_STATES = ("open", "sending", "sent", "unknown", "discarded")
WARNING_KINDS = {
    "new_place": "You have never received a message from here.",
    "broadcast": "This reaches everyone in the channel.",
    "long": "This is a long message.",
    "tainted": "I wrote this after reading what other people sent. Read it before you press Send.",
}
EDITS = ("line", "text", "date")
ACTIONS = ("open", "disconnect", "connect", "copy_open", "dismiss")     # what a button may ask agentd for
ROW_STYLES = ("primary", "quiet")
BUTTONS = {"slack_reply": "Send", "copy": "Copy and open", "task_create": "Create", "task_comment": "Comment"}
VIA = {"slack": "Slack", "linear": "Linear", "notion": "Notion", "jira": "Jira", "todoist": "Todoist",
       "clickup": "ClickUp"}

MAX_CARD_TITLE = 80
MAX_THREAD = 6
MAX_ROWS = 12
MAX_ROW_ACTIONS = 2
MAX_FIELDS = 12
MAX_WARNINGS = 4
MAX_FROM = 120
MAX_MESSAGE = 4000
MAX_CONTENT = 20_000        # the box of a reply or of a task field: past this a card is refused, never cut
MAX_FIELD_TEXT = 5_000
MAX_SENTENCE = 240          # a warning, a note, a receipt line, a task's `why`
MAX_WHERE = 80
MAX_ROW_TITLE = 80

_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+=-]{1,200}$")   # a card or a row: as a diagram's id, long enough for a ref
_REF_RE = re.compile(r"^[a-z]{2,12}:[A-Za-z0-9_.:@/+=-]{1,187}$")
_PROPOSAL_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
_FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9_-]{0,128}$")
# Not \t or \n, which a message may hold; every other control, the line and paragraph separators, and lone surrogates.
_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f  \ud800-\udfff]")
_CONTROL_LINE = re.compile("[\x00-\x1f\x7f-\x9f  \ud800-\udfff]")
# Invisible padding, and the marks that reorder text on screen: a name that has them can read as another name.
_INVISIBLE = re.compile("[­͏؜᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")


def _line(v, limit: int) -> str:
    """Somebody else's words as one plain line, cut at `limit` characters with an ellipsis."""
    s = _INVISIBLE.sub("", _CONTROL_LINE.sub(" ", str(v if v is not None else "")))
    return _cut(" ".join(s.split()), limit)


def _block(v, limit: int) -> str:
    """Somebody else's words with their line breaks (one blank line at most), cut like `_line`."""
    s = _INVISIBLE.sub("", _CONTROL.sub(" ", str(v if v is not None else ""))).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(ln.split()) for ln in s.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return _cut("\n".join(out).strip(), limit)


def _typed(v, limit: int, at: str, errors: list[str]) -> str:
    """What would be sent, as it is: refused (not cut, not cleaned) when it is not plain or too long."""
    s = str(v if v is not None else "")
    if _INVISIBLE.search(s) or _CONTROL.search(s) or "\r" in s:
        errors.append(f"{at} has control, invisible or direction-changing characters; it is what would be sent, "
                      "so it cannot be made plain here")
        return ""
    if len(s) > limit:
        errors.append(f"{at} is {len(s)} characters; the limit is {limit}")
        return ""
    return s


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return float(v)


def _when(ts: float, now: float | None = None) -> str:
    """When a message was said, short: the time today, the day and time within a week, else the date."""
    now = time.time() if now is None else now
    then, today = time.localtime(ts), time.localtime(now)
    if then[:3] == today[:3]:
        return time.strftime("%H:%M", then)
    if 0 <= now - ts < 6 * 86400:
        return time.strftime("%a %H:%M", then)
    return time.strftime("%d %b", then).lstrip("0")


def _via(card: dict) -> str:
    """The source word the card's head shows, from where it came."""
    source = card.get("source")
    if source == "notification":
        return (card.get("reply") or {}).get("where") or "Notification"
    if source == "mail":
        return "Mail"
    if card.get("type") == "task":
        return VIA.get(card.get("service", ""), str(card.get("service", "")).capitalize())
    if card.get("type") == "list":
        return "Connections"
    return "Slack"


def _common(spec: dict, kind: str, errors: list[str]) -> dict:
    out: dict = {"type": kind}
    cid = spec.get("id")
    if not isinstance(cid, str) or not _ITEM_ID_RE.match(cid):
        errors.append("id must be a string of letters, digits and _ . : @ / + = - only, up to 200")
    else:
        out["id"] = cid
    title = _line(spec.get("title"), MAX_CARD_TITLE)
    if not title:
        errors.append("title is required: who or what this is about, in a few words")
    out["title"] = title
    if spec.get("source") not in CARD_SOURCES:
        errors.append(f"source must be one of {', '.join(CARD_SOURCES)}")
    else:
        out["source"] = spec["source"]
    say = _line(spec.get("say"), MAX_SAY)
    if say:
        out["say"] = say
    return out


def _open(spec: dict, errors: list[str]) -> dict | None:
    """Where the card's own "Open" goes: the message or the ticket on the service's page."""
    if spec.get("open") is None:
        return None
    opens, err = check_opens(spec["open"])
    if err:
        errors.append(f"open: {err}")
    return opens


def _warnings(raw, at: str, errors: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"{at} must be a list")
        return []
    out: list[dict] = []
    for i, w in enumerate(raw[:MAX_WARNINGS]):
        kind, text = (w, "") if isinstance(w, str) else ((w.get("kind"), w.get("text")) if isinstance(w, dict)
                                                          else (None, ""))
        if kind not in WARNING_KINDS:
            errors.append(f"{at}[{i}].kind must be one of {', '.join(WARNING_KINDS)}")
            continue
        out.append({"kind": kind, "text": _line(text, MAX_SENTENCE) or WARNING_KINDS[kind]})
    return out


def _receipt(raw, at: str, errors: list[str]) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append(f"{at} must be {{line, web?}} or null")
        return None
    line = _line(raw.get("line"), MAX_SENTENCE)
    if not line:
        errors.append(f"{at}.line is empty")
    out: dict = {"line": line}
    web = raw.get("web")
    if web is not None:
        target, err = check_opens({"kind": "url", "value": web.get("url") if isinstance(web, dict) else None})
        if err or not isinstance(web, dict):
            errors.append(f"{at}.web must be {{name, url}} with an http or https address")
        else:
            out["web"] = {"name": _line(web.get("name"), 40) or "the web", "url": target["value"]}
    return out


def _reply(raw, at: str, kinds: tuple[str, ...], errors: list[str], task: bool = False) -> dict | None:
    """The `reply` of a message card or the `proposal` of a task card: where a draft stands."""
    if not isinstance(raw, dict):
        errors.append(f"{at} must be an object")
        return None
    out: dict = {}
    kind = raw.get("kind")
    if kind not in kinds:
        errors.append(f"{at}.kind must be one of {', '.join(kinds)}")
    out["kind"] = kind
    prop = raw.get("proposal")
    if prop in (None, ""):
        out["proposal"] = None
    elif not isinstance(prop, str) or not _PROPOSAL_RE.match(prop):
        errors.append(f"{at}.proposal must be a proposal id (letters, digits, _ and - only, up to 40) or null")
    else:
        out["proposal"] = prop
    target = str(raw.get("target") or "")
    if not _ITEM_ID_RE.match(target) and target:
        errors.append(f"{at}.target {target[:40]!r} is not a ref")
    out["target"] = target
    out["where"] = _line(raw.get("where"), MAX_WHERE)
    content = raw.get("content", {} if task else "")
    if task:
        if not isinstance(content, dict) or len(content) > MAX_FIELDS:
            errors.append(f"{at}.content must be an object of at most {MAX_FIELDS} field values")
            content = {}
        fixed: dict = {}
        for key, value in content.items():
            if not isinstance(key, str) or not _KEY_RE.match(key):
                errors.append(f"{at}.content has the key {str(key)[:40]!r}; a key starts with a letter and uses "
                              "letters, digits and _ . - only")
            elif isinstance(value, bool) or not isinstance(value, (str, int, float, type(None))):
                errors.append(f"{at}.content.{key} must be text")
            else:
                fixed[key] = _typed(value, MAX_FIELD_TEXT, f"{at}.content.{key}", errors)
        out["content"] = fixed
    else:
        if not isinstance(content, str):
            errors.append(f"{at}.content must be text")
            content = ""
        out["content"] = _typed(content, MAX_CONTENT, f"{at}.content", errors)
    out["warnings"] = _warnings(raw.get("warnings"), f"{at}.warnings", errors)
    fingerprint = raw.get("fingerprint") or ""
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.match(fingerprint):
        errors.append(f"{at}.fingerprint must be the proposal's fingerprint")
        fingerprint = ""
    out["fingerprint"] = fingerprint
    out["ready"] = raw.get("ready") is True
    state = raw.get("state") or "open"
    if state not in PROPOSAL_STATES:
        errors.append(f"{at}.state must be one of {', '.join(PROPOSAL_STATES)}")
        state = "open"
    out["state"] = state
    by = raw.get("by") or "person"
    if by not in ("person", "agent"):
        errors.append(f"{at}.by must be person or agent")
        by = "person"
    out["by"] = by
    out["receipt"] = _receipt(raw.get("receipt"), f"{at}.receipt", errors)
    out["note"] = _line(raw.get("note"), MAX_SENTENCE)
    out["button"] = BUTTONS.get(kind, "")
    return out


def validate_message(spec, now: float | None = None) -> tuple[dict | None, list[str]]:
    """The message card for this input, or the errors to fix (each names what and why)."""
    if not isinstance(spec, dict):
        return None, ["the card must be an object"]
    errors: list[str] = []
    card = _common(spec, "message", errors)
    thread_in = spec.get("thread")
    if thread_in is None:
        thread_in = []
    if not isinstance(thread_in, list):
        errors.append("thread must be a list")
        thread_in = []
    first = max(0, len(thread_in) - MAX_THREAD)      # the last six: a longer thread is cut at its start
    thread: list[dict] = []
    for i, m in enumerate(thread_in[first:], start=first):
        at = f"thread[{i}]"
        if not isinstance(m, dict):
            errors.append(f"{at} must be an object")
            continue
        ref = m.get("ref")
        if not isinstance(ref, str) or not _REF_RE.match(ref):
            errors.append(f"{at}.ref must be a message ref like slack:T1/C1/1700000000.000100")
        ts = _number(m.get("ts"))
        if ts is None:
            errors.append(f"{at}.ts must be the time in seconds since 1970")
            ts = 0.0
        thread.append({"ref": ref if isinstance(ref, str) else "", "from": _line(m.get("from"), MAX_FROM) or "Someone",
                       "text": _block(m.get("text"), MAX_MESSAGE), "ts": ts, "when": _when(ts, now),
                       "mine": m.get("mine") is True, "unread": m.get("unread") is True})
    card["thread"] = thread
    reply = None
    if spec.get("reply") is not None:
        reply = _reply(spec["reply"], "reply", ("slack_reply", "copy"), errors)
    card["reply"] = reply
    card["open"] = _open(spec, errors)
    if errors:
        return None, errors
    card["via"] = _via({**card, "type": "message"})
    card["text"] = text_of(card)
    return card, []


def validate_task(spec, now: float | None = None) -> tuple[dict | None, list[str]]:
    """The task card for this input, or the errors to fix. The proposal is `proposal` (a `reply` is taken as one)."""
    if not isinstance(spec, dict):
        return None, ["the card must be an object"]
    errors: list[str] = []
    card = _common(spec, "task", errors)
    service = _line(spec.get("service"), 40)
    if not service:
        errors.append("service is required: linear, notion, jira, todoist or clickup")
    card["service"] = service
    card["status"] = _line(spec.get("status"), 60)
    card["why"] = _line(spec.get("why"), MAX_SENTENCE)
    fields_in = spec.get("fields")
    if fields_in is None:
        fields_in = []
    if not isinstance(fields_in, list) or len(fields_in) > MAX_FIELDS:
        errors.append(f"fields must be a list of at most {MAX_FIELDS} boxes")
        fields_in = []
    fields: list[dict] = []
    seen: set[str] = set()
    for i, f in enumerate(fields_in):
        at = f"fields[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{at} must be an object")
            continue
        key = f.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            errors.append(f"{at}.key must start with a letter and use letters, digits and _ . - only, up to 40")
        elif key in seen:
            errors.append(f"{at}.key {key!r} is used twice")
        seen.add(str(key))
        label = _line(f.get("label"), 40)
        if not label:
            errors.append(f"{at}.label is empty")
        edit = f.get("edit")
        if edit not in (None, *EDITS):
            errors.append(f"{at}.edit must be one of {', '.join(EDITS)} or null")
            edit = None
        value = f.get("value")
        fields.append({"key": key if isinstance(key, str) else "", "label": label,
                       "value": (_block(value, MAX_FIELD_TEXT) if edit == "text" else _line(value, MAX_FIELD_TEXT))
                       if value is not None else "", "edit": edit})
    card["fields"] = fields
    raw = spec.get("proposal", spec.get("reply"))
    if raw is None:
        errors.append("proposal is required: where the draft stands (kind task_create or task_comment)")
        card["proposal"] = None
    else:
        card["proposal"] = _reply(raw, "proposal", ("task_create", "task_comment"), errors, task=True)
    card["open"] = _open(spec, errors)
    if errors:
        return None, errors
    card["via"] = _via(card)
    card["text"] = text_of(card)
    return card, []


def _row_opens(opens, at: str, errors: list[str]) -> dict | None:
    if opens is None:
        return None
    if isinstance(opens, dict) and opens.get("kind") == "card":
        value = str(opens.get("value") if opens.get("value") is not None else "")
        if not _ITEM_ID_RE.match(value):
            errors.append(f"{at}.opens.value for a card must be a card or row id")
            return None
        return {"kind": "card", "value": value}
    target, err = check_opens(opens)
    if err:
        errors.append(f"{at}.{err}")
    return target


def validate_list(spec, now: float | None = None) -> tuple[dict | None, list[str]]:
    """The list card for this input, or the errors to fix."""
    if not isinstance(spec, dict):
        return None, ["the card must be an object"]
    errors: list[str] = []
    card = _common(spec, "list", errors)
    rows_in = spec.get("rows")
    if rows_in is None:
        rows_in = []
    if not isinstance(rows_in, list):
        errors.append("rows must be a list")
        rows_in = []
    if len(rows_in) > MAX_ROWS:
        errors.append(f"{len(rows_in)} rows; a list holds at most {MAX_ROWS}")
    rows: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(rows_in[:MAX_ROWS]):
        at = f"rows[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{at} must be an object")
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not _ITEM_ID_RE.match(rid):
            errors.append(f"{at}.id must be a string of letters, digits and _ . : @ / + = - only, up to 200")
        elif rid in seen:
            errors.append(f"{at}.id {rid!r} is used twice")
        seen.add(str(rid))
        title = _line(r.get("title"), MAX_ROW_TITLE)
        if not title:
            errors.append(f"{at}.title is empty")
        state = r.get("state") or "ok"
        if state not in STATES:
            errors.append(f"{at}.state must be one of {', '.join(STATES)}")
            state = "ok"
        row: dict = {"id": rid if isinstance(rid, str) else "", "title": title, "sub": _line(r.get("sub"), MAX_SENTENCE),
                     "when": _line(r.get("when"), 24), "state": state, "actions": [], "opens": None}
        actions_in = r.get("actions") or []
        if not isinstance(actions_in, list) or len(actions_in) > MAX_ROW_ACTIONS:
            errors.append(f"{at}.actions must be a list of at most {MAX_ROW_ACTIONS} buttons")
            actions_in = []
        for j, a in enumerate(actions_in):
            if not isinstance(a, dict) or a.get("id") not in ACTIONS:
                errors.append(f"{at}.actions[{j}].id must be one of {', '.join(ACTIONS)}")
                continue
            label = _line(a.get("label"), 24)
            if not label:
                errors.append(f"{at}.actions[{j}].label is empty")
            style = a.get("style") or "quiet"
            if style not in ROW_STYLES:
                errors.append(f"{at}.actions[{j}].style must be primary or quiet")
                style = "quiet"
            row["actions"].append({"id": a["id"], "label": label, "style": style})
        row["opens"] = _row_opens(r.get("opens"), at, errors)
        rows.append(row)
    card["rows"] = rows
    if errors:
        return None, errors
    card["via"] = _via(card)
    card["text"] = text_of(card)
    return card, []


VALIDATORS = {"message": validate_message, "task": validate_task, "list": validate_list}


def accept(card: dict, *, now: float | None = None, diagrams_only: bool = False) -> tuple[dict | None, list[str]]:
    """A card from another process (os-mcp's show_card and system_map, agentd's connection cards): checked
    again here, so nothing malformed reaches the bar whoever sent it. A message, task or list card is
    checked as its kind; any other is a diagram, which keeps what only the OS itself adds.

    `diagrams_only` is for a card that arrives on agentd's socket from a client: any process of the
    person's can write there, the agent's shell included, and a message card in the agent's hands would
    be words made to look like somebody else's. agentd's own connection cards are not refused."""
    if not isinstance(card, dict):
        return None, ["the card must be an object"]
    if card.get("type") in VALIDATORS:
        if diagrams_only:
            return None, ["Messages, tasks and lists come from your connections, not from show_card."]
        return VALIDATORS[card["type"]](card, now)
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

UNKNOWN_LINE = "I can't tell whether that went. Look in {via} before you press again."
LIST_STATE_WORDS = {"warn": "needs a look", "bad": "not working", "new": "new", "gone": "gone", "active": "in use"}


def state_line(reply: dict, via: str) -> str:
    """What a draft's state says under its box: nothing while it is open."""
    state = reply.get("state")
    if state == "sending":
        return "Sending…"
    if state == "sent":
        return (reply.get("receipt") or {}).get("line") or "Sent."
    if state == "unknown":
        return UNKNOWN_LINE.format(via=via)
    return ""


def _draft_lines(reply: dict, via: str) -> list[str]:
    lines = [w["text"] for w in reply.get("warnings") or []]
    line = state_line(reply, via)
    if line:
        lines.append(line)
    if reply.get("note"):
        lines.append(reply["note"])
    if reply.get("button"):
        lines.append(f"Button: {reply['button']}")
    return lines


def _twin_message(card: dict) -> str:
    via = card.get("via", "")
    lines = [f"{card.get('title', '')} ({via})"]
    for m in card.get("thread") or []:
        who = "You" if m.get("mine") else m["from"]
        lines.append(f"{who}, {m['when']}{', unread' if m.get('unread') else ''}: {m['text']}")
    if card.get("say"):
        lines.append(card["say"])
    reply = card.get("reply")
    if reply:
        if reply.get("content"):
            lines.append(f"Reply: {reply['content']}")
        lines += _draft_lines(reply, via)
    if card.get("open") and not (reply and reply.get("kind") == "copy"):
        lines.append(f"Open in {via}")
    return "\n".join(lines).strip()


def _twin_task(card: dict) -> str:
    via = card.get("via", "")
    proposal = card.get("proposal") or {}
    content = proposal.get("content") or {}
    lines = [f"{card.get('title', '')} ({via})"]
    if card.get("status"):
        lines.append(f"Status: {card['status']}")
    if card.get("why"):
        lines.append(card["why"])
    for f in card.get("fields") or []:
        value = content[f["key"]] if f["edit"] and f["key"] in content else f["value"]
        if value or f["edit"]:   # a box with nothing in it is still a box; a read-only row with nothing is not drawn
            lines.append(f"{f['label']}: {value or '(empty)'}")
    if card.get("say"):
        lines.append(card["say"])
    lines += _draft_lines(proposal, via)
    if card.get("open"):
        lines.append(f"Open in {via}")
    return "\n".join(lines).strip()


def _twin_list(card: dict) -> str:
    lines = [card.get("title", "")]
    for r in card.get("rows") or []:
        line = r["title"] + (f" ({r['sub']})" if r.get("sub") else "")
        if r.get("when"):
            line += f", {r['when']}"
        if LIST_STATE_WORDS.get(r.get("state")):
            line += f" [{LIST_STATE_WORDS[r['state']]}]"
        if r.get("actions"):
            line += ": " + ", ".join(a["label"] for a in r["actions"])
        lines.append(line)
    if card.get("say"):
        lines.append(card["say"])
    return "\n".join(lines).strip()


_TWINS = {"message": _twin_message, "task": _twin_task, "list": _twin_list}


def text_of(card: dict) -> str:
    """The picture in words, from the same data: for copying, captions and screen readers. A message,
    task or list card is told as accepted: what a draft says about itself later (sending, sent) is said
    by the card's own lines."""
    if card.get("type") in _TWINS:
        return _TWINS[card["type"]](card)
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

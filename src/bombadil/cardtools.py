"""The picture tools os-mcp gives the agent: show_card (a diagram it fills in) and system_map
(a diagram the machine draws from itself).

Both end the same way: the finished card goes to agentd over its socket, which sends it to the
bar, and the agent gets the picture back in words (the card's text twin), so it writes one line
about it instead of a description.

Both draw diagrams and nothing else. The other kinds of card (a message with its reply box, a
task, a list) belong to the person's connections: agentd makes them from what the connections
hold, and the agent has no tool that draws one, so it cannot put words in a place that looks like
somebody else's message.
"""

import json
import os
import socket

from . import cards, config, paths, sysmap

ACK_TIMEOUT = 2.0


def deliver(card: dict, timeout: float = ACK_TIMEOUT) -> tuple[bool, str]:
    """Hand a card to agentd: (shown, what to tell the agent when it was not)."""
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(str(paths.socket_path()))
    except OSError:
        return False, "agentd is not running, so nothing could be drawn."
    with s:
        try:
            s.sendall((json.dumps({"type": "card", "card": card}) + "\n").encode())
            with s.makefile("rb") as f:
                while line := f.readline():   # agentd greets a new client with its status first
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, dict) and msg.get("type") == "card_ack":
                        if msg.get("errors"):
                            return False, "; ".join(str(e) for e in msg["errors"])
                        if not msg.get("shown"):
                            return False, "No screen is showing it: here it is in words."
                        return True, ""
        except (OSError, ValueError):
            pass
    return False, "agentd did not answer, so it may not have been drawn."


def provider_name() -> str:
    name = os.environ.get("BOMBADIL_PROVIDER")
    if not name:
        try:
            name = config.load().provider
        except (ValueError, OSError):
            name = "claude"
    return name


NODE = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Only needed for links and highlight. Letters, digits and _ . : - @ /."},
        "label": {"type": "string", "description": "At most 32 characters."},
        "sub": {"type": "string", "description": "A second line, at most 60 characters."},
        "state": {"type": "string", "enum": list(cards.STATES),
                  "description": "ok is the usual look, warn amber, bad red, new orange (what just appeared), gone dim "
                                 "(what is no longer there), active lit."},
        "icon": {"type": "string", "description": "A kit icon name (references/components.md, Icons)."},
        "side": {"type": "string", "enum": list(cards.SIDES), "description": "compare only."},
        "key": {"type": "string", "description": "compare only: the same key on both sides puts them on one row."},
        "time": {"type": "string", "description": "timeline only: when, as text (\"0.4 s\", \"9:30\")."},
        "weight": {"type": "number", "description": "timeline only: how long it lasts, in any one unit."},
        "note": {"type": "string", "description": "A line under the box, at most 60 characters."},
        "opens": {"type": "object", "description": "What a click on the box opens.",
                  "properties": {"kind": {"type": "string", "enum": list(cards.OPENS)}, "value": {"type": "string"}},
                  "required": ["kind", "value"]},
    },
    "required": ["label"],
}
LINK = {
    "type": "object",
    "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                   "label": {"type": "string", "description": "At most 24 characters."},
                   "state": {"type": "string", "enum": list(cards.STATES)}},
    "required": ["from", "to"],
}

SHOW_CARD = (
    "Show a picture above the bar: boxes and lines drawn by the OS. Use it when an answer has parts, order "
    "or change and the user would rather see it than read it (how a VPN works, what depends on what, what "
    "changed, what happens when). Shapes: chain (a path, left to right; linked in order unless you give "
    "links), layers (a box sits below what points at it: give links from a box to what it needs), compare "
    "(every box has side before or after; the same key on both sides puts them on one row), timeline (boxes "
    "with a time). At most 12 boxes and 16 links, labels up to 32 characters, one plain sentence in `say`. "
    "Highlight the box to look at. It draws diagrams only (messages, tasks and lists come from the person's "
    "connections). For THIS machine's network, boot, a service, disks, sound or screens use "
    "system_map instead: it reads the real machine, and you must never draw its state from memory. After "
    "the picture say one short line, not a description of it."
)
SYSTEM_MAP = (
    "Draw a picture of a part of this machine, captured from the real machine in a fraction of a second "
    "(no model involved): network (this laptop, Wi-Fi or cable, the router, the internet and the AI "
    "provider, the first broken link in red), boot (what starts when the machine boots, the slow step in "
    "amber), service (one service and what it needs; give target), disks (disks, partitions, where they are "
    "mounted and how full), sound (which app plays to which speaker) or screens (the monitors). Use it for "
    "\"how am I connected\", \"why is boot slow\", \"where did my disk go\", \"what does bluetooth need\". "
    "highlight names a box id to point at (optional); say adds one sentence under the picture (optional, "
    "plain words, at most 160 characters). The result is the picture in words: add one short line, don't "
    "repeat it."
)


def register(tools) -> None:
    t = tools._tool

    @t("show_card", SHOW_CARD, {
        "kind": {"type": "string", "enum": ["diagram"], "default": "diagram"},
        "shape": {"type": "string", "enum": list(cards.SHAPES)},
        "title": {"type": "string", "description": "What the picture shows, in a few words (at most 60 characters)."},
        "nodes": {"type": "array", "items": NODE, "maxItems": cards.MAX_NODES},
        "links": {"type": "array", "items": LINK, "maxItems": cards.MAX_LINKS},
        "highlight": {"type": "array", "items": {"type": "string"}, "description": "Ids of the boxes to point at."},
        "say": {"type": "string", "description": "One plain sentence under the picture (at most 160 characters)."},
        "linked": {"type": "boolean", "description": "chain only: false for boxes with no arrows between them."},
    }, ["shape", "title", "nodes"])
    def show_card(a):
        kind = a.get("kind", "diagram")
        if kind != "diagram":
            raise ValueError(f"kind {kind!r} is not drawn here: only diagram is. "
                             "Messages, tasks and lists come from your connections, not from show_card.")
        card, errors = cards.validate_diagram(a)
        if card is None:
            raise ValueError("The picture was not drawn:\n- " + "\n- ".join(errors))
        shown, note = deliver(card)
        return card["text"] + ("\n(shown above the bar)" if shown else f"\n({note})")

    @t("system_map", SYSTEM_MAP, {
        "kind": {"type": "string", "enum": list(sysmap.KINDS)},
        "target": {"type": "string", "description": "service only: the unit, like bluetooth or NetworkManager."},
        "highlight": {"type": "string", "description": "The id of a box to point at."},
        "say": {"type": "string", "description": "One sentence under the picture."},
    }, ["kind"])
    def system_map(a):
        try:
            res = sysmap.capture(str(a.get("kind", "")), str(a.get("target") or ""), provider=provider_name())
        except sysmap.Unavailable as e:
            return str(e)
        card = sysmap.apply_overrides(res["card"], a.get("highlight"), a.get("say"))
        shown, note = deliver(card)
        return card["text"] + ("\n(shown above the bar)" if shown else f"\n({note})")

"""The cards of messages and connections, as the dicts the bar draws (docs/CONNECT.md, "Cards").

Four kinds of thing become a card: a message and its thread (`message`), a notification (a `message` card whose
reply is a copy), a task (`task`), and the lists (`list`: unread messages, and Connected here). Everything here is a
pure function from what the connection service, the mail service or a proposal said to a plain dict: nothing reads a
socket, a clock it was not given, or a file.

Why it is shaped this way:

- Other people's words arrive made plain by the service already, and are made plain again and cut here, because the
  card is the last thing between them and the screen. Nothing the person might take for Bombadil's own sentence has
  another person's words in it: `say` lines are built from the person's own side of things (a first name, a place).
- A card's id is stable per thing (`msg:<ref>`, `task:<ref>`, `list:unread`, `list:connected`), so showing the same
  thing again swaps the card in place. A ref that does not fit the bar's id alphabet (or its 200 characters) is
  named by a short hash instead, and agentd keeps the way back (it never reads an id to find a ref).
- A card with no proposal yet has the box all the same, with `reply.proposal` null and not ready: the person's first
  keystroke or press on Reply makes the proposal, and the card is sent again with it. A card made from a
  notification has a `copy` reply instead: nothing in it can be sent.
- The `reply` block is built from the proposal as it is told to the shell (proposals.wire), so a card drawn late
  says what the proposal says now.
"""

import hashlib
import re
import time

from .protocol import one_line, plain_text, web_url

MAX_TITLE = 80
MAX_SAY = 160
THREAD_ROWS = 6
THREAD_TEXT = 1_200
MAX_ROWS = 12
ROW_TITLE, ROW_SUB, ROW_WHEN = 60, 100, 24
MAX_FIELDS = 12             # a task card's boxes and rows together, as cards.py allows
_CARD_ID = re.compile(r"[A-Za-z0-9_.:@/+=-]{1,200}")   # as cards.py takes them
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,39}")

SERVICE_TITLES = {"slack": "Slack", "linear": "Linear", "notion": "Notion", "jira": "Jira", "todoist": "Todoist",
                  "clickup": "ClickUp"}
OFFERS = {"slack": "Direct messages, mentions and threads.", "linear": "Issues assigned to you.",
          "notion": "Pages and tasks that mention you.", "jira": "Issues assigned to you.",
          "todoist": "Tasks assigned to you and due soon.", "clickup": "Tasks assigned to you."}
CONNECTION_STATES = {"ok": "ok", "signin": "active", "setup": "active", "blocked": "warn", "error": "bad",
                     "off": "gone"}
ACCOUNT_STATES = {"ok": "ok", "syncing": "active", "signin": "active", "blocked": "warn", "error": "bad"}
# The boxes of a task card when the service has not said which it has.
DEFAULT_FIELDS = {"task_create": [{"key": "title", "label": "Title", "edit": "line"},
                                  {"key": "description", "label": "Description", "edit": "text"}],
                  "task_comment": [{"key": "body", "label": "Comment", "edit": "text"}]}
EDITS = ("line", "text", "date")
UNREAD_ID, CONNECTED_ID = "list:unread", "list:connected"


# -- words --

def card_id(prefix: str, key: str) -> str:
    """`msg:<ref>` and the like, or a short hash of the ref when it does not fit the bar's ids."""
    plain = f"{prefix}:{key}"
    return plain if _CARD_ID.fullmatch(plain) else f"{prefix}:{hashlib.sha256(str(key).encode()).hexdigest()[:16]}"


def who_of(message: dict) -> str:
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    return one_line(sender.get("name"), 60) or "Someone"


def first_name(name: str) -> str:
    words = one_line(name, 60).split()
    return words[0].strip(",") if words else "someone"


def where_of(message: dict) -> str:
    """Where a message was said, as a person says it: #launch, a direct message, a group message."""
    conv = message.get("conversation") if isinstance(message.get("conversation"), dict) else {}
    name = one_line(conv.get("name"), 60)
    kind = conv.get("kind")
    if kind in ("channel", "private"):
        return (name if name.startswith("#") else f"#{name}") if name else "a channel"
    return "a group message" if kind == "group" else "a direct message"


def service_title(service) -> str:
    return SERVICE_TITLES.get(service) or one_line(service or "that service", 30).capitalize()


def connection_title(conn: dict) -> str:
    """"Slack in Acme" for a workspace, "Linear" for a tool."""
    service, name = conn.get("service"), one_line(conn.get("name"), 40)
    base = service_title(service)
    return f"{base} in {name}" if service == "slack" and name else base


def preview(text, limit: int) -> str:
    """A message as one line, cut with an ellipsis."""
    line = one_line(text, 10_000)
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def when_of(ts, now: float | None = None) -> str:
    """A time as it is said beside a row: 11:04 today, Mon 11:04 before."""
    try:
        t = float(ts)
        local, today = time.localtime(t), time.localtime(time.time() if now is None else now)
        return time.strftime("%H:%M" if local[:3] == today[:3] else "%a %H:%M", local)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _say(text: str) -> str:
    return one_line(text, MAX_SAY)


# -- the proposal's side --

def reply_block(proposal: dict, where: str = "") -> dict:
    """The `reply` of a card, from a proposal as the shell is told it (proposals.wire)."""
    return {"proposal": proposal["id"], "kind": proposal["kind"], "target": proposal["target"],
            "where": one_line(proposal.get("where") or where, 80), "content": proposal.get("content", ""),
            "warnings": [{"kind": str(w.get("kind", "")), "text": one_line(w.get("text"), 300)}
                         for w in proposal.get("warnings") or [] if isinstance(w, dict)],
            "fingerprint": proposal.get("fingerprint", ""), "ready": proposal.get("ready") is True,
            "state": proposal["state"], "by": proposal.get("created_by", "person"),
            "receipt": proposal.get("receipt"), "note": one_line(proposal.get("note"), 300)}


def idle_block(kind: str, target: str, where: str) -> dict:
    """A box nobody has opened a proposal for yet: it has a kind and a target and no id, and cannot be sent."""
    return {"proposal": None, "kind": kind, "target": target, "where": one_line(where, 80),
            "content": "" if kind == "slack_reply" else {}, "warnings": [], "fingerprint": "", "ready": False,
            "state": "open", "by": "person", "receipt": None, "note": ""}


def refreshed(card: dict, proposal: dict | None) -> dict:
    """The card as it should be drawn now, given where its proposal has got to."""
    if not proposal or not isinstance(card, dict):
        return card
    key = "reply" if card.get("type") == "message" else "proposal"
    block = card.get(key)
    if not isinstance(block, dict) or block.get("proposal") != proposal.get("id"):
        return card
    return {**card, key: reply_block(proposal, block.get("where", ""))}


# -- messages --

def _thread_row(m: dict) -> dict:
    return {"ref": str(m.get("ref") or ""), "from": who_of(m), "text": plain_text(m.get("text"), THREAD_TEXT),
            "ts": float(m["ts"]) if isinstance(m.get("ts"), (int, float)) and not isinstance(m["ts"], bool) else 0.0,
            "mine": m.get("mine") is True, "unread": m.get("unread") is True}


def message_card(messages: list[dict], ref: str | None = None, proposal: dict | None = None) -> dict:
    """A message and the last six of its thread (oldest first), with the reply box: the proposal's, or none yet.
    `ref` is the message the person is answering (the last one when not told). Raises ValueError for no messages."""
    thread = [m for m in messages if isinstance(m, dict) and isinstance(m.get("ref"), str) and m["ref"]]
    if not thread:
        raise ValueError("a message card needs a message")
    target = next((m for m in thread if m["ref"] == ref), thread[-1])
    where = where_of(target)
    who = who_of(target)
    if proposal:
        reply = reply_block(proposal, where)
        say = f"Reply in {where}. Sending is yours." if where.startswith("#") else \
            f"Reply to {first_name(who)}. Sending is yours."
    else:
        reply = idle_block("slack_reply", target["ref"], where)
        say = f"{first_name(who)} wrote in {where}."
    url = web_url(target.get("web_url"))
    return {"type": "message", "id": card_id("msg", target["ref"]), "title": one_line(f"{who} in {where}", MAX_TITLE),
            "source": "connect", "thread": [_thread_row(m) for m in thread[-THREAD_ROWS:]], "reply": reply,
            "open": {"kind": "url", "value": url} if url else None, "say": _say(say)}


def notification_card(seq: int, app: str, summary: str, body: str, now: float) -> dict:
    """A notification as a card whose reply is a copy: Bombadil can show it and hand its words to the page that sent
    it, and cannot answer it."""
    app = one_line(app, 40) or "the app"
    who = one_line(summary, 60) or app
    return {"type": "message", "id": card_id("msg", f"n{seq}"), "title": one_line(f"{who} in {app}", MAX_TITLE),
            "source": "notification",
            "thread": [{"ref": f"notify:{seq}", "from": who, "text": plain_text(body or summary, THREAD_TEXT),
                        "ts": float(now), "mine": False, "unread": True}],
            "reply": {"proposal": None, "kind": "copy", "where": app, "content": ""}, "open": None,
            "say": _say(f"Copy and open takes your words to {app}.")}


# -- tasks --

def field_spec(kind: str, task_fields) -> list[dict]:
    """The boxes a proposal of this kind has: what the service said (`task_fields`), else a title and a description
    for a new task and one box for a comment."""
    part = "create" if kind == "task_create" else "comment"
    given = task_fields.get(part) if isinstance(task_fields, dict) else None
    spec = []
    for f in given if isinstance(given, list) else []:
        if isinstance(f, dict) and one_line(f.get("key"), 40):
            spec.append({"key": one_line(f["key"], 40), "label": one_line(f.get("label") or f["key"], 40),
                         "edit": f.get("edit") if f.get("edit") in EDITS else "line"})
    return spec[:MAX_FIELDS] or [dict(f) for f in DEFAULT_FIELDS[kind]]


def _row(key: str, label: str, value, edit) -> dict:
    return {"key": key, "label": one_line(label, 40), "value": plain_text(value, 2_000), "edit": edit}


def task_card(*, service: str, task: dict | None = None, proposal: dict | None = None,
              task_fields: dict | None = None) -> dict:
    """A task that waits on the person (`task`) with the box for a comment under it, or the form of a new task (a
    `task_create` proposal and no task). One button: Create or Comment. Before any proposal the box is there and
    empty, as a message card's reply is, and the person's first keystroke makes the proposal."""
    title_of = service_title(service)
    kind = proposal["kind"] if proposal else "task_comment"
    creating = kind == "task_create"
    ref = (task or {}).get("ref")
    target = (proposal or {}).get("target") or ref or service
    spec = field_spec(kind, task_fields)
    values = proposal.get("content") if proposal and isinstance(proposal.get("content"), dict) else {}
    boxes = [_row(f["key"], f["label"], values.get(f["key"], ""), f["edit"]) for f in spec]
    taken = {f["key"] for f in spec}
    # What the task says about itself comes first, and never takes a key a box needs. Anything a proposal holds
    # for a field that has no box is shown as it is: it would go, so the person has to see it.
    about = [("why", "Why", (task or {}).get("why")), ("status", "Status", (task or {}).get("status")),
             ("due", "Due", (task or {}).get("due"))]
    about += [(f.get("key"), f.get("label"), f.get("value")) for f in (task or {}).get("fields") or []
              if isinstance(f, dict)]
    about += [(k, k.capitalize(), v) for k, v in values.items() if k not in taken and isinstance(v, str)]
    rows = []
    for key, label, value in about:
        key = one_line(key, 40)
        if _KEY.fullmatch(key) and key not in taken and plain_text(value, 120):
            taken.add(key)
            rows.append(_row(key, label, value, None))
    title = f"New task in {title_of}" if creating else one_line((task or {}).get("title") or
                                                                f"A task in {title_of}", MAX_TITLE)
    url = web_url((task or {}).get("url"))
    say = (f"New task in {title_of}. Sending is yours." if creating else
           f"Comment on this in {title_of}. Sending is yours." if proposal else
           f"This waits on you in {title_of}.")
    return {"type": "task", "id": card_id("task", f"new:{service}" if creating else target), "title": title,
            "source": "connect", "service": service, "why": one_line((task or {}).get("why"), 80),
            "status": one_line((task or {}).get("status"), 60), "fields": (rows[:MAX_FIELDS - len(boxes)] + boxes),
            "proposal": reply_block(proposal, title_of) if proposal else idle_block(kind, target, title_of),
            "open": {"kind": "url", "value": url} if url else None, "say": _say(say)}


# -- lists --

def list_card(key: str, title: str, rows: list[dict], say: str) -> dict:
    return {"type": "list", "id": key, "title": one_line(title, MAX_TITLE), "source": "connect",
            "rows": rows[:MAX_ROWS], "say": _say(say)}


def unread_card(messages: list[dict], now: float | None = None) -> dict:
    """The unread messages, newest first, each opening its own message card."""
    rows = []
    for m in [m for m in messages if isinstance(m, dict) and isinstance(m.get("ref"), str)][:MAX_ROWS]:
        rows.append({"id": card_id("msg", m["ref"]), "title": one_line(f"{who_of(m)} in {where_of(m)}", ROW_TITLE),
                     "sub": preview(m.get("text"), ROW_SUB), "when": when_of(m.get("ts"), now),
                     "state": "new" if m.get("unread") is not False else "ok", "actions": [],
                     "opens": {"kind": "card", "value": card_id("msg", m["ref"])}})
    count = len(rows)
    return list_card(UNREAD_ID, "Unread messages", rows,
                     f"{count} unread message{'s' if count != 1 else ''}, newest first.")


def row_for(key: str, title: str, sub: str, state: str, action: str, label: str) -> dict:
    return {"id": key, "title": one_line(title, ROW_TITLE), "sub": one_line(sub, ROW_SUB), "when": "", "state": state,
            "actions": [{"id": action, "label": label, "style": "quiet"}], "opens": None}


def connected_card(connections: list[dict], accounts: list[dict] | None = None, say: str = "") -> dict:
    """"Connected here": each mail account, each Slack workspace and each tool, with what it reads, how it is and a
    Disconnect; and a quiet Connect for each service that is not connected yet."""
    rows = []
    for a in accounts or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        state = a.get("state") if a.get("state") in ACCOUNT_STATES else "error"
        unread = a.get("unread") if isinstance(a.get("unread"), int) and not isinstance(a.get("unread"), bool) else 0
        sub = "Reads your mail" + (f", {unread} unread." if unread else ".") if state == "ok" else \
            one_line(a.get("note"), ROW_SUB) or "Not working yet."
        rows.append(row_for(f"mail:{one_line(a['id'], 30)}", a.get("email") or a.get("name") or "Mail account", sub,
                            ACCOUNT_STATES[state], "disconnect", "Disconnect"))
    live = set()
    for c in connections:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        state = c.get("state") if c.get("state") in CONNECTION_STATES else "error"
        if state != "off":
            live.add(c.get("service"))
        steps = c.get("steps") if isinstance(c.get("steps"), list) else []
        step = next((s.get("say") for s in steps if isinstance(s, dict) and s.get("say")), "")
        sub = one_line(c.get("reads"), ROW_SUB) if state == "ok" else \
            one_line(c.get("note") or step, ROW_SUB) or "Not working yet."
        rows.append(row_for(f"conn:{one_line(c['id'], 34)}", connection_title(c), sub, CONNECTION_STATES[state],
                            "disconnect", "Disconnect"))
    for service, title in SERVICE_TITLES.items():
        if service not in live:
            rows.append(row_for(f"add:{service}", title, OFFERS[service], "gone", "connect", "Connect"))
    return list_card(CONNECTED_ID, "Connected here", rows,
                     say or "This is what I read for you. Disconnect any of it and I stop at once.")


__all__ = ["card_id", "connected_card", "connection_title", "field_spec", "first_name",
           "idle_block", "list_card", "message_card", "notification_card", "preview", "refreshed", "reply_block",
           "service_title", "task_card", "unread_card", "when_of", "where_of", "who_of"]

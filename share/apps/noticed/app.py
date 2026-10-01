"""The Noticed window's line to agentd.

Newline-delimited JSON over agentd's Unix socket, the way the kit's Agent does it, with the loop's own
messages (docs/LOOP.md): it asks for the lists (`noticed_list`), hands what arrives (`noticed_full`) to
the QML already cleaned, and sends what he presses (`noticed_do`). Read from the event loop, so there is
no thread to stop when the app reloads, and nothing here can block the window.

Everything that comes from agentd is untrusted text: one wrong message, row or field costs that row (or
one line in the log), never the window. `check` does not connect.
"""

import json
import math
import os
import sys
import time

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

from bombadil import paths

TICK_MS = 3000       # while agentd is away, or has not answered yet, ask or connect again this often
ASK_AFTER_MS = 150   # a burst of messages makes one request: it is sent this long after the first
ASK_GAP_MS = 500     # and two requests are never closer than this
ECHO_S = 2.0         # a `noticed` like the last one, this soon after a request, is that request's echo
ANSWER_S = 10.0      # a tap that has had no answer by now stops waiting
MAX_ROWS = 50        # rows drawn per list
MAX_BUFFER = 16 << 20
OFFLINE = "Bombadil is not listening right now."
NO_ANSWER = "Bombadil did not answer."

# What a form is called in a sentence: agentd says the id ("word") or the letter ("A").
_FORM_IDS = {"word": "a word", "card": "a card", "widget": "a widget", "app": "an app", "routine": "a routine",
             "watcher": "a watcher", "preference": "a preference"}
FORMS = {**_FORM_IDS, **dict(zip("ABCDEFG", _FORM_IDS.values(), strict=True))}
WAITING = ("open", "reported")   # a found row in these states still waits for him
QUIET = ("other_ways", "not_now", "never")   # a row's `others` that are not other ways of making it


# -- cleaning what agentd sends --

def _text(v, limit: int = 300) -> str:
    """A string from anything, one line, cut at `limit` (0: whole, as it came)."""
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    s = " ".join(str(v).split()) if limit else str(v)
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def _id(v) -> str:
    return "" if v is None or isinstance(v, (dict, list, bool)) else str(v).strip()


def _int(v) -> int:
    if isinstance(v, bool):
        return 0
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _bool(v, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return default


def _when(v) -> float:
    """Epoch seconds from a number or a numeric string; 0 when it is neither."""
    if isinstance(v, bool):
        return 0.0
    try:
        t = float(v)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return t if math.isfinite(t) and 0 < t < 1e11 else 0.0   # NaN, negative or milliseconds read as unknown


def _rows(v) -> list[dict]:
    return [r for r in v if isinstance(r, dict)][:MAX_ROWS] if isinstance(v, list) else []


def _strings(v, limit: int = 300) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [s for s in (_text(x, limit) for x in v) if s] if isinstance(v, list) else []


def _resting(v) -> str:
    """The day offers rest until, as "3 Nov": it comes as that, a whole line, an ISO date or epoch seconds."""
    if isinstance(v, bool) or v is None or isinstance(v, (dict, list)):
        return ""
    s = str(v).strip()
    if s.lower().startswith("resting offers until"):
        return s[len("resting offers until"):].strip(" .")
    t = _when(s)
    if t:
        lt = time.localtime(t)
        return f"{lt.tm_mday} {time.strftime('%b', lt)}"
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        try:
            lt = time.strptime(s[:10], "%Y-%m-%d")
            return f"{lt.tm_mday} {time.strftime('%b', lt)}"
        except ValueError:
            return ""
    return s[:40]


def _way(v) -> dict | None:
    """A button agentd describes: {"label", "op", "form"?}; None when it lacks a label or an op."""
    if not isinstance(v, dict):
        return None
    label, op = _text(v.get("label"), 40), _text(v.get("op"), 40)
    return {"label": label, "op": op, "form": _text(v.get("form"), 40)} if label and op else None


def _preview(v) -> dict:
    """What a report would send: {"text", "goes", "stays"}, or {} when there is nothing to show. The
    text is kept exactly as it came: the card must show what would go, not a shortened copy."""
    if isinstance(v, str):
        v = {"text": v}
    if not isinstance(v, dict):
        return {}
    text = _text(v.get("text"), 0)
    goes, stays = _strings(v.get("goes"), 400), _strings(v.get("stays"), 400)
    return {"text": text, "goes": goes, "stays": stays} if text.strip() or goes or stays else {}


def _ask(r: dict) -> dict | None:
    gid = _id(r.get("id") or r.get("group"))
    title = _text(r.get("title") or r.get("label"))
    sentences = _strings(r.get("sentences"))
    title = title or (sentences[0] if sentences else "")
    if not gid or not title:
        return None
    state = _text(r.get("state"), 20)
    # A button only where an offer waits. The row's own says what it will do; a row that says nothing
    # gets the card's default.
    primary = None
    if state == "offered":
        primary = _way(r.get("primary")) or {"label": "Make it", "op": "accept", "form": ""}
    # The other forms it could become: the row's own list of forms without the recommended one, else
    # its `others` that make something. At most two, as on the card.
    ways = []
    for f in _rows(r.get("forms")):
        form, label = _text(f.get("form"), 40), _text(f.get("label"), 40)
        if form and label and not _bool(f.get("recommended")) and form != (primary or {}).get("form"):
            ways.append({"label": label, "op": "accept", "form": form})
    if not ways:
        ways = [w for w in map(_way, _rows(r.get("others"))) if w and w["op"] not in QUIET]
    return {
        "id": gid, "title": title, "state": state, "offered": primary is not None,
        "n": _int(r.get("n")), "days": _int(r.get("days")), "last": _when(r.get("last")),
        "sentences": [s for s in sentences if s != title][:2],
        "became": _text(r.get("became"), 60), "what": _text(r.get("what"), 400),
        "primary": primary or {}, "ways": ways[:2],
    }


def _change(r: dict) -> dict | None:
    cid, title = _id(r.get("id")), _text(r.get("title"))
    if not cid or not title:
        return None
    undone = _bool(r.get("undone"))
    return {"id": cid, "title": title, "t": _when(r.get("t")), "what": _text(r.get("what"), 20),
            "undone": undone, "can_undo": _bool(r.get("can_undo"), True) and not undone}


def _found(r: dict) -> dict | None:
    fid, title = _id(r.get("id") or r.get("fp")), _text(r.get("title"))
    if not fid or not title:
        return None
    return {"id": fid, "title": title, "meta": _text(r.get("meta"), 80), "fp": _text(r.get("fp"), 80),
            "state": _text(r.get("state"), 20) or "open", "can_send": _bool(r.get("can_send")),
            "why": _strings(r.get("why"))[:6], "preview": _preview(r.get("preview"))}


def _said(r: dict) -> dict | None:
    sid = _id(r.get("id"))
    title = _text(r.get("title") or r.get("label") or r.get("sentence"))
    if not sid or not title:
        return None
    form = _text(r.get("form"), 20)
    return {"id": sid, "title": title, "t": _when(r.get("t")), "form": FORMS.get(form, "")}


def _opens(v) -> str:
    name = v.get("name") if isinstance(v, dict) else v
    name = _text(name, 60).replace("-", " ").replace("_", " ")
    return name[:1].upper() + name[1:]


def _word(r: dict) -> dict | None:
    phrase = _text(r.get("phrase"), 80)
    if not phrase:
        return None
    return {"id": phrase, "phrase": phrase, "opens": _opens(r.get("opens")), "away": _bool(r.get("away"))}


def clean_full(msg: dict) -> dict:
    """A `noticed_full` as the QML wants it: every list present, every row with an id and a title, every
    field of the type it is read as. Rows that cannot be acted on or told apart are left out."""
    def rows(key, make):
        return [x for x in map(make, _rows(msg.get(key))) if x]

    return {
        "hidden": _bool(msg.get("hidden")), "held": _bool(msg.get("held")),
        "resting": _resting(msg.get("resting")),
        "asks": rows("asks", _ask),
        # Newest first, whatever order they came in; one with no time stays below the ones that have it.
        "changes": sorted(rows("changes", _change), key=lambda c: -c["t"]), "found": rows("found", _found),
        "said_no": sorted(rows("said_no", _said), key=lambda c: -c["t"]), "words": rows("words", _word),
    }


def clean_result(msg: dict) -> tuple[str, str, bool, str, dict]:
    """A `noticed_result` as (op, id, ok, text, preview). Like the bar, it is ok unless it says it is not."""
    return (_text(msg.get("op"), 40), _id(msg.get("id")), msg.get("ok") is not False,
            _text(msg.get("text"), 400), _preview(msg.get("preview")))


_warned: dict[str, float] = {}


def _warn(key: str, text: str) -> None:
    """One line in the app's log, at most once a minute for each kind of trouble."""
    now = time.monotonic()
    if now - _warned.get(key, -1e9) < 60:
        return
    _warned[key] = now
    try:
        print(f"{time.strftime('%H:%M:%S')} noticed: {text}", file=sys.stderr, flush=True)
    except OSError:
        pass   # stderr is the log, and the disk may be full


class Backend(QObject):
    """Exposed to QML as `backend`: `connected`, `ready`, `full` (the cleaned lists), `pending` (what a tap
    waits on), `act(op, id)` / `actForm(op, id, form)`, `refresh()`, and the `answered` and `listed` signals."""

    connectedChanged = Signal()
    readyChanged = Signal()
    fullChanged = Signal()
    pendingChanged = Signal()
    listed = Signal()            # a noticed_full came, even one that changed nothing
    # op, id, ok, text, preview: what agentd said to a tap (or why there is no answer).
    answered = Signal(str, str, bool, str, "QVariantMap")

    def __init__(self):
        super().__init__()
        self.tick_ms, self.ask_after_ms, self.ask_gap_ms = TICK_MS, ASK_AFTER_MS, ASK_GAP_MS
        self.echo_s, self.answer_s = ECHO_S, ANSWER_S
        self._connected = False
        self._ready = False          # a noticed_full has come on this connection
        self._full = clean_full({})
        self._pending: dict[str, tuple[str, str, float]] = {}   # key -> (op, id, sent at)
        self._buffer = b""
        self._asked = -1e9           # when the last noticed_list went out (monotonic)
        self._last_noticed = ""      # the last `noticed`, as it came, to tell an echo from news
        self._started = False
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._lost)
        self._socket.errorOccurred.connect(self._lost)
        self._socket.readyRead.connect(self._on_ready_read)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._ask = QTimer(self, singleShot=True)
        self._ask.timeout.connect(self._ask_now)
        if not os.environ.get("BOMBADIL_CHECK"):   # a check loads the window, it does not talk to agentd
            QTimer.singleShot(0, self.start)

    # -- what QML reads --

    @Property(bool, notify=connectedChanged)
    def connected(self):
        return self._connected

    @Property(bool, notify=readyChanged)
    def ready(self):
        return self._connected and self._ready

    @Property("QVariantMap", notify=fullChanged)
    def full(self):
        return self._full

    @Property(list, notify=pendingChanged)
    def pending(self):
        return list(self._pending)

    # -- the line --

    def start(self) -> None:
        """Connect, and keep trying while agentd is away."""
        if self._started:
            return
        self._started = True
        self._tick.start(self.tick_ms)
        self._connect()

    def _connect(self) -> None:
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(str(paths.socket_path()))

    def _on_connected(self) -> None:
        self._connected = True
        self._buffer = b""
        self.connectedChanged.emit()
        self.readyChanged.emit()
        self._ask_now()

    def _lost(self, *_) -> None:
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            return
        self._buffer = b""
        self._ask.stop()
        self._last_noticed = ""
        self._ready = False          # what it said before is old: the next connection answers afresh
        if self._pending:
            self._pending = {}
            self.pendingChanged.emit()
        if self._connected:
            self._connected = False
            _warn("lost", "agentd went away; trying again")
            self.connectedChanged.emit()
            self.readyChanged.emit()

    def _on_tick(self) -> None:
        now = time.monotonic()
        if not self._connected:
            self._connect()
            return
        if not self._ready:              # connected, and agentd has not said anything about noticed yet
            self._ask_now()
        late = [k for k, (_, _, t) in self._pending.items() if now - t >= self.answer_s]
        for k in late:
            op, id_, _ = self._pending.pop(k)
            self.answered.emit(op, id_, False, NO_ANSWER, {})
        if late:
            self.pendingChanged.emit()
            self._ask_soon()

    def _send(self, msg: dict) -> bool:
        if self._socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            return False
        if self._socket.write((json.dumps(msg) + "\n").encode()) < 0:
            return False
        self._socket.flush()
        return True

    # -- asking for the lists --

    def _ask_soon(self) -> None:
        """Ask once for however many reasons came in a burst: after a short wait, and not within the gap."""
        if not self._connected or self._ask.isActive():
            return
        gap = self.ask_gap_ms - (time.monotonic() - self._asked) * 1000
        self._ask.start(int(max(self.ask_after_ms, gap)))

    def _ask_now(self) -> None:
        if self._send({"type": "noticed_list"}):
            self._asked = time.monotonic()

    @Slot()
    def refresh(self) -> None:
        self._ask_soon()

    # -- what agentd says --

    def _on_ready_read(self) -> None:
        self._buffer += self._socket.readAll().data()
        *lines, self._buffer = self._buffer.split(b"\n")
        if len(self._buffer) > MAX_BUFFER:
            _warn("long", "a line from agentd was too long; dropped it")
            self._buffer = b""
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                _warn("json", "a line from agentd was not JSON; ignored it")
                continue
            if isinstance(msg, dict):
                try:
                    self.handle(msg)
                except Exception as e:  # noqa: BLE001 - one bad message never costs the window
                    _warn("handle", f"{msg.get('type')!r}: {type(e).__name__}: {e}")

    def handle(self, msg: dict) -> None:
        """One message from agentd. Everything but the loop's own messages is somebody else's."""
        kind = msg.get("type")
        if kind == "noticed_full":
            self._on_full(msg)
        elif kind == "noticed":
            # News asks again. The same message again just after our own request is its echo, and
            # answering echoes would never end. Before the first answer there is nothing to refresh:
            # the request already out will bring the news with it.
            now = json.dumps(msg, sort_keys=True, default=str)
            echo = now == self._last_noticed and time.monotonic() - self._asked < self.echo_s
            self._last_noticed = now
            if self._ready and not echo:
                self._ask_soon()
        elif kind == "noticed_result":
            self._on_result(msg)
        elif kind == "noticed_open":
            self._ask_soon()

    def _on_full(self, msg: dict) -> None:
        full = clean_full(msg)
        if not self._ready:
            self._ready = True
            self.readyChanged.emit()
        if full != self._full:     # the same lists again change nothing on screen
            self._full = full
            self.fullChanged.emit()
        self.listed.emit()

    def _on_result(self, msg: dict) -> None:
        op, id_, ok, text, preview = clean_result(msg)
        done = [k for k, (o, i, _) in self._pending.items() if o == op and id_ in ("", i)] \
            or [k for k, (o, _, _) in self._pending.items() if o == op]
        for k in done:
            self._pending.pop(k)
        if done:
            self.pendingChanged.emit()
        self.answered.emit(op, id_, ok, text, preview)
        self._ask_soon()

    # -- what he presses --

    @Slot(str, str)
    def act(self, op: str, id: str) -> None:
        self.actForm(op, id, "")

    @Slot(str, str, str)
    def actForm(self, op: str, id: str, form: str) -> None:
        """Send `noticed_do`. A second tap on a row that still waits for its answer does nothing."""
        key = id or f"op:{op}"
        if key in self._pending:
            return
        msg = {"type": "noticed_do", "op": op}
        if id:
            msg["id"] = id
        if form:
            msg["form"] = form
        if not self._send(msg):
            self.answered.emit(op, id, False, OFFLINE, {})
            return
        self._pending[key] = (op, id, time.monotonic())
        self.pendingChanged.emit()

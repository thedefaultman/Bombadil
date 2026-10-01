"""The Mail window's lines to bombadil-mail and to agentd, and the few things QML cannot do alone.

bombadil-mail answers on mail.sock in JSON lines (docs/MAIL.md): {"id", "op", "rid", ...} gets {"rid", "ok",
"result" | "error", "code"}, and after "subscribe" it pushes "changed", "new_mail", "show", "sent" and "status".
agentd answers on its own socket, and the only thing this window says to it is a press. QLocalSocket keeps all
of it on Qt's event loop, so nothing here blocks the window.

What QML sees: `sidebar` and `accounts`, `messages` (the list in the middle), `opened` and `mail` (the mail on
the right), `draft` (the reply box), `canSend` and `sendNote` (the gate on Send), `receipt`, `quiet`. QML calls
slots for what the person does and never decides anything about a send: the gate is here, where a test can
reach it without a window.

Why it is shaped this way:

- The press is one place. The only line in this file that says "press" to agentd is in `Backend.press`, which
  QML's Send button calls from its own click handler and nothing else does. `press` checks the gate again, so a
  button that was somehow enabled still cannot send what the service has not been told was drawn.
- The gate says what is true, not what is hoped. Send is enabled only while the draft the box shows is the
  draft the service last answered with (no edit waiting, in flight or refused), the service has been told that
  exact fingerprint was drawn (`draft_shown`, reported by the box once it has drawn it), there is somebody to
  send to, and both the service and agentd are there. Anything else is one quiet sentence next to the button.
- Everything is held in memory. A mail's text is fetched when a mail is opened and dropped when it is closed;
  nothing here opens a file for writing, and nothing logs a payload (only the name of an op and a code).
- Requests carry a "rid", so calls are in flight together; the service serves one connection in order, so what
  can take long (saving an attachment, adding an account) goes over a connection of its own and never holds up
  the person's typing. Every request has a deadline, and a connection that goes takes its requests with it.
- A line is at most 1 MiB going out (the service refuses more); coming in from the service one may be larger
  (it writes non-ASCII text escaped) and is read up to MAIL_LINE_MAX; agentd's broadcasts are read and thrown
  away, however long, and only its answer to a press is kept.
- Nothing retries a press. An answer that never came (agentd went away mid-send) is settled by asking the
  service what became of the draft, which is where the truth is.

With BOMBADIL_CHECK=1 (`bombadil-app check mail`) no socket is opened: the window shows sample mail.
"""

import json
import os
import re
import shutil
import time
from email.utils import getaddresses
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

CHECKING = os.environ.get("BOMBADIL_CHECK") == "1"

RECONNECT_MS = 2000
DEBOUNCE_MS = 400            # typing in the reply box becomes one edit this long after the last key
REFRESH_MS = 250             # a burst of "changed" pushes becomes one refresh
SEARCH_MS = 300
QUIET_MS = 12000             # how long a quiet line stays
REQUEST_S = 15.0             # an answer the service owes within this
SLOW_S = 130.0               # saving an attachment, adding an account
PRESS_S = 100.0              # a press agentd has not answered by now is looked into (the service gives up at 60 s)
SETTLE_MS = 1500             # between looks at a draft whose press got no answer
SETTLE_TRIES = 50
SETTLE_OPEN_TRIES = 5        # a draft still open this many looks later was not sent
PAGE = 50
LIST_MAX = 200
WRITE_LIMIT = (1 << 20) - 1024     # a request line is at most 1 MiB, with its newline
MAIL_LINE_MAX = 16 << 20
AGENT_LINE_MAX = 1 << 20
WRITE_BACKLOG = 8 << 20            # bytes waiting to go out to a service that is not reading

DOWN = "Mail is not running yet."
NOT_AGENTD = "Not connected"
UNKNOWN_LINE = "I can't tell whether that went. Look in Sent before you press Send again."   # outbox.UNKNOWN_LINE
LOST_LINE = "I lost the connection while that was sending."
FIELDS = ("to", "cc", "bcc", "subject", "body")
RECIPIENTS = ("to", "cc", "bcc")
KINDS = ("reply", "reply_all", "forward")
VIEWS = ("all", "needs_reply", "drafts")
USABLE = ("ok", "syncing")
KIND_NAMES = {"new": "New mail", "reply": "Reply", "reply_all": "Reply to all", "forward": "Forward"}
_URL = re.compile(r"https://[^\s\x00-\x1f\x7f-\x9f]{3,2000}")
# What is dropped from a stranger's words before they are drawn: control characters (but not a line break or a tab) and
# the characters that turn text around or hide where it ends, which make "fdp.exe" read as "exe.pdf".
_UNSEEN = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


def _runtime() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    rt = os.environ.get("BOMBADIL_RUNTIME")
    return Path(rt).expanduser() if rt else Path(base) / "bombadil"


def mail_socket() -> str:
    return os.environ.get("BOMBADIL_MAIL_SOCKET") or str(_runtime() / "mail.sock")


def agent_socket() -> str:
    return os.environ.get("BOMBADIL_SOCKET") or str(_runtime() / "agentd.sock")


def _bombadil() -> str | None:
    """The `bombadil` command: on the path, else the checkout's or the image's own."""
    found = shutil.which("bombadil")
    if found:
        return found
    try:
        mine = Path(__file__).resolve().parents[3] / "bin" / "bombadil"
    except (NameError, IndexError):
        return None
    return str(mine) if mine.is_file() else None


class Failure(str):
    """A sentence for the person with the service's code for the program. The code is never shown."""

    code = ""

    def __new__(cls, sentence: str, code: str = ""):
        obj = super().__new__(cls, sentence)
        obj.code = code
        return obj


def _line(text) -> str:
    """One line of someone's words: no control or direction characters, runs of blanks as one space."""
    return " ".join(_UNSEEN.sub("", str(text or "")).split())


def _text(text) -> str:
    """A mail's text for the page: its line breaks and tabs kept, the characters of _UNSEEN (a carriage return too) not."""
    return _UNSEEN.sub("", str(text or ""))


# ---- words ---------------------------------------------------------------------------------------------------

def addr_label(a) -> str:
    """"Priya Shah", else the address."""
    a = a if isinstance(a, dict) else {}
    return _line(a.get("name")) or _line(a.get("email"))


def addr_full(a) -> str:
    """`Priya Shah <priya@acme.example>`, the name quoted when it has to be, the address alone with no name."""
    a = a if isinstance(a, dict) else {}
    name, email = _line(a.get("name")), _line(a.get("email"))
    if not name:
        return email
    if re.search(r'[",<>;:@()\[\]\\]', name):
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{email}>"


def addr_list(addrs) -> str:
    return ", ".join(addr_full(a) for a in addrs or [])


def when_label(ts, now=None) -> str:
    """09:08 today, a weekday this week, else 27 Sep (with the year when it is another year)."""
    try:
        t, n = time.localtime(float(ts)), time.localtime(now if now is not None else time.time())
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    if (t.tm_year, t.tm_yday) == (n.tm_year, n.tm_yday):
        return time.strftime("%H:%M", t)
    age = (time.mktime(n) - time.mktime(t)) / 86400
    if 0 < age < 6:
        return time.strftime("%a", t)
    return time.strftime("%-d %b" if t.tm_year == n.tm_year else "%-d %b %Y", t)


def full_when(ts) -> str:
    try:
        return time.strftime("%-d %b %Y, %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def size_label(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 10 else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def safe_url(url) -> bool:
    """Only an https link is ever handed to the browser: never a file, a script or a program."""
    return isinstance(url, str) and _URL.fullmatch(url) is not None


def _norm(field: str, text: str):
    """What the service would make of this text, so that an edit it normalised is not taken for a change."""
    if field in RECIPIENTS:
        return list(dict.fromkeys(e.strip().lower() for _n, e in getaddresses([text]) if e.strip()))
    if field == "subject":
        return _line(text)
    return text.replace("\x00", "")


def _render(draft: dict, field: str) -> str:
    return addr_list(draft.get(field)) if field in RECIPIENTS else str(draft.get(field) or "")


# ---- the socket ----------------------------------------------------------------------------------------------

class Line(QObject):
    """A QLocalSocket that carries JSON lines, and is kept connected: every RECONNECT_MS while it is away, unless
    it is a one-shot. A line over `limit` ends the link (`drop` False) or is skipped (`drop` True, for a socket
    whose chatter nobody here reads)."""

    opened = Signal()
    closed = Signal()
    got = Signal(dict)

    def __init__(self, path, limit: int, *, drop: bool = False, retry: bool = True, parent=None):
        super().__init__(parent)
        self._path = path
        self._limit = limit
        self._drop = drop
        self._retry = retry
        self._wanted = False
        self._up = False
        self._buf = bytearray()
        self._scan = 0
        self._skipping = False
        self._sock = QLocalSocket(self)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._down)
        self._sock.errorOccurred.connect(self._on_error)
        self._sock.readyRead.connect(self._on_ready)
        self._timer = QTimer(self)
        self._timer.setInterval(RECONNECT_MS)
        self._timer.timeout.connect(self._connect)

    @property
    def up(self) -> bool:
        return self._up

    def start(self) -> None:
        self._wanted = True
        self._connect()
        if self._retry:
            self._timer.start()

    def stop(self) -> None:
        self._wanted = False
        self._timer.stop()
        self._sock.abort()
        self._down()

    def _connect(self) -> None:
        if self._wanted and self._sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._sock.connectToServer(self._path())

    def _on_connected(self) -> None:
        self._buf.clear()
        self._scan = 0
        self._skipping = False
        self._up = True
        self.opened.emit()

    def _on_error(self, _err) -> None:
        if self._sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
            was = self._up
            self._down()
            if not was and not self._retry and self._wanted:
                self._wanted = False
                self.closed.emit()      # a one-shot that never got through: whoever waits on it is told now

    def _down(self) -> None:
        was, self._up = self._up, False
        if was:
            self.closed.emit()

    def send(self, obj: dict) -> Failure | None:
        """None when the line went; else why not. Text goes as it is (not as \\u escapes), which keeps a long
        draft in another language under the limit."""
        if not self._up:
            return Failure(DOWN, "unavailable")
        try:
            data = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
        except UnicodeEncodeError:   # a lone surrogate cannot be written as text
            data = json.dumps(obj, allow_nan=False).encode()
        except (TypeError, ValueError):
            return Failure("That could not be sent.", "bad_request")
        if len(data) > WRITE_LIMIT:
            return Failure("That is too long for Mail to take in one piece. Shorten it.", "too_big")
        if self._sock.bytesToWrite() > WRITE_BACKLOG:
            self._sock.abort()   # a peer that does not read: start again
            self._down()
            return Failure(DOWN, "unavailable")
        self._sock.write(data + b"\n")
        self._sock.flush()
        return None

    def _on_ready(self) -> None:
        chunk = bytes(self._sock.readAll().data())
        if self._skipping:
            i = chunk.find(b"\n")
            if i < 0:
                return
            chunk, self._skipping = chunk[i + 1:], False
        self._buf += chunk
        while True:
            i = self._buf.find(b"\n", self._scan)
            if i < 0:
                self._scan = len(self._buf)
                if len(self._buf) > self._limit:
                    self._too_long()
                return
            raw = bytes(self._buf[:i])
            del self._buf[:i + 1]
            self._scan = 0
            if len(raw) > self._limit:
                if self._drop:
                    continue
                self._too_long()
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.got.emit(msg)

    def _too_long(self) -> None:
        if self._drop:
            self._buf.clear()
            self._scan = 0
            self._skipping = True
        else:
            self._sock.abort()
            self._down()


# ---- the backend ---------------------------------------------------------------------------------------------

class Backend(QObject):
    linkChanged = Signal()
    accountsChanged = Signal()
    viewChanged = Signal()
    listChanged = Signal()
    selectionChanged = Signal()
    draftChanged = Signal()
    draftLoaded = Signal()           # the text of a field changed under QML: load backend.draftFields
    gateChanged = Signal()
    receiptChanged = Signal()
    quietChanged = Signal()
    setupChanged = Signal()
    showRequested = Signal()         # somebody asked for the window: slide it in

    def __init__(self):
        super().__init__()
        self._rid = 0
        self._pending: dict[int, tuple[float, object]] = {}
        self._slow: set[Line] = set()
        self._slow_of: dict[int, Line] = {}      # the one-shot connection a slow request is on
        self._connected = False
        self._agent_up = False
        self._ready = False
        self._asked = False
        self._engine = ""
        self._engine_text = ""
        self._accounts: list[dict] = []
        self._counts = {"unread": 0, "needs_reply": 0, "drafts": 0}
        self._view = "all"
        self._search = ""
        self._rows: list[dict] = []          # what the service listed
        self._messages: list[dict] = []      # ... as the list shows it (a search of a short view filters here)
        self._list_state = "loading"
        self._list_error = ""
        self._more = False
        self._cursor = None
        self._skipped: list[dict] = []
        self._list_gen = 0
        self._opened: dict | None = None
        self._mail: dict | None = None
        self._mail_state = "none"
        self._mail_error = ""
        self._read_gen = 0
        self._show_seq = -1
        self._wants: set[str] = set()
        # the reply box
        self._draft: dict | None = None
        self._want: dict[str, str] = {}      # field -> the text the box shows
        self._sent: dict[str, str] = {}      # field -> the text the service has been given, or loaded
        self._flying: dict[str, str] = {}    # field -> what an edit still in flight carries
        self._inflight = 0
        self._edit_errors: dict[str, str] = {}
        self._shown = ""                     # the fingerprint the service has acknowledged as drawn
        self._shown_asked = ""
        self._looked = False                 # "I looked in Sent"
        self._press = ""                     # "" | "sending" | "checking"
        self._press_for: tuple[str, str] | None = None
        self._press_due = 0.0                # when a press that got no answer is taken for lost
        self._press_line = ""
        self._settle = 0
        self._receipt: dict | None = None
        self._quiet = ""
        self._quiet_tone = ""
        self._staged = False
        self._adding = False
        self._add_error = ""
        self._attach_error = ""

        self._expiry = QTimer(self)
        self._expiry.setInterval(1000)
        self._expiry.timeout.connect(self._expire)
        self._edits = QTimer(self)
        self._edits.setSingleShot(True)
        self._edits.setInterval(DEBOUNCE_MS)
        self._edits.timeout.connect(self._flush)
        self._refresher = QTimer(self)
        self._refresher.setSingleShot(True)
        self._refresher.setInterval(REFRESH_MS)
        self._refresher.timeout.connect(self._refresh_now)
        self._searcher = QTimer(self)
        self._searcher.setSingleShot(True)
        self._searcher.setInterval(SEARCH_MS)
        self._searcher.timeout.connect(self._fetch_list)
        self._hush = QTimer(self)
        self._hush.setSingleShot(True)
        self._hush.setInterval(QUIET_MS)
        self._hush.timeout.connect(self.clearQuiet)
        self._settler = QTimer(self)
        self._settler.setSingleShot(True)
        self._settler.setInterval(SETTLE_MS)
        self._settler.timeout.connect(self._settle_look)

        self._mail_line = Line(mail_socket, MAIL_LINE_MAX, parent=self)
        self._mail_line.opened.connect(self._on_mail_open)
        self._mail_line.closed.connect(self._on_mail_closed)
        self._mail_line.got.connect(self._on_answer)
        self._agent = Line(agent_socket, AGENT_LINE_MAX, drop=True, parent=self)
        self._agent.opened.connect(self._on_agent_changed)
        self._agent.closed.connect(self._on_agent_closed)
        self._agent.got.connect(self._on_agent_line)
        if CHECKING:
            self._sample()
        else:
            self._expiry.start()
            self._mail_line.start()
            self._agent.start()

    def stop(self) -> None:
        """Let go of every socket and timer: the window is closing, or a test is done."""
        for timer in (self._expiry, self._edits, self._refresher, self._searcher, self._hush, self._settler):
            timer.stop()
        for link in list(self._slow):
            self._retire(link)
        self._mail_line.stop()
        self._agent.stop()

    # ---- what QML reads ----------------------------------------------------------------------------------

    @Property(bool, notify=linkChanged)
    def connected(self):
        return self._connected

    @Property(bool, notify=linkChanged)
    def agentConnected(self):
        return self._agent_up

    @Property(bool, notify=linkChanged)
    def ready(self):
        return self._ready

    @Property(str, notify=linkChanged)
    def engine(self):
        return self._engine

    @Property(str, notify=linkChanged)
    def engineNote(self):
        """Why mail cannot be read just now, quietly: the engine's own words, or that the service is away. Empty
        when all is well, and when there is no account yet (the window says that in its own way)."""
        if not self._connected:
            return DOWN + " This reconnects by itself." if self._ready else ""
        if self._engine in ("up", "off", ""):
            return ""
        return self._engine_text

    @Property("QVariant", notify=accountsChanged)
    def accounts(self):
        return self._accounts

    @Property("QVariant", notify=accountsChanged)
    def sidebar(self):
        return self._sidebar()

    @Property(str, notify=accountsChanged)
    def unreadText(self):
        n = self._counts["unread"]
        return f"{n} unread" if n else ""

    @Property(bool, notify=accountsChanged)
    def noAccount(self):
        """Nothing to read yet: no account, or none that works and one still signing in."""
        if not self._ready or not self._connected:
            return False
        if not self._accounts:
            return True
        return not any(a["state"] in USABLE for a in self._accounts) and any(
            a["state"] == "signin" for a in self._accounts)

    @Property("QVariant", notify=accountsChanged)
    def signingIn(self):
        return next((a for a in self._accounts if a["state"] == "signin"), None)

    @Property(str, notify=viewChanged)
    def view(self):
        return self._view

    @Property(str, notify=viewChanged)
    def viewName(self):
        row = next((r for r in self._sidebar() if r["id"] == self._view), None)
        return row["name"] if row else "Mail"

    @Property(str, notify=viewChanged)
    def searchText(self):
        return self._search

    @Property("QVariant", notify=listChanged)
    def messages(self):
        return self._messages

    @Property(str, notify=listChanged)
    def listState(self):
        return self._list_state

    @Property(str, notify=listChanged)
    def listError(self):
        return self._list_error

    @Property(bool, notify=listChanged)
    def listMore(self):
        return self._more

    @Property("QVariant", notify=listChanged)
    def skipped(self):
        return self._skipped

    @Property("QVariant", notify=selectionChanged)
    def opened(self):
        return self._opened

    @Property("QVariant", notify=selectionChanged)
    def mail(self):
        return self._mail

    @Property(str, notify=selectionChanged)
    def mailState(self):
        return self._mail_state

    @Property(str, notify=selectionChanged)
    def mailError(self):
        return self._mail_error

    @Property(str, notify=selectionChanged)
    def selectedId(self):
        if self._view == "drafts":
            return self._draft["id"] if self._draft else ""
        return self._opened["id"] if self._opened else ""

    @Property("QVariant", notify=draftChanged)
    def draft(self):
        return self._draft

    @Property(str, notify=draftChanged)
    def draftKind(self):
        return KIND_NAMES.get(self._draft["kind"], "Draft") if self._draft else ""

    @Property(str, notify=draftChanged)
    def draftFrom(self):
        return addr_full(self._draft["from"]) if self._draft else ""

    @Property("QVariant", notify=draftLoaded)
    def draftFields(self):
        return dict(self._want)

    @Property("QVariant", notify=gateChanged)
    def fieldErrors(self):
        return dict(self._edit_errors)

    @Property(str, notify=gateChanged)
    def attachError(self):
        return self._attach_error

    @Property(bool, notify=gateChanged)
    def canSend(self):
        return self._gate() == ""

    @Property(str, notify=gateChanged)
    def sendNote(self):
        """One quiet line: why Send is not pressable. Empty when it is."""
        return self._gate()

    @Property(str, notify=gateChanged)
    def pressLine(self):
        """What became of the last press, in the words agentd gave (or that a send may have gone), else empty."""
        if self._press_line:
            return self._press_line
        return UNKNOWN_LINE if self._draft is not None and self._draft["state"] == "unknown" else ""

    @Property(str, notify=gateChanged)
    def sendLabel(self):
        if self._press == "sending":
            return "Sending"
        if self._draft and self._draft["state"] == "unknown" and self._looked:
            return "Send again"
        return "Send"

    @Property(bool, notify=gateChanged)
    def unknownOutcome(self):
        """A send that may have gone: the person is asked to look in Sent before pressing again."""
        return bool(self._draft and self._draft["state"] == "unknown")

    @Property(str, notify=gateChanged)
    def unknownLine(self):
        return UNKNOWN_LINE

    @Property(bool, notify=gateChanged)
    def looked(self):
        return self._looked

    @Property(str, notify=gateChanged)
    def pressState(self):
        return self._press

    @Property("QVariant", notify=receiptChanged)
    def receipt(self):
        return self._receipt

    @Property(str, notify=quietChanged)
    def quiet(self):
        return self._quiet

    @Property(str, notify=quietChanged)
    def quietTone(self):
        return self._quiet_tone

    @Property(bool, notify=setupChanged)
    def adding(self):
        return self._adding

    @Property(str, notify=setupChanged)
    def addError(self):
        return self._add_error

    @Property(bool, notify=setupChanged)
    def staged(self):
        return self._staged

    # ---- small helpers -----------------------------------------------------------------------------------

    def _set(self, attr: str, value, signal) -> bool:
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()
            return True
        return False

    def _say(self, text, tone: str = "") -> None:
        """One quiet line at the bottom of the window. `tone` is "" or "bad"."""
        self._quiet, self._quiet_tone = _line(text), tone
        self.quietChanged.emit()
        self._hush.start() if self._quiet else self._hush.stop()

    @Slot()
    def clearQuiet(self) -> None:
        if self._quiet:
            self._quiet, self._quiet_tone = "", ""
            self.quietChanged.emit()

    @Slot()
    def dismissReceipt(self) -> None:
        self._set("_receipt", None, self.receiptChanged)

    def _sidebar(self) -> list[dict]:
        rows = [{"id": "all", "name": "All inboxes", "kind": "all", "count": self._counts["unread"], "note": "",
                 "state": "ok", "web": None}]
        for a in self._accounts:
            usable = a["state"] in USABLE
            note = {"signin": "Signing in", "blocked": a["note"] or "This account cannot be used here.",
                    "error": a["note"] or "Something is wrong with this account."}.get(a["state"], "")
            web = a.get("web") if a["state"] in ("blocked", "error") else None
            rows.append({"id": f"acct:{a['id']}", "name": a["email"], "kind": "account",
                         "count": a["unread"] if usable else 0, "note": note, "state": a["state"],
                         "web": web if isinstance(web, dict) and safe_url(web.get("url")) else None})
        rows.append({"id": "needs_reply", "name": "Needs a reply", "kind": "view",
                     "count": self._counts["needs_reply"], "note": "", "state": "ok", "web": None})
        rows.append({"id": "drafts", "name": "Drafts", "kind": "view", "count": self._counts["drafts"],
                     "note": "", "state": "ok", "web": None})
        return rows

    # ---- requests ----------------------------------------------------------------------------------------

    def _request(self, op: str, on=None, timeout: float = REQUEST_S, slow: bool = False, **args) -> int:
        """Ask the service. `on(result, failure)` runs once: the result, or None and a Failure. A slow request
        goes over a connection of its own, since the service answers one connection in order."""
        self._rid += 1
        rid = self._rid
        line = {**args, "op": op, "rid": rid}
        link = self._mail_line
        if slow:
            link = Line(mail_socket, MAIL_LINE_MAX, retry=False, parent=self)
            self._slow.add(link)
            self._slow_of[rid] = link
            link.got.connect(lambda msg, link=link: self._on_answer(msg, link))
            link.opened.connect(lambda link=link: self._first_line(link, line, rid))
            link.closed.connect(lambda link=link: self._slow_closed(link, rid))
            self._pending[rid] = (time.monotonic() + SLOW_S, on)
            link.start()
            return rid
        failure = link.send(line) if not CHECKING else Failure(DOWN, "unavailable")
        if failure is not None:
            if on is not None:
                QTimer.singleShot(0, lambda: on(None, failure))
            return rid
        self._pending[rid] = (time.monotonic() + timeout, on)
        return rid

    def _first_line(self, link: Line, line: dict, rid: int) -> None:
        failure = link.send(line)
        if failure is not None:
            self._fail(rid, failure)
            self._retire(link)

    def _slow_closed(self, link: Line, rid: int) -> None:
        self._fail(rid, Failure(DOWN, "unavailable"))
        self._retire(link)

    def _retire(self, link: Line) -> None:
        if link in self._slow:
            self._slow.discard(link)
            for rid in [r for r, held in self._slow_of.items() if held is link]:
                del self._slow_of[rid]
            link.stop()
            link.deleteLater()

    def _fail(self, rid: int, failure: Failure) -> None:
        entry = self._pending.pop(rid, None)
        held = self._slow_of.get(rid)
        if held is not None:
            self._retire(held)
        if entry is not None and entry[1] is not None:
            self._run(entry[1], None, failure)

    def _run(self, cb, result, failure) -> None:
        try:
            cb(result, failure)
        except Exception as e:  # noqa: BLE001 - one callback never costs the connection; its text stays out of the log
            print(f"mail: {type(e).__name__} while handling an answer", flush=True)

    def _expire(self) -> None:
        now = time.monotonic()
        if self._press == "sending" and now > self._press_due:
            self._lost()        # agentd has said nothing for too long: ask the service what became of it
        for rid in [r for r, (due, _cb) in self._pending.items() if now > due]:
            self._fail(rid, Failure("Mail did not answer in time.", "timeout"))

    def _on_answer(self, msg: dict, link: Line | None = None) -> None:
        if "push" in msg:
            if link is None:
                self._on_push(msg)
            return
        entry = self._pending.pop(msg.get("rid"), None)
        if link is not None:
            self._retire(link)
        if entry is None or entry[1] is None:
            return
        if msg.get("ok") is True:
            self._run(entry[1], msg.get("result"), None)
        else:
            self._run(entry[1], None, Failure(_line(msg.get("error")) or "Mail could not do that.",
                                              str(msg.get("code") or "")))

    def _on_mail_open(self) -> None:
        self._connected = True
        self._request("subscribe")
        self._request("status", on=self._on_status)
        if not self._asked:
            self._asked = True   # once, at the start: what a launcher asked for before this window existed
            self._request("requested", on=self._on_requested)
        else:
            self._refresh_soon("list", "drafts", "accounts")
        if self._draft is not None:
            self._look_at_draft(reshow=True)
        self.linkChanged.emit()

    def _on_mail_closed(self) -> None:
        self._connected = False
        for rid in list(self._pending):
            self._fail(rid, Failure(DOWN, "unavailable"))
        self._inflight = 0
        self._flying.clear()
        self._shown_asked = ""
        self.linkChanged.emit()
        self.gateChanged.emit()

    def _on_agent_changed(self) -> None:
        self._agent_up = True
        self.linkChanged.emit()
        self.gateChanged.emit()

    def _on_agent_closed(self) -> None:
        self._agent_up = False
        if self._press == "sending":
            self._lost()
        self.linkChanged.emit()
        self.gateChanged.emit()

    # ---- pushes, status, what was asked to be shown -----------------------------------------------------

    def _on_push(self, msg: dict) -> None:
        name = msg.get("push")
        if name == "changed":
            what = msg.get("what")
            self._refresh_soon(*(w for w in what if isinstance(w, str)) if isinstance(what, list) else ("list",))
        elif name == "status":
            self._engine = str(msg.get("engine") or "")
            self._engine_text = _line(msg.get("text") or msg.get("detail"))
            self.linkChanged.emit()
            self._refresh_soon("status")
        elif name == "show":
            self._show(msg)
        elif name == "sent":
            receipt = msg.get("receipt")
            if isinstance(receipt, dict) and self._draft is not None and receipt.get("draft") == self._draft["id"]:
                self._finish_sent(receipt)

    def _refresh_soon(self, *what: str) -> None:
        self._wants.update(what)
        self._refresher.start()

    def _refresh_now(self) -> None:
        wants, self._wants = self._wants, set()
        self._request("status", on=self._on_status)
        if wants & {"list", "drafts", "accounts"} and self._list_state != "loading":
            self._fetch_list(quiet=True)
        if "drafts" in wants and self._draft is not None and self._press != "sending":
            self._look_at_draft()

    def _on_status(self, result, failure) -> None:
        if failure is not None or not isinstance(result, dict):
            return
        accounts = [a for a in result.get("accounts") or [] if isinstance(a, dict) and a.get("id")]
        for a in accounts:
            a["state"] = str(a.get("state") or "")
            a["note"] = _line(a.get("note"))
            a["unread"] = a["unread"] if isinstance(a.get("unread"), int) else 0
            a["email"] = _line(a.get("email"))
        self._engine = str(result.get("engine") or "")
        self._engine_text = _line(result.get("text") or result.get("detail"))
        self._counts = {"unread": result.get("unread") or 0, "needs_reply": result.get("needs_reply") or 0,
                        "drafts": result.get("drafts") or 0}
        self._accounts = accounts
        self._ready = True
        self.accountsChanged.emit()
        self.linkChanged.emit()
        if self._list_state == "error" and self._engine == "up":
            self._fetch_list(quiet=True)    # the engine is back: try the list again

    def _on_requested(self, result, failure) -> None:
        """What the launcher asked the service to show before this window was there, if anything; else the list."""
        if isinstance(result, dict) and (result.get("view") or result.get("id") or result.get("reply")):
            self._show(result, bring=False)
        else:
            self._fetch_list()

    def _show(self, msg: dict, bring: bool = True) -> None:
        """Somebody asked for a view, a mail or a draft: switch to it, then ask for the window."""
        seq = msg.get("seq")
        if isinstance(seq, int):
            if seq <= self._show_seq:
                return
            self._show_seq = seq
        view, mail_id, reply = msg.get("view"), msg.get("id"), msg.get("reply")
        if reply and not view:
            view = "drafts"
        if isinstance(view, str) and view:
            self.setView(view)
        if isinstance(reply, str) and reply:
            self.openDraft(reply)
        elif isinstance(mail_id, str) and mail_id:
            self.openMail(mail_id)
        if bring:
            self.showRequested.emit()

    # ---- the list ---------------------------------------------------------------------------------------

    @Slot(str)
    def setView(self, view: str) -> None:
        if not isinstance(view, str) or not (view in VIEWS or view.startswith("acct:")):
            return
        self._leave_box()
        self.closeMail()
        self._searcher.stop()
        self._search = ""
        self._view = view
        self._rows, self._messages, self._cursor, self._more, self._skipped = [], [], None, False, []
        self._list_state, self._list_error = "loading", ""
        self.viewChanged.emit()
        self.listChanged.emit()
        self._fetch_list()

    @Slot()
    def refresh(self) -> None:
        self._request("status", on=self._on_status)
        self._fetch_list(quiet=True)

    @Slot(str)
    def search(self, text: str) -> None:
        text = _line(text)
        if text == self._search:
            return
        self._search = text
        self.viewChanged.emit()
        if self._view in ("needs_reply", "drafts"):
            self._apply_filter()
        else:
            self._searcher.start()

    @Slot()
    def loadMore(self) -> None:
        if self._more and self._cursor and self._list_state == "ready" and not self._search:
            self._fetch_list(more=True)

    def _account_of_view(self):
        return self._view[5:] if self._view.startswith("acct:") else None

    def _fetch_list(self, *, more: bool = False, quiet: bool = False) -> None:
        view, text = self._view, self._search
        self._list_gen += 1
        gen = self._list_gen
        if not more and not quiet and not self._messages:
            self._set("_list_state", "loading", self.listChanged)

        def done(result, failure):
            if gen != self._list_gen or view != self._view:
                return
            if failure is not None:
                self._list_error = str(failure)
                self._list_state = "error" if not self._rows else "ready"
                if self._rows:
                    self._say(failure, "bad")
                self._skipped = []
                self.listChanged.emit()
                return
            self._landed(result, view, more)

        if view == "drafts":
            self._request("list", on=done, view="drafts", limit=LIST_MAX)
        elif view == "needs_reply":
            self._request("list", on=done, view="needs_reply", limit=LIST_MAX)
        elif text:
            account = self._account_of_view()
            self._request("search", on=done, text=text, limit=PAGE, **({"account": account} if account else {}))
        else:
            limit = min(LIST_MAX, max(PAGE, len(self._rows))) if not more else PAGE
            args = {"cursor": self._cursor} if more and self._cursor else {}
            self._request("list", on=done, view=view, limit=limit, **args)

    def _landed(self, result, view: str, more: bool) -> None:
        if view == "drafts":
            rows = [self._draft_row(d) for d in result if isinstance(d, dict)] if isinstance(result, list) else []
            self._cursor, self._more, self._skipped = None, False, []
        else:
            body = result if isinstance(result, dict) else {}
            rows = [self._row(m) for m in body.get("messages") or [] if isinstance(m, dict)]
            self._cursor = body.get("cursor")
            self._more = bool(body.get("more")) and bool(self._cursor)
            self._skipped = [self._skip(s) for s in body.get("skipped") or [] if isinstance(s, dict)]
            if self._search and view not in ("needs_reply", "drafts"):
                self._cursor, self._more = None, False
        if more:
            seen = {r["id"] for r in self._rows}
            rows = self._rows + [r for r in rows if r["id"] not in seen]
        self._rows = rows
        self._list_state, self._list_error = "ready", ""
        self._apply_filter(emit=False)
        self.listChanged.emit()
        self._patch_opened()

    def _apply_filter(self, emit: bool = True) -> None:
        text = self._search.lower()
        if text and self._view in ("needs_reply", "drafts"):
            self._messages = [r for r in self._rows if text in " ".join(
                [r["sender"], r["subject"], r["why"] or "", r["address"]]).lower()]
        else:
            self._messages = list(self._rows)
        if emit:
            self.listChanged.emit()

    def _row(self, m: dict) -> dict:
        sender = m.get("from") if isinstance(m.get("from"), dict) else {}
        return {"id": str(m.get("id") or ""), "account": str(m.get("account") or ""),
                "sender": addr_label(sender) or "(unknown)", "address": _line(sender.get("email")),
                "subject": _line(m.get("subject")) or "(no subject)", "ts": m.get("ts") or 0,
                "when": when_label(m.get("ts")), "unread": bool(m.get("unread")),
                "flagged": bool(m.get("flagged")), "attachments": bool(m.get("attachments")),
                "needsReply": bool(m.get("needs_reply")), "why": _line(m.get("why")) or None, "draft": False}

    def _draft_row(self, d: dict) -> dict:
        to = d.get("to") or []
        who = addr_label(to[0]) + (f" and {len(to) - 1} more" if len(to) > 1 else "") if to else "No one yet"
        why = {"sending": "Being sent", "unknown": "May have gone: look in Sent"}.get(
            d.get("state"), "Written by Bombadil" if d.get("created_by") == "agent" else None)
        return {"id": str(d.get("id") or ""), "account": str(d.get("account") or ""), "sender": who,
                "address": " ".join(a.get("email", "") for a in to if isinstance(a, dict)),
                "subject": _line(d.get("subject")) or "(no subject)", "ts": d.get("updated") or 0,
                "when": when_label(d.get("updated")), "unread": False, "flagged": False,
                "attachments": bool(d.get("attachments")), "needsReply": False, "why": why, "draft": True}

    def _skip(self, s: dict) -> dict:
        email = next((a["email"] for a in self._accounts if a["id"] == s.get("account")), str(s.get("account") or ""))
        web = s.get("web") if isinstance(s.get("web"), dict) else None
        return {"account": str(s.get("account") or ""), "email": email, "state": str(s.get("state") or ""),
                "note": _line(s.get("note")),
                "web": web if web and safe_url(web.get("url")) else None}

    def _patch_opened(self) -> None:
        """A fresh list may know better whether the open mail is unread, flagged or needs a reply."""
        if self._opened is None:
            return
        row = next((r for r in self._rows if r["id"] == self._opened["id"]), None)
        if row is None:
            return
        keep = {k: self._opened.get(k) for k in ("sender", "address")}
        new = {**self._opened, **{k: v for k, v in row.items() if k not in keep or not self._opened.get(k)}}
        if new != self._opened:
            self._opened = new
            self.selectionChanged.emit()

    # ---- one mail ---------------------------------------------------------------------------------------

    @Slot(str)
    def openMail(self, mail_id: str, keep_receipt: bool = False) -> None:
        if not mail_id:
            return
        self._leave_box()
        if not keep_receipt:
            self.dismissReceipt()
        row = next((r for r in self._rows if r["id"] == mail_id), None)
        self._read_gen += 1
        gen = self._read_gen
        self._opened = dict(row) if row else {"id": mail_id, "sender": "", "address": "", "subject": "", "when": "",
                                              "unread": False, "flagged": False, "needsReply": False, "why": None,
                                              "attachments": False}
        self._mail, self._mail_state, self._mail_error = None, "loading", ""
        self.selectionChanged.emit()

        def done(result, failure):
            if gen != self._read_gen:
                return
            if failure is not None:
                self._mail_state, self._mail_error = "error", str(failure)
            else:
                self._mail_state = "ready"
                self._read_landed(result)
            self.selectionChanged.emit()

        self._request("read", on=done, id=mail_id)

    def _read_landed(self, result) -> None:
        body = result if isinstance(result, dict) else {}
        message = self._row(body["message"]) if isinstance(body.get("message"), dict) else {}
        full = body["message"] if isinstance(body.get("message"), dict) else {}
        header = {"to": addr_list(full.get("to")), "cc": addr_list(full.get("cc")),
                  "from": addr_full(full.get("from")), "full_when": full_when(full.get("ts")),
                  "folder": str(full.get("folder") or "")}
        self._opened = {**(self._opened or {}), **message, **header}
        atts = [{"part": str(p.get("part") or ""), "name": _line(p.get("name")) or "attachment",
                 "size": size_label(p.get("size")), "inline": bool(p.get("inline"))}
                for p in body.get("attachments") or [] if isinstance(p, dict)]
        self._mail = {"text": _text(body.get("text")), "truncated": bool(body.get("truncated")),
                      "htmlOnly": bool(body.get("html_only")), "attachments": atts,
                      "webUrl": body.get("web_url") if safe_url(body.get("web_url")) else "",
                      "webName": self._web_name(self._opened.get("account"))}

    def _web_name(self, account_id) -> str:
        a = next((x for x in self._accounts if x["id"] == account_id), None)
        web = a.get("web") if a else None
        return _line(web.get("name")) if isinstance(web, dict) else ""

    @Slot()
    def closeMail(self) -> None:
        self._read_gen += 1
        if self._opened is not None or self._mail is not None:
            self._opened, self._mail, self._mail_state, self._mail_error = None, None, "none", ""
            self.selectionChanged.emit()

    def _act(self, op: str, then, **args) -> None:
        """A change to the open mail: said plainly when the service says no."""
        def done(result, failure):
            if failure is not None:
                self._say(failure, "bad")
            else:
                then(result)
        self._request(op, on=done, **args)

    def _patch(self, mail_id: str, **fields) -> None:
        for rows in (self._rows, self._messages):
            for r in rows:
                if r["id"] == mail_id:
                    r.update(fields)
        if self._opened is not None and self._opened["id"] == mail_id:
            self._opened = {**self._opened, **fields}
            self.selectionChanged.emit()
        self.listChanged.emit()

    @Slot(bool)
    def setFlagged(self, flagged: bool) -> None:
        o = self._opened
        if o:
            self._act("set_flags", lambda _r: self._patch(o["id"], flagged=bool(flagged)), id=o["id"],
                      flagged=bool(flagged))

    @Slot(bool)
    def setUnread(self, unread: bool) -> None:
        o = self._opened
        if o:
            self._act("set_flags", lambda _r: self._patch(o["id"], unread=bool(unread)), id=o["id"],
                      read=not unread)

    @Slot(bool)
    def markReply(self, needs: bool) -> None:
        o = self._opened
        if not o:
            return

        def then(result):
            body = result if isinstance(result, dict) else {}
            self._patch(o["id"], needsReply=bool(needs), why=_line(body.get("why")) or None)
            self._refresh_soon("list")

        self._act("mark_reply", then, id=o["id"], needs=bool(needs), **({"why": "You marked this."} if needs else {}))

    @Slot()
    @Slot(str)
    def archive(self, mail_id: str = "") -> None:
        self._remove("archive", mail_id)

    @Slot()
    @Slot(str)
    def trash(self, mail_id: str = "") -> None:
        self._remove("trash", mail_id)

    def _remove(self, op: str, mail_id: str) -> None:
        mail_id = mail_id or (self._opened["id"] if self._opened else "")
        if not mail_id or self._view == "drafts":
            return

        def then(_result):
            after = self._next_after(mail_id)
            was_open = self._opened is not None and self._opened["id"] == mail_id
            self._rows = [r for r in self._rows if r["id"] != mail_id]
            self._apply_filter(emit=False)
            self.listChanged.emit()
            self._refresh_soon("list")
            if was_open:
                self.closeMail()
                if after:
                    self.openMail(after)

        self._act(op, then, id=mail_id)

    def _next_after(self, gone: str) -> str:
        """The next unread mail after this one (the first unread when none is below): what shows once a mail is put
        away or answered. A draft is never opened by itself, and a view with nothing unread in it shows its list."""
        rows = self._messages
        at = next((i for i, r in enumerate(rows) if r["id"] == gone), -1)
        for r in rows[at + 1:] + rows[:max(at, 0)]:
            if r["unread"] and r["id"] != gone and not r["draft"]:
                return r["id"]
        return ""

    @Slot(str, result=bool)
    def openWeb(self, url: str) -> bool:
        """Open a link in the browser panel with `bombadil open`, which is how every window's links get there."""
        if not safe_url(url):
            self._say("That link is not one to open from here.", "bad")
            return False
        prog = _bombadil()
        if CHECKING or prog is None:
            return False
        return self._start(prog, ["open", url])

    def _start(self, prog: str, args: list[str]) -> bool:
        ok, _pid = QProcess.startDetached(prog, args)
        return bool(ok)

    @Slot(str)
    def saveAttachment(self, part: str) -> None:
        o = self._opened
        if not o or not part:
            return
        att = next((a for a in (self._mail or {}).get("attachments", []) if a["part"] == part), None)
        self._say(f"Saving {att['name']}…" if att else "Saving…")

        def done(result, failure):
            if failure is not None:
                self._say(failure, "bad")
                return
            body = result if isinstance(result, dict) else {}
            self._say(self._saved_line(body))

        self._request("save_attachment", on=done, slow=True, id=o["id"], part=part)

    @staticmethod
    def _saved_line(body: dict) -> str:
        name = _line(body.get("name")) or "the file"
        folder = os.path.dirname(str(body.get("path") or ""))
        try:
            downloads = os.path.realpath(Path.home() / "Downloads")
            same = os.path.realpath(folder) == downloads
        except (OSError, RuntimeError):
            same = False
        where = "Downloads" if same else (os.path.basename(folder) or "your files")
        return f"Saved to {where} as {name}"

    # ---- accounts ---------------------------------------------------------------------------------------

    @Slot(str)
    def addAccount(self, email: str) -> None:
        email = _line(email)
        if not email or self._adding:
            return
        self._adding, self._add_error = True, ""
        self.setupChanged.emit()

        def done(_result, failure):
            self._adding = False
            self._add_error = str(failure) if failure is not None else ""
            self.setupChanged.emit()
            self._request("status", on=self._on_status)

        self._request("add_account", on=done, slow=True, email=email)

    @Slot(str)
    def removeAccount(self, account_id: str) -> None:
        def done(_result, failure):
            if failure is not None:
                self._add_error = str(failure)
                self.setupChanged.emit()
            self._request("status", on=self._on_status)

        self._request("remove_account", on=done, slow=True, id=account_id)

    @Slot()
    def showEngine(self) -> None:
        self._engine_window("stage")

    @Slot()
    def hideEngine(self) -> None:
        self._engine_window("hide")

    def _engine_window(self, action: str) -> None:
        def done(result, failure):
            if failure is not None:
                self._add_error = str(failure)
            else:
                self._add_error = ""
                self._staged = bool(isinstance(result, dict) and result.get("staged")) and action == "stage"
            self.setupChanged.emit()

        self._request("engine_window", on=done, action=action)

    # ---- the reply box ----------------------------------------------------------------------------------

    @Slot(str)
    @Slot(str, str)
    def reply(self, kind: str, mail_id: str = "") -> None:
        mail_id = mail_id or (self._opened["id"] if self._opened else "")
        if kind not in KINDS or not mail_id:
            return
        if self._opened is None or self._opened["id"] != mail_id:
            self.openMail(mail_id)
        else:
            self._leave_box()

        def listed(result, failure):
            same = next((d for d in result if isinstance(d, dict) and d.get("reply_to") == mail_id
                         and d.get("kind") == kind and d.get("state") in ("open", "unknown")), None) \
                if failure is None and isinstance(result, list) else None
            if same is not None:
                self._open_loaded(same)
                return
            self._request("draft", on=made, kind=kind, reply_to=mail_id)

        def made(result, failure):
            if failure is not None:
                self._say(failure, "bad")
            else:
                self._open_loaded(result)
                self._refresh_soon("drafts")

        # An earlier reply to this mail that was never sent is the one to go on with, not a second one.
        self._request("list", on=listed, view="drafts", limit=LIST_MAX)

    @Slot()
    @Slot(str)
    def newMail(self, account: str = "") -> None:
        self._leave_box()
        self.closeMail()
        account = account or self._account_of_view() or ""

        def made(result, failure):
            if failure is not None:
                self._say(failure, "bad")
            else:
                self._open_loaded(result)
                self._refresh_soon("drafts")

        self._request("draft", on=made, kind="new", **({"account": account} if account else {}))

    @Slot(str)
    def openDraft(self, draft_id: str) -> None:
        if not draft_id:
            return
        self._leave_box()
        self.dismissReceipt()

        def done(result, failure):
            if failure is not None:
                self._say(failure, "bad")
            else:
                self._open_loaded(result)

        self._request("draft_get", on=done, id=draft_id)

    def _open_loaded(self, d) -> None:
        """A draft the service has just answered with, into the box. The box reports it drawn once it has."""
        if not isinstance(d, dict) or not d.get("id"):
            return
        self._leave_box()
        self._draft = d
        self._shown = ""
        self._shown_asked = ""
        self._want = {f: _render(d, f) for f in FIELDS}
        self._sent = dict(self._want)
        self._flying.clear()
        self._edit_errors.clear()
        self._attach_error = ""
        self._press, self._press_for, self._press_line, self._looked = "", None, "", False
        self._edits.stop()
        reply_to = d.get("reply_to")
        if reply_to and (self._opened is None or self._opened["id"] != reply_to):
            self._show_mail_for(reply_to)
        elif not reply_to:
            self.closeMail()
        self.dismissReceipt()
        self.draftChanged.emit()
        self.draftLoaded.emit()
        self.selectionChanged.emit()
        self.gateChanged.emit()

    def _show_mail_for(self, mail_id: str) -> None:
        """The mail a draft answers, above its box, without taking the box away."""
        row = next((r for r in self._rows if r["id"] == mail_id), None)
        self._read_gen += 1
        gen = self._read_gen
        self._opened = dict(row) if row else {"id": mail_id, "sender": "", "address": "", "subject": "", "when": "",
                                              "unread": False, "flagged": False, "needsReply": False, "why": None,
                                              "attachments": False}
        self._mail, self._mail_state, self._mail_error = None, "loading", ""

        def done(result, failure):
            if gen != self._read_gen:
                return
            if failure is not None:
                self._mail_state, self._mail_error = "error", str(failure)
            else:
                self._mail_state = "ready"
                self._read_landed(result)
            self.selectionChanged.emit()

        self._request("read", on=done, id=mail_id)

    @Slot()
    def closeDraft(self) -> None:
        """Leave the box. What was typed is saved first; the draft stays in Drafts."""
        self._leave_box()
        self.selectionChanged.emit()

    def _leave_box(self) -> None:
        if self._draft is None:
            return
        self._flush()
        self._edits.stop()
        self._settler.stop()
        self._draft, self._shown, self._shown_asked = None, "", ""
        self._want, self._sent, self._flying, self._edit_errors = {}, {}, {}, {}
        self._attach_error = ""
        self._press, self._press_for, self._press_line, self._looked = "", None, "", False
        self.draftChanged.emit()
        self.draftLoaded.emit()
        self.gateChanged.emit()
        if self._view == "drafts":
            self.selectionChanged.emit()
        self._refresh_soon("drafts")

    def _look_at_draft(self, reshow: bool = False) -> None:
        """Ask the service what the draft in the box is now. Something else may have changed it (or the service
        started again), and what the box shows has to be what is there."""
        d = self._draft
        if d is None:
            return
        did = d["id"]

        def done(result, failure):
            if self._draft is None or self._draft["id"] != did:
                return
            if failure is not None:
                if failure.code == "not_found":
                    self._say("That draft is gone.")
                    self._drop_box()
                return
            self._adopt(result, reshow=reshow)

        self._request("draft_get", on=done, id=did)

    def _drop_box(self) -> None:
        self._draft = None
        self._want, self._sent = {}, {}
        self.draftChanged.emit()
        self.draftLoaded.emit()
        self.gateChanged.emit()
        self.selectionChanged.emit()

    def _adopt(self, d, reshow: bool = False) -> None:
        """The service's answer for the draft in the box becomes the draft the box is held to. A field that the
        person has not touched since and that the answer says differently about is loaded again, so what is shown
        stays what is there; whatever the person is typing stays."""
        if not isinstance(d, dict) or self._draft is None or d.get("id") != self._draft["id"]:
            return
        changed_elsewhere = d.get("updated") != self._draft.get("updated") or d.get("fingerprint") != self._draft.get(
            "fingerprint")
        self._draft = d
        if changed_elsewhere or reshow:
            self._shown = ""
        reload = False
        if not self._pending_edits():
            for f in FIELDS:
                theirs = _render(d, f)
                if _norm(f, theirs) != _norm(f, self._sent.get(f, "")) and self._want.get(f) == self._sent.get(f):
                    self._want[f] = self._sent[f] = theirs
                    reload = True
        if d["state"] != "unknown":
            self._looked = False
        self.draftChanged.emit()
        if reload:
            self.draftLoaded.emit()
        self.gateChanged.emit()

    # -- what the person types

    @Slot(str, str)
    def editField(self, field: str, text: str) -> None:
        if field not in FIELDS or self._draft is None or self._draft["state"] not in ("open", "unknown"):
            return
        self._want[field] = text
        self._edits.start()
        self.gateChanged.emit()

    @Slot()
    def flush(self) -> None:
        self._edits.stop()
        self._flush()

    def _flush(self) -> None:
        d = self._draft
        if d is None:
            return
        for f in FIELDS:
            if self._want.get(f) != self._sent.get(f) and self._flying.get(f) != self._want.get(f):
                self._edit_field(d["id"], f, self._want[f])
        self.gateChanged.emit()

    def _edit_field(self, did: str, field: str, value: str) -> None:
        self._inflight += 1
        self._flying[field] = value

        def done(result, failure):
            self._inflight = max(0, self._inflight - 1)
            if self._flying.get(field) == value:
                self._flying.pop(field, None)
            if self._draft is None or self._draft["id"] != did:
                if failure is not None:
                    self._say(failure, "bad")
                return
            if failure is None:
                self._sent[field] = value
                self._edit_errors.pop(field, None)
                self._shown = ""
                self._adopt(result)
            else:
                self._edit_errors[field] = str(failure)
                if failure.code in ("refused", "not_found"):
                    self._look_at_draft()
            self.gateChanged.emit()

        self._request("draft_edit", on=done, id=did, **{field: value})

    def _pending_edits(self) -> bool:
        return self._edits.isActive() or self._inflight > 0 or any(
            self._want.get(f) != self._sent.get(f) for f in FIELDS)

    @Slot(list)
    def addFiles(self, urls) -> None:
        d = self._draft
        files = []
        for u in urls or []:
            path = QUrl(str(u)).toLocalFile() or (str(u) if str(u).startswith("/") else "")
            if path:
                files.append(path)
        if not d or not files:
            return
        self._attach_error = ""
        self._inflight += 1

        def done(result, failure):
            self._inflight = max(0, self._inflight - 1)
            if self._draft is None or self._draft["id"] != d["id"]:
                return
            if failure is not None:
                self._attach_error = str(failure)
            else:
                self._shown = ""
                self._adopt(result)
            self.gateChanged.emit()

        self._request("draft_edit", on=done, id=d["id"], add_attachments=files)
        self.gateChanged.emit()

    @Slot(str)
    def removeAttachment(self, name: str) -> None:
        d = self._draft
        if not d or not name:
            return
        self._attach_error = ""
        self._inflight += 1

        def done(result, failure):
            self._inflight = max(0, self._inflight - 1)
            if self._draft is None or self._draft["id"] != d["id"]:
                return
            if failure is not None:
                self._attach_error = str(failure)
            else:
                self._shown = ""
                self._adopt(result)
            self.gateChanged.emit()

        self._request("draft_edit", on=done, id=d["id"], remove_attachments=[name])
        self.gateChanged.emit()

    @Slot()
    def discardDraft(self) -> None:
        d = self._draft
        if d is None:
            return

        def done(_result, failure):
            if failure is not None:
                self._say(failure, "bad")
                return
            if self._draft is not None and self._draft["id"] == d["id"]:
                self._edits.stop()
                self._draft, self._want, self._sent = None, {}, {}
                self.draftChanged.emit()
                self.draftLoaded.emit()
                self.gateChanged.emit()
                self.selectionChanged.emit()
            self._refresh_soon("drafts", "list")

        self._request("draft_discard", on=done, id=d["id"])

    # -- the draft is drawn

    @Slot(str, str)
    def reportShown(self, draft_id: str, fingerprint: str) -> None:
        """The box has drawn this draft, with this fingerprint: tell the service, which will let a press carry
        it. Said again for every draft the box draws, and only for the one the service last answered with."""
        d = self._draft
        if d is None or d["id"] != draft_id or d["fingerprint"] != fingerprint or self._pending_edits():
            return
        if self._shown == fingerprint or self._shown_asked == fingerprint or d["state"] not in ("open", "unknown"):
            return
        self._shown_asked = fingerprint

        def done(_result, failure):
            if self._shown_asked == fingerprint:
                self._shown_asked = ""
            now = self._draft
            if failure is None:
                if now is not None and now["id"] == draft_id and now["fingerprint"] == fingerprint:
                    self._shown = fingerprint
                    self.gateChanged.emit()
            elif failure.code == "changed" and now is not None and now["id"] == draft_id:
                self._look_at_draft(reshow=True)   # it is not what the box was told it is: take what is there
            elif failure.code != "unavailable":
                self._say(failure, "bad")

        self._request("draft_shown", on=done, id=draft_id, fingerprint=fingerprint)

    # ---- Send ------------------------------------------------------------------------------------------

    def _gate(self) -> str:
        """"" when Send may be pressed; else why not, in one quiet sentence."""
        d = self._draft
        if d is None:
            return "There is no draft."
        if not self._agent_up:
            return NOT_AGENTD
        if not self._connected:
            return DOWN
        if self._press == "sending":
            return "Waiting for Mail to say it went."
        if self._press == "checking":
            return "Checking whether that went."
        if d["state"] == "sending":
            return "This is being sent."
        if d["state"] not in ("open", "unknown"):
            return "This draft is closed."
        if self._edit_errors:
            return next(iter(self._edit_errors.values()))
        if self._pending_edits():
            return "Saving"
        if self._shown != d["fingerprint"]:
            return "Waiting for Mail to confirm what you see."
        if not any(d.get(f) for f in RECIPIENTS):
            return "Add who it is going to."
        if d["state"] == "unknown" and not self._looked:
            return "Tick the box once you have looked in Sent."
        return ""

    @Slot(bool)
    def setLooked(self, looked: bool) -> None:
        if self._draft is not None and self._draft["state"] == "unknown":
            self._set("_looked", bool(looked), self.gateChanged)

    @Slot()
    def press(self) -> None:
        """The person pressed Send. This is the only place a press is made, and the Send button's click handler
        is the only caller. Nothing is sent that the service has not been told was drawn."""
        if CHECKING or self._gate() != "":
            return
        d = self._draft
        line = {"type": "press", "kind": "mail", "id": d["id"], "fingerprint": d["fingerprint"]}
        if d["state"] == "unknown":
            line["again"] = True
        if self._agent.send(line) is not None:
            self._press_line = "That press did not go anywhere. Nothing was sent."
            self.gateChanged.emit()
            return
        self._press, self._press_for, self._press_line = "sending", (d["id"], d["fingerprint"]), ""
        self._press_due = time.monotonic() + PRESS_S
        self.gateChanged.emit()

    def _on_agent_line(self, msg: dict) -> None:
        if msg.get("type") != "press_result" or self._press_for is None:
            return
        if msg.get("kind") != "mail" or msg.get("id") != self._press_for[0]:
            return
        did = self._press_for[0]
        if self._draft is None or self._draft["id"] != did:
            self._press, self._press_for = "", None
            return
        receipt = msg.get("receipt") if isinstance(msg.get("receipt"), dict) else None
        line = _line(msg.get("line"))
        if msg.get("ok") is True:
            self._finish_sent(receipt or {"line": line or "Sent."})
            return
        self._press, self._press_for = "", None
        code = str(msg.get("code") or "")
        self._press_line = line or "That did not go. Nothing was sent."
        if code == "unknown_outcome":
            self._press_line = line or UNKNOWN_LINE
        self.gateChanged.emit()
        self._look_at_draft(reshow=code == "changed")

    def _lost(self) -> None:
        """agentd went away while a press was out: the answer is lost, and the service knows what became of it."""
        self._press, self._settle = "checking", 0
        self._press_line = LOST_LINE
        self.gateChanged.emit()
        self._settler.start()

    def _settle_look(self) -> None:
        d = self._draft
        if d is None or self._press != "checking":
            return
        self._settle += 1
        did = d["id"]

        def done(result, failure):
            if self._draft is None or self._draft["id"] != did or self._press != "checking":
                return
            state = result.get("state") if failure is None and isinstance(result, dict) else None
            if state == "sent":
                self._finish_sent(result.get("receipt") or {"line": "Sent."})
            elif state == "unknown":
                self._press, self._press_for, self._press_line = "", None, UNKNOWN_LINE
                self._adopt(result)
            elif state == "open" and self._settle >= SETTLE_OPEN_TRIES or self._settle >= SETTLE_TRIES:
                self._press, self._press_for = "", None
                self._press_line = "Nothing was sent. Press Send again if you still want it to go."
                if state is not None:
                    self._adopt(result)
                self.gateChanged.emit()
            else:
                self._settler.start()

        self._request("draft_get", on=done, id=did)

    def _finish_sent(self, receipt: dict) -> None:
        d = self._draft
        if d is None:
            return
        gone = d["id"]
        reply_to = d.get("reply_to")
        self._edits.stop()
        self._settler.stop()
        self._draft, self._want, self._sent, self._flying, self._edit_errors = None, {}, {}, {}, {}
        self._press, self._press_for, self._press_line, self._looked = "", None, "", False
        self._receipt = {"line": _line(receipt.get("line")) or "Sent.",
                         "webName": _line((receipt.get("web") or {}).get("name")) if isinstance(
                             receipt.get("web"), dict) else "",
                         "webUrl": receipt["web"]["url"] if isinstance(receipt.get("web"), dict) and safe_url(
                             receipt["web"].get("url")) else ""}
        self.draftChanged.emit()
        self.draftLoaded.emit()
        self.gateChanged.emit()
        self.receiptChanged.emit()
        self._refresh_soon("list", "drafts", "accounts")
        if self._view != "drafts" and reply_to:
            after = self._next_after(reply_to)
            self._rows = [r for r in self._rows if not (self._view == "needs_reply" and r["id"] == reply_to)]
            self._apply_filter(emit=False)
            self.listChanged.emit()
            if after:
                self._open_after_send(after)
            elif self._view == "needs_reply":
                self.closeMail()
        elif self._view == "drafts":
            self._rows = [r for r in self._rows if r["id"] != gone]
            self._apply_filter(emit=False)
            self.listChanged.emit()
            self.closeMail()
        self.selectionChanged.emit()

    def _open_after_send(self, mail_id: str) -> None:
        """The next unread mail opens, and the receipt stays under it until the person does something else."""
        self.openMail(mail_id, keep_receipt=True)

    # ---- sample mail, for `bombadil-app check` ------------------------------------------------------------

    def _sample(self) -> None:
        """What `bombadil-app check mail` shows with no service: a few made-up mails and a made-up reply."""
        now = time.time()
        self._connected = self._agent_up = self._ready = True
        self._engine = "up"
        self._accounts = [
            {"id": "a1", "email": "maya@acme.example", "name": "maya@acme.example", "provider": "google",
             "state": "ok", "note": "", "unread": 3, "web": {"name": "Gmail", "url": "https://mail.google.com/"}},
            {"id": "a2", "email": "maya.reyes@gmail.example", "name": "maya.reyes@gmail.example",
             "provider": "google", "state": "ok", "note": "", "unread": 1,
             "web": {"name": "Gmail", "url": "https://mail.google.com/"}},
            {"id": "a3", "email": "maya.reyes@lakeside.example", "name": "maya.reyes@lakeside.example",
             "provider": "microsoft", "state": "blocked",
             "note": "lakeside.example asks an admin to approve mail apps.", "unread": 0,
             "web": {"name": "Outlook", "url": "https://outlook.office.com/mail/"}}]
        self._counts = {"unread": 4, "needs_reply": 3, "drafts": 1}

        def msg(i, who, addr, subject, ago, **kw):
            return self._row({"id": f"a1/{i}", "account": "a1", "from": {"name": who, "email": addr},
                              "subject": subject, "ts": now - ago * 60, "unread": kw.get("unread", False),
                              "attachments": kw.get("attachments", False), "flagged": kw.get("flagged", False),
                              "needs_reply": kw.get("why") is not None, "why": kw.get("why")})

        self._rows = [
            msg("s1", "Priya Shah", "priya@acme.example", "Launch date by noon?", 55, unread=True,
                why="Legal wants the date by noon."),
            msg("s2", "Sam Ortiz", "sam@acme.example", "Pricing copy: ok to go?", 140, unread=True,
                why="Asks for a yes on the pricing copy."),
            msg("s3", "Leo Park", "leo@acme.example", "A or B for the empty state?", 190, unread=True),
            msg("s4", "Brightline Billing", "billing@brightline.example", "Invoice F-0907 for September", 1200,
                attachments=True),
            msg("s5", "Flow Weekly", "news@flowweekly.example", "Flow Weekly: five habits of calm teams", 1800)]
        self._messages = list(self._rows)
        self._list_state = "ready"
        o = self._rows[0]
        self._opened = {**o, "to": "Maya Reyes <maya@acme.example>", "cc": "", "from": "Priya Shah <priya@acme.example>",
                        "full_when": full_when(o["ts"]), "folder": "inbox"}
        self._mail = {"text": "Hi Maya,\n\nLegal needs the launch date by noon. Does the 14th work on your side?"
                              "\n\nPriya",
                      "truncated": False, "htmlOnly": False, "attachments": [], "webName": "Gmail",
                      "webUrl": "https://mail.google.com/mail/u/maya@acme.example/"}
        self._mail_state = "ready"
        kind = os.environ.get("BOMBADIL_MAIL_SAMPLE", "")
        if kind == "first-run":
            self._accounts, self._rows, self._messages, self._opened, self._mail = [], [], [], None, None
            self._counts = {"unread": 0, "needs_reply": 0, "drafts": 0}
            self._mail_state = "none"
        elif kind == "reply":
            d = {"id": "d1", "account": "a1", "kind": "reply", "reply_to": "a1/s1", "from": {
                "name": "Maya Reyes", "email": "maya@acme.example"},
                "to": [{"name": "Priya Shah", "email": "priya@acme.example"}], "cc": [], "bcc": [],
                "subject": "Re: Launch date by noon?", "body": "The 14th works on my side, if legal signs off "
                "Thursday.\n\nMaya", "attachments": [{"name": "launch-plan.pdf", "size": 182000, "sha256": "0" * 64}],
                "fingerprint": "f" * 64, "created_by": "agent", "state": "open", "receipt": None, "updated": now,
                "warnings": [{"kind": "new_address", "addresses": ["legal@acme-partners.example"],
                              "text": "New address: legal@acme-partners.example. You did not type it, this thread "
                                      "does not have it, and mail that was read for you may have asked for it. "
                                      "Check who it is before you press Send."}],
                "adds": "Your Gmail signature and the quoted message are added when it sends."}
            d["cc"] = [{"name": "", "email": "legal@acme-partners.example"}]
            self._draft = d
            self._want = {f: _render(d, f) for f in FIELDS}
            self._sent = dict(self._want)
            self._shown = d["fingerprint"]

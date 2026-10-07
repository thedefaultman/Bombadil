"""The Slack setup recipe: walks the person through making their own private Slack app in the browser panel, with
Bombadil's pointer on each press, and takes the two tokens off Slack's pages into the connection service
(docs/CONNECT.md, "The setup recipe").

`run_setup` is blocking and runs in one of agentd's worker threads. It speaks to two services only through their
client modules, which a test can replace: the browser service (`browserd`: open, point, wait, read, take) and the
connection service (`connect`: it only asks for `connections`). The steps, each waiting for its page before the next:

1. open `manifest.creation_url()` and point at Create (or Next, if Slack asks for a workspace first);
2. on the new app's page point at Generate Token and Scopes (App-Level Tokens); the person names the token, adds
   `connections:write` and presses Generate; the `xapp-` value is taken off the page into `app_token`;
3. point at Install to Workspace, then at Allow;
4. on the OAuth & Permissions page take the `xoxp-` User OAuth Token into `user_token`;
5. ask the connection service what became of the connection.

The rules it keeps:

- The person presses everything. This never clicks, types or navigates: it opens the first page (the panel opening)
  and points. Where it cannot find a button by its words it says what it is looking for ("Look for Install to
  Workspace on the page.") and keeps waiting, rather than failing.
- A token never passes through here. It is taken by the browser service, which reads it off the page and hands it
  to the connection service; all this module sees is "stored" or not. It never calls `store_secret` itself, and what
  it says never holds a token.
- Slack's words are not Bombadil's. Pages are read only to look for a few words (an administrator's approval wall),
  and the one thing from the outside that is repeated, the workspace's name, is quoted.
- It stops, never raises. A wall ("an administrator must approve apps") is said once in plain words and ends it with
  `ok` false; so does a page that never comes (each wait is generous: the person is reading and pressing), a closed
  tab, a service that is not running, and the person calling it off, which is answered within a fraction of a second
  (a call to the browser service runs on its own thread while this one watches `cancelled()`).

It returns `{"ok": bool, "say": "one sentence"}`. The last sentence is returned, not passed to `say`: the caller says
it. Unverified against Slack's real pages: it finds things by their visible words and by token shape (see
tests/browserd_fakes/slack_pages.py for the pages it is tested against).
"""

import re
import threading
import time

from ..browserd import client as browserd_client
from . import client as connect_client
from . import manifest, protocol

# The app's own pages: https://api.slack.com/apps/A0123456789/..., and its OAuth & Permissions page.
APP_PAGE = r"/apps/A[A-Z0-9]+"
TOKENS_PAGE = r"/apps/A[A-Z0-9]+/oauth"
TOKENS_WORDS = "User OAuth Token"

# How long each page is waited for: the person is reading, logging in, choosing a workspace and pressing.
CREATE_WAIT_S = 600.0
APP_TOKEN_WAIT_S = 900.0
INSTALL_WAIT_S = 900.0
USER_TOKEN_WAIT_S = 120.0
CONNECTION_WAIT_S = 20.0

SLICE_S = 0.6            # one `wait` of the browser service looks at the page this long
PAUSE_S = 0.5            # between two looks for a token
POINT_TTL_S = 30.0       # how long the pointer stays when nothing changes ...
REPOINT_S = 25.0         # ... and when it is drawn again
HINT_AFTER_S = 8.0       # a button not found by its words for this long is said out loud, once
WALL_EVERY_S = 2.0
CALL_S = 15.0
OPEN_S = 60.0            # opening the page may start the browser

SAY_OPEN = "I've opened Slack's page to make the app. Press Create."
SAY_APP = ("The app is made. It needs a token to listen with: press Generate Token and Scopes under App-Level "
           "Tokens, name it anything, add the scope connections:write and press Generate.")
SAY_INSTALL = "I have the first token and stored it. Now press Install to Workspace."
SAY_ALLOW = "Slack asks you to confirm. Press Allow."
SAY_TOKEN = "I have the second token and stored it."
SAY_WALL = ("Your Slack workspace needs an administrator to approve this app before it can be installed. Ask them "
            "to approve it, then connect Slack again.")
SAY_STOPPED = "Setup stopped."

# What an approval wall says: something about approval or a request to install, and an administrator.
_WALL_ASKS = ("approval", "approve", "request to install", "request approval")
_WALL_WHO = ("administrator", "workspace admin", "admins")


class _Stop(Exception):
    """The recipe ends here: `ok` and the sentence for the person."""

    def __init__(self, ok: bool, sentence: str):
        super().__init__(sentence)
        self.ok = ok
        self.sentence = sentence


class _NotReady(Exception):
    """The page did not answer just now (it is changing): look again."""


def _sentence(value, fallback: str) -> str:
    """A sentence of the services' own (their exceptions' text) or the fallback; never a stack trace's words."""
    if isinstance(value, (connect_client.ConnectError, connect_client.ConnectUnavailable)):
        return str(value)
    return value if isinstance(value, str) and value else fallback


def _has_wall_words(text: str) -> bool:
    text = text.lower()
    return "request to install" in text or (any(w in text for w in _WALL_ASKS) and any(w in text for w in _WALL_WHO))


class _Setup:
    def __init__(self, connection_id, say, cancelled, browserd, connect):
        self.cid = connection_id
        self.say = say
        self.cancelled = cancelled
        self.b = browserd
        self.c = connect
        self.stored: list[str] = []
        self._up: tuple[str, float] | None = None      # the words the pointer is on, and since when
        self._announced: set[str] = set()
        self._started = time.monotonic()
        self._pointed_here = False
        self._hinted = False

    # -- the walk --

    def go(self) -> dict:
        if not (isinstance(self.cid, str) and protocol.valid_connection_id(self.cid) and self.cid.startswith("slack:")):
            raise _Stop(False, "That is not a Slack connection.")
        self._connection()   # the connection is there, and the connection service is running, before anything opens
        self._call("open", OPEN_S, url=manifest.creation_url())
        self._say(SAY_OPEN)
        self._phase()
        self._until(lambda: self._arrived(APP_PAGE), CREATE_WAIT_S, "the app being made", "Create",
                    pointers=(("Create", "Yours: Create", None), ("Next", "Yours: Next", None)))
        self._say(SAY_APP)
        self._phase()
        self._until(lambda: self._take(manifest.APP_TOKEN_PREFIX, "app_token"), APP_TOKEN_WAIT_S, "the first token",
                    "Generate Token and Scopes", pointers=(("Generate Token and Scopes", "Yours: Generate Token", None),),
                    pause=PAUSE_S)
        self.stored.append("app")
        self._say(SAY_INSTALL)
        self._phase()
        self._until(lambda: self._arrived(TOKENS_PAGE, TOKENS_WORDS), INSTALL_WAIT_S, "the install finish",
                    "Install to Workspace", wall=True,
                    pointers=(("Install to Workspace", "Yours: Install", None), ("Allow", "Yours: Allow", SAY_ALLOW)))
        self._phase()
        self._until(lambda: self._take(manifest.USER_TOKEN_PREFIX, "user_token"), USER_TOKEN_WAIT_S,
                    "the second token", TOKENS_WORDS, pause=PAUSE_S)
        self.stored.append("user")
        self._say(SAY_TOKEN)
        return self._finish()

    def _phase(self) -> None:
        """A new page's worth of work: the pointer of the last one goes, and what was said about it is forgotten."""
        self._unpoint()
        self._started = time.monotonic()
        self._pointed_here = False
        self._hinted = False

    def _until(self, check, total: float, what: str, look_for: str, pointers=(), wall: bool = False,
               pause: float = 0.0) -> None:
        """Look until `check()` is true: point at what the person is to press, say what is being looked for when it
        cannot be found by its words, watch for an approval wall, and give up after `total` seconds."""
        deadline = time.monotonic() + total
        last_wall = None
        while True:
            self._check_cancelled()
            if check():
                return
            now = time.monotonic()
            if pointers:
                self._point(pointers)
            if not self._pointed_here and not self._hinted and now - self._started >= HINT_AFTER_S:
                self._hinted = True
                self._say(f"Look for {look_for} on the page.")
            if wall and (last_wall is None or now - last_wall >= WALL_EVERY_S):
                last_wall = now
                if self._wall_up():
                    raise _Stop(False, SAY_WALL)
            if now >= deadline:
                raise _Stop(False, self._gave_up(what))
            self._nap(pause)

    # -- looking --

    def _arrived(self, url: str, words: str | None = None) -> bool:
        args = {"url": url, "seconds": SLICE_S}
        if words:
            args["text"] = words
        seen = self._look("wait", **args)
        return bool(seen and seen.get("matched"))

    def _take(self, prefix: str, name: str) -> bool:
        """Take the token off the page into the connection. Never abandoned half way: the person calling setup off
        is answered right after, not in the middle of a token being stored."""
        pattern = re.escape(prefix) + r"[A-Za-z0-9-]{20,}"
        got = self._call("take", CALL_S, abandon=False, pattern=pattern, into={"connection": self.cid, "name": name})
        if got.get("stored") is True:
            return True
        if got.get("found"):
            raise _Stop(False, f"{self._where()}{_sentence(got.get('why'), 'It could not be stored.')} Setup stopped.")
        return False

    def _wall_up(self) -> bool:
        """Does the page say an administrator must approve apps, with no button left to install by?"""
        page = self._look("read")
        if not page or not _has_wall_words(str(page.get("text") or "")):
            return False
        for button in ("Install to Workspace", "Allow"):
            seen = self._look("find", text=button)
            if seen is None or seen.get("found"):
                return False
        return True

    def _point(self, pointers) -> bool:
        """Point at the first of `pointers` the page has, and keep it there (drawn again before it times out). A
        pointer that is up stays while its words are still on the page: the person's own scrolling is left alone."""
        now = time.monotonic()
        if self._up is not None:
            words, since = self._up
            if now - since < REPOINT_S:
                seen = self._look("find", text=words)
                if seen is None or seen.get("found"):
                    return True
            self._up = None
        for words, label, announce in pointers:
            got = self._look("point", text=words, label=label, ttl=POINT_TTL_S)
            if got and got.get("pointed"):
                self._up, self._pointed_here = (words, now), True
                if announce and announce not in self._announced:
                    self._announced.add(announce)
                    self._say(announce)
                return True
        return False

    def _unpoint(self) -> None:
        if self._up is not None:
            self._up = None
            try:
                self.b.notify("unpoint")
            except Exception:  # noqa: BLE001 - taking the pointer away is a courtesy
                pass

    # -- the end --

    def _connection(self) -> dict:
        try:
            listed = self.c.request("connections")
        except Exception as e:  # noqa: BLE001 - said as the sentence it is, below
            raise _Stop(False, _sentence(e, "Connections could not be asked just now.")) from None
        rows = listed.get("connections") if isinstance(listed, dict) else None
        found = next((r for r in rows or [] if isinstance(r, dict) and r.get("id") == self.cid), None)
        if found is None:
            raise _Stop(False, "That connection is not there any more.")
        return found

    def _finish(self) -> dict:
        deadline = time.monotonic() + CONNECTION_WAIT_S
        while True:
            conn = self._connection()
            state = conn.get("state")
            if state == "ok":
                name = protocol.one_line(conn.get("name"), 60)
                return {"ok": True, "say": f"Slack is connected to “{name}”." if name else "Slack is connected."}
            if state == "blocked":
                raise _Stop(False, SAY_WALL)
            if state == "error":
                note = protocol.one_line(conn.get("note"), 160)
                raise _Stop(False, f"Slack would not take the connection: “{note}”" if note
                            else "Slack would not take the connection.")
            if time.monotonic() >= deadline:
                raise _Stop(False, "Both tokens are stored, but Slack has not answered yet. Look in Connected here "
                                   "in a moment.")
            self._nap(PAUSE_S)

    def _gave_up(self, what: str) -> str:
        return f"{self._where()}I did not see {what}, so I stopped. Say connect Slack to try again."

    def _where(self) -> str:
        if len(self.stored) == 2:
            return "Both tokens are stored. "
        return "The first token is stored. " if self.stored else ""

    # -- calls --

    def _look(self, op: str, **args):
        """A call to the browser service for something to look at: None when the page was not ready to say."""
        try:
            return self._call(op, CALL_S, **args)
        except _Stop:
            raise
        except _NotReady:
            return None

    def _call(self, op: str, timeout: float = CALL_S, abandon: bool = True, **args):
        """One call to the browser service, on a thread of its own while this one watches for the person calling
        setup off. The service's no is a sentence that ends the recipe, except a page that is not ready yet."""
        box: dict = {}

        def work():
            try:
                box["result"] = self.b.request(op, timeout, **args)
            except BaseException as e:  # noqa: BLE001 - handed to the thread that asked
                box["error"] = e

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.05)
            if abandon and self.cancelled():
                raise _Stop(False, SAY_STOPPED)
        error = box.get("error")
        if error is None:
            return box.get("result") if isinstance(box.get("result"), dict) else {}
        if isinstance(error, browserd_client.BrowserdError):
            if error.code == "page":
                raise _NotReady
            raise _Stop(False, f"{error} Setup stopped.")
        if isinstance(error, browserd_client.BrowserdUnavailable):
            raise _Stop(False, f"{error} Setup stopped.")
        raise _Stop(False, "The browser service did not answer as expected. Setup stopped.")

    def _check_cancelled(self) -> None:
        if self.cancelled():
            raise _Stop(False, SAY_STOPPED)

    def _nap(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self._check_cancelled()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def _say(self, sentence: str) -> None:
        try:
            self.say(sentence)
        except Exception:  # noqa: BLE001 - telling the person is never worth stopping for
            pass


def run_setup(connection_id: str, say, cancelled=lambda: False, *, browserd=None, connect=None) -> dict:
    """Walk the person through making the Slack app and give both tokens to `connection_id` (docs/CONNECT.md).
    `say(sentence)` is told what is happening; `cancelled()` is asked all the time and ends it at once. Blocking."""
    run = _Setup(connection_id, say, cancelled, browserd or browserd_client, connect or connect_client)
    try:
        return run.go()
    except _Stop as stop:
        return {"ok": stop.ok, "say": stop.sentence}
    except Exception:  # noqa: BLE001 - a recipe that was not written for this page ends in a sentence, never a trace
        return {"ok": False, "say": f"{run._where()}Setup could not go on just now."}
    finally:
        run._unpoint()

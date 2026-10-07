"""One driver for every work tool that speaks MCP (docs/CONNECT.md, "Work tools, through MCP").

Linear, Notion, Atlassian's Jira, Todoist and ClickUp each run an official remote MCP server that a person
allows with a browser sign-in. `McpDriver` is the connection to any of them; what differs between the services is a
recipe (`recipes.py`, `share/connect/recipes/<service>.toml`). The driver is built on the official Python SDK: the
Streamable HTTP client, `ClientSession`, and its OAuth client provider, whose token store is `self.secrets`.

How it is shaped, and why:

- **Sign-in.** With no tokens `start()` begins the flow in a task of its own: a listener on 127.0.0.1 (a port of its
  own) for the redirect, then the SDK's discovery, registration and authorization. The SDK registers "Bombadil"
  dynamically; when the server says it takes a client metadata document and an address for ours is given
  (`BOMBADIL_CLIENT_METADATA_URL`, or the recipe's `client_metadata_url`) that address is the client id instead.
  The authorization address is not opened here: it becomes the one step the person sees, `steps()`, and the state
  is `signin`. When the browser comes back, the code is exchanged, the tokens are stored through `Secrets` (under
  `tokens` and `client_info`), and the state is `ok`.
- **One connection per request.** The SDK's Streamable HTTP transport ends the whole session when any one request
  fails, so a session kept open for hours would be one that is mostly dead. A request here opens a connection,
  initializes, asks, and closes. That costs a round trip a few times an hour and means a failure has one cause and
  nothing to repair. Requests run one at a time, so a refresh of the token is never done twice.
- **Refresh is the SDK's.** The stored time the access token runs out is kept with it (the SDK only knows it for
  tokens it has just received), so a restart refreshes on time. The SDK does not refresh on a 401, so when one
  comes back the driver makes it refresh once and asks again; a refresh that fails, or a server that wants the
  person again, is a new sign-in: state `signin`, a fresh flow, and a note that says so.
- **Reading is the only thing done alone.** `tasks()` calls the recipe's list tool; every five minutes the same
  call runs in the background and `emit`s a `task` push for each item not seen before in this run. The first poll
  is silent, so a start-up is not a flood. Nothing else happens without a press.
- **Writing is only `perform`.** One tool call per press, with only the fields the recipe lists. A tool that says
  no (`isError`) or a request refused before it was written is a `DriverError`; a request that was written and
  then failed or timed out (30 s) is `UnknownOutcome`, and nothing retries. A request that cannot have been
  written (the server could not be reached, a timeout before the call went out) is never `UnknownOutcome`.
- **Nothing secret leaves.** Tokens are only in `Secrets`; they are not in a push, a receipt, a note, an exception's
  sentence or a log line. A sentence for the person is made of this module's own words; what a service said is
  quoted as its own, cut and plain. The SDK logs tracebacks of a failed sign-in whose text can quote the other
  side, so those are dropped from its log records.

What it does not do: open the browser (the service and the shell do, from `steps()`), keep messages, revoke tokens
at the service when a connection is removed, or know anything particular about a service (that is the recipe).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets as randomness
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlsplit

import httpx
from mcp import ClientSession, types
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.utils import is_valid_client_metadata_url
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken
from mcp.shared.exceptions import McpError

from . import recipes
from .driver import Driver, DriverError, Secrets, UnknownOutcome
from .protocol import log, one_line, valid_ref, web_url

MAX_FIELD = 20000       # characters of any one box the person filled in
MAX_SHOWN = 200         # characters of a service's own words in a sentence
MAX_PUSHED = 20         # new tasks announced by one poll at most
_ME = types.Implementation(name="bombadil", version="0.1.0")
_PLACEHOLDER_URI = "http://127.0.0.1/callback"     # for requests that never sign in; the SDK wants one


class _Plain(logging.Filter):
    """The SDK logs a failed sign-in with its whole traceback, and the text of a failure can quote the other side.
    Keep the line, drop the traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = record.exc_text = None
        return True


for _name in ("mcp.client.auth.oauth2", "mcp.client.streamable_http"):
    logging.getLogger(_name).addFilter(_Plain())


class _Signin(Exception):
    """Raised from the redirect handler of an ordinary request: the SDK wants the person to sign in again, so the
    request stops here."""


class _Denied(Exception):
    """The browser came back with `error=` (the person pressed Deny, or an administrator said no)."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class _TimedOut(Exception):
    """The person never came back."""


class _Unusable(Exception):
    """The authorization address was not one to open."""


class _Attempt:
    """What one request got as far as, so a failure can be told apart: before the call was written, or after."""

    def __init__(self):
        self.sent = False
        self.done = False
        self.value = None

    async def note(self, request: httpx.Request) -> None:
        try:
            if request.method == "POST" and b'"tools/call"' in request.content:
                self.sent = True
        except httpx.RequestNotRead:
            pass


class _PinnedScope(OAuthClientMetadata):
    """The SDK picks the scope itself, from the server's own advertising, which for some services is everything it
    has. A recipe that names its scopes keeps them: assignments of `scope` are ignored once it has one."""

    def __setattr__(self, name, value):
        if name == "scope" and self.scope:
            return
        super().__setattr__(name, value)


# -- the token store --

class _Storage:
    """The SDK's TokenStorage over `Secrets`: the tokens and the client's registration, as JSON, under `tokens` and
    `client_info`. The tokens carry `expires_at` beside them; the SDK works out when a token runs out only for one it
    has just been given, so after a restart it would send an old one. `oauth_metadata` is the authorization server's
    own description, kept because the SDK refreshes at the address it discovered while signing in and, without it,
    guesses `/token` on the MCP server's host, which is not where Atlassian, Todoist or ClickUp take a refresh."""

    def __init__(self, secrets: Secrets):
        self.secrets = secrets

    def _read(self, name: str) -> dict | None:
        raw = self.secrets.get(name)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def has_tokens(self) -> bool:
        stored = self._read("tokens")
        return bool(stored and stored.get("access_token"))

    def can_refresh(self) -> bool:
        stored = self._read("tokens")
        return bool(stored and stored.get("refresh_token") and self._read("client_info"))

    def expires_at(self) -> float | None:
        stored = self._read("tokens") or {}
        value = stored.get("expires_at")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def clear(self) -> None:
        held = self.secrets.names()
        for name in ("tokens", "client_info", "oauth_metadata"):
            if name in held:
                self.secrets.delete(name)

    async def get_tokens(self) -> OAuthToken | None:
        stored = self._read("tokens")
        try:
            return OAuthToken.model_validate(stored) if stored else None
        except ValueError:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        before = self._read("tokens") or {}
        if not tokens.refresh_token and before.get("refresh_token"):
            tokens.refresh_token = before["refresh_token"]      # a refresh that does not name one keeps the old
        body = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in is not None:
            body["expires_at"] = time.time() + int(tokens.expires_in)
        self.secrets.set("tokens", json.dumps(body))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        stored = self._read("client_info")
        try:
            return OAuthClientInformationFull.model_validate(stored) if stored else None
        except ValueError:
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.secrets.set("client_info", client_info.model_dump_json(exclude_none=True))

    def metadata(self) -> OAuthMetadata | None:
        stored = self._read("oauth_metadata")
        try:
            return OAuthMetadata.model_validate(stored) if stored else None
        except ValueError:
            return None

    def set_metadata(self, metadata: OAuthMetadata) -> None:
        self.secrets.set("oauth_metadata", metadata.model_dump_json(exclude_none=True))


# -- the loopback address the browser comes back to --

def _page(status: int, text: str) -> bytes:
    body = f"<!doctype html><meta charset=utf-8><title>Bombadil</title><p>{text}</p>".encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 410: "Gone"}[status]
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'\r\n"
            "Connection: close\r\n\r\n")
    return head.encode() + body


class _Listener:
    """A listener on 127.0.0.1 that answers the one request the sign-in ends with: `GET /callback?code=...&state=...`
    (or `error=`). It takes only a request whose `state` is the one the authorization address carried, answers the
    browser once it knows whether the sign-in worked, and has no other page."""

    def __init__(self, name: str, port: int = 0):
        self.name = name
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._state: str | None = None
        self._used = False
        self._came: asyncio.Future = asyncio.get_running_loop().create_future()
        self._outcome: asyncio.Future = asyncio.get_running_loop().create_future()
        self._handlers: set[asyncio.Task] = set()

    async def open(self) -> str:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/callback"

    def expect(self, state: str | None) -> None:
        self._state = state

    async def code(self) -> tuple[str, str]:
        code, state, error = await self._came
        if error:
            raise _Denied(error)
        return code, state

    def finish(self, ok: bool) -> None:
        if not self._outcome.done():
            self._outcome.set_result(ok)

    async def close(self) -> None:
        self.finish(False)
        if self._server is not None:
            self._server.close()
        waiting = [t for t in self._handlers if t is not asyncio.current_task()]
        if waiting:
            await asyncio.wait(waiting, timeout=2)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            status, text = await self._answer(head)
            writer.write(_page(status, text))
            await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _answer(self, head: bytes) -> tuple[int, str]:
        line = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
        if len(line) != 3 or line[0] != "GET":
            return 405, "Nothing is served here."
        target = urlsplit(line[1])
        if target.path != "/callback":
            return 404, "Nothing is served here."
        query = dict(parse_qsl(target.query))
        wrong = (self._state is None or not randomness.compare_digest(
            query.get("state", "").encode(), self._state.encode()))
        if wrong or not (query.get("code") or query.get("error")):
            return 400, "That is not an answer Bombadil is waiting for."
        if self._used:
            return 410, "That sign-in was already used. You can close this tab."
        self._used = True
        if query.get("error"):
            self._came.set_result(("", query["state"], query["error"]))
            return 200, f"{self.name} did not allow Bombadil. You can close this tab."
        self._came.set_result((query["code"], query["state"], None))
        try:
            ok = await asyncio.wait_for(asyncio.shield(self._outcome), 20)
        except TimeoutError:
            ok = False
        return 200, ("Bombadil is connected. You can close this tab." if ok else
                     "Bombadil could not finish connecting. You can close this tab.")


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaves(inner)]
    return [exc]


def _quoted(text: str) -> str:
    return one_line(text, MAX_SHOWN).replace('"', "'")


class McpDriver(Driver):
    kind = "mcp"

    OP_SECONDS = 30.0         # one request, whole: connecting, asking, the answer
    ADDRESS_SECONDS = 15.0    # for the sign-in address to be known
    SIGNIN_SECONDS = 900.0    # for the person to come back from the service's page
    START_SECONDS = 12.0      # start() waits this long for the first look at the service
    POLL_SECONDS = 300.0
    SEEN_MAX = 500

    def __init__(self, conn: dict, secrets: Secrets, emit: Callable[[dict], None], clock: Callable[[], float]):
        super().__init__(conn, secrets, emit, clock)
        self.service = str(conn.get("service") or "")
        self.op_seconds, self.address_seconds = self.OP_SECONDS, self.ADDRESS_SECONDS
        self.signin_seconds, self.start_seconds = self.SIGNIN_SECONDS, self.START_SECONDS
        self.poll_interval = self.POLL_SECONDS
        try:
            self.recipe: recipes.Recipe | None = recipes.load(self.service)
            self._recipe_error = ""
        except recipes.RecipeError as exc:
            self.recipe, self._recipe_error = None, str(exc)
        self._storage = _Storage(secrets)
        self._closed = False
        self._steps: list[dict] = []
        self._flow_task: asyncio.Task | None = None
        self._listener: _Listener | None = None
        self._poller: asyncio.Task | None = None
        self._first = asyncio.Event()
        self._lock = asyncio.Lock()
        self._auth: OAuthClientProvider | None = None
        self._vars: dict | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._urls: OrderedDict[str, str] = OrderedDict()
        self._baselined = False
        self._stragglers: set[asyncio.Task] = set()

    # -- what the service asks about --

    @property
    def name(self) -> str:
        return self.recipe.name if self.recipe else (self.service.capitalize() or "This service")

    def reads(self) -> str:
        return self.recipe.reads if self.recipe else ""

    def can_post(self) -> bool:
        return self.state == "ok" and self.recipe is not None and self.recipe.can_post

    def task_fields(self) -> dict:
        return self.recipe.task_fields() if self.recipe else {}

    def steps(self) -> list[dict]:
        return [dict(s) for s in self._steps] if self.state == "signin" else []

    def owns(self, kind: str, target: str) -> bool:
        if kind == "task_create":
            return target == self.service
        if kind == "task_comment":
            return isinstance(target, str) and target.startswith(self.service + ":")
        return False

    # -- life --

    async def start(self) -> None:
        if self.recipe is None:
            self._set_state("error", self._recipe_error or "I do not know how to connect to this service.")
            return
        if not self._storage.has_tokens():
            await self._signin("", reset=True)
            return
        self._first.clear()
        self._poller = asyncio.ensure_future(self._poll_loop())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._first.wait(), self.start_seconds)
        if self.state not in ("ok", "signin"):
            self._set_state("ok", "")

    async def stop(self) -> None:
        self._closed = True
        running = [t for t in (self._flow_task, self._poller, *self._stragglers) if t is not None and not t.done()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        if self._listener is not None:
            await self._listener.close()
        self._steps = []

    def _set_state(self, state: str, note: str = "") -> None:
        if self._closed:
            return
        self.state, self.note = state, note
        self._emit({"push": "state", "state": state, "note": note})

    def _emit(self, push: dict) -> None:
        if self._closed:
            return
        try:
            self.emit(push)
        except Exception as exc:
            log(f"{self.service}: a push was not taken ({type(exc).__name__})")

    # -- the sign-in --

    def _provider(self, redirect_uri: str, redirect, callback) -> OAuthClientProvider:
        recipe = self.recipe
        doc = recipes.client_metadata()
        make = _PinnedScope if recipe.scope else OAuthClientMetadata
        meta = make(redirect_uris=[redirect_uri], token_endpoint_auth_method="none",
                    grant_types=list(recipe.grants), response_types=doc["response_types"],
                    client_name=doc["client_name"], scope=recipe.scope or None)
        address = os.environ.get("BOMBADIL_CLIENT_METADATA_URL") or recipe.client_metadata_url
        return OAuthClientProvider(recipe.url, meta, self._storage, redirect, callback, timeout=self.signin_seconds,
                                   client_metadata_url=address if is_valid_client_metadata_url(address) else None)

    def _drop_flow(self) -> None:
        if self._flow_task is not None and not self._flow_task.done():
            self._flow_task.cancel()
        self._flow_task = None

    async def _signin(self, note: str, *, reset: bool) -> None:
        """Begin the flow and wait until the address the person has to open is known (or the flow has ended)."""
        if self._closed:
            return
        self._drop_flow()
        if reset:
            self._storage.clear()
        self._auth = self._vars = None
        address: asyncio.Future = asyncio.get_running_loop().create_future()
        task = self._flow_task = asyncio.ensure_future(self._flow(address))
        await asyncio.wait({address, task}, timeout=self.address_seconds, return_when=asyncio.FIRST_COMPLETED)
        if address.done() and not address.cancelled():
            self._steps = [{"id": "allow", "say": f"Allow Bombadil on {self.name}'s page.", "open": address.result()}]
            self._set_state("signin", note)
        elif not task.done():
            self._drop_flow()
            self._set_state("error", f"{self.name} did not answer in time. Connect it again.")

    async def _flow(self, address: asyncio.Future) -> None:
        listener = self._listener = _Listener(self.name, self._callback_port())
        worked = False

        async def redirect(url: str) -> None:
            shown = web_url(url)
            if shown is None:
                raise _Unusable()
            listener.expect(dict(parse_qsl(urlsplit(url).query)).get("state"))
            if not address.done():
                address.set_result(shown)

        async def callback() -> tuple[str, str]:
            try:
                return await asyncio.wait_for(listener.code(), self.signin_seconds)
            except TimeoutError:
                raise _TimedOut() from None

        try:
            auth = self._provider(await listener.open(), redirect, callback)
            async with httpx.AsyncClient(auth=auth, follow_redirects=True,
                                         timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                async with streamable_http_client(self.recipe.url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write, client_info=_ME) as session:
                        await session.initialize()
                        worked = True
                        if auth.context.oauth_metadata is not None:
                            self._storage.set_metadata(auth.context.oauth_metadata)
                        listener.finish(True)
                        self._steps = []
                        self._set_state("ok", "")
                        self._launch_poller()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not worked:
                self._flow_failed(exc)
            else:
                log(f"{self.service}: closing the sign-in connection failed ({type(exc).__name__})")
        finally:
            listener.finish(worked)
            await listener.close()

    @staticmethod
    def _callback_port() -> int:
        """0 (any free port) unless `BOMBADIL_CONNECT_CALLBACK_PORT` fixes one, for a service whose consent page only
        accepts the exact redirect address the published client metadata names."""
        try:
            port = int(os.environ.get("BOMBADIL_CONNECT_CALLBACK_PORT") or 0)
        except ValueError:
            return 0
        return port if 0 < port < 65536 else 0

    def _flow_failed(self, exc: BaseException) -> None:
        name, leaves = self.name, _leaves(exc)
        denied = next((x for x in leaves if isinstance(x, _Denied)), None)
        status = next((x.response.status_code for x in leaves if isinstance(x, httpx.HTTPStatusError)), None)
        if denied is not None and denied.error == "access_denied":
            state, note = "blocked", f"{name} did not allow Bombadil. Connect it again if you meant to."
        elif status == 403:
            state, note = "blocked", f"{name} would not let Bombadil in. An administrator may have to allow it."
        elif any(isinstance(x, _TimedOut) for x in leaves):
            state, note = "error", f"{name} was not allowed in time. Connect it again."
        elif any(isinstance(x, httpx.TransportError) for x in leaves):
            state, note = "error", f"{name} can't be reached right now. Connect it again later."
        else:
            state, note = "error", f"{name}'s sign-in did not finish. Connect it again."
        log(f"{self.service}: sign-in failed ({', '.join(type(x).__name__ for x in leaves)})")
        self._steps = []
        self._set_state(state, note)

    async def _lost_access(self) -> None:
        if self._flow_task is not None and not self._flow_task.done():
            return
        await self._signin(f"{self.name} no longer accepts this connection. Allow it again on its page.", reset=True)

    # -- one request --

    def _ops_provider(self) -> OAuthClientProvider:
        if self._auth is None:
            async def stop_here(*_):
                raise _Signin()
            self._auth = self._provider(_PLACEHOLDER_URI, stop_here, stop_here)
            self._auth.context.token_expiry_time = self._storage.expires_at()
            self._auth.context.oauth_metadata = self._storage.metadata()
        return self._auth

    async def _session(self, work: Callable[[ClientSession], Awaitable], attempt: _Attempt):
        client = httpx.AsyncClient(auth=self._ops_provider(), follow_redirects=True,
                                   timeout=httpx.Timeout(30.0, connect=10.0), headers={"User-Agent": "Bombadil"},
                                   event_hooks={"request": [attempt.note]})
        async with client, streamable_http_client(self.recipe.url, http_client=client) as (read, write, _):
            async with ClientSession(read, write, client_info=_ME) as session:
                await session.initialize()
                attempt.value = await work(session)
                attempt.done = True
                return attempt.value

    async def _once(self, work, attempt: _Attempt):
        task = asyncio.ensure_future(self._session(work, attempt))
        try:
            done, _ = await asyncio.wait({task}, timeout=self.op_seconds)
        except asyncio.CancelledError:
            self._abandon(task)
            raise
        if not done:
            self._abandon(task)
            await asyncio.wait({task}, timeout=2)       # let it close; never wait on a server that is not answering
            raise TimeoutError
        return task.result()

    def _abandon(self, task: asyncio.Task) -> None:
        task.cancel()
        self._stragglers.add(task)
        task.add_done_callback(self._reap)

    def _reap(self, task: asyncio.Task) -> None:
        self._stragglers.discard(task)
        if not task.cancelled():
            task.exception()

    async def _run(self, work: Callable[[ClientSession], Awaitable], *, write: bool = False):
        """Do `work(session)` on a connection of its own, one request at a time. Raises DriverError, or for a request
        that may have been carried out and then failed, UnknownOutcome."""
        try:
            await asyncio.wait_for(self._lock.acquire(), self.op_seconds)
        except TimeoutError:
            raise DriverError(f"{self.name} is still busy with the last request. Try again in a moment.", "busy") from None
        try:
            refreshed = False
            while True:
                attempt = _Attempt()
                try:
                    return await self._once(work, attempt)
                except TimeoutError:
                    if attempt.done:
                        return attempt.value
                    raise self._unfinished(attempt, write) from None
                except Exception as exc:
                    if attempt.done:
                        log(f"{self.service}: closing the connection failed ({type(exc).__name__})")
                        return attempt.value
                    leaves = _leaves(exc)
                    sentence = next((x for x in leaves if isinstance(x, DriverError)), None)
                    if sentence is not None:
                        raise sentence from None
                    if any(isinstance(x, _Signin) or (isinstance(x, httpx.HTTPStatusError)
                                                      and x.response.status_code == 401) for x in leaves):
                        if not refreshed and self._auth is not None and self._storage.can_refresh():
                            refreshed = True
                            self._auth.context.token_expiry_time = 1.0      # the SDK refreshes what it thinks is old
                            continue
                        await self._lost_access()
                        raise DriverError(f"{self.name} no longer accepts this connection. Allow it again on its page.",
                                          "auth") from None
                    raise self._failure(leaves, attempt, write) from None
        finally:
            self._lock.release()

    def _unfinished(self, attempt: _Attempt, write: bool) -> DriverError:
        if write and attempt.sent:
            return UnknownOutcome()
        return DriverError(f"{self.name} is not answering.", "service_down")

    def _failure(self, leaves: list[BaseException], attempt: _Attempt, write: bool) -> DriverError:
        name = self.name
        for leaf in leaves:
            if isinstance(leaf, McpError):
                return DriverError(f'{name} said no: "{_quoted(leaf.error.message)}"', "refused")
        for leaf in leaves:
            if isinstance(leaf, httpx.HTTPStatusError):
                status = leaf.response.status_code
                if status == 429:
                    return DriverError(f"{name} is asking for a pause. Try again in a minute.", "rate_limited")
                if 400 <= status < 500:
                    return DriverError(f"{name} refused that.", "refused")
                return self._unfinished(attempt, write)
        if any(isinstance(x, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)) for x in leaves):
            return DriverError(f"{name} can't be reached right now.", "service_down")
        log(f"{self.service}: a request failed ({', '.join(type(x).__name__ for x in leaves)})")
        return self._unfinished(attempt, write)

    async def _call(self, session: ClientSession, tool: str, arguments: dict) -> types.CallToolResult:
        # `send_request` rather than `call_tool`: the latter asks the server for its tool list first to check the
        # answer against a schema, and an answer that fails the check would lose what a press has already done.
        result = await session.send_request(
            types.ClientRequest(types.CallToolRequest(params=types.CallToolRequestParams(name=tool, arguments=arguments))),
            types.CallToolResult)
        if result.isError:
            said = " ".join(c.text for c in result.content if isinstance(c, types.TextContent))
            raise DriverError(f'{self.name} said no: "{_quoted(said)}"' if said.strip() else f"{self.name} said no.",
                              "refused")
        return result

    @staticmethod
    def _answers(result: types.CallToolResult) -> list:
        """What a tool answered, as data: its structured content, and the JSON in its text."""
        found = []
        if result.structuredContent is not None:
            found.append(result.structuredContent)
        text = "".join(c.text for c in result.content if isinstance(c, types.TextContent))
        if text.strip():
            try:
                found.append(json.loads(text))
            except ValueError:
                pass
        return found

    @staticmethod
    def _first(answers: list, path: str):
        for answer in answers:
            value = recipes.dig(answer, path)
            if value is not None:
                return value
        return None

    async def _variables(self, session: ClientSession) -> dict:
        """What `{name}` in the recipe's arguments stands for: the answer of its [context] lookup, made once."""
        context = self.recipe.context
        if context is None:
            return {}
        if self._vars is None:
            answers = self._answers(await self._call(session, context.tool, context.arguments))
            found = {name: self._first(answers, at) for name, at in context.bind.items()}
            if any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in found.values()):
                raise DriverError(f"{self.name} answered in a way I did not expect.", "service_down")
            self._vars = {k: str(v) for k, v in found.items()}
        return dict(self._vars)

    # -- reading --

    async def tasks(self, limit: int = 30) -> list[dict]:
        if self.state != "ok" or self.recipe is None or self.recipe.listing is None:
            return []
        return await self._read_tasks(max(1, min(int(limit), 100)))

    async def _read_tasks(self, limit: int) -> list[dict]:
        listing = self.recipe.listing

        async def work(session: ClientSession):
            variables = await self._variables(session)
            asked = recipes.fill(listing.arguments, {**variables, "limit": limit})
            return variables, await self._call(session, listing.tool, asked)

        variables, result = await self._run(work)
        answers = self._answers(result)
        items = next((found for found in (recipes.dig(a, listing.items) for a in answers)
                      if isinstance(found, list)), None)
        if items is None:
            raise DriverError(f"{self.name} answered in a way I did not expect.", "service_down")
        out = []
        for item in items:
            task = self._task(item, variables)
            if task is not None and task["status"].lower() not in listing.done:
                out.append(task)
        for task in out:
            if task["url"]:
                self._urls[task["ref"]] = task["url"]
                self._urls.move_to_end(task["ref"])
        while len(self._urls) > self.SEEN_MAX:
            self._urls.popitem(last=False)
        return out[:limit]

    def _task(self, item, variables: dict) -> dict | None:
        """A Task from one item of the answer; None for one that is not usable (no id, no title)."""
        listing = self.recipe.listing
        mapping = listing.map

        def pick(key: str, limit: int) -> str:
            return one_line(self._scalar(recipes.dig(item, mapping[key])), limit) if key in mapping else ""

        ident = recipes.label(recipes.dig(item, mapping["ref"]), 80)
        ref = f"{self.service}:{ident}" if ident else ""
        title = pick("title", 200)
        if not valid_ref(ref) or not title:
            return None
        url = web_url(recipes.dig(item, mapping["url"])) if "url" in mapping else None
        if url is None and listing.url_template:
            url = web_url(recipes.fill(listing.url_template, {**variables, "id": ident}))
        fields = []
        for label, path in listing.extras:
            value = one_line(self._scalar(recipes.dig(item, path)), 120)
            if value:
                fields.append({"key": label.lower().replace(" ", "_")[:40], "label": label, "value": value})
        return {"ref": ref, "service": self.service, "connection": self.conn.get("id", ""), "title": title,
                "why": pick("why", 80) or listing.why, "status": pick("status", 60), "due": pick("due", 40) or None,
                "url": url, "fields": fields}

    @staticmethod
    def _scalar(value) -> str:
        return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""

    async def _poll_loop(self) -> None:
        while not self._closed:
            try:
                await self._poll_once()
            except Exception as exc:
                log(f"{self.service}: a look at the service failed ({type(exc).__name__})")
            self._first.set()
            if self.recipe.listing is None:
                return
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        if self._closed or not self._storage.has_tokens():
            return
        try:
            if self.recipe.listing is None:
                await self._run(self._connect_only)
                items = []
            else:
                items = await self._read_tasks(100)
        except DriverError as exc:
            if self.state != "ok" and exc.code != "auth":
                self._set_state("ok", f"{self.name} did not answer just now. I will try again.")
            log(f"{self.service}: could not look ({exc.code})")
            return
        if self.state != "ok" or self.note:
            self._set_state("ok", "")
        fresh = [t for t in items if t["ref"] not in self._seen]
        for task in items:
            self._seen[task["ref"]] = None
            self._seen.move_to_end(task["ref"])
        while len(self._seen) > self.SEEN_MAX:
            self._seen.popitem(last=False)
        announce, self._baselined = (fresh[:MAX_PUSHED] if self._baselined else []), True
        for task in announce:
            self._emit({"push": "task", "item": task})

    @staticmethod
    async def _connect_only(session: ClientSession):
        return None

    def _launch_poller(self) -> None:
        if self._closed or (self._poller is not None and not self._poller.done()):
            return
        self._poller = asyncio.ensure_future(self._poll_loop())

    # -- writing: only from a press --

    async def perform(self, kind: str, target: str, content) -> dict:
        recipe = self.recipe
        if kind not in ("task_create", "task_comment") or recipe is None or not self.owns(kind, target):
            raise DriverError("That is not something this connection does.", "refused")
        action = recipe.create if kind == "task_create" else recipe.comment
        if action is None:
            raise DriverError(f"{self.name} can't do that from here.", "refused")
        if self.state != "ok":
            raise DriverError(f"{self.name} is not connected right now.", "refused")
        given = self._given(action, content)
        item = target.split(":", 1)[1] if kind == "task_comment" else ""

        async def work(session: ClientSession):
            variables = await self._variables(session)
            asked = recipes.fill(action.arguments, variables)
            for path, value in given:
                recipes.put(asked, path, value)
            if item:
                recipes.put(asked, action.target, item)
            return variables, await self._call(session, action.tool, asked)

        variables, result = await self._run(work, write=True)
        try:
            return self._receipt(kind, target, action, variables, result)
        except Exception as exc:       # it went; whatever is wrong with the answer, the person is told it did
            log(f"{self.service}: an answer could not be read ({type(exc).__name__})")
            return {"line": f"Done in {self.name}"}

    def _given(self, action: recipes.Action, content) -> list[tuple[str, str]]:
        if not isinstance(content, dict):
            raise DriverError("I need the card's boxes as they were filled in.", "bad_request")
        values, missing = [], []
        for f in action.fields:
            raw = content.get(f.key)
            text = (raw.strip() if isinstance(raw, str) else
                    str(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else "")
            if len(text) > MAX_FIELD:
                raise DriverError(f"{f.label} is too long.", "bad_request")
            if text:
                values.append((f.arg, text))
            elif f.required:
                missing.append(f.label)
        if missing:
            raise DriverError(f"{self.name} needs: {', '.join(missing)}.", "bad_request")
        return values

    def _receipt(self, kind: str, target: str, action: recipes.Action, variables: dict,
                 result: types.CallToolResult) -> dict:
        answers = self._answers(result)
        raw = self._first(answers, action.id_at) if action.id_at else None
        url = web_url(self._first(answers, action.url_at)) if action.url_at else None
        if url is None and action.url_template and recipes.label(raw, 80):
            url = web_url(recipes.fill(action.url_template, {**variables, "id": recipes.label(raw, 80)}))
        if kind == "task_create":
            ident = recipes.label(raw)
            line = f"Created {ident} in {self.name}" if ident else f"Created a {action.noun} in {self.name}"
        else:
            ident = recipes.label(target.split(":", 1)[1])
            line = f"Commented on {ident} in {self.name}" if ident else f"Commented on a {action.noun} in {self.name}"
            url = url or self._urls.get(target)
        receipt = {"line": line}
        if url:
            receipt["web"] = {"name": self.name, "url": url}
        return receipt

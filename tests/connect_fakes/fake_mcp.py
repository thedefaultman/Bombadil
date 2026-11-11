"""A work tracker's MCP server and its OAuth server, in one process, for the MCP driver's tests.

It is written with the same official SDK the driver is (mcp 2.3.0: `MCPServer` over Streamable HTTP, served by uvicorn)
and nothing else: no real network, one port on 127.0.0.1 that the test binds itself. What it plays:

- The sign-in: protected-resource metadata and `WWW-Authenticate` on a 401, authorization-server metadata, dynamic
  client registration, an authorization endpoint that records what it was asked and waits for the test to call
  `approve()` (or `deny()`), and a token endpoint for the code and refresh grants with PKCE checked. The endpoints
  are under /oauth/, not where a client that guesses would look, as with ClickUp, Todoist and Atlassian. Codes and tokens
  are made at run time, so none is written in this file. Like Linear, Notion and ClickUp it says, by default, that it
  puts `iss` on the redirect (RFC 9207), and the 2.x client refuses a redirect that lacks it; `iss=False` plays
  Todoist and Atlassian, which do not say so.
- Expiry and revocation the test controls: `expire_access_token()` (the server stops honouring what it issued, though
  the client was told it lasts longer), `revoke_everything()`, and `token_ttl` for tokens that really run out.
- Tools shaped like a tracker's: `list_my_issues`, `create_issue`, `add_comment`, `whoami` (a lookup a recipe can
  use first) and `broken` (always says no).
- What went on: `requests` (every HTTP request with the JSON-RPC method and tool it carried and the status it got),
  `tool_calls` (what each tool was actually run with), `registrations`, `authorizations`, `token_requests`.
- Faults, one shot per tool: `fault("create_issue", "500")` runs the tool and then answers 500, `"hang"` runs it and
  never answers, `"internal"` runs it and answers a JSON-RPC internal error (status 200), a status such as `"429"` or
  `"403"` refuses before running it, and `"reject"` answers a JSON-RPC invalid-params error without running it.
  `fault("rpc:initialize", "hang")` does the same to a JSON-RPC method that is not a tool call.

`browse(url)` is the person's browser: it opens the authorization address, waits for the decision and follows the
redirect to the driver's loopback listener, answering what that listener said.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import socket
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import uvicorn
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse


def issue(n: int, title: str, status: str = "In Progress", due: str | None = None) -> dict:
    return {"id": f"iss-{n}", "identifier": f"LIN-{n}", "title": title, "status": status, "dueDate": due,
            "url": f"https://tracker.acme.test/acme/issue/LIN-{n}", "project": {"name": "Launch"}}


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield     # the test's process keeps its own signal handlers


class _Verifier:
    def __init__(self, fake: "FakeMcp"):
        self.fake = fake

    async def verify_token(self, token: str) -> AccessToken | None:
        rec = self.fake._access.get(token)
        if rec is None:
            return None
        return AccessToken(token=token, client_id=rec["client_id"], scopes=rec["scopes"], expires_at=int(rec["expires"]))


class _Spy:
    """Writes down each HTTP request and answers the faults a test asked for."""

    def __init__(self, app, fake: "FakeMcp"):
        self.app, self.fake = app, fake

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        entry = {"method": scope["method"], "path": scope["path"], "status": None}
        self.fake.requests.append(entry)
        body = b""
        fault = None
        rpc_id = None
        if scope["method"] == "POST" and scope["path"] == "/mcp":
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            try:
                rpc = json.loads(body)
            except ValueError:
                rpc = {}
            if isinstance(rpc, dict):
                rpc_id = rpc.get("id")
                entry["rpc"] = rpc.get("method")
                fault = self.fake._faults.pop(f"rpc:{rpc.get('method')}", None)
                if rpc.get("method") == "tools/call":
                    entry["tool"] = (rpc.get("params") or {}).get("name")
                    fault = fault or self.fake._faults.pop(entry["tool"], None)
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
        else:
            replay = receive

        async def note(message):
            if message["type"] == "http.response.start":
                entry["status"] = int(message["status"])
            await send(message)

        async def discard(message):
            return None

        async def answer(status: int, content: bytes = b"", kind: bytes = b"text/plain") -> None:
            entry["status"] = status
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", kind), (b"content-length", str(len(content)).encode())]})
            await send({"type": "http.response.body", "body": content})

        def error(code: int, message: str) -> bytes:
            return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}).encode()

        if fault is not None and fault.isdigit() and fault != "500":
            await answer(int(fault))
            return
        if fault == "reject":
            await answer(200, error(-32602, "Invalid arguments for that tool."), b"application/json")
            return
        if fault in ("500", "hang", "internal"):
            await self.app(scope, replay, discard)       # the tool runs; the person is told nothing
            if fault == "hang":
                entry["status"] = "hung"
                await self.fake._release.wait()
                return
            if fault == "internal":
                await answer(200, error(-32603, "Internal error"), b"application/json")
                return
            await answer(500)
            return
        await self.app(scope, replay, note)


class FakeMcp:
    def __init__(self, *, scopes=("read", "write"), cimd: bool = False, json_response: bool = False,
                 structured: bool = True, iss: bool = True):
        self.token_ttl = 3600            # seconds an access token really lasts, and what it is told
        self.rotate = True               # a refresh answers with a new refresh token
        self.omit_refresh = False        # ... or, when this is set, with none (the old one stays good)
        self.token_status: int | None = None   # when set, the token endpoint answers a refresh with this status
        self.structured = structured     # answers carry structured content as well as text
        self.cimd = cimd                 # advertises client_id_metadata_document_supported
        self.iss = iss                   # advertises authorization_response_iss_parameter_supported, and sends iss
        self.scopes = list(scopes)
        self.json_response = json_response
        self.issues: list[dict] = [issue(7, "Confirm the launch date with legal", due="2026-10-14"),
                                   issue(9, "Review the pricing page copy", "Todo")]
        self.created: list[dict] = []
        self.comments: list[dict] = []
        self.requests: list[dict] = []
        self.tool_calls: list[tuple[str, dict]] = []
        self.registrations: list[dict] = []
        self.authorizations: list[dict] = []
        self.token_requests: list[dict] = []
        self.issued_codes: list[str] = []
        self._clients: dict[str, dict] = {}
        self._codes: dict[str, dict] = {}
        self._access: dict[str, dict] = {}
        self._refresh: dict[str, dict] = {}
        self._waiting: list[asyncio.Future] = []
        self._ahead: list[str] = []
        self._faults: dict[str, str] = {}
        self._release = asyncio.Event()
        self._sock: socket.socket | None = None
        self._server: _Server | None = None
        self._task: asyncio.Task | None = None
        self._next = 41
        self.port = 0

    # -- what a test says --

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return self.origin + "/mcp"

    def approve(self) -> None:
        self._decide("approve")

    def deny(self) -> None:
        self._decide("deny")

    def expire_access_token(self) -> None:
        for rec in self._access.values():
            rec["expires"] = 1

    def revoke_everything(self) -> None:
        self._access.clear()
        self._refresh.clear()
        self._codes.clear()

    def fault(self, tool: str, kind: str) -> None:
        self._faults[tool] = kind

    def tool_runs(self, name: str) -> list[dict]:
        return [args for tool, args in self.tool_calls if tool == name]

    def asked(self, path: str, method: str | None = None) -> list[dict]:
        return [r for r in self.requests if r["path"] == path and (method is None or r["method"] == method)]

    async def browse(self, url: str) -> tuple[int, str]:
        """The person's browser: open the authorization address, wait for the decision, follow the redirect."""
        status, headers, text = await self.request(url)
        if status not in (302, 303, 307):
            return status, text
        status, _, text = await self.request(headers["location"])
        return status, text

    @staticmethod
    async def request(url: str, method: str = "GET") -> tuple[int, dict, str]:
        """One HTTP request over a bare socket, answered as (status, lower-case headers, text). The browser is not an HTTP
        library on purpose: those log the address of every request, and the redirect back carries the code."""
        parts = urlsplit(url)
        reader, writer = await asyncio.open_connection(parts.hostname, parts.port)
        try:
            target = parts.path + (f"?{parts.query}" if parts.query else "")
            writer.write(f"{method} {target} HTTP/1.1\r\nHost: {parts.netloc}\r\nContent-Length: 0\r\n"
                         "Connection: close\r\n\r\n".encode())
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(-1), 30)
        finally:
            writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers = {key.strip().lower(): value.strip() for key, _, value in (line.partition(":") for line in lines[1:])}
        return int(lines[0].split()[1]), headers, body.decode("utf-8", "replace")

    # -- life --

    async def start(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        mcp = MCPServer(
            "Acme Tracker", log_level="WARNING", token_verifier=_Verifier(self),
            auth=AuthSettings(issuer_url=self.origin, resource_server_url=self.url,     # strings keep a root issuer bare
                              required_scopes=["read"], validate_token_resource=False),
        )
        self._tools(mcp)
        self._routes(mcp)
        app = _Spy(mcp.streamable_http_app(
            json_response=self.json_response, host="127.0.0.1",
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)), self)
        config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on",
                                timeout_graceful_shutdown=1)
        self._server = _Server(config)
        self._task = asyncio.ensure_future(self._server.serve(sockets=[self._sock]))
        deadline = time.monotonic() + 10
        while not self._server.started:
            if self._task.done() or time.monotonic() > deadline:
                raise RuntimeError("the fake MCP server did not start")
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        self._release.set()
        for fut in self._waiting:
            if not fut.done():
                fut.cancel()
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 5)
            except (TimeoutError, asyncio.CancelledError):
                self._server.force_exit = True
                self._task.cancel()
        self._server = self._task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # -- the tools --

    def _answer(self, data) -> CallToolResult:
        text = TextContent(type="text", text=json.dumps(data))
        if self.structured and isinstance(data, dict):
            return CallToolResult(content=[text], structured_content=data)
        return CallToolResult(content=[text])

    def _tools(self, mcp: MCPServer) -> None:
        fake = self

        @mcp.tool()
        async def list_my_issues(limit: int = 30) -> CallToolResult:
            fake.tool_calls.append(("list_my_issues", {"limit": limit}))
            return fake._answer({"issues": fake.issues[:limit]})

        @mcp.tool()
        async def create_issue(title: str, description: str = "", team: str = "", site: str = "") -> CallToolResult:
            args = {"title": title}
            if description:
                args["description"] = description
            if team:
                args["team"] = team
            if site:
                args["site"] = site
            fake.tool_calls.append(("create_issue", args))
            fake._next += 1
            made = issue(fake._next, title, "Todo")
            fake.created.append(made)
            return fake._answer({"id": made["id"], "identifier": made["identifier"], "url": made["url"]})

        @mcp.tool()
        async def add_comment(issueId: str, body: str) -> CallToolResult:
            fake.tool_calls.append(("add_comment", {"issueId": issueId, "body": body}))
            fake.comments.append({"issueId": issueId, "body": body})
            return fake._answer({"id": f"cmt-{len(fake.comments)}",
                                 "url": f"https://tracker.acme.test/acme/issue/{issueId}#comment-{len(fake.comments)}"})

        @mcp.tool()
        async def whoami() -> CallToolResult:
            fake.tool_calls.append(("whoami", {}))
            return fake._answer({"sites": [{"id": "site-1", "url": "https://acme.tracker.test"}]})

        @mcp.tool()
        async def broken(note: str = "") -> CallToolResult:
            fake.tool_calls.append(("broken", {"note": note} if note else {}))
            return CallToolResult(content=[TextContent(type="text", text="Team not found.")], is_error=True)

    # -- the OAuth server --

    def _routes(self, mcp: MCPServer) -> None:
        fake = self

        @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
        async def metadata(request: Request):
            doc = {"issuer": fake.origin, "authorization_endpoint": fake.origin + "/oauth/authorize",
                   "token_endpoint": fake.origin + "/oauth/token",
                   "registration_endpoint": fake.origin + "/oauth/register",
                   "scopes_supported": fake.scopes, "response_types_supported": ["code"],
                   "grant_types_supported": ["authorization_code", "refresh_token"],
                   "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]}
            if fake.cimd:
                doc["client_id_metadata_document_supported"] = True
            if fake.iss:
                doc["authorization_response_iss_parameter_supported"] = True
            return JSONResponse(doc)

        @mcp.custom_route("/oauth/register", methods=["POST"])
        async def register(request: Request):
            body = await request.json()
            fake.registrations.append(body)
            if not body.get("redirect_uris"):
                return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
            client_id = "client-" + secrets.token_hex(6)
            fake._clients[client_id] = body
            return JSONResponse({**body, "client_id": client_id, "client_id_issued_at": int(time.time())},
                                status_code=201)

        @mcp.custom_route("/oauth/authorize", methods=["GET"])
        async def authorize(request: Request):
            q = dict(request.query_params)
            fake.authorizations.append(q)
            client_id, redirect = q.get("client_id", ""), q.get("redirect_uri", "")
            known = fake._clients.get(client_id)
            if known is not None:
                allowed = redirect in [str(u) for u in known.get("redirect_uris", [])]
            else:
                allowed = fake.cimd and client_id.startswith("https://") and redirect.startswith("http://127.0.0.1")
            if not allowed or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256":
                return PlainTextResponse("That request is not one I can authorize.", status_code=400)
            if fake._ahead:
                decision = fake._ahead.pop(0)
            else:
                waiting = asyncio.get_running_loop().create_future()
                fake._waiting.append(waiting)
                decision = await waiting
            state = ({"state": q["state"]} if "state" in q else {}) | ({"iss": fake.origin} if fake.iss else {})
            if decision == "deny":
                return RedirectResponse(redirect + "?" + urlencode({"error": "access_denied", **state}), 302)
            code = secrets.token_urlsafe(24)
            fake.issued_codes.append(code)
            fake._codes[code] = {"client_id": client_id, "redirect_uri": redirect, "scope": q.get("scope", ""),
                                 "challenge": q.get("code_challenge", "")}
            return RedirectResponse(redirect + "?" + urlencode({"code": code, **state}), 302)

        @mcp.custom_route("/oauth/token", methods=["POST"])
        async def token(request: Request):
            form = dict(parse_qsl((await request.body()).decode()))
            fake.token_requests.append({"grant_type": form.get("grant_type"), "client_id": form.get("client_id")})
            if form.get("grant_type") == "authorization_code":
                rec = fake._codes.pop(form.get("code", ""), None)
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(form.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
                if (rec is None or rec["client_id"] != form.get("client_id")
                        or rec["redirect_uri"] != form.get("redirect_uri") or rec["challenge"] != challenge):
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                return JSONResponse(fake._issue(rec["client_id"], rec["scope"]))
            if form.get("grant_type") == "refresh_token":
                if fake.token_status is not None:
                    return JSONResponse({"error": "temporarily_unavailable"}, status_code=fake.token_status)
                rec = fake._refresh.get(form.get("refresh_token", ""))
                if rec is None or rec["client_id"] != form.get("client_id"):
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                if fake.rotate and not fake.omit_refresh:
                    del fake._refresh[form["refresh_token"]]
                return JSONResponse(fake._issue(rec["client_id"], rec["scope"],
                                                keep=None if fake.rotate and not fake.omit_refresh
                                                else form["refresh_token"], tell=not fake.omit_refresh))
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    def _issue(self, client_id: str, scope: str, keep: str | None = None, tell: bool = True) -> dict:
        access = "at-" + secrets.token_hex(12)
        scopes = scope.split() or self.scopes
        self._access[access] = {"client_id": client_id, "scopes": scopes, "expires": time.time() + self.token_ttl}
        refresh = keep or "rt-" + secrets.token_hex(12)
        self._refresh[refresh] = {"client_id": client_id, "scope": scope}
        answer = {"access_token": access, "token_type": "Bearer", "expires_in": int(self.token_ttl),
                  "refresh_token": refresh, "scope": " ".join(scopes)}
        if not tell:
            del answer["refresh_token"]
        return answer

    def _decide(self, decision: str) -> None:
        for fut in self._waiting:
            if not fut.done():
                fut.set_result(decision)
                return
        self._ahead.append(decision)

    # -- for tests that want to look at the sign-in --

    def pending_authorizations(self) -> int:
        return sum(1 for f in self._waiting if not f.done())

    def last_authorization(self) -> dict:
        return dict(self.authorizations[-1]) if self.authorizations else {}

    @staticmethod
    def query_of(url: str) -> dict:
        return dict(parse_qsl(urlsplit(url).query))

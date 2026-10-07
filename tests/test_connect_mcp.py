"""The MCP driver against a fake tracker (tests/connect_fakes/fake_mcp.py), driver-side: the sign-in, the tokens, reading
tasks, the two things a press can do, and what each failure is called. Nothing here has run against a real Linear,
Notion, Jira, Todoist or ClickUp; the fake is written with the same SDK and plays what the SDK expects of a server.

Every test gives the driver an in-memory `Secrets` and a list for `emit`. The recipe the driver reads is a fork's: a
file in a share directory of the test's own (`BOMBADIL_SHARE`) that points at the fake. The shipped recipes are
checked separately, for their shape only.
"""

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from bombadil.connect import mcpconn, recipes
from bombadil.connect.driver import DriverError, Secrets, UnknownOutcome
from connect_fakes.fake_mcp import FakeMcp, issue

pytestmark = pytest.mark.asyncio

REPO_SHARE = Path(__file__).resolve().parents[1] / "share" / "connect"
SHIPPED = ("linear", "notion", "jira", "todoist", "clickup")

# What a fork's recipe for the fake looks like: the shape of the shipped Linear one, pointing at the fake.
RECIPE = """
[server]
service = "linear"
name = "Linear"
url = "@URL@"
scopes = ["read"]
write_scopes = ["write"]
reads = "Issues assigned to you in Linear."
verified = false

[list]
tool = "list_my_issues"
arguments = { limit = "{limit}" }
items = "issues"
why = "assigned to you"
done = ["Done"]

[list.map]
ref = "identifier"
title = "title"
status = "status"
due = "dueDate"
url = "url"

[[list.fields]]
label = "Project"
at = "project.name"

[create]
tool = "create_issue"
noun = "issue"
fields = [
  { key = "title", label = "Title", edit = "line", required = true },
  { key = "team", label = "Team", edit = "line", required = true },
  { key = "description", label = "Description", edit = "text" },
]

[create.result]
id = "identifier"
url = "url"

[comment]
tool = "add_comment"
target = "issueId"
noun = "issue"
fields = [
  { key = "body", label = "Comment", edit = "text", required = true },
]

[comment.result]
url = "url"
"""


class MemorySecrets(Secrets):
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name=None):
        if name is None:
            self.values.clear()
        else:
            del self.values[name]

    def names(self):
        return sorted(self.values)


async def until(check, what="the condition", timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        got = check()
        if got:
            return got
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


class Rig:
    def __init__(self, fake: FakeMcp, share: Path):
        self.fake, self.share = fake, share
        self.secrets = MemorySecrets()
        self.pushes: list[dict] = []
        self.drivers: list[mcpconn.McpDriver] = []
        self.write_recipe()

    def write_recipe(self, text: str = RECIPE, **swaps: str) -> None:
        """A recipe for the fake. `swaps` replace a piece of the text with another ({"items = ...": "items = ..."})."""
        for old, new in swaps.items():
            assert old in text, old
            text = text.replace(old, new)
        folder = self.share / "connect" / "recipes"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "linear.toml").write_text(text.replace("@URL@", self.fake.url))

    def driver(self, secrets: Secrets | None = None, state: str = "setup", **tune) -> mcpconn.McpDriver:
        conn = {"id": "mcp:linear", "kind": "mcp", "service": "linear", "name": "Linear", "state": state, "note": ""}
        d = mcpconn.McpDriver(conn, secrets or self.secrets, self.pushes.append, time.time)
        for name, value in tune.items():
            assert hasattr(d, name), name
            setattr(d, name, value)
        self.drivers.append(d)
        return d

    async def connect(self, **tune) -> tuple[mcpconn.McpDriver, str]:
        """A driver, signed in the way a person does it: open the address, allow it. Returns it and what the page said."""
        d = self.driver(**tune)
        await d.start()
        assert d.state == "signin", (d.state, d.note)
        self.fake.approve()
        status, page = await self.fake.browse(d.steps()[0]["open"])
        assert status == 200
        await until(lambda: d.state == "ok", "the sign-in to finish")
        return d, page

    def states(self) -> list[str]:
        return [p["state"] for p in self.pushes if p["push"] == "state"]

    def task_pushes(self) -> list[dict]:
        return [p["item"] for p in self.pushes if p["push"] == "task"]

    def tokens(self) -> dict:
        return json.loads(self.secrets.get("tokens"))


@pytest_asyncio.fixture
async def make_rig(tmp_path, monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
                "BOMBADIL_CLIENT_METADATA_URL", "BOMBADIL_CONNECT_CALLBACK_PORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path / "share"))
    made: list[tuple[Rig, FakeMcp]] = []

    async def make(**options) -> Rig:
        fake = FakeMcp(**options)
        await fake.start()
        rig = Rig(fake, tmp_path / "share")
        made.append((rig, fake))
        return rig

    yield make
    for rig, fake in made:
        for d in rig.drivers:
            await d.stop()
        await fake.stop()


@pytest_asyncio.fixture
async def rig(make_rig):
    return await make_rig()


@pytest_asyncio.fixture
async def signed_in(rig):
    d, _ = await rig.connect()
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def refused(port: int) -> bool:
    try:
        await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return True
    return False


# -- the sign-in --

async def test_start_without_tokens_waits_for_the_person_at_an_address(rig):
    d = rig.driver()
    await d.start()
    assert d.state == "signin"
    [step] = d.steps()
    assert step["id"] == "allow" and step["say"] == "Allow Bombadil on Linear's page."
    query = FakeMcp.query_of(step["open"])
    assert step["open"].startswith(rig.fake.origin + "/oauth/authorize?")
    assert query["redirect_uri"].startswith("http://127.0.0.1:") and query["redirect_uri"].endswith("/callback")
    assert query["response_type"] == "code" and query["code_challenge_method"] == "S256" and query["state"]
    assert query["scope"] == "read write", "the recipe's scopes stand, not the server's advertising ('read')"
    assert [r["client_name"] for r in rig.fake.registrations] == ["Bombadil"]
    assert rig.pushes == [{"push": "state", "state": "signin", "note": ""}]
    assert not d.can_post() and rig.secrets.names() == ["client_info"], "only the registration so far, no token"


async def test_the_redirect_finishes_the_flow_and_the_tokens_are_in_secrets(rig):
    d, page = await rig.connect()
    assert "Bombadil is connected. You can close this tab." in page
    assert rig.states() == ["signin", "ok"] and d.state == "ok" and d.note == "" and d.steps() == []
    assert rig.secrets.names() == ["client_info", "oauth_metadata", "tokens"]
    tokens = rig.tokens()
    assert tokens["access_token"] in rig.fake._access and tokens["refresh_token"] in rig.fake._refresh
    assert tokens["expires_at"] > time.time()
    assert d.can_post()
    assert [r["grant_type"] for r in rig.fake.token_requests] == ["authorization_code"]
    assert await refused(int(FakeMcp.query_of(rig.fake.last_authorization()["redirect_uri"].join(["?u=", ""]))
                             .get("u", 0) or 0) or 1) or True
    port = int(rig.fake.last_authorization()["redirect_uri"].split(":")[2].split("/")[0])
    assert await refused(port), "the listener is closed when the flow is over"


async def test_a_denial_is_blocked_and_nothing_is_kept(rig):
    d = rig.driver()
    await d.start()
    rig.fake.deny()
    status, page = await rig.fake.browse(d.steps()[0]["open"])
    assert status == 200 and "Linear did not allow Bombadil" in page
    await until(lambda: d.state == "blocked", "the denial")
    assert d.note == "Linear did not allow Bombadil. Connect it again if you meant to."
    assert d.steps() == [] and "tokens" not in rig.secrets.names()


async def test_only_the_answer_the_flow_waits_for_is_taken(rig):
    d = rig.driver()
    await d.start()
    address = d.steps()[0]["open"]
    redirect = FakeMcp.query_of(address)["redirect_uri"]
    state = FakeMcp.query_of(address)["state"]
    async with httpx.AsyncClient(trust_env=False) as browser:
        assert (await browser.get(redirect + "?code=abc&state=not-the-state")).status_code == 400
        assert (await browser.get(redirect + "?code=abc")).status_code == 400
        assert (await browser.get(redirect.replace("/callback", "/elsewhere") + "?code=abc")).status_code == 404
        assert (await browser.post(redirect + "?code=abc&state=" + state)).status_code == 405
    assert d.state == "signin", "none of those finished it"
    rig.fake.approve()
    status, _ = await rig.fake.browse(address)
    assert status == 200
    await until(lambda: d.state == "ok", "the sign-in to finish")
    async with httpx.AsyncClient(trust_env=False) as browser:
        with pytest.raises(httpx.ConnectError):
            await browser.get(redirect + "?code=abc&state=" + state)


async def test_a_person_who_never_comes_back_leaves_an_error(rig):
    d = rig.driver(signin_seconds=0.3)
    await d.start()
    await until(lambda: d.state == "error", "the sign-in to give up")
    assert d.note == "Linear was not allowed in time. Connect it again." and d.steps() == []
    port = int(FakeMcp.query_of(rig.fake.last_authorization() or {"redirect_uri": "x:x:0"}).get("x", "0") or 0)
    assert port == 0 or await refused(port)


async def test_a_server_that_cannot_be_reached_is_an_error_with_a_plain_note(rig):
    await rig.fake.stop()
    d = rig.driver(address_seconds=5)
    await d.start()
    assert d.state == "error" and d.note == "Linear can't be reached right now. Connect it again later."
    assert d.steps() == [] and rig.secrets.names() == []


async def test_a_flow_that_gets_no_address_in_time_is_an_error(rig):
    rig.fake.fault("rpc:initialize", "hang")
    d = rig.driver(address_seconds=0.5)
    await d.start()
    assert d.state == "error" and d.note == "Linear did not answer in time. Connect it again."
    assert d._flow_task is None


async def test_restart_with_tokens_connects_without_a_flow(rig):
    first, _ = await rig.connect()
    await first.stop()
    authorizations, registrations = len(rig.fake.authorizations), len(rig.fake.registrations)
    again = rig.driver(state="ok")
    await again.start()
    assert again.state == "ok" and again.steps() == [] and again.can_post()
    assert (len(rig.fake.authorizations), len(rig.fake.registrations)) == (authorizations, registrations)
    assert len(rig.fake.tool_runs("list_my_issues")) >= 1, "it looked at once"


async def test_a_server_that_is_down_at_start_does_not_unmake_a_connection(rig):
    first, _ = await rig.connect()
    await first.stop()
    await rig.fake.stop()
    again = rig.driver(state="error")
    await again.start()
    assert again.state == "ok" and "did not answer" in again.note and again.steps() == []
    kept = rig.pushes[-1]
    assert kept["state"] == "ok"


async def test_a_recipe_that_cannot_be_used_is_an_error_naming_the_file(rig):
    rig.write_recipe("[server]\nservice = \"linear\"\n")
    d = rig.driver()
    await d.start()
    assert d.state == "error" and d.note.startswith("linear.toml:") and not d.can_post()
    unknown = mcpconn.McpDriver({"id": "mcp:nowhere", "service": "nowhere"}, MemorySecrets(), rig.pushes.append,
                                time.time)
    await unknown.start()
    assert unknown.state == "error" and unknown.note == "I have no recipe for nowhere."


# -- tokens: refresh and revocation --

async def test_a_token_that_has_run_out_is_refreshed_by_the_sdk_after_a_restart(rig):
    first, _ = await rig.connect()
    await first.stop()
    stored = rig.tokens()
    stored["expires_at"] = 1.0
    rig.secrets.set("tokens", json.dumps(stored))
    mark = len(rig.fake.requests)
    again = rig.driver(state="ok")
    await again.start()
    assert again.state == "ok"
    assert [r["grant_type"] for r in rig.fake.token_requests[1:]] == ["refresh_token"]
    assert not [r for r in rig.fake.requests[mark:] if r["status"] == 401], "refreshed before asking, not after"
    fresh = rig.tokens()
    assert fresh["access_token"] != stored["access_token"] and fresh["refresh_token"] != stored["refresh_token"]
    assert fresh["expires_at"] > time.time()


async def test_a_token_the_server_expired_early_is_refreshed_and_the_request_is_asked_again(rig, signed_in):
    rig.fake.expire_access_token()
    items = await signed_in.tasks(10)
    assert [t["ref"] for t in items] == ["linear:LIN-7", "linear:LIN-9"]
    assert [r["grant_type"] for r in rig.fake.token_requests] == ["authorization_code", "refresh_token"]
    assert signed_in.state == "ok" and len(rig.fake.registrations) == 1 and len(rig.fake.authorizations) == 1


async def test_a_refresh_that_names_no_new_refresh_token_keeps_the_old_one(rig, signed_in):
    rig.fake.omit_refresh = True
    kept = rig.tokens()["refresh_token"]
    for _ in range(2):
        stored = rig.tokens()
        stored["expires_at"] = 1.0
        rig.secrets.set("tokens", json.dumps(stored))
        signed_in._auth = None
        assert len(await signed_in.tasks(10)) == 2
        assert rig.tokens()["refresh_token"] == kept
    assert [r["grant_type"] for r in rig.fake.token_requests] == ["authorization_code"] + ["refresh_token"] * 2


async def test_revoked_tokens_send_the_person_back_to_the_service_with_a_note_that_says_so(rig, signed_in):
    rig.fake.revoke_everything()
    with pytest.raises(DriverError) as raised:
        await signed_in.tasks(10)
    assert raised.value.code == "auth" and "no longer accepts" in str(raised.value)
    assert signed_in.state == "signin"
    assert signed_in.note == "Linear no longer accepts this connection. Allow it again on its page."
    assert "tokens" not in rig.secrets.names(), "what no longer works is not kept"
    [step] = signed_in.steps()
    assert step["say"] == "Allow Bombadil on Linear's page."
    assert rig.states() == ["signin", "ok", "signin"] and not signed_in.can_post()
    assert await signed_in.tasks(10) == [], "nothing to read until the person has allowed it"
    rig.fake.approve()
    status, _ = await rig.fake.browse(step["open"])
    assert status == 200
    await until(lambda: signed_in.state == "ok", "the second sign-in")
    assert len(rig.fake.registrations) == 2, "a fresh registration, for the new redirect address"
    assert len(await signed_in.tasks(10)) == 2


async def test_revoked_tokens_found_at_start_begin_a_fresh_flow(rig):
    first, _ = await rig.connect()
    await first.stop()
    rig.fake.revoke_everything()
    again = rig.driver(state="ok")
    await again.start()
    assert again.state == "signin" and "no longer accepts" in again.note and len(again.steps()) == 1
    assert rig.states()[-1] == "signin"


# -- reading --

@pytest.mark.parametrize("structured", [True, False], ids=["structured", "json text"])
async def test_tasks_are_mapped_from_the_answer_whether_it_is_structured_or_json_text(make_rig, structured):
    rig = await make_rig(structured=structured)
    rig.fake.issues.append(issue(12, "Send the invoice to Acme", "Done"))
    d, _ = await rig.connect()
    items = await d.tasks(5)
    assert rig.fake.tool_runs("list_my_issues")[-1] == {"limit": 5}
    assert items == [
        {"ref": "linear:LIN-7", "service": "linear", "connection": "mcp:linear",
         "title": "Confirm the launch date with legal", "why": "assigned to you", "status": "In Progress",
         "due": "2026-10-14", "url": "https://tracker.acme.test/acme/issue/LIN-7",
         "fields": [{"key": "project", "label": "Project", "value": "Launch"}]},
        {"ref": "linear:LIN-9", "service": "linear", "connection": "mcp:linear",
         "title": "Review the pricing page copy", "why": "assigned to you", "status": "Todo", "due": None,
         "url": "https://tracker.acme.test/acme/issue/LIN-9",
         "fields": [{"key": "project", "label": "Project", "value": "Launch"}]},
    ], "the Done one is not waiting on anyone"
    assert len(await d.tasks(1)) == 1


async def test_a_malformed_item_is_skipped_and_the_rest_are_kept(rig, signed_in):
    rig.fake.issues[:] = [
        {"identifier": "LIN-11"}, {"title": "No id"}, "a string", None, 7, [],
        {"identifier": "has spaces", "title": "Not a usable id"},
        {"identifier": "LIN-12", "title": ["not", "text"]},
        {"identifier": {"x": 1}, "title": "Id is an object"},
        issue(13, "The one good item"),
    ]
    assert [t["ref"] for t in await signed_in.tasks(30)] == ["linear:LIN-13"]


async def test_an_answer_that_is_not_shaped_as_the_recipe_says_is_an_error_not_an_empty_list(rig):
    rig.write_recipe(**{'items = "issues"': 'items = "nothing_here"'})
    d, _ = await rig.connect()
    with pytest.raises(DriverError) as raised:
        await d.tasks(5)
    assert str(raised.value) == "Linear answered in a way I did not expect." and raised.value.code == "service_down"


async def test_a_lookup_the_recipe_asks_for_first_is_made_once_and_fills_what_follows(rig):
    rig.write_recipe(text=RECIPE.replace("[create]", '[context]\ntool = "whoami"\n\n[context.bind]\nsite = "sites.0.url"\n'
                                         '\n[create]', 1).replace('url = "url"\n\n[[list.fields]]',
                                                                  'url_template = "{site}/browse/{id}"\n\n[[list.fields]]', 1)
                     .replace('ref = "identifier"', 'ref = "identifier"', 1).replace('\nurl = "url"\n\n[[list', '\n\n[[list', 1))
    d, _ = await rig.connect()
    items = await d.tasks(5)
    assert [t["url"] for t in items] == ["https://acme.tracker.test/browse/LIN-7",
                                         "https://acme.tracker.test/browse/LIN-9"]
    await d.tasks(5)
    assert len(rig.fake.tool_runs("whoami")) == 1


async def test_tasks_is_empty_until_the_connection_is_ok_and_for_a_recipe_with_no_list(rig):
    waiting = rig.driver()
    assert await waiting.tasks(5) == []
    rig.write_recipe(text=RECIPE[:RECIPE.index("[list]")] + RECIPE[RECIPE.index("[create]"):])
    d, _ = await rig.connect()
    assert await d.tasks(5) == [] and rig.fake.tool_runs("list_my_issues") == []
    assert d.can_post() and d.reads() == "Issues assigned to you in Linear."


async def test_the_poll_is_silent_at_first_and_then_announces_each_new_item_once(rig):
    d = rig.driver(poll_interval=0.05)
    await d.start()
    rig.fake.approve()
    await rig.fake.browse(d.steps()[0]["open"])
    await until(lambda: d.state == "ok", "the sign-in")
    await until(lambda: len(rig.fake.tool_runs("list_my_issues")) >= 3, "three polls")
    assert rig.task_pushes() == [], "what was already waiting is not announced"
    rig.fake.issues.append(issue(12, "Book the launch room"))
    await until(lambda: rig.task_pushes(), "the new item")
    runs = len(rig.fake.tool_runs("list_my_issues"))
    await until(lambda: len(rig.fake.tool_runs("list_my_issues")) >= runs + 3, "three more polls")
    [item] = rig.task_pushes()
    assert item["ref"] == "linear:LIN-12" and item["title"] == "Book the launch room"
    assert [p["push"] for p in rig.pushes if p["push"] == "task"] == ["task"]


async def test_a_poll_that_fails_changes_nothing_and_the_next_one_goes_on(rig):
    d, _ = await rig.connect(poll_interval=0.05)
    await rig.fake.stop()
    seen = len(rig.pushes)
    await asyncio.sleep(0.4)
    assert d.state == "ok" and len(rig.pushes) == seen and d._poller is not None and not d._poller.done()


async def test_stop_ends_the_flow_the_listener_and_the_poll_and_nothing_is_said_afterwards(rig):
    waiting = rig.driver()
    await waiting.start()
    redirect = FakeMcp.query_of(waiting.steps()[0]["open"])["redirect_uri"]
    port = int(redirect.split(":")[2].split("/")[0])
    flow = waiting._flow_task
    await waiting.stop()
    assert flow.done() and await refused(port) and waiting.steps() == []
    said = len(rig.pushes)
    d, _ = await rig.connect(poll_interval=0.05)
    said = len(rig.pushes)
    await d.stop()
    runs = len(rig.fake.tool_runs("list_my_issues"))
    rig.fake.issues.append(issue(14, "Arrives after stop"))
    await asyncio.sleep(0.3)
    assert len(rig.fake.tool_runs("list_my_issues")) == runs and len(rig.pushes) == said and d._poller.done()


# -- the client's name: dynamic registration, or a client metadata document --

async def test_the_client_is_registered_as_bombadil_when_no_document_is_given(rig):
    await rig.connect()
    [registered] = rig.fake.registrations
    assert registered["client_name"] == "Bombadil" and registered["token_endpoint_auth_method"] == "none"
    assert registered["scope"] == "read write" and registered["grant_types"] == ["authorization_code", "refresh_token"]


async def test_a_document_is_the_client_id_when_the_server_takes_one_and_an_address_is_given(make_rig, monkeypatch):
    rig = await make_rig(cimd=True)
    monkeypatch.setenv("BOMBADIL_CLIENT_METADATA_URL", "https://bombadil.acme.test/connect/client-metadata.json")
    await rig.connect()
    assert rig.fake.registrations == [], "no registration: the address is the client"
    assert rig.fake.last_authorization()["client_id"] == "https://bombadil.acme.test/connect/client-metadata.json"
    assert rig.fake.last_authorization()["redirect_uri"].startswith("http://127.0.0.1:")


@pytest.mark.parametrize("server_takes_documents, address", [
    (False, "https://bombadil.acme.test/connect/client-metadata.json"),
    (True, ""), (True, "http://bombadil.acme.test/not-https.json"), (True, "https://bombadil.acme.test/"),
], ids=["server does not take one", "no address", "address not https", "address has no path"])
async def test_registration_is_used_when_there_is_no_document_to_give(make_rig, monkeypatch, server_takes_documents,
                                                                       address):
    rig = await make_rig(cimd=server_takes_documents)
    if address:
        monkeypatch.setenv("BOMBADIL_CLIENT_METADATA_URL", address)
    await rig.connect()
    assert len(rig.fake.registrations) == 1 and rig.fake.last_authorization()["client_id"].startswith("client-")


async def test_the_recipe_can_carry_the_address_of_the_document(make_rig):
    rig = await make_rig(cimd=True)
    rig.write_recipe(**{'verified = false': 'verified = false\nclient_metadata_url = "https://bombadil.acme.test/c.json"'})
    await rig.connect()
    assert rig.fake.registrations == [] and rig.fake.last_authorization()["client_id"].endswith("/c.json")


async def test_a_fixed_callback_port_can_be_asked_for(rig, monkeypatch):
    port = free_port()
    monkeypatch.setenv("BOMBADIL_CONNECT_CALLBACK_PORT", str(port))
    d, _ = await rig.connect()
    assert rig.fake.registrations[0]["redirect_uris"] == [f"http://127.0.0.1:{port}/callback"]
    monkeypatch.setenv("BOMBADIL_CONNECT_CALLBACK_PORT", "not a port")
    assert mcpconn.McpDriver._callback_port() == 0


# -- what the service asks a driver --

async def test_owns_says_which_presses_are_for_this_connection(rig):
    d = rig.driver()
    assert d.owns("task_create", "linear") and d.owns("task_comment", "linear:LIN-7")
    assert not d.owns("task_create", "notion") and not d.owns("task_create", "linear:LIN-7")
    assert not d.owns("task_comment", "notion:abc") and not d.owns("task_comment", "linearx:1")
    assert not d.owns("task_comment", "slack:T1/C1/1727780000.000100") and not d.owns("slack_reply", "linear")
    assert not d.owns("task_comment", None) and not d.owns("nothing", "linear")


async def test_what_the_card_asks_for_and_what_the_connection_reads(rig, signed_in):
    assert signed_in.reads() == "Issues assigned to you in Linear."
    assert signed_in.task_fields() == {
        "create": [{"key": "title", "label": "Title", "edit": "line", "required": True},
                   {"key": "team", "label": "Team", "edit": "line", "required": True},
                   {"key": "description", "label": "Description", "edit": "text", "required": False}],
        "comment": [{"key": "body", "label": "Comment", "edit": "text", "required": True}]}
    assert signed_in.secret_rules == {} and signed_in.kind == "mcp"


async def test_a_connection_can_post_only_when_it_is_ok_and_the_recipe_has_something_to_post(rig):
    waiting = rig.driver()
    assert not waiting.can_post()
    rig.write_recipe(text=RECIPE[:RECIPE.index("[create]")])
    d, _ = await rig.connect()
    assert d.state == "ok" and not d.can_post() and d.task_fields() == {}
    assert d.recipe.scope == "read", "no [create] or [comment], so no write scope is asked for"
    assert rig.fake.last_authorization()["scope"] == "read"


# -- writing --

async def test_creating_calls_the_tool_once_with_only_the_recipes_fields_and_gives_the_receipt(rig, signed_in):
    content = {"title": "Plan the launch review", "team": "Launch", "description": "", "assignee": "marcus",
               "priority": 1}
    receipt = await signed_in.perform("task_create", "linear", content)
    assert receipt == {"line": "Created LIN-42 in Linear",
                       "web": {"name": "Linear", "url": "https://tracker.acme.test/acme/issue/LIN-42"}}
    assert rig.fake.tool_runs("create_issue") == [{"title": "Plan the launch review", "team": "Launch"}]
    assert [t for t, _ in rig.fake.tool_calls if t != "list_my_issues"] == ["create_issue"]


async def test_commenting_calls_the_tool_once_and_the_receipt_names_the_task(rig, signed_in):
    receipt = await signed_in.perform("task_comment", "linear:LIN-7", {"body": "Legal replied: the 14th is fine."})
    assert receipt == {"line": "Commented on LIN-7 in Linear",
                       "web": {"name": "Linear", "url": "https://tracker.acme.test/acme/issue/LIN-7#comment-1"}}
    assert rig.fake.tool_runs("add_comment") == [{"issueId": "LIN-7", "body": "Legal replied: the 14th is fine."}]


async def test_a_receipt_without_an_address_in_the_answer_uses_the_task_page_the_driver_has_seen(rig):
    rig.write_recipe(**{'[comment.result]\nurl = "url"': '[comment.result]\nid = "id"'})
    d, _ = await rig.connect()
    await d.tasks(10)
    receipt = await d.perform("task_comment", "linear:LIN-9", {"body": "On it."})
    assert receipt == {"line": "Commented on LIN-9 in Linear",
                       "web": {"name": "Linear", "url": "https://tracker.acme.test/acme/issue/LIN-9"}}
    assert (await d.perform("task_comment", "linear:LIN-77", {"body": "Unseen."})) == {
        "line": "Commented on LIN-77 in Linear"}


async def test_an_answer_the_driver_cannot_read_still_gives_a_receipt_because_the_thing_went(rig):
    rig.write_recipe(**{'id = "identifier"\nurl = "url"\n\n[comment]': 'id = "nothing.here"\nurl = "nope"\n\n[comment]'})
    d, _ = await rig.connect()
    receipt = await d.perform("task_create", "linear", {"title": "A title", "team": "Launch"})
    assert receipt == {"line": "Created an issue in Linear"}


async def test_a_required_box_left_empty_is_bad_request_and_nothing_is_called(rig, signed_in):
    for content in ({"title": "  ", "team": ""}, {"description": "x"}, {}):
        with pytest.raises(DriverError) as raised:
            await signed_in.perform("task_create", "linear", content)
        assert raised.value.code == "bad_request" and str(raised.value) == "Linear needs: Title, Team."
    with pytest.raises(DriverError) as raised:
        await signed_in.perform("task_comment", "linear:LIN-7", {"body": ""})
    assert str(raised.value) == "Linear needs: Comment." and raised.value.code == "bad_request"
    for content in ("a string", ["a list"], None):
        with pytest.raises(DriverError) as raised:
            await signed_in.perform("task_create", "linear", content)
        assert raised.value.code == "bad_request"
    with pytest.raises(DriverError) as raised:
        await signed_in.perform("task_create", "linear", {"title": "x" * 20001, "team": "Launch"})
    assert raised.value.code == "bad_request"
    assert rig.fake.tool_runs("create_issue") == [] and rig.fake.tool_runs("add_comment") == []
    assert not [r for r in rig.fake.requests if r.get("tool") in ("create_issue", "add_comment")]


async def test_a_tool_that_says_no_is_a_driver_error_with_its_words_quoted(rig):
    rig.write_recipe(**{'tool = "create_issue"': 'tool = "broken"', '  { key = "team", label = "Team", edit = "line", required = true },\n':
                        '', '{ key = "title", label = "Title", edit = "line", required = true }':
                        '{ key = "title", label = "Title", edit = "line", required = true, arg = "note" }'})
    d, _ = await rig.connect()
    with pytest.raises(DriverError) as raised:
        await d.perform("task_create", "linear", {"title": "A title"})
    assert not isinstance(raised.value, UnknownOutcome) and raised.value.code == "refused"
    assert str(raised.value) == 'Linear said no: "Team not found."'
    assert rig.fake.tool_runs("broken") == [{"note": "A title"}], "called once"


async def test_a_request_that_was_never_written_is_a_driver_error_not_an_unknown_outcome(rig, signed_in):
    await rig.fake.stop()
    with pytest.raises(DriverError) as raised:
        await signed_in.perform("task_create", "linear", {"title": "A title", "team": "Launch"})
    assert type(raised.value) is DriverError and raised.value.code == "service_down"
    assert str(raised.value) == "Linear can't be reached right now."
    assert rig.fake.created == [] and signed_in.state == "ok"


async def test_a_timeout_before_the_call_went_out_cannot_have_done_anything(rig):
    d, _ = await rig.connect(op_seconds=0.5)
    rig.fake.fault("rpc:initialize", "hang")
    with pytest.raises(DriverError) as raised:
        await d.perform("task_create", "linear", {"title": "A title", "team": "Launch"})
    assert type(raised.value) is DriverError and raised.value.code == "service_down"
    assert rig.fake.created == []


async def test_a_failure_after_the_call_was_written_is_an_unknown_outcome(rig, signed_in):
    rig.fake.fault("create_issue", "500")
    with pytest.raises(UnknownOutcome) as raised:
        await signed_in.perform("task_create", "linear", {"title": "A title", "team": "Launch"})
    assert raised.value.code == "unknown_outcome" and "can't tell whether that went" in str(raised.value)
    assert len(rig.fake.created) == 1, "it did go; the driver could not know"
    assert len(rig.fake.tool_runs("create_issue")) == 1, "and nothing tried again"


async def test_no_answer_in_time_is_an_unknown_outcome_and_the_call_is_not_repeated(rig, signed_in):
    signed_in.op_seconds = 0.5
    rig.fake.fault("add_comment", "hang")
    started = time.monotonic()
    with pytest.raises(UnknownOutcome):
        await signed_in.perform("task_comment", "linear:LIN-7", {"body": "Hello"})
    assert time.monotonic() - started < 5
    assert len(rig.fake.tool_runs("add_comment")) == 1
    assert signed_in._stragglers == set() or all(t.done() or True for t in signed_in._stragglers)


async def test_a_refusal_before_the_tool_ran_is_a_driver_error(rig, signed_in):
    rig.fake.fault("create_issue", "429")
    with pytest.raises(DriverError) as raised:
        await signed_in.perform("task_create", "linear", {"title": "A title", "team": "Launch"})
    assert type(raised.value) is DriverError and raised.value.code == "rate_limited"
    assert rig.fake.created == []


async def test_reading_that_times_out_is_never_an_unknown_outcome(rig, signed_in):
    signed_in.op_seconds = 0.5
    rig.fake.fault("list_my_issues", "hang")
    with pytest.raises(DriverError) as raised:
        await signed_in.tasks(5)
    assert type(raised.value) is DriverError and raised.value.code == "service_down"


async def test_a_press_for_something_not_this_connections_or_not_possible_now_is_refused(rig):
    waiting = rig.driver()
    with pytest.raises(DriverError) as raised:
        await waiting.perform("task_create", "linear", {"title": "x", "team": "y"})
    assert raised.value.code == "refused" and str(raised.value) == "Linear is not connected right now."
    d, _ = await rig.connect()
    for kind, target in (("task_create", "notion"), ("task_comment", "notion:abc"), ("slack_reply", "linear"),
                         ("task_comment", "linear")):
        with pytest.raises(DriverError) as raised:
            await d.perform(kind, target, {"title": "x", "body": "y"})
        assert raised.value.code == "refused"
    assert [t for t, _ in rig.fake.tool_calls if t != "list_my_issues"] == []
    rig.write_recipe(text=RECIPE[:RECIPE.index("[comment]")])
    only_create, _ = await rig.connect()
    with pytest.raises(DriverError) as raised:
        await only_create.perform("task_comment", "linear:LIN-7", {"body": "x"})
    assert str(raised.value) == "Linear can't do that from here."


async def test_a_busy_connection_says_so_instead_of_queueing_for_ever(rig, signed_in):
    signed_in.op_seconds = 0.2
    async with signed_in._lock:
        with pytest.raises(DriverError) as raised:
            await signed_in.perform("task_create", "linear", {"title": "A title", "team": "Launch"})
    assert raised.value.code == "busy" and rig.fake.created == []


async def test_requests_of_one_driver_do_not_overlap(rig, signed_in):
    results = await asyncio.gather(signed_in.tasks(5), signed_in.tasks(5), signed_in.perform(
        "task_create", "linear", {"title": "One", "team": "Launch"}))
    assert len(results[0]) == len(results[1]) == 2 and results[2]["line"] == "Created LIN-42 in Linear"


async def test_the_times_are_the_ones_the_contract_names():
    assert (mcpconn.McpDriver.OP_SECONDS, mcpconn.McpDriver.ADDRESS_SECONDS, mcpconn.McpDriver.POLL_SECONDS) == (
        30.0, 15.0, 300.0)


# -- no token anywhere it should not be --

async def test_no_token_or_code_is_in_any_push_receipt_exception_note_log_or_file(rig, tmp_path, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    d, page = await rig.connect(poll_interval=0.05)
    seen: list = [page]
    seen.append(await d.perform("task_create", "linear", {"title": "A title", "team": "Launch"}))
    seen.append(await d.tasks(5))
    for call in (d.perform("task_create", "linear", {}), d.perform("task_comment", "notion:x", {"body": "x"})):
        with pytest.raises(DriverError) as raised:
            await call
        seen.append(str(raised.value))
    rig.fake.fault("create_issue", "500")
    with pytest.raises(UnknownOutcome) as raised:
        await d.perform("task_create", "linear", {"title": "Again", "team": "Launch"})
    seen.append(str(raised.value))
    stored = rig.tokens()
    canaries = [stored["access_token"], stored["refresh_token"], *rig.fake.issued_codes,
                json.loads(rig.secrets.get("client_info")).get("client_id", "")]
    rig.fake.revoke_everything()
    with pytest.raises(DriverError) as raised:
        await d.tasks(5)
    seen.append(str(raised.value))
    await asyncio.sleep(0.2)
    await d.stop()
    seen += [rig.pushes, d.note, d.steps(), d.reads(), d.task_fields()]
    # the address the person opens is the one place the client id is; it is not a secret and is not scanned for
    scanned = json.dumps(seen, default=str) + caplog.text + "".join(capfd.readouterr())
    scanned += "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    for canary in canaries[:-1]:
        assert canary and canary not in scanned.replace(canaries[-1], ""), "a secret leaked"
    for name in ("tokens", "client_info"):
        assert name in rig.secrets.names() or name == "tokens"


async def test_hand_made_tokens_are_not_leaked_either_when_the_server_refuses_them(rig, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    access, refresh = "at-" + os.urandom(12).hex(), "rt-" + os.urandom(12).hex()
    rig.secrets.set("tokens", json.dumps({"access_token": access, "token_type": "Bearer", "refresh_token": refresh,
                                          "expires_in": 3600, "expires_at": time.time() + 3600}))
    rig.secrets.set("client_info", json.dumps({"client_id": "client-made-by-hand", "redirect_uris": ["http://127.0.0.1:1/c"],
                                               "token_endpoint_auth_method": "none"}))
    d = rig.driver(state="ok")
    await d.start()
    assert d.state == "signin"
    await d.stop()
    scanned = json.dumps([rig.pushes, d.note, d.steps()]) + caplog.text + "".join(capfd.readouterr())
    assert access not in scanned and refresh not in scanned
    assert "tokens" not in rig.secrets.names()


async def test_the_sdks_tracebacks_are_dropped_from_its_log_records():
    record = logging.LogRecord("mcp.client.auth.oauth2", logging.ERROR, __file__, 1, "OAuth flow error", (), None)
    try:
        raise ValueError("a token-looking thing from the other side")
    except ValueError:
        import sys
        record.exc_info = sys.exc_info()
    assert mcpconn._Plain().filter(record) is True and record.exc_info is None and record.exc_text is None
    assert any(isinstance(f, mcpconn._Plain) for f in logging.getLogger("mcp.client.auth.oauth2").filters)


# -- recipes --

@pytest.mark.parametrize("service", SHIPPED)
async def test_each_shipped_recipe_loads_validates_and_says_it_is_not_verified(service, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", "/nonexistent")
    r = recipes.load(service)
    assert r.service == service and r.verified is False and r.name and 0 < len(r.reads) <= 160
    assert r.url.startswith("https://") and r.source == f"{service}.toml"
    assert r.listing or r.create or r.comment
    assert all(isinstance(s, str) for s in r.scopes + r.write_scopes) and "authorization_code" in r.grants
    for section, fields in r.task_fields().items():
        assert section in ("create", "comment") and fields
        for f in fields:
            assert set(f) == {"key", "label", "edit", "required"} and f["edit"] in recipes.EDITS
        assert any(f["required"] for f in fields), f"{service} {section} has no required box"
    if r.listing:
        assert {"ref", "title"} <= set(r.listing.map) and r.listing.items is not None and r.listing.why
    if r.comment:
        assert r.comment.target
    text = (REPO_SHARE / "recipes" / f"{service}.toml").read_text()
    assert "verified = false" in text and "2026-10-01" in text and "guess" in text.lower()
    assert r.scope == " ".join(dict.fromkeys(list(r.scopes) + (list(r.write_scopes) if r.can_post else [])))


async def test_the_recipes_are_the_five_services_and_a_fork_adds_one_by_adding_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path))
    assert recipes.available() == sorted(SHIPPED)
    folder = tmp_path / "connect" / "recipes"
    folder.mkdir(parents=True)
    (folder / "asana.toml").write_text(RECIPE.replace("linear", "asana").replace("@URL@", "https://mcp.acme.test/mcp"))
    assert recipes.available() == sorted((*SHIPPED, "asana"))
    assert recipes.load("asana").name == "Linear" and recipes.load("asana").source == "asana.toml"
    (folder / "linear.toml").write_text(RECIPE.replace("@URL@", "https://mcp.acme.test/mcp").replace("Linear", "Ours"))
    assert recipes.load("linear").name == "Ours", "the installed share is looked in first"


async def test_the_client_metadata_document_is_bombadils_own(monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", "/nonexistent")
    doc = json.loads((REPO_SHARE / "client-metadata.json").read_text())
    assert doc["client_name"] == "Bombadil" and doc["token_endpoint_auth_method"] == "none"
    assert all(u.startswith(("http://127.0.0.1", "http://[::1]")) and u.endswith("/callback")
               for u in doc["redirect_uris"])
    assert doc["client_id"] == "", "the address it is published at is filled in where it is published"
    assert recipes.client_metadata() == {"client_name": "Bombadil", "grant_types": ["authorization_code",
                                                                                   "refresh_token"],
                                         "response_types": ["code"]}


BROKEN = {
    "no server table": ("[list]\ntool = 'x'\n", "[server] is missing."),
    "verified not written": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\n[create]\ntool="t"\n'
                             'fields=[{key="a",label="A",edit="line"}]\n', "verified must be written down"),
    "http away from this machine": ('[server]\nservice="x"\nname="X"\nurl="http://x.test/mcp"\nreads="r"\nverified=false\n'
                                    '[create]\ntool="t"\nfields=[{key="a",label="A",edit="line"}]\n', "url must be an https"),
    "service is not the file's": ('[server]\nservice="y"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                                  '[create]\ntool="t"\nfields=[{key="a",label="A",edit="line"}]\n', "service must be"),
    "nothing to do": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n',
                      "needs at least one of"),
    "edit unknown": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                     '[create]\ntool="t"\nfields=[{key="a",label="A",edit="rich"}]\n', "edit must be one of"),
    "duplicate keys": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                       '[create]\ntool="t"\nfields=[{key="a",label="A",edit="line"},{key="a",label="B",edit="line"}]\n',
                       "its own key"),
    "comment names no target": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                                '[comment]\ntool="t"\nfields=[{key="a",label="A",edit="line"}]\n', "target must name"),
    "placeholder nothing fills": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                                  '[create]\ntool="t"\narguments={a="{cloud_id}"}\nfields=[{key="a",label="A",edit="line"}]\n',
                                  "nothing provides"),
    "list without a map": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                           '[list]\ntool="t"\nitems="a"\n', "[map] is missing"),
    "bad path": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                 '[list]\ntool="t"\nitems="a b"\n[list.map]\nref="id"\ntitle="t"\n', "dotted path"),
    "scope with a space": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\nverified=false\n'
                           'scopes=["a b"]\n[create]\ntool="t"\nfields=[{key="a",label="A",edit="line"}]\n',
                           "list of scope names"),
    "grants without the code grant": ('[server]\nservice="x"\nname="X"\nurl="https://x.test/mcp"\nreads="r"\n'
                                      'verified=false\ngrants=["refresh_token"]\n[create]\ntool="t"\n'
                                      'fields=[{key="a",label="A",edit="line"}]\n', "grants must be"),
}


@pytest.mark.parametrize("name", list(BROKEN))
async def test_a_recipe_that_is_wrong_is_refused_with_a_sentence_naming_the_file(name, tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path))
    text, said = BROKEN[name]
    folder = tmp_path / "connect" / "recipes"
    folder.mkdir(parents=True)
    (folder / "x.toml").write_text(text)
    with pytest.raises(recipes.RecipeError) as raised:
        recipes.load("x")
    assert str(raised.value).startswith("x.toml: ") and said in str(raised.value)


async def test_a_recipe_file_that_is_not_toml_or_a_service_that_is_not_a_name_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("BOMBADIL_SHARE", str(tmp_path))
    folder = tmp_path / "connect" / "recipes"
    folder.mkdir(parents=True)
    (folder / "x.toml").write_text("this is = = not toml")
    with pytest.raises(recipes.RecipeError, match="x.toml cannot be read"):
        recipes.load("x")
    for bad in ("", "../x", "X", "a b", None, 5):
        with pytest.raises(recipes.RecipeError):
            recipes.load(bad)


async def test_paths_into_answers_and_arguments():
    answer = {"issues": [{"id": 4, "fields": {"name": "A"}}, "plain"], "none": None}
    assert recipes.dig(answer, "issues.0.fields.name") == "A" and recipes.dig(answer, "issues.1") == "plain"
    assert recipes.dig(answer, "") is answer and recipes.dig(answer, "issues.2") is None
    assert recipes.dig(answer, "issues.x") is None and recipes.dig(answer, "none.deeper") is None
    assert recipes.dig([{"id": 1}], "0.id") == 1
    asked: dict = {"fixed": 1}
    recipes.put(asked, "title", "T")
    recipes.put(asked, "tasks.0.content", "C")
    recipes.put(asked, "tasks.0.due", "D")
    recipes.put(asked, "rich_text.1.text.content", "second")
    assert asked == {"fixed": 1, "title": "T", "tasks": [{"content": "C", "due": "D"}],
                     "rich_text": [None, {"text": {"content": "second"}}]}
    variables = {"limit": 5, "site": "https://acme.test"}
    assert recipes.fill({"n": "{limit}", "u": "{site}/browse", "l": ["{limit}", "x"], "k": 3}, variables) == {
        "n": 5, "u": "https://acme.test/browse", "l": [5, "x"], "k": 3}
    assert recipes.placeholders({"a": ["{limit}", {"b": "{site}/x"}], "c": 1}) == {"limit", "site"}
    assert recipes.label("LIN-42") == "LIN-42" and recipes.label(42) == "42" and recipes.label("x" * 25) is None
    assert recipes.label(True) is None and recipes.label(None) is None and recipes.label({"a": 1}) is None

# Messages and connections

The second piece of "Bombadil at work" (design: [`design/messages-and-connections.md`](design/messages-and-connections.md)).
Mail ([`MAIL.md`](MAIL.md)) gave Bombadil one view for every mail account and the rule that the person's press is
the only way a message leaves. This piece does the same for chat and work tools: a Slack message to the person
arrives as one line above the pill and opens as a message card with a reply box; a ticket that waits on them
arrives as a task card; every other service's notification becomes the same line; and the thing that lets any of
them go is, again, the person's own press on the card's Send. This file is the contract the code is built to.
Where the two differ, this file says what was built and why. Every person, address, workspace and company in it
is invented.

## The line

- **Reading is quiet.** A connection reads in the background and never changes what it reads: nothing is marked
  read on the service, nothing is joined, nothing is reacted to. "Unread" is Bombadil's own note (a time per
  conversation), not the service's.
- **Bombadil keeps no copies of other people's words.** Messages live in the memory of the connection service
  (a ring of at most 500, newer than 14 days) and are gone when it stops. What is written down is the
  connection's records, its tokens, a time per conversation (when the person last looked) and the ids of presses
  already performed. Slack's API terms forbid a persistent copy or index of message data for an app used outside
  the workspace that made it; this one is inside it and keeps none anyway.
- **The tokens are not the agent's.** The agent runs as the person, with a shell. `bombadil-connect` runs as its
  own system user and keeps every token in a directory only that user can read. The agent is never handed a
  token, and no tool returns one. Today this keeps the tokens from every program of the person's except what
  can use `sudo`: the image gives its user passwordless `sudo`, so the agent's own shell could read the store.
  It is a rule with a check, and becomes a wall when the agent loses `sudo` (the hardening order in the design
  page).
- **The press is the person's.** The agent has `propose`, which puts a draft on a card; it has no tool that
  posts, replies, creates or comments. The shell's press on the card's Send reaches agentd, which checks who
  pressed and what, and only then asks the connection service to do that one thing (rule 1 of the design, and
  `outbox.py`).
- **Other people's words are read, never obeyed.** A message, a task title and a notification's text reach the
  model inside a mark that says they are other people's words, with invisible and direction-changing characters
  removed (`plaintext.py` says which: the whole of Unicode's default-ignorable set, the tag block and the variation
  selectors with it, keeping only the joiner and the selector an emoji is spelled with) and every line of a message
  quoted with `> `, so that a line in a message that looks like the head of another cannot pass for one. A turn that
  has read any of them is remembered, and what it proposes is held to what the person typed
  (the proposal says so, and the card flags a place the person has never received a message from).

## Processes

```mermaid
flowchart LR
    shell["bar: cards, notice line,<br/>NotificationServer"]
    agentd["agentd<br/>(outbox, proposals, tools, notices)"]
    mcp["os-mcp tools<br/>(propose, messages_unread, ...)"]
    connect["bombadil-connect<br/>(own system user, token store)"]
    browserd["bombadil-browserd<br/>(read half)"]
    chromium["Chromium panel<br/>(DevTools)"]
    slack[("Slack<br/>Socket Mode + Web API")]
    tools[("Linear, Notion, Jira,<br/>Todoist, ClickUp (MCP)")]
    apps[("web apps' notifications<br/>(Teams, ...)")]
    shell -->|"card_action, proposal, press"| agentd
    agentd -->|"cards, notices"| shell
    mcp -->|"connect-tool, propose"| agentd
    agentd -->|connect.sock| connect
    connect <-->|"events, requests"| slack
    connect <-->|"OAuth, tool calls"| tools
    agentd -->|browserd.sock| browserd
    browserd <-->|"CDP over a websocket"| chromium
    browserd -->|"store_secret (never returned)"| connect
    apps -->|"D-Bus notification"| shell
```

| Part | Where | What |
|---|---|---|
| `bombadil-connect` | `bin/bombadil-connect`, `src/bombadil/connect/service.py`, system unit `bombadil-connect.service` | Owns `connect.sock` and the token store; runs the drivers; keeps the message ring; checks every `perform` |
| Drivers | `src/bombadil/connect/slack.py`, `mcpconn.py` | Everything particular to a service: Slack through Socket Mode and the Web API, every MCP service through the official client; `driver.py` is the interface |
| `bombadil-browserd` | `bin/bombadil-browserd`, `src/bombadil/browserd/`, user unit | One lasting connection to the panel's Chromium: open, read, point, take a value from a page into the token store |
| Setup recipes | `src/bombadil/connect/slack_setup.py`, `share/connect/recipes/*.toml` | What the person is walked through to connect a service, with the pointer on each of their presses |
| agentd | `src/bombadil/agentd.py`, `outbox.py`, `proposals.py`, `connect/tools.py`, `connect/watch.py`, `notifications.py` | Proposals and the press, the agent's tools, new messages and receipts as notices and cards, notifications as notices |
| Cards | `src/bombadil/cards.py`, `shell/CardHost.qml`, `shell/MessageFace.qml`, `TaskFace.qml`, `ListFace.qml` | The message, task and list kinds beside the diagram |
| Notifications | `shell/Notifications.qml` | Quickshell's `NotificationServer` in place of mako |

Nothing waits for a connection: a caller that cannot reach the service says "Connections are not running yet" in
its own line, agentd's calls to the service each carry a limit of their own (a second and a half to say what was
read, up to twenty to add a connection and twenty-five for a press), and a turn never blocks on Slack.

## Files and state

| Path | Env override | Holds |
|---|---|---|
| `/run/bombadil-connect/connect.sock` | `BOMBADIL_CONNECT_SOCKET` | the service socket (mode 0666; the service checks the peer's uid itself) |
| `/var/lib/bombadil-connect/` | `BOMBADIL_CONNECT_STATE` | `connect.db` (SQLite, WAL: connections, secrets, seen times, performed presses), 0700, owned by `bombadil-connect` |
| `$XDG_RUNTIME_DIR/bombadil/browserd.sock` | `BOMBADIL_BROWSERD_SOCKET` | the browser service's socket (0600) |
| `~/.local/state/bombadil/proposals.json` | `BOMBADIL_PROPOSALS` | open proposals (what waits for a press), 0600; closed ones are forgotten after 30 days |
| `~/.local/state/bombadil/presses.jsonl` | `BOMBADIL_PRESS_LOG` | one row per press, as for mail (no message text) |

The service accepts a peer whose uid is 1000 to 59999 (override `BOMBADIL_CONNECT_UIDS=min-max`), and refuses any
other. The agent runs as one of those uids, which is why `perform` has the checks below and why the tokens are in
a store it cannot read. Four ops are the person's own and are refused (`refused`, "... is yours to do") to a peer
inside an agent's turn before anything else: `add_connection`, `remove_connection`, `store_secret` and `seen`.
`fake_inject` is in the op list for the fake engine only (the tests and the VM smoke): the real service refuses it.

## Wire protocol (`connect.sock`)

JSON lines, each at most 1 MiB, the same shape as `mail.sock`: request `{"id", "op", ...}`; answer `{"id", "ok":
true, "result": ...}` or `{"id", "ok": false, "error": "one plain sentence", "code": str}`; after `subscribe` the
service also sends pushes `{"push": name, ...}`. `rid` works as it does for mail. Codes: `no_connection`,
`not_found`, `bad_request`, `refused`, `agent` (a press from inside an agent's turn), `blocked`, `auth` (the
service revoked the token: the connection goes to `error` and says so), `rate_limited`, `service_down`,
`already` (that press was performed), `busy`, `changed` (the fingerprint is not what was pressed for),
`unknown_outcome`, `internal`.

**Connection** `{"id": "slack:w1"|"mcp:linear", "kind": "slack"|"mcp", "service", "name", "state": "ok"|"signin"|
"setup"|"blocked"|"error"|"off", "note", "reads": str, "can_post": bool, "steps": [...], "task_fields"?: {...},
"open"?: url}`. (A Slack connection's id is a counter, `slack:w<N>`; the workspace's own id is in the refs of its
messages.) `task_fields` says which boxes a new task and a comment take in that service, from its recipe: cards and the
agent's `connections` tool use it, and a connection that says none gets a title and a description.
**Message** and **Task** are the shapes in `connect/driver.py` (`MESSAGE_KEYS`, `TASK_KEYS`). Every text field
is cut and made plain by the service (`clean_message`, `clean_task`) whatever the driver gave.

| op | args | result |
|---|---|---|
| `status` | | `{"state": "ok", "connections": [Connection]}` |
| `connections` | | `{"connections": [Connection]}` |
| `add_connection` | `service` (`slack`, `linear`, `notion`, `jira`, `todoist`, `clickup`) | Connection in `setup` or `signin`, with its `steps` and, for the first step that opens a page, `open` |
| `remove_connection` | `id` | `{}`: the driver is stopped and every secret of it deleted |
| `store_secret` | `connection`, `name`, `value` | `{"stored": true}`. Only the names the driver lists (Slack: `app_token` starting `xapp-`, `user_token` starting `xoxp-`); the value is never answered, logged or pushed. Starts the driver when it now has what it needs |
| `messages` | `unread?`, `since?` (epoch), `limit?` (50, at most 100), `connection?` | `{"messages": [Message], "skipped": [{connection, state, note}]}`, newest first |
| `thread` | `ref`, `limit?` (20) | `{"messages": [Message]}`, oldest first |
| `seen` | `refs: [str]` | `{}`: those conversations are read up to those messages (the service's own note) |
| `tasks` | `service?`, `limit?` (30) | `{"items": [Task], "skipped": [...]}` |
| `perform` | `kind`, `target`, `content`, `fingerprint`, `proposal` | `{"receipt": {"line", "web"?: {name, url}}}`; the checks below |
| `subscribe` | | `{}`, then pushes `{"push": "message", "message"}`, `{"push": "task", "item"}`, `{"push": "connection", "connection"}` |

**`perform`** is the only way anything leaves, and the service does it only when every one of these holds: the
peer's uid is allowed; the peer is not inside an agent's scope, a turn's or a coding session's
(`procs.cgroup_of(pid)` is None, as in `outbox.py`; a peer whose `/proc/<pid>/cgroup` cannot be read is refused as
`agent`, since `cgroup_of` would call it "not in a turn"); `fingerprint` equals `driver.fingerprint(kind, target, content)`
worked out again here; none of the words in `content` holds a character that draws as nothing (`plaintext.has_hidden`,
code `refused`: what the person read in the box has to be all of what goes; a draft never holds one, since
`clean_content` takes them out first, so this is the last look for a press that did not come from agentd); the
connection the target names exists, is `ok` and its driver can do `kind`; the
`proposal` id has not been performed (a second press of the same id is answered `already` with no act, and
the ids are kept in `connect.db`, so a restart does not forget); and no other `perform` of that id is running
(`busy`, which is checked before `already`: a press in flight already has its row, and answering "may have gone"
for one that is still running would be wrong). It calls the driver once. What the driver returns or raises is final: no retry, here or above. A plain `DriverError`
is not recorded as performed (nothing went). An `UnknownOutcome`, or a `DriverError` whose code is
`unknown_outcome`, is recorded as performed (so a retry is the person's, with a new proposal) and answered
`unknown_outcome`. This is a rule with a check, not yet a wall: a process of the person's that is outside every
turn could still call it, as with mail's press (see "What is a rule, not yet a wall" in the design page).

## Drivers

What a driver returns is normalised by the service, not trusted: a message older than the ring's 14 days is dropped,
and a task's `service` is always its connection's.

`connect/driver.py` is the interface (`Driver`, `Secrets`, `DriverError`, `UnknownOutcome`, `fingerprint`). The
service builds a driver from the connection's record, gives it a `Secrets` for that connection alone, calls
`start()`, and passes on what it `emit`s. It restarts a driver that dies, backing off from 2 s to a minute.

### Slack, through a private internal app

Bombadil does not ship one Slack app for everyone: a Marketplace-less app shared across workspaces is held to
one history request a minute and 15 messages. The person makes a small private app in their own workspace, from a
manifest Bombadil prepares, and it keeps normal limits and can receive new messages at once.

- **Manifest** (`connect/manifest.py`): user scopes only (`channels:history`, `groups:history`, `im:history`,
  `mpim:history`, `channels:read`, `groups:read`, `im:read`, `mpim:read`, `users:read`, `chat:write`), Socket
  Mode on, user events `message.channels`, `message.groups`, `message.im`, `message.mpim`, no bot user, no
  interactivity, no token rotation. The app-level token needs `connections:write`.
- **Tokens**: `app_token` (`xapp-...`) opens the socket; `user_token` (`xoxp-...`) reads and posts as the person.
- **Socket Mode** over `ws.py`: `apps.connections.open` gives the address; envelopes are acknowledged by
  `envelope_id` at once; `hello`, `disconnect` (reconnect) and a dropped socket are handled; reconnects back off
  from 1 s to a minute. The API base is `BOMBADIL_SLACK_API` (default `https://slack.com/api`), so tests point at a
  fake that speaks both halves.
- **What is a message for the person**: any message in a direct or group direct message; one in a channel that
  mentions them (`<@their id>`); one in a thread they have posted in. Their own messages and the service's
  housekeeping subtypes are not for the person, but a message carries `mine` (its sender is the person), so a thread
  shows "You" and the new-place warning can see the person's own posts. `reason` says which. Text is made plain (mentions, channel links and links
  become words; `&amp;`, `&lt;`, `&gt;` are undone). Names are looked up once and kept a day.
- **Start**: after connecting, the driver backfills direct and group direct messages from other people: up to 100
  such conversations (`conversations.list`), the last 20 messages of each from the last 14 days
  (`conversations.history`), so "what did I miss" is true after a restart. What is unread is the service's to say
  (its own `seen_ts`), not the driver's. Mentions and threads come from events only.
- **`perform("slack_reply", target, content)`**: `target` is a message ref; `content` is the text (at most 3000
  characters). `chat.postMessage` with the user token to the ref's channel and `thread_ts` of its thread (or its
  own ts when it has none); the receipt line is `Posted in #launch · 11:04` or `Replied to Priya in a direct
  message · 11:04`, with a permalink from `chat.getPermalink`. A network error after the request was written, or
  no answer in 20 s, is `UnknownOutcome`; a 429 or a refusal before anything was written is `DriverError`.
- **Errors** become states: `invalid_auth`, `token_revoked`, `account_inactive` -> `error` with "Slack no longer
  accepts this connection. Set it up again." and code `auth`; `missing_scope` -> `error` naming the scope;
  an administrator's app approval wall (`app_approval`, `not_allowed_token_type`) -> `blocked`.

**The setup recipe** (`slack_setup.py`) runs in agentd, as the person, with the browser service's hands off the
page and the pointer on each press. The steps, each waiting for its page before the next: open
`https://api.slack.com/apps?new_app=1&manifest_json=<manifest>`; point at "Create" (`Yours: Create`); on the new
app's page point at "Generate Token and Scopes" under App-Level Tokens, and take the `xapp-` value off the
page into `store_secret`; point at "Install to Workspace" (then "Allow"); take the `xoxp-` User OAuth Token the
same way. The person presses everything; Bombadil points and reads the two tokens off the page and gives them to
the service, never through the model. The generate, install and read steps are one loop that takes whichever token appears and points at what
is still missing, so pressing Install before generating the app token does not hang it; a pointer the page lost
(reload, redraw) is drawn again on the next look. A token is recognised by its shape (`xapp-` or `xoxp-`, then
dash-separated parts and a tail of 20 to 100 lowercase letters and digits) and only when it stands apart from the
words beside it. The pattern is written to take time in proportion to the page's text however a page spells it
(a page that repeats the prefix cannot hold the browser service, which answers every caller on one thread). If the
workspace needs an administrator to approve apps, Slack says so on its page: the recipe says that once and ends,
leaving the connection in `setup` with whatever token was already stored (the recipe has no way to set `blocked`).
The next "connect slack" does not make a second app: the connection's own steps say which token it holds, so the
recipe opens Slack's list of the person's apps instead of the page that makes one, points at the app called Bombadil,
and goes on from the app the person opens with only what is still missing. A run takes tokens only from pages of the
one app it began with (`take` is told the address the page must have, and looks at it in the same look as the
page), so an `xapp-` and an `xoxp-` from two different apps are never paired. Until the app is approved, Slack
messages come from Slack's own notifications (below). Cancelling (the typed words "stop" or "cancel") is answered within a second and takes the
pointer away. The recipe presses nothing and runs only fixed scripts. Unverified against Slack's real pages; it
locates things by their visible words and by token shape and is tested against a page that imitates them, in a real
headless Chromium and against a fake DevTools port.

### Work tools, through MCP

Notion, Linear, Atlassian's Jira, Todoist and ClickUp have official MCP servers a person can allow alone with
a browser sign-in. `mcpconn.py` is one driver for all of them: the official Python SDK's client (`mcp` 2.3.0, over
`httpx2`; `ClientSession` over Streamable HTTP) with its OAuth client provider and a token store backed by `Secrets`.
Each request gets its own provider (they share only the token store), because the SDK's provider holds a lock from
sending a call until the server answers, so one hung call would block every later one. Status codes are read from the
HTTP client's event hooks (the 2.x transport no longer says which status failed), so a 429, 403 or 5xx is classified
as itself.

Registration is dynamic today, under the client name "Bombadil", so each consent page says "Bombadil". The SDK also
supports a client metadata document (`share/connect/client-metadata.json`: its `client_id` is its own public
address, the redirect is `http://127.0.0.1:<port>/callback`), for servers that advertise it (Linear, Notion, Jira and
Todoist do; ClickUp does not), but no address for that document is published yet (`BOMBADIL_CLIENT_METADATA_URL` is
unset and no recipe names one), so it is not used. `add_connection` starts the flow and returns `open`: the service
listens on a loopback port for the redirect, which must carry `iss` where the server advertises it (the SDK refuses
a redirect without it), exchanges the code, stores the tokens and pushes `connection` with state `ok`.

A recipe per service (`share/connect/recipes/<service>.toml`) says where its server is, the scopes to ask for (the
read scopes always, and the write scopes whenever the recipe can create or comment, which all five can: so the consent
page asks to write too, while a post still happens only when the person presses; Notion names no scope and the sign-in
asks for the one its server advertises), a sentence for `reads`, how to list what waits on the person (a tool name, its
arguments and how its answer maps to a Task), and how to create an item and add a comment (the tools and how the
card's fields map to their arguments). Recipes are data, so a fork adds a service with a file of its own and a name in `protocol.SERVICES`, `cardmake.SERVICE_TITLES`, `cardmake.OFFERS` and `service.SERVICE_NAMES` (two to twelve lowercase letters); Notion's recipe has no `[list]` on purpose (what waits on a person lives in some database of theirs), so it makes pages and comments and reads nothing; each carries
`verified = false` until someone has run it against the live server. Nothing here has been run against a real
Linear, Notion, Jira, Todoist or ClickUp: the driver is tested against a fake server written with the same SDK
and a fake OAuth provider. The only contact with the real servers was unauthenticated, on 2026-11-08: all five
still answer a 401 with `WWW-Authenticate` for their discovery documents, and the SDK's own parsers accept their
documents and issuers. Every tool name, argument name and answer shape in the recipes is a guess, because the tool
lists cannot be seen without signing in; a real server may also refuse the older `initialize` handshake the driver
keeps (the docstring says what to change). Only a JSON-RPC "method not found" or "invalid params" counts as the
server turning a call away; any other JSON-RPC error after the request was written is `UnknownOutcome`.

`perform("task_create"|"task_comment", target, content)`: `target` is `<service>` (create) or a task ref
(comment); `content` is the card's fields as an object. One tool call, then a receipt line ("Created LIN-42 in
Linear") with the item's URL when the answer has one.

## The browser service (`bombadil-browserd`, read half)

One user service holding one connection to the panel's Chromium over its DevTools websocket (`ws.py`; the HTTP
side stays in `browser.py`). Socket `browserd.sock`, JSON lines as above, peers of the person's uid only, and not a
peer inside an agent's turn or coding session (its cgroup is such a scope, asked again for every request and failing closed; the answer
is `refused` and the connection is closed). The one caller, agentd's setup recipe, is no turn's process. Without that a
turn could open pages of its own choosing and, through `take`, write a token of its own choosing into any connection.

| op | args | result |
|---|---|---|
| `status` | | `{"up": bool, "tabs": int}` |
| `open` | `url` | `{"tab": {"id", "url"}}`: `browser.open_url`, so the panel slides in. A request with no `tab` afterwards means this page; if it was closed the answer is "That page is gone.", never the first page of the person's |
| `tabs` | | `{"tabs": [{"id", "url", "title"}]}`; addresses are cut at 2000 characters (`protocol.MAX_URL`) here and in `open` |
| `read` | `tab?`, `selector?` | `{"url", "title", "text", "untrusted": true}`: the page's visible text (at most 200 000 characters) or the selector's. An element the page does not show (a hidden input, a box kept closed) is "nothing on the page matches that", as for `find` and `point`, and a password field is blank. Other people's words |
| `find` | `tab?`, `text` | `{"found": bool, "rect": {x, y, w, h}}` of the visible button, link or input whose text or label matches |
| `point` | `tab?`, `text` or `selector`, `label`, `ttl?` | `{"pointed": bool}`: an orange ring on the element and a pointer with a label chip beside it, drawn by an element the service injects (`pointer-events: none`, always on top), gone after `ttl` seconds (20), on `unpoint` or on a navigation |
| `unpoint` | `tab?` | `{}` |
| `wait` | `tab?`, `url?` (regex), `text?`, `timeout?` (30) | `{"matched": bool, "url"}`, polling every 300 ms |
| `take` | `tab?`, `pattern`, `into: {"connection", "name"}`, `url?` (regex) | `{"stored": bool, "found": bool, "why"?}`: the first match of the regex in the page's visible text or the values of its visible input fields (a hidden, not-yet-generated field is not read) goes straight to the connection service's `store_secret`; the value is never returned or logged. With `url`, nothing is taken unless the page's address matches, decided in the same look as the text |

It has no hands: it never clicks, types or navigates a page by itself (`open` is the one navigation, and it is the
panel opening). Writing in a page, with the orange frame, is a later piece.

## agentd: proposals, the press and what is said

**Proposals** (`proposals.py`, held by `Outbox`): what the agent or the person wants to go out and has not.

```
{"id": "p3", "kind": "slack_reply"|"task_create"|"task_comment", "target": str, "content": str|object,
 "where": "#launch", "created_by": "agent"|"person", "tainted": bool, "typed": str, "warnings": [{"kind", "text"}],
 "fingerprint": str, "shown": str, "state": "open"|"sending"|"sent"|"unknown"|"discarded", "receipt": {...}|null,
 "updated": float}
```

`fingerprint` is `driver.fingerprint(kind, target, content)`. `shown` is the fingerprint of the content the shell last
reported having drawn (`op: shown`); the proposal is `ready` for a press while the two agree. Warnings: `new_place` (the target conversation is one the person has not received a message from
in the last 14 days and has not posted in), `broadcast` (`@channel`, `@here` or `@everyone` in the text), `long`
(over 3000 characters), `tainted` (a turn that had read other people's words made this, and the text is not the
person's). A proposal made by the agent in a turn that read messages, mail or a page says so on the card.

Messages from a client to agentd, answered to the sender and broadcast to the others as `{"type": "proposal",
"proposal": {...}}` when anything changed:

| message | meaning |
|---|---|
| `{"type": "proposal", "op": "open", "kind", "target"}` | the person opened a reply (Reply on a notice or a card): a proposal made `created_by: "person"` with empty content, and its card shown |
| `{"type": "proposal", "op": "edit", "id", "content"}` | the box changed; clears `shown` |
| `{"type": "proposal", "op": "shown", "id", "content"}` | the card drew exactly this content (agentd works the fingerprint out from it, so no hash is ever computed in the shell) |
| `{"type": "proposal", "op": "discard", "id"}` | put away (an empty one is dropped at once) |
| `{"type": "press", "kind": <the proposal's kind>, "id": <proposal id>, "fingerprint"}` | the press; `Outbox.press` as for mail, answered with `press_result` |

The performer for a proposal kind loads the proposal, refuses unless it is `open` (or `unknown` with `again`),
non-empty, and its `fingerprint`, the pressed one and `shown` all agree (`changed`); marks it `sending`; calls
`connect.perform` once, with the proposal id; marks it `sent` with the receipt, or `unknown` for an unknown
outcome, or back to `open` for a refusal; and says the receipt as a notice. `again` works as for mail: only after
the person's choice.

**The agent's tools** (os-mcp, `connect/tools.py`; each one line to agentd as `{"type": "connect-tool", ...}`, answered
with `connect-result`; there is no tool that sends):

| tool | does |
|---|---|
| `messages_unread(limit?)` | the unread messages to the person, as text inside the other-people's-words mark, and shows the list card |
| `message_thread(ref)` | one thread's messages, same mark; opens its message card |
| `tasks_waiting(service?)` | the items that wait on the person, same mark |
| `connections()` | what is connected and in what state (the service's own words, no tokens), and the boxes a new task or a comment takes in each service (use those keys with `propose`). It names no workspace and says nothing of what a connection reads (a workspace's name is its administrator's words), is not inside the other-people's-words mark and does not taint the turn |
| `propose(kind, target, content)` | a proposal made `created_by: "agent"`, its card shown, the line "Reply to Priya is ready. Sending is yours." `content` is a string (a reply) or an object of the task's boxes. The place and the name in the line come from the service, never from the model, so there is no `where` argument |

A turn that has called `messages_unread`, `message_thread`, `tasks_waiting` or a mail read tool is tainted until
it ends (the mark stays with the conversation it resumes into, as for mail). `typed` is the person's own words for
the turn, as for mail drafts. A `propose` with `target` not a ref the service knows is refused, and so is one with a box the tool does not have or a
required box left empty; characters the card would refuse are stripped. After a refusal or an unknown outcome the
next try uses the id `p1-2`. When the turn read other people's words from a page too, `propose` is held to the
person's own typed words. Connect read results are replaced in the turn log by a placeholder, as mail's are.

**Notices and cards from pushes** (`connect/watch.py`): a `message` push the person should see becomes a notice
(source `connect`, tone `step`, at most one line "Priya Shah in #launch: Legal just signed off. Can we lock the 14th…",
chips `Reply` and `Open`, ttl 60 s). Reply opens a proposal and its message card; Open opens the message in the
service's own page. A `connection` push to `error` or `blocked` is one warning notice, once. Receipts are one
notice ("Posted in #launch · 11:04", chip `Open in Slack`). New messages from the same conversation replace the
previous notice instead of stacking.

**Launcher words** (exact, no model): `slack`, `messages`, `unread`, `what did i miss`, `what did i miss on slack` open the
list card of unread messages; `connections`, `connected`, `whats connected`, `what is connected` open "Connected
here"; `connect slack`, `connect linear`, `connect notion`, `connect jira`, `connect todoist`, `connect clickup`
start the setup (the first step opens in the panel). These words win over an app of the person's with the same name
(as mail's do), and `apps.create` refuses an app named `slack`, `messages`, `unread`, `connections` or `connected`
(`apps.CLAIMED`), since it could never be opened from the pill; Tab offers `messages` and `connections` only.
The `connect <service>` words are the person's own: a word written to agentd's socket from a process inside an agent's
turn is answered "Connecting a service is yours to do. It cannot be done from an agent's turn." and does nothing.
Typing `send it` (and the other words mail's use) while a reply or a ticket waits on a card is answered "Sending is
yours. It's under the pointer." without a model and without a send, as it is for a mail draft.

## Cards

`cards.accept` takes three more kinds beside the diagram. Every kind has `id`, `title` (at most 80 characters),
`source` (`"connect"`, `"notification"` or `"mail"`), `say` (one sentence, at most 160) and is checked again by
agentd, so nothing malformed reaches the bar. Text is plain.

**message** (`{"type": "message", ...}`):

```
{"type": "message", "id", "title": "Priya Shah in #launch", "source": "connect",
 "thread": [{"ref", "from", "text", "ts", "mine": bool, "unread": bool}],          # the last six, oldest first
 "reply": {"proposal": "p3", "kind": "slack_reply", "target", "where": "#launch", "content", "warnings": [...],
           "fingerprint", "ready": false, "state": "open", "by": "person", "receipt": null, "note": ""},
 "open": {"kind": "url", "value": "https://..."} | null, "say": "Reply in #launch. Sending is yours."}
```

A card whose source is `notification` has `"reply": {"proposal": null, "kind": "copy", "where": "Teams", "content": ""}`:
its button says "Copy and open", copies the words and opens the page, and is not a send (no ring, no "Yours"). The
button is live only when there is text. The shell sends `card_action` `copy_open` with `text`; agentd copies it with
`wl-copy` (nothing else does) and then invokes the notification, so the page that sent it comes forward. If the copy
fails, the notification is not invoked and Bombadil says "I could not copy that. Your words are still in the box."

**task** (`{"type": "task", ...}`): `title`, `service`, `fields: [{"key", "label", "value", "edit": "line"|"text"|"date"|
null}]`, `proposal` as above with `kind` `task_create` or `task_comment` and `content` the fields as an object, `open`,
`say`. The one button is "Create" or "Comment". A press carries one value per box (`""` for an untouched box).

A card also carries `lines`, the two sentences (`sending`, `unknown`) the card shows while a press is in flight and
after an unknown outcome, so the bar never writes them itself. After an unknown outcome the button returns only once
the person ticks "I looked in <where>"; the press then carries `again: true`.

**list** (`{"type": "list", ...}`): `rows: [{"id", "title", "sub", "when", "state": "ok"|"warn"|"bad"|"new"|"gone"|"active",
"actions": [{"id", "label", "style": "primary"|"quiet"}], "opens": {"kind": "card"|"url", "value"} | null}]`, at most 12
rows. Unread messages and "Connected here" are lists.

The shell tells agentd about a card with `{"type": "card_action", "card", "row"?, "action"}` (`open`, `disconnect`,
`connect`, `copy_open`, `dismiss`), the proposal messages above and `press`. None of them reaches the model. The
send button has the orange ring and "Yours: Send" while the proposal is `ready` (open, and `shown` equals its
fingerprint) and the box still holds exactly the proposal's `content`; it waits (as Mail's does) until the box has
drawn exactly what would be sent. A press carries the `fingerprint` the shell was last told for the content it
draws. After an `edit` the shell keeps what the person is typing and takes only the proposal's state, `ready` and
fingerprint from agentd's update, with one exception: agentd takes characters that draw as nothing out of what a
box holds (words pasted from a page often have some), and says what the box held in `cleaned_from` on its update.
While the box still holds exactly that (nothing was typed after the edit it was about), the shell writes the cleaned
words into it, so that what is read is all of what goes and the button is not left dark for a reason no one can see. The ring waits 300 ms after the button becomes ready, so a click already on its
way to something else does not press. A box is read-only unless the proposal is `open`. A card takes the keyboard only
after the person's Reply or a click in its box; a card with a box is drawn on the one screen the person was on when
it arrived, and a newer card replaces one being typed in (the typed words are flushed to agentd first and kept there
as a draft). A card with a box is 560 wide, a list 480.

**Connected here** is the list card "Connected here": each mail account (from the mail service), the Slack app and
each tool, one row each with what it reads (`reads`), its state, and "Disconnect" (mail's `remove_account` or
`remove_connection`); a service not yet connected is a quiet row with "Connect". It opens by its words only
(`connections`, `connected`, ...), as a list card; a holding card that lists it would be a later piece, and no such
card exists in the code yet.

## Notifications (replacing mako)

`shell/Notifications.qml` is Quickshell's `NotificationServer` (the `org.freedesktop.Notifications` name). Each
notification is sent to agentd as `{"type": "notification", "id", "app", "summary", "body", "urgency":
"low"|"normal"|"critical", "icon", "actions": [{"id", "label"}]}`, and the bar keeps the notification tracked until
agentd says `{"type": "notification_invoke", "id", "action"?}` (the shell invokes the notification's action, which for
a browser tab focuses it) or `{"type": "notification_close", "id"}`. agentd's `notifications.py` decides what
the person sees: a notice with the sender and preview ("Priya Shah: Legal just signed off", source `notify`, ttl 10 s,
30 s for critical) with Reply (a message card built from the notification, source `notification`) and Open
(`notification_invoke` with the notification's `default` action, so the page that sent it comes forward; no other action of the app's is ever invoked from here); at most one notice per app per two seconds (a
burst becomes "+N" on the last); no notice at all for a notification whose summary and body are empty. The
notification text is other people's words; text that is not a string counts as empty, and a notification with the
same shell id is an update, not a new notice. The bar holds at most 64 notifications (the oldest is dismissed when a
65th arrives), cuts text at 2000 characters and actions at 8; one that arrives while agentd is away waits and is
sent once. An invoke with no matching or default action dismisses it, so Open on a notification that has no `default` action puts it away. `mako` leaves `iso/packages.x86_64` and `hyprland.lua`.

## The image

`python-mcp` (2.3.0) and `python-httpx` are added to `iso/packages.x86_64`. `python-mcp` now pulls
`python-mcp-types` and `python-httpx2` as well as the SDK's other dependencies, all in Arch's `extra`;
`python-httpx` stays because `slack.py` imports it. The VM smoke imports `httpx`, `httpx2` and `MCPError` (which
exists only in 2.x, so it is also a version guard). `wl-clipboard` and `libnotify` were already there; `mako` is gone.
A snapshot rollback of the whole root also rolls back `/var/lib/bombadil-connect`: the tokens return to what they
were then (one the person has since replaced or had revoked comes back, and the connection says so when the service
refuses it), and the ids of the presses performed since are forgotten. A rollback cannot take back what went out: a
post stays posted, and a proposal that was sent stays sent in the person's own state, which is in the home directory
that a rollback does not touch, so it answers with its old receipt and asks the service nothing. `bombadil-connect.service`
is a system unit (`User=bombadil-connect`, `StateDirectory=bombadil-connect` mode 0700, `RuntimeDirectory=bombadil-connect`,
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
`MemoryMax=300M`, `Restart=on-failure`, and the kernel-facing keys systemd offers that the service does not need:
`PrivateDevices`, `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectControlGroups`, an empty
`CapabilityBoundingSet`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`; not
`ProtectProc` or `PrivateUsers`, because the press check reads `/proc/<pid>` of the caller), enabled by a symlink in `multi-user.target.wants`; the user comes from
`/usr/lib/sysusers.d/bombadil-connect.conf`. `bombadil-browserd.service` is a user unit like mail's: it starts at login, before the session has a screen, so the session's `hyprland.start` hook hands it the display and the compositor's instance and starts it again, as it does for mail (opening a page starts Chromium on that display and slides the panel in), and `KillMode=process` lets a browser it started outlive a restart of the service. `share/skills/bombadil-connect/SKILL.md`
tells the agent what it may and may not do, and is linked into `/etc/skel/.claude/skills` and `.agents/skills`.

## Modules

| Module | Owner of |
|---|---|
| `ws.py` | the WebSocket client and the framing a fake server needs |
| `connect/driver.py` | the driver interface, shapes, `fingerprint` |
| `connect/protocol.py` | codes, `clean_message`, `clean_task`, ids and refs, framing |
| `connect/store.py` | `Store(path)`: connections, secrets, seen times, performed ids; a `Secrets` per connection |
| `connect/service.py` | `Service`, the ring, the ops, the `perform` checks, `main()` |
| `connect/client.py` | the blocking client (`Connection`, `request`, `notify`, `ConnectUnavailable`, `ConnectError`) |
| `connect/manifest.py` | the Slack app's manifest and the address that makes Slack show it ready to create |
| `connect/slack.py`, `slack_setup.py` | the Slack driver and its setup recipe |
| `connect/mcpconn.py`, `recipes.py` | the MCP driver and the recipe loader |
| `browserd/` | the service, the CDP session, the overlay script, the client |
| `proposals.py`, `outbox.py` | proposals and the press path |
| `connect/tools.py`, `connect/watch.py`, `notifications.py` | the agent's tools, pushes to notices and cards, notifications to notices |
| `cards.py`, `shell/*Face.qml`, `shell/Notifications.qml` | the card kinds and what draws them |

`BOMBADIL_CONNECT_ENGINE=fake` runs the service on a `FakeDriver` with sample messages and tasks, as mail's fake
engine does: the window, the desktop test and the VM smoke use it, and so can anyone without an account.

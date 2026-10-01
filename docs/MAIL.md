# Mail

Bombadil's own Mail view: every mail account in one list, Needs a reply, Drafts, a reply box whose Send
is the person's press, and an agent that can read, search, mark and draft but cannot send. Design:
`design/everyday-work-brief.md` piece 1 ("Bombadil at work"), with Daniel's answers 1a 2b 3a. This file
is the contract the code is built to; where the two differ, this file says what was built and why.

## The line

- **Thunderbird is the engine, unseen.** Gmail and Microsoft will not let a new app read mail without a
  review or an admin; Thunderbird is the one mail program both let a person allow alone. It runs on a
  Hyprland special workspace nobody visits, with Bombadil's profile under `~/.local/share/bombadil/mail`,
  and keeps a copy of the mail on this machine (inside the encrypted disk once the installed OS lands).
- **Bombadil keeps no mail text.** The service keeps, in `mail.db`: accounts, Bombadil's own drafts,
  "needs a reply" marks with one line of why, receipts and a press log. A mail's text is read from
  Thunderbird on demand, held in memory while a window needs it, and never written by Bombadil. The Brain
  may learn sender, subject, time and a link (`recent`), never the text.
- **The agent reads, never obeys.** `mail_read` gives the text inside a mark that says it is other
  people's words. The agent has no send tool; `mail_draft` puts a draft in the view; only a press on the
  view's Send lets it go (rule 2). Addresses the person did not type and the thread does not contain are
  flagged on the draft (rule 5).

## Processes

```
 Mail window (kit app share/apps/mail)  --QLocalSocket-->  bombadil-mail  <--unix socket-->  bombadil-mail-host
 agentd (mail-tool, press, notices)     --unix socket--->   (mail.sock)                          |  stdio frames
 bombadil CLI                           --unix socket--->                                  Thunderbird + add-on
```

| Part | Where | What |
|---|---|---|
| `bombadil-mail` | `bin/bombadil-mail`, `src/bombadil/mail/service.py`, user unit `bombadil-mail.service` | Owns `mail.sock` and `mail.db`; supervises Thunderbird; answers the window, agentd and the CLI |
| Thunderbird | package `thunderbird`, profile `~/.local/share/bombadil/mail` | Fetches, stores, sends. Its window lives on `special:mail-engine` |
| Add-on | `share/mail/extension/` | Inside Thunderbird; answers the service's engine requests with the `messenger.*` APIs |
| `bombadil-mail-host` | `bin/bombadil-mail-host` | The native messaging host the add-on connects to; relays frames to `mail.sock` |
| Mail window | `share/apps/mail/` | A kit app: `main.qml` plus `app.py` whose `Backend` talks to `mail.sock` |
| agentd | `src/bombadil/agentd.py`, `outbox.py`, `notices.py` | Brokers the agent's mail tools, routes presses, says new mail and receipts as the pill line |
| os-mcp | `src/bombadil/mail/tools.py` | `mail_search`, `mail_read`, `mail_draft`, `mail_mark`, `mail_show`: each a line to agentd |

Nothing waits for mail: a caller that cannot reach the service says "Mail is not running yet" in its own
line, agentd's pokes give up after 0.3 s, and a turn never blocks on Thunderbird.

## Files and state

| Path | Env override | Holds |
|---|---|---|
| `$XDG_RUNTIME_DIR/bombadil/mail.sock` | `BOMBADIL_MAIL_SOCKET` | the service socket |
| `~/.local/state/bombadil/mail.db` | `BOMBADIL_MAIL_DB` | accounts, drafts, marks, receipts (SQLite, WAL) |
| `~/.local/state/bombadil/mail/` | `BOMBADIL_MAIL_FILES` | copies of a draft's attachments (`drafts/<id>/`), fetched attachments in flight |
| `~/.local/state/bombadil/presses.jsonl` | `BOMBADIL_PRESS_LOG` | one row per press: who, what, fingerprint, result (no mail text) |
| `~/.local/share/bombadil/mail/` | `BOMBADIL_MAIL_PROFILE` | the Thunderbird profile |
| `~/.mozilla/native-messaging-hosts/bombadil_mail.json` | - | host manifest, written by the service at start |

## Wire protocol, client side (`mail.sock`)

JSON lines. Request `{"id": int|str, "op": str, ...}`; answer `{"id", "ok": true, "result": ...}` or
`{"id", "ok": false, "error": "one plain sentence", "code": str}`. After `subscribe` the service also
sends pushes `{"push": name, ...}`. Codes: `engine_down`, `no_account`, `not_found`, `bad_request`,
`changed` (the draft is not what the view showed), `refused`, `too_big`, `engine_error`,
`unknown_outcome` (a send that may or may not have happened).

Shapes (all fields always present unless marked optional):

- **Addr** `{"name": str, "email": str}`
- **Account** `{"id": "a1", "email", "name", "provider": "google"|"microsoft"|"icloud"|"imap",
  "state": "ok"|"syncing"|"signin"|"blocked"|"error", "note": str, "web": {"name": "Gmail", "url": str},
  "unread": int}`
- **Msg** `{"id": "<account>/<key>", "account": "a1", "key": str, "from": Addr, "to": [Addr], "cc": [Addr],
  "subject", "ts": float (epoch s), "unread": bool, "flagged": bool, "attachments": bool,
  "folder": "inbox"|"sent"|"drafts"|"archive"|"trash"|"other", "needs_reply": bool, "why": str|null,
  "thread": str|null}`
- **Draft** `{"id", "account", "kind": "new"|"reply"|"reply_all"|"forward", "reply_to": msg id|null,
  "from": Addr, "to": [Addr], "cc": [Addr], "bcc": [Addr], "subject", "body",
  "attachments": [{"name", "size", "sha256"}], "fingerprint": str, "created_by": "person"|"agent",
  "warnings": [{"kind": "new_address"|"sensitive_file"|"big"|"other", "text": str, "addresses": [str]}],
  "adds": str (what the provider adds when it sends), "state": "open"|"sending"|"sent"|"discarded"|
  "unknown", "receipt": Receipt|null, "updated": float}`
- **Receipt** `{"draft": id, "to": [Addr], "from": Addr, "ts": float, "message_id": str, "web":
  {"name", "url"}, "line": "Sent to Priya from maya@acme.com · 09:08"}`

Ops: `ping`; `status`; `accounts`; `add_account {email}`; `remove_account {id}`; `views`;
`list {view, limit?, cursor?}` with view `all` | `needs_reply` | `drafts` | `acct:<id>`; `search {text?,
from?, account?, unread?, since?, limit?}`; `read {id}`; `set_flags {id, read?, flagged?}`;
`archive {id}`; `trash {id}`; `mark_reply {id, needs, why?}`; `save_attachment {id, part, dir?}`;
`draft {...}`; `draft_edit {id, ...}`; `draft_get {id}`; `draft_discard {id}`; `draft_shown {id,
fingerprint}`; `send {id, fingerprint}`; `known {emails}`; `subscribe`; `show {view?, id?, reply?}`;
`requested`; `recent {since?, limit?}`; `engine_window {action: "stage"|"hide"}`. The exact arguments and
results are in `src/bombadil/mail/service.py` (one handler per op) and its tests.

Pushes: `changed {what: ["list","accounts","drafts","status"]}`, `new_mail {message: Msg, known: bool}`,
`show {view, id, reply, seq}`, `sent {receipt}`, `status {...}`.

## The press

A draft is the person's until it reaches someone, and the only thing that lets it reach someone is a
press on the Send button the view shows, for exactly what the view showed:

1. A draft has a `fingerprint`: SHA-256 of its canonical content (account, kind, reply-to, recipients,
   subject, body, and each attachment's name, size and SHA-256). Attachments are copied into the draft's
   own folder when they are added, so what is fingerprinted is what is sent.
2. The window, when it draws a draft, reports `draft_shown {id, fingerprint}`. Any edit (the agent's or
   the person's) changes the fingerprint and clears what was shown.
3. The press is `{"type": "press", "kind": "mail", "id": draft, "fingerprint": fp}` to agentd (and
   `"again": true` only for a draft whose send was `unknown`, which the person chose to press again).
   agentd refuses a press from a process inside an agent turn's scope (`procs.cgroup_of`) or in the
   running turn's process tree (a turn with no scope), and one whose peer it cannot name (`SO_PEERCRED`;
   it fails closed). It logs the press, calls the service's `send` once, and answers the sender with
   `press_result` (below). The service refuses unless the stored fingerprint, the pressed one and the
   shown one agree, the draft is `open`, there is a recipient, the attachments still hash the same and
   the size is under the account's limit.
4. `sending` is written before the engine is asked; a draft found `sending` after a crash becomes
   `unknown` ("Thunderbird did not say whether this went; look in Sent before pressing again"). Nothing
   retries a send. Pressing twice sends once.
5. A typed "send it" does nothing but say that sending is yours and where the button is (choice 1a):
   the launcher's word table answers it, and only when agentd finds an open draft waiting (asked of the
   service, waiting at most a second); with no draft, or a question ("send it?"), it is the model's
   like any other words.

This is a rule with a check, not yet a wall: the agent runs as the person, with a shell and sudo, and
could in principle write to `mail.sock` or read Thunderbird's profile. The brief's hardening order
(bombadil-connect as its own user, agentd accepting presses only from the shell's own process, the sudo
question) is what turns it into a wall. Bypasses that agentd cannot close from where it stands: a process
the agent starts with `systemd-run --user` is in no turn scope and no process tree, and so is a write
straight to `mail.sock`; an app of the person's own called `mail` (`~/Apps/mail`) would be run by
`apps.app_dir` in place of the real window (the launcher refuses to open it, but `apps.py` is not
agentd's). The press log (`presses.jsonl`) holds both writers' rows: agentd's with `"src": "agentd"`,
the service's with `"src": "mail"`; a send the service reports that no press of agentd's started is
written with code `no_press` and said on the pill ("That was not your press on Send.").

## Agent tools

Each is `os-mcp tool -> {"type": "mail-tool", "id", "turn", "op", ...} -> agentd -> mail.sock`, answered
with `{"type": "mail-result", "id", "ok", "text"}`. They work only inside the turn that is running (agentd
compares `turn`), and only the asking client is answered. `id` names the request, so the mail's own id
travels on this line as `mail` (the tool's `id` argument); ops are `search`, `read`, `mark`, `draft`,
`show`, and there is no other: nothing sends, lists drafts, or discards.

- `mail_search {text?, from?, account?, unread?, since?, limit?}`: senders, subjects, times and ids, as
  rows between the same marks as a read (the ids are copied from the mail, so they are its words too).
- `mail_read {id}`: the text between marks that carry a random word per read ("Other people's words
  begin (a1b2c3d4e5f6)"), cut at 20 000 characters inside them and said; control and bidi characters are
  stripped. The turn is marked as having read mail, and so is a `mail_search` that found some (the
  senders and subjects are other people's words too); the mark stays with the conversation the turn
  resumes into, since the model still has the text, and ends with a new conversation. Reading leaves the mail unread.
- `mail_mark {id, needs_reply, why}`: fills Needs a reply; `why` is one line under 140 characters.
- `mail_draft {reply_to? | to, subject, body, cc?, attachments?, account?}`: a draft in the view, which
  opens on it, and a notice above the pill ("Reply to Priya is ready. Sending is yours."). agentd adds
  `created_by: "agent"`, `typed` (the words the person typed for this turn, none for a coding session's
  turn) and `tainted` (the turn, or the conversation it resumes, has read mail) for the service's address
  check. The first draft a
  person gets also says, once on this machine (`told.json`), that the agent never presses Send.
  Attachments come from paths; credentials-shaped paths are refused. Recipients the person's words and
  the thread do not contain, and that are not in the address book or Sent, are flagged on the draft
  (more strongly when the turn had read mail).
- `mail_show {view?, id?}`: slide the Mail window in on a view.

## agentd: the pill's notices, the press and what it watches

Messages on agentd's own socket (docs in `agentd.py`'s header):

- `{"type": "notice", "id", "source": "mail", "line", "tone": "step"|"ask"|"done"|"error", "actions":
  [{"id", "label", "style": "primary"|"quiet"}], "ttl", "at"}`: a line above the pill, sent to every client
  and to one that joins later, and again with the same `id` when it changes. At most four are live
  (the oldest goes); `ttl` 0 waits, else it ends by itself. `{"type": "notice_end", "id"}` says it is gone.
- Client to agentd: `{"type": "notice_action", "id", "action"}` (a chip; the notice ends unless the action
  failed or changed it, and a failure is said on the notice itself) and `{"type": "notice_dismiss", "id"}`.
  Both are refused, like a press, to a process inside an agent's turn or one agentd cannot name: Reply
  makes a draft as the person's, and a dismissal would hide what agentd said.
- `{"type": "press_result", "kind", "id", "ok", "line", "code", "receipt"}`, to the pressing client only.
  `code` is `""` when it went, else `agent`, `no_peer`, `busy`, `bad_request`, `unknown_kind`, `error`,
  `unknown_outcome` (said in words: "I can't tell whether that went. Look in Sent before you press Send
  again."), `engine_down`, or the service's own code (`changed`, `refused`, `too_big`, ...). Nothing
  retries.

What agentd says, from `mail/watch.py`: new mail from a sender the service says is `known` is one line
("Priya Shah: Launch date", five minutes) with Reply (a reply draft made `created_by: "person"` and
shown) and Open; anyone else waits in the view. A draft from the agent is "Reply to Priya is ready.
Sending is yours." with Open, and stays until that draft is sent (or the person puts it away). A send is
one receipt ("Sent to Priya from maya@acme.com · 09:08", with "Open in Gmail" when the provider has a
link), said once whichever of the press's answer and the service's `sent` push arrives first. A `show`
push brings the Mail window in, unless agentd just did. Mail is optional: with no service agentd retries
every 5 s and says nothing, and a service silent for 45 s is pinged and, if it does not answer in 10 s,
reconnected to.

The launcher answers "mail", "email", "inbox" (with open/show/close/hide) itself; `Launcher.open_mail(**show)`
asks the service to `show` (waiting at most a second) and slides the window in, and `bombadil mail
[status|accounts|add|remove|views|list|show]` is the same from a terminal, with no send.

## Thunderbird and the add-on

See the section "Engine protocol" below and `tests/mail/lab/FINDINGS.md` for what a real Thunderbird
does. The service asks, the add-on answers; nothing in Bombadil reads Thunderbird's files.

## Engine protocol (service <-> Thunderbird add-on)

Transport: the add-on keeps one native-messaging port to `bombadil-mail-host`, which keeps one socket to
`mail.sock`. Frames are JSON objects: on stdio with a 4-byte native-endian length in front (what native
messaging does), on the socket one per line. The host opens with `{"op": "engine_hello", "pid": n}`;
from then on that connection carries engine frames both ways and nothing else. The service serves the
protocol and the add-on answers it.

Request `{"id": n, "op": ..., ...}` (service to add-on); answer `{"id": n, "ok": true, "result": ...}` or
`{"id": n, "ok": false, "error": "a sentence", "code": str}`. Events from the add-on (no id):
`{"event": "hello", "version", "app", "app_version", "caps": [...]}` (first frame after the connection),
`new_mail {account, messages: [EMsg]}`, `accounts_changed`, `sync {account, state: "syncing"|"idle"|"error"|
"signin", detail}`, `counts_changed`, and `blob` (below).

**EMsg** `{"key": str, "account": engine account id, "folder": "inbox"|"sent"|"drafts"|"archive"|"trash"|
"other", "from": Addr, "to": [Addr], "cc": [Addr], "subject", "ts": float, "unread", "flagged",
"attachments": bool, "thread": str|null, "message_id": str}`. `key` is the Message-ID header without angle
brackets, else `fp:` and a SHA-1 of author, date and subject; it is stable across restarts, where
Thunderbird's own message ids are not.

| op | args | result |
|---|---|---|
| `info` | | `{version, app, app_version, api: {messages_send: bool, compose_reply: bool}}` |
| `accounts` | | `[{engine_id, name, type, emails: [str], identities: [{id, email, name}], folders: {inbox, sent, drafts, archive, trash: bool}, unread: int, state: "ok"\|"syncing"\|"signin"\|"error", detail}]` |
| `list` | `account, folder, unread?, limit, before?` | `{messages: [EMsg], more: bool}`, newest first |
| `find` | `accounts?, folders?, text?, from?, to?, subject?, unread?, flagged?, since?, until?, attachments?, limit` | `{messages: [EMsg]}` |
| `get` | `account, key` | `{message: EMsg, text: str\|null, html: str\|null, headers: {message-id, in-reply-to, references, reply-to}, attachments: [{part, name, content_type, size, inline}]}`; reading leaves the mail unread |
| `mark` | `account, key, read?, flagged?` | `{}` |
| `move` | `account, key, to: "archive"\|"trash"\|"inbox"` | `{}` |
| `attachment` | `account, key, part` | `{name, content_type, size, xfer}` and then `blob` events for that `xfer` |
| `blob` | `xfer, seq, data (base64, at most 384 KiB of bytes), last` | `{}`, one at a time, before a `send` that names the `xfer` |
| `send` | `account, identity?, kind: "new"\|"reply"\|"reply_all"\|"forward", reply_to: key\|null, to, cc, bcc, subject, body (plain text), attachments: [{name, content_type, xfer}]` | `{message_id, saved: bool}` once the provider has the message (send now, never a hidden queue) |
| `known` | `emails` | `{email: bool}` from the address books and the Sent folders |

`blob` event `{"event": "blob", "xfer", "seq", "data", "last"}` carries an attachment the add-on was asked
for, in order. Every request has a timeout in the service (10 s for reads, 60 s for `send`, 120 s for an
attachment); a timed-out `send` is `unknown_outcome`, never retried.

## Modules

| Module | Owner of | Used by |
|---|---|---|
| `mail/protocol.py` | codes, ids (`msg_id`, `split_id`), `Addr` parsing and formatting, native-messaging framing (`nm_read`, `nm_write`), canonical JSON | everything |
| `mail/text.py` | HTML to text, quoting, filenames, sensitive-path test, addresses in text | service, drafts |
| `mail/store.py` | `Store(path)`: SQLite for accounts, marks, drafts, receipts, press log | service |
| `mail/drafts.py` | fingerprint, attachment copies, warnings (new address, sensitive file, size) | service |
| `mail/accounts.py` | `Provider`, `detect(email)` (known domains, then MX through a small resolver, then a generic IMAP guess), web links | service, engine |
| `mail/bridge.py` | `EngineLink` (asyncio request/response, events, blob transfer), `serve_engine` | service |
| `mail/fake.py` | `FakeEngine` (same interface as `EngineLink`, in-memory mailboxes, sample mail including a hostile one), `FakeProcess` | tests, `BOMBADIL_MAIL_ENGINE=fake` |
| `mail/engine.py` | `ThunderbirdProcess`: profile, prefs for accounts, add-on and host install, start, supervise, stage and hide the window | service |
| `mail/service.py` | `Service`, `main()` | `bin/bombadil-mail` |
| `mail/client.py` | blocking client: `Connection`, `request(op, **args)`, `notify(op, **args)`, `MailUnavailable`, `MailError` | agentd, launcher, CLI |
| `mail/tools.py` | the five os-mcp tools (`register`) and agentd's `Broker` for them | `mcp_server.py`, agentd |
| `mail/watch.py` | `Watch` (subscribe to `mail.sock`, reconnect, test silence), `Says` (pushes and drafts to notices) | agentd |
| `outbox.py` | `Outbox`: the press registry, log and told-once sentence; `notices.py`: `Notices`, the pure stack | agentd |

`BOMBADIL_MAIL_ENGINE=fake` runs the service on the fake engine with sample mailboxes: the window, the
desktop test and the VM smoke use it, and so can anyone without an account.

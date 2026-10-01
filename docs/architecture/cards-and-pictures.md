# Cards and pictures

> **Status:** Partly shipped
> **Code:** `src/bombadil/cards.py`, `src/bombadil/cardtools.py`, `src/bombadil/sysmap.py`, `src/bombadil/narrate.py`, `src/bombadil/pager.py`, `shell/CardHost.qml`, `shell/StatusLine.qml`, `share/qml/Bombadil/Diagram.qml`
> **Design:** [Riding with Bombadil, piece 1](../design/passenger-brief.md#1-why-and-after-reading-what) and [piece 2](../design/passenger-brief.md#2-pictures-from-the-machine), [How Bombadil should feel, piece 5](../design/ux-brief.md#5-answers-you-can-touch)
> **Verified:** 2026-10-01 against `main` at `26843d3`

A card is validated data for a picture that the shell draws above the status line. One card type exists,
`diagram`, in four fixed shapes (`chain`, `layers`, `compare`, `timeline`), and every card carries a text
version of itself. Cards come from four places: the agent (`show_card`), the machine itself (`system_map`),
exact questions typed in the pill such as "how am I connected" (no turn, no model), and the receipt that follows
a turn. The same piece keeps why the agent did each step (`because`) and what it had read from outside
(`after`), so a person can see the road without asking. Nothing in it asks a model: pictures of the machine are
read from real commands, and a reason is the agent's own sentence from just before it acted.

## What is built and what is designed

| Part | Status | Where |
|---|---|---|
| `diagram` card, four shapes, text twin | Shipped | `cards.py`, `Diagram.qml`, `CardHost.qml` |
| Tools `show_card` and `system_map` | Shipped | `cardtools.py`, registered from `mcp_server.py` |
| Picture words typed in the pill | Shipped | `launcher.py` (`PICTURE_PHRASES`), `agentd.py` (`picture`) |
| Streaming a card a box at a time | Shipped for Claude only | `cards.py` (`CardStream`), `agentd.py` |
| Receipts, a before and after of what a turn changed | Shipped | `sysmap.py` (`receipt`), `agentd.py` (`_receipt`) |
| Why lines (`because`, `after`) and a bare "why" during a turn | Shipped | `narrate.py`, `StatusLine.qml`, `launcher.py` |
| Details drawer and `bombadil view` | Shipped | `launcher.py`, `pager.py` |
| Other card kinds: list, checklist, timer, control, markdown reader | Designed | [UX brief, piece 5](../design/ux-brief.md#5-answers-you-can-touch) |
| Cards the agent writes in QML | Designed | UX brief, piece 5 |
| The `teach` explain level (a faint chip on system words that opens their picture) | Designed | [Passenger brief, "How much does it explain?"](../design/passenger-brief.md#decisions-i-picked-a-default-for) |

The desk draws its own cards (`DeskCard.qml`, `RowsCard.qml`, `NowCard.qml`); they do not use `Diagram` and are
described in [the desk](desk.md). The status line's other behaviours (the counter, the command, Undo, Details) are
described in [the shell](shell.md); this page covers only its `because` and `after` lines.

## How it works

```mermaid
flowchart LR
    SC["show_card"] --> VAL["cards.validate_diagram"]
    SM["system_map"] --> CAP["sysmap.capture"]
    CAP --> VAL
    VAL --> DEL["cardtools.deliver"]
    DEL -->|"socket: card"| ACC["agentd: cards.accept"]
    ACC -.->|"card_ack"| DEL
    ACC --> BC["agentd: card event to every client"]
    PW["typed picture word"] -->|"sysmap in agentd, no model"| BC
    RC["receipt after a turn"] --> BC
    BC --> PS["PillState._takeCard"]
    PS --> CH["CardHost.qml"]
    CH --> DG["Diagram.qml"]
```

1. **A card is made.** The agent fills in `show_card`, or asks `system_map` for a subject (both are tools of
   [os-mcp](os-mcp.md), registered by `cardtools.register`). `system_map` runs
   `sysmap.capture`, which reads the machine and builds its own card through the same `cards.validate_diagram`.
   With `system_map` the agent supplies only a subject, a pointer and one line. With `show_card` it supplies the whole
   diagram, and the tool's description tells it never to draw this machine's state that way.
2. **The tool delivers it.** `cardtools.deliver` writes `{"type":"card","card":{...}}` to the `agentd` socket and
   waits for `{"type":"card_ack"}` (the socket timeout is `ACK_TIMEOUT`, 2 seconds). The tool's result is the card's
   text twin plus a note on whether it was drawn, so the agent writes one short line instead of describing the
   picture.
3. **[`agentd`](agentd.md) checks it again.** `cards.accept` re-validates, so nothing malformed reaches the bar whoever wrote
   to the socket, and keeps the three fields only the OS adds (`source`, `target`, `receipt`). It broadcasts a
   `card` event to every client, then answers `card_ack`.
4. **Two other doors lead to the same event.** A typed picture word is answered inside `agentd` (`picture`), with a
   `local` line first and no model call. A receipt is made after the turn closes (`_receipt`). Both go straight to
   `_show` with no socket hop.
5. **The shell draws it.** `PillState._takeCard` keeps one card. `CardHost.qml`, loaded through a `Loader`, holds
   the title, a source label, a close mark, the kit's `Diagram` inside a scroll area (the shell caps the host at 60
   percent of the screen height, and a taller picture scrolls), and the `say` line. The
   `Diagram` draws the card and places its boxes; Python computes only the `rank` and `col` of a `layers` card and the
   `row` pairing of a `compare` card.

### A turn that records a reason and ends with a receipt

```mermaid
sequenceDiagram
    autonumber
    participant Bar as Shell bar
    participant D as agentd
    participant N as narrate.Narrator
    participant C as Provider CLI
    participant S as sysmap
    Bar->>D: prompt "set my DNS to 1.1.1.1"
    D->>Bar: turn_start (the bar puts away any card)
    D-->>S: snapshot of network, disks, sound and screens (background)
    C->>D: text "NetworkManager owns DNS here, so I will change its settings."
    D->>N: on_event(text)
    C->>D: tool Bash "nmcli connection modify ... ipv4.dns 1.1.1.1"
    D->>N: on_event(tool)
    N-->>D: step marked system, because kept, network touched
    D->>Bar: status with text, risk, command and because
    Note over Bar: because shows at once, the step is marked
    Note over D: the tool event is logged with because for Details
    C->>D: result
    D->>Bar: turn_end (the closing line)
    D->>S: snapshot of the network after the turn
    S-->>D: receipt(network, before, after)
    D->>Bar: card event with receipt true
```

The reason is kept as the text arrives: the narrator holds the agent's words of the current model message, and when a
tool starts it keeps the last cleaned sentence as `because`. The step's parts of the machine (`parts_touched`) decide
which snapshots are compared afterwards. Nothing blocks the turn: the first snapshot is taken in the background, and
the receipt is made after `turn_end`.

### What a card is

There is one card type. `cards.validate_diagram` is the only validator and returns every fixable error at once, each
saying what and why, so the agent can correct the call in one go. This is what it accepts.

| Card field | Rule |
|---|---|
| `shape` | required: `chain`, `layers`, `compare` or `timeline` |
| `title` | required, at most 60 characters (longer is an error, it is not cut) |
| `nodes` | 1 to 12 objects |
| `links` | optional, at most 16 |
| `highlight` | a node id or a list of ids; an unknown id is an error |
| `say` | optional, at most 160 characters, one sentence |
| `linked` | `chain` only: `false` draws boxes with no arrows. Without `links`, a chain is linked in order |
| `kind` | `show_card` input only: `diagram` (the default). Any other value is refused by the tool |

| Node field | Rule |
|---|---|
| `label` | required, at most 32 characters (longer is an error that says to use `sub`) |
| `id` | optional, default `n<position>`; letters, digits and `_ . : @ / -`, up to 40; unique |
| `sub` | second line, at most 60 characters (longer is an error) |
| `state` | `ok`, `warn`, `bad`, `new`, `gone` or `active`. How the kit draws each is in the table below the shapes |
| `icon` | a kit icon name, checked against `share/qml/Bombadil/icons/*.svg` when that folder is found |
| `side` | `before` or `after`; required on every node of a `compare` card |
| `key` | `compare`: the same key on both sides puts the two boxes on one row (cut at 40 characters) |
| `time` | `timeline`: text such as `0.4 s`, cut at 24 characters |
| `weight` | `timeline`: a number (anything else is an error); a negative one or `nan` becomes 0 without an error. The bar is as long as this against the longest step |
| `note` | a line under the box, cut at 60 characters |
| `volatile` | a boolean that is kept on the node; nothing reads it (see Known gaps) |
| `opens` | `{"kind", "value"}`, what a click opens (next table) |

| `opens.kind` | `value` must be |
|---|---|
| `path` | absolute, `~`, or starting with `~/` |
| `unit` | a systemd unit name ending in `.service`, `.socket`, `.timer`, `.target`, `.mount`, `.path`, `.slice`, `.scope`, `.device` or `.swap` |
| `package` | a package name (`a-z`, digits, `@ . _ + -`) |
| `url` | `http://` or `https://` |
| `turn` | a turn number of up to 9 digits |

A link is `{"from", "to", "label", "state"}`. `from` and `to` must be node ids, `label` is at most 24 characters
(an error if longer), and a `state` that is not one of the six is dropped without an error.

The validator adds what the model should not have to write: ids, the links of a chain, `rank` and `col` for every
box of a `layers` card, `row` for every box of a `compare` card, and `text`. A finished card from `show_card`
looks like this (the input was three boxes, one of them with `state` and `opens`, a `highlight` of the second box, and a `say`):

```json
{"type": "diagram", "shape": "chain", "title": "How a VPN works",
 "nodes": [{"id": "n1", "label": "Your device"},
           {"id": "n2", "label": "VPN server", "state": "new", "opens": {"kind": "url", "value": "https://example.com/vpn"}},
           {"id": "n3", "label": "Website"}],
 "links": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
 "highlight": ["n2"], "say": "Traffic goes through the VPN server first.",
 "text": "How a VPN works: Your device → VPN server [new] → Website\nTraffic goes through the VPN server first."}
```

`cards.accept` keeps three fields that only the OS itself may add: `source` (one of `network`, `boot`, `service`,
`disks`, `sound`, `screens`; the shell labels the picture "from this machine" when it is present and "drawn by the
agent" when it is not), `target` (a unit name, set by the `service` capture) and `receipt: true`. `agentd` then
stamps an `id` (`card-<n>`, or `stream-<tool id>` when the card was drawn while it streamed). A draft also carries
`partial: true`.

| Shape | Meaning of the links | Where the layout is done |
|---|---|---|
| `chain` | the path, boxes left to right, wrapping to rows when they do not fit | `Diagram.qml` |
| `layers` | a link from a box to what it needs; a box sits below everything that points at it | `cards.rank_layers` writes `rank` and `col`: cycles lose the edge that closes them, rank is the longest path, four sweeps order each row to avoid crossings |
| `compare` | none; `side` puts a box left (`before`) or right (`after`), `key` pairs rows | `cards.validate_diagram` writes `row`; `Diagram.qml` places it |
| `timeline` | none; one row per box with its `time`, `label`, a bar for `weight` and `sub` on the right | `Diagram.qml` |

How `Diagram.qml` draws a `state` (`_tone` and `_mark`):

| `state` | A box (`chain`, `layers`, `compare`) | A `timeline` row |
|---|---|---|
| `ok` | plain, no mark | grey dot, `Theme.accent` bar |
| `warn` | `Theme.warn` and a triangle | `Theme.warn` dot, bar and `sub`, no mark |
| `bad` | `Theme.bad` and an x | `Theme.bad` dot, bar and `sub`, no mark |
| `new` | `Theme.accent` and a plus | `Theme.accent` dot and bar, no mark |
| `gone` | dim, a minus and the label struck through | dim dot and bar, the label struck through, no mark |
| `active` | `Theme.info`, no mark | `Theme.info` dot and bar, no mark |

The layout is fixed and never a force layout: the same data always draws the same picture. `cards.text_of` writes
the text twin from the same data, so it cannot disagree with the picture. The marks in it are `[slow or weak]`
(`warn`), `[broken]` (`bad`), `[new]` and `[gone]`. A chain reads `title: a → b → c` (boxes joined with `;` when it
has no links), `layers` reads one line per box as `app needs database`, `compare` reads `Before: ...` and
`After: ...`, and `timeline` reads `time: name` per row. The twin is what the agent gets back, what the shell
exposes to screen readers (`Accessible.name`), and what the details drawer lists for a picture.

### Streaming a box at a time

Streaming works for Claude only. The Claude command line runs with `--include-partial-messages`, so the provider layer
yields `tool_start` and `tool_input` events (the call's JSON as it is written). The Codex path yields neither.

1. `cards.CardStream.feed` follows each call whose tool name ends in `__show_card` and appends its partial JSON.
2. `cards.partial_diagram` reads `shape`, `title` and each finished object of `nodes` (a reader that tracks braces and
   quoted strings, so a box cut in the middle of a word is left out). It keeps only `id`, `label`, `sub`, `state` and
   `side`. Validation and layout wait for the whole card.
3. It returns a draft when the shape, the title or the number of finished boxes changed: a card with `partial: true`,
   no links and the id `stream-<tool id>`.
4. `agentd` broadcasts each draft as a `card` event. Drafts are not logged to the turn's file; only the finished card is.
5. When the finished call reaches `agentd` as a `card` message, `_card_message` takes the oldest open stream id and
   gives the finished card that same id.
6. A draft is taken back with `{"id": "stream-...", "gone": true}` when its call fails (`tool_result` for that
   call), when the turn ends with the draft still open, or (in the shell) when the connection to `agentd` is lost.

In the shell a draft shows the label "drawing…", and each box fades in as it arrives. Because a draft has no links and no `rank`,
a `layers` draft shows its boxes in one row until the finished card replaces it.

### Replacing in place

The shell holds one card (`PillState.card`). A card event whose `type` is not `diagram` is ignored. Any other card
replaces the one on screen, and one with the same `id` is the same picture changing: the draft grows into the
finished card in place. A card leaves when:

| Event | Effect |
|---|---|
| The next turn starts (`turn_start`) | any card is put away |
| Esc with nothing to stop and an empty input, or the close mark | `PillState.dismiss`, `PillState.dismissCard` |
| `{"id", "gone": true}` for the card on screen | put away |
| A picture word that could not be drawn (`local` event, `action` `picture`, `ok` false) | the old card is put away so the error does not read as about it |
| The connection to `agentd` drops while the card is a draft | put away (`lost`) |
| The closing line fades (`fade`) | only a receipt goes. A picture the person asked for stays until Esc or the next turn |

The closing line fades after 12 seconds (15 with a receipt, so the two are read together), and resting the pointer on
the line or the card holds it. A turn that changed something keeps its closing line with Undo until the next prompt
(`sticky`, see [restore points](restore-points.md) for Undo), and then the receipt stays with it.

### What a click returns

```mermaid
flowchart LR
    BOX["box with opens"] -->|"tap: opened"| OT["PillState.openThing"]
    OT -->|"kind turn: details"| DET["agentd.details: bombadil watch"]
    OT -->|"other kinds: open"| OP["agentd.open_thing: check_opens again"]
    OP --> L["launcher.open_thing"]
    L --> V["drawer: bombadil view"]
    L --> X["browser panel or xdg-open"]
    DET --> DR["details drawer, a foot window"]
    V --> DR
```

`Diagram` emits `picked(node)` for every tap on a box and `opened(target)` when the box has `opens`. `CardHost` listens
to `opened` only. `PillState.openThing` sends nothing while the socket is down (it flashes "Not connected to the agent
yet."). A `turn` target first gives the keyboard back so the drawer can take it (`handOff`) and sends
`{"type":"details","turn":n}`; every other target
sends `{"type":"open","kind":"...","value":"..."}`. `agentd.open_thing` runs `cards.check_opens` again, because the
card may have come from any process that can reach the socket. For a turn it answers "The details of turn N are not
kept." unless the turn is running or its log is among the last 50. The result of any other open comes back as a
`local` event with `action` `open`, and the line says what happened.

| `kind` | What opens (`launcher.open_thing`) |
|---|---|
| `unit` | the drawer, showing `systemctl status --no-pager -l` for the unit |
| `package` | the drawer, showing `pacman -Qi`, or `pacman -Si` when it is not installed |
| `url` | the browser panel, the way every other link opens |
| `path` that is a folder | the drawer, showing `ls -la -p` |
| `path` that is a text file | the drawer, showing the file |
| `path` that is another file | `xdg-open` |
| `path` that does not exist | the line says "<name> is not there." |

The value is passed as an argument, never as shell text. The drawer is a `foot` window with the app id
`bombadil-details`, run by `launcher.details`, which a Hyprland window rule sends to the `special:details` workspace
without taking focus. `_focus_drawer` waits up to 5 seconds for the window to map and then focuses it, so Esc closes
it. The line says "Showing ..." when the window was there, or when the viewer is still running after the 5 seconds;
it says "Could not open ..." when the viewer ended before any window appeared.

### What `system_map` captures

Every capture runs real commands with a fixed environment (`LC_ALL=C`, `NO_COLOR=1`, `TERM=dumb`, no stdin), side by
side in a worker pool. Each command has the capture's budget: 0.5 seconds (`BUDGET`), and anything that has not
answered by then counts as missing, so a picture always comes back at once, drawn from what could be read. Parsers
are pure functions of text. If nothing at all could be read, the capture raises `sysmap.Unavailable` with one plain
sentence ("Could not read the disks: lsblk did not answer.").

| `kind` | Reads | Budget | The picture |
|---|---|---|---|
| `network` | `ip -j route get 1.1.1.1`, `ip -j route show default`, three `nmcli` queries (devices, the Wi-Fi in use with its signal, connectivity; no rescan), `/etc/resolv.conf`, and a TCP connection to the active provider's host on port 443 (`api.anthropic.com` for Claude, `api.openai.com` for Codex) | 0.5 s, and up to 1.6 s (`PROBE_BUDGET`) for the provider connection when there is a route | `chain`: this machine, a VPN tunnel box when the route runs over a `wg`, `tun`, `tap`, `tailscale`, `ppp`, `vpn` or `zt` device, Wi-Fi (signal under 40 percent is `warn`) or cable, router (with the DNS servers as a note), internet, and the provider last. The first broken link is `bad` and lit, with one sentence in `say`. A captive portal is `warn` |
| `boot` | `systemd-analyze critical-chain --no-pager` and `systemd-analyze time --no-pager` | 4.0 s (`BOOT_BUDGET`): the boot record is slow to read and does not change | `timeline`: one row per unit on the critical chain, at most 12 (the slowest 11 and the last, in order), each with its start time and a bar for how long it took. The slowest unit is `warn` and lit when it took at least a second. Each row opens its unit. A live system has no boot record, and the answer says so |
| `service` | `systemctl show` for the unit (`target` required, `bluetooth` and `bluetooth.service` both work), then the states of what it `Requires` and `Wants` (at most 8, without the common noise such as `sysinit.target`), and `pacman -Qo` for the package that owns a unit file under `/usr/` | 0.5 s | `layers`: the unit on top, its dependencies below, each with its state (`active` ok, `activating` warn, `failed` bad; a required dependency that is failed or inactive is `bad` and lit). Each box opens its unit. The card carries `target` |
| `disks` | `lsblk -J -b` and `findmnt -b -J` | 0.5 s | `layers`: disks of at least 1 MB (no `zram`, `loop`, `ram` or floppy), their partitions and encrypted or logical volumes, with mount point, filesystem, size and how full. Amber from 90 percent, red from 97 percent; read-only filesystems such as `squashfs` and `iso9660` never warn. Each mounted box opens its mount point |
| `sound` | `pw-dump` (PipeWire) | 0.5 s | `layers`: up to 4 speakers (the ones playing and the default), each with its volume or "muted", and up to 6 playing apps per speaker linked to it. A muted speaker is `warn` |
| `screens` | `hyprctl -j monitors` | 0.5 s | `chain`, unlinked: the monitors left to right with mode, refresh rate and scale; the focused one is `active`, a disabled one `warn` |

`sysmap.capture(kind, target)` returns `{"card", "facts"}`. The card has `source` set to the kind. `facts` are the
plain values the card was made from, `{"key", "label", "value"}`, with `volatile: true` on values that move on their
own (a disk's used space); receipts compare them (see Receipts).

What the model never does: it does not capture, parse, lay out or caption anything. `apply_overrides` lets the agent
choose only `highlight` (ids that exist; an unknown id leaves the machine's own highlight) and `say` (at most 160
characters), and rebuilds the text twin. The picture, its boxes, its states and its default sentence are the machine's.
The same holds on both providers. The pictures need no model and work offline: with no connection at all the picture
is "This laptop" then "No network". "not answering" appears under the provider box when a link exists but the
provider host does not accept a connection, unless NetworkManager reports no or limited connectivity: then the
internet box is the broken one and the provider box has no line. A picture word asks no model and starts no turn (see
Picture words).

### Picture words

`launcher._picture` matches the typed text exactly, after it is lowercased, its spaces are folded and `. ! ? , ; :` are
stripped from its ends (a curly apostrophe counts as a straight one). Any other wording goes to the agent, which has
`system_map` for the same pictures.

| Picture | Phrases (`launcher.PICTURE_PHRASES`) |
|---|---|
| `network` | how am i connected, am i connected, am i online, how is my internet connected, network map, connection map |
| `boot` | what starts when i boot, what starts at boot, what starts on boot, what runs at boot, what runs when i boot, boot map |
| `disks` | where did my disk go, where did my space go, where did my disk space go, my disks, disk map, what disks do i have |
| `sound` | what's playing where, whats playing where, what is playing where, sound map, where is my sound going |
| `screens` | my screens, my monitors, screen map, what screens do i have |
| `service` | `what does [the] <unit> [service] need`, `depend on` or `require` instead of `need` (`launcher._NEEDS_RE`) |

For the service form the unit must exist (`sysmap.service_exists` asks `systemctl show -p LoadState` and wants
`loaded`); a name without a unit suffix gets `.service`. When the text is also the name or title of an installed app,
the app wins. `agentd.picture` captures the picture without a model, shows it, and says "Showing ..." in the line. It
also keeps a note (the typed words and the first 400 characters of the picture's text twin), and the next prompt to the
agent starts with `[Done by the user without you since your last turn: ...]`, so a follow-up such as "why is that red"
can be answered. `agentd` keeps the last 10 such notes, which it shares with the other things the person did without
the model. A picture that could not be read says why in the line and adds no note.

### Reasons and what was read (`narrate.py`)

`narrate.Narrator` follows one turn's provider events. Besides the live line (described in [the shell](shell.md)) it
keeps two records for the status line and Details. The `read` list is also written to `turns.jsonl` for later pieces;
no code on `main` reads that field from it (the brain is not on `main`), and Details takes its list from the turn's own
log.

**Reason (`because`).** The sentence the agent wrote just before it acted, kept instead of thrown away.

| Rule | Detail |
|---|---|
| Source | the agent's last text in the current model message (`message_start` clears it). A step in a message with no sentence before it has none |
| Fallback | the `description` of a Bash call |
| Never | thinking text. It can become the live line but is never a reason |
| Cleaning (`reason_from`) | code blocks and markdown dropped; the last sentence; filler openers (`ok`, `great`, `now`, `first`, `so`, ...) and lead phrases (`let me`, `i'll`, `i need to`, ...) removed, and a following verb turned into its `-ing` form ("Let me install ffmpeg" becomes "Installing ffmpeg"); trailing `!` dropped |
| Limit | at most `MAX_BECAUSE` (140) characters, cut at a word with `…`; shorter than 4 characters or without a letter is no reason |
| Carried by | the `status` event of the step (`because`), and the `tool` or `file_change` event, which `agentd` logs with it so Details can show it |

**What was read (`Read`).** A per-turn list of what the turn read, each marked yours or outside. It is sent in
`turn_end` as `read` and written to `turns.jsonl`.

| `kind` | Comes from | `outside` when |
|---|---|---|
| `web` | `WebFetch`, `curl`, `wget`, `aria2c`, `xh`, `http`, `https`, the URL of a `git clone` | the host is not `localhost`, `127.0.0.1`, `::1` or `0.0.0.0`. The label is host and path only, never a credential or query |
| `file` | `Read`, `NotebookRead`, and `cat`, `less`, `more`, `head`, `tail`, `bat`, `zcat`, `xxd`, `hexdump` (up to 3 files) | the file's `user.xdg.origin.url` extended attribute names a host (a download); `origin` then holds that host |
| `search` | `WebSearch` (the query, at most 50 characters) | always |
| `screen` | the `[Screen]` block the "this" chip adds to a prompt | it holds a page address or a selection |
| `session`, `app` | a prompt marked `[asked by <who>, untrusted]` | always |

The list keeps the last 50 reads (`MAX_READS`), and a repeat moves to the end. **`after`** is the latest outside read,
attached only to a step that carries a mark (`system` or `irreversible`), as `{"label", "kind", "text"}` with text such
as "after reading wireguard.com/quickstart", "after searching the web for ...", "after reading the page on screen" or
"after a request from builder". It says what came first, not what caused the step: the order is known, the cause is not.

**Where they show.**

| Surface | Behaviour |
|---|---|
| `StatusLine.qml`, `because` | a quiet second line (muted, at most two lines) while a turn runs, shown when the pointer rests on the line, and always on a marked step |
| `StatusLine.qml`, `after` | an amber caption, only on a marked step |
| A bare `why` typed while a turn runs | `launcher.WHY_WORDS` matches it (plain ASCII only, only while busy); `agentd.local` answers from `Narrator.why_text` with no model and no turn, and the bar flashes it for 8 seconds (answers below). At any other time the word goes to the agent |
| Details (`watch.py`) | `why: ...` under the step's command, the `after` text in amber, and at the end a `read` block with `yours` and `outside` lines |

What a bare `why` answers (`Narrator.why_text`, and `agentd.local` before it):

| State | Answer |
|---|---|
| The step has a reason, is marked (`system` or `irreversible`) and followed an outside read | `<because> (<after text>)` |
| The step has a reason, without both of those | the reason alone |
| The step has none | "It did not say why for this step: ..." and the step's words up to the first comma |
| No step has started | "Nothing has started yet." |
| The turn has no narrator yet (`agentd`) | "Nothing is running." |

**What a step touched, for receipts.** `parts_touched` (and `parts_command`, `parts_changes` for Codex's file
changes) classify a step into `network`, `service` (with the unit), `sound`, `screens` or `disks`. The rules are
tables of commands, paths and units in `_touch_segment`, `_touch_path` and `_touch_unit`: for example `nmcli
connect`, `ip route add`, `wg set`, `resolvectl dns`, a write to `/etc/NetworkManager/` or `/etc/resolv.conf` for the
network; `systemctl restart` or a unit file under `systemd/system` for a service; `wpctl set-*`, `pactl set-*` for
sound; `hyprctl keyword monitor` or `wlr-randr` for screens; `mount` with arguments, `mkfs*`, `udisksctl mount` or an
`/etc/fstab` write for disks. Reading commands (a bare `mount`, `fdisk -l`, `pamixer --get-volume`) touch nothing. `Narrator.drew` is set when
the agent called `show_card` or `system_map` in the turn.

### Receipts

A receipt is a `compare` card, titled with the part's picture title and ", before and after", of what a turn changed
in a part of the machine it touched.

1. At `turn()` start, unless the turn is a typed `!command` or `explain` is `brief`, `agentd` starts
   `sysmap.snapshot` for `BEFORE_KINDS` (`network`, `disks`, `sound`, `screens`) in the background. Nobody waits for
   it. A part that cannot be read here (no Hyprland, no PipeWire) is `None`.
2. As each tool step is read, `agentd` starts `sysmap.snapshot_service` for every `service` the step touches, before
   the step has run (at most 3 units per turn).
3. After `turn_end` and the closing line, `_receipt` runs in the background. It does nothing when there is no snapshot,
   when `Narrator.drew` is set (the agent drew its own picture), or when the turn touched none of these parts. It waits
   up to `BUDGET + 1` seconds for the first snapshot, captures the touched kinds again, and compares each in the order
   the turn touched them. The first part that changed wins; if none did, up to two services are compared (a service
   caught while `activating` or `reloading` is not a valid before).
4. `sysmap.receipt` compares only facts without `volatile`, by `key`: added, removed or changed values. It returns
   `None` when nothing changed, so a signal, a latency or a used-space figure moving is never a change. For at most 6
   changed keys it draws a `before` box (`gone`) and an `after` box (`new`), paired on one row per key, lights the
   after boxes, sets `receipt: true` and `source`, and says "N more changed." in `say` when there are more.
5. The card is shown only when no other turn has started (`_closed_turn == turn`). It carries the turn's number, so it
   is logged with that turn.

Because only the parts a turn's own steps touched are compared, a change in a part the turn did not touch (a Wi-Fi drop
during a turn that only edited files) is never in its receipt. A turn that touched the network is compared on all of the
network's non-volatile facts (the link, the router, the DNS servers, a VPN tunnel), so a drop during such a turn can
appear. At most one receipt card is shown per turn. `explain` is a key in `config.toml` read when `agentd` starts:
`brief` turns receipts off, `normal` (the default) leaves them on, `teach` is accepted and behaves like `normal`.

### The details drawer and `bombadil view`

The drawer is where text goes when a picture is not the right answer. Two things open in it: a turn's own log
(`bombadil watch`, started by `agentd.details`, listing each step with its command, output, `why:` and `after`, then
what was read, and each picture shown in words as a `picture` block, labelled "what changed" for a receipt) and
whatever a box opened (`bombadil view`).

`bombadil view` (`bin/bombadil` calls `pager.view`) shows `--file PATH` (the first 2,000,000 bytes, with a note when the
rest is cut) or the output of `-- COMMAND ARG...` (run with a 15 second limit, errors mixed in). A missing program
says "<name> is not installed.", a silent one "<name> did not answer in time.", and no output "Nothing to show." With a
terminal, text that fits is printed whole, followed by "Press Esc to close.", and any key closes it. Longer text takes the
drawer's own screen with a window of rows and a footer such as `11-19 of 50 · Esc closes`. Esc alone (or `q`, Ctrl-C,
Ctrl-D) closes it; the arrows, PageUp and PageDown, Home and End, the space bar, `f`, `b`, `j`, `k`, `g` and `G` scroll.
The text is laid out again at the next key press after the drawer was resized. Keys are read from `/dev/tty`, so text may be piped in; with no
terminal at all the text is printed. It is a small module rather than `less`; its docstring gives the reason: `less`
cannot leave on Esc, and it was not on the image.

### Failure behaviour

| What goes wrong | What happens |
|---|---|
| `show_card` input is invalid | The tool returns an error result (`isError`), and its text is "ValueError: The picture was not drawn:" followed by one line per fix. The server stays up |
| `kind` is not `diagram` | "kind '...' is not drawn yet: only diagram is" |
| `agentd` is not running | The tool returns the card's text with "(agentd is not running, so nothing could be drawn.)" |
| `agentd` does not answer within 2 seconds | "(agentd did not answer, so it may not have been drawn.)" |
| No other client is connected | `card_ack` has `shown: false`, and the tool adds "(No screen is showing it: here it is in words.)" |
| `agentd` rejects the card on its second check | `card_ack` carries `errors`, and the tool passes them on |
| A capture cannot read anything | `system_map` returns the one-sentence reason as its result. A picture word shows it as the line and puts the old card away |
| A command is slow or missing | It counts as missing and the picture is drawn from the rest |
| `CardHost.qml` cannot load | The `Loader` is not ready, the host stays invisible, and the bar is unaffected |
| The shell gets an odd card | `_takeCard` ignores anything that is not a `diagram` object, and a message that is not valid JSON is dropped by `shell.qml` |
| A click names something that cannot be opened | `agentd` says why in one line ("Cannot open that: ...", "<name> is not there.", "Could not open ...") and the card stays |
| `narrate.py` raises on an odd event | `agentd` logs it and drops that line, never the turn |
| The streaming follower raises | `agentd` writes to stderr, and the card is drawn when whole |

## Interfaces other pieces depend on

The socket protocol as a whole is in [agentd](agentd.md), and the tool registry in [os-mcp](os-mcp.md). This table
lists what this piece owns.

| Name | Kind | Detail |
|---|---|---|
| `show_card` | os-mcp tool | `shape`, `title`, `nodes` required; `kind`, `links`, `highlight`, `say`, `linked`. Returns the text twin and "(shown above the bar)" or why not |
| `system_map` | os-mcp tool | `kind` required (`network`, `boot`, `service`, `disks`, `sound`, `screens`); `target` for `service`; `highlight`, `say` optional |
| `{"type":"card","card":{...}}` | socket message, client to `agentd` | answered with `{"type":"card_ack","shown":bool}`, or `{"type":"card_ack","shown":false,"errors":[...]}` |
| `{"type":"open","kind","value"}` | socket message | what a box names; answered with a `local` event, `action` `open` |
| `{"type":"details","turn":n}` | socket message | the turn's log in the drawer; again while it shows closes it |
| `{"type":"close_details"}` | socket message | puts the drawer away (Esc in the pill) |
| `card` | event | `{"card": {...}, "turn": n or null}`. A finished card, a draft (`partial: true`), or `{"id", "gone": true}`. `turn` is null for a picture word, the running turn for a card sent during it, the closed turn for a receipt |
| `local` | event | `action` `picture` (`phase` `start` and `done`, `ok`, `text`), `open`, `why` |
| `status` | event | may carry `because` (string) and `after` (`{"label","kind","text"}`) |
| `tool`, `file_change` | events | carry the same `because` and `after`, for Details |
| `turn_end` | event | carries `read`: `[{"label","kind","outside","origin"?}]` |
| `explain` | config key | `brief`, `normal` or `teach` in `/etc/bombadil/config.toml` or `~/.config/bombadil/config.toml` |
| `BOMBADIL_PROVIDER` | environment | the provider whose host the network picture ends with; `cardtools.provider_name` reads it, then the config, then `claude` |
| `BOMBADIL_SOCKET` | environment | the `agentd` socket `cardtools.deliver` connects to |
| `bombadil view --file PATH`, `bombadil view -- CMD...` | commands | the drawer's viewer |
| `Diagram` (`import Bombadil`) | kit component | `spec`, `showTitle`, `minBoxWidth`, `maxBoxWidth`; signals `opened(target)` and `picked(node)`. Also used by apps, see [the app kit](app-kit.md); documented in `share/skills/bombadil-apps/references/components.md` |
| `PillState.card`, `dismissCard()`, `openThing(target)` | shell state | what `CardHost.qml` reads and calls |
| `cards.validate_diagram`, `cards.accept`, `cards.text_of`, `cards.check_opens` | Python | the data contract |
| `sysmap.capture`, `sysmap.snapshot`, `sysmap.receipt`, `sysmap.apply_overrides` | Python | captures and receipts |
| `narrate.Narrator` | Python | `step_notes`, `why_text`, `read_list`, `parts`, `drew` |

## Where state lives

| What | Where |
|---|---|
| The card on screen | memory, in `PillState.card`. Nothing stores it: a picture that has gone is made again by asking again, and Details lists it in words |
| Open drafts, the card counter, snapshots, `Narrator`, the notes for the next prompt | `agentd` memory (`_stream_ids`, `_card_seq`, `_befores`, `narrator`, `notes`). The last 50 turns' log paths are in `turn_logs` |
| The turn's event log | `~/.local/state/bombadil/turns/<milliseconds>-<turn>.jsonl` (`BOMBADIL_STATE` overrides the directory). Finished cards with a turn, receipts included, are logged. Drafts are not. `tool` events carry `because` and `after` |
| One line per turn | `~/.local/state/bombadil/turns.jsonl`, with the `read` list and the path of the turn's log. A picture word adds a `local` line there |
| The socket | `$XDG_RUNTIME_DIR/bombadil/agentd.sock` (`BOMBADIL_SOCKET`) |
| `explain` | `/etc/bombadil/config.toml`, overridden by `~/.config/bombadil/config.toml` |
| The picture's component and icons | `share/qml/Bombadil/Diagram.qml`, its line in `share/qml/Bombadil/qmldir`, 78 icons in `share/qml/Bombadil/icons/` |
| The drawer window rule | `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` (`panel-details`, class `bombadil-details`) |

There is no database. The agent is told to use pictures by two clauses of `providers.system_prompt`: say in one
sentence why before a step that changes the machine, and show an answer with parts, order or change as a picture
(`system_map` for this machine, `show_card` otherwise), then say one line.

## Principles it keeps

- [The person is a passenger](../principles.md#the-person-is-a-passenger): the reason and the picture arrive while
  the agent works, and nobody has to ask for them. A marked step shows its `because` and `after` without a hover, while
  Esc is still in reach. The trap is making an explanation something the person must open, request or configure.
- [Records before models](../principles.md#records-before-models): a picture of the machine comes from a command's
  output through a pure parser, and a reason is the agent's own earlier sentence, cleaned. The trap is asking a model
  to summarise, redraw or caption a picture, or to explain a step afterwards. It would cost time on every turn and
  could disagree with the first answer. The `show_card` description tells the agent never to draw machine state from
  memory; keep that sentence true when you edit it.
- [The answer is the thing](../principles.md#the-answer-is-the-thing): a card is the answer. It has parts, it opens
  what it names, and the agent adds one line (`say` is limited to 160 characters). The trap is a card that only links
  to a text window, or a tool result that invites a paragraph. The result is the text twin so the agent has nothing to
  describe.
- [Quiet at rest](../principles.md#quiet-at-rest): one card at a time, replaced, and gone on Esc, on the close mark
  or at the next turn. A receipt appears only when a part the turn touched really changed, and a value that moves on its
  own is never a change. The trap is a card that outlives its turn, or a receipt for something the turn did not do.
- [One design language](../principles.md#one-design-language): every picture is drawn by the kit's `Diagram.qml` from
  `Theme` tokens, in four fixed shapes with a deterministic layout, and a box shows `warn`, `bad`, `new` and `gone` by
  colour and by a mark. The trap is a second drawing component for one card, a force layout, or a meaning carried by
  colour alone; a `timeline` row and an `active` box already do (see Known gaps).
- [Degrade and recover](../principles.md#degrade-and-recover): each capture has a budget and returns what answered, an
  unreadable part is one plain sentence, and the card host loads through a `Loader` so a picture that will not draw
  costs the pictures, never the bar. The trap is a capture that waits on a slow command past its budget, work that can
  throw inside the bar, or a picture that needs a model to exist.

## Extending it

There is no card-type registry and no subject registry. These steps follow how the existing ones are wired.

### Add a `system_map` subject

1. In `sysmap.py`, write `capture_<name>(run_, budget)` after `capture_screens`. Run each command through `run_` with
   the budget (several side by side with `_gather`, one with `_within`), parse the text with a pure function, and
   raise `Unavailable("one plain sentence")` when nothing could be read. Finish with `_result("<name>", spec, facts)`:
   it validates the card and sets `source`. Mark facts that move on their own with `"volatile": True`.
2. Add the name to `KINDS` (the tool's `enum` is built from it in `cardtools.system_map`), add a `TITLES` entry, and add
   a branch to `capture()`. Pass a bigger budget there only for something slow by nature, as `boot` does.
3. Add the name to `cards.SOURCES`. Without it `cards.accept` drops `source`, and the shell labels the picture "drawn
   by the agent".
4. Add the live-line words to `narrate._MAP_WORDS` (the existing ones read "Drawing your disks"). Without an entry the
   line says "Drawing a picture of the machine".
5. For a typed phrase, add it to `launcher.PICTURE_PHRASES` and `PICTURE_TITLES`. `agentd.picture` needs no change.
6. For receipts, add the name to `sysmap.BEFORE_KINDS` and teach `narrate` which steps touch it (see [Add a `narrate` rule](#add-a-narrate-rule)).
7. Update the `SYSTEM_MAP` description in `cardtools.py`. `tests/test_cardtools.py` keeps the listings of the two
   tools (descriptions and schemas together) under 6500 characters.
8. Tests: a fixture of real captured output for the parser in `tests/test_sysmap.py`, a picture-word case in
   `tests/test_launcher.py`, and the subject in the `for kind in ...` loop of
   `iso/airootfs/usr/local/bin/bombadil-smoke`, which logs how long each capture took.

### Add a card type

`diagram` is the only type, and three places assume it, so a second type touches all of them.

1. In `cards.py`, write a validator next to `validate_diagram` that returns the card and a `text` twin, and make
   `accept` call it by `card["type"]`. `accept` calls `validate_diagram` for every card.
2. In `cardtools.py`, add the kind to the `enum` of `show_card`'s `kind` and its properties to the schema, and branch on
   `kind` in `show_card`. It has one flat schema and raises for any `kind` but `diagram`. `deliver`, `_card_message`
   and `_show` carry any card unchanged.
3. In `shell/PillState.qml`, let `_takeCard` accept the type (it returns for anything but `diagram`). In
   `shell/CardHost.qml`, add a face for it next to the `Diagram` and choose by `card.type`. Put a reusable drawing
   component in the kit (a file in `share/qml/Bombadil/`, a line in `qmldir`, a section in
   `share/skills/bombadil-apps/references/components.md`), as `Diagram` is, so apps can use it too.
4. Keep `text` on the card: `watch.py` lists each picture in words from it. If the type can stream, extend
   `CardStream` and `partial_diagram`, which follow diagrams only.
5. Controls that act on the machine, from the UX brief, have no registration point.
6. Tests: the validator in `tests/test_cards.py`, the tool in `tests/test_cardtools.py`, the shell in
   `tests/test_pill_qml.py`, and the component with a test like `tests/test_diagram_qml.py`.

### Add a diagram shape

1. Add the name to `cards.SHAPES` (the tool's `enum` follows it) and any extra rule to `validate_diagram`.
2. In `cards.text_of`, add a branch for the shape, and put the shape in the tuple on the `body =` line (`("layers",
   "timeline", "compare")`): those are the shapes whose lines are joined with newlines, and a shape left out has its
   lines run together.
3. In `Diagram.qml`, add a branch in `_layout` and the drawing for it. Check each shape test that exists there: the
   box `Repeater` draws every shape except `timeline`, and links are drawn only for `chain` and `layers` (`_svg`, the
   `Shape` and the link `Repeater`). Document the shape in `share/skills/bombadil-apps/references/components.md`. Keep
   the layout deterministic.
4. Tests: the validator and the text twin in `tests/test_cards.py`, the drawing in `tests/test_diagram_qml.py`.

### Add a `narrate` rule

1. A step that touches a part of the machine (so a receipt compares it): add a branch to `_touch_segment` for a
   command, a pattern to `_PATH_KINDS` for a path, or an entry to `_NET_UNIT` or `_AUDIO_UNIT` for a unit. The kind
   must be one `sysmap.capture` can draw and `BEFORE_KINDS` must list it. Cover it in `tests/test_narrate.py` and, for
   the receipt, `tests/test_agentd.py`.
2. Another source the turn reads: add a program to `_READ_PROGS` or a branch to `command_reads` for a shell command, a
   branch to `tool_reads` for a tool, or a marker to `prompt_reads` for something in the prompt. Build a
   `Read(label, kind, outside)`. If it is a `kind` that does not exist yet, give `Read.after` its text, or it says "after reading <label>".
3. A reason that comes out wrong: tune `_FILLER_RE`, `_LEAD_RE` (openers) or `_VERBS` (which verbs become `-ing`) and add
   the sentence to the `reason_from` cases in `tests/test_narrate.py`.
4. The live line for an os-mcp tool: add a branch to `_os_tool` (and `partial_step` if the call streams).

## Tests

| File | Covers |
|---|---|
| `tests/test_cards.py` | validation limits and errors, `opens`, `layers` ranking, text twins, `accept`, `partial_diagram` and `CardStream` |
| `tests/test_cardtools.py` | the two tools' inputs and results, delivery and every failure note, a card crossing a real socket |
| `tests/test_sysmap.py` | each parser on captured output, each capture's card and errors, budgets, `receipt` |
| `tests/test_narrate.py` | step words, `reason_from`, reads and their origin, parts touched |
| `tests/test_diagram_qml.py` | `Diagram.qml` offscreen: layouts, states, `opened` and `picked`, the drafts |
| `tests/test_pager.py` | layout, scrolling, keys, a real terminal run |
| `tests/test_agentd.py` | a card reaching every bar, `card_ack`, streaming and taking a draft back, picture words, receipts, clicks, `why` |
| `tests/test_launcher.py` | picture phrases, the drawer and what each `opens.kind` runs |
| `tests/test_pill_qml.py` | the card lifecycle in the shell, hover and fade, `because` and `after` on the line |
| `tests/test_watch.py` | `why:`, `after` and pictures in Details |

```sh
pytest tests/test_cards.py tests/test_cardtools.py tests/test_sysmap.py tests/test_narrate.py \
       tests/test_pager.py tests/test_diagram_qml.py
```

The async tests need `pytest-asyncio` and the QML tests need PySide6 (they skip without it); see
[development](../contributing/development.md). On 2026-10-01 these six files gave 389 passed. The VM smoke script
`bombadil-smoke` calls `system_map` for every kind through `bombadil-os-mcp`, expects "shown above the bar", and logs
the time of each capture; a capture over 500 ms on real hardware is a finding, not a failure. It also sends `open`
for a unit and checks that the drawer window appears with the keyboard and that Esc closes it.

## Known gaps

- `weight` accepts `inf`: `cards.py` keeps it, and `json.dumps` writes the card with `Infinity`, which is not valid
  JSON. `shell/shell.qml` drops a message it cannot parse without a word, so the picture would not draw. The Python
  side was reproduced; the shell side was read, not run.
- A `timeline` row shows its state by colour alone (a dot, a bar and a coloured `sub`; `gone` is also struck through),
  and an `active` box has no mark, although the header comment of `Diagram.qml` says nothing depends on colour alone
  (`Diagram.qml`, `_mark`). The text twin does carry `[slow or weak]`, `[broken]`, `[new]` and `[gone]` in every shape.
- A node's `volatile` is stored and never read, and `accept` keeps a card's `target` and nothing reads it
  (`cards.py`, `shell/`, `agentd.py`). Receipts use the `volatile` flag on `sysmap` facts instead.
- The `show_card` errors start with the class name, `ValueError:`, because the tool raises `ValueError` and the server
  prints its class (`cardtools.py`, `mcp_server.py`). [os-mcp](os-mcp.md) lists it too.
- `card_ack.shown` is true when a client besides the sender is connected (`agentd._card_message`), not when a bar drew
  the card.
- Only the first changed part makes a receipt (`agentd._receipt` stops at the first), so one turn that changes the
  network and the disks shows one before and after.
- The design keeps both snapshots beside the turn's log; the code keeps them in memory and logs only the receipt card.
- Only `diagram` exists. `show_card` refuses other kinds and `_takeCard` ignores them. List, checklist, timer, control and
  reader cards, and cards the agent writes in QML, are designed only.
- `explain = "teach"` is accepted by `config.py` and nothing reads it. No launcher word sets `explain`, so it changes only
  by editing `config.toml`.
- Streaming is Claude only, and a draft has no links, so a `layers` draft is one row (`providers.py`, `cards.py`).
- `pager.py` and its footer say the wheel scrolls, but the module reads only keys; the wheel works only where the
  terminal turns it into arrow keys in the alternate screen. That was not checked.

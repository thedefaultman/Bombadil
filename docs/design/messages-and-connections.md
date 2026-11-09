# Messages and connections: what is for you, one line, one card, one press

An hour after you pressed Send on a mail, a message from Priya arrives in Slack. One line rises above the
pill: "Priya Shah in #launch: Legal just signed off. Can we lock the 14th…" with Reply and Open. You press
Reply and a card opens with the last few messages of the thread above and a reply box below. You say "yes
lock it, tell her to go ahead", and the words appear in the box with an orange ring on Send and the label
"Yours: Send". You read them, press Send, and the line says "Posted in #launch · 11:04" with a link that
opens the message in Slack. At no point did anything leave the machine that you had not seen, and the one
press that let it go was yours.

Every person, workspace and company in this page is invented.

This is the second piece of "Bombadil at work" (the first is [Mail](mail-view.md)). It gives every chat and
work tool the same three things mail got: one place to see what is for you, a card with a box that holds a
draft, and a press that is yours. It also adds the part mail did not need: somewhere safe to keep the keys to
those services. How it is built is [`../CONNECT.md`](../CONNECT.md) (the contract).

## The line

Mail drew the line (Bombadil's own views for what you glance at or answer in a sentence, the browser panel for
work inside a tool's own page, a program only where it beats the site) and this piece keeps to it.

- **A message to you** (a direct message, a mention, a thread you are in) is a card with a reply box.
- **A thing that waits on you in a work tool** (a ticket or a task assigned to you; Notion has no such list yet, it
  only takes the pages and comments you press for) is a card with its fields, and "make a ticket for Leo about the empty state, due Thursday" fills one whose button is
  yours.
- **Anything else a web page tells you** (a Teams message, a calendar reminder, a shop's notice) is the same
  line, built from the notification the page sent.
- **Writing inside the tool's own page** stays in the browser panel. The browser service that arrives with
  this piece only reads, points and takes a value off a page; it has no hands.

## The rules

1. **The press is yours.** A connection can read and a card can draft; nothing posts, replies, creates or
   comments until you press the card's button. The agent has `propose` and no tool that sends. Typing "send
   it" answers "Sending is yours. It's under the pointer." (the same sentence as mail).
2. **Reading is quiet.** A connection never marks anything read on the service, never joins a channel, never
   reacts. "Unread" is Bombadil's own note of when you last looked.
3. **The keys are not the agent's.** The agent runs as you, with a shell, so a key anywhere you can read is a
   key it can read. Every token lives with `bombadil-connect`, a service that runs as its own system user in a
   directory only that user can open. No tool returns a token, no card shows one, no log writes one. (Plainly:
   the image still gives its user passwordless `sudo`, so the agent's own shell could read that directory. The
   directory keeps the keys from every other program of yours, and becomes a wall against the agent when it loses
   `sudo`, which is the hardening order below.)
4. **Bombadil keeps no copy of other people's words.** Messages sit in the memory of the connection service
   (at most 500 of them, none older than 14 days) and are gone when it stops. What is written down is which
   services are connected, their keys, a time per conversation, and which presses were already performed.
   Slack's terms forbid keeping or indexing a workspace's messages outside it; this keeps none.
5. **Other people's words are read, never obeyed.** A message, a task's title and a notification reach the
   model inside a mark that says they are other people's words, with invisible and direction-changing
   characters removed (including the characters that draw as nothing and can spell a sentence: the tag block and
   the variation selectors) and every line of a message quoted, so a line in a message that looks like the head
   of another cannot pass for one. The same goes for what you send: a draft holds nothing that draws as nothing,
   so what you read in the box is all of what goes. A turn that has read any of them is remembered, and what it
   proposes is held to what you typed: the card says so, and flags a place you have never received a message from.
6. **Every answer points at its source.** A card's "Open" opens the message or the ticket on the service's own
   page; a receipt opens what was posted.

## Why these connections, this way

- **Slack through a small private app in your own workspace.** An app shared outside the Slack Marketplace is
  held to one history request a minute and fifteen messages, and gets no search. An app that belongs to your
  own workspace keeps normal limits and can be sent new messages the moment they arrive (Socket Mode, so
  nothing listens on your computer and no address is published). A member can make one alone on every plan,
  unless the workspace turned on app approval; then Slack says so on its own page, Bombadil says it once, and
  Slack messages come from Slack's notifications instead.
- **Work tools through their own MCP servers.** Notion, Linear, Atlassian's Jira, Todoist and ClickUp each run
  an official server a person can allow alone with a browser sign-in. One driver speaks to all of them, from a
  small recipe per service (a file, and a name in four lists, for a fork that adds one), so each consent page says "Bombadil" and the keys stay in
  the service, never in the agent's own tool list.
- **Teams stays in its page.** Reading Teams through Microsoft's API needs an administrator in the default
  setup. Teams' own notifications carry a sender and a preview, which is enough for a line; the card's button
  then says "Copy and open" (it copies your words, opens the chat, and you press Enter there), because in that
  page the press is the page's own.

## What the person sees

- **The line.** A message that is for you is one line above the pill, from the sender, with Reply and Open. A
  second message in the same conversation replaces the first. A connection that stopped working is one line,
  once. A receipt is one line with a link.
- **The message card.** The last few messages of the thread (the unread ones marked), the reply box, any
  warning plain on the card ("You have never received a message from #random", "This reaches everyone in the
  channel"), one button. While the box holds exactly what would be sent the button has the orange ring and
  "Yours: Send"; change a word and the ring goes until the card has drawn it again. After the press the card
  says what happened, or, if it cannot tell ("I can't tell whether that went. Look in Slack before you press
  again."), says that and offers the button again only after.
- **The task card.** The ticket's title, the service, why it is yours, and its fields as boxes you can change;
  one button, "Create" or "Comment", with the same ring and the same rules.
- **"What did I miss?"** A list card of the unread messages to you, each opening its message card.
- **"Connected here".** A list card with each mail account, the Slack app and each tool: what it reads, its
  state, and Disconnect. A service not yet connected is a quiet row with Connect. Say "connections".
- **Connecting Slack.** You say "connect slack". The panel slides in on Slack's own page to make an app from
  Bombadil's ready manifest. An orange ring and a pointer with a label ("Yours: Create") sit on each button
  you press; Bombadil reads the two keys off the page and hands them to the connection service, never through
  the model. If the workspace needs an administrator, Slack's page says so and Bombadil says it once.
- **Connecting a work tool.** You say "connect linear". Linear's own page opens, you sign in and press Allow
  on the page that names Bombadil, and the line says "Linear is connected."
- **Notifications.** Quickshell's notification server takes over from mako, so a web page's notification is
  the same line (sender, preview, Reply, Open) and nothing else puts a box on the screen.

## What the agent can do

Five tools in the OS server: `messages_unread`, `message_thread`, `tasks_waiting`, `connections` and `propose`.
The first four read and answer in text inside the other-people's-words mark; `propose` puts a draft on a card.
There is no tool that sends, posts, replies, creates or comments, and no tool that returns a key. The skill at
`share/skills/bombadil-connect/SKILL.md` says the same to the agent in plain words.

## What is a rule, not yet a wall

As for mail, what keeps "never" true is code that checks where a press comes from (agentd refuses one from a
process inside an agent's turn or a coding session's scope, or from one it cannot name) and what is being pressed (the fingerprint of the
kind, the target and the content, worked out again by the connection service). A process of yours that is
outside every turn could still ask the service to perform something, which is why the service checks the
fingerprint, performs a proposal at most once, and never retries. The keys are in a store only the service's
own user can open, which is a wall against everything of yours except `sudo`. The hardening order, which later
pieces carry out: the agent loses `sudo`, agentd accepts presses only from the shell's own process, and then
both of these are walls.

The other doors a turn's process could try are closed the same way, by a check and not by a wall: the browser
service answers no peer inside a turn (so a turn cannot have it open a page of its own choosing and take a token
from it into a connection), the words that start a connection are not taken from a process of a turn, and typing
"send it" is answered with where the button is whenever a reply or a ticket waits on a card, as it is for mail. A
machine where turns run without a systemd scope (the probe at start did not work) cannot tell a turn's process from
yours on any of these sockets; agentd says so once in its log when systemd is there and the probe failed.

## What changes in the rest of Bombadil

- **Three card kinds** beside the diagram: message, task and list, checked by agentd and by the shell.
- **agentd** gains proposals (what waits for a press), the press path for three more kinds, the five tools and
  the notices from connections and notifications.
- **The image** gains `python-mcp` and `python-httpx` (`wl-clipboard` was already there), loses `mako`, and gains the system unit
  `bombadil-connect` (its own user, `ProtectSystem=strict`, `ProtectHome`, no new privileges) and the user unit
  `bombadil-browserd`.
- **Reserved words.** `slack`, `messages`, `unread` and `connections` open the cards without a model.

## What is checked and what is not

**Checked, against fakes and against a real shell.**

- Each part on its own: the connection service and its token store, the Slack driver against a fake Slack (the Web
  API and Socket Mode), the work-tool driver against a fake server and a fake sign-in written with the same SDK, the
  browser service and the Slack setup against a real headless Chromium showing pages that imitate Slack's, the
  agent's five tools, proposals and the press, and the three card kinds drawn offscreen.
- The service and agentd together, with nothing stubbed between them: a message becomes a line, Reply opens a card, the
  draft lights only when it is what the card drew, one press performs once, a box that changed after it was drawn goes
  nowhere, and a press from inside an agent's turn is refused by agentd and, on its own, by the service.
- A real Quickshell in a headless compositor, with a virtual pointer and real keys: the bar's own notification server
  owns the bus name and turns a page's notification into the line with Reply and Open; a click on Reply and the keys
  that follow reach the card's box; the ring on Send lights only when agentd has the draft; a click on Send is one
  receipt, and a second click sends nothing; Copy and open puts the typed words on the clipboard; the words `unread`
  and `connections` open their cards; a task card draws; the shell logs no QML error.
- Looking at the real shell found what the offscreen tests had not: the words of a reply that had been sent turned up
  in the box of the next card; a service that was not connected yet was drawn struck out, as if it had been removed;
  a task said its status and why twice; and a ticket the agent drafted could never light its button, because the card
  drew an empty box for every field the draft did not name and so was never "exactly what the draft holds". Each is
  fixed and has a test.

**Not checked.** Nothing has run against a real Slack workspace, Notion, Linear, Jira, Todoist or ClickUp: the
sign-in pages, the work tools' tool names and answer shapes (the recipes are marked unverified), Slack's real pages
under the setup recipe, and the services' rate limits are all still guesses, and the first real account will say which.
Not run either: a real Hyprland (keyboard focus for the card's box and the line behaves differently there than in the
headless compositor), a browser's own notifications on the bus, the system unit under a real systemd, the Arch image
with the new packages, a real agent turn using the new tools (and whether both CLIs accept the one tool whose content
may be text or an object). The token store is a rule with a check, not yet a wall, for the reason above.

## Building on it

- A **new work tool** is a recipe file in `share/connect/recipes/`: where its server is, what to ask for, which
  tool lists what waits on you, which tools create an item and add a comment; and its name in the four lists
  that carry a service's name and what it offers (`protocol.SERVICES`, `cardmake.SERVICE_TITLES`,
  `cardmake.OFFERS`, `service.SERVICE_NAMES`). No other code.
- A **new chat service** is a driver (`connect/driver.py` is the interface): what it reads, what a message for
  you is, and one `perform`. It also has to be told about: the service's registry of kinds (`service.REGISTRY`),
  the form of a connection id, `driver.KINDS`, and the places that accept a reply only for the Slack kind
  (`tools.py`, `watch.py`). The card, the press and the receipt are shared.
- A **new card kind that can send** is an entry in the outbox's list of kinds, never a second way to send.

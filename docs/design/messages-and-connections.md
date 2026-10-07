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
- **A thing that waits on you in a work tool** (a ticket assigned to you, a page that mentions you) is a card
  with its fields, and "make a ticket for Leo about the empty state, due Thursday" fills one whose button is
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
   characters removed. A turn that has read any of them is remembered, and what it proposes is held to what
   you typed: the card says so, and flags a place you have never received a message from.
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
  small recipe per service (a file a fork can add), so each consent page says "Bombadil" and the keys stay in
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
process inside an agent's turn, or from one it cannot name) and what is being pressed (the fingerprint of the
kind, the target and the content, worked out again by the connection service). A process of yours that is
outside every turn could still ask the service to perform something, which is why the service checks the
fingerprint, performs a proposal at most once, and never retries. The keys are in a store only the service's
own user can open, which is a wall against everything of yours except `sudo`. The hardening order, which later
pieces carry out: the agent loses `sudo`, agentd accepts presses only from the shell's own process, and then
both of these are walls.

## What changes in the rest of Bombadil

- **Three card kinds** beside the diagram: message, task and list, checked by agentd and by the shell.
- **agentd** gains proposals (what waits for a press), the press path for three more kinds, the five tools and
  the notices from connections and notifications.
- **The image** gains `python-mcp`, `python-httpx` and `wl-clipboard`, loses `mako`, and gains the system unit
  `bombadil-connect` (its own user, `ProtectSystem=strict`, `ProtectHome`, no new privileges) and the user unit
  `bombadil-browserd`.
- **Reserved words.** `slack`, `messages`, `unread` and `connections` open the cards without a model.

## What is checked and what is not

(Filled in when the piece is verified.)

## Building on it

- A **new work tool** is a recipe file in `share/connect/recipes/`: where its server is, what to ask for, which
  tool lists what waits on you, which tools create an item and add a comment. No code.
- A **new chat service** is a driver (`connect/driver.py` is the interface): what it reads, what a message for
  you is, and one `perform`. Nothing else changes: the card, the press and the receipt are shared.
- A **new card kind that can send** is an entry in the outbox's list of kinds, never a second way to send.

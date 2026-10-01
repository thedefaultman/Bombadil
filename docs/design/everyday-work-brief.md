# Bombadil at work

> **Status:** In progress
> **Code:** none on `main` for the pieces below; they build on `src/bombadil/appkit/`, `shell/CardHost.qml`, `src/bombadil/cards.py`, `src/bombadil/launcher.py`, `src/bombadil/mcp_server.py` and `src/bombadil/browser.py`
> **Pieces:** piece 1 (Mail) is in progress: a mail service, the agent's mail tools, the outbox and a Thunderbird lab exist on an unmerged branch (`src/bombadil/mail/`, `src/bombadil/outbox.py`, `src/bombadil/notices.py`, `tests/mail/lab/`), and the Mail window and the production add-on are not written. Pieces 2 to 10 are designed. The only code that touches them is the press half of piece 2's outbox (mail kind only) and the pill-notice stack, both on the same unmerged branch and built for piece 1; the Slack and Teams connections, the browser service, the NotificationServer, the message and task cards, and pieces 3 to 10 have no code. See [the roadmap](../roadmap.md).
> **Design:** this brief and its page, [Bombadil at work](pages/bombadil-at-work.html); the piece being built is described in [mail](../architecture/mail.md)
> **Decided by:** the owner, 30 Sep 2026: choices 1a, 2b and 3a, in [Choices the owner made](#choices-the-owner-made)
> **Verified:** 2026-10-01 against `main` at `26843d3`

You open the laptop and one line rises above the pill: "Good morning, Maya. Design review at 10:00." You type "what needs me in mail?" and Bombadil's own Mail view opens on three mails marked for a reply, from both your work and your personal accounts in one list. You open Priya's, type "the 14th, if legal signs off Thursday" in the pill, and the reply appears in the Mail view's reply box with an orange ring on Send and the label "Yours: Send". You read it and press Send. An hour later a Slack message from Priya arrives as the one line above the pill; Reply opens it as a Bombadil card with the thread above and a reply box below, and your press on the card's Send posts it in #launch. When the work is inside a tool (writing the one-pager, a shared sheet, a course submission, an expense report), the browser panel slides in on the tool's own page, because that is where that work is done. Nothing leaves the machine that you have not seen, and the one press that lets it go is always yours.

This design pass answers the project owner's ask of 30 Sep 2026: the developer experience of Bombadil had been worked on, but not the day-to-day productivity experience for people who use it for general work (project managers, general workers, students). The ask was for ideas and for how that would look. The standing rule is that the flow needs to be simple but complete. Its first version put every app in the browser. The owner pushed back the same day: many apps have a Linux version, or an MCP server that connects with a simple browser sign-in, so could those be made more modular and visual, fitting Bombadil? For example a Slack message shown in a Bombadil-native way, or mail from several mail clients in one Bombadil-native mail client, with what is easier in the browser staying there. Where is the line? This second version draws that line and rebuilds the design around it.

It sits on the briefs before it: How Bombadil should feel (ux-brief.md), Building on Bombadil (dev-brief.md), Bombadil's Brain (brain-brief.md), The Bombadil Desk (widgets-brief.md), Riding with Bombadil (passenger-brief.md), Bombadil's voice (voice-brief.md), Bombadil tends itself (self-improvement-brief.md) and Bombadil installed (installed-os-brief.md). It adds one app that ships with Bombadil, Mail, and one kind of card, the message card. Everything else lands on surfaces the earlier briefs already define: the pill, its line, cards, the browser panel, a window on the stage, the desk, Focus, a promise or a routine (the pill, its line, cards, the browser panel, windows on the stage and the desk are on main as of 2026-10-01; Focus is in progress and not on main, and promises and routines are designed in the passenger brief and not built).

How it was made: facts were checked on 30 Sep 2026 in six areas (accounts and mail, calendars, driving web apps, documents and files, how the three kinds of people work, and what the repository held that day). The first version was drawn from four angles and the four were compared. After the owner's pushback, three more areas were checked the same evening, covering what each service lets a program on the person's own laptop read and send: Slack and Teams; Gmail, Microsoft 365 and Thunderbird; and the official MCP servers of the common work and school tools. Facts are marked with their research area: [accounts], [calendars], [browser], [documents], [people], [repo], and from the second round [chat], [mail], [connectors].

## The line

**Bombadil shows and answers in its own way; the tool does the work.** Three places, one test each.

1. **Bombadil's own views, for anything you glance at or answer in a sentence or a tap.** New mail and messages, your day, what is due, an invitation, a ticket waiting on you, a file that arrived. There is one view per kind, not one per service: one Mail view for every mail account, one message card for Slack and Teams, one task card for Jira, Linear and Notion, and "today" for every calendar. A small connection per service sits behind them.
2. **The browser panel, for work inside a tool's own page.** Writing in a Doc, a sheet or a deck; a shared tracker; a form; a course page, quiz or submission; an expense report; settings; buying and paying; every sign-in; and any service Bombadil has no connection to. These pages are the tools themselves, and copying them would be a worse copy.
3. **A Linux app, only where it is a real program that beats the site, never a website in a wrapper.** Thunderbird runs unseen as the mail engine. LibreOffice opens files that stay on this machine. A call client stays a VM check (see Risks). Slack's Linux app is Electron around the same web client, and Teams has had no Linux app since 2022, so neither earns a place [chat].

**Where a native view gets its data**, in order: the service's own API or MCP server, where you alone can allow it; an app the provider already trusts, where it is the only thing you can allow alone (Thunderbird, for mail); the service's own notifications and pages, from the browser session you already have. The card always says which ("from Teams' notification").

**Your press sends, on whichever surface shows exactly what goes.** On Bombadil's card when Bombadil wrote the whole thing and a connection can send exactly that (a mail, a Slack reply, a Jira comment); on the site's own button when the act happens inside the site (sharing a document, submitting an assignment, saving an event with guests).

### Where this design differs from the first proposed line

The first proposed line was that a glance or a decision is a Bombadil card, read through the service's own API or MCP when it has one; a stretch of work inside a tool stays in the browser; a Linux app only where it clearly beats the site; the last press stays yours; a Mail view can show several accounts, fetched live and never stored; and each native view is a small kit app per service. This design agrees with the three tests and the last press. Five things change once each service's rules are checked:

- **"API or MCP when it has one" fails for the two biggest mail systems.** Every useful Gmail scope is restricted: a new app is capped at 100 users for its whole life, shows Google's warning screen, and passes only after Google's review plus a yearly paid outside security assessment [mail]. Google's own Gmail MCP server is a Workspace-only developer preview that cannot send and may not ship in public apps before general availability [mail]. Under Microsoft's default consent policy a person cannot allow a new app to read mail, calendars or Teams chats; Microsoft's own MCP servers need a Copilot licence and an admin [accounts, chat, mail]. The one mail program both let a person allow alone is Thunderbird, so it becomes the engine.
- **"Fetched live and never stored" does not survive that.** Thunderbird keeps a copy of your mail on this machine. The rule that matters still holds: the Brain keeps a mail's sender, subject and time, not its text, and the agent treats mail as data (rule 5).
- **One view per kind, not an app per service.** People think "my mail", not "my Gmail"; the owner's own example was mail from several mail clients in one Bombadil-native mail client. An app per service would multiply windows.
- **Slack through a private app in your own workspace, not one Bombadil app for everyone.** Slack holds apps shared outside the Marketplace to one history request a minute and 15 messages, and lets only Marketplace or internal apps use its MCP server and search API; a Marketplace listing needs ten active installs and review [chat]. An internal app that Bombadil sets up in your workspace from a ready manifest keeps normal limits and can receive new messages as they arrive. A member can install one alone on every plan unless the workspace turned on app approval [chat].
- **Teams stays browser-backed.** Reading Teams chats through Microsoft's API needs an admin in default tenants, and reading channels always does [chat]. Teams messages come to the card from Teams' own notifications, and replies are typed into the Teams tab.

## The rules

1. **Glance and answer in Bombadil; work in the tool.** The line above. *Why:* the three most frequent things in a working day (mail, chat, the calendar) are short reads and short answers, and people spend about 56% of the day in the browser for the rest [people].

2. **The press that reaches another person is yours.** The machine reads, drafts, fills, attaches and points. It never sends, posts, accepts, declines, shares, saves an event, joins, creates, submits, pays or signs. On a Bombadil card, it stops with the ring on the card's own Send; on a site, with its pointer on the site's button; where a site has no button to stop at (a shared sheet saves as you type), a goes-out card shows exactly what changes and who can see it. *Why:* undo cannot follow anything that left the machine, and the confirmed briefs already hold this rule for code ("nothing is pushed until you say ship") and reports. The agent is never given a send tool: it puts a draft in Bombadil's outbox, and only the shell's press releases it. Risks item 5 says why that is a rule and not yet a wall.

3. **Reading is quiet; writing in a site happens in view.** Connections read in the background and never change what they read (a mail read for a card stays unread). In a site, typing or clicking happens only with the panel on screen under the orange frame. *Why:* you see every word before the press is yours, and Hyprland stops drawing hidden windows, so clicks there fail anyway [browser].

4. **Every answer about your work points at its source.** A row about a mail, message, meeting, file or page opens the original, and anything copied says when ("as of 08:51") or where it came from ("from Teams' notification"). *Why:* summaries change meaning [people], and the source should be one tap away.

5. **Other people's words are read, never obeyed.** Mail, chat, shared documents and pages are data. After a turn has read them, the hands go only to your own apps and to addresses you typed, and the Brain keeps a mail's or a message's sender, subject or channel and time, but not its text. *Why:* a machine holding your private data, other people's words and a way to send is the classic setup for a hostile mail to steer it [people]. Slack's API terms also forbid persistent copies or indexes of message data for apps used outside your organization [chat].

6. **Your files are never overwritten.** Until home has its own restore points, an edited document lands beside the original ("Q3 plan (edited 30 Sep).docx"). *Why:* undo covers the system, not your home folder [documents, repo].

Three confirmed rules carry over unchanged: the agent never starts work on its own (a routine is your ask with a time attached); other apps' notifications become the one pill line; and colour says who acts (white you, orange the machine, amber a system step, red a step undo cannot reach).

### The hand-over: one look, one wording

When a draft is ready to reach someone, the same thing happens wherever the button is. An orange ring sits on the button, and the pointer rests beside it with a label: "Yours: Send" (or Post, Create, Accept, Save, Share, Submit, Join now, Publish). The status line says what is ready and where: "Reply to Priya is ready. Sending is yours." The first time it ever happens, the line adds one sentence, once: "I never press Send for you. Change anything in it first if you like." On a Bombadil card the button is the card's own; in a site the frame fades first and the button is the site's.

- **You press it.** A card sends exactly what it showed, once, and the line gives a receipt that opens the sent item: "Sent to Priya from maya@acme.com · 09:08 [Open in Gmail]". In a site, the site's own page shows it went.
- **You type "send it".** The line answers "Sending is yours. It's under the pointer." (The owner decided that typed words do not count: only the press on the real button lets a message go.)
- **You walk away.** The draft stays: in the Mail view's Drafts, on a message card you can reopen, or in your own Drafts on the site. The holding card lists it under "Waiting for your press". A waiting draft never marks the dot and never joins the coding sessions' your-turn walk; the welcome line may count it ("1 draft is waiting for you").
- **Several at once.** A reply to each of twelve vendors comes as a checklist card, each row opening its draft so you press each Send.

### Where the line falls

| What | Where | The machine does | You do |
|---|---|---|---|
| Read mail from every account | Mail view | lists it, marks what needs a reply and why | nothing |
| Reply to, forward or write a mail | Mail view | writes it, attaches, checks the size limit | **Send** on the view |
| A Slack message to you, a mention, a thread you're in | message card | shows it with the thread above | nothing |
| Reply in Slack | message card | writes the reply from your words | **Send** on the card |
| A Teams message | message card, from Teams' notification | shows sender and preview; opens the chat on Open | nothing |
| Reply in Teams | the Teams tab | types it into the chat box | **Enter**, or the card's Send once the hands exist |
| Your day, what's due, a meeting coming up | "today" card, meeting line | reads the calendar copies | nothing |
| A ticket, a comment, a page in Jira, Linear or Notion | task card | fills it from your words | **Create** or **Comment** on the card |
| Archive, label or mark mail in your own mailbox | Mail view | does it, says so on the closing line | nothing |
| Save attachments and downloads, write notes and files here | this machine | saves under the real name, says where | nothing |
| Accept or decline an invitation | the calendar's page | opens it, types your note | **Accept** or **Decline** |
| An event on your calendar, even with no guests | the calendar's page | opens the event page, filled | **Save** |
| A new meeting with other people | the calendar's page | finds a time, fills guests, room and call | **Save**, which invites |
| Share a document or change who can open it | the site | fills names and access | **Share** |
| Change a shared document or sheet; upload to a shared folder | the site, through a goes-out card | shows before, after and who can see it | the card's button |
| Join a call | the call's own window | opens the call's own screen | **Join now** |
| Submit an assignment, a form, an expense report | the site | fills it, attaches the files | **Submit** |
| Sign a document | a card | fills the rest, shows where the signature goes | the card's **Sign** |
| Pay, delete for good | the site | never touches it | yours, always |

Your calendar counts as other people's, because colleagues see your busy times and often your titles. A draft counts as yours, because nobody else sees it. Printing stays in the room.

## Build these first

Three pieces, in this order: mail, then messages and connections, then the day. Each is native, each is used every morning by all three people in the days below, and together they carry most of what a working day answers. The browser's hands come fourth, for work inside the tools.

### 1. Mail, in Bombadil's own view

**What you see.** A Mail window on the stage, which ships with Bombadil: all your mail accounts in one list, with "All inboxes", each account, "Needs a reply" and "Drafts" on the left, the list in the middle and the open mail on the right. "what needs me in mail?" opens it on Needs a reply: the mails that ask something of you, each with one line on why. A new mail from someone you know arrives as the one pill line, and Reply opens it in the view. You write a reply yourself, or say what it should say ("the 14th, if legal signs off Thursday") and it appears in the reply box; the ring sits on Send. Attachments have Save, which puts the file in Downloads under its real name; a file dropped on the pill with "send this to the partner team" starts a new mail with it attached. Open in Gmail (or Outlook) opens the same mail on the web.

**Setting it up.** The first mail ask with no account asks one thing on a small card: "Your email address". Bombadil tells Google from Microsoft by the address and sets the account up; the provider's own sign-in page slides into the browser panel with your address filled in and no orange frame; you sign in with your own hands and press Allow on the provider's page, which names Thunderbird. The line says "Your mail from maya@acme.com is in Mail." A second account is "add my personal Gmail". When a workplace or school won't allow it, the line says so once and that account stays on the web: "lakeside.edu asks an admin to approve mail apps. Your school mail stays in Outlook on the web. [Open]"; the Mail view lists it with Open.

**Why first.** Every one of the three days starts in mail, it is the example the owner named, and one engine covers every kind of account with approvals a person can give alone. Bombadil's own Gmail app would be capped at 100 users for its lifetime behind Google's warning screen until it passes Google's review and a yearly paid outside assessment; Microsoft's default policy stops a person allowing any new app to read mail; Google's and Microsoft's own MCP servers are previews behind Workspace, Copilot licences and admins [mail, accounts]. Thunderbird is verified by Google and on Microsoft's default allow list of mail apps, and since version 154 it reads Microsoft 365 mail through Microsoft Graph, which keeps working after Exchange Web Services starts switching off in October 2026 [mail].

**What changes.**
- **Thunderbird as the unseen engine.** Arch's Thunderbird (156, Release channel) starts at login with Bombadil's profile under ~/.local/share/bombadil/mail. Its window lives on a Hyprland special workspace you never visit. Linux has no supported hidden or tray mode yet (the tray mode added in 154 is Windows only), so the VM must show that mail keeps syncing there; `--headless` is the second try [mail].
- **A Bombadil add-on inside Thunderbird** (a MailExtension, installed through the profile's policy) talks to Bombadil by native messaging, with its host manifest in ~/.mozilla/native-messaging-hosts/. It uses Thunderbird's documented APIs: accounts, folders and unread counts, `messages.query` and `getFull`, read state, `onNewMailReceived`, attachments [mail].
- **Sending.** A new mail goes with `messages.sendMessage`, which sends in the background (Thunderbird 153 and later). A reply goes with `compose.beginReply` and then `compose.sendMessage` on that compose tab, because only that keeps it in the thread; its window opens on the hidden workspace [mail]. Either happens only when the shell's press reaches agentd, and only if the message still matches what the view showed: recipients, subject, body and each attachment's fingerprint. The view says what the provider adds: "Your Gmail signature and the quoted message are added when it sends."
- **Accounts** are written into Thunderbird's settings from the address, as its own setup would write them: IMAP with OAuth for Gmail and Outlook.com, Microsoft Graph for Microsoft 365, IMAP with an app-specific password for iCloud. Thunderbird opens the provider's sign-in page in the default browser, which is the panel [mail]. If writing settings proves unreliable in the VM, Thunderbird's own account window shows once, on the stage, for that step.
- **The Mail view** is a kit app shipped as a built-in app in `share/apps` (`/usr/share/bombadil/share/apps` on the ISO; see `builtin_dir` in `src/bombadil/apps.py`), built from the app kit's lists, rows, editor and panels (see [the app kit](../architecture/app-kit.md)), talking to a small mail service in agentd that talks to the add-on.
- **The agent's tools** in os-mcp: `mail_search`, `mail_read` (returns the text marked as other people's words) and `mail_draft` (puts a draft in the view). There is no send tool.
- **"Needs a reply"** is the agent reading sender, subject and first lines of new mail and marking those that ask something of you, with one line each on why. Nothing sorts, files or archives mail unless you ask.
- **The Brain** keeps each mail as sender, subject, time and a link that opens it in the view; downloads carry "came from mail from billing@brightline, Thu 17:02".

**Effort.** L.

### 2. Messages and connections

**What you see.** A Slack message to you (a direct message, a mention, a thread you are in) arrives as the one pill line: "Priya Shah in #launch: Legal just signed off. Can we lock the 14th… [Reply] [Open]". Reply opens a message card above the pill with the last few messages of the thread and a reply box. You write, or say "yes lock it, tell her to go ahead" and the reply is written from your words; the ring sits on the card's Send; the receipt says "Posted in #launch · 11:04 [Open in Slack]". "what did I miss on Slack?" answers with a list card of the unread messages to you, each opening its card. A Teams message arrives the same way, built from Teams' own notification and marked "from Teams' notification", with Open in Teams. Jira, Linear and Notion items that wait on you ("assigned to you", "mentioned you") come as task cards, and "make a ticket for Leo about the empty state, due Thursday" fills a task card whose Create is your press.

**Setting up Slack, once.** The first time a Slack message could be a card, the line asks: "Show Slack messages here? I'd make a small private Slack app in acme.slack.com that only you use. [Set up] [Not now]". Set up opens Slack's own create-app page with Bombadil's manifest filled in. The pointer shows each of your presses on Slack's pages: choose the workspace, Create, generate the app's token, and Allow. If the workspace requires an admin to approve apps, Slack says so on its page and offers to send the request; until an admin approves, Slack messages come from Slack's own notifications, marked so.

**Connecting a work tool, once.** The first ask that needs Jira, Linear, Notion, Todoist or ClickUp opens that service's own sign-in page and then its own page asking to allow Bombadil; you sign in and press Allow.

**Why second.** Chat is the second place a working day is answered from, and it brings what mail does not need: a card with a reply box, a safe place for connection tokens, and the outbox that turns a press into exactly one sent thing.

**What changes.**
- **bombadil-connect**, a service running as its own system user, holds every connection's tokens in its own store, so the agent, which runs as you with a shell, cannot read them. Claude Code keeps MCP tokens in a file readable by your own user, which is why connections are not added to the CLI [connectors]. It speaks MCP to services whose official servers a person can allow alone (Notion, Linear, Atlassian's Jira and Confluence, Todoist, ClickUp), through the official Python MCP SDK's OAuth client, with one client metadata document Bombadil publishes, so each consent page says "Bombadil" [connectors]. It asks for read-only access where it exists (Linear's read-only server, Atlassian's read scopes).
- **Slack, through your own internal app.** Bombadil's manifest asks for user scopes only: read your direct messages, mentions and threads, and post as you. It uses Socket Mode, so new messages arrive at once with no public address, and it keeps internal apps' rate limits [chat]. The recipe takes the app's tokens off Slack's pages inside the browser service and hands them to bombadil-connect, never through the model. Slack's API terms fit: it is your own app in your own organization, reading your own messages as they arrive, keeping no copies [chat].
- **The browser service's first half**, bombadil-browserd: one lasting connection to the panel's Chromium that can open, read, point (the ring and the labelled pointer) and take a value from a page into bombadil-connect. The hands that click and type come in piece 4.
- **Teams, and every service without a connection.** Quickshell's NotificationServer replaces mako, as the first brief decided and nobody has built [repo]. Web notifications become the one pill line; a message card shows the sender and preview the notification carried; Open slides the panel in on that chat. Until the hands exist, the card's Reply opens the chat with your reply copied, and you paste and press Enter. Teams preview text depends on a Teams setting whose default Microsoft does not state [chat].
- **The outbox.** os-mcp gains `propose(kind, target, content)`. The agent never gets a send, post or create tool; Claude Code deny rules (which hold even in full-auto) and Codex's tool filter are a second guard [connectors]. The shell's press goes to agentd, which asks bombadil-connect or the mail service to perform exactly that one act, and the receipt comes back as the line.
- **Two card kinds** for the shell's CardHost (`shell/CardHost.qml`): the message card (a thread, a reply box, one button) and the task card (fields, one button).
- **The holding card** gains "Connected here": each mail account, the Slack app, each tool, with what it reads and Disconnect.

**Effort.** L.

### 3. Today, and meetings on time

**What you see.** "today" answers as a card in a tenth of a second with no model, offline too: the day's meetings and blocks from every calendar the machine keeps, each opening its event, a red mark for anything due, and a clash marked when two calendars overlap. The welcome line can carry the day's first meeting or the nearest deadline (the voice brief words it). Before each meeting, one pill line: "Design review in 5 min · Meet [Join] [Brief me]". Join opens the call in its own window on the stage, at the call's own screen: "Yours: Join now". While you share your screen, other lines shrink to a count ("1 new").

**Why third.** The calendar is the spine of all three days and the cheapest piece: a copy on the machine makes the morning instant, keeps meeting lines coming with the browser closed, and gives the welcome line and the Brain real meetings to point at.

**Where the copy works, honestly.** A private calendar link is reliable for schools and personal accounts. For work accounts it depends on the admin: Workspace hides Google's secret address unless sharing is raised above free/busy, and Microsoft 365 may hide Publish or cap it at busy times [calendars]. Thunderbird cannot help here yet: it has no Microsoft 365 calendar outside test builds [mail]. So for many office workers the normal case is the fallback: "today" reads the calendar's own page, marked "read live", and reminders are the calendar's own notifications as the pill line.

**What changes.**
- A `bombadil-calendar` user timer fetches each private link every 15 minutes into ~/.local/state/bombadil/calendar.db (python-icalendar, python-recurring-ical-events and python-x-wr-timezone from Arch's extra repository, with Outlook's Windows time zone names mapped) [calendars]. Join links come from Meet's, Teams' and Zoom's fields. Times show in your zone, with a far guest's beside it ("14:00 · 13:00 for Leo in Lisbon"). Autopilot lists it: "Calendar copy · every 15 minutes · no AI".
- The links are taken at sign-in by the browser service off each calendar's settings page (Google's secret address, a Canvas or Moodle feed) into a file only you can read, never through the model. Microsoft's needs your press on Outlook's own Publish, because it changes your account; putting the panel away skips it.
- os-mcp gains `calendar(from, to)`, which returns events and never the links; `launcher.py` gains "today", drawn with the list card.
- The meeting line is a check with no model, one of passenger piece 4's lasting watchers, at the event's own reminder time or 5 minutes before. It stands in for the calendar's own reminder, which the setup turns off, so nothing pings twice. Autopilot lists it with Stop.
- Calls open as Chromium app windows in the same profile, on the stage, because a hidden panel stops drawing and a shared window freezes once its workspace is hidden [browser]. They need a Hyprland window rule and pipewire-pulse (on main).
- `greet.py` (the welcome-line module, not on main as of 2026-10-01) gains four fact sources, ranked after anything failing: the first meeting today, anything due within 48 hours, drafts waiting for your press, and a routine's result ready.

**Effort.** M.

### After these, in order

4. **Hands that stop at the last button, for work inside the tools.** The passenger brief's hands in the browser (the orange frame, the labelled pointer, take over by moving the mouse), on top of piece 2's browser service: click, type, scroll, upload, download and paste. The guard, `share/bombadil/leaving.toml`, lists per site the buttons and keys that reach other people (Gmail and Outlook on the web, both calendars, Slack, Teams, Canvas, Moodle, Docs and Drive sharing, SharePoint and Microsoft's other file-sharing pages, Jira, Linear, Concur) plus general names for any site (Send, Post, Submit, Share, Accept, Decline, Save in an event editor, Publish, Join, Create, Pay, Sign, Delete forever); `click` refuses them, `type` refuses Enter in chat boxes, and anything unreadable fails toward handing over. The goes-out card is the compare picture of the passenger brief's cards (the `compare` shape in `src/bombadil/cards.py`) with one control button; its press releases one paste or upload bound to that content and that target. Uploads are recorded as "attached" and become "went to" only when the send is seen to land. Web sign-ins that stay: Chromium's policy gains RestoreOnStartup, BackgroundModeEnabled and TabDiscardingExceptions, and the launch keeps `--password-store=basic` for good [accounts, browser]. This is also where Teams replies become the card's Send: the hands type the reply into the Teams chat and press Enter only if the box holds exactly the card's text. Effort L.
5. **Notes.** A leading "note:" appends to the notes of the meeting happening now, or to today's file in ~/Notes, with no model, offline: "Noted in Design review." "note" alone opens that file. Plain Markdown files the Brain indexes, with undo. No notes app. Effort S.
6. **Routines that prepare and wait**, on passenger piece 4: a routine reads while you are away, puts its result in the desk's Away card and the holding card, and nothing goes out until you press. A standup draft for Slack waits as a message card; a report waits as a mail in the Mail view. Effort S on top of piece 4.
7. **Documents.** Carlito and Caladea (the metric twins of Calibri and Cambria), python-docx, openpyxl, pypdf and poppler in the image; pandoc and LibreOffice on first need [documents]. A new Google Doc or Word file is written here and uploaded, which converts it; existing workbooks are edited through LibreOffice. Effort M.
8. **Print and scan** as jobs with a chip that counts pages: cups, nss-mdns and avahi, sane-airscan, and tesseract for searchable scans, each installed on first need as an amber step [documents]. Effort S to M.
9. **Fill and sign.** A PDF form filled into a copy beside the original; "sign it" shows the page on a card with your signature, and the card's Sign is your press. Effort S to M.
10. **Home restore points**, decided in the first brief and not built, which turn "beside the original" into a real Undo.

### Build order, and what it stands on

0. **Merges first:** the app kit, which the Mail view is built from, and the passenger layer's cards (`shell/CardHost.qml`), which the message and task cards join. Both are on main as of 2026-10-01. Native login, `--strict-mcp-config` and pipewire-pulse were already on main on 30 Sep 2026 [repo].
1. **VM checks before code:** Thunderbird keeping mail in sync from a hidden workspace; its sign-in pages for Gmail, Outlook.com and Microsoft 365 opening in the panel; the add-on's native messaging; a reply sent from a compose window on the hidden workspace; Slack's manifest link and an internal app's install in a real workspace; web notifications reaching Quickshell; Google sign-in with the browser's remote-control port open.
2. **Piece 1.**
3. **Piece 2**, with the card kinds and the NotificationServer.
4. **Passenger piece 4's lasting watchers and Autopilot**, which the meeting lines and routines need.
5. **Piece 3.**
6. **Piece 4**, which is passenger piece 3 extended.
7. **Then 5 to 10.**

The days also lean on confirmed pieces that are not on main: the "this" chip and drop onto the pill (first brief), the holding card (passenger piece 5), the desk's Away card (the card is on main and nothing feeds it), the Brain's Time and Focus, and the Noticed chip. Each day step below names what it needs.

## Three days

Each step says what the person types or taps and what appears, and on which surface. The numbers in brackets name the pieces a step needs; "passenger" means a confirmed passenger piece this design does not change.

### Maya, product manager, a Tuesday

A 200-person software company on Google Workspace, Slack, Jira and Meet; her admin allows the calendar link and leaves Slack app approval off. She has a personal Gmail too. Last week she said "every weekday at 8:45, get my standup ready from Jira and yesterday's calendar" and watched its first run.

- **08:50** She opens the lid: "Good morning, Maya. Design review at 10:00." The desk's Away card holds "Standup draft for #team-standup [Open]". [3, 6]
- **08:52** She taps Open. The draft is a message card for #team-standup with the ring on Send. She changes "blocked" to "waiting on legal" in the card and presses Send. "Posted in #team-standup · 08:52 [Open in Slack]". [2, 6]
- **08:55** "today". A card before she lets go of Enter: 10:00 Design review [Join], 13:00 1:1 with Leo [Join], free until 15:30, 15:30 Pricing sync [Join], and her own 17:00 reminder "Q4 deck to Priya", marked red. "From your calendar · as of 08:51". [3]
- **09:05** "what needs me in mail?" The Mail view opens on Needs a reply: Priya (launch date by noon), Sam (a yes on the pricing copy), Leo (pick A or B before 10), all from her work account, with her personal inbox's two new mails in All inboxes below. [1]
- **09:07** Priya's mail is open; the "this" chip holds it. She types "the 14th, if legal signs off Thursday". The reply appears in the view's reply box; the ring sits on Send: "Yours: Send". She reads it and presses Send. "Sent to Priya from maya@acme.com · 09:08 [Open in Gmail]". [1]
- **09:55** "Design review in 5 min · Meet [Join] [Brief me]". Brief me: the agenda from the invitation, the attached doc in five lines, last week's open actions, each linked to its source. [3]
- **10:00** Join opens Meet in its own window at Meet's own screen: "Yours: Join now". During the call: "note: Leo owns the empty state". "Noted in Design review." [3, 5]
- **10:50** "wrap up the design review". Meet took its own notes, on by default for meetings of three or more on her plan [people]. A checklist card: four actions with owner, day and source, each with [Ticket] and [Remind me], and [Draft follow-up to all 6] below. [4, passenger]
- **10:53** Ticket on Leo's action fills a task card: "Empty state for the import screen · PRJ · Leo Park · due Thu", "Yours: Create". She presses it. "Created PRJ-231 [Open in Jira]". Draft follow-up puts a mail to all six in the Mail view: "Yours: Send". She presses it. [1, 2]
- **11:02** Slack: "Priya Shah in #launch: Legal just signed off. Can we lock the 14th here so marketing can go ahead? [Reply] [Open]". Reply opens the message card with Leo's earlier line above it. She types "yes lock it, tell her to go ahead"; the card's reply reads "Locked: launch is Tuesday the 14th. Priya, go ahead with marketing." She presses Send. [2]
- **11:30** "find 30 minutes with Priya and Leo next week about pricing". Google Calendar's own Find a time in the panel; Tuesday 14:00, shown as "13:00 for Leo in Lisbon". The event is filled with a title, the Meet link and a line of agenda: "Yours: Save. It sends 2 invitations." [3, 4]
- **12:30** On a competitor's pricing page, the "this" chip says "acme.com · Pricing". "compare this with ours". A compare picture, each box opening its page: "Acme is 20% cheaper at the entry tier." [passenger]
- **14:00** "draft a one-pager proposing we match Acme's entry price, from this and this morning's notes". A reader with the draft, each claim linked. "put it in a Google Doc": written as a file and uploaded to her Drive, which converts it. "Made 'Match Acme entry price' in your Drive. Only you can open it. [Open]" [4, 7]
- **14:20** "share it with Priya and Leo, they can comment". Docs' Share dialog, filled: "Yours: Send". [4]
- **15:30** Priya's "Q3 numbers" arrives as the pill line and in the Mail view, with Q3.xlsx; Save puts it in Downloads. "put her numbers into the one-pager's table". The doc is shared now, so a goes-out card: the table before and after, "from Q3.xlsx, sheet Summary", "Priya and Leo can see this doc [Put in the doc]". She taps; the hands paste exactly that table in view. "Undo there: Version history." Focus on Q3.xlsx says "came from Priya Shah's mail, Tue 15:28". [1, 4]
- **16:10** "decline Friday's all-hands prep, say I'm at the offsite". The invitation opens on Google Calendar's page with Decline chosen and her note typed: "Yours: Send". [4]
- **16:40** She drops launch-plan.pdf onto the pill: "send this to the partner team with two lines". A new mail in the Mail view with the file, 4 MB, under the limit: "Yours: Send". [1]
- **17:30** A click on the dot at rest opens the holding card: "Reply to Sam: ready, not sent [Open]" under Waiting for your press; Thursday's reminder and the standup routine under Coming up; under Connected here, her two mail accounts, her Slack app in acme.slack.com and Jira. [1, 2, passenger]
- **Later that week.** After the third morning of "what needs me in mail?", the Noticed chip offers to make it a word. That is the self-improvement loop's call.

### Tomás, operations coordinator, a Friday

A 60-person architecture firm on Microsoft 365 (Outlook, Teams, Excel in SharePoint), SAP Concur for expenses and one printer-scanner. His tenant keeps Microsoft's default consent policy, so he could allow Thunderbird himself; at his Microsoft sign-in he pressed Publish, and his firm lets the link carry only titles and times. Four Fridays ago he said "every Friday at 15:00, make the weekly ops report".

- **08:30** "Good morning, Tomás. Hollis call at 11:00." [3]
- **08:35** "what came in overnight?" The Mail view, from Outlook through Thunderbird: nine mails, three carrying invoice PDFs. [1]
- **08:40** "file the invoices and add them to the tracker". The PDFs are saved from the Mail view into Documents/Invoices/2026-10 under their real names, each knowing "came from mail from billing@brightline, Thu 17:02". The tracker is a shared Excel file in SharePoint, so a goes-out card: the tracker's last rows grey and the new ones orange; "Held: Ferris Steel F-0907. The invoice says $4,120.00 and purchase order PO-5530 says $3,900.00."; "4 people can see Invoices 2026 [Add 2 rows]". He taps; the hands add exactly those rows in view. [1, 4]
- **08:50** "send the Ferris one to Ana and ask about the difference". A new mail in the Mail view with the PDF and two lines: "Yours: Send". [1]
- **09:10** "print the Hollis site plan, A3, two copies". The first time only, an amber step: "Setting up Office MFP". A chip counts "Printing 2 × A3" and turns green. [8]
- **09:40** "scan 3 pages into the Hollis contracts folder". "Scanned 'Hollis contract signed.pdf', searchable." The folder is on SharePoint, so a goes-out card: "9 people can see it [Upload]". He taps. [4, 8]
- **10:15** A vendor form from Brightline in the Mail view: "fill this from their W-9". "Filled 14 of 16 fields in 'Vendor setup Brightline (filled).pdf', beside the original." "sign it": a card with his signature in place [Sign]. "send it back": the reply in the Mail view with the signed file, "Yours: Send". [1, 9]
- **10:40** Teams: "Ana Ruiz: Can you bring the Hollis numbers to the 11:00?", a message card marked "from Teams' notification" with [Reply] [Open in Teams]. He types "yes, bringing them"; the card's Send has the hands type it into the Teams chat and press Enter. Before piece 4, Reply opens the chat with his words copied. [2, 4]
- **10:55** "Hollis call in 5 min · Teams [Join] [Brief me]". Brief me: the last three Hollis mails and the last call's open actions. [1, 3]
- **11:00** His calendar copy carries no Teams address, so the machine opens the event in Outlook on the web and takes the Teams link from it into its own window, at the screen before joining: "Yours: Join now". During: "note: revised fee schedule by Monday". [3, 4, 5]
- **11:45** "wrap up". A checklist card marked "from your notes only; the call had no transcript" (Teams' own recap needs a paid add-on [people]). He taps Remind me on "Send revised fee schedule · Mon". [5, passenger]
- **13:30** He drops three receipt photos onto the pill: "expense these for the Lisbon site visit". A card with each receipt's merchant, date, amount and category, total €214.60. Then Concur in the panel, the report filled and the photos attached: "Yours: Submit". Concur has no connection, so it stays in the tool. [4]
- **14:10** The expense policy PDF is open: "what does this say about mileage?" Two lines and the page number. [passenger]
- **15:00** His routine runs while he is there, its route in the desk's Now. "Weekly report ready: 'Ops week 40.docx'. [Show] [Mail it]". Show opens it in LibreOffice; Mail it puts a mail to both directors with the file in the Mail view: "Yours: Send". [1, 6, 7]
- **16:00** Ana's invitation arrives in the Mail view. "accept, and tell her I'll bring the Q3 numbers". Accepting changes his calendar, which Thunderbird cannot reach for Microsoft 365 yet, so the invitation opens in Outlook on the web with Accept chosen and his note typed: "Yours: Send". [1, 4]
- **16:30** "send the Hollis drawings to Kim at BuildCo". "The drawings are 60 MB, over the 35 MB your mail allows, so I'll share them from SharePoint." SharePoint's own Share page, filled with Kim's address and view-only: "Yours: Send". [4]
- **16:45** "remind me Monday at 9 to chase Ferris". "Reminder set: Mon 9:00. [Undo]" Then "shut down" gives the voice brief's goodbye.

### Aisha, second-year biology student, a Wednesday

Canvas and a Microsoft 365 account at university, whose admin has turned off letting students allow apps; a personal Gmail whose calendar holds her café shifts; the group project is a Google Doc under her Gmail, and the group talks in a Slack workspace the class uses. On Sunday she said "every Sunday at 19:00, plan my week from Canvas".

- **07:45** "Good morning, Aisha. Lab report 3 is due tonight at 23:59." The date came from the Canvas feed, with no network yet. [3]
- **07:50** "today", with no model, across two calendars: 09:00 BIO 201 lecture, 11:00 study block, 13:00 Group 4 (Zoom) [Join], 17:00 café shift, 19:00 study block marked "Clash: café shift until 20:00", and a red mark "Lab report 3 · 23:59". [3]
- **07:52** "move tonight's study block to 20:30". Google Calendar's event page with the new time: "Yours: Save". [3, 4]
- **07:55** The Mail view holds her personal Gmail. Her university account is listed with "lakeside.edu asks an admin to approve mail apps. It stays in Outlook on the web. [Open]", said once when she first added it. [1]
- **09:00** In the lecture, the slides open from Canvas in the panel ("download today's BIO 201 slides" put them in ~/School/BIO 201 under their real name). "note: ask about figure 3 for the exam". "Noted in BIO 201 lecture, with BIO201-L9.pdf page 14." [4, 5]
- **09:40** "explain this slide simply". A diagram card: the pathway as five boxes, the step the lecturer stressed lit. [passenger]
- **11:00** "find five peer-reviewed papers on phage therapy since 2020". A list card with Open and Cite; Cite puts the exact APA reference from doi.org into Essay 2's sources file [documents].
- **12:10** Her lab partner's USB stick: "LAB-PHOTOS plugged in · 14 photos [Open] [Eject]". "put the gel photos in my lab report folder". "Copied 6 photos to ~/School/BIO 201/Lab 3. [Show]" [passenger]
- **12:55** Slack: "Sam in #group-4: can someone take methods?" as a message card. "tell them I'll take methods, due Tuesday". Her press on Send. [2]
- **13:00** "Group 4 in 5 min · Zoom [Join]". Zoom in its own window. "note: I take methods, due Tuesday". [3, 5]
- **14:30** A mail in the Mail view: "Study group: Thursday 18:00 on Zoom". "add it to my calendar". Google Calendar's event page, filled, no guests: "Yours: Save". [1, 3, 4]
- **16:00** She drops her own "Lab report 3.docx" onto the pill: "check it against the rubric". A checklist card of the six rubric rows from the assignment page, four ticked; "Discussion doesn't mention sources of error [Show]" opens that paragraph. "rewrite the discussion so it covers sources of error": the paragraph is written into a copy beside hers, and the log gets "Wrote: Discussion paragraph, 16:04" (choice 2). "put my references in APA": the reference list is rewritten, into the same copy. [4, 7]
- **17:30** "put my methods into the group report after the introduction". A goes-out card: "Add 'Methods' (380 words) after 'Introduction' in 'Group 4 report' · Sam, Jo and Lee can see it [Add to doc]". She taps; the hands paste exactly that text in view. [4]
- **20:10** "submit the lab report". Canvas in the panel; the hands choose Lab report 3.pdf; the ring and pointer sit on Submit Assignment: "Yours: Submit". Focus on the PDF now says "Submitted to Canvas: Lab 3, Wed 20:11, this version". [4]
- **21:00** "what did you help me with on the lab report?" Four dated helps from the Brain's Time, each marked Explained, Checked or Wrote, with the file it touched. "draft my AI-use note" writes a paragraph from them into her notes file, for the note her course asks for. [choice 2]
- **21:10** "I lost this afternoon, replan my week". Old blocks grey, new orange, "Moved 3 blocks, added 1 on Saturday · only you can see it [Update]" on a goes-out card. She taps; the hands move the blocks in view. [3, 4, 6]

## Coverage

| Area | Maya, PM | Tomás, office | Aisha, student |
|---|---|---|---|
| Mail | Mail view across two accounts; replies and a new mail, her Send | Mail view on Microsoft 365; invoices saved; forms sent back | Mail view with Gmail; school mail stays on the web, said once |
| Chat | Slack cards: a standup, a reply in #launch | Teams card from its notification | Slack card in the class workspace |
| Calendar and meetings | "today"; Brief me; Meet window; a meeting found, her Save; a decline | titles-only calendar; Teams through Outlook's event; accept on Outlook's page | two calendars with a clash; events moved and added |
| Writing and documents | one-pager as a private Doc, shared by her press; a shared table by card | report as .docx from a routine; a form filled and signed | rubric check, and text written when she asks, in a copy beside hers; APA; her methods into the group Doc by card |
| Tasks and tickets | Jira ticket on a task card | none | none |
| Reading and research | compare picture from "this" | a policy PDF in two lines | a slide as a diagram; papers; citations |
| Notes and reminders | "note:"; Remind me; the holding card | "note:"; a reminder in words | "note:" with the slide's page; the help log, marking what Bombadil wrote |
| Files in and out | Q3.xlsx from mail; a PDF by mail | invoices from mail; scans; 60 MB through SharePoint; printing | slides from Canvas; photos from a USB stick; a Canvas submission |
| Something recurring | weekday standup, posted by her press | Friday report, mailed by his press | Sunday study plan, replanned on asking |

Gaps across all three: an employer or school that admits only managed computers cannot be used from Bombadil at all; a tenant or school that turned off letting people allow apps keeps its mail on the web (Aisha's); Teams messages are only as full as Teams' notifications until the hands open the chat; accepting invitations and changing Microsoft 365 calendars still happen on Outlook's page; many work calendars cannot be copied, so "today" is read live for them; Google Classroom gives students no calendar link [calendars]; exams under Respondus or Honorlock do not run on Linux [people]; Excel macros do not run in Excel on the web and only partly in LibreOffice [people]; whether Teams accepts Chromium is unverified [browser].

## Connections, and how they stay

Every connection is set up once, by your own hands, on the service's own pages in the browser panel: you sign in there, and where Bombadil needs more than the web session, you press Allow on the service's own page. The agent loads only Bombadil's own tools and none of the connectors on the person's Claude account (`--strict-mcp-config` with Bombadil's own list, on main) [repo]. The table says where each service lands.

| Service | Native view | Where the data comes from | Who can allow it | What your press sends |
|---|---|---|---|---|
| Gmail and Google Workspace mail | Mail | Thunderbird, unseen | you, unless a Workspace admin blocks unconfigured apps or you are a school account under 18 [accounts] | the mail |
| Outlook.com and Microsoft 365 mail | Mail | Thunderbird with Microsoft Graph | you, under Microsoft's default policy, which lists Thunderbird among mail apps people may allow; an admin where consent is off [accounts, mail] | the mail |
| iCloud and other mail | Mail | Thunderbird; iCloud needs an app-specific password once | you | the mail |
| Slack | message cards | your own internal Slack app, new messages pushed as they arrive | you, unless the workspace requires app approval (off by default on every plan) [chat] | the reply or post |
| Teams | message cards | Teams' own notifications and the Teams tab; Microsoft's API needs an admin to read chats [chat] | nothing to allow | the reply, typed into the Teams tab |
| Google, school and published Outlook calendars | "today", meeting lines | private calendar links; the page where there is none | you (Microsoft's needs your Publish) | nothing; changes happen on the calendar's page |
| Jira, Confluence, Linear, Notion, Todoist, ClickUp | task cards | the service's official MCP server | you, unless an admin limited apps [connectors] | a ticket, comment or page |
| Moodle | due dates, and a list of what's due | the calendar export; Moodle's mobile service where the site allows it (on by default for HTTPS sites) [connectors] | you | nothing |
| Canvas | due dates | the calendar feed; its API needs a key from each school and forbids asking students to paste tokens [connectors] | you | nothing; submissions on Canvas's page |
| Asana, GitHub, Box, Zoom, HubSpot, Dropbox, Figma | none at first | the browser; each needs an admin or an app approved by the vendor [connectors] | not you alone | the site's own button |
| Docs, Sheets, Word, Excel, forms, Concur, Canvas pages | none: work in the tool | the browser | nothing to allow | the site's own button, or the goes-out card |

**Sign-ins in the browser** use the panel's profile, `~/.local/share/bombadil/browser` (native login's). A Microsoft sign-in asks "Stay signed in?"; the line says "Choose Yes, so this lasts." [accounts] Google may refuse a browser with a remote-control port open; if it does, the line says "Google wants a browser nothing is attached to. Restarting it for your sign-in.", Chromium restarts once without the port, and with it after [accounts].

**How it stays.** Thunderbird's tokens renew themselves (Google's last until six months unused or a password change, Microsoft's roll every 90 days with use), so mail keeps arriving even when a web session has expired [accounts]. Slack's user tokens last until revoked unless token rotation is on, which Bombadil's manifest leaves off (my understanding, not checked this round). MCP connections refresh on their own schedule (Notion's refresh token ends after 30 days unused) [connectors]. When any connection or web session lands on a sign-in page, the line takes the shape provider trouble already has: "Google signed you out of maya@acme.com. [Sign in]".

**When a workplace blocks it.**

| What happens | What you see | What still works |
|---|---|---|
| The organization admits only computers it manages | "contoso.com opens only on computers it manages, and Bombadil can't be one. Use this account on your work laptop or phone." No retry. | every other account |
| Mail apps need an admin (consent off, or a school account under 18) | "lakeside.edu asks an admin to approve mail apps. Your school mail stays in Outlook on the web. [Open]" | that mail on the web; everything else |
| Slack requires app approval | Slack's own page offers to send the request; until approved, "Slack messages come from Slack's notifications." | message cards from notifications |
| An admin limited MCP apps (Notion, Linear, Atlassian) | "acme.atlassian.net asks an admin to allow Bombadil. Jira stays in the browser." | the tool in the browser |
| The calendar link is off or busy-only | the holding card's row says which | "today" reads the page; the service's own reminders |

**At rest.** Thunderbird keeps mail and its tokens in its profile, and the browser keeps its sign-ins, both readable by anything running as you, the agent included; bombadil-connect's tokens are not, because it runs as its own user. The protection for the rest is the encrypted disk and the lock, which belong to "Bombadil as an installed OS" (its question 1). A work or school account should not live on an unencrypted stick.

## Defaults I picked

- **One view per kind, with a connection per service behind it.** *Why:* people think "my mail", and one Mail view for every account is what the owner asked for. *Alternative:* a kit app per service.
- **Thunderbird runs unseen as the mail engine.** *Why:* the one mail program Google has verified and Microsoft's default policy lets a person allow alone, covering Gmail, Outlook.com, Microsoft 365 through Graph, iCloud and IMAP [mail, accounts]. *Alternatives:* Bombadil's own Gmail and Microsoft apps (100 users ever and a warning screen for Gmail until review and a yearly paid assessment; admin consent for Microsoft 365) [mail]; reading Gmail and Outlook on the web (slow, and broken whenever the sites change; Gmail's unread feed has about 20 entries and is not official for personal accounts) [mail]; choice 3.
- **Slack through a private app Bombadil makes in your own workspace.** *Why:* normal rate limits, new messages as they arrive, no Marketplace listing, and allowed by a member alone unless the workspace turned approval on [chat]. *Alternative:* Slack's notifications only; a shared Bombadil app in Slack's Marketplace once it has ten installs.
- **Teams from its notifications and its tab.** *Why:* reading chats through Microsoft's API needs an admin in default tenants, and Microsoft's own MCP servers need a Copilot licence [chat]. *Alternative:* a Graph connection for tenants whose admin approves it, added when someone asks.
- **Work tools through their official MCP servers, held by a service the agent cannot read.** *Why:* Notion, Linear, Atlassian, Todoist and ClickUp let a person allow a new app alone, and the tokens stay out of the agent's reach [connectors]. *Alternative:* adding them to the CLI's own MCP list, which stores tokens where the agent can read them.
- **The agent never holds a send tool.** *Why:* the press must be the person's, and a tool the agent does not have cannot be talked into. *Alternative:* send tools with deny rules only.
- **Sign-in starts from the first ask that needs it, and the provider comes from the address.** *Why:* no setup screen, no word to learn. *Alternative:* an "accounts" word with provider chips.
- **The calendar is copied from its private link where one exists, and read from the page where not.** *Why:* instant, offline, meeting lines with the browser closed. *Alternative:* Google's Calendar API, which needs only a few days' review and could come later for Google write-backs [mail].
- **Meeting lines are on wherever there is a calendar copy, in place of the calendar's own reminder.** *Why:* the same reminder, with Join and Brief me on it, listed in Autopilot with Stop. *Alternative:* the calendar's own notification only.
- **Your calendar counts as other people's.** *Why:* colleagues see busy times and often titles. *Alternative:* events with no guests written freely, with Remove on the closing line.
- **Meeting notes come from the platform and from your "note:" lines; Bombadil records nothing.** *Why:* the platforms already take notes, and recording needs every party's consent in many places [people]. *Alternative:* a local recorder with a consent step.
- **Mail and chat are never kept as text in the Brain.** *Why:* every later turn can search the index, and Slack's terms forbid lasting copies [chat]. *Alternative:* keep them like other pages an agent read.
- **Documents are written where people already write them, and new versions land beside the old.** *Why:* uploading a file is exact; pasting into Docs or Word on the web is only as good as their paste [browser]. *Alternative:* a native rich-text editor, which loses tables and footnotes [documents].
- **Writing for school is logged, not gated.** *Why:* the owner chose that Bombadil writes whatever a student asks (choice 2b), so the log per assignment carries the honesty: each help dated and marked Explained, Checked or Wrote, built from the turn record with no copy of the text, and an AI-use note drafted from it on request. *Alternative:* ask whether the course allows it first, or refuse graded text (choice 2, a and c).
- **LibreOffice, pandoc, printing and scanning install on first need.** *Why:* over a gigabyte most people never need. *Alternative:* all in the image.
- **Which account to use:** the one the thing came to; otherwise the one whose label matches your words; otherwise the first. *Why:* the question stays rare.
- **Only two new words, "today" and a leading "note:".** *Why:* each works with no model and offline, and each is a fast path for a plain ask that also works. *Alternative:* words for mail, Slack and tickets, which the Noticed loop can offer once someone keeps asking.

## Choices the owner made

**Answered.** The owner answered "1a 2b 3a" on 30 Sep 2026. The rest of this brief assumes those answers. The options below are what the owner was offered; the owner's answer is marked **(the owner's answer)** and my recommendation **(recommended)**.

1. **When a mail or message is ready, what should let it go?**
   a. Only your press: on Send in Bombadil's view or card, or on the site's own button. **(recommended) (the owner's answer)**
   b. The same, and also typing "send" in the pill once the draft has been on screen unchanged.
   c. Your ask can include it: "reply to Priya and send it" sends without stopping.

2. **When a student asks Bombadil to write something that will be graded, what should it do?**
   a. Explain, quiz, check against the rubric and cite by default; write graded text only after she says the course allows it; keep a help log per assignment. **(recommended)**
   b. Write whatever she asks, and keep the same log. **(the owner's answer)**
   c. Never write graded text.

3. **Where should Bombadil's Mail view get your mail?**
   a. From Thunderbird running unseen: every account in one list, works offline, and people can allow it themselves; it keeps a copy of your mail on this machine. **(recommended) (the owner's answer)**
   b. Live from Gmail and Outlook on the web: no copy, but slower, and it breaks when those sites change.
   c. No Mail view yet: mail stays in the browser, as in the first version.

**What the owner's answers decide.**
- **1a.** The press is the one way a message goes. Typing "send it" gets "Sending is yours. It's under the pointer.", as drawn. The hands-free path for later voice use (1b) is not built, so agentd has no rule for typed "send".
- **2b.** Bombadil writes what a student asks for, graded or not, and never asks whether the course allows it. Because nothing is refused, the log is the only record of who wrote what, so it is part of the feature, not an extra: for each assignment the Brain's Time lists every help, dated, marked Explained, Checked or Wrote, with the file it touched. It is built from the turn record (what was asked, which file, whether text was written); it keeps no copy of the text. Written text goes into a copy beside her file (rule 6), and her Submit press is unchanged. "what did you help me with on the lab report?" reads the log, and "draft my AI-use note" writes a paragraph from it into her notes file, for the statement many courses ask for. There is no per-course switch and no "are you sure" before writing. A student who hands in text Bombadil wrote, where the course forbids it, has made that choice; the log lets her tell the truth about it.
- **3a.** Mail is built on Thunderbird as drawn, and piece 1 starts with the VM check of whether it keeps syncing unseen (Risks, item 1).

Why these were the owner's. The first decides how Bombadil feels at the moment that matters most: (a) is the safest and simplest, with one way to send; (b) keeps a hands-free path for later voice use, with agentd, not the model, checking that "send" was typed and the draft had not changed; (c) is fastest and gives up the rule. The second is about honesty in school: most universities treat undisclosed AI text as misconduct and set rules course by course [people]. The third decides whether your mail lives on this machine (fast, offline, searchable by you) and whether Bombadil depends on Thunderbird, a program it does not control and whose hidden running on Linux is still a VM check. The first version's third question, whether to keep a copy of mail, is folded into this one. The lock and a login password are not asked here; the installed-OS brief asks them (its question 1). The build of pieces 1 and 2 (Mail, then messages and connections) is its own piece of work, which reads this brief.

## Cut, and why

- **An app per service** (a Slack app, a Gmail app, a Jira app). One view per kind; the service is a connection behind it.
- **A full Slack or Teams client.** Cards for what is addressed to you; reading a whole channel is the service's own job, one Open away.
- **Linux apps that wrap a website** (Slack's desktop app, unofficial Teams apps, Notion wrappers). The same page in a second window, with its own notifications to reconcile.
- **Bombadil's own Google and Microsoft mail apps.** Google's restricted-scope review and yearly assessment, and Microsoft's admin consent, would stop most people [mail, accounts]. Thunderbird already has both approvals.
- **Google's and Microsoft's MCP servers for mail.** Previews, Workspace-only or Copilot-licensed, admin-gated, and Google's cannot send [mail].
- **GNOME Online Accounts and Evolution.** Microsoft work accounts need admin approval for them [accounts], and they want GNOME's settings under Hyprland.
- **An "accounts" word, an accounts card and provider chips.** The first ask that needs an account starts the sign-in; the holding card lists what is connected.
- **Opt-in chips at sign-in** and an "ask IT" chip. Each is a question where a default does; a block is said in one line.
- **A built-in Notes app.** A line typed in the pill writes to a plain file.
- **Calendar, today, notes or deadline widgets on the desk.** The desk brief cut them, and they stay cut.
- **A morning brief that appears by itself, and sorting mail on its own.** Each is one sentence away as a routine [people].
- **"Are you sure?" before every send.** The ring and the label are the check; a second question would be ignored.
- **Typing live into shared documents.** It reaches people as you type; the goes-out card does the job.
- **A meeting recorder or transcriber.** Consent law, and the platforms already take notes.
- **Saving passwords in the browser.** Chromium's store is plain text on Bombadil today [repo].
- **Unsent drafts in Needs you.** Needs you is for coding sessions; a draft waits in the holding card.
- **A lock-password question here.** The installed-OS brief owns it.

## Owned by other pieces of work

- **Setup voice and welcome lines:** the words of the welcome line and the goodbye. This design adds four fact sources.
- **Bombadil as an installed OS:** disk encryption, the lock and the one password (its question 1), which protect Thunderbird's copy and every sign-in; the keyring decision (no daemon); what a flash drive keeps (its question 2); passwordless sudo for the agent, which decides how strong bombadil-connect's separate user is; the image's package list (Thunderbird, fonts, python-playwright, the calendar libraries).
- **Native provider login:** signing in to Claude or Codex; this design reuses its browser launcher, profile and policy file.
- **The self-improvement loop:** turning repeats into words, widgets, apps or routines.
- **Bombadil logo and design system:** the look of the Mail view, the message and task cards, the ring, the "Yours" pointer, the goes-out card and the address card.
- **The passenger brief:** the hands in the browser (piece 3), promises and routines (piece 4) and the holding card (piece 5), each extended below.
- **The app kit:** the components the Mail view is built from, and whether built-in apps live beside agent-made ones.
- **The desk:** the Away card holds routine results; nothing is added to the desk.
- **The Brain:** mail, messages and meetings as things in Focus, kept without their text.

## Extends or changes the earlier briefs

- **The first version of this brief: changed.** Mail and chat move from the browser into Bombadil's own views; Thunderbird goes from cut to the unseen engine; "Accounts" becomes "Connections"; the build order becomes mail, messages, the day, then the hands.
- **Passenger piece 3, the hands in the browser: extended and moved later.** The browser service's reading half comes first, with piece 2; the hands, the guard and the goes-out card come with piece 4. Reading happens in hidden tabs, writing only in view.
- **Passenger piece 4, routines: narrowed.** A routine may read while you are away, and its result waits for your press on a card or in the Mail view.
- **Passenger piece 5, the holding card: extended** with "Waiting for your press" and "Connected here".
- **Answer cards:** two new kinds, the message card and the task card; plus the address card (the first brief's open "rare question" shape, designed once) and the signature card.
- **The app kit:** its first built-in app, Mail, built from the same components the agent uses.
- **Notifications as one pill line, mako removed: unchanged, built here,** carrying the web apps' pings and turning chat notifications into message cards.
- **The passenger layer's keyring: changed** to Chromium's basic store pinned for good, following the installed-OS brief.
- **The Brain: narrowed and extended.** Mail and messages are kept as sender, subject or channel, time and link, not text; downloads carry "came from", uploads "went to".
- **Launcher words: extended** with "today" and a leading "note:", each a fast path for a plain ask.
- **Undo:** unchanged; the closing line of an outside act says where its undo lives.
- **The desk: unchanged.**

## Risks, and what to check in the VM before building

1. **Thunderbird unseen.** Whether it keeps fetching and sending from a Hyprland special workspace, or with `--headless`; how it behaves on first run (Account Hub) with settings written ahead; memory and start time [mail].
2. **Thunderbird sign-ins in the panel:** Gmail, Outlook.com and Microsoft 365 (Graph), with the loopback redirect back from Chromium; and whether Google refuses sign-in in a Chromium with the remote-control port open [accounts, mail].
3. **Replies from the hidden compose window:** `compose.beginReply` then `compose.sendMessage` with the window on the hidden workspace; the signature and quote added; nothing flashes on the stage [mail].
4. **Slack setup:** whether Slack's create-app page accepts a manifest by link; the internal app's install and Socket Mode token taken inside the browser service; what "app approval on" looks like to a member [chat].
5. **The press is a rule, not yet a wall.** The agent runs as you with a shell and passwordless sudo, so a hijacked agent could reach Thunderbird's profile or Chromium's debugging port directly [browser, connectors]. Hardening, in order: bombadil-connect as its own user; browserd owning the only browser connection; agentd accepting card presses only from the shell's own process; and the sudo question, which is the installed-OS brief's. An injection drill (a test mail and page hiding instructions to forward mail or run curl) records what leaks.
6. **Notifications:** web notifications from Slack, Teams and Gmail reaching Quickshell's NotificationServer, and how much text Teams' carry by default [chat].
7. **The guard's reach** (piece 4): every final button and key on each listed site, icon-only buttons, Slack's Enter setting, Teams' Ctrl+Enter.
8. **Calendar links:** which Workspace and Microsoft 365 defaults hide them, freshness, and whether they carry reminder times [calendars].
9. **Calls:** Meet, Teams and Zoom on Hyprland with camera, audio and whole-screen sharing; whether Zoom's Linux client beats its web client there; whether Teams accepts Chromium [browser].
10. **Memory** of Thunderbird plus three or four web apps open all day (my estimate, not checked).

## Checked

First round, 30 Sep 2026: Google, Outlook.com and Microsoft 365 require modern sign-in for mail apps, and only iCloud still takes an app password; Thunderbird is on Microsoft's default allow list of mail apps people may consent to, and GNOME Online Accounts and Evolution are not; under-18 Workspace for Education accounts are blocked from unconfigured apps; Google blocks sign-in from browsers "controlled through software automation"; "Stay signed in?" decides whether a Microsoft web session survives a restart; Chromium's cookie key changes if a keyring appears; Google's secret iCal address, Outlook's Publish a calendar, Canvas's and Moodle's feeds exist as described; python-icalendar, python-recurring-ical-events and python-playwright are in Arch's extra repository; LibreOffice is 551 MiB; Carlito and Caladea match Calibri and Cambria; Gmail allows 25 MB attachments and Microsoft 365 35 MB by default; on Hyprland a shared window freezes when its workspace hides.

Second round, same evening: Slack's MCP server is generally available since 17 Feb 2026 but only for Marketplace and internal apps, with no dynamic registration, and workspace admins approve MCP clients; members may install apps without approval by default on every plan; apps shared outside the Marketplace are held to one history request a minute and 15 messages, internal apps are not; Slack's API terms (10 Oct 2025) forbid lasting copies and LLM training on API data for apps used outside your organization; Socket Mode is allowed for internal apps; Slack's Linux app is Electron and still labelled beta; under Microsoft's default consent policy users cannot allow Chat.Read or any Mail or Calendars read scope for new apps; Microsoft's Work IQ MCP servers need a Microsoft 365 Copilot licence and an admin; Teams has no Linux app since December 2022; every useful Gmail scope is restricted, unverified apps are capped at 100 users for life, and restricted-scope verification includes a yearly outside assessment; Google's Workspace MCP servers are a developer preview and Gmail's cannot send; Thunderbird 153 added `messages.sendMessage` for background sending and 154 reads Microsoft 365 mail through Graph, while its tray mode is Windows only; Thunderbird add-ons reach local programs through native messaging; Notion, Linear, Atlassian, Todoist and ClickUp run official MCP servers that any client can register with; Asana, GitHub, Box, Zoom, HubSpot, Dropbox and Figma need an admin or an approved app; Canvas has no public MCP and forbids asking students to paste tokens; Moodle's mobile service is on by default for HTTPS sites; Claude Code's deny rules hold in full-auto, and the official Python MCP SDK has an OAuth client.

Not checked, my own: whether Thunderbird keeps syncing unseen on Linux, whether Slack prefills a manifest by link, Teams' notification text by default, Outlook's and Slack's quietest notification settings, whether calendar links carry reminder times, reading a hidden tab, and the memory cost of all of it.

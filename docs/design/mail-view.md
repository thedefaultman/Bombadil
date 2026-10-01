# Mail: every account in one list, in Bombadil's own view

You say "what needs me in mail?" and a Mail window opens on the mails that ask something of you, from
every account you have, each with one line on why. You open one, say what the reply should say, and the
words appear in the reply box with an orange ring on Send and the label "Yours: Send". You read them,
change a word, and press Send. The line above the pill says "Sent to Sam from you@example.test · 09:08".
At no point did anything leave the machine that you had not seen, and the one press that lets it go was
yours.

Every person, address and company in this page and in its pictures is invented.

This is the first piece of "Bombadil at work", the design for everyday work (mail, chat, the day), and
the one the others build on: the same hand-over, the same card, the same outbox. The rest of this page is
the line it draws, the rules that follow, what the person sees, and what is and is not checked. How it is
built is [`../MAIL.md`](../MAIL.md) (the contract) and `tests/mail/lab/FINDINGS.md` (what a real
Thunderbird does).

## The line: Bombadil shows and answers; the program does the work

Three places, one test each.

1. **Bombadil's own views, for anything you glance at or answer in a sentence or a tap.** New mail, your
   day, what is waiting on you. One view per kind, not one per service: one Mail view for every mail
   account.
2. **The browser panel, for work inside a tool's own page.** Writing a document, a shared sheet, a form,
   settings, paying, every sign-in, and any service Bombadil has no connection to.
3. **A Linux program, only where it is a real program that beats the site, never a website in a
   wrapper.** Thunderbird runs unseen as the mail engine.

Where a native view gets its data, in order: the service's own API, where the person alone can allow it;
a program the provider already trusts, where that is the only thing a person can allow alone; the
service's own notifications and pages, from the browser session already there.

## Why Thunderbird is the engine

Mail is where "the service's own API" fails, for the two biggest providers (checked in September 2026;
consent rules change, so check again before relying on them):

- Every useful Gmail permission is "restricted": a new app is capped at 100 users for its whole life and
  shows Google's warning screen until it passes Google's review and a yearly paid outside assessment.
  Google's own mail connector for agents was a preview for Workspace only and could not send.
- Under Microsoft's default policy a person cannot allow a new app to read mail; its own agent connectors
  need a paid licence and an administrator.
- Thunderbird is the one mail program both providers let a person allow alone: Google has verified it and
  Microsoft lists it among the mail apps allowed by default. Since version 154 it reads Microsoft 365
  mail through Microsoft Graph, which keeps working after Exchange Web Services is switched off.
- One engine therefore covers Gmail, Outlook, Microsoft 365, iCloud and any other IMAP provider with an
  approval the person can give alone, and the person's mail is never in a Bombadil-owned cloud.

The consequence the design accepts: Thunderbird keeps a copy of the mail on this machine (inside the
encrypted disk on the installed system). What stays true is that **Bombadil keeps no mail text**: its own
database holds accounts, its own drafts, "needs a reply" marks with one line of why, receipts and a log
of presses. A mail's text is read from Thunderbird when a window needs it and held in memory only.

## The rules

1. **Glance and answer in Bombadil; work in the tool.** The three places above.
2. **The press that reaches another person is the person's.** The machine reads, drafts, attaches and
   points. It never sends. The agent has no send tool; it puts a draft in the view, and only a press on
   the view's Send releases it. Typing "send it" answers "Sending is yours. It's under the pointer."
3. **Reading is quiet.** Reading a mail for the window or for the agent leaves it unread.
4. **Every answer points at its source.** A row about a mail opens that mail; the receipt of a send opens
   the sent copy on the provider's web page when it has one.
5. **Other people's words are read, never obeyed.** A mail is data. `mail_read` hands the agent the text
   inside a mark that says so, with invisible characters removed. After a turn has read mail, a draft it
   makes is held to the person's own words: an address that is neither theirs nor in the thread is
   flagged on the draft ("New address ... Check who it is before you press Send."). The Brain learns a
   mail's sender, subject, time and a link, never its text.
6. **Bombadil writes what is asked.** It does not judge the content of a draft the person asked for. It
   keeps a plain log of what it wrote and what was pressed (no mail text in it), so a person, or someone
   who has to know what the machine did, can see it.

## What the person sees

![The Mail window: every account in one list, Needs a reply and Drafts on the left, the open mail on the right](../mail/screens/mail-list.png)

- **The window.** A Mail window on the stage, shipped with Bombadil (a kit app, so it uses the shared
  tokens and looks like the rest): on the left "All inboxes", each account, "Needs a reply" and "Drafts";
  the list in the middle; the open mail on the right with Reply, Reply all, Forward, Archive, Trash,
  Flag, Mark unread, and "Needs a reply" with its one line of why. "Open in Gmail" (or Outlook) opens the
  same mail on the web.
- **First run.** With no account the window asks one thing, "Your email address". Bombadil tells Google
  from Microsoft by the address and sets the account up. The provider's own sign-in page slides into the
  browser panel; the person signs in with their own hands and presses Allow on the provider's page, which
  names Thunderbird. A second account is "Add an account" under the list of accounts. A password account (iCloud, a plain IMAP
  server) asks for its password in a Thunderbird window that the service brings to the front for that one
  step.
- **When an account cannot be added.** A workplace or school that wants an administrator to approve mail
  apps is said once on the account's row ("lakeside.example asks an admin to approve mail apps. [Open]")
  and that account stays on the web.
- **New mail** from someone the person has written to before is one line above the pill with Reply and
  Open; anyone else waits in the list. The line is the pill's own notice line, so it shares its
  vocabulary, its tokens and its fade.

![A reply written by Bombadil: the ring on Send, "Yours: Send", and a warning about an address nobody typed](../mail/screens/mail-reply.png)

- **The hand-over.** A draft the agent made opens in the reply box with the ring on Send and "Yours:
  Send". The line says "Reply to Sam is ready. Sending is yours." The first time it ever happens it adds
  one sentence, once: "I never press Send for you. Change anything in it first if you like." Everything
  the draft will do is on it: recipients, subject, body, each attachment, any warning, and what the
  provider adds ("Your Gmail signature and the quoted message are added when it sends.").
- **The person presses Send.** What goes is exactly what the view showed. The service refuses unless four
  fingerprints agree (the stored draft, the one pressed, the one the window said it showed, and one worked
  out again), and sends once, with no retry. The line gives a receipt that opens the sent copy.
- **The person walks away.** The draft stays in Drafts. A waiting draft never lights the dot and never
  joins the coding sessions' walk.
- **When it cannot tell.** If a send does not finish (Thunderbird stopped, a dialog nobody can answer,
  the network went), the line says "I can't tell whether that went. Look in Sent before you press Send
  again." and Send is offered again only after that. Bombadil never sends a message twice by itself, and
  restarts Thunderbird after such a send so that no dialog is left open on a hidden window.

![First run: one question, the address](../mail/screens/mail-first-run.png)

## What the agent can do

Five tools in the OS server: `mail_search`, `mail_read`, `mail_mark` (fills Needs a reply with one line of
why), `mail_draft` (a draft in the view) and `mail_show` (opens the window on a view). There is no send
tool, and no tool that moves, archives or deletes mail: those are the person's presses in the view. The
skill at `share/skills/bombadil-mail/SKILL.md` tells the agent what it may not do.

## What is a rule, not yet a wall

The agent runs as the person, with a shell. What keeps "never" true is code that checks where a press
comes from (agentd refuses one from a process inside a turn, from a process it cannot name, or from one
that has gone) and what is being pressed (the fingerprints). A process of the same user that is outside
every turn could in principle write to the mail socket or read Thunderbird's profile. The hardening order,
which later pieces carry out: connections run as their own system user with their own token store, agentd
accepts presses only from the shell's own process, and the agent loses `sudo`. Until then it is a rule with
a check, and the pages say so wherever it matters.

## What changes in the rest of Bombadil

- **A reserved app name.** `mail` is Bombadil's own; an app made on request cannot take it.
- **The pill line** gains notices (`notice`, `notice_end`, `notice_action`, `notice_dismiss`) with up to
  three buttons, drawn with the shared tokens. Mail is the first consumer; messages are the second.
- **agentd** gains the press path and the outbox. The narration says "Looking through your mail", "Writing
  a draft" and so on, and counts reading mail as reading other people's words.
- **The image** gains the `thunderbird` package, a user service, a window rule that keeps every
  Thunderbird window on a workspace nobody visits, and the add-on.

## What is checked and what is not

Checked with a real Thunderbird against a local mail server (`tests/test_mail_e2e_real.py`): an account is
added and Thunderbird starts by itself, mail is listed and read, new mail arrives as a push within seconds,
a reply is drafted, shown and pressed and the message at the server is that message, threaded under the one
it answers, a second press sends nothing, and stopping the service leaves no Thunderbird behind. The
add-on has its own tests with and without a real Thunderbird, and the window has screenshot-checked states.

Not checked, and said so wherever a person might assume otherwise: a real Gmail or Microsoft sign-in (the
consent screens change), Microsoft Graph accounts, an iCloud app-specific password, Arch's own Thunderbird
package on Hyprland (the lab used Mozilla's builds), the hidden workspace's behaviour on a real
compositor, and a long-lived mailbox of tens of thousands of mails.

## Building on it

- A **new provider** is an entry in `mail/accounts.py` (hosts, security, authentication, its web page).
- A **new mail tool** is a line in `mail/tools.py` and a branch in agentd's broker. It must not send.
- A **new view** is a name in the service's `list` and a row in the window.
- The same hand-over (a draft, a fingerprint, a shown draft, a press, a receipt) is what the message card
  and the task card reuse. Add a kind by adding an outbox entry, never a second way to send.

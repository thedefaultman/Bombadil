# Principles

> **Status:** Partly shipped. The rules are the design's and stand whole. The code on `main` keeps most of them. Some parts of them need work that is not on `main`, and "Where the rules stand on main" says which: coding sessions and the word "ship", and the self-improvement loop (all In progress), and the single list of everything that runs on its own, the holding card, a history for each app, the "this" chip, the card kinds other than `diagram` and the reader, a memory file the machine writes, a conversation that survives a reboot, the split between Bombadil's defaults and the person's settings, and a rollback after a failed start (all Designed).
> **Code:** `share/qml/Bombadil/Theme.qml`, `shell/StatusLine.qml`, `shell/PillState.qml`, `shell/Stone.qml`, `src/bombadil/agentd.py`, `src/bombadil/voice.py`, `src/bombadil/greet.py`, `src/bombadil/persona.py`, `share/voice/lines.toml`, `src/bombadil/providers.py`, `src/bombadil/launcher.py`, `src/bombadil/snapshots.py`, `src/bombadil/outbox.py`, `src/bombadil/jobs.py`, `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`, `iso/airootfs/etc/sudoers.d/bombadil`, `iso/airootfs/etc/systemd/user/` (the units of the daemon, the bar and `mako`), `src/bombadil/cardtools.py`, `src/bombadil/mail/watch.py`
> **Design:** the [design briefs](design/README.md), listed under "Where each rule was decided"
> **Verified:** 2026-11-08 against `main` at `9d5c729`: every number on the page (200 ms, one line and four, 12 px and 8 px, three things to learn, the newest 30 restore points, 20 jobs, 60 seconds, 5 minutes, twelve briefs, twelve questions) and the code behind each "in practice" sentence and each row of "Where the rules stand on main", read in the files named above and in `bin/bombadil`, `shell/shell.qml`, `shell/Wallpaper.qml`, `src/bombadil/brain/service.py`, `src/bombadil/notices.py`, `src/bombadil/sysmap.py`, `tests/test_agentd_rest.py`, `tests/test_theme.py`, `tests/desktop/driver.py` and `iso/airootfs/usr/local/bin/bombadil-install`; the reduced-motion behaviour of each stone movement in `shell/Stone.qml`; the conversation's life in `AgentD._load_state` and `AgentD.choose`; and that nothing under `src`, `shell`, `share`, `iso` or `bin` writes `memory.md` or the `[Screen]` block. The statements on the voice, the greeting and reduced motion were checked against the code at `f0485c2` on 2026-11-09, and the rest of the page was not read again. The rule texts were compared with the rule sections of the briefs they cite.

Bombadil is a Linux distribution whose main interface is an AI agent. Its design rests on a small set
of rules. They are why it feels the way it does, and a change that breaks one turns it into something
else: a chatbot with a desktop around it, or a desktop with a chat sidebar. This page lists the rules,
why each exists and what it asks of you when you build. The twelve design briefs state rules of their
own, and this page collects the rules that most of them decided. The link at the end of every rule goes
to where it was decided, with the full argument; a rule that rests on the code more than on a brief says so.

The line the project is held to: **stupidly simple to use, yet complete, capable and unique.** The
operating system is for the AI, and the person using it is a passenger who gets things explained
visually. A skill that lets the agent build native visual interactives is what makes it feel like an
operating system and not a chat window.

A rule is what the design holds. It does not say the code on `main` already keeps all of it: a part
that is In progress or Designed is marked in the rule, and the next section lists them, so nobody looks
for a "ship" word or an Autopilot list that is not there.

How to use this page: read it once before you design or change anything the person can see, and
check your work against [the checklist at the end](#checking-a-change-against-the-principles). Pieces
of the system link back here from their own pages, under "Principles it keeps".

## Where the rules stand on main

Four rules need no caveat for `main`: stupidly simple, 200 ms, colour says who and original. For the
others:

| Rule | Status | What `main` does | What it does not do |
|---|---|---|---|
| [The person is a passenger](#the-person-is-a-passenger) | Partly shipped | Each step is narrated in plain words, with its reason on hover, and the machine draws pictures of itself for exact words ([the phrases](architecture/cards-and-pictures.md#picture-words)). | The passenger brief's later pieces are designed: browser tools, promises, the holding card and the update turn. See [cards and pictures](architecture/cards-and-pictures.md). |
| [Full access with undo](#full-access-with-undo) | Partly shipped | The agent runs as the person with passwordless sudo and the CLIs in their no-prompt modes. On an installed system a restore point of the system files is taken before every turn, the newest 30 are kept, and `undo` puts the files back at the next restart. | The live image has no restore points, and the home folder and apps have none. The installer's red button that names the disk is designed; `bombadil-install` asks for the disk's name to be typed unless it is given `--yes`. See [restore points](architecture/restore-points.md). |
| [Real native apps](#real-native-apps) | Partly shipped | An app is a folder in `~/Apps/<name>/` that opens as a native window by name and reloads in place. | A history with undo for each app is designed. `~/Apps` is in no restore point, so undoing a turn that built an app leaves the app. See [app kit](architecture/app-kit.md). |
| [One machine one conversation](#one-machine-one-conversation) | Partly shipped | Claude and Codex run the same `os-mcp` tools on the same files, and the conversation survives a restart of `agentd`. | A switch of provider, a reboot or a new login starts a fresh conversation: `agentd` drops the conversation id on a switch and keeps it only under the runtime directory. Nothing writes `memory.md`; the Brain only reads it. See [agentd](architecture/agentd.md#known-gaps) and [the brain](architecture/brain.md). |
| [Recovery without the broken part](#recovery-without-the-broken-part) | Partly shipped | `Super+Escape` stops through agentd's socket, `bombadil undo` runs with no daemon, `Super+Ctrl+Escape` restarts the bar, and agentd, the bar and notifications restart when they die. | The rollback of a failed start, repair and reinstall from the stick and the rescue page are designed. See [session services](architecture/session-services.md) and [the installed OS](architecture/installed-os.md). |
| [Records before models](#records-before-models) | Partly shipped | "why" during a turn, "why is this here", `history`, the closing line with Details and the pictures of the machine (the network, the boot, one service, the disks, the sound and the screens) answer with no model. | The holding card, `remember` and `forget` and the list of what runs on its own are designed. See [cards and pictures](architecture/cards-and-pictures.md) and [the brain](architecture/brain.md). |
| [The person's press reaches people](#the-persons-press-reaches-people) | Partly shipped | The agent has no tool that sends. A mail draft leaves only on the person's press on the Mail window's Send, which `agentd` checks and refuses from inside a turn. | For everything else the rule is held by the system prompt and the design, not by a block: the agent has a shell, the network and sudo. Other kinds of press are designed. The word "ship" belongs to coding sessions, which are In progress. See [the security model](security-model.md#things-that-cross-to-other-people) and [coding sessions](architecture/coding-sessions.md). |
| [Nothing runs unseen](#nothing-runs-unseen) | Partly shipped | A job, one-shot watcher or timer the agent starts is a transient user unit, a row on the desk's Watching card (at most 20 at once) with a stop that needs no model. The image's own background programs are units. | The single list (Autopilot) and promises, routines and watchers that survive a restart are designed. The self-improvement loop is In progress. A process the agent starts by hand is not listed. See [the desk](architecture/desk.md#the-jobs-registry) and [the loop](architecture/loop.md). |
| [Plain files stay the truth](#plain-files-stay-the-truth) | Partly shipped | The Brain's index sits beside the files and can be rebuilt. Undo swaps the system root and leaves the home folder alone. | Updates, and the split of Bombadil's defaults in `/usr` from the person's short settings files, are designed: the skeleton's `hyprland.lua` is a whole config copied into the home at install. A refresh that keeps the home is In progress. An edited document landing beside its original is designed. See [the installed OS](architecture/installed-os.md). |
| [The answer is the thing](#the-answer-is-the-thing) | Partly shipped | The line holds at one line while the agent works and at most four afterwards, and `diagram` pictures, the machine's own pictures and receipts are drawn above it. | The other card kinds (list, checklist, timer, control, markdown reader) and the reader that slides in from the right are designed: `show_card` takes only `diagram`, so a long text reply is cut at four lines. See [cards and pictures](architecture/cards-and-pictures.md). |
| ["This" means what you see](#this-means-what-you-see) | Partly shipped | The Brain's words "brain" and "why is this here" read what is in front: the focused window, the browser tab, a terminal. | The chip that gives every ask the screen context is designed: nothing on `main` writes the `[Screen]` block. See [the brain](architecture/brain.md) and [the interaction model](ux/README.md). |
| [Quiet at rest](#quiet-at-rest) | Partly shipped | The screen at rest is the wallpaper and the pill, and a widget appears only with something to say. Bombadil speaks when the person arrives or leaves: one line at login, one after the name card, one when they come back after thirty minutes or more if something happened meanwhile, and a goodbye at shutdown. It says nothing at rest. With `greet = false` in `persona.toml` the arrivals say only what failed or was put back, and the goodbye is in Plain's words. Under `BOMBADIL_REDUCE_MOTION=1` every movement has a still version: the stone's roll is a slow pulse, the knock holds still and the glow is fixed, the hop is a fade-in, and the lean, the wallpaper's fade, the desk's folds and the fades and slides of the kit and of every app are instant. | Nothing sets that variable, and the compositor's own window and drawer slides do not read it. The screen is not silent at rest for mail: a new mail from someone the person knows puts a notice above the pill for 5 minutes, decided in the [everyday work brief](design/everyday-work-brief.md#1-mail-in-bombadils-own-view). See [the design system](design-system/README.md#reduced-motion), [motion](MOTION.md) and [the voice](architecture/voice.md). |
| [One design language](#one-design-language) | Partly shipped | The shell and the kit read `Theme.qml`, and a test fails a literal colour in a shell file. The shell's words are fixed strings in the plain register. The person picks one of three voices, Merry, Plain or Quiet, on the name card; a voice changes the words of a greeting and the closing sentence of the agent's reply and never a fact, a count or an error. A greeting line is at most 100 characters, has no dash, and has an exclamation mark only in Merry's lines (`tests/test_greet.py`). | Merry's exclamation marks are the one place the table allows one, where this rule says there are none in system text. See [the design system](design-system/README.md) and [the voice](architecture/voice.md). |
| [Degrade and recover](#degrade-and-recover) | Partly shipped | The bar waits for agentd and says so, the Brain looks at its logs every 60 seconds without a file watcher, a picture that cannot draw costs the pictures and not the bar, and while the AI rests, launcher words and `!` commands still run and asks wait. | Greetings are local templates in `share/voice/lines.toml`, read from files with no model and no network: a missing or broken table, or a source that fails, leaves nothing said and costs the person nothing else. | The table has lines for empty places, but only `bombadil history` uses one (`src/bombadil/watch.py`); no kit component reads it. See [the voice](architecture/voice.md). |

## What the machine is

### The person is a passenger

**The AI is the interface. The person rides along and can see the road.** The agent drives the
machine on the person's behalf. Everything it does is shown in plain words and pictures as it
happens, so the person never has to read a log, open a terminal or approve a step to stay in the
loop. A change that makes the person operate the machine (a settings page, a wizard, a manual)
works against this.

In practice: the machine explains while it drives, a picture of the machine comes from the machine,
and nothing needs an extra key to learn. Decided in the
[passenger brief](design/passenger-brief.md#the-rules) and the [UX brief](design/ux-brief.md#the-rules).

### Full access with undo

**The agent has full access, and safety is a restore point instead of a permission prompt.** The
agent runs as the person, with passwordless sudo and the vendor CLIs in their no-prompt modes: no
prompts, no countdowns, no sandbox between the person and the AI. Two things replace guard rails.
Every change shows in plain words as it happens. On an installed system a restore point of the
system files is taken before every agent turn, so "undo" puts those files back at the next restart
and says exactly what it covered. It does not touch the person's home folder or apps, and the live
image, which keeps no restore points, says that undo starts once Bombadil is installed.

In practice: do not add a confirm dialog to something undo can reverse; make it visible and
undoable instead. A step that undo cannot follow is not paused either: it turns red on the line with
its exact command, Esc stops it, and the closing line says it cannot be undone
([decision D-1428](decisions/limits-and-recovery.md#d-1428)). Two things do wait for the person:
what leaves the machine, which is [the person's press](#the-persons-press-reaches-people), and
erasing a disk, where nothing is erased that the person did not name (the installer asks for the
disk's name to be typed; the red button that names it is designed). Decided in the
[UX brief](design/ux-brief.md#the-rules), [foundation choices](design/foundation-choices.md) and the
[installed OS brief](design/installed-os-brief.md#the-rules).

### Real native apps

**What the agent makes is a real native app, never a web page on a port.** An app the agent writes is
a native window built from the app kit, opened by name and changed by talking. That is what separates
an operating system from a chatbot that writes code. The kit and the skill that teaches it exist so
the window can appear as soon as the agent has written its files, with no compile step; the design
aims at about a second. Rolling an app back on its own, with a history for each app, is designed and
not built, and undo covers system files only.

In practice: capabilities for generated apps go in the kit and its skill, in the OS's look; a
feature that only works as a localhost web page does not ship. Decided in the
[UX brief](design/ux-brief.md#the-rules) and [foundation choices](design/foundation-choices.md).

### One machine one conversation

**One machine, one conversation, one memory the person can read.** There is no list of separate
chats. What the OS can do lives in one tool server and in plain files, so Claude and Codex run the
same tools on the same files. What the machine made and recorded stays when the person switches
provider, but the conversation does not: the other provider starts a fresh one. What the machine
remembers is meant to be kept in a plain file the person can read, `memory.md`. On `main` the
conversation survives a restart of `agentd` and not a reboot or the next login, and nothing writes
`memory.md`: the Brain only reads it.

In practice: put an ability in `os-mcp`, not in one provider's prompt; keep state in plain files or
a rebuildable index. Decided in the [UX brief](design/ux-brief.md#the-rules) and the
[brain brief](design/brain-brief.md#the-rules).

### Recovery without the broken part

**Stop, undo and rescue never go through the part that broke.** The agent can edit the shell, the
compositor config and the session daemon itself, so stop, undo, the rollback after a bad start
(designed) and repair (designed) must work without the model, the network or the graphical shell,
and every way back keeps the person's files.

In practice: a recovery path that needs the thing it recovers is not a recovery path. Decided in the
[UX brief](design/ux-brief.md#the-rules), the
[installed OS brief](design/installed-os-brief.md#the-rules) and the
[self-improvement brief](design/self-improvement-brief.md#the-rules).

### Records before models

**Records answer before models, and the machine explains itself from what it kept.** "Why?", "what
changed?", "what are you holding?" and "what runs on its own?" are to be answered first from what the
machine recorded, instantly and offline. On `main`, "why" during a turn, "why is this here", `history`
and the closing line's Details are answered that way; the holding card and the list of what runs on
its own are designed. A reason is the sentence the agent wrote just before it acted, kept, not a
second model's guess made afterwards. A picture of the machine is captured from the machine, never
drawn by a model, because a passenger cannot check a drawing. Words from outside are marked by order
("after reading a page"), never guessed by cause. A link in the Brain says why and when, and a
description of a thing is pinned to what the model read and greys out when that changes.

In practice: before you ask the model, check whether a record already holds the answer. Decided in
the [passenger brief](design/passenger-brief.md#the-rules) and the
[brain brief](design/brain-brief.md#the-rules).

### The person's press reaches people

**Only the person's own press reaches other people or leaves the machine.** The agent reads, drafts,
fills, attaches and points. It is never to send, post, accept, decline, share, submit, pay, sign, push
or merge on its own: those wait for the person's own press on the real button, or for their word
("ship", which belongs to the coding sessions and is not on `main`), because undo cannot follow what
has left the machine. Words from other people (mail, chat, shared documents, web pages) are data to
read, never instructions to obey.

What the code holds: the agent has no tool that sends, and a mail draft leaves only on the person's
press, which `agentd` checks and refuses from inside a turn. For everything else the rule is held by
the system prompt and the design, not by a block; the agent has a shell, the network and sudo, so
`git push` or `curl` would work ([security model](security-model.md#things-that-cross-to-other-people)).
Other people's words are marked and the prompt says to read them and never obey; that is a convention
for a model with a shell, not a separation.

In practice: give the agent a way to prepare, never a tool that releases. Decided in the
[everyday work brief](design/everyday-work-brief.md#the-rules), the
[dev brief](design/dev-brief.md#the-rules), the
[self-improvement brief](design/self-improvement-brief.md#the-rules), the
[poor man switch brief](design/poor-man-switch-brief.md#the-rules) (rule 6: money and the other
account wait for the person's press) and the [mail view note](design/mail-view.md#the-rules).

### Nothing runs unseen

**Nothing runs on its own that the person cannot see and stop.** Every promise, routine, watcher, job
and piece of upkeep is meant to be a unit listed in one place the person can see and stop, and a
promise that is not a unit does not exist. Counting and checking read what the machine already
writes and call no model. Fixing runs only on the person's word, while they are away, one job at a
time, under a written budget. An idea is a strip the person can ignore, never a popup, and a tap on
it is an ordinary turn with its restore point.

What exists on `main`: a job, a one-shot watcher or a timer that the agent starts is a transient user
unit and a row on the desk's Watching card, and the person can stop it without a model. The single
list, promises that survive a restart and the self-improvement loop are designed or In progress.

In practice: background work is a visible unit with an owner and a stop, and it never sits on the
path of a turn. Decided in the [passenger brief](design/passenger-brief.md#the-rules), the
[self-improvement brief](design/self-improvement-brief.md#the-rules) and the
[desk brief](design/widgets-brief.md#the-rules).

### Plain files stay the truth

**Plain files stay the truth, and the person's home is never rewritten.** Folders are still folders,
and every tool and both agents read them. Any index sits beside them and can be rebuilt; deleting it
loses a view, not the person's data. Undo swaps the system root only, so the home folder and apps
stay as they are. The installed OS brief adds that install, update and refresh only add missing
files to the home, that Bombadil's defaults live in `/usr` and change with each update, and that the
person's own settings live in short files that load those defaults, so no update overwrites them.
That split is designed and not built. Until the home has its own restore points, an edited document
is meant to land beside its original (designed).

In practice: never write over a file the person made; put defaults where updates can replace them.
Decided in the [brain brief](design/brain-brief.md#the-rules), the
[installed OS brief](design/installed-os-brief.md#the-rules) and the
[everyday work brief](design/everyday-work-brief.md#the-rules).

## How it behaves

### Stupidly simple

**One pill, and three things to learn: Super (or Alt+Space) to talk, Esc to stop, "undo" to go
back.** Everything else appears when the person calls it and leaves when it is done, so nobody needs a
manual. Nothing extra to learn: every addition is a word in the pill, a hover, or a mark on something
already on screen.

In practice: if a feature needs a key, a window to find or a page of settings, look for the
version that is a word in the pill. Decided in the [UX brief](design/ux-brief.md#the-rules) and the
[passenger brief](design/passenger-brief.md#the-rules). The exact keys are in
[keys and timings](ux/keys-and-timings.md#keys) and [the shell doc](architecture/shell.md).

### Something true in 200 ms

**Something true is on screen within 200 ms of Enter, and open, stop and undo never wait for the
model.** If nothing happens after Enter, people press it again or decide the machine has frozen.
The line above the pill says what is happening in plain words, and it keeps saying it as the work
goes on.

In practice: show progress from the first event, not from the first model token; route fixed
commands around the model. The pill draws "On it" on the keypress, and the headless desktop test
fails if agentd's first event of the turn (`turn_start`) comes more than 200 ms after Enter. Decided in the
[UX brief](design/ux-brief.md#the-rules).

### The answer is the thing

**The answer is the thing itself.** A window, a card or a panel appears. Written replies stay at one
line while the agent works and at most four afterwards, because a paragraph about the work
turns an operating system back into a chat window.

In practice: when a reply would be long, it is a card, a picture or an app. On `main` the only card
kind is `diagram`, and a text reply longer than four lines is cut at four; the other kinds and the
reader that slides in from the right are designed. Decided in the
[UX brief](design/ux-brief.md#the-rules) and the [voice brief](design/voice-brief.md#the-rules).

### "This" means what you see

**"This" means what is in front of the person.** People talk about what they can see: the focused
window, the page in the browser panel, the selection. Nobody should have to describe their own
screen to their own computer.

In practice: let "this" come from the machine, never from the person describing their screen. The
Brain's words do it on `main`; the design also gives the agent the screen context with every ask,
which is not built. Decided in the [UX brief](design/ux-brief.md#the-rules).

### Quiet at rest

**The screen at rest is wallpaper and one pill, and every movement means one thing.** No splash and
no vitals strip, and the wallpaper is dark surfaces with at most a ghost of the stone. Widgets appear
only when they have something to say and fold away for whatever the AI brings. Bombadil speaks
only when the person arrives or leaves and is silent at rest: on `main` that is the greeting and the goodbye
of its voice ([the voice](architecture/voice.md)). Mail is an exception to that silence: a new mail from someone the person
knows puts a notice above the pill for 5 minutes, as the
[everyday work brief](design/everyday-work-brief.md#1-mail-in-bombadils-own-view) decided. A movement always means the same thing (rolling is working, a
knock is waiting on you, a hop is done, a lean is listening) and has a still version for
reduced motion. `BOMBADIL_REDUCE_MOTION=1` sets one switch, `Theme.reducedMotion`, which the shell, the desk,
the kit and every app read, and a test fails an animation that forgets it ([motion](MOTION.md)). The roll is a
slow pulse, the knock holds still and the glow is fixed, the hop is a fade-in, and the lean and every fade and
slide are instant. Nothing turns it on by default, and the compositor's own slides do not read it.

In practice: before you add an animation, a badge or a greeting, say what single thing it means and
when it goes away. Decided in the [identity brief](design/identity-brief.md#the-rules), the
[desk brief](design/widgets-brief.md#the-rules) and the [voice brief](design/voice-brief.md#the-rules).

## How it looks and sounds

### Colour says who

**White is the person, orange is the machine acting, blue is a coding session.** Amber is a step
that touches the system or something waiting on the person, red is failed, offline or cannot be
undone, and green is connected. The accent orange is the machine's colour: besides the machine at
work it marks only what is chosen or primary (the primary button, the focus ring, the selected tab),
never decoration. The stone's states read without colour, by shape and movement, and a coloured
status elsewhere has words beside it.

In practice: pick colours from the tokens by meaning, never by taste. Decided in the
[identity brief](design/identity-brief.md#the-rules) and the [desk brief](design/widgets-brief.md#the-rules);
the tokens are in the [design system](design-system/README.md).

### One design language

**The shell and every app read the same tokens, the same kit and the same plain voice.** Nothing is
styled one-off. Things that speak are round and things that hold have 12 px corners (controls have 8).
Words are plain full sentences in sentence case that name what changed and how to take it back; there
are no emoji, no "Oops" and no exclamation marks in system text. A voice may
change the words of a greeting and the closing sentence of a reply, never a fact, a count or an error; on
`main` the person picks one of three, Merry, Plain or Quiet, on the name card.

In practice: use `Theme` and the kit's components, and write system text in the plain register.
Decided in the [identity brief](design/identity-brief.md#the-rules), the
[voice brief](design/voice-brief.md#the-rules) and the [design system](design-system/README.md).

### Original

**Nothing borrowed, no face and no mascot.** No imagery from the text or art the name echoes, no face,
no mascot. The character is in how the stone moves. The orange is the exact colour of Claude's logo
and Bombadil also runs Codex, so Bombadil is recognised by a shape, never by an orange blob.

In practice: do not add decoration to carry character; movement and wording carry it. Decided in the
[identity brief](design/identity-brief.md#the-rules).

## How it is built

### Degrade and recover

**Every piece degrades, and nothing waits on a model it does not need.** The bar waits for the agent
daemon and says so. The Brain works without its file watcher, looking at its logs every 60 seconds. A
picture host that cannot draw costs the pictures, never the bar. While the AI rests, apps, files,
launcher words and `!` commands keep working and asks wait instead of failing. A missing optional part
costs a feature, not the machine. Greetings are local templates that
cost nothing at boot and work offline: a table of words and facts read from files, with no model call, and a
missing table or a source that breaks leaves nothing said.

In practice: when you add a dependency, decide what the person sees when it is missing, and test
that. Decided in the [poor man switch brief](design/poor-man-switch-brief.md#the-rules) (rule 1: nothing
you made stops when the AI does), the [UX brief](design/ux-brief.md#the-rules) (rule 7) and, for
greetings, the [voice brief](design/voice-brief.md#the-rules) (rule 7). The watcher and the picture
host were set in the code, not in a brief: see [the brain](architecture/brain.md),
[the shell](architecture/shell.md) and [cards and pictures](architecture/cards-and-pictures.md).

## Checking a change against the principles

Before you open a pull request for something the person can see or that acts on their machine, ask.
Each question names the rules it tests, except the last, which tests the paperwork.

1. Is something true on screen within 200 ms of Enter, do open, stop and undo skip the model, and does
   the line say what is happening in plain words? ([Something true in 200 ms](#something-true-in-200-ms))
2. Can the person see what changed, in plain words, and take it back, and on an installed system did a
   restore point cover it? ([The person is a passenger](#the-person-is-a-passenger),
   [Full access with undo](#full-access-with-undo))
3. Does anything ask permission or leave the machine? A step undo can reverse never asks. A step undo
   cannot follow on the machine is shown in red with its exact command and is not paused. Only the
   person's own press releases what leaves the machine or reaches another person, and the one wait
   inside the machine is the installer naming the disk before it erases it.
   ([Full access with undo](#full-access-with-undo),
   [The person's press reaches people](#the-persons-press-reaches-people))
4. Does it work the same under Claude and Codex, through `os-mcp` and plain files the person can read?
   ([One machine one conversation](#one-machine-one-conversation))
5. Does it write over a file the person made, rewrite their home folder, or put a default where an update
   cannot replace it? ([Plain files stay the truth](#plain-files-stay-the-truth))
6. Is the result a card, a panel, a picture or a native app, not a paragraph and not a web page, and can
   the person reach it without a key, a window to find or a page of settings?
   ([The answer is the thing](#the-answer-is-the-thing), [Real native apps](#real-native-apps),
   [Stupidly simple](#stupidly-simple))
7. Does it use the `Theme` tokens and the kit, with no literal colours or one-off type, colours picked by
   meaning and system text in the plain register? ([One design language](#one-design-language),
   [Colour says who](#colour-says-who))
8. Is it quiet at rest, does each movement mean one thing and have a still version, and does the
   character come from movement and wording, not from decoration, a face or a mascot?
   ([Quiet at rest](#quiet-at-rest), [Original](#original))
9. Could a record answer this without a model, and does "this" mean what is in front of the person rather
   than something they must describe? ([Records before models](#records-before-models),
   ["This" means what you see](#this-means-what-you-see))
10. Does anything run on its own? Is it a unit the person can see and stop?
    ([Nothing runs unseen](#nothing-runs-unseen))
11. If a dependency is missing, or the part that broke is the one you changed, what does the person see,
    is there a test for it, and do stop, undo and rescue still work with no model, network or shell?
    ([Degrade and recover](#degrade-and-recover), [Recovery without the broken part](#recovery-without-the-broken-part))
12. Is the piece documented under `docs/architecture/`, and are the decision and its status recorded in
    [decisions](decisions/README.md) and [the roadmap](roadmap.md)?

## Where each rule was decided

Every one of the twelve briefs has a section of rules, and the table below lists ten of them. The other
two keep rules of their own that this page does not collect: the
[poor man switch brief](design/poor-man-switch-brief.md#the-rules), whose rules 1 and 6 are cited
under degrade and recover and the person's press, and the tips brief
([Tips' own rules](design/tips-and-desk-defaults-brief.md#tips-own-rules)). The
[mail view note](design/mail-view.md#the-rules) has six rules of its own too, and its rule 2 is the
person's press for mail. [Foundation choices](design/foundation-choices.md) is cited for two rules
because it records the stack choices behind them, and it has no rules section; nor do the notes on
[boot and the wallpaper](design/boot-and-wallpaper.md), the [design system](design/design-system.md) and
the [Rust question](design/rust-question.md). How much of each brief is built is in the status column
of [the list of briefs](design/README.md#all-briefs).

| Brief | Rules about |
|---|---|
| [How Bombadil should feel](design/ux-brief.md#the-rules) | the pill, answers as things, 200 ms, no prompts, "this", native apps, recovery, one conversation |
| [Building on Bombadil](design/dev-brief.md#the-rules) | coding sessions: run as shipped, windows are views, one list, rooms, nothing pushed until "ship" |
| [Bombadil's Brain](design/brain-brief.md#the-rules) | an index the machine writes, links that say why, plain files, things hold still |
| [Riding with Bombadil](design/passenger-brief.md#the-rules) | explaining while driving, pictures from the machine, records before models, one list of what runs |
| [The desk](design/widgets-brief.md#the-rules) | widgets answer one question each, rails not the middle, present only with something to say |
| [Bombadil's voice](design/voice-brief.md#the-rules) | speak on arrival and leaving, every line true, words not facts, one line |
| [Bombadil tends itself](design/self-improvement-brief.md#the-rules) | counting and checking are not work, fixes run on the person's word, proof before a fix |
| [Bombadil, installed](design/installed-os-brief.md#the-rules) | nothing erased unnamed, two questions, the layout fixed at install, a home never rewritten |
| [The Bombadil mark](design/identity-brief.md#the-rules) | the stone, colour says who, shape and movement, original |
| [Bombadil at work](design/everyday-work-brief.md#the-rules) | glance in Bombadil and work in the tool, the person's press, other people's words are data |

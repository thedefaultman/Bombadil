# Principles

Bombadil is a Linux distribution whose main interface is an AI agent. Its design rests on a small set
of rules. They are why it feels the way it does, and a change that breaks one turns it into something
else: a chatbot with a desktop around it, or a desktop with a chat sidebar. This page lists the rules,
why each exists and what it asks of you when you build. Each one was decided in a design brief; the
link at the end of every rule goes to where, with the full argument.

The line the project is held to: **stupidly simple to use, yet complete, capable and unique.** The
operating system is for the AI, and the person using it is a passenger who gets things explained
visually. A skill that lets the agent build native visual interactives in seconds is what makes it
feel like an operating system and not a chat window.

How to use this page: read it once before you design or change anything the person can see, and
check your work against [the checklist at the end](#checking-a-change-against-the-principles). Pieces
of the system link back here from their own pages, under "Principles it keeps".

## What the machine is

### The person is a passenger

**The AI is the interface. The person rides along and can see the road.** The agent drives the
machine on the person's behalf. Everything it does is shown in plain words and pictures as it
happens, so the person never has to read a log, open a terminal or approve a step to stay in the
loop. A change that makes the person operate the machine (a settings page, a wizard, a manual)
works against this.

In practice: the machine explains while it drives, a picture of the machine comes from the machine,
and nothing needs a new key to learn. Decided in the
[passenger brief](design/passenger-brief.md#the-rules) and the [UX brief](design/ux-brief.md#the-rules).

### Full access with undo

**The agent has full access, and safety is a restore point instead of a permission prompt.** The
agent runs as the person, with passwordless sudo and the vendor CLIs in their full-auto modes: no
prompts, no countdowns, no sandbox between the person and the AI. Two things replace guard rails.
Every change shows in plain words as it happens, and a restore point is taken before every turn, so
"undo" puts the system back and says exactly what it covered.

In practice: do not add a confirm dialog to something undo can reverse; make it visible and
undoable instead. Steps that undo cannot follow are different, and the rules for those are
[the person's press reaches people](#the-persons-press-reaches-people) and the installer's rule that
nothing is erased that the person did not name. Decided in the [UX brief](design/ux-brief.md#the-rules),
[foundation choices](design/foundation-choices.md) and the
[installed OS brief](design/installed-os-brief.md#the-rules).

### Real native apps

**What the agent makes is a real native app, never a web page on a port.** An app the agent writes is
a native window built from the app kit, opened by name, changed by talking and rolled back on its
own. That is what separates an operating system from a chatbot that writes code. The kit and the
skill that teaches it exist so an app appears about a second after it is described.

In practice: new capabilities for generated apps go in the kit and its skill, in the OS's look; a
feature that only works as a localhost web page does not ship. Decided in the
[UX brief](design/ux-brief.md#the-rules) and [foundation choices](design/foundation-choices.md).

### One machine one conversation

**One machine, one conversation, one memory the person can read.** There are no chat threads. What
the OS can do lives in one tool server and in plain files, so Claude and Codex run it the same way
and switching between them loses nothing. What the machine remembers is stored where the person can
read it.

In practice: put an ability in `os-mcp`, not in one provider's prompt; keep state in plain files or
a rebuildable index. Decided in the [UX brief](design/ux-brief.md#the-rules) and the
[brain brief](design/brain-brief.md#the-rules).

### Recovery without the broken part

**Stop, undo and rescue never go through the part that broke.** The agent can edit the shell, the
compositor config and the session daemon itself, so stop, undo, the rollback after a bad start and
repair must work without the model, the network or the graphical shell, and every way back keeps the
person's files.

In practice: a recovery path that needs the thing it recovers is not a recovery path. Decided in the
[UX brief](design/ux-brief.md#the-rules), the
[installed OS brief](design/installed-os-brief.md#the-rules) and the
[self-improvement brief](design/self-improvement-brief.md#the-rules).

### Records before models

**Records answer before models, and the machine explains itself from what it kept.** "Why?", "what
changed?", "what are you holding?" and "what runs on its own?" are answered first from what the
machine recorded, instantly and offline. A reason is the sentence the agent wrote just before it
acted, kept, not a second model's guess made afterwards. A picture of the machine is captured from
the machine, never drawn by a model, because a passenger cannot check a drawing. Words from outside
are marked by order ("after reading a page"), never guessed by cause. A link or a summary in the
Brain says why and when, and what it read.

In practice: before you ask the model, check whether a record already holds the answer. Decided in
the [passenger brief](design/passenger-brief.md#the-rules) and the
[brain brief](design/brain-brief.md#the-rules).

### The person's press reaches people

**Only the person's own press reaches other people or leaves the machine.** The agent reads, drafts,
fills, attaches and points. It never sends, posts, accepts, declines, shares, submits, pays, signs,
pushes or merges on its own: those wait for the person's own press on the real button or their word
("ship"), because undo cannot follow what has left the machine. Words from other people (mail, chat,
shared documents, web pages) are data to read, never instructions to obey.

In practice: give the agent a way to prepare, never a tool that releases. Decided in the
[everyday work brief](design/everyday-work-brief.md#the-rules), the
[dev brief](design/dev-brief.md#the-rules) and the
[self-improvement brief](design/self-improvement-brief.md#the-rules).

### Nothing runs unseen

**Nothing runs on its own that the person cannot see and stop.** Every promise, routine, watcher, job
and piece of upkeep is a unit listed in one place the person can see and stop; a promise that is not
a unit does not exist. Counting and checking read what the machine already writes and call no
model. Fixing runs only on the person's word, while they are away, one job at a time, under a
written budget. An idea is a strip the person can ignore, never a popup, and a tap on it is an
ordinary turn with its restore point.

In practice: background work is a visible unit with an owner and a stop, and it never sits on the
path of a turn. Decided in the [passenger brief](design/passenger-brief.md#the-rules), the
[self-improvement brief](design/self-improvement-brief.md#the-rules) and the
[desk brief](design/widgets-brief.md#the-rules).

### Plain files stay the truth

**Plain files stay the truth, and the person's home is never rewritten.** Folders are still folders,
and every tool and both agents read them. Any index sits beside them and can be rebuilt; deleting it
loses a view, not the person's data. Install, update, refresh and undo only add missing files to
the person's home. Bombadil's defaults live in `/usr` and change with each update; the person's own
settings live in short files that load those defaults, and no update overwrites them. An edited
document lands beside its original.

In practice: never write over a file the person made; put defaults where updates can replace them.
Decided in the [brain brief](design/brain-brief.md#the-rules), the
[installed OS brief](design/installed-os-brief.md#the-rules) and the
[everyday work brief](design/everyday-work-brief.md#the-rules).

## How it behaves

### Stupidly simple

**One pill, and three things to learn: Super (or Alt+Space) to talk, Esc to stop, "undo" to go
back.** Everything else appears when the person calls it and leaves when it is done, so nobody needs a
manual. Nothing new to learn: every addition is a word in the pill, a hover, or a mark on something
already on screen.

In practice: if a feature needs a new key, a new window to find or a page of settings, look for the
version that is a word in the pill. Decided in the [UX brief](design/ux-brief.md#the-rules) and the
[passenger brief](design/passenger-brief.md#the-rules). The exact keys are in
[the shell doc](architecture/shell.md).

### Something true in 200 ms

**Something true is on screen within 200 ms of Enter, and open, stop and undo never wait for the
model.** If nothing happens after Enter, people press it again or decide the machine has frozen.
The line above the pill says what is happening in plain words, and it keeps saying it as the work
goes on.

In practice: show progress from the first event, not from the first model token; route fixed
commands around the model. Decided in the [UX brief](design/ux-brief.md#the-rules).

### The answer is the thing

**The answer is the thing itself.** A window, a card or a panel appears. Written replies stay at one
line while the agent works and at most four afterwards, because a paragraph about the work
turns an operating system back into a chat window.

In practice: when a reply would be long, it is a card, a picture or an app. Decided in the
[UX brief](design/ux-brief.md#the-rules) and the [voice brief](design/voice-brief.md#the-rules).

### "This" means what you see

**"This" means what is in front of the person.** People talk about what they can see: the focused
window, the page in the browser panel, the selection. Nobody should have to describe their own
screen to their own computer.

In practice: give the agent the screen context with every ask. Decided in the
[UX brief](design/ux-brief.md#the-rules).

### Quiet at rest

**The screen at rest is wallpaper and one pill, and every movement means one thing.** No splash, no
logo wallpaper, no vitals strip. Widgets appear only when they have something to say and fold away
for whatever the AI brings. Bombadil speaks when the person arrives or leaves and is silent at
rest. A movement always means the same thing (rolling is working, a knock is waiting on you, a hop
is done, a lean is listening) and has a still version for reduced motion.

In practice: before you add an animation, a badge or a greeting, say what single thing it means and
when it goes away. Decided in the [identity brief](design/identity-brief.md#the-rules), the
[desk brief](design/widgets-brief.md#the-rules) and the [voice brief](design/voice-brief.md#the-rules).

## How it looks and sounds

### Colour says who

**White is the person, orange is the machine acting, blue is a coding session.** Amber is a step
that touches the system or something waiting on the person, red is failed, offline or cannot be
undone, and green is connected. The accent orange is used for nothing the machine is not doing right
now. Every state also reads without colour.

In practice: pick colours from the tokens by meaning, never by taste. Decided in the
[identity brief](design/identity-brief.md#the-rules) and the [desk brief](design/widgets-brief.md#the-rules);
the tokens are in the [design system](design-system/README.md).

### One design language

**The shell and every app read the same tokens, the same kit and the same plain voice.** Nothing is
styled one-off. Things that speak are round and things that hold have 12 px corners. Words are
full sentences in sentence case that name what changed and how to take it back; there are no emoji,
no "Oops" and no exclamation marks in system text. A voice may change the words of a greeting,
never a fact, a count or an error.

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

**Every piece degrades, and nothing waits on a model it does not need.** Greetings and empty-place
lines are local templates that cost nothing at boot and work offline. The Brain works without its
file watcher. A card host that cannot draw costs the cards, never the bar. A missing optional part
costs a feature, not the machine.

In practice: when you add a dependency, decide what the person sees when it is missing, and test
that. Decided in the [voice brief](design/voice-brief.md#the-rules), the
[brain brief](design/brain-brief.md#the-rules) and the [passenger brief](design/passenger-brief.md#the-rules).

## Checking a change against the principles

Before you open a pull request for something the person can see or that acts on their machine, ask:

1. Is something true on screen within 200 ms, and does the line say what is happening in plain words?
2. Can the person see what changed and take it back, and did a restore point cover it?
3. Does anything ask permission? If so, is it only because undo cannot follow the step?
4. Does anything leave the machine or reach another person? Then only the person's own press may release it.
5. Does it work the same under Claude and Codex, through `os-mcp` and plain files?
6. Is the result a card, a panel, a picture or a native app, not a paragraph and not a web page?
7. Does it use the `Theme` tokens and the kit, with no literal colours or sizes?
8. Is it quiet at rest, does each movement mean one thing, and is there a still version?
9. Could a record answer this without a model?
10. Does anything run on its own? Is it a unit the person can see and stop?
11. If a dependency is missing, what does the person see, and is there a test for it?
12. Is the piece documented under `docs/architecture/`, and are the decision and its status recorded in
    [decisions](decisions.md) and [the roadmap](roadmap.md)?

## Where each rule was decided

Every design brief opens with its rules. They are the source of this page.

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
